"""
Pydantic 请求体模型 —— 从 workflows.py 拆分。
"""
from __future__ import annotations

from pydantic import BaseModel, Field


class _WriteBody(BaseModel):
    chapter: int = Field(ge=1)
    model: str | None = None
    context: str | None = None
    temperature: float | None = Field(default=None, ge=0.0, le=2.0)
    freedom_level: str | None = None  # "0%" | "10%" | "20%"，默认 "0%"
    fast: bool = False  # 快速模式：跳过 critic 审查
    auto_generate: bool = False  # 一键自动生成模式：跳过用户确认点
    resume: bool = False  # 续跑模式：跳过 progress.json 中已 done 的 step


class _ReviewBody(BaseModel):
    chapter: int = Field(ge=1)
    model: str | None = None


class _PlanBody(BaseModel):
    volume: int = Field(ge=1)
    model: str | None = None
    start_chapter: int | None = Field(default=None, ge=1)
    chapter_count: int = Field(default=10, ge=1, le=50)
    temperature: float | None = Field(default=None, ge=0.0, le=2.0)
    auto_generate: bool = False  # 一键自动生成模式：跳过用户确认点


class _InitBody(BaseModel):
    brief: dict = Field(default_factory=dict)
    reference_text_path: str | None = None
    model: str | None = None


class _FinalizeBody(BaseModel):
    chapter: int = Field(ge=1)
    final_path: str | None = None  # 显式指定要入库的正文文件；留空则自动查找
    model: str | None = None


class _UnfinalizeBody(BaseModel):
    chapter: int = Field(ge=1)


class _ConfirmPlotBody(BaseModel):
    chapter: int = Field(ge=1)
    model: str | None = None


class _ReplanBody(BaseModel):
    chapter: int = Field(ge=1)
    model: str | None = None


class _LearnBody(BaseModel):
    description: str = ""
    model: str | None = None


class _QueryBody(BaseModel):
    question: str
    model: str | None = None


class _CharacterSkillBody(BaseModel):
    """角色 skill 通用 body：主体参数 + 可选的场景/上下文。"""
    target: str = Field(default="", description="角色名或标题")
    context: str = Field(default="", description="场景/情绪/副标题等可选上下文")
    model: str | None = None


class _ChannelConfigBody(BaseModel):
    """Phase 6: 通道配置更新请求体。"""
    channel: dict = Field(default_factory=dict, description="通道配置字典，写入 state.json 的 channel 节")
