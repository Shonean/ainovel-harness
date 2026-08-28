#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any, Dict

try:
    from chapter_paths import volume_num_for_chapter
except ImportError:  # pragma: no cover
    from scripts.chapter_paths import volume_num_for_chapter

logger = logging.getLogger(__name__)


_CHAPTER_RANGE_RE = re.compile(r"^\s*(\d+)\s*-\s*(\d+)\s*$")


def _parse_chapters_range(value: object) -> tuple[int, int] | None:
    if not isinstance(value, str):
        return None
    match = _CHAPTER_RANGE_RE.match(value)
    if not match:
        return None
    try:
        start = int(match.group(1))
        end = int(match.group(2))
    except ValueError:
        return None
    if start <= 0 or end <= 0 or start > end:
        return None
    return start, end


def volume_num_for_chapter_from_state(project_root: Path, chapter_num: int) -> int | None:
    state_path = project_root / ".ainovel" / "state.json"
    if not state_path.exists():
        return None

    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except Exception:
        return None

    if not isinstance(state, dict):
        return None

    progress = state.get("progress")
    if not isinstance(progress, dict):
        return None

    # 依次检查 volumes_planned（整卷登记，旧）与 chapters_planned（按批增量登记，新）。
    # 同卷可能在 chapters_planned 中有多条范围记录；取命中该章且起始章最大者所属卷号。
    best: tuple[int, int] | None = None

    for key in ("volumes_planned", "chapters_planned"):
        records = progress.get(key)
        if not isinstance(records, list):
            continue
        for item in records:
            if not isinstance(item, dict):
                continue
            volume = item.get("volume")
            if not isinstance(volume, int) or volume <= 0:
                continue
            parsed = _parse_chapters_range(item.get("chapters_range"))
            if not parsed:
                continue
            start, end = parsed
            if start <= chapter_num <= end:
                candidate = (start, volume)
                if best is None or candidate[0] > best[0] or (candidate[0] == best[0] and candidate[1] < best[1]):
                    best = candidate

    return best[1] if best else None


def _find_split_outline_file(outline_dir: Path, chapter_num: int) -> Path | None:
    patterns = [
        f"第{chapter_num}章*.md",
        f"第{chapter_num:02d}章*.md",
        f"第{chapter_num:03d}章*.md",
        f"第{chapter_num:04d}章*.md",
    ]
    for pattern in patterns:
        matches = sorted(outline_dir.glob(pattern))
        if not matches:
            continue
        # 同一章可能并存 章纲/故事简要/写作任务书.md；字母序会把「故事简要」排到「章纲」前，
        # 导致误读非章纲文件抽不出 directive。优先选文件名含「章纲」者。
        outline_match = next((m for m in matches if "章纲" in m.name), None)
        return outline_match or matches[0]
    return None


def _find_volume_outline_file(project_root: Path, chapter_num: int) -> Path | None:
    outline_dir = project_root / "大纲"
    volume_num = volume_num_for_chapter_from_state(project_root, chapter_num) or volume_num_for_chapter(chapter_num)
    candidates = [
        outline_dir / f"第{volume_num}卷-详细大纲.md",
        outline_dir / f"第{volume_num}卷 - 详细大纲.md",
        outline_dir / f"第{volume_num}卷 详细大纲.md",
    ]
    return next((path for path in candidates if path.exists()), None)


def _extract_outline_section(content: str, chapter_num: int) -> str | None:
    patterns = [
        rf"###\s*第\s*{chapter_num}\s*章[：:]\s*(.+?)(?=###\s*第\s*\d+\s*章|##\s|$)",
        rf"###\s*第{chapter_num}章[：:]\s*(.+?)(?=###\s*第\d+章|##\s|$)",
    ]
    for pattern in patterns:
        match = re.search(pattern, content, re.DOTALL)
        if match:
            return match.group(0).strip()
    return None


def _parse_chinese_chapter_num(value: str) -> int | None:
    text = str(value or "").strip()
    if not text:
        return None
    if text.isdigit():
        return int(text)
    if text in _CHINESE_NUMERAL_DIGITS:
        return _CHINESE_NUMERAL_DIGITS[text]
    if text == "十":
        return 10
    if "十" in text:
        left, _, right = text.partition("十")
        tens = _CHINESE_NUMERAL_DIGITS.get(left, 1 if not left else 0)
        ones = _CHINESE_NUMERAL_DIGITS.get(right, 0) if right else 0
        parsed = tens * 10 + ones
        return parsed or None
    parsed = 0
    for char in text:
        digit = _CHINESE_NUMERAL_DIGITS.get(char)
        if digit is None:
            return None
        parsed = parsed * 10 + digit
    return parsed or None


def _extract_directive_section(content: str, chapter_num: int) -> str | None:
    matches = list(_CHAPTER_HEADING_RE.finditer(content))
    for index, match in enumerate(matches):
        parsed = _parse_chinese_chapter_num(match.group(2))
        if parsed != chapter_num:
            continue
        end = matches[index + 1].start() if index + 1 < len(matches) else len(content)
        return content[match.start():end].strip()
    return _extract_outline_section(content, chapter_num)


def _check_outline_staleness(project_root: Path, chapter_num: int, outline_path: Path) -> None:
    """检查 state.json 中 chapter_status，若为 pending/reset 则打印 WARNING。

    防重写雷同：reset 后旧章纲已过期，调用方应考虑重新生成。
    """
    state_path = project_root / ".ainovel" / "state.json"
    if not state_path.is_file():
        return
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return
    ch_status = (
        state.get("progress", {}).get("chapter_status", {}).get(str(chapter_num))
        or state.get("progress", {}).get("chapter_status", {}).get(chapter_num)
    )
    if ch_status in ("pending", "reset", "pending_reset"):
        logger.warning(
            "[chapter_outline_loader] 第 %d 章状态为 %s，旧章纲 %s 可能已过期；"
            "调用方应考虑重新生成（避免重写雷同）",
            chapter_num, ch_status, outline_path.name,
        )


def load_chapter_outline(project_root: Path, chapter_num: int, max_chars: int | None = 3000) -> str:
    outline_dir = project_root / "大纲"

    split_outline = _find_split_outline_file(outline_dir, chapter_num)
    if split_outline is not None:
        # 陈旧性检查：state.json 中 chapter_status 为 pending/reset 时打印 WARNING
        _check_outline_staleness(project_root, chapter_num, split_outline)
        return split_outline.read_text(encoding="utf-8")

    volume_outline = _find_volume_outline_file(project_root, chapter_num)
    if volume_outline is None:
        return f"⚠️ 大纲文件不存在：第 {chapter_num} 章"

    outline = _extract_outline_section(volume_outline.read_text(encoding="utf-8"), chapter_num)
    if outline is None:
        return f"⚠️ 未找到第 {chapter_num} 章的大纲"

    if max_chars and len(outline) > max_chars:
        return outline[:max_chars] + "\n...(已截断)"
    return outline

_PLOT_SECTION_FIELD_MAP = {
    "cbn": "cbn",
    "cpns": "cpns",
    "cen": "cen",
    "必须覆盖节点": "mandatory_nodes",
    "本章禁区": "prohibitions",
}

_CHAPTER_HEADING_RE = re.compile(
    r"^(#{1,6})\s*第\s*([0-9零〇一二两三四五六七八九十]+)\s*章\b.*$",
    re.MULTILINE,
)

_CHINESE_NUMERAL_DIGITS = {
    "零": 0,
    "〇": 0,
    "一": 1,
    "二": 2,
    "两": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
}

_DIRECTIVE_FIELD_MAP = {
    # 旧格式（21字段散文格式）
    "目标": "goal",
    "本章目标": "goal",
    "章目标": "goal",
    "剧情点": "goal",
    "阻力": "obstacles",
    "障碍": "obstacles",
    "代价": "cost",
    "时间锚点": "time_anchor",
    "时间": "time_anchor",
    "章内跨度": "chapter_span",
    "章节跨度": "chapter_span",
    "倒计时状态": "countdown",
    "倒计时": "countdown",
    "cbn": "cbn",
    "cpns": "cpns",
    "cen": "cen",
    "必须覆盖节点": "must_cover_nodes",
    "本章禁区": "forbidden_zones",
    "章末未闭合问题": "chapter_end_open_question",
    "章末问题": "chapter_end_open_question",
    "钩子": "hook_type",
    "钩子类型": "hook_type",
    "钩子强度": "hook_strength",
    "关键实体": "key_entities",
    "涉及实体": "key_entities",
    "strand": "strand",
    "反派层级": "antagonist_tier",
    "逐段推进": "paragraph_beats",
    "段落推进": "paragraph_beats",
    # Phase 3 新格式（4段扁平清单）—— _clean_plot_line 去掉 ** 和列表前缀后匹配
    "CBN（章节起点）": "cbn",
    "CBN": "cbn",
    "CPN-1": "cpns",
    "CPN-2": "cpns",
    "CPN-3": "cpns",
    "CPN-4": "cpns",
    "CPN-5": "cpns",
    "CEN（章节终点）": "cen",
    "CEN": "cen",
    "章末钩子": "hook_type",
    "未闭合问题": "chapter_end_open_question",
    "必须覆盖": "must_cover_nodes",
    "本章禁区": "forbidden_zones",
    # Phase 6: 开篇章纲额外字段
    "一句话钩子": "hook_sentence",
    "hook_sentence": "hook_sentence",
    "世界观露出节奏": "world_intro_beats",
    "world_intro_beats": "world_intro_beats",
    "角色印象": "character_first_impression",
    "character_first_impression": "character_first_impression",
}

_DIRECTIVE_LIST_FIELDS = {"cpns", "must_cover_nodes", "forbidden_zones", "key_entities", "paragraph_beats"}

# 结构化节点的小节标题 → 字段。agent 常用「#### 章节起点（CBN）」「#### 推进节点（CPNs）」
# 「#### 章节终点（CEN）」「#### 必须覆盖节点」「#### 本章禁区」「#### 逐段推进」这种
# 标题行 + 续行的格式；这些标题行本身没有「标签：值」结构，需单独识别为字段起始，
# 其后续行（反引号节点 / 编号列表）归入对应字段。
_HEADING_FIELD_MAP = {
    "章节起点": "cbn",
    "起点": "cbn",
    "推进节点": "cpns",
    "推进": "cpns",
    "章节终点": "cen",
    "终点": "cen",
    "必须覆盖节点": "must_cover_nodes",
    "必覆盖节点": "must_cover_nodes",
    "本章禁区": "forbidden_zones",
    "禁区": "forbidden_zones",
    "逐段推进": "paragraph_beats",
    "段落推进": "paragraph_beats",
}


def _match_heading_field(cleaned: str) -> str | None:
    """识别「章节起点（CBN）」式小节标题行，返回对应字段名，否则 None。

    仅匹配「纯标题行」——标签后只能跟括号注释（如（CBN））和/或末尾冒号、空格，
    不能带实际值。这样「本章禁区：不得离开宗门」这类「标签：值」行不会被误判为标题，
    仍由标签正则正常解析。

    cleaned 已经过 _clean_plot_line 清洗（去 #、列表前缀、加粗、反引号）。
    """
    if not cleaned:
        return None
    # 按标签长度降序，保证「章节起点」优先于「起点」匹配
    for label in sorted(_HEADING_FIELD_MAP, key=len, reverse=True):
        if cleaned.startswith(label):
            tail = cleaned[len(label):]
            # 去掉括号注释 （CBN）/（CPNs） 等
            tail = re.sub(r"^[（(][^）)]*[）)]", "", tail)
            # 去掉末尾冒号与空白
            tail = tail.strip().rstrip("：:").strip()
            # 标题行：标签后不应再有实际内容（否则是「标签：值」行，交给标签正则）
            if tail == "":
                return _HEADING_FIELD_MAP[label]
    return None

# 这些列表字段「每个编号项即一个完整条目」，不按内部标点拆分：
# paragraph_beats 每条对应正文一段；cpns 节点格式为「主体 | 动作 | 对象」，
# 对象部分常含中文逗号/顿号（自然语言），整条必须保留，不能被切碎。
_DIRECTIVE_NO_SPLIT_FIELDS = {"paragraph_beats", "cpns", "world_intro_beats"}


def _clean_plot_line(line: str) -> str:
    text = str(line or "").strip()
    # 去 markdown 标题前缀 #### → （使「#### 逐段推进：」「#### 章节起点（CBN）」可被识别）
    text = re.sub(r"^#{1,6}\s*", "", text)
    text = re.sub(r"^[\-\*•]+\s*", "", text)
    text = re.sub(r"^\d+[\.、]\s*", "", text)
    # 去除 markdown 加粗标记 **label** → label，使「**目标**：xxx」能被字段正则匹配
    text = re.sub(r"\*\*(.+?)\*\*", r"\1", text)
    # 去除行内反引号包裹（节点常写成 `主体 | 动作 | 对象`）
    text = re.sub(r"`([^`]*)`", r"\1", text)
    return text.strip()


def _append_plot_value(target: Dict[str, Any], field: str, value: str) -> None:
    value = _clean_plot_line(value)
    if not value:
        return

    if field in {"cpns", "mandatory_nodes", "prohibitions"}:
        target.setdefault(field, [])
        candidates = [value]
        if field in {"mandatory_nodes", "prohibitions"}:
            split_values = [part.strip() for part in re.split(r"[、,，；;]+", value) if part.strip()]
            if split_values:
                candidates = split_values
        for item in candidates:
            if item not in target[field]:
                target[field].append(item)
        return

    if field not in target:
        target[field] = value


def _split_directive_values(value: str) -> list[str]:
    text = _clean_plot_line(value)
    if not text:
        return []
    # 不按 | 拆分：| 是 cpns 节点「主体 | 动作 | 对象」的内部分隔符，非条目间分隔符。
    return [part.strip() for part in re.split(r"[、,，；;]+", text) if part.strip()]


def _append_directive_value(target: Dict[str, Any], field: str, value: str) -> None:
    value = _clean_plot_line(value)
    if not value:
        return
    if field in _DIRECTIVE_LIST_FIELDS:
        target.setdefault(field, [])
        if field in _DIRECTIVE_NO_SPLIT_FIELDS:
            # 每个编号项整条保留，不按标点拆分
            if value not in target[field]:
                target[field].append(value)
            return
        for item in _split_directive_values(value) or [value]:
            if item not in target[field]:
                target[field].append(item)
        return
    if field not in target:
        target[field] = value


def parse_chapter_plot_structure(outline_text: str) -> Dict[str, Any]:
    text = str(outline_text or "")
    if not text or text.startswith("⚠️"):
        return {}

    structure: Dict[str, Any] = {}
    current_field = ""

    for raw_line in text.splitlines():
        stripped = raw_line.strip()
        if not stripped:
            current_field = ""
            continue
        if re.match(r"^#{1,6}\s*第\s*\d+\s*章", stripped):
            current_field = ""
            continue

        cleaned = _clean_plot_line(stripped)
        heading_field = _match_heading_field(cleaned)
        if heading_field:
            current_field = heading_field
            continue
        matched_field = ""
        matched_value = ""
        for label, field in _PLOT_SECTION_FIELD_MAP.items():
            match = re.match(rf"^{re.escape(label)}\s*[：:]\s*(.*)$", cleaned, re.IGNORECASE)
            if match:
                matched_field = field
                matched_value = match.group(1).strip()
                break

        if matched_field:
            current_field = matched_field
            _append_plot_value(structure, matched_field, matched_value)
            continue

        if current_field:
            _append_plot_value(structure, current_field, cleaned)

    cpns = structure.get("cpns") or []
    mandatory_nodes = structure.get("mandatory_nodes") or []
    prohibitions = structure.get("prohibitions") or []
    if not any([structure.get("cbn"), cpns, structure.get("cen"), mandatory_nodes, prohibitions]):
        return {}

    return {
        "cbn": str(structure.get("cbn") or "").strip(),
        "cpns": cpns,
        "cen": str(structure.get("cen") or "").strip(),
        "mandatory_nodes": mandatory_nodes,
        "prohibitions": prohibitions,
        "source": "chapter_outline",
    }


def load_chapter_plot_structure(project_root: Path, chapter_num: int) -> Dict[str, Any]:
    outline = load_chapter_outline(project_root, chapter_num, max_chars=None)
    return parse_chapter_plot_structure(outline)


def parse_chapter_execution_directive(outline_text: str) -> Dict[str, Any]:
    text = str(outline_text or "")
    if not text or text.startswith("⚠️"):
        return {}

    # Phase 3 新格式（4段扁平清单）的段标题：纯标记行，不应被当作字段值
    _FLAT_SECTION_HEADERS = {"基础信息", "事件清单", "逐段推进", "硬约束"}

    directive: Dict[str, Any] = {}
    current_field = ""
    for raw_line in text.splitlines():
        stripped = raw_line.strip()
        if not stripped:
            current_field = ""
            continue
        if _CHAPTER_HEADING_RE.match(stripped):
            current_field = ""
            continue

        # Phase 3: ## 段标题行 → 若对应已知字段则设为 current_field，否则重置
        if stripped.startswith("## ") and any(
            stripped.lstrip("# ").strip() == h for h in _FLAT_SECTION_HEADERS
        ):
            section_name = stripped.lstrip("# ").strip()
            # 「逐段推进」同时是段标题和 directive 字段 → 设为当前字段
            mapped = _DIRECTIVE_FIELD_MAP.get(section_name) or _HEADING_FIELD_MAP.get(section_name)
            if mapped:
                current_field = mapped
            else:
                current_field = ""
            continue

        cleaned = _clean_plot_line(stripped)
        # 跳过已被清洗为纯段标题的行（如 `基础信息` 单独一行）
        if cleaned in _FLAT_SECTION_HEADERS:
            mapped = _DIRECTIVE_FIELD_MAP.get(cleaned) or _HEADING_FIELD_MAP.get(cleaned)
            if mapped:
                current_field = mapped
            else:
                current_field = ""
            continue

        # 先识别「#### 章节起点（CBN）」式小节标题行：设为当前字段，续行归入该字段
        heading_field = _match_heading_field(cleaned)
        if heading_field:
            current_field = heading_field
            continue
        matched_field = ""
        matched_value = ""
        for label, field in _DIRECTIVE_FIELD_MAP.items():
            match = re.match(rf"^{re.escape(label)}\s*[：:]\s*(.*)$", cleaned, re.IGNORECASE)
            if match:
                matched_field = field
                matched_value = match.group(1).strip()
                break

        if matched_field:
            current_field = matched_field
            _append_directive_value(directive, matched_field, matched_value)
            continue
        if current_field:
            _append_directive_value(directive, current_field, cleaned)

    plot_structure = parse_chapter_plot_structure(text)
    for source_key, target_key in (
        ("cbn", "cbn"),
        ("cpns", "cpns"),
        ("cen", "cen"),
        ("mandatory_nodes", "must_cover_nodes"),
        ("prohibitions", "forbidden_zones"),
    ):
        if plot_structure.get(source_key) and not directive.get(target_key):
            directive[target_key] = plot_structure[source_key]

    if directive:
        directive["source"] = "chapter_outline"
    return directive


def load_chapter_execution_directive(project_root: Path, chapter_num: int) -> Dict[str, Any]:
    outline_dir = project_root / "大纲"
    split_outline = _find_split_outline_file(outline_dir, chapter_num)
    if split_outline is not None:
        return parse_chapter_execution_directive(split_outline.read_text(encoding="utf-8"))

    volume_outline = _find_volume_outline_file(project_root, chapter_num)
    if volume_outline is None:
        return {}
    section = _extract_directive_section(volume_outline.read_text(encoding="utf-8"), chapter_num)
    if section is None:
        return {}
    return parse_chapter_execution_directive(section)
