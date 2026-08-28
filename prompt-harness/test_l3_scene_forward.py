# -*- coding: utf-8 -*-
"""⑤(a) A/B：l4 场景切分前移进 l3 后，C 档（真实 l1+l2+l3 前向重建）应明显涨分。

对照组 = ladder_sufficiency/raw.json 里已有的 C_from_l3 / D_from_l4 旧分数（同章同模型）。
实验组 = 用新 extract_l3_from_scenes 从**同一份真实 l4 场景**重提切分版 l3，
再走生产链 bridge.step_ladder 到 l5 打分。唯一变量是 l3。

用法（prompt-harness/ 下）：
    python test_l3_scene_forward.py --preset "OpenRouter" --no-ai-flavor

产出 harness_runs/l3_scene_forward/{raw.json,report.md}。
"""
import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

# --preset 必须在 import prompt_harness 前生效（config 读 env）
_PRESET = "glm-5.3"
for i, av in enumerate(sys.argv):
    if av == "--preset" and i + 1 < len(sys.argv):
        _PRESET = sys.argv[i + 1]
    elif av.startswith("--preset="):
        _PRESET = av.split("=", 1)[1]

import api_preset_lib as _apl  # noqa: E402
_match = None
for _p in _apl.list_presets():
    if _p["id"] == _PRESET or _PRESET.lower() in _p["name"].lower():
        _match = _apl.apply_preset(_p["id"])
        break
if _match:
    _apl.refresh_env_from_preset(_match)
    print(f"[preset] 使用 API 预设：{_match['name']}（{_match['model']}）")
else:
    print(f"[preset] 未找到预设 {_PRESET!r}，使用环境/服务器默认")

from prompt_harness import bridge  # noqa: E402
from prompt_harness.ladder import extract_l3_from_scenes  # noqa: E402
from prompt_harness.minimal_train import score_generated  # noqa: E402

# 复用消融脚本的加载/打分/状态构造（同章同样本，保证可比）
from verify_ladder_sufficiency import (  # noqa: E402
    BOOKS, build_state, fact_anchors, fact_recall, load_prose_for_book,
    load_samples_for_book, run_to_l5, skeleton_text,
)

OUT_DIR = HERE / "harness_runs" / "l3_scene_forward"

# 跳过 bridge 内部 AI 味清理（与消融 --no-ai-flavor 同条件）
if "--no-ai-flavor" in sys.argv:
    async def _noop_clean(text, *a, **k):
        return {"text": text, "review": None, "retried": False}
    async def _noop_measure(text, *a, **k):
        return {"score": None, "findings": [], "summary": "", "dirty": False}
    bridge._ai_flavor_clean_text = _noop_clean
    bridge._measure_ai_flavor = _noop_measure


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--preset", type=str, default="glm-5.3")
    ap.add_argument("--chapters", type=int, default=1)
    ap.add_argument("--no-ai-flavor", action="store_true")
    args = ap.parse_args()

    OUT_DIR.mkdir(parents=True, exist_ok=True)

    # 对照组旧分数
    prev_raw = {}
    old_path = HERE / "harness_runs" / "ladder_sufficiency" / "raw.json"
    if old_path.exists():
        for r in json.loads(old_path.read_text(encoding="utf-8")):
            prev_raw[(r.get("book"), int(r.get("chapter_num") or 0))] = r

    rows = []
    t0 = time.time()
    for substr, label in BOOKS:
        prose = load_prose_for_book(substr)
        samps = load_samples_for_book(substr, args.chapters)
        print(f"[load] {label}: 样本 {[s['chapter_num'] for s in samps]}", flush=True)
        for sample in samps:
            ch = sample["chapter_num"]
            target = prose.get(ch, "")
            if not target:
                print(f"  [!] {label} ch{ch} 无原文，跳过", flush=True)
                continue
            # 实验组：从真实 l4 重提切分版 l3（唯一变量）
            new_l3 = await extract_l3_from_scenes(sample["l4"])
            n_beats = len(new_l3.get("beats") or [])
            n_scenes = len(sample["l4"] if isinstance(sample["l4"], list) else [sample["l4"]])
            print(f"[{label} ch{ch}] 新 l3：beats {n_beats} 条 vs 场景 {n_scenes} 个",
                  flush=True)

            st = build_state({**sample, "l3": {
                "title": str(new_l3.get("title") or "").strip(),
                "core": str(new_l3.get("core") or "").strip(),
                "beats": [str(b).strip() for b in (new_l3.get("beats") or [])],
            }}, ["l1", "l2", "l3"])
            res = await run_to_l5(st)
            entry = {"ok": res["ok"], "error": res["error"],
                     "steps": [s["to"] for s in res["steps"]],
                     "gen_len": len(res["l5"].strip())}
            if res["l5"]:
                sc = await score_generated(res["l5"], target, skeleton_text(sample),
                                           ai_flavor=not args.no_ai_flavor)
                fr = fact_recall(res["l5"], fact_anchors(sample))
                entry.update({
                    "score": round(float(sc.get("score") or 0), 4),
                    "plot_sim": sc.get("plot_sim"),
                    "fact_recall": fr,
                })

            # 同模型对照组：用磁盘上旧的泛化 beats l3 跑 C 档（唯一变量 = l3 是否场景对齐）
            st_old = build_state(sample, ["l1", "l2", "l3"])
            res_old = await run_to_l5(st_old)
            old_entry = {"ok": res_old["ok"], "error": res_old["error"],
                         "steps": [s["to"] for s in res_old["steps"]],
                         "gen_len": len(res_old["l5"].strip())}
            if res_old["l5"]:
                sc2 = await score_generated(res_old["l5"], target, skeleton_text(sample),
                                            ai_flavor=not args.no_ai_flavor)
                old_entry.update({
                    "score": round(float(sc2.get("score") or 0), 4),
                    "plot_sim": sc2.get("plot_sim"),
                    "fact_recall": fact_recall(res_old["l5"], fact_anchors(sample)),
                })
            old = (prev_raw.get((label, ch)) or {}).get("runs") or {}
            old_c = (old.get("C_from_l3") or {})
            old_d = (old.get("D_from_l4") or {})
            row = {"book": label, "chapter_num": ch,
                   "new_l3": new_l3,
                   "new_C_doubao": entry,
                   "old_C_doubao": old_entry,
                   "old_C_stealth_score": old_c.get("score"), "old_C_stealth_fr": old_c.get("fact_recall"),
                   "old_D_stealth_score": old_d.get("score"), "old_D_stealth_fr": old_d.get("fact_recall")}
            rows.append(row)
            print(f"  -> 新C(doubao)={entry.get('score')} fr={entry.get('fact_recall')} | "
                  f"旧C(doubao)={old_entry.get('score')} fr={old_entry.get('fact_recall')} | "
                  f"旧C(stealth)={row['old_C_stealth_score']}", flush=True)
            (OUT_DIR / "raw.json").write_text(
                json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")

    # report
    def mean(xs):
        xs = [x for x in xs if isinstance(x, (int, float))]
        return round(sum(xs) / len(xs), 4) if xs else None

    lines = ["# ⑤(a) 场景切分前移进 l3 — A/B 报告", "",
             f"样本 {len(rows)} 章 ｜ 耗时 {time.time()-t0:.0f}s ｜ "
             "模型 doubao-evolving ｜ 唯一变量：l3 beats 是否与 l4 场景一一对应", "",
             "## 主对照：同模型新C vs 旧C（干净 A/B）", "",
             "| 书 | 章 | 新C score | 旧C score | Δscore | 新C fr | 旧C fr | Δfr |",
             "|---|---|---|---|---|---|---|---|"]
    for r in rows:
        e, o = r["new_C_doubao"], r["old_C_doubao"]
        ds = (round(e["score"] - o["score"], 4)
              if isinstance(e.get("score"), (int, float)) and isinstance(o.get("score"), (int, float)) else None)
        df = (round(e["fact_recall"] - o["fact_recall"], 4)
              if isinstance(e.get("fact_recall"), (int, float)) and isinstance(o.get("fact_recall"), (int, float)) else None)
        lines.append(f"| {r['book']} | {r['chapter_num']} | {e.get('score','—')} | "
                     f"{o.get('score','—')} | {ds} | "
                     f"{e.get('fact_recall','—')} | {o.get('fact_recall','—')} | {df} |")
    mc = mean([r["new_C_doubao"].get("score") for r in rows])
    oc = mean([r["old_C_doubao"].get("score") for r in rows])
    mfr = mean([r["new_C_doubao"].get("fact_recall") for r in rows])
    ofr = mean([r["old_C_doubao"].get("fact_recall") for r in rows])
    lines += ["",
              f"- **新C 均分 {mc} vs 旧C {oc}（Δ{None if mc is None or oc is None else round(mc-oc,4)}）**",
              f"- fact_recall 均值 新C {mfr} vs 旧C {ofr}",
              "",
              "## 跨模型参考（stealth 旧基线，仅供看趋势）", "",
              "| 书 | 章 | 新C(doubao) | 旧C(stealth) | 旧D(stealth) |",
              "|---|---|---|---|---|"]
    for r in rows:
        lines.append(f"| {r['book']} | {r['chapter_num']} | "
                     f"{r['new_C_doubao'].get('score','—')} | "
                     f"{r['old_C_stealth_score']} | {r['old_D_stealth_score']} |")
    lines += ["",
              "- 判定：同模型 Δ 明显为正 → 切分前移有效；新C 接近/超过旧D → l4 可省略"]
    (OUT_DIR / "report.md").write_text("\n".join(lines), encoding="utf-8")
    print(f"[done] {OUT_DIR / 'report.md'}")


if __name__ == "__main__":
    asyncio.run(main())
