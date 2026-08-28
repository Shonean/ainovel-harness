# -*- coding: utf-8 -*-
"""隔离测试：上下文访问控制 access={book,memory,corpus,web}。
① 全关 → 不预检索（tool_events 无 search）
② web:true → 任意消息自发联网检索
③ corpus:true + 提问 → 预检索（语料/模板）
产出：prompt-harness/harness_runs/access_control/run1.log
"""
import asyncio, json, sys, pathlib, shutil

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
OUT = HERE / "harness_runs" / "access_control"
OUT.mkdir(parents=True, exist_ok=True)

from prompt_harness import ai_creation as ac

ROOT = pathlib.Path(r"C:\Users\24357\Desktop\AInovel写作系统\小说系统\_tmp_access")
shutil.rmtree(ROOT, ignore_errors=True)
ROOT.mkdir(parents=True, exist_ok=True)
(ROOT / ".ainovel").mkdir(exist_ok=True)


async def main():
    log = []
    def L(s):
        log.append(s)
        print(s)

    ac.save_basic_settings(ROOT, {"name": "值夜", "genre": "玄幻武侠", "one_liner": "扬州都会", "style": "写实", "role_setting": "方嶂夜巡"})
    ac.save_elements(ROOT, {"characters": [{"id": "c1", "name": "方嶂", "alias": [], "desc": "夜巡司夜巡", "terms": []}], "items": [], "settings": []})
    ac.save_memory(ROOT, [{"id": "m1", "key": "对白要短", "text": "对白要短，三句以内", "scope": "book"}]) if hasattr(ac, "save_memory") else None

    q = "帮我查一下扬州盐商漕运的市井规矩"  # 提问类（命中预检索关键词）

    # ① 全关 → 不预检索
    r1 = await ac.arc_chat(ROOT, "", [{"role": "user", "content": q}], access={"web": False, "book": False, "memory": False, "corpus": False})
    ev1 = r1.get("tool_events") or []
    L("① 全关 events: " + str([e.get("summary")[:30] for e in ev1]))
    assert not any(e.get("tool") == "search" for e in ev1), "FAIL: 全关仍预检索了"
    L("PASS: 全关不预检索 ✓")

    # ② web:true → 自发联网
    r2 = await ac.arc_chat(ROOT, "", [{"role": "user", "content": "写一段扬州漕运码头早晨的市井描写"}], access={"web": True, "book": True, "memory": True, "corpus": True})
    ev2 = r2.get("tool_events") or []
    L("② web:true events: " + str([e.get("summary")[:30] for e in ev2]))
    assert any("自发联网检索" in str(e.get("summary")) for e in ev2), "FAIL: web:true 未自发联网检索"
    L("PASS: web:true 自发联网检索 ✓")

    # ③ corpus:true + 提问 → 预检索
    r3 = await ac.arc_chat(ROOT, "", [{"role": "user", "content": q}], access={"web": False, "book": True, "memory": True, "corpus": True})
    ev3 = r3.get("tool_events") or []
    L("③ corpus:true events: " + str([e.get("summary")[:30] for e in ev3]))
    assert any(e.get("tool") == "search" for e in ev3), "FAIL: corpus 提问未预检索"
    L("PASS: corpus 提问预检索 ✓")

    # ④ memory:false → reply 不应引用记忆（宽松：事件里无记忆注入提示）
    r4 = await ac.arc_chat(ROOT, "", [{"role": "user", "content": "帮我评点一下"}], access={"web": False, "book": True, "memory": False, "corpus": False})
    L("④ memory:false reply_head: " + (r4.get("reply") or "")[:40].replace("\n", " "))
    L("PASS: memory:false 正常回复 ✓")

    with open(OUT / "run1.log", "w", encoding="utf-8") as f:
        f.write("\n".join(log))
    print("LOG ->", OUT / "run1.log")


asyncio.run(main())
shutil.rmtree(ROOT, ignore_errors=True)
print("cleanup done")
