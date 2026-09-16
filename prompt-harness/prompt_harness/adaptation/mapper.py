"""改编层 v0 — 确定性映射（规格 §6 表逐条实现）。

l4 场景叶子 → scene 节点；elements → characters/world；资产需求 → assets 清单
（只出 prompt 不生成）；details 首条 → inspect 交互预留。

对白行在此以未解析形态占位（kind="dialogue"、speaker=None），由 pipeline 调
dialogue.normalize_dialogues 后回填；conflicts 只收集为章末 choice 素材，
不直接成节点。本模块确定性、无 LLM（golden file 测试的稳定输出源）。
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field

from .loader import ArcSnapshot

GENRE_TAGS = ["悬疑", "恐怖"]  # 纸人教室题材默认标签（v0 常量）


@dataclass
class MappedPack:
    """mapper 输出的中间产物（dict 形态，最终组装时才过 pydantic）。"""

    story: dict = field(default_factory=dict)
    characters: dict = field(default_factory=dict)
    world: dict = field(default_factory=dict)
    assets: dict = field(default_factory=dict)
    interaction: dict = field(default_factory=dict)
    llm_zones: dict = field(default_factory=dict)
    scene_nodes: list[str] = field(default_factory=list)
    conflicts: list[str] = field(default_factory=list)
    raw_dialogues: dict[str, list[str]] = field(default_factory=dict)  # node_id → 原始对白
    protagonist_char: str = ""
    warnings: list[dict] = field(default_factory=list)


def _hash8(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:8]


def _scene_text(scene: dict) -> str:
    parts = [str(scene.get("environment") or "")]
    for key in ("actions", "narration", "dialogues", "psychologies", "details"):
        parts.extend(str(x) for x in (scene.get(key) or []))
    return "\n".join(parts)


def _slug_book(book_title: str) -> str:
    """书名 → pack_id 片段：保留 ascii 字母数字，中文丢弃；全中文 → 'book'。"""
    words = re.findall(r"[A-Za-z0-9]+", book_title or "")
    slug = "_".join(w.lower() for w in words) or "book"
    return slug[:40]


def compute_pack_id(snap: ArcSnapshot) -> str:
    """pack_id = pack_<book_slug>_<arc_id>_<6位hash>（hash 取自输入快照）。"""
    payload = json_dumps_sorted({
        "elements": snap.elements,
        "l1": snap.l1, "l2": snap.l2,
        "chapter": {"num": snap.chapter_num, "title": snap.chapter.title if snap.chapter else ""},
        "scenes": snap.scenes,
        "arc_id": snap.arc_id,
    })
    h = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:6]
    return f"pack_{_slug_book(snap.book_title)}_{snap.arc_id}_{h}"


def json_dumps_sorted(data) -> str:
    import json
    return json.dumps(data, ensure_ascii=False, sort_keys=True, default=str)


# ============================================================
# elements 投影
# ============================================================

def build_characters(snap: ArcSnapshot, presence: dict[str, list[str]]) -> tuple[dict, str]:
    """elements.characters → characters.json；selected 优先，未出场且未选中不收录。"""
    chars_out: list[dict] = []
    selected = (snap.selected.get("characters") or [])
    protagonist_id = selected[0] if selected else None

    elems = [c for c in (snap.elements.get("characters") or []) if isinstance(c, dict)]
    if not elems:
        # 没有角色表：从场景对白硬提（无 source_element），主角占位
        protagonist_id = "char_protagonist"
        chars_out.append({
            "id": protagonist_id, "name": "主角", "role": "protagonist",
            "aliases": [], "description": "（elements.json 无角色表，mapper 占位）",
            "source_element": None, "presence": sorted(presence.get(protagonist_id, [])),
            "assets": {"portrait": f"spr_{protagonist_id}_default", "expressions": ["default"]},
            "voice": {"kokoro": "zm_x", "cloud": "voice_id_placeholder"},
        })
        return {"characters": chars_out}, protagonist_id

    # 出场判定在 mapper 主流程完成后回填 presence；这里先按 selected/出场过滤
    ordered = sorted(
        elems,
        key=lambda c: (0 if c.get("id") in selected else 1),
    )
    for c in ordered:
        cid = str(c.get("id") or f"char_{len(chars_out) + 1:03d}")
        appeared = bool(presence.get(cid)) or cid in selected
        if not appeared:
            continue
        fields_desc = "；".join(
            f"{f.get('name')}:{f.get('value')}" for f in (c.get("fields") or [])
            if isinstance(f, dict) and (f.get("name") or f.get("value"))
        )
        desc = "，".join(x for x in [str(c.get("desc") or ""), fields_desc] if x)
        chars_out.append({
            "id": cid,
            "name": str(c.get("name") or cid),
            "role": "protagonist" if cid == protagonist_id else "support",
            "aliases": [str(a) for a in (c.get("alias") or [])],
            "description": desc,
            "source_element": cid,
            "presence": sorted(presence.get(cid, [])),
            "assets": {"portrait": f"spr_{cid}_default", "expressions": ["default"]},
            "voice": {"kokoro": "zm_x", "cloud": "voice_id_placeholder"},
        })
    if protagonist_id and not any(c["id"] == protagonist_id for c in chars_out):
        # selected 的主角没在任何场景出场（数据混杂）：仍收录保证引用完整
        c = next((x for x in elems if x.get("id") == protagonist_id), None)
        if c:
            chars_out.insert(0, {
                "id": protagonist_id, "name": str(c.get("name") or protagonist_id),
                "role": "protagonist", "aliases": [str(a) for a in (c.get("alias") or [])],
                "description": str(c.get("desc") or ""), "source_element": protagonist_id,
                "presence": [], "assets": {"portrait": f"spr_{protagonist_id}_default",
                                           "expressions": ["default"]},
                "voice": {"kokoro": "zm_x", "cloud": "voice_id_placeholder"},
            })
    return {"characters": chars_out}, (protagonist_id or (chars_out[0]["id"] if chars_out else ""))


def build_world(snap: ArcSnapshot) -> dict:
    """elements.locations/items/settings/maps 原样投影 world.json，保留 source id。"""
    def _project(key: str) -> list[dict]:
        out = []
        for i, e in enumerate(snap.elements.get(key) or []):
            if not isinstance(e, dict):
                continue
            fields_desc = "；".join(
                f"{f.get('name')}:{f.get('value')}" for f in (e.get("fields") or [])
                if isinstance(f, dict) and (f.get("name") or f.get("value"))
            )
            desc = "，".join(x for x in [str(e.get("desc") or ""), fields_desc] if x)
            out.append({
                "id": str(e.get("id") or f"{key}_{i + 1:03d}"),
                "name": str(e.get("name") or ""),
                "source_element": str(e.get("id") or ""),
                "description": desc,
            })
        return out

    return {
        "locations": _project("locations"),
        "items": _project("items"),
        "settings": _project("settings"),
        "terms": _project("maps"),
    }


# ============================================================
# 场景 → 节点
# ============================================================

def _detect_location_ref(environment: str, world: dict) -> str | None:
    for loc in world.get("locations") or []:
        if loc["name"] and loc["name"] in environment:
            return loc["id"]
    return None


def _present_chars(scene: dict, snap: ArcSnapshot) -> list[str]:
    """场景文本中提到（名/别名）的 element 角色 → char id 列表。"""
    text = _scene_text(scene)
    out: list[str] = []
    for c in snap.elements.get("characters") or []:
        if not isinstance(c, dict):
            continue
        names = [str(c.get("name") or "")] + [str(a) for a in (c.get("alias") or [])]
        if any(n and n in text for n in names):
            out.append(str(c.get("id")))
    return out


def map_snapshot(snap: ArcSnapshot) -> MappedPack:
    mp = MappedPack()
    nodes: list[dict] = []
    assets: list[dict] = []
    entities: list[dict] = []
    bg_index: dict[str, str] = {}  # environment hash → asset_id
    sfx_index: dict[str, str] = {}
    presence: dict[str, list[str]] = {}
    raw_dialogues: dict[str, list[str]] = {}

    world = build_world(snap)

    for si, scene in enumerate(snap.scenes):
        node_id = f"n{si + 1:03d}"
        mp.scene_nodes.append(node_id)
        environment = str(scene.get("environment") or "").strip()
        lines: list[dict] = []

        # environment → 背景资产 + 开场 narration
        background = None
        if environment:
            key = _hash8(environment)
            bg_id = bg_index.get(key)
            if not bg_id:
                bg_id = f"bg_{key}"
                bg_index[key] = bg_id
                assets.append({
                    "asset_id": bg_id, "kind": "background",
                    "ref": _detect_location_ref(environment, world),
                    "prompt": environment,
                    "used_by": [], "status": "pending", "hash": None,
                    "provider_hint": "seedream",
                })
            background = bg_id
            lines.append({"kind": "narration", "text": environment})

        # actions / narration → narration 行
        for key in ("actions", "narration"):
            for t in (scene.get(key) or []):
                t = str(t).strip()
                if t:
                    lines.append({"kind": "narration", "text": t})

        # details → narration（detail=true）
        details = [str(t).strip() for t in (scene.get("details") or []) if str(t).strip()]
        for t in details:
            lines.append({"kind": "narration", "text": t, "detail": True})

        # psychologies → inner（voice 资产只出 prompt 不生成）
        for pi, t in enumerate(scene.get("psychologies") or []):
            t = str(t).strip()
            if not t:
                continue
            lines.append({"kind": "inner", "text": t})

        # dialogues → 未解析 dialogue 行（pipeline 归一回填）
        dialogues = [str(t).strip() for t in (scene.get("dialogues") or []) if str(t).strip()]
        if dialogues:
            raw_dialogues[node_id] = dialogues
            for t in dialogues:
                lines.append({"kind": "dialogue", "text": t, "speaker": None,
                              "expression": None})

        # present：场景文本提及的角色；空 → 主角（build_characters 后补）
        present_ids = _present_chars(scene, snap)
        for cid in present_ids:
            presence.setdefault(cid, []).append(node_id)

        # interaction 预留：首条 detail → inspect 实体
        interaction_refs: list[str] = []
        if details:
            ent_id = f"int_{node_id}_1"
            entities.append({
                "id": ent_id, "node": node_id, "kind": "inspect",
                "label": (str(scene.get("name") or "")
                          or (snap.chapter.title if snap.chapter else "")
                          or f"场景{si + 1}"),
                "prompt": details[0],
                "result": {"narration": details[0], "set": {}},
                "next": None,
            })
            interaction_refs.append(ent_id)

        nodes.append({
            "id": node_id, "type": "scene", "title": str(scene.get("name") or f"场景{si + 1}"),
            "background": background, "present": present_ids, "lines": lines,
            "interaction_refs": interaction_refs, "next": None,  # 串接后回填
            "source": {"arc_id": snap.arc_id, "scene_index": si, "chapter": snap.chapter_num},
        })
        mp.conflicts.extend(str(t) for t in (scene.get("conflicts") or []) if str(t).strip())

    # scene.next 串联：每章最终场景的下一章——v0 单章弧：最后一个 scene.next 留给 choice
    for i, node in enumerate(nodes):
        node["next"] = nodes[i + 1]["id"] if i + 1 < len(nodes) else None

    # inner → voice 资产（ref 主角）；portrait 资产
    protagonist = _pick_protagonist(snap, presence)
    mp.protagonist_char = protagonist
    for node in nodes:
        vi = 0
        for ln in node["lines"]:
            if ln["kind"] == "inner":
                vi += 1
                assets.append({
                    "asset_id": f"voice_inner_{node['id']}_{vi:03d}", "kind": "voice",
                    "ref": protagonist or None,
                    "prompt": f"内心独白：{ln['text']}（中性、压抑）",
                    "used_by": [node["id"]], "status": "pending", "hash": None,
                    "provider_hint": "kokoro|cloud",
                })
            if ln["kind"] == "narration" and ln.get("detail"):
                # detail 行复用背景叙事，无独立资产
                pass
    for c in (snap.elements.get("characters") or []):
        cid = str(c.get("id") or "")
        if cid in presence or cid == protagonist:
            assets.append({
                "asset_id": f"spr_{cid}_default", "kind": "portrait", "ref": cid,
                "prompt": f"角色立绘：{c.get('name')}（中性立绘，中式恐怖基调）",
                "used_by": sorted(set(presence.get(cid, []))), "status": "pending",
                "hash": None, "provider_hint": "seedream",
            })

    # 确保 protagonist 出现在无角色场景（spec：present 不能为空导致对白无处归属）
    for node in nodes:
        if not node["present"] and protagonist:
            node["present"] = [protagonist]
            if protagonist not in presence:
                presence[protagonist] = []
            if node["id"] not in presence[protagonist]:
                presence[protagonist].append(node["id"])

    characters, protagonist_id = build_characters(snap, presence)
    mp.protagonist_char = protagonist_id or protagonist

    mp.story = {"start": nodes[0]["id"] if nodes else "", "nodes": nodes}
    mp.characters = characters
    mp.world = world
    mp.assets = {"assets": assets}
    mp.interaction = {"entities": entities}
    mp.llm_zones = {"zones": [], "global": {
        "rule": "先映射合法动词，映射失败才交给 LLM 演出",
        "forbidden": ["触发硬事件", "新增具名角色", "修改节点结构"],
    }}
    mp.raw_dialogues = raw_dialogues
    if not mp.scene_nodes:
        mp.warnings.append({"code": "NO_SCENES", "detail": "l4 快照为空，未生成任何场景节点"})
    return mp


def _pick_protagonist(snap: ArcSnapshot, presence: dict[str, list[str]]) -> str:
    selected = snap.selected.get("characters") or []
    if selected:
        return str(selected[0])
    if presence:
        return max(presence, key=lambda k: len(presence[k]))
    chars = snap.elements.get("characters") or []
    if chars:
        return str(chars[0].get("id") or "")
    return "char_protagonist"
