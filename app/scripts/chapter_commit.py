#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from runtime_compat import enable_windows_utf8_stdio

from data_modules.chapter_commit_service import ChapterCommitService


DEFAULT_ARTIFACTS = {
    "review_result": ".ainovel/tmp/review_results.json",
    "fulfillment_result": ".ainovel/tmp/fulfillment_result.json",
    "disambiguation_result": ".ainovel/tmp/disambiguation_result.json",
    "extraction_result": ".ainovel/tmp/extraction_result.json",
}


def _read_json(path: str, default: dict | None = None) -> dict:
    p = Path(path)
    if not p.is_file():
        return default if default is not None else {}
    return json.loads(p.read_text(encoding="utf-8"))


def main() -> None:
    parser = argparse.ArgumentParser(description="Chapter commit CLI")
    parser.add_argument("--project-root", required=True)
    parser.add_argument("--chapter", type=int, required=True)
    parser.add_argument("--review-result", default="", help="review_result JSON 文件")
    parser.add_argument("--fulfillment-result", default="", help="fulfillment_result JSON 文件")
    parser.add_argument("--disambiguation-result", default="", help="disambiguation_result JSON 文件")
    parser.add_argument("--extraction-result", default="", help="extraction_result JSON 文件")
    args = parser.parse_args()

    project_root = Path(args.project_root)

    def _resolve(name: str, arg: str) -> str:
        if arg:
            return arg
        default = project_root / DEFAULT_ARTIFACTS[name]
        return str(default)

    service = ChapterCommitService(project_root)
    payload = service.build_commit(
        chapter=args.chapter,
        review_result=_read_json(
            _resolve("review_result", args.review_result),
            default={"blocking_count": 0},
        ),
        fulfillment_result=_read_json(
            _resolve("fulfillment_result", args.fulfillment_result),
            default={"planned_nodes": [], "covered_nodes": [],
                     "missed_nodes": [], "extra_nodes": []},
        ),
        disambiguation_result=_read_json(
            _resolve("disambiguation_result", args.disambiguation_result),
            default={"pending": []},
        ),
        extraction_result=_read_json(
            _resolve("extraction_result", args.extraction_result),
            default={"accepted_events": [], "state_deltas": [],
                     "entity_deltas": [], "entities_appeared": [],
                     "scenes": [], "summary_text": ""},
        ),
    )
    service.persist_commit(payload)
    if payload["meta"]["status"] == "accepted":
        payload = service.apply_projections(payload)
    print(json.dumps(payload, ensure_ascii=False))


if __name__ == "__main__":
    if sys.platform == "win32":
        enable_windows_utf8_stdio()
    main()
