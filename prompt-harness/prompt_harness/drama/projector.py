"""漫剧投影：Adaptation Pack → shot list + 字幕（期 3 漫剧 MVP 地基）。

确定性投影规则（逐字段对齐改编层 spec §5.3 的 story 结构）：
- 只消费 story.json 的 scene 节点与 ending 节点（choice 不成画面，其结果在后续 scene 体现）；
- scene 内每条 line 一个 shot：
  - narration → still_push（静帧缓推），旁白轨
  - inner     → closeup（特写），旁白轨（speaker 取 present 首角色；对应主角内心）
  - dialogue  → shot_reverse_shot（正反打占位，pan 左右交替），台词轨，带 speaker/expression
  - stage     → stage_cue（演出提示，独立短镜头），演出提示字幕轨（硬事件由状态机驱动，仅提示）
- ending 节点 → end_card（收尾标题卡）；
- lines 按 kind 分轨汇总到 tracks；输出 shots.json + subtitles.srt 到 `<pack>/drama/`。

输出必须字节级确定（无时间戳、排序稳定），供 golden 测试断言。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List

SHOTS_SCHEMA_VERSION = "0.1"
OUTPUT_DIRNAME = "drama"

# 每句 ~3s 的 v0 口径；stage 演出提示短一些，end_card 略长
DEFAULT_DURATIONS: Dict[str, float] = {
    "still_push": 3.0,
    "closeup": 3.0,
    "shot_reverse_shot": 3.0,
    "stage_cue": 2.5,
    "end_card": 3.5,
}

# 镜头类型 → 旁白轨 / 台词轨 / 演出提示字幕轨
TRACK_BY_TYPE: Dict[str, str] = {
    "still_push": "narration",
    "closeup": "narration",
    "shot_reverse_shot": "dialogue",
    "stage_cue": "stage",
    "end_card": "narration",
}


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _load_pack(pack_dir: str | Path) -> Dict[str, Any]:
    pack = Path(pack_dir)
    story_path = pack / "story.json"
    chars_path = pack / "characters.json"
    if not story_path.exists():
        raise FileNotFoundError(f"pack 缺少 story.json：{story_path}")
    story = _read_json(story_path)
    characters: Dict[str, str] = {}
    if chars_path.exists():
        for ch in (_read_json(chars_path).get("characters") or []):
            if ch.get("id"):
                characters[ch["id"]] = ch.get("name", ch["id"])
    return {"pack": pack, "story": story, "characters": characters}


def _camera_shot_type(kind: str, dialogue_parity: int) -> Dict[str, Any]:
    """行类型 → 镜头提示。正反打按对白全局序数奇偶交替 pan 方向（确定性）。"""
    if kind == "dialogue":
        return {
            "type": "shot_reverse_shot",
            "camera": {
                "move": "pan",
                "from_scale": 1.05,
                "to_scale": 1.05,
                "pan": "left" if dialogue_parity % 2 == 0 else "right",
            },
        }
    if kind == "inner":
        return {"type": "closeup", "camera": {"move": "push_in", "from_scale": 1.0, "to_scale": 1.14}}
    if kind == "stage":
        return {"type": "stage_cue", "camera": {"move": "static", "from_scale": 1.0, "to_scale": 1.0}}
    return {"type": "still_push", "camera": {"move": "push_in", "from_scale": 1.0, "to_scale": 1.08}}


def project_pack(pack_dir: str | Path) -> Dict[str, Any]:
    """pack 目录 → shots 文档（确定性）。"""
    loaded = _load_pack(pack_dir)
    story: Dict[str, Any] = loaded["story"]
    characters: Dict[str, str] = loaded["characters"]
    pack_id = ""
    pack_json = loaded["pack"] / "pack.json"
    if pack_json.exists():
        pack_id = str(_read_json(pack_json).get("pack_id", ""))

    warnings: List[str] = []
    shots: List[Dict[str, Any]] = []
    tracks: Dict[str, List[str]] = {"narration": [], "dialogue": [], "stage": []}
    dialogue_counter = 0

    for node in story.get("nodes", []):
        ntype = node.get("type")
        nid = node.get("id", "")
        if ntype == "scene":
            present: List[str] = list(node.get("present") or [])
            for idx, line in enumerate(node.get("lines") or []):
                kind = str(line.get("kind", "narration"))
                text = str(line.get("text") or line.get("cue") or "").strip()
                if not text:
                    warnings.append(f"{nid}: 第 {idx} 行文本为空，跳过")
                    continue
                if kind not in ("narration", "inner", "dialogue", "stage"):
                    warnings.append(f"{nid}: 未知行类型 {kind!r}，按 narration 投影")
                    kind = "narration"
                cam = _camera_shot_type(kind, dialogue_counter)
                shot: Dict[str, Any] = {
                    "shot_id": f"{nid}_s{idx:02d}",
                    "node_id": nid,
                    "index": idx,
                    "line_kind": kind,
                    **cam,
                    "background": node.get("background"),
                    "track": TRACK_BY_TYPE[cam["type"]],
                    "duration": DEFAULT_DURATIONS[cam["type"]],
                    "text": text,
                    "speaker": None,
                    "speaker_name": None,
                    "expression": line.get("expression"),
                    "stage_cue": line.get("cue") if kind == "stage" else None,
                }
                if kind == "dialogue":
                    speaker = line.get("speaker")
                    if speaker in characters:
                        shot["speaker"] = speaker
                        shot["speaker_name"] = characters[speaker]
                        shot["camera"]["eyeline"] = (
                            "front" if speaker in present else "reverse"
                        )
                    else:
                        warnings.append(
                            f"{nid}: 对白 speaker={speaker!r} 不在 characters，降级为旁白字幕"
                        )
                        shot["track"] = "narration"
                    dialogue_counter += 1
                elif kind == "inner":
                    shot["speaker"] = present[0] if present else None
                    shot["speaker_name"] = characters.get(shot["speaker"], shot["speaker"])
                if shot["shot_id"] in tracks.get(shot["track"], []):  # 防御：id 冲突
                    warnings.append(f"{nid}: shot_id 重复 {shot['shot_id']}")
                shots.append(shot)
                tracks.setdefault(shot["track"], []).append(shot["shot_id"])
        elif ntype == "ending":
            title = str(node.get("title") or "").strip()
            text = str(node.get("text") or "").strip()
            body = f"{title}\n{text}" if title and text else (title or text)
            shot = {
                "shot_id": f"{nid}_card",
                "node_id": nid,
                "index": 0,
                "line_kind": "ending",
                "type": "end_card",
                "camera": {"move": "static", "from_scale": 1.0, "to_scale": 1.0},
                "background": None,
                "track": "narration",
                "duration": DEFAULT_DURATIONS["end_card"],
                "text": body,
                "speaker": None,
                "speaker_name": None,
                "expression": None,
                "stage_cue": None,
            }
            shots.append(shot)
            tracks["narration"].append(shot["shot_id"])
        # choice 节点：不投影（收敛式选择只影响叙事走向）

    return {
        "schema_version": SHOTS_SCHEMA_VERSION,
        "pack_id": pack_id,
        "size": {"width": 1080, "height": 1920},
        "fps": 24,
        "durations": dict(DEFAULT_DURATIONS),
        "tracks": tracks,
        "shots": shots,
        "warnings": warnings,
    }


# --------------------------------------------------------------------------- 字幕


def _fmt_srt_time(seconds: float) -> str:
    ms = int(round(seconds * 1000))
    h, rem = divmod(ms, 3600_000)
    m, rem = divmod(rem, 60_000)
    s, ms = divmod(rem, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def subtitle_text(shot: Dict[str, Any]) -> str:
    """shot → 单条字幕文本（按轨加可区分前缀）。"""
    kind = shot.get("line_kind")
    text = shot.get("text", "")
    if kind == "dialogue" and shot.get("speaker_name"):
        return f"{shot['speaker_name']}：{text}"
    if kind == "inner":
        return f"（内心）{text}"
    if kind == "stage":
        return f"〔演出〕{text}"
    return text


def build_srt(shots_doc: Dict[str, Any]) -> str:
    """shots 文档 → SRT 字幕全文。时间轴 = 各 shot duration 顺序累计。"""
    blocks: List[str] = []
    cursor = 0.0
    seq = 0
    for shot in shots_doc.get("shots", []):
        duration = float(shot.get("duration", 3.0))
        text = subtitle_text(shot).strip()
        if not text:
            cursor += duration
            continue
        seq += 1
        blocks.append(
            f"{seq}\n{_fmt_srt_time(cursor)} --> {_fmt_srt_time(cursor + duration)}\n{text}"
        )
        cursor += duration
    return "\n\n".join(blocks) + ("\n" if blocks else "")


def write_drama(pack_dir: str | Path, shots_doc: Dict[str, Any]) -> Dict[str, str]:
    """shots 文档落盘到 `<pack>/drama/`：shots.json + subtitles.srt。返回路径表。"""
    out_dir = Path(pack_dir) / OUTPUT_DIRNAME
    out_dir.mkdir(parents=True, exist_ok=True)
    shots_path = out_dir / "shots.json"
    srt_path = out_dir / "subtitles.srt"
    shots_path.write_text(
        json.dumps(shots_doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n"
    )
    srt_path.write_text(build_srt(shots_doc), encoding="utf-8", newline="\n")
    return {"shots_path": str(shots_path), "srt_path": str(srt_path)}


def project_to_drama(pack_dir: str | Path) -> Dict[str, Any]:
    """一步到位：投影 + 落盘。返回 {shots 文档, 路径表}。"""
    shots_doc = project_pack(pack_dir)
    paths = write_drama(pack_dir, shots_doc)
    return {"shots": shots_doc, **paths}


# --------------------------------------------------------------------------- 资产库回填（期 3）

# 回填字段：compose 直接消费的本地文件路径引用
BACKGROUND_PATH_KEY = "background_path"
PORTRAIT_PATH_KEY = "portrait_path"


def load_portrait_map(pack_dir: str | Path) -> Dict[str, str]:
    """characters.json → {char_id: portrait_asset_id}（assets.portrait 字段）。"""
    chars_path = Path(pack_dir) / "characters.json"
    mapping: Dict[str, str] = {}
    if not chars_path.exists():
        return mapping
    for ch in json.loads(chars_path.read_text(encoding="utf-8")).get("characters") or []:
        cid = ch.get("id")
        portrait = (ch.get("assets") or {}).get("portrait")
        if cid and portrait:
            mapping[str(cid)] = str(portrait)
    return mapping


def _entry_path(store: Any, asset_id: Optional[str]) -> Optional[str]:
    """entry 状态机 done 且 blob 可解析 → 本地文件路径；否则 None。"""
    if not asset_id or store is None:
        return None
    try:
        entry = store.get_entry(str(asset_id))
    except Exception:
        return None
    if entry is None or entry.status != "done":
        return None
    try:
        path = store.resolve_path(str(asset_id))
    except Exception:
        return None
    return str(path) if path is not None and Path(path).is_file() else None


def backfill_shot_assets(
    shots_doc: Dict[str, Any],
    store: Any,
    portrait_map: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    """资产库 → shots 引用回填（原地修改并返回 shots_doc）。

    - shot.background（asset_id）在库内 status=done → shot["background_path"] = 本地文件路径；
    - shot.speaker（char id）经 portrait_map → portrait 资产 done → shot["portrait_path"]；
    - 未完成/未登记的引用保持原样（compose 自行走占位兜底），不报错；
    - 返回 stats：{"background_filled", "portrait_filled", "pending"}；
      pending 只统计「引用了背景/角色但两者都未就绪」的镜头（end_card 等无资产引用的不计）。
    """
    portrait_map = portrait_map or {}
    stats = {"background_filled": 0, "portrait_filled": 0, "pending": 0}
    for shot in shots_doc.get("shots", []):
        bg_path = _entry_path(store, shot.get("background"))
        if bg_path:
            shot[BACKGROUND_PATH_KEY] = bg_path
            stats["background_filled"] += 1
        portrait_id = portrait_map.get(str(shot.get("speaker") or ""))
        pt_path = _entry_path(store, portrait_id)
        if pt_path:
            shot[PORTRAIT_PATH_KEY] = pt_path
            stats["portrait_filled"] += 1
        referenced = bool(shot.get("background")) or bool(shot.get("speaker"))
        if referenced and not bg_path and not pt_path:
            stats["pending"] += 1
    return stats


def backfill_pack_drama(
    pack_dir: str | Path,
    store: Any,
    shots_doc: Optional[Dict[str, Any]] = None,
    write: bool = True,
) -> Dict[str, Any]:
    """投影 ↔ 资产库回填一步到位。

    shots_doc 缺省时：优先读 `<pack>/drama/shots.json`（此前投影落盘的），缺失则现场 project_pack。
    回填后（默认）把 shots.json 写回 `<pack>/drama/`，compose 直接消费已生成资产。
    返回 {shots, stats, shots_path?}。
    """
    pack = Path(pack_dir)
    shots_path = pack / OUTPUT_DIRNAME / "shots.json"
    if shots_doc is None:
        if shots_path.exists():
            shots_doc = json.loads(shots_path.read_text(encoding="utf-8"))
        else:
            shots_doc = project_pack(pack)
    stats = backfill_shot_assets(shots_doc, store, portrait_map=load_portrait_map(pack))
    result: Dict[str, Any] = {"shots": shots_doc, "stats": stats}
    if write:
        shots_path.parent.mkdir(parents=True, exist_ok=True)
        shots_path.write_text(
            json.dumps(shots_doc, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
            newline="\n",
        )
        result["shots_path"] = str(shots_path)
    return result
