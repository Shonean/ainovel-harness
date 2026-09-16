"""量产路线图（roadmap）——书级规划层：幕 / 弧卡 / 伏笔台账。

落盘 `<book>/.ainovel/roadmap.json`，与 arcs.json 运行结构解耦：
- 草案（draft）可由 LLM 生成、助手改、逐卡重生成；
- 冻结（frozen）通过校验后成为批量生成的弧卡队列与 l2 上下文来源；
- 不改弧/章节落盘结构（arcs.json 仍由生成流程单独维护）。

模型：
  {schema_version, status: draft|frozen, version, logline{want,obstacle,cost,ending},
   acts:[{id,title,goal}], arcs:[{id,index,act_id,title,role,chapters,l1,l2,
   characters[],elements[],foreshadow_open[],foreshadow_close[],status}],
   foreshadow:[{id,name,open_arc,close_arc,status}], target_chapters,
   chapters_per_arc, generated_at, frozen_at}
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROLES = ("铺垫", "升级", "转折", "高潮", "收束")
_VALID_STATUS = ("draft", "frozen")


def _roadmap_path(book_root: str | Path) -> Path:
    return Path(book_root) / ".ainovel" / "roadmap.json"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_roadmap(book_root: str | Path) -> dict[str, Any] | None:
    p = _roadmap_path(book_root)
    if not p.is_file():
        return None
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else None
    except (OSError, json.JSONDecodeError):
        return None


def save_roadmap(book_root: str | Path, roadmap: dict[str, Any]) -> None:
    p = _roadmap_path(book_root)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(roadmap, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, p)


def _norm_logline(raw: Any) -> dict[str, str]:
    d = raw if isinstance(raw, dict) else {}
    return {
        "want": str(d.get("want") or "").strip(),
        "obstacle": str(d.get("obstacle") or "").strip(),
        "cost": str(d.get("cost") or "").strip(),
        "ending": str(d.get("ending") or "").strip(),
    }


def _infer_role(idx: int, total: int) -> str:
    if total <= 0:
        return "铺垫"
    frac = idx / total
    if frac < 0.25:
        return "铺垫"
    if frac < 0.5:
        return "升级"
    if frac < 0.75:
        return "转折"
    if frac < 0.9:
        return "高潮"
    return "收束"


def _norm_acts(raw_acts: Any) -> list[dict[str, Any]]:
    acts: list[dict[str, Any]] = []
    for i, a in enumerate(raw_acts or []):
        if not isinstance(a, dict):
            continue
        acts.append({
            "id": f"act{i + 1}",
            "title": str(a.get("title") or f"第{i + 1}幕").strip(),
            "goal": str(a.get("goal") or "").strip(),
        })
    return acts


def _norm_arcs(
    raw_arcs: Any, acts: list[dict[str, Any]], n_chapters_per_arc: int,
) -> list[dict[str, Any]]:
    raw = [a for a in (raw_arcs or []) if isinstance(a, dict)]
    total = len(raw)
    arcs: list[dict[str, Any]] = []
    for i, a in enumerate(raw):
        try:
            act_no = int(a.get("act") or 1)
        except (TypeError, ValueError):
            act_no = 1
        act_no = min(max(act_no, 1), max(1, len(acts)))
        role = str(a.get("role") or "").strip()
        if role not in ROLES:
            role = _infer_role(i, total)
        try:
            chapters = int(a.get("chapters") or n_chapters_per_arc)
        except (TypeError, ValueError):
            chapters = n_chapters_per_arc
        arcs.append({
            "id": f"a{i + 1}",
            "index": i + 1,
            "act_id": acts[act_no - 1]["id"] if acts else "act1",
            "title": str(a.get("title") or f"情节 {i + 1}").strip(),
            "role": role,
            "chapters": max(1, chapters),
            "l1": str(a.get("l1") or "").strip(),
            "l2": str(a.get("l2") or "").strip(),
            "characters": [str(x).strip() for x in (a.get("characters") or []) if str(x).strip()],
            "elements": [str(x).strip() for x in (a.get("elements") or []) if str(x).strip()],
            "foreshadow_open": [str(x).strip() for x in (a.get("foreshadow_open") or []) if str(x).strip()],
            "foreshadow_close": [str(x).strip() for x in (a.get("foreshadow_close") or []) if str(x).strip()],
            "status": "planned",
        })
    return arcs


def _build_foreshadow(arcs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """由弧卡的 open/close 名单构建伏笔台账（同名合并，后出现优先）。"""
    ledger: dict[str, dict[str, Any]] = {}

    def _entry(name: str) -> dict[str, Any]:
        if name not in ledger:
            ledger[name] = {
                "id": f"f{len(ledger) + 1}", "name": name,
                "open_arc": "", "close_arc": "", "status": "open",
            }
        return ledger[name]

    for a in arcs:
        for nm in a.get("foreshadow_open") or []:
            e = _entry(nm)
            if not e["open_arc"]:
                e["open_arc"] = a["id"]
    for a in arcs:
        for nm in a.get("foreshadow_close") or []:
            e = _entry(nm)
            e["close_arc"] = a["id"]
            e["status"] = "closed"
    return list(ledger.values())


def validate_roadmap(
    roadmap: dict[str, Any] | None, elements: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """校验路线图：幕覆盖 / 弧序 / 伏笔引用与回收 / 元素引用。

    Returns {ok, errors[], warnings[]}；errors 非空时不允许冻结。
    """
    errors: list[str] = []
    warnings: list[str] = []
    if not isinstance(roadmap, dict):
        return {"ok": False, "errors": ["路线图不存在"], "warnings": []}
    acts = roadmap.get("acts") or []
    arcs = roadmap.get("arcs") or []
    if not acts:
        errors.append("路线图没有幕")
    if not arcs:
        errors.append("路线图没有弧卡")

    act_ids = {a.get("id") for a in acts}
    arc_ids = [a.get("id") for a in arcs]
    if len(arc_ids) != len(set(arc_ids)):
        errors.append("弧卡 id 重复")

    covered: set[str] = set()
    for a in arcs:
        title = a.get("title") or a.get("id")
        if a.get("act_id") not in act_ids:
            errors.append(f"弧卡「{title}」不属于任何幕")
        else:
            covered.add(a.get("act_id"))
        if not (a.get("chapters") or 0) > 0:
            errors.append(f"弧卡「{title}」章数必须大于 0")
        if not a.get("l1"):
            errors.append(f"弧卡「{title}」缺少一句话剧情")
        if not a.get("l2"):
            warnings.append(f"弧卡「{title}」缺少情节线（l2）")

    for act in acts:
        if act.get("id") not in covered:
            warnings.append(f"幕「{act.get('title')}」没有弧卡")

    idx = [a.get("index") for a in arcs]
    if idx and idx != sorted(idx):
        errors.append("弧卡顺序错乱（index 非递增）")

    pos = {a.get("id"): i for i, a in enumerate(arcs)}
    for f in roadmap.get("foreshadow") or []:
        name = f.get("name") or f.get("id")
        o, c = f.get("open_arc") or "", f.get("close_arc") or ""
        if o and o not in arc_ids:
            errors.append(f"伏笔「{name}」开启弧不存在")
        if c and c not in arc_ids:
            errors.append(f"伏笔「{name}」回收弧不存在")
        if o and c and pos.get(c, 0) < pos.get(o, 0):
            errors.append(f"伏笔「{name}」回收早于开启")
        if o and not c:
            errors.append(f"伏笔「{name}」悬空未回收")
        if c and not o:
            warnings.append(f"伏笔「{name}」只有回收没有开启")

    if elements:
        known = {
            str(e.get("name"))
            for coll in ("characters", "items", "settings")
            for e in (elements.get(coll) or [])
            if isinstance(e, dict)
        }
        for a in arcs:
            title = a.get("title") or a.get("id")
            for nm in (a.get("characters") or []) + (a.get("elements") or []):
                if nm and nm not in known:
                    warnings.append(f"弧卡「{title}」引用了未入库元素：{nm}")

    return {"ok": not errors, "errors": errors, "warnings": warnings}


def _build_roadmap(
    raw: dict[str, Any], *, n_chapters_per_arc: int, target_chapters: int,
    version: int = 1,
) -> dict[str, Any]:
    acts = _norm_acts(raw.get("acts"))
    arcs = _norm_arcs(raw.get("arcs"), acts, n_chapters_per_arc)
    if not acts:
        acts = [{"id": "act1", "title": "第一幕", "goal": ""}]
    return {
        "schema_version": 1,
        "status": "draft",
        "version": version,
        "logline": _norm_logline(raw.get("logline")),
        "acts": acts,
        "arcs": arcs,
        "foreshadow": _build_foreshadow(arcs),
        "target_chapters": int(target_chapters or 0),
        "chapters_per_arc": int(n_chapters_per_arc or 3),
        "generated_at": _now(),
        "frozen_at": "",
    }


_LLM_SYSTEM = (
    "你是资深网文总编剧，负责规划量产书的全书路线图。"
    "只输出严格 JSON（不要 Markdown 代码块）；所有内容为中文；"
    "与提供的设定/元素一致，不编造设定外的世界观。"
)


async def generate_roadmap(
    book_root: str | Path,
    *,
    target_chapters: int = 30,
    n_chapters_per_arc: int = 3,
    brief: str = "",
) -> dict[str, Any]:
    """按基本设定/元素生成全书路线图草案（一次 LLM 调用）。

    Returns {ok, roadmap, validation}；失败 {ok: False, error}。
    """
    from .ai_creation import load_basic_settings, load_elements
    from .derive import _tree_llm

    book_root = Path(book_root)
    s = load_basic_settings(book_root)
    els = load_elements(book_root)
    n_arcs = max(3, min(20, round(int(target_chapters or 30) / max(1, int(n_chapters_per_arc or 3)))))
    usr = (
        f"【书名】{s.get('name') or ''}\n"
        f"【题材】{s.get('genre') or ''}\n"
        f"【一句话】{s.get('one_liner') or ''}\n"
        f"【基本设定】\n{json.dumps(s, ensure_ascii=False)[:3000]}\n"
        f"【元素库】\n{json.dumps(els, ensure_ascii=False)[:3000]}\n"
        + (f"【作者补充】{brief}\n" if brief else "")
        + f"要求：把全书拆成 {n_arcs} 个情节弧（每弧 {n_chapters_per_arc} 章，共约 {target_chapters} 章），"
        "归入 3-4 幕。输出 JSON：\n"
        '{"logline": {"want": "主角要什么", "obstacle": "谁/什么挡着", "cost": "代价", "ending": "终局"},'
        ' "acts": [{"title": "第一幕 · 醒来", "goal": "这一幕要完成什么"}],'
        ' "arcs": [{"title": "情节名", "act": 1, "role": "铺垫|升级|转折|高潮|收束",'
        ' "l1": "一句话剧情（30-60字）", "l2": "情节线（起因/核心冲突/转折/结局，150-250字）",'
        ' "characters": ["已入库角色名"], "elements": ["关键物品/设定名"],'
        ' "foreshadow_open": ["本弧提出的待回收伏笔"],'
        ' "foreshadow_close": ["本弧回收的前文伏笔"]}]}\n'
        "伏笔必须跨弧：提出的伏笔要在后面的弧里回收（回收弧的 foreshadow_close 写同名）。"
    )
    try:
        raw = await _tree_llm(_LLM_SYSTEM, usr, "ai_creation_roadmap_generate", max_tokens=8000)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": "路线图生成失败：" + str(e)[:200]}

    rm = _build_roadmap(raw, n_chapters_per_arc=n_chapters_per_arc,
                        target_chapters=target_chapters, version=1)
    save_roadmap(book_root, rm)
    return {"ok": True, "roadmap": rm, "validation": validate_roadmap(rm, els)}


def freeze_roadmap(book_root: str | Path, *, force: bool = False) -> dict[str, Any]:
    """冻结路线图：先校验（悬空伏笔/幕覆盖等错误拦截）；force=True 时跳过错误。"""
    from .ai_creation import load_elements

    rm = load_roadmap(book_root)
    if not rm or not (rm.get("arcs") or []):
        return {"ok": False, "error": "没有可冻结的路线图草案"}
    v = validate_roadmap(rm, load_elements(book_root))
    if v["errors"] and not force:
        return {"ok": False, "errors": v["errors"], "warnings": v["warnings"]}
    rm["status"] = "frozen"
    rm["frozen_at"] = _now()
    save_roadmap(book_root, rm)
    return {"ok": True, "roadmap": rm, "errors": v["errors"], "warnings": v["warnings"]}


async def regenerate_arc(
    book_root: str | Path, arc_id: str, *, instruction: str = "",
) -> dict[str, Any]:
    """单卡重生成：保留 id/序号/幕，重写标题、l1/l2、元素引用与伏笔名单。"""
    from .derive import _tree_llm

    rm = load_roadmap(book_root)
    if not rm:
        return {"ok": False, "error": "路线图不存在"}
    arcs = rm.get("arcs") or []
    idx = next((i for i, a in enumerate(arcs) if a.get("id") == arc_id), -1)
    if idx < 0:
        return {"ok": False, "error": f"弧卡不存在：{arc_id}"}
    arc = arcs[idx]
    prev = arcs[idx - 1] if idx > 0 else None
    nxt = arcs[idx + 1] if idx + 1 < len(arcs) else None
    from .ai_creation import load_elements

    els = load_elements(book_root)
    usr = (
        f"【全书一句话】{json.dumps(rm.get('logline') or {}, ensure_ascii=False)}\n"
        f"【上一弧】{json.dumps(_arc_brief(prev), ensure_ascii=False)}\n"
        f"【下一弧】{json.dumps(_arc_brief(nxt), ensure_ascii=False)}\n"
        f"【当前弧卡】{json.dumps(_arc_brief(arc), ensure_ascii=False)}\n"
        f"【元素库】{json.dumps(els, ensure_ascii=False)[:2000]}\n"
        + (f"【修改要求】{instruction}\n" if instruction else "")
        + "请重写这张弧卡，保持剧情位置与幕归属不变。输出 JSON：\n"
        '{"title": "情节名", "l1": "一句话剧情", "l2": "情节线（起因/核心冲突/转折/结局）",'
        ' "characters": ["角色名"], "elements": ["物品/设定名"],'
        ' "foreshadow_open": ["本弧提出"], "foreshadow_close": ["本弧回收"]}'
    )
    try:
        raw = await _tree_llm(_LLM_SYSTEM, usr, "ai_creation_roadmap_arc_regen", max_tokens=3000)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": "弧卡重生成失败：" + str(e)[:200]}

    for key in ("title", "l1", "l2"):
        val = str(raw.get(key) or "").strip()
        if val:
            arc[key] = val
    for key in ("characters", "elements", "foreshadow_open", "foreshadow_close"):
        if isinstance(raw.get(key), list):
            arc[key] = [str(x).strip() for x in raw[key] if str(x).strip()]
    arc["status"] = "planned"
    rm["foreshadow"] = _build_foreshadow(arcs)
    rm["version"] = int(rm.get("version") or 1) + 1
    rm["status"] = "draft"
    save_roadmap(book_root, rm)
    return {"ok": True, "roadmap": rm, "arc": arc,
            "validation": validate_roadmap(rm, els)}


def patch_arc(
    book_root: str | Path, arc_id: str, fields: dict[str, Any],
) -> dict[str, Any]:
    """弧卡字段修改（助手/前端共用；不改 id/序号/幕归属）。任意修改回到草案。"""
    from .ai_creation import load_elements

    rm = load_roadmap(book_root)
    if not rm:
        return {"ok": False, "error": "路线图不存在"}
    arcs = rm.get("arcs") or []
    arc = next((a for a in arcs if a.get("id") == arc_id), None)
    if arc is None:
        return {"ok": False, "error": f"弧卡不存在：{arc_id}"}
    fields = fields or {}
    if "title" in fields and str(fields["title"]).strip():
        arc["title"] = str(fields["title"]).strip()
    if "l1" in fields:
        arc["l1"] = str(fields["l1"] or "").strip()
    if "l2" in fields:
        arc["l2"] = str(fields["l2"] or "").strip()
    if "role" in fields and str(fields["role"] or "").strip() in ROLES:
        arc["role"] = str(fields["role"]).strip()
    if "chapters" in fields:
        try:
            arc["chapters"] = max(1, int(fields["chapters"]))
        except (TypeError, ValueError):
            pass
    for key in ("characters", "elements", "foreshadow_open", "foreshadow_close"):
        if key in fields and isinstance(fields[key], list):
            arc[key] = [str(x).strip() for x in fields[key] if str(x).strip()]
    rm["foreshadow"] = _build_foreshadow(arcs)
    rm["status"] = "draft"
    rm.pop("frozen_at", None)
    rm["version"] = int(rm.get("version") or 1) + 1
    save_roadmap(book_root, rm)
    return {"ok": True, "roadmap": rm, "arc": arc,
            "validation": validate_roadmap(rm, load_elements(book_root))}


def reorder_arc(
    book_root: str | Path, arc_id: str, new_index: int,
) -> dict[str, Any]:
    """把弧卡移到 new_index（1 基），其余顺延并重排序号；回到草案。"""
    rm = load_roadmap(book_root)
    if not rm:
        return {"ok": False, "error": "路线图不存在"}
    arcs = rm.get("arcs") or []
    pos = next((i for i, a in enumerate(arcs) if a.get("id") == arc_id), -1)
    if pos < 0:
        return {"ok": False, "error": f"弧卡不存在：{arc_id}"}
    n = len(arcs)
    try:
        ni = int(new_index)
    except (TypeError, ValueError):
        ni = 1
    ni = min(max(ni, 1), n) - 1
    arc = arcs.pop(pos)
    arcs.insert(ni, arc)
    for i, a in enumerate(arcs):
        a["index"] = i + 1
    rm["arcs"] = arcs
    rm["foreshadow"] = _build_foreshadow(arcs)
    rm["status"] = "draft"
    rm.pop("frozen_at", None)
    rm["version"] = int(rm.get("version") or 1) + 1
    save_roadmap(book_root, rm)
    return {"ok": True, "roadmap": rm, "arc": arc}


async def continue_arcs(
    book_root: str | Path, *, count: int = 1,
) -> dict[str, Any]:
    """续写弧卡：在现有路线图尾部追加 count 个情节弧（追加到最后一幕）。"""
    from .derive import _tree_llm

    rm = load_roadmap(book_root)
    if not rm:
        return {"ok": False, "error": "路线图不存在"}
    arcs = rm.get("arcs") or []
    if not arcs:
        return {"ok": False, "error": "路线图没有弧卡"}
    acts = rm.get("acts") or [{"id": "act1", "title": "第一幕", "goal": ""}]
    last = arcs[-1]
    tail = [_arc_brief(a) for a in arcs[-3:]]
    usr = (
        f"【全书一句话】{json.dumps(rm.get('logline') or {}, ensure_ascii=False)}\n"
        f"【已写到】{json.dumps(tail, ensure_ascii=False)}\n"
        f"请续写 {max(1, int(count))} 个情节弧，紧接现有剧情推进，不重复已用桥段。"
        f"输出 JSON：{{\"arcs\": [{{\"title\": \"情节名\", \"role\": \"升级|转折|高潮|收束\","
        f" \"l1\": \"一句话剧情\", \"l2\": \"情节线（起因/核心冲突/转折/结局）\","
        f" \"characters\": [], \"elements\": [], \"foreshadow_open\": [], \"foreshadow_close\": []}}]}}"
    )
    try:
        raw = await _tree_llm(_LLM_SYSTEM, usr, "ai_creation_roadmap_continue", max_tokens=4000)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": "弧卡续写失败：" + str(e)[:200]}

    n0 = len(arcs)
    new_arcs = _norm_arcs(raw.get("arcs"), acts, int(rm.get("chapters_per_arc") or 3))
    for i, a in enumerate(new_arcs):
        a["id"] = f"a{n0 + i + 1}"
        a["index"] = n0 + i + 1
        a["act_id"] = last.get("act_id") or acts[-1]["id"]
    arcs.extend(new_arcs)
    rm["arcs"] = arcs
    rm["foreshadow"] = _build_foreshadow(arcs)
    rm["version"] = int(rm.get("version") or 1) + 1
    save_roadmap(book_root, rm)
    return {"ok": True, "roadmap": rm, "added": len(new_arcs)}


def _arc_brief(arc: dict[str, Any] | None) -> dict[str, Any]:
    if not arc:
        return {}
    return {
        "title": arc.get("title"), "role": arc.get("role"),
        "l1": arc.get("l1"), "l2": (str(arc.get("l2") or ""))[:200],
        "foreshadow_open": arc.get("foreshadow_open") or [],
        "foreshadow_close": arc.get("foreshadow_close") or [],
    }


def roadmap_context_for_arc(book_root: str | Path, arc_index: int) -> str:
    """给批量生成的 l2 注入上下文：仅冻结路线图生效，返回空串表示不注入。"""
    rm = load_roadmap(book_root)
    if not rm or rm.get("status") != "frozen":
        return ""
    arcs = rm.get("arcs") or []
    if not (1 <= arc_index <= len(arcs)):
        return ""
    arc = arcs[arc_index - 1]
    lg = rm.get("logline") or {}
    parts = [
        "【全书路线图】",
        f"主线：{lg.get('want', '')}｜阻碍：{lg.get('obstacle', '')}｜代价：{lg.get('cost', '')}｜终局：{lg.get('ending', '')}",
        f"本弧（第 {arc_index} 弧 · {arc.get('role', '')}）：{arc.get('title', '')}",
        "情节线：" + str(arc.get("l2") or arc.get("l1") or ""),
    ]
    if arc_index >= 2:
        prev = arcs[arc_index - 2]
        parts.append(f"上一弧：{prev.get('title', '')}（{str(prev.get('l1') or '')[:80]}）")
    if arc.get("foreshadow_open"):
        parts.append("本弧需埋设： " + "、".join(arc["foreshadow_open"]))
    if arc.get("foreshadow_close"):
        parts.append("本弧需回收： " + "、".join(arc["foreshadow_close"]))
    return "\n".join(parts)
