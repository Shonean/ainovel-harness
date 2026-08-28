"""
决策检查引擎 —— 从 workflows.py 拆分。
"""
from __future__ import annotations

from pathlib import Path

from ..core.constants import (
    DecisionLevel, DEFAULT_FREEDOM_LEVEL,
    get_decision_level_for_operation, validate_decision_level,
    DECISION_LEVEL_DESCRIPTIONS,
)
from ..core.decision_log import get_decision_log
from ..services.task_manager import TASKS, Task


async def _decision_check(
    task: Task,
    step_id: str,
    operation_type: str,
    step_description: str,
) -> bool:
    """
    决策检查：在执行step之前，检查是否需要用户确认，如需则挂起等待用户决策。
    返回True表示可以继续执行，False表示用户拒绝执行。
    """
    # 一键自动生成模式：跳过所有用户确认，直接放行
    if getattr(task, "auto_generate", False):
        task.decision_records[step_id] = {
            "operation_type": operation_type,
            "approved": True,
            "auto_approved": True,
        }
        return True

    # 检查是否已经有该step的决策记录
    if step_id in task.decision_records:
        decision = task.decision_records[step_id]
        if decision.get("approved", False):
            return True
        else:
            await TASKS.emit_error(task, f"步骤 {step_id} 已被用户拒绝执行")
            return False

    # 获取该操作类型对应的默认决策级别
    required_level = get_decision_level_for_operation(operation_type) or DecisionLevel.L3
    required_level_desc = DECISION_LEVEL_DESCRIPTIONS[required_level]

    # L0级别不需要用户确认，直接执行
    if required_level == DecisionLevel.L0:
        task.decision_records[step_id] = {
            "operation_type": operation_type,
            "required_level": required_level.value,
            "approved": True,
            "auto_approved": True,
        }
        return True

    # 其他级别需要用户确认
    prompt = {
        "type": "decision_confirmation",
        "step_id": step_id,
        "operation_type": operation_type,
        "step_description": step_description,
        "required_decision_level": required_level.value,
        "required_decision_level_desc": required_level_desc,
        "schema": {
            "type": "object",
            "properties": {
                "approved": {
                    "type": "boolean",
                    "description": "是否同意执行该步骤",
                },
                "selected_option": {
                    "type": "integer",
                    "description": "L2级别时选择候选方案编号（1/2/3）",
                    "optional": required_level != DecisionLevel.L2,
                },
                "custom_prompt": {
                    "type": "string",
                    "description": "L3级别时用户自定义的Prompt",
                    "optional": required_level != DecisionLevel.L3,
                }
            },
            "required": ["approved"],
        }
    }

    # 挂起任务等待用户决策
    answer = await TASKS.suspend_for_input(task, prompt)

    # 检查是否被取消
    if answer.get("answer") == "__cancelled__":
        await TASKS.emit_error(task, f"步骤 {step_id} 被用户取消")
        return False

    # 检查用户是否批准
    if not answer.get("approved", False):
        task.decision_records[step_id] = {
            "operation_type": operation_type,
            "required_level": required_level.value,
            "approved": False,
            "user_reason": answer.get("reason", ""),
        }
        await TASKS.emit_error(task, f"步骤 {step_id} 被用户拒绝执行")
        return False

    # 校验决策级别是否符合要求
    user_level = answer.get("decision_level", required_level.value)
    if not validate_decision_level(operation_type, DecisionLevel(user_level)):
        await TASKS.emit_error(task, f"决策级别不符合要求，该操作至少需要 {required_level.value} 级别确认")
        return False

    # 记录决策
    task.decision_records[step_id] = {
        "operation_type": operation_type,
        "required_level": required_level.value,
        "user_level": user_level,
        "approved": True,
        "selected_option": answer.get("selected_option"),
        "custom_prompt": answer.get("custom_prompt"),
        "user_reason": answer.get("reason", ""),
    }

    # 持久化决策日志
    try:
        if task.project_root:
            project_root = Path(task.project_root)
            log = get_decision_log(project_root)
            log.record_decision(
                task_id=task.task_id,
                step_id=step_id,
                operation_type=operation_type,
                required_level=required_level.value,
                user_decision=answer,
                step_description=step_description,
            )
    except Exception as exc:
        # 日志记录失败不影响主流程
        await TASKS.emit(task, {
            "phase": "stderr",
            "line": f"决策日志记录失败: {exc}",
        })

    return True
