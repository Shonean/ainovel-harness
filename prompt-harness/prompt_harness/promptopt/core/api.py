# -*- coding: utf-8 -*-
"""Adapter 协议（Phase 5 领域无关化）。

promptopt 核心循环（trainer.py 六阶段）对「被优化对象」的全部要求就两条：

1. await rollout(doc_text, pool="train"|"heldout") -> list[dict]
   每个元素由 promptopt.types.normalize_rollout 规整为：
       {id, score, dims, ok, errors, output_len}
2. await evaluate(doc_text) -> promptopt.gate 可消费的 EvalResult dict
   （held-out 全量分 + 可选 canary 检查；gate 只做算术，无 LLM）

再加两个纯文本原语（有界编辑 + 序列化）由 core 自带，adapter 无需实现：
- promptopt.edits.apply_patch(doc_text, edits)
- promptopt.artifacts.PromptDoc（FIELD 锚点分区 + protected 区）

写一个新领域 adapter = 实现这两个 async 方法（见 adapters/novel.py 的实例，
以及 examples/toy_system_prompt/ 的 60 行最小 demo）。
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class PromptAdapter(ABC):
    """领域 adapter 协议：rollout 产出带分轨迹，evaluate 产出 gate 仲裁输入。"""

    train_pool: str = "train"
    eval_pool: str = "heldout"

    @abstractmethod
    async def rollout(self, doc_text: str, pool: str = "train",
                      keep_output: bool = False) -> list[dict[str, Any]]:
        """用当前 artifact 对样本池 rollout，返回 normalize_rollout 形状的列表。"""

    @abstractmethod
    async def evaluate(self, doc_text: str) -> dict[str, Any]:
        """held-out 全量评估（gate 输入）：primary + dims + 可选 canary 违规。"""
