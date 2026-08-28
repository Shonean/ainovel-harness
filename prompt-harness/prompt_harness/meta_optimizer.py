"""Meta-Optimizer：评分权重自动校准。

在每次训练 run 开始前，通过快速校准确定最优的 5 项评分权重。
避免人工调参，让系统自动适配不同文本类型。

核心思路：
1. 用精心构造的校准测试用例（每个维度有独立对照实验）
2. 对每组权重配置，计算其"综合质量分"（分离度 + 覆盖率 + 平衡度）
3. 在 5 维权空间搜索让综合质量分最大化的权重

V2：不依赖 LLM 生成——用 scoring function 直接计算测试用例的分数。
"""
from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

import numpy as np

from .config import load_scoring_params, SETTINGS
from .structural_analyzer import serialization_fit_score, compute_composite_score_v5


# ---------------------------------------------------------------------------
# 校准测试用例
# ---------------------------------------------------------------------------

class CalibrationCase:
    """一个校准测试用例：已知属性的文本对 (generated, target)。

    Attributes:
        name: 用例名称
        generated: 生成文本
        target: 目标文本
        expected_rank: 期望的相对排名（1=最好，越大越差）
        dimension_tags: 此用例主要测试的维度 ["cos", "dim_match", "length", "serial", "quality"]
    """

    def __init__(
        self,
        name: str,
        generated: str,
        target: str,
        expected_rank: int,
        dimension_tags: list[str] | None = None,
    ):
        self.name = name
        self.generated = generated
        self.target = target
        self.expected_rank = expected_rank
        self.dimension_tags = dimension_tags or []


# 统一的参考目标文本
_REF_TARGET = (
    "林川推开铁门，雨声灌了进来。他抖了抖雨衣上的水珠，把伞收拢靠在墙边。"
    "「病历呢？」他伸出手。一份档案袋从身后递了过来，边角已经被雨水洇湿了一小块。"
    "林川翻开病历，眉头渐渐皱了起来。「什么时候的事？」"
    "「三天前。」身后的声音有些紧张。"
    "林川没回头。他知道身后的脚步声会一直跟着他走进六楼。"
    "走廊的灯管忽明忽暗，像是一双眨动的眼睛。这栋楼里的故事，比病历上写的要多得多。"
    "他在一扇铁门前停下，掏出钥匙。锁芯转动的声音在空旷的走廊里格外刺耳。"
    "「你在外面等。」他说完，推门走了进去。"
)


def _build_calibration_cases() -> list[CalibrationCase]:
    """构造校准测试用例集。

    每个用例代表一种生成质量缺陷，expected_rank 定义了最优排序。
    """
    cases: list[CalibrationCase] = []

    # ── 完美用例（rank=1）：原文本身 ──
    cases.append(CalibrationCase(
        "perfect_exact", _REF_TARGET, _REF_TARGET,
        expected_rank=1,
        dimension_tags=["cos", "dim_match", "length", "serial", "quality"],
    ))

    # ── rank=2：结构稍有不同的好文本 ──
    good_text = (
        "「你迟到了。」林川推开铁门，雨声灌了进来。"
        "他浑身湿透，脸上分不清是雨水还是汗水。"
        "「路上出了点事。」他说着，把档案袋放在桌上。"
        "林川看了他一眼，没有说话，低头翻开病历。"
        "「三号床的病人，昨晚又闹了。」那人补充道。"
        "「我知道。」林川站起身，走向门口。"
        "走廊的灯管在他身后一盏盏熄灭。"
    )
    cases.append(CalibrationCase(
        "good_serial", good_text, _REF_TARGET,
        expected_rank=2,
        dimension_tags=["cos", "dim_match", "length", "serial"],
    ))

    # ── rank=3：结构匹配但字数太少（测试 length 维度）──
    good_match_too_short = (
        "林川推开门走了进去。他翻开病历看了一眼。走廊很长。"
    )
    cases.append(CalibrationCase(
        "good_match_too_short", good_match_too_short, _REF_TARGET,
        expected_rank=3,
        dimension_tags=["length"],
    ))

    # ── rank=4：开头铺陈（测试 serial 维度）──
    bad_opening = (
        "夜幕低垂，古老的城墙在月色下泛着青灰色的光。远处传来隐约的笛声，"
        "像是在诉说着千年的故事。这座城已经很久没有热闹过了，路上的行人稀少，"
        "只有几盏昏黄的灯笼在风中摇曳。据说三百年前这里曾是一片繁华的都城，"
        "如今只剩下断壁残垣和挥之不去的传说。谁也不知道那场大火之后，"
        "城里究竟发生了什么。林川站在城门口，望着这一切。"
    )
    cases.append(CalibrationCase(
        "bad_opening", bad_opening, _REF_TARGET,
        expected_rank=4,
        dimension_tags=["serial"],
    ))

    # ── rank=4：结尾收束（测试 serial 维度）──
    bad_ending = (
        "林川推开铁门走了进去。他翻开病历，一页页地看着。"
        "走廊里传来护士的脚步声。他抬起头，望了一眼窗外。夜色正浓。"
        "他突然想起了很多事情——那些他曾经以为已经遗忘的。"
        "这一夜，他终于明白了人生的真谛。一切都会好起来的。"
        "故事还在继续，但他知道，有些东西已经永远改变了。"
        "余晖洒在窗台上，他深深地叹了口气。"
    )
    cases.append(CalibrationCase(
        "bad_ending", bad_ending, _REF_TARGET,
        expected_rank=4,
        dimension_tags=["serial", "quality"],
    ))

    # ── rank=5：字数严重不足 + 连载问题（双缺陷）──
    too_short_and_bad = (
        "从前有座山，山里有座庙。庙里有个老和尚和一个小和尚。"
        "老和尚在给小和尚讲故事。故事是这样的。"
    )
    cases.append(CalibrationCase(
        "too_short_bad_serial", too_short_and_bad, _REF_TARGET,
        expected_rank=5,
        dimension_tags=["length", "serial"],
    ))

    # ── rank=6：严重重复（质量极差）──
    repetitive = (
        "林川走在走廊上。走廊很长。走廊很暗。走廊很安静。"
        "他走得很慢。一步。两步。三步。他数着自己的脚步。"
        "一。二。三。四。五。他继续走。走着走着。走着走着。"
        "他到了。他推开门。门开了。他走了进去。"
    )
    cases.append(CalibrationCase(
        "repetitive", repetitive, _REF_TARGET,
        expected_rank=6,
        dimension_tags=["quality", "cos", "dim_match"],
    ))

    return cases


# ---------------------------------------------------------------------------
# 权重配置评估（综合质量分）
# ---------------------------------------------------------------------------

_RANK_WEIGHTS = [20, 14, 9, 5, 2, 1]  # rank 1~6 的期望得分


def evaluate_weight_config(
    weights: dict[str, float],
    cases: list[CalibrationCase],
) -> float:
    """评估一组权重配置的综合质量（0~1）。

    用 Spearman 秩相关衡量各用例的排序与期望排序的一致程度。
    完美排序 = 1.0，完全反序 = 0.0，随机 ≈ 0.5。

    额外惩罚：
    - 忽略权重惩罚：任一 β < 0.05 时扣分（防止维度被完全忽略）
    - 极端失衡惩罚：max/median 比 > 3 时扣分
    """
    from .scorer import length_alignment_score, fluency_score
    from .structural_analyzer import (
        extract_structural_vector, compute_structural_similarity,
    )

    # 提取目标文本的结构指纹
    target_vec = extract_structural_vector(cases[0].target) if cases else {}
    t_vec = target_vec.get("vector") if isinstance(target_vec, dict) else None

    # 1. 计算每个用例的得分
    case_scores: list[tuple[float, int]] = []  # (score, expected_rank)

    for case in cases:
        gen_v = extract_structural_vector(case.generated)
        g_vec = gen_v.get("vector") if isinstance(gen_v, dict) else None

        if t_vec and g_vec and len(t_vec) == len(g_vec):
            v_cos = compute_structural_similarity(t_vec, g_vec)
            deltas_pct = []
            for tv, gv in zip(t_vec, g_vec):
                if abs(tv) > 1e-6:
                    deltas_pct.append(abs(tv - gv) / abs(tv) * 100)
                else:
                    deltas_pct.append(0.0)
            mean_delta = float(np.mean(deltas_pct)) if deltas_pct else 0.0
        else:
            v_cos = 0.5
            mean_delta = 15.0
            deltas_pct = None

        l_score = length_alignment_score(case.generated, case.target)
        s_score = serialization_fit_score(case.generated)
        flu = fluency_score(case.generated)
        c_score = 0.5 * 0.5 + 0.5 * flu

        # 【v5.10】事实一致性维度参与校准（第 6 个评分维度）
        from .key_details import get_key_details, factual_consistency_score
        fc = factual_consistency_score(
            case.generated, case.target, key_details=get_key_details(case.target),
        )["score"]

        composite = compute_composite_score_v5(
            v_cos, mean_delta,
            dim_deltas_pct=deltas_pct,
            length_alignment=l_score,
            serialization_fit=s_score,
            content_quality=c_score,
            factual_consistency=fc,
            weights=weights,
        )

        case_scores.append((composite, case.expected_rank))

    # 2. 按得分排序，计算与期望排序的 Spearman 相关系数
    actual_order = sorted(case_scores, key=lambda x: -x[0])
    actual_ranks = {r: i + 1 for i, (_, r) in enumerate(case_scores)}

    expected_ranks = [c.expected_rank for c in cases]
    obtained_ranks = [actual_ranks[c.expected_rank] for c in cases]

    # Spearman 秩相关
    n = len(cases)
    d_squared = sum((o - e) ** 2 for o, e in zip(obtained_ranks, expected_ranks))
    spearman = 1.0 - (6.0 * d_squared) / (n * (n * n - 1))

    # 3. 额外惩罚项
    penalties = 0.0

    # 3a. 忽略维度惩罚
    for key in ["b1", "b2", "b3", "b4", "b5", "b6"]:
        if weights.get(key, 0) < 0.02:
            penalties += 0.15

    # 3b. 极端失衡惩罚
    values = sorted([weights.get(k, 0) for k in ["b1", "b2", "b3", "b4", "b5", "b6"]])
    if values[-1] > 0 and values[2] > 0:
        ratio = values[-1] / values[2]
        if ratio > 4:
            penalties += 0.2 * min(1.0, (ratio - 4) / 4)
        elif ratio > 2:
            penalties += 0.08

    # 4. 综合质量 = (0~1 归一化后的 Spearman) - 惩罚
    raw_quality = (spearman + 1.0) / 2.0
    final_quality = max(0.0, raw_quality - penalties)

    return round(final_quality, 6)


# ---------------------------------------------------------------------------
# 权重采样
# ---------------------------------------------------------------------------

def sample_weights_lhs(
    bounds: dict[str, tuple[float, float]],
    n_samples: int,
    rng: np.random.Generator | None = None,
) -> list[dict[str, float]]:
    """用 LHS 在 6 维权空间采样，然后 L1 归一化到 Σ=1.0。"""
    if rng is None:
        rng = np.random.default_rng()

    keys = ["b1", "b2", "b3", "b4", "b5", "b6"]
    samples = []

    for i in range(n_samples):
        raw = []
        for j, key in enumerate(keys):
            lo, hi = bounds.get(key, (0.0, 1.0))
            # LHS: 将 [0,1] 分成 n_samples 个区间，每个区间随机取一点
            cell = (i + rng.uniform(0, 1)) / n_samples
            val = lo + (hi - lo) * cell
            raw.append(val)

        total = sum(raw)
        if total > 0:
            raw = [v / total for v in raw]
        samples.append({k: float(v) for k, v in zip(keys, raw)})

    return samples


# ---------------------------------------------------------------------------
# WeightCalibrator
# ---------------------------------------------------------------------------

class WeightCalibrator:
    """评分权重的自动校准器。"""

    def __init__(self, params_path: Path | None = None):
        self.params = load_scoring_params(params_path)
        self.cases = _build_calibration_cases()
        self.defaults = self.params.get("scoring_weights", {}).get("defaults", {})
        self.search_space = self.params.get("scoring_weights", {}).get("search_space", {})

        mo = self.params.get("meta_optimizer", {}).get("defaults", {})
        self.n_samples = mo.get("n_calibration_samples", 16)

    def _get_default_weights(self) -> dict[str, float]:
        return {
            "b1": self.defaults.get("b1", 0.08),  # V-cos 风格下限
            "b2": self.defaults.get("b2", 0.0),   # dim_match（降诊断）
            "b3": self.defaults.get("b3", 0.0),   # length_alignment（降诊断）
            "b4": self.defaults.get("b4", 0.07),  # serialization_fit 连载约束
            "b5": self.defaults.get("b5", 0.15),  # 【v5.12】semantic_coverage 语义覆盖
            "b6": self.defaults.get("b6", 0.18),  # 事实一致性
            "b7": self.defaults.get("b7", 0.22),  # 事件保真
            "b8": self.defaults.get("b8", 0.30),  # 原文字符复现（核心）
        }

    def calibrate(self, verbose: bool = True) -> dict[str, float]:
        """执行权重校准，返回最优权重。"""
        if not self.cases:
            if verbose:
                print("[WeightCalibrator] No calibration cases, using defaults")
            return self._get_default_weights()

        # 1. 采样 N 组候选权重（6 维：b1~b6）
        bounds = {}
        for key in ["b1", "b2", "b3", "b4", "b5", "b6"]:
            sp = self.search_space.get(key, {})
            bounds[key] = (sp.get("min", 0.05), sp.get("max", 0.45))

        candidates = sample_weights_lhs(bounds, self.n_samples)
        candidates.insert(0, self._get_default_weights())

        # 2. 评估每组权重的综合质量
        scored: list[tuple[float, dict[str, float]]] = []
        for w in candidates:
            quality = evaluate_weight_config(w, self.cases)
            scored.append((quality, w))

        # 3. 排序并选择最优
        scored.sort(key=lambda x: -x[0])
        best_quality, best_weights = scored[0]

        if verbose:
            print(f"[WeightCalibrator] Calibrated {len(candidates)} weight configs")
            print(f"[WeightCalibrator] Best quality: {best_quality:.4f}")
            print(f"[WeightCalibrator] Best weights: "
                  f"b1={best_weights.get('b1', 0):.3f} b2={best_weights.get('b2', 0):.3f} "
                  f"b3={best_weights.get('b3', 0):.3f} b4={best_weights.get('b4', 0):.3f} "
                  f"b5={best_weights.get('b5', 0):.3f} b6={best_weights.get('b6', 0):.3f}")
            if best_quality < 0.4:
                print(f"[WeightCalibrator] [WARN] Quality too low, falling back to defaults")
                return self._get_default_weights()

        return best_weights

    def calibrate_with_text(
        self, target_text: str, verbose: bool = True,
    ) -> dict[str, float]:
        return self.calibrate(verbose=verbose)


# ---------------------------------------------------------------------------
# 便捷函数
# ---------------------------------------------------------------------------

_B1_B6_KEYS = ("b1", "b2", "b3", "b4", "b5", "b6")
# 【v5.12】内容主导 0.85：b5+b6+b7+b8=0.85，风格下限 b1，连载约束 b4（用户拍板固定表）
_DEFAULT_8DIM = {"b1": 0.08, "b2": 0.0, "b3": 0.0, "b4": 0.07,
                 "b5": 0.15, "b6": 0.18, "b7": 0.22, "b8": 0.30}


def _ensure_8dim(weights: dict[str, float]) -> dict[str, float]:
    """返回 v5.12 固定 8 维权重表（内容主导 0.85）。

    v5.12 起权重表由用户拍板固定（内容核心 b5+b6+b7+b8=0.85），不再由校准覆盖——
    校准合成用例无骨架，调不出内容维度（b5~b8）；且 b2/b3 已剔为诊断（权重 0）。
    保留 weights 参数仅为兼容旧调用签名，实际固定返回 _DEFAULT_8DIM。
    """
    return dict(_DEFAULT_8DIM)


def get_optimal_weights(
    target_text: str | None = None, verbose: bool = True,
) -> dict[str, float]:
    # 【v5.12】权重表固定为内容主导 0.85（用户拍板），跳过 LHS 校准——
    # 校准合成用例无骨架，调不出内容维度（b5~b8）；校准对 v5.12 无意义。
    # Calibrator 保留供 6 维/无骨架路径兼容，不走默认训练链路。
    return _ensure_8dim(None)
