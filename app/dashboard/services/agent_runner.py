"""
agent_runner.py — Phase A2：用 Anthropic SDK 复刻 Claude Code subagent 调用环境。

核心设计
----------
一个 ``AnthropicAgentRunner`` 实例封装一轮多轮 tool-use 循环：
1. 调用 ``client.messages.stream`` 流式接收 token 和 tool_use 块。
2. 收到 tool_use 块后，本地执行 Read/Grep/Bash 中的一种（白名单沙箱），
   把 ``tool_result`` 追加到 messages 列表，继续下一轮，直到 LLM 停止。
3. 每收到一个 text delta 或 tool_use 事件，都通过 ``on_event`` 回调推给前端 SSE。

工具白名单（复用 path_guard.py 的 ``safe_resolve`` 逻辑）：
- Read(file_path)：允许 project_root 下 + plugins/scripts/references 路径。
- Grep(pattern, glob)：同上路径限定。
- Bash(command)：仅允许 ``python -X utf8 -m data_modules.*`` 和
  ``python -X utf8 ainovel.py <subcmd>`` 两类命令。
"""

from __future__ import annotations

import asyncio
import json
import os
import random
import re
import subprocess
import sys
import time
import traceback
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional

from openai import (
    APIConnectionError,
    APITimeoutError,
    AsyncOpenAI,
    InternalServerError,
    RateLimitError,
)

from ..core.path_guard import safe_resolve
from .usage import USAGE

# ---------------------------------------------------------------------------
# 全局 LLM 并发信号量
# ---------------------------------------------------------------------------
# ARK 账号级 RPM 限制下，无并发上限会让 auto-generate 一波打出 60-80 个 chat
# 请求，立刻撞 429。信号量把所有 AnthropicAgentRunner 流式调用排队，
# 默认 4 并发（环境变量 LLM_MAX_CONCURRENCY 可调）。
_LLM_SEMAPHORE: Optional[asyncio.Semaphore] = None


def _get_llm_semaphore() -> asyncio.Semaphore:
    """懒初始化全局 LLM 信号量（必须在 event loop 启动后调）。"""
    global _LLM_SEMAPHORE
    if _LLM_SEMAPHORE is None:
        try:
            limit = int(os.environ.get("LLM_MAX_CONCURRENCY", "4"))
        except (TypeError, ValueError):
            limit = 4
        limit = max(1, limit)
        _LLM_SEMAPHORE = asyncio.Semaphore(limit)
    return _LLM_SEMAPHORE


def _get_retry_config() -> tuple[int, float, float]:
    """读 LLM 重试参数：(max_retries, base_delay, max_delay)。"""
    try:
        max_retries = int(os.environ.get("LLM_MAX_RETRIES", "8"))
    except (TypeError, ValueError):
        max_retries = 8
    try:
        base_delay = float(os.environ.get("LLM_RETRY_BASE_DELAY", "3.0"))
    except (TypeError, ValueError):
        base_delay = 3.0
    try:
        max_delay = float(os.environ.get("LLM_RETRY_MAX_DELAY", "60.0"))
    except (TypeError, ValueError):
        max_delay = 60.0
    return max(1, max_retries), max(0.1, base_delay), max(base_delay, max_delay)

# ---------------------------------------------------------------------------
# 工具定义（发给 Anthropic API 的 JSON schema）
# ---------------------------------------------------------------------------

READ_TOOL = {
    "name": "Read",
    "description": "读取文件内容。仅允许 PROJECT_ROOT 下和插件目录下的文件。",
    "input_schema": {
        "type": "object",
        "properties": {
            "file_path": {
                "type": "string",
                "description": "要读取的文件路径，相对于 PROJECT_ROOT 或绝对于插件目录",
            },
        },
        "required": ["file_path"],
    },
}

GREP_TOOL = {
    "name": "Grep",
    "description": "在文件内容中搜索正则表达式。搜索范围限定在 PROJECT_ROOT 下。",
    "input_schema": {
        "type": "object",
        "properties": {
            "pattern": {
                "type": "string",
                "description": "正则表达式",
            },
            "glob": {
                "type": "string",
                "description": "可选的文件 glob 过滤（如 '*.md'）",
            },
        },
        "required": ["pattern"],
    },
}

BASH_TOOL = {
    "name": "Bash",
    "description": (
        "执行被允许的 shell 命令（单条，不要用管道/&&/sed/for 循环等 bash 语法）。"
        "仅支持以下形式，全部以 `python -X utf8` 开头："
        "1) python -X utf8 -m data_modules.<name> ...   "
        "2) python -X utf8 ainovel.py <subcmd> ...   "
        "3) python -X utf8 reference_search.py ...   "
        "4) python -X utf8 -c \"<inline code>\"   "
        "文件读写改用 Read/Write/Edit 工具，不要用 cat/cp/echo。"
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "command": {
                "type": "string",
                "description": "要执行的命令（单条 python 调用）",
            },
        },
        "required": ["command"],
    },
}

WRITE_TOOL = {
    "name": "Write",
    "description": "写入文件（覆盖）。仅允许 PROJECT_ROOT 下的路径。目录会自动创建。",
    "input_schema": {
        "type": "object",
        "properties": {
            "file_path": {"type": "string", "description": "PROJECT_ROOT 下的相对或绝对路径"},
            "content": {"type": "string", "description": "文件全文内容"},
        },
        "required": ["file_path", "content"],
    },
}

EDIT_TOOL = {
    "name": "Edit",
    "description": "精确替换文件中的一段文本（old_string 必须唯一匹配）。仅允许 PROJECT_ROOT 下的路径。",
    "input_schema": {
        "type": "object",
        "properties": {
            "file_path": {"type": "string"},
            "old_string": {"type": "string", "description": "要被替换的原文（必须唯一）"},
            "new_string": {"type": "string", "description": "替换为的新文本"},
        },
        "required": ["file_path", "old_string", "new_string"],
    },
}

ASKUSER_TOOL = {
    "name": "AskUser",
    "description": (
        "向用户提问并阻塞等待回答（替代 AskUserQuestion）。"
        "用于关键分歧裁决、候选方案选择、最终确认。"
        "提供 options 时前端渲染成按钮；否则渲染成文本框。"
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "question": {"type": "string", "description": "要问的问题"},
            "options": {
                "type": "array",
                "items": {"type": "string"},
                "description": "可选：候选选项列表",
            },
            "placeholder": {"type": "string", "description": "可选：文本框占位提示"},
        },
        "required": ["question"],
    },
}

ALL_TOOLS = [READ_TOOL, GREP_TOOL, BASH_TOOL, WRITE_TOOL, EDIT_TOOL, ASKUSER_TOOL]

# ---------------------------------------------------------------------------
# 沙箱校验
# ---------------------------------------------------------------------------

# Bash 白名单：允许以下命令前缀（只读安全操作）——
#   python -X utf8 ...  →  data_modules / ainovel.py / reference_search.py / -c
#   python ...           →  同上但无 -X utf8（plan 旧命令兼容）
#   cat ...              →  只读文件查看
#   export VAR=...       →  环境变量设置（仅影响 subprocess shell）
# 所有模式 re.match 只锚定开头，允许后续任意参数。
_BASH_WHITELIST = re.compile(
    r"^(?:"
    # python -X utf8 <script/module>
    r"python\s+-X\s+utf8\s+("
    r"-m\s+data_modules\.\w+"
    r"|(?:[^\s;&|`()]*/)?ainovel\.py(?:\s|\"|$)"
    r"|(?:[^\s;&|`()]*/)?reference_search\.py(?:\s|\"|$)"
    r"|-c\s+"
    r")"
    # python (without -X utf8) — plan legacy / update-state
    r"|python\s+"
    # cat — read-only file viewing
    r"|cat\s+"
    # export — env variable setup, only affects subprocess
    r"|export\s+\w+="
    r")"
)

# 危险 shell 元字符（命令链/替换）：出现在引号外即拒绝，防 `; rm -rf /` 注入。
_SHELL_METACHARS = set(";|&`\n\r")

# 安全的 cd 前缀（agent 模板常用 cd ${SCRIPTS_DIR} && python ... ——
# cwd 已在 _exec_bash 中设为 SCRIPTS_DIR，cd 是冗余的安全操作，直接剥离）
_CD_AND_RE = re.compile(r"^cd\s+(?:\"[^\"]*\"|'[^']*'|\S+)\s*&&\s*", re.ASCII)


def _has_unquoted_metachars(cmd: str) -> bool:
    """扫描命令串，若引号外出现 ; | & ` 换行 或 $( 则认为有命令链注入风险。"""
    in_single = in_double = False
    i = 0
    n = len(cmd)
    while i < n:
        ch = cmd[i]
        if in_single:
            if ch == "'":
                in_single = False
        elif in_double:
            if ch == "\\":
                i += 1  # 跳过转义字符
            elif ch == '"':
                in_double = False
        else:
            if ch == "'":
                in_single = True
            elif ch == '"':
                in_double = True
            elif ch in _SHELL_METACHARS:
                return True
            elif ch == "$" and i + 1 < n and cmd[i + 1] == "(":
                return True
        i += 1
    return False

# 路径基准（0817 重构后模块下移一层，parents 索引同步修正）
# 本文件 = app/dashboard/services/agent_runner.py
#   parents[1] = app/dashboard/  · parents[2] = app/  · parents[3] = 仓库根
_PLUGIN_ROOT = Path(__file__).resolve().parents[2]          # app/
_REPO_ROOT = Path(__file__).resolve().parents[3]           # ainovel-write/
_SCRIPTS_DIR = _REPO_ROOT / "tools"                         # 仓库根 tools/
_REFERENCES_DIR = _PLUGIN_ROOT / "references"              # app/references/
_SKILLS_DIR = _REPO_ROOT / "config" / "skills"             # config/skills/


def _resolve_file(project_root: Path, file_path: str) -> Path:
    """解析文件路径：优先 project_root 下，否则插件目录下。"""
    for base in (project_root.resolve(), _PLUGIN_ROOT.resolve()):
        try:
            p = (base / file_path).resolve()
            if p.is_file():
                return p
        except (OSError, ValueError):
            continue
    # 最后试 safe_resolve（project_root 下）
    try:
        return safe_resolve(project_root, file_path)
    except Exception:
        raise FileNotFoundError(f"文件不存在或不在白名单范围内: {file_path}")


def _resolve_writable(project_root: Path, file_path: str) -> Path:
    """解析可写路径：必须位于 project_root 内部（防穿越）。"""
    p = Path(file_path)
    if not p.is_absolute():
        p = project_root / p
    try:
        p = p.resolve()
        pr = project_root.resolve()
        p.relative_to(pr)
    except (OSError, ValueError) as exc:
        raise ValueError(f"写入路径越界（必须在 PROJECT_ROOT 内）: {file_path}") from exc
    return p


def _validate_bash(command: str) -> bool:
    """白名单 + 引号外元字符防注入。仅允许 data_modules / ainovel.py /
    reference_search.py / -c 四种 python 调用，且不得含命令链/替换。"""
    cmd = command.strip()
    # 剥离安全的 cd DIR && 前缀（agent 模板常用；cwd 已设为 SCRIPTS_DIR）
    m = _CD_AND_RE.match(cmd)
    if m:
        cmd = cmd[m.end():]
    if not _BASH_WHITELIST.match(cmd):
        return False
    if _has_unquoted_metachars(cmd):
        return False
    return True


def _usage_to_dict(usage: Any) -> dict:
    """把 Anthropic Message.usage 转成普通 dict（兼容 pydantic model 与原生对象）。"""
    if usage is None:
        return {}
    # pydantic v2 model
    dump = getattr(usage, "model_dump", None)
    if callable(dump):
        try:
            d = dump()
            if isinstance(d, dict):
                return d
        except Exception:
            import logging
            logging.getLogger("dashboard.agent_runner").debug(
                "model_dump failed on usage object, falling back to field extraction")
    # 兼容：逐字段取
    out: dict = {}
    for key in (
        "input_tokens", "output_tokens",
        "cache_read_input_tokens", "cache_creation_input_tokens",
    ):
        val = getattr(usage, key, None)
        if val is not None:
            out[key] = val
    return out


async def _noop_async(*_args, **_kwargs) -> None:
    """默认的 async 空回调。"""
    return None


def _to_openai_tool(tool: dict) -> dict:
    """把 Anthropic 形工具定义转成 OpenAI function-calling 形。

    Anthropic: {"name","description","input_schema"}
    OpenAI:    {"type":"function","function":{"name","description","parameters"}}
    """
    return {
        "type": "function",
        "function": {
            "name": tool["name"],
            "description": tool.get("description", ""),
            "parameters": tool.get("input_schema", {"type": "object", "properties": {}}),
        },
    }


def _openai_usage_to_dict(usage: Any) -> dict:
    """把 OpenAI CompletionUsage 映射成与 Anthropic usage 同构的 dict。

    prompt_tokens → input_tokens；completion_tokens → output_tokens；
    prompt_tokens_details.cached_tokens → cache_read_input_tokens。
    """
    if usage is None:
        return {}
    out: dict = {}
    pt = getattr(usage, "prompt_tokens", None)
    ct = getattr(usage, "completion_tokens", None)
    if pt is not None:
        out["input_tokens"] = int(pt)
    if ct is not None:
        out["output_tokens"] = int(ct)
    pd = getattr(usage, "prompt_tokens_details", None)
    if pd is not None:
        cached = getattr(pd, "cached_tokens", None)
        if cached is not None:
            out["cache_read_input_tokens"] = int(cached)
    cd = getattr(usage, "completion_tokens_details", None)
    if cd is not None:
        # reasoning_tokens 不直接对应 Anthropic 字段，但留作扩展
        pass
    return out


def _default_model() -> str:
    """默认 chat 模型：等同于主力模型。"""
    return _default_pro_model()


def _default_pro_model() -> str:
    """主力模型（正文/大纲/审查）：ARK_MODEL_PRO 环境变量，默认 doubao-1.5-pro-32k。"""
    return (
        os.environ.get("ARK_MODEL_PRO")
        or os.environ.get("ANTHROPIC_MODEL")
        or "doubao-1.5-pro-32k"
    )


def _default_character_model() -> str:
    """人物模型（对话/情绪润色）：ARK_MODEL_CHARACTER 环境变量，默认 Doubao-Seed-Character。"""
    return (
        os.environ.get("ARK_MODEL_CHARACTER")
        or os.environ.get("ANTHROPIC_MODEL")
        or "Doubao-Seed-Character"
    )


def _default_review_model() -> str:
    """审查模型（独立于 draft 模型，避免同模型自审盲区）：ARK_MODEL_REVIEW 环境变量，
    未设置时回退到主力模型。推荐使用与 draft 不同的模型以保证审查独立性。"""
    return (
        os.environ.get("ARK_MODEL_REVIEW")
        or os.environ.get("ANTHROPIC_REVIEW_MODEL")
        or _default_pro_model()
    )


# ---------------------------------------------------------------------------
# AnthropicAgentRunner
# ---------------------------------------------------------------------------

class AnthropicAgentRunner:
    """复用 Anthropic SDK 的 messages.stream 做多轮 tool-use 循环。"""

    def __init__(
        self,
        *,
        project_root: Path,
        model: str | None = None,
        system_prompt: str = "",
        max_tokens: int = 8192,
        allowed_tools: list[str] | None = None,
        on_event: Optional[Callable[[dict], Awaitable[None]]] = None,
        on_token: Optional[Callable[[str], Awaitable[None]]] = None,
        on_ask_user: Optional[Callable[[dict], Awaitable[dict]]] = None,
        max_turns: int = 20,
        agent_name: str = "agent",
        cancel_event: Optional["asyncio.Event"] = None,
        temperature: Optional[float] = None,
        meta: Optional[dict] = None,
    ) -> None:
        self.project_root = project_root
        self.model = model or _default_model()
        self.is_pro_model = (self.model == _default_pro_model())
        self.system_prompt = system_prompt
        self.max_tokens = max_tokens
        self.allowed_tools = set(allowed_tools or ["Read", "Grep", "Bash"])
        # 回调必须是 async（返回 Awaitable），由调用方传入负责推 SSE
        self.on_event = on_event or _noop_async
        self.on_token = on_token or _noop_async
        # AskUser 挂起回调：传入 prompt dict，返回用户 answer dict
        self.on_ask_user = on_ask_user
        self.max_turns = max_turns
        self.agent_name = agent_name
        self.last_usage: dict = {}
        # 取消信号：run() 每轮检查，被 set 则优雅退出
        self.cancel_event = cancel_event
        # 是否因达到 max_turns 而被截断（未自然结束）
        self.truncated = False
        # 采样温度：None = 服务端默认；显式设置可控制生成随机性（plan 高 temp 避免重写雷同）
        self.temperature = temperature
        # Prompt 审阅台：捕获元信息（stage/chapter/batch），用于 log_prompt
        self.meta = meta or {}

        # 按模型选择 API key + base URL：人物模型与主力模型使用独立的接入点
        _char_model = _default_character_model()
        if self.model == _char_model:
            api_key = os.environ.get("ARK_API_KEY_CHARACTER") or os.environ.get("ARK_API_KEY") or os.environ.get("ANTHROPIC_API_KEY") or ""
            base_url = (
                os.environ.get("ARK_BASE_URL_CHARACTER")
                or os.environ.get("ARK_BASE_URL")
                or os.environ.get("ANTHROPIC_BASE_URL")
                or "https://ark.cn-beijing.volces.com/api/coding/v3"
            )
        else:
            api_key = os.environ.get("ARK_API_KEY") or os.environ.get("ANTHROPIC_API_KEY") or ""
            base_url = (
                os.environ.get("ARK_BASE_URL_PRO")
                or os.environ.get("ARK_BASE_URL")
                or os.environ.get("ANTHROPIC_BASE_URL")
                or "https://ark.cn-beijing.volces.com/api/coding/v3"
            )
        self._client = AsyncOpenAI(api_key=api_key, base_url=base_url)

    # ------------------------------------------------------------------
    # 工具执行
    # ------------------------------------------------------------------

    async def _execute_tool(self, name: str, input_: dict) -> str:
        """本地执行一个工具调用，返回结果字符串。"""
        if name not in self.allowed_tools:
            return f"工具 {name} 不在白名单中"
        if name == "Read":
            return await self._exec_read(input_)
        if name == "Grep":
            return await self._exec_grep(input_)
        if name == "Bash":
            return await self._exec_bash(input_)
        if name == "Write":
            return await self._exec_write(input_)
        if name == "Edit":
            return await self._exec_edit(input_)
        if name == "AskUser":
            return await self._exec_ask_user(input_)
        return f"未知工具: {name}"

    async def _exec_read(self, input_: dict) -> str:
        file_path = input_.get("file_path", "")
        await self.on_event({"phase": "tool_use", "name": "Read", "input": input_})
        try:
            p = _resolve_file(self.project_root, file_path)
            content = p.read_text(encoding="utf-8", errors="replace")
            await self.on_event({"phase": "tool_result", "name": "Read",
                                 "output_preview": content[:200]})
            return content
        except FileNotFoundError as exc:
            return str(exc)
        except Exception as exc:
            return f"读文件失败: {exc}"

    async def _exec_grep(self, input_: dict) -> str:
        pattern = input_.get("pattern", "")
        glob_ = input_.get("glob", "*.md")
        await self.on_event({"phase": "tool_use", "name": "Grep", "input": input_})
        try:
            # 用 subprocess 调 ripgrep：
            # 搜索范围：project_root（只读）
            proc = await asyncio.create_subprocess_exec(
                "rg", "-n", "--no-heading", "--glob", glob_,
                pattern, str(self.project_root.resolve()),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=30.0,
            )
            output = stdout.decode("utf-8", errors="replace")[:10000]
            if not output and stderr:
                output = f"grep 无结果或错误: {stderr.decode('utf-8', errors='replace')[:500]}"
            await self.on_event({"phase": "tool_result", "name": "Grep",
                                 "output_preview": output[:200]})
            return output
        except Exception as exc:
            return f"Grep 失败: {exc}"

    async def _exec_bash(self, input_: dict) -> str:
        command = input_.get("command", "").strip()
        await self.on_event({"phase": "tool_use", "name": "Bash", "input": input_})
        if not _validate_bash(command):
            msg = f"Bash 命令被拒绝（白名单不匹配）: {command}"
            await self.on_event({"phase": "tool_result", "name": "Bash",
                                 "output_preview": msg})
            return msg
        try:
            cwd = str(_SCRIPTS_DIR)
            env = os.environ.copy()
            env.setdefault("PYTHONIOENCODING", "utf-8")
            env.setdefault("PYTHONUTF8", "1")
            # 让 skill 里的 ${SCRIPTS_DIR}/${PROJECT_ROOT}/${CLAUDE_PLUGIN_ROOT} 可用
            env["SCRIPTS_DIR"] = str(_SCRIPTS_DIR)
            env["PROJECT_ROOT"] = str(self.project_root.resolve())
            env["CLAUDE_PLUGIN_ROOT"] = str(_PLUGIN_ROOT)
            env["WORKSPACE_ROOT"] = str(self.project_root.resolve().parent)
            # 把开头的 `python ` 替换为解释器绝对路径，避免 PATH 不稳
            run_cmd = command
            if run_cmd.startswith("python "):
                run_cmd = (sys.executable or "python") + run_cmd[len("python"):]
            proc = await asyncio.create_subprocess_shell(
                run_cmd,
                cwd=cwd,
                env=env,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=300.0,
            )
            out = stdout.decode("utf-8", errors="replace")
            err = stderr.decode("utf-8", errors="replace")
            result = out
            if err:
                result += f"\n[stderr]\n{err}"
            await self.on_event({"phase": "tool_result", "name": "Bash",
                                 "output_preview": result[:500]})
            return result
        except Exception as exc:
            return f"Bash 执行失败: {exc}"

    async def _exec_write(self, input_: dict) -> str:
        file_path = input_.get("file_path", "")
        content = input_.get("content", "")
        await self.on_event({"phase": "tool_use", "name": "Write", "input": {
            "file_path": file_path, "content_len": len(content)}})
        try:
            p = _resolve_writable(self.project_root, file_path)
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content, encoding="utf-8")
            msg = f"已写入 {p.relative_to(self.project_root)} ({len(content)} 字符)"
            await self.on_event({"phase": "tool_result", "name": "Write",
                                 "output_preview": msg})
            return msg
        except Exception as exc:
            return f"Write 失败: {exc}"

    async def _exec_edit(self, input_: dict) -> str:
        file_path = input_.get("file_path", "")
        old_string = input_.get("old_string", "")
        new_string = input_.get("new_string", "")
        await self.on_event({"phase": "tool_use", "name": "Edit", "input": {
            "file_path": file_path,
            "old_len": len(old_string), "new_len": len(new_string)}})
        try:
            p = _resolve_writable(self.project_root, file_path)
            if not p.is_file():
                return f"Edit 失败：文件不存在 {file_path}"
            text = p.read_text(encoding="utf-8", errors="replace")
            count = text.count(old_string)
            if count == 0:
                return f"Edit 失败：old_string 未匹配到（文件未改动）"
            if count > 1:
                return f"Edit 失败：old_string 匹配到 {count} 处（需唯一）"
            new_text = text.replace(old_string, new_string, 1)
            p.write_text(new_text, encoding="utf-8")
            msg = f"已替换 {p.relative_to(self.project_root)}"
            await self.on_event({"phase": "tool_result", "name": "Edit",
                                 "output_preview": msg})
            return msg
        except Exception as exc:
            return f"Edit 失败: {exc}"

    async def _exec_ask_user(self, input_: dict) -> str:
        question = input_.get("question", "")
        options = input_.get("options")
        placeholder = input_.get("placeholder", "")
        await self.on_event({"phase": "tool_use", "name": "AskUser",
                             "input": {"question": question, "options": options}})
        prompt = {"question": question, "placeholder": placeholder}
        if options:
            prompt["options"] = options
        if self.on_ask_user is None:
            # 无挂起回调（如独立 agent 路由）→ 自动按第一选项或空作答
            ans = options[0] if options else ""
        else:
            ans_dict = await self.on_ask_user(prompt)
            ans = ans_dict.get("answer", "") if isinstance(ans_dict, dict) else str(ans_dict)
        await self.on_event({"phase": "tool_result", "name": "AskUser",
                             "output_preview": f"用户回答: {ans}"})
        return f"用户回答: {ans}"

    # ------------------------------------------------------------------
    # 主循环
    # ------------------------------------------------------------------

    async def _stream_chat_with_retry(
        self,
        create_kwargs: dict,
        *,
        on_progress: Optional[Callable[[str], Awaitable[None]]] = None,
    ):
        """包一层 chat.completions 流式调用：429/5xx/网络/超时 全部指数退避重试。

        退避时通过 on_progress 推送 '⏳ 限流等待 Ns...' 等提示，让前端可见。
        多次重试仍然成功时，返回的 stream 正常 yield 完所有 chunk。
        重试耗尽时抛最后一次的异常（让上层走最终错误分支）。
        """
        max_retries, base_delay, max_delay = _get_retry_config()
        attempt = 0
        while True:
            try:
                sem = _get_llm_semaphore()
                async with sem:
                    return await self._client.chat.completions.create(**create_kwargs)
            except RateLimitError as exc:
                if attempt >= max_retries:
                    raise
                # 优先 Retry-After 头；openai 库把它放到 exc.response.headers
                retry_after = None
                try:
                    headers = getattr(getattr(exc, "response", None), "headers", None) or {}
                    if hasattr(headers, "get"):
                        retry_after = headers.get("Retry-After") or headers.get("retry-after")
                    else:
                        retry_after = headers.get("Retry-After") or headers.get("retry-after")
                except Exception:
                    pass
                if retry_after:
                    try:
                        delay = float(retry_after)
                    except (TypeError, ValueError):
                        delay = base_delay * (2 ** min(attempt, 5))
                else:
                    delay = base_delay * (2 ** min(attempt, 5))
                # 加 ±20% 抖动防雪崩
                jitter = delay * (0.8 + 0.4 * random.random())
                delay = min(max_delay, jitter)
                attempt += 1
                msg = f"⏳ LLM 限流（429），第 {attempt}/{max_retries} 次重试，等待 {delay:.1f}s"
                print(f"[WARN] {msg}: {exc}")
                if on_progress is not None:
                    try:
                        await on_progress(msg)
                    except Exception:
                        pass
                await asyncio.sleep(delay)
            except (APITimeoutError, APIConnectionError) as exc:
                if attempt >= max_retries:
                    raise
                delay = min(max_delay, base_delay * (2 ** min(attempt, 5)))
                attempt += 1
                msg = f"⏳ LLM 网络/超时，第 {attempt}/{max_retries} 次重试，等待 {delay:.1f}s"
                print(f"[WARN] {msg}: {exc}")
                if on_progress is not None:
                    try:
                        await on_progress(msg)
                    except Exception:
                        pass
                await asyncio.sleep(delay)
            except InternalServerError as exc:
                if attempt >= max_retries:
                    raise
                delay = min(max_delay, base_delay * (2 ** min(attempt, 5)))
                attempt += 1
                msg = f"⏳ LLM 5xx 错误，第 {attempt}/{max_retries} 次重试，等待 {delay:.1f}s"
                print(f"[WARN] {msg}: {exc}")
                if on_progress is not None:
                    try:
                        await on_progress(msg)
                    except Exception:
                        pass
                await asyncio.sleep(delay)

    async def run(self, user_input: str) -> str:
        """运行多轮 function-calling 循环，返回最终的 assistant 文本。"""
        # Prompt 审阅台：捕获实际发出的 system+user prompt（首轮即「发给模型的 prompt」）
        try:
            from .workflows._prompt_log import log_prompt
            log_prompt(
                self.project_root, self.agent_name,
                self.system_prompt, user_input, self.model, self.meta,
            )
        except Exception:
            pass

        messages: list[dict] = []
        if self.system_prompt:
            messages.append({"role": "system", "content": self.system_prompt})
        messages.append({"role": "user", "content": user_input})
        openai_tools = [_to_openai_tool(t) for t in ALL_TOOLS if t["name"] in self.allowed_tools]
        final_text = ""
        truncated = False

        for turn in range(self.max_turns):
            # 取消检查点：用户点"停止"后 cancel_event 被 set，优雅退出循环
            if self.cancel_event is not None and self.cancel_event.is_set():
                await self.on_event({"phase": "stdout", "line": "已取消，停止后续轮次。"})
                break
            await self.on_event({"phase": "token", "text": f"\n\n[Turn {turn + 1}]\n"})

            create_kwargs: dict = {
                "model": self.model,
                "max_tokens": self.max_tokens,
                "messages": messages,
                "stream": True,
                "stream_options": {"include_usage": True},
            }
            if self.temperature is not None:
                create_kwargs["temperature"] = self.temperature
            # 【v7.0.1】所有模型无条件关闭 thinking：AI 写作不需要推理，思考模式
            # 拖慢延迟且烧 token。原 `if self.is_pro_model:` 只对 pro 模型生效，
            # 非 pro 模型（kimi/doubao/glm 等）会漏开 → 统一强制注入 disabled。
            create_kwargs["extra_body"] = {"thinking": {"type": "disabled"}}
            if openai_tools:
                create_kwargs["tools"] = openai_tools
                create_kwargs["tool_choice"] = "auto"

            text_buf: list[str] = []
            # tool_calls 分片按 index 累积：{index: {"id","name","arguments"}}
            tool_acc: dict[int, dict] = {}
            finish_reason: str | None = None
            usage_obj: Any = None

            try:
                # 走带 429/5xx/网络退避重试的包装；recreate stream 内部会让前一次失败的 chunk 全部丢失
                # 这种情况只会发生在 429/5xx 全耗尽前,此时下面 except 会兜住
                async def _retry_progress(msg: str) -> None:
                    await self.on_event({"phase": "stdout", "line": msg})

                stream = await self._stream_chat_with_retry(
                    create_kwargs, on_progress=_retry_progress,
                )
                async for chunk in stream:
                    if getattr(chunk, "usage", None):
                        usage_obj = chunk.usage
                    choices = getattr(chunk, "choices", None) or []
                    if not choices:
                        continue
                    choice = choices[0]
                    if getattr(choice, "finish_reason", None):
                        finish_reason = choice.finish_reason
                    delta = getattr(choice, "delta", None)
                    if delta is None:
                        continue

                    txt = getattr(delta, "content", None)
                    if txt:
                        text_buf.append(txt)
                        await self.on_token(txt)
                        await self.on_event({"phase": "token", "text": txt})

                    tcs = getattr(delta, "tool_calls", None)
                    if tcs:
                        for tc in tcs:
                            idx = tc.index if tc.index is not None else 0
                            slot = tool_acc.setdefault(
                                idx, {"id": "", "name": "", "arguments": ""}
                            )
                            if getattr(tc, "id", None):
                                slot["id"] = tc.id
                            fn = getattr(tc, "function", None)
                            if fn is not None:
                                if getattr(fn, "name", None):
                                    slot["name"] = fn.name
                                if getattr(fn, "arguments", None):
                                    slot["arguments"] += fn.arguments
            except Exception as exc:
                # 流式中断：把已收到的文本返回，不抛（让上层 SSE 收尾）
                await self.on_event({"phase": "error", "msg": f"LLM 流式错误: {exc}"})
                final_text += "".join(text_buf)
                break

            # 记录本轮用量（计费看板 + 统一日志）
            if usage_obj is not None:
                u = _openai_usage_to_dict(usage_obj)
                if u:
                    self.last_usage = u
                    USAGE.record(model=self.model, agent=self.agent_name, usage=u)
                    # 写入全局统一日志
                    try:
                        from .core.universal_logger import get_logger
                        get_logger().log_llm_call(
                            model=self.model,
                            prompt_tokens=u.get("input_tokens", 0),
                            completion_tokens=u.get("output_tokens", 0),
                            total_tokens=u.get("input_tokens", 0) + u.get("output_tokens", 0),
                            call_type=self.agent_name,
                            status="success",
                            subsystem="main",
                        )
                    except Exception:
                        pass  # 日志失败不影响业务

            assistant_text = "".join(text_buf)
            final_text += assistant_text

            # 无工具调用或非 tool_calls 结束 → 本轮结束
            if finish_reason != "tool_calls" or not tool_acc:
                break

            # 在最后一轮仍想调工具 → 被 max_turns 截断
            if turn == self.max_turns - 1:
                truncated = True

            # 组装 assistant 消息（含 tool_calls）
            ordered = [tool_acc[i] for i in sorted(tool_acc)]
            assistant_tool_calls = [
                {
                    "id": slot["id"],
                    "type": "function",
                    "function": {
                        "name": slot["name"],
                        "arguments": slot["arguments"] or "{}",
                    },
                }
                for slot in ordered
            ]
            messages.append({
                "role": "assistant",
                "content": assistant_text or None,
                "tool_calls": assistant_tool_calls,
            })

            # 执行每个工具并把结果作为 role=tool 消息追加
            for slot in ordered:
                try:
                    inp = json.loads(slot["arguments"] or "{}")
                    if not isinstance(inp, dict):
                        inp = {}
                except Exception:
                    import logging
                    logging.getLogger("dashboard.agent_runner").debug(
                        "Failed to parse tool arguments JSON: %s", slot.get("arguments", "")[:100])
                    inp = {}
                result_text = await self._execute_tool(slot["name"], inp)
                messages.append({
                    "role": "tool",
                    "tool_call_id": slot["id"],
                    "content": result_text,
                })

        # 最终结果（done 事件由调用方负责推到 task SSE）
        self.truncated = truncated
        # 空响应诊断日志：记录上下文信息便于排查 API 返回空正文的问题
        if not final_text or not final_text.strip():
            import logging
            logging.getLogger("dashboard.agent_runner").warning(
                "Agent '%s' (model=%s, turns=%d, truncated=%s) returned empty final_text. "
                "Last usage: %s",
                self.agent_name, self.model, turn + 1, truncated,
                self.last_usage,
            )
        return final_text