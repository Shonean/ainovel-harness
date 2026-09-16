"""drama —— 期 3 漫剧投影与合成。

- projector: Adaptation Pack → shot list（shots.json + subtitles.srt，确定性）+ 资产库回填
- compose:   shot list + 静帧 + 可选音轨/BGM/转场 → PyAV 竖屏 mp4（Ken Burns 运镜 + 字幕烧录）
- align:     字幕时间轴对齐（可选 faster-whisper 后端，可注入 mock）
"""
from __future__ import annotations

from .align import align_srt_to_audio, whisper_available
from .projector import (
    backfill_pack_drama,
    backfill_shot_assets,
    build_srt,
    load_portrait_map,
    project_pack,
    project_to_drama,
    write_drama,
)

__all__ = [
    "project_pack",
    "project_to_drama",
    "write_drama",
    "build_srt",
    "backfill_pack_drama",
    "backfill_shot_assets",
    "load_portrait_map",
    "align_srt_to_audio",
    "whisper_available",
]
