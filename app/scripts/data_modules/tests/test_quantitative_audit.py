"""单元测试：quantitative_audit"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]  # scripts/
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from data_modules.quantitative_audit import (  # noqa: E402
    QuantitativeAuditor,
    AuditReport,
    LEXICON,
    THRESHOLDS,
    HOOK_KEYWORDS,
)


def test_audit_returns_report_with_basic_metrics():
    """基础流程：返回 AuditReport，含基础统计指标"""
    text = "这是一段测试文本。" * 30
    auditor = QuantitativeAuditor()
    report = auditor.audit(text, chapter=1)
    assert isinstance(report, AuditReport)
    assert report.chapter == 1
    assert report.total_chars == len(text)
    assert report.total_sentences > 0
    assert "sentence_len_mean" in report.metrics


def test_audit_detects_low_burstiness():
    """所有句子等长 → 应触发 STAT-01 句长方差告警"""
    text = "这一句话固定长度十二字。" * 50
    auditor = QuantitativeAuditor()
    report = auditor.audit(text)
    codes = {f.code for f in report.findings}
    assert "STAT-01" in codes


def test_audit_detects_high_adverb_verb_density():
    """密集万能副词 → 应触发 STAT-05"""
    text = (
        "他缓缓走过去。她淡淡说道。我微微一笑。轻轻一推。静静坐下。"
        "默默离开。悄悄看了一眼。慢慢闭上眼睛。渐渐失去意识。暗暗想道。"
    ) * 5
    auditor = QuantitativeAuditor()
    report = auditor.audit(text)
    codes = {f.code for f in report.findings}
    assert "STAT-05" in codes
    # 应有 evidence
    stat05 = next(f for f in report.findings if f.code == "STAT-05")
    assert len(stat05.evidence) > 0


def test_audit_detects_weak_chapter_end_hook():
    """章末没有钩子词 → 应触发 STAT-07"""
    text = "他走进了房间。然后打开了灯。然后开始读书。一切如常。"
    auditor = QuantitativeAuditor()
    report = auditor.audit(text)
    codes = {f.code for f in report.findings}
    assert "STAT-07" in codes


def test_audit_dialogue_ratio_with_chinese_quotes():
    """中文双引号、直角引号都应被识别为对话"""
    text = "他问：“今天怎么样？”她答道：「还行。」" * 30
    auditor = QuantitativeAuditor()
    report = auditor.audit(text)
    assert report.metrics["dialogue_ratio"] > 0.1


def test_audit_lexicon_density_triggers_finding():
    """密集 A 类总结词 → 应有 LEX-A 类 finding"""
    text = (
        "总之，这件事综上所述。由此可见，归根结底，总而言之，可以看出。"
        "总体来看，从这个角度看，换句话说，简而言之，概括来说。"
    ) * 8
    auditor = QuantitativeAuditor()
    report = auditor.audit(text)
    codes = {f.code for f in report.findings}
    assert any(c.startswith("LEX-A") for c in codes)


def test_audit_to_dict_serializable():
    """to_dict 输出应可序列化"""
    import json
    text = "测试文本。" * 20
    auditor = QuantitativeAuditor()
    report = auditor.audit(text)
    blob = json.dumps(report.to_dict(), ensure_ascii=False)
    parsed = json.loads(blob)
    assert "findings" in parsed
    assert "metrics" in parsed


def test_lexicon_categories_complete():
    """14 类词库齐全（A-N）"""
    expected_prefixes = ["A_", "B_", "C_", "D_", "E_", "F_", "G_", "H_", "I_", "J_", "K_", "L_", "M_", "N_"]
    for prefix in expected_prefixes:
        assert any(k.startswith(prefix) for k in LEXICON.keys()), f"missing {prefix}"


def test_audit_no_division_by_zero_on_empty():
    """空文本不报错"""
    auditor = QuantitativeAuditor()
    report = auditor.audit("", chapter=99)
    assert report.total_chars == 0
    assert isinstance(report.findings, list)
