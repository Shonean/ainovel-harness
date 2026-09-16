"""优化器使用的固定 Prompt 模板。

v2 核心变更：
- LLM 自己设计 prompt 结构，不预设字段
- 评分改为风格维度（L5→L1），不再奖励内容重叠
- 新增泛化测试、结构评估、验证生成模板
"""
from __future__ import annotations

from .fixed_prompts import get_fidelity_block, get_verify_user_template

# ---------------------------------------------------------------------------
# 风格分析（保持不变）
# ---------------------------------------------------------------------------

REVERSE_ENGINEER_PROMPT = """你是一名资深网文编辑。请分析下面这段高质量网文正文，提取它的写作特征和**剧情结构**，只输出 JSON，不要解释。

目标段落：
---
{target_text}
---

请输出以下字段：

### 基础字段
- genre: 题材大类（如"玄幻","都市","仙侠","科幻","历史","悬疑"）
- scene_type: 场景类型（如"战斗","对话","内心独白","环境描写","转折","日常","爆发"）
- perspective: 叙事视角（如"第三人称限知","第一人称","全知"）
- named_entities: 段落中出现的专有名词列表（角色名、功法名、地名、组织名等）
- style_notes: 文风关键词，如"短句有力","画面感强","心理细腻","对白市井","华丽铺陈"等
- tone: 情绪基调，如"紧张","悲怆","热血","压抑","温馨"
- plot_summary: 用一两句话概括这段在写什么情节

### 剧情结构字段（必填，仔细阅读原文后填写）
- scene_beats: 数组，列出这段内容的**场景节拍**——按时间顺序描述发生了什么，每一条对应一段动作/对话/反应（3-8 条，每条 10-30 字）。
  示例：['沈七推门走进大堂，目光扫过每个角落', '柜台后的小二抬头看了他一眼']
- conflict_type: 字符串，冲突类型（"人物vs人物" / "人物vs环境" / "人物vs自我" / "无冲突纯过渡" / "暗流涌动"）
- emotional_arc: 字符串，本段的情绪走向，格式"起始情绪→结尾情绪"。
  示例：'警觉→紧张加剧'、'平静→温馨'、'压抑→爆发'
- key_dialogue_beats: 数组（如果没有对白则为空数组），列出关键对白节点及其潜台词。每条约 15-30 字。
  示例：['小二问客官打尖还是住店——试探性的招呼', '沈七说来壶茶，等人——简短敷衍']
- narrative_technique: 字符串，主要叙事手法（"顺叙" / "倒叙" / "插叙" / "内心独白" / "多线并行" / "回忆与现实交织"等）

### 场景设定字段（新增，从原文中直接提取，不要推断）
- setting: 对象，包含场景的物理环境信息：
  - location: 地点描述（从原文直接提取，如"城隍庙后院柴房"）。如果原文未明确写出，填"（未提及）"
  - time_of_day: 时间（如"清晨""深夜""午后"）。未提及则填"（未提及）"
  - atmosphere: 氛围关键词（1-3个，如"阴冷潮湿""尘土飞扬"）
  - key_objects: 场景中出现的物品及其状态（数组，如["半片竹筒，底铺薄层白粉","匕首，刃口卷了"]）
  - characters_present: 出场角色列表及其位置/姿态（如["陈迹-蹲在墙根下","沈七-门外站立"]）
- world_state: 对象，记录本段涉及的世界状态信息：
  - established_facts: 本段新建立或确认的事实（数组，每条 10-30 字）
  - pending_mysteries: 本段提出的悬而未决的疑问（数组，如["敲门的三人是谁——未交代"]）
  - character_knowledge: 角色已知但读者可能不知的信息（字符串，无则填""）

### 行文节奏字段（新增，从原文直接观察，描述"怎么写"而非"写什么"）
- prose_economy: 对象，描述原文的行文节奏和写作经济性：
  - noun_reference: 名词回指习惯。选项：
    "pronoun_dominant" —— 首次全称后主要用代词/省略，不反复重复全名
    "mixed" —— 名词和代词混用
    "name_heavy" —— 反复重复全名，很少用代词
    依据：挑一个在文中出现≥3次的物品，统计它每次的称呼方式（全名/代词/省略）
  - paragraph_ending: 段落收尾方式。选项：
    "action_cutoff" —— 动作截止，不延伸评价或推测
    "sensory" —— 感官/环境细节收尾（声音、光线、触感等）
    "evaluation" —— 以角色的评价/推测/计划延申收尾（如"少说还得半个月"）
    "transition" —— 以过渡句收尾，为下一段铺垫
    依据：看每段最后一句在做什么——是动作、感官、评价还是过渡
  - transition_style: 动作/场景转场风格。选项：
    "jump_cut" —— 跳切，只写结果，省略中间步骤
    "minimal_bridge" —— 1-2个衔接动作
    "full_chain" —— 逐帧描写每步微动作
    依据：观察原文的动作链——中间步骤是否被省略
  - character_intro_depth: 新角色出场描写深度。选项：
    "none" —— 不做外貌描写
    "single_trait" —— 仅1个特征（如仅写"短褐草鞋"）
    "two_traits" —— 2个特征（如衣着+体态）
    "catalog" —— 3+个特征，从头到脚全面描写
    依据：原文中新角色出场时给了多少外貌信息
  - narrator_intrusion: 叙述者介入程度。选项：
    "never" —— 纯客观呈现，从不评价
    "rare" —— 极少介入判断
    "occasional" —— 偶尔插入一句判断
    "frequent" —— 频繁做定性评价
    依据：原文中叙述者是否跳出来做定性判断（"活脱脱一个XX""分明是""俨然一副"等句子）
"""


# ---------------------------------------------------------------------------
# v4.0 统一段落提取（替代 REVERSE_ENGINEER_PROMPT 的结构化 JSON）
# ---------------------------------------------------------------------------

UNIFIED_PARAGRAPH_EXTRACTOR = """你是一名资深网文编辑。请将下面这段高质量网文正文，改写为一段**场景描述段落**——这段描述将直接作为 prompt 发给 LLM 来重新生成这个场景的小说正文。

目标段落：
---
{target_text}
---

## 输出格式

直接输出一段连贯的中文段落（200-400 字），不要加任何标题、标签或 JSON 包裹。段落必须自然融合以下信息：

### 必须包含的内容（按此顺序自然融入）

1. **类型标记**（开头）：「写一段XX题材的小说片段」——题材从原文推断（玄幻武侠/都市日常/仙侠/历史/悬疑等）

2. **主角设定**：姓名、身份、核心性格特征（2-3 个关键词）、当前在做什么

3. **前情提要**：本场景之前发生了什么（1-2 句话概括必要背景）

4. **本场景情节**：按时间顺序描述本场景中发生的事件——谁做了什么、谁说了什么、发生了什么转折。这是段落的核心，占主要篇幅

5. **关键角色互动**：与其他角色的对话和博弈关系

6. **写作重点**：本文需要特别侧重的方面，如「全程刻画XX的心理活动」「重点描写XX和XX之间的试探博弈」等

7. **文风约束**（自然融入，不用清单格式）：
   - 开头不做介绍，不可当为一个故事的开头来写，写的只是一个文章中的一部分
   - 末尾不做总结、升华或收束段，情节推进到当前节点即止
   - 直接陈述，不用「不是……是……」「……是……的」等判断句
   - 动作以传递核心信息为准，点到为止，不逐帧拆解
   - 角色互动必须有自然对白，纯叙述无对话段落连续不超过 2 段
   - 不用破折号「——」

### 关键原则

- **自然段落**：这是给 LLM 看的场景描述，不是规则清单。所有约束要自然融入段落中
- **具体不空洞**：角色名、地名、事件都要具体写出来
- **信息密集**：每句话都在传递有用信息，不写「请根据以上要求写作」之类的废话
- **直接输出**：只输出段落本身，不加任何前缀/后缀"""


# ---------------------------------------------------------------------------
# v4.0 变体生成（替代 REFINEMENT_PROMPT，人工选择驱动）
# ---------------------------------------------------------------------------

VARIANT_GENERATOR_PROMPT = """你是一名 Prompt 优化师。你有一个基础场景描述段落，用它可以生成小说正文。现在你需要从 3 个不同角度改写这个段落，生成 3 个变体。

## 基础段落
---
{base_paragraph}
---

## 本轮改写角度

{angle_descriptions}

## 改写要求

对每个角度：
1. 从该角度重新审视基础段落，思考哪些地方可以强化
2. 改写整个段落——不是只改一两句，而是通篇调整表述方式、信息密度、强调点
3. 保持段落结构完整（类型标记→主角设定→前情→情节→互动→重点→约束），但各部分的篇幅和措辞可以调整
4. 保持原文中所有具体信息（角色名、事件、关系），不能编造原文没有的情节
5. 每个变体 200-400 字

## 输出格式

只输出一个合法 JSON 对象：

```json
{{
  "variants": [
    {{"angle": "角度1名称", "paragraph": "变体段落1"}},
    {{"angle": "角度2名称", "paragraph": "变体段落2"}},
    {{"angle": "角度3名称", "paragraph": "变体段落3"}}
  ]
}}
```"""

# ---------------------------------------------------------------------------
# 候选 Prompt 生成（核心变更：LLM 自己设计结构）
# ---------------------------------------------------------------------------

CANDIDATE_GENERATOR_PROMPT = """你的任务是从原文中提取风格特征，然后生成一个**极简**的写作 system prompt（200-300 字）。

**重要：用户消息中已经包含详细的场景上下文**——场景节拍（逐条列出要写的事件）、场景设定（地点/时间/物品/角色位置）、世界状态（已确立事实/未解悬念）、行文节奏（指代习惯/段落收尾/转场方式）。你的 system prompt **只负责写作风格和 AI 味防范**，不要重复用户消息已有的内容。

**这决定了 system prompt 应该是短而精准的风格指南，而非面面俱到的规则清单。** 每次生成是连载中的一个片段，不是独立短篇故事。

## 目标文本
---
{target_text}
---

{category_prior}

{cross_run_lessons}

{retrieved_examples}

## 核心要求

你设计的 prompt 是**不变层**——只包含写作规则、结构约束、输出格式等通用规则。**不能**包含目标段落中的具体角色名、地名、功法名、情节细节——这些在用户消息的可变层中提供。

## 规则生成原则

1. **越短越好**——目标 200-300 字。3-5 条精准的规则比 10 条互相矛盾的规则效果好得多。
2. **只写可验证的规则**——"不使用破折号"是可验证的，"文笔要有张力"不是。
3. **方向性引导优于硬性百分比**——"长短句交错有节奏"比"短句占60%、中句占30%"更容易被模型正确执行。
4. **禁止角色扮演**——prompt 以规则开头，不是"你是一名XX"。
5. **禁止主观感受词汇**——不出现"冷峻""克制""有张力""流畅"等无法量化的评价词。

## 必须包含的核心约束（不超过 5 条）

1. **AI味禁令**：禁止「……是……的」「不是……是……」判断句；禁止「心中涌起」「不由得」等情感标签；禁止解释性总结。
2. **连载规则**：末尾不总结、升华、收束；情节推进到当前节点即止。
3. **对白引导**：若场景含角色互动，必须有自然对话。
4. **破折号禁止**：不使用「——」。
5. **句式方向**：从原文观察句长分布，写一句方向性引导（如"以短句为主，关键时刻用长句铺陈"）。

你只能输出一个合法 JSON 对象：
```json
{{
  "sentence_length_profile": "<句长分布观察>",
  "paragraph_composition": "<段落构成观察>",
  "dialogue_density": "<对白密度观察>",
  "connective_patterns": "<连接方式观察>",
  "opening_pattern": "<首句类型观察>",
  "sentence_structure_rules": "<句式方向性引导，1-2句>",
  "system_prompt": "<完整的 system prompt 文本（200-300 字纯文本，以规则开头，不以角色扮演开头）>"
}}
```"""


# ---------------------------------------------------------------------------
# 迭代改进（不再引用 ROUGE/BLEU，改为风格维度反馈）
# ---------------------------------------------------------------------------

REFINEMENT_PROMPT = """你正在优化一个中文网文写作的**系统 Prompt**。你的目标是让 LLM 用这个 prompt 生成的正文，尽可能接近目标原文。

{escalation_instruction}

{lessons_block}

**重要：这是为长篇连载小说设计的写作 prompt。** 每次生成是连载中的一个片段，不是独立短篇故事。

## ⚠️ 硬性字数上限（不可违反）

**system prompt 必须控制在 400 字以内。** 越短的 prompt 越容易被模型准确执行。

## 目标风格段落
---
{target_text}
---

## 当前 Prompt
```
{current_system_prompt}
```

## 学习文本（基于当前 prompt 的实际生成结果）
---
{gen_a}
---

## 📊 相似度评分

### 字符级相似度
{similarity_feedback}

{length_feedback}

## 具体修改指令

基于以上反馈，请执行以下修改（**严格按优先级执行，不可跳过**）：

1. **🔴 硬性字数检查（最高优先级）** — 如果当前 system prompt 超过 400 字，**本轮只做删减**。
2. **🔴 相似度<0.30** — 当前相似度极低，说明 prompt 完全不对路。**忘掉当前 prompt，从零重新设计**——观察原文的句长、对白密度、叙事节奏，写一套全新的规则。
3. **🟡 相似度0.30-0.60** — 中等相似度。需要**实质性改动**——增减规则、调整约束强度、或换一种组织方式。关注生成文本与原文的具体差异点。
4. **🟢 相似度>0.60** — 方向对了，**微调**即可。重点放在缩小长度差异和消除残留的不匹配点。不要大改。
5. **长度对齐** — 如果长度差异超出 ±10%，调整 prompt 中的字数约束或改变叙事的详略程度。

**核心原则**：
- **少即是多**。3 条精准的规则比 10 条互相矛盾的规则效果好得多。
- **最高目标：让生成文本和原文在字符级上一模一样**。每改一条规则，自问：这条规则能让生成更接近原文吗？
- 禁止使用角色扮演型描述
- 禁止添加不可量化验证的主观写作规则

请输出改进后的 system prompt，并附带一句改动摘要。只输出合法 JSON：
```json
{{
  "system_prompt": "<改进后的 system prompt 文本（必须 ≤ 400 字）>",
  "change_summary": "<一句话准确描述本轮改了什么>"
}}
```"""


# ---------------------------------------------------------------------------
# v5.0 结构化修改指令模板（替代 REFINEMENT_PROMPT）
# 每轮 diff → 分类 → 根因 → 精确修改指令 → LLM 受限执行
# ---------------------------------------------------------------------------

STRUCTURED_MODIFICATION_PROMPT = """你正在优化一个中文网文写作的 system prompt。你的任务是**精确执行以下修改指令**，不自行发挥，不修改指令未涉及的规则。

## 当前 Prompt
```
{current_system_prompt}
```

## 🔧 本轮必须执行的修改（按优先级从高到低排列）

{modification_instructions}

## 目标原文（供参考差异方向，不要抄袭内容）
---
{target_text}
---

## 上一轮生成文本（供了解当前问题所在）
---
{last_generation}
---

## 执行铁律

1. **严格按优先级执行**：P0 优先于 P1，P1 优先于 P2。先做完高优先级，再做低优先级。
2. **只解决指令列出的问题**：不要主动修改指令未涉及的规则。保留已有的有效规则。
3. **禁止删除正在生效的规则**：如果某条规则在上轮产生了较好效果（相似度较高），只能强化不能删除。
4. **400 字硬上限（不可违反）**：system prompt 总字数不得超过 400 字。若指令要求新增规则导致超限，优先精简/合并现有规则中效果最弱的部分。
5. **每条规则必须可验证**：写「禁止句式X」而非「文笔要有张力」；写「纯叙述不超过2段」而非「注意对白节奏」。
6. **禁止类规则必须附正确示例**：每条禁止句式后面跟一个正确写法的简短示例。
7. **以规则开头，不以角色扮演开头**：prompt 第一句直接写规则，不要「你是一名XX写手」。
8. **不写入具体角色名、地名、情节内容**：这些在用户消息的可变层中提供。
9. **如果一轮有多个修改**：先做加法（新增缺失规则）再做减法（精简冗余规则），避免误删。

## 输出格式

只输出合法 JSON 对象，不要任何其他文字：

```json
{{
  "system_prompt": "<修改后的完整 system prompt 纯文本>",
  "change_summary": "<一句话描述本轮改了什么，例如：强化了AI味禁令并新增了3条禁止句式>",
  "modifications_applied": [
    "<修改1的简短描述>",
    "<修改2的简短描述>"
  ],
  "rules_kept": "<列出本轮保留未动的有效规则>",
  "word_count": <system_prompt 的总字数>
}}
```"""


# ---------------------------------------------------------------------------
# diff 报告构建（从 diff_analyzer 输出组装 REFINEMENT_PROMPT 的参数）
# ---------------------------------------------------------------------------

def build_diff_report(
    generated: str,
    target: str,
    similarity: float,
    length_diff: float,
) -> dict:
    """从生成/目标文本构建完整的 diff 分析报告。

    封装了 diff_analyzer.analyze() 调用，返回可直接用于填充
    STRUCTURED_MODIFICATION_PROMPT 参数字典。

    Returns:
        {
            "similarity_feedback": str,
            "length_feedback": str,
            "modification_instructions": str,
            "error_summary": str,
            "error_count": int,
            "has_critical": bool,
        }
    """
    from .diff_analyzer import analyze, format_modifications_for_prompt
    from .scorer import normalize_text

    report = analyze(
        generated=generated,
        target=target,
        similarity=similarity,
        length_diff=length_diff,
        normalize_fn=normalize_text,
    )

    # 相似度反馈
    sim_fb = build_similarity_feedback(
        similarity, length_diff, report.gen_len, report.target_len,
    )

    # 长度反馈
    len_fb = build_length_feedback(length_diff, report.gen_len, report.target_len)

    # 修改指令
    mod_instructions = format_modifications_for_prompt(report.modifications)

    return {
        "similarity_feedback": sim_fb,
        "length_feedback": len_fb,
        "modification_instructions": mod_instructions,
        "error_summary": report.summary,
        "error_count": len(report.errors),
        "has_critical": report.has_critical_errors,
        "error_counts": report.error_counts,
        "modifications": report.modifications,
        "report": report,
    }


def build_structured_modification_prompt(
    current_system_prompt: str,
    target_text: str,
    last_generation: str,
    modification_instructions: str,
) -> str:
    """填充 STRUCTURED_MODIFICATION_PROMPT 模板。

    Args:
        current_system_prompt: 当前正在使用的 system prompt
        target_text: 目标原文
        last_generation: 上一轮生成的正文
        modification_instructions: format_modifications_for_prompt() 的输出

    Returns:
        完整的 LLM prompt 字符串
    """
    if len(last_generation) > 800:
        last_generation = last_generation[:800] + f"\n\n……（以下省略，共 {len(last_generation)} 字）"

    return STRUCTURED_MODIFICATION_PROMPT.format(
        current_system_prompt=current_system_prompt,
        modification_instructions=modification_instructions,
        target_text=target_text,
        last_generation=last_generation,
    )


# ---------------------------------------------------------------------------
# 相似度反馈构建（替代旧的多维度评分反馈）
# ---------------------------------------------------------------------------

def build_similarity_feedback(
    similarity: float,
    length_diff: float,
    gen_len: int,
    target_len: int,
) -> str:
    """构建 REFINEMENT_PROMPT 的相似度反馈块。

    Args:
        similarity: 字符级相似度 (0.0 ~ 1.0)
        length_diff: 长度差异比例（正=偏长，负=偏短）
        gen_len: 生成文本规范化后的字符数
        target_len: 目标文本规范化后的字符数
    """
    lines = [f"### 字符级相似度：{similarity:.2%}"]
    lines.append(f"- 目标文本：{target_len} 字符（规范化后）")
    lines.append(f"- 生成文本：{gen_len} 字符（规范化后）")

    if similarity >= 0.80:
        lines.append("- ✅ 相似度较高，继续保持当前 prompt 的核心规则，仅做小幅微调")
    elif similarity >= 0.60:
        lines.append("- ⚠️ 相似度中等，方向对但不够精确。检查生成文本与原文的差异在哪里")
    elif similarity >= 0.30:
        lines.append("- 🔴 相似度偏低，需要实质性改动 prompt 规则")
    else:
        lines.append("- 🔴🔴 相似度极低，prompt 完全不对路，需要从零重新设计")

    return "\n".join(lines)


def build_length_feedback(length_diff: float, gen_len: int, target_len: int) -> str:
    """构建长度差异反馈。"""
    lines = [f"### 长度差异"]
    diff_pct = abs(length_diff) * 100
    if length_diff > 0.10:
        lines.append(f"- 🔴 生成偏长 {diff_pct:.0f}%（{gen_len} vs {target_len} 字符）")
        lines.append(f"- **修改指令**：在 prompt 中减少冗余描写，提高信息密度，或降低 max_tokens")
    elif length_diff < -0.10:
        lines.append(f"- 🔴 生成偏短 {diff_pct:.0f}%（{gen_len} vs {target_len} 字符）")
        lines.append(f"- **修改指令**：在 prompt 中增加细节描写要求，或提高 max_tokens")
    elif abs(length_diff) > 0.05:
        lines.append(f"- ⚠️ 长度略有差异 {diff_pct:.0f}%，可微调")
    else:
        lines.append(f"- ✅ 长度匹配良好（差异 {diff_pct:.0f}%）")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 蒸馏 Prompt（适配新的 prompt 格式：system prompt 是纯文本）
# ---------------------------------------------------------------------------

DISTILL_PROMPT = """你是一名 Prompt 蒸馏师。下面是一组同题材同场景的高分 Prompt 经验，请总结它们的共性约束，生成一份通用、可复用的**系统写作 Prompt**。

经验数据：
{experiences_json}

请直接输出一个完整的 system prompt 文本（300–800 字），涵盖写作规则、风格约束、输出格式、禁忌事项等。这是**不变层**——不含具体角色名和情节。

同时输出一个 JSON 包裹：
```json
{{
  "genre": "题材",
  "scene_type": "场景类型",
  "system_prompt": "<完整 system prompt 文本>",
  "notes": "<额外说明>"
}}
```"""


# ---------------------------------------------------------------------------
# Prompt 生成器模板（从风格积累生成新 Prompt，无需 target_text）
# ---------------------------------------------------------------------------

PROMPT_GENERATOR_TEMPLATE = """你是一名 Prompt 工程师。基于已有的同风格高分 Prompt 经验，为新的设定生成一个系统级写作 Prompt。

**重要：这是为长篇连载小说设计的写作 prompt。** 每次生成的内容是连载中的一个片段（约 1000–2000 字），不是独立完整的短篇故事。

## 新设定（可变层）
{settings_text}

## 情节方向
{plot_direction}

## 参考 Prompt（同风格高分经验，按得分排序）
{reference_prompts}

## 要求
1. **继承风格约束**：从参考 Prompt 中提取共性的写作规则（句式、节奏、描写密度、对白风格、叙事视角、情绪控制等），融入新 prompt
2. **适配新设定**：确保 prompt 的规则适用于新设定中的题材和角色类型
3. **不变层原则**：不能硬编码参考 prompt 中的具体角色名、地名、功法名——这些属于可变层
4. **防 AI 味**：包含防止空洞修辞、过度解释、情感标签化的约束
5. **连载写作规则（关键）**：必须包含明确的规则禁止短篇故事式收尾——禁止总结全文、禁止升华主题、禁止给情节画句号；片段结尾应自然流动、留钩子或悬念，让读者期待下一段
6. 输出 300–1000 字的完整纯文本 system prompt

只输出合法 JSON：
```json
{{
  "system_prompt": "<完整 prompt 文本>",
  "notes": "<设计说明：参考了哪些风格约束，做了哪些适配>"
}}
```"""

PROMPT_GENERATOR_REFINE_TEMPLATE = """你正在优化一个基于风格积累生成的写作 Prompt。请根据测试反馈改进它。

**重要：这是为长篇连载小说设计的写作 prompt。** 如果生成文本试图总结、升华、点题或给故事画句号，这是严重的 prompt 缺陷。

## 新设定
{settings_text}

## 当前 Prompt
```
{current_prompt}
```

## 测试生成正文
---
{generated_text}
---

## 评分反馈
{feedback_json}

## 改进方向
- 如果质感不足：检查是否遗漏了参考 prompt 中的关键风格约束
- 如果泛化不佳：检查 prompt 是否过于贴合特定设定、缺乏通用性
- 如果有 AI 味：加强反空洞、反标签化的约束
- **如果生成文本出现「短篇故事式收尾」（总结、升华、点题、画句号）：必须在 prompt 中增加连载写作规则——禁止总结、禁止收束、要求片段结尾自然流动、留钩子。检查生成文本末尾 2–3 句是否有这种倾向。**

请输出改进后的 system prompt，只输出合法 JSON：
```json
{{
  "system_prompt": "<改进后的 prompt 文本>",
  "notes": "<改进说明>"
}}
```"""


# ---------------------------------------------------------------------------
# 验证生成模板（用户手动验证时使用）
# ---------------------------------------------------------------------------

# VERIFY_USER_TEMPLATE 从 data/fixed_prompts.json 动态读取
# 保留模块级变量作为运行时缓存（首次调用时加载）
_VERIFY_USER_TEMPLATE_CACHE: str | None = None


def _get_verify_template() -> str:
    """获取用户消息模板（动态加载，支持运行时编辑）。"""
    global _VERIFY_USER_TEMPLATE_CACHE
    _VERIFY_USER_TEMPLATE_CACHE = get_verify_user_template()
    return _VERIFY_USER_TEMPLATE_CACHE


# ---------------------------------------------------------------------------
# 辅助函数
# ---------------------------------------------------------------------------

def format_retrieved_examples(experiences: list[dict]) -> str:
    """将历史高分经验格式化为参考文本。

    适配新的 prompt 格式：optimal_prompt 现在是一个纯文本 system prompt（不再是字段字典）。
    """
    if not experiences:
        return ""
    parts = ["### 参考历史最优 Prompt（按语义相似度召回）"]
    for i, exp in enumerate(experiences, 1):
        prompt = exp.get("optimal_prompt", "")
        # 兼容旧格式（dict）和新格式（纯文本）
        if isinstance(prompt, dict):
            prompt_text = (
                f"identity: {prompt.get('identity', '')}\n"
                f"craft_rules: {prompt.get('craft_rules', '')}\n"
                f"output_instruction: {prompt.get('output_instruction', '')}"
            )
        else:
            prompt_text = str(prompt)
        parts.append(
            f"\n参考 {i}（score={exp.get('score')}, "
            f"genre={exp.get('genre', '')}, scene={exp.get('scene_type', '')}）：\n"
            f"```\n{prompt_text}\n```"
        )
    return "\n".join(parts)


def build_verify_user_prompt(
    settings: dict,
    plot_direction: str,
    *,
    scene_beats: list[str] | None = None,
    conflict_type: str = "",
    emotional_arc: str = "",
    key_dialogue_beats: list[str] | None = None,
    # v3.16: 场景设定 + 世界状态（减少编造）
    setting_info: dict | None = None,
    world_state: dict | None = None,
    # v3.17: 行文节奏（受保护，不参与迭代）
    prose_economy: dict | None = None,
) -> str:
    """组装验证用的 user prompt：可变层（设定 + 剧情结构）。

    将剧情结构融入叙述式 brief，让 LLM 写小说而非执行大纲清单。
    """
    settings_lines = []
    for key, value in (settings or {}).items():
        settings_lines.append(f"- **{key}**：{value}")
    settings_text = "\n".join(settings_lines) if settings_lines else "（未提供）"

    # 格式化场景节拍——不再用"先是→接着→然后"流水账
    if scene_beats:
        beats_text = "## 场景节拍（必须全部覆盖，按编号顺序展开）\n" + "\n".join(
            f"{i + 1}. {b}" for i, b in enumerate(scene_beats)
        )
        # 有节拍时注入硬约束（generate 模式无节拍则不注入，避免引用"编号节拍"造成困惑）
        fidelity_block = get_fidelity_block()
    else:
        beats_text = plot_direction.strip() or "（场景说明见后）"
        fidelity_block = ""

    # 对白指令：有对白节点时强调必须写对白
    if key_dialogue_beats:
        dialogue_instruction = (
            "这个场景有角色对白，必须有自然对话——体现角色性格和情绪，不要一问一答式说明。"
        )
    else:
        dialogue_instruction = "（纯叙述场景，如果有角色出现仍可加入简短对白）"

    # v3.16: 场景设定块（从原文提取，减少编造）
    setting_parts = []
    if setting_info:
        loc = setting_info.get("location", "") or ""
        tod = setting_info.get("time_of_day", "") or ""
        atm = setting_info.get("atmosphere", "") or ""
        objs = setting_info.get("key_objects", []) or []
        chars = setting_info.get("characters_present", []) or []
        if loc and loc != "（未提及）":
            setting_parts.append(f"地点：{loc}")
        if tod and tod != "（未提及）":
            setting_parts.append(f"时间：{tod}")
        if atm:
            setting_parts.append(f"氛围：{atm if isinstance(atm, str) else '、'.join(atm)}")
        if objs:
            setting_parts.append("场景物品：" + "；".join(objs))
        if chars:
            setting_parts.append("角色位置：" + "；".join(chars))
    ws_parts = []
    if world_state:
        facts = world_state.get("established_facts", []) or []
        mysteries = world_state.get("pending_mysteries", []) or []
        if facts:
            ws_parts.append("已确立事实：" + "；".join(facts))
        if mysteries:
            ws_parts.append("未解悬念（不可提前解开）：" + "；".join(mysteries))
    scene_setting_block = ""
    if setting_parts or ws_parts:
        lines = []
        if setting_parts:
            lines.append("## 场景设定（原文提取，禁止偏离）")
            lines.extend(setting_parts)
        if ws_parts:
            lines.append("## 世界状态")
            lines.extend(ws_parts)
        scene_setting_block = "\n".join(lines)

    # v3.17: 行文节奏正向参考（受保护，不参与迭代）
    prose_rhythm_block = ""
    if prose_economy:
        rhythm_lines = ["## 行文节奏（原文正向参考）"]
        rhythm_lines.append("以下是从原文观察到的行文节奏——不是规则，而是让你感受原文的呼吸方式：")

        nr = prose_economy.get("noun_reference", "")
        if nr:
            nr_map = {
                "pronoun_dominant": "原文倾向用代词/省略替代已提及的名词，不反复重复全名",
                "mixed": "原文名词和代词混用，间隔一定距离重复全名",
                "name_heavy": "原文倾向重复全名而非代词替代",
            }
            rhythm_lines.append(f"- 指代习惯：{nr_map.get(nr, nr)}")

        pe = prose_economy.get("paragraph_ending", "")
        if pe:
            pe_map = {
                "action_cutoff": "段落以动作戛然而止收尾，不做评述延申",
                "sensory": "段落以感官细节（声音、气味、光线）收尾",
                "evaluation": "段落以角色的评价或推测收尾",
                "transition": "段落以过渡性语句引出下段",
            }
            rhythm_lines.append(f"- 段落收尾：{pe_map.get(pe, pe)}")

        ts = prose_economy.get("transition_style", "")
        if ts:
            ts_map = {
                "jump_cut": "动作转场用跳切——只写结果，省略中间步骤",
                "minimal_bridge": "动作之间用1-2个衔接动作过渡",
                "full_chain": "动作链的每一步都展开描写",
            }
            rhythm_lines.append(f"- 转场方式：{ts_map.get(ts, ts)}")

        cd = prose_economy.get("character_intro_depth", "")
        if cd:
            cd_map = {
                "none": "新出场角色不做外貌描写",
                "single_trait": "新角色出场仅1个特征",
                "two_traits": "新角色出场约2个特征",
                "catalog": "新角色出场有较全面外貌/服饰描写",
            }
            rhythm_lines.append(f"- 人物出场：{cd_map.get(cd, cd)}")

        ni = prose_economy.get("narrator_intrusion", "")
        if ni:
            ni_map = {
                "never": "叙述者从不评价角色行为，纯呈现",
                "rare": "叙述者极少介入评价",
                "occasional": "叙述者偶尔插入一句判断",
                "frequent": "叙述者频繁对角色行为做定性判断",
            }
            rhythm_lines.append(f"- 叙述距离：{ni_map.get(ni, ni)}")

        prose_rhythm_block = "\n".join(rhythm_lines)

    template = _get_verify_template()
    return template.format(
        settings_text=settings_text,
        scene_beats=beats_text,
        scene_setting_block=scene_setting_block,
        prose_rhythm_block=prose_rhythm_block,
        conflict_type=conflict_type.strip() or "（未提供）",
        emotional_arc=emotional_arc.strip() or "（未提供）",
        dialogue_instruction=dialogue_instruction,
        fidelity_block=fidelity_block,
    )


# ---------------------------------------------------------------------------
# v4.0 变体角度池 + 极简生成 prompt 组装
# ---------------------------------------------------------------------------

VARIANT_ANGLES: list[str] = [
    "信息密度优先：段落更紧凑，每句话传递更多具体信息，减少过渡词和修饰冗余",
    "情感张力优先：强化内心冲突描写，突出角色情绪的起伏和矛盾，让情感层次更丰富",
    "对白引导优先：强调对话推动剧情，明确对白中的潜台词和博弈关系",
    "节奏控制优先：张弛有度的叙事节奏，动作段简洁、心理段细腻、转场利落",
    "动作描写优先：以动作传递信息和情绪，每句动作有明确意图，不逐帧拆解",
    "氛围营造优先：强化场景氛围和时代感，用环境细节烘托人物处境",
    "情节推进优先：事件因果链更清晰，每段都在推进剧情或揭示信息",
    "约束清晰优先：写作规则表述更明确自然，让 LLM 更容易理解和遵守",
]


def pick_random_angles(n: int = 3, *, pool: list[str] | None = None) -> list[str]:
    """从角度池中随机抽取 n 个不重复的角度。pool=None 时使用默认 VARIANT_ANGLES。"""
    import random
    source = pool if pool else VARIANT_ANGLES
    return random.sample(source, min(n, len(source)))


def select_angles_bandit(
    n: int = 3,
    *,
    pool: list[str] | None = None,
    stats: dict[str, dict[str, float]] | None = None,
    mode: str = "ucb",
    ucb_c: float = 1.0,
    epsilon: float = 0.3,
) -> tuple[list[str], str]:
    """Bandit 角度选择（UCB1 或 ε-greedy）。

    替代随机抽取，利用历史表现智能选择角度。

    Args:
        n: 选几个角度
        pool: 角度池，None 时用 VARIANT_ANGLES
        stats: 历史统计 {angle: {mean, std, win_rate, n}}
        mode: "ucb" | "epsilon_greedy"
        ucb_c: UCB 探索系数（越大越探索）
        epsilon: ε-greedy 探索概率

    Returns:
        (selected_angles, selection_mode)
        selection_mode: "ucb" | "epsilon_greedy" | "random_fallback"
    """
    import math
    import random

    source = pool if pool else VARIANT_ANGLES
    if not source:
        return [], "random_fallback"
    if len(source) <= n:
        return list(source), "random_fallback"

    # 无统计数据 → fallback 随机
    if not stats:
        return random.sample(source, n), "random_fallback"

    # 池中没有统计数据的角度（新角度）也算入，给一个乐观初始值
    total_rounds = 0
    scored_angles = {}
    for name in source:
        s = stats.get(name)
        if s:
            scored_angles[name] = s
            total_rounds += s.get("n", 0)

    # 统计覆盖不足（<30% 角度有数据）→ fallback 随机
    if len(scored_angles) < max(3, len(source) * 0.3):
        return random.sample(source, n), "random_fallback"

    # 给没数据的角度一个乐观先验（当前均值 + 10%），鼓励探索
    if scored_angles:
        avg_mean = sum(s["mean"] for s in scored_angles.values()) / len(scored_angles)
    else:
        avg_mean = 0.5

    def _ucb_score(name: str) -> float:
        s = stats.get(name)
        if not s:
            # 未探索 → 乐观初始分（高于均值）
            return avg_mean * 1.2
        mean = s.get("mean", 0.0)
        ni = s.get("n", 1)
        if ni == 0:
            return avg_mean * 1.2
        # UCB1: mean + c * sqrt(ln(N) / n_i)
        exploration = ucb_c * math.sqrt(math.log(max(total_rounds, 1)) / ni)
        return mean + exploration

    if mode == "epsilon_greedy":
        # ε 概率随机探索，1−ε 选 top-n
        if random.random() < epsilon:
            selected = random.sample(source, n)
            return selected, "epsilon_greedy_explore"
        # 按均值排序取 top-n
        ranked = sorted(source, key=lambda a: stats.get(a, {}).get("mean", avg_mean), reverse=True)
        return ranked[:n], "epsilon_greedy_exploit"

    # 默认 UCB1
    scored = [(name, _ucb_score(name)) for name in source]
    scored.sort(key=lambda x: x[1], reverse=True)
    selected = [name for name, _ in scored[:n]]
    return selected, "ucb"


def format_angle_descriptions(angles: list[str]) -> str:
    """将角度列表格式化为 VARIANT_GENERATOR_PROMPT 的角度描述块。"""
    lines = []
    for i, angle in enumerate(angles, 1):
        lines.append(f"**角度{i}**：{angle}")
    return "\n".join(lines)


def build_generation_prompt(scene_paragraph: str) -> str:
    """v4.0 极简生成：直接返回场景段落作为 user prompt。

    system prompt 由调用方独立传入（极简指令），不再从模板拼接。
    """
    return scene_paragraph


def format_style_references(experiences: list[dict]) -> str:
    """将同风格高分经验格式化为 Prompt 生成器的参考文本。"""
    if not experiences:
        return "（暂无同风格参考经验，请先通过「优化」tab 训练一些该风格的 prompt）"
    parts = []
    for i, exp in enumerate(experiences, 1):
        prompt = exp.get("optimal_prompt", "")
        if isinstance(prompt, dict):
            prompt_text = "\n".join(
                f"{k}: {v}" for k, v in prompt.items() if v
            )
        else:
            prompt_text = str(prompt)
        parts.append(
            f"### 参考 {i}（综合分={exp.get('score', 0)}, "
            f"题材={exp.get('genre', '')}, 场景={exp.get('scene_type', '')}）\n"
            f"```\n{prompt_text}\n```"
        )
    return "\n\n".join(parts)


def build_refinement_feedback(
    style_scores: dict,
    structure_scores: dict,
    improvements: dict | None = None,
    regressions: dict | None = None,
) -> tuple[str, str, str, str]:
    """构建 REFINEMENT_PROMPT 的维度级反馈。

    将各维度按得分分为三级：
    - 🔴 必须修复（< 0.6 或出现回归）
    - 🟡 需要提升（0.6–0.75）
    - 🟢 必须保留（≥ 0.75，绝对不能退化）

    Returns:
        (regression_warning, critical_fixes, improvement_targets, preserve_strengths)
    """
    improvements = improvements or {}
    regressions = regressions or {}

    # 维度 → 中文标签
    STYLE_LABELS: dict[str, str] = {
        "L5_dialogue": "对白",
        "L4_narrative": "叙事深度",
        "L3_sentence": "句式变化",
        "L2_emotion": "情绪呈现",
        "L1_style_match": "风格匹配",
    }
    STRUCT_LABELS: dict[str, str] = {
        "pacing": "节奏感",
        "scene_arc": "场景推进",
        "dialogue_quality": "对白质量",
        "tension": "张力悬念",
        "anti_ai_smell": "AI味防范",
    }

    # 收集所有维度及其得分、来源
    all_dims: list[tuple[str, float, str]] = []  # (label, score, source)
    for key, label in STYLE_LABELS.items():
        score = style_scores.get(key, 0)
        all_dims.append((f"{label}（风格/{key}）", score, "style"))
    for key, label in STRUCT_LABELS.items():
        score = structure_scores.get(key, 0)
        all_dims.append((f"{label}（结构/{key}）", score, "structure"))

    # 找出回归维度（从 improvements/regressions 中读取）
    regressed_keys: set[str] = set()
    improved_keys: set[str] = set()
    for dim_key, delta in regressions.items():
        label = STYLE_LABELS.get(dim_key, dim_key)
        regressed_keys.add(dim_key)
    for dim_key, delta in improvements.items():
        label = STYLE_LABELS.get(dim_key, dim_key)
        improved_keys.add(dim_key)

    # 分类
    critical: list[str] = []
    improve: list[str] = []
    preserve: list[str] = []

    for label, score, source in all_dims:
        line = f"- {label}：{score:.2f}"
        # 检查是否回归（仅风格维度有 tracking）
        dim_key = label.split("（")[1].rstrip("）") if "（" in label else ""
        style_key = dim_key.split("/")[-1] if "/" in dim_key else dim_key
        if style_key in regressed_keys:
            delta_val = regressions.get(style_key, 0)
            line += f" ⚠️ 较上轮下降 {abs(delta_val):.2f}"
        elif style_key in improved_keys:
            delta_val = improvements.get(style_key, 0)
            line += f" ↑ 较上轮提升 {delta_val:.2f}"

        if score < 0.6 or style_key in regressed_keys:
            critical.append(line)
        elif score < 0.75:
            improve.append(line)
        else:
            preserve.append(line)

    # 构建回归警告
    regression_warning = ""
    if regressions:
        reg_lines = []
        for dim_key, delta in regressions.items():
            label = STYLE_LABELS.get(dim_key, dim_key)
            reg_lines.append(f"- **{label}** 下降了 {abs(delta):.2f}（虽然总分提升，但这个维度退步了）")
        regression_warning = (
            "## ⚠️ 维度回归警告\n"
            "以下维度在上一轮改进中出现了**明显退步**，本轮必须优先恢复：\n"
            + "\n".join(reg_lines)
            + "\n\n**改进时请对照上一轮的生成文本，理解是哪些修改导致了退化，并在本轮恢复那些有效的约束。**\n"
        )

    # 构建三个级别
    critical_fixes = (
        "**🔴 必须修复的维度（得分 < 0.6 或出现明显回归）：**\n"
        + ("\n".join(critical) if critical else "（无）")
    )
    improvement_targets = (
        "**🟡 需要提升的维度（得分 0.6–0.75）：**\n"
        + ("\n".join(improve) if improve else "（无）")
    )
    preserve_strengths = (
        "**🟢 必须保留的优势（得分 ≥ 0.75，绝对不能退化）：**\n"
        + ("\n".join(preserve) if preserve else "（无）")
        + "\n\n改进时注意：这些高维度对应的 prompt 约束是有效的，不要删除或弱化它们。"
        "如需修改其他维度，确保不破坏这些已有的优势。"
    )

    return (regression_warning, critical_fixes, improvement_targets, preserve_strengths)


def build_plot_fidelity_feedback(
    plot_scores: dict | None,
    beat_verdicts: list | None = None,
    fabricated_notes: str = "",
) -> str:
    """构建 REFINEMENT_PROMPT 的情节保真度反馈块。

    指出哪些节拍缺失、编造了什么、是否超前解谜，并给出可执行的修改指令：
    在 system prompt（不变层/通用）中加入「严格遵循用户场景节拍」硬约束。
    """
    if not plot_scores:
        return ""

    composite = plot_scores.get("composite", 0) or 0
    beat_cov = plot_scores.get("beat_coverage", 0) or 0
    order = plot_scores.get("order_fidelity", 0) or 0
    no_fab = plot_scores.get("no_fabrication", 0) or 0
    outcome = plot_scores.get("outcome_fidelity", 0) or 0

    lines = ["### 情节保真度（对照原文节拍）"]
    lines.append(
        f"- 综合保真度：{composite:.2f}（覆盖{beat_cov:.2f} / 顺序{order:.2f} / 未编造{no_fab:.2f} / 结局保真{outcome:.2f}）"
    )

    # 缺失节拍
    missing: list[str] = []
    if isinstance(beat_verdicts, list):
        for v in beat_verdicts:
            if isinstance(v, dict) and not v.get("present", True):
                beat = v.get("beat", "") or v.get("note", "")
                if beat:
                    missing.append(str(beat))
    if missing:
        lines.append(f"- ❌ 缺失节拍：{'；'.join(missing[:3])}")

    # 编造
    fab = (fabricated_notes or "").strip()
    if fab and fab != "无":
        lines.append(f"- ❌ 编造情节：{fab[:120]}")

    if composite < 0.75:
        lines.append(
            "- **修改指令**：生成偏离了原文节拍。在 system prompt 中加入/强化「严格遵循用户提供的场景节拍」"
            "规则--覆盖每一个节拍、不编造节拍外的情节转折或情报、不超前解开节拍未交代的悬念、"
            "事件结果与节拍一致。注意：这是通用规则，不要写具体情节。"
        )
    else:
        lines.append("- ✅ 节拍遵循良好，保持当前规则。")

    return "\n".join(lines)


def build_hard_data_feedback(
    punct_diff: dict | None = None,
    person_first: dict | None = None,
    serial_note: str = "",
    dialogue_diff: dict | None = None,
) -> tuple[str, str, str, str, str]:
    """构建 REFINEMENT_PROMPT 的硬数据差异反馈（🔴 部分）。

    返回 (hdata_punctuation, hdata_person_first, hdata_sentence_length,
           hdata_serial_awareness, hdata_dialogue)
    每个都是 markdown 段落，直接用于 REFINEMENT_PROMPT 的 {hdata_*} 占位符。
    """
    punct = punct_diff or {}
    pf = person_first or {}

    # 标点节奏（仅参考，不强制匹配）
    target_ratio = punct.get("target_ratio", 0)
    gen_ratio = punct.get("gen_ratio", 0)
    diff_pct = punct.get("diff_pct", 0)
    target_avg = punct.get("target_avg_len", 0)
    gen_avg = punct.get("gen_avg_len", 0)

    lines_punct = []
    if target_ratio and gen_ratio:
        lines_punct.append(f"### 标点节奏参考")
        lines_punct.append(f"- 目标段落：逗号/句号 ≈ {target_ratio:.1f}:1，平均句长 {target_avg:.0f} 字")
        lines_punct.append(f"- 生成段落：逗号/句号 ≈ {gen_ratio:.1f}:1，平均句长 {gen_avg:.0f} 字")
        if diff_pct > 50:
            lines_punct.append(f"- 标点节奏有差异（{diff_pct:.0f}%）。如果文本读起来节奏僵硬可适当参考调整，避免写入机械的比例要求")
        elif diff_pct > 30:
            lines_punct.append(f"- 标点节奏略有差异（{diff_pct:.0f}%），可留意但不强求")
        else:
            lines_punct.append(f"- 标点节奏接近（差异 {diff_pct:.0f}%）")
    else:
        lines_punct.append("（标点数据不足，跳过）")

    # 首句人物主语（参考）
    lines_pf = []
    first_sentence = pf.get("first_sentence", "")
    if pf.get("starts_with_person"):
        lines_pf.append(f"### 首句主语")
        lines_pf.append(f"- ✅ 首句以人物主语开头：「{first_sentence[:50]}」")
        lines_pf.append(f"- 保持当前的人物切入规则")
    else:
        lines_pf.append(f"### 首句主语")
        lines_pf.append(f"- 当前首句：「{first_sentence[:60]}」— 以非人物元素开头")
        lines_pf.append(f"- 如非刻意营造氛围，建议在 prompt 中增加「段落以人物为主语」的引导")
        lines_pf.append(f"- 注意：极短碎片句（≤6字）或刻意营造氛围的段落不受此限")

    # 句长分布参考
    target_dist = punct.get("target_dist", {})
    gen_dist = punct.get("gen_dist", {})
    lines_sl = []
    if target_dist and gen_dist:
        t_short = target_dist.get("short_pct", 0)
        t_med = target_dist.get("medium_pct", 0)
        t_long = target_dist.get("long_pct", 0)
        g_short = gen_dist.get("short_pct", 0)
        g_med = gen_dist.get("medium_pct", 0)
        g_long = gen_dist.get("long_pct", 0)

        lines_sl.append(f"### 句长分布参考")
        lines_sl.append(f"- 目标：短句 {t_short:.0f}% / 中句 {t_med:.0f}% / 长句 {t_long:.0f}%")
        lines_sl.append(f"- 生成：短句 {g_short:.0f}% / 中句 {g_med:.0f}% / 长句 {g_long:.0f}%")
        lines_sl.append(f"- 关注整体节奏感而非精确比例。如果文本读起来句长单一，可适当调整方向性提示")
    else:
        lines_sl.append("（句长分布数据不足，跳过）")

    # 连载适配性
    lines_sa = []
    if serial_note:
        lines_sa.append(f"### 连载适配性")
        lines_sa.append(f"- {serial_note}")
    else:
        lines_sa.append("（连载适配性数据不足，跳过）")

    # 对白密度
    dd = dialogue_diff or {}
    lines_di = []
    ratio = dd.get("dialogue_ratio", 0)
    has_dialogue = dd.get("has_dialogue", False)
    if dd:
        lines_di.append(f"### 对白密度")
        lines_di.append(f"- 对白占比：{ratio:.1f}%（引号内文字/总字数）")
        if not has_dialogue or ratio < 5.0:
            lines_di.append(f"- **❌ 严重缺乏对白**（占比 {ratio:.1f}% < 5%，小说必须有对白）")
            lines_di.append(f"- **修改指令**：在 prompt 中写入「每 300-500 字至少有一段对话」规则，并检查是否因过多叙事描写约束抑制了对白生成")
        elif ratio < 10.0:
            lines_di.append(f"- ⚠️ 对白偏少（{ratio:.1f}% < 10%），建议在 prompt 中增加对话引导")
        else:
            lines_di.append(f"- ✅ 对白密度正常（小说典型范围 10-30%）")
    else:
        lines_di.append("（对白密度数据不足，跳过）")

    return (
        "\n".join(lines_punct) if len(lines_punct) > 1 else "",
        "\n".join(lines_pf) if len(lines_pf) > 1 else "",
        "\n".join(lines_sl) if len(lines_sl) > 1 else "",
        "\n".join(lines_sa) if len(lines_sa) > 1 else "",
        "\n".join(lines_di) if len(lines_di) > 1 else "",
    )


def format_settings_text(settings: dict) -> str:
    """将 settings dict 格式化为文本。"""
    if not settings:
        return "（未提供）"
    return "\n".join(f"- **{k}**：{v}" for k, v in settings.items() if v)


# ===========================================================================
# 情节库提取 Prompt 模板（v3.12 新增）
# ===========================================================================
# 以下 9 个模板构成 4 阶段提取 + 1 交叉验证流水线。
# 每个模板都要求：
#   - 原文锚定：每个字段必须引用原文具体句子作为依据
#   - 置信度自评：AI 输出 confidence 评估自身判断可靠性
#   - 结构化 JSON 输出：便于下游处理和校验

NOVEL_CONTEXT_ANALYSIS_PROMPT = """你是一名资深网文编辑，正在进行小说全局分析。你需要仔细阅读下方提供的参考小说全文（或代表性样本），提取这部小说的核心特征。

## 参考小说
- 小说名：{novel_name}
- 类型：{genre}

## 正文文本
---
{full_text}
---

## 分析要求

请逐项提取以下信息，**每一项都必须引用原文中的具体句子作为依据**。

### 1. 世界观设定（world_building）
- 故事发生的世界类型（如：传统玄幻、现代都市、架空历史等）
- 力量体系/世界规则（如果有修炼体系、魔法、科技等）
- 社会结构与主要势力（宗门、家族、组织等）
- **必须引用原文中明确描述这些设定的句子**

### 2. 主要角色（main_characters）
对于每个主要角色，提取：
- 角色名、身份、性格特征
- 故事中的角色定位（主角/重要配角/反派等）
- 角色的核心动机/目标
- **必须引用原文中展现这些特征的对话或叙述句子**

### 3. 总体情节走向（overall_arc）
- 故事的开端（起）
- 故事的发展过程（承）
- 故事的关键转折点（转）
- 故事目前的走向趋势（合/待续）
- **每个阶段引用 1-2 个原文句子作为佐证**

### 4. 叙事风格（narrative_style）
- 视角（第一人称/第三人称限知/全知）
- 时间线结构（顺叙/倒叙/插叙）
- 典型描写方式（如：以人物动作为主、以环境渲染为主、对白驱动等）
- 语言风格特征（如：古风、口语化、简洁、密集描写等）

### 5. 类型元素清单（genre_elements）
列出本文中出现的属于「{genre}」这个类型的典型元素：
- 每个元素注明在哪段原文中出现
- 例如玄幻的「奇遇」「炼药」「境界突破」等
- 例如都市的「商战」「职场冲突」「都市异能」等

## 输出格式

只输出合法的 JSON，不要解释。所有字段都必须有内容。"""


SCENE_BOUNDARY_DETECTION_PROMPT = """你是一名资深网文编辑，负责从小说正文中识别自然的场景边界。

## 小说全局上下文
{novel_context}

## 待分析文本
---
{full_text}
---

## 场景定义

一个「场景」是一个自包含的叙事单元，具有以下特征：
1. **时间连续性**：场景内的时间是连续的（没有时间跳跃）
2. **空间一致性**：场景发生在同一个地点（或连续空间内）
3. **冲突/目标统一**：场景内角色追求的目标是统一的
4. **有起承转合**：每个场景有开始（进入状态）、发展（冲突升级）、结束（获得结果或过渡）
5. **自然边界标记**：场景切换往往伴随：时间跳转、地点变换、视角切换、叙事焦点转移

## 分析步骤

1. 首先通读全文，识别所有明显的地点变换和时间跳转
2. 在每个候选边界处，检查是否满足「新场景」的特征（至少满足 3 条）
3. 过滤掉过短的碎片（< 300 字的场景合并到相邻场景）
4. 为每个场景标注类型提示

## 输出

输出一个场景边界列表，每个场景包含：

- **start_pos**: 场景在原文中的起始字符偏移
- **end_pos**: 场景在原文中的结束字符偏移
- **scene_type_hint**: 场景类型——以下之一：
  - "开端" = 新的情节线开始或引入新信息
  - "发展" = 冲突升级、关系演进
  - "转折" = 意外事件、反转、关键发现
  - "高潮" = 冲突爆发、对决、情感顶点
  - "收尾" = 冲突解决、过渡、情绪沉淀
  - "铺垫" = 为后续高潮做准备的过渡性场景
- **boundary_rationale**: 一句话说明为什么这里是场景边界（原文依据）
- **estimated_chars**: 估算该场景的字数

## 约束

- 场景总字数应在 500-3000 字之间（过短的合并，过长的拆分为多个场景）
- 每个场景必须包含至少 2 个「节拍」（beat）的内容
- 如果全文字数不足 10000 字，场景数控制在 3-8 个
- 边界位置必须精确到字符偏移（可通过搜索原文中的特定句子来定位）
- **必须输出所有识别到的场景，不能遗漏任何一段原文**

只输出合法的 JSON 数组。"""


SCENE_BEAT_EXTRACTION_PROMPT = """你是一名资深网文编辑，正在对小说中的一个场景进行精细的节拍分析。

## 小说全局上下文
{novel_context}

## 场景原文
---
{scene_text}
---

## 场景元信息
- 所在小说：{novel_name}
- 类型：{genre}
- 场景类型提示：{scene_type_hint}

## 分析要求

你需要将本场景分解为「节拍」（beats）。节拍是场景中最小叙事单元——每个节拍描述一个不可再分的事件或动作-反应单元。

### 节拍识别规则

1. **每个节拍至少包含一个动作或一次对话交流**
2. **每个节拍必须有叙事推进**——不能是纯静态描写
3. **节拍间的切换标志**：角色进出场、对话主题变化、动作方向改变、情绪转折
4. **场景通常包含 5-15 个节拍**（低于 5 个表示拆分不够细，需重新分析）
5. **节拍顺序必须与原文一致**

### 每个节拍需要提取

```
beat_num: 节拍序号
description: 用一句话描述该节拍发生了什么（20-50 字）
原文依据: 原文中体现该节拍的 1-2 句原句（精确引用）
characters_involved: 参与的角色列表
conflict_level: 冲突强度（"无" / "低" / "中" / "高" / "爆发"）
character_emotion: 关键角色的情绪状态
narrative_momentum: 叙事推进类型（"新信息引入" / "冲突升级" / "关系变化" / "环境变化" / "内心变化" / "动作推进"）
```

## 严苛规则

1. **每一条 beat 都必须引用原文中的具体句子作为证据**——不能编造节拍
2. **节拍必须覆盖场景的全部内容**——原文的每个叙事段落都应归入某个节拍
3. **如场景中存在对白**，节拍分解必须体现对话回合的推进
4. **如场景中存在打斗**，节拍分解必须体现打斗的交手回合和结果变化
5. **AI 自评**：输出后请给出 confidence（0-1），评估本分析的可靠性
6. **如果 confidence < 0.7**，列出具体哪些部分不确定及原因

只输出合法的 JSON。"""


CHARACTER_GOAL_EXTRACTION_PROMPT = """你是一名资深网文编辑，正在分析小说场景中每个角色的动机和行为逻辑。

## 小说全局上下文
{novel_context}

## 场景节拍
{scene_beats_formatted}

## 场景原文
---
{scene_text}
---

## 分析要求

对场景中出现的每一个角色（包括主角、配角、反派、龙套），提取：

### 1. 角色出场信息
- 角色名
- 在本场景中的角色定位（主动方/被动方/旁观者/催化剂）
- 出场时的状态描述（引用原文中的具体句子）

### 2. 角色目标与动机
- 进入本场景时的目标：角色希望在本场景中达成什么？（引用原文依据）
- 动机来源：这个目标来自于——"角色自身意愿" / "外部压力" / "他人要求" / "突发事件迫使"
- 目标的明确度："明确" / "模糊" / "潜意识" / "读者能猜到但角色未明说"

### 3. 角色行动与策略
- 为实现目标采取了哪些行动？（逐条列出，引用原文）
- 策略类型："直接" / "试探" / "伪装" / "等待" / "逃避" / "对抗"

### 4. 障碍与冲突
- 遇到了什么障碍？（引用原文）
- 障碍来源："对手角色" / "环境" / "自身局限" / "信息不对称"
- 角色如何应对障碍？

### 5. 结果与变化
- 目标是否达成？（全部达成 / 部分达成 / 未达成 / 目标改变）
- 角色在本场景结束时状态有何变化？（引用原文描述）
- 角色的目标在场景结束时是否发生了转变？

## 特别要求

- 对于未直接出场的角色（被提及但未现身），用 "mentioned" 标记，不提取具体行为
- **每个场角色都必须引用原文中的至少一句原文作为分析依据**
- 如果某个角色全场景没有说话也没有明显动作，注明「背景角色」

只输出合法的 JSON。若场景中没有角色互动（纯环境描写），则在 description 中说明并输出空列表。"""


CONFLICT_STRUCTURE_EXTRACTION_PROMPT = """你是一名资深网文编辑，正在分析小说场景中的冲突结构。

## 小说全局上下文
{novel_context}

## 场景节拍
{scene_beats_formatted}

## 角色目标
{character_goals_formatted}

## 场景原文
---
{scene_text}
---

## 分析要求

### 1. 冲突类型（conflict_type）
判断本场景的主要冲突类型（可多选）：
- "人物 vs 人物"：角色之间有直接对抗（对话争辩、打斗、阴谋等）
- "人物 vs 环境"：角色与环境/自然/社会规则对抗
- "人物 vs 自我"：角色内心挣扎、道德困境
- "人物 vs 命运/超自然"：与命运的对抗或超自然力量的对决
- "无冲突/纯过渡"：推进情节但无明显冲突
- "暗流涌动"：表面平静但角色间有潜藏矛盾

**每项选择都必须引用原文句子作为依据。**

### 2. 冲突升级模式（escalation_pattern）
描述冲突在本场景中的发展过程：
- **起始状态**：冲突开始时的态势（引用原文）
- **触发性事件**：点燃冲突的事件（引用原文）
- **升级过程**：冲突如何一步步加剧（引用 2-3 个原文节点）
- **转折点**：冲突态势发生转变的时刻（如果有）
- **结果/解决**：冲突的结果（引用原文）
- **残余张力**：场景结束后是否还有未解决的冲突

### 3. 冲突参数
- escalation_speed: 冲突升级速度（"缓慢" / "渐进" / "快速" / "爆发式"）
- information_asymmetry: 角色间的信息差（"对称" / "部分不对称" / "严重不对称"）
- stakes: 当前冲突的利害关系（引用原文说明为什么这些利害关系是真实的）
- power_dynamics: 角色间的力量对比（"均势" / "甲方占优" / "乙方占优" / "动态变化"）

### 4. 叙事功能（narrative_function）
这个冲突在整部小说中承担什么叙事功能：
- "推动主线" / "塑造角色" / "埋设伏笔" / "制造悬念" / "调节节奏" / "世界展示"

只输出合法的 JSON。"""


EMOTIONAL_ARC_EXTRACTION_PROMPT = """你是一名资深网文编辑，正在分析小说场景中的情绪弧线。

## 小说全局上下文
{novel_context}

## 场景节拍
{scene_beats_formatted}

## 冲突结构
{conflict_structure_formatted}

## 场景原文
---
{scene_text}
---

## 分析要求

### 1. 场景整体情绪基调（overall_tone）
- 本场景最核心的情绪基调："紧张" / "悲怆" / "热血" / "压抑" / "温馨" / "悬疑" / "轻松" / "激昂" / "恐惧" / "平静" / "悲喜交加"
- 为什么是这个基调（引用原文 2-3 句）

### 2. 情绪逐节拍变化（emotional_progression）
按照节拍顺序，每个节拍的情绪状态：
- beat_num
- 主要情绪（如：平静、好奇、焦虑、紧张、恐惧、愤怒、悲伤、喜悦、激动、失落、希望等）
- 情绪强度（1-10）
- 触发事件（原文中直接引起情绪变化的句子）
- 情绪表达方式（"内心独白" / "对话语气" / "动作描写" / "环境烘托" / "细节暗示"）

### 3. 情绪弧线类型（arc_type）
- "上升型"：从低强度情绪逐渐升至高强度（如平静→紧张→恐惧→爆发）
- "下降型"：从高强度降至低强度（如愤怒→悲伤→接受→平静）
- "波浪型"：情绪上下波动
- "平坦型"：情绪没有显著变化
- "反转型"：情绪基调发生根本转变（如喜→悲）

### 4. 情绪转换点（key_emotional_turns）
- 情绪发生显著变化的位置（引用原文句子）
- 变化原因（事件驱动 / 对话驱动 / 内心活动驱动 / 环境驱动）
- 变化速度（"瞬间" / "渐进" / "延迟反应"）

### 5. 氛围营造手法（atmosphere_techniques）
作者使用了哪些手法来渲染情绪：
- "环境描写"（引用原文）
- "细节刻画"（引用原文）
- "节奏控制"（句长/段长变化）
- "留白/省略"
- "对比/反差"
- "象征/隐喻"

只输出合法的 JSON。"""


NARRATIVE_TECHNIQUE_EXTRACTION_PROMPT = """你是一名资深网文编辑，正在分析小说场景中使用的叙事技法。

## 场景节拍
{scene_beats_formatted}

## 场景原文
---
{scene_text}
---

## 分析要求

识别本场景中使用的叙事技法，**每项技法必须引用原文的具体句子作为证据**。

### 技法宝库（完整列表，请从以下选择）

**视角与结构：**
- "顺叙"：按时间顺序叙述
- "倒叙"：从时间的较晚点开始，回溯之前的事件
- "插叙"：在主线叙述中插入过去的片段
- "多线并行"：同时叙述两条或多条情节线
- "视角切换"：在不同角色的视角之间转换
- "第一人称限知叙述"
- "第三人称限知叙述"
- "全知视角"

**节奏与信息：**
- "悬念留白"：刻意不交代某些信息，留待后文揭示
- "信息释放"：逐步释放信息而非一次性交代
- "节奏变化"：通过句长/段长变化控制阅读节奏
- "高潮加速"：在冲突高潮使用短句短段加速节奏
- "铺垫"：为后续重要事件做前期准备

**描写与表达：**
- "环境渲染"：通过环境描写烘托情绪或氛围
- "对白驱动"：以对白为主要推进手段
- "动作描写"：以动作为主推进情节
- "内心独白"：展现角色的内心活动
- "细节暗示"：通过微妙的细节传达重要信息
- "对比/反差"：通过对比增强效果（如安静/喧嚣对比）
- "象征/隐喻"：用具体事物隐喻抽象概念

**结构与技法：**
- "首尾呼应"
- "伏笔回收"
- "戏谑/反差萌"
- "旁白/解构"
- "多角度叙述"

### 每项技法需要提取

```
technique: 技法名称（从上面选择）
evidence: 原文中体现该技法的具体句子（至少 1 句）
narrative_purpose: 使用该技法的叙事目的
effectiveness: 效果评估（"很好" / "适中" / "生硬" / "过度使用"），并说明理由
usage_density: 该技法在本场景中的使用密度（"少量" / "中等" / "大量"）
```

只输出合法的 JSON。如果没有使用任何特殊技法（纯基本叙述），则输出 {"techniques": [], "note": "基本叙述为主，未识别到特殊技法"}。"""


CROSS_SCENE_PATTERN_DISCOVERY_PROMPT = """你是一名资深网文编辑，现在需要对比同一类型下的多个已提取场景，发现重复出现的情节模式。

## 类型
{genre}

## 已提取场景列表

以下是从同类型多本小说中提取的场景结构化数据。请对比分析它们的共同模式。

{scenes_json}

---

## 分析任务

### Step 1: 逐场景阅读
先仔细阅读每个场景的摘要、节拍和冲突结构，理解它们在讲什么。

### Step 2: 识别重复模式
找出多个场景之间共有的叙事模式。模式可以基于：
- **节拍序列相似**：多个场景的 beat 顺序和类型相似（如「发现异常→探索→遭遇危险→获得线索」）
- **冲突类型相似**：冲突结构、升级模式和解决方式类相同
- **角色动机模式相似**：角色目标类型、障碍类型和结果类似
- **情绪弧线相似**：情绪变化模式相同
- **叙事手法相似**：使用了相同的叙事技法组合

### Step 3: 为每个模式命名和定义
对于每个识别到的模式：

```
group_name: 模式名称（如「奇遇觉醒」「冤家路窄」「秘境探宝」「师徒传承」「都市异能初现」等）
         注意：名称必须有辨识度，能让人一眼看出这是什么情节
description: 这个模式的完整定义描述（100-200 字）
genre: 所属类型
common_beats: [模式共有的节拍序列模板]
  每个节拍模板包含：
  - beat_order: 在第几步
  - beat_template: 节拍模板描述（如"主角在平常状态下发现异常现象"）
  - typical_variations: 可能的变体（如"异常现象可以是光/声/气味/预感"）
common_conflicts: 该模式的典型冲突结构
common_techniques: 该模式常用的叙事技法
representative_scenes: 属于这个模式的场景 ID 列表（从上面提取的）
```

### Step 4: 场景归属判断
对于发现的每个模式，判断上面的哪些场景属于它：
- 场景属于该模式的**严格匹配**：匹配度 ≥ 80%
- 场景属于该模式的**部分匹配**：匹配度 50-80%，注明哪些部分不匹配
- 不属于任何模式的场景：标记为"unique"并注明原因

### Step 5: 置信度评估
- 整体模式发现的 confidence（0-1）
- 每个模式的内聚度评估（0-1）：该模式下的场景彼此有多相似
- 如果不确定某个分组是否合理，注明原因

## 约束

1. **宁缺毋滥**：如果没有发现重复模式，输出空列表并注明"未发现重复模式"
2. **模式必须有至少 2 个场景支持**——单个场景不构成模式
3. **可跨小说匹配**：不同小说的类似场景也可以归为同一模式
4. **输出时 group_name 必须与实际场景内容一致**——如果场景是战斗为主，不要命名为"情感纠葛"
5. **不需要覆盖所有场景**——那些独特的、不重复的场景不应该被硬塞进某个组

只输出合法的 JSON。"""


SCENE_EXTRACTION_VALIDATOR_PROMPT = """你是一名严格的网文编辑审查官。你的任务是**交叉验证**前一阶段对某个场景的提取结果是否准确。

## 交叉验证规则

你不能直接信任之前的提取结果。你需要：
1. **重新阅读原文**（下面提供的场景原文）
2. **逐条核对**之前提取的每个字段是否与原文一致
3. **发现编造的内容**必须标记
4. **发现遗漏的内容**必须补充
5. **发现错误解读**必须纠正

## 场景原文
---
{scene_text}
---

## 之前提取的结果

### 场景节拍
{previous_beats}

### 角色目标
{previous_goals}

### 冲突结构
{previous_conflict}

### 情绪弧线
{previous_emotional}

### 叙事技法
{previous_techniques}

---

## 验证任务

### 1. 节拍检查（beat_check）
对每个节拍：
- **present**：是否在原文中真实存在？如果 AI 编造了一个不存在的节拍，标记为 false
- **accurate**：节拍描述是否与原文一致？如果描述偏离了原文内容，标记为 false
- **missing**：是否有原文中存在的节拍被遗漏了？列出遗漏的节拍
- **note**：如果发现问题，在 note 中说明原文实际是什么

### 2. 角色目标检查（goal_check）
- 每个角色的目标描述是否与原文一致？
- 是否有角色的目标被错误解读？
- 是否有出场的角色被遗漏了？

### 3. 冲突结构检查（conflict_check）
- 冲突类型判断是否正确？
- 冲突升级过程是否与实际一致？
- 关键冲突节点是否遗漏？

### 4. 情绪弧线检查（emotion_check）
- 逐节拍的情绪判断是否准确？
- 整体情绪基调是否正确？

### 5. 叙事技法检查（technique_check）
- 列出的技法是否在原文中真实存在？
- 是否有重要的技法被遗漏？

### 6. 总体评估

```
validation_confidence: 对前序提取的整体信任度（0-1）
issues_found: 发现的问题总数
critical_issues: 严重问题数（编造内容、严重偏离原文）
verdict: "通过" / "部分通过" / "不通过"
correction_needed: 是否需要重新提取？(true/false)
```

## 铁律

1. **必须逐条核对**，不能笼统评估
2. **编造内容（原文中不存在的节拍/事件）必须标记为 critical**
3. **遗漏重要节拍必须标记**
4. **如果你发现之前的提取结果质量很差（多处编造/错误），verdict = "不通过"**
5. **你的验证结果需要有原文引用作为依据**

只输出合法的 JSON。"""


# ---------------------------------------------------------------------------
# 人物档案提取 Prompt
# ---------------------------------------------------------------------------

CHARACTER_EXTRACTION_PROMPT = """你是一名资深网文编辑，正在从小说文本中提取人物档案。

## 小说信息
- 小说名：{novel_name}
- 类型：{genre}

## 正文片段（采样）
---
{novel_text}
---

## 任务

阅读以上小说片段，识别所有**有名有姓的角色**（不包括「店小二」「路人甲」等无名的完全龙套），
为每个角色生成一份人物档案。

### 对每个角色，提取以下信息并用自由格式文本写一份档案：

1. **名字和别名**：最常用的名字、其他称呼/别名
2. **角色定位**：主角/配角/反派/龙套/背景人物（一句话判断依据）
3. **身份**：社会身份（如"青山镇医馆学徒"）
4. **性格特征**：引用原文中的行为或对话作为依据
5. **外貌描写**：如有（引用原文）
6. **说话风格（关键）**：用词习惯、语气、话多话少、是否话里有话——引用原文对话片段
7. **背景故事**：如有（引用原文）
8. **能力/技能**：如有（引用原文）
9. **与其他角色的关系**：每对关系说明互动模式
10. **角色发展**：从出场到当前的变化方向
11. **备注**：对主线的作用

### 输出格式

输出 JSON 对象，格式如下：
```json
{{
  "characters": [
    {{
      "name": "角色名",
      "aliases": ["别名1", "别名2"],
      "role": "主角/配角/反派",
      "profile": "自由格式文本，融入以上所有字段的信息。\n\n可以包含 Markdown 格式。"
    }}
  ]
}}
```

**核心要求**：
- profile 字段是**自由格式文本**，将第4-11项的所有信息写在一起，用 Markdown 组织
- 每个角色必须引用原文中的具体句子或对话作为依据
- 确保不遗漏任何有名有姓的角色
- 如果某些信息原文中没有提及，就写"（原文未提及）"

只输出合法 JSON。"""


# ---------------------------------------------------------------------------
# Prompt 分析台（v4.1）：从高分 prompt 中反向挖掘写法规律
# ---------------------------------------------------------------------------

PROMPT_ANALYZER_TEMPLATE = """你是一位 Prompt 工程专家。下面是一组训练过的写作 prompt 及其得分。请分析这些 prompt，提取写 prompt 的方法论。

## 分析样本

{analyze_targets}

## 分析任务

请逐一完成以下分析，输出一个 JSON 对象：

### 1. 同类共性规则（common_rules）
从以上 prompt 中提取反复出现的高频约束。只统计出现在 **得分 ≥ {high_score_threshold}** 的 prompt 中的规则。
- 每条规则给出：规则内容、出现在几个 prompt 中（及百分比）、一个典型原文片段
- 按出现频率降序排列，只输出出现率 ≥ 30% 的规则
- 用一句话概括每条规则的核心诉求（如「禁止 AI 味句式」「强制对白穿插」）

### 2. 结构模式（structure_pattern）
这些高分 prompt 在结构上的共同特征：
- 规则的组织顺序（先写什么后写什么）
- 是否使用了分段标签（如【xxx】）
- 规则的平均粒度（一句话一条 vs 一段一条）
- 正面引导 vs 反面禁止的比例
用一段话描述，200 字以内。

### 3. 有害规则（harmful_rules）
如果提供了低分 prompt 作为对比：找出那些 **在低分 prompt 中频繁出现、但在高分 prompt 中几乎不出现** 的规则。这些是「写 prompt 时应避免的陷阱」。
每条规则给出：规则内容、低分组出现率、高分组出现率、为什么它可能导致低分。

### 4. 分类特有规则（category_specific_rules）
如果有多个题材/情节类型的 prompt：找出**只在本分类中出现**的规则（其他分类没有的）。这些是「本类题材的特化规则」。
如果没有跨分类对比数据，此字段返回空数组。

### 5. 通用规则（universal_rules）
跨所有分类的高分 prompt 中都出现的规则——无论什么题材都应该写进 prompt 的「万能规则」。
如果没有跨分类对比数据，就基于本批样本判断哪些规则看起来是普适的。

### 6. 推荐模板（recommended_template）
综合以上分析，输出一个推荐的 prompt 模板结构。
- 用自然语言描述结构，200-400 字
- 标注哪些部分是「通用」（任何题材都写）、哪些是「可替换」（按题材/场景调整）
- 模板本身不是具体规则清单，而是「这个位置的规则应该解决什么问题」

## 输出格式

只输出一个合法 JSON 对象，不要任何其他内容：

```json
{{
  "common_rules": [
    {{ "rule": "规则内容", "frequency_pct": 80, "appears_in": 8, "total_prompts": 10, "example": "原文片段", "essence": "一句话概括" }}
  ],
  "structure_pattern": "结构描述文本",
  "harmful_rules": [
    {{ "rule": "规则内容", "low_score_pct": 60, "high_score_pct": 10, "why_harmful": "为什么有害" }}
  ],
  "category_specific_rules": [
    {{ "rule": "规则内容", "category": "玄幻-战斗", "note": "为什么本类特有" }}
  ],
  "universal_rules": [
    {{ "rule": "规则内容", "confidence": "high", "reason": "为什么是通用的" }}
  ],
  "recommended_template": "模板描述文本"
}}
```"""


