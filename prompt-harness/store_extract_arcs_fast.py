# -*- coding: utf-8 -*-
"""并发批量入库（替代同步 store_extract_arcs.py 的慢速串行版）。

后台跑被任务框架当"无输出长任务"杀掉 → 改用 asyncio 8 路并发调 store 端点，
单任务 ~16 弧并发处理，秒级完成（每弧 store 端点内部 LLM 建模板已并发）。

用法：python store_extract_arcs_fast.py <task_id> [--min-score 0.60] [--host http://127.0.0.1:8765]
幂等：already=True 的跳过不计新模板。
"""
import argparse
import asyncio
import json
import sys
import urllib.request
from pathlib import Path

USER_DATA = Path.home() / ".claude" / "ainovel-write" / "prompt-harness" / "plot_extract_results"


def _load_result(task_id: str, host: str) -> dict:
    fp = USER_DATA / f"{task_id}.json"
    if fp.is_file():
        return json.loads(fp.read_text(encoding="utf-8"))
    d = json.loads(urllib.request.urlopen(f"{host}/api/prompt-harness/optimize/status/{task_id}").read().decode("utf-8"))
    if d.get("status") != "done":
        raise RuntimeError(f"任务 {task_id} 状态={d.get('status')}，未完成")
    return d["result"]


async def _store_one(host: str, task_id: str, arc: int, min_score: float, sem: asyncio.Semaphore) -> dict:
    async with sem:
        body = json.dumps({"task_id": task_id, "arc": arc, "min_score": min_score}).encode("utf-8")
        req = urllib.request.Request(
            f"{host}/api/prompt-harness/plot-templates/extract-store",
            data=body, headers={"Content-Type": "application/json"}, method="POST")
        try:
            r = json.loads(urllib.request.urlopen(req, timeout=300).read().decode("utf-8"))
            return {"arc": arc, "ok": True, "templates": r.get("templates") or []}
        except Exception as exc:  # noqa: BLE001
            return {"arc": arc, "ok": False, "error": str(exc)[:200]}


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("task_id")
    ap.add_argument("--host", default="http://127.0.0.1:8765")
    ap.add_argument("--min-score", type=float, default=0.65)
    ap.add_argument("--concurrency", type=int, default=8)
    args = ap.parse_args()

    result = _load_result(args.task_id, args.host)
    report = result.get("report") or []
    q = [r for r in report
         if any(c.get("qualified") or float(c.get("score") or 0.0) >= args.min_score
                for c in r.get("chapters") or [])]
    print(f"任务 {args.task_id} | 弧 {len(report)} | 有≥{args.min_score}章的弧 {len(q)}", flush=True)

    sem = asyncio.Semaphore(args.concurrency)
    results = await asyncio.gather(*[
        _store_one(args.host, args.task_id, r["arc"], args.min_score, sem) for r in q])

    n_new = n_already = n_err = 0
    for r in results:
        if not r["ok"]:
            print(f"  [ERR] 弧{r['arc']}: {r['error']}", flush=True)
            n_err += 1
            continue
        tpls = r["templates"]
        for t in tpls:
            tt = t.get("template") or {}
            if tt.get("already"):
                n_already += 1
            else:
                n_new += 1
        print(f"  [OK] 弧{r['arc']} → {len(tpls)} 模板（新{sum(1 for t in tpls if not (t.get('template') or {}).get('already'))}/already{sum(1 for t in tpls if (t.get('template') or {}).get('already'))}）", flush=True)
    print(f"\n汇总: 入库 {n_new} | 已存在 {n_already} | 错误 {n_err}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
