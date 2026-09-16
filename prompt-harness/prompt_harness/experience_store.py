"""Prompt 经验库：SQLite + FAISS。"""
from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import SETTINGS
from .embed_client import get_embedding


class ExperienceStore:
    def __init__(self, data_dir: Path | None = None) -> None:
        global faiss, np
        import faiss
        import numpy as np

        # 【v5.33.1】函数内取 SETTINGS：顶层 import 捕获旧对象会误写 cwd
        from .config import SETTINGS as _s
        self.data_dir = data_dir or _s.data_dir
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.db_path = self.data_dir / "experiences.db"
        self.index_path = self.data_dir / "experiences.faiss"
        self._dim: int | None = None
        self._index: faiss.Index | None = None
        self._init_db()
        self._load_index()

    def _init_db(self) -> None:
        with sqlite3.connect(self.db_path) as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS experiences (
                    rowid INTEGER PRIMARY KEY AUTOINCREMENT,
                    exp_id TEXT UNIQUE NOT NULL,
                    target_text TEXT NOT NULL,
                    genre TEXT,
                    scene_type TEXT,
                    perspective TEXT,
                    named_entities TEXT,
                    optimal_prompt TEXT,
                    variant_type TEXT,
                    score REAL,
                    scores_detail TEXT,
                    rounds INTEGER,
                    settings_snapshot TEXT,
                    generalization_score REAL,
                    created_at TEXT
                );
                CREATE TABLE IF NOT EXISTS embeddings (
                    rowid INTEGER PRIMARY KEY,
                    vector BLOB NOT NULL,
                    FOREIGN KEY (rowid) REFERENCES experiences(rowid) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_exp_id ON experiences(exp_id);
                CREATE INDEX IF NOT EXISTS idx_genre ON experiences(genre);
                CREATE INDEX IF NOT EXISTS idx_scene_type ON experiences(scene_type);
                """
            )
            # 兼容旧库：尝试添加新列（如果不存在则忽略错误）
            for col, col_type in [
                ("settings_snapshot", "TEXT"),
                ("generalization_score", "REAL"),
                ("style_name", "TEXT"),
                ("is_starred", "INTEGER DEFAULT 0"),
                ("source_file", "TEXT"),
                ("best_generation", "TEXT"),
                ("best_generation_gen", "TEXT"),
                ("attributes_json", "TEXT"),
                ("gen_params_json", "TEXT"),
                ("max_rounds", "INTEGER DEFAULT 5"),
                ("success_threshold", "REAL DEFAULT 0.90"),
                ("is_same_passage", "INTEGER DEFAULT 0"),
                # v4.2+ 量化训练扩展字段
                ("v_target_json", "TEXT"),
                ("v_best_json", "TEXT"),
                ("p_structured_json", "TEXT"),
                ("chapter_section", "TEXT"),
                ("angles_used", "TEXT"),
                ("angle_scores", "TEXT"),
                ("user_preference_signal", "TEXT"),
                ("convergence_curve", "TEXT"),
                ("plot_skeleton", "TEXT"),
            ]:
                try:
                    conn.execute(f"ALTER TABLE experiences ADD COLUMN {col} {col_type}")
                except sqlite3.OperationalError:
                    pass  # 列已存在

    def _load_index(self) -> None:
        if self.index_path.is_file():
            try:
                self._index = faiss.read_index(str(self.index_path))
                return
            except Exception:  # noqa: BLE001
                self._index = None

        # 从数据库重建
        rows: list[tuple[int, bytes]] = []
        with sqlite3.connect(self.db_path) as conn:
            cur = conn.execute("SELECT rowid, vector FROM embeddings")
            rows = cur.fetchall()
        if not rows:
            self._index = None
            return
        vectors = np.frombuffer(b"".join(v for _, v in rows), dtype=np.float32)
        self._dim = vectors.shape[0] // len(rows)
        vectors = vectors.reshape(-1, self._dim)
        ids = np.array([r[0] for r in rows], dtype=np.int64)
        base = faiss.IndexFlatIP(self._dim)
        self._index = faiss.IndexIDMap2(base)
        self._index.add_with_ids(vectors, ids)
        self._save_index()

    def _save_index(self) -> None:
        if self._index is not None:
            try:
                faiss.write_index(self._index, str(self.index_path))
            except RuntimeError:
                # FAISS 首次写入可能因路径问题失败，索引会在下次启动时从数据库重建
                pass

    def _ensure_index_dim(self, vector: np.ndarray) -> None:
        dim = vector.shape[0]
        if self._index is None or self._dim != dim:
            self._dim = dim
            base = faiss.IndexFlatIP(dim)
            self._index = faiss.IndexIDMap2(base)

    async def add_experience(self, record: dict[str, Any]) -> str:
        """保存一条经验，返回 exp_id。"""
        exp_id = record.get("exp_id") or uuid.uuid4().hex[:12]
        now = datetime.now(timezone.utc).isoformat()
        optimal_prompt = record["optimal_prompt"]
        if isinstance(optimal_prompt, dict):
            optimal_prompt = json.dumps(optimal_prompt, ensure_ascii=False)

        # settings_snapshot
        settings = record.get("settings_snapshot")
        if isinstance(settings, dict):
            settings = json.dumps(settings, ensure_ascii=False)

        def _json_or_none(val: Any) -> str | None:
            """将值序列化为 JSON 字符串，None 返回 None。"""
            if val is None:
                return None
            if isinstance(val, str):
                return val
            return json.dumps(val, ensure_ascii=False)

        with sqlite3.connect(self.db_path) as conn:
            cur = conn.execute(
                """
                INSERT INTO experiences
                (exp_id, target_text, genre, scene_type, perspective, named_entities,
                 optimal_prompt, variant_type, score, scores_detail, rounds,
                 settings_snapshot, generalization_score, style_name, source_file, created_at,
                 best_generation, best_generation_gen, attributes_json, gen_params_json,
                 max_rounds, success_threshold, is_same_passage,
                 v_target_json, v_best_json, p_structured_json, chapter_section,
                 angles_used, angle_scores, user_preference_signal, convergence_curve,
                 plot_skeleton)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                        ?, ?, ?, ?,
                        ?, ?, ?,
                        ?, ?, ?, ?,
                        ?, ?, ?, ?,
                        ?)
                """,
                (
                    exp_id,
                    record.get("target_text", ""),
                    record.get("genre") or None,
                    record.get("scene_type") or None,
                    record.get("perspective") or None,
                    json.dumps(record.get("named_entities", []), ensure_ascii=False),
                    optimal_prompt,
                    record.get("variant_type") or None,
                    record.get("score", 0.0),
                    json.dumps(record.get("scores_detail", {}), ensure_ascii=False),
                    record.get("rounds", 0),
                    settings,
                    record.get("generalization_score"),
                    record.get("style_name") or None,
                    record.get("source_file") or None,
                    now,
                    record.get("best_generation"),
                    record.get("best_generation_gen"),
                    _json_or_none(record.get("attributes_json")),
                    _json_or_none(record.get("gen_params_json")),
                    record.get("max_rounds", 5),
                    record.get("success_threshold", 0.90),
                    record.get("is_same_passage", 0),
                    _json_or_none(record.get("v_target_json")),
                    _json_or_none(record.get("v_best_json")),
                    _json_or_none(record.get("p_structured_json")),
                    record.get("chapter_section") or None,
                    _json_or_none(record.get("angles_used")),
                    _json_or_none(record.get("angle_scores")),
                    _json_or_none(record.get("user_preference_signal")),
                    _json_or_none(record.get("convergence_curve")),
                    record.get("plot_skeleton") or None,
                ),
            )
            rowid = cur.lastrowid

        embedding = record.get("embedding")
        if embedding is None:
            embedding = await get_embedding(record.get("target_text", ""))
        if embedding is not None:
            vec = np.array(embedding, dtype=np.float32).reshape(1, -1)
            self._ensure_index_dim(vec[0])
            assert self._index is not None
            self._index.add_with_ids(vec, np.array([rowid], dtype=np.int64))
            with sqlite3.connect(self.db_path) as conn:
                conn.execute(
                    "INSERT OR REPLACE INTO embeddings (rowid, vector) VALUES (?, ?)",
                    (rowid, vec[0].tobytes()),
                )
            self._save_index()
        return exp_id

    def get_best_for_source(self, source_file: str) -> dict[str, Any] | None:
        """获取同一来源文档的历史最高分记录（用于跨段落热启动）。

        Args:
            source_file: 来源文档名（corpus/ 下的文件名）
        Returns:
            最优记录或 None（无历史 / source_file 为空）
        """
        if not source_file or not source_file.strip():
            return None
        results = self.list_experiences(
            source_file=source_file.strip(),
            order_by="score DESC",
            limit=1,
        )
        return results[0] if results else None

    async def get_best_for_passage(
        self, source_file: str, target_text: str,
        similarity_threshold: float = 0.7,
    ) -> dict[str, Any] | None:
        """获取同一来源文档**同段落**的历史最高分记录。

        通过语义嵌入相似度，在 source_file 内找到与 target_text 最相似的历史段落，
        返回其中最高分记录。无匹配时 fallback 到 get_best_for_source（原行为）。

        Args:
            source_file: 来源文档名（corpus/ 下的文件名）
            target_text: 当前训练段落文本，用于语义匹配
            similarity_threshold: FAISS 内积相似度阈值，>=此值视为"同一段落"

        Returns:
            最优记录或 None（无历史）
        """
        if not source_file or not source_file.strip():
            return None
        if not target_text or not target_text.strip():
            return self.get_best_for_source(source_file)

        try:
            similar = await self.semantic_search(target_text, top_k=50)
        except Exception:
            similar = []

        sf = source_file.strip()
        same_source = [
            exp for exp in similar
            if (exp.get("source_file") or "").strip() == sf
            and exp.get("search_score", 0) >= similarity_threshold
        ]

        if same_source:
            same_source.sort(
                key=lambda x: (x.get("score", 0), x.get("search_score", 0)),
                reverse=True,
            )
            return same_source[0]

        # Fallback: 同文档最优（可能是不同段落，总比没有好）
        return self.get_best_for_source(source_file)

    def get_experience(self, exp_id: str) -> dict[str, Any] | None:
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cur = conn.execute("SELECT * FROM experiences WHERE exp_id = ?", (exp_id,))
            row = cur.fetchone()
            if not row:
                return None
            return self._row_to_dict(row)

    def delete_experience(self, exp_id: str) -> bool:
        with sqlite3.connect(self.db_path) as conn:
            cur = conn.execute(
                "SELECT rowid FROM experiences WHERE exp_id = ?", (exp_id,)
            )
            row = cur.fetchone()
            if not row:
                return False
            rowid = row[0]
            conn.execute("DELETE FROM experiences WHERE rowid = ?", (rowid,))
        if self._index is not None:
            self._index.remove_ids(np.array([rowid], dtype=np.int64))
            self._save_index()
        return True

    def star_experience(self, exp_id: str) -> bool:
        """精选一条经验。"""
        with sqlite3.connect(self.db_path) as conn:
            cur = conn.execute(
                "UPDATE experiences SET is_starred=1 WHERE exp_id=?",
                (exp_id,),
            )
            return cur.rowcount > 0

    def unstar_experience(self, exp_id: str) -> bool:
        """取消精选一条经验。"""
        with sqlite3.connect(self.db_path) as conn:
            cur = conn.execute(
                "UPDATE experiences SET is_starred=0 WHERE exp_id=?",
                (exp_id,),
            )
            return cur.rowcount > 0

    def update_experience(self, exp_id: str, updates: dict[str, Any]) -> bool:
        """更新一条经验的指定字段。只允许白名单中的字段。"""
        allowed = {
            "style_name", "is_starred",
            "best_generation", "best_generation_gen",
            "attributes_json", "gen_params_json",
            "max_rounds", "success_threshold",
        }
        # 只保留 allowed 中且值不为 None 的 key
        filtered = {k: v for k, v in updates.items() if k in allowed and v is not None}
        if not filtered:
            return False

        # JSON 列需要序列化
        json_cols = {"attributes_json", "gen_params_json"}
        for key in json_cols:
            if key in filtered and not isinstance(filtered[key], str):
                filtered[key] = json.dumps(filtered[key], ensure_ascii=False)

        set_clause = ", ".join(f"{k}=?" for k in filtered)
        values = list(filtered.values()) + [exp_id]
        with sqlite3.connect(self.db_path) as conn:
            cur = conn.execute(
                f"UPDATE experiences SET {set_clause} WHERE exp_id=?",
                values,
            )
            return cur.rowcount > 0

    def list_experiences(
        self,
        *,
        genre: str | None = None,
        scene_type: str | None = None,
        style_name: str | None = None,
        is_starred: bool | None = None,
        source_file: str | None = None,
        limit: int = 100,
        offset: int = 0,
        order_by: str = "created_at DESC",
    ) -> list[dict[str, Any]]:
        """列出经验。

        Args:
            genre: 题材过滤
            scene_type: 场景类型过滤
            style_name: 风格名称过滤
            is_starred: 精选过滤（True=仅精选, False=仅非精选, None=全部）
            source_file: 来源文档过滤
            limit: 每页数量
            offset: 偏移
            order_by: 排序字段，可选 "created_at DESC" (默认), "score DESC",
                      "generalization_score DESC", "combined_score DESC"
        """
        # 安全校验 order_by
        allowed = {
            "created_at DESC",
            "created_at ASC",
            "score DESC",
            "score ASC",
            "generalization_score DESC",
            "generalization_score ASC",
            "combined_score DESC",
            "combined_score ASC",
        }
        if order_by not in allowed:
            order_by = "created_at DESC"
        # map "combined_score" to "score" (our new score is the combined score)
        order_clause = order_by.replace("combined_score", "score")

        query = "SELECT * FROM experiences WHERE 1=1"
        params: list[Any] = []
        if genre:
            query += " AND genre = ?"
            params.append(genre)
        if scene_type:
            query += " AND scene_type = ?"
            params.append(scene_type)
        if style_name:
            query += " AND style_name = ?"
            params.append(style_name)
        if is_starred is not None:
            query += " AND is_starred = ?"
            params.append(1 if is_starred else 0)
        if source_file:
            query += " AND source_file = ?"
            params.append(source_file)
        query += f" ORDER BY {order_clause} LIMIT ? OFFSET ?"
        params.extend([limit, offset])
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cur = conn.execute(query, params)
            return [self._row_to_dict(row) for row in cur.fetchall()]

    async def semantic_search(
        self, target_text: str, top_k: int = 3
    ) -> list[dict[str, Any]]:
        if self._index is None:
            return []
        embedding = await get_embedding(target_text)
        if embedding is None:
            return []
        vec = np.array(embedding, dtype=np.float32).reshape(1, -1)
        distances, ids = self._index.search(vec, min(top_k, self._index.ntotal))
        results: list[dict[str, Any]] = []
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            for rowid, score in zip(ids[0], distances[0]):
                if rowid == -1:
                    continue
                cur = conn.execute(
                    "SELECT * FROM experiences WHERE rowid = ?", (int(rowid),)
                )
                row = cur.fetchone()
                if row:
                    item = self._row_to_dict(row)
                    item["search_score"] = round(float(score), 4)
                    results.append(item)
        return results

    # ------------------------------------------------------------------
    # V 向量精排：embedding 召回 + 结构相似度重排
    # ------------------------------------------------------------------

    async def search_by_structure(
        self,
        v_target: dict,
        *,
        top_k: int = 3,
        filter_source_file: str | None = None,
        filter_section: str | None = None,
        recall_k: int = 50,
        target_text: str | None = None,
        embedding_weight: float = 0.6,
    ) -> list[dict[str, Any]]:
        """按结构相似度检索经验。

        两阶段：
        1. FAISS 语义召回 top-recall_k（embedding 相似度）
        2. 对每条计算 V 向量余弦相似度 → 复合分 = emb_weight × embedding + (1-emb_weight) × V_cosine
        3. 按复合分排序返回 top-k

        Args:
            v_target: 目标结构指纹（extract_structural_vector 的输出，含 vector 字段）
            top_k: 返回数量
            filter_source_file: 限制来源文档（可选）
            filter_section: 限制 chapter_section（可选）
            recall_k: FAISS 召回数量
            target_text: 用于 FAISS 语义检索的文本；None 时无法做语义召回，退化为全表扫 + V 排序
            embedding_weight: 复合分中 embedding 相似度的权重（0~1）。V_cosine 权重 = 1 - embedding_weight
        """
        target_vec = v_target.get("vector", [])
        if not target_vec:
            return []

        candidates: list[dict[str, Any]] = []

        # 阶段 1：语义召回
        if target_text and self._index is not None:
            candidates = await self.semantic_search(target_text, top_k=recall_k)
        else:
            # 无文本或无索引 → 全表取最近 N 条作为候选（兜底，效率低但功能可用）
            all_rows = self.list_experiences(
                source_file=filter_source_file or None,
                limit=recall_k,
                order_by="score DESC",
            )
            for item in all_rows:
                item["search_score"] = 0.0  # 无 embedding 分
            candidates = all_rows

        # 过滤 section / source（semantic_search 结果不带 source 过滤，这里补）
        if filter_source_file:
            candidates = [
                c for c in candidates
                if (c.get("source_file") or "").strip() == filter_source_file.strip()
            ]
        if filter_section:
            candidates = [
                c for c in candidates
                if (c.get("chapter_section") or "").strip() == filter_section.strip()
            ]

        # 阶段 2：V 向量精排
        v_weight = 1.0 - embedding_weight
        scored: list[tuple[float, dict[str, Any]]] = []
        for item in candidates:
            v_item = item.get("v_target_json") or {}
            item_vec = v_item.get("vector") if isinstance(v_item, dict) else None
            if not item_vec or len(item_vec) != len(target_vec):
                # 没有 V 向量 → 只按 embedding 分排，降权
                emb = item.get("search_score", 0.0)
                composite = emb * embedding_weight
            else:
                import math as _math
                dot = sum(a * b for a, b in zip(target_vec, item_vec))
                n1 = _math.sqrt(sum(a * a for a in target_vec))
                n2 = _math.sqrt(sum(b * b for b in item_vec))
                v_cos = dot / (n1 * n2) if n1 and n2 else 0.0
                emb = item.get("search_score", 0.0)
                composite = emb * embedding_weight + v_cos * v_weight
                item["v_cosine_similarity"] = round(v_cos, 4)
            item["composite_score"] = round(composite, 4)
            scored.append((composite, item))

        scored.sort(key=lambda x: x[0], reverse=True)
        return [item for _, item in scored[:top_k]]

    # ------------------------------------------------------------------
    # 角度统计（供 bandit 使用）
    # ------------------------------------------------------------------

    def get_angle_stats(
        self,
        *,
        source_file: str | None = None,
        chapter_section: str | None = None,
        min_samples: int = 1,
    ) -> dict[str, dict[str, float]]:
        """聚合每个角度的历史表现。

        返回: {angle_name: {mean, std, win_rate, n}}
            win_rate = 该角度得分 > 同轮平均的比例
        """
        query = "SELECT angle_scores FROM experiences WHERE 1=1"
        params: list[Any] = []
        if source_file:
            query += " AND source_file = ?"
            params.append(source_file)
        if chapter_section:
            query += " AND chapter_section = ?"
            params.append(chapter_section)
        query += " AND angle_scores IS NOT NULL LIMIT 5000"

        # per_angle: {name: [scores]}
        per_angle: dict[str, list[float]] = {}
        # per_round: list of {name: score}（用于算胜率，需要同轮对比）
        per_round_scores: list[dict[str, float]] = []

        with sqlite3.connect(self.db_path) as conn:
            cur = conn.execute(query, params)
            for row in cur.fetchall():
                raw = row[0]
                try:
                    scores = json.loads(raw) if raw else {}
                except json.JSONDecodeError:
                    continue
                if not isinstance(scores, dict):
                    continue
                # 兼容数值和 dict
                clean: dict[str, float] = {}
                for name, val in scores.items():
                    if isinstance(val, (int, float)):
                        clean[str(name)] = float(val)
                    elif isinstance(val, dict) and "score" in val:
                        clean[str(name)] = float(val["score"])
                if not clean:
                    continue
                per_round_scores.append(clean)
                for name, s in clean.items():
                    per_angle.setdefault(name, []).append(s)

        stats: dict[str, dict[str, float]] = {}
        if not per_round_scores:
            return stats

        # 每轮均值
        round_means = [
            sum(r.values()) / len(r) if r else 0.0
            for r in per_round_scores
        ]

        for name, all_scores in per_angle.items():
            n = len(all_scores)
            if n < min_samples:
                continue
            mean = sum(all_scores) / n
            if n > 1:
                variance = sum((s - mean) ** 2 for s in all_scores) / n
                std = variance ** 0.5
            else:
                std = 0.0
            # 胜率：该角度得分 > 同轮均值的轮次比例
            wins = 0
            total_rounds = 0
            for r_scores, r_mean in zip(per_round_scores, round_means):
                if name in r_scores:
                    total_rounds += 1
                    if r_scores[name] > r_mean:
                        wins += 1
            win_rate = wins / total_rounds if total_rounds else 0.0

            stats[name] = {
                "mean": round(mean, 4),
                "std": round(std, 4),
                "win_rate": round(win_rate, 4),
                "n": n,
            }

        return stats

    # ------------------------------------------------------------------
    # 修改建议动态阈值（从历史 ΔV 分布统计）
    # ------------------------------------------------------------------

    def compute_suggestion_thresholds(
        self,
        *,
        min_samples: int = 20,
    ) -> dict[str, dict[str, float]] | None:
        """从历史经验的偏差分布中计算每维阈值。

        返回: {field_name: {medium: float, high: float, n_samples: int}}
            medium = p50，high = p75
        数据不足（任一维 < min_samples）时返回 None，fallback 到硬编码阈值。
        """
        # 从 v_best 和 v_target 的偏差来统计
        # 需要 v_target_json.vector 和 v_best_json.vector 都有值
        query = "SELECT v_target_json, v_best_json FROM experiences WHERE v_target_json IS NOT NULL AND v_best_json IS NOT NULL LIMIT 2000"

        delta_matrix: dict[str, list[float]] = {}
        scalar_fields = [
            "avg_sentence_len", "sentence_len_variance", "dialogue_density",
            "median_paragraph_len", "paragraph_frequency",
            "comma_density", "special_punct_density",
            "line_break_frequency", "lexical_richness",
            "sentence_start_diversity", "modifier_density",
            "dialogue_turn_density", "sentences_per_paragraph",
        ]
        for f in scalar_fields:
            delta_matrix[f] = []

        with sqlite3.connect(self.db_path) as conn:
            cur = conn.execute(query)
            for row in cur.fetchall():
                try:
                    vt = json.loads(row[0])
                    vb = json.loads(row[1])
                except (json.JSONDecodeError, TypeError):
                    continue
                if not isinstance(vt, dict) or not isinstance(vb, dict):
                    continue
                vt_labels = vt.get("labels", {}) if isinstance(vt, dict) else {}
                vb_labels = vb.get("labels", {}) if isinstance(vb, dict) else {}
                for field in scalar_fields:
                    tv = vt_labels.get(field)
                    gv = vb_labels.get(field)
                    if tv is None or gv is None:
                        continue
                    if tv == 0 and gv == 0:
                        continue
                    delta_pct = abs((gv - tv) / abs(tv)) * 100 if tv else 100.0
                    delta_matrix[field].append(delta_pct)

        # 检查样本量
        min_n = min(len(v) for v in delta_matrix.values()) if delta_matrix else 0
        if min_n < min_samples:
            return None

        def _percentile(sorted_vals: list[float], p: float) -> float:
            if not sorted_vals:
                return 0.0
            k = (len(sorted_vals) - 1) * p
            f = int(k)
            c = f + 1 if f + 1 < len(sorted_vals) else f
            return sorted_vals[f] + (sorted_vals[c] - sorted_vals[f]) * (k - f)

        result: dict[str, dict[str, float]] = {}
        for field, deltas in delta_matrix.items():
            if not deltas:
                continue
            s = sorted(deltas)
            result[field] = {
                "medium": round(_percentile(s, 0.5), 1),
                "high": round(_percentile(s, 0.75), 1),
                "n_samples": len(s),
            }
        return result

    # ------------------------------------------------------------------
    # V-P 相关矩阵（元层，可解释性）
    # ------------------------------------------------------------------

    def compute_vp_correlation(
        self,
        *,
        min_samples: int = 10,
        cache_path: str | None = None,
    ) -> dict | None:
        """计算 V 维度 × P 维度的皮尔逊相关矩阵。

        返回:
        {
          "v_labels": [...],
          "p_labels": [...],
          "matrix": [[r11, r12, ...], ...],  # shape (n_v, n_p)
          "n_samples": N,
          "cached_at": iso_timestamp,
        }
        数据不足返回 None。
        """
        # 拉取所有有 v_target + p_structured 的记录
        query = "SELECT v_target_json, p_structured_json FROM experiences WHERE v_target_json IS NOT NULL AND p_structured_json IS NOT NULL LIMIT 5000"

        v_rows: list[list[float]] = []
        p_rows: list[list[float]] = []
        v_labels: list[str] = []
        p_labels: list[str] = []

        with sqlite3.connect(self.db_path) as conn:
            cur = conn.execute(query)
            for row in cur.fetchall():
                try:
                    vt = json.loads(row[0])
                    ps = json.loads(row[1])
                except (json.JSONDecodeError, TypeError):
                    continue
                if not isinstance(vt, dict) or not isinstance(ps, dict):
                    continue
                v_vec = vt.get("vector")
                p_vec = ps.get("vector") if isinstance(ps, dict) else None
                if not v_vec or not p_vec:
                    continue
                if not v_labels:
                    v_labels = list(vt.get("labels", {}).keys())[:len(v_vec)] or [f"d{i+1}" for i in range(len(v_vec))]
                if not p_labels:
                    p_labels = ps.get("labels") if isinstance(ps, dict) and isinstance(ps.get("labels"), list) else [f"p{i+1}" for i in range(len(p_vec))]
                v_rows.append([float(x) for x in v_vec])
                p_rows.append([float(x) for x in p_vec])

        n = len(v_rows)
        if n < min_samples:
            return None

        n_v = len(v_rows[0])
        n_p = len(p_rows[0])

        # 皮尔逊相关系数（向量化）
        def _col_means(matrix: list[list[float]]) -> list[float]:
            cols = len(matrix[0])
            return [sum(row[c] for row in matrix) / len(matrix) for c in range(cols)]

        def _col_stds(matrix: list[list[float]], means: list[float]) -> list[float]:
            cols = len(matrix[0])
            stds = []
            for c in range(cols):
                var = sum((row[c] - means[c]) ** 2 for row in matrix) / len(matrix)
                stds.append(var ** 0.5)
            return stds

        v_means = _col_means(v_rows)
        p_means = _col_means(p_rows)
        v_stds = _col_stds(v_rows, v_means)
        p_stds = _col_stds(p_rows, p_means)

        matrix: list[list[float]] = []
        for i in range(n_v):
            row: list[float] = []
            for j in range(n_p):
                if v_stds[i] == 0 or p_stds[j] == 0:
                    row.append(0.0)
                    continue
                cov = sum(
                    (v_rows[k][i] - v_means[i]) * (p_rows[k][j] - p_means[j])
                    for k in range(n)
                ) / n
                r = cov / (v_stds[i] * p_stds[j])
                row.append(round(r, 4))
            matrix.append(row)

        return {
            "v_labels": v_labels,
            "p_labels": p_labels,
            "matrix": matrix,
            "n_samples": n,
        }

    def get_top_correlations(
        self,
        *,
        v_dim: str | None = None,
        p_dim: str | None = None,
        top_k: int = 5,
    ) -> list[dict[str, Any]]:
        """获取 V-P 相关系数最高的维度对。

        Args:
            v_dim: 只查某个 V 维度（可选）
            p_dim: 只查某个 P 维度（可选）
            top_k: 返回条数

        返回: [{v_dim, p_dim, correlation, v_idx, p_idx}, ...]
        按 |r| 降序排列。
        """
        corr = self.compute_vp_correlation()
        if not corr:
            return []

        v_labels = corr["v_labels"]
        p_labels = corr["p_labels"]
        matrix = corr["matrix"]

        pairs: list[dict[str, Any]] = []
        for i, v_lbl in enumerate(v_labels):
            for j, p_lbl in enumerate(p_labels):
                if v_dim and v_lbl != v_dim:
                    continue
                if p_dim and p_lbl != p_dim:
                    continue
                r = matrix[i][j]
                pairs.append({
                    "v_dim": v_lbl,
                    "p_dim": p_lbl,
                    "correlation": r,
                    "abs_correlation": abs(r),
                    "v_idx": i,
                    "p_idx": j,
                })

        pairs.sort(key=lambda x: x["abs_correlation"], reverse=True)
        return pairs[:top_k]

    def _row_to_dict(self, row: sqlite3.Row) -> dict[str, Any]:
        d = dict(row)
        json_cols = ("named_entities", "scores_detail", "optimal_prompt", "settings_snapshot",
                     "attributes_json", "gen_params_json",
                     "v_target_json", "v_best_json", "p_structured_json",
                     "angles_used", "angle_scores", "user_preference_signal",
                     "convergence_curve")
        for key in json_cols:
            try:
                if key in d and d[key]:
                    d[key] = json.loads(d[key])
                else:
                    d[key] = {} if key not in ("named_entities", "angles_used",
                                               "convergence_curve") else []
            except (json.JSONDecodeError, TypeError):
                pass
        d["is_starred"] = bool(d.get("is_starred", 0))
        return d
