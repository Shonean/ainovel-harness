"""
_prompt_fragments.py - 可覆盖的 prompt 片段管理（Prompt 审阅台后端基础）

把原本硬编码在各组装器中的 prompt 文本抽取为「代码默认 + 测试书本地覆盖」模型：

- 默认值 = 代码常量（FRAGMENT_DEFAULTS，单一真相源；无覆盖时行为与改造前完全一致）
- 测试书可在 <书>/.ainovel/prompts/fragments/{name} 放本地覆盖
- 组装器调 resolve_fragment() 读「本地覆盖 or 代码默认」
- reset_fragment = 删本地覆盖（回到代码默认）

被抽取的片段（name -> 默认来源 -> 注入点）：
  draft/craft_rules.md          <- 原 _anti_ai._CRAFT_RULES_INJECTION -> _build_craft_rules()
  draft/output_instruction.md   <- 原 _draft.py §7 字符串            -> draft user 组装
  stages/plan_user_input.md     <- 原 _router.py plan user_input     -> plan skill agent
  stages/context_user_input.md  <- 原 _context.py user_input         -> context agent
  stages/character_user_input.md<- 原 _character.py user_input       -> character agent
  stages/reviewer_user_input.md <- 原 _review.py reviewer user_input -> reviewer agent
  stages/critic_user_input.md   <- 原 _critic.py user_input          -> critic agent
  stages/data_user_input.md     <- 原 _write.py data user_input      -> data agent
  review/opening_injection.md   <- 原 _review.py 开篇审查注入        -> reviewer system 注入

agent.md / 通道 / references 仍走 _prompts.py（PromptFilesPage / ChannelConfigPage），不在此处。
"""
from __future__ import annotations

from pathlib import Path


# ═══════════════════════════════════════════════════════════════════════════
# 片段默认值（代码真相源）
# ═══════════════════════════════════════════════════════════════════════════

# --- draft/craft_rules.md：5 级写作质感规范（原 _anti_ai._CRAFT_RULES_INJECTION）---
_CRAFT_RULES = """## 写作质感规范--决定正文"读起来像不像人写的"

按重要性从高到低排列。每条规则配一个"坏->好"示例。

### L5 对话（最重要--剧本模型最易失控）

**规则**：
- 对话占比 40-50%。如果发现自己一直在写对话，立刻插入叙事段落
- 连续 6 句以上纯对话必须插入叙事（动作/环境/心理/身体感受）
- 用前置动作替代"XX说道"（said tag 占比不超过 30%）："他把杯子搁下--'你确定？'"
- 对话必须有意图（试探/施压/回避/诱导），不能是纯信息交换
- 允许抢话、沉默、答非所问、一方连续说两句--禁止严格 A->B->A->B 轮流

**示例**：
> ❌ 「你真的要去？」他问道。「我必须去。」她回答。「可是很危险。」他担心地说。「我不怕。」她坚定地说。
> ✅ 「你真要去？」他把烟掐了。
> 「--」
> 她已经拉开门。风灌进来，吹得桌上的纸哗啦一响。「天黑前回来。」

---

### L4 叙事深度（小说区别于剧本的核心）

**规则**：
- 身体感受自然融入叙事--不设频率硬指标，在关键场景（紧张/冲突/决策）中通过角色身体反应传递状态
- 关键决策前展示角色的推理/犹豫/权衡过程--不要"他想了想，决定 X"
- 场景切换必须有过渡锚点（时间标记/空间移动路径/状态变化过程），禁止直接跳切
- 每场景至少 2 种非视觉感官（声音/气味/触觉/温度/身体感受）

**示例**：
> ❌ 他想了想，决定先去找老张。
> ✅ 找老张？老张上次坑他的账还没算。但现在能拿到仓库钥匙的，除了老张没有第二个人。他把烟头碾进烟灰缸--碾得特别用力，直到滤嘴都扁了--然后起身。

---

### L3 句式（AI 味的骨架--最容易批量出现）

**规则**：
- 短句占比 30-50%。段落有长有短，有的段落只有一句话
- 禁止连续 3 句以上"主语 + 谓语 + 宾语"同构句
- 禁止"起因->经过->结果->感悟"四段闭环--删掉段末的感悟/总结句
- 禁止万能副词：缓缓/淡淡/微微/轻轻/静静/默默/悄悄/慢慢/渐渐/暗暗
- 禁止递进模板：更重要的是/更令人惊讶的是/最让他在意的是

**示例**：
> ❌ 他缓缓走到窗边，轻轻推开窗户，静静地看着外面的夜色。心中不由得感慨万千。
> ✅ 窗。推开。冷风灌进来--他眯起眼。街上有人在跑，不知道在追什么。

---

### L2 情感（展示不告知--让读者自己判断）

**规则**：
- 禁止"他感到 X""他心中 Y""他不由得 Z"--这是给情绪贴标签
- 改为：生理反应 + 微动作 + 环境映射。"他感到愤怒"->"他指节捏得发白，喉咙里压着一口气"
- 每个角色有专属微动作（咬笔帽=焦虑，拧手表=不耐烦，摸耳垂=说谎），不全员"瞳孔微缩""心中一凛"
- 禁止情绪三连："他感到愤怒，又有些无奈，内心深处还有一丝悲哀"

**示例**：
> ❌ 他感到一阵恐惧涌上心头，内心五味杂陈。
> ✅ 他的手在发抖。不是冷--他自己也知道不是冷。茶水洒了半桌，他盯着那片水渍看了很久。

---

### L1 收尾（按本章节拍位置决定）

**规则**：
- **开启章/发展章**：章末必须保留未闭合张力。不得在章内完成节拍闭环。停在问题恶化/压力加剧/选择收窄的瞬间
- **收束章**：可完整收束本节拍矛盾，但必须绑定一种未闭合形式（问题未解决/代价已付但结果未知/关系变化未确认/信息缺口）
- **单章节拍**：可闭合可未闭合，但优先未闭合张力
- 禁止"安全着陆"式收尾：「他回去休息了」「一切归于平静」「夜色如墨，城市沉沉睡去」

**示例**：
> ❌ [发展章末尾] 他解决了眼前的麻烦，长舒一口气，回房休息了。
> ✅ [发展章末尾] 麻烦暂时压下去了。但他知道--压下去的东西，迟早要弹回来的。而且弹得更狠。
"""

# --- draft/output_instruction.md：§7 输出指令（{word_range} 由调用方填）---
_OUTPUT_INSTRUCTION = (
    "直接输出 markdown 正文，不要解释，不要工具调用，字数 {word_range} 中文字符。"
    "第一行必须是章节标题，格式：第X章 章节名（不要省略标题行）。"
)

# --- stages/plan_user_input.md ---
_PLAN_USER_INPUT = (
    "规划第 {volume} 卷第 {start}-{end} 章。PROJECT_ROOT={root}。\n"
    "本次仅规划第 {start} 到第 {end} 章，共 {chapter_count} 章，"
    "不得越界规划整卷其它章节（避免输出截断）。\n"
    "按 SKILL.md 执行：加载总纲与设定、补齐设定基线、"
    "生成卷摘要与每章独立章纲文件（大纲/第NNNN章-章纲.md，含 CBN/CPNs/CEN 结构化节点与逐段推进）、"
    "把新增设定写回设定集、运行 master-outline-sync 与 update-state（--add-chapters-planned 增量登记本次章范围）。"
    "需要确认卷范围/裁决冲突时调 AskUser。"
)

# --- stages/context_user_input.md ---
_CONTEXT_USER_INPUT = (
    "为第 {chapter} 章组装写作任务书。\n"
    "project_root: {project_root}\n"
    "scripts_dir: {scripts_dir}\n"
    "调 Bash 工具跑 load-context、style_sampler select 等。"
)

# --- stages/character_user_input.md（{chapter_padded}=0001 形式）---
_CHARACTER_USER_INPUT = (
    "为第 {chapter} 章预生成角色对话场景（prose 小说体）。\n"
    "chapter: {chapter}\n"
    "project_root: {project_root}\n"
    "scripts_dir: {scripts_dir}\n\n"
    "===== 写作任务书（含交互设计简报）=====\n"
    "{ctx_text}\n\n"
    "请按 agent 指令：加载角色设定 -> 解析简报 -> 逐 beat 生成 prose 场景 -> "
    "自检 -> 落盘 AI生成/第{chapter_padded}章-角色脚本.md。"
)

# --- stages/reviewer_user_input.md（{review_injection} 由调用方填，可能为空）---
_REVIEWER_USER_INPUT = (
    "审查第 {chapter} 章。chapter_file: {content_path}\n"
    "project_root: {project_root}\n"
    "请输出结构化 issues JSON 并保存到 {review_results_path}。"
    "{review_injection}"
)

# --- stages/critic_user_input.md（每窗口一次调用）---
_CRITIC_USER_INPUT = (
    "审查以下窗口（第 {chapter} 章，窗口 {window_index}/{window_count}），"
    "挑出普通读者会觉得是 AI 写的地方：\n"
    "```\n{window_text}\n```"
)

# --- stages/data_user_input.md ---
_DATA_USER_INPUT = (
    "从第 {chapter} 章提取数据。chapter_file: {draft_path}\n"
    "project_root: {project_root}\n"
    "输出 {fulfill} / {disambig} / {extract}。"
)

# --- review/opening_injection.md：开篇章节审查专用标准 ---
_OPENING_INJECTION = """
【开篇章节审查专用标准】（本章为开篇章节，额外检查以下维度）
1. [钩子强度] 开篇钩子是否在前 500 字内出现？钩子强度（悬念/冲突/好奇/情感冲击）？
2. [主角记忆度] 主角是否让人印象深刻？是否有独特的特质/行动/对话？
3. [世界观引入] 世界观是否自然展开？是否存在大段信息堆砌段落（超过3句纯说明）？
4. [阅读动力] 本章结束时，读者是否有迫切的继续阅读欲望？
5. [节奏] 开篇节奏是否合理？是否过快（缺乏基础信息）或过慢（前500字无钩子）？
6. [禁用检查] 是否使用了禁用开局方式（失忆/醒来/系统面板弹窗/大段世界观介绍）？
其余审查维度（设定一致性、时间线、叙事连贯、角色一致性、逻辑、AI味）照常执行。
"""


# ═══════════════════════════════════════════════════════════════════════════
# 片段注册表
# ═══════════════════════════════════════════════════════════════════════════

FRAGMENT_DEFAULTS: dict[str, str] = {
    "draft/craft_rules.md": _CRAFT_RULES,
    "draft/output_instruction.md": _OUTPUT_INSTRUCTION,
    "stages/plan_user_input.md": _PLAN_USER_INPUT,
    "stages/context_user_input.md": _CONTEXT_USER_INPUT,
    "stages/character_user_input.md": _CHARACTER_USER_INPUT,
    "stages/reviewer_user_input.md": _REVIEWER_USER_INPUT,
    "stages/critic_user_input.md": _CRITIC_USER_INPUT,
    "stages/data_user_input.md": _DATA_USER_INPUT,
    "review/opening_injection.md": _OPENING_INJECTION,
}

# 元信息：描述 + 默认来源标注 + 占位符（供审阅台展示）
FRAGMENT_META: list[dict] = [
    {
        "name": "draft/craft_rules.md",
        "description": "Draft §4 写作质感规范（5 级 L5->L1：对话/叙事深度/句式/情感/收尾）",
        "default_source": "_anti_ai._CRAFT_RULES_INJECTION",
        "placeholders": [],
    },
    {
        "name": "draft/output_instruction.md",
        "description": "Draft §7 输出指令",
        "default_source": "_draft.py §7",
        "placeholders": ["{word_range}"],
    },
    {
        "name": "stages/plan_user_input.md",
        "description": "Plan 阶段 user_input 模板",
        "default_source": "_router.py plan",
        "placeholders": ["{volume}", "{start}", "{end}", "{chapter_count}", "{root}"],
    },
    {
        "name": "stages/context_user_input.md",
        "description": "Context 阶段 user_input 模板",
        "default_source": "_context.py",
        "placeholders": ["{chapter}", "{project_root}", "{scripts_dir}"],
    },
    {
        "name": "stages/character_user_input.md",
        "description": "Character 阶段 user_input 模板",
        "default_source": "_character.py",
        "placeholders": ["{chapter}", "{chapter_padded}", "{project_root}", "{scripts_dir}", "{ctx_text}"],
    },
    {
        "name": "stages/reviewer_user_input.md",
        "description": "Reviewer 阶段 user_input 模板",
        "default_source": "_review.py reviewer",
        "placeholders": ["{chapter}", "{content_path}", "{project_root}", "{review_results_path}", "{review_injection}"],
    },
    {
        "name": "stages/critic_user_input.md",
        "description": "Critic 阶段 user_input 模板（每窗口一次调用）",
        "default_source": "_critic.py",
        "placeholders": ["{chapter}", "{window_index}", "{window_count}", "{window_text}"],
    },
    {
        "name": "stages/data_user_input.md",
        "description": "Data 阶段 user_input 模板",
        "default_source": "_write.py data",
        "placeholders": ["{chapter}", "{draft_path}", "{project_root}", "{fulfill}", "{disambig}", "{extract}"],
    },
    {
        "name": "review/opening_injection.md",
        "description": "Reviewer 开篇章节审查专用标准（注入 system）",
        "default_source": "_review.py opening_injection",
        "placeholders": [],
    },
]


# ═══════════════════════════════════════════════════════════════════════════
# Public API
# ═══════════════════════════════════════════════════════════════════════════

def _fragment_dir(project_root: Path) -> Path:
    return project_root / ".ainovel" / "prompts" / "fragments"


def _fragment_path(project_root: Path, name: str) -> Path:
    return _fragment_dir(project_root) / name


def resolve_fragment(project_root: Path | None, name: str) -> str:
    """读「本地覆盖 or 代码默认」。project_root 为 None 时直接返代码默认。"""
    if project_root is not None:
        local = _fragment_path(project_root, name)
        if local.is_file():
            try:
                return local.read_text(encoding="utf-8")
            except OSError:
                pass
    return FRAGMENT_DEFAULTS.get(name, "")


def is_override(project_root: Path, name: str) -> bool:
    return _fragment_path(project_root, name).is_file()


def write_fragment(project_root: Path, name: str, content: str) -> None:
    """写入本地覆盖。自动创建父目录。"""
    if name not in FRAGMENT_DEFAULTS:
        raise KeyError(f"未知片段: {name}")
    dest = _fragment_path(project_root, name)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(content, encoding="utf-8")


def reset_fragment(project_root: Path, name: str) -> bool:
    """删除本地覆盖（回到代码默认）。返回是否确实删除了。"""
    dest = _fragment_path(project_root, name)
    if dest.is_file():
        dest.unlink()
        return True
    return False


def read_fragment_detail(project_root: Path, name: str) -> dict:
    """返回 {content, is_override, source, description, placeholders}。"""
    local = _fragment_path(project_root, name)
    is_ov = local.is_file()
    if is_ov:
        try:
            content = local.read_text(encoding="utf-8")
        except OSError:
            content = FRAGMENT_DEFAULTS.get(name, "")
            is_ov = False
    else:
        content = FRAGMENT_DEFAULTS.get(name, "")
    meta = next((m for m in FRAGMENT_META if m["name"] == name), {})
    return {
        "name": name,
        "content": content,
        "is_override": is_ov,
        "source": ("本地覆盖" if is_ov else "代码默认") + f"（{meta.get('default_source', '')}）",
        "description": meta.get("description", ""),
        "placeholders": meta.get("placeholders", []),
    }


def list_fragments(project_root: Path) -> list[dict]:
    """列出全部片段及覆盖状态。"""
    out = []
    for m in FRAGMENT_META:
        out.append({
            "name": m["name"],
            "description": m["description"],
            "default_source": m["default_source"],
            "placeholders": m["placeholders"],
            "is_override": is_override(project_root, m["name"]),
        })
    return out


def fill_template(tpl: str, **kw) -> str:
    """安全 {key} 替换：仅替换已知键，未知 {x} 原样保留（避免 .format 对花括号报错）。"""
    out = tpl
    for k, v in kw.items():
        out = out.replace("{" + k + "}", str(v))
    return out
