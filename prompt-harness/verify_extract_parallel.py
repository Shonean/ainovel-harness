"""验证 extract_from_doc 并行化（2026-08-15）：
- 并发真实生效（重叠调用，墙钟显著小于串行）
- 结果按输入顺序保真（idx/章号/分数/skeleton 不串位）
- report 弧条目结构完整、qualified 逻辑正确

mock build_ladder/verify_ladder（不调 LLM）；style/role 传值跳过自动提取。
运行：python verify_extract_parallel.py（产出 harness_runs/extract_parallel/）
"""
import asyncio
import json
import sys
import time
from pathlib import Path
from unittest.mock import patch

from prompt_harness.plot_library import extract_from_doc

HARNESS = Path(__file__).parent / "harness_runs" / "extract_parallel"
HARNESS.mkdir(parents=True, exist_ok=True)

N = 6
LADDER = {"l1_minimal": "L1极简", "l2_arc": "L2弧线", "l3_chapter": {"core": "L3核心"},
          "l4_scenes": [{"scene": "场景1", "action": ["动作"]}]}
VRES = {"score": 0.8, "ok": True, "s_char": 0.7, "turn_fidelity": 0.6,
        "ai_flavor": 0.1, "len_ratio": 1.0, "text": "重建正文"}


async def main() -> int:
    in_flight = 0
    max_in_flight = 0

    async def fake_build(*_a, **_k):
        nonlocal in_flight, max_in_flight
        in_flight += 1
        max_in_flight = max(max_in_flight, in_flight)
        await asyncio.sleep(0.15)
        in_flight -= 1
        return dict(LADDER)

    async def fake_verify(*_a, **_k):
        nonlocal in_flight, max_in_flight
        in_flight += 1
        max_in_flight = max(max_in_flight, in_flight)
        await asyncio.sleep(0.15)
        in_flight -= 1
        return dict(VRES)

    async def fake_beat_cov(*_a, **_k):
        return {"coverage": 0.9, "rebuildable": True, "missing": []}

    chapters = [f"第{i}章 正文文本" for i in range(1, N + 1)]
    with patch("prompt_harness.ladder.build_ladder", side_effect=fake_build), \
         patch("prompt_harness.ladder.verify_ladder", side_effect=fake_verify), \
         patch("prompt_harness.plot_library._judge_beat_coverage", side_effect=fake_beat_cov):
        t0 = time.perf_counter()
        result = await extract_from_doc(
            chapters, style="测试风格", role_setting="测试角色", budget=80, min_score=0.65)
        wall = time.perf_counter() - t0

    fails: list[str] = []
    report = result.get("report") or []
    if len(report) != N:
        fails.append(f"report 弧数={len(report)} 期望 {N}")
    for i, r in enumerate(report, 1):
        if r["arc"] != i:
            fails.append(f"弧 {r['arc']} 乱序（期望 {i}）")
        if not r.get("qualified"):
            fails.append(f"弧 {i} 应合格")
        chs = r["chapters"]
        if len(chs) != 1:
            fails.append(f"弧 {i} 章数={len(chs)} 期望 1")
            continue
        ch = chs[0]
        if ch["chapter"] != i or ch["chapter_num"] != i:
            fails.append(f"弧 {i} 章号错位 {ch['chapter']}/{ch['chapter_num']}")
        if ch["score"] != 0.8 or (ch["scores"] or {}).get("score") != 0.8:
            fails.append(f"弧 {i} 分数错位")
        if not ch["qualified"]:
            fails.append(f"弧 {i} 章未合格")
        if (ch["skeleton"] or {}).get("l1") != "L1极简":
            fails.append(f"弧 {i} 骨架 l1 缺失")
        if not ch["prose"]:
            fails.append(f"弧 {i} 正文为空")
    if max_in_flight < 2:
        fails.append(f"无并发（最大并发 {max_in_flight}）——并行未生效")
    serial_est = N * 0.30
    if wall > serial_est * 0.85:
        fails.append(f"耗时 {wall:.2f}s 接近串行（串行估计 {serial_est:.2f}s）——未提速")

    summary = {"n": N, "max_concurrent": max_in_flight, "wall_s": round(wall, 2),
               "serial_est_s": serial_est, "qualified": result.get("qualified"),
               "fails": fails, "ok": not fails}
    HARNESS.joinpath("verify_result.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(f"产出: {HARNESS / 'verify_result.json'}")
    return 0 if not fails else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
