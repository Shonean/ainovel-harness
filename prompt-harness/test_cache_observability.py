# -*- coding: utf-8 -*-
"""隔离测试：_normalize_usage_cache / _infer_call_type（不调真实 LLM）。"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from prompt_harness.llm_client import _normalize_usage_cache, _infer_call_type


def test_normalize():
    # DeepSeek 原生字段：原样保留
    u = {"prompt_tokens": 100, "prompt_cache_hit_tokens": 80,
         "prompt_cache_miss_tokens": 20}
    _normalize_usage_cache(u)
    assert u["prompt_cache_hit_tokens"] == 80 and u["prompt_cache_miss_tokens"] == 20, u

    # ark 风格：cached_tokens -> 归一 + 补算 miss
    u = {"prompt_tokens": 100, "cached_tokens": 60}
    _normalize_usage_cache(u)
    assert u["prompt_cache_hit_tokens"] == 60, u
    assert u["prompt_cache_miss_tokens"] == 40, u

    # OpenAI/dashscope 风格：prompt_tokens_details.cached_tokens
    u = {"prompt_tokens": 200, "prompt_tokens_details": {"cached_tokens": 150}}
    _normalize_usage_cache(u)
    assert u["prompt_cache_hit_tokens"] == 150, u
    assert u["prompt_cache_miss_tokens"] == 50, u

    # 无任何缓存字段：不动
    u = {"prompt_tokens": 100}
    _normalize_usage_cache(u)
    assert "prompt_cache_hit_tokens" not in u, u

    # None / 非 dict：原样返回
    assert _normalize_usage_cache(None) is None
    assert _normalize_usage_cache("x") == "x"

    # hit > prompt_tokens 的病态值：miss 钳到 0
    u = {"prompt_tokens": 50, "cached_tokens": 99}
    _normalize_usage_cache(u)
    assert u["prompt_cache_miss_tokens"] == 0, u
    print("test_normalize OK")


def test_infer():
    # 模拟真实链：caller -> chat_completion(包装层) -> _infer_call_type
    # frame0=_infer frame1=包装层 frame2=caller -> 返回 caller 名
    def _fake_chat_completion():
        return _infer_call_type()

    def _caller():
        return _fake_chat_completion()

    ct = _caller()
    assert ct == "auto:_caller", ct

    # 模块顶层直接调用：frame2=<module>，返回 auto:<module>（可接受）
    assert _infer_call_type().startswith("auto:")
    print("test_infer OK")


if __name__ == "__main__":
    test_normalize()
    test_infer()
    print("ALL PASS")
