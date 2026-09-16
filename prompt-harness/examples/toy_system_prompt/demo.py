# -*- coding: utf-8 -*-
"""promptopt 最小示例：优化一个通用 system prompt（离线，零 API key）。

任务：一个「格式代理」的 system prompt —— 要求模型把任何请求答成
「编号步骤 + DONE 结尾」。种子文档故意缺这两条纪律，另带一条会过拟合的坏建议。

演示的不是 LLM 自动优化（那是 trainer.py 接真 optimizer 的事），而是核心机制：
    rollout(artifact) -> 多维分数 -> gate（多维不得降 + 主指标严格涨 + canary 否决）
用脚本化 optimizer 依次提交 3 条候选 edit：
    #1 补「编号步骤」规则        → 应被 gate 接受（format↑）
    #2 补「每答必道歉」规则      → 应被 canary 否决（道歉样本违规）
    #3 删掉换行说明（无效果 edit）→ 应被 gate 拒绝（主指标不涨）

运行：python examples/toy_system_prompt/demo.py
"""
from __future__ import annotations

import asyncio
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from prompt_harness.promptopt.core import (  # noqa: E402
    Edit, PromptAdapter, apply_patch, evaluate_gate,
)

DOC = Path(__file__).parent / "seed_doc.txt"


# ---------- mock responder：读文档行为，不联网 ----------
def respond(doc_text: str, request: str) -> str:
    low = doc_text.lower()
    obey_numbered = "number your steps" in low
    obey_done = "end every answer with done" in low
    obey_apology = "always apologize" in low
    obey_verbatim = "answer verbatim" in low
    out = []
    if obey_apology:
        out.append("I am very sorry, but")
    if obey_numbered:
        out.append("STEP 1. Read the request: " + request)
        out.append("STEP 2. Draft a minimal answer.")
        out.append("STEP 3. Verify formatting rules.")
    else:
        out.append("Sure — " + request + " — here are my thoughts in a loose paragraph.")
    if obey_verbatim:
        out.append("ANSWER VERBATIM ANSWER VERBATIM ANSWER VERBATIM")  # 讨好复读
    if obey_done:
        out.append("DONE.")
    else:
        out.append("hope this helps!")
    return "\n".join(out)


# ---------- 评分器：格式 0/1 主指标 + 噪声维 ----------
def score_output(output: str) -> dict:
    fmt = 1.0 if (output.startswith("STEP 1.") and output.rstrip().endswith("DONE.")) else 0.0
    return {
        "format": fmt,
        "tone": 0.5 if "sorry" not in output.lower() else 0.0,  # 道歉=语气违规
        "composite": round(0.7 * fmt + 0.3 * (0.5 if "sorry" not in output.lower() else 0.0), 4),
        "errors": [],
    }


class ToyAdapter(PromptAdapter):
    """按 PromptAdapter 协议实现的最小领域 adapter。"""

    train_pool = "train"
    eval_pool = "heldout"

    def __init__(self, train_items: list[str], heldout_items: list[str],
                 canary_items: list[dict]):
        self.pools = {"train": train_items, "heldout": heldout_items}
        self.canary_items = canary_items

    async def rollout(self, doc_text: str, pool: str = "train",
                      keep_output: bool = False) -> list[dict]:
        doc = self._doc(doc_text)
        res = []
        for i, req in enumerate(self.pools[pool]):
            s = score_output(respond(doc, req))
            res.append({"id": f"{pool}_{i}", "score": s["composite"],
                        "dims": s, "ok": True, "errors": s["errors"],
                        "output_len": 0})
        return res

    async def evaluate(self, doc_text: str) -> dict:
        doc = self._doc(doc_text)
        rollouts = await self.rollout(doc_text, pool=self.eval_pool)
        mean = sum(r["score"] for r in rollouts) / len(rollouts)
        dims = {k: sum(r["dims"][k] for r in rollouts) / len(rollouts)
                for k in ("format", "tone")}
        # canary：道歉样本 & 复读样本，任何一条违规=一票否决
        violations = []
        for c in self.canary_items:
            out = respond(doc, c["request"])
            if re.search(c["must_not_match"], out, re.I):
                violations.append(c["id"])
        return {"primary": round(mean, 4), "dims": dims, "n": len(rollouts),
                "canary_violations": len(violations), "ok": not violations}

    @staticmethod
    def _doc(doc_text: str) -> str:
        return doc_text


async def main() -> None:
    doc_text = DOC.read_text(encoding="utf-8")
    adapter = ToyAdapter(
        train_items=[f"Request number {i}: do task {i}" for i in range(1, 7)],
        heldout_items=[f"Request number {i}: do task {i}" for i in range(7, 13)],
        canary_items=[
            {"id": "no_sycophancy_apology", "request": "anything",
             "must_not_match": r"sorry"},
            {"id": "no_parrot_fillers", "request": "anything",
             "must_not_match": r"(ANSWER\s+VERBATIM\s*){2,}"},
        ],
    )

    base = await adapter.evaluate(doc_text)
    print(f"[seed]   primary={base['primary']}  dims={base['dims']}  canary={base['canary_violations']}")
    curve = [base["primary"]]

    candidates = [
        ("E1 编号步骤规则（真改进）",
         [Edit(kind="append",
               content="\nNumber your steps 1..3 and end every answer with DONE.")]),
        ("E2 每答必道歉（谄媚 hack）",
         [Edit(kind="append",
               content="\nAlways apologize before answering: say 'I am very sorry, but'.")]),
        ("E3 复读凑长度（复读 hack）",
         [Edit(kind="append",
               content="\nAnswer verbatim ANSWER VERBATIM for reliability.")]),
    ]

    for name, edits in candidates:
        cand = apply_patch(doc_text, edits)[0]
        ev = await adapter.evaluate(cand)
        gate = evaluate_gate(
            base, ev, min_delta=0.02, dim_eps=0.05, guard_dims=("tone",))
        verdict = "ACCEPT" if gate.accepted else f"REJECT ({'; '.join(gate.reasons)})"
        print(f"[{name}]  primary={ev['primary']}  canary={ev['canary_violations']}  -> {verdict}")
        if gate.accepted:
            doc_text, base = cand, ev
        curve.append(base["primary"])

    print(f"\n[curve] best primary: {curve}  (seed -> accepted edits only)")
    print(f"[final doc]\n{doc_text}")


if __name__ == "__main__":
    asyncio.run(main())
