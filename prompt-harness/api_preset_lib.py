# -*- coding: utf-8 -*-
"""API 预设库 —— 复用主系统 api_library.json 的文字模型预设，独立脚本可随便切换 API。

背景（2026-08-16 用户需求）：l5 测试脚本不能只有 qwen，要测其它 API，配置做成
api 预设那样可随便切换。

主系统 `~/.claude/ainovel-write/api_library.json` 已有完整 text_presets：
  {text_presets: [{id, name, fields: {ARK_API_KEY, ARK_BASE_URL, ARK_MODEL_PRO}}],
   current_text_id: "..."}

本模块只读它，不复制一份——单一 API 来源（v5.22.2 原则）。
用法：
  from api_preset_lib import list_presets, current_preset, apply_preset
  list_presets()          # [{id, name, model}]  —— 候选列表
  current_preset()        # 当前预设 {id, name, api_key, base_url, model}
  apply_preset(preset_id) # 切到指定预设 → {id, name, api_key, base_url, model}
"""
import json
import os
import uuid
from pathlib import Path

_LIBRARY_FILE = Path.home() / ".claude" / "ainovel-write" / "api_library.json"
_TEXT_KEYS = ("ARK_API_KEY", "ARK_BASE_URL", "ARK_MODEL_PRO")


def _load() -> dict:
    try:
        if _LIBRARY_FILE.is_file():
            return json.loads(_LIBRARY_FILE.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {"text_presets": [], "current_text_id": None}


def _save(data: dict) -> None:
    _LIBRARY_FILE.parent.mkdir(parents=True, exist_ok=True)
    _LIBRARY_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


# ════════════════════════════════════════════════════════════════
# 写操作（像主系统 api 预设一样新建/编辑/删除）
# ════════════════════════════════════════════════════════════════
def create_preset(name: str, fields: dict) -> str:
    """新建预设 → {api_key, base_url, model} 字段。返回新预设 id。"""
    data = _load()
    data.setdefault("text_presets", [])
    pid = uuid.uuid4().hex[:8]
    data["text_presets"].append({
        "id": pid,
        "name": name or "未命名预设",
        "fields": {k: str(fields.get(k, "") or "") for k in _TEXT_KEYS},
    })
    if not data.get("current_text_id"):
        data["current_text_id"] = pid
    _save(data)
    return pid


def update_preset(preset_id: str, name: str | None = None, fields: dict | None = None) -> bool:
    """编辑预设（name 或 fields 任一）。fields 空值保留原值。"""
    data = _load()
    p = next((x for x in data.get("text_presets", []) if x.get("id") == preset_id), None)
    if p is None:
        return False
    if name:
        p["name"] = name
    if isinstance(fields, dict):
        old = p.get("fields", {})
        p["fields"] = {k: (fields.get(k) if fields.get(k) not in (None, "") else old.get(k, ""))
                       for k in _TEXT_KEYS}
    _save(data)
    return True


def delete_preset(preset_id: str) -> bool:
    data = _load()
    before = len(data.get("text_presets", []))
    data["text_presets"] = [p for p in data.get("text_presets", []) if p.get("id") != preset_id]
    if data.get("current_text_id") == preset_id:
        data["current_text_id"] = None
    _save(data)
    return len(data["text_presets"]) < before


def list_presets() -> list[dict]:
    """返回 [{id, name, model}] 候选列表（fields 里只暴露模型名，key 不暴露）。"""
    d = _load()
    out = []
    for p in d.get("text_presets") or []:
        if not isinstance(p, dict):
            continue
        fields = p.get("fields") or {}
        out.append({
            "id": p.get("id"),
            "name": p.get("name") or "未命名",
            "model": (fields.get("ARK_MODEL_PRO") or "").strip(),
        })
    return out


def current_preset() -> dict | None:
    """当前预设 → {id, name, api_key, base_url, model}。无预设返回 None。"""
    d = _load()
    cur_id = d.get("current_text_id")
    for p in d.get("text_presets") or []:
        if not isinstance(p, dict):
            continue
        if cur_id and p.get("id") == cur_id:
            return _to_preset(p)
    # 无 current_text_id → 第一个预设
    for p in d.get("text_presets") or []:
        if isinstance(p, dict):
            return _to_preset(p)
    return None


def apply_preset(preset_id: str) -> dict | None:
    """切到指定预设 → {id, name, api_key, base_url, model}。不存在返回 None。"""
    d = _load()
    for p in d.get("text_presets") or []:
        if isinstance(p, dict) and p.get("id") == preset_id:
            return _to_preset(p)
    return None


def _to_preset(p: dict) -> dict:
    fields = p.get("fields") or {}
    return {
        "id": p.get("id"),
        "name": p.get("name") or "未命名",
        "api_key": (fields.get("ARK_API_KEY") or "").strip(),
        "base_url": (fields.get("ARK_BASE_URL") or "").strip(),
        "model": (fields.get("ARK_MODEL_PRO") or "").strip(),
    }


def refresh_env_from_preset(preset: dict | None) -> None:
    """把预设写入 os.environ（供后续调用读取）；preset=None 不清除。"""
    if not preset:
        return
    if preset.get("api_key"):
        os.environ["ARK_API_KEY"] = preset["api_key"]
    if preset.get("base_url"):
        os.environ["ARK_BASE_URL"] = preset["base_url"]
    if preset.get("model"):
        os.environ["ARK_MODEL_PRO"] = preset["model"]


if __name__ == "__main__":
    print("=== API 预设库（主系统 api_library.json）===")
    for i, p in enumerate(list_presets(), 1):
        cur = current_preset()
        mark = "  ← 当前" if cur and p["id"] == cur["id"] else ""
        print(f"  {i:>2}. {p['name']}  [{p['model']}]{mark}")
