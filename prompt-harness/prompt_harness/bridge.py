# -*- coding: utf-8 -*-
"""v5.33 阶梯桥接 —— 主系统正文模块 ↔ prompt-harness 训练模块的连接器。

用户需求（2026-08-06 睡觉前拍板，与 [[ainovel-ladder-v532]] 的 1-12-123-1234-12345
阶梯衔接）：
- 主系统正文模块填完大纲+设定后，第一步生成「前十章章纲」。章纲 = prompt，
  通过 1 → 12 → 123 → 1234 → 12345 阶梯逐级展开。
- **分步确认制**：每一级生成后停在「待审阅」态，用户在前端审阅、修改、确认后
  才允许进下一级。修改 = 对话式（LLM 按用户意见改写当前级）。
- **命中情节模板库**：写剧情时若 match_plot_templates 命中同款剧情 →
  套用该模板的格式（template_format_block）+ 1→12345 策略（strategy_overrides）。
- **对话窗口后端**：chat_turn 让用户与 LLM 就当前阶梯讨论/提修改建议。

数据流：
  用户手写 l1（一句话极简，天然已确认）
    → step_ladder() 逐级生成 l2 → l3 → l4 → l5（每级 confirmed=False 待审阅）
    → confirm_level() 确认后继续
    → 任一级 modify_level() 对话修改 → 清空下游级（stale）→ 重新 step 生成
    → chat_turn() 讨论/建议（不改动内容，只对话）

参考：plot_library.template_format_block / strategy_overrides（模板格式+策略），
ladder.extract_key_facts（反漂移）、ladder.generate_chapter_prose（正文生成），
derive._tree_llm（JSON 稳定调用）、derive._expand_arc_scenes_llm（场景拆解）。
"""
from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone
from typing import Any

from .config import SETTINGS
from .llm_client import chat_completion

# 五级阶梯顺序（1=极简 → 5=正文）
_LEVEL_ORDER = ("l1", "l2", "l3", "l4", "l5")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── 渲染/判断辅助 ───────────────────────────────────────────────────────

def _render_l3(data: dict[str, Any]) -> str:
    """章核心 dict → 可读文本。支持多章：data={"chapters":[单章...]}。"""
    if not isinstance(data, dict):
        return ""
    chs = data.get("chapters")
    if isinstance(chs, list):
        parts: list[str] = []
        for i, ch in enumerate(chs, 1):
            ch = ch if isinstance(ch, dict) else {}
            title = str(ch.get("title") or f"第{i}章").strip()
            core = str(ch.get("core") or "").strip()
            beats = [str(b).strip() for b in (ch.get("beats") or []) if str(b).strip()]
            s = [f"第{i}章 {title}".strip()]
            if core:
                s.append("核心：" + core)
            if beats:
                s.append("拍：" + "；".join(beats))
            parts.append("\n".join(s))
        return "\n\n".join(parts)
    title = str(data.get("title") or "").strip()
    core = str(data.get("core") or "").strip()
    beats = [str(b).strip() for b in (data.get("beats") or []) if str(b).strip()]
    parts = []
    if title:
        parts.append("标题：" + title)
    if core:
        parts.append("核心：" + core)
    if beats:
        parts.append("拍：" + "；".join(beats))
    return "\n".join(parts)


def _active_chapter(state: dict[str, Any]) -> tuple[dict[str, Any], int]:
    """取当前要下钻的章核心（单章=l3.data；多章=l3.data.chapters[active_chapter]）。
    Returns (chapter_dict, idx)。
    """
    l3d = (state.get("levels") or {}).get("l3", {}).get("data") or {}
    chs = l3d.get("chapters") if isinstance(l3d, dict) else None
    if isinstance(chs, list) and chs:
        idx = max(0, min(int(state.get("active_chapter") or 0), len(chs) - 1))
        return (chs[idx] if isinstance(chs[idx], dict) else {}), idx
    return (l3d if isinstance(l3d, dict) else {}), 0


def _render_l4(scenes: list[dict[str, Any]]) -> str:
    """场景列表 → 可读摘要（名称 + 素材丰度）。"""
    lines = []
    for i, sc in enumerate(scenes or [], 1):
        if not isinstance(sc, dict):
            continue
        name = str(sc.get("name") or f"场景{i}").strip()
        env = str(sc.get("environment") or "").strip()

        def _n(k: str) -> int:
            v = sc.get(k)
            return len([x for x in v if str(x).strip()]) if isinstance(v, list) else 0

        env_s = (env[:30] + "…") if len(env) > 30 else (env or "—")
        lines.append(
            f"{i}. {name}｜环境：{env_s}｜动作{_n('actions')}/对白{_n('dialogues')}"
            f"/心理{_n('psychologies')}/冲突{_n('conflicts')}/细节{_n('details')}"
        )
    return "\n".join(lines) if lines else "（无场景）"


# ── AI 味防线：文生文每步 LLM 测 AI 率（用户 2026-08-07 强调）──────────────
# 不只 l5 正文——l2 概要/l3 章核心/l4 场景等所有文生文环节生成后都要 LLM 审阅 AI 率。
_AI_FLAVOR_GUARD_THRESHOLD = 0.65


async def _measure_ai_flavor(text: str) -> dict[str, Any]:
    """文生文每步 LLM 测 AI 率：{score, findings, summary, dirty}。

    score（AI 率分，越高越不像 AI）为 None（审阅失败/文本过短）时不阻断；
    dirty = score < 阈值（AI 味重，需修正）。返回供前端展示/记录。
    """
    from .ai_flavor import review_ai_flavor_standalone
    rv = await review_ai_flavor_standalone(str(text or ""))
    score = rv.get("score")
    rv["dirty"] = score is not None and score < _AI_FLAVOR_GUARD_THRESHOLD
    return rv


async def _ai_flavor_clean_text(text: str, role_hint: str, call_type: str) -> dict[str, Any]:
    """l2/l3/l4 AI 味防线（2026-08-10）：测分 → 脏则带反馈文本层修正一次（保留信息只改措辞）。
    Returns {text, review, retried}。复用 review_ai_flavor_standalone + build_ai_flavor_feedback。"""
    from .ai_flavor import build_ai_flavor_feedback
    from .llm_client import chat_completion
    review = await _measure_ai_flavor(text)
    if not review.get("dirty"):
        return {"text": text, "review": review, "retried": 0}
    fb = build_ai_flavor_feedback(review.get("findings") or [])
    if not fb:
        return {"text": text, "review": review, "retried": 0}
    sys_p = (
        "你是苛刻的小说编辑。下面是情节创作中间产物，AI 味重。请**完整保留全部信息与结构**，"
        "只按审阅意见修正措辞——删解释性旁白/对白装饰/氛围词堆砌/总结升华，留白克制，"
        "把话说得自然直接。直接输出修正后的完整文本，不要解释、不要代码块。\n"
        f"【你的角色】{role_hint}"
    )
    usr_p = (
        "【待修正文本】\n" + text
        + "\n\n【审阅意见】\n" + fb
        + "\n\n直接输出修正后的完整文本。"
    )
    r = await chat_completion(system=sys_p, user=usr_p, temperature=0.5,
                              max_tokens=2000, call_type=call_type)
    new_text = (r.get("content") or "").strip() or text
    review2 = await _measure_ai_flavor(new_text)
    return {"text": new_text, "review": review2, "retried": 1}


def _filled(state: dict[str, Any], level: str) -> bool:
    """某级是否已有内容（l3 看 data，l4 看 scenes，其余看 text）。"""
    lv = (state.get("levels") or {}).get(level) or {}
    if level == "l3":
        return bool(lv.get("data")) if isinstance(lv.get("data"), dict) else bool((lv.get("text") or "").strip())
    if level == "l4":
        return bool(lv.get("scenes"))
    return bool((lv.get("text") or "").strip())


def _clear_downstream(state: dict[str, Any], level: str) -> None:
    """修改某级后，把它的下游级清空（stale，须重新 step 生成）。"""
    levels = state.setdefault("levels", {})
    idx = _LEVEL_ORDER.index(level)
    for k in _LEVEL_ORDER[idx + 1:]:
        if k == "l3":
            levels[k] = {"data": None, "text": "", "confirmed": False, "prompt": ""}
        elif k == "l4":
            levels[k] = {"scenes": [], "text": "", "confirmed": False, "prompt": ""}
        else:
            levels[k] = {"text": "", "confirmed": False, "prompt": ""}


def _facts_block(facts: list[str]) -> str:
    """【关键事实·不可变】反漂移块（从 ladder.extract_key_facts 结果构造）。"""
    if not facts:
        return ""
    return (
        "【关键事实·不可变】以下事实必须原样保留在后续每一级展开与正文里："
        "人物不得改名（如 王慧玲 不能写成 张兰），地点/物品/数字/事件不得丢失。\n"
        + "；".join(facts)
    )


def _element_block_from_state(state: dict[str, Any]) -> str:
    """元素白名单块（ai_creation 注入 state.element_block，l3 起生效）。"""
    return str((state or {}).get("element_block") or "").strip()


def _open_names_from_block(element_block: str) -> tuple[list[str], list[str]]:
    """从元素白名单块提取人名/物件名，供段落开头检测器（v6.4）。

    块格式（ai_creation._element_block）：参与角色：沈石（desc）、…；
    参与物品：…（desc）；参与设定：名（别名1/别名2）……。
    角色名进 names；物品/设定名及设定别名进 objects。
    """
    names: list[str] = []
    objs: list[str] = []
    seg = str(element_block or "")
    for label, target in (("参与角色", names), ("参与物品", objs), ("参与设定", objs)):
        m = re.search(re.escape(label) + r"：(.+?)(?=\n|【|$)", seg)
        if not m:
            continue
        for item in m.group(1).split("、"):
            core = re.split(r"[（(]", item, maxsplit=1)[0].strip()
            if core and core not in target:
                target.append(core)
            if label == "参与设定":
                pm = re.search(r"[（(](.+?)[）)]", item)
                if pm:
                    for a in re.split(r"[/、]", pm.group(1)):
                        a = a.strip()
                        if len(a) >= 2 and a not in objs:
                            objs.append(a)
    return names, objs


def _constraints_block_from_state(state: dict[str, Any]) -> str:
    """硬约束块（ai_creation 注入 state.constraints_block，l3 起生效）。"""
    return str((state or {}).get("constraints_block") or "").strip()


def _prev_anchor_block(pa: str | None) -> str:
    """前文摘要块（上一情节结局锚点，l2/l3 承接用）。"""
    pa = str(pa or "").strip()
    if not pa:
        return ""
    return "【前文摘要（上一情节结局，本情节应承接其状态）】\n" + pa


def _roadmap_block_from_state(state: dict[str, Any]) -> str:
    """【T32 P4】量产路线图方向块（ai_creation 注入 state.roadmap_block，仅 l2 生效）。"""
    return str((state or {}).get("roadmap_block") or "").strip()


def _fields_block(field_values: dict[str, Any] | None) -> str:
    """【已填写字段·不可变】块（v5.33.2）：用户按模板填好的字段值，注入各级防漂移。"""
    fv = field_values or {}
    filled = {k: str(v).strip() for k, v in fv.items() if str(v or "").strip()}
    if not filled:
        return ""
    from .plot_library import _FIELD_LABELS
    lines = [f"{_FIELD_LABELS.get(k, k)}：{v}" for k, v in filled.items()]
    return (
        "【已填写字段·不可变】以下字段是用户按模板填好的，后续每一级展开与正文必须"
        "原样保留（人物不得改名、地点/数字/物件不得丢失）：\n" + "\n".join(lines)
    )


def _ladder_invariant_block(level: str) -> str:
    """阶梯某级（l1-l5）不变 prompt 块（v6.3：fixed_prompts.json 运行时可编辑）。"""
    from .fixed_prompts import get_ladder_invariant
    return get_ladder_invariant(level)


def _strip_code_fence(text: str) -> str:
    t = re.sub(r"^```[a-zA-Z]*\s*", "", (text or "").strip())
    t = re.sub(r"\s*```$", "", t)
    return t.strip()


async def _arc_to_scenes(
    title: str,
    core: str,
    style: str,
    role_setting: str,
    *,
    archetype: str = "",
    template: dict[str, Any] | None = None,
    facts: list[str] | None = None,
    field_values: dict[str, Any] | None = None,
    extra: str = "",
    element_block: str = "",
    constraints_block: str = "",
    trend_block: str = "",
    beats: list[str] | None = None,
) -> list[dict[str, Any]]:
    """章核心 → 场景拆解（l3→l4 生成 与 修改 l4 重拆 共用，消除重复）。

    注入：关键事实反漂移块 + 模板字段锚定块 + 模板 l4 格式参考 + 模板策略
    scene_density 提示 + 可选修改意见（extra）+ 元素白名单块（element_block）
    + 硬约束块（constraints_block）+ 流行钩子块（trend_block，T8 场景决断）。
    【v5.34】beats（l3 的场景切分计划）透传成 scene_plan——展开时严格按切分逐场景进行。
    返回 6 类叶子场景列表。
    """
    from .derive import _expand_arc_scenes_llm
    from .plot_library import strategy_overrides, template_format_block

    parts: list[str] = [core] if core else []
    if extra:
        parts.append("【修改意见】" + extra)
    if trend_block:
        parts.append("【流行钩子参考】\n" + trend_block)
    inv4 = _ladder_invariant_block("l4")
    if inv4:
        parts.append(inv4)
    fb = _facts_block(facts or [])
    if fb:
        parts.append(fb)
    fvb = _fields_block(field_values or {})
    if fvb:
        parts.append(fvb)
    eb = str(element_block or "").strip()
    if eb:
        parts.append(eb)
    cb = str(constraints_block or "").strip()
    if cb:
        parts.append(cb)
    tb = template_format_block(template, "l4")
    if tb:
        parts.append(tb)
    sd = (strategy_overrides(template) or {}).get("scene_density")
    if sd:
        parts.append(f"（模板策略：本剧情场景叶子密度每类约 {sd} 条）")
    core_in = "\n".join(p for p in parts if p)
    ch0: dict[str, Any] = {"title": title or "第1章", "core": core_in}
    plan = [str(b).strip() for b in (beats or []) if str(b).strip()]
    if plan:
        ch0["scene_plan"] = plan
    arc = {
        "name": title or "第1章",
        "archetype": archetype or "",
        "parent": "",
        "chapters": [ch0],
    }
    chaps = await _expand_arc_scenes_llm(arc, style, role_setting)
    return [sc for ch in chaps for sc in (ch.get("scenes") or []) if isinstance(sc, dict)]


# ── 阶梯状态 ────────────────────────────────────────────────────────────

def new_state(
    *,
    style: str = "",
    role_setting: str = "",
    archetype: str = "",
    template: dict[str, Any] | None = None,
    l1: str = "",
    n_chapters: int = 1,
    field_values: dict[str, Any] | None = None,
    l1_confirmed: bool | None = None,
) -> dict[str, Any]:
    """新建一次阶梯创作的状态。l1 默认由用户手写（天然已确认）；
    若 l1 由模板字段自动组装（assemble_l1），传 l1_confirmed=False 使其待审阅。

    n_chapters>1 = 「前十章章纲」模式：l2 情节概要 → 一次拆出 N 章 l3 章纲，
    每章单独审阅/确认/修改，可切章下钻 l4/l5。
    """
    l1_confirmed = bool(str(l1 or "").strip()) if l1_confirmed is None else l1_confirmed
    return {
        "id": "bridge_" + uuid.uuid4().hex[:12],
        "template": template,          # 命中的情节模板（格式+策略来源）
        "template_id": (template or {}).get("id"),
        "archetype": str(archetype or "").strip(),
        "style": str(style or "").strip(),
        "role_setting": str(role_setting or "").strip(),
        "n_chapters": max(1, int(n_chapters or 1)),
        "active_chapter": 0,           # 多章模式下当前下钻的章（0-based）
        "key_facts": [],
        "field_values": dict(field_values or {}),   # 【v5.33.2】用户按模板填好的字段值
        "history": [],
        "levels": {
            "l1": {"text": str(l1 or "").strip(), "confirmed": l1_confirmed, "prompt": ""},
            "l2": {"text": "", "confirmed": False, "prompt": ""},
            "l3": {"data": None, "text": "", "confirmed": False, "prompt": ""},
            "l4": {"scenes": [], "text": "", "confirmed": False, "prompt": ""},
            "l5": {"text": "", "confirmed": False, "prompt": ""},
        },
    }


def confirm_level(state: dict[str, Any], level: str) -> dict[str, Any]:
    """确认某级内容（确认后才允许从它继续生成下一级）。"""
    if level not in _LEVEL_ORDER:
        return {"ok": False, "error": f"无效级别：{level}"}
    if not _filled(state, level):
        return {"ok": False, "error": f"{level} 还没有内容，无法确认"}
    state.setdefault("levels", {})[level]["confirmed"] = True
    return {"ok": True, "level": level, "state": state}


def set_active_chapter(state: dict[str, Any], idx: int) -> dict[str, Any]:
    """多章章纲模式下切换当前下钻的章。

    【v7.8.3 自动保存】不再清空 l4/l5——切章前把当前章 l4/l5 按章暂存到
    state["_l45"]{idx: {l4, l5}}，切回时恢复（未落盘的编辑不丢失）。
    首次切到无内容章才清空（_clear_downstream）。
    """
    from copy import deepcopy

    l3d = (state.get("levels") or {}).get("l3", {}).get("data") or {}
    chs = l3d.get("chapters") if isinstance(l3d, dict) else None
    if not isinstance(chs, list) or not chs:
        return {"ok": False, "error": "l3 不是多章章纲（请用 n_chapters>1 生成章纲）", "state": state}
    old_idx = int(state.get("active_chapter") or 0)
    idx = max(0, min(int(idx), len(chs) - 1))
    levels = state.setdefault("levels", {})
    if idx != old_idx:
        bak = state.setdefault("_l45", {})
        bak[str(old_idx)] = {
            "l4": deepcopy(levels.get("l4")),
            "l5": deepcopy(levels.get("l5")),
        }
        saved = bak.pop(str(idx), None)
        if saved:
            levels["l4"] = saved.get("l4") or {"scenes": [], "text": "", "confirmed": False, "prompt": ""}
            levels["l5"] = saved.get("l5") or {"text": "", "confirmed": False, "prompt": ""}
        else:
            _clear_downstream(state, "l3")
    state["active_chapter"] = idx
    return {"ok": True, "active_chapter": idx, "chapter": chs[idx] if isinstance(chs[idx], dict) else {},
            "state": state}


# ── 分步确认制：逐级生成 ───────────────────────────────────────────────

async def step_ladder(state: dict[str, Any]) -> dict[str, Any]:
    """从当前已确认的最高级，生成下一级（一次只进一级，分步确认制）。

    Returns: {ok, to, data, text, prompt, review, state}
      - to: 新生成的一级（l2/l3/l4/l5）
      - data: 该级结构化内容（l3=dict / l4=scenes / 其余=str）
      - text: 该级可读展示文本
      - prompt: 该级生成指令（供对话窗口透明展示）
      - review: l5 才有的 AI 味审阅摘要
    """
    levels = state.setdefault("levels", {})
    template = state.get("template")
    style = str(state.get("style") or "").strip()
    role_setting = str(state.get("role_setting") or "").strip()

    src = None
    for k in _LEVEL_ORDER:
        if _filled(state, k):
            src = k
    if src is None:
        return {"ok": False, "error": "空阶梯：请先在 l1 填写一句话极简剧情"}
    if src == "l5":
        return {"ok": False, "error": "已达正文（l5），无需继续生成"}
    if src != "l1" and not levels[src].get("confirmed"):
        return {"ok": False, "error": f"请先确认当前级 {src} 的内容，再生成下一级"}

    target = _LEVEL_ORDER[_LEVEL_ORDER.index(src) + 1]

    # 【T8 场景决断】题材×场景流行钩子块（静态零成本；注入各档 usr 供生成参考）
    thb = ""
    try:
        from .trend_lib import detect_genre, trend_inject_block, LEVEL_SCENE
        _g_txt = str(levels.get("l1", {}).get("text") or "").strip()
        _g_txt += " " + style + " " + role_setting
        _g = detect_genre(_g_txt)
        _scene = LEVEL_SCENE.get(target, "")
        thb = trend_inject_block(_g, _scene)
    except Exception:  # noqa: BLE001 —— 钩子注入失败绝不影响阶梯生成
        thb = ""
    trend_note = (("\n【流行钩子参考】\n" + thb) if thb else "")

    # 关键事实（l1 首步提取，后续沿用；l1 修改时重提）
    facts = state.get("key_facts") or []
    if not facts:
        from .ladder import extract_key_facts
        facts = await extract_key_facts(str(levels["l1"].get("text") or "").strip())
        state["key_facts"] = facts
    fb = _facts_block(facts)
    fvb = _fields_block(state.get("field_values") or {})   # 【v5.33.2】模板字段锚定
    eb = _element_block_from_state(state)                  # 【AI创作】元素白名单（l3起）
    cb = _constraints_block_from_state(state)              # 【AI创作】硬约束（l3起）
    pa = _prev_anchor_block(state.get("prev_anchor"))      # 【AI创作】前文摘要（l2/l3）
    rb = _roadmap_block_from_state(state)                  # 【T32 P4】路线图方向（仅 l2）

    # 命中模板 → 注入该级格式参考 + 策略
    from .plot_library import strategy_overrides, template_format_block

    # ── 1 → 12：极简 → 情节概要 ─────────────────────────────────────
    if target == "l2":
        l1 = str(levels["l1"].get("text") or "").strip()
        tb = template_format_block(template, "l2")
        sys_p = (
            "你是「情节线拆解专家」。给定一句话极简剧情，扩写成这条情节线的概要"
            "（3-6 句，包含起因/核心冲突/转折/结局，人物与地点齐全）。"
            "**必须具体、细节丰富、可写**：写清关键人物在关键地点做的事、冲突的具体来龙去脉、"
            "标志性场景/物品/数字，不写抽象总结、不写『他决定去调查』这类空话——要写出"
            "『他在哪、对谁、做了什么、冲突卡在哪』。这一级要为下一级拆章纲提供足够细节，"
            "宁可多写几句也不要潦草概括；遇到可写的事件要展开写清因果。"
        )
        usr_p = (
            "极简剧情：\n" + l1
            + (("\n风格基调：" + style) if style else "")
            + (("\n角色设定：" + role_setting) if role_setting else "")
            + (("\n" + fb) if fb else "")
            + (("\n" + fvb) if fvb else "")
            + (("\n" + tb) if tb else "")
            + (("\n" + pa) if pa else "")
            + (("\n\n" + rb) if rb else "")
            + (("\n\n" + _ladder_invariant_block("l2")) if _ladder_invariant_block("l2") else "")
            + (trend_note if trend_note else "")
            + "\n\n严格输出如下 JSON（不要 Markdown 代码块）：\n"
            '{"summary": "2-4 句情节概要"}'
        )
        d = await _tree_llm(sys_p, usr_p, "bridge_l2", max_tokens=1000)
        l2 = str((d.get("summary") if isinstance(d, dict) else None) or "").strip() or l1
        _clean = await _ai_flavor_clean_text(l2, "情节概要", "bridge_l2_depol")   # 【AI味防线】脏则修正
        l2 = _clean["text"]
        ai_review = _clean["review"]
        levels["l2"] = {"text": l2, "confirmed": False, "prompt": sys_p + "\n" + usr_p,
                        "ai_review": ai_review}
        state["history"].append({"step": "l1→l2", "at": _now(), "len": len(l2)})
        return {"ok": True, "to": "l2", "data": l2, "text": l2, "review": ai_review,
                "prompt": levels["l2"]["prompt"], "state": state}

    # ── 12 → 123：情节概要 → 章核心（多章章纲：n_chapters>1 一次拆 N 章）──
    if target == "l3":
        l2 = str(levels["l2"].get("text") or "").strip()
        tb = template_format_block(template, "l3")
        n = max(1, int(state.get("n_chapters") or 1))
        sys_p = (
            "你是「章节拆解专家」。给定情节概要，"
            + (f"拆成 {n} 章章纲（按时序推进，每章一个独立事件段）" if n > 1 else "拆成单章核心")
            + "：每章给标题、一句话核心、以及本章的**场景切分计划**（beats）。保留重建场景所需的全部关键信息"
            "（人物/事件/冲突/结局），用大白话、具体可写。"
            "**beats 每一条 = 本章的一个场景**（拍数即本章场景数，一般 3-6 条），格式："
            "「场景名：谁在该场景做了什么 + 冲突回合 + 标志性物品/数字」。"
            "**每一拍必须具体到可直接展开成一个完整场景**：写明关键人物在该处的动作/反应、"
            "发生了什么具体事件、有无对白苗头或冲突回合，不写『主角了解到真相』这类概括——"
            "要写『主角在何处、从谁口中、听到哪句关键信息、做了什么反应』。"
            "下一级展开场景时将严格按这份切分逐个场景进行，宁可多写也不要潦草概括。"
        )
        usr_p = (
            "情节概要：\n" + l2
            + (("\n" + fb) if fb else "")
            + (("\n" + fvb) if fvb else "")
            + (("\n" + tb) if tb else "")
            + (("\n" + eb) if eb else "")
            + (("\n" + cb) if cb else "")
            + (("\n" + pa) if pa else "")
            + (("\n\n" + _ladder_invariant_block("l3")) if _ladder_invariant_block("l3") else "")
            + (trend_note if trend_note else "")
            + "\n\n严格输出如下 JSON（不要 Markdown 代码块）：\n"
            + ('{"chapters": [{"title": "第1章 …", "core": "…", "beats": ["…"]}, …]}'
               if n > 1 else
               '{"title": "第1章 …", "core": "一句话核心", "beats": ["拍1", "拍2", "拍3"]}')
        )
        if n > 1:
            d = await _tree_llm(sys_p, usr_p, "bridge_l3_multi", max_tokens=4000)
            chs: list[dict[str, Any]] = []
            for ch in (d.get("chapters") if isinstance(d, dict) else []) or []:
                if not isinstance(ch, dict):
                    continue
                chs.append({
                    "title": str(ch.get("title") or f"第{len(chs) + 1}章").strip(),
                    "core": str(ch.get("core") or "").strip(),
                    "beats": [str(b).strip() for b in (ch.get("beats") or []) if str(b).strip()],
                })
            if not chs:
                return {"ok": False, "error": "多章章纲拆解失败：无 chapters", "state": state}
            data = {"chapters": chs}
        else:
            d = await _tree_llm(sys_p, usr_p, "bridge_l3", max_tokens=2000)
            title = str((d.get("title") if isinstance(d, dict) else "") or "第1章").strip()
            core = str((d.get("core") if isinstance(d, dict) else "") or "").strip()
            beats = ([str(b).strip() for b in (d.get("beats") or []) if str(b).strip()]
                     if isinstance(d, dict) else [])
            data = {"title": title, "core": core, "beats": beats}
        l3_text = _render_l3(data)
        _clean = await _ai_flavor_clean_text(l3_text, "章核心/章纲", "bridge_l3_depol")   # 【AI味防线】脏则修正
        l3_text = _clean["text"]
        ai_review = _clean["review"]
        levels["l3"] = {"data": data, "text": l3_text, "confirmed": False,
                        "prompt": sys_p + "\n" + usr_p, "ai_review": ai_review}
        n_chs = len(data.get("chapters") or []) if isinstance(data, dict) else 1
        state["history"].append({"step": "l2→l3", "at": _now(), "chapters": n_chs if n > 1 else 1})
        return {"ok": True, "to": "l3", "data": data, "text": l3_text, "review": ai_review,
                "prompt": levels["l3"]["prompt"], "state": state}

    # ── 123 → 1234：章核心 → 场景分解（当前章，多章模式取 active_chapter）──
    if target == "l4":
        l3ch, ch_idx = _active_chapter(state)
        title = str(l3ch.get("title") or "第1章").strip()
        core = str(l3ch.get("core") or "").strip()
        scenes = await _arc_to_scenes(
            title, core, style, role_setting,
            archetype=str(state.get("archetype") or "").strip(),
            template=template, facts=facts,
            field_values=state.get("field_values") or {},
            element_block=state.get("element_block") or "",
            constraints_block=state.get("constraints_block") or "",
            trend_block=thb,
            beats=[str(b) for b in (l3ch.get("beats") or [])],
        )
        if not scenes:
            return {"ok": False, "error": "场景展开失败：无场景", "state": state}
        l4_text = _render_l4(scenes)
        _clean = await _ai_flavor_clean_text(l4_text, "场景分解", "bridge_l4_depol")   # 【AI味防线】脏则修正
        l4_text = _clean["text"]
        ai_review = _clean["review"]
        levels["l4"] = {
            "scenes": scenes, "text": l4_text, "confirmed": False, "ai_review": ai_review,
            "prompt": f"场景拆解：第 {ch_idx + 1} 章 3-5 场景 × 6 类叶子（模板格式参考已注入，共 {len(scenes)} 场景）",
        }
        state["history"].append({"step": "l3→l4", "at": _now(), "scenes": len(scenes), "chapter": ch_idx + 1})
        return {"ok": True, "to": "l4", "data": scenes, "text": l4_text, "review": ai_review,
                "prompt": levels["l4"]["prompt"], "state": state}

    # ── 1234 → 12345：场景 → 正文（场景级生成 + AI 味审阅）─────────
    if target == "l5":
        scenes = levels["l4"].get("scenes") or []
        if not scenes:
            return {"ok": False, "error": "l4 场景为空，无法生成正文", "state": state}
        l3ch, ch_idx = _active_chapter(state)
        title = str(l3ch.get("title") or "第1章").strip()
        core0 = str(l3ch.get("core") or "").strip()
        l2 = str(levels["l2"].get("text") or "").strip()
        core = (core0
                + (("\n【情节概要】" + l2) if l2 else "")
                + (("\n" + fvb) if fvb else "")
                + (("\n" + eb) if eb else "")
                + (("\n" + nb) if (nb := str(state.get("notes_block") or "").strip()) else "")
                + (("\n" + frb) if (frb := str(state.get("fragments_block") or "").strip()) else "")
                + (("\n" + cb) if cb else "")
                + (("\n" + pf) if (pf := str(state.get("pollution_feedback") or "").strip()) else "")
                + (("\n\n【流行钩子参考】\n" + thb) if thb else "")).strip()
        strat = strategy_overrides(template)
        retry_cap = max(1, int(strat.get("retry_cap") or 0) or 2)
        from .config import SETTINGS as _s   # 【v5.33.1】函数内取最新 SETTINGS
        tpl = float(strat.get("target_len_per_scene") or 0) or float(
            getattr(_s, "derive_scene_target_len", 600))
        from .ladder import generate_chapter_prose
        _onames, _oobjs = _open_names_from_block(state.get("element_block") or "")  # v6.4
        gres = await generate_chapter_prose(
            scenes,
            chapter_title=title, core=core,
            style=style, role_setting=role_setting,
            system_prompt="",
            target_len_per_scene=int(tpl),
            ai_flavor=True,
            target_text=None,
            retry_cap=retry_cap,
            open_names=_onames, open_objects=_oobjs,
        )
        text = gres.get("text") or ""
        levels["l5"] = {
            "text": text, "confirmed": False,
            "prompt": f"场景级正文生成（{len(scenes)} 场景）→ 拼接 → AI 味审阅"
                      f"（retry_cap={retry_cap}）",
        }
        state["history"].append({"step": "l4→l5", "at": _now(), "len": len(text)})
        review = gres.get("review")
        return {"ok": True, "to": "l5", "data": text, "text": text,
                "prompt": levels["l5"]["prompt"], "review": review, "state": state}

    return {"ok": False, "error": f"未识别的目标级：{target}", "state": state}


# ── 对话修改当前级 ─────────────────────────────────────────────────────

async def modify_level(state: dict[str, Any], level: str, instruction: str) -> dict[str, Any]:
    """LLM 按用户意见改写该级内容（保持格式），清空下游级。

    l1/l2 文本改写；l3 重出 JSON 章核心；l4 把意见并入 core 重跑场景拆解。
    """
    if level not in ("l1", "l2", "l3", "l4"):
        return {"ok": False, "error": f"不支持的级：{level}"}
    instruction = str(instruction or "").strip()
    if not instruction:
        return {"ok": False, "error": "请填写修改意见"}
    levels = state.setdefault("levels", {})
    template = state.get("template")
    style = str(state.get("style") or "").strip()
    role_setting = str(state.get("role_setting") or "").strip()
    eb = _element_block_from_state(state)                  # 【AI创作】元素白名单（l3起）
    cb = _constraints_block_from_state(state)              # 【AI创作】硬约束（l3起）
    pa = _prev_anchor_block(state.get("prev_anchor"))      # 【AI创作】前文摘要（l3）

    # ── l1 / l2：文本改写 ────────────────────────────────────────────
    if level in ("l1", "l2"):
        cur = str(levels[level].get("text") or "").strip()
        if not cur:
            return {"ok": False, "error": f"{level} 还没有内容可修改"}
        sys_p = (
            "你是「小说剧情编辑」。按用户的修改意见改写给定的{level}内容。"
            "只改动用户要求的部分，保持体裁、详略与整体结构；"
            "不要输出解释、不要 Markdown 代码块，直接输出改写后的完整内容。"
        ).replace("{level}", "一句话极简" if level == "l1" else "情节概要")
        usr_p = (
            "【当前内容】\n" + cur
            + "\n\n【修改意见】\n" + instruction
            + "\n\n直接输出修改后的完整内容："
        )
        r = await chat_completion(
            system=sys_p, user=usr_p, call_type=f"bridge_modify_{level}",
            temperature=0.5, max_tokens=2000,
        )
        if r.get("error") or not (r.get("content") or "").strip():
            return {"ok": False, "error": "LLM 修改失败：" + str(r.get("error") or "空输出")}
        new_text = _strip_code_fence(r.get("content") or "")
        if not new_text:
            return {"ok": False, "error": "LLM 修改失败：空输出"}
        ai_review = await _measure_ai_flavor(new_text)   # 【AI味防线】文生文每步 LLM 测 AI 率
        levels[level] = {"text": new_text, "confirmed": False, "prompt": f"对话修改：{instruction}",
                         "ai_review": ai_review}
        if level == "l1":
            from .ladder import extract_key_facts
            state["key_facts"] = await extract_key_facts(new_text)
        _clear_downstream(state, level)
        state["history"].append({"modify": level, "at": _now(), "instruction": instruction})
        return {"ok": True, "level": level, "data": new_text, "text": new_text,
                "review": ai_review, "state": state}

    # ── l3：章核心 JSON 改写（多章模式只改当前章）──────────────────
    if level == "l3":
        l3ch, ch_idx = _active_chapter(state)
        cur = _render_l3(l3ch) if l3ch else ""
        if not cur:
            return {"ok": False, "error": "l3 还没有内容可修改"}
        fb = _facts_block(state.get("key_facts") or [])
        fvb = _fields_block(state.get("field_values") or {})
        from .plot_library import template_format_block
        tb = template_format_block(template, "l3")
        sys_p = (
            "你是「章节拆解专家」。根据用户的修改意见改写给定章核心，"
            "保持 JSON 结构（title/core/beats），保留重建场景所需的全部关键信息。"
        )
        usr_p = (
            "【当前章核心】\n" + cur
            + "\n\n【修改意见】\n" + instruction
            + (("\n" + fb) if fb else "")
            + (("\n" + fvb) if fvb else "")
            + (("\n" + tb) if tb else "")
            + (("\n" + eb) if eb else "")
            + (("\n" + cb) if cb else "")
            + (("\n" + pa) if pa else "")
            + "\n\n严格输出如下 JSON（不要 Markdown 代码块）：\n"
            '{"title": "…", "core": "…", "beats": ["…"]}'
        )
        d = await _tree_llm(sys_p, usr_p, "bridge_modify_l3", max_tokens=2000)
        title = str((d.get("title") if isinstance(d, dict) else "") or "").strip()
        core = str((d.get("core") if isinstance(d, dict) else "") or "").strip()
        beats = ([str(b).strip() for b in (d.get("beats") or []) if str(b).strip()]
                 if isinstance(d, dict) else [])
        data2 = {"title": title, "core": core, "beats": beats}
        data = levels["l3"].get("data") or {}
        if isinstance(data, dict) and isinstance(data.get("chapters"), list):
            # 多章：只替换当前章
            data["chapters"][ch_idx] = data2
            levels["l3"]["data"] = data
        else:
            data2_whole = {"chapters": [data2]} if (state.get("n_chapters") or 1) > 1 else data2
            levels["l3"]["data"] = data2_whole
        l3_new = _render_l3(levels["l3"]["data"])
        ai_review = await _measure_ai_flavor(l3_new)   # 【AI味防线】文生文每步 LLM 测 AI 率
        levels["l3"]["text"] = l3_new
        levels["l3"]["confirmed"] = False
        levels["l3"]["prompt"] = f"对话修改：{instruction}"
        levels["l3"]["ai_review"] = ai_review
        _clear_downstream(state, "l3")
        state["history"].append({"modify": "l3", "at": _now(), "instruction": instruction,
                                 "chapter": ch_idx + 1})
        return {"ok": True, "level": "l3", "data": levels["l3"]["data"],
                "text": l3_new, "review": ai_review, "state": state}

    # ── l4：意见并入 core 重跑场景拆解（当前章）────────────────────
    l3ch, ch_idx = _active_chapter(state)
    title = str(l3ch.get("title") or "第1章").strip()
    core = str(l3ch.get("core") or "").strip()
    if not core:
        return {"ok": False, "error": "请先生成 l3 章核心，再修改场景"}
    scenes = await _arc_to_scenes(
        title, core, style, role_setting,
        archetype=str(state.get("archetype") or "").strip(),
        template=template, facts=state.get("key_facts") or [],
        field_values=state.get("field_values") or {},
        extra=instruction,
        element_block=state.get("element_block") or "",
        constraints_block=state.get("constraints_block") or "",
        beats=[str(b) for b in (l3ch.get("beats") or [])],
    )
    if not scenes:
        return {"ok": False, "error": "场景重拆失败：无场景"}
    l4_new = _render_l4(scenes)
    ai_review = await _measure_ai_flavor(l4_new)   # 【AI味防线】文生文每步 LLM 测 AI 率
    levels["l4"] = {"scenes": scenes, "text": l4_new, "confirmed": False,
                    "prompt": f"对话修改：{instruction}", "ai_review": ai_review}
    _clear_downstream(state, "l4")
    state["history"].append({"modify": "l4", "at": _now(), "instruction": instruction})
    return {"ok": True, "level": "l4", "data": scenes, "text": l4_new,
            "review": ai_review, "state": state}


# ── 对话窗口后端 ───────────────────────────────────────────────────────

def _state_context(state: dict[str, Any]) -> str:
    """把当前阶梯各级内容压成一段上下文（供对话窗口）。"""
    levels = (state or {}).get("levels") or {}
    parts = []
    fvb = _fields_block(state.get("field_values") or {})
    if fvb:
        parts.append(fvb)
    for k in _LEVEL_ORDER:
        lv = levels.get(k) or {}
        if k == "l3":
            txt = _render_l3(lv.get("data") or {})
        elif k == "l4":
            txt = _render_l4(lv.get("scenes") or [])
        else:
            txt = str(lv.get("text") or "").strip()
        if txt:
            cf = "已确认" if lv.get("confirmed") else "待确认"
            parts.append(f"[{k} {cf}]\n{txt}")
    return "\n\n".join(parts)


async def chat_turn(
    messages: list[dict[str, Any]],
    state: dict[str, Any] | None = None,
    template: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """对话窗口后端：LLM 结合当前阶梯各级内容 + 命中模板策略，回答/建议。

    messages: [{role: "user"|"assistant", content}] 对话历史（最多取最近 12 条）。
    不改动阶梯内容（修改走 modify_level）；可给出「修改后的某级内容」供用户确认。
    """
    if template is None and isinstance(state, dict):
        template = state.get("template")
    ctx = _state_context(state) if isinstance(state, dict) else ""
    tpl_block = ""
    if isinstance(template, dict):
        from .plot_library import strategy_overrides
        d = str(template.get("description") or "").strip()
        strat = strategy_overrides(template)
        s = "，".join(f"{k}={v}" for k, v in strat.items() if v not in (None, ""))
        tpl_block = (
            "【命中情节模板】" + (d or "（无描述）") + (("；策略：" + s) if s else "")
        )
    hist: list[str] = []
    for m in (messages or [])[-12:]:
        role = "用户" if m.get("role") == "user" else "助手"
        hist.append(f"{role}：{str(m.get('content') or '').strip()}")
    sys_p = (
        "你是资深小说编辑兼导演，正在协助用户逐级创作一部小说。"
        "创作走五级阶梯：l1 极简 → l2 情节概要 → l3 章核心 → l4 场景分解 → l5 正文，"
        "每一级都要用户审阅确认后才能继续。\n"
        "你的职责：评点当前级内容、指出可改进处、给出具体修改建议。"
        "当用户想修改某级时，直接给出修改后的该级内容并说明改动点"
        "（前端会用「修改」操作落地，你不用推进下一级）。"
        "不要替用户决定下一级内容（推进由用户确认后触发生成）。"
    )
    usr_p = "\n\n".join([
        ("【当前阶梯各级内容】\n" + ctx) if ctx else "（阶梯还没有内容）",
        tpl_block,
        "【对话历史】\n" + ("\n".join(hist) if hist else "（开始）"),
    ])
    r = await chat_completion(
        system=sys_p, user=usr_p, call_type="bridge_chat", temperature=0.7, max_tokens=1000,
    )
    if r.get("error"):
        return {"ok": False, "error": str(r.get("error"))}
    return {"ok": True, "reply": (r.get("content") or "").strip()}


# _tree_llm 从 derive 惰性引入（避免顶层 import 循环）
async def _tree_llm(system: str, user: str, call_type: str, max_tokens: int) -> dict[str, Any]:
    from .derive import _tree_llm as _tl
    return await _tl(system, user, call_type, max_tokens)
