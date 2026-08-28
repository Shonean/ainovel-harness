"""_check_draft_quality 边界条件测试 —— Phase 5.3。"""
from __future__ import annotations

import pytest
from dashboard.workflows._anti_ai import _check_draft_quality


class TestCheckDraftQuality:
    def test_empty_text(self):
        result = _check_draft_quality("", 1)
        assert result["passed"] is False
        assert "为空" in result["reason"]

    def test_none_text(self):
        result = _check_draft_quality(None, 1)
        assert result["passed"] is False

    def test_whitespace_only(self):
        result = _check_draft_quality("   \n  ", 1)
        assert result["passed"] is False

    def test_insufficient_chinese_chars(self):
        text = "第1章\n" + "短" * 100  # Only 100 Chinese chars
        result = _check_draft_quality(text, 1)
        assert result["passed"] is False
        assert "2000" in result["reason"]

    def test_missing_chapter_title(self):
        # 2000+ Chinese chars but no chapter title
        text = "这是一个很长的正文内容。" * 250  # ~2500 chars
        result = _check_draft_quality(text, 1)
        assert result["passed"] is False
        assert "标题" in result["reason"]

    def test_valid_draft(self):
        text = "# 第1章 开始\n\n" + "这是一个很长的正文内容，包含了足够的中文字符。" * 150
        result = _check_draft_quality(text, 1)
        assert result["passed"] is True

    def test_ai_marker_density(self):
        # High density of AI markers — need enough base Chinese chars to pass minimum
        markers = "缓缓 淡淡 微微 轻轻 心中暗道 心中一凛 眸中闪过 " * 20
        body = "这是一个很长的正文内容，包含了足够的中文字符。" * 130  # ~2600 chars
        text = "# 第1章\n\n" + markers + body
        result = _check_draft_quality(text, 1)
        # Should flag high AI marker density
        assert result["passed"] is False
        assert "AI" in result["reason"]

    def test_low_ai_marker_density(self):
        text = "# 第1章\n\n" + "这是一个很长的正文内容，包含了足够的中文字符。" * 150
        result = _check_draft_quality(text, 1)
        assert result["passed"] is True
