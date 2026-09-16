"""
write 工作流主编排 —— 从 workflows.py 拆分。
10 步流程：preflight → 清理 → context → 角色脚本 → 起草 → 约束检查 → 审查 → 润色 → 重审 → data-agent → commit → backup
"""
from __future__ import annotations

import asyncio
import json
import os
import time
import traceback
from pathlib import Path

from ..routes.actions import _run_subprocess_streaming, _python
from ..agents.agents import _read_agent_prompt
from ..core.constants import FreedomLevel, DEFAULT_FREEDOM_LEVEL
from ..services.task_manager import TASKS, Task

from ._channel import load_channel
from ._character import _run_character_script
from ._constraints import _run_constraint_check
from ._context import _run_context_for_chapter
from ._draft import _run_draft_for_chapter
from ._prompt_fragments import resolve_fragment, fill_template

from ._review import _run_review_phase
from ._step_runners import _run_step_subprocess, _run_step_agent


async def _run_write_workflow(
    task: Task, *, project_root: Path, chapter: int,
    model: str | None, context: str | None = None,
    temperature: float | None = None,
    freedom_level: FreedomLevel = DEFAULT_FREEDOM_LEVEL,
    fast: bool = False,
    resume: bool = False,
) -> None:
    """write skill 的 6 步流程，复刻自 ainovel-write/SKILL.md。"""
    # 章节级 tmp 目录隔离：防止跨章污染
    tmp_dir = project_root / ".ainovel" / "tmp" / f"chapter_{chapter:04d}"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    global_tmp = project_root / ".ainovel" / "tmp"

    # ── 加载通道配置（Phase 6）──
    channel_profile = load_channel(project_root)
    # 模型解析：调用方 model > channel model_override > 全局默认
    resolved_model = model or channel_profile.model_override

    await TASKS.emit(task, {"phase": "stdout", "line": f"▶ 第 {chapter} 章写章流程启动"})
    try:
        # ===== Step 1: preflight =====
        await _run_step_subprocess(
            task, "preflight",
            argv=[_python(), "-X", "utf8", "ainovel.py",
                  "--project-root", str(project_root), "preflight", "--format", "json"],
            timeout=60.0,
            operation_type="formatting",
            step_description="项目预检查，验证项目结构和配置是否正确",
        )

        # ===== 章纲确认检查 =====
        try:
            state_path = project_root / ".ainovel" / "state.json"
            if state_path.is_file():
                state = json.loads(state_path.read_text(encoding="utf-8"))
                ch_status = state.get("progress", {}).get("chapter_status", {}).get(str(chapter), {})
                stage = ch_status.get("stage", "")
                if stage not in ("outline_confirmed", "finalized", "committed"):
                    await TASKS.emit(task, {
                        "phase": "stdout",
                        "line": (
                            f"⚠ 第 {chapter} 章尚未确认章纲（当前状态: {stage or 'none'}）。"
                            "建议先在章纲页面生成并确认「故事简要」，确保写章按你的意图执行。"
                        ),
                    })
                    # 非阻断警告——允许继续（兼容旧流程），但明确提醒
        except Exception:
            pass

        # ===== 章纲文件存在性检查（非阻断，但明确告知用户缺什么）=====
        try:
            outline_md = project_root / "大纲" / f"第{chapter:04d}章-章纲.md"
            chapter_json = project_root / ".story-system" / "chapters" / f"chapter_{chapter:03d}.json"
            missing = []
            if not outline_md.is_file():
                missing.append(f"大纲/第{chapter:04d}章-章纲.md")
            if not chapter_json.is_file():
                missing.append(f".story-system/chapters/chapter_{chapter:03d}.json")
            if missing:
                await TASKS.emit(task, {
                    "phase": "stdout",
                    "line": (
                        f"⚠ 第 {chapter} 章缺少以下文件: {', '.join(missing)}。"
                        "章纲不全时写章质量会下降（缺少结构化 directive 约束）。"
                        "建议先在章纲页面运行 plan 或 register-chapters。"
                    ),
                })
        except Exception:
            pass

        # ===== Step 1.5: 自动清理当前章节的旧派生数据（切断自我反馈循环）=====
        try:
            from scripts.chapter_reset_service import reset_chapter
            purge_result = reset_chapter(project_root, chapter, purge_directive=False)
            purged_count = sum(
                len(a.get("deleted_chunks", a.get("marked_outdated", a.get("purged", [])) or []))
                for a in purge_result.get("actions", [])
            )
            if purged_count > 0:
                await TASKS.emit(task, {
                    "phase": "stdout",
                    "line": f"已清理第 {chapter} 章旧派生数据（{purged_count} 条），确保干净起点",
                })
        except Exception as exc:
            await TASKS.emit(task, {
                "phase": "stderr",
                "line": f"章节清理失败（不阻断主流程）: {exc}",
            })

        # ===== Step 2: context-agent =====
        if context and context.strip():
            ctx_text = context.strip()
            await TASKS.emit(task, {"phase": "stdout", "line": "使用页面传入的任务书"})
        else:
            ctx_text = await _run_context_for_chapter(
                task, project_root=project_root, chapter=chapter, model=resolved_model,
                temperature=temperature, freedom_level=freedom_level,
            )

        # 将任务书落盘到 大纲/第NNNN章-写作任务书.md
        task_book_path = project_root / "大纲" / f"第{chapter:04d}章-写作任务书.md"
        task_book_path.parent.mkdir(parents=True, exist_ok=True)
        task_book_path.write_text(ctx_text, encoding="utf-8")
        await TASKS.emit(task, {
            "phase": "stdout",
            "line": f"任务书已保存 -> 大纲/第{chapter:04d}章-写作任务书.md",
        })

        # ===== Step 2.5: 角色脚本预生成（Character 模型）=====
        character_script_path = None
        character_script_approved = False
        try:
            character_script_path, character_script_approved = await _run_character_script(
                task, project_root=project_root, chapter=chapter,
                ctx_text=ctx_text, freedom_level=freedom_level,
            )
            if character_script_path and character_script_approved:
                await TASKS.emit(task, {
                    "phase": "stdout",
                    "line": f"用户已确认使用角色脚本 -> {character_script_path.relative_to(project_root)}",
                })
            elif character_script_path:
                await TASKS.emit(task, {
                    "phase": "stdout",
                    "line": "用户选择跳过角色脚本，主力模型自行处理对话",
                })
                character_script_path = None  # 用户跳过时不传路径
        except Exception as exc:
            await TASKS.emit(task, {
                "phase": "stderr",
                "line": f"Step 1.5 角色脚本失败（不阻断主流程）: {exc}",
            })

        # ===== Step 3: 起草（主 LLM，不是 subagent）=====
        draft_path = await _run_draft_for_chapter(
            task, project_root=project_root, chapter=chapter, ctx_text=ctx_text, model=resolved_model,
            temperature=temperature, freedom_level=freedom_level,
            character_script_path=character_script_path if character_script_approved else None,
        )
        if draft_path is None:
            # draft 失败（如返回空），emit_error 已在 _draft.py 中调用，直接返回
            return

        # ===== Step 3.5: 约束检查（Phase 2）=====
        constraint_result = await _run_constraint_check(
            task, project_root=project_root, chapter=chapter,
            content_path=draft_path, freedom_level=freedom_level,
            tmp_dir=tmp_dir,
        )
        if not constraint_result.get("passed", True):
            await TASKS.emit(task, {
                "phase": "stdout",
                "line": (
                    f"⚠ 约束检查发现 {constraint_result.get('hard_count', 0)} 个硬性违规，"
                    f"共 {constraint_result.get('total_violations', 0)} 个违规。"
                    f"详情见 .ainovel/tmp/constraint_audit.json"
                ),
            })

        # ===== Step 4: 审查草稿 =====
        await _run_review_phase(
            task, project_root=project_root, chapter=chapter,
            content_path=draft_path, model=resolved_model, step_id="review",
            temperature=temperature, freedom_level=freedom_level,
            tmp_dir=tmp_dir, skip_critic=fast,
        )

        # ===== Step 5: data-agent + chapter-commit =====
        artifact_paths = {
            "fulfillment_result": tmp_dir / "fulfillment_result.json",
            "disambiguation_result": tmp_dir / "disambiguation_result.json",
            "extraction_result": tmp_dir / "extraction_result.json",
        }
        review_results_path = tmp_dir / "review_results.json"

        # 清除上一章的 data agent artifacts，避免跨章污染
        for _ap in artifact_paths.values():
            try:
                _ap.unlink(missing_ok=True)
            except OSError:
                pass

        data_start = time.time()
        try:
            await _run_step_agent(
                task, "data",
                project_root=project_root,
                system_prompt=_read_agent_prompt("data-agent.md", project_root),
                user_input=fill_template(
                    resolve_fragment(project_root, "stages/data_user_input.md"),
                    chapter=chapter,
                    draft_path=draft_path,
                    project_root=project_root,
                    fulfill=artifact_paths["fulfillment_result"],
                    disambig=artifact_paths["disambiguation_result"],
                    extract=artifact_paths["extraction_result"],
                ),
                model=resolved_model,
                temperature=temperature,
                allowed_tools=["Read", "Grep", "Bash", "Write", "Edit"],
                operation_type="chapter_draft",
                step_description=f"从第 {chapter} 章提取结构化数据，包括情节、人物、设定等",
                freedom_level=freedom_level,
                chapter=chapter,
            )
        except Exception as exc:
            await TASKS.emit(task, {
                "phase": "stderr",
                "line": f"data-agent 失败，将使用默认空 artifacts 继续 commit: {exc}",
            })

        # 兜底：data-agent 常漏写部分文件。检测三份文件是否都被本轮更新
        missing = []
        for key, path in artifact_paths.items():
            try:
                if not path.is_file() or os.path.getmtime(path) < data_start:
                    missing.append(key)
            except OSError:
                missing.append(key)
        if missing:
            await TASKS.emit(task, {
                "phase": "stdout",
                "line": f"data-agent 未生成/未更新: {missing}，二次补写",
            })
            try:
                retry_hint = "、".join(
                    f"{k}->{artifact_paths[k]}" for k in missing
                )
                await _run_step_agent(
                    task, "data-retry",
                    project_root=project_root,
                    system_prompt=_read_agent_prompt("data-agent.md", project_root),
                    user_input=(
                        f"第 {chapter} 章上次提取漏写了 {missing}。chapter_file: {draft_path}\n"
                        f"project_root: {project_root}\n"
                        f"请仅补写这些缺失文件并写入磁盘（{retry_hint}），"
                        "不要重复已生成的文件。按 data-agent.md 的字段要求输出。"
                    ),
                    model=resolved_model,
                    temperature=temperature,
                    allowed_tools=["Read", "Grep", "Bash", "Write", "Edit"],
                    freedom_level=freedom_level,
                    chapter=chapter,
                    stage="data",
                )
            except Exception as exc:
                await TASKS.emit(task, {
                    "phase": "stderr",
                    "line": f"data-retry 失败: {exc}",
                })

        # 确保 commit 所需的四份 JSON 都存在
        tmp_dir.mkdir(parents=True, exist_ok=True)

        # 归一化 review_results.json
        if review_results_path.is_file():
            try:
                raw = review_results_path.read_text(encoding="utf-8")
                review_obj = json.loads(raw) if raw.strip() else {}
                if isinstance(review_obj, dict) and "blocking_count" not in review_obj:
                    issues = review_obj.get("issues")
                    if not isinstance(issues, list):
                        issues = []
                    blocking = sum(
                        1 for it in issues
                        if isinstance(it, dict) and it.get("blocking") is True
                    )
                    review_obj["blocking_count"] = blocking
                    review_obj.setdefault("chapter", chapter)
                    review_results_path.write_text(
                        json.dumps(review_obj, ensure_ascii=False, indent=2),
                        encoding="utf-8",
                    )
                    await TASKS.emit(task, {
                        "phase": "stdout",
                        "line": f"已补全 review_results.blocking_count={blocking}（从 issues 派生）",
                    })
            except Exception as exc:
                await TASKS.emit(task, {
                    "phase": "stderr",
                    "line": f"归一化 review_results.json 失败: {exc}",
                })

        # 检测 data-agent 是否完全失败
        data_agent_failed = all(
            not artifact_paths[k].is_file()
            for k in ("fulfillment_result", "disambiguation_result", "extraction_result")
        )
        if data_agent_failed:
            if getattr(task, "auto_generate", False):
                _da_msg = (
                    "⚠ data-agent 完全失败，3 个 artifact 均未生成。"
                    "自动生成模式：放行阻断，允许自动 ACCEPTED（artifacts 为空默认值）。"
                )
            else:
                _da_msg = (
                    "⚠ data-agent 完全失败，3 个 artifact 均未生成。"
                    "将标记为需人工审核，禁止自动 ACCEPTED。"
                )
            await TASKS.emit(task, {"phase": "stderr", "line": _da_msg})

        defaults = {
            review_results_path: {"blocking_count": 0},
            artifact_paths["fulfillment_result"]: {
                "planned_nodes": [], "covered_nodes": [],
                "missed_nodes": [], "extra_nodes": [],
            },
            artifact_paths["disambiguation_result"]: {"pending": []},
            artifact_paths["extraction_result"]: {
                "accepted_events": [], "state_deltas": [],
                "entity_deltas": [], "entities_appeared": [],
                "scenes": [], "summary_text": "",
            },
        }
        for path, default in defaults.items():
            if not path.is_file():
                if data_agent_failed and path == review_results_path:
                    if getattr(task, "auto_generate", False):
                        # 自动生成模式：data-agent 失败也不阻断，允许自动 ACCEPTED
                        default = {"blocking_count": 0, "_data_agent_failed": True}
                    else:
                        default = {"blocking_count": 1, "_data_agent_failed": True}
                try:
                    path.write_text(
                        json.dumps(default, ensure_ascii=False, indent=2),
                        encoding="utf-8",
                    )
                    await TASKS.emit(task, {
                        "phase": "stdout",
                        "line": f"已生成默认 {path.name}" + ("（data-agent 失败标记）" if data_agent_failed and path == review_results_path else ""),
                    })
                except Exception as exc:
                    await TASKS.emit(task, {
                        "phase": "stderr",
                        "line": f"无法写入 {path.name}: {exc}",
                    })

        await _run_step_subprocess(
            task, "commit",
            argv=[_python(), "-X", "utf8", "ainovel.py",
                  "--project-root", str(project_root),
                  "chapter-commit", "--chapter", str(chapter),
                  "--review-result", str(review_results_path),
                  "--fulfillment-result", str(artifact_paths["fulfillment_result"]),
                  "--disambiguation-result", str(artifact_paths["disambiguation_result"]),
                  "--extraction-result", str(artifact_paths["extraction_result"])],
            timeout=60.0,
            operation_type="plot_adjustment",
            step_description=f"提交第 {chapter} 章到版本库，更新状态和索引",
        )

        # ===== Step 6: Git 备份 =====
        try:
            title = draft_path.stem
            await _run_step_subprocess(
                task, "backup",
                argv=[_python(), "-X", "utf8", "ainovel.py",
                      "--project-root", str(project_root),
                      "backup", "--chapter", str(chapter),
                      "--chapter-title", title],
                timeout=120.0,
                operation_type="formatting",
                step_description=f"备份第 {chapter} 章到Git仓库",
            )
        except Exception as exc:
            await TASKS.emit(task, {
                "phase": "stderr",
                "line": f"Git 备份失败（不影响 commit 结果）: {exc}",
            })

        await TASKS.emit_done(task, {
            "chapter": chapter,
            "draft_path": str(draft_path.relative_to(project_root)),
        })
    except Exception as exc:
        await TASKS.emit_error(task, str(exc), traceback=traceback.format_exc())
