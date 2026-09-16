"""媒体驱动用量账本（期 3 成本记账钩子）。

风格对齐 `prompt_harness/llm_client.py` 的 llm_calls 账本：
- jsonl 按天滚动追加（logs/media/YYYYMMDD.jsonl），写失败静默、绝不影响主流程；
- 每行一条调用记录：时间 / 驱动 / 能力 / 状态 / 数量 / 估算成本 / 来源 pack；
- 汇总接口 summarize()：从 jsonl 读全量行，按驱动 / 按日聚合（调用次数、数量、成本）。

与 llm_calls 的差异：
- 媒体计量单位是「张/秒/句」而非 token，数量与单位成对出现；
- 成本为估算值（est_cost）+ 币种（currency，方舟为 CNY、LLM 账本为 USD），
  聚合时按 (currency) 分桶，绝不跨币种相加；
- 记账来源默认经 drivers/base 的钩子（BaseDriver.record_usage），
  默认账本仅当环境变量 AINOVEL_MEDIA_LEDGER 指向目录时启用（生产由服务端设置）；
  测试/离线场景显式构造 UsageLedger 注入，不产生副作用。
"""
from __future__ import annotations

import json
import os
import time
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

ENV_LEDGER_DIR = "AINOVEL_MEDIA_LEDGER"

# 记录的 status 取值与 DriveResult.status 对齐（done/skipped/not_installed/failed/not_implemented）


def _day_key(ts: Optional[float] = None) -> str:
    return time.strftime("%Y%m%d", time.localtime(ts if ts is not None else time.time()))


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S+08:00", time.localtime())


class UsageLedger:
    """jsonl 追加账本。root 为目录，按天落 `YYYYMMDD.jsonl`。"""

    def __init__(self, root: Union[str, Path]) -> None:
        self.root = Path(root)

    def path_for(self, day: Optional[str] = None) -> Path:
        return self.root / f"{day or _day_key()}.jsonl"

    # ------------------------------------------------------------------ 记账

    def record(
        self,
        driver: str,
        capability: str,
        *,
        status: str,
        quantity: float = 0.0,
        unit: str = "",
        est_cost: float = 0.0,
        currency: str = "cny",
        pack: str = "",
        asset_id: str = "",
        model: str = "",
        meta: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """追加一条调用记录。写失败静默（对齐 llm_client：日志不能影响主流程）。

        约定：est_cost 仅在 status=done 时非零（skipped/failed 不计费，但留痕可审计）。
        """
        entry: Dict[str, Any] = {
            "ts": _now_iso(),
            "driver": str(driver),
            "capability": str(capability),
            "status": str(status),
            "quantity": float(quantity),
            "unit": str(unit),
            "est_cost": round(float(est_cost), 6),
            "currency": str(currency),
            "pack": str(pack or ""),
            "asset_id": str(asset_id or ""),
            "model": str(model or ""),
        }
        if meta:
            entry["meta"] = meta
        try:
            self.root.mkdir(parents=True, exist_ok=True)
            with self.path_for().open("a", encoding="utf-8", newline="\n") as fh:
                fh.write(json.dumps(entry, ensure_ascii=False, default=str) + "\n")
        except Exception:
            pass
        return entry

    # ------------------------------------------------------------------ 汇总

    def read_lines(self) -> List[Dict[str, Any]]:
        """读全部账本行（跨天）。坏行跳过（partial write 容忍）。"""
        out: List[Dict[str, Any]] = []
        if not self.root.exists():
            return out
        for p in sorted(self.root.glob("*.jsonl")):
            try:
                for line in p.read_text(encoding="utf-8").splitlines():
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        obj = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if isinstance(obj, dict):
                        out.append(obj)
            except OSError:
                continue
        return out

    def summarize(self) -> Dict[str, Any]:
        """按驱动 / 按日聚合。成本按币种分桶（_by_currency），绝不跨币种相加。"""
        by_driver: Dict[str, Dict[str, Any]] = defaultdict(_new_bucket)
        by_day: Dict[str, Dict[str, Any]] = defaultdict(_new_bucket)
        total_calls = 0
        for row in self.read_lines():
            driver = str(row.get("driver") or "unknown")
            day = str(row.get("ts") or "")[:10] or "unknown"
            currency = str(row.get("currency") or "cny")
            status = str(row.get("status") or "")
            qty = float(row.get("quantity") or 0.0)
            cost = float(row.get("est_cost") or 0.0)
            unit = str(row.get("unit") or "")
            for bucket, key in ((by_driver, driver), (by_day, day)):
                b = bucket[key]
                b["calls"] += 1
                if status == "done":
                    b["done_calls"] += 1
                    b["quantity"][f"{unit or '次'}"] = b["quantity"].get(f"{unit or '次'}", 0.0) + qty
                else:
                    b["not_done_calls"] += 1
                b["cost"][currency] = round(b["cost"].get(currency, 0.0) + cost, 6)
            total_calls += 1
        return {
            "ledger_dir": str(self.root),
            "total_calls": total_calls,
            "by_driver": _sorted_dict(by_driver),
            "by_day": _sorted_dict(by_day),
        }


def _new_bucket() -> Dict[str, Any]:
    return {"calls": 0, "done_calls": 0, "not_done_calls": 0, "quantity": {}, "cost": {}}


def _sorted_dict(d: Dict[str, Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    return {k: d[k] for k in sorted(d)}


# ------------------------------------------------------------------ 默认账本（env 门控）

_default: Optional[UsageLedger] = None


def default_ledger() -> Optional[UsageLedger]:
    """默认账本：env AINOVEL_MEDIA_LEDGER 指向目录时启用；未设置 → None（不记账）。

    env 门控的原因：驱动测试/离线跑批不应隐式往仓库写账本行；
    生产由服务端（或用户）设置该变量后再统一记账。
    """
    global _default
    if _default is not None:
        return _default
    env = os.environ.get(ENV_LEDGER_DIR, "").strip()
    if not env:
        return None
    _default = UsageLedger(env)
    return _default


def set_default(ledger: Optional[UsageLedger]) -> None:
    """显式替换默认账本（None 关闭）。测试注入用。"""
    global _default
    _default = ledger


def active_ledger(override: Optional["UsageLedger"] = None) -> Optional[UsageLedger]:
    """驱动侧取账本：显式 override > 默认（env 门控）> None。"""
    return override if override is not None else default_ledger()
