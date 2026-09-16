"""改编层 v0 — 对白说话人归一化（规格 §7 四步）。

输入是自由叙事体（如 `陈守念对着空教室低声问道：“这是哪儿？”`），输出结构化
dialogue 行（speaker/expression/text）。流程：

1. 正则优先（规格 §7 的正则，说话人与冒号间修饰语单独捕获用于语气提取）
2. 代词回填：`他/她/它` → 当前场景 present 中唯一角色；不唯一则不回填
3. LLM 兜底（批量 1 次/章）：llm_hook 返回 {index: speaker|""}，新增角色名视为非法
4. 仍未解析：降级 narration + SPEAKER_UNRESOLVED 警告
   语气词（低声/急道/…）提取为 expression，识别不了留空。

本模块不做 I/O：LLM 通过注入的异步 llm_hook 调用（见 cli._default_llm_hook），
单测一律 mock。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

# 步骤 1 正则：speaker(2~8 非标点) + 修饰语(≤12 非引号) + 冒号 + 引号包裹台词
RE_SPEECH = re.compile(
    r"^(?P<speaker>[^，。：！？]{2,8})(?P<desc>[^“”]{0,12})[：:]\s*[“\"](?P<line>.*?)[”\"]\s*$"
)
# 步骤 2 宽松版：任意非引号前导 + 冒号 + 引号台词（用于代词回填/已知名回填）
RE_SPEECH_LOOSE = re.compile(
    r"^(?P<desc>[^“”]{0,40}?)[：:]\s*[“\"](?P<line>.*?)[”\"]\s*$"
)
RE_PRONOUN = re.compile(r"^(他|她|它|他们|她们|它们)")

# 语气词 → expression
TONE_MAP: list[tuple[str, str]] = [
    ("低声", "low"), ("喃喃", "mutter"), ("自言自语", "mutter"), ("嘀咕", "mutter"),
    ("急道", "urgent"), ("急忙", "urgent"), ("失声", "urgent"),
    ("发哑", "hoarse"), ("沙哑", "hoarse"),
    ("颤抖", "trembling"), ("发抖", "trembling"),
    ("冷冷", "cold"), ("淡淡", "calm"), ("平静", "calm"), ("很平", "calm"),
    ("一字一顿", "emphatic"),
    ("笑道", "smiling"), ("笑着", "smiling"),
    ("哭", "crying"),
    ("喊", "shouting"), ("吼", "shouting"), ("叫道", "shouting"),
    ("怒", "angry"),
    ("低语", "low"), ("细语", "low"),
]

PRONOUNS = ("他", "她", "它")


@dataclass
class DialogueLine:
    """归一化结果：unresolved=True 时调用方须降级为 narration。"""

    kind: str = "dialogue"
    speaker: str = ""
    expression: str = ""
    text: str = ""
    unresolved: bool = False
    raw: str = ""
    warnings: list[dict] = field(default_factory=list)


def extract_expression(desc: str) -> str:
    for word, expr in TONE_MAP:
        if word in desc:
            return expr
    return ""


def _known_speaker_prefix(desc: str, name_map: dict[str, str]) -> str | None:
    """desc 以某个已知角色名/别名开头 → 返回规范名。最长名优先。"""
    for name in sorted(name_map, key=len, reverse=True):
        if desc.startswith(name):
            return name_map[name]
    return None


def _pronoun_speaker(desc: str, present_norm: list[str]) -> str | None:
    """代词回填：仅当 desc 以代词开头且 present 恰有一个角色。"""
    if not RE_PRONOUN.match(desc):
        return None
    if len(present_norm) == 1:
        return present_norm[0]
    return None


async def normalize_dialogues(
    texts: list[str],
    present: list[str],
    known_names: list[str],
    llm_hook=None,
    alias_map: dict[str, list[str]] | None = None,
) -> tuple[list[DialogueLine], list[dict]]:
    """把一组对白原文归一为 DialogueLine。

    Args:
        texts: 原始对白句（l4 dialogues 顺序）。
        present: 当前场景在场角色规范名（代词回填候选）。
        known_names: 全部合法角色规范名；LLM 返回名单之外的名字一律视为非法。
        llm_hook: async ({task, system, user}) -> dict|None。
            task="dialogue_speaker"，期望返回 {"speakers": {"0": "名"|"", ...}}。
        alias_map: 规范名 → 别名列表（别名也可作为说话人识别依据）。

    Returns:
        (lines, warnings)：warnings 为 [{code: SPEAKER_UNRESOLVED, detail, index}]。
    """
    warnings: list[dict] = []
    lines: list[DialogueLine] = []

    # 别名 → 规范名（known_names 里可能含别名，须被 alias_map 覆盖回规范名）
    name_map: dict[str, str] = {n: n for n in known_names}
    for canonical, aliases in (alias_map or {}).items():
        name_map[canonical] = canonical
        for alias in aliases or []:
            name_map[alias] = canonical

    pending: list[int] = []  # 需要 LLM 兜底的下标
    for i, raw in enumerate(texts):
        text = (raw or "").strip()
        if not text:
            lines.append(DialogueLine(kind="narration", text="", raw=raw, unresolved=True))
            continue
        m = RE_SPEECH.match(text)
        if m:
            speaker_raw = m.group("speaker")
            desc = m.group("desc") or ""
            line = m.group("line").strip()
            speaker = name_map.get(speaker_raw) or _known_speaker_prefix(speaker_raw, name_map)
            if speaker is None and RE_PRONOUN.match(speaker_raw):
                # 代词回填（spec §7 步骤 2）：说话人以 他/她/它（们） 开头，
                # 且当前场景 present 恰有一个角色才回填
                speaker = _pronoun_speaker(speaker_raw, present)
            if speaker is None and len(present) == 1 and speaker_raw == present[0]:
                speaker = speaker_raw
            lines.append(DialogueLine(
                speaker=speaker or "",
                # 语气词在整个说话人引入段（含被 speaker 吞掉的修饰语）里找
                expression=extract_expression(f"{speaker_raw}{desc}"),
                text=line,
                unresolved=speaker is None,
                raw=raw,
            ))
            if speaker is None:
                pending.append(i)
            continue

        m2 = RE_SPEECH_LOOSE.match(text)
        if m2:
            desc = m2.group("desc") or ""
            line = m2.group("line").strip()
            speaker = _known_speaker_prefix(desc, name_map) or _pronoun_speaker(desc, present)
            lines.append(DialogueLine(
                speaker=speaker or "",
                expression=extract_expression(desc),
                text=line,
                unresolved=speaker is None,
                raw=raw,
            ))
            if speaker is None:
                pending.append(i)
            continue

        # 无引号/无冒号结构：直接进 LLM 兜底
        lines.append(DialogueLine(text=text, unresolved=True, raw=raw))
        pending.append(i)

    # 步骤 3：LLM 批量兜底（1 次调用处理整章未解析对白）
    if pending and llm_hook is not None:
        llm_speakers = await _llm_batch(
            [lines[i].raw for i in pending], present, list(name_map), llm_hook)
        for j, i in enumerate(pending):
            sp = (llm_speakers.get(str(j)) or llm_speakers.get(j) or "").strip()
            if sp and sp in name_map:
                lines[i].speaker = name_map[sp]
                lines[i].unresolved = False
            elif sp:
                # 新增角色名 → 非法，保持未解析
                warnings.append({
                    "code": "SPEAKER_REJECTED",
                    "detail": f"LLM 返回未知角色「{sp}」（第 {j + 1} 条），已忽略",
                    "index": i,
                })

    # 步骤 4：仍未解析 → 降级 narration + 警告
    final: list[DialogueLine] = []
    for i, ln in enumerate(lines):
        if ln.unresolved:
            raw = (ln.raw or ln.text or "").strip()
            final.append(DialogueLine(kind="narration", text=raw, raw=ln.raw))
            warnings.append({
                "code": "SPEAKER_UNRESOLVED",
                "detail": f"第{i + 1}条对白说话人未识别，已降级为 narration",
                "index": i,
            })
        else:
            final.append(ln)
    return final, warnings


async def _llm_batch(
    texts: list[str], present: list[str], known: list[str], llm_hook
) -> dict:
    system = (
        "你是小说结构化助手。给定一个场景的在场角色与若干对白原文，判断每条对白的说话人。"
        "只能输出 JSON：{\"speakers\": {\"序号\": \"角色名或空字符串\"}}。"
        "角色名必须来自给出的在场角色或已知角色名单，绝不允许发明新角色；"
        "判断不了就输出空字符串。"
    )
    user = (
        f"在场角色：{ '、'.join(present) or '（无）' }\n"
        f"已知角色名单：{ '、'.join(known) or '（无）' }\n"
        "对白原文（按序号）：\n" +
        "\n".join(f"{i}. {t}" for i, t in enumerate(texts))
    )
    try:
        data = await llm_hook({"task": "dialogue_speaker", "system": system, "user": user})
    except Exception:
        return {}
    if not isinstance(data, dict):
        return {}
    sp = data.get("speakers")
    return sp if isinstance(sp, dict) else {}
