# -*- coding: utf-8 -*-
"""PromptOpt 核心数据类型（Phase 1）。

全部为可 JSON 序列化的 dataclass：checkpoint/history/日志只存纯数据，
任何模块之间不传递活体对象（防 v5.33.1 式重绑陷阱）。
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any

EDIT_KINDS = ("append", "insert_after", "replace", "delete")


@dataclass
class Edit:
    """一条有界文档编辑。anchor 必须逐字存在于目标文档且唯一。"""

    kind: str  # append | insert_after | replace | delete
    anchor: str = ""  # append 可为空
    content: str = ""  # delete 可为空
    note: str = ""  # optimizer 的理由（进 history 供人审）
    support_count: int = 1  # aggregate 阶段合并后的支持数
    source: str = ""  # error|success|meta，来自哪个 analyst

    def __post_init__(self) -> None:
        if self.kind not in EDIT_KINDS:
            raise ValueError(f"非法 edit kind: {self.kind}")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Edit":
        return cls(
            kind=str(d.get("kind", "")),
            anchor=str(d.get("anchor", "") or ""),
            content=str(d.get("content", "") or ""),
            note=str(d.get("note", "") or ""),
            support_count=int(d.get("support_count", 1) or 1),
            source=str(d.get("source", "") or ""),
        )


@dataclass
class GateReport:
    """gate 仲裁结果：多维度均不得降 + 主指标严格上涨 + canary 否决。"""

    accepted: bool
    reasons: list[str] = field(default_factory=list)
    primary_delta: float = 0.0
    dim_deltas: dict[str, float] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def normalize_rollout(raw: dict[str, Any], sample_id: str) -> dict[str, Any]:
    """把 scorer_adapter.score() 结果规整成统一 RolloutResult 形状（纯 dict）。"""
    dims = {k: raw.get(k) for k in ("fact", "char", "plot", "syn", "flavor", "completion")}
    return {
        "id": sample_id,
        "score": float(raw.get("composite") or 0.0),
        "dims": dims,
        "ok": not raw.get("errors"),
        "errors": list(raw.get("errors") or []),
        "output_len": len(str(raw.get("_output", "") or "")),
    }
