"""Tier-A 单元测试：dashboard/task_manager.py 任务/SSE/挂起恢复语义。"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import pytest

PLUGIN_ROOT = Path(__file__).resolve().parents[3]
if str(PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT))

from dashboard.task_manager import TaskManager  # noqa: E402


def test_create_and_get():
    tm = TaskManager()
    t = tm.create(kind="action", label="lint")
    assert t.task_id in {x["task_id"] for x in tm.list_tasks()}
    assert tm.get(t.task_id) is t
    assert tm.get("not-exist") is None


def test_emit_appends_to_events_and_queue():
    tm = TaskManager()
    t = tm.create(kind="action", label="x")

    async def _run():
        await tm.emit(t, {"phase": "stdout", "line": "hello"})
        ev = await t.queue.get()
        return ev

    ev = asyncio.run(_run())
    assert ev["line"] == "hello"
    assert len(t.events) == 1


def test_emit_done_marks_finished_and_pushes_sentinel():
    tm = TaskManager()
    t = tm.create(kind="action", label="x")

    async def _run():
        await tm.emit_done(t, {"result": 42})
        # 第一条是 done event
        first = await t.queue.get()
        # 第二条是 sentinel None
        second = await t.queue.get()
        return first, second

    first, second = asyncio.run(_run())
    assert first["phase"] == "done"
    assert first["payload"]["result"] == 42
    assert second is None
    assert t.is_finished


def test_emit_error_marks_finished():
    tm = TaskManager()
    t = tm.create(kind="action", label="x")

    async def _run():
        await tm.emit_error(t, "boom", traceback="tb")
        return await t.queue.get(), await t.queue.get()

    err, sentinel = asyncio.run(_run())
    assert err["phase"] == "error"
    assert err["msg"] == "boom"
    assert sentinel is None
    assert t.is_finished


def test_stream_replays_history_then_pushes_live():
    tm = TaskManager()
    t = tm.create(kind="action", label="x")

    async def _run():
        # 先 emit 一条历史事件，再完成
        await tm.emit(t, {"phase": "stdout", "line": "old"})
        # 模拟有 consumer 来接
        gen = tm.stream(t.task_id)
        first_chunk = await gen.__anext__()
        assert "old" in first_chunk
        # 推一条新事件
        await tm.emit(t, {"phase": "stdout", "line": "new"})
        second_chunk = await gen.__anext__()
        assert "new" in second_chunk
        # 完成
        await tm.emit_done(t, {})
        third_chunk = await gen.__anext__()
        assert "done" in third_chunk
        # sentinel 后 generator 退出
        with pytest.raises(StopAsyncIteration):
            await gen.__anext__()

    asyncio.run(_run())


def test_stream_unknown_id_emits_error():
    tm = TaskManager()

    async def _run():
        gen = tm.stream("not-exist")
        chunk = await gen.__anext__()
        # 应当包含 error 而不是历史
        assert "error" in chunk
        with pytest.raises(StopAsyncIteration):
            await gen.__anext__()

    asyncio.run(_run())


def test_stream_for_finished_task_only_replays():
    tm = TaskManager()
    t = tm.create(kind="action", label="x")

    async def _run():
        await tm.emit(t, {"phase": "stdout", "line": "a"})
        await tm.emit_done(t, {"ok": True})
        gen = tm.stream(t.task_id)
        chunks = []
        async for c in gen:
            chunks.append(c)
        return chunks

    chunks = asyncio.run(_run())
    # 至少包含 stdout 'a' 和 done，不应永远阻塞
    assert any("stdout" in c for c in chunks)
    assert any("done" in c for c in chunks)


def test_suspend_and_resume():
    tm = TaskManager()
    t = tm.create(kind="workflow", label="init")

    async def _run():
        # 在另一个 task 里完成 suspend
        suspend_task = asyncio.create_task(
            tm.suspend_for_input(t, {"prompt": "题材？", "schema": {}})
        )
        # 让 suspend 先跑到 await
        await asyncio.sleep(0.01)
        assert t.awaiting_input is not None
        # 用 resume 喂回答
        ok = tm.resume(t.task_id, {"answer": "玄幻"})
        assert ok is True
        result = await suspend_task
        return result

    res = asyncio.run(_run())
    assert res == {"answer": "玄幻"}


def test_resume_returns_false_when_no_pending():
    tm = TaskManager()
    t = tm.create(kind="workflow", label="x")
    assert tm.resume(t.task_id, {"x": 1}) is False  # 还没 suspend
    assert tm.resume("not-exist", {}) is False


def test_summary_contains_event_count():
    tm = TaskManager()
    t = tm.create(kind="action", label="x")

    async def _run():
        await tm.emit(t, {"phase": "stdout", "line": "1"})
        await tm.emit(t, {"phase": "stdout", "line": "2"})

    asyncio.run(_run())
    summary = t.to_summary()
    assert summary["event_count"] == 2
    assert summary["is_finished"] is False
    assert summary["awaiting_input"] is None


def test_gc_drops_old_finished_when_over_limit():
    tm = TaskManager(max_tasks=3)
    tasks = []

    async def _run():
        for i in range(5):
            t = tm.create(kind="action", label=f"t{i}")
            await tm.emit_done(t, {})
            tasks.append(t)

    asyncio.run(_run())
    remaining = {item["task_id"] for item in tm.list_tasks()}
    assert len(remaining) <= 3
    # 最近创建的应留下
    assert tasks[-1].task_id in remaining
