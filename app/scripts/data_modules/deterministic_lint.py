#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Deterministic Lint - 确定性正则 lint（来自用户管线工具的 compliance_checker.py 精简改造）

只保留**通用机械违规**检测，删除旧书《殡仪馆》专属人名/规则。
作为 reviewer / quantitative_audit / critic-agent 之外的兜底保险——任何 hard
violation 都必须由 polish 修复。

设计原则：
- 0.1 秒级，纯正则
- 只检测**所有书都不应出现**的机械错误（标点格式、句式禁忌、感官倒置等）
- 不依赖角色名、不依赖具体设定
- severity 全部标 `hard`

CLI:
    python -m data_modules.deterministic_lint --project-root <dir> --chapter <N>
    python -m data_modules.deterministic_lint --project-root <dir> --content-file <path>
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import List, Optional


# ===== 通用机械违规规则集 =====

# R1：禁用破折号（书面化标志）
PATTERN_DASH = re.compile(r"[—]{1,}|--+")

# R2：禁用"不是X是Y"句型及变体
PATTERNS_NOT_IS = [
    re.compile(r"不是[^。，；！？\n]{1,30}是"),
    re.compile(r"并非[^。，；！？\n]{1,30}是"),
    re.compile(r"不在于[^。，；！？\n]{1,30}在于"),
    re.compile(r"不是[^。，；！？\n]{1,20}而是"),
]

# R6：英文直引号 / 直角引号（应改为中文双引号）
PATTERN_STRAIGHT_QUOTE = re.compile(r'"')
PATTERN_CORNER_QUOTE = re.compile(r"[「」]")

# R6.2：叙述内容用全角括号包裹（白名单：系统弹窗内的【...】不算）
PATTERN_FULL_PAREN = re.compile(r"（[^）\n]{2,40}）")

# R16：禁用"第一反应是X，但是Y"句式
PATTERN_FIRST_REACTION = re.compile(r"第一反应是[^。\n]{1,30}(但是|可是|然而)")

# R18：身体部位+感觉词+,+像（感觉后接比喻）
PATTERN_SENSE_METAPHOR = re.compile(
    r"(指尖|关节|脖颈|手指|皮肤|肤色|嘴唇|喉咙|后脑勺|太阳穴|肩膀|脖子|腰|腿)"
    r"[^。\n]{1,15}(凉|冷|麻|疼|僵|涩|沉|烫|抖|颤)[^。\n]{0,3}[，,]\s*像"
)

# R19：内耗/过渡填充
PATTERNS_INNER_DRAG = [
    re.compile(r"(想了想|犹豫了一下|不知道该不该|要不要)"),
    re.compile(r"(过了大概[一-龥]+秒|此时|过了一会儿|过了片刻)"),
]

# R24：感官因果倒置（睁开眼/抬头 后立即接 发现/看到）
PATTERNS_SENSE_REVERSE = [
    re.compile(r"睁开眼[，,][^。\n]{0,15}(发现|看到|看见|入目)"),
    re.compile(r"睁眼[，,][^。\n]{0,15}(发现|看到|看见|入目)"),
    re.compile(r"抬头[，,][^。\n]{0,15}(发现|看到|看见)"),
]

# 过滤词（通用，不依赖具体角色）
FILTER_WORDS = [
    "注意到", "意识到", "感觉到",
    "这意味着", "这说明",
]


@dataclass
class Violation:
    rule_id: str
    rule_name: str
    severity: str  # always 'hard'
    line_no: int
    line_content: str
    matched_text: str
    suggestion: str

    def to_dict(self) -> dict:
        return asdict(self)


class DeterministicLint:
    """通用机械违规扫描器 - 旧书无关"""

    def __init__(self):
        self.violations: List[Violation] = []
        self.lines: List[str] = []

    def check(self, text: str) -> List[Violation]:
        self.violations = []
        self.lines = text.split("\n")

        for idx, line in enumerate(self.lines, 1):
            if not line.strip():
                continue
            self._check_dash(idx, line)
            self._check_not_is(idx, line)
            self._check_quotes(idx, line)
            self._check_full_paren(idx, line)
            self._check_first_reaction(idx, line)
            self._check_sense_metaphor(idx, line)
            self._check_inner_drag(idx, line)
            self._check_sense_reverse(idx, line)
            self._check_filter_words(idx, line)

        return self.violations

    # ----- 各规则实现 -----

    def _add(self, rule_id: str, rule_name: str, line_no: int, line: str, matched: str, suggestion: str):
        self.violations.append(Violation(
            rule_id=rule_id,
            rule_name=rule_name,
            severity="hard",
            line_no=line_no,
            line_content=line[:200],
            matched_text=matched[:80],
            suggestion=suggestion,
        ))

    def _check_dash(self, line_no: int, line: str):
        for m in PATTERN_DASH.finditer(line):
            self._add(
                "R1", "禁用破折号", line_no, line, m.group(0),
                "破折号过于书面化。改用句号或换行实现停顿；表示插入说明改用逗号。",
            )

    def _check_not_is(self, line_no: int, line: str):
        for pat in PATTERNS_NOT_IS:
            for m in pat.finditer(line):
                self._add(
                    "R2", "禁用'不是X是Y'句型", line_no, line, m.group(0),
                    "这种二元对照式句型 AI 味重。改为正面陈述事实+具体细节，让对比从读者推断中浮现。",
                )

    def _check_quotes(self, line_no: int, line: str):
        if PATTERN_STRAIGHT_QUOTE.search(line):
            self._add(
                "R6-A", "英文直引号", line_no, line, '"',
                '英文直引号 " 应改为中文双引号 "" 。auto_fix 会自动修。',
            )
        if PATTERN_CORNER_QUOTE.search(line):
            self._add(
                "R6-B", "直角引号", line_no, line, "「或」",
                "直角引号「」是日式排版习惯，中文网文统一用 "" 。auto_fix 会自动修。",
            )

    def _check_full_paren(self, line_no: int, line: str):
        # 跳过【】系统弹窗
        if "【" in line and "】" in line:
            return
        for m in PATTERN_FULL_PAREN.finditer(line):
            self._add(
                "R6-C", "全角括号包裹叙述", line_no, line, m.group(0),
                "叙述中尽量不用（）插入说明。改成独立短句、或融入正文。",
            )

    def _check_first_reaction(self, line_no: int, line: str):
        for m in PATTERN_FIRST_REACTION.finditer(line):
            self._add(
                "R16", "'第一反应是X但是Y'句式", line_no, line, m.group(0),
                "这种句式过于书面。改为直接动作+反应，让'但是'转折通过情节自身呈现。",
            )

    def _check_sense_metaphor(self, line_no: int, line: str):
        for m in PATTERN_SENSE_METAPHOR.finditer(line):
            self._add(
                "R18", "感觉后接比喻", line_no, line, m.group(0),
                "身体部位+感觉词后立即接'像'是 AI 模板。删比喻或改为具体动作触发的反应。",
            )

    def _check_inner_drag(self, line_no: int, line: str):
        for pat in PATTERNS_INNER_DRAG:
            for m in pat.finditer(line):
                self._add(
                    "R19", "内耗/过渡填充", line_no, line, m.group(0),
                    "'想了想/犹豫/过了一会儿'是节奏拖沓信号。改为具体动作或直接进入下一拍。",
                )

    def _check_sense_reverse(self, line_no: int, line: str):
        for pat in PATTERNS_SENSE_REVERSE:
            for m in pat.finditer(line):
                self._add(
                    "R24", "感官因果倒置", line_no, line, m.group(0),
                    "'睁眼，发现/看到 XX' 把感官接入和因果颠倒。改为：先描写视觉信息，再用动作（如抬头/转身）自然承接。",
                )

    def _check_filter_words(self, line_no: int, line: str):
        for word in FILTER_WORDS:
            if word in line:
                self._add(
                    "R4", f"过滤词「{word}」", line_no, line, word,
                    f"'{word}' 是观察距离词，让读者跟主角隔了一层。删除或改为直接呈现感官事实。",
                )


# ===== 章节读取（与 quantitative_audit 共用） =====

def _resolve_content(project_root: Path, chapter: Optional[int], content_file: Optional[Path]) -> tuple[str, int]:
    if content_file:
        return content_file.read_text(encoding="utf-8"), chapter or 0
    if chapter is not None:
        text_dir = project_root / "正文"
        if not text_dir.is_dir():
            raise FileNotFoundError(f"正文目录不存在: {text_dir}")
        for pattern in (f"第{chapter:04d}章*.md", f"第{chapter}章*.md"):
            candidates = sorted(text_dir.glob(pattern))
            if candidates:
                return candidates[0].read_text(encoding="utf-8"), chapter
        raise FileNotFoundError(f"找不到第 {chapter} 章正文")
    raise ValueError("必须指定 --chapter 或 --content-file")


# ===== CLI =====

def main():
    parser = argparse.ArgumentParser(description="确定性正则 lint - 通用机械违规扫描")
    parser.add_argument("--project-root", type=str, required=True)
    parser.add_argument("--chapter", type=int, help="章节号")
    parser.add_argument("--content-file", type=str, help="正文文件路径")
    parser.add_argument("--persist", action="store_true", help="持久化到 .ainovel/tmp/lint_audit.json")

    args = parser.parse_args()
    project_root = Path(args.project_root).expanduser().resolve()
    content_file = Path(args.content_file).expanduser().resolve() if args.content_file else None

    started_at = time.perf_counter()
    content, chapter = _resolve_content(project_root, args.chapter, content_file)
    linter = DeterministicLint()
    violations = linter.check(content)
    elapsed_ms = int((time.perf_counter() - started_at) * 1000)

    output = {
        "chapter": chapter,
        "total_chars": len(content),
        "total_violations": len(violations),
        "violations": [v.to_dict() for v in violations],
        "elapsed_ms": elapsed_ms,
    }

    if args.persist:
        tmp_dir = project_root / ".ainovel" / "tmp"
        tmp_dir.mkdir(parents=True, exist_ok=True)
        out_path = tmp_dir / "lint_audit.json"
        out_path.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"[deterministic_lint] persisted to {out_path}", file=sys.stderr)

    print(json.dumps(output, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
