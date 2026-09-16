"""
章纲审批工作流 —— 在 plan 和 write 之间插入用户确认环节。

approve-outline: 从章纲生成故事简要供用户审核
confirm-outline: 用户确认后更新 chapter_NNN.json 并推进状态
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from ..services.agent_runner import AnthropicAgentRunner, _default_pro_model
from ..agents.agents import _read_agent_prompt, make_task_emitters
from ..core.constants import FreedomLevel, DEFAULT_FREEDOM_LEVEL
from ..services.task_manager import TASKS, Task

from ._step_runners import _run_step_subprocess, _python


async def _run_approve_outline_workflow(
    task: Task, *, project_root: Path, chapter: int,
    model: str | None = None,
    freedom_level: FreedomLevel = DEFAULT_FREEDOM_LEVEL,
) -> str:
    """Step 1: 读取章纲，生成故事简要，返回文本供前端展示。

    返回故事简要文本，前端展示给用户编辑。
    同时落盘到 大纲/第NNNN章-故事简要.md。
    """
    await TASKS.emit(task, {"phase": "step", "step": "approve-outline", "status": "running"})

    # 读取章纲原文
    outline_dir = project_root / "大纲"
    outline_files = sorted(outline_dir.glob(f"第{chapter:04d}章*.md"))
    if not outline_files:
        outline_files = sorted(outline_dir.glob(f"第{chapter}章*.md"))
    if not outline_files:
        outline_files = sorted(outline_dir.glob(f"第{chapter:02d}章*.md"))

    outline_text = ""
    for f in outline_files:
        if "故事简要" in f.name or "写作任务书" in f.name:
            continue
        try:
            outline_text += f"\n\n---\n## {f.name}\n\n" + f.read_text(encoding="utf-8")
        except Exception:
            pass

    if not outline_text.strip():
        # 回退：从 volume 详细大纲提取
        volume = 1
        try:
            state = json.loads((project_root / ".ainovel" / "state.json").read_text(encoding="utf-8"))
            volume = state.get("progress", {}).get("current_volume", 1)
        except Exception:
            pass
        vol_outline = outline_dir / f"第{volume}卷-详细大纲.md"
        if vol_outline.is_file():
            outline_text = vol_outline.read_text(encoding="utf-8")

    if not outline_text.strip():
        await TASKS.emit_error(task, f"第 {chapter} 章未找到章纲文件，无法生成故事简要")
        raise RuntimeError("章纲文件不存在")

    # 也读取 chapter_NNN.json directive 作为补充
    chapter_json = project_root / ".story-system" / "chapters" / f"chapter_{chapter:03d}.json"
    directive_text = ""
    if chapter_json.is_file():
        try:
            directive = json.loads(chapter_json.read_text(encoding="utf-8"))
            cd = directive.get("chapter_directive", {})
            if cd:
                directive_text = json.dumps(cd, ensure_ascii=False, indent=2)
        except Exception:
            pass

    # 生成故事简要
    system_prompt = (
        "你是一个网络小说章纲审核助手。你的任务是把章纲（详细的章节大纲）提炼成一份简洁的「故事简要」，"
        "供作者审核确认。作者确认后，写章流程将严格按此简要执行。\n\n"
        "## 输出格式\n\n"
        "### 本章标题\n（从章纲中提取或推断）\n\n"
        "### 核心事件（一句话）\n用一句话说清楚本章发生了什么。\n\n"
        "### 情节推进\n按顺序列出关键情节节点（3-5 个），每个一句话。\n\n"
        "### 出场人物及作用\n每人一行：角色名 — 本章作用。\n\n"
        "### 章末钩子\n本章结尾留给读者的悬念或未完感。\n\n"
        "## 规则\n"
        "- 严格基于章纲内容，不新增章纲没有的情节、人物、设定\n"
        "- 简洁直接，300-500 字\n"
        "- 不输出章纲原文，只输出提炼后的简要\n"
        "- 不要解释、不要工具调用，直接输出 markdown"
    )

    user_input = (
        f"请提炼第 {chapter} 章的故事简要。\n\n"
        f"## 章纲原文\n\n{outline_text[:8000]}\n"
    )
    if directive_text:
        user_input += f"\n## 结构化章纲指令\n\n{directive_text[:4000]}\n"
    user_input += "\n请按上述格式输出本章的故事简要。"

    on_event, on_token = make_task_emitters(task, "approve-outline")
    runner = AnthropicAgentRunner(
        project_root=project_root,
        model=model or _default_pro_model(),
        system_prompt=system_prompt,
        max_tokens=4096,
        allowed_tools=[],
        agent_name="approve-outline",
        on_event=on_event,
        on_token=on_token,
        max_turns=1,
    )
    brief_text = await runner.run(user_input)

    if not brief_text or not brief_text.strip():
        await TASKS.emit_error(task, "故事简要生成返回空")
        raise RuntimeError("故事简要生成失败")

    # 落盘
    brief_path = outline_dir / f"第{chapter:04d}章-故事简要.md"
    brief_path.parent.mkdir(parents=True, exist_ok=True)
    brief_path.write_text(brief_text, encoding="utf-8")

    await TASKS.emit(task, {
        "phase": "step", "step": "approve-outline", "status": "done",
        "brief": brief_text,
        "path": str(brief_path.relative_to(project_root)),
    })
    return brief_text


async def _run_confirm_outline_workflow(
    task: Task, *, project_root: Path, chapter: int,
    brief_text: str,
) -> None:
    """Step 2: 用户确认故事简要，写回章纲并更新 chapter_NNN.json。"""
    await TASKS.emit(task, {"phase": "step", "step": "confirm-outline", "status": "running"})

    # 1. 写回故事简要
    outline_dir = project_root / "大纲"
    outline_dir.mkdir(parents=True, exist_ok=True)
    brief_path = outline_dir / f"第{chapter:04d}章-故事简要.md"
    brief_path.write_text(brief_text, encoding="utf-8")
    await TASKS.emit(task, {
        "phase": "stdout",
        "line": f"故事简要已保存 -> {brief_path.relative_to(project_root)}",
    })

    # 2. 重新注册 chapter_NNN.json（--force 覆盖旧数据）
    scripts_dir = Path(__file__).resolve().parents[3] / "scripts"
    await _run_step_subprocess(
        task, "register-chapters",
        argv=[_python(), "-X", "utf8",
              str(scripts_dir / "register_chapters.py"),
              "--project-root", str(project_root),
              "--start", str(chapter), "--end", str(chapter),
              "--force"],
        timeout=60.0,
        operation_type="formatting",
        step_description=f"重新注册第 {chapter} 章结构化章纲（--force 覆盖）",
    )

    # 3. 推进 chapter_status
    state_path = project_root / ".ainovel" / "state.json"
    if state_path.is_file():
        try:
            state = json.loads(state_path.read_text(encoding="utf-8"))
            chapter_status = state.setdefault("progress", {}).setdefault("chapter_status", {})
            ch_key = str(chapter)
            if ch_key not in chapter_status:
                chapter_status[ch_key] = {}
            chapter_status[ch_key]["stage"] = "outline_confirmed"
            chapter_status[ch_key]["brief_file"] = str(brief_path.relative_to(project_root))
            state_path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
            await TASKS.emit(task, {
                "phase": "stdout",
                "line": f"第 {chapter} 章状态已更新: outline_confirmed",
            })
        except Exception as exc:
            await TASKS.emit(task, {
                "phase": "stderr",
                "line": f"更新 state.json 失败（不阻断确认）: {exc}",
            })

    await TASKS.emit(task, {
        "phase": "step", "step": "confirm-outline", "status": "done",
    })
