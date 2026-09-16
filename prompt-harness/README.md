# promptopt — ablation-driven prompt pipeline optimizer

> Measure which layers of your prompt pipeline actually matter — and optimize them
> without fooling yourself.

`promptopt` is the technical core of the Prompt Harness project: a small, domain-agnostic
optimization loop (Rollout → Reflect → Aggregate → Select → Update → Evaluate) wrapped in
**anti-Goodhart guardrails** (adversarial canaries, three-judge disagreement detection,
scorer×prompt combination audits, drift monitoring). It grew out of optimizing a
long-form fiction generation pipeline, but the kernel only ever sees one thing:

```
rollout(artifact, items) -> scores        serialize() / apply_patch() -> bounded edits
```

## Headline: the ablation table

Before optimizing anything, we ran a **layer-sufficiency ablation** across three novels
(same model, tiers A/B/C/D = progressively richer context):

| Context tier | What the model sees | Score |
|---|---|---|
| A | outline only | ~0.30 |
| B | + chapter briefs | ~0.30 |
| C | + scene beats | ~0.30 |
| **D** | **+ full scene-level prompt (l4)** | **0.67** |

A/B/C are statistically flat: **l1–l3 contribute nothing measurable to generation
quality** — only the finest layer does. That single finding reorganized the whole
product (l1–l3 were re-purposed as human approval checkpoints, not context).
Ablation first, optimization second.

## Guardrail results (why this doesn't fool itself)

| Signal | Before | After |
|---|---|---|
| Scorer–human alignment (Kendall τ, 3 variants) | **−0.11** (hand-tuned weights) | **0.50** (refit v3, 16 held-out scenes) |
| Adversarial canary violations | 9 / 15 | **0 / 19** (3 new probes flagged as open holes — composite gate still blocks them) |
| Auto-optimization convergence | — | held-out 0.4052 → 0.4453 in 20 steps; **test gate rejected the write-back** (−0.008, within noise) — the gate doing its job |
| Toy demo (offline) | 0.15 | **0.85** in one accepted edit; 2 overfit hacks rejected |

## Quickstart (offline, no API key)

```bash
python examples/toy_system_prompt/demo.py
```

A scripted "optimizer" submits three candidate edits to a toy system prompt: a real
improvement (accepted), a sycophancy hack (rejected by canary), a filler hack
(rejected by canary + gate). Shows the full gate arithmetic end to end.

## Using the real optimizer

```bash
# novel domain (adapters/novel.py): optimize the production l4 scene prompt
python run_promptopt.py --steps 20 --fresh        # ~2h / ~$0.7
python run_phase4.py --all                        # weekly guardrail sweep (~$0.4)
python -m prompt_harness.promptopt.drift --trend  # drift history, zero cost
```

## Writing your own adapter (any domain)

```python
from prompt_harness.promptopt.core import PromptAdapter, Trainer, TrainerConfig

class MyAdapter(PromptAdapter):
    async def rollout(self, doc_text, pool="train", keep_output=False):
        # run your pipeline with the candidate prompt, score each output
        return [{"id": ..., "score": ..., "dims": {...}, "ok": True, "errors": []}]

    async def evaluate(self, doc_text):
        return {"primary": ..., "dims": {...},
                "canary_violations": <int>, "n": ...}   # gate input

# Trainer(doc adapter=..., run_dir=...) drives the six-stage loop with
# bounded edits, protected regions, checkpoints and strict-accept gates.
```

The core (`prompt_harness/promptopt/core/`) has **zero domain imports** (enforced by
test); novel-specific generation/scoring lives in `prompt_harness/promptopt/adapters/`.

## The five guardrails

| Tool | What it catches |
|---|---|
| `dispute.py` — three-judge disagreement | reward misalignment: heuristic vs LLM-judge vs human, Kendall τ per scene → annotation queue |
| `audit.py` — top-K audit | highest-scoring events get human verdicts (`ok/hack/unclear`) logged to an append-only file |
| `combo.py` — combination matrix | "score jumped right after the scorer changed" — same document, different scorer, Δ ≥ 0.02 → SUSPECT |
| `canary_expand.py` | permanent adversarial probes; every discovered hack becomes a regression test (`add` auto-caps scores) |
| `drift.py` | weekly fixed-sample monitoring of the production prompt + frozen scorer; alert at −0.02 |

## Repository layout

```
prompt_harness/promptopt/   core loop (core/), novel adapters (adapters/), guardrail tools
examples/toy_system_prompt/ offline minimal example
harness_runs/promptopt/     every run's artifacts, reports and acceptance records
references/CL4R1T4S/        pinned external reference corpus (gitignored, notes kept)
docs/WEBAPP_zh.md           legacy Chinese docs for the standalone web app
```

## Status

Research-grade, single-maintainer. The novel pipeline artifacts are Chinese-language
web fiction; the kernel and the toy example are English.

License: Apache-2.0 (to be attached when the repo is published).
