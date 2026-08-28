"""验证：corpus_loader 章节解析增强（前导空白 + 中文数字章号）对 7 本新书生效，旧书不回归。

改前：新书 parse_chapters 全 0 章；改后应全部非 0 且首章章号正确。
产出：harness_runs/chapter_parse_fix/（响应 json + 日志）。
运行：python verify_chapter_parse_newbooks.py
"""
import json
import sys
from pathlib import Path

from prompt_harness.corpus_loader import parse_chapters

HARNESS_DIR = Path(__file__).parent / "harness_runs" / "chapter_parse_fix"
HARNESS_DIR.mkdir(parents=True, exist_ok=True)

# (corpus 相对路径, 期望非0, 期望首章号)
NEW_BOOKS = [
    ("修仙/书A/书A_1-500章.txt", 500, 1),
    ("修仙/书B/书B_1-500章.txt", 500, 1),
    ("修仙/书E/书E_1-500章.txt", 500, 1),
    ("修仙/书C/书C.txt", 1000, 1),
    ("历史正史/大宋文豪/大宋文豪_1-500章.txt", 400, 1),
    ("修仙/让仙门再次伟大/让仙门再次伟大.txt", 200, 1),
    ("修仙/谁让他修仙的/谁让他修仙的.txt", 1000, 1),
]
# 旧书回归：章数应保持非 0 且 >= 旧值 458
OLD_BOOKS = [
    ("玄幻武侠/示例书/示例书(1-500章).txt", 458),
    ("玄幻武侠/书D/书D(1-500章).txt", 481),
]


def main() -> int:
    results = []
    failed = 0
    for fp, min_ch, first in NEW_BOOKS:
        r = parse_chapters(fp)
        chs = r.get("chapters") or []
        total = len(chs)
        fnum = chs[0]["chapter_num"] if chs else None
        ok = total >= min_ch and fnum == first
        if not ok:
            failed += 1
        results.append({"kind": "new", "file": fp, "total": total, "first": fnum,
                        "expect_min": min_ch, "expect_first": first, "ok": ok})
        print(f"{'OK ' if ok else 'FAIL'} new {fp}: total={total} first={fnum}")

    for fp, old_min in OLD_BOOKS:
        r = parse_chapters(fp)
        total = len(r.get("chapters") or [])
        ok = total >= old_min
        if not ok:
            failed += 1
        results.append({"kind": "old", "file": fp, "total": total,
                        "expect_min": old_min, "ok": ok})
        print(f"{'OK ' if ok else 'FAIL'} old {fp}: total={total}")

    (HARNESS_DIR / "verify_result.json").write_text(
        json.dumps({"failed": failed, "n": len(results), "results": results},
                   ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n结果: {len(results)-failed}/{len(results)} 通过, 失败 {failed}")
    print(f"产出: {HARNESS_DIR / 'verify_result.json'}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
