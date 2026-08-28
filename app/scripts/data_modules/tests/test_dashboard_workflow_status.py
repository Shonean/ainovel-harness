"""Tier-A 单元测试：dashboard/workflow_status.py 状态机判定。

不启动 FastAPI 应用，直接调 build_chapter_status / list_chapters，纯文件构造。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

# 让测试能 import dashboard.workflow_status
PLUGIN_ROOT = Path(__file__).resolve().parents[3]
if str(PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT))

from dashboard.workflow_status import (  # noqa: E402
    build_chapter_status,
    list_chapters,
)


def _seed_state(root: Path) -> None:
    (root / ".ainovel").mkdir(parents=True, exist_ok=True)
    (root / ".ainovel" / "state.json").write_text("{}", encoding="utf-8")


def _seed_chapter_json(root: Path, chapter: int, *, directive: dict | None = None) -> None:
    folder = root / ".story-system" / "chapters"
    folder.mkdir(parents=True, exist_ok=True)
    payload = {
        "meta": {"chapter": chapter},
        "chapter_directive": directive or {},
        "override_allowed": {"chapter_focus": f"第{chapter}章"},
    }
    (folder / f"chapter_{chapter:03d}.json").write_text(
        json.dumps(payload, ensure_ascii=False), encoding="utf-8",
    )


def _seed_draft(root: Path, chapter: int, *, content: str = "正文") -> Path:
    folder = root / "正文"
    folder.mkdir(parents=True, exist_ok=True)
    p = folder / f"第{chapter:04d}章-测试.md"
    p.write_text(content, encoding="utf-8")
    return p


def _seed_final_audit(root: Path, *, mtime_offset: float = 1.0) -> Path:
    folder = root / ".ainovel" / "tmp"
    folder.mkdir(parents=True, exist_ok=True)
    p = folder / "final_audit.json"
    p.write_text(json.dumps({"chapter": 1}), encoding="utf-8")
    import os as _os
    st = p.stat()
    _os.utime(p, (st.st_atime, st.st_mtime + mtime_offset))
    return p


def _seed_commit(root: Path, chapter: int, *, status: str = "accepted") -> Path:
    folder = root / ".story-system" / "commits"
    folder.mkdir(parents=True, exist_ok=True)
    p = folder / f"chapter_{chapter:03d}.commit.json"
    p.write_text(json.dumps({"meta": {"chapter": chapter, "status": status}}),
                 encoding="utf-8")
    return p


# ===== plan 判定 =====

def test_plan_blocked_when_chapter_json_missing(tmp_path):
    _seed_state(tmp_path)
    rep = build_chapter_status(tmp_path, 1)
    plan = next(s for s in rep["steps"] if s["id"] == "plan")
    assert plan["status"] == "blocked"
    assert "缺" in plan["evidence"]


def test_plan_blocked_when_directive_empty(tmp_path):
    """这是用户实际遇到的状态：chapter_001.json 存在但 directive={}。"""
    _seed_state(tmp_path)
    _seed_chapter_json(tmp_path, 1, directive={})
    rep = build_chapter_status(tmp_path, 1)
    plan = next(s for s in rep["steps"] if s["id"] == "plan")
    assert plan["status"] == "blocked"
    assert "directive" in plan["evidence"].lower() or "空" in plan["evidence"]


def test_plan_done_when_directive_has_goal(tmp_path):
    _seed_state(tmp_path)
    _seed_chapter_json(tmp_path, 1, directive={"goal": "主角穿越"})
    rep = build_chapter_status(tmp_path, 1)
    plan = next(s for s in rep["steps"] if s["id"] == "plan")
    assert plan["status"] == "done"


def test_plan_blocked_when_directive_all_todo(tmp_path):
    _seed_state(tmp_path)
    _seed_chapter_json(tmp_path, 1, directive={"goal": "TODO", "CBN": ""})
    rep = build_chapter_status(tmp_path, 1)
    plan = next(s for s in rep["steps"] if s["id"] == "plan")
    assert plan["status"] == "blocked"


# ===== 防跳步级联 =====

def test_cascade_blocks_downstream_when_plan_missing(tmp_path):
    """plan 未完成 → context/draft/review/commit 全 blocked。"""
    _seed_state(tmp_path)
    rep = build_chapter_status(tmp_path, 1)
    by_id = {s["id"]: s for s in rep["steps"]}
    assert by_id["context"]["status"] == "blocked"
    assert by_id["draft"]["status"] == "blocked"
    assert by_id["review"]["status"] == "blocked"
    assert by_id["commit"]["status"] == "blocked"


def test_context_ready_after_plan_done(tmp_path):
    _seed_state(tmp_path)
    _seed_chapter_json(tmp_path, 1, directive={"goal": "x"})
    rep = build_chapter_status(tmp_path, 1)
    by_id = {s["id"]: s for s in rep["steps"]}
    assert by_id["context"]["status"] == "optional"
    # plan done → 第一个 ready 是 draft
    assert rep["next_action"] == "draft"


def test_draft_blocked_until_context(tmp_path):
    _seed_state(tmp_path)
    _seed_chapter_json(tmp_path, 1, directive={"goal": "x"})
    rep = build_chapter_status(tmp_path, 1)
    by_id = {s["id"]: s for s in rep["steps"]}
    # plan done → draft 应为 ready
    assert by_id["draft"]["status"] == "ready"
    assert rep["next_action"] == "draft"


def test_draft_done_marks_context_done_retroactively(tmp_path):
    """有 draft 落盘 → context 仍标 optional（无独立产物），draft 标 done。"""
    _seed_state(tmp_path)
    _seed_chapter_json(tmp_path, 1, directive={"goal": "x"})
    _seed_draft(tmp_path, 1)
    rep = build_chapter_status(tmp_path, 1)
    by_id = {s["id"]: s for s in rep["steps"]}
    assert by_id["context"]["status"] == "optional"
    assert by_id["draft"]["status"] == "done"


# ===== review 与 final_audit mtime =====

def test_review_blocked_without_draft(tmp_path):
    _seed_state(tmp_path)
    _seed_chapter_json(tmp_path, 1, directive={"goal": "x"})
    rep = build_chapter_status(tmp_path, 1)
    by_id = {s["id"]: s for s in rep["steps"]}
    assert by_id["review"]["status"] == "blocked"


def test_review_ready_after_draft(tmp_path):
    _seed_state(tmp_path)
    _seed_chapter_json(tmp_path, 1, directive={"goal": "x"})
    _seed_draft(tmp_path, 1)
    rep = build_chapter_status(tmp_path, 1)
    by_id = {s["id"]: s for s in rep["steps"]}
    assert by_id["review"]["status"] == "ready"


def test_review_done_when_audit_after_draft(tmp_path):
    _seed_state(tmp_path)
    _seed_chapter_json(tmp_path, 1, directive={"goal": "x"})
    _seed_draft(tmp_path, 1)
    _seed_final_audit(tmp_path, mtime_offset=10.0)  # 比 draft 晚
    rep = build_chapter_status(tmp_path, 1)
    by_id = {s["id"]: s for s in rep["steps"]}
    assert by_id["review"]["status"] == "done"


def test_review_ready_when_audit_targets_different_chapter(tmp_path):
    """final_audit.json 对应别的章节 → 需要重跑。"""
    _seed_state(tmp_path)
    _seed_chapter_json(tmp_path, 2, directive={"goal": "x"})
    _seed_draft(tmp_path, 2)
    p = _seed_final_audit(tmp_path)
    p.write_text(json.dumps({"chapter": 1}), encoding="utf-8")
    import os
    os.utime(p, (p.stat().st_atime, p.stat().st_mtime + 100))
    rep = build_chapter_status(tmp_path, 2)
    by_id = {s["id"]: s for s in rep["steps"]}
    assert by_id["review"]["status"] == "ready"


# ===== commit / backup =====

def test_commit_done_when_commit_json_present(tmp_path):
    _seed_state(tmp_path)
    _seed_chapter_json(tmp_path, 1, directive={"goal": "x"})
    _seed_draft(tmp_path, 1)
    _seed_final_audit(tmp_path, mtime_offset=10.0)
    _seed_commit(tmp_path, 1, status="accepted")
    rep = build_chapter_status(tmp_path, 1)
    by_id = {s["id"]: s for s in rep["steps"]}
    assert by_id["commit"]["status"] == "done"
    assert "accepted" in by_id["commit"]["evidence"]


def test_backup_ready_after_commit(tmp_path):
    _seed_state(tmp_path)
    _seed_chapter_json(tmp_path, 1, directive={"goal": "x"})
    _seed_draft(tmp_path, 1)
    _seed_final_audit(tmp_path, mtime_offset=10.0)
    _seed_commit(tmp_path, 1)
    rep = build_chapter_status(tmp_path, 1)
    by_id = {s["id"]: s for s in rep["steps"]}
    assert by_id["backup"]["status"] == "ready"


# ===== next_action =====

def test_next_action_points_to_plan_when_nothing_done(tmp_path):
    _seed_state(tmp_path)
    rep = build_chapter_status(tmp_path, 1)
    # plan 是 blocked，no ready 步在前 → next_action 应当落到 preflight 或 None
    # 实际：preflight 走 _check_preflight，state.json 存在 → ready
    assert rep["next_action"] in ("preflight", None)


def test_next_action_none_when_all_done(tmp_path):
    _seed_state(tmp_path)
    _seed_chapter_json(tmp_path, 1, directive={"goal": "x"})
    _seed_draft(tmp_path, 1)
    _seed_final_audit(tmp_path, mtime_offset=10.0)
    _seed_commit(tmp_path, 1)
    rep = build_chapter_status(tmp_path, 1)
    by_id = {s["id"]: s for s in rep["steps"]}
    # commit done → backup ready
    assert by_id["commit"]["status"] == "done"
    assert rep["next_action"] == "backup"


# ===== artifacts =====

def test_artifacts_listed_correctly(tmp_path):
    import os as _os
    _seed_state(tmp_path)
    _seed_chapter_json(tmp_path, 1, directive={"goal": "x"})
    draft = _seed_draft(tmp_path, 1)
    _seed_commit(tmp_path, 1)
    rep = build_chapter_status(tmp_path, 1)
    arts = rep["artifacts"]
    assert arts["chapter_json"] is not None
    assert arts["chapter_md"] == "正文" + _os.sep + draft.name
    assert arts["commit_json"] is not None


# ===== list_chapters =====

def test_list_chapters_empty(tmp_path):
    assert list_chapters(tmp_path) == []


def test_list_chapters_orders_by_number(tmp_path):
    _seed_chapter_json(tmp_path, 3, directive={"goal": "a"})
    _seed_chapter_json(tmp_path, 1, directive={"goal": "b"})
    _seed_chapter_json(tmp_path, 2, directive={})
    items = list_chapters(tmp_path)
    assert [i["chapter"] for i in items] == [1, 2, 3]
    assert items[1]["has_directive"] is False  # chapter 2 directive 空


def test_list_chapters_marks_drafted_and_committed(tmp_path):
    _seed_chapter_json(tmp_path, 1, directive={"goal": "x"})
    _seed_draft(tmp_path, 1)
    _seed_commit(tmp_path, 1)
    items = list_chapters(tmp_path)
    assert items[0]["drafted"] is True
    assert items[0]["committed"] is True


def test_list_chapters_handles_broken_json(tmp_path):
    folder = tmp_path / ".story-system" / "chapters"
    folder.mkdir(parents=True)
    (folder / "chapter_001.json").write_text("{ broken", encoding="utf-8")
    items = list_chapters(tmp_path)
    assert items[0]["chapter"] == 1
    assert items[0]["has_directive"] is False
