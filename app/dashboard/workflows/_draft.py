"""
草稿生成步骤 -- 从 workflows.py 拆分。

v3.6: 抽出 _assemble_draft_prompt() 纯组装函数（不调 LLM），供
_run_draft_for_chapter（运行）与 _prompt_preview.build_preview（预览）共用。
§7 输出指令、§4 质感规范改为读可覆盖片段（_prompt_fragments）。
"""
from __future__ import annotations

import os
from pathlib import Path

from ..services.agent_runner import AnthropicAgentRunner, _default_pro_model
from ..agents.agents import make_task_emitters
from ..core.constants import FreedomLevel, DEFAULT_FREEDOM_LEVEL
from ..services.hard_constraints import build_3line_header, build_freedom_level_instruction, build_writing_guide
from ..services.task_manager import TASKS, Task

from ._anti_ai import (
    _build_anti_ai_injection,    # backward compat, returns ""
    _build_anti_script_injection,  # backward compat, returns ""
    _build_craft_rules,
    _build_chapter_anchors,
    _build_identity,
    _build_pre_output_check,
    _build_style_anchors,
    _check_draft_quality,
)
from ._channel import load_channel, is_opening_chapter, ChannelProfile
from ._decision import _decision_check
from ._prompt_fragments import resolve_fragment, fill_template


def _read_directive(project_root: Path, chapter: int) -> tuple[str, dict, str, str]:
    """读章纲 directive + 元数据。返回 (directive_text, directive_dict, beat_position, genre_from_directive)。"""
    import json
    directive_text = ""
    directive_dict: dict = {}
    beat_position = "单章节拍"
    directive_path = project_root / ".story-system" / "chapters" / f"chapter_{chapter:03d}.json"
    if directive_path.is_file():
        try:
            raw = json.loads(directive_path.read_text(encoding="utf-8"))
            cd = raw.get("chapter_directive", {})
            directive_dict = cd
            beat_position = (
                cd.get("beat_position")
                or raw.get("beat_position")
                or cd.get("节拍位置")
                or raw.get("节拍位置")
                or "单章节拍"
            )
            if cd:
                parts = ["\n## 本章章纲（硬约束，不可偏离）\n"]
                for field, label in [("goal", "目标"), ("obstacles", "阻力"), ("cost", "代价"), ("strand", "Strand")]:
                    if cd.get(field):
                        parts.append(f"- {label}：{cd[field]}")
                beats = cd.get("paragraph_beats") or []
                if beats:
                    parts.append(f"\n### 逐段分镜（{len(beats)} 段，每段对应正文一段，严格执行）")
                    for i, b in enumerate(beats, 1):
                        parts.append(f"{i}. {str(b)}")
                must = cd.get("must_cover") or []
                forbid = cd.get("forbidden") or []
                if must:
                    parts.append(f"\n必须覆盖：{'; '.join(str(x) for x in must)}")
                if forbid:
                    parts.append(f"\n本章禁区：{'; '.join(str(x) for x in forbid)}")
                directive_text = "\n".join(parts)
        except Exception:
            pass
    return directive_text, directive_dict, beat_position, ""


def _read_genre(project_root: Path) -> str:
    """从 state.json 读题材。"""
    import json
    state_path = project_root / ".ainovel" / "state.json"
    if state_path.is_file():
        try:
            state = json.loads(state_path.read_text(encoding="utf-8"))
            return state.get("project", {}).get("genre", "") or state.get("project_info", {}).get("genre", "")
        except Exception:
            pass
    return ""


def _build_struct_constraint(is_opening: bool) -> str:
    """构建本章结构约束（对抗短篇故事综合征）。

    用正向公式替代纯负面禁止：告诉模型"应该怎么写"，而不只是"不要怎么写"。
    不依赖 LLM 理解软约束 —— 用精确的正反示例约束输出。
    """
    return (
        "【本章结构约束——不可违反】\n"
        "1. 开头公式：从上一章结尾的动作/对话/场景直接延续。"
        "假设读者已在故事中、认识所有人物。不铺垫、不介绍、不渲染。\n"
        "   正确：「马车在驿站前停下，李二掀开帘子跳下来。」\n"
        "   错误：「夜幕低垂，星光洒在古老的城墙上，这是一个动荡的时代…」\n"
        "2. 结尾公式：最后一个节拍的动作结果。写完立刻停笔。\n"
        "   结尾不能是：环境描写（灯光/风声/雨声）、微动作特写（手指敲/眼皮跳）、"
        "情绪渲染（他感到/他想起）、悬念预告（他不知道…/这将…）。\n"
        "   结尾应该是：一句动作、一句对话、或一个事实陈述。写完就停。\n"
        "   正确示例：「他把病历合上，推门出去。」「行。」陈迹挂了电话。」\n"
        "   错误示例：「手指在膝盖上轻轻敲了三下，又停下了。」\n"
        "   错误示例：「他不知道，这会是他最后一次见到她。」\n"
        "3. 对话间隙公式：对白之间只放角色对对方话语的即时反应。"
        "不包括：环境观察、物件描写、回忆片段、心理活动。\n"
        "   正确：「他没答话。」「陈迹看了他一眼。」\n"
        "   错误：「陈迹靠在椅背上，目光落在墙上的锦旗上，锦旗边角泛黄…」\n"
        "4. 场景细节公式：只写角色正在互动的东西。不是节拍要求的物件、"
        "不是角色正在用的物件——不写。用角色的行动承载场景信息，不停下来描写环境。\n"
        "5. 全文：本章是长篇小说连载的一部分，不是独立短篇。"
        "不需要在章内完成完整的起承转合。不设悬念铺垫，不写收束结语，只往前推进一步。"
    )


def _assemble_draft_prompt(
    project_root: Path,
    chapter: int,
    ctx_text: str,
    character_script_injection: str = "",
    freedom_level: FreedomLevel = DEFAULT_FREEDOM_LEVEL,
    model: str | None = None,
) -> dict:
    """纯组装：不调 LLM，返回 draft 阶段的 system_prompt + user_message + segments。

    供 _run_draft_for_chapter（运行）与 _prompt_preview（预览）共用，保证
    「预览组装」与「实际发出」byte 一致。

    返回:
      {system_prompt, user_message, resolved_model, is_opening, beat_position,
       directive_dict, segments}
    """
    # ── 读章纲 directive + 元数据 ──
    directive_text, directive_dict, beat_position, _ = _read_directive(project_root, chapter)
    genre = _read_genre(project_root)

    # ── 加载通道配置（Phase 6）──
    channel_profile = load_channel(project_root)
    is_opening = is_opening_chapter(chapter, channel_profile)

    # ── 构建各层注入 ──
    identity_text = _build_identity(genre, beat_position, channel_profile=channel_profile, chapter=chapter)
    chapter_anchors = _build_chapter_anchors(directive_dict)
    craft_rules = _build_craft_rules(channel_profile=channel_profile, project_root=project_root)
    pre_output_check = _build_pre_output_check(beat_position, channel_profile=channel_profile, chapter=chapter)

    # 风格锚点（采样本书历史高分段落，失败静默降级）
    outline_for_sampler = ""
    if directive_dict:
        goal = directive_dict.get("goal", "")
        beats = directive_dict.get("paragraph_beats") or []
        beat_snippets = " ".join(str(b)[:80] for b in beats[:3])
        outline_for_sampler = f"{goal} {beat_snippets}".strip()
    if not outline_for_sampler:
        outline_for_sampler = directive_text[:500] if directive_text else ""
    scripts_dir = str(Path(__file__).resolve().parent.parent.parent / "scripts")
    style_anchors = _build_style_anchors(project_root, chapter, outline_for_sampler, scripts_dir)

        # ── 本章结构约束（在 §1 章纲后注入）──
    struct_constraint = _build_struct_constraint(is_opening)

    # ── §7 输出指令（可覆盖片段）──
    word_range = "2500-3500" if is_opening else "2000-2500"
    output_instruction = fill_template(
        resolve_fragment(project_root, "draft/output_instruction.md"),
        word_range=word_range,
    )

    # ── System Prompt（精简版：身份 + 硬约束 + 正向写作引导）──
    header = build_3line_header(freedom_level)
    freedom_instr = build_freedom_level_instruction(freedom_level)
    writing_guide = build_writing_guide()
    system_prompt_parts = [identity_text, "", header, "", freedom_instr, "", writing_guide]
    system_prompt = "\n".join(system_prompt_parts)

    # ── User Message（§1-§7）──
    user_message_parts = [
        "## §1 本章任务（硬约束，不可偏离）",
        "",
        directive_text,
        "",
        struct_constraint,
        "",
        "## §2 本章独有锚点（硬性要求--必须出现在正文中，不可遗漏）",
        "",
        chapter_anchors,
        "",
        "## §3 故事上下文",
        "",
        ctx_text,
        character_script_injection,
        "",
        "## §4 写作质感规范（决定正文读起来像不像人写的）",
        "",
        craft_rules,
        "",
        "## §5 风格参考",
        "",
        style_anchors,
        "",
        "## §6 落笔前自检（写完本章后逐条确认再输出）",
        "",
        pre_output_check,
        "",
        "## §7 输出要求",
        "",
        output_instruction,
    ]
    user_message = "\n".join(p for p in user_message_parts if p)

    # ── 模型解析：调用方 model > channel model_override > 全局默认 ──
    resolved_model = model or channel_profile.model_override or _default_pro_model()

    # ── segments（供审阅台分解展示；editable 标注可编辑片段）──
    segments = [
        {"name": "身份声明", "kind": "system", "editable": False,
         "source": "channel.identity", "content": identity_text,
         "note": "通道 identity，在「🔀 通道配置」页编辑"},
        {"name": "硬约束头（三大定律+人称/对话）", "kind": "system", "editable": False,
         "source": "hard_constraints.py", "content": header, "note": "代码内置"},
        {"name": "自由度级别指令", "kind": "system", "editable": False,
         "source": "hard_constraints.py", "content": freedom_instr, "note": "代码内置"},
        {"name": "正向写作引导（对白间隙/结尾/场景细节公式）", "kind": "system", "editable": False,
         "source": "hard_constraints.build_writing_guide", "content": writing_guide, "note": "代码内置"},
        {"name": "§1 本章章纲", "kind": "user", "editable": False,
         "source": f".story-system/chapters/chapter_{chapter:03d}.json", "content": directive_text},
        {"name": "§2 本章独有锚点", "kind": "user", "editable": False,
         "source": "computed from directive", "content": chapter_anchors},
        {"name": "§3 故事上下文（任务书+角色脚本）", "kind": "user", "editable": False,
         "source": "context-agent / 大纲/第NNNN章-写作任务书.md", "content": (ctx_text or "") + character_script_injection},
        {"name": "§4 写作质感规范", "kind": "user", "editable": True,
         "source": "draft/craft_rules.md", "content": craft_rules, "note": "可编辑片段"},
        {"name": "§5 风格参考", "kind": "user", "editable": False,
         "source": "style_sampler", "content": style_anchors},
        {"name": "§6 落笔前自检", "kind": "user", "editable": False,
         "source": "computed", "content": pre_output_check},
        {"name": "§7 输出指令", "kind": "user", "editable": True,
         "source": "draft/output_instruction.md", "content": output_instruction, "note": "可编辑片段（{word_range} 占位）"},
    ]

    return {
        "system_prompt": system_prompt,
        "user_message": user_message,
        "resolved_model": resolved_model,
        "is_opening": is_opening,
        "beat_position": beat_position,
        "directive_dict": directive_dict,
        "segments": segments,
    }


async def _run_draft_for_chapter(
    task: Task, *, project_root: Path, chapter: int, ctx_text: str, model: str | None,
    temperature: float | None = None,
    freedom_level: FreedomLevel = DEFAULT_FREEDOM_LEVEL,
    character_script_path: Path | None = None,
) -> Path | None:
    """根据任务书起草本章正文，返回落盘的正文文件路径。

    若 character_script_path 非空，将其 prose 对话场景注入 draft prompt，
    要求主力模型直接复用脚本中的对话和动作段落，补充叙事过渡和场景衔接。
    """
    # 决策检查
    if not await _decision_check(
        task,
        "draft",
        operation_type="chapter_draft",
        step_description=f"根据任务书起草第 {chapter} 章正文",
    ):
        return None

    await TASKS.emit(task, {"phase": "step", "step": "draft", "status": "running"})

    # ── 角色脚本注入（若用户确认使用）──
    character_script_injection = ""
    if character_script_path and character_script_path.is_file():
        try:
            script_content = character_script_path.read_text(encoding="utf-8")
            character_script_injection = (
                "\n\n## 角色脚本（已预生成，直接复用）\n\n"
                "以下对话场景由人物模型预生成（prose 小说体）。起草时：\n"
                "1. **直接复用**脚本中的对话原文--不要改写、不要换说法、不要调整措辞\n"
                "2. **保留**脚本中的动作和微表情描述\n"
                "3. **补充**场景过渡、环境衔接、非对话段落的叙事推进\n"
                "4. **不要替换**角色说话方式--脚本中的声线是经过人设校验的\n\n"
                f"{script_content}\n"
            )
        except Exception as exc:
            await TASKS.emit(task, {
                "phase": "stderr",
                "line": f"读取角色脚本失败，将不使用: {exc}",
            })

    # ── 组装 prompt（与预览共用同一纯函数）──
    asm = _assemble_draft_prompt(
        project_root, chapter, ctx_text,
        character_script_injection=character_script_injection,
        freedom_level=freedom_level, model=model,
    )
    system_prompt = asm["system_prompt"]
    user_message = asm["user_message"]
    resolved_model = asm["resolved_model"]
    is_opening = asm["is_opening"]
    directive_text = asm["segments"][3]["content"]   # §1 章纲文本
    chapter_anchors = asm["segments"][4]["content"]  # §2 锚点文本
    craft_rules = asm["segments"][5]["content"]      # §4 质感规范
    pre_output_check = asm["segments"][7]["content"] # §6 自检
    ctx_in_user = asm["segments"][2]["content"]      # §3 上下文

    draft_on_event, draft_on_token = make_task_emitters(task, "draft")
    draft_runner = AnthropicAgentRunner(
        project_root=project_root,
        model=resolved_model,
        system_prompt=system_prompt,
        max_tokens=8192 if is_opening else 6144,
        allowed_tools=[],
        agent_name="draft",
        on_event=draft_on_event,
        on_token=draft_on_token,
        max_turns=2,
        temperature=temperature,
        meta={"stage": "draft", "chapter": chapter},
    )
    draft_text = await draft_runner.run(user_message)

    # ── 字数硬上限截断（doubao 模型不遵守 max_tokens，需生成后强制）──
    hard_cap = 4000 if is_opening else 3000
    if len(draft_text) > hard_cap:
        truncated = draft_text[:hard_cap]
        last_para = truncated.rfind("\n\n")
        if last_para > len(truncated) // 2:
            draft_text = truncated[:last_para]
        else:
            draft_text = truncated
        await TASKS.emit(task, {
            "phase": "stdout",
            "line": f"✂ 字数超上限（{len(draft_text)} > {hard_cap}），已截断。",
        })

    # ── 质量闸门：draft 生成后自动检测 ──
    quality_report = _check_draft_quality(draft_text, chapter, channel_profile=load_channel(project_root))
    if not quality_report["passed"]:
        await TASKS.emit(task, {
            "phase": "stdout",
            "line": f"⚠ 初稿质量检测未通过: {quality_report['reason']}。自动重试一次...",
        })
        # 重试时强化提示（v3.4: 问题描述放 user message 开头 + 复用 § 结构）
        word_range = "2500-3500" if is_opening else "2000-2500"
        retry_output_instruction = fill_template(
            resolve_fragment(project_root, "draft/output_instruction.md"),
            word_range=word_range,
        )
        retry_user_parts = [
            f"## ⚠️ 重试说明\n\n上一版草稿有质量问题：{quality_report['reason']}\n请严格改进后重新起草。\n",
            "## §1 本章任务（硬约束，不可偏离）",
            "",
            directive_text,
            "",
            "## §2 本章独有锚点",
            "",
            chapter_anchors,
            "",
            "## §3 故事上下文",
            "",
            ctx_in_user,
            "",
            "## §4 写作质感规范",
            "",
            craft_rules,
            "",
            "## §5 落笔前自检",
            "",
            pre_output_check,
            "",
            "## §6 输出要求",
            "",
            retry_output_instruction,
        ]
        retry_user = "\n".join(p for p in retry_user_parts if p)
        retry_runner = AnthropicAgentRunner(
            project_root=project_root,
            model=resolved_model,
            system_prompt=system_prompt,  # 复用同一个精简 system prompt
            max_tokens=8192 if is_opening else 6144,
            allowed_tools=[],
            agent_name="draft-retry",
            on_event=draft_on_event,
            on_token=draft_on_token,
            max_turns=2,
            temperature=temperature,
            meta={"stage": "draft", "chapter": chapter},
        )
        draft_text = await retry_runner.run(retry_user)

        # 重试后也做字数硬上限截断
        if len(draft_text) > hard_cap:
            truncated = draft_text[:hard_cap]
            last_para = truncated.rfind("\n\n")
            if last_para > len(truncated) // 2:
                draft_text = truncated[:last_para]
            else:
                draft_text = truncated
            await TASKS.emit(task, {
                "phase": "stdout",
                "line": f"✂ 重试后字数仍超上限，已截断。",
            })

        # 重试后再检
        quality_report2 = _check_draft_quality(draft_text, chapter, channel_profile=load_channel(project_root))
        if not quality_report2["passed"]:
            await TASKS.emit(task, {
                "phase": "stderr",
                "line": f"⚠ 重试后质量检测仍未通过: {quality_report2['reason']}。保留当前草稿，建议人工检查。",
            })

    # 拒绝空草稿：两次都返回空则报错而非写 0 字节文件
    if not draft_text or not draft_text.strip():
        await TASKS.emit_error(task, f"第 {chapter} 章草稿两次生成均为空，可能原因：模型超时、prompt 过长、或 API 限流。请检查 API 配置后重试。")
        return None

    # 二次校验：UTF-8 字节数过短视为生成失败（防 API 返回占位符/截断）
    draft_bytes = len(draft_text.encode("utf-8"))
    if draft_bytes < 100:
        await TASKS.emit_error(task, f"第 {chapter} 章草稿过短（{draft_bytes} bytes, {len(draft_text)} chars），放弃写入。可能原因：API 返回截断或空响应。请重试。")
        return None

    # AI 起草稿统一放到 AI生成/ 目录，精修稿才进 正文/
    ai_dir = project_root / "AI生成"
    ai_dir.mkdir(exist_ok=True)

    # 兼容旧结构：如果 正文/第NNNN章-AI起草.md 存在，迁移到 AI生成/第NNNN章.md
    legacy_draft = project_root / "正文" / f"第{chapter:04d}章-AI起草.md"
    draft_path = ai_dir / f"第{chapter:04d}章.md"
    if legacy_draft.is_file():
        import shutil
        try:
            shutil.move(str(legacy_draft), str(draft_path))
            await TASKS.emit(task, {"phase": "stdout",
                                    "line": f"已迁移旧结构: {legacy_draft.name} -> {draft_path.name}"})
        except Exception as exc:
            await TASKS.emit(task, {"phase": "stderr",
                                    "line": f"迁移旧结构失败: {exc}，将覆盖写入 {draft_path.name}"})

    # 原子写入：先写 .tmp 再 os.replace，崩溃不损坏已有草稿
    tmp_path = draft_path.with_suffix(".md.tmp")
    tmp_path.write_text(draft_text, encoding="utf-8")
    os.replace(tmp_path, draft_path)

    await TASKS.emit(task, {
        "phase": "step", "step": "draft", "status": "done",
        "path": str(draft_path.relative_to(project_root)),
        "preview": draft_text[:300],
    })
    return draft_path
