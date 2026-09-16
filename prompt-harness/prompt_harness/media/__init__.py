"""media —— 期 3 资产管线地基。

组成：
- asset_store: 内容寻址资产库（put_bytes/get/manifest/状态机/pack ingest）
- drivers:    驱动插件（seedream 生图 / kokoro 离线 TTS / cloud TTS），带 manifest
- ark_runner: ark-cli（Node 启动器 + Go 二进制）runner 适配层

设计对齐 `docs/总计划-三线串联与开源集成.md` §2.3（内容寻址 / 驱动插件化 / 权限声明）
与 §3 台账（ark-cli / Kokoro / 云 TTS / PyAV）。
"""
from __future__ import annotations

from .asset_store import AssetStore, Entry, default_asset_library_root

__all__ = ["AssetStore", "Entry", "default_asset_library_root"]
