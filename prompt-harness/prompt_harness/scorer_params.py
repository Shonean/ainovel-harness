# -*- coding: utf-8 -*-
"""评分器可训练参数加载器（Phase 0 抽取）。

把散落在 config.py / ai_flavor.py / completion_audit.py 的
**权重 + 阈值 + LLM 审阅 prompt + AI味禁令文本** 收敛到单一真相源：

    config/scorer_params.json

设计要点：
- **函数内读取**（不做模块级单例）。遵守项目铁律：server.py data_dir 在 init_settings
  重绑后才确定，顶层 import 会捕获旧对象/旧路径。每个 getter 在调用时按 data_dir 解析。
- **缺文件/缺键时回退代码内置默认值**（与抽取前完全一致），保证向后兼容、可独立运行。
- Phase 2 起 promptopt 训练直接覆写这个 JSON，生产代码经此模块读到新值。

这是纯重构：默认值与抽取前硬编码一致，验收标准 = 同批样本分数不变。
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

# ── 内置默认值（与抽取前的硬编码一致；JSON 缺失时回退）──────────────────────

_DEFAULT_WEIGHTS: dict[str, float] = {
    "v519_w_char": 0.5,
    "v519_w_plot": 0.3,
    "v519_w_syn": 0.2,
    "v519_n_min": 200,
    "v519_n_max": 250,
    "v519_embed_gap": 0.5,
    "v521_turn_weight": 0.3,
    "v521_turn_match_thresh": 0.6,
    "ai_flavor_penalty": 0.35,
    "derive_ai_flavor_threshold": 0.70,
}

_DEFAULT_THRESHOLDS: dict[str, float] = {
    "review_confidence_floor": 0.6,
    "regen_dirty_threshold": 0.65,
    "regen_gap_clean_thresh": 0.82,
    "audit_min_len_ratio": 0.8,
    "audit_turn_fidelity_thresh": 0.85,
}

# prompt/禁令默认值不在此硬编码中文（避免 \uXXXX 转义陷阱）；
# JSON 缺失时回退到 ai_flavor 模块里的原常量，保持抽取前行为。
_DEFAULT_PROMPT_KEYS = (
    "review_system_prompt",
    "standalone_review_system_prompt",
    "ai_flavor_ban_block",
    "ai_flavor_ban_block_standalone",
    "tree_ai_flavor_ban",
)


def _params_path() -> Path:
    """解析 scorer_params.json 路径：优先 data_dir，其次仓库 config/ 目录。"""
    try:
        from .config import SETTINGS as _s
        data_dir = Path(_s.data_dir) if _s.data_dir else None
    except Exception:
        data_dir = None
    candidates = []
    if data_dir:
        candidates.append(data_dir / "config" / "scorer_params.json")
        candidates.append(data_dir / "scorer_params.json")
    # 仓库内 config/（prompt-harness 的同级 config/）
    here = Path(__file__).resolve()
    candidates.append(here.parents[2] / "config" / "scorer_params.json")
    for c in candidates:
        if c.exists():
            return c
    return candidates[-1]  # 不存在也返回默认候选，调用方按缺失处理


@lru_cache(maxsize=4)
def _load(version_mtime: float | None = None) -> dict[str, Any]:
    """加载并缓存 JSON。version_mtime 仅作缓存失效键（测试/训练覆写后可用）。"""
    path = _params_path()
    if not path.exists():
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _cached() -> dict[str, Any]:
    path = _params_path()
    mtime = path.stat().st_mtime if path.exists() else None
    return _load(mtime)


def reload() -> None:
    """清缓存（训练覆写 JSON 后或测试时调用）。"""
    _load.cache_clear()


# ── 公共 getter ─────────────────────────────────────────────────────────────

def get_weight(key: str, default: float | None = None) -> float:
    """取评分权重/数值参数。缺键回退内置默认值。"""
    section = _cached().get("weights", {})
    if key in section:
        return float(section[key])
    if default is not None:
        return float(default)
    return float(_DEFAULT_WEIGHTS.get(key, 0.0))


def get_threshold(key: str, default: float | None = None) -> float:
    """取评分/审阅阈值。缺键回退内置默认值。"""
    section = _cached().get("thresholds", {})
    if key in section:
        return float(section[key])
    if default is not None:
        return float(default)
    return float(_DEFAULT_THRESHOLDS.get(key, 0.0))


def get_int(key: str, default: int | None = None) -> int:
    """取整型参数（v519_n_min/max 等），容忍 JSON 把整数存成 200.0。"""
    section = _cached().get("weights", {})
    if key in section:
        return int(float(section[key]))
    if default is not None:
        return int(default)
    return int(float(_DEFAULT_WEIGHTS.get(key, 0)))


def get_prompt(key: str, fallback: str = "") -> str:
    """取可训练 prompt/禁令文本。

    缺键时返回调用方提供的 fallback（生产代码传 ai_flavor 模块原常量），
    保证 JSON 不存在时行为与抽取前一致。
    """
    section = _cached().get("prompts", {})
    val = section.get(key)
    if isinstance(val, str) and val.strip():
        return val
    return fallback


def all_weights() -> dict[str, float]:
    return dict(_DEFAULT_WEIGHTS) | {
        k: float(v) for k, v in _cached().get("weights", {}).items()
    }


def all_thresholds() -> dict[str, float]:
    return dict(_DEFAULT_THRESHOLDS) | {
        k: float(v) for k, v in _cached().get("thresholds", {}).items()
    }
