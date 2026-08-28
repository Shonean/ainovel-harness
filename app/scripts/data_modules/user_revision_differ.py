#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
User Revision Differ - 用户修订差分器

监听用户精修动作，把"AI 原稿 → 用户精修稿"的差分提取出来，
按 factual / style / preference / typo 分类，
最终把 style + preference 类的累积偏好写入 .story-system/user_preferences.json，
让下章 context-agent 能 inject 这些偏好作为"避雷+复制"指引。

工作流（被 /ainovel-finalize 触发）：

    1. 读 ai_draft（AI 起草版）和 final_text（用户精修稿）
    2. 用 difflib 算句级 diff hunks
    3. 每个 hunk 调用 LLM 分类（提供 prompt 模板，调方在 SKILL 层调 Agent）
       - 或：调用方提供已经分类好的 hunks 直接传进 commit
    4. 累积到 user_preferences.json（按类型分桶 + 计数）
    5. 当某种"AI 写法 → 用户改写"模式被命中 ≥3 次，自动加 anti_patterns.json

CLI 子命令：
    diff <ai-draft> <final-text>
        → 输出 hunks JSON 给调方喂 LLM 分类
    commit --classified <classified-hunks.json> --chapter <N>
        → 把已分类 hunks 累积到 user_preferences.json
    show
        → 打印当前 user_preferences.json
"""

from __future__ import annotations

import argparse
import difflib
import json
import re
import sys
import time
from dataclasses import dataclass, asdict, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional


VALID_CATEGORIES = {"factual", "style", "preference", "typo"}


@dataclass
class DiffHunk:
    """单个 diff hunk"""
    hunk_idx: int
    op: str             # 'replace' | 'delete' | 'insert'
    ai_text: str        # 原稿对应文本（"" 当 insert）
    final_text: str     # 精修对应文本（"" 当 delete）
    context_before: str = ""  # 前一句作为上下文
    context_after: str = ""   # 后一句作为上下文

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class ClassifiedHunk:
    """LLM 分类后的 hunk"""
    hunk_idx: int
    op: str
    ai_text: str
    final_text: str
    category: str       # factual | style | preference | typo
    rationale: str      # LLM 给的分类理由
    confidence: float = 1.0  # LLM 置信度

    def to_dict(self) -> dict:
        return asdict(self)


# ===== 句级切分 =====

SENTENCE_SPLIT_RE = re.compile(r"([。！？!?\n]+)")


def split_sentences(text: str) -> List[str]:
    """按句切分（保留分隔符在前一个句子末尾）"""
    parts = SENTENCE_SPLIT_RE.split(text or "")
    sentences: List[str] = []
    buf = ""
    for p in parts:
        if not p:
            continue
        if SENTENCE_SPLIT_RE.match(p):
            buf += p
            if buf.strip():
                sentences.append(buf.strip())
            buf = ""
        else:
            if buf.strip():
                sentences.append(buf.strip())
                buf = ""
            buf = p
    if buf.strip():
        sentences.append(buf.strip())
    return sentences


# ===== diff 计算 =====

def compute_hunks(ai_draft: str, final_text: str) -> List[DiffHunk]:
    """计算句级 diff hunks"""
    ai_sents = split_sentences(ai_draft)
    final_sents = split_sentences(final_text)

    matcher = difflib.SequenceMatcher(None, ai_sents, final_sents, autojunk=False)
    hunks: List[DiffHunk] = []

    for idx, (op, i1, i2, j1, j2) in enumerate(matcher.get_opcodes()):
        if op == "equal":
            continue
        ai_chunk = "".join(ai_sents[i1:i2]).strip() if op != "insert" else ""
        final_chunk = "".join(final_sents[j1:j2]).strip() if op != "delete" else ""
        if not ai_chunk and not final_chunk:
            continue
        # 上下文：前后各 1 句
        ctx_before = ai_sents[i1 - 1] if i1 > 0 else (final_sents[j1 - 1] if j1 > 0 else "")
        ctx_after = ai_sents[i2] if i2 < len(ai_sents) else (final_sents[j2] if j2 < len(final_sents) else "")
        hunks.append(DiffHunk(
            hunk_idx=idx,
            op=op,
            ai_text=ai_chunk[:500],
            final_text=final_chunk[:500],
            context_before=ctx_before[:200],
            context_after=ctx_after[:200],
        ))

    return hunks


# ===== preferences 累积 =====

def _read_json_safe(path: Path) -> Any:
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _normalize_preferences(payload: Any) -> Dict[str, Any]:
    """归一化 preferences.json schema"""
    if not isinstance(payload, dict):
        return {
            "version": 1,
            "buckets": {"style": [], "preference": [], "factual": [], "typo": []},
            "summary": {"total_revisions": 0, "by_category": {}},
            "updated_at": "",
        }
    payload.setdefault("version", 1)
    payload.setdefault("buckets", {})
    payload.setdefault("summary", {"total_revisions": 0, "by_category": {}})
    for cat in ("style", "preference", "factual", "typo"):
        payload["buckets"].setdefault(cat, [])
    return payload


def commit_classified_hunks(
    project_root: Path,
    chapter: int,
    classified: List[ClassifiedHunk],
) -> Dict[str, Any]:
    """把已分类的 hunks 提交到 user_preferences.json，并触发 anti_patterns 联动"""
    prefs_path = project_root / ".story-system" / "user_preferences.json"
    prefs = _normalize_preferences(_read_json_safe(prefs_path))

    appended_counts: Dict[str, int] = {}
    for h in classified:
        if h.category not in VALID_CATEGORIES:
            continue
        bucket = prefs["buckets"][h.category]
        bucket.append({
            "chapter": chapter,
            "hunk_idx": h.hunk_idx,
            "op": h.op,
            "ai_text": h.ai_text[:300],
            "final_text": h.final_text[:300],
            "rationale": h.rationale[:200],
            "confidence": float(h.confidence),
            "added_at": datetime.now().isoformat(timespec="seconds"),
        })
        appended_counts[h.category] = appended_counts.get(h.category, 0) + 1

    # 更新 summary
    prefs["summary"]["total_revisions"] = prefs["summary"].get("total_revisions", 0) + sum(appended_counts.values())
    by_cat = prefs["summary"].setdefault("by_category", {})
    for cat, n in appended_counts.items():
        by_cat[cat] = by_cat.get(cat, 0) + n
    prefs["updated_at"] = datetime.now().isoformat(timespec="seconds")

    _write_json(prefs_path, prefs)

    # 触发 anti_patterns 联动：style/preference 类的"AI 写法→用户改写"被同类命中 ≥3 次时
    anti_added = _maybe_promote_to_anti_patterns(project_root, prefs, chapter)

    return {
        "prefs_path": str(prefs_path),
        "appended_counts": appended_counts,
        "total_revisions": prefs["summary"]["total_revisions"],
        "anti_patterns_added": anti_added,
    }


def _maybe_promote_to_anti_patterns(
    project_root: Path,
    prefs: Dict[str, Any],
    chapter: int,
    threshold: int = 3,
) -> int:
    """style/preference 类的同样 ai_text 被改写 ≥threshold 次 → 写入 anti_patterns.json"""
    ap_path = project_root / ".story-system" / "anti_patterns.json"
    existing = _read_json_safe(ap_path) or []
    if not isinstance(existing, list):
        existing = []

    seen_texts = {str(item.get("text") or "").strip() for item in existing if isinstance(item, dict)}
    additions: List[Dict[str, Any]] = []

    for cat in ("style", "preference"):
        bucket = prefs["buckets"].get(cat, [])
        # 按 ai_text 前 60 字归并计数
        counter: Dict[str, List[Dict[str, Any]]] = {}
        for item in bucket:
            key = (item.get("ai_text") or "").strip()[:60]
            if not key:
                continue
            counter.setdefault(key, []).append(item)
        for key, items in counter.items():
            if len(items) < threshold:
                continue
            text = key
            if text in seen_texts:
                continue
            seen_texts.add(text)
            # 取最新一项的改写理由作为 rationale
            latest = max(items, key=lambda x: x.get("added_at", ""))
            additions.append({
                "text": text,
                "source_table": "user_revision_promoted",
                "source_id": f"ch{int(chapter):04d}_{cat}_{len(additions)}",
                "category": "ai_flavor",
                "rationale": latest.get("rationale", "")[:200],
                "added_at": datetime.now().isoformat(timespec="seconds"),
            })

    if additions:
        _write_json(ap_path, [*existing, *additions])

    return len(additions)


def get_high_frequency_preferences(
    project_root: Path,
    min_count: int = 2,
    max_per_bucket: int = 5,
) -> Dict[str, List[Dict[str, Any]]]:
    """读取 user_preferences.json，返回每个 bucket 中出现次数 ≥ min_count 的高频偏好

    供 context-agent 写任务书第 4 段时 inject。
    """
    prefs_path = project_root / ".story-system" / "user_preferences.json"
    prefs = _normalize_preferences(_read_json_safe(prefs_path))

    result: Dict[str, List[Dict[str, Any]]] = {}
    for cat in ("style", "preference"):
        bucket = prefs["buckets"].get(cat, [])
        # 按 ai_text 归并
        groups: Dict[str, List[Dict[str, Any]]] = {}
        for item in bucket:
            key = (item.get("ai_text") or "").strip()[:60]
            if not key:
                continue
            groups.setdefault(key, []).append(item)
        # 取出 count >= min_count 的
        high_freq = []
        for key, items in groups.items():
            if len(items) < min_count:
                continue
            latest = max(items, key=lambda x: x.get("added_at", ""))
            high_freq.append({
                "ai_text": key,
                "final_text": latest.get("final_text", ""),
                "rationale": latest.get("rationale", ""),
                "count": len(items),
            })
        # 按 count 倒序
        high_freq.sort(key=lambda x: -x["count"])
        result[cat] = high_freq[:max_per_bucket]

    return result


# ===== CLI =====

def _cmd_diff(args):
    ai = Path(args.ai_draft).read_text(encoding="utf-8")
    final = Path(args.final_text).read_text(encoding="utf-8")
    hunks = compute_hunks(ai, final)
    out = {
        "ai_draft_path": str(Path(args.ai_draft).resolve()),
        "final_text_path": str(Path(args.final_text).resolve()),
        "total_hunks": len(hunks),
        "hunks": [h.to_dict() for h in hunks],
    }
    if args.output:
        Path(args.output).write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    else:
        print(json.dumps(out, ensure_ascii=False, indent=2))


def _cmd_commit(args):
    project_root = Path(args.project_root).expanduser().resolve()
    classified_payload = json.loads(Path(args.classified).read_text(encoding="utf-8"))
    classified: List[ClassifiedHunk] = []
    for h in classified_payload.get("hunks", classified_payload):
        if isinstance(h, dict):
            classified.append(ClassifiedHunk(
                hunk_idx=int(h.get("hunk_idx", 0)),
                op=h.get("op", "replace"),
                ai_text=h.get("ai_text", ""),
                final_text=h.get("final_text", ""),
                category=h.get("category", "style"),
                rationale=h.get("rationale", ""),
                confidence=float(h.get("confidence", 1.0)),
            ))
    result = commit_classified_hunks(project_root, args.chapter, classified)
    print(json.dumps(result, ensure_ascii=False, indent=2))


def _cmd_show(args):
    project_root = Path(args.project_root).expanduser().resolve()
    prefs = _read_json_safe(project_root / ".story-system" / "user_preferences.json")
    print(json.dumps(prefs or {}, ensure_ascii=False, indent=2))


def _cmd_high_freq(args):
    project_root = Path(args.project_root).expanduser().resolve()
    result = get_high_frequency_preferences(project_root, args.min_count, args.max_per_bucket)
    print(json.dumps(result, ensure_ascii=False, indent=2))


def main():
    parser = argparse.ArgumentParser(description="用户修订差分器")
    parser.add_argument("--project-root", type=str, help="项目根目录")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_diff = sub.add_parser("diff", help="计算 AI 原稿 vs 用户精修的句级 diff")
    p_diff.add_argument("ai_draft", help="AI 原稿文件路径")
    p_diff.add_argument("final_text", help="用户精修稿文件路径")
    p_diff.add_argument("--output", "-o", type=str, help="输出 JSON 路径")

    p_commit = sub.add_parser("commit", help="提交已分类的 hunks 到 user_preferences.json")
    p_commit.add_argument("--classified", required=True, help="已分类 hunks 的 JSON 文件")
    p_commit.add_argument("--chapter", type=int, required=True, help="章节号")

    sub.add_parser("show", help="打印当前 user_preferences.json")

    p_hf = sub.add_parser("high-freq", help="提取高频偏好（供 context-agent 注入）")
    p_hf.add_argument("--min-count", type=int, default=2)
    p_hf.add_argument("--max-per-bucket", type=int, default=5)

    args = parser.parse_args()

    if args.cmd == "diff":
        _cmd_diff(args)
    elif args.cmd == "commit":
        if not args.project_root:
            print("ERROR: commit 必须指定 --project-root", file=sys.stderr)
            sys.exit(1)
        _cmd_commit(args)
    elif args.cmd == "show":
        if not args.project_root:
            print("ERROR: show 必须指定 --project-root", file=sys.stderr)
            sys.exit(1)
        _cmd_show(args)
    elif args.cmd == "high-freq":
        if not args.project_root:
            print("ERROR: high-freq 必须指定 --project-root", file=sys.stderr)
            sys.exit(1)
        _cmd_high_freq(args)


if __name__ == "__main__":
    main()
