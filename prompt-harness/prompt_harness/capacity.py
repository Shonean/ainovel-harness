# -*- coding: utf-8 -*-
"""prompt 容量估算 + 三维诊断 + 自动补齐（创作助手内置能力，2026-08-15）。

创立初衷：从一份 prompt 推断它能最多支撑多少字的高质量网文，并把 AI 参数纳入考虑。
用户定位：**容量 = 质量的镜子**——用"能撑多少字"度量 prompt 的密度/内容/质量，
不足则自动补齐。这是创作助手（ai_creation.new_arc / arc_chat）的内置能力。

成本（2026-08-15 降本）：
- estimate_arc_capacity   0 LLM（纯正则，历史锚点校准）
- diagnose_arc_capacity   0-1 次（内容维度要实体名时才 extract_key_facts，按 l1 hash 缓存）
- enrich_l1               1 次（仅 should_fill 且获授权；保真护栏复用诊断 facts，0 额外调用）

历史锚点（来自 plot_template_library.json 611 条 + plot_extract_results 153 文件）：
- 扩展率 l1→l5 ~28-36x（93字 l1 → 3333字 l5）
- 单章容量 1800-3000 字（对齐真实网文 2600-3100 字/章）
- 每章消耗 ~3 beats（l3 每章 2-5 拍中位 3）
- 信息过密衰减：实体 20→60 分 0.861→0.792（斜率 0.0017/单元）
- AI 味脏阈值 0.65 / derive 0.70
"""
from __future__ import annotations

import re
import statistics
from typing import Any

# ── 单章容量（字）──────────────────────────────────────────────
CH_MIN = 1200          # 4 场景 × _adaptive_target_len floor 250
CH_MAX = 3000          # 真实网文 2600-3100 字/章
EXPAND_PER_CH = 28.0   # l1 字数 → 单章字数（实测 28-36x，取保守）
ANCHOR_PER_CH = 15     # 每个锚点（数字/地名/道具）额外可写字数

# ── 章节数 ──────────────────────────────────────────────────────
BEATS_PER_CH = 3       # 每章消耗 beats（l3 每章 2-5 拍，取中位）
BEATS_MAX = 12         # 单条 l1 的 beats 上限
UNITS_PER_CH_Q = 6     # 过密衰减线（>6 beats/章 → 压缩掉章节）
CH_CAP_USER = 100      # 弧章节上限（物理护栏）

# ── 三维诊断基准 ───────────────────────────────────────────────
IDEAL_CHAR_PER_CH = (60, 160)   # l1 字数/估出章数 的健康带
N_ENTITY_REQ = 3
N_CONFLICT_REQ = 1
N_CHAR_REQ = 2
N_ANCHOR_REQ = 2                # 质量锚点下限（数字/地名/道具）
MAX_CHARS_FILL = 160            # 补齐后 l1 上限

# ── 模型上下文（缺口补丁：qwen_model_registry 只存名字，这里给字符窗近似）──
_MODEL_WINDOW_CHARS: dict[str, int] = {
    "qwen3.5-flash": 60000,
    "qwen3.5-max": 60000,
    "qwen3.5-plus": 60000,
    "qwen3.6-plus": 60000,
    "qwen3.7": 60000,
    "qwen3.8": 60000,
    "qwen2.5": 30000,
    "deepseek": 60000,
}
_DEFAULT_WINDOW_CHARS = 30000

# ── 冲突关键词（确定性）────────────────────────────────────────
_CONFLICT_KW = (
    "冲突", "对抗", "杀", "抢", "夺", "争", "逃", "追", "反击", "陷害",
    "背叛", "决裂", "复仇", "破", "救", "威胁", "秘密", "计", "局", "斗",
    "偷", "骗", "逼", "害", "败", "死", "恩怨",
)

# 复用 plot_library 的地名/金额正则（保持单一来源）
from .plot_library import _MONEY_RE, _PLACE_RE, _PLACE_SUFFIX  # noqa: E402

# 数字（阿拉伯数字 + 中文数字词组）
_NUM_RE = re.compile(r"\d+(?:\.\d+)?|[一二三四五六七八九十百千零]+(?:万|亿|千)?")


# ════════════════════════════════════════════════════════════════
# ① 确定性信息量提取（0 LLM）
# ════════════════════════════════════════════════════════════════
def info_units(l1: str) -> dict[str, int]:
    """纯正则提取 l1 的信息单元。返回 {chars, clauses, conflicts, places,
    money, numbers, anchors, beats}。"""
    l1 = (l1 or "").strip()
    chars = len(l1)
    clauses = sum(1 for c in re.split(r"[，。；！？、,;.!?]", l1) if c.strip())
    conflicts = sum(1 for kw in _CONFLICT_KW if kw in l1)
    places = len(_PLACE_RE.findall(l1))
    money = len(_MONEY_RE.findall(l1))
    numbers = len(_NUM_RE.findall(l1))
    anchors = places + money + numbers
    beats = max(1, min(BEATS_MAX, round(clauses + 0.8 * conflicts)))
    return {
        "chars": chars, "clauses": clauses, "conflicts": conflicts,
        "places": places, "money": money, "numbers": numbers,
        "anchors": anchors, "beats": beats,
    }


# ════════════════════════════════════════════════════════════════
# ② 容量估算核心（0 LLM）
# ════════════════════════════════════════════════════════════════
def estimate_arc_capacity(
    l1: str,
    *,
    n_chapters: int = 1,
    model: str | None = None,
) -> dict[str, Any]:
    """估算一份 l1 极简剧情最多能支撑多少字/章的高质量网文。

    全确定性（0 LLM）。返回：
    {total_chars_est, total_chapters_est, cap_chapter_est, confidence,
     info, quality_cap, ai_cap, breakdown}
    """
    info = info_units(l1)
    n_chars = info["chars"]
    n_anchor = info["anchors"]
    beats = info["beats"]

    # ① 单章容量：每 beat 一段可写内容 + 锚点，字数作次要调节。
    # 真实网文 2600-3100 字/章：4 场景 × ~700 字/场景，与拍数正相关。
    cap_chapter = max(CH_MIN, min(CH_MAX,
        round(beats * 500 + n_anchor * ANCHOR_PER_CH
              + (n_chars - IDEAL_CHAR_PER_CH[0]) * 4)))

    # ② 章节数：信息约束（每章消耗 ~3 beats）
    N_ch_info = max(1, beats // BEATS_PER_CH)

    # ③ 章节数：质量衰减约束（过密 → 每章塞太多单元 → 掉分）
    units_per_ch = beats / max(1, N_ch_info)
    if units_per_ch > UNITS_PER_CH_Q:
        N_ch_q = max(1, beats // UNITS_PER_CH_Q)
    else:
        N_ch_q = N_ch_info

    # ④ 章节数：AI/模型物理约束（整弧正文不超上下文窗口）
    window_chars = model_window_chars(model)
    N_ch_ai = max(1, min(CH_CAP_USER, window_chars // max(1, cap_chapter)))

    # ⑤ 综合（需求章数×3 作需求下限——用户要撑 N 章，至少给 3N 拍空间）
    N_ch = min(N_ch_q, N_ch_ai, max(1, int(n_chapters) * 3))

    total = N_ch * cap_chapter

    # 置信度：字数在健康带 + 冲突≥1 + 锚点≥2 → high
    if IDEAL_CHAR_PER_CH[0] <= n_chars <= IDEAL_CHAR_PER_CH[1] \
            and info["conflicts"] >= 1 and n_anchor >= 2:
        confidence = "high"
    elif n_chars >= 20:
        confidence = "med"
    else:
        confidence = "low"

    return {
        "total_chars_est": total,
        "total_chapters_est": N_ch,
        "cap_chapter_est": cap_chapter,
        "confidence": confidence,
        "info": info,
        "quality_cap": {
            "units_per_ch": round(units_per_ch, 2),
            "decayed": units_per_ch > UNITS_PER_CH_Q,
            "opt_band": IDEAL_CHAR_PER_CH,
        },
        "ai_cap": {
            "window_chars": window_chars,
            "n_chars_est": n_chars,
        },
        "breakdown": [{"ch": i + 1, "chars_lo": CH_MIN, "chars_hi": cap_chapter}
                      for i in range(N_ch)],
    }


# ════════════════════════════════════════════════════════════════
# ③ 三维诊断（0-1 次 LLM）
# ════════════════════════════════════════════════════════════════
# extract_key_facts 按 l1 hash 缓存（同弧重复不重跑）
_facts_cache: dict[str, list[str]] = {}


async def _get_facts_cached(l1: str) -> list[str]:
    """extract_key_facts 带进程内缓存。失败返回空列表（不阻塞诊断）。"""
    key = hash(l1)
    if key in _facts_cache:
        return _facts_cache[key]
    try:
        from .ladder import extract_key_facts
        facts = await extract_key_facts(l1)
    except Exception:  # noqa: BLE001 —— 失败不阻塞诊断
        facts = []
    _facts_cache[key] = facts
    return facts


async def diagnose_arc_capacity(
    l1: str,
    *,
    style: str = "",
    role_setting: str = "",
    template: dict[str, Any] | None = None,
    n_chapters: int = 1,
    use_facts: bool = True,
) -> dict[str, Any]:
    """在容量估算之上做三维诊断（密度/内容/质量），返回缺口与补齐方向。

    use_facts=True 时 1 次 extract_key_facts(l1)（缓存），供内容维度实体数
    + 补齐做保真护栏。返回 {capacity, dimensions, should_fill, fill_targets, facts}
    """
    est = estimate_arc_capacity(l1, n_chapters=n_chapters)
    info = est["info"]
    N_ch = est["total_chapters_est"]

    # 内容维度：实体数（1 次 LLM，缓存）
    facts: list[str] = []
    n_entity = 0
    if use_facts:
        facts = await _get_facts_cached(l1)
        n_entity = len(facts)

    # ── 密度：l1 每章承载字数 vs 健康带 ──
    chars_per_ch = info["chars"] / max(1, N_ch)
    if chars_per_ch < IDEAL_CHAR_PER_CH[0]:
        density_gap, density_dir = "thin", \
            "密度偏薄：每章素材不足，加 1 个中间事件/转折（如 XX 在 YY 被迫做选择）"
    elif chars_per_ch > IDEAL_CHAR_PER_CH[1]:
        density_gap, density_dir = "dense", \
            "信息过密会掉质量：建议拆成更多章或精简重复事件"
    else:
        density_gap, density_dir = "ok", ""
    density_score = 1.0 if density_gap == "ok" else (
        0.4 if density_gap == "thin" else 0.6)

    # ── 内容：实体/冲突/角色/地点/关键物 ──
    content_missing: list[str] = []
    if info["conflicts"] < N_CONFLICT_REQ:
        content_missing.append("冲突")
    if n_entity < N_ENTITY_REQ:
        content_missing.append("实体")
    if info["places"] < 1:
        content_missing.append("具体地点")
    if info["anchors"] < N_ANCHOR_REQ:
        content_missing.append("关键物/数字")
    content_score = 1.0 - 0.3 * len(content_missing)
    content_dir = ("补：" + "、".join(content_missing)) if content_missing else ""

    # ── 质量：锚点 + AI 味风险 + 一致性 ──
    ai_risk = info["anchors"] < N_ANCHOR_REQ or info["chars"] < 40
    consistency_risk = (N_ch > 1 and info["beats"] < 3 * N_ch)
    quality_score = 1.0 - 0.3 * ai_risk - 0.2 * consistency_risk
    quality_dir = ("加具体数字/标志物（如 欠500万 / 掌门玉佩 / 三日后大比）"
                   if ai_risk else "AI 味风险低") + \
                  ("；给后续章留可衍生事件（冲突升级/新线索）"
                   if consistency_risk else "")

    dimensions = {
        "density": {"score": round(density_score, 2), "gap": density_gap,
                    "chars_per_ch": round(chars_per_ch, 1), "direction": density_dir},
        "content": {"score": round(content_score, 2), "missing": content_missing,
                    "n_entity": n_entity, "n_conflict": info["conflicts"],
                    "direction": content_dir},
        "quality": {"score": round(quality_score, 2), "ai_flavor_risk": ai_risk,
                    "consistency_risk": consistency_risk, "direction": quality_dir},
    }

    should_fill = (density_gap == "thin") or bool(content_missing) or ai_risk
    return {
        "capacity": est,
        "dimensions": dimensions,
        "should_fill": should_fill,
        "fill_targets": {
            "density": "thin" if density_gap == "thin" else "ok",
            "content": content_missing,
            "quality": info["anchors"] - N_ANCHOR_REQ,
        },
        "facts": facts,
    }


# ════════════════════════════════════════════════════════════════
# ④ 自动补齐（1 LLM，仅在 should_fill 且获授权时调）
# ════════════════════════════════════════════════════════════════
async def enrich_l1(
    l1: str,
    diag: dict[str, Any],
    *,
    style: str = "",
    role_setting: str = "",
    max_chars: int = MAX_CHARS_FILL,
) -> dict[str, Any]:
    """生成补齐版一句话极简（不破坏原意）。

    返回 {ok, text, length, reason, original_facts}
    护栏：补齐后逐条断言原 facts 仍为子串；任一条丢失 → 回退原 l1
    （reason="fact_drift"）。宁可不补，不破坏。
    """
    from .llm_client import chat_completion

    l1 = (l1 or "").strip()
    if not l1:
        return {"ok": False, "text": l1, "length": 0, "reason": "empty"}

    dims = (diag or {}).get("dimensions") or {}
    gaps: list[str] = []
    if (dims.get("density") or {}).get("gap") == "thin":
        gaps.append("密度：加入 1 个关键转折或中间事件")
    missing = (dims.get("content") or {}).get("missing") or []
    if "冲突" in missing:
        gaps.append("内容：加入 1 个对手/冲突方")
    if "具体地点" in missing:
        gaps.append("内容：加入 1 个具体地点")
    if "实体" in missing:
        gaps.append("内容：加入 1 个配角")
    if "关键物/数字" in missing:
        gaps.append("质量：加入 1 个具体数字或标志性物品")
    if not gaps:
        return {"ok": False, "text": l1, "length": len(l1), "reason": "no_gap"}

    gaps_block = "\n".join(f"- {g}" for g in gaps)
    sys_p = (
        "你是「极简剧情密度补齐专家」。给定一句话极简剧情，在【不改变原意、不删除"
        f"任何已有实体/事件】的前提下，补齐指定缺口，输出仍是 ≤{max_chars} 字的一句话"
        "极简剧情。不要解释、不要引号、不要 Markdown。"
    )
    user = (
        f"【原极简剧情】\n{l1}\n"
        f"【需补齐】\n{gaps_block}\n"
        "【要求】原句所有人物名/地名/事件必须原样保留，不得改名不得丢失；"
        f"用大白话；总字数 ≤{max_chars}；直接输出补齐后的剧情本身。"
        + (f"\n【风格基调】{style}" if style else "")
        + (f"\n【角色设定】{role_setting}" if role_setting else "")
    )
    try:
        resp = await chat_completion(
            system=sys_p, user=user, call_type="capacity_enrich_l1",
            temperature=0.4, max_tokens=512,
        )
        enriched = (resp.get("content") or "").strip()
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "text": l1, "length": len(l1), "reason": f"llm_err:{str(exc)[:50]}"}

    if not enriched:
        return {"ok": False, "text": l1, "length": len(l1), "reason": "empty_out"}

    # 长度控制：超限截断到最近标点，仍超 → 回退
    if len(enriched) > max_chars:
        cut = enriched[:max_chars]
        last = max(cut.rfind("。"), cut.rfind("，"), cut.rfind("；"), cut.rfind("！"))
        enriched = cut[: last + 1] if last > 20 else cut
    if len(enriched) > max_chars + 20:
        return {"ok": False, "text": l1, "length": len(l1), "reason": "too_long"}

    # 保真护栏：原 facts 必须为子串
    original_facts = (diag or {}).get("facts") or []
    if original_facts:
        lost = [f for f in original_facts if f and f not in enriched]
        if lost:
            return {"ok": False, "text": l1, "length": len(l1),
                    "reason": "fact_drift", "lost": lost[:3]}

    return {
        "ok": True, "text": enriched, "length": len(enriched),
        "reason": "filled", "original_facts": original_facts,
    }


# ════════════════════════════════════════════════════════════════
# 模型上下文近似
# ════════════════════════════════════════════════════════════════
def model_window_chars(model: str | None) -> int:
    """模型上下文窗口的字符近似（前缀匹配；未命中返回默认 30000）。"""
    m = (model or "")
    for prefix, w in _MODEL_WINDOW_CHARS.items():
        if prefix in m:
            return w
    return _DEFAULT_WINDOW_CHARS


if __name__ == "__main__":
    # 快速自检：几条典型 l1 的容量估算
    import asyncio

    samples = [
        "主角去复仇。",
        "林九穿越到修仙界，为给妹妹治病加入魔门，在宗门大比暴露身份，被仇家追杀，反杀夺回仙丹。",
        "王平是穿越二十年的隐忍青年，在冬日寒夜被谢师兄押入道观，被迫在玄铁使施压下与苏折枝联手查断崖宗标记。",
    ]
    for s in samples:
        est = estimate_arc_capacity(s, model="qwen3.5-flash")
        print(f"[{est['confidence']}] {est['total_chapters_est']}章/{est['total_chars_est']}字 | {s[:35]}")
