"""字幕对齐接口测试：SRT 解析/回写 + mock transcribe 对齐 + 未装 faster-whisper 降级。"""
from __future__ import annotations

from pathlib import Path

from prompt_harness.drama.align import (
    align_srt_to_audio,
    parse_srt,
    render_srt,
    whisper_available,
)

SAMPLE_SRT = """1
00:00:00,000 --> 00:00:03,000
深夜的旧教室里粉笔灰下沉

2
00:00:03,000 --> 00:00:06,000
这是哪儿我明明记得刚交完卷子

3
00:00:06,000 --> 00:00:09,000
谁在看我
"""


def _write_srt(tmp_path: Path) -> Path:
    p = tmp_path / "subtitles.srt"
    p.write_text(SAMPLE_SRT, encoding="utf-8")
    return p


def _write_wav(tmp_path: Path) -> Path:
    import math
    import wave

    import numpy as np

    p = tmp_path / "audio.wav"
    rate = 8000
    n = int(rate * 9)
    t = np.arange(n) / rate
    data = (0.3 * np.sin(2 * math.pi * 300 * t) * 32767).astype("<i2")
    with wave.open(str(p), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(rate)
        wf.writeframes(data.tobytes())
    return p


def test_parse_and_render_srt_roundtrip():
    cues = parse_srt(SAMPLE_SRT)
    assert [c.index for c in cues] == [1, 2, 3]
    assert cues[0].start == 0.0 and cues[0].end == 3.0
    assert cues[1].text == "这是哪儿我明明记得刚交完卷子"
    rendered = render_srt(cues)
    assert parse_srt(rendered)[2].text == cues[2].text
    assert "00:00:06,000 --> 00:00:09,000" in rendered


def test_align_mock_equal_count_retimes_all(tmp_path):
    srt, wav = _write_srt(tmp_path), _write_wav(tmp_path)
    segments = [
        {"start": 0.2, "end": 2.5, "text": "深夜的旧教室里，粉笔灰在下沉"},
        {"start": 3.1, "end": 5.4, "text": "这是哪儿？我明明记得刚交完卷子"},
        {"start": 6.0, "end": 8.2, "text": "谁在看我"},
    ]
    out = tmp_path / "aligned.srt"
    result = align_srt_to_audio(
        srt, wav, out_path=out,
        transcribe_fn=lambda audio, model_size, language: segments,
    )
    assert result["ok"] and result["status"] == "done"
    assert result["matched"] == 3 and result["segments"] == 3
    cues = parse_srt(out.read_text(encoding="utf-8"))
    assert abs(cues[0].start - 0.2) < 1e-6 and abs(cues[1].end - 5.4) < 1e-6
    # 文本逐字保留，只改时间轴
    assert cues[0].text == "深夜的旧教室里粉笔灰下沉"
    assert cues[2].end == 8.2


def test_align_mock_unequal_count_monotonic_match(tmp_path):
    srt, wav = _write_srt(tmp_path), _write_wav(tmp_path)
    segments = [
        {"start": 0.4, "end": 2.9, "text": "深夜的旧教室里，粉笔灰缓缓下沉，纸人坐在座位上"},
        {"start": 3.2, "end": 5.8, "text": "这是哪儿？我明明记得刚交完最后一科的卷子"},
        {"start": 6.1, "end": 8.9, "text": "谁在看我？是谁在看我"},
        {"start": 9.0, "end": 10.0, "text": "多余的识别段"},
    ]
    result = align_srt_to_audio(
        srt, wav, out_path=tmp_path / "aligned.srt",
        transcribe_fn=lambda audio, model_size, language: segments,
    )
    assert result["ok"] and result["matched"] == 3
    cues = parse_srt((tmp_path / "aligned.srt").read_text(encoding="utf-8"))
    assert abs(cues[0].start - 0.4) < 1e-6  # 相似度最高段匹配
    assert abs(cues[1].start - 3.2) < 1e-6
    assert abs(cues[2].start - 6.1) < 1e-6


def test_align_not_installed_without_backend(tmp_path):
    srt, wav = _write_srt(tmp_path), _write_wav(tmp_path)
    if whisper_available():  # 本机已装 faster-whisper 则本用例不适用
        return
    result = align_srt_to_audio(srt, wav)
    assert result["status"] == "not_installed"
    assert "faster-whisper" in result["reason"]
    assert "pip install" in result["reason"]


def test_align_missing_inputs_failed(tmp_path):
    result = align_srt_to_audio(tmp_path / "nope.srt", tmp_path / "nope.wav")
    assert result["status"] == "failed"
    srt, wav = _write_srt(tmp_path), _write_wav(tmp_path)
    result2 = align_srt_to_audio(srt, wav, transcribe_fn=lambda a, m, l: [])
    assert result2["status"] == "failed"  # 识别段为空 → 无法对齐


def test_align_transcribe_error_maps_to_failed(tmp_path):
    srt, wav = _write_srt(tmp_path), _write_wav(tmp_path)

    def boom(audio, model_size, language):
        raise RuntimeError("模型加载失败")

    result = align_srt_to_audio(srt, wav, transcribe_fn=boom)
    assert result["status"] == "failed"
    assert "模型加载失败" in result["reason"]
