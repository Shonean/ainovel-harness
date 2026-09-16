"""改编层 v0 — mapper 单测（spec §13）。

- 真实 l4 片段 fixture → map_snapshot 输出与 golden file 逐字节稳定；
- §6 映射规则逐条断言：environment 去重/psychologies→inner/details→narration/
  actions→narration/dialogues 占位/conflicts 收集、elements 投影、资产清单。
运行：pytest prompt-harness/tests/test_adaptation_mapper.py -q
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

_PH_SRC = Path(__file__).resolve().parent.parent
if str(_PH_SRC) not in sys.path:
    sys.path.insert(0, str(_PH_SRC))

from prompt_harness.adaptation.loader import ArcSnapshot, ChapterOutline  # noqa: E402
from prompt_harness.adaptation.mapper import compute_pack_id, map_snapshot  # noqa: E402

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "adaptation"


def _load_fixture() -> dict:
    return json.loads((FIXTURES / "l4_fixture.json").read_text(encoding="utf-8"))


def _golden_bytes(data: dict) -> bytes:
    text = json.dumps(data, ensure_ascii=False, sort_keys=True, indent=1)
    return (text + "\n").encode("utf-8")


def _snap_from_fixture() -> ArcSnapshot:
    fx = _load_fixture()
    ch = fx["chapter"]
    return ArcSnapshot(
        book_root=Path("C:/fake/TestBook"),
        book_title="TestBook",
        arc_id=fx["arc_id"],
        arc_name=fx["arc_name"],
        l1=fx["l1"],
        l2=fx["l2"],
        chapter=ChapterOutline(num=ch["num"], title=ch["title"], core=ch["core"],
                               beats=list(ch["beats"])),
        chapter_num=ch["num"],
        scenes=fx["scenes"],
        elements=fx["elements"],
        selected=fx["selected"],
        key_facts=["少年林羽在宗门大比中意外觉醒上古血脉"],
        levels={},
    )


def _mapped_dict() -> dict:
    mp = map_snapshot(_snap_from_fixture())
    return {
        "story": mp.story,
        "characters": mp.characters,
        "world": mp.world,
        "assets": mp.assets,
        "interaction": mp.interaction,
        "llm_zones": mp.llm_zones,
        "scene_nodes": mp.scene_nodes,
        "conflicts": mp.conflicts,
        "raw_dialogues": mp.raw_dialogues,
        "protagonist_char": mp.protagonist_char,
        "warnings": mp.warnings,
    }


def test_mapper_golden_byte_stable():
    """真实 l4 片段 → 输出逐字节等于 golden file（重跑两次校验稳定）。"""
    golden_path = FIXTURES / "golden_map_output.json"
    assert golden_path.is_file(), "golden file 缺失"
    golden = golden_path.read_bytes()
    assert _golden_bytes(_mapped_dict()) == golden
    # 第二次运行结果必须完全一致（确定性）
    assert _golden_bytes(_mapped_dict()) == golden


def test_scene_node_mapping_rules():
    """§6：行类型映射逐条断言。"""
    mp = map_snapshot(_snap_from_fixture())
    node = mp.story["nodes"][0]
    kinds = [ln["kind"] for ln in node["lines"]]
    # environment 开场 narration → actions/narration → details(detail=true) → inner → dialogue
    assert kinds[0] == "narration"
    assert node["lines"][0]["text"].startswith("深夜的旧教室")
    assert "inner" in kinds and "dialogue" in kinds
    detail_lines = [ln for ln in node["lines"] if ln.get("detail")]
    assert detail_lines and all(ln["kind"] == "narration" for ln in detail_lines)
    # 对白占位：未解析（speaker=None），原文进 raw_dialogues
    dlgs = [ln for ln in node["lines"] if ln["kind"] == "dialogue"]
    assert len(dlgs) == 2 and all(ln["speaker"] is None for ln in dlgs)
    assert mp.raw_dialogues["n001"] == [
        "陈守念对着空教室低声问道：“这是哪儿？”",
        "他又问了一遍，声音卡在喉咙里：“我叫什么……我叫什么？”",
    ]
    # present：场景文本提及的角色
    assert node["present"] == ["char_001"]
    # conflicts 只收集为 choice 素材，不成节点
    assert len(mp.conflicts) == 1 and "记忆对抗" in mp.conflicts[0]
    # 串联：单场景 next 为 None（留给 choice）
    assert node["next"] is None
    assert node["source"] == {"arc_id": "arc_golden", "scene_index": 0, "chapter": 1}


def test_environment_dedup_and_assets():
    """§6：相同 environment 复用同一 background 资产；资产清单只出 prompt。"""
    snap = _snap_from_fixture()
    snap.scenes = [dict(snap.scenes[0]), dict(snap.scenes[0])]  # 同背景两个场景
    mp = map_snapshot(snap)
    bgs = [a for a in mp.assets["assets"] if a["kind"] == "background"]
    assert len(bgs) == 1
    assert bgs[0]["status"] == "pending" and bgs[0]["hash"] is None
    assert bgs[0]["prompt"]  # 只出 prompt，不生成
    n1, n2 = mp.story["nodes"]
    assert n1["background"] == n2["background"] == bgs[0]["asset_id"]
    # inner → voice 资产（ref 主角），voice_* 编号
    voices = [a for a in mp.assets["assets"] if a["kind"] == "voice"]
    assert voices and voices[0]["asset_id"].startswith("voice_inner_")
    assert voices[0]["ref"] == "char_001"
    # portrait 资产
    portraits = [a for a in mp.assets["assets"] if a["kind"] == "portrait"]
    assert [p["asset_id"] for p in portraits] == ["spr_char_001_default"]


def test_world_projection_keeps_source_ids():
    """elements → world.json 原样投影，保留 source id。"""
    mp = map_snapshot(_snap_from_fixture())
    assert mp.world["locations"][0]["id"] == "loc_001"
    assert mp.world["locations"][0]["source_element"] == "loc_001"
    assert mp.world["items"][0]["id"] == "item_001"
    assert mp.world["settings"][0]["id"] == "set_001"
    assert mp.world["terms"] == []  # maps 为空


def test_characters_selection_and_presence():
    """selected 优先主角；未出场且未选中不收录（char_002 未提及）。"""
    mp = map_snapshot(_snap_from_fixture())
    chars = mp.characters["characters"]
    assert [c["id"] for c in chars] == ["char_001"]
    assert chars[0]["role"] == "protagonist"
    assert chars[0]["source_element"] == "char_001"
    assert chars[0]["aliases"] == ["守念"]
    assert chars[0]["presence"] == ["n001"]
    assert chars[0]["voice"]["kokoro"] == "zm_x"  # 中性音色占位
    # interaction 预留：details 首条 → inspect 实体
    assert mp.interaction["entities"][0]["kind"] == "inspect"
    assert mp.interaction["entities"][0]["node"] == "n001"
    assert mp.story["nodes"][0]["interaction_refs"] == ["int_n001_1"]


def test_compute_pack_id_format_and_stability():
    snap = _snap_from_fixture()
    pid = compute_pack_id(snap)
    assert pid.startswith("pack_testbook_arc_golden_")
    suffix = pid[len("pack_testbook_arc_golden_"):]
    assert len(suffix) == 6 and all(c in "0123456789abcdef" for c in suffix)
    assert pid == compute_pack_id(snap)  # 稳定
    # 输入变化 → hash 变化
    snap2 = _snap_from_fixture()
    snap2.scenes[0]["details"] = ["改动后的细节"]
    assert pid != compute_pack_id(snap2)


def test_no_characters_fallback_protagonist():
    """elements 无角色表且无 selected → mapper 占位主角。"""
    snap = _snap_from_fixture()
    snap.elements = {}
    snap.selected = {}
    mp = map_snapshot(snap)
    chars = mp.characters["characters"]
    assert chars[0]["id"] == "char_protagonist" and chars[0]["role"] == "protagonist"
    assert mp.story["nodes"][0]["present"] == ["char_protagonist"]
