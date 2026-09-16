# -*- coding: utf-8 -*-
"""Level-0 人类偏好集加载 + Kendall τ 校验（PromptOpt）。

Phase 0：算手调评分器 vs 人类全序的 τ 基线（本模块 main）。
Phase 2：同一入口当 Level-1 训练主指标（τ = Kendall(scorer_rank, human_rank)）。

口径说明
--------
- 人类全序来自 annotate_server 落盘的 human_pref.jsonl（同场景最新一条，
  只取四卡标记 + 全序排名齐全的「完成」记录）；卡与来源的映射用记录里
  服务器落盘的 slot_source（权威），不重算 md5。
- 评分器排名按 scorer_adapter.score() 的 composite 降序；原卡按「生成=原文」
  自比打分，char/plot/fact 恒高——这是生产口径的固有性质（评分器永远以原文
  为参照），因此同时给两种 τ：
  * tau4：4 卡全序（含原卡，受自比偏置影响，反映真实生产排序行为）
  * tau3：仅 3 张 AI 变体卡（Level-2 训练时 reward 真实面对的情形：
    评分器只在 AI 候选之间排序，原文不是候选）
- Kendall τ 用纯 Python 实现（全序无并列），不引 scipy。

运行：cd prompt-harness && python -X utf8 -m prompt_harness.promptopt.human_preference
产出：harness_runs/promptopt/phase0/tau_baseline.json + tau_baseline.md
"""
from __future__ import annotations

import asyncio
import json
import sys
import time
from pathlib import Path
from statistics import mean

from .scorer_adapter import score_many

PKG_DIR = Path(__file__).resolve().parent            # .../prompt_harness/promptopt
HARNESS_ROOT = PKG_DIR.parent.parent                  # .../prompt-harness
DATA = HARNESS_ROOT / "prompt_harness" / "promptopt" / "data"
OUTDIR = HARNESS_ROOT / "harness_runs" / "promptopt" / "phase0"
VAL_SCENES = DATA / "val_scenes.json"
VAL_VARIANTS = DATA / "val_variants.jsonl"
HUMAN_PREF = DATA / "human_pref.jsonl"
EXCLUDED_SCENES = {"val_030"}  # 作者单章/创作谈，非场景正文（2026-09-02 用户裁定剔除）


# ---------------- 偏好集加载 ----------------

def load_scenes() -> dict[str, dict]:
    payload = json.loads(VAL_SCENES.read_text(encoding="utf-8"))
    return {s["id"]: s for s in payload["scenes"] if s["split"] == "val"}


def load_variants() -> dict[str, dict[str, str]]:
    """scene_id -> {profile: text}，跳过 error 行。"""
    out: dict[str, dict[str, str]] = {}
    for line in VAL_VARIANTS.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            r = json.loads(line)
        except Exception:
            continue
        if r.get("error") or not r.get("text"):
            continue
        out.setdefault(r["scene_id"], {})[r["profile"]] = r["text"]
    return out


def load_preferences() -> dict[str, dict]:
    """scene_id -> 最新一条「完成」记录（四卡标记 + 全序齐全），剔除 EXCLUDED_SCENES。"""
    scenes = load_scenes()
    slot_sets = {sid: set() for sid in scenes}  # 占位，完成判定需要卡数=4
    latest: dict[str, dict] = {}
    for line in HUMAN_PREF.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            r = json.loads(line)
        except Exception:
            continue
        latest[r["scene_id"]] = r  # 后行覆盖旧行（与 annotate_server upsert 口径一致）

    out: dict[str, dict] = {}
    for sid, r in latest.items():
        if sid in EXCLUDED_SCENES or sid not in scenes:
            continue
        marks = r.get("marks", {})
        ranking = r.get("ranking", [])
        if len(marks) != 4 or sorted(ranking) != ["A", "B", "C", "D"]:
            continue
        out[sid] = r
    return out


def scene_texts(sid: str, rec: dict, scenes: dict, variants: dict) -> dict[str, str]:
    """slot -> 正文。原卡取场景 reference；变体取 val_variants。"""
    texts: dict[str, str] = {}
    src_map = rec["slot_source"]
    for slot, src in src_map.items():
        if src == "original":
            texts[slot] = scenes[sid]["reference"]
        else:
            texts[slot] = variants[sid][src]
    return texts


# ---------------- Kendall τ ----------------

def kendall_tau(human_rank: list[str], other_rank: list[str]) -> float:
    """两个全序（好→差）的 Kendall τ。并列不处理（标注与 composite 均视为全序）。"""
    pos = {s: i for i, s in enumerate(other_rank)}
    n = len(human_rank)
    if n < 2:
        return 0.0
    concordant = discordant = 0
    for i in range(n):
        for j in range(i + 1, n):
            a, b = human_rank[i], human_rank[j]
            if pos[a] < pos[b]:
                concordant += 1
            else:
                discordant += 1
    return (concordant - discordant) / (n * (n - 1) / 2)


def rank_by_composite(composites: dict[str, float]) -> list[str]:
    return sorted(composites, key=lambda s: -composites[s])


# ---------------- 评估主流程 ----------------

async def evaluate(run_flavor_review: bool = True, concurrency: int = 4) -> dict:
    scenes = load_scenes()
    variants = load_variants()
    prefs = load_preferences()
    if not prefs:
        raise RuntimeError("没有「完成」状态的人类标注，先跑 annotate_server 标注")

    items: list[dict] = []
    for sid, rec in sorted(prefs.items()):
        texts = scene_texts(sid, rec, scenes, variants)
        for slot, text in texts.items():
            items.append({
                "id": f"{sid}:{slot}",
                "scene_id": sid,
                "slot": slot,
                "source": rec["slot_source"][slot],
                "generated": text,
                "reference": scenes[sid]["reference"],
                "target_len": scenes[sid].get("target_len", 0),
            })

    t0 = time.time()
    scored = await score_many(items, run_flavor_review=run_flavor_review, concurrency=concurrency)
    elapsed = time.time() - t0
    by_id = {s["id"]: s for s in scored}

    per_scene = []
    tau4s, tau3s = [], []
    for sid, rec in sorted(prefs.items()):
        comps: dict[str, float] = {}
        dims: dict[str, dict] = {}
        errs: list[str] = []
        for slot in ("A", "B", "C", "D"):
            s = by_id[f"{sid}:{slot}"]
            comps[slot] = s["composite"]
            dims[slot] = {k: s[k] for k in ("fact", "char", "plot", "syn", "flavor", "completion")}
            if s["errors"]:
                errs.extend(f"{slot}:{e}" for e in s["errors"])
        human_rank = rec["ranking"]
        scorer_rank = rank_by_composite(comps)
        tau4 = kendall_tau(human_rank, scorer_rank)

        variant_slots = [sl for sl in ("A", "B", "C", "D") if rec["slot_source"][sl] != "original"]
        h3 = [sl for sl in human_rank if sl in variant_slots]
        s3 = [sl for sl in scorer_rank if sl in variant_slots]
        tau3 = kendall_tau(h3, s3)

        orig_slot = next(sl for sl, src in rec["slot_source"].items() if src == "original")
        per_scene.append({
            "scene_id": sid,
            "category": scenes[sid].get("category", ""),
            "chapter": scenes[sid].get("chapter", ""),
            "human_rank": human_rank,
            "scorer_rank": scorer_rank,
            "composites": comps,
            "dims": dims,
            "original_slot": orig_slot,
            "human_orig_rank": human_rank.index(orig_slot) + 1,
            "scorer_orig_rank": scorer_rank.index(orig_slot) + 1,
            "tau4": round(tau4, 4),
            "tau3": round(tau3, 4),
            "errors": sorted(set(errs)),
        })
        tau4s.append(tau4)
        tau3s.append(tau3)

    # 汇总：按 source 的平均 composite（原卡受自比偏置，单列）
    by_source: dict[str, list[float]] = {}
    for it, s in zip(items, scored):
        by_source.setdefault(it["source"], []).append(s["composite"])
    src_mean = {k: round(mean(v), 4) for k, v in by_source.items()}

    # bootstrap CI（Phase 4）：场景级重采样（B=2000，95% percentile）。
    # 场景数 n 小（12~30），τ 点估计抖动大——触发重拟合判定（τ3 是否仍 ~0.50）
    # 必须带区间才算数。纯离线计算，从 per-scene τ 重采样，零额外成本。
    def _bootstrap_ci(vals: list[float], b: int = 2000, seed: int = 20260905) -> dict:
        if len(vals) < 3:
            return {"lo": None, "hi": None, "b": 0}
        rng = random.Random(seed)
        stats = []
        n = len(vals)
        for _ in range(b):
            sample = [vals[rng.randrange(n)] for _ in range(n)]
            stats.append(mean(sample))
        stats.sort()
        return {"lo": round(stats[int(0.025 * b)], 4),
                "hi": round(stats[int(0.975 * b)], 4), "b": b}

    return {
        "n_scenes": len(prefs),
        "n_items": len(items),
        "run_flavor_review": run_flavor_review,
        "elapsed_sec": round(elapsed, 1),
        "tau4_mean": round(mean(tau4s), 4),
        "tau3_mean": round(mean(tau3s), 4),
        "tau3_ci95": _bootstrap_ci(tau3s),
        "tau4_ci95": _bootstrap_ci(tau4s),
        "tau4_per_scene": {p["scene_id"]: p["tau4"] for p in per_scene},
        "tau3_per_scene": {p["scene_id"]: p["tau3"] for p in per_scene},
        "composite_mean_by_source": src_mean,
        "human_orig_rank_mean": round(mean(p["human_orig_rank"] for p in per_scene), 2),
        "scorer_orig_rank_mean": round(mean(p["scorer_orig_rank"] for p in per_scene), 2),
        "scorer_orig_top1": sum(1 for p in per_scene if p["scorer_orig_rank"] == 1),
        "human_orig_top1": sum(1 for p in per_scene if p["human_orig_rank"] == 1),
        "scenes_with_errors": [p["scene_id"] for p in per_scene if p["errors"]],
        "per_scene": per_scene,
    }


def render_markdown(res: dict) -> str:
    lines = [
        "# Kendall τ 基线：手调评分器 vs 人类全序（Phase 0）",
        "",
        f"- 样本：{res['n_scenes']} 个完成标注场景 × 4 卡 = {res['n_items']} 段"
        f"（val_030 已剔除；run_flavor_review={res['run_flavor_review']}；耗时 {res['elapsed_sec']}s）",
        f"- **tau4（4 卡全序，含原卡）= {res['tau4_mean']}**",
        f"- **tau3（仅 3 张 AI 变体卡，Level-2 reward 真实口径）= {res['tau3_mean']}**",
        f"- 原卡平均排名：人类 {res['human_orig_rank_mean']} / 评分器 {res['scorer_orig_rank_mean']}"
        f"（人类把原卡排第 1：{res['human_orig_top1']}/{res['n_scenes']}；评分器：{res['scorer_orig_top1']}/{res['n_scenes']}）",
        f"- composite 均值按来源：{json.dumps(res['composite_mean_by_source'], ensure_ascii=False)}",
        f"- 评分维度报错的场景：{','.join(res['scenes_with_errors']) or '无'}",
        "",
        "## 逐题明细",
        "",
        "| scene | 人类排序(好→差) | 评分器排序 | τ4 | τ3 | 原卡名次(人/器) |",
        "|---|---|---|---|---|---|",
    ]
    for p in res["per_scene"]:
        lines.append(
            f"| {p['scene_id']} ({p['category']}) | {'>'.join(p['human_rank'])} "
            f"| {'>'.join(p['scorer_rank'])} | {p['tau4']} | {p['tau3']} "
            f"| {p['human_orig_rank']}/{p['scorer_orig_rank']} |"
        )
    lines += [
        "",
        "## 口径备注",
        "",
        "- 原卡按「生成=原文」自比打分，char/plot/fact 恒高 → tau4 天然偏向原卡；"
        "tau3 只在 AI 变体间比较，是 Phase 2/3 训练 reward 的真实口径。",
        "- 人类排序来自 human_pref.jsonl（四卡标记 + 全序齐全的记录）；卡→来源映射用落盘 slot_source。",
        "- Phase 2 以本文件口径为 τ 主指标：edit 接受条件 = tau3（辅 tau4）严格上涨且 canary 违规不增。",
    ]
    return "\n".join(lines)


async def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    res = await evaluate(run_flavor_review=True, concurrency=4)
    OUTDIR.mkdir(parents=True, exist_ok=True)
    (OUTDIR / "tau_baseline.json").write_text(
        json.dumps(res, ensure_ascii=False, indent=2), encoding="utf-8")
    (OUTDIR / "tau_baseline.md").write_text(render_markdown(res), encoding="utf-8")
    print(f"[tau] scenes={res['n_scenes']} tau4={res['tau4_mean']} tau3={res['tau3_mean']} "
          f"orig_rank human={res['human_orig_rank_mean']} scorer={res['scorer_orig_rank_mean']}")
    if res["scenes_with_errors"]:
        print(f"[tau] ⚠ 维度报错场景: {res['scenes_with_errors']}")
    print(f"[tau] -> {OUTDIR / 'tau_baseline.md'}")


if __name__ == "__main__":
    asyncio.run(main())
