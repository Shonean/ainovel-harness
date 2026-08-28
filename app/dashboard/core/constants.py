"""
常量定义模块
"""
from enum import Enum
from typing import Optional

# ---------------------------------------------------------------------------
# 决策分级定义
# ---------------------------------------------------------------------------
class DecisionLevel(Enum):
    """
    决策级别枚举：
    L0：AI自动执行（仅允许无风险操作：格式排版、拼写检查、重复内容清理）
    L1：弹出确认框（所有修改操作：剧情调整、段落增删、人物变动）
    L2：3候选选择（所有生成操作：起草章节、润色版本、剧情分支）
    L3：用户自定义Prompt（所有创造性操作：新卷设定、新人物设计、核心剧情调整）
    """
    L0 = "L0"
    L1 = "L1"
    L2 = "L2"
    L3 = "L3"

# 决策级别描述
DECISION_LEVEL_DESCRIPTIONS = {
    DecisionLevel.L0: "AI自动执行（无风险操作）",
    DecisionLevel.L1: "需用户确认（修改操作）",
    DecisionLevel.L2: "3候选选择（生成操作）",
    DecisionLevel.L3: "用户自定义Prompt（创造性操作）",
}

# 操作类型对应的默认决策级别
OPERATION_DEFAULT_DECISION_LEVEL = {
    # 无风险操作 → L0
    "formatting": DecisionLevel.L0,
    "spell_check": DecisionLevel.L0,
    "duplicate_cleanup": DecisionLevel.L0,
    "log_query": DecisionLevel.L0,
    "cache_view": DecisionLevel.L0,
    # 写章流程内在步骤（审查/润色/提交）：用户点击"写本章全流程"已表达完整意图，无需中途确认
    "chapter_draft": DecisionLevel.L0,
    "chapter_polish": DecisionLevel.L0,
    "chapter_review": DecisionLevel.L0,
    "chapter_commit": DecisionLevel.L0,
    "plot_adjustment": DecisionLevel.L0,

    # 修改操作 → L1
    "paragraph_edit": DecisionLevel.L1,
    "character_edit": DecisionLevel.L1,
    "setting_edit": DecisionLevel.L1,
    "cache_delete": DecisionLevel.L1,
    "backup_restore": DecisionLevel.L1,

    # 生成操作 → L2
    "outline_generate": DecisionLevel.L2,
    "plot_branch_generate": DecisionLevel.L2,
    "character_generate": DecisionLevel.L2,

    # 创造性操作 → L3
    "volume_setting": DecisionLevel.L3,
    "character_design": DecisionLevel.L3,
    "core_plot_adjustment": DecisionLevel.L3,
    "genre_setting": DecisionLevel.L3,
    "system_config": DecisionLevel.L3,
}

# ---------------------------------------------------------------------------
# 自由度锁定义
# ---------------------------------------------------------------------------
class FreedomLevel(Enum):
    """
    自由度级别枚举：
    0%：严格遵守所有约束，不允许任何发挥（默认）
    10%：允许细微描述调整，不改变剧情走向和设定
    20%：允许minor剧情调整，不改变核心剧情和人物设定
    """
    ZERO = "0%"
    LOW = "10%"
    MEDIUM = "20%"

# 自由度级别描述
FREEDOM_LEVEL_DESCRIPTIONS = {
    FreedomLevel.ZERO: "严格遵守所有约束，不允许任何发挥",
    FreedomLevel.LOW: "允许细微描述调整，不改变剧情走向和设定",
    FreedomLevel.MEDIUM: "允许minor剧情调整，不改变核心剧情和人物设定",
}

# 默认自由度级别
DEFAULT_FREEDOM_LEVEL = FreedomLevel.ZERO

# ---------------------------------------------------------------------------
# 自由度级别 → 约束严格度映射（Phase 2）
# ---------------------------------------------------------------------------
FREEDOM_LEVEL_CONSTRAINT_MAP = {
    FreedomLevel.ZERO: {
        "instruction": "严格遵守，不允许任何发挥。所有约束为硬性要求，不得违反。",
        "modifier_key": "strict",
        "anti_ai_level": "strict",
        "description": "严格执行三大定律 + 全部 Anti-AI 规则 + 全部 Style 规则",
    },
    FreedomLevel.LOW: {
        "instruction": "允许细微描述调整（如环境细节、动作顺序），不改变剧情走向和设定。核心约束仍然有效。",
        "modifier_key": "allow_minor_description",
        "anti_ai_level": "normal",
        "description": "核心约束有效；Anti-AI 和 Style 规则正常执行",
    },
    FreedomLevel.MEDIUM: {
        "instruction": "允许minor剧情调整（如分支对话、次要事件顺序），不改变核心剧情和人物设定。三大定律仍然有效。",
        "modifier_key": "allow_minor_plot",
        "anti_ai_level": "relaxed",
        "description": "三大定律有效；Anti-AI 和 Style 规则适度放松",
    },
}


def validate_freedom_level(level: str) -> bool:
    """校验自由度级别字符串是否合法。"""
    try:
        FreedomLevel(level)
        return True
    except ValueError:
        return False

# ---------------------------------------------------------------------------
# 校验函数
# ---------------------------------------------------------------------------
def get_decision_level_for_operation(operation_type: str) -> Optional[DecisionLevel]:
    """
    根据操作类型获取对应的默认决策级别
    """
    return OPERATION_DEFAULT_DECISION_LEVEL.get(operation_type)

def validate_decision_level(operation_type: str, user_decision_level: DecisionLevel) -> bool:
    """
    校验用户选择的决策级别是否合法：
    - 用户不能选择比默认级别更低的决策级别（例如默认L2的操作不能改成L0/L1）
    - 只允许选择等于或高于默认级别的决策级别
    """
    default_level = get_decision_level_for_operation(operation_type)
    if not default_level:
        # 未知操作类型，默认需要最高级别L3确认
        return user_decision_level == DecisionLevel.L3

    # 级别权重：L0 < L1 < L2 < L3
    level_weight = {
        DecisionLevel.L0: 0,
        DecisionLevel.L1: 1,
        DecisionLevel.L2: 2,
        DecisionLevel.L3: 3,
    }

    return level_weight[user_decision_level] >= level_weight[default_level]

# ---------------------------------------------------------------------------
# 魔法数字常量（Phase 5）
# ---------------------------------------------------------------------------

# ── 草稿质量闸门 ──
DRAFT_MIN_CHINESE_CHARS: int = 2000
DRAFT_AI_MARKER_THRESHOLD: int = 15          # AI 标记词总数阈值
DRAFT_AI_MARKER_DENSITY: float = 5.0         # 每千字 AI 标记词密度阈值

# ── Critic 窗口审查 ──
CRITIC_WINDOW_SIZE: int = 250
CRITIC_MAX_WINDOWS: int = 12
CRITIC_CONCURRENCY: int = 4

# ── Agent 默认参数 ──
AGENT_DEFAULT_MAX_TOKENS: int = 8192
AGENT_DEFAULT_MAX_TURNS: int = 8
DRAFT_MAX_TOKENS: int = 16384
POLISH_MAX_TOKENS: int = 24000
REPLAN_MAX_TOKENS: int = 24000
SKILL_AGENT_MAX_TOKENS: int = 16384
SKILL_AGENT_MAX_TURNS: int = 50
CHARACTER_SCRIPT_MAX_TOKENS: int = 16384
CHARACTER_SCRIPT_MAX_TURNS: int = 30

# ── 子进程超时（秒）──
SUBPROCESS_TIMEOUT_DEFAULT: float = 600.0
SUBPROCESS_TIMEOUT_BRIEF: float = 30.0
SUBPROCESS_TIMEOUT_SHORT: float = 60.0
SUBPROCESS_TIMEOUT_MEDIUM: float = 120.0

# ── Polish 相关 ──
POLISH_AUDIT_ISSUES_MAX_CHARS: int = 6000
POLISH_MAX_PARAGRAPH_LENGTH: int = 150

# ── 设定/章节文本截断 ──
SETTINGS_CHUNK_MAX_CHARS: int = 3000
SETTINGS_TOTAL_CHUNKS: int = 20
CHAPTER_TEXT_MAX_CHARS: int = 5000
CHAPTER_TOTAL_CHUNKS: int = 10

# ── Context 输入截断 ──
REPLAN_SETTINGS_MAX_CHARS: int = 15000
REPLAN_CHAPTERS_MAX_CHARS: int = 20000
REPLAN_MASTER_MAX_CHARS: int = 5000
REPLAN_PROJECT_INFO_MAX_CHARS: int = 3000
