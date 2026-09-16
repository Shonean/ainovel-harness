"""投影↔资产库回填测试：done 资产路径回写 shots.json → compose 直接消费（fixture 全链）。"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from PIL import Image

from prompt_harness.drama.projector import (
    backfill_pack_drama,
    backfill_shot_assets,
    load_portrait_map,
    project_pack,
    project_to_drama,
)
from prompt_harness.media.asset_store import AssetStore

TESTS_DIR = Path(__file__).resolve().parent
FIXTURE_PACK = TESTS_DIR / "fixtures" / "adaptation_pack_demo"


def _make_store_with_done_assets(tmp_path: Path) -> tuple[AssetStore, dict[str, bytes]]:
    """手工构造 done 状态资产：背景 ×2 + 主角立绘，走 put_bytes → mark_done。"""
    store = AssetStore(tmp_path / "lib")
    blobs = {
        "bg_classroom_night": b"\x89PNG bg-classroom-bytes",
        "spr_chen_default": b"\x89PNG portrait-chen-bytes",
    }
    store.register("bg_classroom_night", "background", used_by=["n001"])
    cid = store.put_bytes(blobs["bg_classroom_night"], "background")
    store.mark_done("bg_classroom_night", cid)
    store.register("spr_chen_default", "portrait", used_by=["n001", "n002"])
    cid = store.put_bytes(blobs["spr_chen_default"], "portrait")
    store.mark_done("spr_chen_default", cid)
    # bg_hallway_dusk 登记 pending：未生成的引用不回填
    store.register("bg_hallway_dusk", "background")
    return store, blobs


def test_load_portrait_map_from_fixture():
    mapping = load_portrait_map(FIXTURE_PACK)
    assert mapping["char_chen"] == "spr_chen_default"
    assert mapping["char_lin"] == "spr_lin_default"


def test_backfill_fills_only_done_assets(tmp_path):
    store, _ = _make_store_with_done_assets(tmp_path)
    shots_doc = project_pack(FIXTURE_PACK)
    stats = backfill_shot_assets(shots_doc, store, portrait_map=load_portrait_map(FIXTURE_PACK))
    by_id = {s["shot_id"]: s for s in shots_doc["shots"]}
    # done 背景 → background_path
    assert "background_path" in by_id["n001_s00"]  # bg_classroom_night done
    assert Path(by_id["n001_s00"]["background_path"]).is_file()
    # pending 背景 → 不回填，原 asset_id 保留
    assert "background_path" not in by_id["n002_s00"]
    assert by_id["n002_s00"]["background"] == "bg_hallway_dusk"
    # done 立绘 → 经 portrait_map 由 speaker 解析
    assert by_id["n001_s03"]["speaker"] == "char_chen"
    assert Path(by_id["n001_s03"]["portrait_path"]).is_file()
    # 无 speaker 的旁白 → 无立绘
    assert "portrait_path" not in by_id["n001_s00"]
    # n001 4 镜头背景 done；n001_s01/s03、n002_s02 立绘 done（spr_lin_default 未生成）
    assert stats["background_filled"] == 4 and stats["portrait_filled"] == 3
    assert stats["pending"] == 3  # n002_s00/s01/s03：引用了资产但都未就绪（end_card 不计）


def test_backfill_pack_drama_writes_shots_json(tmp_path):
    """pack 复制到 tmp → 投影落盘 → 回填写回 shots.json → compose 可读。"""
    av = pytest.importorskip("av")  # noqa: F841 — compose 依赖
    pack = tmp_path / "pack"
    pack.mkdir()
    for name in ("story.json", "characters.json", "pack.json"):
        (pack / name).write_text((FIXTURE_PACK / name).read_text(encoding="utf-8"), encoding="utf-8")
    project_to_drama(pack)
    shots_path = pack / "drama" / "shots.json"
    assert shots_path.exists()
    assert "background_path" not in shots_path.read_text(encoding="utf-8")

    store, _ = _make_store_with_done_assets(tmp_path)
    result = backfill_pack_drama(pack, store)
    written = json.loads(shots_path.read_text(encoding="utf-8"))
    by_id = {s["shot_id"]: s for s in written["shots"]}
    assert "background_path" in by_id["n001_s00"]  # 已写回
    assert result["shots_path"] == str(shots_path)
    assert result["stats"]["background_filled"] == 4

    # compose 消费：无 frames_dir，直接用回填的 background_path 作帧源
    from prompt_harness.drama.compose import compose_video

    info = compose_video(written, out_path=tmp_path / "drama.mp4", fps=6)
    assert info["streams"] == {"video": 1, "audio": 1}
    assert info["frames"] > 0


def test_compose_prefers_backfilled_background_over_placeholder(tmp_path):
    """回填路径真正生效：背景图颜色出现在成片帧里，而非哈希占位底。"""
    av = pytest.importorskip("av")  # noqa: F841
    import numpy as np

    from prompt_harness.drama.compose import compose_video

    # 纯红背景图（模拟 seedream 产物入库后回填的 background_path）
    red = np.zeros((64, 64, 3), dtype=np.uint8)
    red[:, :, 0] = 255
    bg_png = tmp_path / "bg.png"
    Image.fromarray(red).save(bg_png)

    shots_doc = {
        "size": {"width": 270, "height": 480},
        "fps": 6,
        "shots": [
            {
                "shot_id": "sh_red",
                "line_kind": "narration",
                "type": "still_push",
                "camera": {"move": "static", "from_scale": 1.0, "to_scale": 1.0},
                "background": "bg_red",
                "background_path": str(bg_png),
                "track": "narration",
                "duration": 0.5,
                "text": "",
            }
        ],
    }
    # 占位底口径：哈希派生色亮度低（v=0.24），与纯红区分明显
    out = tmp_path / "red.mp4"
    compose_video(shots_doc, out_path=out)
    center = np.asarray(_first_frame(out))[200:280, 100:170].reshape(-1, 3).mean(axis=0)
    assert center[0] > 180 and center[1] < 90 and center[2] < 90  # 红色主导 → 用了回填资产

    # 对照组：同 doc 去掉回填路径 → 占位深色底，红色不主导
    shots_doc2 = json.loads(json.dumps(shots_doc))
    del shots_doc2["shots"][0]["background_path"]
    out2 = tmp_path / "placeholder.mp4"
    compose_video(shots_doc2, out_path=out2)
    center2 = np.asarray(_first_frame(out2))[200:280, 100:170].reshape(-1, 3).mean(axis=0)
    assert not (center2[0] > 180 and center2[1] < 90 and center2[2] < 90)


def test_composite_overlays_portrait(tmp_path):
    """portrait_path 叠加：绿色立绘出现在画面右下区域。"""
    av = pytest.importorskip("av")  # noqa: F841
    import numpy as np

    from prompt_harness.drama.compose import compose_video

    green = np.zeros((200, 100, 4), dtype=np.uint8)
    green[:, :, 1] = 255
    green[:, :, 3] = 255
    portrait_png = tmp_path / "spr.png"
    Image.fromarray(green, "RGBA").save(portrait_png)

    shots_doc = {
        "size": {"width": 270, "height": 480},
        "fps": 6,
        "shots": [
            {
                "shot_id": "sh_p",
                "line_kind": "dialogue",
                "type": "shot_reverse_shot",
                "camera": {"move": "static", "from_scale": 1.0, "to_scale": 1.0},
                "background": None,
                "track": "dialogue",
                "duration": 0.5,
                "text": "",
                "portrait_path": str(portrait_png),
            }
        ],
    }
    out = tmp_path / "portrait.mp4"
    compose_video(shots_doc, out_path=out)
    frame = np.asarray(_first_frame(out))
    # 立绘区：高度 55% × 底部 12% 边距 → 底部 1/3、右侧 1/3 内必有绿色像素
    region = frame[int(480 * 0.55) : int(480 * 0.85), int(270 * 0.7) : 270]
    green_pixels = ((region[:, :, 1] > 180) & (region[:, :, 0] < 90)).sum()
    assert green_pixels > 100


def _first_frame(mp4: Path):
    import av

    with av.open(str(mp4)) as c:
        for frame in c.decode(video=0):
            return frame.to_ndarray(format="rgb24")
    raise AssertionError("无视频帧")
