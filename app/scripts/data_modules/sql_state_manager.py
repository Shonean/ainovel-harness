#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Thin re-export wrapper — all logic moved to state_manager.py"""
from __future__ import annotations

from .state_manager import SQLStateManager, EntityData  # noqa: F401

__all__ = ["SQLStateManager", "EntityData", "main"]


def main():
    import argparse
    import sys
    from .cli_output import print_success, print_error
    from .cli_args import normalize_global_project_root, load_json_arg
    from .index_manager import IndexManager
    from .observability import safe_log_tool_call

    parser = argparse.ArgumentParser(description="SQL State Manager CLI (v5.4)")
    parser.add_argument("--project-root", type=str, help="项目根目录")

    subparsers = parser.add_subparsers(dest="command")

    subparsers.add_parser("stats")
    subparsers.add_parser("get-protagonist")
    subparsers.add_parser("get-core-entities")
    subparsers.add_parser("export-entities-v3")
    subparsers.add_parser("export-alias-index")

    process_parser = subparsers.add_parser("process-chapter")
    process_parser.add_argument("--chapter", type=int, required=True)
    process_parser.add_argument("--data", required=True, help="JSON 格式的章节数据")

    argv = normalize_global_project_root(sys.argv[1:])
    args = parser.parse_args(argv)

    config = None
    if args.project_root:
        from project_locator import resolve_project_root
        from .config import DataModulesConfig
        resolved_root = resolve_project_root(args.project_root)
        config = DataModulesConfig.from_project_root(resolved_root)

    manager = SQLStateManager(config)
    logger = IndexManager(config)
    tool_name = f"sql_state_manager:{args.command or 'unknown'}"

    def emit_success(data=None, message: str = "ok"):
        print_success(data, message=message)
        safe_log_tool_call(logger, tool_name=tool_name, success=True)

    def emit_error(code: str, message: str, suggestion: str | None = None):
        print_error(code, message, suggestion=suggestion)
        safe_log_tool_call(
            logger,
            tool_name=tool_name,
            success=False,
            error_code=code,
            error_message=message,
        )

    if args.command == "stats":
        stats = manager.get_stats()
        emit_success(stats, message="stats")
    elif args.command == "get-protagonist":
        protagonist = manager.get_protagonist()
        if protagonist:
            emit_success(protagonist, message="protagonist")
        else:
            emit_error("NOT_FOUND", "未设置主角")
    elif args.command == "get-core-entities":
        entities = manager.get_core_entities()
        emit_success(entities, message="core_entities")
    elif args.command == "export-entities-v3":
        data = manager.export_to_entities_v3_format()
        emit_success(data, message="entities_v3")
    elif args.command == "export-alias-index":
        data = manager.export_to_alias_index_format()
        emit_success(data, message="alias_index")
    elif args.command == "process-chapter":
        data = load_json_arg(args.data)
        stats = manager.process_chapter_entities(
            chapter=args.chapter,
            entities_appeared=data.get("entities_appeared", []),
            entities_new=data.get("entities_new", []),
            state_changes=data.get("state_changes", []),
            relationships_new=data.get("relationships_new", []),
        )
        emit_success(stats, message="chapter_processed")
    else:
        emit_error("UNKNOWN_COMMAND", "未指定有效命令", suggestion="请查看 --help")


if __name__ == "__main__":
    main()
