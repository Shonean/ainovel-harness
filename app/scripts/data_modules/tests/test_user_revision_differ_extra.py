"""Tier-B 单元测试：user_revision_differ 覆盖补强 —— CLI 子命令 + 边界。"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from data_modules import user_revision_differ as urd  # noqa: E402
from data_modules.user_revision_differ import (  # noqa: E402
    ClassifiedHunk,
    DiffHunk,
    _normalize_preferences,
    _read_json_safe,
    _write_json,
    get_high_frequency_preferences,
    split_sentences,
)


# ===== _normalize_preferences =====

def test_normalize_preferences_from_none():
    p = _normalize_preferences(None)
    assert p["version"] == 1
    assert "style" in p["buckets"]
    assert p["summary"]["total_revisions"] == 0


def test_normalize_preferences_partial_dict():
    p = _normalize_preferences({"version": 2})
    assert p["version"] == 2
    assert "style" in p["buckets"]
    assert "preference" in p["buckets"]


def test_normalize_preferences_from_list():
    p = _normalize_preferences([])  # 非 dict
    assert p["version"] == 1
    assert isinstance(p["buckets"], dict)


# ===== _read_json_safe / _write_json =====

def test_read_json_safe_missing_file(tmp_path):
    assert _read_json_safe(tmp_path / "no.json") is None


def test_read_json_safe_invalid_json(tmp_path):
    f = tmp_path / "bad.json"
    f.write_text("{ not json", encoding="utf-8")
    assert _read_json_safe(f) is None


def test_read_json_safe_valid(tmp_path):
    f = tmp_path / "good.json"
    f.write_text('{"a": 1}', encoding="utf-8")
    assert _read_json_safe(f) == {"a": 1}


def test_write_json_creates_parent_dirs(tmp_path):
    f = tmp_path / "deep" / "nest" / "x.json"
    _write_json(f, {"x": 1})
    assert f.is_file()
    assert json.loads(f.read_text(encoding="utf-8")) == {"x": 1}


# ===== DiffHunk / ClassifiedHunk to_dict =====

def test_diff_hunk_to_dict_round_trip():
    h = DiffHunk(hunk_idx=0, op="replace", ai_text="a", final_text="b")
    blob = json.dumps(h.to_dict(), ensure_ascii=False)
    parsed = json.loads(blob)
    assert parsed["op"] == "replace"


def test_classified_hunk_to_dict():
    ch = ClassifiedHunk(
        hunk_idx=0, op="insert", ai_text="", final_text="新句",
        category="style", rationale="补具体细节",
    )
    blob = json.dumps(ch.to_dict(), ensure_ascii=False)
    parsed = json.loads(blob)
    assert parsed["category"] == "style"


# ===== split_sentences 边界 =====

def test_split_sentences_empty():
    assert split_sentences("") == []
    assert split_sentences(None) == []  # type: ignore[arg-type]


def test_split_sentences_no_terminator():
    assert split_sentences("一整段没标点") == ["一整段没标点"]


def test_split_sentences_consecutive_terminators():
    sents = split_sentences("？？？！")
    # 多个标点合并到前一句末尾
    assert all(s.strip() for s in sents)


# ===== get_high_frequency_preferences 空文件 =====

def test_high_freq_returns_empty_on_missing_prefs(tmp_path):
    result = get_high_frequency_preferences(tmp_path, min_count=2)
    assert result == {"style": [], "preference": []}


def test_high_freq_skips_empty_ai_text(tmp_path):
    """ai_text 为空字符串的条目被跳过。"""
    from data_modules.user_revision_differ import commit_classified_hunks
    classified = [
        ClassifiedHunk(hunk_idx=i, op="replace", ai_text="",
                       final_text=f"f{i}", category="style", rationale="")
        for i in range(3)
    ]
    commit_classified_hunks(tmp_path, chapter=1, classified=classified)
    high = get_high_frequency_preferences(tmp_path, min_count=2)
    assert high["style"] == []


# ===== CLI diff =====

def test_cli_diff(tmp_path, capsys, monkeypatch):
    ai_path = tmp_path / "ai.md"
    final_path = tmp_path / "final.md"
    ai_path.write_text("他感到一阵恐惧。", encoding="utf-8")
    final_path.write_text("他喉咙发紧。手指抠住门把。", encoding="utf-8")
    monkeypatch.setattr(sys, "argv", [
        "urd", "diff", str(ai_path), str(final_path),
    ])
    urd.main()
    payload = json.loads(capsys.readouterr().out)
    assert payload["total_hunks"] >= 1


def test_cli_diff_with_output_flag(tmp_path, capsys, monkeypatch):
    ai_path = tmp_path / "ai.md"
    final_path = tmp_path / "final.md"
    ai_path.write_text("他走进房间。", encoding="utf-8")
    final_path.write_text("他推门进入房间。", encoding="utf-8")
    out_path = tmp_path / "hunks.json"
    monkeypatch.setattr(sys, "argv", [
        "urd", "diff", str(ai_path), str(final_path), "-o", str(out_path),
    ])
    urd.main()
    assert out_path.is_file()
    parsed = json.loads(out_path.read_text(encoding="utf-8"))
    assert "hunks" in parsed


# ===== CLI commit =====

def test_cli_commit(tmp_path, capsys, monkeypatch):
    classified_file = tmp_path / "c.json"
    classified_file.write_text(json.dumps({
        "hunks": [
            {"hunk_idx": 0, "op": "replace", "ai_text": "a",
             "final_text": "b", "category": "style", "rationale": "r"},
        ]
    }, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(sys, "argv", [
        "urd", "--project-root", str(tmp_path), "commit",
        "--classified", str(classified_file), "--chapter", "1",
    ])
    urd.main()
    payload = json.loads(capsys.readouterr().out)
    assert payload["total_revisions"] == 1


def test_cli_commit_accepts_list_payload(tmp_path, capsys, monkeypatch):
    """commit 接受 {"hunks": [...]} 或 dict-with-list 形式（裸 list 当前不支持）。"""
    # 用 dict 形式且 hunks 为多条记录
    classified_file = tmp_path / "c.json"
    classified_file.write_text(json.dumps({
        "hunks": [
            {"hunk_idx": 0, "op": "replace", "ai_text": "a",
             "final_text": "b", "category": "preference", "rationale": "r"},
            {"hunk_idx": 1, "op": "insert", "ai_text": "",
             "final_text": "新", "category": "preference", "rationale": "r"},
        ]
    }, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(sys, "argv", [
        "urd", "--project-root", str(tmp_path), "commit",
        "--classified", str(classified_file), "--chapter", "2",
    ])
    urd.main()
    payload = json.loads(capsys.readouterr().out)
    assert payload["appended_counts"].get("preference") == 2


def test_cli_commit_without_project_root_exits(monkeypatch):
    monkeypatch.setattr(sys, "argv", [
        "urd", "commit", "--classified", "x", "--chapter", "1",
    ])
    with pytest.raises(SystemExit) as exc:
        urd.main()
    assert exc.value.code == 1


# ===== CLI show =====

def test_cli_show_empty(tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(sys, "argv", [
        "urd", "--project-root", str(tmp_path), "show",
    ])
    urd.main()
    payload = json.loads(capsys.readouterr().out)
    assert payload == {}


def test_cli_show_with_existing_prefs(tmp_path, capsys, monkeypatch):
    prefs_path = tmp_path / ".story-system" / "user_preferences.json"
    prefs_path.parent.mkdir(parents=True)
    prefs_path.write_text(json.dumps({"version": 1, "buckets": {}}), encoding="utf-8")
    monkeypatch.setattr(sys, "argv", [
        "urd", "--project-root", str(tmp_path), "show",
    ])
    urd.main()
    payload = json.loads(capsys.readouterr().out)
    assert payload["version"] == 1


def test_cli_show_without_project_root_exits(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["urd", "show"])
    with pytest.raises(SystemExit) as exc:
        urd.main()
    assert exc.value.code == 1


# ===== CLI high-freq =====

def test_cli_high_freq(tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(sys, "argv", [
        "urd", "--project-root", str(tmp_path), "high-freq",
        "--min-count", "1",
    ])
    urd.main()
    payload = json.loads(capsys.readouterr().out)
    assert "style" in payload


def test_cli_high_freq_without_project_root_exits(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["urd", "high-freq"])
    with pytest.raises(SystemExit) as exc:
        urd.main()
    assert exc.value.code == 1


# ===== 子进程烟测 =====

def test_module_subprocess_diff(tmp_path):
    ai = tmp_path / "ai.md"
    final = tmp_path / "final.md"
    ai.write_text("正文一。\n", encoding="utf-8")
    final.write_text("正文一改。\n", encoding="utf-8")
    scripts_dir = Path(__file__).resolve().parents[2]
    proc = subprocess.run(
        [sys.executable, "-X", "utf8", "-m", "data_modules.user_revision_differ",
         "diff", str(ai), str(final)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd=str(scripts_dir),
    )
    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout)
    assert "hunks" in payload
