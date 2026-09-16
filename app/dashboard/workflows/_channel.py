"""
_channel.py — 通道配置加载器（Phase 6）

每个书通过 state.json 绑定一个通道（channel），通道定义了：
- 写作身份（Identity）
- 题材特有写作规则（Craft Rules）
- 开篇章节特殊要求（Opening Chapters）
- 额外审查维度（Review Criteria）

通道模板存储在 channels/{profile_name}.md（markdown 文件），
state.json 可通过 overrides 字段对特定节进行 per-book 覆盖。

加载逻辑：
1. 从 state.json 读 channel.profile（默认 "default"）
2. 加载 channels/{profile}.md，解析 YAML frontmatter + 分节内容
3. 用 state.json channel.overrides 中的非空字段覆盖对应节
4. 返回 ChannelProfile
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class ChannelProfile:
    """通道 profile，从 channels/{name}.md 解析而来。

    所有 prompt 相关字段为 markdown 字符串（可能为空），
    调用方直接注入到对应的 prompt 节中。
    """
    name: str                        # 通道名称（来自 YAML frontmatter）
    description: str                 # 通道描述
    model_override: str | None       # 模型覆盖（null=使用全局默认）
    identity: str                    # ## Identity 节内容
    craft_rules: str                 # ## Craft Rules 节内容
    opening_rules: str               # ## Opening Chapters 节内容
    review_criteria: str             # ## Review Criteria 节内容
    opening_count: int = 3           # 开篇章数（从 state.json 读取，默认 3）


# Sentinel for "no profile loaded" — equivalent to default empty profile
EMPTY_PROFILE = ChannelProfile(
    name="default",
    description="默认通道（未配置或通道文件缺失时的兜底）",
    model_override=None,
    identity="",
    craft_rules="",
    opening_rules="",
    review_criteria="",
    opening_count=3,
)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def load_channel(project_root: Path) -> ChannelProfile:
    """加载当前书的通道 profile。

    1. 读 state.json → channel.profile（默认 "default"）
    2. 从 channels/{profile}.md 加载文件
    3. 解析 YAML frontmatter + 分节 markdown
    4. 用 state.json channel.overrides 中的非空字段覆盖对应节
    5. 返回 ChannelProfile
    """
    state = _load_state_json(project_root)
    channel_cfg = state.get("channel") if isinstance(state, dict) else None

    if not channel_cfg:
        return EMPTY_PROFILE

    profile_name = channel_cfg.get("profile", "default")
    opening_count = channel_cfg.get("opening_count", 3)
    model_override = channel_cfg.get("model_override")

    # 加载通道 markdown 文件（优先测试书本地副本，回退全局）
    from ._prompts import resolve_channel_path
    md_path = resolve_channel_path(project_root, profile_name)
    if not md_path.is_file():
        # 指定通道不存在 → 降级到 default
        md_path = resolve_channel_path(project_root, "default")
    if not md_path.is_file():
        # 连 default.md 都不存在 → 返回空壳
        return EMPTY_PROFILE

    frontmatter, sections = _parse_channel_md(md_path)

    # Per-book overrides（state.json 覆盖 markdown 文件中的节）
    overrides = channel_cfg.get("overrides") if isinstance(channel_cfg, dict) else None
    if isinstance(overrides, dict):
        for key in ("identity", "craft_rules", "opening_rules", "review_criteria"):
            ov = overrides.get(key)
            if ov and isinstance(ov, str) and ov.strip():
                sections[key] = ov.strip()

    return ChannelProfile(
        name=frontmatter.get("name", profile_name),
        description=frontmatter.get("description", ""),
        model_override=model_override or frontmatter.get("model_override"),
        identity=sections.get("identity", ""),
        craft_rules=sections.get("craft_rules", ""),
        opening_rules=sections.get("opening_rules", ""),
        review_criteria=sections.get("review_criteria", ""),
        opening_count=opening_count,
    )


def is_opening_chapter(chapter: int, profile: ChannelProfile) -> bool:
    """判断给定章节是否为开篇章节。"""
    return 1 <= chapter <= profile.opening_count


def list_channel_profiles() -> list[dict]:
    """列出 channels/ 目录下所有可用通道文件的 YAML 前置元数据。"""
    profiles = []
    channels_dir = _channels_dir()
    if not channels_dir.is_dir():
        return profiles

    for md_file in sorted(channels_dir.glob("*.md")):
        try:
            frontmatter, _sections = _parse_channel_md(md_file)
            profiles.append({
                "profile": md_file.stem,
                "name": frontmatter.get("name", md_file.stem),
                "description": frontmatter.get("description", ""),
                "model_override": frontmatter.get("model_override"),
            })
        except Exception:
            # 解析失败的通道文件跳过
            pass

    return profiles


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _channels_dir() -> Path:
    """channels/ 目录路径（相对于本项目 ainovel-writer 根目录）。"""
    # _channel.py 位于 dashboard/workflows/_channel.py
    # channels/ 位于 ainovel-write/channels/
    return Path(__file__).resolve().parent.parent.parent / "channels"


def _load_state_json(project_root: Path) -> dict:
    """读取 .ainovel/state.json。"""
    state_path = project_root / ".ainovel" / "state.json"
    if state_path.is_file():
        try:
            return json.loads(state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
    return {}


def _parse_channel_md(path: Path) -> tuple[dict, dict[str, str]]:
    """解析通道 .md 文件 → (YAML frontmatter dict, {section_key: content})。

    section_key 映射（markdown 标题 → dataclass 字段名）:
        ## Identity → "identity"
        ## Craft Rules → "craft_rules"
        ## Opening Chapters → "opening_rules"
        ## Review Criteria → "review_criteria"
    """
    text = path.read_text(encoding="utf-8")

    frontmatter: dict = {}
    sections: dict[str, str] = {
        "identity": "",
        "craft_rules": "",
        "opening_rules": "",
        "review_criteria": "",
    }

    # 解析 YAML frontmatter（--- ... ---）
    fm_match = re.match(r'^---\s*\n(.*?)\n---\s*\n', text, re.DOTALL)
    if fm_match:
        try:
            import yaml
            frontmatter = yaml.safe_load(fm_match.group(1)) or {}
        except Exception:
            # yaml 不可用或解析失败 — 用简单的手动解析
            frontmatter = _parse_simple_frontmatter(fm_match.group(1))
        body = text[fm_match.end():]
    else:
        body = text

    # 解析分节内容（## Section Title）
    section_map = {
        "Identity": "identity",
        "Craft Rules": "craft_rules",
        "Opening Chapters": "opening_rules",
        "Review Criteria": "review_criteria",
    }

    # 用正则按 ## 标题拆分
    parts = re.split(r'(?=^## )', body, flags=re.MULTILINE)
    for part in parts:
        header_match = re.match(r'^## (.+?)$', part, re.MULTILINE)
        if not header_match:
            continue
        title = header_match.group(1).strip()
        key = section_map.get(title)
        if key is None:
            # 也尝试匹配 "Identity（xxx）" 等形式
            base_title = title.split("（")[0].split("(")[0].strip()
            key = section_map.get(base_title)
        if key is not None:
            # 去掉标题行，保留内容
            content = re.sub(r'^## .+?\n', '', part, count=1, flags=re.MULTILINE).strip()
            sections[key] = content

    return frontmatter, sections


def _parse_simple_frontmatter(fm_text: str) -> dict:
    """手动解析简单的 key: value 格式 frontmatter（当 yaml 不可用时）。"""
    result = {}
    for line in fm_text.strip().splitlines():
        line = line.strip()
        if ':' in line:
            key, _, value = line.partition(':')
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if value.lower() == "null" or value == "":
                value = None
            result[key] = value
    return result
