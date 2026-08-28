"""端到端测试：调用 reverse-infer API，记录所有分数和中间结果。"""
import json
import sys
import time
import urllib.request
import urllib.error

API_URL = "http://127.0.0.1:8765/api/prompt-harness/optimize/reverse-infer"

TEXT = (
    "夜风习习吹动着窗外的梧桐树叶沙沙作响。李明坐在书房里手中捧着一本泛黄的古籍目光却不在字里行间。"
    "他的思绪早已飘向了远方飘向了那个再也回不去的夏天。桌上的茶已经凉了他却浑然不觉。"
    "窗外偶尔传来几声虫鸣更显得这深夜的寂静。有些事错过了就是一辈子。夜色再浓也终究会被黎明冲散。"
    "他轻轻合上书页发出一声叹息。那些年少的时光如同指间的流沙越想握紧流失得越快。"
    "李明站起身走到窗前望着远处模糊的山影心中涌起一股说不清道不明的惆怅。"
    "人生大概就是这样吧总是在失去之后才懂得珍惜。他苦笑着摇了摇头转身重新坐回书桌前。"
    "桌上摊开的笔记本上密密麻麻写满了字那是他这些年的心路历程。每一页都记录着一个故事有欢笑也有泪水。"
    "他翻开最新的一页提起笔却又不知道该写些什么。千言万语在心头却不知从何说起。"
    "窗外的梧桐叶又落了一片打着旋儿飘进窗来落在他的脚边。他弯腰捡起那片叶子借着灯光仔细观察。"
    "叶脉清晰可见像极了人生的脉络每一根都通往不同的方向。他把叶子夹进书里算是给这个夜晚留个纪念。"
    "时钟敲响了十二点新的一天开始了。李明吹熄了蜡烛房间里陷入一片黑暗。"
    "但他知道当黎明到来的时候一切都会重新开始。就像这窗外的梧桐树明年春天又会发出新芽。"
)

def call_reverse_infer(text: str, mode: str = "fast") -> dict:
    """调用 reverse-infer API。mode: fast / standard / quality"""
    payload = json.dumps({
        "target_text": text,
        "mode": mode,
        "source_file": "测试/端到端测试.txt",
        "chapter_section": "main",
        "include_experience_hits": False,
    }).encode("utf-8")
    req = urllib.request.Request(
        API_URL,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    start = time.monotonic()
    try:
        with urllib.request.urlopen(req, timeout=300) as resp:
            elapsed = time.monotonic() - start
            raw = resp.read().decode("utf-8")
            data = json.loads(raw)
            return {"elapsed": elapsed, **data}
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        return {"error": f"HTTP {e.code}: {body[:500]}", "elapsed": time.monotonic() - start}
    except Exception as e:
        return {"error": str(e), "elapsed": time.monotonic() - start}


def print_result(label: str, result: dict):
    print(f"\n{'='*60}")
    print(f"  {label}")
    print(f"{'='*60}")
    if result.get("error"):
        print(f"  ❌ ERROR: {result['error']}")
        return
    elapsed = result.get("elapsed", 0)
    print(f"  耗时: {elapsed:.1f}s")
    ok = result.get("ok", False)
    print(f"  ok: {ok}")
    print()

    candidates = result.get("candidates", []) or result.get("top3", []) or []
    print(f"  候选数: {len(candidates)}")
    for i, c in enumerate(candidates):
        cs = c.get("composite_score", c.get("score", "N/A"))
        vc = c.get("v_cosine", c.get("v_cosine_similarity", "N/A"))
        sc = c.get("char_similarity", c.get("s_char", c.get("similarity", "N/A")))
        ld = c.get("length_diff", c.get("length_deviation", "N/A"))
        print(f"\n  ── 候选 #{i+1} ──")
        print(f"     综合分:        {cs}")
        print(f"     V_cosine:      {vc}")
        print(f"     S_char:        {sc}")
        print(f"     长度偏差:      {ld}")
        prompt = c.get("prompt", c.get("prompt_text", ""))[:300]
        print(f"     Prompt[前300]: {prompt}")

    # 打印其他字段
    for key in result:
        if key not in ("ok", "candidates", "top3", "elapsed", "error"):
            val = result[key]
            if isinstance(val, float):
                print(f"  {key}: {val:.4f}")
            elif isinstance(val, str) and len(val) > 200:
                print(f"  {key}: {val[:200]}...")
            else:
                print(f"  {key}: {val}")

    print()


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "fast"
    print(f"🔄 测试模式: {mode}")
    print(f"📄 目标文本 ({len(TEXT)} 字符)")
    print(f"🔗 API: {API_URL}")

    result = call_reverse_infer(TEXT, mode)
    print_result(f"Mode={mode}", result)
