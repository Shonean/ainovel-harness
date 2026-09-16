"""改编层 v0 — designer 单测（spec §8；LLM mock，断言结构不变与约束生效）。

- mock LLM 合法输出 → 章末收敛 choice + 双结局按输出装配；
- mock 输出违反约束（数字/新 flag/超 3 句/选项数越界）→ 整体确定性 fallback；
- attach_branches 后结构固定：末场景 → c001 → n900 → end_a/end_b，
  选项全部 converge，condition 仅 flags.* 布尔。
运行：pytest prompt-harness/tests/test_adaptation_designer.py -q
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

_PH_SRC = Path(__file__).resolve().parent.parent
if str(_PH_SRC) not in sys.path:
    sys.path.insert(0, str(_PH_SRC))

from prompt_harness.adaptation import designer as ds  # noqa: E402
from prompt_harness.adaptation.mapper import MappedPack  # noqa: E402

# ---- 公共 mock ----

class _Hook:
    def __init__(self, mapping: dict[str, object]):
        self.mapping = mapping
        self.calls: list[str] = []

    async def __call__(self, payload: dict):
        task = payload.get("task")
        self.calls.append(task)
        item = self.mapping[task]
        if isinstance(item, Exception):
            raise item
        return item


GOOD_CHOICE = {
    "prompt": "面对这间教室，你要先做什么？",
    "options": [
        {"text": "先看那张值日表", "flag": "read_chart"},
        {"text": "去检查后排储物柜", "flag": "search_cabinet"},
    ],
}
GOOD_ENDINGS = {
    "endings": [
        {"title": "直面循环", "text": "他握紧扳指，推开了那扇再也关不上的门。", "flag": "read_chart"},
        {"title": "暂避锋芒", "text": "他退回座位，把名字纸巾折好收起。答案会等他。", "flag": ""},
    ],
}


def _make_mp() -> MappedPack:
    """最小 MappedPack：两个 scene 节点（mapper 输出形态）。"""
    mp = MappedPack()
    mp.story = {"start": "n001", "nodes": [
        {"id": "n001", "type": "scene", "title": "s1", "background": None,
         "present": ["char_001"], "lines": [{"kind": "narration", "text": "a"}],
         "interaction_refs": [], "next": "n002", "source": {}},
        {"id": "n002", "type": "scene", "title": "s2", "background": None,
         "present": ["char_001"], "lines": [{"kind": "narration", "text": "b"}],
         "interaction_refs": [], "next": None, "source": {}},
    ]}
    mp.scene_nodes = ["n001", "n002"]
    mp.llm_zones = {"zones": [], "global": {"rule": "", "forbidden": []}}
    return mp


def _design(mp: MappedPack, mapping: dict, **kw) -> tuple[ds.DesignerResult, _Hook]:
    hook = _Hook(mapping)
    res = asyncio.run(ds.design_branches(
        conflicts=["c"], chapter_core="core", l2_text="l2",
        known_names=["陈守念"], llm_hook=hook, **kw))
    ds.attach_branches(mp, res, 1, "arc_x", mp.llm_zones)
    return res, hook


def test_llm_payload_attached_structure_fixed():
    mp = _make_mp()
    res, hook = _design(mp, {"design_choice": GOOD_CHOICE, "design_endings": GOOD_ENDINGS})
    assert hook.calls == ["design_choice", "design_endings"]
    ids = [n["id"] for n in mp.story["nodes"]]
    # 结构固定：原 scene + choice + 收束 + 双结局
    assert ids == ["n001", "n002", "c001", "n900", "end_a", "end_b"]
    # 末场景 next 接 choice；其余 scene.next 不变（结构不变约束）
    assert mp.story["nodes"][0]["next"] == "n002"
    assert mp.story["nodes"][1]["next"] == "c001"
    choice = next(n for n in mp.story["nodes"] if n["id"] == "c001")
    # 全部选项 converge 到同一节点，效果仅 flags.* 布尔
    assert {o["next"] for o in choice["options"]} == {"n900"}
    assert choice["converge_to"] == "n900"
    assert all(set(o["set"]) == {"flags.read_chart"} or set(o["set"]) == {"flags.search_cabinet"}
               for o in choice["options"])
    assert all(v is True for o in choice["options"] for v in o["set"].values())
    # 结局：条件结局 condition 仅 flags.*；默认结局 condition=None
    end_a = next(n for n in mp.story["nodes"] if n["id"] == "end_a")
    end_b = next(n for n in mp.story["nodes"] if n["id"] == "end_b")
    assert end_a["condition"] == "flags.read_chart"
    assert end_b["condition"] is None
    assert res.warnings == []
    # llm_zone 预留字段挂到 choice
    assert mp.llm_zones["zones"][0]["node"] == "c001"
    assert mp.story["endings"] == ["end_a", "end_b"]


def test_invalid_choice_payload_falls_back():
    bad = {"prompt": "p", "options": [
        {"text": "含1数字", "flag": "a"},
        {"text": "b", "flag": "b"},
        {"text": "c", "flag": "c"},
        {"text": "d", "flag": "d"},
    ]}  # 4 个选项 + 选项文本含数字
    mp = _make_mp()
    res, _ = _design(mp, {"design_choice": bad, "design_endings": GOOD_ENDINGS})
    codes = [w["code"] for w in res.warnings]
    assert "DESIGNER_FALLBACK" in codes
    choice = next(n for n in mp.story["nodes"] if n["id"] == "c001")
    assert choice["prompt"] == "此刻，你要怎么做？"


def test_invalid_endings_fall_back():
    bad = {"endings": [
        {"title": "t", "text": "第1局结束。", "flag": "read_chart"},  # 数字
        {"title": "t2", "text": "x", "flag": ""},
    ]}
    mp = _make_mp()
    res, _ = _design(mp, {"design_choice": GOOD_CHOICE, "design_endings": bad})
    assert any(w["code"] == "DESIGNER_FALLBACK" for w in res.warnings)
    end_a = next(n for n in mp.story["nodes"] if n["id"] == "end_a")
    assert end_a["condition"] == "flags.confront"  # 兜底条件 flag 来自兜底选项


def test_ending_condition_must_use_choice_flag():
    """结局判定 flag 必须来自本次选择建立的 flag（结局差异来自已建立的选择）。"""
    bad = {"endings": [
        {"title": "t", "text": "他推门离开。", "flag": "never_established"},
        {"title": "t2", "text": "他留了下来。", "flag": ""},
    ]}
    mp = _make_mp()
    res, _ = _design(mp, {"design_choice": GOOD_CHOICE, "design_endings": bad})
    assert any(w["code"] == "DESIGNER_FALLBACK" for w in res.warnings)


def test_endings_max_three_sentences():
    long_text = "一句。两句。三句。四句。"
    bad = {"endings": [
        {"title": "t", "text": long_text, "flag": "read_chart"},
        {"title": "t2", "text": "x", "flag": ""},
    ]}
    mp = _make_mp()
    res, _ = _design(mp, {"design_choice": GOOD_CHOICE, "design_endings": bad})
    assert any(w["code"] == "DESIGNER_FALLBACK" for w in res.warnings)


def test_no_llm_hook_deterministic():
    mp = _make_mp()
    res = asyncio.run(ds.design_branches(
        conflicts=["c"], chapter_core="core", l2_text="l2",
        known_names=["陈守念"], llm_hook=None))
    ds.attach_branches(mp, res, 1, "arc_x", mp.llm_zones)
    ids = [n["id"] for n in mp.story["nodes"]]
    assert ids == ["n001", "n002", "c001", "n900", "end_a", "end_b"]
    assert any(w["code"] == "DESIGNER_FALLBACK" for w in res.warnings)


def test_ending_count_fixed_warning():
    res = asyncio.run(ds.design_branches(
        conflicts=[], chapter_core="", l2_text="", known_names=[],
        llm_hook=None, ending_count=3))
    assert any(w["code"] == "ENDING_COUNT_FIXED" for w in res.warnings)


def test_no_scenes_attach_is_noop():
    mp = _make_mp()
    mp.scene_nodes = []
    res = asyncio.run(ds.design_branches(
        conflicts=[], chapter_core="", l2_text="", known_names=[], llm_hook=None))
    ds.attach_branches(mp, res, 1, "arc_x", mp.llm_zones)
    assert [n["id"] for n in mp.story["nodes"]] == ["n001", "n002"]
