#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Thin re-export wrapper — all logic moved to scripts/story_system.py"""
from __future__ import annotations

from story_system import (
    StorySystemEngine,
    StorySystemRoutingError,
    is_placeholder_query,
)

__all__ = ["StorySystemEngine", "StorySystemRoutingError", "is_placeholder_query"]
