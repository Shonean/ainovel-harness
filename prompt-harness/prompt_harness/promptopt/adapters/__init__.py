# -*- coding: utf-8 -*-
"""promptopt.adapters —— 领域实现层（Phase 5）。

小说域 adapter 集中在此门面后面；core 不 import 本包。
- novel.PromptAdapterImpl / novel.L4Adapter：ladder_invariant_l4 生产文档优化
  （rollout = 两阶段生成 → 统一评分入口打分）
- 新领域（客服/代码/翻译…）只依赖 promptopt.core，参照 examples/toy_system_prompt/。
"""
from ..l4_adapter import L4Adapter
from ..prompt_adapter import PromptGenAdapter
from ..scorer_adapter import score, score_many

__all__ = ["L4Adapter", "PromptGenAdapter", "score", "score_many"]
