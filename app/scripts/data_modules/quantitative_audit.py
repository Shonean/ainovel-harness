#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Quantitative Audit - 量化风格审计模块（落地 ai-writing-quirks spec 第六层）

8 项 stylometric 指标 + 14 类词库密度扫描，输出结构化 JSON。

不依赖 LLM，0.1 秒一章，作为 reviewer 的兜底量化补充。

落地于：spec docs/superpowers/specs/2026-04-03-ai-writing-quirks.md 第六层

CLI:
    python -m data_modules.quantitative_audit --project-root <dir> --chapter <N>
    python -m data_modules.quantitative_audit --project-root <dir> --content-file <path>
"""

from __future__ import annotations

import argparse
import json
import re
import statistics
import sys
import time
from dataclasses import dataclass, asdict, field
from pathlib import Path
from typing import Any, Dict, List, Optional


# ===== 14 类词库（A-N，扩展 spec 中的 K/L/M/N 子类） =====

LEXICON: Dict[str, List[str]] = {
    "A_总结归纳": [
        "综合", "总之", "总而言之", "由此可见", "可以看出", "不难发现",
        "归根结底", "说到底", "总体来看", "从这个角度看", "换句话说",
        "简而言之", "概括来说", "可以说", "由此得出", "结论是",
        "最终可知", "总的来说", "总括起来", "整体而言", "总体上", "综上",
    ],
    "B_枚举模板": [
        "首先", "其次", "再次", "最后", "第一", "第二", "第三",
        "其一", "其二", "其三", "一方面", "另一方面", "再者", "此外",
        "另外", "同时", "接着", "然后", "随后", "紧接着",
        "最后一步", "下一步", "第一点", "第二点",
    ],
    "C_书面学术": [
        "某种程度上", "本质上", "意义上", "维度上", "层面上", "在于",
        "体现为", "构成了", "形成了", "实现了", "完成了", "进行了",
        "展开了", "推动了", "促进了", "提供了", "具备了", "拥有了",
        "达成了", "呈现出", "表现出", "反映出", "蕴含着", "折射出",
    ],
    "D_逻辑滥用": [
        "因此", "因而", "所以", "由于", "然而", "不过", "但是",
        "与此同时", "同样地", "对应地", "相应地", "进一步", "更进一步",
        "从而", "进而", "于是", "结果是", "于是乎", "故而", "由此",
        "相较之下", "反过来说",
    ],
    "E_情绪直述": [
        "非常愤怒", "非常开心", "非常难过", "心中五味杂陈", "百感交集",
        "情绪复杂", "内心震撼", "不由得感慨", "感到无奈", "感到痛苦",
        "感到欣慰", "感到恐惧", "深受触动", "心潮起伏", "心情沉重",
        "心情复杂", "心里一暖", "心里一沉", "心中一紧", "不禁一愣",
        "不由一怔", "内心一震",
    ],
    "F_动作套话": [
        "皱起眉头", "叹了口气", "深吸一口气", "缓缓开口", "沉声说道",
        "淡淡说道", "冷冷说道", "轻声说道", "嘴角上扬", "嘴角抽了抽",
        "眼神一凝", "目光一闪", "身形一滞", "脚步一顿", "浑身一震",
        "心头一跳", "不由后退半步", "猛地转身", "抬手一挥", "缓缓点头",
        "轻轻摇头", "下意识后退",
    ],
    "G_环境套话": [
        "空气仿佛凝固", "气氛骤然紧张", "气压陡然下降", "夜色如墨",
        "月色如水", "寒风刺骨", "四周一片寂静", "死一般的寂静",
        "时间仿佛静止", "空间仿佛扭曲", "房间里弥漫着", "唯一的光源",
        "摇摇欲坠", "压抑得让人喘不过气", "沉默像潮水", "空气中充满了",
        "一切都显得", "世界仿佛", "就在这一刻", "忽然之间",
        "刹那间", "顷刻之间",
    ],
    "H_叙事填充": [
        "事实上", "实际上", "某种意义上", "严格来说", "客观而言",
        "主观上", "一般来说", "通常情况下", "在这种情况下",
        "在这个时候", "在此基础上", "在这个意义上", "从某种角度",
        "对于他来说", "对她而言", "这意味着", "这说明", "这代表着",
        "这并不奇怪", "并非偶然", "不可否认", "毋庸置疑",
    ],
    "I_抽象空泛": [
        "命运", "成长", "蜕变", "升华", "价值", "意义", "抉择",
        "坚持", "信念", "初心", "希望", "绝望", "勇气", "正义",
        "邪恶", "真实", "虚伪", "复杂", "深刻", "宏大", "渺小", "沉重",
    ],
    "J_机械开收": [
        "故事要从", "让我们把视线", "镜头转到", "与此同时在另一边",
        "回到现在", "再说回", "这一切都要从", "他并不知道",
        "命运的齿轮开始转动", "新的篇章开始了", "未完待续",
        "故事才刚刚开始", "真正的考验还在后面", "一场风暴即将来临",
        "更大的阴谋正在酝酿", "这只是开始", "答案尚未揭晓",
        "未来会怎样", "谁也不知道", "他深知", "她明白",
        "可他不知道的是", "可她不知道的是", "然而一切才刚开始",
    ],
    "K_神态模板": [
        "眸中闪过", "眼底掠过", "瞳孔微缩", "嘴角微微上扬",
        "嘴角勾起一抹弧度", "眉头微蹙", "眉心微皱", "面色微变",
        "神色一凝", "目光微闪", "眼神微动", "唇角微翘",
        "脸色微沉", "面色如常", "不露声色",
    ],
    "L_万能副词": [
        "缓缓", "淡淡", "微微", "轻轻", "静静", "默默",
        "悄悄", "慢慢", "渐渐", "暗暗",
    ],
    "M_内心套话": [
        "心中暗道", "心中一凛", "心中暗自思量", "心下了然",
        "心中不由得", "心底涌起一股", "心中升起一丝", "暗自盘算",
        "暗自揣测", "心中有了计较",
    ],
    "N_网文转折": [
        "话虽如此", "不过", "只是", "然而就在这时",
        "正当", "就在此刻", "然而下一刻", "可惜的是", "殊不知",
    ],
}


# ===== Stylometric 阈值（来自 spec 第六层） =====

THRESHOLDS = {
    "sentence_len_stddev_min": 8.0,        # < 8 视为可疑（句长过均匀）
    "paragraph_len_stddev_min": 100.0,     # < 100 视为可疑
    "comma_period_ratio_max": 3.0,         # > 3.0 视为可疑（长句病）
    "dialogue_ratio_min": 0.25,            # < 25% 视为可疑（缺少对话推进）
    "adjective_density_max": 0.12,         # > 12% 视为可疑（jieba 不可用时降级关键词比）
    "adverb_verb_per_500": 3,              # 万能副词+动词每 500 字 ≥ 3 次视为可疑
    "four_char_density_max": 0.08,         # > 8% 视为可疑
    "hook_keyword_min": 1,                 # 末段 50 字含 [？！] 或 hook 触发词，否则 weak
}

HOOK_KEYWORDS = [
    "？", "！", "？？", "！！",
    "等等", "这是", "不可能", "天哪",
    "未完", "下一刻", "就在",
]

# 辅助：粗略形容词关键词集（jieba 缺位时用关键词比）
ADJECTIVE_HINTS = [
    "美", "丑", "高", "低", "深", "浅", "明", "暗", "冷", "热",
    "硬", "软", "粗", "细", "重", "轻", "急", "缓", "厚", "薄",
    "新", "旧", "好", "坏", "强", "弱", "干", "湿", "脏", "净",
]


# ===== 数据结构 =====

@dataclass
class Finding:
    """量化审计单条发现"""
    code: str           # 指标代号 / 词库代号
    severity: str       # info | low | medium | high
    metric: str         # 指标名（中文）
    value: Any          # 测量值
    threshold: Any      # 阈值
    description: str    # 人话
    evidence: List[str] = field(default_factory=list)  # 命中样本（最多 5 个）


@dataclass
class AuditReport:
    """单章量化审计报告"""
    chapter: int
    total_chars: int
    total_paragraphs: int
    total_sentences: int
    findings: List[Finding] = field(default_factory=list)
    metrics: Dict[str, Any] = field(default_factory=dict)
    elapsed_ms: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "chapter": self.chapter,
            "total_chars": self.total_chars,
            "total_paragraphs": self.total_paragraphs,
            "total_sentences": self.total_sentences,
            "findings": [asdict(f) for f in self.findings],
            "metrics": self.metrics,
            "elapsed_ms": self.elapsed_ms,
        }


# ===== 主分析器 =====

class QuantitativeAuditor:
    """量化风格审计 — 0.1 秒级正则/统计扫描"""

    SENTENCE_SPLITTER = re.compile(r"[。！？!?\n]+")
    PARAGRAPH_SPLITTER = re.compile(r"\n\s*\n")
    # 支持中文双引号 “”、英文直引号 ""、直角引号 「」
    DIALOGUE_PATTERN = re.compile(r"[\"“「][^\"”」]{1,200}[\"”」]")
    ADVERB_VERB_PATTERN = re.compile(
        r"(缓缓|淡淡|微微|轻轻|静静|默默|悄悄|慢慢|渐渐|暗暗)[一-龥]{1,2}"
    )
    FOUR_CHAR_PATTERN = re.compile(r"[一-龥]{4}(?![一-龥])")

    def audit(self, content: str, chapter: int = 0) -> AuditReport:
        started_at = time.perf_counter()
        text = content or ""
        total_chars = len(text)

        # 1. 切句切段
        sentences = [s.strip() for s in self.SENTENCE_SPLITTER.split(text) if s.strip()]
        paragraphs = [p.strip() for p in self.PARAGRAPH_SPLITTER.split(text) if p.strip()]
        report = AuditReport(
            chapter=chapter,
            total_chars=total_chars,
            total_paragraphs=len(paragraphs),
            total_sentences=len(sentences),
        )

        # 2. 8 项 stylometric 指标
        self._check_burstiness(text, sentences, paragraphs, report)
        self._check_punctuation_ratio(text, report)
        self._check_dialogue_ratio(text, report)
        self._check_adverb_verb_density(text, report)
        self._check_four_char_density(text, report)
        self._check_chapter_end_hook(text, report)
        self._check_adjective_density(text, report)

        # 3. 14 类词库密度扫描
        self._check_lexicon_densities(text, report)

        report.elapsed_ms = int((time.perf_counter() - started_at) * 1000)
        return report

    # ------- 指标实现 -------

    def _check_burstiness(self, text: str, sentences: List[str], paragraphs: List[str], report: AuditReport):
        # 句长 stddev
        sent_lens = [len(s) for s in sentences if len(s) > 0]
        if len(sent_lens) >= 3:
            stdv = statistics.stdev(sent_lens)
            report.metrics["sentence_len_stddev"] = round(stdv, 2)
            report.metrics["sentence_len_mean"] = round(statistics.mean(sent_lens), 2)
            if stdv < THRESHOLDS["sentence_len_stddev_min"]:
                report.findings.append(Finding(
                    code="STAT-01",
                    severity="medium",
                    metric="句长方差（burstiness）",
                    value=round(stdv, 2),
                    threshold=THRESHOLDS["sentence_len_stddev_min"],
                    description=f"全章 {len(sent_lens)} 句平均长度 {round(statistics.mean(sent_lens), 1)} 字，stddev {round(stdv, 1)} 偏低，句子长度过于均匀，是 AI 写作典型特征。建议穿插短句（≤8 字）和长句（≥35 字）。",
                ))

        # 段落长度 stddev
        para_lens = [len(p) for p in paragraphs if len(p) > 0]
        if len(para_lens) >= 3:
            stdv = statistics.stdev(para_lens)
            report.metrics["paragraph_len_stddev"] = round(stdv, 2)
            report.metrics["paragraph_len_mean"] = round(statistics.mean(para_lens), 2)
            if stdv < THRESHOLDS["paragraph_len_stddev_min"]:
                report.findings.append(Finding(
                    code="STAT-02",
                    severity="low",
                    metric="段落长度方差",
                    value=round(stdv, 2),
                    threshold=THRESHOLDS["paragraph_len_stddev_min"],
                    description=f"段落长度 stddev {round(stdv, 1)} 偏低，节奏单调。建议穿插超短段（一句话）和长段。",
                ))

    def _check_punctuation_ratio(self, text: str, report: AuditReport):
        comma = text.count("，") + text.count(",")
        period = text.count("。") + text.count(".")
        if period > 0:
            ratio = comma / period
            report.metrics["comma_period_ratio"] = round(ratio, 2)
            if ratio > THRESHOLDS["comma_period_ratio_max"]:
                report.findings.append(Finding(
                    code="STAT-03",
                    severity="medium",
                    metric="逗号/句号比",
                    value=round(ratio, 2),
                    threshold=THRESHOLDS["comma_period_ratio_max"],
                    description=f"逗号:句号 = {round(ratio, 2)}:1，长句过多。建议拆短或加入更多句号断点。",
                ))

    def _check_dialogue_ratio(self, text: str, report: AuditReport):
        dialogues = self.DIALOGUE_PATTERN.findall(text)
        dialogue_chars = sum(len(d) for d in dialogues)
        if len(text) > 0:
            ratio = dialogue_chars / len(text)
            report.metrics["dialogue_ratio"] = round(ratio, 3)
            if ratio < THRESHOLDS["dialogue_ratio_min"]:
                report.findings.append(Finding(
                    code="STAT-04",
                    severity="low",
                    metric="对话占比",
                    value=round(ratio, 3),
                    threshold=THRESHOLDS["dialogue_ratio_min"],
                    description=f"对话仅占 {round(ratio*100, 1)}%，叙述比例过高。网文典型对话占比 40-60%，灵异恐怖可低至 25%。建议加入对话推进。",
                ))

    def _check_adverb_verb_density(self, text: str, report: AuditReport):
        matches = self.ADVERB_VERB_PATTERN.findall(text)
        n = len(matches)
        per_500 = (n * 500.0 / max(len(text), 1)) if len(text) > 0 else 0
        report.metrics["adverb_verb_count"] = n
        report.metrics["adverb_verb_per_500"] = round(per_500, 2)
        if per_500 >= THRESHOLDS["adverb_verb_per_500"]:
            evidence: List[str] = []
            for m in self.ADVERB_VERB_PATTERN.finditer(text):
                start = max(0, m.start() - 8)
                end = min(len(text), m.end() + 8)
                evidence.append("..." + text[start:end] + "...")
                if len(evidence) >= 5:
                    break
            report.findings.append(Finding(
                code="STAT-05",
                severity="high",
                metric="万能副词+动词密度",
                value=round(per_500, 2),
                threshold=THRESHOLDS["adverb_verb_per_500"],
                description=f"全章共 {n} 处「缓缓/淡淡/微微/轻轻」等万能副词+动词，每 500 字 {round(per_500, 1)} 次。这是 AI 味最重的征兆之一。建议替换为具体动作。",
                evidence=evidence,
            ))

    def _check_four_char_density(self, text: str, report: AuditReport):
        matches = self.FOUR_CHAR_PATTERN.findall(text)
        # 简单估计四字短语比例：4*N / total_chars
        if len(text) > 0:
            ratio = (len(matches) * 4) / len(text)
            report.metrics["four_char_density"] = round(ratio, 3)
            if ratio > THRESHOLDS["four_char_density_max"]:
                report.findings.append(Finding(
                    code="STAT-06",
                    severity="low",
                    metric="四字词密度",
                    value=round(ratio, 3),
                    threshold=THRESHOLDS["four_char_density_max"],
                    description=f"四字短语占 {round(ratio*100, 1)}%（含成语+四字句），偏高。建议减少书面化堆砌。",
                ))

    def _check_chapter_end_hook(self, text: str, report: AuditReport):
        tail = text[-50:] if len(text) >= 50 else text
        hits = sum(1 for kw in HOOK_KEYWORDS if kw in tail)
        report.metrics["chapter_end_hook_hits"] = hits
        if hits < THRESHOLDS["hook_keyword_min"]:
            report.findings.append(Finding(
                code="STAT-07",
                severity="medium",
                metric="章末悬念强度",
                value=hits,
                threshold=THRESHOLDS["hook_keyword_min"],
                description=f"章末 50 字未发现钩子词（？！等等/这是/不可能...）。建议章末留 cliffhanger。",
            ))

    def _check_adjective_density(self, text: str, report: AuditReport):
        # 没有 jieba 时降级用关键词比近似
        hits = sum(text.count(adj) for adj in ADJECTIVE_HINTS)
        if len(text) > 0:
            ratio = hits / len(text)
            report.metrics["adjective_keyword_ratio"] = round(ratio, 3)
            if ratio > THRESHOLDS["adjective_density_max"]:
                report.findings.append(Finding(
                    code="STAT-08",
                    severity="low",
                    metric="形容词关键词密度",
                    value=round(ratio, 3),
                    threshold=THRESHOLDS["adjective_density_max"],
                    description=f"基础形容词关键词比 {round(ratio*100, 1)}% 偏高，词藻堆砌嫌疑。建议减少修饰、增加具体动作。",
                ))

    def _check_lexicon_densities(self, text: str, report: AuditReport):
        """14 类词库扫描"""
        category_summary: Dict[str, Dict[str, Any]] = {}

        for cat, words in LEXICON.items():
            total_hits = 0
            evidence: List[str] = []
            for w in words:
                count = text.count(w)
                total_hits += count
                if count > 0 and len(evidence) < 5:
                    evidence.append(f"{w}×{count}")

            category_summary[cat] = {
                "total_hits": total_hits,
                "per_1000_chars": round(total_hits * 1000 / max(len(text), 1), 2),
                "examples": evidence,
            }

            # severity 阈值
            per_1k = total_hits * 1000 / max(len(text), 1)
            if per_1k >= 8:
                severity = "high"
            elif per_1k >= 4:
                severity = "medium"
            elif per_1k >= 2:
                severity = "low"
            else:
                continue  # 不发 finding

            report.findings.append(Finding(
                code=f"LEX-{cat[:2]}",
                severity=severity,
                metric=f"词库密度：{cat[2:]}",
                value=round(per_1k, 2),
                threshold=2.0,
                description=f"{cat[2:]} 类词每千字出现 {round(per_1k, 1)} 次，建议替换或减少。",
                evidence=evidence,
            ))

        report.metrics["lexicon_summary"] = category_summary


# ===== 章节读取 =====

def _resolve_content(project_root: Path, chapter: Optional[int], content_file: Optional[Path]) -> tuple[str, int]:
    if content_file:
        return content_file.read_text(encoding="utf-8"), chapter or 0
    if chapter is not None:
        text_dir = project_root / "正文"
        if not text_dir.is_dir():
            raise FileNotFoundError(f"正文目录不存在: {text_dir}")
        # 模式：第NNNN章-*.md
        pattern = f"第{chapter:04d}章*.md"
        candidates = sorted(text_dir.glob(pattern))
        if not candidates:
            # 兼容 第N章 老命名
            pattern_old = f"第{chapter}章*.md"
            candidates = sorted(text_dir.glob(pattern_old))
        if not candidates:
            raise FileNotFoundError(f"找不到第 {chapter} 章正文：{text_dir}/{pattern}")
        return candidates[0].read_text(encoding="utf-8"), chapter
    raise ValueError("必须指定 --chapter 或 --content-file")


# ===== CLI =====

def main():
    parser = argparse.ArgumentParser(
        description="量化风格审计 - 8 项 stylometric 指标 + 14 类词库密度扫描",
    )
    parser.add_argument("--project-root", type=str, required=True, help="项目根目录")
    parser.add_argument("--chapter", type=int, help="章节号（从 正文/ 读取）")
    parser.add_argument("--content-file", type=str, help="直接指定正文文件路径")
    parser.add_argument("--output", type=str, help="输出 JSON 文件路径（默认输出到 stdout）")
    parser.add_argument("--persist", action="store_true", help="持久化到 .ainovel/tmp/quant_audit.json")

    args = parser.parse_args()
    project_root = Path(args.project_root).expanduser().resolve()
    content_file = Path(args.content_file).expanduser().resolve() if args.content_file else None

    content, chapter = _resolve_content(project_root, args.chapter, content_file)
    auditor = QuantitativeAuditor()
    report = auditor.audit(content, chapter=chapter)
    output = report.to_dict()

    if args.persist:
        tmp_dir = project_root / ".ainovel" / "tmp"
        tmp_dir.mkdir(parents=True, exist_ok=True)
        out_path = tmp_dir / "quant_audit.json"
        out_path.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"[quant_audit] persisted to {out_path}", file=sys.stderr)
        # 同步更新审查报告语义索引
        try:
            from .config import get_config as _get_config
            from .audit_embedding_index import AuditEmbeddingIndex
            cfg = _get_config(project_root)
            idx = AuditEmbeddingIndex(config=cfg)
            idx.add_report(output)
        except Exception:
            pass

    if args.output:
        Path(args.output).write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    else:
        print(json.dumps(output, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
