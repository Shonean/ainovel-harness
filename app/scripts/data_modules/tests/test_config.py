#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Config tests
"""

import os

from data_modules import config as config_module
from data_modules.config import DataModulesConfig, get_config


def test_config_paths_and_defaults(tmp_path):
    cfg = DataModulesConfig.from_project_root(tmp_path)
    assert cfg.project_root == tmp_path
    assert cfg.ainovel_dir.name == ".ainovel"
    assert cfg.state_file.name == "state.json"
    assert cfg.scratchpad_file.name == "memory_scratchpad.json"
    assert cfg.index_db.name == "index.db"
    assert cfg.rag_db.name == "rag.db"
    assert cfg.vector_db.name == "vectors.db"

    cfg.ensure_dirs()
    assert cfg.ainovel_dir.exists()


def test_get_config_explicit_root_no_cache(tmp_path):
    # 显式传 root：每次新建，无进程级单例缓存。
    cfg_a = get_config(tmp_path / "a")
    cfg_b = get_config(tmp_path / "b")
    assert cfg_a.project_root != cfg_b.project_root
    # 同一 root 两次取，是不同实例（无缓存残留）。
    assert get_config(tmp_path / "a").project_root == cfg_a.project_root


def test_load_user_env_reads_user_level(monkeypatch, tmp_path):
    # 用户级 .env 放在 AINOVEL_CLAUDE_HOME/ainovel-write/.env
    monkeypatch.setenv("AINOVEL_CLAUDE_HOME", str(tmp_path))
    user_env = tmp_path / "ainovel-write" / ".env"
    user_env.parent.mkdir(parents=True, exist_ok=True)
    user_env.write_text("EMBED_BASE_URL=https://example.com\n", encoding="utf-8")

    monkeypatch.delenv("EMBED_BASE_URL", raising=False)
    config_module.reset_user_env_loaded()
    assert config_module.load_user_env() is True
    assert os.environ.get("EMBED_BASE_URL") == "https://example.com"


def test_load_user_env_does_not_read_cwd(monkeypatch, tmp_path):
    # CWD 下放一个 .env（含 POISON），load_user_env 不应读取它。
    (tmp_path / ".env").write_text("EMBED_API_KEY=POISON\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("AINOVEL_CLAUDE_HOME", str(tmp_path / "elsewhere"))
    monkeypatch.delenv("EMBED_API_KEY", raising=False)
    config_module.reset_user_env_loaded()
    config_module.load_user_env()
    assert os.environ.get("EMBED_API_KEY") != "POISON"


def test_config_default_context_template_weights_dynamic_is_available(tmp_path):
    cfg = DataModulesConfig.from_project_root(tmp_path)
    dynamic = cfg.context_template_weights_dynamic

    assert isinstance(dynamic, dict)
    assert "early" in dynamic
    assert "mid" in dynamic
    assert "late" in dynamic
    assert "plot" in dynamic["early"]


def test_config_dynamic_template_weights_are_independent_instances(tmp_path):
    cfg1 = DataModulesConfig.from_project_root(tmp_path)
    cfg2 = DataModulesConfig.from_project_root(tmp_path)

    cfg1.context_template_weights_dynamic["early"]["plot"]["core"] = 0.77

    assert cfg2.context_template_weights_dynamic["early"]["plot"]["core"] != 0.77
