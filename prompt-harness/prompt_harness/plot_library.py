# -*- coding: utf-8 -*-
"""v5.33 情节模板库 —— 双模块连接的地基。

用户需求（2026-08-06，睡觉前拍板）：
- prompt-harness 榨干生成的所有「树状正文生成模板/策略/方法」要落到**情节模板库**，
  只有库里的内容才能与主系统正文模块连接。
- 提取到的模板里**不能有人名、东西、地名等某本小说特有的实体** —— 去实体化：
  LLM 把各级内容槽位化（{{主角}}/{{地点}}/{{关键物}}…）+ 确定性实体护栏兜底。
- 主系统写新剧情时命中同款剧情 → **套用该模板的格式 + 1→12345 策略**。

数据流（从训练到主系统）：
  合格压缩阶梯（build_ladder + 节拍覆盖达标）
    → create_plot_template 去实体化（extract_key_facts 提实体 → LLM 槽位化 → 护栏校验）
    → 存入 plot_template_library.json
    → 主系统写剧情时 match_plot_templates 命中 → bridge 按模板格式+策略逐级展开。

存储：SETTINGS.data_dir / plot_template_library.json
"""
from __future__ import annotations

import asyncio
import json
import os
import random
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from .config import SETTINGS
from .embed_client import cosine_similarity, get_embeddings

# 榨干并发上限（Semaphore）：extract_from_doc 内部逐章 build_ladder 并发数。
# 2026-08-15 极限探针：qwen3.5-flash 64 路重负载零 429、延迟恒定 → 默认 8 安全，
# 多任务并发时总量=任务数×8（8 任务=64 路已验证）。可用 EXTRACT_CONCURRENCY 环境变量覆盖。
_EXTRACT_CONCURRENCY = int(os.getenv("EXTRACT_CONCURRENCY", "8"))
from .llm_client import chat_completion, chat_json

# ── 实体提取（确定性部分）──────────────────────────────────────────────
# 金额/数字：X万/X百万/X千万/X亿（含中文数字）
_MONEY_RE = re.compile(r"[0-9一二三四五六七八九十百千零]+(?:亿|千万|百万|万)")
# 地名尾缀（确定性召回地点/组织，供护栏 + 槽位化）
_PLACE_SUFFIX = ("市", "城", "山", "府", "宫", "阁", "楼", "门", "园", "庄", "寨",
                 "县", "镇", "村", "河", "江", "湖", "海", "谷", "岭", "峰", "堂",
                 "殿", "院", "馆", "巷", "桥", "街", "郡", "国", "宗", "派", "教")
_PLACE_RE = re.compile(r"[一-鿿]{2,4}(?:" + "|".join(_PLACE_SUFFIX) + r")")

# 模板各「级」与它们在阶梯 dict 里的取值路径
_LEVEL_PATHS: dict[str, tuple[str, str]] = {
    "l1": ("l1_minimal", "text"),
    "l2": ("l2_arc", "text"),
    "l3": ("l3_chapter", "text"),
    "l4": ("l4_scenes", "text"),
}

_DEFAULT_STRATEGY = {
    "scene_density": 4,
    "scene_count_hint": "3-5",
    "target_len_mode": "adaptive",
    "dialogue_contract": True,
    "ai_flavor_review": True,
    "retry_cap": 2,
    "budget": 80,
}

# ── 专业模板字段（v5.33.2：模板 = 可填写的字段表单）───────────────────
# 模板不再只是一段挖空的文字，而是结构化字段表：每个字段有 label（显示名）与
# hint（这类剧情该写什么，从源剧情提炼、去实体化）。用户在命中的模板上填写
# 具体内容 → 自动组装 l1 → 走 1→12345 阶梯，字段值全程锚定防漂移。
_TEMPLATE_FIELDS: tuple[tuple[str, str], ...] = (
    ("protagonist", "主角"),
    ("cast", "出场人物"),
    ("time_setting", "时间背景"),
    ("location", "关键地点"),
    ("conflict", "核心冲突"),
    ("goal", "目标"),
    ("twist", "转折"),
    ("ending", "结局/悬念"),
    ("key_item", "关键道具"),
)
_FIELD_LABELS = {k: label for k, label in _TEMPLATE_FIELDS}
_FIELD_KEYS = tuple(k for k, _ in _TEMPLATE_FIELDS)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _scene_text(sc: dict[str, Any]) -> list[str]:
    """单个场景的文本行（环境/动作/对白/心理/冲突/细节），供拼接阶梯文本。"""
    lines: list[str] = []
    nm = str(sc.get("name") or "").strip()
    if nm:
        lines.append("场景：" + nm)
    for leaf in ("environment", "actions", "dialogues",
                 "psychologies", "conflicts", "details"):
        v = sc.get(leaf)
        if isinstance(v, str) and v.strip():
            lines.append(v.strip())
        elif isinstance(v, list):
            lines.extend(str(x).strip() for x in v if isinstance(x, str) and x.strip())
    return lines


def _l3_text(l3: Any) -> list[str]:
    """章核心 dict → 文本行（标题/核心/拍）。"""
    lines: list[str] = []
    if not isinstance(l3, dict):
        return lines
    if isinstance(l3.get("title"), str) and l3["title"].strip():
        lines.append("标题：" + l3["title"].strip())
    if isinstance(l3.get("core"), str) and l3["core"].strip():
        lines.append("核心：" + l3["core"].strip())
    for b in (l3.get("beats") or []):
        if isinstance(b, str) and b.strip():
            lines.append("拍：" + b.strip())
    return lines


def _collect_ladder_text(ladder: dict[str, Any]) -> str:
    """把压缩阶梯的各级内容拼成一段文本（供实体提取/匹配/展示）。

    【v5.33.7】支持两种形态：
    - 单章：l3_chapter(dict) + l4_scenes(list)
    - 多章弧：l3_chapters(list[dict]) + l4_chapters(list[list])（第 k 章标注拼接）
    """
    parts: list[str] = []
    if isinstance(ladder, dict):
        l1 = ladder.get("l1_minimal")
        if isinstance(l1, str) and l1.strip():
            parts.append("极简：" + l1.strip())
        l2 = ladder.get("l2_arc")
        if isinstance(l2, str) and l2.strip():
            parts.append("弧线：" + l2.strip())
        l3 = ladder.get("l3_chapter")
        if isinstance(l3, dict):
            parts.extend(_l3_text(l3))
        l3s = ladder.get("l3_chapters")
        if isinstance(l3s, list):
            for i, c in enumerate(l3s, 1):
                seg = _l3_text(c)
                if seg:
                    parts.append(f"第{i}章｜" + "｜".join(seg))
        l4 = ladder.get("l4_scenes")
        if isinstance(l4, list):
            for sc in l4:
                if isinstance(sc, dict):
                    parts.extend(_scene_text(sc))
        l4s = ladder.get("l4_chapters")
        if isinstance(l4s, list):
            for i, sc_list in enumerate(l4s, 1):
                if isinstance(sc_list, list) and sc_list:
                    parts.append(f"【第{i}章场景】")
                    for sc in sc_list:
                        if isinstance(sc, dict):
                            parts.extend(_scene_text(sc))
    return "\n".join(parts)


def _money_entities(text: str) -> list[str]:
    out: list[str] = []
    for m in _MONEY_RE.finditer(text or ""):
        t = m.group(0)
        if t not in out:
            out.append(t)
    return out


def _place_entities(text: str) -> list[str]:
    out: list[str] = []
    for m in _PLACE_RE.finditer(text or ""):
        t = m.group(0)
        if t not in out and any(t.endswith(s) for s in _PLACE_SUFFIX):
            out.append(t)
    return out


async def _collect_entities(ladder: dict[str, Any]) -> list[str]:
    """从阶梯提取必须去实体化的清单：LLM 关键事实（人名/地点/物品/关键事件）+ 确定性金额/地名。"""
    from .ladder import extract_key_facts

    text = _collect_ladder_text(ladder)
    ents: list[str] = []
    seen: set[str] = set()
    l1 = str((ladder or {}).get("l1_minimal") or "")
    src = (l1.strip() or text[:2000]).strip()
    if src:
        try:
            facts = await extract_key_facts(src)
            for f in facts:
                f = str(f or "").strip()
                if f and len(f) <= 24 and f not in seen:
                    seen.add(f)
                    ents.append(f)
        except Exception:
            pass
    for e in _money_entities(text):
        if e not in seen:
            seen.add(e)
            ents.append(e)
    for e in _place_entities(text):
        if e not in seen:
            seen.add(e)
            ents.append(e)
    return ents


# ── 去实体化（LLM 槽位化 + 确定性护栏）──────────────────────────────────

_SLOT_BACKSTOP = "{{实体}}"


def _guard_entities(text: str, entities: list[str]) -> list[str]:
    """护栏：检查槽位化后的文本是否残留源实体。返回残留清单（空 = 干净）。"""
    out: list[str] = []
    for e in entities or []:
        if not e or len(e) < 2:
            continue
        # 槽位里的实体名（如 {{主角}} 不含真实名）不算残留
        if e in (text or ""):
            out.append(e)
    return out


def _strip_entities(text: str, entities: list[str]) -> str:
    """护栏兜底：把残留实体替换成通用槽位。"""
    t = text or ""
    for e in entities or []:
        if e and len(e) >= 2 and e in t:
            t = t.replace(e, _SLOT_BACKSTOP)
    return t


async def _generalize_llm(ladder: dict[str, Any], entities: list[str]) -> dict[str, Any]:
    """LLM 把各级阶梯内容改写成槽位化的通用模板（保留格式/节奏/详略/写作手法）。"""
    l1 = str((ladder or {}).get("l1_minimal") or "").strip()
    l2 = str((ladder or {}).get("l2_arc") or "").strip()
    l3 = (ladder or {}).get("l3_chapter") or {}
    l3_title = str(l3.get("title") or "").strip()
    l3_core = str(l3.get("core") or "").strip()
    l3_beats = "；".join(str(b).strip() for b in (l3.get("beats") or []) if str(b).strip())
    scenes: list[str] = []
    for i, sc in enumerate((ladder or {}).get("l4_scenes") or [], 1):
        if not isinstance(sc, dict):
            continue
        nm = str(sc.get("name") or f"场景{i}").strip()
        acts = "；".join(str(x).strip() for x in (sc.get("actions") or [])[:3])
        dlgs = "；".join(str(x).strip() for x in (sc.get("dialogues") or [])[:3])
        nars = "；".join(str(x).strip() for x in (sc.get("narration") or [])[:2])
        dets = "；".join(str(x).strip() for x in (sc.get("details") or [])[:3])
        scenes.append(f"{i}. {nm}｜动作：{acts}｜对白：{dlgs}｜叙述：{nars}｜细节：{dets}")

    # 【v5.33.7】多章弧形态（l3_chapters/l4_chapters）：原阶梯内容按「第k章」标注拼接
    l3s = (ladder or {}).get("l3_chapters")
    l4s = (ladder or {}).get("l4_chapters")
    is_multi = isinstance(l3s, list) or isinstance(l4s, list)
    if is_multi:
        n = max(len(l3s or []), len(l4s or []))
        seg: list[str] = []
        for k in range(n):
            c = (l3s[k] if isinstance(l3s, list) and k < len(l3s) else {}) or {}
            if isinstance(c, dict):
                ct = str(c.get("title") or f"第{k + 1}章").strip()
                cc = str(c.get("core") or "").strip()
                cb = "；".join(str(b).strip() for b in (c.get("beats") or []) if str(b).strip())
                seg.append(f"第{k + 1}章｜标题『{ct}』核心『{cc}』拍『{cb}』")
            scs = (l4s[k] if isinstance(l4s, list) and k < len(l4s) else []) or []
            for j, sc in enumerate(scs[:4], 1):
                if not isinstance(sc, dict):
                    continue
                nm2 = str(sc.get("name") or f"场景{j}").strip()
                acts = "；".join(str(x).strip() for x in (sc.get("actions") or [])[:3])
                dlgs = "；".join(str(x).strip() for x in (sc.get("dialogues") or [])[:3])
                nars = "；".join(str(x).strip() for x in (sc.get("narration") or [])[:2])
                dets = "；".join(str(x).strip() for x in (sc.get("details") or [])[:3])
                seg.append(f"  {j}. {nm2}｜动作：{acts}｜对白：{dlgs}｜叙述：{nars}｜细节：{dets}")
        ladder_block = "\n".join(seg) if seg else "（无）"
    else:
        ladder_block = (
            f"一句话极简：{l1}\n"
            + f"弧线概要：{l2}\n"
            + f"章核心：标题『{l3_title}』核心『{l3_core}』拍『{l3_beats}』\n"
            + f"场景分解（前若干场景样例）：\n" + ("\n".join(scenes) if scenes else "（无）")
        )

    entity_line = ("、".join(entities[:40])) if entities else "（无）"
    system = (
        "你是「剧情模板去实体化专家」。把给定小说的剧情阶梯（一句话极简/弧线概要/章核心/"
        "场景分解）改写成**可复用的通用模板**。硬性要求：\n"
        "1. 所有专有名词——人名、地名、组织、物品名、金额数字、特定称呼——一律替换成"
        "{{中文语义槽位}}，如 {{主角}}、{{对手}}、{{地点}}、{{关键物}}、{{金额}}、{{事件}}。\n"
        "2. 保留句型结构、叙事节奏、详略安排、冲突/对白/细节/**叙述原句**的写法套路这些"
        "『格式与策略』——叙述句的措辞感（如『一人白衣如雪，一人血迹斑斑』）也保留，"
        "只把实体槽位化。\n"
        "3. 模板里绝对不得残留原小说特有实体（人名/东西/地名）。\n"
        "4. 每个槽位唯一、语义清楚；同一类实体用同一个槽位名。\n"
        "5. 【v5.33.2】额外提炼 fields：9 个『专业模板字段』的填写提示——每个字段用"
        "『这类剧情里该写什么』的一句话描述（不含具体人名/地名/数字/金额），"
        "如 protagonist『被构陷的适龄晚辈』、cast『串通夺产的亲眷、提前候着的知情者』、"
        "conflict『亲眷串通医生假诊断侵吞遗产』；该剧情没有的字段写空字符串。"
    )
    user = (
        f"以下实体必须全部替换成槽位，不得残留：{entity_line}\n\n"
        "【原阶梯内容】\n" + ladder_block
        + "\n\n请输出：\n"
        "description：一句话描述该模板适用的剧情类型（不含实体）。\n"
        "fields：9 个『专业模板字段』的填写提示（见 system 第 5 条，键："
        "protagonist/cast/time_setting/location/conflict/goal/twist/ending/key_item）。\n"
        "levels：四级槽位化模板——\n"
        "  l1：一句话极简的通用写法模板（含完整冲突/事件序列/结局要素的占位）；\n"
        "  l2：弧线概要的通用写法模板（起因/核心冲突/转折/结局各要素槽位化）；\n"
        "  l3：章核心的通用写法模板（标题/核心/拍，每拍一个 {{槽位}}）；\n"
        "  l4：场景分解的通用写法规范（场景怎么拆、7 类叶子怎么写——环境/动作/对白/叙述原句/"
        "心理/冲突/细节，用槽位化范例；叙述原句保留措辞感、只把实体槽位化）。\n"
        "strategy：提炼该剧情的展开策略——scene_density（每类叶子条数）、"
        "scene_count_hint（一章大概几个场景）、target_len_mode（fixed 或 adaptive）、"
        "notes（一句该剧情的写法要点，如『对白是核心推进力』『冲突回合多』）。\n"
        "严格输出如下 JSON（不要 Markdown 代码块）：\n"
        '{"description": "…", "fields": {"protagonist": "…", "cast": "…", '
        '"time_setting": "…", "location": "…", "conflict": "…", "goal": "…", '
        '"twist": "…", "ending": "…", "key_item": "…"}, '
        '"levels": {"l1": "…", "l2": "…", "l3": "…", "l4": "…"}, '
        '"strategy": {"scene_density": 4, "scene_count_hint": "3-5", '
        '"target_len_mode": "adaptive", "notes": "…"}}'
    )
    # 【v5.33.2】复用 derive._tree_llm 健壮 JSON 处理（重试 6 次 + 补逗号修复 +
    # _parse_json_safe + 上次错误喂回）。chat_json 裸 json.loads 对 doubao 的
    # 复杂嵌套 JSON 畸形（fields+levels+strategy）会解析失败。
    from .derive import _tree_llm
    try:
        data = await _tree_llm(system=system, user=user, call_type="plot_template_generalize",
                               max_tokens=6000)
    except RuntimeError as exc:
        raise RuntimeError("模板去实体化失败：" + str(exc))
    levels = data.get("levels")
    if not isinstance(levels, dict) or not any(
        isinstance(levels.get(k), str) and levels[k].strip() for k in ("l1", "l2", "l3", "l4")
    ):
        raise RuntimeError("模板去实体化失败：levels 结构不完整")
    strat = data.get("strategy")
    if not isinstance(strat, dict):
        strat = {}
    strategy = dict(_DEFAULT_STRATEGY)
    for k, v in strat.items():
        if k in strategy and v not in (None, ""):
            strategy[k] = v
    strategy["budget"] = max(15, int(strategy.get("budget", 80)))
    # 【v5.33.2】字段 hint 值（去实体化兜底：残留实体 → {{实体}}）
    fields_raw = data.get("fields")
    fields: dict[str, str] = {}
    if isinstance(fields_raw, dict):
        for k, _ in _TEMPLATE_FIELDS:
            v = str(fields_raw.get(k) or "").strip()
            rem = _guard_entities(v, entities)
            if rem:
                v = _strip_entities(v, rem)
            fields[k] = v
    return {
        "description": str(data.get("description") or "").strip(),
        "fields": fields,
        "levels": levels,
        "strategy": strategy,
    }


# ── 模板库（持久化 + CRUD）──────────────────────────────────────────────

_TEMPLATE_LEVELS = ("l1", "l2", "l3", "l4")


class PlotTemplateLibrary:
    """情节模板库持久化 + CRUD。

    【2026-08-22】底层从 plot_template_library.json 整文件读写换成统一数据库
    data/ainovel.db（ainovel_db.py）。公开 API 不变：list/get/add/delete/bump_usage/
    match_text。旧 json 原地保留作冷备（tools/import_to_ainovel_db.py 幂等导入）。
    内存仍持有全量列表（611 条级别无压力），写路径改单行 SQL——消灭旧实现
    「每次 bump_usage 全量重写 4.5MB 且非原子写」的问题。
    """

    def __init__(self, path: Path | None = None) -> None:
        # path 参数仅为兼容旧签名保留（init_plot_template_library(path)），已无实际作用：
        # 数据统一存 ainovel_db 管理的库文件，与 SETTINGS.data_dir 解耦——
        # 这正是 2026-08-22「模板库 0 条」事故（路径漂移）的根治点。
        self._templates: list[dict[str, Any]] = []
        self._emb_cache: dict[str, list[float]] = {}
        self._load()

    @staticmethod
    def _row_to_template(r) -> dict[str, Any]:
        t: dict[str, Any] = {
            "id": r["id"], "name": r["name"], "description": r["description"],
            "archetype": r["archetype"],
            "source": json.loads(r["source_json"]) if r["source_json"] else {},
            "usage": {"use_count": int(r["use_count"] or 0),
                      "last_used_at": r["last_used_at"],
                      "avg_quality": r["avg_quality"],
                      "n_finalized": int(r["n_finalized"] or 0)},
        }
        for col, key in (("fields_json", "fields"), ("levels_json", "levels"),
                         ("skeleton_json", "skeleton"),
                         ("qualified_json", "qualified"),
                         ("entities_json", "entities")):
            if r[col]:
                t[key] = json.loads(r[col])
        if r["extra_json"]:
            for k, v in json.loads(r["extra_json"]).items():
                t.setdefault(k, v)
        return t

    @staticmethod
    def _tpl_row(t: dict[str, Any], seq: int) -> tuple:
        known = {"id", "name", "description", "archetype", "source", "fields",
                 "levels", "skeleton", "qualified", "entities", "usage"}
        extra = {k: v for k, v in t.items() if k not in known}
        u = t.get("usage") or {}
        src = t.get("source") or {}

        def j(v):
            return json.dumps(v, ensure_ascii=False) if v is not None else None

        return (t.get("id"), t.get("name") or "", t.get("description") or "",
                t.get("archetype") or "", src.get("corpus") or "", src.get("chapter"),
                j(src), j(t.get("fields")), j(t.get("levels")), j(t.get("skeleton")),
                j(t.get("qualified")), j(t.get("entities")),
                j(extra) if extra else None,
                int(u.get("use_count") or 0), u.get("last_used_at"),
                u.get("avg_quality"), int(u.get("n_finalized") or 0),
                src.get("created_at"), seq)

    def _load(self) -> None:
        try:
            from .ainovel_db import get_conn
            rows = get_conn().execute(
                "SELECT * FROM plot_templates ORDER BY seq").fetchall()
            self._templates = [self._row_to_template(r) for r in rows]
        except Exception:
            # 库不可用时保持空列表（与旧 json 缺失行为一致），不让进程崩
            self._templates = []

    def list(self) -> list[dict[str, Any]]:
        return [dict(t) for t in reversed(self._templates)]

    def get(self, template_id: str) -> dict[str, Any] | None:
        for t in self._templates:
            if t.get("id") == template_id:
                return dict(t)
        return None

    def delete(self, template_id: str) -> bool:
        before = len(self._templates)
        self._templates = [t for t in self._templates if t.get("id") != template_id]
        if len(self._templates) != before:
            self._emb_cache.pop(template_id, None)
            from .ainovel_db import get_conn
            conn = get_conn()
            conn.execute("DELETE FROM plot_templates WHERE id=?", (template_id,))
            conn.commit()
            return True
        return False

    def add(self, template: dict[str, Any]) -> dict[str, Any]:
        template.setdefault("usage", {"use_count": 0, "last_used_at": None, "avg_quality": None, "n_finalized": 0})
        self._templates.append(template)
        from .ainovel_db import get_conn
        conn = get_conn()
        seq = conn.execute(
            "SELECT COALESCE(MAX(seq),-1)+1 FROM plot_templates").fetchone()[0]
        conn.execute(
            "INSERT INTO plot_templates(id,name,description,archetype,corpus,"
            "chapter_num,source_json,fields_json,levels_json,skeleton_json,"
            "qualified_json,entities_json,extra_json,use_count,last_used_at,"
            "avg_quality,n_finalized,created_at,seq) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            self._tpl_row(template, seq))
        conn.commit()
        return dict(template)

    def bump_usage(self, template_id: str, quality: float | None = None) -> bool:
        """【Phase C·反馈闭环】模板被套用/命中时自增用量；finalize 回写平均质量。

        quality=None：仅记录一次使用（new_arc 命中 / arc_set_template 套用）。
        quality 非 None：记录一次落盘结果，并滚动更新 avg_quality（n_finalized 加权）。
        """
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc).isoformat()
        for t in self._templates:
            if t.get("id") != template_id:
                continue
            u = t.setdefault("usage", {"use_count": 0, "last_used_at": None, "avg_quality": None, "n_finalized": 0})
            u["use_count"] = int(u.get("use_count") or 0) + 1
            u["last_used_at"] = now
            if quality is not None:
                n = int(u.get("n_finalized") or 0)
                avg = u.get("avg_quality")
                u["n_finalized"] = n + 1
                u["avg_quality"] = round(float(avg or 0) * n / (n + 1) + quality / (n + 1), 4)
            from .ainovel_db import get_conn
            conn = get_conn()
            conn.execute(
                "UPDATE plot_templates SET use_count=?,last_used_at=?,"
                "avg_quality=?,n_finalized=? WHERE id=?",
                (u["use_count"], u["last_used_at"], u["avg_quality"],
                 u["n_finalized"], template_id))
            conn.commit()
            return True
        return False

    def match_text(self, t: dict[str, Any]) -> str:
        """模板的检索文本（嵌入用）：name + description + 字段 + 各级模板文本。"""
        parts = [str(t.get("name") or ""), str(t.get("description") or "")]
        fields = t.get("fields")
        if isinstance(fields, dict):
            for k, label in _TEMPLATE_FIELDS:
                v = str(fields.get(k) or "").strip()
                if v:
                    parts.append(f"{label}：{v}")
        for k in _TEMPLATE_LEVELS:
            lv = (t.get("levels") or {}).get(k)
            if isinstance(lv, dict):
                parts.append(str(lv.get("text") or ""))
            elif isinstance(lv, str):
                parts.append(lv)
        return "\n".join(p for p in parts if p)


# 模块级单例（init_prompt_harness 后可用；缺省懒加载）
_library: PlotTemplateLibrary | None = None


def get_plot_template_library() -> PlotTemplateLibrary:
    global _library
    if _library is None:
        _library = PlotTemplateLibrary()
    return _library


def init_plot_template_library(path: Path | None = None) -> PlotTemplateLibrary:
    global _library
    _library = PlotTemplateLibrary(path)
    return _library


# ── 创建模板（合格阶梯 → 去实体化入库）──────────────────────────────────

async def create_plot_template(
    name: str,
    ladder: dict[str, Any],
    *,
    qualified: dict[str, Any] | None = None,
    corpus: str = "",
    chapter: int | None = None,
    chapter_start: int | None = None,
    chapter_end: int | None = None,
    archetype: str = "",
) -> dict[str, Any]:
    """把一份合格的压缩阶梯去实体化并存入情节模板库。

    ladder：单章 {l1_minimal, l2_arc, l3_chapter{title,core,beats}, l4_scenes:[...]}，
    或多章弧 {l1_minimal, l2_arc, l3_chapters:[章核心...], l4_chapters:[章场景列表...]}。
    qualified：达标判定结果（节拍覆盖 coverage 近似 score；s_char 等已停用，见 extract_from_doc）。
    【v5.33.7】多章弧模板：chapter_start/chapter_end 记弧的章范围；单章仍用 chapter。
    """
    library = get_plot_template_library()
    entities = await _collect_entities(ladder)
    gen = await _generalize_llm(ladder, entities)

    # 【v5.33 修复】自动命名：名字是「第N章模板」这类无意义名时，改用去实体化描述前段——
    # 否则 match 的 LLM 仲裁看到无意义候选名会臆造名字（不逐字复制）→ 全被 valid 过滤 → 空 reason。
    name = (name or "").strip()
    if not name or re.fullmatch(r"第\d+章模板", name) or name == "未命名模板":
        name = str(gen.get("description") or "").strip()[:18] or "未命名模板"

    levels: dict[str, Any] = {}
    for k in _TEMPLATE_LEVELS:
        raw = str((gen["levels"] or {}).get(k) or "").strip()
        remaining = _guard_entities(raw, entities)
        if remaining:
            raw = _strip_entities(raw, remaining)
            remaining = _guard_entities(raw, entities)
        levels[k] = {"text": raw, "guarded": len(remaining) == 0}
    l1_clean = re.sub(r"\s+", "", levels["l1"]["text"] or "")
    budget = int((gen["strategy"] or {}).get("budget", 80))
    if l1_clean:
        levels["l1"]["budget"] = max(15, min(budget, max(15, len(l1_clean))))

    template = {
        "id": "ptpl_" + uuid.uuid4().hex[:12],
        "name": (name or "未命名模板").strip()[:40],
        "description": str(gen.get("description") or "").strip(),
        "archetype": str(archetype or "").strip(),
        "source": {
            "type": "qualified_ladder",
            "corpus": str(corpus or "").strip(),
            "chapter": chapter,
            "chapter_start": chapter_start,
            "chapter_end": chapter_end,   # 【v5.33.7】多章弧模板记章范围
            "created_at": _now(),
        },
        "qualified": dict(qualified or {}),
        "entities": entities,   # 源实体清单（诊断用；已从模板中剔除）
        "fields": dict(gen.get("fields") or {}),   # 【v5.33.2】专业字段表单（label/hint）
        "levels": levels,
        "strategy": gen["strategy"],
    }
    library.add(template)
    return dict(template)


# ── 命中（主系统写新剧情 → 匹配模板）────────────────────────────────────

async def _select_templates_llm(
    query: str,
    cands: list[dict[str, Any]],
) -> tuple[list[str], dict[str, str]]:
    """LLM 从候选模板里选 1-3 个最匹配的（打乱防锚定）。失败返回 ([], {})。"""
    pool = [dict(c) for c in cands]
    random.shuffle(pool)
    cand_lines = "\n".join(
        f"{i + 1}. {c['name']}（{c.get('description') or ''}）" for i, c in enumerate(pool)
    )
    system = "你是「情节模板匹配专家」。根据用户给出的剧情描述，从候选情节模板里选出最匹配的 1-3 个，作为 1→12345 逐级展开的格式与策略参考。"
    user = (
        "用户剧情：\n" + (query or "").strip()
        + "\n\n候选情节模板：\n" + cand_lines
        + "\n\n请选 1-3 个最匹配的。硬性要求："
          "name 必须从上表候选中【逐字复制】完整的候选名，不得改写、自拟、混入剧情描述；"
          "剧情类型相同/写法套路相近者优先。"
          "**题材/世界观必须相符**：若用户剧情的题材（如星际科幻/现代都市/校园）与候选模板的"
          "题材（如古装武侠）明显不符——即使有字面相似的『觉醒/阴谋/威胁』等词，也**不得选**，"
          "宁可不命中（输出空数组），也不要套用题材不搭的模板劣化生成。"
          "没有真正同款的可以只选 1 个最接近的，甚至 0 个（都不像/题材不符时输出空数组）。"
        + "严格输出如下 JSON（不要 Markdown 代码块）：\n"
        '{"templates": [{"name": "（上表候选名逐字复制）", "reason": "为什么匹配"}]}'
    )
    result = await chat_json(system=system, user=user, call_type="plot_template_match",
                             max_tokens=1200)
    if result.get("error") or not isinstance(result.get("data"), dict):
        return [], {}
    valid = {c["name"] for c in cands}
    names: list[str] = []
    reasons: dict[str, str] = {}
    for a in result["data"].get("templates") or []:
        if not isinstance(a, dict):
            continue
        nm = str(a.get("name") or "").strip()
        if nm in valid and nm not in names:
            names.append(nm)
            reasons[nm] = str(a.get("reason") or "").strip()
    return names[:3], reasons


async def match_plot_templates(
    query: str,
    *,
    style: str = "",
    role_setting: str = "",
    top_k: int = 6,
) -> list[dict[str, Any]]:
    """剧情描述 → 命中情节模板库（embedding 召回 top-K + LLM 仲裁选 1-3 个）。

    返回 [{id, name, description, archetype, similarity, reason, qualified, levels_text}]。
    库为空时返回 []。
    """
    library = get_plot_template_library()
    tpls = library.list()
    if not tpls:
        return []
    q = (query or "").strip()
    if (style or "").strip():
        q += "\n风格基调：" + style.strip()
    if (role_setting or "").strip():
        q += "\n角色设定：" + role_setting.strip()
    texts = [library.match_text(t) for t in tpls]
    # 嵌入缓存（按 id 记忆；模板不变则复用）
    need: list[int] = []
    for i, t in enumerate(tpls):
        if t.get("id") not in library._emb_cache:
            need.append(i)
    if need:
        try:
            vecs = await get_embeddings([texts[i] for i in need])
            for j, i in enumerate(need):
                if j < len(vecs):
                    library._emb_cache[tpls[i]["id"]] = vecs[j]
        except Exception:
            # 嵌入失败降级：用文本前缀相似 + LLM 仲裁
            for i in need:
                library._emb_cache[tpls[i]["id"]] = [0.0]
    # 查询向量失败降级：相似度全 0，靠 LLM 仲裁（_select_templates_llm 读真实文本选）
    qv = None
    try:
        _qvec = await get_embeddings([q or "剧情"])
        qv = _qvec[0] if _qvec else None
    except Exception:
        qv = None
    scored: list[tuple[float, dict[str, Any]]] = []
    for i, t in enumerate(tpls):
        v = library._emb_cache.get(t.get("id"))
        if v is None:
            continue
        sim = cosine_similarity(qv, v) if qv is not None else 0.0
        scored.append((sim, t))
    scored.sort(key=lambda x: x[0], reverse=True)
    # 【Phase B 修正·反同质化】appeal 只做「淘汰过时」，不做排序偏好：
    # 全维度都不流行(过时)的模板剔除，其余按结构相似度排序——避免每次命中同一批高appeal模板。
    # 四维 appeal profile 暴露给下游（创作助手生成时参考当前流行维度）。
    # 【Phase B·题材感知】检测当前弧题材（从 l1 + 风格 + 角色设定），appeal 按题材条件化：
    # 修仙弧看苟道/宗门，宫斗弧看大女主/重生——不再全局一刀切。
    from .trend_lib import appeal_profile_from_vec, is_outdated, detect_genre
    _genre = detect_genre(f"{q} {style or ''} {role_setting or ''}")
    _appeals: dict[str, dict[str, float]] = {}
    _kept: list[tuple[float, dict[str, Any]]] = []
    for s, t in scored[:max(top_k * 2, 6)]:
        v = library._emb_cache.get(t.get("id"))
        prof = await appeal_profile_from_vec(v, focus=_genre) if v else {}
        _appeals[t.get("name") or ""] = {k: round(val, 3) for k, val in prof.items()}
        if v is None or not await is_outdated(prof):
            _kept.append((s, t))
    if not _kept:
        _kept = scored[:top_k]  # 全过时则放行（退化），避免无候选
    _kept.sort(key=lambda x: x[0], reverse=True)
    cands = [t for _, t in _kept[:top_k]]
    sims = {t["name"]: round(float(s), 4) for s, t in _kept[:top_k]}
    names, reasons = await _select_templates_llm(q, cands)
    if not names and _kept:
        # 回退：LLM 仲裁失败/臆造名被过滤 → 取嵌入相似度最高的候选（非任意序 cands[0]）
        names = [_kept[0][1]["name"]]
        reasons[names[0]] = "按剧情嵌入相似度最高命中"
    out: list[dict[str, Any]] = []
    by_name = {t["name"]: t for t in tpls}
    for nm in names:
        t = by_name.get(nm)
        if t is None:
            continue
        out.append({
            "id": t.get("id"),
            "name": t.get("name"),
            "description": t.get("description"),
            "archetype": t.get("archetype"),
            "similarity": sims.get(nm, 0.0),
            "appeal": _appeals.get(nm, {}),   # 【Phase B】四维流行度 {背景,主题,设定,受众}
            "genre": _genre,                   # 【Phase B】检测到的当前弧题材
            "reason": reasons.get(nm, ""),
            "qualified": t.get("qualified"),
            "strategy": t.get("strategy"),
            # 【v5.33 修复】levels 全结构（{text,guarded}）+ levels_text（扁平展示）都要——
            # bridge 用 template_format_block(template, "l2") 读 levels[level].text 注入格式，
            # 只给 levels_text 会导致命中模板后格式参考为空（仅 strategy 生效）。
            "levels": t.get("levels"),
            "levels_text": {k: (t.get("levels") or {}).get(k, {}).get("text", "") for k in _TEMPLATE_LEVELS},
        })
    return out


# ── 模板格式注入块（bridge 展开时套用模板格式 + 策略）────────────────────

def template_format_block(template: dict[str, Any] | None, level: str) -> str:
    """构造某级(2/3/4)的模板格式注入块（槽位化范例，供参考）。

    【v7.8.2 措辞注入增强】槽位文本**保留了原文的说话神态/动作句式/细节措辞**
    （如『轻叹一声』『揉了揉眼睛』『眉眼间有了笑意』）——要求模型填充时**保留这些
    措辞、只替换实体**，让生成场景带原文措辞感，而非退化成通用『说道』。
    """
    if not isinstance(template, dict):
        return ""
    lv = (template.get("levels") or {}).get(level)
    txt = ""
    if isinstance(lv, dict):
        txt = str(lv.get("text") or "").strip()
    elif isinstance(lv, str):
        txt = lv.strip()
    if not txt:
        return ""
    return (
        "【同款剧情模板·格式参考】（槽位已泛化，但**保留了原文的说话神态/动作句式/细节措辞**；"
        "填充时**保留这些措辞，只替换实体**——人物/地点/物品/组织名换成新书的；"
        "说话动词/神态逐轮用模板原词（如『轻叹一声』『揉了揉眼睛』『眉眼间有了笑意』），"
        "不概括成『说道』；动作/细节/环境保留模板的措辞与句式）：\n" + txt
    )


def strategy_overrides(template: dict[str, Any] | None) -> dict[str, Any]:
    """从模板取策略覆盖项（供 bridge/生成端使用）。"""
    if not isinstance(template, dict):
        return {}
    s = template.get("strategy")
    return dict(s) if isinstance(s, dict) else {}


# ── 专业模板字段表单（v5.33.2）─────────────────────────────────────────

def template_fields_block(template: dict[str, Any] | None) -> str:
    """构造【同款剧情模板·字段】块：9 字段表单（label + hint 该填什么），供 bridge 注入。"""
    if not isinstance(template, dict):
        return ""
    fields = template.get("fields")
    if not isinstance(fields, dict):
        return ""
    lines = []
    for k, label in _TEMPLATE_FIELDS:
        hint = str(fields.get(k) or "").strip()
        slot = "{{" + k + "}}"
        lines.append(f"- {label}：{slot}（{hint}）" if hint else f"- {label}：{slot}")
    if not lines:
        return ""
    return "【同款剧情模板·字段】按这套字段组织你的剧情（hint 供参考，把 {{key}} 换成你填的实体）：\n" + "\n".join(lines)


def template_fields(template: dict[str, Any] | None) -> list[dict[str, str]]:
    """模板字段表单（前端展示用）：[{key, label, hint}]。"""
    if not isinstance(template, dict):
        return []
    fields = template.get("fields")
    fields = fields if isinstance(fields, dict) else {}
    return [{"key": k, "label": label, "hint": str(fields.get(k) or "").strip()}
            for k, label in _TEMPLATE_FIELDS]


async def assemble_l1(
    field_values: dict[str, Any] | None,
    template: dict[str, Any] | None,
    *,
    style: str = "",
    role_setting: str = "",
) -> str:
    """用填好的模板字段组装一句话极简 l1（字段值 + 模板 l1 骨架作格式参考）。

    返回组装好的 l1 文本；无任何填好的字段时返回 ""（调用方回退手写 l1）。
    """
    fv = field_values or {}
    filled = {k: str(v).strip() for k, v in fv.items() if str(v or "").strip()}
    if not filled:
        return ""
    lines = [f"{_FIELD_LABELS.get(k, k)}：{v}" for k, v in filled.items()]
    skel = ""
    if isinstance(template, dict):
        l1l = (template.get("levels") or {}).get("l1")
        if isinstance(l1l, dict):
            skel = str(l1l.get("text") or "").strip()
        elif isinstance(l1l, str):
            skel = l1l.strip()
    sys_p = (
        "你是「小说一句话极简编剧」。把用户填好的模板字段组织成一句话极简剧情（l1），"
        "作为 1→12345 阶梯的起点。要求：一句话、完整陈述（含主角/事件/冲突/结局倾向），"
        "必须保留用户填写的全部具体人名/地点/数字，不得自行改名或丢要素。"
    )
    from .fixed_prompts import get_ladder_invariant  # v6.3 l1 不变prompt
    inv1 = get_ladder_invariant("l1")
    usr_p = (
        "【已填字段】\n" + "\n".join(lines)
        + (("\n\n【风格基调】" + style) if style else "")
        + (("\n【角色设定】" + role_setting) if role_setting else "")
        + (("\n\n【同款模板 l1 写法参考】（仅参考句式结构，人物/事件用你填的字段）\n" + skel) if skel else "")
        + (("\n\n" + inv1) if inv1 else "")
        + "\n\n直接输出一句话极简剧情（不要解释、不要 Markdown 代码块）："
    )
    r = await chat_completion(
        system=sys_p, user=usr_p, call_type="bridge_assemble_l1",
        temperature=0.5, max_tokens=300,
    )
    if r.get("error") or not (r.get("content") or "").strip():
        raise RuntimeError("l1 组装失败：" + str(r.get("error") or "空输出"))
    return re.sub(r"^```[a-zA-Z]*\s*|\s*```$", "", (r.get("content") or "").strip()).strip()


# ── 从文档榨干提取（单按钮的 backend：doc → 各章阶梯 → 合格入库）──────────

async def _infer_style_role(text: str) -> dict[str, str]:
    """从原文自动提取风格基调 + 角色设定（用户不手填，系统自取）。

    只读首章开头即可判断全书基调；角色设定取本章出场核心角色 + 世界观背景。
    """
    from .derive import _tree_llm

    d = await _tree_llm(
        "你是「文风与设定分析师」。阅读一段小说原文，提炼本书两点设定："
        "① 风格基调——叙事语气、氛围、节奏，用一句话概括（如「冷峻克制的武侠正剧」）；"
        "② 角色设定——主角与本章出场核心角色的身份/性格/相互关系，加上世界观背景，2-3 句大白话。",
        "【原文节选】\n" + (text or "")[:1500]
        + "\n\n严格输出如下 JSON（不要 Markdown 代码块）：\n"
        '{"style": "风格基调一句话", "role_setting": "角色与世界观设定"}',
        "ladder_style_role", max_tokens=800,
    )
    return {
        "style": str((d.get("style") if isinstance(d, dict) else None) or "").strip(),
        "role_setting": str((d.get("role_setting") if isinstance(d, dict) else None) or "").strip(),
    }


async def _merge_arc_l12(
    chapter_ladders: list[dict[str, Any]],
    style: str = "",
    role_setting: str = "",
    budget: int = 80,
) -> tuple[str, str]:
    """【v5.33.7】弧级 l1/l2 合并：LLM 读弧内各章的 l3 core + beats，合并成弧级
    l1（一句话极简，覆盖整条弧的起因/冲突/转折/结局）+ l2（弧线概要 2-4 句）。

    单章弧不调 LLM，直接用该章的 l1/l2。
    """
    from .derive import _tree_llm

    if len(chapter_ladders) == 1:
        ld = chapter_ladders[0]
        return (str(ld.get("l1_minimal") or "").strip(),
                str(ld.get("l2_arc") or "").strip())
    lines = []
    for i, ld in enumerate(chapter_ladders, 1):
        l3 = ld.get("l3_chapter") or {}
        title = str(l3.get("title") or f"第{i}章").strip()
        core = str(l3.get("core") or "").strip()
        beats = "；".join(str(b).strip() for b in (l3.get("beats") or []) if str(b).strip())
        lines.append(f"第{i}章「{title}」核心：{core}｜拍：{beats}")
    digest = "\n".join(lines)
    d = await _tree_llm(
        "你是「剧情弧线合并专家」。给定同一剧情弧内各章的章核心（按序，可能多章），"
        "合并成弧级两级：① l1 一句话极简剧情——用一句话覆盖整条弧的起因/核心冲突/转折/结局，"
        f"不超过 {budget} 字，大白话，不要引号；② l2 弧线概要——2-4 句，人物地点齐全，"
        "足以据此重建整条弧各章的关键事件。",
        (f"风格基调：{style}\n" if (style or "").strip() else "")
        + (f"角色设定：{role_setting}\n" if (role_setting or "").strip() else "")
        + "【弧内各章核心】\n" + digest
        + "\n\n严格输出如下 JSON（不要 Markdown 代码块）：\n"
        '{"l1": "一句话极简", "l2": "2-4句弧线概要"}',
        "ladder_arc_merge", max_tokens=1600,
    )
    l1 = str((d.get("l1") if isinstance(d, dict) else None) or "").strip()
    l2 = str((d.get("l2") if isinstance(d, dict) else None) or "").strip()
    if not l1:
        l1 = str(chapter_ladders[0].get("l1_minimal") or "").strip()
    if not l2:
        l2 = str(chapter_ladders[0].get("l2_arc") or "").strip()
    return l1, l2


def _group_chapters_into_arcs(
    built: list[dict[str, Any]],
    arcs: list[dict[str, Any]] | None,
) -> list[dict[str, Any]]:
    """把已建阶梯的章节按剧情弧分组（复用 arc_map 的弧边界）。

    built：[{idx(1-based), num(真实章号), text, ladder}]。
    arcs：[{start_chapter, end_chapter, archetype, description, ...}]（已按选择范围裁剪）。
    返回 groups：[{name, archetype, start, end, chapters:[built条目...]}]。
    无 arcs 或单章 → 每章一弧（兼容旧行为）；章号不落在任何弧 → 自成弧。
    """
    if not built:
        return []
    if (not arcs) or len(built) == 1:
        return [{
            "name": f"第{c['num']}章" if c.get("num") else f"第{c['idx']}章",
            "archetype": "", "start": c.get("num") or c["idx"], "end": c.get("num") or c["idx"],
            "chapters": [c],
        } for c in built]
    # 按弧边界匹配（arcs 按 start 排序）
    arcs_sorted = sorted(arcs, key=lambda a: (a.get("start_chapter") or 0, a.get("end_chapter") or 0))
    groups: list[dict[str, Any]] = []
    used: set[int] = set()
    for a in arcs_sorted:
        s = a.get("start_chapter") or 0
        e = a.get("end_chapter") or 0
        matched = []
        for i, c in enumerate(built):
            if i in used:
                continue
            num = c.get("num")
            if num is not None and s <= num <= e:
                matched.append(c)
        if not matched:
            continue
        for c in matched:
            used.add(built.index(c))
        nums = [c["num"] for c in matched]
        groups.append({
            "name": str(a.get("description") or a.get("name") or f"第{min(nums)}-{max(nums)}章").strip(),
            "archetype": str(a.get("archetype") or "").strip(),
            "start": min(nums), "end": max(nums),
            "chapters": matched,
        })
    # 未命中任何弧的章 → 自成弧
    for i, c in enumerate(built):
        if i not in used:
            groups.append({
                "name": f"第{c['num']}章" if c.get("num") else f"第{c['idx']}章",
                "archetype": "", "start": c.get("num") or c["idx"], "end": c.get("num") or c["idx"],
                "chapters": [c],
            })
    groups.sort(key=lambda g: (g["start"], g["end"]))
    return groups


# 【算法结构重构 2026-08-15】达标从「重建原文的散文保真(s_char)」改为「节拍覆盖」：
# 模板的价值是能支撑一节剧情，不是能默写原文。节拍覆盖判定 1 次 LLM 调用（廉价），
# 且对"事件对、措辞不同"的差一口气章更宽容——实测 45/47 章从 0.59/0.61 救回达标。
_BEAT_COV_MIN = 0.6  # 节拍覆盖达标线


async def _judge_beat_coverage(orig_text: str, beats: str) -> dict[str, Any]:
    """LLM 判 l3 节拍 vs 章节原文的覆盖率。返回 {coverage, rebuildable, missing}。
    判定失败不阻塞：退回不达标（宁可少入库，不误放低质）。"""
    from .llm_client import chat_completion
    beats = (beats or "").strip()
    if not beats:
        return {"coverage": 0.0, "rebuildable": False, "missing": ["节拍为空"]}
    user = (
        "【章节原文】\n" + (orig_text or "")[:3000]
        + "\n\n【提取的节拍】\n" + beats
        + "\n\n判断这些节拍是否覆盖了章节的关键事件/冲突/转折（事件对即可，不需逐字）。"
        '严格输出JSON: {"coverage":0-1,"rebuildable":true/false,"missing":[]}'
    )
    try:
        resp = await chat_completion(
            system="你是网络小说剧情节拍覆盖率评估专家。",
            user=user, max_tokens=400, call_type="beat_coverage")
        content = (resp.get("content") or "").strip()
        if content.startswith("```"):
            content = content.split("\n", 1)[1].rsplit("```", 1)[0]
        o = json.loads(content)
        return {
            "coverage": float(o.get("coverage") or 0.0),
            "rebuildable": bool(o.get("rebuildable")),
            "missing": [str(m) for m in (o.get("missing") or [])],
        }
    except Exception:
        return {"coverage": 0.0, "rebuildable": False, "missing": ["判定失败"]}


async def extract_from_doc(
    chapters: list[str],
    *,
    corpus: str = "",
    style: str = "",
    role_setting: str = "",
    budget: int = 80,
    min_score: float = 0.65,
    chapter_nums: list[int] | None = None,
    arcs: list[dict[str, Any]] | None = None,
    progress: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    """对选定文档榨干：逐章 build_ladder（从原文提取压缩阶梯）→ 按剧情弧分组 →
    弧级 l1/l2 合并 → 节拍覆盖达标判定（coverage ≥ _BEAT_COV_MIN）→ 达标章标 qualified。

    【2026-08-15 降本】不再 verify_ladder 全量重建（烧 54% token，重建分消费端不看）——
    达标判定由节拍覆盖承担，score 用 coverage 近似（兼容 store 端 min_score 兜底过滤）。

    返回 {total, qualified, skipped, templates:[], report:[弧条目], style, role_setting, corpus}。
    """
    from .ladder import build_ladder

    texts: list[str] = []
    nums: list[int] = []
    for i, t in enumerate(chapters or []):
        if t and str(t).strip():
            texts.append(t)
            nums.append(int(chapter_nums[i]) if (chapter_nums and i < len(chapter_nums)) else i + 1)
    if not texts:
        return {"total": 0, "qualified": 0, "skipped": [], "templates": [], "report": []}
    # 【v5.33.3】风格/角色不手填，两参皆空时从原文自动提取（失败不阻塞，留空让 LLM 自行领会）
    if not (style or "").strip() and not (role_setting or "").strip():
        if progress:
            progress({"phase": "infer_style_role", "message": "自动提取本书风格基调与角色设定…"})
        try:
            inferred = await _infer_style_role(texts[0])
            style = inferred.get("style") or ""
            role_setting = inferred.get("role_setting") or ""
            if progress and (style or role_setting):
                progress({"phase": "infer_style_role",
                          "message": f"自动提取：风格「{style}」｜角色「{role_setting[:40]}…」"})
        except Exception:
            pass
    skipped: list[dict[str, Any]] = []
    # 【并行化 2026-08-15】① 并发逐章建阶梯（Semaphore 限流，gather 保序，逐完成报进度）
    sem = asyncio.Semaphore(_EXTRACT_CONCURRENCY)
    _done = 0

    async def _build_one(i: int, t: str, num: int) -> dict[str, Any]:
        nonlocal _done
        try:
            async with sem:
                ladder = await build_ladder(t, style=style, role_setting=role_setting, budget=budget)
            return {"idx": i, "num": num, "text": t, "ladder": ladder, "error": None}
        except Exception as exc:
            return {"idx": i, "num": num, "text": t, "ladder": None, "error": exc}
        finally:
            _done += 1
            if progress:
                progress({"chapter": _done, "total": len(texts), "phase": "build_ladder",
                          "message": f"并发建阶梯 {_done}/{len(texts)}"})

    _built_res = await asyncio.gather(
        *[_build_one(i + 1, t, num) for i, (t, num) in enumerate(zip(texts, nums))])
    built: list[dict[str, Any]] = []
    for r in _built_res:  # gather 保序
        if r["error"] is not None:
            skipped.append({"chapter": r["idx"], "chapter_num": r["num"],
                            "reason": f"build_ladder 失败：{r['error']}"})
            continue
        built.append({"idx": r["idx"], "num": r["num"], "text": r["text"], "ladder": r["ladder"]})
    # ② 按剧情弧分组
    groups = _group_chapters_into_arcs(built, arcs)
    # ③ 弧级 l1/l2 合并（弧间并发）
    _merged = await asyncio.gather(*[
        _merge_arc_l12([c["ladder"] for c in g["chapters"]], style, role_setting, budget)
        for g in groups])
    for g, (l1, l2) in zip(groups, _merged):
        g["l1"], g["l2"] = l1, l2
    # ④【节拍覆盖达标】先廉价判达标（l3 节拍 vs 原文覆盖率，1 次 LLM/章）——
    #    达标章才跑全量重建（产出 prose + s_char 质量分，不再参与达标判定）。
    _cov_done = 0
    _work = [(gi, ci, c)
             for gi, g in enumerate(groups, 1)
             for ci, c in enumerate(g["chapters"])]

    async def _cov_one(gi: int, ci: int, c: dict) -> tuple[int, int, dict]:
        nonlocal _cov_done
        try:
            async with sem:
                l3 = (c["ladder"] or {}).get("l3_chapter") or {}
                beats = "；".join(str(b) for b in (l3.get("beats") or []))
                cov = await _judge_beat_coverage(c["text"], beats)
            return (gi, ci, cov)
        except Exception as exc:
            return (gi, ci, {"coverage": 0.0, "rebuildable": False, "missing": [str(exc)[:40]]})
        finally:
            _cov_done += 1
            if progress:
                progress({"chapter": _cov_done, "total": len(_work), "phase": "beat_coverage",
                          "message": f"节拍覆盖判定 {_cov_done}/{len(_work)}"})

    _cmap = {(gi, ci): cov
             for gi, ci, cov in await asyncio.gather(*[_cov_one(gi, ci, c) for gi, ci, c in _work])}

    def _cov_pass(cov: dict) -> bool:
        return bool(cov.get("rebuildable")) and float(cov.get("coverage") or 0.0) >= _BEAT_COV_MIN

    # 【2026-08-15 降本】达标章不再全量重建（verify_ladder 烧 54% token，重建分消费端不看）。
    # 达标判定已由节拍覆盖（_cov_pass）承担；qualified=True 直接入库。report 的 scores 用
    # 节拍覆盖分近似，prose 留空——消费端（match/new_arc）只看 l1/l2 相似度，不看 s_char。
    _vmap: dict[tuple[int, int], tuple[dict, None]] = {}

    report: list[dict[str, Any]] = []
    for gi, g in enumerate(groups, 1):
        arc_entry: dict[str, Any] = {
            "arc": gi,
            "name": g.get("name") or f"第{g['start']}-{g['end']}章",
            "archetype": g.get("archetype") or "",
            "start_chapter": g["start"], "end_chapter": g["end"],
            "l1": g.get("l1") or "", "l2": g.get("l2") or "",
            "qualified": False, "template_id": None, "template_name": None,
            "chapters": [],
        }
        for ci, c in enumerate(g["chapters"]):
            cov = _cmap[(gi, ci)]
            if not _cov_pass(cov):
                skipped.append({"chapter": c["idx"], "chapter_num": c["num"],
                                "reason": f"节拍覆盖不足（{cov.get('coverage')} < {_BEAT_COV_MIN}）",
                                "missing": (cov.get("missing") or [])[:3]})
                continue
            # 达标 = 节拍覆盖判定通过（_cov_pass）。score 用 coverage 近似（0-1），
            # 兼容 store 端 `qualified or score >= min_score` 的兜底过滤。
            score = float(cov.get("coverage") or 0.0)
            skeleton = {
                "l1": str((c["ladder"] or {}).get("l1_minimal") or "").strip(),
                "l2": str((c["ladder"] or {}).get("l2_arc") or "").strip(),
                "l3": (c["ladder"] or {}).get("l3_chapter") or {},
                "l4": (c["ladder"] or {}).get("l4_scenes") or [],
            }
            ch_entry: dict[str, Any] = {
                "chapter": c["idx"], "chapter_num": c["num"],
                "score": score, "qualified": True, "template_id": None,
                "coverage": cov.get("coverage"),
                "scores": {
                    "score": score,
                    "s_char": None, "turn_fidelity": None,
                    "ai_flavor": None, "len_ratio": None,
                },
                "skeleton": skeleton, "prose": "",
            }
            arc_entry["chapters"].append(ch_entry)
        # 弧合格 = 弧内所有章均达标（且至少一章）
        chs = arc_entry["chapters"]
        arc_entry["qualified"] = bool(chs) and all(c.get("qualified") for c in chs)
        report.append(arc_entry)
    return {
        "total": len(texts),
        "qualified": len([r for r in report if r.get("qualified")]),
        "skipped": skipped,
        "templates": [],   # v5.33.4 用户点击入库，不再自动
        "report": report,
        "style": style,
        "role_setting": role_setting,
        # 【v5.33.6】来源书（corpus 相对路径），extract-store 入库时写进模板 source
        "corpus": str(corpus or "").strip(),
        # 【v5.33.7】弧数（前端可显示）
        "arc_count": len(report),
    }
