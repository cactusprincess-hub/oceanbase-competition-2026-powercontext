"""Persistent source dependency graph and native memory eligibility propagation."""

# ruff: noqa: TRY003

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, StrictStr, field_validator
from sqlalchemy import insert, select, update

from powercontext.artifacts import ArtifactRef, MemoryCitation
from powercontext.builtin.persistence.tables import SOURCE_LIFECYCLE_NODES_TABLE
from powercontext.builtin.runtime.models import GetMemoryEntryRequest
from powercontext.sources import SourceRef

if TYPE_CHECKING:
    from powercontext.builtin.runtime.application import BuiltinRuntime


class SourceLifecycleError(ValueError):
    """An invalid binding, event, or generation reference."""


class SourceLifecycleEvent(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    node_id: StrictStr
    revision: StrictInt = Field(gt=0)
    sequence: StrictInt = Field(gt=0)
    available: StrictBool
    reason: StrictStr

    @field_validator("node_id", "reason")
    @classmethod
    def nonempty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("must not be blank")
        return value


@dataclass
class _Node:
    node_id: str
    ordinal: int
    kind: Literal["source", "memory", "topic"]
    reference: SourceRef | MemoryCitation | ArtifactRef
    parents: tuple[str, ...]
    policy: str
    revision: int | None
    sequence: int | None
    event: SourceLifecycleEvent | None
    available: bool


def _encode(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _decode_node(row) -> _Node:
    kind = row["kind"]
    model = {"source": SourceRef, "memory": MemoryCitation, "topic": ArtifactRef}[kind]
    return _Node(
        node_id=row["node_id"],
        ordinal=row["ordinal"],
        kind=kind,
        reference=model.model_validate_json(row["reference"]),
        parents=tuple(json.loads(row["parents"])),
        policy=row["policy"],
        revision=row["revision"],
        sequence=row["sequence"],
        event=None if row["event"] is None else SourceLifecycleEvent.model_validate_json(row["event"]),
        available=bool(row["available"]),
    )


class ScopedSourceLifecycle:
    def __init__(self, runtime: BuiltinRuntime, scope_id: str) -> None:
        self._runtime = runtime
        self.scope_id = scope_id

    @property
    def _database(self):
        return self._runtime._provider.database

    async def _nodes(self) -> list[_Node]:
        async with self._database.transaction() as connection:
            rows = (
                await connection.execute(
                    select(SOURCE_LIFECYCLE_NODES_TABLE)
                    .where(SOURCE_LIFECYCLE_NODES_TABLE.c.scope_id == self.scope_id)
                    .order_by(SOURCE_LIFECYCLE_NODES_TABLE.c.ordinal)
                )
            ).mappings().all()
        return [_decode_node(row) for row in rows]

    async def register_source(self, node_id: str, reference: SourceRef, *, revision: int) -> None:
        if not isinstance(reference, SourceRef) or type(revision) is not int or revision <= 0:
            raise SourceLifecycleError("invalid source binding")
        await self._register(node_id, "source", reference, (), "all", revision)

    async def register_memory(
        self, node_id: str, reference: MemoryCitation, *, parents: tuple[str, ...], policy: str
    ) -> None:
        if not isinstance(reference, MemoryCitation):
            raise SourceLifecycleError("invalid memory citation")
        await self._register(node_id, "memory", reference, parents, policy, None)

    async def register_topic(
        self, node_id: str, reference: ArtifactRef, *, parents: tuple[str, ...], policy: str
    ) -> None:
        if not isinstance(reference, ArtifactRef) or reference.family != "topic-memory":
            raise SourceLifecycleError("invalid topic reference")
        await self._register(node_id, "topic", reference, parents, policy, None)

    async def _register(self, node_id, kind, reference, parents, policy, revision) -> None:
        if not isinstance(node_id, str) or not node_id.strip() or len(node_id) > 255:
            raise SourceLifecycleError("invalid node ID")
        if not isinstance(parents, tuple) or any(not isinstance(parent, str) for parent in parents):
            raise SourceLifecycleError("invalid parents")
        if kind != "source" and (not parents or len(set(parents)) != len(parents) or policy not in {"all", "any"}):
            raise SourceLifecycleError("invalid dependency binding")
        nodes = await self._nodes()
        by_id = {node.node_id: node for node in nodes}
        existing = by_id.get(node_id)
        if existing is not None:
            if (
                existing.kind != kind
                or existing.reference != reference
                or existing.parents != parents
                or existing.policy != policy
                or existing.revision != revision
            ):
                raise SourceLifecycleError("node ID is already bound")
            await self._reconcile(nodes)
            return
        if node_id in parents or any(parent not in by_id for parent in parents):
            raise SourceLifecycleError("parents must be registered before the child")
        await self._validate_reference(kind, reference)
        active = True if kind == "source" else _supported(parents, policy, by_id)
        node = _Node(node_id, len(nodes), kind, reference, parents, policy, revision, None, None, active)
        async with self._database.transaction() as connection:
            await connection.execute(
                insert(SOURCE_LIFECYCLE_NODES_TABLE).values(
                    scope_id=self.scope_id,
                    node_id=node_id,
                    ordinal=node.ordinal,
                    kind=kind,
                    reference=reference.model_dump_json().encode("utf-8"),
                    parents=_encode(parents),
                    policy=policy,
                    revision=revision,
                    sequence=None,
                    event=None,
                    available=active,
                )
            )
        await self._reconcile([*nodes, node])

    async def _validate_reference(self, kind, reference) -> None:
        try:
            if kind == "source":
                await self._runtime.records.for_scope(self.scope_id).get_source(
                    reference.source_type, reference.source_id
                )
            elif kind == "memory":
                await self._runtime.memory.for_scope(self.scope_id).get(GetMemoryEntryRequest(citation=reference))
            else:
                await self._runtime.records.for_scope(self.scope_id).get_artifact_revision(
                    reference.family, reference.artifact_id, reference.revision
                )
        except Exception as error:
            raise SourceLifecycleError("reference is not readable in this scope") from error

    async def apply(self, event: SourceLifecycleEvent) -> list[str]:
        if not isinstance(event, SourceLifecycleEvent):
            raise SourceLifecycleError("invalid lifecycle event")
        nodes = await self._nodes()
        by_id = {node.node_id: node for node in nodes}
        root = by_id.get(event.node_id)
        if root is None or root.kind != "source" or event.revision != root.revision:
            raise SourceLifecycleError("unknown source or lifecycle revision")
        if root.sequence is not None and (
            event.sequence < root.sequence or (event.sequence == root.sequence and event != root.event)
        ):
            raise SourceLifecycleError("event sequence conflicts with the stored event")
        if root.sequence is None or event.sequence > root.sequence:
            async with self._database.transaction() as connection:
                await connection.execute(
                    update(SOURCE_LIFECYCLE_NODES_TABLE)
                    .where(
                        SOURCE_LIFECYCLE_NODES_TABLE.c.scope_id == self.scope_id,
                        SOURCE_LIFECYCLE_NODES_TABLE.c.node_id == root.node_id,
                    )
                    .values(sequence=event.sequence, event=event.model_dump_json().encode("utf-8"))
                )
        root.event = event
        root.sequence = event.sequence
        root.available = event.available
        active = _compute(nodes)
        await self._reconcile(nodes)
        return sorted(active)

    async def _reconcile(self, nodes: list[_Node]) -> None:
        _compute(nodes)
        memory_nodes = [node for node in nodes if node.kind == "memory"]
        if memory_nodes:
            async with self._runtime._context(self.scope_id) as context, self._runtime._locked(self.scope_id):
                service = context.artifacts.memory
                current = await service.head(context.artifacts.memory_artifact_id)
                for target in ("inactive", "active"):
                    entries = {entry.entry_id: entry for entry in await service.entries(current)}
                    states = {entry.entry_id: entry.state for entry in current.content.manifest.entries}
                    selected = []
                    for node in memory_nodes:
                        citation = node.reference
                        entry = entries.get(citation.entry_id)
                        if entry is None or entry.entry_version_id != citation.entry_version_id:
                            raise SourceLifecycleError("bound memory version changed")
                        if (not node.available if target == "inactive" else node.available) and states[citation.entry_id] != target:
                            selected.append(entry)
                    if selected:
                        current = (
                            await service.forget(current, entries=selected, reason="source lifecycle")
                            if target == "inactive"
                            else await service.reactivate(current, entries=selected, reason="source lifecycle")
                        )
        async with self._database.transaction() as connection:
            for node in nodes:
                await connection.execute(
                    update(SOURCE_LIFECYCLE_NODES_TABLE)
                    .where(
                        SOURCE_LIFECYCLE_NODES_TABLE.c.scope_id == self.scope_id,
                        SOURCE_LIFECYCLE_NODES_TABLE.c.node_id == node.node_id,
                    )
                    .values(available=node.available)
                )

    async def resolve(self, node_id: str, *, purpose: str):
        if purpose not in {"generation", "audit"}:
            raise SourceLifecycleError("unknown resolution purpose")
        by_id = {node.node_id: node for node in await self._nodes()}
        node = by_id.get(node_id)
        if node is None:
            raise SourceLifecycleError("unknown node")
        if purpose == "generation" and not node.available:
            raise SourceLifecycleError("node has no active support")
        return node.reference


def _supported(parents: tuple[str, ...], policy: str, by_id: dict[str, _Node]) -> bool:
    states = [by_id[parent].available for parent in parents]
    return all(states) if policy == "all" else any(states)


def _compute(nodes: list[_Node]) -> set[str]:
    by_id: dict[str, _Node] = {}
    for node in nodes:
        if node.kind == "source":
            if node.event is not None:
                node.available = node.event.available
        else:
            node.available = _supported(node.parents, node.policy, by_id)
        by_id[node.node_id] = node
    return {node.node_id for node in nodes if node.available}


class SourceLifecycleApplication:
    def __init__(self, runtime: BuiltinRuntime) -> None:
        self._runtime = runtime

    def for_scope(self, scope_id: str) -> ScopedSourceLifecycle:
        return ScopedSourceLifecycle(self._runtime, scope_id)
