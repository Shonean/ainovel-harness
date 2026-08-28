# -*- coding: utf-8 -*-
"""Append-only 运行记录（rollout）+ 断点续跑基础设施。

借鉴 codex rollout/ 模式：长任务（批量生成、榨干提取）的每一步以事件形式
追加写入书内 `<书根>/.ainovel/runs/<run_id>.jsonl`，重启后可倒扫定位最后完整
步骤续跑，而不是全丢。仅追加、从不修改已有记录（审计轨迹完整）。

设计要点：
- arcs.json / AI生成/*.md 仍是"成果真相源"（finalize_chapter 已落盘）；
  rollout 记录的是"过程与意图"（跑什么参数、到哪一步、为何中断）。
- 记录是 JSONL 一行一事件，崩溃时最多丢最后一条未 flush 的（写后 flush，
  实际几乎不丢）。
- 倒扫用 64KB 块从文件尾读（参考 codex reverse_jsonl_scanner），O(尾部)
  即可取最后状态，不读全文件。

事件结构：
    {ts, run_id, kind, event, step?, arc?, chapter?, data?}
  event ∈ run_started | step_started | step_completed | step_skipped
          | run_completed | run_failed | run_aborted
首条 run_started 带 params（target_chapters 等），供 resume 复用。
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

_READ_CHUNK = 64 * 1024


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _runs_dir(book_root: str | Path) -> Path:
    d = Path(book_root) / ".ainovel" / "runs"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _run_path(book_root: str | Path, run_id: str) -> Path:
    return _runs_dir(book_root) / f"{run_id}.jsonl"


def new_run_id() -> str:
    return uuid.uuid4().hex[:12]


def append_event(
    book_root: str | Path,
    run_id: str,
    event: str,
    *,
    kind: str = "",
    step: str = "",
    arc: str = "",
    chapter: int | None = None,
    data: dict[str, Any] | None = None,
) -> None:
    """追加一条事件到 run 的 JSONL（写后 flush；失败静默不影响主流程）。"""
    rec: dict[str, Any] = {
        "ts": _now(),
        "run_id": run_id,
        "kind": kind,
        "event": event,
    }
    if step:
        rec["step"] = step
    if arc:
        rec["arc"] = arc
    if chapter is not None:
        rec["chapter"] = chapter
    if data:
        rec["data"] = data
    try:
        with open(_run_path(book_root, run_id), "a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")
    except Exception:
        pass  # 记录失败绝不影响生成主流程


def reverse_scan(path: Path, max_records: int = 0) -> Iterator[dict[str, Any]]:
    """从 JSONL 文件尾部倒序产出记录（64KB 块，避免读全文件）。

    max_records>0 时只产最后 N 条。损坏/半截行跳过。
    """
    if not path.is_file():
        return
    size = path.stat().st_size
    if size == 0:
        return
    produced = 0
    leftover = ""
    pos = size
    with open(path, "rb") as fh:
        while pos > 0:
            read_size = min(_READ_CHUNK, pos)
            pos -= read_size
            fh.seek(pos)
            chunk = fh.read(read_size).decode("utf-8", errors="replace")
            # 按换行切：块首可能是半行，与上一块的 leftover 拼接
            lines = chunk.split("\n")
            lines[-1] = lines[-1] + leftover
            leftover = lines[0]
            for line in reversed(lines[1:]):
                line = line.strip()
                if not line:
                    continue
                try:
                    yield json.loads(line)
                    produced += 1
                    if max_records and produced >= max_records:
                        return
                except json.JSONDecodeError:
                    continue
        # 文件最首行（leftover）
        leftover = leftover.strip()
        if leftover:
            try:
                yield json.loads(leftover)
            except json.JSONDecodeError:
                pass


def load_run_meta(book_root: str | Path, run_id: str) -> dict[str, Any] | None:
    """读一个 run 的首条(run_started)与末态，用于续跑/展示。"""
    path = _run_path(book_root, run_id)
    if not path.is_file():
        return None
    started: dict[str, Any] | None = None
    last_events: list[dict[str, Any]] = []
    # 首条：正向读前若干字节即可（run_started 在第一行）
    try:
        with open(path, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    started = json.loads(line)
                    break
    except Exception:
        pass
    # 末态：倒扫最后 1 条
    for rec in reverse_scan(path, max_records=1):
        last_events.append(rec)
    last = last_events[0] if last_events else {}
    terminal = {"run_completed", "run_failed", "run_aborted"}
    return {
        "run_id": run_id,
        "kind": (started or {}).get("kind", ""),
        "params": (started or {}).get("data", {}),
        "started_at": (started or {}).get("ts"),
        "last_event": last.get("event"),
        "last_step": last.get("step", ""),
        "last_ts": last.get("ts"),
        "stale_running": bool(started) and last.get("event") not in terminal,
    }


def list_runs(book_root: str | Path, limit: int = 20) -> list[dict[str, Any]]:
    """列出书内最近的 run（按文件 mtime 倒序），含末态。"""
    out: list[dict[str, Any]] = []
    try:
        files = sorted(
            _runs_dir(book_root).glob("*.jsonl"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
    except Exception:
        return out
    for p in files[:limit]:
        rid = p.stem
        meta = load_run_meta(book_root, rid)
        if meta:
            out.append(meta)
    return out


def find_resumable(book_root: str | Path, kind: str = "batch_generate") -> dict[str, Any] | None:
    """找该书最近一个未正常结束的 run（可续跑）。无则 None。"""
    for meta in list_runs(book_root, limit=10):
        if meta.get("kind") == kind and meta.get("stale_running"):
            return meta
    return None
