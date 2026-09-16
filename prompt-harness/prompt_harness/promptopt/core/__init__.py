# -*- coding: utf-8 -*-
"""promptopt.core —— 领域无关的优化内核（Phase 5）。

对外只暴露六阶段循环 + 文档编辑原语 + adapter 协议。本包不 import 任何
小说域模块（prompt_adapter/scorer_adapter/l4_adapter 等在 promptopt.adapters）。

模块物理位置仍在包根（原地分层，run_*.py 不受影响）；这里按角色重导出：

- 循环：trainer.Trainer / TrainerConfig / TrainerState
- 编辑：edits.apply_patch / Edit（types）
- 文档：artifacts.PromptDoc（FIELD 锚点 + protected 区）
- 闸门：gate.evaluate_gate（纯算术反 Goodhart：多维不得降+主指标严格涨+canary 否决）
- 策略：lr（cosine/autonomous）、reflect（minibatch 反思）、rejection_buffer、
        slow_update、checkpoint
- 契约：core.api.PromptAdapter、types.normalize_rollout
"""
from ..artifacts import PromptDoc
from ..checkpoint import Checkpointer
from ..edits import apply_patch
from ..gate import evaluate_gate
from ..lr import autonomous_lr, cosine_lr
from ..reflect import aggregate_edits, propose_edits, select_edits
from ..rejection_buffer import RejectionBuffer
from ..slow_update import epoch_slow_update, update_meta_notes
from ..trainer import Trainer, TrainerConfig, TrainerState
from ..types import Edit, GateReport, normalize_rollout
from .api import PromptAdapter

__all__ = [
    "PromptAdapter",
    "Trainer", "TrainerConfig", "TrainerState",
    "PromptDoc", "Edit", "GateReport",
    "apply_patch", "evaluate_gate", "normalize_rollout",
    "cosine_lr", "autonomous_lr",
    "propose_edits", "aggregate_edits", "select_edits",
    "RejectionBuffer", "Checkpointer", "epoch_slow_update", "update_meta_notes",
]
