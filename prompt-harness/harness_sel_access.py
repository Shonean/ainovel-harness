# -*- coding: utf-8 -*-
"""隔离测试：条目级选择性注入 sel_access。
① get_sel_access_options 候选项结构（settings 文档/弧/记忆/corpus/templates）
② arc_chat 带 sel_access 正常返回（勾 settings 文档 + 弧 + 记忆）
③ 弧过滤：勾选弧A，弧B 对话不注入其内容（模型收到占位，不报错）
产出：prompt-harness/harness_runs/sel_access/run1.log
"""
import asyncio, json, sys, pathlib, shutil

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
OUT = HERE / "harness_runs" / "sel_access"
OUT.mkdir(parents=True, exist_ok=True)

from prompt_harness import ai_creation as ac

ROOT = pathlib.Path(r"C:\Users\24357\Desktop\AInovel写作系统\小说系统\_tmp_selaccess")
shutil.rmtree(ROOT, ignore_errors=True)
(ROOT / "设定集").mkdir(parents=True, exist_ok=True)


async def main():
    log = []
    def L(s):
        log.append(s)
        print(s)

    ac.save_basic_settings(ROOT, {"name": "值夜", "genre": "玄幻武侠", "one_liner": "扬州都会", "style": "写实", "role_setting": "方嶂夜巡"})
    ac.save_elements(ROOT, {
        "characters": [{"id": "c1", "name": "方嶂", "alias": [], "desc": "夜巡司夜巡", "terms": []}],
        "items": [], "settings": [{"id": "s1", "name": "扬州城", "alias": [], "desc": "运河都会，漕船连夜", "terms": []}]})
    ac.save_memory(ROOT, [{"id": "m1", "key": "对白要短", "text": "对白要短，三句以内", "scope": "book"}])
    (ROOT / "设定集" / "世界观.md").write_text("夜巡司乃皇命暴力机构，掌缉妖，驻扬州。", encoding="utf-8")
    # 两个弧
    r1 = await ac.new_arc(ROOT, l1="方嶂在灶下酒肆发现运河浮尸", n_chapters=1)
    r2 = await ac.new_arc(ROOT, l1="扬州花楼夜半传摄魂曲", n_chapters=1)
    arc_a = r1["arc"]; arc_b = r2["arc"]

    # ① 候选项结构
    opts = ac.get_sel_access_options(ROOT)
    L("① settings: " + str(opts["settings"]))
    L("① arcs: " + str([a["name"] for a in opts["arcs"]]))
    L("① memory: " + str([m["key"] for m in opts["memory"]]))
    L("① corpus: " + str(opts["corpus"][:1]))
    L("① templates: " + str(len(opts["templates"])))
    assert "世界观" in opts["settings"] and "扬州城" in opts["settings"], "FAIL: settings 候选项缺文档/元素设定"
    assert len(opts["arcs"]) == 2, "FAIL: arcs 候选项应 2 个"
    assert opts["memory"] and opts["memory"][0]["key"] == "对白要短", "FAIL: memory 候选项异常"
    L("PASS: ① 候选项结构 ✓")

    # ② 带 sel_access（勾 settings 文档 + 记忆 + 弧A）书级对话
    q = "帮我评点一下当前的设定"
    rr = await ac.arc_chat(ROOT, "", [{"role": "user", "content": q}],
                           access={"web": False, "book": True, "memory": True, "corpus": False},
                           sel_access={"settings": ["世界观"], "memory": ["m1"], "arcs": [arc_a["id"]]})
    L("② ok: " + str(rr.get("ok")) + "  reply_head: " + (rr.get("reply") or "")[:40].replace("\n", " "))
    assert rr.get("ok") is not False, "FAIL: sel_access 书级对话异常"
    L("PASS: ② sel_access 正常执行 ✓")

    # ③ 弧过滤：勾选弧A，对弧B 对话 → 不注入弧B 内容（模型应只收到占位）
    rq = await ac.arc_chat(ROOT, arc_b["id"], [{"role": "user", "content": "这一节讲的是什么？"}],
                           access={"web": False, "book": True, "memory": True, "corpus": False},
                           sel_access={"arcs": [arc_a["id"]]})
    L("③ 弧B sel_access 只勾弧A → ok: " + str(rq.get("ok")) + "  reply_head: " + (rq.get("reply") or "")[:60].replace("\n", " "))
    assert rq.get("ok") is not False, "FAIL: 弧过滤对话异常"
    L("PASS: ③ 弧过滤不报错 ✓")

    with open(OUT / "run1.log", "w", encoding="utf-8") as f:
        f.write("\n".join(log))
    print("LOG ->", OUT / "run1.log")


asyncio.run(main())
shutil.rmtree(ROOT, ignore_errors=True)
print("cleanup done")
