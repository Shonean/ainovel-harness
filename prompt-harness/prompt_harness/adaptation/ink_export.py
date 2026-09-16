"""改编层 v0 — pack → story.ink 导出（规格 §9.1 / §18 样例对齐）。

结构：EXTERNAL 声明 + VAR flags + 入口 divert + knot 流。
- scene → `=== id ===`；行按 kind 渲染；`next` → `-> id`
- choice → 多行缩进式（inkjs 2.4 不接受一行式 `* [x] ~ v = true -> y`）
- 收束 scene 指向结局时，先输出条件结局分流 `{ flags.x: -> end_a }`，
  再流向社会默认结局 `-> <scene.next>`
- ending → 文本 + `-> END`
- stage → `~ stage("cue")`（需 EXTERNAL 声明，inkjs 预览页绑定外部函数）
"""
from __future__ import annotations

from .models import STAGE_CUES, ChoiceNode, EndingNode, SceneNode, Story

_INK_UNSAFE = str.maketrans({
    "[": "［", "]": "］", "{": "｛", "}": "｝",
    "^": "＾", "|": "｜", "#": "＃",
})


def escape_ink(text: str) -> str:
    """选项/文本中的 ink 保留字符替换为全角，避免破坏语法。"""
    out = (text or "").translate(_INK_UNSAFE)
    # 行首 ~ 是逻辑语句前缀，正文需转全角
    if out.startswith("~"):
        out = "～" + out[1:]
    return out


def _render_line(ln: dict, char_names: dict[str, str] | None = None) -> list[str]:
    kind = ln.get("kind")
    text = escape_ink(str(ln.get("text") or ""))
    if kind == "stage":
        cue = str(ln.get("cue") or "")
        lines = [f'~ stage("{cue}")']
        if text:
            lines.append(text)
        return lines
    if kind == "dialogue":
        speaker = str(ln.get("speaker") or "")
        if char_names:
            speaker = char_names.get(speaker, speaker)
        return [f'{speaker}：“{text}”' if speaker else text]
    # narration / inner：直接文本（inner 与 narration 的运行时区分在 story.json，
    # ink 骨架层不做区分，见规格 §9.1 样例）
    return [text] if text else []


def ink_var_name(flag_key: str) -> str:
    """story.json 的 flags.read_card → ink 变量名 flags_read_card（ink 标识符不含点）。"""
    return flag_key.replace(".", "_")


def export_ink(story: Story, char_names: dict[str, str] | None = None) -> str:
    """导出 story.ink。char_names: {char_id: 显示名}（对白行人读友好，可选）。"""
    nodes = story.nodes
    node_map = story.node_map()

    # VAR 声明：收集全部 choice.options[].set 的 flags.*
    flag_names: list[str] = []
    for n in nodes:
        if isinstance(n, ChoiceNode):
            for opt in n.options:
                for key in opt.set:
                    if key not in flag_names:
                        flag_names.append(key)

    out: list[str] = []
    # EXTERNAL 声明：存在 stage 行才声明
    has_stage = any(
        ln.kind == "stage"
        for n in nodes if isinstance(n, SceneNode)
        for ln in n.lines
    )
    if has_stage:
        out.append("EXTERNAL stage(cue)")
        out.append("")
    for name in flag_names:
        out.append(f"VAR {ink_var_name(name)} = false")
    if flag_names:
        out.append("")
    out.append(f"-> {story.start}")
    out.append("")

    # 条件结局分流表：flag 名 → ending id（仅 condition 结局）
    cond_endings = [(e.id, ink_var_name(e.condition.strip()))
                    for e in nodes if isinstance(e, EndingNode) and e.condition]

    for n in nodes:
        out.append(f"=== {n.id} ===")
        if isinstance(n, SceneNode):
            for ln in n.lines:
                out.extend(_render_line(ln.model_dump(), char_names))
            if n.next:
                nxt = node_map.get(n.next)
                if isinstance(nxt, EndingNode) and cond_endings:
                    # spec §18 语义：先列条件结局分流，兜底流向默认结局
                    for eid, cond in cond_endings:
                        out.append(f"{{ {cond}: -> {eid} }}")
                    default = next(
                        (e.id for e in nodes
                         if isinstance(e, EndingNode) and e.condition is None),
                        n.next)
                    out.append(f"-> {default}")
                else:
                    out.append(f"-> {n.next}")
        elif isinstance(n, ChoiceNode):
            for opt in n.options:
                out.append(f"* [{escape_ink(opt.text)}]")
                for key, val in opt.set.items():
                    out.append(f"    ~ {ink_var_name(key)} = {'true' if val else 'false'}")
                out.append(f"    -> {opt.next}")
        elif isinstance(n, EndingNode):
            if n.title:
                out.append(escape_ink(n.title))
            if n.text:
                out.append(escape_ink(n.text))
            out.append("-> END")
        out.append("")

    return "\n".join(out).rstrip() + "\n"


def collect_flag_names(story: Story) -> list[str]:
    """全部 choice.options[].set flag 名（preview.html flag 面板用）。"""
    names: list[str] = []
    for n in story.nodes:
        if isinstance(n, ChoiceNode):
            for opt in n.options:
                for key in opt.set:
                    if key not in names:
                        names.append(key)
    return names
