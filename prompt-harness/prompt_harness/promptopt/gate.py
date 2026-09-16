# -*- coding: utf-8 -*-
"""Gate 仲裁（Phase 1）：多维度均不得降 + 主指标严格上涨 + canary 否决。

EvalResult 是纯数据形状（primary + 维度均值 + canary 违规数），
evaluate_gate 只做纯算术比较——没有 LLM、没有 IO，方便单测与复用。
"""
from __future__ import annotations

from typing import Any

from .types import GateReport


def eval_from_rollouts(
    rollouts: list[dict[str, Any]],
    *,
    canary_violations: int | None = None,
) -> dict[str, Any]:
    """把一批 rollout 规整成 EvalResult dict。

    primary = ok 样本 composite 均值；dims = 各维度均值（None 维跳过）。
    """
    ok = [r for r in rollouts if r.get("ok", True)]
    pool = ok or rollouts
    primary = sum(float(r.get("score") or 0.0) for r in pool) / max(len(pool), 1)
    dims: dict[str, float] = {}
    keys = [k for k in ("fact", "char", "plot", "syn", "flavor", "completion") if any(r.get("dims", {}).get(k) is not None for r in pool)]
    for k in keys:
        vals = [float(r["dims"][k]) for r in pool if r.get("dims", {}).get(k) is not None]
        if vals:
            dims[k] = sum(vals) / len(vals)
    return {
        "primary": primary,
        "dims": dims,
        "canary_violations": canary_violations,
        "n": len(rollouts),
        "n_ok": len(ok),
    }


def evaluate_gate(
    base: dict[str, Any],
    cand: dict[str, Any],
    *,
    min_delta: float = 0.01,
    dim_eps: float = 0.05,
    guard_dims: tuple[str, ...] = ("fact", "plot", "completion"),
) -> GateReport:
    """候选 EvalResult vs 基线：全部通过才 accepted。

    - 主指标：cand.primary >= base.primary + min_delta（严格上涨）
    - 守护维度：双方都非 None 的 guard_dim 上 cand >= base - dim_eps（均不得降）
    - canary：双方都有违规数时 cand <= base（放水即拒）
    """
    reasons: list[str] = []
    delta = cand["primary"] - base["primary"]
    if delta < min_delta:
        reasons.append(f"主指标上涨不足：{delta:+.4f} < min_delta {min_delta}")
    dim_deltas: dict[str, float] = {}
    for d in guard_dims:
        b, c = base["dims"].get(d), cand["dims"].get(d)
        if b is None or c is None:
            continue  # 单侧缺失（如 flavor=None）不参与判定
        dim_deltas[d] = c - b
        if c < b - dim_eps:
            reasons.append(f"守护维度 {d} 下降 {c - b:+.4f}（超容差 {dim_eps}）")
    bv, cv = base.get("canary_violations"), cand.get("canary_violations")
    if bv is not None and cv is not None and cv > bv:
        reasons.append(f"canary 违规增加：{bv} → {cv}")
    return GateReport(
        accepted=not reasons,
        reasons=reasons,
        primary_delta=delta,
        dim_deltas=dim_deltas,
    )
