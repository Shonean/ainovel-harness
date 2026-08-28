# -*- coding: utf-8 -*-
"""T8 场景决断验证：trend_inject_block 构建 + bridge.step_ladder 注入点（不调 LLM）。
运行：python verify_trend_inject.py
"""
import asyncio
import sys

sys.path.insert(0, ".")


def check(name, cond, detail=""):
    print(("PASS " if cond else "FAIL ") + name + (f" | {detail}" if detail else ""))
    return cond


async def main() -> int:
    ok = True
    from prompt_harness.trend_lib import (
        GENRE_HOOK_TAGS, SCENE_HOOKS, LEVEL_SCENE, trend_inject_block, detect_genre,
    )

    # 1) 各档场景映射
    ok &= check("LEVEL_SCENE 覆盖 l2-l5", set(LEVEL_SCENE) == {"l2", "l3", "l4", "l5"},
                str(LEVEL_SCENE))

    # 2) 题材检测（从 l1 风格文本）
    g = detect_genre("宗门弟子在秘境寻宝，身怀灵根却被人嘲笑的苟道修士")
    ok &= check("detect_genre 命中仙侠", g == "仙侠", f"got={g}")
    g2 = detect_genre("古代宅斗，侯门嫡女被继母欺凌")
    ok &= check("detect_genre 命中古言", g2 == "古言", f"got={g2}")

    # 3) 钩子块构建（有题材有场景）
    b = trend_inject_block("仙侠", "开局")
    ok &= check("仙侠×开局 非空且含钩子", "打脸虐渣" in b and "开局钩子" in b)
    print("  --- 仙侠×开局 ---\n" + b + "\n")

    # 4) 题材/场景任一命中即注入（降级不打扰）；两者全空才为空串
    b0 = trend_inject_block("", "")
    ok &= check("题材+场景全空 → 空串", b0 == "", f"got={b0!r}")
    b1 = trend_inject_block("奇幻", "推进")   # 未收录题材：保留场景钩子
    ok &= check("未收录题材 → 场景钩子仍注入", "推进节奏" in b1, f"got={b1!r}")
    b2 = trend_inject_block("仙侠", "")        # 未知场景：保留题材钩子
    ok &= check("未知场景 → 题材钩子仍注入", "打脸虐渣" in b2, f"got={b2!r}")
    b3 = trend_inject_block("", "开局")         # 无题材：场景钩子仍注入
    ok &= check("空题材 → 场景钩子仍注入", "开局钩子" in b3, f"got={b3!r}")

    # 5) 全题材×全场景都能出块
    for gg in list(GENRE_HOOK_TAGS) + ["仙侠"]:
        for ss in SCENE_HOOKS:
            bb = trend_inject_block(gg, ss)
            ok &= check(f"{gg}×{ss} 非空", bool(bb), bb.replace("\n", " ")[:40])

    # 6) bridge 注入点存在且 thr/trend_note 正确接入
    import prompt_harness.bridge as bridge
    src = open(bridge.__file__, encoding="utf-8").read()
    ok &= check("step_ladder 有 trend_note 计算", "【T8 场景决断】" in src)
    ok &= check("l2 注入 trend_note", src.count('(trend_note if trend_note else "")') >= 2)
    ok &= check("_arc_to_scenes 有 trend_block 参数", "trend_block: str = \"\"" in src)
    ok &= check("_arc_to_scenes 注入【流行钩子参考】", '"【流行钩子参考】\\n" + trend_block' in src)
    ok &= check("l5 core 注入 thb", '【流行钩子参考】\\n" + thb' in src)
    ok &= check("l4 传 trend_block=thb", "trend_block=thb," in src)

    # 7) 语法可编译
    import py_compile
    for f in ("prompt_harness/trend_lib.py", "prompt_harness/bridge.py"):
        try:
            py_compile.compile(f, doraise=True)
            ok &= check(f"编译通过 {f}", True)
        except py_compile.PyCompileError as e:
            ok &= check(f"编译失败 {f}", False, str(e))

    print("\n=== " + ("全部通过" if ok else "有失败项") + " ===")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
