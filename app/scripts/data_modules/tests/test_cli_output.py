"""单元测试：cli_output —— 让 cli_output.py 的两条主路径都被覆盖。"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from data_modules import cli_output as co  # noqa: E402


def test_build_success_minimal():
    payload = co.build_success()
    assert payload["status"] == "success"
    assert payload["message"] == "ok"
    assert "data" not in payload
    assert "warnings" not in payload


def test_build_success_with_data_and_warnings():
    payload = co.build_success(data={"k": 1}, message="done", warnings=["w1"])
    assert payload["status"] == "success"
    assert payload["data"] == {"k": 1}
    assert payload["warnings"] == ["w1"]
    assert payload["message"] == "done"


def test_build_success_data_none_omits_data_key():
    payload = co.build_success(data=None, message="m")
    assert "data" not in payload


def test_build_error_minimal():
    payload = co.build_error(code="X1", message="boom")
    assert payload["status"] == "error"
    err = payload["error"]
    assert err["code"] == "X1"
    assert err["message"] == "boom"
    assert "suggestion" not in err
    assert "details" not in err


def test_build_error_with_suggestion_and_details():
    payload = co.build_error(
        code="E_BAD",
        message="bad input",
        suggestion="try harder",
        details={"field": "x"},
    )
    err = payload["error"]
    assert err["suggestion"] == "try harder"
    assert err["details"] == {"field": "x"}


def test_print_success_emits_json(capsys):
    co.print_success(data={"a": 1}, message="ok")
    out = capsys.readouterr().out
    parsed = json.loads(out)
    assert parsed["status"] == "success"
    assert parsed["data"]["a"] == 1


def test_print_error_emits_json_with_chinese_intact(capsys):
    co.print_error(code="E1", message="中文消息", suggestion="重试一下")
    out = capsys.readouterr().out
    # ensure_ascii=False 应保留中文
    assert "中文消息" in out
    parsed = json.loads(out)
    assert parsed["status"] == "error"
    assert parsed["error"]["code"] == "E1"
    assert parsed["error"]["suggestion"] == "重试一下"


def test_print_json_round_trips():
    payload = co.build_success(data={"unicode": "✓ 中文"})
    co.print_json(payload)


def test_error_payload_dataclass_defaults():
    ep = co.ErrorPayload(code="C", message="M")
    assert ep.suggestion is None
    assert ep.details is None
    ep2 = co.ErrorPayload(code="C", message="M", suggestion="s", details={"k": 1})
    assert ep2.suggestion == "s"
    assert ep2.details == {"k": 1}
