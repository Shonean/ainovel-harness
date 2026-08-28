"""
路由工厂 —— 从 workflows.py 拆分。
create_workflows_router() 组装所有 /api/workflows/* 端点。
"""
from __future__ import annotations

import asyncio
import re
import traceback
from pathlib import Path
from typing import Callable

from fastapi import APIRouter

from ..routes.actions import _run_subprocess_streaming, _python, SCRIPTS_DIR
from ..services.agent_runner import _default_character_model
from ..agents.agents import _read_agent_prompt
from ..core.constants import FreedomLevel, DEFAULT_FREEDOM_LEVEL
from ..services.task_manager import TASKS, Task

from ._bodies import (
    _WriteBody, _ReviewBody, _PlanBody, _InitBody,
    _FinalizeBody, _UnfinalizeBody, _ConfirmPlotBody,
    _ReplanBody, _LearnBody, _QueryBody, _CharacterSkillBody,
)
from ._finalize import (
    start_init_task,
    _run_finalize_workflow,
    _run_unfinalize_workflow,
    _run_replan_workflow,
)
from ._approve_outline import _run_approve_outline_workflow, _run_confirm_outline_workflow
from ._prompt_fragments import resolve_fragment, fill_template
from ._step_runners import _run_step_subprocess, _run_step_agent, _run_skill_agent
from ._write import _run_write_workflow


def create_workflows_router(get_project_root: Callable[[], Path]) -> APIRouter:
    router = APIRouter(prefix="/api/workflows", tags=["workflows"])

    @router.post("/write")
    async def workflow_write(body: _WriteBody):
        root = get_project_root()
        await TASKS.cancel_active_by_mutex("write", reason="被新的 write 任务取代")
        task = TASKS.create(kind="workflow", label=f"write ch{body.chapter}", mutex_key="write", project_root=str(root))
        task.auto_generate = body.auto_generate

        # Phase 2: 解析自由度级别
        fl = DEFAULT_FREEDOM_LEVEL
        if body.freedom_level is not None:
            try:
                fl = FreedomLevel(body.freedom_level)
            except ValueError:
                fl = DEFAULT_FREEDOM_LEVEL

        async def _bg() -> None:
            try:
                await _run_write_workflow(
                    task, project_root=root, chapter=body.chapter,
                    model=body.model,
                    context=body.context,
                    temperature=body.temperature,
                    freedom_level=fl,
                    fast=body.fast,
                    resume=body.resume,
                )
            except Exception as exc:
                await TASKS.emit_error(task, f"write ch{body.chapter} 启动失败: {exc}", traceback=traceback.format_exc())

        asyncio.create_task(_bg())
        return {"task_id": task.task_id, "label": task.label}

    # ===== approve-outline workflow =====
    @router.post("/approve-outline")
    async def workflow_approve_outline(body: _WriteBody):
        """从章纲生成故事简要，供用户审核编辑。"""
        root = get_project_root()
        await TASKS.cancel_active_by_mutex("approve-outline", reason="被新的 approve-outline 任务取代")
        task = TASKS.create(kind="workflow", label=f"approve-outline ch{body.chapter}", mutex_key="approve-outline", project_root=str(root))

        async def _bg() -> None:
            try:
                brief = await _run_approve_outline_workflow(
                    task, project_root=root, chapter=body.chapter,
                    model=body.model,
                )
                await TASKS.emit_done(task, {"chapter": body.chapter, "brief": brief})
            except Exception as exc:
                await TASKS.emit_error(task, str(exc), traceback=traceback.format_exc())

        asyncio.create_task(_bg())
        return {"task_id": task.task_id, "label": task.label}

    @router.post("/confirm-outline")
    async def workflow_confirm_outline(body: _WriteBody):
        """用户确认故事简要，更新 chapter_NNN.json 并推进状态。"""
        root = get_project_root()
        await TASKS.cancel_active_by_mutex("confirm-outline", reason="被新的 confirm-outline 任务取代")
        task = TASKS.create(kind="workflow", label=f"confirm-outline ch{body.chapter}", mutex_key="confirm-outline", project_root=str(root))

        brief_text = body.context or ""
        if not brief_text.strip():
            await TASKS.emit_error(task, "故事简要文本不能为空")
            return {"task_id": task.task_id, "label": task.label, "error": "brief_text is required"}

        async def _bg() -> None:
            try:
                await _run_confirm_outline_workflow(
                    task, project_root=root, chapter=body.chapter,
                    brief_text=brief_text,
                )
                await TASKS.emit_done(task, {"chapter": body.chapter, "status": "outline_confirmed"})
            except Exception as exc:
                await TASKS.emit_error(task, str(exc), traceback=traceback.format_exc())

        asyncio.create_task(_bg())
        return {"task_id": task.task_id, "label": task.label}

    @router.post("/character-rewrite")
    async def workflow_character_rewrite(body: _CharacterSkillBody):
        """用人物模型按需重写指定章节的对话和情绪段落（不覆盖原稿）。"""
        root = get_project_root()
        label = f"character-rewrite ch{body.target}" if body.target else "character-rewrite"
        await TASKS.cancel_active_by_mutex("character-rewrite", reason="被新的人物重写任务取代")
        task = TASKS.create(kind="workflow", label=label, mutex_key="character-rewrite", project_root=str(root))

        async def _bg() -> None:
            focus = body.context or "全部"
            await _run_skill_agent(
                task, project_root=root, skill_name="ainovel-character-rewrite",
                user_input=(
                    f"重写第 {body.target} 章的角色对话和情绪段落，focus={focus}。PROJECT_ROOT={root}。\n"
                    "按 SKILL.md 执行：定位章节文件 → 加载角色设定 → 逐段诊断重写 → 落盘 AI生成/第NNNN章-人物重写.md。"
                ),
                model=body.model or _default_character_model(), agent_name="character-rewrite", max_turns=50,
                operation_type="character_rewrite",
                step_description=f"重写第{body.target}章角色对话（focus={focus}）",
            )

        asyncio.create_task(_bg())
        return {"task_id": task.task_id, "label": task.label}

    @router.post("/review")
    async def workflow_review(body: _ReviewBody):
        """独立审查：只跑 4 轨 + audit-merge，不起草不 commit。"""
        root = get_project_root()
        await TASKS.cancel_active_by_mutex("review", reason="被新的 review 任务取代")
        task = TASKS.create(kind="workflow", label=f"review ch{body.chapter}", mutex_key="review", project_root=str(root))

        async def _bg() -> None:
            try:
                from ..services.workflow_status import _find_chapter_md
                draft = _find_chapter_md(root, body.chapter)
                if not draft:
                    await TASKS.emit_error(task, f"第 {body.chapter} 章尚未起草")
                    return
                await _run_step_subprocess(
                    task, "lint",
                    argv=[_python(), "-X", "utf8", "-m", "data_modules.deterministic_lint",
                          "--project-root", str(root), "--chapter", str(body.chapter), "--persist"],
                    timeout=60.0,
                )
                await _run_step_subprocess(
                    task, "quant",
                    argv=[_python(), "-X", "utf8", "-m", "data_modules.quantitative_audit",
                          "--project-root", str(root), "--chapter", str(body.chapter), "--persist"],
                    timeout=120.0,
                )
                rev_prompt = _read_agent_prompt("reviewer.md", root)
                if rev_prompt:
                    await _run_step_agent(
                        task, "reviewer",
                        project_root=root,
                        system_prompt=rev_prompt,
                        user_input=(
                            f"审查第 {body.chapter} 章。chapter_file: {draft}\n"
                            f"project_root: {root}\n输出 issues JSON。"
                        ),
                        model=body.model,
                        operation_type="chapter_review",
                        step_description=f"审查第{body.chapter}章",
                    )
                await _run_step_subprocess(
                    task, "merge",
                    argv=[_python(), "-X", "utf8", "-m", "data_modules.audit_merger",
                          "--project-root", str(root), "--persist"],
                    timeout=30.0,
                )
                await TASKS.emit_done(task, {"chapter": body.chapter})
            except Exception as exc:
                await TASKS.emit_error(task, str(exc), traceback=traceback.format_exc())

        asyncio.create_task(_bg())
        return {"task_id": task.task_id, "label": task.label}

    # ===== plan workflow =====
    @router.post("/plan")
    async def workflow_plan(body: _PlanBody):
        root = get_project_root()
        await TASKS.cancel_active_by_mutex("plan", reason="被新的 plan 任务取代")
        task = TASKS.create(kind="workflow", label=f"plan v{body.volume}", mutex_key="plan", project_root=str(root))
        task.auto_generate = body.auto_generate

        async def _bg() -> None:
            start = body.start_chapter or 1
            end = start + body.chapter_count - 1
            temp_hint = ""
            if body.temperature is not None and body.temperature >= 0.5:
                temp_hint = (
                    "\n\n[随机性指令] 本次 temperature={:.2f}，属于'重新设计'模式。"
                    "若发现已有旧章纲/旧合同/旧摘要，必须刻意重新设计："
                    "爽点顺序不得与旧版相同、CBN/CPNs/CEN 节点内容不得复用旧版、"
                    "章末钩子形式必须不同，逐段推进的具体事件必须与旧版有显著差异。"
                    "但在用户输入了'严格继承'指令时保留旧版逻辑。"
                ).format(body.temperature)
            plan_user_input = fill_template(
                resolve_fragment(root, "stages/plan_user_input.md"),
                volume=body.volume,
                start=start,
                end=end,
                chapter_count=body.chapter_count,
                root=root,
            ) + temp_hint
            await _run_skill_agent(
                task, project_root=root, skill_name="ainovel-plan",
                user_input=plan_user_input,
                model=body.model, agent_name="plan", max_turns=120,
                temperature=body.temperature,
                finalize_task=False,  # 等章纲审阅后再 emit_done
                meta={"stage": "plan", "batch": f"{start}-{end}"},
            )

            # Post-plan：自动注册章节 JSON
            await TASKS.emit(task, {"phase": "step", "step": "register-chapters", "status": "running"})
            await TASKS.emit(task, {"phase": "stdout", "line": f"正在从章纲注册 {start}-{end} 章 JSON directive..."})
            try:
                reg_res = await _run_subprocess_streaming(
                    task,
                    argv=[_python(), "-X", "utf8", str(SCRIPTS_DIR / "register_chapters.py"),
                          "--project-root", str(root),
                          "--start", str(start), "--end", str(end)],
                    timeout=120.0, parse_stdout_json=False, finalize_task=False,
                )
                if reg_res.get("ok"):
                    await TASKS.emit(task, {"phase": "step", "step": "register-chapters", "status": "done"})
                else:
                    await TASKS.emit(task, {
                        "phase": "stderr",
                        "line": (
                            f"章节 JSON 注册失败（不阻断 plan 结果）: 退出码 "
                            f"{reg_res.get('returncode')}；"
                            f"{(reg_res.get('stderr') or '')[-300:]}"
                        ),
                    })
                    await TASKS.emit(task, {"phase": "step", "step": "register-chapters", "status": "failed"})
            except Exception as exc:
                await TASKS.emit(task, {
                    "phase": "stderr",
                    "line": f"章节 JSON 注册失败（不阻断 plan 结果）: {exc}",
                })
                await TASKS.emit(task, {"phase": "step", "step": "register-chapters", "status": "failed"})

            # Post-plan：章纲审阅弹窗（正常和 auto_generate 模式都弹）
            try:
                await TASKS.emit(task, {"phase": "step", "step": "outline-review", "status": "running"})
                chapters_dir = root / ".story-system" / "chapters"
                outlines_dir = root / "大纲"
                outline_texts = []
                for ch in range(start, end + 1):
                    content = ""
                    # 优先读 .md 章纲（plan agent 直接产出）
                    md_path = outlines_dir / f"第{ch:04d}章-章纲.md"
                    if md_path.is_file():
                        content = md_path.read_text(encoding="utf-8")
                    else:
                        # 回退：从 chapter JSON directive 重建可读文本
                        json_path = chapters_dir / f"chapter_{ch:03d}.json"
                        if json_path.is_file():
                            try:
                                directive = json.loads(json_path.read_text(encoding="utf-8"))
                                cd = directive.get("chapter_directive", {}) if isinstance(directive, dict) else {}
                                lines = [f"# 第{ch:04d}章 章纲（从 JSON 重建）", ""]
                                for field, label in [
                                    ("goal", "目标"), ("obstacles", "阻力"), ("cost", "代价"),
                                    ("strand", "Strand"), ("antagonist_tier", "反派层级"),
                                ]:
                                    val = cd.get(field, "")
                                    if val:
                                        lines.append(f"- {label}：{val}")
                                key_entities = cd.get("key_entities") or []
                                if key_entities:
                                    lines.append(f"- 关键实体：{', '.join(str(e) for e in key_entities)}")
                                # 事件节点
                                cbn = cd.get("cbn") or {}
                                cpns = cd.get("cpns") or []
                                cen = cd.get("cen") or {}
                                if cbn:
                                    lines.append(f"\n## CBN\n- {cbn.get('subject','')} | {cbn.get('action','')} | {cbn.get('object','')}")
                                for i, cpn in enumerate(cpns[:6], 1):
                                    lines.append(f"## CPN{i}\n- {cpn.get('subject','')} | {cpn.get('action','')} | {cpn.get('object','')}")
                                if cen:
                                    lines.append(f"## CEN\n- {cen.get('subject','')} | {cen.get('action','')} | {cen.get('object','')}")
                                # 逐段推进
                                beats = cd.get("paragraph_beats") or []
                                if beats:
                                    lines.append(f"\n## 逐段推进（{len(beats)} 段）")
                                    for b in beats:
                                        if isinstance(b, str):
                                            lines.append(f"- {b}")
                                        elif isinstance(b, dict):
                                            lines.append(f"- {b.get('core_event', b.get('text', str(b)))}")
                                # 硬约束
                                must_cover = cd.get("must_cover") or []
                                forbidden = cd.get("forbidden") or []
                                if must_cover or forbidden:
                                    lines.append("\n## 硬约束")
                                    for mc in must_cover:
                                        lines.append(f"- 必须覆盖：{mc}")
                                    for fb in forbidden:
                                        lines.append(f"- 本章禁区：{fb}")
                                content = "\n".join(lines)
                            except (json.JSONDecodeError, KeyError):
                                pass
                    if content:
                        outline_texts.append(f"===== 第{ch:04d}章-章纲.md =====\n{content}")
                    else:
                        outline_texts.append(f"===== 第{ch:04d}章-章纲.md =====\n# 第{ch:04d}章\n（章纲文件未生成，请手动输入章纲内容）\n")
                if outline_texts:
                    combined = "\n\n".join(outline_texts)
                    await TASKS.emit(task, {
                        "phase": "stdout",
                        "line": f"已读取 {len(outline_texts)} 个章纲文件，等待用户审阅",
                    })
                    # 直接调用 TASKS.suspend_for_input（不经过 make_suspend_fn，auto_generate 也要弹）
                    answer = await TASKS.suspend_for_input(task, {
                        "type": "outline_review",
                        "chapter_range": f"{start}-{end}",
                        "outline_text": combined,
                        "hint": f"第 {start}-{end} 章章纲（共 {len(outline_texts)} 章）。你可以直接修改下方内容，保存后继续。",
                    })
                    edited_text = ""
                    if isinstance(answer, dict):
                        edited_text = str(answer.get("edited_text") or answer.get("answer") or "")
                    elif isinstance(answer, str):
                        edited_text = answer
                    if edited_text.strip():
                        # 按分隔符拆回各章文件
                        import re
                        parts = re.split(r"===== (第\d{4}章-章纲\.md) =====\n", edited_text)
                        # parts = [prefix, ch1_name, ch1_content, ch2_name, ch2_content, ...]
                        saved = 0
                        for i in range(1, len(parts) - 1, 2):
                            ch_name = parts[i]
                            ch_content = parts[i + 1].strip()
                            if ch_content:
                                ch_path = outlines_dir / ch_name
                                ch_path.write_text(ch_content, encoding="utf-8")
                                saved += 1
                        await TASKS.emit(task, {
                            "phase": "stdout",
                            "line": f"已保存用户编辑：{saved} 个章纲文件",
                        })
                        # 重新注册（因为内容可能变了）
                        await TASKS.emit(task, {"phase": "stdout", "line": "章纲已修改，重新注册章节 JSON..."})
                        try:
                            reg_res2 = await _run_subprocess_streaming(
                                task,
                                argv=[_python(), "-X", "utf8", str(SCRIPTS_DIR / "register_chapters.py"),
                                      "--project-root", str(root),
                                      "--start", str(start), "--end", str(end)],
                                timeout=120.0, parse_stdout_json=False, finalize_task=False,
                            )
                            if reg_res2.get("ok"):
                                await TASKS.emit(task, {"phase": "step", "step": "register-chapters", "status": "done"})
                        except Exception:
                            pass
                    await TASKS.emit(task, {"phase": "step", "step": "outline-review", "status": "done"})
                else:
                    await TASKS.emit(task, {"phase": "step", "step": "outline-review", "status": "done"})
            except Exception as exc:
                await TASKS.emit(task, {
                    "phase": "stderr",
                    "line": f"章纲审阅弹窗失败（不阻断 plan 结果）: {exc}",
                })

            # Post-plan 完整性检查：确保所有章节都有章纲.md 和 chapter JSON
            await TASKS.emit(task, {"phase": "step", "step": "completeness-check", "status": "running"})
            missing_outlines = []
            missing_jsons = []
            for ch in range(start, end + 1):
                md_path = outlines_dir / f"第{ch:04d}章-章纲.md"
                json_path = chapters_dir / f"chapter_{ch:03d}.json"
                if not md_path.is_file():
                    missing_outlines.append(ch)
                if not json_path.is_file():
                    missing_jsons.append(ch)
            if missing_outlines:
                await TASKS.emit(task, {
                    "phase": "stderr",
                    "line": f"⚠ 以下章节缺少章纲.md: {missing_outlines}。尝试从卷级详细大纲拆分...",
                })
                # 尝试从卷级详细大纲自动拆分缺失章纲
                from scripts.chapter_outline_loader import load_chapter_outline
                for ch in missing_outlines:
                    outline_content = load_chapter_outline(root, ch, max_chars=None)
                    if outline_content and not outline_content.startswith("⚠️"):
                        md_out = outlines_dir / f"第{ch:04d}章-章纲.md"
                        md_out.write_text(outline_content, encoding="utf-8")
                        await TASKS.emit(task, {
                            "phase": "stdout",
                            "line": f"✓ 已从卷级大纲拆分第{ch:04d}章-章纲.md",
                        })
                    else:
                        await TASKS.emit(task, {
                            "phase": "stderr",
                            "line": f"✗ 无法提取第 {ch} 章内容（{outline_content}），请手动补全",
                        })
            else:
                await TASKS.emit(task, {
                    "phase": "stderr",
                    "line": "卷级详细大纲也不存在，无法自动补全。请在章纲页面手动生成。",
                })
            if missing_jsons:
                await TASKS.emit(task, {
                    "phase": "stdout",
                    "line": f"补注册缺失的章节 JSON: {missing_jsons}",
                })
                for ch in missing_jsons:
                    try:
                        reg_single = await _run_subprocess_streaming(
                            task,
                            argv=[_python(), "-X", "utf8", str(SCRIPTS_DIR / "register_chapters.py"),
                                  "--project-root", str(root),
                                  "--start", str(ch), "--end", str(ch)],
                            timeout=30.0, parse_stdout_json=False, finalize_task=False,
                        )
                    except Exception:
                        pass
            await TASKS.emit(task, {
                "phase": "step", "step": "completeness-check", "status": "done",
                "missing_outlines": missing_outlines,
                "missing_jsons": missing_jsons,
            })

            # plan 全部完成（含章纲审阅），现在才 emit_done
            await TASKS.emit_done(task, {"skill": "ainovel-plan", "chapters": f"{start}-{end}"})

        asyncio.create_task(_bg())
        return {"task_id": task.task_id, "label": task.label}

    # ===== init workflow =====
    @router.post("/init")
    async def workflow_init(body: _InitBody):
        root = get_project_root()
        task = await start_init_task(root, body)
        return {"task_id": task.task_id, "label": task.label}

    # ===== finalize workflow =====
    @router.post("/finalize")
    async def workflow_finalize(body: _FinalizeBody):
        root = get_project_root()
        await TASKS.cancel_active_by_mutex("finalize", reason="被新的 finalize 任务取代")
        task = TASKS.create(kind="workflow", label=f"finalize ch{body.chapter}", mutex_key="finalize", project_root=str(root))

        async def _bg() -> None:
            await _run_finalize_workflow(
                task, project_root=root, chapter=body.chapter,
                final_path=body.final_path, model=body.model,
            )

        asyncio.create_task(_bg())
        return {"task_id": task.task_id, "label": task.label}

    # ===== unfinalize workflow =====
    @router.post("/unfinalize")
    async def workflow_unfinalize(body: _UnfinalizeBody):
        root = get_project_root()
        await TASKS.cancel_active_by_mutex("unfinalize", reason="被新的 unfinalize 任务取代")
        task = TASKS.create(kind="workflow", label=f"unfinalize ch{body.chapter}", mutex_key="unfinalize", project_root=str(root))

        async def _bg() -> None:
            await _run_unfinalize_workflow(
                task, project_root=root, chapter=body.chapter,
            )

        asyncio.create_task(_bg())
        return {"task_id": task.task_id, "label": task.label}

    # ===== confirm-plot workflow =====
    @router.post("/confirm-plot")
    async def workflow_confirm_plot(body: _ConfirmPlotBody):
        root = get_project_root()
        await TASKS.cancel_active_by_mutex("confirm-plot", reason="被新的 confirm-plot 任务取代")
        task = TASKS.create(kind="workflow", label=f"confirm-plot ch{body.chapter}", mutex_key="confirm-plot", project_root=str(root))

        async def _bg() -> None:
            await _run_skill_agent(
                task, project_root=root, skill_name="ainovel-confirm-plot",
                user_input=(
                    f"固化第 {body.chapter} 章的确认剧情。PROJECT_ROOT={root}。\n"
                    "按 SKILL.md：从当前会话讨论中提炼用户明确采纳的剧情方向（排除被否/探索性发言），"
                    "读总纲校验不冲突，写入 大纲/第{NNNN}章-确认剧情.md（4 位零填充）。"
                    "信息不足时调 AskUser 补全本章五要素。"
                ),
                model=body.model, agent_name="confirm-plot", max_turns=40,
            )

        asyncio.create_task(_bg())
        return {"task_id": task.task_id, "label": task.label}

    # ===== replan workflow =====
    @router.post("/replan")
    async def workflow_replan(body: _ReplanBody):
        root = get_project_root()
        await TASKS.cancel_active_by_mutex("replan", reason="被新的 replan 任务取代")
        task = TASKS.create(kind="workflow", label=f"replan ch{body.chapter}", mutex_key="replan", project_root=str(root))

        async def _bg() -> None:
            await _run_replan_workflow(
                task, project_root=root, chapter=body.chapter, model=body.model,
            )

        asyncio.create_task(_bg())
        return {"task_id": task.task_id, "label": task.label}

    # ===== learn workflow =====
    @router.post("/learn")
    async def workflow_learn(body: _LearnBody):
        root = get_project_root()
        await TASKS.cancel_active_by_mutex("learn", reason="被新的 learn 任务取代")
        task = TASKS.create(kind="workflow", label="learn", mutex_key="learn", project_root=str(root))

        async def _bg() -> None:
            desc = body.description.strip()
            desc_note = f"\n用户指定的模式描述：{desc}" if desc else ""
            await _run_skill_agent(
                task, project_root=root, skill_name="ainovel-learn",
                user_input=(
                    f"从当前会话提取成功模式并写入 project_memory.json。PROJECT_ROOT={root}.{desc_note}\n"
                    "按 SKILL.md 执行。"
                ),
                model=body.model, agent_name="learn", max_turns=30,
            )

        asyncio.create_task(_bg())
        return {"task_id": task.task_id, "label": task.label}

    # ===== query workflow =====
    @router.post("/query")
    async def workflow_query(body: _QueryBody):
        root = get_project_root()
        await TASKS.cancel_active_by_mutex("query", reason="被新的 query 任务取代")
        task = TASKS.create(kind="workflow", label="query", mutex_key="query", project_root=str(root))

        async def _bg() -> None:
            await _run_skill_agent(
                task, project_root=root, skill_name="ainovel-query",
                user_input=(
                    f"PROJECT_ROOT={root}。\n用户查询：{body.question}\n"
                    "按 SKILL.md 查询项目设定/角色/力量体系/势力/伏笔/金手指等并回答。"
                ),
                model=body.model, agent_name="query", max_turns=30,
            )

        asyncio.create_task(_bg())
        return {"task_id": task.task_id, "label": task.label}

    # ===== 角色工具：4 个独立角色 skill（使用人物模型）=====

    @router.post("/character-bio")
    async def workflow_character_bio(body: _CharacterSkillBody):
        """用人物模型为指定角色生成详细人物小传。"""
        root = get_project_root()
        label = f"character-bio {body.target}" if body.target else "character-bio"
        await TASKS.cancel_active_by_mutex("character-bio", reason="被新的 character-bio 任务取代")
        task = TASKS.create(kind="workflow", label=label, mutex_key="character-bio", project_root=str(root))

        async def _bg() -> None:
            ctx = f"\n场景/上下文：{body.context}" if body.context else ""
            await _run_skill_agent(
                task, project_root=root, skill_name="ainovel-character-bio",
                user_input=(
                    f"为角色「{body.target}」生成人物小传。PROJECT_ROOT={root}。{ctx}\n"
                    "按 SKILL.md 执行：加载已有设定 → 生成完整小传 → 落盘 设定集/{角色}-小传.md。"
                ),
                model=body.model or _default_character_model(), agent_name="character-bio", max_turns=40,
                operation_type="character_bio",
                step_description=f"为「{body.target}」生成人物小传",
            )

        asyncio.create_task(_bg())
        return {"task_id": task.task_id, "label": task.label}

    @router.post("/character-subplot")
    async def workflow_character_subplot(body: _CharacterSkillBody):
        """用人物模型为配角设计独立支线大纲。"""
        root = get_project_root()
        label = f"character-subplot {body.target}" if body.target else "character-subplot"
        await TASKS.cancel_active_by_mutex("character-subplot", reason="被新的 character-subplot 任务取代")
        task = TASKS.create(kind="workflow", label=label, mutex_key="character-subplot", project_root=str(root))

        async def _bg() -> None:
            ctx = f"\n场景/上下文：{body.context}" if body.context else ""
            await _run_skill_agent(
                task, project_root=root, skill_name="ainovel-character-subplot",
                user_input=(
                    f"为配角「{body.target}」设计支线大纲。PROJECT_ROOT={root}。{ctx}\n"
                    "按 SKILL.md 执行：加载角色和主线上下文 → 设计支线 → 落盘 大纲/配角支线-{角色}.md。"
                ),
                model=body.model or _default_character_model(), agent_name="character-subplot", max_turns=50,
                operation_type="character_subplot",
                step_description=f"为「{body.target}」设计配角支线",
            )

        asyncio.create_task(_bg())
        return {"task_id": task.task_id, "label": task.label}

    @router.post("/side-story")
    async def workflow_side_story(body: _CharacterSkillBody):
        """用人物模型生成独立番外短篇。"""
        root = get_project_root()
        label = f"side-story {body.target}" if body.target else "side-story"
        await TASKS.cancel_active_by_mutex("side-story", reason="被新的 side-story 任务取代")
        task = TASKS.create(kind="workflow", label=label, mutex_key="side-story", project_root=str(root))

        async def _bg() -> None:
            ctx = f"\n类型/上下文：{body.context}" if body.context else ""
            await _run_skill_agent(
                task, project_root=root, skill_name="ainovel-side-story",
                user_input=(
                    f"生成番外「{body.target}」。PROJECT_ROOT={root}。{ctx}\n"
                    "按 SKILL.md 执行：加载角色和世界观 → 生成番外短篇 → 落盘 番外/{标题}.md。"
                ),
                model=body.model or _default_character_model(), agent_name="side-story", max_turns=50,
                operation_type="side_story",
                step_description=f"生成番外「{body.target}」",
            )

        asyncio.create_task(_bg())
        return {"task_id": task.task_id, "label": task.label}

    @router.post("/character-lines")
    async def workflow_character_lines(body: _CharacterSkillBody):
        """用人物模型为指定角色批量生成台词库。"""
        root = get_project_root()
        label = f"character-lines {body.target}" if body.target else "character-lines"
        await TASKS.cancel_active_by_mutex("character-lines", reason="被新的 character-lines 任务取代")
        task = TASKS.create(kind="workflow", label=label, mutex_key="character-lines", project_root=str(root))

        async def _bg() -> None:
            scene = body.context or "通用"
            await _run_skill_agent(
                task, project_root=root, skill_name="ainovel-character-lines",
                user_input=(
                    f"为角色「{body.target}」生成台词库，场景：{scene}。PROJECT_ROOT={root}。\n"
                    "按 SKILL.md 执行：加载角色声线约束 → 按场景分类生成台词 → 落盘 AI生成/台词库-{角色}-{场景}.md。"
                ),
                model=body.model or _default_character_model(), agent_name="character-lines", max_turns=40,
                operation_type="character_lines",
                step_description=f"为「{body.target}」生成台词库（{scene}）",
            )

        asyncio.create_task(_bg())
        return {"task_id": task.task_id, "label": task.label}

    return router
