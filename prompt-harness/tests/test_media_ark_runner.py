"""ArkRunner 适配层测试：JSON 容错解析 + 真实 vendored 二进制 --version + 降级路径。"""
from __future__ import annotations

import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest

from prompt_harness.media.ark_runner import (
    ArkRunner,
    RunResult,
    extract_json,
)


def test_extract_json_variants():
    assert extract_json('{"ok": true}') == {"ok": True}
    # 日志在前、结果 JSON 在末尾（取最后一个可解析对象）
    assert extract_json('info line\n{"a":1}\nnoise\n{"a":2}') == {"a": 2}
    # 代码块包裹
    assert extract_json('```json\n{"b": [1,2]}\n```') == {"b": [1, 2]}
    assert extract_json("") is None
    assert extract_json("no json here") is None


def test_runner_not_found_when_no_candidates(tmp_path):
    runner = ArkRunner(
        executable=tmp_path / "missing.exe",
        search_paths=[tmp_path / "empty"],  # 空搜索域，屏蔽 vendored/PATH
    )
    ok, why = runner.available()
    assert not ok and "未找到" in why
    result = runner.run(["--version"])
    assert isinstance(result, RunResult)
    assert result.ok is False and result.status == "not_found"
    assert "vendor" in result.error or "vendor" in why or "安装" in result.error


def _write_cli(tmp_path: Path, body: str) -> Path:
    cli = tmp_path / "fake_cli.js"
    cli.write_text(textwrap.dedent(body), encoding="utf-8")
    return cli


@pytest.mark.skipif(shutil.which("node") is None, reason="node 不可用")
def test_runner_timeout(tmp_path):
    node = shutil.which("node")
    cli = _write_cli(tmp_path, """
        setTimeout(() => console.log('late'), 60000);
    """)
    runner = ArkRunner(executable=node, search_paths=[tmp_path], timeout_default=2)
    result = runner.run([str(cli)], timeout=2)
    assert result.status == "timeout" and result.ok is False


@pytest.mark.skipif(shutil.which("node") is None, reason="node 不可用")
def test_runner_parses_json_stdout(tmp_path):
    node = shutil.which("node")
    cli = _write_cli(tmp_path, """
        console.log('boot ok');
        console.log(JSON.stringify({ok: true, value: 42}));
    """)
    runner = ArkRunner(executable=node, search_paths=[tmp_path])
    result = runner.run([str(cli)])
    assert result.ok and result.status == "done"
    assert result.parsed_json == {"ok": True, "value": 42}
    assert "boot ok" in result.raw


@pytest.mark.skipif(shutil.which("node") is None, reason="node 不可用")
def test_runner_nonzero_exit_is_error(tmp_path):
    node = shutil.which("node")
    cli = _write_cli(tmp_path, """
        console.error('boom: AccessDenied');
        process.exit(3);
    """)
    runner = ArkRunner(executable=node, search_paths=[tmp_path])
    result = runner.run([str(cli)])
    assert result.ok is False and result.status == "error"
    assert "boom" in result.error


def test_runner_vendored_binary_real_version():
    """真实 vendored 二进制：third_party/ark-cli/bin/arkcli-*.exe --version（本地调用，不触平台 API）。"""
    runner = ArkRunner()
    ok, why = runner.available()
    if not ok:
        pytest.skip(f"vendored ark-cli 未就位：{why}")
    result = runner.run(["--version"], timeout=60)
    assert result.ok is True, result.error
    assert result.status == "done"
    assert "1.0.27" in result.raw or "arkcli" in result.raw


def test_env_override_executable(tmp_path, monkeypatch):
    exe = tmp_path / "envcli.cmd"
    exe.write_text("@echo off\r\n", encoding="utf-8")  # 仅需存在（is_file 命中）
    monkeypatch.setenv("AINOVEL_ARK_CLI", str(exe))
    runner = ArkRunner(search_paths=[tmp_path / "empty"])
    found = runner.find()
    assert found == [str(exe)]
    monkeypatch.delenv("AINOVEL_ARK_CLI")


def test_subprocess_devnull_implied():
    """去交互约定：run() 使用 stdin=DEVNULL；这里通过源码断言防回归。"""
    from prompt_harness.media import ark_runner as _mod

    src = Path(_mod.__file__).read_text(encoding="utf-8")
    assert "stdin=subprocess.DEVNULL" in src
