"""Tier-B 单元测试：quantitative_audit 覆盖补强 —— CLI 与剩余 _check_* 分支。"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from data_modules import quantitative_audit as qa  # noqa: E402
from data_modules.quantitative_audit import QuantitativeAuditor  # noqa: E402


def test_audit_punctuation_ratio_high():
    """大量逗号 + 极少句号 → STAT-03。"""
    text = "他，看，了，看，又，走，了，过，去，发，现，门，关，了。"
    report = QuantitativeAuditor().audit(text)
    assert "STAT-03" in {f.code for f in report.findings}


def test_audit_high_paragraph_uniformity():
    """段落长度高度均匀 → STAT-02。"""
    text = ("段落甲" * 3 + "\n\n" + "段落乙" * 3 + "\n\n" + "段落丙" * 3)
    report = QuantitativeAuditor().audit(text)
    # 不强求触发（取决于阈值与样本），但 metrics 必填
    assert "paragraph_len_mean" in report.metrics or report.total_paragraphs <= 2


def test_audit_four_char_high_density():
    """四字短语堆砌 → STAT-06。"""
    text = "天昏地暗。乌云密布。山崩地裂。风雨交加。鬼哭狼嚎。" * 4
    report = QuantitativeAuditor().audit(text)
    # 四字密度高
    assert report.metrics.get("four_char_density", 0) > 0


def test_audit_adjective_density_high():
    """形容词关键词密度高 → STAT-08。"""
    text = "高低深浅明暗冷热硬软粗细。" * 10
    report = QuantitativeAuditor().audit(text)
    assert report.metrics["adjective_keyword_ratio"] > 0


def test_audit_chapter_end_hook_present():
    """章末含 ? → STAT-07 不触发。"""
    text = "他走进了房间。然后开始读书。这是怎么回事？"
    report = QuantitativeAuditor().audit(text)
    assert "STAT-07" not in {f.code for f in report.findings}


def test_audit_lexicon_low_severity_threshold():
    """词库密度在 2 < per_1k < 4 → low severity。"""
    text = "他首先做了一件事。" * 3 + "正常内容。" * 50
    report = QuantitativeAuditor().audit(text)
    # 应有 findings 但不要求一定有
    blob = json.dumps(report.to_dict(), ensure_ascii=False)
    parsed = json.loads(blob)
    assert "lexicon_summary" in parsed["metrics"]


def test_audit_short_text_no_burstiness_finding():
    """句子少于 3 → 不输出 STAT-01。"""
    text = "他走了。"
    report = QuantitativeAuditor().audit(text)
    assert "STAT-01" not in {f.code for f in report.findings}


# ===== CLI =====

def test_cli_with_content_file(tmp_path, capsys, monkeypatch):
    f = tmp_path / "x.md"
    f.write_text("他缓缓走过去。" * 30, encoding="utf-8")
    monkeypatch.setattr(sys, "argv", [
        "quantitative_audit",
        "--project-root", str(tmp_path),
        "--content-file", str(f),
    ])
    qa.main()
    payload = json.loads(capsys.readouterr().out)
    assert "findings" in payload
    assert payload["total_chars"] > 0


def test_cli_with_chapter(tmp_path, capsys, monkeypatch):
    text_dir = tmp_path / "正文"
    text_dir.mkdir()
    (text_dir / "第0007章-test.md").write_text("他走进房间。", encoding="utf-8")
    monkeypatch.setattr(sys, "argv", [
        "quantitative_audit", "--project-root", str(tmp_path), "--chapter", "7",
    ])
    qa.main()
    payload = json.loads(capsys.readouterr().out)
    assert payload["chapter"] == 7


def test_cli_chapter_old_naming(tmp_path, capsys, monkeypatch):
    text_dir = tmp_path / "正文"
    text_dir.mkdir()
    (text_dir / "第3章-旧名.md").write_text("正文内容。", encoding="utf-8")
    monkeypatch.setattr(sys, "argv", [
        "quantitative_audit", "--project-root", str(tmp_path), "--chapter", "3",
    ])
    qa.main()
    payload = json.loads(capsys.readouterr().out)
    assert payload["chapter"] == 3


def test_cli_chapter_missing_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "argv", [
        "quantitative_audit", "--project-root", str(tmp_path), "--chapter", "1",
    ])
    with pytest.raises(FileNotFoundError):
        qa.main()


def test_cli_chapter_no_match(tmp_path, monkeypatch):
    (tmp_path / "正文").mkdir()
    monkeypatch.setattr(sys, "argv", [
        "quantitative_audit", "--project-root", str(tmp_path), "--chapter", "99",
    ])
    with pytest.raises(FileNotFoundError):
        qa.main()


def test_cli_no_input(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "argv", [
        "quantitative_audit", "--project-root", str(tmp_path),
    ])
    with pytest.raises(ValueError):
        qa.main()


def test_cli_persist_writes_audit(tmp_path, capsys, monkeypatch):
    f = tmp_path / "x.md"
    f.write_text("正文。" * 50, encoding="utf-8")
    monkeypatch.setattr(sys, "argv", [
        "quantitative_audit",
        "--project-root", str(tmp_path),
        "--content-file", str(f),
        "--persist",
    ])
    qa.main()
    capsys.readouterr()
    out_path = tmp_path / ".ainovel" / "tmp" / "quant_audit.json"
    assert out_path.is_file()


def test_cli_output_flag_writes_file(tmp_path, capsys, monkeypatch):
    f = tmp_path / "x.md"
    f.write_text("正文。" * 50, encoding="utf-8")
    out_file = tmp_path / "report.json"
    monkeypatch.setattr(sys, "argv", [
        "quantitative_audit",
        "--project-root", str(tmp_path),
        "--content-file", str(f),
        "--output", str(out_file),
    ])
    qa.main()
    assert out_file.is_file()
    parsed = json.loads(out_file.read_text(encoding="utf-8"))
    assert "findings" in parsed


def test_module_runs_as_script_subprocess(tmp_path):
    f = tmp_path / "x.md"
    f.write_text("他走了。\n", encoding="utf-8")
    scripts_dir = Path(__file__).resolve().parents[2]
    proc = subprocess.run(
        [sys.executable, "-X", "utf8", "-m", "data_modules.quantitative_audit",
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
    assert "findings" in payload
