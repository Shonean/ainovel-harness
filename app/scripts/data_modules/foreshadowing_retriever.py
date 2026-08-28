#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
大纲/伏笔语义检索器

对总纲中的伏笔表、卷划分等节点做 embedding 索引，写作时按当前章节大纲
语义召回相关前置伏笔，辅助 context-agent 自动发现需要回收/呼应的线索。
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np

from .config import get_config, DataModulesConfig
from .vector_index import VectorIndex


@dataclass
class ForeshadowingNode:
    """大纲节点（伏笔/开放环/情节线）。"""

    id: str
    kind: str           # foreshadow / open_loop / volume / plot_thread
    content: str
    buried_chapter: str
    payoff_chapter: str
    level: str
    source: str         # 来源文件相对路径


class ForeshadowingRetriever:
    """伏笔语义检索器。"""

    DEFAULT_MASTER_OUTLINE = "大纲/总纲.md"

    def __init__(self, config: Optional[DataModulesConfig] = None):
        self.config = config or get_config()
        self._vector_index: Optional[VectorIndex] = None

    @property
    def index_path(self) -> Path:
        return self.config.faiss_index_dir / "foreshadowing.faiss"

    def _embed_available(self) -> bool:
        return bool((self.config.embed_api_key or "").strip())

    def _get_vector_index(self) -> Optional[VectorIndex]:
        """懒加载/构建 FAISS 索引。"""
        if self._vector_index is not None:
            return self._vector_index
        if not self._embed_available():
            return None

        # 首次构建前不知道维度，先解析节点并 embed 一个向量获取维度
        nodes = self._parse_all_nodes()
        if not nodes:
            return None

        # 用第一个节点的文本做一次 embed 以获取维度
        dim = self._probe_embedding_dim(nodes[0].content)
        if dim is None:
            return None

        index = VectorIndex(
            dim=dim,
            index_path=self.index_path,
            use_faiss=self.config.faiss_index_enabled,
        )
        if not index.load():
            self._rebuild_index(index, nodes)
        else:
            # 简单校验：如果节点数量变化较大，重建索引
            if abs(index.size - len(nodes)) > max(5, len(nodes) * 0.2):
                self._rebuild_index(index, nodes)

        self._vector_index = index
        return index

    def _probe_embedding_dim(self, text: str) -> Optional[int]:
        """调用一次 embedding 获取维度。"""
        import asyncio
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

    def rebuild(self) -> bool:
        """强制重建索引。"""
        nodes = self._parse_all_nodes()
        if not nodes:
            return False
        dim = self._probe_embedding_dim(nodes[0].content)
        if dim is None:
            return False
        index = VectorIndex(
            dim=dim,
            index_path=self.index_path,
            use_faiss=self.config.faiss_index_enabled,
        )
        self._rebuild_index(index, nodes)
        self._vector_index = index
        return True

    def _rebuild_index(
        self,
        index: VectorIndex,
        nodes: List[ForeshadowingNode],
    ) -> None:
        """从节点列表重建 FAISS 索引。"""
        if not nodes:
            return
        try:
            from .api_client import get_client
        except Exception:
            return

        async def _embed():
            client = get_client(self.config)
            try:
                texts = [self._node_to_text(n) for n in nodes]
                return await client.embed_batch(texts)
            finally:
                await client.close()

        embeddings = asyncio.run(_embed())
        valid = [
            (n, emb)
            for n, emb in zip(nodes, embeddings)
            if emb is not None
        ]
        if not valid:
            return
        ids = [n.id for n, _ in valid]
        vectors = np.array([emb for _, emb in valid], dtype=np.float32)
        index.build(vectors, ids)
        index.save()

    # ------------------------------------------------------------------
    # 解析
    # ------------------------------------------------------------------
    def _parse_all_nodes(self) -> List[ForeshadowingNode]:
        """解析总纲和详细大纲中的节点。"""
        nodes: List[ForeshadowingNode] = []
        project_root = self.config.project_root
        outline_dir = project_root / "大纲"
        if not outline_dir.is_dir():
            return nodes

        master = outline_dir / "总纲.md"
        if master.is_file():
            nodes.extend(self._parse_master_outline(master))

        # 解析各卷详细大纲中的章节点（如果包含伏笔/情节线标记）
        for detail in sorted(outline_dir.glob("第*卷-详细大纲.md")):
            nodes.extend(self._parse_volume_outline(detail))

        return nodes

    def _parse_master_outline(self, path: Path) -> List[ForeshadowingNode]:
        """解析总纲.md，提取伏笔表、卷划分等。"""
        nodes: List[ForeshadowingNode] = []
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except Exception:
            return nodes

        # 1. 伏笔表
        nodes.extend(self._parse_markdown_table(
            text, "## 伏笔表", "foreshadow", path.relative_to(self.config.project_root).as_posix()
        ))

        # 2. 持续开放环表（如有）
        nodes.extend(self._parse_markdown_table(
            text, "## 持续开放环", "open_loop", path.relative_to(self.config.project_root).as_posix()
        ))

        # 3. 卷划分表
        nodes.extend(self._parse_volume_table(text, path))

        return nodes

    def _parse_volume_outline(self, path: Path) -> List[ForeshadowingNode]:
        """解析详细大纲，提取显式标记的伏笔/情节线。"""
        nodes: List[ForeshadowingNode] = []
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except Exception:
            return nodes

        # 简单规则：以 "【伏笔" 或 "【开放环" 开头的行
        pattern = re.compile(
            r"^\s*[【\[]\s*(伏笔|开放环|plot|foreshadow)\s*[】\]]\s*(?P<content>.*?)$",
            re.IGNORECASE | re.MULTILINE,
        )
        source = path.relative_to(self.config.project_root).as_posix()
        for idx, m in enumerate(pattern.finditer(text), start=1):
            content = m.group("content").strip()
            if not content:
                continue
            nodes.append(ForeshadowingNode(
                id=f"{path.stem}_{idx}",
                kind="foreshadow",
                content=content,
                buried_chapter="",
                payoff_chapter="",
                level="",
                source=source,
            ))
        return nodes

    def _parse_markdown_table(
        self,
        text: str,
        header: str,
        kind: str,
        source: str,
    ) -> List[ForeshadowingNode]:
        """从 markdown 文本中解析指定标题后的表格。"""
        nodes: List[ForeshadowingNode] = []
        idx = text.find(header)
        if idx < 0:
            return nodes
        block = text[idx:]
        lines = block.splitlines()

        # 找到表头行和分隔行
        table_lines: List[str] = []
        in_table = False
        for line in lines[1:]:
            if line.strip().startswith("|"):
                table_lines.append(line)
                in_table = True
            elif in_table:
                break

        if len(table_lines) < 2:
            return nodes

        headers = [c.strip() for c in table_lines[0].strip().strip("|").split("|")]
        for line in table_lines[2:]:
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            if len(cells) < 2:
                continue
            row = {h: c for h, c in zip(headers, cells)}
            content = row.get("伏笔内容") or row.get("内容") or row.get("content") or ""
            if not content:
                continue
            node_id = f"{kind}_{len(nodes)}_{hash(content) & 0xFFFFFFFF}"
            nodes.append(ForeshadowingNode(
                id=node_id,
                kind=kind,
                content=content,
                buried_chapter=row.get("埋设章", ""),
                payoff_chapter=row.get("回收章", ""),
                level=row.get("层级", ""),
                source=source,
            ))
        return nodes

    def _parse_volume_table(self, text: str, path: Path) -> List[ForeshadowingNode]:
        """解析卷划分表作为高层情节线。"""
        nodes: List[ForeshadowingNode] = []
        idx = text.find("## 卷划分")
        if idx < 0:
            return nodes
        block = text[idx:]
        lines = block.splitlines()
        table_lines: List[str] = []
        in_table = False
        for line in lines[1:]:
            if line.strip().startswith("|"):
                table_lines.append(line)
                in_table = True
            elif in_table:
                break
        if len(table_lines) < 3:
            return nodes

        headers = [c.strip() for c in table_lines[0].strip().strip("|").split("|")]
        source = path.relative_to(self.config.project_root).as_posix()
        for line in table_lines[2:]:
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            if len(cells) < 2:
                continue
            row = {h: c for h, c in zip(headers, cells)}
            parts = [f"第{row.get('卷号', '')}卷 {row.get('卷名', '')}"]
            if row.get("核心冲突"):
                parts.append(f"核心冲突：{row['核心冲突']}")
            if row.get("卷末高潮"):
                parts.append(f"卷末高潮：{row['卷末高潮']}")
            content = "；".join(parts)
            if not content.replace("第", "").replace("卷", "").strip():
                continue
            nodes.append(ForeshadowingNode(
                id=f"volume_{len(nodes)}_{hash(content) & 0xFFFFFFFF}",
                kind="volume",
                content=content,
                buried_chapter=row.get("章节范围", ""),
                payoff_chapter="",
                level="volume",
                source=source,
            ))
        return nodes

    @staticmethod
    def _node_to_text(node: ForeshadowingNode) -> str:
        """将节点转换为用于 embedding 的文本。"""
        parts = [f"[{node.kind}] {node.content}"]
        if node.buried_chapter:
            parts.append(f"埋设：{node.buried_chapter}")
        if node.payoff_chapter:
            parts.append(f"回收：{node.payoff_chapter}")
        if node.level:
            parts.append(f"层级：{node.level}")
        return " ".join(parts)

    # ------------------------------------------------------------------
    # 搜索
    # ------------------------------------------------------------------
    def search_related(
        self,
        query: str,
        current_chapter: Optional[int] = None,
        top_k: int = 5,
        *,
        payoff_filter: bool = True,
    ) -> List[Dict[str, Any]]:
        """
        检索与 query 最相关的大纲节点。

        Args:
            query: 查询文本，通常是当前章节大纲。
            current_chapter: 当前章节号，用于过滤掉已回收或埋设在未来的伏笔。
            top_k: 返回节点数。
            payoff_filter: 是否根据埋设/回收章过滤（仅当 current_chapter 提供时有效）。
        """
        index = self._get_vector_index()
        if index is None:
            return []

        try:
            from .api_client import get_client
        except Exception:
            return []

        async def _embed_search():
            client = get_client(self.config)
            try:
                embs = await client.embed([query if query else " "])
                if not embs or embs[0] is None:
                    return []
                qv = np.array(embs[0], dtype=np.float32)
                return index.search(qv, top_k=max(top_k * 3, 15))
            finally:
                await client.close()

        results = asyncio.run(_embed_search())
        if not results:
            return []

        nodes = self._parse_all_nodes()
        node_map = {n.id: n for n in nodes}

        out: List[Dict[str, Any]] = []
        for rid, score in results:
            node = node_map.get(rid)
            if node is None:
                continue
            if payoff_filter and current_chapter is not None:
                if self._should_skip(node, current_chapter):
                    continue
            out.append({
                "id": node.id,
                "kind": node.kind,
                "content": node.content,
                "buried_chapter": node.buried_chapter,
                "payoff_chapter": node.payoff_chapter,
                "level": node.level,
                "source": node.source,
                "score": round(score, 4),
            })
            if len(out) >= top_k:
                break
        return out

    def _should_skip(self, node: ForeshadowingNode, current_chapter: int) -> bool:
        """根据当前章节过滤：跳过已回收或未埋设的伏笔。"""
        # 解析回收章：取第一个数字
        def _first_chapter(text: str) -> Optional[int]:
            nums = re.findall(r"第?\s*(\d+)\s*章", text)
            if nums:
                return int(nums[0])
            nums = re.findall(r"(\d+)", text)
            if nums:
                return int(nums[0])
            return None

        payoff = _first_chapter(node.payoff_chapter) if node.payoff_chapter else None
        if payoff is not None and current_chapter > payoff:
            return True

        buried = _first_chapter(node.buried_chapter) if node.buried_chapter else None
        if buried is not None and current_chapter < buried:
            return True

        return False


def main():
    import argparse
    parser = argparse.ArgumentParser(description="大纲/伏笔语义检索器")
    parser.add_argument("--project-root", required=True, help="项目根目录")
    parser.add_argument("--query", required=True, help="查询文本（当前章节大纲）")
    parser.add_argument("--chapter", type=int, default=None, help="当前章节号")
    parser.add_argument("--top-k", type=int, default=5, help="返回节点数")
    parser.add_argument("--rebuild", action="store_true", help="强制重建索引")
    args = parser.parse_args()

    from .config import load_user_env
    load_user_env()

    config = get_config(Path(args.project_root))
    retriever = ForeshadowingRetriever(config)
    if args.rebuild:
        retriever.rebuild()

    results = retriever.search_related(
        args.query,
        current_chapter=args.chapter,
        top_k=args.top_k,
    )
    print(json.dumps({"status": "success", "data": results}, ensure_ascii=False))


if __name__ == "__main__":
    main()
