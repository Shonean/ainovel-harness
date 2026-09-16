"""配置读取 — 统一从环境变量读取（单一真相源：用户级 .env）。

⚠️ v5.2 起：配置已统一由主系统管理。
- 唯一真相源：~/.claude/ainovel-write/.env（用户级）
- 主系统 env_config.py 负责加载和写入
- prompt-harness 直接从 os.environ 读取，不再加载自己的 .env 文件
- 这样改一次 API key，两个系统同时生效

优先级（从高到低）：
  1. 环境变量 os.environ（由主系统统一加载）
  2. 主应用通过 init_settings() 传入的参数
  3. 默认值
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

# 注意：不再加载 prompt-harness/.env
# 之前的 load_dotenv 逻辑已移除，避免与主系统的用户级 .env 冲突
# 独立运行 prompt-harness 时，需要自己设置环境变量或在启动脚本中加载 .env


def _user_env_path() -> Path:
    """用户级 .env 路径（对齐主系统 _user_env_path：AINOVEL_CLAUDE_HOME 可覆盖）。"""
    home = Path(os.environ.get("AINOVEL_CLAUDE_HOME", str(Path.home())))
    return home / ".claude" / "ainovel-write" / ".env"


def _scoring_flag_from_env_file() -> bool | None:
    """从用户级 .env 文件读 SCORING_V519（None = 文件未定义）。

    为什么要读文件而不是 os.environ：rebuild-and-restart 的 helper 用 Popen
    不传 env=，新进程继承旧进程 os.environ——若某次测试把 SCORING_V519=0 写进
    .env，该值会冻结在进程 env；.env 还原后主系统 load_dotenv(override=False)
    又不会删除已存在的变量 → 陈旧 "0" 永远生效。以 .env 文件为准可绕开该坑。
    """
    try:
        p = _user_env_path()
        if p.exists():
            for line in p.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line and not line.startswith("#") and line.startswith("SCORING_V519="):
                    return line.split("=", 1)[1].strip().lower() not in ("0", "false")
    except Exception:
        pass
    return None


@dataclass
class Settings:
    ark_api_key: str = ""
    ark_base_url: str = "https://ark.cn-beijing.volces.com/api/coding/v3"
    ark_model_pro: str = ""
    judge_model: str = ""
    embed_api_key: str = ""
    embed_base_url: str = "https://api.siliconflow.cn/v1"
    embed_model: str = "Qwen/Qwen3-Embedding-8B"
    host: str = "127.0.0.1"
    port: int = 0  # deprecated: 独立端口已废弃，统一通过主应用 8765 访问
    data_dir: Path = field(default_factory=Path)
    corpus_dir: Path = field(default_factory=Path)
    output_dir: Path = field(default_factory=Path)

    # 【v5.20】新评分（用户拍板）：综合 = 0.5×字符 + 0.3×剧情 + 0.2×句式
    # SCORING_V519=0 一键回退旧 b1-b8 评分；其余 v519_* 调权重/分段/限流。
    scoring_v519: bool = True
    v519_w_char: float = 0.5
    v519_w_plot: float = 0.3
    v519_w_syn: float = 0.2
    v519_n_min: int = 200
    v519_n_max: int = 250
    v519_embed_gap: float = 0.5

    # 【v5.21】对白轮保真混合权重：plot_sim = (1-w)×beat余弦 + w×对白轮保真。
    # V521_TURN_WEIGHT=0 → 退化为纯 beat（v5.20 行为）。
    v521_turn_weight: float = 0.3
    v521_turn_match_thresh: float = 0.6
    # 【v5.27】AI 味审阅闭环：LLM 对照原文审阅生成正文的 AI 味，分数进综合分惩罚系数
    # （乘法惩罚，不稀释 s_char/plot_sim/v_cos 权重）；findings 进迭代反馈与修正块。
    ai_flavor_review: bool = True
    ai_flavor_penalty: float = 0.35  # 综合分 × (1 - penalty×(1-ai_flavor))
    ai_flavor_retry: int = 3         # 【v5.27.4】AI 味/间隙/转述 → 同轮重生成上限（顽固章需多次）
    # 【v5.21】生成端有序对白契约：把原文对话轮次按序确定性提取，注入生成 prompt，
    # 强制模型按序复现（骨架保持紧凑，对白顺序靠生成输入保证）。V521_DIALOGUE_CONTRACT=0 关闭。
    dialogue_order_contract: bool = True

    # 【v5.29】极简推导训练（压缩前沿探索）：字数预算档 / 每档采样数 / 充分阈值比例。
    # MINIMAL_TRAIN_BUDGETS="15,30,50,80,120"；threshold：综合分 ≥ ratio×最高档均分 = 充分。
    minimal_train_budgets: str = "15,30,50,80,120"
    minimal_train_samples: int = 2
    minimal_train_threshold_ratio: float = 0.9
    # 训练评分是否跑 AI 味审阅（LLM，慢；冒烟可关 MINIMAL_TRAIN_AI_FLAVOR=0）。
    minimal_train_ai_flavor: bool = True

    # 【v5.30】推导树 AI 味防线：场景叶子生成后逐章 LLM 审阅（无原文对照），
    # 脏章注入【AI味修正块】重生成。derive_ai_flavor_threshold=审阅分低于此触发。
    derive_ai_flavor_review: bool = True
    derive_ai_flavor_retry: int = 1
    derive_ai_flavor_threshold: float = 0.70

    # 【v5.32】生成阶梯：场景叶子每类条数（密集度，保证骨架撑得起全文）+
    # 场景级正文生成的单场景目标字数（有原文时按原文长/场景数自适应覆盖）。
    derive_scene_density: int = 4
    derive_scene_target_len: int = 600


def _scorer_param_overrides() -> dict:
    """【Phase 2-full 修复 2026-09-04】模块级 SETTINGS 也收敛到 scorer_params.json。

    原先只有 init_settings()（主应用启动才调用）读 JSON，standalone 进程
    （promptopt trainer / 冻结验证 / 离线脚本）全部拿到 dataclass 硬编码 v1 默认值，
    导致「冻结评分器」纪律对独立进程从未生效（Phase 3 T17 实际跑的是 v1 权重）。
    env 优先级只在 init_settings 保持（与主应用行为一致）；JSON 缺失时
    get_weight/get_int 回退内置默认（=原 dataclass 值），行为兼容。
    """
    from . import scorer_params as _sp
    return {
        "v519_w_char": _sp.get_weight("v519_w_char"),
        "v519_w_plot": _sp.get_weight("v519_w_plot"),
        "v519_w_syn": _sp.get_weight("v519_w_syn"),
        "v519_n_min": _sp.get_int("v519_n_min"),
        "v519_n_max": _sp.get_int("v519_n_max"),
        "v519_embed_gap": _sp.get_weight("v519_embed_gap"),
        "v521_turn_weight": _sp.get_weight("v521_turn_weight"),
        "v521_turn_match_thresh": _sp.get_weight("v521_turn_match_thresh"),
        "ai_flavor_penalty": _sp.get_weight("ai_flavor_penalty"),
        "derive_ai_flavor_threshold": _sp.get_weight("derive_ai_flavor_threshold"),
    }


SETTINGS = Settings(**_scorer_param_overrides())


def init_settings(
    data_dir: Path,
    corpus_dir: Path | None = None,
    output_dir: Path | None = None,
    *,
    ark_api_key: str = "",
    ark_base_url: str = "",
    ark_model_pro: str = "",
    judge_model: str = "",
    embed_api_key: str = "",
    embed_base_url: str = "",
    embed_model: str = "",
) -> None:
    """由主应用调用，用主系统的 API 预设初始化 prompt-harness 配置。"""
    global SETTINGS

    # Phase 0：评分权重/阈值默认值从 scorer_params.json 读（env 仍最高优先级）
    from . import scorer_params as _sp

    # API 配置优先使用主系统环境变量，其次用传入参数
    SETTINGS = Settings(
        ark_api_key=os.getenv("ARK_API_KEY", ark_api_key),
        ark_base_url=os.getenv("ARK_BASE_URL", ark_base_url or "https://ark.cn-beijing.volces.com/api/coding/v3"),
        ark_model_pro=os.getenv("ARK_MODEL_PRO", ark_model_pro),
        judge_model=os.getenv("JUDGE_MODEL", judge_model or os.getenv("ARK_MODEL_PRO", ark_model_pro)),
        embed_api_key=os.getenv("EMBED_API_KEY", embed_api_key),
        embed_base_url=os.getenv("EMBED_BASE_URL", embed_base_url or "https://api.siliconflow.cn/v1"),
        embed_model=os.getenv("EMBED_MODEL", embed_model or "Qwen/Qwen3-Embedding-8B"),
        host="127.0.0.1",
        port=0,  # 独立端口已废弃，统一通过主应用 8765 访问
        data_dir=data_dir,
        corpus_dir=corpus_dir or (data_dir.parent / "corpus"),
        output_dir=output_dir or (data_dir.parent / "output"),
        # 【v5.20】新评分开关/参数（主应用不设这些 → 默认生效；SCORING_V519=0 回退旧评分）
        # 【v5.21】以 .env 文件为准读 SCORING_V519（os.environ 可能有陈旧值：回归测试
        # 写过 SCORING_V519=0 后 .env 还原，但进程 env 冻结、override=False 删不掉）。
        scoring_v519=(
            True if _scoring_flag_from_env_file() is None else _scoring_flag_from_env_file()
        ),
        # 数值默认值收敛到 scorer_params（Phase 0 抽取）；env 仍最高优先级
        v519_w_char=float(os.getenv("V519_W_CHAR", str(_sp.get_weight("v519_w_char")))),
        v519_w_plot=float(os.getenv("V519_W_PLOT", str(_sp.get_weight("v519_w_plot")))),
        v519_w_syn=float(os.getenv("V519_W_SYN", str(_sp.get_weight("v519_w_syn")))),
        v519_n_min=int(os.getenv("V519_N_MIN", str(_sp.get_int("v519_n_min")))),
        v519_n_max=int(os.getenv("V519_N_MAX", str(_sp.get_int("v519_n_max")))),
        v519_embed_gap=float(os.getenv("V519_EMBED_GAP", str(_sp.get_weight("v519_embed_gap")))),
        # 【v5.21】对白轮保真（V521_TURN_WEIGHT=0 退化纯 beat）
        v521_turn_weight=float(os.getenv("V521_TURN_WEIGHT", str(_sp.get_weight("v521_turn_weight")))),
        v521_turn_match_thresh=float(os.getenv("V521_TURN_MATCH_THRESH", str(_sp.get_weight("v521_turn_match_thresh")))),
        # 【v5.27】AI 味审阅（AI_FLAVOR_REVIEW=0 关闭；AI_FLAVOR_PENALTY 调惩罚强度）
        ai_flavor_review=os.getenv("AI_FLAVOR_REVIEW", "1").strip().lower()
        not in ("0", "false", ""),
        ai_flavor_penalty=float(os.getenv("AI_FLAVOR_PENALTY", str(_sp.get_weight("ai_flavor_penalty")))),
        ai_flavor_retry=int(os.getenv("AI_FLAVOR_RETRY", "3")),
        # 【v5.21】生成端有序对白契约（V521_DIALOGUE_CONTRACT=0 关闭）
        dialogue_order_contract=os.getenv("V521_DIALOGUE_CONTRACT", "1").strip().lower()
        not in ("0", "false", ""),
        # 【v5.29】极简推导训练（压缩前沿）
        minimal_train_budgets=os.getenv("MINIMAL_TRAIN_BUDGETS", "15,30,50,80,120"),
        minimal_train_samples=int(os.getenv("MINIMAL_TRAIN_SAMPLES", "2")),
        minimal_train_threshold_ratio=float(os.getenv("MINIMAL_TRAIN_THRESHOLD_RATIO", "0.9")),
        minimal_train_ai_flavor=os.getenv("MINIMAL_TRAIN_AI_FLAVOR", "1").strip().lower()
        not in ("0", "false", ""),
        # 【v5.30】推导树 AI 味防线（DERIVE_AI_FLAVOR_REVIEW=0 关闭；RETRY 脏章重生成上限）
        derive_ai_flavor_review=os.getenv("DERIVE_AI_FLAVOR_REVIEW", "1").strip().lower()
        not in ("0", "false", ""),
        derive_ai_flavor_retry=int(os.getenv("DERIVE_AI_FLAVOR_RETRY", "1")),
        derive_ai_flavor_threshold=float(os.getenv("DERIVE_AI_FLAVOR_THRESHOLD", "0.70")),
        # 【v5.32】生成阶梯（DERIVE_SCENE_DENSITY / DERIVE_SCENE_TARGET_LEN）
        derive_scene_density=int(os.getenv("DERIVE_SCENE_DENSITY", "4")),
        derive_scene_target_len=int(os.getenv("DERIVE_SCENE_TARGET_LEN", "600")),
    )
    SETTINGS.data_dir.mkdir(parents=True, exist_ok=True)
    SETTINGS.corpus_dir.mkdir(parents=True, exist_ok=True)
    SETTINGS.output_dir.mkdir(parents=True, exist_ok=True)


def load_scoring_params(path: Path | None = None) -> dict:
    """加载 scoring_params.json 配置。

    从 data/scoring_params.json 加载所有可调参数的默认值和搜索空间。
    如果文件不存在或解析失败，返回空 dict（调用方使用内置默认值）。
    """
    if path is None:
        path = SETTINGS.data_dir / "scoring_params.json"
    try:
        import json
        if path.exists():
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception:
        print(f"[config] Warning: failed to load scoring params from {path}")
    return {}


def get_llm_config(preset_id: str | None = None) -> dict[str, str]:
    """获取 LLM API 配置。preset_id 为 None 时使用当前设置。

    注意：完整 API 预设支持需要在主应用层通过 api_library 实现。
    这里只提供当前 env 或 SETTINGS 的键值。
    """
    return {
        "api_key": SETTINGS.ark_api_key,
        "base_url": SETTINGS.ark_base_url,
        "model": SETTINGS.ark_model_pro,
    }
