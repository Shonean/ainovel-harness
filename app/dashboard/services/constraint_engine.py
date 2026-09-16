"""
约束校验引擎（Phase 2）

按 JSON 规则注册表加载约束，按"自由度级别"过滤后对文本执行三类检查：
  - regex:  已预编译的 pattern
  - wrapped: 委托给 scripts.data_modules.deterministic_lint（不内联，避免重复）
  - programmatic: 由本模块的 _check_* 方法实现（H003/H004/H005/A001/A002/A003）

约束规则文件: references/shared/constraint-rules.json
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import List, Optional

from ..core.constants import DEFAULT_FREEDOM_LEVEL, FreedomLevel

logger = logging.getLogger(__name__)

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_RULES_PATH = PLUGIN_ROOT / "references" / "shared" / "constraint-rules.json"

# wrapped 规则的实际实现由 DeterministicLint 提供。
# 避免在 dashboard 重新实现机械违规扫描，保持单一真相源。
try:
    from scripts.data_modules.deterministic_lint import DeterministicLint
    from scripts.data_modules.deterministic_lint import Violation as DlintViolation
except Exception:  # noqa: BLE001 — 打包/独立运行场景下允许缺失
    DeterministicLint = None  # type: ignore[assignment]
    DlintViolation = None  # type: ignore[assignment]


# ---------------------------------------------------------------------------
# Programmatic 检查所用的模块级正则（仅 H/A 规则需要，与 deterministic_lint 互补）
# ---------------------------------------------------------------------------

# A002: 情绪标签化（他/她/它 + 心理/认知动词）
_EMOTION_LABEL_PATTERN = re.compile(
    r"(他|她|它)(心中的|眼里|意识到|感触到|感觉到|认识到|觉得|感到|明白)[^。\n]{1,40}"
)

# H003: 模糊实体（一身衣服/某个路人/一件东西/一把伞/有人X）
_VAGUE_ENTITY_PATTERN = re.compile(
    r"(一个穿着[^。\n]{1,10}[男女]|某个[路人个家]|一件[^。\n]{1,10}[包裹]|"
    r"一把[伞剑]|有[一个人].{1,5}(的|了|说道|站|走))"
)

# A003: 章末"安全着陆"套式（沉默/一片/圆满/夜色已定/晚风）
SAFE_LANDING_PATTERNS = [
    re.compile(r"沉默[^。]{1,20}[。、]"),
    re.compile(r"一片[^。]{1,30}[。、]"),
    re.compile(r"圆满[^。]{0,10}[。]"),
    re.compile(r"夜色已定[。]"),
    re.compile(r"晚风[^。]{1,20}[。、]"),
]

# A001: 用于剥除标点和空白以做 fingerprint
_PUNCT_WS_RE = re.compile(r"[，。！？；：、\s]")


# ---------------------------------------------------------------------------
# 数据类
# ---------------------------------------------------------------------------

@dataclass
class Rule:
    """约束规则定义。"""
    rule_id: str
    name: str
    category: str
    severity: str
    freedom_level_gate: List[str]
    check_type: str  # programmatic | regex | wrapped | llm
    description: str
    pattern: Optional[str] = None

    @classmethod
    def from_dict(cls, d: dict) -> "Rule":
        return cls(
            rule_id=d.get("id") or d.get("rule_id", ""),
            name=d["name"],
            category=d["category"],
            severity=d["severity"],
            freedom_level_gate=list(d.get("freedom_level_gate", []) or []),
            check_type=d.get("check_type", "programmatic"),
            description=d.get("description", ""),
            pattern=d.get("pattern"),
        )


@dataclass
class Violation:
    """单条违规记录。"""
    rule_id: str
    rule_name: str
    severity: str
    category: str
    location: str  # "行N" / "段N-M" / "章末" / "全局"
    evidence: str
    fix_hint: str

    def to_dict(self) -> dict:
        return asdict(self)


# ---------------------------------------------------------------------------
# 引擎
# ---------------------------------------------------------------------------

class ConstraintEngine:
    """正则+注解式约束引擎。

    使用:
        engine = ConstraintEngine()                         # 默认从 constraint-rules.json 加载
        violations = engine.validate(text, freedom_level=...)  # 校验
        summary = engine.get_summary(violations)            # 统计
    """

    DEFAULT_FREEDOM_LEVEL = DEFAULT_FREEDOM_LEVEL

    def __init__(self, rules_path: Optional[Path] = None):
        self.rules: List[Rule] = []
        self._compiled_patterns: dict[str, "re.Pattern[str]"] = {}
        self._load_rules(Path(rules_path) if rules_path else DEFAULT_RULES_PATH)

    # ----- 规则加载 -----

    def _load_rules(self, path: Path) -> None:
        """从 JSON 文件加载规则并预编译 regex。"""
        if not path.is_file():
            raise FileNotFoundError(f"约束规则文件不存在: {path}")
        raw = json.loads(path.read_text(encoding="utf-8"))
        rule_dicts = raw.get("rules", [])
        for d in rule_dicts:
            try:
                rule = Rule.from_dict(d)
            except Exception as e:  # noqa: BLE001
                logger.warning("dashboard.constraint_engine: 规则解析失败 %s: %s", d.get("id"), e)
                continue
            self.rules.append(rule)
            if rule.check_type == "regex" and rule.pattern:
                try:
                    self._compiled_patterns[rule.rule_id] = re.compile(rule.pattern)
                except re.error as e:
                    logger.warning(
                        "dashboard.constraint_engine: 规则 %s pattern 编译失败: %s",
                        rule.rule_id, e,
                    )

    # ----- 查询 -----

    def get_active_rules(self, freedom_level) -> List[Rule]:
        """返回在给定自由度级别下激活的规则列表。"""
        level_str = (
            freedom_level.value if hasattr(freedom_level, "value") else str(freedom_level)
        )
        return [r for r in self.rules if level_str in (r.freedom_level_gate or [])]

    def get_rules_by_category(self, category: str) -> List[Rule]:
        """按分类获取规则。"""
        return [r for r in self.rules if r.category == category]

    # ----- 校验主流程 -----

    def validate(
        self,
        text: str,
        chapter_data: Optional[dict] = None,
        freedom_level=None,
    ) -> List[Violation]:
        """对文本执行全部活跃规则校验，返回违规列表（按 location 升序）。"""
        if freedom_level is None:
            freedom_level = DEFAULT_FREEDOM_LEVEL

        active_rules = self.get_active_rules(freedom_level)
        violations: List[Violation] = []

        # 1) wrapped → DeterministicLint
        wrapped_rules = [r for r in active_rules if r.check_type == "wrapped"]
        if wrapped_rules and DeterministicLint is not None:
            wrapped_ids = {r.rule_id for r in wrapped_rules}
            dlint_violations = DeterministicLint().check(text)
            for v in dlint_violations:
                if v.rule_id in wrapped_ids:
                    violations.append(Violation(
                        rule_id=v.rule_id,
                        rule_name=v.rule_name,
                        severity=v.severity,
                        category="hard",
                        location=f"行{v.line_no}",
                        evidence=v.matched_text,
                        fix_hint=v.suggestion,
                    ))

        # 2) regex
        lines = text.split("\n")
        for rule in active_rules:
            if rule.check_type != "regex":
                continue
            pat = self._compiled_patterns.get(rule.rule_id)
            if pat is None:
                continue
            for idx, line in enumerate(lines, 1):
                if not line.strip():
                    continue
                m = pat.search(line)
                if m:
                    violations.append(Violation(
                        rule_id=rule.rule_id,
                        rule_name=rule.name,
                        severity=rule.severity,
                        category=rule.category,
                        location=f"行{idx}",
                        evidence=m.group(0),
                        fix_hint=rule.description,
                    ))

        # 3) programmatic
        for rule in active_rules:
            if rule.check_type != "programmatic":
                continue
            violations.extend(self._run_programmatic_check(rule, text, lines, chapter_data))

        violations.sort(key=lambda v: _extract_line_number(v.location))
        return violations

    def get_summary(self, violations: List[Violation]) -> dict:
        """汇总违规为统计摘要。返回字段：passed, total, hard_count, by_severity, by_category。"""
        by_severity: dict[str, int] = {}
        by_category: dict[str, int] = {}
        hard_count = 0
        for v in violations:
            by_severity[v.severity] = by_severity.get(v.severity, 0) + 1
            by_category[v.category] = by_category.get(v.category, 0) + 1
            if v.severity == "hard":
                hard_count += 1
        return {
            "passed": hard_count == 0,
            "total": len(violations),
            "hard_count": hard_count,
            "by_severity": by_severity,
            "by_category": by_category,
        }

    # ----- Programmatic 调度 -----

    def _run_programmatic_check(
        self, rule: Rule, text: str, lines: List[str], chapter_data: Optional[dict]
    ) -> List[Violation]:
        rid = rule.rule_id
        if rid == "H003":
            return self._check_vague_entities(text, rule, lines)
        if rid == "H004":
            return self._check_placeholder(text, rule, lines)
        if rid == "H005":
            return self._check_progression(text, rule, lines)
        if rid == "A001":
            return self._check_consecutive_similar_sentences(text, rule, lines)
        if rid == "A002":
            return self._check_emotion_labels(text, rule, lines)
        if rid == "A003":
            return self._check_safe_landing(text, rule, lines)
        # H001/H002/H006/S001/S002 等暂未提供程序化实现，留待未来扩展
        return []

    # ----- Programmatic 各检查实现 -----

    def _check_placeholder(self, text: str, rule: Rule, lines: List[str]) -> List[Violation]:
        """H004: 禁止占位省略号（单独一行只含占位符时再补一次——regex 已覆盖内联情况）。"""
        PLACEHOLDERS = ("...", "。。。", "……")
        violations: List[Violation] = []
        for idx, line in enumerate(lines, 1):
            stripped = line.strip()
            if stripped in PLACEHOLDERS:
                violations.append(Violation(
                    rule_id=rule.rule_id,
                    rule_name=rule.name,
                    severity=rule.severity,
                    category=rule.category,
                    location=f"行{idx}",
                    evidence=stripped,
                    fix_hint="删除占位省略号，写全本意。",
                ))
        return violations

    def _check_progression(self, text: str, rule: Rule, lines: List[str]) -> List[Violation]:
        """H005: 当文本去空后 < 500 字符时告警。"""
        chars = len(text.replace("\n", "").replace(" ", ""))
        if chars < 500:
            return [Violation(
                rule_id=rule.rule_id,
                rule_name=rule.name,
                severity=rule.severity,
                category=rule.category,
                location="全局",
                evidence=f"全字 {chars} 字符",
                fix_hint="总字数偏少，请检查是否缺推进点/目标/变化。",
            )]
        return []

    def _check_consecutive_similar_sentences(
        self, text: str, rule: Rule, lines: List[str]
    ) -> List[Violation]:
        """A001: 连续 3 段以相同 fingerprint（去标点/空白后前 10 字）开头即告警。"""
        violations: List[Violation] = []
        paragraphs = [p for p in text.split("\n\n") if p.strip()]
        fingerprints: List[str] = []
        for para in paragraphs:
            clean = _PUNCT_WS_RE.sub("", para)
            fingerprints.append(clean[:10])

        for i in range(len(fingerprints) - 2):
            f0, f1, f2 = fingerprints[i], fingerprints[i + 1], fingerprints[i + 2]
            if f0 and f0 == f1 == f2:
                violations.append(Violation(
                    rule_id=rule.rule_id,
                    rule_name=rule.name,
                    severity=rule.severity,
                    category=rule.category,
                    location=f"段{i + 1}-{i + 3}",
                    evidence="连续 3 段以相同句式起头",
                    fix_hint="变换句式结构，或避免使用相同起头模式。",
                ))
        return violations

    def _check_emotion_labels(self, text: str, rule: Rule, lines: List[str]) -> List[Violation]:
        """A002: 心理/认知动词标签化（他/她/它 + 意识到/感到/觉得...）。"""
        violations: List[Violation] = []
        for idx, line in enumerate(lines, 1):
            m = _EMOTION_LABEL_PATTERN.search(line)
            if m:
                violations.append(Violation(
                    rule_id=rule.rule_id,
                    rule_name=rule.name,
                    severity=rule.severity,
                    category=rule.category,
                    location=f"行{idx}",
                    evidence=m.group(0),
                    fix_hint="用行为/动作暗示情绪，避免直接贴标签（如'攥紧拳头'而非'心中有怒'）。",
                ))
        return violations

    def _check_safe_landing(self, text: str, rule: Rule, lines: List[str]) -> List[Violation]:
        """A003: 章末若落入'安全着陆'套式（沉默/一片/圆满/夜色已定/晚风）即告警。"""
        paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
        if not paragraphs:
            return []
        last_para = paragraphs[-1]
        for pat in SAFE_LANDING_PATTERNS:
            if pat.search(last_para):
                return [Violation(
                    rule_id=rule.rule_id,
                    rule_name=rule.name,
                    severity=rule.severity,
                    category=rule.category,
                    location="章末",
                    evidence="章末落入'安全着陆'套式",
                    fix_hint="章末以悬念/新信息/情绪断层收束，避免落入'沉默/夜色/晚风'式套式着陆。",
                )]
        return []

    def _check_vague_entities(self, text: str, rule: Rule, lines: List[str]) -> List[Violation]:
        """H003: 模糊实体词（一个穿着...男女/某个路人/一件东西/一把伞/有人X）。"""
        violations: List[Violation] = []
        for idx, line in enumerate(lines, 1):
            m = _VAGUE_ENTITY_PATTERN.search(line)
            if m:
                violations.append(Violation(
                    rule_id=rule.rule_id,
                    rule_name=rule.name,
                    severity=rule.severity,
                    category=rule.category,
                    location=f"行{idx}",
                    evidence=m.group(0),
                    fix_hint="为模糊实体提供明确名称或具体描写（data-agent 会自动提取）。",
                ))
        return violations


# ---------------------------------------------------------------------------
# 工具
# ---------------------------------------------------------------------------

def _extract_line_number(location: str) -> int:
    """从 '行N' / '段N-M' / '段N' 提取首位数字用于排序。无数字时返回 0。"""
    if not location:
        return 0
    m = re.search(r"(\d+)", location)
    return int(m.group(1)) if m else 0
