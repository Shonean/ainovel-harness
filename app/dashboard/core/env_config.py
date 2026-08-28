"""
env_config.py — 用户级 .env 的可视化读写。

用户级 .env（~/.claude/ainovel-write/.env）是 API key 的单一事实源。
本模块提供：
- ENV_FIELDS：可配置字段元数据（key/label/分组/是否敏感）
- read_env_config()：读取各字段当前值，敏感字段返回掩码（永不明文回显）
- write_env_config(updates)：写入 .env（保留注释与未提及行）+ 更新当前进程
  os.environ + 重新加载，使下一次 workflow 启动的 agent 立即用新值。

安全：API key 等敏感值在响应中只返回掩码；POST 时前端发明文（localhost 可接受）。
"""

from __future__ import annotations

import os
from pathlib import Path

# 可配置字段元数据。secret=True 的字段在 GET 时只返回掩码。
ENV_FIELDS = [
    {"key": "ARK_API_KEY",   "label": "主力模型 API Key",  "group": "主力模型（Ark）", "secret": True,  "placeholder": "ark-..."},
    {"key": "ARK_BASE_URL",  "label": "主力模型 Base URL",  "group": "主力模型（Ark）", "secret": False, "placeholder": "https://ark.cn-beijing.volces.com/api/coding/v3"},
    {"key": "ARK_MODEL_PRO",      "label": "主力模型 ID（正文/大纲/审查）", "group": "主力模型（Ark）", "secret": False, "placeholder": "doubao-1.5-pro-32k"},
    {"key": "ARK_MODEL_CHARACTER", "label": "人物模型 ID（对话/情绪润色）", "group": "人物模型（Ark）", "secret": False, "placeholder": "Doubao-Seed-Character"},
    {"key": "ARK_BASE_URL_CHARACTER", "label": "人物模型 Base URL", "group": "人物模型（Ark）", "secret": False, "placeholder": "留空则使用上方'主力模型 Base URL'"},
    {"key": "ARK_API_KEY_CHARACTER", "label": "人物模型 API Key", "group": "人物模型（Ark）", "secret": True, "placeholder": "ark-...（留空则使用上方'主力模型 API Key'）"},
    {"key": "EMBED_API_KEY", "label": "Embedding API Key",  "group": "Embedding",      "secret": True,  "placeholder": "ark-...（可与主力模型 key 相同）"},
    {"key": "EMBED_BASE_URL","label": "Embedding Base URL", "group": "Embedding",      "secret": False, "placeholder": "https://ark.cn-beijing.volces.com/api/coding/v3"},
    {"key": "EMBED_MODEL",   "label": "Embedding 模型 ID",  "group": "Embedding",      "secret": False, "placeholder": "doubao-embedding-vision-251215"},
    {"key": "RERANK_API_KEY","label": "Rerank API Key",     "group": "Rerank（可选）",  "secret": True,  "placeholder": "留空则降级 BM25"},
    {"key": "RERANK_BASE_URL","label": "Rerank Base URL",   "group": "Rerank（可选）",  "secret": False, "placeholder": "https://api.jina.ai/v1"},
    {"key": "RERANK_MODEL",  "label": "Rerank 模型 ID",     "group": "Rerank（可选）",  "secret": False, "placeholder": "jina-reranker-v3"},
]

_SECRET_KEYS = {f["key"] for f in ENV_FIELDS if f["secret"]}


def _user_env_path() -> Path:
    try:
        from data_modules.config import _user_env_path as _p
        return _p()
    except Exception:
        return Path.home() / ".claude" / "ainovel-write" / ".env"


def _mask(value: str) -> str:
    """敏感值掩码：保留前 4 + 后 4，中间星号；过短则全星号。"""
    if not value:
        return ""
    if len(value) <= 8:
        return "*" * len(value)
    return f"{value[:4]}{'*' * (len(value) - 8)}{value[-4:]}"


def read_env_config() -> list[dict]:
    """读取各字段当前值（从 os.environ，已含 .env 加载结果），敏感字段掩码。"""
    out = []
    for f in ENV_FIELDS:
        key = f["key"]
        raw = os.environ.get(key, "")
        configured = bool(raw.strip())
        if f["secret"]:
            display = _mask(raw) if configured else ""
        else:
            display = raw
        out.append({
            "key": key,
            "label": f["label"],
            "group": f["group"],
            "secret": f["secret"],
            "placeholder": f.get("placeholder", ""),
            "configured": configured,
            "value": display,
        })
    return out


def _parse_env_lines(text: str) -> list[list[str]]:
    """把 .env 文本解析成 [原始行, key] 列表；key 为 '' 表示注释/空行/无等号行。"""
    parsed = []
    for line in text.splitlines():
        stripped = line.strip()
        key = ""
        if stripped and not stripped.startswith("#") and "=" in stripped:
            key = stripped.split("=", 1)[0].strip()
        parsed.append([line, key])
    return parsed


def write_env_config(updates: dict) -> None:
    """写入 .env（保留注释与未提及行）+ 更新 os.environ + 重新加载。

    updates: {KEY: value}；value 为 '' 表示清空该字段（写入 KEY=）。
    """
    path = _user_env_path()
    path.parent.mkdir(parents=True, exist_ok=True)

    # 1) 读现有内容
    text = ""
    if path.exists():
        text = path.read_text(encoding="utf-8")
    parsed = _parse_env_lines(text)

    remaining = dict(updates)
    new_lines: list[str] = []

    # 2) 逐行替换已存在的 key
    for line, key in parsed:
        if key in remaining:
            new_lines.append(f"{key}={remaining[key]}")
            del remaining[key]
        else:
            new_lines.append(line)

    # 3) 追加 .env 里没有的新 key
    if remaining:
        if new_lines and new_lines[-1].strip() != "":
            new_lines.append("")
        for key, value in remaining.items():
            new_lines.append(f"{key}={value}")

    # 4) 写回（UTF-8 无 BOM，避免 dotenv 解析问题）
    path.write_text("\n".join(new_lines) + "\n", encoding="utf-8")

    # 5) 更新当前进程 os.environ（override，因为用户显式改）
    for key, value in updates.items():
        os.environ[key] = value

    # 6) 复位 config 的幂等标志并强制重载，使 dataclass default_factory 下次读到新值
    try:
        from data_modules.config import reset_user_env_loaded, load_user_env
        reset_user_env_loaded()
        load_user_env(force=True)
    except Exception:
        pass
