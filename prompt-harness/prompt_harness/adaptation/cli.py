"""改编层 v0 — CLI 与构建编排。

用法（规格 §12）：

    python -m prompt_harness.adaptation build --book <path> --arc <arc_id> [--ending-count 2]
    python -m prompt_harness.adaptation validate --pack <path>
    python -m prompt_harness.adaptation preview --pack <path>

build 编排（数据流见规格 §4）：
    loader → mapper（确定性）→ dialogue 归一（LLM 兜底）→ designer（选择/结局包装）
    → 组装 PackData → validator → ink 导出 → 编译 → 落盘（失败保留 *.failed/）

LLM 调用：v0 至多 3~4 次/包（对白归一 1 次 + 选择 1 次 + 结局 1 次），全部经
llm_hook 走项目现有 llm_client.chat_json；调用数与成本进 validation.stats。
单测注入 mock llm_hook，不触网。
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from .compiler import compile_ink
from . import dialogue as dialogue_mod
from . import designer as designer_mod
from . import mapper as mapper_mod
from .ink_export import collect_flag_names, export_ink
from .loader import AdaptationError, load_snapshot
from .models import (
    PackData, PackFiles, PackGame, PackInfo, PackModes, PackSource, ValidationReport,
)
from .packer import collect_files, make_preview_html, write_pack
from .validator import validate_data


class LlmCounter:
    """本地 LLM 用量计数（hook 闭包累计）。

    说明：llm_client 有 run 级计数器（set_llm_run_id/get_run_usage），但它是全局
    ContextVar——服务端并发场景下 build 覆盖外层 run_id 会互踩统计，故 build_pack
    用本地计数；每次调用的 tokens/cost 仍由 llm_client 日志（call_type=adaptation_*）
    全量记账，两边数字可对账。
    """

    def __init__(self) -> None:
        self.calls = 0
        self.prompt_tokens = 0
        self.completion_tokens = 0
        self.cost_est = 0.0

    def record(self, usage: dict | None, model: str | None) -> None:
        self.calls += 1
        usage = usage or {}
        self.prompt_tokens += int(usage.get("prompt_tokens") or 0)
        self.completion_tokens += int(usage.get("completion_tokens") or 0)
        try:
            from ..llm_client import _estimate_cost_usd
            from ..config import SETTINGS
            self.cost_est += _estimate_cost_usd(model or SETTINGS.ark_model_pro, usage)
        except Exception:
            pass

    def as_stats(self) -> tuple[int, float]:
        return self.calls, round(self.cost_est, 6)


def default_llm_hook(counter: LlmCounter):
    """真实 LLM hook：走项目现有 llm_client.chat_json（同一客户端与 JSON 约定）。"""
    from ..llm_client import chat_json

    async def hook(payload: dict):
        resp = await chat_json(
            system=payload.get("system") or "",
            user=payload.get("user") or "",
            call_type=f"adaptation_{payload.get('task') or 'misc'}",
            temperature=0.3,
            max_tokens=1024,
        )
        counter.record(resp.get("usage"), None)
        if resp.get("error"):
            return None
        return resp.get("data")

    return hook


def _build_pack_data(
    snap, mp, designer, *, pack_id: str, created_at: str,
    validation_warnings: list[dict], llm_calls: int = 0, cost_est: float = 0.0,
) -> PackData:
    """把 mapper/designer 的 dict 中间产物组装成 pydantic PackData。"""
    from .models import Story

    story = mp.story
    node_map = {n["id"]: n for n in story["nodes"]}
    endings = story.pop("endings", [])
    if not endings:
        endings = [n["id"] for n in story["nodes"] if n.get("type") == "ending"]

    # pack.json（§5.2）
    logline = (snap.l2 or (snap.chapter.core if snap.chapter else "") or snap.l1).strip()
    info = PackInfo(
        pack_id=pack_id,
        created_at=created_at,
        source=PackSource(
            book_title=snap.book_title,
            book_root=str(snap.book_root),
            arc_ids=[snap.arc_id],
            chapter_nums=[snap.chapter_num],
        ),
        game=PackGame(
            title=(snap.arc_name
                   or (snap.chapter.title if snap.chapter else "")
                   or snap.book_title),
            logline=logline[:120],
            genre_tags=list(mapper_mod.GENRE_TAGS),
            play_minutes_est=max(5, len(mp.scene_nodes) * 2),
            entry_node=story["start"],
            endings=endings,
        ),
        modes=PackModes(offline=True),
        files=PackFiles(),
    )
    return PackData(
        info=info,
        story=Story(start=story["start"], nodes=story["nodes"]),
        characters={"characters": mp.characters["characters"]},
        world=mp.world,
        assets=mp.assets,
        interaction=mp.interaction,
        llm_zones=mp.llm_zones,
    )


async def build_pack(
    book_root: str | Path,
    arc_id: str,
    ending_count: int = 2,
    llm_hook=None,
) -> dict:
    """构建一个 pack（同步编排，内部 LLM 调用为异步）。

    返回 {"ok", "pack_id", "pack_dir", "validation", "warnings"}；
    ok=false 时中间产物保留在 `<pack_id>.failed/`。
    """
    from .models import ValidationReport

    counter = LlmCounter()
    hook = llm_hook if llm_hook is not None else default_llm_hook(counter)

    # ── 1. loader ──
    snap = load_snapshot(book_root, arc_id)
    data_warnings: list[dict] = list(snap.warnings)

    # ── 2. mapper（确定性） ──
    mp = mapper_mod.map_snapshot(snap)
    data_warnings.extend(mp.warnings)

    # ── 3. dialogue 归一（正则 → 代词 → LLM 批量兜底 → 降级） ──
    char_names = [c["name"] for c in mp.characters.get("characters", [])]
    alias_map = {c["name"]: c.get("aliases") or []
                 for c in mp.characters.get("characters", [])}
    known_names = sorted({*char_names, *(a for v in alias_map.values() for a in v)})
    name_to_id = {c["name"]: c["id"] for c in mp.characters.get("characters", [])}

    node_map_by_id = {n["id"]: n for n in mp.story["nodes"]}
    for node_id, texts in mp.raw_dialogues.items():
        node = node_map_by_id[node_id]
        present_names = [n for n in (
            next((c["name"] for c in mp.characters.get("characters", [])
                  if c["id"] == cid), cid)
            for cid in node.get("present") or []
        )]
        lines, warns = await dialogue_mod.normalize_dialogues(
            texts, present_names, known_names, llm_hook=hook, alias_map=alias_map)
        data_warnings.extend({**w, "node": node_id} for w in warns)
        # 回填：替换该节点 lines 中的未解析 dialogue 行（与 texts 同序）
        it = iter(lines)
        for i, ln in enumerate(node["lines"]):
            if ln.get("kind") == "dialogue":
                r = next(it)
                if r.kind == "dialogue":
                    ln["kind"] = "dialogue"
                    ln["text"] = r.text
                    ln["speaker"] = name_to_id.get(r.speaker, r.speaker) if r.speaker else None
                    ln["expression"] = r.expression or None
                    ln.pop("detail", None)
                else:  # 降级 narration
                    ln["kind"] = "narration"
                    ln["text"] = r.text
                    ln.pop("speaker", None)
                    ln.pop("expression", None)
                    ln.pop("detail", None)
        # 语气词进角色表情候选
        for r in lines:
            if r.kind == "dialogue" and r.expression and r.speaker:
                for c in mp.characters.get("characters", []):
                    if c["name"] == r.speaker and r.expression not in c["assets"]["expressions"]:
                        c["assets"]["expressions"].append(r.expression)

    # ── 4. designer（章末收敛选择 + 弧末双结局） ──
    chapter_core = snap.chapter.core if snap.chapter else ""
    designer = await designer_mod.design_branches(
        conflicts=mp.conflicts, chapter_core=chapter_core, l2_text=snap.l2,
        known_names=[c["name"] for c in mp.characters.get("characters", [])],
        llm_hook=hook, ending_count=ending_count,
    )
    data_warnings.extend(designer.warnings)
    designer_mod.attach_branches(mp, designer, snap.chapter_num, snap.arc_id, mp.llm_zones)

    # ── 5. 组装 + 校验 ──
    pack_id = mapper_mod.compute_pack_id(snap)
    created_at = datetime.now(timezone.utc).isoformat()
    pack = _build_pack_data(
        snap, mp, designer, pack_id=pack_id, created_at=created_at,
        validation_warnings=data_warnings,
    )

    validation = ValidationReport(ok=True)
    for w in data_warnings:
        validation.add_warning(w.get("code") or "WARNING", str(w.get("detail") or ""),
                               w.get("node"))
    spec_report = validate_data(pack)
    validation.errors.extend(spec_report.errors)
    validation.warnings.extend(
        w for w in spec_report.warnings
        if (w.code, w.node, w.detail) not in {(x.code, x.node, x.detail)
                                              for x in validation.warnings})
    validation.stats = spec_report.stats

    # ── 6. ink 导出 + 编译 ──
    char_names = {c.id: c.name for c in pack.characters.characters}
    ink_text = export_ink(pack.story, char_names)
    files_present = {f for f in (
        "pack.json", "story.json", "characters.json", "world.json", "assets.json",
        "interaction.json", "llm_zones.json", "ink/story.ink")}
    compile_result = compile_ink(ink_text)
    story_ink_json = None
    if compile_result.ok:
        story_ink_json = compile_result.story_json
        files_present.add("ink/story.ink.json")
    else:
        for detail in compile_result.error_details(ink_text):
            validation.add_error("INK_COMPILE", detail)
        for w in compile_result.warnings:
            validation.add_warning("INK_COMPILE_WARNING", f"line {w.line}: {w.message}")

    # 文件齐全性复核（含编译产物）
    files_check = validate_data(pack, files_present=sorted(files_present))
    for e in files_check.errors:
        if e.code == "MISSING_FILE":
            validation.add_error(e.code, e.detail, e.node)
    validation.stats.llm_calls, validation.stats.cost_est = counter.as_stats()
    # 重算行/资产统计之外，把 llm 用量带上后 finalize
    validation.finalize()

    # ── 7. 落盘 ──
    preview_html = None
    if story_ink_json is not None:
        preview_html = make_preview_html(
            story_ink_json, collect_flag_names(pack.story))
    files = collect_files(pack, ink_text, story_ink_json, preview_html,
                          validation=validation)

    book_root_path = Path(book_root)
    out_root = book_root_path / ".ainovel" / "adaptation"
    final_dir = out_root / (pack_id if validation.ok else f"{pack_id}.failed")
    write_pack(final_dir, files)

    return {
        "ok": validation.ok,
        "pack_id": pack_id,
        "pack_dir": str(final_dir),
        "failed": not validation.ok,
        "validation": json.loads(validation.model_dump_json()),
        "book_root": str(book_root_path),
        "arc_id": arc_id,
    }


# ============================================================
# CLI
# ============================================================

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m prompt_harness.adaptation",
        description="改编层 v0：把一本书的弧编译为引擎无关的 Adaptation Pack + ink 骨架")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_build = sub.add_parser("build", help="构建 pack")
    p_build.add_argument("--book", required=True, help="书根目录（含 .ainovel/）")
    p_build.add_argument("--arc", required=True, help="弧 id（arcs.json）")
    p_build.add_argument("--ending-count", type=int, default=2, help="结局数（v0 固定 2）")

    p_validate = sub.add_parser("validate", help="校验已落盘 pack")
    p_validate.add_argument("--pack", required=True, help="pack 目录")

    p_preview = sub.add_parser("preview", help="打印预览页路径")
    p_preview.add_argument("--pack", required=True, help="pack 目录")

    args = parser.parse_args(argv)
    if args.cmd == "build":
        try:
            result = asyncio.run(build_pack(args.book, args.arc, args.ending_count))
        except AdaptationError as exc:
            print(f"[adaptation] 构建失败：{exc}", file=sys.stderr)
            return 2
        v = result["validation"]
        print(f"pack_id : {result['pack_id']}")
        print(f"目录    : {result['pack_dir']}")
        print(f"校验    : ok={v['ok']} errors={len(v['errors'])} warnings={len(v['warnings'])}")
        print(f"stats   : {json.dumps(v['stats'], ensure_ascii=False)}")
        for e in v["errors"]:
            print(f"  [E] {e['code']}: {e['detail']}", file=sys.stderr)
        for w in v["warnings"][:10]:
            print(f"  [W] {w['code']}: {w['detail']}")
        return 0 if result["ok"] else 1

    if args.cmd == "validate":
        from .validator import validate_dir
        report = validate_dir(args.pack)
        print(json.dumps(json.loads(report.model_dump_json()), ensure_ascii=False, indent=2))
        return 0 if report.ok else 1

    if args.cmd == "preview":
        path = Path(args.pack) / "preview.html"
        if not path.is_file():
            print(f"预览页不存在：{path}", file=sys.stderr)
            return 1
        print(str(path))
        return 0
    return 2


if __name__ == "__main__":
    sys.exit(main())
