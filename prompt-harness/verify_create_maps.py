"""为《草稿》书创建「京城地图」+「陆府（京城宅邸）」地图卡（kind=map，含 layout 平面结构）。
产出 → harness_runs/map_cards/"""
import json
from pathlib import Path
import requests

BASE = "http://127.0.0.1:8765/api/prompt-harness"
BOOK = r"C:\Users\24357\Desktop\AInovel写作系统\小说系统\草稿"
OUT = Path(__file__).parent / "harness_runs" / "map_cards"
OUT.mkdir(parents=True, exist_ok=True)


def add(kind, name, desc, fields=None, relations=None, layout=None):
    r = requests.post(f"{BASE}/ai-creation/element", json={
        "book_root": BOOK, "kind": kind, "name": name, "desc": desc,
        "fields": fields or [], "relations": relations or [], "layout": layout or [],
    }, timeout=60)
    return r.json().get("card") or {}


def get_elements():
    r = requests.get(f"{BASE}/ai-creation/elements", params={"book_root": BOOK}, timeout=60)
    d = r.json()
    return d.get("data") or d.get("elements") or {}


els = get_elements()
chars = {e["name"]: e["id"] for e in els.get("characters", [])}
print("现有角色卡:", chars)

# 1. 京城地图
cj = add("map", "京城地图",
         "大明京师（架空为「京城」），宫城—皇城—内城—外城四重方城，中轴贯穿。",
         fields=[
             {"name": "整体格局", "value": "宫城—皇城—内城—外城四重方城，平面呈「凸」字"},
             {"name": "中轴线", "value": "永定门→正阳门→大明门→承天门→午门→紫禁城→景山→鼓楼→钟楼"},
             {"name": "内城九门", "value": "正阳/崇文/宣武（南）、朝阳/东直（东）、阜成/西直（西）、安定/德胜（北）"},
             {"name": "皇城四门", "value": "承天、东安、西安、地安"},
             {"name": "官署", "value": "皇城前T形广场：东为六部翰林院，西为五军都督府锦衣卫"},
             {"name": "坊制", "value": "内城二十九坊、外城七坊，五城兵马司管辖"},
         ],
         layout=[
             {"zone": "outer", "name": "外城（南郊）", "desc": "嘉靖后增筑，商贾贫民居此，街巷曲折"},
             {"zone": "inner", "name": "内城", "desc": "官署官员富户云集，东城勋贵宅第尤盛"},
             {"zone": "imperial", "name": "皇城", "desc": "承天东安西安地安四门，官署广场两侧"},
             {"zone": "palace", "name": "宫城·紫禁城", "desc": "皇帝居所，午门金水桥"},
         ])
print("京城地图 id:", cj.get("id"))

# 2. 陆府（京城宅邸）
lf = add("map", "陆府（京城宅邸）",
         "陆氏在京城东城·时雍坊的宅邸，首辅陆渊称病三年居此，是「清流孤臣」的对外壳子，也是密谋的中心。",
         fields=[
             {"name": "位置", "value": "东城·时雍坊，近东安门（官员入宫必经之地）"},
             {"name": "格局", "value": "五进院落，前堂后寝，带东西跨院与宅后小园"},
             {"name": "对外", "value": "二进正厅见客，只露病容"},
             {"name": "密处", "value": "西跨院密室藏密信/旧档"},
             {"name": "陆知砚", "value": "东跨院为回京居所"},
         ],
         relations=[
             {"to_kind": "c", "to_id": chars.get("陆渊", ""), "name": "首辅居所", "mult": "1:1"},
             {"to_kind": "c", "to_id": chars.get("陆知砚", ""), "name": "祖宅居所", "mult": "1:1"},
             {"to_kind": "map", "to_id": cj.get("id", ""), "name": "位于", "mult": "1:1"},
         ],
         layout=[
             {"zone": "gate", "name": "大门·门房", "desc": "广亮大门，陆府脸面；门房两间，下人多在此候"},
             {"zone": "first", "name": "一进院·倒座", "desc": "账房、外客厅、门房所在"},
             {"zone": "second", "name": "二进院·正厅", "desc": "陆渊称病见客处，对外只露病容；东西厢房"},
             {"zone": "third", "name": "三进院·内堂", "desc": "陆渊寝居；西耳房暗通密室"},
             {"zone": "rear", "name": "后罩房·小花园", "desc": "内眷居所与宅后小园"},
             {"zone": "east", "name": "东跨院·陆知砚居所", "desc": "书房+寝居，陆知砚回京住处"},
             {"zone": "west", "name": "西跨院·密室", "desc": "陆渊密议藏信处，表面是废库房"},
         ])
print("陆府 id:", lf.get("id"))

# 验证
els2 = get_elements()
maps = els2.get("maps", [])
print("\nmaps 卡数:", len(maps))
for m in maps:
    print(f" - {m.get('name')} | layout {len(m.get('layout') or [])} 块 | relations {len(m.get('relations') or [])} 条")
    for r in (m.get("relations") or []):
        print(f"     {r.get('name')}→{r.get('to_kind')}:{r.get('to_id')}")

(OUT / "created.json").write_text(json.dumps(maps, ensure_ascii=False, indent=2), encoding="utf-8")
assert len(maps) >= 2, "地图卡未建全"
print("\nPASS: 两张地图卡创建完成")
