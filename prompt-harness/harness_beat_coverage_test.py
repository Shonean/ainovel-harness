"""节拍覆盖算法验证：用现有榨干结果（l3 节拍）+ 章节原文，
LLM 判「节拍覆盖率」，对比旧的 s_char 散文保真评分——检验新达标方向能否救回差一口气的章。

产出：harness_runs/beat_coverage_test/
运行：python harness_beat_coverage_test.py
"""
import asyncio
import json
import sys
from pathlib import Path

from prompt_harness.corpus_loader import parse_chapters, _read_file_text, _resolve_corpus_dir
from prompt_harness.llm_client import chat_completion

HARNESS = Path(__file__).parent / "harness_runs" / "beat_coverage_test"
HARNESS.mkdir(parents=True, exist_ok=True)
USER_DATA = Path.home() / ".claude" / "ainovel-write" / "prompt-harness"

# (结果task, 章号范围, corpus文件, 书名标签)
CASES = [
    ("9a8148863b35", (45, 50), "修仙/书A/书A_1-500章.txt", "长生路45-49"),
]


def load_sample(task_id: str, lo: int, hi: int, corpus_fp: str):
    d = json.loads((USER_DATA / "plot_extract_results" / f"{task_id}.json").read_text(encoding="utf-8"))
    base = _resolve_corpus_dir()
    txt = _read_file_text(base / corpus_fp)
    pos = parse_chapters(corpus_fp).get("chapters") or []
    out = []
    for r in d.get("report") or []:
        for c in r.get("chapters") or []:
            num = c.get("chapter_num") or 0
            if lo <= num < hi:
                l3 = (c.get("skeleton") or {}).get("l3") or {}
                orig = ""
                for ch in pos:
                    if int(ch.get("chapter_num") or 0) == num:
                        orig = txt[ch["start_pos"]:ch["end_pos"]].strip()
                        break
                out.append({"num": num, "score": c.get("score"), "l3": l3, "orig": orig[:2500]})
    return out


async def judge(s: dict) -> tuple:
    beats = "；".join(str(b) for b in (s["l3"].get("beats") or []))
    user = (
        f"【章节原文】\n{s['orig']}\n\n【提取的节拍】\n{beats}\n\n"
        "判断这些节拍是否覆盖了章节的关键事件/冲突/转折（事件对即可，不需逐字）。"
        '严格输出JSON: {"coverage":0-1,"rebuildable":true/false,"missing":[]}'
    )
    r = await chat_completion(
        system="你是网络小说剧情节拍覆盖率评估专家。", user=user,
        max_tokens=400, call_type="probe_beatcov")
    content = (r.get("content") or "").strip()
    try:
        c = content
        if c.startswith("```"):
            c = c.split("\n", 1)[1].rsplit("```", 1)[0]
        o = json.loads(c)
        return o.get("coverage"), o.get("rebuildable"), (o.get("missing") or [])
    except Exception as e:
        return None, None, [f"解析失败:{str(e)[:40]}|原始:{content[:80]}"]


async def main():
    rows = []
    for task_id, (lo, hi), corpus_fp, label in CASES:
        for s in load_sample(task_id, lo, hi, corpus_fp):
            cov, rb, miss = await judge(s)
            old_q = (s["score"] or 0) >= 0.65
            new_q = bool(rb and cov is not None and cov >= 0.6)
            rows.append({"num": s["num"], "old_score": s["score"], "coverage": cov,
                         "rebuildable": rb, "old_qualified": old_q,
                         "new_qualified": new_q, "missing": miss})
            print(f"章{s['num']}: 旧分{s['score']} 覆盖{cov} 可重建{rb} "
                  f"旧{'✓' if old_q else '✗'}→新{'✓' if new_q else '✗'} | {str(miss)[:50]}")
    HARNESS.joinpath("beat_cov_result.json").write_text(
        json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n产出: {HARNESS / 'beat_cov_result.json'}")


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
