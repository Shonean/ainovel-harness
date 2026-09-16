"""ark 控制台（T36）单元测试：任务持久化 / 重试 / 取消 / 预算 / 账本剩余。

不触网：驱动 available 一律 mock 为降级。
运行：py -m pytest prompt-harness/tests/test_ark_console.py -q
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_PH = Path(__file__).resolve().parent.parent
if str(_PH) not in sys.path:
    sys.path.insert(0, str(_PH))

from prompt_harness import ark_console as ac  # noqa: E402
from prompt_harness.media.usage_ledger import UsageLedger  # noqa: E402


@pytest.fixture()
def console(tmp_path, monkeypatch):
    from prompt_harness.media.drivers.seedream import SeedreamDriver
    from prompt_harness.media.drivers.seedance import SeedanceDriver

    monkeypatch.setattr(SeedreamDriver, "available", lambda self: (False, "测试降级"))
    monkeypatch.setattr(SeedanceDriver, "available", lambda self: (False, "测试降级"))
    root = tmp_path / "lib"
    root.mkdir()
    monkeypatch.setattr(ac, "default_asset_library_root", lambda: root)
    monkeypatch.setattr(ac, "budget_path", lambda: root / "budget.json")
    ledger = UsageLedger(root / "ledger")
    monkeypatch.setattr(ac, "media_ledger", lambda: ledger)
    return ac.ArkConsole(root=root / "ark", ledger=ledger)


def test_submit_persists_and_lists(console):
    r = console.submit({"kind": "image", "prompt": "深夜教室", "label": "生图"})
    assert r["ok"] is True
    tid = r["task"]["id"]
    t = console.get(tid)
    assert t["status"] in ("skipped", "not_installed")  # 无凭据 → 诚实降级
    assert t["cost_charged"] == 0.0 and t["reason"]
    assert console.tasks_path.is_file()
    assert any(x["id"] == tid for x in console.list_tasks())


def test_retry_creates_new_task(console):
    first = console.submit({"kind": "image", "prompt": "x", "label": "t"})
    t1 = console.get(first["task"]["id"])
    r2 = console.retry(t1["id"])
    assert r2["ok"] is True and r2["task"]["id"] != t1["id"]
    assert r2["task"]["prompt"] == "x"
    # 成功任务不可重试
    ok_task = dict(t1)
    ok_task["id"] = "task_done_demo"
    ok_task["status"] = "succeeded"
    data = console.load()
    data["tasks"].append(ok_task)
    console.save(data)
    r3 = console.retry("task_done_demo")
    assert r3["ok"] is False


def test_cancel_sets_status(console):
    data = console.load()
    data["tasks"].append({
        "id": "task_x", "status": "queued", "kind": "image", "prompt": "p",
        "created_at": ac.now_iso(), "updated_at": ac.now_iso(), "ctx": "drama",
    })
    console.save(data)
    r = console.cancel("task_x")
    assert r["ok"] is True and r["task"]["status"] == "cancelled"
    assert console.cancel("task_x")["ok"] is False  # 终态不可再取消


def test_budget_read_write(console):
    bud = ac.read_budget()
    assert bud["image_month_cny"] == 200.0 and bud["enabled"] is True
    ac.write_budget({"video_month_cny": 88, "enabled": False})
    bud2 = ac.read_budget()
    assert bud2["video_month_cny"] == 88.0 and bud2["enabled"] is False


def test_usage_remaining_from_ledger(console):
    console.ledger.record("seedream", "text2image", status="done", quantity=1, unit="张", est_cost=0.2)
    console.ledger.record("seedance", "text2video", status="skipped", quantity=0, unit="秒", est_cost=0.0)
    u = console.usage()
    assert u["summary"]["total_calls"] == 2
    assert u["remaining"]["image_month_cny"] == round(200.0 - 0.2, 4)
    assert u["remaining"]["video_month_cny"] == 120.0
    assert u["spent_by_driver"]["seedream"]["cny"] == 0.2
