"""v5.16 文本梯度改进上下文（improvement_context）。

核心心法（用户 2026-08-01 拍板）：扣分诊断 = 文本梯度，十章 = 梯度的 mini-batch。
- 每次迭代后，用算法分析当前候选在每个评分维度上的扣分原因（b6 缺锚点 /
  b7 低覆盖拍 / b8 缺失句 / b5 低语义段——这些扣分原因系统本来就算得出来，
  分散在 factual_consistency detail_results、plot_fidelity per_beat、
  select_reproduction_gaps 里，本模块把它们聚拢成结构化报告）。
- 跨章节聚合：同一复现点在多章失败 = 系统性问题，优先改进（抑制单章噪声）。
- 把系统性问题累积成【复现强调块】，注入后续候选的 user_input（开关 C：
  user_input | skeleton），让"扣分在正文、改进在 prompt"真正跨迭代生效。

配置开关（Part 4 消融找最优）：
  A = update_trigger: 强调块更新时机（champion = 换最优才更新 / each_iter = 每迭代更新）
  B = proposal_target: LLM-OPRO 提案对象（user_input | user_input+system）
  C = inject:          强调块注入位置（user_input | skeleton）

纯规则、零 embedding；b5 语义只做粗粒度提示（embedding 段分析后续增强）。
"""
from __future__ import annotations

from typing import Any


# ── v5.16 配置常量 ─────────────────────────────────────────
MULTI_BATCH = {"fast": 2, "standard": 3, "quality": 4}   # 旋转章批大小
FINAL_TOP_K = 3                                          # 终选全量复评 top-k（单章/旧行为）
# 【v5.28】多章终选复评池按挡位扩容：批内分（旋转章批）受章难度噪声主导，
# top-3 会漏掉真最优；高挡位搜索探索更多 → 复评池更大，终选更可能命中真最优。
FINAL_TOP_K_BY_MODE = {"fast": 4, "standard": 6, "quality": 8}
SYS_THRESHOLD = 2                                        # ≥N 章都失败 = 系统性问题
EMPHASIS_CAP_CHARS = 900                                 # 单章强调块注入上限（防 prompt 膨胀）
DIRECTIVE_CAP = 14                                       # 单章指令上限（优先系统性）
BEAT_LOW_COV = 0.5                                       # 拍覆盖率低于此 → 扣分指令
SEM_LOW = 0.45                                           # 语义覆盖低于此 → 粗粒度提示
HIGH_VALUE_MIN_WEIGHT = 2.0                              # 锚点扣分阈值（对齐 anchor_control）

# A / B / C 开关默认（Part 4 消融后把胜者写回这里）
# 【v5.16 消融定稿】A=each_iter（fixed-P×3章：A_each_iter FINAL pf 0.4767/fc 0.9171/sem 0.8128
# 均 A 档最佳，score 与 champion 持平 0.5388 vs 0.5386）；C=user_input（C_skeleton 无 FINAL、
# 迭代内 pf 无显著优势，保守保持）；B=user_input/预算保守（OPRO 冒烟现反向误读风险，
# 且文本梯度 FINAL 收益微弱，不放大 LLM 层）。
DEFAULT_IMPROVEMENT: dict[str, Any] = {
    "update_trigger": "each_iter",     # A: champion | each_iter（消融胜者 each_iter）
    "inject": "user_input",            # C: user_input | skeleton
    "proposal_target": "user_input",   # B: user_input | user_input+system
    "opre_budget": {"standard": 3, "quality": 6},   # LLM-OPRO 全程调用额度
    "opre_freq": 3,                    # 每 N 迭代触发一次 OPRO 提案
    "opre_history_k": 5,               # 提案 meta-prompt 用最近 K 次迭代
}

OPRO_SYSTEM_PROMPT = (
    "你是 prompt 逆向推理优化器。给定最近几次迭代的完整 prompt、生成指令、"
    "各项评分与扣分原因，输出对下一次迭代生成指令（user_input 内容块）的改进建议。"
    "要求：只输出改进块本身，不要解释；用【...】包一个区块；"
    "内容必须是可执行的生成指令（强调/补写/改写），不得提评分数字。"
)


# ── 扣分原因分析（文本梯度）────────────────────────────────

def _norm(text: str) -> str:
    from .scorer import normalize_text
    return normalize_text(text or "")


# 【v5.27】AI 味类别 → 修正动词（与 ai_flavor.build_ai_flavor_feedback 保持一致措辞）
_AI_FLAVOR_FIX_VERB = {
    "对白装饰": "对白轮之间删除装饰性动作+氛围描写，直接接续对白",
    "感官堆砌": "删除无信息量的感官/氛围词堆砌",
    "情绪明说": "删掉直接点破情绪的措辞，让动作/对白本身传达",
    "环境空镜头": "删除与情节无关的环境填充句",
    "副词冗余": "删掉「X地」「回答得X」类冗余副词",
    "微动作特写": "把逐帧微动作压缩为一个动作或不写",
    "心理独白": "压缩心理独白，保留外部可观察的动作",
    "套话过渡": "删除套话式过渡与悬念旁白",
    "其它": "删除或改写该 AI 味句子",
}


def _ai_flavor_directive(f: dict, ch_idx: int) -> dict | None:
    """单条 AI 味审阅 findings → 扣分指令（kind='ai_flavor'）。"""
    if f.get("severity") not in ("high", "medium"):
        return None
    verb = _AI_FLAVOR_FIX_VERB.get(f.get("category", "其它"),
                                   "删除或改写该 AI 味句子")
    quote = str(f.get("quote", "")).strip()
    fix = str(f.get("fix", "")).strip()
    text = f"{verb}：「{quote}」" if quote else verb
    if fix and fix != quote:
        text += f"→{fix}"
    return {"kind": "ai_flavor", "text": text[:120],
            "quote": quote, "fails": {ch_idx}}


def _chapter_deductions(chapter: dict) -> list[dict]:
    """单章扣分原因 → 指令候选。

    输入是 _evaluate_p_multi 里每个章的结果 dict（含 factual_detail /
    plot_fidelity_detail / repro_gaps / semantic_coverage）。纯 n-gram/规则。
    """
    out: list[dict] = []
    ch_idx = chapter.get("_chapter_idx", chapter.get("idx", 0))
    fc = chapter.get("factual_detail") or {}
    for d in fc.get("detail_results") or []:
        if d.get("matched"):
            continue
        if d.get("weight", 0) < HIGH_VALUE_MIN_WEIGHT:
            continue
        out.append({"kind": "anchor", "text": d.get("text", ""),
                    "type": d.get("type", ""), "fails": {ch_idx}})

    pf = chapter.get("plot_fidelity_detail") or {}
    for b in pf.get("per_beat") or []:
        cov = b.get("coverage")
        if not b.get("verifiable") or cov is None or cov >= BEAT_LOW_COV:
            continue
        out.append({"kind": "beat", "text": b.get("beat", ""),
                    "fails": {ch_idx}})

    for g in chapter.get("repro_gaps") or []:
        sent = g.get("sentence", "")
        if not sent:
            continue
        d = {"kind": "sentence", "text": sent, "fails": {ch_idx}}
        if g.get("beat_idx") is not None and g.get("beat_summary"):
            d["beat"] = g.get("beat_summary", "")
        out.append(d)

    if (chapter.get("semantic_coverage") or 0.0) < SEM_LOW:
        out.append({"kind": "semantic", "text": "保证关键情节段落完整呈现，避免内容跳跃或遗漏大段原文情节。",
                    "fails": {ch_idx}})

    # 【v5.27】AI 味审阅扣分：LLM 对照原文发现的"多写了什么"（对白装饰/氛围堆砌等）。
    # 与上面四类（anchor/beat/sentence/semantic，都是"漏了原文什么"）互补——
    # 之前迭代只反馈复现缺口，从不反馈 AI 味，这是"LLM 能看出但不改"的根因之一。
    for f in chapter.get("ai_flavor_findings") or []:
        d = _ai_flavor_directive(f, ch_idx)
        if d:
            out.append(d)
    return out


def analyze_deductions(per_chapter_results: list[dict]) -> list[dict]:
    """跨章聚合扣分指令：合并同文本指令、累计 fails、标记系统性。

    Returns:
        [{kind, text, type?, beat?, fails:set[int], count:int, systematic:bool}]，
        按系统性→count 降序。
    """
    merged: list[dict] = []
    for ch in per_chapter_results:
        for d in _chapter_deductions(ch):
            hit = None
            for m in merged:
                if m["kind"] == d["kind"] and m["text"] == d["text"]:
                    hit = m
                    break
            if hit is None:
                merged.append(d)
            else:
                hit["fails"] |= d["fails"]
    for m in merged:
        m["count"] = len(m["fails"])
        m["systematic"] = m["count"] >= SYS_THRESHOLD
    merged.sort(key=lambda m: (not m["systematic"], -m["count"]))
    return merged


# ── 强调块构建（C 开关：user_input 注入）────────────────────

def format_directive(d: dict, chapter_idx: int) -> str:
    """把一条指令格式化为对本章有效的 prompt 行（本章不在 fails 里 → None）。"""
    if chapter_idx not in d["fails"]:
        return ""
    star = "★" if d.get("systematic") else "·"
    if d["kind"] == "anchor":
        label = {"character": "人物", "number": "数字", "dialogue": "经典对白", "object": "物品/专有名词"}.get(
            d.get("type", ""), "关键细节")
        return f"{star} {label}「{d['text']}」必须原样出现"
    if d["kind"] == "beat":
        return f"{star} 情节拍「{d['text'][:40]}」必须完整呈现"
    if d["kind"] == "sentence":
        base = f"{star} 原文句必须原样复现（一字不改）「{d['text']}」"
        if d.get("beat"):
            base += f"（对应情节：{d['beat'][:30]}）"
        return base
    if d["kind"] == "semantic":
        return f"{star} {d['text']}"
    if d["kind"] == "ai_flavor":
        # 【v5.27】AI 味修正指令：明确告诉生成端"上次多写了什么、这次别写"
        return f"{star} 修正AI味：{d['text']}"
    return f"{star} {d['text']}"


def build_emphasis_block(directives: list[dict], chapter_idx: int,
                         *, cap_chars: int = EMPHASIS_CAP_CHARS) -> str | None:
    """为本章挑选有效指令 → 拼成【复现强调块】。无有效指令 → None。"""
    valid = [d for d in directives if chapter_idx in d.get("fails", set())]
    if not valid:
        return None
    valid = valid[:DIRECTIVE_CAP]
    lines = ["【复现强调块】多章训练中反复失败的复现点，本次生成必须优先保证："]
    used = 0
    for d in valid:
        line = format_directive(d, chapter_idx)
        if not line:
            continue
        if used + len(line) > cap_chars:
            break
        lines.append(line)
        used += len(line)
    if len(lines) == 1:
        return None
    return "\n".join(lines)


# ── 骨架重写（C 开关：skeleton 注入）────────────────────────

def apply_emphasis_to_skeleton(skeleton_text: str, directives: list[dict],
                               chapter_idx: int) -> str:
    """把低覆盖拍的对应原文句内联重写进骨架拍文本。

    只在"拍→原文句"对齐指令（kind=sentence 且带 beat）上生效：找到骨架里
    匹配的编号拍行，在拍文本后内联【原句：...】，让拍 distinctive 4-gram
    与原文重叠 → pf/s_char 同杠杆。骨架行结构不变（parse_beats 仍可解析）。
    """
    import re
    _num = re.compile(r"^\s*[0-9]+[.、:]\s*(.+)$")
    lines = skeleton_text.split("\n")
    for d in directives:
        if chapter_idx not in d.get("fails", set()):
            continue
        if d.get("kind") != "sentence" or not d.get("beat"):
            continue
        beat_n = _norm(d["beat"])
        sent = d["text"]
        if not beat_n or not sent:
            continue
        for i, ln in enumerate(lines):
            m = _num.match(ln)
            if not m:
                continue
            if _norm(m.group(1)) != beat_n:
                continue
            if f"原句：{sent}" in ln:
                break  # 已内联，幂等
            lines[i] = f"{ln.rstrip()}（本拍原句：{sent}）"
            break
    return "\n".join(lines)


# ── 改进上下文（跨迭代累积）────────────────────────────────

class ImprovementContext:
    """携带跨迭代的强调指令、最优分、迭代历史。

    - emphasis: {chapter_idx: [directive, ...]}（每条带累计 fails 集合）
    - 更新时机开关 A：champion（换最优才合入新指令，稳）/ each_iter（每迭代合入，快）
    """
    def __init__(self, config: dict | None = None):
        cfg = dict(DEFAULT_IMPROVEMENT)
        if config:
            cfg.update(config)
        self.cfg = cfg
        self.trigger = cfg["update_trigger"]
        self.inject = cfg["inject"]
        self.emphasis: dict[int, list[dict]] = {}
        self.champion_score = -1.0
        self.history: list[dict] = []        # 供 OPRO 用
        self.opre_used = 0
        self.opre_budget = cfg.get("opre_budget", {})
        self.extra_block: str | None = None  # OPRO 全局提案块（注入所有后续候选）

    def _merge(self, per_chapter_results: list[dict]) -> None:
        directives = analyze_deductions(per_chapter_results)
        for d in directives:
            for ch in d["fails"]:
                lst = self.emphasis.setdefault(ch, [])
                # 去重：同 kind 同文本 → 累加 fails
                hit = None
                for e in lst:
                    if e["kind"] == d["kind"] and e["text"] == d["text"]:
                        hit = e
                        break
                if hit is None:
                    lst.append(dict(d))
                else:
                    hit["fails"] |= d["fails"]

    def update(self, result: dict) -> None:
        """候选评估后更新上下文。result = _evaluate_p_multi 聚合结果。"""
        per_chapter = result.get("per_chapter") or []
        if not per_chapter:
            return
        score = result.get("score") or 0.0
        if self.trigger == "each_iter" or score > self.champion_score:
            self._merge(per_chapter)
            self.champion_score = max(self.champion_score, score)

    def block_for(self, chapter_idx: int) -> str | None:
        return build_emphasis_block(self.emphasis.get(chapter_idx, []), chapter_idx)

    def effective_block(self, chapter_idx: int) -> str | None:
        """本章注入块 = 强调块 + OPRO 全局提案块（若有）。"""
        parts = [p for p in (self.block_for(chapter_idx), self.extra_block) if p]
        return "\n\n".join(parts) if parts else None

    def rewrite_skeleton(self, skeleton_text: str, chapter_idx: int) -> str:
        return apply_emphasis_to_skeleton(skeleton_text, self.emphasis.get(chapter_idx, []), chapter_idx)

    def note_history(self, result: dict, rendered_prompt: str) -> None:
        """记录迭代历史（供 OPRO meta-prompt）。截断大字段防膨胀。"""
        per_chapter = result.get("per_chapter") or []
        deductions = analyze_deductions(per_chapter) if per_chapter else []
        summary = [{
            "kind": d["kind"], "text": (d["text"] or "")[:40],
            "fails": sorted(d["fails"]), "systematic": d.get("systematic", False),
        } for d in deductions[:8]]
        self.history.append({
            "rendered_prompt": (rendered_prompt or "")[:400],
            "score": round(result.get("score", 0), 4),
            "fc": round(result.get("factual_consistency", 0), 4),
            "pf": round(result.get("plot_fidelity", 0), 4),
            "s_char": round(result.get("s_char", 0), 4),
            "sem": round(result.get("semantic_coverage", 0), 4),
            "deductions": summary,
        })
        if len(self.history) > 10:
            self.history = self.history[-10:]

    # ── LLM-OPRO 提案层（Part 3，B 开关）──────────────────
    async def propose_user_block(self, mode: str) -> str | None:
        """OPRO：基于迭代历史+扣分报告，提案下一候选的 user_input 改进块。"""
        if mode not in self.opre_budget or self.opre_used >= self.opre_budget.get(mode, 0):
            return None
        hist = self.history[-self.cfg.get("opre_history_k", 5):]
        if not hist:
            return None
        lines = [f"最近 {len(hist)} 次迭代：" + "；".join(
            f"#{i + 1}(score={h['score']},fc={h['fc']},pf={h['pf']},s_char={h['s_char']})"
            for i, h in enumerate(hist))]
        for h in hist:
            lines.append(f"- 指令/生成块摘录：{h['rendered_prompt'][:150]}")
            if h["deductions"]:
                _ded = "；".join(f"{d['kind']}:{d['text']}" for d in h["deductions"])
                lines.append(f"  扣分原因：{_ded}")
        user = "\n".join(lines) + "\n\n请输出下一次迭代的 user_input 改进块（【...】包一个区块）。"
        from .llm_client import chat_completion, DISABLE_THINKING
        result = await chat_completion(
            system=OPRO_SYSTEM_PROMPT, user=user,
            model=None, temperature=0.6, max_tokens=600,
            extra_body=DISABLE_THINKING, call_type="opro_user_proposal",
        )
        self.opre_used += 1
        content = (result.get("content") or "").strip() if not result.get("error") else ""
        return content or None

    async def propose_system_edit(self, mode: str) -> str | None:
        """OPRO B 开关：基于历史+扣分报告，提案完整 system prompt 修订文本。

        返回修订后的完整 system prompt（由 reverse_infer 用 parse_prompt_to_p
        转回 P 向量作为独立候选评估，覆盖 b1/b4 风格维的"扣分→改进"）。
        """
        if mode not in self.opre_budget or self.opre_used >= self.opre_budget.get(mode, 0):
            return None
        hist = self.history[-self.cfg.get("opre_history_k", 5):]
        if not hist:
            return None
        best = max(hist, key=lambda h: h["score"])
        lines = [f"当前最优迭代（score={best['score']}）：", f"{best['rendered_prompt']}"]
        if best["deductions"]:
            lines.append("扣分原因：" + "；".join(
                f"{d['kind']}:{d['text']}" for d in best["deductions"][:6]))
        lines.append("最近几次迭代分数：" + "、".join(str(h["score"]) for h in hist))
        user = "\n".join(lines) + (
            "\n\n请基于以上内容，输出一份**完整的修订版 system prompt**（中文，直接给 prompt"
            "文本，不要任何解释/JSON/前后缀）。针对扣分原因调整约束与风格措辞，"
            "重点是提升对原文细节与情节的复现。")
        from .llm_client import chat_completion, DISABLE_THINKING
        result = await chat_completion(
            system="你是 prompt 逆向推理优化器，输出修订后的完整 system prompt 文本。",
            user=user, model=None, temperature=0.6, max_tokens=1500,
            extra_body=DISABLE_THINKING, call_type="opro_system_edit",
        )
        self.opre_used += 1
        content = (result.get("content") or "").strip() if not result.get("error") else ""
        return content or None
