"""字幕时间轴对齐（可选 faster-whisper 后端，期 3 收口项）。

定位：投影侧字幕时间轴是「每句 ~3s」的占位口径；旁白/配音真实音频产出后，
用语音识别的句级时间戳把 SRT 重对齐，字幕跟着配音走。

- 后端可选：faster_whisper 未安装 → status=not_installed（不抛异常）；
  测试/离线场景经 transcribe_fn 注入 mock 段（接口与真实段形状一致）；
- 对齐策略 v0：cue 与 whisper 段按文本相似度顺序匹配（difflib，单调不回退）；
  数量相等时直接 1:1 重定时；匹配不到的 cue 保持原时间；
- 输出 SRT 文本与输入逐字一致，只改时间轴。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence

from .projector import _fmt_srt_time

# 真实后端依赖标记
WHISPER_MODULE = "faster_whisper"

# 默认模型档（CPU int8 足够句级对齐；权重首次运行自动下载）
DEFAULT_MODEL_SIZE = "small"
DEFAULT_LANGUAGE = "zh"


def whisper_available() -> bool:
    import importlib.util

    try:
        return importlib.util.find_spec(WHISPER_MODULE) is not None
    except (ImportError, ValueError):
        return False


@dataclass
class SrtCue:
    index: int
    start: float
    end: float
    text: str
    matched: bool = False  # 对齐成功与否（未匹配保持原时间）
    source: Dict[str, float] = field(default_factory=dict)  # 原 start/end


def parse_srt(text: str) -> List[SrtCue]:
    """SRT 文本 → cue 列表（容错：块序号行缺失也接受）。"""
    cues: List[SrtCue] = []
    blocks = re.split(r"\n\s*\n", text.strip())
    seq = 0
    for block in blocks:
        lines = [ln for ln in block.strip().splitlines() if ln.strip()]
        if not lines:
            continue
        time_idx = next((i for i, ln in enumerate(lines) if "-->" in ln), -1)
        if time_idx < 0:
            continue
        m = re.match(
            r"(\d+):(\d+):(\d+)[,.](\d+)\s*-->\s*(\d+):(\d+):(\d+)[,.](\d+)", lines[time_idx]
        )
        if not m:
            continue
        g = [int(x) for x in m.groups()]
        start = g[0] * 3600 + g[1] * 60 + g[2] + g[3] / 1000.0
        end = g[4] * 3600 + g[5] * 60 + g[6] + g[7] / 1000.0
        seq += 1
        cues.append(
            SrtCue(
                index=seq,
                start=start,
                end=end,
                text="\n".join(lines[time_idx + 1 :]).strip(),
            )
        )
    return cues


def render_srt(cues: Sequence[SrtCue]) -> str:
    blocks: List[str] = []
    for i, cue in enumerate(cues, 1):
        blocks.append(
            f"{i}\n{_fmt_srt_time(cue.start)} --> {_fmt_srt_time(cue.end)}\n{cue.text}"
        )
    return "\n\n".join(blocks) + ("\n" if blocks else "")


def _norm_text(s: str) -> str:
    return re.sub(r"[\s，。！？、：；「」（）〔〕·…—-]+", "", s or "").lower()


def _default_transcribe(audio_path: str | Path, model_size: str, language: str) -> List[Dict[str, Any]]:
    """真实 faster-whisper 路径（仅依赖齐备时可达）。返回 [{start, end, text}, ...]。"""
    from faster_whisper import WhisperModel  # 延迟导入：未安装时由调用方降级

    model = WhisperModel(model_size, device="cpu", compute_type="int8")
    segments, _info = model.transcribe(str(audio_path), language=language, vad_filter=True)
    return [
        {"start": float(seg.start), "end": float(seg.end), "text": seg.text}
        for seg in segments
    ]


def _match_cues_to_segments(
    cues: List[SrtCue], segments: List[Dict[str, Any]]
) -> None:
    """顺序单调匹配：数量相等 1:1；否则按文本相似度从上一匹配位置向后找最优段。"""
    if not segments:
        return
    if len(cues) == len(segments):
        for cue, seg in zip(cues, segments):
            cue.start = float(seg["start"])
            cue.end = float(seg["end"])
            cue.matched = True
            cue.source = {"start": cue.source.get("start", cue.start), "end": cue.source.get("end", cue.end)}
        return
    search_from = 0
    for cue in cues:
        target = _norm_text(cue.text)
        best_i, best_score = -1, 0.0
        for i in range(search_from, len(segments)):
            score = SequenceMatcher(None, target, _norm_text(str(segments[i].get("text", "")))).ratio()
            if score > best_score:
                best_i, best_score = i, score
        if best_i >= 0 and best_score >= 0.30:  # 相似度过低的宁可不改时间
            cue.start = float(segments[best_i]["start"])
            cue.end = float(segments[best_i]["end"])
            cue.matched = True
            search_from = best_i + 1


def align_srt_to_audio(
    srt_path: str | Path,
    audio_path: str | Path,
    out_path: Optional[str | Path] = None,
    *,
    model_size: str = DEFAULT_MODEL_SIZE,
    language: str = DEFAULT_LANGUAGE,
    transcribe_fn: Optional[Callable[[str | Path, str, str], List[Dict[str, Any]]]] = None,
) -> Dict[str, Any]:
    """SRT + 音频 → 重对齐 SRT。返回 {ok, status, cues, matched, out_path?, reason}。

    status：done / not_installed（未装 faster-whisper 且未注入 transcribe_fn）/ failed。
    """
    srt_file = Path(srt_path)
    if not srt_file.exists():
        return {"ok": False, "status": "failed", "reason": f"SRT 不存在：{srt_file}", "cues": 0, "matched": 0}
    if not Path(audio_path).exists():
        return {"ok": False, "status": "failed", "reason": f"音频不存在：{audio_path}", "cues": 0, "matched": 0}

    fn = transcribe_fn
    if fn is None:
        if not whisper_available():
            return {
                "ok": False,
                "status": "not_installed",
                "reason": (
                    "faster-whisper 未安装，无法真实对齐。安装：py -m pip install faster-whisper "
                    "（首次运行自动下载权重；测试可注入 transcribe_fn mock）"
                ),
                "cues": 0,
                "matched": 0,
            }
        fn = _default_transcribe

    cues = parse_srt(srt_file.read_text(encoding="utf-8"))
    if not cues:
        return {"ok": False, "status": "failed", "reason": "SRT 无有效 cue", "cues": 0, "matched": 0}
    for cue in cues:
        cue.source = {"start": cue.start, "end": cue.end}

    try:
        segments = fn(audio_path, model_size, language)
    except Exception as exc:
        return {"ok": False, "status": "failed", "reason": f"语音识别失败：{exc}", "cues": len(cues), "matched": 0}
    if not isinstance(segments, list):
        return {"ok": False, "status": "failed", "reason": "transcribe_fn 返回形状非法（期待段列表）", "cues": len(cues), "matched": 0}
    if not segments:
        return {"ok": False, "status": "failed", "reason": "语音识别未返回任何段，无法对齐", "cues": len(cues), "matched": 0}

    _match_cues_to_segments(cues, segments)
    matched = sum(1 for c in cues if c.matched)
    result: Dict[str, Any] = {
        "ok": True,
        "status": "done",
        "cues": len(cues),
        "matched": matched,
        "segments": len(segments),
    }
    if out_path is not None:
        out_file = Path(out_path)
        out_file.parent.mkdir(parents=True, exist_ok=True)
        out_file.write_text(render_srt(cues), encoding="utf-8", newline="\n")
        result["out_path"] = str(out_file)
    return result
