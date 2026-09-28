"""What a careful developer gets from ripgrep-style word-boundary search plus indentation heuristics."""

from __future__ import annotations

import re
from keyword import kwlist

from citegraph.evaluation.golden import GoldenQuestion
from citegraph.evaluation.spans import CorpusText, PyDef

CALL_NAME = re.compile(r"([A-Za-z_]\w*)\s*\(")
NOT_CALLS = frozenset(kwlist) | {"print"}


def _split(symbol: str) -> tuple[str, str]:
    repo, _, qualified = symbol.partition(":")
    return repo, qualified


def _callers(symbol: str, text: CorpusText) -> list[str]:
    _, qualified = _split(symbol)
    name = qualified.rsplit(".", 1)[-1]
    call = re.compile(rf"\b{re.escape(name)}\s*\(")
    definition = re.compile(rf"^\s*(?:async\s+)?(?:def|class)\s+{re.escape(name)}\b")
    found: set[str] = set()
    for (repo, path), lines in text.files.items():
        if not path.endswith(".py"):
            continue
        for number, line in enumerate(lines, start=1):
            if call.search(line) and not definition.match(line):
                found.add(f"{repo}:{text.enclosing(repo, path, number)}")
    return sorted(found)


def _body(definition: PyDef, lines: list[str]) -> list[str]:
    body: list[str] = []
    for line in lines[definition.line :]:
        if line.strip() and len(line) - len(line.lstrip()) <= definition.indent:
            break
        body.append(line)
    return body


def _callees(symbol: str, text: CorpusText) -> list[str]:
    repo, qualified = _split(symbol)
    matches = [d for d in text.defs if d.qualified == qualified and d.repo == repo]
    if not matches:
        return []
    definition = matches[0]
    found: set[str] = set()
    for line in _body(definition, text.files[(definition.repo, definition.path)]):
        for name in CALL_NAME.findall(line):
            if name in NOT_CALLS:
                continue
            found.update(f"{d.repo}:{d.qualified}" for d in text.defs_by_name.get(name, []))
    return sorted(found)


def _config(pattern: str, text: CorpusText) -> list[str]:
    needle = re.compile(rf"(?<![A-Za-z0-9_]){re.escape(pattern)}(?![A-Za-z0-9_])", re.IGNORECASE)
    return sorted(
        f"{repo}:{path}:{number}"
        for (repo, path), lines in text.files.items()
        for number, line in enumerate(lines, start=1)
        if needle.search(line)
    )


def baseline_answer(question: GoldenQuestion, text: CorpusText) -> list[str] | None:
    if question.tool == "what_calls":
        return _callers(question.args["symbol"], text)
    if question.tool == "what_does_it_call":
        return _callees(question.args["symbol"], text)
    if question.tool == "find_config_key":
        return _config(question.args["pattern"], text)
    return None
