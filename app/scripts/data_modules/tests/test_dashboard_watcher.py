#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace


def test_dashboard_watcher_notifies_story_system_commit_changes(tmp_path):
    from dashboard.watcher import _AInovelFileHandler

    changed = []
    handler = _AInovelFileHandler(
        lambda path, kind: changed.append((Path(path).name, kind)),
        watch_ainovel_dir=tmp_path / ".ainovel",
        watch_story_system_dir=tmp_path / ".story-system",
    )

    event = SimpleNamespace(
        is_directory=False,
        src_path=str(tmp_path / ".story-system" / "commits" / "chapter_003.commit.json"),
    )

    handler.on_modified(event)

    assert changed == [("chapter_003.commit.json", "modified")]
