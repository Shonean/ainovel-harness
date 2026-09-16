"""compose 期 3 增强测试：BGM 混音+ducking、crossfade 转场、无 BGM 单轨回归。"""
from __future__ import annotations

import math
import wave
from pathlib import Path

import numpy as np
import pytest

av = pytest.importorskip("av")

from prompt_harness.drama.compose import (  # noqa: E402
    DEFAULT_BGM_GAIN,
    DEFAULT_DUCK_GAIN,
    compose_video,
    resolve_bgm_path,
)
from prompt_harness.media.asset_store import AssetStore  # noqa: E402

RATE = 44100


def _make_sine_wav(path: Path, seconds: float, freq: float = 440.0, amp: float = 0.6):
    n = int(seconds * RATE)
    t = np.arange(n) / RATE
    data = (amp * np.sin(2 * math.pi * freq * t) * 32767).astype("<i2")
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(RATE)
        wf.writeframes(data.tobytes())
    return path


def _shots_doc_2shots():
    return {
        "size": {"width": 270, "height": 480},
        "fps": 6,
        "shots": [
            {
                "shot_id": "sh_a",
                "line_kind": "narration",
                "type": "still_push",
                "camera": {"move": "static", "from_scale": 1.0, "to_scale": 1.0},
                "background": "bg_x",
                "track": "narration",
                "duration": 1.0,
                "text": "",
            },
            {
                "shot_id": "sh_b",
                "line_kind": "narration",
                "type": "still_push",
                "camera": {"move": "static", "from_scale": 1.0, "to_scale": 1.0},
                "background": "bg_x",
                "track": "narration",
                "duration": 1.0,
                "text": "",
            },
        ],
    }


def _decode_audio_mono(path: Path) -> np.ndarray:
    """解码整条音轨 → 单声道 float（-1..1）。"""
    with av.open(str(path)) as c:
        chunks = []
        for frame in c.decode(audio=0):
            arr = frame.to_ndarray()
            if arr.dtype.kind == "i":
                arr = arr.astype(np.float32) / 32768.0
            if arr.ndim == 1:  # packed s16: (1, n*ch) → (ch, n)
                arr = arr.reshape(2, -1) if arr.size % 2 == 0 else arr.reshape(1, -1)
            chunks.append(arr.mean(axis=0))
    return np.concatenate(chunks) if chunks else np.zeros(0)


def _rms(x: np.ndarray) -> float:
    return float(np.sqrt(np.mean(np.square(x)))) if x.size else 0.0


# --------------------------------------------------------------------- BGM 混音


def test_bgm_mixes_and_ducks_under_voice(tmp_path):
    """shot A 有旁白（正弦 wav）→ BGM 被 duck；shot B 静音 → BGM 全增益可闻。"""
    audio_dir = tmp_path / "audio"
    audio_dir.mkdir()
    _make_sine_wav(audio_dir / "sh_a.wav", 1.0, freq=880.0, amp=0.7)
    bgm_path = _make_sine_wav(tmp_path / "bgm.wav", 3.0, freq=220.0, amp=0.9)

    doc = _shots_doc_2shots()
    out = tmp_path / "mix.mp4"
    info = compose_video(doc, audio_dir=audio_dir, bgm=str(bgm_path), out_path=out)
    assert info["bgm"] == str(bgm_path)
    assert info["streams"] == {"video": 1, "audio": 1}

    mono = _decode_audio_mono(out)
    assert mono.size >= int(2.0 * RATE) * 0.9  # 音频时长 ≈ 正片时长
    seg_a = mono[int(0.1 * RATE) : int(0.8 * RATE)]  # 旁白段（避开过渡）
    seg_b = mono[int(1.3 * RATE) : int(1.9 * RATE)]  # 纯 BGM 段
    rms_a, rms_b = _rms(seg_a), _rms(seg_b)
    assert rms_b > 0.02, "静音段应能听到 BGM"
    assert rms_a > rms_b * 1.3, "旁白段 BGM 应被压低（ducking），旁白主导响度"


def test_no_bgm_keeps_single_track(tmp_path):
    """无 bgm：行为与基线一致（单轨、静音段为 0）。"""
    audio_dir = tmp_path / "audio"
    audio_dir.mkdir()
    _make_sine_wav(audio_dir / "sh_a.wav", 1.0)
    out = tmp_path / "plain.mp4"
    info = compose_video(_shots_doc_2shots(), audio_dir=audio_dir, out_path=out)
    assert info["bgm"] is None
    mono = _decode_audio_mono(out)
    assert _rms(mono[int(1.3 * RATE) : int(1.9 * RATE)]) < 1e-4  # B 段仍是静音


def test_bgm_via_store_asset_id(tmp_path):
    """shots_doc['bgm'] = 'bgm_anxious'（资产 id）→ 经 store 解析入库字节。"""
    bgm_path = _make_sine_wav(tmp_path / "bgm_anxious.wav", 3.0, freq=220.0, amp=0.9)
    store = AssetStore(tmp_path / "lib")
    store.register("bgm_anxious", "bgm", used_by=["n001", "n002"])
    store.mark_done("bgm_anxious", store.put_bytes(bgm_path.read_bytes(), "bgm"))

    doc = _shots_doc_2shots()
    doc["bgm"] = "bgm_anxious"
    out = tmp_path / "asset_bgm.mp4"
    info = compose_video(doc, out_path=out, store=store)
    assert info["bgm"] is not None
    assert _rms(_decode_audio_mono(out)[int(1.3 * RATE) : int(1.9 * RATE)]) > 0.02


def test_bgm_dict_and_unresolvable_ref(tmp_path):
    bgm_path = _make_sine_wav(tmp_path / "b.wav", 1.0)
    # dict 形状
    assert resolve_bgm_path({"path": str(bgm_path)}) == bgm_path
    # 未知资产 id 且无 store → None（保持单轨，不报错）
    assert resolve_bgm_path("bgm_unknown_mood") is None
    assert resolve_bgm_path(None) is None
    doc = _shots_doc_2shots()
    doc["bgm"] = "bgm_unknown_mood"
    out = tmp_path / "fallback.mp4"
    info = compose_video(doc, out_path=out)
    assert info["bgm"] is None  # 解析失败不阻断合成
    assert info["streams"]["audio"] == 1


# --------------------------------------------------------------------- 转场


def test_crossfade_transition(tmp_path):
    doc = _shots_doc_2shots()
    doc["shots"][1]["transition"] = {"type": "crossfade", "duration": 0.4}
    out = tmp_path / "xfade.mp4"
    info = compose_video(doc, out_path=out)
    assert info["transitions"] == {"cut": 1, "crossfade": 1}
    assert info["streams"] == {"video": 1, "audio": 1}
    assert abs(info["duration"] - 2.0) < 0.3  # 溶解窗在本镜头头部，总时长不变
    assert out.stat().st_size > 2_000  # 270x480 纯色小视频，压缩率极高


def test_crossfade_string_shorthand_and_first_shot_cut(tmp_path):
    doc = _shots_doc_2shots()
    doc["shots"][0]["transition"] = "crossfade"  # 首镜头无上一帧 → 按硬切计
    doc["shots"][1]["transition"] = "crossfade"
    info = compose_video(doc, out_path=tmp_path / "x2.mp4")
    assert info["transitions"] == {"cut": 1, "crossfade": 1}


def test_default_transitions_all_cut(tmp_path):
    info = compose_video(_shots_doc_2shots(), out_path=tmp_path / "cuts.mp4")
    assert info["transitions"] == {"cut": 2, "crossfade": 0}


# --------------------------------------------------------------------- 信息口径


def test_bgm_gain_bounds_use_defaults():
    assert 0 < DEFAULT_DUCK_GAIN < DEFAULT_BGM_GAIN < 1.0
