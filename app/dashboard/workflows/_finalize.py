"""
finalize / unfinalize / replan 工作流 + init 入口 —— 从 workflows.py 拆分。
"""
from __future__ import annotations

import json
import re
import sqlite3
import traceback
from datetime import datetime
from pathlib import Path

from ..routes.actions import _python, SCRIPTS_DIR, PLUGIN_ROOT
from ..services.agent_runner import _default_pro_model
from ..agents.agents import _read_agent_prompt
from ..core.constants import FreedomLevel, DEFAULT_FREEDOM_LEVEL
from ..services.task_manager import TASKS, Task
from ..services.workflow_status import _find_chapter_md

from ._decision import _decision_check
from ._step_runners import _run_step_subprocess, _run_step_agent, _run_skill_agent
from ._bodies import _InitBody


async def start_init_task(
    project_root: Path,
    body: _InitBody,
    *,
    label: str = "init",
) -> Task:
    """启动一个 init skill agent 任务，返回 Task（不绑定到具体 router 的 get_project_root）。

    供 /api/workflows/init 与 /api/project/create 复用。
    """
    import asyncio

    await TASKS.cancel_active_by_mutex("init", reason="被新的 init 任务取代")
    task = TASKS.create(kind="workflow", label=label, mutex_key="init", project_root=str(project_root))

    brief_str = json.dumps(body.brief, ensure_ascii=False, indent=2)
    ref_note = ""
    if body.reference_text_path:
        ref_note = (
            f"\n参考书文本路径: {body.reference_text_path}\n"
            "用 Read 读取该文件，按 deconstruction-agent 的输出格式自行拆解，"
            "结果作为灵感来源（不得原样复制其角色/设定/剧情）。"
        )

    async def _bg() -> None:
        await _run_skill_agent(
            task, project_root=project_root, skill_name="ainovel-init",
            user_input=(
                f"对当前项目做深度初始化。PROJECT_ROOT={project_root}（已是目标项目根，"
                f"不要再 slug 化建子目录，直接在此目录生成 state.json/设定集/大纲/总纲/"
                f"idea_bank）。\n\n用户已提供的创作 brief（未填的字段用 AskUser 补问，"
                f"已填的不要重复问）：\n```json\n{brief_str}\n```{ref_note}\n\n"
                f"按 SKILL.md 充分性闸门收齐信息后执行 init_project.py 与 story-system。"
            ),
            model=body.model, agent_name="init", max_turns=60,
        )

    asyncio.create_task(_bg())
    return task


def _mark_finalized(project_root: Path, chapter: int) -> None:
    state_path = project_root / ".ainovel" / "state.json"
    state = {}
    if state_path.is_file():
        try:
            state = json.loads(state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            state = {}
    cf = state.setdefault("chapters_finalized", {})
    cf[str(chapter)] = {
        "finalized": True,
        "finalized_at": datetime.now().isoformat(timespec="seconds"),
    }
    state_path.write_text(json.dumps(state, ensure_ascii=False, indent=2),
                          encoding="utf-8")


async def _run_finalize_workflow(
    task: Task, *, project_root: Path, chapter: int, final_path: str | None,
    model: str | None,
) -> None:
    """finalize 入库链（精简版）：整理正文 -> 风格库强制高分入库 -> 标记 finalized。

    旧的 diff/LLM分类/user_preferences/自炼参考库重拆 链路产物无人消费，已移除。
    """
    import shutil

    try:
        # 1. 定位精修稿：优先 final_path，其次 正文/第NNNN章-精修.md，再退到当前工作稿
        if final_path:
            final_md = Path(final_path)
            if not final_md.is_file():
                # 允许传相对路径
                final_md = project_root / final_path
        else:
            body_dir = project_root / "正文"
            final_md = body_dir / f"第{chapter:04d}章-精修.md"
            if not final_md.is_file():
                final_md = body_dir / f"第{chapter}章-精修.md"
            if not final_md.is_file():
                final_md = _find_chapter_md(project_root, chapter)
        if not final_md or not final_md.is_file():
            await TASKS.emit_error(task, f"找不到正文 {final_path or '第' + str(chapter) + '章-精修.md'}")
            return

        # 2. 把工作稿移动到正文目录并命名为 -精修.md（finalize 后的唯一正文位置）
        if not await _decision_check(
            task,
            "copy",
            operation_type="plot_adjustment",
            step_description=f"将第 {chapter} 章精修稿移动到正文目录并标准化命名",
        ):
            return

        body_dir = project_root / "正文"
        body_dir.mkdir(exist_ok=True)
        polished_md = body_dir / f"第{chapter:04d}章-精修.md"
        try:
            if final_md.resolve() != polished_md.resolve():
                shutil.move(str(final_md), str(polished_md))
                await TASKS.emit(task, {"phase": "stdout",
                                        "line": f"正文已整理: {final_md.name} -> {polished_md.name}"})
        except Exception as exc:
            await TASKS.emit(task, {"phase": "stderr",
                                    "line": f"正文整理失败: {exc}"})
        await TASKS.emit(task, {"phase": "step", "step": "copy", "status": "done"})

        # 3. 入风格样本库（强制 score=95，让本书高分片段优先被检索）
        commit_json = (project_root / ".story-system" / "commits"
                       / f"chapter_{chapter:03d}.commit.json")
        scenes_arg = "[]"
        if commit_json.is_file():
            try:
                cj = json.loads(commit_json.read_text(encoding="utf-8"))
                scenes = (cj.get("extraction_result") or {}).get("scenes", [])
                scenes_arg = json.dumps(scenes, ensure_ascii=False)
            except Exception as exc:
                await TASKS.emit(task, {"phase": "stderr",
                                        "line": f"读取 commit json 失败，改用正文提取: {exc}"})

        await _run_step_subprocess(
            task, "style-extract",
            argv=[_python(), "-X", "utf8", "-m", "data_modules.style_sampler",
                  "--project-root", str(project_root),
                  "extract", "--chapter", str(chapter),
                  "--score", "95", "--scenes", scenes_arg],
            timeout=60.0,
            operation_type="formatting",
            step_description=f"将第 {chapter} 章的优秀片段提取到风格样本库，强制打95分",
        )

        # 4. 标记 finalized 到 state.json
        if not await _decision_check(
            task,
            "mark-state",
            operation_type="plot_adjustment",
            step_description=f"将第 {chapter} 章标记为已完成（finalized），锁定后续修改",
        ):
            return

        _mark_finalized(project_root, chapter)
        await TASKS.emit(task, {"phase": "step", "step": "mark-state", "status": "done"})

        await TASKS.emit_done(task, {"chapter": chapter})
    except Exception as exc:
        await TASKS.emit_error(task, str(exc), traceback=traceback.format_exc())


async def _run_unfinalize_workflow(
    task: Task, *, project_root: Path, chapter: int,
) -> None:
    try:
        # 1. 验证已 finalized
        state_path = project_root / ".ainovel" / "state.json"
        if not state_path.is_file():
            await TASKS.emit_error(task, "state.json 不存在")
            return
        state = json.loads(state_path.read_text(encoding="utf-8"))
        cf = state.get("chapters_finalized") or {}
        entry = cf.get(str(chapter))
        if not (isinstance(entry, dict) and entry.get("finalized")):
            await TASKS.emit_error(task, f"第 {chapter} 章未 finalize，无需撤销")
            return

        # 2. 确认
        ans = await TASKS.suspend_for_input(task, {
            "question": f"确认撤销第 {chapter} 章的 finalize？"
                        "会删除该章风格样本并取消 finalized 标记。",
            "options": ["确认撤销", "取消"],
        })
        if not str(ans.get("answer", "")).startswith("确认撤销"):
            await TASKS.emit_done(task, {"chapter": chapter, "skipped": True})
            return

        await TASKS.emit(task, {"phase": "step", "step": "revoke-samples", "status": "running"})
        # 3. 删除 style_samples（无 revoked 列 -> DELETE）
        db_path = project_root / ".ainovel" / "style_samples.db"
        deleted = 0
        if db_path.is_file():
            with sqlite3.connect(str(db_path)) as conn:
                cur = conn.execute(
                    "DELETE FROM samples WHERE chapter = ?", (chapter,))
                deleted = cur.rowcount
                conn.commit()
        await TASKS.emit(task, {"phase": "step", "step": "revoke-samples", "status": "done",
                                "deleted": deleted})

        # 4. unmark state
        cf[str(chapter)] = {**entry, "finalized": False,
                            "unfinalized_at": datetime.now().isoformat(timespec="seconds")}
        state["chapters_finalized"] = cf
        state_path.write_text(json.dumps(state, ensure_ascii=False, indent=2),
                              encoding="utf-8")
        await TASKS.emit(task, {"phase": "step", "step": "unmark-state", "status": "done"})

        await TASKS.emit_done(task, {
            "chapter": chapter, "samples_deleted": deleted,
        })
    except Exception as exc:
        await TASKS.emit_error(task, str(exc), traceback=traceback.format_exc())


async def _run_replan_workflow(
    task: Task, *, project_root: Path, chapter: int, model: str | None,
    temperature: float | None = None,
) -> None:
    """根据已精修章节和设定集生成重规划建议报告。"""
    try:
        # 1. 收集设定集
        settings_dir = project_root / "设定集"
        settings_parts = []
        if settings_dir.is_dir():
            for p in sorted(settings_dir.rglob("*.md")):
                try:
                    text = p.read_text(encoding="utf-8")
                    settings_parts.append(
                        f"--- {p.relative_to(project_root).as_posix()} ---\n{text[:3000]}"
                    )
                except OSError:
                    pass
        settings_text = "\n\n".join(settings_parts[:20])

        # 2. 收集从起始章开始的精修章节
        body_dir = project_root / "正文"
        chapter_texts = []
        if body_dir.is_dir():
            for p in sorted(body_dir.glob("第*-精修.md")):
                m = re.search(r"第(\d+)章", p.name)
                if not m:
                    continue
                ch = int(m.group(1))
                if ch < chapter:
                    continue
                try:
                    text = p.read_text(encoding="utf-8")
                    chapter_texts.append(f"--- 第{ch:04d}章 ---\n{text[:5000]}")
                except OSError:
                    pass
        chapters_text = "\n\n".join(chapter_texts[:10])

        # 3. 总纲
        master_path = project_root / "大纲" / "总纲.md"
        master_text = master_path.read_text(encoding="utf-8") if master_path.is_file() else "（无总纲）"

        # 4. project_info
        state_path = project_root / ".ainovel" / "state.json"
        project_info_text = "（无项目信息）"
        if state_path.is_file():
            try:
                state = json.loads(state_path.read_text(encoding="utf-8"))
                project_info_text = json.dumps(state.get("project_info", {}), ensure_ascii=False, indent=2)
            except (OSError, json.JSONDecodeError):
                pass

        # 5. 调用 LLM 生成报告
        await TASKS.emit(task, {"phase": "step", "step": "analyze", "status": "running"})
        system_prompt = (
            "你是一名小说架构师。请根据用户提供的项目信息、总纲、设定集和已精修章节，"
            "生成一份「重规划建议报告」。报告必须包含：\n"
            "1. 摘要：检测到哪些新增/变更（如新系统、新角色、新能力）。\n"
            "2. 设定变更建议：需要新增/修改哪些设定文件，给出文件路径和内容。\n"
            "3. 总纲调整建议：卷划分、核心冲突、伏笔表是否需要调整。\n"
            "4. 后续章纲调整方向：从第 N 章开始，后续章节应如何演进。\n"
            "5. 应用指令 JSON 块：在报告最后提供一个 ```json 代码块，包含可解析的 actions 数组。\n\n"
            "JSON 指令支持以下 action 类型：\n"
            '- {"type": "create_setting", "path": "设定集/xxx.md", "content": "..."}\n'
            '- {"type": "update_master", "rewrite_section": "卷划分|伏笔表|...", "content": "..."}\n'
            '- {"type": "replan_from_chapter", "chapter": N}\n\n'
            "只输出 markdown 报告，不要解释。"
        )
        user_input = (
            f"## 项目信息\n{project_info_text[:3000]}\n\n"
            f"## 总纲\n{master_text[:5000]}\n\n"
            f"## 设定集\n{settings_text[:15000]}\n\n"
            f"## 已精修章节（从第 {chapter} 章起）\n{chapters_text[:20000]}\n\n"
            f"请生成重规划建议报告，保存到 大纲/重规划建议-第{chapter:04d}章.md。"
        )
        report_text = await _run_step_agent(
            task, "replan-analyze",
            project_root=project_root,
            system_prompt=system_prompt,
            user_input=user_input,
            model=model,
            max_tokens=24000,
            allowed_tools=[],
            temperature=temperature,
            operation_type="core_plot_adjustment",
            step_description=f"分析第 {chapter} 章之后的剧情走向，生成重规划建议报告",
        )

        # 6. 保存报告
        report_path = project_root / "大纲" / f"重规划建议-第{chapter:04d}章.md"
        report_path.write_text(report_text, encoding="utf-8")
        await TASKS.emit(task, {"phase": "step", "step": "report", "status": "done",
                                "path": str(report_path.relative_to(project_root))})
        await TASKS.emit_done(task, {
            "chapter": chapter,
            "report_path": str(report_path.relative_to(project_root)),
        })
    except Exception as exc:
        await TASKS.emit_error(task, str(exc), traceback=traceback.format_exc())
