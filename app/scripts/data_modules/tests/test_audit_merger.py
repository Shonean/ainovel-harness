"""单元测试：audit_merger"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from data_modules.audit_merger import merge_audits, _dedupe_and_rank, SEVERITY_ORDER  # noqa: E402


@pytest.fixture
def project_with_tmp(tmp_path: Path) -> Path:
    """创建一个含 .ainovel/tmp/ 的临时项目"""
    tmp_dir = tmp_path / ".ainovel" / "tmp"
    tmp_dir.mkdir(parents=True)
    return tmp_path


def _write(path: Path, payload):
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def test_merge_with_no_input_returns_empty(project_with_tmp: Path):
    result = merge_audits(project_with_tmp)
    assert result["merged_findings"] == []
    assert result["summary"]["total"] == 0
    assert all(v is False for v in result["tracks_loaded"].values())


def test_merge_track1_reviewer_only(project_with_tmp: Path):
    tmp = project_with_tmp / ".ainovel" / "tmp"
    _write(tmp / "review_results.json", {
        "issues": [
            {"severity": "high", "category": "ai_flavor", "location": "para 3", "description": "情绪标签化", "evidence": "他感到恐惧。", "fix_hint": "改为生理反应", "blocking": False},
        ],
    })
    result = merge_audits(project_with_tmp)
    assert result["summary"]["total"] == 1
    assert result["summary"]["by_track"]["reviewer"] == 1
    assert result["merged_findings"][0]["severity"] == "high"


def test_merge_track4_lint_marks_blocking(project_with_tmp: Path):
    tmp = project_with_tmp / ".ainovel" / "tmp"
    _write(tmp / "lint_audit.json", {
        "violations": [
            {"rule_id": "R1", "rule_name": "禁用破折号", "severity": "hard", "line_no": 5, "line_content": "他——", "matched_text": "——", "suggestion": "改为句号"},
        ],
    })
    result = merge_audits(project_with_tmp)
    assert result["summary"]["total"] == 1
    assert result["summary"]["blocking"] == 1
    assert result["merged_findings"][0]["track"] == "lint"


def test_cross_track_dedupe_and_severity_boost():
    """同 evidence 出自不同轨 → 合并、severity 提升"""
    findings = [
        {
            "track": "reviewer", "track_idx": 0, "severity": "medium",
            "evidence": "他感到一阵恐惧", "description": "情绪标签化",
            "fix_hint": "改生理反应", "blocking": False,
        },
        {
            "track": "critic", "track_idx": 0, "severity": "medium",
            "evidence": "他感到一阵恐惧", "description": "E1",
            "fix_hint": "改成微动作", "blocking": False,
        },
    ]
    merged = _dedupe_and_rank(findings)
    assert len(merged) == 1
    assert merged[0]["cross_track"] is True
    assert merged[0]["severity"] == "high"  # medium → high
    assert "改生理反应" in merged[0]["fix_hint"]
    assert "改成微动作" in merged[0]["fix_hint"]


def test_severity_ordering_blocking_first():
    """合并后排序：blocking > severity 倒序"""
    findings = [
        {"track": "lint", "evidence": "a", "severity": "hard", "blocking": True, "track_idx": 0},
        {"track": "reviewer", "evidence": "b", "severity": "critical", "blocking": False, "track_idx": 0},
        {"track": "critic", "evidence": "c", "severity": "medium", "blocking": False, "track_idx": 0},
    ]
    merged = _dedupe_and_rank(findings)
    assert merged[0]["evidence"] == "a"  # blocking 排在最前
    # 后面按 severity 排
    assert merged[1]["evidence"] == "b"
    assert merged[2]["evidence"] == "c"


def test_full_4_track_merge(project_with_tmp: Path):
    """4 轨齐全，合并去重"""
    tmp = project_with_tmp / ".ainovel" / "tmp"
    _write(tmp / "review_results.json", {
        "issues": [{"severity": "medium", "category": "ai_flavor", "location": "p1", "description": "E1 标签化", "evidence": "他感到一阵恐惧。", "fix_hint": "用生理反应", "blocking": False}],
    })
    _write(tmp / "quant_audit.json", {
        "findings": [{"code": "STAT-05", "severity": "high", "metric": "万能副词密度", "value": 5, "threshold": 3, "description": "缓缓密度高", "evidence": ["缓缓走过去"]}],
    })
    _write(tmp / "critic_audit.json", [
        {"evidence_quote": "他感到一阵恐惧", "quirk_type": "E1", "quirk_name": "情绪标签化", "rationale": "用名词代替反应", "suggested_rewrite": "改具体生理反应"},
    ])
    _write(tmp / "lint_audit.json", {
        "violations": [{"rule_id": "R1", "rule_name": "破折号", "severity": "hard", "line_no": 3, "line_content": "他——", "matched_text": "——", "suggestion": "改句号"}],
    })

    result = merge_audits(project_with_tmp)
    summary = result["summary"]
    # 4 个 finding，但 reviewer 和 critic 的 evidence 重叠（都是"他感到一阵恐惧"）→ 合并为 1
    # 所以总 = 1（cross_track 合并的）+ 1（quant）+ 1（lint）= 3
    assert summary["total"] == 3
    assert summary["cross_track_hits"] == 1
    assert summary["blocking"] == 1
    # tracks_loaded 全部为 True
    assert all(result["tracks_loaded"].values())
    # blocking 应该排第一
    assert result["merged_findings"][0]["track"] == "lint"


def test_severity_normalization():
    """blocking → critical, hard 保留"""
    from data_modules.audit_merger import _normalize_severity
    assert _normalize_severity("blocking") == "critical"
    assert _normalize_severity("CRITICAL") == "critical"
    assert _normalize_severity("hard") == "hard"
    assert _normalize_severity("h") == "hard"
    assert _normalize_severity("") == "info"
    assert _normalize_severity("unknown") == "info"
