"""
workbench_logger.py —— 书的工作台完整日志系统（含 LLM 输入输出）

服务「工作台微调设计」：每步发了什么 prompt、得到什么输出，全程可追溯。

- **捕获点**：llm_client.chat_completion/chat_json（工作台全部 LLM 调用唯一入口）→
  自动全覆盖：阶梯 l1-l5、污染检查、片段扩写、AI 味审阅、弧对话、灵感工坊、写书检索。
- **存储**：logs/workbench/<book_slug>/<YYYY-MM-DD>.jsonl（按书分子目录，按天滚动）。
- **上下文**：ContextVar —— 工作台驱动函数设 set_workbench_ctx(book/arc/chapter/step/run_id)，
  llm_client 记录时读取；call_type 兜底步骤语义。
- **滚动**：软上限（单文件 >5MB 保留最后 2000 行），与 workflows/_prompt_log 一致。
- **分工**：本日志 = 完整 I/O（微调分析用）；llm.jsonl = 元数据（成本/性能统计），两者并存。

记录字段：
  ts / seq / book / arc / chapter / step / run_id / call_type / model / endpoint /
  system / user / output / prompt_tokens / completion_tokens / total_tokens /
  latency_ms / retry_count / status / error
"""
from __future__ import annotations

import contextvars
import json
import os
import re
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

# 软上限滚动参数（与 workflows/_prompt_log 一致）
_MAX_BYTES = 5 * 1024 * 1024  # 5MB
_KEEP_LINES = 2000
_RETENTION_DAYS = 30     # 【2026-08-22】保留期：超期 workbench 日志自动清理
_last_cleanup_date = ""

# ============================================================
# 工作台上下文（ContextVar）
# ============================================================

_CTX: contextvars.ContextVar[dict[str, Any]] = contextvars.ContextVar(
    "workbench_trace_ctx", default={}
)


def set_workbench_ctx(**fields: Any) -> contextvars.Token:
    """设置工作台追踪上下文（返回 token，用 clear_workbench_ctx(token) 恢复）。

    例：set_workbench_ctx(book="示例书", arc="第1弧", chapter=3, step="l4", run_id="r_xxx")
    只更新传入字段，其余保持。返回 token 供 try/finally 恢复。
    """
    merged = dict(_CTX.get())
    for k, v in fields.items():
        if v is not None and v != "":
            merged[k] = v
    return _CTX.set(merged)


def get_workbench_ctx() -> dict[str, Any]:
    """读取当前工作台上下文（没有则空 dict）。"""
    return dict(_CTX.get())


def clear_workbench_ctx(token: contextvars.Token) -> None:
    """恢复到 token 时的工作台上下文。"""
    _CTX.reset(token)


# ============================================================
# 文件写入
# ============================================================

_base_dir: Path | None = None
_open_handles: dict[tuple[str, str], Any] = {}   # (book_slug, date) -> file handle
_seq_cache: dict[tuple[str, str], int] = {}      # (book_slug, date) -> 最后 seq


def _log_base_dir() -> Path:
    """logs/workbench 目录。"""
    global _base_dir
    if _base_dir is None:
        _base_dir = Path(__file__).resolve().parent.parent / "logs" / "workbench"
        _base_dir.mkdir(parents=True, exist_ok=True)
    return _base_dir


def _book_slug(book: str) -> str:
    """书名清洗为目录名：保留中文/字母/数字，去非法字符，空则 unknown。"""
    if not book:
        return "unknown"
    slug = re.sub(r"[\\/:*?\"<>|]", "_", str(book)).strip()
    return slug or "unknown"


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _get_handle(book_slug: str, date: str):
    """获取 (book_slug, date) 的文件句柄（懒打开 + 自动滚动）。"""
    key = (book_slug, date)
    handle = _open_handles.get(key)
    if handle is None:
        day_dir = _log_base_dir() / book_slug
        day_dir.mkdir(parents=True, exist_ok=True)
        path = day_dir / f"{date}.jsonl"
        # 新句柄首次打开时读取末尾 seq，避免进程重启后 seq 重复
        last_seq = 0
        if path.is_file():
            try:
                size = path.stat().st_size
                if size > 0:
                    with open(path, "rb") as fh:
                        fh.seek(-min(size, 4096), 2)
                        tail = fh.read().decode("utf-8", errors="ignore")
                    lines = [l for l in tail.split("\n") if l.strip()]
                    if lines:
                        last = json.loads(lines[-1])
                        last_seq = int(last.get("seq", 0) or 0)
            except Exception:
                pass
        handle = open(path, "a", encoding="utf-8")
        _open_handles[key] = handle
        _seq_cache[key] = last_seq
    return handle


def log_trace(
    *,
    model: str,
    endpoint: str = "",
    call_type: str = "",
    system: str = "",
    user: str = "",
    history: list | None = None,
    output: str = "",
    prompt_tokens: int = 0,
    completion_tokens: int = 0,
    total_tokens: int = 0,
    latency_ms: int = 0,
    retry_count: int = 0,
    status: str = "success",
    error: str = "",
) -> None:
    """写一条完整 LLM 调用 trace 到工作台日志。失败静默（不影响主流程）。"""
    try:
        ctx = get_workbench_ctx()
        book = ctx.get("book", "") or ""
        book_slug = _book_slug(book)
        date = _today()
        _cleanup_expired()
        handle = _get_handle(book_slug, date)

        key = (book_slug, date)
        seq = _seq_cache.get(key, 0) + 1
        _seq_cache[key] = seq

        record = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "seq": seq,
            "book": book,
            "arc": ctx.get("arc", ""),
            "chapter": ctx.get("chapter", ""),
            "step": ctx.get("step", ""),
            "run_id": ctx.get("run_id", ""),
            "call_type": call_type,
            "model": model,
            "endpoint": endpoint,
            "system": system,
            "user": user,
            "history": history,
            "output": output,
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": total_tokens,
            "latency_ms": latency_ms,
            "retry_count": retry_count,
            "status": status,
            "error": error,
        }
        handle.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
        handle.flush()

        # 软上限滚动：超 5MB → 保留最后 _KEEP_LINES 行
        path = _log_base_dir() / book_slug / f"{date}.jsonl"
        if path.stat().st_size > _MAX_BYTES:
            _rollover(key, handle, path)
    except Exception:
        pass


def _rollover(key: tuple[str, str], handle: Any, path: Path) -> None:
    """把超限文件截为最后 N 行。

    【2026-08-22】旧实现 readlines() 全量进内存 + 原地 "w" 重写，在 Windows 上
    被占用/杀毒锁一下就 PermissionError → 外层 except:pass 静默吞掉，结果
    unknown/2026-08-15.jsonl 涨到 199MB 滚动从未生效。改为：deque 只留尾部
    N 行 + temp 文件 + os.replace 原子替换；失败打印告警不再静默。
    """
    try:
        from collections import deque
        tail: deque[str] = deque(maxlen=_KEEP_LINES)
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                tail.append(line)
        handle.close()
        tmp = path.with_name(path.name + ".tmp")
        with open(tmp, "w", encoding="utf-8") as fh:
            fh.writelines(tail)
        os.replace(tmp, path)
        _open_handles[key] = open(path, "a", encoding="utf-8")
    except Exception as e:
        print(f"[workbench_logger] rollover 失败 {path}: {e}")


def _cleanup_expired() -> None:
    """【2026-08-22】删除超过保留期的 workbench 日志（每天最多跑一次）。"""
    global _last_cleanup_date
    today = _today()
    if _last_cleanup_date == today:
        return
    _last_cleanup_date = today
    try:
        base = _log_base_dir()
        if not base.is_dir():
            return
        cutoff = (datetime.now(timezone.utc) - timedelta(days=_RETENTION_DAYS)).strftime("%Y-%m-%d")
        for bdir in base.iterdir():
            if not bdir.is_dir():
                continue
            for f in bdir.glob("*.jsonl"):
                if f.stem[:10] < cutoff:
                    f.unlink(missing_ok=True)
    except Exception:
        pass


def new_run_id() -> str:
    """生成一次批量生成/操作的 run_id。"""
    return f"run_{uuid.uuid4().hex[:8]}"


# ============================================================
# 查询
# ============================================================

def _iter_files(book_slug: str = "", date: str = "") -> list[Path]:
    """枚举工作台日志文件（book_slug 过滤 + date 过滤）。"""
    base = _log_base_dir()
    if not base.is_dir():
        return []
    files: list[Path] = []
    for bdir in sorted(base.iterdir()):
        if not bdir.is_dir() or (book_slug and bdir.name != book_slug):
            continue
        for f in bdir.glob("*.jsonl"):
            if date and f.stem != date:
                continue
            files.append(f)
    files.sort(reverse=True)  # 最新在前
    return files


def list_books() -> list[dict[str, Any]]:
    """列出有日志的书 + 最新日期 + 条数。"""
    base = _log_base_dir()
    if not base.is_dir():
        return []
    books: list[dict[str, Any]] = []
    for bdir in sorted(base.iterdir()):
        if not bdir.is_dir():
            continue
        files = sorted(bdir.glob("*.jsonl"), reverse=True)
        if not files:
            continue
        total = 0
        for f in files:
            try:
                total += sum(1 for _ in open(f, "r", encoding="utf-8"))
            except Exception:
                pass
        books.append({
            "book": bdir.name,
            "latest_date": files[0].stem,
            "total": total,
        })
    return books


def _read_lines(f: Path) -> list[dict[str, Any]]:
    lines: list[dict[str, Any]] = []
    try:
        with open(f, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    lines.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    except Exception:
        pass
    return lines


def list_traces(
    book: str = "",
    date: str = "",
    call_type: str = "",
    limit: int = 100,
) -> list[dict[str, Any]]:
    """列出 trace 摘要（最新在前）。"""
    files = _iter_files(_book_slug(book), date)
    results: list[dict[str, Any]] = []
    for f in files:
        for evt in _read_lines(f):
            if call_type and evt.get("call_type") != call_type:
                continue
            results.append({
                "seq": evt.get("seq"),
                "ts": evt.get("ts"),
                "book": evt.get("book", ""),
                "arc": evt.get("arc", ""),
                "chapter": evt.get("chapter", ""),
                "step": evt.get("step", ""),
                "run_id": evt.get("run_id", ""),
                "call_type": evt.get("call_type", ""),
                "model": evt.get("model", ""),
                "status": evt.get("status", "success"),
                "latency_ms": evt.get("latency_ms", 0),
                "prompt_len": len(evt.get("user", "") or ""),
                "output_len": len(evt.get("output", "") or ""),
                "total_tokens": evt.get("total_tokens", 0),
            })
    results.sort(key=lambda r: r.get("ts") or "", reverse=True)
    return results[:limit]


def read_trace(book: str = "", date: str = "", seq: int = 0) -> dict[str, Any] | None:
    """读取单条完整 trace（含 system/user/output）。"""
    for f in _iter_files(_book_slug(book), date):
        for evt in _read_lines(f):
            if evt.get("seq") == seq:
                return evt
    return None


def summary(book: str = "", date: str = "") -> dict[str, Any]:
    """按 call_type 聚合：次数 / 平均延迟 / token 总量 / 错误数。"""
    files = _iter_files(_book_slug(book), date)
    by_call: dict[str, dict[str, Any]] = {}
    total_calls = 0
    total_tokens = 0
    error_count = 0
    for f in files:
        for evt in _read_lines(f):
            total_calls += 1
            ct = evt.get("call_type") or "unknown"
            if ct not in by_call:
                by_call[ct] = {"calls": 0, "latency_sum_ms": 0, "tokens": 0, "errors": 0}
            by_call[ct]["calls"] += 1
            by_call[ct]["latency_sum_ms"] += evt.get("latency_ms", 0) or 0
            by_call[ct]["tokens"] += evt.get("total_tokens", 0) or 0
            if evt.get("status") == "error":
                by_call[ct]["errors"] += 1
                error_count += 1
            total_tokens += evt.get("total_tokens", 0) or 0
    for ct, st in by_call.items():
        st["avg_latency_ms"] = round(st["latency_sum_ms"] / max(st["calls"], 1), 0)
        st.pop("latency_sum_ms", None)
    return {
        "total_calls": total_calls,
        "total_tokens": total_tokens,
        "error_count": error_count,
        "by_call_type": by_call,
    }
