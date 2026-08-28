# -*- coding: utf-8 -*-
"""v5.x 逆向推理 run 记录：扫描 logs/runs/*.json 并提供列表/读取。

背景：RunLogger.list_logs 只扫 *.jsonl（v3.x 运行日志 / sessions / tasks），
而 v5.x reverse-infer run 是单文件 JSON（logs/runs/reverse_infer_*.json），
前端任务记录页签需要结构化访问这些 run。这里单独实现扫描 + 汇总。

- 汇总只抽小字段（best_score/mode/chapter_count/candidates 分数），不返回全文。
- 进程内 mtime 缓存：同一 run 文件未变则复用汇总，避免重复 json.load 大文件。
- 文件名安全：仅接受 reverse_infer_*.json 且 resolve 后仍在 logs/runs/ 内。
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

RUN_FILENAME_RE = re.compile(r"^reverse_infer_.+\.json$")

# key = 绝对路径 str -> (mtime, summary)
_cache: dict[str, tuple[float, dict[str, Any]]] = {}


def _runs_dir() -> Path:
    return Path(__file__).resolve().parent.parent / "logs" / "runs"


def _safe_run_path(filename: str) -> Path | None:
    """仅允许 logs/runs/ 下的 reverse_infer_*.json，防路径穿越。"""
    if not RUN_FILENAME_RE.match(filename or ""):
        return None
    d = _runs_dir().resolve()
    p = (d / filename).resolve()
    if not p.is_file() or p.parent != d:
        return None
    return p


def _summarize(run: dict[str, Any]) -> dict[str, Any]:
    opt = run.get("optimization") or {}
    cands = run.get("candidates") or []
    return {
        "best_score": opt.get("best_score"),
        "mode": opt.get("mode", ""),
        "chapter_count": len(run.get("target_texts") or []) or None,
        "saved_at": run.get("saved_at", ""),
        "source_file": run.get("source_file", ""),
        "candidates": [
            {
                "idx": i,
                "score": c.get("score"),
                "s_char": c.get("s_char"),
                "turn_fidelity": c.get("turn_fidelity"),
                "plot_sim": c.get("plot_sim"),
                "rep_chapter_idx": c.get("rep_chapter_idx"),
            }
            for i, c in enumerate(cands)
        ],
    }


def list_runs(limit: int = 50) -> list[dict[str, Any]]:
    """列出全部 v5.x run 汇总，按修改时间倒序。"""
    d = _runs_dir()
    if not d.is_dir():
        return []
    out: list[dict[str, Any]] = []
    for f in sorted(
        d.glob("reverse_infer_*.json"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    ):
        try:
            mtime = f.stat().st_mtime
            key = str(f.resolve())
            cached = _cache.get(key)
            if cached and cached[0] == mtime:
                summary = cached[1]
            else:
                run = json.loads(f.read_text(encoding="utf-8"))
                summary = _summarize(run)
                _cache[key] = (mtime, summary)
        except Exception:
            summary = {}
        summary.setdefault("filename", f.name)
        out.append(summary)
        if len(out) >= limit:
            break
    return out


def read_run(filename: str) -> dict[str, Any] | None:
    """读取单个 run 完整 JSON；非法文件名/不存在/解析失败返回 None。"""
    p = _safe_run_path(filename)
    if p is None:
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None
