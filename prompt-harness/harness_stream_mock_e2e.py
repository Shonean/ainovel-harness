"""流式全链路 mock 验证（无需真实 API key）。

本机 api_library.json 缺失（用户清缓存波及），真实 LLM 实测暂不可行；
本脚本用本地 mock OpenAI 兼容 SSE 端点验证：
  llm_client 流式分支 → _make_reply_stream_extractor 增量提取 → arc_chat(on_event)
  → token/tool_call/pending_proposal 事件序列 → pending 提案不执行（闸门）。

运行：python harness_stream_mock_e2e.py
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

PH = r"C:\Users\24357\Desktop\AInovel Harness\小说系统\ainovel-write\prompt-harness"
sys.path.insert(0, PH)

MOCK_PORT = 18766
# 第一轮：模型先输出一段 reply 文本，再要求建弧（内容工具 → 应产出 pending 提案）
ROUND1 = {
    "reply": "好的，我来为这本书创建第一个情节。",
    "action": {"tool": "new_arc", "args": {"l1": "闸门测试情节：少年发现断刀自鸣", "n_chapters": 1}},
}
# 把 dict 编成"逐字块"SSE 序列（模拟真实分片）
_chunks = json.dumps(ROUND1, ensure_ascii=False)
SSE_PIECES = [_chunks[i:i + 7] for i in range(0, len(_chunks), 7)]


class MockHandler(BaseHTTPRequestHandler):
    def do_POST(self):  # noqa: N802
        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length) or b"{}")
        is_stream = bool(body.get("stream"))
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream" if is_stream else "application/json")
        self.end_headers()
        if not is_stream:
            content = "".join(SSE_PIECES)
            out = {"choices": [{"message": {"content": content}}], "usage": {"total_tokens": 42}}
            self.wfile.write(json.dumps(out).encode("utf-8"))
            return
        for piece in SSE_PIECES:
            frame = {"choices": [{"delta": {"content": piece}}]}
            self.wfile.write(f"data: {json.dumps(frame, ensure_ascii=False)}\n\n".encode("utf-8"))
            self.wfile.flush()
        usage_frame = {"choices": [], "usage": {"prompt_tokens": 10, "completion_tokens": 32, "total_tokens": 42}}
        self.wfile.write(f"data: {json.dumps(usage_frame)}\n\n".encode("utf-8"))
        self.wfile.write(b"data: [DONE]\n\n")
        self.wfile.flush()

    def log_message(self, *_a):  # 静默访问日志
        pass


async def main() -> int:
    os.environ["ARK_API_KEY"] = "mock-key"
    os.environ["ARK_BASE_URL"] = f"http://127.0.0.1:{MOCK_PORT}"
    os.environ["ARK_MODEL_PRO"] = "mock-model"

    server = ThreadingHTTPServer(("127.0.0.1", MOCK_PORT), MockHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()

    from prompt_harness import ai_creation as ac

    book = r"C:\Users\24357\Desktop\AInovel Harness\小说系统\TestBook"
    events: list[tuple[str, dict]] = []

    async def on_event(etype: str, data: dict) -> None:
        events.append((etype, data))

    result = await ac.arc_chat(
        book, "", [{"role": "user", "content": "建个情节"}],
        access={"web": False, "book": True, "memory": False, "corpus": False},
        on_event=on_event,
    )
    server.shutdown()

    from prompt_harness.http_client import get_session
    _s = await get_session()
    await _s.close()  # 避免 Unclosed client session 警告

    tokens = [d.get("text", "") for t, d in events if t == "token"]
    proposals = [d.get("proposal") for t, d in events if t == "pending_proposal"]

    ok = True
    def check(name, cond, detail=""):
        nonlocal ok
        print(("✓" if cond else "✗"), name, detail)
        ok = ok and cond

    check("token 事件 ≥2 帧", len(tokens) >= 2, f"{len(tokens)} 帧（mock 按 7 字分片）")
    check("流式文本稳定前缀（无重复/乱序）",
          "".join(tokens) == result["reply"], f"{len(result['reply'])} 字")
    check("最终 reply 与流式拼接一致", result["reply"] == ROUND1["reply"])
    check("pending 提案产出（new_arc）",
          any(p.get("tool") == "new_arc" for p in result["pending"]),
          str([p.get('summary') for p in result['pending']]))
    check("pending_proposal 事件已推送", len(proposals) >= 1)
    check("changed=False（闸门：未真执行）", result["changed"] is False)

    print("\n===== mock 全链路：", "全部通过" if ok else "存在失败", "=====")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
