"""
审查阶段 —— 从 workflows.py 拆分。
"""
from __future__ import annotations

import asyncio
import json
import shutil
from pathlib import Path

from ..routes.actions import _run_subprocess_streaming, _python
from ..agents.agents import _read_agent_prompt
from ..core.constants import FreedomLevel, DEFAULT_FREEDOM_LEVEL
from ..services.task_manager import TASKS, Task

from ._channel import load_channel, is_opening_chapter
from ._critic import _run_critic_windows
from ._prompt_fragments import resolve_fragment, fill_template
from ._step_runners import _run_step_subprocess, _run_step_agent
from ._utils import _extract_json


async def _run_review_phase(
    task: Task, *, project_root: Path, chapter: int, content_path: Path,
    model: str | None, step_id: str = "review",
    temperature: float | None = None,
    freedom_level: FreedomLevel = DEFAULT_FREEDOM_LEVEL,
    tmp_dir: Path | None = None,
    skip_critic: bool = False,
) -> None:
    """对 content_path 跑 4 轨审查（quant + lint + reviewer + critic）+ audit-merge。

    产出 .ainovel/tmp/ 下的 review_results.json / quant_audit.json /
    lint_audit.json / critic_audit.json / final_audit.json。可复用：先审草稿
    （喂给 polish），润色后再审润色稿（供 commit 判定最终文本）。

    本函数复用时（re-review）必须清除上一轮的 review_results.json，否则 reviewer
    万一没重新落盘，commit 会静默沿用上一轮（草稿）的审查结果，导致判错。
    """
    from ..services.agent_runner import _default_review_model

    await TASKS.emit(task, {"phase": "step", "step": step_id, "status": "running"})

    td = tmp_dir if tmp_dir else project_root / ".ainovel" / "tmp"
    review_results_path = td / "review_results.json"
    final_audit_path = td / "final_audit.json"
    try:
        review_results_path.unlink(missing_ok=True)
    except OSError:
        pass
    try:
        final_audit_path.unlink(missing_ok=True)
    except OSError:
        pass

    content_arg = str(content_path)
    content_text = content_path.read_text(encoding="utf-8")

    async def _track_quant():
        return await _run_subprocess_streaming(
            task, argv=[_python(), "-X", "utf8", "-m", "data_modules.quantitative_audit",
                        "--project-root", str(project_root),
                        "--content-file", content_arg, "--persist"],
            timeout=120.0, parse_stdout_json=False,
            finalize_task=False,
        )

    async def _track_lint():
        return await _run_subprocess_streaming(
            task, argv=[_python(), "-X", "utf8", "-m", "data_modules.deterministic_lint",
                        "--project-root", str(project_root),
                        "--content-file", content_arg, "--persist"],
            timeout=60.0, parse_stdout_json=False,
            finalize_task=False,
        )

    async def _track_reviewer():
        rev_prompt = _read_agent_prompt("reviewer.md", project_root)
        if not rev_prompt:
            return None
        # 审查使用独立模型（与 draft 不同，避免同模型自审盲区）
        review_model = model or _default_review_model()

        # ── 加载通道配置 + 开篇审查注入（Phase 6）──
        channel_profile = load_channel(project_root)
        is_opening = is_opening_chapter(chapter, channel_profile)

        review_injection = ""
        if is_opening:
            review_injection = resolve_fragment(project_root, "review/opening_injection.md")
        # 通道特有审查标准
        channel_review = getattr(channel_profile, 'review_criteria', '') or ''
        if channel_review.strip():
            review_injection += f"\n【题材特有审查标准】\n{channel_review}\n"

        user_input = fill_template(
            resolve_fragment(project_root, "stages/reviewer_user_input.md"),
            chapter=chapter,
            content_path=content_path,
            project_root=project_root,
            review_results_path=review_results_path,
            review_injection=review_injection,
        )
        text = await _run_step_agent(
            task, "reviewer",
            project_root=project_root,
            system_prompt=rev_prompt,
            user_input=user_input,
            model=review_model,
            temperature=temperature,
            allowed_tools=["Read", "Grep", "Bash", "Write", "Edit"],
            operation_type="chapter_draft",
            step_description=f"审查第 {chapter} 章草稿，查找内容问题和AI写作痕迹",
            freedom_level=freedom_level,
            chapter=chapter,
        )
        # 兼容：agent 可能未写文件，尝试从返回文本提取 JSON 落盘
        if not review_results_path.is_file():
            try:
                extracted = _extract_json(text)
                parsed = json.loads(extracted)
                if isinstance(parsed, dict):
                    parsed.setdefault("chapter", chapter)  # 标注章节号，提交时校验
                    review_results_path.parent.mkdir(parents=True, exist_ok=True)
                    review_results_path.write_text(
                        json.dumps(parsed, ensure_ascii=False, indent=2),
                        encoding="utf-8",
                    )
                    await TASKS.emit(task, {
                        "phase": "stdout",
                        "line": "已从 reviewer 输出提取并保存 review_results.json",
                    })
            except Exception as exc:
                await TASKS.emit(task, {
                    "phase": "stderr",
                    "line": f"reviewer 输出提取 JSON 失败: {exc}",
                })
        return text

    tasks_to_run = [_track_quant(), _track_lint()]
    tasks_to_run.append(_track_reviewer())
    if skip_critic:
        await TASKS.emit(task, {"phase": "stdout", "line": "fast 模式：跳过 critic 审查"})
    else:
        tasks_to_run.append(
            _run_critic_windows(
                task, project_root=project_root,
                draft_text=content_text, chapter=chapter,
                model=model, freedom_level=freedom_level,
                tmp_dir=tmp_dir,
            )
        )
    await asyncio.gather(*tasks_to_run, return_exceptions=True)

    # Track 5: audit_merger
    await _run_step_subprocess(
        task, "audit-merge",
        argv=[_python(), "-X", "utf8", "-m", "data_modules.audit_merger",
              "--project-root", str(project_root), "--persist"],
        timeout=30.0,
        operation_type="formatting",
        step_description="合并所有审查结果，生成最终审计报告",
    )

    # 将子进程写入全局 tmp 的输出复制到章节级 tmp_dir
    if tmp_dir and tmp_dir != (project_root / ".ainovel" / "tmp"):
        for fname in ("quant_audit.json", "lint_audit.json", "final_audit.json"):
            src = project_root / ".ainovel" / "tmp" / fname
            dst = tmp_dir / fname
            try:
                if src.is_file():
                    shutil.copy2(src, dst)
            except OSError:
                pass

    await TASKS.emit(task, {"phase": "step", "step": step_id, "status": "done"})
