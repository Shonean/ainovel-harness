"""Tier-A：style_sampler 语义重排。

验证：semantic=False 行为不变；semantic=True 用 stub embed 按余弦重排。
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

SCRIPTS_DIR = PLUGIN_ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))


def _make_sampler(tmp_path, embed_key="test-key"):
    from data_modules.config import DataModulesConfig
    from data_modules.style_sampler import StyleSampler, StyleSample
    os.environ["EMBED_API_KEY"] = embed_key
    cfg = DataModulesConfig(project_root=tmp_path)
    cfg.ensure_dirs()
    sampler = StyleSampler(cfg)
    # 插入 3 个样本
    sampler.add_sample(StyleSample(id="s1", chapter=1, scene_type="描写",
                                   content="天空湛蓝如洗，白云悠悠", score=0.9, tags=[]))
    sampler.add_sample(StyleSample(id="s2", chapter=1, scene_type="描写",
                                   content="刀光剑影，血溅三尺", score=0.95, tags=[]))
    sampler.add_sample(StyleSample(id="s3", chapter=1, scene_type="描写",
                                   content="她轻声叹息，泪落如珠", score=0.85, tags=[]))
    return sampler


def test_select_semantic_false_returns_score_order(tmp_path):
    sampler = _make_sampler(tmp_path)
    # max 大到能取全部；无语义重排 → 按 score 倒序
    out = sampler.select_samples_for_chapter(
        "描写场景", target_types=["描写"], max_samples=3, semantic=False)
    ids = [s.id for s in out]
    assert ids == ["s2", "s1", "s3"]  # 0.95, 0.9, 0.85


def test_select_semantic_true_reranks_by_query(monkeypatch, tmp_path):
    sampler = _make_sampler(tmp_path)

    # stub embed：style_query 含"战斗" → 与 s2(刀光剑影) 最近
    def fake_embed(texts):
        vecs = []
        for t in texts:
            if "战斗" in t or "刀光" in t or "血溅" in t:
                vecs.append([1.0, 0.0])
            else:
                vecs.append([0.0, 1.0])
        return vecs

    class _Stub:
        async def embed(self, texts):
            return fake_embed(texts)
        async def embed_batch(self, texts):
            return fake_embed(texts)
        async def close(self):
            pass

    import data_modules.api_client as ac
    monkeypatch.setattr(ac, "get_client", lambda cfg: _Stub())

    out = sampler.select_samples_for_chapter(
        "战斗描写", target_types=["描写"], max_samples=2,
        semantic=True, style_query="战斗 场景")
    ids = [s.id for s in out]
    # s2 与 query 最近，应排第一
    assert ids[0] == "s2"
    assert len(out) == 2


def test_select_semantic_no_key_ignores(monkeypatch, tmp_path):
    """无 embed key 时 semantic=True 不报错、不重排（退回 score 序）。"""
    sampler = _make_sampler(tmp_path, embed_key="")
    out = sampler.select_samples_for_chapter(
        "描写", target_types=["描写"], max_samples=2, semantic=True)
    # 无 key → pool_cap=max_samples，按 score 序
    assert [s.id for s in out] == ["s2", "s1"]
