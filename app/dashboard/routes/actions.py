"""
actions.py — Phase A1：把所有"无 LLM"的脚本动作做成 POST 路由。

设计原则
----------
1. 每个动作对应一个 FastAPI ``POST /api/actions/<name>``，body 是简单 JSON。
2. 路由本身只负责：参数校验 → 创建 Task → 异步起子进程 → 把 stdout/stderr
   按行 emit 到 task → 子进程退出后 emit_done/emit_error。
3. 路由立即返回 ``{"task_id": "..."}``，前端凭 task_id 调
   ``GET /api/tasks/{task_id}/stream`` 拿 SSE 实时流，或
   ``GET /api/tasks/{task_id}``（同步 JSON）等结果。
4. 全部走 ``python -X utf8 -m data_modules.XXX`` 或
   ``python -X utf8 ainovel.py <subcmd>`` 子进程；不直接 import，
   一方面隔离崩溃，另一方面与终端调用对齐。
5. 默认 cwd = ``scripts/``，因为 data_modules 通过相对路径 import 互相依赖。
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import traceback
from pathlib import Path
from typing import Any, Callable, Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from ..services.task_manager import TASKS, Task

# ---------------------------------------------------------------------------
# 通用工具
# ---------------------------------------------------------------------------

PLUGIN_ROOT = Path(__file__).resolve().parents[2]      # app/
SCRIPTS_DIR = Path(__file__).resolve().parents[3] / "tools"  # 仓库根 tools/


def _python() -> str:
    return sys.executable or "python"


async def _run_subprocess_streaming(
    task: Task,
    *,
    argv: list[str],
    cwd: Path | None = None,
    env_extra: dict[str, str] | None = None,
    parse_stdout_json: bool = True,
    timeout: float = 600.0,
    finalize_task: bool = True,
) -> dict:
    """启动一个子进程，把 stdout/stderr 按行流到 task，结束后返回结果 dict。

    若 ``parse_stdout_json`` 为真且 stdout 是合法 JSON，则把它放进 payload["json"]；
    否则放原文 string。stderr 不进 JSON，但会作为完整字符串记到 payload["stderr"]。

    ``finalize_task`` 为真时（默认，用于 ``_spawn`` 这类「子进程即整个任务」的场景），
    子进程结束会调 ``emit_done``/``emit_error`` 把 task 标记完成。为假时（用于多步工作流
    里 ``_run_step_subprocess`` 等子步骤——同一 task 后续还有别的步骤）不标记完成，
    否则第一步子进程成功就会把整个工作流 task 误判为 finished，导致
    ``cancel_active_by_mutex`` 失效与 UI 提前显示完成。
    """
    cwd = cwd or SCRIPTS_DIR
    env = os.environ.copy()
    env.setdefault("PYTHONIOENCODING", "utf-8")
    env.setdefault("PYTHONUTF8", "1")
    if env_extra:
        env.update(env_extra)

    await TASKS.emit(task, {"phase": "argv", "argv": argv, "cwd": str(cwd)})

    try:
        proc = await asyncio.create_subprocess_exec(
            *argv,
            cwd=str(cwd),
            env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except FileNotFoundError as exc:
        await TASKS.emit_error(task, f"无法启动子进程：{exc}")
        return {"ok": False, "msg": str(exc)}

    stdout_chunks: list[str] = []
    stderr_chunks: list[str] = []

    async def _pump(stream, label: str, sink: list[str]) -> None:
        while True:
            line = await stream.readline()
            if not line:
                break
            try:
                text = line.decode("utf-8", errors="replace").rstrip("\r\n")
            except Exception:
                text = repr(line)
            sink.append(text)
            await TASKS.emit(task, {"phase": label, "line": text})

    try:
        await asyncio.wait_for(
            asyncio.gather(
                _pump(proc.stdout, "stdout", stdout_chunks),
                _pump(proc.stderr, "stderr", stderr_chunks),
                proc.wait(),
            ),
            timeout=timeout,
        )
    except asyncio.TimeoutError:
        proc.kill()
        await TASKS.emit_error(task, f"子进程超时（{timeout}s），已 kill")
        return {"ok": False, "msg": "timeout"}

    stdout_text = "\n".join(stdout_chunks)
    stderr_text = "\n".join(stderr_chunks)

    payload: dict[str, Any] = {
        "returncode": proc.returncode,
        "stdout": stdout_text,
        "stderr": stderr_text,
    }
    if parse_stdout_json and stdout_text.strip():
        try:
            payload["json"] = json.loads(stdout_text)
        except json.JSONDecodeError:
            payload["json"] = None

    if proc.returncode == 0:
        if finalize_task:
            await TASKS.emit_done(task, payload)
        return {"ok": True, **payload}
    else:
        if finalize_task:
            await TASKS.emit_error(
                task,
                f"子进程退出码 {proc.returncode}",
                traceback=stderr_text[-2000:],
            )
        return {"ok": False, **payload}


async def _spawn(
    *,
    kind: str,
    label: str,
    argv_builder: Callable[[], list[str]],
    cwd: Path | None = None,
    parse_stdout_json: bool = True,
    timeout: float = 600.0,
) -> dict:
    """统一入口：建 Task，把 _run_subprocess_streaming 起到后台。

    必须在 running loop 里调用（FastAPI async 路由内自然满足）。
    """
    task = TASKS.create(kind=kind, label=label)
    argv = argv_builder()

    async def _bg() -> None:
        try:
            await _run_subprocess_streaming(
                task,
                argv=argv,
                cwd=cwd,
                parse_stdout_json=parse_stdout_json,
                timeout=timeout,
            )
        except Exception as exc:  # noqa: BLE001
            await TASKS.emit_error(task, str(exc), traceback=traceback.format_exc())

    asyncio.create_task(_bg())
    return {"task_id": task.task_id, "label": label}


# ---------------------------------------------------------------------------
# Pydantic body 模型
# ---------------------------------------------------------------------------

class _ChapterBody(BaseModel):
    chapter: Optional[int] = Field(default=None, ge=1)
    content_file: Optional[str] = None
    persist: bool = False


class _PathBody(BaseModel):
    path: str


class _DiffBody(BaseModel):
    ai_path: str
    final_path: str
    output: Optional[str] = None


class _CommitClassifiedBody(BaseModel):
    classified_path: str
    chapter: int = Field(ge=1)


class _StyleExtractBody(BaseModel):
    chapter: int = Field(ge=1)
    score: int = Field(default=4, ge=0, le=5)
    scenes: Optional[str] = None  # JSON 字符串


class _StyleSelectBody(BaseModel):
    outline: str
    max: int = 3
    semantic: bool = True
    style_query: Optional[str] = None


class _ReferenceSearchBody(BaseModel):
    query: str
    top_k: int = 5
    csv_name: Optional[str] = None
    skill: str = "write"
    genre: Optional[str] = None
    mode: str = "auto"


class _ForeshadowingSearchBody(BaseModel):
    query: str
    chapter: Optional[int] = Field(default=None, ge=1)
    top_k: int = 5


class _RebuildVectorsBody(BaseModel):
    chapter: Optional[int] = None
    rebuild: bool = False


class _ReviewPipelineBody(BaseModel):
    chapter: int = Field(ge=1)
    review_results: str
    report_file: Optional[str] = None
    metrics_out: Optional[str] = None
    save_metrics: bool = True


class _ChapterCommitBody(BaseModel):
    chapter: int = Field(ge=1)
    review_result: Optional[str] = None       # 默认 .ainovel/tmp/review_results.json
    fulfillment_result: Optional[str] = None  # 默认 .ainovel/tmp/fulfillment_result.json
    disambiguation_result: Optional[str] = None  # 默认 .ainovel/tmp/disambiguation_result.json
    extraction_result: Optional[str] = None   # 默认 .ainovel/tmp/extraction_result.json


class _MasterOutlineSyncBody(BaseModel):
    volume: int = Field(ge=1)
    writeback_file: Optional[str] = None


class _StorySystemBody(BaseModel):
    args: list[str] = Field(default_factory=list)


class _StateUpdateBody(BaseModel):
    args: list[str] = Field(default_factory=list)


class _BackupBody(BaseModel):
    args: list[str] = Field(default_factory=list)


class _RunBody(BaseModel):
    # 命令控制台：跑任意 ainovel.py 子命令。args 为 ainovel.py 之后的参数，
    # 如 ["knowledge", "query-relationships", "--entity", "E1", "--at-chapter", "5"]
    args: list[str] = Field(default_factory=list)


class _ApplyReplanReportBody(BaseModel):
    report_path: str


# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------

def create_router(get_project_root: Callable[[], Path]) -> APIRouter:
    """工厂：拿主 app 的 ``_get_project_root`` 依赖注入。"""
    router = APIRouter(prefix="/api/actions", tags=["actions"])

    def _root_or_die() -> Path:
        return get_project_root()

    def _common_argv(*tail: str) -> list[str]:
        return [_python(), "-X", "utf8", *tail]

    # ===== data_modules.deterministic_lint =====
    @router.post("/lint")
    async def action_lint(body: _ChapterBody):
        root = _root_or_die()
        if not body.chapter and not body.content_file:
            raise HTTPException(400, "至少传 chapter 或 content_file")

        def _build() -> list[str]:
            argv = _common_argv("-m", "data_modules.deterministic_lint",
                                "--project-root", str(root))
            if body.chapter:
                argv += ["--chapter", str(body.chapter)]
            if body.content_file:
                argv += ["--content-file", body.content_file]
            if body.persist:
                argv += ["--persist"]
            return argv

        return await _spawn(kind="action", label="lint", argv_builder=_build)

    # ===== data_modules.quantitative_audit =====
    @router.post("/quant-audit")
    async def action_quant(body: _ChapterBody):
        root = _root_or_die()
        if not body.chapter and not body.content_file:
            raise HTTPException(400, "至少传 chapter 或 content_file")

        def _build() -> list[str]:
            argv = _common_argv("-m", "data_modules.quantitative_audit",
                                "--project-root", str(root))
            if body.chapter:
                argv += ["--chapter", str(body.chapter)]
            if body.content_file:
                argv += ["--content-file", body.content_file]
            if body.persist:
                argv += ["--persist"]
            return argv

        return await _spawn(kind="action", label="quant-audit", argv_builder=_build)

    # ===== data_modules.audit_merger =====
    @router.post("/audit-merge")
    async def action_audit_merge(body: _ChapterBody):
        root = _root_or_die()

        def _build() -> list[str]:
            argv = _common_argv("-m", "data_modules.audit_merger",
                                "--project-root", str(root))
            if body.persist:
                argv += ["--persist"]
            return argv

        return await _spawn(kind="action", label="audit-merge", argv_builder=_build)

    # ===== data_modules.user_revision_differ diff =====
    @router.post("/revision-diff")
    async def action_revision_diff(body: _DiffBody):
        root = _root_or_die()

        def _build() -> list[str]:
            argv = _common_argv("-m", "data_modules.user_revision_differ",
                                "--project-root", str(root),
                                "diff", body.ai_path, body.final_path)
            if body.output:
                argv += ["-o", body.output]
            return argv

        return await _spawn(kind="action", label="revision-diff", argv_builder=_build)

    # ===== data_modules.user_revision_differ commit =====
    @router.post("/revision-commit")
    async def action_revision_commit(body: _CommitClassifiedBody):
        root = _root_or_die()

        def _build() -> list[str]:
            return _common_argv(
                "-m", "data_modules.user_revision_differ",
                "--project-root", str(root),
                "commit", "--classified", body.classified_path,
                "--chapter", str(body.chapter),
            )

        return await _spawn(kind="action", label="revision-commit", argv_builder=_build)

    # ===== data_modules.user_revision_differ show / high-freq =====
    @router.post("/revision-show")
    async def action_revision_show():
        root = _root_or_die()

        def _build() -> list[str]:
            return _common_argv("-m", "data_modules.user_revision_differ",
                                "--project-root", str(root), "show")

        return await _spawn(kind="action", label="revision-show", argv_builder=_build)

    @router.post("/revision-high-freq")
    async def action_revision_high():
        root = _root_or_die()

        def _build() -> list[str]:
            return _common_argv("-m", "data_modules.user_revision_differ",
                                "--project-root", str(root), "high-freq")

        return await _spawn(kind="action", label="revision-high-freq", argv_builder=_build)

    # ===== data_modules.style_sampler extract / select =====
    @router.post("/style-extract")
    async def action_style_extract(body: _StyleExtractBody):
        root = _root_or_die()

        def _build() -> list[str]:
            argv = _common_argv("-m", "data_modules.style_sampler",
                                "--project-root", str(root),
                                "extract", "--chapter", str(body.chapter),
                                "--score", str(body.score))
            if body.scenes:
                argv += ["--scenes", body.scenes]
            return argv

        return await _spawn(kind="action", label="style-extract", argv_builder=_build)

    @router.post("/style-select")
    async def action_style_select(body: _StyleSelectBody):
        root = _root_or_die()

        def _build() -> list[str]:
            argv = _common_argv(
                "-m", "data_modules.style_sampler",
                "--project-root", str(root),
                "select", "--outline", body.outline, "--max", str(body.max),
            )
            if body.semantic:
                argv += ["--semantic"]
            if body.style_query:
                argv += ["--style-query", body.style_query]
            return argv

        return await _spawn(kind="action", label="style-select", argv_builder=_build)

    # ===== reference_search =====
    @router.post("/reference-search")
    async def action_reference_search(body: _ReferenceSearchBody):
        root = _root_or_die()

        def _build() -> list[str]:
            argv = _common_argv("reference_search.py",
                                "--project-root", str(root),
                                "--query", body.query,
                                "--top-k", str(body.top_k),
                                "--skill", body.skill,
                                "--mode", body.mode)
            if body.csv_name:
                argv += ["--table", body.csv_name]
            if body.genre:
                argv += ["--genre", body.genre]
            return argv

        return await _spawn(kind="action", label="reference-search", argv_builder=_build)

    # ===== data_modules.foreshadowing_retriever =====
    @router.post("/foreshadowing-search")
    async def action_foreshadowing_search(body: _ForeshadowingSearchBody):
        root = _root_or_die()

        def _build() -> list[str]:
            argv = _common_argv("-m", "data_modules.foreshadowing_retriever",
                                "--project-root", str(root),
                                "--query", body.query,
                                "--top-k", str(body.top_k))
            if body.chapter:
                argv += ["--chapter", str(body.chapter)]
            return argv

        return await _spawn(kind="action", label="foreshadowing-search", argv_builder=_build)

    # ===== ainovel.py 子命令 =====
    @router.post("/preflight")
    async def action_preflight():
        root = _root_or_die()

        def _build() -> list[str]:
            return _common_argv("ainovel.py", "--project-root", str(root),
                                "preflight", "--format", "json")

        return await _spawn(kind="action", label="preflight", argv_builder=_build)

    @router.post("/placeholder-scan")
    async def action_placeholder():
        root = _root_or_die()

        def _build() -> list[str]:
            return _common_argv("ainovel.py", "--project-root", str(root),
                                "placeholder-scan", "--format", "json")

        return await _spawn(kind="action", label="placeholder-scan", argv_builder=_build)

    @router.post("/master-outline-sync")
    async def action_master_sync(body: _MasterOutlineSyncBody):
        root = _root_or_die()

        def _build() -> list[str]:
            argv = _common_argv("ainovel.py", "--project-root", str(root),
                                "master-outline-sync",
                                "--volume", str(body.volume),
                                "--format", "json")
            if body.writeback_file:
                argv += ["--writeback-file", body.writeback_file]
            return argv

        return await _spawn(kind="action", label="master-outline-sync", argv_builder=_build)

    @router.post("/review-pipeline")
    async def action_review_pipeline(body: _ReviewPipelineBody):
        root = _root_or_die()

        def _build() -> list[str]:
            argv = _common_argv("ainovel.py", "--project-root", str(root),
                                "review-pipeline",
                                "--chapter", str(body.chapter),
                                "--review-results", body.review_results)
            if body.report_file:
                argv += ["--report-file", body.report_file]
            if body.metrics_out:
                argv += ["--metrics-out", body.metrics_out]
            if body.save_metrics:
                argv += ["--save-metrics"]
            return argv

        return await _spawn(kind="action", label="review-pipeline",
                      argv_builder=_build, parse_stdout_json=False)

    @router.post("/chapter-commit")
    async def action_chapter_commit(body: _ChapterCommitBody):
        root = _root_or_die()

        def _build() -> list[str]:
            argv = _common_argv("ainovel.py", "--project-root", str(root),
                                "chapter-commit", "--chapter", str(body.chapter))
            for flag, val in [
                ("--review-result", body.review_result),
                ("--fulfillment-result", body.fulfillment_result),
                ("--disambiguation-result", body.disambiguation_result),
                ("--extraction-result", body.extraction_result),
            ]:
                if val:
                    argv += [flag, val]
            return argv

        return await _spawn(kind="action", label="chapter-commit",
                      argv_builder=_build, parse_stdout_json=False)

    @router.post("/backup")
    async def action_backup(body: _BackupBody):
        root = _root_or_die()

        def _build() -> list[str]:
            return _common_argv("ainovel.py", "--project-root", str(root),
                                "backup", *body.args)

        return await _spawn(kind="action", label="backup",
                      argv_builder=_build, parse_stdout_json=False)

    @router.post("/state-update")
    async def action_state_update(body: _StateUpdateBody):
        root = _root_or_die()

        def _build() -> list[str]:
            return _common_argv("ainovel.py", "--project-root", str(root),
                                "update-state", *body.args)

        return await _spawn(kind="action", label="state-update",
                      argv_builder=_build, parse_stdout_json=False)

    @router.post("/story-system")
    async def action_story_system(body: _StorySystemBody):
        root = _root_or_die()

        def _build() -> list[str]:
            return _common_argv("ainovel.py", "--project-root", str(root),
                                "story-system", *body.args)

        return await _spawn(kind="action", label="story-system",
                      argv_builder=_build, parse_stdout_json=False)

    # ===== rebuild-vectors：回填/重建向量库 =====
    @router.post("/rebuild-vectors")
    async def action_rebuild_vectors(body: _RebuildVectorsBody):
        root = _root_or_die()

        def _build() -> list[str]:
            argv = _common_argv("-m", "data_modules.rebuild_vectors",
                                "--project-root", str(root))
            if body.chapter:
                argv += ["--chapter", str(body.chapter)]
            if body.rebuild:
                argv += ["--rebuild"]
            return argv

        return await _spawn(kind="action", label="rebuild-vectors", argv_builder=_build,
                            parse_stdout_json=False)

    # ===== vector-stats：向量库统计 =====
    @router.post("/vector-stats")
    async def action_vector_stats():
        root = _root_or_die()

        def _build() -> list[str]:
            return _common_argv("-m", "data_modules.rag_adapter",
                                "--project-root", str(root), "stats")

        return await _spawn(kind="action", label="vector-stats", argv_builder=_build)

    # ===== 应用重规划建议报告 =====
    @router.post("/apply-replan-report")
    async def action_apply_replan_report(body: _ApplyReplanReportBody):
        root = _root_or_die()

        def _build() -> list[str]:
            return _common_argv("apply_replan_report.py",
                                "--project-root", str(root),
                                "--report", body.report_path)

        return await _spawn(kind="action", label="apply-replan-report", argv_builder=_build,
                            parse_stdout_json=True)

    # ===== 命令控制台：跑任意 ainovel.py 子命令 =====
    @router.post("/run")
    async def action_run(body: _RunBody):
        root = _root_or_die()
        # args 来自前端输入框，create_subprocess_exec 不经 shell 无注入风险。
        # 强制带 --project-root，确保子命令落到当前选中的书。
        def _build() -> list[str]:
            return _common_argv("ainovel.py", "--project-root", str(root), *body.args)

        return await _spawn(kind="action", label="console", argv_builder=_build)

    return router


# ---------------------------------------------------------------------------
# 任务查询路由（独立 router，方便 mount 在不同前缀）
# ---------------------------------------------------------------------------

def create_tasks_router() -> APIRouter:
    from fastapi.responses import StreamingResponse

    router = APIRouter(prefix="/api/tasks", tags=["tasks"])

    @router.get("")
    def list_tasks(limit: int = 50):
        return {"tasks": TASKS.list_tasks(limit=limit)}

    @router.get("/{task_id}")
    def get_task(task_id: str):
        task = TASKS.get(task_id)
        if not task:
            raise HTTPException(404, f"task {task_id} 不存在")
        return {
            **task.to_summary(),
            "events": task.events,
        }

    @router.get("/{task_id}/stream")
    async def stream_task(task_id: str, request: Request):
        if not TASKS.get(task_id):
            raise HTTPException(404, f"task {task_id} 不存在")
        # 浏览器 SSE 断线重连时会自动带 Last-Event-ID 头；解析后只补发增量，
        # 避免历史事件（尤其是 awaiting_input 弹窗）被重复重放导致弹窗叠加/闪烁。
        raw = request.headers.get("last-event-id") or "0"
        try:
            last_event_id = int(raw)
        except (TypeError, ValueError):
            last_event_id = 0
        return StreamingResponse(
            TASKS.stream(task_id, last_event_id=last_event_id),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @router.post("/{task_id}/resume")
    def resume_task(task_id: str, body: dict):
        ok = TASKS.resume(task_id, body.get("answer", body))
        if not ok:
            raise HTTPException(409, "任务未挂起或已结束")
        return {"ok": True}

    @router.post("/{task_id}/cancel")
    async def cancel_task(task_id: str):
        """请求取消一个运行中的任务（agent 循环下一轮检查点退出）。"""
        ok = await TASKS.cancel(task_id)
        if not ok:
            raise HTTPException(409, "任务不存在或已结束")
        return {"ok": True}

    return router
