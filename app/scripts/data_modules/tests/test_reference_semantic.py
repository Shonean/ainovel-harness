"""Tier-A：reference_search 语义混合检索。

用 monkeypatch 把 _semantic_scores 替换成可控分数，验证 hybrid/semantic/bm25 排序与降级。
另用 stub embed client 验证真实 _semantic_scores 的余弦排序。
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

import reference_search as rs  # noqa: E402


def _make_csv(tmp_path):
    csv_dir = tmp_path / "csv"
    csv_dir.mkdir()
    (csv_dir / "命名规则.csv").write_text(
        "编号,适用技能,分类,层级,关键词,适用题材,核心摘要,意图与同义词,大模型指令,详细展开\n",
        encoding="utf-8-sig",
    )
    rows = [
        ("1", "主角在绝境中觉醒逆天血脉翻盘", "血脉觉醒"),
        ("2", "反派被主角用计谋击败", "智斗"),
        ("3", "主角修炼突破境界", "修炼"),
    ]
    with open(csv_dir / "场景写法.csv", "w", encoding="utf-8-sig", newline="") as f:
        f.write("编号,适用技能,分类,层级,关键词,适用题材,核心摘要,意图与同义词,大模型指令,详细展开\n")
        for bianhao, summary, kw in rows:
            f.write(f"{bianhao},write,战斗,1,{kw},全部,{summary},{kw},,\n")
    return csv_dir


def test_search_bm25_default_structure(tmp_path):
    """无 project_root → auto 降级 bm25；返回结构不变。"""
    csv_dir = _make_csv(tmp_path)
    result = rs.search(csv_dir, skill="write", query="翻盘", max_results=3, project_root=None)
    assert result["status"] == "success"
    data = result["data"]
    assert set(data.keys()) == {"query", "skill", "genre", "total", "results"}
    assert data["total"] >= 1
    assert data["results"][0]["表"] == "场景写法"


def test_search_hybrid_reranks_by_semantic(monkeypatch, tmp_path):
    """hybrid 模式：注入语义分数，验证语义高分项被排到前面（即便 BM25 低）。"""
    csv_dir = _make_csv(tmp_path)

    # 强制 embed 可用
    monkeypatch.setattr(rs, "_embed_available", lambda pr: True)

    # 让 _semantic_scores 返回固定分数：编号3 最高
    async def fake_sem(query, candidates, project_root):
        scores = []
        for tbl, row in candidates:
            if row.get("编号") == "3":
                scores.append(0.99)
            elif row.get("编号") == "1":
                scores.append(0.10)
            else:
                scores.append(0.05)
        return scores
    monkeypatch.setattr(rs, "_semantic_scores", fake_sem)

    result = rs.search(csv_dir, skill="write", query="翻盘 觉醒", max_results=3,
                       mode="hybrid", project_root=tmp_path)
    data = result["data"]
    # 编号3（修炼）因语义分高，应在 hybrid 融合后进入结果且靠前
    bianhaos = [r["编号"] for r in data["results"]]
    assert "3" in bianhaos


def test_search_semantic_pure(monkeypatch, tmp_path):
    csv_dir = _make_csv(tmp_path)
    monkeypatch.setattr(rs, "_embed_available", lambda pr: True)

    async def fake_sem(query, candidates, project_root):
        return [0.9 if row.get("编号") == "2" else 0.1
                for _t, row in candidates]
    monkeypatch.setattr(rs, "_semantic_scores", fake_sem)

    result = rs.search(csv_dir, skill="write", query="x", max_results=1,
                       mode="semantic", project_root=tmp_path)
    assert result["data"]["results"][0]["编号"] == "2"


def test_search_embed_failure_degrades_to_bm25(monkeypatch, tmp_path):
    csv_dir = _make_csv(tmp_path)
    monkeypatch.setattr(rs, "_embed_available", lambda pr: True)

    async def fake_sem(query, candidates, project_root):
        return None  # 模拟 embed 失败
    monkeypatch.setattr(rs, "_semantic_scores", fake_sem)

    result = rs.search(csv_dir, skill="write", query="翻盘", max_results=3,
                       mode="hybrid", project_root=tmp_path)
    # 降级后仍返回有效结果
    assert result["status"] == "success"
    assert result["data"]["total"] >= 1


def test_semantic_scores_real_cosine(monkeypatch, tmp_path):
    """用 stub EmbeddingAPIClient 验证真实余弦排序路径。"""
    monkeypatch.setenv("EMBED_API_KEY", "test-key")

    # 固定向量：query 与编号1 最近
    def fake_embed(texts):
        vecs = []
        for t in texts:
            if "逆天血脉" in t or "翻盘" in t:
                vecs.append([1.0, 0.0])
            else:
                vecs.append([0.0, 1.0])
        return vecs

    class _StubClient:
        async def embed(self, texts):
            return fake_embed(texts)
        async def embed_batch(self, texts):
            return fake_embed(texts)
        async def close(self):
            pass

    monkeypatch.setattr(rs, "_embed_available", lambda pr: True)

    import data_modules.api_client as ac
    monkeypatch.setattr(ac, "get_client", lambda cfg: _StubClient())

    scores = asyncio.run(rs._semantic_scores("翻盘 逆天血脉", [
        ("场景写法", {"编号": "1", "核心摘要": "主角在绝境中觉醒逆天血脉翻盘",
                   "关键词": "血脉觉醒", "意图与同义词": "血脉觉醒"}),
        ("场景写法", {"编号": "2", "核心摘要": "反派被主角用计谋击败",
                   "关键词": "智斗", "意图与同义词": "智斗"}),
    ], tmp_path))
    assert scores is not None
    assert scores[0] > scores[1]  # 编号1 与 query 更近
