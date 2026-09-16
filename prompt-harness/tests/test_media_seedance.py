"""Seedance 视频驱动测试：draft 控本档位 / 提交-轮询-超时 / 产物入库 / 记账（不真实外呼）。"""
from __future__ import annotations

from pathlib import Path

import pytest

from prompt_harness.media.ark_runner import RunResult
from prompt_harness.media.asset_store import AssetStore
from prompt_harness.media.drivers.base import STATUS_DONE, STATUS_SKIPPED
from prompt_harness.media.drivers.seedance import (
    DRAFT_MAX_DURATION,
    DRAFT_PRICE_PER_SECOND,
    DRAFT_RESOLUTION,
    QUALITY_PRICE_PER_SECOND,
    QUALITY_RESOLUTION,
    SeedanceDriver,
    extract_status,
    extract_task_id,
    normalize_status,
)
from prompt_harness.media.usage_ledger import UsageLedger


class ScriptedRunner:
    """脚本化假 runner：按序返回响应（耗尽后重复最后一条，供忙等超时场景）；
    on_call 可在调用时伪造产物落盘（模拟 gen get 自动下载）。"""

    def __init__(self, responses, on_call=None, available=True):
        self.responses = list(responses)
        self.on_call = on_call
        self._available = available
        self.calls = []
        self._last = None

    def available(self):
        return (True, "") if self._available else (False, "找不到 arkcli")

    def run(self, args, timeout=60):
        self.calls.append(list(args))
        if self.on_call is not None:
            self.on_call(list(args))
        if self.responses:
            self._last = self.responses.pop(0)
        return self._last


def _driver(tmp_path, runner, **kw):
    store = kw.pop("store", None)
    return SeedanceDriver(
        runner=runner, store=store, save_dir=tmp_path / "clips",
        poll_interval=0.0, max_wait=kw.pop("max_wait", 5.0),
        sleep_fn=lambda s: None, **kw,
    )


def _noop_sleep(_s):
    return None


# --------------------------------------------------------------------- manifest / 降级


def test_manifest_declares_cost_items():
    m = SeedanceDriver.manifest.to_dict()
    assert "text2video" in m["capabilities"]
    items = {x["item"] for x in m["cost_items"]}
    assert items == {"video_draft", "video_quality"}
    assert all(x["unit"] == "秒" for x in m["cost_items"])
    assert any("seedance" in s.lower() for s in m["permissions"]["spends"])


def test_skipped_without_runner(monkeypatch):
    monkeypatch.setenv("ARK_API_KEY", "k")
    driver = SeedanceDriver(runner=None)
    result = driver.run({"prompt": "镜头缓缓推近纸人教室"})
    assert result.status == STATUS_SKIPPED
    assert "runner" in result.reason


def test_skipped_without_credentials(monkeypatch):
    for key in ("ARK_API_KEY", "VOLC_INIT_ACCESS_KEY", "VOLC_INIT_SECRET_KEY"):
        monkeypatch.delenv(key, raising=False)
    driver = SeedanceDriver(runner=ScriptedRunner([]))
    result = driver.run({"prompt": "镜头"})
    assert result.status == STATUS_SKIPPED
    assert "认证" in result.reason
    assert "视觉" in result.reason  # reason 说明 Coding Plan key 无视觉权限


def test_skipped_records_zero_cost(tmp_path, monkeypatch):
    monkeypatch.setenv("ARK_API_KEY", "k")
    ledger = UsageLedger(tmp_path / "ledger")
    driver = SeedanceDriver(runner=ScriptedRunner([], available=False), ledger=ledger)
    result = driver.run({"prompt": "镜头"})
    assert result.status == STATUS_SKIPPED
    row = ledger.read_lines()[0]
    assert row["driver"] == "seedance" and row["status"] == "skipped" and row["est_cost"] == 0.0


def test_missing_prompt_failed(monkeypatch):
    monkeypatch.setenv("ARK_API_KEY", "k")
    driver = SeedanceDriver(runner=ScriptedRunner([]))
    assert driver.run({}).status == "failed"


# --------------------------------------------------------------------- draft 控本档位


def test_draft_preset_caps_cost(monkeypatch, tmp_path):
    """draft：--draft 旗标 + 时长封顶 5s + 480p（请求给 30s 也压回 5s）。"""
    monkeypatch.setenv("ARK_API_KEY", "k")
    runner = ScriptedRunner([RunResult(ok=True, status="done", parsed_json={"id": "cgtv-t1"})])
    driver = _driver(tmp_path, runner)
    preset = driver.resolve_preset({"duration": 30})
    assert preset["draft"] is True and preset["duration"] == DRAFT_MAX_DURATION
    assert preset["resolution"] == DRAFT_RESOLUTION
    driver.run({"prompt": "镜头", "duration": 30, "wait": False})
    args = runner.calls[0]
    assert "--draft" in args
    assert args[args.index("--duration") + 1] == str(DRAFT_MAX_DURATION)
    assert args[args.index("--resolution") + 1] == DRAFT_RESOLUTION
    assert "--no-open" in args  # 无人值守不弹播放器


def test_quality_preset_for_whitelist_shots(monkeypatch, tmp_path):
    monkeypatch.setenv("ARK_API_KEY", "k")
    runner = ScriptedRunner([RunResult(ok=True, status="done", parsed_json={"id": "cgtv-t2"})])
    driver = _driver(tmp_path, runner)
    preset = driver.resolve_preset({"mode": "quality"})
    assert preset["draft"] is False and preset["resolution"] == QUALITY_RESOLUTION
    driver.run({"prompt": "镜头", "mode": "quality", "wait": False})
    args = runner.calls[0]
    assert "--draft" not in args
    assert args[args.index("--resolution") + 1] == QUALITY_RESOLUTION


# --------------------------------------------------------------------- 提交/轮询/超时


def _success_poll_parsed():
    return {"status": "succeeded", "video_url": "https://cdn.example/v.mp4"}


def test_submit_poll_success_enters_store(tmp_path, monkeypatch):
    monkeypatch.setenv("ARK_API_KEY", "k")
    clips = tmp_path / "clips"
    store = AssetStore(tmp_path / "lib")
    store.register("clip_sh001", "video", used_by=["n001"])

    def on_call(args):
        # 模拟 `gen get` succeeded 时自动把产物下载到 --save-to
        if args[0] == "gen":
            (clips / "cgtv-t3.mp4").write_bytes(b"fake mp4 bytes")

    runner = ScriptedRunner(
        [
            RunResult(ok=True, status="done", parsed_json={"id": "cgtv-t3"}),
            RunResult(ok=True, status="done", parsed_json={"status": "running"}),
            RunResult(ok=True, status="done", parsed_json=_success_poll_parsed()),
        ],
        on_call=on_call,
    )
    ledger = UsageLedger(tmp_path / "ledger")
    driver = SeedanceDriver(
        runner=runner, store=store, save_dir=clips,
        poll_interval=0.0, max_wait=5.0, sleep_fn=_noop_sleep, ledger=ledger,
    )
    result = driver.run({"prompt": "教室全景缓推", "entry_asset_id": "clip_sh001", "pack_id": "pack_demo"})
    assert result.ok and result.status == STATUS_DONE
    out = result.outputs[0]
    assert out["kind"] == "video" and out["task_id"] == "cgtv-t3"
    assert out["bytes"] == len(b"fake mp4 bytes")
    entry = store.get_entry("clip_sh001")
    assert entry.status == "done" and entry.content_id == out["content_id"]
    assert store.get_bytes("clip_sh001") == b"fake mp4 bytes"
    # 提交 + 2 次轮询
    assert runner.calls[0][0] == "+gen"
    assert runner.calls[1][:3] == ["gen", "get", "cgtv-t3"]
    # 记账：draft 5 秒 × 单价
    row = ledger.read_lines()[0]
    assert row["status"] == "done" and row["quantity"] == 5.0 and row["unit"] == "秒"
    assert row["est_cost"] == pytest.approx(DRAFT_PRICE_PER_SECOND * 5)
    assert row["asset_id"] == "clip_sh001" and row["pack"] == "pack_demo"


def test_poll_timeout_returns_task_id_for_resume(tmp_path, monkeypatch):
    monkeypatch.setenv("ARK_API_KEY", "k")
    runner = ScriptedRunner(
        [RunResult(ok=True, status="done", parsed_json={"id": "cgtv-t4"})]
        + [RunResult(ok=True, status="done", parsed_json={"status": "running"})] * 50
    )
    driver = SeedanceDriver(
        runner=runner, save_dir=tmp_path / "clips",
        poll_interval=0.0, max_wait=0.05, sleep_fn=_noop_sleep,
    )
    result = driver.run({"prompt": "镜头"})
    assert result.status == "failed"
    assert "cgtv-t4" in result.reason  # 断点续跑凭据
    assert "gen get" in result.reason


def test_task_failed_status_maps_to_failed(tmp_path, monkeypatch):
    monkeypatch.setenv("ARK_API_KEY", "k")
    store = AssetStore(tmp_path / "lib")
    store.register("clip_x", "video")
    runner = ScriptedRunner(
        [
            RunResult(ok=True, status="done", parsed_json={"id": "cgtv-t5"}),
            RunResult(ok=True, status="done", parsed_json={"status": "failed", "error": "content blocked"}),
        ]
    )
    driver = SeedanceDriver(
        runner=runner, store=store, save_dir=tmp_path / "clips",
        poll_interval=0.0, max_wait=5.0, sleep_fn=_noop_sleep,
    )
    result = driver.run({"prompt": "镜头", "entry_asset_id": "clip_x"})
    assert result.status == "failed"
    assert "failed" in result.reason
    assert store.get_entry("clip_x").status == "failed"  # entry 状态机联动


def test_submit_failure_maps_permission_error(tmp_path, monkeypatch):
    monkeypatch.setenv("ARK_API_KEY", "k")
    runner = ScriptedRunner(
        [RunResult(ok=False, status="error", error="AccessDenied: model not authorized")]
    )
    driver = _driver(tmp_path, runner)
    result = driver.run({"prompt": "镜头"})
    assert result.status == "failed"
    assert "视觉" in result.reason


def test_wait_false_submit_only(tmp_path, monkeypatch):
    """批量排队模式：只提交返回 task_id，不轮询不计费。"""
    monkeypatch.setenv("ARK_API_KEY", "k")
    ledger = UsageLedger(tmp_path / "ledger")
    runner = ScriptedRunner([RunResult(ok=True, status="done", parsed_json={"task_id": "cgtv-t6"})])
    driver = SeedanceDriver(
        runner=runner, save_dir=tmp_path / "clips",
        poll_interval=0.0, max_wait=5.0, sleep_fn=_noop_sleep, ledger=ledger,
    )
    result = driver.run({"prompt": "镜头", "wait": False})
    assert result.ok
    assert result.outputs[0]["task_id"] == "cgtv-t6"
    assert result.outputs[0]["pending"] is True
    assert len(runner.calls) == 1  # 只提交
    row = ledger.read_lines()[0]
    assert row["capability"] == "text2video_submit" and row["est_cost"] == 0.0


def test_quality_price_differs_from_draft():
    assert QUALITY_PRICE_PER_SECOND > DRAFT_PRICE_PER_SECOND


# --------------------------------------------------------------------- 解析工具


def test_extract_task_id_tolerant_shapes():
    assert extract_task_id({"id": "cgtv-2026-1"}) == "cgtv-2026-1"
    assert extract_task_id({"data": {"task_id": "cgtv-2026-2"}}) == "cgtv-2026-2"
    assert extract_task_id({"resp": {"id": "abc123"}}) == "abc123"  # 非前缀兜底
    assert extract_task_id({"id": None}) is None
    assert extract_task_id("not a dict") is None


def test_normalize_status():
    assert normalize_status("Succeeded") == "succeeded"
    assert normalize_status("running") == "running"
    assert normalize_status("FAILED") == "failed"
    assert normalize_status(None) == "unknown"
    assert normalize_status("cancelled") == "cancelled"


def test_extract_status_nested():
    assert extract_status({"task": {"status": "succeeded"}}) == "succeeded"
    assert extract_status({}) is None
