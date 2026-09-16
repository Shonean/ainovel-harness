# -*- coding: utf-8 -*-
"""学习率调度（Phase 1）。

- cosine：cosine 退火控制每步最多采纳的 edit 数 L（L_max → L_min）。
- autonomous：optimizer LLM 看失败严重程度自定 L（lr_autonomous.md），出错回退 cosine。
"""
from __future__ import annotations

import math
from pathlib import Path

from ..llm_client import chat_json


def cosine_lr(step: int, total_steps: int, l_max: int, l_min: int = 1) -> int:
    """第 step/total_steps 步允许的最大 edit 数（cosine 退火）。"""
    if total_steps <= 1:
        return l_min
    t = min(max(step / total_steps, 0.0), 1.0)
    val = l_min + (l_max - l_min) * 0.5 * (1.0 + math.cos(math.pi * t))
    return max(l_min, min(l_max, round(val)))


async def autonomous_lr(
    *,
    step: int,
    total_steps: int,
    l_max: int,
    l_min: int,
    failures_summary: str,
    gate_reasons: str,
    call_type: str = "promptopt_lr",
) -> tuple[int, str]:
    """让 optimizer 自定本步 L。返回 (L, 理由)；LLM 失败回退 cosine。"""
    fallback = cosine_lr(step, total_steps, l_max, l_min)
    system = (Path(__file__).parent / "prompts" / "lr_autonomous.md").read_text(encoding="utf-8")
    user = (
        f"当前步：{step}/{total_steps}\n允许范围：L ∈ [{l_min}, {l_max}]\n\n"
        f"【本步失败样本摘要】\n{failures_summary or '（无）'}\n\n"
        f"【上一步被拒原因】\n{gate_reasons or '（无）'}"
    )
    res = await chat_json(system=system, user=user, call_type=call_type, temperature=0.2, max_tokens=300)
    data = res.get("data") or {}
    try:
        L = int(data.get("L"))
    except (TypeError, ValueError):
        return fallback, f"LLM 输出无效，回退 cosine L={fallback}"
    L = max(l_min, min(l_max, L))
    return L, str(data.get("reason") or "")[:200] or f"autonomous L={L}"
