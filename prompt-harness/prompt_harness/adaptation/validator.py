"""改编层 v0 — 校验器（规格 §10 规则全集）。

| 类别 | 规则 |
| Schema | 全文件过 pydantic；schema_version 必须 "0.1" |
| 引用完整性 | next/converge_to/background/voice/interaction_refs/llm_zone 引用必须存在 |
| 图可达性 | 从 start BFS：所有节点可达；所有结局可达；每条路径必然终止于 ending；v0 不允许环 |
| 内容 | 行文本非空；dialogue 的 speaker ∈ characters；stage.cue ∈ 枚举 |
| 资产 | used_by 中的节点必须存在；asset_id 唯一 |
| 模式字段 | 离线必需文件齐全；online 字段缺失只警告 |

errors 非空 → 构建失败（packer 保留中间产物到 `<pack_id>.failed/`）。
"""
from __future__ import annotations

import json
from pathlib import Path

from pydantic import ValidationError

from .models import (
    SCHEMA_VERSION, STAGE_CUES, ChoiceNode, EndingNode, PackData, SceneNode,
    ValidationReport, ValidationStats,
)

# 离线模式必需文件（相对 pack 目录）
REQUIRED_FILES = (
    "pack.json", "story.json", "characters.json", "world.json", "assets.json",
    "interaction.json", "llm_zones.json", "ink/story.ink",
)


def validate_data(pack: PackData, *, files_present: tuple[str, ...] | None = None) -> ValidationReport:
    """校验内存 PackData。files_present 传入实际已生成的文件相对路径集合
    （validate_dir 从磁盘读；构建流程传生成物清单）。"""
    report = ValidationReport(ok=True)

    # ── Schema / 版本 ──
    if pack.info.schema_version != SCHEMA_VERSION:
        report.add_error("SCHEMA_VERSION",
                         f"schema_version={pack.info.schema_version!r}，必须为 {SCHEMA_VERSION!r}")

    # 文件索引齐全（模式字段：离线必需文件齐全）
    present = set(files_present) if files_present is not None else None
    for rel in REQUIRED_FILES:
        if present is not None and rel not in present:
            report.add_error("MISSING_FILE", f"离线必需文件缺失：{rel}")
    if present is not None and "ink/story.ink.json" not in present:
        # 编译产物由 compiler 生成；缺失在构建流程中会以 INK_COMPILE 错误出现
        report.add_warning("INK_JSON_MISSING", "编译产物 ink/story.ink.json 未生成（编译可能未执行）")

    story = pack.story
    node_map = story.node_map()
    ids = list(node_map)
    if len(ids) != len(story.nodes):
        report.add_error("DUPLICATE_NODE_ID", "节点 id 重复")

    char_ids = {c.id for c in pack.characters.characters}
    asset_ids = [a.asset_id for a in pack.assets.assets]
    bg_ids = {a.asset_id for a in pack.assets.assets if a.kind == "background"}
    interaction_ids = {e.id for e in pack.interaction.entities}
    zone_ids = {z.id for z in pack.llm_zones.zones}

    # ── 资产：asset_id 唯一；used_by 节点必须存在 ──
    seen_assets: set[str] = set()
    for a in pack.assets.assets:
        if a.asset_id in seen_assets:
            report.add_error("DUPLICATE_ASSET_ID", f"asset_id 重复：{a.asset_id}", a.asset_id)
        seen_assets.add(a.asset_id)
        for nid in a.used_by:
            if nid not in node_map:
                report.add_error("DANGLING_USED_BY",
                                 f"资产 {a.asset_id} 的 used_by 节点不存在：{nid}", a.asset_id)
        # ref 完整性：voice/portrait → characters；background → world.locations
        if a.kind in ("voice", "portrait") and a.ref and a.ref not in char_ids:
            report.add_error("DANGLING_ASSET_REF",
                             f"资产 {a.asset_id} 引用的角色不存在：{a.ref}", a.asset_id)
        if a.kind == "background" and a.ref and a.ref not in {
                loc.id for loc in pack.world.locations}:
            report.add_error("DANGLING_ASSET_REF",
                             f"背景资产 {a.asset_id} 引用的地点不存在：{a.ref}", a.asset_id)

    # choice set flags（结局 condition 的合法域；存裸 flag 名，如 read_card）
    used_flags: set[str] = set()
    for n in story.nodes:
        if isinstance(n, ChoiceNode):
            for opt in n.options:
                for key in opt.set:
                    used_flags.add(key[len("flags."):] if key.startswith("flags.") else key)

    for n in story.nodes:
        nid = n.id
        if isinstance(n, SceneNode):
            # 引用完整性
            if n.next and n.next not in node_map:
                report.add_error("DANGLING_NEXT", f"next 指向不存在的节点：{n.next}", nid)
            if n.background and n.background not in bg_ids:
                report.add_error("DANGLING_BACKGROUND",
                                 f"background 资产不存在：{n.background}", nid)
            for r in n.interaction_refs:
                if r not in interaction_ids:
                    report.add_error("DANGLING_INTERACTION",
                                     f"interaction 实体不存在：{r}", nid)
            for cid in n.present:
                if cid not in char_ids:
                    report.add_error("UNKNOWN_CHARACTER",
                                     f"present 角色不存在：{cid}", nid)
            # 内容
            for i, ln in enumerate(n.lines):
                if not str(ln.text or "").strip():
                    report.add_error("EMPTY_TEXT", f"第{i + 1}行文本为空", nid)
                if ln.kind == "dialogue":
                    if not ln.speaker:
                        report.add_error("SPEAKER_MISSING", f"第{i + 1}条对白缺 speaker", nid)
                    elif ln.speaker not in char_ids:
                        report.add_error("UNKNOWN_SPEAKER",
                                         f"第{i + 1}条对白 speaker 不在 characters：{ln.speaker}", nid)
                if ln.kind == "stage" and ln.cue not in STAGE_CUES:
                    report.add_error("INVALID_STAGE_CUE",
                                     f"stage.cue 非法：{ln.cue}（允许：{', '.join(STAGE_CUES)}）", nid)
        elif isinstance(n, ChoiceNode):
            if n.converge_to not in node_map:
                report.add_error("DANGLING_CONVERGE",
                                 f"converge_to 指向不存在的节点：{n.converge_to}", nid)
            if not (2 <= len(n.options) <= 3):
                report.add_error("INVALID_CHOICE",
                                 f"选项数 {len(n.options)} 不在 2~3", nid)
            option_nexts = {o.next for o in n.options}
            for o in n.options:
                if o.next not in node_map:
                    report.add_error("DANGLING_NEXT",
                                     f"选项「{o.text}」next 指向不存在的节点：{o.next}", nid)
                if not str(o.text or "").strip():
                    report.add_error("EMPTY_TEXT", "选项文本为空", nid)
            # 收敛约束：全部选项 next 相同且 == converge_to
            if len(option_nexts) > 1:
                report.add_error("NOT_CONVERGENT",
                                 f"选项 next 不收敛：{sorted(option_nexts)}", nid)
            elif n.converge_to and option_nexts and option_nexts != {n.converge_to}:
                report.add_error("CONVERGE_MISMATCH",
                                 f"converge_to={n.converge_to} 与选项 next 不一致", nid)
            if n.llm_zone and n.llm_zone not in zone_ids:
                report.add_error("DANGLING_LLM_ZONE",
                                 f"llm_zone 不存在：{n.llm_zone}", nid)
        elif isinstance(n, EndingNode):
            if not str(n.text or "").strip():
                report.add_error("EMPTY_TEXT", "结局文本为空", nid)
            if n.condition is not None:
                cond = n.condition.strip()
                if not cond.startswith("flags."):
                    report.add_error(
                        "INVALID_CONDITION",
                        f"v0 condition 仅支持 flags.<name> 布尔：{cond!r}", nid)
                else:
                    flag = cond[len("flags."):].strip()
                    if flag not in used_flags:
                        report.add_error(
                            "UNKNOWN_CONDITION_FLAG",
                            f"condition 引用未在任何选择中设置的 flag：{cond}", nid)

    # ── 图可达性 / 环 / 终止性 ──
    ending_ids_all = {n.id for n in story.nodes if isinstance(n, EndingNode)}
    default_ending_ids = {n.id for n in story.nodes
                          if isinstance(n, EndingNode) and n.condition is None}

    def out_edges(nid: str) -> list[str]:
        n = node_map.get(nid)
        if n is None:
            return []
        if isinstance(n, SceneNode):
            if not n.next:
                return []
            # spec §18 语义：scene.next 指向条件结局时，默认结局是条件不满足的
            # 隐式出口（ink：`{ flags.x: -> end_a }` + `-> end_b`）
            if n.next in ending_ids_all and default_ending_ids:
                return [n.next] + sorted(default_ending_ids - {n.next})
            return [n.next]
        if isinstance(n, ChoiceNode):
            return [o.next for o in n.options if o.next]
        return []  # ending

    if story.start not in node_map:
        report.add_error("DANGLING_NEXT", f"start 指向不存在的节点：{story.start}")
    else:
        # BFS 可达
        seen = {story.start}
        queue = [story.start]
        while queue:
            cur = queue.pop(0)
            for nxt in out_edges(cur):
                if nxt in node_map and nxt not in seen:
                    seen.add(nxt)
                    queue.append(nxt)
        for nid in ids:
            if nid not in seen:
                report.add_error("UNREACHABLE_NODE", f"节点从 start 不可达：{nid}", nid)
        for eid in ending_ids_all:
            if eid not in seen:
                report.add_error("UNREACHABLE_ENDING", f"结局不可达：{eid}", eid)
        # 环（v0 不允许）+ 死路（非 ending 无出边 ⇒ 存在不终止于 ending 的路径）
        WHITE, GRAY, BLACK = 0, 1, 2
        color = {nid: WHITE for nid in ids}

        def dfs(nid: str) -> None:
            color[nid] = GRAY
            for nxt in out_edges(nid):
                if nxt not in node_map:
                    continue
                if color.get(nxt) == GRAY:
                    report.add_error("CYCLE_FORBIDDEN",
                                     f"v0 不允许环：{nid} -> {nxt}", nid)
                elif color.get(nxt) == WHITE:
                    dfs(nxt)
            color[nid] = BLACK

        dfs(story.start)
        for nid in ids:
            n = node_map[nid]
            if not isinstance(n, EndingNode) and not out_edges(nid):
                report.add_error("DEAD_END", f"非结局节点没有出边（路径无法终止于结局）：{nid}", nid)

    # ── 模式字段：online 缺失只警告 ──
    if pack.info.modes.online.enabled and not pack.llm_zones.zones:
        report.add_warning("ONLINE_ZONE_MISSING",
                           "modes.online.enabled=true 但 llm_zones 为空")

    # ── stats ──
    report.stats = ValidationStats(
        nodes=len(story.nodes),
        scenes=sum(1 for n in story.nodes if isinstance(n, SceneNode)),
        choices=sum(1 for n in story.nodes if isinstance(n, ChoiceNode)),
        endings=sum(1 for n in story.nodes if isinstance(n, EndingNode)),
        lines=sum(len(n.lines) for n in story.nodes if isinstance(n, SceneNode)),
        assets=len(pack.assets.assets),
    )
    return report.finalize()


def validate_dir(pack_dir: str | Path) -> ValidationReport:
    """读取已落盘 pack 目录并校验（CLI validate / server /pack 用）。"""
    d = Path(pack_dir)

    def _load(rel: str):
        p = d / rel
        if not p.is_file():
            return None
        return json.loads(p.read_text(encoding="utf-8"))

    present = tuple(
        str(p.relative_to(d)).replace("\\", "/")
        for p in d.rglob("*") if p.is_file()
    )
    report = ValidationReport(ok=True)

    required = [rel for rel in REQUIRED_FILES if rel not in present]
    if required:
        for rel in required:
            report.add_error("MISSING_FILE", f"离线必需文件缺失：{rel}")
        return report.finalize()

    try:
        pack = PackData(
            info=_load("pack.json"),
            story=_load("story.json"),
            characters=_load("characters.json"),
            world=_load("world.json"),
            assets=_load("assets.json"),
            interaction=_load("interaction.json"),
            llm_zones=_load("llm_zones.json"),
            ink_text=(d / "ink" / "story.ink").read_text(encoding="utf-8"),
        )
    except (ValidationError, json.JSONDecodeError, KeyError) as exc:
        report.add_error("MODEL_INVALID", f"pack 文件未通过 schema 校验：{exc}")
        return report.finalize()

    report = validate_data(pack, files_present=present)
    # 保留编译期错误（story.ink.json 缺失时）
    if "ink/story.ink.json" not in present:
        report.add_warning("INK_JSON_MISSING",
                           "编译产物 ink/story.ink.json 不在目录中（pack 可能只构建了一半）")
        report.finalize()
    return report
