#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Index Manager - 索引管理模块 (v5.4)

管理 index.db (SQLite) 的读写操作：
- 章节元数据索引
- 实体出场记录
- 场景索引
- 实体存储 (从 state.json 迁移)
- 别名索引 (一对多)
- 状态变化记录
- 关系存储
- 快速查询接口
- 追读力债务管理 (v5.3 引入，v5.4 沿用)

v5.4 变更:
- 新增 invalid_facts 表：追踪无效事实 (pending/confirmed)
- 新增 tool_call_stats 表：记录工具调用成功率与错误信息
- 新增 review_metrics 表：记录审查指标与趋势数据

v5.3 变更:
- 新增 override_contracts 表：记录违背软建议时的Override Contract
- 新增 chase_debt 表：追读力债务追踪
- 新增 debt_events 表：债务事件日志（产生/偿还/利息）
- 新增 chapter_reading_power 表：章节追读力元数据

v5.1 变更:
- 新增 entities 表替代 state.json 中的 entities_v3
- 新增 aliases 表替代 state.json 中的 alias_index (支持一对多)
- 新增 state_changes 表替代 state.json 中的 state_changes
- 新增 relationships 表替代 state.json 中的 structured_relationships
"""

import sqlite3
import json
import logging
import re
import sys
import time
from pathlib import Path

from runtime_compat import enable_windows_utf8_stdio
from typing import Dict, List, Optional, Any, Tuple
from dataclasses import dataclass, field
from contextlib import contextmanager
from datetime import datetime

logger = logging.getLogger(__name__)

from .config import get_config
from .observability import safe_append_perf_timing, safe_log_tool_call


@dataclass
class ChapterMeta:
    """章节元数据"""

    chapter: int
    title: str
    location: str
    word_count: int
    characters: List[str]
    summary: str = ""


@dataclass
class SceneMeta:
    """场景元数据"""

    chapter: int
    scene_index: int
    start_line: int
    end_line: int
    location: str
    summary: str
    characters: List[str]


@dataclass
class EntityMeta:
    """实体元数据 (v5.1 引入)"""

    id: str
    type: str  # 角色/地点/物品/势力/招式
    canonical_name: str
    tier: str = "装饰"  # 核心/重要/次要/装饰
    desc: str = ""
    current: Dict = field(default_factory=dict)  # 当前状态 (realm/location/items等)
    first_appearance: int = 0
    last_appearance: int = 0
    is_protagonist: bool = False
    is_archived: bool = False


@dataclass
class StateChangeMeta:
    """状态变化记录 (v5.1 引入)"""

    entity_id: str
    field: str
    old_value: str
    new_value: str
    reason: str
    chapter: int


@dataclass
class RelationshipMeta:
    """关系记录 (v5.1 引入)"""

    from_entity: str
    to_entity: str
    type: str
    description: str
    chapter: int


@dataclass
class RelationshipEventMeta:
    """关系事件记录 (v5.5 引入)"""

    from_entity: str
    to_entity: str
    type: str
    chapter: int
    action: str = "update"  # create/update/decay/remove
    polarity: int = 0  # -1/0/1
    strength: float = 0.5  # 0~1
    description: str = ""
    scene_index: int = 0
    evidence: str = ""
    confidence: float = 1.0


@dataclass
class OverrideContractMeta:
    """Override Contract (v5.3 引入)"""

    chapter: int
    constraint_type: str  # SOFT_HOOK_STRENGTH / SOFT_MICROPAYOFF / etc.
    constraint_id: str  # 具体约束标识
    rationale_type: str  # TRANSITIONAL_SETUP / LOGIC_INTEGRITY / etc.
    rationale_text: str  # 具体理由说明
    payback_plan: str  # 偿还计划描述
    due_chapter: int  # 偿还截止章节
    status: str = "pending"  # pending / fulfilled / overdue / cancelled


@dataclass
class ChaseDebtMeta:
    """追读力债务 (v5.3 引入)"""

    id: int = 0
    debt_type: str = ""  # hook_strength / micropayoff / coolpoint / etc.
    original_amount: float = 1.0  # 初始债务量
    current_amount: float = 1.0  # 当前债务量（含利息）
    interest_rate: float = 0.1  # 利息率（每章）
    source_chapter: int = 0  # 产生债务的章节
    due_chapter: int = 0  # 截止章节
    override_contract_id: int = 0  # 关联的Override Contract
    status: str = "active"  # active / paid / overdue / written_off


@dataclass
class DebtEventMeta:
    """债务事件日志 (v5.3 引入)"""

    debt_id: int
    event_type: (
        str  # created / interest_accrued / partial_payment / full_payment / overdue
    )
    amount: float
    chapter: int
    note: str = ""


@dataclass
class ChapterReadingPowerMeta:
    """章节追读力元数据 (v5.3 引入)"""

    chapter: int
    hook_type: str = ""  # 章末钩子类型
    hook_strength: str = "medium"  # strong / medium / weak
    coolpoint_patterns: List[str] = field(default_factory=list)  # 使用的爽点模式
    micropayoffs: List[str] = field(default_factory=list)  # 微兑现列表
    hard_violations: List[str] = field(default_factory=list)  # 硬约束违规
    soft_suggestions: List[str] = field(default_factory=list)  # 软建议
    is_transition: bool = False  # 是否为过渡章
    override_count: int = 0  # Override Contract数量
    debt_balance: float = 0.0  # 当前债务余额


@dataclass
class ReviewMetrics:
    """审查指标记录 (v5.4 引入)"""

    start_chapter: int
    end_chapter: int
    overall_score: float = 0.0
    dimension_scores: Dict[str, float] = field(default_factory=dict)
    severity_counts: Dict[str, int] = field(default_factory=dict)
    critical_issues: List[str] = field(default_factory=list)
    report_file: str = ""
    notes: str = ""




@dataclass
class WritingChecklistScoreMeta:
    """写作清单评分记录（Context Contract v2 Phase F）"""

    chapter: int
    template: str = "plot"
    total_items: int = 0
    required_items: int = 0
    completed_items: int = 0
    completed_required: int = 0
    total_weight: float = 0.0
    completed_weight: float = 0.0
    completion_rate: float = 0.0
    score: float = 0.0
    score_breakdown: Dict[str, Any] = field(default_factory=dict)
    pending_items: List[str] = field(default_factory=list)
    source: str = "context_manager"
    notes: str = ""


# ===================================================================
# IndexChapterMixin — 章节 / 场景 / 出场记录
# ===================================================================

class IndexChapterMixin:
    def add_chapter(self, meta: ChapterMeta):
        """添加/更新章节元数据"""
        with self._get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT OR REPLACE INTO chapters
                (chapter, title, location, word_count, characters, summary)
                VALUES (?, ?, ?, ?, ?, ?)
            """,
                (
                    meta.chapter,
                    meta.title,
                    meta.location,
                    meta.word_count,
                    json.dumps(meta.characters, ensure_ascii=False),
                    meta.summary,
                ),
            )
            conn.commit()

    def get_chapter(self, chapter: int) -> Optional[Dict]:
        """获取章节元数据"""
        with self._get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM chapters WHERE chapter = ?", (chapter,))
            row = cursor.fetchone()
            if row:
                return self._row_to_dict(row, parse_json=["characters"])
            return None

    def get_recent_chapters(self, limit: int = None) -> List[Dict]:
        """获取最近章节"""
        if limit is None:
            limit = self.config.query_recent_chapters_limit
        with self._get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT * FROM chapters
                ORDER BY chapter DESC
                LIMIT ?
            """,
                (limit,),
            )
            return [
                self._row_to_dict(row, parse_json=["characters"])
                for row in cursor.fetchall()
            ]

    # ---- 场景 ----

    def add_scenes(self, chapter: int, scenes: List[SceneMeta]):
        """添加章节场景"""
        with self._get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM scenes WHERE chapter = ?", (chapter,))
            for scene in scenes:
                cursor.execute(
                    """
                    INSERT INTO scenes
                    (chapter, scene_index, start_line, end_line, location, summary, characters)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                    (
                        scene.chapter,
                        scene.scene_index,
                        scene.start_line,
                        scene.end_line,
                        scene.location,
                        scene.summary,
                        json.dumps(scene.characters, ensure_ascii=False),
                    ),
                )
            conn.commit()

    def get_scenes(self, chapter: int) -> List[Dict]:
        """获取章节场景"""
        with self._get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT * FROM scenes WHERE chapter = ? ORDER BY scene_index",
                (chapter,),
            )
            return [
                self._row_to_dict(row, parse_json=["characters"])
                for row in cursor.fetchall()
            ]

    def search_scenes_by_location(self, location: str, limit: int = None) -> List[Dict]:
        """按地点搜索场景"""
        if limit is None:
            limit = self.config.query_scenes_by_location_limit
        with self._get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT * FROM scenes WHERE location LIKE ? ORDER BY chapter DESC LIMIT ?",
                (f"%{location}%", limit),
            )
            return [
                self._row_to_dict(row, parse_json=["characters"])
                for row in cursor.fetchall()
            ]

    # ---- 出场记录 ----

    def record_appearance(
        self, entity_id: str, chapter: int, mentions: List[str],
        confidence: float = 1.0, skip_if_exists: bool = False,
    ):
        """记录实体出场"""
        with self._get_conn() as conn:
            cursor = conn.cursor()
            if skip_if_exists:
                cursor.execute(
                    "SELECT 1 FROM appearances WHERE entity_id = ? AND chapter = ?",
                    (entity_id, chapter),
                )
                if cursor.fetchone():
                    return
            cursor.execute(
                """
                INSERT OR REPLACE INTO appearances
                (entity_id, chapter, mentions, confidence)
                VALUES (?, ?, ?, ?)
            """,
                (entity_id, chapter, json.dumps(mentions, ensure_ascii=False), confidence),
            )
            conn.commit()

    def get_entity_appearances(self, entity_id: str, limit: int = None) -> List[Dict]:
        if limit is None:
            limit = self.config.query_entity_appearances_limit
        with self._get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT * FROM appearances WHERE entity_id = ? ORDER BY chapter DESC LIMIT ?",
                (entity_id, limit),
            )
            return [
                self._row_to_dict(row, parse_json=["mentions"])
                for row in cursor.fetchall()
            ]

    def get_recent_appearances(self, limit: int = None) -> List[Dict]:
        if limit is None:
            limit = self.config.query_recent_appearances_limit
        with self._get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT entity_id, MAX(chapter) as last_chapter, COUNT(*) as total
                FROM appearances GROUP BY entity_id
                ORDER BY last_chapter DESC LIMIT ?
            """,
                (limit,),
            )
            return [dict(row) for row in cursor.fetchall()]

    def get_chapter_appearances(self, chapter: int) -> List[Dict]:
        with self._get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT * FROM appearances WHERE chapter = ? ORDER BY confidence DESC",
                (chapter,),
            )
            return [
                self._row_to_dict(row, parse_json=["mentions"])
                for row in cursor.fetchall()
            ]

    def process_chapter_data(
        self, chapter: int, title: str, location: str, word_count: int,
        entities: List[Dict], scenes: List[Dict],
    ) -> Dict[str, int]:
        """处理章节数据，批量写入索引。返回写入统计。"""
        stats: Dict[str, int] = {"chapters": 0, "scenes": 0, "appearances": 0}
        characters = [e.get("id") for e in entities if e.get("type") == "角色"]

        self.add_chapter(ChapterMeta(
            chapter=chapter, title=title, location=location,
            word_count=word_count, characters=characters, summary="",
        ))
        stats["chapters"] = 1

        scene_metas = [
            SceneMeta(
                chapter=chapter, scene_index=s.get("index", 0),
                start_line=s.get("start_line", 0), end_line=s.get("end_line", 0),
                location=s.get("location", ""), summary=s.get("summary", ""),
                characters=s.get("characters", []),
            )
            for s in scenes
        ]
        self.add_scenes(chapter, scene_metas)
        stats["scenes"] = len(scene_metas)

        for entity in entities:
            entity_id = entity.get("id")
            if entity_id and entity_id != "NEW":
                self.record_appearance(
                    entity_id=entity_id, chapter=chapter,
                    mentions=entity.get("mentions", []),
                    confidence=entity.get("confidence", 1.0),
                )
                stats["appearances"] += 1

        return stats


# ===================================================================
# IndexEntityMixin — 实体 / 别名 / 状态变化 / 关系 / 关系事件
# ===================================================================

class IndexEntityMixin:
    def _register_alias_with_cursor(
        self, cursor: sqlite3.Cursor, alias: str, entity_id: str, entity_type: str
    ) -> bool:
        alias = str(alias).strip() if alias is not None else ""
        if not alias or not entity_id:
            return False
        cursor.execute(
            "INSERT OR IGNORE INTO aliases (alias, entity_id, entity_type) VALUES (?, ?, ?)",
            (alias, entity_id, entity_type),
        )
        return cursor.rowcount > 0

    def _register_canonical_alias(self, cursor: sqlite3.Cursor, entity: EntityMeta) -> None:
        canonical_name = str(entity.canonical_name).strip() if entity.canonical_name else ""
        if canonical_name and canonical_name != entity.id:
            self._register_alias_with_cursor(cursor, canonical_name, entity.id, entity.type)
        if entity.is_protagonist:
            for alias in self._protagonist_aliases(entity, canonical_name):
                self._register_alias_with_cursor(cursor, alias, entity.id, entity.type)

    def _protagonist_aliases(self, entity: EntityMeta, canonical_name: str) -> List[str]:
        aliases = ["protagonist", "主角"]
        compact_id = str(entity.id or "").replace("_", "").replace("-", "").strip()
        if compact_id and compact_id != entity.id:
            aliases.append(compact_id)
        if canonical_name:
            aliases.append(canonical_name)
        return list(dict.fromkeys(alias for alias in aliases if alias and alias != entity.id))

    def upsert_entity(self, entity: EntityMeta, update_metadata: bool = False) -> bool:
        """插入或更新实体 (智能合并)。返回是否为新实体。"""
        with self._get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT id, current_json FROM entities WHERE id = ?", (entity.id,))
            existing = cursor.fetchone()

            if existing:
                old_current = {}
                if existing["current_json"]:
                    try:
                        old_current = json.loads(existing["current_json"])
                    except json.JSONDecodeError as exc:
                        logger.warning("failed to parse JSON in entities.current_json: %s", exc)
                merged_current = {**old_current, **entity.current}

                if update_metadata:
                    cursor.execute(
                        """UPDATE entities SET type=?, canonical_name=?, tier=?, desc=?,
                           current_json=?, last_appearance=?, is_protagonist=?, is_archived=?,
                           updated_at=CURRENT_TIMESTAMP WHERE id=?""",
                        (entity.type, entity.canonical_name, entity.tier, entity.desc,
                         json.dumps(merged_current, ensure_ascii=False),
                         entity.last_appearance, 1 if entity.is_protagonist else 0,
                         1 if entity.is_archived else 0, entity.id),
                    )
                else:
                    cursor.execute(
                        """UPDATE entities SET current_json=?, last_appearance=?,
                           updated_at=CURRENT_TIMESTAMP WHERE id=?""",
                        (json.dumps(merged_current, ensure_ascii=False),
                         entity.last_appearance, entity.id),
                    )
                self._register_canonical_alias(cursor, entity)
                conn.commit()
                return False
            else:
                cursor.execute(
                    """INSERT INTO entities (id, type, canonical_name, tier, desc, current_json,
                       first_appearance, last_appearance, is_protagonist, is_archived)
                       VALUES (?,?,?,?,?,?,?,?,?,?)""",
                    (entity.id, entity.type, entity.canonical_name, entity.tier, entity.desc,
                     json.dumps(entity.current, ensure_ascii=False),
                     entity.first_appearance, entity.last_appearance,
                     1 if entity.is_protagonist else 0, 1 if entity.is_archived else 0),
                )
                self._register_canonical_alias(cursor, entity)
                conn.commit()
                return True

    def get_entity(self, entity_id: str) -> Optional[Dict]:
        """获取单个实体；ID 查不到时回退到别名查找。"""
        with self._get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM entities WHERE id = ?", (entity_id,))
            row = cursor.fetchone()
            if row:
                return self._row_to_dict(row, parse_json=["current_json"])

        alias_matches = self.get_entities_by_alias(entity_id)
        if alias_matches:
            return alias_matches[0]

        compact = str(entity_id or "").replace("_", "").replace("-", "").strip()
        if compact and compact != entity_id:
            with self._get_conn() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT * FROM entities WHERE id = ?", (compact,))
                row = cursor.fetchone()
                if row:
                    return self._row_to_dict(row, parse_json=["current_json"])
            alias_matches = self.get_entities_by_alias(compact)
            if alias_matches:
                return alias_matches[0]
        return None

    def get_entities_by_type(self, entity_type: str, include_archived: bool = False) -> List[Dict]:
        with self._get_conn() as conn:
            cursor = conn.cursor()
            if include_archived:
                cursor.execute(
                    "SELECT * FROM entities WHERE type=? ORDER BY last_appearance DESC",
                    (entity_type,),
                )
            else:
                cursor.execute(
                    "SELECT * FROM entities WHERE type=? AND is_archived=0 ORDER BY last_appearance DESC",
                    (entity_type,),
                )
            return [self._row_to_dict(row, parse_json=["current_json"]) for row in cursor.fetchall()]

    def get_entities_by_tier(self, tier: str) -> List[Dict]:
        with self._get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT * FROM entities WHERE tier=? AND is_archived=0 ORDER BY last_appearance DESC",
                (tier,),
            )
            return [self._row_to_dict(row, parse_json=["current_json"]) for row in cursor.fetchall()]

    def get_core_entities(self) -> List[Dict]:
        with self._get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute("""SELECT * FROM entities
                WHERE (tier IN ('核心','重要') OR is_protagonist=1) AND is_archived=0
                ORDER BY is_protagonist DESC, tier, last_appearance DESC""")
            return [self._row_to_dict(row, parse_json=["current_json"]) for row in cursor.fetchall()]

    def get_protagonist(self) -> Optional[Dict]:
        with self._get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM entities WHERE is_protagonist=1 LIMIT 1")
            row = cursor.fetchone()
            if row:
                return self._row_to_dict(row, parse_json=["current_json"])
            return None

    def update_entity_current(self, entity_id: str, updates: Dict) -> bool:
        with self._get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT current_json FROM entities WHERE id=?", (entity_id,))
            row = cursor.fetchone()
            if not row:
                return False
            current = {}
            if row["current_json"]:
                try:
                    current = json.loads(row["current_json"])
                except json.JSONDecodeError as exc:
                    logger.warning("failed to parse JSON in update_entity_current: %s", exc)
            current.update(updates)
            cursor.execute(
                "UPDATE entities SET current_json=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
                (json.dumps(current, ensure_ascii=False), entity_id),
            )
            conn.commit()
            return True

    def archive_entity(self, entity_id: str) -> bool:
        with self._get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE entities SET is_archived=1, updated_at=CURRENT_TIMESTAMP WHERE id=?",
                (entity_id,),
            )
            conn.commit()
            return cursor.rowcount > 0

    # ---- 别名 ----

    def register_alias(self, alias: str, entity_id: str, entity_type: str) -> bool:
        alias = str(alias).strip() if alias is not None else ""
        if not alias or not entity_id:
            return False
        with self._get_conn() as conn:
            cursor = conn.cursor()
            try:
                inserted = self._register_alias_with_cursor(cursor, alias, entity_id, entity_type)
                conn.commit()
                return inserted
            except sqlite3.IntegrityError:
                return False

    def get_entities_by_alias(self, alias: str) -> List[Dict]:
        alias = str(alias).strip() if alias is not None else ""
        if not alias:
            return []
        with self._get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """SELECT e.*, a.entity_type as alias_type FROM entities e
                   JOIN aliases a ON e.id=a.entity_id WHERE a.alias=?
                   ORDER BY e.is_archived ASC, e.is_protagonist DESC,
                   CASE e.tier WHEN '核心' THEN 0 WHEN '重要' THEN 1 WHEN '次要' THEN 2 ELSE 3 END,
                   e.last_appearance DESC, e.id ASC""",
                (alias,),
            )
            return [self._row_to_dict(row, parse_json=["current_json"]) for row in cursor.fetchall()]

    def get_entity_aliases(self, entity_id: str) -> List[str]:
        with self._get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT alias FROM aliases WHERE entity_id=?", (entity_id,))
            return [row["alias"] for row in cursor.fetchall()]

    def remove_alias(self, alias: str, entity_id: str) -> bool:
        with self._get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM aliases WHERE alias=? AND entity_id=?", (alias, entity_id))
            conn.commit()
            return cursor.rowcount > 0

    # ---- 状态变化 ----

    def record_state_change(self, change: StateChangeMeta) -> int:
        with self._get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """INSERT INTO state_changes (entity_id, field, old_value, new_value, reason, chapter)
                   VALUES (?,?,?,?,?,?)""",
                (change.entity_id, change.field, change.old_value, change.new_value,
                 change.reason, change.chapter),
            )
            conn.commit()
            return cursor.lastrowid

    def get_entity_state_changes(self, entity_id: str, limit: int = 20) -> List[Dict]:
        with self._get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT * FROM state_changes WHERE entity_id=? ORDER BY chapter DESC, id DESC LIMIT ?",
                (entity_id, limit),
            )
            return [dict(row) for row in cursor.fetchall()]

    def get_recent_state_changes(self, limit: int = 50) -> List[Dict]:
        with self._get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT * FROM state_changes ORDER BY chapter DESC, id DESC LIMIT ?",
                (limit,),
            )
            return [dict(row) for row in cursor.fetchall()]

    def get_chapter_state_changes(self, chapter: int) -> List[Dict]:
        with self._get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM state_changes WHERE chapter=? ORDER BY id", (chapter,))
            return [dict(row) for row in cursor.fetchall()]

    # ---- 关系 ----

    def upsert_relationship(self, rel: RelationshipMeta) -> bool:
        with self._get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT id FROM relationships WHERE from_entity=? AND to_entity=? AND type=?",
                (rel.from_entity, rel.to_entity, rel.type),
            )
            existing = cursor.fetchone()
            if existing:
                cursor.execute(
                    "UPDATE relationships SET description=?, chapter=? WHERE id=?",
                    (rel.description, rel.chapter, existing["id"]),
                )
                conn.commit()
                return False
            else:
                cursor.execute(
                    """INSERT INTO relationships (from_entity, to_entity, type, description, chapter)
                       VALUES (?,?,?,?,?)""",
                    (rel.from_entity, rel.to_entity, rel.type, rel.description, rel.chapter),
                )
                conn.commit()
                return True

    def get_entity_relationships(self, entity_id: str, direction: str = "both") -> List[Dict]:
        with self._get_conn() as conn:
            cursor = conn.cursor()
            if direction == "from":
                cursor.execute(
                    "SELECT * FROM relationships WHERE from_entity=? ORDER BY chapter DESC",
                    (entity_id,),
                )
            elif direction == "to":
                cursor.execute(
                    "SELECT * FROM relationships WHERE to_entity=? ORDER BY chapter DESC",
                    (entity_id,),
                )
            else:
                cursor.execute(
                    "SELECT * FROM relationships WHERE from_entity=? OR to_entity=? ORDER BY chapter DESC",
                    (entity_id, entity_id),
                )
            return [dict(row) for row in cursor.fetchall()]

    def get_relationship_between(self, entity1: str, entity2: str) -> List[Dict]:
        with self._get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """SELECT * FROM relationships
                   WHERE (from_entity=? AND to_entity=?) OR (from_entity=? AND to_entity=?)
                   ORDER BY chapter DESC""",
                (entity1, entity2, entity2, entity1),
            )
            return [dict(row) for row in cursor.fetchall()]

    def get_recent_relationships(self, limit: int = 30) -> List[Dict]:
        with self._get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT * FROM relationships ORDER BY chapter DESC, id DESC LIMIT ?",
                (limit,),
            )
            return [dict(row) for row in cursor.fetchall()]

    # ---- 关系事件与图谱 (v5.5) ----

    def _infer_relationship_polarity(self, rel_type: str) -> int:
        t = str(rel_type or "")
        if any(k in t for k in ("敌", "仇", "恨", "对立", "冲突", "背叛", "追杀")):
            return -1
        if any(k in t for k in ("盟友", "友好", "师徒", "同伴", "亲", "爱", "合作")):
            return 1
        return 0

    def record_relationship_event(self, event: RelationshipEventMeta) -> int:
        from_entity = str(getattr(event, "from_entity", "") or "").strip()
        to_entity = str(getattr(event, "to_entity", "") or "").strip()
        rel_type = str(getattr(event, "type", "") or "").strip()
        if not from_entity or not to_entity or not rel_type:
            return 0
        action = str(getattr(event, "action", "update") or "update").strip().lower()
        if action not in {"create", "update", "decay", "remove"}:
            action = "update"
        try:
            chapter = int(getattr(event, "chapter", 0) or 0)
        except (TypeError, ValueError):
            return 0
        if chapter <= 0:
            return 0
        try:
            scene_index = int(getattr(event, "scene_index", 0) or 0)
        except (TypeError, ValueError):
            scene_index = 0
        raw_polarity = getattr(event, "polarity", None)
        if raw_polarity is None:
            polarity = self._infer_relationship_polarity(rel_type)
        else:
            try:
                polarity = int(raw_polarity)
            except (TypeError, ValueError):
                polarity = 0
        polarity = max(-1, min(1, polarity))
        try:
            strength = float(getattr(event, "strength", 0.5) or 0.5)
        except (TypeError, ValueError):
            strength = 0.5
        strength = max(0.0, min(1.0, strength))
        description = str(getattr(event, "description", "") or "").strip()
        evidence = str(getattr(event, "evidence", "") or "").strip()
        try:
            confidence = float(getattr(event, "confidence", 1.0) or 1.0)
        except (TypeError, ValueError):
            confidence = 1.0
        confidence = max(0.0, min(1.0, confidence))
        with self._get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """INSERT INTO relationship_events
                   (from_entity, to_entity, type, action, polarity, strength, description,
                    chapter, scene_index, evidence, confidence)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                (from_entity, to_entity, rel_type, action, polarity, strength, description,
                 chapter, scene_index, evidence, confidence),
            )
            conn.commit()
            return int(cursor.lastrowid or 0)

    def get_relationship_events(
        self, entity_id: str, direction: str = "both",
        from_chapter: Optional[int] = None, to_chapter: Optional[int] = None,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        direction = str(direction or "both").lower()
        clauses: List[str] = []
        params: List[Any] = []
        if direction == "from":
            clauses.append("from_entity = ?"); params.append(entity_id)
        elif direction == "to":
            clauses.append("to_entity = ?"); params.append(entity_id)
        else:
            clauses.append("(from_entity = ? OR to_entity = ?)"); params.extend([entity_id, entity_id])
        if from_chapter is not None:
            clauses.append("chapter >= ?"); params.append(int(from_chapter))
        if to_chapter is not None:
            clauses.append("chapter <= ?"); params.append(int(to_chapter))
        where_sql = " AND ".join(clauses) if clauses else "1=1"
        with self._get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute(
                f"SELECT * FROM relationship_events WHERE {where_sql} ORDER BY chapter DESC, id DESC LIMIT ?",
                (*params, int(limit)),
            )
            return [dict(row) for row in cursor.fetchall()]

    def get_relationship_timeline(
        self, entity1: str, entity2: str,
        from_chapter: Optional[int] = None, to_chapter: Optional[int] = None,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        clauses = ["((from_entity=? AND to_entity=?) OR (from_entity=? AND to_entity=?))"]
        params: List[Any] = [entity1, entity2, entity2, entity1]
        if from_chapter is not None:
            clauses.append("chapter >= ?"); params.append(int(from_chapter))
        if to_chapter is not None:
            clauses.append("chapter <= ?"); params.append(int(to_chapter))
        where_sql = " AND ".join(clauses)
        with self._get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute(
                f"SELECT * FROM relationship_events WHERE {where_sql} ORDER BY chapter ASC, id ASC LIMIT ?",
                (*params, int(limit)),
            )
            return [dict(row) for row in cursor.fetchall()]

    def _load_effective_relationship_edges(
        self, chapter: Optional[int] = None, relation_types: Optional[List[str]] = None,
    ) -> List[Dict[str, Any]]:
        relation_types = [str(t) for t in (relation_types or []) if str(t).strip()]
        with self._get_conn() as conn:
            cursor = conn.cursor()
            if chapter is None:
                clauses: List[str] = []; params: List[Any] = []
                if relation_types:
                    placeholders = ",".join("?" for _ in relation_types)
                    clauses.append(f"type IN ({placeholders})"); params.extend(relation_types)
                where_sql = f"WHERE {' AND '.join(clauses)}" if clauses else ""
                cursor.execute(
                    f"SELECT from_entity, to_entity, type, description, chapter FROM relationships {where_sql} ORDER BY chapter DESC, id DESC",
                    tuple(params),
                )
                return [
                    {"from": str(r["from_entity"]), "to": str(r["to_entity"]), "type": str(r["type"]),
                     "description": str(r["description"] or ""), "chapter": int(r["chapter"] or 0),
                     "action": "snapshot", "polarity": self._infer_relationship_polarity(str(r["type"])),
                     "strength": 0.5, "evidence": "", "confidence": 1.0}
                    for r in cursor.fetchall()
                ]
            clauses = ["chapter <= ?"]; params = [int(chapter)]
            if relation_types:
                placeholders = ",".join("?" for _ in relation_types)
                clauses.append(f"type IN ({placeholders})"); params.extend(relation_types)
            cursor.execute(
                f"SELECT * FROM relationship_events WHERE {' AND '.join(clauses)} ORDER BY chapter DESC, id DESC",
                tuple(params),
            )
            event_rows = cursor.fetchall()
            snapshot_clauses = ["chapter <= ?"]; snapshot_params: List[Any] = [int(chapter)]
            if relation_types:
                placeholders = ",".join("?" for _ in relation_types)
                snapshot_clauses.append(f"type IN ({placeholders})"); snapshot_params.extend(relation_types)
            cursor.execute(
                f"SELECT from_entity, to_entity, type, description, chapter FROM relationships WHERE {' AND '.join(snapshot_clauses)} ORDER BY chapter DESC, id DESC",
                tuple(snapshot_params),
            )
            snapshot_rows = cursor.fetchall()
        effective: List[Dict[str, Any]] = []; seen: set = set()
        for row in event_rows:
            key = (str(row["from_entity"]), str(row["to_entity"]), str(row["type"]))
            if key in seen:
                continue
            seen.add(key)
            if str(row["action"] or "update") == "remove":
                continue
            effective.append({
                "from": key[0], "to": key[1], "type": key[2],
                "description": str(row["description"] or ""), "chapter": int(row["chapter"] or 0),
                "action": str(row["action"] or "update"), "polarity": int(row["polarity"] or 0),
                "strength": float(row["strength"] or 0.5), "evidence": str(row["evidence"] or ""),
                "confidence": float(row["confidence"] or 1.0),
            })
        for row in snapshot_rows:
            key = (str(row["from_entity"]), str(row["to_entity"]), str(row["type"]))
            if key in seen:
                continue
            effective.append({
                "from": key[0], "to": key[1], "type": key[2],
                "description": str(row["description"] or ""), "chapter": int(row["chapter"] or 0),
                "action": "snapshot", "polarity": self._infer_relationship_polarity(key[2]),
                "strength": 0.5, "evidence": "", "confidence": 1.0,
            })
        return effective

    def build_relationship_subgraph(
        self, center_entity: str, depth: int = 2, chapter: Optional[int] = None,
        top_edges: int = 50, relation_types: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        center_entity = str(center_entity or "").strip()
        depth = max(1, int(depth or 1)); top_edges = max(1, int(top_edges or 1))
        edges_all = self._load_effective_relationship_edges(chapter=chapter, relation_types=relation_types)
        edges_all.sort(key=lambda x: int(x.get("chapter", 0)), reverse=True)
        selected_edges: List[Dict[str, Any]] = []; selected_keys: set = set()
        visited_nodes: set[str] = {center_entity} if center_entity else set()
        frontier: set[str] = {center_entity} if center_entity else set()
        for _ in range(depth):
            if not frontier:
                break
            next_frontier: set[str] = set()
            for edge in edges_all:
                from_entity = str(edge.get("from") or ""); to_entity = str(edge.get("to") or "")
                if from_entity not in frontier and to_entity not in frontier:
                    continue
                key = (from_entity, to_entity, str(edge.get("type") or ""))
                if key in selected_keys:
                    continue
                selected_keys.add(key); selected_edges.append(edge)
                if from_entity and from_entity not in visited_nodes:
                    visited_nodes.add(from_entity); next_frontier.add(from_entity)
                if to_entity and to_entity not in visited_nodes:
                    visited_nodes.add(to_entity); next_frontier.add(to_entity)
                if len(selected_edges) >= top_edges:
                    break
            frontier = next_frontier
            if len(selected_edges) >= top_edges:
                break
        if center_entity and center_entity not in visited_nodes:
            visited_nodes.add(center_entity)
        entity_map: Dict[str, Dict[str, Any]] = {}
        if visited_nodes:
            with self._get_conn() as conn:
                cursor = conn.cursor()
                placeholders = ",".join("?" for _ in visited_nodes)
                cursor.execute(
                    f"SELECT id, canonical_name, type, tier, last_appearance FROM entities WHERE id IN ({placeholders})",
                    tuple(visited_nodes),
                )
                for row in cursor.fetchall():
                    entity_map[str(row["id"])] = {
                        "id": str(row["id"]), "name": str(row["canonical_name"] or row["id"]),
                        "type": str(row["type"] or "未知"), "tier": str(row["tier"] or "装饰"),
                        "last_appearance": int(row["last_appearance"] or 0),
                    }
        nodes: List[Dict[str, Any]] = []
        for entity_id in sorted(visited_nodes, key=lambda eid: (
            0 if eid == center_entity else 1,
            -(entity_map.get(eid, {}).get("last_appearance", 0)), eid,
        )):
            if entity_id in entity_map:
                nodes.append(entity_map[entity_id])
            else:
                nodes.append({"id": entity_id, "name": entity_id or "未知", "type": "未知", "tier": "装饰", "last_appearance": 0})
        return {
            "center": center_entity, "depth": depth, "chapter": chapter,
            "nodes": nodes, "edges": selected_edges[:top_edges],
            "generated_at": datetime.now().isoformat(timespec="seconds"),
        }

    def _sanitize_mermaid_node_id(self, raw_id: str) -> str:
        safe = re.sub(r"[^0-9a-zA-Z_]", "_", str(raw_id or "node"))
        if not safe:
            safe = "node"
        if safe[0].isdigit():
            safe = f"n_{safe}"
        return safe

    def render_relationship_subgraph_mermaid(self, graph: Dict[str, Any]) -> str:
        lines = ["```mermaid", "graph LR"]
        nodes = graph.get("nodes") or []; edges = graph.get("edges") or []
        if not nodes:
            lines.append("    EMPTY[暂无关系数据]"); lines.append("```"); return "\n".join(lines)
        node_alias: Dict[str, str] = {}
        for node in nodes:
            entity_id = str(node.get("id") or "")
            if not entity_id:
                continue
            alias = self._sanitize_mermaid_node_id(entity_id); node_alias[entity_id] = alias
            label = str(node.get("name") or entity_id).replace('"', "'")
            lines.append(f'    {alias}["{label}"]')
        for edge in edges:
            from_entity = str(edge.get("from") or ""); to_entity = str(edge.get("to") or "")
            if from_entity not in node_alias or to_entity not in node_alias:
                continue
            edge_type = str(edge.get("type") or "关联")
            chapter = edge.get("chapter"); chapter_suffix = f"@{chapter}" if chapter not in (None, "") else ""
            label = f"{edge_type}{chapter_suffix}".replace('"', "'")
            try:
                polarity = int(edge.get("polarity", 0) or 0)
            except (TypeError, ValueError):
                polarity = 0
            connector = "-.->" if polarity < 0 else "-->"
            lines.append(f"    {node_alias[from_entity]} {connector}|{label}| {node_alias[to_entity]}")
        lines.append("```")
        return "\n".join(lines)

    def update_entity_field(self, entity_id: str, field: str, value: Any) -> bool:
        return self.update_entity_current(entity_id, {field: value})


# ===================================================================
# IndexDebtMixin — Override Contract / 追读力债务
# ===================================================================

class IndexDebtMixin:
    def create_override_contract(self, contract: OverrideContractMeta) -> int:
        with self._get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """INSERT INTO override_contracts
                   (chapter, constraint_type, constraint_id, rationale_type,
                    rationale_text, payback_plan, due_chapter, status)
                   VALUES (?,?,?,?,?,?,?,?)
                   ON CONFLICT(chapter, constraint_type, constraint_id) DO UPDATE SET
                       rationale_type = CASE WHEN override_contracts.status IN ('fulfilled','cancelled')
                           THEN override_contracts.rationale_type ELSE excluded.rationale_type END,
                       rationale_text = CASE WHEN override_contracts.status IN ('fulfilled','cancelled')
                           THEN override_contracts.rationale_text ELSE excluded.rationale_text END,
                       payback_plan = CASE WHEN override_contracts.status IN ('fulfilled','cancelled')
                           THEN override_contracts.payback_plan ELSE excluded.payback_plan END,
                       due_chapter = CASE WHEN override_contracts.status IN ('fulfilled','cancelled')
                           THEN override_contracts.due_chapter ELSE excluded.due_chapter END,
                       status = CASE WHEN override_contracts.status IN ('fulfilled','cancelled')
                           THEN override_contracts.status ELSE excluded.status END""",
                (contract.chapter, contract.constraint_type, contract.constraint_id,
                 contract.rationale_type, contract.rationale_text, contract.payback_plan,
                 contract.due_chapter, contract.status),
            )
            cursor.execute(
                "SELECT id FROM override_contracts WHERE chapter=? AND constraint_type=? AND constraint_id=?",
                (contract.chapter, contract.constraint_type, contract.constraint_id),
            )
            row = cursor.fetchone()
            if not row:
                raise RuntimeError(
                    f"Override Contract UPSERT 后无法获取 id: chapter={contract.chapter}, "
                    f"type={contract.constraint_type}, id={contract.constraint_id}"
                )
            conn.commit()
            return row[0]

    def get_pending_overrides(self, before_chapter: int = None) -> List[Dict]:
        with self._get_conn() as conn:
            cursor = conn.cursor()
            if before_chapter:
                cursor.execute(
                    "SELECT * FROM override_contracts WHERE status='pending' AND due_chapter<=? ORDER BY due_chapter ASC",
                    (before_chapter,),
                )
            else:
                cursor.execute("SELECT * FROM override_contracts WHERE status='pending' ORDER BY due_chapter ASC")
            return [dict(row) for row in cursor.fetchall()]

    def get_overdue_overrides(self, current_chapter: int) -> List[Dict]:
        with self._get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT * FROM override_contracts WHERE status='pending' AND due_chapter<? ORDER BY due_chapter ASC",
                (current_chapter,),
            )
            return [dict(row) for row in cursor.fetchall()]

    def fulfill_override(self, contract_id: int) -> bool:
        with self._get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE override_contracts SET status='fulfilled', fulfilled_at=CURRENT_TIMESTAMP WHERE id=?",
                (contract_id,),
            )
            conn.commit()
            return cursor.rowcount > 0

    def get_chapter_overrides(self, chapter: int) -> List[Dict]:
        with self._get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM override_contracts WHERE chapter=?", (chapter,))
            return [dict(row) for row in cursor.fetchall()]

    # ---- 追读力债务 ----

    def create_debt(self, debt: ChaseDebtMeta) -> int:
        with self._get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """INSERT INTO chase_debt (debt_type, original_amount, current_amount, interest_rate,
                   source_chapter, due_chapter, override_contract_id, status)
                   VALUES (?,?,?,?,?,?,?,?)""",
                (debt.debt_type, debt.original_amount, debt.current_amount, debt.interest_rate,
                 debt.source_chapter, debt.due_chapter,
                 debt.override_contract_id if debt.override_contract_id else None, debt.status),
            )
            conn.commit()
            debt_id = cursor.lastrowid
            self._record_debt_event(cursor, debt_id, "created", debt.original_amount,
                                    debt.source_chapter, f"创建债务: {debt.debt_type}")
            conn.commit()
            return debt_id

    def get_active_debts(self) -> List[Dict]:
        with self._get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM chase_debt WHERE status='active' ORDER BY due_chapter ASC")
            return [dict(row) for row in cursor.fetchall()]

    def get_overdue_debts(self, current_chapter: int) -> List[Dict]:
        with self._get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """SELECT * FROM chase_debt
                   WHERE status='overdue' OR (status='active' AND due_chapter<?)
                   ORDER BY due_chapter ASC""",
                (current_chapter,),
            )
            return [dict(row) for row in cursor.fetchall()]

    def get_total_debt_balance(self) -> float:
        with self._get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT COALESCE(SUM(current_amount),0) FROM chase_debt WHERE status IN ('active','overdue')"
            )
            return cursor.fetchone()[0]

    def accrue_interest(self, current_chapter: int) -> Dict[str, Any]:
        result = {"debts_processed": 0, "total_interest": 0.0, "new_overdues": 0, "skipped_already_processed": 0}
        with self._get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM chase_debt WHERE status IN ('active','overdue')")
            for debt in cursor.fetchall():
                debt_id = debt["id"]; current_amount = debt["current_amount"]
                interest_rate = debt["interest_rate"]; due_chapter = debt["due_chapter"]
                debt_status = debt["status"]
                cursor.execute(
                    "SELECT 1 FROM debt_events WHERE debt_id=? AND chapter=? AND event_type='interest_accrued'",
                    (debt_id, current_chapter),
                )
                if cursor.fetchone():
                    result["skipped_already_processed"] += 1; continue
                interest = current_amount * interest_rate; new_amount = current_amount + interest
                cursor.execute(
                    "UPDATE chase_debt SET current_amount=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
                    (new_amount, debt_id),
                )
                self._record_debt_event(cursor, debt_id, "interest_accrued", interest, current_chapter,
                                        f"利息: {interest:.2f} (利率: {interest_rate*100:.0f}%)")
                result["debts_processed"] += 1; result["total_interest"] += interest
                if debt_status == "active" and current_chapter > due_chapter:
                    cursor.execute(
                        "UPDATE chase_debt SET status='overdue' WHERE id=? AND status='active'",
                        (debt_id,),
                    )
                    if cursor.rowcount > 0:
                        result["new_overdues"] += 1
                        self._record_debt_event(cursor, debt_id, "overdue", new_amount, current_chapter,
                                                f"债务逾期 (截止: 第{due_chapter}章)")
            conn.commit()
        return result

    def pay_debt(self, debt_id: int, amount: float, chapter: int) -> Dict[str, Any]:
        if amount <= 0:
            return {"remaining": 0, "fully_paid": False, "error": "偿还金额必须大于0"}
        with self._get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT current_amount, override_contract_id FROM chase_debt WHERE id=?", (debt_id,))
            row = cursor.fetchone()
            if not row:
                return {"remaining": 0, "fully_paid": False, "error": "债务不存在"}
            current = row["current_amount"]; override_contract_id = row["override_contract_id"]
            remaining = max(0, current - amount); override_fulfilled = False
            if remaining == 0:
                cursor.execute(
                    "UPDATE chase_debt SET current_amount=0, status='paid', updated_at=CURRENT_TIMESTAMP WHERE id=?",
                    (debt_id,),
                )
                self._record_debt_event(cursor, debt_id, "full_payment", amount, chapter, "债务已完全偿还")
                if override_contract_id:
                    cursor.execute(
                        """UPDATE override_contracts SET status='fulfilled', fulfilled_at=CURRENT_TIMESTAMP
                           WHERE id=? AND status='pending'
                           AND NOT EXISTS (SELECT 1 FROM chase_debt WHERE override_contract_id=? AND status IN ('active','overdue'))""",
                        (override_contract_id, override_contract_id),
                    )
                    if cursor.rowcount > 0:
                        override_fulfilled = True
            else:
                cursor.execute(
                    "UPDATE chase_debt SET current_amount=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
                    (remaining, debt_id),
                )
                self._record_debt_event(cursor, debt_id, "partial_payment", amount, chapter,
                                        f"部分偿还，剩余: {remaining:.2f}")
            conn.commit()
            return {"remaining": remaining, "fully_paid": remaining == 0, "override_fulfilled": override_fulfilled}

    def _record_debt_event(self, cursor, debt_id: int, event_type: str, amount: float, chapter: int, note: str = ""):
        cursor.execute(
            "INSERT INTO debt_events (debt_id, event_type, amount, chapter, note) VALUES (?,?,?,?,?)",
            (debt_id, event_type, amount, chapter, note),
        )

    def get_debt_history(self, debt_id: int) -> List[Dict]:
        with self._get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM debt_events WHERE debt_id=? ORDER BY created_at ASC", (debt_id,))
            return [dict(row) for row in cursor.fetchall()]

    def get_debt_summary(self) -> Dict[str, Any]:
        with self._get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) as count, COALESCE(SUM(current_amount),0) as total FROM chase_debt WHERE status='active'")
            active = cursor.fetchone()
            cursor.execute("SELECT COUNT(*) as count, COALESCE(SUM(current_amount),0) as total FROM chase_debt WHERE status='overdue'")
            overdue = cursor.fetchone()
            cursor.execute("SELECT COUNT(*) FROM override_contracts WHERE status='pending'")
            pending_overrides = cursor.fetchone()[0]
            return {
                "active_debts": active["count"], "active_total": active["total"],
                "overdue_debts": overdue["count"], "overdue_total": overdue["total"],
                "pending_overrides": pending_overrides,
                "total_balance": active["total"] + overdue["total"],
            }


# ===================================================================
# IndexReadingMixin — 追读力元数据 / 审查指标 / 写作清单
# ===================================================================

class IndexReadingMixin:
    def save_chapter_reading_power(self, meta: ChapterReadingPowerMeta):
        with self._get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """INSERT OR REPLACE INTO chapter_reading_power
                   (chapter, hook_type, hook_strength, coolpoint_patterns, micropayoffs,
                    hard_violations, soft_suggestions, is_transition, override_count, debt_balance)
                   VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (meta.chapter, meta.hook_type, meta.hook_strength,
                 json.dumps(meta.coolpoint_patterns, ensure_ascii=False),
                 json.dumps(meta.micropayoffs, ensure_ascii=False),
                 json.dumps(meta.hard_violations, ensure_ascii=False),
                 json.dumps(meta.soft_suggestions, ensure_ascii=False),
                 1 if meta.is_transition else 0, meta.override_count, meta.debt_balance),
            )
            conn.commit()

    def get_chapter_reading_power(self, chapter: int) -> Optional[Dict]:
        with self._get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM chapter_reading_power WHERE chapter=?", (chapter,))
            row = cursor.fetchone()
            if row:
                return self._row_to_dict(row, parse_json=["coolpoint_patterns","micropayoffs","hard_violations","soft_suggestions"])
            return None

    def get_recent_reading_power(self, limit: int = 10) -> List[Dict]:
        with self._get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM chapter_reading_power ORDER BY chapter DESC LIMIT ?", (limit,))
            return [self._row_to_dict(row, parse_json=["coolpoint_patterns","micropayoffs","hard_violations","soft_suggestions"])
                    for row in cursor.fetchall()]

    def get_pattern_usage_stats(self, last_n_chapters: int = 20) -> Dict[str, int]:
        with self._get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT coolpoint_patterns FROM chapter_reading_power ORDER BY chapter DESC LIMIT ?", (last_n_chapters,))
            stats: Dict[str, int] = {}
            for row in cursor.fetchall():
                if row["coolpoint_patterns"]:
                    try:
                        for p in json.loads(row["coolpoint_patterns"]):
                            stats[p] = stats.get(p, 0) + 1
                    except json.JSONDecodeError as exc:
                        print(f"[index_manager] failed to parse JSON in chapter_reading_power.coolpoint_patterns: {exc}", file=sys.stderr)
            return stats

    def get_hook_type_stats(self, last_n_chapters: int = 20) -> Dict[str, int]:
        with self._get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT hook_type FROM chapter_reading_power WHERE hook_type IS NOT NULL AND hook_type!='' ORDER BY chapter DESC LIMIT ?",
                (last_n_chapters,),
            )
            stats: Dict[str, int] = {}
            for row in cursor.fetchall():
                hook = row["hook_type"]; stats[hook] = stats.get(hook, 0) + 1
            return stats

    # ---- 审查指标 ----

    def save_review_metrics(self, metrics: ReviewMetrics) -> None:
        with self._get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """INSERT INTO review_metrics
                   (start_chapter, end_chapter, overall_score, dimension_scores, severity_counts,
                    critical_issues, report_file, notes, created_at, updated_at)
                   VALUES (?,?,?,?,?,?,?,?,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)
                   ON CONFLICT(start_chapter, end_chapter) DO UPDATE SET
                       overall_score=excluded.overall_score, dimension_scores=excluded.dimension_scores,
                       severity_counts=excluded.severity_counts, critical_issues=excluded.critical_issues,
                       report_file=excluded.report_file, notes=excluded.notes, updated_at=CURRENT_TIMESTAMP""",
                (metrics.start_chapter, metrics.end_chapter, metrics.overall_score,
                 json.dumps(metrics.dimension_scores, ensure_ascii=False),
                 json.dumps(metrics.severity_counts, ensure_ascii=False),
                 json.dumps(metrics.critical_issues, ensure_ascii=False),
                 metrics.report_file, metrics.notes),
            )
            conn.commit()

    def get_recent_review_metrics(self, limit: int = 5) -> List[Dict]:
        with self._get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT * FROM review_metrics ORDER BY end_chapter DESC, start_chapter DESC LIMIT ?",
                (limit,),
            )
            return [self._row_to_dict(row, parse_json=["dimension_scores","severity_counts","critical_issues"])
                    for row in cursor.fetchall()]

    def get_review_trend_stats(self, last_n: int = 5) -> Dict[str, Any]:
        records = self.get_recent_review_metrics(last_n)
        if not records:
            return {"count": 0, "overall_avg": 0.0, "dimension_avg": {}, "severity_totals": {}, "recent_ranges": []}
        overall_scores: List[float] = []; dimension_totals: Dict[str, float] = {}
        dimension_counts: Dict[str, int] = {}; severity_totals: Dict[str, int] = {}
        for record in records:
            score = record.get("overall_score")
            if score is not None:
                try:
                    overall_scores.append(float(score))
                except (TypeError, ValueError):
                    pass
            dimensions = record.get("dimension_scores") or {}
            if isinstance(dimensions, dict):
                for key, value in dimensions.items():
                    try:
                        val = float(value)
                    except (TypeError, ValueError):
                        continue
                    dimension_totals[key] = dimension_totals.get(key, 0.0) + val
                    dimension_counts[key] = dimension_counts.get(key, 0) + 1
            severities = record.get("severity_counts") or {}
            if isinstance(severities, dict):
                for key, value in severities.items():
                    try:
                        severity_totals[key] = severity_totals.get(key, 0) + int(value)
                    except (TypeError, ValueError):
                        pass
        overall_avg = round(sum(overall_scores) / len(overall_scores), 2) if overall_scores else 0.0
        dimension_avg = {key: round(dimension_totals[key] / dimension_counts[key], 2)
                         for key in dimension_totals if dimension_counts.get(key, 0) > 0}
        return {
            "count": len(records), "overall_avg": overall_avg, "dimension_avg": dimension_avg,
            "severity_totals": severity_totals,
            "recent_ranges": [{"start_chapter": r.get("start_chapter"), "end_chapter": r.get("end_chapter"),
                               "overall_score": r.get("overall_score", 0)} for r in records],
        }

    # ---- 写作清单评分 ----

    def save_writing_checklist_score(self, meta: WritingChecklistScoreMeta) -> None:
        with self._get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """INSERT INTO writing_checklist_scores
                   (chapter, template, total_items, required_items, completed_items, completed_required,
                    total_weight, completed_weight, completion_rate, score, score_breakdown, pending_items, source, notes)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(chapter) DO UPDATE SET
                       template=excluded.template, total_items=excluded.total_items,
                       required_items=excluded.required_items, completed_items=excluded.completed_items,
                       completed_required=excluded.completed_required, total_weight=excluded.total_weight,
                       completed_weight=excluded.completed_weight, completion_rate=excluded.completion_rate,
                       score=excluded.score, score_breakdown=excluded.score_breakdown,
                       pending_items=excluded.pending_items, source=excluded.source, notes=excluded.notes,
                       updated_at=CURRENT_TIMESTAMP""",
                (meta.chapter, meta.template, meta.total_items, meta.required_items, meta.completed_items,
                 meta.completed_required, meta.total_weight, meta.completed_weight, meta.completion_rate,
                 meta.score, json.dumps(meta.score_breakdown, ensure_ascii=False),
                 json.dumps(meta.pending_items, ensure_ascii=False), meta.source, meta.notes),
            )
            conn.commit()

    def get_writing_checklist_score(self, chapter: int) -> Optional[Dict[str, Any]]:
        with self._get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM writing_checklist_scores WHERE chapter=?", (chapter,))
            row = cursor.fetchone()
            if not row:
                return None
            return self._row_to_dict(row, parse_json=["score_breakdown","pending_items"])

    def get_recent_writing_checklist_scores(self, limit: int = 10) -> List[Dict[str, Any]]:
        with self._get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM writing_checklist_scores ORDER BY chapter DESC LIMIT ?", (limit,))
            return [self._row_to_dict(row, parse_json=["score_breakdown","pending_items"]) for row in cursor.fetchall()]

    def get_writing_checklist_score_trend(self, last_n: int = 10) -> Dict[str, Any]:
        records = self.get_recent_writing_checklist_scores(limit=max(1, int(last_n)))
        if not records:
            return {"count": 0, "score_avg": 0.0, "completion_avg": 0.0, "required_completion_avg": 0.0, "recent": []}
        scores: List[float] = []; completion_rates: List[float] = []; required_rates: List[float] = []
        for row in records:
            try:
                scores.append(float(row.get("score", 0.0)))
            except (TypeError, ValueError):
                pass
            try:
                completion_rates.append(float(row.get("completion_rate", 0.0)))
            except (TypeError, ValueError):
                pass
            required_items = int(row.get("required_items") or 0)
            completed_required = int(row.get("completed_required") or 0)
            required_rates.append(completed_required / required_items if required_items > 0 else 1.0)
        return {
            "count": len(records),
            "score_avg": round(sum(scores) / len(scores), 2) if scores else 0.0,
            "completion_avg": round(sum(completion_rates) / len(completion_rates), 4) if completion_rates else 0.0,
            "required_completion_avg": round(sum(required_rates) / len(required_rates), 4) if required_rates else 0.0,
            "recent": [{"chapter": r.get("chapter"), "score": r.get("score"), "completion_rate": r.get("completion_rate")}
                       for r in records],
        }


# ===================================================================
# IndexObservabilityMixin — Row 转换 / 无效事实 / 日志 / 统计
# ===================================================================

class IndexObservabilityMixin:
    def _row_to_dict(self, row: sqlite3.Row, parse_json: List[str] = None) -> Dict:
        d = dict(row)
        if parse_json:
            for key in parse_json:
                if key in d and d[key]:
                    try:
                        d[key] = json.loads(d[key])
                    except json.JSONDecodeError as exc:
                        logger.warning("failed to parse JSON field %s in _row_to_dict: %s", key, exc)
        return d

    def mark_invalid_fact(self, source_type: str, source_id: str, reason: str,
                          marked_by: str = "user", chapter_discovered: Optional[int] = None) -> int:
        with self._get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "INSERT INTO invalid_facts (source_type, source_id, reason, status, marked_by, chapter_discovered) VALUES (?,?,?,'pending',?,?)",
                (source_type, str(source_id), reason, marked_by, chapter_discovered),
            )
            conn.commit()
            return int(cursor.lastrowid)

    def resolve_invalid_fact(self, invalid_id: int, action: str) -> bool:
        action = action.lower()
        with self._get_conn() as conn:
            cursor = conn.cursor()
            if action == "confirm":
                cursor.execute(
                    "UPDATE invalid_facts SET status='confirmed', confirmed_at=CURRENT_TIMESTAMP WHERE id=?",
                    (invalid_id,),
                )
            elif action == "dismiss":
                cursor.execute("DELETE FROM invalid_facts WHERE id=?", (invalid_id,))
            else:
                return False
            conn.commit()
            return cursor.rowcount > 0

    def list_invalid_facts(self, status: Optional[str] = None) -> List[Dict]:
        with self._get_conn() as conn:
            cursor = conn.cursor()
            if status:
                cursor.execute("SELECT * FROM invalid_facts WHERE status=? ORDER BY id DESC", (status,))
            else:
                cursor.execute("SELECT * FROM invalid_facts ORDER BY id DESC")
            return [dict(r) for r in cursor.fetchall()]

    def get_invalid_ids(self, source_type: str, status: str = "confirmed") -> set[str]:
        with self._get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT source_id FROM invalid_facts WHERE source_type=? AND status=?",
                (source_type, status),
            )
            return {str(r[0]) for r in cursor.fetchall() if r and r[0] is not None}

    def log_rag_query(self, query: str, query_type: str, results_count: int,
                      hit_sources: Optional[str] = None, latency_ms: Optional[int] = None,
                      chapter: Optional[int] = None) -> None:
        with self._get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "INSERT INTO rag_query_log (query, query_type, results_count, hit_sources, latency_ms, chapter) VALUES (?,?,?,?,?,?)",
                (query, query_type, results_count, hit_sources, latency_ms, chapter),
            )
            conn.commit()

    def log_tool_call(self, tool_name: str, success: bool, retry_count: int = 0,
                      error_code: Optional[str] = None, error_message: Optional[str] = None,
                      chapter: Optional[int] = None) -> None:
        with self._get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "INSERT INTO tool_call_stats (tool_name, success, retry_count, error_code, error_message, chapter) VALUES (?,?,?,?,?,?)",
                (tool_name, int(bool(success)), retry_count, error_code, error_message, chapter),
            )
            conn.commit()

    def get_stats(self) -> Dict[str, int]:
        with self._get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM chapters"); chapters = cursor.fetchone()[0]
            cursor.execute("SELECT COUNT(*) FROM scenes"); scenes = cursor.fetchone()[0]
            cursor.execute("SELECT COUNT(DISTINCT entity_id) FROM appearances"); appearances = cursor.fetchone()[0]
            cursor.execute("SELECT MAX(chapter) FROM chapters"); max_chapter = cursor.fetchone()[0] or 0
            cursor.execute("SELECT COUNT(*) FROM entities"); entities = cursor.fetchone()[0]
            cursor.execute("SELECT COUNT(*) FROM entities WHERE is_archived=0"); active_entities = cursor.fetchone()[0]
            cursor.execute("SELECT COUNT(*) FROM aliases"); aliases = cursor.fetchone()[0]
            cursor.execute("SELECT COUNT(*) FROM state_changes"); state_changes = cursor.fetchone()[0]
            cursor.execute("SELECT COUNT(*) FROM relationships"); relationships = cursor.fetchone()[0]
            cursor.execute("SELECT COUNT(*) FROM relationship_events"); relationship_events = cursor.fetchone()[0]
            cursor.execute("SELECT COUNT(*) FROM override_contracts"); override_contracts = cursor.fetchone()[0]
            cursor.execute("SELECT COUNT(*) FROM override_contracts WHERE status='pending'"); pending_overrides = cursor.fetchone()[0]
            cursor.execute("SELECT COUNT(*) FROM chase_debt WHERE status='active'"); active_debts = cursor.fetchone()[0]
            cursor.execute("SELECT COALESCE(SUM(current_amount),0) FROM chase_debt WHERE status IN ('active','overdue')"); total_debt = cursor.fetchone()[0]
            cursor.execute("SELECT COUNT(*) FROM chapter_reading_power"); reading_power_records = cursor.fetchone()[0]
            cursor.execute("SELECT COUNT(*) FROM review_metrics"); review_metrics = cursor.fetchone()[0]
            return {
                "chapters": chapters, "scenes": scenes, "appearances": appearances, "max_chapter": max_chapter,
                "entities": entities, "active_entities": active_entities, "aliases": aliases,
                "state_changes": state_changes, "relationships": relationships,
                "relationship_events": relationship_events,
                "override_contracts": override_contracts, "pending_overrides": pending_overrides,
                "active_debts": active_debts, "total_debt": total_debt,
                "reading_power_records": reading_power_records, "review_metrics": review_metrics,
            }


class IndexManager(IndexChapterMixin, IndexEntityMixin, IndexDebtMixin, IndexReadingMixin, IndexObservabilityMixin):
    """索引管理器"""

    def __init__(self, config=None):
        self.config = config or get_config()
        self._init_db()

    def _init_db(self):
        """初始化数据库表"""
        self.config.ensure_dirs()

        with self._get_conn() as conn:
            cursor = conn.cursor()

            # 章节表
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS chapters (
                    chapter INTEGER PRIMARY KEY,
                    title TEXT,
                    location TEXT,
                    word_count INTEGER,
                    characters TEXT,
                    summary TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            # 场景表
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS scenes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    chapter INTEGER,
                    scene_index INTEGER,
                    start_line INTEGER,
                    end_line INTEGER,
                    location TEXT,
                    summary TEXT,
                    characters TEXT,
                    UNIQUE(chapter, scene_index)
                )
            """)

            # 实体出场表
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS appearances (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    entity_id TEXT,
                    chapter INTEGER,
                    mentions TEXT,
                    confidence REAL,
                    UNIQUE(entity_id, chapter)
                )
            """)

            # 创建索引
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_scenes_chapter ON scenes(chapter)"
            )
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_appearances_entity ON appearances(entity_id)"
            )
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_appearances_chapter ON appearances(chapter)"
            )

            # ==================== v5.1 引入表 ====================

            # 实体表 (替代 state.json 中的 entities_v3)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS entities (
                    id TEXT PRIMARY KEY,
                    type TEXT NOT NULL,
                    canonical_name TEXT NOT NULL,
                    tier TEXT DEFAULT '装饰',
                    desc TEXT,
                    current_json TEXT,
                    first_appearance INTEGER DEFAULT 0,
                    last_appearance INTEGER DEFAULT 0,
                    is_protagonist INTEGER DEFAULT 0,
                    is_archived INTEGER DEFAULT 0,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            # 别名表 (替代 state.json 中的 alias_index，支持一对多)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS aliases (
                    alias TEXT NOT NULL,
                    entity_id TEXT NOT NULL,
                    entity_type TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (alias, entity_id, entity_type)
                )
            """)

            # 状态变化表 (替代 state.json 中的 state_changes)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS state_changes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    entity_id TEXT NOT NULL,
                    field TEXT NOT NULL,
                    old_value TEXT,
                    new_value TEXT,
                    reason TEXT,
                    chapter INTEGER NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            # 关系表 (替代 state.json 中的 structured_relationships)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS relationships (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    from_entity TEXT NOT NULL,
                    to_entity TEXT NOT NULL,
                    type TEXT NOT NULL,
                    description TEXT,
                    chapter INTEGER NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(from_entity, to_entity, type)
                )
            """)

            # v5.1 引入索引
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_entities_type ON entities(type)"
            )
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_entities_tier ON entities(tier)"
            )
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_entities_protagonist ON entities(is_protagonist)"
            )
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_aliases_entity ON aliases(entity_id)"
            )
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_aliases_alias ON aliases(alias)"
            )
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_state_changes_entity ON state_changes(entity_id)"
            )
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_state_changes_chapter ON state_changes(chapter)"
            )
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_relationships_from ON relationships(from_entity)"
            )
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_relationships_to ON relationships(to_entity)"
            )
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_relationships_chapter ON relationships(chapter)"
            )

            # 关系事件表 (v5.5 引入，用于时序回放/图谱分析)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS relationship_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    from_entity TEXT NOT NULL,
                    to_entity TEXT NOT NULL,
                    type TEXT NOT NULL,
                    action TEXT NOT NULL DEFAULT 'update',
                    polarity INTEGER DEFAULT 0,
                    strength REAL DEFAULT 0.5,
                    description TEXT,
                    chapter INTEGER NOT NULL,
                    scene_index INTEGER DEFAULT 0,
                    evidence TEXT,
                    confidence REAL DEFAULT 1.0,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_relationship_events_from_chapter ON relationship_events(from_entity, chapter)"
            )
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_relationship_events_to_chapter ON relationship_events(to_entity, chapter)"
            )
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_relationship_events_chapter ON relationship_events(chapter)"
            )
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_relationship_events_type_chapter ON relationship_events(type, chapter)"
            )

            # ==================== v5.3 引入表：追读力债务管理 ====================

            # Override Contract 表
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS override_contracts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    chapter INTEGER NOT NULL,
                    constraint_type TEXT NOT NULL,
                    constraint_id TEXT NOT NULL,
                    rationale_type TEXT NOT NULL,
                    rationale_text TEXT,
                    payback_plan TEXT,
                    due_chapter INTEGER NOT NULL,
                    status TEXT DEFAULT 'pending',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    fulfilled_at TIMESTAMP,
                    UNIQUE(chapter, constraint_type, constraint_id)
                )
            """)

            # 追读力债务表
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS chase_debt (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    debt_type TEXT NOT NULL,
                    original_amount REAL DEFAULT 1.0,
                    current_amount REAL DEFAULT 1.0,
                    interest_rate REAL DEFAULT 0.1,
                    source_chapter INTEGER NOT NULL,
                    due_chapter INTEGER NOT NULL,
                    override_contract_id INTEGER,
                    status TEXT DEFAULT 'active',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (override_contract_id) REFERENCES override_contracts(id)
                )
            """)

            # 债务事件日志表
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS debt_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    debt_id INTEGER NOT NULL,
                    event_type TEXT NOT NULL,
                    amount REAL NOT NULL,
                    chapter INTEGER NOT NULL,
                    note TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (debt_id) REFERENCES chase_debt(id) ON DELETE CASCADE
                )
            """)

            # 章节追读力元数据表
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS chapter_reading_power (
                    chapter INTEGER PRIMARY KEY,
                    hook_type TEXT,
                    hook_strength TEXT DEFAULT 'medium',
                    coolpoint_patterns TEXT,
                    micropayoffs TEXT,
                    hard_violations TEXT,
                    soft_suggestions TEXT,
                    is_transition INTEGER DEFAULT 0,
                    override_count INTEGER DEFAULT 0,
                    debt_balance REAL DEFAULT 0.0,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            # v5.3 引入索引
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_override_contracts_chapter ON override_contracts(chapter)"
            )
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_override_contracts_status ON override_contracts(status)"
            )
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_override_contracts_due ON override_contracts(due_chapter)"
            )
            from .projections import ensure_override_ledger_columns
            ensure_override_ledger_columns(conn)
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_chase_debt_status ON chase_debt(status)"
            )
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_chase_debt_source ON chase_debt(source_chapter)"
            )
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_chase_debt_due ON chase_debt(due_chapter)"
            )
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_debt_events_debt ON debt_events(debt_id)"
            )
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_debt_events_chapter ON debt_events(chapter)"
            )

            # ==================== v5.4 新增表：无效事实与日志 ====================

            # 无效事实表
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS invalid_facts (
                    id INTEGER PRIMARY KEY,
                    source_type TEXT NOT NULL,
                    source_id TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    status TEXT DEFAULT 'pending',
                    marked_by TEXT NOT NULL,
                    marked_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    confirmed_at TIMESTAMP,
                    chapter_discovered INTEGER
                )
            """)

            cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_invalid_status ON invalid_facts(status)"
            )
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_invalid_source ON invalid_facts(source_type, source_id)"
            )

            # 审查指标表
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS review_metrics (
                    start_chapter INTEGER NOT NULL,
                    end_chapter INTEGER NOT NULL,
                    overall_score REAL DEFAULT 0,
                    dimension_scores TEXT,
                    severity_counts TEXT,
                    critical_issues TEXT,
                    report_file TEXT,
                    notes TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (start_chapter, end_chapter)
                )
            """)
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_review_metrics_end ON review_metrics(end_chapter)"
            )

            # RAG 查询日志
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS rag_query_log (
                    id INTEGER PRIMARY KEY,
                    query TEXT,
                    query_type TEXT,
                    results_count INTEGER,
                    hit_sources TEXT,
                    latency_ms INTEGER,
                    chapter INTEGER,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_rag_query_type ON rag_query_log(query_type)"
            )
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_rag_query_chapter ON rag_query_log(chapter)"
            )

            # 工具调用统计
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS tool_call_stats (
                    id INTEGER PRIMARY KEY,
                    tool_name TEXT,
                    success BOOLEAN,
                    retry_count INTEGER DEFAULT 0,
                    error_code TEXT,
                    error_message TEXT,
                    chapter INTEGER,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_tool_stats_name ON tool_call_stats(tool_name)"
            )
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_tool_stats_chapter ON tool_call_stats(chapter)"
            )

            # 写作清单评分记录（Phase F）
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS writing_checklist_scores (
                    chapter INTEGER PRIMARY KEY,
                    template TEXT DEFAULT 'plot',
                    total_items INTEGER DEFAULT 0,
                    required_items INTEGER DEFAULT 0,
                    completed_items INTEGER DEFAULT 0,
                    completed_required INTEGER DEFAULT 0,
                    total_weight REAL DEFAULT 0,
                    completed_weight REAL DEFAULT 0,
                    completion_rate REAL DEFAULT 0,
                    score REAL DEFAULT 0,
                    score_breakdown TEXT,
                    pending_items TEXT,
                    source TEXT,
                    notes TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_checklist_score_value ON writing_checklist_scores(score)"
            )

            conn.commit()

    @contextmanager
    def _get_conn(self):
        """获取数据库连接"""
        conn = sqlite3.connect(str(self.config.index_db))
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()

    def apply_entity_delta(self, delta: Dict[str, Any]) -> bool:
        """将 commit/entity 提取产物映射为实体或关系索引更新。"""
        if not isinstance(delta, dict):
            return False

        from_entity = str(delta.get("from_entity") or delta.get("from") or "").strip()
        to_entity = str(delta.get("to_entity") or delta.get("to") or "").strip()
        rel_type = str(delta.get("relation_type") or delta.get("relationship_type") or delta.get("type") or "").strip()
        chapter = int(delta.get("chapter") or 0)
        if from_entity and to_entity and rel_type:
            self.upsert_relationship(
                RelationshipMeta(
                    from_entity=from_entity,
                    to_entity=to_entity,
                    type=rel_type,
                    description=str(delta.get("description") or "").strip(),
                    chapter=chapter,
                )
            )
            return True

        entity_id = str(delta.get("entity_id") or delta.get("id") or "").strip()
        if not entity_id:
            return False

        current = dict(delta.get("current") or {})
        field = str(delta.get("field") or delta.get("field_path") or "").strip()
        if field:
            new_value = (
                delta.get("new")
                if "new" in delta
                else delta.get("new_value")
                if "new_value" in delta
                else None
            )
            if new_value is not None and field not in current:
                current[field] = new_value

        payload = delta.get("payload") or {}
        canonical_name = str(
            delta.get("canonical_name")
            or delta.get("name")
            or payload.get("name")
            or entity_id
        ).strip()

        tier = str(delta.get("tier") or "装饰").strip() or "装饰"
        is_protagonist = bool(delta.get("is_protagonist"))
        # tier='主角' 视同 is_protagonist=True（LLM 实际输出常用 tier 标注）
        if not is_protagonist and tier == "主角":
            is_protagonist = True
        if "is_protagonist" not in delta and tier != "主角":
            existing = self.get_entity(entity_id)
            if existing:
                is_protagonist = bool(existing.get("is_protagonist"))

        entity_type = str(
            delta.get("type")
            or delta.get("entity_type")
            or "角色"
        ).strip() or "角色"

        entity = EntityMeta(
            id=entity_id,
            type=entity_type,
            canonical_name=canonical_name,
            tier=tier,
            desc=str(delta.get("desc") or delta.get("description") or "").strip(),
            current=current,
            first_appearance=chapter,
            last_appearance=chapter,
            is_protagonist=is_protagonist,
            is_archived=bool(delta.get("is_archived")),
        )
        self.upsert_entity(entity, update_metadata=True)
        return True

    # ==================== 章节操作 ====================

# ==================== CLI 接口 ====================





def main():
    import argparse
    import sys
    from .cli_output import print_success, print_error
    from .cli_args import normalize_global_project_root, load_json_arg

    parser = argparse.ArgumentParser(description="Index Manager CLI (v5.4)")
    parser.add_argument("--project-root", type=str, help="项目根目录")

    subparsers = parser.add_subparsers(dest="command")

    # 获取统计
    subparsers.add_parser("stats")

    # 查询章节
    chapter_parser = subparsers.add_parser("get-chapter")
    chapter_parser.add_argument("--chapter", type=int, required=True)

    # 查询最近出场
    recent_parser = subparsers.add_parser("recent-appearances")
    recent_parser.add_argument("--limit", type=int, default=None)

    # 查询实体出场
    entity_parser = subparsers.add_parser("entity-appearances")
    entity_parser.add_argument("--entity", required=True)
    entity_parser.add_argument("--limit", type=int, default=None)

    # 搜索场景
    search_parser = subparsers.add_parser("search-scenes")
    search_parser.add_argument("--location", required=True)
    search_parser.add_argument("--limit", type=int, default=None)

    # 处理章节数据 (写入)
    process_parser = subparsers.add_parser("process-chapter")
    process_parser.add_argument("--chapter", type=int, required=True)
    process_parser.add_argument("--title", required=True)
    process_parser.add_argument("--location", required=True)
    process_parser.add_argument("--word-count", type=int, required=True)
    process_parser.add_argument("--entities", required=True, help="JSON 格式的实体列表")
    process_parser.add_argument("--scenes", required=True, help="JSON 格式的场景列表")

    # ==================== v5.1 引入命令 ====================

    # 获取实体
    get_entity_parser = subparsers.add_parser("get-entity")
    get_entity_parser.add_argument("--id", required=True, help="实体 ID")

    # 获取核心实体
    subparsers.add_parser("get-core-entities")

    # 获取主角
    subparsers.add_parser("get-protagonist")

    # 按类型获取实体
    type_parser = subparsers.add_parser("get-entities-by-type")
    type_parser.add_argument(
        "--type", required=True, help="实体类型 (角色/地点/物品/势力/招式)"
    )
    type_parser.add_argument("--include-archived", action="store_true")

    # 按别名查找实体
    alias_parser = subparsers.add_parser("get-by-alias")
    alias_parser.add_argument("--alias", required=True, help="别名")

    # 获取实体别名
    aliases_parser = subparsers.add_parser("get-aliases")
    aliases_parser.add_argument("--entity", required=True, help="实体 ID")

    # 注册别名
    reg_alias_parser = subparsers.add_parser("register-alias")
    reg_alias_parser.add_argument("--alias", required=True)
    reg_alias_parser.add_argument("--entity", required=True)
    reg_alias_parser.add_argument("--type", required=True, help="实体类型")

    # 获取实体关系
    rel_parser = subparsers.add_parser("get-relationships")
    rel_parser.add_argument("--entity", required=True)
    rel_parser.add_argument(
        "--direction", choices=["from", "to", "both"], default="both"
    )

    # 获取关系事件
    rel_events_parser = subparsers.add_parser("get-relationship-events")
    rel_events_parser.add_argument("--entity", required=True)
    rel_events_parser.add_argument("--direction", choices=["from", "to", "both"], default="both")
    rel_events_parser.add_argument("--from-chapter", type=int, default=None)
    rel_events_parser.add_argument("--to-chapter", type=int, default=None)
    rel_events_parser.add_argument("--limit", type=int, default=100)

    # 获取关系图谱
    rel_graph_parser = subparsers.add_parser("get-relationship-graph")
    rel_graph_parser.add_argument("--center", required=True, help="中心实体 ID")
    rel_graph_parser.add_argument("--depth", type=int, default=2)
    rel_graph_parser.add_argument("--chapter", type=int, default=None)
    rel_graph_parser.add_argument("--top-edges", type=int, default=50)
    rel_graph_parser.add_argument("--format", choices=["json", "mermaid"], default="json")

    # 获取关系时间线
    rel_timeline_parser = subparsers.add_parser("get-relationship-timeline")
    rel_timeline_parser.add_argument("--a", required=True, help="实体 A")
    rel_timeline_parser.add_argument("--b", required=True, help="实体 B")
    rel_timeline_parser.add_argument("--from-chapter", type=int, default=None)
    rel_timeline_parser.add_argument("--to-chapter", type=int, default=None)
    rel_timeline_parser.add_argument("--limit", type=int, default=100)

    # 写入关系事件
    rel_event_record_parser = subparsers.add_parser("record-relationship-event")
    rel_event_record_parser.add_argument("--data", required=True, help="JSON 格式的关系事件数据")

    # 获取状态变化
    changes_parser = subparsers.add_parser("get-state-changes")
    changes_parser.add_argument("--entity", required=True)
    changes_parser.add_argument("--limit", type=int, default=20)

    # 写入实体
    upsert_entity_parser = subparsers.add_parser("upsert-entity")
    upsert_entity_parser.add_argument(
        "--data", required=True, help="JSON 格式的实体数据"
    )

    # 写入关系
    upsert_rel_parser = subparsers.add_parser("upsert-relationship")
    upsert_rel_parser.add_argument("--data", required=True, help="JSON 格式的关系数据")

    # 写入状态变化
    state_change_parser = subparsers.add_parser("record-state-change")
    state_change_parser.add_argument(
        "--data", required=True, help="JSON 格式的状态变化数据"
    )

    # ==================== v5.4 新增命令 ====================
    invalid_parser = subparsers.add_parser("mark-invalid")
    invalid_parser.add_argument("--source-type", required=True)
    invalid_parser.add_argument("--source-id", required=True)
    invalid_parser.add_argument("--reason", required=True)
    invalid_parser.add_argument("--marked-by", default="user")
    invalid_parser.add_argument("--chapter", type=int, default=None)

    resolve_parser = subparsers.add_parser("resolve-invalid")
    resolve_parser.add_argument("--id", type=int, required=True)
    resolve_parser.add_argument("--action", choices=["confirm", "dismiss"], required=True)

    list_invalid_parser = subparsers.add_parser("list-invalid")
    list_invalid_parser.add_argument("--status", choices=["pending", "confirmed"], default=None)

    review_save_parser = subparsers.add_parser("save-review-metrics")
    review_save_parser.add_argument("--data", required=True, help="JSON 格式的审查指标数据")

    review_recent_parser = subparsers.add_parser("get-recent-review-metrics")
    review_recent_parser.add_argument("--limit", type=int, default=5)

    review_trend_parser = subparsers.add_parser("get-review-trend-stats")
    review_trend_parser.add_argument("--last-n", type=int, default=5)

    checklist_score_save_parser = subparsers.add_parser("save-writing-checklist-score")
    checklist_score_save_parser.add_argument("--data", required=True, help="JSON 格式的写作清单评分数据")

    checklist_score_get_parser = subparsers.add_parser("get-writing-checklist-score")
    checklist_score_get_parser.add_argument("--chapter", type=int, required=True)

    checklist_score_recent_parser = subparsers.add_parser("get-recent-writing-checklist-scores")
    checklist_score_recent_parser.add_argument("--limit", type=int, default=10)

    checklist_score_trend_parser = subparsers.add_parser("get-writing-checklist-score-trend")
    checklist_score_trend_parser.add_argument("--last-n", type=int, default=10)

    # ==================== v5.3 引入命令 ====================

    # 获取债务汇总
    subparsers.add_parser("get-debt-summary")

    # 获取最近章节追读力元数据
    reading_power_parser = subparsers.add_parser("get-recent-reading-power")
    reading_power_parser.add_argument("--limit", type=int, default=10)

    # 获取章节追读力元数据
    chapter_rp_parser = subparsers.add_parser("get-chapter-reading-power")
    chapter_rp_parser.add_argument("--chapter", type=int, required=True)

    # 获取爽点模式使用统计
    pattern_stats_parser = subparsers.add_parser("get-pattern-usage-stats")
    pattern_stats_parser.add_argument("--last-n", type=int, default=20)

    # 获取钩子类型使用统计
    hook_stats_parser = subparsers.add_parser("get-hook-type-stats")
    hook_stats_parser.add_argument("--last-n", type=int, default=20)

    # 合并查询：追读力 + 模式统计 + 钩子统计（减少工具调用次数）
    reader_signals_parser = subparsers.add_parser("get-reader-signals")
    reader_signals_parser.add_argument("--limit", type=int, default=5)
    reader_signals_parser.add_argument("--last-n", type=int, default=20)

    # 获取待偿还Override
    pending_override_parser = subparsers.add_parser("get-pending-overrides")
    pending_override_parser.add_argument("--before-chapter", type=int, default=None)

    # 获取逾期Override
    overdue_override_parser = subparsers.add_parser("get-overdue-overrides")
    overdue_override_parser.add_argument("--current-chapter", type=int, required=True)

    # 获取活跃债务
    subparsers.add_parser("get-active-debts")

    # 获取逾期债务
    overdue_debt_parser = subparsers.add_parser("get-overdue-debts")
    overdue_debt_parser.add_argument("--current-chapter", type=int, required=True)

    # 计算利息
    accrue_parser = subparsers.add_parser("accrue-interest")
    accrue_parser.add_argument("--current-chapter", type=int, required=True)

    # 偿还债务
    pay_debt_parser = subparsers.add_parser("pay-debt")
    pay_debt_parser.add_argument("--debt-id", type=int, required=True)
    pay_debt_parser.add_argument("--amount", type=float, required=True)
    pay_debt_parser.add_argument("--chapter", type=int, required=True)

    # 创建Override Contract
    create_override_parser = subparsers.add_parser("create-override-contract")
    create_override_parser.add_argument(
        "--data", required=True, help="JSON 格式的Override Contract数据"
    )

    # 创建债务
    create_debt_parser = subparsers.add_parser("create-debt")
    create_debt_parser.add_argument("--data", required=True, help="JSON 格式的债务数据")

    # 标记Override已偿还
    fulfill_override_parser = subparsers.add_parser("fulfill-override")
    fulfill_override_parser.add_argument("--contract-id", type=int, required=True)

    # 保存章节追读力元数据
    save_rp_parser = subparsers.add_parser("save-chapter-reading-power")
    save_rp_parser.add_argument(
        "--data", required=True, help="JSON 格式的章节追读力元数据"
    )

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

    manager = IndexManager(config)
    tool_name = f"index_manager:{args.command or 'unknown'}"

    def _append_timing(
        success: bool,
        *,
        error_code: Optional[str] = None,
        error_message: Optional[str] = None,
        chapter: Optional[int] = None,
    ):
        elapsed_ms = int((time.perf_counter() - command_started_at) * 1000)
        safe_append_perf_timing(
            manager.config.project_root,
            tool_name=tool_name,
            success=success,
            elapsed_ms=elapsed_ms,
            chapter=chapter,
            error_code=error_code,
            error_message=error_message,
        )

    def emit_success(data=None, message: str = "ok", chapter: Optional[int] = None):
        print_success(data, message=message)
        safe_log_tool_call(manager, tool_name=tool_name, success=True, chapter=chapter)
        _append_timing(True, chapter=chapter)

    def emit_error(code: str, message: str, suggestion: Optional[str] = None, chapter: Optional[int] = None):
        print_error(code, message, suggestion=suggestion)
        safe_log_tool_call(
            manager,
            tool_name=tool_name,
            success=False,
            error_code=code,
            error_message=message,
            chapter=chapter,
        )
        _append_timing(False, error_code=code, error_message=message, chapter=chapter)

    if args.command == "stats":
        emit_success(manager.get_stats(), message="stats")

    elif args.command == "get-chapter":
        chapter = manager.get_chapter(args.chapter)
        if chapter:
            emit_success(chapter, message="chapter")
        else:
            emit_error("NOT_FOUND", f"未找到章节: {args.chapter}")

    elif args.command == "recent-appearances":
        appearances = manager.get_recent_appearances(args.limit)
        emit_success(appearances, message="recent_appearances")

    elif args.command == "entity-appearances":
        appearances = manager.get_entity_appearances(args.entity, args.limit)
        emit_success({"entity": args.entity, "appearances": appearances}, message="entity_appearances")

    elif args.command == "search-scenes":
        scenes = manager.search_scenes_by_location(args.location, args.limit)
        emit_success(scenes, message="scenes")

    elif args.command == "process-chapter":
        entities = load_json_arg(args.entities)
        scenes = load_json_arg(args.scenes)
        stats = manager.process_chapter_data(
            chapter=args.chapter,
            title=args.title,
            location=args.location,
            word_count=args.word_count,
            entities=entities,
            scenes=scenes,
        )
        emit_success(stats, message="chapter_processed", chapter=args.chapter)

    # ==================== v5.1 引入命令处理 ====================

    elif args.command == "get-entity":
        entity = manager.get_entity(args.id)
        if entity:
            emit_success(entity, message="entity")
        else:
            emit_error("NOT_FOUND", f"未找到实体: {args.id}")

    elif args.command == "get-core-entities":
        entities = manager.get_core_entities()
        emit_success(entities, message="core_entities")

    elif args.command == "get-protagonist":
        protagonist = manager.get_protagonist()
        if protagonist:
            emit_success(protagonist, message="protagonist")
        else:
            emit_error("NOT_FOUND", "未设置主角")

    elif args.command == "get-entities-by-type":
        entities = manager.get_entities_by_type(args.type, args.include_archived)
        emit_success(entities, message="entities_by_type")

    elif args.command == "get-by-alias":
        entities = manager.get_entities_by_alias(args.alias)
        if entities:
            emit_success(entities, message="entities_by_alias")
        else:
            emit_error("NOT_FOUND", f"未找到别名: {args.alias}")

    elif args.command == "get-aliases":
        aliases = manager.get_entity_aliases(args.entity)
        if aliases:
            emit_success({"entity": args.entity, "aliases": aliases}, message="aliases")
        else:
            emit_error("NOT_FOUND", f"{args.entity} 没有别名")

    elif args.command == "register-alias":
        success = manager.register_alias(args.alias, args.entity, args.type)
        if success:
            emit_success(
                {"alias": args.alias, "entity": args.entity, "type": args.type},
                message="alias_registered",
            )
        else:
            emit_error("ALIAS_EXISTS", f"别名已存在或注册失败: {args.alias}")

    elif args.command == "get-relationships":
        rels = manager.get_entity_relationships(args.entity, args.direction)
        emit_success(rels, message="relationships")

    elif args.command == "get-relationship-events":
        events = manager.get_relationship_events(
            entity_id=args.entity,
            direction=args.direction,
            from_chapter=args.from_chapter,
            to_chapter=args.to_chapter,
            limit=args.limit,
        )
        emit_success(events, message="relationship_events")

    elif args.command == "get-relationship-graph":
        graph = manager.build_relationship_subgraph(
            center_entity=args.center,
            depth=args.depth,
            chapter=args.chapter,
            top_edges=args.top_edges,
        )
        if args.format == "mermaid":
            emit_success({"mermaid": manager.render_relationship_subgraph_mermaid(graph)}, message="relationship_graph")
        else:
            emit_success(graph, message="relationship_graph")

    elif args.command == "get-relationship-timeline":
        timeline = manager.get_relationship_timeline(
            entity1=args.a,
            entity2=args.b,
            from_chapter=args.from_chapter,
            to_chapter=args.to_chapter,
            limit=args.limit,
        )
        emit_success(timeline, message="relationship_timeline")

    elif args.command == "get-state-changes":
        changes = manager.get_entity_state_changes(args.entity, args.limit)
        emit_success(changes, message="state_changes")

    elif args.command == "record-relationship-event":
        try:
            data = load_json_arg(args.data)
        except (TypeError, ValueError, json.JSONDecodeError):
            emit_error("INVALID_RELATIONSHIP_EVENT", "关系事件 JSON 无效")
        else:
            event = RelationshipEventMeta(
                from_entity=data.get("from_entity", ""),
                to_entity=data.get("to_entity", ""),
                type=data.get("type", ""),
                chapter=data.get("chapter", 0),
                action=data.get("action", "update"),
                polarity=data.get("polarity", 0),
                strength=data.get("strength", 0.5),
                description=data.get("description", ""),
                scene_index=data.get("scene_index", 0),
                evidence=data.get("evidence", ""),
                confidence=data.get("confidence", 1.0),
            )
            event_id = manager.record_relationship_event(event)
            if event_id > 0:
                emit_success({"id": event_id}, message="relationship_event_recorded")
            else:
                emit_error("INVALID_RELATIONSHIP_EVENT", "关系事件参数无效，未写入")

    elif args.command == "upsert-entity":
        data = load_json_arg(args.data)
        entity = EntityMeta(
            id=data["id"],
            type=data["type"],
            canonical_name=data["canonical_name"],
            tier=data.get("tier", "装饰"),
            desc=data.get("desc", ""),
            current=data.get("current", {}),
            first_appearance=data.get("first_appearance", 0),
            last_appearance=data.get("last_appearance", 0),
            is_protagonist=data.get("is_protagonist", False),
            is_archived=data.get("is_archived", False),
        )
        is_new = manager.upsert_entity(entity)
        emit_success({"id": entity.id, "created": is_new}, message="entity_upserted")

    elif args.command == "upsert-relationship":
        data = load_json_arg(args.data)
        rel = RelationshipMeta(
            from_entity=data["from_entity"],
            to_entity=data["to_entity"],
            type=data["type"],
            description=data.get("description", ""),
            chapter=data["chapter"],
        )
        is_new = manager.upsert_relationship(rel)
        emit_success(
            {"from": rel.from_entity, "to": rel.to_entity, "type": rel.type, "created": is_new},
            message="relationship_upserted",
        )

    elif args.command == "record-state-change":
        data = load_json_arg(args.data)
        change = StateChangeMeta(
            entity_id=data["entity_id"],
            field=data["field"],
            old_value=data.get("old_value", ""),
            new_value=data["new_value"],
            reason=data.get("reason", ""),
            chapter=data["chapter"],
        )
        record_id = manager.record_state_change(change)
        emit_success({"id": record_id, "entity": change.entity_id, "field": change.field}, message="state_change_recorded")

    # ==================== v5.4 无效事实命令处理 ====================

    elif args.command == "mark-invalid":
        invalid_id = manager.mark_invalid_fact(
            args.source_type,
            args.source_id,
            args.reason,
            marked_by=args.marked_by,
            chapter_discovered=args.chapter,
        )
        emit_success({"id": invalid_id}, message="invalid_marked")

    elif args.command == "resolve-invalid":
        ok = manager.resolve_invalid_fact(args.id, args.action)
        if ok:
            emit_success({"id": args.id, "action": args.action}, message="invalid_resolved")
        else:
            emit_error("INVALID_ACTION", f"无法处理 action: {args.action}")

    elif args.command == "list-invalid":
        rows = manager.list_invalid_facts(args.status)
        emit_success(rows, message="invalid_list")

    elif args.command == "save-review-metrics":
        data = load_json_arg(args.data)
        metrics = ReviewMetrics(
            start_chapter=data["start_chapter"],
            end_chapter=data["end_chapter"],
            overall_score=data.get("overall_score", 0.0),
            dimension_scores=data.get("dimension_scores", {}),
            severity_counts=data.get("severity_counts", {}),
            critical_issues=data.get("critical_issues", []),
            report_file=data.get("report_file", ""),
            notes=data.get("notes", ""),
        )
        manager.save_review_metrics(metrics)
        emit_success(
            {"start_chapter": metrics.start_chapter, "end_chapter": metrics.end_chapter},
            message="review_metrics_saved",
        )

    elif args.command == "get-recent-review-metrics":
        records = manager.get_recent_review_metrics(args.limit)
        emit_success(records, message="recent_review_metrics")

    elif args.command == "get-review-trend-stats":
        stats = manager.get_review_trend_stats(args.last_n)
        emit_success(stats, message="review_trend_stats")

    elif args.command == "save-writing-checklist-score":
        data = load_json_arg(args.data)
        metrics = WritingChecklistScoreMeta(
            chapter=data["chapter"],
            template=data.get("template", "plot"),
            total_items=data.get("total_items", 0),
            required_items=data.get("required_items", 0),
            completed_items=data.get("completed_items", 0),
            completed_required=data.get("completed_required", 0),
            total_weight=data.get("total_weight", 0.0),
            completed_weight=data.get("completed_weight", 0.0),
            completion_rate=data.get("completion_rate", 0.0),
            score=data.get("score", 0.0),
            score_breakdown=data.get("score_breakdown", {}),
            pending_items=data.get("pending_items", []),
            source=data.get("source", "context_manager"),
            notes=data.get("notes", ""),
        )
        manager.save_writing_checklist_score(metrics)
        emit_success({"chapter": metrics.chapter, "score": metrics.score}, message="writing_checklist_score_saved")

    elif args.command == "get-writing-checklist-score":
        score = manager.get_writing_checklist_score(args.chapter)
        if score:
            emit_success(score, message="writing_checklist_score")
        else:
            emit_error("NOT_FOUND", f"未找到第 {args.chapter} 章的写作清单评分")

    elif args.command == "get-recent-writing-checklist-scores":
        scores = manager.get_recent_writing_checklist_scores(args.limit)
        emit_success(scores, message="recent_writing_checklist_scores")

    elif args.command == "get-writing-checklist-score-trend":
        trend = manager.get_writing_checklist_score_trend(args.last_n)
        emit_success(trend, message="writing_checklist_score_trend")

    # ==================== v5.3 引入命令处理 ====================

    elif args.command == "get-debt-summary":
        summary = manager.get_debt_summary()
        emit_success(summary, message="debt_summary")

    elif args.command == "get-recent-reading-power":
        records = manager.get_recent_reading_power(args.limit)
        emit_success(records, message="recent_reading_power")

    elif args.command == "get-chapter-reading-power":
        record = manager.get_chapter_reading_power(args.chapter)
        if record:
            emit_success(record, message="chapter_reading_power")
        else:
            emit_error("NOT_FOUND", f"未找到第 {args.chapter} 章的追读力元数据")

    elif args.command == "get-pattern-usage-stats":
        stats = manager.get_pattern_usage_stats(args.last_n)
        emit_success(stats, message="pattern_usage_stats")

    elif args.command == "get-hook-type-stats":
        stats = manager.get_hook_type_stats(args.last_n)
        emit_success(stats, message="hook_type_stats")

    elif args.command == "get-reader-signals":
        signals = {
            "recent_reading_power": manager.get_recent_reading_power(args.limit),
            "pattern_usage_stats": manager.get_pattern_usage_stats(args.last_n),
            "hook_type_stats": manager.get_hook_type_stats(args.last_n),
        }
        emit_success(signals, message="reader_signals")

    elif args.command == "get-pending-overrides":
        overrides = manager.get_pending_overrides(args.before_chapter)
        emit_success(overrides, message="pending_overrides")

    elif args.command == "get-overdue-overrides":
        overrides = manager.get_overdue_overrides(args.current_chapter)
        emit_success(overrides, message="overdue_overrides")

    elif args.command == "get-active-debts":
        debts = manager.get_active_debts()
        emit_success(debts, message="active_debts")

    elif args.command == "get-overdue-debts":
        debts = manager.get_overdue_debts(args.current_chapter)
        emit_success(debts, message="overdue_debts")

    elif args.command == "accrue-interest":
        result = manager.accrue_interest(args.current_chapter)
        emit_success(result, message="interest_accrued", chapter=args.current_chapter)

    elif args.command == "pay-debt":
        result = manager.pay_debt(args.debt_id, args.amount, args.chapter)
        if "error" in result:
            emit_error("PAY_DEBT_FAILED", result["error"], chapter=args.chapter)
        else:
            emit_success(result, message="debt_payment", chapter=args.chapter)

    elif args.command == "create-override-contract":
        data = load_json_arg(args.data)
        contract = OverrideContractMeta(
            chapter=data["chapter"],
            constraint_type=data["constraint_type"],
            constraint_id=data["constraint_id"],
            rationale_type=data["rationale_type"],
            rationale_text=data.get("rationale_text", ""),
            payback_plan=data.get("payback_plan", ""),
            due_chapter=data["due_chapter"],
            status=data.get("status", "pending"),
        )
        contract_id = manager.create_override_contract(contract)
        emit_success({"id": contract_id}, message="override_contract_created")

    elif args.command == "create-debt":
        data = load_json_arg(args.data)
        debt = ChaseDebtMeta(
            debt_type=data["debt_type"],
            original_amount=data.get("original_amount", 1.0),
            current_amount=data.get("current_amount", data.get("original_amount", 1.0)),
            interest_rate=data.get("interest_rate", 0.1),
            source_chapter=data["source_chapter"],
            due_chapter=data["due_chapter"],
            override_contract_id=data.get("override_contract_id", 0),
            status=data.get("status", "active"),
        )
        debt_id = manager.create_debt(debt)
        emit_success({"id": debt_id, "debt_type": debt.debt_type}, message="debt_created")

    elif args.command == "fulfill-override":
        success = manager.fulfill_override(args.contract_id)
        if success:
            emit_success({"id": args.contract_id}, message="override_fulfilled")
        else:
            emit_error("NOT_FOUND", f"未找到 Override Contract #{args.contract_id}")

    elif args.command == "save-chapter-reading-power":
        data = load_json_arg(args.data)
        meta = ChapterReadingPowerMeta(
            chapter=data["chapter"],
            hook_type=data.get("hook_type", ""),
            hook_strength=data.get("hook_strength", "medium"),
            coolpoint_patterns=data.get("coolpoint_patterns", []),
            micropayoffs=data.get("micropayoffs", []),
            hard_violations=data.get("hard_violations", []),
            soft_suggestions=data.get("soft_suggestions", []),
            is_transition=data.get("is_transition", False),
            override_count=data.get("override_count", 0),
            debt_balance=data.get("debt_balance", 0.0),
        )
        manager.save_chapter_reading_power(meta)
        emit_success({"chapter": meta.chapter}, message="reading_power_saved")

    else:
        emit_error("UNKNOWN_COMMAND", "未指定有效命令", suggestion="请查看 --help")


if __name__ == "__main__":
    import sys
    if sys.platform == "win32":
        enable_windows_utf8_stdio()
    main()
