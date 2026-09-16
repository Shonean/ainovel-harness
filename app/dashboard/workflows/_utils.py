"""
共享工具函数 —— 从 workflows.py 拆分。
"""
from __future__ import annotations

from pathlib import Path

from ..routes.actions import PLUGIN_ROOT
from ..services.agent_runner import AnthropicAgentRunner, _default_pro_model


def _extract_json(text: str) -> str:
    """从 LLM 输出里抠出第一个 {...} 或 [...] JSON 块。"""
    s = text.find("{")
    if s < 0:
        return "{}"
    depth = 0
    for i in range(s, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                return text[s:i + 1]
    return text[s:]


def _read_skill(skill_name: str, project_root: Path | None = None) -> str:
    """读取 skill SKILL.md。优先读测试书本地副本，回退全局。"""
    if project_root is not None:
        from ._prompts import resolve_prompt_path
        # plan skill → prompt category "plan"
        if skill_name == "ainovel-plan":
            p = resolve_prompt_path(project_root, "plan", "SKILL.md")
            if p.is_file():
                return p.read_text(encoding="utf-8")
    p = PLUGIN_ROOT / "skills" / skill_name / "SKILL.md"
    return p.read_text(encoding="utf-8") if p.is_file() else ""


def _read_write_reference(name: str, project_root: Path | None = None) -> str:
    """读取 ainovel-write skill 的 reference 文件。优先读测试书本地副本，回退全局。"""
    if project_root is not None:
        from ._prompts import resolve_prompt_path
        p = resolve_prompt_path(project_root, "write", name)
        if p.is_file():
            return p.read_text(encoding="utf-8")
    p = PLUGIN_ROOT / "skills" / "ainovel-write" / "references" / name
    return p.read_text(encoding="utf-8") if p.is_file() else ""
