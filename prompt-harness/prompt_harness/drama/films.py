# -*- coding: utf-8 -*-
"""漫剧成品层：成片 / 合辑 / 关键镜头 / 发布 / 批量出片（T36 收口）。

状态落盘（per-book，均在 `<book>/.ainovel/adaptation/` 下）：
- `films.json`      单集导出/发布状态、合辑编排与导出状态
- `keyshots.json`   关键镜头白名单的 draft/正式状态
- `batch.json`      批量出片队列状态

产物统一放 `<book>/.ainovel/films/`（导出的单集 mp4 / 合辑 mp4 / 发布包 zip）。

本模块只做数据与引擎层，不依赖 FastAPI；HTTP 路由在 server.py。
诚实降级：PyAV 缺失 / 无配音素材时如实返回 reason，不伪造产物。
"""
from __future__ import annotations

import json
import os
import shutil
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

# ---------------------------------------------------------------- 基础工具


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _read_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def _write_json(path: Path, data: Any) -> None:
    """原子写 JSON（临时文件 + replace），写失败抛 OSError 由调用方处理。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8", newline="\n")
    os.replace(tmp, path)


def _valid_seg(seg: str) -> bool:
    return bool(seg) and seg not in (".", "..") and "/" not in seg and "\\" not in seg and not seg.startswith(".")


def adaptation_root(book_root: str | Path) -> Path:
    return Path(book_root) / ".ainovel" / "adaptation"


def safe_pack_dir(book_root: str | Path, pack_id: str) -> Path:
    """pack_id → 目录；白名单校验 + 存在性检查，防穿越。"""
    if not _valid_seg(pack_id):
        raise ValueError(f"非法 pack_id：{pack_id!r}")
    d = adaptation_root(book_root) / pack_id
    if not d.is_dir():
        raise FileNotFoundError(f"pack 不存在：{pack_id}")
    return d


def pack_dir_of(book_root: str | Path, pack_id: str) -> Path:
    return safe_pack_dir(book_root, pack_id)


def list_pack_ids(book_root: str | Path) -> List[str]:
    root = adaptation_root(book_root)
    if not root.is_dir():
        return []
    return [d.name for d in root.iterdir() if d.is_dir() and not d.name.endswith(".failed")]


def pack_meta(book_root: str | Path, pack_id: str) -> Dict[str, Any]:
    d = safe_pack_dir(book_root, pack_id)
    pack = _read_json(d / "pack.json", {}) or {}
    validation = _read_json(d / "validation.json", {}) or {}
    return {"pack": pack, "validation": validation, "dir": d, "pack_dir": str(d)}


def pack_shots(book_root: str | Path, pack_id: str, refresh: bool = False) -> Dict[str, Any]:
    """读（或现场投影）分镜文档。返回 {"doc","shots_path","srt_path","generated"}。"""
    from .projector import project_to_drama

    d = safe_pack_dir(book_root, pack_id)
    shots_path = d / "drama" / "shots.json"
    if refresh or not shots_path.is_file():
        out = project_to_drama(d)
        return {"doc": out["shots"], "shots_path": out["shots_path"], "srt_path": out["srt_path"], "generated": True}
    doc = _read_json(shots_path, None)
    if not isinstance(doc, dict):
        out = project_to_drama(d)
        return {"doc": out["shots"], "shots_path": out["shots_path"], "srt_path": out["srt_path"], "generated": True}
    return {"doc": doc, "shots_path": str(shots_path), "srt_path": str(d / "drama" / "subtitles.srt"), "generated": False}


def shots_summary(doc: Dict[str, Any]) -> Dict[str, Any]:
    """shots 文档 → 工作台/详情所需的汇总（时长/轨计数/逐节点时间线）。"""
    shots = doc.get("shots") or []
    total = 0.0
    track_counts = {"narration": 0, "dialogue": 0, "stage": 0}
    type_counts: Dict[str, int] = {}
    nodes: Dict[str, Dict[str, Any]] = {}
    for s in shots:
        dur = float(s.get("duration") or 0)
        total += dur
        track = str(s.get("track") or s.get("line_kind") or "")
        if track in track_counts:
            track_counts[track] += 1
        t = str(s.get("type") or "unknown")
        type_counts[t] = type_counts.get(t, 0) + 1
        nid = str(s.get("node_id") or "")
        row = nodes.setdefault(nid, {
            "node_id": nid, "shots": 0, "duration": 0.0,
            "narration": 0, "dialogue": 0, "type_counts": {},
            "sample": "",
        })
        row["shots"] += 1
        row["duration"] = round(row["duration"] + dur, 3)
        if track in ("narration", "dialogue"):
            row[track] += 1
        row["type_counts"][t] = row["type_counts"].get(t, 0) + 1
        if not row["sample"]:
            txt = str(s.get("text") or "").strip()
            if txt:
                row["sample"] = txt[:60]
    timeline = []
    for nid, row in nodes.items():
        if nid.startswith("end_"):
            row.setdefault("is_ending", True)
        timeline.append(row)
    return {
        "shots": len(shots),
        "duration": round(total, 3),
        "duration_text": fmt_duration(total),
        "tracks": track_counts,
        "type_counts": type_counts,
        "timeline": timeline,
        "size": doc.get("size") or {"width": 1080, "height": 1920},
        "fps": doc.get("fps") or 24,
        "warnings": doc.get("warnings") or [],
    }


def fmt_duration(sec: float) -> str:
    m = int(sec // 60)
    s = int(round(sec % 60))
    return f"{m}:{s:02d}"


def srt_rows(book_root: str | Path, pack_id: str, limit: int = 40) -> List[Dict[str, Any]]:
    d = safe_pack_dir(book_root, pack_id)
    srt = d / "drama" / "subtitles.srt"
    if not srt.is_file():
        return []
    rows: List[Dict[str, Any]] = []
    try:
        from .align import parse_srt

        for cue in parse_srt(srt.read_text(encoding="utf-8"))[:limit]:
            rows.append({"t": fmt_duration(cue.start), "x": cue.text})
    except Exception:
        text = srt.read_text(encoding="utf-8", errors="replace").splitlines()
        for i, line in enumerate(text):
            if line.strip().isdigit():
                t = text[i + 1].split("-->")[0].strip() if i + 1 < len(text) else ""
                x = text[i + 2].strip() if i + 2 < len(text) else ""
                rows.append({"t": t, "x": x})
            if len(rows) >= limit:
                break
    return rows


# ---------------------------------------------------------------- 资产


def pack_assets(book_root: str | Path, pack_id: str, store: Any = None) -> List[Dict[str, Any]]:
    """pack assets.json + 全局资产库状态合并（asset_id 为键）。"""
    d = safe_pack_dir(book_root, pack_id)
    data = _read_json(d / "assets.json", {}) or {}
    items = data.get("assets") if isinstance(data, dict) else []
    store_entries: Dict[str, Any] = {}
    if store is not None:
        try:
            store_entries = store.entries()
        except Exception:
            store_entries = {}
    out: List[Dict[str, Any]] = []
    for a in items or []:
        if not isinstance(a, dict):
            continue
        aid = str(a.get("asset_id") or a.get("id") or "")
        entry = store_entries.get(aid)
        status = str(a.get("state") or (getattr(entry, "status", "") if entry else "pending") or "pending")
        out.append({
            "asset_id": aid,
            "kind": str(a.get("kind") or ""),
            "prompt": str(a.get("prompt") or ""),
            "used_by": a.get("used_by") or [],
            "state": status,
            "content_id": (getattr(entry, "content_id", None) if entry else None) or a.get("content_id"),
        })
    return out


def mark_pack_asset(book_root: str | Path, pack_id: str, asset_id: str, state: str, content_id: str = "") -> bool:
    """回填 pack assets.json 的资产状态（内容寻址产物已由驱动写入全局库）。"""
    if not asset_id:
        return False
    d = safe_pack_dir(book_root, pack_id)
    f = d / "assets.json"
    data = _read_json(f, {}) or {}
    items = data.get("assets") if isinstance(data, dict) else None
    if not isinstance(items, list):
        return False
    changed = False
    for a in items:
        if isinstance(a, dict) and (a.get("asset_id") or a.get("id")) == asset_id:
            a["state"] = state
            if content_id:
                a["content_id"] = content_id
            changed = True
    if changed:
        _write_json(f, data)
    return changed


# ---------------------------------------------------------------- 成片 / 合辑


FILMS_FILE = "films.json"


def films_state(book_root: str | Path) -> Dict[str, Any]:
    f = adaptation_root(book_root) / FILMS_FILE
    data = _read_json(f, None)
    if not isinstance(data, dict):
        data = {"version": 1, "episodes": {}, "compilations": [], "updated_at": now_iso()}
    data.setdefault("episodes", {})
    data.setdefault("compilations", [])
    return data


def save_films_state(book_root: str | Path, data: Dict[str, Any]) -> None:
    data["updated_at"] = now_iso()
    _write_json(adaptation_root(book_root) / FILMS_FILE, data)


def episode_state(book_root: str | Path, pack_id: str) -> Dict[str, Any]:
    return films_state(book_root)["episodes"].get(pack_id, {})


def update_episode(book_root: str | Path, pack_id: str, **fields: Any) -> Dict[str, Any]:
    data = films_state(book_root)
    ep = data["episodes"].setdefault(pack_id, {})
    ep.update(fields)
    ep["updated_at"] = now_iso()
    save_films_state(book_root, data)
    return ep


def episode_mp4(book_root: str | Path, pack_id: str) -> Optional[Path]:
    d = safe_pack_dir(book_root, pack_id)
    mp4 = d / "drama" / "drama_preview.mp4"
    return mp4 if mp4.is_file() else None


def episodes(book_root: str | Path) -> List[Dict[str, Any]]:
    """该书的单集列表（有 drama/*.mp4 的 pack）。"""
    out: List[Dict[str, Any]] = []
    state = films_state(book_root)
    for pid in list_pack_ids(book_root):
        d = adaptation_root(book_root) / pid
        mp4s = sorted((d / "drama").glob("*.mp4")) if (d / "drama").is_dir() else []
        if not mp4s:
            continue
        mp4 = d / "drama" / "drama_preview.mp4"
        if not mp4.is_file():
            mp4 = max(mp4s, key=lambda p: p.stat().st_mtime)
        meta = _read_json(d / "pack.json", {}) or {}
        game = meta.get("game") or {}
        shots_summary_doc = _read_json(d / "drama" / "shots.json", {}) or {}
        duration = sum(float(s.get("duration") or 0) for s in (shots_summary_doc.get("shots") or []))
        ep = state["episodes"].get(pid, {})
        out.append({
            "pack_id": pid,
            "arc_id": (meta.get("source") or {}).get("arc_ids", [""])[0] if meta.get("source") else "",
            "title": game.get("title") or pid,
            "logline": game.get("logline") or "",
            "genre_tags": game.get("genre_tags") or [],
            "duration": round(duration, 3),
            "duration_text": fmt_duration(duration),
            "mp4": str(mp4),
            "mp4_bytes": mp4.stat().st_size,
            "mtime": datetime.fromtimestamp(mp4.stat().st_mtime, timezone.utc).isoformat(),
            "exported": bool(ep.get("exported")),
            "export_path": ep.get("export_path") or "",
            "published": bool(ep.get("published")),
            "pub": ep.get("pub") or {},
            "cover_path": ep.get("cover_path") or "",
            "trailer_path": ep.get("trailer_path") or "",
            "publish_zip": ep.get("publish_zip") or "",
        })
    out.sort(key=lambda x: x["mtime"], reverse=True)
    return out


def films_dir(book_root: str | Path) -> Path:
    d = Path(book_root) / ".ainovel" / "films"
    d.mkdir(parents=True, exist_ok=True)
    return d


def export_episode(book_root: str | Path, pack_id: str) -> Dict[str, Any]:
    """把 pack 内成片复制到 <book>/.ainovel/films/ 并更新状态。"""
    src = episode_mp4(book_root, pack_id)
    if src is None:
        return {"ok": False, "error": "该 pack 还没有合成成片（先在漫剧工作台执行合成）"}
    dst = films_dir(book_root) / f"{pack_id}.mp4"
    shutil.copy2(src, dst)
    ep = update_episode(book_root, pack_id, exported=True, export_path=str(dst), exported_at=now_iso())
    return {"ok": True, "export_path": str(dst), "bytes": dst.stat().st_size, "episode": ep}


def concat_videos(paths: List[Path], out_path: Path) -> Dict[str, Any]:
    """PyAV 重编码拼接（同构图/音参数；跨段重新计 pts，失败返回明确 reason）。"""
    try:
        import av
    except ImportError as exc:  # pragma: no cover - 环境相关
        return {"ok": False, "error": f"PyAV 未安装：{exc}"}
    if not paths:
        return {"ok": False, "error": "没有可拼接的片段"}
    try:
        out = av.open(str(out_path), mode="w")
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"无法创建输出：{exc}"}
    out_v = None
    out_a = None
    frames = 0
    try:
        for p in paths:
            inp = av.open(str(p))
            try:
                if out_v is None:
                    in_v = inp.streams.video[0]
                    rate = int(round(float(in_v.average_rate or 24))) or 24
                    out_v = out.add_stream("libx264", rate=rate)
                    out_v.width = in_v.width
                    out_v.height = in_v.height
                    out_v.pix_fmt = "yuv420p"
                    if inp.streams.audio:
                        in_a = inp.streams.audio[0]
                        out_a = out.add_stream("aac", rate=in_a.sample_rate or 44100)
                        out_a.layout = "stereo"
                for frame in inp.decode(inp.streams.video[0]):
                    frame = frame.reformat(width=out_v.width, height=out_v.height, format="yuv420p")
                    frame.pts = None  # 跨段重新计 pts，避免时间戳回退
                    for packet in out_v.encode(frame):
                        out.mux(packet)
                    frames += 1
                if out_a is not None:
                    try:
                        for aframe in inp.decode(inp.streams.audio[0]):
                            aframe.pts = None
                            for packet in out_a.encode(aframe):
                                out.mux(packet)
                    except Exception:
                        pass  # 音频参数不一致时保视频拼接
            finally:
                inp.close()
        for packet in out_v.encode(None):
            out.mux(packet)
        if out_a is not None:
            for packet in out_a.encode(None):
                out.mux(packet)
    except Exception as exc:  # noqa: BLE001
        try:
            out.close()
        except Exception:
            pass
        return {"ok": False, "error": f"拼接失败：{exc}"}
    try:
        out.close()
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"关闭输出失败：{exc}"}
    if not out_path.exists() or out_path.stat().st_size == 0:
        return {"ok": False, "error": f"无有效数据（frames={frames}）"}
    return {"ok": True, "out": str(out_path), "bytes": out_path.stat().st_size, "frames": frames}


def compilation_action(book_root: str | Path, action: str, pack_id: str = "", title: str = "", index: int = -1) -> Dict[str, Any]:
    """合辑编排：add / remove / export / rename。"""
    data = films_state(book_root)
    comps = data["compilations"]
    comp = comps[0] if comps else None
    if comp is None and action != "list":
        comp = {"id": "comp_main", "title": title or "合集", "items": [], "exported": False}
        comps.append(comp)
    if action == "add":
        if not comp:
            return {"ok": False, "error": "没有合辑"}
        if pack_id and pack_id not in comp["items"]:
            comp["items"].append(pack_id)
        save_films_state(book_root, data)
        return {"ok": True, "compilation": comp}
    if action == "remove":
        if comp:
            if pack_id and pack_id in comp["items"]:
                comp["items"].remove(pack_id)
            elif not pack_id and 0 <= index < len(comp["items"]):
                comp["items"].pop(index)
        save_films_state(book_root, data)
        return {"ok": True, "compilation": comp}
    if action == "export":
        if not comp or not comp["items"]:
            return {"ok": False, "error": "合辑还是空的——先从单集「加入合辑」"}
        paths: List[Path] = []
        for pid in comp["items"]:
            ep = films_state(book_root)["episodes"].get(pid, {})
            p = Path(ep["export_path"]) if ep.get("export_path") and Path(ep["export_path"]).is_file() else episode_mp4(book_root, pid)
            if p is not None and Path(p).is_file():
                paths.append(Path(p))
        if not paths:
            return {"ok": False, "error": "合辑内单集都没有可用成片（先导出单集或合成）"}
        out = films_dir(book_root) / f"{comp['id']}.mp4"
        r = concat_videos(paths, out)
        if not r.get("ok"):
            return r
        comp.update({"exported": True, "export_path": str(out), "exported_at": now_iso(),
                     "duration_bytes": out.stat().st_size})
        save_films_state(book_root, data)
        return {"ok": True, "compilation": comp, "out": str(out)}
    return {"ok": True, "compilation": comp}


# ---------------------------------------------------------------- 关键镜头


KEYSHOTS_FILE = "drama/keyshots.json"
DRAFT_COST = 2.4
FINAL_COST = 15.6


def _default_keyshots(doc: Dict[str, Any], limit: int = 4) -> List[Dict[str, Any]]:
    """默认白名单：每个节点挑第一个非 end_card 镜头，最多 limit 个。"""
    picks: List[Dict[str, Any]] = []
    seen: set = set()
    for s in doc.get("shots") or []:
        nid = str(s.get("node_id") or "")
        if nid.startswith("end_") or nid in seen:
            continue
        if str(s.get("type")) == "end_card":
            continue
        seen.add(nid)
        picks.append({
            "shot_id": str(s.get("shot_id") or ""),
            "node_id": nid,
            "desc": str(s.get("text") or "")[:42],
            "stage": 0,
            "cost_draft": DRAFT_COST,
            "cost_final": FINAL_COST,
            "result_path": "",
        })
        if len(picks) >= limit:
            break
    return picks


def keyshots_state(book_root: str | Path, pack_id: str) -> Dict[str, Any]:
    d = safe_pack_dir(book_root, pack_id)
    f = d / KEYSHOTS_FILE
    data = _read_json(f, None)
    if not isinstance(data, dict) or not data.get("items"):
        shots = pack_shots(book_root, pack_id)["doc"]
        data = {"version": 1, "items": _default_keyshots(shots), "updated_at": now_iso()}
        _write_json(f, data)
    return data


def save_keyshots(book_root: str | Path, pack_id: str, data: Dict[str, Any]) -> None:
    d = safe_pack_dir(book_root, pack_id)
    data["updated_at"] = now_iso()
    _write_json(d / KEYSHOTS_FILE, data)


# ---------------------------------------------------------------- 发布


PUB_KEYS = ("cover", "trailer", "i18nEn", "audit", "specs", "package")


def publish_checklist(book_root: str | Path, pack_id: str) -> Dict[str, Any]:
    ep = episode_state(book_root, pack_id)
    pub = ep.get("pub") or {}
    shots_ok = (adaptation_root(book_root) / pack_id / "drama" / "drama_preview.mp4").is_file()
    items = [
        {"key": "cover", "name": "封面图", "d": "1080×1440 · 从成片抽帧生成", "done": bool(ep.get("cover_path"))},
        {"key": "trailer", "name": "预告片（横屏）", "d": "1920×1080 · 关键镜头占位合成", "done": bool(ep.get("trailer_path"))},
        {"key": "i18nEn", "name": "多语言字幕 zh→en", "d": "srt 翻译（真实 LLM，需配置）", "done": bool(pub.get("i18nEn"))},
        {"key": "audit", "name": "审核预检", "d": "本地敏感词 + 规则（离线）", "done": bool(pub.get("audit"))},
        {"key": "specs", "name": "平台规格导出", "d": "抖音 / 快手 / B站 / 合辑 / 预告片", "done": bool(pub.get("specs"))},
        {"key": "package", "name": "发布包", "d": "成片 + srt + 封面 + 预告片 + 发布文案", "done": bool(ep.get("publish_zip"))},
    ]
    done = sum(1 for x in items if x["done"])
    return {
        "ok": True, "pack_id": pack_id, "items": items, "done": done, "total": len(items),
        "has_film": shots_ok,
    }


SPEC_ROWS = [
    ["抖音 / 快手", "1080×1920 · H.264 · ≤ 5 分钟"],
    ["B站（竖屏）", "1080×1920 · H.264 · 封面 1146×717"],
    ["合辑（长片）", "1080×1920 · 10 分钟级 · 章节标记"],
    ["预告片（横屏）", "1920×1080 · 30–60s"],
]

# 本地审核词表（R3 §6 层 1 的最小实现：离线、可审计；非全面审查）
AUDIT_WORDS = [
    "赌博", "毒品", "自杀", "血腥", "恐怖袭击", "邪教", "色情", "暴力",
    "杀人", "分尸", "虐待", "诈骗", "枪支", "爆炸",
]


def audit_pack(book_root: str | Path, pack_id: str) -> Dict[str, Any]:
    d = safe_pack_dir(book_root, pack_id)
    hits: List[Dict[str, Any]] = []
    sources: List[tuple[str, str]] = []
    srt = d / "drama" / "subtitles.srt"
    if srt.is_file():
        sources.append(("subtitles.srt", srt.read_text(encoding="utf-8", errors="replace")))
    for name in ("story.json", "pack.json"):
        f = d / name
        if f.is_file():
            sources.append((name, f.read_text(encoding="utf-8", errors="replace")))
    for src, text in sources:
        for i, line in enumerate(text.splitlines(), 1):
            for w in AUDIT_WORDS:
                if w in line:
                    hits.append({"source": src, "line": i, "word": w, "excerpt": line.strip()[:80]})
                    if len(hits) >= 50:
                        break
    return {
        "ok": True, "hits": hits, "hit_count": len(hits),
        "scope": "正片字幕 / 故事文本 / pack 元信息",
        "note": "本地词表最小实现，不替代平台审核",
    }


def make_cover(book_root: str | Path, pack_id: str) -> Dict[str, Any]:
    """从成片抽第一帧生成封面 jpg（PyAV + PIL）。"""
    src = episode_mp4(book_root, pack_id)
    if src is None:
        return {"ok": False, "error": "没有成片可抽帧"}
    try:
        import av
        from PIL import Image
    except ImportError as exc:  # pragma: no cover
        return {"ok": False, "error": f"缺依赖：{exc}"}
    out = safe_pack_dir(book_root, pack_id) / "drama" / "cover.jpg"
    try:
        with av.open(str(src)) as container:
            stream = container.streams.video[0]
            frame = next(container.decode(stream))
            img = frame.to_image()
            img.save(str(out), quality=88)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"抽帧失败：{exc}"}
    update_episode(book_root, pack_id, cover_path=str(out))
    return {"ok": True, "cover_path": str(out)}


def make_trailer(book_root: str | Path, pack_id: str, max_seconds: float = 45.0) -> Dict[str, Any]:
    """用分镜 doc 前段镜头做横屏预告片（占位素材；真实关键镜头生成后替换）。"""
    from .compose import compose_video

    got = pack_shots(book_root, pack_id)
    doc = got["doc"]
    picked: List[Dict[str, Any]] = []
    total = 0.0
    for s in doc.get("shots") or []:
        picked.append(s)
        total += float(s.get("duration") or 0)
        if total >= max_seconds:
            break
    if not picked:
        return {"ok": False, "error": "分镜为空，无法生成预告片"}
    sub = dict(doc)
    sub["shots"] = picked
    out = safe_pack_dir(book_root, pack_id) / "drama" / "trailer_preview.mp4"
    try:
        res = compose_video(sub, out_path=str(out), width=1920, height=1080, burn_subtitles=False, store=None)
    except ImportError as exc:
        return {"ok": False, "error": f"PyAV 未安装：{exc}"}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"预告片合成失败：{exc}"}
    update_episode(book_root, pack_id, trailer_path=str(out))
    return {"ok": True, "trailer_path": str(out), "duration": res.get("duration"), "shots": len(picked)}


async def translate_srt_async(book_root: str | Path, pack_id: str, target: str = "en") -> Dict[str, Any]:
    """把 subtitles.srt 翻译为目标语言（真实 LLM 调用；失败如实返回 error）。"""
    d = safe_pack_dir(book_root, pack_id)
    srt = d / "drama" / "subtitles.srt"
    if not srt.is_file():
        return {"ok": False, "error": "没有 subtitles.srt（先在工作台投影分镜）"}
    text = srt.read_text(encoding="utf-8")
    from .align import parse_srt, render_srt

    cues = parse_srt(text)
    if not cues:
        return {"ok": False, "error": "字幕为空"}
    from ..llm_client import chat_json

    payload = [{"i": i + 1, "zh": c.text} for i, c in enumerate(cues)]
    try:
        res = await chat_json(
            system="你是字幕翻译。逐条翻译为英文，保持口语自然、不合并；输出 JSON。",
            user=json.dumps({"target": "en", "lines": payload}, ensure_ascii=False),
            call_type="translate",
        )
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"翻译调用失败：{exc}"}
    data = (res or {}).get("data")
    arr = data.get("lines") if isinstance(data, dict) else None
    if not isinstance(arr, list) or len(arr) != len(cues):
        return {"ok": False, "error": f"翻译结果不符：{(res or {}).get('error') or '条数不匹配'}"}
    for cue, en in zip(cues, arr):
        cue.text = str(en)
    out = d / "drama" / "subtitles.en.srt"
    out.write_text(render_srt(cues), encoding="utf-8", newline="\n")
    ep = films_state(book_root)["episodes"].get(pack_id, {})
    pub = dict(ep.get("pub") or {})
    pub["i18nEn"] = True
    update_episode(book_root, pack_id, pub=pub)
    return {"ok": True, "out": str(out), "lines": len(cues)}


def make_publish_package(book_root: str | Path, pack_id: str) -> Dict[str, Any]:
    """打包：成片 + srt(+en) + 封面 + 预告片 + 发布文案。"""
    d = safe_pack_dir(book_root, pack_id)
    items: List[Path] = []
    mp4 = episode_mp4(book_root, pack_id)
    if mp4 is None:
        return {"ok": False, "error": "没有成片（先合成）"}
    items.append(mp4)
    for rel in ("drama/subtitles.srt", "drama/subtitles.en.srt", "drama/cover.jpg", "drama/trailer_preview.mp4"):
        f = d / rel
        if f.is_file():
            items.append(f)
    meta = _read_json(d / "pack.json", {}) or {}
    game = meta.get("game") or {}
    readme = (
        f"{game.get('title') or pack_id}\n"
        f"{game.get('logline') or ''}\n\n"
        "本包由 AInovel Harness 漫剧线导出（T36）。\n"
        "内容：正片 / 字幕 / 封面 / 预告片（如已生成）。\n"
    )
    out = films_dir(book_root) / f"{pack_id}_publish.zip"
    try:
        with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
            for f in items:
                z.write(f, arcname=f.name)
            z.writestr("发布文案.txt", readme)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"打包失败：{exc}"}
    ep = films_state(book_root)["episodes"].get(pack_id, {})
    pub = dict(ep.get("pub") or {})
    pub.update({"audit": True, "specs": True, "package": True})
    update_episode(book_root, pack_id, publish_zip=str(out), pub=pub, published=True)
    return {"ok": True, "zip": str(out), "bytes": out.stat().st_size, "files": [f.name for f in items]}


def mark_pub(book_root: str | Path, pack_id: str, key: str, done: bool = True) -> Dict[str, Any]:
    if key not in PUB_KEYS:
        return {"ok": False, "error": f"未知发布项：{key}"}
    ep = films_state(book_root)["episodes"].get(pack_id, {})
    pub = dict(ep.get("pub") or {})
    pub[key] = done
    update_episode(book_root, pack_id, pub=pub)
    return {"ok": True, "pub": pub}


# ---------------------------------------------------------------- 批量出片


BATCH_FILE = "batch.json"


def _arcs_of(book_root: str | Path) -> List[Dict[str, Any]]:
    data = _read_json(Path(book_root) / ".ainovel" / "arcs.json", {}) or {}
    arcs = data.get("arcs") if isinstance(data, dict) else None
    out: List[Dict[str, Any]] = []
    for a in arcs or []:
        if not isinstance(a, dict):
            continue
        levels = ((a.get("state") or {}).get("levels")) or {}
        l4 = levels.get("l4")
        scenes = l4.get("scenes") if isinstance(l4, dict) else None
        count = len(scenes) if isinstance(scenes, list) else 0
        out.append({"arc_id": str(a.get("id") or ""), "name": str(a.get("name") or ""), "l4": count})
    return out


def batch_state(book_root: str | Path) -> Dict[str, Any]:
    f = adaptation_root(book_root) / BATCH_FILE
    data = _read_json(f, None)
    if not isinstance(data, dict) or not data.get("items"):
        data = {"version": 1, "items": [], "running": False, "paused": False, "updated_at": now_iso()}
    return data


def save_batch_state(book_root: str | Path, data: Dict[str, Any]) -> None:
    data["updated_at"] = now_iso()
    _write_json(adaptation_root(book_root) / BATCH_FILE, data)


def batch_queue(book_root: str | Path) -> Dict[str, Any]:
    """按弧重建队列（保留已有状态；新弧/已删弧同步）。"""
    data = batch_state(book_root)
    existing = {it["arc_id"]: it for it in data["items"]}
    items: List[Dict[str, Any]] = []
    for arc in _arcs_of(book_root):
        it = existing.get(arc["arc_id"]) or {
            "arc_id": arc["arc_id"], "name": arc["name"], "l4": arc["l4"],
            "status": "ready" if arc["l4"] > 0 else "locked", "progress": 0, "cost": 0.0, "error": "",
        }
        it["name"] = arc["name"]
        it["l4"] = arc["l4"]
        if arc["l4"] > 0 and it["status"] == "locked":
            it["status"] = "ready"
        elif arc["l4"] <= 0 and it["status"] in ("ready", "paused"):
            it["status"] = "locked"
            it["progress"] = 0
        items.append(it)
    data["items"] = items
    save_batch_state(book_root, data)
    return data
