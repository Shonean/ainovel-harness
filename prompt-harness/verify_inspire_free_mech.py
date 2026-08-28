"""验证灵感工坊新增「自由对话」机制（改动只测改动部分）。
直连 _inspire_mech_system，检查 4 机制 + 未知 mech 兜底。"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from prompt_harness.ai_creation import _inspire_mech_system

OUT = Path(__file__).parent / "harness_runs" / "inspire_free_mech"
OUT.mkdir(parents=True, exist_ok=True)

results = {}
for mech in ["seed", "analog", "invert", "free", "unknown"]:
    s = _inspire_mech_system(mech)
    results[mech] = s
    print(f"[{mech}] {s[:50]}…")

# 断言：4 机制各有专属文案；未知 mech 兜底自由对话
assert "种子推演" in results["seed"]
assert "类比迁移" in results["analog"]
assert "约束反转" in results["invert"]
assert "自由对话" in results["free"]
assert "自由对话" in results["unknown"]

print("ALL PASS")
json.dump(results, open(OUT / "mech_system.json", "w", encoding="utf-8"),
          ensure_ascii=False, indent=2)
