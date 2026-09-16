"""漫剧合成：shots + 每镜头静帧 + 可选音轨 → 竖屏 1080x1920 mp4（PyAV 进程内直连 ffmpeg）。

- Ken Burns 运镜：push_in（缩放）/ pan（平移）/ static，PIL 逐帧仿射裁切；
- 字幕烧录：PIL 预渲染字条（半透明黑条 + 白字）贴帧底部，不依赖 drawtext/系统字体配置；
- 音轨：逐镜头 `<shot_id>.wav`，缺失的镜头按时长补静音（每句 ~3s 口径由 shots.duration 决定）；
- BGM 混音（期 3 增强）：`bgm` 参数或 shots_doc["bgm"]（bgm_<mood> 资产 id / 路径 / dict），
  与旁白同播时简单 ducking（旁白段压低 BGM，段边界线性过渡）；无 bgm 保持现状单轨；
- 转场（期 3 增强）：shot.transition 缺省硬切（cut）；"crossfade" / {"type":"crossfade","duration":0.5}
  为与上一镜头末帧的交叉溶解（溶解窗在本镜头头部，总时长不变）；
- 资产回填消费（期 3 增强）：shot["background_path"] / shot["portrait_path"]（由 projector 回填函数
  从资产库写回）直接作为帧源/立绘叠加，优先级：frames_dir 文件 > background_path > 占位底；
- 全程 PyAV（L1 集成），无外部 ffmpeg exe。

帧文件约定：`<frames_dir>/<shot_id>.png`；音轨文件约定：`<audio_dir>/<shot_id>.wav`。
"""
from __future__ import annotations

import colorsys
import hashlib
import wave
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
from PIL import Image, ImageDraw, ImageFont

try:  # PyAV 可选：未安装时 compose_video 明确报错，测试侧 importorskip
    import av
except ImportError:  # pragma: no cover
    av = None

AUDIO_RATE = 44100
AUDIO_CHANNELS = 2

# BGM 混音默认参数（振幅增益 0..1）
DEFAULT_BGM_GAIN = 0.30   # 无旁白段 BGM 增益
DEFAULT_DUCK_GAIN = 0.12  # 旁白段 ducking 后增益
DEFAULT_DUCK_FADE = 0.3   # 段边界线性过渡秒数
# 立绘叠加
PORTRAIT_HEIGHT_RATIO = 0.55
PORTRAIT_MARGIN_X_RATIO = 0.05
PORTRAIT_BOTTOM_RATIO = 0.12

_FONT_CANDIDATES = (
    r"C:\Windows\Fonts\msyh.ttc",
    r"C:\Windows\Fonts\msyhbd.ttc",
    r"C:\Windows\Fonts\simhei.ttf",
    r"C:\Windows\Fonts\simsun.ttc",
    r"C:\Windows\Fonts\arial.ttf",
)


def av_available() -> bool:
    return av is not None


# --------------------------------------------------------------------------- 字幕渲染


def _load_font(px: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for path in _FONT_CANDIDATES:
        try:
            return ImageFont.truetype(path, px)
        except OSError:
            continue
    return ImageFont.load_default()


def _wrap_text(text: str, font: Any, max_w: int, max_lines: int = 3) -> List[str]:
    """中文按字断行（v0 简单稳定），最多 max_lines 行，超出截断加省略号。"""
    lines: List[str] = []
    for para in text.split("\n"):
        cur = ""
        for ch in para:
            if font.getlength(cur + ch) <= max_w or not cur:
                cur += ch
            else:
                lines.append(cur)
                cur = ch
                if len(lines) >= max_lines:
                    break
        else:
            if cur:
                lines.append(cur)
        if len(lines) >= max_lines:
            break
    if not lines:
        lines = [""]
    if len(text.replace("\n", "")) > sum(len(x) for x in lines) and len(lines) >= max_lines:
        lines[-1] = lines[-1][:-1] + "…" if len(lines[-1]) > 1 else "…"
    return lines


def render_subtitle_strip(text: str, width: int, height: int, font_px: Optional[int] = None) -> Image.Image:
    """预渲染一条字幕（RGBA）。字条宽 = 画面宽，贴帧时放底部。"""
    if font_px is None:
        font_px = max(22, width // 28)
    font = _load_font(font_px)
    max_w = int(width * 0.88)
    lines = _wrap_text(text, font, max_w)
    line_h = font_px + int(font_px * 0.55)
    pad_x, pad_y = int(width * 0.03), int(font_px * 0.5)
    strip_h = pad_y * 2 + line_h * len(lines)
    strip = Image.new("RGBA", (width, strip_h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(strip)
    draw.rectangle([0, 0, width - 1, strip_h - 1], fill=(0, 0, 0, 165))
    y = pad_y
    for line in lines:
        w = font.getlength(line)
        draw.text(((width - w) / 2, y), line, font=font, fill=(255, 255, 255, 255))
        y += line_h
    return strip


# --------------------------------------------------------------------------- 帧与运镜


def _cover(img: Image.Image, width: int, height: int, headroom: float = 1.2) -> Image.Image:
    """源帧放大 cover 到至少 (w*headroom, h*headroom)，给 Ken Burns 留运镜余量。"""
    src_w, src_h = img.size
    scale = max(width * headroom / src_w, height * headroom / src_h)
    return img.resize((max(width, int(src_w * scale)), max(height, int(src_h * scale))), Image.BILINEAR)


def _placeholder_frame(background: Optional[str], width: int, height: int) -> Image.Image:
    """缺帧兜底：由 background id 哈希派生一个稳定深色底（确定性）。"""
    seed = int(hashlib.sha256((background or "none").encode("utf-8")).hexdigest()[:8], 16)
    hue = (seed % 360) / 360.0
    sat = 0.22 + (seed >> 8 % 16) / 16 * 0.12
    r, g, b = colorsys.hsv_to_rgb(hue, sat, 0.24)
    img = Image.new("RGB", (width, height), (int(r * 255), int(g * 255), int(b * 255)))
    return img


def _ken_burns_frame(
    base: Image.Image,
    width: int,
    height: int,
    t: float,
    camera: Dict[str, Any],
) -> Image.Image:
    """t∈[0,1] → 仿射运镜帧：缩放/平移线性插值。"""
    move = str(camera.get("move", "push_in"))
    from_scale = float(camera.get("from_scale", 1.0))
    to_scale = float(camera.get("to_scale", 1.0))
    scale = from_scale + (to_scale - from_scale) * t
    scale = max(scale, 1.0)
    bw, bh = base.size
    win_w = min(bw, int(width * scale))
    win_h = min(bh, int(height * scale))
    max_off_x = bw - win_w
    if move == "pan":
        direction = 1 if camera.get("pan") == "right" else -1
        off_x = int(max_off_x / 2 * (2 * t - 1) * direction)
    elif move == "static":
        off_x = max_off_x // 2
    else:  # push_in 等缩放运镜：窗口中心固定
        off_x = max_off_x // 2
    off_x = max(0, min(max_off_x, off_x))
    off_y = (bh - win_h) // 2
    crop = base.crop((off_x, off_y, off_x + win_w, off_y + win_h))
    return crop.resize((width, height), Image.BILINEAR)


def _load_image_source(path: Any) -> Optional[Image.Image]:
    """读帧源图片（含回填的资产路径）；坏文件返回 None（调用方回退占位底，不崩合成）。"""
    try:
        return Image.open(str(path)).convert("RGB")
    except Exception:
        return None


def _overlay_portrait(frame: Image.Image, portrait_path: Path, width: int, height: int) -> Image.Image:
    """立绘叠加：右下角，高度 55% 画面，位于字幕条上方（PNG 透明通道保留）。"""
    try:
        img = Image.open(portrait_path)
    except OSError:
        return frame
    target_h = max(1, int(height * PORTRAIT_HEIGHT_RATIO))
    scale = target_h / max(1, img.height)
    img = img.resize((max(1, int(img.width * scale)), target_h), Image.BILINEAR)
    if img.mode != "RGBA":
        img = img.convert("RGBA")
    x = max(0, width - img.width - int(width * PORTRAIT_MARGIN_X_RATIO))
    y = max(0, height - img.height - int(height * PORTRAIT_BOTTOM_RATIO))
    frame = frame.convert("RGBA")
    frame.alpha_composite(img, (x, y))
    return frame.convert("RGB")


# --------------------------------------------------------------------------- 音频


def _load_wav_s16_stereo(path: Path, target_rate: int) -> np.ndarray:
    """读 wav → float → 线性插值重采样 → (2, n) int16 立体声。"""
    with wave.open(str(path), "rb") as wf:
        rate = wf.getframerate()
        nch = wf.getnchannels()
        sampwidth = wf.getsampwidth()
        raw = wf.readframes(wf.getnframes())
    if sampwidth == 2:
        data = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0
    elif sampwidth == 1:
        data = (np.frombuffer(raw, dtype=np.uint8).astype(np.float32) - 128.0) / 128.0
    elif sampwidth == 4:
        data = np.frombuffer(raw, dtype="<i4").astype(np.float32) / 2147483648.0
    else:
        raise ValueError(f"不支持的 wav 位深 sampwidth={sampwidth}：{path}")
    if nch > 1:
        data = data.reshape(-1, nch).mean(axis=1)
    if rate != target_rate and len(data) > 0:
        n_out = int(round(len(data) * target_rate / rate))
        data = np.interp(
            np.arange(n_out) * (rate / target_rate), np.arange(len(data)), data
        ).astype(np.float32)
    pcm = (np.clip(data, -1.0, 1.0) * 32767.0).astype("<i2")
    stereo = np.repeat(pcm[:, None], AUDIO_CHANNELS, axis=1)
    return np.ascontiguousarray(stereo.T)


def _collect_audio(
    shots: List[Dict[str, Any]], audio_dir: Optional[Path], total_samples: int
) -> np.ndarray:
    """逐镜头 wav（缺失补静音）→ 拼接并裁齐到 total_samples。"""
    pcm = np.zeros((AUDIO_CHANNELS, 0), dtype="<i2")
    for shot in shots:
        n = int(round(float(shot.get("duration", 3.0)) * AUDIO_RATE))
        wav_path = audio_dir / f"{shot.get('shot_id')}.wav" if audio_dir else None
        if wav_path and wav_path.exists():
            seg = _load_wav_s16_stereo(wav_path, AUDIO_RATE)
            if seg.shape[1] < n:  # 尾部补静音
                pad = np.zeros((AUDIO_CHANNELS, n - seg.shape[1]), dtype="<i2")
                seg = np.concatenate([seg, pad], axis=1)
            seg = seg[:, :n]
        else:
            seg = np.zeros((AUDIO_CHANNELS, n), dtype="<i2")
        pcm = np.concatenate([pcm, seg], axis=1)
    if pcm.shape[1] < total_samples:
        pad = np.zeros((AUDIO_CHANNELS, total_samples - pcm.shape[1]), dtype="<i2")
        pcm = np.concatenate([pcm, pad], axis=1)
    return pcm[:, :total_samples]


# --------------------------------------------------------------------------- BGM 混音（期 3 增强）


def resolve_bgm_path(bgm_ref: Any, store: Any = None) -> Optional[Path]:
    """bgm 引用 → 本地 wav 路径。支持：路径 str/Path、资产 id（bgm_<mood>，经 store 解析）、
    {"path"|"asset_id", "gain"?, ...} dict。解析不到返回 None（调用方保持单轨现状）。"""
    if bgm_ref is None:
        return None
    if isinstance(bgm_ref, dict):
        bgm_ref = bgm_ref.get("path") or bgm_ref.get("asset_id") or bgm_ref.get("asset")
    if bgm_ref is None:
        return None
    ref = str(bgm_ref).strip()
    if not ref:
        return None
    p = Path(ref)
    if p.is_file():
        return p
    if store is not None:
        try:
            resolved = store.resolve_path(ref)
        except Exception:
            resolved = None
        if resolved is not None and Path(resolved).is_file():
            return Path(resolved)
    return None


def _load_bgm_track(bgm_path: Path, total_samples: int) -> Optional[np.ndarray]:
    """BGM wav → (2, total_samples) float32。短于正片则循环补齐，长则截断。"""
    try:
        seg = _load_wav_s16_stereo(bgm_path, AUDIO_RATE).astype(np.float32) / 32768.0
    except (OSError, ValueError, wave.Error):
        return None
    if seg.shape[1] == 0:
        return None
    if seg.shape[1] < total_samples:  # 循环补齐
        reps = int(np.ceil(total_samples / seg.shape[1]))
        seg = np.tile(seg, (1, reps))
    return np.ascontiguousarray(seg[:, :total_samples])


def _ducking_envelope(
    shots: List[Dict[str, Any]],
    audio_dir: Optional[Path],
    total_samples: int,
    full_gain: float,
    duck_gain: float,
    fade: float,
) -> np.ndarray:
    """逐样本 BGM 增益包络：有旁白的镜头段压到 duck_gain，其余 full_gain，段边界线性过渡。"""
    seg_lens: List[int] = []
    levels: List[float] = []
    for shot in shots:
        n = max(1, int(round(float(shot.get("duration", 3.0)) * AUDIO_RATE)))
        wav_path = audio_dir / f"{shot.get('shot_id')}.wav" if audio_dir else None
        seg_lens.append(n)
        levels.append(duck_gain if (wav_path and wav_path.exists()) else full_gain)
    env = np.empty(total_samples, dtype=np.float32)
    fade_n = max(1, int(round(fade * AUDIO_RATE)))
    pos = 0
    prev = levels[0] if levels else full_gain
    for seg_n, tgt in zip(seg_lens, levels):
        if pos >= total_samples:
            break
        end = min(pos + seg_n, total_samples)
        ramp = min(fade_n, end - pos)
        if ramp > 0:
            env[pos : pos + ramp] = np.linspace(prev, tgt, ramp, endpoint=False)
        env[pos + ramp : end] = tgt
        prev = tgt
        pos = end
    if pos < total_samples:
        env[pos:] = prev
    return env


def _mix_bgm(
    narration: np.ndarray,
    bgm_pcm: Optional[np.ndarray],
    envelope: Optional[np.ndarray],
    total_samples: int,
) -> np.ndarray:
    """旁白 int16 + BGM float32×包络 → 混音 int16（clip 防削波）。无 BGM 原样返回。"""
    if bgm_pcm is None or envelope is None:
        return narration
    mixed = narration.astype(np.float32) / 32768.0 + bgm_pcm * envelope[None, :]
    return (np.clip(mixed, -1.0, 1.0) * 32767.0).astype("<i2")


# --------------------------------------------------------------------------- 转场解析（期 3 增强）


def _transition_spec(shot: Dict[str, Any]) -> Tuple[str, float]:
    """shot.transition → (类型, 时长秒)。缺省/未知 → ("cut", 0)。"""
    spec = shot.get("transition")
    if not spec:
        return "cut", 0.0
    if isinstance(spec, str):
        return ("crossfade", 0.5) if spec.strip().lower() == "crossfade" else ("cut", 0.0)
    if isinstance(spec, dict):
        ttype = str(spec.get("type", "cut")).strip().lower()
        dur = float(spec.get("duration", 0.5) or 0.0)
        return (ttype, dur) if ttype == "crossfade" and dur > 0 else ("cut", 0.0)
    return "cut", 0.0


# --------------------------------------------------------------------------- 主合成


def compose_video(
    shots_doc: Dict[str, Any],
    frames_dir: Optional[str | Path] = None,
    audio_dir: Optional[str | Path] = None,
    out_path: str | Path = "drama_composed.mp4",
    width: Optional[int] = None,
    height: Optional[int] = None,
    fps: Optional[int] = None,
    burn_subtitles: bool = True,
    bgm: Any = None,
    bgm_gain: float = DEFAULT_BGM_GAIN,
    duck_gain: float = DEFAULT_DUCK_GAIN,
    duck_fade: float = DEFAULT_DUCK_FADE,
    store: Any = None,
) -> Dict[str, Any]:
    """合成竖屏 mp4。返回 {path, width, height, fps, frames, duration, streams, bgm, transitions}
    （含回读自证）。

    shots_doc 为 projector.project_pack 的输出（也可手写等价 dict，测试用）。
    bgm：显式传入优先；None 时回退 shots_doc["bgm"]（路径 / bgm_<mood> 资产 id / dict，
    资产 id 需传 store 才能解析）。无 bgm 保持单轨现状。
    """
    if av is None:
        raise ImportError("PyAV 未安装：py -m pip install av（compose 依赖 PyAV 进程内 ffmpeg）")
    shots: List[Dict[str, Any]] = list(shots_doc.get("shots", []))
    if not shots:
        raise ValueError("shots 为空，无可合成内容")
    width = int(width or shots_doc.get("size", {}).get("width", 1080))
    height = int(height or shots_doc.get("size", {}).get("height", 1920))
    fps = int(fps or shots_doc.get("fps", 24))
    frames_path = Path(frames_dir) if frames_dir else None
    audio_path = Path(audio_dir) if audio_dir else None

    total_duration = sum(float(s.get("duration", 3.0)) for s in shots)
    total_frames = max(1, int(round(total_duration * fps)))
    total_samples = int(round(total_duration * AUDIO_RATE))

    # BGM 解析：显式参数 > shots_doc["bgm"]
    bgm_ref = bgm if bgm is not None else shots_doc.get("bgm")
    bgm_path = resolve_bgm_path(bgm_ref, store)

    # 预加载帧源与字幕条（每 shot 一次）
    sources: List[Tuple[Image.Image, Optional[Image.Image], Optional[Image.Image], Dict[str, Any], float]] = []
    for shot in shots:
        frame_file = frames_path / f"{shot.get('shot_id')}.png" if frames_path else None
        bg_file = shot.get("background_path")
        base = None
        if frame_file and frame_file.exists():
            base = _load_image_source(frame_file)
        if base is None and bg_file and Path(str(bg_file)).is_file():
            base = _load_image_source(bg_file)
        if base is None:
            base = _cover(
                _placeholder_frame(shot.get("background"), width, height), width, height
            )
        else:
            base = _cover(base, width, height)
        portrait: Optional[Image.Image] = None
        portrait_file = shot.get("portrait_path")
        if portrait_file and Path(str(portrait_file)).is_file():
            try:
                p_img = Image.open(str(portrait_file))
                target_h = max(1, int(height * PORTRAIT_HEIGHT_RATIO))
                scale = target_h / max(1, p_img.height)
                p_img = p_img.resize(
                    (max(1, int(p_img.width * scale)), target_h), Image.BILINEAR
                )
                portrait = p_img.convert("RGBA") if p_img.mode != "RGBA" else p_img
            except OSError:
                portrait = None
        strip = (
            render_subtitle_strip(str(shot.get("text", "")), width, height)
            if burn_subtitles and str(shot.get("text", "")).strip()
            else None
        )
        sources.append((base, portrait, strip, dict(shot.get("camera") or {}), float(shot.get("duration", 3.0))))

    # 转场窗口（帧数）：crossfade 溶解窗在本镜头头部；首镜头无上一帧可溶，按硬切计
    xfade_frames: Dict[int, int] = {}
    transition_count = {"cut": 0, "crossfade": 0}
    for si, shot in enumerate(shots):
        ttype, tdur = _transition_spec(shot)
        if ttype == "crossfade" and si > 0:
            local_n = max(1, int(round(float(shot.get("duration", 3.0)) * fps)))
            xfade_frames[si] = max(1, min(int(round(tdur * fps)), local_n - 1))
            transition_count["crossfade"] += 1
        else:
            transition_count["cut"] += 1

    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    container = av.open(str(out), mode="w")
    try:
        vstream = container.add_stream("libx264", rate=fps)
        vstream.width = width
        vstream.height = height
        vstream.pix_fmt = "yuv420p"
        vstream.options = {"preset": "ultrafast", "crf": "23"}
        astream = container.add_stream("aac", rate=AUDIO_RATE)
        astream.layout = "stereo"

        # 视频：逐帧 Ken Burns + 立绘叠加 + 字幕贴片（crossfade 与上一镜头末帧溶解）
        shot_of_frame: List[int] = []
        for si, (_base, _portrait, _strip, _cam, dur) in enumerate(sources):
            shot_of_frame.extend([si] * max(1, int(round(dur * fps))))
        shot_of_frame = shot_of_frame[:total_frames] + [len(sources) - 1] * max(
            0, total_frames - len(shot_of_frame)
        )
        frame_cursor: Dict[int, int] = {}
        prev_end: Optional[Image.Image] = None  # 上一镜头的末帧（crossfade 用）
        cur_shot = -1
        cur_shot_end: Optional[Image.Image] = None
        for _fi, si in enumerate(shot_of_frame):
            base, portrait, strip, cam, dur = sources[si]
            if si != cur_shot:  # 进入新镜头：接管其上一镜头末帧
                cur_shot = si
                prev_end = cur_shot_end
                cur_shot_end = None
            local_n = max(1, int(round(dur * fps)))
            li = frame_cursor.get(si, 0)
            frame_cursor[si] = li + 1
            t = li / max(1, local_n - 1) if local_n > 1 else 0.0
            frame_img = _ken_burns_frame(base, width, height, min(t, 1.0), cam)
            if portrait is not None:
                frame_img = frame_img.convert("RGBA")
                px = max(0, width - portrait.width - int(width * PORTRAIT_MARGIN_X_RATIO))
                py = max(0, height - portrait.height - int(height * PORTRAIT_BOTTOM_RATIO))
                frame_img.alpha_composite(portrait, (px, py))
                frame_img = frame_img.convert("RGB")
            xf_n = xfade_frames.get(si, 0)
            if xf_n and li < xf_n and prev_end is not None:
                alpha = (li + 0.5) / xf_n
                frame_img = Image.blend(prev_end.convert("RGB"), frame_img.convert("RGB"), alpha)
            if li == local_n - 1:  # 本镜头末帧缓存（供下一镜头 crossfade）
                cur_shot_end = frame_img
            if strip is not None:
                frame_img = frame_img.convert("RGBA")
                frame_img.alpha_composite(strip, (0, height - strip.height - int(height * 0.06)))
                frame_img = frame_img.convert("RGB")
            vframe = av.VideoFrame.from_ndarray(np.asarray(frame_img), format="rgb24")
            for packet in vstream.encode(vframe):
                container.mux(packet)

        # 音频：逐镜头 wav / 静音 → 拼接 →（可选 BGM ducking 混音）→ 编码
        pcm = _collect_audio(shots, audio_path, total_samples)
        if bgm_path is not None:
            bgm_track = _load_bgm_track(bgm_path, total_samples)
            if bgm_track is not None:
                envelope = _ducking_envelope(
                    shots, audio_path, total_samples, bgm_gain, duck_gain, duck_fade
                )
                pcm = _mix_bgm(pcm, bgm_track, envelope, total_samples)
            else:
                bgm_path = None  # BGM 文件不可解码：保持单轨，不阻断合成
        chunk = AUDIO_RATE  # 1s per encode chunk
        for i in range(0, pcm.shape[1], chunk):
            seg = np.ascontiguousarray(pcm[:, i : i + chunk].T.reshape(1, -1))  # packed s16: (1, n*ch)
            aframe = av.AudioFrame.from_ndarray(seg, format="s16", layout="stereo")
            aframe.sample_rate = AUDIO_RATE
            for packet in astream.encode(aframe):
                container.mux(packet)
        for packet in vstream.encode(None):
            container.mux(packet)
        for packet in astream.encode(None):
            container.mux(packet)
    finally:
        container.close()

    # 回读自证：流数 / 时长 / 规格
    with av.open(str(out)) as probe:
        streams = {"video": 0, "audio": 0}
        for s in probe.streams:
            if s.type == "video":
                streams["video"] += 1
            elif s.type == "audio":
                streams["audio"] += 1
        duration = probe.duration / av.time_base if probe.duration else 0.0
        v = probe.streams.video[0]
        size = (v.codec_context.width, v.codec_context.height)
    return {
        "path": str(out),
        "width": size[0],
        "height": size[1],
        "fps": fps,
        "frames": total_frames,
        "duration": round(duration, 3),
        "streams": streams,
        "bgm": str(bgm_path) if bgm_path else None,
        "transitions": transition_count,
    }
