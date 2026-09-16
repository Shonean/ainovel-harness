# -*- coding: utf-8 -*-
"""scorer × prompt 组合矩阵（Phase 4 §八-3）。

独立版本号 + 组合审计：history.json 记 (scorer_version, doc_hash, score) 的落地版——
扫描 harness_runs/promptopt/* 的 history.jsonl 与现场日志，重建每轮 (scorer 版本,
prompt 文档哈希, 分数) 矩阵，检测「涨分只发生在 scorer 刚改完」的可疑情况：

- 同一 doc_hash 在不同 scorer 版本下 best 分差 |Δ| ≥ 0.02 → 标 SUSPECT（分差来自评分器而非 prompt）；
- 同一轮内 doc 未变而分数跳变 → 记录待查（当前历史里 scorer 版本按轮记，不逐步记，故 v1 只做跨轮）。

纯离线，零网络。运行：python -X utf8 -m prompt_harness.promptopt.combo
产出：harness_runs/promptopt/phase4/combo_matrix.{json,md}
"""
from __future__ import annotations

import hashlib
import json
import re
import sys
from datetime import datetime
from pathlib import Path

PKG_DIR = Path(__file__).resolve().parent
HARNESS_ROOT = PKG_DIR.parent.parent
RUNS = HARNESS_ROOT / "harness_runs" / "promptopt"
OUT = RUNS / "phase4"

_DOC_RE = re.compile(r"^([0-9a-f]{12})$")


def _sha12(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:12]


def _scorer_version(run_dir: Path) -> str | None:
    """从现场日志/验收报告推断该轮评分器版本；推断不出返回 None。"""
    for log in sorted(RUNS.glob(f"{run_dir.name}_console.log")) + \
               sorted(RUNS.glob(f"{run_dir.name}_console*.log")):
        m = re.search(r"评分器版本锁定：v(\d+)", log.read_text(encoding="utf-8", errors="replace"))
        if m:
            return f"v{m.group(1)}"
    acc = run_dir / "ACCEPTANCE.md"
    if acc.exists() and "v1reward" in run_dir.name:
        return "v1"
    if "v3reward" in run_dir.name or "v2reward" in run_dir.name:
        return "v3" if "v3reward" in run_dir.name else "v2"
    return None


def build() -> dict:
    runs = []
    for run_dir in sorted(RUNS.iterdir()):
        if not run_dir.is_dir() or not (run_dir / "history.jsonl").exists():
            continue
        hist = []
        for line in (run_dir / "history.jsonl").read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                try:
                    hist.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        if not hist:
            continue
        best_path = run_dir / "best_prompt.md"
        doc_hash = _sha12(best_path.read_text(encoding="utf-8")) if best_path.exists() else "seed"
        scorer_v = _scorer_version(run_dir)
        accepts = [h for h in hist if h.get("action") == "accept"]
        cands = [float(h["cand_primary"]) for h in hist if h.get("cand_primary") is not None]
        runs.append({
            "run": run_dir.name,
            "scorer_version": scorer_v,
            "doc_hash": doc_hash,
            "steps": len(hist),
            "n_accept": len(accepts),
            "best_primary": round(max(cands), 4) if cands else None,
            "suspect_within_run": _within_run_jumps(hist),
        })

    # 跨轮可疑：同 doc_hash 不同 scorer_version 且 best 分差大
    suspects = []
    by_doc: dict[str, list[dict]] = {}
    for r in runs:
        by_doc.setdefault(r["doc_hash"], []).append(r)
    for doc_hash, group in by_doc.items():
        vers = {}
        for r in group:
            if r["scorer_version"] and r["best_primary"] is not None:
                vers.setdefault(r["scorer_version"], []).append(r)
        if len(vers) < 2:
            continue
        names = sorted(vers)
        for a, b in zip(names, names[1:]):
            ma = max(x["best_primary"] for x in vers[a])
            mb = max(x["best_primary"] for x in vers[b])
            if abs(mb - ma) >= 0.02:
                suspects.append({
                    "doc_hash": doc_hash, "scorer_a": a, "best_a": round(ma, 4),
                    "scorer_b": b, "best_b": round(mb, 4), "delta": round(mb - ma, 4),
                })
    return {"ts": datetime.now().isoformat(timespec="seconds"),
            "n_runs": len(runs), "runs": runs, "suspects": suspects}


def _within_run_jumps(hist: list[dict]) -> list[int]:
    """同轮内 accepted 分数单步跳 >0.02 的 step 号（doc 每步在变，仅提示性记录）。"""
    jumps = []
    prev = None
    for h in hist:
        if h.get("action") != "accept" or h.get("cand_primary") is None:
            continue
        cur = float(h["cand_primary"])
        if prev is not None and cur - prev >= 0.02:
            jumps.append(h.get("step"))
        prev = cur
    return [j for j in jumps if j is not None]


def render_markdown(res: dict) -> str:
    lines = [
        "# scorer × prompt 组合矩阵（Phase 4）",
        "",
        f"- 重建 {res['n_runs']} 轮 history；零网络离线审计",
        "",
        "| run | scorer | doc_hash | steps | accept | best | 轮内跳变 |",
        "|---|---|---|---|---|---|---|",
    ]
    for r in res["runs"]:
        lines.append(
            f"| {r['run']} | {r['scorer_version'] or '?'} | {r['doc_hash'][:12]} "
            f"| {r['steps']} | {r['n_accept']} | {r['best_primary'] if r['best_primary'] is not None else '—'} "
            f"| {','.join(map(str, r['suspect_within_run'])) or '—'} |"
        )
    lines += ["", "## 跨轮可疑（同 doc 换 scorer 后 |Δbest| ≥ 0.02）", ""]
    if res["suspects"]:
        for s in res["suspects"]:
            lines.append(f"- doc `{s['doc_hash']}`：{s['scorer_a']} best={s['best_a']} → "
                         f"{s['scorer_b']} best={s['best_b']}（Δ={s['delta']:+.4f}）→ **SUSPECT**")
    else:
        lines.append("- 无：未发现涨分只归因 scorer 更换的组合")
    return "\n".join(lines)


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    res = build()
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "combo_matrix.json").write_text(json.dumps(res, ensure_ascii=False, indent=2), encoding="utf-8")
    md = render_markdown(res)
    (OUT / "combo_matrix.md").write_text(md, encoding="utf-8")
    print(f"[combo] runs={res['n_runs']} suspects={len(res['suspects'])}")
    for s in res["suspects"]:
        print(f"  ⚠ {s['doc_hash']} {s['scorer_a']}→{s['scorer_b']} Δbest={s['delta']:+.4f}")
    print(f"[combo] -> {OUT / 'combo_matrix.md'}")


if __name__ == "__main__":
    main()
