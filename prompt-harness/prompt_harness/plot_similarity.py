# -*- coding: utf-8 -*-
"""v5.20 生产剧情相似度模块：0.3 权重维度（句子级最大余弦均值）。

从 v5.19 harness（harness_scoring.py）原样移植，零生产耦合：
  - segment_into_plots：原文按 n 范围分段（整段吸附、绝不拆段）
  - LLM 剧情概括：5 段/次批量 + 信号量并发 6，持久缓存跨 run 复用
  - generated 按句切（短句并入前句、绝不拆句），句 embedding 限流安全分批
  - 每 beat 与全部 generated 句子的最大余弦 → 均值（锁定指标，见 v5.19 B+ 扫描）

缓存（防重复烧 doubao/embedding）：
  - 目标侧概括：持久缓存 SETTINGS.data_dir/plot_summaries_v519.json，
    key={hash(target)}|{n_min}-{n_max}|{seg_idx}（同章跨 run 只概括一次）
  - beat 嵌入：进程内 dict，key={hash(target)}|{n_min}-{n_max}
  - generated 句嵌入：进程内 OrderedDict（cap 128 防膨胀），key={hash(generated)}

生产与 harness 差异：
  - 不显式传 model/base_url/api_key → chat_completion 三层回退（SETTINGS→env→.env）
  - n 范围 / 限流间隔从 config.SETTINGS 读（v519_*）
  - 目标 key 用 target 文本 hash（无章节名，多章/跨 run 稳定）
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import re
from collections import OrderedDict
from typing import Any

from .config import SETTINGS
from .scorer import split_sentences

# 剧情概括并发/批：doubao-seed 单次 20-50s，串行 236 段 2h+；
# 批量 5 段/次摊销固定延迟 + 信号量并发 6（实测升 10 端点排队，均延迟 37→56s）。
_SUMMARY_SEMAPHORE = asyncio.Semaphore(6)
SUMMARY_BATCH = 5

# 嵌入限流：embed_client 批量非 200 直接返回 [None]*len（无重试），必须包一层。
_EMBED_SEMAPHORE = asyncio.Semaphore(4)
_EMBED_RETRIES = 6                 # 429/失败退避重试（2s 起，指数翻倍，cap 12s）
_MIN_SENT_LEN = 8                  # 过短句并入前句（对白叹词/转场碎片是噪声）
_MAX_MERGE_LEN = 40                # 短句并入后上限
_EMBED_BATCH = 10                  # Embeddings API 单次 input 上限

# 缓存
_SUMMARY_CACHE: dict | None = None           # 懒加载（data_dir 启动后才定）
_SUMMARY_CACHE_LOCK = asyncio.Lock()
_BEAT_EMB_CACHE: dict = {}                   # 进程内 beat 嵌入
_SENT_EMB_CACHE: OrderedDict = OrderedDict()  # 进程内句嵌入（cap 128 防膨胀）
_SENT_EMB_CAP = 128

_embed_gap_lock = asyncio.Lock()
_embed_last_ts = 0.0


def _target_hash(text: str) -> str:
    """目标/生成文本的稳定短 hash（缓存 key 用）。"""
    return hashlib.md5((text or "").encode("utf-8")).hexdigest()[:16]


def _summary_cache_path():
    return SETTINGS.data_dir / "plot_summaries_v519.json"


async def _ensure_summary_cache() -> dict:
    """懒加载持久缓存（data_dir 在 init_settings 后才定，不能模块导入期读）。"""
    global _SUMMARY_CACHE
    if _SUMMARY_CACHE is None:
        async with _SUMMARY_CACHE_LOCK:
            if _SUMMARY_CACHE is None:
                path = _summary_cache_path()
                try:
                    if path.exists():
                        with open(path, encoding="utf-8") as f:
                            _SUMMARY_CACHE = json.load(f)
                    else:
                        _SUMMARY_CACHE = {}
                except Exception:
                    _SUMMARY_CACHE = {}
    return _SUMMARY_CACHE


async def _persist_summary_cache() -> None:
    """写回持久缓存（并发下加锁防 JSON 损坏）。"""
    try:
        async with _SUMMARY_CACHE_LOCK:
            if _SUMMARY_CACHE is None:
                return
            with open(_summary_cache_path(), "w", encoding="utf-8") as f:
                json.dump(_SUMMARY_CACHE, f, ensure_ascii=False)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# 剧情分段（n 范围 + 整段吸附，绝不拆段）
# ---------------------------------------------------------------------------

def segment_into_plots(text: str, n_min: int, n_max: int) -> list[str]:
    """按 n 范围 [n_min, n_max] 把原文切成剧情段（原样移植自 harness_scoring）。

    规则（用户口径）：
      - paras = 按换行切分（兼容 \\r\\n），每段=一个完整段落，先 strip
      - cur 逐段累加；当 cur 非空 且 len(cur)+len(para) > n_max 且 len(cur) >= n_min
        → 在「当前段尾」（上一段最后一字）收一段，para 起新段
      - 否则 cur += para（整段并入，绝不在段落中间切）
      - 单个 para 自身 > n_max → 该 para 单独成段（无法避免的越界，仍不拆段）
      - 末尾残留 cur 收尾
    每段终点 = 段落结束的最后一字；段长落在 [n_min, n_max] 附近（被段界吸附）。
    """
    raw = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    paras = [p.strip() for p in raw.split("\n") if p.strip()]
    if not paras:
        return []

    segs: list[str] = []
    cur: list[str] = []
    cur_len = 0
    for para in paras:
        plen = len(para)
        # 单段超长（无论 cur 是否非空）：cur 若非空先收段，然后该段单独成段
        if plen > n_max:
            if cur:
                segs.append("\n".join(cur))
                cur, cur_len = [], 0
            segs.append(para)
            continue
        if cur and cur_len + plen > n_max and cur_len >= n_min:
            # 在「当前段尾」收一段，para 起新段
            segs.append("\n".join(cur))
            cur, cur_len = [para], plen
        else:
            cur.append(para)
            cur_len += plen
    if cur:
        segs.append("\n".join(cur))
    return segs


# ---------------------------------------------------------------------------
# 剧情概括（LLM 批量 + 信号量并发）
# ---------------------------------------------------------------------------

async def _summarize_segments_batch(segs: list[str]) -> list[str]:
    """一次 LLM 调用概括多段（编号列表），返回与 segs 等长的列表；失败段空串。

    输出格式「1. 概括」逐行解析；解析失败段由调用方回退用原句。
    """
    from .llm_client import chat_completion

    n = len(segs)
    if n == 0:
        return []
    body = "\n".join(f"[{i + 1}]\n{s}" for i, s in enumerate(segs))
    sys_prompt = (
        f"你是剧情概括器。把下面编号的 {n} 个小说段落各概括成一句不超过50字的剧情要点，"
        "只保留事件、人物、数字，去掉修辞、描写、氛围。严格按编号逐行输出，每行一条，"
        '格式「1. 概括」，不要任何解释或标题。'
    )
    user = f"请概括以下 {n} 段：\n{body}"
    async with _SUMMARY_SEMAPHORE:
        resp = await chat_completion(
            system=sys_prompt,
            user=user,
            temperature=0.2,
            top_p=0.5,
            max_tokens=n * 80,
            call_type="plot_segment_summary_batch",
        )
    content = (resp.get("content") or "").strip()
    if resp.get("error") or not content:
        return [""] * n
    out = [""] * n
    for line in content.splitlines():
        line = line.strip()
        if not line:
            continue
        m = re.match(r"^(\d{1,2})[\.、)．]\s*(.*)$", line)
        if m:
            idx = int(m.group(1)) - 1
            if 0 <= idx < n:
                out[idx] = (m.group(2) or "")[:80]
    return out


# ---------------------------------------------------------------------------
# generated 句子切分 + 限流安全嵌入
# ---------------------------------------------------------------------------

def _chunk_generated_sentences(generated: str) -> list[str]:
    """generated 按句切分（split_sentences 同一套分隔符），过短句并入前一句；绝不拆句。

    短句（对白叹词/转场碎片）嵌入质量差且是噪声源，并入前句提高匹配精度。
    """
    sents = split_sentences(generated or "")
    out: list[str] = []
    for s in sents:
        s = s.strip()
        if not s:
            continue
        if out and len(s) < _MIN_SENT_LEN and len(out[-1]) + len(s) <= _MAX_MERGE_LEN:
            out[-1] = out[-1] + s
        else:
            out.append(s)
    return out


async def _throttle_embed() -> None:
    """全局最小间隔：相邻 embedding HTTP 请求至少隔 SETTINGS.v519_embed_gap 秒。"""
    global _embed_last_ts
    loop = asyncio.get_running_loop()
    async with _embed_gap_lock:
        now = loop.time()
        gap = max(0.0, SETTINGS.v519_embed_gap)
        wait = gap - (now - _embed_last_ts)
        if wait > 0:
            await asyncio.sleep(wait)
        _embed_last_ts = loop.time()


async def _safe_embed_batch(texts: list[str]) -> list:
    """限流安全嵌入一个子批（≤10 条）：并发受限 + 请求间隔 + 失败退避重试。

    embed_client 批量非 200（含 429）直接返回 [None]*len，无重试——这里对
    含 None 的结果退避重试（2s 起，指数翻倍），最后一次仍返回（调用方跳过 None）。
    """
    from .embed_client import get_embeddings

    async with _EMBED_SEMAPHORE:
        embs: list = []
        for attempt in range(_EMBED_RETRIES):
            await _throttle_embed()
            embs = await get_embeddings(texts)
            if all(e is not None for e in embs):
                return embs
            if attempt < _EMBED_RETRIES - 1:
                await asyncio.sleep(min(2 ** attempt, 12))
    return embs


async def safe_get_embeddings(texts: list[str]) -> list:
    """限流安全批量嵌入：自己分批 ≤10（重试粒度细），子批并发过 semaphore。

    返回与 texts 等长的列表（可能含 None，失败段在调用方跳过）。
    """
    subs = [texts[i:i + _EMBED_BATCH] for i in range(0, len(texts), _EMBED_BATCH)]
    if not subs:
        return []
    results = await asyncio.gather(*(_safe_embed_batch(s) for s in subs))
    return [e for r in results for e in r]


# ---------------------------------------------------------------------------
# 对白轮保真（v5.21）：原文对白轮序列 vs 生成对白轮序列，缺失/乱序同时惩罚
# ---------------------------------------------------------------------------

_QUOTE_RE = re.compile(r'[「“"]([^「」“”"]+)[」”"]')


def _norm_turn(q: str) -> str:
    """对白轮归一化：去空白、去尾部标点，只留核心内容做匹配（消除全/半角差异）。"""
    t = (q or "").replace("　", "").replace(" ", "").replace("　", "").strip()
    return t.rstrip("。！？!?.…～~ .,;；:：")


def _extract_dialogue_turns(text: str) -> list[str]:
    """按序提取全部对白轮（引号内容，归一化）。全量有序，不按 salience 截断。

    归一化后为空串的轮（纯省略号/标点的引号，如「……」）不承载内容，直接过滤
    ——否则匹配时永远不达标，只会在对齐里当恒失配噪声。
    """
    if not text:
        return []
    return [q for q in (_norm_turn(x) for x in re.findall(_QUOTE_RE, text)) if q]


def _turn_grams(t: str) -> set[str]:
    """字符二元组（重叠窗口），保留字内顺序；短于 2 字退回单字符集合。"""
    if len(t) <= 1:
        return set(t)
    return {t[i:i + 2] for i in range(len(t) - 1)}


def _turn_char_sim(a: str, b: str) -> float:
    """对白轮相似度：字符二元组交集比（保留字内顺序，比字符集更判别）。

    二元组对 2-8 字短轮判别力高于字符集："我没有" vs "我没有" → 1.0，
    vs "我不是" → 2/3；字符集对同集异序（"你自己"/"己自你"）误判 1.0，二元组则 0。
    """
    if not a or not b:
        return 0.0
    ga, gb = _turn_grams(a), _turn_grams(b)
    return len(ga & gb) / max(1.0, max(len(ga), len(gb)))


def _dp_turn_fidelity(
    target_turns: list[str],
    gen_turns: list[str],
    thresh: float,
) -> tuple[float, float, int]:
    """最优有序对齐（模糊 LCS + 软阈值连续奖励），同时惩罚缺失/乱序/错配。

    dp[i][j] = 前 i 个 target 轮与前 j 个 gen 轮对齐的最大累计奖励：
      - 缺失（跳过 target 轮）：dp[i-1][j] + 0
      - 插入（跳过 gen 轮，生成注水不惩罚）：dp[i][j-1] + 0
      - 匹配：dp[i-1][j-1] + (sim if sim>=thresh else 0)  ← 达标才给连续奖励
    对齐天然不交叉 → 逆序轮无法同时被匹配，乱序被惩罚（非贪心，最优解）。
    fidelity = dp[n][m] / n（每 target 轮奖励上限 1，缺失贡献 0）。
    无 target 轮 → 1.0（无对白无缺陷）。
    """
    n = len(target_turns)
    if n == 0:
        return 1.0, 0.0, 0
    if not gen_turns:
        return 0.0, 0.0, n
    m = len(gen_turns)
    prev = [0.0] * (m + 1)
    for t in target_turns:
        cur = [0.0] * (m + 1)
        for j in range(1, m + 1):
            s = _turn_char_sim(t, gen_turns[j - 1])
            r = s if s >= thresh else 0.0
            cur[j] = max(prev[j], cur[j - 1], prev[j - 1] + r)
        prev = cur
    return prev[-1] / n, prev[-1], n


def _turn_recall_present(
    target_turns: list[str],
    gen_turns: list[str],
    thresh: float,
) -> float:
    """仅度量「有没有」：每 target 轮是否在 gen 中任意位置被匹配（不看顺序）。

    与 _dp_turn_fidelity 的差异即乱序损失：fidelity < recall ⇔ 存在乱序。
    """
    n = len(target_turns)
    if n == 0:
        return 1.0
    if not gen_turns:
        return 0.0
    hit = 0
    for t in target_turns:
        if any(_turn_char_sim(t, g) >= thresh for g in gen_turns):
            hit += 1
    return hit / n


def dialogue_turn_fidelity(generated: str, target: str) -> dict:
    """对白轮保真（诊断入口，零 API）：target 对白轮 vs generated 对白轮。

    返回 {fidelity, recall, n_target_turns, n_gen_turns}。
    fidelity = 最优有序对齐奖励比例（缺失+乱序+错配同时惩罚，DP 精确解）；
    recall = 只看「有没有」的命中比例（fidelity < recall ⇔ 存在乱序）。
    """
    gen_turns = _extract_dialogue_turns(generated)
    tgt_turns = _extract_dialogue_turns(target)
    thresh = float(getattr(SETTINGS, "v521_turn_match_thresh", 0.6))
    fid, _reward, n = _dp_turn_fidelity(tgt_turns, gen_turns, thresh)
    recall = _turn_recall_present(tgt_turns, gen_turns, thresh)
    return {
        "fidelity": round(fid, 4),
        "recall": round(recall, 4),
        "n_target_turns": n,
        "n_gen_turns": len(gen_turns),
    }


# ---------------------------------------------------------------------------
# 剧情相似度主入口
# ---------------------------------------------------------------------------

async def plot_similarity(
    generated: str,
    target: str,
    *,
    n_min: int | None = None,
    n_max: int | None = None,
) -> dict[str, Any]:
    """剧情相似度（0~1）：每段剧情点被 generated 句子覆盖的最大余弦均值。

    返回 {"plot": 均值, "coverage": 覆盖率@0.50, "n_beats", "n_gen"}。
    n 范围默认取 SETTINGS.v519_n_min/n_max（生产锁定 [200,250]）。

    beats = 每段 LLM 剧情概括（持久缓存，跨 run 只概括一次）
    beat_embs = beats 嵌入（进程内缓存，同章同范围复用）
    gen_sents = generated 按句切（短句并入），句 embedding 进程内缓存
    每 beat 取与全部 gen_sents embedding 的最大余弦 → 均值 + 覆盖率@0.50。
    """
    from .embed_client import cosine_similarity

    n_min = n_min if n_min is not None else SETTINGS.v519_n_min
    n_max = n_max if n_max is not None else SETTINGS.v519_n_max

    segments = segment_into_plots(target, n_min, n_max)
    if not segments:
        return {"plot": 0.0, "coverage": 0.0, "n_beats": 0, "n_gen": 0}

    target_key = _target_hash(target)

    # ---- 目标侧剧情概括（持久缓存，未命中的并发概括） ----
    cache = await _ensure_summary_cache()
    beats: list[str] = [""] * len(segments)
    pending: list[tuple[int, str, str]] = []  # (seg_idx, key, seg)
    for si, seg in enumerate(segments):
        key = f"{target_key}|{n_min}-{n_max}|{si}"
        if cache.get(key):
            beats[si] = cache[key]
        else:
            pending.append((si, key, seg))
    if pending:
        # 批量概括：SUMMARY_BATCH 段一次调用（摊销固定延迟），多批并发（semaphore 限并发）
        batches = [pending[i:i + SUMMARY_BATCH]
                   for i in range(0, len(pending), SUMMARY_BATCH)]
        batch_results = await asyncio.gather(
            *(_summarize_segments_batch([seg for _, _, seg in b]) for b in batches))
        summaries = [s for br in batch_results for s in br]
        for (si, key, seg), summary in zip(pending, summaries):
            if not summary:
                summary = seg[:120]  # 回退：原句做 beat（不理想，但保可用）
            cache[key] = summary
            beats[si] = summary
        await _persist_summary_cache()

    # ---- beat 嵌入（进程内缓存，同章同范围复用） ----
    emb_key = f"emb|{target_key}|{n_min}-{n_max}"
    beat_embs = _BEAT_EMB_CACHE.get(emb_key)
    if beat_embs is None:
        beat_embs = await safe_get_embeddings(beats)
        _BEAT_EMB_CACHE[emb_key] = beat_embs

    # ---- generated 句嵌入（进程内缓存 cap 128，防长期进程内存膨胀） ----
    gen_key = _target_hash(generated)
    gen_embs = _SENT_EMB_CACHE.get(gen_key)
    if gen_embs is None:
        gen_sents = _chunk_generated_sentences(generated)
        gen_embs = await safe_get_embeddings(gen_sents) if gen_sents else []
        _SENT_EMB_CACHE[gen_key] = gen_embs
        while len(_SENT_EMB_CACHE) > _SENT_EMB_CAP:
            _SENT_EMB_CACHE.popitem(last=False)

    # ---- v5.21 对白轮保真（全章全局最优对齐）：缺失/乱序/错配同时惩罚 ----
    # 全章级 target 对白轮 vs generated 对白轮做一次最优有序对齐（DP），
    # 再按 w_turn 混合进 plot。逐段混合会被段数均值稀释（开头缺陷看不见），
    # 全局对齐让「开头对白密集的 15 轮」直接占对白轮维度的多数。
    w_turn = float(getattr(SETTINGS, "v521_turn_weight", 0.0))
    turn_thresh = float(getattr(SETTINGS, "v521_turn_match_thresh", 0.6))
    gen_turns = _extract_dialogue_turns(generated)
    tgt_turns = _extract_dialogue_turns(target)
    turn_fid, _reward, n_target = _dp_turn_fidelity(tgt_turns, gen_turns, turn_thresh)
    turn_recall = _turn_recall_present(tgt_turns, gen_turns, turn_thresh)

    scores = []       # 纯 beat 余弦（coverage 用）
    for be in beat_embs:
        if be is None:
            continue
        best = 0.0
        for ge in gen_embs:
            if ge is None:
                continue
            c = cosine_similarity(be, ge)
            if c > best:
                best = c
        scores.append(max(0.0, min(1.0, best)))
    if not scores:
        return {"plot": 0.0, "coverage": 0.0, "turn_fidelity": round(turn_fid, 4),
                "turn_recall": round(turn_recall, 4), "n_target_turns": n_target,
                "n_gen_turns": len(gen_turns), "n_beats": len(segments),
                "n_gen": len(gen_embs)}
    beat_mean = sum(scores) / len(scores)
    plot = round((1.0 - w_turn) * beat_mean + w_turn * turn_fid, 4)
    coverage = round(sum(1 for s in scores if s >= 0.50) / len(scores), 4)
    return {"plot": plot, "coverage": coverage, "turn_fidelity": round(turn_fid, 4),
            "turn_recall": round(turn_recall, 4), "n_target_turns": n_target,
            "n_gen_turns": len(gen_turns), "n_beats": len(segments),
            "n_gen": len(gen_embs)}
