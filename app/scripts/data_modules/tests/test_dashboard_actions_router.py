"""Tier-A 单元测试：dashboard router 挂载烟测 + 路径校验。

不真起子进程；只验证 FastAPI app 能装入新路由，GET 状态机正常，
POST 入参校验生效。子进程层的真实运行交由 A1 端到端验证。
"""
from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


def _make_client(monkeypatch, project_root: Path) -> TestClient:
    plugin_root = Path(__file__).resolve().parents[3]
    scripts_dir = plugin_root / "scripts"
    clean = [p for p in sys.path if Path(p).name != scripts_dir.name]
    if str(plugin_root) not in clean:
        clean.insert(0, str(plugin_root))
    monkeypatch.setattr(sys, "path", clean)
    for n in list(sys.modules):
        if n.startswith("dashboard.") or n == "dashboard":
            sys.modules.pop(n, None)
    module = importlib.import_module("dashboard.app")
    app = module.create_app(project_root)
    return TestClient(app)


def _seed_minimal_state(root: Path) -> None:
    (root / ".ainovel").mkdir(parents=True, exist_ok=True)
    (root / ".ainovel" / "state.json").write_text("{}", encoding="utf-8")


# ===== workflow_status router 通过 HTTP =====

def test_chapter_status_endpoint_returns_blocked_plan(monkeypatch, tmp_path):
    _seed_minimal_state(tmp_path)
    client = _make_client(monkeypatch, tmp_path)
    resp = client.get("/api/workflow/chapter/1/status")
    assert resp.status_code == 200
    body = resp.json()
    assert body["chapter"] == 1
    plan = next(s for s in body["steps"] if s["id"] == "plan")
    assert plan["status"] == "blocked"


def test_chapters_list_endpoint(monkeypatch, tmp_path):
    _seed_minimal_state(tmp_path)
    folder = tmp_path / ".story-system" / "chapters"
    folder.mkdir(parents=True)
    (folder / "chapter_001.json").write_text(
        json.dumps({"chapter_directive": {"goal": "x"}}),
        encoding="utf-8",
    )
    client = _make_client(monkeypatch, tmp_path)
    resp = client.get("/api/workflow/chapters")
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["chapters"][0]["chapter"] == 1
    assert payload["chapters"][0]["has_directive"] is True


# ===== tasks router =====

def test_tasks_list_initially_empty(monkeypatch, tmp_path):
    _seed_minimal_state(tmp_path)
    client = _make_client(monkeypatch, tmp_path)
    resp = client.get("/api/tasks")
    assert resp.status_code == 200
    # 上一个测试可能留下 task，这里只检查 schema
    body = resp.json()
    assert "tasks" in body
    assert isinstance(body["tasks"], list)


def test_task_404_when_unknown(monkeypatch, tmp_path):
    _seed_minimal_state(tmp_path)
    client = _make_client(monkeypatch, tmp_path)
    resp = client.get("/api/tasks/nonexistent")
    assert resp.status_code == 404


def test_task_resume_404(monkeypatch, tmp_path):
    _seed_minimal_state(tmp_path)
    client = _make_client(monkeypatch, tmp_path)
    resp = client.post("/api/tasks/nonexistent/resume", json={"answer": {}})
    assert resp.status_code == 409


# ===== actions router：入参校验 =====

def test_lint_requires_chapter_or_content_file(monkeypatch, tmp_path):
    _seed_minimal_state(tmp_path)
    client = _make_client(monkeypatch, tmp_path)
    resp = client.post("/api/actions/lint", json={})
    assert resp.status_code == 400


def test_quant_requires_chapter_or_content_file(monkeypatch, tmp_path):
    _seed_minimal_state(tmp_path)
    client = _make_client(monkeypatch, tmp_path)
    resp = client.post("/api/actions/quant-audit", json={})
    assert resp.status_code == 400


def test_revision_diff_requires_paths(monkeypatch, tmp_path):
    _seed_minimal_state(tmp_path)
    client = _make_client(monkeypatch, tmp_path)
    # 缺 ai_path / final_path → pydantic 422
    resp = client.post("/api/actions/revision-diff", json={})
    assert resp.status_code == 422


def test_master_outline_sync_requires_volume(monkeypatch, tmp_path):
    _seed_minimal_state(tmp_path)
    client = _make_client(monkeypatch, tmp_path)
    resp = client.post("/api/actions/master-outline-sync", json={})
    assert resp.status_code == 422


def test_chapter_commit_requires_chapter(monkeypatch, tmp_path):
    _seed_minimal_state(tmp_path)
    client = _make_client(monkeypatch, tmp_path)
    resp = client.post("/api/actions/chapter-commit", json={})
    assert resp.status_code == 422


def test_review_pipeline_requires_chapter_and_review_results(monkeypatch, tmp_path):
    _seed_minimal_state(tmp_path)
    client = _make_client(monkeypatch, tmp_path)
    resp = client.post("/api/actions/review-pipeline", json={"chapter": 1})
    assert resp.status_code == 422


def test_reference_search_requires_query(monkeypatch, tmp_path):
    _seed_minimal_state(tmp_path)
    client = _make_client(monkeypatch, tmp_path)
    resp = client.post("/api/actions/reference-search", json={})
    assert resp.status_code == 422


def test_style_extract_requires_chapter(monkeypatch, tmp_path):
    _seed_minimal_state(tmp_path)
    client = _make_client(monkeypatch, tmp_path)
    resp = client.post("/api/actions/style-extract", json={})
    assert resp.status_code == 422


# ===== POST 成功路径：只检查返回 task_id（不等子进程完成）=====

def test_preflight_returns_task_id(monkeypatch, tmp_path):
    """preflight 是无参 POST，应当立刻返回 task_id（子进程在后台跑）。"""
    _seed_minimal_state(tmp_path)
    client = _make_client(monkeypatch, tmp_path)
    resp = client.post("/api/actions/preflight", json={})
    assert resp.status_code == 200
    body = resp.json()
    assert "task_id" in body
    assert body["label"] == "preflight"


def test_discussion_load_returns_task_id(monkeypatch, tmp_path):
    _seed_minimal_state(tmp_path)
    client = _make_client(monkeypatch, tmp_path)
    resp = client.post("/api/actions/discussion-load", json={"max_age_hours": 1.0})
    assert resp.status_code == 200
    assert "task_id" in resp.json()


def test_placeholder_scan_returns_task_id(monkeypatch, tmp_path):
    _seed_minimal_state(tmp_path)
    client = _make_client(monkeypatch, tmp_path)
    resp = client.post("/api/actions/placeholder-scan", json={})
    assert resp.status_code == 200
    assert "task_id" in resp.json()
