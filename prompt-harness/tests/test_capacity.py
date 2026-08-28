"""prompt 容量估算单元测试（纯确定性，0 LLM，不烧 token）。

覆盖：单调性（信息多 > 少）、诊断打标（薄/正常）、补齐护栏（fact_drift 回退）。
"""
from __future__ import annotations

import asyncio

from prompt_harness.capacity import (
    estimate_arc_capacity,
    diagnose_arc_capacity,
    enrich_l1,
    info_units,
    model_window_chars,
)

THIN_L1 = "主角去复仇。"
RICH_L1 = (
    "林九穿越到修仙界，为给妹妹治病加入魔门，"
    "在宗门大比暴露身份，被仇家追杀，反杀夺回仙丹。"
)


def test_info_units_deterministic():
    """信息单元提取纯确定性、单调。"""
    thin = info_units(THIN_L1)
    rich = info_units(RICH_L1)
    assert thin["beats"] >= 1
    assert rich["beats"] > thin["beats"]
    assert rich["conflicts"] >= 1
    # 多次调用一致
    assert info_units(RICH_L1) == rich


def test_estimate_monotonic():
    """信息越多容量越高（核心不变式）。"""
    thin = estimate_arc_capacity(THIN_L1)
    rich = estimate_arc_capacity(RICH_L1)
    assert rich["total_chars_est"] > thin["total_chars_est"]
    assert rich["total_chapters_est"] >= thin["total_chapters_est"]
    assert thin["total_chapters_est"] >= 1
    # 单章容量在健康带内
    assert 1200 <= thin["cap_chapter_est"] <= 3000


def test_estimate_no_llm():
    """estimate_arc_capacity 无网络副作用（即使 chat_completion 抛错也不影响）。"""
    import prompt_harness.capacity as cap

    orig = None
    import prompt_harness.llm_client as lc
    if hasattr(lc, "chat_completion"):
        orig = lc.chat_completion
        lc.chat_completion = None  # type: ignore  # 打 None 模拟故障

    try:
        est = estimate_arc_capacity(RICH_L1)
        assert est["total_chars_est"] > 0
    finally:
        if orig is not None:
            lc.chat_completion = orig


def test_diagnose_thin():
    """极薄 l1 → 密度偏薄 + should_fill。"""
    d = asyncio.run(diagnose_arc_capacity(THIN_L1, use_facts=False))
    assert d["dimensions"]["density"]["gap"] == "thin"
    assert d["should_fill"] is True
    assert d["capacity"]["total_chapters_est"] == 1


def test_diagnose_content_dimension():
    """RICH_L1 内容维度不缺冲突；use_facts=False 时缺实体（n_entity=0 触发）。"""
    d = asyncio.run(diagnose_arc_capacity(RICH_L1, use_facts=False))
    missing = d["dimensions"]["content"]["missing"]
    assert "冲突" not in missing
    assert "实体" in missing  # use_facts=False → 无实体计数 → 标缺（降级保守）


async def _fake_chat_filled(**kwargs):
    return {"content": "林九穿越到修仙界，为救妹妹加入魔门，在宗门大比暴露身份被仇家追杀，反杀夺回仙丹，躲进青羊山。",
            "usage": None, "error": None}


async def _fake_chat_drift(**kwargs):
    # 故意漏掉原事实「林九」→ 应触发 fact_drift 回退
    return {"content": "某人穿越到修仙界，为救妹妹加入魔门。",
            "usage": None, "error": None}


def test_enrich_guard_fact_drift(monkeypatch):
    """补齐护栏：原 facts 丢失 → 回退原 l1。"""
    from prompt_harness import llm_client
    monkeypatch.setattr(llm_client, "chat_completion", _fake_chat_drift)
    diag = {
        "dimensions": {"density": {"gap": "thin"},
                       "content": {"missing": ["冲突"]},
                       "quality": {"ai_flavor_risk": True}},
        "facts": ["林九", "魔门"],
    }
    r = asyncio.run(enrich_l1("林九去复仇", diag))
    assert r["ok"] is False
    assert r["reason"] == "fact_drift"
    assert r["text"] == "林九去复仇"  # 回退原 l1


def test_enrich_filled(monkeypatch):
    """补齐成功：护栏通过，返回补齐版。"""
    from prompt_harness import llm_client
    monkeypatch.setattr(llm_client, "chat_completion", _fake_chat_filled)
    diag = {
        "dimensions": {"density": {"gap": "thin"},
                       "content": {"missing": ["具体地点"]},
                       "quality": {"ai_flavor_risk": True}},
        "facts": ["林九", "魔门", "宗门大比"],
    }
    r = asyncio.run(enrich_l1("林九穿越到修仙界，为给妹妹治病加入魔门，在宗门大比暴露身份。", diag))
    assert r["ok"] is True
    # 原 facts 都在补齐版里
    for f in diag["facts"]:
        assert f in r["text"]


def test_model_window_chars():
    assert model_window_chars("qwen3.5-flash") >= 30000
    assert model_window_chars(None) == 30000
    assert model_window_chars("unknown-model") == 30000
