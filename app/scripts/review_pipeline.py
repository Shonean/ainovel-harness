#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Step 3 审查结果处理。

读取 reviewer agent 的原始输出 JSON，解析为 ReviewResult，
生成 metrics 用于 index.db 沉淀。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional

from runtime_compat import enable_windows_utf8_stdio


def _ensure_scripts_path() -> None:
    scripts_dir = Path(__file__).resolve().parent
    if str(scripts_dir) not in sys.path:
        sys.path.insert(0, str(scripts_dir))


_ensure_scripts_path()

# ── Review schema (merged from data_modules/review_schema.py) ─────────

try:
    from security_utils import atomic_write_json
except ImportError:  # pragma: no cover
    from scripts.security_utils import atomic_write_json

VALID_SEVERITIES = {"critical", "high", "medium", "low"}
VALID_CATEGORIES = {
    "continuity", "setting", "character", "timeline",
    "ai_flavor", "logic", "pacing", "other",
}
SCORE_CATEGORIES = (
    "continuity",
    "setting",
    "character",
    "timeline",
    "ai_flavor",
    "logic",
    "pacing",
    "other",
)
SEVERITY_PENALTIES = {
    "critical": 35.0,
    "high": 15.0,
    "medium": 6.0,
    "low": 2.0,
}


def _clamp_score(value: float) -> float:
    return round(max(0.0, min(100.0, value)), 2)


def _issue_penalty(issue: "ReviewIssue") -> float:
    return float(SEVERITY_PENALTIES.get(issue.severity, SEVERITY_PENALTIES["medium"]))


@dataclass
class ReviewIssue:
    severity: str
    category: str = "other"
    location: str = ""
    description: str = ""
    evidence: str = ""
    fix_hint: str = ""
    blocking: Optional[bool] = None

    def __post_init__(self):
        if self.severity not in VALID_SEVERITIES:
            self.severity = "medium"
        if self.category not in VALID_CATEGORIES:
            self.category = "other"
        if self.blocking is None:
            self.blocking = self.severity == "critical"

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class ReviewResult:
    chapter: int
    issues: List[ReviewIssue] = field(default_factory=list)
    summary: str = ""

    @property
    def issues_count(self) -> int:
        return len(self.issues)

    @property
    def blocking_count(self) -> int:
        return sum(1 for i in self.issues if i.blocking)

    @property
    def has_blocking(self) -> bool:
        return self.blocking_count > 0

    @property
    def severity_counts(self) -> Dict[str, int]:
        counts = {level: 0 for level in ("critical", "high", "medium", "low")}
        for issue in self.issues:
            severity = issue.severity if issue.severity in counts else "medium"
            counts[severity] += 1
        return counts

    @property
    def categories(self) -> List[str]:
        return sorted(set(i.category for i in self.issues))

    @property
    def critical_issues(self) -> List[str]:
        return [
            issue.description
            for issue in self.issues
            if issue.severity == "critical" and issue.description
        ]

    def _build_dimension_scores(self) -> Dict[str, float]:
        scores = {category: 100.0 for category in SCORE_CATEGORIES}
        for issue in self.issues:
            category = issue.category if issue.category in scores else "other"
            scores[category] = _clamp_score(scores[category] - _issue_penalty(issue))
        return scores

    def _build_notes(self, categories: List[str]) -> str:
        parts: List[str] = []
        if self.summary:
            parts.append(self.summary)
        parts.append(f"issues={self.issues_count}")
        parts.append(f"blocking={self.blocking_count}")
        if categories:
            parts.append("categories=" + ",".join(categories))
        return " | ".join(parts)

    def _calculate_overall_score(self) -> float:
        score = 100.0
        for issue in self.issues:
            score -= _issue_penalty(issue)
        return _clamp_score(score)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "chapter": self.chapter,
            "issues": [i.to_dict() for i in self.issues],
            "issues_count": self.issues_count,
            "blocking_count": self.blocking_count,
            "has_blocking": self.has_blocking,
            "summary": self.summary,
        }

    def to_metrics_dict(self, report_file: str = "") -> Dict[str, Any]:
        categories = self.categories
        severity_counts = self.severity_counts
        return {
            "chapter": self.chapter,
            "start_chapter": self.chapter,
            "end_chapter": self.chapter,
            "overall_score": self._calculate_overall_score(),
            "dimension_scores": self._build_dimension_scores(),
            "severity_counts": severity_counts,
            "critical_issues": self.critical_issues,
            "report_file": report_file,
            "notes": self._build_notes(categories),
            "issues_count": self.issues_count,
            "blocking_count": self.blocking_count,
            "categories": categories,
            "timestamp": datetime.now().isoformat(timespec="seconds"),
        }


def parse_review_output(chapter: int, raw: Dict[str, Any]) -> ReviewResult:
    issues = []
    for item in raw.get("issues", []):
        if not isinstance(item, dict):
            continue
        issues.append(ReviewIssue(
            severity=str(item.get("severity", "medium")),
            category=str(item.get("category", "other")),
            location=str(item.get("location", "")),
            description=str(item.get("description", "")),
            evidence=str(item.get("evidence", "")),
            fix_hint=str(item.get("fix_hint", "")),
            blocking=item.get("blocking"),
        ))
    return ReviewResult(
        chapter=chapter,
        issues=issues,
        summary=str(raw.get("summary", "")),
    )


def _read_json_if_exists(path: Path) -> Any | None:
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"Bad JSON in {path}") from exc


def _write_json(path: Path, payload: Any) -> None:
    atomic_write_json(path, payload, backup=True)


def append_ai_flavor_anti_patterns(project_root: str | Path, result: ReviewResult) -> int:
    root = Path(project_root).expanduser().resolve()
    path = root / ".story-system" / "anti_patterns.json"
    existing = _read_json_if_exists(path) or []
    if not isinstance(existing, list):
        existing = []

    seen_texts = {str(item.get("text") or "").strip() for item in existing if isinstance(item, dict)}
    additions: List[Dict[str, Any]] = []
    for index, issue in enumerate(result.issues, start=1):
        if issue.category != "ai_flavor" or issue.severity not in {"medium", "high", "critical"}:
            continue
        text = (issue.evidence or issue.description or "").strip()[:200]
        if not text or text in seen_texts:
            continue
        seen_texts.add(text)
        additions.append(
            {
                "text": text,
                "source_table": "review_extracted",
                "source_id": f"ch{int(result.chapter):04d}_issue_{index}",
                "category": issue.category,
                "added_at": datetime.now().isoformat(timespec="seconds"),
            }
        )

    if additions:
        path.parent.mkdir(parents=True, exist_ok=True)
        _write_json(path, [*existing, *additions])
    return len(additions)


def _resolve_report_path(project_root: Path, report_file: str) -> Path:
    root = project_root.expanduser().resolve()
    report_path = Path(report_file).expanduser()
    if not report_path.is_absolute():
        report_path = root / report_path
    report_path = report_path.resolve()
    try:
        report_path.relative_to(root)
    except ValueError as exc:
        raise ValueError("report-file 必须位于 project_root 目录内") from exc
    return report_path


def _format_issue(issue: Dict[str, Any], index: int) -> List[str]:
    description = str(issue.get("description") or "未填写问题描述")
    severity = str(issue.get("severity") or "medium")
    category = str(issue.get("category") or "other")
    location = str(issue.get("location") or "未标注位置")
    evidence = str(issue.get("evidence") or "未提供证据")
    fix_hint = str(issue.get("fix_hint") or "未提供修复方向")
    blocking = "是" if issue.get("blocking") else "否"

    return [
        f"{index}. **{description}**",
        f"   - 严重级别：{severity}",
        f"   - 分类：{category}",
        f"   - 位置：{location}",
        f"   - 阻断：{blocking}",
        f"   - 证据：{evidence}",
        f"   - 修复方向：{fix_hint}",
    ]


def render_review_report(payload: Dict[str, Any]) -> str:
    result = payload["review_result"]
    metrics = payload["metrics"]
    issues = list(result.get("issues", []))
    blocking_issues = [issue for issue in issues if issue.get("blocking")]
    non_blocking_issues = [issue for issue in issues if not issue.get("blocking")]
    severity_counts = metrics.get("severity_counts", {})

    lines: List[str] = [
        f"# 第{payload['chapter']}章审查报告",
        "",
        "## 总览",
        "",
        f"- 问题数：{result.get('issues_count', 0)}",
        f"- 阻断数：{result.get('blocking_count', 0)}",
        f"- 结论：{'需修复后重审' if result.get('has_blocking') else '无阻断问题'}",
    ]
    summary = str(result.get("summary") or "").strip()
    if summary:
        lines.append(f"- 摘要：{summary}")
    if severity_counts:
        ordered = [
            f"{level}={severity_counts.get(level, 0)}"
            for level in ("critical", "high", "medium", "low")
        ]
        lines.append(f"- 严重级别统计：{', '.join(ordered)}")

    lines.extend(["", "## 阻断问题", ""])
    if blocking_issues:
        for index, issue in enumerate(blocking_issues, start=1):
            lines.extend(_format_issue(issue, index))
            lines.append("")
    else:
        lines.append("无。")
        lines.append("")

    lines.extend(["## 其他问题", ""])
    if non_blocking_issues:
        for index, issue in enumerate(non_blocking_issues, start=1):
            lines.extend(_format_issue(issue, index))
            lines.append("")
    else:
        lines.append("无。")
        lines.append("")

    lines.extend(["## 修复方向", ""])
    if issues:
        ordered_issues = [*blocking_issues, *non_blocking_issues]
        for index, issue in enumerate(ordered_issues, start=1):
            description = str(issue.get("description") or "未填写问题描述")
            fix_hint = str(issue.get("fix_hint") or "未提供修复方向")
            lines.append(f"{index}. {description}：{fix_hint}")
    else:
        lines.append("暂无需要修复的问题。")

    return "\n".join(lines).rstrip() + "\n"


def write_review_report(project_root: Path, report_file: str, payload: Dict[str, Any]) -> Path:
    report_path = _resolve_report_path(project_root, report_file)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(render_review_report(payload), encoding="utf-8")
    return report_path


def _build_review_metrics_record(metrics: Dict[str, Any]):
    from data_modules.index_manager import ReviewMetrics

    return ReviewMetrics(
        start_chapter=int(metrics["start_chapter"]),
        end_chapter=int(metrics["end_chapter"]),
        overall_score=float(metrics.get("overall_score", 0.0)),
        dimension_scores=dict(metrics.get("dimension_scores", {})),
        severity_counts=dict(metrics.get("severity_counts", {})),
        critical_issues=list(metrics.get("critical_issues", [])),
        report_file=str(metrics.get("report_file", "")),
        notes=str(metrics.get("notes", "")),
    )


def build_review_artifacts(
    project_root: Path,
    chapter: int,
    review_results_path: Path,
    report_file: str = "",
) -> Dict[str, Any]:
    raw = json.loads(review_results_path.read_text(encoding="utf-8"))
    result = parse_review_output(chapter=chapter, raw=raw)
    anti_patterns_added = append_ai_flavor_anti_patterns(project_root, result)
    metrics = result.to_metrics_dict(report_file=report_file)

    return {
        "chapter": chapter,
        "review_result": result.to_dict(),
        "metrics": metrics,
        "anti_patterns_added": anti_patterns_added,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Review pipeline v6")
    parser.add_argument("--project-root", required=True)
    parser.add_argument("--chapter", type=int, required=True)
    parser.add_argument("--review-results", required=True)
    parser.add_argument("--metrics-out", default="")
    parser.add_argument("--report-file", default="")
    parser.add_argument("--save-metrics", action="store_true",
                        help="直接写入 index.db，省去单独调用 save-review-metrics")

    args = parser.parse_args()
    project_root = Path(args.project_root)
    review_results_path = Path(args.review_results)

    payload = build_review_artifacts(
        project_root=project_root,
        chapter=args.chapter,
        review_results_path=review_results_path,
        report_file=args.report_file,
    )

    if args.metrics_out:
        out_path = Path(args.metrics_out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(
            json.dumps(payload["metrics"], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    if args.report_file:
        write_review_report(
            project_root=project_root,
            report_file=args.report_file,
            payload=payload,
        )

    if args.save_metrics:
        from data_modules.config import DataModulesConfig
        from data_modules.index_manager import IndexManager
        config = DataModulesConfig.from_project_root(project_root)
        manager = IndexManager(config)
        manager.save_review_metrics(_build_review_metrics_record(payload["metrics"]))

    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    if sys.platform == "win32":
        enable_windows_utf8_stdio()
    main()
