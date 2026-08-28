# -*- coding: utf-8 -*-
"""隔离测试：创作助手 new_arc 工具（书级对话建弧）+ 书级 arc_chat 冒烟。
产出：prompt-harness/harness_runs/arcchat_booklevel/run1_response.json + run1.log
直连 ai_creation._execute_chat_tool("new_arc")，不依赖 LLM 选择工具，确定性验证建弧。
"""
import asyncio, json, sys, pathlib, shutil, datetime

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
OUT = HERE / "harness_runs" / "arcchat_booklevel"
OUT.mkdir(parents=True, exist_ok=True)

from prompt_harness import ai_creation as ac

ROOT = pathlib.Path(r"C:\Users\24357\Desktop\AInovel写作系统\小说系统\_tmp_arcchat_test")
ROOT.mkdir(parents=True, exist_ok=True)
(ROOT / ".ainovel").mkdir(exist_ok=True)


async def main():
    log = []
    def L(s):
        log.append(s)
        print(s)

    ac.save_basic_settings(ROOT, {
        "name": "值夜", "genre": "玄幻武侠",
        "one_liner": "扬州都会妖事频发，夜巡司夜巡方嶂破局",
        "style": "写实冷峻", "role_setting": "方嶂是夜巡司夜巡、前江湖刀客",
    })
    ac.save_elements(ROOT, {
        "characters": [{"id": "c1", "name": "方嶂", "alias": [], "desc": "夜巡司夜巡", "terms": []}],
        "items": [], "settings": [],
    })

    # 1) new_arc 工具（arc=None 书级），确定性验证
    ev, needs_confirm = await ac._execute_chat_tool(
        "new_arc", {"l1": "妖潮漫上黑水渡，方嶂被迫拔刀", "n_chapters": 1},
        ROOT, None, ac.load_elements(ROOT))
    L("EVENT: " + json.dumps(ev, ensure_ascii=False))
    L(f"needs_confirm={needs_confirm}")
    assert ev and ev.get("new_arc_id"), "FAIL: 没有 new_arc_id"
    assert ev.get("failed") is not True, "FAIL: 建弧 failed"
    arcs = ac.load_arcs(ROOT)
    assert len(arcs.get("arcs", [])) == 1, "FAIL: 弧未创建"
    a = arcs["arcs"][0]
    L(f"ARC: id={a['id']} name={a['name']} l1={a['l1']}")
    assert a.get("l1") == "妖潮漫上黑水渡，方嶂被迫拔刀", "FAIL: l1 未写入"
    L("PASS: new_arc 工具建弧 ✓")

    # 2) 同弧内 step 能生成 l2（验证建弧后可推进）
    try:
        step_res = await ac.arc_step(ROOT, a["id"])
        L("STEP: " + json.dumps({k: step_res.get(k) for k in ("ok", "to", "error")}, ensure_ascii=False))
    except Exception as e:
        L(f"STEP exception: {e}")

    # 3) 书级 arc_chat 冒烟（arc_id=''，经 chat_json 真实 LLM；不强求建弧，只验不崩）
    try:
        chat_res = await ac.arc_chat(ROOT, "", [
            {"role": "user", "content": "根据设定，第一个情节的 l1 应该写什么？只给建议，不要建弧。"},
        ])
        L("CHAT_BOOK_LEVEL: " + json.dumps({
            "ok": chat_res.get("ok"), "has_reply": bool(chat_res.get("reply")),
            "new_arc_id": chat_res.get("new_arc_id"),
            "n_events": len(chat_res.get("tool_events") or []),
        }, ensure_ascii=False))
    except Exception as e:
        L(f"CHAT_BOOK_LEVEL exception: {type(e).__name__} {e}")

    with open(OUT / "run1.log", "w", encoding="utf-8") as f:
        f.write("\n".join(log))
    print("LOG ->", OUT / "run1.log")


asyncio.run(main())
shutil.rmtree(ROOT, ignore_errors=True)
print("cleanup done")
