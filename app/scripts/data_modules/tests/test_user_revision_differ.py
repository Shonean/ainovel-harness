"""单元测试：user_revision_differ"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from data_modules.user_revision_differ import (  # noqa: E402
    split_sentences,
    compute_hunks,
    commit_classified_hunks,
    ClassifiedHunk,
    get_high_frequency_preferences,
)


def test_split_sentences_basic():
    text = "他走了进来。她抬起头。怎么了？"
    sents = split_sentences(text)
    assert len(sents) == 3
    assert sents[0].endswith("。")
    assert sents[2].endswith("？")


def test_compute_hunks_replace():
    ai = "他感到一阵恐惧。然后转身就跑。"
    final = "他喉咙发紧。手指抠住了门把。然后转身就跑。"
    hunks = compute_hunks(ai, final)
    assert len(hunks) >= 1
    # 至少有一个 replace 操作
    ops = {h.op for h in hunks}
    assert "replace" in ops or "insert" in ops


def test_compute_hunks_pure_insert():
    ai = "他走进了房间。"
    final = "他走进了房间。屋里没有声音。"
    hunks = compute_hunks(ai, final)
    assert any(h.op == "insert" for h in hunks)


def test_compute_hunks_pure_delete():
    ai = "他走进了房间。屋里没有声音。"
    final = "他走进了房间。"
    hunks = compute_hunks(ai, final)
    assert any(h.op == "delete" for h in hunks)


def test_compute_hunks_no_change():
    text = "他走进了房间。屋里没有声音。"
    hunks = compute_hunks(text, text)
    assert len(hunks) == 0


def test_commit_classified_hunks_creates_preferences(tmp_path: Path):
    classified = [
        ClassifiedHunk(
            hunk_idx=0, op="replace",
            ai_text="他感到一阵恐惧。",
            final_text="他喉咙发紧。手指抠住了门把。",
            category="style",
            rationale="情绪标签化改为生理反应",
            confidence=0.95,
        ),
        ClassifiedHunk(
            hunk_idx=1, op="delete",
            ai_text="他不知道，这只是开始。",
            final_text="",
            category="preference",
            rationale="戏剧性反讽提示，删除",
            confidence=0.9,
        ),
    ]
    result = commit_classified_hunks(tmp_path, chapter=1, classified=classified)
    assert result["appended_counts"]["style"] == 1
    assert result["appended_counts"]["preference"] == 1
    assert result["total_revisions"] == 2

    prefs_path = tmp_path / ".story-system" / "user_preferences.json"
    assert prefs_path.is_file()
    prefs = json.loads(prefs_path.read_text(encoding="utf-8"))
    assert len(prefs["buckets"]["style"]) == 1
    assert len(prefs["buckets"]["preference"]) == 1


def test_commit_skips_invalid_category(tmp_path: Path):
    classified = [
        ClassifiedHunk(
            hunk_idx=0, op="replace",
            ai_text="x", final_text="y",
            category="invalid_category", rationale="r",
        ),
    ]
    result = commit_classified_hunks(tmp_path, chapter=1, classified=classified)
    assert result["total_revisions"] == 0


def test_promote_to_anti_patterns_when_threshold_hit(tmp_path: Path):
    """同样的 ai_text 被改写 ≥3 次 → 写入 anti_patterns.json"""
    same_text = "他感到一阵恐惧。"
    classified = [
        ClassifiedHunk(hunk_idx=i, op="replace",
                       ai_text=same_text, final_text=f"改写 {i}",
                       category="style", rationale="情绪标签化")
        for i in range(3)
    ]
    result = commit_classified_hunks(tmp_path, chapter=2, classified=classified)
    assert result["anti_patterns_added"] >= 1

    ap_path = tmp_path / ".story-system" / "anti_patterns.json"
    assert ap_path.is_file()
    ap = json.loads(ap_path.read_text(encoding="utf-8"))
    texts = [p.get("text") for p in ap]
    assert any("他感到一阵恐惧" in t for t in texts)


def test_anti_patterns_dedupe(tmp_path: Path):
    """重复提交不重复入 anti_patterns"""
    same_text = "他感到一阵恐惧。"
    classified = [
        ClassifiedHunk(hunk_idx=i, op="replace",
                       ai_text=same_text, final_text=f"改写 {i}",
                       category="style", rationale="r")
        for i in range(3)
    ]
    commit_classified_hunks(tmp_path, chapter=1, classified=classified)
    initial_ap = json.loads((tmp_path / ".story-system" / "anti_patterns.json").read_text(encoding="utf-8"))
    initial_count = len(initial_ap)

    # 再提一次同样 3 次
    commit_classified_hunks(tmp_path, chapter=2, classified=classified)
    after_ap = json.loads((tmp_path / ".story-system" / "anti_patterns.json").read_text(encoding="utf-8"))
    assert len(after_ap) == initial_count  # 不增加


def test_high_freq_returns_high_count_only(tmp_path: Path):
    """get_high_frequency_preferences 只返回 count >= min_count 的项"""
    classified = []
    # "AAA" 出现 3 次
    for i in range(3):
        classified.append(ClassifiedHunk(hunk_idx=i, op="replace",
                                         ai_text="AAA", final_text="改A",
                                         category="style", rationale="改写A"))
    # "BBB" 出现 1 次
    classified.append(ClassifiedHunk(hunk_idx=10, op="replace",
                                     ai_text="BBB", final_text="改B",
                                     category="style", rationale="改写B"))
    commit_classified_hunks(tmp_path, chapter=1, classified=classified)

    high = get_high_frequency_preferences(tmp_path, min_count=2)
    style_high = high["style"]
    keys = [item["ai_text"] for item in style_high]
    assert "AAA" in keys
    assert "BBB" not in keys
