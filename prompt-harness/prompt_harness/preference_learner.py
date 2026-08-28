"""用户偏好学习。

从用户的选择历史中提取偏好向量——用户到底喜欢什么样的风格。
把用户偏好融入 V_target，让逆向推理和角度选择更"对味"。

核心方法：符号检验（Sign Test）
- 对每个 V 维度，比较 选中变体 vs 未选中变体 的平均值
- 如果用户系统性地选某个方向上更高/更低的变体，说明有偏好
- 至少 5 轮选择后结果才比较可靠

偏好向量 V_pref ∈ [-1, +1]^D：
    +1 = 用户强烈偏好该维度更高的值
    -1 = 用户强烈偏好该维度更低的值
     0 = 无显著偏好

应用方式：V_target' = V_target + strength × V_pref × σ(V)
    （按各维度的标准差比例偏移，避免量纲差异）
"""
from __future__ import annotations

import math
from typing import Any


def learn_preference(choice_history: list[dict[str, Any]]) -> dict[str, Any]:
    """从选择历史中学习用户偏好。

    Args:
        choice_history: [
            {
                "chosen_v": [v1, v2, ..., v9],   # 选中变体的 V 向量
                "other_vs": [[...], [...]],      # 未选中变体的 V 向量列表
                "round": int,
            },
            ...
        ]

    Returns:
        {
            "preference_vector": [float, ...],  # 10 个？不，9 维（对应 V 向量）
            "confidence": [float, ...],         # 0~1，每维的置信度
            "n_rounds": int,
            "direction": [+1/-1/0, ...],        # 离散化偏好方向
            "interpretation": str,              # 人可读的偏好描述
            "per_dim_stats": [{dim, mean_chosen, mean_other, diff, sign_pos, sign_neg, z_score}, ...],
        }
    """
    if not choice_history:
        return _empty_result(0)

    n_rounds = len(choice_history)
    n_dims = len(choice_history[0]["chosen_v"])

    # 收集每维：选中值列表、未选中平均值列表
    chosen_vals: list[list[float]] = [[] for _ in range(n_dims)]
    other_vals: list[list[float]] = [[] for _ in range(n_dims)]

    for record in choice_history:
        chosen = record["chosen_v"]
        others = record["other_vs"]
        if not others or len(chosen) != n_dims:
            continue

        # 计算未选中的平均
        other_avg = [0.0] * n_dims
        for ov in others:
            for d in range(n_dims):
                other_avg[d] += ov[d]
        for d in range(n_dims):
            other_avg[d] /= len(others)

        for d in range(n_dims):
            chosen_vals[d].append(chosen[d])
            other_vals[d].append(other_avg[d])

    # 每维分析
    per_dim_stats = []
    preference_vector = []
    confidence = []
    direction = []

    v_dim_names = [
        "avg_sentence_len", "sentence_len_variance", "dialogue_density",
        "median_paragraph_len", "paragraph_frequency",
        "comma_density", "special_punct_density",
        "line_break_frequency", "lexical_richness",
        "sentence_start_diversity", "modifier_density",
        "dialogue_turn_density", "sentences_per_paragraph",
    ]

    for d in range(n_dims):
        chosen_d = chosen_vals[d]
        other_d = other_vals[d]
        n = len(chosen_d)

        if n == 0:
            per_dim_stats.append({"dim": v_dim_names[d] if d < len(v_dim_names) else f"dim_{d}",
                                   "mean_chosen": 0, "mean_other": 0, "diff": 0,
                                   "sign_pos": 0, "sign_neg": 0, "z_score": 0})
            preference_vector.append(0.0)
            confidence.append(0.0)
            direction.append(0)
            continue

        mean_chosen = sum(chosen_d) / n
        mean_other = sum(other_d) / n
        diff = mean_chosen - mean_other

        # 符号检验：正号（选中 > 未选中）和负号的数量
        sign_pos = sum(1 for c, o in zip(chosen_d, other_d) if c > o)
        sign_neg = sum(1 for c, o in zip(chosen_d, other_d) if c < o)

        # 二项检验近似（正态近似）
        # H0: 正负号各 50%
        expected = n * 0.5
        variance = n * 0.25
        if variance > 0:
            z = (sign_pos - expected) / math.sqrt(variance)
        else:
            z = 0.0

        # 偏好强度 = z 分数压缩到 [-1, 1]
        pref = math.tanh(z / 2.0)  # tanh 平滑压缩

        # 置信度 = 1 - p-value 近似
        # |z| 越大越置信
        conf = min(1.0, abs(z) / 2.5)  # z=2.5 约 p=0.01 → 置信度 1.0

        # 离散方向
        if conf > 0.5:  # 中等以上置信
            dir_val = 1 if pref > 0 else -1
        else:
            dir_val = 0

        per_dim_stats.append({
            "dim": v_dim_names[d] if d < len(v_dim_names) else f"dim_{d}",
            "mean_chosen": round(mean_chosen, 4),
            "mean_other": round(mean_other, 4),
            "diff": round(diff, 4),
            "sign_pos": sign_pos,
            "sign_neg": sign_neg,
            "z_score": round(z, 3),
        })
        preference_vector.append(round(pref, 4))
        confidence.append(round(conf, 4))
        direction.append(dir_val)

    # 人可读解释
    interpretation = _generate_interpretation(v_dim_names, preference_vector, confidence, n_rounds)

    return {
        "preference_vector": preference_vector,
        "confidence": confidence,
        "n_rounds": n_rounds,
        "direction": direction,
        "interpretation": interpretation,
        "per_dim_stats": per_dim_stats,
        "is_reliable": n_rounds >= 5,
    }


def _empty_result(n_rounds: int) -> dict[str, Any]:
    return {
        "preference_vector": [0.0] * 9,
        "confidence": [0.0] * 9,
        "n_rounds": n_rounds,
        "direction": [0] * 9,
        "interpretation": "数据不足，暂无法判断偏好。",
        "per_dim_stats": [],
        "is_reliable": False,
    }


def _generate_interpretation(names: list[str], prefs: list[float], confs: list[float], n: int) -> str:
    """生成人可读的偏好描述。"""
    if n < 3:
        return f"仅 {n} 轮选择，数据不足。继续训练以积累偏好数据。"

    strong_prefs = []
    for i, name in enumerate(names):
        if i >= len(prefs):
            break
        if confs[i] > 0.5:
            direction = "更高" if prefs[i] > 0 else "更低"
            strong_prefs.append(f"{name}{direction}")

    if not strong_prefs:
        return "目前没有显著的风格偏好。你的选择比较多样化。"

    desc = f"基于 {n} 轮选择，你似乎偏好：" + "、".join(strong_prefs[:5]) + "。"
    if len(strong_prefs) > 5:
        desc += f" 等 {len(strong_prefs)} 个维度。"
    return desc


def apply_preference_bias(
    base_v_target: dict,
    preference: dict,
    strength: float = 0.3,
) -> dict:
    """把用户偏好融入 V_target。

    V_target' = V_target + strength × V_pref × σ_hat

    其中 σ_hat 用 V_target 的各维度值的比例估计（近似标准差），
    避免不同量纲的维度偏移量不可比。

    Args:
        base_v_target: 基础目标 V 向量 dict（含 vector, labels）
        preference: learn_preference 的返回值
        strength: 0~1，偏好强度系数

    Returns:
        调整后的 V_target dict（结构相同，vector 已偏移）
    """
    if not preference.get("is_reliable", False):
        return base_v_target  # 不可靠就不偏移

    pref_vec = preference["preference_vector"]
    conf_vec = preference["confidence"]
    base_vec = list(base_v_target.get("vector", []))

    if len(pref_vec) != len(base_vec):
        return base_v_target  # 维度不匹配就跳过

    # 对每个维度，按比例偏移
    # 偏移量 = strength × pref × conf × base_value × 0.2
    # （最多偏移 base_value 的 20% × strength）
    new_vec = []
    for i, base_val in enumerate(base_vec):
        pref = pref_vec[i]
        conf = conf_vec[i]
        # 用 base_val 的一定比例作为偏移尺度
        scale = max(abs(base_val) * 0.2, 0.01)  # 最小 0.01，防止 0 值
        delta = strength * pref * conf * scale
        new_val = base_val + delta
        # 非负约束（大多数 V 维度都是非负的）
        new_val = max(0.0, new_val)
        new_vec.append(round(new_val, 4))

    # 更新 labels 里的值（同步更新）
    new_labels = dict(base_v_target.get("labels", {}))
    label_keys = [
        "avg_sentence_len", "sentence_len_variance", "dialogue_density",
        "median_paragraph_len", "paragraph_frequency",
        "comma_density", "special_punct_density",
        "line_break_frequency", "lexical_richness",
        "sentence_start_diversity", "modifier_density",
        "dialogue_turn_density", "sentences_per_paragraph",
    ]
    for i, key in enumerate(label_keys):
        if i < len(new_vec) and key in new_labels:
            new_labels[key] = round(new_vec[i], 4)

    result = dict(base_v_target)
    result["vector"] = new_vec
    result["labels"] = new_labels
    result["preference_applied"] = True
    result["preference_strength"] = strength
    return result


def angle_preference_bonus(
    angle_name: str,
    preference: dict,
    angle_to_v_dims: dict[str, list[tuple[str, float]]] | None = None,
) -> float:
    """计算某个角度的偏好加成。

    用于角度 bandit 的 bonus 调整——用户喜欢的方向的角度加权重。

    Args:
        angle_name: 角度名称
        preference: 偏好向量
        angle_to_v_dims: 角度 → [(v_dim_name, direction_sign)] 的映射
                       direction_sign = +1 表示这个角度会提升该 V 维度
                       direction_sign = -1 表示会降低

    Returns:
        bonus 值（0~0.3 左右），加到 bandit 的 mean 上
    """
    if not preference.get("is_reliable", False):
        return 0.0

    # 默认映射（粗糙的先验知识）
    if angle_to_v_dims is None:
        angle_to_v_dims = {
            "强化对话节奏": [("dialogue_density", 1.0), ("avg_sentence_len", -0.5)],
            "增加环境描写": [("dialogue_density", -0.5), ("avg_sentence_len", 0.5), ("special_punct_density", -0.3)],
            "压缩节奏加快": [("avg_sentence_len", -0.8), ("paragraph_frequency", 0.5)],
            "深化心理描写": [("avg_sentence_len", 0.3), ("lexical_richness", 0.4)],
            "强化场景画面": [("avg_sentence_len", 0.5), ("comma_density", 0.4), ("special_punct_density", -0.2)],
            "调整句式结构": [("sentence_len_variance", 0.6), ("avg_sentence_len", 0.2)],
        }

    dim_effects = angle_to_v_dims.get(angle_name, [])
    if not dim_effects:
        return 0.0

    pref_vec = preference["preference_vector"]
    conf_vec = preference["confidence"]
    v_dim_names = [
        "avg_sentence_len", "sentence_len_variance", "dialogue_density",
        "median_paragraph_len", "paragraph_frequency",
        "comma_density", "special_punct_density",
        "line_break_frequency", "lexical_richness",
        "sentence_start_diversity", "modifier_density",
        "dialogue_turn_density", "sentences_per_paragraph",
    ]

    bonus = 0.0
    for v_dim, direction in dim_effects:
        if v_dim in v_dim_names:
            idx = v_dim_names.index(v_dim)
            # 如果该角度提升这个维度 × 用户偏好这个维度更高 → 正 bonus
            # 如果该角度降低这个维度 × 用户偏好这个维度更低 → 正 bonus（因为 pref 为负，direction 为负，乘积为正）
            alignment = direction * pref_vec[idx] * conf_vec[idx]
            bonus += alignment * 0.1  # 每个维度最多贡献 0.1

    return max(0.0, bonus)  # 只加不减，避免负反馈
