"""单元测试：deterministic_lint"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]  # scripts/
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from data_modules.deterministic_lint import DeterministicLint  # noqa: E402


def test_dash_violation():
    text = "他抬起头——看见了那张脸。"
    linter = DeterministicLint()
    vs = linter.check(text)
    codes = {v.rule_id for v in vs}
    assert "R1" in codes


def test_not_is_pattern():
    """'不是X是Y' 单句变体（X 与"是"之间无分句标点）"""
    text = "这不是恐惧而是绝望。"
    linter = DeterministicLint()
    vs = linter.check(text)
    codes = {v.rule_id for v in vs}
    assert "R2" in codes


def test_straight_quote_detected():
    text = '他说："今天怎么样？"'
    linter = DeterministicLint()
    vs = linter.check(text)
    codes = {v.rule_id for v in vs}
    assert "R6-A" in codes


def test_corner_quote_detected():
    text = "「你听得到吗？」"
    linter = DeterministicLint()
    vs = linter.check(text)
    codes = {v.rule_id for v in vs}
    assert "R6-B" in codes


def test_full_paren_in_narration():
    text = "他走进了房间（这是他第一次来），开始检查。"
    linter = DeterministicLint()
    vs = linter.check(text)
    codes = {v.rule_id for v in vs}
    assert "R6-C" in codes


def test_full_paren_in_system_popup_skipped():
    """【系统弹窗】里的（）不应触发"""
    text = "【任务（紧急）：找到出口】"
    linter = DeterministicLint()
    vs = linter.check(text)
    codes = {v.rule_id for v in vs}
    assert "R6-C" not in codes


def test_first_reaction_pattern():
    text = "他第一反应是逃跑，但是腿不听使唤。"
    linter = DeterministicLint()
    vs = linter.check(text)
    codes = {v.rule_id for v in vs}
    assert "R16" in codes


def test_sense_metaphor():
    text = "指尖发凉，像泡进冰水。"
    linter = DeterministicLint()
    vs = linter.check(text)
    codes = {v.rule_id for v in vs}
    assert "R18" in codes


def test_inner_drag():
    text = "他想了想，决定离开。"
    linter = DeterministicLint()
    vs = linter.check(text)
    codes = {v.rule_id for v in vs}
    assert "R19" in codes


def test_sense_reverse():
    text = "他睁开眼，发现自己在一片陌生的房间。"
    linter = DeterministicLint()
    vs = linter.check(text)
    codes = {v.rule_id for v in vs}
    assert "R24" in codes


def test_filter_words():
    text = "他注意到桌上的纸条。"
    linter = DeterministicLint()
    vs = linter.check(text)
    codes = {v.rule_id for v in vs}
    assert "R4" in codes


def test_clean_text_no_violations():
    text = "他抬手挡了一下。骨头响。"
    linter = DeterministicLint()
    vs = linter.check(text)
    assert len(vs) == 0


def test_violation_severity_is_hard():
    text = "他——看见了。"
    linter = DeterministicLint()
    vs = linter.check(text)
    assert all(v.severity == "hard" for v in vs)


def test_no_old_book_character_names():
    """关键回归测试：不能出现旧书《殡仪馆》专属人名"""
    import data_modules.deterministic_lint as mod
    src_text = Path(mod.__file__).read_text(encoding="utf-8")
    forbidden = ["苏清寒", "李公公", "雷豹", "周富", "刘知县", "沈晚", "萧景"]
    for name in forbidden:
        assert name not in src_text, f"deterministic_lint.py 残留了旧书人名: {name}"
