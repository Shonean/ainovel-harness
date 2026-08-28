# -*- coding: utf-8 -*-
"""隔离测试：创作助手「🌐 联网搜索」全局开关（AnySearch 自发模式）。
web_search=True → 任意消息（len≥8）触发自发预检索（web 优先）；web_search=False → 非关键词消息不预检索。
产出：prompt-harness/harness_runs/websearch_auto/run1.log
"""
import asyncio, json, sys, pathlib, shutil

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
OUT = HERE / "harness_runs" / "websearch_auto"
OUT.mkdir(parents=True, exist_ok=True)

from prompt_harness import ai_creation as ac

ROOT = pathlib.Path(r"C:\Users\24357\Desktop\AInovel写作系统\小说系统\_tmp_websearch_auto")
ROOT.mkdir(parents=True, exist_ok=True)
(ROOT / ".ainovel").mkdir(exist_ok=True)

MSG = "写一段扬州漕运码头早晨的市井描写"  # 不含检索关键词 → 仅 web_search=True 才应预检索


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

    # 1) web_search=True：应触发自发预检索
    res = await ac.arc_chat(ROOT, "", [{"role": "user", "content": MSG}], web_search=True)
    evs = res.get("tool_events") or []
    L("web_search=True  events:")
    for ev in evs:
        L("  " + str(ev.get("summary"))[:80])
    auto = [e for e in evs if "自发联网检索" in str(e.get("summary"))]
    assert auto, f"FAIL: web_search=True 未触发自发预检索（events={[e.get('summary') for e in evs]}）"
    L("PASS: web_search=True 自发预检索 ✓ (命中 " + str(len(evs)) + " 事件)")
    L("reply_head: " + (res.get("reply") or "")[:50].replace("\n", " "))

    # 2) web_search=False：同消息（无检索关键词）不应预检索
    res2 = await ac.arc_chat(ROOT, "", [{"role": "user", "content": MSG}], web_search=False)
    evs2 = res2.get("tool_events") or []
    L("web_search=False events:")
    for ev in evs2:
        L("  " + str(ev.get("summary"))[:80])
    assert not any("预检索" in str(e.get("summary")) for e in evs2), "FAIL: web_search=False 却预检索了"
    L("PASS: web_search=False 非关键词消息不预检索 ✓")

    with open(OUT / "run1.log", "w", encoding="utf-8") as f:
        f.write("\n".join(log))
    print("LOG ->", OUT / "run1.log")


asyncio.run(main())
shutil.rmtree(ROOT, ignore_errors=True)
print("cleanup done")
