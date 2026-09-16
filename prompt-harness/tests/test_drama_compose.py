"""PyAV 合成测试：纯色/渐变 PNG + 正弦 wav → 竖屏 mp4 → PyAV 回读断言。

依赖 PyAV；未安装时整组跳过（阻塞记录在 compose.py 的 ImportError 文案里）。
"""
from __future__ import annotations

import math
import wave
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

av = pytest.importorskip("av")

from prompt_harness.drama.compose import av_available, compose_video  # noqa: E402

W, H, FPS = 1080, 1920, 24
SHOT_DURATION = 1.0


def _make_frames(tmp_path: Path):
    """两帧素材：垂直渐变 + 纯色。"""
    frames_dir = tmp_path / "frames"
    frames_dir.mkdir()
    grad = np.zeros((H, W, 3), dtype=np.uint8)
    grad[:, :, 0] = np.linspace(30, 220, W, dtype=np.uint8)[None, :]
    grad[:, :, 2] = np.linspace(200, 40, H, dtype=np.uint8)[:, None]
    Image.fromarray(grad).save(frames_dir / "sh_a.png")
    Image.fromarray(np.full((H, W, 3), (40, 160, 90), dtype=np.uint8)).save(frames_dir / "sh_b.png")
    return frames_dir


def _make_sine_wav(path: Path, seconds: float = 1.0, freq: float = 440.0, rate: int = 44100):
    n = int(seconds * rate)
    t = np.arange(n) / rate
    data = (0.6 * np.sin(2 * math.pi * freq * t) * 32767).astype("<i2")
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(rate)
        wf.writeframes(data.tobytes())
    return path


def _shots_doc(narration_texts=("镜头甲", "镜头乙")):
    return {
        "schema_version": "0.1",
        "pack_id": "test",
        "size": {"width": W, "height": H},
        "fps": FPS,
        "shots": [
            {
                "shot_id": "sh_a",
                "node_id": "n1",
                "index": 0,
                "line_kind": "narration",
                "type": "still_push",
                "camera": {"move": "push_in", "from_scale": 1.0, "to_scale": 1.08},
                "background": "bg_x",
                "track": "narration",
                "duration": SHOT_DURATION,
                "text": narration_texts[0],
                "speaker": None,
                "speaker_name": None,
                "expression": None,
                "stage_cue": None,
            },
            {
                "shot_id": "sh_b",
                "node_id": "n1",
                "index": 1,
                "line_kind": "dialogue",
                "type": "shot_reverse_shot",
                "camera": {"move": "pan", "from_scale": 1.05, "to_scale": 1.05, "pan": "left"},
                "background": "bg_x",
                "track": "dialogue",
                "duration": SHOT_DURATION,
                "text": narration_texts[1],
                "speaker": "char_a",
                "speaker_name": "甲",
                "expression": None,
                "stage_cue": None,
            },
        ],
    }


def test_av_available_true():
    assert av_available() is True


def test_compose_two_shots_with_audio(tmp_path):
    frames_dir = _make_frames(tmp_path)
    audio_dir = tmp_path / "audio"
    audio_dir.mkdir()
    _make_sine_wav(audio_dir / "sh_a.wav", seconds=SHOT_DURATION)
    # sh_b 无 wav → 补静音，总长不变
    out = tmp_path / "drama.mp4"
    info = compose_video(
        _shots_doc(),
        frames_dir=frames_dir,
        audio_dir=audio_dir,
        out_path=out,
        fps=FPS,
    )
    assert out.exists() and out.stat().st_size > 10_000
    assert info["streams"] == {"video": 1, "audio": 1}
    assert (info["width"], info["height"]) == (W, H)
    assert info["frames"] == int(SHOT_DURATION * 2 * FPS)
    assert abs(info["duration"] - 2.0) < 0.3  # 1s + 1s（静音补齐）
    # 音频时长也应 ≈ 视频时长
    with av.open(str(out)) as c:
        astream = c.streams.audio[0]
        assert astream.codec_context.sample_rate == 44100


def test_compose_with_placeholder_frames_only(tmp_path):
    """无帧目录/无音轨：占位深色底 + 静音轨，规格仍正确。"""
    out = tmp_path / "placeholder.mp4"
    info = compose_video(
        _shots_doc(("旁白字幕", "")),
        frames_dir=None,
        audio_dir=None,
        out_path=out,
        fps=12,
    )
    assert (info["width"], info["height"]) == (W, H)
    assert info["streams"] == {"video": 1, "audio": 1}
    assert abs(info["duration"] - 2.0) < 0.3


def test_compose_rejects_empty_shots(tmp_path):
    with pytest.raises(ValueError):
        compose_video({"shots": []}, out_path=tmp_path / "x.mp4")


def test_compose_pan_and_closeup_moves(tmp_path):
    """pan / static 运镜均可用（不崩、规格正确）。"""
    frames_dir = _make_frames(tmp_path)
    doc = _shots_doc()
    doc["shots"][0]["camera"] = {"move": "static", "from_scale": 1.0, "to_scale": 1.0}
    doc["shots"][1]["type"] = "closeup"
    doc["shots"][1]["camera"] = {"move": "push_in", "from_scale": 1.0, "to_scale": 1.14}
    info = compose_video(doc, frames_dir=frames_dir, out_path=tmp_path / "moves.mp4")
    assert info["streams"]["video"] == 1
