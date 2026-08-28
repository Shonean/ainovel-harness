#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
State Manager - 状态管理模块 (v5.4)

管理 state.json 的读写操作：
- 实体状态管理
- 进度追踪
- 关系记录

v5.1 变更（v5.4 沿用）:
- 集成 SQLStateManager，同步写入 SQLite (index.db)
- state.json 保留精简数据，大数据自动迁移到 SQLite
"""

import json
import logging
import sys
import time
from copy import deepcopy
from pathlib import Path

from runtime_compat import enable_windows_utf8_stdio
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, field, asdict
from datetime import datetime
import filelock

from .config import get_config
from .index_manager import IndexManager, EntityMeta, StateChangeMeta, RelationshipMeta, RelationshipEventMeta
from .observability import safe_append_perf_timing, safe_log_tool_call

# ── Pydantic schemas (merged from schemas.py) ──────────────────────────
from pydantic import BaseModel, Field, ConfigDict, ValidationError


class EntityAppeared(BaseModel):
    model_config = ConfigDict(extra="allow")
    id: str
    type: str
    mentions: List[str] = Field(default_factory=list)
    confidence: float = 1.0


class EntityNew(BaseModel):
    model_config = ConfigDict(extra="allow")
    suggested_id: str
    name: str
    type: str
    tier: str = "装饰"


class StateChange(BaseModel):
    model_config = ConfigDict(extra="allow")
    entity_id: str
    field: str
    old: Optional[str] = None
    new: str
    reason: Optional[str] = None


class RelationshipNew(BaseModel):
    model_config = ConfigDict(extra="allow", populate_by_name=True)
    from_entity: str = Field(alias="from")
    to_entity: str = Field(alias="to")
    type: str
    description: Optional[str] = None
    chapter: Optional[int] = None


class UncertainCandidate(BaseModel):
    model_config = ConfigDict(extra="allow")
    type: str
    id: str


class UncertainMention(BaseModel):
    model_config = ConfigDict(extra="allow")
    mention: str
    candidates: List[UncertainCandidate] = Field(default_factory=list)
    confidence: float = 0.0
    adopted: Optional[str] = None


class TimelineEvent(BaseModel):
    model_config = ConfigDict(extra="allow")
    event: str
    chapter: Optional[int] = None
    time_hint: Optional[str] = None
    event_type: Optional[str] = None


class WorldRule(BaseModel):
    model_config = ConfigDict(extra="allow")
    rule: str
    scope: Optional[str] = None
    domain: Optional[str] = None
    field: Optional[str] = None


class OpenLoop(BaseModel):
    model_config = ConfigDict(extra="allow")
    content: str
    status: Optional[str] = None
    urgency: Optional[float] = None
    planted_chapter: Optional[int] = None
    expected_payoff: Optional[str] = None


class ReaderPromise(BaseModel):
    model_config = ConfigDict(extra="allow")
    content: str
    type: Optional[str] = None
    target: Optional[str] = None


class MemoryFacts(BaseModel):
    model_config = ConfigDict(extra="allow")
    timeline_events: List[TimelineEvent] = Field(default_factory=list)
    world_rules: List[WorldRule] = Field(default_factory=list)
    open_loops: List[OpenLoop] = Field(default_factory=list)
    reader_promises: List[ReaderPromise] = Field(default_factory=list)


class DataAgentOutput(BaseModel):
    model_config = ConfigDict(extra="allow")
    entities_appeared: List[EntityAppeared] = Field(default_factory=list)
    entities_new: List[EntityNew] = Field(default_factory=list)
    state_changes: List[StateChange] = Field(default_factory=list)
    relationships_new: List[RelationshipNew] = Field(default_factory=list)
    scenes_chunked: int = 0
    uncertain: List[UncertainMention] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)
    memory_facts: Optional[MemoryFacts] = None


class ErrorSchema(BaseModel):
    model_config = ConfigDict(extra="allow")
    code: str
    message: str
    suggestion: Optional[str] = None
    details: Optional[Dict[str, Any]] = None


def validate_data_agent_output(payload: Dict[str, Any]) -> DataAgentOutput:
    return DataAgentOutput.model_validate(payload)


def format_validation_error(exc: ValidationError) -> Dict[str, Any]:
    return {
        "code": "SCHEMA_VALIDATION_FAILED",
        "message": "数据结构校验失败",
        "details": {"errors": exc.errors()},
        "suggestion": "请检查 data-agent 输出字段是否完整且类型正确",
    }


def normalize_data_agent_output(payload: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(payload, dict):
        return {}

    payload = dict(payload)  # 操作副本

    def _ensure_list(key: str):
        value = payload.get(key)
        if value is None:
            payload[key] = []
        elif isinstance(value, list):
            return
        else:
            payload[key] = [value]

    for key in [
        "entities_appeared",
        "entities_new",
        "state_changes",
        "relationships_new",
        "uncertain",
        "warnings",
    ]:
        _ensure_list(key)

    memory_facts = payload.get("memory_facts")
    if memory_facts is None:
        payload["memory_facts"] = {}
    elif not isinstance(memory_facts, dict):
        payload["memory_facts"] = {}
    else:
        memory_facts = dict(memory_facts)
        payload["memory_facts"] = memory_facts
        for key in ["timeline_events", "world_rules", "open_loops", "reader_promises"]:
            value = memory_facts.get(key)
            if value is None:
                memory_facts[key] = []
            elif not isinstance(value, list):
                memory_facts[key] = [value]

    payload.setdefault("scenes_chunked", 0)

    return payload


logger = logging.getLogger(__name__)


try:
    # 当 scripts 目录在 sys.path 中（常见：从 scripts/ 运行）
    from security_utils import atomic_write_json, read_json_safe
except ImportError:  # pragma: no cover
    # 当以 `python -m scripts.data_modules...` 从仓库根目录运行
    from scripts.security_utils import atomic_write_json, read_json_safe


@dataclass
class EntityState:
    """实体状态"""
    id: str
    name: str
    type: str  # 角色/地点/物品/势力
    tier: str = "装饰"  # 核心/重要/次要/装饰
    aliases: List[str] = field(default_factory=list)
    attributes: Dict[str, Any] = field(default_factory=dict)
    first_appearance: int = 0
    last_appearance: int = 0


@dataclass
class Relationship:
    """实体关系"""
    from_entity: str
    to_entity: str
    type: str
    description: str
    chapter: int


@dataclass
class StateChange:
    """状态变化记录"""
    entity_id: str
    field: str
    old_value: Any
    new_value: Any
    reason: str
    chapter: int
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())


@dataclass
class _EntityPatch:
    """待写入的实体增量补丁（用于锁内合并）"""
    entity_type: str
    entity_id: str
    replace: bool = False
    base_entity: Optional[Dict[str, Any]] = None  # 新建实体时的完整快照（用于填充缺失字段）
    top_updates: Dict[str, Any] = field(default_factory=dict)
    current_updates: Dict[str, Any] = field(default_factory=dict)
    appearance_chapter: Optional[int] = None


def _unique_aliases(*groups: Any) -> List[str]:
    result = []
    seen = set()
    for group in groups:
        if not group:
            continue
        values = [group] if isinstance(group, str) else group
        for value in values:
            alias = str(value).strip() if value is not None else ""
            if alias and alias not in seen:
                seen.add(alias)
                result.append(alias)
    return result


# ── SQLStateManager (merged from sql_state_manager.py) ──

@dataclass
class EntityData:
    """实体数据（用于 Data Agent 输入）"""
    id: str
    type: str  # 角色/地点/物品/势力/招式
    name: str
    tier: str = "装饰"
    desc: str = ""
    current: Dict[str, Any] = field(default_factory=dict)
    aliases: List[str] = field(default_factory=list)
    first_appearance: int = 0
    last_appearance: int = 0
    is_protagonist: bool = False


class SQLStateManager:
    """
    SQLite 状态管理器（v5.1 引入，v5.4 沿用）

    提供与 StateManager 兼容的接口，但数据存储在 SQLite (index.db) 中。
    用于替代 state.json 中膨胀的数据结构。

    用法:
    ```python
    manager = SQLStateManager(config)

    # 写入实体
    manager.upsert_entity(EntityData(
        id="linyue",
        type="角色",
        name="林越",
        tier="核心",
        current={"realm": "武师", "location": "天云宗"},
        aliases=["小炎子", "废柴"],
        is_protagonist=True
    ))

    # 写入状态变化
    manager.record_state_change(
        entity_id="linyue",
        field="realm",
        old_value="武者",
        new_value="武师",
        reason="闭关突破",
        chapter=100
    )

    # 写入关系
    manager.upsert_relationship(
        from_entity="linyue",
        to_entity="laoyaoshi",
        type="师徒",
        description="药师收林越为徒",
        chapter=5
    )

    # 读取
    protagonist = manager.get_protagonist()
    core_entities = manager.get_core_entities()
    changes = manager.get_recent_state_changes(limit=50)
    ```
    """

    # v5.0 引入的实体类型
    ENTITY_TYPES = ["角色", "地点", "物品", "势力", "招式"]

    def __init__(self, config=None):
        self.config = config or get_config()
        self._index_manager = IndexManager(config)

    def _unique_aliases(self, *groups: Any) -> List[str]:
        """合并 Data Agent 传入的 aliases/mentions，保持顺序并去重。"""
        result = []
        seen = set()

        for group in groups:
            if not group:
                continue
            values = [group] if isinstance(group, str) else group
            for value in values:
                alias = str(value).strip() if value is not None else ""
                if alias and alias not in seen:
                    seen.add(alias)
                    result.append(alias)

        return result

    # ==================== 实体操作 ====================

    def upsert_entity(self, entity: EntityData) -> bool:
        """
        插入或更新实体

        自动处理：
        - 实体基本信息写入 entities 表
        - 别名写入 aliases 表
        - canonical_name 自动添加为别名

        返回: 是否为新实体
        """
        # 构建 EntityMeta
        meta = EntityMeta(
            id=entity.id,
            type=entity.type,
            canonical_name=entity.name,
            tier=entity.tier,
            desc=entity.desc,
            current=entity.current,
            first_appearance=entity.first_appearance,
            last_appearance=entity.last_appearance,
            is_protagonist=entity.is_protagonist,
            is_archived=False
        )

        is_new = self._index_manager.upsert_entity(meta)

        # 注册别名
        # 1. canonical_name 本身作为别名
        self._index_manager.register_alias(entity.name, entity.id, entity.type)

        # 2. 其他别名
        for alias in entity.aliases:
            if alias and alias != entity.name:
                self._index_manager.register_alias(alias, entity.id, entity.type)

        return is_new

    def get_entity(self, entity_id: str) -> Optional[Dict]:
        """获取实体详情"""
        entity = self._index_manager.get_entity(entity_id)
        if entity:
            # 添加别名
            entity["aliases"] = self._index_manager.get_entity_aliases(entity["id"])
        return entity

    def get_entities_by_type(self, entity_type: str, include_archived: bool = False) -> List[Dict]:
        """按类型获取实体"""
        entities = self._index_manager.get_entities_by_type(entity_type, include_archived)
        for e in entities:
            e["aliases"] = self._index_manager.get_entity_aliases(e["id"])
        return entities

    def get_core_entities(self) -> List[Dict]:
        """
        获取核心实体（用于 Context Agent 全量加载）

        返回所有 tier=核心/重要 或 is_protagonist=1 的实体
        （次要/装饰实体按需查询，不全量加载）
        """
        entities = self._index_manager.get_core_entities()
        for e in entities:
            e["aliases"] = self._index_manager.get_entity_aliases(e["id"])
        return entities

    def get_protagonist(self) -> Optional[Dict]:
        """获取主角实体"""
        protagonist = self._index_manager.get_protagonist()
        if protagonist:
            protagonist["aliases"] = self._index_manager.get_entity_aliases(protagonist["id"])
        return protagonist

    def update_entity_current(self, entity_id: str, updates: Dict) -> bool:
        """增量更新实体的 current 字段"""
        return self._index_manager.update_entity_current(entity_id, updates)

    def resolve_alias(self, alias: str) -> List[Dict]:
        """
        根据别名解析实体（一对多）

        返回所有匹配的实体
        """
        return self._index_manager.get_entities_by_alias(alias)

    def register_alias(self, alias: str, entity_id: str, entity_type: str) -> bool:
        """注册别名"""
        return self._index_manager.register_alias(alias, entity_id, entity_type)

    # ==================== 状态变化操作 ====================

    def record_state_change(
        self,
        entity_id: str,
        field: str,
        old_value: Any,
        new_value: Any,
        reason: str,
        chapter: int
    ) -> int:
        """
        记录状态变化

        返回: 记录 ID
        """
        change = StateChangeMeta(
            entity_id=entity_id,
            field=field,
            old_value=str(old_value) if old_value is not None else "",
            new_value=str(new_value),
            reason=reason,
            chapter=chapter
        )
        return self._index_manager.record_state_change(change)

    def get_entity_state_changes(self, entity_id: str, limit: int = 20) -> List[Dict]:
        """获取实体的状态变化历史"""
        return self._index_manager.get_entity_state_changes(entity_id, limit)

    def get_recent_state_changes(self, limit: int = 50) -> List[Dict]:
        """获取最近的状态变化"""
        return self._index_manager.get_recent_state_changes(limit)

    def get_chapter_state_changes(self, chapter: int) -> List[Dict]:
        """获取某章的所有状态变化"""
        return self._index_manager.get_chapter_state_changes(chapter)

    # ==================== 关系操作 ====================

    def upsert_relationship(
        self,
        from_entity: str,
        to_entity: str,
        type: str,
        description: str,
        chapter: int
    ) -> bool:
        """
        插入或更新关系

        返回: 是否为新关系
        """
        rel = RelationshipMeta(
            from_entity=from_entity,
            to_entity=to_entity,
            type=type,
            description=description,
            chapter=chapter
        )
        return self._index_manager.upsert_relationship(rel)

    def get_entity_relationships(self, entity_id: str, direction: str = "both") -> List[Dict]:
        """获取实体的关系"""
        return self._index_manager.get_entity_relationships(entity_id, direction)

    def get_relationship_between(self, entity1: str, entity2: str) -> List[Dict]:
        """获取两个实体之间的所有关系"""
        return self._index_manager.get_relationship_between(entity1, entity2)

    def get_recent_relationships(self, limit: int = 30) -> List[Dict]:
        """获取最近建立的关系"""
        return self._index_manager.get_recent_relationships(limit)

    # ==================== 批量写入（供 Data Agent 使用） ====================

    def process_chapter_entities(
        self,
        chapter: int,
        entities_appeared: List[Dict],
        entities_new: List[Dict],
        state_changes: List[Dict],
        relationships_new: List[Dict]
    ) -> Dict[str, int]:
        """
        处理章节的实体数据（Data Agent 主入口）

        参数:
        - chapter: 章节号
        - entities_appeared: 出场的已有实体
          [{"id": "linyue", "type": "角色", "mentions": ["林越", "他"], "confidence": 0.95}]
        - entities_new: 新发现的实体
          [{"suggested_id": "hongyi_girl", "name": "红衣女子", "type": "角色", "tier": "装饰"}]
        - state_changes: 状态变化
          [{"entity_id": "linyue", "field": "realm", "old": "武者", "new": "武师", "reason": "突破"}]
        - relationships_new: 新关系
          [{"from": "linyue", "to": "hongyi_girl", "type": "相识", "description": "初次见面"}]

        返回: 写入统计
        """
        stats = {
            "entities_updated": 0,
            "entities_created": 0,
            "state_changes": 0,
            "relationships": 0,
            "aliases": 0
        }

        # 1. 处理出场实体（更新 last_appearance）
        for entity in entities_appeared:
            entity_id = entity.get("id")
            if not entity_id:
                continue

            existing = self._index_manager.get_entity(entity_id)
            resolved_id = existing.get("id") if existing else entity_id

            if existing:
                self._index_manager.update_entity_current(resolved_id, {})  # 触发 updated_at
                entity_type = entity.get("type") or existing.get("type", "角色")
                for alias in self._unique_aliases(entity.get("mentions", [])):
                    if self._index_manager.register_alias(alias, resolved_id, entity_type):
                        stats["aliases"] += 1

            # 更新 last_appearance
            if existing:
                # 使用 SQL 直接更新 last_appearance
                self._update_last_appearance(resolved_id, chapter)
                stats["entities_updated"] += 1

            # 记录出场（保留原有逻辑）
            self._index_manager.record_appearance(
                entity_id=resolved_id,
                chapter=chapter,
                mentions=entity.get("mentions", []),
                confidence=entity.get("confidence", 1.0)
            )

        # 2. 处理新实体
        for entity in entities_new:
            suggested_id = entity.get("suggested_id") or entity.get("id")
            if not suggested_id:
                continue

            entity_data = EntityData(
                id=suggested_id,
                type=entity.get("type", "角色"),
                name=entity.get("name", suggested_id),
                tier=entity.get("tier", "装饰"),
                desc=entity.get("desc", ""),
                current=entity.get("current", {}),
                aliases=self._unique_aliases(
                    entity.get("aliases", []), entity.get("mentions", [])
                ),
                first_appearance=chapter,
                last_appearance=chapter,
                is_protagonist=entity.get("is_protagonist", False)
            )
            is_new = self.upsert_entity(entity_data)
            if is_new:
                stats["entities_created"] += 1
            else:
                stats["entities_updated"] += 1

            # 统计别名
            stats["aliases"] += 1 + len(entity_data.aliases)

            # 记录新实体的首次出场（解决 appearances 缺失问题）
            mentions = entity.get("mentions", [])
            if not mentions:
                mentions = [entity_data.name]  # 至少包含实体名
            self._index_manager.record_appearance(
                entity_id=suggested_id,
                chapter=chapter,
                mentions=mentions,
                confidence=entity.get("confidence", 1.0)
            )

        # 3. 处理状态变化
        for change in state_changes:
            entity_id = change.get("entity_id")
            if not entity_id:
                continue

            self.record_state_change(
                entity_id=entity_id,
                field=change.get("field", ""),
                old_value=change.get("old", change.get("old_value", "")),
                new_value=change.get("new", change.get("new_value", "")),
                reason=change.get("reason", ""),
                chapter=chapter
            )
            stats["state_changes"] += 1

            # 同步更新实体的 current
            field_name = change.get("field")
            new_value = change.get("new", change.get("new_value"))
            # 注意：new_value 可能是 0/""/False 等 falsy 值，需要用 is not None 判断
            if field_name and new_value is not None:
                self._index_manager.update_entity_current(entity_id, {field_name: new_value})

        # 4. 处理新关系
        for rel in relationships_new:
            from_entity = rel.get("from", rel.get("from_entity"))
            to_entity = rel.get("to", rel.get("to_entity"))
            if not from_entity or not to_entity:
                continue
            rel_type = rel.get("type", "相识")
            description = rel.get("description", "")

            # v5.5: 先记录关系事件，再更新关系快照
            self._index_manager.record_relationship_event(
                RelationshipEventMeta(
                    from_entity=from_entity,
                    to_entity=to_entity,
                    type=rel_type,
                    chapter=chapter,
                    action=rel.get("action", "update"),
                    polarity=rel.get("polarity", 0),
                    strength=rel.get("strength", 0.5),
                    description=description,
                    scene_index=rel.get("scene_index", 0),
                    evidence=rel.get("evidence", ""),
                    confidence=rel.get("confidence", 1.0),
                )
            )

            self.upsert_relationship(
                from_entity=from_entity,
                to_entity=to_entity,
                type=rel_type,
                description=description,
                chapter=chapter
            )
            stats["relationships"] += 1

        return stats

    def _update_last_appearance(self, entity_id: str, chapter: int):
        """更新实体的 last_appearance"""
        with self._index_manager._get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                UPDATE entities SET
                    last_appearance = MAX(last_appearance, ?),
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
            """, (chapter, entity_id))
            conn.commit()

    # ==================== 统计 ====================

    def get_stats(self) -> Dict[str, int]:
        """获取统计信息"""
        return self._index_manager.get_stats()

    # ==================== 格式转换（兼容性） ====================

    def export_to_entities_v3_format(self) -> Dict[str, Dict[str, Dict]]:
        """
        导出为 entities_v3 格式（用于兼容性）

        返回: {"角色": {"linyue": {...}}, "地点": {...}, ...}
        """
        result = {t: {} for t in self.ENTITY_TYPES}

        for entity_type in self.ENTITY_TYPES:
            entities = self.get_entities_by_type(entity_type, include_archived=True)
            for e in entities:
                entity_dict = {
                    "canonical_name": e.get("canonical_name"),
                    "name": e.get("canonical_name"),  # 兼容性别名
                    "tier": e.get("tier", "装饰"),
                    "aliases": e.get("aliases", []),
                    "desc": e.get("desc", ""),
                    "current": e.get("current_json", {}),
                    "history": [],  # 历史记录需要从 state_changes 表查询
                    "first_appearance": e.get("first_appearance", 0),
                    "last_appearance": e.get("last_appearance", 0)
                }
                if e.get("is_protagonist"):
                    entity_dict["is_protagonist"] = True
                result[entity_type][e["id"]] = entity_dict

        return result

    def export_to_alias_index_format(self) -> Dict[str, List[Dict[str, str]]]:
        """
        导出为 alias_index 格式（用于兼容性）

        返回: {"林越": [{"type": "角色", "id": "linyue"}], ...}
        """
        result = {}

        with self._index_manager._get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT alias, entity_id, entity_type FROM aliases")
            for row in cursor.fetchall():
                alias = row["alias"]
                if alias not in result:
                    result[alias] = []
                result[alias].append({
                    "type": row["entity_type"],
                    "id": row["entity_id"]
                })

        return result


# ==================== CLI 接口 ====================

class StateManager:
    """状态管理器（v5.1 entities_v3 格式 + SQLite 同步，v5.4 沿用）"""

    # v5.0 引入的实体类型
    ENTITY_TYPES = ["角色", "地点", "物品", "势力", "招式"]

    # 章节状态机（单调递进）
    CHAPTER_STATUS_ORDER = ["chapter_drafted", "chapter_reviewed", "chapter_committed"]

    def __init__(self, config=None, enable_sqlite_sync: bool = True):
        """
        初始化状态管理器

        参数:
        - config: 配置对象
        - enable_sqlite_sync: 是否启用 SQLite 同步 (默认 True)
        """
        self.config = config or get_config()
        self._state: Dict[str, Any] = {}
        # 与 security_utils.atomic_write_json 保持一致：state.json.lock
        self._lock_path = self.config.state_file.with_suffix(self.config.state_file.suffix + ".lock")

        # v5.1 引入: SQLite 同步
        self._enable_sqlite_sync = enable_sqlite_sync
        self._sql_state_manager = None
        if enable_sqlite_sync:
            try:
                self._sql_state_manager = SQLStateManager(self.config)
            except ImportError:
                pass  # SQLStateManager 不可用时静默降级

        # 待写入的增量（锁内重读 + 合并 + 写入）
        self._pending_entity_patches: Dict[tuple[str, str], _EntityPatch] = {}
        self._pending_alias_entries: Dict[str, List[Dict[str, str]]] = {}
        self._pending_state_changes: List[Dict[str, Any]] = []
        self._pending_structured_relationships: List[Dict[str, Any]] = []
        self._pending_disambiguation_warnings: List[Dict[str, Any]] = []
        self._pending_disambiguation_pending: List[Dict[str, Any]] = []
        self._pending_progress_chapter: Optional[int] = None
        self._pending_progress_words_delta: int = 0
        self._pending_chapter_meta: Dict[str, Any] = {}

        # v5.1 引入: 缓存待同步到 SQLite 的数据
        self._pending_sqlite_data: Dict[str, Any] = {
            "entities_appeared": [],
            "entities_new": [],
            "state_changes": [],
            "relationships_new": [],
            "chapter": None
        }

        self._load_state()

    def _now_progress_timestamp(self) -> str:
        return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    def _ensure_state_schema(self, state: Dict[str, Any]) -> Dict[str, Any]:
        """确保 state.json 具备运行所需的关键字段（尽量不破坏既有数据）。"""
        if not isinstance(state, dict):
            state = {}

        state.setdefault("project_info", {})
        state.setdefault("progress", {})
        state.setdefault("protagonist_state", {})

        # relationships: 旧版本可能是 list（实体关系），v5.0 运行态用 dict（人物关系/重要关系）
        relationships = state.get("relationships")
        if isinstance(relationships, list):
            state.setdefault("structured_relationships", [])
            if isinstance(state.get("structured_relationships"), list):
                state["structured_relationships"].extend(relationships)
            state["relationships"] = {}
        elif not isinstance(relationships, dict):
            state["relationships"] = {}

        state.setdefault("world_settings", {"power_system": [], "factions": [], "locations": []})
        state.setdefault("plot_threads", {"active_threads": [], "foreshadowing": []})
        state.setdefault("review_checkpoints", [])
        state.setdefault("chapter_meta", {})
        state.setdefault(
            "strand_tracker",
            {
                "last_quest_chapter": 0,
                "last_fire_chapter": 0,
                "last_constellation_chapter": 0,
                "current_dominant": "quest",
                "chapters_since_switch": 0,
                "history": [],
            },
        )

        entities_v3 = state.get("entities_v3")
        # v5.1 引入: entities_v3, alias_index, state_changes, structured_relationships 已迁移到 index.db
        # 不再在 state.json 中初始化或维护这些字段

        if not isinstance(state.get("disambiguation_warnings"), list):
            state["disambiguation_warnings"] = []

        if not isinstance(state.get("disambiguation_pending"), list):
            state["disambiguation_pending"] = []

        # progress 基础字段
        progress = state["progress"]
        if not isinstance(progress, dict):
            progress = {}
            state["progress"] = progress
        progress.setdefault("current_chapter", 0)
        progress.setdefault("total_words", 0)
        progress.setdefault("last_updated", self._now_progress_timestamp())

        return state

    def _load_state(self):
        """加载状态文件"""
        if self.config.state_file.exists():
            self._state = read_json_safe(self.config.state_file, default={})
            self._state = self._ensure_state_schema(self._state)
        else:
            self._state = self._ensure_state_schema({})

    def save_state(self):
        """
        保存状态文件（锁内重读 + 合并 + 原子写入）。

        解决多 Agent 并行下的“读-改-写覆盖”风险：
        - 获取锁
        - 重新读取磁盘最新 state.json
        - 仅合并本实例产生的增量（pending_*）
        - 原子化写入
        """
        # 无增量时不写入，避免无意义覆盖
        has_pending = any(
            [
                self._pending_entity_patches,
                self._pending_alias_entries,
                self._pending_state_changes,
                self._pending_structured_relationships,
                self._pending_disambiguation_warnings,
                self._pending_disambiguation_pending,
                self._pending_chapter_meta,
                self._pending_progress_chapter is not None,
                self._pending_progress_words_delta != 0,
            ]
        )
        if not has_pending:
            return

        self.config.ensure_dirs()

        lock = filelock.FileLock(str(self._lock_path), timeout=10)
        try:
            with lock:
                disk_state = read_json_safe(self.config.state_file, default={})
                disk_state = self._ensure_state_schema(disk_state)

                # progress（合并为 max(chapter) + words_delta 累加）
                if self._pending_progress_chapter is not None or self._pending_progress_words_delta != 0:
                    progress = disk_state.get("progress", {})
                    if not isinstance(progress, dict):
                        progress = {}
                        disk_state["progress"] = progress

                    try:
                        current_chapter = int(progress.get("current_chapter", 0) or 0)
                    except (TypeError, ValueError):
                        current_chapter = 0

                    if self._pending_progress_chapter is not None:
                        progress["current_chapter"] = max(current_chapter, int(self._pending_progress_chapter))

                    if self._pending_progress_words_delta:
                        try:
                            total_words = int(progress.get("total_words", 0) or 0)
                        except (TypeError, ValueError):
                            total_words = 0
                        progress["total_words"] = total_words + int(self._pending_progress_words_delta)

                    progress["last_updated"] = self._now_progress_timestamp()

                # v5.1 引入: 强制使用 SQLite 模式，移除大数据字段
                # 确保 state.json 中不存在这些膨胀字段
                for field in ["entities_v3", "alias_index", "state_changes", "structured_relationships"]:
                    disk_state.pop(field, None)
                # 标记已迁移
                disk_state["_migrated_to_sqlite"] = True

                # disambiguation_warnings（追加去重 + 截断）
                if self._pending_disambiguation_warnings:
                    warnings_list = disk_state.get("disambiguation_warnings")
                    if not isinstance(warnings_list, list):
                        warnings_list = []
                        disk_state["disambiguation_warnings"] = warnings_list

                    def _warn_key(w: Dict[str, Any]) -> tuple:
                        return (
                            w.get("chapter"),
                            w.get("mention"),
                            w.get("chosen_id"),
                            w.get("confidence"),
                        )

                    existing_keys = {_warn_key(w) for w in warnings_list if isinstance(w, dict)}
                    for w in self._pending_disambiguation_warnings:
                        if not isinstance(w, dict):
                            continue
                        k = _warn_key(w)
                        if k in existing_keys:
                            continue
                        warnings_list.append(w)
                        existing_keys.add(k)

                    # 只保留最近 N 条，避免文件无限增长
                    max_keep = self.config.max_disambiguation_warnings
                    if len(warnings_list) > max_keep:
                        disk_state["disambiguation_warnings"] = warnings_list[-max_keep:]

                # disambiguation_pending（追加去重 + 截断）
                if self._pending_disambiguation_pending:
                    pending_list = disk_state.get("disambiguation_pending")
                    if not isinstance(pending_list, list):
                        pending_list = []
                        disk_state["disambiguation_pending"] = pending_list

                    def _pending_key(w: Dict[str, Any]) -> tuple:
                        return (
                            w.get("chapter"),
                            w.get("mention"),
                            w.get("suggested_id"),
                            w.get("confidence"),
                        )

                    existing_keys = {_pending_key(w) for w in pending_list if isinstance(w, dict)}
                    for w in self._pending_disambiguation_pending:
                        if not isinstance(w, dict):
                            continue
                        k = _pending_key(w)
                        if k in existing_keys:
                            continue
                        pending_list.append(w)
                        existing_keys.add(k)

                    max_keep = self.config.max_disambiguation_pending
                    if len(pending_list) > max_keep:
                        disk_state["disambiguation_pending"] = pending_list[-max_keep:]

                # chapter_meta（新增：按章节号覆盖写入）
                if self._pending_chapter_meta:
                    chapter_meta = disk_state.get("chapter_meta")
                    if not isinstance(chapter_meta, dict):
                        chapter_meta = {}
                        disk_state["chapter_meta"] = chapter_meta
                    chapter_meta.update(self._pending_chapter_meta)

                # 原子写入（锁已持有，不再二次加锁）
                atomic_write_json(self.config.state_file, disk_state, use_lock=False, backup=True)

                # v5.1 引入: 同步到 SQLite（失败时保留 pending 以便重试）
                sqlite_pending_snapshot = self._snapshot_sqlite_pending()
                sqlite_sync_ok = self._sync_to_sqlite()

                # 同步内存为磁盘最新快照
                self._state = disk_state

                # state.json 侧 pending 已写盘，直接清空
                self._pending_disambiguation_warnings.clear()
                self._pending_disambiguation_pending.clear()
                self._pending_chapter_meta.clear()
                self._pending_progress_chapter = None
                self._pending_progress_words_delta = 0

                # SQLite 侧 pending：成功后清空，失败则恢复快照（避免静默丢数据）
                if sqlite_sync_ok:
                    self._pending_entity_patches.clear()
                    self._pending_alias_entries.clear()
                    self._pending_state_changes.clear()
                    self._pending_structured_relationships.clear()
                    self._clear_pending_sqlite_data()
                else:
                    self._restore_sqlite_pending(sqlite_pending_snapshot)

        except filelock.Timeout:
            raise RuntimeError("无法获取 state.json 文件锁，请稍后重试")

    def _sync_to_sqlite(self) -> bool:
        """同步待处理数据到 SQLite（v5.1 引入，v5.4 沿用）"""
        if not self._sql_state_manager:
            return True

        # 方式1: 通过 process_chapter_result 收集的数据
        sqlite_data = self._pending_sqlite_data
        chapter = sqlite_data.get("chapter")

        # 记录已处理的 (entity_id, chapter) 组合，避免重复写入 appearances
        processed_appearances = set()

        if chapter is not None:
            try:
                self._sql_state_manager.process_chapter_entities(
                    chapter=chapter,
                    entities_appeared=sqlite_data.get("entities_appeared", []),
                    entities_new=sqlite_data.get("entities_new", []),
                    state_changes=sqlite_data.get("state_changes", []),
                    relationships_new=sqlite_data.get("relationships_new", [])
                )
                # 标记已处理的出场记录
                for entity in sqlite_data.get("entities_appeared", []):
                    if entity.get("id"):
                        processed_appearances.add((entity.get("id"), chapter))
                for entity in sqlite_data.get("entities_new", []):
                    eid = entity.get("suggested_id") or entity.get("id")
                    if eid:
                        processed_appearances.add((eid, chapter))
            except Exception as exc:
                logger.warning("SQLite sync failed (process_chapter_entities): %s", exc)
                return False

        # 方式2: 使用 add_entity/update_entity 收集的增量数据。
        # 数据缓存在 _pending_entity_patches 等变量中。
        return self._sync_pending_patches_to_sqlite(processed_appearances)

    def _sync_pending_patches_to_sqlite(self, processed_appearances: set = None) -> bool:
        """同步 _pending_entity_patches 等到 SQLite（v5.1 引入，v5.4 沿用）

        Args:
            processed_appearances: 已通过 process_chapter_entities 处理的 (entity_id, chapter) 集合，
                                   用于避免重复写入 appearances 表（防止覆盖 mentions）
        """
        if not self._sql_state_manager:
            return True

        if processed_appearances is None:
            processed_appearances = set()

        # 元数据字段（不应写入 current_json）
        METADATA_FIELDS = {"canonical_name", "tier", "desc", "is_protagonist", "is_archived"}

        try:
            from .index_manager import EntityMeta, StateChangeMeta, RelationshipMeta, RelationshipEventMeta

            # 同步实体补丁
            for (entity_type, entity_id), patch in self._pending_entity_patches.items():
                if patch.base_entity:
                    # 新实体
                    entity_data = EntityData(
                        id=entity_id,
                        type=entity_type,
                        name=patch.base_entity.get("canonical_name", entity_id),
                        tier=patch.base_entity.get("tier", "装饰"),
                        desc=patch.base_entity.get("desc", ""),
                        current=patch.base_entity.get("current", {}),
                        aliases=patch.base_entity.get("aliases", []),
                        first_appearance=patch.base_entity.get("first_appearance", 0),
                        last_appearance=patch.base_entity.get("last_appearance", 0),
                        is_protagonist=patch.base_entity.get("is_protagonist", False)
                    )
                    self._sql_state_manager.upsert_entity(entity_data)

                    # 记录首次出场（跳过已处理的，避免覆盖 mentions）
                    if patch.appearance_chapter is not None:
                        if (entity_id, patch.appearance_chapter) not in processed_appearances:
                            self._sql_state_manager._index_manager.record_appearance(
                                entity_id=entity_id,
                                chapter=patch.appearance_chapter,
                                mentions=[entity_data.name],
                                confidence=1.0,
                                skip_if_exists=True  # 关键：不覆盖已有记录
                            )
                else:
                    # 更新现有实体
                    has_metadata_updates = bool(patch.top_updates and
                                                 any(k in METADATA_FIELDS for k in patch.top_updates))

                    # 非元数据的 top_updates 应该当作 current 更新
                    # 例如：realm, layer, location 等状态字段
                    non_metadata_top_updates = {
                        k: v for k, v in patch.top_updates.items()
                        if k not in METADATA_FIELDS
                    } if patch.top_updates else {}

                    # 合并 current_updates 和非元数据的 top_updates
                    effective_current_updates = {**non_metadata_top_updates}
                    if patch.current_updates:
                        effective_current_updates.update(patch.current_updates)

                    if has_metadata_updates:
                        # 有元数据更新：使用 upsert_entity(update_metadata=True)
                        existing = self._sql_state_manager.get_entity(entity_id)
                        if existing:
                            # 合并 current
                            current = existing.get("current_json", {})
                            if isinstance(current, str):
                                import json
                                current = json.loads(current) if current else {}
                            if effective_current_updates:
                                current.update(effective_current_updates)

                            new_canonical_name = patch.top_updates.get("canonical_name")
                            old_canonical_name = existing.get("canonical_name", "")

                            entity_meta = EntityMeta(
                                id=entity_id,
                                type=existing.get("type", entity_type),
                                canonical_name=new_canonical_name or old_canonical_name,
                                tier=patch.top_updates.get("tier", existing.get("tier", "装饰")),
                                desc=patch.top_updates.get("desc", existing.get("desc", "")),
                                current=current,
                                first_appearance=existing.get("first_appearance", 0),
                                last_appearance=patch.appearance_chapter or existing.get("last_appearance", 0),
                                is_protagonist=patch.top_updates.get("is_protagonist", existing.get("is_protagonist", False)),
                                is_archived=patch.top_updates.get("is_archived", existing.get("is_archived", False))
                            )
                            self._sql_state_manager._index_manager.upsert_entity(entity_meta, update_metadata=True)

                            # 如果 canonical_name 改名，自动注册新名字为 alias
                            if new_canonical_name and new_canonical_name != old_canonical_name:
                                self._sql_state_manager.register_alias(
                                    new_canonical_name, entity_id, existing.get("type", entity_type)
                                )
                    elif effective_current_updates:
                        # 只有 current 更新（包括非元数据的 top_updates）
                        self._sql_state_manager.update_entity_current(entity_id, effective_current_updates)

                    # 更新 last_appearance 并记录出场
                    if patch.appearance_chapter is not None:
                        self._sql_state_manager._update_last_appearance(entity_id, patch.appearance_chapter)
                        # 补充 appearances 记录
                        # 使用 skip_if_exists=True 避免覆盖已有记录的 mentions
                        if (entity_id, patch.appearance_chapter) not in processed_appearances:
                            self._sql_state_manager._index_manager.record_appearance(
                                entity_id=entity_id,
                                chapter=patch.appearance_chapter,
                                mentions=[],
                                confidence=1.0,
                                skip_if_exists=True  # 关键：不覆盖已有记录
                            )

            # 同步别名
            for alias, entries in self._pending_alias_entries.items():
                for entry in entries:
                    entity_type = entry.get("type")
                    entity_id = entry.get("id")
                    if entity_type and entity_id:
                        self._sql_state_manager.register_alias(alias, entity_id, entity_type)

            # 同步状态变化
            for change in self._pending_state_changes:
                self._sql_state_manager.record_state_change(
                    entity_id=change.get("entity_id", ""),
                    field=change.get("field", ""),
                    old_value=change.get("old", change.get("old_value", "")),
                    new_value=change.get("new", change.get("new_value", "")),
                    reason=change.get("reason", ""),
                    chapter=change.get("chapter", 0)
                )

            # 同步关系
            for rel in self._pending_structured_relationships:
                self._sql_state_manager.upsert_relationship(
                    from_entity=rel.get("from_entity", ""),
                    to_entity=rel.get("to_entity", ""),
                    type=rel.get("type", "相识"),
                    description=rel.get("description", ""),
                    chapter=rel.get("chapter", 0)
                )

            return True

        except Exception as e:
            # SQLite 同步失败时记录警告（不中断主流程）
            logger.warning("SQLite sync failed: %s", e)
            return False

    def _snapshot_sqlite_pending(self) -> Dict[str, Any]:
        """抓取 SQLite 侧 pending 快照，用于同步失败回滚内存队列。"""
        return {
            "entity_patches": deepcopy(self._pending_entity_patches),
            "alias_entries": deepcopy(self._pending_alias_entries),
            "state_changes": deepcopy(self._pending_state_changes),
            "structured_relationships": deepcopy(self._pending_structured_relationships),
            "sqlite_data": deepcopy(self._pending_sqlite_data),
        }

    def _restore_sqlite_pending(self, snapshot: Dict[str, Any]) -> None:
        """恢复 SQLite 侧 pending 快照，避免同步失败后数据静默丢失。"""
        self._pending_entity_patches = snapshot.get("entity_patches", {})
        self._pending_alias_entries = snapshot.get("alias_entries", {})
        self._pending_state_changes = snapshot.get("state_changes", [])
        self._pending_structured_relationships = snapshot.get("structured_relationships", [])
        self._pending_sqlite_data = snapshot.get("sqlite_data", {
            "entities_appeared": [],
            "entities_new": [],
            "state_changes": [],
            "relationships_new": [],
            "chapter": None,
        })

    def _clear_pending_sqlite_data(self):
        """清空待同步的 SQLite 数据"""
        self._pending_sqlite_data = {
            "entities_appeared": [],
            "entities_new": [],
            "state_changes": [],
            "relationships_new": [],
            "chapter": None
        }

    # ==================== 进度管理 ====================

    def get_current_chapter(self) -> int:
        """获取当前章节号"""
        return self._state.get("progress", {}).get("current_chapter", 0)

    def update_progress(self, chapter: int, words: int = 0):
        """更新进度"""
        if "progress" not in self._state:
            self._state["progress"] = {}
        self._state["progress"]["current_chapter"] = chapter
        if words > 0:
            total = self._state["progress"].get("total_words", 0)
            self._state["progress"]["total_words"] = total + words

        # 记录增量：锁内合并时用 max(chapter) + words_delta 累加
        if self._pending_progress_chapter is None:
            self._pending_progress_chapter = chapter
        else:
            self._pending_progress_chapter = max(self._pending_progress_chapter, chapter)
        if words > 0:
            self._pending_progress_words_delta += int(words)

    # ==================== 章节状态管理 ====================

    def get_chapter_status(self, chapter: int) -> Optional[str]:
        """查询章节状态。"""
        statuses = self._state.get("progress", {}).get("chapter_status", {})
        return statuses.get(str(chapter))

    def set_chapter_status(self, chapter: int, status: str) -> None:
        """设置章节状态（单调递进，不可回退）。"""
        if status not in self.CHAPTER_STATUS_ORDER:
            raise ValueError(f"无效状态: {status}，有效值: {self.CHAPTER_STATUS_ORDER}")

        current = self.get_chapter_status(chapter)
        if current is not None:
            current_idx = self.CHAPTER_STATUS_ORDER.index(current)
            new_idx = self.CHAPTER_STATUS_ORDER.index(status)
            if new_idx < current_idx:
                raise ValueError(
                    f"章节 {chapter} 状态不可回退: {current} -> {status}"
                )
            if new_idx == current_idx:
                return  # 幂等

        progress = self._state.setdefault("progress", {})
        chapter_status = progress.setdefault("chapter_status", {})
        chapter_status[str(chapter)] = status
        self._save_state()

    def _save_state(self) -> None:
        """直接持久化当前内存状态到 state.json（轻量写入，不走 pending 合并）。"""
        self.config.ensure_dirs()
        atomic_write_json(self.config.state_file, self._state, backup=False)

    # ==================== 实体管理 (v5.1 SQLite-first) ====================

    def get_entity(self, entity_id: str, entity_type: str = None) -> Optional[Dict]:
        """获取实体（v5.1 引入：优先从 SQLite 读取）"""
        # v5.1 引入: 优先从 SQLite 读取
        if self._sql_state_manager:
            entity = self._sql_state_manager.get_entity(entity_id)
            if entity:
                return entity

        # 回退到内存 state (兼容未迁移场景)
        entities_v3 = self._state.get("entities_v3", {})
        if entity_type:
            return entities_v3.get(entity_type, {}).get(entity_id)

        # 遍历所有类型查找
        for type_name, entities in entities_v3.items():
            if entity_id in entities:
                return entities[entity_id]
        return None

    def get_entity_type(self, entity_id: str) -> Optional[str]:
        """获取实体所属类型"""
        # v5.1 引入: 优先从 SQLite 读取
        if self._sql_state_manager:
            entity = self._sql_state_manager._index_manager.get_entity(entity_id)
            if entity:
                return entity.get("type")

        # 回退到内存 state
        for type_name, entities in self._state.get("entities_v3", {}).items():
            if entity_id in entities:
                return type_name
        return None

    def get_all_entities(self) -> Dict[str, Dict]:
        """获取所有实体（扁平化视图）"""
        # v5.1 引入: 优先从 SQLite 读取
        if self._sql_state_manager:
            result = {}
            for entity_type in self.ENTITY_TYPES:
                entities = self._sql_state_manager._index_manager.get_entities_by_type(entity_type)
                for e in entities:
                    eid = e.get("id")
                    if eid:
                        result[eid] = {**e, "type": entity_type}
            if result:
                return result

        # 回退到内存 state
        result = {}
        for type_name, entities in self._state.get("entities_v3", {}).items():
            for eid, e in entities.items():
                result[eid] = {**e, "type": type_name}
        return result

    def get_entities_by_type(self, entity_type: str) -> Dict[str, Dict]:
        """按类型获取实体"""
        # v5.1 引入: 优先从 SQLite 读取
        if self._sql_state_manager:
            entities = self._sql_state_manager._index_manager.get_entities_by_type(entity_type)
            if entities:
                return {e.get("id"): e for e in entities if e.get("id")}

        # 回退到内存 state
        return self._state.get("entities_v3", {}).get(entity_type, {})

    def get_entities_by_tier(self, tier: str) -> Dict[str, Dict]:
        """按层级获取实体"""
        # v5.1 引入: 优先从 SQLite 读取
        if self._sql_state_manager:
            result = {}
            for entity_type in self.ENTITY_TYPES:
                entities = self._sql_state_manager._index_manager.get_entities_by_tier(tier)
                for e in entities:
                    eid = e.get("id")
                    if eid and e.get("type") == entity_type:
                        result[eid] = {**e, "type": entity_type}
            if result:
                return result

        # 回退到内存 state
        result = {}
        for type_name, entities in self._state.get("entities_v3", {}).items():
            for eid, e in entities.items():
                if e.get("tier") == tier:
                    result[eid] = {**e, "type": type_name}
        return result

    def add_entity(self, entity: EntityState) -> bool:
        """添加新实体（v5.0 entities_v3 格式，v5.4 沿用）"""
        entity_type = entity.type
        if entity_type not in self.ENTITY_TYPES:
            entity_type = "角色"

        if "entities_v3" not in self._state:
            self._state["entities_v3"] = {t: {} for t in self.ENTITY_TYPES}

        if entity_type not in self._state["entities_v3"]:
            self._state["entities_v3"][entity_type] = {}

        # 检查是否已存在
        if entity.id in self._state["entities_v3"][entity_type]:
            return False

        # 转换为 v3 格式
        v3_entity = {
            "canonical_name": entity.name,
            "tier": entity.tier,
            "desc": "",
            "current": entity.attributes,
            "aliases": list(entity.aliases),
            "first_appearance": entity.first_appearance,
            "last_appearance": entity.last_appearance,
            "history": []
        }
        self._state["entities_v3"][entity_type][entity.id] = v3_entity

        # 记录实体补丁（新建：仅填充缺失字段，避免覆盖并发写入）
        patch = self._pending_entity_patches.get((entity_type, entity.id))
        if patch is None:
            patch = _EntityPatch(entity_type=entity_type, entity_id=entity.id)
            self._pending_entity_patches[(entity_type, entity.id)] = patch
        patch.replace = True
        patch.base_entity = v3_entity

        # v5.1 引入: 注册别名到 index.db (通过 SQLStateManager)
        if self._sql_state_manager:
            self._sql_state_manager._index_manager.register_alias(entity.name, entity.id, entity_type)
            for alias in entity.aliases:
                if alias:
                    self._sql_state_manager._index_manager.register_alias(alias, entity.id, entity_type)

        return True

    def _register_alias_internal(self, entity_id: str, entity_type: str, alias: str):
        """内部方法：注册别名到 index.db（v5.1 引入）"""
        if not alias:
            return
        # v5.1 引入: 直接写入 SQLite
        if self._sql_state_manager:
            self._sql_state_manager._index_manager.register_alias(alias, entity_id, entity_type)

    def update_entity(self, entity_id: str, updates: Dict[str, Any], entity_type: str = None) -> bool:
        """更新实体属性（v5.0 引入，v5.4 沿用）"""
        # v5.1+ SQLite-first:
        # - entity_type 可能来自 SQLite（entities 表），但 state.json 不再持久化 entities_v3。
        # - 因此不能假设 self._state["entities_v3"][type][id] 一定存在（issues7 日志曾 KeyError）。
        resolved_type = entity_type or self.get_entity_type(entity_id)
        if not resolved_type:
            return False
        if resolved_type not in self.ENTITY_TYPES:
            resolved_type = "角色"

        # 仅在内存存在 v3 实体时才更新内存快照（不强行创建，避免 state.json 再膨胀）
        entities_v3 = self._state.get("entities_v3")
        entity = None
        if isinstance(entities_v3, dict):
            bucket = entities_v3.get(resolved_type)
            if isinstance(bucket, dict):
                entity = bucket.get(entity_id)

        # SQLite 启用时，即使内存实体缺失，也要记录 patch，确保 current 能增量写回 index.db
        patch = None
        if self._sql_state_manager:
            patch = self._pending_entity_patches.get((resolved_type, entity_id))
            if patch is None:
                patch = _EntityPatch(entity_type=resolved_type, entity_id=entity_id)
                self._pending_entity_patches[(resolved_type, entity_id)] = patch

        if entity is None and patch is None:
            return False

        did_any = False
        for key, value in updates.items():
            if key == "attributes" and isinstance(value, dict):
                if entity is not None:
                    if "current" not in entity:
                        entity["current"] = {}
                    entity["current"].update(value)
                if patch is not None:
                    patch.current_updates.update(value)
                did_any = True
            elif key == "current" and isinstance(value, dict):
                if entity is not None:
                    if "current" not in entity:
                        entity["current"] = {}
                    entity["current"].update(value)
                if patch is not None:
                    patch.current_updates.update(value)
                did_any = True
            else:
                if entity is not None:
                    entity[key] = value
                if patch is not None:
                    patch.top_updates[key] = value
                did_any = True

        return did_any

    def update_entity_appearance(self, entity_id: str, chapter: int, entity_type: str = None):
        """更新实体出场章节"""
        if not entity_type:
            entity_type = self.get_entity_type(entity_id)
        if not entity_type:
            return

        entities_v3 = self._state.get("entities_v3")
        if not isinstance(entities_v3, dict):
            entities_v3 = {t: {} for t in self.ENTITY_TYPES}
            self._state["entities_v3"] = entities_v3
        entities_v3.setdefault(entity_type, {})

        entity = entities_v3[entity_type].get(entity_id)
        if entity:
            if entity.get("first_appearance", 0) == 0:
                entity["first_appearance"] = chapter
            entity["last_appearance"] = chapter

            # 记录补丁：锁内应用 first=min(non-zero), last=max
            patch = self._pending_entity_patches.get((entity_type, entity_id))
            if patch is None:
                patch = _EntityPatch(entity_type=entity_type, entity_id=entity_id)
                self._pending_entity_patches[(entity_type, entity_id)] = patch
            if patch.appearance_chapter is None:
                patch.appearance_chapter = chapter
            else:
                patch.appearance_chapter = max(int(patch.appearance_chapter), int(chapter))

    # ==================== 状态变化记录 ====================

    def record_state_change(
        self,
        entity_id: str,
        field: str,
        old_value: Any,
        new_value: Any,
        reason: str,
        chapter: int
    ):
        """记录状态变化"""
        if "state_changes" not in self._state:
            self._state["state_changes"] = []

        change = StateChange(
            entity_id=entity_id,
            field=field,
            old_value=old_value,
            new_value=new_value,
            reason=reason,
            chapter=chapter
        )
        change_dict = asdict(change)
        self._state["state_changes"].append(change_dict)
        self._pending_state_changes.append(change_dict)

        # 同时更新实体属性
        self.update_entity(entity_id, {"attributes": {field: new_value}})

    def get_state_changes(self, entity_id: Optional[str] = None) -> List[Dict]:
        """获取状态变化历史"""
        changes = self._state.get("state_changes", [])
        if entity_id:
            changes = [c for c in changes if c.get("entity_id") == entity_id]
        return changes

    # ==================== 关系管理 ====================

    def add_relationship(
        self,
        from_entity: str,
        to_entity: str,
        rel_type: str,
        description: str,
        chapter: int
    ):
        """添加关系"""
        rel = Relationship(
            from_entity=from_entity,
            to_entity=to_entity,
            type=rel_type,
            description=description,
            chapter=chapter
        )

        # v5.0 引入: 实体关系存入 structured_relationships，避免与 relationships(人物关系字典) 冲突
        if "structured_relationships" not in self._state:
            self._state["structured_relationships"] = []
        rel_dict = asdict(rel)
        self._state["structured_relationships"].append(rel_dict)
        self._pending_structured_relationships.append(rel_dict)

    def get_relationships(self, entity_id: Optional[str] = None) -> List[Dict]:
        """获取关系列表"""
        rels = self._state.get("structured_relationships", [])
        if entity_id:
            rels = [
                r for r in rels
                if r.get("from_entity") == entity_id or r.get("to_entity") == entity_id
            ]
        return rels

    # ==================== 批量操作 ====================

    def _record_disambiguation(self, chapter: int, uncertain_items: Any) -> List[str]:
        """
        记录消歧反馈到 state.json，便于 Writer/Context Agent 感知风险。

        约定：
        - >= extraction_confidence_medium：写入 disambiguation_warnings（采用但警告）
        - < extraction_confidence_medium：写入 disambiguation_pending（需人工确认）
        """
        if not isinstance(uncertain_items, list) or not uncertain_items:
            return []

        warnings: List[str] = []
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        for item in uncertain_items:
            if not isinstance(item, dict):
                continue

            mention = str(item.get("mention", "") or "").strip()
            if not mention:
                continue

            raw_conf = item.get("confidence", 0.0)
            try:
                confidence = float(raw_conf)
            except (TypeError, ValueError):
                confidence = 0.0

            # 候选：支持 [{"type","id"}...] 或 ["id1","id2"] 两种形式
            candidates_raw = item.get("candidates", [])
            candidates: List[Dict[str, str]] = []
            if isinstance(candidates_raw, list):
                for c in candidates_raw:
                    if isinstance(c, dict):
                        cid = str(c.get("id", "") or "").strip()
                        ctype = str(c.get("type", "") or "").strip()
                        entry: Dict[str, str] = {}
                        if ctype:
                            entry["type"] = ctype
                        if cid:
                            entry["id"] = cid
                        if entry:
                            candidates.append(entry)
                    else:
                        cid = str(c).strip()
                        if cid:
                            candidates.append({"id": cid})

            entity_type = str(item.get("type", "") or "").strip()
            suggested_id = str(item.get("suggested", "") or "").strip()

            adopted_raw = item.get("adopted", None)
            chosen_id = ""
            if isinstance(adopted_raw, str):
                chosen_id = adopted_raw.strip()
            elif adopted_raw is True:
                chosen_id = suggested_id
            else:
                # 兼容字段名：entity_id / chosen_id
                chosen_id = str(item.get("entity_id") or item.get("chosen_id") or "").strip() or suggested_id

            context = str(item.get("context", "") or "").strip()
            note = str(item.get("warning", "") or "").strip()

            record: Dict[str, Any] = {
                "chapter": int(chapter),
                "mention": mention,
                "type": entity_type,
                "suggested_id": suggested_id,
                "chosen_id": chosen_id,
                "confidence": confidence,
                "candidates": candidates,
                "context": context,
                "note": note,
                "created_at": now,
            }

            if confidence >= float(self.config.extraction_confidence_medium):
                self._state.setdefault("disambiguation_warnings", []).append(record)
                self._pending_disambiguation_warnings.append(record)
                chosen_part = f" → {chosen_id}" if chosen_id else ""
                warnings.append(f"消歧警告: {mention}{chosen_part} (confidence: {confidence:.2f})")
            else:
                self._state.setdefault("disambiguation_pending", []).append(record)
                self._pending_disambiguation_pending.append(record)
                warnings.append(f"消歧需人工确认: {mention} (confidence: {confidence:.2f})")

        return warnings

    def process_chapter_result(self, chapter: int, result: Dict) -> List[str]:
        """
        处理 Data Agent 的章节处理结果（v5.1 引入，v5.4 沿用）

        输入格式:
        - entities_appeared: 出场实体列表
        - entities_new: 新实体列表
        - state_changes: 状态变化列表
        - relationships_new: 新关系列表

        返回警告列表
        """
        warnings = []

        # v5.1 引入: 记录章节号用于 SQLite 同步
        self._pending_sqlite_data["chapter"] = chapter

        # 处理出场实体
        for entity in result.get("entities_appeared", []):
            entity_id = entity.get("id")
            entity_type = entity.get("type")
            if entity_id:
                self.update_entity_appearance(entity_id, chapter, entity_type)
                resolved_type = entity_type or self.get_entity_type(entity_id) or "角色"
                for alias in _unique_aliases(entity.get("mentions", [])):
                    entries = self._pending_alias_entries.setdefault(alias, [])
                    alias_entry = {"type": resolved_type, "id": entity_id}
                    if alias_entry not in entries:
                        entries.append(alias_entry)
                # v5.1 引入: 缓存用于 SQLite 同步
                self._pending_sqlite_data["entities_appeared"].append(entity)

        # 处理新实体
        for entity in result.get("entities_new", []):
            entity_id = entity.get("suggested_id") or entity.get("id")
            if entity_id and entity_id != "NEW":
                new_entity = EntityState(
                    id=entity_id,
                    name=entity.get("name", ""),
                    type=entity.get("type", "角色"),
                    tier=entity.get("tier", "装饰"),
                    aliases=_unique_aliases(
                        entity.get("mentions", []), entity.get("aliases", [])
                    ),
                    first_appearance=chapter,
                    last_appearance=chapter
                )
                if not self.add_entity(new_entity):
                    warnings.append(f"实体已存在: {entity_id}")
                # v5.1 引入: 缓存用于 SQLite 同步
                self._pending_sqlite_data["entities_new"].append(entity)

        # 处理状态变化
        for change in result.get("state_changes", []):
            self.record_state_change(
                entity_id=change.get("entity_id", ""),
                field=change.get("field", ""),
                old_value=change.get("old"),
                new_value=change.get("new"),
                reason=change.get("reason", ""),
                chapter=chapter
            )
            # v5.1 引入: 缓存用于 SQLite 同步
            self._pending_sqlite_data["state_changes"].append(change)

        # 处理关系
        for rel in result.get("relationships_new", []):
            self.add_relationship(
                from_entity=rel.get("from", ""),
                to_entity=rel.get("to", ""),
                rel_type=rel.get("type", ""),
                description=rel.get("description", ""),
                chapter=chapter
            )
            # v5.1 引入: 缓存用于 SQLite 同步
            self._pending_sqlite_data["relationships_new"].append(rel)

        # 处理消歧不确定项（不影响实体写入，但必须对 Writer 可见）
        warnings.extend(self._record_disambiguation(chapter, result.get("uncertain", [])))


        # 写入 chapter_meta（钩子/模式/结束状态）
        chapter_meta = result.get("chapter_meta")
        if isinstance(chapter_meta, dict):
            meta_key = f"{int(chapter):04d}"
            self._state.setdefault("chapter_meta", {})
            self._state["chapter_meta"][meta_key] = chapter_meta
            self._pending_chapter_meta[meta_key] = chapter_meta
        # 更新进度
        self.update_progress(chapter)

        # 同步主角状态（entities_v3 → protagonist_state）
        self.sync_protagonist_from_entity()

        # 长期记忆写入（best-effort，不阻断主流程）
        try:
            from .memory.writer import MemoryWriter

            writer = MemoryWriter(self.config)
            mem_result = writer.update_from_chapter_result(chapter, result)
            logger.info("memory_write: %s", mem_result)
        except Exception as exc:
            logger.warning("memory_write_failed: %s", exc)

        return warnings

    # ==================== 导出 ====================

    def export_for_context(self) -> Dict:
        """导出用于上下文的精简版状态（v5.0 引入，v5.4 沿用）"""
        # 从 entities_v3 构建精简视图
        entities_flat = {}
        for type_name, entities in self._state.get("entities_v3", {}).items():
            for eid, e in entities.items():
                entities_flat[eid] = {
                    "name": e.get("canonical_name", eid),
                    "type": type_name,
                    "tier": e.get("tier", "装饰"),
                    "current": e.get("current", {})
                }

        return {
            "progress": self._state.get("progress", {}),
            "entities": entities_flat,
            # v5.1 引入: alias_index 已迁移到 index.db，这里返回空（兼容性）
            "alias_index": {},
            "recent_changes": [],  # v5.1 引入: 从 index.db 查询
            "disambiguation": {
                "warnings": self._state.get("disambiguation_warnings", [])[-self.config.export_disambiguation_slice:],
                "pending": self._state.get("disambiguation_pending", [])[-self.config.export_disambiguation_slice:],
            },
        }

    # ==================== 主角同步 ====================

    def get_protagonist_entity_id(self) -> Optional[str]:
        """获取主角实体 ID（通过 is_protagonist 标记或 SQLite 查询）"""
        # 方式1: 通过 SQLStateManager 查询 (v5.1)
        if self._sql_state_manager:
            protagonist = self._sql_state_manager.get_protagonist()
            if protagonist:
                return protagonist.get("id")

        # 方式2: 通过 protagonist_state.name 查找别名
        protag_name = self._state.get("protagonist_state", {}).get("name")
        if protag_name and self._sql_state_manager:
            entities = self._sql_state_manager._index_manager.get_entities_by_alias(protag_name)
            for entry in entities:
                if entry.get("type") == "角色":
                    return entry.get("id")

        return None

    def sync_protagonist_from_entity(self, entity_id: str = None):
        """
        将主角实体的状态同步到 protagonist_state (v5.1: 从 SQLite 读取)

        用于确保 consistency-checker 等依赖 protagonist_state 的组件获取最新数据
        """
        if entity_id is None:
            entity_id = self.get_protagonist_entity_id()
        if entity_id is None:
            return

        entity = self.get_entity(entity_id, "角色")
        if not entity:
            return

        current = entity.get("current")
        if not isinstance(current, dict):
            current = entity.get("current_json", {})
        if isinstance(current, str):
            try:
                current = json.loads(current) if current else {}
            except (json.JSONDecodeError, TypeError):
                current = {}
        if not isinstance(current, dict):
            current = {}
        protag = self._state.setdefault("protagonist_state", {})

        # 同步境界
        if "realm" in current:
            power = protag.setdefault("power", {})
            power["realm"] = current["realm"]
            if "layer" in current:
                power["layer"] = current["layer"]

        # 同步位置
        if "location" in current:
            loc = protag.setdefault("location", {})
            loc["current"] = current["location"]
            if "last_chapter" in current:
                loc["last_chapter"] = current["last_chapter"]

    def sync_protagonist_to_entity(self, entity_id: str = None):
        """
        将 protagonist_state 同步到 entities_v3 中的主角实体

        用于初始化或手动编辑 protagonist_state 后保持一致性
        """
        if entity_id is None:
            entity_id = self.get_protagonist_entity_id()
        if entity_id is None:
            return

        protag = self._state.get("protagonist_state", {})
        if not protag:
            return

        updates = {}

        # 同步境界
        power = protag.get("power", {})
        if power.get("realm"):
            updates["realm"] = power["realm"]
        if power.get("layer"):
            updates["layer"] = power["layer"]

        # 同步位置
        loc = protag.get("location", {})
        if loc.get("current"):
            updates["location"] = loc["current"]

        if updates:
            self.update_entity(entity_id, updates, "角色")


# ==================== CLI 接口 ====================

def main():
    import argparse
    import sys
    from pydantic import ValidationError
    from .cli_output import print_success, print_error
    from .cli_args import normalize_global_project_root, load_json_arg

    from .index_manager import IndexManager, StateChangeMeta, RelationshipMeta, RelationshipEventMeta

    parser = argparse.ArgumentParser(description="State Manager CLI (v5.4)")
    parser.add_argument("--project-root", type=str, help="项目根目录")

    subparsers = parser.add_subparsers(dest="command")

    # 读取进度
    subparsers.add_parser("get-progress")

    # 获取实体
    get_entity_parser = subparsers.add_parser("get-entity")
    get_entity_parser.add_argument("--id", required=True)

    # 列出实体
    list_parser = subparsers.add_parser("list-entities")
    list_parser.add_argument("--type", help="按类型过滤")
    list_parser.add_argument("--tier", help="按层级过滤")

    # 处理章节结果
    process_parser = subparsers.add_parser("process-chapter")
    process_parser.add_argument("--chapter", type=int, required=True, help="章节号")
    process_parser.add_argument("--data", required=True, help="JSON 格式的处理结果")

    # 查询章节状态
    status_get_parser = subparsers.add_parser("get-chapter-status")
    status_get_parser.add_argument("--chapter", type=int, required=True)

    # 设置章节状态
    status_set_parser = subparsers.add_parser("set-chapter-status")
    status_set_parser.add_argument("--chapter", type=int, required=True)
    status_set_parser.add_argument("--status", required=True,
        choices=["chapter_drafted", "chapter_reviewed", "chapter_committed"])

    argv = normalize_global_project_root(sys.argv[1:])
    args = parser.parse_args(argv)
    command_started_at = time.perf_counter()

    # 初始化
    config = None
    if args.project_root:
        # 允许传入“工作区根目录”，统一解析到真正的 book project_root（必须包含 .ainovel/state.json）
        from project_locator import resolve_project_root
        from .config import DataModulesConfig

        try:
            resolved_root = resolve_project_root(args.project_root)
        except FileNotFoundError as exc:
            print_error(
                "INVALID_PROJECT_ROOT",
                str(exc),
                suggestion="请传入包含 .ainovel/state.json 的书项目根目录，或先通过 ainovel.py 解析 project_root。",
            )
            raise SystemExit(1) from exc
        config = DataModulesConfig.from_project_root(resolved_root)

    manager = StateManager(config)
    logger = IndexManager(config)
    tool_name = f"state_manager:{args.command or 'unknown'}"

    def _append_timing(success: bool, *, error_code: str | None = None, error_message: str | None = None, chapter: int | None = None):
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

    if args.command == "get-progress":
        emit_success(manager._state.get("progress", {}), message="progress")

    elif args.command == "get-entity":
        entity = manager.get_entity(args.id)
        if entity:
            emit_success(entity, message="entity")
        else:
            emit_error("NOT_FOUND", f"未找到实体: {args.id}")

    elif args.command == "list-entities":
        if args.type:
            entities = manager.get_entities_by_type(args.type)
        elif args.tier:
            entities = manager.get_entities_by_tier(args.tier)
        else:
            entities = manager.get_all_entities()

        payload = [{"id": eid, **e} for eid, e in entities.items()]
        emit_success(payload, message="entities")

    elif args.command == "process-chapter":
        data = load_json_arg(args.data)
        validated = None
        last_exc = None
        for _ in range(3):
            try:
                validated = validate_data_agent_output(data)
                break
            except ValidationError as exc:
                last_exc = exc
                data = normalize_data_agent_output(data)
        if validated is None:
            err = format_validation_error(last_exc) if last_exc else {
                "code": "SCHEMA_VALIDATION_FAILED",
                "message": "数据结构校验失败",
                "details": {"errors": []},
                "suggestion": "请检查 data-agent 输出字段是否完整且类型正确",
            }
            emit_error(err["code"], err["message"], suggestion=err.get("suggestion"))
            return

        warnings = manager.process_chapter_result(args.chapter, validated.model_dump(by_alias=True))
        manager.save_state()
        emit_success({"chapter": args.chapter, "warnings": warnings}, message="chapter_processed", chapter=args.chapter)

    elif args.command == "get-chapter-status":
        manager._load_state()
        status = manager.get_chapter_status(args.chapter)
        emit_success({"chapter": args.chapter, "status": status},
                     message="chapter_status")

    elif args.command == "set-chapter-status":
        manager._load_state()
        manager.set_chapter_status(args.chapter, args.status)
        emit_success({"chapter": args.chapter, "status": args.status},
                     message="chapter_status_set")

    else:
        emit_error("UNKNOWN_COMMAND", "未指定有效命令", suggestion="请查看 --help")


if __name__ == "__main__":
    if sys.platform == "win32":
        enable_windows_utf8_stdio()
    main()
