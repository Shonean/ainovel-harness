"""Diff 分析与错误分类模块。

对生成文本和目标文本进行字符级差异分析，将差异分类为可操作的错误类别，
并将错误映射回 prompt 中的具体规则，生成精确的修改指令。

核心流水线：
    compute_diff → classify_errors → analyze_root_cause → build_modification_plan

纯计算模块，不依赖 LLM 调用。输出供 templates.py 中的模板使用。
"""
from __future__ import annotations

import difflib
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


# ---------------------------------------------------------------------------
# 数据结构
# ---------------------------------------------------------------------------

class ErrorCategory(str, Enum):
    """错误类别枚举。"""
    AI_SMELL_PATTERN = "ai_smell"          # AI 味句式
    CONTENT_FABRICATION = "fabrication"    # 编造原文没有的内容
    CONTENT_OMISSION = "omission"          # 遗漏原文关键内容
    DIALOGUE_MISSING = "dialogue_missing"  # 原文有对白但生成没有
    DIALOGUE_EXTRA = "dialogue_extra"      # 生成有对白但原文没有
    STRUCTURE_DEVIATION = "structure"      # 段落/句数结构差异
    LENGTH_DEVIATION = "length"            # 总长度差异
    PUNCTUATION_DEVIATION = "punctuation"  # 标点使用差异
    WORDING_DRIFT = "wording"              # 同义替换/语序变化


@dataclass
class DiffBlock:
    """单个 diff 操作块。"""
    op: str                # 'equal' | 'replace' | 'insert' | 'delete'
    gen_text: str          # 生成侧的文本片段
    target_text: str       # 目标侧的文本片段
    gen_start: int         # 生成文本中的起始位置
    gen_end: int           # 生成文本中的结束位置
    target_start: int      # 目标文本中的起始位置
    target_end: int        # 目标文本中的结束位置

    @property
    def is_different(self) -> bool:
        return self.op != "equal"

    @property
    def size(self) -> int:
        return max(len(self.gen_text), len(self.target_text))


@dataclass
class ClassifiedError:
    """一个已分类的错误。"""
    category: ErrorCategory
    diff_block: DiffBlock
    evidence: str          # 错误证据（具体的匹配文本）
    description: str       # 人类可读的描述
    severity: float = 0.0  # 严重程度 (0-1)


@dataclass
class ModificationAction:
    """一条 prompt 修改动作。"""
    priority: int                     # P0 / P1 / P2 (越小越优先)
    error_category: ErrorCategory     # 对应的错误类别
    target_rules: list[str]           # 需要修改的 prompt 规则名称
    action_type: str                  # 'add' | 'strengthen' | 'remove' | 'fine_tune' | 'replace'
    instruction: str                  # 人类可读的修改指令
    example_fix: str = ""             # 修改示例


@dataclass
class ErrorReport:
    """完整的错误分析报告。"""
    similarity: float
    length_diff: float
    gen_len: int
    target_len: int
    diff_blocks: list[DiffBlock] = field(default_factory=list)
    errors: list[ClassifiedError] = field(default_factory=list)
    modifications: list[ModificationAction] = field(default_factory=list)
    summary: str = ""

    @property
    def has_critical_errors(self) -> bool:
        """是否有严重错误（编造内容、大段遗漏、对白严重缺失）。"""
        critical_categories = {
            ErrorCategory.CONTENT_FABRICATION,
            ErrorCategory.CONTENT_OMISSION,
            ErrorCategory.DIALOGUE_MISSING,
        }
        return any(e.category in critical_categories for e in self.errors)

    @property
    def error_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for e in self.errors:
            key = e.category.value
            counts[key] = counts.get(key, 0) + 1
        return counts


# ---------------------------------------------------------------------------
# AI 味句式检测模式
# ---------------------------------------------------------------------------

# 每组: (正则, 描述, 严重度系数 0-1)
_AI_SMELL_PATTERNS: list[tuple[str, str, float]] = [
    # 判断句式（最严重的 AI 味标志）
    (r'不是[^。！？；\n]{1,30}(而是|是)[^。！？；\n]{1,30}', '「不是...而是/是...」判断句', 0.95),
    (r'[一-鿿]{1,4}是[一-鿿]{1,6}的(?![一-鿿])', '「...是...的」强调结构', 0.85),
    # 情感标签
    (r'心中涌起[^。！？；\n]{0,10}', '「心中涌起」情感标签', 0.80),
    (r'不由得[^。！？；\n]{0,5}', '「不由得」情感标签', 0.70),
    (r'不禁[^。！？；\n]{0,5}(?!锢|忌|止)', '「不禁」情感标签', 0.70),
    (r'忍不住[^。！？；\n]{0,5}', '「忍不住」情感标签', 0.65),
    # 文言否定判断
    (r'并非[^。！？；\n]{1,20}', '「并非」文言否定判断', 0.75),
    (r'绝非[^。！？；\n]{1,20}', '「绝非」文言否定判断', 0.75),
    # 判断句式
    (r'与其说[^。！？]*不如说[^。！？]*', '「与其说...不如说...」判断句', 0.80),
    # 过度比喻 / 模板化修辞
    (r'俨然[^。！？；\n]{0,15}', '「俨然」模板化修辞', 0.60),
    (r'仿佛[^。！？；\n]{0,10}(?!回到|看见|听到|听到|回到)', '「仿佛」过度比喻', 0.50),
    # 解释性总结（段落末尾）
    (r'[^。！？\n]{5,30}(这意味着|这说明|由此可见|总之|归根结底)[^。！？\n]{5,30}', '解释性总结', 0.90),
    # 模式化心理描写
    (r'(心想|暗想|心道|暗道)[：:][^。！？\n]{5,50}', '模式化心理描写标签', 0.65),
]

# 中文引号字符
_QUOTE_CHARS = set('「」『』""\'\'＂＇')

# 标点字符
_PUNCT_CHARS = set('，。！？；：、—…～·,.;:!?-')


# ---------------------------------------------------------------------------
# Diff 计算
# ---------------------------------------------------------------------------

def compute_diff(generated: str, target: str) -> list[DiffBlock]:
    """使用 difflib.SequenceMatcher 计算字符级差异块。

    Args:
        generated: 生成的正文文本
        target: 目标原文

    Returns:
        DiffBlock 列表，包含所有差异块（含 equal 块）
    """
    matcher = difflib.SequenceMatcher(
        None,   # isjunk=None，不跳过任何字符
        generated,
        target,
        autojunk=False,  # 禁用自动 junk 检测，保持精确
    )

    blocks: list[DiffBlock] = []
    for op, i1, i2, j1, j2 in matcher.get_opcodes():
        block = DiffBlock(
            op=op,
            gen_text=generated[i1:i2],
            target_text=target[j1:j2],
            gen_start=i1,
            gen_end=i2,
            target_start=j1,
            target_end=j2,
        )
        blocks.append(block)

    return blocks


def compute_diff_normalized(
    generated: str,
    target: str,
    normalize_fn,
) -> list[DiffBlock]:
    """在规范化后的文本上计算 diff。

    对规范化文本计算 diff，便于忽略空白/标点差异。
    """
    gen_norm = normalize_fn(generated)
    tgt_norm = normalize_fn(target)
    return compute_diff(gen_norm, tgt_norm)


# ---------------------------------------------------------------------------
# 辅助检测函数
# ---------------------------------------------------------------------------

def _count_quoted_segments(text: str) -> int:
    """统计文本中引号包裹的段落数（近似对白数）。"""
    count = 0
    in_quote = False
    for ch in text:
        if ch in '「『""\'＂＇':
            in_quote = not in_quote
            if not in_quote:
                count += 1
    return count


def _has_dialogue(text: str) -> bool:
    """检测文本是否包含对白。"""
    return _count_quoted_segments(text) > 0


def _extract_named_entities(text: str) -> set[str]:
    """从文本中提取可能的专有名词（中文人名/地名/组织名等）。

    使用简单启发式：连续 2-4 个汉字且不在常见词表中。
    """
    # 排除常见虚词和普通词汇
    _COMMON_WORDS = {
        '他们', '我们', '你们', '自己', '什么', '怎么', '为什么',
        '可以', '已经', '没有', '还是', '但是', '因为', '所以',
        '如果', '虽然', '不过', '而且', '然后', '之后', '之前',
        '这个', '那个', '哪个', '这么', '那么', '这里', '那里',
        '一个', '一种', '一些', '一下', '一点', '一直', '一定',
        '起来', '下来', '出来', '过来', '回来', '进去', '上去',
        '不是', '不会', '不能', '不要', '不用', '不敢',
    }

    # 中文人名/专有名词模式：2-4 个连续汉字
    pattern = re.compile(r'[一-鿿]{2,4}')
    matches = pattern.findall(text)
    entities = set()
    for m in matches:
        if m not in _COMMON_WORDS:
            entities.add(m)
    return entities


def _count_paragraphs(text: str) -> int:
    """统计段落数（空行分隔）。"""
    parts = re.split(r'\n\s*\n', text.strip())
    return len([p for p in parts if p.strip()])


def _count_sentences(text: str) -> int:
    """统计句子数（以句号、问号、感叹号分隔）。"""
    return len(re.findall(r'[。！？]', text))


def _count_punctuation_by_type(text: str) -> dict[str, int]:
    """统计各类标点出现次数。"""
    counts: dict[str, int] = {
        "comma": 0,       # 逗号类
        "period": 0,      # 句号类
        "dash": 0,        # 破折号
        "ellipsis": 0,    # 省略号
        "question": 0,    # 问号
        "exclamation": 0, # 感叹号
        "colon": 0,       # 冒号
        "semicolon": 0,   # 分号
        "quote": 0,       # 引号
    }
    for ch in text:
        if ch in '，,':
            counts["comma"] += 1
        elif ch in '。.':
            counts["period"] += 1
        elif ch in '——-—':
            counts["dash"] += 1
        elif ch in '…～~':
            counts["ellipsis"] += 1
        elif ch in '？?':
            counts["question"] += 1
        elif ch in '！!':
            counts["exclamation"] += 1
        elif ch in '：:':
            counts["colon"] += 1
        elif ch in '；;':
            counts["semicolon"] += 1
        elif ch in '「」『』""\'\'＂＇':
            counts["quote"] += 1
    return counts


# ---------------------------------------------------------------------------
# 错误分类器
# ---------------------------------------------------------------------------

def classify_errors(
    diff_blocks: list[DiffBlock],
    generated: str,
    target: str,
) -> list[ClassifiedError]:
    """对 diff 块进行分类，识别每处差异的错误类别。

    Args:
        diff_blocks: compute_diff() 的输出
        generated: 原始生成文本
        target: 原始目标文本

    Returns:
        ClassifiedError 列表（仅包含错误，不含 equal 块）
    """
    errors: list[ClassifiedError] = []

    # 预计算全局特征
    gen_has_dialogue = _has_dialogue(generated)
    tgt_has_dialogue = _has_dialogue(target)
    gen_entities = _extract_named_entities(generated)
    tgt_entities = _extract_named_entities(target)
    gen_paragraphs = _count_paragraphs(generated)
    tgt_paragraphs = _count_paragraphs(target)

    for block in diff_blocks:
        if block.op == "equal":
            continue

        classified = _classify_single_block(
            block,
            generated, target,
            gen_has_dialogue, tgt_has_dialogue,
            gen_entities, tgt_entities,
            gen_paragraphs, tgt_paragraphs,
        )
        if classified:
            errors.append(classified)

    # 全局级别错误（不绑定到单个 diff 块）
    _add_global_errors(
        errors, generated, target,
        gen_has_dialogue, tgt_has_dialogue,
        gen_paragraphs, tgt_paragraphs,
    )

    return errors


def _classify_single_block(
    block: DiffBlock,
    generated: str,
    target: str,
    gen_has_dialogue: bool,
    tgt_has_dialogue: bool,
    gen_entities: set[str],
    tgt_entities: set[str],
    gen_paragraphs: int,
    tgt_paragraphs: int,
) -> ClassifiedError | None:
    """对单个 diff 块分类。"""

    gen_text = block.gen_text
    tgt_text = block.target_text

    # difflib opcode 语义：
    #   'delete': gen 有多余内容（gen_text 有内容，target_text 为空）
    #   'insert': target 有多余内容（target_text 有内容，gen_text 为空）
    #   'replace': 双方不同（gen_text 和 target_text 都有内容）
    # AI 味检测关注 gen 中有但 target 中没有的内容 → 检查 'delete' 和 'replace'
    _gen_extra_ops = ("insert", "replace", "delete")

    # 1. 检查 AI 味句式（gen 侧多余内容中是否有 AI 味）
    for pattern, desc, severity_coeff in _AI_SMELL_PATTERNS:
        if block.op in _gen_extra_ops and gen_text:
            match = re.search(pattern, gen_text)
            if match:
                return ClassifiedError(
                    category=ErrorCategory.AI_SMELL_PATTERN,
                    diff_block=block,
                    evidence=match.group(0),
                    description=f"生成文本出现{desc}：「{match.group(0)[:40]}」",
                    severity=severity_coeff * (block.size / max(len(target), 1)),
                )

    # 2. 检查内容编造（生成侧有原文没有的专有名词）
    if block.op in _gen_extra_ops and gen_text:
        gen_only_entities = _extract_named_entities(gen_text) - tgt_entities
        if gen_only_entities and len(gen_text) > 10:
            sample = list(gen_only_entities)[:3]
            return ClassifiedError(
                category=ErrorCategory.CONTENT_FABRICATION,
                diff_block=block,
                evidence=gen_text[:80],
                description=f"生成文本编造了原文没有的内容（新增实体：{'、'.join(sample)}）",
                severity=0.85 * (block.size / max(len(target), 1)),
            )

    # 3. 检查内容遗漏（原文侧有生成没有的关键内容）
    # target 侧多出的内容（原文有而生成没有）→ 'insert' 和 'replace' 的 target_text
    if block.op in ("insert", "replace") and tgt_text:
        tgt_only_entities = _extract_named_entities(tgt_text) - gen_entities
        if tgt_only_entities and len(tgt_text) > 10:
            sample = list(tgt_only_entities)[:3]
            return ClassifiedError(
                category=ErrorCategory.CONTENT_OMISSION,
                diff_block=block,
                evidence=tgt_text[:80],
                description=f"生成文本遗漏了原文的关键内容（缺失实体：{'、'.join(sample)}）",
                severity=0.85 * (block.size / max(len(target), 1)),
            )

    # 4. 检查对白差异
    gen_quotes = _count_quoted_segments(gen_text)
    tgt_quotes = _count_quoted_segments(tgt_text)
    # 原文有对白但生成没有 → 'insert'（target有对话，gen没有）/ 'replace'
    if block.op in ("insert", "replace") and tgt_quotes > 0 and gen_quotes == 0:
        return ClassifiedError(
            category=ErrorCategory.DIALOGUE_MISSING,
            diff_block=block,
            evidence=f"原文对白: {tgt_text[:60]}",
            description="原文有对白但生成文本变成了纯叙述",
            severity=0.75 * (block.size / max(len(target), 1)),
        )
    # 生成有对白但原文没有 → 'delete'（gen有对话，target没有）/ 'replace'
    if block.op in ("delete", "replace") and gen_quotes > 0 and tgt_quotes == 0:
        return ClassifiedError(
            category=ErrorCategory.DIALOGUE_EXTRA,
            diff_block=block,
            evidence=f"生成对白: {gen_text[:60]}",
            description="生成文本添加了原文没有的对白",
            severity=0.65 * (block.size / max(len(target), 1)),
        )

    # 5. 检查标点差异
    if block.op == "replace" and len(gen_text) < 20 and len(tgt_text) < 20:
        # 小块替换且主要是标点差异
        gen_punct_only = all(ch in _PUNCT_CHARS or ch.isspace() for ch in gen_text)
        tgt_punct_only = all(ch in _PUNCT_CHARS or ch.isspace() for ch in tgt_text)
        if gen_punct_only or tgt_punct_only:
            return ClassifiedError(
                category=ErrorCategory.PUNCTUATION_DEVIATION,
                diff_block=block,
                evidence=f"生成:{gen_text!r} → 目标:{tgt_text!r}",
                description="标点使用与原文不一致",
                severity=0.30 * (block.size / max(len(target), 1)),
            )

    # 6. 默认：措辞差异（语义相近但表述不同）
    if gen_text and tgt_text:
        return ClassifiedError(
            category=ErrorCategory.WORDING_DRIFT,
            diff_block=block,
            evidence=f"生成「{gen_text[:40]}」→ 原文「{tgt_text[:40]}」",
            description="措辞差异：内容方向对但具体表述不同",
            severity=0.20 * (block.size / max(len(target), 1)),
        )

    return None


def _add_global_errors(
    errors: list[ClassifiedError],
    generated: str,
    target: str,
    gen_has_dialogue: bool,
    tgt_has_dialogue: bool,
    gen_paragraphs: int,
    tgt_paragraphs: int,
) -> None:
    """添加全局级别错误（不绑定到单个 diff 块）。"""

    gen_len = len(generated)
    tgt_len = len(target)

    # 长度差异
    if tgt_len > 0:
        diff_ratio = (gen_len - tgt_len) / tgt_len
        if abs(diff_ratio) > 0.10:
            direction = "偏长" if diff_ratio > 0 else "偏短"
            errors.append(ClassifiedError(
                category=ErrorCategory.LENGTH_DEVIATION,
                diff_block=DiffBlock(
                    op="replace", gen_text="", target_text="",
                    gen_start=0, gen_end=gen_len,
                    target_start=0, target_end=tgt_len,
                ),
                evidence=f"生成 {gen_len} 字 vs 原文 {tgt_len} 字 ({diff_ratio:+.1%})",
                description=f"长度{direction}：生成 {gen_len} 字，原文 {tgt_len} 字",
                severity=min(abs(diff_ratio), 1.0),
            ))

    # 全局对白缺失
    if tgt_has_dialogue and not gen_has_dialogue:
        errors.append(ClassifiedError(
            category=ErrorCategory.DIALOGUE_MISSING,
            diff_block=DiffBlock(
                op="delete", gen_text="", target_text="",
                gen_start=0, gen_end=gen_len,
                target_start=0, target_end=tgt_len,
            ),
            evidence="原文包含对白但生成文本完全没有对白",
            description="全局对白缺失：原文有对白但生成文本全为纯叙述",
            severity=0.90,
        ))

    # 全局对白编造
    if gen_has_dialogue and not tgt_has_dialogue:
        errors.append(ClassifiedError(
            category=ErrorCategory.DIALOGUE_EXTRA,
            diff_block=DiffBlock(
                op="insert", gen_text="", target_text="",
                gen_start=0, gen_end=gen_len,
                target_start=0, target_end=tgt_len,
            ),
            evidence="原文无对白但生成文本添加了对白",
            description="全局对白编造：原文无对白但生成文本自行添加",
            severity=0.70,
        ))

    # 段落结构差异
    if tgt_paragraphs > 0:
        para_diff = abs(gen_paragraphs - tgt_paragraphs) / tgt_paragraphs
        if para_diff > 0.5 and tgt_paragraphs >= 2:
            errors.append(ClassifiedError(
                category=ErrorCategory.STRUCTURE_DEVIATION,
                diff_block=DiffBlock(
                    op="replace", gen_text="", target_text="",
                    gen_start=0, gen_end=gen_len,
                    target_start=0, target_end=tgt_len,
                ),
                evidence=f"生成 {gen_paragraphs} 段 vs 原文 {tgt_paragraphs} 段",
                description=f"段落结构差异显著：生成 {gen_paragraphs} 段，原文 {tgt_paragraphs} 段",
                severity=min(para_diff, 1.0) * 0.5,
            ))


# ---------------------------------------------------------------------------
# 根因分析器
# ---------------------------------------------------------------------------

# 错误类别 → Prompt 规则 映射表
_ERROR_TO_RULES: dict[ErrorCategory, dict[str, Any]] = {
    ErrorCategory.AI_SMELL_PATTERN: {
        "target_rules": ["AI味禁令", "判断句禁止", "陈述句式规范"],
        "action_type": "strengthen",
        "template": (
            "生成文本中检测到 AI 味句式：{evidence}。"
            "在 prompt 中强化 AI 味禁令——列出具体的禁止句式，"
            "每条附一个正确写法的简短示例。"
        ),
    },
    ErrorCategory.CONTENT_FABRICATION: {
        "target_rules": ["情节保真约束", "节拍覆盖要求"],
        "action_type": "add_or_strengthen",
        "template": (
            "生成文本编造了原文没有的内容：{evidence}。"
            "在 prompt 中增加「严格遵循用户提供的场景节拍，不得编造额外情节、角色行动或情报内容」的硬约束。"
        ),
    },
    ErrorCategory.CONTENT_OMISSION: {
        "target_rules": ["节拍覆盖要求", "情节保真约束"],
        "action_type": "add_or_strengthen",
        "template": (
            "生成文本遗漏了原文的关键内容：{evidence}。"
            "在 prompt 中增加「必须覆盖所有场景节拍，逐一检查，不得跳过任何一个节拍」的规则。"
        ),
    },
    ErrorCategory.DIALOGUE_MISSING: {
        "target_rules": ["对白引导", "叙事平衡"],
        "action_type": "strengthen",
        "template": (
            "原文有对白但生成文本缺少或变成纯叙述：{evidence}。"
            "在 prompt 中提高对白规则的优先级，增加「纯叙述无对话段落连续不超过 2 段」「每 300 字至少一段对话」"
            "等硬约束，并检查是否有过多叙事描写约束抑制了对白生成。"
        ),
    },
    ErrorCategory.DIALOGUE_EXTRA: {
        "target_rules": ["情节保真约束"],
        "action_type": "add",
        "template": (
            "生成文本添加了原文没有的对白：{evidence}。"
            "在 prompt 中增加「对白内容应与场景节拍一致，不编造对话主题或添加节拍外的交流内容」的约束。"
        ),
    },
    ErrorCategory.LENGTH_DEVIATION: {
        "target_rules": ["篇幅控制", "详略程度"],
        "action_type": "add_or_adjust",
        "template": (
            "生成文本长度与原文偏差较大：{evidence}。"
            "{length_direction}"
        ),
    },
    ErrorCategory.STRUCTURE_DEVIATION: {
        "target_rules": ["段落结构", "叙事节奏"],
        "action_type": "fine_tune",
        "template": (
            "生成文本的段落结构与原文差异显著：{evidence}。"
            "在 prompt 中微调段落结构引导，使生成的段落划分更接近原文的节奏。"
        ),
    },
    ErrorCategory.PUNCTUATION_DEVIATION: {
        "target_rules": ["标点规范"],
        "action_type": "strengthen",
        "template": (
            "生成文本的标点使用与原文不一致：{evidence}。"
            "在 prompt 中强化标点使用规范，特别注意禁用破折号「——」和标点节奏。"
        ),
    },
    ErrorCategory.WORDING_DRIFT: {
        "target_rules": ["句式方向", "措辞习惯"],
        "action_type": "fine_tune",
        "template": (
            "措辞差异：{evidence}。"
            "微调 prompt 中的句式引导方向，使表述更接近原文风格。不要大改核心规则。"
        ),
    },
}


def _get_length_direction(error: ClassifiedError) -> str:
    """根据长度偏差方向生成具体的修改指令。"""
    evidence = error.evidence
    if "偏长" in error.description:
        return (
            f"生成偏长（{evidence}）。"
            "在 prompt 中增加精简要求——减少冗余修饰、提高信息密度、每句推进情节。"
            "同时可适当降低 max_tokens 参数。"
        )
    else:
        return (
            f"生成偏短（{evidence}）。"
            "在 prompt 中增加细节描写要求——适当展开动作描写、环境渲染或心理活动。"
            "同时可适当提高 max_tokens 参数。"
        )


def analyze_root_cause(errors: list[ClassifiedError]) -> list[ModificationAction]:
    """根因分析：将错误映射为具体的 prompt 修改动作。

    Args:
        errors: classify_errors() 的输出

    Returns:
        ModificationAction 列表，按优先级排序
    """
    # 按类别去重，保留每个类别中 severity 最高的
    best_per_category: dict[ErrorCategory, ClassifiedError] = {}
    for e in errors:
        if e.category not in best_per_category or e.severity > best_per_category[e.category].severity:
            best_per_category[e.category] = e

    actions: list[ModificationAction] = []
    for category, error in best_per_category.items():
        rule_info = _ERROR_TO_RULES.get(category)
        if not rule_info:
            continue

        # 填充模板
        instruction = rule_info["template"].format(
            evidence=error.evidence[:100],
            length_direction=_get_length_direction(error) if category == ErrorCategory.LENGTH_DEVIATION else "",
        )

        # 计算优先级：基于 severity 和类别权重
        category_weights = {
            ErrorCategory.CONTENT_FABRICATION: 1.0,
            ErrorCategory.CONTENT_OMISSION: 1.0,
            ErrorCategory.AI_SMELL_PATTERN: 0.8,
            ErrorCategory.DIALOGUE_MISSING: 0.7,
            ErrorCategory.DIALOGUE_EXTRA: 0.6,
            ErrorCategory.LENGTH_DEVIATION: 0.5,
            ErrorCategory.STRUCTURE_DEVIATION: 0.4,
            ErrorCategory.PUNCTUATION_DEVIATION: 0.3,
            ErrorCategory.WORDING_DRIFT: 0.2,
        }
        weight = category_weights.get(category, 0.1)
        priority_score = weight * error.severity

        # P0: priority > 0.5, P1: 0.2-0.5, P2: < 0.2
        if priority_score > 0.5:
            priority = 0
        elif priority_score > 0.2:
            priority = 1
        else:
            priority = 2

        actions.append(ModificationAction(
            priority=priority,
            error_category=category,
            target_rules=list(rule_info["target_rules"]),
            action_type=rule_info["action_type"],
            instruction=instruction,
            example_fix="",
        ))

    # 按优先级排序（P0 在前）
    actions.sort(key=lambda a: (a.priority, -category_weights.get(a.error_category, 0)))
    return actions


# ---------------------------------------------------------------------------
# 完整分析流水线
# ---------------------------------------------------------------------------

def analyze(
    generated: str,
    target: str,
    similarity: float,
    length_diff: float,
    normalize_fn=None,
) -> ErrorReport:
    """执行完整的 diff → 分类 → 根因分析流水线。

    Args:
        generated: 生成文本（原始，含空白）
        target: 目标文本（原始，含空白）
        similarity: 字符级相似度 (0-1)
        length_diff: 长度差异比例
        normalize_fn: 可选的规范化函数（用于忽略空白/标点差异的 diff）

    Returns:
        完整的 ErrorReport
    """
    # Step 1: Diff
    if normalize_fn:
        blocks = compute_diff_normalized(generated, target, normalize_fn)
    else:
        blocks = compute_diff(generated, target)

    # Step 2: 错误分类
    errors = classify_errors(blocks, generated, target)

    # Step 3: 根因分析
    modifications = analyze_root_cause(errors)

    # Step 4: 生成摘要
    error_summary_parts = []
    for cat in ErrorCategory:
        count = sum(1 for e in errors if e.category == cat)
        if count > 0:
            error_summary_parts.append(f"{cat.value}: {count}")
    summary = "; ".join(error_summary_parts) if error_summary_parts else "无明显差异"

    return ErrorReport(
        similarity=similarity,
        length_diff=length_diff,
        gen_len=len(generated),
        target_len=len(target),
        diff_blocks=blocks,
        errors=errors,
        modifications=modifications,
        summary=summary,
    )


# ---------------------------------------------------------------------------
# 辅助：将 Modifications 转为自然语言指令列表
# ---------------------------------------------------------------------------

def format_modifications_for_prompt(
    modifications: list[ModificationAction],
    max_per_priority: dict[int, int] | None = None,
) -> str:
    """将修改动作列表格式化为可供 LLM 模板使用的自然语言指令。

    Args:
        modifications: analyze_root_cause() 的输出
        max_per_priority: 每个优先级最多保留几条，默认 {0: 3, 1: 2, 2: 1}

    Returns:
        格式化的 Markdown 指令文本
    """
    if max_per_priority is None:
        max_per_priority = {0: 3, 1: 2, 2: 1}

    priority_labels = {0: "🔴 P0 - 必须修复", 1: "🟡 P1 - 应该修复", 2: "🟢 P2 - 可以优化"}
    action_labels = {
        "add": "新增规则",
        "strengthen": "强化现有规则",
        "add_or_strengthen": "新增或强化规则",
        "remove": "移除规则",
        "fine_tune": "微调规则",
        "replace": "替换规则",
    }

    lines: list[str] = []
    counts: dict[int, int] = {0: 0, 1: 0, 2: 0}

    for mod in modifications:
        p = mod.priority
        max_n = max_per_priority.get(p, 1)
        if counts[p] >= max_n:
            continue
        counts[p] += 1

        if counts[p] == 1:
            # 首次出现此优先级，添加标题
            lines.append(f"\n### {priority_labels[p]}")
        else:
            lines.append("")

        action_cn = action_labels.get(mod.action_type, mod.action_type)
        rules_str = "、".join(mod.target_rules)
        lines.append(f"**{action_cn}** → 涉及规则：{rules_str}")
        lines.append(f"> {mod.instruction}")

    if not lines:
        lines.append("（本轮无需修改，当前 prompt 表现良好）")

    return "\n".join(lines).strip()
