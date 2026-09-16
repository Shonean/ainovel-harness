"""Seedance 视频驱动（ark-cli `+gen` 视频路线，期 3「Seedance 关键镜头 draft 控本」）。

CLI 事实（third_party/ark-cli v1.0.27 实测 --help，见 UPSTREAM.md）：
- 提交：`arkcli +gen --model <seedance> --ratio 9:16 --duration 5 --draft --no-open --save-to <dir> "<prompt>"`
  视频任务立即返回 task id（不阻塞）；`--draft` 为上游原生 draft 档（更快更便宜、画质降低）；
- 轮询：`arkcli gen get <task-id> --save-to <dir> --no-open` —— 任务 succeeded 时自动把产物下载到
  --save-to 目录（对应 UPSTREAM.md patches #3「产物直落」的适配层实现）；
- 输出默认 `--format json`，runner 层 extract_json 容错解析（patches #2）。

控本设计（总计划 §R2「Seedance 关键镜头 draft 控本」）：
- mode="draft"（默认）：--draft + 480p + 时长封顶 5s，成本估算 DRAFT_PRICE_PER_SECOND；
- mode="quality"：不用 --draft，默认 720p / 10s，单价更高；白名单镜头才允许走 quality；
- 估算单价是常量口径（真实账单以 `arkcli usage stats` / `arkcli pricing` 为准，账本行仅 est_cost）。

无凭据 / 无 runner → skipped + 明确 reason（用户 key 无视觉权限，本驱动绝不真实外呼）；
长任务轮询带总 deadline，超时返回 failed 且 reason 带 task id（`gen get` 可断点续跑）。
"""
from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from ..asset_store import AssetStore
from .base import BaseDriver, DriveResult, DriverManifest

# 默认模型 id 来自 arkcli --help 示例；实际可用模型以平台开通与 `arkcli models search seedance` 为准
DEFAULT_SEEDANCE_MODEL = "doubao-seedance-1-5-pro-251215"
DEFAULT_RATIO = "9:16"  # 漫剧竖屏

# draft 控本档位（对齐总计划：draft 用于关键镜头低成本预览/量产档）
DRAFT_MAX_DURATION = 5
DRAFT_RESOLUTION = "480p"
DRAFT_PRICE_PER_SECOND = 0.15  # 估算口径（CNY/秒）；真实以 arkcli usage stats 账单为准
QUALITY_DURATION = 10
QUALITY_RESOLUTION = "720p"
QUALITY_PRICE_PER_SECOND = 0.50  # 估算口径（CNY/秒）

# 轮询语义（patches #4：--wait 语义可控轮询）
DEFAULT_POLL_INTERVAL = 5.0
DEFAULT_MAX_WAIT = 600.0

# ark-cli 任务状态归一
_SUCCESS_STATES = {"succeeded", "success", "done", "completed", "complete"}
_FAILURE_STATES = {"failed", "failure", "error", "cancelled", "canceled", "expired", "timeout"}

_TASK_ID_KEYS = ("task_id", "taskId", "TaskId", "id", "Id", "ID")
_STATUS_KEYS = ("status", "state", "Status", "State")
_FILE_KEYS = ("path", "file_path", "filepath", "video_path", "local_path", "download_path", "save_path")
_URL_KEYS = ("url", "video_url", "download_url", "content_url")

_PERMISSION_HINTS = (
    "无视觉",
    "没有权限",
    "not authorized",
    "access denied",
    "permission",
    "403",
    "invalidparameter.model",
)


class SeedanceDriver(BaseDriver):
    """文本生视频驱动（异步任务：提交 → 轮询 → 产物入资产库）。"""

    manifest = DriverManifest(
        name="seedance",
        version="0.1.0",
        capabilities=["text2video", "image2video"],
        params={
            "prompt": "str, 必填，视频提示词（运镜/主体/氛围一句话）",
            "mode": "str, draft（默认，控本档）| quality（白名单关键镜头档）",
            "duration": "int, 秒；draft 封顶 5、quality 默认 10",
            "ratio": "str, 默认 9:16（漫剧竖屏）",
            "resolution": "str, draft 默认 480p、quality 默认 720p",
            "model": "str, 默认 " + DEFAULT_SEEDANCE_MODEL,
            "entry_asset_id": "str, 可选；成功后联动资产库 entry mark_done（kind=video）",
            "extra_args": "list[str], 透传 arkcli +gen 追加旗标（--camera-fixed/--seed 等）",
            "wait": "bool, 提交后轮询到终态（默认 True）；False 仅提交返回 task_id",
        },
        permissions={
            "write_paths": ["<asset_library>/blobs/**", "<save_dir>/**"],
            "network": ["ark.volcengineapi.com", "lf3-static.bytednsdoc.com"],
            "spends": [
                "ark: Seedance 按秒计费（draft 便宜档 / quality 高清档，需平台开通视觉模型）"
            ],
        },
        cost_items=[
            {
                "item": "video_draft",
                "unit": "秒",
                "source": "volcengine ark Seedance（draft 档）",
                "when": "仅任务 succeeded 且产物入库时计费；提交/轮询/失败不计",
            },
            {
                "item": "video_quality",
                "unit": "秒",
                "source": "volcengine ark Seedance（quality 档）",
                "when": "仅任务 succeeded 且产物入库时计费；白名单镜头专用",
            },
        ],
    )

    def __init__(
        self,
        runner: Any = None,
        store: Optional[AssetStore] = None,
        model: str = DEFAULT_SEEDANCE_MODEL,
        mode: str = "draft",
        ratio: str = DEFAULT_RATIO,
        duration: Optional[int] = None,
        resolution: Optional[str] = None,
        save_dir: Optional[str | Path] = None,
        poll_interval: float = DEFAULT_POLL_INTERVAL,
        max_wait: float = DEFAULT_MAX_WAIT,
        sleep_fn: Any = time.sleep,
        ledger: Any = None,
    ) -> None:
        self.runner = runner
        self.store = store
        self.model = model
        self.mode = mode
        self.ratio = ratio
        self.duration = duration
        self.resolution = resolution
        self.save_dir = Path(save_dir) if save_dir else None
        self.poll_interval = float(poll_interval)
        self.max_wait = float(max_wait)
        self._sleep = sleep_fn  # 测试注入：lambda s: None 避免真实等待
        self.ledger = ledger

    # ------------------------------------------------------------------ 环境检查

    def available(self) -> Tuple[bool, str]:
        if self.runner is None:
            return False, "未注入 ark-cli runner（ArkRunner）"
        ok, why = self.runner.available()  # type: ignore[attr-defined]
        if not ok:
            return False, f"ark-cli runner 不可用：{why}"
        if not self._has_credentials():
            return (
                False,
                "缺少认证：未检测到 ARK_API_KEY / VOLC_INIT_* 环境变量，且无法确认 ark-cli profile；"
                "请先 `arkcli init-volc` 或设置 ARK_API_KEY（注意：Coding Plan key 无视觉模型权限，"
                "Seedance 需在方舟平台单独开通）",
            )
        return True, ""

    @staticmethod
    def _has_credentials() -> bool:
        for key in ("ARK_API_KEY", "VOLC_INIT_ACCESS_KEY", "VOLC_INIT_SECRET_KEY"):
            if os.environ.get(key, "").strip():
                return True
        return False

    # ------------------------------------------------------------------ 档位（draft 控本）

    def resolve_preset(self, request: Dict[str, Any]) -> Dict[str, Any]:
        """request + 驱动默认 → 本次任务的档位参数（draft 控本口径）。

        draft：--draft 旗标 + 时长封顶 DRAFT_MAX_DURATION + 默认 480p；
        quality：无 --draft，默认 720p / 10s。请求显式给的值优先（但 draft 仍封顶时长）。
        """
        mode = str(request.get("mode") or self.mode or "draft").lower()
        draft = mode != "quality"
        duration = int(request.get("duration") or self.duration or (DRAFT_MAX_DURATION if draft else QUALITY_DURATION))
        if draft:
            duration = min(duration, DRAFT_MAX_DURATION)
        resolution = str(request.get("resolution") or self.resolution or (DRAFT_RESOLUTION if draft else QUALITY_RESOLUTION))
        return {
            "mode": "draft" if draft else "quality",
            "draft": draft,
            "duration": duration,
            "ratio": str(request.get("ratio") or self.ratio),
            "resolution": resolution,
            "model": str(request.get("model") or self.model),
        }

    def _price_per_second(self, preset: Dict[str, Any]) -> float:
        return DRAFT_PRICE_PER_SECOND if preset["draft"] else QUALITY_PRICE_PER_SECOND

    # ------------------------------------------------------------------ 提交 / 轮询（patches #4）

    def _save_dir(self, request: Dict[str, Any]) -> Path:
        sd = request.get("save_dir") or self.save_dir
        if sd:
            path = Path(sd)
        elif self.store is not None:
            path = self.store.root / "clips"
        else:
            import tempfile

            path = Path(tempfile.gettempdir()) / "ainovel_seedance"
        path.mkdir(parents=True, exist_ok=True)
        return path

    def _submit_args(self, prompt: str, preset: Dict[str, Any], save_dir: Path, request: Dict[str, Any]) -> List[str]:
        args = [
            "+gen",
            "--model", preset["model"],
            "--ratio", preset["ratio"],
            "--duration", str(preset["duration"]),
            "--save-to", str(save_dir),
            "--no-open",  # 无人值守：绝不让 CLI 弹系统播放器
        ]
        if preset["draft"]:
            args.append("--draft")
        if preset["resolution"]:
            args += ["--resolution", preset["resolution"]]
        extra = request.get("extra_args") or []
        if isinstance(extra, (list, tuple)):
            args += [str(a) for a in extra]
        args.append(prompt)
        return args

    def submit(self, request: Dict[str, Any]) -> Tuple[Optional[str], Any]:
        """提交视频任务。返回 (task_id | None, RunResult)。task id 从输出容错提取。"""
        prompt = str(request.get("prompt") or "").strip()
        preset = self.resolve_preset(request)
        save_dir = self._save_dir(request)
        before = _snapshot_dir(save_dir)
        result = self.runner.run(  # type: ignore[attr-defined]
            self._submit_args(prompt, preset, save_dir, request),
            timeout=int(request.get("submit_timeout", 60)),
        )
        result.before_files = before  # type: ignore[attr-defined]
        if not result.ok:
            return None, result
        task_id = extract_task_id(result.parsed_json)
        return task_id, result

    def poll_once(self, task_id: str, save_dir: Path) -> Tuple[str, Any]:
        """轮询一次 `gen get <task-id>`。返回 (归一状态, RunResult)。"""
        before = _snapshot_dir(save_dir)
        result = self.runner.run(  # type: ignore[attr-defined]
            ["gen", "get", task_id, "--save-to", str(save_dir), "--no-open"],
            timeout=int(self.max_wait if self.max_wait < 60 else 60),
        )
        result.before_files = before  # type: ignore[attr-defined]
        if not result.ok:
            return "error", result
        return normalize_status(extract_status(result.parsed_json)), result

    def wait_for(
        self,
        task_id: str,
        save_dir: Path,
        *,
        poll_interval: Optional[float] = None,
        max_wait: Optional[float] = None,
    ) -> Tuple[str, Any]:
        """可控轮询到终态或超时。返回 (状态, 最后一次 RunResult)。

        超时状态为 "timeout"，reason 携带 task id —— `arkcli gen get <id>` 可断点续跑。
        """
        interval = float(poll_interval if poll_interval is not None else self.poll_interval)
        deadline = time.monotonic() + float(max_wait if max_wait is not None else self.max_wait)
        status, result = "queued", None
        while time.monotonic() < deadline:
            status, result = self.poll_once(task_id, save_dir)
            if status in _SUCCESS_STATES or status in _FAILURE_STATES:
                return status, result
            self._sleep(interval)
        return "timeout", result if result is not None else _empty_result()

    # ------------------------------------------------------------------ 执行

    def run(self, request: Dict[str, Any]) -> DriveResult:
        ok, why = self.available()
        if not ok:
            self.record_usage(DriveResult.skipped(why), request=request, ledger=self.ledger)
            return DriveResult.skipped(why)

        prompt = str(request.get("prompt") or "").strip()
        if not prompt:
            return DriveResult.failed("request 缺少 prompt")
        entry_asset_id = request.get("entry_asset_id")
        preset = self.resolve_preset(request)
        save_dir = self._save_dir(request)
        before_submit = _snapshot_dir(save_dir)

        # 1) 提交
        try:
            task_id, submit_result = self.submit(request)
        except Exception as exc:  # runner 崩溃也不向上抛
            return DriveResult.failed(f"ark-cli runner 异常：{exc}")
        if task_id is None:
            reason = map_error(submit_result, stage="提交")
            if entry_asset_id and self.store is not None:
                self.store.mark_failed(entry_asset_id, reason)
            self.record_usage(DriveResult.failed(reason), request=request, ledger=self.ledger)
            return DriveResult.failed(reason)

        # 2) 仅提交模式（批量排队用）：返回 task_id，不入库不计费
        if not request.get("wait", True):
            self.record_usage(
                DriveResult.done([{"kind": "video", "provider": "seedance", "task_id": task_id, "pending": True}]),
                request=request, capability="text2video_submit", ledger=self.ledger,
            )
            return DriveResult.done(
                [{"kind": "video", "provider": "seedance", "task_id": task_id, "pending": True}],
                reason=f"已提交 {preset['mode']} 任务 {task_id}（未等待完成，不计费）",
            )

        # 3) 轮询到终态
        status, poll_result = self.wait_for(
            task_id, save_dir,
            poll_interval=request.get("poll_interval"),
            max_wait=request.get("max_wait"),
        )
        if status == "timeout":
            reason = (
                f"Seedance 任务轮询超时（>{request.get('max_wait') or self.max_wait}s），task_id={task_id}；"
                f"断点续跑：arkcli gen get {task_id} --save-to {save_dir}"
            )
            if entry_asset_id and self.store is not None:
                self.store.mark_failed(entry_asset_id, reason)
            self.record_usage(DriveResult.failed(reason), request=request, ledger=self.ledger)
            return DriveResult.failed(reason)
        if status in _FAILURE_STATES:
            reason = map_error(poll_result, stage=f"任务终态 {status}")
            if entry_asset_id and self.store is not None:
                self.store.mark_failed(entry_asset_id, reason)
            self.record_usage(DriveResult.failed(reason), request=request, ledger=self.ledger)
            return DriveResult.failed(f"Seedance 任务失败（{status}）：{reason}")

        # 4) 产物定位 → 入资产库（put_bytes/mark_done，patches #3）
        outputs = self._collect_outputs(poll_result, save_dir, before_submit=before_submit)
        if not outputs:
            reason = (
                "任务 succeeded 但未定位到视频产物（save-to 目录无新文件且输出无 path/url）；"
                "raw 前 200 字符：" + str(getattr(poll_result, "raw", ""))[:200]
            )
            if entry_asset_id and self.store is not None:
                self.store.mark_failed(entry_asset_id, reason)
            self.record_usage(DriveResult.failed(reason), request=request, ledger=self.ledger)
            return DriveResult.failed(reason)

        price = self._price_per_second(preset)
        est_cost = round(price * preset["duration"], 4)
        out_items: List[Dict[str, Any]] = []
        for out in outputs:
            item: Dict[str, Any] = {
                "kind": "video",
                "provider": "seedance",
                "mode": preset["mode"],
                "duration": preset["duration"],
                "task_id": task_id,
                "bytes": len(out["data"]),
                "src": out["src"],
            }
            if self.store is not None:
                content_id = self.store.put_bytes(
                    out["data"], "video",
                    meta={
                        "prompt": prompt, "model": preset["model"], "mode": preset["mode"],
                        "duration": preset["duration"], "task_id": task_id,
                    },
                )
                item["content_id"] = content_id
                item["path"] = str(self.store.blob_path(content_id))
                if entry_asset_id:
                    self.store.mark_done(entry_asset_id, content_id)
            out_items.append(item)

        self.record_usage(
            DriveResult.done(out_items), request=request,
            quantity=float(preset["duration"]), unit="秒",
            est_cost=est_cost, ledger=self.ledger,
        )
        return DriveResult.done(
            out_items,
            reason=f"seedance {preset['mode']} {preset['duration']}s 任务 {task_id} 完成，"
                   f"估算成本 {est_cost} CNY（{price}/秒 口径）",
        )

    # ------------------------------------------------------------------ 解析

    def _collect_outputs(self, poll_result: Any, save_dir: Path, before_submit: Sequence[str] = ()) -> List[Dict[str, Any]]:
        """从轮询结果 / save-to 目录差集定位视频字节。

        优先级：输出 JSON 的本地 path > 目录新增文件（相对轮询前快照；为空再相对提交前快照，
        覆盖「下载发生在中间某次轮询」的情形）> 输出里的 url（真实路径才走下载）。
        """
        collected: List[Dict[str, Any]] = []
        parsed = getattr(poll_result, "parsed_json", None)
        for out in iter_values_for_keys(parsed, _FILE_KEYS):
            path = Path(str(out))
            if path.exists() and path.is_file():
                collected.append({"data": path.read_bytes(), "src": str(path)})
        if not collected:
            before = set(getattr(poll_result, "before_files", []) or [])
            for path in _new_files(save_dir, before):
                collected.append({"data": path.read_bytes(), "src": str(path)})
        if not collected:
            # before_submit 为空列表=提交时目录为空：当前所有文件都算本次任务产物
            for path in _new_files(save_dir, set(before_submit)):
                collected.append({"data": path.read_bytes(), "src": str(path)})
        if not collected:
            for url in iter_values_for_keys(parsed, _URL_KEYS):
                data = _download(str(url))
                if data:
                    collected.append({"data": data, "src": str(url)})
        return collected


# --------------------------------------------------------------------------- 模块级工具（可独立测试）


def _snapshot_dir(save_dir: Path) -> List[str]:
    if not save_dir.exists():
        return []
    return [str(p) for p in save_dir.rglob("*") if p.is_file()]


def _new_files(save_dir: Path, before: Sequence[str]) -> List[Path]:
    if not save_dir.exists():
        return []
    current = [p for p in save_dir.rglob("*") if p.is_file() and str(p) not in set(before)]
    # 新文件按修改时间倒序：最新产物优先
    return sorted(current, key=lambda p: p.stat().st_mtime, reverse=True)


def _walk(obj: Any) -> Any:
    """深度优先遍历 JSON 结构，产出 (key, value) 叶子对。"""
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield k, v
            yield from _walk(v)
    elif isinstance(obj, list):
        for item in obj:
            yield from _walk(item)


def extract_task_id(parsed_json: Any) -> Optional[str]:
    """从 +gen 输出容错提取视频 task id（形状未定，按 key 名扫描；偏好 cgtv-* 形态）。"""
    if not isinstance(parsed_json, dict):
        return None
    fallback: Optional[str] = None
    for key, value in _walk(parsed_json):
        if key in _TASK_ID_KEYS and isinstance(value, str) and value.strip():
            v = value.strip()
            if v.startswith(("cgtv", "cgt-")):
                return v
            if fallback is None and not v.startswith("http"):
                fallback = v
    return fallback


def extract_status(parsed_json: Any) -> Optional[str]:
    for key, value in _walk(parsed_json):
        if key in _STATUS_KEYS and isinstance(value, str) and value.strip():
            return value.strip()
    return None


def normalize_status(raw: Optional[str]) -> str:
    s = str(raw or "").strip().lower()
    if s in _SUCCESS_STATES:
        return s
    if s in _FAILURE_STATES:
        return s
    if s in ("running", "queued", "pending", "in_progress", "processing", "submitted", ""):
        return s or "unknown"
    return s


def iter_values_for_keys(parsed_json: Any, keys: Sequence[str]) -> List[str]:
    keyset = set(keys)
    return [str(v) for k, v in (_walk(parsed_json) or []) if k in keyset and isinstance(v, str) and v.strip()]


def _download(url: str, timeout: int = 60) -> Optional[bytes]:
    """url → 字节（仅真实链路使用；CDN 产物 url 过期前由 gen get 自动落盘，此为兜底）。"""
    if not url.startswith(("http://", "https://")):
        return None
    try:
        from urllib.request import urlopen

        with urlopen(url, timeout=timeout) as resp:  # noqa: S310 - 上游 CDN 固定域名
            return resp.read()
    except Exception:
        return None


def map_error(result: Any, stage: str = "") -> str:
    err = (getattr(result, "error", "") or "") + " " + (getattr(result, "raw", "") or "")
    low = err.lower()
    if any(h in low for h in _PERMISSION_HINTS):
        return (
            "Seedance 调用被拒（疑似 API key 无视觉模型权限）：需在方舟平台开通 Seedance 后重试。"
            f"平台报错：{err.strip()[:300]}"
        )
    return f"ark-cli 视频任务{stage}失败（status={getattr(result, 'status', 'error')}）：{err.strip()[:300]}"


def _empty_result() -> Any:
    from ..ark_runner import RunResult

    return RunResult(ok=False, status="error", error="no poll executed")
