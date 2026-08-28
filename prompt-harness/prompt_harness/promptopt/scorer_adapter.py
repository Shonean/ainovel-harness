# -*- coding: utf-8 -*-
"""统一评分入口（PromptOpt Phase 0）。

所有 promptopt 训练/验证只走 ``score()`` 这一个口径，杜绝各模块自算分导致的
口径漂移（fact_recall 引号 bug 那类问题）。内部复用现有生产评分函数，不重写算法：

- fact       ← key_details.factual_consistency_score（锚点/关键细节保留）
- char       ← scorer.char_level_similarity（原文字符 n-gram 相似度，s_char）
- plot       ← plot_similarity.plot_similarity（剧情点覆盖率，异步/embedding）
- syn        ← 生成 vs 原文的 embedding 余弦（句式/风格 proxy，失败回退 1.0）
- flavor     ← ai_flavor.review_ai_flavor 的 score（1=干净，异步/LLM）
- completion ← completion_audit.audit_l5 的 ok_high（无 high blocker=1）
- composite  ← structural_analyzer.compute_composite_score_v5 的 v519 模式
               （0.5 char + 0.3 plot + 0.2 syn，再乘 AI 味惩罚），与生产排序口径一致

设计：
- async 统一接口（plot/flavor 本身异步）。批量用 ``score_many``。
- 所有维度 0~1，越大越好；失败的维度记 error 并按 0 处理，但不抛断整个评分。
- ``run_flavor_review=False`` 可关掉 LLM 审阅做快速冒烟（flavor 返回 None，composite 不惩罚）。
- scene/kd/target_len 透传给 completion_audit 做锚点/对白/字数校验。
"""
from __future__ import annotations

import asyncio
from typing import Any

from .. import scorer as _scorer
from .. import scorer_params as _sp
from ..completion_audit import audit_l5
from ..structural_analyzer import compute_composite_score_v5


async def _plot_score(generated: str, reference: str) -> float:
    try:
        from ..plot_similarity import plot_similarity
        res = await plot_similarity(generated, reference)
        return float(res.get("plot") or 0.0)
    except Exception as e:  # noqa: BLE001
        return 0.0


def _char_score(generated: str, reference: str) -> float:
    try:
        return float(_scorer.char_level_similarity(generated, reference))
    except Exception:  # noqa: BLE001
        return 0.0


def _fact_score(generated: str, reference: str, kd: dict | None) -> float:
    try:
        res = _scorer.factual_consistency_score(generated, reference, key_details=kd)
        return float(res.get("score") or 0.0)
    except Exception:  # noqa: BLE001
        return 0.0


async def _syn_score(generated: str, reference: str) -> float:
    """句式/风格相似度 proxy：生成 vs 原文 embedding 余弦。失败回退 1.0（中性）。"""
    try:
        from ..embed_client import get_embedding, cosine_similarity
        eg = await get_embedding(generated)
        er = await get_embedding(reference)
        return float(cosine_similarity(eg, er))
    except Exception:  # noqa: BLE001
        return 1.0


async def _flavor_score(generated: str, reference: str) -> tuple[float | None, dict | None]:
    try:
        from ..ai_flavor import review_ai_flavor
        res = await review_ai_flavor(generated, reference)
        if res.get("error"):
            return None, res
        return float(res.get("score") if res.get("score") is not None else 1.0), res
    except Exception as e:  # noqa: BLE001
        return None, {"error": str(e)}


def _completion_score(
    generated: str, scene: dict | None, kd: dict | None, target_len: int
) -> tuple[float, dict]:
    try:
        audit = audit_l5(generated, scene=scene, kd=kd, target_len=target_len)
        # 无 high blocker 视为达标；medium/low 不阻断但透出
        return (1.0 if audit.get("ok_high") else 0.0), audit
    except Exception as e:  # noqa: BLE001
        return 0.0, {"error": str(e)}


async def score(
    generated: str,
    reference: str,
    scene: dict[str, Any] | None = None,
    *,
    kd: dict[str, Any] | None = None,
    target_len: int = 0,
    run_flavor_review: bool = True,
) -> dict[str, Any]:
    """对单段生成正文打分。

    Returns
    -------
    dict with keys:
        fact, char, plot, syn, flavor, completion, composite （0~1）
        audit / flavor_detail ：原始诊断 dict
        errors：失败的维度列表
    composite 用生产 v519 公式（权重经 scorer_params，Phase 2 可训练）。
    """
    generated = generated or ""
    reference = reference or ""
    errors: list[str] = []

    # 可并行的异步维度
    plot_task = asyncio.create_task(_plot_score(generated, reference))
    syn_task = asyncio.create_task(_syn_score(generated, reference))
    flavor_task = None
    if run_flavor_review:
        flavor_task = asyncio.create_task(_flavor_score(generated, reference))

    # 同步维度
    fact = _fact_score(generated, reference, kd)
    char = _char_score(generated, reference)

    plot = await plot_task
    syn = await syn_task
    flavor: float | None = None
    flavor_detail: dict | None = None
    if flavor_task is not None:
        flavor, flavor_detail = await flavor_task
        if flavor is None:
            errors.append("flavor")
            flavor_for_comp = None
        else:
            flavor_for_comp = flavor
    else:
        flavor_for_comp = None

    completion, audit = _completion_score(generated, scene, kd, target_len)

    composite = compute_composite_score_v5(
        v_cosine=syn,
        mean_abs_delta_pct=0.0,
        s_char=char,
        plot_sim=plot,
        ai_flavor=flavor_for_comp,
    )

    return {
        "fact": round(fact, 4),
        "char": round(char, 4),
        "plot": round(plot, 4),
        "syn": round(syn, 4),
        "flavor": round(flavor, 4) if flavor is not None else None,
        "completion": round(completion, 4),
        "composite": round(composite, 4),
        "audit": audit,
        "flavor_detail": flavor_detail,
        "errors": errors,
    }


async def score_many(
    items: list[dict[str, Any]],
    *,
    run_flavor_review: bool = True,
    concurrency: int = 4,
) -> list[dict[str, Any]]:
    """批量评分。每个 item 需含 generated/reference，可选 scene/kd/target_len。

    用信号量限并发，避免一次性打爆 embedding/LLM。
    """
    sem = asyncio.Semaphore(concurrency)

    async def _one(it: dict[str, Any]) -> dict[str, Any]:
        async with sem:
            res = await score(
                it.get("generated", ""),
                it.get("reference", ""),
                scene=it.get("scene"),
                kd=it.get("kd"),
                target_len=int(it.get("target_len") or 0),
                run_flavor_review=run_flavor_review,
            )
            res["id"] = it.get("id")
            return res

    return await asyncio.gather(*[_one(it) for it in items])
