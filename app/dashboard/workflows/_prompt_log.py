"""
_prompt_log.py - 捕获实际发出的 prompt（Prompt 审阅台后端基础）

在 AnthropicAgentRunner.run() 进入主循环前调用 log_prompt()，把每次 LLM 调用
的 system_prompt + user_input 落盘到 <书>/.ainovel/prompts/log.jsonl。

- 仅记首轮 system+user（即「发给模型的 prompt」）；多轮 tool-use agent 的后续
  tool 消息不记（用户关心的是 prompt 本体）。
- 单条 JSON 一行（JSONL），原子追加。
- 软上限滚动：超 5MB 保留最后 2000 条，避免无限膨胀。
- read_log() 按 stage/chapter 过滤，返回最近记录（latest first）。
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path


_MAX_BYTES = 5 * 1024 * 1024  # 5MB
_KEEP_LINES = 2000


def _log_path(project_root: Path) -> Path:
    return project_root / ".ainovel" / "prompts" / "log.jsonl"


def log_prompt(
    project_root: Path,
    agent_name: str,
    system_prompt: str,
    user_input: str,
    model: str,
    meta: dict | None = None,
) -> None:
    """追加一条 prompt 记录。失败静默（捕获不能影响主流程）。"""
    try:
        meta = meta or {}
        rec = {
            "ts": time.time(),
            "iso": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
            "stage": meta.get("stage") or agent_name,
            "chapter": meta.get("chapter"),
            "batch": meta.get("batch"),
            "model": model,
            "agent_name": agent_name,
            "system_prompt": system_prompt or "",
            "user_input": user_input or "",
        }
        path = _log_path(project_root)
        path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(rec, ensure_ascii=False)
        # 原子追加：单行 write
        with path.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
        # 软上限滚动
        try:
            if path.stat().st_size > _MAX_BYTES:
                _rotate(path)
        except OSError:
            pass
    except Exception:
        # 捕获失败绝不阻断写作主流程
        return


def _rotate(path: Path) -> None:
    """保留最后 _KEEP_LINES 行，原子替换。"""
    try:
        with path.open("r", encoding="utf-8") as f:
            lines = f.readlines()
        if len(lines) <= _KEEP_LINES:
            return
        kept = lines[-_KEEP_LINES:]
        tmp = path.with_suffix(path.suffix + ".tmp")
        with tmp.open("w", encoding="utf-8") as f:
            f.writelines(kept)
        os.replace(tmp, path)
    except Exception:
        return


def read_log(
    project_root: Path,
    stage: str | None = None,
    chapter: int | None = None,
    limit: int = 50,
) -> list[dict]:
    """读最近记录（latest first）。可按 stage/chapter 过滤。"""
    path = _log_path(project_root)
    if not path.is_file():
        return []
    try:
        with path.open("r", encoding="utf-8") as f:
            lines = f.readlines()
    except OSError:
        return []

    out: list[dict] = []
    # 从末尾向前扫，命中即收，够 limit 即停
    for line in reversed(lines):
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        if stage and rec.get("stage") != stage:
            continue
        # chapter 过滤：chapter 为 None 的记录（如 plan）在指定 chapter 时不命中；
        # 未指定 chapter 时全部放行
        if chapter is not None and rec.get("chapter") != chapter:
            continue
        out.append(rec)
        if len(out) >= limit:
            break
    return out


def latest_for(project_root: Path, stage: str, chapter: int | None) -> dict | None:
    """取某 (stage, chapter) 的最近一条记录。"""
    items = read_log(project_root, stage=stage, chapter=chapter, limit=1)
    return items[0] if items else None
