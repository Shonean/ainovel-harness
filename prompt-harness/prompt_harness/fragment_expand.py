# -*- coding: utf-8 -*-
"""v6.5 片段锚定扩写（fragment_expand.py）：用户半成品片段 → 只扩写【】→ 完整章。

用户输入形态（2026-08-08 用户拍板为 AInovel 最常见输入方式）：
    一段半成品章节。用户写下的对白与叙述是「锚点」，要系统扩写的地方用【】标出——
    【】内可能是描写指令（如「描写一个恢宏大气的建筑前」），也可能是空白的待补内容。

核心契约（用户明确要求，三条）：
    1. 只扩写【】内的内容；锚点（对白+叙述）一个字节都不改、不删、不重排。
    2. LLM 先理解每个【】的内容（它要求写什么）再动手扩写——理解先行。
    3. 两种【】分开对待：
       - 对白内【】（位于引号内）→ 句子补全：只补语法不造事实，标「待用户确认」
       - 非对白【】（独立行 / 叙述内）→ 描写/动作/收束：按指令写，克制不堆砌

实现（锚点物理锁定，不靠 LLM 自觉）：
    parse_fragment    确定性解析（零 LLM）：锚点 + 槽位 + 行结构
    understand_slots  LLM 先理解每个槽位 → 扩写指令（intent/requirements/constraints）
    expand_slots      逐槽位 LLM 扩写 —— 模型只产出填充文本，拼回原文行，摸不到锚点
    assemble          确定性拼接：锚点逐字 + 填充插入
    verify            锚点保真（逐条子串顺序校验）+ 槽位覆盖 + 无残留【】+ 风格检测

对外入口：expand_fragment(text, directives=None) → {output, fills, verify, slots, directives}
"""
from __future__ import annotations

import json
import re
import uuid
from pathlib import Path
from typing import Any

from .ai_flavor import overdetail_detector, paragraph_open_diversity_detector

_MARKER_RE = re.compile(r"【([^】]*)】")
_DIALOGUE_OPEN = "“「"
_DIALOGUE_CLOSE = "”」"

# 扩写硬约束（复用 v6.3/v6.4.1 的 l5 经验：过场一笔带过 / 宁缺毋凑 / 不堆砌）
_EXPAND_STYLE_RULES = """扩写硬约束：
- 过场一笔带过：不写"如何走到现场/沿途景物/固定装备（除非当下信息相关）"的前奏，直接从现场写起。
- 克制不堆砌：感官氛围最多一句且服务当下判断；不逐微动作展开；物件清单压缩成一个。
- 具体设定宁缺毋错：拿不准的专有名词/数字/事件宁可留白，不要编造。
- 同一信息只写一次；写不足字数宁缺毋凑，不靠展开细节补字。
- 若扩写内容里需要出现对白，用与片段内现有对白一致的引号样式。"""

_UNDERSTAND_SYSTEM = """你是一位网文章节编辑助手。用户把一段小说的半成品片段交给你扩写：片段里已经保留了对白和叙述（这些是"锚点"，一个字都不能动），凡是要你扩写的地方都用【】标出——【】里可能是描写指令，也可能是空白待补。

你的任务：逐条【】位置给出"扩写指令"。请先理解每个【】（或空白处）要求你写什么，再判断：
- kind：inline（【】在人物对白引号内，是句子补全）/ prose（独立行或叙述内的描写/动作/收束）
- intent：这个位置要写什么（一段环境描写/一个动作场景/一句话的补全/一章收束等），用一句话说清
- requirements：写的时候必须包含什么（结合【】内的指令字面意思）
- constraints：写的时候不能做什么（结合上下文，如"不得编造具体人名/事件/数字"）
- needs_review：补全内容是否可能引入用户尚未定稿的设定或剧情事实（若是标 true，供用户确认；句子补全类一般标 true）

只输出 JSON，不要其它文字。结构：
{"slots":[{"id":"...","kind":"inline|prose","intent":"...","requirements":["..."],"constraints":["..."],"needs_review":true|false}]}"""

_EXPAND_SYSTEM = """你是一位网文写手。下面是一段小说的半成品片段，其中【】标记是用户留给你扩写的位置。你负责扩写指定槽位。

铁律：
1. 你只负责扩写「目标槽位」，绝对禁止改动、删除、重排片段里用户写下的任何字（包括对白和叙述）。
2. 扩写内容要与上下文自然衔接、符合人物口吻与场景氛围。
3. 扩写内容必须满足下方【扩写指令】的 intent/requirements/constraints。
4. 只输出扩写文本本身，不要任何解释、不要引号包裹、不要编号、不要重复目标槽位指令。"""


# ════════════════════════════════════════════════════════════════
# 确定性解析（零 LLM）
# ════════════════════════════════════════════════════════════════

def _quote_style_of(text: str) -> str:
    """检测片段主要对白引号样式：返回'curly'（“”）或'angle'（「」），默认 curly。"""
    n_curly = len(re.findall(r"[“”]", text or ""))
    n_angle = len(re.findall(r"[「」]", text or ""))
    return "angle" if n_angle > n_curly else "curly"


def _line_has_quote(line: str) -> bool:
    return any(c in line for c in _DIALOGUE_OPEN) or any(c in line for c in _DIALOGUE_CLOSE)


def _inside_quotes(line: str, marker_start: int) -> bool:
    """该【】位置是否位于一对引号内（对白内 = 句子补全）。"""
    before = line[:marker_start]
    open_at = max([before.rfind(c) for c in _DIALOGUE_OPEN] + [-1])
    after = line[marker_start + 1:]
    close_idx = min([i for i in (after.find(c) for c in _DIALOGUE_CLOSE) if i >= 0]
                    + [10 ** 9])
    return open_at >= 0 and close_idx < 10 ** 9


def _extract_anchors(text: str) -> list[str]:
    """从片段提取锚点文本（删掉【...】标记后的非空块，用于保真校验）。"""
    parts: list[str] = []
    last = 0
    for m in _MARKER_RE.finditer(text or ""):
        if (text[last:m.start()] or "").strip():
            parts.append(text[last:m.start()])
        last = m.end()
    if (text[last:] or "").strip():
        parts.append(text[last:])
    return parts


def parse_fragment(text: str) -> dict[str, Any]:
    """确定性解析：行 + 锚点 + 槽位。零 LLM。

    Returns:
        {ok, lines, anchors, slots, n_slots, quote_style}
        anchors: [{id, text, line_idx}]     # 非空锚点块（去【】后）
        slots:  [{id, instruction, line, line_idx, kind, blank_idx}]
                # blank_idx：该行内第几个空白（1 起）；仅 inline 有意义
    """
    text = (text or "")
    lines = text.split("\n")
    anchors: list[dict[str, Any]] = []
    slots: list[dict[str, Any]] = []
    qs = _quote_style_of(text)
    for li, line in enumerate(lines):
        blanks: list[tuple[str, str, int, int, bool]] = []
        for m in _MARKER_RE.finditer(line):
            ins = m.group(1).strip()
            inside = _inside_quotes(line, m.start())
            blanks.append((m.group(0), ins, m.start(), m.end(), inside))
        for bidx, (raw, ins, _, _, inside) in enumerate(blanks, start=1):
            kind = "inline" if inside else "prose"
            slots.append({
                "id": f"s{len(slots) + 1}",
                "instruction": ins,
                "line": line,
                "line_idx": li,
                "kind": kind,
                "blank_idx": bidx if inside else None,
                "quote_style": qs,
            })
        # 锚点 = 删掉【...】标记后的非空文本（用非捕获切分，别把标记内容当锚点）
        for p in re.split(r"【[^】]*】", line):
            if p.strip():
                anchors.append({"id": f"a{len(anchors) + 1}", "text": p, "line_idx": li})
    if not text.strip():
        return {"ok": False, "error": "片段为空"}
    if not slots:
        return {"ok": True, "lines": lines, "anchors": anchors, "slots": slots,
                "n_slots": 0, "quote_style": qs,
                "warn": "片段里没有【】标记——没有可扩写的位置"}
    return {"ok": True, "lines": lines, "anchors": anchors, "slots": slots,
            "n_slots": len(slots), "quote_style": qs}


# ════════════════════════════════════════════════════════════════
# 理解先行：LLM 解读每个【】 → 扩写指令
# ════════════════════════════════════════════════════════════════

async def understand_slots(text: str, slots: list[dict[str, Any]]) -> dict[str, Any]:
    """LLM 先理解每个【】的内容 → 扩写指令（intent/requirements/constraints/needs_review）。

    用户明确要求「llm先理解【】内的内容再工作」——这一步是扩写的前置，产出可被
    用户在 UI 里审阅/修改后再扩写。
    """
    from .llm_client import chat_json
    slot_brief = "\n".join(
        f"- [{s['id']}] kind={s['kind']} 位置第{s['line_idx'] + 1}行"
        f" 指令:「{s['instruction'] or '（空白待补）'}」"
        f" 所在行:「{s['line'].strip()}」"
        for s in slots
    )
    user = (
        "【完整片段】\n---\n" + text + "\n---\n\n"
        "【待理解的槽位清单】\n" + slot_brief + "\n\n"
        "请逐条给出扩写指令，只输出 JSON。"
    )
    data = await chat_json(
        system=_UNDERSTAND_SYSTEM, user=user, temperature=0.2,
        max_tokens=3000, call_type="fragment_understand",
    )
    slots_out = []
    parsed = data.get("data") if isinstance(data, dict) else None
    if isinstance(parsed, dict):
        raw_slots = parsed.get("slots") or []
        by_id = {s["id"]: s for s in slots}
        for rs in raw_slots if isinstance(raw_slots, list) else []:
            sid = str(rs.get("id") or "")
            base = by_id.get(sid)
            if not base:
                continue
            slots_out.append({
                "id": sid,
                "kind": str(rs.get("kind") or base["kind"]),
                "instruction": base["instruction"],
                "line": base["line"],
                "line_idx": base["line_idx"],
                "blank_idx": base.get("blank_idx"),
                "quote_style": base.get("quote_style"),
                "intent": str(rs.get("intent") or "").strip(),
                "requirements": [str(x).strip() for x in (rs.get("requirements") or [])
                                 if str(x).strip()],
                "constraints": [str(x).strip() for x in (rs.get("constraints") or [])
                                if str(x).strip()],
                "needs_review": bool(rs.get("needs_review")),
            })
    # 兜底：LLM 漏掉的槽位 → 默认指令（不因解析失败卡死）
    have = {s["id"] for s in slots_out}
    for s in slots:
        if s["id"] not in have:
            slots_out.append({
                **s,
                "intent": "（未理解成功）按【】内字面意思扩写",
                "requirements": [s["instruction"]] if s["instruction"] else [],
                "constraints": [],
                "needs_review": s["kind"] == "inline",
            })
    return {"ok": True, "slots": slots_out}


# ════════════════════════════════════════════════════════════════
# 逐槽位扩写（锚点物理锁定）
# ════════════════════════════════════════════════════════════════

def _after_marker_on_line(line: str, instruction: str) -> str:
    """取行内该标记之后的原文后缀（用于扩写衔接，如锚点紧跟的「：」+ 引号）。"""
    seg = f"【{instruction}】"
    idx = line.find(seg)
    if idx < 0:
        # 空指令标记【】
        for m in _MARKER_RE.finditer(line):
            if m.group(1).strip() == instruction:
                idx = m.start()
                break
    if idx < 0:
        return ""
    return line[idx + len(seg):].strip()


def _numbered_line(line: str) -> tuple[str, int]:
    """把行内【...】逐个编号为【1】【2】…，返回 (编号行, 空白总数)。"""
    n = 0
    def _repl(_m: re.Match) -> str:
        nonlocal n
        n += 1
        return f"【{n}】"
    return _MARKER_RE.sub(_repl, line), n


def _extract_json_obj(content: str) -> dict | None:
    """从 LLM 输出里稳健提取顶层 JSON 对象：容忍 code fence / 前后杂质。"""
    content = (content or "").strip()
    if content.startswith("```"):
        lines = content.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        content = "\n".join(lines).strip()
    try:
        obj = json.loads(content)
        return obj if isinstance(obj, dict) else None
    except Exception:
        pass
    # 找第一个括号平衡的完整顶层对象（容忍尾部垃圾/多个 JSON）
    for i in range(len(content)):
        if content[i] != "{":
            continue
        depth = 0
        in_str = False
        esc = False
        for j in range(i, len(content)):
            c = content[j]
            if in_str:
                if esc:
                    esc = False
                elif c == "\\":
                    esc = True
                elif c == '"':
                    in_str = False
            else:
                if c == '"':
                    in_str = True
                elif c == "{":
                    depth += 1
                elif c == "}":
                    depth -= 1
                    if depth == 0:
                        try:
                            obj = json.loads(content[i:j + 1])
                            return obj if isinstance(obj, dict) else None
                        except Exception:
                            return None
    return None


def _parse_fills(content: str, group: list[dict[str, Any]]) -> dict[str, str]:
    """稳健解析 inline 组的填充：fills 可为 list 或 dict；无 slot_id 时按 blank 序号映射。"""
    obj = _extract_json_obj(content)
    fills: dict[str, str] = {}
    if not obj:
        return fills
    raw = obj.get("fills")
    if isinstance(raw, dict):
        raw = list(raw.values())
    if not isinstance(raw, list):
        return fills
    by_blank = {int(s.get("blank_idx") or 0): s["id"] for s in group}
    for f in raw:
        if not isinstance(f, dict):
            continue
        txt = str(f.get("text") or "").strip()
        if not txt:
            continue
        sid = str(f.get("slot_id") or "")
        if sid and any(s["id"] == sid for s in group):
            fills[sid] = txt
        else:
            blank = f.get("blank")
            try:
                bid = int(blank)
            except (TypeError, ValueError):
                continue
            if bid in by_blank:
                fills[by_blank[bid]] = txt
    return fills


async def _expand_inline_group(
    text: str, line: str, group: list[dict[str, Any]], directives: dict[str, Any],
) -> dict[str, int, str]:
    """扩写同一行内的全部 inline 槽位（一次调用，保证补全互相咬合）。

    Returns: {slot_id: fill}
    """
    from .llm_client import chat_completion
    numbered, total = _numbered_line(line)
    targets = []
    for s in group:
        d = directives.get(s["id"], {})
        targets.append(
            f"- [{s['id']}] 第 {s['blank_idx']} 个空白（kind=inline，对白内句子补全）\n"
            f"  intent：{d.get('intent') or s.get('instruction') or '（空白待补）'}\n"
            f"  必须包含：{('；'.join(d.get('requirements') or [])) or '（按上下文自然补全）'}\n"
            f"  禁止：{('；'.join(d.get('constraints') or [])) or '（不造事实、不新增设定）'}"
        )
    user = (
        "【扩写指令】\n" + "\n".join(targets) + "\n\n"
        "【完整片段】（只读参考，禁止改动）\n---\n" + text + "\n---\n\n"
        "【目标行】（各空白已编号为【1】..【N】）\n" + numbered + "\n\n"
        + _EXPAND_STYLE_RULES + "\n\n"
        "请对上述每个目标空白给出补全文本，输出 JSON："
        '{"fills":[{"blank":1,"text":"补全第1个空白的内容","slot_id":"sX"},...]}。'
        "补全是句子的自然延续，不要带引号、不要解释。"
    )
    # 走 _tree_llm（重试+修复）而不是裸 chat_completion：doubao 密集嵌套 JSON 经常
    # 畸形（缺字符/缺逗号），_tree_llm 的状态机修复 + _first_json_value 三连能救回。
    try:
        from .derive import _tree_llm
        obj = await _tree_llm(_EXPAND_SYSTEM, user, "fragment_expand_inline",
                              max_tokens=2000)
    except Exception:  # noqa: BLE001
        return {}
    return _parse_fills(json.dumps(obj, ensure_ascii=False), group)


async def _expand_prose(
    text: str, slot: dict[str, Any], directive: dict[str, Any],
) -> str:
    """扩写单个 prose 槽位（独立行 / 叙述内的描写、动作、收束）。"""
    from .llm_client import chat_completion
    # 邻接行上下文（前后各 2 行）+ 紧随其后的原文（让扩写自然衔接到锚点的冒号/引号）
    lines = text.split("\n")
    ctx_lines = lines[max(0, slot["line_idx"] - 2): slot["line_idx"] + 3]
    ctx = "\n".join(ctx_lines)
    suffix = _after_marker_on_line(slot["line"], slot["instruction"])
    suffix_blk = f"【紧随其后的原文】（你的扩写要能自然接到这段文字前面，不要以句号等标点结尾把它断开）\n{suffix}\n\n" if suffix else ""
    user = (
        "【目标槽位】" + slot["id"] + "（kind=prose）\n"
        "【扩写指令】" + (directive.get("intent") or slot["instruction"]
                          or "（空白）") + "\n"
        "必须包含：" + ("；".join(directive.get("requirements") or []) or "（按指令）") + "\n"
        "禁止：" + ("；".join(directive.get("constraints") or []) or "（不编造设定、克制不堆砌）") + "\n\n"
        "【邻接上下文】\n" + ctx + "\n\n"
        + suffix_blk
        + _EXPAND_STYLE_RULES + "\n\n"
        "【输出要求】只输出这个位置的扩写文本本身，不要引号包裹、不要解释。"
        "注意：这段扩写是接在原文中间的（前后都有用户写好的字），"
        "所以不要写人物台词/对白（引号和冒号由原文提供）；只写动作、神态、场景、氛围。"
        "结尾不要带句号等终结标点，除非你的扩写就是一句话的完整收束且后面没有原文。"
    )
    data = await chat_completion(
        system=_EXPAND_SYSTEM, user=user, temperature=0.6,
        max_tokens=1500, call_type="fragment_expand_prose",
    )
    return str(data.get("content") or "").strip()


async def expand_slots(
    text: str, slots: list[dict[str, Any]], directives: list[dict[str, Any]] | None,
) -> dict[str, Any]:
    """逐槽位扩写。锚点物理锁定：模型只产出填充，拼接在原文行上。

    Returns: {ok, fills: {slot_id: fill}}
    """
    dmap: dict[str, dict[str, Any]] = {}
    for d in (directives or []):
        if isinstance(d, dict) and d.get("id"):
            dmap[d["id"]] = d
    # 同行的 inline 槽位合并为一次调用；prose 逐槽位
    inline_groups: dict[int, list[dict[str, Any]]] = {}
    prose_slots: list[dict[str, Any]] = []
    for s in slots:
        if s["kind"] == "inline":
            inline_groups.setdefault(s["line_idx"], []).append(s)
        else:
            prose_slots.append(s)
    fills: dict[str, str] = {}
    # inline 组
    for li, group in inline_groups.items():
        line = group[0]["line"]
        r = await _expand_inline_group(text, line, group, dmap)
        fills.update(r)
    # prose
    for s in prose_slots:
        fill = await _expand_prose(text, s, dmap.get(s["id"], {}))
        if fill:
            fills[s["id"]] = fill
    return {"ok": True, "fills": fills}


# ════════════════════════════════════════════════════════════════
# 确定性拼接 + 校验
# ════════════════════════════════════════════════════════════════

def assemble(text: str, fills: dict[str, str]) -> str:
    """把填充拼回原文行（锚点逐字），返回完整章。确定性，不依赖 LLM。

    槽位 id 按全局标记出现顺序编号（与 parse_fragment 一致）：第 i 个【】→ s{i}。
    """
    out: list[str] = []
    mi = 0
    for line in text.split("\n"):
        parts: list[str] = []
        last = 0
        for m in _MARKER_RE.finditer(line):
            parts.append(line[last:m.start()])
            mi += 1
            parts.append(str(fills.get(f"s{mi}") or ""))
            last = m.end()
        parts.append(line[last:])
        out.append("".join(parts))
    return "\n".join(out)


def verify(text: str, output: str, fills: dict[str, str]) -> dict[str, Any]:
    """锚点保真（确定性）+ 槽位覆盖 + 无残留【】 + 风格检测。

    Returns: {anchor_fidelity, coverage, leftover_markers, style}
    """
    checks: list[dict[str, Any]] = []
    search_from = 0
    for a in _extract_anchors(text):
        idx = output.find(a, search_from)
        checks.append({
            "anchor": a[:40] + ("…" if len(a) > 40 else ""),
            "found": idx >= 0,
            "order_ok": idx >= search_from,
        })
        if idx >= 0:
            search_from = idx + len(a)
    anchor_ok = all(c["found"] and c["order_ok"] for c in checks)

    total = len(_MARKER_RE.findall(text))
    missing = [f"s{i}" for i in range(1, total + 1)
               if not str(fills.get(f"s{i}") or "").strip()]
    leftover = _MARKER_RE.findall(output)

    style = {}
    try:
        style["overdetail"] = overdetail_detector(output)
    except Exception as e:  # 检测失败不崩，但透出原因供诊断
        style["overdetail_error"] = str(e)
    try:
        style["open"] = paragraph_open_diversity_detector(output)
    except Exception as e:
        style["open_error"] = str(e)

    return {
        "anchor_fidelity": {"pass": anchor_ok, "checked": len(checks), "details": checks},
        "coverage": {"filled": total - len(missing), "total": total, "missing": missing},
        "leftover_markers": leftover,
        "style": style,
    }


# ════════════════════════════════════════════════════════════════
# 总入口
# ════════════════════════════════════════════════════════════════

async def expand_fragment(
    text: str,
    directives: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """片段 → 完整章 总入口。

    directives 可传 understand_slots 的结果（用户审阅过）；不传则内部先理解。
    Returns: {ok, output, fills, slots, directives, verify, parse}
    """
    parsed = parse_fragment(text)
    if not parsed.get("ok"):
        return parsed
    slots = parsed["slots"]
    if not slots:
        return {**parsed, "output": text, "fills": {}, "verify": verify(text, text, {})}
    if directives is None:
        und = await understand_slots(text, slots)
        directives = und["slots"]
    exp = await expand_slots(text, slots, directives)
    fills = exp["fills"]
    # 兜底：没填上的槽位给占位提示（保证输出完整可读）
    for s in slots:
        if not str(fills.get(s["id"]) or "").strip():
            fills[s["id"]] = "【待补】"
    output = assemble(text, fills)
    v = verify(text, output, fills)
    return {
        "ok": True,
        "output": output,
        "fills": fills,
        "slots": slots,
        "directives": directives,
        "verify": v,
        "parse": parsed,
    }


# ════════════════════════════════════════════════════════════════
# 落盘：片段扩写结果 → 当前书（合成情节 + 复用 finalize_chapter）
# ════════════════════════════════════════════════════════════════

async def finalize_fragment(
    book_root: str | Path,
    text: str,
    output: str,
    *,
    title: str = "",
) -> dict[str, Any]:
    """把片段扩写结果落盘当前书：合成一条情节（l2=原始片段作意图 target、l5=完整章）。

    意图兑现分以「用户片段」为 target（测扩写是否覆盖用户锚点内容）；
    元素合规跳过（片段模式不做元素选择）。复用 ai_creation.finalize_chapter 落盘
    章纲/正文/评分报告，记录进 arcs.json，章号按书全局递增。
    """
    from . import ai_creation as ac
    book_root = Path(book_root)
    arcs = ac.load_arcs(book_root)
    title = (title or "").strip()
    l1 = title or "片段扩写"
    arc_id = "frag_" + uuid.uuid4().hex[:12]
    arc = {
        "id": arc_id,
        "name": f"片段{len(arcs.get('arcs', [])) + 1}",
        "l1": l1,
        "l2": "",
        "n_chapters": 1,
        "selected": {"characters": [], "items": [], "settings": []},
        "state": {
            "levels": {
                "l1": {"text": l1, "confirmed": True},
                "l2": {"text": text},                    # 原始片段 = 意图 target
                "l3": {"data": {"title": l1, "core": "", "beats": []}},
                "l5": {"text": output},
            },
            "active_chapter": 0,
        },
        "chapters": [],
        "status": "draft",
        "source": "fragment",
    }
    arcs.setdefault("arcs", []).append(arc)
    ac.save_arcs(book_root, arcs)
    try:
        return await ac.finalize_chapter(
            book_root, arc_id, chapter_idx=0, skip_compliance=True)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"落盘失败：{e}"}
