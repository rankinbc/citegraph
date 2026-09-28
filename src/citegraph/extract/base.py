"""Extractor protocol and module naming shared by language extractors."""

from __future__ import annotations

from typing import Protocol

from citegraph.models import ExtractResult


class Extractor(Protocol):
    language: str

    def extract(self, rel_path: str, source: bytes) -> ExtractResult: ...


def module_name_for(rel_path: str) -> str:
    parts = rel_path.replace("\\", "/").split("/")
    if len(parts) > 1 and parts[0] == "src":
        parts = parts[1:]
    if parts[-1].endswith(".py"):
        parts[-1] = parts[-1][:-3]
    if len(parts) > 1 and parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def package_for(rel_path: str) -> str:
    module = module_name_for(rel_path)
    if rel_path.endswith("__init__.py"):
        return module
    return module.rsplit(".", 1)[0] if "." in module else ""
