#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Data Modules - 配置文件

API 配置通过环境变量读取（支持 .env 文件）：
- EMBED_BASE_URL, EMBED_MODEL, EMBED_API_KEY
- RERANK_BASE_URL, RERANK_MODEL, RERANK_API_KEY
"""

import os
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional

from runtime_compat import normalize_windows_path


# ===================================================================
# Context template weights (was context_weights.py)
# ===================================================================

DEFAULT_TEMPLATE = "plot"

TEMPLATE_WEIGHTS: dict[str, dict[str, float]] = {
    "plot": {"core": 0.40, "scene": 0.35, "global": 0.25},
    "battle": {"core": 0.35, "scene": 0.45, "global": 0.20},
    "emotion": {"core": 0.45, "scene": 0.35, "global": 0.20},
    "transition": {"core": 0.50, "scene": 0.25, "global": 0.25},
}

TEMPLATE_WEIGHTS_DYNAMIC_DEFAULT: dict[str, dict[str, dict[str, float]]] = {
    "early": {
        "plot": {"core": 0.48, "scene": 0.39, "global": 0.13},
        "battle": {"core": 0.42, "scene": 0.50, "global": 0.08},
        "emotion": {"core": 0.52, "scene": 0.38, "global": 0.10},
        "transition": {"core": 0.56, "scene": 0.28, "global": 0.16},
    },
    "mid": {
        "plot": {"core": 0.40, "scene": 0.35, "global": 0.25},
        "battle": {"core": 0.35, "scene": 0.45, "global": 0.20},
        "emotion": {"core": 0.45, "scene": 0.35, "global": 0.20},
        "transition": {"core": 0.50, "scene": 0.25, "global": 0.25},
    },
    "late": {
        "plot": {"core": 0.36, "scene": 0.29, "global": 0.35},
        "battle": {"core": 0.31, "scene": 0.39, "global": 0.30},
        "emotion": {"core": 0.41, "scene": 0.29, "global": 0.30},
        "transition": {"core": 0.46, "scene": 0.21, "global": 0.33},
    },
}

def _get_user_claude_root() -> Path:
    raw = os.environ.get("AINOVEL_CLAUDE_HOME") or os.environ.get("CLAUDE_HOME")
    if raw:
        try:
            return normalize_windows_path(raw).expanduser().resolve()
        except Exception:
            return normalize_windows_path(raw).expanduser()
    return (Path.home() / ".claude").resolve()


def _load_dotenv_file(env_path: Path, *, override: bool = False) -> bool:
    if not env_path.exists():
        return False
    try:
        with open(env_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, _, value = line.partition("=")
                    key = key.strip()
                    value = value.strip()
                    if not key:
                        continue
                    # 默认不覆盖已有环境变量（保持“显式 > .env”优先级）
                    if override or key not in os.environ:
                        os.environ[key] = value
        return True
    except Exception:
        return False


def _user_env_path() -> Path:
    """用户级全局 .env 路径：~/.claude/ainovel-write/.env（受 AINOVEL_CLAUDE_HOME 覆盖）。"""
    return _get_user_claude_root() / "ainovel-write" / ".env"


# 模块级幂等标志：避免重复读盘。可被测试/切换上下文时通过 reset_user_env() 复位。
_user_env_loaded: bool = False


def load_user_env(*, force: bool = False) -> bool:
    """
    加载用户级全局 .env（best-effort，override=False：显式 shell 环境变量优先）。

    设计要点（模块隔离 / 每本书独立于系统）：
    - **仅**加载 `~/.claude/ainovel-write/.env` 一份；**不**读 CWD、**不**读项目级 .env。
      API key 统一放用户级，书目录不放敏感 key，杜绝跨书 .env 串台。
    - 不在 import 时调用，避免「import 时 CWD 的 .env 先入 os.environ 永久阻塞项目值」。
      由 dashboard 启动与各 CLI main() 显式调用一次。
    - 幂等：默认只加载一次；force=True 可强制重读（测试/切换用户上下文时用）。
    """
    global _user_env_loaded
    if _user_env_loaded and not force:
        return False
    loaded = _load_dotenv_file(_user_env_path(), override=False)
    _user_env_loaded = True
    return loaded


def reset_user_env_loaded() -> None:
    """复位幂等标志（测试中切换 AINOVEL_CLAUDE_HOME 后重载用）。"""
    global _user_env_loaded
    _user_env_loaded = False


def _default_context_template_weights_dynamic() -> dict[str, dict[str, dict[str, float]]]:
    return {
        stage: {
            template: dict(weights)
            for template, weights in templates.items()
        }
        for stage, templates in TEMPLATE_WEIGHTS_DYNAMIC_DEFAULT.items()
    }


@dataclass
class DataModulesConfig:
    """数据模块配置"""

    # ================= 项目路径 =================
    project_root: Path = field(default_factory=lambda: Path.cwd())

    @property
    def ainovel_dir(self) -> Path:
        return self.project_root / ".ainovel"

    @property
    def state_file(self) -> Path:
        return self.ainovel_dir / "state.json"

    @property
    def scratchpad_file(self) -> Path:
        return self.ainovel_dir / "memory_scratchpad.json"

    @property
    def index_db(self) -> Path:
        return self.ainovel_dir / "index.db"

    # v5.1 引入: alias_index_file 已废弃，别名存储在 index.db aliases 表

    @property
    def chapters_dir(self) -> Path:
        return self.project_root / "正文"

    @property
    def settings_dir(self) -> Path:
        return self.project_root / "设定集"

    @property
    def outline_dir(self) -> Path:
        return self.project_root / "大纲"

    @property
    def story_system_dir(self) -> Path:
        return self.project_root / ".story-system"

    @property
    def story_system_chapters_dir(self) -> Path:
        return self.story_system_dir / "chapters"

    @property
    def story_system_master_json(self) -> Path:
        return self.story_system_dir / "MASTER_SETTING.json"

    @property
    def story_system_anti_patterns_json(self) -> Path:
        return self.story_system_dir / "anti_patterns.json"


    # ================= Embedding API 配置 =================
    embed_api_type: str = "openai"
    embed_base_url: str = field(default_factory=lambda: os.getenv("EMBED_BASE_URL", "https://api-inference.modelscope.cn/v1"))
    embed_model: str = field(default_factory=lambda: os.getenv("EMBED_MODEL", "Qwen/Qwen3-Embedding-8B"))
    embed_api_key: str = field(default_factory=lambda: os.getenv("EMBED_API_KEY", ""))

    @property
    def embed_url(self) -> str:
        return self.embed_base_url

    # ================= Rerank API 配置 =================
    rerank_api_type: str = "openai"
    rerank_base_url: str = field(default_factory=lambda: os.getenv("RERANK_BASE_URL", "https://api.jina.ai/v1"))
    rerank_model: str = field(default_factory=lambda: os.getenv("RERANK_MODEL", "jina-reranker-v3"))
    rerank_api_key: str = field(default_factory=lambda: os.getenv("RERANK_API_KEY", ""))

    @property
    def rerank_url(self) -> str:
        return self.rerank_base_url

    # ================= FAISS 配置 =================
    faiss_index_enabled: bool = field(default_factory=lambda: os.getenv("FAISS_INDEX_ENABLED", "1") == "1")
    rerank_enabled: bool = field(default_factory=lambda: os.getenv("RERANK_ENABLED", "1") == "1")
    guidance_semantic_filter_enabled: bool = field(
        default_factory=lambda: os.getenv("GUIDANCE_SEMANTIC_FILTER_ENABLED", "1") == "1"
    )

    @property
    def faiss_index_dir(self) -> Path:
        return self.ainovel_dir / "faiss_index"

    # ================= 并发配置 =================
    # embedding 并发请求数。火山引擎 Ark Coding Plan 有账号级 RPM 限制，并发过高
    # 会触发 429 AccountRateLimitExceeded（整批失败→语义检索降级）。默认 4 为保守值，
    # 可用 EMBED_CONCURRENCY 调高（OpenAI/Jina 等可设 32/64）。
    embed_concurrency: int = field(default_factory=lambda: int(os.getenv("EMBED_CONCURRENCY", "4")))
    rerank_concurrency: int = 32
    # 单次 embedding 请求的 input 条数上限。火山引擎 Ark 限制 max 10/请求，
    # 故默认 10（对 OpenAI/Jina 同样安全；如 provider 允许更大批次，可用
    # EMBED_BATCH_SIZE 调高）。设过大（如原 64）会触发 400 InvalidParameter，
    # 导致整批失败、语义检索静默降级为 BM25。
    embed_batch_size: int = field(default_factory=lambda: int(os.getenv("EMBED_BATCH_SIZE", "10")))

    # ================= 超时配置 =================
    cold_start_timeout: int = 300
    normal_timeout: int = 180

    # ================= 重试配置 =================
    api_max_retries: int = 3  # 最大重试次数
    api_retry_delay: float = 1.0  # 初始重试延迟（秒），使用指数退避

    # ================= 检索配置 =================
    vector_top_k: int = 30
    bm25_top_k: int = 20
    rerank_top_n: int = 10
    rrf_k: int = 60

    vector_full_scan_max_vectors: int = 500
    vector_prefilter_bm25_candidates: int = 200
    vector_prefilter_recent_candidates: int = 200

    # ================= Graph-RAG 配置 =================
    graph_rag_enabled: bool = False
    graph_rag_expand_hops: int = 1
    graph_rag_max_expanded_entities: int = 30
    graph_rag_candidate_limit: int = 150
    graph_rag_boost_same_entity: float = 0.2
    graph_rag_boost_related_entity: float = 0.1
    graph_rag_boost_recency: float = 0.05

    relationship_graph_from_index_enabled: bool = True

    # ================= 实体提取配置 =================
    extraction_confidence_high: float = 0.8
    extraction_confidence_medium: float = 0.5

    # ================= 列表截断限制 =================
    max_disambiguation_warnings: int = 500
    max_disambiguation_pending: int = 1000
    max_state_changes: int = 2000

    context_recent_summaries_window: int = 3
    context_recent_meta_window: int = 3
    context_alerts_slice: int = 10
    context_max_appearing_characters: int = 10
    context_max_urgent_foreshadowing: int = 5
    context_story_skeleton_interval: int = 20
    context_story_skeleton_max_samples: int = 5
    context_story_skeleton_snippet_chars: int = 400
    context_extra_section_budget: int = 800
    context_ranker_enabled: bool = True
    context_ranker_recency_weight: float = 0.7
    context_ranker_frequency_weight: float = 0.3
    context_ranker_hook_bonus: float = 0.2
    context_ranker_length_bonus_cap: float = 0.2
    context_ranker_alert_critical_keywords: tuple[str, ...] = (
        "冲突",
        "矛盾",
        "critical",
        "break",
        "违规",
        "断裂",
    )
    context_ranker_debug: bool = False
    context_reader_signal_enabled: bool = True
    context_reader_signal_recent_limit: int = 5
    context_reader_signal_window_chapters: int = 20
    context_reader_signal_review_window: int = 5
    context_reader_signal_include_debt: bool = False
    context_genre_profile_enabled: bool = True
    context_genre_profile_max_refs: int = 8
    context_genre_profile_fallback: str = "shuangwen"
    context_compact_text_enabled: bool = True
    context_compact_min_budget: int = 120
    context_compact_head_ratio: float = 0.65
    context_writing_guidance_enabled: bool = True
    context_writing_guidance_max_items: int = 6
    context_writing_guidance_low_score_threshold: float = 75.0
    context_writing_guidance_hook_diversify: bool = True
    context_methodology_enabled: bool = True
    context_methodology_genre_whitelist: tuple[str, ...] = ("*",)
    context_methodology_label: str = "digital-serial-v1"
    context_writing_checklist_enabled: bool = True
    context_writing_checklist_min_items: int = 3
    context_writing_checklist_max_items: int = 6
    context_writing_checklist_default_weight: float = 1.0
    context_writing_score_persist_enabled: bool = True
    context_writing_score_include_reader_trend: bool = True
    context_writing_score_trend_window: int = 10
    context_rag_assist_enabled: bool = True
    context_rag_assist_top_k: int = 4
    context_rag_assist_min_outline_chars: int = 40
    context_rag_assist_max_query_chars: int = 120
    context_dynamic_budget_enabled: bool = True
    context_dynamic_budget_early_chapter: int = 30
    context_dynamic_budget_late_chapter: int = 120
    context_dynamic_budget_early_core_bonus: float = 0.08
    context_dynamic_budget_early_scene_bonus: float = 0.04
    context_dynamic_budget_late_global_bonus: float = 0.08
    context_dynamic_budget_late_scene_penalty: float = 0.06
    context_template_weights_dynamic: dict[str, dict[str, dict[str, float]]] = field(
        default_factory=_default_context_template_weights_dynamic
    )
    context_genre_profile_support_composite: bool = True
    context_genre_profile_max_genres: int = 2
    context_genre_profile_separators: tuple[str, ...] = (
        "+",
        "/",
        "|",
        ",",
        "，",
        "、",
    )
    context_use_memory_orchestrator: bool = False
    memory_orchestrator_max_items: int = 30
    memory_orchestrator_recent_changes_limit: int = 10
    memory_orchestrator_source_window: int = 20
    memory_compactor_enabled: bool = True
    memory_compactor_threshold: int = 500

    export_recent_changes_slice: int = 20
    export_disambiguation_slice: int = 20

    # ================= 查询默认限制 =================
    query_recent_chapters_limit: int = 10
    query_scenes_by_location_limit: int = 20
    query_entity_appearances_limit: int = 50
    query_recent_appearances_limit: int = 20

    # ================= 伏笔紧急度 =================
    foreshadowing_urgency_pending_high: int = 100
    foreshadowing_urgency_pending_medium: int = 50
    foreshadowing_urgency_target_proximity: int = 5
    foreshadowing_urgency_score_high: int = 100
    foreshadowing_urgency_score_medium: int = 60
    foreshadowing_urgency_score_target: int = 80
    foreshadowing_urgency_score_low: int = 20
    foreshadowing_urgency_threshold_show: int = 60

    foreshadowing_tier_weight_core: float = 3.0
    foreshadowing_tier_weight_sub: float = 2.0
    foreshadowing_tier_weight_decor: float = 1.0

    # ================= 角色活跃度 =================
    character_absence_warning: int = 30
    character_absence_critical: int = 100
    character_candidates_limit: int = 800

    # ================= Strand Weave 节奏 =================
    strand_quest_max_consecutive: int = 5
    strand_fire_max_gap: int = 10
    strand_constellation_max_gap: int = 15

    strand_quest_ratio_min: int = 55
    strand_quest_ratio_max: int = 65
    strand_fire_ratio_min: int = 20
    strand_fire_ratio_max: int = 30
    strand_constellation_ratio_min: int = 10
    strand_constellation_ratio_max: int = 20

    # ================= 爽点节奏 =================
    pacing_segment_size: int = 100
    pacing_words_per_point_excellent: int = 1000
    pacing_words_per_point_good: int = 1500
    pacing_words_per_point_acceptable: int = 2000

    # ================= RAG 存储 =================
    @property
    def rag_db(self) -> Path:
        return self.ainovel_dir / "rag.db"

    @property
    def vector_db(self) -> Path:
        return self.ainovel_dir / "vectors.db"

    def ensure_dirs(self):
        self.ainovel_dir.mkdir(parents=True, exist_ok=True)

    @classmethod
    def from_project_root(cls, project_root: str | Path) -> "DataModulesConfig":
        root = normalize_windows_path(project_root).expanduser().resolve()
        # 不再加载项目级 .env：API key 统一来自用户级全局 .env（load_user_env，由
        # dashboard 启动 / CLI main 显式调用）+ shell 环境变量。书目录不放敏感 key，
        # 杜绝跨书 .env 串台。
        return cls(project_root=root)


def get_config(project_root: Optional[Path] = None) -> DataModulesConfig:
    """
    取配置。**无进程级缓存**——每次按显式 root 或 project_locator 现取，确保
    多书之间无单例残留（每本书独立于系统）。

    - project_root 非 None：直接为该 root 构造（每次新建）。
    - project_root 为 None：用 project_locator 自动探测（AINOVEL_PROJECT_ROOT /
      `.claude/.ainovel-current-project` 指针 / 从 cwd 向上找 `.ainovel/state.json`）。
    """
    if project_root is not None:
        return DataModulesConfig.from_project_root(project_root)
    from project_locator import resolve_project_root

    root = resolve_project_root()
    return DataModulesConfig.from_project_root(root)
