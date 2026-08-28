# -*- coding: utf-8 -*-
"""阶梯充分性验证：逐级消融前向重建，定位 l1→l2→l3→l4 断点。

同一章，分别从真实的 l1 / l1+l2 / l1+l2+l3 / 全到 l4 起跑，用生产链
bridge.step_ladder 前向生成到正文 l5，与语料原文打分（score_generated +
事实锚点召回）。每多喂一级真实信息分数应涨；哪一档喂了不涨，断点就在它前面。

用法（在 prompt-harness/ 目录下）：
    python verify_ladder_sufficiency.py --chapters 3
    python verify_ladder_sufficiency.py --chapters 1          # 单章冒烟

产出 harness_runs/ladder_sufficiency/{raw.json,prose_cache.json,report.md}。
不改任何 prompt，只出数据。
"""
import argparse
import asyncio
import json
import re
import sys
import time
from pathlib import Path

# ── 路径与导入（脚本在 prompt-harness/ 根，包名 prompt_harness）──
# 注意：--preset 必须在 import prompt_harness 之前写入 os.environ，
# 因为 config/llm_client 在导入时读取 API 配置。
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

# 先解析 --preset（其余参数在 main 里解析，这里只挑预设）
_PRESET_IDX = None
for i, av in enumerate(sys.argv):
    if av == "--preset" and i + 1 < len(sys.argv):
        _PRESET_IDX = sys.argv[i + 1]
    elif av.startswith("--preset="):
        _PRESET_IDX = av.split("=", 1)[1]

if _PRESET_IDX is None:
    _PRESET_IDX = "doubao-seed-2.0-lite"  # 默认用最便宜的豆包文本模型

import api_preset_lib as _apl  # noqa: E402
_match = None
for _p in _apl.list_presets():
    if _p["id"] == _PRESET_IDX or _PRESET_IDX.lower() in _p["name"].lower():
        _match = _apl.apply_preset(_p["id"])
        break
if _match:
    _apl.refresh_env_from_preset(_match)
    print(f"[preset] 使用 API 预设：{_match['name']}（{_match['model']}）")
else:
    print(f"[preset] 未找到预设 {_PRESET_IDX!r}，使用环境/服务器默认")

from prompt_harness import bridge  # noqa: E402
from prompt_harness.minimal_train import score_generated  # noqa: E402

# 快速验证时跳过 bridge 内部 l2/l3/l4 的 AI 味清理（每档省 3+ 次 LLM 调用）。
# 不修改生产代码，仅在本脚本 monkeypatch 成 no-op。
if "--no-ai-flavor" in sys.argv:
    async def _noop_clean(text, *a, **k):
        return {"text": text, "review": None, "retried": False}
    async def _noop_measure(text, *a, **k):
        return {"score": None, "findings": [], "summary": "", "dirty": False}
    bridge._ai_flavor_clean_text = _noop_clean
    bridge._measure_ai_flavor = _noop_measure

EXTRACT_DIR = Path.home() / ".claude" / "ainovel-write" / "prompt-harness" / \
    "plot_extract_results"
CORPUS_DIR = HERE / "corpus"
OUT_DIR = HERE / "harness_runs" / "ladder_sufficiency_doubao"

# 纳入测试的书（corpus 字段子串 → 可读名）。l4-l5 主要由这两本训练，外加书C作跨题材对照。
BOOKS = [
    ("玄幻武侠/示例书", "示例书"),
    ("玄幻武侠/书D", "书D"),
    ("修仙/书C", "书C(对照)"),
]

# 四档消融：(档名, 注入到哪一级的真实种子)
# A=只 l1，B=l1+l2，C=l1+l2+l3，D=全到 l4
RUNS = [
    ("A_from_l1", ["l1"]),
    ("B_from_l2", ["l1", "l2"]),
    ("C_from_l3", ["l1", "l2", "l3"]),
    ("D_from_l4", ["l1", "l2", "l3", "l4"]),
]

_CHAPTER_RE = re.compile(r"^\s*第\s*([0-9零一二三四五六七八九十百千万两]+)\s*章[^\n]*$",
                         re.MULTILINE)


def _cn_to_int(s: str) -> int | None:
    """阿拉伯或中文数字转 int。"""
    if s.isdigit():
        return int(s)
    cn = "零一二三四五六七八九"
    if s == "十":
        return 10
    if all(c in "零一二三四五六七八九" + "十百千万两" for c in s):
        digits = {"零": 0, "一": 1, "二": 2, "三": 3, "四": 4, "五": 5,
                  "六": 6, "七": 7, "八": 8, "九": 9, "两": 2}
        total, section, cur = 0, 0, 0
        units = {"十": 10, "百": 100, "千": 1000, "万": 10000}
        for ch in s:
            if ch in digits:
                cur = digits[ch]
            elif ch in units:
                if ch == "万":
                    section = (section + cur) * 10000
                    total += section
                    section, cur = 0, 0
                else:
                    section += (cur or 1) * units[ch]
                    cur = 0
        return total + section + cur
    return None


def split_chapters(path: Path) -> dict[int, str]:
    """语料 txt → {章号: 正文}。跳过卷首语/站点噪音。"""
    raw = path.read_bytes()
    for enc in ("utf-8", "gb18030", "gbk"):
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    else:
        text = raw.decode("gb18030", errors="ignore")
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    matches = list(_CHAPTER_RE.finditer(text))
    out: dict[int, str] = {}
    for i, m in enumerate(matches):
        num = _cn_to_int(m.group(1))
        if num is None:
            continue
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        body = text[start:end].strip()
        # 跳过太短的（误匹配的目录行/卷首）
        if len(body) < 500:
            continue
        # 去掉开头连续的站点 URL/广告行
        body = re.sub(r"^(https?://\S+|[\s　]*书名[：:][^\n]*|[\s　]*作者[：:][^\n]*)\n",
                      "", body).strip()
        out[num] = body
    return out


def load_samples_for_book(book_substr: str, want: int) -> list[dict]:
    """扫描所有提取结果，凡 corpus 命中该本子串的，取其 qualified 章。

    示例书/书D的提取结果分散在大量小 json 里（每个 1-5 章），所以必须全扫并去重。
    返回 [{book, arc_name, archetype, chapter_num, l1,l2,l3,l4}]。
    """
    out = []
    seen = set()  # (chapter_num, l1 前 20 字) 去重
    for ef in sorted(EXTRACT_DIR.glob("*.json")):
        try:
            data = json.loads(ef.read_text(encoding="utf-8"))
        except Exception:
            continue
        corpus = str(data.get("corpus") or "")
        if book_substr not in corpus:
            continue
        for arc in data.get("report", []):
            for ch in arc.get("chapters", []):
                if not ch.get("qualified"):
                    continue
                sk = ch.get("skeleton") or {}
                if not all(sk.get(k) for k in ("l1", "l2", "l3", "l4")):
                    continue
                l3 = sk["l3"]
                if not (isinstance(l3, dict) and l3.get("core")):
                    continue
                cn = int(ch.get("chapter_num") or ch.get("chapter") or 0)
                key = (cn, str(sk["l1"]).strip()[:20])
                if key in seen:
                    continue
                seen.add(key)
                out.append({
                    "arc_name": arc.get("name", ""),
                    "archetype": arc.get("archetype", ""),
                    "chapter_num": cn,
                    "l1": str(sk["l1"]).strip(),
                    "l2": str(sk["l2"]).strip(),
                    "l3": {"title": str(l3.get("title") or "").strip(),
                           "core": str(l3.get("core") or "").strip(),
                           "beats": [str(b).strip() for b in (l3.get("beats") or [])
                                     if str(b).strip()]},
                    "l4": sk["l4"],
                })
                if len(out) >= want:
                    return out
    return out


def load_prose_for_book(book_substr: str) -> dict[int, str]:
    """找到该书所有语料卷文件，合并 {章号: 正文}（同章号后出现的卷覆盖）。"""
    merged: dict[int, str] = {}
    for p in CORPUS_DIR.rglob("*.txt"):
        if book_substr in str(p).replace("\\", "/"):
            merged.update(split_chapters(p))
    return merged


def _scenes_from_l4(l4) -> list[dict]:
    """把提取的 skeleton.l4 归一化成 bridge l5 消费的 scene 结构。

    提取里 l4 是场景数组，每场景 {name, environment, actions, dialogues,
    narration, psychologies, conflicts, details}。bridge l5 用 generate_chapter_prose
    消费这些字段；保持原名透传即可。
    """
    if isinstance(l4, dict):
        l4 = [l4]
    scenes = []
    for i, sc in enumerate(l4 or [], 1):
        if not isinstance(sc, dict):
            continue
        scenes.append({
            "name": str(sc.get("name") or f"场景{i}").strip(),
            "environment": str(sc.get("environment") or "").strip(),
            "actions": [str(x) for x in (sc.get("actions") or [])],
            "dialogues": [str(x) for x in (sc.get("dialogues") or [])],
            "narration": [str(x) for x in (sc.get("narration") or [])],
            "psychologies": [str(x) for x in (sc.get("psychologies") or [])],
            "conflicts": [str(x) for x in (sc.get("conflicts") or [])],
            "details": [str(x) for x in (sc.get("details") or [])],
        })
    return scenes


def build_state(sample: dict, seed_levels: list[str]) -> dict:
    """构造单章阶梯 state，把 seed_levels 列出的真实级填进去并标记已确认。"""
    st = bridge.new_state(
        archetype=sample["archetype"],
        l1=sample["l1"],
        n_chapters=1,
    )
    levels = st["levels"]
    if "l2" in seed_levels:
        levels["l2"].update(text=sample["l2"], confirmed=True, prompt="(seed)")
    if "l3" in seed_levels:
        levels["l3"].update(data=sample["l3"], confirmed=True, prompt="(seed)")
    if "l4" in seed_levels:
        levels["l4"].update(scenes=_scenes_from_l4(sample["l4"]),
                            text="(seed)", confirmed=True, prompt="(seed)")
    # l1 默认 confirmed=True（new_state 行为）
    return st


async def run_to_l5(st: dict) -> dict:
    """从当前 state 最高已确认级一路 step 到 l5，返回 {ok, l5_text, steps, error}。"""
    steps = []
    last_text = ""
    for _ in range(6):
        r = await bridge.step_ladder(st)
        to = r.get("to")
        if not r.get("ok"):
            return {"ok": False, "l5": last_text, "steps": steps,
                    "error": str(r.get("error") or "step_failed")}
        steps.append({"to": to, "ok": True})
        # 确认这一级，才能继续往下
        if to != "l5":
            bridge.confirm_level(st, to)
        if to == "l5":
            return {"ok": True, "l5": str(r.get("text") or ""), "steps": steps,
                    "error": None}
        last_text = str(r.get("text") or last_text)
    return {"ok": False, "l5": last_text, "steps": steps, "error": "max_steps_exceeded"}


def fact_anchors(sample: dict) -> list[str]:
    """从提取的 l4 取事实锚点（details + 对白），用作 fact_recall。"""
    anchors = []
    for sc in _scenes_from_l4(sample["l4"]):
        for d in sc.get("details", []):
            d = d.strip()
            if len(d) >= 4:
                anchors.append(d)
        for dl in sc.get("dialogues", []):
            # 取对白里的实义片段（去说话人前缀）
            q = re.search(r"[“\"](.+?)[”\"]", dl)
            frag = (q.group(1) if q else dl).strip()
            if len(frag) >= 6:
                anchors.append(frag)
    # 去重 + 截断，避免太长
    seen, uniq = set(), []
    for a in anchors:
        if a not in seen:
            seen.add(a)
            uniq.append(a)
    return uniq[:40]


def fact_recall(generated: str, anchors: list[str]) -> float:
    """锚点在生成文中的字面出现率（归一化空白/标点后子串匹配）。"""
    if not anchors:
        return 0.0
    g = re.sub(r"\s+", "", generated or "")
    g = re.sub(r"[，。！？、；：“”‘’（）()…—.-]", "", g)
    hit = 0
    for a in anchors:
        na = re.sub(r"\s+", "", a)
        na = re.sub(r"[，。！？、；：“”‘’（）()…—.-]", "", na)
        if na and na in g:
            hit += 1
    return round(hit / len(anchors), 4)


def skeleton_text(sample: dict) -> str:
    l3 = sample["l3"]
    return l3.get("title", "") + " " + l3.get("core", "") + " " + \
        "；".join(l3.get("beats", []))


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--preset", type=str, default="glm-5.3",
                    help="API 预设（id 或名称子串）")
    ap.add_argument("--chapters", "--per-book", dest="chapters", type=int,
                    default=3, help="每本书取多少 qualified 章")
    ap.add_argument("--books", type=str, default="",
                    help="只测指定书名子串（逗号分隔），默认三本书全测")
    ap.add_argument("--no-ai-flavor", action="store_true",
                    help="跳过 LLM AI味审阅（省钱冒烟）")
    args = ap.parse_args()

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    books = [(s, n) for s, n in BOOKS
             if not args.books or any(k in n for k in args.books.split(","))]

    # 每本书加载自己的原文 + 样本
    book_samples = []
    prose_by_book: dict[str, dict[int, str]] = {}
    for substr, label in books:
        prose = load_prose_for_book(substr)
        prose_by_book[label] = prose
        samps = load_samples_for_book(substr, args.chapters)
        for s in samps:
            s["book"] = label
        book_samples.append((label, samps))
        print(f"[load] {label}：原文 {len(prose)} 章，样本 {len(samps)} 章 "
              f"({', '.join('ch'+str(s['chapter_num']) for s in samps)})")

    total = sum(len(s) for _, s in book_samples)
    print(f"[load] 共 {total} 个样本")

    raw = []

    # ── 断点续跑：读旧 raw.json，四档齐全的章直接复用，不重复烧 LLM ──
    prev = {}
    _raw_path = OUT_DIR / "raw.json"
    if _raw_path.exists():
        try:
            for r in json.loads(_raw_path.read_text(encoding="utf-8")):
                runs = r.get("runs") or {}
                if all(isinstance(runs.get(rn, {}).get("score"), (int, float))
                       for rn, _ in RUNS):
                    prev[(r.get("book"), int(r.get("chapter_num") or 0))] = r
        except Exception as e:
            print(f"[resume] 旧 raw.json 读不动（{e}），全量重跑")
        if prev:
            print(f"[resume] 已有 {len(prev)} 章完整结果，将跳过")
    t0 = time.time()
    done = 0
    for label, samples in book_samples:
        prose = prose_by_book[label]
        for si, sample in enumerate(samples, 1):
            done += 1
            ch = sample["chapter_num"]
            target = prose.get(ch, "")
            if not target:
                print(f"  [!] {label} ch{ch} 在语料里没切到原文，跳过")
                continue
            print(f"[3/4] {label} 章 {ch}（{done}/{total}） 原文 {len(target)} 字",
                  flush=True)
            if (label, ch) in prev:
                print("      [resume] 四档已测过，跳过", flush=True)
                raw.append(prev[(label, ch)])
                continue
            anchors = fact_anchors(sample)
            skel = skeleton_text(sample)
            row = {"book": label, "chapter_num": ch,
                   "arc_name": sample["arc_name"],
                   "prose_len": len(target), "runs": {}}
            for run_name, seed in RUNS:
                print(f"      档 {run_name}（种子：{'/'.join(seed)}）...", flush=True)
                st = build_state(sample, seed)
                res = await run_to_l5(st)
                l5 = res["l5"]
                entry = {"ok": res["ok"], "error": res["error"],
                         "steps": [s["to"] for s in res["steps"]],
                         "gen_len": len(re.sub(r"\s+", "", l5))}
                if l5:
                    sc = await score_generated(
                        l5, target, skel,
                        ai_flavor=not args.no_ai_flavor)
                    fr = fact_recall(l5, anchors)
                    entry.update({
                        "score": round(float(sc.get("score") or 0), 4),
                        "s_char": sc.get("s_char"),
                        "plot_sim": sc.get("plot_sim"),
                        "v_cos": sc.get("v_cos"),
                        "fact_recall": fr,
                    })
                    print(f"          -> score={entry.get('score')} "
                          f"plot_sim={entry.get('plot_sim')} fr={fr} "
                          f"len={entry['gen_len']}"
                          + (f" ERR={res['error']}" if res["error"] else ""))
                else:
                    print(f"          -> 无正文 ERR={res['error']}")
                entry["l5_preview"] = l5[:300]
                row["runs"][run_name] = entry
            raw.append(row)
            _raw_path.write_text(json.dumps(raw, ensure_ascii=False, indent=2),
                                 encoding="utf-8")

    (OUT_DIR / "raw.json").write_text(
        json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")

    # ── report.md ──
    book_names = [n for _, n in books]
    lines = ["# 阶梯充分性验证报告（逐级消融前向重建）", "",
             f"书：{', '.join(book_names)} ｜ 样本：{len(raw)} 章 ｜ "
             f"耗时 {time.time()-t0:.0f}s", "",
             "评分 = score_generated(0.5 s_char + 0.3 plot_sim + 0.2 v_cos)；"
             "fact_recall = l4 锚点在生成文中的字面出现率。", "",
             "四档：A=只喂真实 l1｜B=喂到 l2｜C=喂到 l3｜D=喂到 l4。"
             "分数随种子爬升的台阶就是断点位置。", ""]
    metrics = ["score", "plot_sim", "v_cos", "fact_recall", "gen_len"]

    def avg(rows, m, rn):
        xs = [r["runs"][rn].get(m) for r in rows
              if isinstance(r["runs"].get(rn, {}).get(m), (int, float))]
        return round(sum(xs) / len(xs), 4) if xs else None

    def mean_table(title, rows):
        out = [f"## {title}", "",
               "| 指标 | " + " | ".join(r for r, _ in RUNS) + " | ΔB-A | ΔC-B | ΔD-C |",
               "|" + "---|" * (1 + len(RUNS) + 3)]
        for m in metrics:
            vals = [avg(rows, m, rn) for rn, _ in RUNS]
            d_ba = round(vals[1] - vals[0], 4) if vals[0] is not None and vals[1] is not None else None
            d_cb = round(vals[2] - vals[1], 4) if vals[1] is not None and vals[2] is not None else None
            d_dc = round(vals[3] - vals[2], 4) if vals[2] is not None and vals[3] is not None else None
            out.append("| " + " | ".join(
                [m] + [str(v) if v is not None else "—" for v in vals] +
                [str(d_ba) if d_ba is not None else "—",
                 str(d_cb) if d_cb is not None else "—",
                 str(d_dc) if d_dc is not None else "—"]) + " |")
        out.append("")
        return out

    lines += mean_table("全部均值爬升", raw)
    for label in book_names:
        brows = [r for r in raw if r.get("book") == label]
        if brows:
            lines += mean_table(f"{label}（{len(brows)} 章）", brows)

    # 逐章明细
    lines += ["## 逐章明细", "",
              "| 书 | 章 | "
              + " | ".join(f"{r} {m}" for r, _ in RUNS for m in ["score", "fr", "len"])
              + " |",
              "|" + "---|" * (2 + len(RUNS) * 3)]
    for row in raw:
        cells = [row.get("book", ""), str(row["chapter_num"])]
        for run_name, _ in RUNS:
            e = row["runs"].get(run_name, {})
            cells.append(f"{e.get('score', '—')}")
            cells.append(f"{e.get('fact_recall', '—')}")
            cells.append(f"{e.get('gen_len', '—')}")
        lines.append("| " + " | ".join(cells) + " |")
    lines.append("")

    # errors
    errs = [(r.get("book", ""), r["chapter_num"], rn, e.get("error"))
            for r in raw for rn, e in r["runs"].items() if e.get("error")]
    lines.append("## 错误")
    if errs:
        for bk, ch, rn, e in errs:
            lines.append(f"- {bk} ch{ch} {rn}: {e}")
    else:
        lines.append("无")
    lines.append("")

    # 结论（自动提示）
    lines.append("## 结论提示（数据导向，需人工判读）")
    a_s, b_s, c_s, d_s = (avg(raw, "score", rn) for rn, _ in RUNS)
    for label, prev, cur, hint in [
        ("B-A", a_s, b_s, "l2 相对 l1 带来的增量；不涨说明 l2 概要信息增量不足"),
        ("C-B", b_s, c_s, "l3 章纲相对 l2 的增量；不涨说明 l3 拆解不充分"),
        ("D-C", c_s, d_s, "l4 场景相对 l3 的增量（已知 l4→l5 能行，应明显涨）"),
    ]:
        if prev is not None and cur is not None:
            delta = round(cur - prev, 4)
            lines.append(f"- **{label}**: score {prev:.4f} → {cur:.4f} "
                         f"({'+' if delta >= 0 else ''}{delta}) — {hint}")
    lines.append("")

    (OUT_DIR / "report.md").write_text("\n".join(lines), encoding="utf-8")
    print(f"[4/4] 完成 → {OUT_DIR / 'report.md'}")


if __name__ == "__main__":
    asyncio.run(main())
