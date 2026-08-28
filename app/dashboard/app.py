"""
AInovel Harness - FastAPI 主应用

仅提供 GET 接口（严格只读）；所有文件读取经过 path_guard 防穿越校验。
"""

import asyncio
import json
import threading
import os
import re
import sqlite3
import sys
from datetime import datetime, timezone
from contextlib import asynccontextmanager, closing
from pathlib import Path
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field

# 启动时加载用户级全局 .env（~/.claude/ainovel-write/.env）。
# 不读 CWD、不读书目录 .env——API key 统一放用户级，杜绝跨书 .env 串台。
# 注意：data_modules 在 scripts/ 下，需先 _ensure_scripts_dir_on_path() 才能 import，
# 故实际加载放在 create_app() 内；此处仅留说明。

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response, StreamingResponse, FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles

from .core.path_guard import safe_resolve
from .core.project_guide import ProjectGuide
from .services.workflow_status import _find_chapter_md, list_chapters as _list_chapters
from .build.watcher import FileWatcher
from .build.auto_rebuild import AutoCodeWatcher
from .core.universal_logger import get_logger, create_request_logging_middleware

# ---------------------------------------------------------------------------
# 全局状态
# ---------------------------------------------------------------------------
_project_root: Path | None = None
_watcher = FileWatcher()
_auto_code_watcher = AutoCodeWatcher()

STATIC_DIR = Path(__file__).parent / "frontend" / "dist"


def _get_project_root() -> Path | None:
    """返回当前项目根目录，可能为 None（无项目选中）。"""
    return _project_root


def _require_project_root() -> Path:
    """需要项目上下文的端点调用此函数，无项目时返回 400 错误。"""
    if _project_root is None:
        raise HTTPException(status_code=400, detail="请先在主页选择一本书")
    return _project_root


def _default_workspace_root() -> Path:
    """新建书的工作区根（小说系统目录）：当前无选中书时的兜底定位。

    优先级：workspaces.json last_used 的父目录 → 本文件所在仓库上一级（小说系统目录）。
    修复 500：服务器重启后无当前书（current_root=None）时 `current_root.parent` 崩。
    """
    try:
        from data_modules.config import _get_user_claude_root
        reg = _get_user_claude_root() / "ainovel-write" / "workspaces.json"
        if reg.is_file():
            data = json.loads(reg.read_text(encoding="utf-8") or "{}")
            last = data.get("last_used_project_root") or ""
            if last:
                p = Path(last).resolve()
                if p.parent.is_dir():
                    return p.parent
    except Exception:
        pass
    return Path(__file__).resolve().parents[3]


def _restore_last_project_root() -> Path | None:
    """服务器重启后自动恢复上次的当前书（best-effort）。

    否则每次重启（含「一键应用新代码」rebuild-and-restart）当前书都会丢，
    测试书工作台/创作端点 400 需要重新选书。
    """
    try:
        # 注意：本函数在 _ensure_scripts_dir_on_path() 之前调用，不能 import data_modules
        #（scripts/ 还不在 sys.path）。直接拼 Path.home()/.claude，与 _get_user_claude_root()
        # 的默认返回值一致。
        reg = Path.home() / ".claude" / "ainovel-write" / "workspaces.json"
        if reg.is_file():
            data = json.loads(reg.read_text(encoding="utf-8") or "{}")
            last = data.get("last_used_project_root") or ""
            if last:
                p = Path(last).resolve()
                if (p / ".ainovel" / "state.json").is_file():
                    return p
    except Exception:
        pass
    return None


def _ainovel_dir() -> Path:
    return _require_project_root() / ".ainovel"


def _story_system_dir() -> Path:
    return _require_project_root() / ".story-system"


def _build_story_runtime_health_report(project_root: Path) -> dict:
    from data_modules.story_runtime_health import build_story_runtime_health

    return build_story_runtime_health(project_root)


def _ensure_scripts_dir_on_path() -> None:
    scripts_dir = Path(__file__).resolve().parents[1] / "scripts"
    scripts_entry = str(scripts_dir)
    if scripts_entry not in sys.path:
        sys.path.insert(0, scripts_entry)


def _load_state_payload(*, required: bool = False) -> dict:
    root = _get_project_root()
    if root is None:
        if required:
            raise HTTPException(400, "请先选择一本书")
        return {}
    state_path = root / ".ainovel" / "state.json"
    if not state_path.is_file():
        if required:
            raise HTTPException(404, "state.json 不存在")
        return {}

    try:
        payload = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=500, detail=f"state.json 读取失败: {exc}") from exc

    return payload if isinstance(payload, dict) else {}


def _gather_chapter_data(project_root: Path, state: dict) -> list[dict]:
    """收集所有章节数据用于导出。"""
    chapters = []
    outlines_dir = project_root / "大纲"
    ai_dir = project_root / "AI生成"
    tmp_dir = project_root / ".ainovel" / "tmp"
    story_chapters_dir = project_root / ".story-system" / "chapters"
    story_commits_dir = project_root / ".story-system" / "commits"

    # 从 progress 获取已规划的章范围
    progress = state.get("progress", {})
    chapters_planned = progress.get("chapters_planned", [])
    max_ch = 0
    for entry in chapters_planned:
        rng = str(entry.get("chapters_range", ""))
        if "-" in rng:
            try:
                end = int(rng.split("-")[1].strip())
                if end > max_ch:
                    max_ch = end
            except ValueError:
                pass

    for ch in range(1, max(max_ch, 100) + 1):
        ch4 = f"{ch:04d}"
        entry = {"chapter": ch}

        # 章纲
        outline_files = list(outlines_dir.glob(f"第{ch4}章-章纲.md")) + \
                        list(outlines_dir.glob(f"第{ch}章-章纲.md"))
        if outline_files:
            try:
                entry["outline"] = outline_files[0].read_text(encoding="utf-8")
            except Exception:
                entry["outline"] = None

        # 正文
        draft_files = list(ai_dir.glob(f"第{ch4}章.md")) + list(ai_dir.glob(f"第{ch}章.md"))
        if draft_files:
            try:
                entry["draft"] = draft_files[0].read_text(encoding="utf-8")
            except Exception:
                entry["draft"] = None

        # 章纲合约
        contract = story_chapters_dir / f"chapter_{ch}.json"
        if contract.is_file():
            try:
                entry["chapter_contract"] = json.loads(contract.read_text(encoding="utf-8"))
            except Exception:
                entry["chapter_contract"] = None

        # 提交记录
        commit = story_commits_dir / f"chapter_{ch}.commit.json"
        if commit.is_file():
            try:
                entry["commit"] = json.loads(commit.read_text(encoding="utf-8"))
            except Exception:
                entry["commit"] = None

        # 审查/审计结果
        ch_tmp = tmp_dir / f"chapter_{ch4}"
        if ch_tmp.is_dir():
            for audit_name in ["review_results", "constraint_audit", "final_audit",
                               "extraction_result", "fulfillment_result", "disambiguation_result"]:
                af = ch_tmp / f"{audit_name}.json"
                if af.is_file():
                    try:
                        entry[audit_name] = json.loads(af.read_text(encoding="utf-8"))
                    except Exception:
                        entry[audit_name] = None

        # 只要有任何数据就加入
        if len(entry) > 1:
            chapters.append(entry)

    return chapters


def _parse_json_value(raw: object, default):
    if raw is None:
        return default
    if isinstance(raw, (dict, list)):
        return raw
    if not isinstance(raw, str):
        return default
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return default


def _resolve_volume_for_chapter(state: dict, chapter: int) -> int | None:
    progress = state.get("progress") if isinstance(state, dict) else {}
    if not isinstance(progress, dict):
        return None
    volumes_planned = progress.get("volumes_planned")
    if not isinstance(volumes_planned, list):
        return None

    best: tuple[int, int] | None = None
    for item in volumes_planned:
        if not isinstance(item, dict):
            continue
        volume = item.get("volume")
        if not isinstance(volume, int) or volume <= 0:
            continue
        chapter_range = str(item.get("chapters_range") or "").strip()
        if "-" not in chapter_range:
            continue
        left, _, right = chapter_range.partition("-")
        try:
            start = int(left.strip())
            end = int(right.strip())
        except ValueError:
            continue
        if start <= 0 or end <= 0 or start > end:
            continue
        if start <= chapter <= end:
            candidate = (start, volume)
            if best is None or candidate[0] > best[0] or (
                candidate[0] == best[0] and candidate[1] < best[1]
            ):
                best = candidate
    return best[1] if best else None


def _build_strand_map(state: dict) -> dict[int, str]:
    tracker = state.get("strand_tracker") if isinstance(state, dict) else {}
    history = tracker.get("history") if isinstance(tracker, dict) else []
    if not isinstance(history, list):
        return {}

    strand_map: dict[int, str] = {}
    for index, entry in enumerate(history, start=1):
        if not isinstance(entry, dict):
            continue
        chapter_value = entry.get("chapter", index)
        try:
            chapter = int(chapter_value)
        except (TypeError, ValueError):
            chapter = index
        strand = str(entry.get("strand") or entry.get("dominant") or "").strip().lower()
        if chapter > 0 and strand:
            strand_map[chapter] = strand
    return strand_map


def _extract_story_chapter(path: Path) -> int:
    stem = path.stem
    if "_" not in stem:
        return 0
    _, _, tail = stem.partition("_")
    try:
        return int(tail.split(".")[0])
    except ValueError:
        return 0


def _inspect_vector_db(project_root: Path) -> dict:
    from data_modules.config import DataModulesConfig

    cfg = DataModulesConfig.from_project_root(project_root)
    vector_db = cfg.vector_db
    exists = vector_db.is_file()
    size_bytes = vector_db.stat().st_size if exists else 0
    record_count = 0
    error = ""

    if exists and size_bytes > 0:
        try:
            with sqlite3.connect(str(vector_db)) as conn:
                cursor = conn.cursor()
                table_exists = cursor.execute(
                    "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'vectors'"
                ).fetchone()
                if table_exists:
                    row = cursor.execute("SELECT COUNT(*) FROM vectors").fetchone()
                    record_count = int(row[0] or 0) if row else 0
        except sqlite3.Error as exc:
            error = str(exc)

    return {
        "path": str(vector_db),
        "exists": exists,
        "size_bytes": size_bytes,
        "record_count": record_count,
        "error": error,
    }


def _user_env_path_safe() -> Path:
    """用户级 .env 路径（容错：config 不可用时回退默认）。"""
    try:
        from data_modules.config import _user_env_path
        return _user_env_path()
    except Exception:
        return Path.home() / ".claude" / "ainovel-write" / ".env"


def _build_env_status(project_root: Path) -> dict:
    from data_modules.config import DataModulesConfig

    cfg = DataModulesConfig.from_project_root(project_root)
    vector_info = _inspect_vector_db(project_root)

    embed_ready = bool(str(cfg.embed_api_key or "").strip())
    rerank_ready = bool(str(cfg.rerank_api_key or "").strip())
    vector_ready = bool(vector_info["exists"] and vector_info["size_bytes"] > 0)

    if vector_ready and embed_ready and rerank_ready:
        rag_mode = "full"
    elif vector_ready and embed_ready:
        rag_mode = "embed_only"
    else:
        rag_mode = "bm25_only"

    return {
        "embed": {
            "base_url": cfg.embed_base_url,
            "model": cfg.embed_model,
            "api_key_present": embed_ready,
        },
        "rerank": {
            "base_url": cfg.rerank_base_url,
            "model": cfg.rerank_model,
            "api_key_present": rerank_ready,
        },
        "vector_db": vector_info,
        "rag_mode": rag_mode,
    }


# ---------------------------------------------------------------------------
# 应用工厂
# ---------------------------------------------------------------------------

def create_app(project_root: str | Path | None = None) -> FastAPI:
    global _project_root

    if project_root:
        _project_root = Path(project_root).resolve()
    elif _project_root is None:
        # 重启后自动恢复上次的当前书（best-effort，不影响显式传参）
        _project_root = _restore_last_project_root()

    _ensure_scripts_dir_on_path()

    # 加载用户级全局 .env（API key 单一来源，不依赖 CWD/书目录）。
    # Phase 6: 强制重载，避免 import 链提前触发导致幂等标志阻塞。
    from data_modules.config import load_user_env, reset_user_env_loaded

    reset_user_env_loaded()
    load_user_env(force=True)

    # v5.22.2：api_library.json 是唯一 API 来源，应用当前预设 + 向量配置覆盖 .env 旧值
    from .routes.api_library import apply_current_to_env

    apply_current_to_env()

    # 启动时验证关键配置
    _key = os.environ.get("ARK_API_KEY") or os.environ.get("ANTHROPIC_API_KEY") or ""
    _pro = os.environ.get("ARK_MODEL_PRO") or os.environ.get("ANTHROPIC_MODEL") or ""
    _char = os.environ.get("ARK_MODEL_CHARACTER") or ""
    _base = os.environ.get("ARK_BASE_URL") or os.environ.get("ANTHROPIC_BASE_URL") or ""
    _base_pro = os.environ.get("ARK_BASE_URL_PRO") or _base
    _base_char = os.environ.get("ARK_BASE_URL_CHARACTER") or _base
    if _key.strip():
        import logging
        logging.getLogger("dashboard.app").info("API key loaded (%d chars), pro=%s, char=%s", len(_key), _pro, _char or 'N/A')
    else:
        import logging
        logging.getLogger("dashboard.app").warning("ARK_API_KEY not found in .env or environment!")

    # 重建 watcher：同进程内重复 create_app（如测试）时，丢弃上一本书的订阅队列，
    # 保证「干净切换」——新书不残留旧书的文件监听状态。
    global _watcher
    _watcher = FileWatcher()

    @asynccontextmanager
    async def _lifespan(_: FastAPI):
        root = _get_project_root()
        if root is not None:
            ainovel_dir = root / ".ainovel"
            story_system = root / ".story-system"
            if ainovel_dir.is_dir() or story_system.is_dir():
                _watcher.start(
                    watch_ainovel_dir=ainovel_dir if ainovel_dir.is_dir() else None,
                    watch_story_system_dir=story_system if story_system.is_dir() else None,
                    loop=asyncio.get_running_loop(),
                )
        # 自动代码重载：监视 app/ + prompt_harness/ + frontend/src/ 变化自动 rebuild
        _auto_code_watcher.start()
        try:
            yield
        finally:
            _auto_code_watcher.stop()
            _watcher.stop()

    app = FastAPI(title="AInovel Harness", version="0.1.0", lifespan=_lifespan)

    app.add_middleware(
        CORSMiddleware,
        # 【2026-08-23 收紧】不再全开 *：仅本机开发前端 + Tauri 壳（过渡期保留）+
        # VS Code webview 动态来源（正则）。扩展宿主经 REST 调用不带浏览器 CORS 限制。
        allow_origins=[
            "http://localhost:5173",
            "http://127.0.0.1:5173",
            "tauri://localhost",
            "http://tauri.localhost",
            "https://tauri.localhost",
        ],
        allow_origin_regex=r"^vscode-webview://|^https://[a-z0-9-]+\.vscode-cdn\.net$",
        allow_methods=["GET", "POST"],
        allow_headers=["*"],
    )

    # ===== 统一日志系统：请求日志中间件 =====
    app.middleware("http")(create_request_logging_middleware())
    _ulog = get_logger()
    _ulog.log_system_start(component="main_app")

    @app.on_event("shutdown")
    async def _ulog_shutdown():
        _ulog.log_system_shutdown(component="main_app", reason="shutdown")
        _ulog.close()

    # ===== Phase A1：动作路由（POST）+ 任务流（SSE）=====
    from .routes.actions import create_router as _create_actions_router, create_tasks_router
    from .services.workflow_status import create_router as _create_workflow_router
    app.include_router(_create_actions_router(_get_project_root))
    app.include_router(create_tasks_router())
    app.include_router(_create_workflow_router(_get_project_root))

    # ===== Phase A2：LLM agent 路由 + 工作流编排 =====
    try:
        from .agents.agents import create_agents_router
        from .workflows import create_workflows_router, start_init_task, _InitBody as _WorkflowsInitBody  # now a package
        app.include_router(create_agents_router(_get_project_root))
        app.include_router(create_workflows_router(_get_project_root))
    except ImportError as exc:
        # openai SDK 未装时优雅降级
        import logging
        logging.getLogger("dashboard.app").warning("Phase A2 路由未挂载（openai SDK 缺失？）: %s", exc)
        start_init_task = None  # type: ignore
        _WorkflowsInitBody = None  # type: ignore

    # ===== Prompt Harness 路由（prompt 优化训练器）=====
    _ph_src = str(Path(__file__).resolve().parent.parent.parent / "prompt-harness")
    _ph_data_dir = Path(os.environ.get("AINOVEL_WRITE_CONFIG", Path.home() / ".claude" / "ainovel-write")) / "prompt-harness"

    try:
        if _ph_src not in sys.path:
            sys.path.insert(0, _ph_src)
        from prompt_harness.server import (
            router as ph_router,
            init_prompt_harness,
            shutdown_prompt_harness,
        )
        _ph_corpus_dir = Path(_ph_src) / "corpus"
        init_prompt_harness(_ph_data_dir, corpus_dir=_ph_corpus_dir)
        app.include_router(ph_router)

        # ===== VS Code 扩展 WS JSON-RPC 服务层（协议优先分层，GUI设计参考.md §2）=====
        # 生成一次性 token 落盘（扩展宿主读取握手）；挂 /api/appserver/ws
        try:
            from prompt_harness.appserver import (
                router as _appserver_router,
                ensure_appserver_token,
            )
            ensure_appserver_token()
            app.include_router(_appserver_router)
        except Exception as _appserver_exc:  # noqa: BLE001 —— 扩展层失败不拖垮主系统
            import logging
            logging.getLogger("dashboard.app").warning("appserver 未挂载: %s", _appserver_exc)

        # 桥接：prompt-harness 的 LLM 日志同时写入全局 UniversalLogger
        try:
            from prompt_harness.run_logger import RunLogger
            from .core.universal_logger import bridge_run_logger_llm
            # 给 RunLogger 类打猴子补丁，让所有实例的 log_llm_call 都镜像到全局
            _original_run_logger_init = RunLogger.__init__

            def _patched_run_logger_init(self, *args, **kwargs):
                _original_run_logger_init(self, *args, **kwargs)
                bridge_run_logger_llm(self)

            RunLogger.__init__ = _patched_run_logger_init
        except Exception:
            pass

        @app.on_event("shutdown")
        async def _ph_shutdown():
            await shutdown_prompt_harness()

        import logging
        logging.getLogger("dashboard.app").info("Prompt Harness 路由已挂载")
    except ImportError as exc:
        import logging
        logging.getLogger("dashboard.app").warning("Prompt Harness 路由未挂载: %s", exc)

    # ===========================================================
    # API：项目元信息
    # ===========================================================

    @app.get("/api/project/info")
    def project_info():
        """返回 state.json 完整内容（只读）。"""
        return _load_state_payload(required=True)

    @app.get("/api/project/current")
    def project_current():
        """返回当前选中书的根目录与元信息（AI 创作工作台取 book_root 用）。"""
        root = _get_project_root()
        if root is None:
            return {"project_root": None, "test_book": False, "title": ""}
        try:
            state = _load_state_payload()
            title = ((state.get("project_info") or {}).get("title") or "").strip()
        except HTTPException:
            title = ""
        return {
            "project_root": str(root.resolve()),
            "test_book": _is_test_book(),
            "title": title or root.name,
        }

    @app.get("/api/project/next-step")
    def project_next_step():
        """根据项目文件状态返回下一步建议。"""
        root = _get_project_root()
        if root is None:
            return {"step": "select_book", "message": "请选择一本书开始创作"}
        return ProjectGuide(root).next_step()

    @app.get("/api/project/checklist")
    def project_checklist():
        """返回项目前置条件检查清单。"""
        project_root = _get_project_root()
        env = _build_env_status(project_root)
        runtime = _build_story_runtime_health_report(project_root)
        state = _load_state_payload()
        has_outline = (project_root / "大纲" / "总纲.md").is_file()
        chapters = _list_chapters(project_root)
        has_chapters = bool(chapters)
        has_draft = bool(any(_find_chapter_md(project_root, c["chapter"]) for c in chapters))

        return {
            "ok": bool(runtime.get("mainline_ready")),
            "items": [
                {"name": "env_keys", "ok": bool(env["embed"]["api_key_present"]), "label": "Embedding API Key"},
                {"name": "rerank_key", "ok": bool(env["rerank"]["api_key_present"]), "label": "Rerank API Key"},
                {"name": "vector_db", "ok": bool(env["vector_db"]["exists"] and env["vector_db"]["size_bytes"] > 0), "label": "向量库"},
                {"name": "story_runtime", "ok": bool(runtime.get("mainline_ready")), "label": "Story Runtime"},
                {"name": "outline", "ok": has_outline, "label": "总纲"},
                {"name": "chapters", "ok": has_chapters, "label": "章纲"},
                {"name": "draft", "ok": has_draft, "label": "已起草章节"},
            ],
        }

    @app.get("/api/project/init-data")
    def project_init_data():
        """返回当前书已有的初始化字段，用于预填 InitProjectPage 向导。"""
        state = _load_state_payload() or {}
        info = state.get("project_info") or {}
        ps = state.get("protagonist_state") or {}

        def _str(value):
            return str(value) if value is not None else ""

        def _int(value):
            try:
                return int(value) if value is not None else 0
            except (TypeError, ValueError):
                return 0

        def _list_str(value):
            if isinstance(value, list):
                return [str(v) for v in value]
            if isinstance(value, str):
                return [line.strip() for line in value.splitlines() if line.strip()]
            return []

        return {
            "project": {
                "title": _str(info.get("title")),
                "genre": _str(info.get("genre")),
                "target_words": _int(info.get("target_words")),
                "target_chapters": _int(info.get("target_chapters")),
                "one_liner": _str(info.get("core_selling_points")),
                "core_conflict": _str(info.get("core_conflict")),
                "target_reader": _str(info.get("target_reader")),
                "platform": _str(info.get("platform")),
            },
            "protagonist": {
                "name": _str(info.get("protagonist_name") or ps.get("name")),
                "desire": _str(info.get("protagonist_desire")),
                "flaw": _str(info.get("protagonist_flaw")),
                "archetype": _str(info.get("protagonist_archetype")),
                "structure": _str(info.get("protagonist_structure")),
            },
            "relationship": {
                "heroine_config": _str(info.get("heroine_config")),
                "antagonist_tiers": _str(info.get("antagonist_tiers")),
                "antagonist_mirror": _str(info.get("antagonist_mirror")),
            },
            "golden_finger": {
                "type": _str(info.get("golden_finger_type")),
                "name": _str(info.get("golden_finger_name")),
                "style": _str(info.get("golden_finger_style")),
                "visibility": _str(info.get("gf_visibility")),
                "irreversible_cost": _str(info.get("gf_irreversible_cost")),
                "growth_rhythm": "",
            },
            "world": {
                "scale": _str(info.get("world_scale")),
                "factions": _str(info.get("factions")),
                "power_system_type": _str(info.get("power_system_type")),
                "social_class": _str(info.get("social_class")),
            },
            "constraints": {
                "anti_trope": _str(info.get("anti_trope")),
                "hard_constraints": _list_str(info.get("hard_constraints")),
                "opening_hook": _str(info.get("opening_hook")),
            },
        }

    # ===========================================================
    # API：通道配置（Phase 6）—— 读/写当前书的通道配置，列出可用通道
    # ===========================================================

    @app.get("/api/channel/profiles")
    def channel_profiles():
        """列出 channels/ 目录下所有可用通道文件。"""
        try:
            from .workflows._channel import list_channel_profiles
            return {"profiles": list_channel_profiles()}
        except Exception as exc:
            raise HTTPException(500, f"读取通道列表失败: {exc}")

    @app.get("/api/channel/config")
    def channel_config_get():
        """读取当前书 state.json 中的 channel 配置。"""
        state = _load_state_payload() or {}
        channel = state.get("channel")
        if not channel:
            return {
                "channel": {
                    "profile": "default",
                    "model_override": None,
                    "opening_count": 3,
                    "overrides": {
                        "identity": "",
                        "craft_rules": "",
                        "opening_rules": "",
                        "review_criteria": "",
                    },
                },
            }
        return {"channel": channel}

    @app.post("/api/channel/config")
    def channel_config_post(body: dict):
        """写入当前书 state.json 中的 channel 配置。"""
        channel_data = body.get("channel")
        if channel_data is None:
            raise HTTPException(400, "missing 'channel' field in request body")

        state_path = _ainovel_dir() / "state.json"
        if not state_path.is_file():
            raise HTTPException(404, "state.json 不存在，请先初始化项目")

        try:
            state = json.loads(state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise HTTPException(500, f"state.json 读取失败: {exc}")

        state["channel"] = channel_data

        # 原子写入
        tmp_path = state_path.with_suffix(".json.tmp")
        try:
            tmp_path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
            os.replace(tmp_path, state_path)
        except OSError as exc:
            raise HTTPException(500, f"state.json 写入失败: {exc}")

        # 通知前端（通过 watcher 机制）
        _watcher._mark_changed("state.json")

        return {"ok": True}

    # ===========================================================
    # API：多书管理——列书 + 运行时切书（单实例内切换 _project_root）
    # ===========================================================

    def _is_book_dir(path: Path) -> bool:
        return (path / ".ainovel" / "state.json").is_file()

    def _is_partial_book_dir(path: Path) -> bool:
        """有 .ainovel 目录但无 state.json（init 失败残留）。"""
        return (path / ".ainovel").is_dir() and not (path / ".ainovel" / "state.json").is_file()

    def _book_stats(path: Path) -> dict:
        """读 .ainovel/ 给一本书补统计字段（首页书架信息密度用）。容错：缺失→null/0。"""
        st = {"arcs": 0, "chapters": 0, "avg_score": None, "polluted": 0,
              "words": 0, "genre": None, "one_liner": None, "updated": 0}
        ain = path / ".ainovel"
        try:
            # 最近修改时间：.ainovel 目录下所有文件的最大 mtime
            latest = 0
            for f in ain.rglob("*"):
                if f.is_file():
                    latest = max(latest, f.stat().st_mtime)
            st["updated"] = int(latest * 1000)
        except Exception:
            pass
        # 剧情弧统计（弧数/章节/评分/污染/字数）
        try:
            af = ain / "arcs.json"
            if af.is_file():
                data = json.loads(af.read_text(encoding="utf-8"))
                arcs = data.get("arcs") if isinstance(data, dict) else data
                if isinstance(arcs, list):
                    st["arcs"] = len(arcs)
                    chs = [c for a in arcs if isinstance(a, dict)
                           for c in (a.get("chapters") or []) if isinstance(c, dict)]
                    st["chapters"] = len(chs)
                    scored = [c["overall"] for c in chs if c.get("overall") is not None]
                    if scored:
                        st["avg_score"] = round(sum(scored) / len(scored), 3)
                    st["polluted"] = sum(1 for c in chs if c.get("polluted"))
                    st["words"] = sum(len(c.get("text") or "") for c in chs)
        except Exception:
            pass
        # 题材 / 一句话简介
        try:
            bf = ain / "basic_settings.json"
            if bf.is_file():
                bs = json.loads(bf.read_text(encoding="utf-8"))
                if isinstance(bs, dict):
                    st["genre"] = bs.get("genre") or None
                    st["one_liner"] = bs.get("one_liner") or None
        except Exception:
            pass
        return st

    def _list_projects() -> list[dict]:
        """列出可管理的书：workspaces.json 注册项 + 当前 root 同级目录扫描，去重。"""
        from data_modules.config import _get_user_claude_root

        def _read_test_book(path: Path) -> bool:
            try:
                sp = path / ".ainovel" / "state.json"
                if sp.is_file():
                    return json.loads(sp.read_text(encoding="utf-8")).get("test_book", False)
            except Exception:
                pass
            return False

        books: dict[str, dict] = {}  # project_root -> info

        # 1) workspaces.json 注册表
        try:
            reg_path = _get_user_claude_root() / "ainovel-write" / "workspaces.json"
            if reg_path.is_file():
                data = json.loads(reg_path.read_text(encoding="utf-8") or "{}")
                for ws in (data.get("workspaces") or {}).values():
                    pr = ws.get("current_project_root") or ws.get("last_used") or ""
                    if pr and _is_book_dir(Path(pr)):
                        rp = str(Path(pr).resolve())
                        books.setdefault(rp, {
                            "name": Path(pr).name, "project_root": rp,
                            "test_book": _read_test_book(Path(pr)),
                        })
                last = data.get("last_used_project_root") or ""
                if last and _is_book_dir(Path(last)):
                    rp = str(Path(last).resolve())
                    books.setdefault(rp, {
                        "name": Path(last).name, "project_root": rp,
                        "test_book": _read_test_book(Path(last)),
                    })
        except Exception:
            pass

        # 2) 同级目录扫描（覆盖未注册的新书 + init 失败残留）
        cur = _get_project_root()
        # 确定扫描的基础目录
        scan_dirs = []
        if cur is not None:
            scan_dirs.append(cur.parent)
        else:
            # 无选中项目时，向上查找"小说系统"目录
            search = Path.cwd()
            for _ in range(6):
                novel_base = search / "小说系统"
                if novel_base.is_dir():
                    scan_dirs.append(novel_base)
                    break
                if search.name == "小说系统":
                    scan_dirs.append(search)
                    break
                search = search.parent

        for scan_dir in scan_dirs:
            for sib in scan_dir.iterdir():
                if sib.is_dir():
                    if _is_book_dir(sib):
                        rp = str(sib.resolve())
                        books.setdefault(rp, {
                            "name": sib.name, "project_root": rp,
                            "test_book": _read_test_book(sib),
                        })
                    elif _is_partial_book_dir(sib):
                        rp = str(sib.resolve())
                        books.setdefault(rp, {"name": sib.name, "project_root": rp, "incomplete": True})

        # 标记当前选中
        result = list(books.values())
        for b in result:
            b.setdefault("incomplete", False)
            b.setdefault("test_book", False)
            if cur is not None:
                b["active"] = (b["project_root"] == str(cur.resolve())) and not b["incomplete"]
            else:
                b["active"] = False
            # 完整书补充统计字段（首页书架信息密度）
            if not b.get("incomplete"):
                b.update(_book_stats(Path(b["project_root"])))
        # 排序：完整书优先，init 失败残留靠后
        result.sort(key=lambda b: (b.get("incomplete", False), not b.get("active", False)))
        return result

    @app.get("/api/projects")
    def list_projects():
        return {"projects": _list_projects()}

    class _SwitchBody(BaseModel):
        project_root: str

    @app.post("/api/project/switch")
    async def switch_project(body: _SwitchBody):
        """运行时切换管理的书：改 _project_root + 重置 _watcher（单实例内切多书）。"""
        new_root = Path(body.project_root).resolve()
        if not _is_book_dir(new_root):
            raise HTTPException(400, f"不是有效的书目录（缺 .ainovel/state.json）：{new_root}")

        global _project_root, _watcher
        _project_root = new_root

        _watcher.stop()
        _watcher = FileWatcher()
        ainovel_dir = new_root / ".ainovel"
        story_system = new_root / ".story-system"
        if ainovel_dir.is_dir() or story_system.is_dir():
            _watcher.start(
                watch_ainovel_dir=ainovel_dir if ainovel_dir.is_dir() else None,
                watch_story_system_dir=story_system if story_system.is_dir() else None,
                loop=asyncio.get_running_loop(),
            )
        return {"ok": True, "project_root": str(new_root), "name": new_root.name}

    class _RegisterBody(BaseModel):
        project_root: str

    @app.post("/api/project/register")
    async def register_project(body: _RegisterBody):
        """把已创建但未登记的（测试书）目录入库：校验书目录 + 切为当前书 + 写指针/registry。

        AI 创作模块新建测试书走「创建（裸目录）→ init（设定集+elements）→ 入库」三步，
        入库才登记进书库（主页列表可见、成为当前书）。
        """
        new_root = Path(body.project_root).resolve()
        if not _is_book_dir(new_root):
            raise HTTPException(400, f"不是有效的书目录（缺 .ainovel/state.json）：{new_root}")

        global _project_root, _watcher
        _project_root = new_root
        _watcher.stop()
        _watcher = FileWatcher()
        ainovel_dir = new_root / ".ainovel"
        story_system = new_root / ".story-system"
        if ainovel_dir.is_dir() or story_system.is_dir():
            _watcher.start(
                watch_ainovel_dir=ainovel_dir if ainovel_dir.is_dir() else None,
                watch_story_system_dir=story_system if story_system.is_dir() else None,
                loop=asyncio.get_running_loop(),
            )
        _write_project_pointer(new_root)
        _update_registry_current_project(new_root)
        return {"ok": True, "project_root": str(new_root), "name": new_root.name}

    class _DeleteProjectBody(BaseModel):
        project_root: str

    @app.post("/api/project/delete")
    async def delete_project(body: _DeleteProjectBody):
        """永久删除一本书：物理删除目录 + 从 workspaces.json 摘除 + 若是当前书则复位。

        危险操作，前端必须以「输入书名」强确认后调用。删除当前书后 _project_root 置空，
        由重启恢复逻辑（state.json 不存在则不复位）自然落空。
        """
        import shutil

        target = Path(body.project_root).resolve()
        if not target.is_dir():
            raise HTTPException(404, f"书目录不存在：{target}")
        if not (_is_book_dir(target) or _is_partial_book_dir(target)):
            raise HTTPException(400, f"不是有效的书目录（缺 .ainovel/state.json）：{target}")

        name = target.name

        global _project_root, _watcher
        was_active = _project_root is not None and target.resolve() == Path(_project_root).resolve()
        if was_active:
            _watcher.stop()
            _watcher = FileWatcher()
            _project_root = None

        _remove_from_registry(target)

        # 复位指向被删书的指针文件（若是当前书）
        if was_active:
            try:
                pointer = target.parent / ".claude" / ".ainovel-current-project"
                if pointer.is_file():
                    cur = pointer.read_text(encoding="utf-8").strip()
                    if cur and Path(cur).resolve() == target:
                        pointer.unlink(missing_ok=True)
            except Exception:
                pass

        # 物理删除（最后一步，成功即不可逆）
        shutil.rmtree(target)

        return {"ok": True, "name": name, "deleted": True}

    # ------------------------------------------------------------------
    # 新建书（create + init 一体化）
    # ------------------------------------------------------------------

    _PROJECT_NAME_RE = re.compile(r"[\\/:*?\"<>|]")

    def _slugify_project_name(name: str) -> str:
        """把书名转成可用作文件夹名的 slug：保留中文/字母/数字，空格转下划线。"""
        name = name.strip()
        name = re.sub(r"\s+", "_", name)
        name = _PROJECT_NAME_RE.sub("", name)
        name = name.strip("._")
        if not name:
            raise HTTPException(400, "书名无效")
        return name

    def _write_project_pointer(project_root: Path) -> None:
        """把工作区指针指向新书（best-effort，不阻断主流程）。"""
        try:
            workspace_root = _get_project_root().parent
            pointer = workspace_root / ".claude" / ".ainovel-current-project"
            pointer.parent.mkdir(parents=True, exist_ok=True)
            pointer.write_text(str(project_root.resolve()), encoding="utf-8")
        except Exception:
            pass

    def _update_registry_current_project(project_root: Path) -> None:
        """更新用户级 workspaces.json（best-effort）。"""
        try:
            from data_modules.config import _get_user_claude_root

            reg_path = _get_user_claude_root() / "ainovel-write" / "workspaces.json"
            reg_path.parent.mkdir(parents=True, exist_ok=True)
            data = {}
            if reg_path.is_file():
                try:
                    data = json.loads(reg_path.read_text(encoding="utf-8") or "{}")
                except (OSError, json.JSONDecodeError):
                    data = {}
            if not isinstance(data, dict):
                data = {}
            if not isinstance(data.get("workspaces"), dict):
                data["workspaces"] = {}

            workspace_root = str(_get_project_root().parent.resolve())
            data["workspaces"][workspace_root] = {
                "current_project_root": str(project_root.resolve()),
                "last_used": str(project_root.resolve()),
            }
            data["last_used_project_root"] = str(project_root.resolve())
            data["schema_version"] = 1
            data["updated_at"] = datetime.now().isoformat(timespec="seconds")
            reg_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass

    def _remove_from_registry(project_root: Path) -> None:
        """把书从用户级 workspaces.json 摘除（best-effort）：清掉各工作区指向它的 current/last_used。"""
        try:
            from data_modules.config import _get_user_claude_root

            reg_path = _get_user_claude_root() / "ainovel-write" / "workspaces.json"
            if not reg_path.is_file():
                return
            data = json.loads(reg_path.read_text(encoding="utf-8") or "{}")
            if not isinstance(data, dict):
                return
            rp = str(project_root.resolve())
            changed = False
            ws = data.get("workspaces")
            if isinstance(ws, dict):
                for entry in ws.values():
                    if not isinstance(entry, dict):
                        continue
                    for key in ("current_project_root", "last_used"):
                        val = entry.get(key)
                        if val and Path(val).resolve() == Path(rp).resolve():
                            entry[key] = ""
                            changed = True
            last = data.get("last_used_project_root")
            if last and Path(last).resolve() == Path(rp).resolve():
                data["last_used_project_root"] = ""
                changed = True
            if changed:
                data["updated_at"] = datetime.now().isoformat(timespec="seconds")
                reg_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass

    class _CreateProjectBody(BaseModel):
        name: str
        brief: dict = {}
        reference_text_path: str | None = None
        model: str | None = None
        test_book: bool = False

    @app.post("/api/project/create")
    async def create_project(body: _CreateProjectBody):
        r"""在当前书同级的 `小说系统\` 下新建一本书，切到它并启动 init。

        普通书 → 启动 ainovel-init skill（agent 补问 + 生成设定）；
        测试书（body.test_book）→ 只建裸结构 + 切书，**不跑 skill**——
        由 AI 创作模块 `/api/prompt-harness/ai-creation/init`（手写 brief → 设定集 + elements.json）初始化。
        """
        is_test = bool(body.test_book)

        if start_init_task is None and not is_test:
            raise HTTPException(503, "LLM agent 路由未挂载，无法初始化新书")

        current_root = _get_project_root()
        workspace_root = current_root.parent if current_root is not None else _default_workspace_root()
        slug = _slugify_project_name(body.name)
        new_root = (workspace_root / slug).resolve()

        if new_root.exists():
            raise HTTPException(
                409,
                f"目录已存在：{new_root.name}。请换书名或先删除/重命名该目录。",
            )

        # 创建最小目录结构
        for subdir in (
            ".ainovel",
            ".ainovel/archive",
            ".ainovel/backups",
            ".ainovel/summaries",
            ".story-system",
            "大纲",
            "设定集",
            "正文",
            "审查报告",
            "参考文章示例",
        ):
            (new_root / subdir).mkdir(parents=True, exist_ok=True)

        # 写最小占位文件（init 会覆盖/补充）
        try:
            (new_root / ".env.example").write_text(
                "ARK_API_KEY=ark-\nARK_BASE_URL=https://ark.cn-beijing.volces.com/api/coding/v3\n"
                "ARK_MODEL_PRO=doubao-1.5-pro-32k\n",
                encoding="utf-8",
            )
            (new_root / ".gitignore").write_text(
                ".ainovel/\n.story-system/\n.env\n", encoding="utf-8"
            )
        except OSError:
            pass

        # 写最小 state.json（包含 test_book 标记，init 会填充剩余字段）
        _atomic_write_json(new_root / ".ainovel" / "state.json", {
            "schema_version": "5.0",
            "test_book": body.test_book,
            "project_info": {"title": body.name},
        })

        # 测试书：复制全局 prompt 到本地副本，实现 prompt 隔离
        if body.test_book:
            try:
                from .workflows._prompts import init_test_book_prompts
                init_test_book_prompts(new_root)
            except Exception:
                pass  # prompt 复制失败不阻断创建流程

        # 【AI 创作模块】测试书：只建裸目录，**不切换当前书、不登记入库**——
        # 由独立新建页创建后，前端调 /api/prompt-harness/ai-creation/init 生成设定集+元素清单，
        # 再点「入库」（POST /api/project/register）才登记进书库。
        if is_test:
            return {
                "ok": True,
                "project_root": str(new_root),
                "name": new_root.name,
                "task_id": None,
                "test_book": True,
            }

        # 普通书：切后端 root 到新书 + 登记 + 启动 init skill
        global _project_root, _watcher
        _project_root = new_root
        _watcher.stop()
        _watcher = FileWatcher()

        # 更新工作区指针与 registry（让下次启动/其他工具能找到新书）
        _write_project_pointer(new_root)
        _update_registry_current_project(new_root)

        # 普通书：启动 init skill（复用现有 workflow init 逻辑）
        init_body = _WorkflowsInitBody(
            brief=body.brief,
            reference_text_path=body.reference_text_path,
            model=body.model,
        )
        task = await start_init_task(new_root, init_body, label=f"init {new_root.name}")

        return {
            "ok": True,
            "project_root": str(new_root),
            "name": new_root.name,
            "task_id": task.task_id,
        }

    # ===========================================================
    # API：章节重置
    # ===========================================================

    class _ResetBody(BaseModel):
        chapter: int
        purge_directive: bool = False

    @app.post("/api/chapter/reset")
    async def reset_chapter_api(body: _ResetBody):
        """重置指定章节：清除派生数据，回到 draft-not-written 状态。"""
        root = _get_project_root()
        try:
            from scripts.chapter_reset_service import reset_chapter
            result = reset_chapter(root, body.chapter, purge_directive=body.purge_directive)
            # 重置后更新 checkpoint（如果存在），回退 last_completed_chapter
            cp_path = _checkpoint_path()
            if cp_path.is_file():
                try:
                    cp = json.loads(cp_path.read_text(encoding="utf-8"))
                    if isinstance(cp, dict) and cp.get("last_completed_chapter", 0) >= body.chapter:
                        cp["last_completed_chapter"] = body.chapter - 1
                        cp["updated_at"] = datetime.now(timezone.utc).isoformat()
                        _atomic_write_json(cp_path, cp)
                except Exception:
                    pass
            return {"ok": True, "chapter": body.chapter, "actions": result.get("actions", [])}
        except Exception as e:
            raise HTTPException(500, f"重置第 {body.chapter} 章失败: {e}")

    # ===========================================================
    # API：一键生成全书 — 存档/断点续跑 checkpoint
    # ===========================================================

    def _checkpoint_path() -> Path:
        return _ainovel_dir() / "auto_generate_checkpoint.json"

    def _atomic_write_json(path: Path, data: dict) -> None:
        """原子写：先写 .tmp 再 os.replace，崩溃不损坏文件。"""
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, path)

    class _CheckpointBody(BaseModel):
        status: str = "running"
        book_name: str = ""
        target_chapters: int = 0
        batch_size: int = 10
        model: str | None = None
        current_phase: str = "writing"
        last_completed_chapter: int = 0
        batch_start: int = 0
        batch_end: int = 0
        error: str | None = None

    @app.get("/api/auto-generate/checkpoint")
    def get_auto_generate_checkpoint():
        """读取 checkpoint；不存在则从项目实际状态推断进度。"""
        cp_path = _checkpoint_path()
        if cp_path.is_file():
            try:
                data = json.loads(cp_path.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    return {"checkpoint": data, "inferred": False}
            except (OSError, json.JSONDecodeError):
                pass

        # 无 checkpoint 文件：从项目状态推断
        root = _get_project_root()
        state = _load_state_payload()
        inferred = {
            "project_root": str(root),
            "book_name": "",
            "target_chapters": 0,
            "batch_size": 10,
            "model": None,
            "status": "inferred",
            "current_phase": "writing",
            "last_completed_chapter": 0,
            "batch_start": 0,
            "batch_end": 0,
            "created_at": None,
            "updated_at": None,
            "error": None,
            "inferred": True,
        }

        if not state:
            return {"checkpoint": None, "inferred": None}

        # 书名
        pi = state.get("project_info") or {}
        inferred["book_name"] = pi.get("title", "")
        inferred["target_chapters"] = int(pi.get("target_chapters") or 0)

        # 已规划的章范围
        progress = state.get("progress") or {}
        chapters_planned = progress.get("chapters_planned") or []
        max_planned = 0
        for entry in chapters_planned:
            rng = str(entry.get("chapters_range", ""))
            if "-" in rng:
                try:
                    end = int(rng.split("-")[1].strip())
                    if end > max_planned:
                        max_planned = end
                except ValueError:
                    pass
        inferred["batch_end"] = max_planned

        # 已完成的章节：检查 chapter_status 中 committed / finalized 的章
        chapter_status = progress.get("chapter_status") or {}
        last_done = 0
        for ch_str, cs in chapter_status.items():
            try:
                ch = int(ch_str)
            except ValueError:
                continue
            stage = cs.get("stage", "") if isinstance(cs, dict) else ""
            if stage in ("committed", "finalized") and ch > last_done:
                last_done = ch
        inferred["last_completed_chapter"] = last_done

        # 检查 AI生成/ 目录中已起草的章
        ai_dir = root / "AI生成"
        if ai_dir.is_dir():
            for md in ai_dir.glob("第*章.md"):
                try:
                    ch = int(md.stem.replace("第", "").replace("章", ""))
                    if ch > last_done:
                        last_done = ch
                except ValueError:
                    continue

        inferred["last_completed_chapter"] = last_done

        if last_done > 0 or max_planned > 0:
            inferred["status"] = "inferred"
            return {"checkpoint": inferred, "inferred": True}

        return {"checkpoint": None, "inferred": None}

    @app.post("/api/auto-generate/checkpoint")
    async def save_auto_generate_checkpoint(body: _CheckpointBody):
        """创建/更新自动生成 checkpoint。"""
        cp_path = _checkpoint_path()
        # 读已有 checkpoint，保留 created_at
        existing: dict = {}
        if cp_path.is_file():
            try:
                existing = json.loads(cp_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                existing = {}
        if not isinstance(existing, dict):
            existing = {}

        now_iso = datetime.now(timezone.utc).isoformat()
        checkpoint = {
            "project_root": str(_get_project_root()),
            "book_name": body.book_name or existing.get("book_name", ""),
            "target_chapters": body.target_chapters,
            "batch_size": body.batch_size,
            "model": body.model,
            "status": body.status,
            "current_phase": body.current_phase,
            "last_completed_chapter": body.last_completed_chapter,
            "batch_start": body.batch_start,
            "batch_end": body.batch_end,
            "created_at": existing.get("created_at", now_iso),
            "updated_at": now_iso,
            "error": body.error,
        }
        _atomic_write_json(cp_path, checkpoint)
        return {"ok": True, "checkpoint": checkpoint}

    @app.post("/api/auto-generate/checkpoint/clear")
    async def clear_auto_generate_checkpoint():
        """清除自动生成 checkpoint（完成或放弃时调用）。"""
        cp_path = _checkpoint_path()
        try:
            cp_path.unlink(missing_ok=True)
        except OSError:
            pass
        return {"ok": True}

    @app.get("/api/story-runtime/health")
    def story_runtime_health():
        """健康检查端点（无项目时也返回 200，仅标记状态）。"""
        root = _get_project_root()
        if root is None:
            return {"status": "ready", "mode": "system", "message": "无预选项目，系统工具可用"}
        return _build_story_runtime_health_report(root)

    # ===========================================================
    # API：测试书初始化（清空运行时数据，保留设定）
    # ===========================================================

    class _InitBody(BaseModel):
        project_root: str

    @app.post("/api/project/initialize")
    def initialize_project(body: _InitBody):
        """初始化测试书：清空运行时数据，保留设定集 + project_info + channel + test_book。"""
        import shutil
        target = Path(body.project_root).resolve()
        if not _is_book_dir(target):
            raise HTTPException(400, f"不是有效的书目录（缺 .ainovel/state.json）：{target}")
        if target == _get_project_root().resolve():
            raise HTTPException(400, "不能初始化当前活跃书，请先切换到其他书")

        # 读取现有设定
        sp = target / ".ainovel" / "state.json"
        try:
            old_state = json.loads(sp.read_text(encoding="utf-8")) if sp.is_file() else {}
        except (OSError, json.JSONDecodeError):
            old_state = {}
        project_info = old_state.get("project_info", {})
        channel = old_state.get("channel", {})
        test_book = old_state.get("test_book", False)

        # 删除运行时目录
        for dirname in ["正文", "大纲", "审查报告", "AI生成", ".story-system"]:
            d = target / dirname
            if d.is_dir():
                shutil.rmtree(d)

        # 清理 .ainovel/ 内部
        ainovel_dir = target / ".ainovel"
        for sub in ["archive", "backups", "decision_logs", "faiss_index",
                     "summaries", "tmp", "observability", "init_research",
                     "style_anchors_user"]:
            d = ainovel_dir / sub
            if d.is_dir():
                shutil.rmtree(d)
        for fname in ["index.db", "reference_vectors.db", "vectors.db",
                       "style_samples.db", "auto_generate_checkpoint.json",
                       "state.json.bak", "health_report.md", "idea_bank.json",
                       "memory_scratchpad.json", "memory_scratchpad.json.bak"]:
            f = ainovel_dir / fname
            if f.is_file():
                f.unlink()

        # 重写 state.json（保留设定，重置运行时字段）
        from datetime import datetime as dt
        new_state = {
            "test_book": test_book,
            "project_info": project_info,
            "channel": channel,
            "progress": {
                "current_chapter": 0,
                "total_words": 0,
                "last_updated": dt.now().strftime("%Y-%m-%d %H:%M:%S"),
                "volumes_completed": [],
                "current_volume": 1,
                "volumes_planned": [],
                "chapters_planned": [],
            },
            "protagonist_state": {
                "name": project_info.get("protagonist_name", ""),
                "power": {"realm": "", "layer": 1, "bottleneck": ""},
                "location": {"current": "", "last_chapter": 0},
                "golden_finger": {"name": "", "level": 1, "cooldown": 0, "skills": []},
                "attributes": {},
            },
            "relationships": {},
            "disambiguation_warnings": [],
            "disambiguation_pending": [],
            "world_settings": {"power_system": [], "factions": [], "locations": []},
            "plot_threads": {"active_threads": [], "foreshadowing": []},
            "review_checkpoints": [],
            "chapter_meta": {},
            "strand_tracker": {
                "last_quest_chapter": 0,
                "last_fire_chapter": 0,
                "last_constellation_chapter": 0,
                "current_dominant": None,
                "chapters_since_switch": 0,
                "history": [],
            },
        }
        _atomic_write_json(sp, new_state)

        # 重建空目录
        for subdir in [".ainovel/archive", ".ainovel/backups", ".ainovel/summaries",
                        ".story-system", "大纲", "正文", "审查报告", "AI生成"]:
            (target / subdir).mkdir(parents=True, exist_ok=True)

        # 测试书：重新复制 prompt 文件
        if test_book:
            try:
                from .workflows._prompts import init_test_book_prompts
                init_test_book_prompts(target)
            except Exception:
                pass

        return {"ok": True, "name": target.name}

    # ===========================================================
    # API：测试书 prompt 文件管理
    # ===========================================================

    class _PromptReadBody(BaseModel):
        category: str
        filename: str

    class _PromptWriteBody(BaseModel):
        category: str
        filename: str
        content: str

    @app.get("/api/prompts/list")
    def list_prompt_files_api():
        """列出所有 prompt 文件（category/path/size/is_local/editable）。"""
        from .workflows._prompts import list_prompt_files
        return {"files": list_prompt_files(_get_project_root())}

    @app.post("/api/prompts/read")
    def read_prompt_file_api(body: _PromptReadBody):
        """读取单个 prompt 文件内容。"""
        from .workflows._prompts import read_prompt_file_safe
        return read_prompt_file_safe(_get_project_root(), body.category, body.filename)

    @app.post("/api/prompts/write")
    def write_prompt_file_api(body: _PromptWriteBody):
        """写入 prompt 文件到本地副本。"""
        from .workflows._prompts import write_prompt_file
        write_prompt_file(_get_project_root(), body.category, body.filename, body.content)
        return {"ok": True}

    @app.post("/api/prompts/reset")
    def reset_prompt_file_api(body: _PromptReadBody):
        """从全局重置单个文件。"""
        from .workflows._prompts import reset_prompt_file
        msg = reset_prompt_file(_get_project_root(), body.category, body.filename)
        return {"ok": True, "message": msg}

    @app.post("/api/prompts/reset-all")
    def reset_all_prompt_files_api():
        """从全局重置所有 prompt 文件。"""
        from .workflows._prompts import reset_all_prompt_files
        stats = reset_all_prompt_files(_get_project_root())
        return {"ok": True, "stats": stats}

    # ===========================================================
    # API：Prompt 审阅台（预览 / 捕获 / 可编辑片段）
    # ===========================================================

    class _PromptPreviewBody(BaseModel):
        stage: str
        chapter: int = 1
        batch: str | None = None

    class _PromptFragmentNameBody(BaseModel):
        name: str

    class _PromptFragmentWriteBody(BaseModel):
        name: str
        content: str

    def _is_test_book() -> bool:
        return bool(_load_state_payload().get("test_book", False))

    @app.get("/api/prompts/stages")
    def prompt_stages_api():
        """列出所有可预览/捕获的 prompt 阶段。"""
        from .workflows._prompt_preview import STAGES
        return {"stages": STAGES, "test_book": _is_test_book()}

    @app.post("/api/prompts/preview")
    def prompt_preview_api(body: _PromptPreviewBody):
        """组装某阶段的 prompt 预览（不调 LLM）。"""
        from .workflows._prompt_preview import build_preview
        root = _get_project_root()
        result = build_preview(root, body.stage, body.chapter, body.batch)
        result["stage"] = body.stage
        result["chapter"] = body.chapter
        result["test_book"] = _is_test_book()
        return result

    @app.get("/api/prompts/log")
    def prompt_log_api(stage: str | None = None, chapter: int | None = None, limit: int = 50):
        """读取捕获的实际发出 prompt 记录（latest first）。"""
        from .workflows._prompt_log import read_log
        items = read_log(_get_project_root(), stage=stage, chapter=chapter, limit=limit)
        return {"items": items}

    @app.get("/api/prompts/fragments")
    def prompt_fragments_api():
        """列出所有可编辑片段及覆盖状态。"""
        from .workflows._prompt_fragments import list_fragments
        return {"fragments": list_fragments(_get_project_root()), "test_book": _is_test_book()}

    @app.post("/api/prompts/fragment/read")
    def prompt_fragment_read_api(body: _PromptFragmentNameBody):
        """读取单个片段（内容 + 是否覆盖 + 来源 + 占位符）。"""
        from .workflows._prompt_fragments import read_fragment_detail, FRAGMENT_DEFAULTS
        if body.name not in FRAGMENT_DEFAULTS:
            raise HTTPException(404, f"未知片段: {body.name}")
        detail = read_fragment_detail(_get_project_root(), body.name)
        detail["test_book"] = _is_test_book()
        return detail

    @app.post("/api/prompts/fragment/write")
    def prompt_fragment_write_api(body: _PromptFragmentWriteBody):
        """写入片段本地覆盖（仅测试书）。"""
        if not _is_test_book():
            raise HTTPException(403, "仅测试书可编辑 prompt 片段")
        from .workflows._prompt_fragments import write_fragment
        try:
            write_fragment(_get_project_root(), body.name, body.content)
        except KeyError as exc:
            raise HTTPException(404, str(exc)) from exc
        return {"ok": True}

    @app.post("/api/prompts/fragment/reset")
    def prompt_fragment_reset_api(body: _PromptFragmentNameBody):
        """删除片段本地覆盖，回到代码默认（仅测试书）。"""
        if not _is_test_book():
            raise HTTPException(403, "仅测试书可编辑 prompt 片段")
        from .workflows._prompt_fragments import reset_fragment
        removed = reset_fragment(_get_project_root(), body.name)
        return {"ok": True, "removed": removed}

    # ===========================================================
    # API：API 预设库（多组主力模型配置，可切换）
    # ===========================================================

    class _ApiLibraryBody(BaseModel):
        id: str | None = None
        name: str = ""
        fields: dict = Field(default_factory=dict)

    class _ApiLibraryApplyBody(BaseModel):
        id: str

    class _EmbedConfigBody(BaseModel):
        fields: dict = Field(default_factory=dict)

    def _refresh_after_api_preset() -> None:
        """切文字模型预设 / 存向量配置后：重放当前预设到 os.environ，并重绑
        prompt-harness SETTINGS，避免「陈旧环境变量」（切模型不生效要重启）的坑。
        apply_current_to_env 读 api_library.json（唯一来源）覆盖 env；init_settings
        优先 os.getenv → 必须先重放 env 再重绑。prompt-harness 未加载时静默跳过。
        """
        try:
            from .routes.api_library import apply_current_to_env
            apply_current_to_env()
        except Exception:
            return
        try:
            from prompt_harness.server import init_prompt_harness
            init_prompt_harness(_ph_data_dir, corpus_dir=_ph_corpus_dir)
        except Exception:
            pass

    @app.get("/api/api-library")
    def api_library_list_api():
        """列出文字模型预设 + 向量模型配置 + 字段元数据（敏感字段掩码）。"""
        from .routes.api_library import list_all
        return list_all()

    @app.post("/api/api-library")
    def api_library_save_api(body: _ApiLibraryBody):
        """新建或更新文字模型预设。id 为空=新建，否则更新。"""
        from .routes.api_library import create_text_preset, update_text_preset
        if body.id:
            ok = update_text_preset(body.id, body.name, body.fields)
            if not ok:
                raise HTTPException(404, "预设不存在")
            return {"ok": True, "id": body.id}
        pid = create_text_preset(body.name, body.fields)
        return {"ok": True, "id": pid}

    @app.delete("/api/api-library/{preset_id}")
    def api_library_delete_api(preset_id: str):
        """删除文字模型预设。"""
        from .routes.api_library import delete_text_preset
        ok = delete_text_preset(preset_id)
        if not ok:
            raise HTTPException(404, "预设不存在")
        return {"ok": True}

    @app.post("/api/api-library/apply")
    def api_library_apply_api(body: _ApiLibraryApplyBody):
        """应用文字模型预设：把 ARK_* 字段写入 .env，记 current_text_id。"""
        from .routes.api_library import apply_text_preset
        try:
            result = apply_text_preset(body.id)
        except KeyError as exc:
            raise HTTPException(404, str(exc)) from exc
        # 即时生效：重放 env + 重绑 prompt-harness SETTINGS，无需重启
        _refresh_after_api_preset()
        return result

    @app.post("/api/api-library/embed")
    def api_library_embed_save_api(body: _ApiLibraryBody):
        """新建或更新向量模型预设。id 为空=新建，否则更新。"""
        from .routes.api_library import create_embed_preset, update_embed_preset
        if body.id:
            ok = update_embed_preset(body.id, body.name, body.fields)
            if not ok:
                raise HTTPException(404, "向量预设不存在")
            return {"ok": True, "id": body.id}
        pid = create_embed_preset(body.name, body.fields)
        return {"ok": True, "id": pid}

    @app.delete("/api/api-library/embed/{preset_id}")
    def api_library_embed_delete_api(preset_id: str):
        """删除向量模型预设。"""
        from .routes.api_library import delete_embed_preset
        ok = delete_embed_preset(preset_id)
        if not ok:
            raise HTTPException(404, "向量预设不存在")
        return {"ok": True}

    @app.post("/api/api-library/embed/apply")
    def api_library_embed_apply_api(body: _ApiLibraryApplyBody):
        """应用向量模型预设：把 EMBED_* 字段写入 .env，记 current_embed_id。"""
        from .routes.api_library import apply_embed_preset
        try:
            result = apply_embed_preset(body.id)
        except KeyError as exc:
            raise HTTPException(404, str(exc)) from exc
        # 即时生效：重放 env + 重绑 prompt-harness SETTINGS，无需重启
        _refresh_after_api_preset()
        return result

    @app.get("/api/api-library/embed-config")
    def api_library_embed_config_get():
        """获取向量模型配置（掩码）。"""
        from .routes.api_library import list_all
        data = list_all()
        return {
            "fields": data["embed_config"]["fields"],
            "meta": data["embed_fields"],
        }

    @app.post("/api/api-library/embed-config")
    def api_library_embed_config_save(body: _EmbedConfigBody):
        """保存向量模型配置：写入 api_library.json + .env。"""
        from .routes.api_library import save_embed_config
        result = save_embed_config(body.fields)
        # 即时生效：重放 env + 重绑 prompt-harness SETTINGS
        _refresh_after_api_preset()
        return result


    # ===========================================================
    # API：测试书导出
    # ===========================================================

    @app.get("/api/project/export")
    def export_project(include_chapters: bool = True):
        """导出测试书的完整数据为 JSON。"""
        root = _get_project_root()
        state = _load_state_payload(required=True)

        result = {
            "export_metadata": {
                "exported_at": datetime.now(timezone.utc).isoformat(),
                "book_name": root.name,
                "test_book": state.get("test_book", False),
            },
        }

        # 1. project_info
        result["project_info"] = state.get("project_info", {})

        # 2. channel 配置
        channel_cfg = state.get("channel", {})
        profile_name = channel_cfg.get("profile", "default")
        result["channel"] = {
            "profile": profile_name,
            "opening_count": channel_cfg.get("opening_count", 3),
            "overrides": channel_cfg.get("overrides", {}),
        }
        try:
            from .workflows._channel import _channels_dir, _parse_channel_md
            md_path = _channels_dir() / f"{profile_name}.md"
            if md_path.is_file():
                fm, sections = _parse_channel_md(md_path)
                result["channel"]["profile_name"] = fm.get("name", profile_name)
                result["channel"]["profile_content"] = sections
        except Exception:
            pass

        # 3. 设定集 markdown
        result["设定集"] = {}
        settings_dir = root / "设定集"
        if settings_dir.is_dir():
            for md_file in settings_dir.glob("*.md"):
                try:
                    result["设定集"][md_file.stem] = md_file.read_text(encoding="utf-8")
                except Exception:
                    pass

        # 4. Agent prompt 文件（优先读本地副本）
        result["agent_prompts"] = {}
        try:
            from .workflows._prompts import resolve_prompt_path
            for agent_name in ["context-agent", "reviewer", "critic-agent",
                               "data-agent", "character-dialogue-agent", "deconstruction-agent"]:
                p = resolve_prompt_path(root, "agents", f"{agent_name}.md")
                if p.is_file():
                    result["agent_prompts"][agent_name] = p.read_text(encoding="utf-8")
        except Exception:
            pass

        # 5. 章节数据
        if include_chapters:
            result["chapters"] = _gather_chapter_data(root, state)

        # 6. .story-system 合约
        ss = root / ".story-system"
        for fname in ["MASTER_SETTING.json", "anti_patterns.json"]:
            f = ss / fname
            if f.is_file():
                try:
                    result[f.stem.lower()] = json.loads(f.read_text(encoding="utf-8"))
                except Exception:
                    result[f.stem.lower()] = None

        return result

    # ===========================================================
    # API：实体数据库（index.db 只读查询）
    # ===========================================================

    def _get_db() -> sqlite3.Connection:
        db_path = _ainovel_dir() / "index.db"
        if not db_path.is_file():
            raise HTTPException(404, "index.db 不存在")
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        return conn

    def _fetchall_safe(conn: sqlite3.Connection, query: str, params: tuple = ()) -> list[dict]:
        """执行只读查询；若目标表不存在（旧库），返回空列表。"""
        try:
            rows = conn.execute(query, params).fetchall()
            return [dict(r) for r in rows]
        except sqlite3.OperationalError as exc:
            if "no such table" in str(exc).lower() or "no such column" in str(exc).lower():
                return []
            raise HTTPException(status_code=500, detail=f"数据库查询失败: {exc}") from exc

    @app.get("/api/entities")
    def list_entities(
        entity_type: Optional[str] = Query(None, alias="type"),
        include_archived: bool = False,
    ):
        """列出所有实体（可按类型过滤）。"""
        with closing(_get_db()) as conn:
            q = "SELECT * FROM entities"
            params: list = []
            clauses: list[str] = []
            if entity_type:
                clauses.append("type = ?")
                params.append(entity_type)
            if not include_archived:
                clauses.append("is_archived = 0")
            if clauses:
                q += " WHERE " + " AND ".join(clauses)
            q += " ORDER BY last_appearance DESC"
            rows = conn.execute(q, params).fetchall()
            return [dict(r) for r in rows]

    @app.get("/api/entities/{entity_id}")
    def get_entity(entity_id: str):
        with closing(_get_db()) as conn:
            row = conn.execute("SELECT * FROM entities WHERE id = ?", (entity_id,)).fetchone()
            if not row:
                raise HTTPException(404, "实体不存在")
            return dict(row)

    @app.get("/api/relationships")
    def list_relationships(entity: Optional[str] = None, limit: int = 200):
        with closing(_get_db()) as conn:
            if entity:
                rows = conn.execute(
                    "SELECT * FROM relationships WHERE from_entity = ? OR to_entity = ? ORDER BY chapter DESC LIMIT ?",
                    (entity, entity, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM relationships ORDER BY chapter DESC LIMIT ?",
                    (limit,),
                ).fetchall()
            return [dict(r) for r in rows]

    @app.get("/api/relationship-events")
    def list_relationship_events(
        entity: Optional[str] = None,
        from_chapter: Optional[int] = None,
        to_chapter: Optional[int] = None,
        limit: int = 200,
    ):
        with closing(_get_db()) as conn:
            q = "SELECT * FROM relationship_events"
            params: list = []
            clauses: list[str] = []
            if entity:
                clauses.append("(from_entity = ? OR to_entity = ?)")
                params.extend([entity, entity])
            if from_chapter is not None:
                clauses.append("chapter >= ?")
                params.append(from_chapter)
            if to_chapter is not None:
                clauses.append("chapter <= ?")
                params.append(to_chapter)
            if clauses:
                q += " WHERE " + " AND ".join(clauses)
            q += " ORDER BY chapter DESC, id DESC LIMIT ?"
            params.append(limit)
            rows = conn.execute(q, params).fetchall()
            return [dict(r) for r in rows]

    @app.get("/api/chapters")
    def list_chapters():
        with closing(_get_db()) as conn:
            rows = conn.execute("SELECT * FROM chapters ORDER BY chapter ASC").fetchall()
            normalized = []
            for row in rows:
                item = dict(row)
                item["characters"] = _parse_json_value(item.get("characters"), [])
                normalized.append(item)
            return normalized

    @app.get("/api/scenes")
    def list_scenes(chapter: Optional[int] = None, limit: int = 500):
        with closing(_get_db()) as conn:
            if chapter is not None:
                rows = conn.execute(
                    "SELECT * FROM scenes WHERE chapter = ? ORDER BY scene_index ASC", (chapter,)
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM scenes ORDER BY chapter ASC, scene_index ASC LIMIT ?", (limit,)
                ).fetchall()
            return [dict(r) for r in rows]

    @app.get("/api/reading-power")
    def list_reading_power(limit: int = 50):
        with closing(_get_db()) as conn:
            rows = conn.execute(
                "SELECT * FROM chapter_reading_power ORDER BY chapter DESC LIMIT ?", (limit,)
            ).fetchall()
            return [dict(r) for r in rows]

    @app.get("/api/review-metrics")
    def list_review_metrics(limit: int = 20):
        with closing(_get_db()) as conn:
            rows = conn.execute(
                "SELECT * FROM review_metrics ORDER BY end_chapter DESC LIMIT ?", (limit,)
            ).fetchall()
            normalized = []
            for row in rows:
                item = dict(row)
                item["dimension_scores"] = _parse_json_value(item.get("dimension_scores"), {})
                item["severity_counts"] = _parse_json_value(item.get("severity_counts"), {})
                item["critical_issues"] = _parse_json_value(item.get("critical_issues"), [])
                normalized.append(item)
            return normalized

    @app.get("/api/stats/chapter-trend")
    def chapter_trend(limit: int = Query(50, ge=1, le=500), offset: int = Query(0, ge=0)):
        state = _load_state_payload()
        strand_map = _build_strand_map(state)

        with closing(_get_db()) as conn:
            total_rows = _fetchall_safe(conn, "SELECT COUNT(*) AS count FROM chapters")
            latest_rows = _fetchall_safe(conn, "SELECT MAX(chapter) AS chapter FROM chapters")
            rows = _fetchall_safe(
                conn,
                """
                WITH selected_chapters AS (
                    SELECT chapter, title, location, word_count, characters, summary
                    FROM chapters
                    ORDER BY chapter DESC
                    LIMIT ? OFFSET ?
                )
                SELECT
                    c.chapter,
                    c.title,
                    c.location,
                    c.word_count,
                    c.characters,
                    c.summary,
                    rp.hook_type,
                    rp.hook_strength,
                    rp.is_transition,
                    rp.override_count,
                    rp.debt_balance,
                    rm.overall_score AS review_score,
                    rm.severity_counts
                FROM selected_chapters c
                LEFT JOIN chapter_reading_power rp ON rp.chapter = c.chapter
                LEFT JOIN review_metrics rm ON rm.end_chapter = c.chapter
                ORDER BY c.chapter ASC
                """,
                (limit, offset),
            )

        hook_strength_value = {"weak": 1, "medium": 3, "strong": 5}
        items = []
        for row in rows:
            chapter = int(row.get("chapter") or 0)
            hook_strength = str(row.get("hook_strength") or "").strip().lower()
            items.append(
                {
                    "chapter": chapter,
                    "title": row.get("title") or "",
                    "location": row.get("location") or "",
                    "word_count": int(row.get("word_count") or 0),
                    "characters": _parse_json_value(row.get("characters"), []),
                    "summary": row.get("summary") or "",
                    "review_score": row.get("review_score"),
                    "review_severity_counts": _parse_json_value(row.get("severity_counts"), {}),
                    "hook_type": row.get("hook_type") or "",
                    "hook_strength": hook_strength,
                    "hook_strength_value": hook_strength_value.get(hook_strength, 0),
                    "is_transition": bool(row.get("is_transition")),
                    "override_count": int(row.get("override_count") or 0),
                    "debt_balance": float(row.get("debt_balance") or 0.0),
                    "strand": strand_map.get(chapter, ""),
                    "volume": _resolve_volume_for_chapter(state, chapter),
                }
            )

        return {
            "items": items,
            "total": int(total_rows[0]["count"] or 0) if total_rows else 0,
            "latest_chapter": int(latest_rows[0]["chapter"] or 0) if latest_rows else 0,
            "limit": limit,
            "offset": offset,
        }

    @app.get("/api/commits")
    def list_commits(limit: int = Query(20, ge=1, le=200)):
        commits_dir = _story_system_dir() / "commits"
        if not commits_dir.is_dir():
            return {"items": [], "total": 0, "limit": limit}

        items = []
        for path in commits_dir.glob("chapter_*.commit.json"):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue

            meta = payload.get("meta") if isinstance(payload, dict) else {}
            provenance = payload.get("provenance") if isinstance(payload, dict) else {}
            chapter = int((meta or {}).get("chapter") or _extract_story_chapter(path))
            items.append(
                {
                    "chapter": chapter,
                    "status": str((meta or {}).get("status") or "missing"),
                    "projection_status": payload.get("projection_status") or {},
                    "write_fact_role": str((provenance or {}).get("write_fact_role") or ""),
                    "contract_refs": payload.get("contract_refs") or {},
                    "path": path.name,
                    "updated_at": datetime.fromtimestamp(
                        path.stat().st_mtime, tz=timezone.utc
                    ).isoformat(),
                }
            )

        items.sort(key=lambda item: item["chapter"], reverse=True)
        return {"items": items[:limit], "total": len(items), "limit": limit}

    @app.get("/api/contracts/summary")
    def contracts_summary():
        from data_modules.story_contracts import StoryContractPaths, read_json_if_exists

        project_root = _get_project_root()
        state = _load_state_payload()
        runtime = _build_story_runtime_health_report(project_root)
        chapter = int(runtime.get("chapter") or ((state.get("progress") or {}).get("current_chapter") or 0))
        current_volume = _resolve_volume_for_chapter(state, chapter) or int(
            ((state.get("progress") or {}).get("current_volume") or 1)
        )

        paths = StoryContractPaths.from_project_root(project_root)
        master_payload = read_json_if_exists(paths.master_json) or {}

        return {
            "chapter": chapter,
            "current_volume": current_volume,
            "master": {
                "exists": bool(master_payload),
                "primary_genre": str(((master_payload.get("route") or {}).get("primary_genre") or "")),
                "core_tone": str(
                    ((master_payload.get("master_constraints") or {}).get("core_tone") or "")
                ),
            },
            "counts": {
                "volumes": len(list(paths.volumes_dir.glob("volume_*.json"))) if paths.volumes_dir.is_dir() else 0,
                "chapters": len(list(paths.chapters_dir.glob("chapter_*.json"))) if paths.chapters_dir.is_dir() else 0,
                "reviews": len(list(paths.reviews_dir.glob("chapter_*.review.json"))) if paths.reviews_dir.is_dir() else 0,
                "commits": len(list(paths.commits_dir.glob("chapter_*.commit.json"))) if paths.commits_dir.is_dir() else 0,
            },
            "current_contracts": {
                "volume": paths.volume_json(current_volume).is_file(),
                "chapter": paths.chapter_json(chapter).is_file() if chapter > 0 else False,
                "review": paths.review_json(chapter).is_file() if chapter > 0 else False,
                "commit": paths.commit_json(chapter).is_file() if chapter > 0 else False,
            },
        }

    @app.get("/api/env-status")
    def env_status():
        return _build_env_status(_get_project_root())

    # ===== 系统级：一键应用新代码 + 重启后端 =====
    # 用于开发模式：改了前端/后端代码后，点一下按钮全部生效
    # 流程：前端重新 build → 同步 dist → 启动重启助手 → 旧进程退出 → 新进程启动
    _rebuild_lock = threading.Lock()

    @app.post("/api/system/rebuild-and-restart")
    async def system_rebuild_and_restart():
        """一键应用新代码（带构建锁：防止并发 build 竞争把 dist 删没导致页面失效）。"""
        if not _rebuild_lock.acquire(blocking=False):
            return {"ok": False, "message": "正在构建中，请稍后重试"}
        try:
            return await _do_system_rebuild_and_restart()
        finally:
            _rebuild_lock.release()

    async def _do_system_rebuild_and_restart():
        """一键应用新代码：前端构建 + 后端自重启。

        v5.2 起为单 dist 模式：主系统和 prompt-harness 共用同一份 dist，
        不再需要同步步骤。
        """
        import subprocess
        import shutil
        import time
        import tempfile

        # 计算路径
        app_dir = Path(__file__).resolve().parent          # app/dashboard/
        project_root_dir = app_dir.parent.parent             # ainovel-write/
        frontend_dir = app_dir / "frontend"
        dist_src = frontend_dir / "dist"

        steps = []
        overall_ok = True
        t_start = time.monotonic()

        # ── Step 1: 前端构建（v5.2 起：单 dist 模式，无需同步）──
        # 主系统直接使用 app/dashboard/frontend/dist/ 作为静态文件
        # prompt-harness 也从同一份 dist 读取（通过 _resolve_frontend_dist()）
        t0 = time.monotonic()
        try:
            if not (frontend_dir / "package.json").is_file():
                raise FileNotFoundError(f"前端源码目录不存在: {frontend_dir}")

            # 构建前先手动清空 dist，确保产物绝对干净
            # vite 的 emptyOutDir 在某些场景下会失效（文件被占用/权限/路径问题），
            # 这里做双重保险：先删目录再构建。
            # 【2026-08-10】彻底杜绝 404：清空前把旧 index.html 备份到临时文件，
            # 构建结束后若新 index.html 未生成则恢复——空窗期页面也永远有 index.html
            #（serve_spa 另加「构建中」占位页兜底，见 _BUILDING_PLACEHOLDER）。
            # 【2026-08-10 彻底修复】备份整个 dist 目录到临时位置（不只 index.html）：
            # 构建失败/中断时能完整恢复（index.html + assets），前端不白屏不 404。
            _dist_bak = None
            try:
                if dist_src.is_dir():
                    _tmpd = tempfile.mkdtemp(suffix="_distbak")
                    _dist_bak = Path(_tmpd)
                    shutil.copytree(dist_src, _dist_bak / "dist", dirs_exist_ok=True)
                    shutil.rmtree(dist_src, ignore_errors=True)
            except Exception:
                _dist_bak = None

            # 判断包管理器
            npm_cmd = "npm"
            if (frontend_dir / "pnpm-lock.yaml").is_file():
                npm_cmd = "pnpm"
            elif (frontend_dir / "yarn.lock").is_file():
                npm_cmd = "yarn"

            # Windows 上 npm 是 npm.cmd（批处理文件），shell=False 时
            # CreateProcess 找不到可执行文件，需 shell=True 经由 cmd.exe 启动。
            # capture_output 的 text=True 默认用 gbk 解码含中文输出会报错，
            # 故使用 errors='replace' 避免 UnicodeDecodeError。
            proc = subprocess.run(
                [npm_cmd, "run", "build"],
                cwd=str(frontend_dir),
                capture_output=True,
                text=True,
                timeout=300,
                shell=(sys.platform == "win32"),
                errors='replace',
            )
            build_err = None
            if proc.returncode != 0:
                build_err = proc.stderr or proc.stdout or "构建失败"
            # 【2026-08-11 修复】无论 build 成败，dist 缺失时都恢复备份——
            # 之前恢复逻辑在 raise 之后，build 失败时永不执行 → dist 永久丢失（页面一直占位）。
            if _dist_bak and not dist_src.is_dir():
                try:
                    shutil.copytree(_dist_bak / "dist", dist_src, dirs_exist_ok=True)
                except Exception:
                    pass
            if _dist_bak:
                try:
                    shutil.rmtree(_dist_bak, ignore_errors=True)  # 清理临时备份
                except Exception:
                    pass
            if build_err:
                raise RuntimeError(build_err)

            steps.append({
                "step": "frontend_build",
                "ok": True,
                "duration_ms": round((time.monotonic() - t0) * 1000),
            })

            # 【2026-08-17】清除 WebView2 缓存，防止旧 index.html 引用旧 hash 文件
            # WebView2 会缓存 index.html（无 hash），导致加载旧的 JS/CSS
            t_wv = time.monotonic()
            try:
                import glob as _glob
                _appdata = os.environ.get("LOCALAPPDATA", "")
                _wv_cache_pattern = os.path.join(_appdata, "com.ainovel.write", "EBWebView", "Default", "Cache", "*")
                _cleared = 0
                for _f in _glob.glob(_wv_cache_pattern):
                    try:
                        if os.path.isfile(_f):
                            os.remove(_f)
                            _cleared += 1
                    except Exception:
                        pass
                # 也清理 Code Cache 子目录
                _code_cache_dir = os.path.join(_appdata, "com.ainovel.write", "EBWebView", "Default", "Code Cache")
                if os.path.isdir(_code_cache_dir):
                    for _f in _glob.glob(os.path.join(_code_cache_dir, "*")):
                        try:
                            if os.path.isfile(_f):
                                os.remove(_f)
                                _cleared += 1
                        except Exception:
                            pass
                steps.append({
                    "step": "webview_cache_clear",
                    "ok": True,
                    "cleared_files": _cleared,
                    "duration_ms": round((time.monotonic() - t_wv) * 1000),
                })
            except Exception as e:
                # 缓存清理失败不影响整体流程
                steps.append({
                    "step": "webview_cache_clear",
                    "ok": False,
                    "error": str(e),
                })

            # 前端热更新标记：build 成功即更新版本号，前端轮询 /api/version 检测变化自动 reload
            try:
                _version_dir = Path(os.environ.get("AINOVEL_WRITE_CONFIG", Path.home() / ".claude" / "ainovel-write"))
                _version_dir.mkdir(parents=True, exist_ok=True)
                (_version_dir / "frontend_version.txt").write_text(
                    f"{int(time.time() * 1000)}", encoding="utf-8")
            except Exception:
                pass
        except Exception as e:
            overall_ok = False
            steps.append({
                "step": "frontend_build",
                "ok": False,
                "duration_ms": round((time.monotonic() - t0) * 1000),
                "error": str(e),
            })

        # ── Step 2: 安排后端自重启 ──
        # 用一个独立的 Python 子进程作为"重启助手"：
        #   1. 等 2 秒让当前请求响应返回
        #   2. 杀掉当前进程
        #   3. 用同样的命令行参数重新启动
        t0 = time.monotonic()
        try:
            current_pid = os.getpid()
            python_exe = sys.executable
            # sys.argv 里是当前的命令行参数，但需要把 -m dashboard.server 还原
            # 因为 sys.argv[0] 可能是被 uvicorn 改了的
            # 最可靠的方式：直接用同样的 python -m dashboard.server + 原始参数
            # 我们用一个 restart marker 文件来传递参数
            restart_info = {
                "pid": current_pid,
                "python": python_exe,
                "cwd": str(Path.cwd()),
                "module": "dashboard.server",
                "args": sys.argv[1:],  # 去掉第一个（模块名或脚本名）
            }

            # 写重启标记到临时文件
            marker_dir = Path(tempfile.gettempdir())
            marker_file = marker_dir / f"ainovel_restart_{current_pid}.json"
            with open(marker_file, "w", encoding="utf-8") as f:
                json.dump(restart_info, f, ensure_ascii=False, indent=2)

            # 重启助手脚本（内联）
            helper_code = f'''
import time, os, sys, json, subprocess, signal

marker_file = r"{marker_file}"
with open(marker_file, "r", encoding="utf-8") as _f:
    info = json.load(_f)

old_pid = info["pid"]
python_exe = info["python"]
cwd = info["cwd"]

# 等 2 秒，让当前请求响应返回
time.sleep(2)

# 杀掉旧进程
try:
    os.kill(old_pid, signal.SIGTERM)
except Exception:
    pass

# 再等 1 秒确保端口释放
time.sleep(1)

# 启动新进程
# AINOVEL_RESTARTED=1：告知新进程这是「一键应用新代码」自重启，不要重复弹浏览器
# （桌面应用已有 WebView2，再开外部浏览器是骚扰）。
cmd = [python_exe, "-m", info["module"]] + info["args"]
_restart_env = dict(os.environ)
_restart_env["AINOVEL_RESTARTED"] = "1"
subprocess.Popen(cmd, cwd=cwd,
                 stdout=subprocess.DEVNULL,
                 stderr=subprocess.DEVNULL,
                 env=_restart_env,
                 creationflags=0x00000008 if sys.platform == "win32" else 0)

# 删除标记文件
try:
    os.unlink(marker_file)
except Exception:
    pass
'''

            # 启动助手进程（detached，不随父进程退出）
            creationflags = 0
            if sys.platform == "win32":
                creationflags = 0x00000008  # DETACHED_PROCESS
            subprocess.Popen(
                [sys.executable, "-c", helper_code],
                cwd=str(Path.cwd()),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=creationflags,
            )

            steps.append({
                "step": "schedule_restart",
                "ok": True,
                "duration_ms": round((time.monotonic() - t0) * 1000),
                "message": "后端将在 2 秒后重启",
            })
        except Exception as e:
            overall_ok = False
            steps.append({
                "step": "schedule_restart",
                "ok": False,
                "duration_ms": round((time.monotonic() - t0) * 1000),
                "error": str(e),
            })

        total_ms = round((time.monotonic() - t_start) * 1000)

        return {
            "ok": overall_ok,
            "total_ms": total_ms,
            "steps": steps,
            "will_restart": True,
            "message": "完成，后端即将重启，请稍候..." if overall_ok else "部分步骤失败，后端仍将尝试重启",
        }

    # ===== 前端热更新版本标记 =====
    @app.get("/api/version")
    async def system_version():
        """返回前端构建版本标记。前端轮询此端点，标记变化即自动 reload（热更新检测）。"""
        try:
            _vdir = Path(os.environ.get("AINOVEL_WRITE_CONFIG", Path.home() / ".claude" / "ainovel-write"))
            vfile = _vdir / "frontend_version.txt"
            version = vfile.read_text(encoding="utf-8").strip() if vfile.is_file() else "0"
        except Exception:
            version = "0"
        return {"version": version}

    # ===== 系统级：安全关闭后端 =====
    @app.post("/api/system/shutdown")
    async def system_shutdown():
        """安全关闭后端服务器。"""
        import logging
        import asyncio
        logging.getLogger("dashboard.app").warning("收到关闭请求，后端即将关闭...")
        _ulog.log_system_shutdown(component="main_app", reason="user_request")
        # 延迟 500ms 确保响应返回后再退出
        asyncio.get_event_loop().call_later(0.5, os._exit, 0)
        return {"ok": True, "message": "服务器正在关闭..."}

    # ===== API key / 模型配置（可视化在线更换）=====
    # v5.22.2：移除「系统自带 API 配置」——api_library.json 是唯一 API 来源，
    # 前端只保留 API 预设库（/api/api-library*）。用户级 .env 仅由
    # api_library.apply_current_to_env() 在启动/应用预设时同步，不再有独立编辑入口。

    @app.get("/api/env-status/probe")
    def env_status_probe():
        # v5.22.2：无项目时返回 400（原先传 None 进 _inspect_vector_db 崩溃 500）
        project_root = _get_project_root()
        if project_root is None:
            raise HTTPException(status_code=400, detail="请先在主页选择一本书")
        status = _build_env_status(project_root)
        runtime = _build_story_runtime_health_report(project_root)
        vector_db = status["vector_db"]
        checks = [
            {
                "name": "embed_api_key",
                "ok": bool(status["embed"]["api_key_present"]),
                "detail": "已配置" if status["embed"]["api_key_present"] else "未配置",
            },
            {
                "name": "rerank_api_key",
                "ok": bool(status["rerank"]["api_key_present"]),
                "detail": "已配置" if status["rerank"]["api_key_present"] else "未配置",
            },
            {
                "name": "vector_db",
                "ok": bool(vector_db["exists"] and not vector_db["error"]),
                "detail": vector_db["error"]
                or f"{vector_db['record_count']} records · {vector_db['size_bytes']} bytes",
            },
            {
                "name": "story_runtime",
                "ok": bool(runtime.get("mainline_ready")),
                "detail": (
                    f"chapter={runtime.get('chapter')} "
                    f"status={runtime.get('latest_commit_status')} "
                    f"fallback={','.join(runtime.get('fallback_sources') or []) or 'none'}"
                ),
            },
        ]
        return {
            "ok": all(bool(item["ok"]) for item in checks),
            "rag_mode": status["rag_mode"],
            "checks": checks,
            "checked_at": datetime.now(timezone.utc).isoformat(),
        }

    @app.get("/api/usage")
    def llm_usage(window_hours: int = Query(24, ge=1, le=168), recent: int = Query(50, ge=1, le=500)):
        """近 N 小时 LLM token 用量（计费看板）。"""
        from .services.usage import USAGE
        return USAGE.summary(window_seconds=window_hours * 3600, recent=recent)

    # ===== 统一日志系统 API =====
    @app.get("/api/logs/summary")
    def logs_summary(days: int = Query(1, ge=1, le=30)):
        """LLM 调用汇总（近 N 天，来自统一日志系统）。"""
        from .core.universal_logger import UniversalLogger
        return UniversalLogger.llm_summary(days=days)

    @app.get("/api/logs/files")
    def logs_files(category: str = "all", days: int = Query(7, ge=1, le=30)):
        """列出日志文件列表。"""
        from .core.universal_logger import UniversalLogger
        return {"files": UniversalLogger.list_log_files(category=category, days=days)}

    @app.get("/api/logs/read")
    def logs_read(
        filepath: str,
        limit: int = Query(100, ge=1, le=1000),
        event: str | None = None,
    ):
        """读取一个日志文件的内容。"""
        from .core.universal_logger import UniversalLogger
        event_filter = [event] if event else None
        return {"events": UniversalLogger.read_log(filepath, event_filter=event_filter, limit=limit)}
    # ===== 缓存管理 API =====
    from .services.cache_manager import CACHE_MANAGER
    @app.get("/api/cache/list")
    async def list_cache():
        """获取所有缓存项列表"""
        return {"caches": await CACHE_MANAGER.list_cache(_get_project_root())}
    @app.post("/api/cache/delete")
    async def delete_cache(body: dict):
        """删除指定缓存项"""
        cache_id = body.get("cache_id")
        if not cache_id:
            raise HTTPException(400, "cache_id 不能为空")
        try:
            success = await CACHE_MANAGER.delete_cache(_get_project_root(), cache_id)
            return {"ok": success}
        except ValueError as e:
            raise HTTPException(400, str(e))
        except RuntimeError as e:
            raise HTTPException(500, str(e))
    @app.post("/api/cache/rebuild")
    async def rebuild_cache(body: dict):
        """重建指定缓存项，返回task_id"""
        cache_id = body.get("cache_id")
        if not cache_id:
            raise HTTPException(400, "cache_id 不能为空")
        try:
            task_id = await CACHE_MANAGER.rebuild_cache(_get_project_root(), cache_id)
            return {"ok": True, "task_id": task_id}
        except ValueError as e:
            raise HTTPException(400, str(e))
        except RuntimeError as e:
            raise HTTPException(500, str(e))
    @app.post("/api/cache/clean-safe")
    async def clean_safe_cache():
        """一键清理所有安全可删缓存"""
        try:
            result = await CACHE_MANAGER.clean_safe(_get_project_root())
            return {"ok": True, **result}
        except Exception as e:
            raise HTTPException(500, f"清理失败：{str(e)}")

    @app.post("/api/cache/preview")
    async def cache_preview(body: dict):
        """预览缓存内容（支持 files / single_file / sqlite / binary 四种 kind）"""
        cache_id = body.get("cache_id")
        if not cache_id:
            raise HTTPException(400, "cache_id 不能为空")
        file_path = body.get("file_path")
        try:
            return await CACHE_MANAGER.preview_cache(_get_project_root(), cache_id, file_path)
        except ValueError as e:
            raise HTTPException(400, str(e))
        except FileNotFoundError as e:
            raise HTTPException(404, str(e))
        except Exception as e:
            raise HTTPException(500, f"预览失败：{str(e)}")

    @app.post("/api/cache/sqlite/update")
    async def cache_sqlite_update(body: dict):
        """编辑 SQLite 单元格（仅 vectors_db / reference_vectors_db 的白名单列）"""
        cache_id = body.get("cache_id")
        table = body.get("table")
        column = body.get("column")
        value = body.get("value", "")
        row_pk = body.get("row_pk", {})
        if not all([cache_id, table, column, isinstance(row_pk, dict)]):
            raise HTTPException(400, "cache_id / table / column / row_pk 必填")
        try:
            return await CACHE_MANAGER.update_sqlite_cell(
                _get_project_root(), cache_id, table, row_pk, column, value,
            )
        except ValueError as e:
            raise HTTPException(400, str(e))
        except FileNotFoundError as e:
            raise HTTPException(404, str(e))
        except Exception as e:
            raise HTTPException(500, f"更新失败：{str(e)}")

    @app.post("/api/cache/chapter_directive/purge_stale")
    async def purge_stale_chapter_directives():
        """清理失效的章节指令缓存：源章纲已删除但缓存仍存在的文件
        返回被删除的失效章号列表
        """
        try:
            deleted = await CACHE_MANAGER.purge_stale_chapter_directives(_get_project_root())
            return {
                "ok": True,
                "deleted_chapters": deleted,
                "message": f"成功清理{len(deleted)}个失效章节缓存"
            }
        except ValueError as e:
            raise HTTPException(400, str(e))
        except Exception as e:
            raise HTTPException(500, f"清理失败：{str(e)}")

    @app.get("/api/state-changes")
    def list_state_changes(entity: Optional[str] = None, limit: int = 100):
        with closing(_get_db()) as conn:
            if entity:
                rows = conn.execute(
                    "SELECT * FROM state_changes WHERE entity_id = ? ORDER BY chapter DESC LIMIT ?",
                    (entity, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM state_changes ORDER BY chapter DESC LIMIT ?", (limit,)
                ).fetchall()
            return [dict(r) for r in rows]

    # ===== 决策日志 API =====
    @app.get("/api/decision-log")
    def list_decision_log(
        task_id: Optional[str] = None,
        step_id: Optional[str] = None,
        limit: int = Query(100, ge=1, le=1000),
    ):
        """
        查询决策日志，支持按 task_id 和 step_id 过滤。
        返回按时间倒序排列的决策记录。
        """
        from .core.decision_log import get_decision_log

        log = get_decision_log(_get_project_root())
        records = log.list_decisions(task_id=task_id, step_id=step_id, limit=limit)
        return {"records": records, "count": len(records)}

    @app.get("/api/aliases")
    def list_aliases(entity: Optional[str] = None):
        with closing(_get_db()) as conn:
            if entity:
                rows = conn.execute(
                    "SELECT * FROM aliases WHERE entity_id = ?", (entity,)
                ).fetchall()
            else:
                rows = conn.execute("SELECT * FROM aliases").fetchall()
            return [dict(r) for r in rows]

    # ===========================================================
    # API：扩展表（v5.3+ / v5.4+）
    # ===========================================================

    @app.get("/api/overrides")
    def list_overrides(status: Optional[str] = None, limit: int = 100):
        with closing(_get_db()) as conn:
            if status:
                return _fetchall_safe(
                    conn,
                    "SELECT * FROM override_contracts WHERE status = ? ORDER BY chapter DESC LIMIT ?",
                    (status, limit),
                )
            return _fetchall_safe(
                conn,
                "SELECT * FROM override_contracts ORDER BY chapter DESC LIMIT ?",
                (limit,),
            )

    @app.get("/api/debts")
    def list_debts(status: Optional[str] = None, limit: int = 100):
        with closing(_get_db()) as conn:
            if status:
                return _fetchall_safe(
                    conn,
                    "SELECT * FROM chase_debt WHERE status = ? ORDER BY updated_at DESC LIMIT ?",
                    (status, limit),
                )
            return _fetchall_safe(
                conn,
                "SELECT * FROM chase_debt ORDER BY updated_at DESC LIMIT ?",
                (limit,),
            )

    @app.get("/api/debt-events")
    def list_debt_events(debt_id: Optional[int] = None, limit: int = 200):
        with closing(_get_db()) as conn:
            if debt_id is not None:
                return _fetchall_safe(
                    conn,
                    "SELECT * FROM debt_events WHERE debt_id = ? ORDER BY chapter DESC, id DESC LIMIT ?",
                    (debt_id, limit),
                )
            return _fetchall_safe(
                conn,
                "SELECT * FROM debt_events ORDER BY chapter DESC, id DESC LIMIT ?",
                (limit,),
            )

    @app.get("/api/invalid-facts")
    def list_invalid_facts(status: Optional[str] = None, limit: int = 100):
        with closing(_get_db()) as conn:
            if status:
                return _fetchall_safe(
                    conn,
                    "SELECT * FROM invalid_facts WHERE status = ? ORDER BY marked_at DESC LIMIT ?",
                    (status, limit),
                )
            return _fetchall_safe(
                conn,
                "SELECT * FROM invalid_facts ORDER BY marked_at DESC LIMIT ?",
                (limit,),
            )

    @app.get("/api/rag-queries")
    def list_rag_queries(query_type: Optional[str] = None, limit: int = 100):
        with closing(_get_db()) as conn:
            if query_type:
                return _fetchall_safe(
                    conn,
                    "SELECT * FROM rag_query_log WHERE query_type = ? ORDER BY created_at DESC LIMIT ?",
                    (query_type, limit),
                )
            return _fetchall_safe(
                conn,
                "SELECT * FROM rag_query_log ORDER BY created_at DESC LIMIT ?",
                (limit,),
            )

    @app.get("/api/tool-stats")
    def list_tool_stats(tool_name: Optional[str] = None, limit: int = 200):
        with closing(_get_db()) as conn:
            if tool_name:
                return _fetchall_safe(
                    conn,
                    "SELECT * FROM tool_call_stats WHERE tool_name = ? ORDER BY created_at DESC LIMIT ?",
                    (tool_name, limit),
                )
            return _fetchall_safe(
                conn,
                "SELECT * FROM tool_call_stats ORDER BY created_at DESC LIMIT ?",
                (limit,),
            )

    @app.get("/api/checklist-scores")
    def list_checklist_scores(limit: int = 100):
        with closing(_get_db()) as conn:
            return _fetchall_safe(
                conn,
                "SELECT * FROM writing_checklist_scores ORDER BY chapter DESC LIMIT ?",
                (limit,),
            )

    @app.get("/api/story-events")
    def list_story_events(chapter: Optional[int] = None, limit: int = 200):
        with closing(_get_db()) as conn:
            if chapter is not None:
                rows = _fetchall_safe(
                    conn,
                    """
                    SELECT event_id, chapter, event_type, subject, payload_json, created_at
                    FROM story_events
                    WHERE chapter = ?
                    ORDER BY id DESC
                    LIMIT ?
                    """,
                    (chapter, limit),
                )
            else:
                rows = _fetchall_safe(
                    conn,
                    """
                    SELECT event_id, chapter, event_type, subject, payload_json, created_at
                    FROM story_events
                    ORDER BY chapter DESC, id DESC
                    LIMIT ?
                    """,
                    (limit,),
                )

        normalized = []
        for row in rows:
            payload = {}
            try:
                payload = json.loads(row.get("payload_json") or "{}")
            except json.JSONDecodeError:
                payload = {}
            normalized.append({**row, "payload": payload})
        return normalized

    @app.get("/api/story-events/health")
    def story_event_health():
        with closing(_get_db()) as conn:
            event_rows = _fetchall_safe(conn, "SELECT COUNT(*) AS count FROM story_events")
            proposal_rows = _fetchall_safe(
                conn,
                """
                SELECT COUNT(*) AS count
                FROM override_contracts
                WHERE record_type = 'amend_proposal' AND status = 'pending'
                """,
            )

        events_dir = _story_system_dir() / "events"
        file_count = len(list(events_dir.glob("chapter_*.events.json"))) if events_dir.is_dir() else 0
        return {
            "story_events": event_rows[0]["count"] if event_rows else 0,
            "pending_amend_proposals": proposal_rows[0]["count"] if proposal_rows else 0,
            "event_files": file_count,
        }

    # ===========================================================
    # API：文档浏览（正文/大纲/设定集 —— 只读）
    # ===========================================================

    @app.get("/api/files/tree")
    def file_tree():
        """列出 正文/、AI生成/、大纲/、设定集/ 四个目录的树结构。"""
        root = _get_project_root()
        result = {}
        for folder_name in ("正文", "AI生成", "大纲", "设定集"):
            folder = root / folder_name
            if not folder.is_dir():
                result[folder_name] = []
                continue
            result[folder_name] = _walk_tree(folder, root)
        return result

    @app.get("/api/files/read")
    def file_read(path: str):
        """只读读取一个文件内容（限 正文/大纲/设定集 目录）。"""
        root = _get_project_root()
        resolved = safe_resolve(root, path)

        # 二次限制：允许读取正文/AI生成/大纲/设定集 目录
        allowed_parents = [root / n for n in ("正文", "AI生成", "大纲", "设定集")]
        if not any(_is_child(resolved, p) for p in allowed_parents):
            raise HTTPException(403, "仅允许读取 正文/AI生成/大纲/设定集 目录下的文件")

        if not resolved.is_file():
            raise HTTPException(404, "文件不存在")

        # 文本文件直接读；其他情况返回占位信息
        try:
            content = resolved.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            content = "[二进制文件，无法预览]"

        return {"path": path, "content": content}

    class _FileWriteBody(BaseModel):
        path: str
        content: str

    def _allowed_write_parent(root: Path, resolved: Path) -> bool:
        allowed = [root / n for n in ("正文", "AI生成", "大纲", "设定集", ".ainovel/context")]
        return any(_is_child(resolved, p) for p in allowed)

    @app.post("/api/files/write")
    async def file_write(body: _FileWriteBody):
        """写入一个文本文件（限白名单目录），自动创建父目录。"""
        root = _get_project_root()
        resolved = safe_resolve(root, body.path)

        if not _allowed_write_parent(root, resolved):
            raise HTTPException(403, "仅允许写入 正文/大纲/设定集/.ainovel/context 目录下的文件")

        try:
            resolved.parent.mkdir(parents=True, exist_ok=True)
            resolved.write_text(body.content, encoding="utf-8")
        except OSError as exc:
            raise HTTPException(500, detail=f"文件写入失败: {exc}") from exc

        return {"ok": True, "path": body.path, "bytes": len(body.content.encode("utf-8"))}

    # ===========================================================
    # SSE：实时变更推送
    # ===========================================================

    @app.get("/api/events")
    async def sse():
        """Server-Sent Events 端点，推送 .ainovel/.story-system 的文件变更。"""
        q = _watcher.subscribe()

        async def _gen():
            try:
                while True:
                    try:
                        msg = await asyncio.wait_for(q.get(), timeout=15.0)
                        yield f"data: {msg}\n\n"
                    except asyncio.TimeoutError:
                        yield ": heartbeat\n\n"
            except asyncio.CancelledError:
                pass
            finally:
                _watcher.unsubscribe(q)

        return StreamingResponse(_gen(), media_type="text/event-stream")

    # ===========================================================
    # 前端静态文件托管
    # ===========================================================

    # 构建空窗期占位页（2026-08-10）：dist 被清空重建时前端请求 index.html 的兜底，
    # 返回「正在更新…」自动刷新页（2s 后 reload，构建完自动恢复），彻底杜绝 404 白屏。
    _BUILDING_PLACEHOLDER = """<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>正在更新…</title>
<style>
body{font-family:system-ui,-apple-system,sans-serif;display:flex;align-items:center;justify-content:center;height:100vh;margin:0;background:#faf7f2;color:#3b3a36}
.card{text-align:center}
.dots{display:flex;justify-content:center;gap:6px;margin-bottom:18px}
.dot{width:8px;height:8px;border-radius:50%;background:#b0392e;opacity:.25;animation:blink 1.2s infinite}
.dot:nth-child(2){animation-delay:.2s}.dot:nth-child(3){animation-delay:.4s}
@keyframes blink{0%,80%,100%{opacity:.25}40%{opacity:1}}
h1{font-size:18px;font-weight:600;margin:0 0 8px}
p{font-size:13px;opacity:.65;margin:0}
</style></head>
<body><div class="card"><div class="dots"><span class="dot"></span><span class="dot"></span><span class="dot"></span></div>
<h1>系统正在更新…</h1><p>前端构建中，页面将自动刷新，请稍候。</p></div>
<script>setTimeout(function(){location.reload()},2000)</script></body></html>"""

    if STATIC_DIR.is_dir():
        # 子类化 StaticFiles，前端构建后旧文件名缓存导致白屏/缺模块
        class _NoCacheStaticFiles(StaticFiles):
            def file_response(self, *args: Any, **kwargs: Any) -> Response:
                resp = super().file_response(*args, **kwargs)
                resp.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
                resp.headers["Pragma"] = "no-cache"
                resp.headers["Expires"] = "0"
                return resp

        app.mount("/assets", _NoCacheStaticFiles(directory=str(STATIC_DIR / "assets")), name="assets")

    # 【2026-08-10 彻底修复】无条件注册 SPA 兜底路由——不依赖启动时 dist 是否存在：
    # dist 存在 → 返回 index.html；dist 缺失/构建空窗 → 返回「构建中」占位页，绝不再 404。
    # 必须在 assets mount 之后注册（否则 `/{full_path:path}` 会吞掉 /assets/*）。
    @app.get("/{full_path:path}")
    def serve_spa(full_path: str):
        """SPA fallback：任何非 /api 路径都返回 index.html；dist 缺失时返回占位页。"""
        if full_path.startswith("api/"):
            raise HTTPException(404, "API 路径不存在")
        index = STATIC_DIR / "index.html"
        if index.is_file():
            resp = FileResponse(str(index))
            resp.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
            resp.headers["Pragma"] = "no-cache"
            resp.headers["Expires"] = "0"
            return resp
        return HTMLResponse(_BUILDING_PLACEHOLDER)

    # ===== Phase 2: 全局异常处理器 =====
    from .core.errors import install_error_handlers
    install_error_handlers(app)

    return app


# ---------------------------------------------------------------------------
# 辅助函数
# ---------------------------------------------------------------------------

def _walk_tree(folder: Path, root: Path) -> list[dict]:
    items = []
    for child in sorted(folder.iterdir()):
        rel = str(child.relative_to(root)).replace("\\", "/")
        if child.is_dir():
            items.append({"name": child.name, "type": "dir", "path": rel, "children": _walk_tree(child, root)})
        else:
            items.append({"name": child.name, "type": "file", "path": rel, "size": child.stat().st_size})
    return items


def _is_child(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False
