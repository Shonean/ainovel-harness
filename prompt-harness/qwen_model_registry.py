# -*- coding: utf-8 -*-
"""qwen 模型名注册表 —— 集中管理可用模型名，持久化到 JSON。

替代原 test_qwen_l5_gui.py / test_qwen_l5.py 里硬编码的 QWEN_MODELS：
用户自定义模型名保存后能真正持久化，并出现在下拉候选里。

文件：qwen_models.json（本模块同目录）。结构：
  {"models": ["qwen3.5-flash", ...], "current": "qwen3.5-flash"}

用法：
  from qwen_model_registry import list_models, current, add_model, remove_model
  add_model("qwen-max")   # 新增并设为当前（已存在则仅设为当前）
  list_models()           # 全量列表（含 current）
  remove_model("qwen-max")
"""
import json
from pathlib import Path

REGISTRY_FILE = Path(__file__).resolve().parent / "qwen_models.json"
DEFAULT_MODELS = ["qwen3.5-flash"]  # 唯一出厂默认（用户实测手感最好的）


def _load() -> dict:
    try:
        if REGISTRY_FILE.is_file():
            d = json.loads(REGISTRY_FILE.read_text(encoding="utf-8"))
            models = [str(m).strip() for m in (d.get("models") or []) if str(m).strip()]
            if models:
                cur = str(d.get("current") or "").strip()
                return {"models": models, "current": cur if cur in models else models[0]}
    except Exception:
        pass
    return {"models": list(DEFAULT_MODELS), "current": DEFAULT_MODELS[0]}


def _save(d: dict) -> None:
    REGISTRY_FILE.write_text(
        json.dumps({"models": d.get("models") or [], "current": d.get("current") or ""},
                   ensure_ascii=False, indent=2),
        encoding="utf-8")


def list_models() -> list[str]:
    """全部模型名（含 current），用于下拉候选。"""
    return _load().get("models") or list(DEFAULT_MODELS)


def current() -> str:
    """当前选中的模型名。"""
    return _load().get("current") or DEFAULT_MODELS[0]


def add_model(name: str) -> bool:
    """保存模型名：新增（若未收录）并设为当前。返回是否为新收录。

    输入自定义模型名后调用本函数 → 持久化到注册表，下次启动下拉可见。
    """
    name = (name or "").strip()
    if not name:
        return False
    d = _load()
    models = [str(m) for m in (d.get("models") or [])]
    is_new = name not in models
    if is_new:
        models.append(name)
    d["models"] = models
    d["current"] = name
    _save(d)
    return is_new


def set_current(name: str) -> None:
    """仅设为当前（不新增），用于下拉选择已收录模型。"""
    name = (name or "").strip()
    if not name:
        return
    d = _load()
    if name in (d.get("models") or []):
        d["current"] = name
        _save(d)


def remove_model(name: str) -> bool:
    """移除模型名；若移的是当前，回落到列表第一个。返回是否成功。"""
    name = (name or "").strip()
    d = _load()
    models = [str(m) for m in (d.get("models") or [])]
    if name not in models:
        return False
    models = [m for m in models if m != name]
    if not models:
        models = list(DEFAULT_MODELS)
    d["models"] = models
    if d.get("current") == name:
        d["current"] = models[0]
    _save(d)
    return True


if __name__ == "__main__":
    import sys
    print("当前模型:", current())
    print("全部模型:", list_models())
