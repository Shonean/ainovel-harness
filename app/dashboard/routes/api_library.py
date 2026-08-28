"""
api_library.py - API 预设库（文字模型多预设 + 向量模型全局共用）。

存储：~/.claude/ainovel-write/api_library.json
  {
    "text_presets": [{"id","name","fields":{ARK_API_KEY, ARK_BASE_URL, ARK_MODEL_PRO}}],
    "current_text_id": "...",
    "embed_presets": [{"id","name","fields":{EMBED_API_KEY, EMBED_BASE_URL, EMBED_MODEL}}],
    "current_embed_id": "...",
    "embed_config": {"fields": {...}}   // 旧单配置字段，向后兼容，恒等于当前向量预设
  }

文字模型 / 向量模型：均多组预设，可一键切换
向量旧「单配置」自动迁移为首个向量预设（embed_presets）

- 向后兼容：加载时自动从旧结构（presets + current_id）迁移到新结构
- 敏感字段（API_KEY）list 时掩码，存储明文（与 .env 同级安全）
"""
from __future__ import annotations

import json
import os
import uuid
from pathlib import Path

from ..core.env_config import _mask, _user_env_path, write_env_config


# ============================================================
# 字段定义
# ============================================================

TEXT_PRESET_FIELDS = [
    {"key": "ARK_API_KEY",   "label": "API Key",  "group": "文字模型", "secret": True,  "placeholder": "ark-..."},
    {"key": "ARK_BASE_URL",  "label": "Base URL", "group": "文字模型", "secret": False, "placeholder": "https://ark.cn-beijing.volces.com/api/coding/v3"},
    {"key": "ARK_MODEL_PRO", "label": "模型 ID",  "group": "文字模型", "secret": False, "placeholder": "doubao-seed-2.0-lite"},
]
TEXT_PRESET_KEYS = [f["key"] for f in TEXT_PRESET_FIELDS]
TEXT_SECRET_KEYS = {f["key"] for f in TEXT_PRESET_FIELDS if f["secret"]}

EMBED_CONFIG_FIELDS = [
    {"key": "EMBED_API_KEY",  "label": "API Key",  "group": "向量模型", "secret": True,  "placeholder": "ark-...（可与文字模型 key 相同）"},
    {"key": "EMBED_BASE_URL", "label": "Base URL", "group": "向量模型", "secret": False, "placeholder": "https://ark.cn-beijing.volces.com/api/coding/v3"},
    {"key": "EMBED_MODEL",    "label": "模型 ID",  "group": "向量模型", "secret": False, "placeholder": "doubao-embedding-vision-251215"},
]
EMBED_CONFIG_KEYS = [f["key"] for f in EMBED_CONFIG_FIELDS]
EMBED_SECRET_KEYS = {f["key"] for f in EMBED_CONFIG_FIELDS if f["secret"]}


# ============================================================
# 存储读写
# ============================================================

def _library_path() -> Path:
    return _user_env_path().parent / "api_library.json"


def _migrate_old_format(data: dict) -> bool:
    """如果是旧结构（presets + current_id），迁移到新结构。
    返回 True 表示发生了迁移。
    """
    # 有 text_presets 说明已经是新结构
    if "text_presets" in data:
        return False
    # 没有 presets 说明是空文件，也不用迁移，直接走新结构
    if "presets" not in data:
        return False

    old_presets = data.get("presets", [])
    old_current = data.get("current_id")

    # 文字预设：抽取 ARK_* 字段
    text_presets = []
    current_text_id = None
    for p in old_presets:
        old_fields = p.get("fields", {})
        new_fields = {k: old_fields.get(k, "") for k in TEXT_PRESET_KEYS}
        new_p = {"id": p["id"], "name": p.get("name", ""), "fields": new_fields}
        text_presets.append(new_p)
        if p["id"] == old_current:
            current_text_id = p["id"]

    # 向量配置：取当前预设的 EMBED_* 字段
    embed_fields = {k: "" for k in EMBED_CONFIG_KEYS}
    if old_current:
        for p in old_presets:
            if p["id"] == old_current:
                old_fields = p.get("fields", {})
                for k in EMBED_CONFIG_KEYS:
                    embed_fields[k] = old_fields.get(k, "")
                break
    # 如果当前预设没向量配置，取第一个有配置的预设
    if not embed_fields.get("EMBED_MODEL"):
        for p in old_presets:
            old_fields = p.get("fields", {})
            if old_fields.get("EMBED_MODEL"):
                for k in EMBED_CONFIG_KEYS:
                    embed_fields[k] = old_fields.get(k, "")
                break

    data["text_presets"] = text_presets
    data["current_text_id"] = current_text_id
    data["embed_config"] = {"fields": embed_fields}

    # 删除旧字段
    data.pop("presets", None)
    data.pop("current_id", None)

    return True


def _load() -> dict:
    p = _library_path()
    if not p.is_file():
        return {"text_presets": [], "current_text_id": None,
                "embed_presets": [], "current_embed_id": None,
                "embed_config": {"fields": {}}}
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
        if not isinstance(d, dict):
            d = {}
    except (OSError, json.JSONDecodeError):
        d = {}

    # 确保新结构字段存在
    migrated = _migrate_old_format(d)
    d.setdefault("text_presets", [])
    d.setdefault("current_text_id", None)
    d.setdefault("embed_config", {"fields": {}})
    if "fields" not in d["embed_config"]:
        d["embed_config"]["fields"] = {}

    # 向量多预设迁移：旧「单配置 embed_config」→ 首个向量预设
    if "embed_presets" not in d:
        d["embed_presets"] = []
        d["current_embed_id"] = None
        ec = (d.get("embed_config") or {}).get("fields") or {}
        if any(ec.values()):
            eid = uuid.uuid4().hex[:8]
            d["embed_presets"].append({"id": eid, "name": "默认向量", "fields": dict(ec)})
            d["current_embed_id"] = eid
        migrated = True
    d.setdefault("embed_presets", [])
    d.setdefault("current_embed_id", None)

    if migrated:
        _save(d)  # 迁移后写回

    return d


def _save(data: dict) -> None:
    p = _library_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


# ============================================================
# 工具函数
# ============================================================

def _mask_text_fields(fields: dict) -> dict:
    return {k: (_mask(v) if k in TEXT_SECRET_KEYS and v else v) for k, v in fields.items()}


def _mask_embed_fields(fields: dict) -> dict:
    return {k: (_mask(v) if k in EMBED_SECRET_KEYS and v else v) for k, v in fields.items()}


def text_field_metadata() -> list[dict]:
    """供前端渲染文字模型预设表单。"""
    return [
        {"key": f["key"], "label": f["label"], "group": f["group"],
         "secret": f["secret"], "placeholder": f.get("placeholder", "")}
        for f in TEXT_PRESET_FIELDS
    ]


def embed_field_metadata() -> list[dict]:
    """供前端渲染向量模型配置表单。"""
    return [
        {"key": f["key"], "label": f["label"], "group": f["group"],
         "secret": f["secret"], "placeholder": f.get("placeholder", "")}
        for f in EMBED_CONFIG_FIELDS
    ]


# ============================================================
# 列表 / 元数据
# ============================================================

def list_all() -> dict:
    """返回完整结构：文字/向量预设列表 + 当前 id + 向量旧字段（均掩码）+ 字段元数据。"""
    data = _load()
    cur = data.get("current_text_id")

    text_presets = [
        {
            "id": p["id"],
            "name": p.get("name", ""),
            "fields": _mask_text_fields(p.get("fields", {})),
            "is_current": p["id"] == cur,
        }
        for p in data.get("text_presets", [])
    ]

    cur_embed = data.get("current_embed_id")
    embed_presets = [
        {
            "id": p["id"],
            "name": p.get("name", ""),
            "fields": _mask_embed_fields(p.get("fields", {})),
            "is_current": p["id"] == cur_embed,
        }
        for p in data.get("embed_presets", [])
    ]

    # 旧 embed_config 字段向后兼容：恒等于当前向量预设
    cur_embed_fields = {}
    if cur_embed:
        cp = _get_embed_preset(data, cur_embed)
        if cp:
            cur_embed_fields = _mask_embed_fields(cp.get("fields", {}))
    embed_config = {"fields": cur_embed_fields}

    return {
        "text_presets": text_presets,
        "current_text_id": cur,
        "embed_presets": embed_presets,
        "current_embed_id": cur_embed,
        "embed_config": embed_config,
        "text_fields": text_field_metadata(),
        "embed_fields": embed_field_metadata(),
    }


# ============================================================
# 文字模型预设 CRUD
# ============================================================

def _get_text_preset(data: dict, preset_id: str) -> dict | None:
    for p in data.get("text_presets", []):
        if p["id"] == preset_id:
            return p
    return None


def get_text_preset_fields(preset_id: str) -> dict | None:
    """返回文字模型预设的明文字段。"""
    data = _load()
    p = _get_text_preset(data, preset_id)
    if p is None:
        return None
    return dict(p.get("fields", {}))


def create_text_preset(name: str, fields: dict) -> str:
    data = _load()
    pid = uuid.uuid4().hex[:8]
    data["text_presets"].append({
        "id": pid,
        "name": name or "未命名预设",
        "fields": {k: (fields.get(k, "") or "") for k in TEXT_PRESET_KEYS},
    })
    _save(data)
    return pid


def update_text_preset(preset_id: str, name: str | None, fields: dict) -> bool:
    data = _load()
    p = _get_text_preset(data, preset_id)
    if p is None:
        return False

    if name:
        p["name"] = name

    old = p.get("fields", {})
    new_fields = {}
    for k in TEXT_PRESET_KEYS:
        v = fields.get(k, None)
        if v is None:
            new_fields[k] = old.get(k, "")
        elif k in TEXT_SECRET_KEYS and v == "":
            # 空提交=保留原值（前端掩码显示时提交空）
            new_fields[k] = old.get(k, "")
        else:
            new_fields[k] = v
    p["fields"] = new_fields

    _save(data)
    return True


def delete_text_preset(preset_id: str) -> bool:
    data = _load()
    before = len(data.get("text_presets", []))
    data["text_presets"] = [p for p in data.get("text_presets", []) if p["id"] != preset_id]
    if data.get("current_text_id") == preset_id:
        data["current_text_id"] = None
    _save(data)
    return len(data["text_presets"]) < before


def apply_text_preset(preset_id: str) -> dict:
    """应用文字模型预设：把 ARK_* 字段写入 .env，记 current_text_id。"""
    fields = get_text_preset_fields(preset_id)
    if fields is None:
        raise KeyError(f"预设不存在: {preset_id}")

    updates = {k: v for k, v in fields.items() if v}
    if updates:
        write_env_config(updates)

    data = _load()
    data["current_text_id"] = preset_id
    _save(data)

    return {"applied_keys": list(updates.keys()), "current_text_id": preset_id}


# ============================================================
# 向量模型预设 CRUD
# ============================================================

def _get_embed_preset(data: dict, preset_id: str) -> dict | None:
    for p in data.get("embed_presets", []):
        if p["id"] == preset_id:
            return p
    return None


def get_embed_preset_fields(preset_id: str) -> dict | None:
    """返回向量模型预设的明文字段。"""
    data = _load()
    p = _get_embed_preset(data, preset_id)
    if p is None:
        return None
    return dict(p.get("fields", {}))


def create_embed_preset(name: str, fields: dict) -> str:
    data = _load()
    pid = uuid.uuid4().hex[:8]
    data["embed_presets"].append({
        "id": pid,
        "name": name or "未命名向量预设",
        "fields": {k: (fields.get(k, "") or "") for k in EMBED_CONFIG_KEYS},
    })
    _save(data)
    return pid


def update_embed_preset(preset_id: str, name: str | None, fields: dict) -> bool:
    data = _load()
    p = _get_embed_preset(data, preset_id)
    if p is None:
        return False

    if name:
        p["name"] = name

    old = p.get("fields", {})
    new_fields = {}
    for k in EMBED_CONFIG_KEYS:
        v = fields.get(k, None)
        if v is None:
            new_fields[k] = old.get(k, "")
        elif k in EMBED_SECRET_KEYS and v == "":
            # 空提交=保留原值（前端掩码显示时提交空）
            new_fields[k] = old.get(k, "")
        else:
            new_fields[k] = v
    p["fields"] = new_fields

    _save(data)
    return True


def delete_embed_preset(preset_id: str) -> bool:
    data = _load()
    before = len(data.get("embed_presets", []))
    remaining = [p for p in data.get("embed_presets", []) if p["id"] != preset_id]
    data["embed_presets"] = remaining
    if data.get("current_embed_id") == preset_id:
        # 删除当前预设 → 自动提升第一个剩余预设为当前
        data["current_embed_id"] = remaining[0]["id"] if remaining else None
    _save(data)
    return len(remaining) < before


def apply_embed_preset(preset_id: str) -> dict:
    """应用向量模型预设：把 EMBED_* 字段写入 .env，记 current_embed_id。"""
    fields = get_embed_preset_fields(preset_id)
    if fields is None:
        raise KeyError(f"向量预设不存在: {preset_id}")

    updates = {k: v for k, v in fields.items() if v}
    if updates:
        write_env_config(updates)

    data = _load()
    data["current_embed_id"] = preset_id
    # 同步旧 embed_config 字段（向后兼容，恒等于当前向量预设）
    data["embed_config"] = {"fields": fields}
    _save(data)

    return {"applied_keys": list(updates.keys()), "current_embed_id": preset_id}


# ============================================================
# 向量模型配置（旧单配置端点 shim：操作当前向量预设）
# ============================================================

def get_embed_config() -> dict:
    """返回当前向量预设明文（旧端点兼容）。"""
    data = _load()
    cur = data.get("current_embed_id")
    if cur:
        p = _get_embed_preset(data, cur)
        if p:
            return dict(p.get("fields", {}))
    return dict(data["embed_config"].get("fields", {}))


def save_embed_config(fields: dict) -> dict:
    """保存向量配置：更新当前向量预设（无则新建），并写入 .env（非空字段）。"""
    data = _load()
    cur = data.get("current_embed_id")
    if cur:
        p = _get_embed_preset(data, cur)
        if p:
            old = p.get("fields", {})
            new_fields = {}
            for k in EMBED_CONFIG_KEYS:
                v = fields.get(k, None)
                if v is None:
                    new_fields[k] = old.get(k, "")
                elif k in EMBED_SECRET_KEYS and v == "":
                    # 空提交=保留原值
                    new_fields[k] = old.get(k, "")
                else:
                    new_fields[k] = v
            p["fields"] = new_fields
            _save(data)

            # 同步到 .env（只写非空）
            updates = {k: v for k, v in new_fields.items() if v}
            if updates:
                write_env_config(updates)
            return {"saved_keys": list(new_fields.keys()), "applied_to_env": list(updates.keys())}

    # 无当前预设 → 新建一个并应用
    pid = create_embed_preset("向量配置", {k: fields.get(k, "") for k in EMBED_CONFIG_KEYS})
    data = _load()
    data["current_embed_id"] = pid
    _save(data)
    updates = {k: v for k, v in fields.items() if v}
    if updates:
        write_env_config(updates)
    return {"saved_keys": list(updates.keys()), "applied_to_env": list(updates.keys())}


# ============================================================
# 单一 API 来源（v5.22.2）：api_library.json → os.environ
# ============================================================

def apply_current_to_env() -> list[str]:
    """把 api_library 当前文字预设 + 向量配置应用到 os.environ（覆盖 .env 旧值）。

    api_library.json 是唯一 API 来源（v5.22.2）：主系统启动时调用，让当前预设的
    ARK_*（key/base_url/model_pro）+ 向量配置的 EMBED_*（key/base_url/model）
    覆盖用户级 .env 的旧值，消除双配置源不一致。CHARACTER/RERANK 等不在预设库
    内的字段保持 .env 原样（write_env_config 只改提及的 key）。

    返回 applied_keys；api_library 为空/损坏时 no-op（try/except 兜底，回退 .env）。
    """
    try:
        data = _load()
    except Exception:
        return []

    updates: dict[str, str] = {}

    # 文字预设：current_text_id → ARK_*
    cur = data.get("current_text_id")
    if cur:
        p = _get_text_preset(data, cur)
        if p:
            for k in TEXT_PRESET_KEYS:
                v = (p.get("fields") or {}).get(k)
                if v:
                    updates[k] = v

    # 向量配置：current_embed_id 对应预设 → EMBED_*（无则回退旧 embed_config）
    cur_embed = data.get("current_embed_id")
    if cur_embed:
        p = _get_embed_preset(data, cur_embed)
        if p:
            ef = p.get("fields") or {}
            for k in EMBED_CONFIG_KEYS:
                v = ef.get(k)
                if v:
                    updates[k] = v
    else:
        ef = (data.get("embed_config") or {}).get("fields") or {}
        for k in EMBED_CONFIG_KEYS:
            v = ef.get(k)
            if v:
                updates[k] = v

    if not updates:
        return []

    # 覆盖 os.environ（预设库权威）+ 同步写回 .env（保留未提及行）
    for k, v in updates.items():
        os.environ[k] = v
    try:
        write_env_config(updates)
    except Exception:
        pass
    return list(updates.keys())
