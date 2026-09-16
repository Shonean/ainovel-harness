# -*- coding: utf-8 -*-
"""Checkpoint（Phase 1）：prompt_vXXXX.md + history.jsonl + best_* + resume。

- 每步存 prompt_v{step:04d}.md（该步结束时的文档快照，被拒步=回滚后的文档）；
- history.jsonl 只增：每步一条记录（edits/gate/分数），resume 与复盘共用；
- best_prompt.md 恒为验证最优快照 + best.json 记录来源 step 与分数；
- load_latest 支持 resume：返回 (最近 step, 该步文档文本)。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .artifacts import PromptDoc


class Checkpointer:
    def __init__(self, run_dir: str | Path):
        self.run_dir = Path(run_dir)
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self.ckpt_dir = self.run_dir / "checkpoints"
        self.ckpt_dir.mkdir(exist_ok=True)
        self.history_path = self.run_dir / "history.jsonl"
        self.best_path = self.run_dir / "best_prompt.md"
        self.best_meta_path = self.run_dir / "best.json"

    # ── 写 ────────────────────────────────────────────────────────────────
    def save_step(self, step: int, doc: PromptDoc, record: dict[str, Any]) -> Path:
        p = self.ckpt_dir / f"prompt_v{step:04d}.md"
        doc.save(p)
        record = {**record, "step": step, "doc_file": p.name}
        with open(self.history_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
        return p

    def update_best(self, step: int, doc: PromptDoc, primary: float) -> bool:
        best = self.load_best()
        if best is not None and primary <= best["primary"] + 1e-9:
            return False
        self.best_path.write_text(doc.text, encoding="utf-8")
        self.best_meta_path.write_text(
            json.dumps({"step": step, "primary": round(primary, 6)}, ensure_ascii=False),
            encoding="utf-8",
        )
        return True

    # ── 读 ────────────────────────────────────────────────────────────────
    def load_best(self) -> dict[str, Any] | None:
        if not self.best_meta_path.exists():
            return None
        try:
            return json.loads(self.best_meta_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return None

    def history(self) -> list[dict[str, Any]]:
        if not self.history_path.exists():
            return []
        out = []
        for line in self.history_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        return out

    def load_latest(self) -> tuple[int, PromptDoc] | None:
        """resume：返回 (最近 step, 该步文档)。无 checkpoint 返回 None。"""
        steps = sorted(int(p.stem.split("v")[1]) for p in self.ckpt_dir.glob("prompt_v*.md"))
        if not steps:
            return None
        latest = steps[-1]
        return latest, PromptDoc.load(self.ckpt_dir / f"prompt_v{latest:04d}.md")
