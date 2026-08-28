#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""批量注册章节契约：为第 start~end 章生成 .story-system/chapters/chapter_NNN.json，
含从详细大纲抽取的 chapter_directive。已存在的章节不覆盖（除非 --force）。

用法：
  python -X utf8 scripts/register_chapters.py --project-root <书> --start 1 --end 50
  python -X utf8 scripts/register_chapters.py --project-root <书> --check          # 健康检查
  python -X utf8 scripts/register_chapters.py --project-root <书> --missing-only   # 只注册缺失JSON的章节
  python -X utf8 scripts/register_chapters.py --project-root <书> --auto-fill      # 从卷级大纲拆分缺失章纲.md
"""
import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from chapter_outline_loader import load_chapter_execution_directive, load_chapter_outline


def _find_chapter_range_from_state(project_root: Path) -> tuple[int, int]:
    """从 state.json 推断已规划的章节范围。"""
    state_path = project_root / ".ainovel" / "state.json"
    if not state_path.is_file():
        return 1, 0
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return 1, 0

    progress = state.get("progress") or {}
    chapters_planned = progress.get("chapters_planned") or []
    max_end = 0
    min_start = 999999
    for entry in chapters_planned:
        rng = str(entry.get("chapters_range", ""))
        if "-" in rng:
            try:
                s = int(rng.split("-")[0].strip())
                e = int(rng.split("-")[1].strip())
                if s < min_start:
                    min_start = s
                if e > max_end:
                    max_end = e
            except ValueError:
                pass
    if max_end == 0:
        return 1, 0
    return min_start, max_end


def _find_all_chapters_with_outlines(project_root: Path) -> set[int]:
    """扫描大纲/目录，返回所有有章纲文件的章号。"""
    outline_dir = project_root / "大纲"
    if not outline_dir.is_dir():
        return set()
    chapters = set()
    for f in outline_dir.glob("第*章-章纲.md"):
        m = re.match(r"^第0*(\d+)章-章纲\.md$", f.name)
        if m:
            chapters.add(int(m.group(1)))
    return chapters


def _find_all_chapters_with_json(project_root: Path) -> set[int]:
    """扫描 .story-system/chapters/，返回所有有 JSON 的章号。"""
    chapters_dir = project_root / ".story-system" / "chapters"
    if not chapters_dir.is_dir():
        return set()
    chapters = set()
    for f in chapters_dir.glob("chapter_*.json"):
        m = re.match(r"^chapter_0*(\d+)\.json$", f.name)
        if m:
            chapters.add(int(m.group(1)))
    return chapters


def _find_all_committed_chapters(project_root: Path) -> set[int]:
    """扫描 .story-system/commits/，返回所有已 commit 的章号。"""
    commits_dir = project_root / ".story-system" / "commits"
    if not commits_dir.is_dir():
        return set()
    chapters = set()
    for f in commits_dir.glob("chapter_*.commit.json"):
        m = re.match(r"^chapter_0*(\d+)\.commit\.json$", f.name)
        if m:
            chapters.add(int(m.group(1)))
    return chapters


def run_check(project_root: Path) -> dict:
    """健康检查：报告章纲/JSON/commit 的完整性和异常。"""
    outlines = _find_all_chapters_with_outlines(project_root)
    jsons = _find_all_chapters_with_json(project_root)
    commits = _find_all_committed_chapters(project_root)
    min_start, max_end = _find_chapter_range_from_state(project_root)

    all_known = outlines | jsons | commits
    if max_end > 0:
        all_known |= set(range(min_start, max_end + 1))

    missing_outlines = sorted(c for c in all_known if c not in outlines)
    missing_jsons = sorted(c for c in all_known if c not in jsons)
    orphan_jsons = sorted(c for c in (jsons - outlines))
    empty_drafts = []
    ai_dir = project_root / "AI生成"
    if ai_dir.is_dir():
        for f in ai_dir.glob("第*章.md"):
            if f.stat().st_size == 0:
                m = re.match(r"^第0*(\d+)章\.md$", f.name)
                if m:
                    empty_drafts.append(int(m.group(1)))

    report = {
        "total_outlines": len(outlines),
        "total_jsons": len(jsons),
        "total_commits": len(commits),
        "chapter_range_from_state": f"{min_start}-{max_end}" if max_end > 0 else "未登记",
        "missing_outlines": missing_outlines,
        "missing_jsons": missing_jsons,
        "orphan_jsons": orphan_jsons,
        "empty_drafts": empty_drafts,
        "healthy": not (missing_outlines or missing_jsons or orphan_jsons or empty_drafts),
    }
    return report


def run_auto_fill(project_root: Path) -> dict:
    """从卷级详细大纲自动拆分缺失的章纲.md 文件。"""
    outlines = _find_all_chapters_with_outlines(project_root)
    min_start, max_end = _find_chapter_range_from_state(project_root)
    if max_end == 0:
        jsons = _find_all_chapters_with_json(project_root)
        commits = _find_all_committed_chapters(project_root)
        all_known = outlines | jsons | commits
        if not all_known:
            return {"filled": 0, "message": "无已知章节，请先运行 plan"}
        min_start = min(all_known)
        max_end = max(all_known)

    missing = sorted(c for c in range(min_start, max_end + 1) if c not in outlines)
    filled = 0
    for ch in missing:
        content = load_chapter_outline(project_root, ch, max_chars=None)
        if content and not content.startswith("⚠️"):
            md_path = project_root / "大纲" / f"第{ch:04d}章-章纲.md"
            md_path.parent.mkdir(parents=True, exist_ok=True)
            md_path.write_text(content, encoding="utf-8")
            print(f"  ch{ch}: ✓ 从卷级大纲拆分章纲")
            filled += 1
        else:
            print(f"  ch{ch}: ✗ 无法提取（{content}）")
    return {"filled": filled, "attempted": len(missing)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--project-root", required=True)
    ap.add_argument("--start", type=int, default=1)
    ap.add_argument("--end", type=int, default=50)
    ap.add_argument("--force", action="store_true", help="覆盖已存在的章节契约")
    ap.add_argument("--check", action="store_true", help="健康检查模式：报告缺失和异常，不写入")
    ap.add_argument("--missing-only", action="store_true", help="只注册缺失 JSON 的章节（自动检测范围）")
    ap.add_argument("--auto-fill", action="store_true", help="从卷级大纲拆分缺失章纲.md，不注册 JSON")
    args = ap.parse_args()

    root = Path(args.project_root)

    # ── 健康检查模式 ──
    if args.check:
        report = run_check(root)
        print("=" * 50)
        print("章纲健康检查")
        print("=" * 50)
        print(f"  章纲.md 总数: {report['total_outlines']}")
        print(f"  chapter JSON 总数: {report['total_jsons']}")
        print(f"  commit 总数: {report['total_commits']}")
        print(f"  登记章范围: {report['chapter_range_from_state']}")
        if report["missing_outlines"]:
            print(f"  ❌ 缺章纲.md: {report['missing_outlines']}")
        if report["missing_jsons"]:
            print(f"  ❌ 缺 chapter JSON: {report['missing_jsons']}")
        if report["orphan_jsons"]:
            print(f"  ⚠️ 孤JSON（有JSON无章纲）: {report['orphan_jsons']}")
        if report["empty_drafts"]:
            print(f"  ❌ 0字节草稿: {report['empty_drafts']}")
        if report["healthy"]:
            print("  ✅ 全部健康，无异常")
        print("=" * 50)
        # 返回 JSON 供脚本消费
        print(json.dumps(report, ensure_ascii=False))
        return

    # ── 自动填充模式 ──
    if args.auto_fill:
        result = run_auto_fill(root)
        print(f"\n完成：填充 {result['filled']}/{result['attempted']} 个缺失章纲")
        return

    # ── 只补缺失模式 ──
    if args.missing_only:
        existing = _find_all_chapters_with_json(root)
        outlines = _find_all_chapters_with_outlines(root)
        all_known = existing | outlines
        if not all_known:
            print("无已知章节，请先运行 plan 或指定 --start/--end")
            return
        start = min(all_known)
        end = max(all_known)
        todo = sorted(c for c in range(start, end + 1) if c not in existing)
        if not todo:
            print(f"所有 {len(all_known)} 个已知章节都已有 JSON，无需补注册")
            return
        print(f"检测到 {len(todo)} 个缺失 JSON 的章节: {todo}")
    else:
        todo = list(range(args.start, args.end + 1))

    folder = root / ".story-system" / "chapters"
    folder.mkdir(parents=True, exist_ok=True)

    created, updated, skipped = 0, 0, 0
    for ch in todo:
        path = folder / f"chapter_{ch:03d}.json"
        directive = load_chapter_execution_directive(root, ch)
        if not directive:
            print(f"  ch{ch}: ⚠️ 抽不到 directive，跳过")
            skipped += 1
            continue

        if path.exists() and not args.force:
            # 已存在：只补 chapter_directive（不动其他字段）
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                data = {}
            if data.get("chapter_directive"):
                skipped += 1
                continue
            data["chapter_directive"] = directive
            data.setdefault("meta", {})["chapter"] = ch
            path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            updated += 1
            print(f"  ch{ch}: ✓ 补 directive")
        else:
            data = {
                "meta": {
                    "schema_version": "story-system/v1",
                    "contract_type": "CHAPTER_BRIEF",
                    "generator_version": "plan-register",
                    "chapter": ch,
                },
                "override_allowed": {},
                "chapter_directive": directive,
            }
            path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            created += 1
            print(f"  ch{ch}: ✓ 新建")

    print(f"\n完成：新建 {created}，补 directive {updated}，跳过 {skipped}")


if __name__ == "__main__":
    main()
