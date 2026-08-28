# -*- coding: utf-8 -*-
"""v5.32 生成阶梯（剧情推导重构第一步）：场景级正文生成。

解决「极简→推导树→正文」整章一次性生成只有 ~833 字（《示例书》原文每章实测
2600-3100 字）的问题：把最后一级「场景分解(1234)→正文(12345)」从整章一次
生成改为**逐场景生成**——每场景写足 ~N 字 + 长度不足重生成 + 拼接 + 整章
AI 味审阅循环。中间层级（弧线/章/场景）复用现有 derive 展开，本模块只管
「场景→正文」这一级的生成质量与篇幅。

入口：
- generate_scene_prose(scene, ...) -> {text, prompt, target_len, retried}
- generate_chapter_prose(scenes, ...) -> {text, per_scene, total_len, retried, review}
- build_scene_prompt(scene, ...) -> str（单场景生成 user prompt，供调用方复用）
- build_generation_input(scenes, ...) -> [每场景生成指令]（场景级路径替代整章 user_input）
"""
from __future__ import annotations

import re
from typing import Any, Callable

from .ai_flavor import ai_flavor_ban_block, build_ai_flavor_feedback, to_dialogue_quotes
from .ai_flavor import paragraph_open_diversity_detector, overdetail_detector, punct_detector
from .optimizer import forward_generation_v4

# 6 类叶子字段与标签（与 derive._LEAF_FIELDS 一致；environment 是单串，其余是 list）
_LEAF_LABELS = (
    ("environment", "环境"),
    ("actions", "动作"),
    ("dialogues", "对白"),
    ("psychologies", "心理"),
    ("conflicts", "冲突"),
    ("details", "细节"),
)

_DEFAULT_GEN_PARAMS = {
    "temperature": 0.3,
    "top_p": 0.7,
    # 【v6.4】段落开头机械重复（ch3 豆腐摊×2 / 沈石 44%）→ 调高采样惩罚抑制重复词。
    # presence 鼓励新主题、frequency 惩罚已出现的词（0.1 几乎不生效，doubao 支持范围 [-0.5,1]）。
    "presence_penalty": 0.3,
    "frequency_penalty": 0.4,
    "max_tokens": 4096,
}


def _last_sentence(text: str, max_len: int = 60) -> str:
    """取上一场景末尾一句（供衔接），最多 max_len 字。"""
    t = (text or "").strip()
    if not t:
        return ""
    parts = re.split(r"(?<=[。！？])", t)
    tail = ""
    for p in reversed(parts):
        p = p.strip()
        if p:
            tail = p
            break
    if len(tail) > max_len:
        tail = tail[-max_len:]
    return tail


_LEAD_TAIL_RESIDUE_RE = re.compile(
    r"^\s*[…“]*[^。！？\n]{2,20}说[。！？]?[”]?\s*\n"  # 开头孤立的「…”陈硕说。」残句
)


def _strip_lead_tail_residue(text: str) -> str:
    """删除场景拼接处开头被误抄的上一场景末句残片（如首行是「…”陈硕说。」）。

    prev_tail 是衔接提示，模型偶会把『…上一场景末句』原样抄进本场景正文开头，
    形成孤立残句（首行极短、以…/引号开头、以『说』结尾、无新动作）。确定性删除。
    """
    t = (text or "").strip()
    if not t:
        return t
    # 最多清理 2 行；只删"像引文残片"的行（含 … 或 孤零零的「XX说。」）
    lines = t.split("\n")
    cut = 0
    for ln in lines[:3]:
        s = ln.strip()
        if not s:
            cut += 1
            continue
        # 残片特征：以 … 开头；或整体是「说。」/「道。」形式的极短行（≤15 字且不以 。 结尾实词）
        is_residue = s.startswith("…") or (
            len(s) <= 15 and s.endswith("说。") and s.count("“") == 0 and "，" not in s
        )
        if is_residue:
            cut += 1
        else:
            break
    if cut:
        rest = "\n".join(lines[cut:]).strip()
        return rest if rest else t
    return t


def _reparagraph_scene(text: str, max_para: int = 120) -> str:
    """保证场景散文是网文式短段（~1-2 句/段，对白轮独立成段）。

    结构评分 v_cos 对段落结构敏感（median_paragraph_len / sentences_per_paragraph /
    paragraph_frequency）：重建正文若整段连续（场景间 \n\n 让 _split_paragraphs 把
    整个场景当一段），结构向量会与原文严重偏离（实测中位段长 1215 vs 原文 28.5）。
    此处按句界把超长段拆成短段，全章统一用单换行分段的网文排版。
    """
    import re as _re
    if not text:
        return text
    blocks = [b.strip() for b in _re.split(r"\n+", text) if b.strip()]
    out: list[str] = []
    for b in blocks:
        if len(b) <= max_para:
            out.append(b)
            continue
        # 按句界拆：…。！？ 及其后可能的中文右引号
        sents = _re.findall(r"[^。！？…]*[。！？…](?:\s*[”』」])?|[^。！？…]+", b)
        sents = [s.strip() for s in sents if s.strip()]
        cur: list[str] = []
        for s in sents:
            if "“" in s or "”" in s or "「" in s or "」" in s:
                if cur:
                    out.append("".join(cur))
                    cur = []
                out.append(s)
            else:
                cur.append(s)
                if len(cur) >= 2:
                    out.append("".join(cur))
                    cur = []
        if cur:
            out.append("".join(cur))
    return "\n".join(out)


def _scene_to_blocks(scene: dict[str, Any]) -> str:
    """把场景 dict 的 6 类叶子序列化为素材块文本（对白统一 “”）。"""
    lines: list[str] = []
    name = str(scene.get("name") or "场景").strip()
    lines.append(f"【场景】{name}")
    env = scene.get("environment")
    if isinstance(env, str) and env.strip():
        lines.append(f"环境：{env}")
    elif isinstance(env, list) and env and str(env[0]).strip():
        lines.append(f"环境：{str(env[0]).strip()}")
    # 【v5.33.3】原文叙述原句（从原文逐字提取）——重建时必须原样采用
    nar = scene.get("narration")
    nar_items = [str(x).strip() for x in nar if str(x).strip()] if isinstance(nar, list) else []
    if nar_items:
        lines.append("【原文叙述·原样采用】")
        lines.extend(f"{i + 1}. {to_dialogue_quotes(x)}" for i, x in enumerate(nar_items, 1))
    for key, label in _LEAF_LABELS:
        if key == "environment":
            continue
        v = scene.get(key)
        if isinstance(v, str):
            v = [v]
        items = [str(x).strip() for x in v if str(x).strip()] if isinstance(v, list) else []
        if not items:
            continue
        lines.append(f"{label}：")
        lines.extend(f"{i + 1}. {to_dialogue_quotes(it)}" for i, it in enumerate(items))
    return "\n".join(lines)


def _scene_dialogue_contract(scene: dict[str, Any]) -> str:
    """该场景对白按序编号 → 顺序契约块（生成必须按序复现）。"""
    v = scene.get("dialogues")
    items = [str(x).strip() for x in v if str(x).strip()] if isinstance(v, list) else []
    if not items:
        return ""
    numbered = "；".join(f"{i}.{to_dialogue_quotes(t)}" for i, t in enumerate(items, 1))
    return f"【对白顺序契约】本场景对白按序复现（不得提前/延后/遗漏）：{numbered}"


def _adaptive_target_len(scene: dict[str, Any], *, cap: int = 800, floor: int = 250,
                         fixed: int | None = None) -> int:
    """【v5.32.6】按素材丰度估算本场景可诚实写足的字数。

    素材薄（如门口场景只有 3 轮对白）时仍要求 600 字 → 模型只能越界注水
    （进门后搜房本、提前写黑衣人）。字数主体=对白轮往来（~90 字/轮），
    动作/冲突/细节是过场（少量），薄场景退到 floor。

    fixed 提供时（verify_ladder 重建：按原文长度/场景数定的密度上限），作为**上限**，
    取「素材丰度估算」与「原文密度上限」的较小值——薄场景（纯思考/过场）诚实写短，
    不让原文密度把目标拔高 → 否则模型只能编造对话凑字（实测思考场景编出整段探监戏）。
    """
    if not isinstance(scene, dict):
        return cap

    def n(k: str) -> int:
        v = scene.get(k)
        return len([x for x in v if str(x).strip()]) if isinstance(v, list) else 0

    dlg = n("dialogues")
    # 【A2 2026-08-15】narration 原文叙述原句是章节正文主体，必须计入预算——
    # 否则重建目标只有原文 39%（len_ratio 0.39），s_char(n-gram包含率)被长度物理压死。
    narr = scene.get("narration") or []
    narr_len = sum(len(str(s)) for s in narr if str(s).strip())
    est = (90 * dlg + 25 * min(n("actions"), 5) + 20 * n("conflicts") + 15 * n("details")
           + narr_len)
    if fixed is not None:
        est = min(est, int(fixed))
    return max(floor, min(cap, int(est)))


def build_scene_prompt(
    scene: dict[str, Any],
    chapter_title: str = "",
    core: str = "",
    style: str = "",
    role_setting: str = "",
    prev_tail: str = "",
    target_len: int = 600,
    extra_feedback: str | None = None,
) -> str:
    """组装单场景正文生成的 user prompt。

    素材逐条列出 + 对白顺序契约 + 写足字数要求 + 独立版 AI 味禁令 + 骨架忠实度。
    extra_feedback：整章审阅的【AI味修正块】注入（脏章整体重生成）。
    """
    blocks = _scene_to_blocks(scene)
    contract = _scene_dialogue_contract(scene)
    ban = ai_flavor_ban_block(standalone=True)
    style_hint = f"\n风格基调：{style.strip()}" if (style or "").strip() else ""
    role_hint = f"\n角色设定：{role_setting.strip()}" if (role_setting or "").strip() else ""
    title_hint = f"\n【章节】{chapter_title.strip()}" if (chapter_title or "").strip() else ""
    core_hint = f"\n【本章核心】{core.strip()}" if (core or "").strip() else ""
    if (prev_tail or "").strip():
        prev_hint = (
            "开头与上一场景末尾自然衔接（上一场景末尾是『…" + prev_tail.strip() + "』）。"
            "**上一场景末尾只作衔接提示，不得原样抄进本场景正文开头**——直接从本场景现场写起，"
            "不要重复上一场景已写过的句子；不写走路/赶路/查看装备等过场过渡。"
        )
    else:
        prev_hint = (
            "本场景是本章第一个场景，**从场景现场直接开始写**——第一句就进入场景事件本身，"
            "**不得先写主角如何走到现场**（不写巡街赶路、不写更梆/打更灯/锣等装备细节、"
            "不写沿途景物与感官氛围）。"
        )
    feedback = f"\n\n{extra_feedback}" if (extra_feedback or "").strip() else ""
    from .fixed_prompts import get_ladder_invariant  # v6.3 l5 不变prompt
    inv5 = get_ladder_invariant("l5")
    # 【v5.33.3】仅当场景带原文叙述原句（来自原文提取）才强令原样采用；模板驱动场景无此字段不受影响
    _nar = scene.get("narration")
    _nar_items = [str(x).strip() for x in _nar if str(x).strip()] if isinstance(_nar, list) else []
    verbatim_hint = (
        "\n- **【原文叙述】逐句全收**：素材里『原文叙述·原样采用』列出的每一句都必须原样"
        "出现在正文对应位置（一条不漏；整体照用，不得改写/缩写/换说法/打散重排）；"
        "只有衔接过渡处才写自己的句子。"
        if _nar_items else ""
    )

    return (
        "这是正文章节中的一段，请续写完整的正文段落。"
        f"{title_hint}{core_hint}{style_hint}{role_hint}"
        f"\n\n本场景素材（必须逐条落实）：\n{blocks}"
        + ("\n素材中的 [xxx] 是留空占位：正文对应位置按 xxx 的意图填充（如 [凶手的真实身份…] → 写出凶手真实身份的具体内容），不要把 [xxx] 原样写进正文。"
           if "[" in blocks else "")
        + (f"\n\n{contract}" if contract else "")
        + "\n\n写作要求：\n"
        f"- 把本场景写成完整的小说正文段落，写足约 {target_len} 字"
        f"（不少于 {int(target_len * 0.8)} 字），不要压缩情节。\n"
        f"- **字数来源（详略导向）**：字数主要来自**对白轮的往来**和**冲突的回合**"
        "（每轮对白往来、每个冲突回合都写充分），其次来自关键动作展开；"
        "过程性动作、环境、物件细节是过场，**合计不超过本场景篇幅的 1/3**，"
        "绝不靠它们凑字数。\n"
        "- 逐条落实素材：对白用“”引号、严格按序出现；冲突要有来有往的回合；"
        "**说话动词/神态逐轮采用素材原词**（素材写『王慧兰阴阳怪气道』『陈硕得意洋洋』"
        "『王慧兰喜滋滋的挽住陈硕胳膊』就照用），"
        "**禁止把多轮对白的说话动词统一占位成『开口说』『说道』『说』**，"
        "相邻对白轮不得重复同一说话动词；"
        "关键动作（推动剧情/人物冲突/引出对白的）展开写，**过程性动作（翻找/收拾/走路/开门）"
        "用一句话概括带过，不得逐微动作平铺成流水账**（如『把能翻的地方都翻遍了，什么也没找到』，"
        "不要『拉开抽屉→翻床底→摸床垫缝→开衣柜』逐条写）。\n"
        "- **素材里已是具体描述的（动作/环境/细节含物品、数字、称呼、原句），正文尽量原样采用这些措辞**"
        "（如素材写『林川上周刚换锁时贴的小标签』，正文就用这个说法），不要改写成别的表述；对白按契约原话。\n"
        f"{verbatim_hint}\n"
        "- **叙述克制**：复述素材时直接呈现动作与对白，不要补心理注释/动机分析/情绪说明"
        "（不写『她心里不满…只觉得…说不出的…』『他心里咯噔一下』这类内心补白），"
        "内心状态用对白与动作传达。\n"
        "- **禁过度心理/微动作解读**：不写『指尖攥得发白，眼圈泛红，硬撑着体面』『情绪压肩，"
        "背脊绷得发颤』『心头受到触动』这类对神态/肢体/内心逐层拆解的解读；不写『他知道…所以…』"
        "式动机分析。神态只用对白素材里已写的说话神态，其余靠对白与动作本身呈现。\n"
        "- **细节必须有推进作用**：细节要么推动情节、要么刻画人物、要么引向冲突；"
        "对这三者都无作用的物件/环境描写（如『踢脚线看不到积灰』『皮面没有开裂』"
        "『垃圾桶堆着废纸和空饮料瓶』『扶手擦得光亮能映出人影』）一律不写。\n"
        "- **禁止否定式反复查找**：『翻A→没找到→翻B→没找到→翻C→没找到』这类同一动作+"
        "否定结果重复 3 次以上 → 合并为一次概括（如『整个二楼翻遍也没找着』），"
        "不要连续罗列『没看见…也没找到…什么都没摸到…也没见着』。\n"
        "- **镜头主次**：一个场景聚焦 1-2 个重点（人/物/冲突），其余一带而过；"
        "不要每个物件都转一圈、每扇门都开一遍、每个角落都摸一遍。\n"
        "- **同一信息只写一次（禁复述/闪回）**：素材里已写、或上一场景已写过的内容，"
        "不得改写成新的回忆/闪回/内心独白再写一遍（如门口对白已提『嫂子烧烤显摆院子』，"
        "客厅就不得再站窗边看烧烤台回忆当年；素材把借钱往事写了一次就只写一次）。"
        "对白里自然的来回问答不算复述。\n"
        f"- {prev_hint}\n"
        "- 不要添加素材之外的主要情节；**不得虚构素材之外的人物身份/职业/背景/来历**"
        "（素材说林川是学生、被送进精神病院，就写学生/病人；不要脑补『跑去医院当全职医生』"
        "『白大褂』这类素材没有的设定）。\n"
        "- **具体设定宁缺毋错**：素材没写明的官职级别/案发经过细节/人物出身来历，正文不得自行补全"
        "（素材写『二叔是御刀卫七品绿袍』就写七品绿袍，不得改成『御前金吾卫千户』；"
        "素材没写案发经过，正文就不要具体写『漕运/江南/通州码头/劫匪』这类细节——"
        "宁可含糊带过，不可编错）。\n"
        "- **禁止排比罗列与句式杂糅**：不得把多个物件/环境堆成「头顶悬着X，客厅摆放着Y，"
        "茶几上摆放着Z」式同构并列句；不得让连续两句用同一句式、同一词收尾（如两句都以"
        "『门前/前』结尾）；物件清单（书籍/证书等）不要干瘪堆叠，在动作/对白中自然带出"
        "或只提一个关键物。\n"
        "- **时序纪律（只写本场景这一段）**：本场景素材是本章时序中的一段，只写素材对应"
        "时间段内发生的事。【本章核心】只是整章走向提示，**不是本场景的事件清单**——素材之外、"
        "更靠后的情节（如放贷人袍哥上门收房、掰断手指、道出真相）属于后续场景，不得在本场景"
        "提前写入。收尾停在素材结束处，由后续场景承接，不要跳到后续事件。\n"
        "- **收尾停在素材末条**：场景（及本章）收尾停在素材最后一条（对白契约最后一轮）处，"
        "不要续写素材之后的内容——原文结尾若是问句/悬念就停在问句（如『你能弄到卷宗吗？』"
        "就到此为止，不要编造对方如何回答）。\n\n"
        + (f"{inv5}\n\n" if inv5 else "")
        + f"{ban}\n\n"
        "【骨架忠实度】逐条复现：每条素材都必须写到，顺序不得改变，不得省略，"
        "不得增加骨架外的主要情节。"
        f"{feedback}"
    )


async def generate_scene_prose(
    scene: dict[str, Any],
    *,
    chapter_title: str = "",
    core: str = "",
    style: str = "",
    role_setting: str = "",
    prev_tail: str = "",
    target_len: int = 600,
    system_prompt: str = "",
    gen_params: dict[str, Any] | None = None,
    extra_feedback: str | None = None,
) -> dict[str, Any]:
    """单场景正文生成：写足 ~target_len 字；长度不足重生成一次。

    复用 forward_generation_v4（自动带 baseline_guard + 出口 to_dialogue_quotes，
    对白引号不可变由代码保证）。system_prompt 传模板/文风指令时保留。

    Returns: {text, prompt, target_len, retried}
    """
    params = dict(_DEFAULT_GEN_PARAMS)
    if gen_params:
        params.update(gen_params)
    prompt = build_scene_prompt(
        scene, chapter_title, core, style, role_setting, prev_tail, target_len,
        extra_feedback=extra_feedback,
    )
    text = await forward_generation_v4(prompt, gen_params=params, system_prompt=system_prompt or None)
    retried = 0
    # 【A2 2026-08-15】空文本也触发重试（原 `text and` 让空场景静默接受 → verify 空重建 → score 0）
    if (not text) or len(text) < target_len * 0.8:
        retried = 1
        boost = dict(params)
        boost["max_tokens"] = max(8192, target_len * 4)
        boost["temperature"] = min(0.4, (boost.get("temperature", 0.3) or 0.3) + 0.05)
        retry_prompt = prompt + (
            f"\n\n【篇幅不足】上一稿只有 {len(text)} 字，不足 {int(target_len * 0.8)} 字。"
            f"请把本场景写足到约 {target_len} 字。补足字数的正路：每轮对白写充分"
            "（说话人+神态+完整的话，不截断）、关键冲突回合一来一回写足。"
            "**严禁为了凑字：展开过程性动作（翻找/走路/收拾）成逐微动作、堆无推进细节、"
            "复述已写过的内容、或把素材之外的物件/数字挪进本场景**。"
            "若本场景情节素材本就有限，宁可精简写净，也不要靠上述注水手段硬凑。"
            "不得新增素材外情节。"
        )
        retry = await forward_generation_v4(retry_prompt, gen_params=boost, system_prompt=system_prompt or None)
        if retry:
            text = retry
    return {"text": text, "prompt": prompt, "target_len": target_len, "retried": retried}


def _open_diversity_feedback(open_det: dict[str, Any]) -> str | None:
    """段落开头单调 → 注入整章重生成的定向反馈块（列违规开头 + 段首多样化规则）。"""
    sig = open_det.get("signals") or {}
    if not sig:
        return None
    lines = ["【段落开头多样化·修正】上一稿段落开头机械重复，本稿必须改写："]
    for f in (open_det.get("findings") or [])[:4]:
        q = str(f.get("quote") or "").strip()
        if q:
            lines.append(f"- 上一稿违规开头：{q[:90]}")
    lines.append("- 规则：连续两段不得以同一词语开头（人物名/物件名/地点名都算，如「豆腐摊→豆腐摊」"
                 "「沈石→沈石」）；同一人物名开头的段落不超过全章 1/3；"
                 "指代同一人物时交替用主语省略/代词/动作先行/环境先行/对白开头；"
                 "章首前两段不得从同一物件/场景元素写起。")
    lines.append("- 重写时逐段检查段落开头，把重复开头改为不同切入方式（对白/动作/环境/心理/无主句交替），"
                 "**只改开头切入，不增删情节、不改对白内容**。")
    return "\n".join(lines)


def _overdetail_feedback(overdet: dict[str, Any]) -> str | None:
    """过场/细节不断展开 → 注入整章重生成的定向反馈块（列过详细句子 + 一笔带过规则）。"""
    sig = overdet.get("signals") or {}
    if not sig:
        return None
    lines = ["【过场一笔带过·修正】上一稿把该一笔带过的东西展开写太细了，本稿必须压缩："]
    for f in (overdet.get("findings") or [])[:4]:
        q = str(f.get("quote") or "").strip()
        if q:
            lines.append(f"- 上一稿过详细片段：{q[:90]}")
    lines.append("- 规则：巡街/走路/查看/摸装备等过场动作一句带过（最多一句），不逐微动作展开、不写"
                 "「如何走到现场」前奏；固定装备（更梆/打更灯/锣/灯笼）除非当下信息相关不描写"
                 "（不写裂纹/油烟/歪斜/破角）；感官/氛围（余温/夜色/水汽/寒气）最多一句且服务于"
                 "当下判断；物件清单删除或压缩为一个。")
    lines.append("- 重写时删掉/压缩上述过详细句子，把篇幅给对白轮与冲突回合（写充分），"
                 "**不增删情节、不改对白内容**。")
    return "\n".join(lines)


def _punct_feedback(punct: dict[str, Any]) -> str | None:
    """省略号/破折号密集 → 注入整章重生成的定向反馈块（v7.6.6 确定性兜底）。"""
    sig = punct.get("signals") or {}
    ell = sig.get("ellipsis") or 0
    dashes = sig.get("dashes") or 0
    if ell <= 0 and dashes <= 0:
        return None
    lines = ["【标点·修正】上一稿省略号/破折号密集（AI 味信号），本稿必须清除："]
    if ell:
        lines.append(f"- 上一稿省略号「……」共 {ell} 处，全部改为直接表达：需要停顿用逗号或句号，"
                     "需要沉默直接写「他没说话」「没有说话」；仅当场景素材原文对白明确含「……」时"
                     "才原样保留那一处。")
    if dashes:
        lines.append(f"- 上一稿破折号「——」共 {dashes} 处，全部删除：补充说明用逗号分句，"
                     "强调转折用句号断句。")
    lines.append("- 重写时逐句检查标点，**不增删情节、不改对白内容**。")
    return "\n".join(lines)


async def generate_chapter_prose(
    scenes: list[dict[str, Any]],
    *,
    chapter_title: str = "",
    core: str = "",
    style: str = "",
    role_setting: str = "",
    system_prompt: str = "",
    target_len_per_scene: int = 600,
    gen_params: dict[str, Any] | None = None,
    ai_flavor: bool = True,
    target_text: str | None = None,
    threshold: float | None = None,
    retry_cap: int = 1,
    target_from_orig: int | None = None,
    open_names: list[str] | None = None,
    open_objects: list[str] | None = None,
) -> dict[str, Any]:
    """逐场景生成整章正文：逐场景写足字数 → 拼接 → 整章 AI 味审阅 → 脏则整体重生成。

    target_text 提供时用「对照原文」审阅（训练有 target 用，fused 分），为 None 用
    「无原文对照」审阅（推导/独立生成）。ai_flavor=False 跳过审阅（快速冒烟）。
    threshold 默认取 config.derive_ai_flavor_threshold。

    Returns: {text, per_scene, total_len, retried, review}
    """
    from .ai_flavor import review_ai_flavor, review_ai_flavor_standalone
    from .config import SETTINGS as _s

    if threshold is None:
        threshold = float(getattr(_s, "derive_ai_flavor_threshold", 0.70))
    scenes_in = [s for s in (scenes or []) if isinstance(s, dict)]
    if not scenes_in:
        return {"text": "", "per_scene": [], "total_len": 0, "retried": 0, "review": None}
    # 【v5.33.3】verify_ladder 重建：目标按「原文长度/场景数×1.05」定（原文密度），
    # 不用素材丰度估算——重建目标是逼近原文篇幅。×1.05 而非 1.2：target 太高会触发
    # 多次长度重生成导致过度写（实测 1.5× 篇幅 → ai_flavor 崩）；×1.05 让 floor≈0.84×
    # 原文密度，配合 narration 原句（5-10 句/场景）足够诚实写满。
    _fixed = None
    if target_from_orig and target_from_orig > 0:
        _fixed = max(300, int(target_from_orig / len(scenes_in) * 1.05))

    def _review(t: str):
        if target_text:
            return review_ai_flavor(t, target_text)
        return review_ai_flavor_standalone(t)

    async def _gen_all(feedback: str | None = None) -> tuple[list[dict[str, Any]], str]:
        blocks: list[dict[str, Any]] = []
        prev_tail = ""
        for sc in scenes_in:
            # 【v5.32.6】关键事实·不可变 场景化：只钉本场景素材里的事实，
            # 不再全章 facts 注入每个场景（防跨场景挪用）。
            scene_core = core
            sfb = _scene_facts_block(sc)
            if sfb:
                scene_core = (core + "\n" + sfb).strip()
            res = await generate_scene_prose(
                sc,
                chapter_title=chapter_title, core=scene_core,
                style=style, role_setting=role_setting,
                prev_tail=prev_tail,
                target_len=_adaptive_target_len(sc, cap=max(300, target_len_per_scene * 2), fixed=_fixed),
                system_prompt=system_prompt, gen_params=gen_params,
                extra_feedback=feedback,
            )
            blocks.append({
                "name": str(sc.get("name") or "").strip(),
                "text": res["text"],
                "len": len(res["text"]),
                "retried": res["retried"],
            })
            prev_tail = _last_sentence(res["text"])
        # 【v5.32.4】清理场景拼接处开头被误抄的上一场景末句残片
        for b in blocks:
            if b["text"]:
                b["text"] = _strip_lead_tail_residue(b["text"])
        # 【v5.33.3】网文式短段 + 全章单换行分段（贴近原文段落结构，v_cos 不崩）
        for b in blocks:
            if b["text"]:
                b["text"] = _reparagraph_scene(b["text"])
        text = "\n".join(b["text"] for b in blocks if b["text"]).strip()
        return blocks, text

    per_scene, text = await _gen_all()
    scene_retried = 0
    chapter_retried = 0

    async def _regen_scene(i: int, feedback: str) -> bool:
        """针对性重生成第 i 个脏场景（含衔接上一场景末句）。"""
        prev_tail = _last_sentence(per_scene[i - 1]["text"]) if i > 0 else ""
        scene_core = core
        sfb = _scene_facts_block(scenes_in[i])
        if sfb:
            scene_core = (core + "\n" + sfb).strip()
        res = await generate_scene_prose(
            scenes_in[i],
            chapter_title=chapter_title, core=scene_core,
            style=style, role_setting=role_setting,
            prev_tail=prev_tail,
            target_len=_adaptive_target_len(scenes_in[i], cap=max(300, target_len_per_scene * 2), fixed=_fixed),
            system_prompt=system_prompt, gen_params=gen_params,
            extra_feedback=feedback,
        )
        if res["text"] and len(res["text"]) >= max(50, len(per_scene[i]["text"]) * 0.5):
            per_scene[i]["text"] = _reparagraph_scene(res["text"])
            per_scene[i]["len"] = len(per_scene[i]["text"])
            per_scene[i]["retried"] += res["retried"] + 1
            return True
        return False

    # ── ① 逐场景 standalone 审阅：脏场景针对性重生成（每场景最多一次，比整章重生成更有效）──
    if ai_flavor and per_scene:
        for i, blk in enumerate(per_scene):
            if not blk["text"] or len(blk["text"]) < 40:
                continue
            sreview = await review_ai_flavor_standalone(blk["text"])
            sscore = sreview.get("score") if isinstance(sreview, dict) else None
            sfindings = sreview.get("findings") or [] if isinstance(sreview, dict) else []
            if sscore is not None and sscore < threshold and sfindings:
                sfb = build_ai_flavor_feedback(sfindings)
                if sfb and await _regen_scene(i, sfb):
                    scene_retried += 1
        # 【v5.32.4】重生成后的场景也可能带衔接残片 → 统一再清一次
        for b in per_scene:
            if b["text"]:
                b["text"] = _strip_lead_tail_residue(b["text"])
        for b in per_scene:
            if b["text"]:
                b["text"] = _reparagraph_scene(b["text"])
        text = "\n".join(b["text"] for b in per_scene if b["text"]).strip()

    # ── ①-bis 完成审计 l5：锚点/对白轮/字数/引号逐项自检（硬约束，不受 ai_flavor 门控）──
    # 同一 blocker 连续 3 轮未修（blocked 三振）→ 如实保留、标记 incomplete，不死循环。
    if per_scene:
        from .completion_audit import audit_l5, build_audit_feedback, StrikeCounter
        from .key_details import extract_key_details
        for i, blk in enumerate(per_scene):
            if not blk["text"]:
                continue
            sc = scenes_in[i]
            # 本场景叶子文本 → 锚点（人物/数字/对白/物品）
            _leaf_txt = "\n".join(
                str(sc.get(k)) for k in ("name", "environment") if isinstance(sc.get(k), str))
            for k in ("actions", "dialogues", "narration", "psychologies", "conflicts", "details"):
                v = sc.get(k)
                if isinstance(v, list):
                    _leaf_txt += "\n" + "\n".join(str(x) for x in v)
            kd = extract_key_details(_leaf_txt) if _leaf_txt.strip() else None
            tgt = _adaptive_target_len(
                sc, cap=max(300, target_len_per_scene * 2), fixed=_fixed)
            strikes = StrikeCounter(cap=3)
            for _try in range(3):
                aud = audit_l5(blk["text"], sc, kd=kd, target_len=tgt)
                if aud["ok"]:
                    break
                # 仍被三振的 blocker 不再为它重试
                live = [f for f in aud["findings"]
                        if f.get("retriable", True) and not strikes.is_blocked(f["blocker_key"])]
                if not live:
                    break
                fb = build_audit_feedback({"findings": live})
                if not fb or not await _regen_scene(i, fb):
                    break
                scene_retried += 1
                # 重生成后重新审计：仍在的 finding 计一次 strike，修好的 reset
                new_aud = audit_l5(per_scene[i]["text"], sc, kd=kd, target_len=tgt)
                live_keys = {f["blocker_key"] for f in live}
                new_keys = {f["blocker_key"] for f in new_aud["findings"]}
                for k in live_keys:
                    if k in new_keys:
                        strikes.hit(k)
                    else:
                        strikes.reset(k)
                blk = per_scene[i]
        for b in per_scene:
            if b["text"]:
                b["text"] = _strip_lead_tail_residue(b["text"])
                b["text"] = _reparagraph_scene(b["text"])
        text = "\n".join(b["text"] for b in per_scene if b["text"]).strip()

    # ── ② 整章审阅（target_text 提供用对照，否则 standalone）→ 脏则整体重生成 ──
    # 【v6.4/v6.4.1】段落开头单调 + 过场/细节展开两类确定性信号——ai_flavor 正常（如 0.87）
    # 也拦不住，作为独立信号强制触发重生成；最多 retry_cap 轮，每轮用
    # 「综合分=审阅分−开头惩罚−过详细惩罚」决定是否保留（两类质量修好优先）。
    review = None
    if ai_flavor and text:
        review = await _review(text)

        # 【v6.4/v6.4.1】两类确定性检测只用于无原文对照路径（AI 创作/推导）；榨干重建
        # （target_text 提供）要对齐原文，开头多样/过详细不强制（避免偏离原文）。
        def _gates(t: str) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
            if target_text is not None:
                return ({"open_score": 1.0, "findings": [], "signals": {}},
                        {"overdetail_score": 1.0, "findings": [], "signals": {}},
                        {"punct_score": 1.0, "signals": {}})
            return (paragraph_open_diversity_detector(t, names=open_names, objects=open_objects),
                    overdetail_detector(t), punct_detector(t))

        def _composite(s, o, od, p):
            if s is None:
                return -1.0
            pen = (max(0.0, 0.7 - (o if isinstance(o, (int, float)) else 0.7)) * 0.25
                   + max(0.0, 0.7 - (od if isinstance(od, (int, float)) else 0.7)) * 0.25
                   + max(0.0, 0.7 - (p if isinstance(p, (int, float)) else 0.7)) * 0.15)
            return round(float(s) - pen, 4)

        for _ in range(retry_cap):
            score = review.get("score") if isinstance(review, dict) else None
            findings = review.get("findings") or [] if isinstance(review, dict) else []
            has_high = any(f.get("severity") == "high" for f in findings)
            open_det, overdet, punct = _gates(text)
            open_score = open_det.get("open_score")
            open_bad = isinstance(open_score, (int, float)) and open_score < 0.7
            od_score = overdet.get("overdetail_score")
            od_bad = isinstance(od_score, (int, float)) and od_score < 0.7
            pn_score = punct.get("punct_score")
            pn_bad = isinstance(pn_score, (int, float)) and pn_score < 0.7
            if not (has_high or (score is not None and score < threshold) or open_bad or od_bad or pn_bad):
                break
            fb = build_ai_flavor_feedback(findings)
            ofb = _open_diversity_feedback(open_det) if open_bad else None
            odb = _overdetail_feedback(overdet) if od_bad else None
            pnb = _punct_feedback(punct) if pn_bad else None
            combined = "\n\n".join(x for x in (fb, ofb, odb, pnb) if x)
            if not combined:
                break
            new_per, new_text = await _gen_all(combined)
            chapter_retried += 1
            new_review = await _review(new_text)
            new_open, new_overdet, new_punct = _gates(new_text)
            c_new = _composite(new_review.get("score") if isinstance(new_review, dict) else None,
                               new_open.get("open_score"), new_overdet.get("overdetail_score"),
                               new_punct.get("punct_score"))
            c_old = _composite(score, open_score, od_score, pn_score)
            # 保更好：优先「满足更多硬约束门」（三门都达标>二门>一门>零门），同门数再比综合分——
            # 避免把修好开头的好稿换成分数略高但开头崩掉的坏稿。
            new_ok = int((new_open.get("open_score") or 0) >= 0.7) \
                + int((new_overdet.get("overdetail_score") or 0) >= 0.7) \
                + int((new_punct.get("punct_score") or 0) >= 0.7)
            old_ok = int((open_score or 0) >= 0.7) + int((od_score or 0) >= 0.7) \
                + int((pn_score or 0) >= 0.7)
            if new_ok > old_ok or (new_ok == old_ok and c_new > c_old) \
                    or (new_text and len(new_text) > len(text) * 1.5):
                per_scene, text, review = new_per, new_text, new_review
            else:
                break  # 重生成未更好 → 保留原稿
    # 【v6.4/v6.4.1】透出确定性信号（供评分/前端观察）
    if isinstance(review, dict):
        review["open"], review["overdetail"], review["punct"] = _gates(text)
    return {
        "text": text,
        "per_scene": per_scene,
        "total_len": len(text),
        "retried": scene_retried + chapter_retried,
        "review": review,
    }


def build_generation_input(
    scenes: list[dict[str, Any]],
    *,
    chapter_title: str = "",
    core: str = "",
    style: str = "",
    role_setting: str = "",
    target_len: int = 600,
) -> list[dict[str, Any]]:
    """每场景的生成指令清单（场景级路径替代 tree_to_generation_input 的整章 user_input）。

    返回 [{scene, prompt}]。prev_tail 在生成时由 generate_scene_prose 动态补（生成结果未知）。
    """
    out: list[dict[str, Any]] = []
    for sc in scenes or []:
        if not isinstance(sc, dict):
            continue
        prompt = build_scene_prompt(
            sc, chapter_title, core, style, role_setting, target_len=target_len,
        )
        out.append({"scene": str(sc.get("name") or "").strip(), "prompt": prompt})
    return out


# ═══════════════════════════ 压缩阶梯（v5.32 第二步）：从原文提取 ═══════════════════════════

def _scenes_for_extract(scenes: list[dict[str, Any]], max_items: int = 200) -> str:
    """把场景列表序列化为紧凑文本（供上一级提取 LLM 阅读）。"""
    lines: list[str] = []
    for i, sc in enumerate(scenes or [], 1):
        if not isinstance(sc, dict):
            continue
        name = str(sc.get("name") or f"场景{i}").strip()
        lines.append(f"【场景{i}】{name}")
        env = sc.get("environment")
        if isinstance(env, str) and env.strip():
            lines.append(f"环境：{env}")
        for key, label in (("actions", "动作"), ("dialogues", "对白"),
                           ("psychologies", "心理"), ("conflicts", "冲突"),
                           ("details", "细节")):
            v = sc.get(key)
            items = [str(x).strip() for x in v if str(x).strip()] if isinstance(v, list) else []
            if items:
                lines.append(f"{label}：" + "；".join(items[:6]))
        if len(lines) > max_items:
            lines.append("…（场景过多，省略）")
            break
    return "\n".join(lines)


async def _extract_scenes_pass(
    src: str,
    style: str,
    role_setting: str,
    density: int,
    seg_label: str = "",
) -> list[dict[str, Any]]:
    """单次场景提取 LLM 调用：一段原文 → 3-5 场景 × 7 类叶子。"""
    from .derive import _tree_llm

    seg_note = (
        f"\n（注意：这是长章的第{seg_label}段，只拆这一段内出现的内容；"
        f"场景名用「{seg_label}·场景N」格式，不要用全章场景名）" if seg_label else ""
    )
    system = (
        "你是「小说场景拆解专家」。给定一章原文，把它按时序拆成场景，每场景给出 6 类叶子。"
        "你的任务是**从原文提取**，不是凭想象重新创作：人物名、事件、对白（保留原话核心与说话人）、"
        "标志性物品/数字都必须来自原文；不得虚构原文没有的情节或对话。"
    )
    user = (
        (f"风格基调：{style.strip()}" if (style or "").strip() else "风格基调：（未指定）")
        + (f"\n角色设定：{role_setting.strip()}" if (role_setting or "").strip() else "\n角色设定：（未指定）")
        + f"\n\n【原文章节】\n{src}\n"
        + seg_note
        + f"\n请把这段拆成 3-5 个场景（按时序推进），每场景 7 类叶子："
          "environment 环境（单条字符串）/ actions 动作 / dialogues 对话 / "
          "narration 原文叙述原句 / psychologies 心理 / conflicts 冲突 / details 细节。"
        + f"动作/心理/冲突/细节各 {density} 条；"
          "**对话 dialogues：列出该场景出现的全部引号轮次**（角色对白保留原话带说话人，"
          "**含说话动词与神态——如『王慧兰阴阳怪气道』『陈硕得意洋洋』『王慧兰喜滋滋的挽住陈硕胳膊』"
          "『道了一声晦气』『撇嘴』『挠挠头皮』，逐轮原样保留原文对「说话人+动词/神态」的写法，"
          "不要只留说话人名**；"
          "含门牌/奖状/牌匾等原文引号内容，只要原文用了引号就原样列出），不要按条数截断、不要遗漏任何一轮。"
          "**narration 原文叙述原句：从该场景时段内逐字摘录 8-12 句原文叙述句**"
          "（直接抄原文原句，不要改写/概括/拼接；**宁可多摘，尽量覆盖该段原文里所有含特色措辞的"
          "叙述句**——身世铺陈、事件经过、环境氛围、关键动作的原文措辞全要，目标是重建正文能"
          "原样复现该段原文 50% 以上的措辞；对白轮次已单列，不必重复），重建正文时必须原样采用。"
          "叶子必须直接来自原文，具体到能据此展开 500 字以上的正文："
          "动作含对象与结果，冲突写清双方与进展，细节含标志性物品/数字/可复用线索。"
          "**动作/细节/环境尽量用原文原句或紧贴原文措辞**（保留人物称呼、物品名、数字、"
          "关键动作的原话表达），不要改写成自己的话——原句保留得越多，重建的字符相似越高。"
          "**但不得机械罗列**：一条细节/环境只承载一个要点（一个物品/一个数字/一件事），"
          "不要用「头顶悬着X，客厅摆放着Y，茶几上摆放着Z」「包括A、B和C，桌子放着D」式"
          "排比压成长条——物件多的场景拆成多条细节，保留原文转折/长短句的信息密度，"
          "重建正文时才不会照抄成排比句。"
          "**动作叶子：原文是概括动作（如『翻翻找找清理』）就保持概括**，"
          "不要为了凑条数拆成逐微动作（翻抽屉→翻床底→摸床垫）；过程性动作用原文的概括句。"
          "不要抽象概括、套话、空镜头。\n"
          "**同一事实只归一个场景**：每场景叶子只取原文该场景时段实际出现的内容；"
          "原文没在这段出现的事实不要预埋/复述到别的场景（如『借四百万只拿二十万』的往事"
          "只在客厅扔遗像后出现，就只放客厅场景，门口场景不要提前埋）。"
          "心理/冲突叶子必须是原文明确写出的，不要为凑条数补原文没有的内心解读。\n"
          "重要：输出必须是一份完整、合法的 JSON，不要省略号或「等」，"
          "不要漏掉任何场景/叶子，也不要截断。\n"
          "严格输出如下 JSON（不要 Markdown 代码块）：\n"
          '{"scenes": [{"name": "场景名", "environment": "…", "actions": ["…"], '
          '"dialogues": ["…"], "narration": ["…"], "psychologies": ["…"], '
          '"conflicts": ["…"], "details": ["…"]}]}'
    )
    data = await _tree_llm(system, user, "ladder_extract_scenes", max_tokens=8000)
    scenes = data.get("scenes")
    if not isinstance(scenes, list):
        raise RuntimeError("场景提取失败：无 scenes 数组")
    out: list[dict[str, Any]] = []
    for s in scenes:
        if not isinstance(s, dict):
            continue
        env = s.get("environment")
        if isinstance(env, list):
            env = env[0] if env else ""
            s = dict(s)
            s["environment"] = env
        out.append(s)
    return out


async def extract_chapter_scenes(
    orig_text: str,
    style: str = "",
    role_setting: str = "",
    density: int | None = None,
) -> list[dict[str, Any]]:
    """压缩阶梯第 4 级（X4）：从原文提取场景分解。

    LLM **读原文**按时序拆成场景 × 6 类叶子，提取原文的实际内容
    （人物名/事件/对白原话核心/标志性物品/数字），不凭空虚构原文没有的情节。
    这是解决「从极简凭空生成的树不承载原文保真」的关键：中间层从原文提取。

    【v7.8.1 修复】原实现 `len(src)>6000 时 src=src[:6000]` 会把超长章截断丢掉
    后半内容（书D 501-955 约 20% 章超 6000 字，len_ratio/s_char 被系统性拖垮）。
    改为：超长章**分段提取**（每段 ~6000 字），各段场景合并后统一加段前缀防重名。

    Returns: list[scene dict]。失败 raise（调用方降级）。
    """
    from .config import SETTINGS as _s

    if density is None:
        density = max(2, int(getattr(_s, "derive_scene_density", 4)))
    src = (orig_text or "").strip()
    if not src:
        return []
    SEG = 6000
    if len(src) <= SEG:
        return await _extract_scenes_pass(src, style, role_setting, density)
    # 长章：按段提取场景，合并后统一加段前缀（防重名、保留时序）
    parts: list[dict[str, Any]] = []
    n_seg = -(-len(src) // SEG)
    for i in range(n_seg):
        seg = src[i * SEG:(i + 1) * SEG].strip()
        if not seg:
            continue
        label = f"段{i + 1}"
        part = await _extract_scenes_pass(seg, style, role_setting, density, seg_label=label)
        for sc in part:
            nm = str(sc.get("name") or f"场景{len(parts) + 1}")
            sc["name"] = f"〔{label}〕{nm}"
            parts.append(sc)
    return parts


async def extract_l3_from_scenes(
    l4_scenes: list[dict[str, Any]],
    style: str = "",
    role_setting: str = "",
) -> dict[str, Any]:
    """X3：从场景分解提取章核心（title + core + beats）。

    【v5.34 场景切分前移】beats 不再是泛泛的「2-5 拍关键事件」，而是与场景分解
    **一一对应的场景切分计划**（每拍=一个场景：场景名 + 该场景的关键人物/动作/
    冲突/标志性物品数字）。消融实验（ladder_sufficiency）证明旧 l3 对生成端零增量
    （C-B≈0），根因是场景切分信息在 X4→X3 时被压掉；前移后 l3 自带切分，l4 展开
    有章可循。
    """
    from .derive import _tree_llm

    scenes_txt = _scenes_for_extract(l4_scenes)
    n_sc = len([s for s in (l4_scenes or []) if isinstance(s, dict)])
    d3 = await _tree_llm(
        "你是「章节摘要专家」。给定一章的场景分解，提取该章核心：标题、一句话核心、"
        "以及**与场景一一对应的切分计划**。"
        "beats 每一条 = 一个场景（共 " + str(n_sc) + " 条，顺序与场景分解一致），格式："
        "「场景名：谁在该场景做了什么关键事 + 冲突回合 + 标志性物品/数字」。"
        "必须保留重建这些场景所需的全部关键信息（人物/事件/冲突/结局），用大白话写。",
        "风格基调：" + (style.strip() or "（未指定）") + "\n角色设定：" + (role_setting.strip() or "（未指定）")
        + "\n\n【场景分解】\n" + scenes_txt
        + "\n\n严格输出如下 JSON（不要 Markdown 代码块）：\n"
        '{"title": "第1章 …", "core": "一句话核心", "beats": ["场景名：关键内容", "…（与场景数相同）"]}',
        "ladder_l3_chapter", max_tokens=2000,
    )
    l3 = {k: d3.get(k) for k in ("title", "core", "beats")}
    return l3


async def build_ladder(
    orig_text: str,
    style: str = "",
    role_setting: str = "",
    density: int | None = None,
    budget: int = 80,
) -> dict[str, Any]:
    """压缩阶梯构建（从原文提取）：X4(场景分解) → X3(章核心) → X2(弧线概要) → X1(极简)。

    每级从上一级提取，prompt 硬性要求「保留重建上级所需的全部关键信息」。
    用户要求的模块目的：12345→1234→123→12→1 每级从原文提取足以生成上一级的内容。

    Returns: {l4_scenes, l3_chapter, l2_arc, l1_minimal}
    """
    l4 = await extract_chapter_scenes(orig_text, style, role_setting, density)

    # X3：从场景分解提取章核心（beats=场景切分计划，v5.34 前移）
    l3 = await extract_l3_from_scenes(l4, style, role_setting)

    # X2：从章核心提取弧线概要（2-4 句）
    from .derive import _tree_llm

    ch_txt = f"标题：{l3.get('title') or ''}\n核心：{l3.get('core') or ''}\n拍："
    ch_txt += "；".join(str(b) for b in (l3.get("beats") or []))
    d2 = await _tree_llm(
        "你是「弧线摘要专家」。给定一章的标题/核心/关键事件，提取这条剧情弧线的概要（2-4 句，"
        "包含起因/核心冲突/转折/结局，人物与地点齐全），足以据此重建该章所有关键事件。",
        "【章节信息】\n" + ch_txt
        + "\n\n严格输出如下 JSON（不要 Markdown 代码块）：\n"
        '{"summary": "2-4 句弧线概要"}',
        "ladder_l2_arc", max_tokens=1000,
    )
    l2 = str((d2.get("summary") if isinstance(d2, dict) else None) or "").strip() or ch_txt

    # X1：从弧线概要压缩成一句话极简（≤ budget 字）。极简是一句话非 JSON → chat_completion
    from .llm_client import chat_completion
    resp = await chat_completion(
        system=(
            "你是「极简剧情压缩专家」。把弧线概要压缩成不超过 "
            f"{budget} 个字的一句话极简剧情，"
            "保留人物名+地点+核心冲突+关键事件序列+结局。用大白话，越短越好，不要引号。"
        ),
        user="【弧线概要】\n" + l2,
        temperature=0.45, top_p=0.7,
        max_tokens=max(128, budget * 3),
        call_type="ladder_l1_minimal",
    )
    l1 = (resp.get("content") or "").strip()
    return {"l4_scenes": l4, "l3_chapter": l3, "l2_arc": l2, "l1_minimal": l1}


async def extract_key_facts(minimal: str) -> list[str]:
    """从极简剧情提取「不可变关键事实」：人物全名/地点/标志性物品/关键事件。

    前向推回时这些事实必须原样贯穿每一级（防改名/防丢失——实测极简里的 王慧兰
    在弧线概要生成时被 LLM 改成 张兰，中央花园33栋/录取通知书/遗像全丢）。
    """
    from .derive import _tree_llm

    d = await _tree_llm(
        "你是「关键要素提取专家」。从极简剧情提取必须原样保留的关键事实（人物全名/地点/"
        "标志性物品/数字/关键事件），后续逐级展开时这些事实不得改名、不得丢失。"
        "每条一句，具体（人物用全名，地点用全称）。",
        "极简剧情：\n" + (minimal or "").strip()
        + "\n\n严格输出如下 JSON（不要 Markdown 代码块）：\n"
        '{"facts": ["人物/地点/物品/事件1", "…"]}',
        "ladder_key_facts", max_tokens=800,
    )
    facts = [str(f).strip() for f in (d.get("facts") or []) if str(f).strip()]
    return facts


def _exemplar_block(exemplars: list[dict[str, Any]] | None, level: int) -> str:
    """构造某级(2/3/4)的真实阶梯范例注入块（仅参考结构/详细程度，不参考内容）。"""
    if not exemplars:
        return ""
    parts: list[str] = []
    for i, ex in enumerate(exemplars, 1):
        if level == 2 and str(ex.get("l2_arc") or "").strip():
            parts.append(f"真实弧线概要范例{i}：{str(ex['l2_arc']).strip()}")
        elif level == 3 and isinstance(ex.get("l3_chapter"), dict):
            c = ex["l3_chapter"]
            t = str(c.get("title") or "").strip()
            co = str(c.get("core") or "").strip()
            bs = "；".join(str(b).strip() for b in (c.get("beats") or []) if str(b).strip())
            parts.append(f"真实章核心范例{i}：标题『{t}』核心『{co}』拍『{bs}』")
        elif level == 4 and isinstance(ex.get("l4_scenes"), list) and ex["l4_scenes"]:
            s = ex["l4_scenes"][0]
            act = "；".join(str(x).strip() for x in (s.get("actions") or [])[:3])
            dlg = "；".join(str(x).strip() for x in (s.get("dialogues") or [])[:3])
            det = "；".join(str(x).strip() for x in (s.get("details") or [])[:3])
            parts.append(
                f"真实场景范例{i}（{str(s.get('name') or '').strip()}）："
                f"动作『{act}』对白『{dlg}』细节『{det}』"
            )
    if not parts:
        return ""
    return (
        "【真实阶梯范例】（仅参考结构与详细程度，剧情内容必须是你自己的极简剧情所对应的，"
        "不得照抄范例内容）：\n" + "\n".join(parts)
    )


async def expand_ladder(
    l1_minimal: str,
    style: str = "",
    role_setting: str = "",
    archetype: str = "",
    parent: str = "",
    n_chapters: int = 1,
    target_len_per_scene: int = 600,
    exemplars: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """前向推回（1→12→123→1234→12345）：从极简逐级展开，不参考原文。

    用户要求的「试试 1 如何设计能一步一步再推回 12345」——这是纯前向路径：
    1 → 12(弧线概要) → 123(章核心) → 1234(场景分解) → 12345(正文)。
    每级以上一级为 spec 生成，不使用提取的阶梯（提取阶梯仅用于对比/校验）。

    【反漂移】从极简提取关键事实清单，贯穿每一级展开（人物名/地点/物品不得改名丢失）——
    实测纯前向会丢事实/改人名（王慧兰→张兰），关键事实块是修复。

    【v5.32.2 真实阶梯范例注入】exemplars：同原型真实弧线的阶梯（l2_arc/l3_chapter/l4_scenes），
    注入各级展开 prompt 作 few-shot 结构参考（提升详细程度/结构保真，内容仍是自己的剧情）。

    Returns: {l2_arc, l3_chapter, l4_scenes, prose, per_scene, review, key_facts}
    """
    from .derive import _expand_arc_scenes_llm, _tree_llm

    facts = await extract_key_facts(l1_minimal)
    facts_block = (
        "【关键事实·不可变】以下事实必须原样保留在后续每一级展开与正文里："
        "人物不得改名（如 王慧兰 不能写成 张兰），地点/物品/数字/事件不得丢失。\n"
        + "；".join(facts)
    ) if facts else ""
    ex2 = _exemplar_block(exemplars, 2)
    ex3 = _exemplar_block(exemplars, 3)
    ex4 = _exemplar_block(exemplars, 4)

    # 1 → 12：极简 → 弧线概要（起因/冲突/转折/结局，人物地点齐全）
    d2 = await _tree_llm(
        "你是「弧线拆解专家」。给定一句话极简剧情，扩写成这条剧情弧线的概要（2-4 句，"
        "包含起因/核心冲突/转折/结局，人物与地点齐全，具体可写，不是抽象总结）。"
        + ("\n" + facts_block if facts_block else "")
        + ("\n" + ex2 if ex2 else ""),
        "极简剧情：\n" + (l1_minimal or "").strip()
        + (("\n风格基调：" + style.strip()) if (style or "").strip() else "")
        + (("\n角色设定：" + role_setting.strip()) if (role_setting or "").strip() else "")
        + "\n\n严格输出如下 JSON（不要 Markdown 代码块）：\n"
        '{"summary": "2-4 句弧线概要"}',
        "ladder_fwd_l2", max_tokens=1000,
    )
    l2 = str((d2.get("summary") if isinstance(d2, dict) else None) or "").strip() or (l1_minimal or "").strip()

    # 12 → 123：弧线概要 → 章核心（title/core/beats=场景切分计划，v5.34 前移）
    d3 = await _tree_llm(
        "你是「章节拆解专家」。给定弧线概要，拆成单章核心：标题、一句话核心、"
        "以及本章的**场景切分计划**（beats）。保留重建场景所需的全部关键信息"
        "（人物/事件/冲突/结局），用大白话、具体可写。"
        "**beats 每一条 = 本章的一个场景**（拍数即本章场景数，一般 3-6 条），格式："
        "「场景名：谁在该场景做了什么 + 冲突回合 + 标志性物品/数字」，下一级展开时严格照此切分。"
        + ("\n" + facts_block if facts_block else "")
        + ("\n" + ex3 if ex3 else ""),
        "弧线概要：\n" + l2
        + "\n\n严格输出如下 JSON（不要 Markdown 代码块）：\n"
        '{"title": "第1章 …", "core": "一句话核心", "beats": ["场景名：关键内容", "…"]}',
        "ladder_fwd_l3", max_tokens=2000,
    )
    l3 = {k: d3.get(k) for k in ("title", "core", "beats")}
    title = str((l3.get("title") if isinstance(l3.get("title"), str) else "") or "第1章").strip()
    core = str((l3.get("core") if isinstance(l3.get("core"), str) else "") or "").strip()
    beats = [str(b).strip() for b in (l3.get("beats") or []) if str(b).strip()]

    # 关键事实块 + 场景范例挂进章核心 → 场景展开（_expand_chapter_scenes_llm 的「本章核心」字段承载）
    core_with_facts = core + (("\n" + facts_block) if facts_block else "")
    if ex4:
        core_with_facts = core_with_facts + "\n\n" + ex4

    # 123 → 1234：章核心 → 场景分解（复用 derive 密集展开 + AI 味修正；beats 作切分计划透传）
    ch0: dict[str, Any] = {"title": title, "core": core_with_facts}
    if beats:
        ch0["scene_plan"] = beats
    arc = {"name": title, "archetype": archetype, "parent": parent,
           "chapters": [ch0]}
    chaps = await _expand_arc_scenes_llm(arc, style, role_setting)
    l4: list[dict[str, Any]] = []
    for ch in chaps:
        for sc in (ch.get("scenes") or []):
            if isinstance(sc, dict):
                l4.append(sc)

    # 1234 → 12345：场景 → 正文（场景级生成，关键事实块随 core 进每个场景 prompt）
    gres = await generate_chapter_prose(
        l4,
        chapter_title=title, core=core_with_facts,
        style=style, role_setting=role_setting,
        system_prompt="",
        target_len_per_scene=target_len_per_scene,
        ai_flavor=True,
        target_text=None,
        retry_cap=1,
    )
    return {
        "l2_arc": l2,
        "l3_chapter": {"title": title, "core": core, "beats": beats},
        "l4_scenes": l4,
        "prose": gres["text"],
        "per_scene": gres["per_scene"],
        "review": gres["review"],
        "retried": gres["retried"],
        "key_facts": facts,
    }


_FACT_NAMES = ("林川", "陈硕", "王慧兰", "袍哥", "二刀", "老刘")
_FACT_LOCS = ("中央花园33栋", "城西精神病院", "洛城")
_FACT_ITEMS = ("录取通知书", "诊断书", "房本", "黄金", "遗像", "荣誉证书", "奖状")


def _facts_from_texts(texts: list[str], *, scope: str = "本章") -> str:
    """从若干文本确定性提取【关键事实·不可变】块（人名/地点/金额/关键物，不调 LLM）。

    实测 原文「抵押一千五百万」被生成端写成「欠袍哥哥三百万」，袍哥→袍哥哥——
    关键数字/称呼必须钉进生成上下文。scope 控制提示文案（「本章」/「本场景」）。
    """
    joined = "\n".join(t for t in texts if t and str(t).strip())
    names = [n for n in _FACT_NAMES if n in joined]
    locs = [n for n in _FACT_LOCS if n in joined]
    # 金额：X百万 / X千万 / X万（含中文数字与阿拉伯数字）
    money = []
    for m in re.finditer(r"[一二三四五六七八九十百千零0-9]+[万亿]", joined):
        t = m.group(0)
        if t not in money and ("百万" in t or "千万" in t or "万亿" in t or t.endswith("万")):
            money.append(t)
    items = [n for n in _FACT_ITEMS if n in joined]
    facts = [f for f in (names + locs + money + items) if f]
    if not facts:
        return ""
    return (
        f"【关键事实·不可变】以下人物/地点/金额/物品是{scope}既定事实，正文必须原样使用，"
        "不得改名、不得改数、不得丢弃（如 袍哥 不得写成 袍哥哥，抵押金额 一千五百万 "
        "不得写成 三百万）："
        + "、".join(facts)
    )


def _ladder_facts_block(ladder: dict[str, Any]) -> str:
    """从阶梯全章提取【关键事实·不可变】块（保留：verify 脚本断言/外部调用兼容）。"""
    texts: list[str] = []
    if isinstance(ladder, dict):
        for key in ("l1_minimal", "l2_arc"):
            v = ladder.get(key)
            if isinstance(v, str) and v.strip():
                texts.append(v.strip())
        l3 = ladder.get("l3_chapter")
        if isinstance(l3, dict):
            for k in ("title", "core"):
                v = l3.get(k)
                if isinstance(v, str) and v.strip():
                    texts.append(v.strip())
            for b in (l3.get("beats") or []):
                if isinstance(b, str) and b.strip():
                    texts.append(b.strip())
        for sc in (ladder.get("l4_scenes") or []):
            if not isinstance(sc, dict):
                continue
            for leaf in ("actions", "dialogues", "psychologies", "conflicts", "details"):
                for it in (sc.get(leaf) or []):
                    if isinstance(it, str) and it.strip():
                        texts.append(it.strip())
    return _facts_from_texts(texts)


def _scene_facts_block(scene: dict[str, Any]) -> str:
    """【v5.32.6】从单个场景的 6 类叶子提取【关键事实·不可变】块（场景级）。

    只钉**本场景素材里出现**的人名/地点/金额/关键物——防止生成端在字数压力下
    把后续场景的物件/数字挪用进本场景（实测 scene1 把二楼的录取通知书搬到
    「一楼旧盒子」、把袍哥场景的三千万安到「大哥身家」）。
    """
    texts: list[str] = []
    if isinstance(scene, dict):
        for key in ("name", "environment", "actions", "dialogues",
                    "narration", "psychologies", "conflicts", "details"):
            v = scene.get(key)
            if isinstance(v, str):
                texts.append(v.strip())
            elif isinstance(v, list):
                texts.extend(str(x).strip() for x in v if str(x).strip())
    return _facts_from_texts(texts, scope="本场景")


async def verify_ladder(
    ladder: dict[str, Any],
    orig_text: str,
    style: str = "",
    role_setting: str = "",
    target_len_per_scene: int = 600,
) -> dict[str, Any]:
    """重建校验：从阶梯的 X4 场景逐场景生成正文 → 评分 vs 原文。

    验证「压缩是否足以重建」：长度比、s_char、turn_fidelity、ai_flavor。
    Returns: {text, score, len_ratio, s_char, turn_fidelity, ai_flavor, ok}
    """
    from .minimal_train import score_generated

    scenes = ladder.get("l4_scenes") or []
    if not scenes:
        return {"error": "无场景", "ok": False}
    # 【v5.32.3】把 L2 弧线概要也注入生成上下文（含「林川在城西精神病院」等事实锚点）：
    # 只传 L3 core 时生成端缺「林川是被送进精神病院的病人/学生」事实，会脑补「当全职医生」。
    l2 = str((ladder.get("l2_arc") or "") or "").strip()
    core0 = str((ladder.get("l3_chapter") or {}).get("core") or "").strip()
    core = (core0 + (("\n【弧线概要】" + l2) if l2 else "")).strip()
    # 【v5.32.6】关键事实·不可变 改为场景级（generate_chapter_prose 内部按场景注入
    # _scene_facts_block），不再全章 facts 注入每个场景——防止把后续场景的物件/数字
    # 挪用进本场景（防改名/改数职责由每场景自家素材承载）。
    gres = await generate_chapter_prose(
        scenes,
        chapter_title=str((ladder.get("l3_chapter") or {}).get("title") or ""),
        core=core,
        style=style, role_setting=role_setting,
        system_prompt="",
        target_len_per_scene=target_len_per_scene,
        # 【v5.33.3】重建目标按原文篇幅/场景数定，逼近原文密度
        target_from_orig=len(orig_text or ""),
        ai_flavor=True,
        target_text=orig_text,
        # 【v5.32.1】重建路径 AI 味重试上限 1→2：复述原文后补心理注释是系统性根因，1 次重生成不够
        retry_cap=2,
    )
    text = gres["text"]
    skeleton = str((ladder.get("l3_chapter") or {}).get("core") or "") + "；" + "；".join(
        str(b) for b in (ladder.get("l3_chapter") or {}).get("beats") or [])
    sc = await score_generated(text, orig_text, skeleton, ai_flavor=True, ai_flavor_result=gres["review"])
    import re as _re
    gen_clean = _re.sub(r"\s+", "", text)
    orig_clean = _re.sub(r"\s+", "", orig_text or "")
    len_ratio = round(len(gen_clean) / max(1, len(orig_clean)), 3)
    return {
        "text": text,
        "score": sc.get("score", 0.0),
        "s_char": sc.get("s_char", 0.0),
        "turn_fidelity": sc.get("turn_fidelity", 0.0),
        "plot_sim": sc.get("plot_sim", 0.0),
        "ai_flavor": sc.get("ai_flavor"),
        "len_ratio": len_ratio,
        "len_ok": len_ratio >= 0.7,
        "retried": gres.get("retried", 0),
        "ok": sc.get("score", 0.0) >= 0.4,
    }


# ═══════════════════════════ 前沿训练（v5.32 第二步）：扫极简预算档 ═══════════════════════════

async def _synthesize_frontier_spec(
    per_sample: list[dict[str, Any]],
    frontier: list[dict[str, Any]],
) -> dict[str, Any]:
    """LLM 从前沿样本归纳极简设计的合格要素清单 + 改进生成器 prompt。"""
    from .llm_client import chat_json

    rows: list[str] = []
    for r in per_sample:
        m = r.get("minimal") or "（空）"
        rows.append(f"预算{r['budget']}字/实际{r['len']}字/复现分{r['score']:.3f}/"
                    f"{'充分' if r.get('ok') else '不足'}：{m}")
    table = "\n".join(rows) if rows else "（无数据）"
    user = (
        f"极简前沿样本（越短越充分越好，找「最短而充分」的边界）：\n{table}\n\n"
        "请分析：①「不足」样本丢了哪些必须要素（对照充分样本补齐）；②归纳该剧情的极简内容"
        "合格要素清单（3-6 条，每条一句具体可判）；③写出改进后的极简内容生成器 prompt"
        "（要求生成器强制保留合格要素，保证前向推回 1→12345 时事实不漂移）。\n"
        "严格输出如下 JSON（不要 Markdown 代码块）：\n"
        '{"elements": ["…"], "reason": "一句判断依据", "generator_prompt": "完整生成器 prompt"}'
    )
    try:
        res = await chat_json(
            system="你是「极简剧情压缩前沿分析师」。分析不同字数预算下极简剧情的前向推回复现分，"
                   "找出最短而充分的预算档与合格要素。",
            user=user, call_type="ladder_frontier_spec", max_tokens=3000,
        )
    except Exception:
        res = {"data": None, "error": "call_failed"}
    data = res.get("data") if not res.get("error") else None
    if isinstance(data, dict):
        elements = [str(e).strip() for e in (data.get("elements") or []) if str(e).strip()]
        reason = str(data.get("reason") or "").strip()
        gen = str(data.get("generator_prompt") or "").strip()
        if gen:
            return {"elements": elements, "reason": reason, "generator_prompt": gen}
    return {"elements": [], "reason": "", "generator_prompt": ""}


async def train_ladder_frontier(
    arc_text: str,
    style: str = "",
    role_setting: str = "",
    budgets: list[int] | None = None,
    samples: int = 1,
    threshold_ratio: float = 0.9,
    progress: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    """极简前沿训练：扫预算档，每档 generate_minimal → expand_ladder 前向推回 → score vs 原文。

    用户要求的「试试 1 如何设计能一步一步再推回 12345」的完整训练版：
    找「最短而充分」的极简设计（score ≥ threshold_ratio × 最高档均分）；
    失败样本反哺 → LLM 归纳合格要素清单（改进极简生成器，防事实漂移）。

    Returns: {budgets, frontier, per_sample, shortest_sufficient, best_mean, threshold, spec}
    """
    from .minimal_train import generate_minimal, score_generated

    if budgets is None or not budgets:
        budgets = [30, 80, 120]
    budgets = sorted(set(budgets))
    target_clean = re.sub(r"\s+", "", arc_text or "")

    per_sample: list[dict[str, Any]] = []
    for bi, budget in enumerate(budgets, start=1):
        for s in range(samples):
            mres = await generate_minimal(
                arc_text, max_chars=budget, style=style, role_setting=role_setting, variant=s)
            minimal = mres["minimal"]
            row: dict[str, Any] = {
                "budget": budget, "sample": s, "minimal": minimal, "len": mres["actual_len"],
                "score": 0.0, "ok": False,
            }
            if minimal:
                try:
                    fl = await expand_ladder(
                        minimal, style=style, role_setting=role_setting,
                        target_len_per_scene=max(300, int(len(target_clean) / 4 * 0.9)),
                    )
                    skeleton = str(fl["l3_chapter"].get("core") or "") + "；" + "；".join(
                        str(b) for b in (fl["l3_chapter"].get("beats") or []))
                    sc = await score_generated(
                        fl["prose"], arc_text, skeleton,
                        ai_flavor=True, ai_flavor_result=fl["review"],
                    )
                    row["score"] = round(sc.get("score", 0.0), 4)
                    row["s_char"] = round(sc.get("s_char", 0.0), 4)
                    row["turn_fidelity"] = round(sc.get("turn_fidelity", 0.0), 4)
                    row["ai_flavor"] = sc.get("ai_flavor")
                    row["gen_len"] = len(re.sub(r"\s+", "", fl["prose"]))
                    row["error"] = None
                except Exception as exc:  # noqa: BLE001
                    row["error"] = str(exc)
            per_sample.append(row)
            if progress:
                progress({
                    "budget": budget, "index": bi, "total": len(budgets),
                    "message": f"预算 {budget} 字样本 {s + 1}/{samples} 完成，分 {row['score']:.3f}",
                })

    frontier: list[dict[str, Any]] = []
    for budget in budgets:
        rows = [r for r in per_sample if r["budget"] == budget]
        mean = sum(r["score"] for r in rows) / len(rows) if rows else 0.0
        frontier.append({
            "budget": budget, "samples": len(rows),
            "mean": round(mean, 4),
            "scores": [r["score"] for r in rows],
            "lens": [r["len"] for r in rows],
            "ok": False,
        })
    best = max((f["mean"] for f in frontier), default=0.0)
    threshold = round(best * threshold_ratio, 4)
    for f in frontier:
        f["ok"] = best > 0 and f["mean"] >= threshold
    for r in per_sample:
        r["ok"] = best > 0 and r["score"] >= threshold

    shortest: dict[str, Any] | None = None
    for f in frontier:
        if f["ok"]:
            ok_row = next((r for r in per_sample if r["budget"] == f["budget"] and r["ok"]), None)
            shortest = {
                "budget": f["budget"], "mean": f["mean"],
                "minimal": ok_row["minimal"] if ok_row else "",
            }
            break

    spec = await _synthesize_frontier_spec(per_sample, frontier)
    return {
        "budgets": budgets,
        "frontier": frontier,
        "per_sample": per_sample,
        "shortest_sufficient": shortest,
        "best_mean": best,
        "threshold": threshold,
        "spec": spec,
    }
