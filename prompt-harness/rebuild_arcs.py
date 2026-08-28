# -*- coding: utf-8 -*-
"""重建《全流程测试书》arcs.json（2026-08-13 误清后恢复）。

数据来源（均为运行态产物，未丢失）：
- workbench 日志 2026-08-12.jsonl：情节1-6 的 l1-l5 阶梯全量 LLM 输入输出
- 审查报告/第NNNN章-评分.json：已落盘章（arc_id/arc_name/title/scores）→ 情节2-5
- AI生成/第NNNN章.md：已落盘正文 → chapters[].text
- 大纲/第NNNN章-章纲.md：章纲（title/core/beats）
- basic_settings.json：情节7-10 占位弧 l1（_auto_arc_brief 确定性重建）

产出：写回 小说系统/全流程测试书/.ainovel/arcs.json（10 弧：6 完整阶梯 + 4 占位）
"""
import json
import re
import collections
from pathlib import Path

BOOK = Path(r"C:\Users\24357\Desktop\AInovel写作系统\小说系统\全流程测试书")
LOG = Path(r"C:\Users\24357\Desktop\AInovel写作系统\小说系统\ainovel-write\prompt-harness\logs\workbench"
           r"\C__Users_24357_Desktop_AInovel Harness_小说系统_全流程测试书\2026-08-12.jsonl")
OUT = BOOK / ".ainovel" / "arcs.json"

REVIEW_DIR = BOOK / "审查报告"
AIGEN_DIR = BOOK / "AI生成"
OUTLINE_DIR = BOOK / "大纲"

_LEVEL_ORDER = ("l1", "l2", "l3", "l4", "l5")


def parse_log() -> list[dict]:
    ds = []
    for line in LOG.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            ds.append(json.loads(line))
        except Exception:
            pass
    return ds


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()


def _l1_from_kf(user: str) -> str:
    m = re.search(r"极简剧情：\s*(.+?)(?:\s*【关键事实|\s*严格输出如下)", user, re.S)
    return _clean(m.group(1)) if m else ""


def _l2_from_out(out: str) -> str:
    try:
        d = json.loads(out)
        if isinstance(d, dict) and d.get("summary"):
            return _clean(d["summary"])
    except Exception:
        pass
    return _clean(out)


def _l3_from_out(out: str) -> dict | None:
    try:
        d = json.loads(out)
        if isinstance(d, dict) and isinstance(d.get("chapters"), list):
            return d
    except Exception:
        pass
    return None


def _l4_scenes_from_out(out: str) -> list | None:
    try:
        d = json.loads(out)
        if isinstance(d, dict) and isinstance(d.get("scenes"), list) and d["scenes"]:
            return d["scenes"]
    except Exception:
        pass
    return None


def _chapter_no_from_user(user: str) -> int | None:
    m = re.search(r"章节：第\s*(\d+)\s*章", user)
    return int(m.group(1)) if m else None


def _render_l4(scenes: list) -> str:
    lines = []
    for i, s in enumerate(scenes, 1):
        name = _clean(s.get("name") or "")
        env = _clean(s.get("environment") or "")
        acts = [_clean(a) for a in (s.get("actions") or [])][:3]
        lines.append(f"场景{i}｜{name}｜{env}")
        for a in acts:
            lines.append(f"  · {a}")
    return "\n".join(lines)


def build_arc_from_log(arc_id: str, entries: list[dict], name: str) -> dict:
    """从日志条目重建一条弧的 l1-l5 阶梯（best-effort，confirmed 按流程推进推断）。"""
    entries = sorted(entries, key=lambda d: d.get("seq") or 0)

    def first_of(ct: str) -> dict | None:
        got = [d for d in entries if d.get("call_type") == ct and d.get("status") == "success"]
        return got[0] if got else None

    def last_of(ct: str) -> dict | None:
        got = [d for d in entries if d.get("call_type") == ct and d.get("status") == "success"]
        return got[-1] if got else None

    # l1：取**首次**阶梯运行的 ladder_key_facts（弧的初始 l1）。
    # 后续阶梯可能被重新 l1（编辑成正文/长文本），首轮才是规范的一句话/初始 brief。
    l1 = ""
    kf = first_of("ladder_key_facts")
    if kf:
        l1 = _l1_from_kf(str(kf.get("user") or ""))
    if not l1:
        b2 = first_of("bridge_l2")
        if b2:
            l1 = _l1_from_kf(str(b2.get("user") or ""))
    if not l1:
        l1 = f"情节 {name}（l1 未在日志中恢复）"

    # l2：bridge_l2_depol 优先（AI味清理后终稿），否则 bridge_l2
    l2 = ""
    dep = last_of("bridge_l2_depol")
    if dep and str(dep.get("output") or "").strip():
        l2 = _clean(dep.get("output"))
    else:
        b2 = last_of("bridge_l2")
        if b2:
            l2 = _l2_from_out(str(b2.get("output") or ""))

    # l3：bridge_l3_depol 优先，否则 bridge_l3_multi
    l3_data: dict | None = None
    dep3 = last_of("bridge_l3_depol")
    if dep3:
        l3_data = _l3_from_out(str(dep3.get("output") or ""))
    if not l3_data:
        b3 = last_of("bridge_l3_multi")
        if b3:
            l3_data = _l3_from_out(str(b3.get("output") or ""))
    chapters_data = (l3_data or {}).get("chapters") or []
    n_chapters = max(1, len(chapters_data))

    # l4：derive_expand_scenes 按章分组（最后一次成功）
    scene_by_ch: dict[int, list] = {}
    for d in entries:
        if d.get("call_type") in ("derive_expand_scenes", "derive_expand_scenes_retry") \
                and d.get("status") == "success":
            ch = _chapter_no_from_user(str(d.get("user") or ""))
            sc = _l4_scenes_from_out(str(d.get("output") or ""))
            if ch and sc:
                scene_by_ch[ch] = sc
    # l5：prose 续写（空 call_type 的 arc_step）按章拼接
    prose_by_ch: dict[int, list] = collections.defaultdict(list)
    for d in entries:
        if d.get("call_type") or d.get("status") != "success":
            continue
        ch = _chapter_no_from_user(str(d.get("user") or ""))
        if ch:
            prose_by_ch[ch].append(str(d.get("output") or ""))

    # 已确认推断：下一级存在 = 本级已确认
    confirmed = {
        "l1": True,
        "l2": bool(l2),
        "l3": bool(l3_data),
        "l4": bool(scene_by_ch),
        "l5": bool(prose_by_ch),
    }
    levels = {
        "l1": {"text": l1, "confirmed": True, "prompt": ""},
        "l2": {"text": l2, "confirmed": confirmed["l2"], "prompt": ""},
        "l3": {"data": l3_data or {"chapters": []}, "confirmed": confirmed["l3"], "prompt": "",
               "text": json.dumps(chapters_data[:2], ensure_ascii=False)[:200]},
        "l4": {"scenes": [], "confirmed": confirmed["l4"], "prompt": "", "text": ""},
        "l5": {"text": "", "confirmed": confirmed["l5"], "prompt": ""},
    }
    # 末章 l4/l5 放 state（active_chapter 语义：显示最后一个有内容的章）
    active_idx = max(list(scene_by_ch.keys()) or [0]) - 1
    active_scenes = scene_by_ch.get(active_idx + 1) or []
    if active_scenes:
        levels["l4"] = {"scenes": active_scenes, "confirmed": True,
                        "prompt": "", "text": _render_l4(active_scenes)}
    active_prose = "".join(prose_by_ch.get(active_idx + 1) or [])
    if active_prose:
        levels["l5"] = {"text": active_prose, "confirmed": True, "prompt": ""}

    return {
        "id": arc_id,
        "name": name,
        "l1": l1,
        "l2": l2,
        "n_chapters": n_chapters,
        "status": "draft",
        "selected": {"characters": [], "items": [], "settings": []},
        "state": {"levels": levels, "history": [], "n_chapters": n_chapters,
                  "active_chapter": max(0, active_idx)},
        "chapters": [],
        "finalized": {},
        "prev_anchor": [],
        "prev_arc_id": None,
    }


def attach_finalized(arc: dict) -> None:
    """从审查报告/AI生成/大纲 挂接已落盘章到 arc.chapters[] + finalized。"""
    arc_id = arc["id"]
    reports = sorted(REVIEW_DIR.glob("第*章-评分.json"))
    for rf in reports:
        try:
            rep = json.loads(rf.read_text(encoding="utf-8"))
        except Exception:
            continue
        if rep.get("arc_id") != arc_id:
            continue
        num = int(rep.get("chapter") or 0)
        nn = f"第{num:04d}章"
        title = _clean(rep.get("title") or "")
        core = _clean(rep.get("core") or "")
        # 大纲兜底 core
        if not core:
            of = OUTLINE_DIR / f"{nn}-章纲.md"
            if of.is_file():
                m = re.search(r"## 一句话核心\s*\n\s*(.+?)\s*\n", of.read_text(encoding="utf-8"), re.S)
                if m:
                    core = _clean(m.group(1))
        l5 = ""
        af = AIGEN_DIR / f"{nn}.md"
        if af.is_file():
            l5 = af.read_text(encoding="utf-8").strip()
        scores = rep.get("scores") or {}
        idx = len(arc["chapters"])
        arc["chapters"].append({
            "num": num, "title": title, "core": core,
            "intent_score": scores.get("intent_score"),
            "quality_score": scores.get("quality_score"),
            "overall": scores.get("overall"),
            "polluted": scores.get("polluted"),
            "text": l5,
        })
        arc["finalized"][str(idx)] = num
        arc["status"] = "done"
    # 让 state 的 l5 指向最后落盘章正文（工作台可继续审阅）
    if arc["chapters"]:
        last = arc["chapters"][-1]
        arc["state"]["levels"]["l5"] = {
            "text": last.get("text") or "", "confirmed": True, "prompt": ""}
        arc["l2"] = arc.get("l2") or ""


def main() -> None:
    ds = parse_log()
    by_arc: dict[str, list[dict]] = collections.defaultdict(list)
    for d in ds:
        a = d.get("arc") or ""
        if a:
            by_arc[a].append(d)
    # 弧顺序按首次出现 seq
    order = []
    for d in sorted(ds, key=lambda x: x.get("seq") or 0):
        a = d.get("arc") or ""
        if a and a not in order:
            order.append(a)

    # 弧名以审查报告为准（情节2-5 已记录）；未记录弧按 seq 补 情节1 / 情节6
    name_map: dict[str, str] = {}
    for rf in REVIEW_DIR.glob("第*章-评分.json"):
        try:
            rep = json.loads(rf.read_text(encoding="utf-8"))
        except Exception:
            continue
        aid = rep.get("arc_id")
        an = rep.get("arc_name")
        if aid and an:
            name_map[aid] = an
    unnamed = [a for a in order if a not in name_map]
    used = set(name_map.values())
    for seq_pos, arc_id in enumerate(unnamed):
        # 未记录弧填补空号：情节1 = 最早出现的未记录弧，其余依次
        slot = next(i for i in range(1, len(order) + 1)
                    if f"情节{i}" not in used)
        used.add(f"情节{slot}")
        name_map[arc_id] = f"情节{slot}"

    arcs_out = []
    for arc_id in order:
        name = name_map.get(arc_id, f"情节{len(arcs_out) + 1}")
        arc = build_arc_from_log(arc_id, by_arc[arc_id], name)
        attach_finalized(arc)
        arcs_out.append(arc)

    # 占位弧：情节{len+1}.. 从 basic_settings 确定性重建（batch 未阶梯）
    import uuid as _uuid
    from prompt_harness.ai_creation import _auto_arc_brief
    while len(arcs_out) < 10:
        name = f"情节{len(arcs_out) + 1}"
        brief = _auto_arc_brief(BOOK, len(arcs_out) + 1)
        if not brief:
            break
        arcs_out.append({
            "id": "arc_" + _uuid.uuid4().hex[:12],
            "name": name, "l1": brief, "l2": "",
            "n_chapters": 3, "status": "draft",
            "selected": {"characters": [], "items": [], "settings": []},
            "state": {"levels": {
                "l1": {"text": brief, "confirmed": True, "prompt": ""},
                "l2": {"text": "", "confirmed": False, "prompt": ""},
                "l3": {"data": None, "text": "", "confirmed": False, "prompt": ""},
                "l4": {"scenes": [], "text": "", "confirmed": False, "prompt": ""},
                "l5": {"text": "", "confirmed": False, "prompt": ""}},
                "history": [], "n_chapters": 3, "active_chapter": 0},
            "chapters": [], "finalized": {},
            "prev_anchor": [], "prev_arc_id": None,
        })

    next_num = 1
    for arc in arcs_out:
        for c in arc.get("chapters", []):
            next_num = max(next_num, int(c.get("num") or 0) + 1)
    payload = {"next_chapter_num": next_num, "arcs": arcs_out}
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"重建完成：{len(arcs_out)} 弧（日志恢复 {len(order)} + 占位 {len(arcs_out) - len(order)}）→ {OUT}")
    for arc in arcs_out:
        lv = arc["state"]["levels"]
        print(f"  {arc['name']} l1={len(arc['l1'])} l2={len(lv['l2']['text'])} "
              f"l3={'Y' if lv['l3']['data'] else '-'} l4={len(lv['l4']['scenes'])} "
              f"l5={len(lv['l5']['text'])} 落盘={len(arc['chapters'])} status={arc['status']}")


if __name__ == "__main__":
    main()
