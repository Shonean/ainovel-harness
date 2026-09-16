"""quick_init 单测：量产快速表单 → 基本设定 + 待审卡片（不自动批准）。

LLM 调用全部 monkeypatch，不打网络。
"""
from __future__ import annotations

import asyncio

from prompt_harness import ai_creation as ac


def _fake_prop():
    return {
        "style": "冷峻克制",
        "role_setting": "主角与循环世界观",
        "elements": {
            "characters": [{"id": "c1", "name": "陈守念", "alias": ["守念"],
                            "desc": "主角", "fields": [], "relations": []}],
            "items": [{"id": "i1", "name": "病人卡", "alias": [],
                       "desc": "病床号48", "fields": [], "relations": []}],
            "settings": [{"id": "s1", "name": "时间循环", "alias": [],
                          "desc": "一周循环", "fields": [], "relations": []}],
        },
    }


def _patch(monkeypatch):
    async def fake_prop(s, title, genre):
        return _fake_prop()

    async def fake_files(book_root, s, elements, title, genre):
        assert elements["characters"][0]["name"] == "陈守念"
        return ["世界观.md"]

    monkeypatch.setattr(ac, "_propose_elements_and_tone", fake_prop)
    monkeypatch.setattr(ac, "_write_setting_files", fake_files)


def test_quick_init_creates_pending_cards_not_elements(tmp_path, monkeypatch):
    _patch(monkeypatch)

    r = asyncio.run(ac.quick_init(
        tmp_path, title="原料书", genre="恐怖", protagonist="陈守念",
        style="压抑", one_liner="循环校园", target_chapters=30))

    assert r["ok"] is True
    assert r["counts"] == {"characters": 1, "items": 1, "settings": 1}
    assert len(r["cards"]) == 3
    assert r["setting_files"] == ["世界观.md"]
    assert r["target_chapters"] == 30
    assert r["pending_count"] == 3

    pend = ac.load_pending(tmp_path)
    assert {p["kind"] for p in pend} == {"characters", "items", "settings"}
    assert all(p["type"] == "card" for p in pend)

    # 出卡不自动批准：elements.json 不落盘
    assert not (tmp_path / ".ainovel" / "elements.json").is_file()

    s = ac.load_basic_settings(tmp_path)
    assert s["name"] == "原料书"
    assert s["protagonist"]["name"] == "陈守念"
    assert s["style"] == "冷峻克制"
    assert s["role_setting"] == "主角与循环世界观"


def test_quick_init_approve_all_writes_elements(tmp_path, monkeypatch):
    _patch(monkeypatch)
    asyncio.run(ac.quick_init(tmp_path, title="原料书", protagonist="陈守念"))

    n = ac.approve_all_pending(tmp_path)
    assert n == 3
    assert ac.load_pending(tmp_path) == []

    els = ac.load_elements(tmp_path)
    names = {e["name"] for e in els["characters"]}
    assert "陈守念" in names
    assert "病人卡" in {e["name"] for e in els["items"]}
    assert "时间循环" in {e["name"] for e in els["settings"]}


def test_quick_init_requires_title(tmp_path):
    r = asyncio.run(ac.quick_init(tmp_path, title=""))
    assert r["ok"] is False
    assert "书名" in r["error"]
