from __future__ import annotations

import os
import shutil
import sqlite3
import tempfile
import uuid
from pathlib import Path

import pytest


_ORIGINAL_SQLITE_CONNECT = sqlite3.connect
_ORIGINAL_TEMPORARY_DIRECTORY = tempfile.TemporaryDirectory


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _tmp_root() -> Path:
    root = _repo_root() / ".tmp" / "pytest"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _safe_mkdtemp(suffix: str | None = None, prefix: str | None = None, dir: str | os.PathLike[str] | None = None) -> str:
    """Avoid WindowsApps Python creating inaccessible 0o700 temp dirs."""
    suffix = "" if suffix is None else suffix
    prefix = "tmp" if prefix is None else prefix
    root = Path(dir) if dir is not None else _tmp_root()
    root.mkdir(parents=True, exist_ok=True)

    for _ in range(100):
        path = root / f"{prefix}{uuid.uuid4().hex}{suffix}"
        try:
            path.mkdir()
        except FileExistsError:
            continue
        return str(path.resolve())

    raise FileExistsError(f"Unable to create unique temporary directory under {root}")


def _install_safe_tempfile() -> None:
    root = _tmp_root()
    for name in ("TMP", "TEMP", "TMPDIR"):
        os.environ[name] = str(root)
    os.environ["AINOVEL_TEST_RELAX_ATOMIC_REPLACE"] = "1"
    tempfile.tempdir = str(root)
    tempfile.mkdtemp = _safe_mkdtemp
    tempfile.TemporaryDirectory = _SafeTemporaryDirectory


class _SafeTemporaryDirectory(_ORIGINAL_TEMPORARY_DIRECTORY):
    def __init__(self, suffix=None, prefix=None, dir=None, ignore_cleanup_errors=True, *, delete=True):
        super().__init__(
            suffix=suffix,
            prefix=prefix,
            dir=dir,
            ignore_cleanup_errors=ignore_cleanup_errors,
            delete=delete,
        )


def _safe_sqlite_connect(*args, **kwargs):
    conn = _ORIGINAL_SQLITE_CONNECT(*args, **kwargs)
    try:
        conn.execute("PRAGMA journal_mode=MEMORY")
    except sqlite3.DatabaseError:
        pass
    return conn


def _install_safe_sqlite() -> None:
    sqlite3.connect = _safe_sqlite_connect


def _isolate_global_state() -> None:
    """隔离用户级全局状态，避免测试污染真实环境。

    project_locator._get_user_claude_root() 运行时（非 import 时）读取
    AINOVEL_CLAUDE_HOME / CLAUDE_HOME，定位全局注册表
    ~/.claude/ainovel-write/workspaces.json 与 .claude/.ainovel-current-project。
    若不隔离，跑 ainovel-init / write_current_project_pointer 的测试会把
    last_used_project_root 等写进用户真实注册表，破坏模块隔离（一进程一书）。

    这里设一个默认临时 home；自设 AINOVEL_CLAUDE_HOME 的测试用 monkeypatch
    覆盖此值，互不冲突。
    """
    home = _tmp_root() / "claude_home"
    home.mkdir(parents=True, exist_ok=True)
    os.environ["AINOVEL_CLAUDE_HOME"] = str(home)


def pytest_configure(config: pytest.Config) -> None:
    _install_safe_tempfile()
    _install_safe_sqlite()
    _isolate_global_state()


@pytest.fixture
def tmp_path(request: pytest.FixtureRequest) -> Path:
    safe_name = "".join(ch if ch.isalnum() or ch in ("-", "_") else "_" for ch in request.node.name)
    # Windows MAX_PATH(260)：超长测试名（如 96 字符的技能流测试）+ 深层中文路径前缀
    # 会让 .story-system/chapters/chapter_NNN_<random>.tmp 的完整路径超过 260。
    # 此时 Path.exists()/mkdir 走长路径 API 仍成功，但 tempfile.mkstemp→os.open
    # 不加 \\?\ 前缀，会抛误导性的 FileNotFoundError。截断测试名（唯一性由 UUID 保证）。
    if len(safe_name) > 40:
        import hashlib

        digest = hashlib.md5(safe_name.encode("utf-8")).hexdigest()[:8]
        safe_name = safe_name[:32] + "_" + digest
    path = _tmp_root() / f"{safe_name}_{uuid.uuid4().hex}"
    path.mkdir(parents=True, exist_ok=False)
    try:
        yield path
    finally:
        if os.environ.get("AINOVEL_KEEP_TEST_TMP") != "1":
            shutil.rmtree(path, ignore_errors=True)


_install_safe_tempfile()
_install_safe_sqlite()
