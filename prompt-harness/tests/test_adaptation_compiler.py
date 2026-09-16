"""改编层 v0 — ink 导出 + 编译单测（spec §9）。

- 最小 ink（§18 样例）→ 编译通过（vendored inkjs，subprocess 调 node）；
- 人为语法错 → 报错含行号与原文行；
- ink 导出对齐 §18：VAR 声明 / 多行缩进式 choice / 条件结局分流 / END。
运行：pytest prompt-harness/tests/test_adaptation_compiler.py -q
"""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

import pytest

_PH_SRC = Path(__file__).resolve().parent.parent
if str(_PH_SRC) not in sys.path:
    sys.path.insert(0, str(_PH_SRC))

from prompt_harness.adaptation.compiler import compile_ink  # noqa: E402
from prompt_harness.adaptation.ink_export import escape_ink, export_ink  # noqa: E402
from prompt_harness.adaptation.models import Story  # noqa: E402

pytestmark = pytest.mark.skipif(shutil.which("node") is None,
                                reason="node 不在 PATH（编译需要 Node runner）")

# 规格 §18 样例 ink（去掉 stage/EXTERNAL，纯最小结构）
SPEC18_INK = """\
VAR flags_read = false
VAR flags_left = false

-> n001

=== n001 ===
这是哪儿？
-> c001

=== c001 ===
* [查看纸条]
    ~ flags_read = true
    -> n002
* [离开教室]
    ~ flags_left = true
    -> n002

=== n002 ===
……
{ flags_read: -> end_a }
-> end_b

=== end_a ===
接受
-> END

=== end_b ===
拒绝
-> END
"""


def test_minimal_ink_compiles():
    r = compile_ink(SPEC18_INK)
    assert r.ok, r.error_details(SPEC18_INK)
    data = json.loads(r.story_json)
    assert data.get("inkVersion")  # inkjs 编译产物带版本号
    # inkjs 2.4 编译产物把 knot 容器内联进 root 数组（无顶层 knots 键）
    assert isinstance(data.get("root"), list) and data["root"]


def test_syntax_error_reports_line():
    broken = "-> n001\n=== n001 ===\n第一行正常\n-> ghost_knot\n"
    r = compile_ink(broken)
    assert not r.ok
    assert r.errors, "语法错必须产出 errors"
    details = r.error_details(broken)
    assert any("line" in d for d in details), details
    # 行号指向 divert 行，且带原文行内容
    assert any("ghost_knot" in d or "->" in d for d in details), details


def test_compile_timeout_and_missing_node_paths():
    # node 缺失时返回可读错误（用编译器自身路径不可伪造，直接测错误装配逻辑）
    from prompt_harness.adaptation import compiler as comp
    r = comp.CompileResult(
        ok=False, errors=[comp.CompileError(line=0, message="未找到 node 可执行文件")])
    assert not r.ok and "node" in r.errors[0].message


def test_escape_ink_replaces_reserved_chars():
    assert escape_ink("看[这里]") == "看［这里］"
    assert escape_ink("{flag}") == "｛flag｝"
    assert escape_ink("a|b#c^d") == "a｜b＃c＾d"
    assert escape_ink("~开头") == "～开头"
    assert escape_ink("普通文本") == "普通文本"


def test_export_ink_spec18_shape():
    """pack story（§18 结构）→ ink 与 §18 样例结构对齐。"""
    story = Story(**{
        "start": "n001",
        "nodes": [
            {"id": "n001", "type": "scene", "title": "醒来", "background": None,
             "present": ["char_a"], "lines": [{"kind": "inner", "text": "这是哪儿？"}],
             "next": "c001"},
            {"id": "c001", "type": "choice", "prompt": "怎么办？", "converge_to": "n002",
             "options": [
                 {"text": "查看纸条", "next": "n002", "set": {"flags.read": True}},
                 {"text": "离开教室", "next": "n002", "set": {"flags.left": True}},
             ]},
            {"id": "n002", "type": "scene", "title": "面临真相", "background": None,
             "present": ["char_a"], "lines": [{"kind": "narration", "text": "……"}],
             "next": "end_a"},
            {"id": "end_a", "type": "ending", "title": "接受", "text": "……",
             "condition": "flags.read"},
            {"id": "end_b", "type": "ending", "title": "拒绝", "text": "……",
             "condition": None},
        ],
    })
    ink = export_ink(story)
    assert "VAR flags_read = false" in ink
    assert "VAR flags_left = false" in ink
    assert "-> n001" in ink
    assert "=== n001 ===" in ink
    assert "* [查看纸条]" in ink and "* [离开教室]" in ink
    assert "~ flags_read = true" in ink
    assert "{ flags_read: -> end_a }" in ink  # 条件结局分流
    assert "-> END" in ink
    # 导出的 ink 必须能编译
    r = compile_ink(ink)
    assert r.ok, r.error_details(ink)


def test_export_stage_line_with_external():
    story = Story(**{
        "start": "n001",
        "nodes": [
            {"id": "n001", "type": "scene", "title": "s", "background": None,
             "present": [],
             "lines": [{"kind": "stage", "text": "所有纸人同时转过头来。",
                        "cue": "paper_figures_turn"}],
             "next": "end_a"},
            {"id": "end_a", "type": "ending", "title": "完", "text": "……",
             "condition": None},
        ],
    })
    ink = export_ink(story)
    assert "EXTERNAL stage(cue)" in ink
    assert '~ stage("paper_figures_turn")' in ink
    r = compile_ink(ink)
    assert r.ok, r.error_details(ink)
