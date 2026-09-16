"""
workflows 包 —— 从 workflows.py 拆分。

导出：
  create_workflows_router  —— 路由工厂（供 app.py 使用）
  start_init_task           —— init 工作流入门（供 app.py /api/project/create 复用）
  _InitBody                 —— init 请求体（供 app.py 类型引用）
"""
from __future__ import annotations

from ._bodies import (
    _WriteBody, _ReviewBody, _PlanBody, _InitBody,
    _FinalizeBody, _UnfinalizeBody, _ConfirmPlotBody,
    _ReplanBody, _LearnBody, _QueryBody, _CharacterSkillBody,
)
from ._finalize import start_init_task
from ._router import create_workflows_router

__all__ = [
    "create_workflows_router",
    "start_init_task",
    "_InitBody",
    "_WriteBody",
    "_ReviewBody",
    "_PlanBody",
    "_FinalizeBody",
    "_UnfinalizeBody",
    "_ConfirmPlotBody",
    "_ReplanBody",
    "_LearnBody",
    "_QueryBody",
    "_CharacterSkillBody",
]
