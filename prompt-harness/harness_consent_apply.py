# -*- coding: utf-8 -*-
"""隔离测试：创作助手同意机制 + 弧/章删除。
① modify_level dry_run=True → 提案、内容未改
② modify_level dry_run=False → 真改
③ delete_arc / delete_chapter 正常
产出：prompt-harness/harness_runs/consent_apply/run1.log
"""
import asyncio, json, sys, pathlib, shutil

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
OUT = HERE / "harness_runs" / "consent_apply"
OUT.mkdir(parents=True, exist_ok=True)

from prompt_harness import ai_creation as ac

ROOT = pathlib.Path(r"C:\Users\24357\Desktop\AInovel写作系统\小说系统\_tmp_consent")
shutil.rmtree(ROOT, ignore_errors=True)  # 清上次残留，防污染
ROOT.mkdir(parents=True, exist_ok=True)
(ROOT / ".ainovel").mkdir(exist_ok=True)


async def main():
    log = []
    def L(s):
        log.append(s)
        print(s)

    ac.save_basic_settings(ROOT, {"name": "值夜", "genre": "玄幻武侠", "one_liner": "扬州都会", "style": "写实", "role_setting": "方嶂夜巡"})
    ac.save_elements(ROOT, {"characters": [], "items": [], "settings": []})

    nres = await ac.new_arc(ROOT, l1="方嶂值夜捞尸遇断崖宗标记", n_chapters=2)
    arc_id = nres["arc"]["id"]
    arcs = ac.load_arcs(ROOT)
    full_arc = ac._find_arc(arcs, arc_id)
    els = ac.load_elements(ROOT)

    # ① dry_run=True → 提案、不改内容
    prop, need = await ac._execute_chat_tool("modify_level", {"level": "l1", "instruction": "改成市井酒肆闹剧"}, ROOT, full_arc, els, dry_run=True)
    L("dry_run prop: " + json.dumps(prop, ensure_ascii=False)[:120])
    assert need is True, "FAIL: dry_run 应 needs_confirm"
    assert prop.get("args"), "FAIL: 提案应带 args"
    l1_after_prop = ac.load_arcs(ROOT)["arcs"][0].get("l1")
    assert l1_after_prop == "方嶂值夜捞尸遇断崖宗标记", f"FAIL: dry_run 不应改内容，实际={l1_after_prop}"
    L("PASS: dry_run=True 只提案不改内容 ✓")

    # ② apply（dry_run=False）→ 真执行工具（内容改动由 LLM 输出，不定；事件必须成功）
    ev2, _ = await ac._execute_chat_tool("modify_level", {"level": "l1", "instruction": "改成市井酒肆闹剧"}, ROOT, full_arc, els, dry_run=False)
    L("apply event: " + json.dumps(ev2, ensure_ascii=False)[:80])
    assert ev2 and not ev2.get("failed"), f"FAIL: apply 未成功执行，ev2={ev2}"
    assert ev2.get("arc_changed"), "FAIL: apply 应标记 arc_changed"
    L("PASS: dry_run=False 真执行工具（arc_changed=True）✓")

    # ③ delete_arc
    da = ac.delete_arc(ROOT, arc_id)
    L("delete_arc: " + json.dumps(da, ensure_ascii=False))
    assert da.get("ok"), f"FAIL delete_arc: {da}"
    assert len(ac.load_arcs(ROOT).get("arcs", [])) == 0, "FAIL: 弧未删干净"
    L("PASS: delete_arc ✓")

    # ④ delete_chapter（另建一弧 + 直接造章再删）
    n2 = await ac.new_arc(ROOT, l1="测试删除章", n_chapters=2)
    aid2 = n2["arc"]["id"]
    arcs2 = ac.load_arcs(ROOT)
    tarc = ac._find_arc(arcs2, aid2)
    tarc.setdefault("state", {}).setdefault("levels", {})["l3"] = {"chapters": [{"title": "第一章", "core": "核心A"}, {"title": "第二章", "core": "核心B"}]}
    ac.save_arcs(ROOT, arcs2)
    chs = ac.load_arcs(ROOT)["arcs"][0]["state"]["levels"]["l3"].get("chapters") or []
    L("chapters: " + str(len(chs)))
    dc = ac.delete_chapter(ROOT, aid2, 0)
    L("delete_chapter: " + json.dumps({k: dc.get(k) for k in ("ok", "active_chapter")}))
    assert dc.get("ok"), f"FAIL delete_chapter: {dc}"
    assert len(dc.get("chapters") or []) == 1, "FAIL: 章未删干净"
    L("PASS: delete_chapter ✓")

    with open(OUT / "run1.log", "w", encoding="utf-8") as f:
        f.write("\n".join(log))
    print("LOG ->", OUT / "run1.log")


asyncio.run(main())
shutil.rmtree(ROOT, ignore_errors=True)
print("cleanup done")
