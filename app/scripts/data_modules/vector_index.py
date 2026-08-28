#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
统一的向量索引管理器。

提供 FAISS 加速语义检索的能力，当 FAISS 不可用时自动降级为 numpy 暴力搜索。
设计目标：供 style_sampler、foreshadowing_retriever、audit report、writing guidance
等模块共享同一套索引构建/加载/搜索接口。
"""

from __future__ import annotations

import json
import logging
import struct
from pathlib import Path
from typing import Iterable, List, Optional, Tuple

import numpy as np

logger = logging.getLogger(__name__)

# 尝试导入 FAISS；若失败，全程使用 numpy fallback。
try:
    import faiss

    _FAISS_AVAILABLE = True
except Exception:
    faiss = None  # type: ignore
    _FAISS_AVAILABLE = False


class VectorIndex:
    """
    向量索引封装。

    - 使用 FAISS IndexFlatIP（内积），写入前对向量做 L2 归一化，使内积等价于余弦相似度。
    - FAISS 不可用时，透明降级为 numpy 暴力搜索。
    - 索引持久化包括：.faiss 索引文件 + _ids.json id 映射。
    """

    def __init__(
        self,
        dim: int,
        index_path: Path,
        *,
        use_faiss: Optional[bool] = None,
    ) -> None:
        self.dim = dim
        self.index_path = Path(index_path)
        self.id_map_path = self.index_path.with_suffix(".ids.json")

        if use_faiss is None:
            use_faiss = _FAISS_AVAILABLE
        self.use_faiss = use_faiss and _FAISS_AVAILABLE

        self._index: Optional["faiss.Index"] = None
        self._vectors: Optional[np.ndarray] = None
        self._ids: List[str] = []

    # ------------------------------------------------------------------
    # 构建与加载
    # ------------------------------------------------------------------
    def build(
        self,
        vectors: np.ndarray,
        ids: List[str],
        *,
        normalize: bool = True,
    ) -> None:
        """从零构建索引。vectors 形状应为 (n, dim)。"""
        if len(vectors) != len(ids):
            raise ValueError(f"vectors ({len(vectors)}) 与 ids ({len(ids)}) 长度不一致")
        if vectors.shape[1] != self.dim:
            raise ValueError(f"向量维度 {vectors.shape[1]} 与索引维度 {self.dim} 不一致")

        self._ids = list(ids)
        self._vectors = self._normalize(vectors) if normalize else vectors.astype(np.float32)

        if self.use_faiss and len(self._vectors) > 0:
            self._index = faiss.IndexFlatIP(self.dim)  # type: ignore
            self._index.add(self._vectors)  # type: ignore
        else:
            self._index = None

    def load(self) -> bool:
        """从磁盘加载索引。返回是否成功。"""
        if not self.index_path.exists() or not self.id_map_path.exists():
            return False
        try:
            self._ids = json.loads(self.id_map_path.read_text(encoding="utf-8"))
            if self.use_faiss:
                self._index = faiss.read_index(str(self.index_path))  # type: ignore
                self._vectors = None
            else:
                # 读取二进制向量数据 (n, dim) float32
                raw = self.index_path.read_bytes()
                n = len(self._ids)
                expected = n * self.dim * 4
                if len(raw) != expected:
                    logger.warning(
                        "向量文件大小与 id 数量不匹配: %d bytes, expected %d", len(raw), expected
                    )
                    return False
                self._vectors = np.frombuffer(raw, dtype=np.float32).reshape(n, self.dim)
                self._index = None
            return True
        except Exception as exc:
            logger.warning("加载向量索引失败 %s: %s", self.index_path, exc)
            self._index = None
            self._vectors = None
            self._ids = []
            return False

    def save(self) -> None:
        """持久化索引到磁盘。"""
        self.index_path.parent.mkdir(parents=True, exist_ok=True)
        self.id_map_path.write_text(json.dumps(self._ids, ensure_ascii=False), encoding="utf-8")
        if self.use_faiss and self._index is not None:
            faiss.write_index(self._index, str(self.index_path))  # type: ignore
        elif self._vectors is not None:
            self.index_path.write_bytes(self._vectors.astype(np.float32).tobytes())

    # ------------------------------------------------------------------
    # 增删改
    # ------------------------------------------------------------------
    def add(
        self,
        vectors: np.ndarray,
        ids: List[str],
        *,
        normalize: bool = True,
    ) -> None:
        """增量添加向量。若存在重复 id，先移除旧向量再添加。"""
        if len(vectors) != len(ids):
            raise ValueError("vectors 与 ids 长度不一致")
        if vectors.shape[1] != self.dim:
            raise ValueError(f"向量维度 {vectors.shape[1]} 与索引维度 {self.dim} 不一致")

        # 移除已存在的 id
        existing = set(self._ids)
        dup_ids = [rid for rid in ids if rid in existing]
        if dup_ids:
            self.remove(dup_ids)

        new_vectors = self._normalize(vectors) if normalize else vectors.astype(np.float32)
        self._ids.extend(ids)

        if self.use_faiss and self._index is not None:
            self._index.add(new_vectors)  # type: ignore
        else:
            if self._vectors is None:
                self._vectors = new_vectors
            else:
                self._vectors = np.vstack([self._vectors, new_vectors])

    def remove(self, ids: Iterable[str]) -> None:
        """按 id 删除向量。FAISS IndexFlatIP 不支持直接删除，因此重建索引。"""
        remove_set = set(ids)
        keep_indices = [i for i, rid in enumerate(self._ids) if rid not in remove_set]
        if len(keep_indices) == len(self._ids):
            return

        self._ids = [self._ids[i] for i in keep_indices]
        if self._vectors is not None:
            self._vectors = self._vectors[keep_indices]

        if self.use_faiss:
            if len(self._vectors or []) > 0:
                self._index = faiss.IndexFlatIP(self.dim)  # type: ignore
                self._index.add(self._vectors)  # type: ignore
            else:
                self._index = None

    # ------------------------------------------------------------------
    # 搜索
    # ------------------------------------------------------------------
    def search(
        self,
        query_vector: np.ndarray,
        top_k: int,
        *,
        score_threshold: Optional[float] = None,
    ) -> List[Tuple[str, float]]:
        """
        语义搜索。

        返回 [(id, score), ...]，按 score 降序排列。
        由于使用归一化向量 + IndexFlatIP，score 等价于余弦相似度，范围 [-1, 1]。
        """
        if query_vector.shape[0] != self.dim:
            raise ValueError(f"查询向量维度 {query_vector.shape[0]} 与索引维度 {self.dim} 不一致")
        if not self._ids:
            return []

        top_k = min(top_k, len(self._ids))
        q = self._normalize(query_vector.reshape(1, -1))

        if self.use_faiss and self._index is not None:
            scores, indices = self._index.search(q, top_k)  # type: ignore
            results = []
            for score, idx in zip(scores[0], indices[0]):
                if idx < 0 or idx >= len(self._ids):
                    continue
                results.append((self._ids[idx], float(score)))
        else:
            results = self._numpy_search(q[0], top_k)

        if score_threshold is not None:
            results = [(rid, s) for rid, s in results if s >= score_threshold]
        return results

    def _numpy_search(self, query: np.ndarray, top_k: int) -> List[Tuple[str, float]]:
        """numpy 暴力搜索 fallback。"""
        if self._vectors is None or len(self._vectors) == 0:
            return []
        similarities = self._vectors @ query
        top_indices = np.argsort(similarities)[::-1][:top_k]
        return [(self._ids[int(i)], float(similarities[i])) for i in top_indices]

    # ------------------------------------------------------------------
    # 工具
    # ------------------------------------------------------------------
    @property
    def is_loaded(self) -> bool:
        return self._index is not None or self._vectors is not None

    @property
    def size(self) -> int:
        return len(self._ids)

    @staticmethod
    def _normalize(vectors: np.ndarray) -> np.ndarray:
        """L2 归一化，使内积等价于余弦相似度。"""
        arr = vectors.astype(np.float32)
        norms = np.linalg.norm(arr, axis=1, keepdims=True)
        # 避免除以 0
        norms = np.where(norms == 0, 1.0, norms)
        return arr / norms


def pack_embedding(vector: List[float]) -> bytes:
    """将 float list 打包为 bytes（兼容 SQLite BLOB）。"""
    return struct.pack(f"{len(vector)}f", *vector)


def unpack_embedding(blob: bytes) -> List[float]:
    """将 bytes 解包为 float list。"""
    n = len(blob) // 4
    return list(struct.unpack(f"{n}f", blob))
