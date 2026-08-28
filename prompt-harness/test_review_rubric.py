"""审阅 rubric 升级隔离测试：置信度门槛 + priority 解析 + 去重取高置信。

直连 _normalize_finding，不起服务、不调 LLM。
运行：cd prompt-harness && python test_review_rubric.py
"""
import sys
sys.path.insert(0, ".")

from prompt_harness.ai_flavor import _normalize_finding, REVIEW_CONFIDENCE_FLOOR

FAILS = []


def check(name, cond):
    print(("PASS" if cond else "FAIL"), name)
    if not cond:
        FAILS.append(name)


def test_below_floor_dropped():
    f = _normalize_finding(
        {"quote": "他缓缓开口", "category": "副词冗余", "severity": "medium",
         "confidence_score": 0.4, "fix": "删掉"})
    check("low confidence dropped", f is None)


def test_at_floor_kept():
    f = _normalize_finding(
        {"quote": "他缓缓开口", "category": "副词冗余", "severity": "high",
         "confidence_score": REVIEW_CONFIDENCE_FLOOR, "fix": "删掉"})
    check("at floor kept", f is not None and f["confidence"] == REVIEW_CONFIDENCE_FLOOR)


def test_priority_parsed():
    f = _normalize_finding(
        {"quote": "x" * 10, "category": "排比句", "severity": "high",
         "confidence_score": 0.9, "priority": 0, "fix": "打散"})
    check("priority parsed", f and f["priority"] == 0)


def test_bad_priority_null():
    f = _normalize_finding(
        {"quote": "x" * 10, "category": "其它", "severity": "low",
         "confidence_score": 0.7, "priority": 9, "fix": "改"})
    check("bad priority -> None", f and f["priority"] is None)


def test_missing_confidence_defaults_by_severity():
    # 缺 confidence 时按 severity 兜底：high=0.8 保留，low=0.5 被门槛过滤
    fh = _normalize_finding(
        {"quote": "a" * 6, "category": "情绪明说", "severity": "high", "fix": "删"})
    fl = _normalize_finding(
        {"quote": "b" * 6, "category": "其它", "severity": "low", "fix": "改"})
    check("high default kept", fh is not None and fh["confidence"] == 0.8)
    check("low default dropped", fl is None)


def test_empty_quote_dropped():
    check("empty quote dropped",
          _normalize_finding({"quote": "  ", "confidence_score": 0.99}) is None)
    check("missing quote dropped",
          _normalize_finding({"confidence_score": 0.99}) is None)


def test_clamp_and_severity_guard():
    f = _normalize_finding(
        {"quote": "q" * 6, "category": "x", "severity": "CRITICAL",
         "confidence_score": 1.7, "fix": "y"})
    check("invalid severity -> medium", f and f["severity"] == "medium")
    check("confidence clamped to 1.0", f and f["confidence"] == 1.0)


def test_non_dict():
    check("non-dict dropped", _normalize_finding("oops") is None)
    check("None dropped", _normalize_finding(None) is None)


if __name__ == "__main__":
    test_below_floor_dropped()
    test_at_floor_kept()
    test_priority_parsed()
    test_bad_priority_null()
    test_missing_confidence_defaults_by_severity()
    test_empty_quote_dropped()
    test_clamp_and_severity_guard()
    test_non_dict()
    print(f"\n{'='*40}\n{len(FAILS)} FAILURES" if FAILS else "\nALL PASS")
    sys.exit(1 if FAILS else 0)
