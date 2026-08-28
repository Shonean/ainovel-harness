# -*- coding: utf-8 -*-
"""v5.27 AI 味审阅模块：LLM 对照原文发现 AI 味 + 确定性对白间隙检测。

背景（用户 2026-08-05 目标）：生成正文"对话前后多余的不自然描写"等 AI 味极重，
但评分（v519 = 0.5s_char + 0.3plot_sim + 0.2v_cos）与迭代反馈对 AI 味零敏感——
s_char 是原文 n-gram 覆盖率（recall 方向），注水不影响；v5.6 黑名单检测抓不住
"微动作+感官词+副词"三件套模式。LLM 能看出 AI 味，但没有任何环节让它看。

本模块补上"对照原文的自查"：
- review_ai_flavor(generated, target)：LLM 以【原文】为"正常写法"基准，逐处找出
  生成正文中原文不会这么写的 AI 味问题，输出结构化 findings + 0-1 分数。
- dialogue_gap_decoration(generated, target)：零 LLM 确定性检测"对白间隙装饰"
  （每轮对白之间塞动作+氛围/副词三件套）——快速信号 + LLM 失败降级。
- build_ai_flavor_feedback / build_ai_flavor_directives：把 findings 转成可注入
  生成 user_input 的【AI味修正块】和 improvement_context 扣分指令。

接入：_evaluate_p 生成后跑审阅 → ai_flavor 进综合分惩罚系数；findings 进
improvement_context 跨迭代反馈；修正块注入下一候选 user_input。
"""
from __future__ import annotations

import json
import re
from typing import Any

from .config import SETTINGS
from . import scorer_params as _sp
from .llm_client import chat_json

# 确定性对白间隙检测的词表（"三件套"判定用）
_ATMOSPHERE_WORDS = [
    "凉意", "夜色", "黑夜", "月光", "灯光", "日光灯", "风", "静", "暗", "影",
    "缓缓", "轻轻", "微微", "渐渐", "终于", "良久", "默默", "无声", "安静",
    "寂静", "沉默", "目光", "视线", "眼神", "窗外", "幽暗", "空旷", "阴森",
    "裹着", "笼罩", "洒", "透", "亮着", "昏暗", "斑驳", "静谧", "氤氲",
]
_ACTION_VERBS = [
    "推", "抬", "低", "点", "握", "敲", "顿", "靠", "站", "坐", "转", "看",
    "望", "皱", "抿", "叹", "耸肩", "眨", "眯", "停", "放", "拿", "扶", "瞥",
    "扫", "环顾", "打量", "伸手", "开口", "出声", "应", "捋", "摸", "捏",
]
_ADVERB_PHRASES = [
    "缓缓", "轻轻", "微微", "悄悄", "默默", "淡淡", "冷冷", "不紧不慢",
    "干净利落", "迅速", "突然", "迟疑", "满意", "尴尬", "惊讶", "喜滋滋",
    "阴阳怪气", "不自然", "漫不经心", "若有所思",
]
# "微动作特写"签名（AI 把一次动作拆成慢镜头）：X了X 结构 / 身体部位特写
_MICRO_MOVE_RE = re.compile(
    r"(抬了抬|眨了眨|抿了抿|点了点头|眯了眯|皱了皱|耸了耸肩|捏了捏|搓了搓"
    r"|指尖|眼皮|嘴角|膝盖|手指|喉结|肩窝|额角|袖口|衣角|坐直|往后靠|朝椅背)"
)

# ═══════════════════════════ v5.32.5 确定性流水账检测（反动作平铺/反细节装饰） ═══════════════════════════
# 用户痛点（2026-08-06）：重建正文把原文一句带过的「翻翻找找清理」展开成逐微动作清单
# （翻抽屉→翻床底→摸床垫→开衣柜→扯衣服），加大量零推进细节（踢脚线没积灰/皮面没开裂）。
# 禁令/审阅是软约束 + 事后纠错，模型重生成还是同一套 prompt → 加零 LLM 确定性检测器，
# 与 dialogue_gap_decoration 一样作审阅融合信号（事前详略导向 + 事后确定性兜底）。

# 否定式反复查找信号（「没找到…没找到…」）——逐动作罗列失败结果
_NEG_SEARCH_RE = re.compile(r"没[^。！？]{0,6}(找到|找着|见着|摸着|翻到)|也没见着|什么都没摸到|空手而归|没翻到|找不到")
# 过场动作动词（翻找/收拾/走路/开门/搜）——高密度=平铺流水账
_GO_TO_VERBS = ["翻", "摸", "搜", "打开", "推开", "拉开", "抽出", "蹲下", "弯腰", "踮脚", "走到", "绕到",
                "转到", "关上", "抬腿", "迈", "跨", "扫过", "翻了", "摸了摸", "掏", "扯"]
# 零推进细节词（对情节/人物/冲突无作用的物件环境描写）
_DECOR_DETAIL_WORDS = ["积灰", "灰尘", "透亮", "开裂", "开裂处", "映出", "光洁", "一尘不染", "擦得",
                       "保养", "锃亮", "光亮", "泛着", "斑驳", "磨得发亮", "掉了一块漆", "堆着废纸",
                       "空饮料瓶", "冰凉", "抛光"]
# ── v5.32.6 确定性「内容重复」检测：同一组核心实体（人名+金额+关键物）相隔较远再出现 = 复述凑字 ──
_REDUNDANCY_NAMES = ("林川", "陈硕", "王慧兰", "袍哥", "二刀", "老刘")
# 强 token（复述/漂移标记，合法高频复现少见）：金额 + 这些；弱 token（人名/物件/别墅/院子）只凑总数。
# 注意：遗像/房本/黄金 是连续事件弧里的合法高频物件（看到→抱怨→处理），不算复述标记——
# 只有金额（借钱往事/抵押数）与专属事件/物件（烧烤/录取通知书）相隔再出现才是不折不扣的复述。
_REDUNDANCY_ITEMS = ("房本", "黄金", "遗像", "录取通知书", "荣誉证书", "奖状", "诊断书",
                     "别墅", "院子", "烧烤", "嫂子", "大哥")
_REDUNDANCY_STRONG = ("录取通知书", "荣誉证书", "奖状", "诊断书", "烧烤")
_MONEY_RE = re.compile(r"[一二三四五六七八九十百千零0-9]+[万亿]")
_REDUNDANCY_GAP = 3      # 句距阈值：相邻对白轮的合法重复（房本呢→房本藏外面）不算
_REDUNDANCY_SHARE = 2    # 共享 token 总数阈值
_QUOTE_CHARS = str.maketrans("", "", "“”「」『』")


def _redundancy_signals(text: str) -> dict:
    """按句切分；每句 token 集 = {人名}∪{金额}∪{关键物}；相隔 ≥GAP 句且**共享 ≥SHARE 个且含强 token** → 记重复。

    强 token（金额/具体物件如 房本、黄金、烧烤、遗像）防误报——人名+「别墅」这类合法高频
    共现（两人站在别墅门口+对话里提别墅）不算复述；复述凑字一定复现具体事实（金额/物件/事件）。
    引号剥离：对白以 。” 结尾时只留孤儿引号句，必须先剥掉再判空。
    """
    sents = []
    for s in re.split(r"[。！？\n]", text or ""):
        s = s.translate(_QUOTE_CHARS).strip()
        if s:
            sents.append(s)
    toks: list[set[str]] = []
    for s in sents:
        tok: set[str] = set()
        for n in _REDUNDANCY_NAMES:
            if n in s:
                tok.add(n)
        for m in _MONEY_RE.finditer(s):
            tok.add(m.group(0))
        for it in _REDUNDANCY_ITEMS:
            if it in s:
                tok.add(it)
        toks.append(tok)
    n_dup = 0
    for i in range(len(toks)):
        for j in range(i + _REDUNDANCY_GAP, len(toks)):
            inter = toks[i] & toks[j]
            if len(inter) < _REDUNDANCY_SHARE:
                continue
            has_strong = any(_MONEY_RE.fullmatch(t) or t in _REDUNDANCY_STRONG for t in inter)
            if has_strong:
                n_dup += 1
    n_sent = max(len(sents), 1)
    return {"n_dup": n_dup, "n_sent": n_sent, "dup_rate": round(n_dup / n_sent, 3)}


def flow_narration_detector(text: str) -> dict[str, Any]:
    """确定性流水账检测（零 LLM）：动作平铺 + 否定式反复 + 零推进细节 + 内容重复 → 0-1 分数。

    判断「叙述像流水账」的四个确定性信号：
    ① 否定式反复查找密度：原文一句「翻翻找找清理」→ 正文「没找到…没找到…」逐条罗列；
    ② 过场动词密度：翻/摸/搜/打开/推开/走到 高频 = 动作平铺；
    ③ 零推进细节密度：积灰/透亮/开裂/映出/保养 等对叙事零作用的描写；
    ④ 【v5.32.6】内容重复密度：同一组核心实体（人名+金额+关键物）相隔 ≥3 句再出现
       = 复述已有信息凑字（借钱往事说两遍、烧烤闪回说两遍、找房本黄金翻多次）。

    Returns: {flow_clean: 0-1（1=叙述干净无流水账）, signals: {...}, verdict: str}
    """
    t = (text or "").strip()
    if not t:
        return {"flow_clean": 1.0, "signals": {}, "verdict": "empty"}
    n_neg = len(_NEG_SEARCH_RE.findall(t))
    n_go = sum(t.count(v) for v in _GO_TO_VERBS)
    n_dec = sum(t.count(w) for w in _DECOR_DETAIL_WORDS)
    red = _redundancy_signals(t)
    n_chars = len(t)
    per_k = n_chars / 1000.0  # 每千字基准
    neg_rate = n_neg / max(per_k, 1)
    go_rate = n_go / max(per_k, 1)
    dec_rate = n_dec / max(per_k, 1)
    dup_rate = red["dup_rate"]
    # 惩罚累计：各信号超过基线后按超标量罚
    penalty = 0.0
    if neg_rate > 1.0:  # 每千字 >1 次否定式查找
        penalty += min(0.35, (neg_rate - 1.0) * 0.12)
    if go_rate > 12.0:  # 每千字 >12 个过场动词
        penalty += min(0.30, (go_rate - 12.0) * 0.02)
    if dec_rate > 2.0:  # 每千字 >2 个零推进细节词
        penalty += min(0.30, (dec_rate - 2.0) * 0.05)
    if dup_rate > 0.05:  # 每句重复率 >5%（相隔≥3句的核心实体对重复）
        penalty += min(0.35, (dup_rate - 0.05) * 2.0)
    flow_clean = round(max(0.0, min(1.0, 1.0 - penalty)), 4)
    verdict = "clean" if flow_clean >= 0.85 else ("mild" if flow_clean >= 0.6 else "bad")
    return {
        "flow_clean": flow_clean,
        "verdict": verdict,
        "signals": {"n_neg": n_neg, "n_go": n_go, "n_dec": n_dec,
                    "neg_rate": round(neg_rate, 2), "go_rate": round(go_rate, 2),
                    "dec_rate": round(dec_rate, 2), "chars": n_chars,
                    "n_dup": red["n_dup"], "n_sent": red["n_sent"], "dup_rate": dup_rate},
    }


def paragraph_open_diversity_detector(
    text: str,
    *,
    names: list[str] | None = None,
    objects: list[str] | None = None,
) -> dict[str, Any]:
    """确定性段落开头多样化检测（零 LLM）：同一开头连续出现 + 人名开头占比 + 章首两段同开头。

    对应 l5 不变prompt「段首多样化」的可执行检查（2026-08-08 第三本测试书 ch3 暴露）：
    ① 连续段落不得以同一词语开头（人物名/物件名/前两字都算）；
    ② 同一人名开头的段落占比过高（>1/3）；
    ③ 章首前两段从同一物件/场景元素写起（如「豆腐摊→豆腐摊」）。

    开头 key：先前缀匹配已知人名（names，最长优先），其次首句含物件词（objects），
    兜底取前 2 字（「沈石」「顾长」这类 2 字名天然被覆盖，无需名单）。

    Returns: {open_score: 0-1（1=开头多样干净）, verdict, signals, findings}
    """
    pars = [p.strip() for p in (text or "").split("\n") if p.strip()]
    if len(pars) < 4:
        return {"open_score": 1.0, "verdict": "empty", "paragraphs": len(pars),
                "signals": {}, "findings": []}
    _names = sorted({str(n).strip() for n in (names or []) if str(n).strip()}, key=len, reverse=True)
    _objs = sorted({str(o).strip() for o in (objects or []) if len(str(o).strip()) >= 2}, key=len, reverse=True)

    def _key(p: str) -> tuple[str, bool]:
        s = p.lstrip("“”\"「」『』…—·-— 0123456789。，；：")
        if not s:
            return "", False
        for n in _names:
            if s.startswith(n):
                return n, True
        if _objs:
            # 首句里出现位置最早的物件词 = 这段「由什么开头写起」（豆腐摊/羊角风灯同句时取豆腐摊）
            first = re.split(r"[。！？!?]", s, maxsplit=1)[0]
            best: tuple[str, int] = ("", -1)
            for o in _objs:
                idx = first.find(o)
                if idx >= 0 and (best[1] < 0 or idx < best[1]):
                    best = (o, idx)
            if best[1] >= 0:
                return best[0], False
        return s[:2], False

    keys = [_key(p) for p in pars]
    n = len(keys)

    def _head_same_subject() -> bool:
        """首两段首句是否共享同一「开头主体」（不依赖物件名单）。

        用户反馈「前两段开头一直是豆腐摊」——豆腐摊是临时场景道具，不在 elements
        名单里。用首句二元组重叠近似：任一段首句前 10 字内出现过的共有二元组
        （如 豆腐摊→豆腐）即判首两段同主体开头。
        """
        a = re.split(r"[。！？!?]", pars[0], maxsplit=1)[0]
        b = re.split(r"[。！？!?]", pars[1], maxsplit=1)[0]
        if len(a) < 3 or len(b) < 3:
            return False
        # 三元组重叠近似「同一开头主体」（豆腐摊→豆腐摊 命中；「了半」这类虚词二元组不误报）。
        # 排除人名三元组——「都以沈石开头」已由 name 前缀 keys 相等覆盖。
        ta = {a[i:i + 3] for i in range(len(a) - 2)}
        tb = {b[i:i + 3] for i in range(len(b) - 2)}
        shared = {g for g in (ta & tb) if not any(n.startswith(g) for n in _names)}
        # 任一段首句前 14 字内出现过的共有三元组（豆腐摊在 P1 第 2 分句也要算）
        return any(g in a[:14] or g in b[:14] for g in shared)
    # 连续相同开头最长 run
    max_run = 1
    cur = 1
    for i in range(1, n):
        if keys[i][0] and keys[i][0] == keys[i - 1][0]:
            cur += 1
            max_run = max(max_run, cur)
        else:
            cur = 1
    name_led = sum(1 for _, is_name in keys if is_name) / n
    from collections import Counter
    cnt = Counter(k[0] for k in keys if k[0])
    top1, top1_n = (cnt.most_common(1)[0] if cnt else ("", 0))
    top1_ratio = top1_n / n if top1_n else 0.0
    distinct = [k for k in cnt if k]
    distinct_ratio = len(distinct) / n if distinct else 0.0
    head_dup = bool(keys[0][0] and keys[1][0] and keys[0][0] == keys[1][0]) or _head_same_subject()

    penalty = 0.0
    if max_run >= 3:
        penalty += 0.30
    elif max_run == 2:
        penalty += 0.12
    if name_led > 0.35:
        penalty += min(0.35, (name_led - 0.35) * 0.9)
    if top1_ratio > 0.35:
        penalty += min(0.30, (top1_ratio - 0.35) * 0.8)
    if head_dup:
        penalty += 0.15
    if distinct_ratio < 0.5:
        penalty += min(0.25, (0.5 - distinct_ratio) * 0.8)
    open_score = round(max(0.0, min(1.0, 1.0 - penalty)), 4)
    verdict = "clean" if open_score >= 0.85 else ("mild" if open_score >= 0.6 else "bad")

    findings: list[dict[str, str]] = []
    if head_dup:
        findings.append({
            "quote": pars[0][:40] + " / " + pars[1][:40],
            "category": "段落开头重复",
            "severity": "medium",
            "fix": "章首两段不要从同一人物/物件写起，第二段换主语省略/代词/动作先行/对白开头",
        })
    if max_run >= 2:
        findings.append({
            "quote": "、".join(pars[i][:16] for i in range(n)
                              if i and keys[i][0] and keys[i][0] == keys[i - 1][0])[:160] or top1,
            "category": "段落开头重复",
            "severity": "high" if max_run >= 3 else "medium",
            "fix": f"连续 {max_run} 段以「{top1}」开头，改为无主句/代词/动作先行/环境先行交替",
        })
    if name_led > 0.35 and _names:
        findings.append({
            "quote": f"人名开头段落占比 {name_led:.0%}",
            "category": "段落开头重复",
            "severity": "medium",
            "fix": "同一人名开头的段落需降到 1/3 以下，交替用主语省略/代词/动作/环境/对白开头",
        })
    return {
        "open_score": open_score,
        "verdict": verdict,
        "paragraphs": n,
        "signals": {
            "max_same_run": max_run, "name_led_ratio": round(name_led, 3),
            "top1": top1, "top1_ratio": round(top1_ratio, 3),
            "distinct_ratio": round(distinct_ratio, 3), "head_dup": head_dup,
        },
        "findings": findings[:6],
    }


# ═══════════════════════════ v6.4.1 过场/细节不断展开检测 ═══════════════════════════
# 用户反馈：模型把该一笔带过的东西（更梆裂纹/灯罩油烟/踢竹筐/余温透鞋底/物件清单）逐条展开
# 凑字数。按句分类——含说话/强情节动作动词=推进句；否则（纯描写/过场动作/装备/感官）=过场句。
# 过场句占比高或连续成 run 即「过详细」。通用非书本化（不靠具体关键词表）。
_SPEECH_VERBS = (
    "说", "问", "答", "喊", "叫", "骂", "叹", "应", "回", "劝",
    "自语", "嘀咕", "开口", "出声", "低声", "沉声", "轻声", "压着嗓子",
    "喝止", "提醒", "吩咐", "摇头", "点头", "皱眉",
)
_PLOT_VERBS = (
    "看", "查", "核对", "辨认", "确认", "发现", "掀", "翻", "摸", "探", "闻", "嗅",
    "听", "数", "指", "按", "握", "提", "掏", "塞", "蹲", "凑", "靠近", "盯", "追",
    "找", "认", "念", "记", "守", "抢", "夺", "递", "接", "照",
    "画", "写", "试", "验", "比", "对", "读", "检", "察", "验尸", "察看", "比照",
)
_PROCESSIVE_VERBS = (
    "走", "敲", "踢", "抬", "挪", "退", "进", "出", "放", "拿", "挂", "吹", "摇",
    "晃", "扶", "迈", "跨", "绕", "躲", "拐", "转身", "侧身", "贴着", "沿着", "顺着",
    "站在", "路过",
)


def overdetail_detector(text: str) -> dict[str, Any]:
    """确定性「过场/细节不断展开」检测（零 LLM，通用非书本化）。

    按句分类：对白句（含引号）| 推进句（含说话/强情节动作动词）| 过场句（其余——纯描写/
    过场动作/装备/感官/物件清单）。信号：overhead_ratio（过场句占比）、max_overhead_run
    （连续过场句最长 run）、proc_chain_per_k（单句≥2 过场动词=逐微动作，每千字）。

    Returns: {overdetail_score: 0-1（1=紧凑干净）, verdict, signals, findings}
    """
    t = (text or "").strip()
    if not t:
        return {"overdetail_score": 1.0, "verdict": "empty", "signals": {}, "findings": []}
    sents = [s.strip() for s in re.split(r"[。！？!?\n]", t) if s.strip()]
    if len(sents) < 6:
        return {"overdetail_score": 1.0, "verdict": "empty", "signals": {}, "findings": []}

    def _cls(s: str) -> str:
        if "“" in s or "”" in s or "「" in s or "」" in s:
            return "dialogue"
        if any(v in s for v in _SPEECH_VERBS) or any(v in s for v in _PLOT_VERBS):
            return "plot"
        return "overhead"

    kinds = [_cls(s) for s in sents]
    n = len(kinds)
    overhead = [k == "overhead" for k in kinds]
    n_oh = sum(overhead)
    overhead_ratio = n_oh / n
    max_run = 0
    cur = 0
    for o in overhead:
        cur = cur + 1 if o else 0
        max_run = max(max_run, cur)
    # 逐微动作链：单句 ≥3 个过场动词（如「踢开…抬脚继续走」）才是真展开，短句/长文本才计
    proc_chain = sum(1 for s in sents if sum(1 for v in _PROCESSIVE_VERBS if v in s) >= 3)
    chars = len(t.replace(" ", "").replace("\n", ""))
    per_k = max(chars / 1000.0, 0.5)  # floor 防短文本放大
    proc_per_k = proc_chain / per_k

    penalty = 0.0
    if overhead_ratio > 0.45:
        penalty += min(0.35, (overhead_ratio - 0.45) * 1.2)
    if max_run >= 4:
        penalty += 0.35
    elif max_run == 3:
        penalty += 0.25
    elif max_run == 2:
        penalty += 0.10
    if chars >= 200 and proc_per_k > 2.0:
        penalty += min(0.25, (proc_per_k - 2.0) * 0.15)
    overdetail_score = round(max(0.0, min(1.0, 1.0 - penalty)), 4)
    verdict = "clean" if overdetail_score >= 0.85 else ("mild" if overdetail_score >= 0.6 else "bad")

    findings: list[dict[str, str]] = []
    if max_run >= 3:
        # 取最长过场句 run 片段
        run_sents: list[str] = []
        cur_run = 0
        for s, o in zip(sents, overhead):
            if o:
                cur_run += 1
                run_sents.append(s)
            else:
                if cur_run >= 3:
                    break
                cur_run = 0
                run_sents = []
        findings.append({
            "quote": "…".join(run_sents[:4])[:120],
            "category": "过场展开",
            "severity": "high" if max_run >= 4 else "medium",
            "fix": "连续 " + str(len(run_sents)) + " 句过场/描写展开，压缩为一两句或删除（走路/装备/感官/物件细节该一笔带过）",
        })
    if overhead_ratio > 0.5:
        findings.append({
            "quote": f"过场/描写句占比 {overhead_ratio:.0%}",
            "category": "过场展开",
            "severity": "medium",
            "fix": "压缩环境/装备/感官/物件细节，把篇幅给对白与冲突回合",
        })
    return {
        "overdetail_score": overdetail_score,
        "verdict": verdict,
        "paragraphs": n,
        "signals": {
            "overhead_ratio": round(overhead_ratio, 3),
            "max_overhead_run": max_run,
            "proc_chain_per_k": round(proc_per_k, 2),
            "chars": chars,
        },
        "findings": findings[:4],
    }


# ═══════════════════════════ v5.35 审阅置信度门槛（对标 codex review rubric） ═══════════════════════════
# 借鉴 codex review/rubric.md：每条 finding 带 confidence_score(0-1)，
# 低于阈值不采纳（宁漏勿错，降误报）；priority 0-3 仅用于排序/展示，不改变是否采纳。
# 「原作者会不会想修」是是否上报的唯一门槛——拿不准、属个人口味、或原文本就如此的不报。
REVIEW_CONFIDENCE_FLOOR = 0.6


def _normalize_finding(f: dict, *, max_quote: int = 200, max_fix: int = 300) -> dict | None:
    """把一条 LLM 审阅 finding 规范化；置信度低于门槛返回 None（不采纳）。"""
    if not isinstance(f, dict):
        return None
    quote = str(f.get("quote") or "").strip()
    if not quote:
        return None
    sev = str(f.get("severity", "medium"))
    if sev not in ("high", "medium", "low"):
        sev = "medium"
    # 置信度：缺省按 severity 兜底（high→0.8, medium→0.65, low→0.5）
    try:
        conf = float(f.get("confidence_score"))
    except (TypeError, ValueError):
        conf = {"high": 0.8, "medium": 0.65, "low": 0.5}[sev]
    conf = round(max(0.0, min(1.0, conf)), 2)
    if conf < _sp.get_threshold("review_confidence_floor", REVIEW_CONFIDENCE_FLOOR):
        return None
    try:
        pr = int(f.get("priority"))
        if pr not in (0, 1, 2, 3):
            pr = None
    except (TypeError, ValueError):
        pr = None
    return {
        "quote": quote[:max_quote],
        "category": str(f.get("category", "其它")) or "其它",
        "severity": sev,
        "fix": str(f.get("fix", ""))[:max_fix],
        "confidence": conf,
        "priority": pr,
    }


# ═══════════════════════════ v5.31 对白引号统一（不可变原则） ═══════════════════════════
# 用户 2026-08-06：全系统对白统一用中文弯引号 “”；评分侧 “”/「」 等价不扣分
# （scorer.normalize_text 已把两套引号映射为 ASCII "，char 相似度/ngram/pf 天然免疫）。
DIALOGUE_QUOTE_OPEN = "“"
DIALOGUE_QUOTE_CLOSE = "”"
_DIALOGUE_QUOTE_FIX = str.maketrans({
    "「": DIALOGUE_QUOTE_OPEN,
    "」": DIALOGUE_QUOTE_CLOSE,
    "『": DIALOGUE_QUOTE_OPEN,
    "』": DIALOGUE_QUOTE_CLOSE,
})


def punct_detector(text: str) -> dict[str, Any]:
    """破折号/Markdown 密度检测（2026-08-10）：AI 味确定性信号。

    破折号「——」密集、省略号连用「……」、Markdown 标记（**、*、#）混入正文
    都是典型 AI 味——LLM 审阅主观可能漏判，这里确定性兜底，保证自动去味触发。
    Returns {punct_score: 0-1（越高越干净）, signals}"""
    t = str(text or "")
    if not t:
        return {"punct_score": 1.0, "signals": {}}
    n = len(t)
    dashes = t.count("——")
    ellipsis = t.count("……")
    stars = t.count("**") + t.count("*")
    hashes = t.count("#")
    md = stars + hashes
    dash_per_200 = dashes / max(1.0, n / 200.0)
    score = 1.0
    if dashes >= 1:
        score -= 0.4  # baseline_guard：全文禁止破折号「——」，任何出现都明显扣分（<0.7 必触发去味）
    if dash_per_200 > 3.0:
        score -= 0.2
    if ellipsis >= 1:
        score -= 0.2  # 省略号「……」也属 AI 味（v7.6.6 加强：3 个及以上跌破 0.7 触发去味）
    if ellipsis >= 3:
        score -= 0.2
    if md > 0:
        score -= 0.5  # Markdown 标记是硬伤，出现即明显压分（避免卡 0.7 门槛漏触发）
    score = round(max(0.0, min(1.0, score)), 4)
    return {"punct_score": score,
            "signals": {"dashes": dashes, "dash_per_200": round(dash_per_200, 2),
                        "ellipsis": ellipsis, "markdown": md}}


def to_dialogue_quotes(text: str) -> str:
    """把文本中的直角引号「」「『』 统一为中文弯引号 “” （“” 原样保留）。

    生成侧不可变原则：正文对白一律 “”。所有生成输入（契约/锚点/反馈）与
    生成输出统一过这里，保证模型只见到 “” 一种对白引号样式。
    """
    return (text or "").translate(_DIALOGUE_QUOTE_FIX)


def _dialogue_spans(text: str) -> list[tuple[int, int]]:
    """定位所有对白 span（兼容 「」 与 “” 两种引号样式）。"""
    spans = []
    for m in re.finditer(r'[「“]([^「」“”]+)[」”]', text or ""):
        spans.append((m.start(), m.end()))
    return spans


def _gap_decorated(gap: str, reference: str | None = None) -> bool:
    """一个对白间隙是否"装饰性"（微动作+氛围/副词三件套 / 微动作特写）。

    【v5.27.5】reference（原文）传入时：间隙前 20 字若在原文中出现 → 视为忠实复现，
    不计装饰。原检测会把原文的信息性动作（如"老刘划去重写"）误判成装饰 → gap.clean
    失真、误触发修正让模型删原文。修正后指标只惩罚"原文没有的额外装饰"，贴合用户
    "多余描写"的定义。
    """
    if not gap or len(gap) < 4:
        return False
    if reference:
        _head = gap.strip()[:20]
        if len(_head) >= 6 and _head in reference:
            return False  # 原文的间隙内容（忠实复现）
    has_action = any(v in gap for v in _ACTION_VERBS)
    has_atmos = any(w in gap for w in _ATMOSPHERE_WORDS)
    has_adv = any(w in gap for w in _ADVERB_PHRASES)
    # 【v5.27.5】微动作特写需 ≥2 条命中才算（逐帧堆叠=慢镜头=AI味）；
    # 单个自然动作（「擦了擦额角的汗」「摸了摸身边的位置」）是合法叙述，不算装饰。
    has_micro = len(_MICRO_MOVE_RE.findall(gap)) >= 2
    # 三件套：动作 + (氛围词 或 副词)；或微动作逐帧堆叠
    return (has_action and (has_atmos or has_adv)) or has_micro


def _gap_stats(text: str, reference: str | None = None) -> dict[str, float]:
    """对白间隙统计：数量、平均长度、装饰率。reference=原文（生成侧判断复现用）。"""
    spans = _dialogue_spans(text or "")
    n = len(spans)
    if n < 2:
        return {"n_gaps": 0, "avg_gap_len": 0.0, "deco_rate": 0.0}
    gaps = [text[spans[i][1]:spans[i + 1][0]] for i in range(n - 1)]
    gaps = [g for g in gaps if g.strip()]
    if not gaps:
        return {"n_gaps": 0, "avg_gap_len": 0.0, "deco_rate": 0.0}
    avg_len = sum(len(g) for g in gaps) / len(gaps)
    deco = sum(1 for g in gaps if _gap_decorated(g, reference))
    return {
        "n_gaps": len(gaps),
        "avg_gap_len": round(avg_len, 1),
        "deco_rate": round(deco / len(gaps), 4),
    }


def dialogue_gap_decoration(generated: str, target: str) -> dict[str, Any]:
    """确定性对白间隙装饰检测（零 LLM）。

    核心：生成文本里"对白轮之间插入装饰性叙述"（微动作三件套）的比例 vs 原文。
    原文对白密集连续（avg_gap_len 小、deco_rate 低）；生成版逐轮配描写 → 高。

    Returns:
        {clean: 0-1（1=干净）, gen: {...}, target: {...},
         ratio_gen_tgt: float, verdict: str}
    """
    gen = _gap_stats(generated, reference=target)
    tgt = _gap_stats(target)
    if gen["n_gaps"] == 0:
        return {"clean": 1.0, "gen": gen, "target": tgt,
                "ratio_gen_tgt": 1.0, "verdict": "no_gaps"}
    # 基准：原文装饰率（原文为 0 时给 0.15 兜底，防把"原文本身就干净"误杀）；
    # 生成高出越多越脏（约 2 倍即判 bad）。
    base = tgt["deco_rate"] if tgt["deco_rate"] > 0 else 0.15
    # 额外信号：生成平均间隙明显长于原文 → 也在注水
    len_pressure = 0.0
    if tgt["avg_gap_len"] > 0:
        len_ratio = gen["avg_gap_len"] / max(tgt["avg_gap_len"], 1)
        if len_ratio > 1.5:
            len_pressure = min(0.3, (len_ratio - 1.5) / 4.0)
    excess = max(0.0, gen["deco_rate"] - base)
    penalty = min(1.0, excess / 0.35) + len_pressure
    clean = round(max(0.0, min(1.0, 1.0 - penalty)), 4)
    verdict = "clean" if clean >= 0.85 else ("mild" if clean >= 0.6 else "bad")
    return {"clean": clean, "gen": gen, "target": tgt,
            "ratio_gen_tgt": round(gen["deco_rate"] / max(tgt["deco_rate"], 1e-9), 2),
            "verdict": verdict}


# ── LLM 对照原文审阅（主通道）────────────────────────────

REVIEW_SYSTEM_PROMPT = """你是一位苛刻的中文网文审稿人，专门识别"AI 味"——语言模型写小说正文时特有的、人类读者一眼能察觉的机械感与虚假文采。你的唯一基准是【原文】：只报"生成正文里出现了、而原文对应场景不会这么写"的内容；原文风格如此的地方一律不算。

【AI 味典型症状】（参照，但不限于，其他不自然之处也要主动发现）：
1. 对白装饰三件套：每个对白轮前后都配"微动作+神态/感官词+副词"（例：「老刘开口，声音裹着深秋夜里的凉意」「林川抬了抬眼，指尖搭在桌沿轻轻敲击」「回答得干净利落」）。原文对白密集连续时，这些是额外注水，尤其要多报。
2. 感官/氛围词堆砌：凉意、夜色、缓缓、微微、轻轻、静默、氤氲等无信息量的氛围词密集。
3. 情绪明说：本应用动作/对白暗示的情绪被直接点破（「满意地点头」「惊喜道」「失落地」）。
4. 环境空镜头：插入与情节无关的景物/环境描写来填充篇幅。
5. 副词冗余：「回答得X」「X地开口」「X地说」。
6. 微动作逐帧特写：把一次动作拆成慢镜头（抬了抬眼→指尖轻敲→往后靠了靠）。
7. 心理独白堆砌：过多「他心想…他意识到…他觉得…」。
8. 套话过渡 / 悬念式旁白 / 总结性句子。
9. 排比句：三个及以上同构短句并列罗列（如「头顶悬着水晶吊灯，客厅摆放着X，茶几上摆放着Y，遗像前放着Z」）——原文是「房门打开便看见恢宏大气的挑高客厅与水晶吊灯，客厅里摆放着…」「然而客厅最显眼的茶几上，竟摆放着…」这种带转折、长短交错的写法，正文却写成机械排比时要报。
10. 句式杂糅：连续两句用同一句式、同一词收尾（如「…站在原木门牌前」「…站在一栋别墅的大门前」两句都以「前/门前」收尾），读起来别扭重复。
11. 物件清单罗列：把书籍/证书/物品堆成一长串「包括A、B和C，桌子放着D」的干瘪清单，且这些物件对当前冲突/人物塑造没有作用。
12. 剧情事实虚构（对照原文报）：正文出现了原文没有的人物身份/职业/背景/事件（如原文林川是被二叔二婶送进城西精神病院的学生，正文却写「跑去医院当全职医生」）——这类脑补必须报。
13. 动作流水账（对照原文报）：原文一句带过的概括动作（「翻翻找找清理」），正文却展开成逐微动作清单（「拉开抽屉→翻床底→摸床垫缝→开衣柜→扯衣服」）或否定式反复查找（「没找到…没找到…」）——原文详略被破坏、动作被注水拉长时必须报。
14. 无推进细节装饰（对照原文报）：正文出现了原文没有、且对情节/人物/冲突零作用的物件与环境描写（「踢脚线看不到积灰」「皮面没有开裂」「垃圾桶堆着废纸」）——纯粹凑篇幅，删掉不影响任何情节时必须报。
15. 说话动词机械化（对照原文报）：多轮对白说话动词被统一成「开口说」「说道」「说」，而原文对应轮次各有神态动词（王慧兰阴阳怪气道/陈硕得意洋洋/王慧兰喜滋滋挽住陈硕胳膊/道了一声晦气）——把原文丰富的说话方式拍平成占位词时必须报。
16. 内容重复叙述（对照原文报）：同一事实/闪回/内心独白在生成文内相隔较远重复出现（借钱往事说两遍、大嫂院子烧烤闪回两遍、找房本黄金翻多次）——原文只说一遍，正文复述凑字时必须报，同一信息只写一次。
17. 素材外事实漂移（对照原文报）：物件/数字被写到原文指定的位置/归属之外（录取通知书本在二楼卧室桌上，正文却写「一楼沙发上旧盒子里翻出录取通知书」；三千万本指父母留给林川的钱，正文却写「大哥手握三千万身家」）——把别的场景的素材挪用进来、或改挂归属时必须报。

【判定原则】
- 必须对照原文：原文同样密集、同样平实的写法不算 AI 味；原文没有、生成多出来的装饰才算。
- **逐字复现原文的句子一律不报**：即使该句读起来像套话/氛围句/总结句（如"他们只是在偏执的世界里与自我周旋""所以当他看到那张素描的时候…"），只要它出现在原文中（或与原句几乎一致），就是高保真复现，不是 AI 味。只报原文中不存在的额外内容。
- 引用精确：从生成正文原样摘出问题句（含标点），一条 findings 对应一处。
- 每条给具体改法（fix）：删除；或改为与原文一致的写法（如让对白直接接续、去掉动作+氛围三件套）。
- 不要惩罚合理的复现：原文如此的对白、动作、内容一概不报。
- 引号样式差异不算 AI 味：正文用“”而原文用「」只是引号样式不同（系统统一“”），不得扣分；只抓真正的 AI 味。
- 宁漏勿错：找不到就不报。只报确有把握的。
- 最多报告 8 条最有把握的 findings，按严重度从高到低排列。

【上报门槛（对标代码评审）】只有「原作者若知道了一定会想修」的问题才上报。仅凭猜测、属个人风格偏好、或需要未言明的假设才成立的，不要报。宁可一条都不报，也不要凑数。语气平实，不奉承、不夸张、不夸大严重度。
- 每条 finding 给 confidence_score（0-1）：你有多确定这是真的 AI 味而非原文本就如此或个人口味。低于 0.6 的不要写进 findings。
- priority：0=放下一切修（普遍性、不依赖具体输入的硬伤），1=本轮该修，2=有空再修，3=锦上添花。
- 不要写「写得很好」「总体不错」这类奉承；summary 只做客观判断。

【输出】只输出一个合法 JSON 对象，不要任何其他文字，不要 Markdown 代码块。控制体积：quote 摘核心片段不超过 24 字，fix 不超过 36 字：
{"ai_flavor_score": <0到1小数，1=完全无AI味，0=AI味极重>, "findings": [{"quote": "<片段>", "category": "对白装饰|感官堆砌|情绪明说|环境空镜头|副词冗余|微动作特写|心理独白|套话过渡|排比句|句式杂糅|物件罗列|剧情事实|动作流水账|细节装饰|说话动词|内容重复|事实漂移|其它", "severity": "high|medium|low", "confidence_score": <0到1小数，低于0.6不要报>, "priority": <0|1|2|3>, "fix": "<具体改法>"}], "summary": "<一句话客观总结，不奉承>"}"""


def _salvage_review_json(raw: str) -> dict | None:
    """doubao 长 JSON 畸形兜底：直接 loads → 断尾修剪 → 正则提取核心字段。

    doubao 对超长嵌套 JSON 会截断/畸形（v5.24 实测：缺逗号可修、断尾救不回）。
    salvage 拿不到完整 findings 时至少抠出 ai_flavor_score，让惩罚仍可生效。
    """
    raw = (raw or "").strip()
    if not raw:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass
    # 断尾修剪：从尾部找最后一个可能是"结构完整"的截断点
    for cut in range(len(raw), 0, -1):
        seg = raw[:cut].rstrip()
        if seg.endswith(("}", "]")) or seg.endswith(('"}', '"]')):
            try:
                obj = json.loads(seg)
                if isinstance(obj, dict):
                    return obj
            except json.JSONDecodeError:
                continue
    # 正则抠分：{"ai_flavor_score": 0.xx
    m = re.search(r'"ai_flavor_score"\s*[:：]\s*([0-9]*\.?[0-9]+)', raw)
    if m:
        try:
            return {"ai_flavor_score": float(m.group(1)), "findings": [], "summary": "salvaged"}
        except ValueError:
            pass
    return None


async def review_ai_flavor(generated: str, target: str) -> dict[str, Any]:
    """LLM 对照原文审阅生成正文的 AI 味。

    Returns:
        {score: 0-1, findings: [...], summary: str,
         ok: bool, error: str|None, gap: dict}  —— score 可能为 None（审阅失败）
    """
    gap = dialogue_gap_decoration(generated, target)
    # 【v5.32.5】确定性流水账检测（动作平铺/否定式反复/零推进细节）——对照版同样接入
    flow = flow_narration_detector(generated)
    if not generated or len(generated.strip()) < 100:
        return {"score": None, "findings": [], "summary": "",
                "ok": False, "error": "text too short", "gap": gap, "flow": flow}
    model = getattr(SETTINGS, "judge_model", "") or getattr(SETTINGS, "ark_model_pro", "")
    user = (
        "【原文】（正常写法基准）\n---\n" + (target or "")[:4000]
        + "\n---\n\n【生成正文】（审查对象）\n---\n" + (generated or "")[:6000]
        + "\n---\n\n请对照原文审阅生成正文的 AI 味，只输出 JSON。"
    )
    # 【v5.27.2】两次审阅取均值降噪声（实测同一文本全局分波动 ±0.3）。
    # 每次都跑（不再只在失败时重试）；分数取均值，findings 合并去重。
    # doubao 长 JSON 有畸形前科 → 紧凑输出 + max_tokens 加大 + 正则 salvage 兜底。
    scores: list[float] = []
    findings_raw: list[dict] = []
    last_err = "unknown"
    for _attempt in (0.3, 0.2):
        data = None
        try:
            resp = await chat_json(
                system=_sp.get_prompt("review_system_prompt", REVIEW_SYSTEM_PROMPT),
                user=user,
                model=model or None,
                temperature=float(_attempt),
                max_tokens=4000,
                call_type="ai_flavor_review",
            )
        except Exception as e:  # noqa: BLE001
            last_err = str(e)
            continue
        if not resp.get("error") and resp.get("data"):
            data = resp["data"]
        else:
            last_err = resp.get("error") or "bad json / empty"
            data = _salvage_review_json(resp.get("raw") or "")
        if data is None:
            continue
        try:
            s = round(max(0.0, min(1.0, float(data.get("ai_flavor_score")))), 4)
        except (TypeError, ValueError):
            s = None
        if s is not None:
            scores.append(s)
        for f in (data.get("findings") or []):
            if isinstance(f, dict) and str(f.get("quote") or "").strip():
                findings_raw.append(f)
    if not scores:
        # 两次审阅都拿不到分 → 退化为确定性 gap.clean（稳定、直击对话装饰）
        det = round(max(0.0, min(1.0, 0.6 * gap["clean"] + 0.4 * flow["flow_clean"])), 4)
        return {"score": det, "findings": [], "summary": "",
                "ok": False, "error": last_err, "gap": gap, "flow": flow}
    avg_score = sum(scores) / len(scores)
    # 【v5.27.2】融合权重 0.5/0.5：gap.clean 是确定性信号（直击"对话前后多余描写"）。
    # 【v5.32.5】把流水账确定性信号并进确定性侧：确定性 = 0.6×gap + 0.4×flow（总 LLM 主导不变）。
    det = round(max(0.0, min(1.0, 0.6 * gap["clean"] + 0.4 * flow["flow_clean"])), 4)
    fused = round(max(0.0, min(1.0, 0.5 * avg_score + 0.5 * det)), 4)
    # 规范化 findings（两次合并，按 quote 去重；置信度门槛过滤；同 quote 取最高置信度）
    by_quote: dict[str, dict] = {}
    for f in findings_raw[:40]:
        nf = _normalize_finding(f)
        if nf is None:
            continue
        q = nf["quote"]
        if q not in by_quote or nf["confidence"] > by_quote[q]["confidence"]:
            by_quote[q] = nf
    norm = sorted(by_quote.values(),
                  key=lambda x: (x["severity"] != "high", -x["confidence"]))[:16]
    return {
        "score": fused, "findings": norm,
        "summary": "",
        "ok": True, "error": None, "gap": gap, "flow": flow,
    }


# ── 反馈 / 指令构建 ───────────────────────────────────────

_FIX_VERB = {
    "对白装饰": "对白轮之间删除装饰性动作+氛围描写，直接接续对白",
    "感官堆砌": "删除无信息量的感官/氛围词堆砌",
    "情绪明说": "删掉直接点破情绪的措辞，让动作/对白本身传达",
    "环境空镜头": "删除与情节无关的环境填充句",
    "副词冗余": "删掉「X地」「回答得X」类冗余副词",
    "微动作特写": "把逐帧微动作压缩为一个动作或不写",
    "心理独白": "压缩心理独白，保留外部可观察的动作",
    "套话过渡": "删除套话式过渡与悬念旁白",
    "重复用词": "删除重复的引动词/动词/形容词，换用具体不重复的表述",
    "抽象概括": "把抽象标签改为具体可感的行为、对白或冲突",
    "对白无引号": "把无引号的裸对白行改为带“”引号并写明说话人的对白（如“钱呢”老刘伸出手）",
    "排比句": "打散同构并列句：把「头顶X，客厅Y，茶几Z」改写为长短句交错、带转折/因果的自然叙述",
    "句式杂糅": "改写连续同句式/同收尾的句子（如两句都以「前/门前」结尾），变换句式避免重复收尾",
    "物件罗列": "删除/精简无作用的物件清单（书籍/证书堆砌），只留对情节或人物有作用的一个物件，织进动作",
    "剧情事实": "删除素材里没有的人物身份/职业/背景/事件（如写林川当全职医生），改回素材设定（学生/精神病人）",
    "动作流水账": "把逐微动作清单合并为一次概括（如『整个二楼翻遍也没找着』），删除否定式反复查找；过程性动作一句话带过",
    "细节装饰": "删除对情节/人物/冲突零作用的物件与环境描写（踢脚线没积灰/皮面没开裂/垃圾桶堆废纸），保留有推进作用的细节",
    "说话动词": "把统一占位的「开口说/说道/说」逐轮还原为与说话人神态匹配的动词（阴阳怪气道/得意洋洋/喜滋滋/道了一声晦气），相邻对白轮不重复同一动词",
    "内容重复": "删除文内相隔较远重复出现的同一事实/闪回/内心独白（借钱往事只留一处、烧烤闪回只留一处、找房本黄金只翻一次），同一信息只写一次",
    "事实漂移": "把物件/数字改回素材指定的位置与归属（录取通知书只在二楼卧室桌、三千万=父母留给林川的钱），删除挪进本场景的素材外内容",
    "其它": "按 fix 修正",
}


def build_ai_flavor_feedback(findings: list[dict], *, cap_chars: int = 700) -> str | None:
    """把审阅 findings 拼成【AI味修正块】，注入下一次生成的 user_input。

    Findings 是"上一稿犯的错"，修正块是"下一稿必须避免"的指令。
    """
    high = [f for f in findings if f.get("severity") == "high"]
    rest = [f for f in findings if f.get("severity") != "high"]
    ordered = high + rest
    if not ordered:
        return None
    lines = ["【AI味修正】上一稿存在以下 AI 味问题，本次生成必须避免。逐条对照修正："]
    used = 0
    for f in ordered[:12]:
        cat = f.get("category", "其它")
        verb = _FIX_VERB.get(cat, "删除或按 fix 改写")
        quote = str(f.get("quote", "")).strip()
        fix = str(f.get("fix", "")).strip()
        line = f"- {verb}；原句：“{to_dialogue_quotes(quote)}”"
        if fix and fix != quote:
            line += f"→{fix}"
        if used + len(line) > cap_chars:
            break
        lines.append(line)
        used += len(line)
    if len(lines) == 1:
        return None
    lines.append("注意：AI味修正不得改变情节与对白内容本身，只去除多余的不自然描写。")
    return "\n".join(lines)


def build_gap_feedback(generated: str, target: str, *, cap: int = 6) -> str | None:
    """基于确定性对白间隙检测，判断装饰率是否明显高于原文，拼成修正块。

    相对式（不列具体句）：逐句列"装饰间隙"会把原文本身的引语（如「王慧兰喜滋滋的
    挽住陈硕胳膊」）误判成装饰 → 模型会误删原文内容伤复现。改为：只有 gen 装饰率
    明显高于原文（>1.3x）才触发，给通用"降到与原文同密度"指令，让模型自行判断。
    与 build_ai_flavor_feedback 互补：审阅器抓具体句子，这个抓结构性密度超标。
    """
    gen = _gap_stats(generated or "", reference=target or "")
    tgt = _gap_stats(target or "")
    if gen["n_gaps"] == 0 or gen["deco_rate"] <= tgt["deco_rate"] * 1.3:
        return None  # 装饰率不高于原文 1.3 倍 → 干净
    return (
        "【对白间隙修正】你的对白间隙装饰率（%.0f%%，%d 个间隙）明显高于原文（%.0f%%）。"
        "原文对白密集、间隙叙述极少；删除多余的动作+神态+感官装饰描写，让对白直接接续，"
        "与原文同一密度。只保留原文本身有的信息性动作（如推眼镜、划去重写），"
        "不要为每个对白轮配动作描写。" % (
            gen["deco_rate"] * 100, gen["n_gaps"], tgt["deco_rate"] * 100)
    )


def build_verbatim_feedback(findings: list[dict]) -> str | None:
    """从审阅 findings 的 fix 提取原文写法，作为「必须逐字复现」要求。

    审阅 fix 常为「改为原文写法：XXX」「与原文一致：XXX」——里面嵌着原文句子。
    对"转述代替逐字"的顽固问题（ch5 同僚等），把原文句直接塞回生成要求，
    比让模型自行回想原文更可靠。
    """
    targets = []
    for f in findings:
        fix = str(f.get("fix", "")).strip()
        if not fix:
            continue
        m = re.search(r'(?:原文|一致|原文写法)[：:]\s*(.{4,80})', fix)
        tgt = m.group(1).strip() if m else None
        if not tgt:
            continue
        tgt = tgt.rstrip('。」！？，,')[:50]
        if len(tgt) < 4:
            continue
        targets.append(tgt)
    if not targets:
        return None
    # 去重（保留顺序）
    seen = set()
    uniq = []
    for t in targets:
        if t not in seen:
            seen.add(t)
            uniq.append(t)
    if not uniq:
        return None
    lines = ["【原文逐字复现】以下句子是原文写法，正文中对应处必须逐字采用（不是转述、不得改写）："]
    for t in uniq[:8]:
        lines.append(f"- “{to_dialogue_quotes(t)}”")
    lines.append("注意：只在对应情节处采用，不得改变情节顺序。")
    return "\n".join(lines)


def ai_flavor_directives(findings: list[dict], ch_idx: int) -> list[dict]:
    """审阅 findings → improvement_context 扣分指令（kind='ai_flavor'）。"""
    out = []
    for f in findings:
        if f.get("severity") not in ("high", "medium"):
            continue
        cat = f.get("category", "其它")
        verb = _FIX_VERB.get(cat, "删除或改写")
        quote = str(f.get("quote", "")).strip()
        fix = str(f.get("fix", "")).strip()
        text = f"{verb}：“{to_dialogue_quotes(quote)}”" if quote else verb
        if fix and fix != quote:
            text += f"→{fix}"
        out.append({"kind": "ai_flavor", "text": text[:120],
                    "quote": quote, "fails": {ch_idx}})
    return out


# ═══════════════════════════ v5.30 共享 AI 味防线（系统级） ═══════════════════════════
# 目的（用户 2026-08-06）：推导树生成/推导正文/所有「文字→文字」环节都要 LLM 参与
# 检查 AI 味并杜绝。本段提供三件共享机制，供复现路径、推导路径、独立生成统一复用：
#   ① 生成端 AI 味禁令块（对照版/独立版，单一来源——plot_skeleton 改为引用这里）；
#   ② 无原文对照审阅 review_ai_flavor_standalone（树内容/独立操作，无 target 场景）；
#   ③ 通用重生成循环 ai_flavor_regenerate_loop（审阅→脏→修正块注入重生成→再审阅）。


# ── ① 生成端禁令（对照版 = 复现路径；独立版 = 推导/独立生成）────────────────────

# 对照版：复现路径（有原文基准）。保留【原文句参考】语义、对白顺序契约、符号原样约定。
AI_FLAVOR_BAN_BLOCK = """【AI味禁令】（硬性，必须遵守——原文对白密集、叙述克制，正文必须与原文同一节奏）
【最高优先·禁止防御性写作】不解释：读者能懂的不解释、该猜的留白，不写分析性旁白/总结性升华（「这说明…」「换句话说…」「这等于…」「说白了…」）；不周全：不把一件事的正反、来龙去脉全写尽（「说它弱吧…说它强吧…」），留一半给读者自己品；不铺垫：转折前不先写一堆说服读者的解释，直接发生；不辩护：不预先堵每个可能被挑刺的点，不写「毕竟/幸好/原来」自圆其说。正文像原文一样信任读者——有省略，有余地。原文若没有这类解说，正文一律不写。
【标点·硬约束】禁止破折号「——」（同一段两处及以上、解释性/强调性破折号都是 AI 味；需补充说明用逗号、需转折直接句号断句）；禁止省略号连用「……」；禁止「不是……而是……」「不是……是……」判断句、禁止「……是……的」强调结构、禁止「并非/绝非」，一律改为直接陈述。
【Markdown·硬约束】正文/回复是小说文本，不得出现 Markdown 语法标记（**、*、#、>、- 列表）。
1. 禁止「动作+神态+感官」三件套：对白轮前不得堆砌「他开口，声音裹着凉意」「抬了抬眼，指尖轻敲」这类微动作+氛围描写。原文里连续多轮对白是直接接续的，正文也必须直接接续。
2. 禁止重复引动词（含占位模板）：「开口开口问道」「说道着说」等；**不得把多轮对白的说话动词统一占位成「开口说」「说道」「说」**——说话动词/神态逐轮用素材原词（阴阳怪气道/喜滋滋/得意洋洋），相邻对白轮不重复同一动词。
3. 禁止逐帧微动作特写：不得把一次动作拆成多个慢镜头（抬了抬眼→指尖轻敲→往后靠）。
4. 禁止无信息量氛围词堆砌：凉意、夜色、缓缓、微微、轻轻、氤氲等，除非原文如此。
5. 禁止情绪明说：用动作/对白暗示情绪，不写「满意地点头」「失落地」。
6. 禁止心理独白：不写「他心想…」「他心里怀疑…」「他意识到…」——原文用「？？？」或直接对白表达内心，正文照此。
7. 禁止环境空镜头：不写与情节无关的景物填充句。
8. 禁止冗余副词：「回答得X」「X地开口」。
9. 禁止对白错序：正文对白必须严格按骨架与【对白顺序契约】中的顺序出现，不得提前或延后。
10. 只加有信息量的动作（原文本就有的）：推了推眼镜、划去重写、从兜里掏出手机递给——这些可以，它们是情节的一部分。
11. 原文用「……」表示沉默、用「？？？」表示无语、用「啊？」表示惊讶时，正文必须原样用这些符号/叹词表达，不得改写成「愣住」「没有说话」「没说话」「停住动作」等叙述。
12. 对白引号统一（不可变原则）：正文对白一律用中文弯引号“”包裹，不用「」『』；即使原文用「」，正文也统一转成“”。（引号样式不影响复现评分。）
13. 禁止排比句：不得把三个及以上同构短句并列罗列（如「头顶悬着水晶吊灯，客厅摆放着X，茶几上摆放着Y，遗像前放着Z」）。改写为长短句交错的自然叙述，与原文句式一致——原文是「房门打开便看见恢宏大气的挑高客厅与水晶吊灯，客厅里摆放着…」「然而…竟…」这种带转折/长短变化的写法，正文照此，不机械排比。
14. 禁止句式杂糅：连续两句不得用同一句式、同一词收尾（如「…站在原木门牌前」「…站在一栋别墅的大门前」两句都以「门前/前」收尾）。变换句式，避免重复收尾。
15. 禁止物件清单罗列：不把书籍/证书/奖状/物品堆成一长串「包括A、B和C，桌子放着D」的干瘪清单。物件在动作/对白中自然带出，或只提一个关键物。
16. 禁止虚构素材外剧情事实：人物身份/职业/背景/关系/来历必须来自素材（原文/骨架/场景叶子）。素材没说林川是医生，就不得写「跑去医院当全职医生」「白大褂」这类脑补；素材说他是学生/精神病人就写学生/精神病人。只呈现素材已有的信息，不补人物来历。
17. 禁止动作流水账/平铺：原文一句带过的概括动作（如「陈硕与王慧兰在房间里翻翻找找，并将林川一家人的东西清理出来扔掉」）就照原文一句话带过；不得展开成「拉开抽屉→翻床底→摸床垫缝→开衣柜→扯衣服」逐微动作清单；不得「翻A→没找到→翻B→没找到」否定式反复。过程性动作一句话概括。
18. 禁止无推进细节装饰：不得写对情节/人物/冲突零作用的物件与环境细节（「踢脚线看不到积灰」「皮面没有开裂」「垃圾桶堆着废纸」「扶手擦得光亮能映出人影」）；原文没有的这类装饰一律不写，细节必须来自原文素材且对叙事有推进。

正确示例（原文就长这样，正文照此直接接续、零装饰；对白用“”）：
“你想结束生命吗”
“……结束谁的生命”
“你自己的”
“那没有”
错误示例（AI 味，绝对禁止）：
老刘开口，声音裹着深秋夜里的凉意。“你想结束生命吗”
林川抬了抬眼，指尖搭在桌沿轻轻敲击。“……结束谁的生命”"""


# 独立版：无原文基准的生成（极简推导正文/剧情素材/独立生成），去掉「原文」对照语义。
AI_FLAVOR_BAN_BLOCK_STANDALONE = """【AI味禁令】（硬性，必须遵守）
【最高优先·禁止防御性写作】不解释：读者能懂的不解释、该猜的留白，不写分析性旁白/总结性升华（「这说明…」「换句话说…」「这等于…」「说白了…」）；不周全：不把一件事的正反、来龙去脉全写尽（「说它弱吧…说它强吧…」），留一半给读者自己品；不铺垫：转折前不先写一堆说服读者的解释，直接发生；不辩护：不预先堵每个可能被挑刺的点，不写「毕竟/幸好/原来」自圆其说。像真实的人在讲一件自己确信的事——有省略，有余地，有信任。
【标点·硬约束】禁止破折号「——」（同一段两处及以上、解释性/强调性破折号都是 AI 味；需补充说明用逗号、需转折直接句号断句）；禁止省略号连用「……」；禁止「不是……而是……」「不是……是……」判断句、禁止「……是……的」强调结构、禁止「并非/绝非」，一律改为直接陈述。
【Markdown·硬约束】正文/回复是小说文本，不得出现 Markdown 语法标记（**、*、#、>、- 列表）。
1. 禁止「动作+神态+感官」三件套：对白轮前不得堆砌「他开口，声音裹着凉意」「抬了抬眼，指尖轻敲」这类微动作+氛围描写。对白直接接续，不要逐轮配动作；**对白一律用中文弯引号“”包裹并写明说话人**（不用「」），禁止无引号的裸对白行。
2. 禁止重复引动词（含占位模板）：「开口开口」「说道着说」等；**不得把多轮对白的说话动词统一占位成「开口说」「说道」「说」**——说话动词/神态逐轮用素材原词（阴阳怪气道/喜滋滋/得意洋洋），相邻对白轮不重复同一动词。
3. 禁止逐帧微动作特写：不得把一次动作拆成多个慢镜头（抬了抬眼→指尖轻敲→往后靠）。
4. 禁止无信息量氛围词堆砌：凉意、夜色、缓缓、微微、轻轻、氤氲、无声等。
5. 禁止情绪明说：用动作/对白暗示情绪，不写「满意地点头」「失落地」。
6. 禁止心理独白堆砌：不写「他心想…」「他心里怀疑…」「他意识到…」——让对白与动作传达内心，叙述克制。
7. 禁止环境空镜头：不写与情节无关的景物填充句。
8. 禁止冗余副词：「回答得X」「X地开口」。
9. 禁止套话过渡/总结性旁白/悬念式收尾/升华句。
10. 每个动作、细节都要有信息量（推动情节或塑造人物），宁缺毋滥。
11. 禁止排比句：不得把三个及以上同构短句并列罗列（如「头顶悬着水晶吊灯，客厅摆放着X，茶几上摆放着Y，遗像前放着Z」）。改写为长短句交错、句式有变化的自然叙述。
12. 禁止句式杂糅：连续两句不得用同一句式、同一词收尾（如「…站在原木门牌前」「…站在一栋别墅的大门前」都以「前/门前」收尾）。变换句式。
13. 禁止物件清单罗列：不把书籍/证书/物品堆成一长串「包括A、B和C，桌子放着D」的干瘪清单。物件在动作/对白中自然带出，或只提一个关键物。
14. 禁止虚构素材外剧情事实：人物身份/职业/背景/关系/来历必须来自素材。素材没说林川是医生，就不得写「跑去医院当全职医生」「白大褂」这类脑补；只呈现素材已有的信息，不补人物来历。
15. 禁止动作流水账/平铺：不得把概括动作（翻找/收拾/走路）展开成逐微动作清单（「拉开抽屉→翻床底→摸床垫缝→开衣柜→扯衣服」）；不得「翻A→没找到→翻B→没找到」否定式反复；过程性动作一句话带过（「把能翻的地方都翻遍了，什么也没找到」）。一个场景聚焦 1-2 个重点，其余一带而过。
16. 禁止无推进细节装饰：不得写对情节/人物/冲突零作用的物件与环境细节（「踢脚线看不到积灰」「皮面没有开裂」「垃圾桶堆着废纸」「扶手擦得光亮能映出人影」）；细节必须推动情节、刻画人物或引向冲突。

正确示例（直接、具体、零装饰；对白用“”）：
“钱呢”老刘伸出手。
林川没接话，把信封拍在桌上。
错误示例（AI 味，绝对禁止）：
老刘开口，声音裹着深秋夜里的凉意。“钱呢”他缓缓伸出手，指尖搭在桌沿轻轻敲击。
林川沉默片刻，心中泛起一阵复杂的情绪，最终只是不自然地笑了笑。"""


def ai_flavor_ban_block(*, standalone: bool = False) -> str:
    """返回生成端 AI 味禁令块：standalone=True 用无原文基准版（推导/独立生成），否则对照版。

    文本经 scorer_params 可被 Phase 2 训练覆写；缺省回退内置常量。
    """
    if standalone:
        return _sp.get_prompt("ai_flavor_ban_block_standalone", AI_FLAVOR_BAN_BLOCK_STANDALONE)
    return _sp.get_prompt("ai_flavor_ban_block", AI_FLAVOR_BAN_BLOCK)


# 推导树专用禁令（6 类叶子的措辞层要求——不消灭心理/冲突叶子本身，只禁措辞污染）。
TREE_AI_FLAVOR_BAN = """【AI味禁令】（硬性，必须遵守——剧情素材措辞层）
1. 对白叶子：必须带“”引号并写清说话人；禁止重复引动词（「开口开口」「说道着说」）；禁止说教式独白。
2. 动作叶子：禁止「动作+神态+感官」三件套与逐帧慢镜头（抬了抬眼→指尖轻敲→往后靠）；只写有信息量的动作。
3. 心理叶子：写具体、有信息量的内心状态；禁止套话式标签（「心头一暖」「心中暗喜」「一种说不清道不明的感觉」）。
4. 环境叶子：禁止空镜头氛围堆砌（凉意、夜色、缓缓、微微、氤氲等无信息量词）；环境必须与情节/人物关联。
5. 细节叶子：禁止空洞凑数（「墙角有张旧桌子」这类与情节无关的填充）；细节要能复用/呼应。
6. 冲突叶子：禁止抽象概括（「矛盾激化」「各怀心思」）代替具体冲突；冲突要写清双方与进展。
7. 全局：禁止重复用词、套话过渡、总结性/升华句；场景名不要带「>」「-」等前缀符号。"""


def tree_ai_flavor_ban() -> str:
    """推导树禁令文本（经 scorer_params 可训练）。"""
    return _sp.get_prompt("tree_ai_flavor_ban", TREE_AI_FLAVOR_BAN)


# ── ② 无原文对照审阅（树内容 / 独立操作）────────────────────────────

STANDALONE_REVIEW_SYSTEM_PROMPT = """你是一位苛刻的中文网文审稿人，专门识别「AI 味」——语言模型生成文本时特有的机械感与虚假文采。没有原文对照，你根据文本自身的特征判断。

【AI 味典型症状】（参照，但不限于，其他不自然之处也要主动发现）：
0. 【最高优先·防御性写作】解释性旁白/自我周全/把话说尽/总结性分析——怕读者看不懂的解说（「这话等于…」「换句话说…」「说白了…」「这等于…」）、正反都写全的周全（「说它弱吧…说它强吧…」）、把来龙去脉/前因后果全交代的分析、收尾的升华/口号，都是防守姿态（写作时想着堵每个漏洞、证明自己周全）。发现必报，severity 至少 high。同时：破折号「——」密集（同一段两处及以上、解释性/强调性破折号）、Markdown 标记混入（**、*、#）也是 AI 味，必报 high。
1. 对白装饰三件套：每个对白轮前后配「微动作+神态/感官词+副词」（「他开口，声音裹着凉意」「抬了抬眼，指尖轻敲」）。
2. 感官/氛围词堆砌：凉意、夜色、缓缓、微微、轻轻、静默、氤氲等无信息量的氛围词密集。
3. 情绪明说：直接点破情绪（「满意地点头」「惊喜道」「失落地」）。
4. 环境空镜头：与情节无关的景物/环境描写填充篇幅。
5. 副词冗余：「回答得X」「X地开口」「X地说」。
6. 微动作逐帧特写：一次动作拆成慢镜头（抬了抬眼→指尖轻敲→往后靠）。
7. 心理独白堆砌：过多「他心想…他意识到…他觉得…」直接宣示内心。
8. 重复引动词/用词重复：「开口开口」「说道着说」；同一动词/形容词连篇复用。
9. 套话过渡/总结性旁白/悬念式收尾/口号式升华。
10. 抽象概括代替具体：用「气氛紧张」「各怀心思」等标签代替具体可感的行为与对白。
11. 对白未带引号或无说话人：对白写成无引号的裸行，或堆在叙述里分不出谁说的；正文对白必须带引号（统一用“”）并尽量写明说话人。引号样式（“”/「」）不同不是 AI 味，不得因样式扣分。
12. 排比句：三个及以上同构短句并列罗列（如「头顶悬着水晶吊灯，客厅摆放着X，茶几上摆放着Y，遗像前放着Z」），句式机械无变化。
13. 句式杂糅：连续两句用同一句式、同一词收尾（如「…站在原木门牌前」「…站在一栋别墅的大门前」都以「前/门前」收尾），读起来别扭重复。
14. 物件清单罗列：把书籍/证书/物品堆成一长串「包括A、B和C，桌子放着D」的干瘪清单，对当前情节/人物塑造没有作用。
15. 剧情事实虚构：出现了素材/上下文里没有的人物身份、职业、背景、事件（如素材说林川是学生，正文却写「跑去医院当全职医生」）——凭空脑补人物来历。对照素材/前后文判断。
16. 动作流水账：把概括动作展开成逐微动作清单（「拉开抽屉→翻床底→摸床垫缝→开衣柜→扯衣服」），或「翻A→没找到→翻B→没找到」否定式反复查找——读起来像流程记录、没有主次与节奏。正常写法是过程性动作一句话带过。
17. 无推进细节装饰：对情节/人物/冲突零作用的物件与环境细节堆砌（「踢脚线看不到积灰」「皮面没有开裂」「垃圾桶堆着废纸」「扶手擦得光亮能映出人影」）——为了填充篇幅而写，删掉不影响任何情节。
18. 说话动词机械化：多轮对白说话动词被统一成「开口说」「说道」「说」占位，缺乏神态变化（如原文该有的阴阳怪气道/得意洋洋/喜滋滋都被拍平）——读起来像同一条生产线拼装。说话动词应有变化、与说话人神态匹配。
19. 内容重复叙述：同一事实/闪回/内心独白在文内相隔较远重复出现（借钱往事说两遍、院子烧烤闪回两遍、找房本黄金翻多次）——同一信息只写一次，复述是凑字。
20. 素材外事实漂移：物件/数字被写到素材指定的位置/归属之外（录取通知书本在二楼卧室桌，被搬到一楼旧盒子；三千万本指父母留给林川的钱，被安到大哥身家）——物件/数字必须待在素材给定的位置与归属。
21. 【v6.4.1】过场/细节不断展开：把该一笔带过的东西逐条展开——走路/赶路/查看装备（更梆裂纹、灯罩油烟、扶灯、踢开挡路物）、感官氛围（余温透鞋底、夜色、水汽）、物件清单（柜台上摆着X、留着Y、带着Z）连写多句。**这是网文里特别严重的缺陷**：连续多条环境空镜头/微动作特写/细节装饰/动作流水账=过场展开，一条 finding 报连续段、severity 至少 medium。正确写法：过场动作一句话带过、固定装备除非当下信息相关不描写、感官/物件细节删掉或压缩。

【判定原则】
- 只报确有把握的机械感/虚假文采，宁漏勿错；找不到就不报。
- 引用精确：从待审文本原样摘出问题片段（含标点），一条 finding 对应一处。
- 每条给具体改法（fix）：删除；或改为具体、直接、有信息量的写法。
- 最多报告 8 条最有把握的 findings，按严重度从高到低排列。

【上报门槛（对标代码评审）】只有「原作者若知道了一定会想修」的问题才上报。仅凭猜测、属个人风格偏好、或需要未言明假设才成立的，不要报。没有原文对照时尤需克制：网文本身允许密集动作/对白/短句，别把正常写法误判成 AI 味。宁可一条不报，也不要凑数。语气平实，不奉承、不夸大严重度。
- 每条 finding 给 confidence_score（0-1）：你有多确定这是真 AI 味。低于 0.6 不要写进 findings。
- priority：0=放下一切修（普遍性硬伤），1=本轮该修，2=有空再修，3=锦上添花。
- summary 只做客观判断，不写「整体不错」「写得很好」这类奉承。

【输出】只输出一个合法 JSON 对象，不要任何其他文字，不要 Markdown 代码块。控制体积：quote 摘核心片段不超过 24 字，fix 不超过 36 字：
{"ai_flavor_score": <0到1小数，1=完全无AI味，0=AI味极重>, "findings": [{"quote": "<片段>", "category": "对白装饰|感官堆砌|情绪明说|环境空镜头|副词冗余|微动作特写|心理独白|重复用词|套话过渡|抽象概括|对白无引号|排比句|句式杂糅|物件罗列|剧情事实|动作流水账|细节装饰|说话动词|内容重复|事实漂移|其它", "severity": "high|medium|low", "confidence_score": <0到1小数，低于0.6不要报>, "priority": <0|1|2|3>, "fix": "<具体改法>"}], "summary": "<一句话客观总结，不奉承>"}"""


async def review_ai_flavor_standalone(text: str, *, max_chars: int = 9000) -> dict[str, Any]:
    """无原文对照的 AI 味审阅（推导树叶子内容/独立生成等）。

    Returns: {score, findings, summary, ok, error} —— score 可能为 None（审阅失败）。
    """
    if not text or len(str(text).strip()) < 60:
        return {"score": None, "findings": [], "summary": "", "ok": False,
                "error": "text too short"}
    text = str(text).strip()[:max_chars]
    # 【v5.32.5】确定性流水账检测（动作平铺/否定式反复/零推进细节）——LLM 失败降级 + 融合信号
    flow = flow_narration_detector(text)
    # 【v6.4.1】过场/细节不断展开检测（通用非书本化）——同样作为确定性融合信号
    overdetail = overdetail_detector(text)
    # 【v7.2.5】破折号/Markdown 密度检测——确定性兜底（LLM 审阅易漏破折号）
    punct = punct_detector(text)
    model = getattr(SETTINGS, "judge_model", "") or getattr(SETTINGS, "ark_model_pro", "")
    user = (
        "【待审文本】（审查对象，可能包含场景/动作/对白/心理/冲突/细节等多类素材）\n---\n"
        + text + "\n---\n\n请审阅这段文本的 AI 味，只输出 JSON。"
    )
    resp = None
    try:
        resp = await chat_json(
            system=_sp.get_prompt("standalone_review_system_prompt", STANDALONE_REVIEW_SYSTEM_PROMPT),
            user=user,
            model=model or None,
            temperature=0.2,
            max_tokens=3000,
            call_type="ai_flavor_review_standalone",
        )
    except Exception as e:  # noqa: BLE001
        return {"score": flow["flow_clean"], "findings": [], "summary": "",
                "ok": False, "error": str(e), "flow": flow, "overdetail": overdetail}
    data = resp.get("data") if not resp.get("error") else None
    if data is None:
        data = _salvage_review_json(resp.get("raw") or "")
    if data is None:
        return {"score": flow["flow_clean"], "findings": [], "summary": "",
                "ok": False, "error": resp.get("error") or "bad json", "flow": flow,
                "overdetail": overdetail}
    try:
        s = round(max(0.0, min(1.0, float(data.get("ai_flavor_score")))), 4)
    except (TypeError, ValueError):
        s = None
    findings: list[dict[str, str]] = []
    for f in (data.get("findings") or []):
        nf = _normalize_finding(f)
        if nf is not None:
            findings.append(nf)
    findings = sorted(findings,
                      key=lambda x: (x["severity"] != "high", -x["confidence"]))[:16]
    if s is None:
        return {"score": flow["flow_clean"], "findings": findings,
                "summary": str(data.get("summary", ""))[:200],
                "ok": False, "error": "no score", "flow": flow, "overdetail": overdetail}
    # 【v5.32.5】流水账确定性融合：LLM 为主，flow 兜底。v6.4.1 并入过场展开信号。
    # v7.2.5 并入破折号/Markdown 信号（权重低，主要靠调用方 punct 独立门槛）。
    fused = round(max(0.0, min(1.0, 0.78 * s + 0.08 * flow["flow_clean"]
                               + 0.08 * overdetail["overdetail_score"]
                               + 0.06 * punct["punct_score"])), 4)
    return {"score": fused, "findings": findings,
            "summary": str(data.get("summary", ""))[:200],
            "ok": True, "error": None, "flow": flow, "overdetail": overdetail,
            "punct": punct}


# ── ③ 通用 AI 味重生成循环（复现路径 / 推导管线 / 独立生成统一复用）────────────────

async def ai_flavor_regenerate_loop(
    *,
    generated: str,
    user_input: str,
    system_prompt: str,
    gen_params: dict[str, Any] | None = None,
    target_text: str | None = None,
    retry_cap: int = 2,
    dirty_threshold: float | None = None,
    gap_clean_thresh: float | None = None,
) -> tuple[str, dict[str, Any] | None, int]:
    """通用 AI 味重生成循环：审阅 → 脏 → 修正块注入 user_input 重生成 → 再审阅。

    target_text 提供时用「对照原文」审阅（复现语义：原文同节奏/逐字复现）；
    为 None 时用「无原文对照」审阅（推导/独立生成语义）。

    Returns: (最终正文, 最后一次审阅结果, 实际重试次数)。审阅失败时保持原文并
    返回审阅结果（调用方用于评分透出）。
    """
    from .optimizer import forward_generation_v4

    if dirty_threshold is None:
        dirty_threshold = _sp.get_threshold("regen_dirty_threshold", 0.65)
    if gap_clean_thresh is None:
        gap_clean_thresh = _sp.get_threshold("regen_gap_clean_thresh", 0.82)

    def _review(t: str):
        if target_text:
            return review_ai_flavor(t, target_text)
        return review_ai_flavor_standalone(t)

    res = await _review(generated)
    retried = 0
    while retried < retry_cap and res is not None and not res.get("error"):
        findings = res.get("findings") or []
        high = [f for f in findings if f.get("severity") == "high"]
        gap_clean = (res.get("gap") or {}).get("clean", 1.0)
        fused = res.get("score") or 1.0
        gap_fb = build_gap_feedback(generated, target_text) if target_text else None
        # 触发条件：①实际装饰性对白间隙 ②gap.clean 低 ③high findings ④融合分低（两信号都差）
        if not high and not gap_fb and gap_clean >= gap_clean_thresh \
                and fused >= dirty_threshold:
            break  # 干净
        parts: list[str] = []
        llm_fb = build_ai_flavor_feedback(high if high else findings)
        if llm_fb:
            parts.append(llm_fb)
        if gap_fb:
            parts.append(gap_fb)
        if not parts:
            break
        fb = "\n\n".join(parts)
        new_user = user_input.rstrip() + "\n\n" + fb
        params = dict(gen_params or {})
        params["temperature"] = min(0.5, (params.get("temperature", 0.3) or 0.3) + 0.05)
        try:
            generated = await forward_generation_v4(
                new_user, gen_params=params, system_prompt=system_prompt)
        except Exception:  # noqa: BLE001
            break
        retried += 1
        res = await _review(generated)
    return generated, res, retried
