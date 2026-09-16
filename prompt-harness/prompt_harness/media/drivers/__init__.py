"""驱动插件：生图 / 生视频 / TTS，统一 manifest 与 DriveResult（总计划 §2.3 驱动插件化约定）。"""
from __future__ import annotations

from .base import BaseDriver, DriveResult, DriverManifest
from .cloud_tts import CloudTTSDriver
from .kokoro import KokoroDriver, NEUTRAL_VOICE, VOICE_TABLE
from .seedance import SeedanceDriver
from .seedream import SeedreamDriver

__all__ = [
    "BaseDriver",
    "DriveResult",
    "DriverManifest",
    "SeedreamDriver",
    "SeedanceDriver",
    "KokoroDriver",
    "CloudTTSDriver",
    "VOICE_TABLE",
    "NEUTRAL_VOICE",
]
