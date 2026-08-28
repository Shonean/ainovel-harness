# -*- coding: utf-8 -*-
"""验证砍掉 verify_ladder 后 extract_from_doc 行为正确（不调 verify、达标章合格）。
patch chat_completion 为 mock（所有 LLM 入口），零网络、零真实 token。
运行：python verify_no_verify.py
"""
import asyncio
import json
import sys

sys.path.insert(0, ".")


def check(name, cond, detail=""):
    print(("PASS " if cond else "FAIL ") + name + (f" | {detail}" if detail else ""))
    return cond


async def main() -> int:
    ok = True
    import prompt_harness.plot_library as pl
    import prompt_harness.llm_client as lc

    # ── mock chat_completion：按 call_type 返回对应 JSON ──
    seen_calls = []

    async def _fake_chat(**kwargs):
        ct = kwargs.get("call_type", "")
        seen_calls.append(ct)
        if ct == "beat_coverage":
            content = '{"coverage": 0.75, "rebuildable": true, "missing": []}'
        elif ct == "ladder_style_role":
            content = '{"style": "轻松修仙", "role_setting": "主角是灵农"}'
        elif ct == "ladder_l1_minimal":
            content = "一句话极简剧情"
        elif ct == "ladder_l2_arc":
            content = '{"summary": "弧线概要"}'
        elif ct == "ladder_l3_chapter":
            content = '{"title": "第1章 修炼", "core": "主角修炼遇阻", "beats": ["修炼", "遇敌"]}'
        elif ct == "ladder_extract_scenes":
            content = json.dumps({"scenes": [{"type": "action", "text": "主角修炼"},
                                             {"type": "dialogue", "text": "遇敌对话"}]})
        elif ct == "arc_merge" or "arc" in ct:
            content = '{"l1": "弧一句话", "l2": "弧概要"}'
        else:
            content = '{"ok": true}'
        return {"content": content, "usage": {"total_tokens": 10}, "error": None}

    lc.chat_completion = _fake_chat

    # 构造最小原文，跑 extract_from_doc（走 mock，不 verify）
    res = await pl.extract_from_doc(
        ["主角方夕在竹林修炼长春诀，遇劫修追杀。",
         "方夕反杀劫修，逃出生天，突破真力境界。"],
        style="", role_setting="", progress=None,
    )
    print("extract total:", res.get("total"), "| qualified:", res.get("qualified"))
    ok &= check("extract 无异常", res.get("total") is not None and res.get("error") is None)

    # 达标章断言
    report = res.get("report") or []
    any_qual = False
    for r in report:
        for c in (r.get("chapters") or []):
            if c.get("qualified"):
                any_qual = True
                ok &= check(f"达标章 score=coverage(0.75)", abs(float(c.get("score")) - 0.75) < 1e-6,
                            f"score={c.get('score')}")
                ok &= check("达标章 prose 为空", c.get("prose") == "")
                ok &= check("达标章 s_char=None（兼容）", c.get("scores", {}).get("s_char") is None)
                ok &= check("达标章有 skeleton", bool(c.get("skeleton", {}).get("l1")))
    ok &= check("有达标章产出", any_qual)
    print("达标章数:", sum(1 for r in report for c in (r.get('chapters') or []) if c.get('qualified')))

    # 确认流程没触发 verify（seen_calls 里没有 rebuild/verify 类）
    rebuild_cts = [c for c in seen_calls if "verify" in c or "rebuild" in c or "prose" in c or "generate" in c]
    ok &= check("流程无 verify/rebuild 类 LLM 调用", not rebuild_cts, f"seen={rebuild_cts}")
    print("流程 LLM 调用类型:", sorted(set(seen_calls)))

    print("\n=== " + ("全部通过" if ok else "有失败项") + " ===")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
