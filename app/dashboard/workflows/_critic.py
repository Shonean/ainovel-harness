"""
critic 并行窗口审查 —— 从 workflows.py 拆分。
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

from ..services.agent_runner import AnthropicAgentRunner, _default_review_model
from ..agents.agents import _read_agent_prompt
from ..core.constants import FreedomLevel, DEFAULT_FREEDOM_LEVEL
from ..services.hard_constraints import build_constrained_prompt
from ..services.task_manager import TASKS, Task

from ._decision import _decision_check
from ._prompt_fragments import resolve_fragment, fill_template


def _slice_windows(text: str, *, size: int = 250, max_windows: int = 12) -> list[str]:
    """把正文切成 ~250 字的窗口，尽量在句末断开。最多 max_windows 个。"""
    windows: list[str] = []
    buf = ""
    for ch in text:
        buf += ch
        if len(buf) >= size and ch in "。！？\n":
            windows.append(buf)
            buf = ""
            if len(windows) >= max_windows:
                return windows
    if buf.strip():
        windows.append(buf)
    return windows[:max_windows]


async def _run_critic_windows(
    task: Task, *, project_root: Path, draft_text: str, chapter: int,
    model: str | None,
    freedom_level: FreedomLevel = DEFAULT_FREEDOM_LEVEL,
    tmp_dir: Path | None = None,
) -> list[dict] | None:
    """对草稿切窗，并行跑 critic-agent（并发上限 4），收集 AI 味发现。"""
    # 决策检查
    step_id = "critic_review"
    if not await _decision_check(
        task,
        step_id,
        operation_type="chapter_draft",
        step_description=f"审查第 {chapter} 章草稿，查找 AI 写作痕迹"
    ):
        return None
    windows = _slice_windows(draft_text)
    system_prompt = _read_agent_prompt("critic-agent.md", project_root)
    # Phase 2: 注入硬约束（critic 同样需要遵守三大定律）
    system_prompt = build_constrained_prompt(system_prompt, freedom_level) if system_prompt else ""
    if not windows or not system_prompt:
        await TASKS.emit(task, {
            "phase": "step", "step": "critic", "status": "skipped",
            "note": "无窗口或 critic-agent.md 缺失",
        })
        return None

    await TASKS.emit(task, {
        "phase": "step", "step": "critic", "status": "running",
        "windows": len(windows),
    })

    sem = asyncio.Semaphore(4)
    findings: list[dict] = []

    async def _one(idx: int, window_text: str) -> None:
        async with sem:
            await TASKS.emit(task, {
                "phase": "stdout", "line": f"[critic] 窗口 {idx + 1}/{len(windows)}",
            })

            async def on_event(ev: dict) -> None:
                ev2 = dict(ev)
                ev2["window"] = idx + 1
                await TASKS.emit(task, ev2)

            async def on_token(_t: str) -> None:
                return None

            runner = AnthropicAgentRunner(
                project_root=project_root,
                model=model or _default_review_model(),
                system_prompt=system_prompt,
                max_tokens=4096,
                allowed_tools=["Read"],
                agent_name="critic",
                on_event=on_event,
                on_token=on_token,
                max_turns=2,
                meta={"stage": "critic", "chapter": chapter},
            )
            user_input = fill_template(
                resolve_fragment(project_root, "stages/critic_user_input.md"),
                chapter=chapter,
                window_index=idx + 1,
                window_count=len(windows),
                window_text=window_text,
            )
            try:
                text = await runner.run(user_input)
                findings.append({"window": idx + 1, "text": text})
            except Exception as exc:
                await TASKS.emit(task, {
                    "phase": "stderr", "line": f"[critic] 窗口 {idx + 1} 失败: {exc}",
                })

    await asyncio.gather(
        *[_one(i, w) for i, w in enumerate(windows)],
        return_exceptions=True,
    )

    # 持久化（供 audit_merger / 后续查阅）
    try:
        td = tmp_dir if tmp_dir else project_root / ".ainovel" / "tmp"
        out = td / "critic_audit.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(
            json.dumps({"chapter": chapter, "windows": findings}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except OSError:
        pass

    await TASKS.emit(task, {
        "phase": "step", "step": "critic", "status": "done",
        "windows": len(findings),
    })
    return findings
