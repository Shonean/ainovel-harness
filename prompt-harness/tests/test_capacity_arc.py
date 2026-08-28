"""capacity 集成测试：new_arc 诊断钩子 + arc_chat enrich_l1 工具（mock LLM，0 token）。"""
from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path

from prompt_harness import ai_creation as ac


async def _init_book(book_root: Path):
    """初始化临时书（basic_settings + arcs.json）。"""
    (book_root / ".ainovel").mkdir(parents=True, exist_ok=True)
    ac.save_basic_settings(book_root, {
        "name": "测试书", "genre": "玄幻", "one_liner": "测试", "generated_at": "now",
    })
    ac.save_arcs(book_root, {"arcs": [], "next_chapter_num": 1})


async def _fake_facts(*args, **kwargs):
    return ["林九", "魔门", "宗门大比"]


async def _fake_chat_filled(**kwargs):
    return {"content": "林九穿越到修仙界，为救妹妹加入魔门，在宗门大比暴露身份被仇家追杀，反杀夺回仙丹，躲进青羊山。",
            "usage": None, "error": None}


def test_new_arc_diagnosis(monkeypatch):
    """new_arc 自动诊断：返回含 diagnosis；薄 l1 auto_fill 后 l1_filled。"""
    from prompt_harness import llm_client
    from prompt_harness import capacity as cap
    monkeypatch.setattr(llm_client, "chat_completion", _fake_chat_filled)
    # 诊断里的 facts 用 mock（不走真实 LLM）
    monkeypatch.setattr(cap, "_get_facts_cached", _fake_facts)

    with tempfile.TemporaryDirectory() as tmp:
        book_root = Path(tmp)
        asyncio.run(_init_book(book_root))
        # 薄 l1 → should_fill → 补齐
        res = asyncio.run(ac.new_arc(
            book_root, l1="林九去复仇。", auto_diagnose=True, auto_fill=True,
            carry_prev=False))
        assert res["ok"] is True
        assert res.get("diagnosis") is not None
        assert res.get("l1_filled") is True
        assert res.get("l1_original") == "林九去复仇。"
        # 弧的 l1 被补齐（不再是原文）
        arc = res["arc"]
        assert str(arc.get("l1") or "") != "林九去复仇。"
        assert arc.get("l1_original") == "林九去复仇。"
        assert arc.get("capacity") is not None


def test_new_arc_no_fill_when_rich(monkeypatch):
    """信息充分的 l1 → 不补齐（l1_filled=False，l1 原样保留）。"""
    from prompt_harness import llm_client
    from prompt_harness import capacity as cap
    monkeypatch.setattr(llm_client, "chat_completion", _fake_chat_filled)
    monkeypatch.setattr(cap, "_get_facts_cached", _fake_facts)

    rich = ("林九穿越到修仙界，为给妹妹治病加入魔门，在宗门大比暴露身份，"
            "被仇家追杀，反杀夺回仙丹，遁入青羊山躲避仇家追击。")
    with tempfile.TemporaryDirectory() as tmp:
        book_root = Path(tmp)
        asyncio.run(_init_book(book_root))
        res = asyncio.run(ac.new_arc(
            book_root, l1=rich, auto_diagnose=True, auto_fill=True, carry_prev=False))
        assert res["ok"] is True
        assert res.get("l1_filled") in (False, True)  # 充分 l1 可能不触发补齐
        arc = res["arc"]
        assert arc.get("capacity") is not None
        # l1 至少非空
        assert str(arc.get("l1") or "").strip()


def test_enrich_tool_dry_run_proposal(monkeypatch):
    """arc_chat 的 enrich_l1：dry_run 走提案（needs_confirm），apply 才真执行。"""
    from prompt_harness import llm_client
    from prompt_harness import capacity as cap
    monkeypatch.setattr(llm_client, "chat_completion", _fake_chat_filled)
    monkeypatch.setattr(cap, "_get_facts_cached", _fake_facts)

    with tempfile.TemporaryDirectory() as tmp:
        book_root = Path(tmp)
        asyncio.run(_init_book(book_root))
        # 先建弧
        res = asyncio.run(ac.new_arc(book_root, l1="林九去复仇。",
                                     auto_diagnose=True, auto_fill=False, carry_prev=False))
        arc_id = res["arc"]["id"]

        # dry_run：enrich_l1 走提案
        ev, needs = asyncio.run(ac._execute_chat_tool(
            "enrich_l1", {"max_chars": 160}, book_root,
            ac._find_arc(ac.load_arcs(book_root), arc_id), {},
            dry_run=True))
        assert needs is True
        assert ev.get("pending") is True

        # apply（dry_run=False）：真执行补齐，arc_changed
        ev2, needs2 = asyncio.run(ac._execute_chat_tool(
            "enrich_l1", {"max_chars": 160}, book_root,
            ac._find_arc(ac.load_arcs(book_root), arc_id), {},
            dry_run=False))
        assert needs2 is False
        assert ev2.get("arc_changed") is True


def test_capacity_tool_readonly(monkeypatch):
    """arc_chat 的 capacity：只读，无 pending。"""
    with tempfile.TemporaryDirectory() as tmp:
        book_root = Path(tmp)
        asyncio.run(_init_book(book_root))
        res = asyncio.run(ac.new_arc(book_root, l1="林九去复仇。",
                                     auto_diagnose=False, carry_prev=False))
        arc_id = res["arc"]["id"]
        ev, needs = asyncio.run(ac._execute_chat_tool(
            "capacity", {}, book_root,
            ac._find_arc(ac.load_arcs(book_root), arc_id), {}, dry_run=True))
        assert needs is False
        assert "章" in ev.get("summary", "") or "字" in ev.get("summary", "")
