"""测试灵感工坊对话记忆（Kimi 式）端到端：
AI 自动提取记忆 → 候选审批 → 进 memory.json → 后续对话注入引用。
测试完自动清理（恢复测试前状态）。
产出 → harness_runs/memory_test/"""
import json
from pathlib import Path
import requests

BASE = "http://127.0.0.1:8765/api/prompt-harness"
BOOK = r"C:\Users\24357\Desktop\AInovel写作系统\小说系统\草稿"
MEM_PATH = Path(r"C:\Users\24357\Desktop\AInovel写作系统\小说系统\草稿\.ainovel\memory.json")
OUT = Path(__file__).parent / "harness_runs" / "memory_test"
OUT.mkdir(parents=True, exist_ok=True)


def chat(msg, hist=None):
    messages = (hist or []) + [{"role": "user", "content": msg}]
    r = requests.post(f"{BASE}/ai-creation/inspire/chat",
                      json={"book_root": BOOK, "messages": messages, "mech": "free", "ctx": {}},
                      timeout=300)
    return r.json()


def pending():
    r = requests.get(f"{BASE}/ai-creation/pending", params={"book_root": BOOK}, timeout=60)
    return r.json().get("items", [])


def approve(pid):
    requests.post(f"{BASE}/ai-creation/pending/{pid}/approve",
                  json={"book_root": BOOK, "edits": {}}, timeout=60)


def reject(pid):
    requests.post(f"{BASE}/ai-creation/pending/{pid}/reject",
                  params={"book_root": BOOK}, timeout=60)


def mem_items():
    if not MEM_PATH.exists():
        return []
    return json.load(open(MEM_PATH, encoding="utf-8"))


# 1. 记录测试前状态
before_ids = {p["id"] for p in pending()}
before_mem = [m.get("id") for m in mem_items()]
print("测试前 pending:", len(before_ids), "| memory:", len(before_mem))

# 2. 第一轮：抛明确设定
r1 = chat("我的主角叫林晚，患有失眠症，能预知未来三天的梦。她住在上海，是个急诊科医生。这些设定对后续剧情很重要。")
reply1 = r1.get("reply", "")
print("\n=== 第一轮回复（len=%d）===" % len(reply1))
print(reply1[:200])

# 3. 检查新增记忆候选
new_pending = [p for p in pending() if p["id"] not in before_ids]
new_mem = [p for p in new_pending if p.get("type") == "mem"]
print("\n=== 新增记忆候选:", len(new_mem), "===")
for m in new_mem:
    print("  ·", m.get("text"))

# 4. 审批新增记忆
for m in new_mem:
    approve(m["id"])
print("已审批", len(new_mem), "条记忆")

# 5. 检查 memory.json
after_mem = mem_items()
new_mem_ids = [m.get("id") for m in after_mem if m.get("id") not in before_mem]
print("\n=== memory.json 现在:", len(after_mem), "条（新增", len(new_mem_ids), "）===")
for m in after_mem:
    if m.get("id") in new_mem_ids:
        print("  ·", str(m.get("text"))[:60])

# 6. 第二轮：引用验证（新会话，无历史）
r2 = chat("你还记得林晚的设定吗？用一句话说说她是谁。")
reply2 = r2.get("reply", "")
print("\n=== 第二轮回复（len=%d）===" % len(reply2))
print(reply2[:250])
hits = [k for k in ["林晚", "失眠", "预知", "梦", "医生", "上海"] if k in reply2]
print("命中设定关键词:", hits)
mem_ok = len(new_mem_ids) > 0 and len(hits) >= 2

# 7. 清理：删除新增记忆 + 恢复 pending
for m in after_mem:
    if m.get("id") in new_mem_ids:
        requests.delete(f"{BASE}/ai-creation/memory/{m['id']}",
                        params={"book_root": BOOK}, timeout=60)
for p in new_pending:
    reject(p["id"])
print("\n已清理测试数据（memory 与 pending 恢复测试前）")

(OUT / "memory_test.json").write_text(json.dumps({
    "new_mem_candidates": [m.get("text") for m in new_mem],
    "reply2": reply2,
    "hits": hits,
    "memory_ok": mem_ok,
}, ensure_ascii=False, indent=2), encoding="utf-8")
print("\nRESULT: 记忆提取+注入", "✓ 好用" if mem_ok else "✗ 有问题")
