# -*- coding: utf-8 -*-
"""Level-2 rollout 适配器（Phase 1）：prompt 文档 → 真实生成 → 七维评分。

任务口径与 Phase 0 一致：给定 val 场景素材（reference 章节），当前 prompt 文档
作为 system 指导改写生成；产物走 scorer_adapter.score() 统一口径打分。

样本池拆分：
- train 池：每步 rollout 用（喂 analyst）；
- heldout 池：gate 评估用，不参与 reflect，防「在评估集上优化」。
flavor 审阅可按池关闭（省 LLM 调用；flavor=None 时 gate 自动跳过该维守护）。
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from ..llm_client import chat_completion
from . import scorer_adapter
from .gate import eval_from_rollouts


class PromptGenAdapter:
    def __init__(
        self,
        *,
        train_scenes: list[dict[str, Any]],
        heldout_scenes: list[dict[str, Any]],
        temperature: float = 0.8,
        eval_temperature: float = 0.3,
        flavor_on_train: bool = False,
        flavor_on_eval: bool = True,
        concurrency: int = 4,
        max_tokens: int = 2048,
    ):
        self.train_scenes = {s["id"]: s for s in train_scenes}
        self.heldout_scenes = {s["id"]: s for s in heldout_scenes}
        self.temperature = temperature
        self.eval_temperature = eval_temperature
        self.flavor_on_train = flavor_on_train
        self.flavor_on_eval = flavor_on_eval
        self.concurrency = concurrency
        self.max_tokens = max_tokens
        self.rollout_sink: Path | None = None  # 由 trainer 设置；落盘正文供复盘

    # ── 生成 ──────────────────────────────────────────────────────────────
    def _gen_user_msg(self, scene: dict[str, Any]) -> str:
        return (
            f"【任务】基于下面的原文素材改写生成正文。\n"
            f"章节：{scene.get('chapter', '')}｜类型：{scene.get('category', '')}｜"
            f"目标字数：约 {scene.get('target_len', 400)} 字\n"
            f"【原文素材】\n{scene['reference']}\n\n只输出正文，不要任何解释。"
        )

    async def _gen_one(self, doc_text: str, scene: dict[str, Any], temperature: float) -> dict[str, Any]:
        res = await chat_completion(
            system=doc_text,
            user=self._gen_user_msg(scene),
            call_type="promptopt_gen",
            temperature=temperature,
            max_tokens=self.max_tokens,
        )
        return {"id": scene["id"], "scene": scene, "output": res.get("content") or "", "error": res.get("error")}

    # ── rollout / evaluate ───────────────────────────────────────────────
    async def rollout(
        self,
        doc_text: str,
        *,
        pool: str = "train",
        keep_output: bool = True,
    ) -> list[dict[str, Any]]:
        """跑一个池的全部样本。返回 rollout dict 列表（含 dims/score/output）。"""
        scenes = list(self.train_scenes.values()) if pool == "train" else list(self.heldout_scenes.values())
        if not scenes:
            raise ValueError(f"样本池 {pool} 为空")
        # train 池高温求多样性；held-out 低温求评估稳定（gate 严格比较，噪声必须小）
        temp = self.temperature if pool == "train" else self.eval_temperature
        sem = asyncio.Semaphore(self.concurrency)

        async def _one(scene: dict[str, Any]) -> dict[str, Any]:
            async with sem:
                g = await self._gen_one(doc_text, scene, temp)
            return g

        gens = await asyncio.gather(*[_one(s) for s in scenes])
        items = [
            {
                "id": g["id"],
                "generated": g["output"],
                "reference": g["scene"]["reference"],
                "scene": g["scene"],
                "target_len": g["scene"].get("target_len", 0),
            }
            for g in gens
        ]
        flavor = self.flavor_on_train if pool == "train" else self.flavor_on_eval
        scores = await scorer_adapter.score_many(items, run_flavor_review=flavor, concurrency=self.concurrency)
        outs: list[dict[str, Any]] = []
        for g, s in zip(gens, scores):
            gen_error = g.get("error")
            dims = {k: s.get(k) for k in ("fact", "char", "plot", "syn", "flavor", "completion")}
            rec = {
                "id": g["id"],
                "pool": pool,
                "score": float(s.get("composite") or 0.0),
                "dims": dims,
                "ok": bool(not gen_error and not s.get("errors")),
                "errors": (["gen"] if gen_error else []) + list(s.get("errors") or []),
                "output": g["output"] if keep_output else "",
            }
            outs.append(rec)
            if self.rollout_sink is not None and g["output"]:
                d = self.rollout_sink
                d.mkdir(parents=True, exist_ok=True)
                (d / f"{pool}_{g['id']}.txt").write_text(g["output"], encoding="utf-8")
        return outs

    async def evaluate(self, doc_text: str) -> dict[str, Any]:
        """held-out 评估 → gate EvalResult 形状（primary/dims/...）。"""
        rollouts = await self.rollout(doc_text, pool="heldout", keep_output=False)
        return eval_from_rollouts(rollouts)
