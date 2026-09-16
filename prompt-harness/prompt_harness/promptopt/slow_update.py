# -*- coding: utf-8 -*-
"""Slow update（Phase 1）：epoch 边界纵向对比 + protected SLOW_UPDATE 区 + meta skill。

- epoch 末用同批样本新旧 artifact 的 rollout 对比，让 optimizer 写一段「跨 epoch
  指导」，经专用通道整块写入文档的 SLOW_UPDATE 保护区（step 级编辑碰不到）；
- meta skill：optimizer 的跨 epoch 策略笔记，存 run_dir/meta_notes.md，
  只注入 optimizer prompt，不进 artifact 文档。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from ..llm_client import chat_json
from .artifacts import PromptDoc
from .types import Edit

_PROMPTS = Path(__file__).parent / "prompts"


def _rollout_summary(rollouts: list[dict[str, Any]], tag: str, max_chars: int = 300) -> str:
    lines = []
    for r in rollouts:
        excerpt = str(r.get("output", ""))[:max_chars]
        lines.append(f"[{r.get('id')}] score={r.get('score'):.4f}\n{excerpt}")
    return f"—— {tag} ——\n" + "\n\n".join(lines)


async def epoch_slow_update(
    *,
    doc: PromptDoc,
    old_rollouts: list[dict[str, Any]],
    new_rollouts: list[dict[str, Any]],
    epoch: int,
    max_guidance_chars: int = 1200,
) -> str:
    """纵向对比 → 写入 SLOW_UPDATE 区。返回本次指导文本（写入失败返回 ""）。"""
    system = (_PROMPTS / "slow_update.md").read_text(encoding="utf-8")
    user = (
        f"epoch {epoch} 结束。同一批样本在【epoch 初的 prompt】与【现在的 prompt】下的生成对比如下。\n\n"
        f"{_rollout_summary(old_rollouts, 'epoch 初 prompt 下的生成')}\n\n"
        f"{_rollout_summary(new_rollouts, '现在 prompt 下的生成')}\n\n"
        f"【当前文档的 SLOW_UPDATE 区现有内容】\n{doc.slow_body() or '（空）'}\n\n"
        "【任务】写一段不超过 400 字的累积式指导：本 epoch 生成质量的真实变化方向、"
        "哪些做法应坚持、哪些苗头要防（防过拟合/防丢约束）。追加进 SLOW_UPDATE 区，"
        "不否定前文，只累积。只输出 JSON：{\"guidance\": \"<新指导段>\"}"
    )
    res = await chat_json(system=system, user=user, call_type="promptopt_slow", temperature=0.3, max_tokens=800)
    data = res.get("data") or {}
    guidance = str(data.get("guidance") or "").strip()[:max_guidance_chars]
    if not guidance or res.get("error"):
        return ""
    body = doc.slow_body().rstrip()
    numbered = f"\n- epoch{epoch}: {guidance}"
    doc.replace_slow_region((body if body and "（epoch 末" not in body[:20] else "") + numbered)
    return guidance


async def update_meta_notes(
    *,
    notes_path: Path,
    epoch: int,
    step_logs: list[str],
    accept_rate: float,
) -> str:
    """跨 epoch 策略笔记（meta skill）：只活在本文件里，optimizer 专属上下文。"""
    existing = notes_path.read_text(encoding="utf-8") if notes_path.exists() else "（尚无笔记）"
    system = (
        "你在维护一份只给你自己看的「optimizer 策略笔记」。它会注入你未来所有优化决策的上下文。"
        "记录跨 epoch 的经验：哪类 edit 容易被 gate 拒、哪类命题真正提升分数、应避免的弯路。"
        "只输出 JSON：{\"notes\": \"<更新后的完整笔记，≤500字>\"}"
    )
    user = (
        f"epoch {epoch} 结束，本 epoch accept 率 {accept_rate:.0%}。\n"
        "【本 epoch 各步日志】\n" + "\n".join(step_logs[-20:]) + "\n\n"
        f"【现有笔记】\n{existing}\n\n更新笔记（保留仍然有效的旧经验，淘汰失效的）。"
    )
    res = await chat_json(system=system, user=user, call_type="promptopt_meta", temperature=0.3, max_tokens=800)
    data = res.get("data") or {}
    notes = str(data.get("notes") or "").strip()
    if notes and not res.get("error"):
        notes_path.write_text(notes, encoding="utf-8")
        return notes
    return existing
