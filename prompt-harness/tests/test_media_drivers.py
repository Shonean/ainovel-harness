"""媒体驱动单元测试：manifest 形状 + 各驱动降级路径（不真实外呼）。"""
from __future__ import annotations

import os
from pathlib import Path

from prompt_harness.media.ark_runner import RunResult
from prompt_harness.media.asset_store import AssetStore
from prompt_harness.media.drivers.base import (
    STATUS_DONE,
    STATUS_NOT_INSTALLED,
    STATUS_NOT_IMPLEMENTED,
    STATUS_SKIPPED,
    BaseDriver,
    DriveResult,
    DriverManifest,
)
from prompt_harness.media.drivers.cloud_tts import (
    STREAM_EVENT_TYPES,
    CloudTTSDriver,
    build_request,
)
from prompt_harness.media.drivers.kokoro import (
    INSTALL_HINT,
    NEUTRAL_VOICE,
    VOICE_TABLE,
    KokoroDriver,
)
from prompt_harness.media.drivers.seedream import SeedreamDriver


# --------------------------------------------------------------------- base


def test_manifest_and_driverresult_shape():
    m = DriverManifest(
        name="x",
        version="0.1",
        capabilities=["t"],
        params={"a": "b"},
        permissions={"write_paths": [], "network": [], "spends": []},
        cost_items=[{"item": "image", "unit": "张"}],
    )
    d = m.to_dict()
    # 总计划 §2.3：能力/参数/权限/成本项四件套
    assert set(d) == {"name", "version", "capabilities", "params", "permissions", "cost_items"}
    assert DriveResult.done([{"path": "x"}]).status == STATUS_DONE
    assert DriveResult.skipped("r").status == STATUS_SKIPPED
    assert DriveResult.not_installed("r").status == STATUS_NOT_INSTALLED
    assert DriveResult.failed("r").status == "failed"
    assert isinstance(BaseDriver(), BaseDriver)


# --------------------------------------------------------------------- seedream


class FakeRunner:
    """注入用假 runner：available/run 可控。"""

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


def test_seedream_skipped_without_runner(monkeypatch, tmp_path):
    monkeypatch.setenv("ARK_API_KEY", "k")
    driver = SeedreamDriver(runner=None)
    result = driver.run({"prompt": "教室"})
    assert result.status == STATUS_SKIPPED
    assert "runner" in result.reason


def test_seedream_skipped_without_credentials(monkeypatch, tmp_path):
    for key in ("ARK_API_KEY", "VOLC_INIT_ACCESS_KEY", "VOLC_INIT_SECRET_KEY"):
        monkeypatch.delenv(key, raising=False)
    driver = SeedreamDriver(runner=FakeRunner())
    result = driver.run({"prompt": "教室"})
    assert result.status == STATUS_SKIPPED
    assert "认证" in result.reason


def test_seedream_skipped_when_runner_unavailable(monkeypatch):
    monkeypatch.setenv("ARK_API_KEY", "k")
    driver = SeedreamDriver(runner=FakeRunner(available=False))
    result = driver.run({"prompt": "教室"})
    assert result.status == STATUS_SKIPPED
    assert "不可用" in result.reason


def test_seedream_success_links_store(tmp_path, monkeypatch):
    monkeypatch.setenv("ARK_API_KEY", "k")
    png = tmp_path / "out.png"
    png.write_bytes(b"\x89PNG fake image bytes")
    store = AssetStore(tmp_path / "lib")
    store.register("bg_classroom_night", "background")
    runner = FakeRunner(parsed={"outputs": [{"path": str(png)}]})
    driver = SeedreamDriver(runner=runner, store=store)
    result = driver.run({"prompt": "深夜旧教室", "entry_asset_id": "bg_classroom_night"})
    assert result.ok and result.status == STATUS_DONE
    out = result.outputs[0]
    assert out["content_id"] == store.get_entry("bg_classroom_night").content_id
    assert Path(out["path"]).read_bytes() == b"\x89PNG fake image bytes"
    assert store.get_entry("bg_classroom_night").status == "done"
    assert runner.calls[0][:4] == ["+gen", "--model", driver.model, "--size"]


def test_seedream_maps_permission_error(monkeypatch):
    monkeypatch.setenv("ARK_API_KEY", "k")
    driver = SeedreamDriver(runner=FakeRunner(ok=False, error="AccessDenied: model not authorized"))
    result = driver.run({"prompt": "教室"})
    assert result.status == "failed"
    assert "视觉" in result.reason  # 明确提示需要开通视觉模型权限


def test_seedream_missing_prompt(monkeypatch):
    monkeypatch.setenv("ARK_API_KEY", "k")
    driver = SeedreamDriver(runner=FakeRunner())
    assert driver.run({}).status == "failed"


# --------------------------------------------------------------------- kokoro


def test_kokoro_voice_table_placeholder():
    # 音色表占位：中性音色必须存在且映射到具体 kokoro 音色 id
    assert NEUTRAL_VOICE == VOICE_TABLE["protagonist"]["kokoro"]
    assert set(VOICE_TABLE) >= {"narrator", "protagonist", "default"}
    assert KokoroDriver.resolve_voice("protagonist") == NEUTRAL_VOICE
    assert KokoroDriver.resolve_voice(None) == NEUTRAL_VOICE
    assert KokoroDriver.resolve_voice("zm_yunyang") == "zm_yunyang"  # 直接给 id 原样透传


def test_kokoro_unavailable_path(tmp_path):
    from prompt_harness.media.drivers.kokoro import _module_installed

    driver = KokoroDriver()
    installed = all(_module_installed(m) for m in ("torch", "kokoro"))
    result = driver.synthesize("这是哪儿？", out_path=str(tmp_path / "v.wav"))
    if installed:  # 本机若已装齐则走推理路径，此处只做冒烟
        assert result.status in ("done", "failed")
        return
    assert result.status == STATUS_NOT_INSTALLED
    # 安装指引必须可执行：含 pip 安装命令与 espeak-ng Windows 说明
    assert "pip install" in result.reason and "espeak-ng" in INSTALL_HINT
    assert not (tmp_path / "v.wav").exists()  # 未安装绝不写文件


def test_kokoro_missing_text():
    driver = KokoroDriver()
    result = driver.run({"text": "  "})
    assert result.status == "failed"


# --------------------------------------------------------------------- cloud_tts


def test_cloud_tts_build_request_shape():
    req = build_request(
        "你终于把它捡起来了。",
        "zh_male_M392_congsong",
        appid="APP-1",
        token="TOKEN-1",
        reqid="fixed-reqid",
    )
    assert set(req) == {"app", "user", "audio", "request"}
    assert req["app"] == {"appid": "APP-1", "token": "TOKEN-1", "cluster": "volcano_tts"}
    assert req["audio"]["encoding"] == "mp3" and req["audio"]["sample_rate"] == 24000
    assert req["request"]["operation"] == "ssup"  # 流式二进制协议
    assert req["request"]["reqid"] == "fixed-reqid"


def test_cloud_tts_skipped_without_key(monkeypatch):
    monkeypatch.delenv("VOLC_TTS_APPID", raising=False)
    monkeypatch.delenv("VOLC_TTS_ACCESS_TOKEN", raising=False)
    driver = CloudTTSDriver()
    events = []
    result = driver.synthesize_stream("文本", "voice", on_event=events.append)
    assert result.status == STATUS_SKIPPED
    assert "VOLC_TTS_APPID" in result.reason
    assert events[-1]["type"] == "error"  # 回调约定：失败事件必达


def test_cloud_tts_stub_never_calls_network(monkeypatch):
    # 即使配置了 key，stub 阶段也不外呼：显式 not_implemented
    monkeypatch.setenv("VOLC_TTS_APPID", "app")
    monkeypatch.setenv("VOLC_TTS_ACCESS_TOKEN", "tok")
    driver = CloudTTSDriver()
    assert driver.available() == (True, "")
    result = driver.synthesize("文本", "voice")
    assert result.status == STATUS_NOT_IMPLEMENTED
    assert "未接入" in result.reason
    assert STREAM_EVENT_TYPES == ("start", "audio", "end", "error")
