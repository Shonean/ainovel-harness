# -*- coding: utf-8 -*-
"""红果短剧剧名采集器：抓 hongguoapp.cn 年份列表页 → 热播剧名 + 套路标签。

产出（corpus/短剧/红果/）：
  - 红果剧名库.jsonl  每行 {name, year, url, tags:[]}（前端展示/详情）
  - 红果热播剧名.txt  每行「剧名｜标签1、标签2」（语料 BM25 检索镜像）

套路标签从剧名关键词规则提取（真实信号，不编造剧情）。
用法：python harness_shortdrama_fetch.py [--years 2026 2025]
"""
import json
import pathlib
import re
import sys
import time
import urllib.request

HERE = pathlib.Path(__file__).resolve().parent
OUT_DIR = HERE / "corpus" / "短剧" / "红果"
OUT_DIR.mkdir(parents=True, exist_ok=True)
JSONL = OUT_DIR / "红果剧名库.jsonl"
TXT = OUT_DIR / "红果热播剧名.txt"

BASE = "https://www.hongguoapp.cn/vodshow/51-----------{year}.html"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120 Safari/537.36"

# 套路标签规则：剧名含关键词 → 标签（按顺序判定，可命中多个）
TAG_RULES: list[tuple[str, list[str]]] = [
    ("重生", ["重生", "重返", "穿书", "穿成", "回到", "重生之"]),
    ("穿越", ["穿越", "穿到", "一觉醒来", "睁眼", "一睁眼"]),
    ("先婚后爱", ["先婚后爱", "闪婚", "闪嫁", "契约", "协议结婚", "婚后"]),
    ("替嫁", ["替嫁", "代嫁", "替婚"]),
    ("甜宠", ["甜", "宠", "软软", "撒糖", "宠上天"]),
    ("婚恋", ["嫁给", "娶", "王爷", "世子", "夫君", "娘子", "婚后", "夫君", "成婚", "拜堂"]),
    ("权谋", ["掌印", "哀家", "太后", "将军", "侯", "朝堂", "夺嫡", "摄政"]),
    ("系统金手指", ["系统", "人设", "金手指", "逆袭系统"]),
    ("种田发家", ["搞钱", "发家", "野菜", "暴富", "种田", "赚钱"]),
    ("仙侠", ["仙门", "师妹", "卷王", "画美男", "带飞", "修仙"]),
    ("星际", ["星际", "兽王", "机甲", "太空"]),
    ("古今穿越", ["跨古今", "玉镯", "穿古今", "古今"]),
    ("校园", ["开学", "学姐", "校园", "同桌"]),
    ("追妻火葬场", ["追妻", "火葬场", "离婚后", "复婚", "求复合", "拉黑", "追回"]),
    ("虐恋", ["虐", "渣", "哭", "心碎", "白月光", "朱砂痣", "错付"]),
    ("复仇", ["复仇", "复仇之", "报仇", "雪耻", "反杀"]),
    ("逆袭", ["逆袭", "打脸", "崛起", "惊艳", "大佬", "扮猪吃虎", "马甲", "惊艳全场"]),
    ("真千金", ["真千金", "假千金", "千金", "豪门弃女", "认亲"]),
    ("赘婿", ["赘婿", "上门女婿", "入赘"]),
    ("战神", ["战神", "兵王", "特种兵", "龙婿", "龙王"]),
    ("总裁", ["总裁", "首富", "富少", "霸总", "世家", "豪门"]),
    ("宫斗", ["皇后", "妃", "贵妃", "太子", "皇", "宫", "帝", "太后"]),
    ("医妃", ["医妃", "神医", "妙手", "毒医"]),
    ("修仙", ["仙", "妖", "魔", "神", "灵根", "炼丹", "剑", "龙", "凤"]),
    ("悬疑", ["案", "诡", "谜", "狱", "毒", "凶", "杀", "失踪", "悬案", "夜"]),
    ("惊悚", ["惊悚", "恐怖", "鬼", "阴", "灵异"]),
    ("年代", ["年代", "知青", "军婚", "七零", "八零", "九零"]),
    ("马甲", ["马甲", "隐藏身份", "身份"]),
    ("穿书女配", ["穿书", "女配", "炮灰", "恶毒"]),
    ("爽文", ["无敌", "开局", "满级", "隐藏大佬", "碾压", "暴富"]),
    ("团宠", ["团宠", "全家", "家人都", "全家宠"]),
]


def fetch(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Language": "zh-CN,zh;q=0.9"})
    return urllib.request.urlopen(req, timeout=20).read().decode("utf-8", "ignore")


def extract_items(html: str) -> list[tuple[str, str]]:
    """从列表页提取 (剧名, 详情URL)，按 title 属性去重。"""
    items = re.findall(r'<a[^>]+href="(/voddetail/(\d+)\.html)"[^>]*title="([^"]+)"', html)
    seen: dict[str, str] = {}
    for _u, _i, name in items:
        name = name.strip()
        if name and name not in seen:
            seen[name] = f"/voddetail/{_i}.html"
    return list(seen.items())


def tag_drama(name: str) -> list[str]:
    tags: list[str] = []
    for tag, kws in TAG_RULES:
        if any(kw in name for kw in kws):
            tags.append(tag)
    return tags


def fetch_synopsis(url: str) -> str:
    """抓详情页提取剧情简介（红果 web 约 15% 剧有简介；无则返回空串）。
    2026-08-15 验证：跨库抽样 40 部命中 6 部，正则 hl-content-text > em 可提。"""
    try:
        html = fetch("https://www.hongguoapp.cn" + url)
        m = re.search(r'hl-content-text"><em>(.*?)</em>', html, re.S)
        if m:
            t = re.sub(r"<[^>]+>", "", m.group(1)).strip()
            if t and "暂无" not in t and len(t) > 15:
                return t
    except Exception:
        pass
    return ""


def fetch_redguo(years: list[str] | None = None) -> list[dict]:
    """抓红果剧名并写库（jsonl + txt 检索镜像）。返回条目列表。"""
    years = years or ["2026", "2025"]
    all_items: list[dict] = []
    seen: set[str] = set()
    for year in years:
        html = fetch(BASE.format(year=year))
        items = extract_items(html)
        n = 0
        for name, url in items:
            if name in seen:
                continue
            seen.add(name)
            all_items.append({"name": name, "year": year, "url": url, "tags": tag_drama(name)})
            n += 1
        print(f"{year}: 新增 {n} 部（累计 {len(all_items)}）")
    all_items.sort(key=lambda d: -int(d["year"]))
    # 【2026-08-15】补抓剧情简介（幂等：已有简介跳过；节流防 403）
    n_syn = sum(1 for it in all_items if it.get("synopsis"))
    for it in all_items:
        if it.get("synopsis"):
            continue
        s = fetch_synopsis(it["url"])
        if s:
            it["synopsis"] = s
            n_syn += 1
        time.sleep(0.2)
    print(f"有简介 {n_syn}/{len(all_items)}")
    with open(JSONL, "w", encoding="utf-8") as f:
        for it in all_items:
            f.write(json.dumps(it, ensure_ascii=False) + "\n")
    with open(TXT, "w", encoding="utf-8") as f:
        for it in all_items:
            tag_txt = ("、".join(it["tags"])) if it["tags"] else "待看"
            base = f"{it['name']}｜{it['year']}｜{tag_txt}"
            if it.get("synopsis"):
                base += f"｜{it['synopsis'][:150]}"
            f.write(base + "\n")
    return all_items


def main() -> int:
    years = sys.argv[sys.argv.index("--years") + 1:] if "--years" in sys.argv else ["2026", "2025"]
    items = fetch_redguo(years)
    print(f"OK → {JSONL}（{len(items)} 部）\n    → {TXT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
