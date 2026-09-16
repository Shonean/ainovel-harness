#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Style Sampler - 风格样本管理模块

管理高质量章节片段作为风格参考：
- 风格样本存储
- 按场景类型分类
- 样本选择策略
"""

import json
import sqlite3
import time
from pathlib import Path
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, asdict
from datetime import datetime
from enum import Enum
from contextlib import contextmanager

import numpy as np

from .config import get_config
from .observability import safe_append_perf_timing, safe_log_tool_call
from .vector_index import VectorIndex, pack_embedding, unpack_embedding


class SceneType(Enum):
    """场景类型"""
    BATTLE = "战斗"
    DIALOGUE = "对话"
    DESCRIPTION = "描写"
    TRANSITION = "过渡"
    EMOTION = "情感"
    TENSION = "紧张"
    COMEDY = "轻松"


@dataclass
class StyleSample:
    """风格样本"""
    id: str
    chapter: int
    scene_type: str
    content: str
    score: float
    tags: List[str]
    created_at: str = ""


class StyleSampler:
    """风格样本管理器"""

    def __init__(self, config=None):
        self.config = config or get_config()
        self._init_db()
        self._vector_index: Optional[VectorIndex] = None

    def _init_db(self):
        """初始化数据库"""
        self.config.ensure_dirs()
        with self._get_conn() as conn:
            cursor = conn.cursor()

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS samples (
                    id TEXT PRIMARY KEY,
                    chapter INTEGER,
                    scene_type TEXT,
                    content TEXT,
                    score REAL,
                    tags TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            cursor.execute("CREATE INDEX IF NOT EXISTS idx_samples_type ON samples(scene_type)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_samples_score ON samples(score DESC)")

            conn.commit()

    @contextmanager
    def _get_conn(self):
        """获取数据库连接（确保关闭，避免 Windows 下文件句柄泄漏导致无法清理临时目录）"""
        db_path = self.config.ainovel_dir / "style_samples.db"
        conn = sqlite3.connect(str(db_path))
        try:
            yield conn
        finally:
            conn.close()

    # ==================== 样本管理 ====================

    def add_sample(self, sample: StyleSample) -> bool:
        """添加风格样本"""
        with self._get_conn() as conn:
            cursor = conn.cursor()
            try:
                cursor.execute("""
                    INSERT INTO samples
                    (id, chapter, scene_type, content, score, tags, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                """, (
                    sample.id,
                    sample.chapter,
                    sample.scene_type,
                    sample.content,
                    sample.score,
                    json.dumps(sample.tags, ensure_ascii=False),
                    sample.created_at or datetime.now().isoformat()
                ))
                conn.commit()
                return True
            except sqlite3.IntegrityError:
                return False

    def get_samples_by_type(
        self,
        scene_type: str,
        limit: int = 5,
        min_score: float = 0.0,
        exclude_chapter: Optional[int] = None,
    ) -> List[StyleSample]:
        """按场景类型获取样本（自动过滤 revoked 行；可选排除指定章节，避免重写时自引用）"""
        with self._get_conn() as conn:
            cursor = conn.cursor()
            # revoked 列在 unfinalize 时由 SKILL 添加；如果列不存在 SQLite 会报错，
            # 故先探测列是否存在
            cursor.execute("PRAGMA table_info(samples)")
            cols = {row[1] for row in cursor.fetchall()}
            # 切断 style_sampler 自馈：重写 chapter N 时，不要把 N 章自己的高分片段当作锚点
            # 否则 LLM 会无意识复现旧版开头。exclude_chapter=None 表示不过滤。
            extra_clause = ""
            extra_params: tuple = ()
            if exclude_chapter is not None and exclude_chapter > 0:
                extra_clause = " AND chapter != ?"
                extra_params = (int(exclude_chapter),)
            if "revoked" in cols:
                cursor.execute(f"""
                    SELECT id, chapter, scene_type, content, score, tags, created_at
                    FROM samples
                    WHERE scene_type = ? AND score >= ? AND COALESCE(revoked, 0) = 0{extra_clause}
                    ORDER BY score DESC
                    LIMIT ?
                """, (scene_type, min_score) + extra_params + (limit,))
            else:
                cursor.execute(f"""
                    SELECT id, chapter, scene_type, content, score, tags, created_at
                    FROM samples
                    WHERE scene_type = ? AND score >= ?{extra_clause}
                    ORDER BY score DESC
                    LIMIT ?
                """, (scene_type, min_score) + extra_params + (limit,))

            return [self._row_to_sample(row) for row in cursor.fetchall()]

    def get_user_cold_start_anchors(
        self,
        scene_type: Optional[str] = None,
        limit: int = 5,
    ) -> List[StyleSample]:
        """从用户冷启动锚点目录读取样本

        路径：`.ainovel/style_anchors_user/*.md`
        每个锚点 .md 文件以 YAML frontmatter 标注 scene_type / source / score / tags，
        frontmatter 之后的正文是被检索的范本段落。

        冷启动期（前 5 章 review_score>=80 的 db 样本不足）由本函数补位，
        让 context-agent 仍能 inject 真实可参考的"我想要的那种感觉"段落。
        """
        anchors_dir = self.config.ainovel_dir / "style_anchors_user"
        if not anchors_dir.is_dir():
            return []

        results: List[StyleSample] = []
        for path in sorted(anchors_dir.glob("*.md")):
            # 跳过 README.md 等非锚点文件——锚点必须以 frontmatter 开头
            if path.stem.lower() in ("readme", "index", "_index"):
                continue
            sample = self._parse_anchor_file(path)
            if sample is None:
                continue
            if scene_type and sample.scene_type != scene_type:
                continue
            results.append(sample)

        # 按 score 倒序，截取前 limit
        results.sort(key=lambda s: s.score, reverse=True)
        return results[:limit]

    def _parse_anchor_file(self, path: Path) -> Optional[StyleSample]:
        """解析一个锚点 .md 文件

        格式：
            ---
            scene_type: 紧张|对话|描写|过渡|情感|战斗|轻松
            source: 用户提供 / 参考书原文摘录 / AI 写后用户精修
            score: 0.95（0-1，默认 1.0，用户冷启动锚点强制高分）
            tags: [短句, 留白, 第一人称]
            ---

            正文段落（被作为 few-shot 锚点的实际文本）...

        解析失败返回 None。
        """
        try:
            text = path.read_text(encoding="utf-8")
        except Exception:
            return None

        meta: Dict[str, Any] = {}
        body = text
        has_frontmatter = False
        if text.startswith("---"):
            parts = text.split("---", 2)
            if len(parts) >= 3:
                has_frontmatter = True
                fm_block = parts[1].strip()
                body = parts[2].strip()
                for line in fm_block.splitlines():
                    line = line.strip()
                    if ":" not in line:
                        continue
                    key, _, value = line.partition(":")
                    key = key.strip().lower()
                    value = value.strip()
                    if not key:
                        continue
                    if key == "tags":
                        # 简单解析 [a, b, c] 或 a, b, c
                        value = value.strip("[]")
                        meta[key] = [t.strip() for t in value.split(",") if t.strip()]
                    elif key == "score":
                        try:
                            meta[key] = float(value)
                        except ValueError:
                            meta[key] = 1.0
                    else:
                        meta[key] = value

        # 没有 frontmatter 的文件不视为有效锚点（避免误吃 README/笔记）
        if not has_frontmatter:
            return None
        if not body.strip():
            return None

        scene_type = str(meta.get("scene_type") or SceneType.DESCRIPTION.value)
        score = float(meta.get("score", 1.0))
        # 用户冷启动锚点默认强制高于普通入库阈值，确保它优先被检索到
        if score < 0.95:
            score = 0.95

        return StyleSample(
            id=f"user_anchor_{path.stem}",
            chapter=0,
            scene_type=scene_type,
            content=body[:2000],
            score=score,
            tags=meta.get("tags", []),
            created_at=meta.get("source", "user_cold_start"),
        )

    def get_best_samples(self, limit: int = 10) -> List[StyleSample]:
        """获取最高分样本（自动过滤 revoked 行）"""
        with self._get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute("PRAGMA table_info(samples)")
            cols = {row[1] for row in cursor.fetchall()}
            if "revoked" in cols:
                cursor.execute("""
                    SELECT id, chapter, scene_type, content, score, tags, created_at
                    FROM samples
                    WHERE COALESCE(revoked, 0) = 0
                    ORDER BY score DESC
                    LIMIT ?
                """, (limit,))
            else:
                cursor.execute("""
                    SELECT id, chapter, scene_type, content, score, tags, created_at
                    FROM samples
                    ORDER BY score DESC
                    LIMIT ?
                """, (limit,))

            return [self._row_to_sample(row) for row in cursor.fetchall()]

    def _row_to_sample(self, row) -> StyleSample:
        """将数据库行转换为样本对象"""
        return StyleSample(
            id=row[0],
            chapter=row[1],
            scene_type=row[2],
            content=row[3],
            score=row[4],
            tags=json.loads(row[5]) if row[5] else [],
            created_at=row[6]
        )

    # ==================== 样本提取 ====================

    def extract_candidates(
        self,
        chapter: int,
        content: str,
        review_score: float,
        scenes: List[Dict]
    ) -> List[StyleSample]:
        """
        从章节中提取风格样本候选

        只有高分章节 (review_score >= 80) 才提取样本。
        如果未传入 scenes 但有 content，会自动按段落粗切为 scenes。
        """
        if review_score < 80:
            return []

        candidates = []

        # 兼容：无 scenes 时从 content 自动粗切
        if not scenes and content:
            scenes = self._slice_scenes_from_content(content)

        for scene in scenes:
            scene_type = self._classify_scene_type(scene)
            scene_content = scene.get("content", "")

            # 跳过过短的场景
            if len(scene_content) < 200:
                continue

            # 创建样本
            sample = StyleSample(
                id=f"ch{chapter}_s{scene.get('index', 0)}",
                chapter=chapter,
                scene_type=scene_type,
                content=scene_content[:2000],  # 限制长度
                score=review_score / 100.0,
                tags=self._extract_tags(scene_content)
            )
            candidates.append(sample)

        return candidates

    def _slice_scenes_from_content(self, content: str) -> List[Dict]:
        """将正文按行累积粗切为 scenes，每个 scene 约 400-800 字。"""
        scenes: List[Dict] = []
        lines = [line.strip() for line in content.splitlines() if line.strip()]
        buf = ""
        idx = 0
        for line in lines:
            # 跳过 markdown 标题行
            if line.startswith("#"):
                continue
            buf += line + "\n"
            if len(buf) >= 400:
                idx += 1
                scenes.append({
                    "index": idx,
                    "content": buf.strip()[:2000],
                    "summary": (buf.strip()[:80] + "...") if len(buf.strip()) > 80 else buf.strip(),
                })
                buf = ""
        if len(buf) >= 200:
            idx += 1
            scenes.append({
                "index": idx,
                "content": buf.strip()[:2000],
                "summary": (buf.strip()[:80] + "...") if len(buf.strip()) > 80 else buf.strip(),
            })
        return scenes

    def _classify_scene_type(self, scene: Dict) -> str:
        """分类场景类型"""
        summary = scene.get("summary", "").lower()
        content = scene.get("content", "").lower()

        # 简单关键词分类
        battle_keywords = ["战斗", "攻击", "出手", "拳", "剑", "杀", "打", "斗"]
        dialogue_keywords = ["说道", "问道", "笑道", "冷声", "对话"]
        emotion_keywords = ["心中", "感觉", "情", "泪", "痛", "喜"]
        tension_keywords = ["危险", "紧张", "恐惧", "压力"]

        text = summary + content

        if any(kw in text for kw in battle_keywords):
            return SceneType.BATTLE.value
        elif any(kw in text for kw in tension_keywords):
            return SceneType.TENSION.value
        elif any(kw in text for kw in dialogue_keywords):
            return SceneType.DIALOGUE.value
        elif any(kw in text for kw in emotion_keywords):
            return SceneType.EMOTION.value
        else:
            return SceneType.DESCRIPTION.value

    def _extract_tags(self, content: str) -> List[str]:
        """提取内容标签"""
        tags = []

        # 简单标签提取
        if "战斗" in content or "攻击" in content:
            tags.append("战斗")
        if "修炼" in content or "突破" in content:
            tags.append("修炼")
        if "对话" in content or "说道" in content:
            tags.append("对话")
        if "描写" in content or "景色" in content:
            tags.append("描写")

        return tags[:5]

    # ==================== 样本选择 ====================

    def select_samples_for_chapter(
        self,
        chapter_outline: str,
        target_types: List[str] = None,
        max_samples: int = 3,
        semantic: bool = False,
        style_query: Optional[str] = None,
        exclude_chapter: Optional[int] = None,
    ) -> List[StyleSample]:
        """
        为章节写作选择合适的风格样本

        基于大纲分析需要什么类型的样本。

        检索策略（按优先级）：
        1. 先从 style_samples.db 取 review_score>=80 自动入库的高分片段（按场景类型）
        2. db 样本不足时，从用户冷启动锚点目录 .ainovel/style_anchors_user/ 补位
        3. 用户锚点也不够时，按场景类型放宽 db 检索阈值
        4. 最终去重 + 截取 max_samples

        冷启动期（前 5 章）db 通常没有数据，全靠用户锚点撑住。
        随着章节积累，db 占比逐渐提升，用户锚点退居补充地位。

        semantic=True 且配置了 EMBED_API_KEY 时：先按上述策略放大候选池
        （max_samples*4），再用 style_query（未给则从大纲派生）对候选做向量
        余弦重排，截取 max_samples。默认 False → 行为与旧版完全一致。
        """
        if target_types is None:
            target_types = self._infer_scene_types(chapter_outline)

        # 语义重排需要更大的候选池
        pool_cap = max_samples * 4 if (semantic and self._embed_available()) else max_samples

        samples: List[StyleSample] = []
        seen_ids = set()
        per_type = max(1, pool_cap // len(target_types)) if target_types else pool_cap

        # 第 1 阶段：db 高分样本
        for scene_type in target_types:
            type_samples = self.get_samples_by_type(scene_type, limit=per_type, min_score=0.8, exclude_chapter=exclude_chapter)
            for s in type_samples:
                if s.id not in seen_ids:
                    samples.append(s)
                    seen_ids.add(s.id)

        # 第 2 阶段：用户冷启动锚点补位
        if len(samples) < pool_cap:
            for scene_type in target_types:
                if len(samples) >= pool_cap:
                    break
                user_anchors = self.get_user_cold_start_anchors(
                    scene_type=scene_type,
                    limit=pool_cap - len(samples),
                )
                for s in user_anchors:
                    if s.id not in seen_ids and len(samples) < pool_cap:
                        samples.append(s)
                        seen_ids.add(s.id)

            # 用户锚点的"通用 description"兜底（无 scene_type 过滤）
            if len(samples) < pool_cap:
                for s in self.get_user_cold_start_anchors(scene_type=None, limit=pool_cap):
                    if s.id not in seen_ids and len(samples) < pool_cap:
                        samples.append(s)
                        seen_ids.add(s.id)

        # 第 3 阶段：放宽 db 阈值
        if len(samples) < pool_cap:
            for scene_type in target_types:
                if len(samples) >= pool_cap:
                    break
                relaxed = self.get_samples_by_type(scene_type, limit=pool_cap - len(samples), min_score=0.0, exclude_chapter=exclude_chapter)
                for s in relaxed:
                    if s.id not in seen_ids and len(samples) < pool_cap:
                        samples.append(s)
                        seen_ids.add(s.id)

        # 语义重排
        if semantic and len(samples) > max_samples:
            query = style_query or self._derive_style_query(chapter_outline, target_types)
            reranked = self._semantic_rerank(samples, query, max_samples=max_samples)
            if reranked is not None:
                samples = reranked

        return samples[:max_samples]

    # ==================== 语义重排 ====================

    def _embed_available(self) -> bool:
        return bool(str(getattr(self.config, "embed_api_key", "") or "").strip())

    def _rerank_available(self) -> bool:
        return bool(
            getattr(self.config, "rerank_enabled", True)
            and (getattr(self.config, "rerank_api_key", "") or "").strip()
        )

    @staticmethod
    def _derive_style_query(
        chapter_outline: str,
        target_types: List[str],
        chapter_goal: Optional[str] = None,
        scene_keywords: Optional[List[str]] = None,
    ) -> str:
        """构造更丰富的风格检索查询。"""
        parts = []
        if target_types:
            parts.append("、".join(target_types) + "场景")
        if chapter_goal:
            parts.append(chapter_goal)
        outline = (chapter_outline or "").strip()[:120]
        if outline:
            parts.append(outline)
        if scene_keywords:
            parts.append(" ".join(scene_keywords))
        body = " ".join(parts)
        return f"文风范本：{body}" if body else "文风范本"

    # ==================== 向量索引 ====================

    def _get_vector_index(self) -> Optional[VectorIndex]:
        """懒加载/构建风格样本的 FAISS 向量索引。"""
        if self._vector_index is not None:
            return self._vector_index
        if not self._embed_available():
            return None

        dim = self._get_embedding_dim()
        if dim is None:
            return None

        index = VectorIndex(
            dim=dim,
            index_path=self.config.faiss_index_dir / "style_samples.faiss",
            use_faiss=self.config.faiss_index_enabled,
        )
        if not index.load():
            self._rebuild_style_index(index)

        self._vector_index = index
        return index

    def _get_embedding_dim(self) -> Optional[int]:
        """从 SQLite 中读取一个已有 embedding 推断维度。"""
        try:
            with self._get_conn() as conn:
                cursor = conn.cursor()
                cursor.execute("PRAGMA table_info(samples)")
                cols = {row[1] for row in cursor.fetchall()}
                if "embedding" not in cols:
                    return None
                cursor.execute(
                    "SELECT embedding FROM samples WHERE embedding IS NOT NULL LIMIT 1"
                )
                row = cursor.fetchone()
                if row and row[0]:
                    return len(row[0]) // 4
        except Exception:
            pass
        return None

    def _rebuild_style_index(self, index: VectorIndex) -> None:
        """从 SQLite 中所有缓存的 embedding 重建 FAISS 索引。"""
        try:
            vectors: List[List[float]] = []
            ids: List[str] = []
            with self._get_conn() as conn:
                cursor = conn.cursor()
                cursor.execute("PRAGMA table_info(samples)")
                cols = {row[1] for row in cursor.fetchall()}
                if "embedding" not in cols:
                    return
                cursor.execute("SELECT id, embedding FROM samples WHERE embedding IS NOT NULL")
                for sid, blob in cursor.fetchall():
                    if blob:
                        vectors.append(unpack_embedding(blob))
                        ids.append(sid)
            if vectors:
                index.build(np.array(vectors), ids)
                index.save()
        except Exception:
            pass

    def _add_sample_to_vector_index(self, sample: StyleSample, embedding: List[float]) -> None:
        """新增样本时同步追加到 FAISS 索引（如索引已加载）。"""
        index = self._get_vector_index()
        if index is None:
            return
        try:
            index.add(np.array([embedding]), [sample.id])
            index.save()
        except Exception:
            pass

    def _ensure_embedding_column(self, conn) -> bool:
        """确保 samples 表有 embedding BLOB 列（迁移友好）。返回列是否存在。"""
        try:
            conn.execute("PRAGMA table_info(samples)")
            cols = {row[1] for row in conn.execute("PRAGMA table_info(samples)").fetchall()}
            if "embedding" not in cols:
                conn.execute("ALTER TABLE samples ADD COLUMN embedding BLOB")
                conn.commit()
            return True
        except Exception:
            return False

    def _semantic_rerank(
        self,
        samples: List[StyleSample],
        style_query: str,
        max_samples: int = 3,
    ) -> Optional[List[StyleSample]]:
        """
        对候选样本做 FAISS 向量召回 + cross-encoder rerank 精排。
        失败返回 None（上层保留原序）。
        """
        if not samples or not self._embed_available():
            return None
        import asyncio
        import struct
        try:
            from .api_client import get_client
        except Exception:
            return None

        # 1) 确保所有样本有 embedding 缓存（复用现有 SQLite 缓存机制）
        cached = self._load_cached_embeddings(samples)
        if cached is None:
            return None

        client = get_client(self.config)
        try:
            # 2) 计算查询向量
            qv_list = asyncio.run(client.embed([style_query or " "]))
            if not qv_list or qv_list[0] is None:
                return None
            qv = np.array(qv_list[0], dtype=np.float32)

            # 3) 补齐缺失样本的 embedding
            miss_idx = [
                i for i, s in enumerate(samples) if cached.get(s.id) is None
            ]
            if miss_idx:
                miss_texts = [samples[i].content or " " for i in miss_idx]
                embs = asyncio.run(client.embed_batch(miss_texts))
                updates = []
                for j, i in enumerate(miss_idx):
                    v = embs[j] if j < len(embs) else None
                    cached[samples[i].id] = v
                    if v:
                        updates.append((samples[i].id, struct.pack(f"{len(v)}f", *v)))
                if updates:
                    try:
                        with self._get_conn() as conn:
                            for sid, blob in updates:
                                conn.execute(
                                    "UPDATE samples SET embedding = ? WHERE id = ?", (blob, sid)
                                )
                            conn.commit()
                    except Exception:
                        pass

            # 4) 同步样本 embedding 到 FAISS 索引
            index = self._get_vector_index()
            if index is None:
                return None
            self._sync_samples_to_index(index, samples, cached)

            # 5) FAISS 召回（取足够多，给 rerank 留空间）
            faiss_top_k = max(len(samples), max_samples * 4, 20)
            search_results = index.search(qv, top_k=faiss_top_k)
            if not search_results:
                return None

            sample_map = {s.id: s for s in samples}
            recalled = [sample_map[rid] for rid, _score in search_results if rid in sample_map]
            if not recalled:
                return None

            # 6) Cross-encoder rerank 精排
            if self._rerank_available():
                reranked = self._rerank_samples(recalled, style_query, top_n=max(max_samples, 10))
                if reranked:
                    return reranked

            return recalled[:max_samples]
        finally:
            try:
                asyncio.run(client.close())
            except Exception:
                pass

    def _load_cached_embeddings(
        self, samples: List[StyleSample]
    ) -> Optional[Dict[str, Optional[List[float]]]]:
        """从 SQLite 加载样本 embedding 缓存。"""
        cached: Dict[str, Optional[List[float]]] = {}
        try:
            with self._get_conn() as conn:
                if not self._ensure_embedding_column(conn):
                    return None
                ids = [s.id for s in samples]
                ph = ",".join(["?"] * len(ids))
                for r in conn.execute(
                    f"SELECT id, embedding FROM samples WHERE id IN ({ph})", ids
                ):
                    if r[1]:
                        cached[r[0]] = unpack_embedding(r[1])
                    else:
                        cached[r[0]] = None
            # 未查到的样本默认 None
            for s in samples:
                cached.setdefault(s.id, None)
            return cached
        except Exception:
            return None

    def _sync_samples_to_index(
        self,
        index: VectorIndex,
        samples: List[StyleSample],
        cached: Dict[str, Optional[List[float]]],
    ) -> None:
        """把尚未进入 FAISS 索引的样本增量添加进去。"""
        try:
            missing_ids = [s.id for s in samples if cached.get(s.id) is not None]
            existing = set(index.search(np.zeros(index.dim, dtype=np.float32), top_k=index.size))
            to_add_ids = [sid for sid in missing_ids if sid not in existing]
            if not to_add_ids:
                return
            vectors = [cached[sid] for sid in to_add_ids]
            if vectors:
                index.add(np.array(vectors, dtype=np.float32), to_add_ids)
                index.save()
        except Exception:
            pass

    def _rerank_samples(
        self,
        samples: List[StyleSample],
        query: str,
        top_n: int,
    ) -> Optional[List[StyleSample]]:
        """调用 cross-encoder rerank 对样本精排。"""
        import asyncio
        try:
            from .api_client import get_client
        except Exception:
            return None

        client = get_client(self.config)
        try:
            documents = [s.content or " " for s in samples]
            rerank_results = asyncio.run(client.rerank(query, documents, top_n=top_n))
            if not rerank_results:
                return None
            reranked: List[StyleSample] = []
            for r in rerank_results:
                idx = r.get("index")
                if idx is not None and 0 <= idx < len(samples):
                    reranked.append(samples[idx])
            return reranked
        except Exception:
            return None
        finally:
            try:
                asyncio.run(client.close())
            except Exception:
                pass

    def _infer_scene_types(self, outline: str) -> List[str]:
        """从大纲推断需要的场景类型"""
        types = []

        if any(kw in outline for kw in ["战斗", "对决", "比试", "交手"]):
            types.append(SceneType.BATTLE.value)

        if any(kw in outline for kw in ["对话", "谈话", "商议", "讨论"]):
            types.append(SceneType.DIALOGUE.value)

        if any(kw in outline for kw in ["情感", "感情", "心理"]):
            types.append(SceneType.EMOTION.value)

        if not types:
            types = [SceneType.DESCRIPTION.value]

        return types

    # ==================== 统计 ====================

    def get_stats(self) -> Dict[str, Any]:
        """获取样本统计"""
        with self._get_conn() as conn:
            cursor = conn.cursor()

            cursor.execute("SELECT COUNT(*) FROM samples")
            total = cursor.fetchone()[0]

            cursor.execute("""
                SELECT scene_type, COUNT(*) as count
                FROM samples
                GROUP BY scene_type
            """)
            by_type = {row[0]: row[1] for row in cursor.fetchall()}

            cursor.execute("SELECT AVG(score) FROM samples")
            avg_score = cursor.fetchone()[0] or 0

            return {
                "total": total,
                "by_type": by_type,
                "avg_score": round(avg_score, 3)
            }


def _find_chapter_text_file(project_root: Path, chapter: int) -> Optional[Path]:
    """查找本章正文文件（第000N章-*.md 或 第N章-*.md）。"""
    folder = project_root / "正文"
    if not folder.is_dir():
        return None
    for pattern in (f"第{chapter:04d}章*.md", f"第{chapter}章*.md"):
        hits = list(folder.glob(pattern))
        if hits:
            return hits[0]
    return None


# ==================== CLI 接口 ====================

def main():
    import argparse
    import sys
    from .config import load_user_env
    load_user_env()
    from .cli_output import print_success, print_error
    from .cli_args import normalize_global_project_root, load_json_arg
    from .index_manager import IndexManager

    parser = argparse.ArgumentParser(description="Style Sampler CLI")
    parser.add_argument("--project-root", type=str, help="项目根目录")

    subparsers = parser.add_subparsers(dest="command")

    # 获取统计
    subparsers.add_parser("stats")

    # 列出样本
    list_parser = subparsers.add_parser("list")
    list_parser.add_argument("--type", help="按类型过滤")
    list_parser.add_argument("--limit", type=int, default=10)

    # 提取样本
    extract_parser = subparsers.add_parser("extract")
    extract_parser.add_argument("--chapter", type=int, required=True)
    extract_parser.add_argument("--score", type=float, required=True)
    extract_parser.add_argument("--scenes", required=True, help="JSON 格式的场景列表")

    # 选择样本
    select_parser = subparsers.add_parser("select")
    select_parser.add_argument("--outline", required=True, help="章节大纲")
    select_parser.add_argument("--max", type=int, default=3)
    select_parser.add_argument("--semantic", action="store_true",
                              help="开启语义重排（需 EMBED_API_KEY；无 key 时自动忽略）")
    select_parser.add_argument("--style-query", default=None,
                              help="语义重排的查询文本；未给则从大纲派生")
    select_parser.add_argument("--exclude-chapter", type=int, default=None,
                              help="排除指定章节的样本（避免自我反馈）")

    argv = normalize_global_project_root(sys.argv[1:])
    args = parser.parse_args(argv)
    command_started_at = time.perf_counter()

    # 初始化
    config = None
    if args.project_root:
        # 允许传入“工作区根目录”，统一解析到真正的 book project_root（必须包含 .ainovel/state.json）
        from project_locator import resolve_project_root
        from .config import DataModulesConfig

        resolved_root = resolve_project_root(args.project_root)
        config = DataModulesConfig.from_project_root(resolved_root)

    sampler = StyleSampler(config)
    logger = IndexManager(config)
    tool_name = f"style_sampler:{args.command or 'unknown'}"

    def _append_timing(success: bool, *, error_code: str | None = None, error_message: str | None = None, chapter: int | None = None):
        elapsed_ms = int((time.perf_counter() - command_started_at) * 1000)
        safe_append_perf_timing(
            sampler.config.project_root,
            tool_name=tool_name,
            success=success,
            elapsed_ms=elapsed_ms,
            chapter=chapter,
            error_code=error_code,
            error_message=error_message,
        )

    def emit_success(data=None, message: str = "ok", chapter: int | None = None):
        print_success(data, message=message)
        safe_log_tool_call(logger, tool_name=tool_name, success=True)
        _append_timing(True, chapter=chapter)

    def emit_error(code: str, message: str, suggestion: str | None = None, chapter: int | None = None):
        print_error(code, message, suggestion=suggestion)
        safe_log_tool_call(
            logger,
            tool_name=tool_name,
            success=False,
            error_code=code,
            error_message=message,
        )
        _append_timing(False, error_code=code, error_message=message, chapter=chapter)

    if args.command == "stats":
        stats = sampler.get_stats()
        emit_success(stats, message="stats")

    elif args.command == "list":
        if args.type:
            samples = sampler.get_samples_by_type(args.type, args.limit)
        else:
            samples = sampler.get_best_samples(args.limit)
        emit_success([s.__dict__ for s in samples], message="samples")

    elif args.command == "extract":
        scenes = load_json_arg(args.scenes)
        content = ""
        # 如果 scenes 为空数组，尝试读取本章正文作为 content 自动切分
        if not scenes and sampler.config and sampler.config.project_root:
            text_file = _find_chapter_text_file(sampler.config.project_root, args.chapter)
            if text_file:
                try:
                    content = text_file.read_text(encoding="utf-8")
                except Exception:
                    content = ""
        candidates = sampler.extract_candidates(
            chapter=args.chapter,
            content=content,
            review_score=args.score,
            scenes=scenes,
        )

        added = []
        skipped = []
        for c in candidates:
            if sampler.add_sample(c):
                added.append(c.id)
            else:
                skipped.append(c.id)
        emit_success({"added": added, "skipped": skipped}, message="extracted", chapter=args.chapter)

    elif args.command == "select":
        samples = sampler.select_samples_for_chapter(
            args.outline,
            max_samples=args.max,
            semantic=bool(getattr(args, "semantic", False)),
            style_query=getattr(args, "style_query", None),
            exclude_chapter=getattr(args, "exclude_chapter", None),
        )
        emit_success([s.__dict__ for s in samples], message="selected")

    else:
        emit_error("UNKNOWN_COMMAND", "未指定有效命令", suggestion="请查看 --help")


if __name__ == "__main__":
    main()
