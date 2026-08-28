#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""chapter_reset_service — 统一重置 chapter N 的所有持久化副作用。

用途：在 /ainovel-write 重写 chapter N 之前调用，撤销上一版 commit 写入的所有派生数据，
切断 RAG / memory / summary / story_system 自馈循环，确保新写不再受旧版「剧情事实」影响。

不删除：
- chapter_directive/chapter_NNN.json（plan 唯一写，由 /ainovel-plan 决定）
- commit_history/chapter_NNN.commit.json（用户已审定的 commit 记录，由 unfinalize 负责清理）
- chapter_meta（state.json 顶层）
- style_samples.db（保留以备 finalize 复用）

只清理：
- vectors.db 中 chapter = N 的所有 chunks（同步删 faiss_index / doc_stats 关联行）
- memory_scratchpad.json 中 source_chapter = N 的 active items（标 outdated，不删）
- summaries/chNNNN.md（旧版摘要）
- state.json 中 chapter N 的 chapter_status / strand_tracker history
- .story-system/下 chapter N 的所有合同数据（commits/events/chapters/reviews）
- 大纲/第NNNN章-章纲.md（旧章纲）
- .ainovel/tmp/下包含章节号的临时文件

CLI 用法：
    python -m scripts.chapter_reset_service <chapter> [--project-root PATH] [--dry-run] [--purge-directive]
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path
from typing import Any, Dict, List

# scripts/与 data_modules/同级（data_modules/在 scripts/内部），
# python -m scripts.xxx 启动时需要把 scripts/加进 sys.path
_PKG_PARENT = Path(__file__).resolve().parent
if str(_PKG_PARENT) not in sys.path:
    sys.path.insert(0, str(_PKG_PARENT))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("chapter_reset_service")


def _resolve_project_root(arg: str | None) -> Path:
    """统一使用 project_locator.resolve_project_root()（Phase 3 去重）。"""
    if arg:
        return Path(arg).resolve()
    try:
        from project_locator import resolve_project_root
        return resolve_project_root()
    except ImportError:
        # 兜底：project_locator 不在 path 时回退到简单逻辑
        import os
        env = os.environ.get("AINOVEL_PROJECT_ROOT")
        if env:
            return Path(env).resolve()
        cwd = Path.cwd()
        if (cwd / ".ainovel" / "state.json").exists():
            return cwd.resolve()
        raise SystemExit("ERROR: 无法定位 PROJECT_ROOT（--project-root 或 .ainovel/state.json 父目录）")


def reset_chapter(project_root: Path, chapter: int, purge_directive: bool = False) -> Dict[str, Any]:
    """重置章节 N 的所有持久化副作用。

    Args:
        project_root: 项目根目录
        chapter: 要重置的章节号
        purge_directive: 是否同时删除 chapter_directive.json（默认不删，由plan负责更新）

    Returns: {"chapter": N, "actions": [...], "errors": [...]}
    """
    if chapter <= 0:
        raise ValueError(f"chapter 必须 > 0，得到 {chapter}")

    actions: List[Dict[str, Any]] = []
    errors: List[Dict[str, str]] = []

    # 1. vectors.db
    try:
        from data_modules.projections import VectorProjectionWriter
        deleted = VectorProjectionWriter.purge_chapter(project_root, chapter)
        actions.append({"writer": "vector", "deleted_chunks": deleted})
    except Exception as exc:
        logger.exception("vectors purge failed")
        errors.append({"writer": "vector", "error": str(exc)})

    # 2. memory_scratchpad.json
    try:
        from data_modules.projections import MemoryProjectionWriter
        marked = MemoryProjectionWriter.purge_chapter(project_root, chapter)
        actions.append({"writer": "memory", "marked_outdated": marked})
    except Exception as exc:
        logger.exception("memory purge failed")
        errors.append({"writer": "memory", "error": str(exc)})

    # 3. summaries/chNNNN.md
    try:
        from data_modules.projections import SummaryProjectionWriter
        purged = SummaryProjectionWriter.purge_chapter(project_root, chapter)
        actions.append({"writer": "summary", "purged": purged})
    except Exception as exc:
        logger.exception("summary purge failed")
        errors.append({"writer": "summary", "error": str(exc)})

    # 4. state.json（部分清理）
    try:
        from data_modules.projections import StateProjectionWriter
        result = StateProjectionWriter.purge_chapter(project_root, chapter)
        actions.append({"writer": "state", **result})
    except Exception as exc:
        logger.exception("state purge failed")
        errors.append({"writer": "state", "error": str(exc)})

    # 5. .story-system/下对应章节的所有数据（核心污染源！）
    try:
        story_system_dir = project_root / ".story-system"
        deleted_files = []
        chapter_str = f"{chapter:04d}"
        # 清理章节commit
        commit_dir = story_system_dir / "commits"
        if commit_dir.exists():
            for commit_file in commit_dir.glob(f"chapter_{chapter_str}*"):
                commit_file.unlink()
                deleted_files.append(commit_file.name)
        # 清理章节events
        event_dir = story_system_dir / "events"
        if event_dir.exists():
            for event_file in event_dir.glob(f"chapter_{chapter_str}*"):
                event_file.unlink()
                deleted_files.append(event_file.name)
        # 清理章节合同
        chapter_dir = story_system_dir / "chapters"
        if chapter_dir.exists():
            for chapter_file in chapter_dir.glob(f"chapter_{chapter_str}*"):
                chapter_file.unlink()
                deleted_files.append(chapter_file.name)
        # 清理章节review
        review_dir = story_system_dir / "reviews"
        if review_dir.exists():
            for review_file in review_dir.glob(f"chapter_{chapter_str}*"):
                review_file.unlink()
                deleted_files.append(review_file.name)
        actions.append({"writer": "story_system", "deleted_files": deleted_files, "count": len(deleted_files)})
    except Exception as exc:
        logger.exception("story_system purge failed")
        errors.append({"writer": "story_system", "error": str(exc)})

    # 6. 大纲/下对应章节的旧章纲文件
    try:
        outline_dir = project_root / "大纲"
        deleted_outline_files = []
        chapter_str = f"{chapter:04d}"
        if outline_dir.exists():
            for outline_file in outline_dir.glob(f"第{chapter_str}章-章纲.md"):
                outline_file.unlink()
                deleted_outline_files.append(outline_file.name)
        actions.append({"writer": "outline", "deleted_files": deleted_outline_files, "count": len(deleted_outline_files)})
    except Exception as exc:
        logger.exception("outline purge failed")
        errors.append({"writer": "outline", "error": str(exc)})

    # 7. .ainovel/tmp/下对应章节的临时文件
    try:
        tmp_dir = project_root / ".ainovel" / "tmp"
        deleted_tmp_files = []
        chapter_str = f"{chapter:04d}"
        if tmp_dir.exists():
            for tmp_file in tmp_dir.glob(f"*{chapter_str}*"):
                if tmp_file.is_file():
                    tmp_file.unlink()
                    deleted_tmp_files.append(tmp_file.name)
        actions.append({"writer": "tmp", "deleted_files": deleted_tmp_files, "count": len(deleted_tmp_files)})
    except Exception as exc:
        logger.exception("tmp purge failed")
        errors.append({"writer": "tmp", "error": str(exc)})

    # 8. 可选清理 chapter_directive.json（默认不删）
    if purge_directive:
        try:
            directive_dir = project_root / ".ainovel" / "tmp" / "chapter_directives"
            deleted_directive_files = []
            chapter_str = f"{chapter:04d}"
            if directive_dir.exists():
                for directive_file in directive_dir.glob(f"chapter_{chapter_str}.json"):
                    directive_file.unlink()
                    deleted_directive_files.append(directive_file.name)
            actions.append({"writer": "directive", "deleted_files": deleted_directive_files, "count": len(deleted_directive_files)})
        except Exception as exc:
            logger.exception("directive purge failed")
            errors.append({"writer": "directive", "error": str(exc)})

    summary = {
        "chapter": chapter,
        "project_root": str(project_root),
        "actions": actions,
        "errors": errors,
    }
    logger.info("chapter %d reset: %d actions, %d errors", chapter, len(actions), len(errors))
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description="重置章节的持久化副作用（切断重写自馈循环）")
    parser.add_argument("chapter", type=int, help="要重置的章节号（>0）")
    parser.add_argument("--project-root", type=str, default=None, help="项目根目录（默认从 .ainovel/state.json 反查）")
    parser.add_argument("--dry-run", action="store_true", help="只打印会执行的动作，不实际修改")
    parser.add_argument("--purge-directive", action="store_true", help="同时删除 chapter_directive.json（默认保留）")
    args = parser.parse_args()

    try:
        project_root = _resolve_project_root(args.project_root)
    except SystemExit as e:
        print(str(e), file=sys.stderr)
        return 2

    if not (project_root / ".ainovel").exists():
        print(f"ERROR: {project_root}/.ainovel 不存在，不是 ainovel 项目", file=sys.stderr)
        return 2

    if args.dry_run:
        actions = [
            {"writer": "vector", "action": "删除 vectors.db 中 chapter = %d 的所有 chunks" % args.chapter},
            {"writer": "memory", "action": "标记 memory_scratchpad 中 source_chapter = %d 的所有 active items 为 outdated" % args.chapter},
            {"writer": "summary", "action": "删除 summaries/ch%04d.md" % args.chapter},
            {"writer": "state", "action": "清理 state.json 中 chapter = %d 的 chapter_status / strand_tracker history" % args.chapter},
            {"writer": "story_system", "action": "删除 .story-system/下所有 chapter = %d 的合同数据（commits/events/chapters/reviews）" % args.chapter},
            {"writer": "outline", "action": "删除 大纲/第%04d章-章纲.md" % args.chapter},
            {"writer": "tmp", "action": "删除 .ainovel/tmp/下所有包含 %04d 的临时文件" % args.chapter},
        ]
        if args.purge_directive:
            actions.append({"writer": "directive", "action": "删除 chapter_directive/chapter_%04d.json" % args.chapter})
        print(json.dumps({
            "dry_run": True,
            "chapter": args.chapter,
            "project_root": str(project_root),
            "would_purge": actions,
        }, ensure_ascii=False, indent=2))
        return 0

    result = reset_chapter(project_root, args.chapter, args.purge_directive)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if not result["errors"] else 1


if __name__ == "__main__":
    sys.exit(main())
