"""
错误分类体系 —— Phase 2 优化。

所有 dashboard 层异常继承 AInovelError，携带：
  - message: 用户可读的错误描述
  - code: 机器可读的错误码（用于前端分类处理）
  - status_code: HTTP 状态码（用于异常处理器自动转换）
  - detail: 可选的调试细节（不出现在 HTTP 响应体中）

在 app.py 中注册全局异常处理器：
    from dashboard.core.errors import AInovelError, install_error_handlers
    install_error_handlers(app)
"""
from __future__ import annotations

from typing import Any


class AInovelError(Exception):
    """基础异常。所有 dashboard 层异常继承此类。"""

    def __init__(
        self,
        message: str,
        *,
        code: str = "INTERNAL",
        status_code: int = 500,
        detail: str | None = None,
    ):
        self.message = message
        self.code = code
        self.status_code = status_code
        self.detail = detail
        super().__init__(message)


# ── 配置错误 ──

class ConfigError(AInovelError):
    """配置缺失或无效。"""
    def __init__(self, message: str, **kwargs: Any):
        kwargs.setdefault("code", "CONFIG")
        kwargs.setdefault("status_code", 500)
        super().__init__(message, **kwargs)


class MissingAPIKeyError(ConfigError):
    """API Key 未配置。"""
    def __init__(self, provider: str = "openai"):
        super().__init__(
            f"缺少 {provider} API Key，请在 .env 或 API 配置页设置",
            code="MISSING_API_KEY",
        )


# ── Agent 执行错误 ──

class AgentError(AInovelError):
    """Agent 执行过程中的可恢复错误。"""
    def __init__(self, message: str, **kwargs: Any):
        kwargs.setdefault("code", "AGENT")
        kwargs.setdefault("status_code", 500)
        super().__init__(message, **kwargs)


class AgentTimeoutError(AgentError):
    """Agent 执行超时。"""
    def __init__(self, agent_name: str, timeout_s: float):
        super().__init__(
            f"Agent '{agent_name}' 执行超时（{timeout_s:.0f}s）",
            code="AGENT_TIMEOUT",
        )


class AgentTruncatedError(AgentError):
    """Agent 输出被截断（turn 限制）。"""
    def __init__(self, agent_name: str, max_turns: int):
        super().__init__(
            f"Agent '{agent_name}' 在 {max_turns} 轮后仍未完成",
            code="AGENT_TRUNCATED",
        )


class AgentModelError(AgentError):
    """模型 API 调用失败。"""
    def __init__(self, message: str):
        super().__init__(message, code="AGENT_MODEL", status_code=502)


# ── 流水线错误 ──

class PipelineError(AInovelError):
    """工作流流水线执行错误。"""
    def __init__(self, message: str, **kwargs: Any):
        kwargs.setdefault("code", "PIPELINE")
        kwargs.setdefault("status_code", 500)
        super().__init__(message, **kwargs)


class StepSkippedError(PipelineError):
    """步骤被用户跳过。"""
    def __init__(self, step_id: str):
        super().__init__(
            f"步骤 '{step_id}' 被用户跳过",
            code="STEP_SKIPPED",
            status_code=409,
        )


class ChapterLockedError(PipelineError):
    """章节已 finalize，禁止修改。"""
    def __init__(self, chapter: int):
        super().__init__(
            f"第 {chapter} 章已 finalize，禁止修改",
            code="CHAPTER_LOCKED",
            status_code=409,
        )


# ── 校验错误 ──

class ValidationError(AInovelError):
    """输入/数据校验失败。"""
    def __init__(self, message: str, **kwargs: Any):
        kwargs.setdefault("code", "VALIDATION")
        kwargs.setdefault("status_code", 400)
        super().__init__(message, **kwargs)


class ChapterNotFoundError(ValidationError):
    """章节文件不存在。"""
    def __init__(self, chapter: int):
        super().__init__(
            f"第 {chapter} 章不存在",
            code="CHAPTER_NOT_FOUND",
            status_code=404,
        )


class ProjectNotFoundError(ValidationError):
    """项目不存在或未初始化。"""
    def __init__(self, project_root: str = ""):
        super().__init__(
            f"项目未初始化{f' ({project_root})' if project_root else ''}，请先运行 init",
            code="PROJECT_NOT_FOUND",
            status_code=404,
        )


# ── 资源错误 ──

class ResourceError(AInovelError):
    """文件/数据库等资源访问错误。"""
    def __init__(self, message: str, **kwargs: Any):
        kwargs.setdefault("code", "RESOURCE")
        kwargs.setdefault("status_code", 500)
        super().__init__(message, **kwargs)


class FileNotFoundError_(ResourceError):
    """文件不存在（业务层）。"""
    def __init__(self, path: str):
        super().__init__(
            f"文件不存在: {path}",
            code="FILE_NOT_FOUND",
            status_code=404,
        )


# ── 全局异常处理器 ──

def install_error_handlers(app) -> None:
    """在 FastAPI app 上注册 AInovelError 的全局异常处理器。

    将 AInovelError 子类自动转换为统一的 JSON 错误响应：
        {"error": {"code": "...", "message": "..."}}
    """
    from fastapi import Request
    from fastapi.responses import JSONResponse

    @app.exception_handler(AInovelError)
    async def _ainovel_error_handler(request: Request, exc: AInovelError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "error": {
                    "code": exc.code,
                    "message": exc.message,
                }
            },
        )

    @app.exception_handler(Exception)
    async def _fallback_error_handler(request: Request, exc: Exception) -> JSONResponse:
        """兜底：未分类的异常统一返回 500，避免泄露内部细节。"""
        import traceback
        import logging
        logger = logging.getLogger("dashboard.errors")
        tb = traceback.format_exc()
        logger.error(f"Unhandled exception: {exc}\n{tb}")
        return JSONResponse(
            status_code=500,
            content={
                "error": {
                    "code": "INTERNAL",
                    "message": "服务器内部错误，请查看日志。",
                }
            },
        )


__all__ = [
    "AInovelError",
    "ConfigError", "MissingAPIKeyError",
    "AgentError", "AgentTimeoutError", "AgentTruncatedError", "AgentModelError",
    "PipelineError", "StepSkippedError", "ChapterLockedError",
    "ValidationError", "ChapterNotFoundError", "ProjectNotFoundError",
    "ResourceError", "FileNotFoundError_",
    "install_error_handlers",
]
