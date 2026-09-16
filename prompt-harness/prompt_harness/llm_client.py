"""OpenAI-compatible 异步 LLM 调用封装（使用共享 HTTP 连接池）。

所有 LLM 调用自动写入 logs/llm/ 目录下的 JSONL 日志，
按天滚动，便于成本分析、错误归因、性能监控。
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import sys
import time
from contextvars import ContextVar
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import aiohttp

from .config import SETTINGS
from .http_client import get_session

# 瞬时 LLM 调用错误（重试）：连接层/网络层错误 + 超时。
# WinError 121「信号灯超时时间已到」就是共享 keep-alive 连接复用到陈旧连接时
# 的 OS 级套接字错误——重试会新建连接绕过死连接。
_TRANSIENT_LLM_ERRORS = (
    aiohttp.ClientConnectionError,  # 含 ClientOSError / ClientConnectorError / ServerDisconnectedError
    aiohttp.ClientPayloadError,
    asyncio.TimeoutError,
    ConnectionError,
    OSError,
)

# ============================================================
# Phase C·成本预算：模型价格表（每 1M token 的 输入/输出 USD）+ 每日预算门禁
# ============================================================
# 前缀匹配：命中则用；未命中按 DEEPSEEK_PRO 兜底价（写网文主力 qwen 未列时按此近似）。
# 输入价含缓存命中的优惠（按未命中计，保守）。价目参考官方公开报价。
_MODEL_PRICES: dict[str, tuple[float, float]] = {
    "deepseek": (0.14, 0.28),          # deepseek-v4-flash 兜底
    "deepseek-v3.2": (0.14, 0.28),
    "deepseek-v4": (0.14, 0.28),
    "qwen3.5-flash": (0.08, 0.20),
    "qwen3.5-max": (2.0, 4.0),
    "qwen3.5-plus": (0.8, 2.0),
    "qwen3.5-turbo": (0.3, 0.6),
    "qwen3-235b-a22b": (0.6, 1.5),
    "qwen2.5": (0.4, 1.2),
    "gpt-4o": (2.5, 10.0),
    "gpt-4.1": (2.0, 8.0),
    "claude": (3.0, 15.0),
}
_DEFAULT_PRICE = (0.14, 0.28)  # 未识别模型按最便宜档近似，宁低估不阻断


def _estimate_cost_usd(model: str, usage: dict[str, Any] | None) -> float:
    """按 usage 估算本次调用花费（USD）。无 usage 返回 0（不计费失败）。"""
    if not usage:
        return 0.0
    in_p, out_p = _DEFAULT_PRICE
    for prefix, (pi, po) in _MODEL_PRICES.items():
        if prefix in (model or ""):
            in_p, out_p = pi, po
            break
    pin = float(usage.get("prompt_tokens", 0) or 0)
    pout = float(usage.get("completion_tokens", 0) or 0)
    return round((pin * in_p + pout * out_p) / 1_000_000, 6)


# 每日预算门禁：默认不设限（None）；超限时返回提示串，调用方据此熔断。
# 可用环境变量 AINOVEL_DAILY_BUDGET_USD 覆盖（如 "5.0"）。
_DAILY_BUDGET_USD: float | None = None
if os.getenv("AINOVEL_DAILY_BUDGET_USD"):
    try:
        _DAILY_BUDGET_USD = float(os.environ["AINOVEL_DAILY_BUDGET_USD"])
    except ValueError:
        _DAILY_BUDGET_USD = None

_BUDGET_LOG: dict[str, float] = {}  # date → 累计 USD（进程内近似；重启清零）


def _budget_check(today: str, cost: float) -> str | None:
    """检查并累计当日花费。超限返回错误串（调用方熔断），否则 None。"""
    if _DAILY_BUDGET_USD is None:
        return None
    spent = _BUDGET_LOG.get(today, 0.0) + cost
    _BUDGET_LOG[today] = spent
    if spent > _DAILY_BUDGET_USD:
        return (f"当日 LLM 预算超限（{spent:.2f}/{_DAILY_BUDGET_USD:.2f} USD），"
                "已熔断。请提高 AINOVEL_DAILY_BUDGET_USD 或充值后再试。")
    return None


# ============================================================
# Unified thinking-off switch
# ============================================================
# DeepSeek reasoning models (e.g. deepseek-v4-flash) emit reasoning_content by
# default, which burns tokens and slows the training loop. All training-module
# call sites should disable thinking via this constant (or rely on the
# chat_completion default guard). Applied to the request payload as
# {"thinking": {"type": "disabled"}} — verified honored by api.deepseek.com.
DISABLE_THINKING: dict[str, Any] = {"thinking": {"type": "disabled"}}


def _apply_api_library_to_env() -> None:
    """v5.22.2：api_library.json 是唯一 API 来源。

    把 api_library 当前文字预设（ARK_*）+ 向量配置（EMBED_*）写入 os.environ。
    服务器启动已由主系统 apply_current_to_env() 应用；此函数供 standalone 进程
    （os.environ 无 ARK key）兜底，使 prompt-harness 独立跑也走预设库，
    而非自己的 .env。api_library 缺失/损坏时 no-op。
    """
    try:
        home = Path(os.environ.get("AINOVEL_CLAUDE_HOME", str(Path.home())))
        p = home / ".claude" / "ainovel-write" / "api_library.json"
        if not p.is_file():
            return
        import json as _json

        data = _json.loads(p.read_text(encoding="utf-8"))
        cur = data.get("current_text_id")
        if cur:
            for pres in data.get("text_presets") or []:
                if pres.get("id") == cur:
                    for k, v in (pres.get("fields") or {}).items():
                        if v:
                            os.environ[k] = v
                    break
        ef = (data.get("embed_config") or {}).get("fields") or {}
        for k, v in ef.items():
            if v:
                os.environ[k] = v
    except Exception:
        pass

# ============================================================
# LLM 调用全局日志
# ============================================================

_llm_log_dir: Path | None = None
_llm_log_file = None
_llm_log_date: str = ""  # YYYYMMDD，用于自动换日

# 【T32 P4】运行级用量累计：批量任务设置 run_id 后，其内所有 LLM 调用
# （含子任务，ContextVar 随协程复制）累计 tokens/费用，供生产线统计。
_llm_run_id: ContextVar[str] = ContextVar("ainovel_llm_run_id", default="")
_run_usage: dict[str, dict[str, Any]] = {}


def set_llm_run_id(run_id: str | None) -> None:
    _llm_run_id.set(str(run_id or ""))


def get_run_usage(run_id: str) -> dict[str, Any]:
    d = _run_usage.get(str(run_id or "")) or {}
    return {
        "calls": int(d.get("calls") or 0),
        "prompt_tokens": int(d.get("prompt_tokens") or 0),
        "completion_tokens": int(d.get("completion_tokens") or 0),
        "total_tokens": int(d.get("total_tokens") or 0),
        "cost_usd": round(float(d.get("cost_usd") or 0.0), 4),
    }


def _accumulate_run_usage(record: dict[str, Any]) -> None:
    rid = _llm_run_id.get()
    if not rid:
        return
    d = _run_usage.setdefault(rid, {
        "calls": 0, "prompt_tokens": 0, "completion_tokens": 0,
        "total_tokens": 0, "cost_usd": 0.0,
    })
    d["calls"] += 1
    d["prompt_tokens"] += int(record.get("prompt_tokens") or 0)
    d["completion_tokens"] += int(record.get("completion_tokens") or 0)
    d["total_tokens"] += int(record.get("total_tokens") or 0)
    d["cost_usd"] += float(record.get("cost_usd") or 0.0)


def _get_llm_log_file():
    """获取今日的 LLM 日志文件（懒加载 + 自动换日）。"""
    global _llm_log_file, _llm_log_date, _llm_log_dir

    today = datetime.now(timezone.utc).strftime("%Y%m%d")
    if _llm_log_dir is None:
        base_dir = Path(__file__).resolve().parent.parent / "logs" / "llm"
        base_dir.mkdir(parents=True, exist_ok=True)
        _llm_log_dir = base_dir

    if _llm_log_date != today or _llm_log_file is None:
        if _llm_log_file is not None:
            try:
                _llm_log_file.close()
            except Exception:
                pass
        path = _llm_log_dir / f"{today}.jsonl"
        _llm_log_file = open(path, "a", encoding="utf-8")
        _llm_log_date = today

    return _llm_log_file


def _write_llm_log(record: dict[str, Any]) -> None:
    """写一条 LLM 调用日志（jsonl 双写 + llm_calls 表；表是查询聚合的正主）。"""
    _accumulate_run_usage(record)
    try:
        fh = _get_llm_log_file()
        fh.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
        fh.flush()
    except Exception:
        # 日志写入失败不能影响主流程
        pass
    # 【2026-08-22】统一数据库镜像：llm_calls 表让 llm_summary/成本统计从
    # 「Python 全文件扫描」变一条 SQL。失败静默——jsonl 仍是原始凭证。
    try:
        from .ainovel_db import get_conn, maybe_backup
        conn = get_conn()
        conn.execute(
            "INSERT INTO llm_calls(ts,model,endpoint,call_type,status,error,"
            "prompt_len,completion_len,prompt_tokens,completion_tokens,"
            "total_tokens,prompt_cache_hit_tokens,latency_ms,retry_count,cost_usd) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (record.get("ts") or "", record.get("model"),
             record.get("endpoint"), record.get("call_type"),
             record.get("status"),
             str(record.get("error") or "")[:500] or None,
             record.get("prompt_len"), record.get("completion_len"),
             record.get("prompt_tokens"), record.get("completion_tokens"),
             record.get("total_tokens"), record.get("prompt_cache_hit_tokens"),
             record.get("latency_ms"), record.get("retry_count"),
             record.get("cost_usd")))
        conn.commit()
        maybe_backup()
    except Exception:
        pass


# ============================================================
# 主函数
# ============================================================

async def chat_completion(
    *,
    system: str = "",
    user: str,
    history: list[dict] | None = None,
    model: str | None = None,
    base_url: str | None = None,
    api_key: str | None = None,
    temperature: float = 0.7,
    top_p: float = 0.7,
    presence_penalty: float = 0.0,
    frequency_penalty: float = 0.0,
    max_tokens: int = 4096,
    json_mode: bool = False,
    extra_body: dict[str, Any] | None = None,
    disable_thinking: bool = True,
    call_type: str = "",  # "generate" | "skeleton_extract" | "analyze" | "variant" 等
    stream_callback: Any = None,  # 【2026-08-23 VS Code 扩展】async fn(delta:str)；传入即走 SSE 流式
) -> dict[str, Any]:
    """返回 {"content": str, "usage": dict|None, "error": str|None}。

    所有调用自动写入全局 LLM 日志（logs/llm/{date}.jsonl）。

    history：可选多轮上下文（list[dict]，role 限 user/assistant）。传入时在
    system 与最后一条 user 之间插入这些轮次——供对话类调用（inspire_chat 等）
    把完整历史交给模型，而不是只丢最后一条消息。不传保持原单轮行为。

    stream_callback：【VS Code 扩展流式】传入 async callable(str) 时请求以
    stream=True 发出，每个增量 delta await 一次回调；最终仍返回聚合后的完整
    {"content","usage","error"}——与非流式返回形状一致，调用方逻辑不变。
    已吐过增量后发生网络错误不再重试（避免文本重复）；首 token 前的重试语义
    与非流式一致。usage 依赖上游 stream_options.include_usage 支持，
    不支持时 usage=None（成本按 0 记，不影响其它统计）。
    不传保持原非流式行为，字节级不变。

    disable_thinking=True（默认）：无条件保证请求带 thinking disabled——
    无论调用方是否传 extra_body，只要没显式开 thinking 就强制注入
    {"thinking": {"type": "disabled"}}。AI 写作调用不需要推理，思考模式
    会烧 token、拖慢延迟，且输出带"精心编排"倾向。
    需要显式开启 thinking 时传 disable_thinking=False + extra_body
    （extra_body 需自带 thinking 字段）。
    """
    # 先保存函数参数（最高优先级），避免后面赋值覆盖
    _param_api_key = api_key
    _param_model = model
    _param_base_url = base_url
    if not call_type:
        call_type = _infer_call_type()

    # ── 配置解析优先级（从低到高，后者覆盖前者）：
    #    SETTINGS 默认值 → 进程环境变量 → prompt-harness/.env → 用户级 .env → 函数参数
    #
    # 为什么不直接 "SETTINGS + 空值兜底"？
    #   因为 SETTINGS.base_url 有默认值 https://ark.cn-beijing.volces.com/api/coding/v3，
    #   如果用 "有值就不读环境变量" 的逻辑，环境变量里真实的 base_url 永远不会生效。
    #   正确做法：从最低优先级开始，每个更高优先级有值就覆盖。

    api_key = SETTINGS.ark_api_key
    model = SETTINGS.ark_model_pro
    base_url = SETTINGS.ark_base_url

    def _apply_from_env():
        nonlocal api_key, model, base_url
        if os.getenv("ARK_API_KEY"):
            api_key = os.environ["ARK_API_KEY"]
        if os.getenv("ARK_MODEL_PRO"):
            model = os.environ["ARK_MODEL_PRO"]
        if os.getenv("ARK_BASE_URL"):
            base_url = os.environ["ARK_BASE_URL"]

    # 第 1 层：进程环境变量（主系统 init_settings 后已写入 os.environ）
    _apply_from_env()

    # 第 1.5 层（v5.22.2）：api_library.json 唯一 API 来源——standalone 进程
    # os.environ 无 ARK key 时从预设库补入（优先级高于 prompt-harness/.env）
    if not api_key:
        _apply_api_library_to_env()
        _apply_from_env()

    # 第 2 层：如果还没 key，加载 prompt-harness/.env 再读
    if not api_key:
        try:
            from dotenv import load_dotenv
            _env_path = Path(__file__).resolve().parent.parent / ".env"
            if _env_path.is_file():
                load_dotenv(_env_path, override=False)
                _apply_from_env()
        except Exception:
            pass

    # 第 3 层：如果还没 key，加载用户级全局 .env 再读
    if not api_key:
        try:
            from dotenv import load_dotenv
            _user_env = Path.home() / ".claude" / "ainovel-write" / ".env"
            if _user_env.is_file():
                load_dotenv(_user_env, override=False)
                _apply_from_env()
        except Exception:
            pass

    # 最高层：函数显式传入的参数（有值才覆盖）
    if _param_api_key:
        api_key = _param_api_key
    # 【T32 P4】量产批量：运行级模型覆盖（低于显式参数，高于 env/SETTINGS）
    try:
        from .runtime_flags import get_model as _get_override_model
        _ov = _get_override_model()
        if _ov:
            model = _ov
    except Exception:  # noqa: BLE001
        pass
    if _param_model:
        model = _param_model
    if _param_base_url:
        base_url = _param_base_url

    base_url = (base_url or "").rstrip("/")

    prompt_len = len(system) + len(user) + sum(len(str(m.get("content") or "")) for m in (history or []))
    t0 = time.time()
    timestamp = datetime.now(timezone.utc).isoformat()
    retry_count = 0  # 瞬时错误重试次数（连接层/网络层/429/5xx），记入 LLM 日志

    if not api_key:
        result = {"content": "", "usage": None, "error": "ARK_API_KEY not configured"}
        _log_llm_call(timestamp, model, base_url or "", prompt_len, 0,
                      round((time.time() - t0) * 1000), retry_count, call_type,
                      status="error", error=result["error"])
        return result
    if not model:
        result = {"content": "", "usage": None, "error": "ARK_MODEL_PRO not configured"}
        _log_llm_call(timestamp, model, base_url or "", prompt_len, 0,
                      round((time.time() - t0) * 1000), retry_count, call_type,
                      status="error", error=result["error"])
        return result

    # 【Phase C·成本预算】请求前熔断：当日已超预算则直接拒绝（不烧 token）。
    # 设了预算才检查；未设预算（默认）零开销。
    if _DAILY_BUDGET_USD is not None:
        _today_b = str(datetime.now(timezone.utc).date())
        if _BUDGET_LOG.get(_today_b, 0.0) > _DAILY_BUDGET_USD:
            result = {"content": "", "usage": None,
                      "error": (f"当日 LLM 预算超限（{_BUDGET_LOG.get(_today_b, 0.0):.2f}/"
                                f"{_DAILY_BUDGET_USD:.2f} USD），已熔断。请提高 "
                                "AINOVEL_DAILY_BUDGET_USD 或充值后再试。")}
            _log_llm_call(timestamp, model, base_url or "", prompt_len, 0,
                          round((time.time() - t0) * 1000), retry_count, call_type,
                          status="error", error=result["error"])
            return result

    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    if history:
        for _m in history:
            _r = _m.get("role") if _m.get("role") in ("user", "assistant") else "user"
            messages.append({"role": _r, "content": str(_m.get("content") or "")})
    messages.append({"role": "user", "content": user})

    # json_mode 防御：DeepSeek 要求 prompt 里必须出现单词 "json"（否则 400），
    # 且不支持 json_schema。若调用方 prompt 没带，自动在 system 头部补一句，
    # 防止未来直连 chat_completion(json_mode=True) 时踩兼容性坑。
    # 【前缀缓存稳定性】只看 system 不看 user：user 是待审阅的生成文本，
    # 偶发包含 "json" 字样会让首条 system 消息时有时无，破坏前缀字节稳定。
    if json_mode and not re.search(r"json", system, re.IGNORECASE):
        messages.insert(0, {
            "role": "system",
            "content": "Return only valid JSON. Do not include Markdown, prose, comments, or code fences.",
        })

    _streaming = stream_callback is not None
    payload: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "top_p": top_p,
        "presence_penalty": presence_penalty,
        "frequency_penalty": frequency_penalty,
        "max_tokens": max_tokens,
        "stream": _streaming,
    }
    if _streaming:
        # 请求末尾 chunk 带 usage；不支持该字段的端点一般忽略而非报错
        payload["stream_options"] = {"include_usage": True}
    if json_mode:
        payload["response_format"] = {"type": "json_object"}
    if extra_body:
        # 与 openai 库语义保持一致：extra_body 的字段合并到请求体顶层
        payload.update(extra_body)
    # 兜底关闭 thinking：无论调用方是否传 extra_body，只要 disable_thinking=True
    #（默认）且请求里还没有 thinking 字段，就强制注入 disabled。
    # v5.11 之前是 elif 分支——调用方一旦传了不含 thinking 的 extra_body，
    # thinking 就会漏开，推理模型直接烧 token / 拖慢训练延迟。
    if disable_thinking and "thinking" not in payload and "enable_thinking" not in payload:
        # 【按端点/模型区分 thinking 关闭策略】
        # - 阿里云百炼（dashscope / aliyuncs.com）：qwen 用 enable_thinking=False
        # - DeepSeek（deepseek.com）：{"thinking":{"type":"disabled"}}
        #   必须 update() 而非 payload["thinking"]=，原因见 DISABLE_THINKING 注释
        # - GLM-5.3+（模型名含 glm）：不能禁用 thinking，只能降强度
        #   → thinking.type: "enabled" + reasoning_effort: "low"
        #   （glm-5.3 传 disabled 报 400；low 档最轻思考，最接近"不深度思考"
        #   默认 clear_thinking=true，返回只给 content 不含思考文本）
        # - GLM-5.3+（模型名含 glm）：不能禁用 thinking，只能降强度 reasoning_effort: low
        # - 豆包推理模型（doubao-seed-evolving / 含 evolving/thinking）：默认开思考，
        #   必须显式 thinking.type=disabled 才关，否则正文生成叠思考 token 极慢
        # - OpenRouter（openrouter.ai）：统一 reasoning 参数。stealth 端点强制
        #   开推理（disabled 报 400 "Reasoning is mandatory"）→ 只能压到最小挡
        #   effort=minimal；若某模型连 minimal 都不认，再手退 low
        # - 其他（doubao 非推理 / minimax / kimi / mimo 等）：默认无 thinking，不发字段
        model_lower = (model or "").lower()
        if any(k in (base_url or "") for k in ("aliyuncs.com", "dashscope")):
            payload["enable_thinking"] = False
        elif "deepseek.com" in (base_url or ""):
            payload.update(DISABLE_THINKING)
        elif "openrouter.ai" in (base_url or ""):
            payload["reasoning"] = {"effort": "minimal"}
        elif "glm" in model_lower:
            payload["thinking"] = {"type": "enabled"}
            payload["reasoning_effort"] = "low"
        elif "evolving" in model_lower or "thinking" in model_lower:
            # 豆包/火山方舟推理模型：显式关思考
            payload["thinking"] = {"type": "disabled"}
        # else: 其他模型默认无 thinking 模式，不发字段

    # GLM-5.3+ 思考模型：max_tokens 含思考+正文，需放大预留思考空间
    # reasoning_effort=low 时思考约占 1500-3000 tok，放大 2.5 倍确保正文有空间
    if "glm" in (model or "").lower():
        orig_max = payload.get("max_tokens", 4096)
        payload["max_tokens"] = min(int(orig_max * 2.5), 32768)

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    status = "success"
    error_msg = ""
    usage = None
    completion_len = 0

    # 瞬时错误重试：初始 1 次 + 最多 2 次重试，指数退避（1s/2s）。
    # 触发条件：连接层/网络层错误（含 WinError 121 陈旧 keep-alive 连接）、
    # 429 限流、5xx 服务端错误。4xx 请求错误与其它异常不重试、快速失败。
    max_retries = 2
    result: dict[str, Any] | None = None
    for attempt in range(max_retries + 1):
        emitted_any = False  # 本轮是否已向回调吐过增量（吐过则不再重试防重复）
        try:
            session = await get_session()
            async with session.post(
                f"{base_url}/chat/completions", headers=headers, json=payload
            ) as resp:
                if _streaming and resp.status == 200:
                    # ── 流式分支：SSE 逐行解析，增量回调，聚合返回形状不变 ──
                    parts: list[str] = []
                    usage = None
                    try:
                        async for raw_line in resp.content:
                            line = raw_line.decode("utf-8", "ignore").strip()
                            if not line.startswith("data:"):
                                continue
                            data_str = line[5:].strip()
                            if data_str == "[DONE]":
                                break
                            try:
                                chunk = json.loads(data_str)
                            except json.JSONDecodeError:
                                continue
                            if isinstance(chunk.get("usage"), dict):
                                usage = _normalize_usage_cache(chunk["usage"])
                            choices = chunk.get("choices") or [{}]
                            delta = ((choices[0].get("delta") or {}) if isinstance(choices[0], dict) else {}).get("content") or ""
                            if delta:
                                emitted_any = True
                                parts.append(delta)
                                await stream_callback(delta)
                    except _TRANSIENT_LLM_ERRORS as exc:
                        if emitted_any:
                            # 已吐过增量：不重试（会重复文本），返回部分内容+错误标记
                            status = "error"
                            error_msg = f"流式中断：{type(exc).__name__}: {exc}"
                            result = {"content": "".join(parts), "usage": usage, "error": error_msg}
                            break
                        raise  # 首 token 前失败 → 交外层按瞬时错误重试
                    completion_len = len("".join(parts))
                    result = {"content": "".join(parts), "usage": usage, "error": None}
                    break
                data = await resp.json()
                if resp.status != 200:
                    if (resp.status == 429 or resp.status >= 500) and attempt < max_retries:
                        retry_count += 1
                        await asyncio.sleep(1.0 * (2 ** attempt))
                        continue
                    status = "error"
                    error_msg = f"HTTP {resp.status}: {data}"
                    result = {
                        "content": "",
                        "usage": None,
                        "error": error_msg,
                    }
                else:
                    choice = data.get("choices", [{}])[0]
                    message = choice.get("message", {}) or {}
                    content = message.get("content") or ""
                    completion_len = len(content)
                    usage = _normalize_usage_cache(data.get("usage"))
                    result = {
                        "content": content,
                        "usage": usage,
                        "error": None,
                    }
                break
        except _TRANSIENT_LLM_ERRORS as exc:
            if attempt < max_retries:
                retry_count += 1
                await asyncio.sleep(1.0 * (2 ** attempt))
                continue
            status = "error"
            error_msg = f"{type(exc).__name__}: {exc}"
            result = {"content": "", "usage": None, "error": error_msg}
        except Exception as exc:  # noqa: BLE001
            status = "error"
            error_msg = f"{type(exc).__name__}: {exc}"
            result = {"content": "", "usage": None, "error": error_msg}
            break

    latency_ms = round((time.time() - t0) * 1000)
    # 【Phase C·成本预算】按 usage 估算本次花费并累计当日（设了预算才累计）
    if _DAILY_BUDGET_USD is not None:
        try:
            _budget_check(str(datetime.now(timezone.utc).date()),
                          _estimate_cost_usd(model, usage))
        except Exception:  # noqa: BLE001 —— 预算累计失败绝不影响调用
            pass
    _log_llm_call(
        timestamp, model, base_url or "", prompt_len, completion_len,
        latency_ms, retry_count, call_type, status=status, error=error_msg,
        usage=usage, temperature=temperature, top_p=top_p,
        max_tokens=max_tokens,
    )
    # 工作台完整日志：全量 LLM 输入输出（服务工作台微调设计）。失败静默不影响主流程。
    try:
        from .workbench_logger import log_trace
        _u = usage or {}
        _content = (result or {}).get("content", "") if isinstance(result, dict) else ""
        log_trace(
            model=model,
            endpoint=base_url or "",
            call_type=call_type,
            system=system,
            user=user,
            history=history,
            output=_content,
            prompt_tokens=_u.get("prompt_tokens", 0) or 0,
            completion_tokens=_u.get("completion_tokens", 0) or 0,
            total_tokens=_u.get("total_tokens", 0) or 0,
            latency_ms=latency_ms,
            retry_count=retry_count,
            status=status,
            error=error_msg,
        )
    except Exception:
        pass
    return result


def _normalize_usage_cache(usage: Any) -> Any:
    """统一各 provider 的前缀缓存命中字段到 prompt_cache_hit_tokens/miss_tokens。

    各家字段名不同，DeepSeek 直连才回 prompt_cache_hit_tokens：
    - Volcengine ark: usage.cached_tokens
    - OpenAI/dashscope 兼容风格: usage.prompt_tokens_details.cached_tokens
    命中字段存在但 miss 缺失时补算 miss = prompt_tokens - hit。
    非成功调用（usage=None）原样返回。
    """
    if not isinstance(usage, dict):
        return usage
    hit = usage.get("prompt_cache_hit_tokens")
    if not hit:
        hit = usage.get("cached_tokens")
    if not hit:
        ptd = usage.get("prompt_tokens_details")
        hit = ptd.get("cached_tokens") if isinstance(ptd, dict) else None
    if hit:
        try:
            hit = int(hit)
        except (TypeError, ValueError):
            return usage
        usage["prompt_cache_hit_tokens"] = hit
        if usage.get("prompt_cache_miss_tokens") is None:
            pt = usage.get("prompt_tokens") or usage.get("input_tokens") or 0
            try:
                usage["prompt_cache_miss_tokens"] = max(0, int(pt) - hit)
            except (TypeError, ValueError):
                pass
    return usage


def _infer_call_type() -> str:
    """call_type 为空时，从调用栈推断来源函数名（跳过本文件内的包装层）。

    大量调用不传 call_type，日志里 6700+ 次调用/3900 万 token 无法归因。
    返回 "auto:<函数名>"，与显式传入的类型区分。
    """
    try:
        _this = os.path.normcase(os.path.abspath(__file__))
        frame = sys._getframe(2)
        while frame is not None and os.path.normcase(
            os.path.abspath(frame.f_code.co_filename)
        ) == _this:
            frame = frame.f_back
        if frame is not None:
            return "auto:" + frame.f_code.co_name
    except Exception:  # noqa: BLE001 -- 推断失败不影响主流程
        pass
    return "unknown"


def _log_llm_call(
    timestamp: str,
    model: str,
    endpoint: str,
    prompt_len: int,
    completion_len: int,
    latency_ms: int,
    retry_count: int,
    call_type: str,
    status: str,
    error: str = "",
    usage: dict[str, Any] | None = None,
    temperature: float = 0.0,
    top_p: float = 0.0,
    max_tokens: int = 0,
) -> None:
    """封装 LLM 调用日志记录。"""
    record = {
        "ts": timestamp,
        "model": model,
        "endpoint": endpoint,
        "call_type": call_type,
        "prompt_len": prompt_len,
        "completion_len": completion_len,
        "prompt_tokens": usage.get("prompt_tokens", 0) if usage else 0,
        "completion_tokens": usage.get("completion_tokens", 0) if usage else 0,
        "total_tokens": usage.get("total_tokens", 0) if usage else 0,
        # DeepSeek 自动磁盘前缀缓存的命中/未命中 token 数，用于观测缓存省钱效果
        "prompt_cache_hit_tokens": usage.get("prompt_cache_hit_tokens", 0) if usage else 0,
        "prompt_cache_miss_tokens": usage.get("prompt_cache_miss_tokens", 0) if usage else 0,
        "latency_ms": latency_ms,
        "retry_count": retry_count,
        "status": status,
        "error": error[:200] if error else "",
        "temperature": temperature,
        "top_p": top_p,
        "max_tokens": max_tokens,
        # Phase C·成本：按 usage+价格表估算本次花费 USD（便于日志聚合看烧钱）
        "cost_usd": _estimate_cost_usd(model, usage),
    }
    _write_llm_log(record)


async def chat_json(
    *,
    system: str = "",
    user: str,
    call_type: str = "",
    **kwargs: Any,
) -> dict[str, Any]:
    """要求模型返回 JSON 并解析。返回 {"data": dict|None, "raw": str, "usage": ..., "error": ...}。"""
    system = (system or "") + "\n你必须只返回合法的 JSON，不要输出 Markdown 代码块或其他说明。"
    response = await chat_completion(
        system=system, user=user, json_mode=True,
        call_type=call_type or "json",
        **kwargs
    )
    if response["error"]:
        return {"data": None, "raw": response["content"], "usage": response["usage"], "error": response["error"]}
    raw = response["content"].strip()
    if raw.startswith("```"):
        # 去除可能的 markdown 代码块
        lines = raw.splitlines()
        if lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        raw = "\n".join(lines).strip()
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        return {"data": None, "raw": raw, "usage": response["usage"], "error": f"JSON decode failed: {exc}"}
    return {"data": data, "raw": raw, "usage": response["usage"], "error": None}


async def chat_completion_stream(
    *,
    system: str = "",
    user: str,
    on_delta: Any,
    call_type: str = "",
    **kwargs: Any,
) -> dict[str, Any]:
    """流式版 chat_completion：每个增量 delta await 一次 ``on_delta(delta_str)``。

    返回与非流式完全同形的聚合结果；供创作助手边生成边推送（VS Code 扩展）。
    """
    return await chat_completion(
        system=system, user=user, call_type=call_type or "stream",
        stream_callback=on_delta, **kwargs,
    )
