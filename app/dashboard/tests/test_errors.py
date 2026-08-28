"""错误分类与 HTTP 映射测试 —— Phase 5.3。"""
from __future__ import annotations

import pytest
from dashboard.core.errors import (
    AInovelError,
    ConfigError, MissingAPIKeyError,
    AgentError, AgentTimeoutError,
    PipelineError, StepSkippedError, ChapterLockedError,
    ValidationError, ChapterNotFoundError, ProjectNotFoundError,
)


class TestAInovelError:
    def test_default_code_and_status(self):
        e = AInovelError("test")
        assert e.message == "test"
        assert e.code == "INTERNAL"
        assert e.status_code == 500

    def test_custom_code(self):
        e = AInovelError("test", code="CUSTOM", status_code=400)
        assert e.code == "CUSTOM"
        assert e.status_code == 400

    def test_is_exception(self):
        e = AInovelError("test")
        with pytest.raises(AInovelError):
            raise e


class TestConfigErrors:
    def test_missing_api_key(self):
        e = MissingAPIKeyError("openai")
        assert e.code == "MISSING_API_KEY"
        assert "openai" in e.message

    def test_config_error(self):
        e = ConfigError("bad config")
        assert e.code == "CONFIG"
        assert e.status_code == 500


class TestAgentErrors:
    def test_agent_timeout(self):
        e = AgentTimeoutError("draft", 120)
        assert e.code == "AGENT_TIMEOUT"
        assert "draft" in e.message
        assert "120" in e.message


class TestPipelineErrors:
    def test_step_skipped(self):
        e = StepSkippedError("polish")
        assert e.code == "STEP_SKIPPED"
        assert e.status_code == 409

    def test_chapter_locked(self):
        e = ChapterLockedError(5)
        assert e.code == "CHAPTER_LOCKED"
        assert e.status_code == 409
        assert "5" in e.message


class TestValidationErrors:
    def test_chapter_not_found(self):
        e = ChapterNotFoundError(42)
        assert e.code == "CHAPTER_NOT_FOUND"
        assert e.status_code == 404

    def test_project_not_found(self):
        e = ProjectNotFoundError()
        assert e.code == "PROJECT_NOT_FOUND"
        assert e.status_code == 404
