#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Audit Merger - 4 轨审计输出合并器

把 4 条独立轨道的 audit JSON 合并成一份给 polish 消费的 final_audit.json：
  Track 1: reviewer_results.json    (LLM 审查 6 维 + ai_flavor 5 子维度)
  Track 2: quant_audit.json         (8 项 stylometric + 14 类词库密度)
  Track 3: critic_audit.json        (对抗式 critic-agent 局部窗输出)
  Track 4: lint_audit.json          (deterministic 正则违规)

合并策略：
- 按 location（line_no / window_index）去重
- severity 按 critical > high > hard > medium > low > info 排序
- 同 location 多轨命中 → 标 cross_track=true，权重提升
- 输出 final_audit.json 供 polish 消费

CLI:
    python -m data_modules.audit_merger --project-root <dir> [--persist]
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional


SEVERITY_ORDER = {
    "critical": 6,
    "blocking": 6,
    "high": 5,
    "hard": 4,    # deterministic_lint 全部是 hard
    "medium": 3,
    "low": 2,
    "info": 1,
    "": 0,
}


def _read_json_safe(path: Path) -> Optional[Any]:
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _normalize_severity(s: str) -> str:
    s = (s or "").lower().strip()
    if s in {"critical", "blocking"}:
        return "critical"
    if s == "high":
        return "high"
    if s in {"hard", "h"}:
        return "hard"
    if s == "medium":
        return "medium"
    if s == "low":
        return "low"
    return "info"


def _merge_track1_reviewer(payload: Any) -> List[Dict[str, Any]]:
    """Track 1: reviewer agent 输出（review_results.json）

    schema 兼容：reviewer 返回的 issues 列表，每项含 severity/category/location/description/evidence/fix_hint/blocking
    """
    if not payload or not isinstance(payload, dict):
        return []
    issues = payload.get("issues") or []
    out: List[Dict[str, Any]] = []
    for idx, issue in enumerate(issues):
        if not isinstance(issue, dict):
            continue
        out.append({
            "track": "reviewer",
            "track_idx": idx,
            "severity": _normalize_severity(str(issue.get("severity", "medium"))),
            "category": issue.get("category", "unknown"),
            "location": issue.get("location", ""),
            "description": issue.get("description", ""),
            "evidence": issue.get("evidence", ""),
            "fix_hint": issue.get("fix_hint", ""),
            "blocking": bool(issue.get("blocking", False)),
        })
    return out


def _merge_track2_quant(payload: Any) -> List[Dict[str, Any]]:
    """Track 2: quantitative_audit 输出"""
    if not payload or not isinstance(payload, dict):
        return []
    findings = payload.get("findings") or []
    out: List[Dict[str, Any]] = []
    for idx, f in enumerate(findings):
        if not isinstance(f, dict):
            continue
        out.append({
            "track": "quant",
            "track_idx": idx,
            "severity": _normalize_severity(str(f.get("severity", "medium"))),
            "category": "ai_flavor_quant",
            "location": f.get("metric", ""),
            "description": f.get("description", ""),
            "evidence": " | ".join(f.get("evidence", [])) if isinstance(f.get("evidence"), list) else "",
            "fix_hint": "",
            "blocking": False,
            "metric_code": f.get("code", ""),
            "metric_value": f.get("value"),
            "metric_threshold": f.get("threshold"),
        })
    return out


def _parse_critic_window_text(text: str) -> List[Dict[str, Any]]:
    """从 critic agent 单个窗口的原始输出文本中提取 findings 列表。

    窗口文本可能是:
      - 纯 JSON 数组: [{"evidence_quote": ..., "quirk_type": ...}, ...]
      - markdown 代码块包裹的 JSON: ```json [...] ```
      - 纯文本描述（无 JSON，返回空列表）
    """
    if not text or not text.strip():
        return []
    t = text.strip()
    # 剥离 markdown 代码块标记
    if t.startswith("```"):
        # 找到第一个换行后的内容，以及最后的 ```
        first_nl = t.find("\n")
        last_fence = t.rfind("```")
        if first_nl >= 0:
            t = t[first_nl + 1:]
        if last_fence >= 0 and last_fence > len(t) - 10:
            t = t[:last_fence]
        t = t.strip()
    try:
        parsed = json.loads(t)
        if isinstance(parsed, list):
            return [item for item in parsed if isinstance(item, dict)]
        if isinstance(parsed, dict):
            return [parsed]
    except (json.JSONDecodeError, TypeError):
        pass
    return []


def _merge_track3_critic(payload: Any) -> List[Dict[str, Any]]:
    """Track 3: critic-agent 输出。

    兼容多种格式:
      - 数组: [{"evidence_quote": ..., "quirk_type": ...}, ...]
      - {"findings": [...]}  — 标准 findings 列表
      - {"chapter": X, "windows": [{"window": N, "text": "..."}, ...]}  — workflows 实际输出
    """
    if not payload:
        return []
    raw_findings: List[Dict[str, Any]] = []
    if isinstance(payload, list):
        raw_findings = [item for item in payload if isinstance(item, dict)]
    elif isinstance(payload, dict):
        # 优先取 "findings" key
        if "findings" in payload:
            f = payload["findings"]
            if isinstance(f, list):
                raw_findings = [item for item in f if isinstance(item, dict)]
        # 兼容 "windows" key（workflows.py 中的实际输出格式）
        elif "windows" in payload:
            windows = payload["windows"]
            if isinstance(windows, list):
                for w in windows:
                    if isinstance(w, dict) and "text" in w:
                        raw_findings.extend(_parse_critic_window_text(str(w["text"])))
    out: List[Dict[str, Any]] = []
    for idx, f in enumerate(raw_findings):
        out.append({
            "track": "critic",
            "track_idx": idx,
            "severity": "medium",  # critic 默认 medium，由跨轨命中提升
            "category": "ai_flavor_critic",
            "location": f.get("evidence_quote", ""),
            "description": f.get("rationale", ""),
            "evidence": f.get("evidence_quote", ""),
            "fix_hint": f.get("suggested_rewrite", ""),
            "blocking": False,
            "quirk_type": f.get("quirk_type", ""),
            "quirk_name": f.get("quirk_name", ""),
        })
    return out


def _merge_track4_lint(payload: Any) -> List[Dict[str, Any]]:
    """Track 4: deterministic_lint 输出"""
    if not payload or not isinstance(payload, dict):
        return []
    violations = payload.get("violations") or []
    out: List[Dict[str, Any]] = []
    for idx, v in enumerate(violations):
        if not isinstance(v, dict):
            continue
        out.append({
            "track": "lint",
            "track_idx": idx,
            "severity": "hard",
            "category": "mechanical",
            "location": f"line {v.get('line_no', 0)}",
            "description": v.get("rule_name", ""),
            "evidence": v.get("matched_text", ""),
            "fix_hint": v.get("suggestion", ""),
            "blocking": True,  # hard violation 必须修
            "rule_id": v.get("rule_id", ""),
            "line_no": v.get("line_no", 0),
            "line_content": v.get("line_content", ""),
        })
    return out


def _dedupe_and_rank(all_findings: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """跨轨去重 + 重排"""
    import re

    def _key_of(ev: str) -> str:
        """归一化 evidence 作聚合 key：去标点、去空白、取前 30 字"""
        # 删除常见中英文标点和空白
        normalized = re.sub(r"[\s，。！？、；：""''「」（）()《》【】,.!?;:—…·-]", "", ev)
        return normalized[:30]

    # 用归一化 evidence 文本简单聚合（同样的引文出自不同轨视为同一处）
    by_evidence: Dict[str, List[Dict[str, Any]]] = {}
    for f in all_findings:
        ev = (f.get("evidence") or "").strip()
        if not ev:
            # 没 evidence 的（如 quant 的指标级 finding）单独保留
            by_evidence[f"_unique_{f.get('track')}_{f.get('track_idx')}"] = [f]
            continue
        key = _key_of(ev)
        if not key:
            by_evidence[f"_empty_{f.get('track')}_{f.get('track_idx')}"] = [f]
            continue
        by_evidence.setdefault(key, []).append(f)

    merged: List[Dict[str, Any]] = []
    for key, group in by_evidence.items():
        if len(group) == 1:
            merged.append(group[0])
            continue
        # 跨轨命中：合并成一条，跨轨提升 severity，标 cross_track
        tracks_hit = sorted({g["track"] for g in group})
        # 取 severity 最高的那条作为主体
        primary = max(group, key=lambda g: SEVERITY_ORDER.get(g.get("severity", ""), 0))
        primary = dict(primary)  # 拷贝
        primary["cross_track"] = True
        primary["tracks_hit"] = tracks_hit
        primary["track_count"] = len(tracks_hit)
        # severity 提升一档（但不超过 critical）
        cur = primary["severity"]
        if cur == "medium":
            primary["severity"] = "high"
        elif cur == "low":
            primary["severity"] = "medium"
        elif cur == "high":
            primary["severity"] = "critical"
        # 合并所有 fix_hint
        all_hints = [g.get("fix_hint") for g in group if g.get("fix_hint")]
        if all_hints:
            primary["fix_hint"] = " | ".join(dict.fromkeys(all_hints))  # dedupe 保序
        merged.append(primary)

    # 排序：blocking 优先 → severity 倒序 → cross_track 优先
    merged.sort(
        key=lambda f: (
            -int(bool(f.get("blocking", False))),
            -SEVERITY_ORDER.get(f.get("severity", ""), 0),
            -int(bool(f.get("cross_track", False))),
            -int(f.get("track_count", 1)),
        ),
    )

    return merged


def merge_audits(
    project_root: Path,
    reviewer_path: Optional[Path] = None,
    quant_path: Optional[Path] = None,
    critic_path: Optional[Path] = None,
    lint_path: Optional[Path] = None,
) -> Dict[str, Any]:
    """主入口：合并 4 轨"""
    started_at = time.perf_counter()
    tmp_dir = project_root / ".ainovel" / "tmp"
    reviewer_path = reviewer_path or (tmp_dir / "review_results.json")
    quant_path = quant_path or (tmp_dir / "quant_audit.json")
    critic_path = critic_path or (tmp_dir / "critic_audit.json")
    if not critic_path.is_file():
        # 兼容旧代码（critic_findings.json）和并行运行的旧服务器实例
        legacy_critic = tmp_dir / "critic_findings.json"
        if legacy_critic.is_file():
            critic_path = legacy_critic
    lint_path = lint_path or (tmp_dir / "lint_audit.json")

    reviewer_payload = _read_json_safe(reviewer_path)
    quant_payload = _read_json_safe(quant_path)
    critic_payload = _read_json_safe(critic_path)
    lint_payload = _read_json_safe(lint_path)

    track1 = _merge_track1_reviewer(reviewer_payload)
    track2 = _merge_track2_quant(quant_payload)
    track3 = _merge_track3_critic(critic_payload)
    track4 = _merge_track4_lint(lint_payload)

    all_findings = track1 + track2 + track3 + track4
    merged = _dedupe_and_rank(all_findings)

    # 统计
    severity_counts: Dict[str, int] = {}
    track_counts: Dict[str, int] = {"reviewer": 0, "quant": 0, "critic": 0, "lint": 0}
    blocking_count = 0
    cross_track_count = 0
    for f in merged:
        sev = f.get("severity", "info")
        severity_counts[sev] = severity_counts.get(sev, 0) + 1
        track_counts[f.get("track", "")] = track_counts.get(f.get("track", ""), 0) + 1
        if f.get("blocking"):
            blocking_count += 1
        if f.get("cross_track"):
            cross_track_count += 1

    elapsed_ms = int((time.perf_counter() - started_at) * 1000)

    return {
        "merged_findings": merged,
        "summary": {
            "total": len(merged),
            "by_severity": severity_counts,
            "by_track": track_counts,
            "blocking": blocking_count,
            "cross_track_hits": cross_track_count,
        },
        "tracks_loaded": {
            "reviewer": reviewer_payload is not None,
            "quant": quant_payload is not None,
            "critic": critic_payload is not None,
            "lint": lint_payload is not None,
        },
        "elapsed_ms": elapsed_ms,
    }


# ===== CLI =====

def main():
    parser = argparse.ArgumentParser(description="4 轨审计输出合并器")
    parser.add_argument("--project-root", type=str, required=True)
    parser.add_argument("--reviewer-path", type=str, help="自定义 reviewer 输出路径")
    parser.add_argument("--quant-path", type=str, help="自定义 quant 输出路径")
    parser.add_argument("--critic-path", type=str, help="自定义 critic 输出路径")
    parser.add_argument("--lint-path", type=str, help="自定义 lint 输出路径")
    parser.add_argument("--persist", action="store_true", help="持久化到 .ainovel/tmp/final_audit.json")

    args = parser.parse_args()
    project_root = Path(args.project_root).expanduser().resolve()

    result = merge_audits(
        project_root,
        reviewer_path=Path(args.reviewer_path).expanduser().resolve() if args.reviewer_path else None,
        quant_path=Path(args.quant_path).expanduser().resolve() if args.quant_path else None,
        critic_path=Path(args.critic_path).expanduser().resolve() if args.critic_path else None,
        lint_path=Path(args.lint_path).expanduser().resolve() if args.lint_path else None,
    )

    if args.persist:
        tmp_dir = project_root / ".ainovel" / "tmp"
        tmp_dir.mkdir(parents=True, exist_ok=True)
        out_path = tmp_dir / "final_audit.json"
        out_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"[audit_merger] persisted to {out_path}", file=sys.stderr)

    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
