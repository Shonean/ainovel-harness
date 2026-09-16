# -*- coding: utf-8 -*-
"""通用两层训练器（Phase 1）：Rollout → Reflect → Aggregate → Select → Update → Evaluate。

纪律（照搬 SkillOpt，领域无关）：
- 每步最多 L 条有界 edit（cosine 或 autonomous LR），protected 区不可 step 编辑；
- gate 严格上涨才接受，否则整步回滚（文档不动），被拒 edit 进 rejection buffer；
- 每步 checkpoint + history；best_* 恒为验证最优；支持 resume 续跑；
- epoch 末 slow update（同批样本新旧 artifact 纵向对比 → SLOW_UPDATE 保护区）
  + meta skill 笔记（只注入 optimizer）。
optimizer 走 llm_client 便宜模型（api_library 预设），gate 纯算术无 LLM。

适配器协议（Level-1/Level-2 各一份实现）：
- await adapter.rollout(doc_text, pool="train") -> rollout dict 列表
- await adapter.evaluate(doc_text) -> gate EvalResult dict
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .artifacts import PromptDoc
from .checkpoint import Checkpointer
from .edits import apply_patch
from .gate import evaluate_gate
from .lr import autonomous_lr, cosine_lr
from .reflect import aggregate_edits, propose_edits, select_edits
from .rejection_buffer import RejectionBuffer
from .slow_update import epoch_slow_update, update_meta_notes


@dataclass
class TrainerConfig:
    epochs: int = 1
    steps_per_epoch: int = 10
    l_max: int = 3
    l_min: int = 1
    lr_mode: str = "cosine"  # cosine | autonomous
    m_minibatch: int = 8
    min_delta: float = 0.01
    dim_eps: float = 0.05
    guard_dims: tuple[str, ...] = ("fact", "plot", "completion", "flavor")
    # 失败/成功相对切分：下 1/3 分位=失败批、上 1/3=成功批（适配任意分数尺度；
    # 分布退化——全同分——时全部计为失败）。绝对阈值已被证明不适配 v2 分数尺度。
    split_quantiles: tuple[float, float] = (0.33, 0.67)
    max_edit_chars: int = 600
    reflect_concurrency: int = 4
    slow_update: bool = True
    meta_notes: bool = True


@dataclass
class TrainerState:
    global_step: int = 0
    accepted: int = 0
    rejected: int = 0
    skipped: int = 0
    best_primary: float = float("-inf")
    best_step: int = -1
    curve: list[float] = field(default_factory=list)  # 每步结束后的 best primary


class Trainer:
    def __init__(
        self,
        *,
        adapter: Any,
        run_dir: str | Path,
        cfg: TrainerConfig | None = None,
    ):
        self.adapter = adapter
        self.cfg = cfg or TrainerConfig()
        self.run_dir = Path(run_dir)
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self.ck = Checkpointer(self.run_dir)
        self.rejbuf = RejectionBuffer(self.run_dir / "rejections.jsonl")
        self.notes_path = self.run_dir / "meta_notes.md"
        if hasattr(adapter, "rollout_sink"):
            adapter.rollout_sink = self.run_dir / "rollouts"
        self.state = TrainerState()

    # ── 主循环 ────────────────────────────────────────────────────────────
    async def run(self, seed_doc: str | None = None, *, resume: bool = False) -> dict[str, Any]:
        t0 = time.time()
        total = self.cfg.epochs * self.cfg.steps_per_epoch
        if resume:
            latest = self.ck.load_latest()
            if latest is None:
                raise RuntimeError("resume=True 但 run_dir 下没有任何 checkpoint")
            step0, doc = latest
            doc = PromptDoc(doc.text).ensure_slow_region()
            self.state.global_step = step0
            base_eval = await self.adapter.evaluate(doc.text)
            best = self.ck.load_best()
            if best:
                self.state.best_primary, self.state.best_step = best["primary"], best["step"]
            print(f"[trainer] resume 自 step {step0}（best={self.state.best_primary:.4f}），基线重测 primary={base_eval['primary']:.4f}")
        else:
            if not seed_doc:
                raise ValueError("全新训练必须给 seed_doc")
            doc = PromptDoc(seed_doc).ensure_slow_region()
            base_eval = await self.adapter.evaluate(doc.text)
            if self.ck.update_best(0, doc, base_eval["primary"]):
                self.state.best_primary, self.state.best_step = base_eval["primary"], 0
            print(f"[trainer] 基线 held-out primary={base_eval['primary']:.4f}")

        spe = self.cfg.steps_per_epoch
        epoch_logs: list[str] = []
        old_roll: list[dict[str, Any]] | None = None
        while self.state.global_step < total:
            self.state.global_step += 1
            step = self.state.global_step
            epoch = (step - 1) // spe + 1
            if (step - 1) % spe == 0:  # 新 epoch 开始
                epoch_logs = []
                old_roll = None
            record = await self._one_step(step, epoch, doc, base_eval, epoch_logs)
            if record.get("step_rollouts"):
                if old_roll is None:
                    old_roll = record["step_rollouts"]
                new_roll = record["step_rollouts"]
            else:
                new_roll = None
            if record["action"] == "accept":
                doc = PromptDoc(record["new_doc"])
                base_eval = record["cand_eval"]
                self.state.accepted += 1
                if base_eval["primary"] > self.state.best_primary + 1e-9:
                    self.state.best_primary = base_eval["primary"]
                    self.state.best_step = step
                    self.ck.update_best(step, doc, base_eval["primary"])
            elif record["action"] == "reject":
                self.state.rejected += 1
            else:
                self.state.skipped += 1
            self.state.curve.append(self.state.best_primary)
            self.ck.save_step(step, doc, record["history"])
            print(
                f"[trainer] step {step:>3} {record['action'].upper():<7} "
                f"primary={(record['cand_eval']['primary'] if record['action'] == 'accept' else base_eval['primary']):.4f} "
                f"best={self.state.best_primary:.4f}"
            )
            # epoch 末：slow update（同批样本纵向对比）+ meta notes
            if step % spe == 0 or step == total:
                if self.cfg.slow_update and old_roll and new_roll:
                    g = await epoch_slow_update(doc=doc, old_rollouts=old_roll, new_rollouts=new_roll, epoch=epoch)
                    epoch_logs.append(f"slow_update 写入 {len(g)} 字")
                if self.cfg.meta_notes:
                    rate = sum(1 for h in self.ck.history()[-spe:] if h.get("action") == "accept") / spe
                    notes = await update_meta_notes(notes_path=self.notes_path, epoch=epoch, step_logs=epoch_logs, accept_rate=rate)
                    if notes:
                        self.notes_path.write_text(notes, encoding="utf-8")

        return {
            "steps": self.state.global_step,
            "accepted": self.state.accepted,
            "rejected": self.state.rejected,
            "skipped": self.state.skipped,
            "best_primary": round(self.state.best_primary, 4),
            "best_step": self.state.best_step,
            "curve": [round(x, 4) for x in self.state.curve],
            "elapsed_sec": round(time.time() - t0, 1),
            "best_doc": str(self.ck.best_path),
            "rejections": len(self.rejbuf),
        }

    # ── 单步六阶段 ────────────────────────────────────────────────────────
    async def _one_step(
        self,
        step: int,
        epoch: int,
        doc: PromptDoc,
        base_eval: dict[str, Any],
        epoch_logs: list[str],
    ) -> dict[str, Any]:
        cfg = self.cfg
        # 1. Rollout（train 池）
        rollouts = await self.adapter.rollout(doc.text, pool="train")
        # 失败/成功相对切分：分位取样，保证两批都非空（全同分 → 全记失败）
        scores = sorted(float(r["score"]) for r in rollouts)
        if scores[0] == scores[-1]:
            failures, successes = list(rollouts), []
        else:
            n = len(scores)
            q_lo = scores[max(0, min(n - 1, int((n - 1) * cfg.split_quantiles[0])))]
            q_hi = scores[max(0, min(n - 1, int((n - 1) * cfg.split_quantiles[1])))]
            failures = [r for r in rollouts if float(r["score"]) <= q_lo]
            successes = [r for r in rollouts if float(r["score"]) >= q_hi]

        # 2. Select：本步最多 L 条 edit
        if cfg.lr_mode == "autonomous":
            fail_sum = "\n".join(f"{r['id']}={r['score']:.3f}" for r in failures) or "（无失败样本）"
            L, lr_note = await autonomous_lr(
                step=step, total_steps=cfg.epochs * cfg.steps_per_epoch,
                l_max=cfg.l_max, l_min=cfg.l_min,
                failures_summary=fail_sum,
                gate_reasons=self.rejbuf.digest(max_entries=1),
            )
        else:
            L = cosine_lr(step, cfg.epochs * cfg.steps_per_epoch, cfg.l_max, cfg.l_min)
            lr_note = f"cosine L={L}"

        # 3. Reflect（minibatch analyst 并行）→ 4. Aggregate
        meta = self.notes_path.read_text(encoding="utf-8") if self.notes_path.exists() else ""
        edits, logs = await propose_edits(
            failures=failures, successes=successes,
            doc_view=doc.optimizer_view(rejection_digest=self.rejbuf.digest(), meta_notes=meta),
            m_minibatch=cfg.m_minibatch, max_edit_chars=cfg.max_edit_chars,
            concurrency=cfg.reflect_concurrency,
        )
        epoch_logs.extend(logs)
        merged = aggregate_edits(edits)
        selected = select_edits(merged, L)

        def _hist(action: str, **extra: Any) -> dict[str, Any]:
            return {
                "action": action, "epoch": epoch, "lr": lr_note,
                "n_fail": len(failures), "n_succ": len(successes),
                "train_scores": [round(s, 4) for s in scores],
                "n_edits_proposed": len(edits), "n_merged": len(merged), "n_selected": len(selected),
                "base_primary": round(base_eval["primary"], 4),
                **extra,
            }

        if not selected:
            return {
                "action": "skip", "new_doc": doc.text, "cand_eval": base_eval, "step_rollouts": rollouts,
                "history": _hist("skip", note="本步无可采纳 edit（analyst 未提或全非法）"),
            }
        # 5. Update（bounded edits）
        new_text, applied, rejected_apply = apply_patch(doc.text, selected, max_edit_chars=cfg.max_edit_chars)
        if not applied:
            return {
                "action": "skip", "new_doc": doc.text, "cand_eval": base_eval, "step_rollouts": rollouts,
                "history": _hist("skip", note="全部 edit 应用失败", rejected_apply=rejected_apply),
                "selected": [e.to_dict() for e in selected],
            }
        # 6. Evaluate（held-out gate：严格上涨才接受，否则回滚）
        cand_eval = await self.adapter.evaluate(new_text)
        gate = evaluate_gate(base_eval, cand_eval, min_delta=cfg.min_delta, dim_eps=cfg.dim_eps, guard_dims=cfg.guard_dims)
        if gate.accepted:
            return {
                "action": "accept", "new_doc": new_text, "cand_eval": cand_eval, "step_rollouts": rollouts,
                "history": _hist("accept", cand_primary=round(cand_eval["primary"], 4), applied=applied,
                                 gate=gate.to_dict(), selected=[e.to_dict() for e in selected]),
            }
        self.rejbuf.append(step=step, edits=[e.to_dict() for e in selected], reasons=gate.reasons, primary_delta=gate.primary_delta)
        return {
            "action": "reject", "new_doc": doc.text, "cand_eval": base_eval, "step_rollouts": rollouts,
            "history": _hist("reject", cand_primary=round(cand_eval["primary"], 4), gate=gate.to_dict(),
                             applied=applied, rejected_apply=rejected_apply, selected=[e.to_dict() for e in selected]),
        }
