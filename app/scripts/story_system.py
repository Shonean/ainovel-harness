#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from pydantic import BaseModel, Field

from runtime_compat import enable_windows_utf8_stdio

from data_modules.story_contracts import persist_runtime_contracts, persist_story_seed, read_json_if_exists, merge_anti_patterns
from reference_search import (
    CSV_CONFIG,
    GENRE_CANONICAL,
    resolve_genre,
    search as search_reference,
    split_multi_value,
)
from chapter_outline_loader import load_chapter_execution_directive, load_chapter_plot_structure, volume_num_for_chapter_from_state



# ── Inlined from runtime_contract_builder.py ──

# ── Contract schemas（来自 story_contract_schema.py） ──

class ContractMeta(BaseModel):
    schema_version: str = "story-system/v1"
    contract_type: str
    generator_version: str = "phase2"
    source_trace: List[Dict[str, Any]] = Field(default_factory=list)


class OverrideBundle(BaseModel):
    locked: Dict[str, Any] = Field(default_factory=dict)
    append_only: Dict[str, Any] = Field(default_factory=dict)
    override_allowed: Dict[str, Any] = Field(default_factory=dict)


class MasterSetting(BaseModel):
    meta: ContractMeta
    route: Dict[str, Any] = Field(default_factory=dict)
    master_constraints: Dict[str, Any] = Field(default_factory=dict)
    base_context: List[Dict[str, Any]] = Field(default_factory=list)
    source_trace: List[Dict[str, Any]] = Field(default_factory=list)
    override_policy: Dict[str, List[str]] = Field(default_factory=dict)


class ChapterBrief(BaseModel):
    meta: ContractMeta
    override_allowed: Dict[str, Any] = Field(default_factory=dict)
    chapter_directive: Dict[str, Any] = Field(default_factory=dict)
    dynamic_context: List[Dict[str, Any]] = Field(default_factory=list)
    source_trace: List[Dict[str, Any]] = Field(default_factory=list)


class VolumeBrief(BaseModel):
    meta: ContractMeta
    volume_goal: Dict[str, Any]
    selected_tropes: List[str] = Field(default_factory=list)
    selected_pacing: Dict[str, Any] = Field(default_factory=dict)
    selected_scenes: List[str] = Field(default_factory=list)
    anti_patterns: List[str] = Field(default_factory=list)
    system_constraints: List[str] = Field(default_factory=list)
    overrides: OverrideBundle = Field(default_factory=OverrideBundle)


class ReviewContract(BaseModel):
    meta: ContractMeta
    must_check: List[str] = Field(default_factory=list)
    blocking_rules: List[str] = Field(default_factory=list)
    genre_specific_risks: List[str] = Field(default_factory=list)
    anti_patterns: List[str] = Field(default_factory=list)
    system_constraints: List[str] = Field(default_factory=list)
    review_thresholds: Dict[str, Any] = Field(default_factory=dict)
    overrides: OverrideBundle = Field(default_factory=OverrideBundle)


class RuntimeContractBuilder:
    def __init__(self, project_root: Path):
        self.project_root = Path(project_root)

    def build_for_chapter(self, chapter: int) -> Tuple[Dict[str, Any], Dict[str, Any]]:
        master = self._load_master_setting()
        anti_patterns = self._load_anti_patterns()
        plot = self._load_plot_structure(chapter)
        volume = self._resolve_volume(chapter)

        volume_brief = VolumeBrief.model_validate(
            {
                "meta": {"schema_version": "story-system/v1", "contract_type": "VOLUME_BRIEF"},
                "volume_goal": {"summary": f"第{volume}卷延续 {master.route.get('primary_genre', '')} 的主冲突"},
                "selected_tropes": [master.route.get("primary_genre", "")],
                "selected_pacing": {"wave": master.master_constraints.get("pacing_strategy", "")},
                "selected_scenes": list(plot.get("cpns") or []),
                "anti_patterns": [row.get("text", "") for row in anti_patterns if row.get("text")],
                "system_constraints": [master.master_constraints.get("core_tone", "")] if master.master_constraints.get("core_tone") else [],
                "overrides": {"locked": {}, "append_only": {}, "override_allowed": {}},
            }
        ).model_dump()
        review_contract = ReviewContract.model_validate(
            {
                "meta": {"schema_version": "story-system/v1", "contract_type": "REVIEW_CONTRACT"},
                "must_check": list(plot.get("mandatory_nodes") or []),
                "blocking_rules": list(plot.get("prohibitions") or []),
                "genre_specific_risks": [master.route.get("primary_genre", "")] if master.route.get("primary_genre") else [],
                "anti_patterns": volume_brief["anti_patterns"],
                "system_constraints": volume_brief["system_constraints"],
                "review_thresholds": {"blocking_count": 0, "missed_nodes": 0},
                "overrides": {"locked": {}, "append_only": {}, "override_allowed": {}},
            }
        ).model_dump()
        return volume_brief, review_contract

    def _load_master_setting(self) -> MasterSetting:
        raw = read_json_if_exists(self.project_root / ".story-system" / "MASTER_SETTING.json") or {}
        return MasterSetting.model_validate(raw)

    def _load_anti_patterns(self) -> list[Dict[str, Any]]:
        raw = read_json_if_exists(self.project_root / ".story-system" / "anti_patterns.json") or []
        return list(raw)

    def _load_plot_structure(self, chapter: int) -> Dict[str, Any]:
        raw = load_chapter_plot_structure(self.project_root, chapter) or {}
        return {
            "mandatory_nodes": list(raw.get("mandatory_nodes") or []),
            "prohibitions": list(raw.get("prohibitions") or []),
            "cpns": list(raw.get("cpns") or []),
        }

    def _resolve_volume(self, chapter: int) -> int:
        return volume_num_for_chapter_from_state(self.project_root, chapter) or 1

# ── StorySystemEngine (merged from story_system_engine.py) ──

ANTI_PATTERN_SOURCE_FIELDS = {
    "场景写法": ["毒点"],
    "写作技法": ["毒点"],
    "爽点与节奏": ["毒点"],
    "人设与关系": ["毒点"],
    "桥段套路": ["毒点"],
    "题材与调性推理": ["毒点"],
    "命名规则": ["毒点"],
    "金手指与设定": ["毒点"],
}

_TEXT_TOKEN_RE = re.compile(r"[\s|,，、/；;：:（）()【】\[\]<>《》\"'!?！？。…]+")
_PLACEHOLDER_QUERY_RE = re.compile(r"^\s*(\{[^{}]*章纲目标[^{}]*\}|第\s*\d+\s*章\s*章纲目标)\s*$")
_ASCII_LETTER_RE = re.compile(r"[A-Za-z]")


def is_placeholder_query(query: str) -> bool:
    text = str(query or "").strip()
    if not text:
        return True
    return bool(_PLACEHOLDER_QUERY_RE.match(text))


class StorySystemRoutingError(ValueError):
    """Raised when story-system cannot select a route row."""


def _validate_explicit_genre_source(genre: Optional[str]) -> Optional[str]:
    normalized = str(genre or "").strip()
    if not normalized:
        return None
    if _ASCII_LETTER_RE.search(normalized):
        raise StorySystemRoutingError(
            "story-system 题材参数必须使用中文名称，不能使用英文 profile key "
            f"'{normalized}'。不会生成 .story-system contracts。"
            "例如：规则怪谈、悬疑、玄幻。"
        )
    return normalized


class StorySystemEngine:
    def __init__(self, csv_dir: str | Path):
        self.csv_dir = Path(csv_dir)

    def build(
        self,
        query: str,
        genre: Optional[str],
        chapter: Optional[int],
        chapter_directive: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        chapter_directive = chapter_directive or {}
        genre = _validate_explicit_genre_source(genre)
        route = self._route(query=query, genre=genre)
        search_query = self._expand_query(
            query,
            route.get("default_query", ""),
            self._directive_query_text(chapter_directive),
        )
        base_context = self._collect_tables(
            search_query,
            route["recommended_base_tables"],
            genre=route["genre_filter"],
            top_k=1,
        )
        dynamic_context = self._collect_tables(
            search_query,
            route["recommended_dynamic_tables"],
            genre=route["genre_filter"],
            top_k=2,
        )

        # Reasoning layer — try routed genre first, then original genre
        canonical_genre = str(route.get("meta", {}).get("canonical_genre", "") or "").strip()
        reasoning = self._load_reasoning(canonical_genre)
        if not reasoning and genre:
            fallback_genre = self._primary_resolved_genre(genre) or genre
            if fallback_genre != canonical_genre:
                reasoning = self._load_reasoning(fallback_genre)
        ranked = self._apply_reasoning(reasoning, base_context, dynamic_context, chapter_directive)

        source_trace = route["source_trace"] + self._build_source_trace_with_reasoning(ranked, reasoning)

        raw_anti = merge_anti_patterns(
            route["route_anti_patterns"],
            self._extract_anti_patterns(base_context),
            self._extract_anti_patterns(dynamic_context),
        )
        anti_patterns = self._rank_anti_patterns(reasoning, raw_anti)

        return {
            "meta": {"query": query, "chapter": chapter, "explicit_genre": genre or ""},
            "master_setting": {
                "meta": {
                    "schema_version": "story-system/v1",
                    "contract_type": "MASTER_SETTING",
                    "generator_version": "phase1",
                    "query": query,
                },
                "route": route["meta"],
                "master_constraints": {
                    "core_tone": route["core_tone"],
                    "pacing_strategy": route["pacing_strategy"],
                },
                "base_context": [r for r in ranked if r.get("_priority_rank", 999) < 999],
                "source_trace": source_trace,
                "override_policy": {
                    "locked": ["route.primary_genre", "master_constraints.core_tone"],
                    "append_only": ["anti_patterns"],
                    "override_allowed": [],
                },
            },
            "chapter_brief": (
                {
                    "meta": {
                        "schema_version": "story-system/v1",
                        "contract_type": "CHAPTER_BRIEF",
                        "generator_version": "phase1",
                        "chapter": chapter,
                    },
                    "override_allowed": {
                        "chapter_focus": self._suggest_chapter_focus(query, chapter_directive),
                    },
                    "chapter_directive": chapter_directive,
                    "dynamic_context": ranked,
                    "source_trace": source_trace,
                    "reasoning": (
                        {
                            "genre": reasoning.get("题材", ""),
                            "inject_target": self._reasoning_inject_target(reasoning),
                            "style_priority": reasoning.get("风格优先级", ""),
                            "pacing_strategy": reasoning.get("节奏默认策略", ""),
                        }
                        if reasoning
                        else {}
                    ),
                }
                if chapter is not None
                else None
            ),
            "anti_patterns": anti_patterns,
        }

    def _route(self, query: str, genre: Optional[str]) -> Dict[str, Any]:
        route_rows = self._load_csv_rows("题材与调性推理")
        query_text = self._normalize_text(" ".join([query or "", genre or ""]))
        inferred_canonical = "" if genre else self._infer_genre_from_text(query)

        matched = None
        route_source = ""
        for row in route_rows:
            aliases = (
                self._split_multi_value(row.get("关键词"))
                + self._split_multi_value(row.get("意图与同义词"))
                + self._split_multi_value(row.get("题材别名"))
            )
            if any(alias and self._normalize_text(alias) in query_text for alias in aliases):
                matched = row
                route_source = "keyword_or_alias_match"
                break
        if matched is None and genre:
            matched = self._fallback_row_for_genre(route_rows, genre)
            if matched is not None:
                route_source = "explicit_genre_fallback"
        if matched is None and inferred_canonical:
            matched = self._fallback_row_for_genre(route_rows, inferred_canonical)
            if matched is not None:
                route_source = "inferred_genre_fallback"
        if matched is None:
            raise self._routing_error(query=query, genre=genre, route_rows=route_rows)

        primary_genre = str(matched.get("题材/流派") or genre or "").strip()
        explicit_canonical = self._primary_resolved_genre(genre)
        canonical_genre = str(matched.get("canonical_genre") or "").strip()
        row_canonicals = [
            resolved
            for raw in self._split_genre_value(matched.get("适用题材"))
            for resolved in [resolve_genre(raw) or str(raw or "").strip()]
            if resolved and resolved != "全部"
        ]
        if explicit_canonical and explicit_canonical != "全部":
            if not row_canonicals or explicit_canonical in row_canonicals or canonical_genre in ("", "全部"):
                canonical_genre = explicit_canonical
        elif inferred_canonical and inferred_canonical != "全部":
            if not row_canonicals or inferred_canonical in row_canonicals or canonical_genre in ("", "全部"):
                canonical_genre = inferred_canonical
        if not canonical_genre:
            resolved_primary = resolve_genre(primary_genre)
            if resolved_primary in GENRE_CANONICAL:
                canonical_genre = resolved_primary
            elif explicit_canonical and explicit_canonical != "全部":
                canonical_genre = explicit_canonical
        genre_filter = canonical_genre if canonical_genre not in ("", "全部") else ""
        return {
            "meta": {
                "primary_genre": primary_genre,
                "canonical_genre": canonical_genre,
                "route_source": route_source,
                "genre_filter": genre_filter,
                "recommended_base_tables": self._split_multi_value(matched.get("推荐基础检索表")),
                "recommended_dynamic_tables": self._split_multi_value(matched.get("推荐动态检索表")),
            },
            "core_tone": str(matched.get("核心调性") or "").strip(),
            "pacing_strategy": str(matched.get("节奏策略") or "").strip(),
            "route_anti_patterns": self._extract_route_anti_patterns(matched),
            "recommended_base_tables": self._split_multi_value(matched.get("推荐基础检索表")),
            "recommended_dynamic_tables": self._split_multi_value(matched.get("推荐动态检索表")),
            "genre_filter": genre_filter,
            "default_query": str(matched.get("默认查询词") or "").strip(),
            "source_trace": [{"table": "题材与调性推理", "id": matched.get("编号", ""), "reason": route_source}],
        }

    def _collect_tables(self, query: str, tables: List[str], genre: str, top_k: int) -> List[Dict[str, Any]]:
        rows: List[Dict[str, Any]] = []
        for table_name in tables:
            result = search_reference(
                csv_dir=self.csv_dir,
                skill="write",
                query=query,
                table=table_name,
                genre=genre or None,
                max_results=top_k,
            )
            raw_rows = {str(row.get("编号") or ""): row for row in self._load_csv_rows(table_name)}
            for item in result.get("data", {}).get("results", []):
                row_id = str(item.get("编号") or "")
                full_row = dict(raw_rows.get(row_id) or {})
                full_row["_table"] = str(item.get("表") or table_name)
                full_row["编号"] = row_id
                full_row["核心摘要"] = str(
                    full_row.get("核心摘要")
                    or item.get("内容摘要")
                    or item.get("核心摘要")
                    or ""
                ).strip()
                rows.append(full_row)
        return rows

    def _extract_anti_patterns(self, rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        extracted: List[Dict[str, Any]] = []
        for row in rows:
            table_name = str(row.get("_table") or "")
            for field_name in ANTI_PATTERN_SOURCE_FIELDS.get(table_name, []):
                for text in self._split_multi_value(row.get(field_name)):
                    extracted.append(
                        {
                            "text": text,
                            "source_table": table_name,
                            "source_id": row.get("编号", ""),
                        }
                    )
        return extracted

    def _suggest_chapter_focus(self, query: str, chapter_directive: Optional[Dict[str, Any]] = None) -> str:
        directive = chapter_directive or {}
        goal = str(directive.get("goal") or "").strip()
        if goal:
            return goal
        query_text = str(query or "").strip()
        if query_text and not is_placeholder_query(query_text):
            return query_text
        return ""

    def _build_source_trace(self, *groups: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        trace: List[Dict[str, Any]] = []
        for group in groups:
            for row in group:
                trace.append(
                    {
                        "table": row.get("_table", ""),
                        "id": row.get("编号", ""),
                        "summary": row.get("核心摘要", ""),
                    }
                )
        return trace

    def _load_csv_rows(self, table_name: str) -> List[Dict[str, Any]]:
        csv_path = self.csv_dir / f"{table_name}.csv"
        if not csv_path.is_file():
            return []
        with csv_path.open("r", encoding="utf-8-sig", newline="") as f:
            return list(csv.DictReader(f))

    def _normalize_text(self, text: str) -> str:
        return str(text or "").strip().lower()

    def _split_multi_value(self, raw: Any) -> List[str]:
        return [item.strip() for item in re.split(r"[|；;]+", str(raw or "")) if item.strip()]

    def _split_genre_value(self, raw: Any) -> List[str]:
        return split_multi_value(raw)

    def _resolve_genre_values(self, raw: Any) -> List[str]:
        return [
            resolved
            for token in self._split_genre_value(raw)
            for resolved in [resolve_genre(token)]
            if resolved
        ]

    def _primary_resolved_genre(self, raw: Any) -> Optional[str]:
        resolved_values = self._resolve_genre_values(raw)
        for value in resolved_values:
            if value in GENRE_CANONICAL or value == "全部":
                return value
        return resolved_values[0] if resolved_values else None

    def _expand_query(self, query: str, default_query: str, chapter_query: str = "") -> str:
        items: List[str] = []
        for candidate in [query, chapter_query, *self._split_multi_value(default_query)]:
            text = str(candidate or "").strip()
            if text and text not in items:
                items.append(text)
        return " ".join(items)

    def _directive_query_text(self, chapter_directive: Dict[str, Any]) -> str:
        parts: List[str] = []
        for key in ("goal", "strand", "antagonist_tier"):
            value = str(chapter_directive.get(key) or "").strip()
            if value:
                parts.append(value)
        for key in ("key_entities", "must_cover_nodes"):
            for value in chapter_directive.get(key) or []:
                text = str(value or "").strip()
                if text:
                    parts.append(text)
        return " ".join(parts)

    def _fallback_row_for_genre(self, rows: List[Dict[str, Any]], genre: str) -> Dict[str, Any] | None:
        genre_texts = {
            self._normalize_text(value)
            for value in self._resolve_genre_values(genre)
            if value
        }
        for row in rows:
            candidates = (
                self._split_genre_value(row.get("适用题材"))
                + self._split_genre_value(row.get("题材/流派"))
                + self._split_genre_value(row.get("canonical_genre"))
            )
            resolved_candidates = {
                self._normalize_text(resolve_genre(candidate) or candidate)
                for candidate in candidates
                if candidate
            }
            if genre_texts.intersection(resolved_candidates):
                return row
        return None

    def _infer_genre_from_text(self, text: str) -> str:
        """Infer a canonical genre from plain query text before default routing."""
        raw_text = str(text or "")
        tokens = [token.strip() for token in _TEXT_TOKEN_RE.split(raw_text) if token.strip()]
        for candidate in tokens:
            resolved = resolve_genre(candidate)
            if resolved in GENRE_CANONICAL:
                return resolved or ""

        normalized = self._normalize_text(raw_text)
        for canonical in sorted(GENRE_CANONICAL, key=len, reverse=True):
            if self._normalize_text(canonical) in normalized:
                return canonical
        return ""

    def _extract_route_anti_patterns(self, row: Dict[str, Any]) -> List[Dict[str, Any]]:
        return [
            {"text": text, "source_table": "题材与调性推理", "source_id": row.get("编号", "")}
            for text in self._split_multi_value(row.get("毒点"))
        ]

    # ------------------------------------------------------------------
    # Reasoning / 裁决 layer
    # ------------------------------------------------------------------

    def _load_reasoning(self, genre: str) -> Dict[str, Any]:
        """Load matching row from 裁决规则.csv for *genre*."""
        rows = self._load_csv_rows("裁决规则")
        genre_norm = self._normalize_text(genre)
        if not genre_norm:
            return {}
        for row in rows:
            if self._normalize_text(row.get("题材")) == genre_norm:
                return row
            aliases = (
                self._split_multi_value(row.get("关键词"))
                + self._split_multi_value(row.get("意图与同义词"))
            )
            if any(genre_norm == self._normalize_text(a) for a in aliases):
                return row
        return {}

    def _apply_reasoning(
        self,
        reasoning: Dict[str, Any],
        base_context: List[Dict[str, Any]],
        dynamic_context: List[Dict[str, Any]],
        chapter_directive: Optional[Dict[str, Any]] = None,
    ) -> List[Dict[str, Any]]:
        """Rank *base_context* + *dynamic_context* rows using 冲突裁决 priority."""
        combined = [dict(r) for r in base_context] + [dict(r) for r in dynamic_context]
        chapter_terms = self._chapter_keyword_terms(chapter_directive or {})
        if not reasoning and not chapter_terms:
            return combined

        priority_order = [
            s.strip()
            for s in str(reasoning.get("冲突裁决") or "").split(">")
            if s.strip()
        ]
        priority_map = {name: idx for idx, name in enumerate(priority_order)}

        genre_label = reasoning.get("题材", "")
        ranked_priorities = sorted(priority_map.values())
        max_priority = max(ranked_priorities) if ranked_priorities else 0
        max_chapter_score = 0
        for row in combined:
            table = str(row.get("_table") or "")
            row["_priority_rank"] = priority_map.get(table, 999)
            row["_reasoning_rule"] = genre_label
            row["_chapter_keyword_score"] = self._chapter_keyword_score(row, chapter_terms)
            max_chapter_score = max(max_chapter_score, int(row.get("_chapter_keyword_score") or 0))

        for row in combined:
            row["_combined_rank_score"] = self._combined_rank_score(
                int(row.get("_priority_rank") or 999),
                int(row.get("_chapter_keyword_score") or 0),
                max_priority=max_priority,
                max_chapter_score=max_chapter_score,
                has_reasoning=bool(priority_map),
                has_chapter_terms=bool(chapter_terms),
            )

        combined.sort(
            key=lambda r: (
                -float(r.get("_combined_rank_score") or 0.0),
                r["_priority_rank"],
                -int(r.get("_chapter_keyword_score") or 0),
            )
        )
        return combined

    def _combined_rank_score(
        self,
        priority_rank: int,
        chapter_score: int,
        *,
        max_priority: int,
        max_chapter_score: int,
        has_reasoning: bool,
        has_chapter_terms: bool,
    ) -> float:
        priority_component = 0.0
        if has_reasoning:
            if priority_rank >= 999:
                priority_component = 0.0
            elif max_priority <= 0:
                priority_component = 1.0
            else:
                priority_component = 1.0 - (priority_rank / float(max_priority + 1))

        chapter_component = 0.0
        if has_chapter_terms and max_chapter_score > 0:
            chapter_component = chapter_score / float(max_chapter_score)

        if has_reasoning and has_chapter_terms:
            return round((priority_component * 0.4) + (chapter_component * 0.6), 6)
        if has_chapter_terms:
            return round(chapter_component, 6)
        return round(priority_component, 6)

    def _chapter_keyword_terms(self, chapter_directive: Dict[str, Any]) -> List[str]:
        raw_items: List[str] = []
        for key in ("goal", "strand", "antagonist_tier"):
            value = str(chapter_directive.get(key) or "").strip()
            if value:
                raw_items.append(value)
        for key in ("key_entities", "must_cover_nodes"):
            raw_items.extend(str(item or "") for item in chapter_directive.get(key) or [])

        terms: List[str] = []
        for item in raw_items:
            for token in _TEXT_TOKEN_RE.split(item):
                token = token.strip().lower()
                if len(token) >= 2 and token not in terms:
                    terms.append(token)
        return terms

    def _chapter_keyword_score(self, row: Dict[str, Any], terms: List[str]) -> int:
        if not terms:
            return 0
        haystack = " ".join(
            str(row.get(field) or "")
            for field in ("关键词", "意图与同义词", "适用场景", "核心摘要", "详细展开")
        ).lower()
        return sum(1 for term in terms if term and term in haystack)

    def _rank_anti_patterns(
        self,
        reasoning: Dict[str, Any],
        anti_patterns: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        """Sort *anti_patterns* by 毒点权重 and append reasoning 反模式."""
        if not reasoning:
            return anti_patterns

        weight_order = [
            s.strip()
            for s in str(reasoning.get("毒点权重") or "").split(">")
            if s.strip()
        ]

        def _sort_key(item: Dict[str, Any]) -> int:
            text = str(item.get("text") or "")
            for idx, keyword in enumerate(weight_order):
                if keyword in text:
                    return idx
            return len(weight_order)

        sorted_anti = sorted(anti_patterns, key=_sort_key)

        # Append 反模式 entries from reasoning row
        existing_texts = {str(a.get("text") or "") for a in sorted_anti}
        for text in self._split_multi_value(reasoning.get("反模式")):
            if text and text not in existing_texts:
                sorted_anti.append(
                    {"text": text, "source_table": "裁决规则", "source_id": reasoning.get("编号", "")}
                )
                existing_texts.add(text)

        return sorted_anti

    def _build_source_trace_with_reasoning(
        self,
        ranked: List[Dict[str, Any]],
        reasoning: Dict[str, Any],
    ) -> List[Dict[str, Any]]:
        """Build source trace entries enriched with reasoning metadata."""
        inject_target = self._reasoning_inject_target(reasoning)
        trace: List[Dict[str, Any]] = []
        for row in ranked:
            trace.append(
                {
                    "table": row.get("_table", ""),
                    "id": row.get("编号", ""),
                    "summary": row.get("核心摘要", ""),
                    "reasoning_rule": row.get("_reasoning_rule", ""),
                    "priority_rank": row.get("_priority_rank", 999),
                    "chapter_keyword_score": row.get("_chapter_keyword_score", 0),
                    "combined_rank_score": row.get("_combined_rank_score", 0),
                    "inject_target": inject_target,
                }
            )
        return trace

    def _reasoning_inject_target(self, reasoning: Dict[str, Any]) -> str:
        if reasoning:
            explicit = str(reasoning.get("contract注入层") or "").strip()
            if explicit:
                return explicit
        cfg = CSV_CONFIG.get("裁决规则") or {}
        return str(cfg.get("contract_inject") or "")

    def _routing_error(
        self,
        *,
        query: str,
        genre: Optional[str],
        route_rows: List[Dict[str, Any]],
    ) -> StorySystemRoutingError:
        query_text = str(query or "").strip()
        genre_text = str(genre or "").strip()
        if not route_rows:
            detail = "题材与调性推理.csv 没有可用路由行"
        else:
            detail = f"query={query_text!r}, genre={genre_text!r} 未命中任何路由行"
        return StorySystemRoutingError(
            f"无法匹配 story-system 题材路由：{detail}。"
            "不会生成 .story-system contracts。"
            "请使用中文题材/流派（例如：规则怪谈、玄幻、仙侠），"
            "或先在 题材与调性推理.csv 添加路由行。"
        )

def _default_csv_dir() -> Path:
    return Path(__file__).resolve().parent.parent / "references" / "csv"


def _resolve_project_root(raw: str) -> Path:
    if raw:
        return Path(raw).expanduser().resolve()

    from project_locator import resolve_project_root

    return resolve_project_root()


def _render_output(format_name: str, contract: dict) -> str:
    if format_name == "json":
        return json.dumps(contract, ensure_ascii=False, indent=2)
    if format_name == "markdown":
        lines = [
            "# Story System",
            f"- 题材：{contract['master_setting']['route'].get('primary_genre', '')}",
        ]
        if contract.get("chapter_brief"):
            lines.append(
                f"- 章节焦点：{contract['chapter_brief']['override_allowed'].get('chapter_focus', '')}"
            )
        return "\n".join(lines)
    return json.dumps(
        {
            "master": contract["master_setting"].get("route", {}),
            "chapter": (contract.get("chapter_brief") or {}).get("override_allowed", {}),
            "anti_patterns": [row.get("text", "") for row in contract.get("anti_patterns", [])],
        },
        ensure_ascii=False,
        indent=2,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Story system seed generator")
    parser.add_argument("query", help="题材 / 需求描述")
    parser.add_argument("--project-root", default="")
    parser.add_argument("--genre", default="")
    parser.add_argument("--chapter", type=int, default=0)
    parser.add_argument("--persist", action="store_true")
    parser.add_argument("--emit-runtime-contracts", action="store_true")
    parser.add_argument("--csv-dir", default="")
    parser.add_argument("--format", choices=["json", "markdown", "both"], default="json")

    args = parser.parse_args()
    project_root = _resolve_project_root(args.project_root)
    csv_dir = Path(args.csv_dir).expanduser().resolve() if args.csv_dir else _default_csv_dir()
    if is_placeholder_query(args.query):
        print(
            "warning: story-system query appears to be a placeholder; parse the real chapter goal from the outline.",
            file=sys.stderr,
        )
    chapter_directive = (
        load_chapter_execution_directive(project_root, args.chapter)
        if args.chapter
        else {}
    )
    engine = StorySystemEngine(csv_dir=csv_dir)
    try:
        contract = engine.build(
            query=args.query,
            genre=args.genre or None,
            chapter=args.chapter or None,
            chapter_directive=chapter_directive,
        )
    except StorySystemRoutingError as exc:
        parser.exit(2, f"error: {exc}\n")

    if args.persist:
        persist_story_seed(
            project_root=project_root,
            master_payload=contract["master_setting"],
            chapter_payload=contract.get("chapter_brief"),
            anti_patterns=contract["anti_patterns"],
        )
    if args.emit_runtime_contracts:
        if not args.chapter:
            raise ValueError("--emit-runtime-contracts 需要 --chapter")
        volume_brief, review_contract = RuntimeContractBuilder(project_root).build_for_chapter(args.chapter)
        persist_runtime_contracts(project_root, args.chapter, volume_brief, review_contract)

    print(_render_output(args.format, contract))


if __name__ == "__main__":
    if sys.platform == "win32":
        enable_windows_utf8_stdio()
    main()
