"""单元测试：summary_projection_writer —— 三条分支全覆盖。"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from data_modules.projections import (  # noqa: E402
    append_summary_projection,
    SummaryProjectionWriter,
)


def _payload(chapter=1, summary_text="本章主角进入异世界。", status="accepted"):
    return {
        "meta": {"chapter": chapter, "status": status},
        "summary_text": summary_text,
    }


def test_append_writes_file_with_header_inserted(tmp_path):
    payload = _payload(chapter=3, summary_text="主角穿越，遇到鬼卿。")
    res = append_summary_projection(tmp_path, payload)
    assert res["applied"] is True
    target = tmp_path / ".ainovel" / "summaries" / "ch0003.md"
    assert target.is_file()
    content = target.read_text(encoding="utf-8")
    assert content.startswith("## 剧情摘要")
    assert "鬼卿" in content


def test_append_preserves_existing_header(tmp_path):
    # 已含 "## 剧情摘要" 标题时不重复加
    payload = _payload(
        chapter=2,
        summary_text="## 剧情摘要\n这是已带标题的摘要。",
    )
    res = append_summary_projection(tmp_path, payload)
    assert res["applied"] is True
    content = (tmp_path / ".ainovel" / "summaries" / "ch0002.md").read_text(encoding="utf-8")
    # 只有一处 "## 剧情摘要"
    assert content.count("## 剧情摘要") == 1


def test_append_skipped_when_chapter_missing(tmp_path):
    payload = {"meta": {"chapter": 0}, "summary_text": "abc"}
    res = append_summary_projection(tmp_path, payload)
    assert res["applied"] is False
    assert res["reason"] == "missing_summary"


def test_append_skipped_when_summary_empty(tmp_path):
    payload = _payload(chapter=1, summary_text="")
    res = append_summary_projection(tmp_path, payload)
    assert res["applied"] is False
    assert res["reason"] == "missing_summary"


def test_append_skipped_when_summary_whitespace_only(tmp_path):
    payload = _payload(chapter=1, summary_text="   \n\n   ")
    res = append_summary_projection(tmp_path, payload)
    assert res["applied"] is False


def test_writer_class_skips_rejected_commits(tmp_path):
    writer = SummaryProjectionWriter(tmp_path)
    payload = _payload(status="rejected")
    res = writer.apply(payload)
    assert res["applied"] is False
    assert res["reason"] == "commit_rejected"


def test_writer_class_writes_for_accepted_commits(tmp_path):
    writer = SummaryProjectionWriter(tmp_path)
    res = writer.apply(_payload(chapter=4, summary_text="本章高潮。", status="accepted"))
    assert res["applied"] is True
    assert (tmp_path / ".ainovel" / "summaries" / "ch0004.md").is_file()


def test_writer_creates_summaries_dir_when_missing(tmp_path):
    # 目录从无到有
    target_dir = tmp_path / ".ainovel" / "summaries"
    assert not target_dir.exists()
    append_summary_projection(tmp_path, _payload(chapter=7))
    assert target_dir.is_dir()


def test_writer_handles_missing_chapter_key(tmp_path):
    payload = {"meta": {}, "summary_text": "abc"}
    res = append_summary_projection(tmp_path, payload)
    assert res["applied"] is False
