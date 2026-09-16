# -*- coding: utf-8 -*-
"""有界编辑原语（Phase 1）。

只提供 append / insert_after / replace / delete 四种操作，每条 edit 带逐字锚点；
protected region（``<!-- PROTECTED-BEGIN:id -->`` … ``<!-- PROTECTED-END:id -->``）
内的一切内容 step 级编辑不可触碰（slow update 走独立通道写入）。

apply_patch 永不抛异常：非法 edit（锚点缺失/多义/超长/落保护区内）逐条拒绝并在
rejected 里给出中文原因，其余 edit 继续应用——optimizer 的坏主意不拖垮整步。
"""
from __future__ import annotations

import re
from typing import Any

from .types import Edit

_BEGIN = "<!-- PROTECTED-BEGIN:"
_END = "<!-- PROTECTED-END:"
_END_RE = re.compile(r"<!-- PROTECTED-END:[^>]*-->")


def protected_spans(doc: str) -> list[tuple[int, int, str]]:
    """返回文档中所有保护区的 (start, end, region_id)，含标记本身。"""
    spans = []
    for m in re.finditer(re.escape(_BEGIN) + r"([^>]*)-->", doc):
        end_m = _END_RE.search(doc, m.end())
        if end_m:
            spans.append((m.start(), end_m.end(), m.group(1).strip()))
    return spans


def _hit_protected(pos: int, spans: list[tuple[int, int, str]]) -> str | None:
    for s, e, rid in spans:
        if s <= pos < e:
            return rid or "未命名保护区"
    return None


def _line_end_after(doc: str, pos: int) -> int:
    nl = doc.find("\n", pos)
    return len(doc) if nl == -1 else nl + 1


def apply_patch(
    doc: str,
    edits: list[Edit],
    *,
    max_edit_chars: int = 600,
) -> tuple[str, list[str], list[str]]:
    """顺序应用 edits。返回 (新文档, applied 说明, rejected 说明)。

    每个 edit 独立成败；锚点匹配基于「应用前一条之后的最新文档」，因此同一步内
    先 insert 后对同一锚点 replace 是允许的（顺序执行语义）。
    """
    doc = doc or ""
    applied: list[str] = []
    rejected: list[str] = []
    for e in edits:
        try:
            doc, ok, msg = _apply_one(doc, e, max_edit_chars)
        except Exception as exc:  # noqa: BLE001
            ok, msg = False, f"应用异常: {exc}"
        if ok:
            applied.append(msg)
        else:
            rejected.append(msg)
    return doc, applied, rejected


def _apply_one(doc: str, e: Edit, cap: int) -> tuple[str, bool, str]:
    tag = f"[{e.kind}]" + (f"@{e.anchor[:24]!r}" if e.anchor else "")
    if e.kind != "append":
        if not e.anchor:
            return doc, False, f"{tag} 拒绝：锚点为空"
        n = doc.count(e.anchor)
        if n == 0:
            return doc, False, f"{tag} 拒绝：锚点在文档中不存在"
        if n > 1:
            return doc, False, f"{tag} 拒绝：锚点出现 {n} 次（不唯一）"
    if e.kind != "delete" and not e.content.strip():
        return doc, False, f"{tag} 拒绝：content 为空"
    if len(e.content) > cap:
        return doc, False, f"{tag} 拒绝：content {len(e.content)} 字超上限 {cap}"

    spans = protected_spans(doc)
    pos_desc = {"append": "文档末尾"}.get(e.kind)

    if e.kind == "append":
        pos = len(doc)
        hit = _hit_protected(max(pos - 1, 0), spans)
        if hit:
            return doc, False, f"{tag} 拒绝：文档末尾处于保护区[{hit}]内"
        sep = "" if doc.endswith("\n") else "\n"
        return doc + sep + e.content + "\n", True, f"{tag} 已追加到文档末尾"

    start = doc.index(e.anchor)
    end = start + len(e.anchor)
    hit = _hit_protected(start, spans)
    if hit:
        return doc, False, f"{tag} 拒绝：锚点落在保护区[{hit}]内（保护区不可 step 级编辑）"

    if e.kind == "insert_after":
        pos = _line_end_after(doc, end)
        hit2 = _hit_protected(pos, spans)
        if hit2:
            return doc, False, f"{tag} 拒绝：插入点紧邻保护区[{hit2}]"
        sep = "" if doc[pos - 1] == "\n" else "\n"
        new = doc[:pos] + sep + e.content + "\n" + doc[pos:]
        return new, True, f"{tag} 已插入到锚点行后"

    if e.kind == "replace":
        new = doc[:start] + e.content + doc[end:]
        return new, True, f"{tag} 已替换锚点段"

    # delete
    new = doc[:start] + doc[end:]
    return new, True, f"{tag} 已删除锚点段"
