"""
_progress.py — 章节 write 流程的 checkpoint 进度快照写入器。

设计要点
----------
- 进度文件：{project_root}/.ainovel/tmp/chapter_{N:04d}/progress.json
- 原子写：先写 .tmp 再 os.replace，崩溃中途不破坏文件
- 每个 step 状态：pending / running / done / failed
- done 状态下记录 output_paths；resume 时校验文件仍在磁盘才跳过
- 顶层记录 mode 快照（fast / minimal / model / temperature / freedom_level / auto_generate）
  —— resume 时回放，避免用户重传
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Optional

# 12 个 write step 的固定顺序（与 _write.py 一致）
STEP_ORDER = [
    "preflight",
    "reset",
    "context",
    "character",
    "draft",
    "constraint",
    "review",
    "polish",
    "re_review",
    "data",
    "commit",
    "backup",
]


class ProgressRecorder:
    """单章 write 流程的进度快照写入器。"""

    def __init__(self, project_root: Path, chapter: int):
        self.project_root = Path(project_root)
        self.chapter = int(chapter)
        self.path = (
            self.project_root
            / ".ainovel"
            / "tmp"
            / f"chapter_{self.chapter:04d}"
            / "progress.json"
        )
        now = time.time()
        self.data: dict[str, Any] = {
            "chapter": self.chapter,
            "project_root": str(self.project_root),
            "started_at": now,
            "updated_at": now,
            "mode": {},
            "workflow_status": "running",  # running / done / failed
            "workflow_error": "",
            "steps": {
                sid: {
                    "status": "pending",
                    "started_at": None,
                    "finished_at": None,
                    "output_paths": [],
                    "error": "",
                }
                for sid in STEP_ORDER
            },
        }

    # ------------------------------------------------------------------
    # 持久化
    # ------------------------------------------------------------------

    def save(self) -> None:
        """原子写：先写 .tmp 再 replace。"""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.data["updated_at"] = time.time()
        tmp_path = self.path.with_suffix(".json.tmp")
        tmp_path.write_text(
            json.dumps(self.data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        os.replace(tmp_path, self.path)

    @classmethod
    def load_existing(
        cls, project_root: Path, chapter: int
    ) -> Optional["ProgressRecorder"]:
        """从磁盘加载已有进度文件；不存在返回 None。"""
        rec = cls(project_root, chapter)
        if not rec.path.is_file():
            return None
        try:
            rec.data = json.loads(rec.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        # 兼容旧文件：缺失的 step 补 pending
        for sid in STEP_ORDER:
            rec.data.setdefault(
                "steps",
                {},
            )
            rec.data["steps"].setdefault(
                sid,
                {
                    "status": "pending",
                    "started_at": None,
                    "finished_at": None,
                    "output_paths": [],
                    "error": "",
                },
            )
        return rec

    @classmethod
    def clear(cls, project_root: Path, chapter: int) -> bool:
        """删除进度文件，返回是否存在并被删除。"""
        path = (
            Path(project_root)
            / ".ainovel"
            / "tmp"
            / f"chapter_{int(chapter):04d}"
            / "progress.json"
        )
        if path.is_file():
            path.unlink()
            return True
        return False

    # ------------------------------------------------------------------
    # 字段操作
    # ------------------------------------------------------------------

    def set_mode(self, mode: dict) -> None:
        """记录本次 write 的 mode 快照，resume 时回放。"""
        self.data["mode"] = dict(mode or {})

    def mark_running(self, step_id: str) -> None:
        step = self.data["steps"].get(step_id)
        if step is None:
            step = {
                "status": "pending",
                "started_at": None,
                "finished_at": None,
                "output_paths": [],
                "error": "",
            }
            self.data["steps"][step_id] = step
        step["status"] = "running"
        step["started_at"] = time.time()
        step["error"] = ""
        self.save()

    def mark_done(self, step_id: str, output_paths: Optional[list] = None) -> None:
        step = self.data["steps"].setdefault(
            step_id,
            {
                "status": "pending",
                "started_at": None,
                "finished_at": None,
                "output_paths": [],
                "error": "",
            },
        )
        step["status"] = "done"
        step["finished_at"] = time.time()
        step["output_paths"] = list(output_paths or [])
        step["error"] = ""
        self.save()

    def mark_failed(self, step_id: str, error: str) -> None:
        step = self.data["steps"].setdefault(
            step_id,
            {
                "status": "pending",
                "started_at": None,
                "finished_at": None,
                "output_paths": [],
                "error": "",
            },
        )
        step["status"] = "failed"
        step["finished_at"] = time.time()
        step["error"] = str(error)[:500]
        self.save()

    def mark_workflow_done(self) -> None:
        self.data["workflow_status"] = "done"
        self.data["workflow_error"] = ""
        self.save()

    def mark_workflow_failed(self, error: str) -> None:
        self.data["workflow_status"] = "failed"
        self.data["workflow_error"] = str(error)[:500]
        self.save()

    # ------------------------------------------------------------------
    # 跳过判定
    # ------------------------------------------------------------------

    def is_step_done(self, step_id: str) -> bool:
        step = self.data["steps"].get(step_id)
        return step is not None and step.get("status") == "done"

    def verify_outputs(self, step_id: str) -> bool:
        """校验 step 记录的所有 output_paths 是否仍存在于磁盘。
        输出列表为空时（如 reset）一律视为 OK。
        """
        step = self.data["steps"].get(step_id)
        if step is None or step.get("status") != "done":
            return False
        paths = step.get("output_paths") or []
        if not paths:
            return True
        for rel in paths:
            p = Path(rel)
            if not p.is_absolute():
                p = self.project_root / p
            if not p.is_file():
                return False
        return True

    def get_done_steps(self) -> list[str]:
        """返回所有 done 且产物仍在磁盘的 step 列表。"""
        return [sid for sid in STEP_ORDER if self.verify_outputs(sid)]

    def get_failed_step(self) -> Optional[str]:
        for sid in STEP_ORDER:
            step = self.data["steps"].get(sid)
            if step and step.get("status") == "failed":
                return sid
        return None

    def summary(self) -> dict:
        """给前端展示用的精简摘要。"""
        steps_out: list[dict] = []
        for sid in STEP_ORDER:
            s = self.data["steps"].get(sid, {})
            steps_out.append(
                {
                    "id": sid,
                    "status": s.get("status", "pending"),
                    "started_at": s.get("started_at"),
                    "finished_at": s.get("finished_at"),
                    "output_paths": s.get("output_paths") or [],
                    "error": s.get("error") or "",
                }
            )
        return {
            "chapter": self.chapter,
            "project_root": str(self.project_root),
            "started_at": self.data.get("started_at"),
            "updated_at": self.data.get("updated_at"),
            "mode": self.data.get("mode") or {},
            "workflow_status": self.data.get("workflow_status", "running"),
            "workflow_error": self.data.get("workflow_error") or "",
            "exists": True,
            "steps": steps_out,
        }
