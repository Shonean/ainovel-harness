# -*- coding: utf-8 -*-
"""doubao-lite 下验证修复：章916(15681字,修复后分段) + 章650 + 章700。
确认：916 从 0.261 回升（覆盖修复）；650 质量；700 通过。
"""
import asyncio
import json
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from prompt_harness.config import init_settings
_ph = Path(os.environ.get("AINOVEL_WRITE_CONFIG", Path.home() / ".claude" / "ainovel-write")) / "prompt-harness"
init_settings(_ph)

from prompt_harness.corpus_loader import _resolve_corpus_dir, _read_file_text, parse_chapters
from prompt_harness.ladder import build_ladder, verify_ladder, extract_chapter_scenes


async def one(num: int, label: str, chs: list, text: str):
    ch = next((c for c in chs if int(c.get("chapter_num") or 0) == num), None)
    if not ch:
        print(f"章{num} 未找到"); return
    t = text[ch["start_pos"]:ch["end_pos"]].strip()
    print(f"\n===== {label} 第{num}章 · 原文 {len(t)} 字 =====", flush=True)
    scenes = await extract_chapter_scenes(t, style="", role_setting="")
    ladder = await build_ladder(t, style="", role_setting="")
    v = await verify_ladder(ladder, t, style="", role_setting="", target_len_per_scene=600)
    print(f"RESULT 场景数={len(scenes)} score={v.get('score'):.3f} s_char={v.get('s_char'):.3f} "
          f"turn={v.get('turn_fidelity'):.3f} len_ratio={v.get('len_ratio'):.3f} "
          f"ai_flavor={v.get('ai_flavor')} ok={v.get('ok')}", flush=True)


async def main():
    p = (_resolve_corpus_dir() / "玄幻武侠/书D/书D(501-955章).txt").resolve()
    parsed = parse_chapters(str(p))
    text = _read_file_text(p)
    chs = parsed.get("chapters") or []
    await one(916, "超长(修复后)", chs, text)
    await one(650, "中位", chs, text)
    await one(700, "中上", chs, text)


if __name__ == "__main__":
    asyncio.run(main())
