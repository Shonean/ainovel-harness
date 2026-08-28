"""流式链路探针：llm_client 层 → 提取器层，定位 E2 零帧问题。"""
from __future__ import annotations

import asyncio
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, r"C:\Users\24357\Desktop\AInovel Harness\小说系统\ainovel-write\prompt-harness")


async def main() -> None:
    from prompt_harness.llm_client import chat_completion_stream
    from prompt_harness.ai_creation import _make_reply_stream_extractor

    # ── 层1：llm_client 裸流式 ──
    deltas: list[str] = []
    buf = ""

    async def cb(s: str) -> None:
        deltas.append(s)

    r = await chat_completion_stream(
        system='你必须只返回合法 JSON。格式：{"reply":"一句话","action":null}',
        user="用十个字夸一下春天",
        on_delta=cb,
        call_type="probe_stream",
        temperature=0.7,
        max_tokens=200,
        json_mode=True,
    )
    print(f"[层1] error={r['error']} usage={bool(r['usage'])}")
    print(f"[层1] 增量帧数={len(deltas)}")
    print(f"[层1] 聚合content={r['content'][:120]!r}")

    # ── 层2：reply 提取器 ──
    feed = _make_reply_stream_extractor()
    acc = ""
    shown = 0
    for d in deltas:
        acc += d
        new = feed(acc)
        if new:
            shown += len(new)
    print(f"[层2] 提取器产出字符数={shown}")
    print(f"[层2] 最终缓冲={acc[:160]!r}")


if __name__ == "__main__":
    asyncio.run(main())
