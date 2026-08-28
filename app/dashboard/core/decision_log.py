"""
决策日志模块

记录所有用户决策操作，支持历史查询和审计追溯。
决策日志是只读流水账，不允许修改和删除，确保留痕可回溯。
"""
import json
import threading
from datetime import datetime
from pathlib import Path
from typing import Optional


class DecisionLog:
    """决策日志记录器，所有用户决策操作都会被持久化到文件中。"""

    def __init__(self, log_dir: Path):
        """
        初始化决策日志记录器。
        :param log_dir: 日志存储目录，通常是 {project_root}/.ainovel/decision_logs/
        """
        self._log_dir = Path(log_dir)
        self._lock = threading.Lock()
        self._log_dir.mkdir(parents=True, exist_ok=True)
        self._current_log_path = self._log_dir / "current.jsonl"

    def record_decision(
        self,
        *,
        task_id: str,
        step_id: str,
        operation_type: str,
        required_level: str,
        user_decision: dict,
        step_description: str = "",
    ) -> dict:
        """
        记录一次决策到日志文件。
        :return: 完整的决策记录
        """
        record = {
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            "task_id": task_id,
            "step_id": step_id,
            "operation_type": operation_type,
            "required_level": required_level,
            "user_level": user_decision.get("decision_level", required_level),
            "approved": bool(user_decision.get("approved", False)),
            "selected_option": user_decision.get("selected_option"),
            "custom_prompt": user_decision.get("custom_prompt"),
            "user_reason": user_decision.get("reason", ""),
            "step_description": step_description,
        }

        with self._lock:
            try:
                with open(self._current_log_path, "a", encoding="utf-8") as f:
                    f.write(json.dumps(record, ensure_ascii=False) + "\n")
            except OSError as exc:
                # 日志写入失败不应该阻塞主流程
                import logging
                logging.getLogger("dashboard.decision_log").warning(
                    "决策日志写入失败: %s", exc)

        return record

    def list_decisions(
        self,
        *,
        task_id: Optional[str] = None,
        step_id: Optional[str] = None,
        limit: int = 100,
    ) -> list[dict]:
        """
        查询决策日志，支持按任务ID和步骤ID过滤。
        :return: 决策记录列表，按时间倒序
        """
        records: list[dict] = []
        if not self._current_log_path.is_file():
            return records

        with self._lock:
            try:
                with open(self._current_log_path, "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            record = json.loads(line)
                        except json.JSONDecodeError:
                            continue
                        if task_id and record.get("task_id") != task_id:
                            continue
                        if step_id and record.get("step_id") != step_id:
                            continue
                        records.append(record)
            except OSError:
                return records

        # 按时间倒序
        records.reverse()
        return records[:limit]

    def get_decision(self, *, task_id: str, step_id: str) -> Optional[dict]:
        """获取指定任务+步骤的最新决策记录。"""
        records = self.list_decisions(task_id=task_id, step_id=step_id, limit=1)
        return records[0] if records else None

    def clear_log(self) -> bool:
        """
        清空决策日志（谨慎使用，仅在调试或用户明确要求时使用）。
        实际操作中是创建新文件并归档旧文件。
        """
        with self._lock:
            if not self._current_log_path.is_file():
                return True
            try:
                # 归档旧日志
                archive_name = (
                    f"decision_log_{datetime.now().strftime('%Y%m%d_%H%M%S')}.jsonl"
                )
                archive_path = self._log_dir / archive_name
                self._current_log_path.rename(archive_path)
                return True
            except OSError:
                return False


# 全局决策日志实例缓存（按 project_root 缓存）
_loggers: dict[str, DecisionLog] = {}
_loggers_lock = threading.Lock()


def get_decision_log(project_root: Path) -> DecisionLog:
    """
    获取指定项目根目录的决策日志实例（单例模式）。
    """
    project_root = Path(project_root).resolve()
    key = str(project_root)
    with _loggers_lock:
        if key not in _loggers:
            log_dir = project_root / ".ainovel" / "decision_logs"
            _loggers[key] = DecisionLog(log_dir)
        return _loggers[key]
