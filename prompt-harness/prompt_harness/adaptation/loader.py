"""改编层 v0 — 书数据只读加载层。

读 `<book>/.ainovel/elements.json` 与 `arcs.json`（只读，不依赖 ai_creation 内部
实现细节），产出 ArcSnapshot 供 mapper/designer 消费。

v0 限制：单章弧 —— 只取弧的最新完成章的 l4 场景快照（`state.levels.l4.scenes`，
历史章的 l4 不落盘，见规格 §17 风险表）。数据体检只产 warning 不修数据。
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path


class AdaptationError(ValueError):
    """改编层输入数据致命缺失（构建中止）。"""


@dataclass
class ChapterOutline:
    num: int
    title: str
    core: str
    beats: list[str] = field(default_factory=list)


@dataclass
class ArcSnapshot:
    book_root: Path
    book_title: str
    arc_id: str
    arc_name: str
    l1: str = ""
    l2: str = ""
    chapter: ChapterOutline | None = None  # 最新完成章章纲（l3 或落盘章）
    chapter_num: int = 1
    scenes: list[dict] = field(default_factory=list)  # l4 场景叶子
    elements: dict = field(default_factory=dict)
    selected: dict[str, list[str]] = field(default_factory=dict)
    key_facts: list[str] = field(default_factory=list)
    levels: dict = field(default_factory=dict)  # 原始 levels（体检只读用）
    warnings: list[dict] = field(default_factory=list)  # 数据体检警告 {code, detail}


def _read_json(path: Path, default):
    if not path.is_file():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def _level_text(levels: dict, key: str) -> str:
    """levels[key] 可能是 {'text': ...} 或裸字符串，统一取文本。"""
    v = levels.get(key)
    if isinstance(v, dict):
        return str(v.get("text") or "")
    return str(v or "")


def _levels_of(arc: dict) -> dict:
    """兼容两种布局：state.levels.*（主）与弧顶层 l1~l5（旧）。"""
    state = arc.get("state") or {}
    levels = state.get("levels")
    if isinstance(levels, dict) and levels:
        return levels
    return {k: v for k, v in arc.items() if k in ("l1", "l2", "l3", "l4", "l5")}


def _l4_scenes(levels: dict) -> list[dict]:
    holder = levels.get("l4")
    if isinstance(holder, dict):
        scenes = holder.get("scenes")
        if isinstance(scenes, list) and scenes:
            return [s for s in scenes if isinstance(s, dict)]
    return []


def _l3_chapters(levels: dict) -> list[dict]:
    l3 = levels.get("l3")
    if isinstance(l3, dict):
        data = l3.get("data")
        if isinstance(data, dict):
            chs = data.get("chapters")
            if isinstance(chs, list):
                return [c for c in chs if isinstance(c, dict)]
    return []


def _latest_finalized_chapter(arc: dict) -> dict | None:
    """arc.chapters 里 num 最大的已落盘章（ai_creation.finalize_chapter 写入）。"""
    chapters = arc.get("chapters")
    if isinstance(chapters, list) and chapters:
        try:
            return max((c for c in chapters if isinstance(c, dict)),
                       key=lambda c: int(c.get("num") or 0))
        except (TypeError, ValueError):
            return chapters[-1]
    return None


def _protagonist_name(snap: ArcSnapshot) -> str:
    chars = snap.selected.get("characters") or []
    for c in snap.elements.get("characters") or []:
        if c.get("id") == (chars[0] if chars else None):
            return str(c.get("name") or "")
    if snap.elements.get("characters"):
        return str(snap.elements["characters"][0].get("name") or "")
    return ""


def _overlap(a: str, b: str) -> bool:
    """字符 2-gram 粗重合判断（体检用，不追求精确）。"""
    ga = {a[i:i + 2] for i in range(max(0, len(a) - 1))} - {""}
    gb = {b[i:i + 2] for i in range(max(0, len(b) - 1))} - {""}
    if not ga:
        return False
    return len(ga & gb) / len(ga) > 0.34


def health_check(snap: ArcSnapshot) -> None:
    """数据体检：只追加 warning，绝不修改数据（规格 §13 已知数据风险）。"""
    if not snap.l1:
        snap.warnings.append({"code": "DATA_MISSING_L1", "detail": f"弧 {snap.arc_id} 缺 l1"})
    if not snap.l2:
        snap.warnings.append({
            "code": "DATA_MISSING_L2",
            "detail": f"弧 {snap.arc_id} 缺 l2（结局素材将退化为章纲 core）",
        })
    # 已知混杂风险：l1/l2 叙事主体与 l4 场景主角是否脱节（TestBook：修仙 l1/l2 + 纸人教室 l4）
    if snap.l2 and snap.scenes:
        protagonist = _protagonist_name(snap)
        if protagonist and protagonist not in snap.l2:
            snap.warnings.append({
                "code": "DATA_LEVEL_MISMATCH",
                "detail": (f"l2 概要未提及 l4 场景主角「{protagonist}」，l1/l2 与 l4 "
                           "内容可能不一致（混杂书态），只警告不修"),
            })
    if snap.key_facts and snap.l2:
        if not any(_overlap(f, snap.l2) for f in snap.key_facts):
            snap.warnings.append({
                "code": "DATA_KEYFACTS_MISMATCH",
                "detail": "key_facts 与 l2 概要零重合，弧数据可能混杂",
            })
    l3chs = _l3_chapters(snap.levels)
    if l3chs and snap.chapter and snap.chapter.title:
        if not any(snap.chapter.title in (c.get("title") or "") for c in l3chs):
            snap.warnings.append({
                "code": "DATA_CHAPTER_OUTLINE_MISMATCH",
                "detail": "最新落盘章标题不在 l3 章纲内，章纲与正文可能不同步",
            })


def load_snapshot(book_root: str | Path, arc_id: str) -> ArcSnapshot:
    book_root = Path(book_root)
    dot = book_root / ".ainovel"
    if not dot.is_dir():
        raise AdaptationError(f"不是有效的书目录（缺 .ainovel/）：{book_root}")
    elements = _read_json(dot / "elements.json", {}) or {}
    arcs_data = _read_json(dot / "arcs.json", {}) or {}
    arc = next((a for a in arcs_data.get("arcs") or []
                if isinstance(a, dict) and a.get("id") == arc_id), None)
    if arc is None:
        raise AdaptationError(f"arcs.json 中找不到弧：{arc_id}")

    levels = _levels_of(arc)
    scenes = _l4_scenes(levels)
    if not scenes:
        raise AdaptationError(
            f"弧 {arc_id} 没有可用的 l4 场景快照（state.levels.l4.scenes 为空）；"
            "v0 限制单章弧，请先在当前章生成场景")

    snap = ArcSnapshot(
        book_root=book_root,
        book_title=book_root.name,
        arc_id=arc_id,
        arc_name=str(arc.get("name") or ""),
        l1=_level_text(levels, "l1"),
        l2=_level_text(levels, "l2"),
        scenes=scenes,
        elements=elements if isinstance(elements, dict) else {},
        selected=arc.get("selected") if isinstance(arc.get("selected"), dict) else {},
        key_facts=[str(x) for x in ((arc.get("state") or {}).get("key_facts") or [])],
        levels=levels,
    )

    # 最新完成章：优先已落盘章（arc.chapters），否则 l3 章纲第一章（l4 快照即当前章）
    finalized = _latest_finalized_chapter(arc)
    l3chs = _l3_chapters(levels)
    if finalized is not None:
        snap.chapter = ChapterOutline(
            num=int(finalized.get("num") or 1),
            title=str(finalized.get("title") or ""),
            core=str(finalized.get("core") or ""),
        )
        snap.chapter_num = snap.chapter.num
    elif l3chs:
        ch = l3chs[0]
        snap.chapter = ChapterOutline(
            num=1,
            title=str(ch.get("title") or ""),
            core=str(ch.get("core") or ""),
            beats=[str(b) for b in (ch.get("beats") or [])],
        )
        snap.chapter_num = 1
    else:
        snap.warnings.append({"code": "DATA_MISSING_L3", "detail": "弧没有 l3 章纲，也没有已落盘章"})
        snap.chapter = ChapterOutline(num=1, title="", core="")

    health_check(snap)
    return snap
