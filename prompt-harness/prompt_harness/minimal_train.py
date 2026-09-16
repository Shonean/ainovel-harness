# -*- coding: utf-8 -*-
"""v5.29 极简推导训练：弧线级「压缩前沿探索」。

链路（单元 = 弧线/剧情，以剧情库划分为准）：
  极简 M（LLM 把该弧线原章节压缩成最短而充分的极简剧情）
    → 弧线树状推导（n 步）：
        弧线层挂原型 + 注入真实切法 → 各章 core → 逐章 3-5 场景 × 6 类叶子
    → 每弧线分别填模板 → 每章 P_arc_ci（模板只含该章内容）+ user_input（推导内容，诚实）
    → 生成正文 G_i → 逐章复现评分 vs 原章节 X_i（v519 综合分 + AI 味惩罚），取均值

训练 = 弧线压缩前沿探索：扫字数预算档，找「最短而充分」的 M
（综合分 ≥ threshold_ratio × 最高档均分），失败样本反哺 → LLM 归纳合格要素清单
→ 改进生成器 prompt（写回 fixed_prompts.minimal_plot_generator）。

诚实性（双重保障，不硬依赖 LLM 提炼模板）：
  - 主路径：T_style = 确定性 strip 掉候选 rendered_prompt 的【原文句参考】区块
    （风格引擎，无原文逐字泄漏），推导内容走 user_input；
  - 增强路径：template_store._refine_template 提炼 skeleton+slots，填 {{key}} 槽
    产出每弧线标准模板；失败回退 T_style 路径并标注 mode。

⚠️ 本模块全部 SETTINGS 引用都在函数内 `from .config import SETTINGS as _s`
（顶层 import 会捕获 init_settings 重绑前的旧对象）。
"""
from __future__ import annotations

import asyncio
import json
import random
import re
import time
from pathlib import Path
from typing import Any, Callable

from .derive import _expand_arc_scenes_llm, _real_arcs_by_label, _tree_llm, render_prompt
from .fixed_prompts import get_fixed_prompts, update_fixed_prompts
from .llm_client import chat_completion
from .plot_skeleton import skeleton_to_generation_prompt
from .run_records import read_run

# ═══════════════════════════ 常量 ═══════════════════════════

_DEFAULT_MINIMAL_PROMPT = (
    "你是「极简剧情压缩专家」。把给定章节原文压缩成尽可能短的一句话极简剧情（不超过预算字数）。\n"
    "必须保留：①人物名+身份/势力归属；②时空地点；③核心冲突；④关键事件序列（≥3 拍，含转折）；"
    "⑤关键对白锚点；⑥结局/钩子。\n"
    "用大白话、不修饰、不用省略号；越短越好，但不得为压缩而丢弃以上要素。\n"
    "【v5.30】禁用小说腔：不堆氛围词/形容词（凉意、夜色、缓缓、微微等），不写心理描写，"
    "只报事实与动作，像给编辑写故事梗概。"
)

# 确定性 strip：从候选 rendered_prompt 去掉【原文句参考】区块（该标题到下个区块标题/结尾）。
_T_STYLE_STRIP = re.compile(r"【原文句参考】.*?(?=【[^】]{1,30}】|$)", re.S)

_LEAF_TEXT_KEYS = ("details", "conflicts", "dialogues")  # 推导树场景叶子（list 类）

_DEFAULT_GEN_PARAMS = {
    "temperature": 0.3,
    "top_p": 0.7,
    "presence_penalty": 0.1,
    "frequency_penalty": 0.1,
    "max_tokens": 4096,
}


def _default_budgets() -> list[int]:
    """预算档：config.minimal_train_budgets（env MINIMAL_TRAIN_BUDGETS，默认 15,30,50,80,120）。"""
    from .config import SETTINGS as _s
    raw = getattr(_s, "minimal_train_budgets", "15,30,50,80,120")
    out: list[int] = []
    for part in str(raw).split(","):
        part = part.strip()
        if part.lstrip("-").isdigit():
            out.append(int(part))
    return sorted(set(out)) or [30, 80]


def _output_dir() -> Path:
    p = Path(__file__).resolve().parent.parent / "harness_runs" / "minimal_train"
    p.mkdir(parents=True, exist_ok=True)
    return p


# ═══════════════════════════ 输入解析 ═══════════════════════════

def run_chapter_offset(run_file: str, run: dict[str, Any] | None = None) -> int:
    """从 run 文件名/源文件名解析该 run 覆盖的起始章号（默认 1）。

    「青山1-10章」→ 1；「青山501-815章」→ 501。解析失败默认 1。
    """
    run = run or {}
    src = (run_file or "") + "|" + str(run.get("source_file") or "")
    m = re.search(r"(\d+)\s*[-~至]\s*\d+\s*章", src)
    if m:
        return int(m.group(1))
    return 1


def resolve_arc_chapters(
    run: dict[str, Any],
    run_start: int,
    start_chapter: int,
    end_chapter: int,
) -> tuple[int, int]:
    """把弧线的「原书第 m-n 章」映射到 run.target_texts 的索引区间 [lo, hi]（闭区间）。

    弧线章号是原书绝对章号；run 覆盖 [run_start, run_start+len-1]。越界部分被 clamp；
    完全不重叠（lo>hi）抛 ValueError。
    """
    targets = run.get("target_texts") or []
    if not targets:
        raise ValueError("run 没有 target_texts（非多章 run，无法做弧线训练）")
    lo = max(0, int(start_chapter) - run_start)
    hi = min(len(targets) - 1, int(end_chapter) - run_start)
    if lo > hi:
        raise ValueError(
            f"弧线第 {start_chapter}-{end_chapter} 章超出 run 覆盖范围"
            f"（run 起始 {run_start}，共 {len(targets)} 章）"
        )
    return lo, hi


def _get_minimal_prompt() -> str:
    d = get_fixed_prompts()
    p = (d.get("minimal_plot_generator") or "").strip()
    return p or _DEFAULT_MINIMAL_PROMPT


# ═══════════════════════════ ① 生成极简 ═══════════════════════════

async def generate_minimal(
    arc_text: str,
    max_chars: int = 80,
    style: str = "",
    role_setting: str = "",
    variant: int = 0,
) -> dict[str, Any]:
    """把弧线原章节压缩成 ≤ max_chars 的极简剧情（生成器 prompt 从 fixed_prompts 读）。

    variant 控制温度抖动，同预算可随机多条。返回 {minimal, actual_len, budget, variant}。
    """
    src = (arc_text or "").strip()
    if not src:
        return {"minimal": "", "actual_len": 0, "budget": max_chars, "variant": variant, "error": "empty_arc"}
    if len(src) > 6000:
        src = src[:6000]
    user = (
        (("风格基调：" + style.strip()) if (style or "").strip() else "")
        + (("\n角色设定：" + role_setting.strip()) if (role_setting or "").strip() else "")
        + f"\n【原章节】\n{src}\n"
        + f"\n【任务】把以上章节压缩成不超过 {max_chars} 个字的一句话极简剧情，"
          "要完整包含剧情要素，越短越好。直接输出极简剧情本身，不要解释、不要引号。"
    )
    temps = [0.45 + variant * 0.12, 0.7]
    text = ""
    for i, temp in enumerate(temps):
        resp = await chat_completion(
            system=_get_minimal_prompt(),
            user=user,
            temperature=temp,
            top_p=0.7,
            max_tokens=max(256, max_chars * 3 + 64),
            call_type="minimal_gen",
        )
        content = (resp.get("content") or "").strip()
        if content and not resp.get("error"):
            text = content
            break
    text = re.sub(r"^【极简剧情】[:：]?\s*", "", text.strip())
    text = text.strip().strip("「」\"'")
    return {"minimal": text, "actual_len": len(text), "budget": max_chars, "variant": variant}


# ═══════════════════════════ ② 推导：极简 → 单弧线树 ═══════════════════════════

async def expand_arc_from_minimal(
    minimal: str,
    archetype: str = "",
    parent: str = "",
    real_arc: dict[str, Any] | None = None,
    style: str = "",
    role_setting: str = "",
    n_chapters: int | None = None,
) -> dict[str, Any]:
    """极简 M → 单弧线剧情树（弧线层挂原型 + 各章 core + 逐章场景×6叶子）。

    第 1 步：单弧线 LLM（小 JSON 稳定）；第 2 步：逐章展开场景叶子（复用 derive，
    单章失败降级该章 scenes=[]）。第 1 步失败降级最小弧线树（不整体失败）。

    返回树形：{"root": {"style", "role_setting", "arcs": [{name, archetype, parent,
    chapters: [{title, core, scenes:[...]}]}]}}
    """
    from .arc_classify import _load_registry
    from .config import SETTINGS as _s

    # 注入真实切法/定义（同 expand_plot_tree 的 ref_block）
    ref_lines: list[str] = []
    if archetype:
        reg = _load_registry(_s.data_dir / "archetypes.json")
        meta: dict[str, Any] = {}
        for a in reg:
            if isinstance(a, dict) and a.get("name") == archetype:
                meta = a
                break
        line = (
            f"- {archetype}（{parent or meta.get('parent') or '未挂靠'}）："
            f"{meta.get('definition') or '（无定义）'}"
        )
        if real_arc and isinstance(real_arc, dict):
            real = real_arc
        else:
            real_list = _real_arcs_by_label(archetype)
            real = real_list[0] if real_list else None
        if real:
            line += f"　真实切法：第{real.get('chapters', 'm-n')}章={real.get('description', '')}"
        else:
            beats = (meta.get("beats") or [])[:5]
            if beats:
                line += "　拍示例：" + "、".join(str(b) for b in beats)
        ref_lines.append(line)
    ref_block = "\n".join(ref_lines) if ref_lines else "（未命中剧情库原型，按小说常规套路组织弧线）"

    if n_chapters:
        _nc_hint = f"必须拆成正好 {n_chapters} 章（按时序推进，每章对应一段剧情）。"
    else:
        _nc_hint = "拆成 2-6 章（按时序推进）。"

    system = (
        "你是「剧情库驱动的剧情弧线拆解专家」。给定极简剧情与命中的剧情库原型，"
        "把这条弧线按时序拆成若干章，每章给一句话核心。只输出单条弧线，JSON 要小、完整。"
    )
    user = (
        "极简剧情：\n" + (minimal or "").strip()
        + (("\n风格基调：" + style.strip()) if (style or "").strip() else "")
        + (("\n角色设定：" + role_setting.strip()) if (role_setting or "").strip() else "")
        + "\n\n命中的剧情库原型及真实切法参考：\n" + ref_block
        + f"\n\n请把这条弧线{_nc_hint}每章只写 title 和 core（一句话核心，具体可写，"
          "不是抽象概念）。输出必须是一份完整合法的 JSON，不要省略号/截断。\n"
          "严格输出如下 JSON（不要 Markdown 代码块）：\n"
          '{"name": "弧线标题", "chapters": [{"title": "第1章 …", "core": "…"}]}'
    )
    try:
        data = await _tree_llm(system, user, "minimal_expand_arc", max_tokens=4000)
    except Exception:
        data = None
    if not isinstance(data, dict):
        data = {}
    name = str(data.get("name") or archetype or "剧情弧线").strip() or "剧情弧线"
    chapters_in = [c for c in (data.get("chapters") or []) if isinstance(c, dict)]
    if not chapters_in:
        chapters_in = [{"title": "第1章", "core": (minimal or "")[:60]}]
    arc: dict[str, Any] = {
        "name": name, "archetype": archetype, "parent": parent, "chapters": chapters_in,
    }
    # 第 2 步：逐章展开场景叶子（失败降级该章 scenes=[]）
    chaps = await _expand_arc_scenes_llm(arc, style, role_setting)
    if chaps:
        arc["chapters"] = chaps
    return {"root": {"style": style, "role_setting": role_setting, "arcs": [arc]}}


def _single_arc(tree: dict[str, Any]) -> dict[str, Any] | None:
    root = tree.get("root") or {}
    arcs = [a for a in (root.get("arcs") or []) if isinstance(a, dict)]
    return arcs[0] if arcs else None


def _single_chapter(tree: dict[str, Any], chapter_idx: int) -> dict[str, Any] | None:
    """取单弧线树的第 chapter_idx 章 dict（title/core/scenes），不存在返回 None。"""
    arc = _single_arc(tree)
    if arc is None:
        return None
    chs = [c for c in (arc.get("chapters") or []) if isinstance(c, dict)]
    if chapter_idx < 0 or chapter_idx >= len(chs):
        return None
    return chs[chapter_idx]


# ═══════════════════════════ ③ 每弧线/每章模板 ═══════════════════════════

def render_arc_template(template: Any, tree: dict[str, Any]) -> dict[str, Any]:
    """把单弧线树填入模板，产出该弧线标准模板 P_arc。

    template: 有 skeleton+slots 的模板 dict → render_prompt 填槽；
              string（T_style 兜底）→ 原样作为完整 prompt；None → 空。
    """
    if template is None:
        return {"prompt": "", "slots_filled": [], "slots_missing": [], "mode": "none"}
    if isinstance(template, dict) and template.get("skeleton"):
        r = render_prompt(template, tree)
        r["mode"] = "template"
        return r
    return {"prompt": str(template), "slots_filled": [], "slots_missing": [], "mode": "tstyle"}


def _single_chapter_subtree(tree: dict[str, Any], chapter_idx: int) -> dict[str, Any]:
    """取单弧线树的第 chapter_idx 章 → 单章子树（模板只填该章内容）。"""
    import copy
    root = tree.get("root") or {}
    arcs = [a for a in (root.get("arcs") or []) if isinstance(a, dict)]
    if not arcs:
        return tree
    arc = copy.deepcopy(arcs[0])
    chs = [c for c in (arc.get("chapters") or []) if isinstance(c, dict)]
    if chapter_idx < 0 or chapter_idx >= len(chs):
        return tree
    arc["chapters"] = [chs[chapter_idx]]
    return {
        "root": {
            "style": root.get("style", ""),
            "role_setting": root.get("role_setting", ""),
            "arcs": [arc],
        }
    }


def render_chapter_template(template: Any, tree: dict[str, Any], chapter_idx: int) -> dict[str, Any]:
    """每章独立模板：只用该章树内容填槽（生成第 ci 章只看第 ci 章内容）。"""
    return render_arc_template(template, _single_chapter_subtree(tree, chapter_idx))


# ═══════════════════════════ ④ 生成输入（诚实，非原文） ═══════════════════════════

def _chapter_skeleton_text(tree: dict[str, Any], chapter_idx: int) -> str:
    """从推导树该章内容构造骨架文本（诚实：全部来自推导，不含原文逐字）。"""
    root = tree.get("root") or {}
    arc = _single_arc(tree)
    if arc is None:
        return ""
    chs = [c for c in (arc.get("chapters") or []) if isinstance(c, dict)]
    if chapter_idx < 0 or chapter_idx >= len(chs):
        return ""
    ch = chs[chapter_idx]
    lines: list[str] = []
    style = str(root.get("style") or "").strip()
    if style:
        lines.append(f"【基调】{style}")
    lines.append(f"【第{chapter_idx + 1}章】{ch.get('title', '')}")
    lines.append(str(ch.get("core") or ""))
    for sc in ch.get("scenes") or []:
        if not isinstance(sc, dict):
            continue
        name = str(sc.get("name") or "").strip() or "场景"
        beats: list[str] = []
        for k in ("conflicts", "actions", "dialogues"):
            v = sc.get(k)
            if isinstance(v, list) and v:
                beats.append(str(v[0]))
        b = "；".join(x for x in beats if x)
        lines.append(f"- {name}" + (f"：{b}" if b else ""))
    return "\n".join(x for x in lines if x)


def _derived_details_block(tree: dict[str, Any], chapter_idx: int) -> str | None:
    """从推导树该章的叶子（细节/冲突/对话）构造关键细节锚点（诚实，非原文）。"""
    arc = _single_arc(tree)
    if arc is None:
        return None
    chs = [c for c in (arc.get("chapters") or []) if isinstance(c, dict)]
    if chapter_idx < 0 or chapter_idx >= len(chs):
        return None
    ch = chs[chapter_idx]
    details: list[str] = []
    conflicts: list[str] = []
    for sc in ch.get("scenes") or []:
        if not isinstance(sc, dict):
            continue
        for k, bucket in (("details", details), ("conflicts", conflicts)):
            v = sc.get(k)
            if isinstance(v, list):
                bucket.extend(str(x) for x in v if str(x).strip())
    lines: list[str] = []
    if details:
        lines.append("【关键细节】")
        lines.extend("· " + d for d in details)
    if conflicts:
        lines.append("【冲突推进】")
        lines.extend("· " + c for c in conflicts)
    return "\n".join(lines) if lines else None


def _derived_dialogue_contract(tree: dict[str, Any], chapter_idx: int) -> str:
    """该章推导对白按序编号 → 对白顺序契约块（诚实，非原文）。"""
    arc = _single_arc(tree)
    if arc is None:
        return ""
    chs = [c for c in (arc.get("chapters") or []) if isinstance(c, dict)]
    if chapter_idx < 0 or chapter_idx >= len(chs):
        return ""
    turns: list[str] = []
    for sc in chs[chapter_idx].get("scenes") or []:
        if not isinstance(sc, dict):
            continue
        v = sc.get("dialogues")
        if isinstance(v, list):
            # 【v5.31】树对白统一 “” 引号（不可变原则）
            turns.extend(
                str(x).strip() for x in v if str(x).strip()
            )
    if not turns:
        return ""
    from .ai_flavor import to_dialogue_quotes
    numbered = "；".join(f"{i}.{to_dialogue_quotes(t)}" for i, t in enumerate(turns, 1))
    return (
        f"【对白顺序契约】按序编号复现以下对白轮次（不得提前/延后/遗漏）：{numbered}"
    )


def tree_to_generation_input(tree: dict[str, Any], chapter_idx: int) -> dict[str, Any]:
    """从推导树该章内容构造诚实生成指令（不含原文任何逐字内容）。

    返回 {user_input, skeleton_text, details_block, contract}。
    """
    skeleton = _chapter_skeleton_text(tree, chapter_idx)
    details = _derived_details_block(tree, chapter_idx)
    contract = _derived_dialogue_contract(tree, chapter_idx)
    # 【v5.30】推导路径无原文基准 → 用独立版 AI 味禁令块（去掉「原文」对照语义）
    from .ai_flavor import ai_flavor_ban_block
    user_input = skeleton_to_generation_prompt(
        skeleton, chapter_section="main", key_details_block=details,
        ban_block=ai_flavor_ban_block(standalone=True),
    )
    if contract:
        user_input = user_input.rstrip() + "\n\n" + contract
    user_input += (
        "\n\n【骨架忠实度】逐拍复现：每个拍点都必须写到，拍点顺序不得改变，"
        "不得省略拍点，不得增加骨架外的主要情节。"
    )
    return {
        "user_input": user_input,
        "skeleton_text": skeleton,
        "details_block": details,
        "contract": contract,
    }


# ═══════════════════════════ ⑤ 逐章复现评分（复刻 _evaluate_p v519 评分块） ═══════════════════════════

async def score_generated(
    generated: str,
    target_text: str,
    skeleton_text: str,
    ai_flavor: bool = True,
    ai_flavor_result: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """逐章复现评分：0.5×s_char + 0.3×plot_sim + 0.2×v_cos + AI 味惩罚。

    与生产 `_evaluate_p` 的 v519 评分块使用同一组函数（非重写算法），
    target↔target 自校准应 ≥0.85。ai_flavor=False 跳过 LLM 审阅（快速冒烟）。

    【v5.30】ai_flavor_result：外部已跑的审阅结果（如 ai_flavor_regenerate_loop 的
    最终审阅），复用其 score/error，避免审阅二次 LLM 花费。为 None 时内部审阅。
    """
    from .ai_flavor import review_ai_flavor
    from .config import SETTINGS as _s
    from .key_details import factual_consistency_score, get_key_details
    from .plot_fidelity import beat_sequence_fidelity
    from .plot_similarity import plot_similarity
    from .scorer import (
        char_level_similarity, char_ngram_containment, compression_ratio,
        fluency_score, function_word_kl, length_alignment_score,
        narrative_cohesion, semantic_coverage,
    )
    from .structural_analyzer import (
        ai_divergence_score, compute_composite_score_v5, compute_structural_similarity,
        content_quality_score, defensive_writing_score, dialogue_gap_bloat,
        dialogue_quality_score, ending_action_density, extract_structural_vector,
        serialization_fit_score,
    )

    if not generated:
        return {
            "score": 0.0, "s_char": 0.0, "plot_sim": 0.0, "turn_fidelity": 0.0,
            "v_cos": 0.0, "ai_flavor": None, "error": "empty_generation",
        }

    v_prime = extract_structural_vector(generated)
    target_v = extract_structural_vector(target_text or "")
    tvec = target_v.get("vector") if isinstance(target_v, dict) else None
    pvec = v_prime.get("vector") if isinstance(v_prime, dict) else None
    if not tvec or not pvec:
        return {
            "score": 0.0, "s_char": 0.0, "plot_sim": 0.0, "turn_fidelity": 0.0,
            "v_cos": 0.0, "ai_flavor": None, "error": "missing_vector",
        }

    v_cos = compute_structural_similarity(tvec, pvec)
    deltas_pct: list[float] = []
    for tv, gv in zip(tvec, pvec):
        if abs(tv) > 1e-6:
            deltas_pct.append(abs(tv - gv) / abs(tv) * 100)
        else:
            deltas_pct.append(0.0)
    mean_abs_delta_pct = float(sum(deltas_pct) / len(deltas_pct)) if deltas_pct else 0.0

    l_score = length_alignment_score(generated, target_text)
    s_score = serialization_fit_score(generated)
    sem_task = asyncio.create_task(semantic_coverage(generated, target_text))
    ps_task = None
    if _s.scoring_v519:
        ps_task = asyncio.create_task(plot_similarity(
            generated, target_text, n_min=_s.v519_n_min, n_max=_s.v519_n_max,
        ))
    flu = fluency_score(generated)
    cr = compression_ratio(generated)
    nc = narrative_cohesion(generated)
    fwkl = function_word_kl(generated, target_text)
    dq = dialogue_quality_score(generated)
    dw = defensive_writing_score(generated)
    adv = ai_divergence_score(generated)
    dg = dialogue_gap_bloat(generated)
    ea = ending_action_density(generated)

    sem_cov = await sem_task
    content_q = content_quality_score(
        semantic_coverage=sem_cov, fluency=flu, compression_ratio=cr,
        narrative_cohesion=nc, function_word_kl=fwkl, dialogue_quality=dq,
        defensive_writing=dw, ai_divergence=adv, dialogue_gap=dg, ending_action=ea,
    )
    kd = get_key_details(target_text)
    fc = factual_consistency_score(generated, target_text, key_details=kd)["score"]
    pf_result = beat_sequence_fidelity(skeleton_text, generated, target_text=target_text)
    pf = pf_result.get("composite", 0.0) if isinstance(pf_result, dict) else 0.0
    s_char_ngram = char_ngram_containment(generated, target_text)
    char_sim = char_level_similarity(target_text or "", generated)

    plot_sim = plot_sim_coverage = turn_fidelity = turn_recall = 0.0
    n_target_turns = n_gen_turns = 0
    if _s.scoring_v519 and ps_task is not None:
        ps = await ps_task
        plot_sim = ps.get("plot", 0.0)
        plot_sim_coverage = ps.get("coverage", 0.0)
        turn_fidelity = ps.get("turn_fidelity", 0.0)
        turn_recall = ps.get("turn_recall", 0.0)
        n_target_turns = ps.get("n_target_turns", 0)
        n_gen_turns = ps.get("n_gen_turns", 0)

    ai_score = None
    ai_err = None
    if ai_flavor_result is not None:
        # 【v5.30】复用外部审阅结果（重生成循环已跑过，不二次花费）
        ai_score = ai_flavor_result.get("score")
        ai_err = ai_flavor_result.get("error")
    elif ai_flavor and _s.ai_flavor_review:
        try:
            res = await review_ai_flavor(generated, target_text)
            if isinstance(res, dict):
                ai_score = res.get("score")
                ai_err = res.get("error")
        except Exception as exc:
            ai_err = str(exc)

    composite = compute_composite_score_v5(
        v_cos, mean_abs_delta_pct, dim_deltas_pct=deltas_pct,
        length_alignment=l_score, serialization_fit=s_score,
        content_quality=content_q, semantic_coverage=sem_cov,
        factual_consistency=fc, plot_fidelity=pf, s_char=s_char_ngram,
        plot_sim=plot_sim, ai_flavor=ai_score,
    )
    return {
        "score": composite,
        "s_char": s_char_ngram,
        "plot_sim": plot_sim,
        "plot_sim_coverage": plot_sim_coverage,
        "turn_fidelity": turn_fidelity,
        "turn_recall": turn_recall,
        "n_target_turns": n_target_turns,
        "n_gen_turns": n_gen_turns,
        "v_cos": v_cos,
        "v_cosine": v_cos,
        "char_similarity": char_sim,
        "ai_flavor": ai_score,
        "ai_flavor_error": ai_err,
        "semantic_coverage": sem_cov,
        "length_alignment": l_score,
        "serialization_fit": s_score,
        "content_quality": content_q,
        "factual_consistency": fc,
        "plot_fidelity": pf,
        "generated_len": len(generated),
        "scoring": "v519" if _s.scoring_v519 else "v5.18",
    }


# ═══════════════════════════ 模板解析（自动提炼 / T_style 兜底） ═══════════════════════════

def _tpl_cache_path() -> Path:
    from .config import SETTINGS as _s
    return _s.data_dir / "minimal_train_templates.json"


def _load_tpl_cache() -> dict[str, Any]:
    try:
        return json.loads(_tpl_cache_path().read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_tpl_cache(c: dict[str, Any]) -> None:
    try:
        _tpl_cache_path().write_text(json.dumps(c, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass


async def auto_refine_template(run_file: str, candidate_idx: int) -> dict[str, Any] | None:
    """从 run 候选自动提炼模板（skeleton+slots），按 (run_file:cand_idx) 缓存。

    失败返回 None（调用方回退 T_style 确定性 strip）。
    """
    key = f"{run_file}:{candidate_idx}"
    cache = _load_tpl_cache()
    if cache.get(key):
        return cache[key]
    run = read_run(run_file)
    if run is None:
        return None
    cands = run.get("candidates") or []
    if candidate_idx < 0 or candidate_idx >= len(cands):
        return None
    cand = cands[candidate_idx]
    from .template_store import _refine_template
    refined = await _refine_template([
        (str(cand.get("rendered_prompt") or ""), str(cand.get("user_input") or "")),
    ])
    if refined is None:
        return None
    skeleton, slots = refined
    tpl: dict[str, Any] = {
        "skeleton": skeleton,
        "slots": slots,
        "source_run": run_file,
        "source_candidate_idx": candidate_idx,
    }
    cache[key] = tpl
    _save_tpl_cache(cache)
    return tpl


def t_style_from_candidate(run_file: str, candidate_idx: int) -> str | None:
    """确定性 strip：去掉候选 rendered_prompt 的【原文句参考】区块 → T_style（无泄漏）。

    训练链路诚实主路径（不依赖 LLM 提炼模板）。
    """
    run = read_run(run_file)
    if run is None:
        return None
    cands = run.get("candidates") or []
    if candidate_idx < 0 or candidate_idx >= len(cands):
        return None
    rp = str(cands[candidate_idx].get("rendered_prompt") or "")
    if not rp:
        return None
    stripped = _T_STYLE_STRIP.sub("", rp).strip()
    return stripped or None


# ═══════════════════════════ ⑥ 压缩前沿训练 ═══════════════════════════

async def train_arc_frontier(
    run_file: str,
    candidate_idx: int,
    arc: dict[str, Any],
    template: Any = None,
    budgets: list[int] | None = None,
    samples: int = 2,
    threshold_ratio: float = 0.9,
    progress: Callable[[dict[str, Any]], None] | None = None,
    ai_flavor: bool = True,
) -> dict[str, Any]:
    """弧线压缩前沿训练：扫预算档找「最短而充分」的极简内容。

    每档每样本：生成 M → 推导单弧线树 → 每章填模板 + 生成正文 → 逐章复现评分取均值。
    返回报告（前沿表 + 最短充分 M + 合格要素 + 改进生成器 prompt），落盘 harness_runs/minimal_train/。
    """
    from .optimizer import forward_generation_v4
    from .ladder import generate_chapter_prose
    from .config import SETTINGS as _s

    run = read_run(run_file)
    if run is None:
        raise ValueError(f"run 不存在：{run_file}")
    targets = run.get("target_texts") or []
    run_start = run_chapter_offset(run_file, run)
    start_ch = int(arc.get("start_chapter") or 1)
    end_ch = int(arc.get("end_chapter") or start_ch)
    lo, hi = resolve_arc_chapters(run, run_start, start_ch, end_ch)
    arc_text = "\n".join(targets[lo:hi + 1])
    style = str(arc.get("style") or "").strip()
    role_setting = str(arc.get("role_setting") or "").strip()
    archetype = str(arc.get("archetype") or "").strip()
    parent = str(arc.get("parent") or "").strip()
    real_arc = arc if arc.get("start_chapter") is not None else None
    n_chapters = end_ch - start_ch + 1

    # 模板解析：显式 → provided；否则自动提炼 → auto_refined；再失败 → T_style → tstyle
    template_mode = "none"
    if isinstance(template, dict) and template.get("skeleton"):
        template_mode = "provided"
    elif isinstance(template, str) and template.strip():
        template_mode = "provided_tstyle"
    else:
        auto = await auto_refine_template(run_file, candidate_idx)
        if auto is not None:
            template = auto
            template_mode = "auto_refined"
        else:
            ts = t_style_from_candidate(run_file, candidate_idx)
            if ts:
                template = ts
                template_mode = "tstyle"
            else:
                template = None

    if budgets is None or not budgets:
        budgets = _default_budgets()
    budgets = sorted(set(budgets))

    frontier: list[dict[str, Any]] = []
    per_sample: list[dict[str, Any]] = []
    total = len(budgets)
    for bi, budget in enumerate(budgets, start=1):
        for s in range(samples):
            mres = await generate_minimal(arc_text, budget, style, role_setting, variant=s)
            minimal = mres["minimal"]
            if not minimal:
                per_sample.append({
                    "budget": budget, "sample": s, "minimal": "", "len": 0,
                    "score": 0.0, "chapters": [], "ok": False, "error": "empty_minimal",
                })
                continue
            tree: dict[str, Any] | None = None
            try:
                tree = await expand_arc_from_minimal(
                    minimal, archetype, parent, real_arc, style, role_setting,
                    n_chapters=n_chapters,
                )
            except Exception:
                tree = None
            ch_scores: list[dict[str, Any]] = []
            if tree is not None:
                arc0 = _single_arc(tree)
                n_tree_ch = len([c for c in (arc0.get("chapters") or []) if isinstance(c, dict)]) if arc0 else 0
                for j in range(min(n_tree_ch, hi - lo + 1)):
                    tgt = targets[lo + j]
                    P = render_chapter_template(template, tree, j)
                    user_in = tree_to_generation_input(tree, j)
                    # 【v5.32】场景级正文生成：逐场景写足字数（替代整章一次性生成）。
                    # 目标字数按原文长/场景数自适应；AI 味对照审阅在 generate_chapter_prose 内
                    # （target_text 提供），评分复用其最终审阅结果不二次 LLM。
                    ch = _single_chapter(tree, j)
                    scenes = [s for s in (ch.get("scenes") or []) if isinstance(s, dict)] if ch else []
                    ai_res = None
                    _ai_retried = 0
                    generated = ""
                    if scenes:
                        per_scene_target = max(300, int(len(tgt) / max(1, len(scenes))))
                        try:
                            gres = await generate_chapter_prose(
                                scenes,
                                chapter_title=str(ch.get("title") or ""),
                                core=str(ch.get("core") or ""),
                                style=style, role_setting=role_setting,
                                system_prompt=P.get("prompt") or "",
                                target_len_per_scene=per_scene_target,
                                gen_params=dict(_DEFAULT_GEN_PARAMS),
                                ai_flavor=ai_flavor,
                                target_text=tgt,
                                retry_cap=max(0, int(getattr(_s, "ai_flavor_retry", 1))),
                            )
                            generated = gres.get("text") or ""
                            _ai_retried = gres.get("retried", 0)
                            ai_res = gres.get("review")
                        except Exception:
                            generated = ""
                    else:
                        # 该章无场景（树降级）→ 回退整章一次性生成 + 原 AI 味循环
                        try:
                            generated = await forward_generation_v4(
                                user_in["user_input"],
                                gen_params=dict(_DEFAULT_GEN_PARAMS),
                                system_prompt=P.get("prompt") or "",
                            )
                        except Exception:
                            generated = ""
                        if ai_flavor and generated:
                            from .ai_flavor import ai_flavor_regenerate_loop
                            try:
                                generated, ai_res, _ai_retried = await ai_flavor_regenerate_loop(
                                    generated=generated,
                                    user_input=user_in["user_input"],
                                    system_prompt=P.get("prompt") or "",
                                    gen_params=dict(_DEFAULT_GEN_PARAMS),
                                    target_text=tgt,
                                    retry_cap=max(0, int(getattr(_s, "ai_flavor_retry", 1))),
                                )
                            except Exception:
                                ai_res = None
                    sc = await score_generated(
                        generated, tgt, user_in["skeleton_text"],
                        ai_flavor=ai_flavor, ai_flavor_result=ai_res,
                    )
                    ch_scores.append({
                        "idx": lo + j,
                        "score": round(sc.get("score", 0.0), 4),
                        "s_char": round(sc.get("s_char", 0.0), 4),
                        "plot_sim": round(sc.get("plot_sim", 0.0), 4),
                        "turn_fidelity": round(sc.get("turn_fidelity", 0.0), 4),
                        "ai_flavor": sc.get("ai_flavor"),
                        "ai_retried": _ai_retried,
                        "generated_len": sc.get("generated_len", 0),
                        "error": sc.get("error"),
                    })
            mean = (sum(c["score"] for c in ch_scores) / len(ch_scores)) if ch_scores else 0.0
            per_sample.append({
                "budget": budget, "sample": s, "minimal": minimal, "len": mres["actual_len"],
                "score": round(mean, 4), "chapters": ch_scores, "ok": False,
            })
        rows = [r for r in per_sample if r["budget"] == budget]
        budget_mean = sum(r["score"] for r in rows) / len(rows) if rows else 0.0

        def _avg(key: str) -> float:
            vals = [c[key] for r in rows for c in r.get("chapters", []) if isinstance(c, dict) and key in c]
            return round(sum(vals) / len(vals), 4) if vals else 0.0

        frontier.append({
            "budget": budget, "samples": len(rows),
            "lens": [r["len"] for r in rows],
            "scores": [r["score"] for r in rows],
            "mean": round(budget_mean, 4),
            "s_char": _avg("s_char"),
            "plot_sim": _avg("plot_sim"),
            "turn_fidelity": _avg("turn_fidelity"),
            "ok": False,
        })
        if progress:
            progress({
                "budget": budget, "index": bi, "total": total,
                "mean": round(budget_mean, 4),
                "message": f"预算 {budget} 字完成，均分 {budget_mean:.3f}",
            })

    # 判定：阈值 = threshold_ratio × 最高档均分（必须整条前沿跑完再判）
    best_mean = max((f["mean"] for f in frontier), default=0.0)
    threshold = round(best_mean * threshold_ratio, 4)
    for f in frontier:
        f["ok"] = best_mean > 0 and f["mean"] >= threshold
    for r in per_sample:
        r["ok"] = best_mean > 0 and r["score"] >= threshold

    shortest: dict[str, Any] | None = None
    for f in frontier:
        if f["ok"]:
            ok_sample = next(
                (r for r in per_sample if r["budget"] == f["budget"] and r["ok"]),
                None,
            )
            shortest = {
                "budget": f["budget"],
                "mean": f["mean"],
                "len": ok_sample["len"] if ok_sample else (f["lens"][0] if f["lens"] else 0),
                "minimal": ok_sample["minimal"] if ok_sample else "",
            }
            break

    spec = await _synthesize_spec(per_sample, frontier, archetype or "该剧情", _get_minimal_prompt())

    report: dict[str, Any] = {
        "run_file": run_file,
        "candidate_idx": candidate_idx,
        "arc": {
            "name": str(arc.get("name") or ""),
            "archetype": archetype, "parent": parent,
            "start_chapter": start_ch, "end_chapter": end_ch,
        },
        "arc_text_len": len(arc_text),
        "template_mode": template_mode,
        "budgets": budgets,
        "samples": samples,
        "threshold_ratio": threshold_ratio,
        "best_mean": best_mean,
        "threshold": threshold,
        "frontier": frontier,
        "shortest_sufficient": shortest,
        "spec": {
            "elements": spec.get("elements", []),
            "reason": spec.get("reason", ""),
            "generator_prompt": spec.get("generator_prompt", ""),
            "applied": False,
        },
        "per_sample": per_sample,
        "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    safe = re.sub(r"[^0-9a-zA-Z一-鿿_-]", "_", f"{archetype or 'arc'}_{start_ch}-{end_ch}")[:60]
    out_path = _output_dir() / f"minimal_train_{safe}_{int(time.time())}.json"
    try:
        out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        report["report_path"] = str(out_path)
    except Exception:
        pass
    return report


async def _synthesize_spec(
    per_sample: list[dict[str, Any]],
    frontier: list[dict[str, Any]],
    arc_label: str,
    current_prompt: str,
) -> dict[str, Any]:
    """LLM 归纳合格要素清单 + 改进生成器 prompt。失败返回空（保留当前 prompt）。"""
    from .llm_client import chat_json

    rows: list[str] = []
    for r in per_sample:
        m = r.get("minimal") or "（空）"
        rows.append(f"预算{r['budget']}字/实际{r['len']}字/复现分{r['score']:.3f}/"
                    f"{'充分' if r.get('ok') else '不足'}：{m}")
    table = "\n".join(rows) if rows else "（无数据）"
    user = (
        f"剧情类型：{arc_label}\n\n压缩前沿样本（越短越充分越好，找「最短而充分」的边界）：\n{table}\n\n"
        "请分析：①「不足」样本丢了哪些必须要素（对照充分样本补齐）；②归纳该剧情类型的极简内容"
        "合格要素清单（3-6 条，每条一句具体可判）；③据此前低预算档为什么丢要素，写出改进后的"
        "极简内容生成器 prompt（要求生成器强制保留合格要素）。\n"
        "严格输出如下 JSON（不要 Markdown 代码块）：\n"
        '{"elements": ["…"], "reason": "一句判断依据", "generator_prompt": "完整生成器 prompt"}'
    )
    try:
        res = await chat_json(
            system="你是「极简剧情压缩前沿分析师」。分析不同字数预算下极简剧情的复现分，"
                   "找出最短而充分的预算档与合格要素。",
            user=user,
            call_type="minimal_train_spec",
            max_tokens=3000,
        )
    except Exception:
        res = {"data": None, "error": "call_failed"}
    data = res.get("data") if not res.get("error") else None
    if isinstance(data, dict):
        elements = [str(e).strip() for e in (data.get("elements") or []) if str(e).strip()]
        reason = str(data.get("reason") or "").strip()
        gen = str(data.get("generator_prompt") or "").strip()
        if gen:
            return {"elements": elements, "reason": reason, "generator_prompt": gen}
    return {"elements": [], "reason": "", "generator_prompt": current_prompt}


def save_generator_prompt(text: str) -> dict[str, Any]:
    """把改进后的生成器 prompt 写入 fixed_prompts.minimal_plot_generator。"""
    text = (text or "").strip()
    if not text:
        return {"ok": False, "error": "空 prompt"}
    all_p = update_fixed_prompts({"minimal_plot_generator": text})
    return {"ok": True, "saved": text, "all": all_p}
