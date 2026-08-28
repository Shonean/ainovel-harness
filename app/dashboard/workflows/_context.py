"""
context-agent 步骤 —— 从 workflows.py 拆分。
"""
from __future__ import annotations

from pathlib import Path

from ..routes.actions import SCRIPTS_DIR
from ..agents.agents import _read_agent_prompt
from ..core.constants import FreedomLevel, DEFAULT_FREEDOM_LEVEL
from ..services.task_manager import TASKS, Task

from ._prompt_fragments import resolve_fragment, fill_template
from ._step_runners import _run_step_agent


async def _run_context_for_chapter(
    task: Task, *, project_root: Path, chapter: int, model: str | None,
    temperature: float | None = None,
    freedom_level: FreedomLevel = DEFAULT_FREEDOM_LEVEL,
) -> str:
    """为指定章节运行 context-agent，返回任务书文本。"""
    system_prompt = _read_agent_prompt("context-agent.md", project_root)
    if not system_prompt:
        await TASKS.emit_error(task, "context-agent.md 不存在")
        raise RuntimeError("context-agent.md 不存在")
    # Phase 6: 预替换占位符，避免 LLM 不替换导致命令失败
    system_prompt = system_prompt.replace("${SCRIPTS_DIR}", str(SCRIPTS_DIR))
    system_prompt = system_prompt.replace("${PROJECT_ROOT}", str(project_root))
    system_prompt = system_prompt.replace("{project_root}", str(project_root))
    system_prompt = system_prompt.replace("{NNNN}", f"{chapter:04d}")
    # user_input 模板（可覆盖片段）
    user_input = fill_template(
        resolve_fragment(project_root, "stages/context_user_input.md"),
        chapter=chapter,
        project_root=project_root,
        scripts_dir=SCRIPTS_DIR,
    )
    return await _run_step_agent(
        task, "context",
        project_root=project_root,
        system_prompt=system_prompt,
        user_input=user_input,
        model=model,
        max_tokens=8192,
        temperature=temperature,
        operation_type="chapter_draft",
        step_description=f"为第 {chapter} 章生成写作任务书，包含本章的所有约束和要求",
        freedom_level=freedom_level,
        chapter=chapter,
    )
