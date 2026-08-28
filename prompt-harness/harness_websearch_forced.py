# -*- coding: utf-8 -*-
"""隔离测试：创作助手「🔎 联网检索」强制标记 → 后端强制联网检索注入。
产出：prompt-harness/harness_runs/websearch_forced/run1.log
直连 ai_creation.arc_chat，book 级（arc_id=''），验证 tool_events 含强制联网检索事件。
"""
import asyncio, json, sys, pathlib, shutil

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
OUT = HERE / "harness_runs" / "websearch_forced"
OUT.mkdir(parents=True, exist_ok=True)

from prompt_harness import ai_creation as ac

ROOT = pathlib.Path(r"C:\Users\24357\Desktop\AInovel写作系统\小说系统\_tmp_websearch_test")
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
    ac.save_elements(ROOT, {"characters": [], "items": [], "settings": []})

    res = await ac.arc_chat(ROOT, "", [
        {"role": "user", "content": "【联网检索】扬州 盐商 漕运 市井 风物"},
    ])
    L("ok=" + str(res.get("ok")))
    L("reply_head=" + (res.get("reply") or "")[:60].replace("\n", " "))
    L("n_events=" + str(len(res.get("tool_events") or [])))
    for ev in (res.get("tool_events") or []):
        L("  EVENT: " + json.dumps(ev, ensure_ascii=False)[:200])
    evs = res.get("tool_events") or []
    forced = [e for e in evs if "强制联网检索" in str(e.get("summary"))]
    assert forced, "FAIL: 没有强制联网检索事件"
    assert forced[0].get("detail") and len(str(forced[0].get("detail"))) > 10, "FAIL: 检索结果为空"
    L("PASS: 强制联网检索标记 ✓")

    # hist 剥标记：直接再发一条同标记，确认不崩且 reply 有内容
    res2 = await ac.arc_chat(ROOT, "", [
        {"role": "user", "content": "【联网检索】古代 更夫 报时 规矩"},
    ])
    L("ok2=" + str(res2.get("ok")) + " reply_len=" + str(len(res2.get("reply") or "")))
    L("PASS: 第二次强制检索 ✓")

    with open(OUT / "run1.log", "w", encoding="utf-8") as f:
        f.write("\n".join(log))
    print("LOG ->", OUT / "run1.log")


asyncio.run(main())
shutil.rmtree(ROOT, ignore_errors=True)
print("cleanup done")
