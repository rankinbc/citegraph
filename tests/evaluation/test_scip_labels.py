import json
from pathlib import Path

import pytest
import yaml

from citegraph.evaluation.scip_labels import (
    label_callers,
    load_scip_index,
    parse_scip_symbol,
    write_candidates,
)
from tests.fixtures.sample_corpus import SAMPLE
from tests.helpers import FIXTURES, build_store

PO = "shop:shop.orders.OrderService.place_order"

CHARGE_SYMBOL = "scip-python python shop 0.1 `shop.payments`/charge()."
VALIDATE_SYMBOL = "scip-python python shop 0.1 `shop.orders`/OrderService#validate()."


def test_parse_scip_symbol() -> None:
    assert (
        parse_scip_symbol("scip-python python shop 0.1 `shop.payments`/charge().") == "shop.payments.charge"
    )
    assert parse_scip_symbol("scip-python python shop 0.1 `shop.orders`/OrderService#validate().") == (
        "shop.orders.OrderService.validate"
    )
    assert parse_scip_symbol("local 3") is None


def test_label_callers_uses_enclosing_symbol_and_skips_imports(tmp_path: Path) -> None:
    store = build_store(tmp_path / "i.db", SAMPLE)
    index = json.loads((FIXTURES / "scip_sample.json").read_text(encoding="utf-8"))
    assert label_callers(index, store, "shop") == {
        "shop:shop.payments.charge": {PO},
        "shop:shop.orders.OrderService.validate": {PO},
    }


def test_write_candidates(tmp_path: Path) -> None:
    out = tmp_path / "cands.yaml"
    n = write_candidates({"shop:shop.payments.charge": {PO}}, out)
    assert n == 1
    [question] = yaml.safe_load(out.read_text(encoding="utf-8"))
    assert question["tool"] == "what_calls"
    assert question["expected"] == [PO]
    assert question["note"] == "scip-bootstrapped; review pending"


# --- Tiny protobuf wire-format encoder, used only to build test fixtures for load_scip_index.
# Mirrors scip.proto's Index/Document/Occurrence messages (see scip_labels.py for the field
# numbers, verified against the upstream proto).


def _varint(n: int) -> bytes:
    out = bytearray()
    while True:
        byte = n & 0x7F
        n >>= 7
        if n:
            out.append(byte | 0x80)
        else:
            out.append(byte)
            return bytes(out)


def _tag(field_no: int, wire_type: int) -> bytes:
    return _varint((field_no << 3) | wire_type)


def _length_delimited(field_no: int, payload: bytes) -> bytes:
    return _tag(field_no, 2) + _varint(len(payload)) + payload


def _string_field(field_no: int, value: str) -> bytes:
    return _length_delimited(field_no, value.encode("utf-8"))


def _varint_field(field_no: int, value: int) -> bytes:
    return _tag(field_no, 0) + _varint(value)


def _packed_range_field(values: list[int]) -> bytes:
    payload = b"".join(_varint(v) for v in values)
    return _length_delimited(1, payload)  # Occurrence.range = 1


def _occurrence_bytes(range_: list[int], symbol: str, roles: int, extra: bytes = b"") -> bytes:
    return (
        _packed_range_field(range_)
        + _string_field(2, symbol)  # Occurrence.symbol = 2
        + _varint_field(3, roles)  # Occurrence.symbol_roles = 3
        + extra
    )


def _document_bytes(path: str, occurrences: list[bytes]) -> bytes:
    body = _string_field(1, path)  # Document.relative_path = 1
    for occurrence in occurrences:
        body += _length_delimited(2, occurrence)  # Document.occurrences = 2
    return body


def _index_bytes(documents: list[bytes]) -> bytes:
    body = b""
    for document in documents:
        body += _length_delimited(2, document)  # Index.documents = 2
    return body


def test_load_scip_index_reads_binary_protobuf(tmp_path: Path) -> None:
    definition = _occurrence_bytes([3, 4, 10], CHARGE_SYMBOL, 1)  # definition (bit 1)
    imported = _occurrence_bytes([1, 25, 31], CHARGE_SYMBOL, 2)  # import (bit 2)
    # An unknown field (Occurrence.syntax_kind = 5, arbitrary varint) appended after the known
    # fields: must be skipped rather than corrupting the parse.
    charge_ref = _occurrence_bytes([9, 8, 14], CHARGE_SYMBOL, 0, extra=_varint_field(5, 99))
    validate_ref = _occurrence_bytes([7, 13, 21], VALIDATE_SYMBOL, 0)
    document = _document_bytes("src/shop/orders.py", [definition, imported, charge_ref, validate_ref])
    scip_path = tmp_path / "index.scip"
    scip_path.write_bytes(_index_bytes([document]))

    index = load_scip_index(scip_path)

    assert index == {
        "documents": [
            {
                "relativePath": "src/shop/orders.py",
                "occurrences": [
                    {"range": [3, 4, 10], "symbol": CHARGE_SYMBOL, "symbolRoles": 1},
                    {"range": [1, 25, 31], "symbol": CHARGE_SYMBOL, "symbolRoles": 2},
                    {"range": [9, 8, 14], "symbol": CHARGE_SYMBOL, "symbolRoles": 0},
                    {"range": [7, 13, 21], "symbol": VALIDATE_SYMBOL, "symbolRoles": 0},
                ],
            }
        ]
    }

    store = build_store(tmp_path / "i2.db", SAMPLE)
    assert label_callers(index, store, "shop") == {
        "shop:shop.payments.charge": {PO},
        "shop:shop.orders.OrderService.validate": {PO},
    }


def test_load_scip_index_truncated_varint_raises(tmp_path: Path) -> None:
    # A tag byte with the continuation bit set and no following byte: an incomplete varint.
    scip_path = tmp_path / "truncated.scip"
    scip_path.write_bytes(bytes([0x08, 0x96]))
    with pytest.raises(ValueError):
        load_scip_index(scip_path)
