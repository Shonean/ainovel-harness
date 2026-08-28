"""单元测试：genre_aliases —— 输入别名与 profile-key 映射两条路径全覆盖。"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from data_modules.genre_aliases import (  # noqa: E402
    GENRE_INPUT_ALIASES,
    GENRE_PROFILE_KEY_ALIASES,
    normalize_genre_token,
    to_profile_key,
)


# ===== normalize_genre_token =====

def test_normalize_empty_returns_empty():
    assert normalize_genre_token("") == ""
    assert normalize_genre_token("   ") == ""


def test_normalize_none_treated_as_empty():
    # 实现里 str(None) -> "None"，但 strip 不会消掉。验证不报错。
    val = normalize_genre_token(None)  # type: ignore[arg-type]
    # 实现使用 str(token or "")，None or "" 为 ""
    assert val == ""


def test_normalize_passthrough_for_canonical():
    # 不在 alias 表里的输入直接返回原值
    assert normalize_genre_token("都市") == "都市"
    assert normalize_genre_token("不存在题材") == "不存在题材"


def test_normalize_alias_mapping():
    assert normalize_genre_token("玄幻") == "修仙"
    assert normalize_genre_token("修真") == "修仙"
    assert normalize_genre_token("玄幻修仙") == "修仙"
    assert normalize_genre_token("克系") == "克苏鲁"
    assert normalize_genre_token("克系悬疑") == "克苏鲁"
    assert normalize_genre_token("古言脑洞") == "古言"
    assert normalize_genre_token("直播") == "直播文"


def test_normalize_strips_surrounding_whitespace():
    assert normalize_genre_token("  玄幻  ") == "修仙"


# ===== to_profile_key =====

def test_to_profile_key_empty():
    assert to_profile_key("") == ""
    assert to_profile_key("   ") == ""
    assert to_profile_key(None) == ""  # type: ignore[arg-type]


def test_to_profile_key_canonical_alias_mapping():
    assert to_profile_key("修仙") == "xianxia"
    assert to_profile_key("玄幻") == "xianxia"
    assert to_profile_key("古言") == "romance"
    assert to_profile_key("克苏鲁") == "cosmic-horror"


def test_to_profile_key_falls_back_to_lower():
    # 既不在 INPUT alias 也不在 PROFILE alias → lowercase
    assert to_profile_key("UnknownGenre") == "unknowngenre"
    # 在 INPUT alias 里但映射结果不在 PROFILE alias 里
    # （所有现有 INPUT alias 的结果都在 PROFILE alias 里，无该路径；用纯 ASCII 自造一条）
    assert to_profile_key("MysteryX") == "mysteryx"


def test_alias_tables_consistency():
    """所有 INPUT alias 的值应在 PROFILE alias 里能解析（不能解析则 fallback 也得通）。"""
    for v in GENRE_INPUT_ALIASES.values():
        # 不强制要求一定在 PROFILE alias，但确保 to_profile_key 不抛错
        key = to_profile_key(v)
        assert isinstance(key, str)


def test_profile_alias_table_has_no_empty_values():
    for k, v in GENRE_PROFILE_KEY_ALIASES.items():
        assert k, "empty key in GENRE_PROFILE_KEY_ALIASES"
        assert v, f"empty value for {k}"
