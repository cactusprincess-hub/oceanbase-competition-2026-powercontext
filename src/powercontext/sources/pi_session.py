# Copyright (c) 2026 OceanBase.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Capture the visible event history of one Pi session branch."""

# ruff: noqa: TRY003

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from pydantic import BaseModel, JsonValue

from powercontext.sources.connectors import (
    ConnectorRunCompletion,
    ConnectorRunSession,
    ConnectorRunStatus,
)
from powercontext.sources.definitions import AdapterSourceDefinition
from powercontext.sources.models import Source, SourceMaterialization
from powercontext.sources.projections import TEXT_EVIDENCE_PROJECTION_KEY, TextEvidence


class PiSessionInput(BaseModel):
    content: bytes
    leaf_id: str | None = None


class PiEventInput(BaseModel):
    session_id: str
    event_id: str
    parent_id: str | None
    event_type: str
    role: str
    content: str
    record: dict[str, JsonValue]


class PiEventSource(Source):
    source_id: str
    event: PiEventInput


def _source_id(session_id: str, event_id: str) -> str:
    identity = json.dumps([session_id, event_id], ensure_ascii=False, separators=(",", ":"))
    return "pi:" + hashlib.sha256(identity.encode()).hexdigest()


def _visible_content(value: object) -> str:
    if isinstance(value, str):
        return value
    if not isinstance(value, list):
        return ""
    parts: list[str] = []
    for block in value:
        if not isinstance(block, dict):
            continue
        if block.get("type") == "text" and isinstance(block.get("text"), str):
            parts.append(block["text"])
        elif block.get("type") == "toolCall":
            name, call_id = block.get("name"), block.get("id")
            if isinstance(name, str) and isinstance(call_id, str):
                arguments = json.dumps(
                    block.get("arguments", {}), ensure_ascii=False, sort_keys=True, separators=(",", ":")
                )
                parts.append(f"[tool_call {name} {call_id}] {arguments}")
    return "\n".join(parts)


def _event_body(record: dict[str, JsonValue]) -> tuple[str, str] | None:
    kind = record.get("type")
    if kind == "message":
        message = record.get("message")
        if not isinstance(message, dict) or not isinstance(message.get("role"), str):
            return None
        role = message["role"]
        if role == "toolResult":
            name, call_id = message.get("toolName"), message.get("toolCallId")
            body = _visible_content(message.get("content"))
            if isinstance(name, str) and isinstance(call_id, str) and body:
                return role, f"[tool_result {name} {call_id}] {body}"
            return None
        if role == "bashExecution":
            command, output = message.get("command"), message.get("output")
            if isinstance(command, str) and command:
                return role, f"$ {command}" + (f"\n{output}" if isinstance(output, str) and output else "")
            return None
        body = _visible_content(message.get("content"))
        return (role, body) if body else None
    if kind in {"compaction", "branch_summary"}:
        summary = record.get("summary")
        return (kind, summary) if isinstance(summary, str) and summary else None
    if kind == "custom_message":
        body = _visible_content(record.get("content"))
        return ("custom", body) if body else None
    return None


def _load_archive(content: bytes) -> tuple[str, dict[str, dict[str, JsonValue]]]:
    try:
        lines = content.decode("utf-8-sig").splitlines()
        records = [json.loads(line) for line in lines if line.strip()]
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("invalid Pi session JSONL") from exc
    if not records or not isinstance(records[0], dict):
        raise ValueError("missing Pi session header")
    header = records[0]
    if header.get("type") != "session" or type(header.get("version")) is not int or header["version"] != 3:
        raise ValueError("invalid Pi session header")
    session_id = header.get("id")
    if not isinstance(session_id, str) or not session_id:
        raise ValueError("invalid Pi session ID")

    nodes: dict[str, dict[str, JsonValue]] = {}
    for record in records[1:]:
        if not isinstance(record, dict):
            raise ValueError("invalid Pi event")  # noqa: TRY004
        event_id, parent_id = record.get("id"), record.get("parentId")
        if not isinstance(event_id, str) or not event_id or event_id in nodes:
            raise ValueError("invalid or duplicate Pi event ID")
        if parent_id is not None and (not isinstance(parent_id, str) or parent_id not in nodes):
            raise ValueError("Pi event parent must precede its child")
        nodes[event_id] = record
    return session_id, nodes


def parse_pi_session(value: PiSessionInput) -> tuple[PiEventInput, ...]:
    """Validate a full v3 archive before returning the selected ancestor path."""

    session_id, nodes = _load_archive(value.content)

    leaf_id = value.leaf_id if value.leaf_id is not None else next(reversed(nodes), None)
    if leaf_id not in nodes:
        raise ValueError("Pi session leaf does not exist")
    path: list[dict[str, JsonValue]] = []
    current: str | None = leaf_id
    while current is not None:
        record = nodes[current]
        path.append(record)
        current = record["parentId"]
    events: list[PiEventInput] = []
    for record in reversed(path):
        visible = _event_body(record)
        if visible is None:
            continue
        role, body = visible
        events.append(
            PiEventInput(
                session_id=session_id,
                event_id=record["id"],
                parent_id=record["parentId"],
                event_type=record.get("type", ""),
                role=role,
                content=f"{role}: {body}",
                record=record,
            )
        )
    return tuple(events)


class PiEventSourceAdapter:
    input_class = PiEventInput
    name = "pi-event"
    source_class = PiEventSource

    async def resolve(self, value: PiEventInput, /) -> PiEventSource:
        source_id = _source_id(value.session_id, value.event_id)
        return PiEventSource(
            name=source_id, source_id=source_id, materialization=SourceMaterialization.CAPTURED, event=value
        )

    async def read(self, source: PiEventSource, /) -> PiEventInput:
        return source.event


class PiEventTextEvidenceProjection:
    name = TEXT_EVIDENCE_PROJECTION_KEY.name
    version = TEXT_EVIDENCE_PROJECTION_KEY.version
    source_class = PiEventSource
    output_class = TextEvidence

    def project(self, source: PiEventSource, /) -> TextEvidence:
        event = source.event
        return TextEvidence(
            source_type="pi-event",
            source_id=source.source_id,
            content=event.content,
            metadata={
                "session_id": event.session_id,
                "event_id": event.event_id,
                "parent_id": event.parent_id,
                "event_type": event.event_type,
                "role": event.role,
            },
        )


PI_EVENT_SOURCE_DEFINITION = AdapterSourceDefinition(
    PiEventSourceAdapter(), projections=(PiEventTextEvidenceProjection(),)
)


class PiSessionConnector:
    name = "pi-session-history"
    version = "1"
    source_definitions = frozenset({"pi-event"})

    def __init__(self, path: str | Path, leaf_id: str | None = None) -> None:
        self.path = Path(path)
        self.leaf_id = leaf_id

    async def run(self, session: ConnectorRunSession, /) -> ConnectorRunCompletion:
        content = self.path.read_bytes()
        events = parse_pi_session(PiSessionInput(content=content, leaf_id=self.leaf_id))
        session_id, nodes = _load_archive(content)
        checkpoint = session.checkpoint
        if checkpoint is None:
            imported: dict[str, str] = {}
        elif isinstance(checkpoint, dict) and isinstance(checkpoint.get("events"), dict):
            imported = checkpoint["events"].copy()
        else:
            raise ValueError("invalid Pi connector checkpoint")
        for record in nodes.values():
            source_id = _source_id(session_id, record["id"])
            if source_id in imported:
                checksum = hashlib.sha256(
                    json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
                ).hexdigest()
                if imported[source_id] != checksum:
                    raise ValueError("previously imported Pi event changed")
        pending: list[tuple[PiEventInput, str, str]] = []
        for event in events:
            source_id = _source_id(event.session_id, event.event_id)
            checksum = hashlib.sha256(
                json.dumps(event.record, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
            ).hexdigest()
            if source_id in imported:
                if imported[source_id] != checksum:
                    raise ValueError("previously imported Pi event changed")
            else:
                pending.append((event, source_id, checksum))
        for event, source_id, checksum in pending:
            await session.submit(source_id, "pi-event", event)
            imported[source_id] = checksum
        return ConnectorRunCompletion(status=ConnectorRunStatus.COMPLETE, checkpoint={"events": imported})
