# -*- coding: utf-8 -*-
"""v5.25 剧情桥段审阅模块：逐弧线 LLM 审阅。

用户需求（2026-08-05）：v5.24 已完成 4 张 arc_map 表（青山 108 段 + 大奉 198 段
= 306 段），但：
1. **标签过度概括**：有些情节是本书特有的，被现有子节点标签概括得太笼统（如
   「朝堂博弈」体现不出「国舅调换军粮」这类具体剧情），对后续按原型建模板产生噪声
   → 需在子节点基础上继续细分：LLM 遍历每条弧线，找出被概括漏的具体情节，提炼更细的
   **新原型**（挂靠现有子节点/父节点）。
2. **划分不准确**：当前「第 m-n 章 = 1 情节」的切分未必准确（该拆/该并/边界偏），
   一并纳入 LLM 审阅。

产出：审阅 JSON（每弧线一条：segmentation 审阅 + 新原型建议），再由
render_arc_review_html.py 渲染成白底黑字 HTML 供用户审阅。本模块只出建议，
**不改 arc_map / archetypes.json**（用户批准后才采纳）。

复用 arc_classify 基础设施：parse_chapters / _sample_arc_text / _parse_json_safe /
_load_registry / _label_pool / _label_embed_text。
"""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Any, Callable

from .arc_classify import (
    _label_embed_text,
    _label_pool,
    _load_registry,
    _parse_json_safe,
    _sample_arc_text,
)
from .corpus_loader import _read_file_text, _resolve_corpus_dir, parse_chapters
from .embed_client import cosine_similarity, get_embeddings
from .llm_client import chat_json

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

# 每次审阅 LLM 调用重试次数
_REVIEW_RETRIES = 3
# 本弧线代表文本采样预算（首/中/尾三章均分）
_ARC_BUDGET = int(os.getenv("REVIEW_ARC_CHARS", "1500"))
# 相邻弧线头/尾采样预算（供判断边界/合并）
_NEIGHBOR_BUDGET = int(os.getenv("REVIEW_NEIGHBOR_CHARS", "300"))
# 并发审阅数
_CONCURRENCY = int(os.getenv("REVIEW_CONCURRENCY", "4"))
# 新原型候选 embedding 聚类阈值（name+definition 语义相似判同型）
_CAND_DEDUP_THRESH = float(os.getenv("REVIEW_DEDUP_THRESH", "0.90"))

_REVIEW_SYSTEM = (
    "你是「小说桥段审阅专家」。给定一部小说的一个桥段（第 m-n 章 = 一段剧情主线）"
    "及它的代表文本，你做两项审阅：\n"
    "A. 过度概括审阅：判断现有【原型标签】是否把这个桥段的【核心剧情套路】概括得不够细。\n"
    "只有当这段的核心剧情是一个【比现有标签更细、且可复用的具体剧情套路】时才提新原型，"
    "三个条件【必须同时满足】：\n"
    "   ① 它是模式化的可复用套路（如「报纸曝光构陷政敌」「盐引争夺布局」「边关军功买卖」），"
    "而不是这段独有的具体事件（如「主角在城南茶馆偷听到三皇子密谋」）——桥段都有具体剧情，"
    "独特事件不是新原型；\n"
    "   ② 它比现有【原型清单】里的标签更细、更能看出具体剧情类型：现有标签是大类（如「朝堂"
    "博弈」），新原型是其中反复出现、值得单独成套模板的具体小套路；\n"
    "   ③ 同类套路在本书或同类作品中明显会反复出现（有复用价值），不是一次性事件；\n"
    "   ④ 它明显区别于所属大类的常规内容——大类常规动作（如朝堂博弈下的普通言语交锋、站队、"
    "结盟，江湖夺宝的普通争抢）不细分，只有用了【独特手段/独特场景/独特规则】的才细分"
    "（如「报纸曝光构陷」「盐引争夺」「边关军功买卖」）。\n"
    "四个条件都满足才提 new_archetype，否则输出 null。全篇大约只有一到两成的桥段值得提，"
    "要克制；判断标准严苛一点，拿不准就不提。\n"
    "B. 划分审阅：判断这段的章范围切得是否准确：\n"
    "   - 段内确实包含【两个不同目标/冲突】的独立剧情主线（先完成一件事、再去做另一件完全不同"
    "的事）才该拆 → split；同一条主线连续推进（同一目标层层展开、事件顺序演进）不算拆分。\n"
    "   - 与相邻桥段实为同一剧情主线（该并）→ merge；\n"
    "   - 边界章偏了（实际开始/结束在别的章）→ boundary_shift；\n"
    "   - 切分合理 → ok。\n"
    "规则：\n"
    "1. 新原型必须克制：宁可少提，多数桥段应输出 null。新原型名要比现有子节点更细、能看出"
    "具体剧情套路（如「军粮贪墨案」），不要重复现有标签，也不要泛泛的「争斗」「阴谋」这类空词。\n"
    "2. 新原型名【不要包含具体人名/地名/专属机构名】，要提炼成可复用剧情套路（如「边关围点打援"
    "伏击战」而不是「崇礼关老虎背一战」）；同一本书不同桥段若有相同的新剧情模式，用同一个新原型名。\n"
    "3. 划分审阅依据真实文本判断；拿不准时选 ok，不要为了挑错而挑错。拆分点只给大致章号即可。\n"
    "4. 输出严格 JSON（不要 Markdown 代码块，不要多余文字）：\n"
    '{"arc_id":"桥段id","segmentation":"ok|split|merge|boundary_shift",'
    '"segmentation_detail":"一句话理由 ≤30 字",'
    '"split_suggestion":{"chapters":"建议拆成的新段章范围如 33-34","reason":"≤20 字"}|null,'
    '"new_archetype":{"name":"新原型名","definition":"一句定义 ≤25 字",'
    '"parent":"挂靠的现有子节点或父节点名","reason":"为什么现有标签不够 ≤25 字"}|null}'
)


def _looks_like_review(data: dict[str, Any]) -> bool:
    """审阅输出 schema 校验：必须有 segmentation 字段。"""
    return isinstance(data, dict) and "segmentation" in data


def _build_user(
    arc: dict[str, Any],
    sample: str,
    prev_tail: str,
    next_head: str,
    labels_text: str,
) -> str:
    """组装审阅 prompt 的 user 部分。"""
    seg_label = {
        "ok": "ok（切分合理）",
        "split": "split（段内该拆）",
        "merge": "merge（该与相邻段合并）",
        "boundary_shift": "boundary_shift（边界章偏了）",
    }
    lines = [
        f"【当前桥段】第 {arc['start_chapter']}-{arc['end_chapter']} 章"
        f" ｜ 原型标签：{arc['archetype']}"
        f" ｜ 概述：{arc.get('description') or '（无）'}",
        "",
        "【本桥段代表文本】（首/中/尾采样）：",
        sample[: _ARC_BUDGET] if sample else "（无文本）",
        "",
    ]
    if prev_tail:
        lines.append("【前一桥段的结尾约 300 字】（判断边界/合并用）：")
        lines.append(prev_tail)
        lines.append("")
    if next_head:
        lines.append("【后一桥段的开头约 300 字】（判断边界/合并用）：")
        lines.append(next_head)
        lines.append("")
    lines.append("【现有原型清单】（判断现有标签是否够细、新原型挂靠到哪）：")
    lines.append(labels_text)
    lines.append("")
    lines.append(
        "请按系统规则输出审阅 JSON，segmentation 只能是 "
        + "|".join(seg_label.keys())
        + " 之一，new_archetype 无则 null。"
    )
    return "\n".join(lines)


async def _llm_json(
    system: str,
    user: str,
    call_type: str,
    max_tokens: int,
    check: Callable[[dict[str, Any]], bool],
) -> dict[str, Any]:
    """通用 LLM JSON 调用（重试 + _parse_json_safe 修复层）。失败 raise。"""
    last: dict[str, Any] | None = None
    for _attempt in range(_REVIEW_RETRIES):
        result = await chat_json(system=system, user=user, call_type=call_type, max_tokens=max_tokens)
        data = result.get("data")
        if not result["error"] and check(data):
            return data
        try:
            parsed = _parse_json_safe(result.get("raw") or "")
            if check(parsed):
                return parsed
        except ValueError:
            pass
        last = result
    err = (last or {}).get("error") or "未知错误"
    raise RuntimeError(f"LLM JSON 调用失败（重试 {_REVIEW_RETRIES} 次）：{err}")


async def _review_one(
    arc: dict[str, Any],
    sample: str,
    prev_tail: str,
    next_head: str,
    labels_text: str,
) -> dict[str, Any]:
    """单弧线 LLM 审阅（带重试 + JSON 修复层）。失败兜底 ok + error 标记。"""
    user = _build_user(arc, sample, prev_tail, next_head, labels_text)
    base = {
        "arc_id": arc.get("id"),
        "chapters": f"{arc['start_chapter']}-{arc['end_chapter']}",
        "current": arc.get("archetype"),
        "description": arc.get("description") or "",
    }
    try:
        data = await _llm_json(_REVIEW_SYSTEM, user, "arc_review", 900, _looks_like_review)
    except Exception as exc:
        return {**base, "segmentation": "ok", "segmentation_detail": f"审阅解析失败（{exc}），兜底 ok", "error": str(exc)}
    data.update(base)
    return data


def _arc_edge_text(
    arc: dict[str, Any],
    text_map: dict[int, str],
    edge: str,
    budget: int,
) -> str:
    """弧线边缘文本：head=首章开头，tail=尾章结尾（供判断边界/合并）。"""
    nums = sorted(n for n in text_map if arc["start_chapter"] <= n <= arc["end_chapter"])
    if not nums:
        return ""
    if edge == "tail":
        t = text_map[nums[-1]]
        return t[-budget:] if len(t) > budget else t
    t = text_map[nums[0]]
    return t[:budget] if len(t) > budget else t


async def _review_one_guarded(
    i: int,
    arc: dict[str, Any],
    arcs: list[dict[str, Any]],
    text_map: dict[int, str],
    labels_text: str,
    sem: asyncio.Semaphore,
) -> dict[str, Any]:
    async with sem:
        sample = _sample_arc_text(arc["start_chapter"], arc["end_chapter"], text_map, _ARC_BUDGET)
        prev_tail = (
            _arc_edge_text(arcs[i - 1], text_map, "tail", _NEIGHBOR_BUDGET) if i > 0 else ""
        )
        next_head = (
            _arc_edge_text(arcs[i + 1], text_map, "head", _NEIGHBOR_BUDGET)
            if i < len(arcs) - 1
            else ""
        )
        try:
            return await _review_one(arc, sample, prev_tail, next_head, labels_text)
        except Exception as exc:  # 兜底不中断全量
            return {
                "arc_id": arc.get("id"),
                "chapters": f"{arc['start_chapter']}-{arc['end_chapter']}",
                "current": arc.get("archetype"),
                "description": arc.get("description") or "",
                "segmentation": "ok",
                "segmentation_detail": f"审阅异常（{exc}），兜底 ok",
                "error": str(exc),
            }


async def review_arc_map(
    filepath: str | Path,
    arc_map: dict[str, Any],
    registry: list[dict[str, Any]],
    progress: Callable[[str], None] | None = None,
    max_arcs: int | None = None,
) -> dict[str, Any]:
    """对一张 arc_map 的（全部或前 N 段）弧线做 LLM 审阅。

    Args:
        filepath: 相对 corpus_dir 的 txt 路径（如 玄幻武侠/青山/青山(1-500章).txt）
        arc_map: 已加载的 arc_map dict（含 arcs 列表）
        registry: 剧情库注册表（archetypes.json 的 archetypes 列表）
        progress: 进度回调
        max_arcs: 只审前 N 段（冒烟用）；None 审全部。邻接弧线边界仍用全列表。

    Returns:
        {filename, total_arcs, reviews: [逐弧线审阅], errors}
    """
    parsed = parse_chapters(filepath, None)
    if "error" in parsed:
        raise RuntimeError(f"章节解析失败：{parsed['error']}")
    chapters = parsed["chapters"]
    if not chapters:
        raise RuntimeError("章节解析为空")
    cd = _resolve_corpus_dir(None)
    full_path = (cd / filepath).resolve()
    if not full_path.is_file():
        raise RuntimeError(f"文件不存在：{filepath}")
    text = _read_file_text(full_path)
    text_map = {c["chapter_num"]: text[c["start_pos"]:c["end_pos"]].strip() for c in chapters}

    labels = _label_pool(registry)
    labels_text = "\n".join(f"- {a['name']}：{a['definition']}" for a in labels)

    full_arcs = arc_map.get("arcs") or []
    arcs = full_arcs if max_arcs is None else full_arcs[:max_arcs]
    sem = asyncio.Semaphore(_CONCURRENCY)
    reviews: list[dict[str, Any]] = []
    errors = 0
    for i in range(0, len(arcs), _CONCURRENCY):
        batch = arcs[i : i + _CONCURRENCY]
        results = await asyncio.gather(
            *[
                _review_one_guarded(i + k, a, full_arcs, text_map, labels_text, sem)
                for k, a in enumerate(batch)
            ]
        )
        for r in results:
            if r.get("error"):
                errors += 1
            reviews.append(r)
        if progress:
            done = min(i + _CONCURRENCY, len(arcs))
            progress(f"  [审阅] {done}/{len(arcs)} 段（累计异常 {errors}）")

    return {"filename": str(filepath), "total_arcs": len(arcs), "reviews": reviews, "errors": errors}


# ---------------------------------------------------------------------------
# 聚合
# ---------------------------------------------------------------------------


def _stats_of(reviews: list[dict[str, Any]]) -> dict[str, int]:
    """划分审阅分布统计。"""
    stats = {"ok": 0, "split": 0, "merge": 0, "boundary_shift": 0, "suggest_new": 0, "errors": 0}
    for r in reviews:
        seg = r.get("segmentation")
        if seg in stats:
            stats[seg] += 1
        if isinstance(r.get("new_archetype"), dict):
            stats["suggest_new"] += 1
        if r.get("error"):
            stats["errors"] += 1
    return stats


def _candidate_text(cand: dict[str, Any]) -> str:
    """新原型候选 → embedding 文本（name：definition）。"""
    return f"{cand.get('name') or ''}：{cand.get('definition') or ''}"


async def _cluster_candidates(
    candidates: list[dict[str, Any]], thresh: float
) -> list[dict[str, Any]]:
    """新原型候选 embedding 贪心聚类：语义相似（>thresh）的并成一簇。

    返回每簇：{name, definition, parent(最多来源建议), reason(首条), source_arcs,
    files}。无需严格，人工审阅兜底。
    """
    if not candidates:
        return []
    try:
        vecs = await _load_candidate_embeddings(candidates)
    except Exception as exc:  # embedding 失败 → 全部独立成簇（人工兜底）
        print(f"  [聚合] 候选 embedding 失败（{exc}），按原样展示")
        return [
            {
                "name": c["name"],
                "definition": c["definition"],
                "parent": c.get("parent") or "",
                "reason": c.get("reason") or "",
                "source_arcs": [c.get("arc_id") or ""],
                "files": [c.get("file") or ""],
            }
            for c in candidates
        ]
    clusters: list[dict[str, Any]] = []
    for i, cand in enumerate(candidates):
        v = vecs[i]
        if v is None:
            clusters.append(
                {
                    "name": cand["name"],
                    "definition": cand["definition"],
                    "parent": cand.get("parent") or "",
                    "reason": cand.get("reason") or "",
                    "source_arcs": [cand.get("arc_id") or ""],
                    "files": [cand.get("file") or ""],
                }
            )
            continue
        placed = False
        for cl in clusters:
            if cl.get("_vec") is not None and cosine_similarity(v, cl["_vec"]) > thresh:
                cl["source_arcs"].append(cand.get("arc_id") or "")
                f = cand.get("file") or ""
                if f not in cl["files"]:
                    cl["files"].append(f)
                # 代表取支持数最多/首个 name；definition 取较详细者
                if len(cand.get("name") or "") > len(cl.get("name") or ""):
                    cl["name"] = cand["name"]
                    cl["definition"] = cand["definition"]
                    cl["parent"] = cand.get("parent") or cl.get("parent") or ""
                placed = True
                break
        if not placed:
            clusters.append(
                {
                    "name": cand["name"],
                    "definition": cand["definition"],
                    "parent": cand.get("parent") or "",
                    "reason": cand.get("reason") or "",
                    "source_arcs": [cand.get("arc_id") or ""],
                    "files": [cand.get("file") or ""],
                    "_vec": v,
                }
            )
    for cl in clusters:
        cl.pop("_vec", None)
    clusters.sort(key=lambda c: len(c["source_arcs"]), reverse=True)
    return clusters


async def _load_candidate_embeddings(candidates: list[dict[str, Any]]) -> list[list[float] | None]:
    """候选 → embedding（async batch 调用，siliconflow BGE-M3，内部 10/批）。"""
    texts = [_candidate_text(c) for c in candidates]
    if not texts:
        return []
    return await get_embeddings(texts)


# LLM 归并每批候选数
_MERGE_BATCH = 30

_MERGE_SYSTEM = (
    "你是「小说剧情套路归并专家」。给定一批【新原型候选】（每项 = 从一段小说剧情提炼的"
    "可复用套路名 + 定义 + 挂靠标签），以及当前已归并的【已有簇】，请把语义相同或高度相近的"
    "候选归并。\n"
    "规则：\n"
    "1. 语义相同或高度相近（同一类剧情套路，只是措辞/细节不同，如「敌国谍探渗透接洽」"
    "「跨朝谍战追捕叛谍」「边关谍子潜伏追查」都是敌国谍探追查类）→ 归并到同一簇；真正不同的"
    "套路才分开。拿不准时倾向归并（宁合并勿细分）。\n"
    "2. 每个簇：name=代表名（从成员名里选最能概括的，≤12 字，不含人名地名），"
    "definition=一句定义（≤25 字），members=该簇归入的全部候选名（含已有簇成员 + 本批新增）。\n"
    "3. 与已有簇语义相同的候选归入已有簇（沿用其 name，不必新建）；输出簇里已有的候选成员也"
    "照列（members 全量）。\n"
    "4. 输出严格 JSON（不要 Markdown 代码块）："
    '{"clusters":[{"name":"代表名","definition":"定义","members":["候选名","候选名"]}]}'
)


def _looks_like_clusters(data: dict[str, Any]) -> bool:
    return isinstance(data, dict) and isinstance(data.get("clusters"), list)


async def _merge_candidates_llm(
    candidates: list[dict[str, Any]],
    progress: Callable[[str], None] | None = None,
) -> list[dict[str, Any]]:
    """LLM 分批归并候选 → 簇（每簇 name/definition/members=原候选名集合）。

    两轮：① 分批（30/批）把候选归入已有簇或新建簇；② 全量簇再归并一次（近义簇合并）。
    embedding 对短文本近义判不准（BGE-M3 措辞不同的近义套路余弦<0.85），LLM 归并质量高。
    """
    if progress is None:
        progress = lambda m: print(m)  # noqa: E731
    clusters: dict[str, dict[str, Any]] = {}

    def _apply(data: dict[str, Any]) -> None:
        for cl in data.get("clusters") or []:
            name = str(cl.get("name") or "").strip()
            if not name:
                continue
            members = [str(m).strip() for m in (cl.get("members") or []) if str(m).strip()]
            definition = str(cl.get("definition") or "").strip()
            if name in clusters:
                clusters[name]["members"].update(members)
                if definition and len(definition) > len(clusters[name]["definition"]):
                    clusters[name]["definition"] = definition
            else:
                clusters[name] = {"name": name, "definition": definition, "members": set(members)}

    # 第一轮：分批归并候选
    for i in range(0, len(candidates), _MERGE_BATCH):
        batch = candidates[i : i + _MERGE_BATCH]
        existing = [c for c in clusters.values()]
        existing_lines = (
            "\n".join(f"- {c['name']}：{c['definition']}（含{len(c['members'])}项）" for c in existing)
            if existing
            else "（无）"
        )
        batch_lines = "\n".join(
            f"- {c['name']}：{c['definition']}（挂靠{c.get('parent') or '?'}）" for c in batch
        )
        user = (
            "【已有簇】\n"
            + existing_lines
            + "\n\n【本批候选】\n"
            + batch_lines
            + "\n\n请归并：与已有簇语义相同的候选归入已有簇；其余新建簇。members 全量列出每个簇包含的所有候选名。"
        )
        try:
            data = await _llm_json(_MERGE_SYSTEM, user, "arc_merge_cands", 3000, _looks_like_clusters)
            _apply(data)
        except Exception as exc:
            if progress:
                progress(f"  [归并] 批次失败（{exc}），本批候选独立成簇")
            for c in batch:
                clusters.setdefault(c["name"], {"name": c["name"], "definition": c.get("definition") or "", "members": {c["name"]}})
        if progress:
            progress(f"  [归并] 第{i // _MERGE_BATCH + 1}批（{len(batch)} 候选）→ 累计簇 {len(clusters)}")

    # 第二轮：全量簇之间再归并（近义簇合并）
    if len(clusters) > 1:
        cluster_items = list(clusters.values())
        lines = "\n".join(
            f"- {c['name']}：{c['definition']}（成员：{'、'.join(sorted(c['members']))}）" for c in cluster_items
        )
        user = (
            "以下是【当前簇列表】，请把语义相同或高度相近的簇合并（如多个谍探追查簇合成一个）。\n\n"
            + lines
            + "\n\n输出归并后的最终簇（members 全量）。"
        )
        try:
            data = await _llm_json(_MERGE_SYSTEM, user, "arc_merge_clusters", 3000, _looks_like_clusters)
            merged: dict[str, dict[str, Any]] = {}
            for cl in data.get("clusters") or []:
                name = str(cl.get("name") or "").strip()
                if not name:
                    continue
                members = [str(m).strip() for m in (cl.get("members") or []) if str(m).strip()]
                merged[name] = {
                    "name": name,
                    "definition": str(cl.get("definition") or "").strip(),
                    "members": set(members),
                }
            if merged:
                clusters = merged
        except Exception as exc:
            if progress:
                progress(f"  [归并] 第二轮失败（{exc}），保留第一轮结果")

    out: list[dict[str, Any]] = []
    for cl in clusters.values():
        member_names = sorted(cl["members"])
        # 簇挂靠 parent = 成员候选挂靠中出现最多的
        parents = [cand.get("parent") for cand in candidates if cand.get("name") in member_names and cand.get("parent")]
        parent = max(set(parents), key=parents.count) if parents else ""
        out.append(
            {
                "name": cl["name"],
                "definition": cl["definition"],
                "members": member_names,
                "parent": parent,
                "source_arcs": sorted(
                    {
                        cand.get("arc_id")
                        for cand in candidates
                        if cand.get("name") in member_names and cand.get("arc_id")
                    }
                ),
                "files": sorted(
                    {
                        cand.get("file")
                        for cand in candidates
                        if cand.get("name") in member_names and cand.get("file")
                    }
                ),
            }
        )
    out.sort(key=lambda c: len(c["source_arcs"]), reverse=True)
    return out


async def aggregate_reviews(
    file_reviews: list[dict[str, Any]],
) -> dict[str, Any]:
    """跨文件聚合：每文件统计 + 全量统计 + 新原型候选 LLM 归并。"""
    all_reviews: list[dict[str, Any]] = []
    per_file: list[dict[str, Any]] = []
    candidates: list[dict[str, Any]] = []
    for fr in file_reviews:
        reviews = fr.get("reviews") or []
        all_reviews.extend(reviews)
        per_file.append(
            {
                "filename": fr.get("filename"),
                "total_arcs": fr.get("total_arcs"),
                "stats": _stats_of(reviews),
            }
        )
        for r in reviews:
            na = r.get("new_archetype")
            if isinstance(na, dict) and na.get("name"):
                candidates.append(
                    {
                        "name": na["name"],
                        "definition": na.get("definition") or "",
                        "parent": na.get("parent") or "",
                        "reason": na.get("reason") or "",
                        "arc_id": r.get("arc_id"),
                        "file": fr.get("filename"),
                    }
                )
    clusters = await _merge_candidates_llm(candidates)
    return {
        "total_arcs": len(all_reviews),
        "total_stats": _stats_of(all_reviews),
        "per_file": per_file,
        "candidate_raw_count": len(candidates),
        "candidate_archetypes": clusters,
    }
