#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""CLI entry point — delegates to context_manager for all logic."""
from __future__ import annotations

import json

from .context_manager import scan_placeholders, PrewriteValidator  # noqa: F401


def main() -> None:
    import argparse
    parser = argparse.ArgumentParser(description="Scan outline/settings files for unresolved placeholders")
    parser.add_argument("--project-root", required=True)
    parser.add_argument("--format", choices=["json", "text"], default="json")
    args_ns = parser.parse_args()

    results = scan_placeholders(args_ns.project_root)
    if args_ns.format == "json":
        print(json.dumps({"ok": not results, "placeholders": results}, ensure_ascii=False, indent=2))
        return
    if not results:
        print("OK no placeholders found")
        return
    for item in results:
        print(f"{item['file']}:{item['line']} {item['pattern']} {item['context']}")


if __name__ == "__main__":
    main()
