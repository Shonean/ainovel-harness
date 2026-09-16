"""Kokoro-82M 离线 TTS 驱动（制作侧预生成配音，离线成品用）。

设计（对齐总计划 §3 台账 Kokoro 行 / §3.4 前置条件）：
- 可选 import：torch/kokoro 未安装时返回 not_installed + 安装指引，绝不抛异常；
- Windows 需 espeak-ng（msi 安装），中文走 misaki[zh]；
- 音色表占位：角色性别留白（期 4 纸人教室主角用中性音色），音色 id 与 pack characters.json
  的 `voice.kokoro` 字段对齐；
- 接口签名定好；真实推理路径写全但仅在本机依赖齐备时可达（测试环境不装，走 not_installed）。
"""
from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any, Dict, Optional

from ..asset_store import AssetStore
from .base import BaseDriver, DriveResult, DriverManifest

INSTALL_HINT = (
    "Kokoro 离线 TTS 依赖未安装。安装指引：\n"
    "  1) py -m pip install torch kokoro soundfile \"misaki[zh]\"\n"
    "  2) Windows 需安装 espeak-ng：https://github.com/espeak-ng/espeak-ng/releases\n"
    "     （msi 安装后将安装目录加入 PATH；Kokoro 的 phonemizer 依赖它）\n"
    "  3) 首次运行会自动下载 Kokoro-82M 权重（CPU 可推理，离线成品配音在制作期全部预生成）"
)

# 音色表占位：角色槽位 → Kokoro 音色。与 pack characters.json 的 voice.kokoro 对齐。
# Kokoro-82M v1.0 中文音色 id（zf_*/zm_* 前缀）；中性占位可按角色表后续统一调整。
VOICE_TABLE: Dict[str, Dict[str, str]] = {
    "narrator": {"kokoro": "zm_yunxi", "lang": "zh", "note": "旁白男声占位"},
    "protagonist": {
        "kokoro": "zf_xiaoxiao",
        "lang": "zh",
        "note": "主角中性音色占位（角色性别留白，期 4 纸人教室约定）",
    },
    "default": {"kokoro": "zf_xiaoni", "lang": "zh", "note": "兜底音色"},
}
NEUTRAL_VOICE = VOICE_TABLE["protagonist"]["kokoro"]

DEFAULT_SPEED = 1.0


def _module_installed(name: str) -> bool:
    import importlib.util

    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError):
        return False


def _espeak_available() -> tuple[bool, str]:
    """Kokoro phonemizer 依赖 espeak-ng；Windows 上 PATH 命中即可。"""
    exe = shutil.which("espeak-ng") or shutil.which("espeak")
    if exe:
        return True, exe
    return False, "未在 PATH 找到 espeak-ng（Windows 需 msi 安装并加入 PATH）"


class KokoroDriver(BaseDriver):
    """离线 TTS。synthesize(text, voice=...) → DriveResult(wav 落盘 + 入库)。"""

    manifest = DriverManifest(
        name="kokoro",
        version="0.1.0",
        capabilities=["tts_offline"],
        params={
            "text": "str, 必填，待合成文本（中文走 misaki[zh] 前端）",
            "voice": "str, Kokoro 音色 id 或 VOICE_TABLE 槽位名；默认 NEUTRAL_VOICE",
            "speed": "float, 默认 1.0",
            "out_path": "str, wav 输出路径；缺省放资产库/临时目录",
        },
        permissions={
            "write_paths": ["<asset_library>/blobs/**", "<临时目录>/kokoro_*.wav"],
            "network": ["huggingface.co（仅首次拉权重）"],
            "spends": [],
        },
        cost_items=[
            {"item": "tts_offline", "unit": "句", "source": "本地 CPU 推理", "when": "零外部花费"}
        ],
    )

    def __init__(self, store: Optional[AssetStore] = None) -> None:
        self.store = store

    # ------------------------------------------------------------------ 环境检查

    def available(self) -> tuple[bool, str]:
        missing = [m for m in ("torch", "kokoro") if not _module_installed(m)]
        if missing:
            return False, "未安装：" + ", ".join(missing)
        ok, why = _espeak_available()
        if not ok:
            return False, why
        return True, ""

    # ------------------------------------------------------------------ 执行

    def run(self, request: Dict[str, Any]) -> DriveResult:
        text = (request.get("text") or "").strip()
        if not text:
            return DriveResult.failed("request 缺少 text")
        return self.synthesize(
            text,
            voice=request.get("voice"),
            speed=float(request.get("speed", DEFAULT_SPEED)),
            out_path=request.get("out_path"),
            entry_asset_id=request.get("entry_asset_id"),
        )

    def synthesize(
        self,
        text: str,
        voice: Optional[str] = None,
        speed: float = DEFAULT_SPEED,
        lang: str = "zh",
        out_path: Optional[str] = None,
        entry_asset_id: Optional[str] = None,
    ) -> DriveResult:
        ok, why = self.available()
        if not ok:
            return DriveResult.not_installed(f"{why}\n{INSTALL_HINT}")

        voice_id = self.resolve_voice(voice)
        try:
            wav_bytes = self._synthesize_bytes(text, voice_id, speed, lang)
        except Exception as exc:
            return DriveResult.failed(f"Kokoro 推理失败：{exc}")

        if out_path:
            Path(out_path).parent.mkdir(parents=True, exist_ok=True)
            Path(out_path).write_bytes(wav_bytes)
        item: Dict[str, Any] = {
            "kind": "voice",
            "provider": "kokoro",
            "voice": voice_id,
            "bytes": len(wav_bytes),
        }
        if out_path:
            item["path"] = str(out_path)
        if self.store is not None:
            content_id = self.store.put_bytes(
                wav_bytes, "voice", meta={"text": text, "voice": voice_id, "engine": "kokoro-82m"}
            )
            item["content_id"] = content_id
            if entry_asset_id:
                self.store.mark_done(entry_asset_id, content_id)
        return DriveResult.done([item], reason=f"kokoro voice={voice_id}")

    @staticmethod
    def resolve_voice(voice: Optional[str]) -> str:
        """槽位名（narrator/protagonist/default）→ 音色 id；直接给 zf_*/zm_* 则原样用。"""
        if not voice:
            return NEUTRAL_VOICE
        if voice in VOICE_TABLE:
            return VOICE_TABLE[voice]["kokoro"]
        return voice

    def _synthesize_bytes(self, text: str, voice_id: str, speed: float, lang: str) -> bytes:
        """真实推理路径：依赖齐备时才可达。产出 24kHz 单声道 wav 字节。"""
        import io  # noqa: F401  延迟导入：仅安装环境进入
        import wave

        import numpy as np
        import torch
        from kokoro import KPipeline

        pipeline = KPipeline(lang_code="z" if lang.startswith("zh") else "a")
        chunks = []
        for _gs, _ps, audio in pipeline(text, voice=voice_id, speed=speed):
            chunks.append(torch.from_numpy(audio))
        if not chunks:
            raise RuntimeError("Kokoro 未返回任何音频块")
        audio_np = torch.cat(chunks).cpu().numpy()
        audio_np = (audio_np * 32767).astype("<i2")
        buf = io.BytesIO()
        with wave.open(buf, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(24000)
            wf.writeframes(audio_np.tobytes())
        _ = np  # numpy 仅为张量转换兜底
        return buf.getvalue()
