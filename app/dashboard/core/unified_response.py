"""
统一 API 响应格式 v1.0

所有 API 端点逐步迁移到统一格式：
{
    "ok": bool,           # 是否成功
    "data": any | null,   # 成功时的数据
    "error": str | null,  # 失败时的错误信息
}

好处：
- 前端可以统一处理成功/失败逻辑
- 不用每个端点自己定义错误格式
- 方便调试和日志

迁移策略（渐进式）：
- 新端点必须用 ok_response / error_response
- 旧端点逐步迁移，一次改一批
- 前端 api.js 加兼容层，同时支持新旧格式
"""
from __future__ import annotations

from typing import Any


def ok_response(data: Any = None) -> dict[str, Any]:
    """成功响应。"""
    return {
        "ok": True,
        "data": data,
        "error": None,
    }


def error_response(error: str, data: Any = None) -> dict[str, Any]:
    """失败响应。"""
    return {
        "ok": False,
        "data": data,
        "error": error,
    }


def wrap_existing_response(raw: Any) -> dict[str, Any]:
    """
    把旧格式响应包装成新格式（兼容用）。

    规则：
    - 已经有 ok 字段 → 直接返回
    - 是 dict 且有 "detail"（FastAPI HTTPException）→ 包成 error
    - 其他 → 包成 ok + data
    """
    if isinstance(raw, dict) and "ok" in raw:
        return raw  # 已经是新格式
    if isinstance(raw, dict) and "detail" in raw:
        return error_response(str(raw["detail"]))
    return ok_response(raw)


# 常见错误常量
ERRORS = {
    "NOT_FOUND": "资源不存在",
    "BAD_REQUEST": "请求参数错误",
    "UNAUTHORIZED": "未授权",
    "FORBIDDEN": "无权限",
    "INTERNAL_ERROR": "服务器内部错误",
    "PROJECT_REQUIRED": "请先选择一本书",
}
