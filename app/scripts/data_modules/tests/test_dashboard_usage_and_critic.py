"""Tier-A 单元测试：usage 计费 + critic 窗口切片 + agent 事件接线。"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

PLUGIN_ROOT = Path(__file__).resolve().parents[3]
if str(PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT))

from dashboard.usage import UsageTracker  # noqa: E402
import dashboard.usage as usage_mod  # noqa: E402


# ---------------------------------------------------------------------------
# UsageTracker
# ---------------------------------------------------------------------------

def test_usage_record_and_summary():
    tr = UsageTracker()
    tr.record(model="m", agent="context",
              usage={"input_tokens": 100, "output_tokens": 50, "cache_read_input_tokens": 10})
    tr.record(model="m", agent="context", usage={"input_tokens": 200, "output_tokens": 30})
    s = tr.summary(window_seconds=3600, recent=10)
    assert s["calls_in_window"] == 2
    assert s["totals"]["input_tokens"] == 300
    assert s["totals"]["output_tokens"] == 80
    assert s["totals"]["total_tokens"] == 380
    assert s["totals"]["cache_read_input_tokens"] == 10
    assert s["by_agent"]["context"]["calls"] == 2
    assert len(s["recent"]) == 2
    assert s["recent"][-1]["total_tokens"] == 230


def test_usage_record_none_usage():
    tr = UsageTracker()
    tr.record(model="m", agent="draft", usage=None)
    s = tr.summary(window_seconds=3600)
    assert s["calls_in_window"] == 1
    assert s["totals"]["total_tokens"] == 0


def test_usage_window_excludes_old(monkeypatch):
    tr = UsageTracker()
    monkeypatch.setattr(usage_mod.time, "time", lambda: 1000.0)
    tr.record(model="m", agent="a", usage={"input_tokens": 1000, "output_tokens": 0})
    monkeypatch.setattr(usage_mod.time, "time", lambda: 2000.0)
    tr.record(model="m", agent="a", usage={"input_tokens": 5, "output_tokens": 5})
    # 500s 窗口从 2000 起 → 只含第二条
    s = tr.summary(window_seconds=500, recent=10)
    assert s["calls_in_window"] == 1
    assert s["totals"]["input_tokens"] == 5
    assert s["totals"]["total_tokens"] == 10


def test_usage_caps_max_records():
    tr = UsageTracker(max_records=4)
    for i in range(10):
        tr.record(model="m", agent="a", usage={"input_tokens": i, "output_tokens": 0})
    s = tr.summary(window_seconds=3600, recent=100)
    # 不超过上限
    assert len(s["recent"]) <= 4
    assert s["calls_in_window"] <= 4


# ---------------------------------------------------------------------------
# _slice_windows（不依赖 anthropic 的纯函数？— 在 workflows 模块里，需 anthropic）
# ---------------------------------------------------------------------------

def test_slice_windows_basic():
    pytest.importorskip("openai")
    from dashboard.workflows import _slice_windows
    text = "甲。乙。丙。" + "x" * 300 + "。"
    ws = _slice_windows(text, size=50, max_windows=5)
    assert len(ws) >= 1
    assert all(isinstance(w, str) and w for w in ws)


def test_slice_windows_caps_max():
    pytest.importorskip("openai")
    from dashboard.workflows import _slice_windows
    text = "句。" * 100
    ws = _slice_windows(text, size=2, max_windows=3)
    assert len(ws) == 3


def test_slice_windows_empty():
    pytest.importorskip("openai")
    from dashboard.workflows import _slice_windows
    assert _slice_windows("") == []
    assert _slice_windows("   ") == []


# ---------------------------------------------------------------------------
# agent 事件接线 + usage 解析
# ---------------------------------------------------------------------------

def test_make_task_emitters_pushes_to_task():
    pytest.importorskip("openai")
    from dashboard.agents import make_task_emitters
    from dashboard.task_manager import TaskManager

    tm = TaskManager()
    t = tm.create(kind="agent", label="x")
    on_event, on_token = make_task_emitters(t, "context")

    async def _run():
        await on_event({"phase": "token", "text": "hi"})
        await on_token("hi")  # no-op，不应产生事件

    asyncio.run(_run())
    assert len(t.events) == 1
    assert t.events[0]["text"] == "hi"


def test_usage_to_dict_helpers():
    pytest.importorskip("openai")
    from dashboard.agent_runner import _usage_to_dict

    assert _usage_to_dict(None) == {}

    class _U:
        input_tokens = 10
        output_tokens = 5
        cache_read_input_tokens = 2
        cache_creation_input_tokens = 1

    d = _usage_to_dict(_U())
    assert d["input_tokens"] == 10
    assert d["output_tokens"] == 5
    assert d["cache_read_input_tokens"] == 2
    assert d["cache_creation_input_tokens"] == 1


def test_critic_windows_skips_on_empty_draft():
    """空草稿 → 无窗口 → 发 skipped 事件、返回 None（不调 API）。"""
    pytest.importorskip("openai")
    from dashboard.workflows import _run_critic_windows
    from dashboard.task_manager import TaskManager

    tm = TaskManager()
    t = tm.create(kind="workflow", label="x")

    async def _run():
        return await _run_critic_windows(
            t, project_root=Path("."),
            draft_text="", chapter=1, model=None, fast=False,
        )

    res = asyncio.run(_run())
    assert res is None
    assert any(
        e.get("step") == "critic" and e.get("status") == "skipped"
        for e in t.events
    )


def test_critic_windows_fast_returns_none():
    pytest.importorskip("openai")
    from dashboard.workflows import _run_critic_windows
    from dashboard.task_manager import TaskManager

    tm = TaskManager()
    t = tm.create(kind="workflow", label="x")

    async def _run():
        return await _run_critic_windows(
            t, project_root=Path("."),
            draft_text="甲。乙。", chapter=1, model=None, fast=True,
        )

    res = asyncio.run(_run())
    assert res is None
