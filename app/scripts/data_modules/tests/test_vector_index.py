#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import numpy as np
import pytest

from data_modules.vector_index import VectorIndex


@pytest.fixture
def tmp_index(tmp_path):
    return VectorIndex(dim=4, index_path=tmp_path / "test.index")


def test_build_and_search(tmp_index):
    vectors = np.array([
        [1.0, 0.0, 0.0, 0.0],
        [0.0, 1.0, 0.0, 0.0],
        [0.0, 0.0, 1.0, 0.0],
    ])
    ids = ["a", "b", "c"]
    tmp_index.build(vectors, ids)

    query = np.array([1.0, 0.0, 0.0, 0.0])
    results = tmp_index.search(query, top_k=2)
    assert len(results) == 2
    assert results[0][0] == "a"
    assert results[0][1] > 0.99


def test_save_and_load(tmp_path):
    index_path = tmp_path / "persist.index"
    index = VectorIndex(dim=3, index_path=index_path)
    vectors = np.array([
        [1.0, 2.0, 3.0],
        [4.0, 5.0, 6.0],
    ])
    index.build(vectors, ["x", "y"])
    index.save()

    index2 = VectorIndex(dim=3, index_path=index_path)
    assert index2.load()
    assert index2.size == 2

    results = index2.search(np.array([1.0, 2.0, 3.0]), top_k=1)
    assert results[0][0] == "x"


def test_add_and_remove(tmp_index):
    tmp_index.build(np.array([[1.0, 0.0, 0.0, 0.0]]), ["a"])
    tmp_index.add(np.array([[0.0, 1.0, 0.0, 0.0]]), ["b"])
    assert tmp_index.size == 2

    tmp_index.add(np.array([[0.0, 0.0, 1.0, 0.0]]), ["a"])  # 重复 id 替换
    assert tmp_index.size == 2

    results = tmp_index.search(np.array([0.0, 0.0, 1.0, 0.0]), top_k=1)
    assert results[0][0] == "a"

    tmp_index.remove(["a"])
    assert tmp_index.size == 1


def test_fallback_when_faiss_disabled(tmp_path):
    index = VectorIndex(dim=2, index_path=tmp_path / "fallback.index", use_faiss=False)
    index.build(np.array([[1.0, 0.0], [0.0, 1.0]]), ["a", "b"])
    results = index.search(np.array([0.0, 1.0]), top_k=1)
    assert results[0][0] == "b"
