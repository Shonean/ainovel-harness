"""
硬约束注入模块（Phase 2）

为所有 agent 的 system_prompt 注入"三大硬约束"头部和自由度级别指令。
不重复 core-constraints.md 的完整文本，只构建最精简的铁律声明。

三大硬约束来自 references/shared/core-constraints.md：
  1. 大纲即法律
  2. 设定即物理
  3. 新实体必须可识别
"""
from __future__ import annotations

from ..core.constants import FreedomLevel, DEFAULT_FREEDOM_LEVEL

# ---------------------------------------------------------------------------
# 3 行硬约束头部模板
# ---------------------------------------------------------------------------

_HEADER_PREFIX = "===== 硬约束（以下规则不可协商，优先级高于一切其他指令）====="
_HEADER_SUFFIX = "============================================================"

# 人称硬约束（所有自由度级别通用）
_LINE_NARRATIVE = "[硬约束0] 叙事人称：全文必须使用第三人称（他/她/角色名），严禁使用第一人称（我/我们）和第二人称（你/你们）。主角的心理活动用「内心独白+引号」或自由间接引语表达，绝不使用'我想'、'我感觉'等第一人称叙述。对话中角色自称'我'或对他人说'你'、'你们'是允许的。"
_LINE_DIALOGUE = "[硬约束0b] 对话格式：所有角色对话必须用中文弯引号“”包裹。禁止使用直角引号「」『』和英文直引号\"\"。对话必须带潜台词或意图（试探/回避/施压/诱导），不能是纯信息交换。对话与叙述之间必须有动作或神态穿插，禁止连续6句以上纯对话。"

# 0% 自由度：绝对语气
_LINE1_STRICT = "[硬约束1] 大纲即法律：严格执行大纲，不得擅自发挥。本章所有情节必须与章纲保持完全一致——不新增、不删减、不改动节点。"
_LINE2_STRICT = "[硬约束2] 设定即物理：角色实力/招式/物品/关系必须≤已有记录（index.db/设定集/前文正文）。使用前必须先查询确认，不凭空创造。"
_LINE3_STRICT = "[硬约束3] 新实体必须可识别：任何新角色/地点/物品/组织必须有明确名称和充分描写，确保 data-agent 可自动提取。模糊指代（一个神秘人/某个地方/一件宝物）必须在同段内澄清。"

# 10% 自由度：核心约束不变，描述层放宽
_LINE1_10PCT = "[硬约束1] 大纲即法律：严格执行大纲节点（CBN/CPNs/CEN），不得增删。允许在节点之间的描述细节上有细微调整。"
_LINE2_10PCT = "[硬约束2] 设定即物理：角色能力≤已有记录。允许在已有能力范围内调整表现方式（如速度/角度/顺序），不得越级或创造新能力。"
_LINE3_10PCT = "[硬约束3] 新实体必须可识别：新增角色/地点/物品须有明确名称。允许对已有实体的描述做轻微扩展。"

# 20% 自由度：允许 minor 剧情调整
_LINE1_20PCT = "[硬约束1] 大纲即法律：核心节点（CBN/CEN）不可变。允许minor剧情调整（如分支对话顺序、次要事件的轻重分配），不改变核心剧情走向。"
_LINE2_20PCT = "[硬约束2] 设定即物理：角色核心能力≤已有记录。允许在合理范围内对次要角色/场景做功能性扩展。"
_LINE3_20PCT = "[硬约束3] 新实体必须可识别：重要新角色/地点须有明确名称和描写。次要路人/背景物品可简化处理。"

# ---------------------------------------------------------------------------
# 自由度级别指令
# ---------------------------------------------------------------------------

_INSTRUCTION_0PCT = (
    "\n\n---\n"
    "【自由度级别：0% — 严格遵守】\n"
    "当前自由度设置为 0%，即：严格遵守所有约束，不允许任何发挥。\n"
    "所有约束为硬性要求，不得违反。任何偏离大纲/设定/格式规范的行为均视为 blocking violation。\n"
    "写作质感规范（5 级 L5→L1：对话/叙事深度/句式/情感/收尾）全部激活。\n"
)
_INSTRUCTION_10PCT = (
    "\n\n---\n"
    "【自由度级别：10% — 允许细微描述调整】\n"
    "当前自由度设置为 10%，即：允许细微描述调整（如环境细节、动作顺序、感官描写方式），\n"
    "但不改变剧情走向和核心设定。硬约束和写作质感规范仍然有效。\n"
)
_INSTRUCTION_20PCT = (
    "\n\n---\n"
    "【自由度级别：20% — 允许 minor 剧情调整】\n"
    "当前自由度设置为 20%，即：允许 minor 剧情调整（如分支对话、次要事件顺序），\n"
    "不改变核心剧情和人物设定。三大定律仍然有效，写作质感规范适度放松但不可完全忽略。\n"
)


def build_3line_header(freedom_level: FreedomLevel = DEFAULT_FREEDOM_LEVEL) -> str:
    """返回盒状硬约束头部（含人称+三大硬约束）。"""
    narr = [_LINE_NARRATIVE, _LINE_DIALOGUE]  # 人称+对话约束始终生效
    if freedom_level == FreedomLevel.ZERO:
        lines = narr + [_LINE1_STRICT, _LINE2_STRICT, _LINE3_STRICT]
    elif freedom_level == FreedomLevel.LOW:
        lines = narr + [_LINE1_10PCT, _LINE2_10PCT, _LINE3_10PCT]
    elif freedom_level == FreedomLevel.MEDIUM:
        lines = narr + [_LINE1_20PCT, _LINE2_20PCT, _LINE3_20PCT]
    else:
        lines = narr + [_LINE1_STRICT, _LINE2_STRICT, _LINE3_STRICT]

    return "\n".join([_HEADER_PREFIX] + lines + [_HEADER_SUFFIX])


def build_freedom_level_instruction(
    freedom_level: FreedomLevel = DEFAULT_FREEDOM_LEVEL,
) -> str:
    """返回自由度级别说明文本，附加到 system_prompt 末尾。"""
    if freedom_level == FreedomLevel.ZERO:
        return _INSTRUCTION_0PCT
    elif freedom_level == FreedomLevel.LOW:
        return _INSTRUCTION_10PCT
    elif freedom_level == FreedomLevel.MEDIUM:
        return _INSTRUCTION_20PCT
    else:
        return _INSTRUCTION_0PCT


def build_constrained_prompt(
    base_prompt: str,
    freedom_level: FreedomLevel = DEFAULT_FREEDOM_LEVEL,
    *,
    include_header: bool = True,
    include_instruction: bool = True,
) -> str:
    """
    将 3 行硬约束头部和自由度级别指令注入到 base_prompt 中。

    结果结构：
      [硬约束头部]
      [base_prompt 原文]
      [自由度级别指令]

    参数：
      include_header: 是否注入 3 行头部（默认 True）
      include_instruction: 是否注入自由度指令（默认 True）
    """
    parts: list[str] = []

    if include_header:
        parts.append(build_3line_header(freedom_level))
        parts.append("")  # 空行分隔

    parts.append(base_prompt)

    if include_instruction:
        parts.append(build_freedom_level_instruction(freedom_level))

    return "\n".join(parts)


def build_writing_guide() -> str:
    """返回正向写作引导（不只是"不要写什么"，而是"应该写什么"）。

    从"堵"（负面禁止）转为"疏"（正向公式）：
    - 对白间隙公式：对话之间只放角色即时反应，不描写环境物件
    - 结尾公式：最后一个节拍的动作结果，写完立刻停笔
    - 场景细节公式：只写角色正在互动的东西
    - 叙事连续性：直接叙述，不解释、不补述、不铺垫

    同时被 _draft.py（工作台实际生成）与 prompt-harness（训练台）引用，
    确保两套系统共享同一份核心写作规则。
    """
    return (
        "【正向写作引导——不只是'不要写什么'，更是'应该写什么'】\n"
        "1. 对白间隙公式：对白之间只写角色对对方话语的即时反应（表情变化、身体微动作、"
        "或直接接下一句对白）。不要抬头看环境、不要描写墙上/桌上/窗外的物件、"
        "不要插入回忆或心理活动。\n"
        "   正确：「他没答话。」「林川看了他一眼。」「刘医生点点头。」\n"
        "   错误：「林川靠在椅背上，目光落在墙上的锦旗上。锦旗上写着'妙手回春'四个大字，边角已经有些泛黄。」\n"
        "2. 结尾公式：最后一个节拍的动作结果。写完这个结果，立刻停笔。"
        "不要加环境描写、不要加氛围渲染、不要加人物心理、不要留悬念预告。\n"
        "   正确：「他把病历合上。走了出去。」\n"
        "   错误：「手指在膝盖上轻轻敲了三下，又停下了。日光灯闪了一下，远处有人嘟囔了一句什么。」\n"
        "3. 场景细节公式：只写角色正在互动的东西。角色在看、在碰、在用的——写。"
        "角色没互动的——不写。物件对情节不重要，就不要在叙事里提它。"
        "用角色的行动承载场景信息，不要停下来描写环境。\n"
        "4. 叙事连续性：直接叙述你想表达的东西。读者从上下文已能理解，"
        "不需要作者停下来解释、补述或制造铺垫。\n"
    )
