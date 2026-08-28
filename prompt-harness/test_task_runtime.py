# -*- coding: utf-8 -*-
"""隔离测试：task_runtime 状态机生命周期（不调 LLM、不起服务器）。"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "prompt_harness"))
import task_runtime as tr


async def main():
    # ① 正常完成（协程自己置 done，result 保留）
    tid = tr.start_task("t_ok", progress={"messages": []})
    assert tr.tasks[tid]["status"] == "running"
    assert tr.tasks[tid]["kind"] == "t_ok" and tr.tasks[tid]["type"] == "t_ok"

    async def _ok():
        tr.tasks[tid]["result"] = {"x": 1}
        tr.tasks[tid]["status"] = "done"

    tr.spawn_task(tid, _ok())
    await asyncio.sleep(0.05)
    r = tr.get_task(tid)
    assert r["status"] == "done" and r["result"] == {"x": 1}, r
    assert r["duration_ms"] is not None and r["finished_at"], r
    print("① done + timing OK")

    # ② 协程正常返回但没置终态 -> 兜底 done
    tid = tr.start_task("t_forgot")

    async def _forgot():
        pass

    tr.spawn_task(tid, _forgot())
    await asyncio.sleep(0.05)
    assert tr.tasks[tid]["status"] == "done", tr.tasks[tid]["status"]
    print("② 兜底 done OK")

    # ③ 抛异常没接住 -> failed
    tid = tr.start_task("t_crash")

    async def _crash():
        raise ValueError("boom")

    tr.spawn_task(tid, _crash())
    await asyncio.sleep(0.05)
    assert tr.tasks[tid]["status"] == "failed", tr.tasks[tid]["status"]
    assert "ValueError" in tr.tasks[tid]["error"], tr.tasks[tid]["error"]
    print("③ 兜底 failed OK")

    # ④ 取消 -> cancelling -> cancelled
    tid = tr.start_task("t_cancel")

    async def _slow():
        await asyncio.sleep(10)

    tr.spawn_task(tid, _slow())
    res = tr.cancel_task(tid)
    assert res["ok"] and tr.tasks[tid]["status"] == "cancelling", (res, tr.tasks[tid]["status"])
    await asyncio.sleep(0.05)
    assert tr.tasks[tid]["status"] == "cancelled", tr.tasks[tid]["status"]
    assert tr.tasks[tid]["duration_ms"] is not None
    print("④ cancel OK")

    # ⑤ 取消已完成的任务 -> 报错不炸
    res = tr.cancel_task(tr.start_task("t_done_then_cancel"))
    # 该任务还没 spawn，没有 ref
    assert res["ok"] is False, res
    print("⑤ 幂等取消 OK")

    # ⑥ list_tasks 含 kind/duration
    lst = tr.list_tasks()
    assert all("kind" in x and "duration_ms" in x for x in lst), lst[:1]
    print("⑥ list_tasks OK")

    # ⑦ 事件日志落盘
    ev = Path(__file__).resolve().parent / "logs" / "tasks"
    files = sorted(ev.glob("task_events_*.jsonl"))
    assert files, "no event file"
    last = files[-1].read_text(encoding="utf-8").strip().splitlines()[-1]
    import json
    rec = json.loads(last)
    assert rec["event"] in ("start", "end") and rec["task_id"] and rec["kind"], rec
    print("⑦ 事件日志 OK ->", rec["task_id"], rec["event"])

    # ⑧ 旧名兼容：_safe_start_task 走 spawn_task 完整生命周期
    tid = tr.start_task("t_legacy")
    async def _l():
        pass
    tr._safe_start_task(tid, _l())
    await asyncio.sleep(0.05)
    assert tr.tasks[tid]["status"] == "done", tr.tasks[tid]["status"]
    print("⑧ _safe_start_task 兼容 OK")

    print("ALL PASS")


asyncio.run(main())
