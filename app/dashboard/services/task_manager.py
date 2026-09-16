"""
TaskManager — 跟踪长任务（脚本子进程 / LLM agent 调用）并支持 SSE 重连。

设计要点
----------
* 每个任务有一个 ``task_id``（uuid4），存在内存 dict 里。
* 任务事件流写入一个 ``asyncio.Queue``；同时存档到 ``events`` 列表里，
  以便重连客户端能立刻把已发生事件重放出来。
* ``register_consumer`` 返回一个 async generator，先把历史 events
  逐条吐出，然后接管 queue 实时推流，直到任务完成或客户端断开。
* 不持久化到磁盘 —— dashboard 进程重启则任务列表丢失（与现状一致）。

事件协议
----------
每条事件是一个 ``dict``，至少包含 ``phase`` 字段，前端按 phase 路由：

.. code-block::

    {"phase": "stdout", "line": "<utf-8 string>"}
    {"phase": "stderr", "line": "..."}
    {"phase": "tool_use", "name": "Read", "input": {...}}
    {"phase": "tool_result", "name": "Read", "output_preview": "..."}
    {"phase": "token", "text": "..."}            # LLM 流式 token
    {"phase": "step", "step": "context", "status": "running"}
    {"phase": "awaiting_input", "prompt": "...", "schema": {...}}
    {"phase": "done", "payload": {...}}          # 任务正常结束
    {"phase": "error", "msg": "...", "traceback": "..."}

每条事件经 ``json.dumps`` 后以 ``data: <json>\\n\\n`` 的形式写到 SSE。
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from dataclasses import dataclass, field
from typing import AsyncGenerator, Optional


@dataclass
class Task:
    task_id: str
    kind: str  # "action" / "agent" / "workflow"
    label: str
    created_at: float = field(default_factory=time.time)
    finished_at: Optional[float] = None
    events: list[dict] = field(default_factory=list)
    queue: asyncio.Queue = field(default_factory=asyncio.Queue)
    awaiting_input: Optional[dict] = None  # 工作流挂起时设置
    resume_future: Optional[asyncio.Future] = None  # /resume 喂答案
    _seq: int = 0  # 单调递增事件序号，用于 SSE Last-Event-ID 重连去重
    cancel_event: asyncio.Event = field(default_factory=asyncio.Event)
    cancelled: bool = False
    mutex_key: Optional[str] = None  # 互斥键：同 key 的新任务启动前自动取消旧的，杜绝并发跑
    decision_records: dict[str, dict] = field(default_factory=dict)  # 已确认的决策记录：key=step_id/operation_type，value=决策详情
    project_root: Optional[str] = None  # 项目根目录路径，用于决策日志持久化
    auto_generate: bool = False  # 一键自动生成模式：跳过所有用户确认点（init 除外）

    @property
    def is_cancelled(self) -> bool:
        return self.cancelled

    @property
    def is_finished(self) -> bool:
        return self.finished_at is not None

    def to_summary(self) -> dict:
        return {
            "task_id": self.task_id,
            "kind": self.kind,
            "label": self.label,
            "created_at": self.created_at,
            "finished_at": self.finished_at,
            "is_finished": self.is_finished,
            "event_count": len(self.events),
            "awaiting_input": self.awaiting_input,
        }


class TaskManager:
    """全局任务字典 + 事件流分发器。"""

    def __init__(self, *, max_tasks: int = 200) -> None:
        self._tasks: dict[str, Task] = {}
        self._max_tasks = max_tasks

    # ---------------- 生命周期 ----------------

    def create(self, *, kind: str, label: str, mutex_key: Optional[str] = None,
               project_root: Optional[str] = None) -> Task:
        task = Task(
            task_id=uuid.uuid4().hex,
            kind=kind,
            label=label,
            mutex_key=mutex_key,
            project_root=project_root,
        )
        self._tasks[task.task_id] = task
        self._gc()
        return task

    def get(self, task_id: str) -> Optional[Task]:
        return self._tasks.get(task_id)

    def list_tasks(self, *, limit: int = 50) -> list[dict]:
        items = sorted(
            self._tasks.values(),
            key=lambda t: t.created_at,
            reverse=True,
        )
        return [t.to_summary() for t in items[:limit]]

    def _gc(self) -> None:
        if len(self._tasks) <= self._max_tasks:
            return
        # 删最旧已完成的
        finished = sorted(
            (t for t in self._tasks.values() if t.is_finished),
            key=lambda t: t.finished_at or 0,
        )
        drop = len(self._tasks) - self._max_tasks
        for t in finished[:drop]:
            self._tasks.pop(t.task_id, None)

    # ---------------- 事件推送 ----------------

    async def emit(self, task: Task, event: dict) -> None:
        """把一条事件写入 task：同时存档 + 推到队列。

        给每条事件盖一个单调递增的 ``seq``，并写进 SSE ``id:`` 字段，
        让前端断线重连时能用 Last-Event-ID 只补发增量，避免历史事件
        （尤其是 awaiting_input 弹窗）被重复重放。
        """
        task._seq += 1
        event = {**event, "seq": task._seq}
        task.events.append(event)
        await task.queue.put(event)

    async def emit_done(self, task: Task, payload: dict | None = None) -> None:
        if payload is None:
            payload = {}
        await self.emit(task, {"phase": "done", "payload": payload})
        task.finished_at = time.time()
        # 用 sentinel 唤醒 consumer
        await task.queue.put(None)

    async def emit_error(self, task: Task, msg: str, *, traceback: str = "") -> None:
        await self.emit(task, {"phase": "error", "msg": msg, "traceback": traceback})
        task.finished_at = time.time()
        await task.queue.put(None)

    # ---------------- 工作流挂起/恢复 ----------------

    async def suspend_for_input(self, task: Task, prompt: dict) -> dict:
        """挂起任务等待用户输入；返回用户提交的 answer dict。"""
        task.awaiting_input = prompt
        await self.emit(task, {"phase": "awaiting_input", **prompt})

        loop = asyncio.get_running_loop()
        task.resume_future = loop.create_future()
        try:
            return await task.resume_future
        finally:
            task.awaiting_input = None
            task.resume_future = None

    def resume(self, task_id: str, answer: dict) -> bool:
        task = self.get(task_id)
        if not task or not task.resume_future or task.resume_future.done():
            return False
        task.resume_future.set_result(answer)
        return True

    # ---------------- 取消 ----------------

    def find_active(self, kind: str) -> Optional[Task]:
        """返回指定 kind 下唯一未结束的活跃任务（若有）。"""
        for task in self._tasks.values():
            if task.kind == kind and not task.is_finished:
                return task
        return None

    async def cancel(self, task_id: str | Task, *, reason: str = "用户取消") -> bool:
        """请求取消任务：置 cancel_event，让 agent 循环在下一轮检查点退出。

        ``task_id`` 可传 task_id 字符串或 Task 对象。返回 True 表示发出了取消信号。
        实际终止发生在 agent 的 run() 循环检查到 cancel_event 后 emit_error + 收尾。
        """
        task = task_id if isinstance(task_id, Task) else self.get(task_id)
        if not task or task.is_finished:
            return False
        task.cancelled = True
        task.cancel_event.set()
        # 若任务正挂起等输入，顺带唤醒它让循环继续到检查点
        if task.resume_future and not task.resume_future.done():
            task.resume_future.set_result({"answer": "__cancelled__"})
        await self.emit(task, {"phase": "cancelled", "msg": reason})
        return True

    async def cancel_active_by_mutex(self, mutex_key: str, *, reason: str = "被新任务取代") -> int:
        """取消所有持有该 mutex_key 且未结束的活跃任务。返回取消的数量。

        用于「同类型任务互斥」：启动新的 plan 前先取消旧的 plan，
        杜绝两个同类 workflow 并发跑。
        """
        n = 0
        for task in list(self._tasks.values()):
            if (
                task.mutex_key == mutex_key
                and not task.is_finished
            ):
                await self.cancel(task, reason=reason)
                n += 1
        return n

    # ---------------- SSE consumer ----------------

    async def stream(self, task_id: str, last_event_id: int = 0) -> AsyncGenerator[str, None]:
        """SSE 生成器：先重放历史，再实时推流。

        ``last_event_id`` 取自浏览器的 ``Last-Event-ID`` 请求头（重连时自动带），
        只补发 seq 大于它的增量事件，避免历史弹窗被重复重放。
        """
        task = self.get(task_id)
        if task is None:
            yield _sse({"phase": "error", "msg": f"task {task_id} 不存在"})
            return

        # 1) 重放已存档事件中 seq > last_event_id 的部分（断线重连只补增量）
        replayed = 0
        for ev in task.events:
            if ev.get("seq", 0) <= last_event_id:
                continue
            yield _sse(ev)
            replayed += 1

        if task.is_finished:
            return

        # 2) 接管 queue 直到 sentinel。
        # queue 里存有 emit 时 put 的同一批事件；用 last_event_id 对齐：
        # 每收到一个非 None 事件，若其 seq <= last_event_id 说明已被重放/已发过，跳过。
        while True:
            try:
                ev = await asyncio.wait_for(task.queue.get(), timeout=300.0)
            except asyncio.TimeoutError:
                yield ": keepalive\n\n"
                continue
            if ev is None:
                break
            if ev.get("seq", 0) <= last_event_id:
                continue  # 已在历史重放阶段发过，跳过
            yield _sse(ev)


def _sse(event: dict) -> str:
    seq = event.get("seq")
    id_line = f"id: {seq}\n" if seq is not None else ""
    return f"{id_line}data: {json.dumps(event, ensure_ascii=False)}\n\n"


# 全局单例
TASKS = TaskManager()
