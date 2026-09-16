"""AssetStore（内容寻址资产库）单元测试。"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from prompt_harness.media.asset_store import (
    ASSET_KINDS,
    AssetStore,
    default_asset_library_root,
)

FIXTURE_PACK = Path(__file__).resolve().parent / "fixtures" / "adaptation_pack_demo"


def test_env_overrides_root(monkeypatch, tmp_path):
    monkeypatch.setenv("AINOVEL_ASSET_LIBRARY", str(tmp_path / "lib_env"))
    assert default_asset_library_root() == tmp_path / "lib_env"
    store = AssetStore()  # 显式无参 → 走 env
    assert store.root == tmp_path / "lib_env"


def test_default_root_is_prompt_harness_dot_asset_library(monkeypatch):
    monkeypatch.delenv("AINOVEL_ASSET_LIBRARY", raising=False)
    root = default_asset_library_root()
    assert root.name == ".asset_library"
    assert root.parent.name == "prompt-harness"


def test_put_bytes_content_addressed_and_dedup(tmp_path):
    store = AssetStore(tmp_path / "lib")
    cid1 = store.put_bytes(b"hello image", "background", meta={"prompt": "教室"})
    cid2 = store.put_bytes(b"hello image", "background")  # 同内容 → 去重
    other = store.put_bytes(b"other", "voice")
    assert cid1 == cid2
    assert cid1 != other
    assert len(cid1) == 64  # sha256 hex
    blob_files = list((tmp_path / "lib" / "blobs").rglob("*.bin"))
    assert len(blob_files) == 2  # 两个不同内容只落两个文件
    assert store.get_bytes(cid1) == b"hello image"
    assert store.blob_path(cid1).exists()


def test_put_bytes_rejects_bad_kind(tmp_path):
    store = AssetStore(tmp_path / "lib")
    with pytest.raises(ValueError):
        store.put_bytes(b"x", "not_a_kind")
    with pytest.raises(TypeError):
        store.put_bytes("not bytes", "ui")


def test_entry_state_machine(tmp_path):
    store = AssetStore(tmp_path / "lib")
    entry = store.register("bg_classroom", "background", meta={"prompt": "p"}, used_by=["n001"])
    assert entry.status == "pending"
    assert store.get_entry("bg_classroom").used_by == ["n001"]
    # done 需要 blob 先入库
    with pytest.raises(KeyError):
        store.mark_done("bg_classroom", "0" * 64)
    cid = store.put_bytes(b"png-bytes", "background")
    done = store.mark_done("bg_classroom", cid)
    assert done.status == "done" and done.content_id == cid
    failed = store.mark_failed("bg_classroom", "生成被拒")
    assert failed.status == "failed" and failed.error == "生成被拒"
    # 幂等 register：不覆盖已有
    again = store.register("bg_classroom", "background", meta={"prompt": "new"})
    assert again.meta.get("prompt") == "p"
    # resolve_path 走 entry → blob
    assert store.resolve_path("bg_classroom") == store.blob_path(cid)
    with pytest.raises(KeyError):
        store.mark_done("missing_entry", cid)


def test_manifest_persistence_roundtrip(tmp_path):
    store = AssetStore(tmp_path / "lib")
    cid = store.put_bytes(b"payload", "sfx")
    store.register("sfx_paper", "sfx", used_by=["n001"])
    store.mark_done("sfx_paper", cid)
    # 新实例同根 → manifest 恢复
    store2 = AssetStore(tmp_path / "lib")
    assert store2.has_blob(cid)
    e = store2.get_entry("sfx_paper")
    assert e.status == "done" and e.content_id == cid
    assert store2.get_bytes("sfx_paper") == b"payload"  # 语义 id 取字节
    manifest = json.loads((tmp_path / "lib" / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["version"] == 1 and "sfx_paper" in manifest["entries"]


def test_ingest_pack_assets_registers_pending_items(tmp_path):
    lib_root = tmp_path / "lib"
    store = AssetStore(lib_root)
    stats = store.ingest_pack_assets(FIXTURE_PACK)
    assert stats["total"] == 8
    assert stats["registered"] == 7  # 7 个 pending；ui_choice_frame 已带 hash → done
    assert stats["by_kind"]["background"] == 2
    entry = store.get_entry("bg_classroom_night")
    assert entry.status == "pending"
    assert entry.used_by == ["n001"]
    assert entry.meta["provider_hint"] == "seedream"
    assert entry.meta["source"] == "adaptation_pack"
    assert entry.content_id is None
    # 已带 hash 的清单项：done + content_id=hash
    done_entry = store.get_entry("ui_choice_frame")
    assert done_entry.status == "done"
    assert done_entry.content_id == "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
    # 只登记不生成：blob 目录不应有任何内容
    assert not (lib_root / "blobs").exists() or not any((lib_root / "blobs").rglob("*.bin"))
    # 幂等：再次 ingest 全部 already
    stats2 = AssetStore(lib_root).ingest_pack_assets(FIXTURE_PACK)
    assert stats2["registered"] == 0 and stats2["already"] == 8


def test_ingest_missing_assets_json(tmp_path):
    store = AssetStore(tmp_path / "lib")
    with pytest.raises(FileNotFoundError):
        store.ingest_pack_assets(tmp_path)


def test_asset_kinds_match_spec():
    # spec §5.6 kind 枚举 + "video"（期 3 Seedance 视频片段，由驱动产出后入库，pack 清单不声明）
    assert set(ASSET_KINDS) == {"background", "portrait", "model3d", "bgm", "sfx", "voice", "ui", "video"}
