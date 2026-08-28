# -*- coding: utf-8 -*-
"""AI 创作模块（新测试书）：逐情节创作 + 元素选择隔离 + 双评分。

绑定主系统书结构：`<书根>/.ainovel/` 下新增 elements.json / arcs.json，
正文/章纲/评分落盘到主系统约定目录（AI生成/、大纲/、审查报告/）。
从「手写 brief → LLM 生成设定」到「逐情节 l1→l5 → 每章双评分 → 落盘」整条链路。

元素隔离机制（用户 2026-08-07 拍板）：
- 每剧情情节选择一次参与元素（角色/物品/设定），选中元素**从 l3 章核心开始**生效
- 生成 prompt 白名单注入（_element_block）+ 事后检测（确定性扫描 + LLM 复核双检）
- 未选中元素零出现，完全避免污染

双评分制（每章出分，无参考原文——不复用「复现原文」评分）：
- 意图兑现分：target = 合成意图文本（l2 情节概要 + l3 章核心/拍点），
  复用 plot_similarity / beat_sequence_fidelity / semantic_coverage / factual_consistency_score
- 纯质量分：AI 味（无原文审阅）+ 流水账 + 流畅/连贯/对白质量 + 元素合规
"""
from __future__ import annotations

import json
import re
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# 意图兑现分权重（target 是短意图文本，字符/结构复现无意义，只用语义/拍/事实）
INTENT_W = {"plot": 0.35, "beat": 0.25, "semantic": 0.20, "factual": 0.20}
# 纯质量分权重
QUALITY_W = {"ai": 0.40, "fluency": 0.15, "cohesion": 0.15, "dialogue": 0.10,
             "compliance": 0.20}
# 情节模板自动命中最低相似度（<此值不套用模板，避免强套错款劣化生成；可调）
# 【v7.8.3 调 0.50→0.58】实测相似度分布：同题材 0.60-0.69 / 跨题材 0.52-0.55，
# 0.50 太松导致库外题材（星际AI 0.516）误命中；0.58 拦掉跨题材，保留同题材弱命中(0.597)。
TEMPLATE_MATCH_MIN_SIM = 0.58

_ELEMENTS_FILE = "elements.json"
_ARCS_FILE = "arcs.json"


def _wb_ctx(book_root: str | Path, **extra: Any) -> None:
    """设置工作台日志上下文（book + 可选 arc/step/chapter）。

    驱动函数入口调用：book 从参数取，arc/step/chapter 看调用方掌握什么。
    上下文随 asyncio task 传播，llm_client 记录 trace 时读取；不 reset——
    FastAPI 每请求独立 task，后台任务单任务贯穿，同任务内重复 set 覆盖更新。
    """
    from .workbench_logger import set_workbench_ctx
    set_workbench_ctx(book=str(book_root), **extra)


# ════════════════════════════════════════════════════════════════════
# 书内 JSON 读写（主系统书结构，复用约定目录）
# ════════════════════════════════════════════════════════════════════

def _dot_ainovel(book_root: str | Path) -> Path:
    return Path(book_root) / ".ainovel"


def _read_json(path: Path, default: Any) -> Any:
    if not path.is_file():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def _write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def _norm_fields(raw: Any) -> list[dict[str, str]]:
    """规范化 fields（[{name,value}]）：过滤无 name 或 value 的空行。"""
    if not isinstance(raw, list):
        return []
    out = []
    for f in raw:
        if isinstance(f, dict) and (str(f.get("name") or "").strip() or str(f.get("value") or "").strip()):
            out.append({"name": str(f.get("name") or "").strip(),
                        "value": str(f.get("value") or "").strip()})
    return out


def _norm_relations(raw: Any) -> list[dict[str, Any]]:
    """规范化 relations（[{to_kind,to_id,name,mult}]）：过滤无 to_id 的空关系，to_kind 合法化。"""
    valid_kinds = {"c", "i", "s", "map", "arcs"}
    if not isinstance(raw, list):
        return []
    out = []
    for r in raw:
        if isinstance(r, dict) and (str(r.get("to_id") or "").strip()):
            to_kind = str(r.get("to_kind") or "s").strip()
            out.append({
                "to_kind": to_kind if to_kind in valid_kinds else "s",
                "to_id": str(r.get("to_id") or "").strip(),
                "name": str(r.get("name") or "关联").strip(),
                "mult": str(r.get("mult") or "").strip(),
            })
    return out


def load_elements(book_root: str | Path) -> dict[str, Any]:
    """元素清单：{characters:[{id,name,alias[],desc,setting_file,fields[],relations[]}],
    items:[...], settings:[...]}。fields/relations 读侧补默认（老卡向后兼容，不写盘）。"""
    data = _read_json(_dot_ainovel(book_root) / _ELEMENTS_FILE,
                      {"characters": [], "items": [], "settings": []})
    for kind in ("characters", "items", "settings", "maps"):
        for e in data.get(kind, []):
            if isinstance(e, dict):
                e.setdefault("fields", [])
                e.setdefault("relations", [])
                e.setdefault("scope", "global")
                e.setdefault("arc_name", "")
    return data


def save_elements(book_root: str | Path, elements: dict[str, Any]) -> None:
    _write_json(_dot_ainovel(book_root) / _ELEMENTS_FILE, elements)


# ════════════════════════════════════════════════════════════════════
# 元素单卡 CRUD（类图卡片视图用：建卡/改卡/删卡，含 fields/relations）
# kind 短码 → 存储集合名映射
# ════════════════════════════════════════════════════════════════════
_KIND_TO_COLLECTION = {"c": "characters", "i": "items", "s": "settings", "l": "locations", "map": "maps"}


def _next_elem_id(elements: dict[str, Any], kind: str) -> str:
    """生成新卡 id：kind 短码 + 递增序号（避开已存在的 id）。"""
    coll = _KIND_TO_COLLECTION.get(kind, "characters")
    prefix = kind if kind in ("c", "i", "s", "map") else "e"
    existing = {e.get("id") for e in elements.get(coll, []) if e.get("id")}
    n = 1
    while f"{prefix}{n}" in existing:
        n += 1
    return f"{prefix}{n}"


def _norm_layout(layout):
    """地图卡布局规范化：[{zone,name,desc}]，仅地图卡用。"""
    if not isinstance(layout, list):
        return []
    out = []
    for z in layout:
        if isinstance(z, dict) and (z.get("zone") or z.get("name")):
            out.append({
                "zone": str(z.get("zone") or ""),
                "name": str(z.get("name") or ""),
                "desc": str(z.get("desc") or ""),
            })
    return out


def add_element(book_root: str | Path, kind: str, name: str, desc: str = "",
                fields: list | None = None, relations: list | None = None,
                layout: list | None = None,
                scope: str = "global", arc_name: str = "") -> dict[str, Any]:
    """新增元素卡。kind: c/i/s/map。返回新卡。"""
    coll = _KIND_TO_COLLECTION.get(kind, "characters")
    elements = load_elements(book_root)
    card = {
        "id": _next_elem_id(elements, kind),
        "name": name or "未命名",
        "alias": [],
        "desc": desc or "",
        "terms": [],
        "setting_file": "",
        "fields": _norm_fields(fields),
        "relations": _norm_relations(relations),
        "layout": _norm_layout(layout) if kind == "map" else [],
        "scope": scope or "global",
        "arc_name": arc_name or "",
    }
    elements.setdefault(coll, []).append(card)
    save_elements(book_root, elements)
    return card


def update_element(book_root: str | Path, kind: str, eid: str, name: str | None = None,
                   desc: str | None = None, fields: list | None = None,
                   relations: list | None = None, alias: list[str] | None = None,
                   terms: list[str] | None = None,
                   scope: str | None = None) -> dict[str, Any] | None:
    """更新元素卡（字段级覆盖，None 跳过）。返回更新后的卡或 None（不存在）。"""
    coll = _KIND_TO_COLLECTION.get(kind, "characters")
    elements = load_elements(book_root)
    for e in elements.get(coll, []):
        if e.get("id") == eid:
            if name is not None:
                e["name"] = name
            if desc is not None:
                e["desc"] = desc
            if fields is not None:
                e["fields"] = _norm_fields(fields)
            if relations is not None:
                e["relations"] = _norm_relations(relations)
            if alias is not None:
                e["alias"] = [str(x).strip() for x in alias if str(x).strip()]
            if terms is not None:
                e["terms"] = [str(x).strip() for x in terms if str(x).strip()]
            if scope is not None:
                e["scope"] = scope
            save_elements(book_root, elements)
            return e
    return None


def delete_element(book_root: str | Path, kind: str, eid: str) -> bool:
    coll = _KIND_TO_COLLECTION.get(kind, "characters")
    elements = load_elements(book_root)
    before = len(elements.get(coll, []))
    elements[coll] = [e for e in elements.get(coll, []) if e.get("id") != eid]
    if len(elements[coll]) < before:
        save_elements(book_root, elements)
        # 清理各弧对它的 selected 引用（防悬空）
        try:
            arcs = load_arcs(book_root)
            changed = False
            for a in arcs.get("arcs", []):
                sel = a.get("selected") or {}
                for k in ("characters", "items", "settings"):
                    if eid in sel.get(k, []):
                        sel[k] = [x for x in sel[k] if x != eid]
                        changed = True
            if changed:
                save_arcs(book_root, arcs)
        except Exception:  # noqa: BLE001
            pass
        return True
    return False


# ════════════════════════════════════════════════════════════════════
# 书级待审批池（候选制：记忆/卡片先进池，用户同意才入库）
# ════════════════════════════════════════════════════════════════════
_PENDING_FILE = "pending.json"


def load_pending(book_root: str | Path) -> list[dict[str, Any]]:
    return _read_json(_dot_ainovel(book_root) / _PENDING_FILE, [])


def save_pending(book_root: str | Path, items: list[dict[str, Any]]) -> None:
    _write_json(_dot_ainovel(book_root) / _PENDING_FILE, items)


def add_pending(book_root: str | Path, item: dict[str, Any]) -> None:
    items = load_pending(book_root)
    items.insert(0, {"id": uuid.uuid4().hex[:8], "at": datetime.now(timezone.utc).isoformat(), **item})
    save_pending(book_root, items)


# ════════════════════════════════════════════════════════════════════
# 场景素材层（fragments）—— 绑定到 l4 场景的可复用内容块
# ════════════════════════════════════════════════════════════════════
_FRAGMENTS_FILE = "fragments.json"


def load_fragments(book_root: str | Path) -> list[dict[str, Any]]:
    return _read_json(_dot_ainovel(book_root) / _FRAGMENTS_FILE, [])


def save_fragments(book_root: str | Path, items: list[dict[str, Any]]) -> None:
    _write_json(_dot_ainovel(book_root) / _FRAGMENTS_FILE, items)


def add_fragment(book_root: str | Path, arc_id: str, ftype: str, content: str,
                 scene_idx: int = 0, beat_idx: list[int] | None = None,
                 fixed: bool = True) -> dict:
    items = load_fragments(book_root)
    frag = {
        "id": f"f{uuid.uuid4().hex[:6]}",
        "arc_id": arc_id,
        "type": ftype,
        "scene_idx": scene_idx,
        "beat_idx": beat_idx or [],
        "content": content,
        "fixed": fixed,
        "at": datetime.now(timezone.utc).isoformat(),
    }
    items.append(frag)
    save_fragments(book_root, items)
    return frag


def update_fragment(book_root: str | Path, fid: str, **kwargs) -> bool:
    items = load_fragments(book_root)
    for f in items:
        if f.get("id") == fid:
            for k, v in kwargs.items():
                if v is not None:
                    f[k] = v
            save_fragments(book_root, items)
            return True
    return False


def delete_fragment(book_root: str | Path, fid: str) -> bool:
    items = load_fragments(book_root)
    before = len(items)
    items = [x for x in items if x.get("id") != fid]
    if len(items) < before:
        save_fragments(book_root, items)
        return True
    return False


# ════════════════════════════════════════════════════════════════════
# 初始化助手对话记录持久化（服务端备份，防浏览器 localStorage 丢失）
# ════════════════════════════════════════════════════════════════════
_CHAT_SESSIONS_FILE = "chat_sessions.json"


def load_chat_sessions(book_root: str | Path) -> list[dict[str, Any]]:
    return _read_json(_dot_ainovel(book_root) / _CHAT_SESSIONS_FILE, [])


def save_chat_sessions(book_root: str | Path, sessions: list[dict[str, Any]]) -> None:
    _write_json(_dot_ainovel(book_root) / _CHAT_SESSIONS_FILE, sessions)


# ════════════════════════════════════════════════════════════════════
# 书级备注系统（三级标注：全局 → 情节 → 场景 → 段级）
# ════════════════════════════════════════════════════════════════════
_NOTES_FILE = "notes.json"


def load_notes(book_root: str | Path) -> list[dict[str, Any]]:
    return _read_json(_dot_ainovel(book_root) / _NOTES_FILE, [])


def save_notes(book_root: str | Path, items: list[dict[str, Any]]) -> None:
    _write_json(_dot_ainovel(book_root) / _NOTES_FILE, items)


def add_note(book_root: str | Path, scope: str, content: str) -> dict:
    items = load_notes(book_root)
    note = {
        "id": f"n{uuid.uuid4().hex[:6]}",
        "scope": scope,
        "content": content,
        "at": datetime.now(timezone.utc).isoformat(),
    }
    items.append(note)
    save_notes(book_root, items)
    return note


def delete_note(book_root: str | Path, nid: str) -> bool:
    items = load_notes(book_root)
    before = len(items)
    items = [x for x in items if x.get("id") != nid]
    if len(items) < before:
        save_notes(book_root, items)
        return True
    return False


def approve_pending(book_root: str | Path, pid: str, edits: dict[str, Any] | None = None) -> bool:
    """同意候选：mem → 记忆库；card → 元素卡。edits 可含编辑后的 text/name/desc/fields。"""
    items = load_pending(book_root)
    edits = edits or {}
    for i, p in enumerate(items):
        if p.get("id") == pid:
            item = items.pop(i)
            save_pending(book_root, items)
            try:
                if item.get("type") == "mem":
                    text = str(edits.get("text") or item.get("text") or "").strip()
                    mems = load_memory(book_root)
                    mems.insert(0, {"id": uuid.uuid4().hex[:8], "text": text,
                                    "at": datetime.now(timezone.utc).isoformat()})
                    save_memory(book_root, mems)
                else:  # card
                    add_element(
                        book_root, item.get("kind", "s"),
                        str(edits.get("name") or item.get("name") or "未命名"),
                        str(edits.get("desc") or item.get("desc") or ""),
                        fields=edits.get("fields") if edits.get("fields") is not None else item.get("fields"),
                        relations=item.get("relations"),
                        scope=item.get("scope", "global"),
                        arc_name=item.get("arc_name", ""),
                    )
                return True
            except Exception:
                return False
    return False


def reject_pending(book_root: str | Path, pid: str) -> bool:
    items = load_pending(book_root)
    before = len(items)
    items = [x for x in items if x.get("id") != pid]
    if len(items) < before:
        save_pending(book_root, items)
        return True
    return False


def approve_all_pending(book_root: str | Path) -> int:
    items = load_pending(book_root)
    n = 0
    for p in list(items):
        if p.get("type") == "mem":
            mems = load_memory(book_root)
            mems.insert(0, {"id": uuid.uuid4().hex[:8], "text": p.get("text", ""),
                            "at": datetime.now(timezone.utc).isoformat()})
            save_memory(book_root, mems)
            n += 1
        else:
            try:
                add_element(book_root, p.get("kind", "s"), p.get("name", "未命名"), p.get("desc", ""),
                            fields=p.get("fields"), relations=p.get("relations"))
                n += 1
            except Exception:
                pass
    save_pending(book_root, [])
    return n


def reject_all_pending(book_root: str | Path) -> int:
    items = load_pending(book_root)
    n = len(items)
    save_pending(book_root, [])
    return n


# ════════════════════════════════════════════════════════════════════
# 书级记忆库（灵感对话注入）
# ════════════════════════════════════════════════════════════════════
_MEMORY_FILE = "memory.json"


def load_memory(book_root: str | Path) -> list[dict[str, Any]]:
    return _read_json(_dot_ainovel(book_root) / _MEMORY_FILE, [])


def save_memory(book_root: str | Path, items: list[dict[str, Any]]) -> None:
    _write_json(_dot_ainovel(book_root) / _MEMORY_FILE, items)


def add_memory(
    book_root: str | Path,
    text: str,
    *,
    scope: str = "book",
    key: str = "",
    arc_id: str = "",
    tags: list[str] | None = None,
) -> dict[str, Any]:
    """新增一条记忆。
    scope: 'book'（全书） | 'arc' + arc_id（某弧） | 'element:<id>'（某元素）
    兼容旧数据：旧记录无 scope/key/tags，读取时按 scope=book 处理。
    """
    text = str(text or "").strip()
    if not text:
        raise ValueError("记忆内容不能为空")
    # scope 简写展开
    _scope = str(scope or "book")
    if _scope == "arc" and arc_id:
        _scope = f"arc:{arc_id}"
    if _scope == "element" and key:
        _scope = f"element:{key}"
    items = load_memory(book_root)
    item = {
        "id": uuid.uuid4().hex[:8],
        "text": text,
        "scope": _scope,
        "key": str(key or "")[:50],
        "arc_id": str(arc_id or ""),
        "tags": [str(t) for t in (tags or []) if t],
        "at": datetime.now(timezone.utc).isoformat(),
    }
    items.insert(0, item)
    save_memory(book_root, items)
    return item


def update_memory(book_root: str | Path, mid: str, text: str) -> bool:
    items = load_memory(book_root)
    for x in items:
        if x.get("id") == mid:
            x["text"] = str(text or "").strip()
            save_memory(book_root, items)
            return True
    return False


def delete_memory(book_root: str | Path, mid: str) -> bool:
    items = load_memory(book_root)
    before = len(items)
    items = [x for x in items if x.get("id") != mid]
    if len(items) < before:
        save_memory(book_root, items)
        return True
    return False


def list_memory(book_root: str | Path, scope: str | None = None, *, arc_id: str = '', key: str = '') -> list[dict[str, Any]]:
    """列出记忆，可选按 scope / arc_id / key 过滤（均为 None/空 返回全部）。

    scope 支持：
      - None / '' = 不过滤
      - 'book' / 'arc' / 'element' = 按大类过滤
      - 'arc:xxx' / 'element:xxx' = 精确匹配
    arc_id / key 作为额外过滤条件（与 scope 叠加）。
    """
    items = load_memory(book_root)
    if not items:
        return []

    result = items
    if scope:
        if ':' in scope:
            # 精确匹配 scope 值
            result = [m for m in result if m.get('scope', 'book') == scope]
        else:
            # 按大类过滤
            result = [m for m in result if m.get('scope', 'book').startswith(scope)]
    if arc_id:
        result = [m for m in result if m.get('arc_id') == arc_id]
    if key:
        result = [m for m in result if m.get('key') == key]
    return result


def _mem_scope(m: dict[str, Any]) -> str:
    """兼容旧数据：没有 scope 字段的视为 book。"""
    return m.get("scope") or "book"


def recall_memory(book_root: str | Path, query: str, *, arc_id: str = "", top_k: int = 8) -> list[dict[str, Any]]:
    """根据 query 关键词召回相关记忆（简单关键词匹配 + scope 加权）。
    优先返回：匹配 query 关键词的 + scope 为 arc 当前弧的 + 全书的。
    """
    items = load_memory(book_root)
    if not items:
        return []
    q = str(query or "").strip()
    q_terms = set()
    if q:
        # 简单切词：2 字以上的字符片段
        for i in range(len(q) - 1):
            q_terms.add(q[i:i+2])
        q_terms.add(q)
    scored: list[tuple[float, dict[str, Any]]] = []
    arc_scope = f"arc:{arc_id}" if arc_id else ""
    for m in items:
        score = 0.0
        text = str(m.get("text") or "") + " " + str(m.get("key") or "")
        # 关键词命中
        if q_terms:
            hits = sum(1 for t in q_terms if t in text)
            score += hits * 1.0
        # scope 加权：当前弧记忆优先
        if arc_scope and _mem_scope(m) == arc_scope:
            score += 3.0
        elif _mem_scope(m) == "book":
            score += 1.0
        # 元素记忆也有一定基础分
        elif _mem_scope(m).startswith("element:"):
            score += 0.5
        if score > 0:
            scored.append((score, m))
    scored.sort(key=lambda x: x[0], reverse=True)
    return [m for _, m in scored[:top_k]]


def load_arcs(book_root: str | Path) -> dict[str, Any]:
    """情节注册表：{next_chapter_num:int, arcs:[{id,name,l1,n_chapters,selected,
    state,chapters[],status,prev_anchor}]}"""
    return _read_json(_dot_ainovel(book_root) / _ARCS_FILE,
                      {"next_chapter_num": 1, "arcs": []})


def save_arcs(book_root: str | Path, arcs: dict[str, Any]) -> None:
    _write_json(_dot_ainovel(book_root) / _ARCS_FILE, arcs)


def _find_arc(arcs: dict[str, Any], arc_id: str) -> dict[str, Any] | None:
    for a in arcs.get("arcs", []):
        if a.get("id") == arc_id:
            return a
    return None


def update_arc_meta(book_root: str | Path, arc_id: str, **fields) -> dict[str, Any] | None:
    """更新弧元信息（name / status / start_chapter 等顶层字段），返回更新后的弧。"""
    arcs = load_arcs(book_root)
    arc = _find_arc(arcs, arc_id)
    if arc is None:
        return None
    for k, v in fields.items():
        if v is not None:
            arc[k] = v
    save_arcs(book_root, arcs)
    return arc


# ════════════════════════════════════════════════════════════════════
# 基本设定（原「初始化」）：可编辑字段源 → 生成 elements.json + 设定集/*.md
# 基本设定 = 书级可编辑配置；elements/设定集 = 生成产物，可重跑
# ════════════════════════════════════════════════════════════════════

_BASIC_SETTINGS_FILE = "basic_settings.json"
_INIT_SYSTEM = (
    "你是网文设定策划。根据用户填写的【基本设定】（结构化字段）与题材模板，"
    "提取本书核心元素与创作基调。输出严格 JSON（不要 Markdown 代码块）。"
)


def _empty_basic_settings() -> dict[str, Any]:
    """16 字段基本设定模板（可编辑字段源）。"""
    return {
        "name": "", "genre": "", "one_liner": "", "style": "",
        "protagonist": {"name": "", "desire": "", "flaw": ""},
        "cast": [],                       # [{name, role, relation}]
        "time_setting": "", "power_system": {"type": "", "name": "", "rules": ""},
        "locations": [],                  # [{name, desc}]
        "conflict": "", "goal": "", "twist": "", "ending": "",
        "items": [],                      # [{name, desc}]
        "anti_trope": "", "opening_hook": "",
        "hard_constraints": [],           # [str]
        "role_setting": "",               # LLM 提炼，供阶梯注入
        "generated_at": "",
    }


def load_basic_settings(book_root: str | Path) -> dict[str, Any]:
    return _read_json(_dot_ainovel(book_root) / _BASIC_SETTINGS_FILE,
                      _empty_basic_settings())


def save_basic_settings(book_root: str | Path, settings: dict[str, Any]) -> None:
    merged = dict(_empty_basic_settings())
    merged.update({k: v for k, v in (settings or {}).items() if v is not None})
    _write_json(_dot_ainovel(book_root) / _BASIC_SETTINGS_FILE, merged)


def list_setting_files(book_root: str | Path) -> dict[str, Any]:
    """设定集文档页数据：基本设定 + 设定集/*.md 文件清单（只读，供前端浏览）。"""
    root = Path(book_root)
    files: list[dict[str, str]] = []
    sdir = root / "设定集"
    if sdir.is_dir():
        for p in sorted(sdir.glob("*.md")):
            try:
                files.append({"name": p.stem, "content": p.read_text(encoding="utf-8")})
            except Exception:  # noqa: BLE001
                continue
    return {"settings": load_basic_settings(book_root), "files": files}


def get_sel_access_options(book_root: str | Path) -> dict[str, Any]:
    """条目级选择性注入的候选项（前端 wb-acc-sel 渲染 chips 用）：
    settings 设定文档名+元素设定名 / arcs 弧 / memory 记忆 / corpus 语料书 / templates 模板。"""
    root = Path(book_root)
    settings: list[str] = []
    sdir = root / "设定集"
    if sdir.is_dir():
        settings += [p.stem for p in sorted(sdir.glob("*.md"))]
    try:
        elements = load_elements(root)
        settings += [str(e.get("name")) for e in elements.get("settings", []) if e.get("name")]
    except Exception:  # noqa: BLE001
        pass
    settings = list(dict.fromkeys(settings))
    arcs = [{"id": a.get("id"), "name": str(a.get("name") or a.get("l1") or a.get("id") or "")}
            for a in load_arcs(root).get("arcs", []) if a.get("id")]
    memory = [{"id": m.get("id"), "key": str(m.get("key") or m.get("text") or ""),
               "text": str(m.get("text") or "")[:24]} for m in load_memory(root)]
    corpus: list[str] = []
    try:
        from . import writing_search
        cd = Path(getattr(writing_search.SETTINGS, "corpus_dir", None) or "")
        if cd.is_dir():
            corpus = sorted(f.relative_to(cd).as_posix()[:-4] for f in cd.rglob("*.txt"))
    except Exception:  # noqa: BLE001
        pass
    templates: list[dict[str, Any]] = []
    try:
        from .plot_library import get_plot_template_library
        templates = [{"id": t.get("id"), "name": str(t.get("name") or t.get("id") or "")}
                     for t in get_plot_template_library().list()]
    except Exception:  # noqa: BLE001
        pass
    return {"settings": settings, "arcs": arcs, "memory": memory, "corpus": corpus, "templates": templates}


_SHORT_DRAMA_DIR = Path(__file__).resolve().parents[1] / "corpus" / "短剧" / "红果"
_USER_DRAMA_JSONL = _SHORT_DRAMA_DIR / "用户剧情库.jsonl"
_USER_DRAMA_TXT = _SHORT_DRAMA_DIR / "用户剧情库.txt"


def list_short_dramas() -> dict[str, Any]:
    """短剧库：红果自动采集剧名（auto）+ 用户录入剧情（user）。"""
    auto: list[dict[str, Any]] = []
    if _SHORT_DRAMA_DIR.is_dir():
        p = _SHORT_DRAMA_DIR / "红果剧名库.jsonl"
        if p.is_file():
            for line in p.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    try:
                        auto.append(json.loads(line))
                    except Exception:  # noqa: BLE001
                        continue
    user: list[dict[str, Any]] = []
    if _USER_DRAMA_JSONL.is_file():
        for line in _USER_DRAMA_JSONL.read_text(encoding="utf-8").splitlines():
            if line.strip():
                try:
                    user.append(json.loads(line))
                except Exception:  # noqa: BLE001
                    continue
    return {"auto": auto, "user": user}


def add_user_drama(name: str, tags: list[str] | None = None, intro: str = "") -> dict[str, Any]:
    """录入一条用户剧情（红果官方详情界面有简介，手动贴入）。写 jsonl + 刷新 txt 检索镜像。"""
    from datetime import datetime  # noqa: PLC0415

    _SHORT_DRAMA_DIR.mkdir(parents=True, exist_ok=True)
    item = {"name": str(name).strip(), "tags": [str(t).strip() for t in (tags or []) if str(t).strip()],
            "intro": str(intro).strip(), "ts": datetime.now().strftime("%Y-%m-%d %H:%M")}
    if not item["name"]:
        return {"ok": False, "error": "剧名不能为空"}
    with open(_USER_DRAMA_JSONL, "a", encoding="utf-8") as f:
        f.write(json.dumps(item, ensure_ascii=False) + "\n")
    # txt 镜像：每行「剧名｜标签、标签｜简介」（语料 BM25 可命中简介内容）
    rows = []
    if _USER_DRAMA_JSONL.is_file():
        for line in _USER_DRAMA_JSONL.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                d = json.loads(line)
                tag_txt = "、".join(d.get("tags") or []) or "待看"
                rows.append(f"{d.get('name')}｜{tag_txt}｜{d.get('intro', '')}")
            except Exception:  # noqa: BLE001
                continue
    _USER_DRAMA_TXT.write_text("\n".join(rows), encoding="utf-8")
    return {"ok": True, "item": item, "count": len(rows)}


def fetch_redguo_dramas() -> dict[str, Any]:
    """触发红果剧名采集（重新抓列表页）。"""
    import importlib  # noqa: PLC0415
    try:
        mod = importlib.import_module("harness_shortdrama_fetch")
    except Exception:  # noqa: BLE001
        sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
        mod = importlib.import_module("harness_shortdrama_fetch")
    items = mod.fetch_redguo()
    return {"ok": True, "count": len(items)}


def _fmt_person(c: dict[str, Any]) -> str:
    name = str(c.get("name") or "").strip()
    role = str(c.get("role") or "").strip()
    rel = str(c.get("relation") or "").strip()
    parts = [name] if name else []
    if role:
        parts.append(f"身份：{role}")
    if rel:
        parts.append(f"与主角：{rel}")
    return "/".join(parts)


def _fmt_power(ps: dict[str, Any]) -> str:
    parts = []
    if str(ps.get("type") or "").strip():
        parts.append(str(ps["type"]).strip())
    if str(ps.get("name") or "").strip():
        parts.append(str(ps["name"]).strip())
    if str(ps.get("rules") or "").strip():
        parts.append(str(ps["rules"]).strip())
    return "，".join(parts) or "未填"


def _fmt_named(e: dict[str, Any]) -> str:
    name = str(e.get("name") or "").strip()
    desc = str(e.get("desc") or "").strip()
    return f"{name}（{desc}）" if desc else name


def _basic_settings_prompt_block(s: dict[str, Any]) -> str:
    """把 16 字段基本设定压成一段结构化 prompt 文本。"""
    lines: list[str] = []
    if str(s.get("one_liner") or "").strip():
        lines.append(f"一句话故事：{s['one_liner'].strip()}")
    if str(s.get("style") or "").strip():
        lines.append(f"风格基调：{s['style'].strip()}")
    p = s.get("protagonist") or {}
    if str(p.get("name") or "").strip():
        lines.append(f"主角：{str(p['name']).strip()}")
        if str(p.get("desire") or "").strip():
            lines.append(f"  主角欲望：{str(p['desire']).strip()}")
        if str(p.get("flaw") or "").strip():
            lines.append(f"  主角缺陷：{str(p['flaw']).strip()}")
    cast = s.get("cast") or []
    named_cast = [_fmt_person(c) for c in cast if str(c.get("name") or "").strip()]
    if named_cast:
        lines.append("出场人物：" + "；".join(named_cast))
    if str(s.get("time_setting") or "").strip():
        lines.append(f"时间背景：{s['time_setting'].strip()}")
    ps = s.get("power_system") or {}
    if str(ps.get("name") or "").strip() or str(ps.get("type") or "").strip():
        lines.append(f"力量体系/金手指：{_fmt_power(ps)}")
    locs = s.get("locations") or []
    named_locs = [_fmt_named(l) for l in locs if str(l.get("name") or "").strip()]
    if named_locs:
        lines.append("关键地点/势力：" + "；".join(named_locs))
    for label, key in (("核心冲突", "conflict"), ("目标", "goal"),
                       ("转折", "twist"), ("结局悬念", "ending")):
        v = str(s.get(key) or "").strip()
        if v:
            lines.append(f"{label}：{v}")
    items = s.get("items") or []
    named_items = [_fmt_named(i) for i in items if str(i.get("name") or "").strip()]
    if named_items:
        lines.append("关键道具：" + "；".join(named_items))
    if str(s.get("anti_trope") or "").strip():
        lines.append(f"反套路规则：{s['anti_trope'].strip()}")
    if str(s.get("opening_hook") or "").strip():
        lines.append(f"开篇钩子：{s['opening_hook'].strip()}")
    hc = [str(x).strip() for x in (s.get("hard_constraints") or []) if str(x).strip()]
    if hc:
        lines.append("硬约束：" + "；".join(hc))
    return "\n".join(lines) or "（基本设定未填写）"


def _genre_template_excerpt(genre: str, max_chars: int = 1400) -> str:
    """按类型匹配 app/templates/genres/*.md，取题材要点（核心卖点/流派/世界观）。

    genre 按 +/空格/逗号拆 token，与文件 stem 双向子串匹配（"玄幻+修仙"→修仙.md）。
    """
    genre = str(genre or "").strip()
    if not genre:
        return ""
    base = Path(__file__).resolve().parents[2] / "app" / "templates" / "genres"
    if not base.is_dir():
        return ""
    tokens = [t.strip() for t in re.split(r"[+\s,，/]", genre) if t.strip()]
    best_file, best_len = None, 0
    try:
        for p in base.glob("*.md"):
            stem = p.stem.strip()
            for tok in tokens:
                if len(tok) >= 2 and stem and (stem in tok or tok in stem) \
                        and len(tok) > best_len:
                    best_file, best_len = p, len(tok)
    except Exception:  # noqa: BLE001
        pass
    if best_file is None:
        return ""
    try:
        text = best_file.read_text(encoding="utf-8")[:max_chars]
        return f"【题材模板：{best_file.stem}】\n{text}"
    except Exception:  # noqa: BLE001
        return ""


def _merge_elements(existing: dict[str, Any], fresh: dict[str, Any]) -> dict[str, Any]:
    """合并元素清单：保留用户已手改的条目（desc/alias/terms 已填的不覆盖），
    只补新增 + 填空缺；按 name 匹配。"""
    out: dict[str, Any] = {"characters": [], "items": [], "settings": [], "maps": []}
    for kind in ("characters", "items", "settings", "maps"):
        by_name: dict[str, dict[str, Any]] = {}
        for e in existing.get(kind, []):
            if e.get("name"):
                by_name[str(e["name"])] = dict(e)
        prefix = {"characters": "c", "items": "i", "settings": "s", "maps": "map"}[kind]
        count = len(by_name)
        for fe in fresh.get(kind, []):
            nm = str(fe.get("name") or "").strip()
            if not nm:
                continue
            if nm in by_name:
                cur = by_name[nm]
                if not cur.get("desc") and fe.get("desc"):
                    cur["desc"] = fe["desc"]
                if not cur.get("alias") and fe.get("alias"):
                    cur["alias"] = list(fe["alias"])
                if not cur.get("terms") and fe.get("terms"):
                    cur["terms"] = list(fe["terms"])
                # 保留 fields/relations：已有才保留，不覆盖用户已建关系；缺失才补
                if not cur.get("fields") and fe.get("fields"):
                    cur["fields"] = _norm_fields(fe.get("fields"))
                if not cur.get("relations") and fe.get("relations"):
                    cur["relations"] = _norm_relations(fe.get("relations"))
            else:
                count += 1
                fe2 = dict(fe)
                fe2["id"] = f"{prefix}{count}"
                fe2.setdefault("alias", [])
                fe2.setdefault("terms", [])
                fe2.setdefault("desc", "")
                fe2["fields"] = _norm_fields(fe.get("fields"))
                fe2["relations"] = _norm_relations(fe.get("relations"))
                fe2.setdefault("setting_file", "")
                by_name[nm] = fe2
        out[kind] = list(by_name.values())
    return out


async def generate_settings(
    book_root: str | Path,
    settings: dict[str, Any] | None = None,
    *,
    title: str = "",
    genre: str = "",
) -> dict[str, Any]:
    """生成基本设定的派生产物：elements.json（表单种子 → LLM 补全 desc/alias/terms）
    + 设定集/*.md + style/role_setting。

    **可重跑**：改基本设定后再生成 → 覆盖设定集 + 更新 elements（保留用户手改条目）。
    两次 LLM 调用：① 元素清单+基调；② 设定集 markdown 文件。
    """
    from .derive import _tree_llm

    book_root = Path(book_root)
    _wb_ctx(book_root, step="settings_generate")
    s = dict(settings or load_basic_settings(book_root))
    title = title or str(s.get("name") or "")
    genre = genre or str(s.get("genre") or "")
    if not title:
        return {"ok": False, "error": "基本设定缺少书名"}

    blocks = _basic_settings_prompt_block(s)
    genre_blk = _genre_template_excerpt(genre)

    # ── 调用 1：元素清单 + 基调 ───────────────────────────────────
    usr1 = (
        "【书名】" + (title or "（未命名）") + "\n"
        "【类型】" + (genre or "（未指定）") + "\n"
        "【基本设定】\n" + blocks
        + (("\n\n" + genre_blk) if genre_blk else "")
        + "\n\n请提取：\n"
        '{"style": "全书文风基调（2-4 句，描述叙事口吻/节奏/质感，供正文生成注入）",\n'
        ' "role_setting": "主角与世界观核心设定（一段话，供情节线/场景展开注入）",\n'
        ' "characters": [{"name": "角色名", "alias": ["别名/简称/绰号"], '
        '"desc": "身份与关键特质（30-60字）"}],\n'
        ' "items": [{"name": "物品名", "alias": [], "desc": "用途与重要性"}],\n'
        ' "settings": [{"name": "设定/势力/地点名", "terms": ["固定叫法/专属术语"], '
        '"desc": "与剧情相关的关键设定"}]}\n'
        "主角与出场人物、关键道具、关键地点/势力必须全部出现（保留用户填的名字，"
        "只需补 desc/alias/terms）；可补充必要配角/设定（≤8 角色、≤6 物品、≤8 设定）；"
        "不编造基本设定没有的东西。"
    )
    try:
        d1 = await _tree_llm(_INIT_SYSTEM, usr1, "ai_creation_settings_elements",
                             max_tokens=4000)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": "元素提取失败：" + str(e)[:200]}

    style = str(d1.get("style") or "").strip() or str(s.get("style") or "").strip()
    role_setting = str(d1.get("role_setting") or "").strip()
    fresh: dict[str, Any] = {
        "characters": [_norm_elem(c, "characters", i) for i, c in
                       enumerate((d1.get("characters") or []) if isinstance(d1, dict) else [])],
        "items": [_norm_elem(c, "items", i) for i, c in
                  enumerate((d1.get("items") or []) if isinstance(d1, dict) else [])],
        "settings": [_norm_elem(c, "settings", i) for i, c in
                     enumerate((d1.get("settings") or []) if isinstance(d1, dict) else [])],
    }
    fresh = {k: [e for e in v if e.get("name")] for k, v in fresh.items()}
    elements = _merge_elements(load_elements(book_root), fresh)
    save_elements(book_root, elements)

    # ── 调用 2：设定集 markdown 文件 ─────────────────────────────
    files: list[dict[str, str]] = []
    usr2 = (
        "根据基本设定与元素清单，为本书撰写设定集 markdown 文件。"
        "输出严格 JSON（不要 Markdown 代码块）：\n"
        '{"files": [{"name": "世界观.md", "content": "markdown 内容"}, '
        '{"name": "主角卡.md", "content": "…"}, ...]}\n'
        "文件建议：世界观、主角卡、主要角色、关键物品、核心剧情"
        "（玄幻武侠类可加力量体系，都市类可加背景设定，视类型取舍 4-7 个）。"
        "每个文件内容 150-600 字，具体可写、服务后续写作。"
        "【基本设定】\n" + blocks
        + "\n\n【元素清单】\n" + json.dumps(elements, ensure_ascii=False)
        + (("\n\n" + genre_blk) if genre_blk else "")
    )
    try:
        d2 = await _tree_llm(_INIT_SYSTEM, usr2, "ai_creation_settings_files",
                             max_tokens=7000)
        raw = d2.get("files") if isinstance(d2, dict) else None
        if isinstance(raw, list):
            for f in raw:
                if isinstance(f, dict) and str(f.get("name") or "").strip():
                    files.append({
                        "name": str(f["name"]).strip(),
                        "content": str(f.get("content") or "").strip(),
                    })
    except Exception:  # noqa: BLE001
        files = []

    written: list[str] = []
    if files:
        set_dir = book_root / "设定集"
        set_dir.mkdir(parents=True, exist_ok=True)
        for f in files:
            if not f.get("content"):
                continue
            name = re_safe_filename(f["name"])
            if not name.lower().endswith(".md"):
                name += ".md"
            (set_dir / name).write_text(f["content"], encoding="utf-8")
            written.append(name)

    # 存回：style/role_setting/generated_at
    s["style"] = style or s.get("style", "")
    s["role_setting"] = role_setting
    s["generated_at"] = _now()
    save_basic_settings(book_root, s)

    return {
        "ok": True,
        "style": style,
        "role_setting": role_setting,
        "elements": elements,
        "setting_files": written,
    }


async def init_book(
    book_root: str | Path,
    brief: str,
    *,
    title: str = "",
    genre: str = "",
) -> dict[str, Any]:
    """兼容薄封装：自由文本 brief → 基本设定（one_liner）→ generate_settings。"""
    s = _empty_basic_settings()
    s["name"] = title
    s["genre"] = genre
    s["one_liner"] = str(brief or "").strip()
    return await generate_settings(book_root, s, title=title, genre=genre)


def _constraints_block(hard_constraints: list[str] | None) -> str:
    """硬约束块【本情节硬约束】——与元素白名单并列，从 l3 起注入生成。"""
    hc = [str(x).strip() for x in (hard_constraints or []) if str(x).strip()]
    if not hc:
        return ""
    return "【本情节硬约束】（硬性，必须遵守）\n" + "\n".join(f"- {x}" for x in hc)


def _book_constraints_block(book_root: str | Path) -> str:
    """从书基本设定读硬约束 → 硬约束块（空则返回空串）。"""
    try:
        return _constraints_block(load_basic_settings(book_root).get("hard_constraints") or [])
    except Exception:  # noqa: BLE001
        return ""

    return {
        "ok": True,
        "style": style,
        "role_setting": role_setting,
        "elements": elements,
        "setting_files": written,
    }


def re_safe_filename(name: str) -> str:
    import re
    # 只保留 windows 文件名非法字符，中文/字母/数字正常保留
    name = re.sub(r"[<>:\"/\\|?*\x00-\x1f]", "", str(name or "").strip())
    return (name or "设定").strip()


def _norm_elem(raw: Any, kind: str, idx: int) -> dict[str, Any]:
    """把 LLM 返回的元素条目规范成内部结构（id/name/alias/desc/terms/fields/relations）。"""
    if not isinstance(raw, dict):
        return {}
    name = str(raw.get("name") or "").strip()
    prefix = {"characters": "c", "items": "i", "settings": "s", "maps": "map"}.get(kind, "e")
    return {
        "id": f"{prefix}{idx + 1}",
        "name": name,
        "alias": [str(a).strip() for a in (raw.get("alias") or [])
                  if str(a).strip()] if isinstance(raw.get("alias"), list) else [],
        "desc": str(raw.get("desc") or "").strip(),
        "terms": [str(t).strip() for t in (raw.get("terms") or [])
                  if str(t).strip()] if isinstance(raw.get("terms"), list) else [],
        "setting_file": str(raw.get("setting_file") or "").strip(),
        "fields": _norm_fields(raw.get("fields")),
        "relations": _norm_relations(raw.get("relations")),
    }


# ════════════════════════════════════════════════════════════════════
# 元素白名单块：从 l3 起注入（_element_block），未选元素禁令
# ════════════════════════════════════════════════════════════════════

def _names_of(elem: dict[str, Any]) -> list[str]:
    """元素可匹配的称呼列表（name + alias + terms）。"""
    out = [elem.get("name"), *elem.get("alias", []), *elem.get("terms", [])]
    return [str(x).strip() for x in out if str(x or "").strip()]


def _desc_of(elem: dict[str, Any], cap: int = 60) -> str:
    d = str(elem.get("desc") or "").strip()
    return d[:cap] + ("…" if len(d) > cap else "")


def _notes_block(arc_id: str, scene_idx: int, beat_indices: list[int],
                 notes: list[dict]) -> str:
    """组装备注注入块：全局 → 情节 → 场景 → 段级。"""
    lines = []
    global_notes = [n for n in notes if n.get("scope") == "global"]
    arc_notes = [n for n in notes if n.get("scope") == f"arc:{arc_id}"]
    scene_notes = [n for n in notes if n.get("scope") == f"arc:{arc_id}:scene{scene_idx}"]
    beat_str = ",".join(str(i) for i in beat_indices)
    beat_notes = [n for n in notes if f"beats:{beat_str}" in n.get("scope", "")]

    if global_notes:
        lines.append("【全局备注】")
        lines.extend(n.get("content") or n.get("text") or "" for n in global_notes)
    if arc_notes:
        lines.append("【情节备注】")
        lines.extend(n.get("content") or n.get("text") or "" for n in arc_notes)
    if scene_notes:
        lines.append("【场景备注】")
        lines.extend(n.get("content") or n.get("text") or "" for n in scene_notes)
    if beat_notes:
        lines.append("【段级备注】")
        lines.extend(n.get("content") or n.get("text") or "" for n in beat_notes)
    return "\n".join(lines)


def _fragments_block(scene_idx: int, fragments: list[dict]) -> str:
    """组装场景素材注入块。

    素材文本里的 [xxx] 是留空占位：l5 生成时 LLM 按 xxx 意图填充（不再有独立 blanks 记录）。
    """
    scene_frags = [f for f in fragments if f.get("scene_idx") == scene_idx]
    if not scene_frags:
        return ""
    lines = ["【场景素材】（以下内容必须在正文中体现）"]
    for f in scene_frags:
        content = f.get("content", "")
        lines.append(f"[{f.get('type', '素材')}] {content}")
    return "\n".join(lines)


def _element_block(arc: dict[str, Any], elements: dict[str, Any]) -> str:
    """白名单注入块：本情节参与元素（应当出现）+ 未参与元素（禁止出现）。

    只列出 arc.selected 里的元素；未选元素逐一列名成禁令清单。
    由 bridge.step_ladder 从 state["element_block"] 拼进 l3/l4/l5 prompt。
    """
    sel = arc.get("selected") or {}
    sel_c = set(sel.get("characters") or [])
    sel_i = set(sel.get("items") or [])
    sel_s = set(sel.get("settings") or [])

    part_c = [e for e in elements.get("characters", []) if e.get("id") in sel_c]
    part_i = [e for e in elements.get("items", []) if e.get("id") in sel_i]
    part_s = [e for e in elements.get("settings", []) if e.get("id") in sel_s]
    ban_c = [e for e in elements.get("characters", []) if e.get("id") not in sel_c]
    ban_i = [e for e in elements.get("items", []) if e.get("id") not in sel_i]
    ban_s = [e for e in elements.get("settings", []) if e.get("id") not in sel_s]

    if not (part_c or part_i or part_s):
        return ""  # 未选择参与元素 → 不注入（l3 起由 arc_step 强制先选择）

    lines: list[str] = ["【本情节参与元素】（硬性约束：本情节各章只能出现以下元素——"]
    if part_c:
        lines.append("参与角色：" + "、".join(f"{e['name']}（{_desc_of(e)}）" for e in part_c))
    if part_i:
        lines.append("参与物品：" + "、".join(f"{e['name']}（{_desc_of(e)}）" for e in part_i))
    if part_s:
        lines.append("参与设定：" + "、".join(
            f"{e['name']}（{'/'.join(_names_of(e))}）" for e in part_s))
    lines.append("以上元素应当出现，其相关剧情展开完整；")
    # 【v7.8.3 元素脑补修复】白名单只覆盖「库内未选元素」的禁令；元素库外的
    # 新实体（新角色/物品/地点/组织）不受覆盖 → 模型会脑补（实测弧B 凭空造「林川」）。
    # 补一条「禁元素库外脑补」：剧情角色只限于参与元素；交代性路人无名/无戏份/不参与核心剧情。
    lines.append("**绝对不得凭空创造元素清单以外的角色/物品/地点/组织**——")
    lines.append("剧情中出现的角色、物品、地点、组织只限于上面的参与元素；")
    lines.append("必要的交代性路人（无名侍从/路人）可以至多一笔带过，但不得给名字、")
    lines.append("不得有戏份、不得参与核心剧情，更不得安排其登场推动情节。")

    def _ban_label(e: dict[str, Any]) -> str:
        names = _names_of(e)
        if len(names) > 1:
            return f"{e['name']}（{'/'.join(names)}）"
        return str(e.get("name") or "")

    if ban_c or ban_i or ban_s:
        lines.append("【本情节未参与元素】（禁止出现——绝对不得写出它们的名字/别名/称呼/")
        lines.append("身份代称，也不得安排其登场或让其参与剧情）：")
        if ban_c:
            lines.append("禁出角色：" + "、".join(_ban_label(e) for e in ban_c))
        if ban_i:
            lines.append("禁出物品：" + "、".join(_ban_label(e) for e in ban_i))
        if ban_s:
            lines.append("禁出设定：" + "、".join(_ban_label(e) for e in ban_s))
    lines.append("】")
    return "\n".join(lines)


# ════════════════════════════════════════════════════════════════════
# 情节管理：new / select / step / confirm / modify / chat
# ════════════════════════════════════════════════════════════════════

def _filled(state: dict[str, Any], level: str) -> bool:
    lv = (state.get("levels") or {}).get(level) or {}
    if level == "l3":
        return lv.get("data") is not None
    if level == "l4":
        return bool(lv.get("scenes"))
    return bool(str(lv.get("text") or "").strip())


def _next_target(state: dict[str, Any]) -> str | None:
    """当前阶梯里已填的最高级 → 下一步应生成的目标级。"""
    order = ("l1", "l2", "l3", "l4", "l5")
    src = None
    for k in order:
        if _filled(state, k):
            src = k
    if src is None or src == "l5":
        return None
    return order[order.index(src) + 1]


def _has_selection(arc: dict[str, Any]) -> bool:
    sel = arc.get("selected") or {}
    return bool(sel.get("characters") or sel.get("items") or sel.get("settings"))


def _reset_downstream(state: dict[str, Any], keep: str) -> None:
    """清空 keep 级之后的所有级（选择/修改生效后，下游须重生成）。"""
    levels = state.setdefault("levels", {})
    order = ("l1", "l2", "l3", "l4", "l5")
    for k in order[order.index(keep) + 1:]:
        levels[k] = {"text": "", "data": None, "scenes": [], "confirmed": False,
                     "prompt": ""}
    if keep == "l2":
        state.pop("element_block", None)


def _auto_arc_brief(book_root: str | Path, arc_no: int = 1) -> str:
    """批量生成用：从基本设定自动拼一条一句话极简剧情（第 arc_no 条情节）。

    有 one_liner 用 one_liner 作故事核；否则用 goal/conflict；再否则主角名 + 主题。
    返回非空字符串；空 = 基本设定没内容，调用方应停止。
    """
    s = load_basic_settings(book_root)
    oneliner = str(s.get("one_liner") or "").strip()
    goal = str(s.get("goal") or "").strip()
    conflict = str(s.get("conflict") or "").strip()
    prot = s.get("protagonist") or {}
    pname = str(prot.get("name") or "").strip()
    genre = str(s.get("genre") or "").strip()

    if oneliner:
        if arc_no <= 1:
            return f"{oneliner}（开篇）"
        return f"{oneliner}（第{arc_no}条情节，承接前文，推进冲突）"
    if goal or conflict:
        parts = [goal, conflict]
        base = "；".join(p for p in parts if p)
        return f"{base}（第{arc_no}条情节）"
    if pname:
        return f"{pname}在{genre or '这个世界'}中经历新的考验与抉择（第{arc_no}条情节）"
    return "主角遭遇一场意外事件，迫使他做出关键抉择（第{arc_no}条情节）"


async def new_arc(
    book_root: str | Path,
    *,
    l1: str,
    n_chapters: int = 1,
    style: str = "",
    role_setting: str = "",
    selected: dict[str, list[str]] | None = None,
    carry_prev: bool = True,
    auto_diagnose: bool = True,
    auto_fill: bool = True,
) -> dict[str, Any]:
    """新建一条剧情情节：写 l1 极简剧情 → 生成 bridge state（l1 已填已确认）。

    carry_prev=True 且已有已完成情节时，自动把上一情节末章正文压缩成前文锚点
    （extract_key_facts），注入本情节 l2/l3 prompt 实现逐情节衔接。

    【2026-08-15 容量诊断】auto_diagnose=True 时对 l1 做容量诊断（能撑多少字/章）；
    auto_fill=True 且诊断出 should_fill 时自动补齐 l1（保留 l1_original + confirmed=False，
    前端可还原）。诊断 0-1 次 LLM、补齐 1 次，不破坏原意。
    """
    from . import bridge
    from .ladder import extract_key_facts

    book_root = Path(book_root)
    _wb_ctx(book_root, step="new_arc")
    arcs = load_arcs(book_root)
    elements = load_elements(book_root)
    l1 = str(l1 or "").strip()
    if not l1:
        return {"ok": False, "error": "l1 一句话极简剧情不能为空"}

    prev_anchor: list[str] = []
    prev_arc: dict[str, Any] | None = None
    for a in reversed(arcs.get("arcs", [])):
        if a.get("status") == "done":
            prev_arc = a
            break
    if carry_prev and prev_arc:
        # 取上一情节末章正文（l5）压成关键事实
        last_txt = _last_arc_text(prev_arc)
        if last_txt:
            try:
                prev_anchor = await extract_key_facts(last_txt[:4000])
            except Exception:  # noqa: BLE001
                prev_anchor = []

    # 【v7.7 消费闭环】用户手写 l1 → 自动命中情节模板库（相似度达标才套用）。
    # 命中则带 template 进阶梯：l2/l3/l4 注入模板格式参考、l4/l5 套用策略
    # （scene_density/retry_cap/target_len）。失败静默降级为无模板，不阻断建弧。
    template: dict[str, Any] | None = None
    archetype = ""
    template_sim: float | None = None
    try:
        from .plot_library import match_plot_templates, get_plot_template_library
        hits = await match_plot_templates(
            l1, style=style, role_setting=role_setting, top_k=3,
        )
        hit = hits[0] if hits else None
        if hit and float(hit.get("similarity") or 0) >= TEMPLATE_MATCH_MIN_SIM:
            template = hit
            archetype = str(hit.get("archetype") or "").strip()
            template_sim = hit.get("similarity")
            # 【Phase C·反馈闭环】命中自增 usage（静默失败不阻断建弧）
            try:
                get_plot_template_library().bump_usage(str(hit.get("id") or ""))
            except Exception:  # noqa: BLE001
                pass
            # 【2026-08-22】命中记录进 template_hits 表——/connections/overview
            # 不再全量扫 workbench 日志（199MB 文件的 O(全部字节) 扫盘就此消灭）
            try:
                from datetime import datetime, timezone as _tz
                from .ainovel_db import get_conn as _gc
                _gc().execute(
                    "INSERT INTO template_hits(ts,book,arc_name,template_id,"
                    "template_name,score,matched_via) VALUES(?,?,?,?,?,?,?)",
                    (datetime.now(_tz.utc).isoformat(),
                     Path(book_root).name or "", "",
                     str(hit.get("id") or ""),
                     str(hit.get("name") or ""),
                     float(hit.get("similarity") or 0), "new_arc"))
                _gc().commit()
            except Exception:  # noqa: BLE001
                pass
    except Exception:  # noqa: BLE001
        template, archetype, template_sim = None, "", None

    # 【2026-08-15 容量诊断】l1 容量估算 + 三维诊断 → 不足则自动补齐。
    # 诊断 0-1 次 LLM（use_facts=True 时 extract_key_facts，按 l1 hash 缓存）；
    # 补齐 1 次 LLM（仅 should_fill 且 guard 通过）。失败静默降级，不阻断建弧。
    diagnosis = None
    l1_original: str | None = None
    l1_filled = False
    if auto_diagnose and l1:
        try:
            from .capacity import diagnose_arc_capacity, enrich_l1
            diagnosis = await diagnose_arc_capacity(
                l1, style=style, role_setting=role_setting,
                template=template, n_chapters=n_chapters)
            if auto_fill and diagnosis.get("should_fill"):
                filled = await enrich_l1(
                    l1, diagnosis, style=style, role_setting=role_setting)
                if filled.get("ok") and filled.get("text") and filled["text"] != l1:
                    l1_original, l1_filled = l1, True
                    l1 = filled["text"]
                    diagnosis = await diagnose_arc_capacity(
                        l1, style=style, role_setting=role_setting,
                        template=template, n_chapters=n_chapters)
        except Exception:  # noqa: BLE001 —— 诊断/补齐失败绝不影响建弧
            diagnosis, l1_original, l1_filled = None, None, False

    state = bridge.new_state(
        l1=l1,
        n_chapters=max(1, int(n_chapters or 1)),
        style=style,
        role_setting=role_setting,
        template=template,
        archetype=archetype,
    )
    if template is not None:
        state["template_sim"] = template_sim
    arc = {
        "id": "arc_" + uuid.uuid4().hex[:12],
        "name": f"情节{len(arcs.get('arcs', [])) + 1}",
        "l1": l1,
        "l2": "",
        "n_chapters": max(1, int(n_chapters or 1)),
        "selected": {"characters": [], "items": [], "settings": []},
        "state": state,
        "chapters": [],
        "status": "draft",
        "prev_anchor": prev_anchor,
        "prev_arc_id": (prev_arc or {}).get("id"),
        "capacity": diagnosis,
        "l1_original": l1_original,
        "l1_filled": l1_filled,
    }
    if selected:
        arc["selected"] = {
            "characters": [str(x) for x in selected.get("characters", [])],
            "items": [str(x) for x in selected.get("items", [])],
            "settings": [str(x) for x in selected.get("settings", [])],
        }
    arcs.setdefault("arcs", []).append(arc)
    save_arcs(book_root, arcs)

    return {
        "ok": True,
        "arc": _arc_view(arc, elements),
        "prev_anchor": prev_anchor,
        "diagnosis": diagnosis,
        "l1_original": l1_original,
        "l1_filled": l1_filled,
    }


def delete_arc(book_root: str | Path, arc_id: str) -> dict[str, Any]:
    """删除一条情节弧（从 arcs 移除；next_chapter_num 不回退）。"""
    book_root = Path(book_root)
    arcs = load_arcs(book_root)
    idx = next((i for i, a in enumerate(arcs.get("arcs", [])) if a.get("id") == arc_id), None)
    if idx is None:
        return {"ok": False, "error": f"情节不存在：{arc_id}"}
    arcs["arcs"].pop(idx)
    save_arcs(book_root, arcs)
    return {"ok": True, "arc_id": arc_id}


def delete_chapter(book_root: str | Path, arc_id: str, idx: int) -> dict[str, Any]:
    """删除本情节的某一章（从 l3.data.chapters 移除，清下游 l4/l5、复位 active_chapter）。"""
    book_root = Path(book_root)
    arcs = load_arcs(book_root)
    arc = _find_arc(arcs, arc_id)
    if not arc:
        return {"ok": False, "error": f"情节不存在：{arc_id}"}
    state = arc.setdefault("state", {})
    l3data = (state.get("levels") or {}).get("l3") or {}
    chapters = l3data.get("chapters") or []
    try:
        idx = int(idx)
    except (ValueError, TypeError):
        idx = 0
    if not 0 <= idx < len(chapters):
        return {"ok": False, "error": f"章不存在：第 {idx + 1} 章"}
    del chapters[idx]
    if isinstance(l3data, dict):
        l3data["chapters"] = chapters
    active = state.get("active_chapter", 0)
    if active >= len(chapters):
        state["active_chapter"] = max(0, len(chapters) - 1)
    # 清下游：l4/l5（当前章场景/正文）
    lv = state.setdefault("levels", {})
    lv.pop("l4", None)
    lv.pop("l5", None)
    # 已落盘章数组也移除该索引（若存在）
    fin = arc.get("chapters") or []
    if 0 <= idx < len(fin):
        fin.pop(idx)
        arc["chapters"] = fin
    save_arcs(book_root, arcs)
    return {"ok": True, "chapters": chapters, "active_chapter": state.get("active_chapter", 0)}


def arc_set_template(
    book_root: str | Path,
    arc_id: str,
    template_id: str = "",
) -> dict[str, Any]:
    """套用/清除本情节的情节模板（template_id="" 清除）。

    套用指定模板 → state["template"]=模板全量（levels+strategy），l2-l5 在新格式/
    策略下重生成（l1 保留）。清除 → 恢复无模板生成。持久化。
    """
    from .plot_library import get_plot_template_library

    book_root = Path(book_root)
    arcs = load_arcs(book_root)
    arc = _find_arc(arcs, arc_id)
    if not arc:
        return {"ok": False, "error": f"情节不存在：{arc_id}"}
    state = arc.setdefault("state", {})
    template_id = str(template_id or "").strip()
    if template_id:
        tpl = get_plot_template_library().get(template_id)
        if not tpl:
            return {"ok": False, "error": f"模板不存在：{template_id}"}
        state["template"] = tpl
        state["archetype"] = str(tpl.get("archetype") or "").strip()
        state.pop("template_sim", None)
        # 【Phase C·反馈闭环】手动套用也自增 usage（静默失败）
        try:
            get_plot_template_library().bump_usage(template_id)
        except Exception:  # noqa: BLE001
            pass
    else:
        state.pop("template", None)
        state.pop("template_sim", None)
        state["archetype"] = ""
    # 模板格式/策略影响 l2-l5 → 全清重生成（l1 保留；元素选择不受影响）
    _reset_downstream(state, "l1")
    arc["l2"] = ""
    save_arcs(book_root, arcs)
    elements = load_elements(book_root)
    return {"ok": True, "arc": _arc_view(arc, elements)}


def _last_arc_text(arc: dict[str, Any]) -> str:
    """取情节内最新一版 l5 正文（末章已生成则用之）。"""
    state = arc.get("state") or {}
    l5 = str((state.get("levels") or {}).get("l5", {}).get("text") or "").strip()
    if l5:
        return l5
    chs = arc.get("chapters") or []
    for ch in reversed(chs):
        if str(ch.get("text") or "").strip():
            return str(ch["text"])
    return ""


def arc_select(
    book_root: str | Path,
    arc_id: str,
    selected: dict[str, list[str]] | None,
) -> dict[str, Any]:
    """为本情节选择参与元素（每情节一次）。选择变化 → 清空 l3 起下游强制重生成。"""
    book_root = Path(book_root)
    arcs = load_arcs(book_root)
    arc = _find_arc(arcs, arc_id)
    if not arc:
        return {"ok": False, "error": f"情节不存在：{arc_id}"}
    sel = {
        "characters": [str(x) for x in (selected or {}).get("characters", [])],
        "items": [str(x) for x in (selected or {}).get("items", [])],
        "settings": [str(x) for x in (selected or {}).get("settings", [])],
    }
    arc["selected"] = sel
    arc.setdefault("state", {})["constraints_block"] = _book_constraints_block(book_root)
    _reset_downstream(arc.setdefault("state", {}), "l2")
    save_arcs(book_root, arcs)
    elements = load_elements(book_root)
    return {"ok": True, "arc": _arc_view(arc, elements)}


async def arc_step(book_root: str | Path, arc_id: str) -> dict[str, Any]:
    """包 bridge.step_ladder：注入 element_block（l3 起生效）+ prev_anchor（l2/l3 衔接）。

    l3/l4/l5 前置：必须先选参与元素（未选 → 拒绝，提示先选）。
    """
    from . import bridge

    book_root = Path(book_root)
    _wb_ctx(book_root, arc=arc_id, step="arc_step")
    arcs = load_arcs(book_root)
    arc = _find_arc(arcs, arc_id)
    if not arc:
        return {"ok": False, "error": f"情节不存在：{arc_id}"}
    state = arc.setdefault("state", {})
    target = _next_target(state)

    if target in ("l3", "l4", "l5") and not _has_selection(arc):
        return {"ok": False, "error": "请先为本情节选择参与元素（角色/物品/设定至少选一项）"
                                      "——元素隔离从 l3 章核心开始生效"}
    if target is None:
        return {"ok": False, "error": "已达正文（l5），无需继续生成"}

    elements = load_elements(book_root)
    state["element_block"] = _element_block(arc, elements)
    state["constraints_block"] = _book_constraints_block(book_root)
    # 【Task 10】预计算备注+素材块，存入 state 供 bridge l5 注入
    notes = load_notes(book_root)
    fragments = load_fragments(book_root)
    scenes = (state.get("levels") or {}).get("l4", {}).get("scenes") or []
    _nb_parts, _fb_parts = [], []
    for si in range(len(scenes)):
        nb = _notes_block(arc_id, si, [], notes)
        if nb:
            _nb_parts.append(nb)
        fb = _fragments_block(si, fragments)
        if fb:
            _fb_parts.append(fb)
    state["notes_block"] = "\n".join(_nb_parts) if _nb_parts else ""
    state["fragments_block"] = "\n".join(_fb_parts) if _fb_parts else ""
    if arc.get("prev_anchor"):
        state["prev_anchor"] = ("\n".join(arc["prev_anchor"])
                                if isinstance(arc["prev_anchor"], list)
                                else str(arc["prev_anchor"]))
    else:
        state.pop("prev_anchor", None)

    res = await bridge.step_ladder(state)
    if res.get("ok"):
        arc["status"] = "active"
        l2 = str((state.get("levels") or {}).get("l2", {}).get("text") or "").strip()
        if l2:
            arc["l2"] = l2
        save_arcs(book_root, arcs)
    return res


def arc_confirm(book_root: str | Path, arc_id: str, level: str) -> dict[str, Any]:
    """确认某级内容（确认后才允许继续生成下一级）。"""
    from . import bridge

    book_root = Path(book_root)
    arcs = load_arcs(book_root)
    arc = _find_arc(arcs, arc_id)
    if not arc:
        return {"ok": False, "error": f"情节不存在：{arc_id}"}
    res = bridge.confirm_level(arc.setdefault("state", {}), level)
    if res.get("ok"):
        save_arcs(book_root, arcs)
    return res


def arc_set_active_chapter(
    book_root: str | Path,
    arc_id: str,
    idx: int,
) -> dict[str, Any]:
    """多章章纲：切换当前下钻的章（清空该章下游 l4/l5，须重新生成），并持久化。"""
    from . import bridge

    book_root = Path(book_root)
    arcs = load_arcs(book_root)
    arc = _find_arc(arcs, arc_id)
    if not arc:
        return {"ok": False, "error": f"情节不存在：{arc_id}"}
    res = bridge.set_active_chapter(arc.setdefault("state", {}), idx)
    if res.get("ok"):
        save_arcs(book_root, arcs)
    return res


async def arc_regenerate_l5(book_root: str | Path, arc_id: str) -> dict[str, Any]:
    """l5 污染修正重生成：双检当前 l5 → 命中未参与元素 → 注入污染修正块 → 重生成 l5。

    闭环「检测 → 修正 → 重生成」：prompt 白名单注入没拦住的部分，由事后检测抓住，
    再带着「严禁再次出现这些未选元素」的修正块重新生成。
    """
    from . import bridge

    book_root = Path(book_root)
    _wb_ctx(book_root, arc=arc_id, step="l5_regenerate")
    arcs = load_arcs(book_root)
    arc = _find_arc(arcs, arc_id)
    if not arc:
        return {"ok": False, "error": f"情节不存在：{arc_id}"}
    state = arc.setdefault("state", {})
    levels = state.setdefault("levels", {})
    old = str(levels.get("l5", {}).get("text") or "").strip()
    if not old:
        return {"ok": False, "error": "本情节还没有正文（l5）可重生成"}

    elements = load_elements(book_root)
    poll = await check_element_pollution_full(old, arc, elements)
    hits = poll.get("hits", [])
    if not hits:
        return {"ok": True, "to": "l5", "regenerated": False, "text": old,
                "pollution_was": [], "message": "未检出未参与元素污染，无需重生成"}

    lines = ["【污染修正】（硬性）上一次正文里出现了本情节未参与元素，本次生成严禁再次出现："]
    for h in hits:
        name = str(h.get("name") or h.get("element") or "?").strip()
        kind = str(h.get("kind") or "元素")
        ev = str(h.get("matched") or "").strip()[:80]
        src = "LLM复核" if h.get("src") == "llm" else "确定性"
        lines.append(f"- 禁出{kind}「{name}」（依据：{ev}；检出：{src}）")
    lines.append("涉及这些元素的情节要么省略、要么改写为不点名不登场（只写本情节参与元素）。")
    state["pollution_feedback"] = "\n".join(lines)
    # 【Task 10】预计算备注+素材块，存入 state 供 bridge l5 注入
    notes = load_notes(book_root)
    fragments = load_fragments(book_root)
    scenes = (state.get("levels") or {}).get("l4", {}).get("scenes") or []
    _nb_parts, _fb_parts = [], []
    for si in range(len(scenes)):
        nb = _notes_block(arc_id, si, [], notes)
        if nb:
            _nb_parts.append(nb)
        fb = _fragments_block(si, fragments)
        if fb:
            _fb_parts.append(fb)
    state["notes_block"] = "\n".join(_nb_parts) if _nb_parts else ""
    state["fragments_block"] = "\n".join(_fb_parts) if _fb_parts else ""
    # 清空 l5（l4 保持已确认）→ 带修正块重新生成
    levels["l5"] = {"text": "", "confirmed": False, "prompt": ""}
    res: dict[str, Any] = {"ok": False, "error": "l5 重生成失败"}
    try:
        res = await bridge.step_ladder(state)
    finally:
        # 无论成败都清除，避免残留到下次生成
        state.pop("pollution_feedback", None)
        if res.get("ok"):
            save_arcs(book_root, arcs)
    if res.get("ok"):
        return {**res, "regenerated": True, "pollution_was": hits}
    return {**res, "regenerated": False, "pollution_was": hits}


def arc_set_level(
    book_root: str | Path,
    arc_id: str,
    level: str,
    text: str = "",
    *,
    data: Any = None,
    idx: int | None = None,
) -> dict[str, Any]:
    """直接编辑任一级（l1/l2/l3/l4/l5）——三栏平行工作台的「都可编辑」。

    - l1/l2/l5：text 覆写文本；l1/l2 清下游 + 清 key_facts；l5 替换正文。
    - l3：data = {title, core, beats}（单章）或 {chapters:[...]}（整组章纲）；
      idx 指定多章模式下编辑第几章。
    - l4：data = scenes 列表（整体替换）。
    """
    from . import bridge

    book_root = Path(book_root)
    arcs = load_arcs(book_root)
    arc = _find_arc(arcs, arc_id)
    if not arc:
        return {"ok": False, "error": f"情节不存在：{arc_id}"}
    if level not in ("l1", "l2", "l3", "l4", "l5"):
        return {"ok": False, "error": f"无效级别：{level}"}
    state = arc.setdefault("state", {})
    levels = state.setdefault("levels", {})
    text = str(text or "").strip()

    if level in ("l1", "l2", "l5"):
        if level == "l5":
            levels["l5"] = {"text": text, "confirmed": False,
                            "prompt": (levels.get("l5") or {}).get("prompt", "手动编辑")}
        else:
            levels[level] = {"text": text, "confirmed": False, "prompt": "手动编辑"}
            _reset_downstream(state, level)
            state.pop("key_facts", None)
            # 同步顶层快捷字段（阶梯显示优先 arc.l1/arc.l2）
            if level == "l1":
                arc["l1"] = text
            elif level == "l2":
                arc["l2"] = text

    elif level == "l3":
        if not isinstance(data, dict) or not data:
            return {"ok": False, "error": "l3 编辑需要 data={title, core, beats}"}
        l3 = levels.setdefault("l3", {})
        if isinstance(data.get("chapters"), list):
            l3["data"] = data                      # 整组章纲替换
        elif isinstance(l3.get("data") or {}, dict) \
                and isinstance((l3.get("data") or {}).get("chapters"), list):
            chs = (l3.get("data") or {}).get("chapters", [])
            i = max(0, min(int(idx if idx is not None
                               else state.get("active_chapter") or 0), len(chs) - 1))
            chs[i] = data                                     # 替换多章中的一章
            l3["data"] = {"chapters": chs}
        else:
            l3["data"] = data                       # 单章整体替换
        l3["text"] = bridge._render_l3(l3["data"])
        l3["confirmed"] = False
        l3["prompt"] = "手动编辑"
        _reset_downstream(state, "l3")

    elif level == "l4":
        if not isinstance(data, list):
            return {"ok": False, "error": "l4 编辑需要 data=场景列表（空列表=清空）"}
        l4 = levels.setdefault("l4", {})
        l4["scenes"] = data
        l4["text"] = bridge._render_l4(data)
        l4["confirmed"] = False
        l4["prompt"] = "手动编辑"
        _reset_downstream(state, "l4")

    # 同步顶层 l1/l2（阶梯显示优先 arc.l1/arc.l2；编辑/清空/清下游都要反映到顶层）
    arc["l1"] = (levels.get("l1") or {}).get("text", "")
    arc["l2"] = (levels.get("l2") or {}).get("text", "")
    save_arcs(book_root, arcs)
    return {"ok": True, "level": level, "text": text, "state": state}


def archive_arc_elements(book_root: str | Path, arc_id: str) -> int:
    """情节完结时归档局部元素：scope arc:<id> → archived:<id>。"""
    elements = load_elements(book_root)
    n = 0
    for kind in ("characters", "items", "settings"):
        for e in elements.get(kind, []):
            if e.get("scope") == f"arc:{arc_id}":
                e["scope"] = f"archived:{arc_id}"
                n += 1
    if n:
        save_elements(book_root, elements)
    return n


def arc_finish(book_root: str | Path, arc_id: str) -> dict[str, Any]:
    """标记情节为已完成（done）——下一情节 carry_prev 时以它作前文锚点来源。"""
    book_root = Path(book_root)
    arcs = load_arcs(book_root)
    arc = _find_arc(arcs, arc_id)
    if not arc:
        return {"ok": False, "error": f"情节不存在：{arc_id}"}
    arc["status"] = "done"
    save_arcs(book_root, arcs)
    archived = archive_arc_elements(book_root, arc_id)
    return {"ok": True, "status": "done", "archived_elements": archived}


async def arc_modify(
    book_root: str | Path,
    arc_id: str,
    level: str,
    instruction: str,
) -> dict[str, Any]:
    """对话修改某级内容（保持格式），清空下游级。"""
    from . import bridge

    book_root = Path(book_root)
    _wb_ctx(book_root, arc=arc_id, step="arc_modify")
    arcs = load_arcs(book_root)
    arc = _find_arc(arcs, arc_id)
    if not arc:
        return {"ok": False, "error": f"情节不存在：{arc_id}"}
    state = arc.setdefault("state", {})
    elements = load_elements(book_root)
    state["element_block"] = _element_block(arc, elements)
    state["constraints_block"] = _book_constraints_block(book_root)
    if arc.get("prev_anchor"):
        state["prev_anchor"] = ("\n".join(arc["prev_anchor"])
                                if isinstance(arc["prev_anchor"], list)
                                else str(arc["prev_anchor"]))
    res = await bridge.modify_level(state, level, instruction)
    if res.get("ok"):
        # 同步顶层快捷字段（阶梯显示优先 arc.l1/arc.l2，否则改完看不到；下游被清也要反映）
        lv = state.setdefault("levels", {})
        arc["l1"] = (lv.get("l1") or {}).get("text", "")
        arc["l2"] = (lv.get("l2") or {}).get("text", "")
        save_arcs(book_root, arcs)
    return res


def _strip_discuss_block(msg: str) -> str:
    """从注入消息里取用户真实指令（剥前端「【讨论对象：…】」块，避免污染预检索 query）。

    格式：`【讨论对象：X】\n<内容>\n\n用户指令：<用户话>` → 返回 `<用户话>`；
    无「用户指令：」标记时剥掉开头的【讨论对象：…】整块。
    """
    txt = str(msg or "").strip()
    m = re.search(r"用户指令：(.+)$", txt, re.S)
    if m:
        return m.group(1).strip()
    return re.sub(r"^【讨论对象：.*?】\s*\n.*?(?=\n\n用户指令|$)", "", txt, flags=re.S).strip()


def _corpus_sel_keep(r: dict[str, Any], selected: list[str]) -> bool:
    """语料结果按选中的语料书过滤（id 形如 corpus:玄幻武侠/示例书(1-500章).txt#0）。"""
    rid = str(r.get("id") or "")
    if not rid.startswith("corpus:"):
        return True  # 非语料结果不拦
    return any(v in rid for v in selected)


def _make_reply_stream_extractor():
    """增量提取流式 JSON 中 ``"reply"`` 键的字符串值（约定 reply 是第一个字段）。

    返回 feed(buffer)->new_text：每次把累计缓冲喂进去，返回**新增**的可展示文本。
    处理转义（\\n \\" \\\\ \\uXXXX）；尾部不完整的转义序列等下一片数据再解码，
    保证已吐出的文本是最终文本的稳定前缀。字符串闭合后不再产出。
    """
    st = {"sent": 0, "done": False}
    _ESC = {"n": "\n", "t": "\t", "r": "\r", '"': '"', "\\": "\\", "/": "/", "b": "\b", "f": "\f"}

    def feed(buf: str) -> str:
        if st["done"]:
            return ""
        idx = buf.find('"reply"')
        if idx < 0:
            return ""
        rest = buf[idx + len('"reply"'):]
        j = 0
        while j < len(rest) and rest[j] in " \t\r\n:":
            j += 1
        if j >= len(rest) or rest[j] != '"':
            return ""
        k = j + 1
        out: list[str] = []
        n_total = len(rest)
        while k < n_total:
            c = rest[k]
            if c == "\\":
                if k + 1 >= n_total:
                    break  # 孤立反斜杠：数据未到齐
                nxt = rest[k + 1]
                if nxt == "u":
                    if k + 6 > n_total:
                        break  # \uXXXX 未到齐
                    try:
                        out.append(chr(int(rest[k + 2:k + 6], 16)))
                    except ValueError:
                        out.append(rest[k:k + 6])
                    k += 6
                    continue
                out.append(_ESC.get(nxt, nxt))
                k += 2
                continue
            if c == '"':
                st["done"] = True
                break
            out.append(c)
            k += 1
        text = "".join(out)
        new = text[st["sent"]:] if len(text) > st["sent"] else ""
        st["sent"] = len(text)
        return new

    return feed


async def _stream_chat_json(
    *,
    system: str,
    user: str,
    call_type: str,
    temperature: float,
    max_tokens: int,
    on_token,
) -> dict[str, Any]:
    """chat_json 的流式变体（【2026-08-23 VS Code 扩展】）。

    reply 字段边生成边经 ``on_token(delta)`` 增量回调；最终解析与非流式
    chat_json 同路径（去 fence → json.loads），返回同形 dict。
    """
    from .llm_client import chat_completion_stream

    feed = _make_reply_stream_extractor()
    buf = {"text": ""}

    async def _cb(delta: str) -> None:
        buf["text"] += delta
        new = feed(buf["text"])
        if new:
            await on_token(new)

    r = await chat_completion_stream(
        system=system, user=user, on_delta=_cb, call_type=call_type,
        temperature=temperature, max_tokens=max_tokens, json_mode=True,
    )
    raw = (r.get("content") or "").strip()
    usage = r.get("usage")
    if r.get("error"):
        return {"data": None, "raw": raw, "usage": usage, "error": str(r["error"])}
    if raw.startswith("```"):
        lines = raw.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        raw = "\n".join(lines).strip()
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        return {"data": None, "raw": raw, "usage": usage, "error": f"JSON decode failed: {exc}"}
    return {"data": data, "raw": raw, "usage": usage, "error": None}


async def arc_chat(
    book_root: str | Path,
    arc_id: str,
    messages: list[dict[str, Any]],
    *,
    web_search: bool = False,
    access: dict[str, Any] | None = None,
    sel_access: dict[str, Any] | None = None,
    mode: str = "normal",
    on_event: Any = None,
) -> dict[str, Any]:
    """Codex 式交互窗口：讨论 + 可执行动作（工具循环）。

    on_event（【2026-08-23 VS Code 扩展流式】）：async fn(etype:str, data:dict)。
      传入后每轮模型的 reply 文本边生成边推（"token"），只读工具执行完即推
      （"tool_call"），内容工具提案推（"pending_proposal"）；返回值与非流式
      完全一致，REST 路径（on_event=None）行为不变，作回归对照。

    mode（【2026-08-15 初始化模式】）：
      "normal" — 完整创作助手（五级阶梯工具全开）
      "init"   — 初始化模式（新建书/未初始化书）：复用同一工具循环/记忆/检索骨架，
                 只换初始化 sys_p 并裁剪 l2-l5 阶梯工具（step/modify/finalize/score 等），
                 保留 search/new_arc/create_arc/fill_settings/generate/capacity/enrich_l1/记忆/元素。

    web_search=True：开启本对话自动联网搜索（AnySearch 模式）——助手需要外部/考据
    信息时自发调用 search（web 优先），不依赖用户手动检索。

    access（访问控制）：dict {book, memory, corpus, web} —— 控制注入多少上下文。
      book=False：不注入本书阶梯/元素/基本设定（仅对话历史）；
      memory=False：不召回记忆；
      corpus/False：不预检索语料/模板；
      web：联网（AnySearch 自发）。缺省全开（web 关）。

    sel_access（条目级选择性注入）：dict {settings, arcs, elements, memory, corpus, templates} ——
      只在 access 允许的范围内进一步收窄，只把选中的条目给助手看：
      arcs 选中 → 仅选中的弧注入阶梯；settings → 设定文档/元素设定；elements → 元素清单；
      memory → 仅召回选中的记忆；corpus/templates 勾选 → 预检索收窄到语料书/模板并直接注入选中模板。

    工具：
      search / score / pollution        —— 只读（检索/评分/污染检测）
      modify_level / step / regenerate_l5 —— 有副作用（改写某级/生成下一级/重生成正文）
      finalize                            —— 落盘，返回 needs_confirm，前端确认后才执行
    返回 {ok, reply, tool_events, changed, pending}；前端据此刷新工作台 / 弹确认。
    """
    from . import bridge
    from .llm_client import chat_json

    book_root = Path(book_root)
    _wb_ctx(book_root, arc=arc_id, step="arc_chat")
    arcs = load_arcs(book_root)
    arc = _find_arc(arcs, arc_id) if arc_id else None
    elements = load_elements(book_root)
    settings = load_basic_settings(book_root)

    # 访问控制：book/memory/corpus/web 控制注入多少上下文（缺省全开，web 关）
    acc = {"book": True, "memory": True, "corpus": True, "web": bool(web_search)}
    if isinstance(access, dict):
        acc.update({k: bool(v) for k, v in access.items() if k in acc})
    acc["web"] = bool(acc.get("web") or web_search)

    # 条目级选择性注入：只把选中的条目给助手看（在 access 允许范围内进一步收窄）
    sel = {"settings": [], "arcs": [], "elements": [], "memory": [], "corpus": [], "templates": []}
    if isinstance(sel_access, dict):
        for _k in sel:
            _v = sel_access.get(_k)
            if isinstance(_v, (list, tuple)):
                sel[_k] = [str(x) for x in _v]

    # 参与元素可读名单
    name_of: dict[str, str] = {}
    for kind in ("characters", "items", "settings", "maps"):
        for e in elements.get(kind, []):
            if e.get("id"):
                name_of[e["id"]] = str(e.get("name") or "")

    def _ctx_blocks(a: dict[str, Any] | None) -> tuple[str, str]:
        """构建上下文（阶梯/参与元素）；无弧时用书级上下文（书设定+元素清单）。

        acc.book=False 时不注入本书内容（仅对话历史）。
        sel_access：只注入选中的弧/设定/元素（在 acc 允许范围内）。
        """
        if not acc["book"]:
            return "（未注入本书上下文——已关闭「本书」访问，仅对话历史与你的指令）", "（未注入参与元素）"
        if a is not None and sel["arcs"] and str(a.get("id")) not in sel["arcs"]:
            return "（该情节不在「选择性注入」选中列表，未注入其阶梯内容——仅对话历史与你的指令）", "（未注入参与元素）"
        if a is None:
            s = settings or {}
            # 元素清单：勾选了元素/设定时只显示选中的（settings 名命中「设定」元素；elements 名命中任意元素）
            def _visible(name: str, kind: str) -> bool:
                if not sel["elements"] and not sel["settings"]:
                    return True
                if name in sel["elements"]:
                    return True
                return kind == "settings" and name in sel["settings"]

            elem_lines: list[str] = []
            for label, kind in (("角色", "characters"), ("物品", "items"), ("设定", "settings")):
                lst = [f"{e.get('name')}：{str(e.get('desc') or '')[:40]}"
                       for e in elements.get(kind, []) if e.get("name") and _visible(str(e.get("name")), kind)]
                if lst:
                    elem_lines.append(f"{label}：\n" + "\n".join(f"- {x}" for x in lst[:12]))
            ctx = (
                f"【书】{s.get('name') or ''}（{s.get('genre') or ''}）\n"
                f"【一句话简介】{s.get('one_liner') or ''}\n"
                f"【文风基调】{s.get('style') or ''}\n"
                f"【主角与世界观核心】{s.get('role_setting') or ''}\n"
                f"【元素清单】\n" + ("\n".join(elem_lines) or "（暂无元素）") + "\n"
                f"【现状】还没有任何情节——第一步是规划 l1 并用 new_arc 创建情节。"
            )
            # 选中的设定文档全文注入
            if sel["settings"]:
                sdir = Path(book_root) / "设定集"
                sdoc = []
                if sdir.is_dir():
                    for f in sorted(sdir.glob("*.md")):
                        if f.stem in sel["settings"]:
                            try:
                                sdoc.append(f"【设定·{f.stem}】\n{f.read_text(encoding='utf-8')[:1500]}")
                            except Exception:  # noqa: BLE001
                                continue
                if sdoc:
                    ctx += "\n\n" + "\n\n".join(sdoc)
            return ctx, "（还没有情节）"
        st = a.setdefault("state", {})
        sels = a.get("selected") or {}
        sb = "；".join(
            f"{label}：{('、'.join(str(name_of.get(i, i)) for i in sels.get(k, []))) or '（未选）'}"
            for label, k in (("角色", "characters"), ("物品", "items"), ("设定", "settings"))
        )
        return bridge._state_context(st), sb
    FORCE_TAG = "【联网检索】"
    hist_lines = []
    for m in (messages or [])[-12:]:
        role = "用户" if m.get("role") == "user" else "助手"
        txt = str(m.get('content') or '').strip()
        if m.get("role") == "user" and txt.startswith(FORCE_TAG):
            txt = txt[len(FORCE_TAG):].strip()
        hist_lines.append(f"{role}：{txt}")
    hist = "\n".join(hist_lines) if hist_lines else "（开始）"

    # search 工具可用性（受访问控制约束）：web→全源；仅 corpus→不含 web；仅 book→只查本书；全关→不提供 search 工具
    model_search_srcs: list[str] | None = None
    if acc["web"]:
        model_search_srcs = None
        _search_tool_line = '  search {"query":"..."} — 六源检索（本书/语料/模板/参考资料/web/CSV）。需要上下文就先检索。\n'
    elif acc["corpus"]:
        model_search_srcs = ["book", "corpus", "csv", "refmd", "template"]
        _search_tool_line = '  search {"query":"..."} — 受限检索（本书/语料/模板/参考资料，不含外部）。需要上下文就先检索。\n'
    elif acc["book"]:
        model_search_srcs = ["book"]
        _search_tool_line = '  search {"query":"..."} — 只查本书内容。需要上下文就先检索。\n'
    else:
        model_search_srcs = []
        _search_tool_line = ""
    # 勾选了语料/模板 → 模型自调 search 也只搜勾选的源
    if sel["corpus"] or sel["templates"]:
        cur = list(model_search_srcs) if model_search_srcs else ["book", "corpus", "csv", "refmd", "template", "web"]
        nxt = [s for s in cur if s not in ("corpus", "template")]
        if sel["corpus"]:
            nxt.append("corpus")
        if sel["templates"]:
            nxt.append("template")
        model_search_srcs = nxt or None

    # 【2026-08-15 初始化模式】init 模式换初始化角色描述 + 裁剪 l2-l5 阶梯工具
    if mode == "init":
        _sys_head = (
            "你是「新书初始化创作助手」，正在帮作者从零搭建一本书。"
            "当前书还没有基本设定/情节弧，你的职责是引导作者用自然语言描述故事，"
            "逐步提炼成结构化基本设定，生成设定集，并规划开篇情节弧。\n"
            "【记忆系统】作者可能让你记住某些偏好。"
            "记住的内容会在【相关记忆】里出现，回答和生成时要遵守。\n"
            "工具（一次一个，不要一次调多个）：\n"
            "—— 只读类 ——\n"
            + _search_tool_line
            + "  capacity {} — 估算当前故事核/l1 能支撑多少章/字（只读）。\n"
            "  list_memory {\"scope\":\"book\"} — 列出记忆。\n"
            "  list_arcs {} — 列出所有情节弧的摘要。\n"
            "  lookup_element {\"name\":\"...\"} — 查元素详情（角色/物品/设定）。\n"
            "—— 初始化类（有副作用，改内容）——\n"
            "  fill_settings {\"settings\":{...}} — 把对话内容提炼成基本设定字段并保存。"
            "settings 键：name(书名)/genre(类型)/one_liner(一句话故事核)/style(文风)/"
            "protagonist{name,desire,flaw}/cast[{name,role,relation}]/time_setting(时代背景)/"
            "power_system{type,name,rules}/locations[{name,desc}]/conflict(核心冲突)/"
            "goal(主角目标)/twist(转折)/ending(结局)/items[{name,desc}]/anti_trope(反套路)/"
            "opening_hook(开篇钩子)/hard_constraints[]。\n"
            "  generate {} — 按已填基本设定生成 设定集/*.md + elements.json。\n"
            "  create_arc {\"l1\":\"一句话极简剧情\"} — 建开篇情节弧（自动容量诊断，密度不足自动补齐）。\n"
            "  new_arc {\"l1\":\"一句话极简剧情\",\"n_chapters\":3} — 新建情节（书级对话用）。\n"
            "  enrich_l1 {\"max_chars\":160} — 按三维诊断补齐当前 l1（加密度/内容/质量锚点；需确认）。\n"
            "—— 元素/记忆类（有副作用）——\n"
        )
    else:
        _sys_head = (
            "你是创作助手，正协助用户用五级阶梯方式创作小说："
            "l1 极简 → l2 情节概要 → l3 章核心 → l4 场景分解 → l5 正文。\n"
            "职责：评点、答疑、给修改建议；需要时调用工具。用户说的「改一下/润色/加一点」默认指当前级，自动判断。\n"
            "【记忆系统】用户可能让你记住某些偏好。"
            "记住的内容会在【相关记忆】里出现，回答和生成时要遵守。不要编造记忆里没有的偏好。\n"
            "工具（一次一个，不要一次调多个）：\n"
            "—— 只读类 ——\n"
            + _search_tool_line
            + "  score {} — 对当前 l5 正文双评分（意图兑现+纯质量+综合）。\n"
            "  pollution {} — 元素污染检测（未参与元素是否出现在 l5 里）。\n"
            "  lookup_element {\"name\":\"...\"} — 查元素详情（角色/物品/设定）。\n"
            "  list_memory {\"scope\":\"book\"} — 列出记忆，scope 可选 book/arc/element。\n"
            "  list_arcs {} — 列出所有情节弧的摘要。\n"
            "  capacity {} — 估算当前 l1 能支撑多少章/字的高质量正文（只读）。\n"
            "—— 创作类（有副作用，改内容）——\n"
            "  step {} — 生成下一级阶梯内容。\n"
            "  new_arc {\"l1\":\"一句话极简剧情\",\"n_chapters\":3} — 新建一个情节（书级对话时用；创建后继续 step/confirm_level 推到下一级）。\n"
            "  modify_level {\"level\":\"l2\",\"instruction\":\"...\"} — 按意见改写某级并清空下游。\n"
            "  set_level {\"level\":\"l5\",\"text\":\"...\",\"chapter_idx\":null} — 直接覆写某级内容（l3 传 data 字段）。少用。\n"
            "  confirm_level {\"level\":\"l2\"} — 确认某级阶梯。\n"
            "  regenerate_l5 {} — 检出污染后重生成 l5 正文。\n"
            "  ai_flavor_polish {} — 对 l5 正文做去 AI 味打磨。\n"
            "  set_active_chapter {\"idx\":0} — 切换到第 idx 章（从 0 开始）。\n"
            "  enrich_l1 {\"max_chars\":160} — 按三维诊断补齐当前 l1（加密度/内容/质量锚点；需确认）。\n"
            "—— 元素类（有副作用）——\n"
        )

    sys_p = (
        _sys_head
        + "  add_element {\"kind\":\"characters\",\"name\":\"...\",\"desc\":\"...\"} — 新建元素。kind: characters/items/settings。\n"
        "    ⚠ 添加前先用 lookup_element 查一下是否已存在（同名/别名命中即已存在），已存在就不要 add。\n"
        "  update_element {\"id\":\"...\",\"name\":\"...\",\"kind\":\"...\",\"field\":\"desc\",\"value\":\"...\"} — 修改元素某字段。id 或 name+kind 二选一。\n"
        "  select_for_arc {\"element_id\":\"...\",\"name\":\"...\",\"kind\":\"characters\"} — 把元素加入本弧白名单。id 或 name 二选一。\n"
        "  add_relation {\"from\":\"...\",\"to\":\"...\",\"type\":\"对手\",\"desc\":\"...\"} — 加元素关系。from/to 填名字或 id 均可。\n"
        "—— 记忆类 ——\n"
        "  remember {\"key\":\"对白要短\",\"content\":\"...\",\"scope\":\"book\"} — 写入一条记忆。scope=book 全书/arc 本弧。\n"
        "  forget {\"key\":\"...\"} — 删除一条记忆（按 key 或 id）。\n"
        "—— 落盘类（需用户确认）——\n"
        "  finalize {} — 保存本章到书（落盘）。需用户确认，不要擅自执行。\n"
        "  finish_arc {} — 标记本情节已完成。需用户确认。\n"
        "回复必须是 JSON：{\"reply\":\"给用户看的话（中文）\",\"action\":{\"tool\":\"...\",\"args\":{...}} 或 null}。\n"
        "reply 必填。需要工具就带 action，否则 action 为 null。\n"
        "【行动原则】用户**明确要求**改写/生成/新建时，直接调用对应工具执行（改写→modify_level、推进→step、新建→new_arc），"
        "不要只给方案、不要反复问「是否采纳/确认后再生成」——除非用户没给明确指令（如「你觉得怎么改好？」）才先给建议。"
        "执行后把新内容引用在 reply 里展示，让用户直接看到结果。\n"
        "【勿重复】一次只执行一个必要的修改，改完展示结果即停，**不要重复执行同一个工具**（除非用户给了新指令）。\n"
        "【检索噪音】预检索结果若与请求无关（字义/词典/广告等噪音），忽略它们，直接凭创作能力回答，并说明「检索未提供有效参考」。\n"
        "【建弧后推进】new_arc 创建情节后，继续用 step 生成下一级、confirm_level 确认后接下一级，一路推进到 l5 正文；每级在 reply 里简述，让用户可确认/打断。"
    )
    if mode == "init":
        # init 模式的行动原则补充：引导而非直接生成
        sys_p += (
            "\n【初始化引导】一次只推进一到两个决策项。作者描述模糊时先给 1-2 个书名/方向建议让作者选。"
            "fill_settings 前展示提炼字段让作者确认；generate 前确认基本设定；create_arc 建弧后可让作者继续聊下一弧或结束初始化。"
        )
    if web_search:
        sys_p += (
            "\n【自动联网搜索已开启（AnySearch 模式）】"
            "当你需要外部/背景/考据信息（历史、地理、器物、风物、名物等）时，**主动调用 search 工具联网检索**（web 源优先），"
            "不要等用户明确要求；检索后基于真实结果回答或创作，并标注来源。"
            "若当前内容不依赖外部信息则不必检索。"
        )

    # 预检索：用户消息像"求帮助/问"，带「【联网检索】」标记强制联网，或 web_search 开启（AnySearch 自发）
    # （不依赖模型主动调工具，防幻觉）
    last_user = next((str(m.get("content") or "") for m in reversed(messages or []) if m.get("role") == "user"), "")
    forced_web = last_user.startswith(FORCE_TAG)
    # 预检索 query 取「用户真实指令」：剥掉【讨论对象：…】注入块，避免搜索词带上前缀噪音（如「讨」字义）
    user_query = _strip_discuss_block(last_user)
    query_for_search = user_query[len(FORCE_TAG):].strip() if user_query.startswith(FORCE_TAG) else user_query
    search_block = ""
    # web 开 → 自发联网（任意消息预检索）；仅 corpus → 提问类消息预检索语料/模板；都关 → 不预检索（除非强制标记）
    want_search = forced_web or acc["web"] or (acc["corpus"] and _should_proactive_search(query_for_search))
    tpl_block = ""
    # 勾选了模板 → 强制触发预检索（模板源收窄）；勾选语料书 → 语料源收窄；纯勾选短剧库 → 由下方 sd_block 直接注入，不预检索（避免剧名列表噪音）
    if sel["templates"] or any("短剧" not in v for v in sel["corpus"]):
        want_search = True
    if want_search:
        from . import writing_search
        if acc["web"]:
            srcs = ["web", "book", "corpus", "template"]
        elif acc["corpus"] or forced_web:
            srcs = ["book", "corpus", "template"]
        else:
            srcs = None
        if sel["corpus"] or sel["templates"]:
            cur = list(srcs) if srcs else ["book", "corpus", "template", "web"]
            nxt = [s for s in cur if s not in ("corpus", "template")]
            if sel["corpus"]:
                nxt.append("corpus")
            if sel["templates"]:
                nxt.append("template")
            srcs = nxt or None
        sres = await writing_search.search_around(query_for_search, book_root, sources=srcs)
        if sel["corpus"]:
            sres["results"] = [r for r in sres.get("results", []) if _corpus_sel_keep(r, sel["corpus"])]
        if sres.get("results"):
            fmt = writing_search.format_results(sres["results"])
            search_block = "\n\n[联网检索结果（后端已检索，回答必须基于以下真实内容）]\n" + fmt
            tag = "🌐 强制联网检索" if forced_web else ("🌐 自发联网检索" if acc["web"] else "🔍 预检索")
            tool_events: list[dict[str, Any]] = [{
                "tool": "search",
                "summary": f"{tag}「{query_for_search[:30]}」→ {len(sres['results'])} 命中（{sres.get('intent')}）",
                "detail": fmt, "proactive": True,
            }]
        else:
            tool_events = []
    else:
        tool_events = []
    # 勾选了红果短剧库 → 直接注入红果热播剧名 + 用户录入剧情（不依赖语料 BM25，100% 可参考）
    sd_block = ""
    if any("短剧" in v for v in sel["corpus"]):
        try:
            _lib = list_short_dramas()
            _parts = []
            if _lib.get("user"):
                _parts.append("【红果短剧·用户录入剧情】\n" + "\n".join(
                    f"- 《{u.get('name')}》{('（' + '、'.join(u.get('tags') or []) + '）') if u.get('tags') else ''}：{u.get('intro', '')}"
                    for u in _lib["user"]))
            _auto = (_lib.get("auto") or [])[:40]
            if _auto:
                # 按套路标签分组：先婚后爱（嫁给傻子王爷后/穷鬼真千金…）
                _groups: dict[str, list[str]] = {}
                for a in _auto:
                    _tag = (a.get("tags") or ["热播"])[0]
                    _groups.setdefault(_tag, []).append(str(a.get("name")))
                _gp_lines = [f"- {_t}：{'、'.join(_v[:4])}" for _t, _v in _groups.items()]
                _parts.append("【红果短剧·当前热播（按套路分组）】\n" + "\n".join(_gp_lines))
            if _parts:
                sd_block = "\n\n[红果短剧参考（勾选了语料·模板中的短剧库，以下为可直接参考的红果热播短剧）]\n" + "\n".join(_parts)
        except Exception:  # noqa: BLE001
            sd_block = ""
    # 选中的情节模板直接注入（不走搜索，作为创作结构参考）
    if sel["templates"]:
        try:
            from .plot_library import get_plot_template_library
            _lib = get_plot_template_library()
            parts = []
            for _tid in sel["templates"]:
                t = _lib.get(_tid)
                if t:
                    parts.append(f"【情节模板·{t.get('name') or _tid}】\n{_lib.match_text(t)[:1200]}")
            if parts:
                tpl_block = "\n\n[选定的情节模板（创作时参考其结构）]\n" + "\n\n".join(parts)
        except Exception:  # noqa: BLE001
            tpl_block = ""

    changed = False
    pending: list[dict[str, Any]] = []
    reply = ""
    new_arc_id = None

    # 预召回记忆（对用户消息做关键词匹配，注入上下文）；acc.memory=False 不注入
    mem_block = ""
    if acc["memory"]:
        recalled = recall_memory(book_root, last_user, arc_id=arc_id, top_k=6)
        if sel["memory"]:
            recalled = [m for m in recalled
                        if str(m.get("id")) in sel["memory"] or str(m.get("key")) in sel["memory"]]
        if recalled:
            mem_lines = []
            for m in recalled:
                sc = _mem_scope(m)
                label = "全书" if sc == "book" else ("本弧" if sc.startswith("arc:") else "元素")
                key = m.get("key") or ""
                head = f"[{label}] {key}" if key else f"[{label}]"
                mem_lines.append(f"- {head}：{m.get('text', '')}")
            mem_block = "\n\n【相关记忆（回答和生成时遵守）】\n" + "\n".join(mem_lines)

    for _round in range(3):
        events_note = ""
        if tool_events:
            events_note = "\n[已执行工具]\n" + "\n".join(f"- {e.get('summary')}" for e in tool_events)
        ctx, sel_block = _ctx_blocks(arc)
        usr = (
            f"【当前阶梯各级内容】\n{ctx or '（阶梯还没有内容）'}\n"
            f"【本情节参与元素】\n{sel_block}\n"
            f"【对话历史】\n{hist}\n{events_note}{mem_block}{search_block}{sd_block}{tpl_block}"
        )
        if on_event is not None:
            # 【VS Code 扩展】流式：reply 边生成边推 token
            r = await _stream_chat_json(
                system=sys_p, user=usr, call_type="arc_chat_tool",
                temperature=0.7, max_tokens=1200,
                on_token=lambda s: on_event("token", {"text": s}))
        else:
            r = await chat_json(system=sys_p, user=usr, call_type="arc_chat_tool", temperature=0.7, max_tokens=1200)
        data = r.get("data")
        if not isinstance(data, dict) or not data:
            raw = (r.get("raw") or "").strip()
            reply = raw if raw else "（对话模型返回异常，请重试）"
            break
        reply = str(data.get("reply") or "").strip()
        action = data.get("action")
        if not isinstance(action, dict) or not action.get("tool"):
            break
        ev, needs_confirm = await _execute_chat_tool(action.get("tool"), action.get("args") or {}, book_root, arc, elements, dry_run=True, search_srcs=model_search_srcs, mode=mode)
        if needs_confirm:
            if ev:
                pending.append(ev)
                if on_event is not None:
                    await on_event("pending_proposal", {"proposal": ev})
            break
        if not ev:
            break
        tool_events.append(ev)
        if on_event is not None and not ev.get("failed"):
            await on_event("tool_call", {"event": ev})
        # 有副作用的工具 → changed=True，前端会刷新
        side_effect_tools = {
            "modify_level", "step", "regenerate_l5",
            "set_level", "confirm_level", "ai_flavor_polish", "set_active_chapter",
            "add_element", "update_element", "select_for_arc", "add_relation",
            "remember", "forget",
            "enrich_l1",  # 【2026-08-15】容量补齐 l1
            # 【2026-08-15 初始化模式】
            "fill_settings", "generate", "create_arc",
        }
        if action.get("tool") in side_effect_tools and not ev.get("failed"):
            changed = True
        # 【书级 new_arc】建弧后切到新弧上下文，后续轮次可继续 step/confirm 推进
        if (action.get("tool") == "new_arc") and (ev or {}).get("new_arc_id"):
            new_arc_id = ev["new_arc_id"]
            changed = True
            arcs = load_arcs(book_root)
            arc = _find_arc(arcs, new_arc_id) or arc
        elif ev.get("arc_changed") or ev.get("elem_changed"):
            changed = True
            # 副作用后重载弧状态+元素，让后续轮次读到最新数据
            arcs = load_arcs(book_root)
            reloaded = _find_arc(arcs, arc_id)
            if reloaded:
                arc = reloaded
            if ev.get("elem_changed"):
                elements = load_elements(book_root)
                # 重建参与元素名映射
                name_of.clear()
                for kind in ("characters", "items", "settings", "maps"):
                    for e in elements.get(kind, []):
                        if e.get("id"):
                            name_of[e["id"]] = e.get("name", "")
        # 只读工具结果已并入 tool_events，下一轮 usr 会带上

    return {
        "ok": True,
        "reply": reply,
        "tool_events": tool_events,
        "changed": changed,
        "pending": pending,
        "new_arc_id": new_arc_id,
    }


_RETRIEVAL_HINT = (
    "检索", "搜索", "查", "找", "参考", "资料", "桥段", "套路", "怎么写", "怎么", "如何",
    "为什么", "思路", "灵感", "建议", "帮我", "评点", "分析", "看看", "？", "?",
)

# 【2026-08-15 初始化模式】l2-l5 阶梯工具（init 模式禁用）
_LADDER_TOOLS = {
    "step", "modify_level", "set_level", "confirm_level", "regenerate_l5",
    "ai_flavor_polish", "set_active_chapter", "finalize", "finish_arc",
    "score", "pollution",
}

# 修改内容/状态的工具：dry_run 时只提案、需用户同意后才执行（只读工具不在此列）
_CONTENT_TOOLS = {
    "modify_level", "set_level", "step", "confirm_level", "regenerate_l5", "ai_flavor_polish",
    "finalize", "finish_arc", "new_arc", "set_active_chapter",
    "add_element", "update_element", "select_for_arc", "add_relation",
    "remember", "forget",
    "enrich_l1",  # 【2026-08-15】容量补齐 l1（有副作用，需确认）
    # 【2026-08-15 初始化模式】
    "fill_settings", "generate", "create_arc",
}


def _tool_proposal_summary(tool: str, args: dict[str, Any], arc: dict[str, Any] | None) -> str:
    """内容工具 dry_run 时的提案摘要（前端确认框显示）。"""
    n = (arc or {}).get("name") or ""
    if tool == "modify_level":
        return f"✏ 改写 {args.get('level')}：{str(args.get('instruction') or '')[:30]}"
    if tool == "set_level":
        return f"✍ 覆写 {args.get('level')}"
    if tool == "step":
        return f"➡ 生成下一级阶梯内容（{n}）"
    if tool == "confirm_level":
        return f"✓ 确认 {args.get('level')}（可继续生成下一级）"
    if tool == "regenerate_l5":
        return "⚙️ 重生成 l5 正文（修正元素污染）"
    if tool == "ai_flavor_polish":
        return "✨ 对当前 l5 正文做去 AI 味打磨"
    if tool == "finalize":
        return "💾 保存本章到书（落盘：章纲/正文/评分）"
    if tool == "finish_arc":
        return f"🏁 标记情节「{n}」已完成"
    if tool == "new_arc":
        return f"➕ 新建情节：{str(args.get('l1') or '')[:30]}"
    if tool == "add_element":
        return f"➕ 新建元素：{args.get('name')}（{args.get('kind')}）"
    if tool == "update_element":
        return f"✏ 更新元素：{args.get('name') or args.get('id')}"
    if tool == "select_for_arc":
        return f"🎯 把元素「{args.get('name')}」加入本弧参与"
    if tool == "add_relation":
        return f"🔗 加元素关系：{args.get('from')} ↔ {args.get('to')}"
    if tool == "remember":
        return f"🧠 记入记忆：{str(args.get('key') or '')[:20]}"
    if tool == "forget":
        return f"🗑 删除记忆：{args.get('key')}"
    if tool == "set_active_chapter":
        return f"📖 切换到第 {int(args.get('idx', 0) or 0) + 1} 章"
    if tool == "capacity":
        return "📏 估算当前 l1 能支撑多少章/字的高质量正文（只读）"
    if tool == "enrich_l1":
        return "🛠 按三维诊断补齐当前 l1（加密度/内容/质量锚点）"
    if tool == "fill_settings":
        return f"💾 保存基本设定（{sum(1 for v in (args.get('settings') or {}).values() if v)} 字段）"
    if tool == "generate":
        return "📄 生成设定集 + 元素清单"
    if tool == "create_arc":
        return f"➕ 创建开篇弧：{str(args.get('l1') or '')[:30]}"
    return f"执行工具 {tool}"


def _should_proactive_search(user_msg: str, always: bool = False) -> bool:
    """用户消息像"求帮助/问" → 后端预检索注入上下文（不依赖模型主动调工具，防幻觉）。

    always=True（web_search 开启 / AnySearch 模式）：长度够就预检索，不要求命中关键词。
    """
    q = str(user_msg or "")
    if len(q) < 8:
        return False
    if always:
        return True
    return any(h in q for h in _RETRIEVAL_HINT)


async def _execute_chat_tool(
    tool: str,
    args: dict[str, Any],
    book_root: Path,
    arc: dict[str, Any],
    elements: dict[str, Any],
    *,
    dry_run: bool = False,
    search_srcs: list[str] | None = None,
    mode: str = "normal",
) -> tuple[dict[str, Any] | None, bool]:
    """执行一个对话工具，返回 (event, needs_confirm)。event 含 summary/detail/failed。

    dry_run=True（创作助手需用户同意）：内容工具只构建**提案事件**（含 args），不执行，
    返回 (proposal, needs_confirm=True)；前端确认后经 arc/chat/apply 以 dry_run=False 真执行。
    只读工具（search/score/pollution/lookup/list_*）不受 dry_run 影响，始终执行。

    mode="init"：初始化模式——禁用 l2-l5 阶梯工具（step/modify_level/finalize 等）。
    """
    # 【2026-08-15 初始化模式】init 模式禁用 l2-l5 阶梯工具
    if mode == "init" and tool in _LADDER_TOOLS:
        return {"tool": tool, "summary": f"🚫 初始化阶段不可用 {tool}",
                "detail": "先建好基本设定/开篇弧，进创作模式后再用该工具。", "failed": True}, False
    # ── 内容工具：dry_run 时只提案，需用户同意 ──
    if dry_run and tool in _CONTENT_TOOLS:
        return {
            "tool": tool,
            "args": dict(args or {}),
            "summary": _tool_proposal_summary(tool, args, arc),
            "detail": "用户同意后执行。",
            "pending": True,
        }, True

    # ── new_arc：建新情节（不依赖当前弧；书级对话入口）──
    if tool == "new_arc":
        l1 = str(args.get("l1") or "").strip()
        if not l1:
            return {"tool": "new_arc", "summary": "➕ 建弧失败：没给 l1", "detail": "需要 l1 参数", "failed": True}, False
        try:
            n_chapters = int(args.get("n_chapters") or 1)
        except (ValueError, TypeError):
            n_chapters = 1
        res = await new_arc(book_root, l1=l1, n_chapters=n_chapters, carry_prev=True)
        if res.get("ok"):
            arcv = res.get("arc") or {}
            return {"tool": "new_arc", "summary": f"➕ 已创建情节「{arcv.get('name') or ''}」", "detail": f"l1：{l1}（每弧 {n_chapters} 章）", "new_arc_id": arcv.get("id")}, False
        return {"tool": "new_arc", "summary": f"➕ 建弧失败：{res.get('error')}", "detail": str(res.get("error")), "failed": True}, False

    # ── fill_settings：初始化模式——保存 16 字段基本设定 ──
    if tool == "fill_settings":
        new_s = args.get("settings") or {}
        if not isinstance(new_s, dict) or not new_s:
            return {"tool": "fill_settings", "summary": "💾 保存基本设定失败：缺 settings", "detail": "需要 settings 字典", "failed": True}, False
        try:
            cur = load_basic_settings(book_root)
            merged = dict(_empty_basic_settings())
            for k, v in (cur or {}).items():
                if v:
                    merged[k] = v
            for k, v in new_s.items():
                if v is not None and v != "":
                    merged[k] = v
            save_basic_settings(book_root, merged)
            return {"tool": "fill_settings", "summary": f"💾 已保存基本设定（{sum(1 for v in new_s.values() if v)} 字段）", "detail": json.dumps({k: str(v)[:40] for k, v in new_s.items() if v}, ensure_ascii=False)[:300], "changed": True}, False
        except Exception as exc:  # noqa: BLE001
            return {"tool": "fill_settings", "summary": f"💾 保存失败：{str(exc)[:60]}", "detail": str(exc)[:100], "failed": True}, False

    # ── generate：初始化模式——按基本设定生成 设定集+elements ──
    if tool == "generate":
        try:
            settings = load_basic_settings(book_root)
            res = await generate_settings(book_root, settings)
            if res.get("ok"):
                n_chars = len((res.get("elements") or {}).get("characters", []))
                return {"tool": "generate", "summary": f"📄 已生成设定集（{len(res.get('setting_files') or [])} 文件）+ 元素（{n_chars} 角色）", "detail": f"设定集：{', '.join(f['name'] for f in (res.get('setting_files') or []))}", "changed": True}, False
            return {"tool": "generate", "summary": f"📄 生成失败：{res.get('error')}", "detail": str(res.get("error")), "failed": True}, False
        except Exception as exc:  # noqa: BLE001
            return {"tool": "generate", "summary": f"📄 生成失败：{str(exc)[:60]}", "detail": str(exc)[:100], "failed": True}, False

    # ── create_arc：初始化模式——建开篇弧（带容量诊断补齐）──
    if tool == "create_arc":
        l1 = str(args.get("l1") or "").strip()
        if not l1:
            return {"tool": "create_arc", "summary": "➕ 建弧失败：没给 l1", "detail": "需要 l1 参数", "failed": True}, False
        try:
            from .capacity import diagnose_arc_capacity, enrich_l1
            settings = load_basic_settings(book_root)
            style = str(settings.get("style") or "")
            role_setting = str(settings.get("role_setting") or "")
            diag = await diagnose_arc_capacity(l1, style=style, role_setting=role_setting)
            filled_l1 = l1
            if diag.get("should_fill"):
                filled = await enrich_l1(l1, diag, style=style, role_setting=role_setting)
                if filled.get("ok"):
                    filled_l1 = filled["text"]
            res = await new_arc(book_root, l1=filled_l1, n_chapters=1, auto_diagnose=False, carry_prev=False)
            if res.get("ok"):
                cap = diag.get("capacity") or {}
                note = "已补齐" if filled_l1 != l1 else "未补齐"
                return {"tool": "create_arc", "summary": f"➕ 已建开篇弧（容量 {cap.get('total_chapters_est')} 章/{cap.get('total_chars_est')} 字，{note}）", "detail": f"l1：{filled_l1}", "new_arc_id": res["arc"]["id"], "changed": True}, False
            return {"tool": "create_arc", "summary": f"➕ 建弧失败：{res.get('error')}", "detail": str(res.get("error")), "failed": True}, False
        except Exception as exc:  # noqa: BLE001
            return {"tool": "create_arc", "summary": f"➕ 建弧失败：{str(exc)[:60]}", "detail": "原 l1 未改动。", "failed": True}, False

    # ── capacity：估算当前 l1 能支撑多少字/章（只读，无需当前弧）──
    if tool == "capacity":
        from .capacity import estimate_arc_capacity
        l1_txt = str((arc or {}).get("l1") or "").strip()
        if not l1_txt:
            return {"tool": "capacity", "summary": "📏 容量估算：当前没有 l1", "detail": "（先写 l1 再估算）", "failed": True}, False
        est = estimate_arc_capacity(l1_txt, n_chapters=1)
        det = (f"约可支撑 {est['total_chapters_est']} 章 / {est['total_chars_est']} 字"
               f"（单章约 {est['cap_chapter_est']} 字）。密度 {est['info']['beats']} 拍"
               f"，冲突 {est['info']['conflicts']}，锚点 {est['info']['anchors']}。")
        return {"tool": "capacity", "summary": f"📏 此 l1 约可支撑 {est['total_chapters_est']} 章 / {est['total_chars_est']} 字", "detail": det}, False

    # ── 需要当前弧的工具：无弧（书级对话）时提示先建弧 ──
    if arc is None:
        return {"tool": tool, "summary": "⚠ 当前还没有情节", "detail": "请先让 AI 建弧（new_arc）或用左栏「＋ 新建情节」。", "failed": True}, False

    state = arc.setdefault("state", {})
    levels = state.get("levels") or {}
    l5 = str((levels.get("l5") or {}).get("text") or "").strip()
    kind_label = {"characters": "角色", "items": "物品", "settings": "设定", "locations": "地点", "maps": "地图"}

    if tool == "search":
        from . import writing_search
        query = str(args.get("query") or "").strip() or "当前剧情"
        res = await writing_search.search_around(query, book_root, sources=search_srcs)
        fmt = writing_search.format_results(res["results"])
        return {"tool": "search", "summary": f"🔍 搜索「{query}」→ {len(res['results'])} 命中", "detail": fmt}, False

    if tool == "score":
        if not l5:
            return {"tool": "score", "summary": "📊 评分：当前没有 l5 正文", "detail": "（无正文可评分）", "failed": True}, False
        intent = _build_intent_text(state, state.get("active_chapter", 0))
        sc = await score_chapter(l5, intent, arc, elements)
        det = (f"意图兑现 {sc.get('intent_score')} / 纯质量 {sc.get('quality_score')} / 综合 {sc.get('overall')}")
        return {"tool": "score", "summary": f"📊 双评分：意图 {sc.get('intent_score')} / 质量 {sc.get('quality_score')} / 综合 {sc.get('overall')}", "detail": det}, False

    if tool == "pollution":
        if not l5:
            return {"tool": "pollution", "summary": "🔍 污染检测：当前没有 l5 正文", "detail": "（无正文可检测）", "failed": True}, False
        res = await check_element_pollution_full(l5, arc, elements)
        if res.get("polluted"):
            names = "、".join(str(h.get("name")) for h in res.get("hits", []) if h.get("name"))
            return {"tool": "pollution", "summary": f"🔍 污染检测：⚠ {len(res.get('hits') or [])} 处命中", "detail": f"⚠ 检出未参与元素：{names or '（有命中）'}"}, False
        return {"tool": "pollution", "summary": "🔍 污染检测：✓ 未发现未参与元素", "detail": "✓ 未发现未参与元素污染"}, False

    if tool == "modify_level":
        level = str(args.get("level") or "").strip()
        instruction = str(args.get("instruction") or "").strip()
        if level not in ("l1", "l2", "l3", "l4", "l5") or not instruction:
            return {"tool": "modify_level", "summary": "✏ 改写失败：参数不完整", "detail": "需要 level + instruction", "failed": True}, False
        res = await arc_modify(book_root, arc.get("id"), level, instruction)
        if res.get("ok"):
            return {"tool": "modify_level", "summary": f"✏ 已按意见改写 {level}（下游级已清空）", "detail": f"{level} 已改写，下游级清空，请重新生成。", "arc_changed": True}, False
        return {"tool": "modify_level", "summary": f"✏ 改写 {level} 失败：{res.get('error')}", "detail": str(res.get("error")), "failed": True}, False

    if tool == "step":
        res = await arc_step(book_root, arc.get("id"))
        if res.get("ok"):
            return {"tool": "step", "summary": f"➡ 已生成 {res.get('to')}（待确认）", "detail": f"已生成 {res.get('to')}，停在待确认。", "arc_changed": True}, False
        return {"tool": "step", "summary": f"➡ 生成失败：{res.get('error')}", "detail": str(res.get("error")), "failed": True}, False

    if tool == "regenerate_l5":
        res = await arc_regenerate_l5(book_root, arc.get("id"))
        if res.get("ok"):
            n = res.get("pollution_was") or []
            det = f"已重生成正文（修正 {len(n)} 处污染）" if res.get("regenerated") else str(res.get("message") or "已处理")
            return {"tool": "regenerate_l5", "summary": "⚙️ 已重生成 l5 正文", "detail": det, "arc_changed": True}, False
        return {"tool": "regenerate_l5", "summary": f"⚙️ 重生成失败：{res.get('error')}", "detail": str(res.get("error")), "failed": True}, False

    if tool == "finalize":
        return {"tool": "finalize", "summary": "💾 保存本章到书（需确认）", "detail": "落盘写入：大纲/章纲 + AI生成/正文 + 审查报告/评分.json。"}, True

    # ── 新增：阶梯/章节类 ──

    if tool == "confirm_level":
        level = str(args.get("level") or "").strip()
        if level not in ("l1", "l2", "l3", "l4", "l5"):
            return {"tool": "confirm_level", "summary": f"✓ 确认失败：无效级别 {level}", "detail": "level 必须是 l1-l5", "failed": True}, False
        res = arc_confirm(book_root, arc.get("id"), level)
        if res.get("ok"):
            return {"tool": "confirm_level", "summary": f"✓ 已确认 {level}", "detail": f"{level} 已标记确认，可以继续生成下一级。", "arc_changed": True}, False
        return {"tool": "confirm_level", "summary": f"✓ 确认 {level} 失败：{res.get('error')}", "detail": str(res.get("error")), "failed": True}, False

    if tool == "set_level":
        level = str(args.get("level") or "").strip()
        if level not in ("l1", "l2", "l3", "l4", "l5"):
            return {"tool": "set_level", "summary": f"✍ 覆写失败：无效级别 {level}", "detail": "level 必须是 l1-l5", "failed": True}, False
        text = str(args.get("text") or "")
        data = args.get("data")
        chapter_idx = args.get("chapter_idx")
        if isinstance(chapter_idx, str) and chapter_idx.isdigit():
            chapter_idx = int(chapter_idx)
        res = arc_set_level(book_root, arc.get("id"), level, text, data, chapter_idx)
        if res.get("ok"):
            return {"tool": "set_level", "summary": f"✍ 已覆写 {level}", "detail": f"{level} 内容已直接覆写。", "arc_changed": True}, False
        return {"tool": "set_level", "summary": f"✍ 覆写 {level} 失败：{res.get('error')}", "detail": str(res.get("error")), "failed": True}, False

    if tool == "set_active_chapter":
        idx = args.get("idx", 0)
        try:
            idx = int(idx)
        except (ValueError, TypeError):
            idx = 0
        res = arc_set_active_chapter(book_root, arc.get("id"), idx)
        if res.get("ok"):
            return {"tool": "set_active_chapter", "summary": f"📖 已切换到第 {idx + 1} 章", "detail": f"已切到第 {idx + 1} 章「{res.get('chapter', {}).get('title', '')}」，l4/l5 已清空。", "arc_changed": True}, False
        return {"tool": "set_active_chapter", "summary": f"📖 切章失败：{res.get('error')}", "detail": str(res.get("error")), "failed": True}, False

    # ── enrich_l1：按三维诊断补齐当前 l1（有副作用，dry_run 已提案；apply 真执行）──
    if tool == "enrich_l1":
        from .capacity import diagnose_arc_capacity, enrich_l1 as _enrich_l1
        l1_txt = str(arc.get("l1") or "").strip()
        if not l1_txt:
            return {"tool": "enrich_l1", "summary": "🛠 补齐 l1：当前没有 l1", "detail": "（先写 l1 再补齐）", "failed": True}, False
        max_chars = int(args.get("max_chars") or 160)
        try:
            diag = await diagnose_arc_capacity(
                l1_txt, style=str(state.get("style") or ""),
                role_setting=str(state.get("role_setting") or ""),
                template=state.get("template"), n_chapters=1)
            filled = await _enrich_l1(l1_txt, diag, max_chars=max_chars)
            if not filled.get("ok"):
                return {"tool": "enrich_l1", "summary": f"🛠 补齐 l1 未执行：{filled.get('reason')}", "detail": "原 l1 未改动。", "failed": True}, False
            # 写回 l1（arc_set_level 自动清下游 + 重提 key_facts + 同步 arc.l1）
            res = arc_set_level(book_root, arc.get("id"), "l1", filled["text"])
            if not res.get("ok"):
                return {"tool": "enrich_l1", "summary": f"🛠 补齐后写回失败：{res.get('error')}", "detail": str(res.get("error")), "failed": True}, False
            return {"tool": "enrich_l1", "summary": "🛠 已补齐 l1（密度/内容/质量）", "detail": f"原：{l1_txt[:40]}… → 新：{filled['text'][:60]}", "arc_changed": True}, False
        except Exception as exc:  # noqa: BLE001
            return {"tool": "enrich_l1", "summary": f"🛠 补齐 l1 失败：{str(exc)[:50]}", "detail": "原 l1 未改动。", "failed": True}, False

    if tool == "ai_flavor_polish":
        if not l5:
            return {"tool": "ai_flavor_polish", "summary": "✨ 去 AI 味：当前没有 l5 正文", "detail": "（无正文可处理）", "failed": True}, False
        try:
            from .ai_flavor import polish_text
            polished = await polish_text(l5, context=f"书名：{arc.get('name', '')} 第{state.get('active_chapter', 0) + 1}章")
            if not polished or len(polished) < len(l5) * 0.5:
                return {"tool": "ai_flavor_polish", "summary": "✨ 去 AI 味失败：输出异常", "detail": f"输出长度 {len(polished or '')}，疑似失败", "failed": True}, False
            # 把打磨后的写回 l5
            arc_set_level(book_root, arc.get("id"), "l5", polished)
            delta = len(polished) - len(l5)
            delta_str = f"+{delta}" if delta >= 0 else str(delta)
            return {"tool": "ai_flavor_polish", "summary": f"✨ 已去 AI 味（字数 {delta_str}）", "detail": f"正文 {len(l5)} → {len(polished)} 字。", "arc_changed": True}, False
        except Exception as e:
            return {"tool": "ai_flavor_polish", "summary": f"✨ 去 AI 味失败：{e}", "detail": str(e), "failed": True}, False

    if tool == "finish_arc":
        return {"tool": "finish_arc", "summary": "🏁 标记本情节已完成（需确认）", "detail": "标记后下一个新情节会自动把本情节结局作为前文锚点。"}, True

    # ── 新增：元素类 ──

    if tool == "lookup_element":
        name = str(args.get("name") or "").strip()
        if not name:
            return {"tool": "lookup_element", "summary": "👤 查元素失败：没给名字", "detail": "需要 name 参数", "failed": True}, False
        found = []
        for kind in ("characters", "items", "settings", "maps"):
            for e in elements.get(kind, []):
                en = str(e.get("name") or "")
                if name in en or en in name:
                    found.append((kind, e))
        if not found:
            return {"tool": "lookup_element", "summary": f"👤 未找到元素「{name}」", "detail": "元素库中没有匹配项。", "failed": True}, False
        lines = []
        kind_label = {"characters": "角色", "items": "物品", "settings": "设定", "locations": "地点", "maps": "地图"}
        for kind, e in found[:5]:
            lines.append(f"【{kind_label.get(kind, kind)}】{e.get('name', '')}")
            desc = str(e.get("desc") or "")[:200]
            if desc:
                lines.append(f"  描述：{desc}")
            for k, v in (e.get("fields") or {}).items():
                if isinstance(v, (str, int, float)) and str(v):
                    lines.append(f"  {k}：{v}")
        return {"tool": "lookup_element", "summary": f"👤 找到 {len(found)} 个元素匹配「{name}」", "detail": "\n".join(lines)}, False

    if tool == "add_element":
        kind = str(args.get("kind") or "").strip()
        name = str(args.get("name") or "").strip()
        desc = str(args.get("desc") or "").strip()
        if kind not in ("characters", "items", "settings", "locations") or not name:
            return {"tool": "add_element", "summary": "➕ 新建元素失败：参数不完整", "detail": "需要 kind(characters/items/settings/locations) + name", "failed": True}, False
        fields = args.get("fields") or {}
        elems = load_elements(book_root)
        # 重名检查：同名（含别名匹配）已存在则不新增，返回已有元素
        existing = None
        for e in elems.get(kind, []):
            en = str(e.get("name") or "")
            if en == name or name in en or en in name:
                existing = e
                break
            for alias in (e.get("alias") or []):
                if str(alias) == name or name in str(alias) or str(alias) in name:
                    existing = e
                    break
            if existing:
                break
        if existing:
            changed = False
            if desc and not existing.get("desc"):
                existing["desc"] = desc
                changed = True
            if fields and isinstance(fields, dict):
                flds = existing.setdefault("fields", {})
                for k, v in fields.items():
                    if k not in flds:
                        flds[k] = v
                        changed = True
            if changed:
                save_elements(book_root, elems)
            return {"tool": "add_element",
                    "summary": f"➕ {kind_label.get(kind, kind)}「{name}」已存在，跳过新建",
                    "detail": f"同名元素已存在（id={existing.get('id')}）。"
                              + ("已补充描述/字段。" if changed else ""),
                    "elem_changed": changed}, False
        new_e = {
            "id": uuid.uuid4().hex[:8],
            "name": name,
            "desc": desc,
            "fields": fields if isinstance(fields, dict) else {},
        }
        elems.setdefault(kind, []).append(new_e)
        save_elements(book_root, elems)
        return {"tool": "add_element", "summary": f"➕ 已新建{kind_label.get(kind, kind)}：{name}", "detail": f"id={new_e['id']}\n描述：{desc[:100]}", "elem_changed": True}, False

    if tool == "update_element":
        eid = str(args.get("id") or "").strip()
        ename = str(args.get("name") or "").strip()
        kind = str(args.get("kind") or "").strip()
        field = str(args.get("field") or "").strip()
        value = args.get("value", "")
        if not field:
            return {"tool": "update_element", "summary": "✏ 改元素失败：参数不完整", "detail": "需要 id(或 name+kind) + field", "failed": True}, False
        # 支持按名字查找：没给 id 时，用 name+kind 定位
        if not eid and ename and kind in ("characters", "items", "settings"):
            elems_tmp = load_elements(book_root)
            for e in elems_tmp.get(kind, []):
                if str(e.get("name") or "") == ename or ename in str(e.get("name") or ""):
                    eid = str(e.get("id", ""))
                    break
        if not eid:
            return {"tool": "update_element", "summary": "✏ 改元素失败：找不到元素", "detail": "需要 id 或 name+kind 定位元素", "failed": True}, False
        elems = load_elements(book_root)
        for kind in ("characters", "items", "settings", "maps"):
            for e in elems.get(kind, []):
                if e.get("id") == eid:
                    if field == "desc":
                        e["desc"] = str(value)
                    elif field == "name":
                        e["name"] = str(value)
                    else:
                        e.setdefault("fields", {})[field] = value
                    save_elements(book_root, elems)
                    return {"tool": "update_element", "summary": f"✏ 已更新 {e.get('name', '')} 的 {field}", "detail": f"字段 {field} 已更新为：{str(value)[:80]}", "elem_changed": True}, False
        return {"tool": "update_element", "summary": "✏ 改元素失败：没找到", "detail": f"id={eid} 不存在", "failed": True}, False

    if tool == "select_for_arc":
        eid = str(args.get("element_id") or args.get("id") or "").strip()
        ename = str(args.get("name") or "").strip()
        kind = str(args.get("kind") or "").strip()
        if kind not in ("characters", "items", "settings", "locations"):
            return {"tool": "select_for_arc", "summary": "🎯 加入白名单失败：参数不完整", "detail": "需要 element_id(或 name) + kind(characters/items/settings/locations)", "failed": True}, False
        # 支持按名字查找元素 id
        elems = load_elements(book_root)
        if not eid and ename:
            for e in elems.get(kind, []):
                en = str(e.get("name") or "")
                if en == ename or ename in en or en in ename:
                    eid = str(e.get("id", ""))
                    break
        if not eid:
            return {"tool": "select_for_arc", "summary": "🎯 加入白名单失败：找不到元素", "detail": f"没找到 {kind_label.get(kind, kind)}「{ename}」", "failed": True}, False
        arcs = load_arcs(book_root)
        a = _find_arc(arcs, arc.get("id"))
        if not a:
            return {"tool": "select_for_arc", "summary": "🎯 加入白名单失败：情节不存在", "detail": "", "failed": True}, False
        sel = a.setdefault("selected", {})
        lst = sel.setdefault(kind, [])
        if eid in lst:
            return {"tool": "select_for_arc", "summary": "🎯 该元素已在白名单中", "detail": ""}, False
        lst.append(eid)
        save_arcs(book_root, arcs)
        # 查元素名
        elems = load_elements(book_root)
        ename = eid
        for e in elems.get(kind, []):
            if e.get("id") == eid:
                ename = str(e.get("name", ""))
                break
        kind_label = {"characters": "角色", "items": "物品", "settings": "设定", "locations": "地点"}
        warn = "  ⚠ 当前 l5 已生成，加入后正文可能需要重生成以避免污染。" if l5 else ""
        return {"tool": "select_for_arc", "summary": f"🎯 已加入本弧：{ename}（{kind_label.get(kind, kind)}）", "detail": f"已加入 {kind} 白名单。{warn}", "arc_changed": True}, False

    if tool == "add_relation":
        from_e = str(args.get("from") or "").strip()
        to_e = str(args.get("to") or "").strip()
        rel_type = str(args.get("type") or "关联").strip()
        desc = str(args.get("desc") or "").strip()
        if not from_e or not to_e:
            return {"tool": "add_relation", "summary": "🔗 加关系失败：参数不完整", "detail": "需要 from + to", "failed": True}, False
        elems = load_elements(book_root)
        # 简单实现：在两个元素的 fields.relations 里各加一条
        def _find_id(name: str) -> str | None:
            for kind in ("characters", "items", "settings"):
                for e in elems.get(kind, []):
                    if e.get("name") == name or e.get("id") == name:
                        return e.get("id")
            return None
        fid = _find_id(from_e)
        tid = _find_id(to_e)
        if not fid or not tid:
            return {"tool": "add_relation", "summary": "🔗 加关系失败：找不到元素", "detail": f"from_id={fid}, to_id={tid}", "failed": True}, False
        for kind in ("characters", "items", "settings"):
            for e in elems.get(kind, []):
                if e.get("id") in (fid, tid):
                    rels = e.setdefault("fields", {}).setdefault("relations", [])
                    other = to_e if e.get("id") == fid else from_e
                    rels.append({"target": other, "type": rel_type, "desc": desc})
        save_elements(book_root, elems)
        return {"tool": "add_relation", "summary": f"🔗 已加关系：{from_e} ↔ {to_e}（{rel_type}）", "detail": desc or "（无描述）", "elem_changed": True}, False

    # ── 新增：记忆类 ──

    if tool == "remember":
        key = str(args.get("key") or "").strip()[:50]
        content = str(args.get("content") or "").strip()
        scope_raw = str(args.get("scope") or "book").strip().lower()
        if not content:
            return {"tool": "remember", "summary": "🧠 记不住：内容为空", "detail": "", "failed": True}, False
        scope = "book"
        if scope_raw in ("arc", "current_arc", "本弧"):
            scope = f"arc:{arc.get('id')}"
        elif scope_raw.startswith("arc:"):
            scope = scope_raw
        elif scope_raw.startswith("element:"):
            scope = scope_raw
        item = add_memory(book_root, content, scope=scope, key=key)
        label = "全书" if scope == "book" else ("本弧" if scope.startswith("arc:") else "元素")
        head = f"[{label}] {key}" if key else f"[{label}]"
        return {"tool": "remember", "summary": f"🧠 记住了：{head}", "detail": content[:200]}, False

    if tool == "forget":
        key = str(args.get("key") or args.get("id") or "").strip()
        if not key:
            return {"tool": "forget", "summary": "💭 删记忆失败：没给 key/id", "detail": "", "failed": True}, False
        items = load_memory(book_root)
        before = len(items)
        # 按 id 或 key 匹配
        items = [m for m in items if m.get("id") != key and m.get("key") != key]
        if len(items) == before:
            return {"tool": "forget", "summary": "💭 没找到这条记忆", "detail": f"key={key}", "failed": True}, False
        save_memory(book_root, items)
        return {"tool": "forget", "summary": f"💭 已删除记忆「{key}」", "detail": ""}, False

    if tool == "list_memory":
        scope = args.get("scope")
        items = list_memory(book_root, scope=scope) if scope else load_memory(book_root)
        if not items:
            return {"tool": "list_memory", "summary": "🧠 暂无记忆", "detail": "", "failed": True}, False
        lines = []
        for m in items[:15]:
            sc = _mem_scope(m)
            label = "全书" if sc == "book" else ("本弧" if sc.startswith("arc:") else "元素")
            k = m.get("key") or ""
            t = str(m.get("text", ""))[:60]
            lines.append(f"· [{label}] {k}：{t}")
        return {"tool": "list_memory", "summary": f"🧠 共 {len(items)} 条记忆", "detail": "\n".join(lines)}, False

    # ── 新增：列表类 ──

    if tool == "list_arcs":
        arcs_data = load_arcs(book_root).get("arcs", [])
        if not arcs_data:
            return {"tool": "list_arcs", "summary": "📚 暂无情节", "detail": "", "failed": True}, False
        lines = []
        for i, a in enumerate(arcs_data[:20]):
            status = a.get("status") or "todo"
            status_label = "✓完成" if status == "done" else "●进行中" if status == "writing" else "○待创作"
            lines.append(f"{i+1}. 《{a.get('name', '')}》 {status_label}（{(a.get('chapters') and len(a['chapters'])) or 0} 章）")
        return {"tool": "list_arcs", "summary": f"📚 共 {len(arcs_data)} 个情节", "detail": "\n".join(lines)}, False

    return None, False


def _arc_view(arc: dict[str, Any], elements: dict[str, Any]) -> dict[str, Any]:
    """前端视图：情节信息 + 选中元素的可读名单 + 当前阶梯摘要。"""
    state = arc.get("state") or {}
    levels = state.get("levels") or {}
    sel = arc.get("selected") or {}
    name_of = {e.get("id"): e.get("name") for e in elements.get("characters", [])}
    name_of.update({e.get("id"): e.get("name") for e in elements.get("items", [])})
    name_of.update({e.get("id"): e.get("name") for e in elements.get("settings", [])})
    tpl = state.get("template")
    return {
        "id": arc.get("id"),
        "name": arc.get("name"),
        "l1": arc.get("l1"),
        "l2": arc.get("l2"),
        "n_chapters": arc.get("n_chapters"),
        "status": arc.get("status"),
        "template": (
            {
                "id": tpl.get("id"),
                "name": tpl.get("name"),
                "archetype": tpl.get("archetype"),
                "similarity": state.get("template_sim"),
            } if isinstance(tpl, dict) else None
        ),
        "selected": {
            "characters": [{"id": cid, "name": name_of.get(cid, cid)}
                           for cid in sel.get("characters", [])],
            "items": [{"id": iid, "name": name_of.get(iid, iid)}
                      for iid in sel.get("items", [])],
            "settings": [{"id": sid, "name": name_of.get(sid, sid)}
                         for sid in sel.get("settings", [])],
        },
        "active_chapter": state.get("active_chapter", 0),
        "capacity": arc.get("capacity"),
        "l1_original": arc.get("l1_original"),
        "l1_filled": arc.get("l1_filled"),
        "levels": {
            k: {
                "confirmed": bool((levels.get(k) or {}).get("confirmed")),
                "filled": _filled(state, k),
            } for k in ("l1", "l2", "l3", "l4", "l5")
        },
        "chapters": arc.get("chapters", []),
        "prev_anchor": arc.get("prev_anchor", []),
        "prev_arc_id": arc.get("prev_arc_id"),
    }


# ════════════════════════════════════════════════════════════════════
# 元素污染检测：确定性扫描 + LLM 复核双检
# ════════════════════════════════════════════════════════════════════

def _matches_any(names: list[str], text: str) -> list[str]:
    """确定性匹配：任一称呼作为子串出现在正文（长度≥2 才认，防空泛单字）。"""
    hits: list[str] = []
    for n in names:
        n = str(n or "").strip()
        if len(n) >= 2 and n in text:
            hits.append(n)
    return hits


def check_element_pollution(
    gen_text: str,
    arc: dict[str, Any],
    elements: dict[str, Any],
) -> dict[str, Any]:
    """确定性扫描：未选中元素是否出现在正文（双检第一检）。

    返回 {polluted, hits, sel_present, sel_ratio, unsel_checked}。
    - 未选中元素的名字/别名/terms 在正文出现即污染（除非该称呼是被选中元素的子串，
      即「青云宗」是选中设定「青云宗大比」的一部分——那不算）。
    - sel_present/sel_ratio：选中元素实际出场情况（元素合规分用）。
    """
    text = str(gen_text or "")
    sel = arc.get("selected") or {}
    sel_c = set(sel.get("characters") or [])
    sel_i = set(sel.get("items") or [])
    sel_s = set(sel.get("settings") or [])

    # 允许的子串：所有选中元素的称呼（避免「青云宗大比」里误判「青云宗」）
    allowed = set()
    for e in elements.get("characters", []):
        if e.get("id") in sel_c:
            allowed.update(_names_of(e))
    for e in elements.get("items", []):
        if e.get("id") in sel_i:
            allowed.update(_names_of(e))
    for e in elements.get("settings", []):
        if e.get("id") in sel_s:
            allowed.update(_names_of(e))

    def _is_allowed(names: list[str], hit: str) -> bool:
        return any(hit in al for al in allowed if al and len(al) > len(hit))

    hits: list[dict[str, str]] = []
    sel_present: dict[str, list[str]] = {"characters": [], "items": [], "settings": []}
    sel_total = {"characters": 0, "items": 0, "settings": 0}

    def _scan(kind: str, elems: list[dict[str, Any]], sel_ids: set[str]) -> None:
        for e in elems:
            eid = e.get("id")
            names = _names_of(e)
            matched = _matches_any(names, text)
            if eid in sel_ids:
                sel_total[kind] += 1
                if matched:
                    sel_present[kind].append(eid)
            else:
                for h in matched:
                    if _is_allowed(names, h):
                        continue
                    hits.append({"id": eid, "kind": kind,
                                 "name": e.get("name", ""), "matched": h})

    _scan("characters", elements.get("characters", []), sel_c)
    _scan("items", elements.get("items", []), sel_i)
    _scan("settings", elements.get("settings", []), sel_s)

    n_sel = sum(sel_total.values())
    n_present = sum(len(v) for v in sel_present.values())
    sel_ratio = round(n_present / n_sel, 4) if n_sel else 1.0
    return {
        "polluted": bool(hits),
        "hits": hits,
        "sel_present": sel_present,
        "sel_ratio": sel_ratio,
        "sel_total": sel_total,
        "unsel_checked": len(elements.get("characters", [])) +
                         len(elements.get("items", [])) +
                         len(elements.get("settings", [])) - n_sel,
    }


async def _check_pollution_llm(
    gen_text: str,
    arc: dict[str, Any],
    elements: dict[str, Any],
) -> dict[str, Any]:
    """LLM 复核（双检第二检）：抓确定性扫描漏掉的间接提及（身份代称/绰号/「那人」）。"""
    from .llm_client import chat_json

    sel = arc.get("selected") or {}
    sel_c = set(sel.get("characters") or [])
    sel_i = set(sel.get("items") or [])
    sel_s = set(sel.get("settings") or [])

    part = []
    for e in elements.get("characters", []):
        if e.get("id") in sel_c:
            part.append(f"[参与]角色 {e['name']}")
    for e in elements.get("items", []):
        if e.get("id") in sel_i:
            part.append(f"[参与]物品 {e['name']}")
    for e in elements.get("settings", []):
        if e.get("id") in sel_s:
            part.append(f"[参与]设定 {e['name']}")

    ban = []
    for e in elements.get("characters", []):
        if e.get("id") not in sel_c:
            ban.append(f"角色 {e['name']}（称呼：{'/'.join(_names_of(e))}）")
    for e in elements.get("items", []):
        if e.get("id") not in sel_i:
            ban.append(f"物品 {e['name']}（称呼：{'/'.join(_names_of(e))}）")
    for e in elements.get("settings", []):
        if e.get("id") not in sel_s:
            ban.append(f"设定 {e['name']}（称呼：{'/'.join(_names_of(e))}）")

    sys_p = (
        "你是严格的元素合规审查员。本章正文只允许出现【参与元素】；"
        "【未参与元素】绝对禁止出现——直呼姓名、别名、术语、身份代称、绰号、"
        "间接提及（如用『那位大人』『那人』指向未参与角色）都算污染。"
        "只报告高置信度命中；参与元素的正常出现不算；不要捕风捉影。"
        "输出严格 JSON："
        '{"polluted": true/false, "findings": [{"element": "名字", '
        '"kind": "character/item/setting", "evidence": "正文依据原文"}]}'
    )
    usr_p = (
        "【参与元素】\n" + ("\n".join(part) if part else "（本情节未选择任何元素）")
        + "\n\n【未参与元素（禁出）】\n" + ("\n".join(ban) if ban else "（无）")
        + "\n\n【本章正文】\n---\n" + str(gen_text or "")[:8000] + "\n---\n"
        + "请判断是否出现未参与元素："
    )
    try:
        resp = await chat_json(system=sys_p, user=usr_p, call_type="ai_creation_pollution",
                               temperature=0.2, max_tokens=1500)
    except Exception as e:  # noqa: BLE001
        return {"polluted": None, "findings": [], "error": str(e)[:200]}
    data = resp.get("data") if not resp.get("error") else None
    if not isinstance(data, dict):
        return {"polluted": None, "findings": [], "error": "LLM 审阅无结果"}
    findings = []
    for f in (data.get("findings") or []):
        if isinstance(f, dict) and str(f.get("element") or "").strip():
            findings.append({
                "element": str(f.get("element"))[:50],
                "kind": str(f.get("kind") or "character"),
                "evidence": str(f.get("evidence") or "")[:200],
            })
    return {"polluted": bool(data.get("polluted")) if findings else False,
            "findings": findings[:8], "error": None}


async def check_element_pollution_full(
    gen_text: str,
    arc: dict[str, Any],
    elements: dict[str, Any],
) -> dict[str, Any]:
    """完整双检：确定性扫描 + LLM 复核。返回合并污染清单。"""
    det = check_element_pollution(gen_text, arc, elements)
    llm = await _check_pollution_llm(gen_text, arc, elements)
    all_hits = [dict(h, src="deterministic") for h in det["hits"]]
    all_hits += [{"id": "", "kind": f.get("kind"), "name": f.get("element"),
                  "matched": f.get("evidence"), "src": "llm"}
                 for f in llm.get("findings", [])]
    return {
        "polluted": bool(all_hits) or bool(llm.get("findings")),
        "hits": all_hits,
        "deterministic": det,
        "llm": llm,
    }


# ════════════════════════════════════════════════════════════════════
# 每章双评分（无参考原文）
# ════════════════════════════════════════════════════════════════════

def _build_intent_text(state: dict[str, Any], chapter_idx: int = 0) -> str:
    """合成意图文本：l2 情节概要 + 当前章 l3 core/beats（供意图兑现分作 target）。"""
    levels = state.get("levels") or {}
    l2 = str(levels.get("l2", {}).get("text") or "").strip()
    l3d = levels.get("l3", {}).get("data") or {}
    if isinstance(l3d, dict) and isinstance(l3d.get("chapters"), list):
        chs = l3d["chapters"]
        idx = max(0, min(int(chapter_idx), len(chs) - 1))
        ch = chs[idx] if isinstance(chs[idx], dict) else {}
    else:
        ch = l3d if isinstance(l3d, dict) else {}
    parts: list[str] = []
    if l2:
        parts.append("情节概要：" + l2)
    title = str(ch.get("title") or "").strip()
    core = str(ch.get("core") or "").strip()
    beats = ch.get("beats") or []
    if title:
        parts.append("章节：" + title)
    if core:
        parts.append("章节核心：" + core)
    if isinstance(beats, list):
        beat_lines = [f"- {str(b).strip()}" for b in beats if str(b or "").strip()]
        if beat_lines:
            parts.append("拍点：\n" + "\n".join(beat_lines))
    return "\n".join(parts)


async def score_arc_chapter(
    book_root: str | Path,
    arc_id: str,
    gen_text: str,
    *,
    chapter_idx: int | None = None,
) -> dict[str, Any]:
    """按情节取当前章合成意图文本 → 双评分（server 端点用，内部加载情节）。"""
    book_root = Path(book_root)
    _wb_ctx(book_root, arc=arc_id, step="chapter_score")
    arcs = load_arcs(book_root)
    arc = _find_arc(arcs, arc_id)
    if not arc:
        return {"ok": False, "error": f"情节不存在：{arc_id}"}
    state = arc.get("state") or {}
    idx = (int(chapter_idx) if chapter_idx is not None
           else int(state.get("active_chapter") or 0))
    intent_text = _build_intent_text(state, idx)
    elements = load_elements(book_root)
    score = await score_chapter(str(gen_text or ""), intent_text, arc, elements)
    score["ok"] = True
    score["intent_text"] = intent_text
    return score


async def check_arc_pollution(
    book_root: str | Path,
    arc_id: str,
    gen_text: str,
) -> dict[str, Any]:
    """完整双检（确定性 + LLM）→ 情节内元素污染清单（server 端点用）。"""
    book_root = Path(book_root)
    arcs = load_arcs(book_root)
    arc = _find_arc(arcs, arc_id)
    if not arc:
        return {"ok": False, "error": f"情节不存在：{arc_id}"}
    elements = load_elements(book_root)
    res = await check_element_pollution_full(str(gen_text or ""), arc, elements)
    res["ok"] = True
    return res


async def score_chapter(
    gen_text: str,
    intent_text: str,
    arc: dict[str, Any],
    elements: dict[str, Any],
) -> dict[str, Any]:
    """每章双评分：意图兑现分 + 纯质量分（含元素合规）。

    - intent_score：plot_similarity + beat_sequence_fidelity + semantic_coverage
      + factual_consistency_score 加权（target=合成意图文本）
    - quality_score：AI 味（无原文审阅）+ flow + 流畅/连贯/对白 + 元素合规
    - 单分量全部透出，供前端展开评分明细；任一分量失败用中性值降级（不整体崩）
    """
    from .ai_flavor import flow_narration_detector, review_ai_flavor_standalone
    from .key_details import factual_consistency_score
    from .plot_fidelity import beat_sequence_fidelity
    from .plot_similarity import plot_similarity
    from .scorer import fluency_score, narrative_cohesion, semantic_coverage
    from .structural_analyzer import dialogue_quality_score

    gen_text = str(gen_text or "")
    intent_text = str(intent_text or "")
    details: dict[str, Any] = {}

    # ── 意图兑现分 ───────────────────────────────────────────────
    plot_res = await plot_similarity(gen_text, intent_text)
    plot_s = round(float(plot_res.get("plot") or 0.0), 4) if plot_res.get("n_beats") else None
    details["plot"] = {"plot": plot_s, "coverage": plot_res.get("coverage"),
                       "n_beats": plot_res.get("n_beats")}

    beat_res = beat_sequence_fidelity(intent_text, gen_text)
    beat_s = float(beat_res.get("composite") or 0.0)
    details["beat"] = beat_res

    sem_s = round(await semantic_coverage(gen_text, intent_text, n_segments=4), 4)
    details["semantic"] = sem_s

    fac = factual_consistency_score(gen_text, intent_text)
    fac_s = float(fac.get("score") or 0.0)
    details["factual"] = fac

    def _w(*vals: tuple[float | None, float]) -> float:
        num = sum(v * w for v, w in vals if v is not None)
        den = sum(w for v, w in vals if v is not None)
        return round(num / den, 4) if den else 0.0

    intent_score = _w(
        (plot_s, INTENT_W["plot"]),
        (beat_s, INTENT_W["beat"]),
        (sem_s, INTENT_W["semantic"]),
        (fac_s, INTENT_W["factual"]),
    )

    # ── 纯质量分 ─────────────────────────────────────────────────
    ai = await review_ai_flavor_standalone(gen_text)
    ai_score = ai.get("score")
    if ai_score is None:
        ai_score = float((ai.get("flow") or {}).get("flow_clean", 0.5))
    flow_clean = float((ai.get("flow") or {}).get("flow_clean", 0.5))
    details["ai_flavor"] = {"score": ai_score, "flow_clean": flow_clean,
                            "findings": ai.get("findings", []),
                            "summary": ai.get("summary", "")}

    flu = round(float(fluency_score(gen_text)), 4)
    coh = round(float(narrative_cohesion(gen_text)), 4)
    dlg = round(float(dialogue_quality_score(gen_text)), 4)
    details["fluency"] = flu
    details["cohesion"] = coh
    details["dialogue"] = dlg

    poll = check_element_pollution(gen_text, arc, elements)
    sel_ratio = float(poll.get("sel_ratio") or 0.0)
    clean = 1.0 if not poll.get("polluted") else 0.0
    compliance = round(0.6 * sel_ratio + 0.4 * clean, 4)
    details["compliance"] = {
        "score": compliance, "sel_ratio": sel_ratio,
        "clean": bool(clean), "hits": poll.get("hits", []),
        "sel_present": poll.get("sel_present", {}),
    }

    quality_score = _w(
        (round(float(ai_score), 4), QUALITY_W["ai"]),
        (flu, QUALITY_W["fluency"]),
        (coh, QUALITY_W["cohesion"]),
        (dlg, QUALITY_W["dialogue"]),
        (compliance, QUALITY_W["compliance"]),
    )

    overall = round(0.5 * intent_score + 0.5 * quality_score, 4)
    return {
        "intent_score": intent_score,
        "quality_score": quality_score,
        "overall": overall,
        "polluted": bool(poll.get("polluted")),
        "details": details,
    }


# ════════════════════════════════════════════════════════════════════
# 落盘：正文/章纲/评分 → 主系统书结构
# ════════════════════════════════════════════════════════════════════

async def finalize_chapter(
    book_root: str | Path,
    arc_id: str,
    *,
    chapter_idx: int | None = None,
    force: bool = False,
    chapter_num: int | None = None,
    skip_compliance: bool = False,
) -> dict[str, Any]:
    """把当前章 l5 正文落盘到主系统书结构，并计算双评分写入审查报告。

    - 大纲/第NNNN章-章纲.md      （l3 title/core/beats）
    - AI生成/第NNNN章.md          （l5 正文草稿）
    - 审查报告/第NNNN章-评分.json  （双评分 + 污染 + 生成 prompt 详情）
    记录进 arc.chapters；book 全局章号递增（arcs.json.next_chapter_num）。

    v6.4 重生成重存：force=True 跳过「防重复落盘」守卫并复用已有章号（或 chapter_num
    指定），替换 arc.chapters 对应条目、不递增 next_chapter_num——用于修复已落盘章
    （如段落开头单调重生成后重新保存）。
    """
    book_root = Path(book_root)
    arcs = load_arcs(book_root)
    arc = _find_arc(arcs, arc_id)
    if not arc:
        return {"ok": False, "error": f"情节不存在：{arc_id}"}
    state = arc.get("state") or {}
    levels = state.get("levels") or {}
    l5 = str(levels.get("l5", {}).get("text") or "").strip()
    if not l5:
        return {"ok": False, "error": "本情节还没有正文（l5），先生成正文再落盘"}
    l3d = levels.get("l3", {}).get("data") or {}
    chs = (l3d.get("chapters") if isinstance(l3d, dict) else None) or []
    idx = (int(chapter_idx) if chapter_idx is not None
           else int(state.get("active_chapter") or 0))
    if chs:
        idx = max(0, min(idx, len(chs) - 1))
        ch = chs[idx] if isinstance(chs[idx], dict) else {}
    else:
        ch = l3d if isinstance(l3d, dict) else {}
    title = str(ch.get("title") or "").strip() or f"第{idx + 1}章"
    # 剥掉 l3 标题自带的「第X章」前缀（l3 prompt 要求 title 带章号，落盘只要纯标题）
    title = re.sub(r"^第\s*\d+\s*章[\s：:、．.，,]?", "", title).strip() or title
    core = str(ch.get("core") or "").strip()
    beats = ch.get("beats") or []

    # 防重复落盘：同一章 idx 已写过 → 拒绝（重写需先切换/重生成章节；v6.4 force 重存）
    finalized = arc.setdefault("finalized", {})
    if str(idx) in finalized and not force:
        return {"ok": False, "error": f"第 {idx + 1} 章已落盘为第 {finalized[str(idx)]:04d} 章，"
                                      "请切换/重新生成该章后再保存"}

    elements = load_elements(book_root)
    # v6.5 片段扩写：不做元素选择，跳过元素合规（否则未选元素出现会被误判污染）
    if skip_compliance:
        elements = {"characters": [], "items": [], "settings": []}
    intent_text = _build_intent_text(state, idx)
    score = await score_chapter(l5, intent_text, arc, elements)

    # v6.4：force 重存复用已有章号；显式 chapter_num 覆盖；否则按全局章号分配
    if chapter_num is not None:
        num = int(chapter_num)
    elif force and str(idx) in finalized:
        num = int(finalized[str(idx)])
    else:
        num = int(arcs.get("next_chapter_num") or 1)
    nn = f"第{num:04d}章"
    (book_root / "大纲").mkdir(parents=True, exist_ok=True)
    (book_root / "AI生成").mkdir(parents=True, exist_ok=True)
    (book_root / "审查报告").mkdir(parents=True, exist_ok=True)

    # 章纲
    outline = [f"# {title}", ""]
    if core:
        outline += ["## 一句话核心", core, ""]
    if isinstance(beats, list) and beats:
        outline += ["## 关键事件拍", ""]
        outline += [f"- {str(b).strip()}" for b in beats if str(b or "").strip()]
    (book_root / "大纲" / f"{nn}-章纲.md").write_text(
        "\n".join(outline), encoding="utf-8")

    # 正文草稿
    (book_root / "AI生成" / f"{nn}.md").write_text(l5, encoding="utf-8")

    # 评分报告
    report = {
        "chapter": num,
        "title": title,
        "arc_id": arc.get("id"),
        "arc_name": arc.get("name"),
        "intent_text": intent_text,
        "scores": score,
        "l5_prompt": str(levels.get("l5", {}).get("prompt") or ""),
        "created_at": _now(),
    }
    (book_root / "审查报告" / f"{nn}-评分.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    fresh = chapter_num is None and not (force and str(idx) in finalized)
    if fresh:
        arcs["next_chapter_num"] = num + 1
    arc["finalized"][str(idx)] = num
    entry = {
        "num": num,
        "title": title,
        "core": core,
        "intent_score": score["intent_score"],
        "quality_score": score["quality_score"],
        "overall": score["overall"],
        "polluted": score["polluted"],
        "text": l5,
    }
    chapters = arc.setdefault("chapters", [])
    # v6.4：force 重存 → 替换同章号旧条目（避免重复落盘 + 不同步）
    existing_i = next((i for i, c in enumerate(chapters)
                       if int(str(c.get("num", -1))) == num), None)
    if existing_i is not None:
        chapters[existing_i] = entry
    else:
        chapters.append(entry)
    if arc.get("status") == "draft":
        arc["status"] = "active"
    save_arcs(book_root, arcs)

    # 【Phase C·反馈闭环】本弧若用了模板，把落盘综合分回写模板 avg_quality（静默失败）
    tpl_id = str((state.get("template") or {}).get("id") or "").strip()
    if tpl_id:
        try:
            from .plot_library import get_plot_template_library
            get_plot_template_library().bump_usage(tpl_id, quality=float(score["overall"]))
        except Exception:  # noqa: BLE001
            pass

    return {
        "ok": True,
        "chapter": num,
        "title": title,
        "files": {
            "outline": f"大纲/{nn}-章纲.md",
            "prose": f"AI生成/{nn}.md",
            "report": f"审查报告/{nn}-评分.json",
        },
        "scores": score,
    }


def _now() -> str:
    import datetime
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


# ════════════════════════════════════════════════════════════════════
# 灵感对话（移植自灵感工坊 app.py，三机制 + 记忆提炼 + 编辑建议 + 建卡建议）
# 数据源 = 当前书 elements.json / arcs.json；LLM 用主系统 llm_client
# ════════════════════════════════════════════════════════════════════
_CONTEXT_LIMIT = 1_000_000


def _est_tokens(text: str) -> int:
    if not text:
        return 0
    cjk = sum(1 for ch in text if "一" <= ch <= "鿿")
    other = len(text) - cjk
    return int(cjk * 1.0 + other * 0.6)


def _fit_messages(system: str, history: list[dict], budget: int = _CONTEXT_LIMIT) -> list[dict]:
    msgs = [{"role": "system", "content": system}]
    used = _est_tokens(system)
    kept = []
    for m in reversed(history):
        t = _est_tokens(str(m.get("content") or ""))
        if used + t > budget:
            break
        kept.append(m)
        used += t
    kept.reverse()
    msgs.extend(kept)
    return msgs


def _inspire_base_system(ctx_name: str, ctx_desc: str, ctx_kind: str,
                         ctx_fields: list | None = None, ctx_relations: list | None = None,
                         rel_name_of: dict | None = None) -> str:
    ctx_line = ""
    if ctx_name:
        kind_label = {"arc": "情节", "s": "设定", "c": "角色", "i": "物品"}.get(ctx_kind, "要素")
        fields_str = ""
        if ctx_fields:
            lines = [f"{f.get('name','')}: {f.get('value','')}" for f in ctx_fields
                     if isinstance(f, dict) and (f.get("name") or f.get("value"))]
            if lines:
                fields_str = "\n".join("  - " + x for x in lines)
        rels_str = ""
        if ctx_relations:
            lines = []
            for r in ctx_relations:
                if isinstance(r, dict) and r.get("to_id"):
                    target = (rel_name_of or {}).get(r["to_id"]) or r.get("to_id")
                    lines.append(f"  - {r.get('name','关联')} → {target} ({r.get('mult','')})")
            if lines:
                rels_str = "\n".join(lines)
        ctx_line = (
            f"\n【当前讨论对象】{kind_label}：{ctx_name}\n"
            f"【对象描述】{ctx_desc or '（无）'}\n"
            + (f"【对象字段】\n{fields_str}\n" if fields_str else "")
            + (f"【对象已有关系】\n{rels_str}\n（讨论灵感时可考虑深化或补全这些关系）\n" if rels_str else "")
            + "讨论必须紧紧围绕这个对象，产出的灵感要落到它身上。"
        )
    return (
        "你是资深小说编辑兼灵感伙伴，正在和作者**对话讨论**剧情、设定、灵感。"
        "你不是在写小说，而是陪作者聊创作：给剧情设计思路、指出问题、讨论可能性、"
        "把模糊念头磨成具体可用的设定/剧情点。"
        "**绝不扩写小说正文**——不写场景描写、不写完整段落、不替作者续写片段。"
        + ctx_line +
        "\n\n回复是聊天口吻，说人话，像同行作者边聊边想。直接给想法，通常 200-600 字，"
        "展开 1-3 个可用方向；给示例一两句示意即可，不要整段写出来。"
        "末尾可以问一个问题推动下一步。"
    )


def _inspire_mech_system(mech: str) -> str:
    if mech == "seed":
        return ("【当前机制：种子推演】从「一个点」按因果链条展开——你给一个种子"
                "（一句话设定/画面/角色特质），逐层追问「那会怎样→谁受益谁受害→藏着的真相或导火索」，"
                "把每层结果说成具体可用的设定/剧情点。适合：有灵感没展开 / 新书起点。"
                "用讨论口吻把结果说成设定/剧情点子，不要扩写成小说正文。")
    if mech == "analog":
        return ("【当前机制：类比迁移】把真实世界/其他领域的成熟结构（官场/军制/医经/商帮/宗教/镖局/漕运/科举…）"
                "的骨架移植到当前要素——抄内在逻辑（晋升路径/等级名目/规则约束/资源分配/上位者/普通人处境），"
                "不抄名字，给出映射表和由此长出的设定。适合：搭设定体系 / 要真实感。"
                "用讨论口吻把结果说成设定/剧情点子，不要扩写成小说正文。")
    if mech == "invert":
        return ("【当前机制：约束反转】先列出当前题材被写烂的常见套路，逐条反转，"
                "再找出「还没人写过的空白」，生成反套路的新方向，反转要给出具体的样子。"
                "适合：大纲卡住 / 反套路。"
                "用讨论口吻把结果说成设定/剧情点子，不要扩写成小说正文。")
    return ("【当前机制：自由对话】不套任何预设框架，直接围绕素材卡/书内设定/你抛的想法聊——"
            "提新点子、指漏洞、补细节、头脑风暴，想到什么聊什么。适合：泛讨论 / 兜底。"
            "用讨论口吻说话，不要扩写成小说正文。")


def _inspire_fixed_rules_block() -> str:
    """灵感工坊精简写作规则（2026-08-10 深度瘦身）：合并 AI味禁令/baseline_guard/l5
    中与「对话/对白/段落示范」相关的规则，去重为一份硬约束。

    删掉 l1-l4 阶梯规则与 baseline_guard 正文连载规则（开头进场景/结尾停动作/
    场景公式/动作描写）——那些是「写整章正文」用的，灵感工坊是讨论灵感的对话助手，
    不适用，只会稀释真正要紧的规则并相互重复。"""
    return (
        "\n\n【写作规则·硬约束】这些规则**仅在你需要给出对白/段落示例时**适用；"
        "日常讨论剧情/设定/灵感是对话口吻，不适用正文写作规则，也不要因这些规则去写正文。\n"
        "你给出的对白样例、段落示范必须遵守：\n"
        "①禁止防御性写作：不解释、不周全、不铺垫、不辩护——读者能懂的不解释、该猜的留白；"
        "不把一件事的正反/来龙去脉全写尽；不写分析性旁白/总结性升华（「这说明…」「说白了…」「说它弱吧…说它强吧…」）。\n"
        "②禁止破折号「——」与省略号连用「……」；需要补充说明用逗号、需要转折直接句号断句。\n"
        "③禁止判断句：不用「不是……而是……」「不是……是……」、不用「……是……的」强调结构、不用「并非/绝非」——一律直接陈述。\n"
        "④禁止 Markdown 标记：正文/回复是小说文本，不出现 **、*、# 等语法。\n"
        "⑤对白：一律用中文弯引号“”包裹并写明说话人；对白直接简短、一句话说一件事，少用「吧呢嘛啊」语气词；"
        "不逐轮配「动作+神态+感官」装饰（「他开口，声音裹着凉意」），对白之间只写即时反应或直接接续。\n"
        "⑥不注水：过程性动作一句话带过；段落开头多样化（连续段落不以同一人物名开头）；"
        "细节必须有信息量（推动情节/塑造人物），无推进的细节/物件清单一律不写；不排比、不流水账、不重复叙述。"
        "（注：不注水指正文/对白不靠空话凑数；灵感讨论的回复要展开充分，不要因忌注水而写短。）\n"
        "⑦具体设定宁缺毋错：不虚构素材/上下文之外的人物身份、职业、背景、情节。"
    )


def _inspire_memory_block(book_root: str | Path) -> str:
    items = load_memory(book_root)
    if not items:
        return ""
    lines = "\n".join(f"- {x['text']}" for x in items)
    return f"\n\n【长期记忆（你记得的用户信息/偏好/已确立设定，务必引用）】\n{lines}"


def _inspire_cards_overview(book_root: str | Path, rel_name_of: dict) -> str:
    """素材卡总览注入（2026-08-11）：对话自动知道左侧所有角色/物品/设定卡及其关系，
    不用手动点选卡片。只列 name + 关系，避免过长。"""
    elements = load_elements(book_root)
    lines = []
    for kind, label in (("characters", "角色"), ("items", "物品"), ("settings", "设定"), ("maps", "地图")):
        for e in elements.get(kind, []):
            rels = []
            for r in (e.get("relations") or []):
                target = (rel_name_of or {}).get(r.get("to_id")) or r.get("to_id")
                rels.append(f"{r.get('name', '关联')}→{target}")
            rel_str = ("；" + "、".join(rels)) if rels else ""
            lines.append(f"- {label}·{e.get('name', '')}{rel_str}")
    if not lines:
        return ""
    return ("\n\n【素材卡总览】（左侧素材卡已确立的内容，讨论/建议必须符合，不得与这些设定矛盾）\n"
            + "\n".join(lines))


async def _inspire_auto_extract_memory(user_msg: str, cfg_name: str, book_root: str | Path) -> list[str]:
    from .llm_client import chat_json
    prompt = (
        "你是记忆管理器。判断用户消息里有没有「值得长期记住的创作设定/世界观/角色特质/用户偏好」，"
        "有则提取成简洁条目，没有则返回空数组。只记稳定设定，不记一次性提问。\n"
        "输出 JSON：{\"memories\":[\"条目1\"]}\n"
        f"用户消息：\n{user_msg[:3000]}"
    )
    try:
        r = await chat_json(system="", user=prompt, call_type="inspire_memory", max_tokens=500)
        data = r.get("data") or {}
        return [str(x).strip() for x in data.get("memories", []) if str(x).strip()]
    except Exception:
        return []


async def _inspire_auto_suggest_edits(user_msg: str, ctx: dict, book_root: str | Path) -> list[dict]:
    from .llm_client import chat_json
    name = ctx.get("name", "")
    if not name:
        return []
    fields_str = "、".join(f"{f.get('name','')}={f.get('value','')}" for f in (ctx.get("fields") or []) if f.get("name"))
    rels_str = "；".join(f"{r.get('name','关联')}→{r.get('to_id','?')}({r.get('mult','')})"
                         for r in (ctx.get("relations") or []) if r.get("to_id"))
    edit_id = str(ctx.get("id", ""))
    sys_p = "你是程序。只输出JSON，禁止任何文字。"
    user_p = (
        f"卡:{name}|描述:{ctx.get('desc') or '无'}|字段:{fields_str or '无'}|关系:{rels_str or '无'}\n"
        f"用户请求改进此卡。输出:{{\"edits\":[{{\"id\":\"{edit_id}\","
        "\"desc\":\"改后完整描述或null\",\"fields\":[{\"name\":\"改后字段名\",\"value\":\"新值\"}],"
        "\"relations\":[{\"to_kind\":\"i\",\"to_id\":\"目标id\",\"name\":\"关系名\",\"mult\":\"1:1\"}],"
        "\"reason\":\"理由\"}]}}\n若不改进输出 {\"edits\":[]}"
    )
    try:
        r = await chat_json(system=sys_p, user=user_p, call_type="inspire_edits", max_tokens=800)
        data = r.get("data") or {}
        edits = []
        for e in data.get("edits", []) or []:
            if isinstance(e, dict):
                edits.append({
                    "kind": e.get("kind") or ctx.get("kind", ""),
                    "id": e.get("id") or ctx.get("id", ""),
                    "card_name": e.get("card_name") or name,
                    "desc": e.get("desc"),
                    "fields": e.get("fields"),
                    "relations": e.get("relations"),
                    "reason": e.get("reason") or "",
                })
        return edits[:3]
    except Exception:
        return []


async def _inspire_auto_suggest_cards(user_msg: str, book_root: str | Path) -> list[dict]:
    """自动卡片建议（2026-08-11）：对话中出现的新角色/物品/设定 → 建议建卡进候选审批。

    不再依赖「建卡」关键词：每次对话由 LLM 判断是否有值得入库的新内容，
    参考已有素材卡去重（同名不重复建议）。"""
    from .llm_client import chat_json
    existing = set()
    elements = load_elements(book_root)
    for kind in ("characters", "items", "settings", "maps"):
        for e in elements.get(kind, []):
            existing.add(str(e.get("name") or "").strip())
    sys_p = "你是程序。只输出JSON，禁止任何文字。"
    user_p = (
        "从下面的对话内容判断：有没有值得存入素材库的**新**角色/物品/设定"
        "（对话里出现、有明确名字和特质、且不在已有列表里的）。"
        "有则提取为卡片，没有则输出空数组。不要把泛泛的讨论当卡片。\n"
        '{"cards":[{"kind":"c|s|i","name":"名称","desc":"关键描述",'
        '"fields":[{"name":"字段名","value":"值"}],"reason":"提取理由"}]}\n'
        "kind: c=角色 s=设定 i=物品。最多 2 个；没有就 {\"cards\":[]}\n\n"
        f"已有素材：{'、'.join(sorted(existing)) if existing else '（暂无）'}\n\n"
        f"对话内容：\n{user_msg[:3000]}"
    )
    try:
        r = await chat_json(system=sys_p, user=user_p, call_type="inspire_cards", max_tokens=800)
        data = r.get("data") or {}
        cards = []
        for c in data.get("cards", []) or []:
            if not isinstance(c, dict):
                continue
            name = str(c.get("name") or "").strip()
            if not name or name in existing:  # 去重：已有卡不再建议
                continue
            cards.append({
                "kind": c.get("kind", "s"), "name": name, "desc": c.get("desc", ""),
                "fields": c.get("fields", []) or [], "reason": c.get("reason") or "",
            })
        return cards[:2]
    except Exception:
        return []


async def inspire_chat(book_root: str | Path, messages: list[dict], mech: str = "seed",
                       ctx: dict | None = None) -> dict[str, Any]:
    """灵感对话（三机制）。ctx = 当前素材卡 {kind,name,desc,fields,relations}。
    记忆提炼 + 建卡建议 → add_pending（候选制）；编辑建议返回给前端等用户同意。
    返回 {ok, reply, mech, preset, suggested_edits, pending_count}"""
    from .llm_client import chat_completion

    book_root = Path(book_root)
    _wb_ctx(book_root, step="inspire_chat")
    ctx = ctx or {}
    # id→name 映射（关系目标取名）
    rel_name_of = {}
    elements = load_elements(book_root)
    for kind in ("characters", "items", "settings", "maps"):
        for e in elements.get(kind, []):
            if e.get("id"):
                rel_name_of[e["id"]] = e.get("name", e["id"])
    try:
        arcs = load_arcs(book_root)
        for a in arcs.get("arcs", []):
            if a.get("id"):
                rel_name_of[a["id"]] = a.get("name", a["id"])
    except Exception:
        pass

    system = (_inspire_base_system(ctx.get("name", ""), ctx.get("desc", ""), ctx.get("kind", ""),
                                   ctx_fields=ctx.get("fields"), ctx_relations=ctx.get("relations"),
                                   rel_name_of=rel_name_of)
              + "\n" + _inspire_mech_system(mech)
              + _inspire_memory_block(book_root)
              + _inspire_cards_overview(book_root, rel_name_of)
              + _inspire_fixed_rules_block())

    history = []
    for m in (messages or []):
        role = "user" if m.get("role") == "user" else "assistant"
        history.append({"role": role, "content": str(m.get("content") or "")})
    if not history or history[-1]["role"] != "user":
        history.append({"role": "user", "content": "（继续讨论灵感）"})
    # 多轮上下文（修复 2026-08-10）：原来 _fit_messages 结果被丢弃、只发
    # history[-1] 一条，LLM 完全看不到前文。现在预算内保留 system+最近轮次，
    # 最后一条 user 作 user 参数，其余轮次经 chat_completion 的 history 注入。
    llm_messages = _fit_messages(system, history, budget=_CONTEXT_LIMIT)
    _prior = [m for m in llm_messages if m.get("role") != "system"]
    if _prior and _prior[-1].get("role") == "user":
        _last_user = str(_prior[-1].get("content") or "")
        _prior = _prior[:-1]
    else:
        _last_user = str(history[-1].get("content") or "")

    last_user = next((m.get("content") or "") for m in reversed(history) if m["role"] == "user")

    async def _do_reply():
        # 完整多轮：system + history(前文轮次) + user(最后一条)
        r = await chat_completion(system=system, user=_last_user, history=_prior,
                                  call_type="inspire_chat", max_tokens=8192)
        return r.get("content") or "" if not r.get("error") else ""

    async def _do_memory():
        return await _inspire_auto_extract_memory(last_user, "", book_root)

    async def _do_edits():
        if ctx.get("name"):
            return await _inspire_auto_suggest_edits(last_user, ctx, book_root)
        return []

    async def _do_cards():
        return await _inspire_auto_suggest_cards(last_user, book_root)

    import asyncio
    tasks = [
        asyncio.create_task(_do_reply()),
        asyncio.create_task(_do_memory()),
        asyncio.create_task(_do_edits()),
        asyncio.create_task(_do_cards()),
    ]
    reply, new_memories, suggested_edits, suggested_cards = await asyncio.gather(*tasks)

    # 输出前自动去 AI 味（用户 2026-08-10：必须输出前自动完成）——脏则自动重写，干净直出
    if reply and len(reply) > 20:
        _pol = await _ai_flavor_polish(reply, call_type="inspire_chat_auto_polish")
        reply = _pol["text"] or reply

    # 候选制：记忆 + 建卡建议进待审批池（用户同意才入库/建卡）
    try:
        for t in new_memories:
            add_pending(book_root, {"type": "mem", "text": t})
    except Exception:
        pass
    try:
        for c in suggested_cards:
            add_pending(book_root, {"type": "card", "kind": c.get("kind", "s"), "name": c.get("name", ""),
                                    "desc": c.get("desc", ""), "fields": c.get("fields", []) or [],
                                    "reason": c.get("reason", "")})
    except Exception:
        pass

    try:
        from .config import SETTINGS as _s
        preset = getattr(_s, "model", "") or ""
    except Exception:
        preset = ""
    return {"ok": True, "reply": reply, "mech": mech, "preset": preset,
            "suggested_edits": suggested_edits, "pending_count": len(load_pending(book_root))}


async def extract_cards_from_text(book_root: str | Path, text: str) -> dict[str, Any]:
    """从文本中提取设定/元素/记忆，生成待审核卡片。

    返回 {ok, cards: [{type, kind?, name?, desc?, text?}], pending_count}
    """
    from .llm_client import chat_json

    system = (
        '你是「内容提取专家」。从用户提供的对话文本中，提取所有关于故事设定、角色、物品、记忆的信息。\n'
        '返回 JSON：{"cards":[{"type":"card","kind":"settings|characters|items|locations","name":"...","desc":"...","fields":[]},'
        '{"type":"mem","text":"..."}]}\n'
        '【kind 分类规则】必须严格按以下规则分类：\n'
        '- kind="characters"：人物/角色（主角、配角、反派、NPC等有名字的人物）\n'
        '- kind="items"：物品/道具（武器、法宝、道具等具体物件）\n'
        '- kind="locations"：地点/场景（如茌山一中、城隍庙、教室、城市名等具体地点）\n'
        '- kind="settings"：设定（以下全部归入此类）：\n'
        '  · 世界观/时代背景（如现代都市、修仙世界）\n'
        '  · 时间设定（如时间循环、七天周期）\n'
        '  · 力量体系/规则（如克苏鲁元素、灵气复苏）\n'
        '  · 风格/基调（如中式恐怖、悬疑惊悚）\n'
        '  · 核心设定/概念（如解离性遗忘、纸人教室）\n'
        '- type=mem：记忆/偏好（以下全部归入此类）：\n'
        '  · 写作风格偏好（如对白要短、文风偏严肃）\n'
        '  · 故事约定/规则（如不能出现现代科技、主角不能死）\n'
        '  · 创作决策（如第一人称叙事、章末留悬念）\n'
        '  · 用户明确表达的喜好或要求\n'
        '规则：\n'
        '- name 用简洁中文命名\n'
        '- desc 是完整描述\n'
        '- 每个独立信息点一张卡片\n'
        '- 没有相关信息就返回空数组\n'
        '- 不要编造文本中没有的信息'
    )

    user = f"请从以下对话文本中提取设定、角色、物品和记忆：\n\n{text[:4000]}"

    try:
        r = await chat_json(system=system, user=user, call_type="extract_cards", temperature=0.3, max_tokens=2000)
        data = r.get("data") or {}
        cards = data.get("cards") or []
    except Exception as exc:
        return {"ok": False, "error": str(exc)[:200], "cards": [], "pending_count": 0}

    # 过滤掉无效卡片，添加到 pending 队列
    valid_cards = [c for c in cards if c and isinstance(c, dict) and c.get("type")]
    for c in valid_cards:
        try:
            add_pending(book_root, c)
        except Exception:
            pass

    return {"ok": True, "cards": valid_cards, "pending_count": len(load_pending(book_root))}


async def _ai_flavor_polish(text: str, *, call_type: str = "ai_flavor_polish",
                            threshold: float = 0.65) -> dict[str, Any]:
    """输出前自动去 AI 味（2026-08-10）：审阅 → 脏则带反馈+禁令重写 → 再审阅。

    复用现有防线：review_ai_flavor_standalone 审阅 + build_ai_flavor_feedback 反馈 +
    ai_flavor_ban_block（含最高优先的禁止防御性写作）改写。
    Returns {text, before, after, clean}；clean=True 时 text=原文本；重写失败回退原文本。"""
    from .ai_flavor import (review_ai_flavor_standalone, build_ai_flavor_feedback,
                            ai_flavor_ban_block)
    from .llm_client import chat_completion

    before = await review_ai_flavor_standalone(str(text))
    score = before.get("score")
    findings = before.get("findings") or []
    high = [f for f in findings if f.get("severity") == "high"]
    # v7.2.5：破折号/Markdown 确定性门槛——即使 LLM 审阅没判脏，破折号密集也必触发重写
    punct = (before.get("punct") or {}).get("punct_score") or 1.0
    if not high and punct >= 0.7 and (score is None or score >= threshold):
        return {"text": str(text), "before": before, "after": before, "clean": True}

    fb = build_ai_flavor_feedback(high if high else findings)
    if not fb:
        fb = "按上述 AI 味禁令逐处修正：删解释性旁白、删对白装饰、删氛围堆砌、删总结升华、删破折号「——」、删 Markdown 标记，留白克制。"
    sys_p = (
        "你是苛刻的中文网文编辑。任务：把一段 AI 味重的文本改写成自然、克制、可信的小说叙述。\n"
        + ai_flavor_ban_block(standalone=True)
        + "\n\n改写要求：只去 AI 味，不动情节与人物；逐处落实审阅意见；"
        "保留原有对白与叙述信息，删解释、删装饰、删总结、**删掉所有破折号「——」与 Markdown 标记（**、*）**，留白克制；"
        "直接输出改写后的完整文本。"
    )
    usr_p = (
        "【待改写文本】\n" + str(text)
        + "\n\n【审阅意见】\n" + fb
        + "\n\n直接输出改写后的完整文本，不要任何解释、不要 Markdown 代码块。"
    )
    r = await chat_completion(system=sys_p, user=usr_p, temperature=0.6,
                              max_tokens=8192, call_type=call_type)
    out = (r.get("content") or "").strip()
    if r.get("error") or not out:
        return {"text": str(text), "before": before, "after": before, "clean": False,
                "error": str(r.get("error") or "重写失败")}
    after = await review_ai_flavor_standalone(out)
    # 【v7.2.5】破折号/Markdown 顽固时针对性再重写（最多 1 轮）：LLM 对「——」有惯性，
    # 一次重写可能残留；明确指令逐字删除，保证破折号/Markdown 清干净。
    for _ in range(1):
        p2 = (after.get("punct") or {}).get("punct_score") or 1.0
        if p2 >= 0.7:
            break
        usr_p2 = (
            "【当前文本】\n" + out
            + "\n\n上一版仍含破折号「——」/省略号/Markdown 标记。请**删除所有破折号、省略号与 "
            "Markdown 标记**（改用逗号、句号或直接接续），其余内容一字不动，直接输出修正后的完整文本。"
        )
        r2 = await chat_completion(system=sys_p, user=usr_p2, temperature=0.5,
                                   max_tokens=8192, call_type=call_type)
        out2 = (r2.get("content") or "").strip()
        if r2.get("error") or not out2:
            break
        out = out2
        after = await review_ai_flavor_standalone(out)
    return {"text": out, "before": before, "after": after, "clean": False}


async def inspire_depollute(text: str, book_root: str | Path | None = None,
                            ctx: dict | None = None) -> dict[str, Any]:
    """一键去 AI 味（手动兜底，复用 _ai_flavor_polish）。
    Returns {ok, rewritten, before, after, clean}。"""
    if not text or len(str(text).strip()) < 20:
        return {"ok": False, "error": "文本过短"}
    _wb_ctx(book_root, step="inspire_depollute")
    res = await _ai_flavor_polish(str(text), call_type="inspire_depollute")
    return {"ok": True, "rewritten": res["text"], "before": res["before"],
            "after": res["after"], "clean": res["clean"]}
