"""Tier-A：Ark 接入验证 — _build_url 对多种 base 的拼接 + ARK_* 配置优先级。"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

PLUGIN_ROOT = Path(__file__).resolve().parents[3]
if str(PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT))


def _client(base_url, api_key="k"):
    from data_modules.config import DataModulesConfig
    from data_modules.api_client import EmbeddingAPIClient
    cfg = DataModulesConfig(
        project_root=Path("."),
        embed_base_url=base_url,
        embed_api_key=api_key,
        embed_model="m",
    )
    return EmbeddingAPIClient(cfg)


def test_build_url_ark_coding_v3():
    c = _client("https://ark.cn-beijing.volces.com/api/coding/v3")
    assert c._build_url() == "https://ark.cn-beijing.volces.com/api/coding/v3/embeddings"


def test_build_url_plain_v1():
    c = _client("https://api.openai.com/v1")
    assert c._build_url() == "https://api.openai.com/v1/embeddings"


def test_build_url_bare_host():
    c = _client("https://my-host.example.com")
    assert c._build_url() == "https://my-host.example.com/v1/embeddings"


def test_build_url_already_embeddings():
    c = _client("https://host/v1/embeddings")
    assert c._build_url() == "https://host/v1/embeddings"


def test_build_url_trailing_slash():
    c = _client("https://ark.cn-beijing.volces.com/api/coding/v3/")
    assert c._build_url() == "https://ark.cn-beijing.volces.com/api/coding/v3/embeddings"


def test_rerank_build_url_ark():
    from data_modules.config import DataModulesConfig
    from data_modules.api_client import RerankAPIClient
    cfg = DataModulesConfig(
        project_root=Path("."),
        rerank_base_url="https://ark.cn-beijing.volces.com/api/coding/v3",
        rerank_api_key="k",
        rerank_model="m",
    )
    assert RerankAPIClient(cfg)._build_url() == \
        "https://ark.cn-beijing.volces.com/api/coding/v3/rerank"


def test_agent_runner_default_model_priority(monkeypatch):
    pytest.importorskip("openai")
    monkeypatch.setenv("ARK_API_KEY", "k")
    monkeypatch.delenv("ANTHROPIC_MODEL", raising=False)
    # ARK_MODEL_PRO 优先（主力模型环境变量）
    monkeypatch.setenv("ARK_MODEL_PRO", "my-custom-model")
    from dashboard.agent_runner import _default_model
    assert _default_model() == "my-custom-model"
    # 无 ARK_MODEL_PRO 时兜底为 doubao-1.5-pro-32k
    monkeypatch.delenv("ARK_MODEL_PRO")
    assert _default_model() == "doubao-1.5-pro-32k"


def test_agent_runner_client_uses_ark_env(monkeypatch):
    pytest.importorskip("openai")
    monkeypatch.setenv("ARK_API_KEY", "ark-key-123")
    monkeypatch.setenv("ARK_BASE_URL", "https://ark.cn-beijing.volces.com/api/coding/v3")
    from dashboard.agent_runner import AnthropicAgentRunner
    r = AnthropicAgentRunner(project_root=Path("."), system_prompt="", allowed_tools=[])
    assert r._client.api_key == "ark-key-123"
    assert str(r._client.base_url).rstrip("/") == \
        "https://ark.cn-beijing.volces.com/api/coding/v3"
