"""失败学习机制：轮内账本 + 跨run知识库 + 强制类型先验。"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import SETTINGS

# ---------------------------------------------------------------------------
# 预定义类型与风格先验（用户强制分类，读取文章前选定）
# ---------------------------------------------------------------------------

_CATEGORIES: dict[str, dict[str, str]] = {
    "玄幻武侠": {
        "description": "以武功、修行、江湖、朝堂、异能为背景的东方幻想/武侠连载小说片段。",
        "rhythm": "动作场面用短促动宾句推进；铺陈可用中长句；无功能的环境描写要少；留白比写满重要。",
        "dialogue": "江湖/市井气息，角色辨识度高；对白占比15-30%；禁用书面化套话和说明式对白。",
        "pov": "第三人称限知为主，紧贴主角动作与感知，不随意跳全知。",
        "common_pitfalls": "招式名堆砌（一剑/刀光/剑气三板斧）、环境意象轰炸（月光/烛影/残阳滥用）、内心独白标签化（心中一凛/杀意凛然）、解释武学原理。",
        "key_points": "动作一句一意图；用动词和具体细节制造画面；不点破悬念、不总结收束。",
    },
    "都市日常": {
        "description": "现代都市背景下的日常、职场、恋爱、生活流小说片段。",
        "rhythm": "口语化短句，贴近说话节奏；叙事以事件推进为主，少抒情和文艺腔长段。",
        "dialogue": "生活化、有潜台词、角色说话带阶层/性格；对白占比20-40%。",
        "pov": "第一人称或第三人称限知，细节真实可感，不悬浮。",
        "common_pitfalls": "书面腔（不禁/颇为/心中涌起）、情绪标签化、过度解释心理、文艺化空洞环境描写、讲道理总结。",
        "key_points": "细节具体（物品/动作/地点/价格）；对白驱动；少形容词堆叠；情绪通过动作/对白呈现。",
    },
}

KNOWN_CATEGORIES = set(_CATEGORIES.keys())


def get_category_definition(category: str) -> dict[str, str] | None:
    return _CATEGORIES.get(category)


def build_category_prior(category: str) -> str:
    """生成类型风格先验文本，注入冷启动/refiner。"""
    cat = category.strip() if isinstance(category, str) else ""
    if not cat or cat not in _CATEGORIES:
        return ""
    d = _CATEGORIES[cat]
    return (
        f"## 类型风格先验：{cat}\n"
        f"{d['description']}\n\n"
        f"- **节奏**：{d['rhythm']}\n"
        f"- **对白**：{d['dialogue']}\n"
        f"- **视角**：{d['pov']}\n"
        f"- **该类常见AI味坑**：{d['common_pitfalls']}\n"
        f"- **风格要点**：{d['key_points']}\n"
    )


# ---------------------------------------------------------------------------
# 轮内失败账本
# ---------------------------------------------------------------------------


def extract_failure_patterns(entry: dict[str, Any]) -> list[str]:
    """从单轮评估结果中启发式抽取失败模式。"""
    patterns: list[str] = []
    if not isinstance(entry, dict):
        return patterns

    plot = entry.get("plot_fidelity_scores") or {}
    if not isinstance(plot, dict):
        plot = {}
    style = entry.get("style_scores") or {}
    if not isinstance(style, dict):
        style = {}
    struct = entry.get("structure_scores") or {}
    if not isinstance(struct, dict):
        struct = {}
    gen = entry.get("generation") or ""

    # plot_fidelity
    fab = str(plot.get("fabricated_notes") or "").strip()
    if fab and fab != "无":
        patterns.append("编造节拍外情报")
    if (plot.get("outcome_fidelity") or 0.0) < 0.4:
        patterns.append("超前解谜或改变事件结果")
    if (plot.get("beat_coverage") or 0.0) < 0.5:
        patterns.append("节拍覆盖不全")

    # style
    if (style.get("L3_sentence") or 0.0) < 0.5 and "——" in gen:
        patterns.append("破折号出现")
    if (style.get("L5_dialogue") or 0.0) < 0.5:
        dr = entry.get("dialogue_ratio") or {}
        if isinstance(dr, dict) and dr.get("dialogue_ratio", 100.0) < 5.0:
            patterns.append("对白缺失")
    if (style.get("L1_style_match") or 0.0) < 0.5:
        patterns.append("风格匹配度低")

    # structure
    if (struct.get("anti_ai_smell") or 0.0) < 0.5:
        patterns.append("AI味重")
    if (struct.get("tension") or 0.0) < 0.5:
        patterns.append("张力不足")

    return patterns


@dataclass
class FailureLedger:
    """一次训练 run 内的失败账本。"""

    edit_history: list[dict[str, Any]] = field(default_factory=list)
    failure_pattern_counts: dict[str, int] = field(default_factory=dict)

    def record_round(
        self,
        round_idx: int,
        change_summary: str,
        score_before: float | None,
        score_after: float,
        entry: dict[str, Any],
    ) -> None:
        delta = None
        if score_before is not None:
            delta = round(score_after - score_before, 4)
        accepted = (delta is not None and delta > 0.0)
        self.edit_history.append(
            {
                "round": round_idx,
                "change_summary": change_summary or "（未说明改动）",
                "score_before": score_before,
                "score_after": score_after,
                "delta": delta,
                "accepted": accepted,
            }
        )
        for pattern in extract_failure_patterns(entry):
            self.failure_pattern_counts[pattern] = self.failure_pattern_counts.get(pattern, 0) + 1

    def effective_edits(self, min_delta: float = 0.05) -> list[str]:
        return [
            f"{e['change_summary']} -> {e['delta']:+.3f}"
            for e in self.edit_history
            if e["delta"] is not None and e["delta"] >= min_delta
        ]

    def failed_edits(self, max_delta: float = -0.05) -> list[str]:
        return [
            f"{e['change_summary']} -> {e['delta']:+.3f}"
            for e in self.edit_history
            if e["delta"] is not None and e["delta"] <= max_delta
        ]

    def recurring_patterns(self, min_count: int = 2) -> list[tuple[str, int]]:
        return sorted(
            [(p, c) for p, c in self.failure_pattern_counts.items() if c >= min_count],
            key=lambda x: -x[1],
        )

    def summary(self) -> dict[str, Any]:
        return {
            "effective_edits": self.effective_edits(),
            "failed_edits": self.failed_edits(),
            "recurring_patterns": [
                f"{p}（{c}轮）" for p, c in self.recurring_patterns()
            ],
            "total_rounds_recorded": len(self.edit_history),
        }


# ---------------------------------------------------------------------------
# 跨run知识库（按强制类型分桶）
# ---------------------------------------------------------------------------


class LessonKB:
    """持久化的失败教训知识库，按预定义类型分桶。"""

    MAX_PER_CATEGORY = 20

    def __init__(self, data_dir: Path | None = None) -> None:
        self.data_dir = data_dir or SETTINGS.data_dir
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.kb_path = self.data_dir / "failure_lessons.json"
        self._kb: dict[str, dict[str, Any]] = {"version": 1, "categories": {}}
        self._load()

    def _load(self) -> None:
        if self.kb_path.is_file():
            try:
                with self.kb_path.open("r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, dict) and "categories" in data:
                    self._kb = data
                else:
                    # 兼容旧格式（如果以后变成 dict-of-lessons）
                    self._kb = {"version": 1, "categories": {"通用": {"lessons": []}}}
            except (json.JSONDecodeError, OSError):
                self._kb = {"version": 1, "categories": {}}

    def save(self) -> None:
        try:
            with self.kb_path.open("w", encoding="utf-8") as f:
                json.dump(self._kb, f, ensure_ascii=False, indent=2)
        except OSError:
            pass

    def get_lessons(self, category: str, limit: int = 5) -> list[dict[str, Any]]:
        cat = category.strip() if isinstance(category, str) else ""
        if not cat:
            return []
        bucket = self._kb.get("categories", {}).get(cat, {}).get("lessons", [])
        if not isinstance(bucket, list):
            return []
        # 最新的在前
        return list(reversed(bucket[-limit:]))

    def add_lesson(self, category: str, lesson: dict[str, Any]) -> None:
        cat = category.strip() if isinstance(category, str) else ""
        if not cat:
            return
        categories = self._kb.setdefault("categories", {})
        bucket = categories.setdefault(cat, {"lessons": []}).setdefault("lessons", [])
        if not isinstance(bucket, list):
            return
        lesson = dict(lesson)
        lesson["created_at"] = datetime.now(timezone.utc).isoformat()
        bucket.append(lesson)
        if len(bucket) > self.MAX_PER_CATEGORY:
            bucket[:] = bucket[-self.MAX_PER_CATEGORY :]
        self.save()

    def distill_and_store(
        self,
        category: str,
        ledger: FailureLedger,
        source_file: str,
        best_score: float,
        rounds: int,
    ) -> dict[str, Any]:
        """把本次账本蒸馏成一条跨run教训并存入知识库。"""
        lesson = {
            "source_file": source_file,
            "effective_edits": ledger.effective_edits(),
            "failed_edits": ledger.failed_edits(),
            "persistent_failures": [
                f"{p}（{c}轮）" for p, c in ledger.recurring_patterns(min_count=3)
            ],
            "best_score": best_score,
            "rounds": rounds,
        }
        self.add_lesson(category, lesson)
        return lesson


# ---------------------------------------------------------------------------
# 生成注入 refiner / 冷启动生成器的 lessons 文本块
# ---------------------------------------------------------------------------


def format_cross_run_lessons(lessons: list[dict[str, Any]]) -> str:
    if not lessons:
        return "（暂无历史跨run教训）"
    parts = ["### 历史同类型教训（来自以往训练）"]
    for i, lesson in enumerate(lessons, 1):
        parts.append(f"**[历史 {i}]** 来源：{lesson.get('source_file', '未知')}，best_score={lesson.get('best_score', 0):.3f}")
        eff = lesson.get("effective_edits") or []
        failed = lesson.get("failed_edits") or []
        per = lesson.get("persistent_failures") or []
        if eff:
            parts.append(f"- 有效修改：{'；'.join(eff)}")
        if failed:
            parts.append(f"- 失败修改（不要再做）：{'；'.join(failed)}")
        if per:
            parts.append(f"- 顽固失败模式（必须换思路根绝）：{'；'.join(per)}")
    return "\n".join(parts)


def build_coldstart_block(
    category: str,
    cross_run_lessons: list[dict[str, Any]],
) -> str:
    """冷启动生成器使用的先验+历史教训块。"""
    parts = []
    prior = build_category_prior(category)
    if prior:
        parts.append(prior)
    lessons = format_cross_run_lessons(cross_run_lessons)
    if lessons:
        parts.append(lessons)
    return "\n\n".join(parts)


def build_refinement_block(
    ledger: FailureLedger,
    cross_run_lessons: list[dict[str, Any]],
    category: str,
) -> str:
    """refiner 使用的 lessons 块（类型先验 + 跨run教训 + 轮内账本）。"""
    parts = []

    prior = build_category_prior(category)
    if prior:
        parts.append(prior)

    lessons = format_cross_run_lessons(cross_run_lessons)
    if lessons:
        parts.append(lessons)

    if ledger.edit_history:
        parts.append("### 本轮已尝试的修改方向及效果（不要重复失败方向）")
        for e in ledger.edit_history:
            tag = "成功" if e.get("accepted") else "失败"
            delta_str = f"{e['delta']:+.3f}" if e["delta"] is not None else "N/A"
            parts.append(f"- R{e['round']}: {e['change_summary']} -> {delta_str}（{tag}）")

    recurring = ledger.recurring_patterns(min_count=2)
    if recurring:
        parts.append("### 反复出现的失败模式（已连续出现≥2轮，必须换思路从根本上解决）")
        for p, c in recurring:
            parts.append(f"- {p}（{c}轮）")
        parts.append(
            "**指令**：以上模式不是措辞问题，而是当前 prompt 结构无法解决的系统性问题。"
            "本轮请换完全不同的组织方式，或从不同角度（节奏/对白/视角/信息密度）切入，"
            "而不是重复之前已经失败的规则叠加。"
        )

    return "\n\n".join(parts)


def format_ledger_for_result(ledger: FailureLedger) -> dict[str, Any]:
    """用于给前端展示的历史教训摘要。"""
    return {
        "edit_history": ledger.edit_history,
        "failure_pattern_counts": ledger.failure_pattern_counts,
        "effective_edits": ledger.effective_edits(),
        "failed_edits": ledger.failed_edits(),
        "recurring_patterns": [
            {"pattern": p, "count": c} for p, c in ledger.recurring_patterns()
        ],
    }
