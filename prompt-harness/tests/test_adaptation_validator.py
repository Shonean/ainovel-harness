"""改编层 v0 — 校验器单测（spec §10 规则全集，坏 pack 逐条命中）。

基线 pack 取自规格 §18 最小示例；逐条变异触发：
悬空 next / 死节点 / 未知 speaker / 环 / 不可达 / 缺文件 / 版本错 /
非法 stage.cue / 未建立 flag 的 condition / 不收敛选择 / 重复资产。
运行：pytest prompt-harness/tests/test_adaptation_validator.py -q
"""

from __future__ import annotations

import copy
import sys
from pathlib import Path

_PH_SRC = Path(__file__).resolve().parent.parent
if str(_PH_SRC) not in sys.path:
    sys.path.insert(0, str(_PH_SRC))

from prompt_harness.adaptation.models import (  # noqa: E402
    Asset, AssetsFile, Character, CharactersFile, PackData, PackInfo, Story,
    WorldFile, PackSource, PackGame, PackFiles, PackModes,
)
from prompt_harness.adaptation.validator import validate_data  # noqa: E402


def _spec18_story() -> dict:
    """规格 §18 最小示例（单场景 + 1 选择 + 2 结局）。"""
    return {
        "start": "n001",
        "nodes": [
            {"id": "n001", "type": "scene", "title": "醒来", "background": "bg_room",
             "present": ["char_a"],
             "lines": [{"kind": "inner", "text": "这是哪儿？"}],
             "next": "c001"},
            {"id": "c001", "type": "choice", "prompt": "怎么办？", "converge_to": "n002",
             "options": [
                 {"text": "查看纸条", "next": "n002", "set": {"flags.read": True}},
                 {"text": "离开教室", "next": "n002", "set": {"flags.left": True}},
             ]},
            {"id": "n002", "type": "scene", "title": "面临真相", "background": "bg_room",
             "present": ["char_a"],
             "lines": [{"kind": "narration", "text": "……"}],
             "next": "end_a"},
            {"id": "end_a", "type": "ending", "title": "接受", "text": "……",
             "condition": "flags.read"},
            {"id": "end_b", "type": "ending", "title": "拒绝", "text": "……",
             "condition": None},
        ],
    }


def _pack(story: dict | None = None) -> PackData:
    return PackData(
        info=PackInfo(
            pack_id="pack_x_arc_000000",
            created_at="2026-09-12T00:00:00+00:00",
            source=PackSource(book_title="t", book_root="r", arc_ids=["a"],
                              chapter_nums=[1]),
            game=PackGame(entry_node=(story or _spec18_story())["start"],
                          endings=["end_a", "end_b"]),
        ),
        story=Story(**(story or _spec18_story())),
        characters=CharactersFile(characters=[Character(
            id="char_a", name="甲", role="protagonist")]),
        world=WorldFile(),
        assets=AssetsFile(assets=[{
            "asset_id": "bg_room", "kind": "background", "ref": None,
            "prompt": "房间", "used_by": ["n001", "n002"], "status": "pending",
        }]),
    )


def _codes(report) -> list[str]:
    return [e.code for e in report.errors]


def test_good_spec18_pack_passes():
    report = validate_data(_pack())
    assert report.ok, report.errors
    assert report.stats.nodes == 5 and report.stats.endings == 2


def test_dangling_next():
    story = _spec18_story()
    story["nodes"][0]["next"] = "ghost"
    report = validate_data(_pack(story))
    assert "DANGLING_NEXT" in _codes(report)


def test_dead_end_scene():
    story = _spec18_story()
    story["nodes"][2]["next"] = ""  # 非结局节点无出边
    report = validate_data(_pack(story))
    assert "DEAD_END" in _codes(report)


def test_unknown_speaker():
    story = _spec18_story()
    story["nodes"][0]["lines"].append(
        {"kind": "dialogue", "text": "……", "speaker": "char_ghost"})
    report = validate_data(_pack(story))
    assert "UNKNOWN_SPEAKER" in _codes(report)


def test_speaker_missing():
    story = _spec18_story()
    story["nodes"][0]["lines"].append({"kind": "dialogue", "text": "……", "speaker": None})
    report = validate_data(_pack(story))
    assert "SPEAKER_MISSING" in _codes(report)


def test_cycle_forbidden():
    story = _spec18_story()
    story["nodes"][2]["next"] = "c001"  # n002 → c001 → n002
    report = validate_data(_pack(story))
    assert "CYCLE_FORBIDDEN" in _codes(report)


def test_unreachable_node_and_ending():
    story = _spec18_story()
    story["nodes"][0]["next"] = "end_b"  # c001/n002/end_a 全部悬空
    report = validate_data(_pack(story))
    codes = _codes(report)
    assert "UNREACHABLE_NODE" in codes
    assert "UNREACHABLE_ENDING" in codes  # end_a 不可达


def test_duplicate_node_id():
    story = _spec18_story()
    dup = copy.deepcopy(story["nodes"][0])
    story["nodes"].append(dup)
    report = validate_data(_pack(story))
    assert "DUPLICATE_NODE_ID" in _codes(report)


def test_invalid_stage_cue():
    story = _spec18_story()
    story["nodes"][0]["lines"].append({"kind": "stage", "text": "演出", "cue": "dance"})
    report = validate_data(_pack(story))
    assert "INVALID_STAGE_CUE" in _codes(report)


def test_empty_line_text():
    story = _spec18_story()
    story["nodes"][0]["lines"].append({"kind": "narration", "text": "  "})
    report = validate_data(_pack(story))
    assert "EMPTY_TEXT" in _codes(report)


def test_dangling_background():
    story = _spec18_story()
    story["nodes"][0]["background"] = "bg_ghost"
    report = validate_data(_pack(story))
    assert "DANGLING_BACKGROUND" in _codes(report)


def test_not_convergent_choice():
    story = _spec18_story()
    story["nodes"][1]["options"][1]["next"] = "n001"  # 选项 next 不一致
    report = validate_data(_pack(story))
    codes = _codes(report)
    assert "NOT_CONVERGENT" in codes
    assert "CYCLE_FORBIDDEN" in codes  # 同时构成环


def test_condition_flag_not_established():
    story = _spec18_story()
    story["nodes"][3]["condition"] = "flags.never_set"
    report = validate_data(_pack(story))
    assert "UNKNOWN_CONDITION_FLAG" in _codes(report)


def test_condition_must_be_flags_expr():
    story = _spec18_story()
    story["nodes"][3]["condition"] = "affinity > 3"
    report = validate_data(_pack(story))
    assert "INVALID_CONDITION" in _codes(report)


def test_duplicate_and_dangling_assets():
    pack = _pack()
    pack.assets.assets.append(pack.assets.assets[0].model_copy())  # asset_id 重复
    pack.assets.assets.append(Asset(**{
        "asset_id": "bg_x", "kind": "background", "ref": None, "prompt": "x",
        "used_by": ["ghost_node"], "status": "pending"}))
    report = validate_data(pack)
    codes = _codes(report)
    assert "DUPLICATE_ASSET_ID" in codes
    assert "DANGLING_USED_BY" in codes


def test_missing_required_file():
    report = validate_data(_pack(), files_present=("pack.json", "story.json"))
    codes = _codes(report)
    assert "MISSING_FILE" in codes
    assert any("characters.json" in e.detail for e in report.errors)


def test_wrong_schema_version():
    pack = _pack()
    pack.info.schema_version = "0.2"
    report = validate_data(pack)
    assert "SCHEMA_VERSION" in _codes(report)


def test_unknown_present_character():
    story = _spec18_story()
    story["nodes"][0]["present"] = ["char_nobody"]
    report = validate_data(_pack(story))
    assert "UNKNOWN_CHARACTER" in _codes(report)
