"""语料加载测试。"""
from __future__ import annotations

import tempfile
from pathlib import Path

from prompt_harness.corpus_loader import load_corpus


def test_merge_paragraphs():
    with tempfile.TemporaryDirectory() as tmp:
        corpus_dir = Path(tmp)
        paragraphs = [
            f"这是第{i}段，包含一些用于测试合并逻辑的虚拟正文内容。"
            for i in range(40)
        ]
        (corpus_dir / "sample.txt").write_text("\n\n".join(paragraphs), encoding="utf-8")
        segments, meta = load_corpus(corpus_dir)
        assert meta["total_segments"] >= 1
        for seg in segments:
            assert 250 <= len(seg["text"]) <= 850


if __name__ == "__main__":
    test_merge_paragraphs()
    print("corpus loader tests passed")
