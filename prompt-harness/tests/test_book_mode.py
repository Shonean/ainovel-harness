"""书级模式（premium 精品 / mass 量产）只读助手测试：state.json 读取与兜底。

覆盖：mass / premium / 字段缺失 / 文件缺失 / 坏 JSON / 非法值。
"""
from __future__ import annotations

import json
from pathlib import Path

from prompt_harness.server import load_book_mode


def _write_state(book_root: Path, payload) -> None:
    d = book_root / ".ainovel"
    d.mkdir(parents=True, exist_ok=True)
    if isinstance(payload, str):
        (d / "state.json").write_text(payload, encoding="utf-8")
    else:
        (d / "state.json").write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def test_mass_mode(tmp_path):
    _write_state(tmp_path, {"book_mode": "mass"})
    assert load_book_mode(tmp_path) == "mass"


def test_premium_mode(tmp_path):
    _write_state(tmp_path, {"book_mode": "premium"})
    assert load_book_mode(tmp_path) == "premium"


def test_missing_field_defaults_premium(tmp_path):
    _write_state(tmp_path, {"project_info": {"title": "x"}})
    assert load_book_mode(tmp_path) == "premium"


def test_missing_file_defaults_premium(tmp_path):
    assert load_book_mode(tmp_path) == "premium"


def test_bad_json_defaults_premium(tmp_path):
    _write_state(tmp_path, "{not json")
    assert load_book_mode(tmp_path) == "premium"


def test_illegal_value_defaults_premium(tmp_path):
    _write_state(tmp_path, {"book_mode": "turbo"})
    assert load_book_mode(tmp_path) == "premium"
