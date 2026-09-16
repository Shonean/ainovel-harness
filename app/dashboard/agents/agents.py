"""
agents.py — Phase A2：把 5 个 LLM agent 做成 POST 路由。

每个路由：
1. 从 agent .md 文件读取 system_prompt。
2. 接收 chapter / project_root / 额外参数。
3. 调 AnthropicAgentRunner 做多轮 tool-use 循环。
4. 通过 SSE 流回 token + 工具调用事件 + 最终 JSON。
"""

from __future__ import annotations

import asyncio
import json
import os
import traceback
from pathlib import Path
from typing import Callable

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from ..services.agent_runner import AnthropicAgentRunner, _default_pro_model
from ..core.constants import DEFAULT_FREEDOM_LEVEL
from ..services.hard_constraints import build_constrained_prompt
from ..services.task_manager import TASKS, Task

# ---------------------------------------------------------------------------
# 工具函数
# ---------------------------------------------------------------------------

PLUGIN_ROOT = Path(__file__).resolve().parents[2]  # app/
AGENTS_DIR = PLUGIN_ROOT / "agents"                # app/agents/（.md 文件）
SCRIPTS_DIR = Path(__file__).resolve().parents[3] / "tools"  # 仓库根 tools/


def _ensure_scripts_on_path() -> None:
    import sys
    scripts_entry = str(SCRIPTS_DIR)
    if scripts_entry not in sys.path:
        sys.path.insert(0, scripts_entry)


def _read_agent_prompt(filename: str, project_root: Path | None = None) -> str:
    """读取 agent prompt 文件。优先读测试书本地副本，回退全局。"""
    if project_root is not None:
        from .workflows._prompts import resolve_prompt_path
        p = resolve_prompt_path(project_root, "agents", filename)
        if p.is_file():
            return p.read_text(encoding="utf-8")
    # 回退全局
    p = AGENTS_DIR / filename
    if not p.is_file():
        return ""
    return p.read_text(encoding="utf-8")


def _default_model() -> str:
    return _default_pro_model()


def make_task_emitters(task: Task, agent_name: str = "agent"):
    """构造绑定到 task 的 async 回调：把 agent 的所有事件推到 SSE 流。

    on_token 留空 —— run() 里每个 text delta 已同时调 on_event({"phase":"token"}),
    由 on_event 统一推流，避免 token 重复。
    """
    async def on_event(ev: dict) -> None:
        await TASKS.emit(task, ev)

    async def on_token(_text: str) -> None:
        return None

    return on_event, on_token


def make_suspend_fn(task: Task):
    """构造 AskUser 挂起回调：把问题推到 SSE 并阻塞等待用户回答。

    返回的 answer 形如 {"answer": "<用户输入或所选选项>"}。
    """
    async def _suspend(prompt: dict) -> dict:
        # 一键自动生成模式：AskUser 不挂起，按默认答案自动应答
        if getattr(task, "auto_generate", False):
            options = prompt.get("options")
            if isinstance(options, list) and options:
                first = options[0]
                default = first.get("label", first) if isinstance(first, dict) else str(first)
            else:
                # 加固：无 options 时返回有意义默认值，避免空字符串导致静默失败
                default = "确认"
            await TASKS.emit(task, {
                "phase": "stdout",
                "line": f"自动生成模式：AskUser 自动应答 -> {default}",
            })
            return {"answer": default}
        answer = await TASKS.suspend_for_input(task, prompt)
        if not isinstance(answer, dict):
            answer = {"answer": str(answer)}
        elif "answer" not in answer:
            # resume 路由可能直接传字符串
            answer = {"answer": answer.get("answer") or str(answer)}
        return answer

    return _suspend


def build_runner_kwargs(task: Task, agent_name: str = "agent") -> dict:
    """组装 AnthropicAgentRunner 的回调 kwargs（on_event/on_token/on_ask_user/cancel_event）。"""
    on_event, on_token = make_task_emitters(task, agent_name)
    return {
        "on_event": on_event,
        "on_token": on_token,
        "on_ask_user": make_suspend_fn(task),
        "cancel_event": task.cancel_event,
    }


# ---------------------------------------------------------------------------
# 通用：从 agent 路由到 SSE 流式返回
# ---------------------------------------------------------------------------

async def _run_agent_and_stream(
    project_root: Path,
    system_prompt: str,
    user_input: str,
    *,
    model: str | None = None,
    allowed_tools: list[str] | None = None,
    max_tokens: int = 8192,
    agent_name: str = "agent",
) -> str:
    """在 SSE 流中跑 agent，返回最终文本。"""
    # Phase 2: 注入三大硬约束
    system_prompt = build_constrained_prompt(system_prompt, DEFAULT_FREEDOM_LEVEL)
    final_text = ""

    runner = AnthropicAgentRunner(
        project_root=project_root,
        model=model or _default_model(),
        system_prompt=system_prompt,
        max_tokens=max_tokens,
        allowed_tools=allowed_tools,
        agent_name=agent_name,
        on_event=lambda ev: None,  # SSE 发在外面
        on_token=lambda t: None,
    )

    # 直接用 runner.run 获得最终文本
    try:
        final_text = await runner.run(user_input)
    except Exception as exc:
        final_text = f"[agent 错误] {exc}"
    return final_text


async def _stream_agent_response(
    task: Task,
    project_root: Path,
    system_prompt: str,
    user_input: str,
    *,
    model: str | None = None,
    allowed_tools: list[str] | None = None,
    max_tokens: int = 8192,
    agent_name: str = "agent",
) -> None:
    """把 agent 的所有事件实时推给 task 的 SSE 流。"""
    # Phase 2: 注入三大硬约束
    system_prompt = build_constrained_prompt(system_prompt, DEFAULT_FREEDOM_LEVEL)
    await TASKS.emit(task, {"phase": "stdout", "line": f"启动 agent（模型: {model or _default_model()}）"})

    on_event, on_token = make_task_emitters(task, agent_name)
    runner = AnthropicAgentRunner(
        project_root=project_root,
        model=model or _default_model(),
        system_prompt=system_prompt,
        max_tokens=max_tokens,
        allowed_tools=allowed_tools,
        agent_name=agent_name,
        on_event=on_event,
        on_token=on_token,
    )

    try:
        final_text = await runner.run(user_input)
        await TASKS.emit_done(task, {"text": final_text})
    except Exception as exc:
        import traceback
        await TASKS.emit_error(task, str(exc), traceback=traceback.format_exc())


# ---------------------------------------------------------------------------
# Pydantic body 模型
# ---------------------------------------------------------------------------

class _AgentBody(BaseModel):
    chapter: int = Field(ge=1, default=1)
    model: str | None = None
    params: dict = Field(default_factory=dict)


class _CriticBody(BaseModel):
    chapter: int = Field(ge=1, default=1)
    window_text: str = Field(default="")
    scene_anchor: str = Field(default="")
    style_anchor: str = Field(default="")
    chapter_meta: dict = Field(default_factory=dict)
    model: str | None = None


class _DeconstructionBody(BaseModel):
    reference_title: str = Field(default="")
    source: str = Field(default="")
    text_path: str = Field(default="")
    analysis_mode: str = Field(default="full")
    target_genre: str = Field(default="")
    model: str | None = None


class _DraftBody(BaseModel):
    chapter: int = Field(ge=1, default=1)
    context: str | None = None
    model: str | None = None


class _PolishBody(BaseModel):
    chapter: int = Field(ge=1, default=1)
    model: str | None = None


# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------

def create_agents_router(get_project_root: Callable[[], Path]) -> APIRouter:
    router = APIRouter(prefix="/api/agents", tags=["agents"])

    _ensure_scripts_on_path()

    # ===== context-agent =====
    @router.post("/context")
    async def agent_context(body: _AgentBody):
        root = get_project_root()
        system_prompt = _read_agent_prompt("context-agent.md", root)
        if not system_prompt:
            raise HTTPException(500, "context-agent.md 不存在")

        user_input = (
            f"请为第 {body.chapter} 章组装写作任务书。\n"
            f"project_root: {root}\n"
            f"scripts_dir: {SCRIPTS_DIR}\n"
            f"你需要先调 Bash 工具跑 load-context、style_sampler select、"
            f"user_revision_differ high-freq 等脚本，然后基于结果输出五段任务书。"
        )

        task = TASKS.create(kind="agent", label=f"context-agent ch{body.chapter}")

        async def _bg() -> None:
            await _stream_agent_response(
                task, root, system_prompt, user_input,
                model=body.model,
                allowed_tools=["Read", "Grep", "Bash"],
                agent_name="context",
            )

        asyncio.create_task(_bg())
        return {"task_id": task.task_id, "label": task.label}

    # ===== reviewer =====
    @router.post("/reviewer")
    async def agent_reviewer(body: _AgentBody):
        root = get_project_root()
        system_prompt = _read_agent_prompt("reviewer.md", root)
        if not system_prompt:
            raise HTTPException(500, "reviewer.md 不存在")

        from .services.workflow_status import _find_chapter_md
        draft = _find_chapter_md(root, body.chapter)
        chapter_file = str(draft) if draft else f"正文/第{body.chapter:04d}章-*.md"

        user_input = (
            f"审查第 {body.chapter} 章。\n"
            f"chapter_file: {chapter_file}\n"
            f"project_root: {root}\n"
            f"scripts_dir: {SCRIPTS_DIR}\n"
            f"请输出结构化 issues JSON（含 severity/category/evidence/fix_hint/blocking）。"
        )

        task = TASKS.create(kind="agent", label=f"reviewer ch{body.chapter}")

        async def _bg() -> None:
            await _stream_agent_response(
                task, root, system_prompt, user_input,
                model=body.model,
                allowed_tools=["Read", "Grep", "Bash"],
                agent_name="reviewer",
            )

        asyncio.create_task(_bg())
        return {"task_id": task.task_id, "label": task.label}

    # ===== critic-agent =====
    @router.post("/critic")
    async def agent_critic(body: _CriticBody):
        root = get_project_root()
        system_prompt = _read_agent_prompt("critic-agent.md", root)
        if not system_prompt:
            raise HTTPException(500, "critic-agent.md 不存在")

        user_input = (
            f"审查以下 200-300 字窗口，看是否有 AI 味。\n\n"
            f"```\n{body.window_text}\n```\n\n"
            f"scene_anchor: {body.scene_anchor}\n"
            f"style_anchor: {body.style_anchor}\n"
            f"chapter_meta: {json.dumps(body.chapter_meta, ensure_ascii=False)}\n"
        )

        task = TASKS.create(kind="agent", label=f"critic ch{body.chapter}")

        async def _bg() -> None:
            await _stream_agent_response(
                task, root, system_prompt, user_input,
                model=body.model,
                allowed_tools=["Read"],  # critic 几乎不需要工具
                max_tokens=4096,
                agent_name="critic",
            )

        asyncio.create_task(_bg())
        return {"task_id": task.task_id, "label": task.label}

    # ===== data-agent =====
    @router.post("/data")
    async def agent_data(body: _AgentBody):
        root = get_project_root()
        system_prompt = _read_agent_prompt("data-agent.md", root)
        if not system_prompt:
            raise HTTPException(500, "data-agent.md 不存在")

        from .services.workflow_status import _find_chapter_md
        draft = _find_chapter_md(root, body.chapter)
        chapter_file = str(draft) if draft else f"正文/第{body.chapter:04d}章-*.md"

        user_input = (
            f"从第 {body.chapter} 章提取结构化数据。\n"
            f"chapter_file: {chapter_file}\n"
            f"project_root: {root}\n"
            f"scripts_dir: {SCRIPTS_DIR}\n"
            f"请输出三份 JSON：fulfillment_result（章节指令完成度）、"
            f"disambiguation_result（实体消歧）、extraction_result（新增事实）。"
        )

        task = TASKS.create(kind="agent", label=f"data-agent ch{body.chapter}")

        async def _bg() -> None:
            await _stream_agent_response(
                task, root, system_prompt, user_input,
                model=body.model,
                allowed_tools=["Read", "Grep", "Bash"],
                agent_name="data",
            )

        asyncio.create_task(_bg())
        return {"task_id": task.task_id, "label": task.label}

    # ===== deconstruction-agent =====
    @router.post("/deconstruction")
    async def agent_deconstruction(body: _DeconstructionBody):
        root = get_project_root()
        system_prompt = _read_agent_prompt("deconstruction-agent.md", root)
        if not system_prompt:
            raise HTTPException(500, "deconstruction-agent.md 不存在")

        user_input = (
            f"拆解参考书：{body.reference_title}\n"
            f"source: {body.source}\n"
            f"text_path: {body.text_path}\n"
            f"analysis_mode: {body.analysis_mode}\n"
            f"target_genre: {body.target_genre}\n"
        )

        task = TASKS.create(kind="agent", label=f"deconstruction {body.reference_title}")

        async def _bg() -> None:
            await _stream_agent_response(
                task, root, system_prompt, user_input,
                model=body.model,
                allowed_tools=["Read", "Grep"],
                max_tokens=16384,
                agent_name="deconstruction",
            )

        asyncio.create_task(_bg())
        return {"task_id": task.task_id, "label": task.label}

    # ===== draft-agent =====
    @router.post("/draft")
    async def agent_draft(body: _DraftBody):
        root = get_project_root()
        task = TASKS.create(kind="agent", label=f"draft ch{body.chapter}")

        async def _bg() -> None:
            try:
                # 局部导入，避免与 workflows.py 的循环引用
                from .workflows._context import _run_context_for_chapter
                from .workflows._draft import _run_draft_for_chapter

                if body.context and body.context.strip():
                    ctx_text = body.context.strip()
                    await TASKS.emit(task, {"phase": "stdout", "line": "使用页面传入的任务书"})
                else:
                    ctx_text = await _run_context_for_chapter(
                        task, project_root=root, chapter=body.chapter, model=body.model,
                    )

                draft_path = await _run_draft_for_chapter(
                    task, project_root=root, chapter=body.chapter,
                    ctx_text=ctx_text, model=body.model,
                )
                await TASKS.emit_done(task, {
                    "chapter": body.chapter,
                    "draft_path": str(draft_path.relative_to(root)),
                })
            except Exception as exc:
                await TASKS.emit_error(task, str(exc), traceback=traceback.format_exc())

        asyncio.create_task(_bg())
        return {"task_id": task.task_id, "label": task.label}

    # ===== polish-agent =====
    @router.post("/polish")
    async def agent_polish(body: _PolishBody):
        root = get_project_root()
        task = TASKS.create(kind="agent", label=f"polish ch{body.chapter}")

        async def _bg() -> None:
            try:
                from .workflows._polish import _run_polish_for_chapter
                polished = await _run_polish_for_chapter(
                    task, project_root=root, chapter=body.chapter, model=body.model,
                )
                await TASKS.emit_done(task, {
                    "chapter": body.chapter,
                    "polished_path": str(polished.relative_to(root)),
                })
            except Exception as exc:
                await TASKS.emit_error(task, str(exc), traceback=traceback.format_exc())

        asyncio.create_task(_bg())
        return {"task_id": task.task_id, "label": task.label}

    return router