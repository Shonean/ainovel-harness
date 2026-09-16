"""用量账本测试：jsonl 行形状 + 汇总聚合 + 驱动钩子（FakeRunner 场景）。"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from prompt_harness.media import usage_ledger
from prompt_harness.media.drivers.seedream import SEEDREAM_PRICE_PER_IMAGE, SeedreamDriver
from prompt_harness.media.usage_ledger import ENV_LEDGER_DIR, UsageLedger
from prompt_harness.media.ark_runner import RunResult


class FakeRunner:
    """注入用假 runner：available/run 可控（与 test_media_drivers 同款）。"""

    def __init__(self, ok=True, parsed=None, error="", status="done", available=True):
        self.ok = ok
        self.parsed = parsed
        self.error = error
        self.status = status
        self._available = available
        self.calls = []

    def available(self):
        return (True, "") if self._available else (False, "找不到 arkcli")

    def run(self, args, timeout=60):
        self.calls.append(list(args))
        return RunResult(
            ok=self.ok,
            status=self.status,
            parsed_json=self.parsed,
            raw="" if self.ok else self.error,
            error=self.error,
            cmd=["arkcli"] + list(args),
        )


@pytest.fixture(autouse=True)
def _reset_default_ledger(monkeypatch):
    """每个用例前清默认账本状态，避免全局缓存串场。"""
    monkeypatch.setattr(usage_ledger, "_default", None)
    yield
    monkeypatch.setattr(usage_ledger, "_default", None)


def test_record_appends_jsonl_line(tmp_path):
    lg = UsageLedger(tmp_path / "media")
    entry = lg.record(
        "seedance", "text2video",
        status="done", quantity=5.0, unit="秒", est_cost=0.75,
        pack="pack_demo", asset_id="clip_sh001", model="doubao-seedance-1-5-pro-251215",
    )
    path = lg.path_for()
    assert path.exists() and path.suffix == ".jsonl"
    line = json.loads(path.read_text(encoding="utf-8").splitlines()[0])
    # 对齐 llm_client 账本风格：ts/驱动/能力/状态/数量/估算成本/来源 pack
    for key in ("ts", "driver", "capability", "status", "quantity", "unit", "est_cost", "currency", "pack", "asset_id", "model"):
        assert key in line
    assert line["driver"] == "seedance" and line["status"] == "done"
    assert line["quantity"] == 5.0 and line["unit"] == "秒"
    assert line["est_cost"] == 0.75 and line["currency"] == "cny"
    assert line["pack"] == "pack_demo"
    assert entry["ts"].endswith("+08:00")


def test_summarize_by_driver_and_by_day(tmp_path):
    lg = UsageLedger(tmp_path / "media")
    lg.record("seedance", "text2video", status="done", quantity=5.0, unit="秒", est_cost=0.75)
    lg.record("seedance", "text2video", status="failed", quantity=0.0, unit="秒", est_cost=0.0)
    lg.record("seedream", "text2image", status="done", quantity=2.0, unit="张", est_cost=0.4)
    summary = lg.summarize()
    assert summary["total_calls"] == 3
    sd = summary["by_driver"]["seedance"]
    assert sd["calls"] == 2 and sd["done_calls"] == 1 and sd["not_done_calls"] == 1
    assert sd["quantity"] == {"秒": 5.0}          # failed 不计数量
    assert sd["cost"] == {"cny": pytest.approx(0.75)}
    sm = summary["by_driver"]["seedream"]
    assert sm["quantity"] == {"张": 2.0}
    day = list(summary["by_day"].values())[0]
    assert day["calls"] == 3 and day["cost"]["cny"] == pytest.approx(1.15)


def test_summarize_separates_currencies(tmp_path):
    lg = UsageLedger(tmp_path / "media")
    lg.record("seedream", "text2image", status="done", quantity=1.0, unit="张", est_cost=0.03, currency="usd")
    lg.record("seedance", "text2video", status="done", quantity=5.0, unit="秒", est_cost=0.75, currency="cny")
    summary = lg.summarize()
    assert summary["by_driver"]["seedream"]["cost"] == {"usd": pytest.approx(0.03)}
    assert summary["by_driver"]["seedance"]["cost"] == {"cny": pytest.approx(0.75)}


def test_read_lines_skips_corrupt(tmp_path):
    lg = UsageLedger(tmp_path / "media")
    p = lg.path_for()
    p.parent.mkdir(parents=True)
    p.write_text('{"driver": "x"}\n{broken json\n\n', encoding="utf-8")
    rows = lg.read_lines()
    assert len(rows) == 1 and rows[0]["driver"] == "x"
    assert lg.summarize()["total_calls"] == 1


def test_default_ledger_env_gated(monkeypatch, tmp_path):
    # 未设置 env → None（测试/离线不隐式写账本）
    monkeypatch.delenv(ENV_LEDGER_DIR, raising=False)
    assert usage_ledger.default_ledger() is None
    # 设置 env → 启用并缓存
    monkeypatch.setenv(ENV_LEDGER_DIR, str(tmp_path / "env_ledger"))
    lg = usage_ledger.default_ledger()
    assert isinstance(lg, UsageLedger)
    assert usage_ledger.default_ledger() is lg  # 缓存


def test_seedream_success_writes_ledger_line(tmp_path, monkeypatch):
    monkeypatch.setenv("ARK_API_KEY", "k")
    png = tmp_path / "out.png"
    png.write_bytes(b"\x89PNG fake image bytes")
    ledger = UsageLedger(tmp_path / "ledger")
    driver = SeedreamDriver(runner=FakeRunner(parsed={"outputs": [{"path": str(png)}]}), ledger=ledger)
    result = driver.run({"prompt": "深夜旧教室", "entry_asset_id": "bg_x", "pack_id": "pack_demo"})
    assert result.ok
    rows = ledger.read_lines()
    assert len(rows) == 1
    row = rows[0]
    assert row["driver"] == "seedream" and row["capability"] == "text2image"
    assert row["status"] == "done" and row["quantity"] == 1.0 and row["unit"] == "张"
    assert row["est_cost"] == pytest.approx(SEEDREAM_PRICE_PER_IMAGE)
    assert row["asset_id"] == "bg_x" and row["pack"] == "pack_demo"
    assert row["model"] == driver.model


def test_seedream_skipped_writes_zero_cost_line(tmp_path, monkeypatch):
    for key in ("ARK_API_KEY", "VOLC_INIT_ACCESS_KEY", "VOLC_INIT_SECRET_KEY"):
        monkeypatch.delenv(key, raising=False)
    ledger = UsageLedger(tmp_path / "ledger")
    driver = SeedreamDriver(runner=FakeRunner(), ledger=ledger)
    result = driver.run({"prompt": "教室"})
    assert result.status == "skipped"
    row = ledger.read_lines()[0]
    assert row["status"] == "skipped"
    assert row["est_cost"] == 0.0  # 未执行不计费，但留痕可审计
    assert "认证" in row["meta"]["reason"]


def test_seedream_failed_writes_zero_cost_line(tmp_path, monkeypatch):
    monkeypatch.setenv("ARK_API_KEY", "k")
    ledger = UsageLedger(tmp_path / "ledger")
    driver = SeedreamDriver(runner=FakeRunner(ok=False, error="AccessDenied: not authorized"), ledger=ledger)
    result = driver.run({"prompt": "教室"})
    assert result.status == "failed"
    row = ledger.read_lines()[0]
    assert row["status"] == "failed" and row["est_cost"] == 0.0


def test_base_hook_silent_without_ledger(monkeypatch, tmp_path):
    """无账本（默认 env 未设）时钩子 no-op，绝不影响主流程。"""
    monkeypatch.delenv(ENV_LEDGER_DIR, raising=False)
    monkeypatch.setenv("ARK_API_KEY", "k")
    png = tmp_path / "out.png"
    png.write_bytes(b"x")
    driver = SeedreamDriver(runner=FakeRunner(parsed={"outputs": [{"path": str(png)}]}))
    result = driver.run({"prompt": "教室"})
    assert result.ok  # 不记账也能正常出图
