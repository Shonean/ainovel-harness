"""
约束检查步骤 —— 从 workflows.py 拆分。
"""
from __future__ import annotations

import json
from pathlib import Path

from ..core.constants import FreedomLevel, DEFAULT_FREEDOM_LEVEL
from ..services.constraint_engine import ConstraintEngine
from ..services.task_manager import TASKS, Task


async def _run_constraint_check(
    task: Task, *,
    project_root: Path,
    chapter: int,
    content_path: Path,
    freedom_level: FreedomLevel = DEFAULT_FREEDOM_LEVEL,
    tmp_dir: Path | None = None,
) -> dict:
    """
    Phase 2: 约束检查步骤。

    加载 ConstraintEngine，对草稿文本运行活跃规则（按自由度过滤），
    结果持久化到 .ainovel/tmp/constraint_audit.json。

    返回值：
      - "passed": bool — True 表示无 hard 违规
      - "total_violations": int
      - "hard_count": int
      - "violations": list[dict] — 所有违规详情
    """
    await TASKS.emit(task, {"phase": "step", "step": "constraint-check", "status": "running"})

    try:
        engine = ConstraintEngine()
        text = content_path.read_text(encoding="utf-8")
        violations = engine.validate(text, freedom_level=freedom_level)
        summary = engine.get_summary(violations)
    except Exception as exc:
        await TASKS.emit(task, {
            "phase": "step", "step": "constraint-check", "status": "failed",
            "error": str(exc),
        })
        return {"passed": True, "total_violations": 0, "hard_count": 0, "violations": [], "error": str(exc)}

    output = {
        "chapter": chapter,
        "freedom_level": freedom_level.value,
        "total_violations": summary["total"],
        "hard_count": summary["hard_count"],
        "violations": [v.to_dict() for v in violations],
        "passed": summary["passed"],
    }

    td = tmp_dir if tmp_dir else project_root / ".ainovel" / "tmp"
    td.mkdir(parents=True, exist_ok=True)
    out_path = td / "constraint_audit.json"
    out_path.write_text(
        json.dumps(output, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    await TASKS.emit(task, {
        "phase": "step", "step": "constraint-check", "status": "done",
        "total_violations": summary["total"],
        "hard_count": summary["hard_count"],
        "passed": summary["passed"],
    })

    return output
