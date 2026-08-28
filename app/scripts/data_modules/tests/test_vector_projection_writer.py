#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""VectorProjectionWriter 单元测试。"""
from data_modules.projections import VectorProjectionWriter


def test_event_to_text_formats_power_breakthrough():
    writer = VectorProjectionWriter.__new__(VectorProjectionWriter)
    event = {
        "event_type": "power_breakthrough",
        "chapter": 47,
        "subject": "韩立",
        "payload": {"field": "realm", "new": "筑基初期"},
    }
    text = writer._event_to_text(event)
    assert "第47章" in text
    assert "韩立" in text
    assert "筑基初期" in text


def test_delta_to_text_formats_relationship():
    writer = VectorProjectionWriter.__new__(VectorProjectionWriter)
    delta = {
        "from_entity": "韩立",
        "to_entity": "陈巧倩",
        "relationship_type": "合作",
        "chapter": 47,
    }
    text = writer._delta_to_text(delta)
    assert "第47章" in text
    assert "韩立" in text
    assert "陈巧倩" in text
    assert "合作" in text


def test_collect_chunks_from_commit():
    writer = VectorProjectionWriter.__new__(VectorProjectionWriter)
    payload = {
        "meta": {"chapter": 47, "status": "accepted"},
        "accepted_events": [
            {
                "event_type": "power_breakthrough",
                "chapter": 47,
                "subject": "韩立",
                "payload": {"field": "realm", "new": "筑基初期"},
            },
        ],
        "entity_deltas": [
            {
                "from_entity": "韩立",
                "to_entity": "陈巧倩",
                "relationship_type": "合作",
                "chapter": 47,
            },
        ],
    }
    chunks = writer._collect_chunks(payload)
    assert len(chunks) == 2
    assert chunks[0]["chunk_type"] == "event"
    assert chunks[1]["chunk_type"] == "entity_delta"
    assert chunks[0]["chunk_id"] != chunks[1]["chunk_id"]


def test_collect_chunks_assigns_unique_ids_for_same_chapter_events():
    writer = VectorProjectionWriter.__new__(VectorProjectionWriter)
    payload = {
        "meta": {"chapter": 47, "status": "accepted"},
        "accepted_events": [
            {
                "event_type": "character_state_changed",
                "chapter": 47,
                "subject": "韩立",
                "payload": {"field": "状态", "new": "警觉"},
            },
            {
                "event_type": "character_state_changed",
                "chapter": 47,
                "subject": "陈巧倩",
                "payload": {"field": "状态", "new": "迟疑"},
            },
        ],
        "entity_deltas": [],
    }

    chunks = writer._collect_chunks(payload)

    assert len(chunks) == 2
    assert len({chunk["chunk_id"] for chunk in chunks}) == 2
    assert all(chunk["scene_index"] == 0 for chunk in chunks)


def test_collect_chunks_keeps_event_id_stable_when_order_changes():
    writer = VectorProjectionWriter.__new__(VectorProjectionWriter)
    event_a = {
        "event_id": "evt-a",
        "event_type": "character_state_changed",
        "chapter": 47,
        "subject": "韩立",
        "payload": {"field": "状态", "new": "警觉"},
    }
    event_b = {
        "event_id": "evt-b",
        "event_type": "character_state_changed",
        "chapter": 47,
        "subject": "陈巧倩",
        "payload": {"field": "状态", "new": "迟疑"},
    }

    first = writer._collect_chunks(
        {"meta": {"chapter": 47}, "accepted_events": [event_a, event_b], "entity_deltas": []}
    )
    second = writer._collect_chunks(
        {"meta": {"chapter": 47}, "accepted_events": [event_b, event_a], "entity_deltas": []}
    )

    first_ids = {chunk["content"]: chunk["chunk_id"] for chunk in first}
    second_ids = {chunk["content"]: chunk["chunk_id"] for chunk in second}
    assert first_ids == second_ids


def test_rejected_commit_returns_not_applied():
    writer = VectorProjectionWriter.__new__(VectorProjectionWriter)
    writer.project_root = None
    result = writer.apply({"meta": {"status": "rejected", "chapter": 1}})
    assert result["applied"] is False


def _bare_writer():
    w = VectorProjectionWriter.__new__(VectorProjectionWriter)
    w.project_root = None
    return w


# ---- _event_to_text 全事件类型分支 ----

def test_event_to_text_character_state_new_only():
    w = _bare_writer()
    text = w._event_to_text({"event_type": "character_state_changed", "chapter": 5,
                             "subject": "林越", "payload": {"new": "暴怒"}})
    assert "第5章" in text and "林越" in text and "暴怒" in text


def test_event_to_text_character_state_description_only():
    w = _bare_writer()
    text = w._event_to_text({"event_type": "character_state_changed", "chapter": 5,
                             "subject": "林越", "payload": {"description": "陷入回忆"}})
    assert "陷入回忆" in text and "林越" in text


def test_event_to_text_character_state_empty_returns_empty():
    w = _bare_writer()
    assert w._event_to_text({"event_type": "character_state_changed", "chapter": 5,
                             "subject": "林越", "payload": {}}) == ""


def test_event_to_text_relationship_changed():
    w = _bare_writer()
    text = w._event_to_text({"event_type": "relationship_changed", "chapter": 6,
                             "subject": "林越", "payload": {"to_entity": "药师",
                                                            "relationship_type": "师徒"}})
    assert "药师" in text and "师徒" in text


def test_event_to_text_relationship_changed_no_to_returns_empty():
    w = _bare_writer()
    assert w._event_to_text({"event_type": "relationship_changed", "chapter": 6,
                             "subject": "林越", "payload": {}}) == ""


def test_event_to_text_world_rule_revealed_and_broken():
    w = _bare_writer()
    revealed = w._event_to_text({"event_type": "world_rule_revealed", "chapter": 7,
                                 "subject": "", "payload": {"description": "斗气化马"}})
    assert "揭示" in revealed and "斗气化马" in revealed
    broken = w._event_to_text({"event_type": "world_rule_broken", "chapter": 7,
                               "subject": "", "payload": {"rule": "不得越级"}})
    assert "打破" in broken and "不得越级" in broken


def test_event_to_text_world_rule_empty_returns_empty():
    w = _bare_writer()
    assert w._event_to_text({"event_type": "world_rule_revealed", "chapter": 7,
                             "subject": "", "payload": {}}) == ""


def test_event_to_text_open_loop_created():
    w = _bare_writer()
    text = w._event_to_text({"event_type": "open_loop_created", "chapter": 8,
                             "subject": "林越", "payload": {"description": "神秘戒指"}})
    assert "悬念" in text and "神秘戒指" in text


def test_event_to_text_artifact_obtained_with_and_without_owner():
    w = _bare_writer()
    with_owner = w._event_to_text({"event_type": "artifact_obtained", "chapter": 9,
                                   "subject": "", "payload": {"name": "玄重尺", "owner": "林越"}})
    assert "林越" in with_owner and "玄重尺" in with_owner
    no_owner = w._event_to_text({"event_type": "artifact_obtained", "chapter": 9,
                                 "subject": "药师", "payload": {"name": "骨灵冷火"}})
    assert "获得" in no_owner and "骨灵冷火" in no_owner


def test_event_to_text_unknown_type_returns_empty():
    w = _bare_writer()
    assert w._event_to_text({"event_type": "mystery", "chapter": 1,
                             "subject": "x", "payload": {}}) == ""


# ---- _delta_to_text entity 路径 ----

def test_delta_to_text_entity_id_path():
    w = _bare_writer()
    text = w._delta_to_text({"entity_id": "E001", "canonical_name": "林越", "chapter": 3})
    assert "实体变更" in text and "林越" in text


def test_delta_to_text_empty_returns_empty():
    w = _bare_writer()
    assert w._delta_to_text({"chapter": 3}) == ""


# ---- _collect_chunks 跳过非 dict ----

def test_collect_chunks_skips_non_dict_events_and_deltas():
    w = _bare_writer()
    payload = {
        "meta": {"chapter": 1, "status": "accepted"},
        "accepted_events": ["not-a-dict", None, {"event_type": "power_breakthrough",
                            "chapter": 1, "subject": "林越", "payload": {"new": "武者"}}],
        "entity_deltas": [42, {"entity_id": "E1", "chapter": 1, "canonical_name": "药师"}],
    }
    chunks = w._collect_chunks(payload)
    assert len(chunks) == 2
    assert {c["chunk_type"] for c in chunks} == {"event", "entity_delta"}


# ---- apply() 分支 ----

def _accepted_payload_with_event():
    return {
        "meta": {"status": "accepted", "chapter": 1},
        "accepted_events": [{"event_type": "power_breakthrough", "chapter": 1,
                             "subject": "林越", "payload": {"new": "武者"}}],
        "entity_deltas": [],
    }


def test_apply_accepted_no_chunks_returns_no_chunks():
    w = _bare_writer()
    result = w.apply({"meta": {"status": "accepted", "chapter": 1},
                      "accepted_events": [], "entity_deltas": []})
    assert result["applied"] is False
    assert result["reason"] == "no_chunks"


def test_apply_store_error_returns_error_reason(monkeypatch):
    w = _bare_writer()

    def _boom(_chunks):
        raise RuntimeError("boom")

    monkeypatch.setattr(w, "_store_chunks", _boom)
    result = w.apply(_accepted_payload_with_event())
    assert result["applied"] is False
    assert result["reason"].startswith("error:")


def test_apply_accepted_stores_chunks(monkeypatch):
    w = _bare_writer()
    monkeypatch.setattr(w, "_store_chunks", lambda _chunks: 3)
    result = w.apply(_accepted_payload_with_event())
    assert result["applied"] is True
    assert result["stored"] == 3


def test_apply_accepted_store_returns_zero_not_applied(monkeypatch):
    w = _bare_writer()
    monkeypatch.setattr(w, "_store_chunks", lambda _chunks: 0)
    result = w.apply(_accepted_payload_with_event())
    assert result["applied"] is False
    assert result["stored"] == 0


# ---- _store_chunks: stub RAGAdapter ----

def _patch_store_env(monkeypatch, adapter_cls):
    import data_modules.config as cfg_mod
    import data_modules.rag_adapter as rag_mod

    monkeypatch.setattr(
        cfg_mod.DataModulesConfig, "from_project_root",
        classmethod(lambda cls, root: object()),
    )
    monkeypatch.setattr(rag_mod, "RAGAdapter", adapter_cls)


def test_store_chunks_returns_count_from_adapter(monkeypatch, tmp_path):
    class _FakeAdapter:
        def __init__(self, config):
            self.config = config
            self.api_client = None

        async def store_chunks(self, chunks):
            return len(chunks)

    _patch_store_env(monkeypatch, _FakeAdapter)
    w = VectorProjectionWriter(tmp_path)
    assert w._store_chunks([{"content": "a"}, {"content": "b"}]) == 2


def test_store_chunks_closes_api_client(monkeypatch, tmp_path):
    closed = {"v": False}

    class _FakeApiClient:
        async def close(self):
            closed["v"] = True

    class _FakeAdapter:
        def __init__(self, config):
            self.api_client = _FakeApiClient()

        async def store_chunks(self, chunks):
            return len(chunks)

    _patch_store_env(monkeypatch, _FakeAdapter)
    w = VectorProjectionWriter(tmp_path)
    assert w._store_chunks([{"content": "a"}]) == 1
    assert closed["v"] is True


def test_store_chunks_swallows_close_exception(monkeypatch, tmp_path):
    class _FakeApiClient:
        async def close(self):
            raise RuntimeError("close failed")

    class _FakeAdapter:
        def __init__(self, config):
            self.api_client = _FakeApiClient()

        async def store_chunks(self, chunks):
            return len(chunks)

    _patch_store_env(monkeypatch, _FakeAdapter)
    w = VectorProjectionWriter(tmp_path)
    assert w._store_chunks([{"content": "a"}]) == 1


def test_store_chunks_returns_zero_on_adapter_exception(monkeypatch, tmp_path):
    class _BoomAdapter:
        def __init__(self, config):
            self.api_client = None

        async def store_chunks(self, chunks):
            raise RuntimeError("store failed")

    _patch_store_env(monkeypatch, _BoomAdapter)
    w = VectorProjectionWriter(tmp_path)
    assert w._store_chunks([{"content": "a"}]) == 0
