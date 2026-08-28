"""文档画像 + 主动采样。

对整本书的每章提取 V 向量 → 聚类 → 得到风格分区 →
推荐训练顺序（先覆盖所有风格，再巩固，最后边缘样本泛化测试）。

这是"练哪章、按什么顺序练"的量化决策。
"""
from __future__ import annotations

import math
from typing import Any


def _kmeans(data: list[list[float]], k: int, *, max_iter: int = 50, seed: int = 42) -> tuple[list[int], list[list[float]], float]:
    """轻量 k-means 聚类。

    Returns:
        (labels, centroids, inertia)
    """
    import random
    import math

    rng = random.Random(seed)
    n = len(data)
    if n == 0:
        return [], [], 0.0

    dim = len(data[0])

    # 初始化：k-means++ 风格（简化）
    centroids = []
    first_idx = rng.randrange(n)
    centroids.append(list(data[first_idx]))

    for _ in range(1, k):
        # 每个点到最近已有质心的距离
        distances = []
        for point in data:
            min_dist = float("inf")
            for c in centroids:
                d = _euclidean(point, c)
                if d < min_dist:
                    min_dist = d
            distances.append(min_dist)
        # 按距离平方加权随机选下一个质心
        total = sum(d * d for d in distances)
        if total == 0:
            # 所有点都一样，随便选
            idx = rng.randrange(n)
        else:
            r = rng.random() * total
            cumulative = 0.0
            idx = 0
            for i, d in enumerate(distances):
                cumulative += d * d
                if cumulative >= r:
                    idx = i
                    break
        centroids.append(list(data[idx]))

    # 迭代
    labels = [0] * n
    for iteration in range(max_iter):
        # 分配
        changed = False
        for i, point in enumerate(data):
            best_cluster = 0
            best_dist = float("inf")
            for j, c in enumerate(centroids):
                d = _euclidean(point, c)
                if d < best_dist:
                    best_dist = d
                    best_cluster = j
            if labels[i] != best_cluster:
                labels[i] = best_cluster
                changed = True

        if not changed:
            break

        # 更新质心
        for j in range(k):
            cluster_points = [data[i] for i in range(n) if labels[i] == j]
            if cluster_points:
                new_centroid = [0.0] * dim
                for p in cluster_points:
                    for d in range(dim):
                        new_centroid[d] += p[d]
                for d in range(dim):
                    new_centroid[d] /= len(cluster_points)
                centroids[j] = new_centroid

    # 计算 inertia
    inertia = 0.0
    for i, point in enumerate(data):
        inertia += _euclidean(point, centroids[labels[i]]) ** 2

    return labels, centroids, inertia


def _euclidean(a: list[float], b: list[float]) -> float:
    s = 0.0
    for x, y in zip(a, b):
        s += (x - y) ** 2
    return math.sqrt(s)


def _elbow_method(data: list[list[float]], max_k: int = 8, min_k: int = 2) -> int:
    """手肘法选最佳 k。

    计算每个 k 的 inertia，找"拐点"——inertia 下降速率最大变化的点。
    简化实现：找二阶差分最大的点。
    """
    n = len(data)
    if n <= 2:
        return 1

    effective_max_k = min(max_k, n - 1)
    if effective_max_k < min_k:
        return effective_max_k

    inertias = []
    for k in range(min_k, effective_max_k + 1):
        _, _, inertia = _kmeans(data, k)
        inertias.append(inertia)

    if len(inertias) < 3:
        return min_k

    # 找下降速率变化最大的点（手肘）
    # 一阶差分：d[i] = inertia[i] - inertia[i-1] （都是负的，幅度越大下降越快）
    first_diffs = [inertias[i] - inertias[i - 1] for i in range(1, len(inertias))]
    # 二阶差分：dd[i] = d[i+1] - d[i]
    second_diffs = [first_diffs[i + 1] - first_diffs[i] for i in range(len(first_diffs) - 1)]

    # 二阶差分最大（最正）的位置就是拐点
    # （inertia 下降速度从快变慢的转折点）
    best_dd_idx = max(range(len(second_diffs)), key=lambda i: second_diffs[i])
    best_k = min_k + best_dd_idx + 1  # +1 因为二阶差分索引偏移

    return best_k


def build_document_profile(chapter_vs: list[dict]) -> dict[str, Any]:
    """构建文档画像。

    输入每章的 V 向量列表，计算聚类、风格分区等。

    Args:
        chapter_vs: [{"vector": [...], "labels": {...}, "chapter_index": int, "chapter_title": str}, ...]

    Returns:
        {
            n_chapters: int,
            n_clusters: int,
            cluster_labels: [int, ...],  # 每章的簇索引
            centroids: [[float, ...], ...],
            cluster_sizes: [int, ...],
            cluster_ratios: [float, ...],
            cluster_profiles: [  # 每个簇的典型特征
                {
                    cluster_id: int,
                    size: int,
                    ratio: float,
                    centroid_vector: [float, ...],
                    typical_labels: {...},
                    description: str,
                    representative_chapter: int,  # 最接近质心的章节索引
                },
                ...
            ],
            overall_mean_vector: [float, ...],
            overall_std_vector: [float, ...],
            v_dim_names: [str, ...],
        }
    """
    if not chapter_vs:
        return {"n_chapters": 0, "n_clusters": 0, "cluster_labels": [], "centroids": [],
                "cluster_sizes": [], "cluster_ratios": [], "cluster_profiles": [],
                "overall_mean_vector": [], "overall_std_vector": [], "v_dim_names": []}

    # 归一化 V 向量（不同维度量纲差异大，聚类前归一化到 0~1）
    vectors = [ch["vector"] for ch in chapter_vs]
    n = len(vectors)
    dim = len(vectors[0])

    # 计算 min/max 用于归一化
    mins = [min(v[d] for v in vectors) for d in range(dim)]
    maxs = [max(v[d] for v in vectors) for d in range(dim)]
    ranges = [max(maxs[d] - mins[d], 1e-6) for d in range(dim)]

    normalized = [
        [(vectors[i][d] - mins[d]) / ranges[d] for d in range(dim)]
        for i in range(n)
    ]

    # 选 k
    max_k = min(8, n // 3)
    k = _elbow_method(normalized, max_k=max(2, max_k), min_k=2)
    if k >= n:
        k = max(1, n // 2)

    # 聚类
    labels, centroids_norm, inertia = _kmeans(normalized, k)

    # 把质心还原回原始尺度
    centroids = []
    for cn in centroids_norm:
        c = [cn[d] * ranges[d] + mins[d] for d in range(dim)]
        centroids.append(c)

    # 统计每簇大小
    cluster_sizes = [sum(1 for l in labels if l == j) for j in range(k)]
    cluster_ratios = [s / n for s in cluster_sizes]

    # 每簇画像
    v_dim_names = [
        "avg_sentence_len", "sentence_len_variance", "dialogue_density",
        "median_paragraph_len", "paragraph_frequency",
        "comma_density", "special_punct_density",
        "line_break_frequency", "lexical_richness",
        "sentence_start_diversity", "modifier_density",
        "dialogue_turn_density", "sentences_per_paragraph",
    ]

    cluster_profiles = []
    for j in range(k):
        # 找代表章节（离质心最近）
        best_idx = -1
        best_dist = float("inf")
        for i in range(n):
            if labels[i] == j:
                d = _euclidean(normalized[i], centroids_norm[j])
                if d < best_dist:
                    best_dist = d
                    best_idx = i

        # 典型 labels（用质心的值构造）
        typical_labels = {}
        if chapter_vs and "labels" in chapter_vs[0]:
            # 用质心值估算
            for idx, name in enumerate(v_dim_names):
                if idx < dim:
                    typical_labels[name] = round(centroids[j][idx], 4)

        # 生成描述
        desc = _describe_cluster(centroids[j], v_dim_names, mins, maxs)

        cluster_profiles.append({
            "cluster_id": j,
            "size": cluster_sizes[j],
            "ratio": round(cluster_ratios[j], 3),
            "centroid_vector": [round(v, 4) for v in centroids[j]],
            "typical_labels": typical_labels,
            "description": desc,
            "representative_chapter": best_idx,
            "representative_chapter_title": chapter_vs[best_idx].get("chapter_title", "") if best_idx >= 0 else "",
        })

    # 整体统计
    overall_mean = [sum(v[d] for v in vectors) / n for d in range(dim)]
    overall_std = [
        math.sqrt(sum((v[d] - overall_mean[d]) ** 2 for v in vectors) / n)
        for d in range(dim)
    ]

    return {
        "n_chapters": n,
        "n_clusters": k,
        "cluster_labels": labels,
        "centroids": [round_list(c, 4) for c in centroids],
        "cluster_sizes": cluster_sizes,
        "cluster_ratios": [round(r, 3) for r in cluster_ratios],
        "cluster_profiles": cluster_profiles,
        "overall_mean_vector": [round(v, 4) for v in overall_mean],
        "overall_std_vector": [round(v, 4) for v in overall_std],
        "v_dim_names": v_dim_names,
        "inertia": round(inertia, 4),
    }


def round_list(lst: list[float], n: int) -> list[float]:
    return [round(v, n) for v in lst]


def _describe_cluster(centroid: list[float], dim_names: list[str], mins: list[float], maxs: list[float]) -> str:
    """根据质心生成该风格分区的文字描述。

    找出偏离整体水平最大的几个维度。
    """
    if not centroid:
        return ""

    # 计算每个维度的相对位置（0~1，低/中/高）
    relative = []
    for d, val in enumerate(centroid):
        if d >= len(mins) or d >= len(maxs):
            continue
        rng = max(maxs[d] - mins[d], 1e-6)
        pos = (val - mins[d]) / rng
        relative.append((dim_names[d] if d < len(dim_names) else f"dim_{d}", pos, val))

    # 找偏离两端最大的（最有特征的）
    relative.sort(key=lambda x: abs(x[1] - 0.5), reverse=True)

    parts = []
    for name, pos, val in relative[:3]:
        if pos > 0.66:
            parts.append(f"{name}偏高")
        elif pos < 0.33:
            parts.append(f"{name}偏低")
        else:
            parts.append(f"{name}中等")

    return "；".join(parts) if parts else "风格中性"


def recommend_training_order(
    chapter_vs: list[dict],
    trained_indices: list[int],
    n_recommend: int = 5,
    strategy: str = "phased",
) -> list[dict[str, Any]]:
    """推荐训练顺序。

    Args:
        chapter_vs: 每章 V 向量 + 索引
        trained_indices: 已经训练过的章节索引
        n_recommend: 推荐几个
        strategy: "phased"（阶段式） | "diversity"（多样性优先） | "uncertainty"（不确定优先）

    Returns:
        [{chapter_index, reason, phase, cluster_id, distance_to_centroid}, ...]
    """
    if not chapter_vs:
        return []

    profile = build_document_profile(chapter_vs)
    k = profile["n_clusters"]
    labels = profile["cluster_labels"]
    n = len(chapter_vs)

    trained_set = set(trained_indices)
    untrained = [i for i in range(n) if i not in trained_set]

    if not untrained:
        return []

    if strategy == "diversity":
        return _diversity_recommend(chapter_vs, untrained, labels, k, profile, n_recommend)
    elif strategy == "uncertainty":
        return _uncertainty_recommend(chapter_vs, untrained, labels, profile, n_recommend)
    else:  # phased
        return _phased_recommend(chapter_vs, untrained, labels, k, profile, n_recommend)


def _phased_recommend(
    chapter_vs: list[dict],
    untrained: list[int],
    labels: list[int],
    k: int,
    profile: dict,
    n_recommend: int,
) -> list[dict[str, Any]]:
    """阶段式推荐。

    Phase 1：每簇选 1 个中心章节（覆盖所有风格）
    Phase 2：每簇选更多样本（巩固各风格）
    Phase 3：边缘样本（泛化测试）
    """
    # 统计每簇已训练数量
    trained_per_cluster = [0] * k
    # trained_indices 已被过滤，这里从外部传进来的 chapter_vs 里不带训练状态
    # 改用 untrained 和 labels 来推断

    result = []
    phase_1_picks = []
    phase_2_picks = []
    phase_3_picks = []

    # Phase 1: 每簇选最靠近中心的未训练章节
    for j in range(k):
        cluster_untrained = [(i, _dist_to_centroid(i, j, chapter_vs, profile))
                             for i in untrained if labels[i] == j]
        if cluster_untrained:
            cluster_untrained.sort(key=lambda x: x[1])
            idx, dist = cluster_untrained[0]
            phase_1_picks.append({
                "chapter_index": idx,
                "chapter_title": chapter_vs[idx].get("chapter_title", ""),
                "reason": "覆盖风格",
                "phase": 1,
                "cluster_id": j,
                "distance_to_centroid": round(dist, 4),
            })

    # 如果 Phase 1 就够了，直接返回
    result.extend(phase_1_picks)
    if len(result) >= n_recommend:
        return result[:n_recommend]

    # Phase 2: 各簇次中心样本
    for j in range(k):
        cluster_untrained = [(i, _dist_to_centroid(i, j, chapter_vs, profile))
                             for i in untrained if labels[i] == j
                             and not any(p["chapter_index"] == i for p in phase_1_picks)]
        if cluster_untrained:
            cluster_untrained.sort(key=lambda x: x[1])
            # 每簇加 1-2 个
            n_add = min(2, len(cluster_untrained))
            for idx, dist in cluster_untrained[:n_add]:
                phase_2_picks.append({
                    "chapter_index": idx,
                    "chapter_title": chapter_vs[idx].get("chapter_title", ""),
                    "reason": "巩固风格",
                    "phase": 2,
                    "cluster_id": j,
                    "distance_to_centroid": round(dist, 4),
                })

    result.extend(phase_2_picks)
    if len(result) >= n_recommend:
        return result[:n_recommend]

    # Phase 3: 边缘样本（离质心远的）
    edge_candidates = []
    for i in untrained:
        if any(p["chapter_index"] == i for p in result):
            continue
        j = labels[i]
        dist = _dist_to_centroid(i, j, chapter_vs, profile)
        edge_candidates.append((i, dist, j))
    edge_candidates.sort(key=lambda x: -x[1])  # 越远越优先

    for idx, dist, j in edge_candidates:
        if len(result) >= n_recommend:
            break
        phase_3_picks.append({
            "chapter_index": idx,
            "chapter_title": chapter_vs[idx].get("chapter_title", ""),
            "reason": "泛化测试",
            "phase": 3,
            "cluster_id": j,
            "distance_to_centroid": round(dist, 4),
        })

    result.extend(phase_3_picks)
    return result[:n_recommend]


def _diversity_recommend(
    chapter_vs: list[dict],
    untrained: list[int],
    labels: list[int],
    k: int,
    profile: dict,
    n_recommend: int,
) -> list[dict[str, Any]]:
    """多样性优先：每簇均匀分配推荐名额。"""
    cluster_untrained: dict[int, list[int]] = {j: [] for j in range(k)}
    for i in untrained:
        cluster_untrained[labels[i]].append(i)

    # 按簇大小比例分配
    total_untrained = len(untrained)
    result = []
    per_cluster_quota = {}
    for j in range(k):
        ratio = len(cluster_untrained[j]) / max(1, total_untrained)
        per_cluster_quota[j] = max(1, int(ratio * n_recommend))

    # 每簇按离质心的距离（由近到远）取 quota 个
    for j in range(k):
        indices = cluster_untrained[j]
        indices.sort(key=lambda i: _dist_to_centroid(i, j, chapter_vs, profile))
        for idx in indices[:per_cluster_quota[j]]:
            result.append({
                "chapter_index": idx,
                "chapter_title": chapter_vs[idx].get("chapter_title", ""),
                "reason": "多样性覆盖",
                "phase": 1,
                "cluster_id": j,
                "distance_to_centroid": round(_dist_to_centroid(idx, j, chapter_vs, profile), 4),
            })

    result.sort(key=lambda x: (x["cluster_id"], x["distance_to_centroid"]))
    return result[:n_recommend]


def _uncertainty_recommend(
    chapter_vs: list[dict],
    untrained: list[int],
    labels: list[int],
    profile: dict,
    n_recommend: int,
) -> list[dict[str, Any]]:
    """不确定优先：选离质心最远的（最难分类、最有信息增益）。"""
    candidates = []
    for i in untrained:
        j = labels[i]
        dist = _dist_to_centroid(i, j, chapter_vs, profile)
        candidates.append((i, dist, j))

    candidates.sort(key=lambda x: -x[1])  # 越远越先

    result = []
    for idx, dist, j in candidates[:n_recommend]:
        result.append({
            "chapter_index": idx,
            "chapter_title": chapter_vs[idx].get("chapter_title", ""),
            "reason": "边界样本（信息增益大）",
            "phase": 3,
            "cluster_id": j,
            "distance_to_centroid": round(dist, 4),
        })
    return result


def _dist_to_centroid(chapter_idx: int, cluster_id: int, chapter_vs: list[dict], profile: dict) -> float:
    """计算某章节到指定簇质心的距离（归一化空间中）。"""
    # 用原始 V 向量距离
    v = chapter_vs[chapter_idx]["vector"]
    centroids = profile["centroids"]
    if cluster_id >= len(centroids):
        return float("inf")
    return _euclidean(v, centroids[cluster_id])
