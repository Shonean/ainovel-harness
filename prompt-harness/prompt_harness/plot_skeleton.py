"""情节骨架去风格化。

用 LLM 把目标文本去风格化，提取纯情节骨架。
确保 P 优化的是**文风**，不是"模型对原文情节的记忆"。

骨架作为正向生成的统一输入（替代 base_paragraph）。
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

# 【v5.22 P2c】skeleton persistent cache: same target + same prompt version -> reuse.
# Skeleton is deterministic (same prompt + same model); cache skips the whole
# "LLM extract + L1 anchor repair + semantic gate" chain (up to ~6 LLM calls/chapter),
# cutting reverse-infer init from ~11 min to seconds. Bump _SKELETON_PROMPT_VERSION
# whenever the skeleton prompt/logic changes to invalidate old cache entries.
_SKELETON_PROMPT_VERSION = "v5212"
_SKELETON_CACHE_FILE = Path(__file__).resolve().parent.parent / "skeleton_cache.json"
_SKELETON_CACHE: dict[str, dict] | None = None


def _load_skel_cache() -> dict[str, dict]:
    global _SKELETON_CACHE
    if _SKELETON_CACHE is None:
        try:
            _SKELETON_CACHE = json.loads(
                _SKELETON_CACHE_FILE.read_text(encoding="utf-8")
            )
        except Exception:
            _SKELETON_CACHE = {}
    return _SKELETON_CACHE


def _save_skel_cache() -> None:
    try:
        _SKELETON_CACHE_FILE.write_text(
            json.dumps(_SKELETON_CACHE, ensure_ascii=False, default=str),
            encoding="utf-8",
        )
    except Exception:
        pass


def _skeleton_cache_key(text: str, opt: bool) -> str:
    return (
        f"{_SKELETON_PROMPT_VERSION}|{int(opt)}|"
        f"{hashlib.md5(text.encode('utf-8')).hexdigest()}"
    )


SKELETON_SYSTEM_PROMPT = """你是专业的情节骨架提取器。你的任务是把给定的小说章节去风格化，只保留纯情节骨架。

严格要求：
1. 去掉所有文风修饰（形容词、比喻、氛围渲染、心理描写的文风化表达）
2. 用客观陈述句列出发生了什么，不代入叙述视角
3. 按时间顺序组织，保留事件的因果关系
4. 保留关键人物、动作、对话核心内容、情节转折
5. 输出格式为结构化 bullet list
6. 字数控制在原文的 20%-30% 之间，宁少勿多
7. 不输出任何解释性文字，只输出骨架本身
8. 标志性细节必须原样保留，不得改写或省略：
   - 专有名词：人名、地名、物品名（如"橡皮擦""宇智波鼬的图案""爬墙虎""诊断书"）照抄
   - 关键数字：金额、年龄、时间、楼层等（如"五万""两千万""十二岁""六楼"）原样写出
   - 经典对白：推动情节或塑造人物的对话原句，用引号照抄（如"归零""我没病""我真的来了"）

骨架应包含：
- 出场人物（及当时的关系状态）
- 核心事件节拍（按发生顺序）
- 场景设定（时间、地点）
- 关键冲突或转折
- 情绪走向的客观变化（仅描述事实，不渲染）
- 标志性细节（专有名词 / 关键数字 / 经典对白原句，逐条列出，确保不丢失）
"""

# 【v5.18】生成导向骨架（g17 锁定）：节拍=具体场景动作+结果，替代抽象概括。
# 与 SKELETON_SYSTEM_PROMPT 的区别在第 2/4/5 条：禁用抽象概括词、
# 对白写清内容要点、节拍粒度=场景动作（harness g17 实测 pf +0.039）。
# 【v5.21.1】禁类型词一笔带过 + 荒诞/幽默/反差内容不是文风 + 首轮对白=基调必须保留 +
# 荒诞瞬间独立成拍 + 对照示例（修复开场被合并成「做精神评估」的 bug；A/B 实测 2/2 保留幽默开场）。
# 【v5.21.2】标志性基调标注【基调：XX】防场景误读（杜绝黑色幽默被读成看病；
# 定向测试：内容保真+基调 → 荒诞节奏全开，仅内容保真 → 幽默内容在但节奏平淡，仅基调 → 仍误读）。
SKELETON_SYSTEM_PROMPT_OPT = """你是专业的情节骨架提取器。你的任务是把给定的小说章节提取为「生成导向」的情节骨架——另一个模型将只凭这份骨架重写正文，因此每个节拍必须具体到能直接展开成原场景。

严格要求：
1. 去掉所有文风修饰（形容词、比喻、氛围渲染、心理描写的文风化表达）——但荒诞、幽默、反差的**内容与对话本身不是文风**，必须保留其具体内容
2. 每个节拍 = 一个完整的场景动作：谁 + 做了什么具体动作 + 结果/反应。写清动作链，**不得用事件类型词（如「做评估」「问诊」「交谈」「对峙」「商议」）一笔带过**——必须写出该场景特有的具体内容：谁说了什么、反应如何。「两人处理遗像」要写成「陈硕把客厅里陈迹父母的黑白遗像合照扔进垃圾桶」
3. 按时间顺序组织，保留事件的因果关系
4. 关键对白：推动情节或塑造人物的对话原句用引号照抄；其余对话写清内容要点（谁说了什么观点）。**场景开头的首个对白轮构成该场景的基调，必须保留其具体内容，不得用类型词概括**
5. 节拍粒度 = 一个完整场景动作（进屋/动手/说某句话/发生某事），宁可细分不要合并成大段。**作者刻意营造的开场、荒诞/幽默/反差瞬间必须作为独立节拍，保留其内容与顺序，不得并入抽象动作概括**
6. 标志性细节必须原样保留：专有名词（人名/地名/物品名）、关键数字（金额/年龄/时间/楼层）原样写出
7. **标志性基调标注（防场景误读）**：每个场景开头（及基调切换处）在节拍前标注【基调：XX】，客观描述该场景的文学效果及其构成。若场景性质易被误读（如医生问问题被误读成普通看病），必须写出构成反差的具体内容 + 明示基调，不得用类型词一笔带过。示例：`【基调：黑色幽默（医生一本正经宣布五级评分 vs 陈迹对生死的淡定，反差制造荒诞）】医生老刘一本正经向陈迹宣布评估规则（按「无/很轻/中等/严重/非常严重」五级评分，可以吗）；陈迹同意后第一个问题就问他想不想结束生命——陈迹反问「结束谁的生命」，老刘答「你自己的」，陈迹答「那没有」`
8. 字数控制在原文的 25%-35%
9. 不输出任何解释性文字，只输出骨架本身

对照示例：
❌ 医生老刘对陈迹做精神评估（合并 + 抽象，错误）
✅ 医生老刘一本正经向陈迹宣布评估规则（按「无/很轻/中等/严重/非常严重」五级评分，可以吗）；陈迹同意后，第一个问题就问他想不想结束生命——陈迹反问「结束谁的生命」，老刘答「你自己的」，陈迹答「那没有」（保留开场内容与对话次序，正确）

骨架应包含：
- 出场人物（及当时的关系状态）
- 场景设定（时间、地点）
- 场景基调（【基调：XX】标注每个场景开头的文学效果，防误读）
- 核心事件节拍（按发生顺序，每拍=具体场景动作+结果）
- 关键冲突或转折
"""


async def extract_plot_skeleton(
    text: str,
    *,
    level: str = "chapter",
    opt: bool = False,
) -> dict[str, Any]:
    """提取情节骨架。

    Args:
        text: 目标小说文本
        level: "chapter"（章节级）或 "paragraph"（段落级）

    Returns:
        {
            skeleton_text: str,      # 可直接作为正向生成的 user prompt
            beats: list[str],        # 事件节拍列表
            characters: list[str],   # 出场人物
            scene_setting: str,      # 场景设定
            extraction_model: str,   # 用的模型
            level: str,              # chapter / paragraph
            char_count: int,         # 骨架字数
            original_char_count: int, # 原文字数
            compression_ratio: float, # 压缩比
        }
    """
    from .llm_client import chat_completion, DISABLE_THINKING
    from .config import SETTINGS

    if not text or len(text.strip()) < 50:
        return {
            "skeleton_text": text,
            "beats": [],
            "characters": [],
            "scene_setting": "",
            "extraction_model": "",
            "level": level,
            "char_count": len(text),
            "original_char_count": len(text),
            "compression_ratio": 1.0,
        }

    # 【v5.22 P2c】cache hit -> reuse final skeleton (after L1 repair).
    _ck = _skeleton_cache_key(text, opt)
    _cached = _load_skel_cache().get(_ck)
    if _cached:
        return _cached

    # 根据文本长度调整输出期望
    target_ratio = 0.25 if level == "chapter" else 0.30
    target_chars = max(200, int(len(text) * target_ratio))

    # 【v5.18】opt=True → 生成导向骨架 prompt（g17 锁定）；user 措辞同步改生成导向。
    if opt:
        user_prompt = f"""请提取以下章节的「生成导向」情节骨架（约 {target_chars} 字）。

原文（共 {len(text)} 字）：
---
{text}
---
直接输出骨架内容，不要其他说明。"""
        call_type = "skeleton_extract_opt"
        sys_prompt = SKELETON_SYSTEM_PROMPT_OPT
    else:
        user_prompt = f"""请将以下小说文本去风格化，提取纯情节骨架。

原文（共 {len(text)} 字）：
---
{text}
---

请输出情节骨架，约 {target_chars} 字。直接输出骨架内容，不要其他说明。"""
        call_type = "skeleton_extract"
        sys_prompt = SKELETON_SYSTEM_PROMPT

    result = await chat_completion(
        system=sys_prompt,
        user=user_prompt,
        model=SETTINGS.ark_model_pro,
        temperature=0.3,  # 低温度，确保一致性
        max_tokens=min(4096, target_chars * 3),  # 留出余量
        extra_body=DISABLE_THINKING,  # 骨架提取不需要推理，关闭 thinking 避免烧 token 拖慢训练
        call_type=call_type,  # 便于 LLM 日志归因
    )

    if result.get("error"):
        raise RuntimeError(f"Skeleton extraction failed: {result['error']}")

    skeleton = (result.get("content") or "").strip()

    # 【v5.13】L1 骨架层：字面锚点硬补丁 + 语义覆盖闸门（小循环，cap 2）。
    #  round0: L1a 修复（缺≥3 触发 LLM 重提，否则确定性 append）→ L1b 语义闸门
    #  round1: 语义重提后再跑一遍 L1a（幂等，防重提丢锚点）→ 再判闸门，cap 收尾。
    from .anchor_control import (
        repair_skeleton_anchors, skeleton_semantic_gate, _llm_re_extract,
        SKELETON_SEM_COV_MIN,
    )
    anchor_repair = None
    semantic_gate = None
    cur_skeleton = skeleton
    for _round in range(2):
        repair = await repair_skeleton_anchors(
            cur_skeleton, text, llm_repair=(_round == 0),
        )
        cur_skeleton = repair["skeleton_text"]
        anchor_repair = repair
        gate = await skeleton_semantic_gate(cur_skeleton, text)
        semantic_gate = gate
        if gate.get("unknown") or gate.get("sem_cov", 0.0) >= SKELETON_SEM_COV_MIN:
            break
        # 覆盖不足 → 定向 LLM 重提（带低覆盖分段提示），下一轮 repair 收尾
        re_text = await _llm_re_extract(
            text, repair.get("missing") or [],
            low_segments=gate.get("low_segments"),
        )
        if not re_text:
            break
        cur_skeleton = re_text
    skeleton = cur_skeleton

    # 解析 beats 和 characters（简单启发式；锚点补丁区块已跳过）
    beats, characters, scene_setting = _parse_skeleton(skeleton)

    _skel_result = {
        "skeleton_text": skeleton,
        "beats": beats,
        "characters": characters,
        "scene_setting": scene_setting,
        "extraction_model": result.get("model") or SETTINGS.ark_model_pro,
        "level": level,
        "char_count": len(skeleton),
        "original_char_count": len(text),
        "compression_ratio": round(len(skeleton) / max(1, len(text)), 3),
        # 【v5.13】L1 元数据（供落盘/前端/复盘）
        "anchor_repair": anchor_repair,
        "semantic_gate": semantic_gate,
    }
    # 【v5.22 P2c】persist to cache after L1 repair + gate completed.
    try:
        _load_skel_cache()[_ck] = _skel_result
        _save_skel_cache()
    except Exception:
        pass
    return _skel_result


def _parse_skeleton(skeleton: str) -> tuple[list[str], list[str], str]:
    """从骨架文本中启发式解析节拍、人物、场景。

    不追求完美——主要是给前端展示用。
    真实的骨架质量由 LLM 保证。
    """
    lines = [l.strip() for l in skeleton.split("\n") if l.strip()]
    beats: list[str] = []
    characters: list[str] = []
    scene_setting: str = ""

    current_section = "beats"

    # 【v5.13】L1a 追加的【锚点校验补丁】区块直接跳过，不进 beats/characters。
    # 假设「锚点补丁区块总在骨架末尾」（repair_skeleton_anchors 只做 append 不做插入）。
    from .anchor_control import ANCHOR_SECTION_KEY

    for line in lines:
        # 检测 section 标题
        lower = line.lstrip("-•* ").lower()
        if current_section == "anchors":
            continue
        if ANCHOR_SECTION_KEY in lower:
            current_section = "anchors"
            continue
        if any(k in lower for k in ("人物", "出场", "角色")):
            current_section = "characters"
            continue
        if any(k in lower for k in ("场景", "地点", "时间", "设定")):
            current_section = "scene"
            continue
        if any(k in lower for k in ("事件", "节拍", "情节", "经过", "过程")):
            current_section = "beats"
            continue
        if any(k in lower for k in ("冲突", "转折", "情绪")):
            current_section = "beats"
            continue

        # 清理 bullet 前缀
        clean = line.lstrip("-•* 0123456789.、 ")
        if not clean:
            continue

        if current_section == "characters" and len(clean) < 20:
            # 人物名：短于 20 字，可能是人物
            name = clean.split("：")[0].split(":")[0].strip()
            if name and name not in characters and len(name) < 15:
                characters.append(name)
        elif current_section == "scene" and not scene_setting:
            scene_setting = clean
        elif current_section == "beats":
            beats.append(clean)

    # fallback：如果没有结构化解析出 beats，就把非空行全当 beats
    if not beats:
        beats = [l.lstrip("-•* ") for l in lines if len(l) > 10]

    return beats, characters, scene_setting


def build_key_details_block(key_details: dict | None) -> str | None:
    """把关键细节锚点拼成【关键细节锚点】区块。

    从原文抽出的标志性细节（人物/数字/对白/物品）以列表形式注入生成 prompt，
    强制 LLM 在填充正文时保留这些细节 —— 解决"骨架抽象丢细节"导致生成
    偏离原文（橡皮擦被重写、性别改变、金额缺失、对白丢失）的问题。

    Args:
        key_details: extract_key_details 的返回 dict；None / 全空 → 返回 None

    Returns:
        区块文本（含 Markdown 标题），无可用细节时返回 None
    """
    if not key_details:
        return None
    chars = key_details.get("characters") or []
    nums = key_details.get("numbers") or []
    dias = key_details.get("dialogues") or []
    objs = key_details.get("objects") or []
    if not (chars or nums or dias or objs):
        return None

    lines = ["【关键细节锚点】以下细节是原文的标志性内容，正文中必须原样保留，"
             "不得改写、省略、替换或改变其含义："]
    if chars:
        lines.append(f"- 人物：{'、'.join(chars)}")
    if nums:
        lines.append(f"- 数字/金额：{'、'.join(n['text'] for n in nums)}")
    if dias:
        lines.append(f"- 经典对白（保持原句）：{'；'.join(f'「{q}」' for q in dias)}")
    if objs:
        lines.append(f"- 物品/专有名词：{'、'.join(objs)}")
    return "\n".join(lines)


def skeleton_to_generation_prompt(
    skeleton: str,
    chapter_section: str | None = None,
    key_details_block: str | None = None,
    ban_block: str | None = None,
) -> str:
    """把骨架包装成正向生成的 user prompt。

    ban_block：AI 味禁令块（单一来源 ai_flavor.AI_FLAVOR_BAN_BLOCK）。None 用
    对照版（复现路径，有原文基准）；推导/独立生成路径传 ai_flavor_ban_block(standalone=True)。


    让 LLM 基于骨架 + 风格来写正文，而不是基于原文段落。
    这样确保优化的是文风，不是情节记忆。

    可选 key_details_block：从原文抽出的关键细节锚点区块（build_key_details_block 的产物），
    注入后强制生成端保留标志性细节（橡皮擦/五万/经典对白等）。
    """
    section_hint = ""
    if chapter_section == "opening":
        section_hint = "这是小说开篇，请写出完整的开篇章节。"
    elif chapter_section == "main":
        section_hint = "这是正文章节中的一段，请续写完整的正文段落。"
    else:
        section_hint = "请基于以下情节骨架写出完整的小说正文。"

    anchor = ""
    if key_details_block:
        anchor = f"\n\n{key_details_block}"

    if ban_block is None:
        from .ai_flavor import ai_flavor_ban_block
        ban_block = ai_flavor_ban_block(standalone=False)

    return f"""{section_hint}

【情节骨架】
{skeleton}

请严格按照骨架中的情节来写，不要添加或改变主要事件。你要做的是用你自己的叙述风格把骨架填充成生动的小说正文。骨架中的【基调】标注是场景必须呈现的文学效果（如【基调：黑色幽默】要写出荒诞反差，而非平淡叙述），正文写作语气必须匹配该基调。

{ban_block}
{anchor}"""
