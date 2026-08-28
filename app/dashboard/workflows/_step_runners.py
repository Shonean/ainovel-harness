"""
步骤执行器：子进程 + agent + skill-agent —— 从 workflows.py 拆分。
"""
from __future__ import annotations

import asyncio
import json
import traceback
from pathlib import Path

from ..routes.actions import _run_subprocess_streaming, _python, SCRIPTS_DIR, PLUGIN_ROOT
from ..services.agent_runner import AnthropicAgentRunner, _default_pro_model
from ..agents.agents import _read_agent_prompt, make_task_emitters, build_runner_kwargs
from ..core.constants import FreedomLevel, DEFAULT_FREEDOM_LEVEL
from ..services.hard_constraints import build_constrained_prompt
from ..services.task_manager import TASKS, Task

from ._decision import _decision_check
from ._utils import _read_skill

# 通用 skill-agent 工具集
_SKILL_TOOLSET = ["Read", "Grep", "Bash", "Write", "Edit", "AskUser"]


async def _run_step_subprocess(
    task: Task, step_id: str, argv: list[str], *,
    timeout: float = 600.0,
    operation_type: str = "formatting",  # 默认无风险操作
    step_description: str = "",
) -> dict | None:
    # 决策检查
    desc = step_description or f"执行子进程步骤: {step_id}"
    if not await _decision_check(task, step_id, operation_type, desc):
        return None

    await TASKS.emit(task, {"phase": "step", "step": step_id, "status": "running"})
    res = await _run_subprocess_streaming(
        task, argv=argv, timeout=timeout, parse_stdout_json=False,
        finalize_task=False,
    )
    if not res.get("ok"):
        await TASKS.emit(task, {
            "phase": "step", "step": step_id, "status": "failed",
            "stderr": res.get("stderr", "")[-500:],
        })
        return res
    await TASKS.emit(task, {
        "phase": "step", "step": step_id, "status": "done",
        "stdout_preview": (res.get("stdout") or "")[:500],
    })
    return res


async def _run_step_agent(
    task: Task, step_id: str, *,
    project_root: Path,
    system_prompt: str,
    user_input: str,
    model: str | None = None,
    max_tokens: int = 8192,
    allowed_tools: list[str] | None = None,
    temperature: float | None = None,
    operation_type: str = "chapter_draft",  # 默认生成操作
    step_description: str = "",
    freedom_level: FreedomLevel = DEFAULT_FREEDOM_LEVEL,
    chapter: int | None = None,
    stage: str | None = None,
) -> str | None:
    # 决策检查
    desc = step_description or f"执行Agent步骤: {step_id}"
    if not await _decision_check(task, step_id, operation_type, desc):
        return None

    # Phase 2: 注入三大硬约束 + 自由度级别指令
    system_prompt = build_constrained_prompt(system_prompt, freedom_level)

    await TASKS.emit(task, {"phase": "step", "step": step_id, "status": "running"})
    on_event, on_token = make_task_emitters(task, step_id)
    runner = AnthropicAgentRunner(
        project_root=project_root,
        model=model or _default_pro_model(),
        system_prompt=system_prompt,
        max_tokens=max_tokens,
        allowed_tools=allowed_tools or ["Read", "Grep", "Bash"],
        agent_name=step_id,
        temperature=temperature,
        on_event=on_event,
        on_token=on_token,
        meta={"stage": stage or step_id, "chapter": chapter},
    )
    try:
        text = await runner.run(user_input)
        await TASKS.emit(task, {
            "phase": "step", "step": step_id, "status": "done",
            "text_preview": text[:500],
        })
        return text
    except Exception as exc:
        await TASKS.emit(task, {
            "phase": "step", "step": step_id, "status": "failed",
            "error": str(exc),
        })
        raise


def _build_skill_system_prompt(
    skill_name: str, project_root: Path,
    freedom_level: FreedomLevel = DEFAULT_FREEDOM_LEVEL,
) -> str:
    """构建 skill agent 的 system_prompt（环境上下文 + SKILL.md + 硬约束注入）。

    抽出为公共函数，供 _run_skill_agent（运行）与 _prompt_preview（预览）共用，
    保证「预览组装」与「实际发出」byte 一致。
    """
    skill_md = _read_skill(skill_name, project_root)
    # Phase 6: 预替换 SKILL.md 中的占位符
    skill_md = skill_md.replace("${SCRIPTS_DIR}", str(SCRIPTS_DIR))
    skill_md = skill_md.replace("${PROJECT_ROOT}", str(project_root))
    skill_md = skill_md.replace("{project_root}", str(project_root))
    system_prompt = (
        "# 环境上下文\n"
        f"PROJECT_ROOT={project_root}\n"
        f"SCRIPTS_DIR={SCRIPTS_DIR}\n"
        f"CLAUDE_PLUGIN_ROOT={PLUGIN_ROOT}\n\n"
        f"你在 dashboard 后端运行，代替 Claude Code 终端执行 `{skill_name}` skill。"
        "工具使用约定：\n"
        "- Bash：只跑单条 `python -X utf8 ...` 命令（ainovel.py / data_modules / "
        "reference_search.py / python -c），不要用管道/&&/sed/for 等 bash 语法；\n"
        "- Read/Write/Edit：读写 PROJECT_ROOT 下的文件，不要用 cat/cp/echo；\n"
        "- AskUser：需要用户裁决、候选选择、最终确认时调用（会弹窗阻塞等待）。\n\n"
        "下面是该 skill 的完整指令，严格按其 Step 顺序执行：\n\n"
        f"{skill_md}"
    )
    # Phase 2: 注入三大硬约束 + 自由度级别指令
    return build_constrained_prompt(system_prompt, freedom_level)


async def _run_skill_agent(
    task: Task, *, project_root: Path, skill_name: str, user_input: str,
    model: str | None, agent_name: str, max_tokens: int = 16384,
    max_turns: int = 50,
    temperature: float | None = None,
    operation_type: str = "outline_generate",  # 默认生成操作
    step_description: str = "",
    freedom_level: FreedomLevel = DEFAULT_FREEDOM_LEVEL,
    finalize_task: bool = True,
    meta: dict | None = None,
) -> str | None:
    """以 skill 的 SKILL.md 为 system_prompt 跑一个全工具 agent，代替终端执行该 skill。"""
    # 决策检查
    step_id = f"skill_{skill_name}"
    desc = step_description or f"执行Skill: {skill_name}"
    if not await _decision_check(task, step_id, operation_type, desc):
        return None

    system_prompt = _build_skill_system_prompt(skill_name, project_root, freedom_level)
    if not _read_skill(skill_name, project_root):
        await TASKS.emit_error(task, f"找不到 skill 定义: {skill_name}/SKILL.md")
        return ""
    kw = build_runner_kwargs(task, agent_name)
    runner = AnthropicAgentRunner(
        project_root=project_root,
        model=model or _default_pro_model(),
        system_prompt=system_prompt,
        max_tokens=max_tokens,
        allowed_tools=_SKILL_TOOLSET,
        agent_name=agent_name,
        max_turns=max_turns,
        temperature=temperature,
        meta=meta,
        **kw,
    )
    await TASKS.emit(task, {
        "phase": "stdout",
        "line": f"启动 {skill_name} skill agent（模型: {model or _default_pro_model()}）",
    })
    try:
        text = await runner.run(user_input)
        done_payload = {"skill": skill_name, "text": text[:2000]}
        if getattr(runner, "truncated", False):
            done_payload["truncated"] = True
            done_payload["reason"] = "turn_limit"
        if finalize_task:
            await TASKS.emit_done(task, done_payload)
        else:
            await TASKS.emit(task, {"phase": "stdout", "line": f"skill {skill_name} 完成，等待后续处理"})
        return text
    except Exception as exc:
        await TASKS.emit_error(task, str(exc), traceback=traceback.format_exc())
        return ""
