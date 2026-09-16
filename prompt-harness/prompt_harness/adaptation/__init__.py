"""改编层 v0（Adaptation Layer）—— 把书弧数据编译为引擎无关的 Adaptation Pack。

模块划分见《改编层 v0 设计规格》§4；入口：

    python -m prompt_harness.adaptation build --book <path> --arc <arc_id>
"""
from __future__ import annotations

from .loader import AdaptationError, load_snapshot
from .mapper import compute_pack_id, map_snapshot
from .models import SCHEMA_VERSION
from .validator import validate_data, validate_dir

__all__ = [
    "SCHEMA_VERSION",
    "AdaptationError",
    "load_snapshot",
    "map_snapshot",
    "compute_pack_id",
    "validate_data",
    "validate_dir",
    "build_pack",
]


def build_pack(*args, **kwargs):
    """构建 pack（异步编排，见 cli.build_pack；延迟导入避免 CLI 依赖传染）。"""
    from .cli import build_pack as _build
    return _build(*args, **kwargs)
