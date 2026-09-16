"""
角色脚本预生成步骤 -- 从 workflows.py 拆分。
"""
from __future__ import annotations

from pathlib import Path

from ..services.agent_runner import AnthropicAgentRunner, _default_character_model
from ..agents.agents import _read_agent_prompt, make_task_emitters
from ..core.constants import FreedomLevel, DEFAULT_FREEDOM_LEVEL
from ..services.hard_constraints import build_constrained_prompt
from ..services.task_manager import TASKS, Task

from ._prompt_fragments import resolve_fragment, fill_template


async def _run_character_script(
    task: Task, *, project_root: Path, chapter: int,
    ctx_text: str,
    freedom_level: FreedomLevel = DEFAULT_FREEDOM_LEVEL,
) -> tuple[Path | None, bool]:
    """用 Character 模型预生成 prose 小说体对话场景。

    从任务书中提取交互设计简报，调 character-dialogue-agent 为含对话的
    beat 生成可直接粘入正文的 prose 段落，落盘 AI生成/第NNNN章-角色脚本.md。

    Returns (script_path, user_approved):
      - script_path: 脚本文件路径（None 表示生成失败或跳过）
      - user_approved: 用户是否确认使用（False 表示跳过或失败）
    """
    from ..routes.actions import SCRIPTS_DIR

    agent_md = _read_agent_prompt("character-dialogue-agent.md", project_root)
    if not agent_md:
        await TASKS.emit(task, {
            "phase": "stdout",
            "line": "⚠ character-dialogue-agent.md 不存在，跳过 Step 1.5",
        })
        return None, False

    await TASKS.emit(task, {"phase": "step", "step": "character-script", "status": "running"})

    system_prompt = build_constrained_prompt(agent_md, freedom_level)
    on_event, on_token = make_task_emitters(task, "character-script")

    runner = AnthropicAgentRunner(
        project_root=project_root,
        model=_default_character_model(),
        system_prompt=system_prompt,
        max_tokens=16384,
        allowed_tools=["Read", "Grep", "Write", "Bash"],
        agent_name="character-script",
        on_event=on_event,
        on_token=on_token,
        max_turns=30,
        meta={"stage": "character", "chapter": chapter},
    )

    user_input = fill_template(
        resolve_fragment(project_root, "stages/character_user_input.md"),
        chapter=chapter,
        chapter_padded=f"{chapter:04d}",
        project_root=project_root,
        scripts_dir=SCRIPTS_DIR,
        ctx_text=ctx_text,
    )

    try:
        script_text = await runner.run(user_input)
    except Exception as exc:
        await TASKS.emit(task, {
            "phase": "stderr",
            "line": f"角色脚本生成失败（不阻断主流程）: {exc}",
        })
        return None, False

    if not script_text or not script_text.strip():
        await TASKS.emit(task, {
            "phase": "stdout",
            "line": "角色脚本生成结果为空，跳过",
        })
        return None, False

    # 落盘
    ai_dir = project_root / "AI生成"
    ai_dir.mkdir(exist_ok=True)
    script_path = ai_dir / f"第{chapter:04d}章-角色脚本.md"
    script_path.write_text(script_text, encoding="utf-8")

    await TASKS.emit(task, {
        "phase": "step", "step": "character-script", "status": "done",
        "path": str(script_path.relative_to(project_root)),
        "preview": script_text[:300],
    })

    # 一键自动生成模式：默认使用角色脚本，不挂起询问
    if getattr(task, "auto_generate", False):
        await TASKS.emit(task, {
            "phase": "stdout",
            "line": "自动生成模式：默认使用角色脚本，跳过确认",
        })
        task.decision_records["character-script"] = {
            "step": "character-script",
            "chapter": chapter,
            "user_approved": True,
        }
        return script_path, True

    # 默认使用角色脚本，不弹窗询问用户
    user_approved = True
    await TASKS.emit(task, {
        "phase": "stdout",
        "line": "角色脚本已生成，默认在起草时使用",
    })

    # 记录决策
    task.decision_records["character-script"] = {
        "step": "character-script",
        "chapter": chapter,
        "user_approved": user_approved,
    }

    return script_path, user_approved
