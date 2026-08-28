"""
Structural Text Analyzer — 13-dim text fingerprint extraction and comparison.

Core idea: subjective writing quality can be approximated by measurable
structural features. This module extracts a 13-dimensional feature vector
from any text, enabling quantitative comparison between generated text
and target text.

V(text) = 13 dimensional vector:
  [0] avg_sentence_len        — 平均句长
  [1] sentence_len_variance   — 句长方差
  [2] dialogue_density        — 对白密度
  [3] median_paragraph_len    — 段落中位数长度
  [4] paragraph_frequency     — 段落频率
  [5] comma_density           — 逗号密度
  [6] special_punct_density   — 特殊标点密度
  [7] line_break_frequency    — 换行频率
  [8] lexical_richness        — 词汇丰富度
  [9] sentence_start_diversity  — 句首多样性（新）
  [10] modifier_density       — 修饰词密度（的/地/得）（新）
  [11] dialogue_turn_density  — 对白轮次密度（新）
  [12] sentences_per_paragraph — 每段句数均值（新）
"""

from __future__ import annotations

import difflib
import math
import re
from collections import Counter


# ---------------------------------------------------------------------------
# Dimension 1-2: Sentence length analysis
# ---------------------------------------------------------------------------

_SENTENCE_END_RE = re.compile(r'[。！？.!?]')

def _split_sentences(text: str) -> list[str]:
    """Split text into sentences by Chinese/English sentence-ending punctuation."""
    raw = _SENTENCE_END_RE.split(text)
    return [s.strip() for s in raw if s.strip()]


def _avg_sentence_len(text: str) -> float:
    """d1: average sentence length in characters."""
    sentences = _split_sentences(text)
    if not sentences:
        return 0.0
    return sum(len(s) for s in sentences) / len(sentences)


def _sentence_len_variance(text: str) -> float:
    """d2: variance of sentence lengths."""
    sentences = _split_sentences(text)
    if len(sentences) < 2:
        return 0.0
    lengths = [len(s) for s in sentences]
    mean = sum(lengths) / len(lengths)
    return sum((l - mean) ** 2 for l in lengths) / len(lengths)


# ---------------------------------------------------------------------------
# Dimension 3: Dialogue density
# ---------------------------------------------------------------------------

_QUOTE_PAIRS = [
    ('“', '”'),  # "…"
    ('「', '」'),  # 「…」
    ('"', '"'),
    ('\'', '\''),
]


def _dialogue_density(text: str) -> float:
    """d3: proportion of characters inside quotation marks."""
    if not text:
        return 0.0
    total_chars = len(text)

    # Find all quoted segments
    in_quote = False
    quote_char = None
    dialogue_chars = 0

    i = 0
    while i < len(text):
        ch = text[i]
        if not in_quote:
            for open_q, close_q in _QUOTE_PAIRS:
                if text[i:i+len(open_q)] == open_q:
                    in_quote = True
                    quote_char = close_q
                    i += len(open_q)
                    break
            else:
                i += 1
        else:
            # 安全保护：quote_char 可能为 None（边缘情况，如不完整引号在文末）
            if quote_char is not None and text[i:i+len(quote_char)] == quote_char:
                in_quote = False
                i += len(quote_char)
                quote_char = None
            else:
                dialogue_chars += 1
                i += 1

    return dialogue_chars / max(total_chars, 1)


# ---------------------------------------------------------------------------
# Dimension 4-5: Paragraph structure
# ---------------------------------------------------------------------------

def _split_paragraphs(text: str) -> list[str]:
    """Split text into paragraphs by double newlines or single newlines."""
    # First try double newlines
    parts = re.split(r'\n\s*\n', text)
    if len(parts) <= 1:
        # Fallback to single newlines
        parts = text.split('\n')
    return [p.strip() for p in parts if p.strip()]


def _median_paragraph_len(text: str) -> float:
    """d4: median paragraph length in characters."""
    paragraphs = _split_paragraphs(text)
    if not paragraphs:
        return 0.0
    lengths = sorted(len(p) for p in paragraphs)
    n = len(lengths)
    if n % 2 == 0:
        return (lengths[n//2 - 1] + lengths[n//2]) / 2
    return lengths[n//2]


def _paragraph_frequency(text: str) -> float:
    """d5: number of paragraphs per 1000 characters."""
    paragraphs = _split_paragraphs(text)
    total = len(text)
    if total == 0:
        return 0.0
    return (len(paragraphs) / total) * 1000


# ---------------------------------------------------------------------------
# Dimension 6: Punctuation distribution
# ---------------------------------------------------------------------------

_PUNCT_TYPES = [
    '，', '。', '！', '？', '、', '；', '：',
    ',', '.', '!', '?', ';', ':',
    '“', '”', '「', '」',  # quotation marks
    '…', '——', '～',
]


def _punctuation_distribution(text: str) -> dict[str, float]:
    """d6: normalized frequency of each punctuation type (per 1000 chars)."""
    total = len(text)
    if total == 0:
        return {p: 0.0 for p in _PUNCT_TYPES}

    counts = Counter(ch for ch in text if ch in _PUNCT_TYPES)
    return {p: (counts.get(p, 0) / total) * 1000 for p in _PUNCT_TYPES}


# ---------------------------------------------------------------------------
# Dimension 7-8: Density metrics
# ---------------------------------------------------------------------------

def _comma_density(text: str) -> float:
    """d7: comma count per 1000 characters (Chinese + English commas)."""
    total = len(text)
    if total == 0:
        return 0.0
    commas = text.count('，') + text.count(',')
    return (commas / total) * 1000


def _special_punct_density(text: str) -> float:
    """d8: em-dash, ellipsis, exclamation mark density per 1000 chars."""
    total = len(text)
    if total == 0:
        return 0.0
    special = (text.count('——') * 2 + text.count('…') +
               text.count('！') + text.count('!') +
               text.count('～'))
    return (special / total) * 1000


# ---------------------------------------------------------------------------
# Dimension 9-10: Line breaks & lexical richness
# ---------------------------------------------------------------------------

def _line_break_frequency(text: str) -> float:
    """d9: newline count per 1000 characters."""
    total = len(text)
    if total == 0:
        return 0.0
    return (text.count('\n') / total) * 1000


def _lexical_richness(text: str) -> float:
    """d9 (旧d10): ratio of unique characters to total characters (char-level TTR)."""
    total = len(text)
    if total == 0:
        return 0.0
    # Use character-level type-token ratio for Chinese text
    chars = [ch for ch in text if ch not in ' \t\n\r']
    if not chars:
        return 0.0
    return len(set(chars)) / len(chars)


# ---------------------------------------------------------------------------
# Dimension 10 (new): Sentence start diversity
# ---------------------------------------------------------------------------

def _sentence_start_diversity(text: str) -> float:
    """d10: type-token ratio of sentence-start characters.

    高 → 句首用词丰富多样（文笔好）
    低 → 句首单调（"他""她""这""那"反复出现）
    完全确定性，不依赖词表。
    """
    sentences = _split_sentences(text)
    if len(sentences) < 3:
        return 0.0
    starts = [s[0] for s in sentences if s and s[0].strip()]
    if not starts:
        return 0.0
    return len(set(starts)) / len(starts)


# ---------------------------------------------------------------------------
# Dimension 11 (new): Modifier particle density
# ---------------------------------------------------------------------------

def _modifier_density(text: str) -> float:
    """d11: density of Chinese modifier particles per 1000 chars.

    的 = possessive/adjective marker
    地 = adverb marker
    得 = complement marker

    高 → 修饰成分多（描写密集，可能偏啰嗦）
    低 → 修饰少（白描，简洁）
    """
    total = len(text)
    if total == 0:
        return 0.0
    count = text.count('的') + text.count('地') + text.count('得')
    return (count / total) * 1000


# ---------------------------------------------------------------------------
# Dimension 12 (new): Dialogue turn density
# ---------------------------------------------------------------------------

def _dialogue_turn_density(text: str) -> float:
    """d12: number of dialogue segments per 1000 chars.

    衡量对白的活跃度（对话轮次频率），与 d3 对白密度互补：
    - d3 高 + d12 高 → 多轮短对白（对话驱动）
    - d3 高 + d12 低 → 长篇独白/叙述性对白
    - d3 低 + d12 低 → 无对话
    """
    if not text:
        return 0.0
    total = len(text)

    # Count dialogue segments
    seg_count = 0
    in_quote = False
    i = 0
    while i < len(text):
        ch = text[i]
        if not in_quote:
            for open_q, close_q in _QUOTE_PAIRS:
                if text[i:i+len(open_q)] == open_q:
                    in_quote = True
                    seg_count += 1
                    i += len(open_q)
                    break
            else:
                i += 1
        else:
            found_close = False
            for open_q, close_q in _QUOTE_PAIRS:
                if text[i:i+len(close_q)] == close_q:
                    in_quote = False
                    i += len(close_q)
                    found_close = True
                    break
            if not found_close:
                i += 1

    return (seg_count / max(total, 1)) * 1000


# ---------------------------------------------------------------------------
# Dimension 13 (new): Sentences per paragraph
# ---------------------------------------------------------------------------

def _sentences_per_paragraph(text: str) -> float:
    """d13: average number of sentences per paragraph.

    高 → 段落长，容纳多个句子（叙述性段落）
    低 → 段落短，每段句数少（碎片化/对白段多）
    """
    paragraphs = _split_paragraphs(text)
    if not paragraphs:
        return 0.0
    sentence_counts = []
    for p in paragraphs:
        sentences = _split_sentences(p)
        sentence_counts.append(len(sentences))
    return sum(sentence_counts) / len(sentence_counts)


# ---------------------------------------------------------------------------
# Main extraction function
# ---------------------------------------------------------------------------

def extract_structural_vector(text: str) -> dict:
    """Extract the 13-dimensional structural fingerprint from text.

    Returns a dict with both the raw vector and labeled components,
    suitable for both computation and display.

    Args:
        text: Input text (typically a chapter or generated passage).

    Returns:
        {
            "vector": [d1, d2, ..., d13],   # raw float vector
            "labels": {                       # human-readable labels
                "avg_sentence_len": d1,
                "sentence_len_variance": d2,
                "dialogue_density": d3,
                "median_paragraph_len": d4,
                "paragraph_frequency": d5,
                "punctuation_distribution": {...},
                "comma_density": d7,
                "special_punct_density": d8,
                "line_break_frequency": d9,
                "lexical_richness": d10→d9,
                "sentence_start_diversity": d10,
                "modifier_density": d11,
                "dialogue_turn_density": d12,
                "sentences_per_paragraph": d13,
            },
            "meta": {
                "total_chars": N,
                "sentence_count": N,
                "paragraph_count": N,
            }
        }
    """
    punct_dist = _punctuation_distribution(text)
    sentences = _split_sentences(text)
    paragraphs = _split_paragraphs(text)

    labels = {
        "avg_sentence_len": round(_avg_sentence_len(text), 2),
        "sentence_len_variance": round(_sentence_len_variance(text), 2),
        "dialogue_density": round(_dialogue_density(text), 4),
        "median_paragraph_len": round(_median_paragraph_len(text), 1),
        "paragraph_frequency": round(_paragraph_frequency(text), 2),
        "punctuation_distribution": {k: round(v, 2) for k, v in punct_dist.items()},
        "comma_density": round(_comma_density(text), 2),
        "special_punct_density": round(_special_punct_density(text), 2),
        "line_break_frequency": round(_line_break_frequency(text), 2),
        "lexical_richness": round(_lexical_richness(text), 4),
        # 新维度 d10-d13
        "sentence_start_diversity": round(_sentence_start_diversity(text), 4),
        "modifier_density": round(_modifier_density(text), 2),
        "dialogue_turn_density": round(_dialogue_turn_density(text), 2),
        "sentences_per_paragraph": round(_sentences_per_paragraph(text), 2),
    }

    # Raw vector: 13 维
    # d6 (punctuation distribution) 是多维字典，单独存储不加入 vector
    vector = [
        labels["avg_sentence_len"],
        labels["sentence_len_variance"],
        labels["dialogue_density"],
        labels["median_paragraph_len"],
        labels["paragraph_frequency"],
        labels["comma_density"],
        labels["special_punct_density"],
        labels["line_break_frequency"],
        labels["lexical_richness"],
        # 4 个新维度
        labels["sentence_start_diversity"],
        labels["modifier_density"],
        labels["dialogue_turn_density"],
        labels["sentences_per_paragraph"],
    ]

    return {
        "vector": vector,
        "labels": labels,
        "meta": {
            "total_chars": len(text),
            "sentence_count": len(sentences),
            "paragraph_count": len(paragraphs),
        },
    }


# ---------------------------------------------------------------------------
# Similarity computation
# ---------------------------------------------------------------------------

def compute_cosine_similarity(v1: list[float], v2: list[float]) -> float:
    """Compute cosine similarity between two feature vectors.

    Returns a value in [0, 1], where 1 = identical direction.
    """
    if len(v1) != len(v2):
        raise ValueError(f"Vector length mismatch: {len(v1)} vs {len(v2)}")

    dot = sum(a * b for a, b in zip(v1, v2))
    norm1 = math.sqrt(sum(a * a for a in v1))
    norm2 = math.sqrt(sum(b * b for b in v2))

    if norm1 == 0 or norm2 == 0:
        return 0.0

    return dot / (norm1 * norm2)


# Alias: prompt_inference.py 和 param_search.py 以此名导入
compute_structural_similarity = compute_cosine_similarity


def compute_char_similarity(text_a: str, text_b: str) -> float:
    """Compute character-level sequence similarity using difflib.

    This is S_char — sensitive to content coverage (what events are covered).
    Returns a value in [0, 1].
    """
    if not text_a or not text_b:
        return 0.0
    return difflib.SequenceMatcher(None, text_a, text_b).ratio()


def compute_dimension_deltas(
    v_target: dict,
    v_generated: dict,
) -> dict:
    """Compute per-dimension deviation between target and generated vectors.

    Returns:
        {
            "cosine_similarity": float,
            "deltas": {
                "avg_sentence_len": {
                    "target": float, "generated": float,
                    "delta_pct": float,  # % deviation
                    "direction": "higher" | "lower" | "match",
                },
                ...
            }
        }
    """
    t_labels = v_target["labels"]
    g_labels = v_generated["labels"]
    cos_sim = compute_cosine_similarity(v_target["vector"], v_generated["vector"])

    deltas = {}
    scalar_fields = [
        "avg_sentence_len", "sentence_len_variance", "dialogue_density",
        "median_paragraph_len", "paragraph_frequency",
        "comma_density", "special_punct_density",
        "line_break_frequency", "lexical_richness",
        "sentence_start_diversity", "modifier_density",
        "dialogue_turn_density", "sentences_per_paragraph",
    ]

    for field in scalar_fields:
        tv = t_labels.get(field, 0)
        gv = g_labels.get(field, 0)
        if tv == 0 and gv == 0:
            delta_pct = 0.0
        elif tv == 0:
            delta_pct = 100.0
        else:
            delta_pct = round(((gv - tv) / abs(tv)) * 100, 1)

        deltas[field] = {
            "target": tv,
            "generated": gv,
            "delta_pct": delta_pct,
            "direction": "match" if abs(delta_pct) < 5 else ("higher" if delta_pct > 0 else "lower"),
        }

    return {
        "cosine_similarity": round(cos_sim, 4),
        "deltas": deltas,
    }


# ---------------------------------------------------------------------------
# ΔV → Prompt modification suggestions
# ---------------------------------------------------------------------------

_DELTA_TO_SUGGESTION_MAP = [
    # (field, direction, threshold_pct, suggestion)
    ("avg_sentence_len", "higher", 15,
     "句子偏长，考虑在 system prompt 中加入「使用短句」或「每句话不超过X字」"),
    ("avg_sentence_len", "lower", 15,
     "句子偏短，考虑在 system prompt 中加入「允许复合句」或「句式要有变化」"),
    ("dialogue_density", "lower", 20,
     "对白密度不足，考虑在 system prompt 中加入「角色互动必须有对白」或「对白占比不低于X%」"),
    ("dialogue_density", "higher", 30,
     "对白密度过高，考虑在 system prompt 中加入「对白与叙述保持平衡」"),
    ("comma_density", "higher", 25,
     "逗号密度偏高（叙述性过强），考虑加入「精简修饰从句」或「多用句号断句」"),
    ("comma_density", "lower", 25,
     "逗号密度偏低（句式过于碎片化），考虑加入「适当使用逗号连接分句」"),
    ("paragraph_frequency", "higher", 25,
     "分段过频繁，考虑加入「每段至少3-5句」或「减少不必要的换行」"),
    ("paragraph_frequency", "lower", 25,
     "段落过长，考虑加入「每段不超过X句」或「适当分段增加阅读节奏」"),
    ("special_punct_density", "higher", 30,
     "感叹号/省略号过多，考虑加入「克制使用感叹号和省略号」"),
    ("lexical_richness", "lower", 15,
     "词汇丰富度偏低（重复用词），考虑加入「用词多样化」或「避免重复相同的形容词」"),
    ("lexical_richness", "higher", 20,
     "词汇丰富度偏高（可能用词生僻），考虑加入「使用常用词汇」或「不要刻意使用生僻词」"),
    # ── 新维度 d10-d13 ──
    ("sentence_start_diversity", "lower", 20,
     "句首用词单调（他/她/这/那反复出现），考虑加入「变化句子开头」或「避免连续同词开头」"),
    ("sentence_start_diversity", "higher", 30,
     "句首变化过于丰富（可能生硬），考虑加入「适当使用代指衔接」"),
    ("modifier_density", "higher", 25,
     "修饰词（的/地/得）过多，叙述偏啰嗦。考虑加入「精简修饰，多用动词推动叙事」"),
    ("modifier_density", "lower", 30,
     "修饰词过少，可能描写不够。考虑加入「适当加入环境描写和修饰成分」"),
    ("dialogue_turn_density", "lower", 30,
     "对白轮次不足，对话互动少。考虑加入「增加短轮次对白，让角色间有更多交流」"),
    ("dialogue_turn_density", "higher", 40,
     "对白过于碎片化。考虑加入「增加叙述段穿插，平衡对白与描写」"),
    ("sentences_per_paragraph", "higher", 30,
     "段落句数过多，段落过长。考虑加入「适当分段，每段不超过X句」"),
    ("sentences_per_paragraph", "lower", 30,
     "段落句数过少，过于碎片化。考虑加入「每段至少2-3句，保持段落完整性」"),
]


# ---------------------------------------------------------------------------
# 连载适配检测（反短篇故事综合征）
# ---------------------------------------------------------------------------

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

# 结尾悬念钩子标记（刻意制造悬念/预告未知——长章节结尾应在动作/反应中自然截断，
# 不需要钩子）。与 _anti_ai.py 保持一致。
_ENDING_HOOK_MARKS = {
    "会一直", "即将", "将要", "预示着", "总觉得",
    "预感", "说不清", "好像有什么", "像有什么", "有什么在",
    "他没察觉", "他并不知道", "他浑然不觉", "他没有注意到",
    "暴风雨", "只是开始", "远远没有结束", "才刚刚开始",
}


def serialization_fit_score(generated: str) -> float:
    """连载适配度 (0~1)。

    检测生成文本是否有"短篇故事综合征"：
    - opening_penalty: 前 200 字是否包含开篇铺陈/世界观介绍
    - ending_penalty: 后 300 字是否包含总结/升华/收束模式

    1.0 = 完美适配连载（无开头铺陈、无结尾收束）
    0.0 = 完全像独立短篇
    """
    if not generated or len(generated) < 100:
        return 1.0  # 太短不判断

    opening_penalty = 0.0
    ending_penalty = 0.0

    # 开头检测：前 200 字
    opening = generated[:200]
    # 自然段开头检查：如果文本以描述场景开头（非对话、非动作），罚分
    opening_lines = [l.strip() for l in opening.replace('\r', '').split('\n') if l.strip()]
    if opening_lines:
        first_line = opening_lines[0]
        # 检查是否以铺陈标记开头
        for mark in _OPENING_EXPOSITION_MARKS:
            if mark in first_line[:20]:
                opening_penalty = max(opening_penalty, 0.6)
                break
        # 检查是否是纯环境描写开头（无人物、无对白）
        if not any(ch in first_line[:50] for ch in '他说她道"「'):
            if not any(name in first_line[:50] for name in [chr(0x4ED6), chr(0x5979)]):  # 他/她
                if len(opening_lines) >= 2 and not any(ch in opening_lines[1][:50] for ch in '他说她道"「'):
                    opening_penalty = max(opening_penalty, 0.4)

    # 结尾检测：后 300 字
    ending = generated[-300:] if len(generated) > 300 else generated
    # 检查总结标记
    for mark in _ENDING_SUMMARY_MARKS:
        if mark in ending:
            ending_penalty = max(ending_penalty, 0.7)
            break
    # 检查升华标记
    for mark in _ENDING_SUBLIMATION_MARKS:
        if mark in ending:
            ending_penalty = max(ending_penalty, 0.6)
            break
    # 检查收束标记
    for mark in _ENDING_CLOSURE_MARKS:
        if mark in ending:
            ending_penalty = max(ending_penalty, 0.5)
            break
    # 检查结尾悬念钩子（刻意留悬念/预告未知——结尾自然截断不需要钩子）
    for mark in _ENDING_HOOK_MARKS:
        if mark in ending:
            ending_penalty = max(ending_penalty, 0.5)
            break
    # 检查最后一句是否以省略号/总结性语言结尾
    last_sentences = [s.strip() for s in ending.replace('！', '。').replace('？', '。').split('。') if s.strip()]
    if last_sentences:
        last = last_sentences[-1]
        if last.endswith('…') or last.endswith('...'):
            ending_penalty = max(ending_penalty, 0.3)
        if len(last) < 8 and any(mark in last for mark in _ENDING_SUMMARY_MARKS):
            ending_penalty = max(ending_penalty, 0.8)
        # 最后一句以"预告/未知"收尾 → 悬念钩子（只用强信号，避免误伤正常叙述）
        if any(mark in last for mark in ("会一直", "还没结束", "还没完", "没察觉", "并不知道",
                                          "他浑然不觉", "似乎要", "好像要")):
            ending_penalty = max(ending_penalty, 0.5)

    score = 1.0 - max(opening_penalty, ending_penalty)
    return round(max(0.0, min(1.0, score)), 4)


# ---------------------------------------------------------------------------
# 对白质量评分（对抗"刻意松弛感"）
# ---------------------------------------------------------------------------


def dialogue_quality_score(generated: str) -> float:
    """对白质量 (0~1)。检测对话的"刻意松弛感"：
    - 句末语气词密度过高（吧/呢/嘛/啊/呀/哦/啦/唉）——制造慵懒日常感
    - 对话冗长（平均每段对话字数过多）
    - 松弛寒暄词（没事/反正/也还好/不用着急/没什么大不了的）

    1.0 = 对白直接简洁；0.0 = 对白刻意松弛冗长。
    无对话场景返回 0.5（中性，不惩罚）。
    """
    if not generated or len(generated) < 50:
        return 0.5

    dialogues = re.findall(r'「([^」]+)」|"([^"]+)"', generated)
    dialogues = [a or b for a, b in dialogues]
    if not dialogues:
        return 0.5

    total_chars = sum(len(d) for d in dialogues)
    if total_chars == 0:
        return 0.5

    # 1. 句末语气词密度（语气词=慵懒日常感信号）
    #    阈值 0.06：自然对白里「病历呢？」这类疑问语气词很常见，
    #    只有密度明显偏高才判定为「刻意松弛」。太短的对白样本信号不可靠，
    #    全部并入密度不足分支不加罚。
    particle_chars = sum(d.count(p) for d in dialogues for p in "吧呢嘛啊呀哦啦唉哟咯")
    particle_density = particle_chars / total_chars
    density_penalty = 0.0
    if particle_density > 0.06:  # 每 100 字 >6 个语气词 → 过松
        density_penalty = min(0.5, (particle_density - 0.06) / 0.10)

    # 2. 对话冗长：平均每段对话字数 > 40 → 松弛
    avg_len = total_chars / len(dialogues)
    length_penalty = 0.0
    if avg_len > 40:
        length_penalty = min(0.4, (avg_len - 40) / 60)

    # 3. 松弛寒暄词（贯穿全文的松散日常感）
    _LOOSE_WORDS = {
        "没事", "没事儿", "反正", "也还好", "不用着急", "别急",
        "没什么大不了", "不着急", "算了", "随便", "无所谓的",
        "管他呢", "就这样吧", "不急不急",
    }
    loose_hits = sum(1 for w in _LOOSE_WORDS if w in generated)
    loose_penalty = min(0.4, loose_hits * 0.15)

    score = 1.0 - max(density_penalty, length_penalty, loose_penalty)
    return round(max(0.0, min(1.0, score)), 4)


# ---------------------------------------------------------------------------
# 防御性写作评分（对抗"先假设读者不懂而主动解释"）
# ---------------------------------------------------------------------------

_DEFENSIVE_CAUSE_RE = re.compile(r'之所以.{0,12}是因为|之所以.{0,12}就是因为')
_DEFENSIVE_EXCLUDE_RE = re.compile(
    r'并不是.{0,12}(?:而是|只是|才|因为)|倒不是说|这并不是说|这倒不是说'
)
_DEFENSIVE_META_MARKS = {
    "换句话说", "换言之", "也就是说", "说白了", "言下之意",
    "值得注意的是", "需要注意的是", "简单来说",
}


def defensive_writing_score(generated: str) -> float:
    """防御性写作评分 (0~1)。

    AI 的防御性写作：先假设读者看不明白某处，主动停下解释/补述/排除误解，
    破坏叙事的连续性。人类写作习惯是先直接说想说的东西，不防御。
    - 因果补注：之所以…是因为…（停下来解释因果）
    - 排除误解：并不是…而是/只是…、倒不是说…（替读者排除误解）
    - 元话语：换句话说、说白了、值得注意的是（向读者解释叙事意图）

    1.0 = 直接叙述无防御；0.0 = 防御性写作严重。
    """
    if not generated or len(generated) < 100:
        return 1.0

    penalty = 0.0

    cause_hits = len(_DEFENSIVE_CAUSE_RE.findall(generated))
    if cause_hits:
        # 「之所以…是因为」是防御性写作的典型句法，单次出现即重罚
        penalty = max(penalty, min(0.7, 0.45 + (cause_hits - 1) * 0.15))

    excl_hits = len(_DEFENSIVE_EXCLUDE_RE.findall(generated))
    if excl_hits:
        # 「并不是…而是/只是…」同时受基线「陈述句式」约束，属中轻度防御
        penalty = max(penalty, min(0.6, 0.3 + (excl_hits - 1) * 0.15))

    meta_hits = sum(generated.count(m) for m in _DEFENSIVE_META_MARKS)
    if meta_hits:
        penalty = max(penalty, min(0.4, meta_hits * 0.12))

    score = 1.0 - penalty
    return round(max(0.0, min(1.0, score)), 4)


# ---------------------------------------------------------------------------
# 装饰性细节发散评分（对抗 AI 关联发散）
# ---------------------------------------------------------------------------

_AI_DIVERGENCE_WORDS = {
    # 场景模板装饰词（AI 看到关键词就自动联想）
    "锦旗", "妙手回春", "华佗再世", "悬壶济世", "大医精诚", "救死扶伤",
    "牌匾", "白大褂", "听诊器", "病历夹", "消毒水", "输液架",
    # 通用装饰细节词（AI 往"有质感"方向堆砌）
    "边角泛黄", "落满灰尘", "斑驳", "古色古香", "古朴", "典雅", "雅致",
    "氤氲", "袅袅", "静谧", "赫然", "窗棂",
    # 空转的时间/环境
    "午后的阳光", "夕阳的余晖", "晨光透过", "傍晚的",
}


def ai_divergence_score(generated: str) -> float:
    """装饰性细节发散度 (0~1)。

    AI 的"关联发散"：模型看到场景关键词（医生/医院）就自动联想一堆
    装饰性细节（锦旗/妙手回春/泛黄/古朴），写的是模板不是情节。
    这些细节不在节拍里、角色也没互动——纯粹为"写细节而写细节"。

    1.0 = 无发散；0.0 = 装饰细节堆砌严重。
    """
    if not generated or len(generated) < 100:
        return 1.0

    hits = sum(generated.count(w) for w in _AI_DIVERGENCE_WORDS)
    if hits == 0:
        return 1.0

    chinese_chars = sum(1 for c in generated if '一' <= c <= '鿿')
    # 分母设 600 字下限：短文本里单次命中（如场景设定里确实要写锦旗）不被过度放大，
    # 避免"堵过头"误杀合法写作；真正的发散通常是 2+ 个模板词聚簇
    denom = max(0.6, chinese_chars / 1000)
    density = hits / denom  # 每千字命中数

    # 每千字 ≤1.5 个装饰词视为正常（可能是节拍要求），超出后线性罚分
    if density <= 1.5:
        return 1.0
    penalty = min(0.8, (density - 1.5) / 4.0)
    return round(max(0.0, min(1.0, 1.0 - penalty)), 4)


# ---------------------------------------------------------------------------
# 对话间隙冗余评分（对抗对白之间的环境描写/物件装饰）
# ---------------------------------------------------------------------------

_GAP_OBJECT_KW = {
    "墙", "桌", "窗", "门", "锦旗", "牌匾", "钟", "柜", "灯", "屏风",
    "字画", "笔墨", "花瓶", "香炉", "烛台", "帘",
}
_GAP_GAZE_PATTERNS = (
    "目光落在", "视线移到", "抬头看向", "望向", "打量着", "环顾",
    "低头看", "看了看", "瞥见", "注意到墙上", "视线扫过",
)


def dialogue_gap_bloat(generated: str) -> float:
    """对话间隙冗余 (0~1)。

    对白之间应只放角色对对方话语的即时反应。若两句对白之间的叙述
    很长且塞满了环境/物件描写（角色没在互动的东西），就是冗余。

    1.0 = 对白间隙干净；0.0 = 对白间隙被环境描写塞满。
    """
    if not generated or len(generated) < 100:
        return 1.0

    # 定位所有「」对白
    spans = [(m.start(), m.end()) for m in re.finditer(r'「[^」]+」', generated)]
    if len(spans) < 2:
        return 1.0  # 对白太少不判

    bloat = 0.0
    gap_count = 0
    for i in range(len(spans) - 1):
        gap = generated[spans[i][1]:spans[i + 1][0]]
        if not gap.strip():
            continue
        gap_count += 1
        # 间隙含"目光落在/望向"等凝视+物件模式 → 强信号冗余
        if any(p in gap for p in _GAP_GAZE_PATTERNS):
            bloat = max(bloat, 0.6)
        # 间隙过长（>40 字）且含物件词 → 仅当无动作叙述时判冗余。
        # 例：「搁笔。五六页宣纸铺在桌上…周正翻了两页…眉头皱起来」是
        # 动作推进的合法转场（角色在做事），不是装饰发呆，不能误杀。
        elif len(gap) > 40 and any(kw in gap for kw in _GAP_OBJECT_KW):
            action_count = sum(gap.count(v) for v in _ENDING_ACTION_VERBS)
            if action_count <= 1:
                bloat = max(bloat, 0.5)

    if gap_count == 0:
        return 1.0
    return round(max(0.0, min(1.0, 1.0 - bloat)), 4)


# ---------------------------------------------------------------------------
# 结尾动作密度评分（对抗"氛围收束"）
# ---------------------------------------------------------------------------

_ENDING_ACTION_VERBS = {
    "走", "站", "坐", "放", "推", "拉", "拿", "看", "说", "问", "答",
    "转", "停", "关", "开", "起", "落", "握", "拍", "点", "抬", "低",
    "起身", "离开", "出去", "进来", "推门", "关门", "放下", "拿起",
    "合上", "翻开", "点了点头", "摇了摇头", "挂了",
}

_ENDING_ATMOSPHERE_WORDS = {
    "光", "灯", "风", "雨", "影", "色", "静", "暗",
    "缓缓", "轻轻", "渐渐", "微微", "终于", "月光", "灯光", "日光灯",
    "嘟囔", "翻了个身", "沉默", "漫长",
}

# "静默收尾"模式信号：结尾 80 字内出现即判为坏结尾
# 不是靠动词密度，而是直接识别 AI 收尾的三种签名：
#  1. 微动作特写（手指/膝盖/眼皮/嘴角 + 敲/叩/捻/抚）——把动作拆成逐帧特写
#  2. 无关声响/环境事件（嘟囔/翻身/灯闪/长发遮脸）——与情节无关的旁观细节
#  3. 静默留白（又停下了/停住）——刻意制造"余韵"
_ENDING_MICRO_TELLS = (
    "手指在", "指尖", "膝盖上", "眼皮", "嘴角", "敲了三下", "敲了敲",
    "嘟囔了一句", "翻了个身", "闪了一下", "长发遮", "头发遮",
    "又停下了", "又停住", "沉默不语",
    "目光落在", "一点一点暗", "夜很深",
    # 氛围收尾签名（常在最后 1-2 句出现）
    "良久", "收回视线", "收回目光", "很静", "只剩", "绕着发梢",
    "静下来", "没再开口",
)

# "悬念预告/钩子"收尾签名：叙述者旁白预告后续事件，不是动作结果
# 例：「迎检的事刚了。新的麻烦，已经找上门了。」→ 双行旁白预告
_ENDING_HOOK_TELLS = (
    # 抽象预告名词（新事件/更大的事情将至）
    "新的麻烦", "更大的风波", "更大的风浪", "新的危机", "新的风暴",
    "更大的挑战", "更大的事情", "新的考验",
    # 预告结构（某事才开始/未结束）
    "这才刚刚开始", "才刚刚开始", "这才只是开始", "只是开始",
    "一切才刚刚", "远远没有结束", "远未结束",
    # 等待/未知预告
    "等待他的", "等着他的", "等着他们", "他还不知道", "他不知道，",
    "将要面对", "即将面对", "即将到来", "即将发生", "即将降临",
    "找上门", "找上门来",
    # 时代/命运级预告（结尾常以此类升华句收束）
    "从这一刻起", "从这一天起", "从今天起", "多年以后", "许多年后", "多年后",
    # 叙述者旁白
    "而这一切", "而这只是", "而这不过",
)


def ending_action_density(generated: str) -> float:
    """结尾动作密度 (0~1)。

    好结尾：最后 200 字以动作/对话/事实收束，写完就停。
    坏结尾：以环境描写、氛围渲染、微动作特写收尾（"静默收尾"），
    或加总结/升华/悬念预告。

    1.0 = 结尾是动作/对话；0.0 = 结尾是氛围/微动作/总结。
    """
    if not generated or len(generated) < 150:
        return 1.0

    ending = generated[-200:]
    # 结尾是对话 → 好（以对白收束）
    if re.search(r'「[^」]+」[。！？]?\s*$', ending):
        return 1.0

    # "静默收尾"模式检测：结尾 120 字内有微动作特写/无关声响/静默留白 → 直接判坏
    # 例：「手指在膝盖上轻轻敲了三下，又停下了」→ 0.3（不进入动词密度兜底）
    final_beat = ending[-120:]
    if any(t in final_beat for t in _ENDING_MICRO_TELLS):
        return 0.3
    # "悬念预告/钩子"收尾：叙述者旁白预告后续事件 → 判坏
    # 例：「迎检的事刚了。新的麻烦，已经找上门了。」→ 0.3
    if any(t in final_beat for t in _ENDING_HOOK_TELLS):
        return 0.3

    action_hits = sum(ending.count(v) for v in _ENDING_ACTION_VERBS)
    atmos_hits = sum(ending.count(w) for w in _ENDING_ATMOSPHERE_WORDS)

    if action_hits >= 2 and atmos_hits == 0:
        return 1.0
    if action_hits > atmos_hits:
        return 0.8
    if atmos_hits >= action_hits + 2:
        # 氛围/微动作收尾 → 罚
        return 0.4
    return 0.6


# ---------------------------------------------------------------------------
# 内容质量评分（语义覆盖 + 流畅度）
# ---------------------------------------------------------------------------


def content_quality_score(
    semantic_coverage: float = 0.5,
    fluency: float = 0.5,
    *,
    compression_ratio: float | None = None,
    narrative_cohesion: float | None = None,
    function_word_kl: float | None = None,
    dialogue_quality: float | None = None,
    defensive_writing: float | None = None,
    ai_divergence: float | None = None,
    dialogue_gap: float | None = None,
    ending_action: float | None = None,
    weights: dict[str, float] | None = None,
) -> float:
    """内容质量评分 (0~1)。

    v4.0 蜕变版 — 综合多项质量指标：
    - semantic_coverage: 语义覆盖率 (embedding 余弦相似度)
    - fluency: 流畅度 (4-gram 重复率 + 标点密度)
    - compression_ratio: 信息密度 (gzip 压缩比)
    - narrative_cohesion: 叙事连贯度 (句子长度模式)
    - function_word_kl: 功能词风格距离 (KL 散度)
    - dialogue_quality: 对白质量（抗"刻意松弛感"，v5.6 新增）
    - defensive_writing: 防御性写作评分（抗过度解释，v5.6 新增）
    - ai_divergence: 装饰性细节发散（抗 AI 关联发散，v5.6 补丁新增）
    - dialogue_gap: 对话间隙冗余（抗对白之间环境描写，v5.6 补丁新增）
    - ending_action: 结尾动作密度（抗"氛围收束"，v5.6 补丁新增）

    权重通过 weights 自定义；未指定的维度权重为 0（不影响旧调用）。
    """
    w = weights or {
        "semantic": 0.25, "fluency": 0.20,
        "compression": 0.10, "cohesion": 0.10, "fword_kl": 0.10,
        "dialogue": 0.15, "defensive": 0.10,
        "ai_divergence": 0.05, "dialogue_gap": 0.05, "ending_action": 0.08,
    }

    components = {
        "semantic": semantic_coverage,
        "fluency": fluency,
        "compression": compression_ratio if compression_ratio is not None else 0.5,
        "cohesion": narrative_cohesion if narrative_cohesion is not None else 0.5,
        "fword_kl": function_word_kl if function_word_kl is not None else 0.5,
        "dialogue": dialogue_quality if dialogue_quality is not None else 0.5,
        "defensive": defensive_writing if defensive_writing is not None else 0.5,
        "ai_divergence": ai_divergence if ai_divergence is not None else 0.5,
        "dialogue_gap": dialogue_gap if dialogue_gap is not None else 0.5,
        "ending_action": ending_action if ending_action is not None else 0.5,
    }

    score = 0.0
    total_weight = 0.0
    for key, val in components.items():
        weight = w.get(key, 0.0)
        score += weight * val
        total_weight += weight

    if total_weight > 0:
        score /= total_weight
    return round(max(0.0, min(1.0, score)), 4)


# ---------------------------------------------------------------------------
# 综合评分（v4.0 蜕变版 — 5 项可调权重复合评分）
# ---------------------------------------------------------------------------


def compute_composite_score_v5(
    v_cosine: float,
    mean_abs_delta_pct: float,
    *,
    dim_deltas_pct: list[float] | None = None,
    length_alignment: float = 1.0,
    serialization_fit: float = 1.0,
    content_quality: float = 1.0,
    factual_consistency: float | None = None,
    plot_fidelity: float | None = None,
    s_char: float | None = None,
    semantic_coverage: float | None = None,
    plot_sim: float | None = None,
    ai_flavor: float | None = None,  # 【v5.27】AI 味分（1=干净）；None 不惩罚
    weights: dict[str, float] | None = None,
) -> float:
    """综合分（v4.0 蜕变版 — 5/6/8/v519 维复合评分，v5.11 内容主导）

    四种模式（按传参自动选择，向后兼容）：
      - 都不传 → 5 维公式（保持 v4.0 行为完全不变）：
          score = β₁×V_cosine + β₂×dim_match_pure + β₃×length_alignment
                + β₄×serialization_fit + β₅×content_quality
      - 只传 factual_consistency → 6 维公式，默认权重：
          b1 0.22 / b2 0.22 / b3 0.22 / b4 0.09 / b5 0.13 / b6 0.12
      - 传了 plot_fidelity + s_char（无 plot_sim）→ 8 维公式（v5.12 内容主导，复现原文核心）：
          b1 0.08 / b2 0.00 / b3 0.00 / b4 0.07 / b5 0.15 / b6 0.18 / b7 0.22 / b8 0.30
        b5 起 = semantic_coverage（语义覆盖，v5.12 起含义变更，未传时回退 content_quality）。
        内容核心（b5+b6+b7+b8）合计 0.85——评分由"复现了多少原文"主导；
        被剔标准（b2 dim_match / b3 字数 / 压缩比 / 连贯度 / 流畅度 / 功能词KL / 抗AI五项）
        不再参与综合分（继续计算，作诊断）。
      - 【v5.20】传了 plot_sim + s_char → v519 模式（用户拍板，替代 b5-b8 做排序）：
          score = w_syn×v_cosine + w_plot×plot_sim + w_char×s_char
        权重从 config.SETTINGS 读（v519_w_char/plot/syn，默认 0.5/0.3/0.2），L1 归一化。
        剧情相似度 = generated 句子级匹配原文剧情点（plot_similarity 模块）。
        b5-b8 各分量（sem_cov/pf/fc）继续计算，作诊断输出，不参与综合分。

    dim_match_pure = mean_dim_match × (1 − extreme_penalty)
    mean_dim_match = Σ(max(0, 1 − δ_i/100)) / N
    extreme_penalty = count(δ_i > 50) / N × 0.5

    Args:
        v_cosine: V 向量余弦相似度 (0~1)
        mean_abs_delta_pct: 平均绝对偏差百分比 (0~100+)
        dim_deltas_pct: 逐维度偏差百分比列表，用于极端惩罚
        length_alignment: 字数对齐度 (0~1)
        serialization_fit: 连载适配度 (0~1)
        content_quality: 内容质量 (0~1)；8 维模式下 b5 已被 semantic_coverage 取代，仅作回退/诊断
        semantic_coverage: 语义覆盖 (0~1)，8 维模式 b5 取值；None 时回退 content_quality（5/6 维兼容）
        factual_consistency: 事实一致性 (0~1)，None 时不参与评分（5 维模式）
        plot_fidelity: 事件保真 (0~1)，None 时不参与评分
        s_char: 原文字符相似度 (0~1)，None 时不参与评分
        plot_sim: 剧情相似度 (0~1)，与 s_char 同时传 → v519 模式（0.5字符+0.3剧情+0.2句式）
        weights: 自定义权重 {b1~b8: float}，未指定则按模式用默认值，自动 L1 归一化
                 （v519 模式忽略，权重从 SETTINGS 读）

    Returns:
        综合分 (0~1)，越大越好
    """
    if plot_sim is not None and s_char is not None:
        # 【v5.20】v519 模式：0.5×字符 + 0.3×剧情 + 0.2×句式（用户拍板 v5.19 harness 实测锁定）
        from .config import SETTINGS as _SETTINGS

        w_char = _SETTINGS.v519_w_char
        w_plot = _SETTINGS.v519_w_plot
        w_syn = _SETTINGS.v519_w_syn
        _tot = w_char + w_plot + w_syn
        if _tot <= 0:
            _tot, w_char, w_plot, w_syn = 1.0, 0.5, 0.3, 0.2
        _score = (w_syn * v_cosine + w_plot * plot_sim + w_char * s_char) / _tot
        # 【v5.27】AI 味惩罚（乘法，不稀释字符/剧情/句式三权重）：
        # ai_flavor=1（无AI味）→ 无惩罚；ai_flavor=0 → 最多扣 penalty 比例。
        # 这样修复 AI 味（删注水）不会以牺牲 s_char 等原维为代价。
        if ai_flavor is not None:
            w_pen = float(_SETTINGS.ai_flavor_penalty)
            _score = _score * (1.0 - w_pen * (1.0 - max(0.0, min(1.0, ai_flavor))))
        return round(max(0.0, min(1.0, _score)), 4)
    if plot_fidelity is not None or s_char is not None:
        # 8 维模式（v5.12 内容主导）：内容核心 b5+b6+b7+b8=0.85，风格下限 b1，连载约束 b4
        w = dict(weights) if weights else {
            "b1": 0.08, "b2": 0.0, "b3": 0.0, "b4": 0.07,
            "b5": 0.15, "b6": 0.18, "b7": 0.22, "b8": 0.30,
        }
        w.setdefault("b6", 0.18)
        w.setdefault("b7", 0.22)
        w.setdefault("b8", 0.30)
        _tot = sum(w.values())
        if _tot > 0 and abs(_tot - 1.0) > 1e-9:
            w = {k: v / _tot for k, v in w.items()}
    elif factual_consistency is not None:
        # 6 维模式：默认权重 b1~b6；b6 缺失时补默认值，并 L1 归一化
        # （兼容只传 b1~b5 的旧权重，保证 Σβ = 1）
        w = dict(weights) if weights else {
            "b1": 0.22, "b2": 0.22, "b3": 0.22,
            "b4": 0.09, "b5": 0.13, "b6": 0.12,
        }
        w.setdefault("b6", 0.12)
        _tot = sum(w.values())
        if _tot > 0 and abs(_tot - 1.0) > 1e-9:
            w = {k: v / _tot for k, v in w.items()}
    else:
        # 5 维模式：保持 v4.0 默认与行为
        w = weights or {"b1": 0.25, "b2": 0.25, "b3": 0.25, "b4": 0.10, "b5": 0.15}
    b1 = w.get("b1", 0.25)  # V_cosine
    b2 = w.get("b2", 0.25)  # dim_match
    b3 = w.get("b3", 0.25)  # length_alignment
    b4 = w.get("b4", 0.10)  # serialization_fit
    b5 = w.get("b5", 0.15)  # semantic_coverage（v5.12 起含义变更；未传回退 content_quality）
    b6 = w.get("b6", 0.0)   # factual_consistency
    b7 = w.get("b7", 0.0)   # plot_fidelity（事件保真）
    b8 = w.get("b8", 0.0)   # s_char（原文字符相似度）

    # dim_match: 逐维度匹配度 + 极端惩罚
    if dim_deltas_pct and len(dim_deltas_pct) > 0:
        per_dim_match = [max(0.0, 1.0 - d / 100.0) for d in dim_deltas_pct]
        mean_dim_match = sum(per_dim_match) / len(per_dim_match)
        extreme_count = sum(1 for d in dim_deltas_pct if d > 50)
        extreme_penalty = (extreme_count / len(dim_deltas_pct)) * 0.5
    else:
        mean_dim_match = max(0.0, 1.0 - mean_abs_delta_pct / 100.0)
        extreme_penalty = 0.0

    dim_match_pure = mean_dim_match * (1.0 - extreme_penalty)

    b5_val = semantic_coverage if semantic_coverage is not None else content_quality
    score = (b1 * v_cosine
             + b2 * dim_match_pure
             + b3 * length_alignment
             + b4 * serialization_fit
             + b5 * b5_val)
    if factual_consistency is not None:
        score += b6 * factual_consistency
    if plot_fidelity is not None:
        score += b7 * plot_fidelity
    if s_char is not None:
        score += b8 * s_char
    return round(max(0.0, min(1.0, score)), 4)


# 保留旧版别名以向后兼容
compute_composite_score = compute_composite_score_v5


def delta_to_prompt_suggestions(
    deltas: dict,
    thresholds: dict[str, dict[str, float]] | None = None,
) -> list[dict]:
    """Convert dimension deltas into concrete prompt modification suggestions.

    Args:
        deltas: Output from compute_dimension_deltas().
        thresholds: 动态阈值，{field: {medium: float, high: float, n_samples: int}}。
                    None 时使用硬编码默认阈值。

    Returns:
        List of {"field": str, "severity": "high"|"medium"|"low", "suggestion": str}
    """
    suggestions = []
    deltas_dict = deltas.get("deltas", {})
    has_dynamic = thresholds is not None

    for field, direction, default_threshold, suggestion in _DELTA_TO_SUGGESTION_MAP:
        d = deltas_dict.get(field)
        if not d:
            continue
        if d["direction"] != direction:
            continue

        abs_delta = abs(d["delta_pct"])

        # 取阈值：动态 > 默认
        if thresholds and field in thresholds:
            t = thresholds[field]
            medium_th = t.get("medium", default_threshold)
            high_th = t.get("high", default_threshold * 1.5)
        else:
            medium_th = default_threshold
            high_th = default_threshold * 1.5

        if abs_delta < medium_th:
            continue  # 未达到触发阈值

        if abs_delta >= high_th:
            severity = "high"
        else:
            severity = "medium"

        suggestions.append({
            "field": field,
            "severity": severity,
            "delta_pct": d["delta_pct"],
            "suggestion": suggestion,
            "threshold_source": "data_driven" if has_dynamic and thresholds and field in thresholds else "hardcoded",
        })

    # Sort by severity then by absolute delta
    severity_order = {"high": 0, "medium": 1, "low": 2}
    suggestions.sort(key=lambda s: (severity_order.get(s["severity"], 3), -abs(s["delta_pct"])))

    return suggestions


# ---------------------------------------------------------------------------
# Convenience: full analysis of a training round
# ---------------------------------------------------------------------------

def analyze_round(
    target_text: str,
    variants: list[dict],
    *,
    composite_weights: dict[str, float] | None = None,
    suggestion_thresholds: dict[str, dict[str, float]] | None = None,
) -> dict:
    """Analyze all variants in a training round against the target text.

    Args:
        target_text: The original chapter text (ground truth).
        variants: List of {"index", "angle", "paragraph", "generated_text"}.
        composite_weights: 综合分权重 {alpha, beta, gamma}。
        suggestion_thresholds: 动态建议阈值（来自经验库统计）。

    Returns:
        {
            "target_fingerprint": V(target_text),
            "variants": [
                {
                    ...original fields...,
                    "char_similarity": float,
                    "structural_deltas": {...},
                    "composite_score": float,
                    "mean_abs_delta_pct": float,
                    "suggestions": [...],
                }
            ],
            "best_variant": int,      # index of best by composite_score
            "best_score": float,       # best composite_score
            "best_char_similarity": float,
            "suggestions_source": "data_driven" | "hardcoded",
        }
    """
    target_fp = extract_structural_vector(target_text)
    best_idx = -1
    best_score = -1.0
    best_char = -1.0

    suggestions_source = "hardcoded" if suggestion_thresholds is None else "data_driven"

    analyzed_variants = []
    for v in variants:
        generated = v.get("generated_text", "")
        if not generated:
            analyzed_variants.append({
                **v, "char_similarity": 0.0, "structural_deltas": None,
                "composite_score": 0.0, "mean_abs_delta_pct": 0.0,
                "suggestions": [],
            })
            continue

        char_sim = compute_char_similarity(generated, target_text)
        gen_fp = extract_structural_vector(generated)
        deltas = compute_dimension_deltas(target_fp, gen_fp)

        # 计算平均偏差百分比
        deltas_map = deltas.get("deltas", {})
        delta_vals = [abs(d.get("delta_pct", 0)) for d in deltas_map.values()]
        mean_delta = sum(delta_vals) / len(delta_vals) if delta_vals else 0.0

        v_cosine = deltas.get("cosine_similarity", 0.0)
        # 提取逐维度偏差用于极端惩罚（v5.5 量化规则）
        dim_deltas = [abs(d.get("delta_pct", 0)) for d in deltas_map.values()]
        comp_score = compute_composite_score(
            v_cosine, mean_delta, composite_weights,
            dim_deltas_pct=dim_deltas,
        )

        suggestions = delta_to_prompt_suggestions(deltas, suggestion_thresholds)

        if comp_score > best_score:
            best_score = comp_score
            best_char = char_sim
            best_idx = v.get("index", -1)

        analyzed_variants.append({
            **v,
            "char_similarity": round(char_sim, 4),
            "structural_deltas": deltas,
            "composite_score": comp_score,
            "mean_abs_delta_pct": round(mean_delta, 2),
            "suggestions": suggestions,
        })

    return {
        "target_fingerprint": target_fp,
        "variants": analyzed_variants,
        "best_variant": best_idx,
        "best_score": round(best_score, 4),
        "best_char_similarity": round(best_char, 4),
        "suggestions_source": suggestions_source,
    }


# ---------------------------------------------------------------------------
# 学习曲线拟合 + 收敛预测
# ---------------------------------------------------------------------------

def fit_learning_curve(scores: list[float]) -> dict:
    """指数收敛模型拟合 + 预测。

    模型：score(t) = S_max − (S_max − S_0) * exp(−t/τ)

    Args:
        scores: 每轮 best_score 列表（按轮次顺序）。

    Returns:
        {
          "S_max": 极限分（收敛上限）,
          "S_0": 初始分（第 0 轮）,
          "tau": 收敛速度常数（越大越慢）,
          "predicted_next": 下一轮预测分,
          "rounds_to_95pct": 达到 95% 极限分需要的轮数（从现在算起）,
          "remaining_gain_pct": 剩余提升空间（百分比，相对当前分）,
          "confidence": "high" | "medium" | "low",  # 数据量决定
          "fit_error": None | str,  # 拟合失败原因
        }

    实现：
    - 优先用 scipy.optimize.curve_fit
    - 无 scipy 时用网格搜索（S_max 在 [current, 1.0] 间扫，τ 在 [0.5, 10] 间扫）
    """
    if len(scores) < 2:
        return {
            "S_max": scores[0] if scores else 0.0,
            "S_0": scores[0] if scores else 0.0,
            "tau": 0.0,
            "predicted_next": scores[-1] if scores else 0.0,
            "rounds_to_95pct": 0,
            "remaining_gain_pct": 0.0,
            "confidence": "low",
            "fit_error": "insufficient_data",
        }

    # 置信度
    if len(scores) >= 8:
        confidence = "high"
    elif len(scores) >= 5:
        confidence = "medium"
    else:
        confidence = "low"

    t = list(range(len(scores)))
    y = scores
    current = y[-1]

    # 先尝试 scipy
    try:
        from scipy.optimize import curve_fit  # type: ignore
        import numpy as np  # type: ignore

        def model(t_arr, s_max, s0, tau):
            return s_max - (s_max - s0) * np.exp(-t_arr / tau)

        t_np = np.array(t, dtype=float)
        y_np = np.array(y, dtype=float)

        # 初始猜测
        p0 = [max(max(y) * 1.1, current + 0.05), y[0], 3.0]
        bounds = (
            [current, 0.0, 0.1],  # 下界：S_max 不能低于当前分
            [1.0, current, 20.0],  # 上界：S_max ≤ 1
        )

        popt, _ = curve_fit(model, t_np, y_np, p0=p0, bounds=bounds, maxfev=5000)
        s_max, s0, tau = [float(v) for v in popt]

    except Exception:
        # Fallback：网格搜索
        # S_max 候选：从 current+0.01 到 min(1.0, current + 0.5)，步长 0.01
        # τ 候选：0.5 到 8，步长 0.5
        best_sse = float("inf")
        s_max = current + 0.05
        s0 = y[0]
        tau = 3.0

        import math as _m
        s_max_candidates = []
        top = min(1.0, current + 0.5)
        step = 0.01
        v = current + 0.01
        while v <= top + 1e-9:
            s_max_candidates.append(v)
            v += step
        if not s_max_candidates:
            s_max_candidates = [min(1.0, current + 0.05)]

        tau_candidates = [0.5, 1.0, 1.5, 2.0, 2.5, 3.0, 4.0, 5.0, 6.0, 8.0]

        for sm in s_max_candidates:
            for t_val in tau_candidates:
                sse = 0.0
                for ti, yi in zip(t, y):
                    pred = sm - (sm - s0) * _m.exp(-ti / t_val) if t_val > 0 else sm
                    sse += (pred - yi) ** 2
                if sse < best_sse:
                    best_sse = sse
                    s_max = sm
                    tau = t_val

    # 预测
    import math as _m
    next_t = len(scores)
    predicted_next = s_max - (s_max - s0) * _m.exp(-next_t / tau) if tau > 0 else s_max
    predicted_next = max(current, min(1.0, predicted_next))

    # 达到 95% 极限分还需几轮
    # S_max − (S_max−S_0)e^(−t/τ) = 0.95 * S_max
    # → 0.05 * S_max = (S_max - S_0) * e^(−t/τ)
    # → t = -τ * ln(0.05 * S_max / (S_max - S_0))
    if s_max > current and s_max > s0 and tau > 0:
        ratio = 0.05 * s_max / (s_max - s0)
        if ratio > 0:
            t_total = -tau * _m.log(ratio)
            rounds_left = max(0, int(_m.ceil(t_total - len(scores))))
        else:
            rounds_left = 0
        remaining_gain = max(0.0, (s_max - current) / max(current, 1e-6) * 100)
    else:
        rounds_left = 0
        remaining_gain = 0.0

    return {
        "S_max": round(s_max, 4),
        "S_0": round(s0, 4),
        "tau": round(tau, 3),
        "predicted_next": round(predicted_next, 4),
        "rounds_to_95pct": rounds_left,
        "remaining_gain_pct": round(remaining_gain, 1),
        "confidence": confidence,
        "fit_error": None,
    }
