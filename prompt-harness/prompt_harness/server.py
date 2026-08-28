"""prompt-harness API 路由 — 挂载到主 AInovel Harness 应用。"""
from __future__ import annotations

import asyncio
import json
import sqlite3
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import shutil
from fastapi import APIRouter, HTTPException, UploadFile, File
from pydantic import BaseModel, Field

from .config import SETTINGS, init_settings
from .corpus_loader import (
    build_chapter_groups, build_segments_map, extract_chapter,
    extract_segment, get_file_info, load_corpus,
    list_corpus_files, list_corpus_tree, get_novel_files,
)
from .embed_client import get_embedding
from .experience_store import ExperienceStore
from .fixed_prompts import get_fixed_prompts, get_defaults, update_fixed_prompts, reset_to_defaults
from .http_client import close_session
from .llm_client import chat_completion, DISABLE_THINKING
from .optimizer import OptimizationResult, optimize
from .run_logger import RunLogger
from .task_runtime import (
    tasks,
    _task_refs,
    _safe_start_task,
    start_task,
    spawn_task,
    cancel_task,
    list_tasks,
    cancel_all,
)

router = APIRouter(prefix="/api/prompt-harness", tags=["prompt-harness"])

store: ExperienceStore | None = None
# tasks / _task_refs 由 task_runtime 统一持有（生命周期状态机 + 事件日志）

# ============================================================
# v4.x 训练会话日志缓存
# key = session_id, value = {logger, last_active_ms, source_file, chapter_section, round_count}
# 30 分钟无活动自动结束
# ============================================================
_session_cache: dict[str, dict[str, Any]] = {}
SESSION_TIMEOUT_MS = 30 * 60 * 1000  # 30 分钟


def _get_session_logger(
    source_file: str,
    chapter_section: str = "",
    session_id: str = "",
    config_snapshot: dict[str, Any] | None = None,
) -> tuple[RunLogger, str]:
    """
    获取或创建一个训练会话的 logger。

    - 如果传了 session_id 且缓存中存在 → 复用，更新活动时间
    - 否则创建新 session，写入 session_start 事件
    - 返回 (logger, session_id)
    """
    import time as _time

    now_ms = int(_time.time() * 1000)

    # 先清理过期 session
    _expire_old_sessions(now_ms)

    if session_id and session_id in _session_cache:
        sess = _session_cache[session_id]
        sess["last_active_ms"] = now_ms
        return sess["logger"], session_id

    # 创建新 session
    logger = RunLogger()
    new_sid = logger.start_session(
        source_file=source_file,
        chapter_section=chapter_section,
        config_snapshot=config_snapshot or {},
    )
    _session_cache[new_sid] = {
        "logger": logger,
        "last_active_ms": now_ms,
        "source_file": source_file,
        "chapter_section": chapter_section,
        "round_count": 0,
    }
    return logger, new_sid


def _expire_old_sessions(now_ms: int) -> None:
    """清理超时的 session。"""
    expired = []
    for sid, sess in _session_cache.items():
        if now_ms - sess["last_active_ms"] > SESSION_TIMEOUT_MS:
            expired.append(sid)
    for sid in expired:
        sess = _session_cache.pop(sid)
        try:
            sess["logger"].end_session(
                total_rounds=sess.get("round_count", 0),
                reason="timeout",
            )
        except Exception:
            pass


def _increment_session_round(session_id: str) -> int:
    """会话轮次计数 +1，返回当前轮次号（从 1 开始）。"""
    if session_id not in _session_cache:
        return 1
    _session_cache[session_id]["round_count"] += 1
    return _session_cache[session_id]["round_count"]


def init_prompt_harness(data_dir: Path, corpus_dir: Path | None = None, output_dir: Path | None = None) -> None:
    """由主应用在 startup 时调用，初始化 prompt-harness 模块。"""
    init_settings(data_dir, corpus_dir, output_dir)
    # 【v5.33.1】函数内取 SETTINGS：顶层 from .config import SETTINGS 捕获的是 init_settings
    # 重绑前的旧对象（data_dir=Path('.')），会让模板库/经验库/template_store 误写到服务器 cwd。
    from .config import SETTINGS as _s
    _s.data_dir.mkdir(parents=True, exist_ok=True)
    _s.corpus_dir.mkdir(parents=True, exist_ok=True)
    _s.output_dir.mkdir(parents=True, exist_ok=True)
    for g in ("玄幻武侠", "都市日常"):
        (_s.corpus_dir / g).mkdir(parents=True, exist_ok=True)
    global store
    store = ExperienceStore(_s.data_dir)
    # 【2026-08-22】统一数据库（data/ainovel.db）：迁移 + 启动快照，先于一切存储类初始化
    from .ainovel_db import init_db as _init_ainovel_db
    try:
        _init_ainovel_db()
    except Exception as e:
        print(f"[ainovel_db] 初始化失败（继续用旧文件路径兜底）: {e}")
    from .template_store import init_template_store
    init_template_store(_s.data_dir / "template_library.json")
    from .plot_library import init_plot_template_library
    init_plot_template_library(_s.data_dir / "plot_template_library.json")


async def shutdown_prompt_harness() -> None:
    """由主应用在 shutdown 时调用，清理资源。"""
    await close_session()
    global store
    # Cancel any running tasks（task_runtime 统一收口）
    cancel_all()
    tasks.clear()
    store = None


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------











class ChaptersRequest(BaseModel):
    filename: str = Field(..., min_length=1, description="corpus/ 下的相对路径")
    group_size: int = Field(50, ge=10, le=200, description="每组章节数")


class ChapterAtRequest(BaseModel):
    filename: str = Field(..., min_length=1, description="corpus/ 下的相对路径")
    chapter_index: int = Field(0, ge=0, description="章节在数组中的索引（0-based）")


# v4.0: 统一段落请求模型






# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------


@router.get("/logs")
async def list_logs(
    category: str = "all",
    limit: int = 50,
    source_filter: str = "",
) -> list[dict[str, Any]]:
    """列出所有日志文件（all | sessions | runs | tasks）。"""
    from .run_logger import RunLogger
    return RunLogger.list_logs(
        category=category, limit=limit, source_filter=source_filter,
    )


# ---------------------------------------------------------------------------
# Sessions 日志
# ---------------------------------------------------------------------------

@router.get("/logs/sessions")
async def list_sessions(
    source_filter: str = "",
    limit: int = 50,
    summary: bool = True,
) -> list[dict[str, Any]]:
    """列出训练会话日志（带汇总信息）。"""
    from .run_logger import RunLogger
    if summary:
        return RunLogger.list_sessions(
            source_filter=source_filter, limit=limit,
        )
    return RunLogger.list_logs(
        category="sessions", limit=limit, source_filter=source_filter,
    )


@router.get("/logs/sessions/{session_id}")
async def read_session_log(
    session_id: str,
    event_type: str = "",
    limit: int = 0,
) -> dict[str, Any]:
    """读取单个训练会话的日志。

    返回 {found: bool, filepath, events: [...]}
    """
    from .run_logger import RunLogger

    # 先找文件路径
    logs = RunLogger.list_logs(category="sessions", limit=200)
    target = None
    for log in logs:
        if log.get("session_id") == session_id:
            target = log
            break

    if not target:
        return {"found": False, "session_id": session_id, "events": []}

    event_filter = [event_type] if event_type else None
    events = RunLogger.read_log(target["filepath"], event_filter=event_filter, limit=limit)
    return {
        "found": True,
        "session_id": session_id,
        "filepath": target["filepath"],
        "events": events,
    }


@router.get("/logs/sessions/{session_id}/summary")
async def get_session_summary(session_id: str) -> dict[str, Any]:
    """获取训练会话汇总（事件计数、分数曲线、token 统计等）。"""
    from .run_logger import RunLogger

    logs = RunLogger.list_logs(category="sessions", limit=200)
    for log in logs:
        if log.get("session_id") == session_id:
            return RunLogger.get_session_summary(log["filepath"])
    return {"found": False, "session_id": session_id}


@router.post("/logs/sessions/{session_id}/end")
async def end_session(session_id: str, reason: str = "manual") -> dict[str, Any]:
    """手动结束一个训练会话（写入 session_end 事件）。"""
    if session_id not in _session_cache:
        return {"ok": False, "error": "session not found or already ended"}
    sess = _session_cache.pop(session_id)
    logger = sess["logger"]
    try:
        logger.end_session(
            total_rounds=sess.get("round_count", 0),
            reason=reason,
        )
    except Exception as exc:
        return {"ok": False, "error": str(exc)}
    return {"ok": True, "session_id": session_id, "rounds": sess.get("round_count", 0)}


# ---------------------------------------------------------------------------
# LLM 调用日志
# ---------------------------------------------------------------------------

@router.get("/logs/llm")
async def list_llm_logs(
    date: str = "",
    limit: int = 100,
    status_filter: str = "",
    call_type: str = "",
) -> dict[str, Any]:
    """列出 LLM 调用日志。

    date: 日期 YYYYMMDD，默认今天
    limit: 返回条数（从最新往前）
    """
    from datetime import datetime as _dt
    if not date:
        date = _dt.now(timezone.utc).strftime("%Y%m%d")

    log_dir = Path(__file__).resolve().parent.parent / "logs" / "llm"
    log_file = log_dir / f"{date}.jsonl"

    if not log_file.is_file():
        return {
            "date": date,
            "total": 0,
            "events": [],
            "stats": {},
        }

    # 读全部然后过滤
    events: list[dict[str, Any]] = []
    with open(log_file, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                evt = json.loads(line)
            except json.JSONDecodeError:
                continue
            if status_filter and evt.get("status") != status_filter:
                continue
            if call_type and evt.get("call_type") != call_type:
                continue
            events.append(evt)

    total = len(events)
    # 最新的在后面，取最后 limit 条，倒序返回
    recent = events[-limit:] if limit and limit < len(events) else events
    recent = list(reversed(recent))

    # 统计
    total_tokens = sum(e.get("total_tokens", 0) or 0 for e in events)
    total_prompt_tokens = sum(e.get("prompt_tokens", 0) or 0 for e in events)
    total_completion_tokens = sum(e.get("completion_tokens", 0) or 0 for e in events)
    latencies = [e.get("latency_ms", 0) or 0 for e in events if e.get("status") == "success"]
    error_count = sum(1 for e in events if e.get("status") == "error")
    # 按 call_type 分组
    by_type: dict[str, dict[str, Any]] = {}
    for e in events:
        ct = e.get("call_type") or "unknown"
        if ct not in by_type:
            by_type[ct] = {"count": 0, "total_tokens": 0, "errors": 0}
        by_type[ct]["count"] += 1
        by_type[ct]["total_tokens"] += e.get("total_tokens", 0) or 0
        if e.get("status") == "error":
            by_type[ct]["errors"] += 1

    stats = {
        "total_calls": total,
        "error_count": error_count,
        "error_rate": round(error_count / max(total, 1), 4),
        "total_tokens": total_tokens,
        "prompt_tokens": total_prompt_tokens,
        "completion_tokens": total_completion_tokens,
        "cache_hit_tokens": sum(e.get("prompt_cache_hit_tokens", 0) or 0 for e in events),
        "cache_miss_tokens": sum(e.get("prompt_cache_miss_tokens", 0) or 0 for e in events),
        "total_cost_usd": round(sum(e.get("cost_usd", 0) or 0 for e in events), 4),
        "avg_latency_ms": round(sum(latencies) / max(len(latencies), 1)),
        "min_latency_ms": min(latencies) if latencies else 0,
        "max_latency_ms": max(latencies) if latencies else 0,
        "by_call_type": by_type,
    }

    return {
        "date": date,
        "total": total,
        "returned": len(recent),
        "events": recent,
        "stats": stats,
    }


@router.get("/logs/llm/dates")
async def list_llm_log_dates() -> list[str]:
    """列出有 LLM 日志的日期列表。"""
    log_dir = Path(__file__).resolve().parent.parent / "logs" / "llm"
    if not log_dir.is_dir():
        return []
    dates = []
    for f in log_dir.glob("*.jsonl"):
        dates.append(f.stem)
    return sorted(dates, reverse=True)


# 动态日志文件读取——必须注册在 /logs、/logs/sessions、/logs/sessions/{id}、
# /logs/llm、/logs/llm/dates 等所有字面量路由之后，否则 {filepath:path} 会
# 把它们全部吞掉（FastAPI 按注册顺序匹配）。
@router.get("/logs/{filepath:path}")
async def read_log(
    filepath: str,
    event_type: str = "",
    limit: int = 0,
) -> list[dict[str, Any]]:
    """读取一个日志文件，支持事件类型过滤。

    filepath: 相对 logs 目录的路径，如
      - runs/20260728_143022_exp123.jsonl
      - sessions/20260728/sess_abc123_novel.jsonl
      - tasks/20260728_150000_reverse_infer_novel.jsonl
    """
    from .run_logger import RunLogger
    event_filter = [event_type] if event_type else None
    return RunLogger.read_log(filepath, event_filter=event_filter, limit=limit)


@router.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@router.post("/restart")
async def restart_server() -> dict[str, Any]:
    """
    触发服务重启（开发模式下）。
    通过 touch 一个 .reload_trigger 文件让 uvicorn --reload 检测到变化并自动重载。
    同时清空运行时缓存（session_cache、preference_cache 等）。
    """
    global _session_cache, _preference_cache

    # 清空运行时缓存
    _session_cache = {}
    if _preference_cache is not None:
        _preference_cache.clear()

    # Touch trigger file 触发 uvicorn reload
    try:
        trigger_file = Path(__file__).resolve().parent.parent / ".reload_trigger"
        with open(trigger_file, "w") as f:
            f.write(f"reload at {datetime.now(timezone.utc).isoformat()}\n")
        return {"ok": True, "method": "uvicorn_reload", "message": "服务正在重启，请稍候..."}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


@router.post("/shutdown")
async def shutdown_server() -> dict[str, Any]:
    """关闭后端服务器（独立运行模式用）。"""
    import asyncio
    import os
    from datetime import datetime, timezone
    # 日志记录
    _logger = logging.getLogger("prompt_harness.server")
    _logger.warning("收到关闭请求，后端即将关闭...")
    # 延迟 500ms 确保响应返回后再退出
    asyncio.get_event_loop().call_later(0.5, os._exit, 0)
    return {"ok": True, "message": "服务器正在关闭..."}


@router.post("/rebuild-and-reload")
async def rebuild_and_reload() -> dict[str, Any]:
    """
    一键应用新代码：
    1. 前端重新构建（npm run build）
    2. 清空后端缓存
    3. 触发 uvicorn reload（后端代码热重载）

    v5.2 起为单 dist 模式：不再需要"同步 dist"步骤，
    prompt-harness 直接从主系统 dist 读取。

    **deprecated**: 推荐使用主系统（8765端口）的 `/api/system/rebuild-and-restart`。
    此端点保留用于 prompt-harness 独立运行（8777端口）的场景。

    前提：run.py 必须以开发模式启动（默认就是，即 reload=True）。
    如果当前是 --no-reload 启动的，需要手动重启一次让 reload 生效，之后就都能用了。
    """
    import os
    import subprocess
    import time
    global _session_cache, _preference_cache

    ph_root = Path(__file__).resolve().parent.parent          # prompt-harness/
    project_root = ph_root.parent                              # ainovel-write/
    frontend_src = project_root / "app" / "dashboard" / "frontend"
    dist_src = frontend_src / "dist"

    steps: list[dict[str, Any]] = []
    overall_ok = True
    start_time = time.monotonic()

    # ── Step 1: 前端构建（v5.2 起：单 dist 模式，构建即生效）──
    # prompt-harness 通过 _resolve_frontend_dist() 直接读取这份 dist
    step_start = time.monotonic()
    try:
        if not (frontend_src / "package.json").is_file():
            raise FileNotFoundError(f"找不到前端源码目录: {frontend_src}")

        # 判断用 npm 还是 pnpm / yarn
        npm_cmd = "npm"
        if (frontend_src / "pnpm-lock.yaml").is_file():
            npm_cmd = "pnpm"
        elif (frontend_src / "yarn.lock").is_file():
            npm_cmd = "yarn"

        proc = subprocess.run(
            [npm_cmd, "run", "build"],
            cwd=str(frontend_src),
            capture_output=True,
            text=True,
            timeout=300,  # 5 分钟超时
            shell=True,   # Windows 下需要 shell=True 才能找到 npm
        )
        if proc.returncode != 0:
            raise RuntimeError(proc.stderr or proc.stdout or "构建失败（未知错误）")

        steps.append({
            "step": "frontend_build",
            "ok": True,
            "duration_ms": round((time.monotonic() - step_start) * 1000),
            "output_tail": proc.stdout[-500:] if proc.stdout else "",
        })
    except Exception as exc:
        overall_ok = False
        steps.append({
            "step": "frontend_build",
            "ok": False,
            "duration_ms": round((time.monotonic() - step_start) * 1000),
            "error": str(exc),
        })
        # 构建失败就不继续了，但还是尝试触发后端 reload（可能只改了后端）
        # 不 return，继续往下走

    # ── Step 2: 清空后端缓存 ──────────────────────────────────
    _session_cache = {}
    if _preference_cache is not None:
        _preference_cache.clear()

    # ── Step 3: 触发 uvicorn reload ───────────────────────────
    step_start = time.monotonic()
    try:
        trigger_file = ph_root / ".reload_trigger"
        with open(trigger_file, "w") as f:
            f.write(f"rebuild-and-reload at {datetime.now(timezone.utc).isoformat()}\n")

        steps.append({
            "step": "backend_reload",
            "ok": True,
            "duration_ms": round((time.monotonic() - step_start) * 1000),
            "method": "uvicorn_reload",
        })
    except Exception as exc:
        overall_ok = False
        steps.append({
            "step": "backend_reload",
            "ok": False,
            "duration_ms": round((time.monotonic() - step_start) * 1000),
            "error": str(exc),
        })

    total_ms = round((time.monotonic() - start_time) * 1000)

    return {
        "ok": overall_ok,
        "total_ms": total_ms,
        "steps": steps,
        "message": "完成" if overall_ok else "部分步骤失败，请检查详情",
        "note": "如果后端代码没生效，请确认 run.py 是以默认开发模式（reload=True）启动的。",
    }


@router.get("/health/embedding")
async def health_embedding() -> dict[str, Any]:
    """测试 embedding API 是否正常工作。

    注意：model/base_url 报告**实际解析**到的配置（`_resolve_embed_config`，
    与 embed_client 调用一致），而非 SETTINGS——SETTINGS 可能因 init_settings
    时序没拿到 EMBED 环境变量而停留在默认值，会误导排查。
    """
    import time

    from .embed_client import _resolve_embed_config

    resolved_base_url, _, resolved_model = _resolve_embed_config()
    start = time.monotonic()
    emb = await get_embedding("测试文本")
    elapsed_ms = round((time.monotonic() - start) * 1000)
    return {
        "ok": emb is not None,
        "model": resolved_model or SETTINGS.embed_model,
        "base_url": resolved_base_url or SETTINGS.embed_base_url,
        "latency_ms": elapsed_ms,
        "dimension": len(emb) if emb else 0,
        "error": None if emb else "embedding 请求返回 None（检查 api key / model / base_url）",
    }


# ---------------------------------------------------------------------------
# Optimize
# ---------------------------------------------------------------------------



async def _do_optimize(
    task_id: str,
    target_text: str,
    settings: dict[str, Any],
    plot_a: str,
    max_rounds: int,
    min_rounds: int,
    success_threshold: float,
    style_name: str = "",
    max_tokens: int = 0,
    gen_temperature: float = 0.0,
    gen_top_p: float = 0.0,
    gen_presence_penalty: float | None = None,
    gen_frequency_penalty: float | None = None,
    auto_optimize_params: bool = True,
    source_file: str = "",
    category: str = "",
) -> None:
    try:
        assert store is not None

        # 查询同文档同段落历史最优 prompt（用于热启动）
        warm_start_prompt = ""
        is_same_passage = False
        historical_best_score = 0.0
        if source_file and source_file.strip():
            best_prev = await store.get_best_for_passage(source_file.strip(), target_text)
            if best_prev:
                wp = best_prev.get("optimal_prompt", "")
                if isinstance(wp, dict):
                    warm_start_prompt = json.dumps(wp, ensure_ascii=False)
                elif isinstance(wp, str) and wp.strip():
                    warm_start_prompt = wp.strip()
                if best_prev.get("search_score", 0) >= 0.7:
                    is_same_passage = True
                # v3.15: 提取历史最高分用于 delta 对比
                hs = best_prev.get("score", 0)
                if isinstance(hs, (int, float)) and hs > 0:
                    historical_best_score = float(hs)

        # 创建运行日志
        logger = RunLogger()
        log_id = logger.start(exp_id="", source_file=source_file or "")


        def progress(payload: dict[str, Any]) -> None:
            rounds = list(tasks[task_id]["progress"].get("rounds", []))
            # 只追加轮次级的进度（非 phase 专用事件如 multi_candidate / param_search）
            if not payload.get("phase"):
                rounds.append(payload)
            tasks[task_id]["progress"] = {
                **payload,
                "rounds": rounds,
            }

        result = await optimize(
            target_text,
            store,
            settings=settings,
            plot_a=plot_a,
            max_rounds=max_rounds,
            min_rounds=min_rounds,
            success_threshold=success_threshold,
            style_name=style_name,
            max_tokens=max_tokens,
            gen_temperature=gen_temperature,
            gen_top_p=gen_top_p,
            gen_presence_penalty=gen_presence_penalty,
            gen_frequency_penalty=gen_frequency_penalty,
            auto_optimize_params=auto_optimize_params,
            progress_callback=progress,
            source_file=source_file,
            warm_start_prompt=warm_start_prompt,
            is_same_passage=is_same_passage,
            historical_best_score=historical_best_score,
            category=category,
            logger=logger,
        )

        # v3.15: 记录训练完成日志
        logger.finish(combined_score=result.combined_score, rounds=result.rounds,
                       best_variant_key=result.best_variant_key)

        # 训练成功时，将最佳 prompt 落盘到 output/{style_name}/ 目录
        if style_name and not result.error and result.best_prompt:
            try:
                style_output_dir = SETTINGS.output_dir / style_name
                style_output_dir.mkdir(parents=True, exist_ok=True)
                ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
                # Markdown 版本（含元数据）
                md_file = style_output_dir / f"best_prompt_{ts}.md"
                md_content = (
                    f"# {style_name} — 最佳 Prompt\n\n"
                    f"**字符相似度**：{result.best_similarity:.2%}\n"
                    f"**长度偏差**：{result.best_length_diff:+.1%}\n"
                    f"**轮数**：{result.rounds}\n"
                    f"**时间**：{datetime.now(timezone.utc).isoformat()}\n\n"
                    f"---\n\n"
                    f"```\n{result.best_prompt}\n```\n"
                )
                md_file.write_text(md_content, encoding="utf-8")
                # 纯文本版本（方便直接复制使用）
                txt_file = style_output_dir / f"best_prompt_{ts}.txt"
                txt_file.write_text(result.best_prompt, encoding="utf-8")
            except OSError:
                pass  # 落盘失败不影响训练结果

        tasks[task_id]["status"] = "done" if not result.error else "failed"
        tasks[task_id]["result"] = _result_to_dict(result)
        if result.error:
            tasks[task_id]["error"] = result.error
    except Exception as exc:  # noqa: BLE001
        tasks[task_id]["status"] = "failed"
        tasks[task_id]["error"] = f"{type(exc).__name__}: {exc}\n{traceback.format_exc()}"
        try:
            logger.log_error(f"{type(exc).__name__}: {exc}", traceback.format_exc())
        except Exception:
            pass


@router.get("/optimize/status/{task_id}")
async def optimize_status(task_id: str) -> dict[str, Any]:
    if task_id not in tasks:
        raise HTTPException(status_code=404, detail="task not found")
    return tasks[task_id]


# ---------------------------------------------------------------------------
# v4.0 统一段落 + 人工选择 模式
# ---------------------------------------------------------------------------



































# ── 前端错误上报（诊断白屏/渲染异常用）─────────────────────

class FrontendErrorRequest(BaseModel):
    kind: str = Field("render", description="error | unhandledrejection | resource")
    message: str = Field("", description="错误信息")
    stack: str = Field("", description="调用栈")
    url: str = Field("", description="出错时的页面 URL")


@router.post("/log-frontend-error")
async def log_frontend_error(req: FrontendErrorRequest) -> dict[str, Any]:
    """把浏览器端未捕获错误写入 logs/tasks/frontend_errors.jsonl，供远程诊断白屏。"""
    import datetime as _dt
    from pathlib import Path as _P
    try:
        # 与 run_logger 同目录：prompt-harness/logs/tasks/
        _err_log = _P(__file__).resolve().parent.parent / "logs" / "tasks" / "frontend_errors.jsonl"
        _err_log.parent.mkdir(parents=True, exist_ok=True)
        with open(_err_log, "a", encoding="utf-8") as _f:
            _f.write(json.dumps({
                "ts": _dt.datetime.now().isoformat(),
                "kind": req.kind,
                "message": req.message,
                "stack": req.stack[:4000],
                "url": req.url,
            }, ensure_ascii=False) + "\n")
        return {"ok": True}
    except Exception:
        return {"ok": False}


# ── 用户偏好学习 ──







def _empty_preference() -> dict:
    return {
        "preference_vector": [0.0] * 9,
        "confidence": [0.0] * 9,
        "n_rounds": 0,
        "direction": [0] * 9,
        "interpretation": "暂无足够数据。",
        "per_dim_stats": [],
        "is_reliable": False,
    }


_preference_cache: dict[str, list[dict]] | None = None






# ---------------------------------------------------------------------------
# Experiences (CRUD)
# ---------------------------------------------------------------------------




# ---------------------------------------------------------------------------
# Analysis —— 从高分 prompt 中反向挖掘写法规律
# ---------------------------------------------------------------------------
















# ---------------------------------------------------------------------------
# Corpus
# ---------------------------------------------------------------------------

# 阅读位置存储
_READING_POSITIONS_FILE = SETTINGS.data_dir / "reading_positions.json"


def _load_reading_positions() -> dict[str, dict[str, int]]:
    if _READING_POSITIONS_FILE.is_file():
        try:
            return json.loads(_READING_POSITIONS_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def _save_reading_positions(positions: dict[str, dict[str, int]]) -> None:
    _READING_POSITIONS_FILE.write_text(
        json.dumps(positions, ensure_ascii=False, indent=2), encoding="utf-8"
    )


@router.get("/corpus")
async def corpus_info() -> dict[str, Any]:
    segments, meta = load_corpus()
    return {
        "meta": meta,
        "segments": [
            {"text": s["text"][:200] + "...", "source": s["source"]}
            for s in segments[:20]
        ],
    }








# ── 章节导航 API ──


@router.post("/corpus/chapters")
async def corpus_chapters(req: ChaptersRequest) -> dict[str, Any]:
    """解析文档中的章节结构，按 group_size 分组返回。

    用于前端章节导航：
    - 一级目录：每 50 章一组（第1-50章、第51-100章...）
    - 二级目录：组内每章的标题和字数
    """
    return build_chapter_groups(
        filepath=req.filename,
        group_size=req.group_size,
    )


@router.post("/corpus/chapter-at")
async def corpus_chapter_at(req: ChapterAtRequest) -> dict[str, Any]:
    """提取指定索引的章节全文。

    用于前端显示单章内容和作为训练素材。
    """
    return extract_chapter(
        filepath=req.filename,
        chapter_index=req.chapter_index,
    )






@router.get("/corpus/files")
async def corpus_file_list(mode: str = "flat") -> dict[str, Any]:
    """列出 corpus/ 下所有可用文件（供风格管理的文件选择器）。

    Query params:
        mode: "flat"=扁平列表, "tree"=层级树结构
    """
    if mode == "tree":
        return {"tree": list_corpus_tree()}
    return {"files": list_corpus_files()}


@router.get("/corpus/tree")
async def corpus_tree() -> dict[str, Any]:
    """返回 corpus/ 目录的层级树结构（类型→小说→文件）。"""
    return {"tree": list_corpus_tree()}


@router.get("/corpus/novels")
async def corpus_novels(genre: str = "") -> dict[str, Any]:
    """列出某类型下所有小说及文件。

    Query params:
        genre: 类型名（如"玄幻武侠"），空=返回所有类型的
    """
    tree = list_corpus_tree()
    result: list[dict[str, Any]] = []
    for genre_node in tree:
        if genre and genre_node.get("name") != genre:
            continue
        novels: list[dict[str, Any]] = []
        for child in genre_node.get("children", []):
            if child.get("type") == "novel_dir":
                novels.append({
                    "novel_name": child["name"],
                    "file_count": len(child.get("files", [])),
                    "files": [f["path"] for f in child.get("files", [])],
                })
        result.append({
            "genre": genre_node["name"],
            "novels": novels,
            # 也包含直接挂在类型下的文件
            "direct_files": [f["path"] for f in genre_node.get("files", []) if f.get("type") == "file"],
        })
    return {"genres": result}


@router.get("/corpus/file-info/{filename:path}")
async def corpus_file_info(filename: str) -> dict[str, Any]:
    """返回单个文件的元信息（不加载全文）。

    用于前端在选中文件时显示文件大小、预估段数等。
    """
    return get_file_info(filename)


@router.post("/corpus/upload")
async def corpus_upload(
    file: UploadFile = File(...),
    genre: str = "",
    novel: str = "",
) -> dict[str, Any]:
    """上传 .txt/.md 文件到 corpus/ 目录。

    Query params:
        genre: 类型目录名（如"玄幻武侠"），必填（系统已预建两个类型目录）
        novel: 小说目录名（如"示例书"），可选

    文件大小上限：50MB
    """
    # 文件大小限制
    MAX_UPLOAD_SIZE_MB = 50
    MAX_UPLOAD_SIZE_BYTES = MAX_UPLOAD_SIZE_MB * 1024 * 1024

    if not genre:
        raise HTTPException(status_code=400, detail="必须指定 genre（玄幻武侠 或 都市日常）")
    if genre not in ("玄幻武侠", "都市日常"):
        raise HTTPException(status_code=400, detail=f"genre 必须是「玄幻武侠」或「都市日常」，收到: {genre}")

    suffix = Path(file.filename or "unnamed.txt").suffix.lower()
    if suffix not in (".txt", ".md", ".markdown"):
        raise HTTPException(status_code=400, detail="仅支持 .txt / .md 文件")

    safe_name = Path(file.filename).name

    # 确定目标目录
    if novel:
        dest_dir = SETTINGS.corpus_dir / genre / novel
    else:
        dest_dir = SETTINGS.corpus_dir / genre

    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / safe_name
    # 避免覆盖：重名时加时间戳
    if dest.exists():
        stem = Path(file.filename).stem
        ts = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
        dest = dest_dir / f"{stem}_{ts}{suffix}"

    content = await file.read()

    # 文件大小检查
    if len(content) > MAX_UPLOAD_SIZE_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"文件过大（{len(content) / 1024 / 1024:.1f}MB），上限为 {MAX_UPLOAD_SIZE_MB}MB"
        )

    # 尝试常见编码解码以验证内容
    text = None
    for enc in ("utf-8", "gbk", "gb2312", "latin-1"):
        try:
            text = content.decode(enc)
            break
        except (UnicodeDecodeError, LookupError):
            continue
    if text is None:
        raise HTTPException(status_code=400, detail="无法解码文件内容，请确认是 UTF-8 或 GBK 编码的文本文件")

    dest.write_bytes(content)
    return {
        "filename": dest.name,
        "path": str(dest.relative_to(SETTINGS.corpus_dir)).replace("\\", "/"),
        "size": len(content),
        "chars": len(text),
        "preview": text[:200],
        "genre": genre,
        "novel": novel,
    }


# ── 文档画像 + 主动采样 ──









# ---------------------------------------------------------------------------
# Fixed Prompts（可编辑的固定 prompt 组件）
# ---------------------------------------------------------------------------












# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _result_to_dict(result: OptimizationResult) -> dict[str, Any]:
    return {
        "exp_id": result.exp_id,
        "style_name": result.style_name,
        "target_text": result.target_text,
        "attributes": result.attributes,
        "best_variant_key": result.best_variant_key,
        "best_prompt": result.best_prompt,
        "best_generation": result.best_generation,
        "best_scores": result.best_scores,
        "similarity": result.best_similarity,
        "length_diff": result.best_length_diff,
        "combined_score": result.combined_score,
        "gen_params": result.gen_params,
        "rounds": result.rounds,
        "history": [
            {
                "variant": h.get("variant", ""),
                "combined_score": h.get("combined_score", 0.0),
                "similarity": h.get("similarity", 0.0),
                "length_diff": h.get("length_diff", 0.0),
                "gen_char_count": h.get("gen_char_count", 0),
                "target_char_count": h.get("target_char_count", 0),
                "diagnostic_scores": h.get("diagnostic_scores"),
                "generation": h.get("generation", ""),
                "prompt": h.get("prompt", ""),
                "change_summary": h.get("change_summary", ""),
                "lessons_block": h.get("lessons_block", ""),
                "failure_patterns": h.get("failure_patterns", []),
            }
            for h in result.history
        ],
        "category": result.category,
        "failure_ledger": result.failure_ledger,
        "cross_run_lessons": result.cross_run_lessons,
        "error": result.error,
    }


def _prompt_preview(prompt: Any, max_len: int = 200) -> str:
    """生成 prompt 预览文本。"""
    if isinstance(prompt, dict):
        text = prompt.get("system", "") or prompt.get("craft_rules", "") or json.dumps(prompt, ensure_ascii=False)
    elif isinstance(prompt, str):
        text = prompt
    else:
        text = str(prompt)
    if len(text) > max_len:
        return text[:max_len] + "..."
    return text


# 任务管理（取消 + 列表）
# ---------------------------------------------------------------------------


@router.get("/tasks")
async def tasks_list() -> dict[str, Any]:
    """列出所有后台任务状态（用于进程调查面板；含 kind/耗时）。"""
    summary = list_tasks()
    return {"tasks": summary, "total": len(summary)}


@router.post("/tasks/{task_id}/cancel")
async def tasks_cancel(task_id: str) -> dict[str, Any]:
    """取消一个正在运行的后台任务。"""
    if task_id not in tasks:
        raise HTTPException(status_code=404, detail="task not found")
    return cancel_task(task_id)


# ---------------------------------------------------------------------------
# v5.x 逆向推理 run 记录（前端「任务记录」页签）
# RunLogger.list_logs 只扫 *.jsonl，v5.x run 是 *.json，单独访问。
# ---------------------------------------------------------------------------





# ---------------------------------------------------------------------------
# 模板库（从训练优秀候选提炼）
# ---------------------------------------------------------------------------













# ---------------------------------------------------------------------------
# 剧情推导（极简剧情 → 树形发散 → 填充模板 → 完整 prompt）
# ---------------------------------------------------------------------------









# ── v5.29 极简推导训练：生成极简 / 单情节线推导 / 压缩前沿训练 ──

























async def _do_minimal_train(
    task_id: str,
    run_file: str,
    candidate_idx: int,
    arc: dict[str, Any],
    template: dict[str, Any] | None,
    budgets: list[int],
    samples: int,
    ratio: float,
    ai_flavor: bool,
) -> None:
    from .minimal_train import train_arc_frontier

    def progress(p: dict[str, Any]) -> None:
        budgets_done = list(tasks[task_id]["progress"].get("budgets", []))
        budgets_done.append({
            "budget": p.get("budget"), "index": p.get("index"),
            "total": p.get("total"), "mean": p.get("mean"), "message": p.get("message", ""),
        })
        tasks[task_id]["progress"].update({
            "budgets": budgets_done,
            "total": p.get("total", len(budgets)),
            "percent": round(100 * (p.get("index", 0)) / max(1, p.get("total", len(budgets))), 1),
            "message": p.get("message", ""),
        })

    try:
        report = await train_arc_frontier(
            run_file, candidate_idx, arc, template=template,
            budgets=budgets, samples=samples, threshold_ratio=ratio,
            progress=progress, ai_flavor=ai_flavor,
        )
        tasks[task_id]["status"] = "done"
        tasks[task_id]["progress"]["percent"] = 100
        tasks[task_id]["result"] = report
    except Exception as exc:
        tasks[task_id]["status"] = "failed"
        tasks[task_id]["error"] = f"{type(exc).__name__}: {exc}\n{traceback.format_exc()}"






async def _do_train_frontier(
    task_id: str,
    run_file: str,
    arc: dict[str, Any],
    budgets: list[int],
    samples: int,
    ratio: float,
) -> None:
    """解析 run 的情节线原章节 → train_ladder_frontier（前向推回评分找最短充分极简）。"""
    from .ladder import train_ladder_frontier
    from .minimal_train import read_run, run_chapter_offset, resolve_arc_chapters

    def progress(p: dict[str, Any]) -> None:
        budgets_done = list(tasks[task_id]["progress"].get("budgets", []))
        budgets_done.append({
            "budget": p.get("budget"), "index": p.get("index"),
            "total": p.get("total"), "message": p.get("message", ""),
        })
        tasks[task_id]["progress"].update({
            "budgets": budgets_done,
            "total": p.get("total", len(budgets)),
            "percent": round(100 * (p.get("index", 0)) / max(1, p.get("total", len(budgets))), 1),
            "message": p.get("message", ""),
        })

    try:
        run = read_run(run_file)
        if run is None:
            raise ValueError(f"run 不存在：{run_file}")
        targets = run.get("target_texts") or []
        run_start = run_chapter_offset(run_file, run)
        lo, hi = resolve_arc_chapters(
            run, run_start, int(arc.get("start_chapter") or 1), int(arc.get("end_chapter") or 1))
        arc_text = "\n".join(targets[lo:hi + 1])
        style = str(arc.get("style") or "").strip()
        role_setting = str(arc.get("role_setting") or "").strip()
        report = await train_ladder_frontier(
            arc_text, style=style, role_setting=role_setting,
            budgets=budgets, samples=samples, threshold_ratio=ratio, progress=progress,
        )
        report["arc"] = {"name": str(arc.get("name") or ""), "start_chapter": arc.get("start_chapter"),
                         "end_chapter": arc.get("end_chapter")}
        tasks[task_id]["status"] = "done"
        tasks[task_id]["progress"]["percent"] = 100
        tasks[task_id]["result"] = report
    except Exception as exc:
        tasks[task_id]["status"] = "failed"
        tasks[task_id]["error"] = f"{type(exc).__name__}: {exc}\n{traceback.format_exc()}"


def _novel_from_source_file(src: str) -> str:
    """从 run.source_file 提取小说名：「示例书1-10章」→「示例书」。"""
    import re as _re
    s = (src or "").strip()
    s = _re.sub(r"[0-9][0-9\s]*[-~至][0-9\s]*章(\.txt)?$", "", s)
    s = _re.sub(r"\(\d+\s*[-~至]\s*\d+\s*章\)(\.txt)?$", "", s)
    s = _re.sub(r"\.txt$", "", s)
    return s.strip()


def _novel_from_arcmap(payload: dict[str, Any]) -> str:
    """复用 /arcs 的 novel 提取：payload.novel → filename 路径中段 → stem 剥离。"""
    novel = str(payload.get("novel") or "").strip()
    if novel:
        return novel
    rel = str(payload.get("filename") or "").replace("\\", "/").strip("/")
    parts = rel.split("/") if rel else []
    if len(parts) >= 3:
        return parts[1]
    if len(parts) == 2:
        return parts[0]
    from pathlib import Path as _P
    return _P(payload.get("filename") or "").stem.replace("arc_map_", "")




# ---------------------------------------------------------------------------
# 剧情桥段分类（v5.24 剧情库 + 混合校验）
# ---------------------------------------------------------------------------













async def _do_arc_classify(
    task_id: str,
    filepath: str,
    start_chapter: int,
    end_chapter: int | None,
    window_size: int,
) -> None:
    from .arc_classify import classify_chapters

    try:
        def progress(msg: str) -> None:
            tasks[task_id]["progress"]["messages"].append(msg)

        result = await classify_chapters(
            filepath,
            start_chapter=start_chapter,
            end_chapter=end_chapter,
            window_size=window_size,
            progress=progress,
        )
        tasks[task_id]["status"] = "done"
        tasks[task_id]["result"] = result
    except Exception as exc:
        tasks[task_id]["status"] = "failed"
        tasks[task_id]["error"] = f"{type(exc).__name__}: {exc}\n{traceback.format_exc()}"


# ---------------------------------------------------------------------------
# v5.33 情节模板库（plot_library）+ 阶梯桥接（bridge）—— 双模块连接
# ---------------------------------------------------------------------------

class PlotTemplateMatchRequest(BaseModel):
    query: str = Field(..., min_length=1, description="要写的剧情描述（l1/l2 级文本）")
    style: str = Field("", description="风格基调")
    role_setting: str = Field("", description="角色设定")
    top_k: int = Field(8, ge=1, le=20, description="embedding 召回候选数")


class PlotTemplateExtractRequest(BaseModel):
    filepath: str = Field(..., min_length=1, description="语料文件路径（corpus 下相对路径）")
    start_chapter: int = Field(1, ge=1, description="起始章（1-based）")
    end_chapter: int | None = Field(None, ge=1, description="结束章（None=到文件末尾）")
    style: str = Field("", description="风格基调")
    role_setting: str = Field("", description="角色设定")
    budget: int = Field(80, ge=15, le=2000, description="极简预算（压缩强度）")
    min_score: float = Field(0.65, ge=0.0, le=1.0, description="入库合格分阈值")


class PlotTemplateExtractStoreRequest(BaseModel):
    task_id: str = Field(..., min_length=1, description="榨干任务 id（/plot-templates/extract 返回）")
    arc: int = Field(..., ge=1, description="要入库的剧情情节下标（1-based，report 里的 arc）")
    chapter: int | None = Field(None, ge=1, description="【A1】只入库弧内指定章（不传=弧内全部达标章逐章入库，去 AND 门）")
    min_score: float = Field(0.65, ge=0.0, le=1.0, description="【阈值校准】入库合格分（默认 0.65；可降到 0.60 救回差一口气的章，无需重榨）")


class BridgeStateRequest(BaseModel):
    l1: str = Field("", description="一句话极简剧情（用户手写，无字段时用）")
    style: str = Field("", description="风格基调")
    role_setting: str = Field("", description="角色设定")
    archetype: str = Field("", description="命中剧情库原型名（可空）")
    template: dict[str, Any] | None = Field(None, description="命中的情节模板（match 返回后传入）")
    field_values: dict[str, Any] | None = Field(None, description="用户按模板填好的字段值（v5.33.2，自动组装 l1）")
    n_chapters: int = Field(1, ge=1, le=20, description="章数（>1 = 前十章章纲模式，l2→N 章 l3）")


class BridgeStepRequest(BaseModel):
    state: dict[str, Any] = Field(..., description="阶梯状态（前后端往返）")


class BridgeActiveChapterRequest(BaseModel):
    state: dict[str, Any] = Field(..., description="阶梯状态")
    idx: int = Field(0, ge=0, description="当前要下钻的章（0-based）")


class BridgeConfirmRequest(BaseModel):
    state: dict[str, Any] = Field(..., description="阶梯状态")
    level: str = Field(..., description="l1-l5")


class BridgeModifyRequest(BaseModel):
    state: dict[str, Any] = Field(..., description="阶梯状态")
    level: str = Field(..., description="l1/l2/l3/l4")
    instruction: str = Field(..., min_length=1, description="修改意见")


class BridgeChatRequest(BaseModel):
    state: dict[str, Any] = Field(default_factory=dict, description="阶梯状态（可空）")
    messages: list[dict[str, Any]] = Field(default_factory=list, description="对话历史")
    template: dict[str, Any] | None = Field(None, description="命中模板（可空）")


@router.get("/plot-templates")
async def plot_template_list() -> list[dict[str, Any]]:
    """情节模板库列表（最新在前）。"""
    from .plot_library import get_plot_template_library
    return get_plot_template_library().list()


@router.get("/plot-templates/perf")
async def plot_template_perf() -> dict[str, Any]:
    """【模板绩效面板】聚合模板库 usage 数据——哪些模板被反复用/没用过/效果好。

    返回：
    - total / used / never_used：总量、有使用、从未使用
    - by_usage：按 use_count 排序（id/name/archetype/source/use_count/avg_quality/last_used_at）
    - by_quality：按 avg_quality 排序（有 n_finalized 的）
    - by_archetype：按弧类型聚合 use_count/模板数
    - match_stats：recent_matches 命中统计（哪类模板最常被匹配）
    """
    from .plot_library import get_plot_template_library
    from .workbench_logger import _iter_files

    tpls = get_plot_template_library().list()
    total = len(tpls)

    def _usage(t: dict) -> dict:
        u = t.get("usage") or {}
        return {
            "use_count": int(u.get("use_count") or 0),
            "avg_quality": u.get("avg_quality"),
            "n_finalized": int(u.get("n_finalized") or 0),
            "last_used_at": u.get("last_used_at"),
        }

    used = [t for t in tpls if _usage(t)["use_count"] > 0]
    by_usage = sorted(
        [{
            "id": t.get("id"), "name": t.get("name"), "archetype": t.get("archetype"),
            "source": str(((t.get("source") or {}).get("corpus") or "") or ""),
            "sim": t.get("qualified", {}).get("score") if isinstance(t.get("qualified"), dict) else None,
            **_usage(t),
        } for t in used],
        key=lambda x: x["use_count"], reverse=True)

    finalized = [t for t in tpls if _usage(t)["n_finalized"] > 0]
    by_quality = sorted(
        [{
            "id": t.get("id"), "name": t.get("name"), "archetype": t.get("archetype"),
            **_usage(t),
        } for t in finalized],
        key=lambda x: x["avg_quality"] or 0, reverse=True)

    # 按弧类型聚合
    arc_agg: dict[str, dict[str, Any]] = {}
    for t in tpls:
        arc = str(t.get("archetype") or "未分类")
        a = arc_agg.setdefault(arc, {"templates": 0, "use_count": 0, "n_finalized": 0})
        a["templates"] += 1
        a["use_count"] += _usage(t)["use_count"]
        a["n_finalized"] += _usage(t)["n_finalized"]
    by_archetype = sorted(
        [{"archetype": k, **v} for k, v in arc_agg.items()],
        key=lambda x: x["use_count"], reverse=True)

    # 最近命中统计（workbench 日志 plot_template_match → 匹配到哪些模板）
    match_hits: dict[str, int] = {}
    for f in _iter_files("", ""):
        try:
            for line in open(f, "r", encoding="utf-8"):
                line = line.strip()
                if not line:
                    continue
                try:
                    evt = json.loads(line)
                except Exception:
                    continue
                if evt.get("call_type") != "plot_template_match":
                    continue
                out = str(evt.get("output") or "")
                try:
                    for t in (json.loads(out).get("templates") or []):
                        if t.get("name"):
                            match_hits[str(t["name"])] = match_hits.get(str(t["name"]), 0) + 1
                except Exception:
                    pass
        except Exception:
            continue
    match_stats = sorted(
        [{"name": k, "hits": v} for k, v in match_hits.items()],
        key=lambda x: x["hits"], reverse=True)[:20]

    return {
        "total": total,
        "used": len(used),
        "never_used": total - len(used),
        "by_usage": by_usage[:30],
        "by_quality": by_quality[:20],
        "by_archetype": by_archetype,
        "match_stats": match_stats,
    }


@router.delete("/plot-templates/{template_id}")
async def plot_template_delete(template_id: str) -> dict[str, Any]:
    from .plot_library import get_plot_template_library
    ok = get_plot_template_library().delete(template_id)
    if not ok:
        raise HTTPException(status_code=404, detail="plot template not found")
    return {"ok": True}


@router.post("/plot-templates/match")
async def plot_template_match(req: PlotTemplateMatchRequest) -> dict[str, Any]:
    """写剧情 → 命中情节模板库（embedding 召回 + LLM 仲裁选 1-3）。"""
    from .plot_library import match_plot_templates
    try:
        templates = await match_plot_templates(
            req.query, style=req.style, role_setting=req.role_setting, top_k=req.top_k)
    except RuntimeError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return {"templates": templates}


def _plot_extract_result_path(task_id: str) -> Path:
    """榨干结果落盘路径（data_dir/plot_extract_results/{task_id}.json）。

    v5.33.5：结果持久化到磁盘，服务器重启后 extract-store 仍能按 task_id 取到报告入库。
    """
    from .config import SETTINGS as _s
    d = _s.data_dir / "plot_extract_results"
    d.mkdir(parents=True, exist_ok=True)
    return d / f"{task_id}.json"


def _save_plot_extract_result(task_id: str, result: dict) -> None:
    try:
        _plot_extract_result_path(task_id).write_text(
            json.dumps(result, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass


def _load_plot_extract_result(task_id: str) -> dict | None:
    try:
        p = _plot_extract_result_path(task_id)
        if p.exists():
            return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        pass
    return None


async def _load_arcs_for_range(
    filepath: str,
    start_chapter: int,
    end_chapter: int,
    progress: Callable[[str], None] | None = None,
) -> list[dict[str, Any]]:
    """【v5.33.7】加载本书 arc_map 覆盖 [start,end] 的情节，裁剪到选择范围。

    无 arc_map 或范围未覆盖 → 现切 classify_chapters（写回真实 arc_map）；
    现切失败 → 返回 []（extract_from_doc 退回每章一情节）。
    返回 [{start_chapter, end_chapter, archetype, description}]。
    """
    from .config import SETTINGS as _s
    from pathlib import Path as _P

    try:
        am_path = _s.data_dir / f"arc_map_{_P(filepath).stem}.json"
        if am_path.exists():
            am = json.loads(am_path.read_text(encoding="utf-8"))
            arcs = [
                {"start_chapter": max(a.get("start_chapter") or 0, start_chapter),
                 "end_chapter": min(a.get("end_chapter") or 0, end_chapter),
                 "archetype": str(a.get("archetype") or "").strip(),
                 "description": str(a.get("description") or "").strip()}
                for a in (am.get("arcs") or [])
                if (a.get("end_chapter") or 0) >= start_chapter
                and (a.get("start_chapter") or 0) <= end_chapter
            ]
            if arcs:
                return arcs
    except Exception:
        pass
    # 无 arc_map 或未覆盖 → 现切
    try:
        from .arc_classify import classify_chapters
        if progress:
            progress(f"当前文档范围未在剧情库划分，现切第 {start_chapter}-{end_chapter} 章…")
        res = await classify_chapters(
            filepath, start_chapter=start_chapter, end_chapter=end_chapter,
            window_size=8, progress=progress,
        )
        return [
            {"start_chapter": max(a.get("start_chapter") or 0, start_chapter),
             "end_chapter": min(a.get("end_chapter") or 0, end_chapter),
             "archetype": str(a.get("archetype") or "").strip(),
             "description": str(a.get("description") or "").strip()}
            for a in (res.get("arcs") or [])
        ]
    except Exception:
        return []


@router.post("/plot-templates/extract")
async def plot_template_extract_start(req: PlotTemplateExtractRequest) -> dict[str, str]:
    """异步「单按钮榨干」：对选定文档逐章 build_ladder→verify_ladder→合格去实体化入库。
    进度走 /optimize/status/{task_id} 轮询（type=plot_extract）。"""
    task_id = start_task("plot_extract", progress={"messages": [], "chapter": 0, "total": None})
    _safe_start_task(task_id, _do_plot_extract(
        task_id, req.filepath, req.start_chapter, req.end_chapter,
        req.style, req.role_setting, req.budget, req.min_score,
    ))
    return {"task_id": task_id}


async def _do_plot_extract(
    task_id: str,
    filepath: str,
    start_chapter: int,
    end_chapter: int | None,
    style: str,
    role_setting: str,
    budget: int,
    min_score: float,
) -> None:
    from .corpus_loader import _read_file_text, _resolve_corpus_dir, parse_chapters
    from .plot_library import extract_from_doc

    try:
        # 【v5.33 优化】一次 parse + 一次 read，按章节位置切片——
        # 原实现对每章都 extract_chapter（内部 parse+read 全文件），500 章文档 = O(n²) 全量解析。
        parsed = parse_chapters(filepath)
        total = parsed.get("total_chapters") or 0
        if total <= 0:
            raise RuntimeError(f"文档 {filepath} 无章节可提取")
        end = end_chapter or total
        start = max(1, min(start_chapter, total))
        end = max(start, min(end, total))
        tasks[task_id]["progress"]["total"] = max(1, end - start + 1)
        ch_pos = parsed.get("chapters") or []
        full_path = (_resolve_corpus_dir() / filepath).resolve()
        text = _read_file_text(full_path)
        chapters: list[str] = []
        chapter_nums: list[int] = []   # 【v5.33.7】真实章号（情节分组/来源显示用）
        for i in range(start, end + 1):
            ch = ch_pos[i - 1] if (i - 1) < len(ch_pos) else None
            if not ch:
                continue
            txt = text[ch["start_pos"]:ch["end_pos"]].strip()
            if txt:
                chapters.append(txt)
                chapter_nums.append(int(ch.get("chapter_num") or i))
        if not chapters:
            raise RuntimeError(f"文档 {filepath} 第 {start}-{end} 章无正文")

        def progress(p: dict[str, Any]) -> None:
            # extract_from_doc 回调 key 是 message（非 msg）
            tasks[task_id]["progress"]["messages"].append(p.get("message") or "")
            if "chapter" in p:
                tasks[task_id]["progress"]["chapter"] = p["chapter"]
            phase = p.get("phase")
            if phase:
                tasks[task_id]["progress"]["phase"] = phase

        # 【v5.33.7】按剧情情节分组：加载本书 arc_map 覆盖 [start,end] 的情节；无则现切；
        # 都失败 → 不传 arcs（extract_from_doc 退回每章一情节，兼容旧行为）
        arcs = await _load_arcs_for_range(
            filepath, start, end,
            lambda msg: progress({"phase": "arc_classify", "message": msg}),
        )

        result = await extract_from_doc(
            chapters,
            corpus=filepath,   # 【v5.33.6】记录来源书，入库时写进模板 source
            style=style, role_setting=role_setting,
            budget=budget, min_score=min_score,
            chapter_nums=chapter_nums, arcs=arcs,   # 【v5.33.7】
            progress=progress,
        )
        tasks[task_id]["status"] = "done"
        tasks[task_id]["result"] = result
        # 【v5.33.5】结果落盘，重启后 extract-store 仍可入库
        _save_plot_extract_result(task_id, result)
    except Exception as exc:
        tasks[task_id]["status"] = "failed"
        tasks[task_id]["error"] = f"{type(exc).__name__}: {exc}\n{traceback.format_exc()}"


@router.post("/plot-templates/extract-store")
async def plot_template_extract_store(req: PlotTemplateExtractStoreRequest) -> dict[str, Any]:
    """【v5.33.7】用户确认后把某个剧情情节去实体化入库（幂等）。

    榨干 extract 的 report 以剧情情节为单位（每个情节含情节内全部章的骨架/分数/正文）；
    用户点「入库」→ 本端点按 task_id + arc 取情节条目，**情节内所有章均达标**才可入库，
    重建「情节级阶梯」（单章走单章格式，多章走 l3_chapters/l4_chapters）→ create_plot_template，
    模板记录含情节内全部章内容、来源记章范围。

    【v5.33.5】结果已落盘（data_dir/plot_extract_results/{task_id}.json）：内存 tasks
    找不到时从磁盘读（重启后仍可入库）；入库后回写磁盘保证幂等。
    """
    from .plot_library import create_plot_template
    task = tasks.get(req.task_id)
    result = (task.get("result") if task else None)
    if result is None:
        result = _load_plot_extract_result(req.task_id)
        if result is None:
            raise HTTPException(status_code=404, detail="榨干任务不存在或结果已失效")
    report = result.get("report") or []
    entry = next((r for r in report if r.get("arc") == req.arc), None)
    if not entry:
        raise HTTPException(status_code=404, detail=f"任务里没有第 {req.arc} 个剧情情节")
    chs = entry.get("chapters") or []
    if not chs:
        raise HTTPException(status_code=422, detail=f"第 {req.arc} 个剧情情节无章节结果")
    # 【A1 2026-08-15】去 AND 门：达标章逐章独立入库（不再要求弧内全章达标）。
    # 弧内达标率 9.5% 时 AND 门把多章弧合格率压到 ~0.09%，26 个弧被卡死。
    if entry.get("template_id") and req.chapter is None and entry.get("qualified"):
        # 旧版整弧入库幂等兼容
        return {"template": {"id": entry["template_id"],
                             "name": entry.get("template_name") or entry.get("name"),
                             "already": True}}
    if req.chapter is not None:
        targets = [c for c in chs
                   if c.get("chapter") == req.chapter or c.get("chapter_num") == req.chapter]
        if not targets:
            raise HTTPException(status_code=404, detail=f"弧 {req.arc} 内没有第 {req.chapter} 章")
    else:
        # 【节拍覆盖达标】新结果看 qualified 标志（节拍覆盖判定）；旧结果（无该标志）回退 score 阈值
        targets = [c for c in chs
                   if c.get("qualified") or float(c.get("score") or 0.0) >= req.min_score]
    if not targets:
        fails = [c.get("chapter_num") for c in chs]
        raise HTTPException(status_code=422,
                            detail=f"弧 {req.arc} 内无 ≥{req.min_score} 的章（各章分 {fails} 低于合格线），无可入库")
    stored: list[dict] = []
    for c in targets:
        if c.get("template_id"):
            stored.append({"template": {"id": c["template_id"],
                                        "name": c.get("template_name"), "already": True}})
            continue
        sk = c.get("skeleton") or {}
        ladder = {
            "l1_minimal": str(sk.get("l1") or "").strip(),
            "l2_arc": str(sk.get("l2") or "").strip(),
            "l3_chapter": sk.get("l3") or {},
            "l4_scenes": sk.get("l4") or [],
        }
        scores = c.get("scores") or {}
        arc_name = str(entry.get("name") or f"第{entry.get('start_chapter')}-{entry.get('end_chapter')}章")
        name = f"{arc_name}·第{c.get('chapter_num')}章"
        tpl = await create_plot_template(
            name, ladder,
            qualified={
                "score": c.get("score"),
                "s_char": scores.get("s_char"),
                "turn_fidelity": scores.get("turn_fidelity"),
                "ai_flavor": scores.get("ai_flavor"),
                "len_ratio": scores.get("len_ratio"),
            },
            corpus=str(result.get("corpus") or "").strip(),
            chapter_start=c.get("chapter_num"),
            chapter_end=c.get("chapter_num"),
            archetype=str(entry.get("archetype") or "").strip(),
        )
        c["template_id"] = tpl.get("id")
        c["template_name"] = tpl.get("name")
        stored.append({"template": tpl})
    # 回写磁盘（内存+磁盘一致；重启后幂等仍有效）
    _save_plot_extract_result(req.task_id, result)
    if len(stored) == 1:
        return stored[0]
    return {"templates": stored, "count": len(stored)}


@router.post("/bridge/state")
async def bridge_create_state(req: BridgeStateRequest) -> dict[str, Any]:
    """新建一次阶梯创作。
    n_chapters>1 = 前十章章纲模式：l2 情节概要 → 一次拆 N 章 l3 章纲。
    v5.33.2：命中模板 + 填好字段（field_values）→ 先自动组装 l1（待确认）→ 走阶梯；
    否则用用户手写 l1（天然已确认）。"""
    from .bridge import new_state
    from .plot_library import assemble_l1
    l1 = req.l1
    l1_confirmed = None
    if req.field_values and req.template:
        try:
            l1 = await assemble_l1(
                req.field_values, req.template,
                style=req.style, role_setting=req.role_setting,
            )
        except RuntimeError as exc:
            raise HTTPException(status_code=422, detail=str(exc))
        l1_confirmed = False   # 组装 l1 需用户审阅确认
    return new_state(
        style=req.style, role_setting=req.role_setting,
        archetype=req.archetype, template=req.template, l1=l1,
        n_chapters=req.n_chapters,
        field_values=req.field_values, l1_confirmed=l1_confirmed,
    )


@router.post("/bridge/active-chapter")
async def bridge_active_chapter(req: BridgeActiveChapterRequest) -> dict[str, Any]:
    """多章章纲模式下切换当前下钻的章（清空该章下游 l4/l5）。"""
    from .bridge import set_active_chapter
    return set_active_chapter(req.state, req.idx)


@router.post("/bridge/step")
async def bridge_step(req: BridgeStepRequest) -> dict[str, Any]:
    """分步确认制：从当前已确认最高级生成下一级（一次只进一级）。"""
    from .bridge import step_ladder
    try:
        return await step_ladder(req.state)
    except RuntimeError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


@router.post("/bridge/confirm")
async def bridge_confirm(req: BridgeConfirmRequest) -> dict[str, Any]:
    """确认某级内容（确认后才允许继续生成下一级）。"""
    from .bridge import confirm_level
    return confirm_level(req.state, req.level)


@router.post("/bridge/modify")
async def bridge_modify(req: BridgeModifyRequest) -> dict[str, Any]:
    """对话修改：LLM 按用户意见改写当前级，清空下游级。"""
    from .bridge import modify_level
    try:
        return await modify_level(req.state, req.level, req.instruction)
    except RuntimeError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


@router.post("/bridge/chat")
async def bridge_chat(req: BridgeChatRequest) -> dict[str, Any]:
    """对话窗口后端：结合当前阶梯 + 命中模板，回答/给修改建议。"""
    from .bridge import chat_turn
    try:
        return await chat_turn(req.messages, req.state, req.template)
    except RuntimeError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


# ===========================================================================
# AI 创作模块（新测试书）：逐情节创作 + 元素选择隔离 + 双评分
# 绑定主系统书结构，见 prompt_harness/ai_creation.py
# ===========================================================================

class AiCreationBookRequest(BaseModel):
    book_root: str = Field(..., min_length=1, description="主系统书根目录绝对路径")


class AiInitRequest(AiCreationBookRequest):
    brief: str = Field("", min_length=1, description="手写 brief（与 settings 二选一）")
    title: str = Field("", description="书名")
    genre: str = Field("", description="类型")
    settings: dict[str, Any] | None = Field(None, description="结构化基本设定（16 字段）")


class AiSettingsPutRequest(AiCreationBookRequest):
    settings: dict[str, Any] = Field(..., description="基本设定字段（可编辑源）")


class AiSettingsGenerateRequest(AiCreationBookRequest):
    settings: dict[str, Any] | None = Field(None, description="基本设定（缺省读书内已有）")
    title: str = Field("", description="书名")
    genre: str = Field("", description="类型")


class AiNewArcRequest(AiCreationBookRequest):
    l1: str = Field(..., min_length=1, description="本情节一句话极简剧情")
    n_chapters: int = Field(1, ge=1, le=20, description="本情节章数（>1 = 章纲模式）")
    style: str = Field("", description="风格基调（init 提取，可回填）")
    role_setting: str = Field("", description="角色设定（init 提取，可回填）")
    selected: dict[str, list[str]] | None = Field(None, description="{characters,items,settings} id 列表")
    carry_prev: bool = Field(True, description="带入上一情节结局锚点")


class AiArcSelectRequest(AiCreationBookRequest):
    arc_id: str = Field(..., min_length=1)
    selected: dict[str, list[str]] | None = Field(None)


class AiArcIdRequest(AiCreationBookRequest):
    arc_id: str = Field(..., min_length=1)


class AiArcUpdateRequest(AiArcIdRequest):
    name: str | None = Field(None, description="弧标题")
    status: str | None = Field(None, description="draft / active / done")
    start_chapter: int | None = Field(None, ge=1, description="起始章号")


class AiArcCapacityRequest(AiCreationBookRequest):
    """容量诊断/补齐请求：传 l1（书级）或 arc_id（取弧内 l1）。"""
    l1: str | None = Field(None, description="直接传 l1（优先于 arc_id）")
    arc_id: str | None = Field(None, description="弧 id（取弧内 l1）")
    n_chapters: int = Field(1, ge=1, le=20)
    max_chars: int = Field(160, ge=60, le=300, description="补齐后 l1 上限")


class AiArcSetTemplateRequest(AiArcIdRequest):
    template_id: str = Field("", description="情节模板 id；空串=清除模板")


class AiArcConfirmRequest(AiArcIdRequest):
    level: str = Field(..., description="l1-l5")


class AiActiveChapterRequest(AiArcIdRequest):
    idx: int = Field(0, ge=0, description="当前要下钻的章（0-based）")


class AiArcModifyRequest(AiArcIdRequest):
    level: str = Field(..., description="l1/l2/l3/l4")
    instruction: str = Field(..., min_length=1)


class AiSetLevelRequest(AiArcIdRequest):
    level: str = Field(..., description="l1-l5（都可直接编辑）")
    text: str = Field("", description="l1/l2/l5 的文本内容")
    data: Any | None = Field(None, description="l3={title,core,beats} 或 {chapters:[...]}；l4=场景列表")
    idx: int | None = Field(None, description="l3 多章模式下编辑第几章（0-based）")


class AiArcChatRequest(AiArcIdRequest):
    arc_id: str = Field("", description="情节 id（可空=书级对话，AI 可用 new_arc 建弧）")
    messages: list[dict[str, Any]] = Field(default_factory=list)
    web_search: bool = Field(False, description="开启本对话的自动联网搜索（AnySearch 模式：助手自发检索）")
    access: dict[str, Any] | None = Field(None, description="访问控制 {book,memory,corpus,web}：控制注入多少上下文（缺省全开，web 由 web_search 决定）")
    sel_access: dict[str, Any] | None = Field(None, description="条目级选择性注入 {settings,arcs,elements,memory,corpus,templates}：只把选中的条目给助手看（在 access 允许范围内收窄）")
    mode: str = Field("normal", description="normal / init（初始化模式：裁剪 l2-l5 工具，换初始化引导 sys_p）")


class AiInitAssistantRequest(AiCreationBookRequest):
    """初始化创作助手对话请求（新建书时填充所有初始化内容）。"""
    messages: list[dict[str, Any]] = Field(default_factory=list)
    web_search: bool = Field(False)


class AiSearchRequest(AiCreationBookRequest):
    query: str = Field(..., min_length=1, description="检索问题")
    sources: list[str] | None = Field(None, description="['book','corpus','csv','refmd','template','web']，缺省全开")
    top_k: int | None = Field(None, ge=1, le=20)
    genre: str | None = Field(None, description="题材过滤（csv 源）")
    rerank: bool = Field(False, description="LLM 精排（精确模式，默认关，更准但慢）")
    smart: bool = Field(True, description="查询理解（LLM 自主判别源 + 每源子查询）；False=关键词快路径")


class AiScoreRequest(AiArcIdRequest):
    gen_text: str = Field(..., description="要评分的正文")
    chapter_idx: int | None = Field(None, description="章纲中的章（0-based，缺省当前章）")


class AiFinalizeRequest(AiArcIdRequest):
    chapter_idx: int | None = Field(None)
    force: bool = Field(False, description="v6.4 重存已落盘章（跳过防重复守卫，复用章号）")
    chapter_num: int | None = Field(None, description="v6.4 显式章号（force 重存时覆盖）")


class AiElementsPutRequest(AiCreationBookRequest):
    elements: dict[str, Any] = Field(..., description="完整元素清单")


class AiElementAddRequest(AiCreationBookRequest):
    """新增元素卡（类图卡片视图）。kind: c/i/s"""
    kind: str = Field(..., description="c=角色 i=物品 s=设定 map=地图")
    name: str = Field(..., description="卡名")
    desc: str = Field("", description="描述")
    fields: list | None = Field(None, description="[{name,value}] 自定义字段")
    relations: list | None = Field(None, description="[{to_kind,to_id,name,mult}] 关系")
    layout: list | None = Field(None, description="地图卡布局 [{zone,name,desc}]")


class AiElementUpdateRequest(AiElementAddRequest):
    """更新元素卡。id 定位，字段级覆盖。"""
    id: str = Field(..., description="元素 id")
    alias: list[str] | None = Field(None, description="别名/简称列表（角色/物品）")
    terms: list[str] | None = Field(None, description="固定叫法/专属术语（设定）")


class AiInspireChatRequest(AiCreationBookRequest):
    """灵感对话（多机制：种子推演/类比迁移/约束反转/自由对话）。"""
    messages: list[dict[str, Any]] = Field(..., description="对话历史 [{role,content}]")
    mech: str = Field("seed", description="seed|analog|invert|free")
    ctx: dict[str, Any] | None = Field(None, description="当前素材卡 {kind,name,desc,fields,relations}")


class AiInspireDepolluteRequest(AiCreationBookRequest):
    """一键去 AI 味（审阅 → 脏则重写）。"""
    text: str = Field(..., description="待去味文本")
    ctx: dict[str, Any] | None = Field(None, description="当前素材卡（可选）")


class AiPendingApproveRequest(AiCreationBookRequest):
    """同意候选时可带编辑后的值。"""
    text: str | None = Field(None, description="记忆候选编辑后的文本")
    name: str | None = Field(None, description="卡片候选编辑后的名称")
    desc: str | None = Field(None, description="卡片候选编辑后的描述")
    fields: list | None = Field(None, description="卡片候选编辑后的字段")


class AiMemoryRequest(AiCreationBookRequest):
    text: str = Field("", description="记忆文本")
    scope: str = Field("book", description="记忆范围：book / arc / element")
    key: str = Field("", description="可选记忆键（元素记忆为元素ID）")
    arc_id: str = Field("", description="弧记忆时的弧ID")
    tags: list[str] | None = Field(None, description="标签列表")


class AiFragmentRequest(BaseModel):
    """素材层 CRUD 请求（fragments.json）。"""
    book_root: str
    arc_id: str = ""
    fid: str = ""
    ftype: str = "detail"
    content: str = ""
    scene_idx: int = 0
    beat_idx: list[int] = []
    fixed: bool = True




class AiNoteRequest(BaseModel):
    """三级备注 CRUD 请求（notes.json）。"""
    book_root: str
    nid: str = ""
    scope: str = ""
    content: str = ""


class AiElementsScopeRequest(BaseModel):
    """元素作用域变更请求。"""
    book_root: str
    kind: str
    eid: str
    scope: str
    arc_name: str = ""


class AiFragmentParseRequest(BaseModel):
    text: str = Field(..., description="半成品片段（对白+叙述+【】标记）")


class AiFragmentExpandRequest(AiFragmentParseRequest):
    directives: list[dict[str, Any]] | None = Field(
        None, description="扩写指令（understand 结果；不传则内部先理解）")


class AiFragmentFinalizeRequest(BaseModel):
    book_root: str = Field(..., min_length=1, description="主系统书根目录绝对路径")
    title: str = Field("", description="章节标题（留空自动取片段首句/片段扩写）")
    text: str = Field(..., description="原始片段（用作意图 target）")
    output: str = Field(..., description="扩写后的完整章")


@router.post("/ai-creation/init")
async def ai_creation_init(req: AiInitRequest) -> dict[str, str]:
    """异步初始化（兼容）：brief → 或 settings（结构化基本设定）→ 生成设定集+elements。
    进度/结果走 /optimize/status/{task_id} 轮询（type=ai_creation_init）。"""
    task_id = start_task("ai_creation_init", progress={"messages": ["正在生成设定集与元素清单…"]})
    _safe_start_task(task_id, _do_ai_creation_init(
        task_id, req.book_root, req.brief, req.title, req.genre, req.settings))
    return {"task_id": task_id}


async def _do_ai_creation_init(
    task_id: str, book_root: str, brief: str, title: str, genre: str,
    settings: dict[str, Any] | None,
) -> None:
    from .ai_creation import generate_settings, init_book
    try:
        tasks[task_id]["progress"]["messages"].append("LLM 提取元素与创作基调…")
        if settings:
            r = await generate_settings(book_root, settings, title=title, genre=genre)
        else:
            r = await init_book(book_root, brief, title=title, genre=genre)
        tasks[task_id]["result"] = r
        tasks[task_id]["status"] = "done" if r.get("ok") else "failed"
        if not r.get("ok"):
            tasks[task_id]["error"] = r.get("error")
    except Exception as e:  # noqa: BLE001
        tasks[task_id]["status"] = "failed"
        tasks[task_id]["error"] = str(e)[:300]


@router.get("/ai-creation/settings")
async def ai_creation_settings_get(book_root: str) -> dict[str, Any]:
    """读取书的基本设定（可编辑字段源）。"""
    from . import ai_creation as ac
    return ac.load_basic_settings(book_root)


@router.put("/ai-creation/settings")
async def ai_creation_settings_put(req: AiSettingsPutRequest) -> dict[str, Any]:
    """保存基本设定（只存字段，不触发生成）。"""
    from . import ai_creation as ac
    ac.save_basic_settings(req.book_root, req.settings)
    return {"ok": True, "settings": ac.load_basic_settings(req.book_root)}


@router.post("/ai-creation/settings/generate")
async def ai_creation_settings_generate(req: AiSettingsGenerateRequest) -> dict[str, str]:
    """异步：按基本设定生成 设定集/*.md + elements.json（可重跑）。
    进度走 /optimize/status/{task_id}（type=ai_creation_settings_generate）。"""
    task_id = start_task("ai_creation_settings_generate", progress={"messages": ["正在生成设定集与元素清单…"]})
    _safe_start_task(task_id, _do_ai_creation_settings_generate(
        task_id, req.book_root, req.settings, req.title, req.genre))
    return {"task_id": task_id}


async def _do_ai_creation_settings_generate(
    task_id: str, book_root: str, settings: dict[str, Any] | None,
    title: str, genre: str,
) -> None:
    from .ai_creation import generate_settings
    try:
        tasks[task_id]["progress"]["messages"].append("LLM 提取元素与创作基调…")
        r = await generate_settings(book_root, settings, title=title, genre=genre)
        tasks[task_id]["result"] = r
        tasks[task_id]["status"] = "done" if r.get("ok") else "failed"
        if not r.get("ok"):
            tasks[task_id]["error"] = r.get("error")
    except Exception as e:  # noqa: BLE001
        tasks[task_id]["status"] = "failed"
        tasks[task_id]["error"] = str(e)[:300]


@router.post("/ai-creation/state")
async def ai_creation_state(req: AiCreationBookRequest) -> dict[str, Any]:
    """加载书级创作状态：基本设定 + elements + arcs + fragments/notes 全量（前端工作台刷新）。"""
    from . import ai_creation as ac
    return {
        "book_root": req.book_root,
        "settings": ac.load_basic_settings(req.book_root),
        "elements": ac.load_elements(req.book_root),
        "arcs": ac.load_arcs(req.book_root),
        "fragments": ac.load_fragments(req.book_root),
        "notes": ac.load_notes(req.book_root),
    }


@router.get("/ai-creation/setting-files")
async def ai_creation_setting_files(book_root: str) -> dict[str, Any]:
    """设定集文档页：基本设定 + 设定集/*.md 文件清单（只读）。"""
    from . import ai_creation as ac
    return ac.list_setting_files(book_root)


@router.get("/ai-creation/sel-access-options")
async def ai_creation_sel_access_options(book_root: str) -> dict[str, Any]:
    """条目级选择性注入候选项：settings/arcs/memory/corpus/templates（wb-acc-sel chips 渲染）。"""
    from . import ai_creation as ac
    return ac.get_sel_access_options(book_root)


class AiShortDramaAddRequest(BaseModel):
    name: str = Field(..., min_length=1, description="短剧剧名")
    tags: list[str] | None = Field(None, description="类型标签（如 爱情/古风爱情/日久生情）")
    intro: str = Field("", description="剧情简介（红果详情界面手贴）")


@router.get("/ai-creation/short-dramas")
async def ai_creation_short_dramas_list() -> dict[str, Any]:
    """短剧参考库：红果自动采集剧名(auto) + 用户录入剧情(user)。"""
    from . import ai_creation as ac
    return ac.list_short_dramas()


@router.post("/ai-creation/short-dramas")
async def ai_creation_short_dramas_add(req: AiShortDramaAddRequest) -> dict[str, Any]:
    """录入一条用户短剧剧情（红果官方详情界面有简介，手动贴入）。"""
    from . import ai_creation as ac
    return ac.add_user_drama(req.name, req.tags, req.intro)


@router.post("/ai-creation/short-dramas/fetch")
async def ai_creation_short_dramas_fetch() -> dict[str, Any]:
    """触发红果热播剧名重新采集。"""
    from . import ai_creation as ac
    return ac.fetch_redguo_dramas()


@router.get("/ai-creation/elements")
async def ai_creation_elements_get(book_root: str) -> dict[str, Any]:
    from . import ai_creation as ac
    return ac.load_elements(book_root)


@router.put("/ai-creation/elements")
async def ai_creation_elements_put(req: AiElementsPutRequest) -> dict[str, Any]:
    from . import ai_creation as ac
    ac.save_elements(req.book_root, req.elements)
    return {"ok": True, "elements": ac.load_elements(req.book_root)}


# ── 元素作用域 PATCH ──
@router.patch("/ai-creation/elements/scope")
async def ai_creation_elements_scope(req: AiElementsScopeRequest) -> dict[str, Any]:
    from . import ai_creation as ac
    elements = ac.load_elements(req.book_root)
    coll = {"c": "characters", "i": "items", "s": "settings"}.get(req.kind, req.kind)
    for e in elements.get(coll, []):
        if e.get("id") == req.eid:
            e["scope"] = req.scope
            if req.arc_name:
                e["arc_name"] = req.arc_name
            ac.save_elements(req.book_root, elements)
            return {"ok": True}
    return {"ok": False, "error": "element not found"}


# ── 素材层 CRUD（fragments.json）──
@router.get("/ai-creation/fragments")
async def ai_creation_fragments_get(book_root: str, arc_id: str = "") -> dict[str, Any]:
    from .ai_creation import load_fragments
    items = load_fragments(book_root)
    if arc_id:
        items = [f for f in items if f.get("arc_id") == arc_id]
    return {"ok": True, "items": items}


@router.put("/ai-creation/fragments")
async def ai_creation_fragments_put(req: AiFragmentRequest) -> dict[str, Any]:
    from .ai_creation import add_fragment, update_fragment, delete_fragment
    if req.fid and not req.content:
        ok = delete_fragment(req.book_root, req.fid)
        return {"ok": ok}
    if req.fid:
        ok = update_fragment(req.book_root, req.fid, content=req.content,
                             scene_idx=req.scene_idx, beat_idx=req.beat_idx,
                             fixed=req.fixed)
        return {"ok": ok}
    frag = add_fragment(req.book_root, req.arc_id, req.ftype, req.content,
                        req.scene_idx, req.beat_idx, req.fixed)
    return {"ok": True, "fragment": frag}


# ── 三级备注 CRUD（notes.json）──
@router.get("/ai-creation/notes")
async def ai_creation_notes_get(book_root: str, scope: str = "") -> dict[str, Any]:
    from .ai_creation import load_notes
    items = load_notes(book_root)
    if scope:
        items = [n for n in items if n.get("scope", "").startswith(scope)]
    return {"ok": True, "items": items}


@router.put("/ai-creation/notes")
async def ai_creation_notes_put(req: AiNoteRequest) -> dict[str, Any]:
    from .ai_creation import add_note, delete_note
    if req.nid and not req.content:
        ok = delete_note(req.book_root, req.nid)
        return {"ok": ok}
    note = add_note(req.book_root, req.scope, req.content)
    return {"ok": True, "note": note}


# ── 元素单卡 CRUD（类图卡片视图） ──
@router.post("/ai-creation/element")
async def ai_creation_element_add(req: AiElementAddRequest) -> dict[str, Any]:
    from . import ai_creation as ac
    card = ac.add_element(req.book_root, req.kind, req.name, req.desc,
                          fields=req.fields, relations=req.relations, layout=req.layout)
    return {"ok": True, "card": card}


@router.put("/ai-creation/element/{kind}/{eid}")
async def ai_creation_element_put(kind: str, eid: str, req: AiElementUpdateRequest) -> dict[str, Any]:
    from . import ai_creation as ac
    card = ac.update_element(req.book_root, kind, eid, name=req.name, desc=req.desc,
                             fields=req.fields, relations=req.relations,
                             alias=req.alias, terms=req.terms)
    if card is None:
        return {"ok": False, "error": "元素不存在"}
    return {"ok": True, "card": card}


@router.delete("/ai-creation/element/{kind}/{eid}")
async def ai_creation_element_delete(kind: str, eid: str, book_root: str) -> dict[str, Any]:
    from . import ai_creation as ac
    ok = ac.delete_element(book_root, kind, eid)
    return {"ok": ok, "error": "" if ok else "元素不存在"}


# ── 待审批池（候选制） ──
@router.get("/ai-creation/pending")
async def ai_creation_pending_get(book_root: str) -> dict[str, Any]:
    from . import ai_creation as ac
    return {"items": ac.load_pending(book_root)}


@router.post("/ai-creation/pending/{pid}/approve")
async def ai_creation_pending_approve(pid: str, req: AiPendingApproveRequest) -> dict[str, Any]:
    from . import ai_creation as ac
    try:
        edits = {"text": req.text, "name": req.name, "desc": req.desc, "fields": req.fields}
        ok = ac.approve_pending(req.book_root, pid, {k: v for k, v in edits.items() if v is not None})
        return {"ok": ok, "error": "" if ok else "候选不存在或应用失败"}
    except Exception as e:  # noqa: BLE001
        import traceback
        return {"ok": False, "error": f"approve 异常: {e}\n{traceback.format_exc()}"}


@router.post("/ai-creation/pending/{pid}/reject")
async def ai_creation_pending_reject(pid: str, book_root: str) -> dict[str, Any]:
    from . import ai_creation as ac
    ok = ac.reject_pending(book_root, pid)
    return {"ok": ok}


@router.post("/ai-creation/pending/approve-all")
async def ai_creation_pending_approve_all(req: AiCreationBookRequest) -> dict[str, Any]:
    from . import ai_creation as ac
    n = ac.approve_all_pending(req.book_root)
    return {"ok": True, "count": n}


@router.post("/ai-creation/pending/reject-all")
async def ai_creation_pending_reject_all(book_root: str) -> dict[str, Any]:
    from . import ai_creation as ac
    n = ac.reject_all_pending(book_root)
    return {"ok": True, "count": n}


# ── 初始化助手对话记录持久化 ──
@router.get("/ai-creation/chat-sessions")
async def ai_creation_chat_sessions_get(book_root: str) -> dict[str, Any]:
    from . import ai_creation as ac
    return {"sessions": ac.load_chat_sessions(book_root)}


class ChatSessionsSaveRequest(AiCreationBookRequest):
    sessions: list[dict[str, Any]] = Field(default_factory=list, description="对话会话列表")


@router.post("/ai-creation/chat-sessions")
async def ai_creation_chat_sessions_save(req: ChatSessionsSaveRequest) -> dict[str, Any]:
    from . import ai_creation as ac
    ac.save_chat_sessions(req.book_root, req.sessions)
    return {"ok": True}


class ExtractCardsRequest(AiCreationBookRequest):
    text: str = Field(..., min_length=1, description="要提取内容的对话文本")


@router.post("/ai-creation/extract-cards")
async def ai_creation_extract_cards(req: ExtractCardsRequest) -> dict[str, Any]:
    """从对话文本中提取设定/元素/记忆，生成待审核卡片。"""
    from . import ai_creation as ac
    import traceback
    try:
        return await ac.extract_cards_from_text(req.book_root, req.text)
    except Exception as e:
        tb = traceback.format_exc()
        print(f"[extract-cards ERROR] {e}\n{tb}")
        return {"ok": False, "error": str(e), "traceback": tb[-500:]}


# ── 书级记忆（灵感工坊 + 创作助手共用，支持 scope 过滤）──
@router.get("/ai-creation/memory")
async def ai_creation_memory_get(book_root: str, scope: str | None = None, arc_id: str | None = None, key: str | None = None) -> dict[str, Any]:
    from . import ai_creation as ac
    items = ac.list_memory(book_root, scope=scope, arc_id=arc_id, key=key)
    return {"items": items}


@router.post("/ai-creation/memory")
async def ai_creation_memory_add(req: AiMemoryRequest) -> dict[str, Any]:
    from . import ai_creation as ac
    item = ac.add_memory(req.book_root, req.text, scope=req.scope, key=req.key, arc_id=req.arc_id, tags=req.tags)
    return {"ok": True, "item": item}


@router.put("/ai-creation/memory/{mid}")
async def ai_creation_memory_put(mid: str, req: AiMemoryRequest) -> dict[str, Any]:
    from . import ai_creation as ac
    ok = ac.update_memory(req.book_root, mid, req.text)
    return {"ok": ok}


@router.delete("/ai-creation/memory/{mid}")
async def ai_creation_memory_delete(mid: str, book_root: str) -> dict[str, Any]:
    from . import ai_creation as ac
    ok = ac.delete_memory(book_root, mid)
    return {"ok": ok}


# ── 灵感对话（多机制）+ 一键去 AI 味 ──
@router.post("/ai-creation/inspire/chat")
async def ai_creation_inspire_chat(req: AiInspireChatRequest) -> dict[str, Any]:
    from . import ai_creation as ac
    return await ac.inspire_chat(req.book_root, req.messages, mech=req.mech, ctx=req.ctx)


@router.post("/ai-creation/inspire/depollute")
async def ai_creation_inspire_depollute(req: AiInspireDepolluteRequest) -> dict[str, Any]:
    """一键去 AI 味：审阅 → 脏则带反馈块重写 → 返回改写版。"""
    from . import ai_creation as ac
    return await ac.inspire_depollute(req.text, req.book_root, req.ctx)


@router.post("/ai-creation/arc/new")
async def ai_creation_arc_new(req: AiNewArcRequest) -> dict[str, Any]:
    """新建一条剧情情节（l1 极简剧情 → state；carry_prev 时带入上一情节结局锚点）。"""
    from . import ai_creation as ac
    try:
        return await ac.new_arc(
            req.book_root, l1=req.l1, n_chapters=req.n_chapters,
            style=req.style, role_setting=req.role_setting,
            selected=req.selected, carry_prev=req.carry_prev,
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


@router.post("/ai-creation/arc/update")
async def ai_creation_arc_update(req: AiArcUpdateRequest) -> dict[str, Any]:
    """更新弧元信息（name / status / start_chapter 等顶层字段）。"""
    from . import ai_creation as ac
    arc = ac.update_arc_meta(
        req.book_root, req.arc_id,
        name=req.name, status=req.status, start_chapter=req.start_chapter,
    )
    if arc is None:
        raise HTTPException(status_code=404, detail="弧不存在")
    return {"ok": True, "arc": arc}


@router.post("/ai-creation/arc/capacity")
async def ai_creation_arc_capacity(req: AiArcCapacityRequest) -> dict[str, Any]:
    """【容量诊断】估算 l1 能支撑多少字/章 + 三维诊断（密度/内容/质量）。

    传 l1（书级）或 arc_id（取弧内 l1）。0-1 次 LLM（extract_key_facts 按 l1 缓存）。
    """
    from . import ai_creation as ac
    from .capacity import diagnose_arc_capacity

    l1 = (req.l1 or "").strip()
    style = role_setting = ""
    if not l1 and req.arc_id:
        arcs = ac.load_arcs(req.book_root)
        arc = ac._find_arc(arcs, req.arc_id)
        if not arc:
            raise HTTPException(status_code=404, detail="弧不存在")
        l1 = str(arc.get("l1") or "").strip()
        st = arc.get("state") or {}
        style = str(st.get("style") or "")
        role_setting = str(st.get("role_setting") or "")
    if not l1:
        raise HTTPException(status_code=422, detail="l1 为空：传 l1 或 arc_id")
    try:
        diag = await diagnose_arc_capacity(
            l1, style=style, role_setting=role_setting,
            n_chapters=req.n_chapters)
    except RuntimeError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return {"ok": True, "l1": l1, "diagnosis": diag}


@router.post("/ai-creation/arc/enrich")
async def ai_creation_arc_enrich(req: AiArcCapacityRequest) -> dict[str, Any]:
    """【补齐提案】按诊断补齐 l1（不落盘，返回补齐版供前端确认）。1 次 LLM。"""
    from . import ai_creation as ac
    from .capacity import diagnose_arc_capacity, enrich_l1

    l1 = (req.l1 or "").strip()
    style = role_setting = ""
    if not l1 and req.arc_id:
        arcs = ac.load_arcs(req.book_root)
        arc = ac._find_arc(arcs, req.arc_id)
        if not arc:
            raise HTTPException(status_code=404, detail="弧不存在")
        l1 = str(arc.get("l1") or "").strip()
        st = arc.get("state") or {}
        style = str(st.get("style") or "")
        role_setting = str(st.get("role_setting") or "")
    if not l1:
        raise HTTPException(status_code=422, detail="l1 为空：传 l1 或 arc_id")
    try:
        diag = await diagnose_arc_capacity(l1, style=style, role_setting=role_setting,
                                           n_chapters=req.n_chapters)
        filled = await enrich_l1(l1, diag, style=style, role_setting=role_setting,
                                 max_chars=req.max_chars)
    except RuntimeError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return {"ok": True, "original": l1, "filled": filled}


@router.post("/ai-creation/arc/delete")
async def ai_creation_arc_delete(req: AiArcIdRequest) -> dict[str, Any]:
    """删除一条情节弧。"""
    from . import ai_creation as ac
    return ac.delete_arc(req.book_root, req.arc_id)


class AiDeleteChapterRequest(AiArcIdRequest):
    idx: int = Field(0, description="章索引（从 0 开始）")


@router.post("/ai-creation/arc/delete-chapter")
async def ai_creation_arc_delete_chapter(req: AiDeleteChapterRequest) -> dict[str, Any]:
    """删除本情节的某一章（清下游 l4/l5）。"""
    from . import ai_creation as ac
    return ac.delete_chapter(req.book_root, req.arc_id, req.idx)


@router.post("/ai-creation/arc/set-template")
async def ai_creation_arc_set_template(req: AiArcSetTemplateRequest) -> dict[str, Any]:
    """套用/清除本情节的情节模板（template_id="" 清除；变更后 l2-l5 重生成）。"""
    from . import ai_creation as ac
    return ac.arc_set_template(req.book_root, req.arc_id, req.template_id)


@router.post("/ai-creation/arc/select")
async def ai_creation_arc_select(req: AiArcSelectRequest) -> dict[str, Any]:
    """为本情节选择参与元素（每情节一次；变化后 l3 起下游强制重生成）。"""
    from . import ai_creation as ac
    return ac.arc_select(req.book_root, req.arc_id, req.selected)


@router.post("/ai-creation/arc/step")
async def ai_creation_arc_step(req: AiArcIdRequest) -> dict[str, Any]:
    """分步确认制：从当前已确认最高级生成下一级（注入元素白名单/前文锚点）。"""
    from . import ai_creation as ac
    try:
        return await ac.arc_step(req.book_root, req.arc_id)
    except RuntimeError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


@router.post("/ai-creation/arc/confirm")
async def ai_creation_arc_confirm(req: AiArcConfirmRequest) -> dict[str, Any]:
    from . import ai_creation as ac
    return ac.arc_confirm(req.book_root, req.arc_id, req.level)


@router.post("/ai-creation/arc/active-chapter")
async def ai_creation_arc_active_chapter(req: AiActiveChapterRequest) -> dict[str, Any]:
    """多章章纲：切换当前下钻的章（清空该章下游 l4/l5）。"""
    from . import ai_creation as ac
    return ac.arc_set_active_chapter(req.book_root, req.arc_id, req.idx)


@router.post("/ai-creation/arc/finish")
async def ai_creation_arc_finish(req: AiArcIdRequest) -> dict[str, Any]:
    """标记情节为已完成（下一情节 carry_prev 时作为前文锚点来源）。"""
    from . import ai_creation as ac
    return ac.arc_finish(req.book_root, req.arc_id)


@router.post("/ai-creation/arc/set-level")
async def ai_creation_arc_set_level(req: AiSetLevelRequest) -> dict[str, Any]:
    """直接编辑任一级（l1-l5）——三栏平行工作台的可编辑能力。"""
    from . import ai_creation as ac
    return ac.arc_set_level(req.book_root, req.arc_id, req.level, req.text,
                            data=req.data, idx=req.idx)


@router.post("/ai-creation/arc/regenerate-l5")
async def ai_creation_arc_regenerate_l5(req: AiArcIdRequest) -> dict[str, Any]:
    """l5 污染修正重生成：双检 → 命中未参与元素 → 注入修正块重生成正文。"""
    from . import ai_creation as ac
    try:
        return await ac.arc_regenerate_l5(req.book_root, req.arc_id)
    except RuntimeError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


@router.post("/ai-creation/arc/modify")
async def ai_creation_arc_modify(req: AiArcModifyRequest) -> dict[str, Any]:
    from . import ai_creation as ac
    try:
        return await ac.arc_modify(req.book_root, req.arc_id, req.level, req.instruction)
    except RuntimeError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


@router.post("/ai-creation/arc/chat")
async def ai_creation_arc_chat(req: AiArcChatRequest) -> dict[str, Any]:
    from . import ai_creation as ac
    try:
        return await ac.arc_chat(req.book_root, req.arc_id, req.messages, web_search=req.web_search, access=req.access, sel_access=req.sel_access, mode=req.mode)
    except RuntimeError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


@router.post("/ai-creation/init/assistant")
async def ai_creation_init_assistant(req: AiInitAssistantRequest) -> dict[str, Any]:
    """【初始化创作助手】薄封装：arc_chat 的 init 模式（书级对话，arc_id 空）。

    新建书时前端走本端点，复用创作助手完整框架（工具循环/记忆/检索），
    只换初始化 sys_p + 裁剪 l2-l5 工具。返回与 arc_chat 相同（reply/tool_events/changed）。
    """
    from . import ai_creation as ac
    try:
        return await ac.arc_chat(
            req.book_root, "", req.messages, web_search=req.web_search, mode="init")
    except RuntimeError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


@router.get("/ai-creation/init/status")
async def ai_creation_init_status(book_root: str) -> dict[str, Any]:
    """初始化状态：{initialized, stage, has_settings, has_arcs}（前端判断是否进初始化模式）。"""
    from . import ai_creation as ac
    settings = ac.load_basic_settings(book_root)
    arcs = ac.load_arcs(book_root)
    has_settings = bool(str(settings.get("generated_at") or "").strip())
    has_arcs = bool(arcs.get("arcs"))
    return {
        "initialized": has_settings and has_arcs,
        "stage": "settings" if not has_settings else ("arcs" if not has_arcs else "done"),
        "has_settings": has_settings,
        "has_arcs": has_arcs,
    }


class AiArcChatApplyRequest(AiCreationBookRequest):
    arc_id: str = Field("", description="情节 id（可空=书级，如 new_arc）")
    tool: str = Field(..., min_length=1)
    args: dict[str, Any] = Field(default_factory=dict)
    mode: str = Field("normal", description="normal / init（初始化模式）")


@router.post("/ai-creation/arc/chat/apply")
async def ai_creation_arc_chat_apply(req: AiArcChatApplyRequest) -> dict[str, Any]:
    """用户同意后执行创作助手提案的动作（dry_run=False 真执行）。"""
    from . import ai_creation as ac
    from pathlib import Path
    arcs = ac.load_arcs(req.book_root)
    arc = ac._find_arc(arcs, req.arc_id) if req.arc_id else None
    elements = ac.load_elements(req.book_root)
    ev, _ = await ac._execute_chat_tool(req.tool, req.args or {}, Path(req.book_root), arc, elements, dry_run=False, mode=req.mode)
    return {"ok": True, "event": ev, "arc_id": req.arc_id}


@router.post("/ai-creation/search")
async def ai_creation_search(req: AiSearchRequest) -> dict[str, Any]:
    """写书讨论检索：六源联邦（本书/语料/参考资料CSV/参考文档/情节模板/外部）+ 可选 LLM 精排。"""
    from . import writing_search as ws
    from .workbench_logger import set_workbench_ctx

    set_workbench_ctx(book=req.book_root, step="writing_search")
    return await ws.search_around(
        req.query, req.book_root, sources=req.sources, top_k=req.top_k or 6,
        genre=req.genre, rerank=req.rerank, smart=req.smart,
    )


_corpus_rebuild_task_refs: set[asyncio.Task] = set()


@router.post("/ai-creation/search/rebuild")
async def ai_creation_search_rebuild(req: AiCreationBookRequest) -> dict[str, Any]:
    """重建检索索引：本书快（同步）；语料慢 → 后台任务（体检面板轮询 sources 进度）。"""
    from . import writing_search as ws
    book = await ws.build_book_index(req.book_root)

    async def _run() -> None:
        try:
            await ws.build_corpus_index()
        finally:
            _corpus_rebuild_task_refs.discard(asyncio.current_task())

    task = asyncio.create_task(_run())
    _corpus_rebuild_task_refs.add(task)
    return {"ok": True, "book": book, "corpus": {"status": "building"}}


@router.get("/ai-creation/search/sources")
async def ai_creation_search_sources(book_root: str) -> dict[str, Any]:
    """检索体检：各源健康详情（哪些空/哪些有问题）+ 整体 summary。"""
    from . import writing_search as ws
    return ws.source_status(book_root)


# ── 工作台完整日志查询（logs/workbench/<书>/<日期>.jsonl） ──
@router.get("/ai-creation/logs/books")
async def ai_creation_logs_books() -> dict[str, Any]:
    """列出有工作台日志的书 + 最新日期 + 条数。"""
    from .workbench_logger import list_books
    return {"ok": True, "books": list_books()}


@router.get("/ai-creation/logs/list")
async def ai_creation_logs_list(
    book: str = "",
    date: str = "",
    call_type: str = "",
    limit: int = 100,
) -> dict[str, Any]:
    """列出工作台 trace 摘要（最新在前）：seq/ts/call_type/model/status/latency/长度。"""
    from .workbench_logger import list_traces
    return {"ok": True, "traces": list_traces(book, date, call_type, limit)}


@router.get("/ai-creation/logs/detail")
async def ai_creation_logs_detail(
    book: str = "",
    date: str = "",
    seq: int = 0,
) -> dict[str, Any]:
    """读取单条完整 trace（含完整 system/user/output）。"""
    from .workbench_logger import read_trace
    trace = read_trace(book, date, seq)
    if trace is None:
        raise HTTPException(status_code=404, detail="trace 不存在（检查 book/date/seq）")
    return {"ok": True, "trace": trace}


@router.get("/ai-creation/logs/summary")
async def ai_creation_logs_summary(
    book: str = "",
    date: str = "",
) -> dict[str, Any]:
    """工作台日志聚合：按 call_type 统计次数/平均延迟/token/错误。"""
    from .workbench_logger import summary
    return {"ok": True, "summary": summary(book, date)}


@router.post("/ai-creation/chapter/score")
async def ai_creation_chapter_score(req: AiScoreRequest) -> dict[str, Any]:
    """每章双评分：意图兑现分 + 纯质量分（含元素合规）。"""
    from . import ai_creation as ac
    return await ac.score_arc_chapter(
        req.book_root, req.arc_id, req.gen_text, chapter_idx=req.chapter_idx,
    )


@router.get("/connections/overview")
async def connections_overview() -> dict[str, Any]:
    """连接可视化全景（炼工台统一界面）：prompt-harness 训练产出 → 主系统消费。

    返回：
    - invariants: l1-l5 不变 prompt（注入点/长度/全文）
    - baseline_guard: l5 正文侧固定防线
    - consumption: 主系统各书 arcs 的 state.template（书→弧→模板→相似度）
    - recent_matches: 最近 plot_template_match 命中记录（workbench 日志）
    - counts: 语料文件 / 模板 / 消费 计数
    - log_summary: 工作台日志聚合
    """
    from .plot_library import get_plot_template_library
    from .fixed_prompts import get_ladder_invariant, get_baseline_guard
    from .workbench_logger import _iter_files, summary as _wb_summary

    # ── 1. 五级不变 prompt 注入链 ──
    INJECTIONS = {
        "l1": "用户手填 / 批量 _auto_arc_brief 拼一句话",
        "l2": "bridge.step_ladder · 极简→情节概要",
        "l3": "step_ladder · 概要→章核心（拆 N 章）",
        "l4": "_arc_to_scenes · 章核心→场景分解",
        "l5": "build_scene_prompt · 场景→正文",
    }
    invariants: list[dict[str, Any]] = []
    for lv in ("l1", "l2", "l3", "l4", "l5"):
        txt = get_ladder_invariant(lv) or ""
        invariants.append({
            "level": lv, "length": len(txt), "text": txt,
            "injection": INJECTIONS.get(lv, ""),
        })
    guard = get_baseline_guard() or ""
    baseline = {
        "length": len(guard), "text": guard,
        "injection": "generate_chapter_prose · system = baseline_guard + 场景 prompt",
    }

    # ── 2. 主系统消费：扫 小说系统/<书>/.ainovel/arcs.json 的 state.template ──
    consumption: list[dict[str, Any]] = []
    novels_base = Path(__file__).resolve().parents[3]  # .../小说系统
    if novels_base.is_dir():
        for bdir in sorted(novels_base.iterdir()):
            if not bdir.is_dir():
                continue
            arc_file = bdir / ".ainovel" / "arcs.json"
            if not arc_file.is_file():
                continue
            try:
                arcs = json.loads(arc_file.read_text(encoding="utf-8")).get("arcs", [])
            except Exception:
                continue
            for a in arcs:
                st = a.get("state") or {}
                tpl = st.get("template")
                if not isinstance(tpl, dict) or not tpl.get("id"):
                    continue
                consumption.append({
                    "book": bdir.name,
                    "arc_id": a.get("id"),
                    "arc_name": a.get("name"),
                    "template_id": tpl.get("id"),
                    "template_name": tpl.get("name"),
                    "archetype": tpl.get("archetype"),
                    "similarity": st.get("template_sim"),
                })

    # ── 3. 最近命中记录（【2026-08-22】改查 template_hits 表；旧实现全量扫
    #    workbench 日志过滤 plot_template_match，对 199MB 文件是 O(全部字节) 扫盘）──
    recent_matches: list[dict[str, Any]] = []
    try:
        from .ainovel_db import get_conn as _hit_conn
        for r in _hit_conn().execute(
            "SELECT ts,book,arc_name,template_name,score,matched_via "
            "FROM template_hits ORDER BY ts DESC LIMIT 10"):
            recent_matches.append({
                "book": r["book"] or "",
                "date": (r["ts"] or "")[:10],
                "ts": r["ts"] or "",
                "user": r["arc_name"] or "",
                "matched": [r["template_name"]] if r["template_name"] else [],
                "status": "success",
                "latency_ms": 0,
                "similarity": r["score"],
                "matched_via": r["matched_via"] or "",
            })
    except Exception:
        recent_matches = []

    # ── 4. 计数 + 日志聚合 ──
    try:
        tpl_count = len(get_plot_template_library().list())
    except Exception:
        tpl_count = 0
    corpus_files: list[Path] = []
    if SETTINGS.corpus_dir and SETTINGS.corpus_dir.is_dir():
        corpus_files = [p for p in SETTINGS.corpus_dir.rglob("*.txt") if p.is_file()]
    wb_sum = _wb_summary()

    return {
        "ok": True,
        "overview": {
            "invariants": invariants,
            "baseline_guard": baseline,
            "consumption": consumption,
            "recent_matches": recent_matches,
            "counts": {
                "template_count": tpl_count,
                "corpus_file_count": len(corpus_files),
                "consumption_count": len(consumption),
            },
            "log_summary": {
                "total_calls": wb_sum.get("total_calls", 0),
                "total_tokens": wb_sum.get("total_tokens", 0),
                "error_count": wb_sum.get("error_count", 0),
                "by_call_type": wb_sum.get("by_call_type", {}),
            },
        },
    }


@router.post("/ai-creation/chapter/pollution")
async def ai_creation_chapter_pollution(req: AiScoreRequest) -> dict[str, Any]:
    """元素污染完整双检（确定性扫描 + LLM 复核）。"""
    from . import ai_creation as ac
    return await ac.check_arc_pollution(req.book_root, req.arc_id, req.gen_text)


@router.post("/ai-creation/chapter/finalize")
async def ai_creation_chapter_finalize(req: AiFinalizeRequest) -> dict[str, Any]:
    """把当前章 l5 正文落盘（章纲/正文/评分报告 → 主系统书结构），记录进情节。"""
    from . import ai_creation as ac
    try:
        return await ac.finalize_chapter(
            req.book_root, req.arc_id, chapter_idx=req.chapter_idx,
            force=req.force, chapter_num=req.chapter_num,
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


# ═══════════════════════════════════════════════════════════════════════
# v6.5 片段锚定扩写：用户半成品片段（对白+叙述+【】标记）→ 只扩写【】→ 完整章
# 铁律：只扩写【】内的内容，锚点逐字保留；LLM 先理解【】内容再扩写。
# ═══════════════════════════════════════════════════════════════════════

@router.post("/ai-creation/fragment/parse")
async def ai_fragment_parse(req: AiFragmentParseRequest) -> dict[str, Any]:
    """确定性解析片段 → 锚点 + 槽位（零 LLM，理解前置：先看清有哪些【】要扩写）。"""
    from . import fragment_expand as fe
    return fe.parse_fragment(req.text)


@router.post("/ai-creation/fragment/understand")
async def ai_fragment_understand(req: AiFragmentParseRequest) -> dict[str, Any]:
    """LLM 先理解每个【】的内容 → 扩写指令（intent/requirements/constraints）。

    用户要求「llm先理解【】内的内容再工作」：这一步产出可审阅的扩写指令，
    前端展示后用户可确认/修改，再进入扩写。
    """
    from . import fragment_expand as fe
    parsed = fe.parse_fragment(req.text)
    if not parsed.get("ok"):
        return parsed
    return await fe.understand_slots(req.text, parsed["slots"])


@router.post("/ai-creation/fragment/expand")
async def ai_fragment_expand(req: AiFragmentExpandRequest) -> dict[str, Any]:
    """片段 → 完整章（理解→逐槽位扩写→确定性拼接→校验）。

    directives 不传时内部先 understand；返回 output/fills/slots/directives/verify
    （verify.anchor_fidelity 保证锚点逐字，coverage 保证槽位全覆盖）。
    """
    from . import fragment_expand as fe
    return await fe.expand_fragment(req.text, req.directives)


@router.post("/ai-creation/fragment/finalize")
async def ai_fragment_finalize(req: AiFragmentFinalizeRequest) -> dict[str, Any]:
    """片段扩写结果落盘当前书（合成情节 → 章纲/正文/评分报告），意图 target=原始片段。"""
    from . import fragment_expand as fe
    try:
        return await fe.finalize_fragment(
            req.book_root, req.text, req.output, title=req.title)
    except RuntimeError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


class AiBatchGenerateRequest(AiCreationBookRequest):
    target_chapters: int = Field(10, ge=1, le=500, description="目标总章数")
    n_chapters_per_arc: int = Field(3, ge=1, le=20, description="每情节章数")
    arc_briefs: list[str] | None = Field(None, description="逐情节一句话极简剧情（缺省自动生成）")
    select_all_elements: bool = Field(True, description="批量模式每情节默认全选元素（隔离不拒）")
    resume_run_id: str | None = Field(None, description="续跑：复用上次中断的 run_id（已落盘章节自动跳过）")


@router.post("/ai-creation/batch-generate")
async def ai_creation_batch_generate(req: AiBatchGenerateRequest) -> dict[str, str]:
    """🚀 批量生成全书：自动驱动逐弧流水线（新建弧→选元素→l2→l3→逐章 l4/l5→评分→落盘→finish）。

    进度走 /optimize/status/{task_id} 轮询（type=ai_creation_batch_generate，
    progress={total,target,done,arc_index,arc_name,step,last_chapter}）。可取消。
    续跑：传 resume_run_id，已落盘章节自动跳过，过程记录追加到同一 rollout。
    """
    from . import rollout
    if req.resume_run_id:
        meta = rollout.load_run_meta(req.book_root, req.resume_run_id)
        if meta is None:
            raise HTTPException(status_code=404, detail=f"run {req.resume_run_id} not found")
        if not meta.get("stale_running"):
            raise HTTPException(
                status_code=400,
                detail=f"run 已结束（{meta.get('last_event')}），无需续跑")
    task_id = start_task("ai_creation_batch_generate", progress={
        "messages": ["正在初始化批量生成…"],
        "total": 0, "target": req.target_chapters,
        "done": 0, "arc_index": 0, "arc_name": "", "step": "", "last_chapter": 0,
        **({"resume_run_id": req.resume_run_id} if req.resume_run_id else {})})
    if req.resume_run_id:
        tasks[task_id]["run_id"] = req.resume_run_id
    _safe_start_task(task_id, _do_ai_creation_batch_generate(
        task_id, req.book_root, req.target_chapters,
        req.n_chapters_per_arc, req.arc_briefs, req.select_all_elements))
    return {"task_id": task_id}


async def _do_ai_creation_batch_generate(
    task_id: str, book_root: str, target_chapters: int,
    n_chapters_per_arc: int, arc_briefs: list[str] | None,
    select_all_elements: bool,
) -> None:
    """批量逐弧驱动：复用 ai_creation 内部函数，串行跑到目标章数。"""
    from . import ai_creation as ac
    from . import rollout
    from .workbench_logger import set_workbench_ctx, new_run_id

    # 工作台日志上下文：整批一个 run_id，book 贯穿；ai_creation 驱动函数再补 arc/step
    set_workbench_ctx(book=book_root, step="batch_generate", run_id=new_run_id())

    # ── Rollout：append-only 运行记录（断点续跑 + 审计）──────────────
    # 若调用方传了 run_id（续跑）则复用，否则新建。每次批量生成的参数、
    # 每步进度、终态都追加到 <书根>/.ainovel/runs/<run_id>.jsonl。
    run_id = tasks[task_id].get("run_id") or rollout.new_run_id()
    tasks[task_id]["run_id"] = run_id
    rollout.append_event(
        book_root, run_id, "run_started", kind="batch_generate",
        data={"target_chapters": target_chapters,
              "n_chapters_per_arc": n_chapters_per_arc,
              "select_all_elements": select_all_elements,
              "arc_briefs": arc_briefs or []})

    def _progress(**kw):
        p = tasks[task_id]["progress"]
        p.update(kw)
        p["messages"] = p.get("messages") or []
        if kw.get("step"):
            p["messages"].append(f"[{p.get('done', 0)}/{target_chapters}] {kw['step']}")
            # rollout 事件（每步追加，失败静默）
            rollout.append_event(
                book_root, run_id, "step_completed", kind="batch_generate",
                step=kw["step"], data={"done": p.get("done", 0), "target": target_chapters})

    try:
        elements = ac.load_elements(book_root)
        arcs = ac.load_arcs(book_root)
        existing = len([1 for a in arcs.get("arcs", []) for _ in a.get("chapters", [])])
        _progress(total=existing, done=existing)

        # 全选元素列表（按 kind 去重取 id）
        all_selected: dict[str, list[str]] = {"characters": [], "items": [], "settings": []}
        for kind in ("characters", "items", "settings"):
            all_selected[kind] = [str(e.get("id")) for e in (elements.get(kind) or [])]

        briefs = [b for b in (arc_briefs or []) if str(b or "").strip()]
        arc_i = 0
        guard = 0
        while existing < target_chapters and guard < 100:
            guard += 1
            # 1. 一句话 l1：有 briefs 用之，否则用基本设定拼
            if arc_i < len(briefs):
                l1 = briefs[arc_i]
                arc_i += 1
            else:
                l1 = ac._auto_arc_brief(book_root, arc_i + 1)
            if not l1:
                _progress(step="无可用剧情 brief，停止")
                break

            # 2. 新建弧（carry_prev 自动衔接上一情节）
            res = await ac.new_arc(
                book_root, l1=l1, n_chapters=n_chapters_per_arc,
                carry_prev=True,
                selected=(all_selected if select_all_elements else None),
            )
            if not res.get("ok"):
                _progress(step=f"new_arc 失败: {res.get('error')}")
                break
            arc_id = res["arc"]["id"]
            arc_name = res["arc"].get("name", "")
            _progress(arc_index=arc_i + 1, arc_name=arc_name,
                      step=f"新建情节「{arc_name}」")

            # 3. 逐级推进 l2 → l3 → 逐章 l4/l5 → 落盘
            #    单步 LLM 调用带超时（l4 场景分解是慢调用给 300s，其余 180s）防超长响应卡死；
            #    单章失败跳过继续，不废整本
            import asyncio as _asyncio

            _STEP_TIMEOUT = {"l2": 180, "l3": 180, "l4": 300, "l5": 180}

            async def _step_timeout(label: str, want: str = "l5") -> dict | None:
                try:
                    return await _asyncio.wait_for(
                        ac.arc_step(book_root, arc_id),
                        timeout=_STEP_TIMEOUT.get(want, 180))
                except _asyncio.TimeoutError:
                    _progress(step=f"{label} LLM 超时（>{_STEP_TIMEOUT.get(want, 180)}s），跳过本章")
                    return None

            l3_ok = True
            for want in ("l2", "l3"):
                r = await _step_timeout(want, want)
                if r is None:
                    l3_ok = False
                    break
                if not r.get("ok"):
                    _progress(step=f"{want} 生成失败: {r.get('error')}")
                    l3_ok = False
                    break
                cr = ac.arc_confirm(book_root, arc_id, want)
                if not cr.get("ok"):
                    _progress(step=f"{want} 确认失败: {cr.get('error')}")
                    l3_ok = False
                    break
                _progress(step=f"{want} 完成")
            if not l3_ok:
                ac.arc_finish(book_root, arc_id)
                _progress(step=f"情节「{arc_name}」l2/l3 异常，跳过本章")
                continue

            for idx in range(n_chapters_per_arc):
                if existing >= target_chapters:
                    break
                if n_chapters_per_arc > 1:
                    r = ac.arc_set_active_chapter(book_root, arc_id, idx)
                    if not r.get("ok"):
                        _progress(step=f"第{idx + 1}章 切章失败: {r.get('error')}")
                        break
                ok = True
                for want in ("l4", "l5"):
                    r = await _step_timeout(f"第{idx + 1}章 {want}", want)
                    if r is None:
                        ok = False
                        break
                    if not r.get("ok"):
                        _progress(step=f"第{idx + 1}章 {want} 失败: {r.get('error')}")
                        ok = False
                        break
                    cr = ac.arc_confirm(book_root, arc_id, want)
                    if not cr.get("ok"):
                        _progress(step=f"第{idx + 1}章 {want} 确认失败: {cr.get('error')}")
                        ok = False
                        break
                if not ok:
                    _progress(step=f"第{idx + 1}章 生成异常，跳过该章")
                    continue
                try:
                    fr = await _asyncio.wait_for(
                        ac.finalize_chapter(book_root, arc_id, chapter_idx=idx),
                        timeout=180)
                except _asyncio.TimeoutError:
                    _progress(step=f"第{idx + 1}章 落盘超时，跳过")
                    continue
                if not fr.get("ok"):
                    _progress(step=f"第{idx + 1}章 落盘失败: {fr.get('error')}")
                    continue
                existing += 1
                _progress(done=existing, last_chapter=fr.get("chapter", 0),
                          step=f"第{idx + 1}章 落盘 ✓（第{fr.get('chapter', 0):04d}章）")

            ac.arc_finish(book_root, arc_id)
            _progress(step=f"情节「{arc_name}」完成")

        tasks[task_id]["result"] = {"chapters": existing, "target": target_chapters}
        tasks[task_id]["status"] = "done" if existing >= target_chapters else "stopped"
        rollout.append_event(
            book_root, run_id,
            "run_completed" if existing >= target_chapters else "run_aborted",
            kind="batch_generate",
            data={"chapters": existing, "target": target_chapters})
    except Exception as e:  # noqa: BLE001
        tasks[task_id]["status"] = "failed"
        tasks[task_id]["error"] = f"{type(e).__name__}: {e}"[:600]
        _progress(step=f"批量生成失败: {tasks[task_id]['error']}")
        rollout.append_event(
            book_root, run_id, "run_failed", kind="batch_generate",
            data={"error": tasks[task_id]["error"]})


class RunsRequest(AiCreationBookRequest):
    kind: str = Field("batch_generate", description="只列该 kind 的 run")
    limit: int = Field(20, ge=1, le=100)


@router.post("/ai-creation/runs")
async def ai_creation_runs(req: RunsRequest) -> dict[str, Any]:
    """列出书内最近的批量生成 rollout（含末态，供断点续跑 UI 展示）。"""
    from . import rollout
    runs = rollout.list_runs(req.book_root, limit=req.limit)
    if req.kind:
        runs = [r for r in runs if r.get("kind") == req.kind]
    resumable = rollout.find_resumable(req.book_root, req.kind)
    return {"ok": True, "runs": runs,
            "resumable": resumable["run_id"] if resumable else None}


class RunDetailRequest(AiCreationBookRequest):
    run_id: str = Field(..., min_length=1)


@router.post("/ai-creation/run-detail")
async def ai_creation_run_detail(req: RunDetailRequest) -> dict[str, Any]:
    """单个 run 的完整事件流（正序，供审计/排查）。"""
    from . import rollout
    path = rollout._run_path(req.book_root, req.run_id)
    if not path.is_file():
        raise HTTPException(status_code=404, detail="run not found")
    events = list(rollout.reverse_scan(path, max_records=0))  # 倒扫全部
    return {"ok": True, "run_id": req.run_id, "count": len(events),
            "events": list(reversed(events))}


# ---------------------------------------------------------------------------
# Static files + SPA routing
# ---------------------------------------------------------------------------

# 前端 dist 目录（v5.2 起：统一一份，不再双 dist）
# 查找优先级：
# 1. AINOVEL_FRONTEND_DIST 环境变量（显式指定）
# 2. 主系统源码目录：app/dashboard/frontend/dist/（开发模式）
# 3. SETTINGS.data_dir.parent/frontend/dist/（兼容旧路径）
def _resolve_frontend_dist() -> Path | None:
    import os
    from pathlib import Path

    env_dist = os.environ.get("AINOVEL_FRONTEND_DIST")
    if env_dist:
        p = Path(env_dist).resolve()
        if p.is_dir():
            return p

    # 尝试从源码位置推断主系统 dist
    # server.py 位置：prompt-harness/prompt_harness/server.py
    # 主系统 dist 位置：../../app/dashboard/frontend/dist/
    try:
        src_dist = Path(__file__).resolve().parents[2] / "app" / "dashboard" / "frontend" / "dist"
        if src_dist.is_dir():
            return src_dist
    except Exception:
        pass

    # 兼容旧路径
    old_dist = SETTINGS.data_dir.parent / "frontend" / "dist"
    if old_dist.is_dir():
        return old_dist

    return None

_frontend_dist = _resolve_frontend_dist()


# ---------------------------------------------------------------------------
# Standalone FastAPI app (for 独立运行模式 / 8777 端口)
# ---------------------------------------------------------------------------
# 注意：主系统集成模式下，router 被挂载到主 app 上，这里的 app 不会被使用。
# 独立运行模式（python run.py / uvicorn prompt_harness.server:app）时才用。

def _create_standalone_app():
    """创建独立运行用的 FastAPI app（仅 8777 独立模式使用）。"""
    import contextlib
    from pathlib import Path
    from fastapi import FastAPI
    from fastapi.staticfiles import StaticFiles
    from fastapi.middleware.cors import CORSMiddleware
    from .config import SETTINGS as _s

    # 独立模式数据目录
    _STANDALONE_DATA_DIR = Path(__file__).resolve().parent.parent / "data"

    @contextlib.asynccontextmanager
    async def _standalone_lifespan(app: FastAPI):
        init_prompt_harness(_STANDALONE_DATA_DIR)
        yield
        await shutdown_prompt_harness()

    app = FastAPI(
        title="Prompt Harness",
        version="v3.13",
        lifespan=_standalone_lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(router)

    # 静态文件 + SPA 回退（仅独立模式需要）
    if _frontend_dist is not None:
        assets_dir = _frontend_dist / "assets"
        if assets_dir.is_dir():
            app.mount("/assets", StaticFiles(directory=str(assets_dir)), name="assets")

        @app.get("/", include_in_schema=False)
        async def _root_index():
            from fastapi.responses import FileResponse, HTMLResponse
            index = _frontend_dist / "index.html"
            if index.is_file():
                return FileResponse(str(index))
            return HTMLResponse(
                "<h2>Prompt Harness API is running</h2>"
                "<p>前端尚未构建。</p>"
                '<p>API 文档：<a href="/docs">/docs</a></p>'
            )

        @app.get("/{full_path:path}", include_in_schema=False)
        async def _spa_fallback(full_path: str):
            from fastapi.responses import FileResponse
            index = _frontend_dist / "index.html"
            if index.is_file():
                return FileResponse(str(index))
            raise HTTPException(status_code=404, detail="not found")
    else:
        @app.get("/", include_in_schema=False)
        async def _root_index_no_dist():
            from fastapi.responses import HTMLResponse
            return HTMLResponse(
                "<h2>Prompt Harness API is running</h2>"
                "<p>前端尚未构建或未找到。请在主系统（8765端口）访问。</p>"
                '<p>API 文档：<a href="/docs">/docs</a></p>'
            )

    return app


# 模块级 app — 已废弃：7777 独立端口不再使用
# 所有 prompt-harness 功能通过主应用 8765 端口的 /prompt-harness/ 路由访问
app = None


# ── 以下 router 上的 SPA 路由仅供主系统集成模式使用 ──
# （主系统的 PromptHarnessPage 通过 HashRouter 工作，
#  这些路由实际上很少触发，保留是为了兼容性）

@router.get("/{full_path:path}", include_in_schema=False)
async def spa_fallback(full_path: str) -> Any:
    """SPA 路由回退到 index.html。"""
    if _frontend_dist is None:
        raise HTTPException(status_code=404, detail="frontend dist not found")
    index = _frontend_dist / "index.html"
    if index.is_file():
        from fastapi.responses import FileResponse
        return FileResponse(str(index))
    raise HTTPException(status_code=404, detail="not found")


@router.get("/", include_in_schema=False)
async def root_index() -> Any:
    """根路径返回 index.html。"""
    if _frontend_dist is None:
        from fastapi.responses import HTMLResponse
        return HTMLResponse(
            "<h2>Prompt Harness API is running</h2>"
            "<p>前端尚未构建或未找到。请在主系统（8765端口）访问。</p>"
            '<p>API 文档：<a href="/docs">/docs</a></p>'
        )
    index = _frontend_dist / "index.html"
    if index.is_file():
        from fastapi.responses import FileResponse
        return FileResponse(str(index))
    raise HTTPException(status_code=404, detail="not found")


if __name__ == "__main__":
    print("=" * 60)
    print("  [X] prompt-harness 独立端口已废弃")
    print()
    print("  所有 prompt-harness 功能已集成到主应用 8765 端口：")
    print("  http://127.0.0.1:8765/prompt-harness/")
    print("=" * 60)
    sys.exit(1)
