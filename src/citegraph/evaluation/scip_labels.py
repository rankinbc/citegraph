"""Bootstrap caller labels from a scip-python index. A labeling aid only; every candidate is hand-reviewed."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections import defaultdict
from collections.abc import Iterator
from pathlib import Path
from typing import Any, cast

import yaml

from citegraph.home import index_path
from citegraph.store import Store

DESCRIPTOR = re.compile(r"`([^`]+)`/|([A-Za-z_]\w*)(?:#|\(\)\.|\.)")
DEFINITION, IMPORT = 1, 2

# scip.proto field numbers, verified against
# https://raw.githubusercontent.com/sourcegraph/scip/main/scip.proto (fetched 2026-09-28):
# `message Index` (line 26): documents = 2.
# `message Document` (line 76): relative_path = 1, occurrences = 2.
# `message Occurrence` (line 692): range = 1 (repeated int32, deprecated but still emitted by
# scip-python; packed by default, unpacked accepted too), symbol = 2, symbol_roles = 3.
_INDEX_DOCUMENTS = 2
_DOCUMENT_RELATIVE_PATH = 1
_DOCUMENT_OCCURRENCES = 2
_OCCURRENCE_RANGE = 1
_OCCURRENCE_SYMBOL = 2
_OCCURRENCE_SYMBOL_ROLES = 3

_WIRE_VARINT = 0
_WIRE_FIXED64 = 1
_WIRE_LENGTH_DELIMITED = 2
_WIRE_FIXED32 = 5


def parse_scip_symbol(symbol: str) -> str | None:
    if symbol.startswith("local "):
        return None
    parts = symbol.split(" ", 4)
    if len(parts) < 5:
        return None
    names: list[str] = []
    for namespace, name in DESCRIPTOR.findall(parts[4]):
        names.append(namespace or name)
    return ".".join(names) if names else None


def _field(obj: dict[str, Any], camel: str, snake: str) -> Any:
    return obj.get(camel, obj.get(snake))


def _enclosing(store: Store, repo: str, path: str, line: int) -> str | None:
    row = store.conn.execute(
        "SELECT s.qualified_name FROM symbols s JOIN files f ON f.id = s.file_id JOIN repos r ON r.id = f.repo_id "
        "WHERE r.name = ? AND f.path = ? AND s.line_start <= ? AND s.line_end >= ? "
        "ORDER BY (s.line_end - s.line_start) ASC LIMIT 1",
        (repo, path, line, line),
    ).fetchone()
    return None if row is None else str(row["qualified_name"])


def label_callers(index: dict[str, object], store: Store, repo: str) -> dict[str, set[str]]:
    callers: dict[str, set[str]] = defaultdict(set)
    for document in cast(list[dict[str, Any]], index.get("documents", [])):
        path = str(_field(document, "relativePath", "relative_path"))
        for occurrence in cast(list[dict[str, Any]], document.get("occurrences", [])):
            roles = int(_field(occurrence, "symbolRoles", "symbol_roles") or 0)
            if roles & (DEFINITION | IMPORT):
                continue
            target = parse_scip_symbol(str(occurrence.get("symbol", "")))
            if target is None:
                continue
            line = int(occurrence["range"][0]) + 1
            enclosing = _enclosing(store, repo, path, line)
            if enclosing is not None and enclosing != target:
                callers[f"{repo}:{target}"].add(f"{repo}:{enclosing}")
    return dict(callers)


def write_candidates(callers: dict[str, set[str]], out: Path, per_tool: int = 25) -> int:
    eligible = sorted(t for t, c in callers.items() if 1 <= len(c) <= 15)
    chosen = sorted(eligible, key=lambda t: hashlib.sha256(t.encode()).hexdigest())[:per_tool]
    questions = [
        {
            "id": "py-callers-" + t.split(":", 1)[1].replace(".", "-").lower(),
            "tool": "what_calls",
            "args": {"symbol": t},
            "expected": sorted(callers[t]),
            "note": "scip-bootstrapped; review pending",
        }
        for t in chosen
    ]
    out.write_text(yaml.safe_dump(questions, sort_keys=False), encoding="utf-8")
    return len(questions)


def _read_varint(data: bytes, pos: int) -> tuple[int, int]:
    """Decode a protobuf base-128 varint starting at `pos`. Returns (value, next_pos)."""
    result = 0
    shift = 0
    start = pos
    length = len(data)
    while True:
        if pos >= length:
            raise ValueError(f"truncated varint at offset {start}")
        byte = data[pos]
        pos += 1
        result |= (byte & 0x7F) << shift
        if not byte & 0x80:
            return result, pos
        shift += 7
        if shift > 63:
            raise ValueError(f"varint too long at offset {start}")


def _skip_fixed(data: bytes, pos: int, size: int) -> int:
    end = pos + size
    if end > len(data):
        raise ValueError(f"truncated field at offset {pos}")
    return end


def _iter_fields(data: bytes) -> Iterator[tuple[int, int, int | bytes]]:
    """Walk top-level protobuf fields in `data`, yielding (field_number, wire_type, value).

    Varint fields yield an int; length-delimited fields yield the raw sub-buffer. Fixed32/fixed64
    fields are skipped without yielding (nothing in scip.proto's Index/Document/Occurrence needs
    them). Unsupported wire types (protobuf's deprecated groups, 3 and 4) raise ValueError.
    """
    pos = 0
    length = len(data)
    while pos < length:
        tag, pos = _read_varint(data, pos)
        field_no, wire_type = tag >> 3, tag & 0x7
        if wire_type == _WIRE_VARINT:
            value, pos = _read_varint(data, pos)
            yield field_no, wire_type, value
        elif wire_type == _WIRE_LENGTH_DELIMITED:
            size, pos = _read_varint(data, pos)
            end = pos + size
            if end > length:
                raise ValueError(f"truncated length-delimited field at offset {pos}")
            yield field_no, wire_type, data[pos:end]
            pos = end
        elif wire_type == _WIRE_FIXED64:
            pos = _skip_fixed(data, pos, 8)
        elif wire_type == _WIRE_FIXED32:
            pos = _skip_fixed(data, pos, 4)
        else:
            raise ValueError(f"unsupported wire type {wire_type} at offset {pos}")


def _unpack_varints(data: bytes) -> list[int]:
    values: list[int] = []
    pos = 0
    while pos < len(data):
        value, pos = _read_varint(data, pos)
        values.append(value)
    return values


def _parse_occurrence(data: bytes) -> dict[str, Any]:
    range_values: list[int] = []
    symbol = ""
    roles = 0
    for field_no, wire_type, value in _iter_fields(data):
        if field_no == _OCCURRENCE_RANGE and wire_type == _WIRE_LENGTH_DELIMITED:
            range_values = _unpack_varints(cast(bytes, value))
        elif field_no == _OCCURRENCE_RANGE and wire_type == _WIRE_VARINT:
            range_values.append(cast(int, value))  # unpacked repeated encoding
        elif field_no == _OCCURRENCE_SYMBOL and wire_type == _WIRE_LENGTH_DELIMITED:
            symbol = cast(bytes, value).decode("utf-8")
        elif field_no == _OCCURRENCE_SYMBOL_ROLES and wire_type == _WIRE_VARINT:
            roles = cast(int, value)
    return {"range": range_values, "symbol": symbol, "symbolRoles": roles}


def _parse_document(data: bytes) -> dict[str, Any]:
    relative_path = ""
    occurrences: list[dict[str, Any]] = []
    for field_no, wire_type, value in _iter_fields(data):
        if field_no == _DOCUMENT_RELATIVE_PATH and wire_type == _WIRE_LENGTH_DELIMITED:
            relative_path = cast(bytes, value).decode("utf-8")
        elif field_no == _DOCUMENT_OCCURRENCES and wire_type == _WIRE_LENGTH_DELIMITED:
            occurrences.append(_parse_occurrence(cast(bytes, value)))
    return {"relativePath": relative_path, "occurrences": occurrences}


def load_scip_index(path: Path) -> dict[str, object]:
    """Read a binary `index.scip` (scip.proto's `Index` message) without a protobuf dependency.

    Returns the same shape `label_callers` accepts from `scip print --json`:
    `{"documents": [{"relativePath": str, "occurrences": [{"range": [int, ...], "symbol": str,
    "symbolRoles": int}, ...]}, ...]}`. Raises ValueError on a truncated or malformed buffer.
    """
    data = path.read_bytes()
    documents: list[dict[str, Any]] = []
    for field_no, wire_type, value in _iter_fields(data):
        if field_no == _INDEX_DOCUMENTS and wire_type == _WIRE_LENGTH_DELIMITED:
            documents.append(_parse_document(cast(bytes, value)))
    return {"documents": documents}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--scip-json", type=Path, help="scip print --json output")
    source.add_argument("--scip", type=Path, help="binary index.scip, read directly (no scip CLI needed)")
    parser.add_argument("--repo", required=True, help="repo name as indexed")
    parser.add_argument("--root", type=Path, required=True, help="corpus root that was indexed")
    parser.add_argument("--index-name", default="eval-corpus")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    store = Store.open_read_only(index_path(args.root, args.index_name))
    index = (
        load_scip_index(args.scip) if args.scip else json.loads(args.scip_json.read_text(encoding="utf-8"))
    )
    print(
        f"wrote {write_candidates(label_callers(index, store, args.repo), args.out)} candidates to {args.out}"
    )


if __name__ == "__main__":
    main()
