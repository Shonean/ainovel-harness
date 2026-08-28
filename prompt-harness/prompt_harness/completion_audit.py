"""完成审计（completion audit）—— 借鉴 codex continuation.md「把完成当成未证明」。

l4（场景分解）和 l5（正文）两道出口的**逐项确定性自检**：不依赖 LLM 自报完成，
而是用可复现的检查核对每一项硬性要求是否真的满足。不达标的 finding 带 blocker_key，
调用方喂回 LLM 修正；同一 blocker 连续命中三轮（blocked 三振）才如实标记 incomplete
继续往下，不假装通过、不死循环烧 token。

设计原则：
- 只做确定性检查（结构/子串/计数/已有评分器），不在这里调 LLM；
- 复用锚点（anchor_control.verify_generated_anchors）、对白轮保真
  （plot_similarity.dialogue_turn_fidelity）等既有能力，本模块只编排；
- finding 形状统一：{item, expected, actual, blocker_key, severity, retriable}。
"""
from __future__ import annotations

import re
from typing import Any

# l4 场景必须有的 7 类叶子（environment 是字符串，其余是列表）
_LIST_LEAVES = ("actions", "dialogues", "narration", "psychologies", "conflicts", "details")
_STR_LEAVES = ("environment",)
# 占位/省略/截断噪声（LLM 输出常见的「没写完」信号）
_PLACEHOLDER_RE = re.compile(r"\.\.\.|……|（?等）?$|^\s*$|等等|省略|略\s*$")
# 直引号/直角引号（v5.31：生成端必须统一中文弯引号 “”）
_STRAIGHT_DQUOTE = '"'
_BRACKET_QUOTE_RE = re.compile(r"[「」『』]")
# 事实里切分专有名词：按常见动词/虚词/标点把事实句切成名词片段
# （「林川在医院遇见王慧兰」→ 林川 / 医院 / 王慧兰），再取 2-6 字 CJK 片段。
_FACT_SPLIT_RE = re.compile(
    r"[，。、；：的在了和与跟把被是有去到见告诉给让向从对为以将于发现遇见"
    r"找到拿到拿走送来带去说问答看听哭笑打骂想知道带着拿着]")
_CJK_TOKEN_RE = re.compile(r"[一-鿿]{2,6}")
# 切分事实后要丢弃的通用词（不是必须钉住的专名；硬钉会造成大量误报）
_FACT_STOPWORDS = {
    "医院", "病房", "房间", "屋子", "街道", "城市", "门口", "床上", "桌子", "椅子",
    "早上", "晚上", "中午", "夜里", "白天", "时候", "地方", "东西", "事情", "样子",
    "心里", "眼神", "声音", "空气", "光线", "一个", "这个", "那个", "什么", "怎么",
    "因为", "所以", "但是", "如果", "已经", "正在", "可以", "没有", "不是", "还是",
}

# 对白叶子里「说话动词」（用于检测相邻轮重复同一动词）
_SPEAK_VERB_RE = re.compile(r"[“\"]?[^“”\"”]{0,12}?(轻叹|开口|出声|应|说|道|问|答|笑|骂|吼|低声|压低|揉|抿|皱|眨|瞥|望|看|点|摇)[^“”\"”]{0,6}[：:]")


def _finding(item: str, *, expected: str, actual: str, blocker_key: str,
             severity: str = "high", retriable: bool = True) -> dict[str, Any]:
    return {
        "item": item,
        "expected": expected,
        "actual": actual,
        "blocker_key": blocker_key,
        "severity": severity,   # high / medium / low
        "retriable": retriable,
    }


# ---------------------------------------------------------------------------
# l4 审计：场景分解（7 类叶子）结构完整性
# ---------------------------------------------------------------------------

def audit_l4(
    scenes: list[dict[str, Any]],
    *,
    facts: list[str] | None = None,
    density: int = 4,
    min_scenes: int = 3,
    max_scenes: int = 5,
) -> dict[str, Any]:
    """核对场景分解是否完整可写。

    facts：extract_key_facts 提取的不可变事实句；检查其中的专名（≥3 字 CJK 串）
    是否仍出现在场景文本里（防改名/丢失，如 王慧兰→张兰）。
    """
    findings: list[dict[str, Any]] = []
    scenes = [s for s in (scenes or []) if isinstance(s, dict)]

    # ① 场景数
    if not scenes:
        findings.append(_finding(
            "场景数", expected="至少 1 个场景", actual="0 个场景",
            blocker_key="l4:no_scene", severity="high", retriable=True))
    elif len(scenes) < min_scenes:
        findings.append(_finding(
            "场景数", expected=f"{min_scenes}-{max_scenes} 个场景",
            actual=f"{len(scenes)} 个场景",
            blocker_key="l4:scene_count", severity="medium", retriable=True))

    all_text_parts: list[str] = []
    for si, sc in enumerate(scenes):
        tag = f"场景{si + 1}"
        name = str(sc.get("name") or "").strip()

        # ② environment 必须是非空字符串
        env = sc.get("environment")
        if not isinstance(env, str) or not env.strip():
            findings.append(_finding(
                f"{tag} 环境", expected="非空字符串 environment",
                actual=f"{type(env).__name__}={str(env)[:20]!r}",
                blocker_key=f"l4:env_empty:s{si}", severity="high"))
        else:
            all_text_parts.append(env)

        # ③ 每类列表叶子：存在 + 是 list + 达 density 条数（narration 4-6）+ 无占位
        for k in _LIST_LEAVES:
            v = sc.get(k)
            if not isinstance(v, list):
                findings.append(_finding(
                    f"{tag} {k}", expected="列表", actual=type(v).__name__,
                    blocker_key=f"l4:{k}_notlist:s{si}", severity="high"))
                continue
            items = [str(x).strip() for x in v if str(x).strip()]
            want_lo = 4 if k == "narration" else max(2, density)
            want_hi = 6 if k == "narration" else max(density, want_lo) + 4
            if len(items) < want_lo:
                findings.append(_finding(
                    f"{tag} {k}", expected=f"≥{want_lo} 条",
                    actual=f"{len(items)} 条",
                    blocker_key=f"l4:{k}_few:s{si}",
                    severity="medium" if k != "dialogues" else "high"))
            for it in items:
                if _PLACEHOLDER_RE.search(it) and len(it) <= 6:
                    findings.append(_finding(
                        f"{tag} {k}", expected="具体内容，无省略/占位",
                        actual=repr(it[:24]),
                        blocker_key=f"l4:{k}_placeholder:s{si}",
                        severity="medium"))
            all_text_parts.extend(items)

        # ④ 对白叶子：带说话人 + 弯引号（v5.31）；相邻轮不重复同一说话动词
        对话s = [str(x).strip() for x in (sc.get("dialogues") or []) if str(x).strip()]
        prev_verb = None
        for di, d in enumerate(对话s):
            if not ("“" in d or "”" in d or "：" in d or ":" in d):
                findings.append(_finding(
                    f"{tag} 对白{di + 1}", expected="带说话人/弯引号",
                    actual=repr(d[:24]),
                    blocker_key=f"l4:dlg_quote:s{si}:d{di}", severity="medium"))
            m = _SPEAK_VERB_RE.search(d)
            if m:
                verb = m.group(1)
                if verb == prev_verb:
                    findings.append(_finding(
                        f"{tag} 对白{di + 1}", expected="相邻轮不重复同一说话动词",
                        actual=f"连续使用「{verb}」",
                        blocker_key=f"l4:dlg_repverb:s{si}:d{di}",
                        severity="low", retriable=True))
                prev_verb = verb
            else:
                prev_verb = None

    # ⑤ 关键事实贯穿：把每条事实句按动词/虚词切成名词片段，过滤通用词后，
    # 每个剩余的专名片段（2-6 字）必须出现在场景文本里（防改名/丢失）。
    if facts and all_text_parts:
        blob = "\n".join(all_text_parts)
        for fact in facts:
            tokens: list[str] = []
            for seg in _FACT_SPLIT_RE.split(str(fact)):
                tokens.extend(_CJK_TOKEN_RE.findall(seg or ""))
            for name in {t for t in tokens if t not in _FACT_STOPWORDS and len(t) >= 2}:
                if name not in blob:
                    findings.append(_finding(
                        "关键事实保留", expected=f"「{name}」出现在场景中（不得改名/丢失）",
                        actual="场景文本中未找到",
                        blocker_key=f"l4:fact_lost:{name}", severity="high"))

    return _pack(findings)


# ---------------------------------------------------------------------------
# l5 审计：正文生成后逐项核对锚点/对白/字数/引号
# ---------------------------------------------------------------------------

def audit_l5(
    prose: str,
    scene: dict[str, Any] | None = None,
    *,
    kd: dict[str, Any] | None = None,
    target_len: int = 0,
    min_len_ratio: float | None = None,
    turn_fidelity_thresh: float | None = None,
) -> dict[str, Any]:
    """核对单场景正文是否真的达标。

    scene：本场景的 l4 叶子（对白顺序契约的权威来源）。
    kd：get_key_details 提取的锚点（人物/数字/对白/物品）；提供则做锚点保留校验。
    target_len：本场景目标字数（_adaptive_target_len）；实际低于 ratio 视为不足。
    阈值默认从 scorer_params 读（Phase 0 抽取，Phase 2 可训练）。
    """
    from . import scorer_params as _sp
    if min_len_ratio is None:
        min_len_ratio = _sp.get_threshold("audit_min_len_ratio", 0.8)
    if turn_fidelity_thresh is None:
        turn_fidelity_thresh = _sp.get_threshold("audit_turn_fidelity_thresh", 0.85)
    findings: list[dict[str, Any]] = []
    prose = prose or ""

    # ① 锚点逐字保留（复用 v5.13 三层锚点的 L2 校验）
    if kd is not None and prose:
        from .anchor_control import verify_generated_anchors
        v = verify_generated_anchors(prose, kd)
        for miss in v.get("missing") or []:
            txt = str(miss.get("text") or miss.get("detail") or "")[:24]
            findings.append(_finding(
                "锚点保留", expected=f"高价值锚点「{txt}」出现在正文",
                actual="缺失",
                blocker_key=f"l5:anchor:{txt}", severity="high"))

    # ② 对白轮有序保真（复用 v5.21 DP 对齐）：scene 叶子是权威顺序契约
    if isinstance(scene, dict):
        对话s = [str(x).strip() for x in (scene.get("dialogues") or []) if str(x).strip()]
        if 对话s and prose:
            from .plot_similarity import dialogue_turn_fidelity
            target_text = "\n".join(对话s)
            tf = dialogue_turn_fidelity(prose, target_text)
            fid = float(tf.get("fidelity") or 0.0)
            if fid < turn_fidelity_thresh:
                findings.append(_finding(
                    "对白轮保真",
                    expected=f"有序对齐 ≥{turn_fidelity_thresh:.0%}",
                    actual=f"fidelity={fid:.0%}（{tf.get('n_target_turns')}轮契约/"
                           f"{tf.get('n_gen_turns')}轮生成）",
                    blocker_key="l5:turn_fidelity", severity="high"))

    # ③ 字数
    if target_len and prose:
        floor = int(target_len * min_len_ratio)
        if len(prose) < floor:
            findings.append(_finding(
                "字数", expected=f"≥{floor} 字（目标 {target_len}）",
                actual=f"{len(prose)} 字",
                blocker_key="l5:length", severity="high"))

    # ④ 引号统一（v5.31）：不得有直引号 / 直角引号
    if prose:
        n_straight = prose.count(_STRAIGHT_DQUOTE)
        if n_straight:
            findings.append(_finding(
                "引号样式", expected="中文弯引号 “”",
                actual=f"含 {n_straight} 个直引号 \"",
                blocker_key="l5:straight_quote", severity="medium"))
        n_bracket = len(_BRACKET_QUOTE_RE.findall(prose))
        if n_bracket:
            findings.append(_finding(
                "引号样式", expected="中文弯引号 “”",
                actual=f"含 {n_bracket} 个直角引号 「」『』",
                blocker_key="l5:bracket_quote", severity="low"))

    return _pack(findings)


def _pack(findings: list[dict[str, Any]]) -> dict[str, Any]:
    high = [f for f in findings if f["severity"] == "high"]
    return {
        "ok": not findings,
        "ok_high": not high,
        "findings": findings,
        "n": len(findings),
        "n_high": len(high),
    }


# ---------------------------------------------------------------------------
# findings → LLM 修正反馈
# ---------------------------------------------------------------------------

def build_audit_feedback(audit: dict[str, Any]) -> str:
    """把审计 findings 拼成给 LLM 的修正指令（追加进 user 后重生成）。"""
    findings = audit.get("findings") if isinstance(audit, dict) else None
    if not findings:
        return ""
    lines = ["【完成审计·未通过】你上一稿声明完成，但逐项核对发现以下硬伤，本次必须逐条修正："]
    for i, f in enumerate(findings, 1):
        lines.append(f"{i}. [{f['severity']}] {f['item']}：应为 {f['expected']}，实际 {f['actual']}。")
    lines.append("请重写本场景/场景分解，确保以上每一项都满足后再返回。")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# blocked 三振：同一 blocker 连续三轮未解决 → 放行但如实标记
# ---------------------------------------------------------------------------

class StrikeCounter:
    """按 blocker_key 计数连续未解决次数。

    用法（每轮审计后）：
        audit = audit_l5(...)
        for f in audit["findings"]:
            if strikes.hit(f["blocker_key"]):   # 连续第 3 次 → True
                mark_incomplete(...)            # 如实记录，不再为它重试
        if 还有非 blocked finding: 重生成
    某 blocker 一旦在某轮不再出现，应调用 reset()（说明修好了）。
    """

    def __init__(self, cap: int = 3) -> None:
        self.cap = cap
        self._strikes: dict[str, int] = {}

    def hit(self, key: str) -> bool:
        self._strikes[key] = self._strikes.get(key, 0) + 1
        return self._strikes[key] >= self.cap

    def reset(self, key: str) -> None:
        self._strikes.pop(key, None)

    def is_blocked(self, key: str) -> bool:
        return self._strikes.get(key, 0) >= self.cap

    def active(self) -> dict[str, int]:
        return dict(self._strikes)
