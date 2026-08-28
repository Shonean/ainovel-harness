# -*- coding: utf-8 -*-
"""趋势/读者吸引力词库（Phase B）：官方短剧标签体系 → 多维 appeal。

设计要点（2026-08-15 用户反馈后修正）：
- 词库 = 短剧平台官方标签（背景/主题/设定/受众 四维），不用手搓词表
- appeal = 按维度打分（文本在哪个维度更贴近当前流行），**不做排序偏好**
  （避免 0.8sim+0.2appeal 导致全部收敛到重生/系统/打脸 → 同质化）
- 用法：is_outdated() 淘汰全维度都不流行的模板；四维 profile 暴露给创作助手生成时参考

维度与标签来自用户提供的短剧筛选栏（最新/最热标签）。
"""
import json
from pathlib import Path

from .embed_client import get_embeddings, cosine_similarity

_CORPUS_DIR = Path(__file__).resolve().parent.parent / "corpus"

# 官方短剧标签体系（用户提供，2026-08-15）
TREND_CATEGORIES: dict[str, list[str]] = {
    "背景": ["现代", "都市", "古代", "乡村", "年代", "架空", "职场", "民国", "校园", "宫廷", "荒岛"],
    "主题": ["现言", "女性成长", "脑洞", "奇幻", "玄幻", "古言", "战神", "宫斗", "仙侠", "权谋",
            "种田", "年代爱情", "悬疑", "喜剧", "青春", "志怪", "民国爱情", "灵异", "家国情怀",
            "法律", "刑侦", "抗战", "武侠", "民国传奇", "求生", "动作", "科幻", "恐怖", "商战"],
    "设定": ["打脸虐渣", "大男主", "大女主", "马甲", "重生", "穿越", "系统", "先婚后爱", "家长里短",
            "小人物", "破镜重圆", "神豪", "豪门", "强者回归", "异能", "虐恋", "传承觉醒", "医生",
            "强强联合", "赘婿逆袭", "甜宠", "娱乐圈", "神医", "青梅竹马", "姐弟恋", "玄学",
            "追妻火葬场", "业界精英", "一见钟情", "福宝", "捞偏门", "反派主角", "萌宠",
            "双向救赎", "方言", "白月光", "灵魂互换", "病娇", "暴富", "黑道", "丧尸", "特种兵"],
    "受众": ["男频", "女频"],
}
OUTDATED_FLOOR = 0.32   # 某维度低于此视为该维度不流行

# 题材检测：从弧 l1/风格/角色设定 关键词 → 主题维度（题材决断用）
GENRE_KEYWORDS: dict[str, list[str]] = {
    "仙侠": ["修仙", "仙侠", "宗门", "灵根", "炼丹", "修士", "飞升", "元婴", "金丹",
            "魔门", "苟道", "法宝", "灵兽", "秘境", "结丹", "筑基"],
    "玄幻": ["玄幻", "斗气", "武魂", "异世", "大陆", "武者", "斗者", "血脉"],
    "古言": ["古代", "古言", "宅斗", "侯门", "将军", "王爷", "世家", "大宅", "闺阁", "嫡庶"],
    "宫斗": ["宫斗", "皇后", "妃", "宫", "太后", "夺嫡", "皇上", "嫔", "选秀", "冷宫"],
    "权谋": ["权谋", "朝堂", "谋士", "争权", "庙堂", "权臣", "官场", "政斗"],
    "都市": ["都市", "职场", "商战", "总裁", "豪门", "娱乐圈", "神医", "律师", "医生"],
    "民国": ["民国", "军阀", "上海滩", "租界", "旗袍", "战乱"],
    "科幻": ["科幻", "星际", "机甲", "末世", "外星", "未来", "飞船"],
    "悬疑": ["悬疑", "刑侦", "破案", "侦探", "诡异", "凶案", "谜案"],
    "年代": ["年代", "知青", "七零", "八零", "乡村", "朴实"],
}


def detect_genre(text: str) -> str:
    """从文本关键词检测题材（映射到主题维度）。未命中返回空串。"""
    text = (text or "")
    for genre, kws in GENRE_KEYWORDS.items():
        if any(kw in text for kw in kws):
            return genre
    return ""


def trend_categories() -> dict[str, list[str]]:
    return TREND_CATEGORIES


def _cat_terms(cat: str) -> list[str]:
    return TREND_CATEGORIES.get(cat) or []


_CAT_EMB: dict[str, dict[str, list[float]]] = {}


async def _cat_embeddings(cat: str) -> dict[str, list[float]]:
    if cat in _CAT_EMB and _CAT_EMB[cat]:
        return _CAT_EMB[cat]
    terms = _cat_terms(cat)
    try:
        vecs = await get_embeddings(terms)
        _CAT_EMB[cat] = {t: list(v) for t, v in zip(terms, vecs)}
    except Exception:
        _CAT_EMB[cat] = {}
    return _CAT_EMB[cat]


async def appeal_profile(text: str, top_k: int = 2, focus: str = "") -> dict[str, float]:
    """文本在各维度的流行度得分（0-1）。focus=题材（detect_genre 输出）时加权该题材所在维度。"""
    text = (text or "").strip()
    if len(text) < 10:
        return {c: 0.5 for c in TREND_CATEGORIES}
    try:
        tvec = (await get_embeddings([text]))[0]
    except Exception:
        return {c: 0.5 for c in TREND_CATEGORIES}
    return await appeal_profile_from_vec(tvec, top_k=top_k, focus=focus)


async def appeal_profile_from_vec(vec: list[float], top_k: int = 2, focus: str = "") -> dict[str, float]:
    """复用已有文本向量评多维 appeal（零额外嵌入调用）。focus=题材时加权该题材所在维度。"""
    out: dict[str, float] = {}
    for cat in TREND_CATEGORIES:
        cache = await _cat_embeddings(cat)
        if not cache:
            out[cat] = 0.5
            continue
        try:
            sims = [cosine_similarity(list(vec), v) for v in cache.values()]
        except Exception:
            out[cat] = 0.5
            continue
        if not sims:
            out[cat] = 0.5
            continue
        sims.sort(reverse=True)
        avg = sum(sims[:top_k]) / min(top_k, len(sims))
        out[cat] = max(0.0, min(1.0, (avg - 0.3) / 0.5))
    # 题材条件化：focus 命中某题材时，主题维度（题材所在）加权，凸显"在本题材内的热度"
    if focus:
        out["主题"] = max(out.get("主题", 0.5), 0.55)  # 题材匹配 → 主题维度至少不弱
    return out


async def is_outdated(profile: dict[str, float]) -> bool:
    """所有维度都不流行（都低于下限）→ 过时，应淘汰。任一维度过线即保留。"""
    vals = [profile.get(c, 0.5) for c in TREND_CATEGORIES]
    return all(v < OUTDATED_FLOOR for v in vals)


async def top_category(profile: dict[str, float]) -> str:
    """文本最贴近的维度（创作助手生成时参考）。"""
    return max(profile, key=lambda c: profile.get(c, 0.0)) if profile else ""


# ── T8 场景决断：生成端注入流行钩子（静态，零额外 LLM 成本）──────────
# 题材 → 当前读者爱看的钩子（从官方短剧标签「设定」维度按题材筛选）
GENRE_HOOK_TAGS: dict[str, list[str]] = {
    "仙侠": ["打脸虐渣", "大男主", "马甲", "重生", "穿越", "系统", "强者回归", "传承觉醒", "赘婿逆袭", "反派主角"],
    "玄幻": ["打脸虐渣", "大男主", "重生", "穿越", "系统", "强者回归", "异能", "传承觉醒", "反派主角"],
    "古言": ["先婚后爱", "大女主", "追妻火葬场", "甜宠", "破镜重圆", "青梅竹马", "宅斗", "双洁"],
    "宫斗": ["大女主", "打脸虐渣", "追妻火葬场", "甜宠", "马甲", "重生"],
    "权谋": ["大男主", "大女主", "打脸虐渣", "业界精英", "强强联合", "马甲"],
    "都市": ["神豪", "豪门", "商战", "神医", "医生", "白月光", "甜宠", "追妻火葬场", "娱乐圈", "暴富"],
    "民国": ["军阀", "虐恋", "破镜重圆", "双向救赎", "大女主", "强强联合"],
    "科幻": ["异能", "丧尸", "系统", "末世", "未来", "灵魂互换"],
    "悬疑": ["刑侦", "反派主角", "双向救赎", "强强联合", "马甲"],
    "年代": ["福宝", "家长里短", "甜宠", "小人物", "白月光", "年代爱情"],
}

# 场景 → 写作手法（与题材正交，任何题材都适用）
SCENE_HOOKS: dict[str, str] = {
    "开局": "开局钩子（前 1-2 章）：强冲突/异常早露，第一章结尾留悬念钩子，让读者想读第二章",
    "推进": "推进节奏：2-3 章一个小爽点，打脸/收获/升级轮换，冲突逐级升级不止",
    "高潮": "高潮场景：情绪拉满，绝境反杀/身份揭晓/大能出手，打脸要打到位不留手",
    "收尾": "收尾：兑现前文铺垫，回收伏笔，给下一卷/下一弧留新钩子",
}

# 阶梯级 → 场景（决定注入哪条场景钩子）
LEVEL_SCENE: dict[str, str] = {"l2": "开局", "l3": "推进", "l4": "高潮", "l5": "收尾"}


def trend_inject_block(genre: str, scene: str, max_hooks: int = 4) -> str:
    """生成端注入块：本题材 × 当前场景的流行钩子参考。

    genre=detect_genre 输出；scene∈开局/推进/高潮/收尾。
    纯静态（零额外 LLM 成本），未命中题材或场景时返回空串（不打扰生成）。
    """
    tags = GENRE_HOOK_TAGS.get(genre) or []
    scene_tip = SCENE_HOOKS.get(scene)
    if not tags and not scene_tip:
        return ""
    parts: list[str] = []
    if tags:
        parts.append("本题材当前读者爱看的钩子：" + "、".join(tags[:max_hooks]))
    if scene_tip:
        parts.append(f"当前场景（{scene}）：{scene_tip}")
    return "\n".join(parts)


if __name__ == "__main__":
    import asyncio

    async def main():
        tests = [
            "重生归来，系统在手，我扮猪吃虎打脸所有看不起我的人",
            "古代宫斗，大女主逆袭，从冷宫到太后",
            "农妇在乡间养猪种田，日子平淡如水",
        ]
        for t in tests:
            prof = await appeal_profile(t)
            out = await is_outdated(prof)
            top = await top_category(prof)
            print(f"outdated={out} 最贴近={top} | {prof}")
            print(f"  {t[:25]}")
    asyncio.run(main())
