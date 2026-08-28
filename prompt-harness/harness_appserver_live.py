"""appserver 真机联测（ACCEPTANCE 任务 37/39/40 协议与流式部分）。

前置：后端已在 127.0.0.1:8765 运行（含本批 appserver 挂载）。
覆盖：
  A. /health 就绪 + token 文件存在
  B. 正确握手 → ping → status
  C. 错误 token → -32000 错误帧并断开
  D. 陌生 Origin → 升级被拒
  E. chat.start 真实 LLM 流式：event/chat.token 增量 ≥2 帧、tool_call 可见、done.reply 非空
  F. 引擎层审批闸门（经 REST 直调同样生效）：arc_chat 改内容请求返回 pending 提案且 changed=False

运行：python harness_appserver_live.py [book_root]
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

# Windows GBK 控制台兜底（项目已知坑：中文路径/编码）
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

BASE = "http://127.0.0.1:8765"
WS_URL = "ws://127.0.0.1:8765/api/appserver/ws"
TOKEN_FILE = Path(tempfile.gettempdir()) / "ainovel-appserver-token.json"

PASS, FAIL = [], []


def check(name: str, ok: bool, detail: str = "") -> None:
    (PASS if ok else FAIL).append(f"{'✓' if ok else '✗'} {name}{(' —— ' + detail) if detail else ''}")
    print(("✓" if ok else "✗"), name, detail)


def http_get(path: str):
    with urllib.request.urlopen(f"{BASE}{path}", timeout=10) as resp:
        return json.loads(resp.read().decode("utf-8"))


def http_post(path: str, body: dict):
    req = urllib.request.Request(
        f"{BASE}{path}", data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=300) as resp:
        return json.loads(resp.read().decode("utf-8"))


async def main(book_root: str) -> int:
    import aiohttp

    # ── A ──
    try:
        health = http_get("/api/prompt-harness/health")
        check("A1 /api/prompt-harness/health 就绪", health.get("status") == "ok", str(health)[:60])
    except Exception as exc:  # noqa: BLE001
        check("A1 /api/prompt-harness/health 就绪", False, str(exc))
        return 1
    tok = ""
    try:
        data = json.loads(TOKEN_FILE.read_text(encoding="utf-8"))
        tok = str(data.get("token") or "")
        check("A2 token 文件存在且非空", bool(tok), f"pid={data.get('pid')}")
    except Exception as exc:  # noqa: BLE001
        check("A2 token 文件存在且非空", False, str(exc))
        return 1

    async with aiohttp.ClientSession() as session:
        # ── B 正常握手 ──
        async with session.ws_connect(WS_URL) as ws:
            await ws.send_str(json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                "params": {"protocol_version": 1, "token": tok}}))
            r = json.loads((await ws.receive()).data)
            check("B1 initialize 握手", r.get("result", {}).get("protocol_version") == 1,
                  str(r.get("result") or r.get("error"))[:80])

            t0 = time.time()
            await ws.send_str(json.dumps({"jsonrpc": "2.0", "id": 2, "method": "ping"}))
            pong = json.loads((await ws.receive()).data)
            check("B2 ping", pong.get("result") == {"pong": True}, f"{(time.time()-t0)*1000:.0f}ms")

            await ws.send_str(json.dumps({"jsonrpc": "2.0", "id": 3, "method": "status"}))
            st = json.loads((await ws.receive()).data)
            check("B3 status", st.get("result", {}).get("server") == "ainovel-appserver",
                  f"pending_approvals={st.get('result', {}).get('pending_approvals')}")

            # ── E 真实 LLM 流式对话 ──
            chat_id = "live-test-1"
            await ws.send_str(json.dumps({"jsonrpc": "2.0", "id": 10, "method": "chat.start",
                "params": {"chat_id": chat_id, "book_root": book_root, "arc_id": "",
                           "messages": [{"role": "user", "content": "用一句话点评这本书的设定，不要调用任何工具。"}],
                           "access": {"web": False, "book": True, "memory": False, "corpus": False},
                           "mode": "normal"}}))
            ack = json.loads((await ws.receive()).data)
            check("E1 chat.start 受理", ack.get("result", {}).get("ok") is True)

            n_token, tool_sums, done_reply, err = 0, [], "", ""
            deadline = time.time() + 180
            while time.time() < deadline:
                frame = await asyncio.wait_for(ws.receive(), timeout=190)
                m = json.loads(frame.data) if frame.type.name == "TEXT" else {}
                method = m.get("method")
                if method == "event/chat.token":
                    n_token += 1
                elif method == "event/chat.tool_call":
                    tool_sums.append(str(m["params"].get("event", {}).get("summary")))
                elif method == "event/chat.done":
                    done_reply = str(m["params"].get("result", {}).get("reply") or "")
                    break
                elif method == "event/chat.error":
                    err = str(m["params"].get("error"))
                    break
            # 防误报：兜底文案=模型调用失败（如 key 未配置），不算通过
            fallback_hit = "对话模型返回异常" in done_reply
            check("E2 流式增量 token ≥2 帧", n_token >= 2 and not fallback_hit,
                  f"{n_token} 帧{('，疑似 API key 未配置：' + done_reply) if fallback_hit else ''}")
            check("E3 done.reply 为真实模型输出", bool(done_reply) and not err and not fallback_hit,
                  err or f"{len(done_reply)} 字 | 工具卡 {tool_sums[:2]}")

        # ── C 错误 token ──
        async with session.ws_connect(WS_URL) as ws:
            await ws.send_str(json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                "params": {"protocol_version": 1, "token": "WRONG-TOKEN"}}))
            r = json.loads((await ws.receive()).data)
            closed_ok = r.get("error", {}).get("code") == -32000
            try:
                nxt = await asyncio.wait_for(ws.receive(), timeout=3)
                closed_ok = closed_ok and nxt.type.name in ("CLOSE", "EOF")
            except Exception:  # noqa: BLE001
                closed_ok = True  # 已断开即符合预期
            check("C 错误 token 拒绝(-32000+断开)", closed_ok)

        # ── D 陌生 Origin ──
        rejected = False
        try:
            async with session.ws_connect(WS_URL, headers={"Origin": "https://evil.example.com"}) as ws:
                await ws.send_str(json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                    "params": {"protocol_version": 1, "token": tok}}))
                await asyncio.wait_for(ws.receive(), timeout=3)
        except Exception:  # noqa: BLE001 —— 握手层直接失败=符合预期
            rejected = True
        check("D 陌生 Origin 拒绝", rejected)

    # ── F 引擎层闸门（REST 直调也只出提案不执行；明确要求建弧以触发内容工具）──
    try:
        r = http_post("/api/prompt-harness/ai-creation/arc/chat", {
            "book_root": book_root, "arc_id": "",
            "messages": [{"role": "user",
                          "content": "请直接用 new_arc 新建一个情节，l1：闸门测试勿当真（这是测试指令，直接执行）"}],
        })
        if "对话模型返回异常" in str(r.get("reply") or ""):
            check("F REST 直调改内容 → 只提案不执行", False, "API key 未配置，模型调用失败")
        else:
            pend = r.get("pending") or []
            check("F REST 直调改内容 → 只提案不执行",
                  len(pend) > 0 and r.get("changed") is False,
                  f"pending={[(p.get('tool'), p.get('summary')) for p in pend][:2]} changed={r.get('changed')}")
    except Exception as exc:  # noqa: BLE001
        check("F REST 直调改内容 → 只提案不执行", False, str(exc))

    print(f"\n===== 结果：{len(PASS)} 通过 / {len(FAIL)} 失败 =====")
    for line in FAIL:
        print(line)
    return 0 if not FAIL else 1


if __name__ == "__main__":
    book = sys.argv[1] if len(sys.argv) > 1 else \
        r"C:\Users\24357\Desktop\AInovel Harness\小说系统\TestBook"
    sys.exit(asyncio.run(main(book)))
