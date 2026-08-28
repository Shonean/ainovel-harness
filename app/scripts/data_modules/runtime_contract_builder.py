#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Thin re-export wrapper — all logic moved to scripts/story_system.py"""
from __future__ import annotations

# Re-export from the canonical location
from story_system import (
    RuntimeContractBuilder,
    ContractMeta,
    OverrideBundle,
    MasterSetting,
    ChapterBrief,
    VolumeBrief,
    ReviewContract,
)

__all__ = [
    "RuntimeContractBuilder",
    "ContractMeta",
    "OverrideBundle",
    "MasterSetting",
    "ChapterBrief",
    "VolumeBrief",
    "ReviewContract",
]
