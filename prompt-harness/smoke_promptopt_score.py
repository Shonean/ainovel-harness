# -*- coding: utf-8 -*-
"""PromptOpt Phase 0 收尾冒烟：统一评分入口跑通验证。

1) 从 val_variants.jsonl 抽 8 条（faithful/vivid/terse 混合），走
   scorer_adapter.score() 全维度（含 LLM AI 味审阅），确认各维返回正常。
2) 15 条 canary 走同一入口（关 LLM 审阅提速）+ check_canary_scores，
   确认 canary 上限约束当前全部通过（评分器没放过坏样本）。

产出 → harness_runs/promptopt/phase0_smoke/report.md + scores.json
"""
from __future__ import annotations

import asyncio
import json
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "prompt-harness"))

from prompt_harness.promptopt.scorer_adapter import score_many  # noqa: E402
from prompt_harness.promptopt.canary import load_canaries, check_canary_scores  # noqa: E402

DATA = ROOT / "prompt-harness" / "prompt_harness" / "promptopt" / "data"
OUTDIR = ROOT / "prompt-harness" / "harness_runs" / "promptopt" / "phase0_smoke"
N_SMOKE = 8


def pick_variants() -> list[dict]:
    by_profile: dict[str, list[dict]] = defaultdict(list)
    for line in (DATA / "val_variants.jsonl").read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            r = json.loads(line)
            if not r.get("error"):
                by_profile[r["profile"]].append(r)
    picked: list[dict] = []
    i = 0
    while len(picked) < N_SMOKE:
        added = False
        for prof in ("faithful", "vivid", "terse"):
            if i < len(by_profile[prof]):
                picked.append(by_profile[prof][i])
                added = True
                if len(picked) >= N_SMOKE:
                    break
        if not added:
            break
        i += 1
    return picked


async def main() -> None:
    variants = pick_variants()
    print(f"[smoke] val variants: {len(variants)}")

    items = [
        {
            "id": f"{v['scene_id']}/{v['profile']}",
            "generated": v["text"],
            "reference": v["reference"],
            "target_len": v.get("target_len", 0),
        }
        for v in variants
    ]
    scored = await score_many(items, run_flavor_review=True, concurrency=4)

    canaries = load_canaries()
    print(f"[smoke] canaries: {len(canaries)}")
    c_items = [
        {"id": c["id"], "generated": c["text"], "reference": c["reference"]}
        for c in canaries
    ]
    c_scored = await score_many(c_items, run_flavor_review=False, concurrency=5)
    violations = check_canary_scores(c_scored)

    OUTDIR.mkdir(parents=True, exist_ok=True)
    (OUTDIR / "scores.json").write_text(
        json.dumps(
            {"val_smoke": scored, "canary_scores": c_scored, "canary_violations": violations},
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    lines = ["# PromptOpt Phase 0 冒烟报告", ""]
    lines.append("## 统一评分入口（val 变体 ×8，含 LLM 味审阅）")
    lines.append("")
    lines.append("| id | fact | char | plot | syn | flavor | completion | composite | errors |")
    lines.append("|---|---|---|---|---|---|---|---|---|")
    for s in scored:
        lines.append(
            f"| {s['id']} | {s['fact']} | {s['char']} | {s['plot']} | {s['syn']} "
            f"| {s['flavor']} | {s['completion']} | {s['composite']} | {','.join(s['errors']) or '-'} |"
        )
    lines.append("")
    ok_dims = all(not s["errors"] for s in scored)
    lines.append(f"全维度无 error：{'是' if ok_dims else '否'}")
    lines.append("")
    lines.append("## Canary 对抗集（×15，启发式口径）")
    lines.append("")
    lines.append("| id | attack | composite | fact | char | plot | flavor |")
    lines.append("|---|---|---|---|---|---|---|")
    for c, s in zip(canaries, c_scored):
        lines.append(
            f"| {c['id']} | {c.get('attack','')} | {s['composite']} | {s['fact']} "
            f"| {s['char']} | {s['plot']} | {s['flavor']} |"
        )
    lines.append("")
    lines.append(f"canary 上限违规数：{len(violations)}（0 = 当前评分器未放过坏样本）")
    for v in violations:
        lines.append(f"- 违规 {v}")
    (OUTDIR / "report.md").write_text("\n".join(lines), encoding="utf-8")

    print(f"[smoke] dims_ok={ok_dims} canary_violations={len(violations)} -> {OUTDIR/'report.md'}")


if __name__ == "__main__":
    asyncio.run(main())
