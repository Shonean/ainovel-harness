"""改编层 v0 — story.ink → story.ink.json 编译（规格 §9.2）。

经 subprocess 调 `node compile_ink.cjs`（vendored inkjs 编译器，无额外二进制依赖）。
编译失败 → errors 带行号与原文行，调用方将 validation.ok 置 false，pack 不视为
有效产物。
"""
from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

NODE_DIR = Path(__file__).resolve().parent / "node"
COMPILE_SCRIPT = NODE_DIR / "compile_ink.cjs"
DEFAULT_TIMEOUT_S = 60


@dataclass
class CompileError:
    line: int
    message: str
    severity: str = "Error"


@dataclass
class CompileResult:
    ok: bool = False
    story_json: str | None = None
    errors: list[CompileError] = field(default_factory=list)
    warnings: list[CompileError] = field(default_factory=list)
    node_path: str = ""

    def error_details(self, ink_text: str | None = None) -> list[str]:
        """人读错误串（含原文行，供 validation.json detail）。"""
        out = []
        lines = (ink_text or "").splitlines()
        for e in self.errors:
            src = f" | 原文：{lines[e.line - 1].strip()}" if 0 < e.line <= len(lines) else ""
            out.append(f"line {e.line}: {e.message}{src}")
        return out


def compile_ink(ink_text: str, timeout_s: int = DEFAULT_TIMEOUT_S) -> CompileResult:
    node = shutil.which("node")
    if not node:
        return CompileResult(
            ok=False,
            errors=[CompileError(line=0, message="未找到 node 可执行文件（编译需要 Node runner）")],
        )
    if not COMPILE_SCRIPT.is_file():
        return CompileResult(
            ok=False,
            errors=[CompileError(line=0, message=f"编译脚本缺失：{COMPILE_SCRIPT}")],
        )
    try:
        proc = subprocess.run(
            [node, str(COMPILE_SCRIPT)],
            input=ink_text.encode("utf-8"),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout_s,
        )
    except subprocess.TimeoutExpired:
        return CompileResult(
            ok=False, node_path=node,
            errors=[CompileError(line=0, message=f"ink 编译超时（>{timeout_s}s）")],
        )
    except OSError as exc:
        return CompileResult(
            ok=False, node_path=node,
            errors=[CompileError(line=0, message=f"node 启动失败：{exc}")],
        )

    if proc.returncode == 0:
        return CompileResult(ok=True, story_json=proc.stdout.decode("utf-8"), node_path=node)

    # 失败：stderr 是 JSON {ok:false, errors:[...]}
    raw = proc.stderr.decode("utf-8", "ignore").strip()
    errors: list[CompileError] = []
    warnings: list[CompileError] = []
    try:
        data = json.loads(raw.splitlines()[-1] if raw else "{}")
        for e in data.get("errors") or []:
            item = CompileError(
                line=int(e.get("line") or 0),
                message=str(e.get("message") or ""),
                severity=str(e.get("severity") or "Error"),
            )
            (warnings if item.severity != "Error" else errors).append(item)
    except (json.JSONDecodeError, IndexError, ValueError, AttributeError):
        errors.append(CompileError(
            line=0, message=f"ink 编译器异常输出：{raw[:800] or '（空）'}"))
    if not errors:
        errors.append(CompileError(line=0, message=f"ink 编译失败（exit {proc.returncode}）"))
    return CompileResult(ok=False, errors=errors, warnings=warnings, node_path=node)
