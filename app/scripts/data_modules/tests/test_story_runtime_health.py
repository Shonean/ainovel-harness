#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import json

from data_modules.story_runtime_health import build_story_runtime_health


def test_story_runtime_health_reports_missing_commit_as_not_ready(tmp_path):
    report = build_story_runtime_health(tmp_path, chapter=3)

    assert report["mainline_ready"] is False
    assert "missing_accepted_commit" in report["fallback_sources"]


def test_story_runtime_health_prefers_latest_story_system_chapter_over_state_projection(tmp_path):
    ainovel_dir = tmp_path / ".ainovel"
    ainovel_dir.mkdir(parents=True, exist_ok=True)
    (ainovel_dir / "state.json").write_text(
        json.dumps({"progress": {"current_chapter": 2}}, ensure_ascii=False),
        encoding="utf-8",
    )

    story_root = tmp_path / ".story-system"
    (story_root / "chapters").mkdir(parents=True, exist_ok=True)
    (story_root / "reviews").mkdir(parents=True, exist_ok=True)
    (story_root / "commits").mkdir(parents=True, exist_ok=True)
    (story_root / "MASTER_SETTING.json").write_text(
        json.dumps({"meta": {"contract_type": "MASTER_SETTING"}}, ensure_ascii=False),
        encoding="utf-8",
    )
    (story_root / "chapters" / "chapter_003.json").write_text(
        json.dumps({"meta": {"contract_type": "CHAPTER_BRIEF", "chapter": 3}}, ensure_ascii=False),
        encoding="utf-8",
    )
    (story_root / "reviews" / "chapter_003.review.json").write_text(
        json.dumps({"meta": {"contract_type": "REVIEW_CONTRACT", "chapter": 3}}, ensure_ascii=False),
        encoding="utf-8",
    )
    (story_root / "commits" / "chapter_002.commit.json").write_text(
        json.dumps({"meta": {"chapter": 2, "status": "accepted"}}, ensure_ascii=False),
        encoding="utf-8",
    )
    (story_root / "commits" / "chapter_003.commit.json").write_text(
        json.dumps({"meta": {"chapter": 3, "status": "rejected"}}, ensure_ascii=False),
        encoding="utf-8",
    )

    report = build_story_runtime_health(tmp_path)

    assert report["chapter"] == 3
    assert report["latest_commit_status"] == "rejected"


# ---- _resolve_chapter / _extract_chapter_from_name 边界 ----

from pathlib import Path  # noqa: E402

from data_modules.story_runtime_health import (  # noqa: E402
    _extract_chapter_from_name,
    _resolve_chapter,
)


def test_extract_chapter_from_name_no_match_returns_zero():
    assert _extract_chapter_from_name(Path("foo.json")) == 0


def test_resolve_chapter_invalid_explicit_returns_zero(tmp_path):
    assert _resolve_chapter(tmp_path, "abc") == 0


def test_resolve_chapter_none_without_state_returns_zero(tmp_path):
    assert _resolve_chapter(tmp_path, None) == 0


def test_resolve_chapter_none_bad_state_json_returns_latest(tmp_path):
    (tmp_path / ".ainovel").mkdir(parents=True, exist_ok=True)
    (tmp_path / ".ainovel" / "state.json").write_text("not json", encoding="utf-8")
    assert _resolve_chapter(tmp_path, None) == 0


def test_resolve_chapter_none_bad_current_chapter_type_returns_latest(tmp_path):
    (tmp_path / ".ainovel").mkdir(parents=True, exist_ok=True)
    (tmp_path / ".ainovel" / "state.json").write_text(
        '{"progress":{"current_chapter":"abc"}}', encoding="utf-8"
    )
    assert _resolve_chapter(tmp_path, None) == 0


def test_resolve_chapter_none_reads_state_current_chapter(tmp_path):
    (tmp_path / ".ainovel").mkdir(parents=True, exist_ok=True)
    (tmp_path / ".ainovel" / "state.json").write_text(
        '{"progress":{"current_chapter":12}}', encoding="utf-8"
    )
    assert _resolve_chapter(tmp_path, None) == 12
