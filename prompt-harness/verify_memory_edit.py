"""验证候选记忆编辑后同意（approve_pending 带 edits.text）保存正确。
用临时书目录，不污染用户数据。产出 → harness_runs/memory_edit_test/"""
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from prompt_harness.ai_creation import approve_pending, load_pending, load_memory, add_pending

OUT = Path(__file__).parent / "harness_runs" / "memory_edit_test"
OUT.mkdir(parents=True, exist_ok=True)

tmp = Path(tempfile.mkdtemp())
dot = tmp / ".ainovel"
dot.mkdir()

# 构造一条 mem 候选
add_pending(tmp, {"type": "mem", "text": "原始记忆：陆知砚性格谨慎"})
items = load_pending(tmp)
pid = items[0]["id"]
print("候选:", items[0]["text"])

# 编辑后同意
ok = approve_pending(tmp, pid, {"text": "编辑后的记忆：陆知砚表面谨慎、骨子里傲慢"})
print("approve(带编辑文本) ok:", ok)

mems = load_memory(tmp)
print("memory 条数:", len(mems))
if mems:
    print("保存的记忆文本:", mems[0]["text"])
    assert mems[0]["text"] == "编辑后的记忆：陆知砚表面谨慎、骨子里傲慢", "保存的不是编辑后的文本！"
assert ok and len(mems) == 1
assert len(load_pending(tmp)) == 0, "审批后候选应移除"

(OUT / "result.json").write_text(json.dumps({
    "approved_text": mems[0]["text"],
    "pending_after": len(load_pending(tmp)),
}, ensure_ascii=False, indent=2), encoding="utf-8")
print("PASS: 记忆编辑后同意保存正确")
