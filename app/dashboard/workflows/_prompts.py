"""
_prompts.py — 测试书 prompt 隔离加载器

加载优先级：
1. {project_root}/.ainovel/prompts/{category}/{filename}  （测试书本地）
2. 系统全局目录（agents/, channels/, references/）

对测试书：如果本地副本不存在，自动从全局复制一份。
对正式书：直接读全局文件（不创建本地副本）。

用法：
    from ._prompts import (
        init_test_book_prompts, resolve_prompt_path, resolve_channel_path,
        list_prompt_files, read_prompt_file, write_prompt_file,
        reset_prompt_file, reset_all_prompt_files,
    )
"""

from __future__ import annotations

from pathlib import Path


# ---------------------------------------------------------------------------
# 类别定义
# ---------------------------------------------------------------------------

PROMPT_CATEGORIES = {
    "agents": "agents",
    "channels": "channels",
    "references": "references",
    "plan": "plan",
    "write": "write",
}


def _get_prompt_root(project_root: Path) -> Path:
    return project_root / ".ainovel" / "prompts"


# ---------------------------------------------------------------------------
# 系统目录定位器
# ---------------------------------------------------------------------------

def _system_agents_dir() -> Path:
    """ainovel-write/agents/"""
    from dashboard.agents.agents import AGENTS_DIR
    return AGENTS_DIR


def _system_channels_dir() -> Path:
    """ainovel-write/channels/"""
    from ._channel import _channels_dir
    return _channels_dir()


def _app_root() -> Path:
    """app/ 目录（agents、references 所在）。"""
    return _system_agents_dir().parent


def _repo_root() -> Path:
    """仓库根 ainovel-write/（config、tools 所在）。"""
    return _app_root().parent


def _system_references_dir() -> Path:
    """app/references/（polish-guide.md, typesetting.md, style-adapter.md）"""
    return _app_root() / "references"


def _system_plan_dir() -> Path:
    """config/skills/ainovel-plan/（0817 重构后 skills 从 app/ 移到 config/）"""
    return _repo_root() / "config" / "skills" / "ainovel-plan"


def _system_write_dir() -> Path:
    """config/skills/ainovel-write/references/"""
    return _repo_root() / "config" / "skills" / "ainovel-write" / "references"


def _system_path(category: str, filename: str) -> Path:
    dirs = {
        "agents": _system_agents_dir(),
        "channels": _system_channels_dir(),
        "references": _system_references_dir(),
        "plan": _system_plan_dir(),
        "write": _system_write_dir(),
    }
    return dirs[category] / filename


# ---------------------------------------------------------------------------
# 复制工具
# ---------------------------------------------------------------------------

def _copy_if_newer(src: Path, dst: Path, stats: dict):
    """如果目标不存在则复制。不覆盖已有文件（手动修改过的保留）。"""
    if not dst.is_file():
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_text(src.read_text(encoding="utf-8"))
        stats["copied"] += 1
    else:
        stats["skipped"] += 1


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def init_test_book_prompts(project_root: Path) -> dict:
    """为测试书创建 prompt 本地副本。返回复制统计 {copied, skipped}。

    如果本地副本已存在则跳过（保留用户修改）；初始化流程中也会调用此函数
    来确保 prompt 隔离就绪。
    """
    prompt_root = _get_prompt_root(project_root)
    prompt_root.mkdir(parents=True, exist_ok=True)
    stats = {"copied": 0, "skipped": 0}

    # 复制 agents/
    agents_src = _system_agents_dir()
    agents_dst = prompt_root / "agents"
    if agents_src.is_dir():
        for f in agents_src.glob("*.md"):
            _copy_if_newer(f, agents_dst / f.name, stats)

    # 复制 channels/
    channels_src = _system_channels_dir()
    channels_dst = prompt_root / "channels"
    if channels_src.is_dir():
        for f in channels_src.glob("*.md"):
            _copy_if_newer(f, channels_dst / f.name, stats)

    # 复制 references/
    refs_src = _system_references_dir()
    refs_dst = prompt_root / "references"
    if refs_src.is_dir():
        for f in refs_src.glob("**/*.md"):
            rel = f.relative_to(refs_src)
            _copy_if_newer(f, refs_dst / rel, stats)

    # 复制 plan skill
    plan_src = _system_plan_dir()
    plan_dst = prompt_root / "plan"
    if plan_src.is_dir():
        for f in plan_src.glob("**/*.md"):
            rel = f.relative_to(plan_src)
            _copy_if_newer(f, plan_dst / rel, stats)

    # 复制 write skill references（polish-guide.md, typesetting.md, style-adapter.md）
    write_src = _system_write_dir()
    write_dst = prompt_root / "write"
    if write_src.is_dir():
        for f in write_src.glob("**/*.md"):
            rel = f.relative_to(write_src)
            _copy_if_newer(f, write_dst / rel, stats)

    return stats


def resolve_prompt_path(project_root: Path, category: str, filename: str) -> Path:
    """解析 prompt 文件路径，优先读本地副本。

    对测试书：返回 .ainovel/prompts/{category}/{filename}
    对正式书或无本地副本：返回系统全局路径
    """
    local = _get_prompt_root(project_root) / category / filename
    if local.is_file():
        return local
    return _system_path(category, filename)


def resolve_channel_path(project_root: Path, profile_name: str) -> Path:
    """解析通道文件路径。"""
    return resolve_prompt_path(project_root, "channels", f"{profile_name}.md")


def list_prompt_files(project_root: Path) -> list[dict]:
    """列出所有 prompt 文件（本地+全局混合视图）。

    返回每个文件的：category, path, size, is_local, editable
    """
    files: list[dict] = []
    prompt_root = _get_prompt_root(project_root)
    has_local = prompt_root.is_dir()

    for category in PROMPT_CATEGORIES:
        sys_dir = _system_path(category, "")
        if not sys_dir.is_dir():
            continue
        for f in sys_dir.glob("**/*.md"):
            rel = f.relative_to(sys_dir)
            local_f = prompt_root / category / rel
            # 计算相对于类别根目录的路径
            files.append({
                "category": category,
                "filename": str(rel.as_posix()),
                "size": f.stat().st_size,
                "is_local": local_f.is_file(),
                "editable": has_local,
            })

    # 按 category + filename 排序
    files.sort(key=lambda x: (x["category"], x["filename"]))
    return files


def read_prompt_file(project_root: Path, category: str, filename: str) -> str:
    """读取 prompt 文件内容。优先读本地，回退全局。"""
    path = resolve_prompt_path(project_root, category, filename)
    if path.is_file():
        return path.read_text(encoding="utf-8")
    raise FileNotFoundError(f"Prompt 文件不存在: {category}/{filename}")


def read_prompt_file_safe(project_root: Path, category: str, filename: str) -> dict:
    """读取 prompt 文件，返回 {content, is_local, source_path}。不抛异常。"""
    local_path = _get_prompt_root(project_root) / category / filename
    sys_path = _system_path(category, filename)

    if local_path.is_file():
        return {
            "content": local_path.read_text(encoding="utf-8"),
            "is_local": True,
            "source_path": str(local_path),
        }
    if sys_path.is_file():
        return {
            "content": sys_path.read_text(encoding="utf-8"),
            "is_local": False,
            "source_path": str(sys_path),
        }
    return {"content": "", "is_local": False, "source_path": ""}


def write_prompt_file(project_root: Path, category: str, filename: str, content: str):
    """写入 prompt 文件到测试书本地副本。自动创建父目录。"""
    prompt_root = _get_prompt_root(project_root)
    dest = prompt_root / category / filename
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(content, encoding="utf-8")


def reset_prompt_file(project_root: Path, category: str, filename: str) -> str:
    """重置单个文件：从全局重新复制，覆盖本地修改。返回操作结果描述。"""
    sys_path = _system_path(category, filename)
    if not sys_path.is_file():
        raise FileNotFoundError(f"全局文件不存在: {category}/{filename}")
    prompt_root = _get_prompt_root(project_root)
    dest = prompt_root / category / filename
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(sys_path.read_text(encoding="utf-8"))
    return f"已从全局恢复 {category}/{filename}"


def reset_all_prompt_files(project_root: Path) -> dict:
    """重置所有文件：从全局重新复制，覆盖所有本地修改。返回复制统计。"""
    prompt_root = _get_prompt_root(project_root)
    stats = {"copied": 0, "overwritten": 0}

    for category in PROMPT_CATEGORIES:
        sys_dir = _system_path(category, "")
        if not sys_dir.is_dir():
            continue
        for f in sys_dir.glob("**/*.md"):
            rel = f.relative_to(sys_dir)
            dest = prompt_root / category / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            if dest.is_file():
                stats["overwritten"] += 1
            else:
                stats["copied"] += 1
            dest.write_text(f.read_text(encoding="utf-8"))

    return stats
