"""
_prompt_preview.py - Prompt 审阅台：纯组装预览（不调 LLM）

build_preview(project_root, stage, chapter, batch) 返回某阶段「将发出」的
system_prompt + user_input + 分段 segments + 最近一次实际发出的捕获记录。

- draft：复用 _draft._assemble_draft_prompt（与运行同函数，预览==实际）
- 其余 stage：system = resolve agent.md / SKILL.md + build_constrained_prompt；
  user_input = 填充后的可覆盖片段模板
- 动态运行时数据（任务书/范文/草稿路径）用存档或占位填充，并在 notes 标注
- captured_latest：从 _prompt_log 取该 (stage, chapter) 最近一条实际记录
"""
from __future__ import annotations

from pathlib import Path

from ..routes.actions import SCRIPTS_DIR
from ..services.agent_runner import (
    _default_pro_model, _default_review_model, _default_character_model,
)
from ..agents.agents import _read_agent_prompt
from ..core.constants import FreedomLevel, DEFAULT_FREEDOM_LEVEL
from ..services.hard_constraints import build_constrained_prompt, build_3line_header, build_freedom_level_instruction

from ._channel import load_channel, is_opening_chapter
from ._draft import _assemble_draft_prompt
from ._prompt_fragments import resolve_fragment, fill_template
from ._prompt_log import latest_for
from ._step_runners import _build_skill_system_prompt
from ._utils import _read_skill


STAGES = [
    {"stage": "plan", "label": "📋 Plan 规划大纲", "per_chapter": False,
     "description": "规划批次章纲（SKILL.md 为 system，user_input 指定卷/章范围）"},
    {"stage": "context", "label": "🧭 Context 任务书", "per_chapter": True,
     "description": "为章节组装写作任务书（context-agent.md 为 system）"},
    {"stage": "character", "label": "🎭 Character 角色脚本", "per_chapter": True,
     "description": "预生成 prose 对话场景（character-dialogue-agent.md，人物模型）"},
    {"stage": "draft", "label": "✍️ Draft 起草正文", "per_chapter": True,
     "description": "8 层锚定 prompt 起草本章正文（身份/章纲/锚点/质感/范文/自检）"},
    {"stage": "reviewer", "label": "🔍 Reviewer 审查", "per_chapter": True,
     "description": "审查草稿查找内容问题与 AI 痕迹（reviewer.md，独立审查模型）"},
    {"stage": "critic", "label": "🪟 Critic 窗口审查", "per_chapter": True,
     "description": "切窗审查 AI 味（critic-agent.md，每窗口一次调用）"},
    {"stage": "data", "label": "🗃️ Data 数据提取", "per_chapter": True,
     "description": "提取情节/人物/设定结构化数据（data-agent.md）"},
]


def _saved_task_book(project_root: Path, chapter: int) -> str:
    """读上次运行存档的写作任务书；无则返空。"""
    p = project_root / "大纲" / f"第{chapter:04d}章-写作任务书.md"
    if p.is_file():
        try:
            return p.read_text(encoding="utf-8")
        except OSError:
            return ""
    return ""


def _task_book_or_placeholder(project_root: Path, chapter: int) -> tuple[str, str]:
    """返回 (ctx_text, note)。有存档用存档，否则占位。"""
    tb = _saved_task_book(project_root, chapter)
    if tb:
        return tb, "任务书取自上次运行存档（大纲/第NNNN章-写作任务书.md）"
    return "（尚未生成任务书；运行 context 阶段后此处填充实际任务书内容）", "⚠ 尚无任务书存档，此处为占位"


def _hard_constraint_segments(freedom_level: FreedomLevel = DEFAULT_FREEDOM_LEVEL) -> list[dict]:
    """硬约束注入段（system，只读）。"""
    return [
        {"name": "硬约束头（三大定律+人称/对话）", "kind": "system", "editable": False,
         "source": "hard_constraints.py", "content": build_3line_header(freedom_level),
         "note": "代码内置"},
        {"name": "自由度级别指令", "kind": "system", "editable": False,
         "source": "hard_constraints.py", "content": build_freedom_level_instruction(freedom_level),
         "note": "代码内置"},
    ]


def _preview_draft(project_root: Path, chapter: int) -> dict:
    ctx_text, note = _task_book_or_placeholder(project_root, chapter)
    asm = _assemble_draft_prompt(
        project_root, chapter, ctx_text,
        character_script_injection="",
        freedom_level=DEFAULT_FREEDOM_LEVEL, model=None,
    )
    captured = latest_for(project_root, "draft", chapter)
    notes = [note]
    if not asm["directive_dict"]:
        notes.append("⚠ 未找到 chapter_NNN.json 章纲 directive，§1/§2 为空（先 plan + register-chapters）")
    return {
        "system_prompt": asm["system_prompt"],
        "user_input": asm["user_message"],
        "segments": asm["segments"],
        "model": asm["resolved_model"],
        "is_opening": asm["is_opening"],
        "captured_latest": captured,
        "notes": notes,
    }


def _preview_agent_stage(
    project_root: Path, stage: str, chapter: int, *,
    agent_file: str, user_input_fragment: str,
    fill_kw: dict, model: str,
    extra_user_note: str = "",
) -> dict:
    """通用 agent 型 stage 预览（context/character/reviewer/critic/data）。"""
    raw_agent = _read_agent_prompt(agent_file, project_root)
    # context-agent 有占位符需预替换（与 _context.py 一致）
    if stage == "context":
        raw_agent = raw_agent.replace("${SCRIPTS_DIR}", str(SCRIPTS_DIR))
        raw_agent = raw_agent.replace("${PROJECT_ROOT}", str(project_root))
        raw_agent = raw_agent.replace("{project_root}", str(project_root))
        raw_agent = raw_agent.replace("{NNNN}", f"{chapter:04d}")
    system_prompt = build_constrained_prompt(raw_agent, DEFAULT_FREEDOM_LEVEL) if raw_agent else ""
    user_input = fill_template(resolve_fragment(project_root, user_input_fragment), **fill_kw)

    segments = [
        {"name": f"Agent 提示词（{agent_file}）", "kind": "system", "editable": True,
         "source": f"agents/{agent_file}", "content": raw_agent,
         "note": "在「📁 Prompt 文件」页编辑（测试书本地副本）"},
        *[
            {**seg, "content": (seg["content"] if isinstance(seg.get("content"), str) else "")}
            for seg in _hard_constraint_segments()
        ],
        {"name": "user_input 模板", "kind": "user", "editable": True,
         "source": user_input_fragment, "content": user_input,
         "note": "可编辑片段（{占位符} 运行时填充）" + (f"；{extra_user_note}" if extra_user_note else "")},
    ]
    captured = latest_for(project_root, stage, chapter)
    return {
        "system_prompt": system_prompt,
        "user_input": user_input,
        "segments": segments,
        "model": model,
        "captured_latest": captured,
        "notes": [],
    }


def _preview_reviewer(project_root: Path, chapter: int) -> dict:
    channel_profile = load_channel(project_root)
    is_opening = is_opening_chapter(chapter, channel_profile)
    review_injection = ""
    if is_opening:
        review_injection = resolve_fragment(project_root, "review/opening_injection.md")
    channel_review = getattr(channel_profile, "review_criteria", "") or ""
    if channel_review.strip():
        review_injection += f"\n【题材特有审查标准】\n{channel_review}\n"
    review_results_path = project_root / ".ainovel" / "tmp" / f"chapter_{chapter:04d}" / "review_results.json"
    res = _preview_agent_stage(
        project_root, "reviewer", chapter,
        agent_file="reviewer.md",
        user_input_fragment="stages/reviewer_user_input.md",
        fill_kw={
            "chapter": chapter,
            "content_path": f"AI生成/第{chapter:04d}章.md（运行时为草稿路径）",
            "project_root": str(project_root),
            "review_results_path": str(review_results_path),
            "review_injection": review_injection,
        },
        model=_default_review_model(),
        extra_user_note=("开篇章触发开篇审查注入" if is_opening else "非开篇章，无开篇注入"),
    )
    if is_opening:
        # 插入开篇注入段（可编辑片段）
        res["segments"].insert(2, {
            "name": "开篇审查注入", "kind": "user", "editable": True,
            "source": "review/opening_injection.md", "content": resolve_fragment(project_root, "review/opening_injection.md"),
            "note": "可编辑片段（仅开篇章注入）",
        })
    return res


def _preview_critic(project_root: Path, chapter: int) -> dict:
    return _preview_agent_stage(
        project_root, "critic", chapter,
        agent_file="critic-agent.md",
        user_input_fragment="stages/critic_user_input.md",
        fill_kw={
            "chapter": chapter,
            "window_index": 1,
            "window_count": "N（运行时按草稿长度，最多12）",
            "window_text": "（运行时按窗口填充：草稿切窗后的第1个~250字片段）",
        },
        model=_default_review_model(),
        extra_user_note="critic 每窗口一次调用，预览展示第1窗",
    )


def _preview_context(project_root: Path, chapter: int) -> dict:
    return _preview_agent_stage(
        project_root, "context", chapter,
        agent_file="context-agent.md",
        user_input_fragment="stages/context_user_input.md",
        fill_kw={"chapter": chapter, "project_root": str(project_root), "scripts_dir": str(SCRIPTS_DIR)},
        model=_default_pro_model(),
    )


def _preview_character(project_root: Path, chapter: int) -> dict:
    ctx_text, note = _task_book_or_placeholder(project_root, chapter)
    return _preview_agent_stage(
        project_root, "character", chapter,
        agent_file="character-dialogue-agent.md",
        user_input_fragment="stages/character_user_input.md",
        fill_kw={
            "chapter": chapter,
            "chapter_padded": f"{chapter:04d}",
            "project_root": str(project_root),
            "scripts_dir": str(SCRIPTS_DIR),
            "ctx_text": ctx_text,
        },
        model=_default_character_model(),
        extra_user_note=note,
    )


def _preview_data(project_root: Path, chapter: int) -> dict:
    tmp_dir = project_root / ".ainovel" / "tmp" / f"chapter_{chapter:04d}"
    return _preview_agent_stage(
        project_root, "data", chapter,
        agent_file="data-agent.md",
        user_input_fragment="stages/data_user_input.md",
        fill_kw={
            "chapter": chapter,
            "draft_path": f"AI生成/第{chapter:04d}章.md（运行时为草稿路径）",
            "project_root": str(project_root),
            "fulfill": str(tmp_dir / "fulfillment_result.json"),
            "disambig": str(tmp_dir / "disambiguation_result.json"),
            "extract": str(tmp_dir / "extraction_result.json"),
        },
        model=_default_pro_model(),
    )


def _preview_plan(project_root: Path, batch: str | None) -> dict:
    """plan 预览。batch 形如 '1-10'；缺省用 1-10。"""
    batch = batch or "1-10"
    try:
        start_s, end_s = batch.split("-")
        start, end = int(start_s), int(end_s)
    except Exception:
        start, end = 1, 10
    volume = (start - 1) // 30 + 1
    chapter_count = end - start + 1

    system_prompt = _build_skill_system_prompt("ainovel-plan", project_root, DEFAULT_FREEDOM_LEVEL)
    skill_md_raw = _read_skill("ainovel-plan", project_root)
    user_input = fill_template(
        resolve_fragment(project_root, "stages/plan_user_input.md"),
        volume=volume, start=start, end=end, chapter_count=chapter_count, root=str(project_root),
    )

    segments = [
        {"name": "SKILL.md（plan skill 指令）", "kind": "system", "editable": True,
         "source": "plan/SKILL.md", "content": skill_md_raw,
         "note": "在「📁 Prompt 文件」页编辑（plan 类别）"},
        {"name": "环境上下文 + 工具约定", "kind": "system", "editable": False,
         "source": "_step_runners._build_skill_system_prompt", "content": "# 环境上下文 ...（代码内置前缀）",
         "note": "代码内置"},
        *[
            {**seg, "content": (seg["content"] if isinstance(seg.get("content"), str) else "")}
            for seg in _hard_constraint_segments()
        ],
        {"name": "user_input 模板", "kind": "user", "editable": True,
         "source": "stages/plan_user_input.md", "content": user_input,
         "note": f"可编辑片段；当前批次 第{start}-{end}章（卷{volume}）"},
    ]
    captured = latest_for(project_root, "plan", None)
    return {
        "system_prompt": system_prompt,
        "user_input": user_input,
        "segments": segments,
        "model": _default_pro_model(),
        "batch": f"{start}-{end}",
        "captured_latest": captured,
        "notes": [f"plan 为批次规划，当前预览批次 第{start}-{end}章（卷{volume}）"],
    }


def build_preview(project_root: Path, stage: str, chapter: int = 1, batch: str | None = None) -> dict:
    """主入口：返回某阶段的预览组装结果。"""
    if stage == "draft":
        return _preview_draft(project_root, chapter)
    if stage == "context":
        return _preview_context(project_root, chapter)
    if stage == "character":
        return _preview_character(project_root, chapter)
    if stage == "reviewer":
        return _preview_reviewer(project_root, chapter)
    if stage == "critic":
        return _preview_critic(project_root, chapter)
    if stage == "data":
        return _preview_data(project_root, chapter)
    if stage == "plan":
        return _preview_plan(project_root, batch)
    return {"system_prompt": "", "user_input": "", "segments": [], "model": "",
            "captured_latest": None, "notes": [f"未知阶段: {stage}"]}
