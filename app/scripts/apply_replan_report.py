#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
应用「重规划建议报告」中的结构化指令。

用法：
    python apply_replan_report.py --project-root ROOT --report 大纲/重规划建议-第0001章.md
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from pathlib import Path
from typing import Any

from runtime_compat import enable_windows_utf8_stdio


def _extract_json_block(text: str) -> dict | None:
    """提取报告中最后一个 ```json 代码块。"""
    blocks = re.findall(r"```json\s*\n(.*?)\n```", text, re.DOTALL)
    if not blocks:
        return None
    try:
        return json.loads(blocks[-1].strip())
    except json.JSONDecodeError:
        return None


def _load_report(project_root: Path, report_path: str) -> tuple[Path, str]:
    p = Path(report_path)
    if not p.is_absolute():
        p = project_root / p
    if not p.is_file():
        raise FileNotFoundError(f"报告文件不存在: {p}")
    if not p.name.startswith("重规划建议-") or not p.name.endswith(".md"):
        raise ValueError(f"不是有效的重规划建议报告: {p.name}")
    return p, p.read_text(encoding="utf-8")


def _safe_path(project_root: Path, rel_path: str) -> Path:
    p = project_root / rel_path
    p.resolve()
    # 简单防穿越：必须落在 project_root 下
    try:
        p.relative_to(project_root.resolve())
    except ValueError as exc:
        raise ValueError(f"非法路径: {rel_path}") from exc
    return p


def _create_setting(project_root: Path, action: dict, results: list[dict]) -> None:
    rel = action.get("path", "")
    content = action.get("content", "")
    if not rel.endswith(".md"):
        raise ValueError(f"create_setting path 必须是 .md: {rel}")
    p = _safe_path(project_root, rel)
    p.parent.mkdir(parents=True, exist_ok=True)
    existed = p.is_file()
    p.write_text(content, encoding="utf-8")
    results.append({
        "type": "create_setting",
        "path": rel,
        "existed": existed,
        "status": "written",
    })


def _update_master(project_root: Path, action: dict, results: list[dict]) -> None:
    section = action.get("rewrite_section", "")
    content = action.get("content", "")
    master = project_root / "大纲" / "总纲.md"
    master.parent.mkdir(parents=True, exist_ok=True)
    marker = f"\n\n<!-- 重规划更新：{section} -->\n\n"
    append_text = marker + content
    if master.is_file():
        text = master.read_text(encoding="utf-8")
        text = text.rstrip() + append_text
    else:
        text = f"# 总纲\n{append_text}"
    master.write_text(text, encoding="utf-8")
    results.append({
        "type": "update_master",
        "section": section,
        "status": "appended",
    })


def _replan_from_chapter(project_root: Path, action: dict, results: list[dict]) -> None:
    chapter = int(action.get("chapter", 1))
    outline_dir = project_root / "大纲"
    removed = []
    if outline_dir.is_dir():
        for p in outline_dir.glob("第*.md"):
            m = re.search(r"第(\d+)章", p.name)
            if m and int(m.group(1)) >= chapter:
                # 移动到 _old_outlines 备份而不是直接删除
                backup_dir = outline_dir / "_old_outlines"
                backup_dir.mkdir(parents=True, exist_ok=True)
                dst = backup_dir / p.name
                shutil.move(str(p), str(dst))
                removed.append(p.name)
    results.append({
        "type": "replan_from_chapter",
        "chapter": chapter,
        "removed_chapters": removed,
        "status": "ok",
    })


def apply_report(project_root: Path, report_path: str) -> dict[str, Any]:
    p, text = _load_report(project_root, report_path)
    payload = _extract_json_block(text)
    if not payload:
        raise ValueError("报告中未找到有效的 ```json 应用指令块")
    actions = payload.get("actions") or []
    if not isinstance(actions, list):
        raise ValueError("应用指令块中的 actions 必须是数组")

    results: list[dict] = []
    for action in actions:
        if not isinstance(action, dict):
            continue
        typ = action.get("type")
        if typ == "create_setting":
            _create_setting(project_root, action, results)
        elif typ == "update_master":
            _update_master(project_root, action, results)
        elif typ == "replan_from_chapter":
            _replan_from_chapter(project_root, action, results)
        else:
            results.append({"type": typ, "status": "skipped", "reason": "未知 action 类型"})

    return {
        "ok": True,
        "report": report_path,
        "actions_count": len(actions),
        "results": results,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Apply replan suggestion report")
    parser.add_argument("--project-root", required=True)
    parser.add_argument("--report", required=True)
    args = parser.parse_args()

    project_root = Path(args.project_root).expanduser().resolve()
    try:
        result = apply_report(project_root, args.report)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except Exception as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1


if __name__ == "__main__":
    if sys.platform == "win32":
        enable_windows_utf8_stdio()
    sys.exit(main())
