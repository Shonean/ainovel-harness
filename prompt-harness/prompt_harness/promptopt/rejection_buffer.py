# -*- coding: utf-8 -*-
"""Rejection buffer（Phase 1）：被拒 edit + 分数差 + 拒因的持久流水账。

用途：喂回 optimizer 防打转（digest 注入下几步的 analyst prompt），
以及事后复盘「optimizer 提过什么烂主意」。jsonl 只增不删。
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


class RejectionBuffer:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.entries: list[dict[str, Any]] = []
        if self.path.exists():
            for line in self.path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line:
                    try:
                        self.entries.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue

    def append(
        self,
        *,
        step: int,
        edits: list[dict[str, Any]],
        reasons: list[str],
        primary_delta: float,
    ) -> None:
        rec = {"step": step, "edits": edits, "reasons": reasons, "primary_delta": round(primary_delta, 4), "ts": _ts()}
        self.entries.append(rec)
        tmp = self.path.with_suffix(".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            for e in self.entries:
                f.write(json.dumps(e, ensure_ascii=False) + "\n")
        os.replace(tmp, self.path)

    def digest(self, max_entries: int = 6) -> str:
        """最近被拒记录的紧凑摘要（注入 optimizer 视图）。"""
        out = []
        for e in self.entries[-max_entries:]:
            edit_descs = "; ".join(
                f"{x.get('kind')}@{(x.get('anchor') or '')[:20]}（{x.get('note', '')[:30]}）"
                for x in e.get("edits", [])[:3]
            )
            reason = "; ".join(e.get("reasons", []))[:120]
            out.append(f"step{e['step']} Δ={e['primary_delta']:+.3f} 被拒[{edit_descs}] 原因: {reason}")
        return "\n".join(out)

    def __len__(self) -> int:
        return len(self.entries)


def _ts() -> str:
    from datetime import datetime

    return datetime.now().isoformat(timespec="seconds")
