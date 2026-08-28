# -*- coding: utf-8 -*-
"""Canary 对抗集：评分器/生成 prompt 的回归测试。

每条 canary 是一段"评分器必须判低分"的坏样本，标注它攻击的维度与该维度的
上限分数（max_*）。Phase 2 评分器优化的 gate 硬约束：**canary 任何维度得分
不得超过其标注上限**，否则说明评分器在放过坏样本，候选 edit 直接 reject。

这是反 Goodhart 的核心护栏之一。新发现的 hack 方式永久追加到 canary.jsonl。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

_DATA = Path(__file__).resolve().parent / "data" / "canary.jsonl"

# canary 可标注的上限维度 → scorer_adapter.score() 返回键
_CANARY_CAPS = {
    "max_fact": "fact",
    "max_char": "char",
    "max_plot": "plot",
    "max_flavor": "flavor",
    "max_composite": "composite",
}


def load_canaries(path: Path | None = None) -> list[dict[str, Any]]:
    """加载冻结 canary 集。"""
    p = path or _DATA
    out = []
    with open(p, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out


def check_canary_scores(scores: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """检查一批 canary 评分是否全部低于标注上限。

    Parameters
    ----------
    scores : 与 load_canaries() 同序的 score() 结果列表。

    Returns
    -------
    violations : list[dict]，每项 {id, attack, dim, got, cap}；空 = 全部通过。
    """
    canaries = load_canaries()
    violations: list[dict[str, Any]] = []
    for canary, res in zip(canaries, scores):
        for cap_key, dim in _CANARY_CAPS.items():
            cap = canary.get(cap_key)
            if cap is None:
                continue
            got = res.get(dim)
            # flavor 可能为 None（关闭审阅）；不参与 canary 判定
            if got is None:
                continue
            if float(got) > float(cap) + 1e-6:
                violations.append({
                    "id": canary["id"],
                    "attack": canary.get("attack", ""),
                    "dim": dim,
                    "got": round(float(got), 4),
                    "cap": float(cap),
                })
    return violations
