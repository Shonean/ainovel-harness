"""
usage.py — LLM token 用量追踪（计费看板数据源）。

设计
----
* 进程内单例 ``USAGE``，记录每次 Anthropic API message 的 usage。
* 不落盘 —— dashboard 重启则清零（与 task 列表一致）。
* ``record`` 由 ``AnthropicAgentRunner`` 在每轮 message 结束后调用。
* ``GET /api/usage`` 暴露：当前模型、近 24h 聚合、最近 N 条记录。

usage 对象字段（来自 Anthropic ``message.usage``）：
  input_tokens / output_tokens / cache_read_input_tokens /
  cache_creation_input_tokens
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from threading import Lock


@dataclass
class UsageRecord:
    ts: float
    model: str
    agent: str  # "context" / "reviewer" / "critic" / "draft" / ...
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_input_tokens: int = 0
    cache_creation_input_tokens: int = 0

    def to_dict(self) -> dict:
        return {
            "ts": self.ts,
            "model": self.model,
            "agent": self.agent,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "cache_read_input_tokens": self.cache_read_input_tokens,
            "cache_creation_input_tokens": self.cache_creation_input_tokens,
            "total_tokens": self.input_tokens + self.output_tokens,
        }


class UsageTracker:
    """线程安全的用量记录器。"""

    def __init__(self, *, max_records: int = 2000) -> None:
        self._records: list[UsageRecord] = []
        self._lock = Lock()
        self._max = max_records

    def record(
        self,
        *,
        model: str,
        agent: str,
        usage: dict | None = None,
    ) -> None:
        """记录一次 message 的 usage。usage 为 None 时记 0。"""
        u = usage or {}
        rec = UsageRecord(
            ts=time.time(),
            model=model,
            agent=agent,
            input_tokens=int(u.get("input_tokens", 0) or 0),
            output_tokens=int(u.get("output_tokens", 0) or 0),
            cache_read_input_tokens=int(u.get("cache_read_input_tokens", 0) or 0),
            cache_creation_input_tokens=int(u.get("cache_creation_input_tokens", 0) or 0),
        )
        with self._lock:
            self._records.append(rec)
            if len(self._records) > self._max:
                # 删最旧的一半
                del self._records[: len(self._records) - self._max]

    def summary(self, *, window_seconds: float = 86400.0, recent: int = 50) -> dict:
        """返回计费看板所需的聚合 + 最近记录。"""
        now = time.time()
        cutoff = now - window_seconds
        with self._lock:
            recent_records = list(self._records)

        in_window = [r for r in recent_records if r.ts >= cutoff]

        def _sum(field_: str) -> int:
            return sum(getattr(r, field_) for r in in_window)

        by_agent: dict[str, dict[str, int]] = {}
        for r in in_window:
            bucket = by_agent.setdefault(r.agent, {
                "input_tokens": 0, "output_tokens": 0, "calls": 0,
            })
            bucket["input_tokens"] += r.input_tokens
            bucket["output_tokens"] += r.output_tokens
            bucket["calls"] += 1

        return {
            "model": (
                os.environ.get("ARK_MODEL_PRO")
                or os.environ.get("ANTHROPIC_MODEL")
                or "doubao-1.5-pro-32k"
            ),
            "api_key_configured": bool(
                (os.environ.get("ARK_API_KEY") or os.environ.get("ANTHROPIC_API_KEY") or "").strip()
            ),
            "window_seconds": int(window_seconds),
            "calls_in_window": len(in_window),
            "totals": {
                "input_tokens": _sum("input_tokens"),
                "output_tokens": _sum("output_tokens"),
                "cache_read_input_tokens": _sum("cache_read_input_tokens"),
                "cache_creation_input_tokens": _sum("cache_creation_input_tokens"),
                "total_tokens": _sum("input_tokens") + _sum("output_tokens"),
            },
            "by_agent": by_agent,
            "recent": [r.to_dict() for r in recent_records[-recent:]],
        }


# 全局单例
USAGE = UsageTracker()
