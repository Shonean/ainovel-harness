"""改编层 v0 — pydantic 模型（Game Pack schema 0.1）。

按《改编层 v0 设计规格》§5 定义 pack 全部 JSON 结构 + §10 校验报告结构。
所有模型 schema_version 固定 "0.1"；story 节点用 type 字段做 discriminated union。
"""
from __future__ import annotations

from typing import Any, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, model_validator

SCHEMA_VERSION = "0.1"

# §5.3 行类型
LineKind = Literal["narration", "inner", "dialogue", "stage"]
# §5.6 资产类型
AssetKind = Literal["background", "portrait", "model3d", "bgm", "sfx", "voice", "ui"]
# §5.3 stage.cue 枚举（v0）
STAGE_CUES = ("paper_figures_turn", "note_flip", "light_flicker", "scene_shift")
# §5.7 交互动词（v0 只生成 inspect/advance，schema 允许预留动词）
INTERACTION_KINDS = ("inspect", "advance", "take", "replace", "use", "open", "hide", "listen")


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


# ============================================================
# story.json
# ============================================================

class Line(_Model):
    kind: LineKind
    text: str = ""
    # dialogue
    speaker: str | None = None
    expression: str | None = None
    # stage
    cue: str | None = None
    # narration（details 投影可标 detail=true）
    detail: bool | None = None


class SceneNode(_Model):
    id: str
    type: Literal["scene"]
    title: str
    background: str | None = None
    present: list[str] = Field(default_factory=list)
    lines: list[Line] = Field(default_factory=list)
    interaction_refs: list[str] = Field(default_factory=list)
    next: str
    source: dict[str, Any] = Field(default_factory=dict)


class ChoiceOption(_Model):
    text: str
    next: str
    set: dict[str, bool] = Field(default_factory=dict)


class ChoiceNode(_Model):
    id: str
    type: Literal["choice"]
    prompt: str
    options: list[ChoiceOption] = Field(min_length=1)
    converge_to: str
    llm_zone: str | None = None
    source: dict[str, Any] = Field(default_factory=dict)


class EndingNode(_Model):
    id: str
    type: Literal["ending"]
    title: str
    text: str
    # v0 仅 flags.<name> 布尔表达式；None=默认结局
    condition: str | None = None
    source: dict[str, Any] = Field(default_factory=dict)


AnyNode = Union[SceneNode, ChoiceNode, EndingNode]


class Story(_Model):
    start: str
    nodes: list[AnyNode] = Field(min_length=1)

    def node_map(self) -> dict[str, AnyNode]:
        return {n.id: n for n in self.nodes}


# ============================================================
# characters.json / world.json
# ============================================================

class CharacterAssets(_Model):
    portrait: str | None = None
    expressions: list[str] = Field(default_factory=list)


class CharacterVoice(_Model):
    kokoro: str | None = None
    cloud: str | None = None


class Character(_Model):
    id: str
    name: str
    role: Literal["protagonist", "support"] = "support"
    aliases: list[str] = Field(default_factory=list)
    description: str = ""
    source_element: str | None = None
    presence: list[str] = Field(default_factory=list)
    assets: CharacterAssets = Field(default_factory=CharacterAssets)
    voice: CharacterVoice = Field(default_factory=CharacterVoice)


class CharactersFile(_Model):
    characters: list[Character] = Field(default_factory=list)


class WorldEntry(_Model):
    id: str
    name: str
    source_element: str | None = None
    description: str = ""


class WorldFile(_Model):
    locations: list[WorldEntry] = Field(default_factory=list)
    items: list[WorldEntry] = Field(default_factory=list)
    settings: list[WorldEntry] = Field(default_factory=list)
    terms: list[WorldEntry] = Field(default_factory=list)


# ============================================================
# assets.json / interaction.json / llm_zones.json
# ============================================================

class Asset(_Model):
    asset_id: str
    kind: AssetKind
    ref: str | None = None
    prompt: str = ""
    used_by: list[str] = Field(default_factory=list)
    status: Literal["pending", "ready", "failed"] = "pending"
    hash: str | None = None
    provider_hint: str | None = None


class AssetsFile(_Model):
    assets: list[Asset] = Field(default_factory=list)


class InteractionResult(_Model):
    narration: str = ""
    set: dict[str, bool] = Field(default_factory=dict)


class InteractionEntity(_Model):
    id: str
    node: str
    kind: str = "inspect"
    label: str = ""
    prompt: str = ""
    result: InteractionResult = Field(default_factory=InteractionResult)
    next: str | None = None


class InteractionFile(_Model):
    entities: list[InteractionEntity] = Field(default_factory=list)


class ZoneBudget(_Model):
    max_turns: int = 6
    max_chars: int = 1200


class LlmZone(_Model):
    id: str
    node: str
    allow: list[str] = Field(default_factory=list)
    persona_guard: str = ""
    hard_events: list[str] = Field(default_factory=list)
    fallback_map: dict[str, str] = Field(default_factory=dict)
    budget: ZoneBudget = Field(default_factory=ZoneBudget)


class LlmZoneGlobal(_Model):
    rule: str = ""
    forbidden: list[str] = Field(default_factory=list)


class LlmZonesFile(_Model):
    zones: list[LlmZone] = Field(default_factory=list)
    global_: LlmZoneGlobal = Field(default_factory=LlmZoneGlobal, alias="global")

    model_config = ConfigDict(extra="forbid", populate_by_name=True)


# ============================================================
# pack.json
# ============================================================

class PackSource(_Model):
    book_title: str = ""
    book_root: str = ""
    arc_ids: list[str] = Field(default_factory=list)
    chapter_nums: list[int] = Field(default_factory=list)
    builder_version: str = "0.1.0"


class PackGame(_Model):
    title: str = ""
    logline: str = ""
    genre_tags: list[str] = Field(default_factory=list)
    play_minutes_est: int = 8
    entry_node: str = ""
    endings: list[str] = Field(default_factory=list)


class PackOnlineMode(_Model):
    llm_zones: str = "llm_zones.json"
    enabled: bool = False


class PackModes(_Model):
    offline: bool = True
    online: PackOnlineMode = Field(default_factory=PackOnlineMode)


class PackFiles(_Model):
    story: str = "story.json"
    characters: str = "characters.json"
    world: str = "world.json"
    assets: str = "assets.json"
    interaction: str = "interaction.json"
    llm_zones: str = "llm_zones.json"
    ink: str = "ink/story.ink.json"


class PackInfo(_Model):
    schema_version: str = SCHEMA_VERSION
    pack_id: str
    created_at: str
    source: PackSource = Field(default_factory=PackSource)
    game: PackGame = Field(default_factory=PackGame)
    modes: PackModes = Field(default_factory=PackModes)
    files: PackFiles = Field(default_factory=PackFiles)


# ============================================================
# validation.json（§5.9 / §10）
# ============================================================

class ValidationIssue(_Model):
    code: str
    node: str | None = None
    detail: str = ""


class ValidationStats(_Model):
    nodes: int = 0
    scenes: int = 0
    choices: int = 0
    endings: int = 0
    lines: int = 0
    assets: int = 0
    llm_calls: int = 0
    cost_est: float = 0.0


class ValidationReport(_Model):
    ok: bool = True
    errors: list[ValidationIssue] = Field(default_factory=list)
    warnings: list[ValidationIssue] = Field(default_factory=list)
    stats: ValidationStats = Field(default_factory=ValidationStats)

    def add_error(self, code: str, detail: str = "", node: str | None = None) -> None:
        self.errors.append(ValidationIssue(code=code, node=node, detail=detail))

    def add_warning(self, code: str, detail: str = "", node: str | None = None) -> None:
        self.warnings.append(ValidationIssue(code=code, node=node, detail=detail))

    def finalize(self) -> "ValidationReport":
        self.ok = not self.errors
        return self


# ============================================================
# pack 目录内全部数据的内存载体（构建/校验共用）
# ============================================================

class PackData(_Model):
    """一个 pack 的内存表示：落盘前与 validate_dir 读回后共用同一结构。"""

    info: PackInfo
    story: Story
    characters: CharactersFile = Field(default_factory=CharactersFile)
    world: WorldFile = Field(default_factory=WorldFile)
    assets: AssetsFile = Field(default_factory=AssetsFile)
    interaction: InteractionFile = Field(default_factory=InteractionFile)
    llm_zones: LlmZonesFile = Field(default_factory=LlmZonesFile)
    ink_text: str | None = None  # story.ink 原文（校验用；不进 story.json）
