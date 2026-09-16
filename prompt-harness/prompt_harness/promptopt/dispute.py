# -*- coding: utf-8 -*-
"""三评委分歧检测（Phase 4 §八-1，反 Goodhart 护栏）。

评委A 启发式 = scorer_adapter.score() composite 排名（生产口径）；
评委B LLM-judge = chat_json 对同场景 3 张 AI 变体卡做质量全序（乱序防位置偏置）；
评委C 人工 = human_pref.jsonl 已完成标注（如有）。

分歧大的场景进 disputed.jsonl，成为下一次 annotate_server 人工标注的优先素材
——这正是「disputed 机制提前建空转」的正主：用两评委独立跑，先找 τ≤0 的争议题。

运行：cd prompt-harness && python -X utf8 -m prompt_harness.promptopt.dispute [--limit 12]
产出：harness_runs/promptopt/phase4/dispute_report.{json,md}
      + prompt_harness/promptopt/data/disputed.jsonl（追加式，同场景 pending 去重）
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import sys
import time
from datetime import datetime
from pathlib import Path

from .human_preference import kendall_tau, load_preferences
from .scorer_adapter import score_many
from ..llm_client import chat_json

PKG_DIR = Path(__file__).resolve().parent
HARNESS_ROOT = PKG_DIR.parent.parent
DATA = PKG_DIR / "data"
OUT = HARNESS_ROOT / "harness_runs" / "promptopt" / "phase4"
VAL_VARIANTS = DATA / "val_variants.jsonl"
DISPUTED = DATA / "disputed.jsonl"

_JUDGE_SYSTEM = "你是网文写作质量评审。根据原文判断候选段落的综合质量（忠实度、人物刻画、情节推进、文风）。只输出 JSON。"


def load_variant_pool() -> dict[str, dict[str, str]]:
    """scene_id -> {profile: text}（跳过 error 行），与 human_preference.load_variants 同口径。"""
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


def slot_labels(scene_id: str, profiles: list[str]) -> dict[str, str]:
    """确定性乱序：profile -> 甲/乙/丙（防 LLM 位置偏置，md5 同 annotate_server 风格）。"""
    h = hashlib.md5(scene_id.encode("utf-8")).hexdigest()
    order = sorted(range(len(profiles)), key=lambda i: h[i])
    labels = ["甲", "乙", "丙"][: len(profiles)]
    return {profiles[i]: labels[k] for k, i in enumerate(order)}


async def judge_rank(scene: dict, texts: dict[str, str], labels: dict[str, str]) -> dict | None:
    """评委B：LLM 对 甲/乙/丙 全序。返回 {"ranking": [...], "reason": str} 或 None。"""
    parts = [f"【原文】\n{scene['reference']}", "【候选】"]
    for profile, text in texts.items():
        parts.append(f"\n〖{labels[profile]}〗\n{text}")
    parts.append(
        "\n按综合质量给 甲乙丙 排序（好→差）。只输出 JSON："
        '{"ranking": ["甲","乙","丙"], "reason": "不超过60字"}'
    )
    try:
        res = await chat_json(
            system=_JUDGE_SYSTEM, user="\n".join(parts),
            call_type="promptopt_judge", temperature=0.1, max_tokens=300,
        )
        data = res.get("data")
        if not isinstance(data, dict):
            return None
        ranking = data.get("ranking")
        if not isinstance(ranking, list) or sorted(ranking) != sorted(labels.values()):
            return None
        return {"ranking": [str(x) for x in ranking], "reason": str(data.get("reason") or "")[:120]}
    except Exception:
        return None


def _label_to_profile(labels: dict[str, str], ranked_labels: list[str]) -> list[str]:
    rev = {v: k for k, v in labels.items()}
    return [rev[x] for x in ranked_labels if x in rev]


def _pending_scene_ids() -> set[str]:
    if not DISPUTED.exists():
        return set()
    out: set[str] = set()
    for line in DISPUTED.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            r = json.loads(line)
        except Exception:
            continue
        if r.get("status") == "pending":
            out.add(r.get("scene_id", ""))
    return out


async def run(limit: int = 12, run_flavor: bool = True, concurrency: int = 4) -> dict:
    pool = load_variant_pool()
    prefs = load_preferences()  # 评委C 素材（可能为空）
    scene_ids = sorted(pool.keys())[:limit]
    if not scene_ids:
        raise RuntimeError("val_variants.jsonl 无可用变体")

    scenes = {s["id"]: s for s in json.loads(
        (DATA / "val_scenes.json").read_text(encoding="utf-8"))["scenes"]}

    # 评委A：批量打分
    items = []
    for sid in scene_ids:
        for profile, text in pool[sid].items():
            items.append({"id": f"{sid}:{profile}", "generated": text,
                          "reference": scenes[sid]["reference"],
                          "target_len": scenes[sid].get("target_len", 0)})
    t0 = time.time()
    scored = await score_many(items, run_flavor_review=run_flavor, concurrency=concurrency)
    comp = {s["id"]: s["composite"] for s in scored}

    per_scene, disputed = [], []
    for sid in scene_ids:
        profiles = list(pool[sid].keys())
        labels = slot_labels(sid, profiles)
        scorer_rank = sorted(profiles, key=lambda p: -comp[f"{sid}:{p}"])

        jr = await judge_rank(scenes[sid], pool[sid], labels)
        judge_rank_profiles = _label_to_profile(labels, jr["ranking"]) if jr else None

        human_rank = None
        if sid in prefs:
            src_map = prefs[sid]["slot_source"]
            human_rank = [src_map[sl] for sl in prefs[sid]["ranking"] if src_map.get(sl) in pool[sid]]

        taus = {}
        if judge_rank_profiles:
            taus["A_vs_B"] = round(kendall_tau(scorer_rank, judge_rank_profiles), 4)
        if human_rank:
            taus["A_vs_C"] = round(kendall_tau(scorer_rank, human_rank), 4)
            if judge_rank_profiles:
                taus["B_vs_C"] = round(kendall_tau(judge_rank_profiles, human_rank), 4)

        flags = []
        if any(v <= 0 for v in taus.values()):
            flags.append("tau_le_0")
        if judge_rank_profiles and scorer_rank[0] != judge_rank_profiles[0]:
            gap = round(comp[f"{sid}:{scorer_rank[0]}"] - comp[f"{sid}:{judge_rank_profiles[0]}"], 4)
            flags.append("top1_mismatch")
        else:
            gap = 0.0

        row = {
            "scene_id": sid, "category": scenes[sid].get("category", ""),
            "scorer_rank": scorer_rank,
            "scorer_composite": {p: comp[f"{sid}:{p}"] for p in profiles},
            "judge_rank": judge_rank_profiles, "judge_reason": (jr or {}).get("reason", ""),
            "human_rank": human_rank, "taus": taus, "top1_gap": gap,
            "flags": flags, "disputed": bool(flags),
        }
        per_scene.append(row)
        if flags:
            disputed.append(row)

    # 落 disputed.jsonl（同场景已有 pending 则跳过，避免重复排期）
    already = _pending_scene_ids()
    ts = datetime.now().isoformat(timespec="seconds")
    new_rows = []
    for r in disputed:
        if r["scene_id"] in already:
            continue
        new_rows.append({
            "ts": ts, "scene_id": r["scene_id"], "category": r["category"],
            "flags": r["flags"], "taus": r["taus"],
            "scorer_rank": r["scorer_rank"], "judge_rank": r["judge_rank"],
            "status": "pending",
        })
    if new_rows:
        with open(DISPUTED, "a", encoding="utf-8") as f:
            for r in new_rows:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")

    res = {
        "ts": ts, "n_scenes": len(scene_ids), "elapsed_sec": round(time.time() - t0, 1),
        "tau_ab_mean": round(sum(r["taus"].get("A_vs_B", 0) for r in per_scene
                                 if "A_vs_B" in r["taus"]) / max(1, sum(1 for r in per_scene if "A_vs_B" in r["taus"])), 4),
        "n_disputed": len(disputed),
        "new_pending": [r["scene_id"] for r in new_rows],
        "disputed_file": str(DISPUTED),
        "per_scene": per_scene,
    }
    return res


def render_markdown(res: dict) -> str:
    lines = [
        "# 三评委分歧检测（Phase 4）",
        "",
        f"- 样本：{res['n_scenes']} 场景 × 3 AI 变体；耗时 {res['elapsed_sec']}s",
        f"- **τ(A,B) 均值 = {res['tau_ab_mean']}**（启发式 vs LLM-judge）",
        f"- 争议场景：**{res['n_disputed']}**；本轮新入队 pending：{', '.join(res['new_pending']) or '无'}",
        f"- 队列：`{res['disputed_file']}`（status=pending 去重）",
        "",
        "| scene | 类目 | 评分器序 | LLM-judge 序 | 人工序 | τ(A,B) | τ(A,C) | flags |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for r in res["per_scene"]:
        lines.append(
            f"| {r['scene_id']} | {r['category']} | {'>'.join(r['scorer_rank'])} "
            f"| {'>'.join(r['judge_rank']) if r['judge_rank'] else '—'} "
            f"| {'>'.join(r['human_rank']) if r['human_rank'] else '—'} "
            f"| {r['taus'].get('A_vs_B', '—')} | {r['taus'].get('A_vs_C', '—')} "
            f"| {','.join(r['flags']) or '—'} |"
        )
    return "\n".join(lines)


async def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    limit = 12
    if "--limit" in sys.argv:
        limit = int(sys.argv[sys.argv.index("--limit") + 1])
    res = await run(limit=limit)
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "dispute_report.json").write_text(json.dumps(res, ensure_ascii=False, indent=2), encoding="utf-8")
    (OUT / "dispute_report.md").write_text(render_markdown(res), encoding="utf-8")
    print(f"[dispute] scenes={res['n_scenes']} tau_AB={res['tau_ab_mean']} "
          f"disputed={res['n_disputed']} new_pending={res['new_pending']}")
    print(f"[dispute] -> {OUT / 'dispute_report.md'}")


if __name__ == "__main__":
    asyncio.run(main())
