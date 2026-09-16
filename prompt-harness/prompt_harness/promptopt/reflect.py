# -*- coding: utf-8 -*-
"""Reflect 阶段（Phase 1）：minibatch 失败/成功 analyst，提共性 edit。

纪律（照搬 SkillOpt）：
- 失败/成功样本分开、按 M=8 打包，每批一次 optimizer LLM 调用提共性 edit；
- 失败 analyst 找「哪里坏了怎么修」，成功 analyst 找「为什么好、如何固化」；
- 所有批并行（asyncio.gather + 信号量）；
- 输出强制 JSON，非法 edit 逐条丢弃不炸批；
- Aggregate/Select 在本模块完成：跨批 dedup 合并（support_count 累加）、按支持数取 top-L。

doc_view 由调用方给「文档全文 + 被拒历史 + meta 笔记」的完整上下文
（PromptDoc.optimizer_view 产出），本模块不做拼接。
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from ..llm_client import chat_json
from .types import Edit

_PROMPTS = Path(__file__).parent / "prompts"

KIND_SPEC = (
    "kind 只能是：append（文档末尾追加新规则）、insert_after（在锚点行后插入）、"
    "replace（把锚点原文整段替换为新文）、delete（删除锚点段）。"
)


def _batch_user_prompt(role: str, doc_view: str, batch: list[dict[str, Any]], max_edit_chars: int) -> str:
    samples = []
    for i, r in enumerate(batch, 1):
        dims = r.get("dims", {})
        dim_s = " ".join(f"{k}={v}" for k, v in dims.items() if v is not None)
        excerpt = str(r.get("output", ""))[:800]
        samples.append(f"--- 样本{i} [{r.get('id')}] score={r.get('score'):.4f} {dim_s}\n{excerpt}")
    parts = [
        f"【角色】{role}",
        f"【当前 prompt 文档全文】（anchor 必须逐字摘自其中）\n{doc_view}",
    ]
    parts.append("【本批样本】\n" + "\n\n".join(samples))
    parts.append(
        "【任务】分析这批样本的共性，提出 1~3 条能修共性问题的文档 edit。"
        f"{KIND_SPEC} anchor 必须是文档中逐字、唯一的连续片段（先在文档里找好再写）；"
        f"content ≤ {max_edit_chars} 字，超限会被直接拒掉；note 用一句话说理由。"
        "宁缺毋滥：没有把握的共性就不要提 edit（edits 可为空数组）。"
        '【输出】只输出 JSON：{"analysis": "<共性诊断一句话>", "edits": [{"kind": "...", "anchor": "...", "content": "...", "note": "..."}]}'
    )
    return "\n\n".join(parts)


def _coerce_edits(data: Any, source: str, max_edit_chars: int) -> tuple[list[Edit], str]:
    if not isinstance(data, dict):
        return [], "输出非 JSON 对象"
    analysis = str(data.get("analysis") or "")[:200]
    out: list[Edit] = []
    for e in data.get("edits") or []:
        if not isinstance(e, dict):
            continue
        try:
            edit = Edit(
                kind=str(e.get("kind") or ""),
                anchor=str(e.get("anchor") or ""),
                content=str(e.get("content") or ""),
                note=str(e.get("note") or "")[:200],
                source=source,
            )
        except ValueError:
            continue
        if len(edit.content) > max_edit_chars:
            continue
        if edit.kind != "append" and not edit.anchor:
            continue
        if edit.kind != "delete" and not edit.content.strip():
            continue
        out.append(edit)
    return out, analysis


async def analyze_batch(
    *,
    role: str,
    system_prompt: str,
    doc_view: str,
    batch: list[dict[str, Any]],
    max_edit_chars: int,
    sem: asyncio.Semaphore,
) -> tuple[list[Edit], str]:
    """一批样本 → 一次 LLM 调用 → (edits, analysis)。"""
    user = _batch_user_prompt(role, doc_view, batch, max_edit_chars)
    async with sem:
        res = await chat_json(system=system_prompt, user=user, call_type="promptopt_reflect", temperature=0.4, max_tokens=1500)
    if res.get("error"):
        return [], f"LLM error: {res['error']}"
    source = "error" if "失败" in role else "success"
    return _coerce_edits(res.get("data"), source, max_edit_chars)


async def propose_edits(
    *,
    failures: list[dict[str, Any]],
    successes: list[dict[str, Any]],
    doc_view: str,
    m_minibatch: int = 8,
    max_edit_chars: int = 600,
    concurrency: int = 4,
) -> tuple[list[Edit], list[str]]:
    """失败/成功分批并行分析。返回 (合并前 edits, 各批 analysis 日志)。"""
    sys_err = (_PROMPTS / "analyst_error.md").read_text(encoding="utf-8")
    sys_ok = (_PROMPTS / "analyst_success.md").read_text(encoding="utf-8")
    sem = asyncio.Semaphore(max(1, concurrency))
    tasks, roles = [], []
    for i in range(0, len(failures), m_minibatch):
        tasks.append(analyze_batch(role="失败样本分析师", system_prompt=sys_err, doc_view=doc_view, batch=failures[i : i + m_minibatch], max_edit_chars=max_edit_chars, sem=sem))
        roles.append("error")
    for i in range(0, len(successes), m_minibatch):
        tasks.append(analyze_batch(role="成功样本分析师", system_prompt=sys_ok, doc_view=doc_view, batch=successes[i : i + m_minibatch], max_edit_chars=max_edit_chars, sem=sem))
        roles.append("success")
    if not tasks:
        return [], ["（本步无失败也无成功样本，跳过 reflect）"]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    edits: list[Edit] = []
    logs: list[str] = []
    for role, r in zip(roles, results):
        if isinstance(r, Exception):
            logs.append(f"[{role}] 批分析异常: {r}")
            continue
        es, analysis = r
        edits.extend(es)
        logs.append(f"[{role}] {analysis}（提 {len(es)} 条 edit）")
    return edits, logs


def aggregate_edits(edits: list[Edit]) -> list[Edit]:
    """跨批合并相似 edit：key = (kind, anchor, content 前 60 字)。support_count 累加。"""
    merged: dict[tuple, Edit] = {}
    order: list[tuple] = []
    for e in edits:
        key = (e.kind, e.anchor, e.content[:60])
        if key in merged:
            merged[key].support_count += 1
        else:
            merged[key] = e
            order.append(key)
    return [merged[k] for k in order]


def select_edits(candidates: list[Edit], L: int) -> list[Edit]:
    """Select 阶段：support_count 降序，同票先来后到，最多 L 条。"""
    ranked = sorted(enumerate(candidates), key=lambda t: (-t[1].support_count, t[0]))
    return [e for _, e in ranked[:max(0, L)]]
