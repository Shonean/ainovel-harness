"""
UniversalLogger v1.0 —— 全系统统一日志系统

日志目录结构：
  logs/
  ├── {YYYY-MM-DD}/
  │   ├── business.jsonl    # 业务事件（章节生成/审查/润色/决策等）
  │   ├── llm.jsonl         # 全局 LLM 调用（主系统 + prompt-harness 都写这里）
  │   ├── requests.jsonl    # HTTP 请求日志
  │   └── system.jsonl      # 系统事件（启动/错误/告警）
  └── prompt-harness/       # prompt-harness 训练特有日志（沿用 RunLogger）
      ├── sessions/
      ├── runs/
      └── tasks/

设计原则：
- 单一真相源：所有 LLM 调用统一写入 llm.jsonl
- 低开销：append-only JSONL，异步写入（可选）
- 可查询：提供 list / read / summary 等静态方法
- 向后兼容：RunLogger 继续独立工作，同时将 LLM 事件镜像到全局
"""
from __future__ import annotations

import json
import os
import platform
import sys
import threading
import time
import traceback
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# ============================================================
# 版本信息
# ============================================================

LOGGER_VERSION = "1.0.0"
SOFTWARE_VERSION = "5.2.0"  # 主系统版本


# ============================================================
# 基础工具
# ============================================================

def _ts() -> str:
    """ISO 格式 UTC 时间戳（毫秒精度）。"""
    return datetime.now(timezone.utc).isoformat()


def _ts_ms() -> int:
    """毫秒级时间戳。"""
    return int(time.time() * 1000)


def _new_id(prefix: str = "") -> str:
    """生成短 ID。"""
    s = uuid.uuid4().hex[:12]
    return f"{prefix}_{s}" if prefix else s


def _safe_str(s: str, max_len: int = 200) -> str:
    """安全截断字符串。"""
    if not s:
        return ""
    if len(s) <= max_len:
        return s
    return s[:max_len] + f"...[{len(s)}]"


def _get_env_snapshot() -> dict[str, Any]:
    """采集环境快照。"""
    return {
        "python_version": sys.version,
        "platform": platform.platform(),
        "os": os.name,
        "hostname": platform.node(),
        "log_version": LOGGER_VERSION,
        "software_version": SOFTWARE_VERSION,
    }


def _resolve_log_dir() -> Path:
    """
    解析全局日志根目录。
    优先级：环境变量 AINOVEL_LOG_DIR > 项目根目录下的 logs/
    """
    env_dir = os.environ.get("AINOVEL_LOG_DIR")
    if env_dir:
        return Path(env_dir).resolve()

    # 从本文件位置推断项目根：app/dashboard/universal_logger.py → ../../
    project_root = Path(__file__).resolve().parents[2]
    return project_root / "logs"


# ============================================================
# 全局单例（懒加载）
# ============================================================

_global_logger: "UniversalLogger | None" = None
_init_lock = threading.Lock()


def get_logger() -> "UniversalLogger":
    """获取全局单例 Logger。"""
    global _global_logger
    if _global_logger is None:
        with _init_lock:
            if _global_logger is None:
                _global_logger = UniversalLogger()
    return _global_logger


# ============================================================
# 主 Logger 类
# ============================================================

class UniversalLogger:
    """
    全系统统一日志记录器。

    四类核心日志文件（按天滚动）：
    - business.jsonl  — 业务事件
    - llm.jsonl       — LLM 调用（所有子系统共享）
    - requests.jsonl  — HTTP 请求
    - system.jsonl    — 系统事件

    使用方式：
        logger = UniversalLogger()  # 或 get_logger() 获取单例
        logger.log_llm_call(model="...", prompt_tokens=100, ...)
        logger.log_business(event="chapter_generated", data={...})
    """

    # 日志类别 → 文件名
    _CATEGORIES = {
        "business": "business.jsonl",
        "llm": "llm.jsonl",
        "requests": "requests.jsonl",
        "system": "system.jsonl",
    }

    def __init__(self, log_dir: Path | None = None) -> None:
        self.base_dir = log_dir or _resolve_log_dir()
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self._file_handles: dict[str, Any] = {}  # category → file handle
        self._file_date: dict[str, str] = {}    # category → 当前日期
        self._lock = threading.Lock()

        # 请求 ID 生成（用于 requests 日志关联）
        self._req_counter = 0
        self._req_lock = threading.Lock()

    # ============================================================
    # 文件管理（按天滚动）
    # ============================================================

    def _get_handle(self, category: str):
        """获取对应类别的文件句柄，自动按天滚动。"""
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

        with self._lock:
            handle = self._file_handles.get(category)
            current_date = self._file_date.get(category, "")

            # 日期变了或句柄不存在 → 重新打开
            if handle is None or current_date != today:
                if handle:
                    try:
                        handle.close()
                    except Exception:
                        pass

                filename = self._CATEGORIES.get(category, f"{category}.jsonl")
                day_dir = self.base_dir / today
                day_dir.mkdir(parents=True, exist_ok=True)
                path = day_dir / filename
                handle = open(path, "a", encoding="utf-8")
                self._file_handles[category] = handle
                self._file_date[category] = today

        return handle

    def _emit(self, category: str, record: dict[str, Any]) -> None:
        """写一条事件到对应类别的 JSONL 文件。"""
        try:
            record.setdefault("ts", _ts())
            record.setdefault("ts_ms", _ts_ms())
            record.setdefault("category", category)

            handle = self._get_handle(category)
            handle.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
            handle.flush()
        except Exception:
            # 日志系统自身的错误不能影响业务，静默失败
            pass

    def _next_req_id(self) -> str:
        """生成递增请求 ID。"""
        with self._req_lock:
            self._req_counter += 1
            return f"req_{self._req_counter}_{int(time.time())}"

    def close(self) -> None:
        """关闭所有文件句柄。"""
        with self._lock:
            for handle in self._file_handles.values():
                try:
                    handle.close()
                except Exception:
                    pass
            self._file_handles.clear()
            self._file_date.clear()

    # ============================================================
    # 【第 1 类】业务事件
    # ============================================================

    def log_business(
        self,
        event: str,
        data: dict[str, Any] | None = None,
        project: str = "",
        chapter: int | None = None,
        status: str = "success",  # success | error | running
        duration_ms: int | None = None,
    ) -> str:
        """
        记录业务事件。

        event 示例：
        - chapter_generated    章节生成完成
        - chapter_reviewed     章节审查完成
        - chapter_polished     章节润色完成
        - outline_generated    大纲生成
        - decision_made        决策事件
        - project_created      新建项目
        - project_switched     切换项目
        """
        event_id = _new_id("evt")
        self._emit("business", {
            "event_id": event_id,
            "event": event,
            "project": project,
            "chapter": chapter,
            "status": status,
            "duration_ms": duration_ms,
            "data": data or {},
        })
        return event_id

    # ============================================================
    # 【第 2 类】LLM 调用（全局统一）
    # ============================================================

    def log_llm_call(
        self,
        model: str,
        endpoint: str = "",
        prompt_len: int = 0,
        completion_len: int = 0,
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        total_tokens: int = 0,
        latency_ms: int = 0,
        cache_hit: bool = False,
        retry_count: int = 0,
        call_type: str = "",   # generate | skeleton_extract | analyze | review | etc.
        status: str = "success",  # success | error | timeout
        error: str = "",
        subsystem: str = "main",  # main | prompt-harness | agent
        project: str = "",
        chapter: int | None = None,
    ) -> str:
        """
        记录一次 LLM 调用。所有子系统共享此日志。

        这是成本统计、性能分析、错误归因的单一真相源。
        """
        call_id = _new_id("llm")
        self._emit("llm", {
            "call_id": call_id,
            "model": model,
            "endpoint": endpoint,
            "prompt_len": prompt_len,
            "completion_len": completion_len,
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": total_tokens,
            "latency_ms": latency_ms,
            "cache_hit": cache_hit,
            "retry_count": retry_count,
            "call_type": call_type,
            "status": status,
            "error": error,
            "subsystem": subsystem,
            "project": project,
            "chapter": chapter,
        })
        return call_id

    def log_llm_error(
        self,
        model: str,
        error_type: str,
        error_message: str,
        retry_count: int = 0,
        max_retries: int = 0,
        will_retry: bool = False,
        call_type: str = "",
        subsystem: str = "main",
    ) -> None:
        """LLM 错误详情。"""
        self._emit("llm", {
            "event": "llm_error",
            "model": model,
            "error_type": error_type,
            "error_message": _safe_str(error_message, 500),
            "retry_count": retry_count,
            "max_retries": max_retries,
            "will_retry": will_retry,
            "call_type": call_type,
            "subsystem": subsystem,
        })

    # ============================================================
    # 【第 3 类】HTTP 请求日志
    # ============================================================

    def log_request_start(
        self,
        method: str,
        path: str,
        client_ip: str = "",
        query_params: dict[str, Any] | None = None,
    ) -> str:
        """请求开始。返回 request_id 用于结束时关联。"""
        req_id = self._next_req_id()
        self._emit("requests", {
            "request_id": req_id,
            "phase": "start",
            "method": method,
            "path": path,
            "client_ip": client_ip,
            "query_params": query_params or {},
        })
        return req_id

    def log_request_end(
        self,
        request_id: str,
        method: str,
        path: str,
        status_code: int,
        duration_ms: int,
        response_size: int = 0,
        error: str = "",
    ) -> None:
        """请求结束。"""
        self._emit("requests", {
            "request_id": request_id,
            "phase": "end",
            "method": method,
            "path": path,
            "status_code": status_code,
            "duration_ms": duration_ms,
            "response_size": response_size,
            "error": error,
        })

    # ============================================================
    # 【第 4 类】系统事件
    # ============================================================

    def log_system_start(
        self,
        component: str = "main_app",
        config_snapshot: dict[str, Any] | None = None,
    ) -> None:
        """系统启动。"""
        self._emit("system", {
            "event": "system_start",
            "component": component,
            "env": _get_env_snapshot(),
            "config": config_snapshot or {},
        })

    def log_system_shutdown(self, component: str = "main_app", reason: str = "normal") -> None:
        """系统关闭。"""
        self._emit("system", {
            "event": "system_shutdown",
            "component": component,
            "reason": reason,
        })

    def log_warning(
        self,
        message: str,
        component: str = "",
        detail: dict[str, Any] | None = None,
    ) -> None:
        """警告。"""
        self._emit("system", {
            "event": "warning",
            "component": component,
            "message": message,
            "detail": detail or {},
        })

    def log_error(
        self,
        error: str | Exception,
        component: str = "",
        context: str = "",
    ) -> None:
        """错误。"""
        if isinstance(error, Exception):
            tb = traceback.format_exc()
            self._emit("system", {
                "event": "error",
                "component": component,
                "error_type": type(error).__name__,
                "error_message": str(error),
                "context": context,
                "traceback": tb,
            })
        else:
            self._emit("system", {
                "event": "error",
                "component": component,
                "error_message": error,
                "context": context,
            })

    # ============================================================
    # 通用事件
    # ============================================================

    def log_custom(self, category: str, event: str, data: dict[str, Any] | None = None) -> None:
        """自定义类别事件。"""
        self._emit(category, {"event": event, **(data or {})})

    # ============================================================
    # 类方法：日志查询
    # ============================================================

    @staticmethod
    def list_log_files(
        log_dir: Path | None = None,
        category: str = "all",  # all | business | llm | requests | system
        days: int = 7,
    ) -> list[dict[str, Any]]:
        """列出日志文件（按日期倒序）。"""
        base = log_dir or _resolve_log_dir()
        if not base.is_dir():
            return []

        categories = (
            ["business", "llm", "requests", "system"]
            if category == "all"
            else [category]
        )

        results = []
        # 遍历 date 目录
        for day_dir in sorted(base.iterdir(), reverse=True):
            if not day_dir.is_dir() or not day_dir.name[0].isdigit():
                continue
            date_str = day_dir.name
            for cat in categories:
                f = day_dir / f"{cat}.jsonl"
                if f.is_file():
                    stat = f.stat()
                    results.append({
                        "date": date_str,
                        "category": cat,
                        "size_bytes": stat.st_size,
                        "size_kb": round(stat.st_size / 1024, 1),
                        "modified_at": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(),
                        "filepath": str(f.relative_to(base)),
                    })
            if len(results) >= days * len(categories):
                break

        return results

    @staticmethod
    def read_log(
        filepath: str,
        log_dir: Path | None = None,
        event_filter: list[str] | None = None,
        limit: int = 100,
        reverse: bool = True,
    ) -> list[dict[str, Any]]:
        """读取一个日志文件，支持事件过滤。"""
        base = log_dir or _resolve_log_dir()
        f = base / filepath
        if not f.is_file():
            return []

        events: list[dict[str, Any]] = []
        with open(f, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    evt = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if event_filter and evt.get("event") not in event_filter:
                    continue
                events.append(evt)

        if reverse:
            events.reverse()

        return events[:limit]

    @staticmethod
    def llm_summary(
        log_dir: Path | None = None,
        days: int = 1,
    ) -> dict[str, Any]:
        """LLM 调用汇总（近 N 天）。"""
        base = log_dir or _resolve_log_dir()
        total_calls = 0
        total_tokens = 0
        total_prompt_tokens = 0
        total_completion_tokens = 0
        total_latency_ms = 0
        error_count = 0
        by_model: dict[str, dict[str, Any]] = {}
        by_subsystem: dict[str, int] = {}

        for day_dir in sorted(base.iterdir(), reverse=True)[:days]:
            if not day_dir.is_dir():
                continue
            f = day_dir / "llm.jsonl"
            if not f.is_file():
                continue
            try:
                with open(f, "r", encoding="utf-8") as fh:
                    for line in fh:
                        try:
                            evt = json.loads(line.strip())
                        except json.JSONDecodeError:
                            continue
                        # 只统计 llm_call 事件，不算 llm_error
                        if evt.get("event") == "llm_error":
                            error_count += 1
                            continue
                        if "call_id" not in evt and "total_tokens" not in evt:
                            # 可能是自定义事件，跳过
                            continue

                        total_calls += 1
                        tok = evt.get("total_tokens", 0) or 0
                        ptok = evt.get("prompt_tokens", 0) or 0
                        ctok = evt.get("completion_tokens", 0) or 0
                        lat = evt.get("latency_ms", 0) or 0
                        total_tokens += tok
                        total_prompt_tokens += ptok
                        total_completion_tokens += ctok
                        total_latency_ms += lat

                        model = evt.get("model", "unknown")
                        if model not in by_model:
                            by_model[model] = {"calls": 0, "tokens": 0, "errors": 0}
                        by_model[model]["calls"] += 1
                        by_model[model]["tokens"] += tok
                        if evt.get("status") == "error":
                            by_model[model]["errors"] += 1
                            error_count += 1

                        sub = evt.get("subsystem", "unknown")
                        by_subsystem[sub] = by_subsystem.get(sub, 0) + 1
            except Exception:
                pass

        avg_latency = round(total_latency_ms / max(total_calls, 1), 0)
        return {
            "period_days": days,
            "total_calls": total_calls,
            "total_tokens": total_tokens,
            "prompt_tokens": total_prompt_tokens,
            "completion_tokens": total_completion_tokens,
            "avg_latency_ms": avg_latency,
            "error_count": error_count,
            "by_model": by_model,
            "by_subsystem": by_subsystem,
        }


# ============================================================
# FastAPI 中间件（请求日志）
# ============================================================

def create_request_logging_middleware(logger: UniversalLogger | None = None):
    """
    创建 FastAPI 请求日志中间件。

    用法：
        from universal_logger import create_request_logging_middleware
        app.middleware("http")(create_request_logging_middleware())
    """
    _logger = logger or get_logger()

    async def request_logging_middleware(request, call_next):
        import time as _time
        method = request.method
        path = request.url.path

        # 跳过健康检查和静态资源，减少噪音
        if path in ("/health", "/docs", "/openapi.json", "/redoc") or path.startswith("/assets/"):
            return await call_next(request)

        req_id = _logger.log_request_start(
            method=method,
            path=path,
            client_ip=request.client.host if request.client else "",
        )

        start = _time.monotonic()
        status_code = 500
        error_msg = ""
        try:
            response = await call_next(request)
            status_code = response.status_code
            return response
        except Exception as e:
            status_code = 500
            error_msg = str(e)
            raise
        finally:
            duration_ms = int((_time.monotonic() - start) * 1000)
            _logger.log_request_end(
                request_id=req_id,
                method=method,
                path=path,
                status_code=status_code,
                duration_ms=duration_ms,
                error=error_msg,
            )

    return request_logging_middleware


# ============================================================
# Prompt Harness 桥接：让 RunLogger 的 LLM 事件也写入全局
# ============================================================

def bridge_run_logger_llm(run_logger_instance: Any) -> None:
    """
    将 RunLogger 的 LLM 调用镜像到 UniversalLogger。

    原理：替换 RunLogger 的 log_llm_call 方法，让它同时写全局日志。
    这样 prompt-harness 的所有 LLM 调用都会出现在 llm.jsonl 里。
    """
    original_log_llm = run_logger_instance.log_llm_call
    universal = get_logger()

    def _bridged_log_llm(*args, **kwargs):
        # 先调用原版（保证 prompt-harness 自己的日志完整）
        original_log_llm(*args, **kwargs)
        # 再镜像到全局
        try:
            universal.log_llm_call(
                model=kwargs.get("model", ""),
                endpoint=kwargs.get("endpoint", ""),
                prompt_len=kwargs.get("prompt_len", 0),
                completion_len=kwargs.get("completion_len", 0),
                prompt_tokens=kwargs.get("prompt_tokens", 0),
                completion_tokens=kwargs.get("completion_tokens", 0),
                total_tokens=kwargs.get("total_tokens", 0),
                latency_ms=kwargs.get("latency_ms", 0),
                cache_hit=kwargs.get("cache_hit", False),
                retry_count=kwargs.get("retry_count", 0),
                call_type=kwargs.get("call_type", ""),
                status=kwargs.get("status", "success"),
                error=kwargs.get("error", ""),
                subsystem="prompt-harness",
            )
        except Exception:
            pass  # 镜像失败不影响主流程

    run_logger_instance.log_llm_call = _bridged_log_llm
