"""Optional citegraph.toml at the repos root."""

from __future__ import annotations

import re
import tomllib
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

DEFAULT_EXCLUDE = [
    "**/node_modules/**",
    "**/.venv/**",
    "**/venv/**",
    "**/vendor/**",
    "**/dist/**",
    "**/build/**",
]


class ConfigError(Exception):
    """citegraph.toml is not valid TOML, has an unknown key or a wrong type, or holds an invalid regex."""


class QueuesConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # C# methods whose first argument names the job they send
    send_methods: list[str] = Field(default_factory=lambda: ["Enqueue", "EnqueueAsync"])


class CitegraphConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    include: list[str] = Field(default_factory=list[str])
    exclude: list[str] = Field(default_factory=lambda: list(DEFAULT_EXCLUDE))
    languages: list[str] = Field(default_factory=lambda: ["python", "csharp"])
    extra_redaction_patterns: list[str] = Field(default_factory=list[str])
    # "off": each git repository is one logical repo; "auto" or a list of folders: projects inside a repository
    projects: Literal["off", "auto"] | list[str] = "off"
    queues: QueuesConfig = Field(default_factory=QueuesConfig)

    @field_validator("extra_redaction_patterns")
    @classmethod
    def _patterns_compile(cls, patterns: list[str]) -> list[str]:
        for pattern in patterns:
            try:
                re.compile(pattern)
            except re.error as exc:
                raise ValueError(f"invalid regex {pattern!r}: {exc}") from exc
        return patterns


def load_config(root: Path) -> CitegraphConfig:
    path = root / "citegraph.toml"
    if not path.is_file():
        return CitegraphConfig()
    try:
        return CitegraphConfig.model_validate(tomllib.loads(path.read_text(encoding="utf-8")))
    except (tomllib.TOMLDecodeError, UnicodeDecodeError) as exc:
        raise ConfigError(f"{path}: invalid TOML: {exc}") from exc
    except ValidationError as exc:
        # loc and msg only: pydantic's own message would echo the offending input value
        problems = "; ".join(f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in exc.errors())
        raise ConfigError(f"{path}: {problems}") from exc
