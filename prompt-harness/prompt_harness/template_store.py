# -*- coding: utf-8 -*-
"""Prompt 模板库：从逆向推理优秀候选「存为模板」——AI 自动抽变量槽。

背景：训练模块的目标是找到能复现原文的 prompt，但用户没法直接拿优秀 prompt 写作
（只能给极简剧情）。模板库把每次训练的优秀候选提炼成「带变量槽 {{key}} 的模板」，
供剧情推导模块（derive.py）按剧情树填充，产出一个趋近模板的完整 prompt。

设计要点：
- 存储：SETTINGS.data_dir / "template_library.json"（与 experience_store 同地持久化）。
- 来源：仅从训练提炼（create 时指定 run_file + candidate_idx），不做手动新建/编辑。
- 提炼：调 LLM（chat_json）把候选的 rendered_prompt + user_input 中会随剧情变化
  的部分换成 {{key}} 占位，输出 skeleton + slots。
- 抗 LLM 不稳：slots 与 skeleton 里的 {{key}} 强制对齐（缺槽自动补、冗余槽剔除）。
"""
from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import SETTINGS
from .llm_client import chat_json
from .run_records import read_run

NODE_TYPES = (
    "root_style", "characters", "chapter_core", "arc", "scene",
    "environment", "action", "dialogue", "psychology", "conflict", "detail",
)

PLACEHOLDER_RE = re.compile(r"\{\{\s*([a-zA-Z0-9_]+)\s*\}\}")

# 缺失槽的兜底描述（按 node_type 分类，仅供前端展示）
_SLOT_FALLBACK_DESC = {
    "root_style": "整体风格与基调",
    "characters": "人物设定",
    "chapter_core": "本章核心剧情",
    "arc": "弧线概要（第m-n章=什么剧情、挂哪个原型）",
    "scene": "场景",
    "environment": "环境描写",
    "action": "动作描写",
    "dialogue": "对话内容",
    "psychology": "心理描写",
    "conflict": "冲突事件",
    "detail": "细节锚点",
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _refine_slots(skeleton: str, slots: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """对齐 skeleton 中的 {{key}} 与 slots：剔除冗余、补齐缺失、约束 node_type。"""
    keys_in_skeleton = set(PLACEHOLDER_RE.findall(skeleton or ""))
    cleaned: list[dict[str, Any]] = []
    seen: set[str] = set()
    for s in slots or []:
        if not isinstance(s, dict):
            continue
        key = str(s.get("key", "")).strip()
        if not key or key in seen or key not in keys_in_skeleton:
            continue
        seen.add(key)
        node_type = str(s.get("node_type", ""))
        if node_type not in NODE_TYPES:
            node_type = "detail"
        cleaned.append({
            "key": key,
            "label": str(s.get("label") or key),
            "node_type": node_type,
            "description": str(s.get("description") or _SLOT_FALLBACK_DESC.get(node_type, "待补充")),
        })
    # 补齐 skeleton 里出现但 slots 漏掉的槽
    for key in sorted(keys_in_skeleton):
        if key in seen:
            continue
        node_type = "detail"
        cleaned.append({
            "key": key,
            "label": key,
            "node_type": node_type,
            "description": _SLOT_FALLBACK_DESC[node_type],
        })
    return cleaned


async def _refine_template(sources: list[tuple[str, str]]) -> tuple[str, list[dict[str, Any]]] | None:
    """调 LLM 把一组候选（单个或多个）融合提炼成 {skeleton, slots}。失败返回 None。

    sources: [(rendered_prompt, user_input), ...] —— 多记录融合时传多个候选，
    让 LLM 取共同结构/风格/写作约束提炼一个统一模板（对应「多记录→100 章→1 个 prompt」）。
    """
    system = (
        "你是「Prompt 模板提炼专家」。你的任务是把一组已经训练出、都能复现原文风格的优秀 "
        "prompt 融合提炼成一个可复用的统一模板：找出会随剧情/风格/角色变化的内容，用 "
        "{{key}} 占位（key 用英文小写下划线）；保持不变的部分原样保留。"
    )
    blocks: list[str] = []
    for i, (rp, ui) in enumerate(sources, start=1):
        blocks.append(
            f"【候选 {i} 最小 prompt】\n{(rp or '(无)')}\n\n【候选 {i} 完整指令】\n{(ui or '(无)')}"
        )
    user = (
        "下面是从同一部小说不同章节训练出的 " + str(len(sources)) + " 个优秀候选 prompt，"
        "每个都能复现对应章节的原文风格。请把它们融合提炼成 ONE 个统一模板"
        "（保留共同的结构/风格/对白契约/写作约束，把会随剧情变化的部分替换成 {{key}}）：\n\n"
        + "\n\n".join(blocks)
        + "\n\n请输出模板。要求：\n"
        "1. skeleton 必须是可直接使用的完整 prompt 模板，把一切会随剧情变化的部分都替换成 {{key}}；\n"
        "2. slots 覆盖 skeleton 中出现的全部 {{key}}，不要有多余的槽；\n"
        "3. 严格输出如下 JSON（不要 Markdown 代码块）：\n"
        '{"skeleton": "…", "slots": ['
        '{"key": "plot", "label": "剧情核心", "node_type": "chapter_core", "description": "一句话说明该槽该填什么"}]}\n'
        "其中 node_type 只能取以下之一："
        + "/".join(NODE_TYPES)
        + "。"
    )
    result = await chat_json(
        system=system, user=user, call_type="template_refine", max_tokens=12000,
    )
    if result["error"] or not isinstance(result["data"], dict):
        return None
    data = result["data"]
    skeleton = str(data.get("skeleton") or "").strip()
    if not skeleton:
        return None
    slots = _refine_slots(skeleton, data.get("slots"))
    return skeleton, slots


class TemplateStore:
    """模板库持久化 + CRUD（列表/读取/删除）。create 走 _refine_template。"""

    def __init__(self, path: Path | None = None) -> None:
        # 【v5.33.1】函数内取 SETTINGS：顶层 import 捕获旧对象会误写 cwd
        from .config import SETTINGS as _s
        self.path = path or (_s.data_dir / "template_library.json")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._templates: list[dict[str, Any]] = []
        self._load()

    def _load(self) -> None:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            self._templates = raw.get("templates") if isinstance(raw, dict) else []
        except Exception:
            self._templates = []
        if not isinstance(self._templates, list):
            self._templates = []

    def _save(self) -> None:
        payload = {"templates": self._templates}
        self.path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def list(self) -> list[dict[str, Any]]:
        # 倒序（最新在前）；返回浅拷贝避免外部改动内存态
        return [dict(t) for t in reversed(self._templates)]

    def get(self, template_id: str) -> dict[str, Any] | None:
        for t in self._templates:
            if t.get("id") == template_id:
                return dict(t)
        return None

    def delete(self, template_id: str) -> bool:
        before = len(self._templates)
        self._templates = [t for t in self._templates if t.get("id") != template_id]
        if len(self._templates) != before:
            self._save()
            return True
        return False

    async def create(
        self,
        name: str,
        sources: list[tuple[str, int]],
    ) -> dict[str, Any]:
        """从多个训练的候选融合提炼并入库。返回新模板；LLM 失败抛 RuntimeError。

        sources: [(run_file, candidate_idx), ...]，最多 10 个记录（合计 ≤ 100 章）。
        每个 source 取其候选的 rendered_prompt + user_input，一起喂 LLM 融合成一个模板。
        """
        if not sources:
            raise RuntimeError("未选择任何记录")
        if len(sources) > 10:
            raise RuntimeError(f"最多支持 10 个记录（当前 {len(sources)} 个）")
        prompt_pairs: list[tuple[str, str]] = []
        run_files: list[str] = []
        best_scores: list[float] = []
        for run_file, candidate_idx in sources:
            run = read_run(run_file)
            if run is None:
                raise RuntimeError(f"找不到 run 文件：{run_file}")
            cands = run.get("candidates") or []
            if not 0 <= int(candidate_idx) < len(cands):
                raise RuntimeError(f"候选下标越界：{candidate_idx}（{run_file} 共 {len(cands)} 个）")
            cand = cands[int(candidate_idx)]
            prompt_pairs.append((cand.get("rendered_prompt", ""), cand.get("user_input", "")))
            run_files.append(run_file)
            bs = (run.get("optimization") or {}).get("best_score")
            if isinstance(bs, (int, float)):
                best_scores.append(float(bs))
        refined = await _refine_template(prompt_pairs)
        if refined is None:
            raise RuntimeError("模板提炼失败（LLM 未返回合法 JSON），请重试")
        skeleton, slots = refined
        template = {
            "id": "tpl_" + uuid.uuid4().hex[:12],
            "name": (name or "未命名模板").strip()[:40],
            "source_run": run_files[0],
            "source_runs": run_files,
            "source_count": len(run_files),
            "source_best_score": max(best_scores) if best_scores else None,
            "created_at": _now(),
            "skeleton": skeleton,
            "slots": slots,
        }
        self._templates.append(template)
        self._save()
        return dict(template)

    async def create_single(
        self,
        run_file: str,
        candidate_idx: int,
        name: str,
    ) -> dict[str, Any]:
        """兼容封装：单记录提炼（老调用方 / 老测试）。"""
        return await self.create(name, [(run_file, int(candidate_idx))])


# 模块级单例（init_prompt_harness 时注入）
_template_store: TemplateStore | None = None


def get_template_store() -> TemplateStore:
    global _template_store
    if _template_store is None:
        _template_store = TemplateStore()
    return _template_store


def init_template_store(path: Path | None = None) -> TemplateStore:
    global _template_store
    _template_store = TemplateStore(path)
    return _template_store
