"""漫剧线收口（T36）— drama 域端点/引擎集成测试。

覆盖：workbench 汇总 / 关键镜头降级 / 发布清单·审核·规格·发布包 /
成片导出 / 合辑编排 / 批量队列 / 媒体白名单路由 / PyAV 拼接。

运行：py -m pytest prompt-harness/tests/test_drama_api.py -q
"""
from __future__ import annotations

import json
import shutil
import sys
import time
from pathlib import Path

import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient

_PH = Path(__file__).resolve().parent.parent
if str(_PH) not in sys.path:
    sys.path.insert(0, str(_PH))

from prompt_harness.drama import films as drama_films  # noqa: E402

GOLDEN = Path(__file__).resolve().parent / "fixtures" / "drama_golden"
PACK_ID = "pack_testbook_arc_test001_abc123"


def _make_book(tmp_path: Path) -> Path:
    book = tmp_path / "TestBook"
    d = book / ".ainovel" / "adaptation" / PACK_ID
    (d / "drama").mkdir(parents=True)
    shutil.copy(GOLDEN / "shots.json", d / "drama" / "shots.json")
    shutil.copy(GOLDEN / "subtitles.srt", d / "drama" / "subtitles.srt")
    (d / "pack.json").write_text(json.dumps({
        "schema_version": "0.1", "pack_id": PACK_ID,
        "game": {"title": "纸人教室", "logline": "失忆者在纸人教室醒来。",
                 "endings": ["end_a", "end_b"], "genre_tags": ["悬疑恐怖"]},
        "source": {"arc_ids": ["arc_a"], "chapter_nums": [1]},
    }, ensure_ascii=False), encoding="utf-8")
    (d / "validation.json").write_text(json.dumps({
        "ok": True, "errors": [], "warnings": [],
        "stats": {"nodes": 4, "llm_calls": 2, "cost_est": 0.0002},
    }, ensure_ascii=False), encoding="utf-8")
    (d / "assets.json").write_text(json.dumps({"assets": [
        {"asset_id": "bg_1", "kind": "background", "prompt": "深夜的旧教室"},
        {"asset_id": "voice_1", "kind": "voice", "prompt": "内心独白：他试着回忆。"},
    ]}, ensure_ascii=False), encoding="utf-8")
    (book / ".ainovel" / "arcs.json").write_text(json.dumps({"arcs": [
        {"id": "arc_a", "name": "情节A", "state": {"levels": {"l4": {"scenes": [{}, {}]}}}},
        {"id": "arc_b", "name": "情节B", "state": {"levels": {"l4": {"scenes": []}}}},
    ]}, ensure_ascii=False), encoding="utf-8")
    return book


@pytest.fixture()
def client(tmp_path, monkeypatch):
    """隔离资产库/账本（monkeypatch 模块级路径 + 重置全局 console）。"""
    from prompt_harness import ark_console as ac_mod
    from prompt_harness import server as server_mod
    from prompt_harness.media import asset_store as as_mod
    from prompt_harness.media import usage_ledger as ul_mod

    root = tmp_path / "asset_library"

    def _alr():
        root.mkdir(parents=True, exist_ok=True)
        return root

    monkeypatch.setattr(as_mod, "default_asset_library_root", _alr)
    monkeypatch.setattr(ac_mod, "default_asset_library_root", _alr)
    monkeypatch.setattr(ac_mod, "budget_path", lambda: root / "budget.json")
    monkeypatch.setattr(ac_mod, "media_ledger", lambda: ul_mod.UsageLedger(root / "ledger"))
    monkeypatch.setattr(ac_mod, "_CONSOLE", ac_mod.ArkConsole(root=root / "ark", ledger=ul_mod.UsageLedger(root / "ledger")))

    app = FastAPI()
    app.include_router(server_mod.router)
    with TestClient(app) as c:
        yield c


def _post(client, path, body):
    r = client.post(f"/api/prompt-harness{path}", json=body)
    assert r.status_code == 200, r.text
    return r.json()


def _wait_task(client, task_id, timeout=5.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        data = _post(client, "/ai-creation/ark", {"action": "list", "payload": {}})
        for t in data.get("tasks", []):
            if t.get("id") == task_id and t.get("status") not in ("queued", "running"):
                return t
        time.sleep(0.05)
    raise AssertionError(f"任务 {task_id} 未在 {timeout}s 内到终态")


# ---------------------------------------------------------------- workbench


def test_workbench_returns_real_summary(client, tmp_path):
    book = _make_book(tmp_path)
    data = _post(client, "/ai-creation/drama/workbench", {"book_root": str(book), "pack_id": PACK_ID})
    assert data["ok"] is True
    summary = data["shots"]
    assert summary["shots"] >= 5
    assert summary["duration"] > 0
    assert summary["tracks"]["narration"] >= 1 and summary["tracks"]["dialogue"] >= 1
    assert len(summary["timeline"]) >= 2
    assert len(data["srt"]) >= 3
    assert len(data["assets"]) == 2
    assert len(data["voice"]) == 1
    assert len(data["keyshots"]) >= 1
    assert data["budget"]["image_month_cny"] > 0
    assert data["pack"]["game"]["title"] == "纸人教室"


def test_workbench_missing_pack_404(client, tmp_path):
    book = _make_book(tmp_path)
    r = client.post("/api/prompt-harness/ai-creation/drama/workbench",
                    json={"book_root": str(book), "pack_id": "pack_ghost"})
    assert r.status_code == 404


# ---------------------------------------------------------------- keyshot


def test_keyshot_degraded_is_honest(client, tmp_path, monkeypatch):
    from prompt_harness.media.drivers.seedance import SeedanceDriver

    monkeypatch.setattr(SeedanceDriver, "available", lambda self: (False, "测试降级：无凭据"))
    book = _make_book(tmp_path)
    data = _post(client, "/ai-creation/drama/keyshot",
                 {"book_root": str(book), "pack_id": PACK_ID, "action": "draft", "index": 0})
    assert data["ok"] is False
    assert data["driver_status"] == "skipped"
    assert "测试降级" in data["reason"]
    assert data["keyshots"][0]["stage"] == 0  # 降级不推进阶段


# ---------------------------------------------------------------- 发布


def test_publish_checklist_audit_specs(client, tmp_path):
    book = _make_book(tmp_path)
    cl = _post(client, "/ai-creation/drama/publish",
               {"book_root": str(book), "pack_id": PACK_ID, "action": "checklist"})
    assert cl["ok"] and cl["total"] == 6 and cl["done"] == 0
    au = _post(client, "/ai-creation/drama/publish",
               {"book_root": str(book), "pack_id": PACK_ID, "action": "audit"})
    assert au["ok"] and isinstance(au["hits"], list)
    sp = _post(client, "/ai-creation/drama/publish",
               {"book_root": str(book), "pack_id": PACK_ID, "action": "specs"})
    assert sp["ok"] and len(sp["specs"]) >= 4
    cl2 = _post(client, "/ai-creation/drama/publish",
                {"book_root": str(book), "pack_id": PACK_ID, "action": "checklist"})
    assert cl2["done"] >= 1  # audit + specs 标记


def test_publish_package_requires_film(client, tmp_path):
    book = _make_book(tmp_path)
    r = _post(client, "/ai-creation/drama/publish",
              {"book_root": str(book), "pack_id": PACK_ID, "action": "package"})
    assert r["ok"] is False and "没有成片" in r["error"]


def test_publish_package_with_dummy_film(client, tmp_path):
    book = _make_book(tmp_path)
    pack = book / ".ainovel" / "adaptation" / PACK_ID
    (pack / "drama" / "drama_preview.mp4").write_bytes(b"fake-mp4-bytes")
    r = _post(client, "/ai-creation/drama/publish",
              {"book_root": str(book), "pack_id": PACK_ID, "action": "package"})
    assert r["ok"] is True and Path(r["zip"]).is_file()
    with __import__("zipfile").ZipFile(r["zip"]) as z:
        names = z.namelist()
    assert "drama_preview.mp4" in names and "subtitles.srt" in names


# ---------------------------------------------------------------- 成片/合辑


def test_films_list_and_export(client, tmp_path):
    book = _make_book(tmp_path)
    pack = book / ".ainovel" / "adaptation" / PACK_ID
    (pack / "drama" / "drama_preview.mp4").write_bytes(b"fake-mp4-bytes")
    films = _post(client, "/ai-creation/drama/films", {"book_root": str(book)})
    assert films["ok"] and len(films["episodes"]) == 1
    ep = films["episodes"][0]
    assert ep["exported"] is False and ep["mp4_bytes"] > 0
    exp = _post(client, "/ai-creation/drama/film/export", {"book_root": str(book), "pack_id": PACK_ID})
    assert exp["ok"] is True and Path(exp["export_path"]).is_file()
    films2 = _post(client, "/ai-creation/drama/films", {"book_root": str(book)})
    assert films2["episodes"][0]["exported"] is True


def test_compilation_add_remove(client, tmp_path):
    book = _make_book(tmp_path)
    r = _post(client, "/ai-creation/drama/compilation",
              {"book_root": str(book), "action": "add", "pack_id": PACK_ID})
    assert r["ok"] and PACK_ID in r["compilation"]["items"]
    r2 = _post(client, "/ai-creation/drama/compilation",
               {"book_root": str(book), "action": "remove", "pack_id": PACK_ID})
    assert r2["ok"] and PACK_ID not in r2["compilation"]["items"]
    r3 = _post(client, "/ai-creation/drama/compilation",
               {"book_root": str(book), "action": "export"})
    assert r3["ok"] is False  # 空合辑


# ---------------------------------------------------------------- 批量


def test_batch_queue_and_run_locked(client, tmp_path):
    book = _make_book(tmp_path)
    data = _post(client, "/ai-creation/drama/batch", {"book_root": str(book), "action": "queue"})
    assert data["ok"] and len(data["items"]) == 2
    by_id = {it["arc_id"]: it for it in data["items"]}
    assert by_id["arc_a"]["status"] == "ready" and by_id["arc_a"]["l4"] == 2
    assert by_id["arc_b"]["status"] == "locked"
    # 把两弧的 l4 都清空后 run 走空队列分支（不触发真实构建）
    (book / ".ainovel" / "arcs.json").write_text(json.dumps({"arcs": [
        {"id": "arc_a", "name": "情节A", "state": {"levels": {"l4": {"scenes": []}}}},
        {"id": "arc_b", "name": "情节B", "state": {"levels": {"l4": {"scenes": []}}}},
    ]}, ensure_ascii=False), encoding="utf-8")
    run = _post(client, "/ai-creation/drama/batch", {"book_root": str(book), "action": "run"})
    assert run["ok"] is False and "没有可运行" in run["error"]


# ---------------------------------------------------------------- 媒体路由


def test_media_route_whitelist(client, tmp_path):
    book = _make_book(tmp_path)
    r = client.get("/api/prompt-harness/ai-creation/drama/media",
                   params={"book_root": str(book), "pack_id": PACK_ID, "name": "drama/shots.json"})
    assert r.status_code == 200
    r2 = client.get("/api/prompt-harness/ai-creation/drama/media",
                    params={"book_root": str(book), "pack_id": PACK_ID, "name": "../../evil"})
    assert r2.status_code == 404
    r3 = client.get("/api/prompt-harness/ai-creation/drama/media",
                    params={"book_root": str(book), "pack_id": "..", "name": "drama/shots.json"})
    assert r3.status_code == 400


# ---------------------------------------------------------------- ark 控制台


def test_ark_degraded_submit_and_fill_refused(client, tmp_path, monkeypatch):
    from prompt_harness.media.drivers.seedream import SeedreamDriver

    monkeypatch.setattr(SeedreamDriver, "available", lambda self: (False, "测试降级：无凭据"))
    book = _make_book(tmp_path)
    sub = _post(client, "/ai-creation/ark", {"action": "submit", "payload": {
        "kind": "image", "prompt": "深夜教室", "label": "测试生图",
        "book_root": str(book), "pack_id": PACK_ID, "entry_asset_id": "bg_1",
    }})
    assert sub["ok"] is True
    task = _wait_task(client, sub["task"]["id"])
    assert task["status"] == "skipped" and "测试降级" in task["reason"]
    assert task["cost_charged"] == 0
    fill = _post(client, "/ai-creation/ark", {"action": "fill", "task_id": task["id"]})
    assert fill["ok"] is False and "未成功" in fill["error"]
    usage = _post(client, "/ai-creation/ark", {"action": "usage"})
    assert usage["ok"] and usage["summary"]["total_calls"] >= 1


def test_ark_succeeded_fill_marks_pack_asset(client, tmp_path, monkeypatch):
    from prompt_harness import ark_console as ac_mod

    console = ac_mod._CONSOLE
    monkeypatch.setattr(console, "_execute", lambda rec: {
        "status": "succeeded", "driver_status": "done", "reason": "mock",
        "outputs": [{"kind": "background", "content_id": "sha256_mock", "path": "x"}],
    })
    book = _make_book(tmp_path)
    sub = _post(client, "/ai-creation/ark", {"action": "submit", "payload": {
        "kind": "image", "prompt": "深夜教室", "book_root": str(book), "pack_id": PACK_ID,
        "entry_asset_id": "bg_1",
    }})
    task = _wait_task(client, sub["task"]["id"])
    assert task["status"] == "succeeded" and task["cost_charged"] > 0
    fill = _post(client, "/ai-creation/ark", {"action": "fill", "task_id": task["id"]})
    assert fill["ok"] is True
    assets = json.loads((book / ".ainovel" / "adaptation" / PACK_ID / "assets.json").read_text(encoding="utf-8"))["assets"]
    bg = next(a for a in assets if a["asset_id"] == "bg_1")
    assert bg["state"] == "done" and bg["content_id"] == "sha256_mock"


def test_ark_models_auth(client):
    models = _post(client, "/ai-creation/ark", {"action": "models"})
    assert models["ok"] and len(models["models"]) >= 5
    assert any(d["name"] == "seedream" for d in models["drivers"])
    auth = _post(client, "/ai-creation/ark", {"action": "auth"})
    assert auth["ok"] is True and "profile_hint" in auth


# ---------------------------------------------------------------- PyAV 拼接


def test_concat_videos(tmp_path):
    av = pytest.importorskip("av")
    from prompt_harness.drama.compose import compose_video

    doc = {
        "schema_version": "0.1", "size": {"width": 96, "height": 96}, "fps": 24,
        "shots": [
            {"shot_id": "s1", "node_id": "n001", "index": 0, "line_kind": "narration",
             "type": "still_push", "track": "narration", "duration": 0.4, "text": "一"},
            {"shot_id": "s2", "node_id": "n002", "index": 0, "line_kind": "narration",
             "type": "still_push", "track": "narration", "duration": 0.4, "text": "二"},
        ],
    }
    a = tmp_path / "a.mp4"
    b = tmp_path / "b.mp4"
    res_a = compose_video(doc, out_path=str(a), width=96, height=96, burn_subtitles=False)
    res_b = compose_video(doc, out_path=str(b), width=96, height=96, burn_subtitles=False)
    assert a.is_file() and b.is_file()
    out = tmp_path / "out.mp4"
    r = drama_films.concat_videos([a, b], out)
    assert r["ok"] is True and out.is_file() and out.stat().st_size > 0
