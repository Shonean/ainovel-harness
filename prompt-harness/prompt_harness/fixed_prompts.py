"""
固定 Prompt 持久化模块。

将 _BASELINE_GUARD / VERIFY_USER_TEMPLATE / fidelity_block 等硬编码字符串
移到 data/fixed_prompts.json，支持运行时查看和编辑。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .config import SETTINGS

# ---------------------------------------------------------------------------
# 默认值（代码兜底，JSON 文件不存在时自动生成）
# ---------------------------------------------------------------------------

_DEFAULT_BASELINE_GUARD = (
    "\n\n【以下为不可变基线规则——任何情况下都必须遵守，违反任意一条即为不合格】"
    "\n\n【标点规范·硬约束】全文禁止使用破折号「——」，也禁止使用省略号「……」制造停顿或渲染情绪"
    "（需要停顿用逗号或句号，需要沉默直接写「他没说话」「没有说话」；"
    "仅当场景素材中的原文对白本身含「……」时才原样保留该处）。"
    "需要插入补充说明时用逗号分句，需要强调转折时直接句号断句另起一句。"
    "\n\n【陈述句式·硬约束】绝对禁止以下句式，对白中也必须遵守："
    "\n  ✗ 禁止「不是……而是……」「不是……是……」判断句 → 改为直接陈述"
    "\n  ✗ 禁止「……是……的」强调结构（如「他是知道的」「门是开着的」）→ 改为「他知道」「门开着」"
    "\n  ✗ 禁止「并非」「绝非」等文言否定判断 → 用口语化否定"
    "\n  ✗ 禁止「与其说……不如说……」→ 直接说结论"
    "\n正确示例：「此事必然发生」而非「这不是偶然，而是必然」"
    "\n正确示例：「他知道」而非「他是知道的」"
    "\n\n【连载规则·硬约束】"
    "\n1. 开头直接进入场景或事件。假设读者已在故事中、认识人物，不铺垫、不介绍、不渲染氛围、不做世界观说明。"
    "\n   正确示范：「你迟到了。」陈迹推开铁门，雨声灌了进来。"
    "\n   错误示范：「夜幕低垂，古老的城墙在月色下泛着青灰色的光…」"
    "\n2. 结尾情节推进到当前节点即止，停在动作、反应或对话上。不总结、不升华、不抒情收束、不留悬念钩子。"
    "\n   正确示范：「陈迹把杯子搁下。门外脚步声远了。」"
    "\n   错误示范：「陈迹没回头。他知道身后的脚步声会一直跟着他走进六楼。」"
    "\n   错误示范：「这一夜，他终于明白了人生的真谛…」"
    "\n3. 全文：本章是长篇小说连载的一部分，不是独立短篇。每个场景都假设读者已认识人物、了解背景。"
    "\n\n【对白风格·硬约束】"
    "\n1. 对白直接简短：一句话说一件事，不加语气词、寒暄和修饰。"
    "\n2. 禁止刻意营造慵懒/松弛的对话质感。对话是为了推进情节或展现性格，不是为写对话而写对话。"
    "\n3. 少用「吧」「呢」「嘛」「啊」等句末语气词，除非角色性格设定如此。"
    "\n   好的对白：「你迟到了。」「病历呢？」「明天早上再谈。」"
    "\n   不好的对白：「你今天来晚啦，没事的，反正也不着急呢。」"
    "\n\n【直接叙述·硬约束——防御性写作禁止】"
    "\n1. 直接写你想表达的东西。不要先假设读者会看不懂某处，因而停下来解释、补述或排除误解。"
    "\n2. 很多叙事可以直接叙述，没有解释的必要。已经由对话或动作表达的信息，不要用叙述再复述一遍。"
    "\n3. 禁止多余的因果补注。好的叙事是连续的，读者从上下文已能理解，不需要作者停下来解释。"
    "\n   错误示例：「他之所以选这条路，是因为另外两条路都有人把守。」（解释因果）"
    "\n   正确示例：「他拐进巷子。另外两条路都有人。」（事实说话，不解释「之所以」）"
    "\n   错误示例：「这倒不是说他胆小，他其实并不缺乏勇气，只是此刻必须谨慎。」（替读者排除误解）"
    "\n   正确示例：「他没动。不是时候。」（直接叙述，不防御）"
    "\n\n【正向写作引导——不只是'不要写什么'，更是'应该写什么'】"
    "\n1. 对白间隙公式：对白之间只写角色对对方话语的即时反应（表情变化、身体微动作、或直接接下一句对白）。不要抬头看环境、不要描写墙上/桌上/窗外的物件、不要插入回忆或心理活动。"
    "\n   正确：「他没答话。」「陈迹看了他一眼。」「刘医生点点头。」"
    "\n   错误：「陈迹靠在椅背上，目光落在墙上的锦旗上。锦旗上写着'妙手回春'四个大字，边角已经有些泛黄。」"
    "\n2. 结尾公式：最后一个节拍的动作结果。写完这个结果，立刻停笔。不要加环境描写、不要加氛围渲染、不要加人物心理、不要留悬念预告。"
    "\n   正确：「他把病历合上。走了出去。」"
    "\n   错误：「手指在膝盖上轻轻敲了三下，又停下了。日光灯闪了一下，远处有人嘟囔了一句什么。」"
    "\n3. 场景细节公式：只写角色正在互动的东西。角色在看、在碰、在用的——写。角色没互动的——不写。物件对情节不重要，就不要在叙事里提它。用角色的行动承载场景信息，不要停下来描写环境。"
    "\n4. 叙事连续性：直接叙述你想表达的东西。读者从上下文已能理解，不需要作者停下来解释、补述或制造铺垫。"
    "\n\n【场景设定】场景地点、时间、氛围、物品以用户提供的设定为准。"
    "\n\n【动作描写】动作以传递核心信息为准，点到为止。一个动作用一句话交代即可，不逐帧拆解。"
    "好的写法：「他换个角度继续刮」。不好的写法：「他眯了眯眼，手腕转了个角度，换一道缝继续刮」。"
)

_DEFAULT_VERIFY_USER_TEMPLATE = """## 基本设定
{settings_text}

## 要写的场景

{scene_beats}

{scene_setting_block}

{prose_rhythm_block}

情绪基调：{emotional_arc}
冲突底色：{conflict_type}

{dialogue_instruction}
请按照 system prompt 中的写作规则，写出这个场景的小说正文片段。

{fidelity_block}

**写作要求：**
- 这是小说正文，不是大纲、不是剧本、不是场景说明
- 如果场景包含对白机会，必须写对白——没有对白是严重缺陷
- 保持叙事的自然流动，情节推进到当前节点即止，不要在末尾总结或收束
- 【判断句禁止】禁止使用「不是……是……」「……是……的」等判断句结构"""

_DEFAULT_FIDELITY_BLOCK = (
    "**情节保真要求（硬约束，必须遵守）：**\n"
    "- 必须覆盖上方每一个编号节拍，按编号顺序展开，不得跳过任一节拍。\n"
    "- 不得编造节拍之外的情节转折、角色行动或情报内容。\n"
    "- 不得解开节拍中未交代的悬念。若节拍是「未解决」，正文也必须未解决。\n"
    "- 不得改变节拍结果。事件结果必须与节拍一致。"
)

_DEFAULT_WORD_COUNT = "（根据原文自动推算）"

# v4.0: 极简 system prompt（生成时使用，不再追加 baseline_guard）
_DEFAULT_MINIMAL_PLOT_GENERATOR = (
    "你是「极简剧情压缩专家」。把给定章节原文压缩成尽可能短的一句话极简剧情（不超过预算字数）。\n"
    "必须保留：①人物名+身份/势力归属；②时空地点；③核心冲突；④关键事件序列（≥3 拍，含转折）；"
    "⑤关键对白锚点；⑥结局/钩子。\n"
    "用大白话、不修饰、不用省略号；越短越好，但不得为压缩而丢弃以上要素。"
)

_DEFAULT_MINIMAL_SYSTEM_PROMPT = (
    "你是一个小说作家。请按照以下场景描述直接写出小说正文。只输出正文，不输出任何其他内容。"
)

_DEFAULT_MINIMAL_SYSTEM_PROMPT_OPENING = (
    "你是一个小说作家。你现在写的是小说的开篇部分。请按照以下场景描述直接写出小说正文。"
    "开篇的重点是：建立世界观、引入主角人设、铺设悬念钩子。"
    "只输出正文，不输出任何其他内容。"
)

_DEFAULT_MINIMAL_SYSTEM_PROMPT_MAIN = (
    "你是一个小说作家。你现在写的是小说的正文连载部分。请按照以下场景描述直接写出小说正文。"
    "正文的重点是：推进情节发展、深化人物冲突、保持前后连贯。"
    "只输出正文，不输出任何其他内容。"
)

# v4.0: 改写角度（variant angles）
_DEFAULT_VARIANT_ANGLES: list[str] = [
    "角度一：强化人物弧光 — 在改写场景段落时，重点突出主角的性格特征和成长潜能。",
    "角度二：铺设世界观 — 在改写场景段落时，自然融入环境描写和世界观细节。",
    "角度三：制造悬念钩子 — 在改写场景段落时，植入悬念或未解答的问题。",
    "角度四：渲染情绪基调 — 在改写场景段落时，强化当前场景的情绪色彩。",
    "角度五：精简叙事效率 — 在改写场景段落时，去除冗余修饰，用最短的文字传递最多的信息。",
    "角度六：强化对白驱动 — 在改写场景段落时，以角色对白为场景主线。",
    "角度七：丰富感官细节 — 在改写场景段落时，加入多感官描写。",
    "角度八：突显冲突张力 — 在改写场景段落时，强化对抗双方的立场差异和行动冲突。",
]

_DEFAULT_VARIANT_ANGLES_OPENING: list[str] = [
    "角度一：强化人物弧光 — 在改写场景段落时，重点突出主角的性格特征、成长潜力和初始状态，让读者在第一段就建立起对主角的认知和共情。",
    "角度二：铺设世界观 — 在改写场景段落时，自然融入环境描写、势力关系和世界观细节，让读者感受到故事世界的厚度和真实感。",
    "角度三：制造悬念钩子 — 在改写场景段落时，在开头或结尾植入悬念或未解答的问题，引发读者的好奇心和继续阅读的欲望。",
    "角度四：渲染情绪基调 — 在改写场景段落时，强化当前场景的情绪色彩（紧张、温情、悲壮、幽默等），让读者迅速进入情感状态。",
    "角度五：精简叙事效率 — 在改写场景段落时，去除冗余修饰，用最短的文字传递最多的信息，保持叙事节奏紧凑。",
    "角度六：强化对白驱动 — 在改写场景段落时，以角色对白为场景主线，叙述仅作为对白的补充和衔接，增加戏剧张力。",
    "角度七：丰富感官细节 — 在改写场景段落时，加入视觉、听觉、触觉、嗅觉等多感官描写，让场景立体而生动。",
    "角度八：突显冲突张力 — 在改写场景段落时，强化对抗双方的立场差异和行动冲突，让读者感受到剑拔弩张的紧张感。",
]

_DEFAULT_VARIANT_ANGLES_MAIN: list[str] = [
    "角度一：推动情节发展 — 在改写场景段落时，聚焦于情节的推进，确保每个段落都在向故事的下一个关键节点迈进。",
    "角度二：深化人物关系 — 在改写场景段落时，通过互动细节展现角色之间关系的变化（信任加深、矛盾激化、立场转变等）。",
    "角度三：控制节奏起伏 — 在改写场景段落时，在紧张与舒缓之间切换，避免长时间保持同一节奏导致读者疲劳。",
    "角度四：保持前后连贯 — 在改写场景段落时，注意与前后章节的衔接，引用或呼应前文的伏笔和设定，维持叙事的整体性。",
    "角度五：提升对白质量 — 在改写场景段落时，让每句对白都有明确的功能（推进情节、揭示信息、展现性格），消除无意义的寒暄和废话。",
    "角度六：压缩冗余描写 — 在改写场景段落时，删除对核心情节没有贡献的描写和叙述，用更精炼的文字达到相同的表达效果。",
    "角度七：强化冲突升级 — 在改写场景段落时，让冲突逐步升级（从隐到显、从小到大），给读者持续的紧张感和期待感。",
    "角度八：营造场景氛围 — 在改写场景段落时，通过环境描写和细节刻画营造统一的氛围基调，增强场景的沉浸感。",
]

# ── v6.3 阶梯各级不变 prompt（AI 创作 l1-l5 各配各自的不变 prompt）────────
# 每个生成级注入「本级不变prompt」：本级输出必须遵守的硬性不变规则
# （保留要素/格式/禁令），运行时可编辑（fixed_prompts.json）。
_DEFAULT_LADDER_INVARIANTS: dict[str, str] = {
    "l1": (
        "【l1 不变prompt】一句话极简剧情必须保留全部关键要素：\n"
        "①人物名+身份/势力归属；②时空地点；③核心冲突；④关键事件序列（≥3拍，含转折）；"
        "⑤关键对白锚点；⑥结局/钩子。\n"
        "越短越好，但不为压缩丢弃以上任何一类要素；完整陈述，不抽象总结、不用省略号。"
    ),
    "l2": (
        "【l2 不变prompt】保留 l1 全部关键要素并扩写，必须包含起因/核心冲突/转折/结局四段。\n"
        "人物与地点齐全、具体可写，不抽象总结；人物不得改名、地点/数字/事件不得丢失，"
        "不得虚构人物身份/职业/背景。"
    ),
    "l3": (
        "【l3 不变prompt】按时序推进，把情节概要拆成各章章纲，每章含：标题+一句话核心+2-5拍关键事件。\n"
        "保留重建场景所需的全部关键信息（人物/事件/冲突/结局），具体可写；人物不得改名、"
        "地点/数字不得丢失，不虚构人物身份/职业/背景，具体设定宁缺毋错。"
    ),
    "l4": (
        "【l4 不变prompt】每章拆 3-5 场景，每场景 6 类叶子：环境/动作/对白/心理/冲突/细节。\n"
        "场景覆盖该章全部拍子、不遗漏；只写本场景时间段内的事，不提前写后续场景情节；"
        "对白列全部引号轮次、说话动词带神态（如『阴阳怪气道』），不得统一占位『开口说』；"
        "元素白名单/硬约束生效，场景不得出现未参与元素。"
    ),
    "l5": (
        "【l5 不变prompt】素材逐条落实、顺序不得改变、不得省略；对白按契约原词、用“”引号。\n"
        "写足目标字数，不注水、不流水账；【段首多样化·硬约束】①连续两段不得以同一词语开头"
        "（人物名/物件名/地点名都算，「豆腐摊→豆腐摊」「沈石→沈石」都不行）；"
        "②同一人物名开头的段落不得超过全章段落数的 1/3；③指代同一人物时交替用主语省略/代词/"
        "动作先行/环境先行/对白开头；④章首前两段不得从同一物件/场景元素写起（不要两段都先写"
        "豆腐摊，第二段改从人物动作/对白/环境切入）；【过场一笔带过·硬约束】⑤巡街/走路/查看/"
        "摸装备等过场动作一句带过（最多一句），不逐微动作展开，不写「如何走到现场」前奏——从"
        "场景现场直接开始；⑥固定装备（更梆/打更灯/锣/灯笼）除非当下信息相关（如借灯照妖气）"
        "不展开描写，不写裂纹/油烟/歪斜/破角这类无信息量细节；⑦感官/氛围（余温/夜色/水汽/寒气）"
        "最多一句且须服务于当下判断或情绪，物件清单（柜台上摆着X、留着Y、带着Z）删除或压缩为"
        "一个；⑧写不足目标字数时宁缺毋凑——对白轮写充分、冲突回合写足来达字数，素材本就有限"
        "就写短，绝不靠展开过场/感官/装备细节补字；具体设定宁缺毋错，"
        "不编造素材外的人物身份/职业/背景/情节，同一信息只写一次。"
    ),
}

_DEFAULT_FIXED_PROMPTS: dict[str, Any] = {
    # v3.x 旧字段（保留向后兼容，v4.0 不再使用）
    "baseline_guard": _DEFAULT_BASELINE_GUARD,
    "verify_user_template": _DEFAULT_VERIFY_USER_TEMPLATE,
    "fidelity_block": _DEFAULT_FIDELITY_BLOCK,
    "word_count_guide": _DEFAULT_WORD_COUNT,
    # v4.0 新字段
    "minimal_system_prompt": _DEFAULT_MINIMAL_SYSTEM_PROMPT,
    "minimal_system_prompt_opening": _DEFAULT_MINIMAL_SYSTEM_PROMPT_OPENING,
    "minimal_system_prompt_main": _DEFAULT_MINIMAL_SYSTEM_PROMPT_MAIN,
    "variant_angles": _DEFAULT_VARIANT_ANGLES,
    "variant_angles_opening": _DEFAULT_VARIANT_ANGLES_OPENING,
    "variant_angles_main": _DEFAULT_VARIANT_ANGLES_MAIN,
    # v5.29 极简推导训练：极简内容生成器 prompt（压缩前沿训练会优化并写回这里）
    "minimal_plot_generator": _DEFAULT_MINIMAL_PLOT_GENERATOR,
    # v6.3 阶梯各级不变 prompt（AI 创作 l1-l5 各配不变 prompt）
    "ladder_invariant_l1": _DEFAULT_LADDER_INVARIANTS["l1"],
    "ladder_invariant_l2": _DEFAULT_LADDER_INVARIANTS["l2"],
    "ladder_invariant_l3": _DEFAULT_LADDER_INVARIANTS["l3"],
    "ladder_invariant_l4": _DEFAULT_LADDER_INVARIANTS["l4"],
    "ladder_invariant_l5": _DEFAULT_LADDER_INVARIANTS["l5"],
}


# ---------------------------------------------------------------------------
# 文件路径
# ---------------------------------------------------------------------------

_fixed_prompts_path: Path | None = None


def _get_path() -> Path:
    global _fixed_prompts_path
    if _fixed_prompts_path is None:
        _fixed_prompts_path = (SETTINGS.data_dir / "fixed_prompts.json").resolve()
    return _fixed_prompts_path


# ---------------------------------------------------------------------------
# 读写
# ---------------------------------------------------------------------------

_cache: dict[str, Any] | None = None


# ---------------------------------------------------------------------------
# 模板安全：禁止角色身份信息占位符
# ---------------------------------------------------------------------------
# 这些占位符会从原文提取角色名/身份/动机，注入用户 prompt 后导致
# 源小说角色信息泄露到生成文本中。此列表为硬性禁止项，_load() 自动剥离。
_FORBIDDEN_TEMPLATE_PATTERNS: list[str] = [
    "{character_goals}",
    "{character_profiles}",
    "角色状态：",
]


def _sanitize_template(text: str) -> str:
    """剥离模板中的禁止占位符，永不将角色身份信息注入用户 prompt。"""
    for pattern in _FORBIDDEN_TEMPLATE_PATTERNS:
        # 移除含禁止占位符的整行（包括前后空白行）
        import re
        text = re.sub(r'\n*[^\n]*' + re.escape(pattern) + r'[^\n]*\n*', '\n', text)
    # 清理多余的连续空行
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text


def _load() -> dict[str, Any]:
    """从 JSON 文件加载，不存在则用默认值。启动时自动清洗禁止占位符。"""
    path = _get_path()
    if path.exists():
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            # 合并默认值——新增字段自动补全
            merged = dict(_DEFAULT_FIXED_PROMPTS)
            merged.update(data)
            # 安全清洗：剥离禁止占位符
            for key in ("verify_user_template",):
                if key in merged:
                    cleaned = _sanitize_template(merged[key])
                    if cleaned != merged[key]:
                        merged[key] = cleaned
            return merged
        except (json.JSONDecodeError, OSError):
            pass
    # 首次运行：写入默认值
    data = dict(_DEFAULT_FIXED_PROMPTS)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except OSError:
        pass
    return data


def _save(data: dict[str, Any]) -> None:
    """写入 JSON 文件并更新内存缓存。"""
    global _cache
    path = _get_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        _cache = data
    except OSError:
        pass


# ---------------------------------------------------------------------------
# 公开 API
# ---------------------------------------------------------------------------


def get_fixed_prompts() -> dict[str, Any]:
    """返回全部固定 prompt（含缓存）。"""
    global _cache
    if _cache is None:
        _cache = _load()
    return dict(_cache)


def get_baseline_guard() -> str:
    """获取基线规则文本（供 optimizer.py 拼接）。"""
    d = get_fixed_prompts()
    return d.get("baseline_guard", _DEFAULT_BASELINE_GUARD)


def get_verify_user_template() -> str:
    """获取用户消息模板（供 templates.py）。"""
    d = get_fixed_prompts()
    return d.get("verify_user_template", _DEFAULT_VERIFY_USER_TEMPLATE)


def get_fidelity_block() -> str:
    """获取情节保真约束块（供 build_verify_user_prompt）。"""
    d = get_fixed_prompts()
    return d.get("fidelity_block", _DEFAULT_FIDELITY_BLOCK)


def update_fixed_prompts(updates: dict[str, Any]) -> dict[str, Any]:
    """更新一个或多个字段，写回文件，返回全量。"""
    current = _load()
    current.update(updates)
    _save(current)
    return dict(current)


def reset_to_defaults() -> dict[str, str]:
    """重置为默认值。"""
    data = dict(_DEFAULT_FIXED_PROMPTS)
    _save(data)
    return data


def get_defaults() -> dict[str, Any]:
    """返回默认值（不参考文件）。"""
    return dict(_DEFAULT_FIXED_PROMPTS)


def get_minimal_system_prompt(section: str | None = None) -> str:
    """v4.0：获取极简 system prompt（生成时使用）。

    Args:
        section: None=默认, "opening"=开篇, "main"=正文
    """
    d = get_fixed_prompts()
    if section == "opening":
        return d.get("minimal_system_prompt_opening", _DEFAULT_MINIMAL_SYSTEM_PROMPT_OPENING)
    elif section == "main":
        return d.get("minimal_system_prompt_main", _DEFAULT_MINIMAL_SYSTEM_PROMPT_MAIN)
    return d.get("minimal_system_prompt", _DEFAULT_MINIMAL_SYSTEM_PROMPT)


def get_variant_angles(section: str | None = None) -> list[str]:
    """v4.0：获取改写角度列表。

    Args:
        section: None=默认, "opening"=开篇, "main"=正文
    """
    d = get_fixed_prompts()
    if section == "opening":
        return d.get("variant_angles_opening", _DEFAULT_VARIANT_ANGLES_OPENING)
    elif section == "main":
        return d.get("variant_angles_main", _DEFAULT_VARIANT_ANGLES_MAIN)
    return d.get("variant_angles", _DEFAULT_VARIANT_ANGLES)


def get_ladder_invariant(level: str) -> str:
    """获取阶梯某级（l1-l5）的不变 prompt（AI 创作各级生成注入用）。

    fixed_prompts.json 里可编辑覆盖（ladder_invariant_l1..l5），缺省回退代码默认。
    """
    d = get_fixed_prompts()
    key = f"ladder_invariant_{level}"
    val = d.get(key)
    if isinstance(val, str) and val.strip():
        return val.strip()
    return _DEFAULT_LADDER_INVARIANTS.get(level, "").strip()
