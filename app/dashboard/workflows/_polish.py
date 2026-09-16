"""
润色步骤 —— 从 workflows.py 拆分。
"""
from __future__ import annotations

import json
from pathlib import Path

from ..services.agent_runner import AnthropicAgentRunner, _default_pro_model
from ..agents.agents import make_task_emitters
from ..core.constants import FreedomLevel, DEFAULT_FREEDOM_LEVEL
from ..services.hard_constraints import build_constrained_prompt
from ..services.task_manager import TASKS, Task
from ..services.workflow_status import _get_chapter_stage

from ._decision import _decision_check
from ._utils import _read_write_reference


async def _run_polish_for_chapter(
    task: Task, *, project_root: Path, chapter: int, model: str | None,
    temperature: float | None = None,
    freedom_level: FreedomLevel = DEFAULT_FREEDOM_LEVEL,
    tmp_dir: Path | None = None,
) -> Path | None:
    """基于 final_audit.json 润色排版本章正文，返回修改后的文件路径。"""
    # 决策检查
    if not await _decision_check(
        task,
        "polish",
        operation_type="chapter_polish",
        step_description=f"根据审查结果润色第 {chapter} 章正文，修复问题并优化表达",
    ):
        raise RuntimeError("润色步骤被用户跳过")

    td = tmp_dir if tmp_dir else project_root / ".ainovel" / "tmp"
    final_audit = td / "final_audit.json"
    audit_data = None
    if final_audit.is_file():
        try:
            audit_data = json.loads(final_audit.read_text(encoding="utf-8"))
            audit_chapter = audit_data.get("chapter") or audit_data.get("target_chapter") if isinstance(audit_data, dict) else None
            if audit_chapter is not None and audit_chapter != chapter:
                await TASKS.emit(task, {
                    "phase": "stderr",
                    "line": f"final_audit.json 对应第 {audit_chapter} 章，与目标 {chapter} 不符，将忽略审查数据",
                })
                audit_data = None
        except (OSError, json.JSONDecodeError) as exc:
            await TASKS.emit(task, {
                "phase": "stderr",
                "line": f"final_audit.json 解析失败: {exc}，将仅用润色指南运行",
            })
            audit_data = None
    else:
        await TASKS.emit(task, {
            "phase": "stderr",
            "line": f"第 {chapter} 章缺少 final_audit.json，将仅用润色指南运行（建议先跑 review 获取完整审查数据）",
        })

    # Polish 的输入必须是 AI 原稿，输出到 AI生成/第NNNN章-润色.md
    ai_dir = project_root / "AI生成"
    ai_dir.mkdir(exist_ok=True)
    stage_info = _get_chapter_stage(project_root, chapter)
    stage = stage_info.get("stage", "none")
    if stage == "finalized":
        await TASKS.emit_error(task, f"第 {chapter} 章已 finalize，禁止 Polish 覆盖精修稿")
        raise RuntimeError("已 finalize 的章节不能 Polish")
    if stage == "polished_final":
        await TASKS.emit_error(task, f"第 {chapter} 章已有 -精修.md，请先 finalize 或删除精修稿后再 Polish")
        raise RuntimeError("已有精修稿的章节不能 Polish")

    # 优先使用新结构 AI生成/第NNNN章.md，兼容旧结构 正文/第NNNN章-AI起草.md
    ai_draft = ai_dir / f"第{chapter:04d}章.md"
    if not ai_draft.is_file():
        ai_draft = ai_dir / f"第{chapter}章.md"
    if not ai_draft.is_file():
        legacy = project_root / "正文" / f"第{chapter:04d}章-AI起草.md"
        if legacy.is_file():
            ai_draft = legacy
        else:
            legacy = project_root / "正文" / f"第{chapter}章-AI起草.md"
            if legacy.is_file():
                ai_draft = legacy
    draft = ai_draft if ai_draft.is_file() else stage_info.get("primary_path")
    if not draft:
        await TASKS.emit_error(task, f"找不到第 {chapter} 章 AI 原稿")
        raise RuntimeError("AI 原稿不存在")

    polish_guide = _read_write_reference("polish-guide.md", project_root)
    typesetting = _read_write_reference("typesetting.md", project_root)
    style_adapter = _read_write_reference("style-adapter.md", project_root)

    await TASKS.emit(task, {"phase": "step", "step": "polish", "status": "running"})

    draft_text = draft.read_text(encoding="utf-8")
    on_event, on_token = make_task_emitters(task, "polish")
    current_text = draft_text

    # ── 分层润色：3 轮独立执行，每轮只专注一个目标，避免指令过载 ──

    # Round 1: 修复 audit issues（critical + high 优先）
    await TASKS.emit(task, {"phase": "stdout", "line": "润色 Round 1/3：修复审查问题（critical/high 优先）..."})
    audit_section = ""
    if audit_data and isinstance(audit_data, dict):
        # 只提取 critical/high 级别 issues，不灌全量 audit JSON
        issues = audit_data.get("issues") or []
        if isinstance(issues, list):
            critical_high = [i for i in issues if isinstance(i, dict) and i.get("severity") in ("critical", "high")]
            if critical_high:
                audit_section = (
                    "## 必须修复的问题（仅 critical/high，共 " + str(len(critical_high)) + " 处）\n"
                    + json.dumps(critical_high, ensure_ascii=False, indent=2)[:6000] + "\n\n"
                )
    if not audit_section:
        audit_section = "（无 critical/high 审查问题，跳过修复轮）\n\n"

    r1_prompt = (
        "你是一名网文修改编辑。你的唯一任务是修复以下审查报告中的 critical 和 high 级别问题。\n\n"
        + audit_section +
        "修复规则：\n"
        "- 只修改问题涉及的具体段落/句子，不改其他内容\n"
        "- 不改剧情走向，不改设定，不删伏笔\n"
        "- OOC -> 恢复角色话术/决策边界\n"
        "- POWER_CONFLICT -> 能力回落到合法境界\n"
        "- TIMELINE/LOCATION -> 补时间/空间锚点\n"
        "- CONTINUITY_BREAK -> 补衔接句\n\n"
        "输出要求：直接返回完整 markdown 正文，不要解释，不要省略。"
    )
    r1_system = build_constrained_prompt(r1_prompt, freedom_level)
    r1_runner = AnthropicAgentRunner(
        project_root=project_root,
        model=model or _default_pro_model(),
        system_prompt=r1_system,
        max_tokens=24000,
        allowed_tools=[],
        agent_name="polish-r1",
        on_event=on_event,
        on_token=on_token,
        max_turns=2,
        temperature=temperature,
    )
    r1_text = await r1_runner.run(
        f"修复第 {chapter} 章的问题。以下是待修改原稿：\n\n{current_text}"
    )
    if r1_text and r1_text.strip():
        current_text = r1_text
        await TASKS.emit(task, {"phase": "stdout", "line": "Round 1 完成：审查问题修复"})
    else:
        await TASKS.emit(task, {"phase": "stderr", "line": "Round 1 返回空，保持原稿"})

    # Round 2: Anti-AI 专项检查与改写
    await TASKS.emit(task, {"phase": "stdout", "line": "润色 Round 2/3：Anti-AI 专项检查与改写..."})
    # 提取 polish-guide 中 Anti-AI 核心规则（第 1-2 层 + 对话专项 + 节奏专项）
    r2_prompt = (
        "你是一名网文反AI味编辑。你的唯一任务是对本章正文做 Anti-AI 专项改写。\n\n"
        "## 必须改写的 AI 痕迹\n\n"
        "### 高风险词汇（命中即改写，不可保留）\n"
        "- 万能副词：缓缓、淡淡、微微、轻轻、静静、默默、悄悄、慢慢、渐渐、暗暗 -> 删掉副词，用前置动作或结果暗示\n"
        "- 情绪直述：他感到X、心中五味杂陈、百感交集、不由得感慨、内心震撼 -> 改为生理反应+微动作+环境映射\n"
        "- 动作套话：皱起眉头、叹了口气、深吸一口气、缓缓开口、沉声说道、眸中闪过、心中一凛 -> 改为角色专属微动作\n"
        "- 总结归纳：综合、总之、总而言之、由此可见、可以看出 -> 直接删掉，不总结\n"
        "- 枚举模板：首先/其次/最后、第一/第二/第三 -> 删掉编号词，用自然过渡\n"
        "- 内心套话：心中暗道、心中一凛、心中暗自思量 -> 删掉引号词，直接写内心句\n"
        "- 转折模板：话虽如此、殊不知、然而就在这时、更令人惊讶的是 -> 直接写转折，不用模板\n\n"
        "### 句式规则\n"
        "- 禁止连续三句「主语+谓语+宾语」同构句\n"
        "- 禁止「起因->经过->结果->感悟」四段闭环 -> 删掉感悟句\n"
        "- 禁止段末总结句（「他终于明白了」「她显然很生气」）-> 用动作或对话结尾\n"
        "- 每3-5段至少出现一次句长变化（短句打断/插入动作）\n\n"
        "### 对话专项\n"
        "- 有人抢话、沉默、答非所问\n"
        "- said tag ≤30%，优先用前置动作替代\n"
        "- 每句对话有潜台词（试探/回避/施压/诱导），不做信息宣讲\n"
        "- 角色说话节奏不同（有人啰嗦、有人惜字如金）\n\n"
        "### 节奏专项\n"
        "- 紧张时句子变短、变碎\n"
        "- 信息密集段落和留白段落交替\n"
        "- 禁止每段3-5句均匀分布\n\n"
        "### 改写算法\n"
        "1. 抽象情绪句 -> 生理反应+当下意图+下一动作\n"
        "2. 结论句 -> 事实细节+代价/风险+决策\n"
        "3. 连续说明句(≥3) -> 对白/动作/反问混排\n"
        "4. 连续同构句(≥3) -> 至少打断1句为短句\n"
        "5. 情绪标签(「他感到X」) -> 生理反应+微动作\n"
        "6. 千人一面反应 -> 按角色性格定制\n\n"
        "输出要求：直接返回完整 markdown 正文，不要解释，不要省略。"
    )
    r2_system = build_constrained_prompt(r2_prompt, freedom_level)
    r2_runner = AnthropicAgentRunner(
        project_root=project_root,
        model=model or _default_pro_model(),
        system_prompt=r2_system,
        max_tokens=24000,
        allowed_tools=[],
        agent_name="polish-r2",
        on_event=on_event,
        on_token=on_token,
        max_turns=2,
        temperature=temperature,
    )
    r2_text = await r2_runner.run(
        f"对第 {chapter} 章做 Anti-AI 专项改写。以下是待处理原稿：\n\n{current_text}"
    )
    if r2_text and r2_text.strip():
        current_text = r2_text
        await TASKS.emit(task, {"phase": "stdout", "line": "Round 2 完成：Anti-AI 专项改写"})
    else:
        await TASKS.emit(task, {"phase": "stderr", "line": "Round 2 返回空，保持当前文本"})

    # Round 3: 排版 + 风格适配
    await TASKS.emit(task, {"phase": "stdout", "line": "润色 Round 3/3：排版规则 + 风格适配..."})
    r3_prompt = (
        "你是一名网文排版和风格适配编辑。你的唯一任务是对本章正文做排版修正和风格微调。\n\n"
        "## 排版规则\n" + (typesetting or "（无特殊排版规则）") + "\n\n"
        "## 风格适配\n" + (style_adapter or "（无特殊风格要求）") + "\n\n"
        "## 约束\n"
        "- 只做排版和风格微调，不改剧情、不改设定、不改字数\n"
        "- 不改已经修复好的表达，不改对话内容\n"
        "- 段落过长（超过150字）的酌情拆分\n"
        "- 标点节奏：省略号/感叹号每段最多1次\n\n"
        "输出要求：直接返回完整 markdown 正文，不要解释，不要省略。"
    )
    r3_system = build_constrained_prompt(r3_prompt, freedom_level)
    r3_runner = AnthropicAgentRunner(
        project_root=project_root,
        model=model or _default_pro_model(),
        system_prompt=r3_system,
        max_tokens=24000,
        allowed_tools=[],
        agent_name="polish-r3",
        on_event=on_event,
        on_token=on_token,
        max_turns=2,
        temperature=temperature,
    )
    polished_text = await r3_runner.run(
        f"对第 {chapter} 章做排版和风格微调。以下是待处理原稿：\n\n{current_text}"
    )

    # 空产物保护：润色返回空（agent 未产出正文）则视为失败，由调用方回退用草稿
    if not polished_text or not polished_text.strip():
        await TASKS.emit(task, {
            "phase": "stderr",
            "line": f"polish 返回空正文，回退用草稿继续",
        })
        raise RuntimeError("polish 产出空正文")

    polished_path = ai_dir / f"第{chapter:04d}章-润色.md"
    polished_path.write_text(polished_text, encoding="utf-8")
    await TASKS.emit(task, {
        "phase": "step", "step": "polish", "status": "done",
        "path": str(polished_path.relative_to(project_root)),
        "preview": polished_text[:300],
    })
    return polished_path
