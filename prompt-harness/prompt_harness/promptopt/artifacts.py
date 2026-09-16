# -*- coding: utf-8 -*-
"""Prompt 文档 artifact（Phase 1）。

可训练 artifact = 一篇 markdown 文档（生产里就是生成 prompt 的可编辑层）。
文档格式约定：
- ``<!-- FIELD:key -->`` 独占一行开一个逻辑区，到下一个 FIELD 标记或文档尾；
- 保护区用 edits.PROTECTED-BEGIN/END 包裹（如 SLOW_UPDATE 区）。
文档本身保持纯 markdown——checkpoint 直接存盘，生产链路可整篇取用。
"""
from __future__ import annotations

import re
from pathlib import Path

_FIELD_RE = re.compile(r"^<!--\s*FIELD:([\w\-]+)\s*-->\s*$", re.M)

SLOW_UPDATE_TEMPLATE = (
    "<!-- PROTECTED-BEGIN:SLOW_UPDATE -->\n"
    "<!-- FIELD:SLOW_UPDATE -->\n"
    "（epoch 末由慢更新通道写入跨 epoch 指导；step 级编辑不可修改本区）\n"
    "<!-- PROTECTED-END:SLOW_UPDATE -->"
)


class PromptDoc:
    """markdown 文档的轻封装：解析 FIELD 区、统计、读写盘。"""

    def __init__(self, text: str):
        self.text = text or ""

    # ── IO ────────────────────────────────────────────────────────────────
    @classmethod
    def load(cls, path: str | Path) -> "PromptDoc":
        return cls(Path(path).read_text(encoding="utf-8"))

    def save(self, path: str | Path) -> None:
        Path(path).write_text(self.text, encoding="utf-8")

    # ── 结构 ──────────────────────────────────────────────────────────────
    def fields(self) -> dict[str, str]:
        """{key: 区内容}（到下一个 FIELD 标记或文档尾）。"""
        ms = list(_FIELD_RE.finditer(self.text))
        out: dict[str, str] = {}
        for i, m in enumerate(ms):
            start = m.end()
            end = ms[i + 1].start() if i + 1 < len(ms) else len(self.text)
            out[m.group(1)] = self.text[start:end].strip()
        return out

    def field_names(self) -> list[str]:
        return [m.group(1) for m in _FIELD_RE.finditer(self.text)]

    def ensure_slow_region(self) -> "PromptDoc":
        """无 SLOW_UPDATE 保护区则补上（幂等；基础设施行为，不算 step 编辑）。"""
        if "PROTECTED-BEGIN:SLOW_UPDATE" in self.text:
            return self
        sep = "" if self.text.endswith("\n") else "\n"
        self.text = self.text + sep + "\n" + SLOW_UPDATE_TEMPLATE + "\n"
        return self

    def replace_slow_region(self, body: str) -> "PromptDoc":
        """整块替换 SLOW_UPDATE 区内文（慢更新专用通道）。"""
        m = re.search(
            r"(<!-- PROTECTED-BEGIN:SLOW_UPDATE -->\n)(.*?)(<!-- PROTECTED-END:SLOW_UPDATE -->)",
            self.text,
            re.S,
        )
        if not m:
            self.ensure_slow_region()
            return self.replace_slow_region(body)
        self.text = self.text[: m.start(2)] + body.strip() + "\n" + self.text[m.start(3):]
        return self

    def slow_body(self) -> str:
        m = re.search(
            r"<!-- PROTECTED-BEGIN:SLOW_UPDATE -->\n(.*?)<!-- PROTECTED-END:SLOW_UPDATE -->",
            self.text,
            re.S,
        )
        return (m.group(1) if m else "").strip()

    # ── 视图 ──────────────────────────────────────────────────────────────
    def optimizer_view(self, *, rejection_digest: str = "", meta_notes: str = "") -> str:
        """给 optimizer LLM 看的完整上下文：文档全文 + 被拒历史 + 跨 epoch 笔记。"""
        parts = [self.text]
        if rejection_digest.strip():
            parts.append("\n【最近被拒的 edit（勿再提同类，先诊断为什么被拒）】\n" + rejection_digest)
        if meta_notes.strip():
            parts.append("\n【你的跨 epoch 策略笔记（meta skill，仅你可读）】\n" + meta_notes)
        return "\n".join(parts)

    def stats(self) -> dict[str, int]:
        return {
            "chars": len(self.text),
            "lines": self.text.count("\n") + 1,
            "fields": len(self.field_names()),
            "protected": self.text.count("PROTECTED-BEGIN:"),
        }
