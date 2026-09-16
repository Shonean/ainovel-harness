"""D1 三级记忆系统测试（book_memory.py + ai_creation 兼容层接线）。

覆盖（Phase 2 验收清单）：
- 冲突产卡：同实体同属性不同值 → open 卡 + 反方通道双值并存
- 处置四动作：coexist_layered / version_rewrite / revert / add_slot
- 单对话工作记忆：session 隔离、每轮必注入（_memory_block 拆段）
- 旧 memory.json 迁移幂等（旧文件只读保留）
- SW1 消融对照：关冲突管线 = 静默覆盖（无卡无标记），证明管线有效
- remember/forget 工具接线：scope=session / 冲突卡摘要 / v2 归档
- 兼容层：load/add/update/delete_memory 走 v2 后签名与形状不变
"""
from __future__ import annotations

import asyncio
import json
import tempfile
from pathlib import Path

import pytest

from prompt_harness import ai_creation as ac
from prompt_harness import book_memory as bmem


@pytest.fixture()
def book(tmp_path: Path) -> Path:
    (tmp_path / ".ainovel").mkdir(parents=True, exist_ok=True)
    return tmp_path


# ── 语义库：冲突产卡 + 反方通道 ──

def test_slow_write_conflict_card(book: Path):
    bm = bmem.BookMemory(book)
    r1 = bm.slow_write("艾琳", "eye_color", "绿色", via="user_edit")
    assert "node" in r1 and "card" not in r1
    r2 = bm.slow_write("艾琳", "eye_color", "灰色", via="user_edit")
    assert "card" in r2, "同实体同属性不同值应产卡"
    card = r2["card"]
    assert card["status"] == "open"
    assert card["claim_old"]["text"] == "绿色"
    assert card["claim_new"]["text"] == "灰色"
    # 链上双值都在（不覆盖）
    assert bmem.node_value(r2["node"]) == "灰色"
    assert r2["node"]["version_chain"][0]["value"] == "绿色"
    # 同值幂等：不产新卡
    r3 = bm.slow_write("艾琳", "eye_color", "灰色", via="user_edit")
    assert "card" not in r3
    assert len(bm.all_cards()) == 1


def test_slow_write_requires_valid_via(book: Path):
    bm = bmem.BookMemory(book)
    with pytest.raises(AssertionError):
        bm.slow_write("艾琳", "eye_color", "绿色", via="inference")  # 推理期禁直写


def test_recall_conflict_devil_channel(book: Path):
    """反方通道：open 卡时双值并存注入，不静默取一。"""
    bm = bmem.BookMemory(book)
    bm.slow_write("陈守念", "state", "活着的病人", via="user_edit")
    bm.slow_write("陈守念", "state", "已死的身份", via="user_edit")
    r = bm.recall("陈守念 的状态")
    assert len(r["conflicts"]) == 1
    mb = ac._memory_block(bm, "陈守念 的状态是什么", arc_id="", session_id="", sel_memory=[])
    assert "⚠" in mb and "旧说" in mb and "新说" in mb
    # 处置后（version_rewrite）反方通道关闭
    bm.resolve_card(r["conflicts"][0]["id"], "version_rewrite", note="定版")
    mb2 = ac._memory_block(bm, "陈守念 的状态是什么", arc_id="", session_id="", sel_memory=[])
    assert "⚠" not in mb2


# ── 处置四动作 ──

def _open_card(bm: bmem.BookMemory) -> tuple[dict, dict]:
    bm.slow_write("艾琳", "eye_color", "绿色", via="user_edit")
    r = bm.slow_write("艾琳", "eye_color", "灰色", via="user_edit")
    return r["node"], r["card"]


def test_resolve_coexist_layered(book: Path):
    bm = bmem.BookMemory(book)
    node, card = _open_card(bm)
    bm.resolve_card(card["id"], "coexist_layered", note="主线绿、回忆线灰")
    c = bm.all_cards()[0]
    assert c["status"] == "resolved" and c["resolution"]["action"] == "coexist_layered"
    n = [x for x in bm._load_nodes() if x["id"] == node["id"]][0]
    assert n["layered"] and len(n["layer_values"]) == 2
    mb = ac._memory_block(bm, "艾琳", arc_id="", session_id="", sel_memory=[])
    assert "分层并存" in mb and "旧：绿色" in mb and "新：灰色" in mb


def test_resolve_version_rewrite(book: Path):
    bm = bmem.BookMemory(book)
    node, card = _open_card(bm)
    bm.resolve_card(card["id"], "version_rewrite")
    assert bmem.node_value([x for x in bm._load_nodes() if x["id"] == node["id"]][0]) == "灰色"


def test_resolve_revert(book: Path):
    bm = bmem.BookMemory(book)
    node, card = _open_card(bm)
    bm.resolve_card(card["id"], "revert")
    n = [x for x in bm._load_nodes() if x["id"] == node["id"]][0]
    assert bmem.node_value(n) == "绿色"
    c = bm.all_cards()[0]
    assert c["resolution"]["reverted_value"] == "灰色"


def test_resolve_add_slot(book: Path):
    bm = bmem.BookMemory(book)
    node, card = _open_card(bm)
    bm.resolve_card(card["id"], "add_slot", slot="回忆线")
    nodes = bm._load_nodes()
    assert any(x["attr"] == "eye_color（回忆线）" and bmem.node_value(x) == "绿色" for x in nodes)


def test_resolve_unknown_card(book: Path):
    bm = bmem.BookMemory(book)
    assert bm.resolve_card("CCnotexist", "version_rewrite") is None


# ── 单对话工作记忆（session 隔离 + 必注入）──

def test_working_memory_session_isolation(book: Path):
    bm = bmem.BookMemory(book)
    bm.add_working("s1", "纸条必须一句话")
    bm.add_working("s2", "对白要短")
    assert [c["text"] for c in bm.get_working("s1")] == ["纸条必须一句话"]
    assert [c["text"] for c in bm.get_working("s2")] == ["对白要短"]
    bm.add_working("s1", "纸条必须一句话")  # 幂等
    assert len(bm.get_working("s1")) == 1
    assert bm.remove_working("s1", "纸条必须一句话")
    assert bm.get_working("s1") == []


def test_memory_block_working_always_injected(book: Path):
    """铁律跨轮必注入：工作记忆不参与召回排序，块里必有。"""
    bm = bmem.BookMemory(book)
    bm.add_working("sess1", "第三张纸条必须是简单的一句话")
    # query 与约束毫无关键词重叠，仍必须出现（必注入，不召回）
    mb = ac._memory_block(bm, "帮我看看这段节奏", arc_id="", session_id="sess1", sel_memory=[])
    assert "【工作记忆·本会话约束" in mb
    assert "第三张纸条必须是简单的一句话" in mb
    # 别的 session 不带这条
    mb2 = ac._memory_block(bm, "帮我看看这段节奏", arc_id="", session_id="sess2", sel_memory=[])
    assert "工作记忆" not in mb2


# ── 经情账本 ──

def test_episodic_append_only(book: Path):
    bm = bmem.BookMemory(book)
    bm.log_episode("第一轮用户消息", kind="user_msg", session_id="s1")
    bm.log_episode("第一轮助手回复", kind="assistant_reply", session_id="s1")
    eps = bm.recent_episodes(10)
    assert len(eps) == 2 and eps[0]["text"] == "第一轮助手回复"  # 倒序
    assert eps[0]["kind"] == "assistant_reply"


# ── 旧 memory.json 迁移（幂等）──

def test_migration_idempotent(book: Path):
    legacy_path = book / ".ainovel" / "memory.json"
    legacy_path.write_text(json.dumps([
        {"id": "abc12345", "text": "对白要短", "at": "2026-09-01T00:00:00Z"},
        {"id": "def67890", "text": "主角姓陈", "scope": "book", "at": "2026-09-01T00:00:00Z"},
    ], ensure_ascii=False), encoding="utf-8")
    bm = bmem.BookMemory(book)
    n1 = bm.ensure_migrated(json.loads(legacy_path.read_text(encoding="utf-8")))
    assert n1 == 2
    n2 = bm.ensure_migrated(json.loads(legacy_path.read_text(encoding="utf-8")))
    assert n2 == 0, "二次迁移必须幂等"
    assert bm.is_migrated()
    # 旧文件只读保留
    assert json.loads(legacy_path.read_text(encoding="utf-8"))[0]["id"] == "abc12345"
    # 迁移后 load_memory 投影出 2 条
    items = ac.load_memory(book)
    assert len(items) == 2
    assert {i["text"] for i in items} == {"对白要短", "主角姓陈"}


# ── SW1 消融：关冲突管线 = 静默覆盖（反面教材对照）──

def test_ablation_sw1_silent_overwrite(book: Path, monkeypatch):
    monkeypatch.setattr(bmem, "CONFLICT_PIPELINE", False)
    bm = bmem.BookMemory(book)
    bm.slow_write("艾琳", "eye_color", "绿色", via="user_edit")
    r = bm.slow_write("艾琳", "eye_color", "灰色", via="user_edit")
    assert "card" not in r, "管线关闭：不产卡"
    n = r["node"]
    assert n["version_chain"][-1]["via"] == "silent_overwrite"
    assert bm.all_cards() == []
    assert bmem.node_value(n) == "灰色"


def test_consolidation_deferred_by_default(book: Path, monkeypatch):
    """自动巩固默认关：consolidation 路径不动库、不产卡、不覆盖（骨架预留）。"""
    monkeypatch.delenv("AINOVEL_CONSOLIDATION", raising=False)
    bm = bmem.BookMemory(book)
    bm.slow_write("艾琳", "eye_color", "绿色", via="user_edit")
    r = bm.slow_write("艾琳", "eye_color", "灰色", via="consolidation")
    assert r.get("consolidation_deferred") is True
    assert bmem.node_value(r["node"]) == "绿色"  # 未被覆盖
    assert bm.all_cards() == []


# ── 工具接线：remember / forget ──

def test_remember_tool_session_scope(book: Path):
    ev, needs = asyncio.run(ac._execute_chat_tool(
        "remember", {"key": "纸条铁律", "content": "纸条必须一句话", "scope": "session"},
        book, None, {}, dry_run=False, session_id="sess9"))
    assert not ev.get("failed")
    assert "本会话" in ev["summary"]
    # 只进工作记忆，不进语义库
    bm = bmem.BookMemory(book)
    assert any(c["text"] == "纸条必须一句话" for c in bm.get_working("sess9"))
    assert bm._load_nodes() == []


def test_remember_tool_conflict_card_summary(book: Path):
    ev1, _ = asyncio.run(ac._execute_chat_tool(
        "remember", {"key": "纸条", "content": "第一版：留饭"}, book, None, {}, dry_run=False))
    ev2, _ = asyncio.run(ac._execute_chat_tool(
        "remember", {"key": "纸条", "content": "第二版：尸体在床上"}, book, None, {}, dry_run=False))
    assert "冲突卡" in ev2["summary"]
    bm = bmem.BookMemory(book)
    assert len(bm.all_cards(status="open")) == 1


def test_remember_tool_no_session_falls_back_book(book: Path):
    ev, _ = asyncio.run(ac._execute_chat_tool(
        "remember", {"content": "全书基调：冷", "scope": "session"},
        book, None, {}, dry_run=False, session_id=""))
    assert not ev.get("failed")
    bm = bmem.BookMemory(book)
    assert bm._load_nodes() and not any(bm.get_working("default"))


def test_forget_tool_v2(book: Path):
    asyncio.run(ac._execute_chat_tool(
        "remember", {"key": "旧设定", "content": "主角独臂"}, book, None, {}, dry_run=False))
    ev, _ = asyncio.run(ac._execute_chat_tool(
        "forget", {"key": "旧设定"}, book, None, {}, dry_run=False))
    assert not ev.get("failed")
    bm = bmem.BookMemory(book)
    assert all(n["status"] != "active" for n in bm._load_nodes())


# ── 兼容层 CRUD（面板端点零改动走 v2）──

def test_compat_crud_roundtrip(book: Path):
    item = ac.add_memory(book, "文风要冷硬", key="文风")
    assert item["text"] == "文风要冷硬" and item["scope"] == "book"
    # update → 版本链追加 + 不产卡
    assert ac.update_memory(book, item["id"], "文风要冷硬，句子短")
    items = ac.load_memory(book)
    assert len(items) == 1 and items[0]["text"] == "文风要冷硬，句子短"
    assert len(items[0]["node_id"] or "") > 0
    # delete → 归档
    assert ac.delete_memory(book, item["id"])
    assert ac.load_memory(book) == []
    # 账本可溯：节点还在（archived）
    bm = bmem.BookMemory(book)
    assert len(bm._load_nodes()) == 1


def test_memory_tiers_shape(book: Path):
    ac.add_memory(book, "全局规则A", key="ga")
    ac.add_memory(book, "本弧规则B", scope="arc", arc_id="arc1", key="gb")
    bm = bmem.BookMemory(book)
    bm.add_working("sessX", "会话约束C")
    t = ac.memory_tiers(book, arc_id="arc1", session_id="sessX")
    assert [i["text"] for i in t["global"]] == ["全局规则A"]
    assert [i["text"] for i in t["arc"]] == ["本弧规则B"]
    assert [c["text"] for c in t["session"]] == ["会话约束C"]
    assert t["stats"]["nodes"] == 2
    assert t["cards"] == []
