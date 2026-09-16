"""驱动基类：manifest（能力/参数/权限/成本项）+ 统一 DriveResult。

对齐总计划 §2.3：
- 驱动/导出器插件化：每个驱动带 manifest（capabilities / params / permissions / cost_items）；
- 能力与权限声明：写文件范围、外呼服务、花费来源显式声明，为沙箱与预算闸铺路。

status 约定（DriveResult.status）：
- done           成功产出（outputs 非空）
- skipped        未执行即放弃（环境/凭据/权限不具备），reason 必须给明确原因；不算失败
- not_installed  可选依赖未安装（如 kokoro/torch），reason 带安装指引
- failed         执行了但失败（reason 记因；调用方决定是否重试）
- not_implemented接口已定、真实链路未接入（如云 TTS stub）
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

STATUS_DONE = "done"
STATUS_SKIPPED = "skipped"
STATUS_NOT_INSTALLED = "not_installed"
STATUS_FAILED = "failed"
STATUS_NOT_IMPLEMENTED = "not_implemented"


@dataclass
class DriverManifest:
    """驱动自描述。permissions 声明写文件范围/外呼服务/花费来源。"""

    name: str
    version: str
    capabilities: List[str] = field(default_factory=list)
    params: Dict[str, Any] = field(default_factory=dict)
    permissions: Dict[str, Any] = field(default_factory=dict)
    cost_items: List[Dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "version": self.version,
            "capabilities": list(self.capabilities),
            "params": dict(self.params),
            "permissions": dict(self.permissions),
            "cost_items": [dict(x) for x in self.cost_items],
        }


@dataclass
class DriveResult:
    """驱动统一返回。ok 仅在 status=done 时为 True；skipped/not_installed 不抛异常。"""

    ok: bool
    status: str
    reason: str = ""
    outputs: List[Dict[str, Any]] = field(default_factory=list)

    @classmethod
    def done(cls, outputs: List[Dict[str, Any]], reason: str = "") -> "DriveResult":
        return cls(ok=True, status=STATUS_DONE, reason=reason, outputs=outputs)

    @classmethod
    def skipped(cls, reason: str) -> "DriveResult":
        return cls(ok=False, status=STATUS_SKIPPED, reason=reason)

    @classmethod
    def not_installed(cls, reason: str) -> "DriveResult":
        return cls(ok=False, status=STATUS_NOT_INSTALLED, reason=reason)

    @classmethod
    def not_implemented(cls, reason: str) -> "DriveResult":
        return cls(ok=False, status=STATUS_NOT_IMPLEMENTED, reason=reason)

    @classmethod
    def failed(cls, reason: str) -> "DriveResult":
        return cls(ok=False, status=STATUS_FAILED, reason=reason)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "ok": self.ok,
            "status": self.status,
            "reason": self.reason,
            "outputs": [dict(o) for o in self.outputs],
        }


class BaseDriver:
    """所有媒体驱动基类：子类提供 manifest 与 run(request)。"""

    manifest: DriverManifest = DriverManifest(name="base", version="0")

    def available(self) -> Tuple[bool, str]:
        """运行环境检查：(可运行, 原因)。默认可运行；子类按需覆写。"""
        return True, ""

    def run(self, request: Dict[str, Any]) -> DriveResult:
        """执行一次驱动动作。约定：任何环境缺失走 skipped/not_installed，绝不抛异常、不重试风暴。"""
        raise NotImplementedError

    # ------------------------------------------------------------------ 成本记账钩子

    def record_usage(
        self,
        result: DriveResult,
        *,
        request: Optional[Dict[str, Any]] = None,
        capability: Optional[str] = None,
        quantity: float = 0.0,
        unit: str = "",
        est_cost: float = 0.0,
        currency: str = "cny",
        pack: str = "",
        asset_id: str = "",
        ledger: Optional[Any] = None,
    ) -> None:
        """一次驱动调用写入用量账本（usage_ledger）。账本不可用时静默 no-op。

        - 账本来源：显式 ledger > 默认账本（env AINOVEL_MEDIA_LEDGER 门控）> None；
        - 约定 est_cost 仅 status=done 时非零；skipped/failed 记 0 成本但留痕（可审计为何没产出）；
        - 任何异常吞掉：记账绝不能影响生成主流程（对齐 llm_client 日志纪律）。
        """
        try:
            from ..usage_ledger import active_ledger  # 延迟导入避免环

            lg = active_ledger(ledger)
            if lg is None:
                return
            req = request or {}
            manifest = self.manifest
            lg.record(
                driver=manifest.name,
                capability=capability
                or (manifest.capabilities[0] if manifest.capabilities else ""),
                status=result.status,
                quantity=quantity,
                unit=unit,
                est_cost=est_cost if result.status == "done" else 0.0,
                currency=currency,
                pack=str(req.get("pack_id") or req.get("pack") or ""),
                asset_id=str(req.get("entry_asset_id") or asset_id or ""),
                model=str(req.get("model") or getattr(self, "model", "") or ""),
                meta={"reason": result.reason} if result.reason else None,
            )
        except Exception:
            pass
