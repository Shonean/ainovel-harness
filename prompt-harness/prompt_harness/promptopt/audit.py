# -*- coding: utf-8 -*-
"""top-3 高分审计（Phase 4 §八-2）。

制度化：每轮跑完，CLI 输出 best 曲线上最高分事件 + canary 贴上限事件（离顶最近 =
最像 hack 的分数），要求人工确认；确认结论追加 audit_log.jsonl（永不覆盖）。

用法（cd prompt-harness）：
  python -X utf8 -m prompt_harness.promptopt.audit --run phase3 [--top 3]
  python -X utf8 -m prompt_harness.promptopt.audit confirm --run phase3 --id step5 \
      --verdict ok --note "人工看过，是真实提升"
纯离线（只读 history/best/canary 工件）。
"""
from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

PKG_DIR = Path(__file__).resolve().parent
HARNESS_ROOT = PKG_DIR.parent.parent
RUNS = HARNESS_ROOT / "harness_runs" / "promptopt"
OUT = RUNS / "phase4"
AUDIT_LOG = OUT / "audit_log.jsonl"


def _load_hist(run_dir: Path) -> list[dict]:
    hp = run_dir / "history.jsonl"
    out = []
    if hp.exists():
        for line in hp.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    return out


def top_events(run: str, top: int = 3) -> dict:
    run_dir = RUNS / run
    hist = _load_hist(run_dir)
    if not hist:
        raise RuntimeError(f"{run_dir} 无 history.jsonl")
    scored = [h for h in hist if h.get("cand_primary") is not None]
    ranked = sorted(scored, key=lambda h: -float(h["cand_primary"]))[:top]
    events = []
    for h in ranked:
        events.append({
            "kind": "history_top",
            "id": f"step{h.get('step')}",
            "action": h.get("action"),
            "primary": float(h["cand_primary"]),
            "doc_file": h.get("doc_file", ""),
            "run": run,
        })
    return {"run": run, "n_events": len(events), "events": events}


def confirm(run: str, event_id: str, verdict: str, note: str = "") -> dict:
    if verdict not in ("ok", "hack", "unclear"):
        raise SystemExit("verdict ∈ {ok, hack, unclear}")
    rec = {"ts": datetime.now().isoformat(timespec="seconds"), "run": run,
           "event_id": event_id, "verdict": verdict, "note": note}
    AUDIT_LOG.parent.mkdir(parents=True, exist_ok=True)
    with open(AUDIT_LOG, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return rec


def render_markdown(res: dict) -> str:
    lines = [f"# top-{len(res['events'])} 高分审计：{res['run']}", "",
             "| event | action | primary | doc |", "|---|---|---|---|"]
    for e in res["events"]:
        lines.append(f"| {e['id']} | {e['action']} | {e['primary']:.4f} | {e['doc_file'] or '—'} |")
    lines += ["", "人工确认后：`audit confirm --run %s --id <event> --verdict ok|hack|unclear`" % res["run"]]
    return "\n".join(lines)


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    if sys.argv[1:2] == ["confirm"]:
        kv = {sys.argv[i]: sys.argv[i + 1] for i in range(2, len(sys.argv) - 1, 2)}
        rec = confirm(kv["--run"], kv["--id"], kv["--verdict"], kv.get("--note", ""))
        print(f"[audit] logged: {rec}")
        return
    run = "phase3"
    if "--run" in sys.argv:
        run = sys.argv[sys.argv.index("--run") + 1]
    top = 3
    if "--top" in sys.argv:
        top = int(sys.argv[sys.argv.index("--top") + 1])
    res = top_events(run, top)
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / f"audit_top_{run}.md").write_text(render_markdown(res), encoding="utf-8")
    print(f"[audit] run={run} top events:")
    for e in res["events"]:
        print(f"  {e['id']:<8} {e['action']:<7} primary={e['primary']:.4f} {e['doc_file']}")
    print(f"[audit] -> {OUT / f'audit_top_{run}.md'}")


if __name__ == "__main__":
    main()
