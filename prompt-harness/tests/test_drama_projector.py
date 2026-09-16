"""漫剧投影测试：结构断言 + golden 逐字节稳定。"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from prompt_harness.drama.projector import (
    build_srt,
    project_pack,
    project_to_drama,
    write_drama,
)

TESTS_DIR = Path(__file__).resolve().parent
FIXTURE_PACK = TESTS_DIR / "fixtures" / "adaptation_pack_demo"
GOLDEN_DIR = TESTS_DIR / "fixtures" / "drama_golden"


@pytest.fixture(scope="module")
def shots_doc():
    return project_pack(FIXTURE_PACK)


def test_overall_shape(shots_doc):
    assert shots_doc["schema_version"] == "0.1"
    assert shots_doc["pack_id"] == "pack_demo_arc0001_c0ffee"
    assert shots_doc["size"] == {"width": 1080, "height": 1920}
    assert shots_doc["fps"] == 24
    assert shots_doc["warnings"] == []
    assert len(shots_doc["shots"]) == 10  # n001 4 行 + n002 4 行 + 2 个结局卡


def test_lines_split_into_tracks(shots_doc):
    tracks = shots_doc["tracks"]
    assert len(tracks["narration"]) == 5  # 2 narration + 1 inner + 2 end_card
    assert len(tracks["dialogue"]) == 3
    assert len(tracks["stage"]) == 2
    assert set(tracks) == {"narration", "dialogue", "stage"}


def test_shot_type_camera_mapping(shots_doc):
    by_id = {s["shot_id"]: s for s in shots_doc["shots"]}
    # narration → 静帧缓推
    s0 = by_id["n001_s00"]
    assert s0["type"] == "still_push"
    assert s0["camera"] == {"move": "push_in", "from_scale": 1.0, "to_scale": 1.08}
    assert s0["background"] == "bg_classroom_night"
    # inner → 特写，speaker 取 present 首角色
    s1 = by_id["n001_s01"]
    assert s1["type"] == "closeup"
    assert s1["speaker"] == "char_chen" and s1["speaker_name"] == "陈守念"
    # dialogue → 正反打，pan 方向按全局序交替
    d1 = by_id["n001_s03"]
    assert d1["type"] == "shot_reverse_shot" and d1["camera"]["pan"] == "left"
    assert d1["expression"] == "fear"
    d2 = by_id["n002_s01"]
    assert d2["camera"]["pan"] == "right"
    assert d2["speaker_name"] == "林老师"
    d3 = by_id["n002_s02"]
    assert d3["camera"]["pan"] == "left"
    # stage → 演出提示镜头，cue 透传
    st = by_id["n001_s02"]
    assert st["type"] == "stage_cue" and st["stage_cue"] == "paper_figures_turn"
    assert st["track"] == "stage" and st["duration"] == 2.5
    # ending → 收尾卡
    ec = by_id["end_a_card"]
    assert ec["type"] == "end_card" and ec["duration"] == 3.5
    assert "接受真相" in ec["text"]


def test_unknown_line_kind_degrades_with_warning(tmp_path):
    pack = tmp_path / "pack"
    pack.mkdir()
    (pack / "story.json").write_text(
        json.dumps(
            {
                "start": "n1",
                "nodes": [
                    {
                        "id": "n1",
                        "type": "scene",
                        "title": "t",
                        "background": None,
                        "present": [],
                        "lines": [
                            {"kind": "weird", "text": "未知类型"},
                            {"kind": "narration", "text": ""},
                        ],
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    doc = project_pack(pack)
    assert doc["warnings"], "空文本/未知类型应产生警告"
    assert len(doc["shots"]) == 1  # 空文本跳过；unknown 按 narration 投影
    assert doc["shots"][0]["line_kind"] == "narration"


def test_missing_story_json(tmp_path):
    with pytest.raises(FileNotFoundError):
        project_pack(tmp_path)


def test_golden_shots_json_byte_stable(shots_doc):
    """golden：输出必须逐字节稳定（确定性投影）。"""
    golden = (GOLDEN_DIR / "shots.json").read_text(encoding="utf-8")
    produced = json.dumps(shots_doc, ensure_ascii=False, indent=2) + "\n"
    assert produced == golden


def test_golden_srt_byte_stable(shots_doc):
    golden = (GOLDEN_DIR / "subtitles.srt").read_text(encoding="utf-8")
    assert build_srt(shots_doc) == golden


def test_write_drama_outputs(tmp_path, shots_doc):
    paths = write_drama(tmp_path, shots_doc)
    shots_path = Path(paths["shots_path"])
    srt_path = Path(paths["srt_path"])
    assert shots_path == tmp_path / "drama" / "shots.json"
    assert srt_path == tmp_path / "drama" / "subtitles.srt"
    assert shots_path.read_text(encoding="utf-8") == (
        GOLDEN_DIR / "shots.json"
    ).read_text(encoding="utf-8")
    assert srt_path.read_text(encoding="utf-8") == (
        GOLDEN_DIR / "subtitles.srt"
    ).read_text(encoding="utf-8")


def test_project_to_drama_end_to_end(shots_doc):
    # 不重复落盘（fixture 目录只读），仅验证组合函数返回结构
    doc = project_pack(FIXTURE_PACK)
    assert doc == shots_doc
    assert callable(project_to_drama)
