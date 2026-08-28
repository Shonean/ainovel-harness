"""appserver WS JSON-RPC 契约测试（契约用例 = VS Code 扩展端对接文档）。

覆盖：握手/token 拒连、Origin 校验、ping/status、未知方法、任务事件转发、
chat.start 流式事件序列、approval/request→respond→applied 闭环（同意/拒绝）。

运行：pytest prompt-harness/tests/test_appserver_contract.py -q
"""

from __future__ import annotations

import json
import sys
import threading
import time
from pathlib import Path

import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

_PH_SRC = Path(__file__).resolve().parent.parent
if str(_PH_SRC) not in sys.path:
    sys.path.insert(0, str(_PH_SRC))

from prompt_harness import appserver as aps  # noqa: E402


@pytest.fixture()
def client(monkeypatch):
    """干净的应用 + 固定 token。"""
    monkeypatch.setattr(aps, "_APPSERVER_TOKEN", "test-token")
    monkeypatch.setattr(aps, "_CONNECTIONS", set())
    monkeypatch.setattr(aps, "_APPROVALS", {})
    monkeypatch.setattr(aps, "_APPROVAL_TIMEOUT_TASKS", {})
    app = FastAPI()
    app.include_router(aps.router)
    with TestClient(app) as c:
        yield c


def _init_ok(ws):
    ws.send_text(json.dumps({
        "jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {"protocol_version": 1, "token": "test-token"},
    }))
    return json.loads(ws.receive_text())


def _recv_until(ws, predicate, timeout=5.0):
    """在后台线程收帧直到 predicate 命中（避免阻塞死等）。"""
    buf = []
    done = threading.Event()

    def _run():
        try:
            while not done.is_set():
                raw = ws.receive_text()
                msg = json.loads(raw)
                buf.append(msg)
                if predicate(msg):
                    done.set()
                    return
        except Exception:  # noqa: BLE001
            done.set()

    th = threading.Thread(target=_run, daemon=True)
    th.start()
    th.join(timeout)
    if not done.is_set():
        raise AssertionError(f"等待消息超时，已收到：{buf}")
    return buf[-1]


# ── 握手与基础命令 ──


def test_handshake_wrong_token_rejected(client):
    with client.websocket_connect("/api/appserver/ws") as ws:
        ws.send_text(json.dumps({
            "jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {"protocol_version": 1, "token": "wrong"},
        }))
        resp = json.loads(ws.receive_text())
        assert "error" in resp and resp["error"]["code"] == -32000


def test_handshake_bad_version_rejected(client):
    with client.websocket_connect("/api/appserver/ws") as ws:
        ws.send_text(json.dumps({
            "jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {"protocol_version": 99, "token": "test-token"},
        }))
        resp = json.loads(ws.receive_text())
        assert "error" in resp


def test_handshake_ok_then_ping(client):
    with client.websocket_connect("/api/appserver/ws") as ws:
        resp = _init_ok(ws)
        assert resp["result"]["protocol_version"] == aps.PROTOCOL_VERSION
        ws.send_text(json.dumps({"jsonrpc": "2.0", "id": 2, "method": "ping"}))
        pong = json.loads(ws.receive_text())
        assert pong["result"] == {"pong": True}


def test_unknown_method_error(client):
    with client.websocket_connect("/api/appserver/ws") as ws:
        _init_ok(ws)
        ws.send_text(json.dumps({"jsonrpc": "2.0", "id": 3, "method": "nope"}))
        resp = json.loads(ws.receive_text())
        assert resp["error"]["code"] == -32601


def test_origin_blocked():
    """陌生 Origin 的升级请求被拒（扩展宿主不带 Origin，不受影响）。"""
    app = FastAPI()
    app.include_router(aps.router)
    with TestClient(app) as c:
        with pytest.raises(Exception):  # noqa: B017 —— 握手被拒表现为断开/403
            with c.websocket_connect(
                "/api/appserver/ws",
                headers={"origin": "https://evil.example.com"},
            ) as ws:
                ws.send_text(json.dumps({
                    "jsonrpc": "2.0", "id": 1, "method": "initialize",
                    "params": {"protocol_version": 1, "token": "test-token"},
                }))
                ws.receive_text()
                # 若未拒连，强制失败
                raise AssertionError("陌生 Origin 未被拒绝")


# ── 任务事件转发 ──


class _FakeTask:
    def __init__(self):
        self.events = []


class _FakeTM:
    def __init__(self):
        self.tasks = {}

    def get(self, tid):
        return self.tasks.get(tid)


def test_task_subscribe_forwards_events(client, monkeypatch):
    tm = _FakeTM()
    t = _FakeTask()
    tm.tasks["T1"] = t
    monkeypatch.setattr(aps, "_get_tasks_singleton", lambda: tm)

    with client.websocket_connect("/api/appserver/ws") as ws:
        _init_ok(ws)
        ws.send_text(json.dumps({
            "jsonrpc": "2.0", "id": 5, "method": "task.subscribe",
            "params": {"task_id": "T1"},
        }))
        sub = json.loads(ws.receive_text())
        assert sub["result"]["subscribed"] == "T1"

        t.events.append({"phase": "step", "step": "draft", "status": "running", "seq": 1})
        got = _recv_until(ws, lambda m: m.get("method") == "event/task.event")
        assert got["params"]["task_id"] == "T1"
        assert got["params"]["event"]["phase"] == "step"


def test_task_subscribe_missing_task_errors(client, monkeypatch):
    monkeypatch.setattr(aps, "_get_tasks_singleton", lambda: _FakeTM())
    with client.websocket_connect("/api/appserver/ws") as ws:
        _init_ok(ws)
        ws.send_text(json.dumps({
            "jsonrpc": "2.0", "id": 6, "method": "task.subscribe",
            "params": {"task_id": "GHOST"},
        }))
        resp = json.loads(ws.receive_text())
        assert resp["error"]["code"] == -32002


# ── chat 流式 + 审批闭环 ──


def test_chat_stream_and_approval_flow(client, monkeypatch, tmp_path):
    """token 增量推送 → done → pending 升级为 approval/request → 同意真执行。"""
    from prompt_harness import ai_creation as ac

    book = tmp_path / "书A"
    book.mkdir()

    captured = {}

    async def fake_arc_chat(book_root, arc_id, messages, *, web_search=False,
                            access=None, sel_access=None, mode="normal", on_event=None):
        captured["on_event"] = on_event
        await on_event("token", {"text": "好的"})
        await on_event("token", {"text": "，我来改写 l1。"})
        return {
            "ok": True, "reply": "好的，我来改写 l1。", "tool_events": [],
            "changed": False,
            "pending": [{"tool": "modify_level", "args": {"level": "l1", "instruction": "改写"},
                         "summary": "✏ 改写 l1：改写", "detail": "用户同意后执行。"}],
            "new_arc_id": None,
        }

    async def fake_execute(tool, args, book_root, arc, elements, *, dry_run=False, mode="normal"):
        captured["execute_args"] = args
        return {"tool": tool, "summary": "✏ 已改写 l1", "detail": "", "changed": True}, False

    monkeypatch.setattr(ac, "arc_chat", fake_arc_chat)
    monkeypatch.setattr(ac, "_execute_chat_tool", fake_execute)

    with client.websocket_connect("/api/appserver/ws") as ws:
        _init_ok(ws)
        ws.send_text(json.dumps({
            "jsonrpc": "2.0", "id": 7, "method": "chat.start",
            "params": {"chat_id": "c1", "book_root": str(book), "arc_id": "",
                       "messages": [{"role": "user", "content": "改一下 l1"}]},
        }))

        got_appr = _recv_until(ws, lambda m: m.get("method") == "approval/request")

        # 审批卡字段契约
        p = got_appr["params"]
        assert p["level"] == "prompt"
        assert p["tool"] == "modify_level"
        assert p["summary_zh"].startswith("✏")
        aid = p["approval_id"]

        # 同意（带可编辑草稿覆盖）
        ws.send_text(json.dumps({
            "jsonrpc": "2.0", "id": 8, "method": "approval.respond",
            "params": {"approval_id": aid, "approved": True,
                       "args": {"level": "l1", "instruction": "改写（用户编辑过）"}},
        }))
        applied = _recv_until(ws, lambda m: m.get("method") == "event/chat.applied")
        assert applied["params"]["approved"] is True
        assert applied["params"]["event"]["summary"] == "✏ 已改写 l1"
        assert captured["execute_args"]["instruction"] == "改写（用户编辑过）"


def test_chat_approval_rejected(client, monkeypatch, tmp_path):
    from prompt_harness import ai_creation as ac

    book = tmp_path / "书B"
    book.mkdir()

    async def fake_arc_chat(book_root, arc_id, messages, **kw):
        return {"ok": True, "reply": "r", "tool_events": [], "changed": False,
                "pending": [{"tool": "finalize", "args": {},
                             "summary": "💾 落盘本章", "detail": ""}],
                "new_arc_id": None}

    executed = {"called": False}

    async def fake_execute(*a, **kw):
        executed["called"] = True
        return {"tool": "finalize", "summary": "x"}, False

    monkeypatch.setattr(ac, "arc_chat", fake_arc_chat)
    monkeypatch.setattr(ac, "_execute_chat_tool", fake_execute)

    with client.websocket_connect("/api/appserver/ws") as ws:
        _init_ok(ws)
        ws.send_text(json.dumps({
            "jsonrpc": "2.0", "id": 9, "method": "chat.start",
            "params": {"chat_id": "c2", "book_root": str(book)},
        }))
        appr = _recv_until(ws, lambda m: m.get("method") == "approval/request")
        ws.send_text(json.dumps({
            "jsonrpc": "2.0", "id": 10, "method": "approval.respond",
            "params": {"approval_id": appr["params"]["approval_id"], "approved": False},
        }))
        applied = _recv_until(ws, lambda m: m.get("method") == "event/chat.applied")
        assert applied["params"]["approved"] is False
        assert not executed["called"]


def test_respond_unknown_approval_errors(client, monkeypatch):
    monkeypatch.setattr(aps, "_APPROVALS", {})
    with client.websocket_connect("/api/appserver/ws") as ws:
        _init_ok(ws)
        ws.send_text(json.dumps({
            "jsonrpc": "2.0", "id": 11, "method": "approval.respond",
            "params": {"approval_id": "ghost", "approved": True},
        }))
        resp = json.loads(ws.receive_text())
        assert resp["error"]["code"] == -32003


def test_chat_invalid_book_root(client):
    with client.websocket_connect("/api/appserver/ws") as ws:
        _init_ok(ws)
        ws.send_text(json.dumps({
            "jsonrpc": "2.0", "id": 12, "method": "chat.start",
            "params": {"chat_id": "c3", "book_root": r"X:\不存在的目录"},
        }))
        resp = json.loads(ws.receive_text())
        assert resp["error"]["code"] == -32001
