"""压力测试：把草稿书填满超长文本，测前端遮挡/溢出问题。
运行：python _pressure_test.py
"""
import sys, json
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from prompt_harness import ai_creation as ac

BOOK = Path(r"C:\Users\24357\Desktop\AInovel写作系统\小说系统\草稿")
print(f"book: {BOOK}")

LOREM = "玄霜殿前月如霜，剑气横空八万丈。碧落黄泉寻常见，争教生死两茫茫。"
LOREM_LONG = (LOREM * 8) + "……（以下省略三千字，情节转折、人物纠葛、江湖恩怨、庙堂权谋交织在一起，构成了一幅波澜壮阔的时代画卷。）"

# ── 1. 清掉旧弧 ──
reg = ac.load_arcs(BOOK)
reg["arcs"] = []
reg["next_chapter_num"] = 1
ac.save_arcs(BOOK, reg)
print("已清空 arcs")

# ── 2. 建一个超长名字的弧 ──
arc_name = "第三百七十二章 开成五年冬夜大雪陆知砚守孝期满归京途中遇袭于霸陵驿外断魂坡"
result = ac.add_arc(BOOK, arc_name, prev_arc=None)
arc = result["arc"]
arc_id = arc["id"]
print(f"建弧: {arc_id}")

# ── 3. 填满五级阶梯（全部超长文本） ──
# 重新加载最新的 arcs，找到刚建的弧
reg = ac.load_arcs(BOOK)
arc_obj = next(a for a in reg["arcs"] if a["id"] == arc_id)
state = arc_obj.setdefault("state", {})
levels = state.setdefault("levels", {})

levels["l1"] = {
    "text": "陆知砚守孝三年期满归京途中于霸陵驿外断魂坡遭遇不明势力伏击身陷绝境之际得一神秘白衣剑客出手相救",
    "data": None, "confirmed": True, "prompt": "（l1 已确认，作为后续生成的锚点）",
}
levels["l2"] = {
    "text": LOREM_LONG + "\n" + LOREM_LONG + "\n" + LOREM_LONG,
    "data": None, "confirmed": True, "prompt": "",
}
levels["l3"] = {
    "text": "",
    "data": {
        "chapters": [
            {"idx": 0, "title": "第一章 霸陵驿雪夜惊变剑光寒彻九幽黄泉路漫漫",
             "summary": LOREM_LONG, "key_details": "时间：开成五年冬 / 地点：霸陵驿外断魂坡 / 人物：陆知砚、神秘白衣人、黑衣伏击者十二人"},
            {"idx": 1, "title": "第二章 血染征袍故人来相见不相识泪满襟",
             "summary": LOREM_LONG, "key_details": "白衣人身份成谜 / 陆知砚认出玉佩 / 对话暗藏机锋"},
            {"idx": 2, "title": "第三章 京城风云动各方势力暗流涌动山雨欲来风满楼",
             "summary": LOREM_LONG, "key_details": "陈党李党相继出手 / 陆渊称病不出 / 京城局势扑朔迷离"},
        ]
    },
    "confirmed": True, "prompt": "",
    "active_chapter_idx": 0,
}
levels["l4"] = {
    "text": "",
    "scenes": [
        {"idx": 0, "title": "场景一：雪夜孤驿",
         "content": LOREM_LONG + "\n\n" + LOREM_LONG,
         "characters": ["陆知砚", "店小二", "神秘访客"],
         "location": "霸陵驿"},
        {"idx": 1, "title": "场景二：断魂坡伏击",
         "content": LOREM_LONG + "\n\n" + LOREM_LONG,
         "characters": ["陆知砚", "黑衣首领", "十二剑手"],
         "location": "断魂坡"},
    ],
    "confirmed": True, "prompt": "",
}
levels["l5"] = {
    "text": LOREM_LONG * 20 + "\n\n" + LOREM_LONG * 20,
    "scenes": [],
    "confirmed": False, "prompt": "（l5 正文，超长压力测试）",
}
ac.save_arcs(BOOK, reg)
print("已填满 l1-l5 阶梯（超长文本）")

# ── 4. 加超长名字元素 ──
elements = ac.load_elements(BOOK)
elements["characters"] = [
    {"id": "c1", "name": "陆知砚（字子瑜，号梅庵居士，开成二年状元及第，丁母忧守孝三年）",
     "alias": ["子瑜", "陆状元", "梅庵"],
     "desc": "陆家嫡长子，开成二年高中状元，才高八斗学富五车，琴棋书画样样精通，尤擅兵法谋略。为人刚正不阿，胸有丘壑，身在江湖心存魏阙。" + LOREM_LONG,
     "fields": [{"name": "年龄", "value": "二十六岁"}, {"name": "身份", "value": "新科状元/丁忧归京"}, {"name": "武器", "value": "腰间三尺青锋剑，乃其父陆渊所赐，名曰『寒梅』"}, {"name": "武功", "value": "陆家独门『落梅剑法』，共三十六式，最后一式『梅开二度』威震江湖"}],
     "relations": [], "setting_file": ""},
    {"id": "c2", "name": "沈清寒（字无双，号白衣卿相，身份成谜的江湖第一剑客）",
     "alias": ["白衣人", "沈先生", "无双公子"],
     "desc": "神秘白衣剑客，行踪飘忽不定，剑法通神，据传已达剑意通玄之境。与陆家似有旧怨，又似有旧恩，身世扑朔迷离。" + LOREM_LONG,
     "fields": [{"name": "年龄", "value": "不详，约莫三十许"}, {"name": "身份", "value": "江湖第一剑客"}, {"name": "武器", "value": "『霜华』长剑，吹毛断发"}, {"name": "门派", "value": "不详，疑为昆仑一脉"}],
     "relations": [], "setting_file": ""},
    {"id": "c3", "name": "陈太傅（陈玄礼，字公明，当朝太傅，陈党领袖，三朝元老）",
     "alias": ["陈相", "陈太傅", "陈阁老"],
     "desc": "陈党领袖，三朝元老，门生故吏遍天下。城府极深，喜怒不形于色。与李党相争十余年，朝野上下无人不知无人不晓。" + LOREM_LONG,
     "fields": [{"name": "年龄", "value": "七十有二"}, {"name": "官职", "value": "太子太傅/文渊阁大学士"}, {"name": "派系", "value": "陈党领袖"}],
     "relations": [], "setting_file": ""},
    {"id": "c4", "name": "李首辅（李德裕，字文饶，当朝首辅，李党领袖，锐意革新）",
     "alias": ["李相", "李阁老", "文饶先生"],
     "desc": "李党领袖，锐意革新，政绩卓著。性刚直，不阿权贵。与陈党相争，朝野震动。" + LOREM_LONG,
     "fields": [], "relations": [], "setting_file": ""},
]
elements["items"] = [
    {"id": "i1", "name": "寒梅剑（陆家家传宝剑，由天外陨铁锻造而成，吹毛断发削铁如泥）",
     "alias": ["青锋", "寒梅"],
     "desc": "陆家家传宝剑，陆渊年轻时的佩剑，后传于陆知砚。剑身修长，出鞘有寒梅清香，故名寒梅。" + LOREM_LONG,
     "fields": [{"name": "材质", "value": "天外陨铁 + 寒铁混合锻造"}, {"name": "长度", "value": "三尺三寸"}, {"name": "重量", "value": "七斤二两"}],
     "relations": [], "setting_file": ""},
]
elements["settings"] = [
    {"id": "s1", "name": "开成五年冬京城局势（陈李党争白热化，新帝登基在即，各方势力蠢蠢欲动）",
     "terms": ["牛李党争", "甘露之变后遗症", "神策军", "枢密院"],
     "desc": "开成五年冬，皇帝病危，太子未立。陈李二党各拥其主，争储之势愈演愈烈。京城暗流涌动，山雨欲来风满楼。" + LOREM_LONG,
     "fields": [], "relations": [], "setting_file": ""},
]
elements["maps"] = [
    {"id": "m1", "name": "京城布局图（外郭城/皇城/宫城/三大内/十六宅/万年县/长安县）",
     "layout": [
         {"zone": "宫城", "name": "太极宫/大明宫/兴庆宫", "desc": "三大内，天子所居"},
         {"zone": "皇城", "name": "尚书省/中书省/门下省/六部九寺", "desc": "百官衙署"},
         {"zone": "外郭城", "name": "朱雀大街/东西市/108坊", "desc": "民居商肆"},
     ],
     "desc": "唐代长安城布局，东西一十八里，南北一十五里，周长六十七里。" + LOREM_LONG,
     "fields": [], "relations": [], "setting_file": ""},
]
ac.save_elements(BOOK, elements)
print("已添加超长命名元素 4+1+1+1 = 7 个")

# ── 5. 选元素入白名单 ──
ac.select_elements(BOOK, arc_id, {
    "characters": ["c1", "c2"],
    "items": ["i1"],
    "settings": ["s1"],
})
print("已选元素入白名单")

# ── 6. 加长记忆（book + arc） ──
ac.add_memory(BOOK, "【风格】对白要短而有力，多用短句，少用长句。叙述要克制，不要把心理活动写在明面上，要通过动作和对话侧面表现。叙事节奏要张弛有度，紧张处要密集，舒缓处要有留白。文风追求古意，化用诗词意境，避免现代语汇和网络用语。" + LOREM_LONG, scope="book", key="风格基调")
ac.add_memory(BOOK, "【世界观】陈李党争是贯穿全书的主线。陈党代表门阀世族利益，领袖陈玄礼；李党代表新兴进士阶层利益，领袖李德裕。两党相争四十余年，朝政反复。陆渊早年属李党，后称病退隐，态度暧昧。" + LOREM_LONG, scope="book", key="党争格局")
ac.add_memory(BOOK, "【人物】陆知砚性格外圆内方，表面温润如玉，内心自有主见。不轻易许诺，许诺必践。不结党，不营私，洁身自好。重情义，轻生死。" + LOREM_LONG, scope="book", key="陆知砚人设")
ac.add_memory(BOOK, "本弧注意：霸陵驿这一段要突出『孤绝』的意境。雪夜、孤驿、残灯、独影。对话要少，动作要凝练。伏击的节奏要快——先静，再骤起，再速决。白衣人出场要『未见其人，先闻其剑』。" + LOREM_LONG, scope="arc", arc_id=arc_id, key="本章基调")
ac.add_memory(BOOK, "注意：本章不要让陆知砚直接认出白衣人。要埋下伏笔——玉佩、剑法路数、一句似曾相识的话。留到后面几章再揭晓。" + LOREM_LONG, scope="arc", arc_id=arc_id, key="悬念控制")
print("已添加 5 条超长记忆")

# ── 7. 加更多弧（测试侧栏超长列表） ──
for i in range(8):
    name = f"第{373+i}章 {'这是一个非常非常非常长的情节名字用来测试侧栏会不会溢出或者折行或者被截断啊怎么办呢'.replace('怎么办呢', str(i))}"
    ac.add_arc(BOOK, name, prev_arc=None)
print("已追加 8 个超长命名弧（压测侧栏）")

print("\n✅ 压力测试数据已写入。刷新页面查看遮挡情况。")
print(f"   弧数: {len(ac.load_arcs(BOOK)['arcs'])}")
print(f"   元素: c={len(elements['characters'])}, i={len(elements['items'])}, s={len(elements['settings'])}, m={len(elements.get('maps',[]))}")
print(f"   记忆: {len(ac.load_memory(BOOK))}")
