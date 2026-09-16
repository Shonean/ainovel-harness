"""ark-cli runner 适配层（Node 启动器壳 + Go 二进制，见 third_party/ark-cli/UPSTREAM.md）。

职责：
- 定位可执行体（vendored 二进制 > env AINOVEL_ARK_CLI > node 启动器 > PATH）；
- run(args, timeout) → RunResult(ok/status/parsed_json/raw/error)：稳定解析 JSON 输出
  （对应 UPSTREAM.md patches #2「稳定 JSON 输出」的适配层容错实现）；
- 找不到可执行体 / 超时 / 非零退出都返回明确 status，不抛异常；
- stdin 全程关闭（去交互：对应 patches #1，杜绝首跑 SSO 交互挂起）。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, List, Optional, Sequence, Tuple, Union

REPO_ROOT = Path(__file__).resolve().parents[3]
ARK_CLI_DIR = REPO_ROOT / "third_party" / "ark-cli"
ENV_ARK_CLI = "AINOVEL_ARK_CLI"

STATUS_DONE = "done"
STATUS_NOT_FOUND = "not_found"
STATUS_TIMEOUT = "timeout"
STATUS_ERROR = "error"


def _platform_key() -> Tuple[str, str, str]:
    """(platform, arch, exe_ext)，与上游 manifest.json 的平台键对齐。"""
    import platform as _platform

    platform = {"win32": "windows", "darwin": "darwin", "linux": "linux"}.get(sys.platform, sys.platform)
    machine = (_platform.machine() or "amd64").lower()
    arch = "arm64" if ("arm" in machine or "aarch" in machine) else "amd64"
    ext = ".exe" if platform == "windows" else ""
    return platform, arch, ext


@dataclass
class RunResult:
    """一次 runner 调用的结果。parsed_json 为从 stdout 容错解析出的 JSON 对象（可能为 None）。"""

    ok: bool
    status: str
    parsed_json: Any = None
    raw: str = ""
    error: str = ""
    cmd: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "ok": self.ok,
            "status": self.status,
            "parsed_json": self.parsed_json,
            "raw": self.raw,
            "error": self.error,
            "cmd": list(self.cmd),
        }


class ArkRunner:
    """ark-cli 子进程适配器。executable / search_paths 可显式注入（测试/自定义安装位置）。"""

    def __init__(
        self,
        executable: Optional[Union[str, Path]] = None,
        search_paths: Optional[Sequence[Path]] = None,
        timeout_default: int = 60,
    ) -> None:
        self._explicit = Path(executable) if executable else None
        self._search_paths = [Path(p) for p in search_paths] if search_paths is not None else None
        self.timeout_default = timeout_default
        self._cache: Optional[Optional[List[str]]] = None

    # ------------------------------------------------------------------ 定位

    def _candidates(self) -> List[List[str]]:
        cands: List[List[str]] = []
        if self._explicit is not None:
            cands.append([str(self._explicit)])
        env = os.environ.get(ENV_ARK_CLI, "").strip()
        if env:
            cands.append([env])

        platform, arch, ext = _platform_key()
        if self._search_paths is not None:
            roots = self._search_paths
        else:
            roots = [ARK_CLI_DIR]  # vendored 二进制 / node 启动器 / PATH 属默认搜索域
        for root in roots:
            cands.append([str(root / "bin" / f"arkcli-{platform}-{arch}{ext}")])
            launcher = root / "scripts" / "run.js"
            node = _which("node")
            if node and launcher.exists():
                cands.append([node, str(launcher)])
        if self._search_paths is None:
            for name in ("arkcli", "arkcli.exe"):
                found = _which(name)
                if found:
                    cands.append([found])
        return cands

    def find(self) -> Optional[List[str]]:
        """返回第一个可用的命令前缀（列表）；全不可用返回 None。结果缓存。"""
        if self._cache is not None:
            return self._cache
        for cand in self._candidates():
            exe = cand[0]
            if exe.endswith(".js"):
                self._cache = cand
                return cand
            if Path(exe).is_file():
                self._cache = cand
                return cand
        self._cache = []
        return None

    def available(self) -> Tuple[bool, str]:
        cmd = self.find()
        if cmd:
            return True, ""
        return False, (
            "ark-cli 可执行体未找到：搜索过 "
            f"env {ENV_ARK_CLI}、{ARK_CLI_DIR}/bin/<平台二进制>、{ARK_CLI_DIR}/scripts/run.js(node)、PATH:arkcli；"
            "请先按 third_party/ark-cli/UPSTREAM.md 完成 vendor 或安装"
        )

    # ------------------------------------------------------------------ 执行

    def run(self, args: Sequence[str], timeout: Optional[int] = None) -> RunResult:
        """跑一次 arkcli。任何失败路径都返回 RunResult（不抛异常）。"""
        cmd_prefix = self.find()
        if not cmd_prefix:
            ok, why = self.available()
            return RunResult(ok=False, status=STATUS_NOT_FOUND, error=why)

        cmd = cmd_prefix + [str(a) for a in args]
        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout or self.timeout_default,
                stdin=subprocess.DEVNULL,  # 去交互：CLI 若试图等输入立即拿到 EOF
                cwd=str(ARK_CLI_DIR) if ARK_CLI_DIR.exists() else None,
            )
        except subprocess.TimeoutExpired:
            return RunResult(
                ok=False,
                status=STATUS_TIMEOUT,
                error=f"ark-cli 超时（>{timeout or self.timeout_default}s）：{cmd}",
                cmd=cmd,
            )
        except OSError as exc:
            return RunResult(ok=False, status=STATUS_ERROR, error=f"进程启动失败：{exc}", cmd=cmd)

        raw = (proc.stdout or "") + (proc.stderr or "")
        if proc.returncode != 0:
            return RunResult(
                ok=False,
                status=STATUS_ERROR,
                raw=raw,
                error=(proc.stderr or proc.stdout or f"exit code {proc.returncode}").strip()[:2000],
                cmd=cmd,
            )
        return RunResult(
            ok=True,
            status=STATUS_DONE,
            parsed_json=extract_json(proc.stdout or ""),
            raw=raw,
            cmd=cmd,
        )


def _which(name: str) -> Optional[str]:
    from shutil import which

    return which(name)


def extract_json(text: str) -> Any:
    """从 CLI 输出容错提取 JSON：先整体解析；失败则从左到右扫描顶层 JSON 对象
    （每命中一个即跳过其整体，避免进入子元素），取最后一个顶层对象
    （CLI 习惯：日志在前、结果 JSON 在末尾）。"""
    stripped = text.strip()
    if not stripped:
        return None
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        pass
    decoder = json.JSONDecoder()
    best: Any = None
    idx = 0
    while idx < len(text):
        if text[idx] in "{[":
            try:
                obj, end = decoder.raw_decode(text, idx)
                best = obj
                idx = end
                continue
            except json.JSONDecodeError:
                pass
        idx += 1
    return best
