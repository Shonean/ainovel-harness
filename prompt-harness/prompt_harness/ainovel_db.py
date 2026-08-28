# -*- coding: utf-8 -*-
"""AInovel 统一数据库（data/ainovel.db）—— codex-rs/state 风格的 SQLite 基础设施。

单库多域 + 版本化迁移 + WAL + VACUUM INTO 自动快照。
主系统（app/dashboard）与 prompt-harness 双进程共用同一文件：
- 每线程独立连接（thread-local），WAL + busy_timeout 保证跨进程并发安全
- run_migrations() 幂等：schema_migrations 记版本，代码内有序迁移列表
- backup_now() 一致性快照到 data/backups/，保留最近 KEEP_BACKUPS 份

路径解析：环境变量 AINOVEL_DB_PATH > 仓库内 data/ainovel.db（默认）。
"""
from __future__ import annotations

import os
import re
import sqlite3
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

DB_FILENAME = "ainovel.db"
BACKUP_DIRNAME = "backups"
KEEP_BACKUPS = 10
BACKUP_INTERVAL_S = 6 * 3600
BUSY_TIMEOUT_MS = 5000

# ── 版本化迁移（codex migrations 风格的 Python stdlib 版）──
# 每条 (version, sql)；version 严格递增，只追加不修改。
_MIGRATIONS: list[tuple[int, str]] = [
    (1, """
CREATE TABLE IF NOT EXISTS schema_migrations(
    version INTEGER PRIMARY KEY,
    applied_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS plot_templates(
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL DEFAULT '',
    description TEXT NOT NULL DEFAULT '',
    archetype TEXT NOT NULL DEFAULT '',
    corpus TEXT NOT NULL DEFAULT '',
    chapter_num INTEGER,
    source_json TEXT,
    fields_json TEXT,
    levels_json TEXT,
    skeleton_json TEXT,
    qualified_json TEXT,
    entities_json TEXT,
    extra_json TEXT,
    use_count INTEGER NOT NULL DEFAULT 0,
    last_used_at TEXT,
    avg_quality REAL,
    n_finalized INTEGER NOT NULL DEFAULT 0,
    created_at TEXT,
    seq INTEGER
);
CREATE INDEX IF NOT EXISTS idx_pt_archetype ON plot_templates(archetype);
CREATE INDEX IF NOT EXISTS idx_pt_corpus ON plot_templates(corpus);

CREATE TABLE IF NOT EXISTS template_embeddings(
    template_id TEXT PRIMARY KEY REFERENCES plot_templates(id) ON DELETE CASCADE,
    vec BLOB NOT NULL
);

CREATE TABLE IF NOT EXISTS api_presets(
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    kind TEXT NOT NULL DEFAULT 'text',
    base_url TEXT NOT NULL DEFAULT '',
    model TEXT NOT NULL DEFAULT '',
    api_key TEXT NOT NULL DEFAULT '',
    fields_json TEXT NOT NULL DEFAULT '{}',
    is_current INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT
);

CREATE TABLE IF NOT EXISTS llm_calls(
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    model TEXT,
    endpoint TEXT,
    call_type TEXT,
    status TEXT,
    error TEXT,
    prompt_len INTEGER,
    completion_len INTEGER,
    prompt_tokens INTEGER,
    completion_tokens INTEGER,
    total_tokens INTEGER,
    prompt_cache_hit_tokens INTEGER,
    latency_ms INTEGER,
    retry_count INTEGER,
    cost_usd REAL
);
CREATE INDEX IF NOT EXISTS idx_llm_ts ON llm_calls(ts);
CREATE INDEX IF NOT EXISTS idx_llm_calltype ON llm_calls(call_type, status);

CREATE TABLE IF NOT EXISTS template_hits(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    book TEXT,
    arc_name TEXT,
    template_id TEXT,
    template_name TEXT,
    score REAL,
    matched_via TEXT
);
CREATE INDEX IF NOT EXISTS idx_th_ts ON template_hits(ts);

CREATE TABLE IF NOT EXISTS extract_results(
    task_id TEXT PRIMARY KEY,
    corpus TEXT NOT NULL DEFAULT '',
    arc_name TEXT NOT NULL DEFAULT '',
    total INTEGER,
    qualified INTEGER,
    skipped INTEGER,
    arc_count INTEGER,
    created_at TEXT,
    payload TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_er_corpus ON extract_results(corpus);

CREATE TABLE IF NOT EXISTS corpus_chapters(
    book TEXT NOT NULL,
    chapter_num INTEGER NOT NULL,
    char_len INTEGER,
    path TEXT,
    PRIMARY KEY(book, chapter_num)
);

CREATE TABLE IF NOT EXISTS reading_positions(
    book TEXT PRIMARY KEY,
    position TEXT NOT NULL DEFAULT '',
    updated_at TEXT
);

CREATE TABLE IF NOT EXISTS meta(
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL DEFAULT ''
);
"""),
]

_local = threading.local()
_cfg_lock = threading.Lock()
_db_override: Path | None = None
_last_backup_ts = 0.0


def _default_db_path() -> Path:
    """默认：仓库 ainovel-write/data/ainovel.db（本文件在 prompt-harness/prompt_harness/ 下）。"""
    return Path(__file__).resolve().parents[2] / "data" / DB_FILENAME


def configure(path: str | Path | None = None) -> Path:
    """设定库路径（进程级，通常启动时调一次）；不传则用默认/环境变量。"""
    global _db_override
    with _cfg_lock:
        if path is not None:
            _db_override = Path(path)
        elif _db_override is None:
            env = os.environ.get("AINOVEL_DB_PATH")
            _db_override = Path(env) if env else _default_db_path()
        p = _db_override
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def db_path() -> Path:
    return configure() if _db_override is None else _db_override


def _connect_raw(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path), timeout=BUSY_TIMEOUT_MS / 1000)
    conn.row_factory = sqlite3.Row
    conn.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS}")
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


def run_migrations(conn: sqlite3.Connection) -> None:
    """按版本号补跑未应用的迁移；幂等，进程/线程安全（库级写锁由 SQLite 保证）。"""
    conn.execute(
        "CREATE TABLE IF NOT EXISTS schema_migrations("
        "version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)")
    applied = {r[0] for r in conn.execute("SELECT version FROM schema_migrations")}
    for version, sql in _MIGRATIONS:
        if version in applied:
            continue
        conn.executescript(sql)
        conn.execute(
            "INSERT INTO schema_migrations(version, applied_at) VALUES(?,?)",
            (version, datetime.now(timezone.utc).isoformat()))
        conn.commit()


def get_conn() -> sqlite3.Connection:
    """当前线程的连接（懒建）。线程结束连接随线程销毁，不跨线程共享。"""
    conn = getattr(_local, "conn", None)
    if conn is None:
        conn = _connect_raw(db_path())
        run_migrations(conn)
        _local.conn = conn
    return conn


def close_current_thread_conn() -> None:
    conn = getattr(_local, "conn", None)
    if conn is not None:
        try:
            conn.close()
        except Exception:
            pass
        _local.conn = None


# ── 自动快照备份 ──

def backup_now(force: bool = False) -> Path | None:
    """VACUUM INTO 一致性快照；距上次不足 BACKUP_INTERVAL_S 且非 force 则跳过。
    返回快照路径；失败返回 None（备份失败不阻断主流程）。"""
    global _last_backup_ts
    now = time.time()
    if not force and now - _last_backup_ts < BACKUP_INTERVAL_S:
        return None
    with _cfg_lock:
        if not force and now - _last_backup_ts < BACKUP_INTERVAL_S:
            return None
        _last_backup_ts = now
    try:
        bdir = db_path().parent / BACKUP_DIRNAME
        bdir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        target = bdir / f"ainovel-{stamp}.db"
        src = _connect_raw(db_path())
        try:
            src.execute("VACUUM INTO ?", (str(target),))
        finally:
            src.close()
        # 清旧：只留最近 KEEP_BACKUPS 份
        snaps = sorted(bdir.glob("ainovel-*.db"))
        for old in snaps[:-KEEP_BACKUPS] if len(snaps) > KEEP_BACKUPS else []:
            try:
                old.unlink()
            except OSError:
                pass
        return target
    except Exception:
        return None


def maybe_backup() -> None:
    """惰性触发：距上次快照超过间隔才真正落盘（给高频写路径顺手调）。"""
    backup_now(force=False)


def init_db(path: str | Path | None = None) -> Path:
    """启动挂载点：设路径 + 建表迁移 + 首次快照。"""
    p = configure(path)
    conn = get_conn()
    run_migrations(conn)
    backup_now(force=True)
    return p


def meta_get(key: str, default: str = "") -> str:
    row = get_conn().execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    return row[0] if row else default


def meta_set(key: str, value: str) -> None:
    get_conn().execute(
        "INSERT INTO meta(key,value) VALUES(?,?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))
    get_conn().commit()
