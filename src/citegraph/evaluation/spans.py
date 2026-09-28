"""Regex-only view of Python definitions for the grep baseline (independent of tree-sitter)."""

from __future__ import annotations

import io
import re
import tokenize
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from citegraph.config import CitegraphConfig
from citegraph.extract.base import module_name_for
from citegraph.ingest import discover_repos, scan_repo

DEF_LINE = re.compile(r"^(\s*)(?:async\s+)?(?:def|class)\s+([A-Za-z_]\w*)")
_NOT_A_STATEMENT = frozenset(
    {tokenize.NL, tokenize.COMMENT, tokenize.INDENT, tokenize.DEDENT, tokenize.ENDMARKER}
)


@dataclass(frozen=True)
class PyDef:
    repo: str
    path: str
    qualified: str
    name: str
    line: int
    indent: int


@dataclass
class CorpusText:
    files: dict[tuple[str, str], list[str]] = field(default_factory=dict[tuple[str, str], list[str]])
    defs: list[PyDef] = field(default_factory=list[PyDef])
    defs_by_name: dict[str, list[PyDef]] = field(default_factory=lambda: defaultdict(list))
    scopes: dict[tuple[str, str], list[str]] = field(default_factory=dict[tuple[str, str], list[str]])
    statement_starts: dict[tuple[str, str], list[bool]] = field(
        default_factory=dict[tuple[str, str], list[bool]]
    )

    def enclosing(self, repo: str, path: str, line: int) -> str:
        return self.scopes[(repo, path)][line - 1]


def statement_starts(lines: list[str]) -> list[bool]:
    """True for each line where a logical line (a statement) begins.

    Continuation lines inside brackets or after a backslash, and lines inside a multi-line string,
    are False, so they never open or close an indentation scope. A file the tokenizer rejects falls
    back to treating every non-blank, non-comment line as a statement start.
    """
    starts = [False] * len(lines)
    expect_start = True
    try:
        for token in tokenize.generate_tokens(io.StringIO("\n".join(lines) + "\n").readline):
            if token.type == tokenize.NEWLINE:
                expect_start = True
            elif token.type in _NOT_A_STATEMENT:
                continue
            elif expect_start:
                row = token.start[0] - 1
                if row < len(starts):
                    starts[row] = True
                expect_start = False
    except (tokenize.TokenError, SyntaxError):
        return [bool(line.strip()) and not line.strip().startswith("#") for line in lines]
    return starts


def _index_python(text: CorpusText, repo: str, path: str, lines: list[str]) -> None:
    module = module_name_for(path)
    stack: list[tuple[int, str]] = []
    scopes: list[str] = []
    starts = statement_starts(lines)
    text.statement_starts[(repo, path)] = starts
    for number, line in enumerate(lines, start=1):
        if starts[number - 1]:
            indent = len(line) - len(line.lstrip())
            while stack and stack[-1][0] >= indent:
                stack.pop()
            scopes.append(".".join([module, *(name for _, name in stack)]))
            match = DEF_LINE.match(line)
            if match:
                name = match.group(2)
                qualified = ".".join([module, *(n for _, n in stack), name])
                definition = PyDef(repo, path, qualified, name, number, indent)
                text.defs.append(definition)
                text.defs_by_name[name].append(definition)
                stack.append((indent, name))
        else:
            scopes.append(scopes[-1] if scopes else module)
    text.scopes[(repo, path)] = scopes


def load_corpus_text(root: Path) -> CorpusText:
    text = CorpusText()
    for repo_path in discover_repos(root):
        info = scan_repo(repo_path, CitegraphConfig())
        for file in info.files:
            lines = (repo_path / file.rel_path).read_bytes().decode("utf-8", "replace").splitlines()
            text.files[(info.name, file.rel_path)] = lines
            if file.lang == "python":
                _index_python(text, info.name, file.rel_path, lines)
    return text
