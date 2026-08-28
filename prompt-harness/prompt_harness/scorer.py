"""评分模块 v3。

v3 核心变更：用严格的字符级编辑距离相似度替换 LLM-as-Judge 主观评分。
保留 ROUGE-L / BLEU-4 / Embedding 作为诊断参考（不计入优化目标）。
"""
from __future__ import annotations

import re

from .embed_client import cosine_similarity, get_embedding


# ---------------------------------------------------------------------------
# 文本规范化（预处理：去空白 + 全角→半角标点 + 统一引号）
# ---------------------------------------------------------------------------

# 全角标点 → 半角标点映射
_FULLWIDTH_TO_HALFWIDTH: dict[int, int] = {
    0xFF0C: 0x002C,  # ， → ,
    0x3002: 0x002E,  # 。 → .
    0xFF01: 0x0021,  # ！ → !
    0xFF1F: 0x003F,  # ？ → ?
    0xFF1B: 0x003B,  # ； → ;
    0xFF1A: 0x003A,  # ： → :
    0x2018: 0x0027,  # ' → '
    0x2019: 0x0027,  # ' → '
    0x201C: 0x0022,  # " → "
    0x201D: 0x0022,  # " → "
    0xFF08: 0x0028,  # （ → (
    0xFF09: 0x0029,  # ） → )
    0x3010: 0x005B,  # 【 → [
    0x3011: 0x005D,  # 】 → ]
    0x300A: 0x003C,  # 《 → <
    0x300B: 0x003E,  # 》 → >
    0x300C: 0x0022,  # 「 → "
    0x300D: 0x0022,  # 」 → "
    0x300E: 0x0022,  # 『 → "
    0x300F: 0x0022,  # 』 → "
}

# 编译全角→半角转换表
_FULLWIDTH_TRANS = str.maketrans({chr(k): chr(v) for k, v in _FULLWIDTH_TO_HALFWIDTH.items()})

# 空白字符正则（含 Unicode 空白）
_WHITESPACE_RE = re.compile(r'\s+')


def normalize_text(text: str) -> str:
    """规范化文本用于相似度比较。

    规则：
    1. 移除所有空白字符（空格、换行、制表符等）
    2. 全角标点 → 半角标点
    3. 统一引号（已在上一步处理）
    4. 中文字符本身不转换
    """
    # 去空白
    text = _WHITESPACE_RE.sub('', text)
    # 全角标点 → 半角标点
    text = text.translate(_FULLWIDTH_TRANS)
    return text


# ---------------------------------------------------------------------------
# 字符级编辑距离（Levenshtein）
# ---------------------------------------------------------------------------

def levenshtein_distance(s1: str, s2: str) -> int:
    """计算两个字符串的 Levenshtein 编辑距离。

    使用 O(min(m,n)) 空间的动态规划实现。
    """
    if len(s1) < len(s2):
        s1, s2 = s2, s1

    m, n = len(s1), len(s2)
    # 单行 DP：prev 是上一行，cur 是当前行
    prev = list(range(n + 1))

    for i in range(1, m + 1):
        cur = [i] + [0] * n
        for j in range(1, n + 1):
            cost = 0 if s1[i - 1] == s2[j - 1] else 1
            cur[j] = min(
                cur[j - 1] + 1,       # 插入
                prev[j] + 1,           # 删除
                prev[j - 1] + cost,    # 替换
            )
        prev = cur

    return prev[n]


# ---------------------------------------------------------------------------
# 字符级相似度（唯一优化目标）
# ---------------------------------------------------------------------------

def char_level_similarity(generated: str, target: str) -> float:
    """计算生成文本与目标文本的字符级相似度。

    预处理：
    1. 移除所有空白字符
    2. 全角标点统一为半角
    3. 统一引号

    相似度 = 1 - 编辑距离 / max(len1, len2)
    返回 0.0 ~ 1.0，越高越相似。
    如果两段文本都为空，返回 1.0。
    """
    gen_norm = normalize_text(generated)
    tgt_norm = normalize_text(target)

    if not gen_norm and not tgt_norm:
        return 1.0
    if not gen_norm or not tgt_norm:
        return 0.0

    dist = levenshtein_distance(gen_norm, tgt_norm)
    max_len = max(len(gen_norm), len(tgt_norm))
    similarity = 1.0 - dist / max_len
    return round(max(0.0, similarity), 4)


# ---------------------------------------------------------------------------
# char n-gram 包含率（v5.11，b8 内容相似度——复现原文的主导评分维度）
# ---------------------------------------------------------------------------

def char_ngram_containment(generated: str, target: str, ns: tuple[int, ...] = (3, 4, 5)) -> float:
    """b8 与原文字符相似度：target 的 char n-gram 有多大比例出现在 generated 中。

    方向 = 复现度：生成文本复现了原文多少内容。
    与编辑距离（全序列对齐、要求长度相当）不同，包含率只看"原文片段被生成复现了多少"，
    对长度差异不敏感、对语序适度敏感——更贴合"12345 vs 12344 相似 80%"这类判断。

    纯规则、O(len)、无 LLM 调用。normalize（去空白+统一标点）后计算，
    n=3/4/5 分别求 set 交集包含率再取平均。
    常用字 n-gram（的了/一个）天然给底分 ~15-25%，不影响区分度，v1 接受。
    target 为空 → 1.0；generated 为空 → 0.0。
    """
    gen_norm = normalize_text(generated)
    tgt_norm = normalize_text(target)

    if not tgt_norm:
        return 1.0
    if not gen_norm:
        return 0.0

    def _n_grams(s: str, n: int) -> set[str]:
        return {s[i:i + n] for i in range(len(s) - n + 1)} if len(s) >= n else set()

    total = 0.0
    for n in ns:
        tgt_ngrams = _n_grams(tgt_norm, n)
        if not tgt_ngrams:
            continue
        gen_ngrams = _n_grams(gen_norm, n)
        if not gen_ngrams:
            continue
        total += len(tgt_ngrams & gen_ngrams) / len(tgt_ngrams)
    return round(total / len(ns), 4)


# 【v5.14 L2b】供原文复现反馈选句/对齐复用（对齐 b8 评分语义）
def char_ngrams(s: str, n: int) -> set[str]:
    """字符 n-gram 集合（输入须已 normalize；len(s)<n → 空集）。

    与 char_ngram_containment 同一套语义 —— select_reproduction_gaps
    用它估算字面复现增益，保证反馈选择的句子收益与 b8 评分口径一致。
    """
    return {s[i:i + n] for i in range(len(s) - n + 1)} if len(s) >= n else set()


def split_sentences(text: str) -> list[str]:
    """按句末标点切句（与 _extract_semantic_anchor_sentences 同一套分隔符）。

    空句/纯空白句剔除。切出的句子未经 normalize（保留原始措辞用于反馈展示）。
    """
    return [s.strip() for s in re.split(r"[。！？\n.!?]", text) if s.strip()]

def length_diff_ratio(generated: str, target: str) -> float:
    """计算生成文本与目标文本的长度差异比例。

    返回 -1.0 ~ +∞：
    - 0.0 表示长度完全一致
    - 正数表示生成偏长（如 0.2 表示生成比原文长 20%）
    - 负数表示生成偏短（如 -0.2 表示生成比原文短 20%）
    """
    gen_norm = normalize_text(generated)
    tgt_norm = normalize_text(target)
    if not tgt_norm:
        return 0.0
    return round((len(gen_norm) - len(tgt_norm)) / len(tgt_norm), 4)


def combined_optimization_score(generated: str, target: str) -> float:
    """优化综合分：直接使用字符级相似度作为唯一指标。"""
    return char_level_similarity(generated, target)


# ---------------------------------------------------------------------------
# 新增评分指标（v4.0 蜕变: 字数对齐 + 语义覆盖 + 流畅度）
# ---------------------------------------------------------------------------


def length_alignment_score(generated: str, target: str) -> float:
    """字数对齐度 (0~1)。

    衡量生成文本与目标文本的字数匹配程度。
    字数差 < 10% → ≈1.0, 差 > 50% → <0.5, 完全匹配 → 1.0

    L = max(0, 1.0 − |gen_len − tgt_len| / tgt_len)
    """
    gen_norm = normalize_text(generated)
    tgt_norm = normalize_text(target)
    if not tgt_norm:
        return 0.0
    if not gen_norm:
        return 0.0
    diff_ratio = abs(len(gen_norm) - len(tgt_norm)) / len(tgt_norm)
    return round(max(0.0, 1.0 - diff_ratio), 4)


def _chunk_text(text: str, n: int) -> list[str]:
    """把文本均匀切成至多 n 段（按字符数）。

    embedding 对长度敏感，长文本分段比取前 N 字更鲁棒——
    保证全文（不只是开头）都在语义覆盖的评分视野内。
    短文本（< n 字符）按实际字符数分段。
    """
    text = (text or "").strip()
    if not text or n <= 1:
        return [text] if text else []
    n = min(n, len(text))
    chunk_len = (len(text) + n - 1) // n
    return [text[i:i + chunk_len] for i in range(0, len(text), chunk_len)][:n]


# 【性能优化】target 分段 embedding 缓存（同 target 每评估恒量复用，防每候选重算）
_TGT_EMB_CACHE: dict[str, list[list[float] | None]] = {}
_TGT_EMB_CACHE_CAP = 64


async def _cached_target_segment_emb(target: str, n_segments: int) -> list[list[float] | None]:
    """semantic_coverage 的 target 侧段 embedding（缓存复用，cap 防膨胀）。"""
    import hashlib
    from .embed_client import get_embeddings

    key = f"{hashlib.md5(target.encode('utf-8')).hexdigest()}|{n_segments}"
    if key in _TGT_EMB_CACHE:
        return _TGT_EMB_CACHE[key]
    emb = await get_embeddings(_chunk_text(target, n_segments))
    if len(_TGT_EMB_CACHE) >= _TGT_EMB_CACHE_CAP:
        _TGT_EMB_CACHE.clear()
    _TGT_EMB_CACHE[key] = emb
    return emb


async def semantic_coverage(generated: str, target: str, n_segments: int = 4) -> float:
    """语义覆盖率 (0~1)。

    用 embedding 余弦相似度衡量生成文本是否覆盖了与原文相同的内容。
    比字符级相似度更鲁棒——同一意思不同表达也能匹配。

    全文分段：把生成文本和原文各切成 n_segments 段，逐段做 embedding
    余弦取均值。段按"内容进度"对齐（各自切 n 段，第 i 段对应全文前
    i/n 的情节）。这样能惩罚"后半章情节跑偏"或"只写了开头就停笔"，
    而不是只看开头 1024 字。
    """
    from .embed_client import get_embeddings

    gen_chunks = _chunk_text(generated, n_segments)
    tgt_chunks = _chunk_text(target, n_segments)
    if not gen_chunks or not tgt_chunks:
        return 0.0

    emb_g = await get_embeddings(gen_chunks)
    # 【性能优化】target 分段 embedding 是恒量（同 target 每评估重复），加进程内缓存。
    # 生成文本 embedding 每候选不同，不能缓存。
    emb_t = await _cached_target_segment_emb(target, n_segments)

    scores = []
    for eg, et in zip(emb_g, emb_t):
        if eg is None or et is None:
            continue
        scores.append(max(0.0, min(1.0, cosine_similarity(eg, et))))
    if not scores:
        return 0.0
    return round(sum(scores) / len(scores), 4)


def fluency_score(generated: str) -> float:
    """流畅度代理评分 (0~1)。

    基于 n-gram 覆盖率的轻量流畅度估计（无需 LLM 调用）：
    - 计算字符级 4-gram 的重复率（高重复 = 低流畅度）
    - gzip 压缩比（高压缩比 = 低信息量/高重复）
    - 返回 0~1 归一化值
    """
    if not generated:
        return 0.0

    gen_norm = normalize_text(generated)
    if len(gen_norm) < 10:
        return 1.0  # 太短不判断

    # 1. 4-gram 重复率
    fourgrams = {}
    for i in range(len(gen_norm) - 3):
        ng = gen_norm[i:i+4]
        fourgrams[ng] = fourgrams.get(ng, 0) + 1
    if not fourgrams:
        return 1.0
    total_positions = len(gen_norm) - 3
    unique_ratio = len(fourgrams) / max(total_positions, 1)
    # unique_ratio=1.0 表示完全不重复 → 高流畅度
    # unique_ratio=0.1 表示大量重复 → 低流畅度

    # 2. 标点方差（标点滥用 = 流畅度低）
    punct_count = sum(1 for ch in gen_norm if ch in '，。！？；：、')
    punct_density = punct_count / max(len(gen_norm), 1)
    # 标点密度太低（没有断句）或太高（碎片化）都不好
    punct_ideal = abs(punct_density - 0.12)  # 12% 标点密度是理想值
    punct_norm = max(0.0, 1.0 - punct_ideal * 3)  # 偏差 33% 以上 → 0

    # 3. 综合
    score = 0.6 * unique_ratio + 0.4 * punct_norm
    return round(max(0.0, min(1.0, score)), 4)


# ---------------------------------------------------------------------------
# 新增指标（v4.0 蜕变: 信息量 + 叙事连贯 + 风格距离）
# ---------------------------------------------------------------------------


def compression_ratio(generated: str) -> float:
    """压缩比信息密度指标 (0~1)。

    用 gzip 压缩比衡量文本的信息密度。
    高压缩率（无压缩）→ 信息密度高 → 高分
    低压缩率（被压缩得很小）→ 重复/低信息 → 低分

    原理：高度重复的文本（如"哈哈哈哈哈"）gzip 能大幅压缩，
    而信息量大的文本（每句话都不同）gzip 压缩后体积变化不大。

    返回 0~1，1.0 表示与参考完美匹配，0.0 表示极度重复。
    """
    import gzip
    if not generated:
        return 0.5

    gen_norm = normalize_text(generated)
    if len(gen_norm) < 20:
        return 0.5  # 太短不判断

    raw_bytes = gen_norm.encode("utf-8")
    compressed = gzip.compress(raw_bytes)
    ratio = len(compressed) / max(len(raw_bytes), 1)

    # 中文 gzip 压缩比经验值：
    # - 高信息量正常文本：ratio 0.4 ~ 0.6
    # - 中等重复文本：ratio 0.25 ~ 0.4
    # - 极度重复文本：ratio < 0.2
    # 映射到 0~1 分数
    score = max(0.0, min(1.0, (ratio - 0.15) / 0.45))
    return round(score, 4)


def narrative_cohesion(generated: str) -> float:
    """叙事连贯度 (0~1)。

    衡量相邻句子之间的语义连贯性。
    通过比较相邻句子的起始词/长度模式的方差来计算。

    原理：
    - 连贯的叙事：句子长度渐进变化（长短句交替有致）
    - 不连贯的叙事：句子长度剧烈波动 + 句式单一

    返回 0~1，1.0 = 非常连贯，0.0 = 碎片化。
    """
    import numpy as np

    if not generated or len(generated) < 50:
        return 0.5

    # 按句号/问号/感叹号分句
    sentences = [s.strip() for s in generated.replace("?", "？").replace("!", "！")
                 .replace("？", "。").replace("！", "。").split("。") if s.strip()]
    if len(sentences) < 3:
        return 0.5

    # 1. 句子长度序列的稳定性
    lengths = [len(s) for s in sentences]
    mean_len = float(np.mean(lengths)) if lengths else 1.0
    if mean_len < 1:
        return 0.5
    len_cv = float(np.std(lengths)) / mean_len  # 变异系数

    # 2. 相邻句子的长度差异
    adj_diffs = [abs(lengths[i] - lengths[i-1]) / max(lengths[i], lengths[i-1], 1)
                 for i in range(1, len(lengths))]
    mean_adj_diff = float(np.mean(adj_diffs)) if adj_diffs else 0.0

    # 3. 句子长度极差比
    max_len = max(lengths)
    min_len = max(min(lengths), 1)
    range_ratio = max_len / min_len if min_len > 0 else 1.0

    # 综合三个维度的分数
    # - len_cv: 适中的变异系数（0.4~0.8）表示长短句交替好
    # - mean_adj_diff: 适中的邻差异（0.2~0.5）表示渐进的节奏变化
    # - range_ratio: 不要太极端

    score_cv = max(0.0, 1.0 - abs(len_cv - 0.6) / 0.6)
    score_adj = max(0.0, 1.0 - mean_adj_diff * 1.5)
    score_range = max(0.0, min(1.0, 3.0 / range_ratio))

    score = 0.4 * score_cv + 0.3 * score_adj + 0.3 * score_range
    return round(max(0.0, min(1.0, score)), 4)


# 中文功能词表（虚词 — 功能词的分布差异反映了文风差异）
_FUNCTION_WORDS = ["的", "了", "在", "是", "和", "着", "就", "地", "过", "把"]


def _function_word_freq(text: str) -> dict[str, float]:
    """计算文本中功能词的频率分布。"""
    total_chars = max(len(text), 1)
    freq = {}
    for w in _FUNCTION_WORDS:
        count = text.count(w)
        freq[w] = count / total_chars * 1000  # 千字频率
    return freq


def function_word_kl(generated: str, target: str) -> float:
    """功能词分布 KL 散度 (0~1)。

    衡量生成文本与目标文本在功能词使用上的风格差异。
    功能词（的/了/在/是/和/着/就/地/过/把）的使用频率反映了语感。

    KL(P_target || P_gen)，对称化。

    返回 0~1：
    1.0 = 风格完全相同
    0.0 = 风格差异极大
    """
    if not generated or not target:
        return 0.0

    import numpy as np

    gen_norm = normalize_text(generated)
    tgt_norm = normalize_text(target)

    gen_freq = _function_word_freq(gen_norm)
    tgt_freq = _function_word_freq(tgt_norm)

    # 对称 KL 散度
    kl_div = 0.0
    for w in _FUNCTION_WORDS:
        p = tgt_freq.get(w, 0.0) + 1e-10  # 避免 log(0)
        q = gen_freq.get(w, 0.0) + 1e-10
        kl_div += p * np.log(p / q) + q * np.log(q / p)

    kl_div /= 2.0  # 对称化

    # 映射到 0~1：KL=0 → 1.0，KL=5 → 0.0
    # 经验范围：同类作者 KL < 0.5，不同作者 KL 1~3
    score = max(0.0, min(1.0, 1.0 - kl_div / 5.0))
    return round(score, 4)


# ---------------------------------------------------------------------------
# 旧版诊断指标（保留作为可选参考，不参与优化决策）
# ---------------------------------------------------------------------------

class _CharTokenizer:
    """rouge-score 需要 tokenizer 对象。"""

    def tokenize(self, text: str) -> list[str]:
        return list(text)


_ROUGE = None  # lazy init: see _get_rouge()


def _get_rouge():
    """Lazy-init RougeScorer (avoids heavy rouge_score import at startup)."""
    global _ROUGE
    if _ROUGE is None:
        from rouge_score import rouge_scorer

        _ROUGE = rouge_scorer.RougeScorer(["rougeL"], tokenizer=_CharTokenizer())
    return _ROUGE


def rouge_l(generated: str, target: str) -> float:
    scores = _get_rouge().score(target=target, prediction=generated)
    return float(scores["rougeL"].fmeasure)


def bleu_4(generated: str, target: str) -> float:
    """返回 0–1 之间的 BLEU-4 分数。"""
    import sacrebleu

    bleu = sacrebleu.sentence_bleu(generated, [target], tokenize="zh")
    return bleu.score / 100.0


async def embedding_sim(generated: str, target: str) -> float:
    emb_g = await get_embedding(generated)
    emb_t = await get_embedding(target)
    if emb_g is None or emb_t is None:
        return 0.0
    return cosine_similarity(emb_g, emb_t)


async def compute_scores(generated: str, target: str) -> dict:
    """旧版综合分（仅诊断参考，不用于优化决策）。"""
    rl = rouge_l(generated, target)
    bl = bleu_4(generated, target)
    es = await embedding_sim(generated, target)
    composite = 0.4 * rl + 0.3 * bl + 0.3 * es
    return {
        "rouge_l": round(rl, 4),
        "bleu_4": round(bl, 4),
        "embedding_sim": round(es, 4),
        "composite": round(composite, 4),
    }


# ---------------------------------------------------------------------------
# diff 分析便捷封装（v3.1）
# ---------------------------------------------------------------------------


def compute_diff_report(
    generated: str,
    target: str,
    similarity: float | None = None,
    length_diff: float | None = None,
) -> dict:
    """对生成/目标文本运行 diff 分析的便捷封装。

    如果 similarity/length_diff 未提供，自动计算。
    返回 diff_analyzer.ErrorReport 的字典序列化。

    这是 diff_analyzer.analyze() 的轻量包装——自动处理规范化，
    返回可直接序列化为 JSON 的结果。
    """
    from .diff_analyzer import analyze as diff_analyze

    if similarity is None:
        similarity = char_level_similarity(generated, target)
    if length_diff is None:
        length_diff = length_diff_ratio(generated, target)

    report = diff_analyze(
        generated=generated,
        target=target,
        similarity=similarity,
        length_diff=length_diff,
        normalize_fn=normalize_text,
    )

    return {
        "similarity": report.similarity,
        "length_diff": report.length_diff,
        "gen_len": report.gen_len,
        "target_len": report.target_len,
        "error_count": len(report.errors),
        "has_critical": report.has_critical_errors,
        "error_summary": report.summary,
        "error_counts": report.error_counts,
        "modifications": [
            {
                "priority": m.priority,
                "category": m.error_category.value,
                "target_rules": m.target_rules,
                "action_type": m.action_type,
                "instruction": m.instruction,
            }
            for m in report.modifications
        ],
        "diff_block_count": len(report.diff_blocks),
        "diff_blocks_summary": [
            {"op": b.op, "size": b.size}
            for b in report.diff_blocks
            if b.op != "equal"
        ][:10],  # 最多 10 个非 equal 块摘要
    }


# ---------------------------------------------------------------------------
# 兼容性别名（供旧代码过渡）
# ---------------------------------------------------------------------------

def get_scoring_weights() -> dict:
    """获取当前评分权重配置（兼容旧接口）。

    v3 中权重已简化为单一相似度，此函数返回空配置供旧代码兼容。
    """
    return {}


# ---------------------------------------------------------------------------
# 关键细节锚点 & 事实一致性评分（v5.10，实现在 key_details 模块）
# ---------------------------------------------------------------------------
# 从原文抽取"橡皮擦/五万/李青鸟/经典对白"这类标志性细节，并校验候选文本
# 对细节的保留程度。详见 prompt_harness/key_details.py。


def extract_key_details(text: str, caps: dict | None = None) -> dict:
    """抽取原文关键细节锚点（人物/数字/对白/物品）。实现见 key_details 模块。"""
    from .key_details import extract_key_details as _impl
    return _impl(text, caps=caps)


def factual_consistency_score(
    candidate: str,
    target: str,
    key_details: dict | None = None,
) -> dict:
    """候选对原文关键细节的保留程度（0~1）。实现见 key_details 模块。"""
    from .key_details import factual_consistency_score as _impl
    return _impl(candidate, target, key_details=key_details)
