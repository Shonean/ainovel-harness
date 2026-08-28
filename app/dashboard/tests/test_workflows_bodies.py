"""Pydantic 模型验证测试 —— Phase 5.3。"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from dashboard.workflows._bodies import (
    _WriteBody, _ReviewBody, _PlanBody, _InitBody,
    _FinalizeBody, _UnfinalizeBody, _ConfirmPlotBody,
    _ReplanBody, _LearnBody, _QueryBody, _CharacterSkillBody,
)


class TestWriteBody:
    def test_minimal_valid(self):
        body = _WriteBody(chapter=1)
        assert body.chapter == 1
        assert body.model is None

    def test_chapter_must_be_positive(self):
        with pytest.raises(ValidationError):
            _WriteBody(chapter=0)
        with pytest.raises(ValidationError):
            _WriteBody(chapter=-1)

    def test_temperature_range(self):
        body = _WriteBody(chapter=1, temperature=1.5)
        assert body.temperature == 1.5
        with pytest.raises(ValidationError):
            _WriteBody(chapter=1, temperature=-0.1)
        with pytest.raises(ValidationError):
            _WriteBody(chapter=1, temperature=2.1)


class TestPlanBody:
    def test_defaults(self):
        body = _PlanBody(volume=1)
        assert body.volume == 1
        assert body.chapter_count == 10

    def test_chapter_count_range(self):
        _PlanBody(volume=1, chapter_count=1)
        _PlanBody(volume=1, chapter_count=50)
        with pytest.raises(ValidationError):
            _PlanBody(volume=1, chapter_count=0)
        with pytest.raises(ValidationError):
            _PlanBody(volume=1, chapter_count=51)


class TestCharacterSkillBody:
    def test_defaults(self):
        body = _CharacterSkillBody()
        assert body.target == ""
        assert body.context == ""
        assert body.model is None

    def test_with_target(self):
        body = _CharacterSkillBody(target="主角")
        assert body.target == "主角"
