"""改编层 v0 — 对白说话人归一化单测（spec §7，真实样例）。

覆盖：正则（X道：“…”）、代词回填（唯一在场）、别名、无冒号引号句、
LLM 批量兜底（mock）、未知角色名拒绝、降级 narration + SPEAKER_UNRESOLVED、
语气词 → expression。LLM 一律 mock，不触网。
运行：pytest prompt-harness/tests/test_adaptation_dialogue.py -q
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

_PH_SRC = Path(__file__).resolve().parent.parent
if str(_PH_SRC) not in sys.path:
    sys.path.insert(0, str(_PH_SRC))

from prompt_harness.adaptation.dialogue import (  # noqa: E402
    DialogueLine, extract_expression, normalize_dialogues,
)


class _MockHook:
    """记录调用次数并按序返回固定 payload 的 mock llm_hook。"""

    def __init__(self, *payloads):
        self.payloads = list(payloads)
        self.calls = 0
        self.last_payload = None

    async def __call__(self, payload: dict):
        self.calls += 1
        self.last_payload = payload
        item = self.payloads[min(self.calls - 1, len(self.payloads) - 1)]
        if isinstance(item, Exception):
            raise item
        return item


@pytest.fixture()
def mock_llm_hook():
    def _make(*payloads):
        return _MockHook(*payloads)
    return _make


def _run(texts, present, known, llm_hook=None, alias_map=None):
    return asyncio.run(normalize_dialogues(
        texts, present, known, llm_hook=llm_hook, alias_map=alias_map))


def test_regex_speaker_with_tone():
    """真实样例：`陈守念对着空教室低声问道：“这是哪儿？”`。"""
    lines, warns = _run(
        ["陈守念对着空教室低声问道：“这是哪儿？”"],
        present=["陈守念"], known=["陈守念"])
    assert warns == []
    ln = lines[0]
    assert isinstance(ln, DialogueLine) and ln.kind == "dialogue"
    assert ln.speaker == "陈守念"
    assert ln.expression == "low"  # 低声 → expression
    assert ln.text == "这是哪儿？"
    assert not ln.unresolved


def test_regex_plain_quote():
    lines, _ = _run(['白纸人冷冷地说：“坐好。”'], present=["白纸人"], known=["白纸人"])
    assert lines[0].speaker == "白纸人"
    assert lines[0].expression == "cold"
    assert lines[0].text == "坐好。"


def test_alias_backfill():
    lines, _ = _run(
        ["守念把纸巾折起来塞进胸口口袋，一字一顿地念道：“守、念。”"],
        present=["陈守念"], known=["陈守念"],
        alias_map={"陈守念": ["守念"]})
    assert lines[0].speaker == "陈守念"  # 别名 → 规范名
    assert lines[0].expression == "emphatic"  # 一字一顿
    assert lines[0].text == "守、念。"


def test_pronoun_backfill_unique_present():
    """他 → 当前场景唯一角色。"""
    lines, warns = _run(
        ["他压低声音：“谁在门外？”"],
        present=["陈守念"], known=["陈守念"])
    assert warns == []
    assert lines[0].kind == "dialogue" and lines[0].speaker == "陈守念"
    assert lines[0].expression == "low"


def test_pronoun_ambiguous_falls_to_llm(mock_llm_hook):
    """在场不唯一 → 不回填，交 LLM 兜底解析（批量 1 次）。"""
    hook = mock_llm_hook({"speakers": {"0": "白纸人"}})
    lines, warns = _run(
        ["他压低声音：“谁在门外？”"],
        present=["陈守念", "白纸人"], known=["陈守念", "白纸人"], llm_hook=hook)
    assert warns == []
    assert lines[0].speaker == "白纸人"
    assert hook.calls == 1


def test_no_structure_goes_to_llm_and_resolves(mock_llm_hook):
    """无冒号/无引号结构的自由叙事体 → LLM 兜底。"""
    hook = mock_llm_hook({"speakers": {"0": "陈守念"}})
    lines, warns = _run(
        ["“有人吗？”他朝着走廊喊了一声。"],
        present=["陈守念"], known=["陈守念"], llm_hook=hook)
    assert warns == []
    assert lines[0].kind == "dialogue" and lines[0].speaker == "陈守念"


def test_llm_unknown_name_rejected_and_downgraded(mock_llm_hook):
    """LLM 返回名单之外的角色 → 非法，降级 narration 并警告。"""
    hook = mock_llm_hook({"speakers": {"0": "神秘新角色"}})
    lines, warns = _run(
        ["“有人吗？”他朝着走廊喊了一声。"],
        present=["陈守念"], known=["陈守念"], llm_hook=hook)
    codes = [w["code"] for w in warns]
    assert "SPEAKER_REJECTED" in codes and "SPEAKER_UNRESOLVED" in codes
    assert lines[0].kind == "narration"
    assert lines[0].text == "“有人吗？”他朝着走廊喊了一声。"


def test_llm_returns_none_degrades():
    """LLM 返回 None（调用失败）→ 降级 narration。"""
    lines, warns = _run(
        ["“有人吗？”他朝着走廊喊了一声。"],
        present=["陈守念"], known=["陈守念"], llm_hook=_MockHook(None))
    assert lines[0].kind == "narration"
    assert [w["code"] for w in warns] == ["SPEAKER_UNRESOLVED"]


def test_llm_exception_is_caught(mock_llm_hook):
    """LLM 抛异常 → 捕获后降级，不崩溃。"""
    lines, warns = _run(
        ["“有人吗？”他朝着走廊喊了一声。"],
        present=["陈守念"], known=["陈守念"],
        llm_hook=mock_llm_hook(RuntimeError("boom")))
    assert lines[0].kind == "narration"
    assert [w["code"] for w in warns] == ["SPEAKER_UNRESOLVED"]


def test_no_llm_hook_downgrades():
    """无 llm_hook（离线/测试）→ 未解析直接降级。"""
    lines, warns = _run(
        ["“有人吗？”他朝着走廊喊了一声。"],
        present=["陈守念"], known=["陈守念"], llm_hook=None)
    assert lines[0].kind == "narration"
    assert [w["code"] for w in warns] == ["SPEAKER_UNRESOLVED"]


def test_batch_single_call_for_multiple_pending(mock_llm_hook):
    """同一场景多条未解析对白合并为 1 次调用。"""
    hook = mock_llm_hook({"speakers": {"0": "陈守念", "1": "陈守念"}})
    lines, warns = _run(
        ["“有人吗？”他朝着走廊喊了一声。",
         "没有回应，只有挂钟的秒针停在那里的滴答声回响。"],
        present=["陈守念"], known=["陈守念"], llm_hook=hook)
    assert hook.calls == 1
    assert warns == []
    assert all(ln.speaker == "陈守念" for ln in lines)


def test_empty_text_line_downgrades():
    lines, warns = _run(["   "], present=["陈守念"], known=["陈守念"])
    assert lines[0].kind == "narration"
    assert [w["code"] for w in warns] == ["SPEAKER_UNRESOLVED"]


def test_extract_expression_map():
    assert extract_expression("低声") == "low"
    assert extract_expression("急道") == "urgent"
    assert extract_expression("颤抖着") == "trembling"
    assert extract_expression("笑着") == "smiling"
    assert extract_expression("平静地") == "calm"
    assert extract_expression("莫名其妙地说") == ""  # 识别不了留空
