"""并行 / 429 上限探针：测当前生效模型（api_library current_text_id）在并发 N 下
的吞吐、延迟与 429 触发点。走真实 chat_completion（qwen3.5-flash / thinking disabled）。

用法：python harness_parallel_probe.py
产出：harness_runs/parallel_probe/probe_report.json

说明：探针与正在跑的榨干共用同一 API key/端点，会引入额外负载——
这正是要测的。若榨干被限流，其自身有重试退避兜底。
"""
import asyncio
import json
import time
from pathlib import Path

from prompt_harness.llm_client import chat_completion

HARNESS_DIR = Path(__file__).parent / "harness_runs" / "parallel_probe"
HARNESS_DIR.mkdir(parents=True, exist_ok=True)

LEVELS = [1, 2, 4, 8, 12, 16]
CALLS_PER_LEVEL = 12
TINY_PROMPT = "只回复一个字：好。不要输出其它内容。"


async def _call(i: int) -> dict:
    t0 = time.perf_counter()
    r = await chat_completion(user=TINY_PROMPT, max_tokens=16, call_type="parallel_probe")
    return {"i": i, "latency_ms": round((time.perf_counter() - t0) * 1000),
            "error": r.get("error"), "ok": bool(r.get("content"))}


async def _run_level(level: int) -> dict:
    sem = asyncio.Semaphore(level)
    results: list[dict] = []

    async def _wrapped(i: int):
        async with sem:
            return await _call(i)

    t0 = time.perf_counter()
    results = await asyncio.gather(*[_wrapped(i) for i in range(CALLS_PER_LEVEL)])
    wall = time.perf_counter() - t0

    lat = [r["latency_ms"] for r in results]
    errs = [r for r in results if r["error"]]
    err429 = [r for r in results if r["error"] and "429" in r["error"]]
    return {
        "level": level,
        "n": len(results),
        "wall_ms": round(wall * 1000),
        "ok": sum(1 for r in results if r["ok"]),
        "err": len(errs),
        "err429": len(err429),
        "lat_avg_ms": round(sum(lat) / len(lat)) if lat else 0,
        "lat_max_ms": max(lat) if lat else 0,
        "calls_per_sec": round(len(results) / wall, 2) if wall else 0,
    }


async def main():
    report = {"levels": [], "model": "current(api_library)", "note": ""}
    for level in LEVELS:
        row = await _run_level(level)
        report["levels"].append(row)
        mark = " ★429触发" if row["err429"] else (" ★错误率高" if row["err"] / row["n"] > 0.3 else "")
        print(f"并发{level:>2}: {row['n']}调用 墙钟{row['wall_ms']}ms "
              f"成功{row['ok']} 错误{row['err']}(429:{row['err429']}) "
              f"均延迟{row['lat_avg_ms']}ms 吞吐{row['calls_per_sec']}/s{mark}")
        # 触发即停：429 或错误率 >30% 就不再升档，避免持续轰炸 API
        if row["err429"] or (row["err"] / row["n"] > 0.3):
            report["note"] = f"在并发 {level} 处触发上限，停止升档"
            break
    HARNESS_DIR.joinpath("probe_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n报告: {HARNESS_DIR / 'probe_report.json'} | {report['note']}")


if __name__ == "__main__":
    asyncio.run(main())
