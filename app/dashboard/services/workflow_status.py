"""
workflow_status.py — 章节工作流状态机（防跳步核心）。

每条 step.status ∈ {"done", "ready", "blocked", "skipped"}：
  done    — 该步产物已落盘且未失效。
  ready   — 上游都 done，可以执行了。
  blocked — 上游缺产物，不允许执行。
  skipped — 在当前模式下被跳过（如 --minimal 跳 review）。

判定一律基于**文件存在性 + JSON 字段空值**，没有 LLM 调用，<10ms 返回。
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Callable

from fastapi import APIRouter


# ---------------------------------------------------------------------------
# 个体判定
# ---------------------------------------------------------------------------

def _chapter_json(root: Path, chapter: int) -> Path:
    return root / ".story-system" / "chapters" / f"chapter_{chapter:03d}.json"


def _commit_json(root: Path, chapter: int) -> Path:
    return root / ".story-system" / "commits" / f"chapter_{chapter:03d}.commit.json"


def _find_chapter_md(root: Path, chapter: int) -> Path | None:
    """按优先级返回当前章节的工作稿路径：精修 > 润色 > AI起草。"""
    stage_info = _get_chapter_stage(root, chapter)
    return stage_info.get("primary_path")


def _get_chapter_stage(root: Path, chapter: int) -> dict[str, Any]:
    """返回章节当前阶段与相关文件路径。

    新结构：
      - 正文/ 只放 -精修.md
      - AI生成/ 放 AI原稿（.md）和 -润色.md
    阶段优先级：finalized > 精修 > 润色 > AI原稿 > 未起草
    """
    body_dir = root / "正文"
    ai_dir = root / "AI生成"

    polished_final = body_dir / f"第{chapter:04d}章-精修.md"
    if not polished_final.is_file():
        polished_final = body_dir / f"第{chapter}章-精修.md"

    polished = ai_dir / f"第{chapter:04d}章-润色.md"
    if not polished.is_file():
        polished = ai_dir / f"第{chapter}章-润色.md"

    draft = ai_dir / f"第{chapter:04d}章.md"
    if not draft.is_file():
        draft = ai_dir / f"第{chapter}章.md"

    # 兼容旧结构：正文/第NNNN章-AI起草.md 视为 AI原稿
    legacy_draft = body_dir / f"第{chapter:04d}章-AI起草.md"
    if not legacy_draft.is_file():
        legacy_draft = body_dir / f"第{chapter}章-AI起草.md"
    if legacy_draft.is_file() and not draft.is_file():
        draft = legacy_draft

    # 兼容旧结构：正文/第NNNN章-润色.md 视为润色稿
    legacy_polished = body_dir / f"第{chapter:04d}章-润色.md"
    if not legacy_polished.is_file():
        legacy_polished = body_dir / f"第{chapter}章-润色.md"
    if legacy_polished.is_file() and not polished.is_file():
        polished = legacy_polished

    found: dict[str, Path] = {}
    if polished_final.is_file():
        found["polished_final"] = polished_final
    if polished.is_file():
        found["polished"] = polished
    if draft.is_file():
        found["draft"] = draft

    state_path = root / ".ainovel" / "state.json"
    finalized = False
    finalized_at: str | None = None
    if state_path.is_file():
        try:
            state = json.loads(state_path.read_text(encoding="utf-8"))
            entry = (state.get("chapters_finalized") or {}).get(str(chapter))
            if isinstance(entry, dict) and entry.get("finalized"):
                finalized = True
                finalized_at = entry.get("finalized_at")
        except (OSError, json.JSONDecodeError):
            pass

    if finalized:
        stage = "finalized"
    elif "polished_final" in found:
        stage = "polished_final"
    elif "polished" in found:
        stage = "polished"
    elif "draft" in found:
        stage = "drafted"
    else:
        stage = "none"

    # 为 UI 生成可读的文件位置提示
    if stage == "finalized":
        display_hint = "正文/（已精修入库）"
    elif stage == "polished_final":
        display_hint = "正文/（精修稿，待入库）"
    elif stage == "polished":
        display_hint = "AI生成/（润色稿，待 finalize 移入正文）"
    elif stage == "drafted":
        display_hint = "AI生成/（AI 原稿，待润色后 finalize）"
    else:
        display_hint = "尚未起草"

    return {
        "stage": stage,
        "finalized": finalized,
        "finalized_at": finalized_at,
        "files": {k: v.relative_to(root).as_posix() for k, v in found.items()},
        "primary_path": found.get("polished_final") or found.get("polished") or found.get("draft"),
        "display_hint": display_hint,
    }


def _check_plan(root: Path, chapter: int) -> tuple[str, str]:
    """plan 阶段判定：chapter_NNN.json（结构化 directive）> 章纲 markdown > 缺失。

    - JSON 存在且 directive 非空 → done
    - JSON 缺失但章纲 markdown 存在 → ready（可绕过直接起草，但建议跑 plan 生成 JSON）
    - 两者皆无 → blocked
    """
    f = _chapter_json(root, chapter)
    if f.is_file():
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            return "blocked", f"chapter_directive JSON 解析失败：{exc}"
        directive = data.get("chapter_directive") or {}
        if not isinstance(directive, dict) or not directive:
            return "blocked", "chapter_directive 为空 — 请先跑 /ainovel-plan 该卷"
        nonempty_keys = [k for k, v in directive.items() if v not in (None, "", [], {}, "TODO")]
        if not nonempty_keys:
            return "blocked", "chapter_directive 字段全空"
        return "done", f"已填字段：{', '.join(nonempty_keys[:5])}"

    # Fallback：检查章纲 markdown 是否存在
    outline_md = root / "大纲" / f"第{chapter:04d}章-章纲.md"
    if not outline_md.is_file():
        outline_md = root / "大纲" / f"第{chapter}章-章纲.md"
    if outline_md.is_file():
        return "ready", "章纲 markdown 存在（可起草，建议先跑 ainovel-plan 生成 JSON 获得更完整的 directive）"

    return "blocked", f"缺 {f.relative_to(root)} 且无章纲 markdown"


def _check_preflight(root: Path) -> tuple[str, str]:
    """preflight 不留产物，作为辅助检查按钮总是可点 —— 标为 optional。"""
    state = root / ".ainovel" / "state.json"
    if not state.is_file():
        return "blocked", "缺 .ainovel/state.json"
    return "optional", "可执行（辅助检查，不影响主流程）"


def _check_context(root: Path, chapter: int) -> tuple[str, str]:
    """context-agent 任务书状态：plan done→optional（全量质量），plan ready→optional（章纲 fallback），plan blocked→blocked。"""
    plan_st, _ = _check_plan(root, chapter)
    if plan_st == "done":
        return "optional", "辅助 — 有完整 JSON directive，调 context-agent 看任务书"
    if plan_st == "ready":
        return "optional", "辅助 — 章纲可用（JSON 缺失），调 context-agent 将直接读取章纲"
    return "blocked", "plan 未完成且无章纲"


def _check_draft(root: Path, chapter: int) -> tuple[str, str]:
    stage_info = _get_chapter_stage(root, chapter)
    primary = stage_info.get("primary_path")
    if primary:
        return "done", f"已落盘 {primary.name}（阶段：{stage_info['stage']}）"
    plan_st, _ = _check_plan(root, chapter)
    if plan_st == "blocked":
        return "blocked", "plan 未完成且无章纲"
    if plan_st == "ready":
        return "ready", "可执行 — 有章纲（建议先跑 plan 生成 JSON 获得更完整的 directive）"
    return "ready", "可执行 — 起草正文"


def _check_review(root: Path, chapter: int) -> tuple[str, str]:
    """review.done = final_audit.json 存在且目标章号匹配。不比较 mtime（避免 polish 反复触发）。"""
    draft = _find_chapter_md(root, chapter)
    if not draft:
        return "blocked", "尚未起草"
    audit = root / ".ainovel" / "tmp" / "final_audit.json"
    if not audit.is_file():
        return "ready", "可执行 — 跑 5 Track 审查"
    try:
        data = json.loads(audit.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            tgt = data.get("chapter") or data.get("target_chapter")
            if tgt and tgt != chapter:
                return "ready", f"final_audit.json 目标章 {tgt} ≠ {chapter}，需重跑"
    except (OSError, json.JSONDecodeError):
        pass
    return "done", "final_audit.json 已就绪"


def _check_polish(root: Path, chapter: int) -> tuple[str, str]:
    """polish 基于 AI 原稿生成 -润色.md；若已有精修稿则不建议执行。"""
    review_st, _ = _check_review(root, chapter)
    if review_st != "done":
        return "blocked", "review 未完成"
    stage_info = _get_chapter_stage(root, chapter)
    stage = stage_info.get("stage", "none")
    if stage == "finalized":
        return "blocked", "该章已 finalize，Polish 会覆盖精修稿"
    if stage == "polished_final":
        return "blocked", "该章已有 -精修.md，请先走 finalize 或改 AI 原稿"
    if stage == "polished":
        return "optional", "辅助 — 已存在 -润色.md，重新 Polish 将覆盖它"
    return "optional", "辅助 — 按 final_audit 生成 -润色.md"


def _check_commit(root: Path, chapter: int) -> tuple[str, str]:
    cj = _commit_json(root, chapter)
    if cj.is_file():
        try:
            data = json.loads(cj.read_text(encoding="utf-8"))
            status = (data.get("meta") or {}).get("status", "")
            return "done", f"commit status={status}"
        except (OSError, json.JSONDecodeError) as exc:
            return "done", f"commit JSON 存在但解析失败：{exc}"
    # 是否能 commit 取决于 review 是否 done
    review_st, _ = _check_review(root, chapter)
    if review_st != "done":
        return "blocked", "review 未完成"
    return "ready", "可执行 — data-agent + chapter-commit"


def _check_backup(root: Path, chapter: int) -> tuple[str, str]:
    """没有标准记号文件，作为「可选最后步」：commit 完成后即 ready。"""
    commit_st, _ = _check_commit(root, chapter)
    if commit_st != "done":
        return "blocked", "commit 未完成"
    return "ready", "可选 — git 备份"


# ---------------------------------------------------------------------------
# 综合状态
# ---------------------------------------------------------------------------

def build_chapter_status(root: Path, chapter: int) -> dict:
    """返回该章 8 步状态。"""
    steps_def = [
        ("plan", "卷纲与章纲", _check_plan),
        ("preflight", "写前预检", lambda r, c: _check_preflight(r)),
        ("context", "写作任务书 (context-agent)", _check_context),
        ("draft", "起草正文", _check_draft),
        ("review", "Track 1-5 审查", _check_review),
        ("polish", "修补排版", _check_polish),
        ("commit", "三份 artifact + commit", _check_commit),
        ("backup", "Git 备份", _check_backup),
    ]

    steps = []
    prev_blocked = False
    next_action: str | None = None
    for step_id, label, fn in steps_def:
        status, evidence = fn(root, chapter)
        # 「上游 blocked → 本步 blocked」级联（plan/preflight 自查，不受上游影响）
        if status == "ready" and prev_blocked and step_id not in ("plan", "preflight"):
            status = "blocked"
            evidence = "前置步骤未完成"
        steps.append({
            "id": step_id, "label": label, "status": status, "evidence": evidence,
        })
        if status == "ready" and next_action is None:
            next_action = step_id
        if status == "blocked":
            prev_blocked = True

    # 找出本章相关文件路径
    artifacts = {
        "chapter_json": str(_chapter_json(root, chapter).relative_to(root))
            if _chapter_json(root, chapter).is_file() else None,
        "chapter_md": (str(p.relative_to(root)) if (p := _find_chapter_md(root, chapter)) else None),
        "commit_json": (str(_commit_json(root, chapter).relative_to(root))
            if _commit_json(root, chapter).is_file() else None),
        "final_audit": (
            ".ainovel/tmp/final_audit.json"
            if (root / ".ainovel" / "tmp" / "final_audit.json").is_file()
            else None
        ),
    }

    return {
        "chapter": chapter,
        "steps": steps,
        "next_action": next_action,
        "artifacts": artifacts,
    }


def list_chapters(root: Path) -> list[dict]:
    """返回项目内所有已知章节的简表。

    主源：.story-system/chapters/chapter_*.json（ainovel-plan 产物）。
    Fallback：大纲/第*章-章纲.md（未跑 plan 或 JSON 被清理时仍可展示章号）。
    """
    items: list[dict] = []

    # ── 主源：.story-system/chapters/*.json ──
    folder = root / ".story-system" / "chapters"
    if folder.is_dir():
        pat = re.compile(r"^chapter_(\d{3,4})\.json$")
        for f in sorted(folder.glob("chapter_*.json")):
            m = pat.match(f.name)
            if not m:
                continue
            chapter = int(m.group(1))
            directive: dict = {}
            label: str = f.stem
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    directive = data.get("chapter_directive") or {}
                    overrides = data.get("override_allowed") or {}
                    label = overrides.get("chapter_focus") or directive.get("goal") or f.stem
            except (OSError, json.JSONDecodeError):
                pass

            items.append(_build_chapter_item(root, chapter, label, bool(directive)))

    # ── Fallback：大纲/第*章-章纲.md ──
    if not items:
        outline_dir = root / "大纲"
        if outline_dir.is_dir():
            outline_pat = re.compile(r"^第(\d+)章-章纲\.md$")
            for f in sorted(outline_dir.glob("第*章-章纲.md")):
                m = outline_pat.match(f.name)
                if not m:
                    continue
                chapter = int(m.group(1))
                # 尝试从章纲第一行提取标题作为 label
                label = f"第{chapter}章"
                try:
                    first_line = f.read_text(encoding="utf-8").split("\n", 1)[0].strip()
                    if first_line.startswith("# "):
                        # 格式：# 第0001章：九品典史实习生
                        title_part = first_line[2:].strip()
                        if "：" in title_part:
                            label = title_part.split("：", 1)[1].strip() or title_part
                        elif ":" in title_part:
                            label = title_part.split(":", 1)[1].strip() or title_part
                        else:
                            label = title_part
                except (OSError, UnicodeDecodeError):
                    pass

                items.append(_build_chapter_item(root, chapter, str(label)[:60], False))

    return items


def _build_chapter_item(root: Path, chapter: int, label: str, has_directive: bool) -> dict:
    """构建单个章节的列表项 dict，消除主源/fallback 的重复代码。"""
    stage_info = _get_chapter_stage(root, chapter)
    primary_path = stage_info.get("primary_path")
    modified_after_finalized = False
    if stage_info.get("finalized") and primary_path and primary_path.is_file():
        finalized_at = stage_info.get("finalized_at")
        if finalized_at:
            try:
                from datetime import datetime
                fa_ts = datetime.fromisoformat(finalized_at).timestamp()
                modified_after_finalized = primary_path.stat().st_mtime > fa_ts
            except (OSError, ValueError):
                pass

    confirm_plot_path = root / "大纲" / f"第{chapter:04d}章-确认剧情.md"
    has_confirm_plot = confirm_plot_path.is_file()

    # 检测 plan 来源：JSON directive > 章纲 markdown > 无
    outline_md = root / "大纲" / f"第{chapter:04d}章-章纲.md"
    if not outline_md.is_file():
        outline_md = root / "大纲" / f"第{chapter}章-章纲.md"
    if has_directive:
        plan_source = "json"
    elif outline_md.is_file():
        plan_source = "outline_md"
    else:
        plan_source = "none"

    # 提取章纲预览（从 chapter JSON 提取关键字段，不返回完整 directive）
    directive_preview = None
    chapter_json_path = _chapter_json(root, chapter)
    if chapter_json_path.is_file():
        try:
            data = json.loads(chapter_json_path.read_text(encoding="utf-8"))
            cd = data.get("chapter_directive", {}) if isinstance(data, dict) else {}
            if cd:
                preview: dict = {}
                if cd.get("goal"):
                    preview["goal"] = str(cd["goal"])[:100]
                if cd.get("cbn"):
                    preview["cbn"] = str(cd["cbn"])[:120]
                if cd.get("cen"):
                    preview["cen"] = str(cd["cen"])[:120]
                cpns = cd.get("cpns") or []
                if cpns:
                    preview["cpns_count"] = len(cpns)
                beats = cd.get("paragraph_beats") or []
                if beats:
                    preview["beats_count"] = len(beats)
                    preview["beats_sample"] = [str(b)[:80] for b in beats[:3]]
                must_cover = cd.get("must_cover_nodes") or cd.get("must_cover") or []
                if must_cover:
                    preview["must_cover"] = [str(m)[:60] for m in must_cover[:4]]
                forbidden = cd.get("forbidden_zones") or cd.get("forbidden") or []
                if forbidden:
                    preview["forbidden"] = [str(f)[:60] for f in forbidden[:4]]
                if cd.get("hook_type"):
                    preview["hook_type"] = str(cd["hook_type"])[:40]
                if cd.get("strand"):
                    preview["strand"] = str(cd["strand"])[:20]
                if preview:
                    directive_preview = preview
        except (OSError, json.JSONDecodeError):
            pass

    return {
        "chapter": chapter,
        "label": str(label)[:60],
        "has_directive": has_directive,
        "plan_source": plan_source,
        "drafted": bool(primary_path),
        "committed": _commit_json(root, chapter).is_file(),
        "stage": stage_info.get("stage", "none"),
        "finalized": stage_info.get("finalized", False),
        "modified_after_finalized": modified_after_finalized,
        "has_confirm_plot": has_confirm_plot,
        "primary_path": primary_path.relative_to(root).as_posix() if primary_path else None,
        "display_hint": stage_info.get("display_hint", ""),
        "directive_preview": directive_preview,
    }


# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------

def create_router(get_project_root: Callable[[], Path]) -> APIRouter:
    router = APIRouter(prefix="/api/workflow", tags=["workflow"])

    @router.get("/chapter/{chapter}/status")
    def chapter_status(chapter: int):
        return build_chapter_status(get_project_root(), chapter)

    @router.get("/chapters")
    def all_chapters():
        return {"chapters": list_chapters(get_project_root())}

    return router
