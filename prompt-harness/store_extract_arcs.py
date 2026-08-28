"""把某次榨干任务的所有「达标弧」批量入库（/plot-templates/extract-store，幂等）。

用法：python store_extract_arcs.py <task_id> [--host http://127.0.0.1:8765] [--dry-run]
结果源：优先磁盘 data_dir/plot_extract_results/{task_id}.json（重启不丢），否则走状态端点。
只入库 qualified=True 且 template_id 为空的弧；已入库的会返回 already=True（幂等）。
"""
import argparse
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

USER_DATA = Path.home() / ".claude" / "ainovel-write" / "prompt-harness" / "plot_extract_results"


def _load_result(task_id: str, host: str) -> dict:
    fp = USER_DATA / f"{task_id}.json"
    if fp.is_file():
        return json.loads(fp.read_text(encoding="utf-8"))
    # 回退：状态端点 result
    url = f"{host}/api/prompt-harness/optimize/status/{task_id}"
    d = json.loads(urllib.request.urlopen(url).read().decode("utf-8"))
    if d.get("status") != "done":
        raise RuntimeError(f"任务 {task_id} 状态={d.get('status')}，未完成")
    return d["result"]


def _store(host: str, task_id: str, arc: int, min_score: float = 0.65) -> dict:
    body = json.dumps({"task_id": task_id, "arc": arc, "min_score": min_score}).encode("utf-8")
    req = urllib.request.Request(
        f"{host}/api/prompt-harness/plot-templates/extract-store",
        data=body, headers={"Content-Type": "application/json"}, method="POST")
    try:
        return json.loads(urllib.request.urlopen(req).read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return {"error": f"HTTP {e.code}: {e.read().decode('utf-8', errors='replace')[:200]}"}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("task_id")
    ap.add_argument("--host", default="http://127.0.0.1:8765")
    ap.add_argument("--min-score", type=float, default=0.65,
                    help="入库合格分（默认0.65；降到0.60可救回差一口气的章）")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    result = _load_result(args.task_id, args.host)
    report = result.get("report") or []
    # 【A1+A4+节拍覆盖】弧内有章 qualified（节拍覆盖达标）或 score≥min_score 即可入库
    q = [r for r in report
         if any(c.get("qualified") or float(c.get("score") or 0.0) >= args.min_score
                for c in r.get("chapters") or [])]
    print(f"任务 {args.task_id} | 弧 {len(report)} | 有≥{args.min_score}章的弧 {len(q)}")
    n_ok = n_already = n_err = 0
    for r in q:
        arc = r["arc"]
        name = r.get("name") or f"弧{arc}"
        if args.dry_run:
            print(f"  [DRY] [{arc}] {name} 第{r['start_chapter']}-{r['end_chapter']}章 达标章将逐章入库(min={args.min_score})")
            continue
        resp = _store(args.host, args.task_id, arc, args.min_score)
        if resp.get("error"):
            print(f"  [ERR] [{arc}] {name}: {resp['error']}")
            n_err += 1
            continue
        tpls = resp.get("templates") or ([resp] if resp.get("template") or resp.get("already") else [])
        n_ok += sum(1 for t in tpls if t.get("template") and not t["template"].get("already"))
        n_already += sum(1 for t in tpls if (t.get("template") or {}).get("already"))
        ids = [((t.get("template") or {}).get("id") or "already") for t in tpls]
        print(f"  [{'OK' if ids else 'ERR'}] [{arc}] {name} → {len(ids)} 个模板 {ids}")
    print(f"\n汇总: 入库 {n_ok} | 已存在 {n_already} | 错误 {n_err}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
