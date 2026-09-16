"""Seedream 生图驱动（ark-cli +gen 底座）。

设计：
- 真实生图走 `arkcli +gen --model <seedream> --size WxH "<prompt>"`（见 third_party/ark-cli/UPSTREAM.md）；
- 无运行环境（runner 缺失 / 无认证）时返回 skipped + 明确 reason，不抛异常、不重试风暴；
- 用户的 API key 为 Coding Plan、无视觉模型权限：本驱动只做接口与降级路径，测试不真实调用。
"""
from __future__ import annotations

import base64
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..asset_store import AssetStore
from .base import BaseDriver, DriveResult, DriverManifest

# 默认模型 id 来自 arkcli --help 示例；实际可用模型以平台开通与 `arkcli models search seedream` 为准
DEFAULT_SEEDREAM_MODEL = "doubao-seedream-5-0-260128"
DEFAULT_SIZE = "1920x1080"

# 成本估算口径（CNY/张）；真实账单以 `arkcli usage stats` 为准，账本行仅 est_cost
SEEDREAM_PRICE_PER_IMAGE = 0.20

# 无视觉权限时的错误特征（真实调用被平台拒绝时给出可行动的 reason）
_PERMISSION_HINTS = (
    "无视觉",
    "没有权限",
    "not authorized",
    "access denied",
    "permission",
    "403",
    "invalidparameter.model",
)


class SeedreamDriver(BaseDriver):
    """文本生图驱动。构造注入 ArkRunner 与 AssetStore（均可为 None → 降级路径可测）。"""

    manifest = DriverManifest(
        name="seedream",
        version="0.1.0",
        capabilities=["text2image"],
        params={
            "prompt": "str, 必填，生图提示词",
            "size": "str, 默认 1920x1080，漫剧背景建议 1080x1920 竖屏或 16:9 抽帧",
            "model": "str, 默认 " + DEFAULT_SEEDREAM_MODEL,
            "entry_asset_id": "str, 可选；传入时成功后联动资产库 entry 状态机 mark_done",
        },
        permissions={
            "write_paths": ["<asset_library>/blobs/**"],
            "network": ["ark.volcengineapi.com", "lf3-static.bytednsdoc.com"],
            "spends": ["ark: Seedream 按张计费（需平台开通视觉模型）"],
        },
        cost_items=[
            {
                "item": "image",
                "unit": "张",
                "source": "volcengine ark Seedream",
                "when": "仅成功生成时计费；skipped/failed 不计",
            }
        ],
    )

    def __init__(
        self,
        runner: Any = None,
        store: Optional[AssetStore] = None,
        model: str = DEFAULT_SEEDREAM_MODEL,
        size: str = DEFAULT_SIZE,
        ledger: Any = None,
    ) -> None:
        self.runner = runner
        self.store = store
        self.model = model
        self.size = size
        self.ledger = ledger

    # ------------------------------------------------------------------ 环境检查

    def available(self) -> tuple[bool, str]:
        if self.runner is None:
            return False, "未注入 ark-cli runner（ArkRunner）"
        ok, why = self.runner.available()  # type: ignore[attr-defined]
        if not ok:
            return False, f"ark-cli runner 不可用：{why}"
        if not self._has_credentials():
            return (
                False,
                "缺少认证：未检测到 ARK_API_KEY / VOLC_INIT_* 环境变量，且无法确认 ark-cli profile；"
                "请先 `arkcli init-volc` 或设置 ARK_API_KEY",
            )
        return True, ""

    @staticmethod
    def _has_credentials() -> bool:
        for key in ("ARK_API_KEY", "VOLC_INIT_ACCESS_KEY", "VOLC_INIT_SECRET_KEY"):
            if os.environ.get(key, "").strip():
                return True
        return False

    # ------------------------------------------------------------------ 执行

    def run(self, request: Dict[str, Any]) -> DriveResult:
        ok, why = self.available()
        if not ok:
            self.record_usage(DriveResult.skipped(why), request=request, ledger=self.ledger)
            return DriveResult.skipped(why)

        prompt = (request.get("prompt") or "").strip()
        if not prompt:
            return DriveResult.failed("request 缺少 prompt")
        size = request.get("size") or self.size
        model = request.get("model") or self.model
        entry_asset_id = request.get("entry_asset_id")

        args: List[str] = ["+gen", "--model", model, "--size", size]
        try:
            result = self.runner.run(args + [prompt], timeout=int(request.get("timeout", 120)))
        except Exception as exc:  # runner 崩溃也不向上抛
            return DriveResult.failed(f"ark-cli runner 异常：{exc}")

        if not result.ok:
            reason = self._map_error(result)
            if entry_asset_id and self.store is not None:
                self.store.mark_failed(entry_asset_id, reason)
            self.record_usage(DriveResult.failed(reason), request=request, ledger=self.ledger)
            return DriveResult.failed(reason)

        outputs = self._collect_outputs(result.parsed_json)
        if not outputs:
            reason = (
                "ark-cli 返回成功但未解析到图片产物（期待 outputs[].path 或 image_b64）；"
                "raw 前 200 字符：" + str(result.raw)[:200]
            )
            self.record_usage(DriveResult.failed(reason), request=request, ledger=self.ledger)
            return DriveResult.failed(reason)

        out_items: List[Dict[str, Any]] = []
        for out in outputs:
            item: Dict[str, Any] = {
                "kind": "background",
                "provider": "seedream",
                "bytes": len(out["data"]),
                "src": out["src"],
            }
            if self.store is not None:
                content_id = self.store.put_bytes(
                    out["data"], "background", meta={"prompt": prompt, "model": model, "size": size}
                )
                item["content_id"] = content_id
                item["path"] = str(self.store.blob_path(content_id))
                if entry_asset_id:
                    self.store.mark_done(entry_asset_id, content_id)
            out_items.append(item)
        self.record_usage(
            DriveResult.done(out_items), request=request,
            quantity=float(len(out_items)), unit="张",
            est_cost=round(SEEDREAM_PRICE_PER_IMAGE * len(out_items), 4),
            ledger=self.ledger,
        )
        return DriveResult.done(out_items, reason=f"seedream {model} {size} 生成 {len(out_items)} 张")

    # ------------------------------------------------------------------ 解析

    @staticmethod
    def _collect_outputs(parsed_json: Any) -> List[Dict[str, Any]]:
        """从 ark-cli 输出提取图片字节。支持两种形状（对应 UPSTREAM.md patches #2/#3）：

        1. {"outputs": [{"path": "<本地文件>"}]}  —— 产物直落目录（适配层约定）
        2. {"data": {"images": [{"b64": "<base64>"}]}} —— inline base64
        """
        collected: List[Dict[str, Any]] = []
        if not isinstance(parsed_json, dict):
            return collected
        for out in parsed_json.get("outputs", []) or []:
            path = out.get("path") if isinstance(out, dict) else None
            if path and Path(path).exists():
                collected.append({"data": Path(path).read_bytes(), "src": str(path)})
        images = ((parsed_json.get("data") or {}).get("images")) or []
        for img in images:
            b64 = img.get("b64") if isinstance(img, dict) else None
            if b64:
                collected.append({"data": base64.b64decode(b64), "src": "inline_b64"})
        return collected

    @staticmethod
    def _map_error(result: Any) -> str:
        err = (getattr(result, "error", "") or "") + " " + (getattr(result, "raw", "") or "")
        low = err.lower()
        if any(h in low for h in _PERMISSION_HINTS):
            return (
                "Seedream 调用被拒（疑似 API key 无视觉模型权限）："
                "需在方舟平台开通 Seedream/Endpoint 后重试。平台报错：" + err.strip()[:300]
            )
        return f"ark-cli 生成失败（status={getattr(result, 'status', 'error')}）：{err.strip()[:300]}"
