"""roadmap 单测：生成/校验/冻结/单卡重生成/续写/上下文注入（LLM 全 mock）。"""
from __future__ import annotations

import asyncio

import prompt_harness.derive as derive
from prompt_harness import roadmap as rm


def _raw(open_in_last=False, close=True):
    arcs = [
        {"title": "纸人教室", "act": 1, "role": "铺垫",
         "l1": "陈守念在纸人教室醒来。", "l2": "起因/冲突/转折/结局。",
         "characters": ["陈守念"], "elements": ["病人卡"],
         "foreshadow_open": ["死亡宣告纸条"] if not open_in_last else [],
         "foreshadow_close": []},
        {"title": "死讯纸条", "act": 1, "role": "升级",
         "l1": "纸条上的死讯开始应验。", "l2": "起因/冲突/转折/结局。",
         "foreshadow_close": []},
        {"title": "循环真相", "act": 2, "role": "收束",
         "l1": "陈守念直面循环真相。", "l2": "起因/冲突/转折/结局。",
         "foreshadow_open": ["死亡宣告纸条"] if open_in_last else [],
         "foreshadow_close": (["死亡宣告纸条"] if close and not open_in_last else [])},
    ]
    return {
        "logline": {"want": "找回身份", "obstacle": "纸人诅咒", "cost": "遗忘", "ending": "叫出名字"},
        "acts": [{"title": "第一幕 · 醒来", "goal": "建立规则"},
                 {"title": "第二幕 · 真相", "goal": "揭底"}],
        "arcs": arcs,
    }


def _patch_llm(monkeypatch, payload):
    async def fake(system, user, call_type, max_tokens=8000):
        return payload
    monkeypatch.setattr(derive, "_tree_llm", fake)


def test_generate_structure_and_ledger(tmp_path, monkeypatch):
    _patch_llm(monkeypatch, _raw())
    r = asyncio.run(rm.generate_roadmap(tmp_path, target_chapters=9, n_chapters_per_arc=3))
    assert r["ok"] is True
    road = r["roadmap"]
    assert [a["id"] for a in road["arcs"]] == ["a1", "a2", "a3"]
    assert road["arcs"][0]["act_id"] == "act1"
    assert road["arcs"][2]["act_id"] == "act2"
    assert road["status"] == "draft"
    assert road["version"] == 1
    f = {x["name"]: x for x in road["foreshadow"]}
    assert f["死亡宣告纸条"]["open_arc"] == "a1"
    assert f["死亡宣告纸条"]["close_arc"] == "a3"
    assert f["死亡宣告纸条"]["status"] == "closed"
    assert rm.load_roadmap(tmp_path)["arcs"][1]["title"] == "死讯纸条"


def test_freeze_blocks_dangling_then_ok(tmp_path, monkeypatch):
    _patch_llm(monkeypatch, _raw(open_in_last=True, close=False))
    asyncio.run(rm.generate_roadmap(tmp_path, target_chapters=9, n_chapters_per_arc=3))
    fr = rm.freeze_roadmap(tmp_path)
    assert fr["ok"] is False
    assert any("悬空" in e for e in fr["errors"])

    road = rm.load_roadmap(tmp_path)
    road["arcs"][2]["foreshadow_close"] = ["死亡宣告纸条"]
    road["foreshadow"] = rm._build_foreshadow(road["arcs"])
    rm.save_roadmap(tmp_path, road)
    fr2 = rm.freeze_roadmap(tmp_path)
    assert fr2["ok"] is True
    assert fr2["roadmap"]["status"] == "frozen"
    assert fr2["roadmap"]["frozen_at"]


def test_validate_element_reference_warning(tmp_path, monkeypatch):
    _patch_llm(monkeypatch, _raw())
    r = asyncio.run(rm.generate_roadmap(tmp_path))
    v = r["validation"]
    assert v["ok"] is True
    assert any("未入库元素" in w for w in v["warnings"])


def test_regenerate_arc_keeps_identity(tmp_path, monkeypatch):
    _patch_llm(monkeypatch, _raw())
    asyncio.run(rm.generate_roadmap(tmp_path))
    _patch_llm(monkeypatch, {"title": "纸人审判", "l1": "重写的一句话。",
                             "l2": "重写的情节线。", "characters": [],
                             "elements": [],
                             "foreshadow_open": ["纸人身份"], "foreshadow_close": []})
    r = asyncio.run(rm.regenerate_arc(tmp_path, "a2", instruction="更紧张"))
    assert r["ok"] is True
    assert r["arc"]["id"] == "a2"
    assert r["arc"]["index"] == 2
    assert r["arc"]["title"] == "纸人审判"
    assert r["roadmap"]["version"] == 2
    f = {x["name"]: x for x in r["roadmap"]["foreshadow"]}
    assert f["纸人身份"]["open_arc"] == "a2"


def test_continue_arcs_appends(tmp_path, monkeypatch):
    _patch_llm(monkeypatch, _raw())
    asyncio.run(rm.generate_roadmap(tmp_path))
    _patch_llm(monkeypatch, {"arcs": [{"title": "新的循环", "role": "高潮",
                                       "l1": "又一轮。", "l2": "情节线。"}]})
    r = asyncio.run(rm.continue_arcs(tmp_path, count=1))
    assert r["ok"] is True
    assert r["added"] == 1
    arcs = r["roadmap"]["arcs"]
    assert len(arcs) == 4
    assert arcs[-1]["id"] == "a4"
    assert arcs[-1]["act_id"] == arcs[2]["act_id"]


def test_context_only_when_frozen(tmp_path, monkeypatch):
    _patch_llm(monkeypatch, _raw())
    asyncio.run(rm.generate_roadmap(tmp_path))
    assert rm.roadmap_context_for_arc(tmp_path, 1) == ""
    rm.freeze_roadmap(tmp_path)
    ctx = rm.roadmap_context_for_arc(tmp_path, 1)
    assert "全书路线图" in ctx
    assert "纸人教室" in ctx
    assert "本弧需回收" in ctx or "本弧需埋设" in ctx
    assert rm.roadmap_context_for_arc(tmp_path, 99) == ""


def test_patch_and_reorder(tmp_path, monkeypatch):
    _patch_llm(monkeypatch, _raw())
    asyncio.run(rm.generate_roadmap(tmp_path))
    r = rm.patch_arc(tmp_path, "a2", {"l1": "改后的一句话", "chapters": 5})
    assert r["ok"] is True
    assert r["arc"]["l1"] == "改后的一句话"
    assert r["arc"]["chapters"] == 5
    assert r["roadmap"]["status"] == "draft"

    r2 = rm.reorder_arc(tmp_path, "a3", 1)
    assert r2["ok"] is True
    road = r2["roadmap"]
    assert [a["id"] for a in road["arcs"]] == ["a3", "a1", "a2"]
    assert [a["index"] for a in road["arcs"]] == [1, 2, 3]


def test_chat_tool_roadmap_branch(tmp_path, monkeypatch):
    from prompt_harness import ai_creation as ac

    _patch_llm(monkeypatch, _raw())
    asyncio.run(rm.generate_roadmap(tmp_path))

    ev, needs = asyncio.run(ac._execute_chat_tool(
        "roadmap", {"action": "patch", "arc_id": "a1", "fields": {"l2": "新的情节线"}},
        tmp_path, None, {}, dry_run=False))
    assert needs is False
    assert ev["changed"] is True
    assert rm.load_roadmap(tmp_path)["arcs"][0]["l2"] == "新的情节线"

    ev2, needs2 = asyncio.run(ac._execute_chat_tool(
        "roadmap", {"action": "patch", "arc_id": "a1", "fields": {"l2": "x"}},
        tmp_path, None, {}, dry_run=True))
    assert needs2 is True
    assert ev2["pending"] is True
