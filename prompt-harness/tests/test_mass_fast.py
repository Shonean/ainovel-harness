"""P4 量产 fast 单测：运行时开关/用量累计/l2 方向注入/跳过评分落 score_deferred。"""
from __future__ import annotations

import asyncio
import json

import prompt_harness.bridge as bridge
from prompt_harness import ai_creation as ac
from prompt_harness import llm_client as lc
from prompt_harness import roadmap as rm
from prompt_harness import runtime_flags as rf


def _write_arcs(book_root, *, l5: str = ""):
    arc = {
        "id": "arc_001", "name": "测试弧", "l1": "一句话剧情", "l2": "情节概要",
        "status": "active",
        "selected": {"characters": [], "items": [], "settings": []},
        "state": {
            "levels": {
                "l1": {"text": "一句话剧情", "confirmed": True},
                "l2": {"text": "情节概要", "confirmed": True},
                "l3": {"text": "章纲", "confirmed": True,
                       "data": {"title": "第1章 测试", "core": "核心", "beats": ["拍1"]}},
                "l4": {"text": "", "confirmed": True, "scenes": [{"name": "场景"}]},
                "l5": {"text": l5, "confirmed": True, "prompt": "p"},
            },
            "active_chapter": 0,
        },
        "chapters": [],
    }
    ac.save_arcs(book_root, {"arcs": [arc], "next_chapter_num": 1})


def test_runtime_flags_defaults_and_set():
    assert rf.is_fast() is False
    assert rf.get_model() == ""
    rf.set_fast(True)
    rf.set_model("deepseek-v4-flash")
    assert rf.is_fast() is True
    assert rf.get_model() == "deepseek-v4-flash"
    rf.set_fast(False)
    rf.set_model(None)
    assert rf.is_fast() is False
    assert rf.get_model() == ""


def test_run_usage_accumulation():
    rid = "test_run_usage_001"
    lc.set_llm_run_id(rid)
    try:
        lc._accumulate_run_usage({"prompt_tokens": 10, "completion_tokens": 5,
                                  "total_tokens": 15, "cost_usd": 0.01})
        lc._accumulate_run_usage({"prompt_tokens": 20, "completion_tokens": 5,
                                  "total_tokens": 25, "cost_usd": 0.02})
    finally:
        lc.set_llm_run_id("")
    u = lc.get_run_usage(rid)
    assert u == {"calls": 2, "prompt_tokens": 30, "completion_tokens": 10,
                 "total_tokens": 40, "cost_usd": 0.03}
    # 未设置 run_id 的调用不累计
    lc._accumulate_run_usage({"total_tokens": 999, "cost_usd": 9.9})
    assert lc.get_run_usage(rid)["total_tokens"] == 40


def test_finalize_skip_score_marks_deferred(tmp_path, monkeypatch):
    _write_arcs(tmp_path, l5="正文内容。" * 60)

    async def _boom(*a, **kw):
        raise AssertionError("skip_score 时不应调用 score_chapter")

    monkeypatch.setattr(ac, "score_chapter", _boom)
    r = asyncio.run(ac.finalize_chapter(tmp_path, "arc_001", chapter_idx=0, skip_score=True))
    assert r.get("ok") is True

    report = json.loads((tmp_path / "审查报告" / "第0001章-评分.json").read_text(encoding="utf-8"))
    assert report["scores"]["score_deferred"] is True
    assert report["scores"]["overall"] is None

    arcs = ac.load_arcs(tmp_path)
    entry = arcs["arcs"][0]["chapters"][0]
    assert entry["score_deferred"] is True
    assert entry["overall"] is None
    assert entry["num"] == 1


def test_rescore_deferred_updates_scores(tmp_path, monkeypatch):
    _write_arcs(tmp_path, l5="正文内容。" * 60)
    arcs = ac.load_arcs(tmp_path)
    entry = {"num": 1, "title": "测试章", "core": "核心",
             "intent_score": None, "quality_score": None, "overall": None,
             "polluted": 0, "score_deferred": True, "text": "正文内容。" * 60}
    arcs["arcs"][0]["chapters"] = [entry]
    arcs["arcs"][0]["finalized"] = {"0": 1}
    ac.save_arcs(tmp_path, arcs)

    rep_dir = tmp_path / "审查报告"
    rep_dir.mkdir(parents=True, exist_ok=True)
    (rep_dir / "第0001章-评分.json").write_text(json.dumps({
        "chapter": 1, "title": "测试章", "intent_text": "情节概要",
        "scores": {"score_deferred": True, "overall": None},
    }, ensure_ascii=False), encoding="utf-8")

    async def fake_score(text, intent, arc, elements):
        return {"intent_score": 0.8, "quality_score": 0.7, "overall": 0.75, "polluted": 0}

    monkeypatch.setattr(ac, "score_chapter", fake_score)
    r = asyncio.run(ac.rescore_deferred(tmp_path))
    assert r["rescored"] == 1
    assert r["failed"] == 0

    arcs2 = ac.load_arcs(tmp_path)
    e2 = arcs2["arcs"][0]["chapters"][0]
    assert e2["overall"] == 0.75
    assert e2["score_deferred"] is False
    rep = json.loads((rep_dir / "第0001章-评分.json").read_text(encoding="utf-8"))
    assert rep["scores"]["overall"] == 0.75
    assert rep.get("rescored_at")


def test_arc_step_injects_roadmap_only_for_l2(tmp_path, monkeypatch):
    # 书 + 冻结路线图（1 弧）
    ac.save_arcs(tmp_path, {"arcs": [{
        "id": "arc_001", "name": "一弧", "l1": "一句话",
        "status": "draft",
        "selected": {"characters": ["c1"], "items": [], "settings": []},
        "state": {"levels": {"l1": {"text": "一句话", "confirmed": True}},
                  "active_chapter": 0},
        "chapters": [],
    }], "next_chapter_num": 1})
    road = rm._build_roadmap(
        {"logline": {"want": "要", "obstacle": "挡", "cost": "价", "ending": "终"},
         "acts": [{"title": "第一幕"}],
         "arcs": [{"title": "一弧", "act": 1, "l1": "一句话",
                   "l2": "起因/冲突/转折/结局"}]},
        n_chapters_per_arc=3, target_chapters=3)
    road["status"] = "frozen"
    rm.save_roadmap(tmp_path, road)

    seen: dict = {}

    async def fake_step(state):
        seen["roadmap_block"] = state.get("roadmap_block")
        state.setdefault("levels", {})["l2"] = {"text": "生成的情节概要"}
        return {"ok": True, "to": "l2", "text": "生成的情节概要"}

    monkeypatch.setattr(bridge, "step_ladder", fake_step)
    r = asyncio.run(ac.arc_step(tmp_path, "arc_001", roadmap_index=1))
    assert r.get("ok") is True
    assert "全书路线图" in (seen.get("roadmap_block") or "")
    assert "一弧" in seen["roadmap_block"]

    # l2 已生成 → 下一目标 l3：不再注入（并清掉 state 里的方向块）
    async def fake_step_l3(state):
        seen["l3_block"] = state.get("roadmap_block")
        state["levels"]["l3"] = {"text": "章纲"}
        return {"ok": True, "to": "l3", "text": "章纲"}

    monkeypatch.setattr(bridge, "step_ladder", fake_step_l3)
    r2 = asyncio.run(ac.arc_step(tmp_path, "arc_001", roadmap_index=1))
    assert r2.get("ok") is True
    assert seen.get("l3_block") is None
