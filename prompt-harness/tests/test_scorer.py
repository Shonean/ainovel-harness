"""打分器单元测试。"""
from __future__ import annotations

import asyncio

from prompt_harness.scorer import bleu_4, compute_scores, rouge_l


def test_identical_strings():
    text = "他走了。"
    assert rouge_l(text, text) == 1.0
    assert bleu_4(text, text) >= 0.999


def test_different_strings():
    assert rouge_l("他走了。", "她来了。") < 1.0
    assert bleu_4("他走了。", "她来了。") < 1.0


def test_compute_scores_without_embedding():
    result = asyncio.run(compute_scores("他走了。", "他走了。"))
    assert result["rouge_l"] == 1.0
    assert result["bleu_4"] >= 0.999
    assert 0.69 <= result["composite"] <= 0.71


if __name__ == "__main__":
    test_identical_strings()
    test_different_strings()
    test_compute_scores_without_embedding()
    print("scorer tests passed")
