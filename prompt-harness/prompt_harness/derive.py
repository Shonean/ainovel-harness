# -*- coding: utf-8 -*-
"""剧情推导（v5.26 剧情库驱动）：极简剧情 → 命中剧情库原型 → 弧线层展开 → 填模板。

背景：用户训练出优秀 prompt 模板后，没法直接拿它写作——用户只能给「极简剧情」。
v5.23 版本是自由发散（根→章→场景），不参考剧情库。v5.26 改为**弧线驱动**：
先对极简剧情做 embedding 召回 + LLM 仲裁命中剧情库原型（1-4 个），注入该原型
在真实小说（arc_map）里的切法范例，再展开为「根→弧线→章→场景→6 类叶子」的树，
每条弧线挂一个剧情库原型。树的各层内容再按模板变量槽（node_type）归集，替换
{{key}} 占位符，产出趋近模板的完整 prompt（正文生成交给主系统）。

入口：
- match_archetypes(root_plot, style, role_setting) -> [命中原型（含 real_arcs）]
- expand_plot_tree(root_plot, style, role_setting, archetypes=None) -> tree
- render_prompt(template, tree) -> {prompt, slots_filled, slots_missing}
"""
from __future__ import annotations

import json
import random
import re
from collections import defaultdict
from typing import Any

from .arc_classify import _label_embeddings, _label_pool, _load_registry, _parse_json_safe
from .embed_client import cosine_similarity, get_embeddings
from .llm_client import chat_json

# 6 类叶子在场景 dict 里的字段名（环境是单串，其余是 list）
_LEAF_FIELDS: dict[str, str] = {
    "environment": "environment",
    "action": "actions",
    "dialogue": "dialogues",
    "psychology": "psychologies",
    "conflict": "conflicts",
    "detail": "details",
}

PLACEHOLDER_RE = re.compile(r"\{\{\s*([a-zA-Z0-9_]+)\s*\}\}")


def _repair_tree_json(raw: str) -> str:
    """修复剧情树 JSON 的缺逗号（LLM 超长嵌套输出常见畸形）。

    状态机：跟踪字符串内/外，在「值/闭合（数字/字符串/true/false/null/}/]）」之后
    紧跟下一个元素（"key": 或 { 或 [ 或字面值）且缺逗号时补逗号。只修结构逗号，
    绝不改动字符串内容（正则无法区分字符串内外的 }"/[ 会误伤，故用状态机）。
    """
    s = raw.strip()
    if s.startswith("```"):
        s = re.sub(r"^```[^\n]*\n", "", s)
        s = re.sub(r"\n```\s*$", "", s)
    s = s.strip()
    out: list[str] = []
    i, n = 0, len(s)
    in_str = False
    escape = False
    prev = None  # 'open'({/[) / 'close'(}/]) / 'comma' / 'colon' / 'key' / 'value'

    def need_comma(p: str | None) -> bool:
        return p in ("close", "value")

    while i < n:
        c = s[i]
        if in_str:
            out.append(c)
            if escape:
                escape = False
            elif c == "\\":
                escape = True
            elif c == '"':
                in_str = False
            i += 1
            continue
        if c == '"':
            if need_comma(prev):
                out.append(",")
            out.append(c)
            in_str = True
            escape = False
            prev = "value"  # 读到 ':' 才变 key
            i += 1
            continue
        if c == ":":
            if prev == "value":
                prev = "key"
            out.append(c)
            i += 1
            continue
        if c in ("{", "["):
            if need_comma(prev):
                out.append(",")
            out.append(c)
            prev = "open"
            i += 1
            continue
        if c in ("}", "]"):
            out.append(c)
            prev = "close"
            i += 1
            continue
        if c == ",":
            # 尾随逗号：`,` 后（跳过空白）紧跟 `}`/`]` → 丢弃（doubao 常见
            # "key": value,\n} 畸形）。只删结构逗号，字符串内不会走到这里。
            j = i + 1
            while j < n and s[j].isspace():
                j += 1
            if j < n and s[j] in ("}", "]"):
                i += 1
                continue
            out.append(c)
            prev = "comma"
            i += 1
            continue
        if c.isspace():
            out.append(c)
            i += 1
            continue
        # 字面值（数字/true/false/null）：扫描完整 token
        j = i
        while j < n and s[j] not in ",}]\"" and not s[j].isspace():
            j += 1
        if need_comma(prev):
            out.append(",")
        out.append(s[i:j])
        prev = "value"
        i = j
    return "".join(out)


def _first_json_value(raw: str) -> dict[str, Any] | None:
    """取第一个完整顶层 JSON 对象（治 Extra data：有效 JSON 后跟尾部垃圾/第二个 JSON）。

    _repair_json_text 用「首 { 到末 }」截取——尾部垃圾若含 } 会把垃圾也包进来导致仍失败；
    这里做括号深度扫描停在第一个完整顶层值的结束处，仅当它是合法对象才返回。
    """
    s = raw.strip()
    if not s:
        return None
    if s.startswith("```"):
        s = re.sub(r"^```[^\n]*\n", "", s)
        s = re.sub(r"\n```\s*$", "", s)
        s = s.strip()
    i, n = 0, len(s)
    while i < n and s[i] not in "{[":
        i += 1
    if i >= n:
        return None
    depth = 0
    in_str = False
    escape = False
    for j in range(i, n):
        c = s[j]
        if in_str:
            if escape:
                escape = False
            elif c == "\\":
                escape = True
            elif c == '"':
                in_str = False
            continue
        if c == '"':
            in_str = True
        elif c in "{[":
            depth += 1
        elif c in "}]":
            depth -= 1
            if depth == 0:
                try:
                    d = json.loads(s[i:j + 1])
                    return d if isinstance(d, dict) else None
                except Exception:
                    return None  # 第一个顶层值本身坏（缺 } 救不回），不强行截断
    return None


def _real_arcs_by_label(label: str, max_n: int = 3) -> list[dict[str, str]]:
    """从 4 张 arc_map 取该 label 的真实弧线范例（第m-n章 + description）。"""
    from .config import SETTINGS as _s
    out: list[dict[str, str]] = []
    for p in sorted(_s.data_dir.glob("arc_map_*.json")):
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        for a in data.get("arcs") or []:
            if a.get("archetype") != label:
                continue
            out.append({
                "id": str(a.get("id") or ""),
                "chapters": f"{a.get('start_chapter')}-{a.get('end_chapter')}",
                "description": str(a.get("description") or ""),
            })
            if len(out) >= max_n:
                return out
    return out


async def _select_archetypes_llm(
    query: str,
    cands: list[dict[str, Any]],
) -> tuple[list[str], dict[str, str]]:
    """LLM 从候选 label 里选 1-4 个最匹配的（打乱防锚定）。失败返回 ([], {})。"""
    pool = [dict(c) for c in cands]
    random.shuffle(pool)
    cand_lines = "\n".join(
        f"{i + 1}. {c['name']}（{c.get('parent') or ''}）：{c.get('definition') or ''}"
        for i, c in enumerate(pool)
    )
    system = (
        "你是「剧情库原型匹配专家」。根据用户给出的极简剧情，从候选剧情原型里选出"
        "最匹配的 1-4 个，作为后续章节推导的主线骨架。"
    )
    user = (
        "极简剧情：\n" + (query or "").strip() +
        "\n\n候选剧情原型（剧情库子节点/叶，label）：\n" + cand_lines +
        "\n\n请选 1-4 个最匹配的原型（编号无关，直接输出 name）。"
        "若候选里有比同类更具体、直接对应剧情手段的专用原型，优先选更具体的"
        "（如剧情是「边关追查敌国谍探渗透」，优先选「敌国谍探追查渗透」而不是宽泛的"
        "「军争外交与暗线渗透」；「主角卷进盐引争夺」优先选「盐引产业利益争夺」而不是"
        "「世家权斗」）。"
        "严格输出如下 JSON（不要 Markdown 代码块）：\n"
        '{"archetypes": [{"name": "盐引产业利益争夺", "reason": "剧情核心是盐引利益争夺"}]}'
    )
    result = await chat_json(
        system=system, user=user, call_type="derive_match", max_tokens=1500,
    )
    if result["error"] or not isinstance(result["data"], dict):
        return [], {}
    valid = {c["name"] for c in cands}
    names: list[str] = []
    reasons: dict[str, str] = {}
    for a in result["data"].get("archetypes") or []:
        if not isinstance(a, dict):
            continue
        nm = str(a.get("name") or "").strip()
        if nm in valid and nm not in names:
            names.append(nm)
            reasons[nm] = str(a.get("reason") or "").strip()
    return names[:4], reasons


async def match_archetypes(
    root_plot: str,
    style: str = "",
    role_setting: str = "",
    top_k: int = 6,
) -> list[dict[str, Any]]:
    """极简剧情 → 命中剧情库原型（embedding 召回 top-K + LLM 仲裁选 1-4 个）。

    返回 [{name, parent, definition, reason, similarity, real_arcs:[...]}]。
    剧情库为空时返回 []。
    """
    query = (root_plot or "").strip()
    if (style or "").strip():
        query += "\n风格基调：" + style.strip()
    if (role_setting or "").strip():
        query += "\n角色设定：" + role_setting.strip()
    from .config import SETTINGS as _s
    labels = _label_pool(_load_registry(_s.data_dir / "archetypes.json"))
    if not labels:
        return []
    vecs = await _label_embeddings(labels, None)
    qv = (await get_embeddings([query or "极简剧情"]))[0]
    scored: list[tuple[float, dict[str, Any]]] = []
    for i, lab in enumerate(labels):
        v = vecs[i] if i < len(vecs) else None
        if v is None:
            continue
        scored.append((cosine_similarity(qv, v), lab))
    scored.sort(key=lambda x: x[0], reverse=True)
    cands = [lab for _, lab in scored[:top_k]]
    sims = {lab["name"]: round(float(s), 4) for s, lab in scored[:top_k]}
    names, reasons = await _select_archetypes_llm(query, cands)
    if not names and cands:
        names = [cands[0]["name"]]
    out: list[dict[str, Any]] = []
    by_name = {lab["name"]: lab for lab in labels}
    for name in names:
        lab = by_name.get(name)
        if lab is None:
            continue
        out.append({
            "name": name,
            "parent": str(lab.get("parent") or ""),
            "definition": str(lab.get("definition") or ""),
            "reason": reasons.get(name, ""),
            "similarity": sims.get(name, 0.0),
            "real_arcs": _real_arcs_by_label(name),
        })
    return out


async def _tree_llm(
    system: str,
    user: str,
    call_type: str,
    max_tokens: int = 8000,
) -> dict[str, Any]:
    """通用剧情树 JSON LLM 调用：重试 4 次 + 补逗号修复 + _parse_json_safe 三连。

    v5.26 分步展开后各步 JSON 规模可控；失败 raw 落盘 harness_runs/derive_v526/bad_raw.json
    供诊断。仍失败 raise RuntimeError。
    """
    result = None
    last_err = ""  # 上次 JSON 解析错误 → 喂给下次重试（让模型自纠，而非盲采样）
    for attempt in range(6):
        _user = user
        if last_err:
            _user = user + "\n\n【上次输出 JSON 解析失败，请修正后重新输出完整 JSON】\n" + last_err
        result = await chat_json(
            system=system, user=_user,
            call_type=call_type if attempt == 0 else call_type + "_retry",
            max_tokens=max_tokens,
        )
        if result["error"] or not isinstance(result["data"], dict):
            raw = result.get("raw") or ""
            if raw:
                try:
                    # 先补缺/删尾随逗号（状态机），再走 _parse_json_safe 三连修复
                    fixed = _parse_json_safe(_repair_tree_json(raw))
                except Exception:
                    fixed = None
                if isinstance(fixed, dict):
                    result = {"data": fixed, "error": None, "raw": raw}
                    break
                # 【v6.3.1】Extra data 兜底：截取第一个完整顶层 JSON 对象再走修复
                try:
                    head = _first_json_value(raw)
                    if isinstance(head, dict):
                        result = {"data": head, "error": None, "raw": raw}
                        break
                except Exception:
                    pass
                err = str(result.get("error") or "").strip()
                if "JSON" in err:
                    last_err = err[:200]
                try:
                    from pathlib import Path
                    _bad = Path(__file__).resolve().parent.parent / "harness_runs" / "derive_v526" / "bad_raw.json"
                    _bad.parent.mkdir(parents=True, exist_ok=True)
                    _bad.write_text(raw, encoding="utf-8")
                    # 【v6.3.1】追加诊断：不覆盖，留全样本供分析
                    _bad_all = Path(__file__).resolve().parent.parent / "harness_runs" / "derive_v526" / "bad_raw_all.jsonl"
                    with _bad_all.open("a", encoding="utf-8") as _f:
                        _f.write(json.dumps({"call_type": call_type, "attempt": attempt, "raw": raw}, ensure_ascii=False) + "\n")
                except Exception:
                    pass
            continue
        break
    if result is None:
        raise RuntimeError("剧情展开失败：LLM 无响应")
    if result["error"]:
        raw_snip = (result.get("raw") or "")[:300].replace("\n", " ")
        raise RuntimeError("剧情展开失败：" + result["error"][:120] + f"（原始输出：{raw_snip}）")
    data = result["data"]
    if not isinstance(data, dict):
        raise RuntimeError("剧情展开失败：LLM 未返回合法 JSON")
    return data


def _fallback_tree(
    archetypes: list[dict[str, Any]],
    style: str,
    role_setting: str,
) -> dict[str, Any]:
    """第一步失败降级：用命中原型直接构造最小弧线树（每原型一条弧线、1 章无场景）。

    保证前端至少能显示弧线层（避免整体 422）；用户可继续逐章补场景或重新推导。
    """
    arcs: list[dict[str, Any]] = []
    for at in archetypes or []:
        name = str((at or {}).get("name") or "").strip()
        if not name:
            continue
        definition = str(at.get("definition") or name)
        parent = str(at.get("parent") or "")
        arcs.append({
            "name": f"{name}·开端",
            "archetype": name,
            "parent": parent,
            "chapters": [{
                "title": "第1章 " + name,
                "core": definition,
                "scenes": [],
            }],
        })
    if not arcs:
        arcs = [{"name": "剧情", "archetype": "", "parent": "", "chapters": []}]
    return {"root": {"style": style, "role_setting": role_setting, "arcs": arcs}}


async def _expand_arcs_llm(
    root_plot: str,
    style: str,
    role_setting: str,
    ref_block: str,
) -> dict[str, Any]:
    """第一步：弧线层 + 各章概要（不展开场景，JSON 小、稳定）。"""
    system = (
        "你是「剧情库驱动的剧情弧线拆解专家」。用户给出极简剧情，你按剧情库命中的原型"
        "组织成弧线序列（每条弧线=什么剧情、挂哪个原型、覆盖几章），先只给出弧线与各章概要。"
    )
    user = (
        "极简剧情：\n" + (root_plot or "").strip() +
        ("\n风格基调：" + style.strip() if (style or "").strip() else "") +
        ("\n角色设定：" + role_setting.strip() if (role_setting or "").strip() else "") +
        "\n\n剧情库命中的原型及真实切法参考：\n" + ref_block +
        "\n\n请输出弧线层与各章概要：\n"
        "1. 根：整体风格基调 + 角色设定；\n"
        "2. 弧线：3-6 条，按时序推进。以命中原型为核心弧线（每条 2-6 章），"
        "围绕组织过渡/铺垫/收束弧线（辅助弧线也从剧情库其余原型里选合适的挂靠，"
        "但不要造出剧情库里没有的原型名）；每条弧线带 archetype（挂的原型名，可空=未挂靠）"
        "+ parent（原型所属父节点，可空）；\n"
        "3. 每弧线章节：2-6 章，按时序推进，每章只写 title 和 core（一句话核心）。\n"
        "要求：core 必须具体可写（不是抽象概念），用大白话、具体动词写，"
        "不要套话/总结句/氛围词堆砌；输出必须是一份完整、合法的 JSON，"
        "不要省略号/截断。\n"
        "严格输出如下 JSON（不要 Markdown 代码块）：\n"
        '{"root": {"style": "…", "role_setting": "…", "arcs": ['
        '{"name": "弧线标题", "archetype": "盐引产业利益争夺", "parent": "世家权斗", '
        '"chapters": [{"title": "第1章 …", "core": "…"}]}]}}'
    )
    return await _tree_llm(system, user, "derive_expand_arcs", max_tokens=8000)


# v5.30 推导树 AI 味防线：确定性清理 + 逐章 LLM 审阅 + 脏章重生成。

_LEAD_PUNCT_RE = re.compile("^[\\s>|·\\-—,，、:：]+")
_DOUBLE_VERB_RE = re.compile("(开口|说道|问道|喃喃)\\s*\\1")


def _clean_scene_leaves(scenes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """确定性清理场景叶子的机械瑕疵（LLM 输出常见）：
    - 场景名前导符号（`>关押入病房` → `关押入病房`）；
    - 叶子前导标点；重复引动词（「开口开口」→「开口」）。
    只做字符串清洗，不改语义。
    """
    out: list[dict[str, Any]] = []
    for s in scenes or []:
        if not isinstance(s, dict):
            continue
        s = dict(s)
        name = str(s.get("name") or "").strip()
        name = _LEAD_PUNCT_RE.sub("", name).strip() or "场景"
        s["name"] = name
        for k in ("actions", "dialogues", "narration", "psychologies", "conflicts", "details"):
            v = s.get(k)
            if isinstance(v, list):
                cleaned: list[str] = []
                for item in v:
                    t = str(item).strip()
                    t = _LEAD_PUNCT_RE.sub("", t).strip()
                    t = _DOUBLE_VERB_RE.sub("\\1", t)
                    if t:
                        cleaned.append(t)
                s[k] = cleaned
        out.append(s)
    return out


def _scenes_to_text(scenes: list[dict[str, Any]]) -> str:
    """把一章的场景叶子序列化为审阅文本（带类别前缀，供 LLM 定位问题句）。"""
    lines: list[str] = []
    for s in scenes or []:
        if not isinstance(s, dict):
            continue
        lines.append("【场景】" + str(s.get("name") or ""))
        env = s.get("environment")
        if str(env or "").strip():
            lines.append("环境：" + str(env))
        for k, label in (("actions", "动作"), ("dialogues", "对白"),
                         ("psychologies", "心理"), ("conflicts", "冲突"),
                         ("details", "细节")):
            v = s.get(k)
            if isinstance(v, list):
                for item in v:
                    if str(item or "").strip():
                        lines.append(f"{label}：{str(item)}")
    return "\n".join(lines)


async def _fix_chapter_ai_flavor(
    scenes: list[dict[str, Any]],
    ch: dict[str, Any],
    idx: int,
    arc: dict[str, Any],
    style: str,
    role_setting: str,
    threshold: float = 0.70,
    retry_cap: int = 1,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """LLM 审阅单章场景叶子的 AI 味 → 脏章注入修正块重生成（≤retry 次，保最好）。

    审「措辞层」AI 味（重复引动词/三件套/套话标签/空镜头/抽象概括），不消灭
    心理/冲突叶子本身（它们在剧情素材里是合法的）。审阅失败（score=None）保守不动。

    Returns: (最终 scenes, 审阅信息 info)。
    """
    from .ai_flavor import build_ai_flavor_feedback, review_ai_flavor_standalone

    info: dict[str, Any] = {
        "reviewed": False, "score": None, "fixed": False, "retries": 0, "error": None,
    }
    text = _scenes_to_text(scenes)
    if len(text.strip()) < 60:
        return scenes, info
    try:
        res = await review_ai_flavor_standalone(text)
    except Exception as exc:  # noqa: BLE001
        info["error"] = str(exc)
        return scenes, info
    score = res.get("score")
    info["reviewed"] = True
    info["score"] = score
    if score is None or score >= threshold:
        return scenes, info  # 干净（或审阅失败，保守不动）
    findings = res.get("findings") or []
    fb = build_ai_flavor_feedback(findings)
    if not fb:
        return scenes, info
    best, best_score = scenes, score if score is not None else 0.0
    attempts = 0
    for _ in range(max(0, retry_cap)):
        attempts += 1
        try:
            new_scenes = await _expand_chapter_scenes_llm(
                ch, idx, arc, style, role_setting, extra_feedback=fb)
        except Exception:  # noqa: BLE001
            break
        if not new_scenes:
            break
        new_scenes = _clean_scene_leaves(new_scenes)
        try:
            res2 = await review_ai_flavor_standalone(_scenes_to_text(new_scenes))
        except Exception:  # noqa: BLE001
            break
        s2 = res2.get("score")
        if s2 is not None and s2 > best_score:
            best, best_score = new_scenes, s2
        if s2 is not None and s2 >= threshold:
            break
    info["fixed"] = best_score > (score if score is not None else 0.0)
    info["score"] = best_score
    info["retries"] = attempts
    return best, info


async def _expand_chapter_scenes_llm(
    ch: dict[str, Any],
    idx: int,
    arc: dict[str, Any],
    style: str,
    role_setting: str,
    extra_feedback: str | None = None,
) -> list[dict[str, Any]]:
    """把单章拆成场景与 6 类叶子（单章调用，JSON 规模小、稳定）。失败 raise（调用方降级）。

    v5.30：system 注入推导树 AI 味禁令（6 类叶子措辞层）；extra_feedback 供脏章
    重生成时把上一稿的【AI味修正块】追加进 user。
    """
    from .ai_flavor import tree_ai_flavor_ban

    from .config import SETTINGS as _s

    # 【v5.32】场景叶子密度：config.derive_scene_density（env DERIVE_SCENE_DENSITY，默认 4）
    density = max(2, int(getattr(_s, "derive_scene_density", 4)))

    system = (
        "你是「小说场景拆解专家」。给定一章概要，把它拆成按时序推进的场景，每场景给出"
        " 6 类叶子，供填充 prompt 模板。\n" + tree_ai_flavor_ban()
    )
    plan = [str(b).strip() for b in (ch.get("scene_plan") or []) if str(b).strip()]
    plan_block = ""
    if plan:
        plan_lines = "\n".join(f"{i + 1}. {b}" for i, b in enumerate(plan))
        plan_block = (
            f"\n【场景切分计划·必须严格执行】本章恰好拆成以下 {len(plan)} 个场景，"
            f"顺序与场景名严格照此展开，不得增删合并：\n{plan_lines}\n"
        )
    count_rule = (
        f"请严格按上面的场景切分计划展开 {len(plan)} 个场景（场景名照抄计划）"
        if plan else "请把本章拆成 3-5 个场景（按时序推进）"
    )
    user = (
        f"弧线：{arc.get('name', '')}（挂靠原型：{arc.get('archetype') or '未挂靠'}）\n"
        f"章节：{ch.get('title', f'第{idx + 1}章')}\n"
        f"本章核心：{ch.get('core', '')}\n"
        f"风格基调：{style.strip() if (style or '').strip() else '（未指定）'}\n"
        f"角色设定：{role_setting.strip() if (role_setting or '').strip() else '（未指定）'}\n"
        f"{plan_block}"
        f"\n{count_rule}，每场景 7 类叶子："
        "environment 环境（**单条字符串**，不是数组）/ actions 动作 / dialogues 对话 / "
        "narration 叙述原句 / psychologies 心理 / conflicts 冲突 / details 细节，"
        f"每类 {density} 条（narration 4-6 条）。"
        "叶子必须具体到能据此展开 500 字以上的正文：动作含对象与结果（谁做了什么、怎么样了）；"
        "对话带说话人（“”引号包裹）且**逐轮写清说话动词/神态**（如『轻叹一声』『压低声音』"
        "『揉了揉眼睛』『眉眼间有了笑意』——不要统一占位成『说道』『开口说』，相邻对白轮"
        "不重复同一动词）；**narration 叙述原句：写 4-6 句可直接用作正文的叙述句**（描述场景内"
        "事件经过/环境氛围/关键动作的具体措辞，带信息量，不要抽象概括，正文生成时将原样采用）；"
        "冲突写清双方与回合进展；细节含标志性物品/数字/可复用线索。不要抽象概念、套话、空镜头。\n"
        "重要：输出必须是一份完整、合法的 JSON，不要省略号（.../……）或「等」这类省略，"
        "不要漏掉任何场景/叶子，也不要截断。\n"
        "严格输出如下 JSON（不要 Markdown 代码块）：\n"
        '{"scenes": [{"name": "场景名", "environment": "…", "actions": ["…"], '
        '"dialogues": ["…"], "narration": ["…"], "psychologies": ["…"], '
        '"conflicts": ["…"], "details": ["…"]}]}'
    )
    if extra_feedback:
        user = user.rstrip() + "\n\n" + extra_feedback
    data = await _tree_llm(system, user, "derive_expand_scenes", max_tokens=8000)
    scenes = data.get("scenes")
    if not isinstance(scenes, list):
        raise RuntimeError("场景展开失败：无 scenes 数组")
    out: list[dict[str, Any]] = []
    for s in scenes:
        if not isinstance(s, dict):
            continue
        env = s.get("environment")
        # 兼容 LLM 把 environment 输出成数组：取第一条作字符串
        if isinstance(env, list):
            env = env[0] if env else ""
            s = dict(s)
            s["environment"] = env
        out.append(s)
    return _clean_scene_leaves(out)


async def _expand_arc_scenes_llm(
    arc: dict[str, Any],
    style: str,
    role_setting: str,
    extra_feedback: str | None = None,
) -> list[dict[str, Any]]:
    """第二步：把一条弧线的各章**逐章**展开成场景与 6 类叶子。

    逐章调用（单章 JSON 小，避免超长嵌套畸形）；单章失败降级该章 scenes=[]。
    【v5.30】每章展开后接 LLM AI 味审阅：脏章注入修正块重生成（config
    derive_ai_flavor_review/retry/threshold 门控）；审阅信息挂到章 dict["ai_flavor"]。
    extra_feedback：l4 完成审计不通过时的修正指令，透传到每章展开 prompt。
    """
    from .config import SETTINGS as _s

    review_on = bool(getattr(_s, "derive_ai_flavor_review", True))
    threshold = float(getattr(_s, "derive_ai_flavor_threshold", 0.70))
    retry_cap = max(0, int(getattr(_s, "derive_ai_flavor_retry", 1)))

    chaps_in = [c for c in (arc.get("chapters") or []) if isinstance(c, dict)]
    if not chaps_in:
        return []
    out: list[dict[str, Any]] = []
    density = max(2, int(getattr(_s, "derive_scene_density", 4)))
    for i, ch in enumerate(chaps_in):
        ch_feedback = extra_feedback
        scenes: list[dict[str, Any]] = []
        audit_tries = 0
        # 【完成审计 l4】场景分解后逐项自检：结构不全/叶子空/对白无引号 → 最多修正 2 轮
        # （共 3 次尝试 = blocked 三振），仍不过则如实保留，不假装通过。
        for attempt in range(3):
            audit_tries = attempt
            try:
                scenes = await _expand_chapter_scenes_llm(
                    ch, i, arc, style, role_setting, extra_feedback=ch_feedback)
            except Exception:
                scenes = []
            scenes = _clean_scene_leaves(scenes)
            from .completion_audit import audit_l4, build_audit_feedback
            aud = audit_l4(scenes, density=density)
            if aud["ok_high"]:
                break
            fb = build_audit_feedback(aud)
            if not fb or attempt >= 2:
                break
            ch_feedback = fb
        info: dict[str, Any] = {
            "reviewed": False, "score": None, "fixed": False, "retries": 0,
            "audit_tries": audit_tries,
        }
        if scenes and review_on:
            scenes, info = await _fix_chapter_ai_flavor(
                scenes, ch, i, arc, style, role_setting,
                threshold=threshold, retry_cap=retry_cap,
            )
        out.append({
            "title": ch.get("title", f"第{i + 1}章"),
            "core": ch.get("core", ""),
            "scenes": scenes,
            "ai_flavor": info,
        })
    return out


async def expand_plot_tree(
    root_plot: str,
    style: str = "",
    role_setting: str = "",
    archetypes: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """极简剧情 → 剧情树（根→弧线→章节→场景→6类叶子）。

    分步展开（v5.26）：先弧线层+章概要（小 JSON 稳定），再逐弧线展开场景叶子
    （单弧线规模小，避免超长 JSON 结构畸形）。第一步失败抛 RuntimeError；
    单弧线场景失败降级保留章概要（不整体失败）。

    archetypes：命中的剧情库原型（[{name,...}]），可来自 match_archetypes 或前端手动
    调整；为 None 时内部自动 match。
    """
    if archetypes is None:
        archetypes = await match_archetypes(root_plot, style, role_setting)
    # 补全原型元信息（definition / 真实弧线范例 / 拍示例），供注入推导 prompt
    from .config import SETTINGS as _s
    reg = _load_registry(_s.data_dir / "archetypes.json")
    meta_by_name = {a["name"]: a for a in reg}
    ref_lines: list[str] = []
    for at in archetypes:
        name = str((at or {}).get("name") or "").strip()
        if not name:
            continue
        meta = meta_by_name.get(name) or {}
        definition = str(at.get("definition") or meta.get("definition") or "").strip()
        parent = str(at.get("parent") or meta.get("parent") or "").strip()
        line = f"- {name}（{parent or '未挂靠'}）：{definition or '（无定义）'}"
        real = at.get("real_arcs") if isinstance(at.get("real_arcs"), list) else _real_arcs_by_label(name)
        if real:
            ex = real[:2]
            line += "　真实切法：" + "；".join(
                f"第{r.get('chapters')}章={r.get('description')}" for r in ex
            )
        else:
            beats = (meta.get("beats") or [])[:5]
            if beats:
                line += "　拍示例：" + "、".join(str(b) for b in beats)
        ref_lines.append(line)
    ref_block = "\n".join(ref_lines) if ref_lines else "（未命中剧情库原型，按小说常规套路自行组织弧线）"

    # 第一步：弧线层 + 章概要（6 次重试仍失败 → 降级最小弧线树，不整体 422）
    try:
        data = await _expand_arcs_llm(root_plot, style, role_setting, ref_block)
    except Exception:
        data = _fallback_tree(archetypes, style, role_setting)
    root = data.get("root")
    if not isinstance(root, dict):
        raise RuntimeError("剧情展开失败：LLM 未返回合法剧情树 JSON")
    # 兼容兜底：LLM 若返回旧结构（无 arcs 有 chapters）→ 包一层弧线，不破坏调用方
    if not isinstance(root.get("arcs"), list) or not root["arcs"]:
        if isinstance(root.get("chapters"), list):
            root["arcs"] = [{
                "name": "剧情",
                "archetype": "",
                "parent": "",
                "chapters": root["chapters"],
            }]
    arcs = root.get("arcs") or []
    # 第二步：逐弧线展开场景叶子（失败降级保留章概要，不整体失败）
    for arc in arcs:
        if not isinstance(arc, dict):
            continue
        try:
            chaps = await _expand_arc_scenes_llm(arc, style, role_setting)
        except Exception:
            chaps = []
        if chaps:
            arc["chapters"] = chaps
    return data


def _collect_chapter(ch: Any, result: defaultdict) -> None:
    """归集单章内容（title/core/scenes→叶子）到 result。兼容新树与旧树的章结构。"""
    if not isinstance(ch, dict):
        return
    title = str(ch.get("title") or "").strip()
    core = str(ch.get("core") or "").strip()
    if title or core:
        result["chapter_core"].append((title + "：" + core) if (title and core) else (title or core))
    for sc in ch.get("scenes") or []:
        if not isinstance(sc, dict):
            continue
        name = str(sc.get("name") or "").strip()
        if name:
            result["scene"].append(name)
        for node_type, field in _LEAF_FIELDS.items():
            val = sc.get(field)
            if isinstance(val, str):
                val = [val]
            if isinstance(val, list):
                for item in val:
                    item = str(item).strip()
                    if item:
                        result[node_type].append(item)


def _collect_by_node_type(tree: dict[str, Any]) -> dict[str, list[str]]:
    """把树各层内容按 node_type 归集为文本清单。

    兼容新树（根→弧线→章→场景→6 叶子）与旧树（根→章→场景→6 叶子）。
    新增归集键 arc：弧线标题（挂靠原型）。
    """
    result: dict[str, list[str]] = defaultdict(list)
    root = tree.get("root") or {}
    if not isinstance(root, dict):
        return dict(result)
    style = str(root.get("style") or "").strip()
    if style:
        result["root_style"].append(style)
    role = str(root.get("role_setting") or "").strip()
    if role:
        result["characters"].append(role)

    arcs = root.get("arcs")
    if isinstance(arcs, list) and arcs:
        for arc in arcs:
            if not isinstance(arc, dict):
                continue
            arc_name = str(arc.get("name") or "").strip()
            atype = str(arc.get("archetype") or "").strip()
            if arc_name:
                result["arc"].append(arc_name + (f"（{atype}）" if atype else ""))
            for ch in arc.get("chapters") or []:
                _collect_chapter(ch, result)
    else:
        for ch in root.get("chapters") or []:
            _collect_chapter(ch, result)
    return dict(result)


def _slot_text(node_type: str, items: list[str]) -> str | None:
    if not items:
        return None
    if node_type == "chapter_core":
        return "\n".join("· " + it for it in items)
    return "\n".join(f"{i + 1}. {it}" for i, it in enumerate(items))


# 补充段关心弧线 + 场景与叶子 6 类（root_style/characters/chapter_core 通常已有槽覆盖）
_APPENDIX_META = {
    "arc": "弧线",
    "scene": "场景",
    "environment": "环境",
    "action": "动作",
    "dialogue": "对话",
    "psychology": "心理",
    "conflict": "冲突",
    "detail": "细节",
}


def _appendix(by_type: dict[str, list[str]], consumed: set[str]) -> str | None:
    """模板槽未覆盖的树内容 -> 追加补充段，保证剧情树内容不丢失。"""
    blocks: list[str] = []
    for nt in ("arc", "scene", "environment", "action", "dialogue", "psychology", "conflict", "detail"):
        if nt in consumed:
            continue
        items = by_type.get(nt) or []
        if not items:
            continue
        text = _slot_text(nt, items) or ""
        blocks.append(f"▸ {_APPENDIX_META[nt]}：\n{text}")
    if not blocks:
        return None
    return (
        "【剧情展开补充】（供写作参考，请按此把场景/环境/动作/心理/冲突/细节写进正文，"
        "不要遗漏）\n" + "\n".join(blocks)
    )


def render_prompt(template: dict[str, Any], tree: dict[str, Any]) -> dict[str, Any]:
    """把剧情树填入模板 skeleton 的 {{key}}，返回完整 prompt + 填充/缺失槽。"""
    by_type = _collect_by_node_type(tree)
    slot_values: dict[str, str] = {}
    consumed_types: set[str] = set()
    for slot in template.get("slots") or []:
        key = str(slot.get("key") or "")
        node_type = str(slot.get("node_type") or "")
        if not key:
            continue
        text = _slot_text(node_type, by_type.get(node_type) or [])
        if text:
            slot_values[key] = text
            consumed_types.add(node_type)
    skeleton = str(template.get("skeleton") or "")
    missing: list[str] = []

    def _repl(m: "re.Match[str]") -> str:
        key = m.group(1)
        val = slot_values.get(key)
        if val:
            return val
        missing.append(key)
        return "（待补充：" + key + "）"

    prompt = PLACEHOLDER_RE.sub(_repl, skeleton)
    # 模板槽未覆盖的树内容（场景/环境/动作/心理/冲突/细节）追加补充段，避免丢失
    appendix = _appendix(by_type, consumed_types)
    if appendix:
        prompt = prompt.rstrip() + "\n\n" + appendix
    return {
        "prompt": prompt,
        "slots_filled": list(slot_values.keys()),
        "slots_missing": sorted(set(missing)),
        "appended": appendix is not None,
    }
