"""appserver — VS Code 扩展专用 WS JSON-RPC 服务层（协议优先分层，参考 codex app-server 三原语）。

与 GUI设计参考.md 对应的架构落点：
  commands   客户端→引擎，有响应（task.subscribe / chat.start / approval.respond / ping / status）
  events     引擎→客户端单向推送（event/task.event / event/chat.token|tool_call|done|error|applied）
  approvals  危险操作人工裁决（approval/request 推送 + approval.respond 回传）

设计取舍（与参考文档 §2 的差异，均有意为之）：
  - 命令面继续走既有 194 个 REST 端点（api.js 已是类型化客户端）；本服务只承载 REST
    做不到的三件事：任务事件双推、阻塞式审批、聊天 token 流。
  - 审批不挂起引擎协程：arc_chat 本就返回 pending 提案后结束，无后续排队依赖，
    「先回复、等 respond 再执行」语义等价且不占用连接资源；超时默认拒绝（§5.4）。
  - 闸门在引擎层：内容工具一律 dry_run 提案，直接调 REST 同样拿不到执行（§7.3）。

安全（§7）：仅回环监听由宿主决定；一次性 token 写临时文件（initialize 校验）；
带陌生 Origin 的升级请求拒绝（VS Code 扩展宿主直连不带 Origin）。
"""

from __future__ import annotations

import asyncio
import hmac
import json
import os
import secrets
import tempfile
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

router = APIRouter(prefix="/api/appserver", tags=["appserver"])

PROTOCOL_VERSION = 1
APPROVAL_TIMEOUT_S = 600  # 审批超时默认拒绝并提示（GUI设计参考.md §3 内联审批）

# ============================================================
# 一次性 token（§7.2）：启动生成写临时文件，扩展宿主读取后握手
# ============================================================

_APPSERVER_TOKEN: str | None = None


def _token_file_path() -> Path:
    override = os.environ.get("AINOVEL_APPSERVER_TOKEN_FILE")
    if override:
        return Path(override)
    return Path(tempfile.gettempdir()) / "ainovel-appserver-token.json"


def ensure_appserver_token() -> str:
    """惰性生成一次性 token 并落盘（权限收紧），返回 token。

    进程存活期间复用同一 token；文件含 pid/ts 便于扩展端校验是否过期实例。
    """
    global _APPSERVER_TOKEN
    if _APPSERVER_TOKEN:
        return _APPSERVER_TOKEN
    env_tok = os.environ.get("AINOVEL_APPSERVER_TOKEN")
    _APPSERVER_TOKEN = env_tok or secrets.token_urlsafe(32)
    payload = {
        "token": _APPSERVER_TOKEN,
        "pid": os.getpid(),
        "ts": int(time.time()),
        "protocol_version": PROTOCOL_VERSION,
    }
    p = _token_file_path()
    try:
        fd = os.open(str(p), os.O_CREAT | os.O_WRONLY | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(payload, fh)
    except Exception:  # noqa: BLE001 —— token 文件写失败不阻断服务（本地回环场景仍可用）
        pass
    return _APPSERVER_TOKEN


# 允许的 Origin 白名单之外的浏览器来源一律拒之门外；
# VS Code 扩展宿主用 ws 库直连时不发 Origin 头 → 放行（§7.5）。
_ALLOWED_ORIGIN_FRAGMENTS = ("localhost", "127.0.0.1")


def _origin_allowed(origin: str | None) -> bool:
    if not origin:
        return True  # 非浏览器客户端（扩展宿主）不带 Origin
    low = origin.lower()
    return any(frag in low for frag in _ALLOWED_ORIGIN_FRAGMENTS)


# ============================================================
# 连接管理与审批存储
# ============================================================


class _Conn:
    """一条已握手的 WS 连接。send 用锁串行化（转发循环与命令响应并发）。"""

    def __init__(self, ws: WebSocket) -> None:
        self.ws = ws
        self.lock = asyncio.Lock()
        self.alive = True
        # task_id → {"idx": 已转发的 events 长度}
        self.task_subs: dict[str, dict[str, int]] = {}

    async def send(self, obj: dict[str, Any]) -> None:
        if not self.alive:
            return
        async with self.lock:
            try:
                await self.ws.send_text(json.dumps(obj, ensure_ascii=False))
            except Exception:  # noqa: BLE001 —— 发送失败即视为断开
                self.alive = False


_CONNECTIONS: set[_Conn] = set()


@dataclass
class _Approval:
    approval_id: str
    tool: str
    args: dict[str, Any]
    book_root: str
    arc_id: str
    mode: str
    chat_id: str
    summary_zh: str
    justification: str
    created_at: float = field(default_factory=time.time)


_APPROVALS: dict[str, _Approval] = {}
_APPROVAL_TIMEOUT_TASKS: dict[str, asyncio.Task] = {}


def _audit(decision: str, ap: _Approval, detail: str = "") -> None:
    """审批审计流水账（jsonl 只增不删；UI 提供只读视图）。"""
    try:
        log_dir = Path(__file__).resolve().parent.parent / "logs" / "approvals"
        log_dir.mkdir(parents=True, exist_ok=True)
        rec = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "approval_id": ap.approval_id,
            "tool": ap.tool,
            "book_root": ap.book_root,
            "arc_id": ap.arc_id,
            "decision": decision,  # approved / rejected / expired
            "summary": ap.summary_zh[:200],
            "detail": detail[:500],
            "client": "vscode-extension",
        }
        with open(log_dir / f"{time.strftime('%Y%m%d')}.jsonl", "a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception:  # noqa: BLE001 —— 审计失败不影响主流程
        pass


async def _broadcast(method: str, params: dict[str, Any]) -> None:
    for conn in list(_CONNECTIONS):
        await conn.send({"jsonrpc": "2.0", "method": method, "params": params})


# ============================================================
# 任务事件转发：轮询 TASKS 存档实现多订阅者（不侵入现有 SSE 单消费者队列）
# ============================================================

_FORWARD_INTERVAL_S = 0.25
_forwarder_task: asyncio.Task | None = None


async def _forward_loop() -> None:
    while True:
        try:
            await _forward_once_impl()
        except Exception:  # noqa: BLE001 —— 单轮失败不终止转发循环
            pass
        await asyncio.sleep(_FORWARD_INTERVAL_S)


def _get_tasks_singleton():
    """取全局 TaskManager 单例（dashboard.services.task_manager.TASKS）。

    dashboard 先于本模块挂载，sys.path 已含 app 目录；懒加载避免独立导入
    prompt-harness 时引入 dashboard 依赖。
    """
    try:
        from dashboard.services.task_manager import TASKS  # type: ignore
        return TASKS
    except Exception:  # noqa: BLE001
        try:
            from services.task_manager import TASKS  # type: ignore
            return TASKS
        except Exception:  # noqa: BLE001
            return None


_TERMINAL_PHASES = {"done", "error", "cancelled"}


async def _forward_once_impl() -> None:
    tm = _get_tasks_singleton()
    if tm is None:
        return
    for conn in list(_CONNECTIONS):
        if not conn.alive:
            continue
        for tid, state in list(conn.task_subs.items()):
            task = tm.get(tid)
            if task is None:
                continue
            events = task.events
            idx = state.get("idx", 0)
            if len(events) > idx:
                state["idx"] = len(events)
                for ev in events[idx:]:
                    await conn.send({
                        "jsonrpc": "2.0",
                        "method": "event/task.event",
                        "params": {"task_id": tid, "event": ev},
                    })
                    if ev.get("phase") in _TERMINAL_PHASES:
                        conn.task_subs.pop(tid, None)
                        break


def _ensure_forwarder() -> None:
    global _forwarder_task
    if _forwarder_task is None or _forwarder_task.done():
        _forwarder_task = asyncio.create_task(_forward_loop())


# ============================================================
# 审批执行：respond 后以 dry_run=False 真执行（与 REST apply 端点同路径）
# ============================================================


async def _execute_approval(ap: _Approval, approved: bool, args_override: dict[str, Any] | None) -> None:
    """真执行（或记录拒绝），广播结果。超时路径同样进入这里（approved=False, expired）。"""
    _APPROVALS.pop(ap.approval_id, None)
    tmo = _APPROVAL_TIMEOUT_TASKS.pop(ap.approval_id, None)
    if tmo and not tmo.done():
        tmo.cancel()
    if not approved:
        _audit("expired" if args_override == "__expired__" else "rejected", ap)
        await _broadcast("event/chat.applied", {
            "chat_id": ap.chat_id,
            "approval_id": ap.approval_id,
            "approved": False,
        })
        return
    try:
        from . import ai_creation as ac

        arcs = ac.load_arcs(ap.book_root)
        arc = ac._find_arc(arcs, ap.arc_id) if ap.arc_id else None
        elements = ac.load_elements(ap.book_root)
        args = args_override if isinstance(args_override, dict) else ap.args
        ev, _needs = await ac._execute_chat_tool(
            ap.tool, args or {}, Path(ap.book_root), arc, elements, dry_run=False, mode=ap.mode)
        _audit("approved", ap, str((ev or {}).get("summary") or ""))
        await _broadcast("event/chat.applied", {
            "chat_id": ap.chat_id,
            "approval_id": ap.approval_id,
            "approved": True,
            "tool": ap.tool,
            "event": ev,
            "arc_id": ap.arc_id,
        })
    except Exception as exc:  # noqa: BLE001 —— 执行失败也要回告前端
        _audit("approved_error", ap, str(exc))
        await _broadcast("event/chat.applied", {
            "chat_id": ap.chat_id,
            "approval_id": ap.approval_id,
            "approved": True,
            "error": str(exc)[:300],
        })


def _schedule_approval_timeout(ap: _Approval) -> None:
    async def _expire() -> None:
        await asyncio.sleep(APPROVAL_TIMEOUT_S)
        cur = _APPROVALS.get(ap.approval_id)
        if cur is not None:
            await _execute_approval(cur, False, "__expired__")

    _APPROVAL_TIMEOUT_TASKS[ap.approval_id] = asyncio.create_task(_expire())


async def _create_approvals_for_pending(chat_id: str, book_root: str, arc_id: str,
                                        mode: str, pending: list[dict[str, Any]]) -> None:
    """把 arc_chat 返回的 pending 提案逐条升级为 approval/request 推送。"""
    for prop in pending or []:
        aid = uuid.uuid4().hex
        ap = _Approval(
            approval_id=aid,
            tool=str(prop.get("tool") or ""),
            args=dict(prop.get("args") or {}),
            book_root=book_root,
            arc_id=arc_id,
            mode=mode,
            chat_id=chat_id,
            summary_zh=str(prop.get("summary") or ""),
            justification=str(prop.get("detail") or ""),
        )
        _APPROVALS[aid] = ap
        _schedule_approval_timeout(ap)
        await _broadcast("approval/request", {
            "approval_id": aid,
            "level": "prompt",
            "chat_id": chat_id,
            "arc_id": arc_id,
            "mode": mode,
            "tool": ap.tool,
            "args": ap.args,
            "summary_zh": ap.summary_zh,
            "justification": ap.justification,
        })


# ============================================================
# chat.start：后台跑 arc_chat（流式回调经连接推送），完成时登记审批
# ============================================================

_chat_locks: dict[str, asyncio.Lock] = {}  # book_root → 串行化同书对话


async def _run_chat_job(conn: _Conn, params: dict[str, Any]) -> None:
    chat_id = str(params.get("chat_id") or "")
    book_root = str(params.get("book_root") or "")

    async def on_event(etype: str, data: dict[str, Any]) -> None:
        await conn.send({
            "jsonrpc": "2.0",
            "method": f"event/chat.{etype}",
            "params": {"chat_id": chat_id, **data},
        })

    try:
        from . import ai_creation as ac
        lock = _chat_locks.setdefault(book_root, asyncio.Lock())
        if lock.locked():
            await on_event("error", {"error": "该书已有一次对话在处理中，请稍候"})
            return
        async with lock:
            result = await ac.arc_chat(
                book_root,
                str(params.get("arc_id") or ""),
                params.get("messages") or [],
                web_search=bool(params.get("web_search")),
                access=params.get("access"),
                sel_access=params.get("sel_access"),
                mode=str(params.get("mode") or "normal"),
                on_event=on_event,
            )
        await on_event("done", {"result": result})
        pend = (result or {}).get("pending") or []
        if pend:
            await _create_approvals_for_pending(
                chat_id, book_root, str(params.get("arc_id") or ""),
                str(params.get("mode") or "normal"), pend)
    except Exception as exc:  # noqa: BLE001 —— 任务内异常必须回告而非静默
        try:
            await on_event("error", {"error": f"{type(exc).__name__}: {exc}"})
        except Exception:  # noqa: BLE001
            pass


# ============================================================
# WS 端点与命令分发
# ============================================================


def _rpc_error(id_: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": id_, "error": {"code": code, "message": message}}


async def _handle_command(conn: _Conn, msg: dict[str, Any]) -> None:
    method = str(msg.get("method") or "")
    id_ = msg.get("id")
    params = msg.get("params") or {}

    if method == "ping":
        await conn.send({"jsonrpc": "2.0", "id": id_, "result": {"pong": True}})
        return
    if method == "status":
        await conn.send({"jsonrpc": "2.0", "id": id_, "result": {
            "protocol_version": PROTOCOL_VERSION,
            "server": "ainovel-appserver",
            "ts": int(time.time()),
            "connections": len(_CONNECTIONS),
            "pending_approvals": len(_APPROVALS),
        }})
        return
    if method == "task.subscribe":
        tm = _get_tasks_singleton()
        tid = str(params.get("task_id") or "")
        task = tm.get(tid) if tm else None
        if task is None:
            await conn.send(_rpc_error(id_, -32002, f"task {tid} 不存在"))
            return
        last_seq = int(params.get("last_seq") or 0)
        # 对齐 seq：跳过已消费的历史事件（重连续订不重放）
        idx = 0
        for i, ev in enumerate(task.events):
            if ev.get("seq", 0) > last_seq:
                idx = i
                break
        else:
            idx = len(task.events)
        conn.task_subs[tid] = {"idx": idx}
        _ensure_forwarder()
        await conn.send({"jsonrpc": "2.0", "id": id_, "result": {"subscribed": tid}})
        return
    if method == "task.unsubscribe":
        conn.task_subs.pop(str(params.get("task_id") or ""), None)
        await conn.send({"jsonrpc": "2.0", "id": id_, "result": {"unsubscribed": True}})
        return
    if method == "chat.start":
        chat_id = str(params.get("chat_id") or "") or uuid.uuid4().hex
        book_root = str(params.get("book_root") or "")
        if not book_root or not Path(book_root).is_dir():
            await conn.send(_rpc_error(id_, -32001, f"book_root 无效：{book_root!r}"))
            return
        params = {**params, "chat_id": chat_id}
        asyncio.create_task(_run_chat_job(conn, params))
        await conn.send({"jsonrpc": "2.0", "id": id_, "result": {"ok": True, "chat_id": chat_id}})
        return
    if method == "approval.respond":
        aid = str(params.get("approval_id") or "")
        ap = _APPROVALS.get(aid)
        if ap is None:
            await conn.send(_rpc_error(id_, -32003, f"approval {aid} 不存在或已处理"))
            return
        approved = bool(params.get("approved"))
        args_override = params.get("args") if isinstance(params.get("args"), dict) else None
        asyncio.create_task(_execute_approval(ap, approved, args_override))
        await conn.send({"jsonrpc": "2.0", "id": id_, "result": {"ok": True}})
        return
    await conn.send(_rpc_error(id_, -32601, f"未知方法：{method}"))


@router.websocket("/ws")
async def appserver_ws(ws: WebSocket):
    # Origin 校验必须在 accept 之前（拒绝恶意网页跨站 WS）
    if not _origin_allowed(ws.headers.get("origin")):
        await ws.close(code=1008)
        return
    await ws.accept()

    # ── 握手：第一条消息必须是 initialize 且 token 正确 ──
    conn = _Conn(ws)
    try:
        first_raw = await ws.receive_text()
        first = json.loads(first_raw)
    except Exception:  # noqa: BLE001
        await ws.close(code=1002)
        return
    ok_init = (
        isinstance(first, dict)
        and first.get("method") == "initialize"
        and isinstance(first.get("params"), dict)
        and int((first.get("params") or {}).get("protocol_version") or 0) == PROTOCOL_VERSION
        and hmac.compare_digest(
            str((first.get("params") or {}).get("token") or ""),
            ensure_appserver_token(),
        )
    )
    if not ok_init:
        await conn.send(_rpc_error(first.get("id") if isinstance(first, dict) else None,
                                   -32000, "握手失败：token 或 protocol_version 不正确"))
        await ws.close(code=1008)
        return
    await conn.send({"jsonrpc": "2.0", "id": first.get("id"), "result": {
        "protocol_version": PROTOCOL_VERSION,
        "server": "ainovel-appserver",
        "approval_timeout_s": APPROVAL_TIMEOUT_S,
    }})
    _CONNECTIONS.add(conn)
    _ensure_forwarder()
    try:
        while True:
            raw = await ws.receive_text()
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                await conn.send(_rpc_error(None, -32700, "解析错误"))
                continue
            if not conn.alive:
                break
            await _handle_command(conn, msg)
    except WebSocketDisconnect:
        pass
    except Exception:  # noqa: BLE001 —— 其它异常一律按断开处理
        pass
    finally:
        conn.alive = False
        _CONNECTIONS.discard(conn)
