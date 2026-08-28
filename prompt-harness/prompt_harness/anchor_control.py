"""三层量化锚点硬约束（v5.13）。

训练模块唯一目的 = 复现原文（见 [[ainovel-training-goal]]）。v5.12 的量化全在
「测量+搜索」侧，内容侧纯 LLM 自由发挥、从不做锚点级校验。本模块把量化约束
压进内容生产链路的三个层：

  L1 骨架层   —— 字面锚点校验+硬补丁（repair_skeleton_anchors）+ 语义覆盖闸门
                 （skeleton_semantic_gate），让 I3 骨架钉住 target 的锚点字面量。
  L2 生成层   —— 生成后高价值锚点校验（verify_generated_anchors）→ 反馈重生成
                 （build_anchor_feedback）→ 对白保守补丁（deterministic_anchor_patch）。
  L3 观测集闸门—— 任何观测入库前先过 gate_observation，漂移/拒答/过短直接拒绝，
                 不污染 BO 的观测集（观测 <3 时由调用方强制放行，防饿死 BO）。

不变量：测量侧与校验侧用同一套规则 —— 锚点权重、归一化、命中判定全部复用
key_details.factual_consistency_score 的 detail_results（唯一真相源）。
embedding 失败一律降级为「不判定」（unknown），绝不阻塞或误触发修复：
scorer.semantic_coverage 失败时也返回 0.0，不能把 0.0 误判为覆盖不足。
"""
from __future__ import annotations

from typing import Any


# ── 阈值常量 ──────────────────────────────────────────────
# 初值基于 2026-08-01 基线：fc 0.80-0.92 / pf ~0.40 / s_char ~0.10，
# 全部「远松于基线」→ 只拦真正漂移/拒答/过短，不误杀正常候选。
HIGH_VALUE_MIN_WEIGHT = 2.0        # 人物3.0/大额数字4.0/货币时间3.0/对白2.0 为高价值；物品1.5 排除
SKELETON_ANCHOR_CAP = 8            # L1 骨架锚点上限
FEEDBACK_ANCHOR_CAP = 5            # L2 单次反馈锚点上限
MAX_ANCHOR_RETRY = {"fast": 1, "standard": 2, "quality": 2}   # L2 反馈重试上限
SKELETON_REPAIR_LLM_MIN_MISSING = 3   # 缺 ≥3 个锚点才触发 LLM 重提（成本控制）
SKELETON_SEM_COV_MIN = 0.60        # L1b 骨架语义覆盖阈值（首轮真跑后按分布调）
GATE = {
    "fast": {
        "min_len_ratio": 0.20, "min_fc": 0.35, "min_pf": 0.15,
        "min_s_char": 0.02, "min_len_align": 0.25, "min_score": 0.15,
    },
    "standard": {
        "min_len_ratio": 0.20, "min_fc": 0.40, "min_pf": 0.20,
        "min_s_char": 0.03, "min_len_align": 0.25, "min_score": 0.18,
    },
    "quality": {
        "min_len_ratio": 0.20, "min_fc": 0.40, "min_pf": 0.20,
        "min_s_char": 0.03, "min_len_align": 0.25, "min_score": 0.18,
    },
}
REFUSAL_MARKERS = ("抱歉，我不能", "我不能帮", "作为AI", "作为 AI",
                   "无法完成这个", "拒绝回答")
ANCHOR_SECTION_KEY = "锚点"        # _parse_skeleton 用它短路跳过锚点补丁区块


# ── 公共小工具 ────────────────────────────────────────────

def _high_value_anchors(kd: dict, cap: int = SKELETON_ANCHOR_CAP) -> list[dict]:
    """从 get_key_details 的 dict 抽高价值锚点 [{type,text,weight}]，weight 降序取 cap。

    权重唯一真相源 = factual_consistency_score 的 detail_results
    （空候选 → 全部 unmatched，但 type/text/weight 齐全）。
    """
    from .key_details import factual_consistency_score
    dr = factual_consistency_score("", "", key_details=kd)["detail_results"]
    high = [d for d in dr if d.get("weight", 0) >= HIGH_VALUE_MIN_WEIGHT]
    high.sort(key=lambda d: -d["weight"])
    return high[:cap]


def _group_anchor_lines(missing: list[dict]) -> list[str]:
    """把锚点按类型分组拼成 bullet 行（对白用“”包裹保持原句）。"""
    from .ai_flavor import to_dialogue_quotes
    by_label = [
        ("character", "人物"),
        ("number", "数字/金额"),
        ("dialogue", "经典对白（原句照抄）"),
        ("object", "物品/专有名词"),
    ]
    grouped: dict[str, list[str]] = {}
    for a in missing:
        grouped.setdefault(a["type"], []).append(a["text"])
    lines: list[str] = []
    for t, label in by_label:
        texts = grouped.get(t)
        if texts:
            if t == "dialogue":
                lines.append(f"- {label}：{'；'.join('“' + to_dialogue_quotes(q) + '”' for q in texts)}")
            else:
                lines.append(f"- {label}：{'、'.join(texts)}")
    return lines


def _build_anchor_patch(missing: list[dict]) -> str:
    """把缺失锚点拼成【锚点校验补丁】bullet 区块（100% 确定性，零 LLM）。"""
    lines = ["【锚点校验补丁】以下标志性细节必须原样出现在正文中（照抄，不得改写）："]
    lines.extend(_group_anchor_lines(missing))
    return "\n".join(lines)


# ── L1 骨架层 ─────────────────────────────────────────────

def _missing_anchors(skeleton_text: str, kd: dict, *, cap: int = SKELETON_ANCHOR_CAP) -> list[dict]:
    """骨架里缺失的高价值锚点（weight≥2.0 且未命中）。

    与评分侧同一套判定：factual_consistency_score 对 skeleton 做归一化子串匹配。
    """
    from .key_details import factual_consistency_score
    fc = factual_consistency_score(skeleton_text, target="", key_details=kd)
    missing = [d for d in fc["detail_results"]
               if not d["matched"] and d.get("weight", 0) >= HIGH_VALUE_MIN_WEIGHT]
    missing.sort(key=lambda d: -d["weight"])
    return missing[:cap]


async def _llm_re_extract(
    target_text: str,
    missing: list[dict],
    *,
    low_segments: list[int] | None = None,
) -> str | None:
    """定向 LLM 重提骨架（一次 chat_completion，沿用现有瞬时错误重试）。

    提示包含缺失锚点 +（可选）语义低覆盖的原文分段。重提后由调用方再 verify。
    ⚠️ 函数内 lazy import SKELETON_SYSTEM_PROMPT（plot_skeleton.py），
    避免 anchor_control ↔ plot_skeleton 互相 import 死循环。
    """
    from .llm_client import chat_completion, DISABLE_THINKING
    from .plot_skeleton import SKELETON_SYSTEM_PROMPT

    parts: list[str] = []
    if missing:
        hint = "、".join(a["text"] for a in missing[:6])
        parts.append(f"以下标志性细节必须原样保留在骨架中（照抄，不得改写）：{hint}")
    if low_segments:
        from .scorer import _chunk_text
        tgt_chunks = _chunk_text(target_text, 4)
        seg_hints = [
            f"第{i + 1}段：{tgt_chunks[i][:120]}……" for i in low_segments if i < len(tgt_chunks)
        ]
        if seg_hints:
            parts.append("以下原文片段语义覆盖偏低，骨架中必须覆盖到对应内容：\n" + "\n".join(seg_hints))
    hint_block = "\n".join(parts) if parts else ""

    user = f"""请将以下小说文本去风格化，提取纯情节骨架。

{hint_block}

原文（共 {len(target_text)} 字）：
---
{target_text}
---
直接输出骨架内容，不要其他说明。"""

    result = await chat_completion(
        system=SKELETON_SYSTEM_PROMPT,
        user=user,
        model=None,  # chat_completion 内部按 5 级优先级解析模型（调用时，规避 SETTINGS 导入期冻结）
        temperature=0.3,
        max_tokens=4096,
        extra_body=DISABLE_THINKING,
        call_type="skeleton_re_extract",
    )
    if result.get("error"):
        return None
    return (result.get("content") or "").strip()


async def repair_skeleton_anchors(
    skeleton_text: str,
    target_text: str,
    *,
    llm_repair: bool = False,
    cap: int = SKELETON_ANCHOR_CAP,
) -> dict[str, Any]:
    """L1a：骨架字面锚点校验 + 硬补丁。

    缺锚点 → 确定性 append【锚点校验补丁】区块（零 LLM，100% 保证）；
    可选 LLM 重提（缺 ≥ SKELETON_REPAIR_LLM_MIN_MISSING 才触发），
    重提后仍缺 → append 兜底。幂等：已修复骨架再调 → missing=[]、method="none"。

    Returns:
        {skeleton_text, missing, fixed, checked, appended_block, repaired, method}
        method ∈ {"none", "append", "llm", "llm+append"}
    """
    from .key_details import get_key_details

    kd = get_key_details(target_text)
    checked = _high_value_anchors(kd, cap=cap)
    initial_missing = _missing_anchors(skeleton_text, kd, cap=cap)
    missing = initial_missing

    new_skeleton = skeleton_text
    method = "none"
    appended_block = ""

    if missing:
        if llm_repair and len(missing) >= SKELETON_REPAIR_LLM_MIN_MISSING:
            re_text = await _llm_re_extract(target_text, missing)
            if re_text:
                re_missing = _missing_anchors(re_text, kd, cap=cap)
                if len(re_missing) < len(missing):
                    new_skeleton = re_text
                    missing = re_missing
                    method = "llm"
        # 兜底：仍缺 → 确定性 append（也覆盖 LLM 重提失败/未改善）
        if missing:
            appended_block = _build_anchor_patch(missing)
            new_skeleton = new_skeleton.rstrip() + "\n\n" + appended_block
            method = "llm+append" if method == "llm" else "append"
            missing = []  # 字面量已进骨架，视为补齐

    remaining_texts = {d["text"] for d in missing}
    fixed = [a["text"] for a in initial_missing if a["text"] not in remaining_texts]

    return {
        "skeleton_text": new_skeleton,
        "missing": missing,              # 修复后仍缺的（append 后为空）
        "fixed": fixed,                  # 本次实际修复的锚点文本
        "checked": [d["text"] for d in checked],
        "appended_block": appended_block,
        "repaired": method != "none",
        "method": method,
    }


async def skeleton_semantic_gate(
    skeleton: str,
    target: str,
    n_segments: int = 4,
) -> dict[str, Any]:
    """L1b：骨架对原文的语义覆盖闸门。

    自己调 get_embeddings（复刻 scorer.semantic_coverage 的分段余弦逻辑），
    返回 {sem_cov, per_segment, low_segments}。
    **任一 embedding 为 None → 返回 {unknown:True} 跳过闸门**——
    不能把 0.0 误判为覆盖不足触发修复（scorer.semantic_coverage 失败也返回 0.0）。
    """
    from .embed_client import get_embeddings
    from .scorer import _chunk_text, cosine_similarity

    skel_chunks = _chunk_text(skeleton, n_segments)
    tgt_chunks = _chunk_text(target, n_segments)
    if not skel_chunks or not tgt_chunks:
        return {"unknown": True, "sem_cov": 0.0, "per_segment": [], "low_segments": []}

    emb_s = await get_embeddings(skel_chunks)
    emb_t = await get_embeddings(tgt_chunks)
    if any(e is None for e in emb_s) or any(e is None for e in emb_t):
        return {"unknown": True, "sem_cov": 0.0, "per_segment": [], "low_segments": []}

    per_segment = [
        max(0.0, min(1.0, cosine_similarity(es, et)))
        for es, et in zip(emb_s, emb_t)
    ]
    sem_cov = round(sum(per_segment) / len(per_segment), 4) if per_segment else 0.0
    low_segments = [i for i, s in enumerate(per_segment) if s < SKELETON_SEM_COV_MIN]
    return {
        "unknown": False,
        "sem_cov": sem_cov,
        "per_segment": per_segment,
        "low_segments": low_segments,
    }


# ── L2 生成层 ─────────────────────────────────────────────

def verify_generated_anchors(generated: str, kd: dict, *, cap: int = FEEDBACK_ANCHOR_CAP) -> dict[str, Any]:
    """L2：生成文本对高价值锚点的保留校验。

    复用 factual_consistency_score 的 detail_results（同一套权重/归一化/命中判定），
    过滤 weight≥2.0 且未命中 → 缺失锚点（weight 降序取 cap）。
    """
    from .key_details import factual_consistency_score
    fc = factual_consistency_score(generated, target="", key_details=kd)
    missing = [d for d in fc["detail_results"]
               if not d["matched"] and d.get("weight", 0) >= HIGH_VALUE_MIN_WEIGHT]
    missing.sort(key=lambda d: -d["weight"])
    return {
        "missing": missing[:cap],
        "score": fc["score"],
        "all_present": not missing,
    }


def build_anchor_feedback(missing: list[dict], *, attempt: int = 0) -> str:
    """L2：把缺失锚点拼成生成侧反馈指令（追加进 user_input 后重生成）。"""
    if not missing:
        return ""
    lines = [f"【缺失锚点补丁】你上一稿遗漏了以下标志性细节（第 {attempt + 1} 次补写），"
             "本次必须原样补写进正文："]
    lines.extend(_group_anchor_lines(missing))
    return "\n".join(lines)


def deterministic_anchor_patch(generated: str, missing: list[dict]) -> tuple[str, list]:
    """L2 兜底：只对经典对白做保守插入。

    在「角色+说/道：」位置后补插原句引号（推动情节/塑造人物的对白可复现）。
    数字/人物不硬插 —— 宁缺勿污，交给 L3 拒收，不污染正文；
    无合适插入点 → 返回原文本。

    Returns:
        (patched_text, inserted_quotes) — 未补插时 inserted_quotes 为空。
    """
    if not missing:
        return generated, []
    dias = [a for a in missing if a["type"] == "dialogue"]
    if not dias:
        return generated, []

    import re
    from .ai_flavor import to_dialogue_quotes
    from .scorer import normalize_text

    patched = generated
    inserted: list[str] = []
    # 「XX说：」「XX道：」「XX喊道：」等叙述位置
    pattern = re.compile(r'([^。！？\n]{0,12}?[说道喊]道?[:：])')
    for anchor in dias:
        q = anchor["text"]
        if not q:
            continue
        # 归一化后已命中 → 是标点差异误报，跳过不重复插入
        if normalize_text(q) in normalize_text(patched):
            continue
        matches = list(pattern.finditer(patched))
        if not matches:
            continue
        m = matches[-1]
        pos = m.end()
        patched = patched[:pos] + "“" + to_dialogue_quotes(q) + "”" + patched[pos:]
        inserted.append(q)
    return patched, inserted


# ── L2b 原文复现反馈（v5.14→v5.15）────────────────────────
# 把 L2 锚点反馈泛化为「字面复现反馈」：找 target 里字面复现收益最大的句子
# （s_char 杠杆 b8=0.30 权重最高却数值最低 ~0.10）+ pf 低覆盖拍的对应原句
# （pf 杠杆 b7=0.22）。纯 n-gram 重叠、零 embedding、零 LLM。
# v5.15（用户反馈提升还是太低）把已激活的杠杆真正部署开：
#   ① pf 对齐阈值 0.2→0.1 + 跳过元数据拍（时间/地点/出场人物等不可叙述行）
#     → 19 低覆盖拍从「0 对齐入选」变为事件拍 0.17-0.27 重叠都能对齐
#   ② 选句分槽：pf 槽（≤4 保证对齐句）优先 + s_char 槽（copyability 加权短句）
#     → 不再选 49-52 字长复杂句（最难逐字复现）
#   ③ 预算解耦：repro_ctx 额度独立于 MAX_ANCHOR_RETRY（此前单计数器封顶花不掉）
REPRO_GAP_CAP = 8                  # 单次反馈句子上限（v5.15: 4→8）
REPRO_PF_SLOTS = 4                 # 其中 pf 对齐句保底槽位（优先保证）
REPRO_MIN_GAIN_PER_SENT = 0.005    # s_char 槽单句增益下限（低于不选，省生成）
REPRO_MIN_TOTAL_GAIN = 0.02        # 触发重试的合并增益下限（占 target 总 n-gram 比）
REPRO_LOW_BEAT_COV = 0.5           # 拍覆盖率低于此 → 触发 pf 反馈
REPRO_ALIGN_MIN_OVERLAP = 0.1      # 拍→原句对齐最低 distinctive 4-gram 重叠（v5.15: 0.2→0.1）
REPRO_META_PREFIXES = ("时间：", "地点：", "出场人物：", "人物：", "环境：", "场景：")  # 骨架元数据拍（不可叙述，跳过 pf 对齐）
REPRO_COPY_LEN_MIN = 10            # s_char 槽偏好句长区间 [10,30]（短句可复现性高）
REPRO_COPY_LEN_MAX = 30
REPRO_RUN_BUDGET = {"fast": 8, "standard": 12, "quality": 16}  # 纯复现重试全程总额度（v5.15.2：4/6/10 → 8/12/16，实测 v5.15.1 fast 预算 4 只够 4/8 迭代做第 2 次复现重试，后 4 迭代 s_char 掉到 0.16）
REPRO_ITER_CAP = 2                 # 单迭代纯复现重试上限（v5.15 新）
REPRO_NS = (3, 4, 5)               # 对齐 char_ngram_containment 的 n-gram 阶数


def select_reproduction_gaps(
    generated: str,
    target_text: str,
    per_beat: list[dict],
    *,
    cap: int = REPRO_GAP_CAP,
) -> list[dict]:
    """L2b：找「字面复现收益最大」的 target 句子（s_char 杠杆 + pf 低覆盖拍杠杆）。

    纯 n-gram 重叠、零 embedding、零 LLM。v5.15 分两槽：
    - **pf 槽（≤REPRO_PF_SLOTS，优先保证）**：低覆盖 EVENT 拍（跳过时间/地点/出场
      人物等元数据拍）→ distinctive 4-gram 单调贪心对齐 target 原句（阈值 0.1）。
      复现该句直接抬 b7（拍 distinctive 内容进 generated）。
    - **s_char 槽（剩余 ≤cap）**：copyability 加权 —— 偏好 10-30 字短句
      （长句可复现性差）、对白引号/数字加分；贪心 max-coverage（已选句 n-gram
      从剩余句扣除，避免共享 n-gram 重复计增益）。

    Returns:
        [{sentence, gain_estimate, missing_count, beat_idx?, beat_summary?}]（≤cap）。
        pf 槽句在前（带 beat 标注）；s_char 槽句补足。
        全空（无缺失句）→ 调用方不重试，省生成。
    """
    from .scorer import normalize_text, char_ngrams, split_sentences

    gen_norm = normalize_text(generated)
    tgt_norm = normalize_text(target_text)
    if not gen_norm or not tgt_norm:
        return []

    # target 总 n-gram 数（分母，对齐 char_ngram_containment）
    denom = sum(len(char_ngrams(tgt_norm, n)) for n in REPRO_NS)
    if denom == 0:
        return []

    # generated 已含的 n-gram（n=3/4/5 并集）
    gen_ng: set[str] = set()
    for n in REPRO_NS:
        gen_ng |= char_ngrams(gen_norm, n)

    # 逐拍 distinctive 4-gram（与 plot_fidelity.beat_sequence_fidelity 同式）
    beat_norms = [normalize_text(b.get("beat") or "") for b in per_beat]
    beat_sets = [char_ngrams(bn, 4) for bn in beat_norms]
    distinctives: list[set[str]] = []
    for i in range(len(beat_sets)):
        others: set[str] = set()
        for j, s in enumerate(beat_sets):
            if j != i:
                others |= s
        distinctives.append(beat_sets[i] - others)

    # target 句子（长度过滤：太短无信息 / 太长占 prompt）
    sents = [s for s in split_sentences(target_text) if 6 <= len(s) <= 80]

    selected: list[dict] = []
    selected_idx: set[int] = set()
    covered: set[str] = set()  # 已选句 n-gram 并集（贪心去重）

    def _miss_of(sn: str) -> set[str]:
        m: set[str] = set()
        for n in REPRO_NS:
            m |= char_ngrams(sn, n)
        m -= gen_ng
        m -= covered
        return m

    # ── 1) pf 槽：低覆盖 EVENT 拍 → 单调贪心对齐 target 原句 ──
    # 保序（last_j 只前进）、去重句、≤REPRO_PF_SLOTS。对齐成功才入槽；
    # 对齐失败不兜底（拍文本已在骨架里，兜底纯重复）。
    last_j = -1
    for i, b in enumerate(per_beat):
        if len(selected) >= cap or len(selected_idx) >= REPRO_PF_SLOTS:
            break
        if not b.get("verifiable"):
            continue
        beat_txt = b.get("beat") or ""
        if beat_txt.startswith(REPRO_META_PREFIXES):
            continue  # 元数据拍（时间/地点/出场人物）不可叙述，跳过
        cov = b.get("coverage")
        if cov is None or cov >= REPRO_LOW_BEAT_COV:
            continue
        d = distinctives[i]
        if not d:
            continue
        best_j, best_overlap = -1, 0.0
        for j in range(last_j + 1, len(sents)):
            sn = normalize_text(sents[j])
            if len(sn) < 4:
                continue
            ov = len(d & char_ngrams(sn, 4)) / len(d)
            if ov > best_overlap:
                best_overlap, best_j = ov, j
        if best_j >= 0 and best_overlap >= REPRO_ALIGN_MIN_OVERLAP:
            sn = normalize_text(sents[best_j])
            miss = _miss_of(sn)
            last_j = best_j
            if not miss:
                continue
            selected.append({
                "sentence": sents[best_j],
                "gain_estimate": round(len(miss) / denom, 5),
                "missing_count": len(miss),
                "beat_idx": i,
                "beat_summary": beat_txt[:40],
            })
            selected_idx.add(best_j)
            covered |= miss

    # ── 2) s_char 槽：copyability 加权贪心 max-coverage ──
    def _copy_score(miss_count: int, s: str) -> float:
        n = len(s)
        if REPRO_COPY_LEN_MIN <= n <= REPRO_COPY_LEN_MAX:
            lf = 1.0
        elif n < REPRO_COPY_LEN_MIN:
            lf = 0.6
        elif n <= 50:
            lf = 0.5
        else:
            lf = 0.25
        boost = 1.0
        if "「" in s or "“" in s or '"' in s:
            boost += 0.3
        if any(ch.isdigit() for ch in s):
            boost += 0.2
        return miss_count * lf * boost

    pool: list[dict] = []
    for j, s in enumerate(sents):
        if j in selected_idx:
            continue
        sn = normalize_text(s)
        miss = _miss_of(sn)
        if not miss:
            continue
        pool.append({
            "sentence": s, "miss": miss, "j": j,
            "gain_estimate": len(miss) / denom,
            "missing_count": len(miss),
        })
    while pool and len(selected) < cap:
        for c in pool:
            c["score"] = _copy_score(c["missing_count"], c["sentence"])
        pool.sort(key=lambda c: c["score"], reverse=True)
        best = pool[0]
        if best["gain_estimate"] < REPRO_MIN_GAIN_PER_SENT:
            break
        selected.append({
            "sentence": best["sentence"],
            "gain_estimate": round(best["gain_estimate"], 5),
            "missing_count": best["missing_count"],
        })
        selected_idx.add(best["j"])
        covered |= best["miss"]
        new_pool: list[dict] = []
        for c in pool[1:]:
            new_miss = c["miss"] - best["miss"]
            if not new_miss:
                continue
            c["miss"] = new_miss
            c["gain_estimate"] = len(new_miss) / denom
            c["missing_count"] = len(new_miss)
            new_pool.append(c)
        pool = new_pool
    return selected


def build_reproduction_feedback(gaps: list[dict], *, attempt: int = 0) -> str:
    """L2b：把复现缺口拼成生成侧反馈指令（追加进 user_input 后重生成）。

    v5.15 措辞强化：pf 对齐句（带 beat_idx）标注「必须原样写入该情节处，一字不改」。
    v5.17 织入版（harness g13→g14 实测移植）：把「原样写入」改为「织入对应情节叙述流、
    不单独成行」——原措辞让模型把 10-30 字可复制短句写成孤立成行碎片（亲戚 cohesion
    0.48→0.10，s_char 涨但 prose 断）；织入版救回 0.48，g14 s_char 0.683 历史最佳。
    """
    if not gaps:
        return ""
    lines = [f"【原文复现提示】你上一稿未完整复现以下原文关键句（第 {attempt + 1} 次补写）。"
             "请把它们织入对应情节的叙述流中：当场景推进到该处时自然写出这些句子，"
             "与上下文句句衔接、连成连贯段落；不得单独成段、不得堆砌孤句。"
             "可做衔接性微调，但关键措辞不得整体改写："]
    for i, g in enumerate(gaps, 1):
        if g.get("beat_idx") is not None and g.get("beat_summary"):
            lines.append(f"{i}. （对应情节：{g['beat_summary']}）"
                         f"在此处自然写出：「{g['sentence']}」")
        else:
            lines.append(f"{i}. 「{g['sentence']}」")
    lines.append("以上句子必须全部出现在正文中，且与所在段落连贯、不单独成行。")
    return "\n".join(lines)


# ── L3 观测集闸门 ─────────────────────────────────────────

def gate_observation(
    result: dict,
    *,
    mode: str = "quality",
    target_len: int,
) -> tuple[bool, str]:
    """L3：观测集入库闸门（硬执行）。

    短路判定：error / 空文本 / 长度比 / REFUSAL_MARKERS / fc / pf / s_char /
    len_align / score。观测 <3 时由调用方强制放行（防饿死 BO）。

    Returns:
        (passed, reason) — passed=False 时 reason 为拒绝原因（入 gate_rejects）。
    """
    g = GATE.get(mode, GATE["quality"])
    if result.get("error"):
        return False, "error"
    generated = result.get("generated_text") or ""
    if not generated or not generated.strip():
        return False, "empty"
    if target_len > 0:
        len_ratio = len(generated) / target_len
        if len_ratio < g["min_len_ratio"]:
            return False, f"len_ratio:{len_ratio:.2f}"
    lower = generated.lower()
    if any(m.lower() in lower for m in REFUSAL_MARKERS):
        return False, "refusal"
    if result.get("factual_consistency", 0.0) < g["min_fc"]:
        return False, f"fc:{result.get('factual_consistency', 0.0):.2f}"
    if result.get("plot_fidelity", 0.0) < g["min_pf"]:
        return False, f"pf:{result.get('plot_fidelity', 0.0):.2f}"
    if result.get("s_char", 0.0) < g["min_s_char"]:
        return False, f"s_char:{result.get('s_char', 0.0):.2f}"
    if result.get("length_alignment", 0.0) < g["min_len_align"]:
        return False, f"len_align:{result.get('length_alignment', 0.0):.2f}"
    if result.get("score", 0.0) < g["min_score"]:
        return False, f"score:{result.get('score', 0.0):.2f}"
    return True, "pass"
