"""Records produced by extractors and the evidence envelope returned by every query."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

SymbolKind = Literal["module", "class", "interface", "function", "method"]
# queue: a job sent by a literal name; queue_const: by a constant (dotted reference); queue_actor: `<actor>.send(...)`
RefKind = Literal[
    "call", "import", "inherit", "instantiate", "queue", "queue_const", "queue_actor", "curated"
]
ConfigOrigin = Literal["json", "yaml", "env-example", "code-read"]
Source = Literal["parsed", "derived", "curated"]
ErrorCode = Literal["not_indexed", "ambiguous_symbol", "not_found", "invalid_argument"]


class Symbol(BaseModel):
    kind: SymbolKind
    name: str
    qualified_name: str
    line_start: int
    line_end: int
    visibility: Literal["public", "private"] = "public"
    param_count: int | None = None


class Reference(BaseModel):
    from_qualified: str
    to_name: str
    kind: RefKind
    line: int
    receiver_type: str | None = None
    note: str | None = None  # curated links only


class ImportFact(BaseModel):
    local_name: str
    target: str
    line: int


class ConfigKey(BaseModel):
    key_path: str
    line: int
    origin: ConfigOrigin
    reader_qualified: str | None = None


class QueueHandler(BaseModel):
    """A function that handles jobs sent under `name`."""

    protocol: Literal["dramatiq"] = "dramatiq"
    name: str
    handler_qualified: str
    line: int


class StringConst(BaseModel):
    """A constant whose value is shaped like a name; job names behind constants are matched through these."""

    qualified_name: str
    value: str
    line: int


class EntryPoint(BaseModel):
    kind: Literal["main-block", "console-script", "program-main"]
    name: str
    line: int
    target: str | None = None


class ExtractResult(BaseModel):
    symbols: list[Symbol] = Field(default_factory=list[Symbol])
    references: list[Reference] = Field(default_factory=list[Reference])
    imports: list[ImportFact] = Field(default_factory=list[ImportFact])
    config_keys: list[ConfigKey] = Field(default_factory=list[ConfigKey])
    entry_points: list[EntryPoint] = Field(default_factory=list[EntryPoint])
    queue_handlers: list[QueueHandler] = Field(default_factory=list[QueueHandler])
    string_consts: list[StringConst] = Field(default_factory=list[StringConst])
    parse_error: bool = False
    module: str | None = None


class Evidence(BaseModel):
    repo: str
    path: str
    line: int
    commit: str


class AsOf(BaseModel):
    repo: str
    commit: str
    indexed_at: datetime


class Answer[T](BaseModel):
    data: T
    evidence: list[Evidence] = Field(default_factory=list[Evidence])
    source: Source = "parsed"
    as_of: list[AsOf] = Field(default_factory=list[AsOf])
    confidence: float = 1.0
    stale: bool = False
    notes: list[str] = Field(default_factory=list[str])


def combine_sources(sources: Iterable[Source]) -> Source:
    seen = set(sources)
    if "derived" in seen:
        return "derived"
    if "curated" in seen:
        return "curated"
    return "parsed"


class ToolError(Exception):
    """A structured failure returned to the agent as {error, message, hint, data}."""

    def __init__(
        self,
        code: ErrorCode,
        message: str,
        hint: str = "",
        data: list[dict[str, object]] | None = None,
    ) -> None:
        super().__init__(message)
        self.code: ErrorCode = code
        self.message = message
        self.hint = hint
        self.data: list[dict[str, object]] = data or []

    def to_dict(self) -> dict[str, object]:
        return {"error": self.code, "message": self.message, "hint": self.hint, "data": self.data}
