"""Harness 核心调度 v3.1。

v3.1 核心变更（本次）：
- 新增 diff 分析流水线：每轮评估后计算详细差异 → 错误分类 → 根因分析
- Prompt 修改从"模糊 REFINEMENT_PROMPT"升级为"结构化修改指令"
- LLM 不再自由发挥，而是精确执行确定性算法生成的修改指令
- 新增修改追踪：每轮记录改了什么规则、效果如何

v3 核心变更：
- 评分从 LLM-as-Judge 改为严格的字符级编辑距离相似度
- Loop 模式：自定节奏持续迭代直到收敛
- 去掉参数搜索、泛化测试、双轨竞跑
- 长度差异逐轮缩小策略
"""
from __future__ import annotations

import asyncio
import json
import math
import random
import time
import traceback
from dataclasses import dataclass, field
from typing import Any, Callable

from .diff_analyzer import analyze as diff_analyze, ErrorReport, format_modifications_for_prompt
from .embed_client import get_embedding
from .experience_store import ExperienceStore
from .fixed_prompts import get_baseline_guard
from .lesson_store import (
    FailureLedger,
    LessonKB,
    build_category_prior,
    build_coldstart_block,
    build_refinement_block,
    extract_failure_patterns,
    format_cross_run_lessons,
    format_ledger_for_result,
)
from .llm_client import chat_completion, chat_json, DISABLE_THINKING
from .scorer import (
    char_level_similarity,
    combined_optimization_score,
    compute_scores,
    length_diff_ratio,
)
from .templates import (
    CANDIDATE_GENERATOR_PROMPT,
    REFINEMENT_PROMPT,
    REVERSE_ENGINEER_PROMPT,
    STRUCTURED_MODIFICATION_PROMPT,
    build_diff_report,
    build_generation_prompt,
    build_length_feedback,
    build_similarity_feedback,
    build_structured_modification_prompt,
    build_verify_user_prompt,
    format_retrieved_examples,
)

# 元数据 key（不应被视为 prompt 内容，供 _extract_system_prompt fallback 使用）
_META_KEYS = {"name", "description", "changes_summary", "version", "notes", "label", "type", "approach", "focus", "rationale", "summary", "tags"}

# thinking 统一关闭常量见 llm_client.DISABLE_THINKING（import 自 llm_client）

# 改写角度池（停滞时轮换使用）
_FOCUS_ANGLES = [
    "对白增强：当前的生成文本缺乏自然对话。请检查 prompt 是否过度强调叙事描写而抑制了对白，添加明确的对白规则",
    "句式丰富：当前的句子结构单一。请重点修改标点节奏和句长分布规则，使长短句交错",
    "AI味消除：当前有明显的AI写作特征（判断句、模式化比喻、解释性总结）。请强化AI味禁令",
    "情绪力度：当前的情感传达平淡。请在 prompt 中加入情绪呈现规则——通过对白/动作/环境细节呈现情绪",
    "节奏紧凑：当前的叙事拖沓、信息密度低。请加入节奏控制规则——减少冗余修饰、每句推进情节",
    "长度对齐：当前生成长度与原文偏差较大。请在 prompt 中加入字数约束或调整详略程度",
]


@dataclass
class OptimizationResult:
    exp_id: str | None = None
    target_text: str = ""
    attributes: dict[str, Any] = field(default_factory=dict)
    best_variant_key: str = ""   # 最佳变体的标识（如 "variant_a"），用于日志和追踪
    best_prompt: str = ""
    best_generation: str = ""
    best_similarity: float = 0.0
    best_length_diff: float = 0.0
    best_scores: dict[str, float] = field(default_factory=dict)
    style_name: str = ""
    combined_score: float = 0.0
    rounds: int = 0
    history: list[dict[str, Any]] = field(default_factory=list)
    gen_params: dict[str, Any] = field(default_factory=dict)
    error: str | None = None
    source_file: str = ""
    category: str = ""
    failure_ledger: dict[str, Any] = field(default_factory=dict)
    cross_run_lessons: list[dict[str, Any]] = field(default_factory=list)


# ---------------------------------------------------------------------------
# 风格分析（保持不变）
# ---------------------------------------------------------------------------

async def analyze_target(target_text: str) -> dict[str, Any]:
    resp = await chat_json(
        system="你是资深网文编辑，只输出 JSON。",
        user=REVERSE_ENGINEER_PROMPT.format(target_text=target_text),
        temperature=0.1,
        top_p=0.3,
        extra_body=DISABLE_THINKING,
    )
    if resp["error"] or not isinstance(resp["data"], dict):
        return {
            "genre": "",
            "scene_type": "",
            "perspective": "",
            "named_entities": [],
            "style_notes": [],
            "tone": "",
            "plot_summary": "",
            "scene_beats": [],
            "character_goals": {},
            "conflict_type": "",
            "emotional_arc": "",
            "key_dialogue_beats": [],
            "narrative_technique": "",
            "setting": {},
            "world_state": {},
            "prose_economy": {},
        }
    data = resp["data"]
    for key in ("named_entities", "style_notes"):
        if not isinstance(data.get(key), list):
            data[key] = []
    if not isinstance(data.get("scene_beats"), list):
        data["scene_beats"] = []
    if not isinstance(data.get("character_goals"), dict):
        data["character_goals"] = {}
    if not isinstance(data.get("conflict_type"), str):
        data["conflict_type"] = ""
    if not isinstance(data.get("emotional_arc"), str):
        data["emotional_arc"] = ""
    if not isinstance(data.get("key_dialogue_beats"), list):
        data["key_dialogue_beats"] = []
    if not isinstance(data.get("narrative_technique"), str):
        data["narrative_technique"] = ""
    if not isinstance(data.get("setting"), dict):
        data["setting"] = {}
    if not isinstance(data.get("world_state"), dict):
        data["world_state"] = {}
    if not isinstance(data.get("prose_economy"), dict):
        data["prose_economy"] = {}
    return data


# ---------------------------------------------------------------------------
# 候选生成
# ---------------------------------------------------------------------------

def _extract_system_prompt(data: dict) -> str | None:
    """从 LLM 返回的 JSON 中提取纯文本 system prompt。"""
    if isinstance(data.get("system_prompt"), str) and data["system_prompt"].strip():
        return data["system_prompt"].strip()

    for field in ("prompt", "prompt_text", "content", "text", "body", "new_prompt"):
        v = data.get(field)
        if isinstance(v, str) and v.strip():
            return v.strip()

    strings: list[tuple[str, str]] = []
    for fk, fv in data.items():
        if isinstance(fv, str) and fk not in _META_KEYS and fv.strip():
            strings.append((fk, fv.strip()))
        elif isinstance(fv, dict):
            for sk, sv in fv.items():
                if isinstance(sv, str) and sk not in _META_KEYS and sv.strip():
                    strings.append((sk, sv.strip()))

    valid = [(fk, s) for fk, s in strings if len(s) >= 100]
    if not valid:
        valid = strings
    if valid:
        return max(valid, key=lambda x: len(x[1]))[1]

    return None


async def generate_candidates(
    target_text: str,
    retrieved_experiences: list[dict[str, Any]],
    previous_attempt: dict[str, Any] | None = None,
    stagnant_rounds: int = 0,
    focus_angle: str = "",
    category_prior: str = "",
    cross_run_lessons: str = "",
    lessons_block: str = "",
) -> tuple[str | None, str]:
    """生成一个 system prompt（纯文本），带重试。返回 (prompt, change_summary)。"""
    if previous_attempt is None:
        system = "你是 Prompt 工程师，只输出合法 JSON。"
        user = CANDIDATE_GENERATOR_PROMPT.format(
            target_text=target_text,
            category_prior=category_prior,
            cross_run_lessons=cross_run_lessons,
            retrieved_examples=format_retrieved_examples(retrieved_experiences),
        )
        temp = 0.5
    else:
        system = "你是 Prompt 优化师，只输出合法 JSON。"
        if stagnant_rounds >= 2:
            temp = 0.95
        elif stagnant_rounds >= 1:
            temp = 0.85
        else:
            temp = 0.65

        if stagnant_rounds >= 2:
            escalation = (
                "## ⚠️ 连续多轮相似度没有改善！必须**彻底重构**——换一种完全不同的 prompt 组织方式。\n"
            )
            if focus_angle:
                escalation += f"\n## 本轮方向\n{focus_angle}"
        elif stagnant_rounds >= 1:
            escalation = (
                "## ⚠️ 当前 prompt 已连续1轮没有改善。请进行**实质性改动**。\n"
            )
            if focus_angle:
                escalation += f"\n## 本轮方向\n{focus_angle}"
        else:
            if focus_angle:
                escalation = f"## 本轮方向\n{focus_angle}"
            else:
                escalation = ""

        prev_prompt = previous_attempt.get("prompt", "")
        prev_gen = previous_attempt.get("generation", "")
        if len(prev_gen) > 500:
            prev_gen = prev_gen[:500] + "\n\n……（以下省略，共" + str(len(prev_gen)) + "字）"

        # 构建相似度反馈
        similarity = previous_attempt.get("similarity", 0.0)
        length_diff = previous_attempt.get("length_diff", 0.0)
        gen_len = previous_attempt.get("gen_char_count", 0)
        target_len = previous_attempt.get("target_char_count", 0)

        similarity_feedback = build_similarity_feedback(similarity, length_diff, gen_len, target_len)
        length_feedback = build_length_feedback(length_diff, gen_len, target_len)

        user = REFINEMENT_PROMPT.format(
            target_text=target_text,
            current_system_prompt=prev_prompt if isinstance(prev_prompt, str) else json.dumps(prev_prompt, ensure_ascii=False, indent=2),
            gen_a=prev_gen,
            similarity_feedback=similarity_feedback,
            length_feedback=length_feedback,
            escalation_instruction=escalation,
            lessons_block=lessons_block,
        )

    last_error = ""
    for attempt in range(2):
        if stagnant_rounds >= 2 and previous_attempt is not None and attempt == 0:
            system = "你是 Prompt 工程师，只输出合法 JSON。"
            user = CANDIDATE_GENERATOR_PROMPT.format(
                target_text=target_text,
                category_prior=category_prior,
                cross_run_lessons=cross_run_lessons,
                retrieved_examples=format_retrieved_examples(retrieved_experiences),
            )
            mt = 2048
        else:
            if previous_attempt is None:
                mt = 2048
            else:
                mt = 6144 if stagnant_rounds >= 1 else 4096
        resp = await chat_json(
            system=system, user=user, temperature=temp, top_p=0.7,
            max_tokens=mt, extra_body=DISABLE_THINKING,
        )
        if not resp["error"] and isinstance(resp["data"], dict):
            data = resp["data"]
            prompt = _extract_system_prompt(data)
            if prompt:
                if previous_attempt is None:
                    change_summary = "(冷启动初始生成)"
                else:
                    change_summary = str(data.get("change_summary") or data.get("changes_summary") or "").strip()
                    if not change_summary:
                        change_summary = "（本轮改动未说明）"
                return prompt, change_summary
            last_error = "提取 Prompt 为空"
        else:
            last_error = resp.get("error", "未知错误")
    print(f"[generate_candidates] 全部 {2} 次重试失败: {last_error}")
    return None, ""


# ---------------------------------------------------------------------------
# 正文生成
# ---------------------------------------------------------------------------

def _calc_max_tokens(target_text: str, min_tokens: int = 2048, max_tokens: int = 8192, override: int = 0) -> int:
    """根据目标文本字符数计算合理的 max_tokens。"""
    if override > 0:
        return override
    if not target_text:
        return 4096
    char_count = len(target_text)
    target_tokens = int(char_count * 2.0)
    return max(min_tokens, min(max_tokens, target_tokens))


def _auto_gen_params(attributes: dict[str, Any], target_text: str = "") -> dict[str, Any]:
    """从目标文本分析结果中自动推导生成参数。"""
    style_notes = attributes.get("style_notes") or []
    tone = attributes.get("tone") or ""

    temperature = 0.60
    top_p = 0.8
    presence_penalty = 0.1
    frequency_penalty = 0.1
    max_tokens = _calc_max_tokens(target_text)

    style_text = " ".join(style_notes) if isinstance(style_notes, list) else str(style_notes or "")

    if any(kw in style_text for kw in ["短句", "有力", "简洁", "克制", "精准", "冷峻"]):
        temperature = 0.50
        top_p = 0.80
    elif any(kw in style_text for kw in ["华丽", "铺陈", "飘逸", "奔放", "渲染", "诗化"]):
        temperature = 0.70
        top_p = 0.90
        presence_penalty = 0.05

    if any(kw in style_text for kw in ["对白", "市井", "口语", "对话"]):
        presence_penalty = max(presence_penalty, 0.15)

    key_dialogue = attributes.get("key_dialogue_beats")
    if key_dialogue and isinstance(key_dialogue, list) and len(key_dialogue) > 0:
        presence_penalty = max(presence_penalty, 0.12)

    if any(kw in tone for kw in ["紧张", "压抑", "激烈", "热血"]):
        frequency_penalty = max(frequency_penalty, 0.12)
    elif any(kw in tone for kw in ["温馨", "舒缓", "恬淡"]):
        temperature = min(temperature + 0.05, 0.6)

    return {
        "temperature": round(temperature, 2),
        "top_p": round(top_p, 2),
        "presence_penalty": round(presence_penalty, 2),
        "frequency_penalty": round(frequency_penalty, 2),
        "max_tokens": max_tokens,
    }


async def forward_generation(
    system_prompt: str,
    settings: dict[str, Any],
    plot_direction: str,
    *,
    gen_params: dict[str, Any] | None = None,
    scene_beats: list[str] | None = None,
    conflict_type: str = "",
    emotional_arc: str = "",
    key_dialogue_beats: list[str] | None = None,
    setting_info: dict | None = None,
    world_state: dict | None = None,
    prose_economy: dict | None = None,
) -> str:
    """用不变层（system_prompt）+ 可变层（settings + 剧情结构）生成正文。"""
    params = gen_params or {}
    user_prompt = build_verify_user_prompt(
        settings, plot_direction,
        scene_beats=scene_beats,
        conflict_type=conflict_type,
        emotional_arc=emotional_arc,
        key_dialogue_beats=key_dialogue_beats,
        setting_info=setting_info,
        world_state=world_state,
        prose_economy=prose_economy,
    )

    baseline_guard = get_baseline_guard()

    async def _try(temp: float) -> str:
        resp = await chat_completion(
            system=baseline_guard + "\n\n" + system_prompt,
            user=user_prompt,
            temperature=temp,
            top_p=params.get("top_p", 0.7),
            presence_penalty=params.get("presence_penalty", 0.1),
            frequency_penalty=params.get("frequency_penalty", 0.1),
            max_tokens=params.get("max_tokens", 4096),
            extra_body=DISABLE_THINKING,
        )
        content = resp.get("content", "")
        return content.strip() if content.strip() else ""

    base_temp = params.get("temperature", 0.3)
    text = await _try(base_temp)
    if text:
        return text

    retry_temp = min(base_temp + 0.1, 0.6)
    if retry_temp != base_temp:
        text = await _try(retry_temp)
        if text:
            return text

    return ""


# ---------------------------------------------------------------------------
# 评估一个候选（v3：纯计算相似度，无 LLM 评分调用）
# ---------------------------------------------------------------------------

async def evaluate_candidate(
    system_prompt: str,
    target_text: str,
    settings: dict[str, Any],
    plot_a: str,
    *,
    gen_params: dict[str, Any] | None = None,
    named_entities: list[str] | None = None,
    scene_beats: list[str] | None = None,
    conflict_type: str = "",
    emotional_arc: str = "",
    key_dialogue_beats: list[str] | None = None,
    setting_info: dict | None = None,
    world_state: dict | None = None,
    prose_economy: dict | None = None,
) -> dict[str, Any]:
    """完整评估一个候选 prompt：生成正文 + 计算字符级相似度。

    不再调用 LLM-as-Judge，评分变为纯计算，速度极快。
    """
    # Step 1: 生成正文
    gen_a = await forward_generation(
        system_prompt, settings, plot_a, gen_params=gen_params,
        scene_beats=scene_beats,
        conflict_type=conflict_type,
        emotional_arc=emotional_arc,
        key_dialogue_beats=key_dialogue_beats,
        setting_info=setting_info,
        world_state=world_state,
        prose_economy=prose_economy,
    )

    # Step 2: 计算字符级相似度（纯计算，无 LLM 调用）
    similarity = char_level_similarity(gen_a, target_text)
    length_diff = length_diff_ratio(gen_a, target_text)

    # Step 3: 诊断指标（可选，异步计算）
    diag = await compute_scores(gen_a, target_text)

    # 规范化字符数
    from .scorer import normalize_text
    gen_norm = normalize_text(gen_a)
    tgt_norm = normalize_text(target_text)

    return {
        "prompt": system_prompt,
        "generation": gen_a,
        "similarity": similarity,
        "length_diff": length_diff,
        "gen_char_count": len(gen_norm),
        "target_char_count": len(tgt_norm),
        "combined_score": similarity,
        "diagnostic_scores": diag,
    }


# ---------------------------------------------------------------------------
# LLM 精排
# ---------------------------------------------------------------------------

async def _rerank_experiences(
    target_text: str,
    candidates: list[dict[str, Any]],
    top_k: int = 3,
) -> list[dict[str, Any]]:
    """用 LLM 从 FAISS 粗筛结果中精排。"""
    if not candidates or len(candidates) <= top_k:
        return candidates[:top_k]

    lines = []
    for i, c in enumerate(candidates, 1):
        txt = (c.get("target_text") or "")[:120]
        score = c.get("search_score", 0)
        genre = c.get("genre", "") or ""
        scene = c.get("scene_type", "") or ""
        lines.append(f"[{i}] 题材={genre} 场景={scene} FAISS分={score:.3f}\n    预览={txt}…")

    user_prompt = (
        "你正在辅助一个网文 Prompt 优化系统。当前要训练的目标文本如下：\n\n"
        f"【当前目标】\n{target_text[:300]}\n\n"
        "以下是 FAISS 向量检索找出的候选历史经验：\n\n"
        + "\n".join(lines)
        + f"\n\n请选出与当前目标**在文风、场景、情绪上最相似**的 {top_k} 条。"
        f"只输出 JSON：{{\"selected_indices\": [索引号列表]}}"
    )

    resp = await chat_json(
        system="你是网文风格分析专家，只输出 JSON。",
        user=user_prompt,
        temperature=0.1,
        top_p=0.3,
        extra_body=DISABLE_THINKING,
    )

    if resp["error"] or not isinstance(resp.get("data"), dict):
        return candidates[:top_k]

    indices = resp["data"].get("selected_indices", [])
    if not indices or not isinstance(indices, list):
        return candidates[:top_k]

    results = []
    seen = set()
    for idx in indices:
        pos = int(idx) - 1
        if 0 <= pos < len(candidates) and pos not in seen:
            results.append(candidates[pos])
            seen.add(pos)
        if len(results) >= top_k:
            break

    return results if results else candidates[:top_k]


# ---------------------------------------------------------------------------
# 自动推导
# ---------------------------------------------------------------------------

def _auto_settings(attributes: dict[str, Any]) -> dict[str, Any]:
    """从目标段落分析结果中自动构建可变层设定。"""
    settings: dict[str, Any] = {}
    if attributes.get("genre"):
        settings["题材"] = attributes["genre"]
    if attributes.get("tone"):
        settings["基调"] = attributes["tone"]
    if attributes.get("perspective"):
        settings["视角"] = attributes["perspective"]
    entities = attributes.get("named_entities", [])
    if entities:
        settings["关键元素"] = "、".join(entities[:5])
    style_notes = attributes.get("style_notes", [])
    if isinstance(style_notes, list) and style_notes:
        settings["风格关键词"] = "、".join(style_notes[:5])
    elif isinstance(style_notes, str) and style_notes.strip():
        settings["风格关键词"] = style_notes
    if attributes.get("conflict_type"):
        settings["冲突类型"] = attributes["conflict_type"]
    if attributes.get("narrative_technique"):
        settings["叙事手法"] = attributes["narrative_technique"]
    return settings


# ---------------------------------------------------------------------------
# 主优化流程（v3 Loop 模式）
# ---------------------------------------------------------------------------

async def apply_structured_modifications(
    current_prompt: str,
    target_text: str,
    last_generation: str,
    similarity: float,
    length_diff: float,
    history: list[dict[str, Any]],
    *,
    stagnant_rounds: int = 0,
    max_modifications: int = 3,
) -> tuple[str | None, str]:
    """使用 diff 分析 + 结构化修改指令来修改 prompt。

    与旧 generate_candidates() 的关键区别：
    - 先运行 diff_analyzer.analyze() 进行确定性分析
    - 生成精确的修改指令（而非模糊的「微调」/「实质性改动」）
    - LLM 受限执行指令，不允许自由发挥

    Returns:
        (new_prompt, change_summary) 或 (None, error_message)
    """
    from .scorer import normalize_text

    # Step 1: 运行 diff 分析
    report = diff_analyze(
        generated=last_generation,
        target=target_text,
        similarity=similarity,
        length_diff=length_diff,
        normalize_fn=normalize_text,
    )

    # Step 2: 根据相似度范围限制修改数量
    if similarity < 0.20:
        # 分数太低，diff 无参考意义，走冷重启
        return None, "similarity_too_low"
    elif similarity < 0.40:
        max_per = {0: 5, 1: 3, 2: 1}
    elif similarity < 0.60:
        max_per = {0: 3, 1: 2, 2: 1}
    elif similarity < 0.80:
        max_per = {0: 2, 1: 1, 2: 1}
    else:
        max_per = {0: 1, 1: 1, 2: 0}

    # 应用 max_modifications 硬上限
    total_allowed = max_modifications
    # 按比例分配
    max_per = {k: min(v, total_allowed) for k, v in max_per.items()}

    # Step 3: 格式化修改指令
    modification_instructions = format_modifications_for_prompt(
        report.modifications, max_per_priority=max_per,
    )

    if not modification_instructions.strip() or "无需修改" in modification_instructions:
        return current_prompt, "（本轮无需修改）"

    # Step 4: 构建结构化修改 prompt
    system = "你是 Prompt 优化师，只输出合法 JSON。请精确执行修改指令，不自作主张。"
    user = build_structured_modification_prompt(
        current_system_prompt=current_prompt,
        target_text=target_text,
        last_generation=last_generation,
        modification_instructions=modification_instructions,
    )

    # Step 5: 调用 LLM 执行修改
    if stagnant_rounds >= 2:
        temp = 0.90
    elif stagnant_rounds >= 1:
        temp = 0.80
    else:
        temp = 0.60

    last_error = ""
    for attempt in range(2):
        resp = await chat_json(
            system=system,
            user=user,
            temperature=temp,
            top_p=0.70,
            max_tokens=4096,
            extra_body=DISABLE_THINKING,
        )
        if not resp["error"] and isinstance(resp["data"], dict):
            data = resp["data"]
            prompt = _extract_system_prompt(data)
            if prompt:
                change_summary = str(data.get("change_summary") or "").strip()
                modifications_applied = data.get("modifications_applied", [])
                if modifications_applied:
                    change_summary = "; ".join(modifications_applied) if isinstance(modifications_applied, list) else change_summary
                if not change_summary:
                    change_summary = "（结构化修改）"
                return prompt, change_summary
            last_error = "提取 Prompt 为空"
        else:
            last_error = resp.get("error", "未知错误")

    print(f"[apply_structured_modifications] 全部 {2} 次重试失败: {last_error}")
    return None, last_error


# 加载配置
def _load_loop_config() -> dict:
    """加载 Loop 配置（从 scoring_weights.json）。"""
    from pathlib import Path
    config_path = Path(__file__).resolve().parent.parent / "data" / "scoring_weights.json"
    defaults = {
        "similarity_threshold": 0.90,
        "length_tolerance": 0.10,
        "max_rounds": 20,
        "stagnant_limit": 5,
        "cold_restart_at": 5,
        "auto_stop_at": 8,
    }
    if config_path.is_file():
        try:
            saved = json.loads(config_path.read_text(encoding="utf-8"))
            for k, v in saved.items():
                if k in defaults:
                    defaults[k] = v
        except (json.JSONDecodeError, OSError):
            pass
    return defaults


async def optimize(
    target_text: str,
    store: ExperienceStore,
    *,
    settings: dict[str, Any] | None = None,
    plot_a: str = "",
    max_rounds: int = 20,
    min_rounds: int = 2,
    success_threshold: float = 0.90,
    style_name: str = "",
    max_tokens: int = 0,
    gen_temperature: float = 0.0,
    gen_top_p: float = 0.0,
    gen_presence_penalty: float | None = None,
    gen_frequency_penalty: float | None = None,
    auto_optimize_params: bool = True,
    progress_callback: Callable[[dict[str, Any]], None] | None = None,
    source_file: str = "",
    warm_start_prompt: str = "",
    is_same_passage: bool = False,
    historical_best_score: float = 0.0,
    category: str = "",
    logger: Any = None,
) -> OptimizationResult:
    """优化 prompt 主流程（v3 Loop 模式）。

    核心变化：
    - 评分从 LLM-as-Judge 改为字符级相似度（纯计算）
    - Loop 自适应策略：低分重构 → 中分 refine → 高分微调
    - 长度差异逐轮缩小
    - 去掉参数搜索、泛化测试、双轨竞跑
    """
    config = _load_loop_config()
    max_rounds = max_rounds or config["max_rounds"]
    stagnant_limit = config["stagnant_limit"]
    cold_restart_at = config["cold_restart_at"]
    auto_stop_at = config["auto_stop_at"]

    settings = settings or {}
    result = OptimizationResult(target_text=target_text)
    result.style_name = style_name
    result.source_file = source_file
    result.category = category

    # 1. 分析目标风格
    attributes = await analyze_target(target_text)
    result.attributes = attributes

    # 1.5 失败学习机制
    lesson_kb = LessonKB()
    ledger = FailureLedger()
    cross_run_lessons = lesson_kb.get_lessons(category) if category else []
    if progress_callback:
        progress_callback({
            "phase": "category_loaded",
            "category": category,
            "retrieved_lessons": len(cross_run_lessons),
        })
    if category:
        if not settings:
            settings = _auto_settings(attributes)
        settings["题材"] = category

    # 1a. 自动推导 settings / plot_a
    if not settings:
        settings = _auto_settings(attributes)
    if not plot_a.strip():
        plot_a = attributes.get("plot_summary", "") or "（从原文自动提取）"

    # 提取剧情结构
    scene_beats: list[str] = attributes.get("scene_beats", []) or []
    conflict_type: str = attributes.get("conflict_type", "") or ""
    emotional_arc: str = attributes.get("emotional_arc", "") or ""
    key_dialogue_beats: list[str] = attributes.get("key_dialogue_beats", []) or []
    setting_info: dict = attributes.get("setting", {}) or {}
    world_state: dict = attributes.get("world_state", {}) or {}
    prose_economy: dict = attributes.get("prose_economy", {}) or {}

    # 1b. 自动推导生成参数
    gen_params = _auto_gen_params(attributes, target_text)
    if max_tokens > 0:
        gen_params["max_tokens"] = max_tokens
    if gen_temperature > 0:
        gen_params["temperature"] = gen_temperature
    if gen_top_p > 0:
        gen_params["top_p"] = gen_top_p
    if gen_presence_penalty is not None:
        gen_params["presence_penalty"] = gen_presence_penalty
    if gen_frequency_penalty is not None:
        gen_params["frequency_penalty"] = gen_frequency_penalty

    # 2. 召回相似历史经验
    retrieved = await store.semantic_search(target_text, top_k=10)
    if retrieved:
        retrieved = await _rerank_experiences(target_text, retrieved, top_k=3)

    # 3. 初始候选
    category_prior_text = build_category_prior(category)
    cross_run_lessons_text = format_cross_run_lessons(cross_run_lessons)

    if warm_start_prompt and warm_start_prompt.strip() and is_same_passage:
        # 同段落热启动
        prompt_text = warm_start_prompt
        entry = await evaluate_candidate(
            warm_start_prompt, target_text, settings, plot_a,
            gen_params=gen_params,
            named_entities=attributes.get("named_entities", []),
            scene_beats=scene_beats,
            conflict_type=conflict_type,
            emotional_arc=emotional_arc,
            key_dialogue_beats=key_dialogue_beats,
            setting_info=setting_info,
            world_state=world_state,
            prose_economy=prose_economy,
        )
        entry["variant"] = "warm_start"
        entry["change_summary"] = "(热启动历史最优 prompt)"
        entry["lessons_block"] = build_category_prior(category)
        entry["failure_patterns"] = extract_failure_patterns(entry)
        result.history.append(entry)
    else:
        # 冷启动
        prompt_text, change_summary = await generate_candidates(
            target_text, retrieved,
            category_prior=category_prior_text,
            cross_run_lessons=cross_run_lessons_text,
        )
        if not prompt_text:
            result.error = "生成候选 Prompt 失败"
            result.failure_ledger["generate_candidates"] = {
                "error": "生成候选 Prompt 失败",
                "underlying": "",  # 在调用处填入实际错误
            }
            return result
        entry = await evaluate_candidate(
            prompt_text, target_text, settings, plot_a,
            gen_params=gen_params,
            named_entities=attributes.get("named_entities", []),
            scene_beats=scene_beats,
            conflict_type=conflict_type,
            emotional_arc=emotional_arc,
            key_dialogue_beats=key_dialogue_beats,
            setting_info=setting_info,
            world_state=world_state,
            prose_economy=prose_economy,
        )
        entry["variant"] = "cold_start"
        entry["change_summary"] = change_summary
        entry["lessons_block"] = build_coldstart_block(category, cross_run_lessons)
        entry["failure_patterns"] = extract_failure_patterns(entry)
        result.history.append(entry)

    # 设置 Round 1 结果
    gen_text_r1 = (entry.get("generation") or "").strip()
    if len(gen_text_r1) >= 100:
        previous_best = entry
        result.best_prompt = entry.get("prompt", "")
        result.best_generation = entry.get("generation", "")
        result.best_similarity = entry.get("similarity", 0.0)
        result.best_length_diff = entry.get("length_diff", 0.0)
        result.combined_score = entry.get("similarity", 0.0)
        result.best_scores = entry.get("diagnostic_scores", {})
        ledger.record_round(1, entry.get("change_summary", "（初始生成）"), None, entry.get("similarity", 0.0), entry)
    else:
        previous_best = None

    stagnant_rounds = 0
    best_similarity = result.best_similarity

    # Round 1 进度回调
    r1_sim = entry.get("similarity", 0.0)
    if progress_callback:
        progress_callback({
            "round": 1,
            "max_rounds": max_rounds,
            "similarity": r1_sim,
            "best_similarity": best_similarity,
            "length_diff": entry.get("length_diff", 0.0),
            "gen_char_count": entry.get("gen_char_count", 0),
            "target_char_count": entry.get("target_char_count", 0),
            "improved_this_round": True,
            "stagnant_rounds": 0,
            "gen_params": gen_params,
            "variant": entry.get("variant", "cold_start"),
            "change_summary": entry.get("change_summary", ""),
            "category": category,
        })

    # =========================================================================
    # Loop 模式：每轮执行 diff → 分类 → 根因 → 精确修改指令 → LLM 执行
    # =========================================================================
    for round_idx in range(2, max_rounds + 1):
        result.rounds = round_idx

        if previous_best is None:
            print(f"第 {round_idx} 轮无精调基础，跳过")
            continue

        # Step A: 获取当前状态
        current_sim = previous_best.get("similarity", 0.0)
        current_prompt = previous_best.get("prompt", "")
        last_gen = previous_best.get("generation", "")
        current_length_diff = previous_best.get("length_diff", 0.0)

        # Step B: 长度对齐（逐轮缩小 max_tokens）
        if abs(current_length_diff) > 0.15:
            target_chars = previous_best.get("target_char_count", len(target_text))
            current_chars = previous_best.get("gen_char_count", target_chars)
            if current_chars > 0:
                ratio = target_chars / max(current_chars, 1)
                current_max = gen_params.get("max_tokens", 4096)
                new_max = int(current_max * ratio)
                new_max = max(2048, min(8192, new_max))
                if abs(new_max - current_max) > 200:
                    gen_params["max_tokens"] = new_max

        # Step C: 构建 lessons block
        category_prior_text = build_category_prior(category)
        cross_run_lessons_text = format_cross_run_lessons(cross_run_lessons)
        lessons_block = build_refinement_block(ledger, cross_run_lessons, category)

        # Step D: 决定本轮动作
        action = "structured_refine"  # 默认：结构化修改
        focus_angle = ""

        if stagnant_rounds >= cold_restart_at:
            action = "cold_restart"
        elif current_sim < 0.20:
            # diff 无参考意义，冷重启
            action = "cold_restart"
        elif stagnant_rounds >= 2:
            # 停滞，尝试并行多角度结构化修改
            action = "parallel_structured"
            focus_angle = _FOCUS_ANGLES[(round_idx - 1) % (len(_FOCUS_ANGLES) - 1)]
        elif abs(current_length_diff) > 0.15:
            focus_angle = _FOCUS_ANGLES[5]

        # Step E: 执行对应动作
        change_summary = ""
        diff_error_summary = ""
        modifications_applied_count = 0

        if action == "cold_restart":
            # ---- 冷重启：使用 CANDIDATE_GENERATOR_PROMPT 全新生成 ----
            print(f"[optimizer] 第 {round_idx} 轮：冷重启（sim={current_sim:.4f}，停滞={stagnant_rounds}）")
            prompt_text, change_summary = await generate_candidates(
                target_text, retrieved,
                previous_attempt=None,
                category_prior=category_prior_text,
                cross_run_lessons=cross_run_lessons_text,
            )
            variant = "cold_restart"

        elif action == "parallel_structured":
            # ---- 并行双角度结构化修改 ----
            print(f"[optimizer] 第 {round_idx} 轮：并行双角度结构化修改（stagnant={stagnant_rounds}）")
            alt_angle = _FOCUS_ANGLES[(round_idx + 2) % (len(_FOCUS_ANGLES) - 1)]

            # 两条路径：结构化修改 + 带角度引导的旧 refine
            c1 = apply_structured_modifications(
                current_prompt=current_prompt,
                target_text=target_text,
                last_generation=last_gen,
                similarity=current_sim,
                length_diff=current_length_diff,
                history=result.history,
                stagnant_rounds=stagnant_rounds,
            )
            # 备用路径：带角度引导的 REFINEMENT_PROMPT（确保不被卡住）
            c2 = generate_candidates(
                target_text, retrieved, previous_attempt=previous_best,
                stagnant_rounds=stagnant_rounds, focus_angle=alt_angle,
                lessons_block=lessons_block,
                category_prior=category_prior_text,
                cross_run_lessons=cross_run_lessons_text,
            )
            prompts_results = await asyncio.gather(c1, c2)
            valid_results = [(p, s) for p, s in prompts_results if p and p != current_prompt]
            if valid_results:
                evals = await asyncio.gather(*[
                    evaluate_candidate(p, target_text, settings, plot_a,
                        gen_params=gen_params,
                        named_entities=attributes.get("named_entities", []),
                        scene_beats=scene_beats,
                        conflict_type=conflict_type,
                        emotional_arc=emotional_arc,
                        key_dialogue_beats=key_dialogue_beats,
                        setting_info=setting_info,
                        world_state=world_state,
                        prose_economy=prose_economy,
                    ) for p, _ in valid_results
                ])
                best_idx = max(range(len(evals)), key=lambda i: evals[i]["similarity"])
                prompt_text = valid_results[best_idx][0]
                change_summary = valid_results[best_idx][1]
                best_eval = evals[best_idx]
                best_eval["variant"] = "parallel_structured"
                best_eval["change_summary"] = change_summary
                best_eval["lessons_block"] = lessons_block
                best_eval["failure_patterns"] = extract_failure_patterns(best_eval)
                result.history.append(best_eval)
                if best_eval["similarity"] > result.best_similarity:
                    previous_best = best_eval
                    result.best_similarity = best_eval["similarity"]
                    result.best_prompt = prompt_text
                    result.best_generation = best_eval.get("generation", "")
                    result.best_length_diff = best_eval.get("length_diff", 0.0)
                    result.combined_score = best_eval["similarity"]
                    result.best_scores = best_eval.get("diagnostic_scores", {})
                    best_similarity = best_eval["similarity"]
                    stagnant_rounds = 0
                else:
                    stagnant_rounds += 1
                # 进度回调
                if progress_callback:
                    progress_callback({
                        "round": round_idx, "max_rounds": max_rounds,
                        "similarity": best_eval["similarity"],
                        "best_similarity": best_similarity,
                        "length_diff": best_eval.get("length_diff", 0.0),
                        "gen_char_count": best_eval.get("gen_char_count", 0),
                        "target_char_count": best_eval.get("target_char_count", 0),
                        "improved_this_round": best_eval["similarity"] > current_sim,
                        "stagnant_rounds": stagnant_rounds,
                        "gen_params": gen_params,
                        "variant": "parallel_structured",
                        "change_summary": change_summary,
                        "action": action,
                        "category": category,
                    })
                # 收敛检查
                if round_idx >= min_rounds and best_similarity >= success_threshold:
                    print(f"[optimizer] 收敛：similarity={best_similarity:.4f} >= {success_threshold}，第 {round_idx} 轮结束")
                    break
                if stagnant_rounds >= auto_stop_at:
                    print(f"[optimizer] 连续 {stagnant_rounds} 轮无提升，自动结束")
                    break
                continue  # 跳过本轮正常的 entry 评估
            else:
                # 并行失败，回退到旧 refine
                prompt_text, change_summary = await generate_candidates(
                    target_text, retrieved, previous_attempt=previous_best,
                    stagnant_rounds=stagnant_rounds, focus_angle=focus_angle,
                    lessons_block=lessons_block,
                    category_prior=category_prior_text,
                    cross_run_lessons=cross_run_lessons_text,
                )
                variant = "refined_fallback"

        else:
            # ---- 结构化修改：diff → 分类 → 根因 → 精确指令 → LLM 执行 ----
            print(f"[optimizer] 第 {round_idx} 轮：结构化修改（sim={current_sim:.4f}）")
            prompt_text, change_summary = await apply_structured_modifications(
                current_prompt=current_prompt,
                target_text=target_text,
                last_generation=last_gen,
                similarity=current_sim,
                length_diff=current_length_diff,
                history=result.history,
                stagnant_rounds=stagnant_rounds,
            )
            variant = "structured_refine"

            # 如果结构化修改返回 None（相似度太低或无修改必要），回退
            if not prompt_text or change_summary == "similarity_too_low":
                if change_summary == "similarity_too_low":
                    print(f"[optimizer] 相似度 {current_sim:.4f} 过低，转为冷重启")
                    prompt_text, change_summary = await generate_candidates(
                        target_text, retrieved,
                        previous_attempt=None,
                        category_prior=category_prior_text,
                        cross_run_lessons=cross_run_lessons_text,
                    )
                    variant = "cold_restart"
                if not prompt_text:
                    # 最终回退：使用旧 REFINEMENT_PROMPT
                    prompt_text, change_summary = await generate_candidates(
                        target_text, retrieved, previous_attempt=previous_best,
                        stagnant_rounds=stagnant_rounds, focus_angle=focus_angle,
                        lessons_block=lessons_block,
                        category_prior=category_prior_text,
                        cross_run_lessons=cross_run_lessons_text,
                    )
                    variant = "refined_fallback"

        if not prompt_text:
            print(f"第 {round_idx} 轮生成候选失败，跳过")
            continue

        # Step F: 评估候选
        entry = await evaluate_candidate(
            prompt_text, target_text, settings, plot_a,
            gen_params=gen_params,
            named_entities=attributes.get("named_entities", []),
            scene_beats=scene_beats,
            conflict_type=conflict_type,
            emotional_arc=emotional_arc,
            key_dialogue_beats=key_dialogue_beats,
            setting_info=setting_info,
            world_state=world_state,
            prose_economy=prose_economy,
        )
        entry["variant"] = variant
        entry["change_summary"] = change_summary
        entry["lessons_block"] = lessons_block
        entry["failure_patterns"] = extract_failure_patterns(entry)
        entry["action"] = action
        result.history.append(entry)

        # Step G: 记录账本
        score_before = previous_best.get("similarity", 0.0) if previous_best else None
        ledger.record_round(round_idx, entry.get("change_summary", ""), score_before, entry.get("similarity", 0.0), entry)

        # Step H: 更新最佳
        if entry["similarity"] > best_similarity:
            previous_best = entry
            result.best_prompt = entry["prompt"]
            result.best_generation = entry["generation"]
            result.best_similarity = entry["similarity"]
            result.best_length_diff = entry.get("length_diff", 0.0)
            result.combined_score = entry["similarity"]
            result.best_scores = entry.get("diagnostic_scores", {})
            best_similarity = entry["similarity"]
            stagnant_rounds = 0
        else:
            stagnant_rounds += 1

        # Step I: 进度回调
        if progress_callback:
            progress_callback({
                "round": round_idx,
                "max_rounds": max_rounds,
                "similarity": entry["similarity"],
                "best_similarity": best_similarity,
                "length_diff": entry.get("length_diff", 0.0),
                "gen_char_count": entry.get("gen_char_count", 0),
                "target_char_count": entry.get("target_char_count", 0),
                "improved_this_round": entry["similarity"] > (score_before or 0),
                "stagnant_rounds": stagnant_rounds,
                "gen_params": gen_params,
                "variant": variant,
                "change_summary": change_summary,
                "action": action,
                "focus_angle": focus_angle if focus_angle else "",
                "category": category,
            })

        # Step J: 收敛检查
        if round_idx >= min_rounds and best_similarity >= success_threshold:
            print(f"[optimizer] 收敛：similarity={best_similarity:.4f} >= {success_threshold}，第 {round_idx} 轮结束")
            break

        # 自动停止：连续 N 轮没提升
        if stagnant_rounds >= auto_stop_at:
            print(f"[optimizer] 连续 {stagnant_rounds} 轮无提升，自动结束")
            break

    if previous_best is None:
        result.error = "未能生成有效候选"
        return result

    # 失败学习：蒸馏本次账本
    result.failure_ledger = format_ledger_for_result(ledger)
    if category:
        lesson_kb.distill_and_store(category, ledger, source_file or "", result.best_similarity, result.rounds)
        result.cross_run_lessons = lesson_kb.get_lessons(category)

    # 存入经验库
    embedding = await get_embedding(target_text)
    exp_id = await store.add_experience({
        "target_text": target_text,
        "genre": attributes.get("genre"),
        "scene_type": attributes.get("scene_type"),
        "perspective": attributes.get("perspective"),
        "named_entities": attributes.get("named_entities", []),
        "optimal_prompt": result.best_prompt,
        "variant_type": "similarity_loop",
        "score": result.best_similarity,
        "scores_detail": {
            "similarity": result.best_similarity,
            "length_diff": result.best_length_diff,
            "diagnostic": result.best_scores,
            "error": result.error,
        },
        "rounds": result.rounds,
        "embedding": embedding,
        "settings_snapshot": settings,
        "style_name": style_name or None,
        "source_file": source_file or None,
        "is_same_passage": int(is_same_passage),
        "best_generation": result.best_generation or None,
        "attributes_json": attributes,
        "gen_params_json": gen_params,
        "max_rounds": max_rounds,
        "success_threshold": success_threshold,
    })
    result.exp_id = exp_id
    result.gen_params = gen_params
    return result


# ============================================================================
# v4.0 统一段落 + 人工选择 模式（保持不变）
# ============================================================================

_MINIMAL_SYSTEM_PROMPT = (
    "你是一个小说作家。请按照以下场景描述直接写出小说正文。只输出正文，不输出任何其他内容。"
)


async def extract_paragraph(target_text: str) -> dict[str, Any]:
    """v4.0 从原文提取统一场景描述段落。

    返回 {paragraph: str, error: str|None, retry_count: int}
    """
    from .templates import UNIFIED_PARAGRAPH_EXTRACTOR

    system_prompt = "你是资深网文编辑。请直接输出一段场景描述，不要加任何前缀或 JSON 包裹。"
    user_prompt = UNIFIED_PARAGRAPH_EXTRACTOR.format(target_text=target_text)

    # 多温度重试：低温优先（更准确），空了就升温
    temps_to_try = [0.2, 0.5, 0.8]
    last_error = None

    for i, temp in enumerate(temps_to_try):
        resp = await chat_completion(
            system=system_prompt,
            user=user_prompt,
            temperature=temp,
            top_p=0.5,
            max_tokens=2048,
            extra_body=DISABLE_THINKING,
            call_type="extract_paragraph",
        )

        if resp.get("error"):
            last_error = resp["error"]
            continue  # 报错也试试下一个温度？不，报错就是配置问题，不重试

        content = (resp.get("content") or "").strip()
        if content and len(content) >= 20:
            return {
                "paragraph": content,
                "error": None,
                "retry_count": i,
                "temperature": temp,
            }
        last_error = f"模型返回内容过短（{len(content)} 字）"

    # 所有重试都失败
    return {
        "paragraph": "",
        "error": last_error or "提取失败，模型未返回有效内容",
        "retry_count": len(temps_to_try),
    }


async def generate_variants(
    base_paragraph: str,
    angles: list[str],
) -> list[dict[str, str]]:
    """v4.0 从 base 段落 + 角度列表生成 3 个变体。"""
    from .templates import VARIANT_GENERATOR_PROMPT, format_angle_descriptions

    angle_desc = format_angle_descriptions(angles)
    user = VARIANT_GENERATOR_PROMPT.format(
        base_paragraph=base_paragraph,
        angle_descriptions=angle_desc,
    )
    resp = await chat_json(
        system="你是 Prompt 优化师，只输出合法 JSON。",
        user=user,
        temperature=0.7,
        top_p=0.9,
        max_tokens=4096,
        extra_body=DISABLE_THINKING,
    )
    if resp["error"] or not isinstance(resp.get("data"), dict):
        return []

    variants = resp["data"].get("variants", [])
    if not isinstance(variants, list):
        return []

    return [
        {"angle": v.get("angle", ""), "paragraph": v.get("paragraph", "")}
        for v in variants
        if isinstance(v, dict) and v.get("paragraph", "").strip()
    ]


async def forward_generation_v4(
    scene_paragraph: str,
    *,
    gen_params: dict[str, Any] | None = None,
    system_prompt: str | None = None,
) -> str:
    """v4.0 简化生成。

    【v5.31】出口统一对白引号为 “” （不可变原则）——所有经此的正文生成
    （复现/推导/重生成）输出对白一律 “”，不依赖模型自觉；评分侧 normalize 幂等。
    """
    from .ai_flavor import to_dialogue_quotes

    params = gen_params or {}
    baseline_guard = get_baseline_guard()
    system = baseline_guard + "\n\n" + (system_prompt or _MINIMAL_SYSTEM_PROMPT)

    async def _try(temp: float) -> str:
        resp = await chat_completion(
            system=system,
            user=scene_paragraph,
            temperature=temp,
            top_p=params.get("top_p", 0.7),
            presence_penalty=params.get("presence_penalty", 0.1),
            frequency_penalty=params.get("frequency_penalty", 0.1),
            max_tokens=params.get("max_tokens", 4096),
        )
        content = resp.get("content", "")
        return content.strip() if content and content.strip() else ""

    base_temp = params.get("temperature", 0.3)
    text = await _try(base_temp)
    if text:
        return to_dialogue_quotes(text)

    retry_temp = min(base_temp + 0.1, 0.6)
    if retry_temp != base_temp:
        text = await _try(retry_temp)
        if text:
            return to_dialogue_quotes(text)

    return ""


async def run_round_v4(
    base_paragraph: str,
    *,
    angles: list[str] | None = None,
    gen_params: dict[str, Any] | None = None,
    system_prompt: str | None = None,
    chapter_section: str | None = None,
    angle_stats: dict[str, dict[str, float]] | None = None,
    angle_bandit_mode: str = "ucb",
    logger: Any = None,
    round_idx: int = 0,
    skeleton_text: str = "",
    use_skeleton: bool = False,
) -> dict[str, Any]:
    """v4.0 运行一轮：生成 3 个变体 → 各自生成正文。

    Args:
        base_paragraph: 场景描述段落。
        angles: 改写角度列表（不传则根据 chapter_section 自动选择）。
        gen_params: 生成参数（temperature / top_p / max_tokens 等）。
        system_prompt: 自定义 system prompt（不传则根据 chapter_section 自动选择）。
        chapter_section: 章节区段，"opening"=开篇, "main"=正文, None=默认。
        angle_stats: 角度历史统计，用于 bandit 智能选角。None 时随机。
        angle_bandit_mode: "ucb" | "epsilon_greedy"
        logger: 可选 RunLogger 实例，用于记录训练日志。
        round_idx: 轮次序号（配合 logger 使用）。
        skeleton_text: 情节骨架文本（use_skeleton=True 时作为 user prompt 替代 base_paragraph）。
        use_skeleton: 是否使用骨架模式。
    """
    from .templates import pick_random_angles, select_angles_bandit
    from .fixed_prompts import get_minimal_system_prompt as _get_min_sp, get_variant_angles
    from .config import SETTINGS

    t_round_start = time.time()
    perf_phases: dict[str, float] = {}

    angle_selection_mode = "manual"
    selected_angles = angles or []
    arm_stats_list = []

    # 根据 chapter_section 选择角度
    if angles is None:
        if chapter_section:
            section_angles = get_variant_angles(chapter_section)
            pool = section_angles if section_angles else None
        else:
            pool = None

        if angle_stats:
            angles, angle_selection_mode = select_angles_bandit(
                3, pool=pool, stats=angle_stats, mode=angle_bandit_mode,
            )
            # 构造 arm_stats 列表供日志用
            for name, stat in angle_stats.items():
                arm_stats_list.append({
                    "name": name,
                    "mean": stat.get("mean", 0),
                    "variance": stat.get("variance", 0),
                    "n": stat.get("count", 0),
                    "win_rate": stat.get("win_rate", 0),
                })
        else:
            angles = pick_random_angles(3, pool=pool)
            angle_selection_mode = "random"

    selected_angles = angles or []

    # 根据 chapter_section 选择 system prompt
    if system_prompt is None:
        system_prompt = _get_min_sp(chapter_section)

    # 构建完整 system prompt（早构建，日志里用）
    baseline_guard = get_baseline_guard()
    full_system_prompt = baseline_guard + "\n\n" + (system_prompt or _MINIMAL_SYSTEM_PROMPT)

    # === 日志：轮次开始 + bandit 决策 ===
    if logger:
        logger.log_round_start(
            round_idx=round_idx,
            base_paragraph=base_paragraph,
            system_prompt=full_system_prompt,
            angles=selected_angles,
            angle_strategy=angle_selection_mode,
            gen_params=gen_params or {},
            skeleton_text=skeleton_text,
            use_skeleton=use_skeleton,
        )
        if angle_stats is not None and arm_stats_list:
            # 记录 bandit 决策快照
            ucb_values = {}
            if angle_selection_mode == "bandit_ucb":
                total = sum(s.get("n", 0) for s in arm_stats_list)
                import math
                for s in arm_stats_list:
                    n = s.get("n", 0)
                    if n > 0 and total > 0:
                        ucb_values[s["name"]] = s.get("mean", 0) + 1.0 * math.sqrt(
                            math.log(total) / n
                        )
            logger.log_bandit_decision(
                round_idx=round_idx,
                mode=angle_selection_mode,
                selected_angles=selected_angles,
                arm_stats=arm_stats_list,
                ucb_values=ucb_values,
                ucb_c=1.0,
                total_pulls=sum(s.get("n", 0) for s in arm_stats_list),
            )

    # === 变体改写 ===
    t_variant_start = time.time()
    user_prompt_text = skeleton_text if (use_skeleton and skeleton_text) else base_paragraph
    variants_raw = await generate_variants(user_prompt_text, angles or [])
    perf_phases["variant_generation"] = round((time.time() - t_variant_start) * 1000)

    # === 正向生成 ===
    t_forward_start = time.time()
    model_name = SETTINGS.ark_model_pro or ""

    async def _gen_one(index: int, variant: dict[str, str]) -> dict[str, Any]:
        para = variant.get("paragraph", "")
        angle = variant.get("angle", "")
        if not para:
            result = {
                "index": index, "angle": angle, "paragraph": "",
                "generated_text": "", "timing_ms": 0, "token_usage": {},
            }
            return result
        # 【v5.30】独立正文生成注入 AI 味禁令（无原文基准 → 独立版），生成端防患
        from .ai_flavor import ai_flavor_ban_block
        _para = para + "\n\n" + ai_flavor_ban_block(standalone=True)
        t0 = time.time()
        text = await forward_generation_v4(_para, gen_params=gen_params, system_prompt=system_prompt)
        timing_ms = round((time.time() - t0) * 1000)

        result = {
            "index": index,
            "angle": angle,
            "paragraph": para,
            "generated_text": text,
            "timing_ms": timing_ms,
            "token_usage": {},  # 暂时空，LLM 封装层统一补充
            "model": model_name,
        }
        return result

    tasks = [_gen_one(i, v) for i, v in enumerate(variants_raw)]
    results = await asyncio.gather(*tasks)
    perf_phases["forward_generation"] = round((time.time() - t_forward_start) * 1000)

    # === 日志：变体生成完成 ===
    if logger:
        for res in results:
            logger.log_variant_generated(
                round_idx=round_idx,
                variant_idx=res.get("index", 0),
                angle_name=res.get("angle", ""),
                variant_prompt=res.get("paragraph", ""),
                generated_text=res.get("generated_text", ""),
                timing_ms=res.get("timing_ms", 0),
                model=res.get("model", ""),
                token_usage=res.get("token_usage", {}),
            )

    total_ms = round((time.time() - t_round_start) * 1000)

    # === 日志：性能拆解 ===
    if logger:
        perf_phases_ms = {k: int(v) for k, v in perf_phases.items()}
        logger.log_perf_breakdown(
            round_idx=round_idx,
            phases=perf_phases_ms,
            total_ms=total_ms,
        )

    return {
        "ok": True,
        "base_paragraph": base_paragraph,
        "angles": angles,
        "angle_selection_mode": angle_selection_mode,
        "variants": list(results),
        "system_prompt_used": full_system_prompt,
        "user_prompt_used": user_prompt_text,
        "chapter_section": chapter_section,
        "timing_ms": total_ms,
        "perf_phases": perf_phases,
    }


def set_minimal_system_prompt(prompt: str) -> None:
    """覆盖默认的极简 system prompt。"""
    global _MINIMAL_SYSTEM_PROMPT
    _MINIMAL_SYSTEM_PROMPT = prompt


def get_minimal_system_prompt() -> str:
    """获取当前的极简 system prompt。"""
    return _MINIMAL_SYSTEM_PROMPT


# ---------------------------------------------------------------------------
# v5 统一优化入口（合并优化器 + 逆向推理）
# ---------------------------------------------------------------------------

async def optimize_v5(
    target_text: str,
    *,
    mode: str = "quality",
    skeleton_text: str | None = None,
    chapter_section: str | None = None,
    gen_params: dict | None = None,
    source_file: str = "",
    store=None,
) -> dict:
    """v5 统一优化函数。

    将 prompt_inference 的贝叶斯优化 + 多阶段精炼引入 optimizer，
    替代旧的 v3 手工迭代模式。

    参数：
        target_text: 目标章节原文（≥100 字符）
        mode: "fast" | "standard" | "quality"
        skeleton_text: 可选，情节骨架
        chapter_section: "opening" | "main" | None
        gen_params: 生成参数覆盖
        source_file: 源文件路径
        store: ExperienceStore 实例

    返回：
        和 prompt_inference.reverse_infer() 相同格式的字典：
        {v_target, skeleton_text, candidates, optimization, vp_correlation_hint}
    """
    from .prompt_inference import reverse_infer as _v5_reverse_infer

    return await _v5_reverse_infer(
        target_text=target_text,
        mode=mode,
        skeleton_text=skeleton_text,
        chapter_section=chapter_section,
        gen_params=gen_params,
        source_file=source_file,
        store=store,
    )
