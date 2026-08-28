"""自动代码重载（Auto Code Watcher）

系统级监视代码目录变化 → 自动触发 rebuild-and-restart。
效果：修改 app/ 或 prompt_harness/*.py 或 frontend/src/* 后，几秒内自动重新构建并重启，
无需手动点「应用新代码」按钮。

安全设计：
- 防抖：检测到变化后等 DEBOUNCE 秒再确认，稳定才触发（避免编辑中途多次重启）
- 排除自身产物：dist/、node_modules/、target/、__pycache__/、logs/、frontend_version.txt
  （rebuild 自己写的东西不触发循环）
- 可用环境变量 AINOVEL_AUTO_REBUILD=0 关闭
"""
from __future__ import annotations

import logging
import os
import threading
import time
import urllib.request
from pathlib import Path

log = logging.getLogger("dashboard.auto_rebuild")

POLL_INTERVAL = 3.0     # 扫描间隔（秒）
DEBOUNCE = 12.0         # 变化稳定后等几秒再触发（连续改多个文件时合并成一次 rebuild）
COOLDOWN = 60.0         # rebuild 触发后的冷却期（秒）——冷却期内检测到变化只记 pending 不立即触发，
                        # 等冷却结束才真正 rebuild，避免「rebuild 刚完成又因仍在编辑」反复重启
WATCH_SUBDIRS = [
    # 主系统后端源码（只这两个目录的 .py；scripts/ 下是运行时数据，不监视）
    "app/dashboard",
    # prompt-harness 后端源码
    "prompt-harness/prompt_harness",
    # 前端源码（只 src，不含 dist/node_modules/src-tauri）
    "app/dashboard/frontend/src",
]
# 只监视代码文件（py/jsx/js/css/json 配置），忽略 md/README 等文档
WATCH_EXTS = {".py", ".jsx", ".js", ".css", ".json"}
EXCLUDE_DIRS = {
    "node_modules", "dist", "target", "__pycache__", ".git", "logs",
    ".ainovel", ".story-system", "harness_runs", ".claude",
    # 构建/运行时/临时产物：绝不触发自动 rebuild
    "src-tauri", "gen", "evals", "tests", "scripts",
    "agents", "tools", "skills", "references", "templates", "web",
}
# 前缀排除：命中即跳过（相对仓库根，用 / 归一化比较）。
# 注意：frontend/ 只在「经 app/dashboard 递归」时排除；src 由 WATCH_SUBDIRS 的
# 独立项 app/dashboard/frontend/src 直接监视，因此这里排除非 src 的 frontend 产物。
EXCLUDE_PREFIXES = (
    "app/dashboard/frontend/src-tauri",
    "app/dashboard/frontend/dist",
    "app/dashboard/frontend/node_modules",
    "app/dashboard/frontend/package-lock.json",
    "app/dashboard/logs/",
)
# frontend/ 下仅保留 src 子目录；其余（src-tauri/gen/dist/临时文件）一律不监视。
# 实现：在 _iter_watch_files 中对 app/dashboard 扫描时跳过 frontend 子树，
# frontend/src 由独立 WATCH_SUBDIRS 项覆盖。
SKIP_SUBTREE = {"frontend"}
EXCLUDE_FILES = {"frontend_version.txt", "package-lock.json", "package.json"}


def _code_root() -> Path:
    # app.py 在 ainovel-write/app/dashboard/ → 上两级为 ainovel-write/
    return Path(__file__).resolve().parent.parent.parent


def _iter_watch_files() -> list[tuple[Path, float, int]]:
    """扫描代码目录，返回 [(path, mtime, size)] 指纹。"""
    root = _code_root()
    out: list[tuple[Path, float, int]] = []
    for sub in WATCH_SUBDIRS:
        d = root / sub
        if not d.is_dir():
            continue
        for p in d.rglob("*"):
            if not p.is_file():
                continue
            # 跳过 frontend 子树（src 由独立 WATCH_SUBDIRS 项精确覆盖）
            if SKIP_SUBTREE and set(p.relative_to(d).parts) & SKIP_SUBTREE:
                continue
            # 只监视代码文件
            if p.suffix not in WATCH_EXTS:
                continue
            # 排除目录（按路径片段）
            rel_parts = set(p.parts)
            if rel_parts & EXCLUDE_DIRS:
                continue
            # 前缀排除：相对仓库根路径（用 / 归一化）
            rel_str = str(p.relative_to(root)).replace("\\", "/")
            if any(rel_str.startswith(pref) for pref in EXCLUDE_PREFIXES):
                continue
            if p.name in EXCLUDE_FILES:
                continue
            try:
                st = p.stat()
                out.append((p, st.st_mtime, st.st_size))
            except OSError:
                pass
    return out


def _fingerprint(files: list[tuple[Path, float, int]]) -> str:
    return "\n".join(f"{p}|{m}|{s}" for p, m, s in sorted(files, key=lambda x: str(x[0])))


def _trigger_rebuild() -> None:
    """调用本机 rebuild-and-restart 端点（构建 + 重启 + 更新 version）。"""
    try:
        url = "http://127.0.0.1:8765/api/system/rebuild-and-restart"
        req = urllib.request.Request(url, data=b"", method="POST")
        with urllib.request.urlopen(req, timeout=8) as resp:
            resp.read()
        log.info("auto_rebuild: 代码变化 → 已触发 rebuild-and-restart")
    except Exception as e:  # noqa: BLE001
        log.warning("auto_rebuild: 触发 rebuild 失败（服务器可能正在重启）: %s", e)


class AutoCodeWatcher:
    """后台线程：轮询代码目录指纹，变化稳定后触发 rebuild。"""

    def __init__(self) -> None:
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._enabled = os.environ.get("AINOVEL_AUTO_REBUILD", "1") != "0"
        # 【2026-08-11 用户决定】不用自动更新（一直出问题），以后手动更新。
        # AutoCodeWatcher 反复触发 rebuild 导致 dist 反复丢失。永久默认关闭；
        # 如需恢复自动 reload，设 AINOVEL_DISABLE_WATCHER=0（重启后生效）。
        if self._enabled and os.environ.get("AINOVEL_DISABLE_WATCHER", "1") != "0":
            self._enabled = False

    def start(self) -> None:
        if not self._enabled:
            log.info("auto_rebuild: 已关闭（AINOVEL_AUTO_REBUILD=0）")
            return
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="auto-rebuild-watcher", daemon=True)
        self._thread.start()
        log.info("auto_rebuild: 已启动，监视 %s", [str(Path(_code_root()) / s) for s in WATCH_SUBDIRS])

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=3)

    def _run(self) -> None:
        # 首次扫描作为基线（不触发）
        base = _fingerprint(_iter_watch_files())
        cooldown_until = 0.0   # 冷却截止时间
        while not self._stop.is_set():
            time.sleep(POLL_INTERVAL)
            try:
                cur = _fingerprint(_iter_watch_files())
            except Exception:  # noqa: BLE001
                continue
            if cur == base:
                continue
            # 有变化 → 防抖：等 DEBOUNCE 秒，期间若继续变化则重置等待（合并连续编辑）
            pending = cur
            changed_at = time.time()
            while not self._stop.is_set():
                time.sleep(POLL_INTERVAL)
                try:
                    cur2 = _fingerprint(_iter_watch_files())
                except Exception:  # noqa: BLE001
                    continue
                if cur2 != pending:
                    pending = cur2
                    changed_at = time.time()
                if time.time() - changed_at >= DEBOUNCE:
                    break
            if self._stop.is_set():
                break

            # 冷却期检查：rebuild 后 COOLDOWN 秒内不立即触发。
            # 若还在冷却期，把新状态暂存为 base，等冷却结束后的下一轮变化再触发——
            # 但更稳妥：冷却期内积累的变化在冷却结束后一次性处理（不丢改动）。
            if time.time() < cooldown_until:
                # 还在冷却 → 接受当前状态为基线，等冷却结束；若有新变化下一轮再进防抖
                base = pending
                continue

            base = pending  # 更新基线，防止 rebuild 产物触发自身
            _trigger_rebuild()
            cooldown_until = time.time() + COOLDOWN
