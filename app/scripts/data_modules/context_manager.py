#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ContextManager - assemble context packs with weighted priorities.
"""
from __future__ import annotations

import json
import math
import re
import sys
import logging
from pathlib import Path

from runtime_compat import enable_windows_utf8_stdio
from typing import Any, Dict, List, Optional

try:
    from chapter_outline_loader import (
        load_chapter_outline,
        load_chapter_plot_structure,
    )
except ImportError:  # pragma: no cover
    from scripts.chapter_outline_loader import (
        load_chapter_outline,
        load_chapter_plot_structure,
    )

from .config import (
    get_config,
    DEFAULT_TEMPLATE as CONTEXT_DEFAULT_TEMPLATE,
    TEMPLATE_WEIGHTS as CONTEXT_TEMPLATE_WEIGHTS,
    TEMPLATE_WEIGHTS_DYNAMIC_DEFAULT as CONTEXT_TEMPLATE_WEIGHTS_DYNAMIC_DEFAULT,
)
from .index_manager import IndexManager, WritingChecklistScoreMeta
from .story_contracts import read_json_if_exists
from .story_runtime_sources import RuntimeSourceSnapshot, load_runtime_sources
from .genre_aliases import normalize_genre_token, to_profile_key
from .genre_profile_builder import (
    build_composite_genre_hints,
    extract_genre_section,
    extract_markdown_refs,
    parse_genre_tokens,
)
# ── PrewriteValidator + placeholder scanner (merged from prewrite_validator.py) ─

PLACEHOLDER_PATTERNS = [
    re.compile(r"\[待[^\]]*\]"),
    re.compile(r"（暂名）|\(暂名\)|（待补充）|\(待补充\)"),
    re.compile(r"\{占位\}|<占位>"),
]


def _scan_file(path: Path, project_root: Path) -> List[Dict[str, Any]]:
    results: List[Dict[str, Any]] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except UnicodeDecodeError:
        return results
    for line_no, line in enumerate(lines, start=1):
        for pattern in PLACEHOLDER_PATTERNS:
            for match in pattern.finditer(line):
                rel = path.relative_to(project_root).as_posix()
                results.append({
                    "file": rel, "line": line_no,
                    "pattern": match.group(0), "context": line.strip(),
                    "suggested_fill_phase": "plan" if rel.startswith("大纲/") else "setting_update",
                })
    return results


def scan_placeholders(project_root: str | Path) -> List[Dict[str, Any]]:
    root = Path(project_root).expanduser().resolve()
    targets: List[Path] = []
    for dirname in ("大纲", "设定集"):
        base = root / dirname
        if base.is_dir():
            targets.extend(sorted(base.rglob("*.md")))
    results: List[Dict[str, Any]] = []
    for path in targets:
        results.extend(_scan_file(path, root))
    return results


class PrewriteValidator:
    def __init__(self, project_root: Path):
        self.project_root = Path(project_root)

    def build(
        self,
        chapter: int,
        review_contract: Dict[str, Any],
        plot_structure: Dict[str, Any],
        story_contract: Dict[str, Any] | None = None,
    ) -> Dict[str, Any]:
        state = json.loads(
            (self.project_root / ".ainovel" / "state.json").read_text(encoding="utf-8")
        )
        pending = state.get("disambiguation_pending") or []
        warnings = state.get("disambiguation_warnings") or []
        contract_provided = story_contract is not None
        story_contract = story_contract or {}
        missing_contracts = []
        if contract_provided:
            missing_contracts = [
                name
                for name in ("master_setting", "chapter_brief", "volume_brief", "review_contract")
                if not story_contract.get(name)
            ]
        blocking_reasons = []
        if pending:
            blocking_reasons.append("存在高优先级 disambiguation_pending")
        if missing_contracts:
            blocking_reasons.append(
                "缺少 Story System 合同: " + ", ".join(missing_contracts)
            )
        related_placeholders = self._related_placeholders(story_contract)
        if related_placeholders:
            blocking_reasons.append("当前章节相关设定存在未补齐占位")
        return {
            "chapter": chapter,
            "blocking": bool(pending) or bool(missing_contracts) or bool(related_placeholders),
            "blocking_reasons": blocking_reasons,
            "missing_contracts": missing_contracts,
            "related_placeholders": related_placeholders,
            "forbidden_zones": list(review_contract.get("blocking_rules") or []),
            "disambiguation_domain": {
                "pending_count": len(pending),
                "warning_count": len(warnings),
                "allowed_mentions": [
                    item.get("mention", "")
                    for item in warnings
                    if isinstance(item, dict) and item.get("mention")
                ],
            },
            "fulfillment_seed": {
                "planned_nodes": list(plot_structure.get("mandatory_nodes") or []),
                "prohibitions": list(plot_structure.get("prohibitions") or []),
            },
        }

    def _related_placeholders(self, story_contract: Dict[str, Any]) -> list[Dict[str, Any]]:
        chapter_brief = story_contract.get("chapter_brief") or {}
        directive = chapter_brief.get("chapter_directive") or {}
        entity_terms = [
            str(item or "").strip()
            for item in directive.get("key_entities") or []
            if str(item or "").strip()
        ]
        if not entity_terms:
            return []

        related: list[Dict[str, Any]] = []
        for item in scan_placeholders(self.project_root):
            context = str(item.get("context") or "")
            file_name = Path(str(item.get("file") or "")).stem
            if any(term in context or term in file_name for term in entity_terms):
                related.append(item)
        return related


# ── Writing guidance builder (merged from writing_guidance_builder.py) ─

GENRE_GUIDANCE_TEXT: dict[str, str] = {
    "xianxia": "题材加权：强化升级/对抗结果的可见反馈，术语解释后置。",
    "shuangwen": "题材加权：维持高爽点密度，主爽点外叠加一个副轴反差。",
    "urban-power": "题材加权：优先写社会反馈链（他人反应→资源变化→地位变化）。",
    "romance": "题材加权：每章推进关系位移，避免情绪原地打转。",
    "mystery": "题材加权：线索必须可回收，优先以规则冲突制造悬念。",
    "rules-mystery": "题材加权：规则先于解释，代价先于胜利。",
    "zhihu-short": "题材加权：压缩铺垫，优先反转与高强度结尾钩。",
    "substitute": "题材加权：强化误解-拉扯-决断链路，避免重复虐点。",
    "esports": "题材加权：每场对抗至少写清一个战术决策点与其后果。",
    "livestream": "题材加权：强化“外部反馈→主角反制→数据变化”即时闭环。",
    "cosmic-horror": "题材加权：恐怖来源于规则与代价，不依赖空泛惊悚形容。",
}


GENRE_METHOD_ANCHORS: dict[str, dict[str, str]] = {
    "xianxia": {
        "pressure_source": "资源争夺/境界压制",
        "release_target": "主角主动破局并拿到可见收益",
    },
    "urban-power": {
        "pressure_source": "阶层卡位/权力压制",
        "release_target": "主角通过资源博弈拿到地位与回报",
    },
    "romance": {
        "pressure_source": "关系误解/情感拉扯",
        "release_target": "关系位移落地并形成下一步承诺",
    },
    "mystery": {
        "pressure_source": "线索缺失/规则冲突",
        "release_target": "给出可验证的新线索并保留未知区",
    },
    "rules-mystery": {
        "pressure_source": "规则反噬/代价递增",
        "release_target": "用代价换突破并留下更高阶规则问题",
    },
    "zhihu-short": {
        "pressure_source": "信息落差/立场对撞",
        "release_target": "反转兑现并形成高强度尾钩",
    },
    "substitute": {
        "pressure_source": "身份误读/情绪对峙",
        "release_target": "误解链推进到明确决断",
    },
    "esports": {
        "pressure_source": "战术压制/节奏失衡",
        "release_target": "关键决策生效并转化为局势优势",
    },
    "livestream": {
        "pressure_source": "舆论波动/数据下滑",
        "release_target": "当场反制形成可见数据回弹",
    },
    "cosmic-horror": {
        "pressure_source": "认知失真/规则侵蚀",
        "release_target": "以明确代价换阶段性生存窗口",
    },
    "history-travel": {
        "pressure_source": "历史惯性/礼教阻力",
        "release_target": "知识优势兑现并引发新的连锁反应",
    },
    "game-lit": {
        "pressure_source": "系统规则限制/资源稀缺",
        "release_target": "数值突破并暴露更高层级威胁",
    },
}


def build_methodology_strategy_card(
    *,
    chapter: int,
    reader_signal: Dict[str, Any],
    genre_profile: Dict[str, Any],
    label: str = "digital-serial-v1",
) -> Dict[str, Any]:
    genre = str(genre_profile.get("genre") or "").strip()
    profile_key = to_profile_key(genre) or "general"

    hook_usage = reader_signal.get("hook_type_usage") or {}
    pattern_usage = reader_signal.get("pattern_usage") or {}
    review_trend = reader_signal.get("review_trend") or {}
    low_ranges = reader_signal.get("low_score_ranges") or []

    dominant_hook = ""
    if isinstance(hook_usage, dict) and hook_usage:
        dominant_hook = max(hook_usage.items(), key=lambda kv: kv[1])[0]

    dominant_pattern = ""
    if isinstance(pattern_usage, dict) and pattern_usage:
        dominant_pattern = max(pattern_usage.items(), key=lambda kv: kv[1])[0]

    overall_avg = float(review_trend.get("overall_avg") or 0.0)
    has_low_range = bool(low_ranges)
    hook_variety = len(hook_usage) if isinstance(hook_usage, dict) else 0
    pattern_variety = len(pattern_usage) if isinstance(pattern_usage, dict) else 0

    next_reason_clarity = 70.0 + (4.0 if has_low_range else 8.0)
    anchor_effectiveness = 68.0 + (6.0 if dominant_hook else 0.0) + (4.0 if overall_avg >= 75 else -4.0)
    rhythm_naturalness = 65.0 + min(10.0, float(hook_variety + pattern_variety) * 2.0)

    risk_flags: List[str] = []
    if has_low_range:
        risk_flags.append("low_score_recency")
    if dominant_pattern:
        risk_flags.append("pattern_overuse_watch")
    if overall_avg > 0 and overall_avg < 75:
        risk_flags.append("readability_guard")

    stage_mod = chapter % 5
    if stage_mod in {1, 2}:
        stage = "build_up"
    elif stage_mod in {3, 4}:
        stage = "confront"
    else:
        stage = "release"

    anchor_preset = GENRE_METHOD_ANCHORS.get(
        profile_key,
        {
            "pressure_source": "生存目标/资源竞争",
            "release_target": "主角完成阶段目标并留下新的行动理由",
        },
    )

    return {
        "enabled": True,
        "framework": label,
        "pilot": profile_key,
        "genre_profile_key": profile_key,
        "chapter_stage": stage,
        "emotion_anchor": {
            "pressure_source": anchor_preset["pressure_source"],
            "release_target": anchor_preset["release_target"],
            "position_hint": "前段设压，中后段释放，避免固定字位打点",
        },
        "long_arc_controls": {
            "map_transition": "阶段切换承接既有资产与关系账本，避免能力与收益归零",
            "power_guard": "关键胜利必须给机制理由（信息/资源/代价/策略）",
            "antagonist_model": "反派需具备目标-手段-代价三要素，避免工具人推进",
        },
        "serialization_ops": {
            "next_reason": "章末或后段给出可复述的下一章动机句",
            "interaction_note": "保留一个可讨论分歧点，便于连载互动反馈",
        },
        "observability": {
            "next_reason_clarity": round(max(0.0, min(100.0, next_reason_clarity)), 2),
            "anchor_effectiveness": round(max(0.0, min(100.0, anchor_effectiveness)), 2),
            "rhythm_naturalness": round(max(0.0, min(100.0, rhythm_naturalness)), 2),
        },
        "signals": {
            "dominant_hook": dominant_hook,
            "dominant_pattern": dominant_pattern,
            "risk_flags": risk_flags,
        },
    }


def build_methodology_guidance_items(strategy_card: Dict[str, Any]) -> List[str]:
    if not isinstance(strategy_card, dict) or not strategy_card.get("enabled"):
        return []

    observability = strategy_card.get("observability") or {}
    signals = strategy_card.get("signals") or {}
    risk_flags = list(signals.get("risk_flags") or [])
    stage = str(strategy_card.get("chapter_stage") or "build_up")
    genre_key = str(strategy_card.get("genre_profile_key") or strategy_card.get("pilot") or "general")

    stage_text = {
        "build_up": "本章以铺压为主，优先做威胁与代价的可感知铺垫。",
        "confront": "本章以正面对抗为主，确保破局路径清晰可复盘。",
        "release": "本章以释放与余波为主，给出实质收益并引出下一问。",
    }.get(stage, "本章保持压力-破局-余波的完整链路。")

    items = [
        f"方法论策略（通用/{genre_key}）：{stage_text}",
        "长线控制：换图承接旧资产，避免主角进入新地图后能力与资源归零。",
        "机制控制：关键胜利必须写出机制理由与代价，不用纯光环碾压。",
        (
            "连载互动：保留一个可讨论分歧点，强化下章追更动机。"
            f"（next_reason={observability.get('next_reason_clarity')}）"
        ),
    ]

    if "pattern_overuse_watch" in risk_flags:
        dominant_pattern = str(signals.get("dominant_pattern") or "").strip()
        if dominant_pattern:
            items.append(f"风险修正：近期“{dominant_pattern}”偏高频，本章补一个异质副轴避免疲劳。")
    if "readability_guard" in risk_flags:
        items.append("风险修正：近期审查均分偏低，本章优先保证段落动作-结果闭环与可读性。")

    return items


def build_guidance_items(
    *,
    chapter: int,
    reader_signal: Dict[str, Any],
    genre_profile: Dict[str, Any],
    low_score_threshold: float,
    hook_diversify_enabled: bool,
) -> Dict[str, Any]:
    guidance: List[str] = []

    low_ranges = reader_signal.get("low_score_ranges") or []
    if low_ranges:
        worst = min(
            low_ranges,
            key=lambda row: float(row.get("overall_score", 9999)),
        )
        guidance.append(
            f"第{chapter}章优先修复近期低分段问题：参考{worst.get('start_chapter')}-{worst.get('end_chapter')}章，强化冲突推进与结尾钩子。"
        )

    hook_usage = reader_signal.get("hook_type_usage") or {}
    if hook_usage and hook_diversify_enabled:
        dominant_hook = max(hook_usage.items(), key=lambda kv: kv[1])[0]
        guidance.append(
            f"近期钩子类型“{dominant_hook}”使用偏多，本章建议做钩子差异化，避免连续同构。"
        )

    pattern_usage = reader_signal.get("pattern_usage") or {}
    if pattern_usage:
        top_pattern = max(pattern_usage.items(), key=lambda kv: kv[1])[0]
        guidance.append(
            f"爽点模式“{top_pattern}”近期高频，本章可保留主爽点但叠加一个新爽点副轴。"
        )

    review_trend = reader_signal.get("review_trend") or {}
    overall_avg = review_trend.get("overall_avg")
    if isinstance(overall_avg, (int, float)) and float(overall_avg) < low_score_threshold:
        guidance.append(
            f"最近审查均分{overall_avg:.1f}低于阈值{low_score_threshold:.1f}，建议先保稳：减少跳场、每段补动作结果闭环。"
        )

    genre = str(genre_profile.get("genre") or "").strip()
    refs = genre_profile.get("reference_hints") or []
    if genre:
        guidance.append(f"题材锚定：按“{genre}”叙事主线推进，保持题材读者预期稳定兑现。")
    if refs:
        guidance.append(f"题材策略可执行提示：{refs[0]}")

    guidance.append("网文节奏基线：章首300字内给出目标与阻力，章末保留未闭合问题。")
    guidance.append("兑现密度基线：每600-900字给一次微兑现，并确保本章至少1处可量化变化。")

    normalized_genre = to_profile_key(genre)
    genre_hint = GENRE_GUIDANCE_TEXT.get(normalized_genre)
    if genre_hint:
        guidance.append(genre_hint)

    composite_hints = genre_profile.get("composite_hints") or []
    if composite_hints:
        guidance.append(f"复合题材协同：{composite_hints[0]}")

    if not guidance:
        guidance.append("本章执行默认高可读策略：冲突前置、信息后置、段末留钩。")

    return {
        "guidance": guidance,
        "low_ranges": low_ranges,
        "hook_usage": hook_usage,
        "pattern_usage": pattern_usage,
        "genre": genre,
    }


def build_writing_checklist(
    *,
    guidance_items: List[str],
    reader_signal: Dict[str, Any],
    genre_profile: Dict[str, Any],
    strategy_card: Dict[str, Any] | None = None,
    min_items: int,
    max_items: int,
    default_weight: float,
) -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []

    def _add_item(
        item_id: str,
        label: str,
        *,
        weight: float | None = None,
        required: bool = False,
        source: str = "writing_guidance",
        verify_hint: str = "",
    ) -> None:
        if len(items) >= max_items:
            return
        if any(row.get("id") == item_id for row in items):
            return

        item_weight = float(weight if weight is not None else default_weight)
        if item_weight <= 0:
            item_weight = default_weight

        items.append(
            {
                "id": item_id,
                "label": label,
                "weight": round(item_weight, 2),
                "required": bool(required),
                "source": source,
                "verify_hint": verify_hint,
            }
        )

    low_ranges = reader_signal.get("low_score_ranges") or []
    if low_ranges:
        worst = min(low_ranges, key=lambda row: float(row.get("overall_score", 9999)))
        span = f"{worst.get('start_chapter')}-{worst.get('end_chapter')}"
        _add_item(
            "fix_low_score_range",
            f"修复低分区间问题（参考第{span}章）",
            weight=max(default_weight, 1.4),
            required=True,
            source="reader_signal.low_score_ranges",
            verify_hint="至少完成1处冲突升级，并在段末留下钩子。",
        )

    hook_usage = reader_signal.get("hook_type_usage") or {}
    if hook_usage:
        dominant_hook = max(hook_usage.items(), key=lambda kv: kv[1])[0]
        _add_item(
            "hook_diversification",
            f"钩子差异化（避免继续单一“{dominant_hook}”）",
            weight=max(default_weight, 1.2),
            required=True,
            source="reader_signal.hook_type_usage",
            verify_hint="结尾钩子类型与近20章主类型至少有一处差异。",
        )

    pattern_usage = reader_signal.get("pattern_usage") or {}
    if pattern_usage:
        top_pattern = max(pattern_usage.items(), key=lambda kv: kv[1])[0]
        _add_item(
            "coolpoint_combo",
            f"主爽点+副爽点组合（主爽点：{top_pattern}）",
            weight=default_weight,
            required=False,
            source="reader_signal.pattern_usage",
            verify_hint="新增至少1个副爽点，并与主爽点形成因果链。",
        )

    review_trend = reader_signal.get("review_trend") or {}
    overall_avg = review_trend.get("overall_avg")
    if isinstance(overall_avg, (int, float)):
        _add_item(
            "readability_loop",
            "段落可读性闭环（动作→结果→情绪）",
            weight=max(default_weight, 1.1),
            required=True,
            source="reader_signal.review_trend",
            verify_hint="抽查3段，均包含动作结果闭环。",
        )

    genre = str(genre_profile.get("genre") or "").strip()
    if genre:
        _add_item(
            "genre_anchor_consistency",
            f"题材锚定一致性（{genre}）",
            weight=max(default_weight, 1.1),
            required=True,
            source="genre_profile.genre",
            verify_hint="主冲突与题材核心承诺保持一致。",
        )

    if isinstance(strategy_card, dict) and strategy_card.get("enabled"):
        _add_item(
            "methodology_next_reason",
            "方法论：下章动机需可复述（章末或后段均可）",
            weight=default_weight,
            required=False,
            source="methodology.next_reason",
            verify_hint="提炼一句“为什么要点下一章”的动机句。",
        )
        _add_item(
            "methodology_power_guard",
            "方法论：越级与破局给出机制理由与代价",
            weight=default_weight,
            required=False,
            source="methodology.power_guard",
            verify_hint="至少写清1个机制理由与1个代价。"
        )
        _add_item(
            "methodology_antagonist_pressure",
            "方法论：反派行动具备目标-手段-代价",
            weight=default_weight,
            required=False,
            source="methodology.antagonist",
            verify_hint="反派不是工具人推进，需有可解释行动逻辑。",
        )

    for idx, text in enumerate(guidance_items, start=1):
        if len(items) >= max_items:
            break
        label = str(text).strip()
        if not label:
            continue
        _add_item(
            f"guidance_item_{idx}",
            label,
            weight=default_weight,
            required=False,
            source="writing_guidance.guidance_items",
            verify_hint="完成后可在正文中定位对应段落。",
        )

    fallback_items = [
        (
            "opening_conflict",
            "开篇300字内给出冲突触发",
            "开头段出现明确目标与阻力。",
        ),
        (
            "scene_goal_block",
            "场景目标与阻力清晰",
            "每个场景至少有1个可验证目标。",
        ),
        (
            "ending_hook",
            "段末留钩并引出下一问",
            "结尾出现未解问题或下一步行动。",
        ),
    ]
    for item_id, label, verify_hint in fallback_items:
        if len(items) >= min_items or len(items) >= max_items:
            break
        _add_item(
            item_id,
            label,
            weight=default_weight,
            required=False,
            source="fallback",
            verify_hint=verify_hint,
        )

    return items[:max_items]


def is_checklist_item_completed(item: Dict[str, Any], reader_signal: Dict[str, Any]) -> bool:
    item_id = str(item.get("id") or "")
    if item_id in {"fix_low_score_range", "readability_loop"}:
        review_trend = reader_signal.get("review_trend") or {}
        overall = review_trend.get("overall_avg")
        return isinstance(overall, (int, float)) and float(overall) >= 75.0

    if item_id == "hook_diversification":
        hook_usage = reader_signal.get("hook_type_usage") or {}
        return len(hook_usage) >= 2

    if item_id == "coolpoint_combo":
        pattern_usage = reader_signal.get("pattern_usage") or {}
        return len(pattern_usage) >= 2

    if item_id == "genre_anchor_consistency":
        return True

    source = str(item.get("source") or "")
    if source.startswith("fallback"):
        return True

    if source.startswith("methodology."):
        # 方法论条目当前作为软提示，仅做观察与引导，不参与扣分。
        return True

    return False


logger = logging.getLogger(__name__)


# ── ContextRanker (merged from context_ranker.py) ──

class ContextRanker:
    """Rank context-pack sections with lightweight deterministic heuristics."""

    SUMMARY_HOOK_HINTS = ("?", "？", "悬念", "钩子", "反转", "冲突")

    def __init__(self, config=None):
        self.config = config or get_config()

    def rank_pack(self, pack: Dict[str, Any], chapter: int) -> Dict[str, Any]:
        ranked = dict(pack)

        core = dict(ranked.get("core") or {})
        core["recent_summaries"] = self.rank_recent_summaries(core.get("recent_summaries") or [], chapter)
        core["recent_meta"] = self.rank_recent_meta(core.get("recent_meta") or [], chapter)
        ranked["core"] = core

        scene = dict(ranked.get("scene") or {})
        scene["appearing_characters"] = self.rank_appearances(scene.get("appearing_characters") or [], chapter)
        ranked["scene"] = scene

        ranked["story_skeleton"] = self.rank_story_skeleton(ranked.get("story_skeleton") or [], chapter)

        alerts = dict(ranked.get("alerts") or {})
        alerts["disambiguation_warnings"] = self.rank_alerts(alerts.get("disambiguation_warnings") or [], chapter)
        alerts["disambiguation_pending"] = self.rank_alerts(alerts.get("disambiguation_pending") or [], chapter)
        ranked["alerts"] = alerts

        meta = dict(ranked.get("meta") or {})
        meta.setdefault("context_contract_version", "v2")
        meta["ranker"] = {
            "enabled": True,
            "recency_weight": float(self.config.context_ranker_recency_weight),
            "frequency_weight": float(self.config.context_ranker_frequency_weight),
            "hook_bonus": float(self.config.context_ranker_hook_bonus),
        }
        ranked["meta"] = meta
        return ranked

    def rank_recent_summaries(self, items: List[Dict[str, Any]], current_chapter: int) -> List[Dict[str, Any]]:
        scored = []
        for raw in items:
            item = dict(raw)
            chapter = self._as_int(item.get("chapter"))
            summary = str(item.get("summary") or "")

            recency = self._recency_score(chapter, current_chapter)
            frequency = self._length_score(summary)
            hook_bonus = float(self.config.context_ranker_hook_bonus) if self._has_hook_hint(summary) else 0.0
            score = self._combine_score(recency, frequency, hook_bonus)
            scored.append(self._with_debug_score(item, score, recency, frequency, hook_bonus))

        scored.sort(key=lambda row: row[0], reverse=True)
        return [row[1] for row in scored]

    def rank_recent_meta(self, items: List[Dict[str, Any]], current_chapter: int) -> List[Dict[str, Any]]:
        scored = []
        for raw in items:
            item = dict(raw)
            chapter = self._as_int(item.get("chapter"))
            hook = str(item.get("hook") or "")
            hook_bonus = float(self.config.context_ranker_hook_bonus) if hook else 0.0
            recency = self._recency_score(chapter, current_chapter)
            frequency = self._length_score(hook)
            score = self._combine_score(recency, frequency, hook_bonus)
            scored.append(self._with_debug_score(item, score, recency, frequency, hook_bonus))

        scored.sort(key=lambda row: row[0], reverse=True)
        return [row[1] for row in scored]

    def rank_appearances(self, items: List[Dict[str, Any]], current_chapter: int) -> List[Dict[str, Any]]:
        scored = []
        for raw in items:
            item = dict(raw)
            last_chapter = self._as_int(item.get("last_chapter") or item.get("chapter"))
            total = self._as_int(item.get("total")) or 0
            warning_penalty = 0.15 if item.get("warning") else 0.0

            recency = self._recency_score(last_chapter, current_chapter)
            frequency = self._frequency_score(total)
            score = self._combine_score(recency, frequency, 0.0) - warning_penalty
            scored.append(self._with_debug_score(item, score, recency, frequency, -warning_penalty))

        scored.sort(key=lambda row: row[0], reverse=True)
        return [row[1] for row in scored]

    def rank_story_skeleton(self, items: List[Dict[str, Any]], current_chapter: int) -> List[Dict[str, Any]]:
        scored = []
        for raw in items:
            item = dict(raw)
            chapter = self._as_int(item.get("chapter"))
            summary = str(item.get("summary") or "")
            recency = self._recency_score(chapter, current_chapter)
            frequency = self._length_score(summary)
            score = self._combine_score(recency, frequency, 0.0)
            scored.append(self._with_debug_score(item, score, recency, frequency, 0.0))

        scored.sort(key=lambda row: row[0], reverse=True)
        return [row[1] for row in scored]

    def rank_alerts(self, alerts: List[Any], current_chapter: int) -> List[Any]:
        scored = []
        keywords = tuple(self.config.context_ranker_alert_critical_keywords)

        for raw in alerts:
            if isinstance(raw, dict):
                item: Any = dict(raw)
                chapter = self._as_int(item.get("chapter"))
                text = str(item.get("message") or item.get("content") or json_safe(item))
                severity = str(item.get("severity") or "").lower()
                critical_bonus = 0.3 if severity in {"critical", "high"} else 0.0
            else:
                item = raw
                chapter = None
                text = str(raw)
                critical_bonus = 0.0

            recency = self._recency_score(chapter, current_chapter)
            keyword_bonus = 0.3 if any(word and word in text for word in keywords) else 0.0
            score = recency + critical_bonus + keyword_bonus

            if isinstance(item, dict):
                scored.append(self._with_debug_score(item, score, recency, critical_bonus, keyword_bonus))
            else:
                scored.append((score, item))

        scored.sort(key=lambda row: row[0], reverse=True)
        return [row[1] for row in scored]

    def _combine_score(self, recency: float, frequency: float, bonus: float) -> float:
        return (
            recency * float(self.config.context_ranker_recency_weight)
            + frequency * float(self.config.context_ranker_frequency_weight)
            + bonus
        )

    def _recency_score(self, source_chapter: Optional[int], current_chapter: int) -> float:
        if source_chapter is None:
            return 0.0
        gap = max(0, int(current_chapter) - int(source_chapter))
        return 1.0 / (1.0 + gap)

    def _frequency_score(self, total: int) -> float:
        if total <= 0:
            return 0.0
        return min(1.0, math.log(1.0 + float(total)) / math.log(11.0))

    def _length_score(self, text: str) -> float:
        if not text:
            return 0.0
        ratio = min(len(text) / 1200.0, 1.0)
        cap = float(self.config.context_ranker_length_bonus_cap)
        return ratio * cap

    def _has_hook_hint(self, text: str) -> bool:
        return any(token in text for token in self.SUMMARY_HOOK_HINTS)

    def _as_int(self, value: Any) -> Optional[int]:
        if value is None:
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    def _with_debug_score(
        self,
        item: Dict[str, Any],
        score: float,
        recency: float,
        frequency: float,
        bonus: float,
    ) -> tuple[float, Dict[str, Any]]:
        if getattr(self.config, "context_ranker_debug", False):
            item["_context_score"] = round(score, 6)
            item["_context_score_detail"] = {
                "recency": round(recency, 6),
                "frequency": round(frequency, 6),
                "bonus": round(bonus, 6),
            }
        return score, item


def json_safe(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False)
    except Exception:
        return str(value)


class ContextManager:
    DEFAULT_TEMPLATE = CONTEXT_DEFAULT_TEMPLATE
    TEMPLATE_WEIGHTS = CONTEXT_TEMPLATE_WEIGHTS
    TEMPLATE_WEIGHTS_DYNAMIC = CONTEXT_TEMPLATE_WEIGHTS_DYNAMIC_DEFAULT
    EXTRA_SECTIONS = {
        "story_skeleton",
        "memory",
        "long_term_memory",
        "preferences",
        "alerts",
        "reader_signal",
        "genre_profile",
        "writing_guidance",
        "plot_structure",
        "story_contract",
        "runtime_status",
        "latest_commit",
        "prewrite_validation",
    }
    SECTION_ORDER = [
        "core",
        "story_contract",
        "runtime_status",
        "latest_commit",
        "prewrite_validation",
        "scene",
        "global",
        "reader_signal",
        "genre_profile",
        "writing_guidance",
        "plot_structure",
        "story_skeleton",
        "memory",
        "long_term_memory",
        "preferences",
        "alerts",
    ]
    SUMMARY_SECTION_RE = re.compile(r"##\s*剧情摘要\s*\r?\n(.*?)(?=\r?\n##|\Z)", re.DOTALL)

    def __init__(self, config=None):
        self.config = config or get_config()
        self.index_manager = IndexManager(self.config)
        self.context_ranker = ContextRanker(self.config)

    def build_context(
        self,
        chapter: int,
        template: str | None = None,
        max_chars: Optional[int] = None,
    ) -> Dict[str, Any]:
        template = template or self.DEFAULT_TEMPLATE
        self._active_template = template
        if template not in self.TEMPLATE_WEIGHTS:
            template = self.DEFAULT_TEMPLATE
            self._active_template = template

        pack = self._build_pack(chapter)
        if getattr(self.config, "context_ranker_enabled", True):
            pack = self.context_ranker.rank_pack(pack, chapter)

        return self._assemble_json_payload(pack, template=template)

    def _assemble_json_payload(self, pack: Dict[str, Any], template: str = DEFAULT_TEMPLATE) -> Dict[str, Any]:
        chapter = int((pack.get("meta") or {}).get("chapter") or 0)
        weights = self._resolve_template_weights(template=template, chapter=chapter)

        payload: Dict[str, Any] = {
            "meta": {
                **(pack.get("meta") or {}),
                "context_contract_version": "v3",
            },
        }

        for section_name in self.SECTION_ORDER:
            if section_name in pack and section_name != "global":
                content = pack[section_name]
                weight = weights.get(section_name, 0.0)
                if weight > 0 or section_name in self.EXTRA_SECTIONS:
                    payload[section_name] = content

        if chapter > 0:
            payload["meta"]["context_weight_stage"] = self._resolve_context_stage(chapter)

        return payload

    def filter_invalid_items(self, items: List[Dict[str, Any]], source_type: str, id_key: str) -> List[Dict[str, Any]]:
        confirmed = self.index_manager.get_invalid_ids(source_type, status="confirmed")
        pending = self.index_manager.get_invalid_ids(source_type, status="pending")
        result = []
        for item in items:
            item_id = str(item.get(id_key, ""))
            if item_id in confirmed:
                continue
            if item_id in pending:
                item = dict(item)
                item["warning"] = "pending_invalid"
            result.append(item)
        return result

    def apply_confidence_filter(self, items: List[Dict[str, Any]], min_confidence: float) -> List[Dict[str, Any]]:
        filtered: List[Dict[str, Any]] = []
        for item in items:
            conf = item.get("confidence")
            if conf is None or conf >= min_confidence:
                filtered.append(item)
        return filtered

    def _build_pack(self, chapter: int) -> Dict[str, Any]:
        state = self._load_state()
        runtime_sources = load_runtime_sources(self.config.project_root, chapter)
        use_orchestrator = bool(getattr(self.config, "context_use_memory_orchestrator", False))

        orchestrator_pack: Dict[str, Any] = {}
        if use_orchestrator:
            try:
                from .memory.orchestrator import MemoryOrchestrator

                orchestrator = MemoryOrchestrator(self.config)
                orchestrator_pack = orchestrator.build_memory_pack(chapter)
            except Exception as exc:
                logger.warning("memory_orchestrator_failed: %s", exc)

        core = {
            "chapter_outline": self._load_outline(chapter),
            "protagonist_snapshot": state.get("protagonist_state", {}),
            "recent_summaries": self._load_recent_summaries(
                chapter,
                window=self.config.context_recent_summaries_window,
            ),
            "recent_meta": self._load_recent_meta(
                state,
                chapter,
                window=self.config.context_recent_meta_window,
            ),
        }
        if use_orchestrator and orchestrator_pack:
            working_items = list(orchestrator_pack.get("working_memory") or [])
            outline_item = next((x for x in working_items if x.get("source") == "outline"), None)
            state_item = next((x for x in working_items if x.get("source") == "state_export"), None)
            summary_items = [
                {"chapter": x.get("chapter"), "summary": x.get("content")}
                for x in working_items
                if x.get("source") == "summary"
            ]
            core["chapter_outline"] = str(outline_item.get("content", "")) if outline_item else core["chapter_outline"]
            if isinstance(state_item, dict) and isinstance(state_item.get("content"), dict):
                state_export = dict(state_item.get("content") or {})
                core["protagonist_snapshot"] = state_export.get("protagonist_state", core["protagonist_snapshot"])
            if summary_items:
                core["recent_summaries"] = summary_items

        scene = {
            "location_context": state.get("protagonist_state", {}).get("location", {}),
            "appearing_characters": self._load_recent_appearances(
                limit=self.config.context_max_appearing_characters,
            ),
        }
        scene["appearing_characters"] = self.filter_invalid_items(
            scene["appearing_characters"], source_type="entity", id_key="entity_id"
        )
        story_contract = self._build_story_contract_from_runtime(runtime_sources)
        runtime_status = runtime_sources.to_dict()
        latest_commit = runtime_sources.latest_commit or {}

        global_ctx = {
            "worldview_skeleton": self._load_setting("世界观"),
            "power_system_skeleton": self._load_setting("力量体系"),
            "style_contract_ref": self._load_setting("风格契约"),
        }

        preferences = self._load_json_optional(self.config.ainovel_dir / "preferences.json")
        memory = self._load_json_optional(self.config.ainovel_dir / "project_memory.json")
        long_term_memory: Dict[str, Any] = orchestrator_pack if orchestrator_pack else {}
        story_skeleton = self._load_story_skeleton(chapter)
        alert_slice = max(0, int(self.config.context_alerts_slice))
        reader_signal = self._load_reader_signal(chapter)
        genre_profile = self._build_runtime_genre_profile(state, story_contract)
        writing_guidance = self._build_writing_guidance(chapter, reader_signal, genre_profile)
        plot_structure = self._load_plot_structure(chapter)
        prewrite_validation = PrewriteValidator(self.config.project_root).build(
            chapter=chapter,
            review_contract=story_contract.get("review_contract") or {},
            plot_structure=plot_structure,
            story_contract=story_contract,
        )

        return {
            "meta": {"chapter": chapter},
            "core": core,
            "story_contract": story_contract,
            "runtime_status": runtime_status,
            "latest_commit": latest_commit,
            "prewrite_validation": prewrite_validation,
            "scene": scene,
            "global": global_ctx,
            "reader_signal": reader_signal,
            "genre_profile": genre_profile,
            "writing_guidance": writing_guidance,
            "plot_structure": plot_structure,
            "story_skeleton": story_skeleton,
            "preferences": preferences,
            "memory": memory,
            "long_term_memory": long_term_memory,
            "alerts": {
                "disambiguation_warnings": (
                    state.get("disambiguation_warnings", [])[-alert_slice:] if alert_slice else []
                ),
                "disambiguation_pending": (
                    state.get("disambiguation_pending", [])[-alert_slice:] if alert_slice else []
                ),
            },
        }

    def _load_reader_signal(self, chapter: int) -> Dict[str, Any]:
        if not getattr(self.config, "context_reader_signal_enabled", True):
            return {}

        recent_limit = max(1, int(getattr(self.config, "context_reader_signal_recent_limit", 5)))
        pattern_window = max(1, int(getattr(self.config, "context_reader_signal_window_chapters", 20)))
        review_window = max(1, int(getattr(self.config, "context_reader_signal_review_window", 5)))
        include_debt = bool(getattr(self.config, "context_reader_signal_include_debt", False))

        recent_power = self.index_manager.get_recent_reading_power(limit=recent_limit)
        pattern_stats = self.index_manager.get_pattern_usage_stats(last_n_chapters=pattern_window)
        hook_stats = self.index_manager.get_hook_type_stats(last_n_chapters=pattern_window)
        review_trend = self.index_manager.get_review_trend_stats(last_n=review_window)

        low_score_ranges: List[Dict[str, Any]] = []
        for row in review_trend.get("recent_ranges", []):
            score = row.get("overall_score")
            notes = row.get("notes", "")
            has_blocking = "blocking=" in notes and "blocking=0" not in notes
            is_low_score = isinstance(score, (int, float)) and float(score) < 75
            if is_low_score or has_blocking:
                low_score_ranges.append(
                    {
                        "start_chapter": row.get("start_chapter"),
                        "end_chapter": row.get("end_chapter"),
                        "overall_score": score if isinstance(score, (int, float)) else 0.0,
                        "notes": notes,
                    }
                )

        signal: Dict[str, Any] = {
            "recent_reading_power": recent_power,
            "pattern_usage": pattern_stats,
            "hook_type_usage": hook_stats,
            "review_trend": review_trend,
            "low_score_ranges": low_score_ranges,
            "next_chapter": chapter,
        }

        if include_debt:
            signal["debt_summary"] = self.index_manager.get_debt_summary()

        return signal

    def _load_genre_profile(self, state: Dict[str, Any]) -> Dict[str, Any]:
        if not getattr(self.config, "context_genre_profile_enabled", True):
            return {}

        fallback = str(getattr(self.config, "context_genre_profile_fallback", "shuangwen") or "shuangwen")
        project = state.get("project") or {}
        project_info = state.get("project_info") or {}
        genre_raw = str(project.get("genre") or project_info.get("genre") or fallback)
        genres = self._parse_genre_tokens(genre_raw)
        if not genres:
            genres = [fallback]
        max_genres = max(1, int(getattr(self.config, "context_genre_profile_max_genres", 2)))
        genres = genres[:max_genres]

        primary_genre = genres[0]
        secondary_genres = genres[1:]
        composite = len(genres) > 1
        profile_path = self.config.project_root / ".claude" / "references" / "genre-profiles.md"
        taxonomy_path = self.config.project_root / ".claude" / "references" / "reading-power-taxonomy.md"

        profile_text = profile_path.read_text(encoding="utf-8") if profile_path.exists() else ""
        taxonomy_text = taxonomy_path.read_text(encoding="utf-8") if taxonomy_path.exists() else ""

        profile_excerpt = self._extract_genre_section(profile_text, primary_genre)
        taxonomy_excerpt = self._extract_genre_section(taxonomy_text, primary_genre)

        secondary_profiles: List[str] = []
        secondary_taxonomies: List[str] = []
        for extra in secondary_genres:
            secondary_profiles.append(self._extract_genre_section(profile_text, extra))
            secondary_taxonomies.append(self._extract_genre_section(taxonomy_text, extra))

        refs = self._extract_markdown_refs(
            "\n".join([profile_excerpt] + secondary_profiles),
            max_items=int(getattr(self.config, "context_genre_profile_max_refs", 8)),
        )

        composite_hints = self._build_composite_genre_hints(genres, refs)

        return {
            "genre": primary_genre,
            "genre_raw": genre_raw,
            "genres": genres,
            "composite": composite,
            "secondary_genres": secondary_genres,
            "profile_excerpt": profile_excerpt,
            "taxonomy_excerpt": taxonomy_excerpt,
            "secondary_profile_excerpts": secondary_profiles,
            "secondary_taxonomy_excerpts": secondary_taxonomies,
            "reference_hints": refs,
            "composite_hints": composite_hints,
        }

    def _build_runtime_genre_profile(
        self,
        state: Dict[str, Any],
        story_contract: Dict[str, Any],
    ) -> Dict[str, Any]:
        legacy_profile = self._load_genre_profile(state)
        if legacy_profile:
            legacy_profile = dict(legacy_profile)
            legacy_profile["mode"] = "fallback_only"

        primary_genre = str(
            (
                ((story_contract.get("master_setting") or {}).get("route") or {}).get("primary_genre")
                or ""
            )
        ).strip()
        if not primary_genre:
            return legacy_profile or {}

        runtime_profile = self._load_genre_profile({"project": {"genre": primary_genre}})
        runtime_profile = dict(runtime_profile or {})
        runtime_profile.setdefault("genre", primary_genre)
        runtime_profile.setdefault("genre_raw", primary_genre)
        runtime_profile.setdefault("genres", [primary_genre])
        runtime_profile.setdefault("secondary_genres", [])
        runtime_profile.setdefault("composite", len(runtime_profile.get("genres") or []) > 1)
        runtime_profile.setdefault("reference_hints", [])
        runtime_profile.setdefault("composite_hints", [])
        runtime_profile["mode"] = "contract_first"

        if legacy_profile:
            runtime_profile["legacy_genre"] = legacy_profile.get("genre")
            runtime_profile["legacy_genre_raw"] = legacy_profile.get("genre_raw")
            runtime_profile["legacy_genres"] = list(legacy_profile.get("genres") or [])

        return runtime_profile

    def _build_writing_guidance(
        self,
        chapter: int,
        reader_signal: Dict[str, Any],
        genre_profile: Dict[str, Any],
    ) -> Dict[str, Any]:
        if not getattr(self.config, "context_writing_guidance_enabled", True):
            return {}

        limit = max(1, int(getattr(self.config, "context_writing_guidance_max_items", 6)))
        low_score_threshold = float(
            getattr(self.config, "context_writing_guidance_low_score_threshold", 75.0)
        )

        guidance_bundle = build_guidance_items(
            chapter=chapter,
            reader_signal=reader_signal,
            genre_profile=genre_profile,
            low_score_threshold=low_score_threshold,
            hook_diversify_enabled=bool(
                getattr(self.config, "context_writing_guidance_hook_diversify", True)
            ),
        )

        guidance = list(guidance_bundle.get("guidance") or [])
        methodology_strategy: Dict[str, Any] = {}

        if self._is_methodology_enabled_for_genre(genre_profile):
            methodology_strategy = build_methodology_strategy_card(
                chapter=chapter,
                reader_signal=reader_signal,
                genre_profile=genre_profile,
                label=str(getattr(self.config, "context_methodology_label", "digital-serial-v1")),
            )
            guidance.extend(build_methodology_guidance_items(methodology_strategy))

        checklist = self._build_writing_checklist(
            chapter=chapter,
            guidance_items=guidance,
            reader_signal=reader_signal,
            genre_profile=genre_profile,
            strategy_card=methodology_strategy,
        )

        checklist_score = self._compute_writing_checklist_score(
            chapter=chapter,
            checklist=checklist,
            reader_signal=reader_signal,
        )

        if getattr(self.config, "context_writing_score_persist_enabled", True):
            self._persist_writing_checklist_score(checklist_score)

        low_ranges = guidance_bundle.get("low_ranges") or []
        hook_usage = guidance_bundle.get("hook_usage") or {}
        pattern_usage = guidance_bundle.get("pattern_usage") or {}
        genre = str(guidance_bundle.get("genre") or genre_profile.get("genre") or "").strip()

        hook_types = list(hook_usage.keys())[:3] if isinstance(hook_usage, dict) else []
        top_patterns = (
            sorted(pattern_usage, key=pattern_usage.get, reverse=True)[:3]
            if isinstance(pattern_usage, dict)
            else []
        )

        return {
            "chapter": chapter,
            "guidance_items": guidance[:limit],
            "checklist": checklist,
            "checklist_score": checklist_score,
            "methodology": methodology_strategy,
            "signals_used": {
                "has_low_score_ranges": bool(low_ranges),
                "hook_types": hook_types,
                "top_patterns": top_patterns,
                "genre": genre,
                "methodology_enabled": bool(methodology_strategy.get("enabled")),
            },
        }

    def _compute_writing_checklist_score(
        self,
        chapter: int,
        checklist: List[Dict[str, Any]],
        reader_signal: Dict[str, Any],
    ) -> Dict[str, Any]:
        total_items = len(checklist)
        required_items = 0
        completed_items = 0
        completed_required = 0
        total_weight = 0.0
        completed_weight = 0.0
        pending_labels: List[str] = []

        for item in checklist:
            if not isinstance(item, dict):
                continue
            required = bool(item.get("required"))
            weight = float(item.get("weight") or 1.0)
            total_weight += weight
            if required:
                required_items += 1

            completed = self._is_checklist_item_completed(item, reader_signal)
            if completed:
                completed_items += 1
                completed_weight += weight
                if required:
                    completed_required += 1
            else:
                pending_labels.append(str(item.get("label") or item.get("id") or "未命名项"))

        completion_rate = (completed_items / total_items) if total_items > 0 else 1.0
        weighted_rate = (completed_weight / total_weight) if total_weight > 0 else completion_rate
        required_rate = (completed_required / required_items) if required_items > 0 else 1.0

        score = 100.0 * (0.5 * weighted_rate + 0.3 * required_rate + 0.2 * completion_rate)

        if getattr(self.config, "context_writing_score_include_reader_trend", True):
            trend_window = max(1, int(getattr(self.config, "context_writing_score_trend_window", 10)))
            trend = self.index_manager.get_writing_checklist_score_trend(last_n=trend_window)
            baseline = float(trend.get("score_avg") or 0.0)
            if baseline > 0:
                score += max(-10.0, min(10.0, (score - baseline) * 0.1))

        score = round(max(0.0, min(100.0, score)), 2)

        return {
            "chapter": chapter,
            "score": score,
            "completion_rate": round(completion_rate, 4),
            "weighted_completion_rate": round(weighted_rate, 4),
            "required_completion_rate": round(required_rate, 4),
            "total_items": total_items,
            "required_items": required_items,
            "completed_items": completed_items,
            "completed_required": completed_required,
            "total_weight": round(total_weight, 2),
            "completed_weight": round(completed_weight, 2),
            "pending_items": pending_labels,
            "trend_window": int(getattr(self.config, "context_writing_score_trend_window", 10)),
        }

    def _is_checklist_item_completed(self, item: Dict[str, Any], reader_signal: Dict[str, Any]) -> bool:
        return is_checklist_item_completed(item, reader_signal)

    def _persist_writing_checklist_score(self, checklist_score: Dict[str, Any]) -> None:
        if not checklist_score:
            return
        try:
            self.index_manager.save_writing_checklist_score(
                WritingChecklistScoreMeta(
                    chapter=int(checklist_score.get("chapter") or 0),
                    template=str(getattr(self, "_active_template", self.DEFAULT_TEMPLATE) or self.DEFAULT_TEMPLATE),
                    total_items=int(checklist_score.get("total_items") or 0),
                    required_items=int(checklist_score.get("required_items") or 0),
                    completed_items=int(checklist_score.get("completed_items") or 0),
                    completed_required=int(checklist_score.get("completed_required") or 0),
                    total_weight=float(checklist_score.get("total_weight") or 0.0),
                    completed_weight=float(checklist_score.get("completed_weight") or 0.0),
                    completion_rate=float(checklist_score.get("completion_rate") or 0.0),
                    score=float(checklist_score.get("score") or 0.0),
                    score_breakdown={
                        "weighted_completion_rate": checklist_score.get("weighted_completion_rate"),
                        "required_completion_rate": checklist_score.get("required_completion_rate"),
                        "trend_window": checklist_score.get("trend_window"),
                    },
                    pending_items=list(checklist_score.get("pending_items") or []),
                    source="context_manager",
                )
            )
        except Exception as exc:
            logger.warning("failed to persist writing checklist score: %s", exc)

    def _resolve_context_stage(self, chapter: int) -> str:
        early = max(1, int(getattr(self.config, "context_dynamic_budget_early_chapter", 30)))
        late = max(early + 1, int(getattr(self.config, "context_dynamic_budget_late_chapter", 120)))
        if chapter <= early:
            return "early"
        if chapter >= late:
            return "late"
        return "mid"

    def _resolve_template_weights(self, template: str, chapter: int) -> Dict[str, float]:
        template_key = template if template in self.TEMPLATE_WEIGHTS else self.DEFAULT_TEMPLATE
        base = dict(self.TEMPLATE_WEIGHTS.get(template_key, self.TEMPLATE_WEIGHTS[self.DEFAULT_TEMPLATE]))
        if not getattr(self.config, "context_dynamic_budget_enabled", True):
            return base

        stage = self._resolve_context_stage(chapter)
        dynamic_weights = getattr(self.config, "context_template_weights_dynamic", None)
        if not isinstance(dynamic_weights, dict):
            dynamic_weights = self.TEMPLATE_WEIGHTS_DYNAMIC

        stage_weights = dynamic_weights.get(stage, {}) if isinstance(dynamic_weights.get(stage, {}), dict) else {}
        staged = stage_weights.get(template_key)
        if isinstance(staged, dict):
            return dict(staged)

        return base

    def _parse_genre_tokens(self, genre_raw: str) -> List[str]:
        support_composite = bool(getattr(self.config, "context_genre_profile_support_composite", True))
        separators_raw = getattr(self.config, "context_genre_profile_separators", ("+", "/", "|", ","))
        separators = tuple(str(token) for token in separators_raw if str(token))
        return parse_genre_tokens(
            genre_raw,
            support_composite=support_composite,
            separators=separators,
        )

    def _normalize_genre_token(self, token: str) -> str:
        return normalize_genre_token(token)

    def _build_composite_genre_hints(self, genres: List[str], refs: List[str]) -> List[str]:
        return build_composite_genre_hints(genres, refs)

    def _build_writing_checklist(
        self,
        chapter: int,
        guidance_items: List[str],
        reader_signal: Dict[str, Any],
        genre_profile: Dict[str, Any],
        strategy_card: Dict[str, Any] | None = None,
    ) -> List[Dict[str, Any]]:
        _ = chapter
        if not getattr(self.config, "context_writing_checklist_enabled", True):
            return []

        min_items = max(1, int(getattr(self.config, "context_writing_checklist_min_items", 3)))
        max_items = max(min_items, int(getattr(self.config, "context_writing_checklist_max_items", 6)))
        default_weight = float(getattr(self.config, "context_writing_checklist_default_weight", 1.0))
        if default_weight <= 0:
            default_weight = 1.0

        return build_writing_checklist(
            guidance_items=guidance_items,
            reader_signal=reader_signal,
            genre_profile=genre_profile,
            strategy_card=strategy_card,
            min_items=min_items,
            max_items=max_items,
            default_weight=default_weight,
        )

    def _is_methodology_enabled_for_genre(self, genre_profile: Dict[str, Any]) -> bool:
        if not bool(getattr(self.config, "context_methodology_enabled", False)):
            return False

        whitelist_raw = getattr(self.config, "context_methodology_genre_whitelist", ("*",))
        if isinstance(whitelist_raw, str):
            whitelist_iter = [whitelist_raw]
        else:
            whitelist_iter = list(whitelist_raw or [])

        whitelist = {str(token).strip().lower() for token in whitelist_iter if str(token).strip()}
        if not whitelist:
            return True
        if "*" in whitelist or "all" in whitelist:
            return True

        genre = str((genre_profile or {}).get("genre") or "").strip()
        if not genre:
            return False

        profile_key = to_profile_key(genre)
        return profile_key in whitelist

    def _extract_genre_section(self, text: str, genre: str) -> str:
        return extract_genre_section(text, genre)

    def _extract_markdown_refs(self, text: str, max_items: int = 8) -> List[str]:
        return extract_markdown_refs(text, max_items=max_items)

    def _load_state(self) -> Dict[str, Any]:
        path = self.config.state_file
        if not path.exists():
            return {}
        return json.loads(path.read_text(encoding="utf-8"))

    def _load_outline(self, chapter: int) -> str:
        return load_chapter_outline(self.config.project_root, chapter, max_chars=1500)

    def _load_plot_structure(self, chapter: int) -> Dict[str, Any]:
        return load_chapter_plot_structure(self.config.project_root, chapter)

    def _build_story_contract_from_runtime(self, runtime_sources: RuntimeSourceSnapshot) -> Dict[str, Any]:
        story_root = self.config.story_system_dir
        return {
            "master_setting": runtime_sources.contracts.get("master") or {},
            "chapter_brief": runtime_sources.contracts.get("chapter") or {},
            "volume_brief": runtime_sources.contracts.get("volume") or {},
            "review_contract": runtime_sources.contracts.get("review") or {},
            "anti_patterns": read_json_if_exists(story_root / "anti_patterns.json") or [],
        }

    def _load_recent_summaries(self, chapter: int, window: int = 3) -> List[Dict[str, Any]]:
        summaries = []
        for ch in range(max(1, chapter - window), chapter):
            summary = self._load_summary_text(ch)
            if summary:
                summaries.append(summary)
        return summaries

    def _load_recent_meta(self, state: Dict[str, Any], chapter: int, window: int = 3) -> List[Dict[str, Any]]:
        meta = state.get("chapter_meta", {}) or {}
        results = []
        for ch in range(max(1, chapter - window), chapter):
            for key in (f"{ch:04d}", str(ch)):
                if key in meta:
                    results.append({"chapter": ch, **meta.get(key, {})})
                    break
        return results

    def _load_recent_appearances(self, limit: Optional[int] = None) -> List[Dict[str, Any]]:
        appearances = self.index_manager.get_recent_appearances(limit=limit)
        return appearances or []

    def _load_setting(self, keyword: str) -> str:
        settings_dir = self.config.settings_dir
        candidates = [
            settings_dir / f"{keyword}.md",
        ]
        for path in candidates:
            if path.exists():
                return path.read_text(encoding="utf-8")
        # fallback: any file containing keyword
        matches = list(settings_dir.glob(f"*{keyword}*.md"))
        if matches:
            return matches[0].read_text(encoding="utf-8")
        return f"[{keyword}设定未找到]"

    def _extract_summary_excerpt(self, text: str, max_chars: int) -> str:
        if not text:
            return ""
        match = self.SUMMARY_SECTION_RE.search(text)
        excerpt = match.group(1).strip() if match else text.strip()
        if max_chars > 0 and len(excerpt) > max_chars:
            return excerpt[:max_chars].rstrip()
        return excerpt

    def _load_summary_text(self, chapter: int, snippet_chars: Optional[int] = None) -> Optional[Dict[str, Any]]:
        summary_path = self.config.ainovel_dir / "summaries" / f"ch{chapter:04d}.md"
        if not summary_path.exists():
            return None
        text = summary_path.read_text(encoding="utf-8")
        if snippet_chars:
            summary_text = self._extract_summary_excerpt(text, snippet_chars)
        else:
            summary_text = text
        return {"chapter": chapter, "summary": summary_text}

    def _load_story_skeleton(self, chapter: int) -> List[Dict[str, Any]]:
        interval = max(1, int(self.config.context_story_skeleton_interval))
        max_samples = max(0, int(self.config.context_story_skeleton_max_samples))
        snippet_chars = int(self.config.context_story_skeleton_snippet_chars)

        if max_samples <= 0 or chapter <= interval:
            return []

        samples: List[Dict[str, Any]] = []
        cursor = chapter - interval
        while cursor >= 1 and len(samples) < max_samples:
            summary = self._load_summary_text(cursor, snippet_chars=snippet_chars)
            if summary and summary.get("summary"):
                samples.append(summary)
            cursor -= interval

        samples.reverse()
        return samples

    def _load_json_optional(self, path: Path) -> Dict[str, Any]:
        if not path.exists():
            return {}
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {}


def main():
    import argparse
    from .cli_output import print_success, print_error

    parser = argparse.ArgumentParser(description="Context Manager CLI")
    parser.add_argument("--project-root", type=str, help="项目根目录")
    parser.add_argument("--chapter", type=int, required=True)
    parser.add_argument("--template", type=str, default=ContextManager.DEFAULT_TEMPLATE)

    args = parser.parse_args()

    config = None
    if args.project_root:
        # 允许传入"工作区根目录"，统一解析到真正的 book project_root（必须包含 .ainovel/state.json）
        from project_locator import resolve_project_root
        from .config import DataModulesConfig

        resolved_root = resolve_project_root(args.project_root)
        config = DataModulesConfig.from_project_root(resolved_root)

    manager = ContextManager(config)
    try:
        payload = manager.build_context(
            chapter=args.chapter,
            template=args.template,
        )
        print_success(payload, message="context_built")
        try:
            manager.index_manager.log_tool_call("context_manager:build", True, chapter=args.chapter)
        except Exception as exc:
            logger.warning("failed to log successful tool call: %s", exc)
    except Exception as exc:
        print_error("CONTEXT_BUILD_FAILED", str(exc), suggestion="请检查项目结构与依赖文件")
        try:
            manager.index_manager.log_tool_call(
                "context_manager:build", False, error_code="CONTEXT_BUILD_FAILED", error_message=str(exc), chapter=args.chapter
            )
        except Exception as log_exc:
            logger.warning("failed to log failed tool call: %s", log_exc)


if __name__ == "__main__":
    import sys
    if sys.platform == "win32":
        enable_windows_utf8_stdio()
    main()
