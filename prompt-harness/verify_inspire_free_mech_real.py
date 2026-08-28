"""真机验证灵感工坊 mech=free 端到端：inspire/chat 端点。
产出 → harness_runs/inspire_free_mech/run1_response.json"""
import json
import sys
from pathlib import Path

import requests

OUT = Path(__file__).parent / "harness_runs" / "inspire_free_mech"
OUT.mkdir(parents=True, exist_ok=True)

URL = "http://127.0.0.1:8765/api/prompt-harness/ai-creation/inspire/chat"
payload = {
    "book_root": r"C:\Users\24357\Desktop\AInovel写作系统\小说系统\草稿",
    "messages": [
        {"role": "user",
         "content": "我有个设定：主角会炼制让人返老还童的丹药，但丹药要吞服者折寿十年。随便聊聊这个设定能长出什么故事。"}
    ],
    "mech": "free",
    "ctx": {},
}
r = requests.post(URL, json=payload, timeout=180)
data = r.json()
resp_path = OUT / "run1_response.json"
resp_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

print("HTTP", r.status_code)
print("ok =", data.get("ok"), "| mech =", data.get("mech"), "| preset =", data.get("preset"))
print("pending_count =", data.get("pending_count"))
reply = data.get("reply", "")
print("reply 前 120 字:", reply[:120])
print("saved ->", resp_path)

assert r.status_code == 200
assert data.get("ok") is True
assert data.get("mech") == "free"
assert reply
# 自由对话 = 直接围绕抛出的想法回应，不强制套固定结构
print("REAL TEST PASS")
