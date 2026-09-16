# -*- coding: utf-8 -*-
"""Canary 扩充机制（Phase 4 §八-4）。

「每发现新 hack 方式永久追加一条」的落地工具：

- verify   全量重跑现有 canary 集，报告越限（回归体检）
- add      把新 hack 样本加入 canary.jsonl：现场打分 → 自动定上限（观测值+margin），
           若评分器根本压不住该攻击（attacked dim 观测 ≥ 0.7）也照常入册，但标注
           "open hole"——这类条目是下一轮评分器重拟合的靶子，不是静默放过。

用法（cd prompt-harness）：
  python -X utf8 -m prompt_harness.promptopt.canary_expand verify
  python -X utf8 -m prompt_harness.promptopt.canary_expand add new_hacks.jsonl
新样本行格式：{"attack": "...", "reference": "...", "text": "...", "note": "...",
              "max_fact": 0.x, "max_char": 0.x, "max_plot": 0.x}  （max_* 可省略=自动）
"""
from __future__ import annotations

import asyncio
import json
import sys
from datetime import datetime
from pathlib import Path

from .canary import load_canaries
from .scorer_adapter import score_many

PKG_DIR = Path(__file__).resolve().parent
DATA = PKG_DIR / "data"
CANARY = DATA / "canary.jsonl"
OUT = Path(__file__).resolve().parent.parent.parent / "harness_runs" / "promptopt" / "phase4"

_CAP_DIMS = ("max_fact", "max_char", "max_plot", "max_flavor", "max_composite")


def _next_id(existing: list[dict]) -> str:
    nums = [int(c["id"].split("_")[1]) for c in existing if c.get("id", "").startswith("canary_")]
    return f"canary_{(max(nums) + 1 if nums else 1):03d}"


async def verify() -> dict:
    """全量 canary 回归体检：逐条打分 → check 上限。"""
    canaries = load_canaries()
    items = [{"id": c["id"], "generated": c["text"], "reference": c.get("reference", ""),
              "target_len": 0} for c in canaries]
    scored = await score_many(items, run_flavor_review=True, concurrency=4)
    from .canary import check_canary_scores
    violations = check_canary_scores(scored)
    res = {
        "ts": datetime.now().isoformat(timespec="seconds"),
        "n_canaries": len(canaries), "n_violations": len(violations),
        "violations": violations,
        "per_canary": [
            {"id": c["id"], "attack": c.get("attack", ""),
             "composite": s["composite"], "fact": s["fact"], "char": s["char"],
             "plot": s["plot"], "flavor": s["flavor"]}
            for c, s in zip(canaries, scored)
        ],
    }
    return res


async def add(path: str | Path, margin: float = 0.05) -> dict:
    """新 hack 样本入册：现场打分 → 自动上限 → 追加 canary.jsonl。"""
    src = Path(path)
    new_rows = [json.loads(l) for l in src.read_text(encoding="utf-8").splitlines() if l.strip()]
    if not new_rows:
        raise RuntimeError(f"{src} 无样本行")
    existing = load_canaries()
    known = {c.get("attack") for c in existing}

    scored = await score_many(
        [{"id": f"new_{i}", "generated": r["text"], "reference": r.get("reference", ""), "target_len": 0}
         for i, r in enumerate(new_rows)],
        run_flavor_review=True, concurrency=4)

    appended = []
    for r, s in zip(new_rows, scored):
        if r.get("attack") in known:
            print(f"[canary] 跳过重复攻击：{r['attack']}")
            continue
        entry = {"id": _next_id(existing + appended), "attack": r["attack"],
                 "reference": r["reference"], "text": r["text"],
                 "note": r.get("note", "")}
        holes = []
        for cap_key, obs_key in (("max_fact", "fact"), ("max_char", "char"),
                                 ("max_plot", "plot"), ("max_flavor", "flavor"),
                                 ("max_composite", "composite")):
            if r.get(cap_key) is not None:
                entry[cap_key] = float(r[cap_key])
                continue
            obs = s.get(obs_key)
            if obs is None:
                continue
            entry[cap_key] = round(min(1.0, float(obs) + margin), 2)
            if float(obs) >= 0.7:
                holes.append(f"{obs_key}={obs}")
        if holes:
            entry["note"] = (entry["note"] + "；" if entry["note"] else "") + \
                f"OPEN HOLE（评分器未压住：{','.join(holes)}），重拟合靶子"
        appended.append(entry)
        print(f"[canary] +{entry['id']} {entry['attack']}"
              + ("  ⚠ " + entry["note"].split("；")[-1] if "OPEN HOLE" in entry["note"] else ""))

    if appended:
        with open(CANARY, "a", encoding="utf-8") as f:
            for e in appended:
                f.write(json.dumps(e, ensure_ascii=False) + "\n")
    return {"added": [e["id"] for e in appended], "n_total": len(existing) + len(appended)}


def _print_verify(res: dict) -> None:
    print(f"[canary] n={res['n_canaries']} violations={res['n_violations']}")
    for v in res["violations"]:
        print(f"  ✗ {v['id']} {v['attack'][:30]} {v['dim']} got={v['got']} cap={v['cap']}")
    if not res["violations"]:
        print("  ✓ 全部在上限内")


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    cmd = sys.argv[1] if len(sys.argv) > 1 else "verify"
    if cmd == "verify":
        res = asyncio.run(verify())
        OUT.mkdir(parents=True, exist_ok=True)
        (OUT / "canary_verify.json").write_text(json.dumps(res, ensure_ascii=False, indent=2), encoding="utf-8")
        _print_verify(res)
    elif cmd == "add":
        if len(sys.argv) < 3:
            raise SystemExit("用法：canary_expand add <file.jsonl>")
        res = asyncio.run(add(sys.argv[2]))
        print(f"[canary] added={res['added']} total={res['n_total']}")
    else:
        raise SystemExit(f"未知命令：{cmd}")


if __name__ == "__main__":
    main()
