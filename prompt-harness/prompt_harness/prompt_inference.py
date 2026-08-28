"""Prompt 逆向推理引擎（v5.0）— 被 optimizer.py::optimize_v5() 调用。

本模块提供贝叶斯优化核心算法，是 prompt-harness 的低层推理引擎。
不直接暴露为 API 端点，而是通过 optimizer.py::optimize_v5() 供 server.py 使用。

核心思想：量化的套娃。
把 prompt 形式化为 47 维连续向量 P ∈ [0, 1]^D，
在 P 空间做贝叶斯优化，目标是使 LLM(P) 的结构指纹 V' 最接近目标 V(T)。
（v5.11 起评分内容主导：与原文的内容相似度为复现核心，见 compute_composite_score_v5）

v5.5 架构调整：此模块不再作为独立"一键提取"入口，
而是作为 optimizer.py 的推理引擎被调用。
详见：eager-humming-wilkes.md 和 vivid-noodling-wombat.md

P 向量结构（中粒度，47 维）：
  role (5维, one-hot 约束软化为连续)：
    [0] 玄幻写手  [1] 都市作家  [2] 资深网文作者  [3] 严肃文学  [4] 评论家
  focus (10维):
    [5] 对白驱动  [6] 短句节奏  [7] 场景铺陈  [8] 心理描写  [9] 动作刻画
    [10] 情感张力 [11] 情节推进 [12] 信息密度 [13] 氛围营造 [14] 悬念设置
  constraints (8维, 值越高约束越强):
    [15] 每段≤5句  [16] 对白≥40%  [17] 克制感叹号  [18] 每段≥3句
    [19] 避免省略号 [20] 不用生僻词 [21] 每章字数控制 [22] 视角统一
  style_rules (12维, 强度 0~1):
    [23] 使用短句  [24] 用词多样化 [25] 句式多变  [26] 叙述从容
    [27] 节奏明快  [28] 白描为主   [29] 多用动词  [30] 心理内化
    [31] 环境烘托  [32] 潜台词    [33] 章回体节奏 [34] 第三人称限知
  plot (7维, 剧情策略, 0.5 = 中性, >0.6 正向 / <0.4 反向):
    [35] 骨架忠实度 [36] 细节注入强度 [37] 信息释放 [38] 结尾策略
    [39] 冲突主轴  [40] 伏笔密度    [41] 对白博弈
  gen_params (5维):
    [42] temperature [43] top_p [44] presence_penalty [45] frequency_penalty [46] max_tokens_ratio
"""
from __future__ import annotations

import itertools
from functools import lru_cache

import numpy as np
from typing import Any, Callable


# ── P 向量维度元数据 ───────────────────────────────────────

P_DIM_META: dict[str, Any] = {
    "dim": 47,
    "groups": {
        "role": {
            "start": 0, "end": 5,
            "labels": ["玄幻写手", "都市作家", "资深网文作者", "严肃文学", "评论家"],
            "type": "categorical",  # one-hot 风格
        },
        "focus": {
            "start": 5, "end": 15,
            "labels": [
                "对白驱动", "短句节奏", "场景铺陈", "心理描写", "动作刻画",
                "情感张力", "情节推进", "信息密度", "氛围营造", "悬念设置",
            ],
            "type": "intensity",  # 强度 0~1
        },
        "constraints": {
            "start": 15, "end": 23,
            "labels": [
                "每段≤5句", "对白≥40%", "克制感叹号", "每段≥3句",
                "避免省略号", "不用生僻词", "每章字数控制", "视角统一",
            ],
            "type": "constraint",  # 约束强度 0~1，>0.5 生效
        },
        "style_rules": {
            "start": 23, "end": 35,
            "labels": [
                "使用短句", "用词多样化", "句式多变", "叙述从容",
                "节奏明快", "白描为主", "多用动词", "心理内化",
                "环境烘托", "潜台词", "章回体节奏", "第三人称限知",
            ],
            "type": "intensity",
        },
        "plot": {
            "start": 35, "end": 42,
            "labels": [
                "骨架忠实度", "细节注入强度", "信息释放",
                "结尾策略", "冲突主轴", "伏笔密度", "对白博弈",
            ],
            "type": "plot_strategy",  # 剧情策略，0.5 = 中性；>0.6 正向 / <0.4 反向
        },
        "gen_params": {
            "start": 42, "end": 47,
            "labels": [
                "temperature", "top_p", "presence_penalty",
                "frequency_penalty", "max_tokens_ratio",
            ],
            "type": "generation_params",  # 生成参数，0.5 = 默认/中性
        },
    },
}


# ── 剧情策略双极渲染表（plot 组 7 维，v>0.6 正向 / v<0.4 反向）──────────
# 骨架忠实度 / 细节注入强度 另在 _evaluate_p 直接消费（骨架包装强度 / 锚点切片）
PLOT_STRATEGY_TABLE: dict[str, dict[str, str]] = {
    "骨架忠实度": {
        "positive": "严格逐拍复现骨架：每个拍点都写到，顺序一致，不添加骨架外的主要情节。",
        "negative": "在骨架基础上自然发挥：保留主干，允许补充细节与过渡。",
    },
    "细节注入强度": {
        "positive": "优先保留原文标志性细节：人物、数字、对白、物品逐一呈现。",
        "negative": "细节为叙事服务，不必逐一保留。",
    },
    "信息释放": {
        "positive": "信息渐进释放：先反常细节制造疑问，真相后文逐层揭开。",
        "negative": "信息前置点明：关键背景与意图开篇即交代。",
    },
    "结尾策略": {
        "positive": "收在未决处：停反常、意外或半句话，疑问留给读者。",
        "negative": "即时收束：事态告一段落，干净利落。",
    },
    "冲突主轴": {
        "positive": "冲突以人际博弈为轴：角色试探、算计、攻防驱动。",
        "negative": "冲突以事件/环境为轴：外部变故压迫人物。",
    },
    "伏笔密度": {
        "positive": "前置埋伏：无关物件、动作、对话成为后文回收的线。",
        "negative": "即时兑现：细节当场说明用途。",
    },
    "对白博弈": {
        "positive": "对白是攻防：每句暗藏意图试探与掩饰。",
        "negative": "对白重在传达：直接承载信息与情绪。",
    },
}


def p_to_gen_params(p_vector: list[float] | np.ndarray, base_max_tokens: int | None = None) -> dict[str, float]:
    """把 P 向量 [42:47]（gen_params 组）映射为实际的生成参数。

    0.5 是中性值，落在各参数厂商推荐范围的中间：
      temperature      [0.1, 0.9]    → 0.1 + x*0.8
      top_p            [0.3, 0.95]   → 0.3 + x*0.65
      presence_penalty [-0.3, 0.5]   → -0.3 + x*0.8
      frequency_penalty[-0.3, 0.5]   → -0.3 + x*0.8
      max_tokens_ratio [0.5, 2.5]    → 0.5 + x*2.0（乘数，基值 = base_max_tokens）
    """
    p = np.asarray(p_vector, dtype=float).ravel()
    g = P_DIM_META["groups"]["gen_params"]
    vals = np.clip(p[g["start"]:g["end"]], 0.0, 1.0)
    x = vals
    params = {
        "temperature": round(float(0.1 + x[0] * 0.8), 3),
        "top_p": round(float(0.3 + x[1] * 0.65), 3),
        "presence_penalty": round(float(-0.3 + x[2] * 0.8), 3),
        "frequency_penalty": round(float(-0.3 + x[3] * 0.8), 3),
        "max_tokens_ratio": round(float(0.5 + x[4] * 2.0), 3),
    }
    if base_max_tokens:
        params["max_tokens"] = max(256, int(base_max_tokens * params["max_tokens_ratio"]))
    return params


def _p_to_dict(p_vector: list[float] | np.ndarray) -> dict[str, dict[str, float]]:
    """把 P 向量转成按组的 dict，方便渲染和展示。"""
    p = np.asarray(p_vector, dtype=float).ravel()
    result: dict[str, dict[str, float]] = {}
    for gname, gmeta in P_DIM_META["groups"].items():
        result[gname] = {}
        for i, label in enumerate(gmeta["labels"]):
            idx = gmeta["start"] + i
            result[gname][label] = float(p[idx]) if idx < len(p) else 0.0
    return result


# ── P 向量 → 文本 prompt ──────────────────────────────────

def _intensity(v: float) -> str:
    """把连续强度 v 映射为措辞分级（v5.13 L4a）。

    让同一模板项在不同 v 下渲染出不同句子——此前渲染器只做 0/1 勾选
    （focus>0.3、constraints>0.5…），同一条目不同 v 只换 ★/· 前缀，
    Prompt 文本表达力被封闭词库压扁。这里把强度嵌进文本。
    """
    if v >= 0.8:
        return "必须全力"
    if v >= 0.6:
        return "重点"
    if v >= 0.45:
        return "注重"
    return "适当"


def render_prompt(
    p_vector: list[float] | np.ndarray,
    *,
    chapter_section: str | None = None,
    threshold: float = 0.3,
    key_phrases: dict | None = None,
    target_char_count: int | None = None,
) -> str:
    """把 P 向量渲染成可读的 system prompt 文本。

    策略：
    1. role 组取 argmax 作为主身份，附带该身份的具体写作特征描述
    2. focus 组取 top-k（阈值以上的）作为侧重点，附带具体执行指令
    3. constraints 组取 >0.5 的作为硬约束，附带可验证的量化标准
    4. style_rules 组取 >threshold 的作为风格规则，含 do/don't 对
    5. plot 组按离 0.5 距离取前 6 条双极指令（v>0.6 正向 / v<0.4 反向）——剧情复现核心
    6. key_phrases 不为空时，添加用词参考段（提升 S_char）
    7. target_char_count 不为空时，注入动态字数硬约束（替代静态"2000-3000字"）

    Args:
        p_vector: 47 维 P 向量
        chapter_section: "opening" / "main" / None
        threshold: 风格规则的最低强度阈值（默认 0.3）
        key_phrases: 从目标文本抽取的词汇特征（可选）
        target_char_count: 目标文本的字符数（用于动态字数约束）

    Returns:
        渲染后的 system prompt 字符串
    """
    pd = _p_to_dict(p_vector)

    lines: list[str] = []
    lines.append("你是一位小说作家，请严格按照以下指令进行创作。")
    lines.append("")

    # ── 身份（role）──
    role_scores = pd["role"]
    best_role = max(role_scores, key=role_scores.get)
    role_descriptions = {
        "玄幻写手": "玄幻/修仙题材写手，擅长构建宏大的世界观和修炼体系，想象丰富",
        "都市作家": "都市/现实题材作家，善于刻画现代人际关系和社会细节，笔触细腻",
        "资深网文作者": "资深网络文学作者，深谙网文节奏，开篇抓人、爽点密集、更新稳定",
        "严肃文学": "纯文学/严肃文学风格，讲究语言精炼，注重意象和象征，叙事克制",
        "评论家": "评论/分析视角行文，带有叙事距离感，善于在叙事中插入评述",
    }
    role_desc = role_descriptions.get(best_role, f"{best_role}风格创作")
    lines.append(f"【身份定位】以{role_desc}进行写作。")

    # 融合第二角色（如果有）
    sorted_roles = sorted(role_scores.items(), key=lambda x: -x[1])
    if len(sorted_roles) > 1 and sorted_roles[1][1] > 0.35 and sorted_roles[0][1] - sorted_roles[1][1] < 0.25:
        secondary = sorted_roles[1][0]
        sec_desc = role_descriptions.get(secondary, f"{secondary}风格")
        lines.append(f"  同时吸收{sec_desc}的某些特质。")

    # ── 侧重点（focus）── 每条附带具体执行指令
    focus_items = [(k, v) for k, v in pd["focus"].items() if v > threshold]
    focus_items.sort(key=lambda x: -x[1])
    if focus_items:
        lines.append("")
        lines.append("【创作侧重】按重要性排列：")
        focus_instructions = {
            "对白驱动": "对话推动情节发展，每段对白需承载信息量或角色关系变化，避免无意义闲聊",
            "短句节奏": "多用短句（不超过20字），句号密集制造急促感，长短句交错控制阅读节奏",
            "场景铺陈": "展开环境细节，让读者在脑中构建画面，但避免大段静态描写",
            "心理描写": "深入角色内心，展示其思考、犹豫、决断的过程，增强代入感",
            "动作刻画": "用具体动作代替抽象描述，一个动作胜过三个形容词",
            "情感张力": "通过对白和细节传递情绪，让读者感受到角色的喜怒哀乐",
            "情节推进": "每段文字推进情节，减少游离于主线之外的描写和议论",
            "信息密度": "高密度信息输出，每句话包含有效情节或角色信息，不注水",
            "氛围营造": "用环境、光线、声音、气味等感官细节营造特定氛围",
            "悬念设置": "层层设疑，逐步释放信息，利用读者好奇心驱动阅读",
        }
        for name, val in focus_items[:5]:
            instruction = focus_instructions.get(name, f"注重{name}")
            marker = "★" if val > 0.75 else "·"
            lines.append(f"  {marker} {_intensity(val)}：{instruction}")

    # ── 约束（constraints）── 含可量化的可验证标准
    constraints_active = [(k, v) for k, v in pd["constraints"].items() if v > 0.5]
    if constraints_active:
        lines.append("")
        lines.append("【硬性约束】必须遵守：")
        constraint_map = {
            "每段≤5句": "每段不超过 5 句话。可验证：任一段落句数 ≤5。",
            "对白≥40%": "对白内容占比 ≥40%。可验证：引号内文字 / 总字数 ≥0.4。",
            "克制感叹号": "全文感叹号不超过 3 个。可验证：全文 '！' 计数 ≤3。",
            "每段≥3句": "每段至少 3 句话。可验证：任一段落句数 ≥3。",
            "避免省略号": "不使用省略号『……』。可验证：全文 '…' 计数 = 0。",
            "不用生僻词": "用词通俗易懂，不使用生僻字词。避免文言语汇。",
            "每章字数控制": (
                f"参见上方【篇幅】要求，约 {target_char_count} 字，紧凑推进。"
            ) if target_char_count else "章节长度控制在 2000-3000 字。",
            "视角统一": "全文保持统一的叙事视角，不随意切换 POV。",
        }
        for name, val in constraints_active:
            constraint_text = constraint_map.get(name, name)
            # 约束强度 >0.8 加 ⚠ 强调；强度词（v5.13 L4a）嵌进文本
            prefix = "⚠ " if val > 0.8 else "  "
            lines.append(f"  {prefix}{constraint_text}（{_intensity(val)}）")

    # ── 风格规则（style_rules）── 含 do/don't 对
    style_items = [(k, v) for k, v in pd["style_rules"].items() if v > threshold]
    style_items.sort(key=lambda x: -x[1])
    if style_items:
        lines.append("")
        lines.append("【文风要求】")
        style_rules_detail = {
            "使用短句": {
                "do": "单句不超过 25 字，多用句号断句",
                "dont": "避免超过 40 字的长句，不用多层嵌套从句",
            },
            "用词多样化": {
                "do": "同一段内避免重复使用相同的形容词/动词",
                "dont": "不要连续使用『的』结构",
            },
            "句式多变": {
                "do": "长短句错落，连续 5 句后至少变换一次句式结构",
                "dont": "不要连续使用『主语+谓语+宾语』的固定句式",
            },
            "叙述从容": {
                "do": "给关键场景足够的篇幅展开，不急于推进",
                "dont": "不要跳过情绪积累直接进入情节",
            },
            "节奏明快": {
                "do": "事件衔接紧凑，情节推进不留空白",
                "dont": "不要大段铺陈无关的景物描写",
            },
            "白描为主": {
                "do": "用具体的动作、对话、细节呈现，让事实说话",
                "dont": "不要使用主观评价（『他感到很伤心』→『他眼眶红了』）",
            },
            "多用动词": {
                "do": "优先选择动态动词替代静态描述",
                "dont": "减少『是』『有』『在』等静态系动词的使用",
            },
            "心理内化": {
                "do": "通过外部行为暗示心理活动",
                "dont": "避免直接写出『他想…』『他觉得…』等内心独白",
            },
            "环境烘托": {
                "do": "用环境描写暗示角色情绪和故事走向",
                "dont": "不要为写景而写景，环境必须服务于叙事",
            },
            "潜台词": {
                "do": "对白要有所保留，角色的真实意图在话语之外",
                "dont": "避免角色直白说出内心想法（『我其实很害怕』）",
            },
            "章回体节奏": {
                "do": "章节末尾留钩子，起承转合分明",
                "dont": "不要平平无奇地结束一章",
            },
            "第三人称限知": {
                "do": "紧贴主角视角叙事，只写主角能看到/听到/感受到的",
                "dont": "不要切换到他/她/它的内心或视野",
            },
        }
        for name, val in style_items[:8]:
            detail = style_rules_detail.get(name)
            if detail:
                prefix = "★ " if val > 0.75 else "  "
                lines.append(f"  {prefix}{name}（{_intensity(val)}）：")
                lines.append(f"    ✅ {detail['do']}")
                lines.append(f"    ❌ {detail['dont']}")
            else:
                lines.append(f"  · 注重{name}")

    # ── 区段提示 ──
    lines.append("")
    if chapter_section == "opening":
        lines.append("【任务要求】")
        lines.append("这是小说开篇。请写出开篇段落：")
        lines.append("1. 直接进入场景或事件。假设读者已在故事中、认识人物——不铺垫、不介绍、不渲染氛围。")
        lines.append("2. 通过行动/对话/冲突展现角色性格，避免纯描述式人物出场。")
        lines.append("3. 故事本身的张力自然驱动好奇心，不要刻意「制造悬念钩子」或「渲染情绪基调」。")
        lines.append("4. 前 200 字内必须有人出现并有行动——不能全是景物或心理描写。")
    elif chapter_section == "main":
        lines.append("【任务要求】")
        lines.append("请写出小说正文：")
        lines.append("1. 根据场景描述展开情节，保持事件逻辑连贯")
        lines.append("2. 角色互动要自然，对白需符合人设和当前情绪")
        lines.append("3. 叙事节奏要有张有弛，关键冲突场景充分展开")
        lines.append("4. 确保角色情绪变化有足够的铺垫和过渡")
    else:
        lines.append("【任务要求】")
        lines.append("请根据场景描述，写出完整的小说正文。")

    # ── 剧情策略（plot）── 复现原文的核心指令，双极渲染
    # 每维按离 0.5 的距离排序取前 6 条；v>0.55 正向 / v<0.45 反向；0.45~0.55 中性不渲染
    # 【v5.13 L4b】中性带从 0.4~0.6 收窄到 0.45~0.55，更多迭代携带【剧情策略】
    plot_items: list[tuple[str, float, str]] = []
    for name, val in pd["plot"].items():
        if val > 0.55:
            plot_items.append((name, val, "positive"))
        elif val < 0.45:
            plot_items.append((name, val, "negative"))
    plot_items.sort(key=lambda x: abs(x[1] - 0.5), reverse=True)
    if plot_items:
        lines.append("")
        lines.append("【剧情策略】关于如何复现故事情节，必须遵守：")
        for name, val, pole in plot_items[:6]:
            entry = PLOT_STRATEGY_TABLE.get(name)
            if not entry:
                continue
            text = entry.get(pole)
            if not text:
                continue
            marker = "★" if abs(val - 0.5) > 0.3 else " "
            lines.append(f"  {marker} {text}")

    # ── 用词参考（key_phrases 注入，提升 S_char）─────────
    if key_phrases:
        lines.append("")
        lines.append("【用词参考】写作时注意以下词汇风格特征：")
        # 高频实词
        top_words = key_phrases.get("top_words", [])
        if top_words:
            words_str = "、".join(w for w, _ in top_words[:10])
            lines.append(f"  · 高频字：{words_str}")
        # 二字短语
        top_bigrams = key_phrases.get("top_bigrams", [])
        if top_bigrams:
            bigram_str = "、".join(b for b, _ in top_bigrams[:6])
            lines.append(f"  · 常用组合：{bigram_str}")
        # 功能词
        func_stats = key_phrases.get("func_word_stats", {})
        if func_stats:
            lines.append(f"  · 功能词频率参考：的{func_stats.get('的/千字','?')}‰ "
                        f"了{func_stats.get('了/千字','?')}‰ "
                        f"地{func_stats.get('地/千字','?')}‰ "
                        f"着{func_stats.get('着/千字','?')}‰")
        # 风格标记
        markers = key_phrases.get("distinctive_markers", [])
        if markers:
            markers_str = "、".join(markers[:5])
            lines.append(f"  · 风格用词：{markers_str}")
        # 句首词
        starters = key_phrases.get("sentence_starters", [])
        if starters:
            st_str = "、".join(f"{w}" for w, _ in starters[:4])
            lines.append(f"  · 句首偏好：{st_str}")
        # 【v5.13 ④ 语义化】embedding 选出的原文代表性句子——可直接原样复现
        # 【v5.18】g17：输出全部（70 平衡锚），不再截断 [:5]。
        anchor_sents = key_phrases.get("semantic_anchor_sentences", [])
        if anchor_sents:
            lines.append("")
            lines.append("【原文句参考】以下句子是原文的标志性语句，可原样复现或高度贴近（提升复现度）：")
            for i, s in enumerate(anchor_sents, 1):
                lines.append(f"  {i}. {s}")

    lines.append("")
    lines.append("只输出正文，不输出任何元说明、提示、描述或注释。")

    # ── 字数约束块（强制注入，不受 P[21] 控制）───────────
    if target_char_count:
        lines.append("")
        lines.append(f"【篇幅】本章预计写 {target_char_count} 字左右。")
        lines.append(f"请用对白和场景推进，保持节奏紧凑，不注水、不拖沓。")

    # ── 连载约束块（强制注入，抑制短篇故事综合征）────────
    lines.append("")
    lines.append("【连载约束——必须遵守】")
    lines.append("1. 开头：直接进入场景或事件，不要做氛围渲染、世界观介绍、角色出场铺垫。")
    lines.append("   正确示范：「“你迟到了。”林川推开铁门，雨声灌了进来。」")
    lines.append("   错误示范：「夜幕低垂，古老的城墙在月色下泛着青灰色的光…」")
    lines.append("2. 结尾：情节推进到当前节点即止——停在动作、反应或对话上。不要总结、不要升华、不要抒情收束、不要刻意留悬念钩子。")
    lines.append("   正确示范：「林川把杯子搁下。门外脚步声远了。」")
    lines.append("   错误示范：「林川没回头。他知道身后的脚步声会一直跟着他走进六楼。」")
    lines.append("   错误示范：「这一夜，他终于明白了人生的真谛…」")
    lines.append("3. 全文：本章是长篇小说连载的一部分，不是独立短篇。")
    lines.append("   每个场景都应当假设读者已经认识人物、了解背景。")
    if target_char_count:
        lines.append(f"4. 字数：目标约 {target_char_count} 字，以对白和行动推进，拒绝平铺直叙。")
    lines.append("5. 结尾不需要刻意留钩子或悬念——当前情节的自然张力已足够驱动读者翻页。")
    lines.append("   禁止在结尾制造新悬念、新疑问或「暴风雨前的宁静」式收束。")

    return "\n".join(lines)


# ── 文本 prompt → P 向量（粗糙反解，用于冷启动）─────────

def parse_prompt_to_p(prompt_text: str, *, dim_meta: dict | None = None) -> list[float]:
    """从文本 prompt 粗糙反解 P 向量。

    用关键词匹配做启发式估计，主要用于历史经验的冷启动（给 BO 一个先验）。
    不追求精确——BO 会自己修正。
    """
    meta = dim_meta or P_DIM_META
    dim = meta["dim"]
    p = np.zeros(dim)
    text = prompt_text

    # keyword → (group_index_within_group, score)
    keyword_map: dict[str, tuple[str, int, float]] = {}

    for gname, gmeta in meta["groups"].items():
        for i, label in enumerate(gmeta["labels"]):
            keyword_map[label] = (gname, i, 0.7)

    # 补充一些同义词/相关词
    synonyms = {
        "对白": ("focus", 0, 0.6),
        "对话": ("focus", 0, 0.6),
        "短句": ("focus", 1, 0.6),
        "场景": ("focus", 2, 0.5),
        "心理": ("focus", 3, 0.5),
        "动作": ("focus", 4, 0.5),
        "情感": ("focus", 5, 0.5),
        "情节": ("focus", 6, 0.5),
        "信息": ("focus", 7, 0.4),
        "氛围": ("focus", 8, 0.5),
        "悬念": ("focus", 9, 0.5),
        "玄幻": ("role", 0, 0.8),
        "修仙": ("role", 0, 0.7),
        "都市": ("role", 1, 0.8),
        "网文": ("role", 2, 0.7),
        "文学": ("role", 3, 0.6),
        "评论": ("role", 4, 0.6),
        # v5.11 plot 组同义词（剧情策略）
        "骨架": ("plot", 0, 0.6),
        "拍点": ("plot", 0, 0.6),
        "复现骨架": ("plot", 0, 0.7),
        "细节注入": ("plot", 1, 0.6),
        "信息释放": ("plot", 2, 0.6),
        "结尾策略": ("plot", 3, 0.6),
        "冲突主轴": ("plot", 4, 0.6),
        "伏笔": ("plot", 5, 0.6),
        "对白博弈": ("plot", 6, 0.6),
    }
    keyword_map.update(synonyms)

    for kw, (gname, idx, score) in keyword_map.items():
        if kw in text:
            gmeta = meta["groups"][gname]
            global_idx = gmeta["start"] + idx
            if global_idx < dim:
                p[global_idx] = max(p[global_idx], score)

    # 【v5.8】gen_params 维度（生成参数）用英文标签，中文 prompt 匹配不到 → 留在 0。
    # 0 会映射到参数下限（如 temperature=0.1），不是好的冷启动先验；
    # 统一落到 0.5 中性值，让 BO 从"厂商推荐默认"出发。
    if "gen_params" in meta["groups"]:
        g = meta["groups"]["gen_params"]
        p[g["start"]:g["end"]] = np.where(
            p[g["start"]:g["end"]] == 0, 0.5, p[g["start"]:g["end"]],
        )

    # 【v5.11】plot 维度（剧情策略）同样避免 0 值——0 会渲染成"反向极"指令，
    # 冷启动不应假设剧情策略是反向的；统一落到 0.5 中性，由 BO 自己探索。
    if "plot" in meta["groups"]:
        g = meta["groups"]["plot"]
        p[g["start"]:g["end"]] = np.where(
            p[g["start"]:g["end"]] == 0, 0.5, p[g["start"]:g["end"]],
        )

    # role 组：归一化（如果有非零值）
    role_start = meta["groups"]["role"]["start"]
    role_end = meta["groups"]["role"]["end"]
    role_sum = p[role_start:role_end].sum()
    if role_sum > 0:
        p[role_start:role_end] /= role_sum
        p[role_start:role_end] *= 0.9  # 留一点不确定性

    return p.tolist()


# ── 连载适配检查（反短篇故事综合征）─────────────────────

# 结尾总结/升华/收束标记词（与 _anti_ai.py 一致）
_ENDING_SUMMARY_MARKS = {"总之", "最终", "从此", "这一夜", "一切都", "一切都会",
                         "就这样", "这便是", "这就是", "这才是"}
_ENDING_SUBLIMATION_MARKS = {"他终于明白", "人生的", "命运的", "未来的路",
                              "生命的", "也许有一天", "或许有一天", "他深深"}
_ENDING_CLOSURE_MARKS = {"故事还在继续", "沉沉睡去", "一切归于平静",
                          "夜色如墨", "天边泛白", "黎明", "夕阳", "余晖",
                          "晚风", "夜风", "夜更深了", "日复一日"}
_OPENING_EXPOSITION_MARKS = {"夜幕低垂", "星光洒在", "古老的",
                              "在这个世界", "这是一个", "话说",
                              "传说中", "很久以前", "从前"}


def _check_serialization_patterns(generated: str) -> dict[str, float]:
    """检测生成文本是否包含"短篇故事综合征"模式。

    Returns:
        {
            "opening_penalty": float,  # 0~1 开头铺陈惩罚
            "ending_penalty": float,   # 0~1 结尾收束惩罚
            "serialization_fit": float # 0~1 总体连载适配度
        }
    """
    if not generated or len(generated) < 200:
        return {"opening_penalty": 0.0, "ending_penalty": 0.0, "serialization_fit": 1.0}

    opening_penalty = 0.0
    ending_penalty = 0.0

    # 开头检测：前 200 字
    opening = generated[:200]
    opening_lines = [l.strip() for l in opening.replace('\r', '').split('\n') if l.strip()]
    if opening_lines:
        first_line = opening_lines[0]
        for mark in _OPENING_EXPOSITION_MARKS:
            if mark in first_line[:30]:
                opening_penalty = max(opening_penalty, 0.6)
                break
        # 连续多段纯环境描写（无人无对白）
        env_only_count = 0
        for line in opening_lines[:4]:
            if not any(ch in line[:80] for ch in '他说她道："“「'):
                env_only_count += 1
            else:
                break
        if env_only_count >= 3:
            opening_penalty = max(opening_penalty, 0.5)
        elif env_only_count >= 2 and len(opening_lines) >= 3:
            opening_penalty = max(opening_penalty, 0.3)

    # 结尾检测：后 300 字
    ending = generated[-300:] if len(generated) > 300 else generated
    for mark in _ENDING_SUMMARY_MARKS:
        if mark in ending:
            ending_penalty = max(ending_penalty, 0.7)
            break
    for mark in _ENDING_SUBLIMATION_MARKS:
        if mark in ending:
            ending_penalty = max(ending_penalty, 0.6)
            break
    for mark in _ENDING_CLOSURE_MARKS:
        if mark in ending:
            ending_penalty = max(ending_penalty, 0.5)
            break
    # 检查省略号结尾（总结感）
    if ending.rstrip().endswith('…') or ending.rstrip().endswith('...'):
        ending_penalty = max(ending_penalty, 0.3)
    # 检查是否有 "空行 + 抒情短句" 的收束模式
    ending_paras = [p.strip() for p in ending.split('\n\n') if p.strip()]
    if ending_paras and len(ending_paras[-1]) < 20:
        for mark in _ENDING_SUMMARY_MARKS | _ENDING_SUBLIMATION_MARKS:
            if mark in ending_paras[-1]:
                ending_penalty = max(ending_penalty, 0.8)
                break

    serialization_fit = 1.0 - max(opening_penalty, ending_penalty)
    return {
        "opening_penalty": round(opening_penalty, 4),
        "ending_penalty": round(ending_penalty, 4),
        "serialization_fit": round(max(0.0, serialization_fit), 4),
    }


# ── 关键细节锚点（v5.10）────────────────────────────────
# 从原文抽取标志性细节（人物/数字/对白/物品）→ 组装【关键细节锚点】区块
# → 注入生成 prompt；同一批细节参与事实一致性评分。
# 抽取本身有 lru_cache（key_details.get_key_details），这里再缓存组装结果，
# 避免对同一个 target_text 评估上百个 P 向量时反复序列化。

@lru_cache(maxsize=16)
def _cached_anchor_block(target_text: str) -> str | None:
    """构建 target_text 的【关键细节锚点】区块（无细节时返回 None）。"""
    from .key_details import get_key_details
    from .plot_skeleton import build_key_details_block
    return build_key_details_block(get_key_details(target_text))


@lru_cache(maxsize=16)
def _cached_dialogue_contract(target_text: str) -> str:
    """构建 target_text 的【对白顺序契约】区块（确定性提取，零 LLM）。

    【v5.21】骨架保持紧凑（LLM 提取无法在紧凑骨架里逐轮拆对白——合并/漏轮实证），
    对白顺序改由生成输入保证：把原文全部对话轮次按出现顺序提取并编号，注入
    生成 prompt，强制模型按序复现，不倒序、不遗漏。返回空串 = 无对白。
    """
    from .plot_similarity import _extract_dialogue_turns
    turns = _extract_dialogue_turns(target_text)
    if not turns:
        return ""
    lines = [f"{i}. {t}" for i, t in enumerate(turns, 1)]
    return (
        "【对白顺序契约】以下是从原文按出现顺序提取的全部对话轮次（共 "
        f"{len(turns)} 轮）。正文中的对白必须按此顺序逐一出现，不得颠倒顺序、"
        "不得省略；每轮可用原句或转述，但相对顺序必须与清单一致。极短反应轮"
        "（≤2 字）如与原语境不符可并入相邻轮，其余轮次必须完整保留。"
        "⚠️ 原文对白极其密集，常连续多轮直接接续（两轮之间无任何叙述）。"
        "契约中连续出现的轮次，正文必须让它们直接接续——不得为了凑字数在每轮"
        "对白前后添加『动作+神态+感官』描写（如「他抬了抬眼，指尖轻敲」「声音裹着"
        "凉意」）。只在对白本身交代不清时才加极简、有信息量的动作（如「推了推眼镜」"
        "「划去重写」）：\n"
        + "\n".join(lines)
    )


def _slice_anchor_block(block: str | None, strength: float) -> str | None:
    """按细节注入强度（plot 组 [36]）切片锚点区块。

    v >= 0.4 → 保留全部（0.5 中性 = v5.10 行为，向后兼容）；
    v < 0.4  → 按 v/0.4 比例保留前 N 行（人物/数字 在区块前列，优先保留更显著的锚点）。
    """
    if not block:
        return block
    v = float(strength)
    if v >= 0.4:
        return block
    lines = block.splitlines()
    if not lines:
        return block
    detail_lines = lines[1:]
    if not detail_lines:
        return block
    frac = max(0.1, v / 0.4)
    keep = max(1, int(round(len(detail_lines) * frac)))
    return "\n".join([lines[0]] + detail_lines[:keep])


# ── 评估一个 P 向量（正向生成 + 计算综合分）──────────────

async def _evaluate_p(
    p_vector: list[float],
    *,
    skeleton_text: str,
    target_text: str,  # 原文（ground truth），用于 S_char 比较
    target_v: dict,
    chapter_section: str | None,
    gen_params: dict | None,
    key_phrases: dict | None = None,  # 用词参考（提升 S_char）
    scoring_weights: dict[str, float] | None = None,  # 【v4.0】自定义评分权重
    mode: str = "quality",  # 【v5.13 L2】fast/standard/quality，决定锚点反馈重试上限
    repro_ctx: dict | None = None,  # 【v5.14 L2b】纯复现重试全程额度（reverse_infer 注入，防生成翻倍）
    emphasis_block: str = "",  # 【v5.16】文本梯度强调块（跨迭代累积），注入 user_input 基础段
    repro_cfg: dict | None = None,  # 【v5.18】复现链条配置（l2_rounds 等，None → 默认 g17）
) -> dict[str, Any]:
    """评估一个 P 向量：渲染 → 生成 → 计算 V' → 综合分。

    Returns:
        {score, v_cosine, char_similarity, s_char, plot_fidelity,
         length_alignment, serialization_fit, content_quality,
         factual_consistency, generated_text, v_prime}
    """
    from .optimizer import forward_generation_v4
    from .structural_analyzer import (
        extract_structural_vector, compute_structural_similarity,
        compute_composite_score_v5, serialization_fit_score,
        content_quality_score, dialogue_quality_score, defensive_writing_score,
        ai_divergence_score, dialogue_gap_bloat, ending_action_density,
    )
    from .scorer import (
        char_level_similarity, length_alignment_score, semantic_coverage,
        fluency_score, compression_ratio, narrative_cohesion, function_word_kl,
    )
    from .plot_skeleton import skeleton_to_generation_prompt

    prompt_text = render_prompt(p_vector, chapter_section=chapter_section,
                                key_phrases=key_phrases,
                                target_char_count=len(target_text))

    # 【v5.13 L2】关键细节锚点前移：校验侧与评分侧共用同一 kd（lru_cache，零成本）。
    from .key_details import get_key_details
    kd = get_key_details(target_text)

    # 【修复】生成端：把骨架包进"严格按骨架情节写"的指令再喂给模型。
    # 裸骨架只提供素材、模型没有义务遵守 → 自由发挥添加/删减情节；
    # skeleton_to_generation_prompt 明确要求"不添加或改变主要事件"。
    # 【v5.8】从 P[42:47] 提取生成参数，与传入基值合并（P 维度值优先）。
    # 之前 temperature/top_p/penalties 全循环固定 → 优化器只搜风格、不搜生成方式；
    # 现在 5 个生成参数成为 BO 的可优化维度，每个候选携带自己的参数。
    # 【v5.10】关键细节锚点：把原文标志性细节（人物/数字/对白/物品）注入生成 prompt，
    # 约束生成端保留这些细节 —— 解决"骨架抽象丢细节"导致生成偏离原文（橡皮擦被重写、
    # 性别改变、金额缺失、经典对白丢失）。
    # 【v5.11】plot 组消费：
    #   - 细节注入强度（p[36]）→ 锚点区块按比例切片（v>=0.4 保留全部 = v5.10 行为）
    #   - 骨架忠实度（p[35]）  → 骨架包装措辞强度（正向强约束 / 反向松绑）
    _plot_start = P_DIM_META["groups"]["plot"]["start"]
    _fid = p_vector[_plot_start] if _plot_start < len(p_vector) else 0.5
    _det = p_vector[_plot_start + 1] if _plot_start + 1 < len(p_vector) else 0.5
    user_input = skeleton_to_generation_prompt(
        skeleton_text, chapter_section=chapter_section,
        key_details_block=_slice_anchor_block(_cached_anchor_block(target_text), _det),
    )
    # 【v5.16】文本梯度强调块（跨迭代累积的系统性复现点），作为基础段注入，
    # 本地 L2 反馈随后只补"强调块没盖住的剩余缺口"。
    if emphasis_block:
        user_input = user_input.rstrip() + "\n\n" + emphasis_block
    if _fid >= 0.6:
        user_input += ("\n\n【骨架忠实度】逐拍复现：每个拍点都必须写到，拍点顺序不得改变，"
                       "不得省略拍点，不得增加骨架外的主要情节。")
    elif _fid < 0.4:
        user_input += ("\n\n【骨架忠实度】在骨架主干基础上自然发挥：保留主要事件，"
                       "允许补充细节与过渡。")
    # 【v5.21】生成端有序对白契约：骨架保持紧凑，对白顺序靠生成输入保证。
    # 确定性提取原文全部对话轮次（按序编号）注入，强制模型按序复现。
    # 局部导入（本函数内 _SETTINGS 在更靠后的评分块才定义，此处单独取）。
    from .config import SETTINGS as _stg
    if getattr(_stg, "dialogue_order_contract", True):
        _contract = _cached_dialogue_contract(target_text)
        if _contract:
            user_input += "\n\n" + _contract
    eff_gen_params = dict(gen_params or {})
    _gp = p_to_gen_params(p_vector, base_max_tokens=eff_gen_params.get("max_tokens"))
    eff_gen_params.update({k: v for k, v in _gp.items() if k != "max_tokens_ratio"})

    try:
        generated = await forward_generation_v4(
            user_input,
            gen_params=eff_gen_params or {},
            system_prompt=prompt_text,
        )
    except Exception as e:
        return {
            "score": 0.0, "v_cosine": 0.0, "char_similarity": 0.0,
            "s_char": 0.0,
            "length_alignment": 0.0, "serialization_fit": 0.0,
            "content_quality": 0.0, "factual_consistency": 0.0,
            "plot_fidelity": 0.0, "semantic_coverage": 0.0,
            "generated_text": "", "v_prime": None, "error": str(e),
            "user_input": "", "gen_params": eff_gen_params,
            "repro_retried": 0, "repro_gaps": [],  # 【v5.14 L2b】默认值（错误路径形状一致）
        }

    # 字数控制：生成长度不足 85% 时，加大 max_tokens 重试一次
    target_len = len(target_text)
    if generated and len(generated) > 0 and len(generated) < target_len * 0.85:
        cap = eff_gen_params.get("max_tokens", 4096)
        if cap < 8192:
            retry_params = dict(eff_gen_params)
            retry_params["max_tokens"] = min(8192, max(4096, int(cap * 1.5)))
            retry_params["temperature"] = max(0.2, retry_params.get("temperature", 0.3) - 0.05)
            try:
                generated = await forward_generation_v4(
                    user_input,
                    gen_params=retry_params,
                    system_prompt=prompt_text,
                )
            except Exception:
                pass

    # 【v5.17】L2 前的初稿快照：供反退化闸门（L2 补写不得把 cohesion 打到 floor 以下）回退。
    _gen_before_l2 = generated

    # 【v5.13 L2】生成层锚点校验 + 反馈重生成（硬执行，最多 MAX_ANCHOR_RETRY 次）。
    # 【v5.14 L2b】合并「锚点反馈 + 原文复现反馈」为一次重生成，同时修 fc/pf/s_char。
    # 【v5.15】预算解耦：锚点缺失驱动 → 沿用 MAX_ANCHOR_RETRY（免费，成本=现状）；
    #   纯复现驱动（锚点已齐但复现缺口大）→ 独立计数 repro_retried < REPRO_ITER_CAP
    #   且扣 repro_ctx 全程额度（v5.14 单计数器封顶 fast 每迭代 1 次 → 额度花不掉）。
    # 与评分侧同一套判定（factual_consistency_score 的 detail_results）：
    #   缺高价值锚点（weight≥2.0）→ 把缺失锚点拼成反馈指令追加重生成；
    #   仍缺 → deterministic_anchor_patch 只对经典对白做保守插入（数字/人物不硬插）。
    # 空/过短不重试（交给 L3 拒收，省一次 LLM 调用）。
    from .anchor_control import (
        verify_generated_anchors, build_anchor_feedback, deterministic_anchor_patch,
        select_reproduction_gaps, build_reproduction_feedback,
        MAX_ANCHOR_RETRY, FEEDBACK_ANCHOR_CAP, REPRO_GAP_CAP, REPRO_MIN_TOTAL_GAIN,
        REPRO_ITER_CAP,
    )
    from .plot_fidelity import beat_sequence_fidelity
    max_anchor_retry = MAX_ANCHOR_RETRY.get(mode, 1)
    # 【v5.18】g17：l2_rounds 覆盖纯复现重试上限（0 → 关闭 L2 复现反馈，保持锚点驱动）。
    _l2_rounds = int((repro_cfg or {}).get("l2_rounds", 2))
    repro_iter_cap = REPRO_ITER_CAP if _l2_rounds >= 0 else 0  # 负值兜底关 L2
    if _l2_rounds == 0:
        repro_iter_cap = 0
    elif _l2_rounds < REPRO_ITER_CAP:
        repro_iter_cap = _l2_rounds
    anchors_retried = 0
    anchors_patched: list[str] = []
    anchor_verify = {"missing": [], "score": 1.0, "all_present": True}
    repro_retried = 0              # 【v5.14 L2b】复现反馈重试次数
    repro_gaps: list[dict] = []    # 【v5.14 L2b】最终残留复现缺口（诊断）
    if generated and len(generated) >= target_len * 0.30:
        anchor_verify = verify_generated_anchors(generated, kd, cap=FEEDBACK_ANCHOR_CAP)
        # 【v5.14 L2b】复现缺口：s_char 缺失独有 n-gram 句 + pf 低覆盖拍对齐句（纯 n-gram，零 LLM）
        _pf_pre = beat_sequence_fidelity(skeleton_text, generated, target_text=target_text)
        repro_gaps = select_reproduction_gaps(
            generated, target_text, _pf_pre.get("per_beat") or [], cap=REPRO_GAP_CAP,
        )
        _gain = sum(g.get("gain_estimate", 0.0) for g in repro_gaps)
        repro_active = bool(repro_gaps) and _gain >= REPRO_MIN_TOTAL_GAIN
        # 【v5.15】预算解耦循环：锚点驱动 / 纯复现驱动各自独立计数。
        # 【v5.15.1 修复】真实链路锚点残留（fc≈0.98）让锚点预算先耗尽 → 若直接 break
        #   会把纯复现驱动一并掐死（v5.15 实测 repro_retried 恒 1、额度花不掉）。修复：
        #   锚点预算耗尽但复现仍激活 → 降级为纯复现驱动（去掉锚点块，扣 repro 额度）。
        while anchor_verify["missing"] or repro_active:
            _anchor_missing = bool(anchor_verify["missing"])
            if _anchor_missing:
                # 锚点驱动：沿用 MAX_ANCHOR_RETRY（免费）
                if anchors_retried >= max_anchor_retry:
                    if not repro_active:
                        break
                    _anchor_missing = False  # 锚点预算耗尽 → 降级为纯复现驱动继续
            if not _anchor_missing:
                # 纯复现驱动：单迭代上限 + 全程额度双控（额度尽则不再额外生成）
                if (repro_ctx is None or repro_ctx.get("remaining", 0) <= 0
                        or repro_retried >= repro_iter_cap):
                    break
                repro_ctx["remaining"] -= 1
            else:
                anchors_retried += 1  # 锚点驱动 / 合并迭代
            if repro_active:
                repro_retried += 1
            _blocks = []
            if _anchor_missing:
                _blocks.append(build_anchor_feedback(anchor_verify["missing"], attempt=anchors_retried))
            if repro_active:
                _blocks.append(build_reproduction_feedback(repro_gaps, attempt=anchors_retried))
            user_input = user_input.rstrip() + "\n\n" + "\n\n".join(_blocks)  # 局部 user_input，不动共享 skeleton_text
            try:
                generated = await forward_generation_v4(
                    user_input,
                    gen_params=eff_gen_params,
                    system_prompt=prompt_text,
                )
            except Exception:
                break
            anchor_verify = verify_generated_anchors(generated, kd, cap=FEEDBACK_ANCHOR_CAP)
            # 【v5.14 L2b】重算复现缺口（基于当前 generated，避免反复拼同一句）
            _pf_pre = beat_sequence_fidelity(skeleton_text, generated, target_text=target_text)
            repro_gaps = select_reproduction_gaps(
                generated, target_text, _pf_pre.get("per_beat") or [], cap=REPRO_GAP_CAP,
            )
            _gain = sum(g.get("gain_estimate", 0.0) for g in repro_gaps)
            repro_active = bool(repro_gaps) and _gain >= REPRO_MIN_TOTAL_GAIN
        if anchor_verify["missing"]:
            # 兜底：对白保守补丁（插入后重新校验，更新 anchor_verify）
            _patched, _inserted = deterministic_anchor_patch(generated, anchor_verify["missing"])
            if _inserted:
                generated = _patched
                anchors_patched = list(_inserted)
                anchor_verify = verify_generated_anchors(generated, kd, cap=FEEDBACK_ANCHOR_CAP)

    # 【v5.27.1】AI 味同轮修正循环：审阅发现 high 级 AI 味 → 追加【AI味修正块】重生成。
    # 之前审阅只在评分阶段跑，findings 只能跨迭代（improvement_context）注入下一候选；
    # 这里让修正立即作用于本候选（LLM 能看出但"本轮不改"的根因修复）。
    # 审阅结果 ai_flavor_res 供评分阶段复用（不再二次 create_task）。
    ai_flavor_res = None
    ai_flavor_retried = 0
    try:
        from .config import SETTINGS as _stg2
        _ai_on = bool(getattr(_stg2, "ai_flavor_review", True))
        _ai_retry_cap = max(0, int(getattr(_stg2, "ai_flavor_retry", 1)))
    except Exception:
        _ai_on = False
        _ai_retry_cap = 1
    if _ai_on and generated and len(generated) >= target_len * 0.30:
        from .ai_flavor import (review_ai_flavor, build_ai_flavor_feedback,
                                build_gap_feedback, build_verbatim_feedback)
        _AI_FLAVOR_DIRTY_THRESH = 0.65  # 融合分低于此（两个信号都差）→ 触发同轮修正
        _GAP_CLEAN_THRESH = 0.82        # 确定性对白间隙检测低于此 → 触发间隙修正（主信号，稳定）
        ai_flavor_res = await review_ai_flavor(generated, target_text)
        while ai_flavor_retried < _ai_retry_cap and ai_flavor_res is not None \
                and not ai_flavor_res.get("error"):
            _findings = ai_flavor_res.get("findings") or []
            _high = [f for f in _findings if f.get("severity") == "high"]
            _gap_clean = (ai_flavor_res.get("gap") or {}).get("clean", 1.0)
            _fused = ai_flavor_res.get("score") or 1.0
            _gap_fb = build_gap_feedback(generated, target_text)
            # 触发条件（以确定性信号为主，避免审阅全局分噪声误触发/误判）：
            #  ① 存在实际装饰性对白间隙（build_gap_feedback 非空，最可靠——直接检测到装饰）
            #  ② gap.clean < 0.82（对白间隙装饰率高）
            #  ③ 有 high 级 findings（审阅器抓到的明确问题）
            #  ④ 融合分 < 0.65（两个信号都差）
            if not _high and not _gap_fb and _gap_clean >= _GAP_CLEAN_THRESH \
                    and _fused >= _AI_FLAVOR_DIRTY_THRESH:
                break  # 干净
            _fb_parts = []
            _llm_fb = build_ai_flavor_feedback(_high if _high else _findings)
            if _llm_fb:
                _fb_parts.append(_llm_fb)
            if _gap_fb:
                _fb_parts.append(_gap_fb)
            # 【v5.27.4】原文逐字复现：审阅 fix 里嵌的原文写法 → 转述代替逐字的顽固问题
            _verbatim_fb = build_verbatim_feedback(_findings)
            if _verbatim_fb:
                _fb_parts.append(_verbatim_fb)
            if not _fb_parts:
                break
            _fb = "\n\n".join(_fb_parts)
            user_input = user_input.rstrip() + "\n\n" + _fb
            try:
                # 修正重生成温度略调高（顽固章换措辞），其余参数沿用
                _regen_params = dict(eff_gen_params)
                _regen_params["temperature"] = min(
                    0.5, (_regen_params.get("temperature", 0.3) or 0.3) + 0.05)
                generated = await forward_generation_v4(
                    user_input, gen_params=_regen_params, system_prompt=prompt_text,
                )
            except Exception:
                break
            ai_flavor_retried += 1
            ai_flavor_res = await review_ai_flavor(generated, target_text)

    # 【v5.17】反退化闸门：L2 补写不得把 cohesion 打到 floor 以下（生产版 floor 选轮，
    # 保守只回退、不挑 best，保 keep-last 语义）。harness g13 实测管线措辞下 L2 补写把
    # 亲戚 cohesion 压到 0.097（短句碎片化）；织入措辞已救回（g14 0.479），此处再加兜底。
    REPRO_COHESION_FLOOR = 0.40
    from .scorer import narrative_cohesion
    if generated and _gen_before_l2 and generated != _gen_before_l2:
        _final_coh = narrative_cohesion(generated)
        if _final_coh < REPRO_COHESION_FLOOR:
            _pre_coh = narrative_cohesion(_gen_before_l2)
            if _pre_coh > _final_coh:
                generated = _gen_before_l2

    v_prime = extract_structural_vector(generated)
    target_vec = target_v.get("vector") if isinstance(target_v, dict) else None
    v_prime_vec = v_prime.get("vector") if isinstance(v_prime, dict) else None
    if not target_vec or not v_prime_vec:
        return {
            "score": 0.0, "v_cosine": 0.0, "char_similarity": 0.0,
            "s_char": 0.0,
            "length_alignment": 0.0, "serialization_fit": 0.0,
            "content_quality": 0.0, "factual_consistency": 0.0,
            "plot_fidelity": 0.0, "semantic_coverage": 0.0,
            "generated_text": generated, "v_prime": v_prime,
            "user_input": "",
            "repro_retried": repro_retried, "repro_gaps": repro_gaps,  # 【v5.14 L2b】
            "error": "Missing target vector or generated vector",
        }

    v_cos = compute_structural_similarity(target_vec, v_prime_vec)
    # char_similarity（编辑距离）仅展示用，不参与综合分；
    # 参与评分的 b8 是下面的 s_char_ngram（char_ngram_containment，v5.11）
    s_char = char_level_similarity(target_text or "", generated)

    # 计算逐维度偏差百分比
    deltas_pct = []
    for tv, gv in zip(target_vec, v_prime_vec):
        if abs(tv) > 1e-6:
            deltas_pct.append(abs(tv - gv) / abs(tv) * 100)
        else:
            deltas_pct.append(0.0)
    mean_abs_delta_pct = float(np.mean(deltas_pct)) if deltas_pct else 0.0

    # v4.0 蜕变版：5 项复合评分
    # 各项 0~1，越大越好
    l_score = length_alignment_score(generated, target_text)
    s_score = serialization_fit_score(generated)
    # 【v5.22 P2】semantic_coverage（embedding）与 plot_similarity（概括+embedding）
    # 相互独立 → create_task 提前并行，替代串行 await；CPU 评分先算，IO 并行跑。
    import asyncio as _asyncio5
    _sem_task = _asyncio5.create_task(semantic_coverage(generated, target_text))
    # 【v5.27.1】AI 味审阅已在生成后同轮修正循环里完成（ai_flavor_res），
    # 此处不再 create_task（避免重复审阅调用）。见上方同轮修正循环。
    flu = fluency_score(generated)
    cr = compression_ratio(generated)            # 信息密度
    nc = narrative_cohesion(generated)           # 叙事连贯
    fwkl = function_word_kl(generated, target_text)  # 风格距离

    # 对白质量 + 防御性写作（v5.6 量化检测：对抗"刻意松弛感"和"过度解释"）
    dq = dialogue_quality_score(generated)
    dw = defensive_writing_score(generated)
    # v5.6 补丁：装饰性发散 + 对话间隙冗余 + 结尾动作密度
    adv = ai_divergence_score(generated)
    dg = dialogue_gap_bloat(generated)
    ea = ending_action_density(generated)

    # 【v5.22 P2】评分开关 + plot_similarity 并行任务提前创建（IO 与上面 CPU 评分并行）
    from .config import SETTINGS as _SETTINGS
    _ps_task = None
    if _SETTINGS.scoring_v519:
        from .plot_similarity import plot_similarity
        _ps_task = _asyncio5.create_task(plot_similarity(
            generated, target_text,
            n_min=_SETTINGS.v519_n_min,
            n_max=_SETTINGS.v519_n_max,
        ))

    sem_cov = await _sem_task

    content_q = content_quality_score(
        semantic_coverage=sem_cov, fluency=flu,
        compression_ratio=cr, narrative_cohesion=nc, function_word_kl=fwkl,
        dialogue_quality=dq, defensive_writing=dw,
        ai_divergence=adv, dialogue_gap=dg, ending_action=ea,
    )

    # 【v5.10】事实一致性：候选对原文标志性细节的保留程度（人物/数字/对白/物品）。
    # 注入锚点后，模型"写没写对"这些细节现在是可量化的第 6 个评分维度。
    # 【v5.13 L2】kd 已在生成前算好（校验侧与评分侧共用），此处只复用。
    from .key_details import factual_consistency_score
    fc_result = factual_consistency_score(generated, target_text, key_details=kd)
    fc = fc_result["score"]

    # 【v5.11】b7 事件保真（确定性节拍匹配）+ b8 原文字符相似度（n-gram 包含率）。
    # 内容相似度家族（b6+b7+b8）合计 0.50，评分主导 = 复现原文。
    from .plot_fidelity import beat_sequence_fidelity
    from .scorer import char_ngram_containment
    pf_result = beat_sequence_fidelity(
        skeleton_text, generated, target_text=target_text,
    )  # 【v5.12】发明扣分对原文
    pf = pf_result["composite"]
    s_char_ngram = char_ngram_containment(generated, target_text)

    # 【v5.20】新评分（用户拍板 v5.19，harness 实测锁定）：
    # 综合 = 0.5×字符 + 0.3×剧情 + 0.2×句式。plot_sim = generated 句子级匹配
    # 原文剧情点（每 n 字分段概括 + embedding 最大余弦均值，plot_similarity 模块）。
    # b5-b8 各分量（sem_cov/pf/fc）继续计算，作诊断输出（响应保留），不参与综合分。
    # SCORING_V519=0 一键回退旧 b1-b8 路径。（_SETTINGS 已在上面评分块定义）
    plot_sim = 0.0
    plot_sim_coverage = 0.0
    turn_fidelity = 0.0
    turn_recall = 0.0
    n_target_turns = 0
    n_gen_turns = 0
    # 【v5.27.1】AI 味审阅结果（已在生成后同轮修正循环算好）：分数进综合分惩罚；
    # findings 供迭代反馈/透出。ai_flavor_res=None → 不惩罚（未审/审阅失败）。
    ai_flavor = None
    ai_flavor_findings: list = []
    ai_flavor_err = None
    ai_gap_decoration = None
    if ai_flavor_res is not None:
        try:
            ai_flavor = ai_flavor_res.get("score")
            ai_flavor_findings = ai_flavor_res.get("findings") or []
            ai_flavor_err = ai_flavor_res.get("error")
            ai_gap_decoration = ai_flavor_res.get("gap")
        except Exception as e:  # noqa: BLE001
            ai_flavor_err = str(e)
    if _SETTINGS.scoring_v519:
        _ps = await _ps_task if _ps_task is not None else {}
        plot_sim = _ps.get("plot", 0.0)
        plot_sim_coverage = _ps.get("coverage", 0.0)
        # 【v5.21】对白轮保真（诊断透出；plot 已含混合，这里单列供复盘）
        turn_fidelity = _ps.get("turn_fidelity", 0.0)
        turn_recall = _ps.get("turn_recall", 0.0)
        n_target_turns = _ps.get("n_target_turns", 0)
        n_gen_turns = _ps.get("n_gen_turns", 0)
        composite = compute_composite_score_v5(
            v_cos, mean_abs_delta_pct,
            dim_deltas_pct=deltas_pct,
            length_alignment=l_score,
            serialization_fit=s_score,
            content_quality=content_q,
            semantic_coverage=sem_cov,  # 诊断（不参与 v519 综合分）
            factual_consistency=fc,
            plot_fidelity=pf,
            s_char=s_char_ngram,
            plot_sim=plot_sim,
            ai_flavor=ai_flavor,  # 【v5.27】AI 味惩罚
            weights=scoring_weights,
        )
    else:
        composite = compute_composite_score_v5(
            v_cos, mean_abs_delta_pct,
            dim_deltas_pct=deltas_pct,
            length_alignment=l_score,
            serialization_fit=s_score,
            content_quality=content_q,
            semantic_coverage=sem_cov,  # 【v5.12】b5 语义覆盖（内容核心 0.85）
            factual_consistency=fc,
            plot_fidelity=pf,
            s_char=s_char_ngram,
            ai_flavor=ai_flavor,  # 【v5.27】AI 味惩罚
            weights=scoring_weights,
        )

    return {
        "score": composite,
        "v_cosine": v_cos,
        "char_similarity": s_char,
        "s_char": s_char_ngram,
        "plot_sim": plot_sim,                    # 【v5.20】剧情相似度（句子级最大余弦均值）
        "plot_sim_coverage": plot_sim_coverage,  # 【v5.20】剧情覆盖率@0.50（诊断）
        "turn_fidelity": turn_fidelity,          # 【v5.21】对白轮保真（缺失/乱序惩罚）
        "turn_recall": turn_recall,              # 【v5.21】对白轮召回率（诊断）
        "n_target_turns": n_target_turns,        # 【v5.21】原文对白轮数
        "n_gen_turns": n_gen_turns,              # 【v5.21】生成对白轮数
        "scoring": "v519" if _SETTINGS.scoring_v519 else "v5.18",  # 【v5.20】评分模式标记
        "ai_flavor": ai_flavor,                    # 【v5.27】AI 味分（1=干净，None=未审/失败）
        "ai_flavor_findings": ai_flavor_findings,  # 【v5.27】AI 味审阅明细（迭代反馈用）
        "ai_flavor_error": ai_flavor_err,          # 【v5.27】审阅失败原因（诊断）
        "ai_flavor_retried": ai_flavor_retried,    # 【v5.27.1】同轮 AI 味重生成次数（诊断）
        "ai_gap_decoration": ai_gap_decoration,    # 【v5.27】确定性对白间隙检测
        "semantic_coverage": sem_cov,  # 【v5.12】语义覆盖（b5）
        "length_alignment": l_score,
        "serialization_fit": s_score,
        "content_quality": content_q,
        "factual_consistency": fc,
        "factual_detail": fc_result,
        "plot_fidelity": pf,
        "plot_fidelity_detail": pf_result,
        "user_input": user_input,  # 【v5.12】完整生成指令（含剧情骨架）持久化供展示
        "anchor_verify": anchor_verify,      # 【v5.13 L2】锚点校验明细（missing/score/all_present）
        "anchors_retried": anchors_retried,  # 【v5.13 L2】锚点反馈重试次数
        "anchors_patched": anchors_patched,  # 【v5.13 L2】对白保守补丁插入的引号句
        "repro_retried": repro_retried,      # 【v5.14 L2b】复现反馈重试次数
        "repro_gaps": repro_gaps,            # 【v5.14 L2b】最终残留复现缺口（诊断）
        "target_len": target_len,            # 【v5.13 L3】目标长度（观测闸门长度比用）
        "compression_ratio": cr,
        "narrative_cohesion": nc,
        "function_word_kl": fwkl,
        "mean_abs_delta_pct": mean_abs_delta_pct,
        "dialogue_quality": dq,
        "defensive_writing": dw,
        "ai_divergence": adv,
        "dialogue_gap": dg,
        "ending_action": ea,
        "generated_text": generated,
        "v_prime": v_prime,
        "rendered_prompt": prompt_text,
        "gen_params": eff_gen_params,
    }


# ── v5.16 多章评估 + 跨章聚合 ──────────────────────────────

async def _evaluate_p_multi(
    p_vector: list[float],
    chapters: list[dict],
    batch_idxs: list[int],
    *,
    chapter_section: str | None,
    scoring_weights: dict[str, float] | None,
    mode: str,
    repro_ctx: dict | None,
    improvement=None,  # ImprovementContext（v5.16 文本梯度）
    repro_cfg: dict | None = None,  # 【v5.18】g17 l2_rounds 等
    baselines: dict[int, float] | None = None,  # 【v5.28】每章难度基线分（难度归一化）
) -> dict[str, Any]:
    """v5.16 多章评估：同一候选 P 在章批上逐章评估，跨章聚合各维均值。

    - 每章走 _evaluate_p（骨架/锚点/复现反馈全沿用单章逻辑，零重复实现）。
    - 聚合：score 及各维取均值 → 观测分数喂 BO；per_chapter 保留逐章明细，
      供文本梯度分析（improvement_context.analyze_deductions）用。
    - repro_ctx 共享：逐章的重试都从同一全程额度扣（总生成次数有界）。
    - C 开关：inject=user_input → 强调块注入；inject=skeleton → 强调重写进骨架。
    """
    # 【性能优化】batch 内章节并行评估：同一候选 P 在章批上逐章评估相互独立，
    # asyncio.gather 并发（batch 通常 2-4 章，生成 LLM + 评分并发 → wall-clock 明显缩短）。
    # repro_ctx 共享额度：并发下「remaining 扣减」为原子操作，总额度语义保持（略超 1 次可接受）。
    # 【v5.22 P2】并发信号量上限：终选全量复评 batch=10 章，若 10 路生成 LLM 全并发会
    # 打爆 doubao 限流（429 退避反而更慢）→ 限 4 路。主搜索 batch≤4 不受影响。
    import asyncio as _asyncio
    _EVAL_CONCURRENCY = 4
    _eval_sem = _asyncio.Semaphore(_EVAL_CONCURRENCY)

    async def _eval_one(ci: int) -> dict:
        async with _eval_sem:
            ch = chapters[ci]
            skel = ch["skeleton"]
            em_block = ""
            if improvement is not None:
                if improvement.inject == "skeleton":
                    skel = improvement.rewrite_skeleton(skel, ci)
                else:
                    em_block = improvement.effective_block(ci) or ""
            r = await _evaluate_p(
                p_vector, skeleton_text=skel, target_text=ch["text"],
                target_v=ch["v_target"], chapter_section=chapter_section,
                gen_params=ch["gen_params"], key_phrases=ch["key_phrases"],
                scoring_weights=scoring_weights, mode=mode, repro_ctx=repro_ctx,
                emphasis_block=em_block,
                repro_cfg=repro_cfg,  # 【v5.18】g17 l2_rounds 等
            )
            r["_chapter_idx"] = ci
            return r

    per_chapter = await _asyncio.gather(*[_eval_one(ci) for ci in batch_idxs])
    return _aggregate_results(per_chapter, baselines=baselines)


def _aggregate_results(per_chapter: list[dict], *, baselines: dict[int, float] | None = None) -> dict[str, Any]:
    """多章结果 → 候选观测：各维均值 + 代表字段 + per_chapter 明细。

    【v5.28】难度归一化：baselines 提供每章基线分（heuristic P 全章评估），
    计算 `score_adj` = 批内各章 (原始分 − 章基线) 均值。旋转章批的批内分被
    章难度主导（[8,9]批 0.56 vs [0,1]批 0.78），不可比、让 BO 学到噪声；
    score_adj 消除章难度偏差 → BO 优化"相对该章难度的提升"，目标平稳。
    score/各维保持原始均值（展示/门控/终选排序用）。
    """
    _dims = [
        "score", "v_cosine", "char_similarity", "s_char", "plot_sim",  # 【v5.20】plot_sim 多章聚合
        "turn_fidelity", "turn_recall",  # 【v5.21】对白轮保真多章聚合
        "ai_flavor",  # 【v5.27】AI 味分多章聚合
        "semantic_coverage",
        "length_alignment", "serialization_fit", "content_quality",
        "factual_consistency", "plot_fidelity", "mean_abs_delta_pct",
        "compression_ratio", "narrative_cohesion", "function_word_kl",
        "dialogue_quality", "defensive_writing", "ai_divergence",
        "dialogue_gap", "ending_action",
    ]
    agg: dict[str, Any] = {}
    for d in _dims:
        vals = [r.get(d) for r in per_chapter if isinstance(r.get(d), (int, float))]
        agg[d] = round(sum(vals) / len(vals), 4) if vals else 0.0
    # 【v5.28】难度归一化分 score_adj（仅内部：BO 观察 + top-K 选择用）
    if baselines:
        _adj: list[float] = []
        for _r in per_chapter:
            _ci = _r.get("_chapter_idx")
            _b = baselines.get(_ci) if _ci is not None else None
            _s = _r.get("score")
            if _b is not None and isinstance(_s, (int, float)):
                _adj.append(_s - _b)
        if _adj:
            agg["score_adj"] = round(sum(_adj) / len(_adj), 4)
    first = per_chapter[0] if per_chapter else {}
    for k in ("generated_text", "v_prime", "user_input", "rendered_prompt",
              "gen_params", "anchor_verify", "factual_detail",
              "plot_fidelity_detail", "repro_gaps", "error",
              "anchors_retried", "anchors_patched", "repro_retried"):
        agg[k] = first.get(k)
    # 【v5.16.1】顶层代表字段属于哪一章（前端逐章 chip 默认定位用）
    agg["rep_chapter_idx"] = first.get("_chapter_idx")
    _lens = [r.get("target_len") or 0 for r in per_chapter]
    agg["target_len"] = round(sum(_lens) / max(1, len(_lens)))
    agg["per_chapter"] = per_chapter
    agg["chapters_evaluated"] = [r["_chapter_idx"] for r in per_chapter]
    return agg


def _output_per_chapter(p: dict, pi: int) -> dict:
    """逐章输出明细（前端章节 chip 切换展示用）。

    【v5.16.1】补逐章 prompt/指令/正文。
    【多章显示修复】不再 [:2000] 截断 —— 原截断导致前端正文永远显示 2000 字且句子腰斩。
    rendered_prompt/user_input/sample_text 均存全文（示例书章节约 3k-8k 字，响应 ~0.6MB 可接受）。
    """
    return {
        "idx": p.get("_chapter_idx", pi),
        "score": round(p.get("score", 0), 4),
        "fc": round(p.get("factual_consistency", 0), 4),
        "pf": round(p.get("plot_fidelity", 0), 4),
        "s_char": round(p.get("s_char", 0), 4),
        "plot_sim": round(p.get("plot_sim", 0), 4),  # 【v5.20】剧情相似度
        "turn_fidelity": round(p.get("turn_fidelity", 0), 4),  # 【v5.21】对白轮保真
        "ai_flavor": round(p.get("ai_flavor", 0), 4) if isinstance(p.get("ai_flavor"), (int, float)) else None,  # 【v5.27】AI味
        "rendered_prompt": p.get("rendered_prompt") or "",
        "user_input": p.get("user_input") or "",
        "sample_text": p.get("generated_text") or "",
    }


# ── 子空间局部精炼（降维搜索的核心）────────────────────────

async def _subspace_refine(
    base_p: list[float],
    group_names: list[str],
    n_samples: int,
    *,
    skeleton_text: str,
    target_text: str,
    target_v: dict,
    chapter_section: str | None,
    gen_params: dict | None,
    noise_scale: float = 0.12,
    key_phrases: dict | None = None,
    scoring_weights: dict[str, float] | None = None,  # 【v4.0】
    mode: str = "quality",  # 【v5.13 L2】转发给 _evaluate_p 决定锚点重试上限
    repro_ctx: dict | None = None,  # 【v5.14 L2b】纯复现重试全程额度，转发给 _evaluate_p
    eval_candidate: Callable | None = None,  # 【v5.16】多章评估回调（未传 → 单章 _evaluate_p）
) -> list[dict]:
    """在指定维度组上进行局部扰动精炼。

    核心思路：47 维全空间 BO 只能做粗搜索（维度灾难），
    通过固定大部分维度、只扰动少数维度组，将有效搜索维度降到 5-12，
    从而让局部搜索更有效。

    Args:
        base_p: 基础 P 向量（通常取全空间 BO 的最好结果）
        group_names: 要精炼的组名列表（如 ["focus"]、["constraints", "style_rules"]）
        n_samples: 每组采样数
        noise_scale: 扰动幅度
    """
    assert group_names, "至少指定一个组"
    results: list[dict] = []

    # 收集所有要扰动的维度索引
    active_indices: list[int] = []
    for gname in group_names:
        if gname not in P_DIM_META["groups"]:
            continue
        g = P_DIM_META["groups"][gname]
        active_indices.extend(range(g["start"], g["end"]))

    if not active_indices:
        return []

    # 记录哪些维度属于 role 组（需要归一化）
    role_start = P_DIM_META["groups"]["role"]["start"]
    role_end = P_DIM_META["groups"]["role"]["end"]

    # 【v5.22 P2】n_samples 个扰动点相互独立 → 并行评估（gather + Semaphore 限流），
    # 替代串行 for 循环。组内并行、组间仍串行（调用方逐组 await，保观测渐进入集）。
    import asyncio as _asyncio4
    _REFINE_CONCURRENCY = 3
    _refine_sem = _asyncio4.Semaphore(_REFINE_CONCURRENCY)

    async def _sample_one(_: int) -> dict | None:
        async with _refine_sem:
            p = list(base_p)  # 深拷贝
            for idx in active_indices:
                p[idx] = np.clip(p[idx] + np.random.normal(0, noise_scale), 0.01, 0.99)

            # role 组归一化（保持 one-hot 近似）
            if role_start in active_indices or any(role_start <= i < role_end for i in active_indices):
                role_vals = p[role_start:role_end]
                role_sum = sum(role_vals)
                if role_sum > 0:
                    for i in range(role_end - role_start):
                        p[role_start + i] = (role_vals[i] / role_sum) * 0.9 + 0.02

            if eval_candidate is not None:
                result = await eval_candidate(p)
            else:
                result = await _evaluate_p(
                    p, skeleton_text=skeleton_text, target_text=target_text,
                    target_v=target_v,
                    chapter_section=chapter_section, gen_params=gen_params,
                    key_phrases=key_phrases, scoring_weights=scoring_weights,
                    mode=mode,  # 【v5.13 L2】
                    repro_ctx=repro_ctx,  # 【v5.14 L2b】
                )
            if result.get("v_prime") is not None:
                source_tag = f"refine_{'_'.join(group_names)}"
                return {**result, "p_vector": p, "source": source_tag}
            return None

    _refined = await _asyncio4.gather(*[_sample_one(_) for _ in range(n_samples)])
    results = [r for r in _refined if r is not None]
    return results


# ── V_target → P 启发式初始化（冷启动增强）─────────────────

def _heuristic_p_from_v(v_target: dict) -> list[float]:
    """从目标结构指纹 V(T) 启发式推导 P 向量初始值。

    核心思想：V 和 P 之间有可预测的映射关系。
    比如「V 对白密度高 → P 对白驱动应该高」。
    这给 BO 一个远比随机 LHS 更好的起点。

    Returns:
        47 维 P 向量，各维 ∈ [0, 1]（含 plot 7 维 + gen_params 5 维，默认 0.5 中性）
    """
    labels = v_target.get("labels", {})
    p = [0.5] * P_DIM_META["dim"]  # 中值初始化
    g = P_DIM_META["groups"]

    # ── avg_sentence_len → 短句偏好 + 约束 ──
    asl = labels.get("avg_sentence_len", 20)
    # 句长 < 15 → 强烈倾向于短句
    if asl < 15:
        p[g["style_rules"]["start"] + 0] = 0.85  # 使用短句
        p[g["constraints"]["start"] + 0] = 0.70  # 每段≤5句
    elif asl < 25:
        p[g["style_rules"]["start"] + 0] = 0.50  # 中性
    else:
        p[g["style_rules"]["start"] + 0] = 0.20  # 允许长句

    # ── sentence_len_variance → 句式多样 ──
    slv = labels.get("sentence_len_variance", 50)
    if slv > 80:
        p[g["style_rules"]["start"] + 2] = 0.80  # 句式多变
    elif slv > 40:
        p[g["style_rules"]["start"] + 2] = 0.55
    else:
        p[g["style_rules"]["start"] + 2] = 0.30

    # ── dialogue_density → 对白驱动 + 约束 ──
    dd = labels.get("dialogue_density", 0.3)
    if dd > 0.35:
        p[g["focus"]["start"] + 0] = 0.80       # 对白驱动
        p[g["constraints"]["start"] + 1] = 0.65  # 对白≥40%
        p[g["style_rules"]["start"] + 4] = 0.40  # 节奏明快（对白多通常节奏快）
    elif dd > 0.15:
        p[g["focus"]["start"] + 0] = 0.50
        p[g["constraints"]["start"] + 1] = 0.30
    else:
        p[g["focus"]["start"] + 0] = 0.20
        p[g["constraints"]["start"] + 1] = 0.10

    # ── special_punct_density → 标点克制 ──
    spd = labels.get("special_punct_density", 5)
    if spd > 10:
        p[g["constraints"]["start"] + 2] = 0.10  # 不克制感叹号（原文就在用）
        p[g["constraints"]["start"] + 4] = 0.10  # 不避免省略号
    elif spd > 3:
        p[g["constraints"]["start"] + 2] = 0.50
        p[g["constraints"]["start"] + 4] = 0.50
    else:
        p[g["constraints"]["start"] + 2] = 0.80  # 克制
        p[g["constraints"]["start"] + 4] = 0.80

    # ── lexical_richness → 用词多样化 ──
    lr = labels.get("lexical_richness", 0.4)
    if lr > 0.55:
        p[g["style_rules"]["start"] + 1] = 0.80  # 用词多样化
        p[g["constraints"]["start"] + 5] = 0.20  # 不用生僻词 ← 低（词汇丰富 ≠ 生僻）
    elif lr > 0.35:
        p[g["style_rules"]["start"] + 1] = 0.50
    else:
        p[g["style_rules"]["start"] + 1] = 0.25

    # ── paragraph_frequency → 叙事节奏 ──
    pf = labels.get("paragraph_frequency", 20)
    if pf > 30:
        # 分段频繁 → 节奏快、碎片化
        p[g["style_rules"]["start"] + 4] = 0.70  # 节奏明快
        p[g["style_rules"]["start"] + 6] = 0.60  # 多用动词
        p[g["constraints"]["start"] + 3] = 0.30  # 每段≥3句 ← 低
    elif pf > 15:
        p[g["style_rules"]["start"] + 4] = 0.50
    else:
        # 分段少 → 叙事从容
        p[g["style_rules"]["start"] + 3] = 0.70  # 叙述从容
        p[g["style_rules"]["start"] + 8] = 0.60  # 环境烘托

    # ── 新维度: modifier_density → 白描 vs 修饰 ──
    md = labels.get("modifier_density", 50)
    if md > 70:
        p[g["style_rules"]["start"] + 5] = 0.25  # 白描为主 ← 低（修饰多 → 不白描）
    elif md < 30:
        p[g["style_rules"]["start"] + 5] = 0.80  # 白描为主

    # ── modifier_density + commas → 叙述从容 vs 紧凑 ──
    cd = labels.get("comma_density", 30)
    if cd > 50:
        p[g["focus"]["start"] + 7] = 0.60  # 信息密度（高逗号=叙述性强）
        p[g["style_rules"]["start"] + 3] = 0.60  # 叙述从容
    elif cd < 20:
        p[g["focus"]["start"] + 1] = 0.60  # 短句节奏

    # ── sentences_per_paragraph → 约束 ──
    spp = labels.get("sentences_per_paragraph", 4)
    if spp > 6:
        p[g["constraints"]["start"] + 0] = max(p[g["constraints"]["start"] + 0], 0.60)  # 每段≤5句
    elif spp < 2:
        p[g["constraints"]["start"] + 3] = 0.60  # 每段≥3句

    # role 组：统一设为 0.2 + 最有匹配的稍高
    # 根据 lexical_richness + avg_sentence_len 判断文学性
    is_literary = lr > 0.5 and asl > 20
    is_webserial = lr < 0.45 and asl < 25
    for i in range(5):
        p[g["role"]["start"] + i] = 0.15
    if is_literary:
        p[g["role"]["start"] + 3] = 0.40  # 严肃文学
        p[g["role"]["start"] + 2] = 0.25
    elif is_webserial:
        p[g["role"]["start"] + 2] = 0.40  # 资深网文作者
        p[g["role"]["start"] + 0] = 0.25  # 玄幻
        p[g["role"]["start"] + 1] = 0.25
    else:
        p[g["role"]["start"] + 2] = 0.35
        p[g["role"]["start"] + 0] = 0.20
        p[g["role"]["start"] + 1] = 0.20

    # role 归一化
    role_sum = sum(p[g["role"]["start"]:g["role"]["end"]])
    if role_sum > 0:
        for i in range(5):
            p[g["role"]["start"] + i] /= role_sum

    return [max(0.02, min(0.98, v)) for v in p]


# ── 关键词/短语抽取（用于词汇级精炼）────────────────────────

_CHINESE_STOP_CHARS: set[str] = {
    "的", "了", "在", "是", "我", "有", "和", "就", "不", "人",
    "都", "一", "一个", "上", "也", "很", "到", "说", "要", "去",
    "你", "会", "着", "没有", "看", "好", "自己", "这", "他", "她",
    "它", "们", "那", "什么", "怎么", "因为", "所以", "但是",
    "然而", "不过", "虽然", "如果", "而且", "或者", "只是",
    "还是", "可是", "然后", "之后", "这时", "那时", "这个", "那个",
    "把", "被", "让", "给", "对", "从", "以", "与", "为", "于",
    "之", "而", "但", "又", "再", "才", "已", "还", "将", "便",
}


def _extract_key_phrases(text: str, top_n: int = 20) -> dict[str, Any]:
    """从目标文本抽取关键词/短语和风格用词特征。

    返回:
        {
            "top_words": [("词", 频率), ...],         # 最常用的实词
            "top_bigrams": [("短语", 频率), ...],      # 最常用的二字组合
            "sentence_starters": [("开头词", 次数), ...],  # 句首高频词
            "distinctive_markers": [标记, ...],         # 风格标记（语气词/连接词）
            "func_word_stats": {"的/地/得": 比例},       # 功能词统计
        }
    """
    import re
    from collections import Counter

    # 分句
    sentences = re.split(r'[。！？\n.!?]', text)
    sentences = [s.strip() for s in sentences if s.strip()]

    # 分词（简单按字符拆+常用词组合，无需 jieba）
    all_chars = list(text)

    # 1. 实词频率：跳过停用字，统计单字频率
    word_counter: Counter[str] = Counter()
    for ch in all_chars:
        if ch.strip() and ch not in _CHINESE_STOP_CHARS and '一' <= ch <= '鿿':
            word_counter[ch] += 1
    top_words = word_counter.most_common(top_n)

    # 2. 二字短语（bigram）
    bigram_counter: Counter[str] = Counter()
    for i in range(len(text) - 1):
        bigram = text[i:i+2]
        if all('一' <= c <= '鿿' for c in bigram):
            bigram_counter[bigram] += 1
    # 过滤掉含停用字的 bigram
    filtered_bigrams = [(b, c) for b, c in bigram_counter.most_common(top_n * 3)
                        if b[0] not in _CHINESE_STOP_CHARS or b[1] not in _CHINESE_STOP_CHARS]
    top_bigrams = filtered_bigrams[:top_n]

    # 3. 句首高频词（取句首 1-2 字）
    starter_counter: Counter[str] = Counter()
    for s in sentences:
        if len(s) >= 1:
            starter_counter[s[0]] += 1  # 首字
        if len(s) >= 2:
            starter_counter[s[:2]] += 1  # 首二字
    top_starters = starter_counter.most_common(10)

    # 4. 风格标记（语气词/连接词）
    markers = ["呢", "吗", "吧", "啊", "哦", "嗯", "啦", "哟",
               "却", "但", "可", "便", "就", "才", "也", "又", "再",
               "忽然", "突然", "仿佛", "好像", "似乎", "原来", "只见", "却说"]
    found_markers: list[str] = []
    for m in markers:
        if m in text:
            count = text.count(m)
            if count >= 2:
                found_markers.append(f"{m}×{count}")

    # 5. 功能词统计
    total_chars = len([c for c in text if '一' <= c <= '鿿'])
    de_count = text.count("的")
    di_count = text.count("地")
    de_count2 = text.count("得")
    le_count = text.count("了")
    zhe_count = text.count("着")
    guo_count = text.count("过")

    func_word_stats: dict[str, float] = {
        "的/千字": round(de_count / max(total_chars, 1) * 1000, 1),
        "地/千字": round(di_count / max(total_chars, 1) * 1000, 1),
        "得/千字": round(de_count2 / max(total_chars, 1) * 1000, 1),
        "了/千字": round(le_count / max(total_chars, 1) * 1000, 1),
        "着/千字": round(zhe_count / max(total_chars, 1) * 1000, 1),
        "过/千字": round(guo_count / max(total_chars, 1) * 1000, 1),
    }

    # 6. 代表性示例句（选取长度适中、内容典型的 2-3 句）
    example_sentences: list[str] = []
    # 按句长排序，选长度居中的句子（太短无信息，太长占 prompt 空间）
    sorted_sents = sorted(sentences, key=len)
    mid = len(sorted_sents) // 2
    candidates = sorted_sents[max(0, mid-3):mid+3]
    # 优先选含高频率实词的句子
    top_word_set = set(w for w, _ in top_words[:10])
    scored_sents = sorted(
        candidates,
        key=lambda s: sum(1 for w in s if w in top_word_set),
        reverse=True,
    )
    example_sentences = [s.strip() for s in scored_sents[:3] if len(s.strip()) >= 10]

    return {
        "top_words": top_words[:15],
        "top_bigrams": top_bigrams[:10],
        "sentence_starters": top_starters,
        "distinctive_markers": found_markers[:8],
        "func_word_stats": func_word_stats,
        "example_sentences": example_sentences,
    }


def _balance_anchors(anchors: list[str], n: int) -> list[str]:
    """【v5.18】对白/叙述平衡（g17）：锚句含对白引号（「」或“”）的算对白，否则算叙述。

    当前 MMR 选的锚句几乎全是对白（对白密集的章），但 s_char 的散文部分
    更需要叙述锚句。平衡后按 对白:叙述 ≈ 1:1 分组拼接（保留组内 MMR 顺序）。
    """
    def _is_dlg(s: str) -> bool:
        return any(q in s for q in ("「", "“"))
    dlg = [s for s in anchors if _is_dlg(s)]
    nar = [s for s in anchors if not _is_dlg(s)]
    if not nar or not dlg:
        return anchors[:n]
    half_d = (n + 1) // 2
    half_n = n - half_d
    pick = dlg[:half_d] + nar[:half_n]
    return pick[:n]


# 【v5.22 P2d】anchor persistent cache: MMR selection is deterministic for same text.
# Skips the whole embeddings call chain (10 chapters x ~140 anchors re-embedded every
# run). Empty result is NOT cached so 429 failures cannot lock in empty anchors.
_ANCHOR_CACHE_VERSION = "v522"
_ANCHOR_CACHE_FILE: Any = None
_ANCHOR_CACHE: dict | None = None


def _load_anchor_cache() -> dict:
    global _ANCHOR_CACHE, _ANCHOR_CACHE_FILE
    if _ANCHOR_CACHE is None:
        import json
        from pathlib import Path

        if _ANCHOR_CACHE_FILE is None:
            _ANCHOR_CACHE_FILE = (
                Path(__file__).resolve().parent.parent / "anchor_cache.json"
            )
        try:
            _ANCHOR_CACHE = json.loads(_ANCHOR_CACHE_FILE.read_text(encoding="utf-8"))
        except Exception:
            _ANCHOR_CACHE = {}
    return _ANCHOR_CACHE


def _save_anchor_cache() -> None:
    import json

    try:
        _ANCHOR_CACHE_FILE.write_text(
            json.dumps(_ANCHOR_CACHE, ensure_ascii=False), encoding="utf-8"
        )
    except Exception:
        pass


async def _extract_semantic_anchor_sentences(text: str, top_n: int = 5) -> list[str]:
    """【v5.13 ④ 语义化】embedding 选原文代表性句子。

    句向量与全文质心（句向量均值）的余弦相似度 + MMR 多样性挑选 top_n，
    让生成 prompt 直接携带原文句子（可原样复现 → 抬升 s_char）。

    embedding 失败 / 向量不足 → 返回空列表不阻塞（降级为纯统计现状）。
    """
    import hashlib
    import re
    from .embed_client import get_embeddings

    # 【v5.22 P2d】cache hit -> reuse selected anchors (deterministic MMR).
    _akey = f"{_ANCHOR_CACHE_VERSION}|{top_n}|" \
            f"{hashlib.md5(text.encode('utf-8')).hexdigest()}"
    _cached_anchors = _load_anchor_cache().get(_akey)
    if _cached_anchors:
        return list(_cached_anchors)

    sentences = [s.strip() for s in re.split(r"[。！？\n.!?]", text) if s.strip()]
    # 过滤：长度适中（太短无信息，太长占 prompt 空间）
    cands = [s for s in sentences if 8 <= len(s) <= 60]
    if len(cands) < 2:
        return cands[:top_n]

    emb = await get_embeddings(cands)
    vecs = [(s, v) for s, v in zip(cands, emb) if v is not None]
    if len(vecs) < 2:
        return [s for s, _ in vecs][:top_n]

    import numpy as _np
    centroid = _np.mean(_np.array([v for _, v in vecs], dtype=_np.float32), axis=0)
    lmbda = 0.7  # 相关性权重；(1-lmbda)=多样性

    def _cos(a, b):
        na = _np.linalg.norm(a)
        nb = _np.linalg.norm(b)
        if na == 0 or nb == 0:
            return 0.0
        return float(_np.dot(a, b) / (na * nb))

    selected: list[tuple[str, object]] = []
    pool = list(vecs)
    while len(selected) < top_n and pool:
        best_idx = 0
        best_score = -1.0
        for i, (_s, v) in enumerate(pool):
            rel = _cos(v, centroid)
            div = max((_cos(v, sv) for _ss, sv in selected), default=0.0)
            score = lmbda * rel - (1.0 - lmbda) * div
            if score > best_score:
                best_score = score
                best_idx = i
        s, v = pool.pop(best_idx)
        selected.append((s, v))

    _result = [s for s, _ in selected]
    if _result:
        try:
            _load_anchor_cache()[_akey] = _result
            _save_anchor_cache()
        except Exception:
            pass
    return _result


# ── 基于结构+词汇差异的 Prompt 精炼（v5.3+）───────────────

_REFINE_PROMPT_TEMPLATE = """你是一个 Prompt 优化专家。当前的 system prompt 生成文本与目标文本存在以下偏差：

当前 Prompt：
```
{current_prompt}
```

【结构偏差】（绝对值 > 15% 的维度）：
{delta_report}

{lexical_report}

目标文本前 200 字：
{target_preview}

生成文本前 200 字：
{gen_preview}

请修改当前 Prompt，使新的 Prompt 能更好地匹配目标文本。
要求：
1. 保留当前 Prompt 中效果好的部分
2. 针对偏差项逐条修改 Prompt
3. 特别关注词汇/用词偏差：在 Prompt 中增加具体的用词指导
4. 修改后的 Prompt 必须仍然清晰、可执行
5. 输出 JSON：{{"refined_prompt": "修改后的完整 prompt 文本", "change_summary": "简要说明改了哪些部分"}}"""


async def _refine_with_diff(
    best_result: dict,
    target_text: str,
    *,
    skeleton_text: str,
    target_v: dict,
    chapter_section: str | None,
    gen_params: dict | None,
    key_phrases: dict | None = None,
    scoring_weights: dict[str, float] | None = None,  # 【v5.10】修复：此前签名缺此参数
) -> list[dict]:
    """基于结构差异对最佳候选的 prompt 进行精炼。

    与 _subspace_refine 不同，这里直接对 prompt 文本做语义级修改
    （而非对 P 向量做数值扰动），可以更精确地修正具体偏差。
    """
    from .llm_client import chat_json, DISABLE_THINKING
    from .structural_analyzer import compute_dimension_deltas
    from .scorer import char_level_similarity

    current_prompt = best_result.get("rendered_prompt", "")
    generated = best_result.get("generated_text", "")
    v_prime = best_result.get("v_prime")

    if not current_prompt or not generated or not v_prime:
        return []

    # 1. 计算结构偏差
    deltas = compute_dimension_deltas(target_v, v_prime)
    delta_map = deltas.get("deltas", {})

    # 只保留偏差 > 15% 的维度
    significant_deltas = [
        (field, d) for field, d in delta_map.items()
        if isinstance(d, dict) and abs(d.get("delta_pct", 0)) > 15
    ]
    if not significant_deltas:
        return []  # 偏差不大，不需要精炼

    delta_lines = []
    for field, d in significant_deltas[:6]:  # 最多 6 条
        target_val = d.get("target", "?")
        gen_val = d.get("generated", "?")
        delta_pct = d.get("delta_pct", 0)
        direction = "偏高" if delta_pct > 0 else "偏低"
        delta_lines.append(
            f"  · {field}: 目标={target_val}, 生成={gen_val}, "
            f"偏差={delta_pct:+.1f}%（{direction}）"
        )
    delta_report = "\n".join(delta_lines)

    target_preview = target_text[:200]
    gen_preview = generated[:200]

    # 1b. 计算词汇差异
    target_kp = key_phrases if key_phrases else _extract_key_phrases(target_text)
    gen_kp = _extract_key_phrases(generated)

    lexical_lines: list[str] = []
    # 功能词对比
    tw = target_kp["func_word_stats"]
    gw = gen_kp["func_word_stats"]
    func_diffs = []
    for key in tw:
        tv = tw[key]
        gv = gw.get(key, 0)
        if abs(tv - gv) > 5:  # 偏差 > 5‰
            func_diffs.append(f"{key}: 目标={tv}‰, 生成={gv}‰")
    if func_diffs:
        lexical_lines.append("【功能词偏差】（/千字）")
        lexical_lines.extend(f"  · {d}" for d in func_diffs)

    # 句首词对比
    t_starters = set(w for w, _ in target_kp["sentence_starters"][:5])
    g_starters = set(w for w, _ in gen_kp["sentence_starters"][:5])
    missing_starters = t_starters - g_starters
    if missing_starters:
        lexical_lines.append(f"【句首词】目标常用但生成中未出现：{', '.join(sorted(missing_starters)[:5])}")

    # 风格标记词对比
    t_markers = set(target_kp["distinctive_markers"])
    t_marker_words = set(m.split("×")[0] for m in t_markers)
    g_marker_words = set(m.split("×")[0] for m in gen_kp.get("distinctive_markers", []))
    missing_markers = t_marker_words - g_marker_words
    if missing_markers:
        lexical_lines.append(f"【语气/连接词】目标使用但生成缺少：{', '.join(sorted(missing_markers)[:5])}")

    # 高频实词对比
    t_top_words = set(w for w, _ in target_kp["top_words"][:10])
    g_top_words = set(w for w, _ in gen_kp["top_words"][:10])
    missing_words = t_top_words - g_top_words
    if missing_words:
        lexical_lines.append(f"【高频字】目标使用但生成缺少：{', '.join(sorted(missing_words)[:10])}")

    lexical_report = "\n".join(lexical_lines) if lexical_lines else "【用词】无明显偏差。"

    user = _REFINE_PROMPT_TEMPLATE.format(
        current_prompt=current_prompt,
        delta_report=delta_report,
        lexical_report=lexical_report,
        target_preview=target_preview,
        gen_preview=gen_preview,
    )

    # 2. 调用 LLM 精炼
    try:
        resp = await chat_json(
            system="你是 Prompt 优化专家，请基于数据精确修改。只输出 JSON。",
            user=user,
            temperature=0.4,
            top_p=0.7,
            extra_body=DISABLE_THINKING,
        )
        if resp["error"]:
            return []
        data = resp.get("data", {})
        refined_prompt = (data.get("refined_prompt") or "").strip()
        if not refined_prompt or len(refined_prompt) < 50:
            return []

        # 3. 评估精炼后的 prompt
        from .optimizer import forward_generation_v4
        from .structural_analyzer import (
            extract_structural_vector, compute_structural_similarity,
            compute_composite_score,
        )
        from .plot_skeleton import skeleton_to_generation_prompt
        from .key_details import get_key_details, factual_consistency_score

        # 【v5.10】精炼后的 prompt 重新生成时同样注入关键细节锚点，
        # 保证"改 prompt"和"测 prompt"用同一批细节约束。
        new_user_input = skeleton_to_generation_prompt(
            skeleton_text, chapter_section=chapter_section,
            key_details_block=_cached_anchor_block(target_text),
        )
        new_gen = await forward_generation_v4(
            new_user_input,
            gen_params=gen_params or {},
            system_prompt=refined_prompt,
        )
        if not new_gen or len(new_gen) < 100:
            return []

        new_v = extract_structural_vector(new_gen)
        new_v_cos = compute_structural_similarity(
            target_v["vector"], new_v["vector"]
        )
        new_s_char = char_level_similarity(target_text or "", new_gen)

        new_deltas_pct = []
        for tv, gv in zip(target_v["vector"], new_v["vector"]):
            if abs(tv) > 1e-6:
                new_deltas_pct.append(abs(tv - gv) / abs(tv) * 100)
            else:
                new_deltas_pct.append(0.0)
        new_mean_delta = float(np.mean(new_deltas_pct)) if new_deltas_pct else 0.0
        # 【v5.10】事实一致性也参与精炼评估，并用与主循环一致的权重
        new_fc = factual_consistency_score(
            new_gen, target_text, key_details=get_key_details(target_text),
        )["score"]
        # 【v5.11】精炼评估同步 b7 事件保真 + b8 原文字符相似度（与主循环一致）
        from .plot_fidelity import beat_sequence_fidelity
        from .scorer import char_ngram_containment, semantic_coverage
        new_pf = beat_sequence_fidelity(
            skeleton_text, new_gen, target_text=target_text,
        )["composite"]  # 【v5.12】发明扣分对原文
        new_s_char_ngram = char_ngram_containment(new_gen, target_text)
        # 【v5.12】精炼评估补语义覆盖（此前 b5 恒等于 1.0，是隐藏躺分）
        new_sem_cov = await semantic_coverage(new_gen, target_text)
        new_score = compute_composite_score(
            new_v_cos, new_mean_delta,
            dim_deltas_pct=new_deltas_pct,
            semantic_coverage=new_sem_cov,  # 【v5.12】b5 语义覆盖
            factual_consistency=new_fc,
            plot_fidelity=new_pf,
            s_char=new_s_char_ngram,
            weights=scoring_weights,
        )

        # 4. 更新最佳分
        best_score = best_result.get("score", 0)
        if new_score <= best_score:
            return []  # 没有改进

        return [{
            "p_vector": best_result.get("p_vector", []),
            "rendered_prompt": refined_prompt,
            "score": new_score,
            "v_cosine": new_v_cos,
            "char_similarity": new_s_char,
            "s_char": new_s_char_ngram,
            "mean_abs_delta_pct": new_mean_delta,
            "factual_consistency": new_fc,
            "plot_fidelity": new_pf,
            "semantic_coverage": new_sem_cov,  # 【v5.12】语义覆盖
            "generated_text": new_gen,
            "v_prime": new_v,
            "user_input": new_user_input,  # 【v5.12】完整生成指令
            "source": "refined_with_diff",
        }]
    except Exception:
        return []


# ── 逆向推理主流程 ────────────────────────────────────────


def _json_default(o: Any) -> Any:
    """json.dump 的 default：numpy 标量/数组 → python 原生类型。"""
    for method in ("item", "tolist"):
        fn = getattr(o, method, None)
        if callable(fn):
            try:
                return fn()
            except (TypeError, ValueError):
                pass
    return str(o)


def _save_reverse_infer_response(
    result: dict[str, Any],
    *,
    all_results: list[dict[str, Any]],
    source_file: str,
    chapter_section: str | None,
    target_text: str,
    target_texts: list[str] | None = None,  # 【v5.16】多章参考原文
) -> str:
    """把 reverse-infer 完整响应落盘到 logs/runs/，返回保存路径。

    历史问题：reverse_infer 在 v5.8 之前从不落盘完整响应，
    每次运行的结果只在浏览器展示，事后无法复盘/对比。
    这里在 return 前把候选全文、原文、骨架、评分、优化曲线一并写盘，
    文件名带时间戳，多次运行互不覆盖。
    落盘失败不抛异常（由调用方 try 包裹，不影响主流程）。
    """
    import datetime as _dt
    import json
    from pathlib import Path as _P

    log_dir = _P(__file__).resolve().parent.parent / "logs" / "runs"
    log_dir.mkdir(parents=True, exist_ok=True)

    # 文件名：时间戳 + 来源文档（清洗到字母数字，保留中文/._-）
    ts = _dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_src = "".join(
        c for c in (source_file or "unknown")
        if c.isalnum() or c in "._-"
    )[:20] or "unknown"
    # 【v5.16】多章文件名带章数（如 _x10章），便于区分单/多章 run
    if target_texts and len(target_texts) > 1:
        safe_src = f"{safe_src}_x{len(target_texts)}章"
    path = log_dir / f"reverse_infer_{ts}_{safe_src}.json"

    # 候选补全全文（API 返回的 sample_text 被截断到 5000 字，这里存完整正文）
    full_candidates = []
    for c in result.get("candidates", []):
        item = dict(c)
        full_text = ""
        for r in all_results:
            if r.get("p_vector") == c.get("p_vector"):
                full_text = r.get("generated_text") or ""
                break
        item["full_text"] = full_text
        full_candidates.append(item)

    payload = {
        "saved_at": _dt.datetime.now().isoformat(timespec="seconds"),
        "source_file": source_file,
        "chapter_section": chapter_section,
        "target_text": target_text,          # 参考原文（ground truth，单章）
        "target_texts": target_texts,        # 【v5.16】多章参考原文（None = 单章）
        "skeleton_text": result.get("skeleton_text", ""),
        "v_target": result.get("v_target"),
        "candidates": full_candidates,        # 含 full_text 完整正文
        "optimization": result.get("optimization"),
        "vp_correlation_hint": result.get("vp_correlation_hint"),
        "gate_rejects": result.get("gate_rejects", []),      # 【v5.13 L3】闸门拒绝的观测
        "skeleton_l1": result.get("skeleton_l1"),            # 【v5.13 L1】骨架 L1 元数据
    }

    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2, default=_json_default)

    return str(path)


async def _try_observe(
    opt,
    p,
    result,
    *,
    all_results: list[dict],
    gate_rejects: list[dict],
    source_tag: str,
    mode: str,
    info: dict | None = None,
    strategy=None,
    auto_selector=None,
    strategy_name: str | None = None,
    optimizer_info: dict | None = None,
) -> bool:
    """【v5.13 L3】观测集入库闸门：过 gate_observation 才 observe，否则记 gate_rejects。

    - `v_prime` 无效（生成失败）→ 不入库也不记 reject（是生成失败不是漂移），返回 False。
    - **观测 <3 强制放行**（防饿死 BO）：BO 观测用 opt.n_observations()，
      opt=None 的站点（subspace_refine / diff_refined）不享受豁免，始终走闸门。
    - 入库三分支（auto_selector / strategy / opt），champion 只在闸门通过后更新。
    - opt=None 时只进 all_results、不进 BO（subspace/diff 候选不污染 BO 观测集）。

    Returns:
        True = 已入库；False = 未入库（生成失败或闸门拒绝）。
    """
    from .anchor_control import gate_observation

    if result.get("v_prime") is None:
        return False

    if opt is not None and opt.n_observations() < 3:
        passed, reason = True, "force_allow_starve"
    else:
        passed, reason = gate_observation(
            result, mode=mode, target_len=result.get("target_len") or 0,
        )

    if not passed:
        _gen = result.get("generated_text") or ""
        gate_rejects.append({
            "source": source_tag,
            "p_vector": p,
            "reason": reason,
            "score": round(result.get("score", 0), 4),
            "fc": round(result.get("factual_consistency", 0), 4),
            "pf": round(result.get("plot_fidelity", 0), 4),
            "s_char": round(result.get("s_char", 0), 4),
            "len_ratio": round(len(_gen) / max(1, result.get("target_len") or 1), 3),
        })
        return False

    # 入库三分支（champion 只在闸门通过后更新）
    # 【v5.28】BO 观察 score_adj（难度归一化分）；无归一化时回退原始分。
    _obs_score = result.get("score_adj") if isinstance(result.get("score_adj"), (int, float)) else result["score"]
    if opt is not None:
        if auto_selector is not None and strategy is not None and strategy_name:
            strategy.observe(p, _obs_score)
            if optimizer_info is not None:
                champion = auto_selector.get_champion_name()
                if champion:
                    optimizer_info["champion"] = champion
        elif strategy is not None:
            strategy.observe(p, _obs_score)
        else:
            opt.observe(p, _obs_score)

    entry = {**result, "p_vector": p, "source": source_tag}
    if info is not None and isinstance(info, dict) and "ei" in info:
        entry["ei"] = info.get("ei", 0)
    all_results.append(entry)
    return True


async def reverse_infer(
    target_text: str,
    *,
    mode: str = "quality",       # "fast" | "standard" | "quality"
    source_file: str = "",
    chapter_section: str | None = None,
    skeleton_text: str | None = None,
    include_experience_hits: bool = True,
    gen_params: dict | None = None,
    store=None,  # ExperienceStore 实例，可选
    optimization_strategy: str | None = None,  # 【v4.0】"auto" / "bo-gp-ei" / "random" / "bo-tpe" / "cma-es" / "evolutionary"
    target_texts: list[str] | None = None,  # 【v5.16】多章参考原文（≥2 章 → 旋转章批 + 全量终选）
    improvement_config: dict | None = None,  # 【v5.16】文本梯度 A/B/C 开关（见 improvement_context）
    repro_config: dict | None = None,  # 【v5.18】复现链条 g17 开关（anchor_sents_n/anchor_balanced/skeleton_opt/l2_rounds/l2_weave）
) -> dict[str, Any]:
    """Prompt 逆向推理主流程。

    1. V_target = extract_structural_vector(target_text)
    2. 若 skeleton 未提供 → 调用 extract_plot_skeleton
    3. 初始化观测集：
       a. KNN 先验：经验库 top-3 → 正向验证（需 store）
       b. LHS 拉丁超立方采 4~6 个 P → 正向验证
    4. 贝叶斯循环：GP + EI，剩余轮数
    5. 后处理：去重 → 综合分排序 → top-3（多样性过滤）
    6. V-P 相关增量

    Args:
        target_text: 目标原文
        mode: fast=8次, standard=15次, quality=25次
        source_file: 来源文档名
        chapter_section: opening / main
        skeleton_text: 情节骨架（不传则自动提取）
        include_experience_hits: 是否用经验库做冷启动
        gen_params: 生成参数
        store: ExperienceStore 实例（可选）

    Returns:
        {
            v_target: dict,
            candidates: [{p_vector, rendered_prompt, score, v_cosine, char_similarity, sample_text}, ...],
            optimization: {mode, total_iterations, best_score, curve, method},
            vp_correlation_hint: [{v_dim, p_dim, r}, ...],
        }
    """
    from .structural_analyzer import extract_structural_vector
    from .bayesian_opt import PromptBayesianOptimizer

    # 模式配置
    mode_config = {
        "fast":     {"total": 8,  "knn": 1, "lhs": 3},
        "standard": {"total": 15, "knn": 2, "lhs": 5},
        "quality":  {"total": 25, "knn": 3, "lhs": 6},
    }
    cfg = mode_config.get(mode, mode_config["quality"])
    total_iter = cfg["total"]
    n_knn = cfg["knn"] if include_experience_hits and store else 0
    n_lhs = cfg["lhs"]

    # 【v5.16】章池归一化：target_texts 提供 → 多章模式；否则单章（兼容旧调用）。
    raw_texts = [target_text] if not target_texts else list(target_texts)
    multi = len(raw_texts) > 1

    # 1. 预提取每章产物：V_target / 骨架(L1) / 关键词 / 生成参数。
    #    摊销在 run 级（一次性成本）；单章路径行为与 v5.15 完全一致。
    from .optimizer import _calc_max_tokens
    from .plot_skeleton import extract_plot_skeleton

    # 【v5.18】复现链条 g17 配置（repro_config 解参，缺省即 g17 档位）：
    #   锚句 70 平衡 / 生成导向骨架 / L2 ×2 织入。None → 默认 g17（向后兼容旧调用）。
    _rc = repro_config or {}
    _anchor_n = int(_rc.get("anchor_sents_n", 70))
    _anchor_bal = bool(_rc.get("anchor_balanced", True))
    _skel_opt = bool(_rc.get("skeleton_opt", True))
    _l2_rounds = int(_rc.get("l2_rounds", 2))
    _l2_weave = bool(_rc.get("l2_weave", True))

    # 【性能优化】章节初始化并行：每章 V/锚句/骨架相互独立，asyncio.gather 并发。
    # 骨架 LLM + 锚句 embedding 是主要 IO；信号量限流 3（骨架并发防 API 限流，
    # 锚句 embedding 自身已批间并发）。gather 保序，行为与原串行完全一致。
    import asyncio as _asyncio
    _INIT_CONCURRENCY = 3
    _init_sem = _asyncio.Semaphore(_INIT_CONCURRENCY)

    async def _prepare_chapter(_i: int, _t: str) -> dict:
        async with _init_sem:
            _v = extract_structural_vector(_t)
            _gp = dict(gen_params or {})
            if "max_tokens" not in _gp or _gp.get("max_tokens", 0) <= 0:
                _gp["max_tokens"] = _calc_max_tokens(_t)
            _kp = _extract_key_phrases(_t)
            try:
                # 【v5.18】g17：锚句数 _anchor_n；平衡模式先取 2×，再对白/叙述 1:1 挑。
                _raw_anchor_n = _anchor_n * 2 if _anchor_bal else _anchor_n
                _anchors = await _extract_semantic_anchor_sentences(_t, top_n=_raw_anchor_n)
                if _anchor_bal and len(_anchors) > _anchor_n:
                    _anchors = _balance_anchors(_anchors, _anchor_n)
                _kp["semantic_anchor_sentences"] = _anchors[: _anchor_n or None]
            except Exception:
                _kp["semantic_anchor_sentences"] = []
            _skel = skeleton_text if (not multi and _i == 0 and skeleton_text) else None
            if _skel is None:
                _skr = await extract_plot_skeleton(_t, level="chapter", opt=_skel_opt)
                _skel = _skr["skeleton_text"]
                _l1 = {"repair": _skr.get("anchor_repair"), "semantic_gate": _skr.get("semantic_gate")}
            else:
                from .anchor_control import repair_skeleton_anchors, skeleton_semantic_gate
                _lr = await repair_skeleton_anchors(_skel, _t, llm_repair=True)
                _skel = _lr["skeleton_text"]
                _lg = await skeleton_semantic_gate(_skel, _t)
                _l1 = {"repair": _lr, "semantic_gate": _lg}
            return {
                "idx": _i, "text": _t, "v_target": _v, "skeleton": _skel,
                "skeleton_l1": _l1, "key_phrases": _kp, "gen_params": _gp,
            }

    chapters = await _asyncio.gather(
        *[_prepare_chapter(_i, _t) for _i, _t in enumerate(raw_texts)]
    )

    v_target = chapters[0]["v_target"]              # 返回用（首章结构指纹）
    target_key_phrases = chapters[0]["key_phrases"]  # 供 render/diff（单章主路径）
    skeleton_text = chapters[0]["skeleton"]          # 返回用（首章骨架）
    skeleton_l1 = chapters[0]["skeleton_l1"]

    # 1c. 【v4.0】权重校准（v5.12 起权重表固定，此步实为默认 8 维表）
    from .meta_optimizer import get_optimal_weights
    _scoring_weights = get_optimal_weights(chapters[0]["text"], verbose=True)
    print(f"[reverse-infer] Calibrated scoring weights: "
          f"b1={_scoring_weights.get('b1', 0):.3f} b2={_scoring_weights.get('b2', 0):.3f} "
          f"b3={_scoring_weights.get('b3', 0):.3f} b4={_scoring_weights.get('b4', 0):.3f} "
          f"b5={_scoring_weights.get('b5', 0):.3f} "
          f"b6={_scoring_weights.get('b6', 0):.3f}")

    # 【v5.16】旋转章批 + 文本梯度改进上下文（A/B/C 开关见 improvement_context）
    from .improvement_context import MULTI_BATCH, FINAL_TOP_K, FINAL_TOP_K_BY_MODE, ImprovementContext
    batch_size = 1 if not multi else MULTI_BATCH.get(mode, 2)
    n_pool = len(chapters)
    # 【v5.28】终选复评池按挡位扩容（fast 4 / standard 6 / quality 10）
    final_top_k = FINAL_TOP_K_BY_MODE.get(mode, FINAL_TOP_K)
    # 【v5.28】每章难度基线分（multi 模式，heuristic P 全章评估产出）；None → 不归一化
    baselines: dict[int, float] | None = None
    _rot = itertools.count()

    def _next_batch() -> list[int]:
        if not multi:
            return [0]
        start = (next(_rot) * batch_size) % n_pool
        return [(start + k) % n_pool for k in range(min(batch_size, n_pool))]

    improvement = ImprovementContext(improvement_config)

    async def _eval_candidate(p, batch_idxs=None):
        if batch_idxs is None:
            batch_idxs = _next_batch()
        if multi:
            return await _evaluate_p_multi(
                p, chapters, batch_idxs,
                chapter_section=chapter_section,
                scoring_weights=_scoring_weights, mode=mode,
                repro_ctx=repro_ctx, improvement=improvement,
                repro_cfg=repro_config,  # 【v5.18】g17 l2_rounds 等
                baselines=baselines,  # 【v5.28】难度归一化（None → 不归一化）
            )
        ch = chapters[0]
        r = await _evaluate_p(
            p, skeleton_text=ch["skeleton"], target_text=ch["text"],
            target_v=ch["v_target"], chapter_section=chapter_section,
            gen_params=ch["gen_params"], key_phrases=ch["key_phrases"],
            scoring_weights=_scoring_weights, mode=mode, repro_ctx=repro_ctx,
            emphasis_block=improvement.effective_block(0) or "",
            repro_cfg=repro_config,  # 【v5.18】g17 l2_rounds 等
        )
        # 单章也喂文本梯度：per_chapter 单条（供 improvement.update 分析扣分 + 前端统一序列化）。
        # 【v5.16.1】补齐分数/内容字段 → output_candidates 序列化结构统一（修 idx:None/0 分 bug）。
        r["per_chapter"] = [{
            "_chapter_idx": 0,
            "score": r.get("score", 0),
            "factual_consistency": r.get("factual_consistency", 0),
            "plot_fidelity": r.get("plot_fidelity", 0),
            "s_char": r.get("s_char", 0),
            "plot_sim": r.get("plot_sim", 0),  # 【v5.20】剧情相似度（诊断透出）
            "ai_flavor": r.get("ai_flavor"),  # 【v5.27】AI 味分（1=干净）
            "ai_flavor_findings": r.get("ai_flavor_findings") or [],  # 【v5.27.1】单章也喂 improvement_context
            "rendered_prompt": r.get("rendered_prompt") or "",
            "user_input": r.get("user_input") or "",
            "generated_text": r.get("generated_text") or "",
            "factual_detail": r.get("factual_detail"),
            "plot_fidelity_detail": r.get("plot_fidelity_detail"),
            "repro_gaps": r.get("repro_gaps"),
            "semantic_coverage": r.get("semantic_coverage"),
        }]
        return r

    # 3. 初始化优化器
    # 【v4.0】支持可替换优化策略
    _optimizer_info: dict[str, Any] = {"strategy": "bo-gp-ei", "champion": None}
    if optimization_strategy:
        from .optimization_strategies import AutoSelector, create_strategy, list_strategies
        available = list_strategies()
        if optimization_strategy not in available and optimization_strategy != "auto":
            print(f"[reverse-infer] [WARN] Unknown strategy '{optimization_strategy}', "
                  f"falling back to bo-gp-ei. Available: {available}")
            optimization_strategy = None
        elif optimization_strategy == "auto":
            _optimizer_info["strategy"] = "auto"
            print(f"[reverse-infer] Using AutoSelector (strategies: "
                  f"{', '.join(['bo-gp-ei', 'bo-tpe', 'evolutionary'])})")
        else:
            _optimizer_info["strategy"] = optimization_strategy
            print(f"[reverse-infer] Using strategy: {optimization_strategy}")

    opt = PromptBayesianOptimizer(dim=P_DIM_META["dim"])
    all_results: list[dict[str, Any]] = []  # 存完整结果
    gate_rejects: list[dict[str, Any]] = []  # 【v5.13 L3】闸门拒绝的观测（不入库、不污染 BO）
    # 【v5.14 L2b】纯复现重试全程额度（防 fast 8 迭代生成翻倍）：贯穿所有 _evaluate_p 直调点
    from .anchor_control import REPRO_RUN_BUDGET
    repro_ctx: dict | None = {"remaining": REPRO_RUN_BUDGET.get(mode, 3)}

    # 3a. KNN 先验（经验库冷启动）
    if n_knn > 0 and store is not None:
        try:
            hits = await store.search_by_structure(
                v_target=v_target,
                top_k=n_knn,
                filter_source_file=source_file or None,
                filter_section=chapter_section,
                target_text=target_text,
            )
            for hit in hits:
                # 从经验的 prompt 反解 P 向量
                hit_prompt = hit.get("best_prompt", "")
                if not hit_prompt:
                    continue
                p = parse_prompt_to_p(hit_prompt)
                result = await _eval_candidate(p)
                await _try_observe(
                    opt, p, result,
                    all_results=all_results, gate_rejects=gate_rejects,
                    source_tag="knn_prior", mode=mode,
                )
        except Exception:
            pass  # 经验库失败不影响主流程

    # 3b. V_target 启发式初始采样（新：比纯 LHS 更好的起点）
    # 利用 V(T) 和 P 之间已知的映射关系，生成一个领域感知的初始点
    # 【v5.22 P2】初始点相互独立 → 全部并行评估（gather + Semaphore 限流），
    # 替代「heuristic → perturb → LHS 逐个串行评估」。观测集无序，统一入集
    # 顺序不影响 BO；总点数（heuristic + perturb + LHS）与原串行完全一致。
    import asyncio as _asyncio2
    _INIT_CONCURRENT = 4
    _init_pool_sem = _asyncio2.Semaphore(_INIT_CONCURRENT)

    heuristic_p = _heuristic_p_from_v(v_target)
    heur_rng = np.random.RandomState(123)   # 主循环 4 的去重重采样也用它
    _init_points: list[tuple[list[float], str]] = []

    # 【v5.28】难度归一化基线：multi 模式先单独全章评估 heuristic P →
    # 产出每章基线分 baselines（纯测量：零复现额度、无强调块）。
    # 旋转章批的批内分被章难度主导（[8,9]批 0.56 vs [0,1]批 0.78）→ 批内分不可比、
    # BO 学到章组成噪声 → 多迭代不增值（挡位形同虚设）。score_adj = 批内分 − 章基线。
    if multi:
        try:
            _heur_res = await _evaluate_p_multi(
                heuristic_p, chapters, list(range(n_pool)),
                chapter_section=chapter_section, scoring_weights=_scoring_weights,
                mode=mode, repro_ctx={"remaining": 0}, improvement=None,
                repro_cfg=repro_config,
            )
            if _heur_res.get("per_chapter"):
                baselines = {
                    p.get("_chapter_idx", i): p.get("score", 0.0)
                    for i, p in enumerate(_heur_res["per_chapter"])
                    if (p.get("score") or 0.0) > 0.01  # 排除失败章（0 分），防虚高 adj
                }
                # heuristic 是难度参考点：相对自身基线的 adj 定义为 0（供终选池排序一致）
                _heur_res["score_adj"] = 0.0
            if _heur_res.get("v_prime") is not None:
                await _try_observe(
                    opt, heuristic_p, _heur_res,
                    all_results=all_results, gate_rejects=gate_rejects,
                    source_tag="heuristic", mode=mode,
                )
        except Exception:
            import traceback
            print(f"[reverse-infer] 难度基线评估失败（回退旧行为）:\n{traceback.format_exc()}")
            baselines = None
            _init_points.append((heuristic_p, "heuristic"))
    else:
        _init_points.append((heuristic_p, "heuristic"))

    # 从启发式点附近再采 1-2 个扰动点（增加初始多样性）
    if mode == "quality":
        pert_p = list(heuristic_p)
        pert_indices = heur_rng.choice(P_DIM_META["dim"], size=8, replace=False)
        for idx in pert_indices:
            pert_p[idx] = np.clip(pert_p[idx] + heur_rng.normal(0, 0.15), 0.02, 0.98)
        _init_points.append((pert_p, "heuristic_perturbed"))

    # 3c. LHS 拉丁超立方采样（补足剩余初始点；先扣 heuristic/perturb 点数，总点数不变）
    n_initial_needed = max(2, n_lhs - len(all_results) - len(_init_points))
    if n_initial_needed > 0:
        try:
            # 简单的 LHS（用 bayesian_opt 里的？不，自己写个轻量的）
            from .param_search import _lhs_sample
            dim_ranges = [(0.0, 1.0)] * P_DIM_META["dim"]
            lhs_points = _lhs_sample(n_initial_needed, dim_ranges, seed=42)

            # role 组做 softmax 化（让初始点更合理）
            role_s = P_DIM_META["groups"]["role"]["start"]
            role_e = P_DIM_META["groups"]["role"]["end"]
            for p in lhs_points:
                role_vals = p[role_s:role_e]
                role_sum = sum(role_vals)
                if role_sum > 0:
                    for i in range(role_e - role_s):
                        p[role_s + i] = (role_vals[i] / role_sum) * 0.9 + 0.02
            _init_points.extend((p, "lhs_initial") for p in lhs_points)
        except Exception:
            import traceback
            print(f"[reverse-infer] LHS 采样阶段出错:\n{traceback.format_exc()}")
            # LHS 失败不影响已收集的数据

    async def _eval_init_point(_p: list[float], _tag: str):
        async with _init_pool_sem:
            _r = await _eval_candidate(_p)
            return _p, _tag, _r

    _init_res = await _asyncio2.gather(
        *[_eval_init_point(_p, _t) for _p, _t in _init_points]
    )
    for _p, _tag, _result in _init_res:
        if _result.get("v_prime") is not None:
            await _try_observe(
                opt, _p, _result,
                all_results=all_results, gate_rejects=gate_rejects,
                source_tag=_tag, mode=mode,
            )

    # ── 4. 优化循环（支持可替换策略） ──
    # 收集所有现有观测用于初始化策略
    _n_initial = opt.n_observations()
    remaining = total_iter - _n_initial

    # 初始化策略（如果指定了 optimization_strategy）
    _strategy_instance = None
    _auto_selector = None
    _auto_strategy_name = None

    if optimization_strategy == "auto" and remaining > 0:
        from .optimization_strategies import AutoSelector
        _auto_selector = AutoSelector(dim=P_DIM_META["dim"],
                                       strategy_pool=["bo-gp-ei", "bo-tpe", "evolutionary"])
        for r in all_results:
            if r.get("v_prime") is not None and r.get("p_vector") is not None:
                _auto_selector.observe_shared(r["p_vector"], r["score"])
        _auto_selector.select_candidates(n_candidates=2)
        _auto_selector.set_budget(total_iter, selection_budget_ratio=0.3)
        _optimizer_info["auto_selector"] = True
        print(f"[reverse-infer] AutoSelector ready: candidates={_auto_selector._candidates}, "
              f"budget_each={_auto_selector._strategy_budget}")
    elif optimization_strategy and optimization_strategy in (
        "bo-gp-ei", "random", "bo-tpe", "cma-es", "evolutionary"
    ):
        from .optimization_strategies import create_strategy
        _strategy_instance = create_strategy(optimization_strategy, dim=P_DIM_META["dim"])
        for r in all_results:
            if r.get("v_prime") is not None and r.get("p_vector") is not None:
                _strategy_instance.observe(r["p_vector"], r["score"])

    # 【v5.13 L4c】建议点渲染文本去重：初始观测的 rendered_prompt 全部入 seen，
    # BO 建议点若渲染文本已见过 → 小扰动重采样（至多 3 次），让每次迭代真正换文本。
    seen_prompt_texts: set[str] = set()
    for _r in all_results:
        _rp = _r.get("rendered_prompt")
        if _rp:
            seen_prompt_texts.add(_rp)
    dup_resamples = 0

    for i in range(max(0, remaining)):
        try:
            # ── 决定本次使用的策略 ──
            if _auto_selector is not None:
                _auto_strategy_name = _auto_selector.next_strategy()
                if _auto_strategy_name is None:
                    break
                _current_strat = _auto_selector.get_strategy(_auto_strategy_name)
                p_next, info = _current_strat.suggest()
                info["_strategy"] = _auto_strategy_name
            elif _strategy_instance is not None:
                p_next, info = _strategy_instance.suggest()
            else:
                p_next, info = opt.suggest_next()

            # 【v5.13 L4c】渲染文本去重 → 小扰动重采样（至多 3 次）
            p_text = render_prompt(
                p_next, chapter_section=chapter_section,
                key_phrases=target_key_phrases,
                target_char_count=len(target_text),
            )
            _n_dup = 0
            while p_text in seen_prompt_texts and _n_dup < 3:
                _n_dup += 1
                dup_resamples += 1
                _pert_p = list(p_next)
                _dup_idx = heur_rng.choice(P_DIM_META["dim"], size=5, replace=False)
                for _di in _dup_idx:
                    _pert_p[_di] = np.clip(_pert_p[_di] + heur_rng.normal(0, 0.05), 0.02, 0.98)
                p_next = _pert_p
                p_text = render_prompt(
                    p_next, chapter_section=chapter_section,
                    key_phrases=target_key_phrases,
                    target_char_count=len(target_text),
                )
            seen_prompt_texts.add(p_text)

            result = await _eval_candidate(p_next)
            source_tag = "bayesian"
            if _auto_strategy_name:
                source_tag = f"bayesian:{_auto_strategy_name}"
            elif _strategy_instance is not None:
                source_tag = f"bayesian:{optimization_strategy}"
            _observed = await _try_observe(
                opt, p_next, result,
                all_results=all_results, gate_rejects=gate_rejects,
                source_tag=source_tag, mode=mode,
                info=info,
                strategy=(_current_strat if _auto_strategy_name else _strategy_instance),
                auto_selector=_auto_selector if _auto_strategy_name else None,
                strategy_name=_auto_strategy_name,
                optimizer_info=_optimizer_info,
            )
            # 【v5.16】文本梯度：过闸门才喂改进上下文（拒收漂移不污染强调块）
            if _observed:
                improvement.update(result)
                improvement.note_history(result, result.get("rendered_prompt", ""))

                # 【v5.16 OPRO】standard/quality 每 opre_freq 迭代提案（B 开关）
                if (mode in ("standard", "quality")
                        and i % improvement.cfg.get("opre_freq", 3) == 0):
                    _proposal = await improvement.propose_user_block(mode)
                    if _proposal:
                        improvement.extra_block = _proposal
                    if improvement.cfg.get("proposal_target") == "user_input+system":
                        _sys_text = await improvement.propose_system_edit(mode)
                        if _sys_text:
                            try:
                                _p_sys = parse_prompt_to_p(_sys_text)
                                _sys_result = await _eval_candidate(_p_sys)
                                _sys_obs = await _try_observe(
                                    opt, _p_sys, _sys_result,
                                    all_results=all_results, gate_rejects=gate_rejects,
                                    source_tag="opro_system", mode=mode,
                                )
                                if _sys_obs:
                                    improvement.update(_sys_result)
                                    improvement.note_history(
                                        _sys_result, _sys_result.get("rendered_prompt", ""))
                            except Exception:
                                pass
        except Exception:
            import traceback
            print(f"[reverse-infer] 第 {i + 1} 轮出错:\n{traceback.format_exc()}")
            # 单轮失败不影响整体流程

    # ── 4b. 子空间局部精炼（降维搜索） ──
    # 全空间 BO 在 47D 中只能做粗搜索。这里固定找到的最佳 P 向量中的大部分维度，
    # 每次只扰动一组维度（5-12D），做更密集的局部搜索。
    if mode in ("standard", "quality") and len(all_results) >= 3:
        valid_for_refine = [r for r in all_results if r.get("v_prime") is not None]
        if valid_for_refine:
            valid_for_refine.sort(key=lambda x: x["score"], reverse=True)
            best_p = valid_for_refine[0]["p_vector"]

            refine_configs = []
            if mode == "quality":
                # quality 模式：三轮精炼（【v5.11】剧情策略纳入局部搜索）
                refine_configs = [
                    (["focus", "constraints"], 4),   # 18D → 有效局部搜索
                    (["style_rules"], 4),             # 12D → 密集搜索
                    (["plot"], 4),                    # 7D → 剧情策略局部搜索（复现原文核心）
                ]
            else:
                # standard 模式：一轮精炼
                refine_configs = [
                    (["focus", "constraints"], 3),
                ]

            for group_names, n_samples in refine_configs:
                try:
                    refine_results = await _subspace_refine(
                        best_p, group_names=group_names, n_samples=n_samples,
                        skeleton_text=skeleton_text, target_text=target_text,
                        target_v=v_target,
                        chapter_section=chapter_section, gen_params=gen_params,
                        key_phrases=target_key_phrases,
                        scoring_weights=_scoring_weights,
                        mode=mode,  # 【v5.13 L2】
                        repro_ctx=repro_ctx,  # 【v5.14 L2b】
                        eval_candidate=eval_candidate,  # 【v5.16】多章评估回调
                    )
                    for rr in refine_results:
                        await _try_observe(
                            None, rr.get("p_vector"), rr,
                            all_results=all_results, gate_rejects=gate_rejects,
                            source_tag=rr.get("source", "refine"), mode=mode,
                        )
                except Exception:
                    print(f"[reverse-infer] 子空间精炼 {group_names} 出错，跳过")

    # ── 4c. 基于结构差异的 Prompt 语义精炼 ──
    # 与 4b 的数值扰动不同，这里直接在 prompt 文本层面做语义级调整，
    # 针对具体偏差维度（如"对白密度偏低""词汇丰富度偏高"）修改 prompt 规则。
    # 【v5.16】多章模式跳过（_refine_with_diff 是单章语义精炼，其 system-prompt
    # 提案角色已由 OPRO 的 propose_system_edit 承担）。
    if not multi and mode in ("standard", "quality") and len(all_results) >= 3:
        try:
            valid_for_diff = [r for r in all_results if r.get("v_prime") is not None]
            if valid_for_diff:
                valid_for_diff.sort(key=lambda x: x["score"], reverse=True)
                best_candidate = valid_for_diff[0]
                # 【v5.8】用最佳候选自身的 gen_params 重评精炼后的 prompt，
                # 保证"改 prompt"和"测 prompt"用同一组生成参数。
                refine_gen_params = best_candidate.get("gen_params") or gen_params
                diff_refined = await _refine_with_diff(
                    best_candidate, target_text,
                    skeleton_text=skeleton_text,
                    target_v=v_target,
                    chapter_section=chapter_section,
                    gen_params=refine_gen_params,
                    key_phrases=target_key_phrases, scoring_weights=_scoring_weights,
                )
                for rr in diff_refined:
                    await _try_observe(
                        None, rr.get("p_vector"), rr,
                        all_results=all_results, gate_rejects=gate_rejects,
                        source_tag="refined_with_diff", mode=mode,
                    )
        except Exception:
            print("[reverse-infer] 语义精炼出错，跳过")

    # 5. 后处理：去重 + 排序 + 多样性 top-3
    # 【v5.28】排序用 score_adj（难度归一化分）：原始批内分被章难度主导不可比，
    # 直接按它选终选池会漏掉"在难章上更优"的真最优（最高批内分候选全量复评掉 0.07）。
    valid = [r for r in all_results if r.get("v_prime") is not None]
    valid.sort(key=lambda x: x.get("score_adj") if isinstance(x.get("score_adj"), (int, float)) else x["score"], reverse=True)

    # 【v5.16】终选：多章模式下 top-k 候选在全 10 章复评，按聚合分重排。
    # 搜索阶段用旋转章批（快、粗筛），终选全量复评 = 稳定反映系统状态（去单章噪声）。
    # 【v5.28】复评池按挡位扩容 final_top_k（fast 4 / standard 6 / quality 10）。
    # 注：终选复用共享 repro_ctx（搜索耗尽则终选为纯测量，成本有界）。
    if multi and valid:
        _top = valid[:final_top_k]
        # 【v5.22 P2】终选 3 候选相互独立 → 并行全量复评（每候选内部 A2 已限 4 路，
        # 候选级 Semaphore(2) → 总 ≤8 路 LLM，防限流）。repro_ctx 共享额度并发扣减
        # 为原子操作，总额度语义保持。观测聚合顺序不影响结果（候选互不引用）。
        import asyncio as _asyncio3
        _FINAL_CAND_CONCURRENCY = 2
        _final_cand_sem = _asyncio3.Semaphore(_FINAL_CAND_CONCURRENCY)

        async def _final_eval(_r: dict) -> dict | None:
            async with _final_cand_sem:
                _agg = await _evaluate_p_multi(
                    _r["p_vector"], chapters, list(range(n_pool)),
                    chapter_section=chapter_section, scoring_weights=_scoring_weights,
                    mode=mode, repro_ctx=repro_ctx, improvement=improvement,
                    repro_cfg=repro_config,  # 【v5.18】g17 l2_rounds 等
                )
                # 【v5.27.2】终选稳健性：v_prime 只是 v5.16 时代的"评估成功"占位检查，
                # 单章 v_prime 提取偶发失败会让整次终选丢弃（候选保持搜索值、无 final10）。
                # 改为：有合法 score 即接受终选（score 是逐章均值，更能反映评估成功与否）。
                if _agg.get("v_prime") is None \
                        and not isinstance(_agg.get("score"), (int, float)):
                    return None
                _r["per_chapter"] = _agg.get("per_chapter", [])
                _r["chapters_evaluated"] = list(range(n_pool))
                for _d in ("score", "v_cosine", "char_similarity", "s_char",
                           "plot_sim",  # 【v5.20】终选复评后顶层剧情分
                           "ai_flavor",  # 【v5.27】终选复评后顶层 AI 味分
                           "semantic_coverage", "factual_consistency",
                           "plot_fidelity", "length_alignment"):
                    if isinstance(_agg.get(_d), (int, float)):
                        _r[_d] = _agg[_d]
                # 【v5.16.1】终选后顶层代表字段 = 全章复评首章（确定性），与 per_chapter 一致
                for _k in ("rendered_prompt", "user_input", "generated_text", "rep_chapter_idx"):
                    if _agg.get(_k) is not None:
                        _r[_k] = _agg[_k]
                _r["source"] = f"{_r.get('source', '')}+final{n_pool}"
                return _r

        await _asyncio3.gather(*[_final_eval(_r) for _r in _top])
        # 【v5.27.5】终选后排序：final-evaluated 候选优先（它们有全 10 章复评的准确分），
        # 其余按分。否则全 10 章均值 vs 2 章批均值的得分差异会把终选候选挤出 top-3，
        # 输出变成未终选的搜索候选（用户看不到全 10 章 AI 味）。
        valid.sort(key=lambda x: ("+final" not in str(x.get("source", "")), -x["score"]))

    # 多样性过滤：选 top-3，要求 P 向量距离 > 0.2
    candidates: list[dict[str, Any]] = []
    for r in valid:
        p = np.array(r["p_vector"])
        too_close = False
        for c in candidates:
            cp = np.array(c["p_vector"])
            if np.linalg.norm(p - cp) < 0.2:
                too_close = True
                break
        if not too_close:
            candidates.append(r)
            if len(candidates) >= 3:
                break

    # 如果不够 3 个，把后面的补上
    if len(candidates) < 3:
        for r in valid:
            if r not in candidates:
                candidates.append(r)
                if len(candidates) >= 3:
                    break

    # 6. V-P 相关增量（简单 Pearson 近似）
    vp_hint = _compute_vp_hint(all_results, v_target)

    # 构造输出
    # 【v5.28】curve/best_score 用原始分口径重建：BO 观察的是 score_adj（难度归一化），
    # opt.get_convergence_curve() 是 adj 的 best-so-far，展示出来不可读。
    # 按 all_results 观测顺序重算原始分 best-so-far，口径与旧版一致。
    curve: list[float] = []
    _raw_best = -float("inf")
    for _r in all_results:
        if _r.get("v_prime") is None or not isinstance(_r.get("score"), (int, float)):
            continue
        _raw_best = max(_raw_best, _r["score"])
        curve.append(_raw_best)
    best_score = max(curve) if curve else 0.0

    # 每轮迭代的详细相似度数据（按观测顺序）
    iteration_details = []
    for r in all_results:
        if r.get("v_prime") is None:
            continue
        iteration_details.append({
            "score": round(r["score"], 4),
            "score_adj": r.get("score_adj"),  # 【v5.28】难度归一化分（BO 观察目标，诊断透出）
            "v_cosine": round(r["v_cosine"], 4),
            "char_similarity": round(r["char_similarity"], 4),
            "mean_abs_delta_pct": round(r.get("mean_abs_delta_pct", 0), 2),
            "factual_consistency": round(r.get("factual_consistency", 0), 4),  # 【v5.10】事实一致性
            "plot_fidelity": round(r.get("plot_fidelity", 0), 4),  # 【v5.11】事件保真
            "s_char": round(r.get("s_char", 0), 4),                # 【v5.11】原文字符相似度
            "gen_params": r.get("gen_params") or {},  # 【v5.8】该轮迭代实际使用的参数
            "source": r.get("source", "unknown"),
            # 【v5.14】方向 3：落盘全部 8 次迭代的完整字段（供复盘）
            "semantic_coverage": round(r.get("semantic_coverage", 0), 4),
            "anchors_retried": r.get("anchors_retried", 0),
            "repro_retried": r.get("repro_retried", 0),
            "rendered_prompt": r.get("rendered_prompt") or "",
            "user_input": r.get("user_input") or "",
            "generated_text": r.get("generated_text") or "",
            # 【v5.16】多章逐章明细（score/fc/pf/s_char，诊断单章过拟合）
            "per_chapter": [{
                "idx": p.get("_chapter_idx"),
                "score": round(p.get("score", 0), 4),
                "fc": round(p.get("factual_consistency", 0), 4),
                "pf": round(p.get("plot_fidelity", 0), 4),
                "s_char": round(p.get("s_char", 0), 4),
                "plot_sim": round(p.get("plot_sim", 0), 4),  # 【v5.20】剧情相似度
                "turn_fidelity": round(p.get("turn_fidelity", 0), 4),  # 【v5.21】对白轮保真
            } for p in (r.get("per_chapter") or [])],
        })

    # 整理 candidate 格式（去掉大字段，加预览）
    output_candidates = []
    for c in candidates:
        gen_text = c.get("generated_text") or ""
        sample_text = gen_text[:20000] + ("..." if len(gen_text) > 20000 else "")  # 【显示修复】5000→20000，单章顶层正文不再腰斩
        output_candidates.append({
            "p_vector": c["p_vector"],
            "p_dict": _p_to_dict(c["p_vector"]),
            "rendered_prompt": c.get("rendered_prompt") or "",
            "gen_params": c.get("gen_params") or {},  # 【v5.8】该候选实际使用的生成参数
            "score": round(c.get("score", 0), 4),
            "v_cosine": round(c.get("v_cosine", 0), 4),
            "char_similarity": round(c.get("char_similarity", 0), 4),
            "mean_abs_delta_pct": round(c.get("mean_abs_delta_pct", 0), 2),
            "factual_consistency": round(c.get("factual_consistency", 0), 4),  # 【v5.10】事实一致性
            "plot_fidelity": round(c.get("plot_fidelity", 0), 4),  # 【v5.11】事件保真
            "s_char": round(c.get("s_char", 0), 4),                # 【v5.11】原文字符相似度
            "plot_sim": round(c.get("plot_sim", 0), 4),          # 【v5.20】剧情相似度（句子级最大余弦均值）
            "plot_sim_coverage": round(c.get("plot_sim_coverage", 0), 4),  # 【v5.20】剧情覆盖率@0.50（诊断）
            "turn_fidelity": round(c.get("turn_fidelity", 0), 4),  # 【v5.21】对白轮保真（缺失/乱序惩罚）
            "ai_flavor": round(c.get("ai_flavor", 0), 4) if isinstance(c.get("ai_flavor"), (int, float)) else None,  # 【v5.27】AI 味分（1=干净）
            "scoring": c.get("scoring") or "v519",  # 【v5.20】评分模式
            "semantic_coverage": round(c.get("semantic_coverage", 0), 4),  # 【v5.12】语义覆盖
            "user_input": c.get("user_input") or "",              # 【v5.12】完整生成指令（含剧情骨架）
            "sample_text": sample_text,
            "source": c.get("source", "unknown"),
            # 【v5.16.1】顶层代表字段所属章节（前端逐章 chip 默认定位）
            "rep_chapter_idx": c.get("rep_chapter_idx"),
            # 【v5.16】多章逐章明细（诊断单章过拟合）
            # 【v5.16.1】补逐章 prompt/指令/正文（前端章节 chip 切换展示用）
            # 【显示修复】改为 _output_per_chapter，不再 2000 截断 → 前端正文完整显示
            "per_chapter": [_output_per_chapter(p, pi) for pi, p in enumerate(c.get("per_chapter") or [])],
        })

    result = {
        "v_target": v_target,
        "skeleton_text": skeleton_text,
        "scoring": output_candidates[0].get("scoring", "v519") if output_candidates else "v519",  # 【v5.20】评分模式
        "candidates": output_candidates,
        "optimization": {
            "mode": mode,
            "total_iterations": total_iter,
            "completed_iterations": opt.n_observations(),
            "best_score": round(best_score, 4),
            "curve": [round(s, 4) for s in curve],
            "method": "gp_ei",
            "dup_resamples": dup_resamples,  # 【v5.13 L4c】建议点文本去重重采样次数
            "iteration_details": iteration_details,
        },
        "vp_correlation_hint": vp_hint,
        "gate_rejects": gate_rejects,      # 【v5.13 L3】闸门拒绝的观测（不入库、不污染 BO）
        "skeleton_l1": skeleton_l1,        # 【v5.13 L1】骨架锚点修复 + 语义闸门元数据
        # 【v5.16】多章信息
        "multi_chapter": multi,
        "chapter_count": n_pool,
        "improvement": {
            "cfg": improvement.cfg,
            "champion_score": round(improvement.champion_score, 4),
            "opre_used": improvement.opre_used,
        },
    }

    # 【自动落盘】完整响应写入 logs/runs/，供事后复盘/对比。
    # 失败不影响主流程（结果照常返回给前端）。
    try:
        saved_path = _save_reverse_infer_response(
            result, all_results=all_results,
            source_file=source_file, chapter_section=chapter_section,
            target_text=target_text,
            target_texts=(raw_texts if multi else None),
        )
        result["saved_to"] = saved_path
        # 【编码安全】服务端 stdout 在 Windows 上是 GBK，emoji 无法编码会抛
        # UnicodeEncodeError 并让整个端点 500（v5.10 验证时暴露），必须用 ASCII 标记。
        print(f"[reverse-infer] [OK] 完整响应已落盘: {saved_path}")
    except Exception as _e:
        print(f"[reverse-infer] [WARN] 响应落盘失败（不影响结果）: {_e}")

    return result


def _compute_vp_hint(all_results: list[dict], v_target: dict) -> list[dict]:
    """从当前轮的观测中计算 V-P 相关的 top 关联对。

    这是一个粗略的、单 run 级别的提示，不是全局 V-P 矩阵。
    全局的在经验库里算。
    """
    if len(all_results) < 5:
        return []

    try:
        # 收集 P 矩阵 (n, P_DIM_META["dim"]) 和 V 偏差矩阵 (n, 13)
        # 安全过滤：确保 p_vector 和 v_prime.vector 都存在
        safe_results = [
            r for r in all_results
            if r.get("p_vector") is not None
            and r.get("v_prime") is not None
            and isinstance(r["v_prime"], dict)
            and r["v_prime"].get("vector") is not None
        ]
        if len(safe_results) < 5:
            return []
        p_mat = np.array([r["p_vector"] for r in safe_results])
        v_mat = np.array([r["v_prime"]["vector"] for r in safe_results])

        v_labels = [
            "avg_sentence_len", "sentence_len_variance", "dialogue_density",
            "median_paragraph_len", "paragraph_frequency",
            "comma_density", "special_punct_density",
            "line_break_frequency", "lexical_richness",
            "sentence_start_diversity", "modifier_density",
            "dialogue_turn_density", "sentences_per_paragraph",
        ]
        p_labels = []
        for gname, gmeta in P_DIM_META["groups"].items():
            for label in gmeta["labels"]:
                p_labels.append(f"{gname}.{label}")

        # 计算相关矩阵
        n = len(all_results)
        # 标准化
        p_std = (p_mat - p_mat.mean(axis=0)) / (p_mat.std(axis=0) + 1e-10)
        v_std = (v_mat - v_mat.mean(axis=0)) / (v_mat.std(axis=0) + 1e-10)
        corr = (v_std.T @ p_std) / n  # (13, dim)，随 P_DIM_META["dim"] 自动扩展

        # 找 top-5 绝对值最大的
        pairs = []
        for i, vlabel in enumerate(v_labels):
            for j, plabel in enumerate(p_labels):
                r = corr[i, j]
                if abs(r) > 0.3:  # 只报告中强相关
                    pairs.append({"v_dim": vlabel, "p_dim": plabel, "r": round(float(r), 4)})

        pairs.sort(key=lambda x: abs(x["r"]), reverse=True)
        return pairs[:5]
    except Exception:
        return []
