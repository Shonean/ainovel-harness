"""批量运行上下文开关（ContextVar，按异步任务隔离）。

量产 fast 档由批量生成入口设置；ladder/derive/llm_client 读取：
- fast_mode=True：l4/l5 审计重试降为 0/1 轮、ai_flavor 修补降为 0 轮；
- model_override：运行级文本模型覆盖（空=不生效，显式参数仍最高优先）。

默认全部保持精品行为（fast=False / override 空）。
"""
from __future__ import annotations

from contextvars import ContextVar

fast_mode: ContextVar[bool] = ContextVar("ainovel_fast_mode", default=False)
model_override: ContextVar[str] = ContextVar("ainovel_model_override", default="")


def set_fast(enabled: bool) -> None:
    fast_mode.set(bool(enabled))


def is_fast() -> bool:
    return bool(fast_mode.get())


def set_model(model: str | None) -> None:
    model_override.set(str(model or ""))


def get_model() -> str:
    return model_override.get()
