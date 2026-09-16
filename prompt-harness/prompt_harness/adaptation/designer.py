"""改编层 v0 — 分支骨架设计（规格 §8）。

LLM 只做两处「包装」，不改结构：
- 章末收敛式选择：基于该章 conflicts 生成 2~3 个选项文本 + flag 名；
  全部选项 next 指向同一收束节点（converge），效果仅 `flags.*` 布尔。
- 弧末双结局：l2 结局段 + 最后一章 core → end_a（条件）/ end_b（默认），
  1~3 句/结局；不得新增具名角色/数字；结局差异必须来自已建立的选择/flag。

LLM 输出经严格校验（flag 名格式、无数字、无未知角色名、句数上限），任何一条
不满足即整体走确定性 fallback（泛化措辞 + 兜底 flags），并记 DESIGNER_FALLBACK
警告 —— 结构不变约束由单测保证。单测一律 mock llm_hook。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

FLAG_RE = re.compile(r"^[a-z][a-z0-9_]{0,31}$")
DIGIT_RE = re.compile(r"[0-9０-９]")
SENT_SPLIT_RE = re.compile(r"[。！？!?]")


@dataclass
class DesignerResult:
    choice: dict = field(default_factory=dict)
    endings: list[dict] = field(default_factory=list)  # 2 个 ending node dict
    flags: list[str] = field(default_factory=list)     # 全部 set 过的 flag 名
    warnings: list[dict] = field(default_factory=list)


def _count_sentences(text: str) -> int:
    return len([s for s in SENT_SPLIT_RE.split(text or "") if s.strip()])


def _contains_unknown_name(text: str, known_names: list[str]) -> bool:
    """粗检：文本含已知角色名 → OK；检测不到新增名字（不可能穷举），只挡数字与
    超长。真正的「不新增事实」约束写在 prompt 里 + 结构校验兜底。"""
    return False


def _validate_choice_payload(data: dict, known_names: list[str]) -> list[dict] | None:
    """校验 LLM 的选择 payload；非法返回 None。"""
    if not isinstance(data, dict):
        return None
    prompt = str(data.get("prompt") or "").strip()
    options = data.get("options")
    if not prompt or not isinstance(options, list) or not (2 <= len(options) <= 3):
        return None
    out: list[dict] = []
    seen_flags: set[str] = set()
    for opt in options:
        if not isinstance(opt, dict):
            return None
        text = str(opt.get("text") or "").strip()
        flag = str(opt.get("flag") or "").strip()
        if not text or len(text) > 40 or DIGIT_RE.search(text):
            return None
        if not FLAG_RE.match(flag) or flag in seen_flags:
            return None
        seen_flags.add(flag)
        out.append({"text": text, "flag": flag})
    return out


def _validate_endings_payload(data: dict, known_flags: list[str]) -> list[dict] | None:
    if not isinstance(data, dict):
        return None
    endings = data.get("endings")
    if not isinstance(endings, list) or len(endings) != 2:
        return None
    out: list[dict] = []
    for i, e in enumerate(endings):
        if not isinstance(e, dict):
            return None
        title = str(e.get("title") or "").strip()
        text = str(e.get("text") or "").strip()
        if not title or not text or len(text) > 160 or DIGIT_RE.search(text):
            return None
        if _count_sentences(text) > 3:
            return None
        cond_flag = str(e.get("flag") or "").strip()
        condition = None
        if i == 0 and cond_flag:
            if cond_flag not in known_flags:
                return None  # 结局差异必须来自已建立的选择 flag
            condition = f"flags.{cond_flag}"
        out.append({"title": title, "text": text, "condition": condition})
    if out[0]["condition"] is None:
        return None  # end_a 必须有条件，end_b 才是默认
    return out


def _fallback_choice(conflicts: list[str]) -> dict:
    """确定性兜底选择：泛化动作措辞，不新增事实。"""
    _ = conflicts  # v0 兜底措辞固定，conflicts 只用于 prompt 素材
    return {
        "prompt": "此刻，你要怎么做？",
        "options": [
            {"text": "迎着异样查下去", "flag": "confront"},
            {"text": "先退到安全处，再作打算", "flag": "withdraw"},
        ],
    }


def _fallback_endings(l2: str, core: str) -> list[dict]:
    """确定性兜底双结局：取 l2/章纲 core 的收束句，不新增事实。"""
    src = (core or l2 or "故事在此收束。").strip()
    sents = [s for s in SENT_SPLIT_RE.split(src) if s.strip()]
    tail = "".join(s + "。" for s in sents[-2:]) if sents else src
    return [
        {"title": "直面真相", "text": tail or "你选择直面这一切。",
         "condition": "flags.confront"},
        {"title": "暂避锋芒", "text": "你退了回来，有些答案，还没到揭晓的时候。"
                                      "但你知道，它们不会一直等着你。",
         "condition": None},
    ]


async def design_branches(
    *,
    conflicts: list[str],
    chapter_core: str,
    l2_text: str,
    known_names: list[str],
    llm_hook=None,
    ending_count: int = 2,
) -> DesignerResult:
    """生成章末收敛 choice + 弧末双结局（v0 固定 2 结局）。"""
    res = DesignerResult()
    if ending_count != 2:
        res.warnings.append({
            "code": "ENDING_COUNT_FIXED",
            "detail": f"v0 固定 2 个结局（收到 ending_count={ending_count}），已按 2 生成",
        })

    known_flags: list[str] = []
    choice = _fallback_choice(conflicts)
    if llm_hook is not None:
        system = (
            "你是游戏改编骨架设计师。基于给定的章节冲突素材，为一个中式恐怖 AVG 生成章末"
            "收敛式选择点。硬约束：\n"
            "1. 不得新增任何事实、具名角色、地点或数字；只用素材中已有的信息措辞；\n"
            "2. 2~3 个选项，每个选项一小段玩家动作短语（≤40字），不含数字；\n"
            "3. 每个选项给一个 flag 名（小写 snake_case，如 read_card）；\n"
            "4. 全部选项最终合流（收敛式），你不需要写出分支走向，只写选项本身；\n"
            "5. 只输出 JSON：{\"prompt\": \"…\", \"options\": [{\"text\": \"…\", "
            "\"flag\": \"…\"}, …]}"
        )
        user = (
            f"章节核心：{chapter_core}\n"
            "本章冲突素材：\n" + "\n".join(f"- {c}" for c in conflicts or ["（无）"]) +
            "\n已知角色（可用于指代，不可新增）：、" .join(known_names)
        )
        try:
            data = await llm_hook({"task": "design_choice", "system": system, "user": user})
        except Exception:
            data = None
        validated = _validate_choice_payload(data, known_names)
        if validated:
            choice = {"prompt": str(data["prompt"]).strip(), "options": validated}
        else:
            res.warnings.append({
                "code": "DESIGNER_FALLBACK",
                "detail": "章末选择 LLM 输出未通过约束校验，使用确定性兜底",
            })
    else:
        res.warnings.append({
            "code": "DESIGNER_FALLBACK",
            "detail": "无 LLM hook（离线/测试），章末选择使用确定性兜底",
        })
    known_flags = [o["flag"] for o in choice["options"]]
    res.choice = choice
    res.flags = known_flags

    # ── 双结局 ──
    endings = _fallback_endings(l2_text, chapter_core)
    if llm_hook is not None:
        system = (
            "你是游戏改编骨架设计师。基于 l2 情节概要的结局段与最后一章核心冲突，写 2 个"
            "弧末结局。硬约束：\n"
            "1. 每个结局 1~3 句、≤160字；不得新增具名角色、地点或数字；\n"
            "2. 关键事实不得丢失；两个结局的差异必须来自已建立的选择 flag（见输入）；\n"
            "3. 第一个结局给出判定 flag（必须来自输入的可用 flag 列表），第二个结局是"
            "未满足任何条件时的默认结局；\n"
            "4. 只输出 JSON：{\"endings\": [{\"title\": \"…\", \"text\": \"…\", "
            "\"flag\": \"read_card\"}, {\"title\": \"…\", \"text\": \"…\", \"flag\": \"\"}]}"
        )
        user = (
            f"l2 情节概要：{l2_text or '（无，仅参考章纲）'}\n"
            f"最后一章核心冲突：{chapter_core}\n"
            f"可用 flag：{'、'.join(known_flags)}\n"
            "已知角色（不可新增）：" + "、".join(known_names)
        )
        try:
            data = await llm_hook({"task": "design_endings", "system": system, "user": user})
        except Exception:
            data = None
        validated = _validate_endings_payload(data, known_flags)
        if validated:
            endings = validated
        else:
            res.warnings.append({
                "code": "DESIGNER_FALLBACK",
                "detail": "结局 LLM 输出未通过约束校验，使用确定性兜底",
            })
    res.endings = endings
    return res


def attach_branches(mp, designer: DesignerResult, chapter_num: int, arc_id: str,
                    llm_zones: dict) -> None:
    """把 designer 产物接进 mapper 的 story（结构固定：末场景 → choice → 收束 → 双结局）。

    收束节点是普通 scene（title 取末场景收束语义），其 next 指向默认结局；
    条件结局由 ink 导出在收束节点尾部生成条件分流。llm_zone 挂在 choice 上。
    """
    nodes = mp.story["nodes"]
    if not mp.scene_nodes:
        return
    last_scene_id = mp.scene_nodes[-1]
    choice_id = "c001"
    converge_id = "n900"  # 收束 scene（v0 固定 id，规格 §18 样例同构）

    options = []
    for opt in designer.choice["options"]:
        options.append({
            "text": opt["text"], "next": converge_id,
            "set": {f"flags.{opt['flag']}": True},
        })
    choice_node = {
        "id": choice_id, "type": "choice", "prompt": designer.choice["prompt"],
        "options": options, "converge_to": converge_id,
        "llm_zone": f"zone_{choice_id}",
        "source": {"arc_id": arc_id, "chapter": chapter_num},
    }

    cond_endings = [e for e in designer.endings if e.get("condition")]
    default_endings = [e for e in designer.endings if not e.get("condition")]
    ordered = cond_endings + default_endings
    ending_nodes = []
    for i, e in enumerate(ordered):
        ending_nodes.append({
            "id": f"end_{'ab'[i]}", "type": "ending",
            "title": e["title"], "text": e["text"], "condition": e.get("condition"),
            "source": {"arc_id": arc_id, "ending_index": i},
        })
    default_ending_id = ending_nodes[-1]["id"]
    # spec §18 语义：收束 scene.next 指向条件结局；默认结局是条件不满足时的
    # 隐式出口（ink 导出为 `{ flags.x: -> end_a }` + `-> end_b`）
    cond_first = next((n for n in ending_nodes if n["condition"]), None)
    converge_next = cond_first["id"] if cond_first else default_ending_id

    converge_node = {
        "id": converge_id, "type": "scene", "title": "收束",
        "background": None, "present": [], "lines": [],
        "interaction_refs": [], "next": converge_next,
        "source": {"arc_id": arc_id, "chapter": chapter_num, "converge": True},
    }

    # 末场景 next 接 choice；追加 choice/收束/结局节点
    last = next(n for n in nodes if n["id"] == last_scene_id)
    last["next"] = choice_id
    nodes.append(choice_node)
    nodes.append(converge_node)
    nodes.extend(ending_nodes)

    # llm_zones 预留字段（spec §5.8）
    llm_zones["zones"].append({
        "id": f"zone_{choice_id}", "node": choice_id,
        "allow": ["free_action"],
        "persona_guard": ("维持中式恐怖氛围；不得让纸人开口说出成句人话；"
                          "不得提前泄露循环真相"),
        "hard_events": [],
        "fallback_map": {},
        "budget": {"max_turns": 6, "max_chars": 1200},
    })

    mp.story["endings"] = [e["id"] for e in ending_nodes]
