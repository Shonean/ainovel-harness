#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import pytest

from reference_search import _rerank_results


@pytest.mark.asyncio
async def test_rerank_results_reorders_candidates(monkeypatch, tmp_path):
    """验证 rerank 后返回的索引按 relevance_score 降序排列。"""

    class FakeClient:
        async def rerank(self, query, documents, top_n=None):
            # 假设 rerank 把 documents 的逆序作为结果
            return [
                {"index": i, "relevance_score": len(documents) - i}
                for i in range(len(documents))
            ]

        async def close(self):
            pass

    monkeypatch.setenv("EMBED_API_KEY", "fake")
    monkeypatch.setenv("RERANK_API_KEY", "fake")

    def fake_get_client(cfg):
        return FakeClient()

    import reference_search
    monkeypatch.setattr(reference_search, "_ensure_data_modules", lambda: None)

    # 由于 _rerank_results 内部 import get_client，我们用 sys.modules 注入 stub
    import data_modules.api_client as api_client_mod
    monkeypatch.setattr(api_client_mod, "get_client", fake_get_client)

    candidates = [
        ("table1", {"关键词": "a", "意图与同义词": "", "详细展开": "doc1"}),
        ("table1", {"关键词": "b", "意图与同义词": "", "详细展开": "doc2"}),
        ("table1", {"关键词": "c", "意图与同义词": "", "详细展开": "doc3"}),
    ]
    ranked_indices = [0, 1, 2]

    from data_modules.config import DataModulesConfig
    cfg = DataModulesConfig(project_root=tmp_path, rerank_api_key="fake", rerank_enabled=True)

    result = await _rerank_results("query", candidates, ranked_indices, tmp_path, top_n=3)
    assert result == [2, 1, 0]
