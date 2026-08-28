"""真机验证 inspire_chat 多轮上下文（修复后）。
第一轮抛设定 → 第二轮复述第一轮设定，看 LLM 是否记得。
产出 → harness_runs/inspire_multiturn/"""
import json
from pathlib import Path

import requests

OUT = Path(__file__).parent / "harness_runs" / "inspire_multiturn"
OUT.mkdir(parents=True, exist_ok=True)

URL = "http://127.0.0.1:8765/api/prompt-harness/ai-creation/inspire/chat"
BOOK = r"C:\Users\24357\Desktop\AInovel写作系统\小说系统\草稿"


def chat(messages):
    r = requests.post(URL, json={
        "book_root": BOOK, "messages": messages, "mech": "free", "ctx": {},
    }, timeout=180)
    return r.json()


# 第一轮：抛设定
r1 = chat([{"role": "user", "content": "设定一个设定：主角会炼制让人返老还童的丹药，吞服者折寿十年。"}])
assert r1.get("ok"), r1
reply1 = r1["reply"]

# 第二轮：复述验证（messages 带完整历史：第一轮 user + AI 回复 + 新问题）
r2 = chat([
    {"role": "user", "content": "设定一个设定：主角会炼制让人返老还童的丹药，吞服者折寿十年。"},
    {"role": "assistant", "content": reply1},
    {"role": "user", "content": "我第一轮说的是什么设定？用一句话复述，开头写『第一轮设定是：』。"},
])
assert r2.get("ok"), r2
reply2 = r2["reply"]

resp = {"reply1": reply1, "reply2": reply2}
(OUT / "run1_response.json").write_text(json.dumps(resp, ensure_ascii=False, indent=2), encoding="utf-8")
print("=== 第二轮回复（前 300 字）===")
print(reply2[:300])

# 校验：修复后 LLM 应能看到第一轮设定（关键词命中即上下文打通）
hit = any(k in reply2 for k in ("返老还童", "返老", "折寿", "折了", "十年"))
print("=== 复述命中第一轮设定关键词:", hit, "===")
(OUT / "result.txt").write_text(f"context_recalled={hit}\n", encoding="utf-8")
print("saved ->", OUT)
