"""
project_guide.py — 根据项目文件状态推断「下一步该做什么」。

所有判断基于只读文件扫描，不调用 LLM，<10ms 返回。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from ..services.workflow_status import _chapter_json, _commit_json, _find_chapter_md, list_chapters


class ProjectGuide:
    def __init__(self, project_root: Path) -> None:
        self.root = Path(project_root).resolve()

    # ------------------------------------------------------------------
    # 文件存在性快捷
    # ------------------------------------------------------------------

    def _state_file(self) -> Path:
        return self.root / ".ainovel" / "state.json"

    def _master_json(self) -> Path:
        return self.root / ".story-system" / "MASTER_SETTING.json"

    def _final_audit(self) -> Path:
        return self.root / ".ainovel" / "tmp" / "final_audit.json"

    def _chapters_dir(self) -> Path:
        return self.root / ".story-system" / "chapters"

    # ------------------------------------------------------------------
    # 数据读取
    # ------------------------------------------------------------------

    def _load_state(self) -> dict:
        path = self._state_file()
        if not path.is_file():
            return {}
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            return payload if isinstance(payload, dict) else {}
        except (OSError, json.JSONDecodeError):
            return {}

    def _read_final_audit_chapter(self) -> int | None:
        path = self._final_audit()
        if not path.is_file():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data.get("chapter") or data.get("target_chapter")
        except (OSError, json.JSONDecodeError):
            pass
        return None

    # ------------------------------------------------------------------
    # 阶段判断
    # ------------------------------------------------------------------

    def next_step(self) -> dict:
        state = self._load_state()
        project = state.get("project_info") if isinstance(state, dict) else {}

        # 1. 未初始化
        if not project or not project.get("title"):
            return {
                "phase": "init",
                "message": "项目尚未初始化，请先填写故事核与基本设定。",
                "target": "/init",
                "action_label": "去初始化项目",
                "chapter": None,
            }

        # 2. 缺少总纲 / MASTER_SETTING -> 需要 plan volume
        has_outline = (self.root / "大纲" / "总纲.md").is_file()
        has_master = self._master_json().is_file()
        if not has_outline or not has_master:
            return {
                "phase": "plan_volume",
                "message": "缺少总纲或 MASTER_SETTING，需要先规划第 1 卷。",
                "target": "/outline",
                "action_label": "去生成卷纲",
                "chapter": None,
            }

        # 3. 没有章节合同 -> 需要拆章
        chapters = list_chapters(self.root)
        if not chapters:
            return {
                "phase": "plan_chapters",
                "message": "卷纲已就绪，但还没有章节合同，请继续生成章纲。",
                "target": "/outline",
                "action_label": "去生成章纲",
                "chapter": None,
            }

        # 4. 找到第一个未写完的章
        for item in chapters:
            chapter = int(item.get("chapter") or 0)
            if chapter <= 0:
                continue
            if not _find_chapter_md(self.root, chapter):
                return {
                    "phase": "write",
                    "message": f"第 {chapter} 章尚未起草，进入写章流水线。",
                    "target": "/write",
                    "action_label": f"去写第 {chapter} 章",
                    "chapter": chapter,
                }

        # 5. 所有章都已起草，找最早需要 review/polish/commit 的章
        for item in chapters:
            chapter = int(item.get("chapter") or 0)
            if chapter <= 0:
                continue
            if not _commit_json(self.root, chapter).is_file():
                audit_chapter = self._read_final_audit_chapter()
                if audit_chapter != chapter:
                    return {
                        "phase": "review",
                        "message": f"第 {chapter} 章已起草但审查结果未就绪，先跑 review。",
                        "target": "/write",
                        "action_label": f"去审查第 {chapter} 章",
                        "chapter": chapter,
                    }
                return {
                    "phase": "polish_commit",
                    "message": f"第 {chapter} 章审查完成，继续润色并提交。",
                    "target": "/write",
                    "action_label": f"去提交第 {chapter} 章",
                    "chapter": chapter,
                }

        # 6. 全部 committed
        latest = max((int(c["chapter"]) for c in chapters), default=0)
        next_ch = latest + 1
        return {
            "phase": "backup_finalize",
            "message": f"已完成到第 {latest} 章，建议备份/finalize 或开始第 {next_ch} 章。",
            "target": "/write",
            "action_label": f"开始第 {next_ch} 章",
            "chapter": next_ch,
        }
