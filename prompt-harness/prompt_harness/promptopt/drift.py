# -*- coding: utf-8 -*-
"""生产漂移监控（Phase 4 §八-5）。

每周用锁定评分器 + 生产 prompt（fixed_prompts.json 的 ladder_invariant_l4）在
固定 val 子集（与 Phase 3 held-out 同 6 场景）上跑一次生成+评分，追加
drift_log.jsonl；均值低于基线 -0.02 即告警（基线 = 首次记录或 --baseline 指定值）。

上分告警同样记录（分数无端上涨也可能是评分服务漂移，如 embedding 服务换版）。

用法（cd prompt-harness）：
  python -X utf8 -m prompt_harness.promptopt.drift                # 跑一次并记录
  python -X utf8 -m prompt_harness.promptopt.drift --baseline 0.42
产出：harness_runs/promptopt/phase4/drift_log.jsonl（追加式）
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import sys
from datetime import datetime
from pathlib import Path

from .l4_adapter import L4Adapter
from .. import scorer_params as _sp
from ..fixed_prompts import get_ladder_invariant

PKG_DIR = Path(__file__).resolve().parent
HARNESS_ROOT = PKG_DIR.parent.parent
OUT = HARNESS_ROOT / "harness_runs" / "promptopt" / "phase4"
DRIFT_LOG = OUT / "drift_log.jsonl"

# 与 run_promptopt.HELDOUT_IDS 保持一致（Phase 3 收口时冻结的 6 场景）
DRIFT_IDS = ["val_010", "val_011", "val_029", "val_018", "val_023", "val_027"]
ALERT_DELTA = -0.02   # 相对基线掉分告警阈值
RISE_DELTA = 0.02     # 无端上分也记录（可能是评分服务漂移）


def _load_scenes() -> dict[str, dict]:
    data = json.loads((HARNESS_ROOT / "prompt_harness/promptopt/data/val_scenes.json")
                      .read_text(encoding="utf-8"))
    by_id = {s["id"]: s for s in data["scenes"]}
    missing = [i for i in DRIFT_IDS if i not in by_id]
    if missing:
        raise RuntimeError(f"drift 场景缺失：{missing}")
    return {i: by_id[i] for i in DRIFT_IDS}


def _last_mean() -> float | None:
    if not DRIFT_LOG.exists():
        return None
    rows = [json.loads(l) for l in DRIFT_LOG.read_text(encoding="utf-8").splitlines() if l.strip()]
    return rows[-1]["mean"] if rows else None


async def run(baseline: float | None = None) -> dict:
    scenes = _load_scenes()
    inv4 = get_ladder_invariant("l4").strip()
    if not inv4:
        raise RuntimeError("fixed_prompts.json 缺 ladder_invariant_l4")
    doc_text = "<!-- FIELD:ladder_invariant_l4 -->\n" + inv4
    doc_hash = hashlib.sha1(inv4.encode("utf-8")).hexdigest()[:12]

    adapter = L4Adapter(
        train_scenes=list(scenes.values()), heldout_scenes=list(scenes.values()),
        eval_temperature=0.3, flavor_on_eval=True, concurrency=4,
        canary_caps=None,  # drift 只看分布，攻击面由 canary_expand 周期体检
    )
    rollouts = await adapter.rollout(doc_text, pool="eval", keep_output=False)
    ok = [r for r in rollouts if r["ok"]]
    mean = sum(r["score"] for r in ok) / len(ok) if ok else 0.0
    scorer_v = f"v{_sp._cached().get('version', '?')}"

    prev = _last_mean()
    base = baseline if baseline is not None else (prev if prev is not None else mean)
    delta = round(mean - base, 4)
    alert = delta <= ALERT_DELTA
    rise = delta >= RISE_DELTA

    rec = {
        "ts": datetime.now().isoformat(timespec="seconds"),
        "doc_hash": doc_hash, "scorer_version": scorer_v,
        "n": len(ok), "mean": round(mean, 4),
        "baseline": round(base, 4) if base is not None else None,
        "delta": delta, "alert": alert, "rise": rise,
        "per_scene": [{"id": r["id"], "score": round(r["score"], 4),
                       "dims": r["dims"]} for r in ok],
    }
    OUT.mkdir(parents=True, exist_ok=True)
    with open(DRIFT_LOG, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return rec


def trend(last: int = 8) -> list[dict]:
    """drift_log 最近 N 次采样（跨周趋势视图用）。"""
    if not DRIFT_LOG.is_file():
        return []
    rows = []
    for line in DRIFT_LOG.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            try:
                rows.append(json.loads(line))
            except Exception:
                continue
    return rows[-last:]


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    if "--trend" in sys.argv:  # 只看趋势不新采样（零成本）
        rows = trend()
        if not rows:
            print("[drift] 无采样记录")
            return
        base = rows[0]["mean"]
        print(f"[drift-trend] 采样 {len(rows)} 次，参照首采 {rows[0]['ts']} mean={base}")
        for r in rows:
            flag = " ⚠ALERT" if r.get("alert") else (" ⚠RISE" if r.get("rise") else "")
            print(f"  {r['ts']} scorer={r['scorer_version']} doc={r['doc_hash']} "
                  f"mean={r['mean']} Δvs首采={round(r['mean'] - base, 4):+.4f}{flag}")
        hashes = {r["doc_hash"] for r in rows}
        vers = {r["scorer_version"] for r in rows}
        if len(hashes) > 1 or len(vers) > 1:
            print(f"  ⚠ 期间发生 doc_hash/scorer 变更：docs={hashes} scorers={vers}（趋势跨版本不可比）")
        return
    baseline = None
    if "--baseline" in sys.argv:
        baseline = float(sys.argv[sys.argv.index("--baseline") + 1])
    rec = asyncio.run(run(baseline))
    print(f"[drift] {rec['ts']} scorer={rec['scorer_version']} doc={rec['doc_hash']} "
          f"n={rec['n']} mean={rec['mean']} baseline={rec['baseline']} delta={rec['delta']:+.4f}")
    for p in rec["per_scene"]:
        print(f"  {p['id']}: {p['score']:.4f} dims={json.dumps(p['dims'])[:90]}")
    if rec["alert"]:
        print("  ⚠ DRIFT ALERT：生产均值跌破基线 -0.02，检查评分服务/模型版本/fixed_prompts 是否被改")
        sys.exit(2)
    if rec["rise"]:
        print("  ⚠ 分数无端上涨（≥+0.02）：可能是评分服务漂移，人工复核")
    print(f"[drift] -> {DRIFT_LOG}")


if __name__ == "__main__":
    main()
