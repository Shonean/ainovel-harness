"""云 TTS（火山语音）接口层 stub。

定位（总计划 §3 台账「云 TTS」行 / §3.4）：
- 在线模式与成品质量升级用；与 Kokoro 同一音色表映射（voice_type 由上层按角色解析）；
- 本文件只定请求形状 + 流式回调约定；无 key → skipped；
- 即使配置了 key，真实连接也未接入（not_implemented），确保不会在 stub 阶段意外外呼。

请求形状对齐火山引擎「语音合成大模型」WebSocket 二进制流式协议（ssup）：
连接网关 wss://openspeech.bytedance.com/api/v3/tts/unidirectional，
首帧发送本文件 build_request() 生成的 JSON（full client request），
后续帧为带序号的二进制音频分片，结尾为 type=frontmatter 的结束帧。
"""
from __future__ import annotations

import os
import uuid
from typing import Any, Callable, Dict, List, Optional

from ..asset_store import AssetStore
from .base import BaseDriver, DriveResult, DriverManifest

TTS_GATEWAY = "wss://openspeech.bytedance.com/api/v3/tts/unidirectional"

# 流式回调事件类型（on_event 收到的 event["type"] 取值）
STREAM_EVENT_TYPES = ("start", "audio", "end", "error")

ENV_APPID = "VOLC_TTS_APPID"
ENV_TOKEN = "VOLC_TTS_ACCESS_TOKEN"
ENV_CLUSTER = "VOLC_TTS_CLUSTER"

DEFAULT_CLUSTER = "volcano_tts"
DEFAULT_ENCODING = "mp3"
DEFAULT_SAMPLE_RATE = 24000


def build_request(
    text: str,
    voice_type: str,
    appid: str,
    token: str,
    cluster: str = DEFAULT_CLUSTER,
    encoding: str = DEFAULT_ENCODING,
    sample_rate: int = DEFAULT_SAMPLE_RATE,
    speed_ratio: float = 1.0,
    reqid: Optional[str] = None,
) -> Dict[str, Any]:
    """构造火山语音 TTS 大模型 API 的请求体（协议首帧 JSON）。

    形状（固定，供上层与测试对齐）：
    {
      "app":    {"appid": ..., "token": ..., "cluster": ...},
      "user":   {"uid": "ainovel-harness"},
      "audio":  {"voice_type": ..., "encoding": ..., "sample_rate": ..., "speed_ratio": ...},
      "request": {"reqid": <uuid>, "text": ..., "text_type": "plain", "operation": "ssup"}
    }
    operation="ssup" 表示流式二进制协议（单向推送，服务端持续回音频分片）。
    """
    if not text or not text.strip():
        raise ValueError("text 不能为空")
    if not voice_type:
        raise ValueError("voice_type（音色 id）不能为空")
    return {
        "app": {"appid": appid, "token": token, "cluster": cluster},
        "user": {"uid": "ainovel-harness"},
        "audio": {
            "voice_type": voice_type,
            "encoding": encoding,
            "sample_rate": sample_rate,
            "speed_ratio": speed_ratio,
        },
        "request": {
            "reqid": reqid or str(uuid.uuid4()),
            "text": text,
            "text_type": "plain",
            "operation": "ssup",
        },
    }


class CloudTTSDriver(BaseDriver):
    """云 TTS stub：请求/回调约定 + 凭据降级。绝不真实外呼（见 synthesize_stream 内说明）。"""

    manifest = DriverManifest(
        name="cloud_tts",
        version="0.1.0-stub",
        capabilities=["tts_online", "tts_streaming"],
        params={
            "text": "str, 必填",
            "voice_type": "str, 火山音色 id（上层按角色从统一音色表解析）",
            "encoding": "str, mp3/pcm/wav，默认 mp3",
            "sample_rate": "int, 默认 24000",
            "speed_ratio": "float, 默认 1.0",
        },
        permissions={
            "write_paths": ["<asset_library>/blobs/**"],
            "network": [TTS_GATEWAY],
            "spends": ["volcengine 火山语音：按字符计费（商用前需确认音色授权）"],
        },
        cost_items=[
            {
                "item": "tts_cloud",
                "unit": "万字符",
                "source": "volcengine 火山语音",
                "when": "仅真实调用成功时计费（stub 阶段不产生）",
            }
        ],
    )

    def __init__(
        self,
        store: Optional[AssetStore] = None,
        appid: Optional[str] = None,
        token: Optional[str] = None,
        cluster: Optional[str] = None,
    ) -> None:
        self.store = store
        self.appid = (appid or os.environ.get(ENV_APPID, "")).strip()
        self.token = (token or os.environ.get(ENV_TOKEN, "")).strip()
        self.cluster = (cluster or os.environ.get(ENV_CLUSTER, "") or DEFAULT_CLUSTER).strip()

    # ------------------------------------------------------------------ 环境检查

    def available(self) -> tuple[bool, str]:
        if self.appid and self.token:
            return True, ""
        return False, (
            "云 TTS 未配置凭据：请设置环境变量 "
            f"{ENV_APPID} / {ENV_TOKEN}（或构造时传入 appid/token）。"
            "需先在火山引擎开通语音服务并确认音色商用授权。"
        )

    # ------------------------------------------------------------------ 执行

    def run(self, request: Dict[str, Any]) -> DriveResult:
        """统一入口。request 形状见 manifest.params；回调经 on_audio/on_event 传入。"""
        return self.synthesize_stream(
            text=request.get("text", ""),
            voice_type=request.get("voice_type", ""),
            on_audio=request.get("on_audio"),
            on_event=request.get("on_event"),
            encoding=request.get("encoding", DEFAULT_ENCODING),
            sample_rate=request.get("sample_rate", DEFAULT_SAMPLE_RATE),
            speed_ratio=request.get("speed_ratio", 1.0),
        )

    def synthesize_stream(
        self,
        text: str,
        voice_type: str,
        on_audio: Optional[Callable[[bytes], None]] = None,
        on_event: Optional[Callable[[Dict[str, Any]], None]] = None,
        encoding: str = DEFAULT_ENCODING,
        sample_rate: int = DEFAULT_SAMPLE_RATE,
        speed_ratio: float = 1.0,
    ) -> DriveResult:
        """流式合成。回调约定：
        - on_audio(chunk: bytes)：每收到一段音频分片调用一次，按序交付；
        - on_event(event: dict)：事件通知，event["type"] ∈ STREAM_EVENT_TYPES，
          start（会话建立）/ audio（分片送达，与 on_audio 同步触发）/ end（正常收尾）/ error（失败）。
        """
        ok, why = self.available()
        if not ok:
            if on_event is not None:
                on_event({"type": "error", "reason": why})
            return DriveResult.skipped(why)

        # 有凭据也不在此 stub 阶段真实外呼：真实连接留待期 3 后续阶段接入
        # （计划：websockets 连 TTS_GATEWAY → 首帧 build_request(...) → 循环收分片回调）。
        _ = build_request(
            text, voice_type, self.appid, self.token, self.cluster, encoding, sample_rate, speed_ratio
        )
        if on_event is not None:
            on_event({"type": "error", "reason": "云 TTS 真实连接未接入"})
        return DriveResult.not_implemented(
            "云 TTS 真实连接未接入（期 3 后续阶段）：请求形状与流式回调约定已定，"
            "见 build_request / STREAM_EVENT_TYPES；接入点在 _connect_stream()。"
        )

    def synthesize(
        self,
        text: str,
        voice_type: str,
        **kwargs: Any,
    ) -> DriveResult:
        """非流式包装：收集流式分片为整段音频字节。"""
        chunks: List[bytes] = []
        result = self.synthesize_stream(text, voice_type, on_audio=chunks.append, **kwargs)
        if result.ok:
            audio = b"".join(chunks)
            result.outputs.append({"kind": "voice", "provider": "cloud_tts", "bytes": len(audio)})
            if self.store is not None and audio:
                content_id = self.store.put_bytes(
                    audio, "voice", meta={"text": text, "voice_type": voice_type, "engine": "volcengine_tts"}
                )
                result.outputs[-1]["content_id"] = content_id
        return result

    def _connect_stream(self, request: Dict[str, Any]) -> None:
        """真实连接接入点（期 3 后续实现，见 synthesize_stream 内注释）。"""
        raise NotImplementedError
