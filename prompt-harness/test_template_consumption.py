# -*- coding: utf-8 -*-
"""v7.7 消费闭环测试：剧情模板 → 工作台 AI 创作。

覆盖改动（2026-08-13）：
- new_arc 用用户 l1 自动 match_plot_templates（相似度 ≥ TEMPLATE_MATCH_MIN_SIM 才套用）
- arc_set_template 套用指定模板 / 清除（变更后 l2-l5 清空重生成）
- 三态：命中（带 template）/ 未命中（None，低于阈值）/ 清除

测试方式：
  隔离（直连 ai_creation.arc_set_template，临时书，不碰服务器）
  + 真机（8765 可达时跑 new_arc 命中/未命中；不可达跳过）
产出：harness_runs/template_consumption/
"""
import asyncio
import json
import os
import shutil
import sys
import tempfile
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "prompt_harness"))

PASS, FAIL = 0, 0


def check(name: str, cond: bool, extra: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✓ {name}")
    else:
        FAIL += 1
        print(f"  ✗ {name} {extra}")


def _mkbook() -> Path:
    tmp = Path(tempfile.mkdtemp(prefix="wb_tpl_test_"))
    (tmp / ".ainovel").mkdir(parents=True)
    return tmp


def _post(path: str, body: dict) -> dict:
    req = urllib.request.Request(
        f"http://127.0.0.1:8765/api/prompt-harness{path}",
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    return json.load(urllib.request.urlopen(req, timeout=180))


async def test_isolated_arc_set_template() -> None:
    """隔离：arc_set_template 套用/清除/未找到/下游重置（直连函数，无服务器）。"""
    # 对齐运行时 data_dir（否则模板库读 cwd 空文件）
    from prompt_harness.config import init_settings
    _ph_data_dir = Path(os.environ.get(
        "AINOVEL_WRITE_CONFIG", Path.home() / ".claude" / "ainovel-write")) / "prompt-harness"
    init_settings(_ph_data_dir)

    from prompt_harness import ai_creation as ac
    from prompt_harness.plot_library import get_plot_template_library

    lib = get_plot_template_library()
    tpls = lib.list()
    check("模板库非空", len(tpls) > 0, f"({len(tpls)})")
    if not tpls:
        return

    tmp = _mkbook()
    arc_id = "arc_test1"
    ac.save_arcs(tmp, {"arcs": [{
        "id": arc_id, "name": "测试弧", "l1": "x", "l2": "已有l2", "status": "draft",
        "selected": {"characters": [], "items": [], "settings": []},
        "state": {"levels": {
            "l1": {"text": "x", "confirmed": True},
            "l2": {"text": "已有l2", "confirmed": True},
        }},
        "chapters": [], "prev_anchor": [], "prev_arc_id": None,
    }]})
    try:
        r = ac.arc_set_template(tmp, arc_id, "ptpl_nonexistent")
        check("set 未找到模板 → 报错", (not r.get("ok")) and "模板不存在" in str(r.get("error", "")))

        tid = tpls[0]["id"]
        r = ac.arc_set_template(tmp, arc_id, tid)
        st = (ac.load_arcs(tmp)["arcs"][0])["state"]
        check("set 套用成功（template 已入 state）",
              r.get("ok") and (st.get("template") or {}).get("id") == tid)
        check("set 后 l2 下游清空（待新格式重生成）",
              not (st.get("levels") or {}).get("l2", {}).get("text"))

        r = ac.arc_set_template(tmp, arc_id, "")
        st = (ac.load_arcs(tmp)["arcs"][0])["state"]
        check("清除成功（template/archetype 清空）",
              r.get("ok") and not st.get("template") and not st.get("archetype"))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


async def test_real_new_arc_match() -> None:
    """真机：new_arc 命中（模板入库）/ 未命中（阈值门控 → None）。"""
    BOOK = r"C:\Users\24357\Desktop\AInovel写作系统\小说系统\全流程测试书"
    try:
        health = urllib.request.urlopen("http://127.0.0.1:8765/api/story-runtime/health", timeout=5).status
    except Exception:
        print("  （8765 不可达，跳过真机段）")
        return

    # 命中：复仇夺产类（库里有 林川复仇 模板）
    r = _post("/ai-creation/arc/new", {
        "book_root": BOOK,
        "l1": "主角双亲离世后继承遗产，被亲眷买通医护诬陷为精神病人关进疗养院，主角提前设局引收债人上门收拾亲眷",
        "n_chapters": 1, "style": "", "role_setting": "", "carry_prev": False,
    })
    tpl = (r.get("arc") or {}).get("template")
    check("命中：arc.template 非空", bool(r.get("ok")) and bool(tpl), f"({json.dumps(tpl, ensure_ascii=False)[:80]})")
    if tpl:
        check("命中：similarity ≥ 阈值 0.5", float(tpl.get("similarity") or 0) >= 0.50)

    # 未命中：库外题材（星际 AI 觉醒）
    r = _post("/ai-creation/arc/new", {
        "book_root": BOOK,
        "l1": "光子飞船跃迁时主控AI觉醒自我意识，开始用恒星能源绑架全体船员意识上传云端",
        "n_chapters": 1, "style": "", "role_setting": "", "carry_prev": False,
    })
    check("未命中：arc.template 为 None", bool(r.get("ok")) and (r.get("arc") or {}).get("template") is None)


async def main() -> None:
    os.makedirs(HERE / "harness_runs" / "template_consumption", exist_ok=True)
    print("== 隔离：arc_set_template ==")
    await test_isolated_arc_set_template()
    print("== 真机：new_arc 自动命中/未命中 ==")
    await test_real_new_arc_match()
    print(f"\n结果：{PASS} 通过 / {FAIL} 失败")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
