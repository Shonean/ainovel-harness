"""改编层 v0 — 构建编排（cli.build_pack）与 server 端点集成测试。

- fixture 书 + mock llm_hook → 全流程 build：pack 落盘、validation.ok=true、
  编译产物/预览页齐全、LLM 调用 ≤4；
- 缺 l4 快照 → AdaptationError；
- 编译失败 → validation.ok=false + <pack_id>.failed/ 保留中间产物；
- server 4 端点（build/packs/pack/pack/delete）契约。
运行：pytest prompt-harness/tests/test_adaptation_build.py -q
"""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient

_PH_SRC = Path(__file__).resolve().parent.parent
if str(_PH_SRC) not in sys.path:
    sys.path.insert(0, str(_PH_SRC))

from prompt_harness.adaptation import cli as adaptation_cli  # noqa: E402
from prompt_harness.adaptation.compiler import CompileError, CompileResult  # noqa: E402
from prompt_harness.adaptation.loader import AdaptationError  # noqa: E402

FIXTURE_BOOK = (Path(__file__).resolve().parent / "fixtures" / "adaptation"
                / "fixture_book")


class _CountingHook:
    """mock llm_hook：按 task 分发固定 JSON，并记录调用数（单测不触网）。"""

    def __init__(self, dialogue_speaker=None):
        self.calls: list[str] = []
        self._dialogue = dialogue_speaker or {}

    async def __call__(self, payload: dict):
        task = payload.get("task") or ""
        self.calls.append(task)
        if task == "dialogue_speaker":
            # 场景 1：第二条对白；场景 2：引号前置对白 → 都归主角
            return {"speakers": {"0": "陈守念"}}
        if task == "design_choice":
            return {"prompt": "教室安静得反常，你要先做什么？",
                    "options": [{"text": "细看值日表的残角", "flag": "read_chart"},
                                {"text": "去翻后排储物柜", "flag": "search_cabinet"}]}
        if task == "design_endings":
            return {"endings": [
                {"title": "接受名字",
                 "text": "他念出纸巾上的名字，扳指温了下来。",
                 "flag": "read_chart"},
                {"title": "转身离开",
                 "text": "他没有回头，走廊尽头的血字在黑暗里等着。",
                 "flag": ""},
            ]}
        return None


@pytest.fixture()
def book(tmp_path) -> Path:
    dst = tmp_path / "TestBook"
    shutil.copytree(FIXTURE_BOOK, dst)
    return dst


def test_build_pack_full_pipeline(book, tmp_path):
    hook = _CountingHook()
    result = asyncio_run_build(book, "arc_test001", hook)
    assert result["ok"], json.dumps(result["validation"], ensure_ascii=False)[:800]
    assert result["failed"] is False
    pack_dir = Path(result["pack_dir"])
    assert pack_dir.parent == book / ".ainovel" / "adaptation"
    assert pack_dir.name == result["pack_id"]
    # 目录结构（spec §5.1）
    for rel in ("pack.json", "story.json", "characters.json", "world.json",
                "assets.json", "interaction.json", "llm_zones.json",
                "validation.json", "ink/story.ink", "ink/story.ink.json",
                "preview.html"):
        assert (pack_dir / rel).is_file(), f"缺文件：{rel}"
    # validation
    v = json.loads((pack_dir / "validation.json").read_text(encoding="utf-8"))
    assert v["ok"] is True and v["errors"] == []
    assert v["stats"]["endings"] == 2 and v["stats"]["choices"] == 1
    assert "llm_calls" in v["stats"] and "cost_est" in v["stats"]
    # LLM 调用数：对白兜底 1 批 + 选择 1 + 结局 1 = 3（spec §15 上限 4）
    assert len(hook.calls) <= 4
    # pack.json 元信息
    info = json.loads((pack_dir / "pack.json").read_text(encoding="utf-8"))
    assert info["schema_version"] == "0.1"
    assert info["source"]["arc_ids"] == ["arc_test001"]
    assert info["game"]["entry_node"] == "n001"
    assert set(info["game"]["endings"]) == {"end_a", "end_b"}
    assert info["modes"]["offline"] is True
    # 编译产物是合法 ink json
    compiled = json.loads((pack_dir / "ink" / "story.ink.json").read_text(encoding="utf-8"))
    assert compiled.get("inkVersion")
    # 预览页内嵌编译产物与 inkjs 运行时
    html = (pack_dir / "preview.html").read_text(encoding="utf-8")
    assert "inkjs" in html and "flags_read_chart" in html
    # 对白归一进 story.json：n001 两条对白全部解析成功（regex + LLM 兜底）
    story = json.loads((pack_dir / "story.json").read_text(encoding="utf-8"))
    n001 = next(n for n in story["nodes"] if n["id"] == "n001")
    dlg = [ln for ln in n001["lines"] if ln["kind"] == "dialogue"]
    assert len(dlg) == 2 and all(ln.get("speaker") == "char_001" for ln in dlg)
    # 全部对白解析成功：无 SPEAKER_UNRESOLVED 警告
    assert not any(w["code"] == "SPEAKER_UNRESOLVED" for w in v["warnings"])


def asyncio_run_build(book: Path, arc_id: str, hook) -> dict:
    import asyncio
    return asyncio.run(adaptation_cli.build_pack(str(book), arc_id, 2, llm_hook=hook))


def test_build_pack_missing_l4_raises(book):
    with pytest.raises(AdaptationError):
        import asyncio
        asyncio.run(adaptation_cli.build_pack(str(book), "arc_nol4", 2))


def test_build_pack_compile_failure_keeps_failed_dir(book, monkeypatch):
    def _broken(ink_text, timeout_s=60):
        return CompileResult(ok=False,
                             errors=[CompileError(line=3, message="模拟编译失败")])
    monkeypatch.setattr(adaptation_cli, "compile_ink", _broken)
    import asyncio
    result = asyncio.run(adaptation_cli.build_pack(
        str(book), "arc_test001", 2, llm_hook=_CountingHook()))
    assert result["ok"] is False and result["failed"] is True
    assert result["pack_dir"].endswith(".failed")
    v = result["validation"]
    assert any(e["code"] == "INK_COMPILE" and "line 3" in e["detail"] for e in v["errors"])
    # 中间产物保留在 .failed/ 目录
    assert (Path(result["pack_dir"]) / "story.json").is_file()
    # 正常目录不应存在
    assert not (Path(result["pack_dir"]).parent / result["pack_id"]).exists()


def test_validate_cli_on_built_pack(book, tmp_path):
    import asyncio
    result = asyncio.run(adaptation_cli.build_pack(
        str(book), "arc_test001", 2, llm_hook=_CountingHook()))
    from prompt_harness.adaptation.validator import validate_dir
    report = validate_dir(result["pack_dir"])
    assert report.ok, report.errors


# ============================================================
# server 端点（/api/prompt-harness/ai-creation/adaptation/*）
# ============================================================

@pytest.fixture()
def client(monkeypatch, book):
    from prompt_harness import server as server_mod
    app = FastAPI()
    app.include_router(server_mod.router)
    async def _stub(*a, **k):
        return _stub_build_result(book, a[1] if len(a) > 1 else "arc_test001")
    monkeypatch.setattr(adaptation_cli, "build_pack", _stub)
    with TestClient(app) as c:
        yield c


def _stub_build_result(book: Path, arc_id: str = "arc_test001") -> dict:
    if arc_id == "arc_nol4":
        return {"ok": False, "arc_id": arc_id,
                "error": f"弧 {arc_id} 没有可用的 l4 场景快照（state.levels.l4.scenes 为空）；"
                         "v0 限制单章弧，请先在当前章生成场景"}
    return {
        "ok": True, "pack_id": "pack_testbook_arc_test001_abc123",
        "pack_dir": str(book / ".ainovel" / "adaptation"
                        / "pack_testbook_arc_test001_abc123"),
        "failed": False,
        "validation": {"ok": True, "errors": [], "warnings": [],
                       "stats": {"nodes": 7, "llm_calls": 0, "cost_est": 0.0}},
        "book_root": str(book), "arc_id": arc_id,
    }


def _make_pack_dir(book: Path, name="pack_testbook_arc_test001_abc123") -> Path:
    d = book / ".ainovel" / "adaptation" / name
    (d / "ink").mkdir(parents=True, exist_ok=True)
    (d / "pack.json").write_text(json.dumps({
        "schema_version": "0.1", "pack_id": name,
        "game": {"title": "纸人教室", "endings": ["end_a", "end_b"]},
    }, ensure_ascii=False), encoding="utf-8")
    (d / "validation.json").write_text(json.dumps({
        "ok": True, "errors": [], "warnings": [],
        "stats": {"nodes": 7, "llm_calls": 4, "cost_est": 0.0042},
    }, ensure_ascii=False), encoding="utf-8")
    (d / "story.json").write_text(json.dumps({"start": "n001", "nodes": []}),
                                  encoding="utf-8")
    (d / "ink" / "story.ink.json").write_text(json.dumps({"inkVersion": 21}),
                                              encoding="utf-8")
    return d


def test_server_build_endpoint(client, book):
    r = client.post("/api/prompt-harness/ai-creation/adaptation/build", json={
        "book_root": str(book), "arc_ids": ["arc_test001"]})
    assert r.status_code == 200
    data = r.json()
    assert data["ok"] is True and data["results"][0]["pack_id"].startswith("pack_")
    # 单弧失败不中断：坏弧记录 error
    r = client.post("/api/prompt-harness/ai-creation/adaptation/build", json={
        "book_root": str(book), "arc_ids": ["arc_nol4"]})
    assert r.status_code == 200
    assert r.json()["results"][0]["ok"] is False
    assert "l4" in r.json()["results"][0]["error"]


def test_server_packs_endpoint(client, book):
    _make_pack_dir(book)
    r = client.post("/api/prompt-harness/ai-creation/adaptation/packs",
                    json={"book_root": str(book)})
    assert r.status_code == 200
    packs = r.json()["packs"]
    assert len(packs) == 1
    assert packs[0]["pack_id"] == "pack_testbook_arc_test001_abc123"
    assert packs[0]["failed"] is False
    assert packs[0]["validation"]["ok"] is True
    assert packs[0]["validation"]["stats"]["llm_calls"] == 4


def test_server_packs_marks_failed(client, book):
    _make_pack_dir(book, "pack_x_arc_y_000001.failed")
    r = client.post("/api/prompt-harness/ai-creation/adaptation/packs",
                    json={"book_root": str(book)})
    entry = r.json()["packs"][0]
    assert entry["failed"] is True and entry["pack_id"] == "pack_x_arc_y_000001"


def test_server_pack_endpoint_with_include(client, book):
    _make_pack_dir(book)
    r = client.post("/api/prompt-harness/ai-creation/adaptation/pack", json={
        "book_root": str(book), "pack_id": "pack_testbook_arc_test001_abc123",
        "include": ["story", "ink"]})
    assert r.status_code == 200
    data = r.json()
    assert data["pack"]["pack_id"] == "pack_testbook_arc_test001_abc123"
    assert data["validation"]["ok"] is True
    assert data["has_preview"] is False
    assert data["files"]["story"] == {"start": "n001", "nodes": []}
    assert data["files"]["ink"] == {"inkVersion": 21}


def test_server_pack_not_found(client, book):
    r = client.post("/api/prompt-harness/ai-creation/adaptation/pack", json={
        "book_root": str(book), "pack_id": "pack_ghost"})
    assert r.status_code == 404


def test_server_pack_path_traversal_blocked(client, book):
    r = client.post("/api/prompt-harness/ai-creation/adaptation/pack", json={
        "book_root": str(book), "pack_id": "../evil"})
    assert r.status_code == 400


def test_server_pack_delete_endpoint(client, book):
    d = _make_pack_dir(book)
    r = client.post("/api/prompt-harness/ai-creation/adaptation/pack/delete", json={
        "book_root": str(book), "pack_id": "pack_testbook_arc_test001_abc123"})
    assert r.status_code == 200 and r.json()["ok"] is True
    assert not d.exists()
    # 再删一次 → 404
    r = client.post("/api/prompt-harness/ai-creation/adaptation/pack/delete", json={
        "book_root": str(book), "pack_id": "pack_testbook_arc_test001_abc123"})
    assert r.status_code == 404
