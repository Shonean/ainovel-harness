"""
训练运行日志系统 v2.0 —— 全量化可追溯日志体系

7 大类事件：
  1. session        会话层（开始/结束/配置快照）
  2. training       训练循环（轮次/变体/用户选择）
  3. algorithm      量化引擎（bandit/BO/帕累托/偏好/收敛）
  4. capability     辅助能力（冷启动/骨架/逆向推理/文档画像/存经验）
  5. interaction    用户交互（prompt修改/参数调整/推荐覆盖）
  6. resource       资源层（LLM调用/错误/性能拆解）
  7. system         系统层（错误/警告/信息）

目录结构：
  logs/sessions/{date}/{session_id}_{source}.jsonl    v4.x 手动训练会话
  logs/runs/{timestamp}_{exp_id}.jsonl               v3.x 自动优化
  logs/tasks/{timestamp}_{type}_{source}.jsonl       v5.0 一次性任务
"""
from __future__ import annotations

import json
import os
import platform
import sys
import time
import traceback
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# ============================================================
# 版本信息（每次日志都带，便于未来回溯分析）
# ============================================================

LOGGER_VERSION = "2.0.0"
SOFTWARE_VERSION = "5.0.0"  # prompt-harness 系统版本


def _get_env_snapshot() -> dict[str, Any]:
    """采集环境快照（写入 session_start）。"""
    return {
        "python_version": sys.version,
        "platform": platform.platform(),
        "os": os.name,
        "hostname": platform.node(),
        "log_version": LOGGER_VERSION,
        "software_version": SOFTWARE_VERSION,
    }


# ============================================================
# 基础工具
# ============================================================

def _ts() -> str:
    """ISO 格式 UTC 时间戳（毫秒精度）。"""
    return datetime.now(timezone.utc).isoformat()


def _ts_ms() -> int:
    """毫秒级时间戳（用于计算耗时更方便）。"""
    return int(time.time() * 1000)


def _new_id(prefix: str = "") -> str:
    """生成短 ID。"""
    s = uuid.uuid4().hex[:12]
    return f"{prefix}_{s}" if prefix else s


def _safe_str(s: str, max_len: int = 200) -> str:
    """安全截断字符串。"""
    if not s:
        return ""
    if len(s) <= max_len:
        return s
    return s[:max_len] + f"...[{len(s)}]"


# ============================================================
# 主 Logger 类
# ============================================================

class RunLogger:
    """
    结构化训练日志记录器。

    两种使用模式：
    1. 会话模式（v4.x 手动训练）：session 贯穿多轮，多次调用不同事件方法
       logger = RunLogger().start_session(source_file="xxx.txt", ...)
       logger.log_round_start(...)
       logger.log_user_choice(...)
       logger.end_session(...)

    2. 运行模式（v3.x 自动训练 / 一次性任务）：
       logger = RunLogger().start_run(exp_id="...", source_file="...")
       logger.log_round(...)
       logger.finish(...)
    """

    def __init__(self, log_dir: Path | None = None) -> None:
        self.base_dir = log_dir or Path(__file__).resolve().parent.parent / "logs"
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self._file = None
        self._log_path: Path | None = None

        # 会话/运行上下文
        self.session_id: str = ""
        self.run_id: str = ""
        self.source_file: str = ""
        self.chapter_section: str = ""
        self.started_at: str = ""
        self._started_ms: int = 0

        # 轮次计数器
        self._round_seq: int = 0

        # 性能计时栈（用于 perf_breakdown）
        self._timers: dict[str, int] = {}

    # ============================================================
    # 文件管理
    # ============================================================

    def _open_file(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._file = open(path, "w", encoding="utf-8")
        self._log_path = path

    def _close_file(self) -> None:
        if self._file:
            self._file.flush()
            self._file.close()
            self._file = None

    def _emit(self, record: dict[str, Any]) -> None:
        """写一条事件到 JSONL。"""
        if not self._file:
            return
        # 保证有 ts 和层级 id
        record.setdefault("ts", _ts())
        record.setdefault("ts_ms", _ts_ms())
        if self.session_id:
            record.setdefault("session_id", self.session_id)
        if self.run_id:
            record.setdefault("run_id", self.run_id)
        if self.source_file:
            record.setdefault("source_file", self.source_file)
        if self.chapter_section:
            record.setdefault("chapter_section", self.chapter_section)

        self._file.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
        self._file.flush()

    # ============================================================
    # 模式 1：会话模式（v4.x 手动训练）
    # ============================================================

    def start_session(
        self,
        source_file: str,
        chapter_section: str = "",
        user_id: str = "",
        config_snapshot: dict[str, Any] | None = None,
        doc_profile: dict[str, Any] | None = None,
    ) -> str:
        """
        开始一个训练会话，返回 session_id。

        写入 session_start 事件，包含：
        - 环境快照（Python/平台/版本）
        - 配置快照（综合分权重、UCB参数、贝叶斯超参等所有可调参数）
        - 文档画像（如有）
        """
        self.session_id = _new_id("sess")
        self.source_file = source_file
        self.chapter_section = chapter_section
        self.started_at = _ts()
        self._started_ms = _ts_ms()

        date_str = datetime.now(timezone.utc).strftime("%Y%m%d")
        safe_name = Path(source_file).stem[:40] if source_file else "nosource"
        fname = f"{self.session_id}_{safe_name}.jsonl"
        path = self.base_dir / "sessions" / date_str / fname
        self._open_file(path)

        self._emit({
            "event": "session_start",
            "session_id": self.session_id,
            "user_id": user_id,
            "started_at": self.started_at,
            "env": _get_env_snapshot(),
            "config": config_snapshot or {},
            "doc_profile": doc_profile or {},
            "log_path": str(path),
        })
        return self.session_id

    def end_session(
        self,
        total_rounds: int = 0,
        final_best_score: float | None = None,
        summary: dict[str, Any] | None = None,
        reason: str = "manual",  # manual | completed | closed | error
    ) -> None:
        """结束会话，写入汇总。"""
        duration_ms = _ts_ms() - self._started_ms
        self._emit({
            "event": "session_end",
            "session_id": self.session_id,
            "ended_at": _ts(),
            "duration_ms": duration_ms,
            "duration_sec": round(duration_ms / 1000, 2),
            "total_rounds": total_rounds,
            "final_best_score": final_best_score,
            "end_reason": reason,
            "summary": summary or {},
        })
        self._close_file()

    # ============================================================
    # 模式 2：运行模式（v3.x 自动训练，兼容旧接口）
    # ============================================================

    def start(self, exp_id: str = "", source_file: str = "") -> str:
        """v3.x 兼容：开始一个 run。"""
        ts_str = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        log_id = f"{ts_str}_{exp_id}" if exp_id else ts_str
        self.run_id = log_id
        self.source_file = source_file
        self.started_at = _ts()
        self._started_ms = _ts_ms()

        path = self.base_dir / "runs" / f"{log_id}.jsonl"
        self._open_file(path)

        self._emit({
            "event": "start",
            "exp_id": exp_id,
            "source_file": source_file,
            "started_at": self.started_at,
            "env": _get_env_snapshot(),
        })
        return log_id

    def log_round(self, round_idx: int, progress: dict[str, Any]) -> None:
        """v3.x 兼容：记录一轮。"""
        if not self._file:
            return
        gp = progress.get("gen_params", {})
        self._emit({
            "event": "round",
            "round": round_idx,
            "combined_score": progress.get("combined_score"),
            "best_combined_sofar": progress.get("best_combined_sofar"),
            "score_delta": progress.get("score_delta"),
            "historical_best_score": progress.get("historical_best_score"),
            "improved_this_round": progress.get("improved_this_round"),
            "stagnant_rounds": progress.get("stagnant_rounds"),
            "style_composite": progress.get("style_composite"),
            "structure_composite": progress.get("structure_composite"),
            "plot_fidelity_composite": progress.get("plot_fidelity_composite"),
            "improvements": progress.get("improvements", {}),
            "regressions": progress.get("regressions", {}),
            "gen_params": {k: v for k, v in gp.items()
                           if k in ("temperature", "top_p", "max_tokens",
                                    "presence_penalty", "frequency_penalty")},
            "change_summary": _safe_str(progress.get("change_summary") or "", 200),
        })

    def log_error(self, error: str, traceback_str: str = "") -> None:
        """通用错误日志。"""
        self._emit({
            "event": "error",
            "error": error,
            "traceback": traceback_str,
        })

    def finish(self, **result: Any) -> None:
        """v3.x 兼容：完成。"""
        duration_ms = _ts_ms() - self._started_ms
        self._emit({
            "event": "finish",
            "best_score": result.get("combined_score"),
            "rounds": result.get("rounds"),
            "best_variant": result.get("best_variant_key"),
            "duration_ms": duration_ms,
            "duration_sec": round(duration_ms / 1000, 2),
        })
        self._close_file()

    # ============================================================
    # 模式 3：任务模式（v5.0 逆向推理、参数搜索等一次性任务）
    # ============================================================

    def start_task(
        self,
        task_type: str,  # "reverse_infer" | "pareto_search" | "skeleton_extract"
        source_file: str = "",
        chapter_section: str = "",
        params: dict[str, Any] | None = None,
    ) -> str:
        """开始一个一次性任务。"""
        ts_str = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        task_id = _new_id("task")
        self.run_id = task_id
        self.source_file = source_file
        self.chapter_section = chapter_section
        self.started_at = _ts()
        self._started_ms = _ts_ms()

        safe_name = Path(source_file).stem[:30] if source_file else "nosource"
        fname = f"{ts_str}_{task_type}_{safe_name}.jsonl"
        path = self.base_dir / "tasks" / fname
        self._open_file(path)

        self._emit({
            "event": "task_start",
            "task_id": task_id,
            "task_type": task_type,
            "started_at": self.started_at,
            "env": _get_env_snapshot(),
            "params": params or {},
        })
        return task_id

    def end_task(
        self,
        success: bool,
        result_summary: dict[str, Any] | None = None,
        error: str = "",
    ) -> None:
        """结束任务。"""
        duration_ms = _ts_ms() - self._started_ms
        self._emit({
            "event": "task_end",
            "success": success,
            "error": error,
            "duration_ms": duration_ms,
            "duration_sec": round(duration_ms / 1000, 2),
            "result_summary": result_summary or {},
        })
        self._close_file()

    # ============================================================
    # 【第 2 类】训练循环事件
    # ============================================================

    def log_round_start(
        self,
        round_idx: int,
        base_paragraph: str,
        system_prompt: str,
        angles: list[str],
        angle_strategy: str,  # "random" | "bandit_ucb" | "bandit_egreedy" | "manual"
        gen_params: dict[str, Any],
        skeleton_text: str = "",
        use_skeleton: bool = False,
    ) -> None:
        """轮次开始：记录输入 + 角度 + 参数。"""
        self._round_seq = round_idx
        self._emit({
            "event": "round_start",
            "round_idx": round_idx,
            "base_paragraph": _safe_str(base_paragraph, 500),
            "base_paragraph_len": len(base_paragraph),
            "system_prompt": _safe_str(system_prompt, 1000),
            "system_prompt_len": len(system_prompt),
            "angles": angles,
            "angle_strategy": angle_strategy,
            "gen_params": gen_params,
            "use_skeleton": use_skeleton,
            "skeleton_text": _safe_str(skeleton_text, 500) if skeleton_text else "",
            "skeleton_len": len(skeleton_text) if skeleton_text else 0,
        })

    def log_variant_generated(
        self,
        round_idx: int,
        variant_idx: int,
        angle_name: str,
        variant_prompt: str,
        generated_text: str,
        v_vector: dict[str, Any] | None = None,
        scores: dict[str, Any] | None = None,
        timing_ms: int = 0,
        model: str = "",
        token_usage: dict[str, int] | None = None,
    ) -> None:
        """单变体生成完成：完整 prompt + 文本 + V 向量 + 分数 + 资源消耗。"""
        self._emit({
            "event": "variant_generated",
            "round_idx": round_idx,
            "variant_idx": variant_idx,
            "angle_name": angle_name,
            "variant_prompt": _safe_str(variant_prompt, 1000),
            "variant_prompt_len": len(variant_prompt),
            "generated_text": _safe_str(generated_text, 2000),
            "generated_text_len": len(generated_text),
            "v_vector": v_vector or {},
            "scores": scores or {},
            "timing_ms": timing_ms,
            "model": model,
            "token_usage": token_usage or {},
        })

    def log_round_complete(
        self,
        round_idx: int,
        variants: list[dict[str, Any]],
        recommended_idx: int,
        recommendation_reason: str = "",
        sorting_scores: list[float] | None = None,
        convergence_info: dict[str, Any] | None = None,
    ) -> None:
        """轮次完成：3 变体排序 + 系统推荐 + 收敛状态。"""
        # 变体精简信息（完整文本已在 variant_generated 里）
        compact_variants = []
        for i, v in enumerate(variants):
            compact_variants.append({
                "idx": i,
                "angle": v.get("angle", ""),
                "composite_score": v.get("composite_score"),
                "char_similarity": v.get("char_similarity"),
                "v_cosine": v.get("v_cosine"),
                "mean_abs_delta_pct": v.get("mean_abs_delta_pct"),
                "text_len": len(v.get("generated_text", "")),
            })
        self._emit({
            "event": "round_complete",
            "round_idx": round_idx,
            "variants": compact_variants,
            "recommended_idx": recommended_idx,
            "recommendation_reason": recommendation_reason,
            "sorting_scores": sorting_scores,
            "convergence": convergence_info or {},
        })

    def log_user_choice(
        self,
        round_idx: int,
        chosen_idx: int,
        recommended_idx: int,
        think_time_ms: int = 0,  # 从结果呈现到选择的时间
        override_reason: str = "",
        user_note: str = "",
    ) -> None:
        """
        用户选择记录。

        重点：think_time_ms 反映决策难度；
        chosen != recommended 时说明推荐被 override，是重要学习信号。
        """
        is_override = chosen_idx != recommended_idx
        self._emit({
            "event": "user_choice",
            "round_idx": round_idx,
            "chosen_idx": chosen_idx,
            "recommended_idx": recommended_idx,
            "is_override": is_override,
            "think_time_ms": think_time_ms,
            "override_reason": override_reason,
            "user_note": user_note,
        })

    # ============================================================
    # 【第 3 类】量化引擎事件
    # ============================================================

    def log_bandit_decision(
        self,
        round_idx: int,
        mode: str,  # "ucb" | "egreedy" | "fallback_random"
        selected_angles: list[str],
        arm_stats: list[dict[str, Any]],  # 每个 arm 的统计
        ucb_values: dict[str, float] | None = None,
        ucb_c: float = 1.0,
        epsilon: float = 0.0,
        total_pulls: int = 0,
    ) -> None:
        """
        角度 bandit 决策快照。
        arm_stats: [{name, mean, variance, n, win_rate}, ...]
        未来可以用来离线评估 bandit 策略好坏。
        """
        self._emit({
            "event": "bandit_decision",
            "round_idx": round_idx,
            "mode": mode,
            "selected_angles": selected_angles,
            "arm_stats": arm_stats,
            "ucb_values": ucb_values or {},
            "ucb_c": ucb_c,
            "epsilon": epsilon,
            "total_pulls": total_pulls,
        })

    def log_bo_suggest(
        self,
        iteration: int,
        suggested_p_vector: list[float],
        acquisition_value: float,
        gp_hyperparams: dict[str, Any],  # length_scale, signal_var, noise
        best_score_so_far: float,
        n_observations: int,
    ) -> None:
        """贝叶斯优化：下一个采样点建议。"""
        self._emit({
            "event": "bo_suggest",
            "iteration": iteration,
            "suggested_p_vector": suggested_p_vector,
            "acquisition_value": acquisition_value,
            "gp_hyperparams": gp_hyperparams,
            "best_score_so_far": best_score_so_far,
            "n_observations": n_observations,
        })

    def log_bo_observe(
        self,
        iteration: int,
        p_vector: list[float],
        score: float,
        score_breakdown: dict[str, float] | None = None,
    ) -> None:
        """贝叶斯优化：加入一个观测点。"""
        self._emit({
            "event": "bo_observe",
            "iteration": iteration,
            "p_vector": p_vector,
            "score": score,
            "score_breakdown": score_breakdown or {},
        })

    def log_pareto_sample(
        self,
        all_samples: list[dict[str, Any]],  # 所有 LHS 采样点（完整数据）
        pareto_front_indices: list[int],
        recommendations: dict[str, Any],  # 保守/平衡/创新
        param_ranges: dict[str, Any],
        n_samples: int,
    ) -> None:
        """
        帕累托搜索：全量采样数据。
        全量保存，便于以后重新计算前沿、重新选档。
        """
        self._emit({
            "event": "pareto_search",
            "n_samples": n_samples,
            "param_ranges": param_ranges,
            "all_samples": all_samples,
            "pareto_front_indices": pareto_front_indices,
            "recommendations": recommendations,
        })

    def log_preference_update(
        self,
        round_idx: int,
        preference_vector: list[float],
        confidence: list[float],
        n_rounds: int,
        interpretation: str = "",
    ) -> None:
        """偏好向量每轮更新。"""
        self._emit({
            "event": "preference_update",
            "round_idx": round_idx,
            "preference_vector": preference_vector,
            "confidence": confidence,
            "n_rounds": n_rounds,
            "interpretation": interpretation,
        })

    def log_convergence_check(
        self,
        round_idx: int,
        window_scores: list[float],
        delta: float,
        is_converged: bool,
        status: str,  # "training" | "near_convergence" | "converged"
        learning_curve: dict[str, Any] | None = None,
    ) -> None:
        """收敛检测：窗口值、delta、状态、学习曲线拟合。"""
        self._emit({
            "event": "convergence_check",
            "round_idx": round_idx,
            "window_scores": window_scores,
            "delta": delta,
            "is_converged": is_converged,
            "status": status,
            "learning_curve": learning_curve or {},
        })

    # ============================================================
    # 【第 4 类】辅助能力事件
    # ============================================================

    def log_cold_start(
        self,
        v_target: dict[str, Any],
        n_candidates_total: int,
        top3_candidates: list[dict[str, Any]],
        embedding_hit_count: int,
        v_rerank_count: int,
        method: str = "embedding_v_rerank",
    ) -> None:
        """冷启动：检索过程 + top-3 完整数据。"""
        self._emit({
            "event": "cold_start",
            "v_target": v_target,
            "method": method,
            "n_candidates_total": n_candidates_total,
            "embedding_hit_count": embedding_hit_count,
            "v_rerank_count": v_rerank_count,
            "top3_candidates": top3_candidates,
        })

    def log_skeleton_extract(
        self,
        original_text_len: int,
        skeleton_text: str,
        beats: list[str],
        characters: list[str],
        scene_setting: str,
        model: str = "",
        timing_ms: int = 0,
        compression_ratio: float = 0.0,
    ) -> None:
        """骨架提取：原文→骨架的完整过程。"""
        self._emit({
            "event": "skeleton_extract",
            "original_text_len": original_text_len,
            "skeleton_text": skeleton_text,
            "skeleton_len": len(skeleton_text),
            "compression_ratio": round(compression_ratio, 3) if compression_ratio
                                  else round(len(skeleton_text) / max(original_text_len, 1), 3),
            "beats": beats,
            "characters": characters,
            "scene_setting": scene_setting,
            "model": model,
            "timing_ms": timing_ms,
        })

    def log_reverse_infer_step(
        self,
        step: int,
        total_steps: int,
        phase: str,  # "init_knn" | "init_lhs" | "bo_loop" | "post_process"
        detail: dict[str, Any] | None = None,
    ) -> None:
        """逆向推理：每一步进展。"""
        self._emit({
            "event": "reverse_infer_step",
            "step": step,
            "total_steps": total_steps,
            "phase": phase,
            "detail": detail or {},
        })

    def log_doc_profile(
        self,
        n_chapters: int,
        n_clusters: int,
        elbow_k: int,
        clusters: list[dict[str, Any]],
        overall_v_stats: dict[str, Any],
    ) -> None:
        """文档画像：聚类 + 风格分区。"""
        self._emit({
            "event": "doc_profile",
            "n_chapters": n_chapters,
            "n_clusters": n_clusters,
            "elbow_k": elbow_k,
            "clusters": clusters,
            "overall_v_stats": overall_v_stats,
        })

    def log_active_sample_recommend(
        self,
        strategy: str,
        trained_indices: list[int],
        recommended: list[dict[str, Any]],
        phase_breakdown: dict[str, Any] | None = None,
    ) -> None:
        """主动采样推荐。"""
        self._emit({
            "event": "active_sample_recommend",
            "strategy": strategy,
            "trained_count": len(trained_indices),
            "trained_indices": trained_indices,
            "recommended": recommended,
            "phase_breakdown": phase_breakdown or {},
        })

    def log_save_experience(
        self,
        experience_id: str,
        v_target: dict[str, Any],
        score: float,
        is_duplicate: bool,
        duplicate_of: str = "",
        stored_fields: list[str] | None = None,
    ) -> None:
        """保存经验到经验库。"""
        self._emit({
            "event": "save_experience",
            "experience_id": experience_id,
            "v_target": v_target,
            "score": score,
            "is_duplicate": is_duplicate,
            "duplicate_of": duplicate_of,
            "stored_fields": stored_fields or [],
        })

    def log_vp_correlation_hint(
        self,
        top_correlations: list[dict[str, Any]],
        n_observations_used: int,
        new_pairs_count: int = 0,
    ) -> None:
        """V-P 相关提示（元层可解释性）。"""
        self._emit({
            "event": "vp_correlation_hint",
            "top_correlations": top_correlations,
            "n_observations_used": n_observations_used,
            "new_pairs_count": new_pairs_count,
        })

    # ============================================================
    # 【第 5 类】用户交互事件
    # ============================================================

    def log_prompt_edit(
        self,
        section: str,  # "opening" | "main"
        field: str,    # "system_prompt" | "baseline_guard" | "role_prompt" | "angle_xxx"
        old_value: str,
        new_value: str,
        edit_source: str = "manual",  # manual | cold_start_apply | reverse_infer_apply | auto
    ) -> None:
        """用户手动修改 prompt（或自动应用）。diff 式记录。"""
        self._emit({
            "event": "prompt_edit",
            "section": section,
            "field": field,
            "old_value": _safe_str(old_value, 500),
            "new_value": _safe_str(new_value, 500),
            "old_len": len(old_value),
            "new_len": len(new_value),
            "edit_source": edit_source,
        })

    def log_param_change(
        self,
        param_name: str,
        old_value: Any,
        new_value: Any,
        change_source: str = "manual",
    ) -> None:
        """生成参数调整。"""
        self._emit({
            "event": "param_change",
            "param_name": param_name,
            "old_value": old_value,
            "new_value": new_value,
            "change_source": change_source,
        })

    def log_recommendation_override(
        self,
        round_idx: int,
        rec_type: str,  # "variant" | "angle" | "params" | "chapter"
        recommended: Any,
        chosen: Any,
        user_reason: str = "",
    ) -> None:
        """
        推荐覆盖事件（用户选择了系统没推荐的）。
        这是最有价值的学习信号之一。
        """
        self._emit({
            "event": "recommendation_override",
            "round_idx": round_idx,
            "rec_type": rec_type,
            "recommended": recommended,
            "chosen": chosen,
            "user_reason": user_reason,
        })

    # ============================================================
    # 【第 6 类】资源层事件
    # ============================================================

    def log_llm_call(
        self,
        model: str,
        endpoint: str = "",
        prompt_len: int = 0,
        completion_len: int = 0,
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        total_tokens: int = 0,
        latency_ms: int = 0,
        cache_hit: bool = False,
        retry_count: int = 0,
        call_type: str = "",  # "generate" | "skeleton_extract" | "analyze" | etc.
        status: str = "success",  # success | error | timeout
        error: str = "",
    ) -> None:
        """
        每一次 LLM 调用都记录。
        用于成本统计、性能分析、错误归因。
        """
        self._emit({
            "event": "llm_call",
            "model": model,
            "endpoint": endpoint,
            "prompt_len": prompt_len,
            "completion_len": completion_len,
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": total_tokens,
            "latency_ms": latency_ms,
            "cache_hit": cache_hit,
            "retry_count": retry_count,
            "call_type": call_type,
            "status": status,
            "error": error,
        })

    def log_llm_error(
        self,
        model: str,
        error_type: str,
        error_message: str,
        retry_count: int,
        max_retries: int,
        will_retry: bool,
        call_type: str = "",
    ) -> None:
        """LLM 错误详情（重试期间每次都记）。"""
        self._emit({
            "event": "llm_error",
            "model": model,
            "error_type": error_type,
            "error_message": _safe_str(error_message, 300),
            "retry_count": retry_count,
            "max_retries": max_retries,
            "will_retry": will_retry,
            "call_type": call_type,
        })

    def log_perf_breakdown(
        self,
        round_idx: int,
        phases: dict[str, int],  # {phase_name: ms}
        total_ms: int,
    ) -> None:
        """一轮训练的性能拆解。"""
        self._emit({
            "event": "perf_breakdown",
            "round_idx": round_idx,
            "phases": phases,
            "total_ms": total_ms,
            "total_sec": round(total_ms / 1000, 2),
        })

    # ============================================================
    # 计时器工具
    # ============================================================

    def start_timer(self, name: str) -> None:
        """启动一个命名计时器。"""
        self._timers[name] = _ts_ms()

    def end_timer(self, name: str) -> int:
        """结束计时器，返回耗时 ms。"""
        if name not in self._timers:
            return 0
        elapsed = _ts_ms() - self._timers[name]
        del self._timers[name]
        return elapsed

    # ============================================================
    # 通用事件
    # ============================================================

    def log(self, event: str, data: dict[str, Any] | None = None) -> None:
        """通用事件记录。"""
        if not self._file:
            return
        self._emit({"event": event, **(data or {})})

    def log_exception(self, exc: Exception, context: str = "") -> None:
        """捕获的异常。"""
        tb = traceback.format_exc()
        self._emit({
            "event": "exception",
            "error_type": type(exc).__name__,
            "error_message": str(exc),
            "context": context,
            "traceback": tb,
        })

    # ============================================================
    # 类方法：日志查询
    # ============================================================

    @staticmethod
    def list_logs(
        log_dir: Path | None = None,
        category: str = "all",  # all | sessions | runs | tasks
        limit: int = 50,
        source_filter: str = "",
    ) -> list[dict[str, Any]]:
        """列出日志文件（支持分类和过滤）。"""
        base = log_dir or Path(__file__).resolve().parent.parent / "logs"
        if not base.is_dir():
            return []

        categories = ["sessions", "runs", "tasks"] if category == "all" else [category]
        all_files: list[tuple[Path, str]] = []  # (path, category)

        for cat in categories:
            cat_dir = base / cat
            if not cat_dir.is_dir():
                continue
            # sessions 有子目录（按日期），runs/tasks 直接是文件
            if cat == "sessions":
                for date_dir in sorted(cat_dir.iterdir(), reverse=True):
                    if date_dir.is_dir():
                        for f in date_dir.glob("*.jsonl"):
                            all_files.append((f, cat))
            else:
                for f in cat_dir.glob("*.jsonl"):
                    all_files.append((f, cat))

        # 按修改时间倒序
        all_files.sort(key=lambda x: x[0].stat().st_mtime, reverse=True)

        if source_filter:
            all_files = [
                (f, c) for f, c in all_files
                if source_filter.lower() in f.stem.lower()
            ]

        results = []
        for f, cat in all_files[:limit]:
            stat = f.stat()
            # 读第一条事件获取元数据
            first_event = {}
            try:
                with open(f, "r", encoding="utf-8") as fh:
                    first_line = fh.readline()
                    if first_line.strip():
                        first_event = json.loads(first_line)
            except Exception:
                pass

            # 读最后一条事件获取状态
            last_event = {}
            try:
                # 小文件直接读，大文件读末尾
                if stat.st_size < 100_000:
                    lines = f.read_text(encoding="utf-8").strip().split("\n")
                    if lines:
                        last_event = json.loads(lines[-1])
                else:
                    with open(f, "rb") as fh:
                        fh.seek(-2048, 2)
                        tail = fh.read().decode("utf-8", errors="ignore")
                        tail_lines = [l for l in tail.split("\n") if l.strip()]
                        if tail_lines:
                            last_event = json.loads(tail_lines[-1])
            except Exception:
                pass

            results.append({
                "filename": f.name,
                "filepath": str(f.relative_to(base)),
                "category": cat,
                "size": stat.st_size,
                "size_kb": round(stat.st_size / 1024, 1),
                "created_at": datetime.fromtimestamp(stat.st_ctime, tz=timezone.utc).isoformat(),
                "modified_at": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(),
                "session_id": first_event.get("session_id", ""),
                "run_id": first_event.get("run_id", ""),
                "source_file": first_event.get("source_file", ""),
                "started_at": first_event.get("started_at", ""),
                "status": _infer_status(first_event, last_event),
                "last_event": last_event.get("event", ""),
            })
        return results

    @staticmethod
    def read_log(
        filepath: str,
        log_dir: Path | None = None,
        event_filter: list[str] | None = None,
        limit: int = 0,
    ) -> list[dict[str, Any]]:
        """读取一个日志文件，支持事件类型过滤。"""
        base = log_dir or Path(__file__).resolve().parent.parent / "logs"
        f = base / filepath
        if not f.is_file():
            return []

        events: list[dict[str, Any]] = []
        with open(f, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    evt = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if event_filter and evt.get("event") not in event_filter:
                    continue
                events.append(evt)
                if limit and len(events) >= limit:
                    break
        return events

    @staticmethod
    def get_session_summary(filepath: str, log_dir: Path | None = None) -> dict[str, Any]:
        """获取一个会话的汇总信息（事件计数、分数曲线等）。"""
        events = RunLogger.read_log(filepath, log_dir=log_dir)
        if not events:
            return {}

        event_counts: dict[str, int] = {}
        round_scores: list[dict[str, Any]] = []
        round_indices = set()
        total_tokens = 0
        llm_calls = 0
        errors = 0

        for evt in events:
            name = evt.get("event", "unknown")
            event_counts[name] = event_counts.get(name, 0) + 1

            # 收集所有出现过的轮次号
            if "round_idx" in evt and isinstance(evt["round_idx"], int):
                round_indices.add(evt["round_idx"])

            if name == "round_complete":
                variants = evt.get("variants", [])
                if variants:
                    best = max(v.get("composite_score", 0) or 0 for v in variants)
                    round_scores.append({
                        "round_idx": evt.get("round_idx"),
                        "best_composite_score": best,
                    })

            if name == "llm_call":
                llm_calls += 1
                total_tokens += evt.get("total_tokens", 0) or 0

            if name in ("error", "exception", "llm_error"):
                errors += 1

        first = events[0]
        last = events[-1]

        return {
            "filepath": filepath,
            "session_id": first.get("session_id", ""),
            "source_file": first.get("source_file", ""),
            "chapter_section": first.get("chapter_section", ""),
            "started_at": first.get("started_at", first.get("ts", "")),
            "ended_at": last.get("ts", ""),
            "status": _infer_status(first, last),
            "event_counts": event_counts,
            "total_rounds": max(len(round_scores), len(round_indices)),
            "best_score": round_scores[-1]["best_composite_score"] if round_scores else None,
            "score_curve": round_scores,
            "llm_calls": llm_calls,
            "total_tokens": total_tokens,
            "error_count": errors,
        }

    @staticmethod
    def list_sessions(
        log_dir: Path | None = None,
        source_filter: str = "",
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """列出所有训练会话（带汇总）。"""
        logs = RunLogger.list_logs(
            log_dir=log_dir,
            category="sessions",
            limit=limit,
            source_filter=source_filter,
        )
        # 为每个会话加载汇总
        results = []
        for log in logs:
            try:
                summary = RunLogger.get_session_summary(log["filepath"], log_dir=log_dir)
                results.append({**log, "summary": summary})
            except Exception:
                results.append(log)
        return results


def _infer_status(first: dict[str, Any], last: dict[str, Any]) -> str:
    """从首末事件推断日志状态。"""
    if not last:
        return "empty"
    last_event = last.get("event", "")
    if last_event in ("session_end", "finish", "task_end"):
        if not last.get("success", True) or last_event == "error":
            return "failed"
        return "completed"
    if last_event in ("error", "exception"):
        return "error"
    # 开始了但没结束 → 可能在进行中，也可能异常中断
    first_event = first.get("event", "")
    if first_event in ("session_start", "start", "task_start"):
        return "running"  # 或 "interrupted"，但我们没法区分
    return "unknown"
