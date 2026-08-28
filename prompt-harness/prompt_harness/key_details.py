# -*- coding: utf-8 -*-
"""关键细节锚点抽取 & 事实一致性评分（v5.10）。

解决的问题：reverse-infer 生成的章节在细节层面偏离原文 ——
橡皮擦标志性细节被重写、角色性别改变、贿赂金额五万缺失、经典对白丢失。

本模块从目标原文抽取"标志性细节"（人物/数字/对白原句/物品），供两处使用：
  1. 生成端注入：把细节拼成【关键细节锚点】区块塞进生成 prompt，
     约束 LLM 在填充正文时保留这些细节（解决"骨架抽象丢细节"）。
  2. 评分端校验：factual_consistency_score 检查候选文本对细节的保留程度，
     作为新增的评分维度（C3）。

纯规则实现：不调 LLM / embedding，零成本。
来源：_proto_extract.py 收敛版（v8），8 轮调参后人物/数字/对白/物品全部命中。
"""
from __future__ import annotations

import re
from collections import Counter
from functools import lru_cache

from .scorer import normalize_text


# ══ 数字抽取 ════════════════════════════════════════════════════════
_CN_NUM = {"零": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5,
           "六": 6, "七": 7, "八": 8, "九": 9}
_CN_MAG = {"十": 10, "百": 100, "千": 1000, "万": 10000, "亿": 100000000}
_NUM_BASE = "零一二两三四五六七八九十"
_NUM_TOKEN_RE = re.compile(
    '([0-9]+(?:\\.[0-9]+)?|[' + _NUM_BASE + '百千万亿]+)(万|千|百|亿|元|块|分|岁|年|月|天|周|层|楼|米|斤|个|张|次|号)?')
_MONEY_UNITS = {"元", "块", "万", "千", "百", "亿"}
_TIME_UNITS = {"岁", "年", "月", "天", "周", "层", "楼"}
_COUNT_UNITS = {"个", "张", "次", "分", "号", "米", "斤"}


def _cn_to_value(s: str) -> int:
    total, section, cur = 0, 0, 0
    for ch in s:
        if ch in _CN_NUM:
            cur = _CN_NUM[ch]
        elif ch in _CN_MAG:
            mag = _CN_MAG[ch]
            if mag >= 10000:
                section = (section + cur) * mag
                cur = 0
            else:
                section += (cur or 1) * mag
                cur = 0
        else:
            break
    return section + cur


def _token_value(s: str) -> float:
    if re.fullmatch(r'[0-9]+(?:\.[0-9]+)?', s):
        return float(s)
    return float(_cn_to_value(s))


def extract_numbers(text: str, cap: int = 8) -> list[dict]:
    """抽取数字锚点。金额/大数（万级）显著度更高，优先保留。"""
    seen = []
    for m in _NUM_TOKEN_RE.finditer(text):
        num, unit = m.group(1), (m.group(2) or "")
        if not num:
            continue
        if num in _CN_NUM and not unit and len(num) == 1:
            continue
        val = _token_value(num)
        if val == 0:
            continue
        if any(abs(o["value"] - val) < 1e-6
               and (o["unit"] == unit or not o["unit"] or not unit)
               for o in seen):
            continue
        seen.append({"text": m.group(0), "value": val, "unit": unit})

    def salience(n: dict) -> float:
        s = 0.0
        last = n["text"][-1]
        if n["unit"] in _MONEY_UNITS or last in "万亿千百":
            s += 3.0
        elif n["unit"] in _TIME_UNITS:
            s += 2.0
        elif n["unit"] in _COUNT_UNITS:
            s += 1.0
        if n["value"] >= 10000:
            s += 2.0
        elif n["value"] >= 1000:
            s += 1.0
        elif n["value"] >= 10:
            s += 0.5
        return s

    seen.sort(key=lambda n: -salience(n))
    kept = []
    for n in seen:
        if any(n["text"] != o["text"] and n["text"] in o["text"] for o in seen):
            continue
        kept.append(n)
        if len(kept) >= cap:
            break
    return kept


# ══ 对白抽取 ════════════════════════════════════════════════════════
_QUOTE_RE = re.compile('[「“"]([^「」“”"]{1,40})[」”"]')
_GENERIC = {"可以", "好的", "行", "对", "嗯", "哦", "啊", "是", "好吧",
            "没问题", "什么", "什么意思", "没有", "你好", "再见", "没事", "怎幺",
            "怎幺了", "等等", "真的假的", "不会吧"}
_CN_DIGIT_CHARS = set("零一二两三四五六七八九十百千万亿")


def extract_dialogues(text: str, cap: int = 12) -> list[str]:
    """抽取对白原句。去泛化短句，保留带"你/我"、否定、疑问、数字的标志句。"""
    found = []
    for m in _QUOTE_RE.finditer(text):
        q = m.group(1).strip().replace("　", "").rstrip("。！？，、；：")
        if not q or len(q) > 14 or len(q) < 2:
            continue
        if not re.search(r'[一-鿿0-9a-zA-Z]', q):
            continue
        if q in _GENERIC:
            continue
        if q not in found:
            found.append(q)

    def salience(q: str) -> float:
        s = max(0.0, 2.5 - len(q) * 0.2)
        if any(ch in "你我" for ch in q):
            s += 1.5
        if "不" in q or "没" in q:
            s += 1.0
        if any(ch in "？!！" for ch in q):
            s += 0.5
        if any(ch in _CN_DIGIT_CHARS for ch in q):
            s += 1.0
        if any(k in q for k in ("爸爸", "妈妈", "爷爷", "奶奶", "哥哥", "姐姐",
                                "弟弟", "妹妹", "病号", "医院")):
            s += 1.0
        return s

    found.sort(key=lambda q: -salience(q))
    return found[:cap]


# ══ 人物抽取 ════════════════════════════════════════════════════════
_SURNAMES = set("赵钱孙李周吴郑王冯陈褚卫蒋沈韩杨朱秦尤许何吕施张孔曹严华金魏陶姜戚谢邹喻柏水窦章云苏潘葛奚范彭郎鲁韦昌马苗凤花方俞任袁柳酆鲍史唐费廉岑薛雷贺倪汤滕殷罗毕郝邬安常乐于时傅皮卞齐康伍余元卜顾孟平黄和穆萧尹姚邵湛汪祁毛禹狄米贝明臧计伏成戴谈宋茅庞熊纪舒屈项祝董梁杜阮蓝闵席季麻强贾路娄危江童颜郭梅盛林刁钟徐邱骆高夏蔡田樊胡凌霍虞万支柯昝管卢莫经房裘缪干解应宗丁宣贲邓郁单杭洪包诸左石崔吉钮龚程嵇邢滑裴陆荣翁荀羊於惠甄曲家封芮羿储靳汲邴糜松井段富巫乌焦巴弓牧隗山谷车侯宓蓬全郗班仰秋仲伊宫宁仇栾暴甘钭厉戎祖武符刘景詹束龙叶幸司韶郜黎蓟薄印宿白怀蒲邰从鄂索咸籍赖卓蔺屠蒙池乔阴鬱胥能苍双闻莘党翟谭贡劳逄姬申扶堵冉宰郦雍却璩桑桂濮牛寿通边扈燕冀郏浦尚农温别庄晏柴瞿阎充慕连茹习宦艾鱼容向古易慎戈廖庾终暨居衡步都耿满弘匡国文寇广禄阙东欧殳沃利蔚越夔隆师巩厍聂晁勾敖融冷訾辛阚那简饶空曾毋沙乜养鞠须丰巢关蒯相查后荆红游竺权逯盖益桓公")
_SURNAMES.discard("那")
_NAME_BLACKLIST = {"房间", "高兴", "黄河", "王国", "花园", "同学", "病人", "家人",
                   "医生", "护士", "老人", "老爷", "老家", "老板", "小孩", "小姐",
                   "少爷", "爷爷", "奶奶", "阿姨", "老师", "朋友", "父母", "父亲",
                   "母亲", "警察", "事情", "时间", "时候", "房子", "楼房", "东西",
                   "国家", "大家", "人家", "孩子", "意思", "结果", "明白", "城市",
                   "银行", "手机", "电视", "消息", "新闻", "网友", "用户", "医院",
                   "病房", "走廊", "大厅", "铁门", "窗户", "爬墙虎", "橡皮擦",
                   "诊断书", "束缚带", "单人床", "精神病", "六楼", "档案袋",
                   "小伙子", "女子", "男人", "女人", "中年人", "年轻人", "小伙",
                   "老太", "老妈", "老爸", "于是", "在于", "关于", "对于", "由于",
                   "小儿", "小说", "小学", "小组", "帅哥", "美女", "姑娘"}
_TITLE_PREFIX = ("同桌", "同学", "医生", "护士", "病人", "老人", "年轻人", "小伙子",
                 "老师", "朋友", "邻居", "叔叔", "婶婶", "爷爷", "奶奶", "父亲",
                 "母亲", "二叔", "二婶", "大哥", "大叔", "小女孩", "小男孩",
                 "中年男人", "中年女人", "老婆", "媳妇", "儿子", "女儿")
_PARTICLES = set("的了了吗呢吧啊呀嘛哦")
_GIVEN_STOP = _PARTICLES | set("你我他她它们这那什怎个种些来去走说道问答看笑叫喊给把被往向对跟让打开点点头头皮笑拿掏出递塞拽拉举放带推挤坐站起指骂哭笑停望瞪发低抬高掀翻解系扣转过回上下进出偷左右为是在有要会能也可刚这着过")
_WEAK_SURNAMES = set("都曾全左右上下中前后里外内边旁安常万白高于易莫单双关查相权后孔石文国家田门方户为是在有要会能")
_COMMON_2GRAM = {"严重", "应激", "能力", "明白", "结果", "意思", "高兴", "开始",
                 "结束", "出现", "存在", "需要", "要求", "问题", "情况", "环境",
                 "关系", "责任", "义务", "权力", "精神", "神经", "身体", "影响",
                 "治疗", "诊断", "症状", "反应", "障碍", "行为", "判断", "方面",
                 "程度", "范围", "阶段", "部分", "原因", "目的", "消息", "信息",
                 "新闻", "媒体", "生活", "工作", "学习", "事情", "时间", "地方",
                 "城市", "医院", "病房", "走廊", "窗户", "手机", "病人", "医生",
                 "护士", "警察", "老师", "学生", "爸爸", "妈妈", "哥哥", "姐姐",
                 "弟弟", "妹妹", "爷爷", "奶奶", "父母", "孩子", "朋友", "申请",
                 "能力", "治疗", "病号"}
_HARD_BOUND = set("，。！？；：、.!,?;:  「」“”\"'…~——\n\r\t（）()")


def _pre_plausible(name: str, text: str, pos: int) -> bool:
    before = text[pos - 1] if pos > 0 else ""
    if not before:
        return True
    if before in "的了是就向对跟给把被":
        return True
    if before in _HARD_BOUND:
        return True
    return False


def _speech_after(name: str, text: str, pos: int) -> bool:
    after = text[pos + len(name):pos + len(name) + 1]
    return after in ("说", "道", "问", "答", "喊", "叫")


def _title_adjacent(name: str, text: str, pos: int) -> bool:
    """称谓必须紧贴名字（中间无间隔），如"同桌马凯""二叔陈硕""医生老刘"。"""
    for t in _TITLE_PREFIX:
        if pos >= len(t) and text[pos - len(t):pos] == t:
            return True
    return False


def _find_all(name: str, text: str):
    start = 0
    while True:
        i = text.find(name, start)
        if i < 0:
            return
        yield i
        start = i + 1


def extract_characters(text: str, cap: int = 10) -> list[str]:
    """抽取人物名。强姓多次出现 + 谓词位置合理 → 收；弱姓需称谓邻接或说话动作佐证。"""
    cand = Counter()
    pat = re.compile('[' + "".join(_SURNAMES) + '][一-鿿]{1,2}')
    for m in pat.finditer(text):
        if m.start() > 0 and text[m.start() - 1] in "老少阿":
            continue
        name3 = m.group(0)
        if len(name3) == 3:
            last, given = name3[2], name3[1:]
            if last in _GIVEN_STOP or given in _COMMON_2GRAM:
                name2 = name3[:2]
                if name2[1] not in _GIVEN_STOP:
                    cand[name2] += 1
                continue
            cand[name3] += 1
            continue
        if name3[1] not in _GIVEN_STOP:
            cand[name3] += 1

    for m in re.finditer('[老少阿][一-鿿]', text):
        w = m.group(0)
        if w[1] not in _GIVEN_STOP:
            cand[w] += 1

    out = []
    for name in sorted(cand, key=lambda n: -cand[n]):
        if name in _NAME_BLACKLIST:
            continue
        freq = cand[name]
        positions = list(_find_all(name, text))
        ok_pre = any(_pre_plausible(name, text, i) for i in positions)
        ok_title = any(_title_adjacent(name, text, i) for i in positions)
        ok_speech = any(_speech_after(name, text, i) for i in positions)
        weak = name[0] in _WEAK_SURNAMES
        if freq >= 2 and ok_pre:
            out.append(name)
        elif not weak and freq >= 1 and (ok_title or (ok_speech and ok_pre)):
            out.append(name)
        if len(out) >= cap:
            break
    return out


# ══ 物品/专有名词抽取 ════════════════════════════════════════════════
_OBJ_STOP = set("的了是在就都也很还把被不没你有我他她它们和与向对跟给让叫说道问答喊应声回这那什怎啊呀呢吧吗过着想看去来上下中里外内前后左右出进要会把可但然所以因为于从按往对为着之其所此个条种点些等上下午晚夜日时月年分秒真特幺")
_COMMON_WORDS = {"什么", "怎么", "为什么", "因为", "所以", "但是", "然后", "之后",
                 "之前", "这个", "那个", "哪个", "这么", "那么", "这里", "那里",
                 "一个", "一种", "一些", "一下", "一点", "一直", "一定", "起来",
                 "下来", "出来", "过来", "回去", "进去", "上去", "不是", "不会",
                 "不能", "不要", "不用", "不敢", "他们", "我们", "你们", "自己",
                 "已经", "没有", "还是", "而且", "不过", "然后", "非常", "知道",
                 "时候", "东西", "今天", "明天", "昨天", "现在", "开始", "觉得",
                 "感觉", "问题", "事情", "时间", "地方", "房间", "房子", "医院",
                 "病人", "医生", "护士", "老人", "年轻人", "小伙子", "孩子", "小朋友",
                 "朋友", "邻居", "同学", "老师", "警察", "银行", "城市", "公司",
                 "单位", "手机", "电脑", "电视", "消息", "新闻", "网友", "用户",
                 "大家", "人家", "国家", "王国", "花园", "黄河", "海洋", "环境",
                 "情况", "办法", "主意", "想法", "非常严重", "极其严重", "精神病",
                 "输入密码", "无民事行",
                 # 场景/过程泛词，不作为锚点（占用槽位且生成端不易写错）
                 "病房", "走廊", "大厅", "六楼", "治疗", "重度", "治好", "法院",
                 "通道", "窗户", "大门", "门口", "电梯", "楼道", "病床", "医院楼",
                 "墙壁"}
# 量词NP核心里出现这些 → 判定为动作片段，丢弃（"一个男护士捂住陈…"）
_NP_VERB_STOP = set("瘦捂掏递坐站按拿塞拽拉举放推挤抱绑锁拧拖夹吹翻掀抬低打挖踢踩压掐捏")
_OBJ_CONVO = ("喜欢", "妈妈", "爸爸", "爷爷", "奶奶", "哥哥", "姐姐", "弟弟", "妹妹",
              "爱你", "你说", "我说", "好不好", "是不是", "对不对", "有病吧")
_QUANTIFIER_RE = re.compile('一[个块张把支台座扇副只件条根杯碗栋堆位沓叠摞][一-鿿]{1,6}')


def extract_objects(text: str, cap: int = 10, skip_names: set | None = None,
                    skip_numbers: list[str] | None = None) -> list[str]:
    """抽取物品/专有名词锚点。

    三层来源：
      1. 重复 n-gram（2~8 字，取最大覆盖）→ 场景反复出现的实体
      2. 量词 NP（一块橡皮擦/一张张单人床）→ 具体物品，核心里出现动作/虚词即截断
      3. X的图案/牌子/名字 → 专有名词锚（宇智波鼬）

    跳过人物名（skip_names）和大额数字（skip_numbers，已被数字锚覆盖）。
    """
    skip = skip_names or set()
    skip_numbers = skip_numbers or []
    scored: dict[str, float] = {}

    # 1. 重复 n-gram
    ngram_counts = Counter()
    for n in range(2, 9):
        for i in range(len(text) - n + 1):
            seg = text[i:i + n]
            if all("一" <= c <= "鿿" for c in seg) and not any(
                    c in _OBJ_STOP for c in seg):
                ngram_counts[seg] += 1
    repeated = sorted((w for w, c in ngram_counts.items() if c >= 2),
                      key=len, reverse=True)
    maximal = [w for w in repeated if not any(w != x and w in x for x in repeated)]
    for w in maximal:
        if w in _COMMON_WORDS or any(k in w for k in _OBJ_CONVO):
            continue
        if w.startswith("一") and len(w) == 3 and w[1] == w[2] and w[1] in "个块张把支台座扇副只件条根杯碗栋堆位沓叠摞":
            continue  # 量词叠词片段（一张张/一个个），无实义
        scored[w] = scored.get(w, 0) + 1.5 + min(ngram_counts[w], 5) * 0.6 + len(w) * 0.2

    # 2. 量词 NP：核心里出现停顿/虚词即截断；含动作字/称谓则丢弃
    for m in _QUANTIFIER_RE.finditer(text):
        np_ = m.group(0)
        core = np_[2:]
        cut = len(core)
        for idx, ch in enumerate(core):
            if ch in _OBJ_STOP:
                cut = idx
                break
        core = core[:cut]
        if len(core) >= 2 and core[0] == core[1]:
            core = core[1:]
        if len(core) < 2 or core in _COMMON_WORDS:
            continue
        if any(c in _NP_VERB_STOP for c in core):
            continue
        if any(t in core for t in _TITLE_PREFIX):
            continue
        if any(core.endswith(k) for k in ("囊囊", "乎乎", "彤彤", "晶晶", "油油", "嫩嫩", "腾腾")):
            continue  # 形容词叠词核心（鼓囊囊/黑乎乎），非实体
        np_ = np_[:2] + core
        scored[np_] = scored.get(np_, 0) + 3.2 + len(core) * 0.2

    # 3. X的图案/牌子/名字（专有名词锚）
    for m in re.finditer('([一-鿿]{2,6}?)的(?:图案|牌子|花纹|名字|样子|标志)', text):
        obj = re.sub(r'^[有是贴印画写在上边面里内中背下面这那]+', "", m.group(1))
        if obj and obj not in _COMMON_WORDS and len(obj) >= 2:
            scored[obj] = scored.get(obj, 0) + 4.0 + len(obj) * 0.2

    out = []
    for w, s in sorted(scored.items(), key=lambda kv: -kv[1]):
        if any(n in w or w in n for n in skip):
            continue
        if any(t in w for t in skip_numbers):
            continue  # 已被数字锚点覆盖（两千万/一栋两千多万…）
        if w in _COMMON_WORDS or len(w) < 2:
            continue
        out.append(w)
        if len(out) >= cap:
            break
    return out


# ══ 汇总入口 ════════════════════════════════════════════════════════

def extract_key_details(text: str, caps: dict | None = None) -> dict:
    """从目标原文抽取关键细节锚点（人物/数字/对白/物品）。

    Args:
        text: 目标原文（reverse-infer 的 target_text）
        caps: 各类型抽取上限，默认 characters=10, numbers=8, dialogues=12, objects=10

    Returns:
        {
            "characters": list[str],
            "numbers": [{"text", "value", "unit"}, ...],
            "dialogues": list[str],
            "objects": list[str],
            "number_texts": list[str],   # 大额数字原文（供物品抽取 skip）
        }
    """
    caps = caps or {}
    c_cap = caps.get("characters", 10)
    n_cap = caps.get("numbers", 8)
    d_cap = caps.get("dialogues", 12)
    o_cap = caps.get("objects", 10)

    characters = extract_characters(text, c_cap)
    numbers = extract_numbers(text, n_cap)
    dialogues = extract_dialogues(text, d_cap)
    skip_nums = [n["text"] for n in numbers if n["value"] >= 10000]
    objects = extract_objects(text, o_cap, skip_names=set(characters), skip_numbers=skip_nums)

    return {
        "characters": characters,
        "numbers": numbers,
        "dialogues": dialogues,
        "objects": objects,
        "number_texts": skip_nums,
    }


@lru_cache(maxsize=32)
def get_key_details(text: str) -> dict:
    """抽取关键细节并缓存。

    reverse-infer 会对同一个 target_text 评估上百个 P 向量，
    n-gram 抽取在长文本上略贵，必须缓存避免重复计算。
    返回的 dict 只读使用（拼 prompt / 评分），调用方不得修改。
    """
    return extract_key_details(text)


# ══ 事实一致性评分 ══════════════════════════════════════════════════

def factual_consistency_score(
    candidate: str,
    target: str,
    key_details: dict | None = None,
) -> dict:
    """事实一致性评分：候选文本对原文关键细节的保留程度（纯规则，零成本）。

    锚点价值越高权重越大：
    - 人物  3.0/个    出现即命中
    - 数字  金额/大额(value≥10000) 4.0；时间/货币单位 3.0；其余 2.0
    - 对白  2.0/句    归一化后子串命中（要求原句保留）
    - 物品  1.5/个    出现即命中

    命中 = 锚点原文（归一化后）作为子串出现在候选（归一化后）。
    金额/人名/物品是"绝不能改"的硬锚；对白略松（归一化吸收标点差异）。

    Returns:
        {
            "score": float,      # ∈ [0,1]，0=全部丢失，1=全部保留
            "matched": float,    # 加权命中数
            "total": float,      # 加权总数
            "detail_results": [  # 逐锚点明细（供落盘/前端展示）
                {"type","text","weight","matched"}, ...
            ]
        }
    """
    if key_details is None:
        key_details = extract_key_details(target)
    cand = normalize_text(candidate or "")

    chars = key_details.get("characters", [])
    nums = key_details.get("numbers", [])
    dias = key_details.get("dialogues", [])
    objs = key_details.get("objects", [])

    w_char, w_num_hi, w_num_lo, w_dia, w_obj = 3.0, 4.0, 2.0, 2.0, 1.5

    detail_results = []
    total = 0.0

    # 锚点自身也做归一化：对白里的 ？！等全角标点会转半角，候选侧同样已转，
    # 只有两侧都归一化才能对齐（人物/数字/物品的归一化是恒等变换）。
    for name in chars:
        m = name in cand
        detail_results.append({"type": "character", "text": name, "weight": w_char, "matched": m})
        total += w_char

    for num in nums:
        w = w_num_hi if num.get("value", 0) >= 10000 else (
            w_num_lo if num.get("unit", "") in _MONEY_UNITS or num.get("unit", "") in _TIME_UNITS
            else 2.0)
        m = num["text"] in cand
        detail_results.append({"type": "number", "text": num["text"], "weight": w, "matched": m})
        total += w

    for q in dias:
        qn = normalize_text(q)
        m = qn in cand
        detail_results.append({"type": "dialogue", "text": q, "weight": w_dia, "matched": m})
        total += w_dia

    for obj in objs:
        m = obj in cand
        detail_results.append({"type": "object", "text": obj, "weight": w_obj, "matched": m})
        total += w_obj

    matched = sum(d["weight"] for d in detail_results if d["matched"])
    score = round(matched / total, 4) if total > 0 else 1.0

    return {
        "score": score,
        "matched": round(matched, 2),
        "total": round(total, 2),
        "detail_results": detail_results,
    }
