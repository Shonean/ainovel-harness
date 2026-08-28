"""v5.12 确定性节拍匹配（b7 事件保真 v2，规则实现，无 LLM）。

复现原文的核心：骨架（skeleton_text）是原文情节拍点的时间有序序列，
b7 度量"生成文本在多大程度上按顺序复现了这些拍点"。

v1（v5.11）用全局锚点判覆盖——任意共享锚点（主角名/老刘）命中即"覆盖"，
所有候选 coverage≈0.95、order=1.0，恒定 0.9706，不区分好坏，白送 0.194。

v2（v5.12）改用**每拍独有内容**度量：
- distinctive 4-gram = 本拍有、其它拍都没有的 4-gram（剔除跨拍共享内容）
- 拍覆盖度（分级）= |distinctive ∩ generated| / |distinctive|（不再 0/1 二元）
- coverage = 各可验证拍覆盖度的均值（区分"写全细节" vs "只写主干"）
- order = 被复现拍按 distinctive 4-gram 最早出现位置两两比对
- composite = 0.6 * coverage + 0.4 * order
- 发明扣分：generated 里 normalize 后不在 target_text（原文）中的数字 = 发明，
  invention = min(0.25, 0.02 * 发明个数)，composite *= (1 - invention)。
  扣分对原文而非骨架：忠实复现的原文细节数字可能不在骨架里，对骨架判会误伤。

纯规则、免费、可复现。
"""
from __future__ import annotations

import re

from .key_details import extract_numbers
from .scorer import normalize_text


_NUM_BEAT_RE = re.compile(r"^\s*[0-9]+[.、:]\s*(.+)$")


def parse_beats(skeleton_text: str) -> list[str]:
    """把 skeleton_text 切成拍点序列（时间有序）。

    优先解析编号拍点（"1. 老刘…" / "2、老刘…"）——extract_plot_skeleton
    chapter 级格式的核心事件；人物/细节列表项（缩进子 bullet）不属于拍点，不纳入。
    若没有编号拍点，退回扁平 "- " bullet 列表（v5.7 旧格式，逐行即拍点）。
    """
    numbered: list[str] = []
    for line in skeleton_text.splitlines():
        m = _NUM_BEAT_RE.match(line)
        if m and m.group(1).strip():
            numbered.append(m.group(1).strip())
    if numbered:
        return numbered

    beats: list[str] = []
    for line in skeleton_text.splitlines():
        line = line.strip()
        if line.startswith("- "):
            beat = line[2:].strip()
            if beat:
                beats.append(beat)
    return beats


def _char_ngrams(s: str, n: int = 4) -> set[str]:
    """字符 n-gram 集合。len(s) < n 时返回自身（s 非空时）。"""
    if len(s) < n:
        return {s} if s else set()
    return {s[i:i + n] for i in range(len(s) - n + 1)}


def beat_sequence_fidelity(
    skeleton_text: str,
    generated: str,
    target_text: str | None = None,
) -> dict:
    """b7 事件保真 v2 评分。

    返回 {coverage, order, composite, beats, covered, verifiable, invention, invented_numbers}。

    规则：
    - 拍点无 distinctive 4-gram（独有内容太短 / 全是跨拍共享内容）→ 不可验证，不进分母
    - 无拍点 / 生成空 / 无任何可验证拍点 → 中性 0.5（无法判定，不惩罚）
    - 每拍覆盖度（分级）= 命中的 distinctive 4-gram 占比；coverage = 各可验证拍均值
    - 每拍取命中的 distinctive 4-gram 在 generated 中最早出现位置代表其位置；两两比较顺序
    - 发明扣分：target_text 提供时，generated 中不在原文的数字视为发明
    """
    beats = parse_beats(skeleton_text)
    gen_norm = normalize_text(generated)

    result: dict = {
        "coverage": 0.5, "order": 0.5, "composite": 0.5,
        "beats": len(beats), "covered": 0, "verifiable": 0,
        "invention": 0.0, "invented_numbers": [],
        # 【v5.14 L2b】每拍诊断：{beat, verifiable, distinctive_count, hit_count, coverage}
        # 供 select_reproduction_gaps 找低覆盖拍。只放标量不放 set（_json_default 会把 set 转 str）。
        "per_beat": [],
    }

    if not beats or not gen_norm:
        return result

    # 每拍独有 4-gram：本拍有、其它拍都没有的（剔除跨拍共享内容）。
    # 注意不能用 total - s（s 的共享 n-gram 也会被整体删掉 → 误保留进本拍 distinctive），
    # 必须逐拍求"其它拍的并集"再相减。
    beat_norms = [normalize_text(b) for b in beats]
    beat_sets = [_char_ngrams(bn, 4) for bn in beat_norms]
    distinctives: list[set[str]] = []
    for i in range(len(beat_sets)):
        others: set[str] = set()
        for j, s in enumerate(beat_sets):
            if j != i:
                others |= s
        distinctives.append(beat_sets[i] - others)
    gen_ngrams = _char_ngrams(gen_norm, 4)

    verifiable = 0
    coverage_sum = 0.0
    covered_seq: list[tuple[int, int]] = []  # (beat_idx, 最早位置) 按骨架顺序

    per_beat: list[dict] = []
    for i in range(len(beats)):
        distinctive = distinctives[i]
        entry: dict = {
            "beat": beats[i],
            "verifiable": bool(distinctive),
            "distinctive_count": len(distinctive),
            "hit_count": 0,
            "coverage": None,  # None = 不可验证
        }
        if distinctive:
            verifiable += 1
            hit = distinctive & gen_ngrams
            entry["hit_count"] = len(hit)
            entry["coverage"] = round(len(hit) / len(distinctive), 4)
            coverage_sum += len(hit) / len(distinctive)
            if hit:
                pos = min(gen_norm.find(ng) for ng in hit)
                covered_seq.append((i, pos))
        per_beat.append(entry)
    result["per_beat"] = per_beat

    result["beats"] = len(beats)
    result["verifiable"] = verifiable
    result["covered"] = len(covered_seq)

    if verifiable == 0:
        return result  # 全中性 0.5（无拍点可验证）

    coverage = coverage_sum / verifiable

    k = len(covered_seq)
    if k < 2:
        order = 1.0
    else:
        correct = 0
        total_pairs = 0
        for a in range(k):
            for b in range(a + 1, k):
                total_pairs += 1
                if covered_seq[a][1] <= covered_seq[b][1]:
                    correct += 1
        order = correct / total_pairs

    composite = 0.6 * coverage + 0.4 * order

    # 发明扣分（对原文，而非骨架——防误伤忠实复现的原文细节数字）
    if target_text:
        target_norm = normalize_text(target_text)
        invented = []
        for num in extract_numbers(generated, cap=20):
            n = normalize_text(num["text"])
            if n and n not in target_norm:
                invented.append(num["text"])
        if invented:
            result["invented_numbers"] = invented
            result["invention"] = round(min(0.25, 0.02 * len(invented)), 4)
            composite *= (1.0 - result["invention"])

    result.update({
        "coverage": round(coverage, 4),
        "order": round(order, 4),
        "composite": round(max(0.0, min(1.0, composite)), 4),
    })
    return result
