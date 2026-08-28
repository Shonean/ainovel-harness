"""初始化模式测试：arc_chat(mode="init") 工具裁剪 + 初始化工具接入 _execute_chat_tool。

mock LLM，0 token。覆盖：
- init 模式禁用 l2-l5 阶梯工具（step/finalize 等）
- fill_settings 保存基本设定
- create_arc 建开篇弧（带容量诊断）
- normal 模式工具仍可用（回归）
"""
from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path

from prompt_harness import ai_creation as ac


def _init_book(book_root: Path):
    (book_root / ".ainovel").mkdir(parents=True, exist_ok=True)
    ac.save_basic_settings(book_root, ac._empty_basic_settings())
    ac.save_arcs(book_root, {"arcs": [], "next_chapter_num": 1})


def test_init_mode_blocks_ladder_tools(monkeypatch):
    """init 模式：_execute_chat_tool 拒绝 l2-l5 工具。"""
    with tempfile.TemporaryDirectory() as tmp:
        book_root = Path(tmp)
        _init_book(book_root)
        # step（l2-l5 工具）在 init 模式被拒
        ev, needs = asyncio.run(ac._execute_chat_tool(
            "step", {}, book_root, None, {}, dry_run=False, mode="init"))
        assert ev.get("failed") is True
        assert "初始化阶段不可用" in ev.get("summary", "")
        # finalize 同样被拒
        ev2, _ = asyncio.run(ac._execute_chat_tool(
            "finalize", {}, book_root, None, {}, dry_run=False, mode="init"))
        assert ev2.get("failed") is True


def test_normal_mode_allows_ladder_tools(monkeypatch):
    """normal 模式：step 不因 mode 被拒（回归，不真正执行）。"""
    with tempfile.TemporaryDirectory() as tmp:
        book_root = Path(tmp)
        _init_book(book_root)
        # normal 模式下 step 不走 mode 拦截（但需要弧才能执行——这里只验证不走 init 拦截）
        ev, needs = asyncio.run(ac._execute_chat_tool(
            "step", {}, book_root, None, {}, dry_run=False, mode="normal"))
        # step 无弧 → 会走"当前还没有情节"分支，不是 init 拦截
        assert "初始化阶段不可用" not in (ev or {}).get("summary", "")


async def _fake_facts(*args, **kwargs):
    return ["少年", "青云宗", "妹妹"]


async def _fake_chat_filled(**kwargs):
    return {"content": "少年入青云宗考弟子，遇仇家追杀，救下被绑的妹妹，遁入青羊山。",
            "usage": None, "error": None}


def test_fill_settings_tool(monkeypatch):
    """fill_settings：保存 16 字段基本设定。"""
    with tempfile.TemporaryDirectory() as tmp:
        book_root = Path(tmp)
        _init_book(book_root)
        ev, needs = asyncio.run(ac._execute_chat_tool(
            "fill_settings", {"settings": {"name": "青云传", "genre": "玄幻",
                                           "one_liner": "少年入宗门救妹妹"}},
            book_root, None, {}, dry_run=False, mode="init"))
        assert needs is False
        assert ev.get("changed") is True
        s = ac.load_basic_settings(book_root)
        assert s.get("name") == "青云传"
        assert s.get("genre") == "玄幻"


def test_create_arc_tool(monkeypatch):
    """create_arc：建开篇弧（容量诊断+补齐）。"""
    from prompt_harness import llm_client
    from prompt_harness import capacity as cap
    monkeypatch.setattr(llm_client, "chat_completion", _fake_chat_filled)
    monkeypatch.setattr(cap, "_get_facts_cached", _fake_facts)

    with tempfile.TemporaryDirectory() as tmp:
        book_root = Path(tmp)
        _init_book(book_root)
        ac.save_basic_settings(book_root, {"name": "青云传", "genre": "玄幻",
                                           "one_liner": "少年入宗门救妹妹",
                                           "style": "热血", "generated_at": "now"})
        ev, needs = asyncio.run(ac._execute_chat_tool(
            "create_arc", {"l1": "少年入青云宗考弟子。"},
            book_root, None, {}, dry_run=False, mode="init"))
        assert ev.get("changed") is True
        assert "建开篇弧" in ev.get("summary", "")
        arcs = ac.load_arcs(book_root)
        assert len(arcs.get("arcs") or []) >= 1


async def _fake_chat_json_any(**kwargs):
    # 预检索 search_understand / 主对话都返回无 action（只 reply）
    return {"data": {"reply": "初始化助手回应", "action": None}, "raw": ""}


def test_arc_chat_init_mode_sys_p(monkeypatch):
    """arc_chat(mode='init')：sys_p 是初始化引导版（含 fill_settings 工具）。"""
    from prompt_harness import llm_client
    monkeypatch.setattr(llm_client, "chat_json", _fake_chat_json_any)

    with tempfile.TemporaryDirectory() as tmp:
        book_root = Path(tmp)
        _init_book(book_root)
        res = asyncio.run(ac.arc_chat(
            book_root, "", [{"role": "user", "content": "帮我初始化这本书"}],
            mode="init"))
        assert res.get("ok") is True
