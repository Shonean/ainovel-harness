"""模块隔离 / 每本书独立于系统 — 隔离断言。

验证：
1. 两本书的数据路径（vectors.db / index.db / ainovel_dir）严格按 project_root 隔离；
2. get_config() 无进程级单例缓存——切换探测目标后不残留旧书的 config；
3. load_user_env 只读用户级 .env，不读 CWD .env，两用户上下文不互相串台；
4. import data_modules.config 不会因 CWD 下有 .env 而污染 os.environ。
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[3]
if str(PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT))

SCRIPTS_DIR = PLUGIN_ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))


def _make_book(root: Path) -> Path:
    ainovel = root / ".ainovel"
    ainovel.mkdir(parents=True, exist_ok=True)
    (ainovel / "state.json").write_text("{}", encoding="utf-8")
    return root


def test_two_books_have_distinct_data_paths(tmp_path):
    from data_modules.config import get_config

    book_a = _make_book(tmp_path / "bookA")
    book_b = _make_book(tmp_path / "bookB")

    cfg_a = get_config(book_a)
    cfg_b = get_config(book_b)

    assert cfg_a.vector_db != cfg_b.vector_db
    assert cfg_a.index_db != cfg_b.index_db
    assert cfg_a.ainovel_dir != cfg_b.ainovel_dir
    assert cfg_a.vector_db.parent == book_a / ".ainovel"
    assert cfg_b.vector_db.parent == book_b / ".ainovel"


def test_get_config_no_cache_between_calls(monkeypatch, tmp_path):
    """get_config() 无单例缓存：切换 AINOVEL_PROJECT_ROOT 后取到不同 root。"""
    from data_modules.config import get_config

    book_a = _make_book(tmp_path / "bookA")
    book_b = _make_book(tmp_path / "bookB")

    monkeypatch.setenv("AINOVEL_PROJECT_ROOT", str(book_a))
    root_a = get_config().project_root
    assert root_a == book_a

    # 若有进程级缓存，这里仍会返回 book_a —— 断言它现取到 book_b。
    monkeypatch.setenv("AINOVEL_PROJECT_ROOT", str(book_b))
    root_b = get_config().project_root
    assert root_b == book_b
    assert root_a != root_b


def test_user_env_shell_overrides_file(monkeypatch, tmp_path):
    """显式 shell 环境变量优先于用户级 .env（override=False）。"""
    from data_modules import config as config_module

    home = tmp_path / "home"
    d = home / "ainovel-write"
    d.mkdir(parents=True, exist_ok=True)
    (d / ".env").write_text("EMBED_API_KEY=from-file\n", encoding="utf-8")

    monkeypatch.setenv("AINOVEL_CLAUDE_HOME", str(home))
    monkeypatch.setenv("EMBED_API_KEY", "from-shell")
    config_module.reset_user_env_loaded()
    config_module.load_user_env()
    assert os.environ.get("EMBED_API_KEY") == "from-shell"

    # force 重读也不会覆盖已存在的 shell 值（override=False 语义一致）。
    config_module.load_user_env(force=True)
    assert os.environ.get("EMBED_API_KEY") == "from-shell"


def test_import_does_not_load_cwd_env(tmp_path):
    """import data_modules.config 不应因 CWD 下有 .env 而污染 os.environ。"""
    poison_dir = tmp_path / "poison_cwd"
    poison_dir.mkdir(parents=True, exist_ok=True)
    (poison_dir / ".env").write_text("EMBED_API_KEY=POISON_VALUE\n", encoding="utf-8")

    code = (
        "import os, sys; sys.path.insert(0, %r); sys.path.insert(0, %r); "
        "import data_modules.config as c; "
        "print(os.environ.get('EMBED_API_KEY', 'UNSET'))"
    ) % (str(PLUGIN_ROOT), str(SCRIPTS_DIR))

    env = dict(os.environ)
    env.pop("EMBED_API_KEY", None)
    # 给一个独立的用户级 home，避免真实用户 .env 干扰断言。
    empty_home = tmp_path / "empty_home"
    (empty_home / "ainovel-write").mkdir(parents=True, exist_ok=True)
    env["AINOVEL_CLAUDE_HOME"] = str(empty_home)

    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=str(poison_dir),
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "UNSET"
