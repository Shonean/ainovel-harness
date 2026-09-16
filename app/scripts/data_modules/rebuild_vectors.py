#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
rebuild_vectors.py — 回填/重建向量库 (.ainovel/vectors.db)。

遍历 .story-system/commits/chapter_*.commit.json，逐个用 VectorProjectionWriter
把 event / entity_delta 切块并 embed 入库（复用 RAGAdapter.store_chunks）。

用法：
    python -X utf8 -m data_modules.rebuild_vectors --project-root <root>
    python -X utf8 -m data_modules.rebuild_vectors --project-root <root> --chapter 5
    python -X utf8 -m data_modules.rebuild_vectors --project-root <root> --rebuild

--rebuild：先清空 vectors / bm25_index / doc_stats 再重灌。
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time
from pathlib import Path

from runtime_compat import enable_windows_utf8_stdio


def _find_project_root(arg: str | None) -> Path:
    if arg:
        from project_locator import resolve_project_root
        return resolve_project_root(arg)
    from project_locator import resolve_project_root
    return resolve_project_root()


def _clear_vector_db(project_root: Path) -> None:
    db = project_root / ".ainovel" / "vectors.db"
    if not db.exists():
        return
    with sqlite3.connect(str(db)) as conn:
        for tbl in ("vectors", "bm25_index", "doc_stats"):
            try:
                conn.execute(f"DELETE FROM {tbl}")
            except sqlite3.OperationalError:
                pass
        conn.commit()
    print(f"[rebuild] 已清空 vectors.db 各表")


def _iter_commit_files(project_root: Path, chapter: int | None):
    commits_dir = project_root / ".story-system" / "commits"
    if not commits_dir.is_dir():
        return
    files = sorted(commits_dir.glob("chapter_*.commit.json"))
    if chapter is not None:
        files = [f for f in files if f.name == f"chapter_{chapter:03d}.commit.json"]
    for f in files:
        yield f


def main() -> int:
    from data_modules.config import load_user_env
    load_user_env()
    parser = argparse.ArgumentParser(description="回填/重建向量库")
    parser.add_argument("--project-root", type=str, default=None, help="项目根目录")
    parser.add_argument("--chapter", type=int, default=None, help="只回填指定章")
    parser.add_argument("--rebuild", action="store_true", help="先清空 vectors.db 再重灌")
    args = parser.parse_args()

    project_root = _find_project_root(args.project_root)
    print(f"[rebuild] project_root={project_root}")

    # 检查 embedding 配置
    from data_modules.config import DataModulesConfig
    cfg = DataModulesConfig.from_project_root(project_root)
    if not (cfg.embed_api_key or "").strip():
        print("[rebuild] ⚠️ 未配置 EMBED_API_KEY，回填将无向量入库（仅 BM25 索引）。"
              "请在 .env 配置 EMBED_API_KEY 后重跑。", file=sys.stderr)

    if args.rebuild:
        _clear_vector_db(project_root)

    from data_modules.projections import VectorProjectionWriter
    writer = VectorProjectionWriter(project_root)

    files = list(_iter_commit_files(project_root, args.chapter))
    if not files:
        print("[rebuild] 没有找到 commit JSON（.story-system/commits/chapter_*.commit.json）")
        return 0

    total_stored = 0
    total_skipped = 0
    for f in files:
        try:
            payload = json.loads(f.read_text(encoding="utf-8"))
        except Exception as exc:
            print(f"[rebuild] {f.name} 读取失败: {exc}", file=sys.stderr)
            continue
        result = writer.apply(payload)
        stored = result.get("stored", 0) if isinstance(result, dict) else 0
        applied = result.get("applied", False) if isinstance(result, dict) else False
        reason = result.get("reason", "") if isinstance(result, dict) else ""
        total_stored += int(stored or 0)
        marker = "✓" if applied else "·"
        print(f"[rebuild] {marker} {f.name} stored={stored} {reason}")
        # 刷新 stdout 供 SSE 流式
        sys.stdout.flush()

    print(f"[rebuild] 完成：共 {len(files)} 章，入库 {total_stored} 条向量")

    # 输出统计 JSON 供前端解析
    try:
        from data_modules.rag_adapter import RAGAdapter
        adapter = RAGAdapter(cfg)
        stats = adapter.get_stats()
        print(json.dumps({"status": "ok", "files": len(files),
                          "stored": total_stored, "stats": stats}, ensure_ascii=False))
    except Exception as exc:
        adapter = None
        print(json.dumps({"status": "ok", "files": len(files),
                          "stored": total_stored, "stats_error": str(exc)}, ensure_ascii=False))

    # 关闭底层 aiohttp session，避免「Unclosed client session」告警
    try:
        import asyncio
        api_client = getattr(adapter, "api_client", None) if adapter else None
        if api_client is not None and hasattr(api_client, "close"):
            try:
                asyncio.run(api_client.close())
            except RuntimeError:
                # 已在事件循环内时退化为创建新循环关闭
                loop = asyncio.new_event_loop()
                loop.run_until_complete(api_client.close())
                loop.close()
    except Exception:
        pass
    return 0


if __name__ == "__main__":
    if sys.platform == "win32":
        enable_windows_utf8_stdio()
    raise SystemExit(main())
