# -*- coding: utf-8 -*-
"""整本小说剧情桥段分类（v5.24 · 剧情库驱动 + 窗口 JSON 修复）。

用户需求（2026-08-05）：LLM 遍历整本小说，把剧情每一个桥段分类——
「第 m 章到第 n 章 = 什么剧情」。最终目的：给每个「桥段原型」（监狱救人 / 朝堂争斗
这类可复用桥段）建一套统一模板，写作时按章节所在桥段的原型套对应模板。

v5.24 改动（用户拍板）：
1. **剧情库驱动**：archetypes.json（玄幻武侠根 → 6 父节点 + 25 子节点 + 6 直接叶，
   1797 拍）作为分类注册表。**父节点只起组织/给子节点分类的骨架作用，不参与弧线标注**；
   实际弧线/prompt 分类以**子节点（label=true）为主**。窗口 prompt 只注入 label 池
   （name + definition + 拍示例），LLM 优先复用子节点原型。
2. **窗口 JSON 修复**（修前 100 章分类窗口截断——非 token 上限，是 LLM 畸形输出）：
   - 窗口默认 15→8（每窗弧线少、输出小）；
   - 紧凑输出：description/gist ≤15 字，禁引号/换行/特殊字符；
   - `_parse_json_safe` 修复层：json.loads → 去非法控制符/尾部垃圾重试 → 正则 salvage
     → 仍失败才 raise；`_window_llm` 每次重试都走修复层。
3. **embedding 校验（充分利用剧情库）**：label 池原型预计算 embedding（缓存）；
   全局规整后每个弧线取章节文本首 ~400 字 embed → 余弦 vs label 池 → 弧线带
   verification（最佳匹配 label + score + confidence 高/中/低）；LLM 标注与 embedding
   最佳匹配不一致 → mismatch=true 警告（**不自动改**）。
4. **输出**：弧线 {id, start_chapter, end_chapter, archetype(子节点), parent(层级推导),
   description, verification}；arc_map 落盘同前。

入口：`classify_chapters(filepath, start_chapter, end_chapter, window_size, ...)`。
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from .config import SETTINGS
from .corpus_loader import _read_file_text, _resolve_corpus_dir, parse_chapters
from .embed_client import cosine_similarity, get_embeddings
from .llm_client import chat_json

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

# 起步原型清单（无 archetypes.json 时兜底；有剧情库时以剧情库为准）
_STARTER_ARCHETYPES = [
    ("拜师学艺", "主角拜入师门、习武/修仙入门阶段"),
    ("宗门考核", "通过宗门/门派的选拔考核"),
    ("比武招亲", "通过比试武艺招亲或争夺伴侣"),
    ("擂台比试", "公开擂台/斗场分胜负"),
    ("秘境探险", "进入秘境/禁地探险寻机缘"),
    ("寻宝夺宝", "追寻或争夺宝物/功法/丹药"),
    ("监狱救人", "闯入牢狱救出被囚之人"),
    ("追杀逃亡", "被追捕、一路逃亡避祸"),
    ("伏击围杀", "设伏或遭伏击围困厮杀"),
    ("朝堂争斗", "朝堂/官场派系权谋争斗"),
    ("家族夺权", "家族内部争权夺位"),
    ("退婚打脸", "被退婚/羞辱后打脸翻盘"),
    ("装逼打脸", "低调扮猪吃虎后扬眉吐气"),
    ("身世揭秘", "主角真实身世/来历被揭示"),
    ("认祖归宗", "找回血脉宗族、认祖归宗"),
    ("报仇雪恨", "为仇恨讨回公道、手刃仇敌"),
    ("师徒对决", "师徒反目或切磋对决"),
    ("感情告白", "男女主感情升温/表明心迹"),
    ("结盟谈判", "多方势力谈判结盟或讲条件"),
    ("背叛反水", "盟友/下属背叛或反水"),
    ("揭穿阴谋", "查明并揭穿幕后阴谋黑手"),
    ("突破奇遇", "主角获得机缘、突破境界"),
    ("决战终章", "全书/大卷末高潮决战收束"),
]

# 新原型名 vs 已有原型 的 embedding 近义阈值（name+definition 语义；并入已有不再重复登记）
_ARCH_DEDUP_THRESH = float(os.getenv("ARC_DEDUP_THRESH", "0.85"))
# 相邻桥段描述相似度阈值：高于此 → 全局规整提示 LLM 考虑合并
_MERGE_SIM_THRESH = float(os.getenv("ARC_MERGE_SIM_THRESH", "0.78"))
# 单窗最大字符数（保护上下文窗口；超限时窗口自动缩小）
_MAX_WINDOW_CHARS = int(os.getenv("ARC_MAX_WINDOW_CHARS", "110000"))
# 每章喂给 LLM 的最大字符数（超长章截断尾部并标注）
_MAX_CHAPTER_CHARS = int(os.getenv("ARC_MAX_CHAPTER_CHARS", "8000"))
# 每窗/规整 LLM 调用重试次数
_WINDOW_RETRIES = 3
# 弧线章节文本 embedding 校验：每段取多少字（跨首/中/尾三章采样拼接）
_VERIFY_CHARS = int(os.getenv("ARC_VERIFY_CHARS", "600"))
# 校验 confidence 阈值（margin = 最佳余弦 - 次佳余弦；BGE-M3 叙事 vs 抽象定义绝对分
# 天然只有 0.5-0.7，绝对分不可比 → 用 margin 反映判别力）：≥0.06 高 / ≥0.03 中 / 其余低
_CONF_HIGH_MARGIN, _CONF_MID_MARGIN = 0.06, 0.03
# 混合校验：embedding 召回 top-K（K=3）；LLM 原标签不在 top-K 才升级 LLM 仲裁
_ADJUDICATE_TOPK = int(os.getenv("ARC_ADJUDICATE_TOPK", "3"))

_ADJUDICATE_SYSTEM = (
    "你是「桥段标签仲裁专家」。给定一段小说弧线的代表文本和几个候选原型，"
    "请判断这段文本**最符合**哪个原型。\n"
    "规则：\n"
    "1. 只能从给出的【候选原型】里选一个，不要自创新名、不要综合。\n"
    "2. 以文本实际内容为准（人物在做什么、冲突是什么），候选顺序无意义，凭内容判断。\n"
    "3. reason 一句话 ≤10 个汉字，不用引号/换行。\n"
    "输出严格 JSON（不要 Markdown 代码块）：{\"label\": \"选中的原型名\", \"reason\": \"理由\"}"
)
# label 池原型 embedding 缓存文件（SETTINGS.data_dir/arc_label_embeddings.json）
_LABEL_EMBED_CACHE = "arc_label_embeddings.json"

_WINDOW_SYSTEM = (
    "你是「小说桥段原型分类专家」。你的任务是通读一部小说的章节正文，把剧情切分成一个个"
    "桥段，并给每个桥段标注「桥段原型」——一个可复用的剧情套路名。\n"
    "规则：\n"
    "1. 按剧情起承转合切桥段：一段剧情主线（人物目标+冲突+结果）从头到尾算一个桥段；"
    "剧情类型切换即断段。\n"
    "2. 每个桥段尽量包含 3 章或更多（除非某段剧情确实只占 1-2 章）。\n"
    "3. 桥段原型：**必须且只能从【原型清单】里选一个原型名**（同原型全文统一叫法，"
    "不要自创近义词）；【原型清单】里列出的都是子节点剧情类型（父节点如 朝堂权谋/后宫争宠 "
    "只是分类骨架，**禁止当作弧线原型用**）。只有当本窗出现与清单所有原型都不匹配的全新"
    "剧情套路，才登记新原型（给一个简短短语名 + 一句 ≤15 字的定义）。\n"
    "4. 紧凑输出：description（及 open_arc 的 gist）一句话 **≤15 个汉字**，不要用引号、"
    "逗号、换行、反斜杠或任何特殊符号，不要描述细节，只概括主线。\n"
    "5. 窗口边界：如果一个桥段在本窗口结束时**尚未完结**（仍在进行中），不要把它写进 "
    "arcs_closed，而是放入 open_arc（start 章 = 它实际开始的那一章，可能早于本窗起点）。\n"
    "6. 若上一窗遗留【未闭合桥段】open_arc：本窗从它接着读；若它在窗内完结，把该桥段写进 "
    "arcs_closed（start 沿用它的起始章，end = 完结章）；若仍不完结，open_arc 原样延续。\n"
    "7. 覆盖：本窗每一章的正文都必须属于某个桥段——要么在 arcs_closed 中闭合，要么由 "
    "open_arc 延续。不得漏章。"
)

_MERGE_SYSTEM = (
    "你是「小说桥段原型规整专家」。你的任务是把 LLM 分窗识别出的一部小说的原始桥段列表，"
    "规整成最终版：合并相邻同原型的小桥段、统一原型措辞、补全每段描述，确保覆盖指定章节"
    "范围无遗漏、无重叠。\n"
    "规则：\n"
    "1. 相邻两段如果原型相同（或可归为同一原型）且边界相邻，合并成一个桥段。\n"
    "2. 【相邻相似度提示】标出的相邻两段剧情高度相似，即使原型名不同也考虑是否应合并/统一。\n"
    "3. 原型措辞统一：同一剧情套路全文用一个原型名，优先用【原型清单】里的名字（子节点原型）；"
    "需要改名时通过 registry_updates 声明（old_name → new_name）。\n"
    "4. 描述补全：每一段给一句通顺的剧情概述（该段讲了什么，≤15 字）。\n"
    "5. 边界规整：第一段从覆盖范围的起点章开始，最后一段到覆盖范围的终点章结束；中间不得有"
    "遗漏章节、不得有重叠（后一段的 start 必须严格大于前一段的 end）。"
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# 原型清单（剧情库注册表）持久化
# ---------------------------------------------------------------------------


def _load_registry(path: Path | None = None) -> list[dict[str, Any]]:
    """读剧情库注册表 archetypes.json → [{name, definition, parent, label, beats}]。

    label=true = 子节点/直接叶（可作弧线原型）；label=false = 父节点（仅组织，不参与标注）。
    无文件/空清单 → 起步清单兜底。
    """
    p = path or (SETTINGS.data_dir / "archetypes.json")
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
        reg: list[dict[str, Any]] = []
        for a in raw.get("archetypes", []):
            if not isinstance(a, dict):
                continue
            name = str(a.get("name", "")).strip()
            if not name:
                continue
            reg.append({
                "name": name,
                "definition": str(a.get("definition", "")).strip(),
                "parent": str(a.get("parent", "")).strip(),
                "label": bool(a.get("label", True)),
                "beats": [str(b).strip() for b in (a.get("beats") or []) if str(b).strip()],
            })
        if reg:
            return reg
    except Exception:
        pass
    return [
        {"name": n, "definition": d, "parent": "", "label": True, "beats": []}
        for n, d in _STARTER_ARCHETYPES
    ]


def _save_registry(reg: list[dict[str, Any]], path: Path | None = None) -> None:
    p = path or (SETTINGS.data_dir / "archetypes.json")
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        json.dumps({"root": "玄幻武侠", "created_at": _now(), "archetypes": reg},
                   ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _label_pool(reg: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """label 池 = label=true 的原型（子节点 + 直接叶）；没有则退回全量。"""
    labels = [a for a in reg if a.get("label", True)]
    return labels or reg


def _registry_text(reg: list[dict[str, Any]]) -> str:
    """窗口 prompt 注入用：只列 label 池（name + definition + 前 2 拍示例）。"""
    labels = _label_pool(reg)
    if not labels:
        return "（无）"
    lines: list[str] = []
    for i, a in enumerate(labels, 1):
        beats = a.get("beats") or []
        ex = "；".join(str(b) for b in beats[:2])
        line = f"{i}. {a['name']}：{a['definition']}"
        if ex:
            line += f"（例：{ex}）"
        lines.append(line)
    return "\n".join(lines)


async def _dedup_name(
    name: str,
    definition: str,
    registry: list[dict[str, Any]],
    progress: Callable[[str], None] | None,
) -> tuple[str, bool, str]:
    """新原型名 vs 已有原型 embedding 近义查重。

    Returns: (canonical_name, is_new, definition)。is_new=False 表示并入已有原型。
    """
    if not registry:
        return name, True, definition
    texts = [f"{name}：{definition}"] + [f"{a['name']}：{a['definition']}" for a in registry]
    vecs = await get_embeddings(texts)
    v0 = vecs[0]
    if v0 is None:
        # embedding 不可用 → 退化为精确名匹配
        for a in registry:
            if a["name"] == name:
                return a["name"], False, a["definition"]
        return name, True, definition
    best_i, best_sim = -1, -1.0
    for i in range(1, len(vecs)):
        v = vecs[i]
        if v is None:
            continue
        sim = cosine_similarity(v0, v)
        if sim > best_sim:
            best_sim, best_i = sim, i
    if best_i >= 1 and best_sim >= _ARCH_DEDUP_THRESH:
        canonical = registry[best_i - 1]["name"]
        if canonical != name and progress:
            progress(f"  [原型查重] 「{name}」并入已有原型「{canonical}」（余弦 {best_sim:.3f}）")
        return canonical, False, registry[best_i - 1]["definition"]
    return name, True, definition


def _new_registry_entry(name: str, definition: str) -> dict[str, Any]:
    """新登记原型的完整结构（label=true 可作弧线标注；parent 留空待归组）。"""
    return {"name": name, "definition": definition, "parent": "", "label": True, "beats": []}


async def _register_new_archetypes(
    new_types: list[dict[str, str]],
    registry: list[dict[str, Any]],
    progress: Callable[[str], None] | None,
) -> dict[str, str]:
    """把本窗 LLM 提出的新原型写入清单（带 embedding 查重）。返回 {raw_name: canonical_name}。"""
    mapping: dict[str, str] = {}
    names = [a["name"] for a in registry]
    for nt in new_types:
        name = str(nt.get("name") or "").strip()
        if not name:
            continue
        definition = str(nt.get("definition") or "").strip()
        canonical, is_new, _def = await _dedup_name(name, definition, registry, progress)
        mapping[name] = canonical
        if is_new and canonical not in names:
            registry.append(_new_registry_entry(canonical, _def or definition or "（待补充）"))
            names.append(canonical)
            if progress:
                progress(f"  [新原型] 登记「{canonical}」")
    return mapping


async def _normalize_archetype(
    name: str,
    registry: list[dict[str, Any]],
    mapping: dict[str, str],
    progress: Callable[[str], None] | None,
) -> str | None:
    """把弧线标注的原型名归一化到清单里的正式名（查重并入 / 自动登记）。"""
    name = (name or "").strip()
    if not name:
        return None
    if name in mapping:
        name = mapping[name]
    existing = [a["name"] for a in registry]
    if name in existing:
        # 命中的若是父节点（label=false），允许使用但后续 parent 推导为空——报告会提示
        return name
    # 清单里没有 → 查重；近义并入已有，否则登记新原型
    canonical, is_new, _def = await _dedup_name(name, "", registry, progress)
    if is_new:
        registry.append(_new_registry_entry(canonical, _def or "（待补充）"))
        if progress:
            progress(f"  [新原型] 登记「{canonical}」（弧线自动带出）")
    return canonical


# ---------------------------------------------------------------------------
# 窗口 JSON 修复层
# ---------------------------------------------------------------------------


def _repair_json_text(s: str) -> str:
    """修复：去掉非法控制字符；截取第一个 { 到最后一个 } 之间的内容（丢尾部垃圾）。"""
    s = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", s)
    start = s.find("{")
    end = s.rfind("}")
    if start != -1 and end > start:
        s = s[start:end + 1]
    return s


def _salvage_json(s: str) -> dict[str, Any] | None:
    """正则 salvage：从坏 JSON 里尽量救出 start/end/archetype 三元组（保底不空跑）。"""
    starts = [int(x) for x in re.findall(r'"start"\s*:\s*(\d+)', s)]
    ends = [int(x) for x in re.findall(r'"end"\s*:\s*(\d+)', s)]
    names = re.findall(r'"archetype"\s*:\s*"([^"]{1,40})"', s)
    if not starts or not names:
        return None
    arcs: list[dict[str, Any]] = []
    for i in range(max(len(starts), len(names))):
        st = starts[i] if i < len(starts) else None
        en = ends[i] if i < len(ends) else None
        nm = names[i] if i < len(names) else ""
        if st is None or not nm:
            continue
        arcs.append({"start": st, "end": en if en is not None else st, "archetype": nm})
    if arcs:
        return {"arcs_closed": arcs, "open_arc": None, "new_archetypes": []}
    return None


def _parse_json_safe(raw: str) -> dict[str, Any]:
    """解析窗口 LLM 的 JSON 输出，带修复层（三步：直接 → 修复 → salvage）。"""
    if not raw:
        raise ValueError("空输出")
    s = raw.strip()
    if s.startswith("```"):
        s = re.sub(r"^```[^\n]*\n", "", s)
        s = re.sub(r"\n```\s*$", "", s)
        s = s.strip()
    try:
        return json.loads(s)
    except Exception:
        pass
    fixed = _repair_json_text(s)
    try:
        return json.loads(fixed)
    except Exception:
        pass
    # salvage 在原始文本和修复后文本上都试（修复会截断掉尾部的 start/end 对，原始文本信息更全）
    for candidate in (s, fixed):
        salvaged = _salvage_json(candidate)
        if salvaged is not None:
            return salvaged
    raise ValueError(f"JSON 解析失败；raw 前缀={str(s)[:200]}")


def _looks_like_arc_payload(data: dict[str, Any]) -> bool:
    """窗口 schema（arcs_closed/open_arc/new_archetypes）或 规整 schema（arcs/registry_updates）。"""
    return any(k in data for k in ("arcs_closed", "open_arc", "new_archetypes", "arcs", "registry_updates"))


async def _window_llm(system: str, user: str, call_type: str, max_tokens: int = 4000) -> dict[str, Any]:
    last: dict[str, Any] | None = None
    for _attempt in range(_WINDOW_RETRIES):
        result = await chat_json(system=system, user=user, call_type=call_type, max_tokens=max_tokens)
        data = result.get("data")
        if not result["error"] and isinstance(data, dict) and _looks_like_arc_payload(data):
            return data
        # chat_json 报 JSON 错误 → raw 里可能有可救回的 JSON，走修复层
        try:
            parsed = _parse_json_safe(result.get("raw") or "")
            if _looks_like_arc_payload(parsed):
                return parsed
        except ValueError:
            pass
        last = result
    err = (last or {}).get("error") or "未知错误"
    raw = (last or {}).get("raw") or ""
    raise RuntimeError(f"LLM 调用失败（重试 {_WINDOW_RETRIES} 次）：{err}；raw 前缀={str(raw)[:200]}")


# ---------------------------------------------------------------------------
# 分窗直接通读
# ---------------------------------------------------------------------------


def _parse_window_output(data: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, Any] | None, list[dict[str, str]]]:
    """解析窗口 LLM 输出 → (arcs_closed, open_arc, new_archetypes)。"""
    closed: list[dict[str, Any]] = []
    for a in data.get("arcs_closed") or []:
        if not isinstance(a, dict):
            continue
        try:
            s = int(a.get("start"))
            e = int(a.get("end"))
        except (TypeError, ValueError):
            continue
        name = str(a.get("archetype") or "").strip()
        if not name or s > e:
            continue
        closed.append({
            "start_chapter": s,
            "end_chapter": e,
            "archetype": name,
            "description": str(a.get("description") or "").strip(),
        })

    oa: dict[str, Any] | None = None
    o = data.get("open_arc")
    if isinstance(o, dict):
        name = str(o.get("archetype") or "").strip()
        if name:
            try:
                s = int(o.get("start"))
            except (TypeError, ValueError):
                s = None
            cont_raw = o.get("continues", True)
            continues = not str(cont_raw).strip().lower() in ("false", "0", "no", "none", "null")
            oa = {
                "archetype": name,
                "start_chapter": s,
                "gist": str(o.get("gist") or "").strip(),
                "continues": continues,
            }

    new_types: list[dict[str, str]] = []
    for nt in data.get("new_archetypes") or []:
        if not isinstance(nt, dict):
            continue
        nm = str(nt.get("name") or "").strip()
        if nm:
            new_types.append({"name": nm, "definition": str(nt.get("definition") or "").strip()})
    return closed, oa, new_types


async def _process_windows(
    chapter_texts: list[dict[str, Any]],
    start_chapter: int,
    end_chapter: int,
    window_size: int,
    registry: list[dict[str, Any]],
    progress: Callable[[str], None] | None,
) -> list[dict[str, Any]]:
    """贪心组窗（window_size 章 或 max_window_chars 字符，取先到者），逐窗直接通读。"""
    arcs: list[dict[str, Any]] = []
    open_arc: dict[str, Any] | None = None
    idx = 0
    n = len(chapter_texts)
    win_no = 0
    while idx < n:
        # 组窗
        batch: list[dict[str, Any]] = []
        total_chars = 0
        while idx < n and len(batch) < window_size:
            ch = chapter_texts[idx]
            text = ch["text"]
            if len(text) > _MAX_CHAPTER_CHARS:
                text = text[:_MAX_CHAPTER_CHARS] + "\n【…本章过长，已截断…】"
            if batch and total_chars + len(text) > _MAX_WINDOW_CHARS:
                break
            batch.append({"chapter_num": ch["chapter_num"], "title": ch["title"], "text": text})
            total_chars += len(text)
            idx += 1
        win_no += 1
        win_start = batch[0]["chapter_num"]
        win_end = batch[-1]["chapter_num"]
        if progress:
            progress(
                f"[窗 {win_no}] 第 {win_start}-{win_end} 章（{len(batch)} 章 / {total_chars} 字），"
                f"通读识别桥段…"
            )

        # 组装 user prompt
        open_arc_block = ""
        if open_arc:
            start_s = f"自第 {open_arc['start_chapter']} 章起" if open_arc.get("start_chapter") else "承接上文"
            open_arc_block = (
                "【未闭合桥段】\n"
                f"- 原型「{open_arc['archetype']}」，{start_s}，梗概：{open_arc.get('gist') or '（无）'}\n"
                "本窗从该桥段继续读起，若完结请在 arcs_closed 中闭合（start 沿用其起始章）。\n\n"
            )
        chapters_block = "\n\n".join(
            f"第{c['chapter_num']}章 {c['title']}\n{c['text']}" for c in batch
        )
        user = (
            "【原型清单】（只能从中选弧线原型，父节点名禁用）\n"
            + _registry_text(registry)
            + "\n\n"
            + open_arc_block
            + f"【本窗章节正文】（第 {win_start} 章到第 {win_end} 章）\n"
            + chapters_block
            + "\n\n请输出严格 JSON（不要 Markdown 代码块、不要任何说明文字）。description 和 gist "
            "都一句话 ≤15 个汉字，不要用引号、逗号、换行或特殊符号。格式如下：\n"
            '{"arcs_closed": [{"start": 1, "end": 3, "archetype": "拜师学艺", '
            '"description": "拜入师门习武"}], '
            '"open_arc": {"archetype": "追杀逃亡", "start": 5, "gist": "遭追捕逃入深山", "continues": true}, '
            '"new_archetypes": [{"name": "秘境探险", "definition": "入秘境寻机缘"}]}\n'
            "注意：没有闭合桥段时 arcs_closed 给空数组；没有跨窗桥段时 open_arc 给 null；"
            "没有新原型时 new_archetypes 给空数组。"
        )

        data = await _window_llm(_WINDOW_SYSTEM, user, call_type="arc_window", max_tokens=4000)
        mapping = await _register_new_archetypes(data.get("new_archetypes") or [], registry, progress)
        closed, oa, _ = _parse_window_output(data)

        for a in closed:
            name = await _normalize_archetype(a["archetype"], registry, mapping, progress)
            if not name:
                continue
            arcs.append({"start_chapter": a["start_chapter"], "end_chapter": a["end_chapter"],
                         "archetype": name, "description": a["description"]})

        if oa:
            name = await _normalize_archetype(oa["archetype"], registry, mapping, progress)
            if not name:
                open_arc = None
            elif not oa["continues"]:
                # 本窗结束即闭合
                arcs.append({"start_chapter": oa["start_chapter"] or win_start,
                             "end_chapter": win_end,
                             "archetype": name, "description": oa["gist"] or "（跨窗完结，概述缺失）"})
                open_arc = None
            else:
                oa["archetype"] = name
                open_arc = oa
        else:
            open_arc = None

        if progress:
            tail = f"，未闭合桥段「{open_arc['archetype']}」延续" if open_arc else "，无未闭合桥段"
            progress(f"  ↳ 累计闭合 {len(arcs)} 段" + tail)

    # 最后一窗仍开放的桥段 → 闭合到 end_chapter
    if open_arc:
        if progress:
            progress(f"  [收尾] 最后一窗仍开放的桥段「{open_arc['archetype']}」闭合到第 {end_chapter} 章")
        arcs.append({"start_chapter": open_arc.get("start_chapter") or start_chapter,
                     "end_chapter": end_chapter,
                     "archetype": open_arc["archetype"],
                     "description": open_arc.get("gist") or "（跨窗延续，概述缺失）"})
    return arcs


# ---------------------------------------------------------------------------
# 全局规整（embedding 辅助合并）
# ---------------------------------------------------------------------------


async def _merge_arcs(
    arcs: list[dict[str, Any]],
    registry: list[dict[str, Any]],
    start_chapter: int,
    end_chapter: int,
    progress: Callable[[str], None] | None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """全局规整 1 次：embedding 相似度提示 + LLM 合并/统一/补描述。"""
    if not arcs:
        return [], registry

    hints: list[str] = []
    try:
        texts = [f"{a['archetype']}：{a.get('description') or ''}" for a in arcs]
        vecs = await get_embeddings(texts)
        for i in range(len(arcs) - 1):
            if vecs[i] and vecs[i + 1]:
                sim = cosine_similarity(vecs[i], vecs[i + 1])
                if sim >= _MERGE_SIM_THRESH:
                    a, b = arcs[i], arcs[i + 1]
                    hints.append(
                        f"第 {i + 1} 段（第 {a['start_chapter']}-{a['end_chapter']} 章）与第 {i + 2} 段"
                        f"（第 {b['start_chapter']}-{b['end_chapter']} 章）描述余弦相似 {sim:.2f}，"
                        f"可能同原型，请考虑合并。"
                    )
    except Exception as exc:
        if progress:
            progress(f"  [提示] 相邻相似度计算失败（{exc}），跳过 embedding 提示")

    hints_block = "\n".join(hints) if hints else "（无）"
    arcs_block = "\n".join(
        f"{i + 1}. 第 {a['start_chapter']}-{a['end_chapter']} 章 | {a['archetype']} | {a.get('description') or ''}"
        for i, a in enumerate(arcs)
    )
    user = (
        "【覆盖范围】第 "
        + str(start_chapter)
        + " 章到第 "
        + str(end_chapter)
        + " 章\n"
        "【原型清单】\n" + _registry_text(registry) + "\n\n"
        "【相邻相似度提示】\n" + hints_block + "\n\n"
        "【原始桥段列表】\n" + arcs_block + "\n\n"
        "请输出严格 JSON（不要 Markdown 代码块），格式：\n"
        '{"arcs": [{"start": 1, "end": 3, "archetype": "拜师学艺", "description": "拜入师门习武"}], '
        '"registry_updates": [{"old_name": "救人", "new_name": "监狱救人"}]}\n'
        "要求：arcs 覆盖第 "
        + str(start_chapter)
        + " 到第 "
        + str(end_chapter)
        + " 章全部章节，无遗漏、无重叠，后一段 start 严格大于前一段 end；description ≤15 字；"
        "registry_updates 没有改名时给空数组。"
    )
    if progress:
        progress("[规整] 全局规整原始桥段（合并/统一/补描述）…")
    result = await _window_llm(_MERGE_SYSTEM, user, call_type="arc_merge", max_tokens=8000)

    merged: list[dict[str, Any]] = []
    for a in result.get("arcs") or []:
        if not isinstance(a, dict):
            continue
        try:
            s = int(a.get("start"))
            e = int(a.get("end"))
        except (TypeError, ValueError):
            continue
        name = str(a.get("archetype") or "").strip()
        if not name or s > e:
            continue
        merged.append({"start_chapter": s, "end_chapter": e, "archetype": name,
                       "description": str(a.get("description") or "").strip()})

    # 应用 registry_updates（统一原型措辞）
    registry = list(registry)
    for u in result.get("registry_updates") or []:
        if not isinstance(u, dict):
            continue
        old = str(u.get("old_name") or "").strip()
        new = str(u.get("new_name") or "").strip()
        if not old or not new or old == new:
            continue
        for a in merged:
            if a["archetype"] == old:
                a["archetype"] = new
        renamed = False
        for r in registry:
            if r["name"] == old:
                r["name"] = new
                renamed = True
        if new not in [r["name"] for r in registry]:
            registry.append(_new_registry_entry(new, "（待补充）"))
        if renamed and progress:
            progress(f"  [统一] 原型「{old}」→「{new}」")
    return merged, registry


def _repair_and_merge_arcs(arcs: list[dict[str, Any]], start_chapter: int, end_chapter: int) -> list[dict[str, Any]]:
    """程序化规整：钳制边界→排序→修漏缝/修重叠→合并相邻同原型连续段→丢空段。"""
    cleaned: list[dict[str, Any]] = []
    for a in arcs:
        s = max(int(a["start_chapter"]), start_chapter)
        e = min(int(a["end_chapter"]), end_chapter)
        if s <= e:
            cleaned.append({"start_chapter": s, "end_chapter": e,
                            "archetype": a["archetype"], "description": a.get("description") or ""})
    cleaned.sort(key=lambda a: (a["start_chapter"], a["end_chapter"]))
    if not cleaned:
        return []

    out: list[dict[str, Any]] = [cleaned[0]]
    for a in cleaned[1:]:
        prev = out[-1]
        if a["start_chapter"] <= prev["end_chapter"]:
            # 重叠：把当前段起点推到前一段之后
            a["start_chapter"] = prev["end_chapter"] + 1
            if a["start_chapter"] > a["end_chapter"]:
                continue  # 完全被覆盖，丢弃
        elif a["start_chapter"] > prev["end_chapter"] + 1:
            # 漏缝：把前一段延长填缝
            prev["end_chapter"] = a["start_chapter"] - 1
            if not prev.get("description"):
                prev["description"] = "（边界补缝，概述缺失）"
        out.append(a)
    out[0]["start_chapter"] = start_chapter
    out[-1]["end_chapter"] = end_chapter

    # 合并相邻同原型连续段（前一步已保证连续）
    merged_out: list[dict[str, Any]] = []
    for a in out:
        if (merged_out and merged_out[-1]["archetype"] == a["archetype"]
                and merged_out[-1]["end_chapter"] + 1 == a["start_chapter"]):
            prev = merged_out[-1]
            prev["end_chapter"] = a["end_chapter"]
            parts = [p for p in (prev.get("description"), a.get("description")) if p]
            prev["description"] = "；".join(parts)
        else:
            merged_out.append(dict(a))
    return merged_out


# ---------------------------------------------------------------------------
# embedding 校验（充分利用剧情库：弧线章节 → label 池）
# ---------------------------------------------------------------------------


def _label_embed_text(a: dict[str, Any]) -> str:
    """label 原型 → embedding 文本（name：definition；示例前 8 拍）。"""
    beats = (a.get("beats") or [])[:8]
    tail = "；".join(str(b) for b in beats)
    text = f"{a['name']}：{a['definition']}"
    if tail:
        text += "；" + tail
    return text


async def _label_embeddings(
    labels: list[dict[str, Any]],
    progress: Callable[[str], None] | None,
) -> list[list[float] | None]:
    """label 池原型 embedding，带缓存（name→vector 按顺序对齐）。"""
    texts = [_label_embed_text(a) for a in labels]
    cache_path = SETTINGS.data_dir / _LABEL_EMBED_CACHE
    key = hashlib.md5("|".join(texts).encode("utf-8")).hexdigest()
    try:
        cache = json.loads(cache_path.read_text(encoding="utf-8"))
        if cache.get("key") == key:
            vecs = cache.get("vectors")
            if isinstance(vecs, list) and len(vecs) == len(texts):
                return vecs
    except Exception:
        pass
    if progress:
        progress(f"[校验] label 池 {len(texts)} 个原型 embedding（缓存未命中，重算）…")
    vecs = await get_embeddings(texts)
    try:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(
            json.dumps({"key": key, "vectors": vecs}, ensure_ascii=False), encoding="utf-8"
        )
    except Exception:
        pass
    return vecs


def _confidence(margin: float) -> str:
    return "高" if margin >= _CONF_HIGH_MARGIN else ("中" if margin >= _CONF_MID_MARGIN else "低")


async def _adjudicate_arc(
    sample: str,
    llm_label: str,
    top3: list[str],
    labels: list[dict[str, Any]],
) -> str:
    """LLM 仲裁：弧线文本 vs LLM 原标签 + embedding top-3 候选，强制选一。

    Returns: 仲裁选中的原型名（失败/解析失败 → 回退 llm_label）。
    """
    cands = [llm_label] + [n for n in top3 if n != llm_label]
    # 候选顺序打乱（按标签名哈希偏移旋转），且不标明哪个是初标 → 消除锚定偏置
    offset = sum(ord(c) for c in llm_label) % len(cands)
    cands = cands[offset:] + cands[:offset]
    name2a = {a["name"]: a for a in labels if a["name"] in cands}
    # 候选块：名+定义+前 2 拍
    blocks = []
    for i, name in enumerate(cands, 1):
        a = name2a.get(name, {"definition": "", "beats": []})
        beats = (a.get("beats") or [])[:2]
        line = f"{i}. {name}：{a.get('definition') or ''}"
        if beats:
            line += "（例：" + "；".join(str(b) for b in beats) + "）"
        blocks.append(line)
    user = (
        "【候选原型】\n" + "\n".join(blocks) + "\n\n"
        "【弧线代表文本】\n" + (sample[:800] if sample else "（无文本）") + "\n\n"
        "请判断这段文本最符合哪个候选原型，输出严格 JSON："
        '{"label": "选中的原型名（必须与候选完全一致）", "reason": "≤10字理由"}'
    )
    try:
        result = await _window_llm(_ADJUDICATE_SYSTEM, user, call_type="arc_adjudicate", max_tokens=300)
        picked = str(result.get("label") or "").strip()
        if picked in cands:
            return picked
        # 容忍带序号前缀（如 "1. 牢狱" / "1、牢狱"）
        for i, name in enumerate(cands, 1):
            if str(i) == picked.strip() or picked.startswith(str(i)):
                return name
    except Exception:
        pass
    return llm_label


def _sample_arc_text(
    start_chapter: int, end_chapter: int, text_map: dict[int, str], budget: int
) -> str:
    """弧线代表文本：跨弧线取首/中/尾三章开头片段拼接（单章则取该章开头）。

    为什么：单章首 400 字可能是场景铺陈/对白，不代表弧线核心剧情；
    多章采样覆盖弧线起承转合，embedding 特征更代表弧线内容。
    """
    nums = sorted(n for n in text_map if start_chapter <= n <= end_chapter)
    if not nums:
        return ""
    if len(nums) == 1:
        return text_map[nums[0]][:budget]
    mid = nums[len(nums) // 2]
    picks = [nums[0], mid, nums[-1]]
    per = max(budget // len(picks), 100)
    return "".join(text_map[n][:per] for n in picks)


async def _verify_arcs(
    arcs: list[dict[str, Any]],
    chapter_texts: list[dict[str, Any]],
    registry: list[dict[str, Any]],
    progress: Callable[[str], None] | None,
) -> None:
    """混合校验（embedding 召回 + LLM 仲裁）：文字模型与 embedding 同时工作。

    逐弧线：
    ① embedding 召回：弧线多章采样文本 → 余弦 vs label 池 → top-K 候选。
    ② LLM 原标签 ∈ top-K → 两模型一致，method=embed，零额外 LLM 调用。
       confidence 由 margin 定（embedding 判别力）；mismatch=false。
    ③ LLM 原标签 ∉ top-K → 两模型分歧 → 升级 LLM 仲裁（_adjudicate_arc，读真实文本
       在候选里强制选一）。仲裁选原标签 → 原标签可信（embedding 被误导），mismatch=false；
       仲裁选某候选 → mismatch=true，附 suggested_label。
    不自动改 LLM 标注（保持计划「只警告不改」），suggested_label 供用户采纳。
    """
    labels = _label_pool(registry)
    if not labels:
        return
    try:
        label_vecs = await _label_embeddings(labels, progress)
    except Exception as exc:
        if progress:
            progress(f"  [校验] label 池 embedding 失败（{exc}），跳过校验")
        return
    text_map = {c["chapter_num"]: c["text"] for c in chapter_texts}
    verified = 0
    adjudicated_n = 0
    for a in arcs:
        sample = _sample_arc_text(a["start_chapter"], a["end_chapter"], text_map, _VERIFY_CHARS)
        if not sample:
            sample = a["archetype"]
        try:
            vec = (await get_embeddings([sample]))[0]
        except Exception:
            vec = None
        if vec is None:
            continue
        sims: list[tuple[float, str]] = []
        for i, lv in enumerate(label_vecs):
            if lv is None:
                continue
            sims.append((cosine_similarity(vec, lv), labels[i]["name"]))
        if not sims:
            continue
        sims.sort(reverse=True, key=lambda x: x[0])
        best_sim, best_label = sims[0]
        margin = best_sim - (sims[1][0] if len(sims) > 1 else 0.0)
        top3 = [name for _, name in sims[:_ADJUDICATE_TOPK]]
        llm_label = a["archetype"]

        ver: dict[str, Any] = {
            "method": "embed",
            "best": best_label,
            "top3": top3,
            "embed_score": round(best_sim, 3),
            "margin": round(margin, 3),
            "adjudicated": False,
            "mismatch": False,
        }
        if llm_label in top3:
            # 两模型一致：LLM 标签被 embedding 召回覆盖
            ver["final"] = llm_label
            ver["confidence"] = _confidence(margin)
        else:
            # 分歧 → LLM 仲裁（文字模型读真实文本强制选一）
            adjudicated_n += 1
            picked = await _adjudicate_arc(sample, llm_label, top3, labels)
            ver["adjudicated"] = True
            ver["method"] = "hybrid"
            if picked == llm_label:
                ver["final"] = llm_label
                ver["confidence"] = "高"
            else:
                ver["mismatch"] = True
                ver["final"] = llm_label
                ver["suggested"] = picked
                ver["confidence"] = "高"
        a["verification"] = ver
        verified += 1
    if progress:
        mism = sum(1 for a in arcs if a.get("verification", {}).get("mismatch"))
        low = sum(1 for a in arcs if a.get("verification", {}).get("confidence") == "低")
        progress(
            f"  [校验] {verified}/{len(arcs)} 段，仲裁 {adjudicated_n}，mismatch {mism}，低置信 {low}"
        )


# ---------------------------------------------------------------------------
# 主入口
# ---------------------------------------------------------------------------


async def classify_chapters(
    filepath: str | Path,
    start_chapter: int = 1,
    end_chapter: int | None = None,
    window_size: int = 8,
    corpus_dir: Path | None = None,
    progress: Callable[[str], None] | None = None,
    registry_path: Path | None = None,
    arc_map_path: Path | None = None,
) -> dict[str, Any]:
    """对整本小说（或指定范围）做剧情桥段分类。

    Args:
        filepath: 相对 corpus_dir 的文件路径（如 玄幻武侠/青山/青山(1-500章).txt）
        start_chapter / end_chapter: 分类的章号范围（1-based，默认全书）
        window_size: 每窗直接通读多少章（默认 8；v5.24 为输出小而缩小，避免窗口 JSON 截断）
        corpus_dir: 语料目录（默认自动解析）
        progress: 进度回调 progress(msg)
        registry_path: 原型清单文件路径（默认 SETTINGS.data_dir/archetypes.json，剧情库）
        arc_map_path: 桥段表输出路径（默认 SETTINGS.data_dir/arc_map_<novel>.json）

    Returns:
        {filename, total_chapters, classified_range, arcs, archetype_registry,
         arc_map_path, registry_path}
    """
    parsed = parse_chapters(filepath, corpus_dir)
    if "error" in parsed:
        raise RuntimeError(f"章节解析失败：{parsed['error']}")
    chapters = parsed["chapters"]
    total = parsed["total_chapters"]
    if not chapters:
        raise RuntimeError("章节解析为空")
    # 用实际章号边界 clamp（章号可能非连续：如 青山(501-815章).txt 内章号 474-772，
    # 若按 total_chapters=299 clamp 会把 end 压到 299，selected 过滤 474-772 全空 → 0 段）
    min_ch = min(c["chapter_num"] for c in chapters)
    max_ch = max(c["chapter_num"] for c in chapters)
    end_chapter = int(end_chapter) if end_chapter else max_ch
    start_chapter = max(min_ch, min(int(start_chapter), max_ch))
    end_chapter = max(start_chapter, min(end_chapter, max_ch))
    if progress:
        progress(f"[语料] {filepath}，共 {total} 章（第 {min_ch}-{max_ch} 章）；本次分类第 {start_chapter}-{end_chapter} 章")

    # 一次读取全文，按章切片（避免 extract_chapter 每章重读整个大文件）
    cd = _resolve_corpus_dir(corpus_dir)
    full_path = (cd / filepath).resolve()
    try:
        full_path.relative_to(cd.resolve())
    except ValueError:
        raise RuntimeError(f"路径越界：{filepath}")
    if not full_path.is_file():
        raise RuntimeError(f"文件不存在：{filepath}")
    text = _read_file_text(full_path)

    selected = [c for c in chapters if start_chapter <= c["chapter_num"] <= end_chapter]
    chapter_texts = [
        {"chapter_num": c["chapter_num"], "title": c["title"], "text": text[c["start_pos"]:c["end_pos"]].strip()}
        for c in selected
    ]

    registry = _load_registry(registry_path)
    labels = _label_pool(registry)
    parents = [a for a in registry if not a.get("label", True)]
    if progress:
        progress(
            f"[原型] 剧情库注册表 {len(registry)} 个"
            f"（label 池 {len(labels)} 个子节点/直接叶 + {len(parents)} 个父节点）"
        )

    arcs = await _process_windows(chapter_texts, start_chapter, end_chapter, window_size, registry, progress)
    if progress:
        progress(f"[分段] 分窗识别完成，原始桥段 {len(arcs)} 段")

    merged, registry = await _merge_arcs(arcs, registry, start_chapter, end_chapter, progress)
    if not merged:
        merged = arcs
    final_arcs = _repair_and_merge_arcs(merged, start_chapter, end_chapter)
    if progress:
        progress(f"[规整] 全局规整完成，最终桥段 {len(final_arcs)} 段")

    # embedding 校验（充分利用剧情库）
    await _verify_arcs(final_arcs, chapter_texts, registry, progress)

    _save_registry(registry, registry_path)

    parent_of = {a["name"]: (a.get("parent") or "") for a in registry}
    novel = Path(filepath).stem
    out_path = arc_map_path or (SETTINGS.data_dir / f"arc_map_{novel}.json")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    arcs_out = [
        {
            "id": f"arc_{i}",
            "start_chapter": a["start_chapter"],
            "end_chapter": a["end_chapter"],
            "archetype": a["archetype"],
            "parent": parent_of.get(a["archetype"], ""),
            "description": a.get("description") or "",
            "verification": a.get("verification"),
        }
        for i, a in enumerate(final_arcs, 1)
    ]
    payload = {
        "filename": str(filepath),
        "total_chapters": total,
        "classified_range": {"start_chapter": start_chapter, "end_chapter": end_chapter},
        "created_at": _now(),
        "archetype_registry": registry,
        "arcs": arcs_out,
    }
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    if progress:
        progress(f"[保存] 桥段表 → {out_path}")

    return {
        "filename": str(filepath),
        "total_chapters": total,
        "classified_range": {"start_chapter": start_chapter, "end_chapter": end_chapter},
        "arcs": arcs_out,
        "archetype_registry": registry,
        "arc_map_path": str(out_path),
        "registry_path": str(registry_path or (SETTINGS.data_dir / "archetypes.json")),
    }
