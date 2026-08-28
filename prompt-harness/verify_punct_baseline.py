"""验证破折号确定性检测 + baseline_guard 固定 prompt 注入（2026-08-10）。
产出 → harness_runs/ai_flavor_guard/"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from prompt_harness.ai_flavor import punct_detector

# ── 单测 punct_detector ──
dash = "他——顿了顿——又说——这话——实在——荒唐——太荒唐——了——吧——七个——以上"
md = "这段有 **加粗** 和 # 标题标记，还带 —— 破折号"
clean = "他顿了顿，又说这话实在荒唐。"
for name, t in [("破折号密集", dash), ("Markdown+破折号", md), ("干净", clean)]:
    r = punct_detector(t)
    print(f"{name}: punct_score={r['punct_score']} signals={r['signals']}")
assert punct_detector(dash)["punct_score"] < 0.5, "破折号密集应低分"
assert punct_detector(md)["punct_score"] < 0.7, "Markdown 应低分"
assert punct_detector(clean)["punct_score"] >= 0.9, "干净应高分"
print("punct_detector 单测 PASS")

# ── 真机：inspire/chat + workbench 日志 system ──
import requests

BOOK = r"C:\Users\24357\Desktop\AInovel写作系统\小说系统\草稿"
r = requests.post("http://127.0.0.1:8765/api/prompt-harness/ai-creation/inspire/chat", json={
    "book_root": BOOK,
    "messages": [{"role": "user", "content": "用文字写一段陆知砚在寿宴上砸酒壶怼周主事的场景，含对白，别解释。"}],
    "mech": "free", "ctx": {},
}, timeout=300)
d = r.json()
reply = d.get("reply") or ""
print("\ninspire/chat ok=", d.get("ok"), "| reply 前 80:", reply[:80])
print("reply 破折号数:", reply.count("——"), "| Markdown(**):", reply.count("**"))

WBLOG = Path(r"C:\Users\24357\Desktop\AInovel写作系统\小说系统\ainovel-write\prompt-harness\logs\workbench\C__Users_24357_Desktop_AInovel Harness_小说系统_草稿\2026-08-10.jsonl")
sys_block = ""
for l in reversed([l for l in WBLOG.read_text(encoding="utf-8").splitlines() if l.strip()]):
    try:
        rec = json.loads(l)
    except Exception:
        continue
    if rec.get("call_type") == "inspire_chat":
        sys_block = rec.get("system") or ""
        break
print("\nsystem 注入检查:")
for kw in ["禁止使用破折号", "不是……而是", "不是……是", "省略号", "防御性写作",
           "l1 不变prompt", "l5 不变prompt", "段首多样化"]:
    print(f"  含[{kw}]:", kw in sys_block)

assert "禁止使用破折号" in sys_block, "baseline_guard 未注入！"
assert "不是……而是" in sys_block, "判断句禁令未注入！"
assert "省略号" in sys_block, "省略号规则未注入！"
print("\nPASS: baseline_guard 固定 prompt 已注入灵感工坊")
