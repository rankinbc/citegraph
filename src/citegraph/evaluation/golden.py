"""Golden questions: a tool, its arguments, and the expected answer set."""

from __future__ import annotations

from pathlib import Path
from typing import Literal, cast

import yaml
from pydantic import BaseModel, ConfigDict

EvalTool = Literal["what_calls", "what_does_it_call", "find_path", "find_config_key"]


class GoldenQuestion(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str
    tool: EvalTool
    args: dict[str, str]
    expected: list[str]
    ci: bool = False
    note: str = ""


def load_golden(path: Path) -> list[GoldenQuestion]:
    raw = cast(list[object], yaml.safe_load(path.read_text(encoding="utf-8")) or [])
    questions = [GoldenQuestion.model_validate(item) for item in raw]
    ids = [q.id for q in questions]
    duplicates = sorted({i for i in ids if ids.count(i) > 1})
    if duplicates:
        raise ValueError(f"duplicate golden ids: {duplicates}")
    return questions
