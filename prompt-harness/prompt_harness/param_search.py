"""生成参数帕累托搜索。

LHS 采样 N 组 (temperature, top_p, max_tokens, presence_penalty)，
每组正向生成 → 提取 V' → 双目标评估 → 帕累托前沿 → 三档推荐。

双目标：
    f1 = V_cosine  （贴近目标风格，越大越好）
    f2 = diversity  （多样性/创造力，lexical_richness + 句长方差，越大越好）

帕累托前沿上选三档：
    保守档：f1 最高（最贴近目标）
    平衡档：折中（归一化后 f1+f2 最大）
    创新档：f2 最高（最有创造力）
"""
from __future__ import annotations

import math
import random
from typing import Any


# ── LHS 采样 ──────────────────────────────────────────────

def _lhs_sample(n_samples: int, dim_ranges: list[tuple[float, float]], *, seed: int | None = None) -> list[list[float]]:
    """拉丁超立方采样。

    对每个维度，把 [0,1] 分成 n_samples 个区间，每个区间随机取一点，
    然后随机排列各维度的索引，保证每个样本在每个维度上都不重复区间。
    """
    rng = random.Random(seed)
    dims = len(dim_ranges)

    # 每个维度：随机排列的索引
    perms = []
    for d in range(dims):
        perm = list(range(n_samples))
        rng.shuffle(perm)
        perms.append(perm)

    samples = []
    for i in range(n_samples):
        point = []
        for d in range(dims):
            # 第 d 维，第 i 个样本落在第 perms[d][i] 个区间
            bucket = perms[d][i]
            low = bucket / n_samples
            high = (bucket + 1) / n_samples
            # 在区间内随机取点
            u = rng.uniform(low, high)
            # 映射到实际范围
            lo, hi = dim_ranges[d]
            point.append(lo + u * (hi - lo))
        samples.append(point)
    return samples


def _pareto_front(points: list[dict[str, Any]], f1_key: str, f2_key: str) -> list[dict[str, Any]]:
    """计算帕累托前沿（最大化双目标）。

    点 A 支配 B 当且仅当 A.f1 >= B.f1 且 A.f2 >= B.f2，且至少有一个严格大于。
    返回所有不被支配的点，按 f1 降序排列。
    """
    if not points:
        return []

    front = []
    for i, p in enumerate(points):
        dominated = False
        for j, q in enumerate(points):
            if i == j:
                continue
            if q[f1_key] >= p[f1_key] and q[f2_key] >= p[f2_key]:
                if q[f1_key] > p[f1_key] or q[f2_key] > p[f2_key]:
                    dominated = True
                    break
        if not dominated:
            front.append(p)

    front.sort(key=lambda x: x[f1_key], reverse=True)
    return front


async def pareto_search(
    system_prompt: str,
    skeleton_text: str,
    target_v: dict,
    *,
    param_ranges: dict | None = None,
    n_combinations: int = 12,
    seed: int | None = 42,
    section: str | None = None,
    target_text: str | None = None,  # 【v5.10】原文，用于注入关键细节锚点
) -> dict[str, Any]:
    """参数帕累托搜索。

    Args:
        system_prompt: 固定的 system prompt（已收敛的风格 prompt）
        skeleton_text: 情节骨架，作为正向生成的统一输入
        target_v: 目标结构指纹 dict（含 vector 字段）
        param_ranges: 自定义参数范围，默认见下
        n_combinations: 采样多少组参数（质量优先，默认 12）
        seed: 随机种子
        section: 章节区段（影响生成长度等）

    Returns:
        {
            n_combinations: int,
            all_results: [{params, v_prime, v_cosine, diversity, sample_text}, ...],
            pareto_front: [same structure],
            recommendations: {
                conservative: {...},   # f1 最高
                balanced: {...},       # 折中
                creative: {...},       # f2 最高
            },
            f1_key: "v_cosine",
            f2_key: "diversity",
        }
    """
    from .optimizer import forward_generation_v4
    from .structural_analyzer import compute_structural_similarity

    # 默认参数范围
    ranges = param_ranges or {
        "temperature": (0.5, 1.5),
        "top_p": (0.7, 1.0),
        "presence_penalty": (-0.5, 1.2),
    }
    # max_tokens 根据目标长度自适应
    target_len = target_v.get("meta", {}).get("total_chars", 2000)
    max_tokens_lo = max(500, int(target_len * 0.8))
    max_tokens_hi = max(1000, int(target_len * 1.5))
    ranges.setdefault("max_tokens", (float(max_tokens_lo), float(max_tokens_hi)))

    dim_order = ["temperature", "top_p", "max_tokens", "presence_penalty"]
    dim_ranges = [ranges[k] for k in dim_order]

    # LHS 采样
    sample_points = _lhs_sample(n_combinations, dim_ranges, seed=seed)

    # 每组参数 → 正向生成 → 计算 V'
    all_results = []
    target_vector = target_v["vector"]

    for idx, point in enumerate(sample_points):
        params = {
            "temperature": round(point[0], 2),
            "top_p": round(point[1], 3),
            "max_tokens": int(point[2]),
            "presence_penalty": round(point[3], 2),
        }

        try:
            from .plot_skeleton import skeleton_to_generation_prompt, build_key_details_block
            # 【v5.10】提供原文时注入关键细节锚点，约束生成端保留标志性细节
            key_block = None
            if target_text:
                from .key_details import get_key_details
                key_block = build_key_details_block(get_key_details(target_text))
            generated = await forward_generation_v4(
                skeleton_to_generation_prompt(
                    skeleton_text, chapter_section=section,
                    key_details_block=key_block,
                ),
                gen_params=params,
                system_prompt=system_prompt,
            )
        except Exception as e:
            # 失败的样本跳过
            all_results.append({
                "params": params,
                "v_prime": None,
                "v_cosine": 0.0,
                "diversity": 0.0,
                "sample_text": "",
                "error": str(e),
            })
            continue

        from .structural_analyzer import extract_structural_vector
        v_prime = extract_structural_vector(generated)
        v_cos = compute_structural_similarity(target_vector, v_prime["vector"])

        # diversity = lexical_richness (归一化) + sentence_len_variance (归一化)
        # 简单起见，直接按 0~1 范围估算：
        # lexical_richness ≈ 0.2~0.7 → 归一化到 0~1
        lr = v_prime["labels"].get("lexical_richness", 0.3)
        lr_norm = max(0.0, min(1.0, (lr - 0.2) / 0.5))
        # sentence_len_variance ≈ 0~500 → 归一化
        slv = v_prime["labels"].get("sentence_len_variance", 50)
        slv_norm = max(0.0, min(1.0, slv / 500.0))
        diversity = 0.5 * lr_norm + 0.5 * slv_norm

        # 截取样本文本用于前端预览
        sample_preview = generated[:5000] + ("..." if len(generated) > 5000 else "")

        all_results.append({
            "params": params,
            "v_prime": v_prime,
            "v_cosine": round(v_cos, 4),
            "diversity": round(diversity, 4),
            "sample_text": sample_preview,
            "full_text": generated,
        })

    # 过滤失败样本
    valid_results = [r for r in all_results if r["v_prime"] is not None]

    # 帕累托前沿
    front = _pareto_front(valid_results, "v_cosine", "diversity")

    # 三档推荐
    recommendations: dict[str, dict[str, Any]] = {}
    if front:
        # 保守档：f1 最高
        conservative = max(front, key=lambda x: x["v_cosine"])
        recommendations["conservative"] = conservative

        # 创新档：f2 最高
        creative = max(front, key=lambda x: x["diversity"])
        recommendations["creative"] = creative

        # 平衡档：归一化后的 f1 + f2 最大
        # （用有效结果的 min/max 做归一化）
        if valid_results:
            v_min = min(r["v_cosine"] for r in valid_results)
            v_max = max(r["v_cosine"] for r in valid_results)
            d_min = min(r["diversity"] for r in valid_results)
            d_max = max(r["diversity"] for r in valid_results)
            v_range = max(1e-6, v_max - v_min)
            d_range = max(1e-6, d_max - d_min)

            best_balanced = None
            best_score = -1
            for p in front:
                v_norm = (p["v_cosine"] - v_min) / v_range
                d_norm = (p["diversity"] - d_min) / d_range
                score = 0.5 * v_norm + 0.5 * d_norm
                if score > best_score:
                    best_score = score
                    best_balanced = p
            if best_balanced:
                recommendations["balanced"] = best_balanced

    return {
        "n_combinations": n_combinations,
        "n_valid": len(valid_results),
        "all_results": all_results,
        "pareto_front": front,
        "recommendations": recommendations,
        "f1_key": "v_cosine",
        "f2_key": "diversity",
        "param_ranges": {k: list(v) for k, v in ranges.items()},
    }
