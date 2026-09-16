# -*- coding: utf-8 -*-
"""ark 控制台后端（T36）：ark-cli 自建前端的数据面。

职责：
- 任务队列：提交生成任务（图片 / 视频 / 语音）→ 后台执行媒体驱动 → 状态与结果持久化；
- 回填：生成成功后把状态接到 pack assets.json（产物已由驱动写入全局资产库与账本）；
- 只读域：模型 / 用量账单 / 账号健康 / 理解 / 对话 / 高级 api 透传。

诚实降级（T34 边界）：驱动不可用（无 runner / 无凭据 / 缺依赖）时任务状态照实记为
skipped / not_installed 并带 reason，绝不假造产物；预算只在 status=done 时扣减。
账本 / 预算与 dashboard 同口径：<asset_library>/ledger、<asset_library>/budget.json；
如 env AINOVEL_MEDIA_LEDGER 已设置则账本优先用 env 指向目录。
"""
from __future__ import annotations

import asyncio
import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from .media.ark_runner import ArkRunner
from .media.asset_store import AssetStore, default_asset_library_root
from .media.drivers.base import (
    STATUS_DONE,
    STATUS_FAILED,
    STATUS_NOT_IMPLEMENTED,
    STATUS_NOT_INSTALLED,
    STATUS_SKIPPED,
)
from .media.drivers.kokoro import KokoroDriver
from .media.drivers.seedance import SeedanceDriver
from .media.drivers.seedream import SEEDREAM_PRICE_PER_IMAGE, SeedreamDriver
from .media.usage_ledger import UsageLedger, default_ledger

TASKS_FILE = "tasks.json"
BUDGET_FILE = "budget.json"

DEFAULT_BUDGET: Dict[str, Any] = {
    "enabled": True,
    "image_month_cny": 200.0,
    "video_month_cny": 120.0,
    "director_month_usd": 30.0,
}

# 模型面静态清单（真实可用性以驱动 available() 与平台开通为准）
MODEL_ROWS = [
    {"id": "doubao-seedream-4-0/5-0", "cap": "图片生成", "cost": "¥0.20/张起（估算）", "plan": "需开通视觉资源"},
    {"id": "doubao-seedance-1-5-pro", "cap": "视频生成", "cost": "draft ¥0.15/秒 · 正式 ¥0.50/秒（估算）", "plan": "需开通视觉资源"},
    {"id": "doubao-seed-evolving", "cap": "文本 / 推理", "cost": "Coding Plan", "plan": "已购"},
    {"id": "kokoro-82m", "cap": "离线 TTS", "cost": "本地免费", "plan": "本地（需 torch/espeak-ng）"},
    {"id": "volc-tts-zh", "cap": "云语音合成", "cost": "按字符", "plan": "未接入（stub）"},
]


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _read_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def _write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8", newline="\n")
    os.replace(tmp, path)


def console_root() -> Path:
    return default_asset_library_root() / "ark"


def media_ledger() -> UsageLedger:
    """账本：env 指定优先，否则落资产库 ledger/（与驱动记账同根）。"""
    lg = default_ledger()
    return lg if lg is not None else UsageLedger(default_asset_library_root() / "ledger")


def budget_path() -> Path:
    return default_asset_library_root() / BUDGET_FILE


def read_budget() -> Dict[str, Any]:
    data = _read_json(budget_path(), None)
    if not isinstance(data, dict):
        data = {}
    merged = dict(DEFAULT_BUDGET)
    merged.update({k: v for k, v in data.items() if k in merged})
    return merged


def write_budget(cfg: Dict[str, Any]) -> Dict[str, Any]:
    cur = read_budget()
    for k in ("enabled", "image_month_cny", "video_month_cny", "director_month_usd"):
        if k in cfg:
            cur[k] = cfg[k]
    _write_json(budget_path(), cur)
    return cur


def _short_id() -> str:
    return "task_" + uuid.uuid4().hex[:10]


class ArkConsole:
    """任务队列 + 九域数据面。单进程内存 + JSON 持久化（重启保留任务历史）。"""

    def __init__(self, root: Optional[Path] = None, ledger: Optional[UsageLedger] = None) -> None:
        self.root = Path(root) if root else console_root()
        self.ledger = ledger if ledger is not None else media_ledger()
        self._running: Dict[str, asyncio.Task] = {}

    # ------------------------------------------------------------------ 存储

    @property
    def tasks_path(self) -> Path:
        return self.root / TASKS_FILE

    def load(self) -> Dict[str, Any]:
        data = _read_json(self.tasks_path, None)
        if not isinstance(data, dict) or not isinstance(data.get("tasks"), list):
            data = {"version": 1, "tasks": [], "updated_at": now_iso()}
        return data

    def save(self, data: Dict[str, Any]) -> None:
        data["updated_at"] = now_iso()
        _write_json(self.tasks_path, data)

    def list_tasks(self, ctx: str = "", book_root: str = "", limit: int = 50) -> List[Dict[str, Any]]:
        tasks = self.load()["tasks"]
        if ctx:
            tasks = [t for t in tasks if t.get("ctx") == ctx]
        if book_root:
            tasks = [t for t in tasks if t.get("book_root") == book_root]
        return list(reversed(tasks))[: max(1, min(int(limit or 50), 200))]

    def get(self, task_id: str) -> Optional[Dict[str, Any]]:
        return next((t for t in self.load()["tasks"] if t.get("id") == task_id), None)

    def _update(self, task_id: str, **fields: Any) -> Optional[Dict[str, Any]]:
        data = self.load()
        rec = next((t for t in data["tasks"] if t.get("id") == task_id), None)
        if rec is None:
            return None
        rec.update(fields)
        rec["updated_at"] = now_iso()
        self.save(data)
        return rec

    # ------------------------------------------------------------------ 提交 / 执行

    def submit(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        kind = str(payload.get("kind") or "image").lower()
        if kind not in ("image", "video", "voice"):
            return {"ok": False, "error": f"未知任务类型：{kind}"}
        prompt = str(payload.get("prompt") or "").strip()
        if kind in ("image", "video", "voice") and not prompt:
            return {"ok": False, "error": "缺少提示词 / 文本"}
        draft = bool(payload.get("draft", kind == "video"))
        duration = int(payload.get("duration") or (5 if draft else 10))
        model = str(payload.get("model") or "")
        size = str(payload.get("size") or ("1080x1920" if kind != "voice" else ""))
        count = max(1, min(int(payload.get("count") or 1), 4))
        if kind == "video":
            cost_est = round((0.15 if draft else 0.50) * duration, 4)
        elif kind == "image":
            cost_est = round(SEEDREAM_PRICE_PER_IMAGE * count, 4)
        else:
            cost_est = 0.0
        rec = {
            "id": _short_id(),
            "created_at": now_iso(),
            "updated_at": now_iso(),
            "status": "queued",
            "kind": kind,
            "label": str(payload.get("label") or {"image": "生成图片", "video": "生成镜头视频", "voice": "生成语音"}[kind]),
            "driver": {"image": "seedream", "video": "seedance", "voice": "kokoro"}[kind],
            "model": model,
            "size": size,
            "prompt": prompt,
            "draft": draft,
            "duration": duration,
            "count": count,
            "voice": str(payload.get("voice") or ""),
            "speed": float(payload.get("speed") or 0.92),
            "cmd": self._cmd_text(kind, model, size, draft, duration, prompt, count),
            "book_root": str(payload.get("book_root") or ""),
            "pack_id": str(payload.get("pack_id") or ""),
            "entry_asset_id": str(payload.get("entry_asset_id") or ""),
            "ctx": str(payload.get("ctx") or "drama"),
            "cost_est": cost_est,
            "cost_charged": 0.0,
            "currency": "cny",
            "driver_status": "",
            "reason": "",
            "outputs": [],
            "filled": False,
        }
        data = self.load()
        data["tasks"].append(rec)
        self.save(data)
        try:
            loop = asyncio.get_running_loop()
            self._running[rec["id"]] = loop.create_task(self._run(rec["id"]))
        except RuntimeError:
            # 无事件循环（同步测试）：直接同步执行，保证可测
            self._execute_and_store(rec["id"])
        return {"ok": True, "task": self.get(rec["id"])}

    @staticmethod
    def _cmd_text(kind: str, model: str, size: str, draft: bool, duration: int, prompt: str, count: int) -> str:
        p = prompt[:48] + ("…" if len(prompt) > 48 else "")
        if kind == "image":
            return f'arkcli +gen --model {model or "seedream"} --size {size} "{p}"' + (f" ×{count}" if count > 1 else "")
        if kind == "video":
            return (f'arkcli +gen --model {model or "seedance"} --ratio 9:16 --duration {duration} '
                    f'{"--draft " if draft else ""}"{p}"')
        return f'arkcli +gen --model tts --voice {model or "kokoro"} "{p}"'

    async def _run(self, task_id: str) -> None:
        self._update(task_id, status="running")
        try:
            await asyncio.to_thread(self._execute_and_store, task_id)
        finally:
            self._running.pop(task_id, None)

    def _execute_and_store(self, task_id: str) -> None:
        rec = self.get(task_id)
        if rec is None or rec.get("status") == "cancelled":
            return
        result = self._execute(rec)
        # 取消竞态：执行期间被取消则不改终态
        cur = self.get(task_id)
        if cur is None or cur.get("status") == "cancelled":
            return
        cost = rec["cost_est"] if result["status"] == "succeeded" else 0.0
        self._update(
            task_id,
            status=result["status"],
            driver_status=result["driver_status"],
            reason=result["reason"],
            outputs=result["outputs"],
            cost_charged=cost,
            finished_at=now_iso(),
        )

    # ------------------------------------------------------------------ 驱动分派

    def _execute(self, rec: Dict[str, Any]) -> Dict[str, Any]:
        kind = rec["kind"]
        runner = ArkRunner()
        store = AssetStore()
        try:
            if kind == "image":
                driver = SeedreamDriver(runner=runner, store=store, ledger=self.ledger)
                return self._drive(driver, {
                    "prompt": rec["prompt"], "size": rec["size"], "model": rec["model"],
                    "entry_asset_id": rec["entry_asset_id"] or None,
                    "pack_id": rec["pack_id"], "count": rec["count"], "timeout": 120,
                })
            if kind == "video":
                driver = SeedanceDriver(
                    runner=runner, store=store, ledger=self.ledger,
                    mode="draft" if rec["draft"] else "quality",
                )
                return self._drive(driver, {
                    "prompt": rec["prompt"], "model": rec["model"], "duration": rec["duration"],
                    "entry_asset_id": rec["entry_asset_id"] or None,
                    "pack_id": rec["pack_id"], "wait": True, "max_wait": 300,
                })
            driver = KokoroDriver(store=store)
            return self._drive(driver, {
                "text": rec["prompt"], "voice": rec["voice"] or None, "speed": rec["speed"],
                "entry_asset_id": rec["entry_asset_id"] or None, "pack_id": rec["pack_id"],
            })
        except Exception as exc:  # noqa: BLE001 - 驱动层不应抛，兜底记为 failed
            return {"status": STATUS_FAILED, "driver_status": "exception", "reason": f"驱动异常：{exc}", "outputs": []}

    def _drive(self, driver: Any, request: Dict[str, Any]) -> Dict[str, Any]:
        """执行驱动（图片支持 count>1 循环；非 done 时首轮即返回）。"""
        count = int(request.pop("count", 1))
        last = None
        outputs: List[Dict[str, Any]] = []
        for _ in range(max(1, count)):
            res = driver.run(dict(request))
            last = res
            outputs.extend(res.to_dict().get("outputs") or [])
            if res.status != STATUS_DONE:
                break
        status_map = {
            STATUS_DONE: "succeeded",
            STATUS_SKIPPED: STATUS_SKIPPED,
            STATUS_NOT_INSTALLED: STATUS_NOT_INSTALLED,
            STATUS_NOT_IMPLEMENTED: STATUS_SKIPPED,
            STATUS_FAILED: STATUS_FAILED,
        }
        return {
            "status": status_map.get(last.status, STATUS_FAILED),
            "driver_status": last.status,
            "reason": last.reason,
            "outputs": outputs,
        }

    # ------------------------------------------------------------------ 操作

    def cancel(self, task_id: str) -> Dict[str, Any]:
        rec = self.get(task_id)
        if rec is None:
            return {"ok": False, "error": "任务不存在"}
        if rec.get("status") in ("succeeded", "skipped", "not_installed", "failed", "cancelled"):
            return {"ok": False, "error": f"任务已 {rec.get('status')}"}
        t = self._running.pop(task_id, None)
        if t is not None:
            t.cancel()
        self._update(task_id, status="cancelled", reason="用户取消")
        return {"ok": True, "task": self.get(task_id)}

    def retry(self, task_id: str) -> Dict[str, Any]:
        rec = self.get(task_id)
        if rec is None:
            return {"ok": False, "error": "任务不存在"}
        if rec.get("status") not in ("failed", "cancelled", "skipped", "not_installed"):
            return {"ok": False, "error": "仅失败 / 取消 / 降级任务可重试（重新提交一条新任务）"}
        payload = {
            "kind": rec["kind"], "label": rec["label"], "model": rec["model"], "size": rec["size"],
            "prompt": rec["prompt"], "draft": rec["draft"], "duration": rec["duration"],
            "count": rec.get("count", 1), "voice": rec.get("voice", ""), "speed": rec.get("speed", 0.92),
            "book_root": rec.get("book_root", ""), "pack_id": rec.get("pack_id", ""),
            "entry_asset_id": rec.get("entry_asset_id", ""), "ctx": rec.get("ctx", "drama"),
        }
        return self.submit(payload)

    def fill(self, task_id: str) -> Dict[str, Any]:
        """回填：把成功任务的产物状态写回 pack assets.json（账本已由驱动记录）。"""
        from .drama import films as films_mod

        rec = self.get(task_id)
        if rec is None:
            return {"ok": False, "error": "任务不存在"}
        if rec.get("status") != "succeeded":
            return {"ok": False, "error": f"任务未成功（{rec.get('status')}），不能回填"}
        changed = False
        content_id = ""
        for out in rec.get("outputs") or []:
            if out.get("content_id"):
                content_id = str(out["content_id"])
                break
        if rec.get("book_root") and rec.get("pack_id") and rec.get("entry_asset_id"):
            try:
                changed = films_mod.mark_pack_asset(
                    rec["book_root"], rec["pack_id"], rec["entry_asset_id"], "done", content_id,
                )
            except (OSError, ValueError) as exc:
                return {"ok": False, "error": f"回填失败：{exc}"}
        self._update(task_id, filled=True, filled_at=now_iso(), content_id=content_id)
        return {"ok": True, "task": self.get(task_id), "assets_updated": changed, "content_id": content_id}

    # ------------------------------------------------------------------ 九域只读面

    def models(self) -> Dict[str, Any]:
        runner = ArkRunner()
        ok, why = runner.available()
        drivers = []
        for d in (SeedreamDriver(runner=runner), SeedanceDriver(runner=runner), KokoroDriver()):
            avail, reason = d.available()
            drivers.append({"name": d.manifest.name, "available": bool(avail), "reason": reason,
                            "capabilities": d.manifest.capabilities, "cost_items": d.manifest.cost_items})
        return {"ok": True, "models": MODEL_ROWS, "drivers": drivers,
                "runner_ok": ok, "runner_reason": why}

    def auth(self) -> Dict[str, Any]:
        runner = ArkRunner()
        ok, why = runner.available()
        version = ""
        if ok:
            res = runner.run(["--version"], timeout=10)
            version = (res.raw or "").strip().splitlines()[0][:120] if res.raw else ""
        return {
            "ok": True,
            "runner_ok": ok,
            "runner_reason": why,
            "executable": (runner.find() or [""])[0],
            "version": version,
            "credentials_env": SeedreamDriver._has_credentials(),
            "profile_hint": "arkcli auth login volc-sso（密钥由 arkcli 管理，本项目不代管）",
            "doctor": "安装 " + ("✓" if ok else "✗") + " · 连通/配置需真实凭据后由 arkcli doctor 输出",
        }

    def usage(self) -> Dict[str, Any]:
        lg = self.ledger
        summary = lg.summarize()
        rows = lg.read_lines()
        spent = {"cny": 0.0, "usd": 0.0}
        by_driver: Dict[str, Dict[str, float]] = {}
        for r in rows:
            if str(r.get("status")) != "done":
                continue
            cur = str(r.get("currency") or "cny")
            cost = float(r.get("est_cost") or 0.0)
            spent[cur] = round(spent.get(cur, 0.0) + cost, 6)
            d = by_driver.setdefault(str(r.get("driver") or "unknown"), {})
            d[cur] = round(d.get(cur, 0.0) + cost, 6)
        bud = read_budget()
        image_spent = float(by_driver.get("seedream", {}).get("cny", 0.0))
        video_spent = float(by_driver.get("seedance", {}).get("cny", 0.0))
        return {
            "ok": True,
            "summary": summary,
            "rows": list(reversed(rows))[:50],
            "budget": bud,
            "spent": spent,
            "spent_by_driver": by_driver,
            "remaining": {
                "image_month_cny": round(float(bud.get("image_month_cny") or 0) - image_spent, 4),
                "video_month_cny": round(float(bud.get("video_month_cny") or 0) - video_spent, 4),
                "note": "账本按币种分桶；生图/视频余额以账本 done 行累计为准，budget.json 同时做任务级扣减",
            },
        }

    async def understand(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        from .drama.align import align_srt_to_audio, whisper_available

        action = str(payload.get("action") or "subtitle_align")
        if action in ("subtitle_align", "align"):
            srt = str(payload.get("srt") or "")
            audio = str(payload.get("audio") or "")
            if not srt or not audio:
                return {"ok": False, "status": "not_installed",
                        "reason": "需要 srt 与音频文件；当前 pack 没有配音音频（Kokoro 未产出）"}
            if not whisper_available():
                return {"ok": False, "status": "not_installed",
                        "reason": "faster-whisper 未安装（pip install faster-whisper 后可用）"}
            res = align_srt_to_audio(srt, audio)
            return {"ok": bool(res.get("ok")), "status": res.get("status"), "result": res}
        return {
            "ok": False, "status": "not_implemented",
            "reason": "该子能力本地未接入（OCR / 视频总结 / 字段抽取属 T34 边界外的官方能力）：" + action,
        }

    async def chat(self, message: str, history: Optional[List[Dict[str, str]]] = None,
                   ctx: str = "drama") -> Dict[str, Any]:
        from .llm_client import chat_completion

        system = (
            "你是 AInovel Harness 漫剧线的 ark 控制台助手。回答围绕：生成参数（Seedream 生图 / "
            "Seedance 视频 draft 控本 / Kokoro 与云 TTS）、任务队列、资产入库与账本预算。"
            "当前开发机未开通方舟视觉资源，生成任务会如实降级（skipped），不要假装已生成。"
            "回答简短、可执行。"
        )
        try:
            resp = await chat_completion(
                system=system, user=message, history=history or [], call_type="ark_chat",
            )
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": f"对话调用失败：{exc}"}
        return {
            "ok": not resp.get("error"),
            "reply": resp.get("content") or "",
            "error": resp.get("error"),
            "usage": resp.get("usage"),
        }

    def api(self, action: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        runner = ArkRunner()
        ok, why = runner.available()
        if not ok:
            return {"ok": False, "status": "skipped", "reason": why}
        args = ["api", action]
        if params:
            args.append(json.dumps(params, ensure_ascii=False))
        res = runner.run(args, timeout=30)
        return {
            "ok": bool(res.ok), "status": res.status,
            "stdout": (res.raw or "")[:4000], "parsed": res.parsed_json,
            "error": res.error, "cmd": "arkcli " + " ".join(args),
        }


_CONSOLE: Optional[ArkConsole] = None


def get_console() -> ArkConsole:
    global _CONSOLE
    if _CONSOLE is None:
        _CONSOLE = ArkConsole()
    return _CONSOLE
