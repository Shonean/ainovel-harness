"""Tier-A：验证 agent_runner 的 OpenAI 兼容 function-calling 循环。

用 FakeOpenAI client 模拟分片 tool_calls + usage，覆盖：
- 文本 delta 流式推送
- tool_calls 分片累积 → 本地执行 → role=tool 回填 → 下一轮
- usage 捕获与计费
- finish_reason=stop 退出
- AskUser 工具在 run() 内的挂起/续跑
"""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

import pytest

PLUGIN_ROOT = Path(__file__).resolve().parents[3]
if str(PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT))


# ---- 轻量 fake 对象，匹配 agent_runner 的属性访问 ----

class _Delta:
    def __init__(self, content=None, tool_calls=None):
        self.content = content
        self.tool_calls = tool_calls


class _TC:
    def __init__(self, index, id=None, name=None, arguments=None):
        self.index = index
        self.id = id
        self.function = type("_Fn", (), {"name": name, "arguments": arguments})()


class _Choice:
    def __init__(self, delta, finish_reason=None):
        self.delta = delta
        self.finish_reason = finish_reason


class _Chunk:
    def __init__(self, choices=None, usage=None):
        self.choices = choices if choices is not None else []
        self.usage = usage


class _Usage:
    def __init__(self, p, c, cached=None):
        self.prompt_tokens = p
        self.completion_tokens = c
        self.prompt_tokens_details = type("_D", (), {"cached_tokens": cached})()


class _FakeStream:
    """async iterable：按顺序吐出 chunk。"""
    def __init__(self, chunks):
        self._chunks = list(chunks)
        self._i = 0

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self._i >= len(self._chunks):
            raise StopAsyncIteration
        ch = self._chunks[self._i]
        self._i += 1
        return ch


class _Completions:
    def __init__(self, fake):
        self._fake = fake

    async def create(self, **kwargs):
        self._fake.call_count += 1
        self._fake.last_kwargs = kwargs
        chunks = self._fake._next_chunks()
        return _FakeStream(chunks)


class _Chat:
    def __init__(self, fake):
        self.completions = _Completions(fake)


class FakeOpenAI:
    """记录每次 create 的 kwargs，按注册顺序返回 chunk 序列。"""
    def __init__(self):
        self._sequences = []
        self.call_count = 0
        self.last_kwargs = {}
        self.chat = _Chat(self)

    def add_sequence(self, chunks):
        self._sequences.append(list(chunks))

    def _next_chunks(self):
        if not self._sequences:
            return []
        return self._sequences.pop(0)


# ---------------------------------------------------------------------------
# 测试
# ---------------------------------------------------------------------------

def _make_runner(tmp_path, fake, **kw):
    pytest.importorskip("openai")
    os.environ.setdefault("ARK_API_KEY", "test-key")
    from dashboard.agent_runner import AnthropicAgentRunner
    r = AnthropicAgentRunner(project_root=tmp_path, model="ark-code-latest",
                             system_prompt="你是助手", **kw)
    r._client = fake  # 注入 fake
    return r


def test_run_streams_text_and_tool_call_and_usage(tmp_path):
    (tmp_path / "a.md").write_text("hello", encoding="utf-8")
    fake = FakeOpenAI()
    # Turn 1：文本 + Read 工具调用（arguments 分两片）+ finish + usage
    fake.add_sequence([
        _Chunk([_Choice(_Delta(content="正在分"))]),
        _Chunk([_Choice(_Delta(content="析"))]),
        _Chunk([_Choice(_Delta(tool_calls=[_TC(0, id="t1", name="Read")]),
                         finish_reason=None)]),
        _Chunk([_Choice(_Delta(tool_calls=[_TC(0, arguments='{"file_path":')]),
                         finish_reason=None)]),
        _Chunk([_Choice(_Delta(tool_calls=[_TC(0, arguments=' "a.md"}')]),
                         finish_reason="tool_calls")]),
        _Chunk(usage=_Usage(120, 30, cached=5)),
    ])
    # Turn 2：最终文本 + stop + usage
    fake.add_sequence([
        _Chunk([_Choice(_Delta(content="完成"))]),
        _Chunk([_Choice(_Delta(), finish_reason="stop")]),
        _Chunk(usage=_Usage(200, 12)),
    ])

    events = []

    async def on_event(ev):
        events.append(ev)

    async def on_token(t):
        pass

    r = _make_runner(tmp_path, fake, allowed_tools=["Read"],
                     on_event=on_event, on_token=on_token, agent_name="context")

    final = asyncio.run(r.run("读 nope.md"))

    assert "正在分析" in final
    assert "完成" in final
    # usage 捕获：最后一轮 input=200 output=12
    assert r.last_usage.get("input_tokens") == 200
    assert r.last_usage.get("output_tokens") == 12
    # 计费记录写入 USAGE
    from dashboard.usage import USAGE
    assert any(rec.agent == "context" for rec in USAGE._records[-3:])
    # 工具事件
    assert any(e.get("phase") == "tool_use" and e.get("name") == "Read" for e in events)
    assert any(e.get("phase") == "tool_result" for e in events)
    # create 被调两次（两轮），序列耗尽
    assert fake.call_count == 2
    assert fake.last_kwargs["model"] == "ark-code-latest"
    assert "tools" in fake.last_kwargs
    assert len(r._client._sequences) == 0


def test_run_ask_user_suspend_and_resume(tmp_path):
    fake = FakeOpenAI()
    # Turn 1：调 AskUser
    fake.add_sequence([
        _Chunk([_Choice(_Delta(tool_calls=[_TC(0, id="a1", name="AskUser")]),
                         finish_reason=None)]),
        _Chunk([_Choice(_Delta(tool_calls=[_TC(0, arguments='{"question":"继续?","options":["是","否"]}')]),
                         finish_reason="tool_calls")]),
    ])
    # Turn 2：stop
    fake.add_sequence([
        _Chunk([_Choice(_Delta(content="好的"))]),
        _Chunk([_Choice(_Delta(), finish_reason="stop")]),
    ])

    captured = {}

    async def on_ask_user(prompt):
        captured["prompt"] = prompt
        return {"answer": "是"}

    r = _make_runner(tmp_path, fake, allowed_tools=["AskUser"], on_ask_user=on_ask_user)

    final = asyncio.run(r.run("问用户"))

    assert captured["prompt"]["question"] == "继续?"
    assert captured["prompt"]["options"] == ["是", "否"]
    assert "好的" in final


def test_run_stop_immediately_without_tools(tmp_path):
    fake = FakeOpenAI()
    fake.add_sequence([
        _Chunk([_Choice(_Delta(content="直接回答"))]),
        _Chunk([_Choice(_Delta(), finish_reason="stop")]),
        _Chunk(usage=_Usage(10, 8)),
    ])

    r = _make_runner(tmp_path, fake, allowed_tools=["Read"])
    final = asyncio.run(r.run("hi"))
    assert final == "直接回答"
    assert r.last_usage.get("input_tokens") == 10
