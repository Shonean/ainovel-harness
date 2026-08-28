"""completion_audit 隔离测试：l4 结构审计 + l5 正文审计 + 三振计数。

直连函数，不起服务、不调 LLM。运行：
    cd prompt-harness && python test_completion_audit.py
"""
import sys
sys.path.insert(0, ".")

from prompt_harness.completion_audit import (
    audit_l4, audit_l5, build_audit_feedback, StrikeCounter,
)

FAILS = []


def check(name, cond):
    print(("PASS" if cond else "FAIL"), name)
    if not cond:
        FAILS.append(name)


def _good_scene():
    return {
        "name": "病房对话",
        "environment": "昏暗的病房，日光灯嗡嗡作响",
        "actions": ["他推开门走到床边", "林川握紧了拳头", "护士换了吊瓶"],
        "dialogues": ["林川低声说：“你还好吗”", "王慧兰轻叹一声：“还活着”",
                      "林川开口：“我会查清楚”"],
        "narration": ["走廊尽头的灯闪了一下", "药味混着消毒水弥漫",
                      "他把通知单折好塞进口袋", "窗外的天还没亮"],
        "psychologies": ["他心里压着一团火", "她不愿让对方担心", "他强压着怒火"],
        "conflicts": ["林川追问真相", "王慧兰回避问题", "两人僵持不下"],
        "details": ["三千万的汇款单", "旧照片边角卷起", "录取通知书"],
    }


def test_l4_ok():
    scenes = [_good_scene(), _good_scene(), _good_scene()]
    aud = audit_l4(scenes, facts=["林川在医院遇见王慧兰"], density=3)
    check("l4 good scene passes", aud["ok"])
    check("l4 no high findings", aud["n_high"] == 0)


def test_l4_no_scene():
    aud = audit_l4([], density=3)
    check("l4 empty fails", not aud["ok"])
    check("l4 empty has high", aud["n_high"] >= 1)
    keys = {f["blocker_key"] for f in aud["findings"]}
    check("l4 empty blocker", "l4:no_scene" in keys)


def test_l4_missing_env():
    s = _good_scene()
    s["environment"] = ""
    aud = audit_l4([s, _good_scene(), _good_scene()], density=3)
    keys = {f["blocker_key"] for f in aud["findings"]}
    check("l4 catches empty environment", any("env_empty" in k for k in keys))


def test_l4_few_leaves():
    s = _good_scene()
    s["dialogues"] = ["只有一句"]
    aud = audit_l4([s, _good_scene(), _good_scene()], density=4)
    check("l4 catches too few dialogues", not aud["ok"])


def test_l4_fact_lost():
    scenes = [_good_scene(), _good_scene(), _good_scene()]
    # 场景里没有「张兰」这个名字
    aud = audit_l4(scenes, facts=["张兰在码头接头"], density=3)
    keys = {f["blocker_key"] for f in aud["findings"]}
    check("l4 catches lost fact name", any(k.startswith("l4:fact_lost:张兰") for k in keys))


def test_l4_placeholder():
    s = _good_scene()
    s["details"] = ["...", "略", "旧照片"]
    aud = audit_l4([s, _good_scene(), _good_scene()], density=3)
    check("l4 flags placeholder", any("placeholder" in f["blocker_key"] for f in aud["findings"]))


def test_l5_length_and_quotes():
    scene = _good_scene()
    short_prose = '他说："你还好吗'  # 含 ASCII 直引号 + 太短
    aud = audit_l5(short_prose, scene, target_len=600)
    keys = {f["blocker_key"] for f in aud["findings"]}
    check("l5 catches short length", "l5:length" in keys)
    check("l5 catches straight quote", "l5:straight_quote" in keys)
    # 直角引号也应被标记
    bp = "他说「你好」，然后沉默地站着，一句话也不再多说。" * 20
    aud2 = audit_l5(bp, scene, target_len=10)
    check("l5 catches bracket quote",
          any(f["blocker_key"] == "l5:bracket_quote" for f in aud2["findings"]))


def test_l5_turn_fidelity():
    scene = _good_scene()
    # 乱序/缺失对白：契约三句只出现一句，且顺序错乱
    prose = "王慧兰轻叹一声：“还活着”。他站在床边没说话。"
    aud = audit_l5(prose, scene, target_len=10, turn_fidelity_thresh=0.85)
    keys = {f["blocker_key"] for f in aud["findings"]}
    check("l5 catches low turn fidelity", "l5:turn_fidelity" in keys)


def test_l5_good_prose_passes():
    scene = _good_scene()
    prose = (
        "林川推开门走到床边，昏暗的病房里日光灯嗡嗡作响。\n"
        "他低声说：“你还好吗”。\n"
        "王慧兰轻叹一声：“还活着”。\n"
        "林川握紧拳头，开口：“我会查清楚”。\n"
        "走廊尽头的灯闪了一下，药味混着消毒水弥漫。他把那张三千万的汇款单"
        "折好塞进口袋，旧照片边角卷起，窗外的天还没亮。他在心里压着一团火，"
        "而她不愿让对方担心，只是回避着关于真相的追问。" * 3
    )
    aud = audit_l5(prose, scene, target_len=200, turn_fidelity_thresh=0.8)
    check("l5 good prose passes high gates", aud["ok_high"])


def test_feedback_builder():
    aud = audit_l4([], density=3)
    fb = build_audit_feedback(aud)
    check("feedback non-empty", bool(fb))
    check("feedback mentions 审计", "完成审计" in fb)
    check("empty feedback for clean audit", build_audit_feedback({"findings": []}) == "")


def test_strike_counter():
    sc = StrikeCounter(cap=3)
    check("strike not blocked initially", not sc.is_blocked("x"))
    check("strike 1 false", not sc.hit("x"))
    check("strike 2 false", not sc.hit("x"))
    check("strike 3 true (blocked)", sc.hit("x"))
    check("strike is_blocked now", sc.is_blocked("x"))
    sc.reset("x")
    check("strike reset clears", not sc.is_blocked("x"))


if __name__ == "__main__":
    test_l4_ok()
    test_l4_no_scene()
    test_l4_missing_env()
    test_l4_few_leaves()
    test_l4_fact_lost()
    test_l4_placeholder()
    test_l5_length_and_quotes()
    test_l5_turn_fidelity()
    test_l5_good_prose_passes()
    test_feedback_builder()
    test_strike_counter()
    print(f"\n{'='*40}\n{len(FAILS)} FAILURES" if FAILS else "\nALL PASS")
    sys.exit(1 if FAILS else 0)
