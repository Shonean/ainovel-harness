#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
审查报告语义索引

对每章量化审计报告中的 findings 做 embedding 索引，写作时按当前章节大纲
语义召回历史上最相关的审查问题，避免重复犯同类错误。
"""

from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np

from .config import get_config, DataModulesConfig
from .vector_index import VectorIndex


class AuditEmbeddingIndex:
    """审查报告语义索引。"""

    REPORT_PATH = ".ainovel/tmp/quant_audit.json"

    def __init__(self, config: Optional[DataModulesConfig] = None):
        self.config = config or get_config()
        self._vector_index: Optional[VectorIndex] = None

    @property
    def index_path(self) -> Path:
        return self.config.faiss_index_dir / "audit_findings.faiss"

    @property
    def reports_dir(self) -> Path:
        return self.config.ainovel_dir / "tmp"

    def _embed_available(self) -> bool:
        return bool((self.config.embed_api_key or "").strip())

    def _probe_embedding_dim(self, text: str) -> Optional[int]:
        try:
            from .api_client import get_client
        except Exception:
            return None
        client = get_client(self.config)
        try:
            embs = asyncio.run(client.embed([text]))
            if embs and embs[0]:
                return len(embs[0])
        except Exception:
            pass
        finally:
            try:
                asyncio.run(client.close())
            except Exception:
                pass
        return None

    def _get_vector_index(self) -> Optional[VectorIndex]:
        if self._vector_index is not None:
            return self._vector_index
        if not self._embed_available():
            return None

        dim = self._probe_embedding_dim("审查报告语义索引")
        if dim is None:
            return None

        index = VectorIndex(
            dim=dim,
            index_path=self.index_path,
            use_faiss=self.config.faiss_index_enabled,
        )
        if not index.load():
            self._rebuild_index(index)
        self._vector_index = index
        return index

    def rebuild(self) -> bool:
        """强制重建索引。"""
        dim = self._probe_embedding_dim("审查报告语义索引")
        if dim is None:
            return False
        index = VectorIndex(
            dim=dim,
            index_path=self.index_path,
            use_faiss=self.config.faiss_index_enabled,
        )
        self._rebuild_index(index)
        self._vector_index = index
        return True

    def _load_all_findings(self) -> List[Dict[str, Any]]:
        """加载所有历史审查报告中的 findings。"""
        findings: List[Dict[str, Any]] = []
        if not self.reports_dir.is_dir():
            return findings

        for path in sorted(self.reports_dir.glob("quant_audit*.json")):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                chapter = data.get("chapter", 0)
                for f in data.get("findings", []):
                    findings.append({
                        "id": f"{path.stem}_{f.get('code', 'unknown')}_{len(findings)}",
                        "chapter": chapter,
                        "code": f.get("code", ""),
                        "severity": f.get("severity", ""),
                        "metric": f.get("metric", ""),
                        "description": f.get("description", ""),
                        "evidence": f.get("evidence", []),
                    })
            except Exception:
                continue
        return findings

    def _rebuild_index(self, index: VectorIndex) -> None:
        findings = self._load_all_findings()
        if not findings:
            return
        try:
            from .api_client import get_client
        except Exception:
            return

        async def _embed():
            client = get_client(self.config)
            try:
                texts = [self._finding_to_text(f) for f in findings]
                return await client.embed_batch(texts)
            finally:
                await client.close()

        embeddings = asyncio.run(_embed())
        valid = [(f, emb) for f, emb in zip(findings, embeddings) if emb is not None]
        if not valid:
            return
        ids = [f["id"] for f, _ in valid]
        vectors = np.array([emb for _, emb in valid], dtype=np.float32)
        index.build(vectors, ids)
        index.save()

    @staticmethod
    def _finding_to_text(finding: Dict[str, Any]) -> str:
        parts = [
            f"第{finding.get('chapter', 0)}章",
            finding.get("metric", ""),
            finding.get("description", ""),
        ]
        return " ".join(p for p in parts if p)

    def add_report(self, report: Dict[str, Any]) -> None:
        """新增一份审查报告到索引。"""
        index = self._get_vector_index()
        if index is None:
            return
        chapter = report.get("chapter", 0)
        findings = report.get("findings", [])
        if not findings:
            return

        try:
            from .api_client import get_client
        except Exception:
            return

        async def _embed_and_add():
            client = get_client(self.config)
            try:
                items = []
                texts = []
                for idx, f in enumerate(findings):
                    item = {
                        "id": f"ch{chapter}_{f.get('code', 'unknown')}_{idx}",
                        "chapter": chapter,
                        "code": f.get("code", ""),
                        "severity": f.get("severity", ""),
                        "metric": f.get("metric", ""),
                        "description": f.get("description", ""),
                        "evidence": f.get("evidence", []),
                    }
                    items.append(item)
                    texts.append(self._finding_to_text(item))
                embs = await client.embed_batch(texts)
                valid_ids = []
                valid_vectors = []
                for item, emb in zip(items, embs):
                    if emb is not None:
                        valid_ids.append(item["id"])
                        valid_vectors.append(emb)
                if valid_vectors:
                    index.add(np.array(valid_vectors, dtype=np.float32), valid_ids)
                    index.save()
            finally:
                await client.close()

        asyncio.run(_embed_and_add())

    def search_related(
        self,
        query: str,
        current_chapter: Optional[int] = None,
        top_k: int = 3,
    ) -> List[Dict[str, Any]]:
        """检索与 query 最相关的历史审查问题。"""
        index = self._get_vector_index()
        if index is None:
            return []

        try:
            from .api_client import get_client
        except Exception:
            return []

        async def _search():
            client = get_client(self.config)
            try:
                embs = await client.embed([query if query else " "])
                if not embs or embs[0] is None:
                    return []
                qv = np.array(embs[0], dtype=np.float32)
                return index.search(qv, top_k=max(top_k * 3, 10))
            finally:
                await client.close()

        results = asyncio.run(_search())
        if not results:
            return []

        all_findings = self._load_all_findings()
        # 也包含新增但未持久化到文件中的 findings？暂时只从文件加载
        finding_map = {f["id"]: f for f in all_findings}

        out: List[Dict[str, Any]] = []
        for rid, score in results:
            finding = finding_map.get(rid)
            if finding is None:
                continue
            if current_chapter is not None and finding.get("chapter") == current_chapter:
                # 通常更关注其他章节的历史问题，但当前章的问题也可以保留
                pass
            out.append({**finding, "score": round(score, 4)})
            if len(out) >= top_k:
                break
        return out


def main():
    import argparse
    parser = argparse.ArgumentParser(description="审查报告语义索引")
    parser.add_argument("--project-root", required=True, help="项目根目录")
    parser.add_argument("--query", required=True, help="查询文本")
    parser.add_argument("--chapter", type=int, default=None, help="当前章节号")
    parser.add_argument("--top-k", type=int, default=3, help="返回条数")
    parser.add_argument("--rebuild", action="store_true", help="强制重建索引")
    args = parser.parse_args()

    from .config import load_user_env
    load_user_env()

    config = get_config(Path(args.project_root))
    index = AuditEmbeddingIndex(config)
    if args.rebuild:
        index.rebuild()
    results = index.search_related(args.query, current_chapter=args.chapter, top_k=args.top_k)
    print(json.dumps({"status": "success", "data": results}, ensure_ascii=False))


if __name__ == "__main__":
    main()
