# -*- coding: utf-8 -*-
"""隔离测试：创作助手行为修复。
① modify_level 等状态工具成功事件含 arc_changed=True（修反复改）
② _strip_discuss_block 剥「讨论对象」注入块 → 预检索 query 干净（修搜索垃圾）
③ 带讨论对象注入块 + web_search → 检索事件 query 不含「【讨论对象：」前缀
产出：prompt-harness/harness_runs/arcchat_behavior/run1.log
"""
import asyncio, json, sys, pathlib, shutil

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
OUT = HERE / "harness_runs" / "arcchat_behavior"
OUT.mkdir(parents=True, exist_ok=True)

from prompt_harness import ai_creation as ac

ROOT = pathlib.Path(r"C:\Users\24357\Desktop\AInovel写作系统\小说系统\_tmp_behavior")
ROOT.mkdir(parents=True, exist_ok=True)
(ROOT / ".ainovel").mkdir(exist_ok=True)


async def main():
    log = []
    def L(s):
        log.append(s)
        print(s)

    # ① _strip_discuss_block
    raw = "【讨论对象：l1 一句话极简】\n旧剧情\n\n用户指令：给我写个更有趣的大众化开篇"
    clean = ac._strip_discuss_block(raw)
    L("strip(discuss块) -> " + repr(clean))
    assert clean == "给我写个更有趣的大众化开篇", f"FAIL strip: {clean}"
    L("PASS: _strip_discuss_block 剥【讨论对象】块 ✓")

    # ② 建一弧 + modify_level 事件 arc_changed
    ac.save_basic_settings(ROOT, {"name": "值夜", "genre": "玄幻武侠", "one_liner": "扬州都会", "style": "写实", "role_setting": "方嶂夜巡"})
    ac.save_elements(ROOT, {"characters": [], "items": [], "settings": []})
    nres = await ac.new_arc(ROOT, l1="方嶂值夜捞尸遇断崖宗标记", n_chapters=1)
    arc = nres["arc"]
    arc_id = arc["id"]
    arcs = ac.load_arcs(ROOT)
    full_arc = ac._find_arc(arcs, arc_id)
    ev, _ = await ac._execute_chat_tool(
        "modify_level", {"level": "l1", "instruction": "改成市井酒肆的荒诞闹剧开篇"},
        ROOT, full_arc, ac.load_elements(ROOT))
    L("modify_level event: " + json.dumps(ev, ensure_ascii=False)[:120])
    assert ev and ev.get("arc_changed") is True, f"FAIL: modify_level 无 arc_changed: {ev}"
    L("PASS: modify_level 事件 arc_changed=True ✓")

    # ③ 带讨论对象注入块 + web_search → 检索 query 干净
    res = await ac.arc_chat(ROOT, arc_id, [
        {"role": "user", "content": raw},
    ], web_search=True)
    evs = res.get("tool_events") or []
    L("chat events:")
    for e in evs:
        L("  " + str(e.get("summary"))[:90])
    search_ev = [e for e in evs if e.get("tool") == "search"]
    if search_ev:
        summ = str(search_ev[0].get("summary"))
        assert "【讨论对象：" not in summ, f"FAIL: 检索 query 含讨论对象前缀: {summ}"
        L("PASS: 检索事件 query 不含【讨论对象：前缀 ✓ (" + summ[:60] + ")")
    else:
        L("（无检索事件——web_search 打开但此消息未触发，需人工核验）")

    with open(OUT / "run1.log", "w", encoding="utf-8") as f:
        f.write("\n".join(log))
    print("LOG ->", OUT / "run1.log")


asyncio.run(main())
shutil.rmtree(ROOT, ignore_errors=True)
print("cleanup done")
