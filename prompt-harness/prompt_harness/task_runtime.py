# -*- coding: utf-8 -*-
"""统一后台任务状态机（借鉴 codex harness 的 tasks/ 模式，骨架不动的增量收编）。

此前 server.py 各端点各自手写任务字典 + done/failed 样板 + 各自的 progress
结构；本模块把生命周期收成一处：

    running -> done | failed | cancelling -> cancelled

统一记录结构（旧字段全保留，前端/轮询方零改动）：
    {
      "task_id": str,
      "type": str,       # 兼容旧字段（= kind）
      "kind": str,       # 稳定标识：plot_extract / ai_creation_init / ...
      "status": str,     # running | done | failed | cancelling | cancelled
      "progress": dict,  # 各流程自定义（messages/rounds/budgets/percent...）
      "result": Any,
      "error": str | None,
      "created_at": iso, "started_at": iso,
      "finished_at": iso, "duration_ms": int,
    }

生命周期事件 append 到 logs/tasks/task_events.jsonl（重启后可审计；
也是后续 rollout 断点续跑的基础设施）。

用法：
    task_id = start_task(kind="plot_extract", progress={"messages": []})
    spawn_task(task_id, _do_plot_extract(task_id, ...))
    # 轮询方不变：tasks[task_id]

_do_* 协程可自行设置 result/status（现有代码不用改）；spawn_task 的
done_callback 兜底：协程正常返回但没置终态 -> done；抛异常没接住 -> failed；
被 cancel -> cancelled。无论谁置的终态，finished_at/duration_ms/事件落盘
都由这里统一完成。
"""
from __future__ import annotations

import asyncio
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

# 终态集合（到达后不再迁移）
_TERMINAL = {"done", "failed", "cancelled"}

# ── 任务注册表（server.py 的旧 `tasks` 字典由这里持有，名字不变）──────
tasks: dict[str, dict[str, Any]] = {}
_task_refs: dict[str, asyncio.Task] = {}

# ── 事件日志：logs/tasks/task_events.jsonl（懒加载，写失败静默）────────
_event_file = None
_event_date: str = ""


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _get_event_file():
    global _event_file, _event_date
    today = datetime.now(timezone.utc).strftime("%Y%m%d")
    if _event_date != today or _event_file is None:
        if _event_file is not None:
            try:
                _event_file.close()
            except Exception:
                pass
        base = Path(__file__).resolve().parent.parent / "logs" / "tasks"
        base.mkdir(parents=True, exist_ok=True)
        _event_file = open(base / f"task_events_{today}.jsonl", "a", encoding="utf-8")
        _event_date = today
    return _event_file


def _write_event(event: str, rec: dict[str, Any]) -> None:
    try:
        fh = _get_event_file()
        fh.write(json.dumps({
            "ts": _now_iso(),
            "event": event,           # start | end
            "task_id": rec.get("task_id"),
            "kind": rec.get("kind"),
            "status": rec.get("status"),
            "duration_ms": rec.get("duration_ms"),
            "error": (rec.get("error") or "")[:200] or None,
        }, ensure_ascii=False, default=str) + "\n")
        fh.flush()
    except Exception:
        pass  # 日志失败不影响任务本身


def start_task(
    kind: str,
    *,
    progress: dict[str, Any] | None = None,
    task_id: str | None = None,
) -> str:
    """注册一个后台任务（状态 running），返回 task_id。"""
    tid = task_id or uuid.uuid4().hex[:12]
    tasks[tid] = {
        "task_id": tid,
        "type": kind,        # 兼容旧字段
        "kind": kind,
        "status": "running",
        "progress": progress if progress is not None else {},
        "result": None,
        "error": None,
        "created_at": _now_iso(),
        "started_at": _now_iso(),
        "finished_at": None,
        "duration_ms": None,
    }
    return tid


def spawn_task(task_id: str, coro: Any) -> asyncio.Task:
    """启动后台协程并挂到 task_id；done_callback 统一收口终态+计时+事件。"""
    rec = tasks.get(task_id)
    if rec is None:
        # 调用方未走 start_task（旧路径直接塞 tasks[task_id]）-- 兜底补齐字段
        rec = tasks[task_id] = {
            "task_id": task_id, "type": "unknown", "kind": "unknown",
            "status": "running", "progress": {}, "result": None, "error": None,
            "created_at": _now_iso(), "started_at": _now_iso(),
            "finished_at": None, "duration_ms": None,
        }
    task = asyncio.create_task(coro)
    _task_refs[task_id] = task
    _write_event("start", rec)

    def _on_done(t: asyncio.Task) -> None:
        _task_refs.pop(task_id, None)
        r = tasks.get(task_id)
        if r is None:
            return
        if t.cancelled():
            r["status"] = "cancelled"
        elif t.exception() is not None and r.get("status") not in _TERMINAL:
            exc = t.exception()
            r["status"] = "failed"
            r["error"] = f"{type(exc).__name__}: {exc}"[:300]
        elif r.get("status") not in _TERMINAL:
            # 协程正常返回但没置终态（调用方漏写）-> 视为完成
            r["status"] = "done"
        _finish(r)
        _write_event("end", r)

    task.add_done_callback(_on_done)
    return task


def _finish(rec: dict[str, Any]) -> None:
    """置终态时间戳与耗时（幂等）。"""
    if rec.get("finished_at"):
        return
    rec["finished_at"] = _now_iso()
    try:
        t0 = datetime.fromisoformat(rec.get("started_at") or rec.get("created_at") or _now_iso())
        rec["duration_ms"] = int((datetime.now(timezone.utc) - t0).total_seconds() * 1000)
    except Exception:
        rec["duration_ms"] = None


def get_task(task_id: str) -> dict[str, Any] | None:
    return tasks.get(task_id)


def list_tasks() -> list[dict[str, Any]]:
    """任务摘要（进程调查面板用；含 timing）。"""
    return [
        {
            "task_id": tid,
            "type": info.get("type", "unknown"),
            "kind": info.get("kind", info.get("type", "unknown")),
            "status": info.get("status"),
            "created_at": info.get("created_at"),
            "finished_at": info.get("finished_at"),
            "duration_ms": info.get("duration_ms"),
            "has_ref": tid in _task_refs,
        }
        for tid, info in tasks.items()
    ]


def cancel_task(task_id: str) -> dict[str, Any]:
    """请求取消一个运行中的任务（与旧 /tasks/{id}/cancel 语义一致）。"""
    if task_id not in tasks:
        return {"ok": False, "error": "task not found"}
    rec = tasks[task_id]
    if rec.get("status") in _TERMINAL:
        return {"ok": False, "error": f"task already {rec['status']}"}
    t = _task_refs.get(task_id)
    if t is None:
        return {"ok": False, "error": "task has no active asyncio reference (already done or not cancellable)"}
    if t.done():
        return {"ok": False, "error": f"task already {rec.get('status')}"}
    if t.cancel():
        rec["status"] = "cancelling"
        return {"ok": True, "status": "cancelling"}
    return {"ok": False, "error": "cancel request failed"}


def cancel_all() -> None:
    """取消全部运行中任务（shutdown 用）。"""
    for tid, task in list(_task_refs.items()):
        task.cancel()
    _task_refs.clear()


def cleanup_finished(max_keep: int = 200) -> None:
    """保留最近 max_keep 条终态记录，防内存无限涨。"""
    if len(tasks) <= max_keep:
        return
    finished = [tid for tid, r in tasks.items() if r.get("status") in _TERMINAL]
    # created_at 字符串排序即时间序
    finished.sort(key=lambda tid: tasks[tid].get("created_at") or "")
    for tid in finished[: len(tasks) - max_keep]:
        tasks.pop(tid, None)


# server.py 旧函数名兼容（现有调用点不改）
def _safe_start_task(task_id: str, coro: Any) -> asyncio.Task:
    return spawn_task(task_id, coro)


def cancel_all_and_wait() -> Any:
    """兼容 server.py shutdown 流程的旧入口名（如有）。"""
    return cancel_all()
