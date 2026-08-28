"""真机验证 AI 味防线三项（2026-08-10）：
1. 灵感工坊 system 注入「禁止防御性写作」+ AI味禁令 + l5 规则（查 workbench 日志）
2. depollute 端点对 AI 味文本去味（before.score < after.score）
产出 → harness_runs/ai_flavor_guard/"""
import json
from pathlib import Path

import requests

OUT = Path(__file__).parent / "harness_runs" / "ai_flavor_guard"
OUT.mkdir(parents=True, exist_ok=True)
BOOK = r"C:\Users\24357\Desktop\AInovel写作系统\小说系统\草稿"
BASE = "http://127.0.0.1:8765/api/prompt-harness"
WBLOG = Path(r"C:\Users\24357\Desktop\AInovel写作系统\小说系统\ainovel-write\prompt-harness\logs\workbench\C__Users_24357_Desktop_AInovel Harness_小说系统_草稿\2026-08-10.jsonl")

# ── 1. 灵感工坊 system 注入 ──
r1 = requests.post(f"{BASE}/ai-creation/inspire/chat", json={
    "book_root": BOOK,
    "messages": [{"role": "user", "content": "给一个剧情点子：一个称病三年的托孤首辅，让儿子去夺嫡。"}],
    "mech": "free", "ctx": {},
}, timeout=180)
print("inspire/chat:", r1.status_code, "ok=", r1.json().get("ok"))

# 从 workbench 日志最新 inspire_chat 记录查 system
sys_block = ""
lines = []
if WBLOG.exists():
    lines = [l for l in WBLOG.read_text(encoding="utf-8").splitlines() if l.strip()]
    for l in reversed(lines):
        try:
            rec = json.loads(l)
        except Exception:
            continue
        if rec.get("call_type") == "inspire_chat":
            sys_block = rec.get("system") or ""
            break
inject_ok = "禁止防御性写作" in sys_block and "AI味禁令" in sys_block and "全系统写作规则" in sys_block
print("system 注入检查: 禁止防御性写作=%s AI味禁令=%s l5规则=%s" % (
    "禁止防御性写作" in sys_block, "AI味禁令" in sys_block, "全系统写作规则" in sys_block))
assert inject_ok, "inspire_chat system 未注入禁令!"

# ── 2. depollute 去味 ──
sample = (
    '酒盏砸在汉白玉砖上的脆响，惊飞了檐下的八哥。满席的珊瑚树、琉璃盏都像是晃了晃。'
    '没人想到，素来以"温吞谨慎"出名的翰林院修撰陆知砚，会在英国公的寿宴上，当众把杯子砸在李党骨干、吏部郎中张维的嫡子脸上。'
    '这话听着是赔罪，可半句没提罚陆知砚，反倒把"小孩子闹脾气"坐实了——等于明着说：我陆家的孩子，闹了也就闹了。'
)
r2 = requests.post(f"{BASE}/ai-creation/inspire/depollute", json={
    "book_root": BOOK, "text": sample, "ctx": {},
}, timeout=180)
d2 = r2.json()
print("depollute:", r2.status_code, "ok=", d2.get("ok"), "clean=", d2.get("clean"))
b, a = d2.get("before") or {}, d2.get("after") or {}
print("before.score =", b.get("score"), "| after.score =", a.get("score"))
rewritten = d2.get("rewritten") or ""
print("rewritten 前 150 字:", rewritten[:150])

(OUT / "run1_response.json").write_text(json.dumps({
    "inspire_injected": inject_ok,
    "depollute": {"before_score": b.get("score"), "after_score": a.get("score"),
                  "clean": d2.get("clean"), "rewritten_head": rewritten[:200]},
}, ensure_ascii=False, indent=2), encoding="utf-8")

assert d2.get("ok"), "depollute 失败"
if not d2.get("clean"):
    assert (a.get("score") or 0) >= (b.get("score") or 0), "去味后分数应提升"
print("ALL PASS")
