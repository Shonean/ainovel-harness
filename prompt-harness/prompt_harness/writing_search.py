# -*- coding: utf-8 -*-
"""writing_search.py — 写书讨论搜索：四源联邦检索（AnySearch 启发）。

数据源（sources）：
  ① book    本书：.ainovel/ 下 basic_settings / elements.json / 设定集/*.md /
             arcs.json（l1-l5 阶梯 + 正文 + 已落盘章节）/ 前文锚点
  ② corpus  参考语料库：SETTINGS.corpus_dir 下的 txt 章节窗口（BM25）
  ③ csv     参考资料：app/references/csv/（BM25，按题材过滤）
  ④ web     外部网页：可插拔 adapter（未配置 key → 空结果，不参与）

设计（对齐 AnySearch）：
  - 意图路由：查询先按关键词分意图（setting/character/plot/craft/general），
    意图决定各源内分块类型权重（如查角色 → 元素块加权，查剧情 → 弧/阶梯/正文加权）。
  - 联邦多源：每源独立检索 → 结果归一化 {source, kind, title, snippet, anchor, score}。
  - 结构化交付：format_results() 拼成带来源标注的 Markdown 块，直入 LLM 推理链。

索引：book 源持久化到 .ainovel/search.db（SQLite chunks 表 + 向量 JSON，embedding 用
      SETTINGS 的 BGE-M3）；构建一次，检索时混合 BM25 + 向量余弦。其余源规模小、现算。

对外接口：
  build_book_index(book_root)           → {status, chunks, embedded}
  search_around(query, book_root, ...)  → {results, sources_used, intent}
  format_results(results)               → Markdown 上下文块
"""
from __future__ import annotations

import asyncio
import json
import math
import re
import sqlite3
from functools import lru_cache
from pathlib import Path
from typing import Any

from .config import SETTINGS

_SEARCH_DB = "search.db"
_KIND_LABEL = {"characters": "角色", "items": "物品", "settings": "设定"}
_INTENT_KEYWORDS = {
    "setting": ("设定", "世界观", "规则", "力量", "体系", "境界", "地图", "势力", "背景", "时代"),
    "character": ("角色", "人物", "主角", "配角", "性格", "人设", "关系", "欲望", "缺陷",
                  "什么样", "是谁", "身份", "来历", "长相", "外貌"),
    "plot": ("剧情", "情节", "冲突", "转折", "反转", "诬陷", "悬念", "套路", "爽点", "桥段", "高潮", "结尾", "埋伏笔"),
    "craft": ("怎么写", "怎么", "如何", "写作", "描写", "技巧", "文笔", "流水账", "对白", "节奏", "组织"),
}


def _dot_ainovel(book_root: str | Path) -> Path:
    return Path(book_root) / ".ainovel"


def _read_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def _db_path(book_root: str | Path) -> Path:
    return _dot_ainovel(book_root) / _SEARCH_DB


def _index_sources(book_root: str | Path) -> list[Path]:
    """本书检索索引的数据源文件（任一变更 → 索引视为过期，检索时自动重建）。"""
    br = Path(book_root)
    files: list[Path] = []
    for name in ("basic_settings.json", "elements.json", "arcs.json"):
        p = _dot_ainovel(br) / name
        if p.exists():
            files.append(p)
    st_dir = br / "设定集"
    if st_dir.is_dir():
        files.extend(sorted(st_dir.glob("*.md")))
    return files


def _index_stale(book_root: str | Path) -> bool:
    """索引缺失或任一源文件比索引新 → 过期。"""
    db = _db_path(book_root)
    if not db.exists():
        return True
    try:
        db_mtime = db.stat().st_mtime
    except Exception:
        return True
    for f in _index_sources(book_root):
        try:
            if f.stat().st_mtime > db_mtime:
                return True
        except Exception:
            pass
    return False


# ════════════════════════════════════════════════════════════════════
# 分词与 BM25（轻量，中文按字符二元组 + 英文/数字单词）
# ════════════════════════════════════════════════════════════════════
_CJK_RE = re.compile(r"[一-鿿]")
_ASCII_RE = re.compile(r"[a-z0-9]+")


def _tokenize(text: str) -> list[str]:
    text = str(text or "").lower()
    toks: list[str] = list(_ASCII_RE.findall(text))
    han = "".join(_CJK_RE.findall(text))
    toks.extend(han[i : i + 2] for i in range(len(han) - 1))
    return toks


def _bm25_scores(query: str, docs: list[dict[str, Any]], k1: float = 1.5, b: float = 0.75) -> list[float]:
    """docs: [{id, content}] → 每条 BM25 分。query 无有效词 → 全 0。"""
    qt = _tokenize(query)
    if not qt:
        return [0.0] * len(docs)
    doc_toks: list[list[str]] = []
    df: dict[str, int] = {}
    for d in docs:
        t = _tokenize(d.get("content", ""))
        doc_toks.append(t)
        for term in set(t):
            df[term] = df.get(term, 0) + 1
    n = len(docs)
    avgl = sum(len(t) for t in doc_toks) / max(n, 1)
    scores: list[float] = []
    for t in doc_toks:
        dl = len(t)
        tf: dict[str, int] = {}
        for term in t:
            tf[term] = tf.get(term, 0) + 1
        s = 0.0
        for term in qt:
            f = tf.get(term, 0)
            if not f:
                continue
            idf = math.log(1.0 + (n - df.get(term, 0) + 0.5) / (df.get(term, 0) + 0.5))
            s += idf * (f * (k1 + 1.0)) / (f + k1 * (1.0 - b + b * dl / avgl))
        scores.append(s)
    return scores


# ════════════════════════════════════════════════════════════════════
# 本书索引：采集分块 → embed → 落盘 .ainovel/search.db
# ════════════════════════════════════════════════════════════════════
def _iter_book_chunks(book_root: str | Path) -> list[dict[str, Any]]:
    """采集本书全部可检索分块：[{kind, title, content, anchor}]。"""
    br = Path(book_root)
    chunks: list[dict[str, Any]] = []

    # ① basic_settings.json：每个字符串字段一块
    bs = _read_json(_dot_ainovel(br) / "basic_settings.json", {})
    for k, v in (bs or {}).items():
        if isinstance(v, str) and v.strip():
            chunks.append({"kind": "settings", "title": f"基本设定·{k}", "content": v.strip(), "anchor": "basic_settings.json"})

    # ② elements.json：角色/物品/设定 各一块
    elems = _read_json(_dot_ainovel(br) / "elements.json", {})
    for kind, items in (elems or {}).items():
        label = _KIND_LABEL.get(kind, kind)
        for e in items or []:
            name = str(e.get("name") or "").strip()
            if not name:
                continue
            alias = "、".join(e.get("alias") or e.get("terms") or [])
            desc = str(e.get("desc") or "").strip()
            chunks.append({
                "kind": "element", "title": f"{label}·{name}",
                "content": f"{name}（{alias}）{desc}".strip() or name,
                "anchor": f"elements.json/{kind}/{name}",
            })

    # ③ 设定集/*.md：按段落切（每段 ~200 字）
    st_dir = br / "设定集"
    if st_dir.is_dir():
        for f in sorted(st_dir.glob("*.md")):
            try:
                text = f.read_text(encoding="utf-8")
            except Exception:
                continue
            paras = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
            for i, p in enumerate(paras):
                for j in range(0, len(p), 200):
                    seg = p[j : j + 200].strip()
                    if seg:
                        chunks.append({"kind": "settings_md", "title": f"{f.stem}·段{i + 1}",
                                       "content": seg, "anchor": f"设定集/{f.name}"})

    # ④ arcs.json：每弧 l1/l2、l3 章纲、l4 场景、l5 正文、已落盘章、前文锚点
    arcs = _read_json(_dot_ainovel(br) / "arcs.json", {})
    for arc in (arcs.get("arcs") or []):
        aname = str(arc.get("name") or arc.get("id") or "弧")
        l1 = str(arc.get("l1") or "").strip()
        if l1:
            chunks.append({"kind": "l1", "title": f"{aname}·l1", "content": l1, "anchor": f"arcs.json/{aname}/l1"})
        l2 = str(arc.get("l2") or "").strip()
        if l2:
            chunks.append({"kind": "l2", "title": f"{aname}·l2", "content": l2, "anchor": f"arcs.json/{aname}/l2"})
        pa = arc.get("prev_anchor") or []
        if pa:
            chunks.append({"kind": "anchor", "title": f"{aname}·前文锚点",
                           "content": "\n".join(pa) if isinstance(pa, list) else str(pa),
                           "anchor": f"arcs.json/{aname}/prev_anchor"})
        st = arc.get("state") or {}
        lv = st.get("levels") or {}
        l3d = (lv.get("l3") or {}).get("data") or {}
        if isinstance(l3d, dict):
            if isinstance(l3d.get("chapters"), list):
                for i, ch in enumerate(l3d["chapters"]):
                    core = " ".join(str(x) for x in [ch.get("title"), ch.get("core")] if x)
                    beats = "；".join(str(x) for x in ch.get("beats") or [])
                    chunks.append({"kind": "l3", "title": f"{aname}·第{i + 1}章",
                                   "content": f"{core} {beats}".strip(), "anchor": f"arcs.json/{aname}/l3.ch{i}"})
            elif l3d.get("title") or l3d.get("core"):
                beats = "；".join(str(x) for x in l3d.get("beats") or [])
                chunks.append({"kind": "l3", "title": f"{aname}·章核心",
                               "content": f"{l3d.get('title')} {l3d.get('core')} {beats}".strip(),
                               "anchor": f"arcs.json/{aname}/l3"})
        scenes = (lv.get("l4") or {}).get("scenes") or []
        for i, sc in enumerate(scenes):
            parts = [str(sc.get("name") or "")]
            for k in ("actions", "dialogues", "conflicts", "details"):
                vals = sc.get(k) or []
                if isinstance(vals, list):
                    parts.extend(str(x) for x in vals)
                elif vals:
                    parts.append(str(vals))
            chunks.append({"kind": "l4", "title": f"{aname}·场景{i + 1}",
                           "content": " ".join(parts).strip(), "anchor": f"arcs.json/{aname}/l4.sc{i}"})
        l5 = str((lv.get("l5") or {}).get("text") or "").strip()
        for i in range(0, len(l5), 300):
            seg = l5[i : i + 300].strip()
            if seg:
                chunks.append({"kind": "l5", "title": f"{aname}·正文{i // 300 + 1}",
                               "content": seg, "anchor": f"arcs.json/{aname}/l5"})
        for j, ch in enumerate(arc.get("chapters") or []):
            txt = str(ch.get("text") or "")
            for i in range(0, len(txt), 300):
                seg = txt[i : i + 300].strip()
                if seg:
                    chunks.append({"kind": "prose", "title": f"{aname}·已落盘第{j + 1}章",
                                   "content": seg, "anchor": f"arcs.json/{aname}/chapters[{j}]"})
    return chunks


async def build_book_index(book_root: str | Path) -> dict[str, Any]:
    """采集本书分块 → embedding（BGE-M3）→ 落盘 .ainovel/search.db。返回统计。"""
    from .embed_client import get_embeddings
    chunks = _iter_book_chunks(book_root)
    if not chunks:
        return {"status": "ok", "chunks": 0, "embedded": 0}
    texts = [c["content"] for c in chunks]
    vecs = await get_embeddings(texts)
    db = _db_path(book_root)
    db.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db))
    conn.execute("DROP TABLE IF EXISTS chunks")
    conn.execute("CREATE TABLE chunks (id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT, title TEXT, content TEXT, anchor TEXT, vec TEXT)")
    rows = []
    embedded = 0
    for i, c in enumerate(chunks):
        v = vecs[i] if i < len(vecs) and vecs[i] is not None else None
        if v is not None:
            embedded += 1
        rows.append((c["kind"], c["title"], c["content"], c.get("anchor", ""), json.dumps(v) if v else None))
    conn.executemany("INSERT INTO chunks (kind,title,content,anchor,vec) VALUES (?,?,?,?,?)", rows)
    conn.commit()
    conn.close()
    return {"status": "ok", "chunks": len(rows), "embedded": embedded}


def _load_chunks(book_root: str | Path) -> list[dict[str, Any]]:
    db = _db_path(book_root)
    if not db.exists():
        return []
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT id,kind,title,content,anchor,vec FROM chunks").fetchall()
    conn.close()
    out = []
    for r in rows:
        v = r["vec"]
        out.append({"id": r["id"], "kind": r["kind"], "title": r["title"],
                    "content": r["content"], "anchor": r["anchor"],
                    "vec": json.loads(v) if v else None})
    return out


# ════════════════════════════════════════════════════════════════════
# 各源检索
# ════════════════════════════════════════════════════════════════════
def _snippet(text: str, limit: int = 180) -> str:
    text = re.sub(r"\s+", " ", str(text or "")).strip()
    return text if len(text) <= limit else text[:limit] + "…"


def _intent(query: str) -> str:
    q = str(query or "")
    # craft（怎么写/技巧）最具体 → 优先；其次 setting/character/plot
    for intent in ("craft", "setting", "character", "plot"):
        kws = _INTENT_KEYWORDS.get(intent, ())
        if any(k in q for k in kws):
            return intent
    return "general"


# 意图 → 各分块类型权重（查角色偏向元素，查剧情偏向弧/阶梯/正文）
_INTENT_KIND_WEIGHT: dict[str, dict[str, float]] = {
    "setting": {"settings": 1.6, "settings_md": 1.6, "element": 0.8, "l1": 1.0, "l2": 1.2, "l3": 1.0, "l4": 1.0, "l5": 0.7, "prose": 0.7, "anchor": 0.8},
    "character": {"element": 1.8, "settings": 1.1, "l3": 1.2, "l4": 1.1, "l2": 1.1, "l5": 0.8, "prose": 0.8, "l1": 1.0, "settings_md": 0.8, "anchor": 1.0},
    "plot": {"l2": 1.5, "l3": 1.5, "l4": 1.4, "l5": 1.2, "prose": 1.2, "l1": 1.2, "anchor": 1.1, "settings_md": 0.8, "settings": 0.9, "element": 0.9},
    "craft": {"settings_md": 1.4, "element": 1.1, "l5": 1.2, "prose": 1.2, "l4": 1.1, "l3": 1.1, "settings": 1.1, "anchor": 0.8, "l1": 0.9, "l2": 1.0},
    "general": {k: 1.0 for k in ("settings", "settings_md", "element", "l1", "l2", "l3", "l4", "l5", "prose", "anchor")},
}


async def _search_book(query: str, book_root: str | Path, top_k: int, intent: str) -> list[dict[str, Any]]:
    if _index_stale(book_root):
        # 自愈：索引缺失或内容变更 → 现建（改完设定/阶梯/正文后检索自动看到最新内容）
        await build_book_index(book_root)
    chunks = _load_chunks(book_root)
    if not chunks:
        return []
    bm = _bm25_scores(query, chunks)
    # 向量余弦（仅对带 vec 的块）
    qv: list[float] | None = None
    try:
        from .embed_client import get_embedding
        qv = await get_embedding(query)
    except Exception:
        qv = None
    cos = [0.0] * len(chunks)
    stale_vec = 0
    if qv is not None:
        import numpy as np
        qn = np.array(qv, dtype=np.float32)
        qn = qn / (np.linalg.norm(qn) + 1e-9)
        for i, c in enumerate(chunks):
            v = c.get("vec")
            if not v:
                continue
            if len(v) != len(qv):
                # 嵌入服务商升维等模型变更：旧向量作废，跳过评分并后台重建本书索引（自愈）
                stale_vec += 1
                continue
            vn = np.array(v, dtype=np.float32)
            vn = vn / (np.linalg.norm(vn) + 1e-9)
            cos[i] = float(np.dot(qn, vn))
        if stale_vec:
            _rebuild_book_index_bg(book_root)
    bmax = max(bm) if bm and max(bm) > 0 else 1.0
    kw = _INTENT_KIND_WEIGHT.get(intent, _INTENT_KIND_WEIGHT["general"])
    scored = []
    for i, c in enumerate(chunks):
        bnorm = bm[i] / bmax if bmax else 0.0
        s = (0.55 * bnorm + 0.45 * cos[i]) * kw.get(c.get("kind", "general"), 1.0)
        scored.append((s, c))
    scored.sort(key=lambda x: x[0], reverse=True)
    return [{
        "id": f"book:{c.get('anchor')}", "source": "book", "kind": c.get("kind"), "title": c.get("title"),
        "snippet": _snippet(c.get("content")), "anchor": c.get("anchor"), "score": round(s, 4),
    } for s, c in scored[:top_k]]


@lru_cache(maxsize=8)
def _load_corpus_chapters(path: str, mtime: float) -> list[dict[str, str]]:
    """读取语料 txt，按「第X章」切章，返回 [{title, content}]。mtime 参与缓存键。"""
    p = Path(path)
    try:
        text = p.read_text(encoding="utf-8")
    except Exception:
        return []
    parts = re.split(r"(?=第\s*\d+\s*章)", text)
    chapters = []
    for seg in parts:
        seg = seg.strip()
        if not seg:
            continue
        m = re.match(r"第\s*\d+\s*章[^\n]*", seg)
        title = (m.group(0).strip() if m else "开篇")[:40]
        chapters.append({"title": title, "content": seg})
    return chapters


async def _search_corpus(query: str, top_k: int) -> list[dict[str, Any]]:
    """语料检索：持久索引就绪 → BM25+向量（快）；否则回退逐本切章 BM25（不阻塞）。"""
    if _corpus_index_ready():
        return await _search_corpus_indexed(query, top_k)
    return _search_corpus_bm25(query, top_k)


def _search_corpus_bm25(query: str, top_k: int) -> list[dict[str, Any]]:
    """回退路径：逐本全量切章 BM25（无持久索引时用，简单但慢）。"""
    corpus_dir = Path(getattr(SETTINGS, "corpus_dir", None) or "")
    if not corpus_dir or not corpus_dir.is_dir():
        return []
    files = sorted(corpus_dir.glob("**/*.txt"))
    if not files:
        return []
    best: list[tuple[float, dict[str, Any]]] = []
    for f in files:
        try:
            mtime = f.stat().st_mtime
        except Exception:
            mtime = 0.0
        chapters = _load_corpus_chapters(str(f), mtime)
        if not chapters:
            continue
        rel = str(f.relative_to(corpus_dir)).replace("\\", "/")
        bm = _bm25_scores(query, chapters)
        for i, ch in enumerate(chapters):
            if bm[i] > 0:
                best.append((bm[i], {
                    "id": f"corpus:{rel}#{i}", "source": "corpus", "kind": "chapter",
                    "title": f"{f.name}·{ch['title']}", "snippet": _snippet(ch["content"]),
                    "anchor": f"{rel} 第{i + 1}段", "score": round(bm[i], 4),
                }))
    best.sort(key=lambda x: x[0], reverse=True)
    return [d for _, d in best[:top_k]]


# ════════════════════════════════════════════════════════════════════
# 语料持久索引（corpus/search.db：meta 表存每本 mtime，chunks 表存章节块+向量）
# ════════════════════════════════════════════════════════════════════
_corpus_build_progress: dict[str, Any] = {}


def _corpus_dir() -> Path:
    return Path(getattr(SETTINGS, "corpus_dir", None) or "")


def _corpus_index_path() -> Path:
    return _corpus_dir() / "search.db"


def _corpus_txt_files() -> list[Path]:
    cd = _corpus_dir()
    if not cd.is_dir():
        return []
    return sorted(cd.rglob("*.txt"))


def _corpus_index_ready() -> bool:
    """索引存在且覆盖所有语料 txt 且都未过期。"""
    db = _corpus_index_path()
    if not db.exists():
        return False
    try:
        conn = sqlite3.connect(str(db))
        meta = {r[0]: float(r[1]) for r in conn.execute("SELECT book, mtime FROM meta")}
        conn.close()
    except Exception:
        return False
    if not meta:
        return False
    for f in _corpus_txt_files():
        rel = str(f.relative_to(_corpus_dir())).replace("\\", "/")
        if rel not in meta:
            return False
        try:
            if f.stat().st_mtime > meta[rel] + 0.01:
                return False
        except Exception:
            return False
    return True


async def build_corpus_index() -> dict[str, Any]:
    """逐本语料重建索引（mtime 变更才重灌该书），进度写 _corpus_build_progress。"""
    from .embed_client import get_embeddings
    files = _corpus_txt_files()
    db = _corpus_index_path()
    db.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db))
    conn.execute("CREATE TABLE IF NOT EXISTS meta (book TEXT PRIMARY KEY, mtime REAL, chapters INTEGER, built_at TEXT)")
    conn.execute("CREATE TABLE IF NOT EXISTS chunks (id INTEGER PRIMARY KEY AUTOINCREMENT, book TEXT, title TEXT, content TEXT, anchor TEXT, vec TEXT)")
    total = 0
    embedded = 0
    rebuilt = 0
    changed_files = []
    for f in files:
        rel = str(f.relative_to(_corpus_dir())).replace("\\", "/")
        try:
            mtime = f.stat().st_mtime
        except Exception:
            mtime = 0.0
        old = conn.execute("SELECT mtime FROM meta WHERE book=?", (rel,)).fetchone()
        if old and float(old[0]) >= mtime - 0.01:
            total += int(conn.execute("SELECT chapters FROM meta WHERE book=?", (rel,)).fetchone()[0] or 0)
            continue
        chapters = _load_corpus_chapters(str(f), mtime)
        _corpus_build_progress.update({"status": "building", "book": f.name, "chapter": len(chapters), "done": rebuilt})
        texts = [c["content"] for c in chapters]
        vecs = await get_embeddings(texts)
        conn.execute("DELETE FROM chunks WHERE book=?", (rel,))
        rows = []
        for i, c in enumerate(chapters):
            v = vecs[i] if i < len(vecs) and vecs[i] is not None else None
            if v is not None:
                embedded += 1
            rows.append((rel, c["title"], c["content"], f"{rel}#{c['title']}", json.dumps(v) if v else None))
        conn.executemany("INSERT INTO chunks (book,title,content,anchor,vec) VALUES (?,?,?,?,?)", rows)
        conn.execute("INSERT OR REPLACE INTO meta (book, mtime, chapters, built_at) VALUES (?,?,?,?)",
                     (rel, mtime, len(rows), _now_iso()))
        total += len(rows)
        rebuilt += 1
        changed_files.append(rel)
    conn.commit()
    conn.close()
    _corpus_build_progress.update({"status": "done", "book": "", "done": rebuilt, "books": len(files)})
    return {"status": "ok", "chunks": total, "embedded": embedded, "books": len(files), "rebuilt": changed_files}


_book_rebuild_inflight: set[str] = set()


def _rebuild_book_index_bg(book_root: str | Path) -> None:
    """检测到旧模型向量（维度与当前嵌入模型不符）→ 后台整本重建索引（去重防风暴）。"""
    key = str(book_root)
    if key in _book_rebuild_inflight:
        return
    _book_rebuild_inflight.add(key)

    async def _run() -> None:
        try:
            await build_book_index(book_root)
        except Exception:
            pass
        finally:
            _book_rebuild_inflight.discard(key)

    try:
        asyncio.get_running_loop().create_task(_run())
    except RuntimeError:
        _book_rebuild_inflight.discard(key)


_corpus_rebuild_inflight = False


def _rebuild_corpus_index_bg() -> None:
    """语料索引向量维度过时 → 清库后台全量重建。

    build_corpus_index 按 mtime 跳过未变更文件，对「模型换维度」这种无 mtime 变化
    的失效必须先删库；重建期间 _search_corpus 退化为 BM25 路径，不阻塞检索。"""
    global _corpus_rebuild_inflight
    if _corpus_rebuild_inflight:
        return
    _corpus_rebuild_inflight = True

    async def _run() -> None:
        global _corpus_rebuild_inflight
        try:
            try:
                _corpus_index_path().unlink()
            except FileNotFoundError:
                pass
            await build_corpus_index()
        except Exception:
            pass
        finally:
            _corpus_rebuild_inflight = False

    try:
        asyncio.get_running_loop().create_task(_run())
    except RuntimeError:
        _corpus_rebuild_inflight = False


def _now_iso() -> str:
    import datetime
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _load_corpus_indexed() -> list[dict[str, Any]]:
    db = _corpus_index_path()
    if not db.exists():
        return []
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT id,book,title,content,anchor,vec FROM chunks").fetchall()
    conn.close()
    out = []
    for r in rows:
        v = r["vec"]
        out.append({"id": f"corpus:{r['id']}", "book": r["book"], "title": r["title"],
                    "content": r["content"], "anchor": r["anchor"],
                    "vec": json.loads(v) if v else None})
    return out


async def _search_corpus_indexed(query: str, top_k: int) -> list[dict[str, Any]]:
    chunks = _load_corpus_indexed()
    if not chunks:
        return []
    bm = _bm25_scores(query, chunks)
    cos = [0.0] * len(chunks)
    stale_vec = 0
    qv: list[float] | None = None
    try:
        from .embed_client import get_embedding
        qv = await get_embedding(query)
    except Exception:
        qv = None
    if qv is not None:
        import numpy as np
        qn = np.array(qv, dtype=np.float32)
        qn = qn / (np.linalg.norm(qn) + 1e-9)
        for i, c in enumerate(chunks):
            v = c.get("vec")
            if not v:
                continue
            if len(v) != len(qv):
                # 旧模型向量作废，跳过评分并触发语料索引后台重建（自愈）
                stale_vec += 1
                continue
            vn = np.array(v, dtype=np.float32)
            vn = vn / (np.linalg.norm(vn) + 1e-9)
            cos[i] = float(np.dot(qn, vn))
        if stale_vec:
            _rebuild_corpus_index_bg()
    bmax = max(bm) if bm and max(bm) > 0 else 1.0
    scored = []
    for i, c in enumerate(chunks):
        bnorm = bm[i] / bmax if bmax else 0.0
        scored.append(((0.55 * bnorm + 0.45 * cos[i]), c))
    scored.sort(key=lambda x: x[0], reverse=True)
    return [{
        "id": c["id"], "source": "corpus", "kind": "chapter", "title": f"{c.get('book')}·{c.get('title')}",
        "snippet": _snippet(c.get("content")), "anchor": c.get("anchor"), "score": round(s, 4),
    } for s, c in scored[:top_k]]


def _load_csv_tables() -> list[dict[str, Any]]:
    csv_dir = Path(__file__).resolve().parents[2] / "app" / "references" / "csv"
    if not csv_dir.is_dir():
        return []
    import csv as _csv
    tables = []
    for f in sorted(csv_dir.glob("*.csv")):
        try:
            with open(f, "r", encoding="utf-8-sig", newline="") as fh:
                rows = list(_csv.DictReader(fh))
        except Exception:
            continue
        if not rows:
            continue
        tables.append({"table": f.stem, "path": f.name, "rows": rows})
    return tables


async def _search_csv(query: str, top_k: int, genre: str | None = None) -> list[dict[str, Any]]:
    tables = _load_csv_tables()
    if not tables:
        return []
    cands: list[tuple[float, dict[str, Any]]] = []
    for tb in tables:
        # 行 → 文本（拼接全部单元格），带题材过滤
        rows = tb["rows"]
        docs = []
        keep = []
        for row in rows:
            cell_genre = str(row.get("适用题材") or "").strip()
            if genre and cell_genre and cell_genre != "全部" and genre not in cell_genre:
                continue
            text = " ".join(str(v) for v in row.values() if v is not None)
            docs.append({"id": len(docs), "content": text})
            keep.append(row)
        if not docs:
            continue
        bm = _bm25_scores(query, docs)
        for i, row in enumerate(keep):
            if bm[i] > 0:
                key = next((row.get(c) for c in ("问题", "要点", "内容", "示例", "建议") if row.get(c)), "")
                cands.append((bm[i], {
                    "id": f"csv:{tb['table']}:{i}", "source": "csv", "kind": "reference",
                    "title": f"参考资料·{tb['table']}",
                    "snippet": _snippet(f"{key}：{' '.join(str(v) for v in row.values() if v)}"),
                    "anchor": f"references/csv/{tb['path']}",
                    "score": round(bm[i], 4),
                }))
    cands.sort(key=lambda x: x[0], reverse=True)
    return [d for _, d in cands[:top_k]]


# ════════════════════════════════════════════════════════════════════
# 源⑤ 情节模板库（合格模板：冲突/转折/悬念/道具…最该查的写作资产）
# ════════════════════════════════════════════════════════════════════
def _search_templates(query: str, top_k: int) -> list[dict[str, Any]]:
    try:
        from .plot_library import get_plot_template_library
        lib = get_plot_template_library()
        tmpls = lib.list()
    except Exception:
        return []
    if not tmpls:
        return []
    docs = []
    for t in tmpls:
        docs.append({"id": str(t.get("id") or len(docs)), "content": lib.match_text(t) or ""})
    bm = _bm25_scores(query, docs)
    scored: list[tuple[float, dict[str, Any]]] = []
    for i, t in enumerate(tmpls):
        if bm[i] <= 0:
            continue
        name = str(t.get("name") or t.get("id") or f"模板{i + 1}")
        archetype = str(t.get("archetype") or "")
        scored.append((bm[i], {
            "id": f"template:{t.get('id') or i}", "source": "template", "kind": "plot_template",
            "title": f"情节模板·{name}{'（' + archetype + '）' if archetype else ''}",
            "snippet": _snippet(docs[i]["content"]), "anchor": f"plot_template_library/{name}",
            "score": round(bm[i], 4),
        }))
    scored.sort(key=lambda x: x[0], reverse=True)
    return [d for _, d in scored[:top_k]]


# ════════════════════════════════════════════════════════════════════
# 源⑥ 参考资料 md（app/references/**/*.md：题材知识/结构/爽点设计文档）
# ════════════════════════════════════════════════════════════════════
_REFMD_DIR = Path(__file__).resolve().parents[2] / "app" / "references"


@lru_cache(maxsize=4)
def _load_refmd_sections(_key: str) -> list[dict[str, str]]:
    """读 app/references/ 下非 csv 的 md，按标题切节。_key 含 mtime 用于缓存失效。"""
    sections: list[dict[str, str]] = []
    if not _REFMD_DIR.is_dir():
        return sections
    for f in sorted(_REFMD_DIR.rglob("*.md")):
        parts = f.parts
        if "csv" in parts or f.name.lower() == "readme.md":
            continue
        try:
            text = f.read_text(encoding="utf-8")
        except Exception:
            continue
        rel = str(f.relative_to(_REFMD_DIR)).replace("\\", "/")
        lines = text.splitlines()
        cur_title = f.stem
        cur: list[str] = []

        def flush() -> None:
            body = " ".join(cur).strip()
            if body and len(body) > 20:
                sections.append({"title": cur_title, "content": body, "anchor": f"references/{rel}#{cur_title}"})

        for ln in lines:
            if ln.startswith("#"):
                flush()
                cur_title = ln.lstrip("# ").strip() or f.stem
                cur = []
            else:
                cur.append(ln.strip())
        flush()
    return sections


def _refmd_cache_key() -> str:
    if not _REFMD_DIR.is_dir():
        return "none"
    parts = []
    for f in sorted(_REFMD_DIR.rglob("*.md")):
        try:
            parts.append(f"{f.name}:{f.stat().st_mtime:.0f}")
        except Exception:
            pass
    return "|".join(parts)


def _search_reference_md(query: str, top_k: int) -> list[dict[str, Any]]:
    sections = _load_refmd_sections(_refmd_cache_key())
    if not sections:
        return []
    bm = _bm25_scores(query, sections)
    scored: list[tuple[float, dict[str, Any]]] = []
    for i, s in enumerate(sections):
        if bm[i] <= 0:
            continue
        scored.append((bm[i], {
            "id": f"refmd:{i}", "source": "refmd", "kind": "reference",
            "title": f"参考资料·{s['title']}", "snippet": _snippet(s["content"]),
            "anchor": s["anchor"], "score": round(bm[i], 4),
        }))
    scored.sort(key=lambda x: x[0], reverse=True)
    return [d for _, d in scored[:top_k]]


# ════════════════════════════════════════════════════════════════════
# 源⑦ 内置网页搜索（keyless：抓公开搜索页 cn.bing.com，无需 AnySearch/搜索 API key）
# ════════════════════════════════════════════════════════════════════
_WEB_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
_web_status: dict[str, Any] = {"ok": False, "engine": "cn.bing.com", "last_error": ""}


# 会让 bing 聚焦跑偏的常见泛化 2-gram（长 query 如「中国古代镖局押镖规矩」会被 bing
# 降级成「中国」百科结果——见 novel_opening_100k 测试；用核心词过滤无关结果）
_WEB_STOP_BIGRAM = {
    "中国", "古代", "历史", "背景", "参考", "设定", "相关", "如何", "什么", "传统",
    "行业", "文明", "发展", "渊源", "起源", "文化", "时代", "今天", "当前", "一个",
    "本书", "这部", "这种", "作为", "用来", "知道", "专业", "资料", "内容", "方面",
    "百度", "百科", "中华", "中央", "人民", "世界", "开放", "百科网",
}
_WEB_PUNCT = re.compile(r"[\s，。；、？！,.;:()（）\"'“”…—·～～]+")


def _web_key_bigrams(query: str) -> set[str]:
    """从 query 提取核心 2-gram（过滤泛化词），用于 web 结果相关度过滤。"""
    q2 = _WEB_PUNCT.sub("", str(query or ""))
    if len(q2) < 2:
        return set()
    bigrams = {q2[i:i + 2] for i in range(len(q2) - 1)}
    return {b for b in bigrams if b not in _WEB_STOP_BIGRAM and not b.isdigit()}


def _web_key_chars(query: str) -> set[str]:
    """核心 2-gram 里出现的字符集（宽松过滤用：结果含任一核心字即保留）。"""
    return set("".join(_web_key_bigrams(query)))


# 常见"跨界杂质"首字（如「国古」「代镖」这类由停用词+核心词拼出的半截），
# 拼接 query 时跳过以它们开头的 bigram，避免 bing 聚焦跑偏。
_WEB_SKIP_LEAD = set("国的古代来要这那等与之及也在从向位于如上把被中里后前后上下不没很又更太最也可还就都")
# 抽象/句子化 bigram（非名词核心），拼接时整体排除（如「真实古代镖局…」里的「真实」）
_WEB_ABSTRACT_BIGRAM = {
    "真实", "实际", "各种", "相关", "主要", "重要", "一个", "一些", "信息", "内容",
    "关于", "对于", "具体", "详细", "有关", "包括", "还有", "需要", "想要", "帮助",
    "知道", "资料", "情况", "方面", "东西", "时候", "地方", "方式", "方法", "介绍",
}


def _web_clean_query(query: str) -> str:
    """web 查询精简：提取核心名词短语，丢弃泛化/跨界杂质。

    长中文 query 易被 bing 聚焦跑偏（「中国古代镖局押镖规矩」→中国百科）。
    策略：去标点 → 取非停用核心 2-gram 里「以名词首字开头」的前 4 个去重拼接
    （如「镖局 押镖 规矩」），bing 按空格多词 AND 检索，命中率远高于原句。
    """
    q2 = _WEB_PUNCT.sub("", str(query or ""))
    if not q2:
        return ""
    bg = _web_key_bigrams(query)
    if not bg:
        return q2[:24]
    picked: list[str] = []
    seen: set[str] = set()
    for i in range(len(q2) - 1):
        b = q2[i:i + 2]
        if (b in bg and b not in seen and b[0] not in _WEB_SKIP_LEAD
                and b not in _WEB_ABSTRACT_BIGRAM):
            picked.append(b)
            seen.add(b)
            if len(picked) >= 4:
                break
    if picked:
        return " ".join(picked)
    return q2[:24]


def _parse_bing(html: str, top_k: int, key_chars: set[str] | None = None,
                key_bigrams: set[str] | None = None) -> list[dict[str, Any]]:
    """解析 Bing 搜索结果 HTML（b_algo 列表）。命中失败返回 []。

    key_chars 非空时做第一级过滤：结果标题+摘要必须含 query 核心 2-gram 的**任一字符**，
    否则判为「bing 聚焦跑偏」的无关结果（如长 query 被降级成中国百科）。
    key_bigrams 非空时做第二级去噪：标题+摘要必须命中**至少一个** query 核心 2-gram，
    否则判为噪音（防单字符误判——如「希腊数字」里撞上一个「市/商」就放进来）。
    """
    results: list[dict[str, Any]] = []
    for m in re.finditer(r'<li class="b_algo".*?</li>', html, re.S):
        block = m.group(0)
        am = re.search(r'<h2[^>]*><a[^>]*href="([^"]+)"[^>]*>(.*?)</a>', block, re.S)
        if not am:
            continue
        url = am.group(1).strip()
        title = re.sub(r"<[^>]+>", "", am.group(2)).strip()
        if not title:
            continue
        pm = re.search(r"<p[^>]*>(.*?)</p>", block, re.S)
        snippet = re.sub(r"<[^>]+>", "", pm.group(1)).strip() if pm else ""
        blob = title + (snippet or "")
        if key_chars:
            if not any(c in blob for c in key_chars):
                continue  # 一级：与 query 核心词无关 → 过滤（bing 聚焦跑偏）
        if key_bigrams:
            blob_bg = {blob[i:i + 2] for i in range(max(0, len(blob) - 1))}
            if not (blob_bg & key_bigrams):
                continue  # 二级：query 核心 2-gram 一个都没命中 → 噪音
        results.append({
            "id": f"web:{len(results)}", "source": "web", "kind": "web",
            "title": f"网页·{title[:44]}", "snippet": _snippet(snippet),
            "anchor": url, "score": 1.0,
        })
        if len(results) >= top_k:
            break
    return results


def _fetch_search_page(query: str, top_k: int, key_chars: set[str] | None = None,
                       key_bigrams: set[str] | None = None) -> list[dict[str, Any]]:
    """同步抓取公开搜索页并解析（在 asyncio.to_thread 中跑，避免阻塞事件循环）。"""
    import urllib.parse
    import urllib.request
    q = urllib.parse.quote(query)
    n = min(top_k + 2, 12)
    candidates = [f"https://cn.bing.com/search?q={q}&count={n}",
                  f"https://www.bing.com/search?q={q}&count={n}"]
    for u in candidates:
        try:
            req = urllib.request.Request(u, headers={
                "User-Agent": _WEB_UA, "Accept-Language": "zh-CN,zh;q=0.9",
                "Accept": "text/html,application/xhtml+xml",
            })
            with urllib.request.urlopen(req, timeout=12) as resp:
                html = resp.read().decode("utf-8", errors="ignore")
            if html and "b_algo" in html:
                _web_status.update({"ok": True, "engine": u, "last_error": ""})
                return _parse_bing(html, top_k, key_chars, key_bigrams)
            _web_status.update({"last_error": "响应无 b_algo（可能被反爬/需 JS）"})
        except Exception as exc:
            _web_status.update({"last_error": f"{type(exc).__name__}: {exc}"})
    return []


async def _search_web(query: str, top_k: int) -> list[dict[str, Any]]:
    """内置网页搜索：keyless 抓 cn.bing.com 公开页（无 AnySearch/搜索 API key）。

    【2026-08-07 修复】长中文 query 易被 bing 聚焦跑偏（如「中国古代镖局押镖规矩」
    返回中国百科）→ 精简 query 为核心名词短语 + 用核心字符过滤无关结果。
    返回带来源标注的结构化结果；被反爬/网络异常时优雅空（体检面板显示 last_error）。
    """
    q_clean = _web_clean_query(query)
    kc = _web_key_chars(query)
    kb = _web_key_bigrams(query)
    return await asyncio.to_thread(_fetch_search_page, q_clean, top_k, kc, kb)


def web_status() -> dict[str, Any]:
    return dict(_web_status)


# 体检触发的一次性 web 连通性探测（同步抓一次，成功/失败都记状态防重试风暴）
_web_probe_started = False


def _probe_web_if_never() -> dict[str, Any] | None:
    """体检用：从未真实探测过 web 源时，同步抓一次公开页做连通性验证。

    返回更新后的 _web_status dict；若本次是探测（此前未测过）则真正抓取
    （最多 ~12s timeout，内部 _fetch_search_page 已兜住异常）。成功/失败都
    写 _web_status，后续调用不再重复抓取（命中缓存秒回）。
    """
    global _web_probe_started
    if _web_status.get("ok") or _web_status.get("probed"):
        return None  # 已探测过（成功或失败），调用方走 web_status()
    if _web_probe_started:
        return None  # 并发体检已在探测，本次不等
    _web_probe_started = True
    try:
        _web_status["probed"] = True
        _fetch_search_page("镖局", 3)
        # 成功路径已由 _fetch_search_page 更新 ok/engine/last_error
    except Exception as e:  # 兜底，不应到达（_fetch_search_page 内部已 catch）
        _web_status["last_error"] = f"{type(e).__name__}: {e}"
    return dict(_web_status)


# ════════════════════════════════════════════════════════════════════
# 联邦入口：查询理解（自主判别）→ 各垂直源检索 → RRF 跨源融合 →（可选 LLM rerank）
# ════════════════════════════════════════════════════════════════════
SEARCH_SOURCES = ["book", "corpus", "csv", "refmd", "template", "web"]
_SRC_LABEL = {"book": "本书", "corpus": "语料", "csv": "参考资料", "refmd": "参考文档", "template": "模板", "web": "外部"}

# 源说明（喂给查询理解 LLM，让它学会自主判别该查哪些源）
_SRC_DESC = {
    "book": "本书内容（设定/元素/弧/阶梯/已写正文/前文锚点）",
    "corpus": "参考语料库（大奉打更人/青山等参考小说的章节原文）",
    "csv": "参考资料表（桥段套路/爽点节奏/命名规则/场景写法等写作知识表）",
    "refmd": "参考文档 md（题材知识/力量体系/爽点设计/多章结构等设计文档）",
    "template": "情节模板库（合格模板：冲突/转折/悬念/道具的桥段组织范例）",
    "web": "外部网页（真实世界事实/流行趋势/平台风格，需确与写作相关的才查）",
}


def _book_context(book_root: str | Path) -> str:
    """本书上下文：书/类型/元素名/弧名——喂给查询理解 LLM 识别书内实体。"""
    br = Path(book_root)
    bs = _read_json(_dot_ainovel(br) / "basic_settings.json", {})
    elems = _read_json(_dot_ainovel(br) / "elements.json", {})
    names: list[str] = []
    for k in ("characters", "items", "settings"):
        for e in elems.get(k) or []:
            n = str(e.get("name") or "").strip()
            if n:
                names.append(n)
    arcs = _read_json(_dot_ainovel(br) / "arcs.json", {})
    arc_names = [str(a.get("name") or "") for a in (arcs.get("arcs") or [])]
    parts = []
    if bs.get("name") or bs.get("genre"):
        parts.append(f"书《{bs.get('name') or ''}》类型:{bs.get('genre') or ''}")
    if names:
        parts.append(f"书中元素:{'、'.join(names[:40])}")
    if arc_names:
        parts.append(f"弧:{'、'.join(arc_names[:10])}")
    return "；".join(parts) or "（本书还没有内容）"


async def understand_query(
    query: str,
    book_root: str | Path,
    allowed_sources: list[str] | None = None,
) -> dict[str, Any]:
    """AnySearch 式查询理解：LLM 自主分类判别 + 垂直源路由 + 每源最优子查询分解。

    返回 {intent, sources, queries, reason}。LLM 失败回退：keyword 意图 + 全源 + 原查询。
    """
    from .llm_client import chat_json
    allowed = [s for s in (allowed_sources or SEARCH_SOURCES) if s in SEARCH_SOURCES]
    src_desc = "\n".join(f"- {k}：{v}" for k, v in _SRC_DESC.items() if k in allowed)
    sys_p = (
        "你是写书讨论检索的「查询理解引擎」。给定一个写作者的查询与本书上下文，你要自主判别：\n"
        "1. intent：查询属于哪类意图（craft 写作技法 / plot 情节规划 / character 角色设定 / setting 本书设定 / corpus 参考语料 / external 外部事实）。\n"
        "2. sources：判别该查哪些垂直源（可从以下选，**只在确实相关时才选，宁少勿滥**——这是自主判别）：\n"
        + src_desc + "\n"
        "3. queries：为每个选中的源写一个更贴合该源的最优子查询（如查书内角色就带书内名字，查桥段就写『X桥段怎么组织』）。\n"
        "必须返回 JSON：{\"intent\":\"...\",\"sources\":[\"book\",...],\"queries\":{\"book\":\"子查询\",...},\"reason\":\"一句话依据\"}。"
    )
    usr = f"查询：{query}\n【本书上下文】{_book_context(book_root)}"
    r = await chat_json(system=sys_p, user=usr, call_type="search_understand", temperature=0.1, max_tokens=400)
    data = r.get("data")
    if not isinstance(data, dict) or not data:
        return {"intent": _intent(query), "sources": allowed, "queries": {}, "reason": "LLM 分类失败，回退关键词"}
    intent = str(data.get("intent") or _intent(query))
    sources = [s for s in (data.get("sources") or []) if s in allowed] or allowed
    queries = {k: str(v) for k, v in (data.get("queries") or {}).items() if k in sources and str(v).strip()}
    return {"intent": intent, "sources": sources, "queries": queries, "reason": str(data.get("reason") or "")}


async def _search_one(src: str, query: str, book_root: str | Path, top_k: int, intent: str, genre: str | None) -> list[dict[str, Any]]:
    if src == "book":
        return await _search_book(query, book_root, top_k, intent)
    if src == "corpus":
        return await _search_corpus(query, top_k)
    if src == "csv":
        return await _search_csv(query, top_k, genre)
    if src == "refmd":
        return _search_reference_md(query, top_k)
    if src == "template":
        return _search_templates(query, top_k)
    if src == "web":
        return await _search_web(query, top_k)
    return []


async def search_around(
    query: str,
    book_root: str | Path,
    sources: list[str] | None = None,
    top_k: int = 6,
    genre: str | None = None,
    rerank: bool = False,
    smart: bool = True,
) -> dict[str, Any]:
    """六源联邦检索（AnySearch 式：查询理解自主判别 → 垂直源路由 → RRF 融合 → 可选 LLM rerank）。

    smart=True（默认）：LLM 查询理解——自主判别意图、选相关源、每源写子查询（宁少勿滥）。
    smart=False：关键词快路径（全源 + keyword intent）。
    每源召回 top-(top_k×2) → RRF（1/(60+rank)）→ 可选 rerank。返回 {results, sources_used, intent, reason}。
    """
    query = str(query or "").strip()
    if not query:
        return {"results": [], "sources_used": [], "intent": "general", "reason": ""}
    pool = [s for s in (sources or SEARCH_SOURCES) if s in SEARCH_SOURCES]
    per_source_top = max(top_k * 2, 8)
    per_source: dict[str, list[dict[str, Any]]] = {}
    sources_used: list[str] = []
    reason = ""
    if smart:
        u = await understand_query(query, book_root, allowed_sources=pool)
        intent, chosen, subq, reason = u["intent"], u["sources"], u["queries"], u["reason"]
    else:
        intent, chosen, subq, reason = _intent(query), pool, {}, ""
    for src in chosen:
        # web 源用**原 query**：LLM 子查询（understand_query）句子化，经核心词提取后
        # 易带「真实/古代/的/等」杂质使 bing 跑偏（实测「真实古代镖局…」→ 真实电影）；
        # 原 query 是写作者的真实问题，核心词提取效果稳定。
        src_query = query if src == "web" else (subq.get(src) or query)
        res = await _search_one(src, src_query, book_root, per_source_top, intent, genre)
        if res:
            sources_used.append(src)
        per_source[src] = res
    # RRF 融合
    rrf: dict[str, float] = {}
    order: dict[str, dict[str, Any]] = {}
    for src, res in per_source.items():
        for rank, r in enumerate(res):
            key = r.get("id") or f"{src}:{rank}"
            rrf[key] = rrf.get(key, 0.0) + 1.0 / (60.0 + rank + 1)
            order.setdefault(key, r)
    fused: list[dict[str, Any]] = []
    for key, sc in sorted(rrf.items(), key=lambda x: x[1], reverse=True):
        r = dict(order[key])
        r["rrf_score"] = round(sc, 4)
        fused.append(r)
    # 可选 LLM rerank（精确模式，默认关）
    if rerank and len(fused) > top_k:
        fused = await _rerank_llm(query, fused, top_k)
    return {
        "results": fused[:top_k], "sources_used": sources_used,
        "chosen_sources": chosen, "intent": intent, "reason": reason,
    }


async def _rerank_llm(query: str, results: list[dict[str, Any]], top_k: int) -> list[dict[str, Any]]:
    """LLM 精排：对融合后候选（截取 top-15）按相关性重排，回 top_k。失败回退原序。"""
    from .llm_client import chat_json
    cands = results[:15]
    if len(cands) <= top_k:
        return cands
    lines = [f"{i + 1}. [{r.get('source')}] {r.get('title')}\n   {_snippet(r.get('snippet'), 120)}" for i, r in enumerate(cands)]
    sys_p = (
        "你是检索结果重排器。根据「查询」与每条候选项的相关性，把候选项按从高到低排序，"
        "只返回相关度最高的若干条。必须只返回 JSON：{\"order\": [4, 2, 1, ...]}"
        "（order 是原序号列表，从最相关到最不相关，数量不超过输入条数）。"
    )
    usr = f"查询：{query}\n候选：\n" + "\n".join(lines)
    r = await chat_json(system=sys_p, user=usr, call_type="search_rerank", temperature=0.0, max_tokens=120)
    data = r.get("data") or {}
    order = data.get("order") or []
    ranked: list[dict[str, Any]] = []
    for idx in order:
        try:
            i = int(idx) - 1
            if 0 <= i < len(cands):
                ranked.append(cands[i])
        except Exception:
            continue
    return ranked[:top_k] if ranked else cands[:top_k]


# ════════════════════════════════════════════════════════════════════
# 检索体检：各源健康详情（哪些空/哪些有问题）
# ════════════════════════════════════════════════════════════════════
def source_status(book_root: str | Path) -> dict[str, Any]:
    """返回各检索源健康详情 + 整体 summary，供前端体检面板展示。"""
    br = Path(book_root)
    status: dict[str, Any] = {}

    # book：内容就绪度 + 索引状态
    elems = _read_json(_dot_ainovel(br) / "elements.json", {})
    arcs = _read_json(_dot_ainovel(br) / "arcs.json", {})
    arc_list = arcs.get("arcs") or []
    settings = _read_json(_dot_ainovel(br) / "basic_settings.json", {})
    prose_chars = 0
    for a in arc_list:
        for ch in a.get("chapters") or []:
            prose_chars += len(str(ch.get("text") or ""))
        # 未落盘的 l5 草稿也算进检索内容量
        l5 = str((a.get("state") or {}).get("levels", {}).get("l5", {}).get("text") or "")
        prose_chars += len(l5)
    db = _db_path(br)
    book = {
        "settings_generated": bool(settings),
        "elements": sum(len(elems.get(k) or []) for k in ("characters", "items", "settings")),
        "arcs": len(arc_list),
        "prose_chars": prose_chars,
        "chunks": _chunk_count(db),
        "index_fresh": not _index_stale(br),
        "last_built": _db_mtime_str(db),
    }
    if not book["settings_generated"]:
        book["issue"] = "基本设定未生成，检索不到本书内容（设定页生成设定集+元素）"
    elif book["chunks"] == 0:
        book["issue"] = "本书索引未建（检索时自动构建，或点重建）"
    status["book"] = book

    # corpus
    files = _corpus_txt_files()
    corpus = {"books": len(files), "index_ready": _corpus_index_ready(), "progress": dict(_corpus_build_progress)}
    if not files:
        corpus["issue"] = "语料库无 txt（corpus/**/*.txt）"
    elif not corpus["index_ready"]:
        corpus["issue"] = f"{len(files)} 本语料未建持久索引（点重建后检索秒回；当前回退全量 BM25）"
    status["corpus"] = corpus

    # csv / refmd
    csv_tables = _load_csv_tables()
    status["csv"] = {"tables": len(csv_tables), "names": [t["table"] for t in csv_tables] if len(csv_tables) <= 12 else [t["table"] for t in csv_tables[:12]]}
    if not csv_tables:
        status["csv"]["issue"] = "无参考资料 CSV"
    md_sections = _load_refmd_sections(_refmd_cache_key())
    md_files = sorted(set(s["anchor"].split("#")[0] for s in md_sections))
    status["refmd"] = {"docs": len(md_files), "sections": len(md_sections)}
    if not md_files:
        status["refmd"]["issue"] = "无参考资料 md（app/references/**/*.md）"

    # template
    try:
        from .plot_library import get_plot_template_library
        tmpl_count = len(get_plot_template_library().list())
    except Exception:
        tmpl_count = 0
    status["template"] = {"count": tmpl_count}
    if tmpl_count == 0:
        status["template"]["issue"] = "情节模板库为空——在榨干提取把达标章入库后这里才有内容"

    # web（内置 keyless，cn.bing.com；是否可用取决于网络/反爬）
    # 从未探测过 → 主动抓一次做连通性验证；首次最多等 5s 拿真实结果（之后命中缓存秒回）
    st_w = _probe_web_if_never()
    if st_w is not None:
        _web_probe_started = False  # 若本次没等到，允许下次体检再触发
    else:
        st_w = web_status()
    status["web"] = {"engine": st_w.get("engine"), "ok": st_w.get("ok"), "last_error": st_w.get("last_error")}
    if not st_w.get("ok"):
        status["web"]["issue"] = f"内置网页搜索未验证（{st_w.get('engine')}）——搜「外部」源时会自动尝试；上次错误：{st_w.get('last_error') or '尚未搜索过'}"

    # embed 配置
    embed_model = str(getattr(SETTINGS, "embed_model", "") or "").strip()
    embed_key = str(getattr(SETTINGS, "embed_api_key", "") or "").strip()
    status["embed"] = {"key_set": bool(embed_key), "model": embed_model}
    if not embed_key:
        status["embed"]["issue"] = "缺 EMBED_API_KEY——向量检索不可用（BM25 仍可用，到 API 预设页配向量模型）"
    elif not embed_model:
        status["embed"]["issue"] = "缺 EMBED_MODEL——到 API 预设页配向量模型"

    # summary
    healthy = empty = broken = 0
    for key, s in status.items():
        if s.get("issue"):
            if key in ("template", "web", "corpus"):
                empty += 1
            else:
                broken += 1
        else:
            healthy += 1
    status["summary"] = {"total": len(status), "healthy": healthy, "empty": empty, "broken": broken}
    return status


def _chunk_count(db: Path) -> int:
    if not db.exists():
        return 0
    try:
        conn = sqlite3.connect(str(db))
        n = conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
        conn.close()
        return int(n)
    except Exception:
        return 0


def _db_mtime_str(db: Path) -> str:
    try:
        import datetime
        return datetime.datetime.fromtimestamp(db.stat().st_mtime).strftime("%m-%d %H:%M")
    except Exception:
        return ""


def format_results(results: list[dict[str, Any]]) -> str:
    """把检索结果拼成带来源标注的 Markdown 上下文块，直入 LLM。"""
    if not results:
        return "（检索无命中）"
    lines = ["【检索结果】"]
    for r in results:
        head = f"- [{_SRC_LABEL.get(r.get('source'), r.get('source'))}·{r.get('kind')}] {r.get('title')}"
        anchor = r.get("anchor")
        if anchor:
            head += f"（→ {anchor}）"
        lines.append(head)
        lines.append(f"  {r.get('snippet')}")
    return "\n".join(lines)
