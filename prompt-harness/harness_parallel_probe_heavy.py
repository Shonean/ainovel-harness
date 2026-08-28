"""重载并发探针：用真实章节文本模拟榨干级 LLM 调用（build_ladder 的输入规模），
阶梯加压测当前模型（qwen3.5-flash / thinking disabled）的并发上限与 429 触发点。

与轻探针（harness_parallel_probe.py 小 prompt）的区别：这里是整章文本（~4-6k token）
+ 生成长 max_tokens，逼近榨干真实负载，测得的上限更可信。

用法：python harness_parallel_probe_heavy.py
产出：harness_runs/parallel_probe/heavy_report.json
"""
import asyncio
import json
import time
from pathlib import Path

from prompt_harness.corpus_loader import parse_chapters, _read_file_text, _resolve_corpus_dir
from prompt_harness.llm_client import chat_completion

HARNESS_DIR = Path(__file__).parent / "harness_runs" / "parallel_probe"
HARNESS_DIR.mkdir(parents=True, exist_ok=True)

LEVELS = [24, 32, 40, 48, 56, 64]
CALLS_PER_LEVEL = 6


def _load_chapter_prompt() -> str:
    """取一真实章节正文作为 prompt（模拟 build_ladder 输入规模）。"""
    base = _resolve_corpus_dir()
    r = parse_chapters("修仙/书A/书A_1-500章.txt")
    chs = r.get("chapters") or []
    txt = _read_file_text(base / "修仙/书A/书A_1-500章.txt")
    seg = txt[chs[0]["start_pos"]:chs[0]["end_pos"]].strip()
    return seg[:3000]  # ~4-6k token


async def _call(prompt: str, i: int) -> dict:
    t0 = time.perf_counter()
    r = await chat_completion(
        system="你是网络小说场景分析助手。请精炼概括本章的情节骨架。",
        user=prompt, max_tokens=800, call_type="parallel_probe_heavy")
    return {"i": i, "latency_ms": round((time.perf_counter() - t0) * 1000),
            "error": r.get("error"), "ok": bool(r.get("content"))}


async def _run_level(level: int, prompt: str) -> dict:
    sem = asyncio.Semaphore(level)

    async def _wrapped(i: int):
        async with sem:
            return await _call(prompt, i)

    t0 = time.perf_counter()
    results = await asyncio.gather(*[_wrapped(i) for i in range(CALLS_PER_LEVEL)])
    wall = time.perf_counter() - t0
    lat = [r["latency_ms"] for r in results]
    err429 = [r for r in results if r["error"] and "429" in r["error"]]
    err5xx = [r for r in results if r["error"] and ("HTTP 5" in r["error"] or "timed out" in r["error"])]
    return {
        "level": level,
        "n": len(results),
        "wall_ms": round(wall * 1000),
        "ok": sum(1 for r in results if r["ok"]),
        "err": sum(1 for r in results if r["error"]),
        "err429": len(err429),
        "err5xx": len(err5xx),
        "lat_avg_ms": round(sum(lat) / len(lat)) if lat else 0,
        "lat_max_ms": max(lat) if lat else 0,
    }


async def main():
    prompt = _load_chapter_prompt()
    print(f"prompt 规模: {len(prompt)} 字符 (~{len(prompt)//1.5:.0f} token)")
    report = {"levels": [], "note": ""}
    for level in LEVELS:
        row = await _run_level(level, prompt)
        report["levels"].append(row)
        stop = row["err429"] or row["err5xx"] or (row["err"] / row["n"] > 0.3)
        mark = " ★429" if row["err429"] else (" ★5xx" if row["err5xx"] else (" ★高错误" if stop else ""))
        print(f"并发{level:>2}: 成功{row['ok']}/{row['n']} 429:{row['err429']} 5xx:{row['err5xx']} "
              f"均延迟{row['lat_avg_ms']}ms 最大{row['lat_max_ms']}ms{mark}")
        if stop:
            report["note"] = f"在并发 {level} 触发上限，停止升档"
            break
    HARNESS_DIR.joinpath("heavy_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n报告: {HARNESS_DIR / 'heavy_report.json'} | {report['note']}")


if __name__ == "__main__":
    asyncio.run(main())
