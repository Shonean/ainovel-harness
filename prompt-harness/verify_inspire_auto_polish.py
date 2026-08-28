"""验证灵感工坊「输出前自动去 AI 味」（2026-08-10）：
inspire/chat 回复在返回前自动经 _ai_flavor_polish 审阅（脏则重写）。
产出 → harness_runs/ai_flavor_guard/run2_auto_polish.json"""
import json
from pathlib import Path

import requests

OUT = Path(__file__).parent / "harness_runs" / "ai_flavor_guard"
OUT.mkdir(parents=True, exist_ok=True)
BOOK = r"C:\Users\24357\Desktop\AInovel写作系统\小说系统\草稿"
BASE = "http://127.0.0.1:8765/api/prompt-harness"
WBLOG = Path(r"C:\Users\24357\Desktop\AInovel写作系统\小说系统\ainovel-write\prompt-harness\logs\workbench\C__Users_24357_Desktop_AInovel Harness_小说系统_草稿\2026-08-10.jsonl")

# 让 AI 写「宴碎」开篇场景+旁白（易出 AI 味的任务）
r1 = requests.post(f"{BASE}/ai-creation/inspire/chat", json={
    "book_root": BOOK,
    "messages": [{"role": "user",
                  "content": "用文字写出《宴碎》第一章的开篇场景描写和旁白：陆知砚在寿宴上砸酒壶怼周主事，以及陆老夫人、二老爷、陆崇老头的反应。直接写正文，不解释。"}],
    "mech": "free", "ctx": {},
}, timeout=300)
d1 = r1.json()
reply = d1.get("reply") or ""
print("inspire/chat ok=", d1.get("ok"), "| reply 前 120 字:", reply[:120])

# 查 workbench 日志：inspire_chat 后是否有 ai_flavor_review_standalone / inspire_chat_auto_polish
polish_seen = review_seen = False
if WBLOG.exists():
    recs = []
    for l in WBLOG.read_text(encoding="utf-8").splitlines():
        if not l.strip():
            continue
        try:
            recs.append(json.loads(l))
        except Exception:
            pass
    # 取最后一批（本次调用产生的）
    tail = recs[-12:]
    for r in tail:
        ct = r.get("call_type")
        if ct == "inspire_chat_auto_polish":
            polish_seen = True
        if ct == "ai_flavor_review_standalone":
            review_seen = True
    print("日志尾部 call_types:", [r.get("call_type") for r in tail])

print("自动审阅(ai_flavor_review_standalone)执行:", review_seen)
print("自动重写(inspire_chat_auto_polish)触发:", polish_seen)

(OUT / "run2_auto_polish.json").write_text(json.dumps({
    "reply_head": reply[:200],
    "auto_review_executed": review_seen,
    "auto_rewrite_triggered": polish_seen,
}, ensure_ascii=False, indent=2), encoding="utf-8")

# 至少审阅必须执行（输出前自动去味已生效）
assert review_seen, "未发现自动审阅执行！"
print("PASS: 输出前自动去味已生效")
