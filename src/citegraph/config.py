"""Optional citegraph.toml at the repos root."""

from __future__ import annotations

import tomllib
from pathlib import Path

from pydantic import BaseModel, Field

DEFAULT_EXCLUDE = [
    "**/node_modules/**",
    "**/.venv/**",
    "**/venv/**",
    "**/vendor/**",
    "**/dist/**",
    "**/build/**",
]


class CitegraphConfig(BaseModel):
    include: list[str] = Field(default_factory=list[str])
    exclude: list[str] = Field(default_factory=lambda: list(DEFAULT_EXCLUDE))
    languages: list[str] = Field(default_factory=lambda: ["python"])
    extra_redaction_patterns: list[str] = Field(default_factory=list[str])


def load_config(root: Path) -> CitegraphConfig:
    path = root / "citegraph.toml"
    if not path.is_file():
        return CitegraphConfig()
    return CitegraphConfig.model_validate(tomllib.loads(path.read_text(encoding="utf-8")))
