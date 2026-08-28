"""Tier-B 单元测试：deterministic_lint 覆盖补强 —— CLI 入口 + 多规则同时触发。"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from data_modules import deterministic_lint as dl  # noqa: E402
from data_modules.deterministic_lint import DeterministicLint, Violation  # noqa: E402


# ===== 规则反例（确保每条规则的"无违规"分支也走过） =====

def test_not_is_negative_no_violation():
    """正面陈述句不触发 R2。"""
    text = "这是恐惧，也是绝望。"  # 没有"不是X是Y"句型
    vs = DeterministicLint().check(text)
    assert "R2" not in {v.rule_id for v in vs}


def test_dash_negative():
    """无破折号不触发 R1。"""
    text = "他抬起头看了过去。"
    vs = DeterministicLint().check(text)
    assert "R1" not in {v.rule_id for v in vs}


def test_first_reaction_negative():
    """第一反应短句无转折不触发 R16。"""
    text = "他的第一反应就是冲过去。"
    vs = DeterministicLint().check(text)
    assert "R16" not in {v.rule_id for v in vs}


def test_sense_metaphor_negative_no_metaphor():
    """感觉词后不接"像"不触发 R18。"""
    text = "指尖发凉。"
    vs = DeterministicLint().check(text)
    assert "R18" not in {v.rule_id for v in vs}


# ===== 多规则同时触发 + 边界 =====

def test_multiple_rules_in_one_line():
    """一行同时触发 R1 + R6-A + R4。"""
    text = '他注意到——"今天怎么样？"'
    vs = DeterministicLint().check(text)
    codes = {v.rule_id for v in vs}
    assert "R1" in codes
    assert "R6-A" in codes
    assert "R4" in codes


def test_violation_to_dict_serializable():
    """Violation.to_dict 可被 JSON 序列化。"""
    v = Violation(
        rule_id="R1", rule_name="测试", severity="hard",
        line_no=1, line_content="x", matched_text="y", suggestion="z",
    )
    blob = json.dumps(v.to_dict(), ensure_ascii=False)
    parsed = json.loads(blob)
    assert parsed["rule_id"] == "R1"


def test_check_handles_empty_lines_silently():
    """空行被跳过，不触发任何规则。"""
    text = "\n\n   \n\n"
    vs = DeterministicLint().check(text)
    assert vs == []


def test_check_returns_fresh_list_on_each_call():
    """每次 check 重置 violations 列表。"""
    linter = DeterministicLint()
    linter.check("他注意到了。")
    second = linter.check("正常文本。")
    assert second == []


def test_full_paren_too_long_not_flagged():
    """全角括号内超过 40 字应不触发 R6-C（正则上限）。"""
    text = "他走进了房间（" + "测" * 50 + "），开始检查。"
    vs = DeterministicLint().check(text)
    assert "R6-C" not in {v.rule_id for v in vs}


def test_inner_drag_pattern2():
    """R19 的第二条正则（"过了一会儿"等）。"""
    text = "过了一会儿，他抬起头。"
    vs = DeterministicLint().check(text)
    assert "R19" in {v.rule_id for v in vs}


def test_sense_reverse_kanji_open_eye():
    """R24 三个变体之一：抬头+看到。"""
    text = "他抬头，看到了悬挂的纸人。"
    vs = DeterministicLint().check(text)
    assert "R24" in {v.rule_id for v in vs}


def test_sense_reverse_short_form():
    """R24：睁眼+发现。"""
    text = "他睁眼，发现自己躺在床上。"
    vs = DeterministicLint().check(text)
    assert "R24" in {v.rule_id for v in vs}


def test_filter_word_yi_si_dao():
    """R4：意识到。"""
    text = "他意识到事情不对。"
    vs = DeterministicLint().check(text)
    assert "R4" in {v.rule_id for v in vs}


def test_filter_word_zhe_yi_wei_zhe():
    """R4：这意味着。"""
    text = "这意味着他必须立刻行动。"
    vs = DeterministicLint().check(text)
    assert "R4" in {v.rule_id for v in vs}


def test_not_is_bing_fei_variant():
    """R2 的并非变体。"""
    text = "这并非偶然而是必然。"
    vs = DeterministicLint().check(text)
    assert "R2" in {v.rule_id for v in vs}


def test_not_is_zai_yu_variant():
    """R2 的"不在于X在于Y"变体。"""
    text = "关键不在于胜负在于过程。"
    vs = DeterministicLint().check(text)
    assert "R2" in {v.rule_id for v in vs}


# ===== CLI 与 _resolve_content =====

def test_cli_with_content_file(tmp_path, capsys, monkeypatch):
    """python -m data_modules.deterministic_lint --content-file"""
    f = tmp_path / "x.md"
    f.write_text("他注意到了纸条。", encoding="utf-8")
    monkeypatch.setattr(sys, "argv", [
        "deterministic_lint",
        "--project-root", str(tmp_path),
        "--content-file", str(f),
    ])
    dl.main()
    out = capsys.readouterr().out
    payload = json.loads(out)
    assert payload["total_violations"] >= 1


def test_cli_with_chapter_resolves_file(tmp_path, capsys, monkeypatch):
    """--chapter N 从正文/ 目录读取章节。"""
    text_dir = tmp_path / "正文"
    text_dir.mkdir()
    (text_dir / "第0003章-测试.md").write_text("他注意到了。", encoding="utf-8")
    monkeypatch.setattr(sys, "argv", [
        "deterministic_lint", "--project-root", str(tmp_path), "--chapter", "3",
    ])
    dl.main()
    payload = json.loads(capsys.readouterr().out)
    assert payload["chapter"] == 3


def test_cli_chapter_old_naming(tmp_path, capsys, monkeypatch):
    """--chapter N 兼容 第N章-*.md 老命名。"""
    text_dir = tmp_path / "正文"
    text_dir.mkdir()
    (text_dir / "第5章-旧式.md").write_text("他想了想。", encoding="utf-8")
    monkeypatch.setattr(sys, "argv", [
        "deterministic_lint", "--project-root", str(tmp_path), "--chapter", "5",
    ])
    dl.main()
    payload = json.loads(capsys.readouterr().out)
    assert payload["chapter"] == 5


def test_cli_chapter_missing_raises(tmp_path, monkeypatch):
    """正文目录不存在 → FileNotFoundError。"""
    monkeypatch.setattr(sys, "argv", [
        "deterministic_lint", "--project-root", str(tmp_path), "--chapter", "1",
    ])
    with pytest.raises(FileNotFoundError):
        dl.main()


def test_cli_chapter_file_missing_raises(tmp_path, monkeypatch):
    """正文目录在但章节文件缺失 → FileNotFoundError。"""
    (tmp_path / "正文").mkdir()
    monkeypatch.setattr(sys, "argv", [
        "deterministic_lint", "--project-root", str(tmp_path), "--chapter", "99",
    ])
    with pytest.raises(FileNotFoundError):
        dl.main()


def test_cli_no_args_raises(tmp_path, monkeypatch):
    """无 --chapter / --content-file → ValueError。"""
    monkeypatch.setattr(sys, "argv", [
        "deterministic_lint", "--project-root", str(tmp_path),
    ])
    with pytest.raises(ValueError):
        dl.main()


def test_cli_persist_writes_audit_file(tmp_path, capsys, monkeypatch):
    """--persist 把结果写到 .ainovel/tmp/lint_audit.json。"""
    f = tmp_path / "x.md"
    f.write_text("他注意到了。", encoding="utf-8")
    monkeypatch.setattr(sys, "argv", [
        "deterministic_lint",
        "--project-root", str(tmp_path),
        "--content-file", str(f),
        "--persist",
    ])
    dl.main()
    capsys.readouterr()
    out_path = tmp_path / ".ainovel" / "tmp" / "lint_audit.json"
    assert out_path.is_file()
    parsed = json.loads(out_path.read_text(encoding="utf-8"))
    assert parsed["total_violations"] >= 1


def test_module_runs_as_script_subprocess(tmp_path):
    """python -X utf8 -m data_modules.deterministic_lint 子进程。"""
    f = tmp_path / "x.md"
    f.write_text("他注意到了。\n", encoding="utf-8")
    scripts_dir = Path(__file__).resolve().parents[2]
    proc = subprocess.run(
        [sys.executable, "-X", "utf8", "-m", "data_modules.deterministic_lint",
         "--project-root", str(tmp_path),
         "--content-file", str(f)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd=str(scripts_dir),
    )
    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout)
    assert payload["total_violations"] >= 1
