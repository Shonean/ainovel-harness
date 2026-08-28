"""Embedding 接口封装（使用共享 HTTP 连接池）。"""
from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Any

from .http_client import get_session_short_timeout

# 【性能优化】批间并发 + 限流：Embeddings API 单次 input 上限 10 条，
# 大 batch（如锚句 140 条 → 14 批）批间 asyncio 并发，信号量限流防 429。
# 429/5xx 退避重试（对齐 plot_similarity 的 _EMBED_RETRIES 节奏），耗尽仍失败回退逐条。
_EMBED_CONCURRENCY = 4
_EMBED_SEMAPHORE = asyncio.Semaphore(_EMBED_CONCURRENCY)
_EMBED_RETRIES = 6                 # 429/失败退避重试（2s 起，指数翻倍，cap 12s）
_EMBED_BATCH = 10                  # Embeddings API 单次 input 上限


def _apply_api_library_to_env() -> None:
    """v5.22.2：api_library.json 是唯一 API 来源（EMBED_* 部分）。

    服务器启动已由主系统 apply_current_to_env() 应用；此函数供 standalone 进程
    （os.environ 无 EMBED_API_KEY）兜底，使 prompt-harness 独立跑也走预设库
    的向量配置，而非自己的 .env。api_library 缺失/损坏时 no-op。
    """
    try:
        home = Path(os.environ.get("AINOVEL_CLAUDE_HOME", str(Path.home())))
        p = home / ".claude" / "ainovel-write" / "api_library.json"
        if not p.is_file():
            return
        import json as _json

        data = _json.loads(p.read_text(encoding="utf-8"))
        ef = (data.get("embed_config") or {}).get("fields") or {}
        for k, v in ef.items():
            if v:
                os.environ[k] = v
    except Exception:
        pass


class _RetryableHTTP(Exception):
    """限流(429)/服务端错误(5xx) → 退避重试。"""

    def __init__(self, status: int) -> None:
        super().__init__(f"HTTP {status}")


def _resolve_embed_config() -> tuple[str, str, str]:
    """调用时解析 embedding 配置（base_url, api_key, model）。

    不在模块导入期缓存 SETTINGS（导入期绑定的是 init_settings 之前的默认空配置，
    后续 init_settings 重绑 config.SETTINGS 不会影响已绑定的引用）。
    与 llm_client 的三层回退保持一致：

      1. 进程环境变量（主系统 load_user_env 已写入 os.environ）
      2. prompt-harness/.env
      3. 用户级全局 .env（~/.claude/ainovel-write/.env）
      4. 兜底：当前 config.SETTINGS（调用时才重新取，反映 init_settings 重绑后的值）
    """
    base_url = os.environ.get("EMBED_BASE_URL") or ""
    api_key = os.environ.get("EMBED_API_KEY") or ""
    model = os.environ.get("EMBED_MODEL") or ""

    def _read_env(path: Path) -> None:
        nonlocal base_url, api_key, model
        try:
            from dotenv import load_dotenv

            if path.is_file():
                load_dotenv(path, override=False)
                base_url = base_url or os.environ.get("EMBED_BASE_URL") or ""
                api_key = api_key or os.environ.get("EMBED_API_KEY") or ""
                model = model or os.environ.get("EMBED_MODEL") or ""
        except Exception:
            pass

    # 第 1.5 层（v5.22.2）：api_library.json 唯一 API 来源——standalone 无 env 时补入，
    # 优先级高于 prompt-harness/.env
    if not api_key:
        _apply_api_library_to_env()
        base_url = base_url or os.environ.get("EMBED_BASE_URL") or ""
        api_key = api_key or os.environ.get("EMBED_API_KEY") or ""
        model = model or os.environ.get("EMBED_MODEL") or ""

    if not api_key:
        _read_env(Path(__file__).resolve().parent.parent / ".env")
    if not api_key:
        _read_env(Path.home() / ".claude" / "ainovel-write" / ".env")

    # 兜底：调用时重新导入 config.SETTINGS（此时 init_settings 可能已重绑全局）
    if not api_key or not model or not base_url:
        try:
            from .config import SETTINGS as _S

            base_url = base_url or _S.embed_base_url
            api_key = api_key or _S.embed_api_key
            model = model or _S.embed_model
        except Exception:
            pass

    return base_url or "", api_key, model


def _normalize(vec):
    import numpy as np

    norm = np.linalg.norm(vec)
    return vec / norm if norm > 0 else vec


async def get_embedding(
    text: str,
    *,
    model: str | None = None,
    base_url: str | None = None,
    api_key: str | None = None,
) -> list[float] | None:
    """获取单条文本的 embedding；失败返回 None。"""
    _base_url, _api_key, _model = _resolve_embed_config()
    base_url = (base_url or _base_url).rstrip("/")
    api_key = api_key or _api_key
    model = model or _model
    if not api_key or not model:
        print("[embed] 缺少 API key 或 model")
        return None

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    payload = {"model": model, "input": text, "encoding_format": "float"}

    # 【v5.22 性能修复】连接错误（WinError 121 陈旧 keep-alive）指数退避重试，
    # 与 llm_client 的 chat_completion 对齐（并发复用共享连接池必然偶发）。
    for _attempt in range(_EMBED_RETRIES):
        try:
            session = await get_session_short_timeout()
            async with session.post(
                f"{base_url}/embeddings", headers=headers, json=payload
            ) as resp:
                data = await resp.json()
                if resp.status != 200:
                    if resp.status == 429 or resp.status >= 500:
                        raise _RetryableHTTP(resp.status)
                    print(f"[embed] HTTP {resp.status}: {data}")
                    return None
                embedding = data.get("data", [{}])[0].get("embedding")
                if embedding is None:
                    print(f"[embed] 响应中无 embedding 数据: {list(data.keys())}")
                    return None
                import numpy as np

                return _normalize(np.array(embedding, dtype=np.float32)).tolist()
        except _RetryableHTTP:
            if _attempt >= _EMBED_RETRIES - 1:
                return None
            await asyncio.sleep(min(12.0, 2.0 * (2 ** _attempt)))
        except Exception as exc:
            if _attempt >= _EMBED_RETRIES - 1:
                print(f"[embed] 请求异常: {type(exc).__name__}: {exc}")
                return None
            print(f"[embed] 请求异常({type(exc).__name__})，重试 {_attempt + 1}/{_EMBED_RETRIES}")
            await asyncio.sleep(min(4.0, 1.0 * (2 ** _attempt)))
    return None


async def get_embeddings(
    texts: list[str],
    *,
    model: str | None = None,
    base_url: str | None = None,
    api_key: str | None = None,
) -> list[list[float] | None]:
    """批量获取 embedding。尝试 API 批量请求，失败则逐条回退。"""
    _base_url, _api_key, _model = _resolve_embed_config()
    base_url = (base_url or _base_url).rstrip("/")
    api_key = api_key or _api_key
    model = model or _model
    if not api_key or not model:
        print("[embed] 批量请求：缺少 API key 或 model")
        return [None] * len(texts)

    # 尝试真正的批量 API 请求（【v5.18】分批：Embeddings API 单次 input 上限 10 条，
    # 避免 140 条锚句一次发送 → 400 → 逐条回退 140 次串行 HTTP）。
    # 【性能优化】批间 asyncio 并发（信号量限流），429/5xx 退避重试，
    # 任一批最终失败 → 整体回退逐条（行为与原串行一致）。
    if len(texts) > 1:
        try:
            headers = {
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            }
            session = await get_session_short_timeout()

            async def _post_batch(batch: list[str]) -> list[list[float] | None] | None:
                """单个 10 条批的 embedding；成功返回按 index 排序列表，最终失败返回 None。"""
                for _attempt in range(_EMBED_RETRIES):
                    try:
                        async with _EMBED_SEMAPHORE:
                            async with session.post(
                                f"{base_url}/embeddings", headers=headers,
                                json={"model": model, "input": batch, "encoding_format": "float"},
                            ) as resp:
                                if resp.status != 200:
                                    data = await resp.json()
                                    if resp.status == 429 or resp.status >= 500:
                                        raise _RetryableHTTP(resp.status)
                                    print(f"[embed] 批量 HTTP {resp.status}: {data}，回退到逐条")
                                    return None
                                data = await resp.json()
                        items = sorted(data.get("data", []), key=lambda x: x.get("index", 0))
                        out: list[list[float] | None] = []
                        for item in items:
                            emb = item.get("embedding")
                            if emb:
                                import numpy as np
                                out.append(_normalize(np.array(emb, dtype=np.float32)).tolist())
                            else:
                                out.append(None)
                        if len(out) != len(batch):
                            print("[embed] 批量返回数异常，回退到逐条")
                            return None
                        return out
                    except _RetryableHTTP:
                        if _attempt >= _EMBED_RETRIES - 1:
                            print(f"[embed] 批量 429/5xx 重试 {_EMBED_RETRIES} 次耗尽，回退到逐条")
                            return None
                        await asyncio.sleep(min(12.0, 2.0 * (2 ** _attempt)))
                    except Exception as exc:
                        if _attempt >= _EMBED_RETRIES - 1:
                            print(f"[embed] 批量请求异常: {type(exc).__name__}: {exc}，回退到逐条")
                            return None
                        print(f"[embed] 批量请求异常({type(exc).__name__})，重试 {_attempt + 1}/{_EMBED_RETRIES}")
                        await asyncio.sleep(min(4.0, 1.0 * (2 ** _attempt)))
                return None

            batches = [texts[s:s + _EMBED_BATCH] for s in range(0, len(texts), _EMBED_BATCH)]
            batch_results = await asyncio.gather(*[_post_batch(b) for b in batches])
            results: list[list[float] | None] = []
            for br in batch_results:
                if br is None:
                    print("[embed] 批结果缺失，回退到逐条")
                    return [None] * len(texts)
                results.extend(br)
            if len(results) == len(texts):
                return results
            print(f"[embed] 批量返回数 ({len(results)}) != 输入数 ({len(texts)})，回退到逐条")
        except Exception as exc:
            print(f"[embed] 批量请求异常: {type(exc).__name__}: {exc}，回退到逐条")

    # 逐条回退
    results: list[list[float] | None] = []
    for text in texts:
        results.append(
            await get_embedding(text, model=model, base_url=base_url, api_key=api_key)
        )
    return results


def cosine_similarity(a: list[float], b: list[float]) -> float:
    import numpy as np

    a_vec = _normalize(np.array(a, dtype=np.float32))
    b_vec = _normalize(np.array(b, dtype=np.float32))
    return float(np.dot(a_vec, b_vec))
