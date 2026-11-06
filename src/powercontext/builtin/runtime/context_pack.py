# Copyright (c) 2026 OceanBase.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0

"""Portable, prefix-based transfer of prepared context."""

# ruff: noqa: TRY003

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any

PACK_SCHEMA = "powercontext.context-pack.v1"
DELTA_SCHEMA = "powercontext.context-pack-delta.v1"
_DIGEST = re.compile(r"sha256:[0-9a-f]{64}\Z")


class ContextPackError(ValueError):
    """The pack, delta, or its encoded representation is invalid."""


def _json_bytes(value: dict[str, Any]) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")


def _read_json(data: bytes) -> dict[str, Any]:
    if not isinstance(data, bytes):
        raise ContextPackError("wire data must be bytes")

    def object_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ContextPackError("duplicate wire field")
            result[key] = value
        return result

    try:
        decoded = json.loads(data.decode("utf-8"), object_pairs_hook=object_pairs)
    except (UnicodeError, ValueError, TypeError) as error:
        raise ContextPackError("invalid JSON wire data") from error
    if not isinstance(decoded, dict):
        raise ContextPackError("wire data must be a JSON object")
    return decoded


def _validate_digest(value: object) -> str:
    if not isinstance(value, str) or _DIGEST.fullmatch(value) is None:
        raise ContextPackError("invalid digest")
    return value


def _validate_items(items: object) -> tuple[bytes, ...]:
    if not isinstance(items, tuple) or any(not isinstance(item, bytes) for item in items):
        raise ContextPackError("items must be a bytes tuple")
    for item in items:
        try:
            item.decode("utf-8")
        except UnicodeDecodeError as error:
            raise ContextPackError("item is not UTF-8") from error
    return items


def _encode_items(items: tuple[bytes, ...]) -> list[str]:
    return [base64.b64encode(item).decode("ascii") for item in items]


def _decode_items(value: object) -> tuple[bytes, ...]:
    if not isinstance(value, list):
        raise ContextPackError("encoded items must be a list")
    items = []
    for encoded in value:
        if not isinstance(encoded, str):
            raise ContextPackError("encoded item must be a string")
        try:
            item = base64.b64decode(encoded, validate=True)
        except (ValueError, UnicodeError, binascii.Error) as error:
            raise ContextPackError("invalid Base64 item") from error
        if base64.b64encode(item).decode("ascii") != encoded:
            raise ContextPackError("non-canonical Base64 item")
        items.append(item)
    return _validate_items(tuple(items))


def _pack_digest(items: tuple[bytes, ...]) -> str:
    unsigned = {"schema": PACK_SCHEMA, "items": _encode_items(items)}
    return "sha256:" + hashlib.sha256(_json_bytes(unsigned)).hexdigest()


@dataclass(frozen=True, slots=True)
class ContextPack:
    items: tuple[bytes, ...]
    digest: str
    byte_count: int

    @classmethod
    def build(cls, items: tuple[bytes, ...]) -> ContextPack:
        items = _validate_items(items)
        digest = _pack_digest(items)
        payload = {"schema": PACK_SCHEMA, "items": _encode_items(items), "digest": digest, "byte_count": 0}
        byte_count = len(_json_bytes(payload))
        while True:
            payload["byte_count"] = byte_count
            actual = len(_json_bytes(payload))
            if actual == byte_count:
                return cls(items, digest, byte_count)
            byte_count = actual

    @classmethod
    def from_content(cls, content: str | None, *, chunk_bytes: int = 512) -> ContextPack:
        if content is not None and not isinstance(content, str):
            raise ContextPackError("content must be text or None")
        if type(chunk_bytes) is not int or chunk_bytes <= 0:
            raise ContextPackError("chunk_bytes must be a positive integer")
        if not content:
            return cls.build(())
        chunks: list[bytes] = []
        for line in content.splitlines(keepends=True):
            current = bytearray()
            for char in line:
                try:
                    encoded = char.encode("utf-8")
                except UnicodeEncodeError as error:
                    raise ContextPackError("content is not valid UTF-8") from error
                if len(encoded) > chunk_bytes:
                    raise ContextPackError("chunk limit cannot hold a UTF-8 codepoint")
                if len(current) + len(encoded) > chunk_bytes:
                    chunks.append(bytes(current))
                    current.clear()
                current.extend(encoded)
            if current:
                chunks.append(bytes(current))
        return cls.build(tuple(chunks))

    def to_bytes(self) -> bytes:
        return _json_bytes({
            "schema": PACK_SCHEMA,
            "items": _encode_items(self.items),
            "digest": self.digest,
            "byte_count": self.byte_count,
        })

    @classmethod
    def from_bytes(cls, data: bytes) -> ContextPack:
        payload = _read_json(data)
        if set(payload) != {"schema", "items", "digest", "byte_count"} or payload["schema"] != PACK_SCHEMA:
            raise ContextPackError("invalid pack fields or schema")
        items = _decode_items(payload["items"])
        digest = _validate_digest(payload["digest"])
        byte_count = payload["byte_count"]
        if type(byte_count) is not int or byte_count < 0:
            raise ContextPackError("invalid byte count")
        expected = cls.build(items)
        if digest != expected.digest or byte_count != expected.byte_count:
            raise ContextPackError("pack digest or byte count mismatch")
        return expected


@dataclass(frozen=True, slots=True)
class ContextPackDelta:
    base_digest: str
    target_digest: str
    prefix_items: int
    appended: tuple[bytes, ...]

    @classmethod
    def between(cls, base: ContextPack, target: ContextPack) -> ContextPackDelta:
        prefix = 0
        for old, new in zip(base.items, target.items, strict=False):
            if old != new:
                break
            prefix += 1
        return cls(base.digest, target.digest, prefix, target.items[prefix:])

    @property
    def byte_count(self) -> int:
        return len(self.to_bytes())

    def to_bytes(self) -> bytes:
        return _json_bytes({
            "schema": DELTA_SCHEMA,
            "base_digest": self.base_digest,
            "target_digest": self.target_digest,
            "prefix_items": self.prefix_items,
            "appended": _encode_items(self.appended),
        })

    @classmethod
    def from_bytes(cls, data: bytes) -> ContextPackDelta:
        payload = _read_json(data)
        if set(payload) != {"schema", "base_digest", "target_digest", "prefix_items", "appended"}:
            raise ContextPackError("invalid delta fields")
        if payload["schema"] != DELTA_SCHEMA:
            raise ContextPackError("invalid delta schema")
        base_digest = _validate_digest(payload["base_digest"])
        target_digest = _validate_digest(payload["target_digest"])
        prefix = payload["prefix_items"]
        if type(prefix) is not int or prefix < 0:
            raise ContextPackError("invalid prefix length")
        return cls(base_digest, target_digest, prefix, _decode_items(payload["appended"]))


def apply_context_pack_delta(base: ContextPack, delta: ContextPackDelta) -> ContextPack:
    if delta.base_digest != base.digest:
        raise ContextPackError("delta base digest mismatch")
    if type(delta.prefix_items) is not int or delta.prefix_items < 0 or delta.prefix_items > len(base.items):
        raise ContextPackError("invalid prefix length")
    target = ContextPack.build(base.items[: delta.prefix_items] + _validate_items(delta.appended))
    if target.digest != delta.target_digest:
        raise ContextPackError("delta target digest mismatch")
    return target
