"""
Anti-AI 直接注入 + 草稿质量闸门 —— 从 workflows.py 拆分。

v3.3: 合并 _ANTI_AI_INJECTION + _ANTI_SCRIPT_INJECTION → _CRAFT_RULES_INJECTION（5级分层）。
      新增 _build_chapter_anchors()、_build_pre_output_check()、_build_style_anchors()。
"""
from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path
from typing import Optional


# ═══════════════════════════════════════════════════════════════════════════
# 核心注入：5 级写作质感规范（合并 Anti-AI + 反剧本，按影响力排列）
# ═══════════════════════════════════════════════════════════════════════════

_CRAFT_RULES_INJECTION = """## 写作质感规范——决定正文"读起来像不像人写的"

按重要性从高到低排列。每条规则配一个"坏→好"示例。

### L5 对话（最重要——剧本模型最易失控）

**规则**：
- 对话占比 40-50%。如果发现自己一直在写对话，立刻插入叙事段落
- 连续 6 句以上纯对话必须插入叙事（动作/环境/心理/身体感受）
- 用前置动作替代"XX说道"（said tag 占比不超过 30%）："他把杯子搁下——'你确定？'"
- 对话必须有意图（试探/施压/回避/诱导），不能是纯信息交换
- 允许抢话、沉默、答非所问、一方连续说两句——禁止严格 A→B→A→B 轮流

**示例**：
> ❌ 「你真的要去？」他问道。「我必须去。」她回答。「可是很危险。」他担心地说。「我不怕。」她坚定地说。
> ✅ 「你真要去？」他把烟掐了。
> 「——」
> 她已经拉开门。风灌进来，吹得桌上的纸哗啦一响。「天黑前回来。」

---

### L4 叙事深度（小说区别于剧本的核心）

**规则**：
- 身体感受自然融入叙事——不设频率硬指标，在关键场景（紧张/冲突/决策）中通过角色身体反应传递状态
- 关键决策前展示角色的推理/犹豫/权衡过程——不要"他想了想，决定 X"
- 场景切换必须有过渡锚点（时间标记/空间移动路径/状态变化过程），禁止直接跳切
- 每场景至少 2 种非视觉感官（声音/气味/触觉/温度/身体感受）

**示例**：
> ❌ 他想了想，决定先去找老张。
> ✅ 找老张？老张上次坑他的账还没算。但现在能拿到仓库钥匙的，除了老张没有第二个人。他把烟头碾进烟灰缸——碾得特别用力，直到滤嘴都扁了——然后起身。

---

### L3 句式（AI 味的骨架——最容易批量出现）

**规则**：
- 短句占比 30-50%。段落有长有短，有的段落只有一句话
- 禁止连续 3 句以上"主语 + 谓语 + 宾语"同构句
- 禁止"起因→经过→结果→感悟"四段闭环——删掉段末的感悟/总结句
- 禁止万能副词：缓缓/淡淡/微微/轻轻/静静/默默/悄悄/慢慢/渐渐/暗暗
- 禁止递进模板：更重要的是/更令人惊讶的是/最让他在意的是

**示例**：
> ❌ 他缓缓走到窗边，轻轻推开窗户，静静地看着外面的夜色。心中不由得感慨万千。
> ✅ 窗。推开。冷风灌进来——他眯起眼。街上有人在跑，不知道在追什么。

---

### L2 情感（展示不告知——让读者自己判断）

**规则**：
- 禁止"他感到 X""他心中 Y""他不由得 Z"——这是给情绪贴标签
- 改为：生理反应 + 微动作 + 环境映射。"他感到愤怒"→"他指节捏得发白，喉咙里压着一口气"
- 每个角色有专属微动作（咬笔帽=焦虑，拧手表=不耐烦，摸耳垂=说谎），不全员"瞳孔微缩""心中一凛"
- 禁止情绪三连："他感到愤怒，又有些无奈，内心深处还有一丝悲哀"

**示例**：
> ❌ 他感到一阵恐惧涌上心头，内心五味杂陈。
> ✅ 他的手在发抖。不是冷——他自己也知道不是冷。茶水洒了半桌，他盯着那片水渍看了很久。

---

### L1 收尾（按本章节拍位置决定）

**规则**：
- **开启章/发展章**：章末必须保留未闭合张力。不得在章内完成节拍闭环。停在问题恶化/压力加剧/选择收窄的瞬间
- **收束章**：可完整收束本节拍矛盾，但必须绑定一种未闭合形式（问题未解决/代价已付但结果未知/关系变化未确认/信息缺口）
- **单章节拍**：可闭合可未闭合，但优先未闭合张力
- 禁止"安全着陆"式收尾：「他回去休息了」「一切归于平静」「夜色如墨，城市沉沉睡去」

**示例**：
> ❌ [发展章末尾] 他解决了眼前的麻烦，长舒一口气，回房休息了。
> ✅ [发展章末尾] 麻烦暂时压下去了。但他知道——压下去的东西，迟早要弹回来的。而且弹得更狠。
"""


# ═══════════════════════════════════════════════════════════════════════════
# 向后兼容：旧函数返回空字符串（不再使用独立注入）
# ═══════════════════════════════════════════════════════════════════════════

def _build_anti_ai_injection() -> str:
    """[已废弃] Anti-AI 规则已合并入 _build_craft_rules() 的 L5-L1 质感规范。"""
    return ""


def _build_anti_script_injection() -> str:
    """[已废弃] 反剧本校准已合并入 _build_craft_rules() 的 L5-L1 质感规范。"""
    return ""


# ═══════════════════════════════════════════════════════════════════════════
# 新 API
# ═══════════════════════════════════════════════════════════════════════════

def _build_craft_rules(channel_profile=None, project_root=None) -> str:
    """返回合并后的 5 级写作质感规范（Anti-AI + 反剧本，单一权威来源）。

    5 级规范默认值已迁移至 _prompt_fragments.draft/craft_rules.md，可被测试书
    本地覆盖（Prompt 审阅台编辑）。project_root 为 None 时使用代码默认。

    若 channel_profile 提供了 craft_rules，追加到默认规范之后。
    channel_profile 可以是 ChannelProfile 或任何有 craft_rules 属性的对象。
    """
    from ._prompt_fragments import resolve_fragment
    rules = resolve_fragment(project_root, "draft/craft_rules.md")
    if channel_profile is not None:
        extra = getattr(channel_profile, 'craft_rules', '') or ''
        if extra.strip():
            rules = rules + "\n\n---\n\n## 题材特有写作规范\n\n" + extra
    return rules


def _has_concrete_object(text: str) -> bool:
    """检查文本是否包含具体物件/环境名词（排除纯抽象描述）。"""
    _OBJECT_KW = [
        "灯", "烛", "火", "窗", "门", "桌", "椅", "床", "柜", "案",
        "纸", "笔", "墨", "砚", "书", "卷", "册", "帖", "信", "折",
        "杯", "碗", "壶", "盘", "碟", "筷", "瓶", "罐", "缸",
        "刀", "剑", "枪", "棍", "棒", "箭", "弓", "盾",
        "酒", "茶", "水", "汤", "药", "饭", "菜", "米", "面",
        "布", "绸", "缎", "纱", "锦", "袍", "衫", "衣", "帽", "鞋",
        "风", "雨", "雪", "霜", "雾", "露", "雷", "电",
        "花", "草", "树", "木", "叶", "枝", "根", "藤",
        "石", "砖", "瓦", "墙", "柱", "梁", "阶", "廊", "院",
        "尘", "泥", "沙", "灰", "烟", "雾", "气", "香",
        "蝉", "鸟", "虫", "犬", "猫", "马", "牛", "鸡",
        "钟", "鼓", "锣", "铃", "琴", "笛",
        "镜", "梳", "篦", "簪", "钗", "环", "佩",
        "锁", "钥", "链", "绳", "袋", "囊", "匣", "盒",
    ]
    return any(kw in text for kw in _OBJECT_KW)


def _build_chapter_anchors(
    directive: dict,
    genre_profile: Optional[dict] = None,
) -> str:
    """从章纲 directive 中提取本章独有的具体感官/动作/场景细节，作为 Layer 3 锚点。

    v3.4: 从关键词匹配升级为结构化标签解析（【感官】【场景】【情绪】）。
          优先提取 paragraph_beats 中的硬约束细节，永不回退到 genre_profile 通用模板
          （通用模板正是 AI 味的来源——"每场景至少3种感官"本身就是模板化指令）。

    锚点的作用：AI 必须围绕这些具体细节写作，无法滑向通用模板。
    """
    anchors: list[str] = []
    beats = directive.get("paragraph_beats") or []

    for beat in beats:
        beat_str = str(beat)

        # 1) 【感官】标签 → 具体感官细节（最高优先级）
        sensory_match = re.search(r'【感官[：:]([^】]+)】', beat_str)
        if sensory_match:
            for detail in re.split(r'[+、]', sensory_match.group(1).strip()):
                detail = detail.strip()
                if detail and len(detail) >= 2:
                    anchors.append(detail)

        # 2) 描述文本（去掉所有【标签】后的核心事件，取前80字——第二优先）
        desc = re.sub(r'【[^】]+】', '', beat_str).strip()
        if len(desc) >= 15:
            anchors.append(desc[:80])

        # 3) 【场景】标签 → 含具体物件的环境子句
        scene_match = re.search(r'【场景[：:]([^】]+)】', beat_str)
        if scene_match:
            for clause in scene_match.group(1).strip().split('，'):
                clause = clause.strip()
                if len(clause) >= 6 and _has_concrete_object(clause):
                    anchors.append(clause)

        # 4) 【情绪】标签 → 情感基调
        emotion_match = re.search(r'【情绪[：:]([^】]+)】', beat_str)
        if emotion_match:
            for e in re.split(r'[+、→]', emotion_match.group(1).strip()):
                e = e.strip()
                if e and len(e) >= 2 and e not in ('无', '无情绪', '无变化'):
                    anchors.append(f"情绪基调：{e}")

    # 5) Fallback: goal/obstacles/cost（本章特有，远优于 genre_profile 通用默认值）
    if len(anchors) < 3:
        for field in ["goal", "obstacles", "cost"]:
            val = directive.get(field)
            if val and isinstance(val, str) and len(val) > 5:
                anchors.append(val)

    # 6) 去重（按前30字）+ 格式化（上限8条）
    seen: set[str] = set()
    unique: list[str] = []
    for a in anchors:
        key = a[:30]
        if key not in seen:
            seen.add(key)
            unique.append(a)

    if not unique:
        return ""

    lines = ["## 本章独有锚点（硬性要求——必须出现在正文中，不可遗漏）", ""]
    for i, anchor in enumerate(unique[:8], 1):
        lines.append(f"{i}. {anchor}")
    lines.append("")

    return "\n".join(lines)


def _build_identity(genre: str, beat_position: str, channel_profile=None, chapter: int = 0) -> str:
    """构建 Layer 1 身份 + 节拍位置声明。

    若 channel_profile 提供了 identity，追加到默认身份声明之后。
    若 chapter 为开篇章节（由调用方判断），追加开篇身份声明。

    channel_profile 可以是 ChannelProfile 或任何有 identity/opening_rules 属性的对象。
    """
    genre_label = genre or "网文"
    bp = beat_position or "单章节拍"

    closure_map = {
        "开启章": "本章不闭合——停在问题刚刚展开的时刻，不得在章内完成任何核心矛盾的闭环",
        "发展章": "本章不闭合——继续加压或揭示新层次，事情要比上章更严重/更复杂",
        "收束章": "本章可收束本节拍核心矛盾，但收束后必须绑入至少一种新的未闭合（问题未解决/代价已付但结果未知/关系变化未确认/信息缺口）",
        "单章节拍": "本章可闭合可未闭合——优先未闭合张力，禁止「安全着陆」式收尾",
    }
    closure_line = closure_map.get(bp, closure_map["单章节拍"])

    lines = [
        f"你是{genre_label}题材的网络小说作者。",
        f"本章节拍位置：{bp}。",
        f"{closure_line}",
    ]

    # 通道身份注入
    if channel_profile is not None:
        extra_identity = getattr(channel_profile, 'identity', '') or ''
        if extra_identity.strip():
            lines.append("")
            lines.append(extra_identity)

    # 开篇章节身份注入（由调用方根据 is_opening_chapter 判断后传入）
    if channel_profile is not None and chapter >= 1:
        opening_count = getattr(channel_profile, 'opening_count', 3)
        if 1 <= chapter <= opening_count:
            opening_rules = getattr(channel_profile, 'opening_rules', '') or ''
            if opening_rules.strip():
                opening_identity = (
                    f"\n\n【开篇章节 —— 第 {chapter} 章（共 {opening_count} 章开篇）】\n"
                    f"这是小说的开篇阶段。你的任务是：\n"
                    f"{opening_rules}"
                )
                lines.append(opening_identity)

    return "\n".join(lines)


def _build_pre_output_check(beat_position: str = "单章节拍", channel_profile=None, chapter: int = 0) -> str:
    """构建 Layer 7 落笔前自检（5 问 + 通道自检 + 开篇自检）。

    若 channel_profile 提供了 review_criteria，追加到自检之后。
    若 chapter 为开篇章节，追加开篇专用自检项。
    """
    is_closing = beat_position == "收束章"

    check = f"""## 落笔前自检（写完本章后，逐条确认再输出）

1. **[对话密度]** 对话占比是否在 40-50%？是否有连续 6 句以上纯对话？→ 如果有，插入叙事（动作/环境/心理）
2. **[叙事深度]** 关键场景中角色是否有身体反应（呼吸/心跳/肌肉/温度）？→ 如果没有，补充
3. **[句式]** 是否有"他感到X""他不由得Y"或万能副词（缓缓/淡淡/微微）？→ 全部替换为具体动作
4. **[情感]** 情绪是"展示"还是"告诉"？是否有连续 2 个以上"他感到X"→ 改为生理反应 + 微动作
5. **[收尾]** 章末是否自然截断？{"开启/发展章：情节推进到当前节点即止。停在动作/反应/对话上，不制造新悬念、不预告下章内容、不刻意停在'最紧张的一刻'" if not is_closing else "收束章：核心矛盾已收束，同时绑入了新未闭合。收束后同样停在动作/反应上，不做总结升华"}"""

    # 开篇章节自检（当 chapter 为开篇章节时追加）
    if channel_profile is not None and chapter >= 1:
        opening_count = getattr(channel_profile, 'opening_count', 3)
        if 1 <= chapter <= opening_count:
            check += f"""

6. **[开篇入场]** 前 300 字是否直接进入场景/行动/对话？是否有超过 2 句的纯环境描写？→ 如果是，删掉铺垫，从事开始
7. **[主角登场]** 主角是否通过行动/对话/选择展现了独特性格？→ 禁止纯描述式介绍
8. **[世界观露出]** 世界观是否通过情节和对话自然展开？→ 检查是否有超过 3 句的纯说明段落
9. **[章末截断]** 结尾是否停在动作/反应/对话上？是否在最后一个节拍完成后立刻收笔不拖泥带水？→ 检查是否有总结句/抒情句/氛围渲染/悬念预告——有则删掉"""

            # 通道特有的开篇规则
            if channel_profile is not None:
                opening_rules = getattr(channel_profile, 'opening_rules', '') or ''
                if opening_rules.strip():
                    check += f"\n\n### 题材开篇要求\n\n{opening_rules}"

    # 通道审查标准追加
    if channel_profile is not None:
        review_criteria = getattr(channel_profile, 'review_criteria', '') or ''
        if review_criteria.strip():
            check += f"\n\n### 题材额外检查项\n\n{review_criteria}"

    return check


def _build_style_anchors(
    project_root: Path,
    chapter: int,
    outline_text: str = "",
    scripts_dir: Optional[str] = None,
) -> str:
    """从本书历史高分章节中采样 1-3 段范文作为 Layer 6 风格锚点。

    调用 style_sampler select --outline "..." --max 2 --exclude-chapter N 获取高分片段。
    失败时静默降级返回空字符串。
    """
    sampler_path = None
    if scripts_dir:
        candidate = Path(scripts_dir) / "style_sampler.py"
        if candidate.is_file():
            sampler_path = candidate

    if sampler_path is None:
        return ""

    # 构造大纲摘要：用章纲目标+场景关键词作为 --outline 参数
    outline_short = outline_text.strip() if outline_text else ""
    if not outline_short:
        return ""

    try:
        result = subprocess.run(
            [
                "python", "-X", "utf8",
                str(sampler_path),
                "--project-root", str(project_root),
                "select",
                "--outline", outline_short,
                "--max", "2",
                "--exclude-chapter", str(chapter),
            ],
            capture_output=True,
            text=True,
            timeout=15,
        )
        if result.returncode != 0 or not result.stdout.strip():
            return ""

        # 解析 JSON 输出（emit_success → print_json → [{id, chapter, scene_type, content, score, tags}, ...]）
        raw = result.stdout.strip()
        data = json.loads(raw)
        samples: list[dict] = []
        if isinstance(data, dict):
            # 可能包裹在 success response 里
            samples = data.get("data") or data.get("result") or []
        elif isinstance(data, list):
            samples = data

        if not samples:
            return ""

        # 提取每段范文的 content
        parts: list[str] = []
        for s in samples[:2]:  # 最多 2 段
            content = s.get("content", "")
            if not content or len(content) < 20:
                continue
            scene_type = s.get("scene_type", "")
            score = s.get("score", 0)
            tag = f"场景：{scene_type}" if scene_type else ""
            if score:
                tag += f" | 得分：{score}"
            if tag:
                parts.append(f"<!-- {tag.strip()} -->\n{content}")
            else:
                parts.append(content)

        if not parts:
            return ""

        excerpts = "\n\n---\n\n".join(parts)

        return f"""## 风格参考（本书历史高分段落——对照质感和节奏）

{excerpts}

注：以上片段只作风格质感和节奏参考。不要照抄情节和句式，但可以对齐它们的叙事密度、对话比例、收尾方式。"""
    except Exception:
        return ""


# ═══════════════════════════════════════════════════════════════════════════
# 草稿质量闸门（不变）
# ═══════════════════════════════════════════════════════════════════════════

# 结尾总结/升华/收束标记词（对抗短篇故事综合征）
_ENDING_SUMMARY_MARKS = [
    "总之", "总的来说", "总而言之", "最终", "最后",
    "从此", "此后", "从那以后", "至此",
    "这一夜", "那天晚上", "那一刻",
    "一切都", "一切又", "一切似乎",
]
_ENDING_SUBLIMATION_MARKS = [
    "他终于明白", "他终于意识到", "他终于懂得了",
    "人生的", "命运的", "生命的真谛",
    "原来", "或许这就是", "也许这便是",
    "未来的路", "前方的路", "漫长的路",
    "新的开始", "新的篇章", "全新的",
]
_ENDING_CLOSURE_MARKS = [
    "故事还在继续", "生活还在继续", "日子还在继续",
    "前方的路还很长", "未来的路还很长",
    "未知的", "等待着他们", "等着他",
    "沉沉睡去", "安然入睡", "进入了梦乡",
    "天色渐渐亮了", "天边泛起了鱼肚白", "东方露出了曙光",
    "一切归于平静", "一切恢复了平静", "一切又恢复了",
    "夜色如墨", "夜深了", "夜更深了",
]
# 结尾悬念钩子标记（刻意制造悬念/预告未知——结尾应在动作/反应中自然截断，不需要钩子）
_ENDING_HOOK_MARKS = [
    "会一直", "即将", "将要", "预示着", "总觉得",
    "预感", "说不清", "好像有什么", "像有什么", "有什么在",
    "他没察觉", "他并不知道", "他浑然不觉", "他没有注意到",
    "暴风雨", "只是开始", "远远没有结束", "才刚刚开始",
]
_ENDING_TRIGGER_KW = _ENDING_SUMMARY_MARKS + _ENDING_SUBLIMATION_MARKS + _ENDING_CLOSURE_MARKS + _ENDING_HOOK_MARKS


def _check_ending_type(text: str, is_opening: bool) -> str | None:
    """检测正文结尾 300 字是否包含总结/升华/收束标记。

    Returns:
        None 表示正常（没有结尾问题）
        str  表示检测到的问题描述
    """
    if not text or len(text) < 200:
        return None  # 太短无法判断，不误报

    tail = text[-300:]
    hits = []
    for kw in _ENDING_TRIGGER_KW:
        idx = tail.find(kw)
        if idx != -1:
            # 计算命中词在原文中的绝对位置（距结尾的偏移）
            offset_from_end = len(tail) - idx
            hits.append((kw, offset_from_end))

    if not hits:
        return None

    # 按距结尾从近到远排序，最近的最严重
    hits.sort(key=lambda h: -h[1])
    top_hits = hits[:3]
    hit_desc = "、".join(f"「{kw}」" for kw, _ in top_hits)

    return f"结尾 300 字内检测到总结/升华/收束/悬念钩子标记：{hit_desc}。长篇小说章节不应在结尾做收束，也不应刻意留悬念钩子。章节要在冲突/动作/反应中自然截断。"

def _check_draft_quality(text: str, chapter: int, channel_profile=None) -> dict:
    """草稿最低质量闸门。返回 {"passed": bool, "reason": str}。

    检查项：
    1. 中文字符数 >= 1200（开篇章节 >= 1800）
    2. 存在章节标题标记（# 或 第X章）
    3. 高频 AI 废话词不超过阈值（开篇章节放宽至 8/千字）
    4. 开篇章节额外检查：前 500 字是否包含钩子信号

    channel_profile 可以是 ChannelProfile 或任何有 opening_count 属性的对象。
    """
    if not text or not text.strip():
        return {"passed": False, "reason": "草稿为空"}

    # 判断是否开篇章节
    is_opening = False
    if channel_profile is not None:
        opening_count = getattr(channel_profile, 'opening_count', 3)
        if 1 <= chapter <= opening_count:
            is_opening = True

    # 1. 字数检查（开篇章节要求更高 + 上限硬截止）
    min_chars = 1800 if is_opening else 1200
    max_chars = 4000 if is_opening else 3000
    chinese_chars = sum(1 for c in text if '一' <= c <= '鿿' or '㐀' <= c <= '䶿')
    if chinese_chars < min_chars:
        ch_label = "开篇章节" if is_opening else "常规"
        return {"passed": False, "reason": f"{ch_label}中文字数仅 {chinese_chars}，低于最低要求 {min_chars}"}
    if chinese_chars > max_chars:
        return {"passed": False, "reason": f"中文字数 {chinese_chars} 超过上限 {max_chars}，建议缩减"}

    # 2. 结构检查：是否有章节标题
    has_title = bool(re.search(r'第\s*\d+\s*章|^#\s', text, re.MULTILINE))
    if not has_title:
        return {"passed": False, "reason": "缺少章节标题（# 或 第X章）"}

    # 3. AI 废话词频快速扫描（开篇章节放宽阈值）
    ai_markers = [
        "缓缓", "淡淡", "微微", "轻轻", "心中暗道", "心中一凛", "眸中闪过",
        "首先", "其次", "最后", "总而言之", "由此可见", "值得注意的是",
        "心中五味杂陈", "百感交集", "不由得感慨", "深吸一口气",
    ]
    marker_count = sum(text.count(m) for m in ai_markers)
    if marker_count > 15 and chinese_chars > 0:
        density = marker_count / (chinese_chars / 1000)
        threshold = 8 if is_opening else 5  # 开篇章节放宽（世界观介绍可能含更多功能性重复）
        if density > threshold:
            return {"passed": False, "reason": f"AI模板词密度过高（{density:.1f}/千字，阈值{threshold}），疑似裸写未做anti-AI对抗"}

    # 4. 开篇章节额外检查：前 500 字钩子信号
    if is_opening:
        opening_head = text[:500] if len(text) >= 500 else text
        hook_signals = [
            "悬念", "冲突", "疑问", "异常", "不对劲", "奇怪", "从未",
            "第一次", "居然", "竟然", "突然", "不过", "可是", "然而",
            "怎么回事", "为什么", "不同寻常", "不对劲", "没想到",
        ]
        has_hook_signal = any(s in opening_head for s in hook_signals)
        if not has_hook_signal:
            return {
                "passed": False,
                "reason": "开篇章节前 500 字缺少钩子信号（悬念/冲突/疑问/异常/转折），建议重写开头增加读者吸引力",
            }

    # 5. 结尾类型检查：检测总结/升华/收束/悬念钩子标记词（所有章节都查）
    ending_issue = _check_ending_type(text, is_opening)
    if ending_issue:
        return {"passed": False, "reason": ending_issue}

    # 6. 对白质量 + 防御性写作（v5.6：对抗刻意松弛感和过度解释）
    try:
        from prompt_harness.structural_analyzer import (
            dialogue_quality_score, defensive_writing_score,
        )
        # 只有文本里确有对白（“”/「」或引号）才判对白质量，纯动作/心理章节不误伤
        if any(q in text for q in ('「', '」', '“', '”', '"')):
            dq = dialogue_quality_score(text)
            if dq < 0.55:
                return {"passed": False, "reason":
                        f"对白质量评分 {dq:.2f}，对话疑似刻意松弛（语气词过多/对话冗长/寒暄词多）。对白应直接简短，像原文一样干净利落。"}
        dw = defensive_writing_score(text)
        if dw < 0.6:
            return {"passed": False, "reason":
                    f"防御性写作评分 {dw:.2f}，存在过度解释（之所以…是因为/并不是说…/换句话说）。叙事应直接叙述，不必为读者排除误解。"}
    except Exception:
        # 量化检测失败不阻断主流程
        pass

    # 7. 装饰性发散 + 对话间隙冗余 + 结尾动作密度（v5.6 补丁：对抗关联发散/氛围收束）
    try:
        from prompt_harness.structural_analyzer import (
            ai_divergence_score, dialogue_gap_bloat, ending_action_density,
        )
        adv = ai_divergence_score(text)
        if adv < 0.7:
            return {"passed": False, "reason":
                    f"装饰性细节发散评分 {adv:.2f}，检测到锦旗/妙手回春/古朴/泛黄等 AI 模板装饰词。只写角色正在互动的东西，不要停下来描写环境物件。"}
        if '「' in text or '"' in text or '」' in text:
            dg = dialogue_gap_bloat(text)
            if dg < 0.5:
                return {"passed": False, "reason":
                        f"对话间隙冗余评分 {dg:.2f}，对白之间夹带环境/物件描写（目光落在…/望向…）。对白之间只放角色即时反应，不写装饰。"}
        ea = ending_action_density(text)
        if ea < 0.5:
            return {"passed": False, "reason":
                    f"结尾动作密度评分 {ea:.2f}，结尾以环境/氛围/微动作收尾。结尾应是最后一个节拍的动作结果，写完立刻停笔。"}
    except Exception:
        # 量化检测失败不阻断主流程
        pass

    return {"passed": True, "reason": "ok"}
