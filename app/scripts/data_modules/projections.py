#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Projections — 事件投影写入器（合并自 8 个模块）。

包含：
  EventLogStore            — 事件日志存储（JSON + SQLite 镜像）
  EventProjectionRouter    — 事件→写入器路由
  AmendProposalTrigger     — 事件触发合同修订提案
  IndexProjectionWriter    — 索引（章节/场景/出场/状态变更）投影
  StateProjectionWriter    — state.json 投影
  SummaryProjectionWriter  — 章节摘要投影
  MemoryProjectionWriter   — memory_scratchpad 投影
  VectorProjectionWriter   — 向量库投影
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import sqlite3
import sys
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterator, List, Set

from pydantic import BaseModel, Field

from .chapter_commit_schema import normalize_accepted_events
from .config import DataModulesConfig
from .index_manager import ChapterMeta, IndexManager, SceneMeta, StateChangeMeta
from .memory.writer import MemoryWriter
from .story_contracts import StoryContractPaths, read_json_if_exists, write_json

try:
    from chapter_paths import find_chapter_file
except ImportError:  # pragma: no cover
    from scripts.chapter_paths import find_chapter_file

logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════════════════════
# Section 0: AmendProposal — 合同修订提案 schema（来自 amend_proposal_schema.py）
# ══════════════════════════════════════════════════════════════════════════════

class AmendProposal(BaseModel):
    proposal_id: str
    chapter: int = Field(ge=1)
    target_level: str
    field: str
    base_value: str = ""
    proposed_value: str = ""
    reason_tag: str


# ══════════════════════════════════════════════════════════════════════════════
# Section 1: EventLogStore — 事件日志存储
# ══════════════════════════════════════════════════════════════════════════════

class EventLogStore:
    def __init__(self, project_root: Path):
        self.project_root = Path(project_root).expanduser().resolve()
        self.paths = StoryContractPaths.from_project_root(self.project_root)

    @contextmanager
    def _connect(self, *, row_factory: bool = False) -> Iterator[sqlite3.Connection]:
        """统一 SQLite 连接管理，确保连接始终关闭。"""
        db_path = self.project_root / ".ainovel" / "index.db"
        db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(db_path))
        if row_factory:
            conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()

    def write_events(self, chapter: int, events: Any) -> Path:
        normalized = self.normalize_events(chapter, events)
        path = self.paths.event_json(chapter)
        write_json(path, normalized)
        self._write_sqlite_mirror(normalized)
        return path

    def read_events(self, chapter: int) -> List[Dict[str, Any]]:
        return list(read_json_if_exists(self.paths.event_json(chapter)) or [])

    def list_recent(self, chapter: int | None = None, limit: int = 200) -> List[Dict[str, Any]]:
        db_path = self.project_root / ".ainovel" / "index.db"
        if not db_path.is_file():
            return []
        with self._connect(row_factory=True) as conn:
            try:
                if chapter is not None:
                    rows = conn.execute(
                        """
                        SELECT event_id, chapter, event_type, subject, payload_json
                        FROM story_events
                        WHERE chapter = ?
                        ORDER BY id DESC
                        LIMIT ?
                        """,
                        (chapter, limit),
                    ).fetchall()
                else:
                    rows = conn.execute(
                        """
                        SELECT event_id, chapter, event_type, subject, payload_json
                        FROM story_events
                        ORDER BY chapter DESC, id DESC
                        LIMIT ?
                        """,
                        (limit,),
                    ).fetchall()
            except sqlite3.OperationalError:
                return []

        result: List[Dict[str, Any]] = []
        for row in rows:
            payload = {}
            try:
                payload = json.loads(row["payload_json"] or "{}")
            except json.JSONDecodeError:
                payload = {}
            result.append(
                {
                    "event_id": row["event_id"],
                    "chapter": row["chapter"],
                    "event_type": row["event_type"],
                    "subject": row["subject"],
                    "payload": payload,
                }
            )
        return result

    def health(self) -> Dict[str, Any]:
        db_path = self.project_root / ".ainovel" / "index.db"
        file_count = len(list(self.paths.events_dir.glob("chapter_*.events.json")))
        sqlite_rows = 0
        if db_path.is_file():
            with self._connect() as conn:
                try:
                    sqlite_rows = int(
                        conn.execute("SELECT COUNT(*) FROM story_events").fetchone()[0]
                    )
                except sqlite3.OperationalError:
                    sqlite_rows = 0
        return {"ok": True, "sqlite_rows": sqlite_rows, "event_files": file_count}

    def normalize_events(self, chapter: int, events: Any) -> List[Dict[str, Any]]:
        return normalize_accepted_events(chapter, events)

    def _write_sqlite_mirror(self, events: List[Dict[str, Any]]) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS story_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_id TEXT NOT NULL UNIQUE,
                    chapter INTEGER NOT NULL,
                    event_type TEXT NOT NULL,
                    subject TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_story_events_chapter ON story_events(chapter)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_story_events_type ON story_events(event_type)"
            )
            conn.executemany(
                """
                INSERT OR IGNORE INTO story_events(event_id, chapter, event_type, subject, payload_json)
                VALUES (?, ?, ?, ?, ?)
                """,
                [
                    (
                        event["event_id"],
                        int(event["chapter"]),
                        event["event_type"],
                        event["subject"],
                        json.dumps(event.get("payload") or {}, ensure_ascii=False),
                    )
                    for event in events
                ],
            )
            conn.commit()


# ══════════════════════════════════════════════════════════════════════════════
# Section 2: EventProjectionRouter — 事件→写入器路由
# ══════════════════════════════════════════════════════════════════════════════

class EventProjectionRouter:
    TABLE = {
        "character_state_changed": ["state", "memory", "vector"],
        "power_breakthrough": ["state", "memory", "vector"],
        "relationship_changed": ["index", "vector"],
        "world_rule_revealed": ["memory", "vector"],
        "world_rule_broken": ["memory", "vector"],
        "open_loop_created": ["memory"],
        "open_loop_closed": ["memory"],
        "promise_created": ["memory"],
        "promise_paid_off": ["memory"],
        "artifact_obtained": ["index", "vector"],
    }

    def route(self, event: Dict) -> List[str]:
        return list(self.TABLE.get(str(event.get("event_type") or "").strip(), []))

    def required_writers(self, commit_payload: Dict) -> List[str]:
        writers: Set[str] = set()
        if str((commit_payload.get("meta") or {}).get("status") or "") == "accepted":
            writers.add("state")
            writers.add("index")
        if commit_payload.get("entity_deltas"):
            writers.add("index")
        if str(commit_payload.get("summary_text") or "").strip():
            writers.add("summary")
        for event in commit_payload.get("accepted_events") or []:
            if not isinstance(event, dict):
                continue
            writers.update(self.route(event))
        return sorted(writers)


# ══════════════════════════════════════════════════════════════════════════════
# Section 3: AmendProposalTrigger — 事件触发合同修订提案
# ══════════════════════════════════════════════════════════════════════════════

def normalize_override_record(
    *,
    record_type: str,
    field: str,
    base_value: str,
    override_value: str,
    source_level: str,
) -> Dict[str, str]:
    return {
        "record_type": str(record_type or "").strip(),
        "field": str(field or "").strip(),
        "base_value": str(base_value or "").strip(),
        "override_value": str(override_value or "").strip(),
        "source_level": str(source_level or "").strip(),
    }


def ensure_override_ledger_columns(conn: sqlite3.Connection) -> None:
    existing = {
        row[1] for row in conn.execute("PRAGMA table_info(override_contracts)").fetchall()
    }
    wanted = {
        "record_type": "TEXT DEFAULT 'soft_deviation'",
        "field": "TEXT DEFAULT ''",
        "base_value": "TEXT DEFAULT ''",
        "override_value": "TEXT DEFAULT ''",
        "source_level": "TEXT DEFAULT ''",
        "reason_tag": "TEXT DEFAULT ''",
    }
    for name, ddl in wanted.items():
        if name not in existing:
            # SECURITY: name 和 ddl 均来自上方硬编码字典，非用户输入，无 SQL 注入风险
            conn.execute(f"ALTER TABLE override_contracts ADD COLUMN {name} {ddl}")
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_override_contracts_record_type ON override_contracts(record_type)"
    )


class AmendProposalTrigger:
    RULES = {
        "world_rule_broken": {"target_level": "master", "reason_tag": "world_rule_broken"},
        "relationship_changed": None,
        "power_breakthrough": None,
        "artifact_obtained": None,
        "character_state_changed": None,
        "world_rule_revealed": None,
        "open_loop_created": None,
        "open_loop_closed": None,
        "promise_created": None,
        "promise_paid_off": None,
    }

    def check(self, chapter: int, events: List[dict]) -> List[Dict[str, str | int]]:
        proposals: List[Dict[str, str | int]] = []
        for event in events or []:
            if not isinstance(event, dict):
                continue
            rule = self.RULES.get(str(event.get("event_type") or "").strip())
            if not rule:
                continue
            payload = dict(event.get("payload") or {})
            proposal = AmendProposal(
                proposal_id=f"amend-{chapter}-{event.get('event_id')}",
                chapter=chapter,
                target_level=rule["target_level"],
                field=str(payload.get("field") or "").strip(),
                base_value=str(payload.get("base_value") or "").strip(),
                proposed_value=str(payload.get("proposed_value") or "").strip(),
                reason_tag=rule["reason_tag"],
            )
            proposals.append(proposal.model_dump())
        return proposals


def persist_amend_proposals(
    conn: sqlite3.Connection, chapter: int, proposals: List[dict]
) -> int:
    inserted = 0
    for proposal in proposals or []:
        row = normalize_override_record(
            record_type="amend_proposal",
            field=str(proposal.get("field") or ""),
            base_value=str(proposal.get("base_value") or ""),
            override_value=str(proposal.get("proposed_value") or ""),
            source_level=str(proposal.get("target_level") or ""),
        )
        cursor = conn.execute(
            """
            INSERT OR IGNORE INTO override_contracts (
                chapter,
                constraint_type,
                constraint_id,
                rationale_type,
                rationale_text,
                payback_plan,
                due_chapter,
                status,
                record_type,
                field,
                base_value,
                override_value,
                source_level,
                reason_tag
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                chapter,
                "AMEND_PROPOSAL",
                str(proposal.get("proposal_id") or ""),
                "story_amend_proposal",
                f"事件触发合同修订提案: {proposal.get('proposal_id')}",
                "",
                chapter,
                "pending",
                row["record_type"],
                row["field"],
                row["base_value"],
                row["override_value"],
                row["source_level"],
                str(proposal.get("reason_tag") or ""),
            ),
        )
        inserted += max(int(cursor.rowcount), 0)
    return inserted


# ══════════════════════════════════════════════════════════════════════════════
# Section 4: IndexProjectionWriter — 索引投影
# ══════════════════════════════════════════════════════════════════════════════

class IndexProjectionWriter:
    def __init__(self, project_root: Path):
        self.project_root = Path(project_root)

    def apply(self, commit_payload: dict) -> dict:
        if commit_payload["meta"]["status"] != "accepted":
            return {"applied": False, "writer": "index", "reason": "commit_rejected"}

        manager = IndexManager(DataModulesConfig.from_project_root(self.project_root))
        applied_count = 0
        chapter_applied = self._upsert_chapter(manager, commit_payload)
        if chapter_applied:
            applied_count += 1

        scenes_count = self._apply_scenes(manager, commit_payload)
        applied_count += scenes_count

        appearances_count = self._apply_appearances(manager, commit_payload)
        applied_count += appearances_count

        state_changes_count = self._apply_state_changes(manager, commit_payload)
        applied_count += state_changes_count

        entity_delta_count = 0
        for delta in self._collect_entity_deltas(commit_payload):
            result = manager.apply_entity_delta(delta)
            if result:
                entity_delta_count += 1
                applied_count += 1
        return {
            "applied": applied_count > 0,
            "writer": "index",
            "applied_count": applied_count,
            "chapters": 1 if chapter_applied else 0,
            "scenes": scenes_count,
            "appearances": appearances_count,
            "state_changes": state_changes_count,
            "entity_deltas": entity_delta_count,
        }

    def _upsert_chapter(self, manager: IndexManager, commit_payload: dict) -> bool:
        chapter = int(commit_payload.get("meta", {}).get("chapter") or 0)
        if chapter <= 0:
            return False

        meta = commit_payload.get("chapter_meta") or {}
        if not isinstance(meta, dict):
            meta = {}

        title = str(
            meta.get("title")
            or commit_payload.get("chapter_title")
            or self._title_from_chapter_file(chapter)
            or ""
        ).strip()
        location = str(meta.get("location") or commit_payload.get("location") or "").strip()
        summary = str(commit_payload.get("summary_text") or meta.get("summary") or "").strip()
        word_count = self._safe_int(meta.get("word_count") or commit_payload.get("word_count"))
        if word_count <= 0:
            word_count = self._chapter_word_count(chapter)

        characters = meta.get("characters") or self._collect_character_ids(commit_payload)
        if not isinstance(characters, list):
            characters = []

        manager.add_chapter(
            ChapterMeta(
                chapter=chapter,
                title=title,
                location=location,
                word_count=word_count,
                characters=[str(c) for c in characters if str(c).strip()],
                summary=summary,
            )
        )
        return True

    def _apply_scenes(self, manager: IndexManager, commit_payload: dict) -> int:
        chapter = int(commit_payload.get("meta", {}).get("chapter") or 0)
        scenes = commit_payload.get("scenes") or []
        if chapter <= 0 or not isinstance(scenes, list) or not scenes:
            return 0

        scene_metas: list[SceneMeta] = []
        for idx, scene in enumerate(scenes, start=1):
            if not isinstance(scene, dict):
                continue
            scene_index = self._safe_int(scene.get("scene_index") or scene.get("index") or idx)
            characters = scene.get("characters") or scene.get("character_ids") or []
            if not isinstance(characters, list):
                characters = []
            scene_metas.append(
                SceneMeta(
                    chapter=chapter,
                    scene_index=scene_index,
                    start_line=self._safe_int(scene.get("start_line")),
                    end_line=self._safe_int(scene.get("end_line")),
                    location=str(scene.get("location") or "").strip(),
                    summary=str(scene.get("summary") or scene.get("content") or "").strip(),
                    characters=[str(c) for c in characters if str(c).strip()],
                )
            )
        if not scene_metas:
            return 0
        manager.add_scenes(chapter, scene_metas)
        return len(scene_metas)

    def _apply_appearances(self, manager: IndexManager, commit_payload: dict) -> int:
        chapter = int(commit_payload.get("meta", {}).get("chapter") or 0)
        entities = commit_payload.get("entities_appeared") or []
        if chapter <= 0 or not isinstance(entities, list):
            return 0

        applied = 0
        for entity in entities:
            if not isinstance(entity, dict):
                continue
            entity_id = str(entity.get("id") or entity.get("entity_id") or "").strip()
            if not entity_id or entity_id == "NEW":
                continue
            mentions = entity.get("mentions") or []
            if isinstance(mentions, str):
                mentions = [mentions]
            if not isinstance(mentions, list):
                mentions = []
            manager.record_appearance(
                entity_id=entity_id,
                chapter=chapter,
                mentions=[str(m) for m in mentions if str(m).strip()],
                confidence=self._safe_float(entity.get("confidence"), 1.0),
            )
            applied += 1
        return applied

    def _apply_state_changes(self, manager: IndexManager, commit_payload: dict) -> int:
        applied = 0
        for change in self._collect_state_changes(commit_payload):
            entity_id = str(change.get("entity_id") or "").strip()
            field = str(change.get("field") or "").strip()
            chapter = self._safe_int(change.get("chapter") or commit_payload.get("meta", {}).get("chapter"))
            if not entity_id or not field or chapter <= 0:
                continue
            old_value = self._stringify(change.get("old"))
            new_value = self._stringify(change.get("new"))
            reason = str(change.get("reason") or "").strip()
            if self._state_change_exists(manager, entity_id, field, old_value, new_value, reason, chapter):
                continue
            manager.record_state_change(
                StateChangeMeta(
                    entity_id=entity_id,
                    field=field,
                    old_value=old_value,
                    new_value=new_value,
                    reason=reason,
                    chapter=chapter,
                )
            )
            applied += 1
        return applied

    def _collect_state_changes(self, commit_payload: dict) -> list[dict]:
        deltas = [
            self._normalize_state_delta(delta)
            for delta in (commit_payload.get("state_deltas") or [])
            if isinstance(delta, dict)
        ]
        seen = {
            (
                str(delta.get("entity_id") or "").strip(),
                str(delta.get("field") or "").strip(),
                self._safe_int(delta.get("chapter") or commit_payload.get("meta", {}).get("chapter")),
            )
            for delta in deltas
        }

        for event in commit_payload.get("accepted_events") or []:
            if not isinstance(event, dict):
                continue
            event_type = str(event.get("event_type") or "").strip()
            payload = dict(event.get("payload") or {})
            if event_type == "power_breakthrough":
                field = str(payload.get("field") or payload.get("field_path") or "realm").strip()
            elif event_type == "character_state_changed":
                field = str(payload.get("field") or payload.get("field_path") or "").strip()
            else:
                continue
            entity_id = str(payload.get("entity_id") or event.get("subject") or "").strip()
            chapter = self._safe_int(event.get("chapter") or commit_payload.get("meta", {}).get("chapter"))
            key = (entity_id, field, chapter)
            if not entity_id or not field or key in seen:
                continue
            seen.add(key)
            deltas.append(
                {
                    "entity_id": entity_id,
                    "field": field,
                    "old": (
                        payload.get("old")
                        if "old" in payload
                        else payload.get("from")
                        if "from" in payload
                        else payload.get("old_value")
                        if "old_value" in payload
                        else payload.get("previous_state")
                    ),
                    "new": (
                        payload.get("new")
                        if "new" in payload
                        else payload.get("to")
                        if "to" in payload
                        else payload.get("new_value")
                        if "new_value" in payload
                        else payload.get("new_state")
                    ),
                    "reason": event_type,
                    "chapter": chapter,
                }
            )
        return deltas

    def _normalize_state_delta(self, delta: dict) -> dict:
        result = dict(delta)
        if "field" not in result and "field_path" in result:
            result["field"] = result["field_path"]
        if "new" not in result and "new_value" in result:
            result["new"] = result["new_value"]
        if "old" not in result and "old_value" in result:
            result["old"] = result["old_value"]
        return result

    def _state_change_exists(
        self,
        manager: IndexManager,
        entity_id: str,
        field: str,
        old_value: str,
        new_value: str,
        reason: str,
        chapter: int,
    ) -> bool:
        with manager._get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT 1 FROM state_changes
                WHERE entity_id = ?
                  AND field = ?
                  AND chapter = ?
                  AND COALESCE(old_value, '') = ?
                  AND COALESCE(new_value, '') = ?
                  AND COALESCE(reason, '') = ?
                LIMIT 1
                """,
                (entity_id, field, chapter, old_value, new_value, reason),
            )
            return cursor.fetchone() is not None

    def _collect_character_ids(self, commit_payload: dict) -> list[str]:
        ids: list[str] = []
        for entity in commit_payload.get("entities_appeared") or []:
            if not isinstance(entity, dict):
                continue
            entity_id = str(entity.get("id") or entity.get("entity_id") or "").strip()
            if entity_id and entity_id != "NEW":
                ids.append(entity_id)
        for delta in commit_payload.get("entity_deltas") or []:
            if not isinstance(delta, dict):
                continue
            entity_id = str(delta.get("entity_id") or delta.get("id") or "").strip()
            entity_type = str(delta.get("type") or delta.get("entity_type") or "").strip()
            if entity_id and (not entity_type or entity_type == "角色"):
                ids.append(entity_id)
        return list(dict.fromkeys(ids))

    def _title_from_chapter_file(self, chapter: int) -> str:
        path = find_chapter_file(self.project_root, chapter)
        if path is None:
            return ""
        stem = path.stem
        match = re.match(r"第0*\d+章[-_ ]+(.+)$", stem)
        return match.group(1).strip() if match else ""

    def _chapter_word_count(self, chapter: int) -> int:
        path = find_chapter_file(self.project_root, chapter)
        if path is None:
            return 0
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            return 0
        text = re.sub(r"```[\s\S]*?```", "", text)
        text = re.sub(r"^#+ .*$", "", text, flags=re.MULTILINE)
        text = re.sub(r"---", "", text)
        return len(text.strip())

    def _stringify(self, value: Any) -> str:
        if value is None:
            return ""
        if isinstance(value, (dict, list)):
            return json.dumps(value, ensure_ascii=False, sort_keys=True)
        return str(value)

    def _safe_int(self, value: object) -> int:
        try:
            return int(value or 0)
        except (TypeError, ValueError):
            return 0

    def _safe_float(self, value: object, default: float) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return default

    def _collect_entity_deltas(self, commit_payload: dict) -> list[dict]:
        deltas = [dict(delta) for delta in (commit_payload.get("entity_deltas") or []) if isinstance(delta, dict)]
        for event in commit_payload.get("accepted_events") or []:
            if not isinstance(event, dict):
                continue
            event_type = str(event.get("event_type") or "").strip()
            payload = dict(event.get("payload") or {})
            chapter = int(event.get("chapter") or commit_payload.get("meta", {}).get("chapter") or 0)
            if event_type == "relationship_changed":
                from_entity = str(payload.get("from_entity") or event.get("subject") or "").strip()
                to_entity = str(payload.get("to_entity") or payload.get("to") or "").strip()
                rel_type = str(
                    payload.get("relationship_type")
                    or payload.get("relation_type")
                    or payload.get("type")
                    or ""
                ).strip()
                if from_entity and to_entity and rel_type:
                    deltas.append(
                        {
                            "from_entity": from_entity,
                            "to_entity": to_entity,
                            "relationship_type": rel_type,
                            "description": str(payload.get("description") or "").strip(),
                            "chapter": chapter,
                        }
                    )
            elif event_type == "artifact_obtained":
                entity_id = str(
                    payload.get("artifact_id")
                    or payload.get("entity_id")
                    or payload.get("id")
                    or event.get("subject")
                    or ""
                ).strip()
                if not entity_id:
                    continue
                current = {}
                owner = str(payload.get("owner") or payload.get("holder") or "").strip()
                location = str(payload.get("location") or "").strip()
                if owner:
                    current["holder"] = owner
                if location:
                    current["location"] = location
                deltas.append(
                    {
                        "entity_id": entity_id,
                        "canonical_name": str(payload.get("name") or event.get("subject") or entity_id).strip(),
                        "type": str(payload.get("type") or "物品").strip() or "物品",
                        "current": current,
                        "desc": str(payload.get("description") or "").strip(),
                        "chapter": chapter,
                    }
                )
        return deltas


# ══════════════════════════════════════════════════════════════════════════════
# Section 5: StateProjectionWriter — state.json 投影
# ══════════════════════════════════════════════════════════════════════════════

# state.json 中按章节范围可清理的字段
PURGEABLE_TOP_KEYS = (
    "strand_tracker",  # 整体覆盖（含 last_X_chapter + history）
)
PURGEABLE_PROGRESS_KEYS = (
    "chapter_status",  # 移除 chapter N 自身的 status
)


class StateProjectionWriter:
    """写入 .ainovel/state.json。

    ⚠️ 架构债务（2026-06-30 记录）：
      state.json.entity_state 与 memory_scratchpad.character_state 是**同一事实的两份副本**，
      分别由 StateProjectionWriter 和 MemoryProjectionWriter 写入，事实来源都是 commit 的
      state_deltas + character_state_changed 事件。这违反了「单一 source of truth」原则。

      完整修复需要：让 state.json.entity_state 从 memory_scratchpad 派生（读时计算），
      移除 StateProjectionWriter 写 entity_state 的代码。改动面较大（下游 state_manager
      消费者多），不在本轮 plan 范围。

      本轮最小修复：purge_chapter(N) 撤销 chapter N 对 state.json 的所有写入，让重写时
      state 不会泄漏上一版的进度统计。
    """
    def __init__(self, project_root: Path):
        self.project_root = Path(project_root)

    def apply(self, commit_payload: dict) -> dict:
        chapter = int(commit_payload.get("meta", {}).get("chapter") or 0)
        status = commit_payload["meta"]["status"]

        if status == "rejected":
            if chapter > 0:
                state_path = self.project_root / ".ainovel" / "state.json"
                state = read_json_if_exists(state_path) or {}
                progress = state.setdefault("progress", {})
                chapter_status = progress.setdefault("chapter_status", {})
                chapter_status[str(chapter)] = "chapter_rejected"
                write_json(state_path, state)
            return {"applied": True, "writer": "state", "reason": "commit_rejected_status_updated"}

        if status != "accepted":
            return {"applied": False, "writer": "state", "reason": f"unknown_status:{status}"}

        state_path = self.project_root / ".ainovel" / "state.json"
        state = read_json_if_exists(state_path) or {}
        entity_state = state.setdefault("entity_state", {})
        progress = state.setdefault("progress", {})
        chapter_status = progress.setdefault("chapter_status", {})

        protagonist_ids = self._collect_protagonist_ids(commit_payload, state)

        applied_count = 0
        for delta in self._collect_state_deltas(commit_payload):
            entity_id = str(delta.get("entity_id") or "").strip()
            field = str(delta.get("field") or "").strip()
            if not entity_id or not field:
                continue
            new_value = delta.get("new")
            entity_bucket = entity_state.setdefault(entity_id, {})
            self._set_path(entity_bucket, field, new_value)
            if entity_id in protagonist_ids:
                self._set_path(state.setdefault("protagonist_state", {}), field, new_value)
            applied_count += 1

        if chapter > 0:
            old_current = self._safe_int(progress.get("current_chapter"))
            old_total = self._safe_int(progress.get("total_words"))
            old_status = chapter_status.get(str(chapter))

            chapter_status[str(chapter)] = "chapter_committed"
            progress["current_chapter"] = max(old_current, chapter)

            projected_total = self._project_total_words(chapter_status)
            if projected_total > 0:
                progress["total_words"] = projected_total
            else:
                progress["total_words"] = old_total

            if (
                old_status != "chapter_committed"
                or progress.get("current_chapter") != old_current
                or progress.get("total_words") != old_total
            ):
                progress["last_updated"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        strand_applied = self._apply_strand_tracker(state, chapter, commit_payload)

        write_json(state_path, state)
        return {
            "applied": applied_count > 0 or chapter > 0,
            "writer": "state",
            "applied_count": applied_count,
            "strand_tracker": strand_applied,
        }

    @staticmethod
    def purge_chapter(project_root: Path, chapter: int) -> dict:
        """重置 chapter N 在 state.json 中的所有贡献。

        保留 entity_state / protagonist_state（属于「上一版剧情事实」与「全书进度」的混合，
        完整去重需要 state.json 改造为 derived view）。只清理：
        - progress.chapter_status[N]：撤销「chapter_committed」标记
        - strand_tracker：移除 history 中 chapter=N 的条目，重新计算 last_X_chapter

        返回变更字段名列表，供 chapter_reset_service 汇总报告。
        """
        if chapter <= 0:
            return {"purged": []}
        state_path = project_root / ".ainovel" / "state.json"
        state = read_json_if_exists(state_path) or {}
        purged: list[str] = []

        # 1. 撤销 chapter_status[N]
        progress = state.get("progress")
        if isinstance(progress, dict):
            chapter_status = progress.get("chapter_status")
            if isinstance(chapter_status, dict) and str(chapter) in chapter_status:
                del chapter_status[str(chapter)]
                purged.append("progress.chapter_status")

        # 2. 重算 strand_tracker
        tracker = state.get("strand_tracker")
        if isinstance(tracker, dict):
            history = tracker.get("history")
            if isinstance(history, list):
                cleaned = [row for row in history if isinstance(row, dict) and row.get("chapter") != chapter]
                cleaned.sort(key=lambda row: row.get("chapter", 0))
                if len(cleaned) > 50:
                    cleaned = cleaned[-50:]
                tracker["history"] = cleaned
                for name in ("quest", "fire", "constellation"):
                    tracker[f"last_{name}_chapter"] = max(
                        (row.get("chapter", 0) for row in cleaned if row.get("dominant") == name),
                        default=0,
                    )
                latest = cleaned[-1] if cleaned else None
                tracker["current_dominant"] = latest.get("dominant") if latest else None
                # 重算 chapters_since_switch
                if latest:
                    streak = 0
                    for row in reversed(cleaned):
                        if row.get("dominant") == latest.get("dominant"):
                            streak += 1
                        else:
                            break
                    tracker["chapters_since_switch"] = streak
                else:
                    tracker["chapters_since_switch"] = 0
                purged.append("strand_tracker")

        if purged:
            write_json(state_path, state)
        return {"purged": purged}

    def _collect_state_deltas(self, commit_payload: dict) -> list[dict]:
        deltas = [
            self._normalize_state_delta(delta)
            for delta in (commit_payload.get("state_deltas") or [])
            if isinstance(delta, dict)
        ]
        seen = {
            (str(delta.get("entity_id") or "").strip(), str(delta.get("field") or "").strip())
            for delta in deltas
        }

        for event in commit_payload.get("accepted_events") or []:
            if not isinstance(event, dict):
                continue
            event_type = str(event.get("event_type") or "").strip()
            payload = dict(event.get("payload") or {})
            entity_id = str(payload.get("entity_id") or event.get("subject") or "").strip()
            if not entity_id:
                continue

            field = ""
            if event_type == "power_breakthrough":
                field = (
                    str(payload.get("field") or payload.get("field_path") or "realm").strip()
                )
            elif event_type == "character_state_changed":
                field = str(payload.get("field") or payload.get("field_path") or "").strip()
            else:
                continue

            key = (entity_id, field)
            if not field or key in seen:
                continue

            seen.add(key)
            deltas.append(
                {
                    "entity_id": entity_id,
                    "field": field,
                    "old": (
                        payload.get("old")
                        if "old" in payload
                        else payload.get("from")
                        if "from" in payload
                        else payload.get("old_value")
                        if "old_value" in payload
                        else payload.get("previous_state")
                    ),
                    "new": (
                        payload.get("new")
                        if "new" in payload
                        else payload.get("to")
                        if "to" in payload
                        else payload.get("new_value")
                        if "new_value" in payload
                        else payload.get("new_state")
                    ),
                }
            )
        return deltas

    @staticmethod
    def _normalize_state_delta(delta: dict) -> dict:
        """统一 state_delta 字段名：field/field_path → field, new/new_value → new."""
        result = dict(delta)
        if "field" not in result and "field_path" in result:
            result["field"] = result["field_path"]
        if "new" not in result and "new_value" in result:
            result["new"] = result["new_value"]
        if "old" not in result and "old_value" in result:
            result["old"] = result["old_value"]
        return result

    @staticmethod
    def _set_path(target: dict, path: str, value: Any) -> None:
        """支持点号路径写入嵌套字典：'power.realm' → target['power']['realm']=value。"""
        if not isinstance(target, dict) or not path:
            return
        if "." not in path:
            target[path] = value
            return
        parts = path.split(".")
        cursor = target
        for part in parts[:-1]:
            nxt = cursor.get(part)
            if not isinstance(nxt, dict):
                nxt = {}
                cursor[part] = nxt
            cursor = nxt
        cursor[parts[-1]] = value

    def _collect_protagonist_ids(self, commit_payload: dict, state: dict) -> set[str]:
        """聚合本次 commit + state.json 中已知的主角实体 ID。

        识别信号（任一命中即视为主角）：
        - entity_deltas 子项 `is_protagonist: true`
        - entity_deltas 子项 `tier == "主角"`
        - entity_deltas 的 canonical_name 与 state.protagonist_state.name 相同
        - state.protagonist_state.entity_id 已经被显式设置过
        """
        ids: set[str] = set()

        protagonist_state = state.get("protagonist_state") or {}
        existing_eid = str(protagonist_state.get("entity_id") or "").strip()
        if existing_eid:
            ids.add(existing_eid)
        protagonist_name = str(protagonist_state.get("name") or "").strip()

        for delta in commit_payload.get("entity_deltas") or []:
            if not isinstance(delta, dict):
                continue
            eid = str(delta.get("entity_id") or delta.get("id") or "").strip()
            if not eid:
                continue
            tier = str(delta.get("tier") or "").strip()
            canonical = str(
                delta.get("canonical_name")
                or (delta.get("payload") or {}).get("name")
                or ""
            ).strip()
            if (
                delta.get("is_protagonist")
                or tier == "主角"
                or (protagonist_name and canonical == protagonist_name)
            ):
                ids.add(eid)
        return ids

    def _apply_strand_tracker(self, state: dict, chapter: int, commit_payload: dict) -> bool:
        strand = self._dominant_strand(commit_payload)
        if chapter <= 0 or not strand:
            return False

        tracker = state.get("strand_tracker")
        if not isinstance(tracker, dict):
            tracker = {}
            state["strand_tracker"] = tracker

        valid = ("quest", "fire", "constellation")
        for name in valid:
            tracker.setdefault(f"last_{name}_chapter", 0)
        tracker.setdefault("current_dominant", None)
        tracker.setdefault("chapters_since_switch", 0)

        history = tracker.get("history")
        if not isinstance(history, list):
            history = []

        replaced_strands = set()
        cleaned = []
        for row in history:
            if not isinstance(row, dict):
                continue
            row_chapter = self._safe_int(row.get("chapter"))
            row_strand = str(row.get("dominant") or "").strip().lower()
            if row_chapter <= 0 or row_strand not in valid:
                continue
            if row_chapter == chapter:
                replaced_strands.add(row_strand)
                continue
            cleaned.append({"chapter": row_chapter, "dominant": row_strand})
        cleaned.append({"chapter": chapter, "dominant": strand})
        cleaned.sort(key=lambda row: row["chapter"])
        if len(cleaned) > 50:
            cleaned = cleaned[-50:]
        tracker["history"] = cleaned

        for name in valid:
            history_last = max((row["chapter"] for row in cleaned if row["dominant"] == name), default=0)
            existing_last = 0 if name in replaced_strands else self._safe_int(tracker.get(f"last_{name}_chapter"))
            tracker[f"last_{name}_chapter"] = max(
                existing_last,
                history_last,
            )

        latest = cleaned[-1]
        current = latest["dominant"]
        tracker["current_dominant"] = current
        streak = 0
        for row in reversed(cleaned):
            if row["dominant"] != current:
                break
            streak += 1
        tracker["chapters_since_switch"] = streak
        return True

    def _dominant_strand(self, commit_payload: dict) -> str:
        chapter_meta = commit_payload.get("chapter_meta") or {}
        if not isinstance(chapter_meta, dict):
            chapter_meta = {}
        raw = (
            commit_payload.get("dominant_strand")
            or commit_payload.get("strand")
            or chapter_meta.get("dominant_strand")
            or chapter_meta.get("strand")
            or ""
        )
        strand = str(raw or "").strip().lower()
        return strand if strand in {"quest", "fire", "constellation"} else ""

    def _project_total_words(self, chapter_status: dict) -> int:
        total = 0
        for raw_chapter, raw_status in chapter_status.items():
            if raw_status != "chapter_committed":
                continue
            chapter = self._safe_int(raw_chapter)
            if chapter <= 0:
                continue
            chapter_file = find_chapter_file(self.project_root, chapter)
            if chapter_file is None:
                continue
            try:
                total += self._count_chapter_words(chapter_file.read_text(encoding="utf-8"))
            except OSError:
                continue
        return total

    def _count_chapter_words(self, content: str) -> int:
        text = re.sub(r"```[\s\S]*?```", "", content)
        text = re.sub(r"^#+ .*$", "", text, flags=re.MULTILINE)
        text = re.sub(r"---", "", text)
        return len(text.strip())

    def _safe_int(self, value: object) -> int:
        try:
            return int(value or 0)
        except (TypeError, ValueError):
            return 0


# ══════════════════════════════════════════════════════════════════════════════
# Section 6: SummaryProjectionWriter — 章节摘要投影
# ══════════════════════════════════════════════════════════════════════════════

def append_summary_projection(project_root: Path, commit_payload: dict) -> dict:
    chapter = int(commit_payload.get("meta", {}).get("chapter") or 0)
    summary_text = str(commit_payload.get("summary_text") or "").strip()
    if chapter <= 0 or not summary_text:
        return {"applied": False, "writer": "summary", "reason": "missing_summary"}

    target = Path(project_root) / ".ainovel" / "summaries" / f"ch{chapter:04d}.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    # 切断 Summary 自馈：重写时 unlink 旧版摘要，避免 plan/write ch(N+1) 读到旧版 chN 摘要
    if target.exists():
        target.unlink()
    if "## 剧情摘要" not in summary_text:
        summary_text = f"## 剧情摘要\n{summary_text}\n"
    target.write_text(summary_text, encoding="utf-8")
    return {"applied": True, "writer": "summary", "path": str(target)}


def purge_summary_for_chapter(project_root: Path, chapter: int) -> bool:
    """重置 chapter N 的摘要：删除 .ainovel/summaries/chNNNN.md。"""
    if chapter <= 0:
        return False
    target = Path(project_root) / ".ainovel" / "summaries" / f"ch{chapter:04d}.md"
    if target.exists():
        target.unlink()
        return True
    return False


class SummaryProjectionWriter:
    def __init__(self, project_root: Path):
        self.project_root = Path(project_root)

    def apply(self, commit_payload: dict) -> dict:
        if commit_payload["meta"]["status"] != "accepted":
            return {"applied": False, "writer": "summary", "reason": "commit_rejected"}
        return append_summary_projection(self.project_root, commit_payload)

    @staticmethod
    def purge_chapter(project_root: Path, chapter: int) -> bool:
        """重置 chapter N 的摘要（重写时手动调用）。"""
        return purge_summary_for_chapter(project_root, chapter)


# ══════════════════════════════════════════════════════════════════════════════
# Section 7: MemoryProjectionWriter — memory_scratchpad 投影
# ══════════════════════════════════════════════════════════════════════════════

class MemoryProjectionWriter:
    def __init__(self, project_root: Path):
        self.project_root = Path(project_root)

    def apply(self, commit_payload: dict) -> dict:
        if commit_payload["meta"]["status"] != "accepted":
            return {"applied": False, "writer": "memory", "reason": "commit_rejected"}
        result = MemoryWriter(DataModulesConfig.from_project_root(self.project_root)).apply_commit_projection(
            commit_payload
        )
        return {
            "applied": bool((result or {}).get("items_added") or (result or {}).get("items_updated")),
            "writer": "memory",
            **(result or {}),
        }

    @staticmethod
    def purge_chapter(project_root: Path, chapter: int) -> int:
        """重置 chapter N 在 memory_scratchpad.json 中的所有 active items。

        委托给 memory.writer.purge_chapter_outdated。Returns: 标记为 outdated 的 item 数。
        """
        if chapter <= 0:
            return 0
        from .memory import writer as _memory_writer
        return _memory_writer.purge_chapter_outdated(project_root, chapter)


# ══════════════════════════════════════════════════════════════════════════════
# Section 8: VectorProjectionWriter — 向量库投影
# ══════════════════════════════════════════════════════════════════════════════

class VectorProjectionWriter:
    def __init__(self, project_root: Path):
        self.project_root = Path(project_root)

    def apply(self, commit_payload: dict) -> dict:
        if commit_payload["meta"]["status"] != "accepted":
            return {"applied": False, "writer": "vector", "reason": "commit_rejected"}

        chunks = self._collect_chunks(commit_payload)
        if not chunks:
            return {"applied": False, "writer": "vector", "reason": "no_chunks"}

        try:
            stored = self._store_chunks(chunks)
            return {"applied": stored > 0, "writer": "vector", "stored": stored}
        except Exception as exc:
            logger.warning("vector_projection_failed: %s", exc)
            return {"applied": False, "writer": "vector", "reason": f"error:{exc}"}

    def _collect_chunks(self, commit_payload: dict) -> List[Dict[str, Any]]:
        chunks: List[Dict[str, Any]] = []
        chapter = int(commit_payload.get("meta", {}).get("chapter") or 0)

        chunk_counts: Dict[str, int] = {}

        for event in commit_payload.get("accepted_events") or []:
            if not isinstance(event, dict):
                continue
            text = self._event_to_text(event)
            if text:
                evt_chapter = int(event.get("chapter") or chapter)
                event_key = event.get("event_id") or f"{event.get('event_type')}:{event.get('subject')}:{text}"
                chunk_id = self._unique_chunk_id(chunk_counts, "event", evt_chapter, event_key)
                chunks.append({
                    "chunk_id": chunk_id,
                    "chapter": evt_chapter,
                    "scene_index": 0,
                    "content": text,
                    "chunk_type": "event",
                    "parent_chunk_id": f"ch{evt_chapter:04d}_summary",
                    "source_file": f"commit:chapter_{evt_chapter:03d}",
                })

        for delta in commit_payload.get("entity_deltas") or []:
            if not isinstance(delta, dict):
                continue
            text = self._delta_to_text(delta)
            if text:
                d_chapter = int(delta.get("chapter") or chapter)
                delta_key = delta.get("delta_id") or delta.get("entity_id") or text
                chunk_id = self._unique_chunk_id(chunk_counts, "entity_delta", d_chapter, delta_key)
                chunks.append({
                    "chunk_id": chunk_id,
                    "chapter": d_chapter,
                    "scene_index": 0,
                    "content": text,
                    "chunk_type": "entity_delta",
                    "parent_chunk_id": f"ch{d_chapter:04d}_summary",
                    "source_file": f"commit:chapter_{d_chapter:03d}",
                })

        return chunks

    def _unique_chunk_id(
        self,
        counts: Dict[str, int],
        kind: str,
        chapter: int,
        key: Any,
    ) -> str:
        base_id = self._chunk_id(kind, chapter, key)
        occurrence = counts.get(base_id, 0) + 1
        counts[base_id] = occurrence
        return base_id if occurrence == 1 else f"{base_id}_{occurrence}"

    def _chunk_id(self, kind: str, chapter: int, key: Any) -> str:
        raw = f"{kind}:{chapter}:{key}"
        digest = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]
        return f"ch{chapter:04d}_{kind}_{digest}"

    def _event_to_text(self, event: dict) -> str:
        chapter = int(event.get("chapter") or 0)
        subject = str(event.get("subject") or "").strip()
        event_type = str(event.get("event_type") or "").strip()
        payload = event.get("payload") or {}

        if event_type == "power_breakthrough":
            new_val = str(
                payload.get("new")
                or payload.get("to")
                or payload.get("new_value")
                or payload.get("new_state")
                or ""
            ).strip()
            return f"第{chapter}章：{subject}突破至{new_val}" if new_val else ""
        elif event_type == "character_state_changed":
            field = str(
                payload.get("field") or payload.get("field_path") or ""
            ).strip()
            new_val = str(
                payload.get("new")
                or payload.get("to")
                or payload.get("new_value")
                or payload.get("new_state")
                or ""
            ).strip()
            description = str(payload.get("description") or "").strip()
            if field and new_val:
                return f"第{chapter}章：{subject}的{field}变为{new_val}"
            if new_val:
                return f"第{chapter}章：{subject}变化为{new_val}"
            if description:
                return f"第{chapter}章：{subject}：{description}"
            return ""
        elif event_type == "relationship_changed":
            to_entity = str(payload.get("to_entity") or payload.get("to") or "").strip()
            rel_type = str(payload.get("relationship_type") or payload.get("type") or "").strip()
            return f"第{chapter}章：{subject}与{to_entity}关系变为{rel_type}" if to_entity else ""
        elif event_type in ("world_rule_revealed", "world_rule_broken"):
            desc = str(
                payload.get("description")
                or payload.get("rule")
                or payload.get("rule_content")
                or ""
            ).strip()
            action = "揭示" if "revealed" in event_type else "打破"
            return f"第{chapter}章：{action}世界规则——{desc}" if desc else ""
        elif event_type == "open_loop_created":
            description = str(
                payload.get("description")
                or payload.get("unanswered_question")
                or payload.get("content")
                or ""
            ).strip()
            return f"第{chapter}章：{subject}埋下悬念——{description}" if description else ""
        elif event_type == "artifact_obtained":
            name = str(payload.get("name") or subject or "").strip()
            owner = str(payload.get("owner") or payload.get("holder") or "").strip()
            return f"第{chapter}章：{owner}获得{name}" if owner else f"第{chapter}章：获得{name}"
        return ""

    def _delta_to_text(self, delta: dict) -> str:
        chapter = int(delta.get("chapter") or 0)
        from_e = str(delta.get("from_entity") or "").strip()
        to_e = str(delta.get("to_entity") or "").strip()
        rel = str(delta.get("relationship_type") or "").strip()

        if from_e and to_e and rel:
            return f"第{chapter}章：{from_e}与{to_e}关系变为{rel}"

        entity_id = str(delta.get("entity_id") or "").strip()
        canonical = str(delta.get("canonical_name") or entity_id).strip()
        if entity_id:
            return f"第{chapter}章：实体变更——{canonical}"
        return ""

    def _store_chunks(self, chunks: List[Dict[str, Any]]) -> int:
        from .rag_adapter import RAGAdapter

        config = DataModulesConfig.from_project_root(self.project_root)
        adapter = RAGAdapter(config)

        async def _run() -> int:
            try:
                return await adapter.store_chunks(chunks)
            finally:
                # 关闭底层 aiohttp session，避免「Unclosed client session」告警
                api_client = getattr(adapter, "api_client", None)
                if api_client is not None and hasattr(api_client, "close"):
                    try:
                        await api_client.close()
                    except Exception:
                        pass

        try:
            stored = asyncio.run(_run())
            return stored
        except Exception as exc:
            logger.warning("vector_store_failed: %s", exc)
            return 0

    @staticmethod
    def purge_chapter(project_root: "Path", chapter: int) -> int:
        """重置 chapter N 在 vectors.db 中的所有 chunks（同步删 bm25_index / doc_stats 关联行）。

        返回删除的 vectors 行数。
        """
        if chapter <= 0:
            return 0
        from .rag_adapter import RAGAdapter

        config = DataModulesConfig.from_project_root(project_root)
        adapter = RAGAdapter(config)
        return adapter.delete_chapter_chunks(chapter)
