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

"""Single-statement native FTS and vector candidate retrieval for seekdb."""

from __future__ import annotations

from pyobvector import VECTOR
from sqlalchemy import Table, bindparam, text
from sqlalchemy.ext.asyncio import AsyncConnection

from powercontext.artifacts import ArtifactRef
from powercontext.builtin.artifacts.memory import (
    CapabilityNotSupportedError,
    EmbeddingProfile,
    MemoryCapabilities,
    MemoryProjection,
    MemorySearchChannels,
    MemorySearchRequest,
)
from powercontext.builtin.artifacts.memory.canonical import validate_embedding
from powercontext.builtin.persistence.memory_index import memory_channel_hits
from powercontext.builtin.persistence.oceanbase.memory_index import (
    OceanBaseMemoryFTSIndex,
    OceanBaseMemoryVectorIndex,
)
from powercontext.builtin.persistence.tags import memory_tag_parameters, memory_tag_sql

_HYBRID_SEARCH_SQL = """
WITH fts_candidates AS (
    SELECT
        f.memory_artifact_id,
        f.head_revision,
        f.entry_id,
        f.entry_version_id,
        v.text,
        MATCH(f.searchable_text) AGAINST (:analyzed_query) AS fts_score
    FROM pc_memory_entry_heads AS f
    JOIN pc_memory_entry_versions AS v
      ON v.scope_id = f.scope_id
     AND v.memory_artifact_id = f.memory_artifact_id
     AND v.entry_version_id = f.entry_version_id
    WHERE f.scope_id = :scope_id
      AND f.memory_artifact_id IN :memory_artifact_ids
      AND MATCH(f.searchable_text) AGAINST (:analyzed_query)
{fts_tag_filter}
    ORDER BY fts_score DESC, f.memory_artifact_id, f.entry_id, f.entry_version_id
    LIMIT :candidate_limit
), vector_neighbors AS (
    SELECT
        m.scope_id,
        m.memory_artifact_id,
        m.head_revision,
        m.entry_id,
        m.entry_version_id,
        l2_distance(m.embedding, :query_vector) AS distance
    FROM pc_memory_vector_entries AS m
    WHERE m.scope_id = :scope_id
      AND m.memory_artifact_id IN :memory_artifact_ids
{vector_tag_filter}
    ORDER BY l2_distance(m.embedding, :query_vector) APPROXIMATE
    LIMIT :candidate_limit
), vector_candidates AS (
    SELECT
        n.memory_artifact_id,
        n.head_revision,
        n.entry_id,
        n.entry_version_id,
        v.text,
        n.distance
    FROM vector_neighbors AS n
    JOIN pc_memory_entry_heads AS h
      ON h.scope_id = n.scope_id
     AND h.memory_artifact_id = n.memory_artifact_id
     AND h.entry_id = n.entry_id
     AND h.entry_version_id = n.entry_version_id
    JOIN pc_memory_entry_versions AS v
      ON v.scope_id = n.scope_id
     AND v.memory_artifact_id = n.memory_artifact_id
     AND v.entry_version_id = n.entry_version_id
)
SELECT
    'fts' AS channel,
    memory_artifact_id,
    head_revision,
    entry_id,
    entry_version_id,
    text,
    CAST(NULL AS DOUBLE) AS distance,
    fts_score
FROM fts_candidates
UNION ALL
SELECT
    'vector' AS channel,
    memory_artifact_id,
    head_revision,
    entry_id,
    entry_version_id,
    text,
    distance,
    CAST(NULL AS DOUBLE) AS fts_score
FROM vector_candidates
ORDER BY channel, fts_score DESC, distance, memory_artifact_id, entry_id, entry_version_id
"""


class SeekDBMemoryHybridIndex:
    """Own seekdb Memory indexes and retrieve both candidate channels in one statement."""

    def __init__(self, profile: EmbeddingProfile) -> None:
        self.profile = profile
        self._fts = OceanBaseMemoryFTSIndex()
        self._vector = OceanBaseMemoryVectorIndex(profile)
        self.capabilities = MemoryCapabilities(
            fts=True,
            vector=True,
            hybrid=True,
            tag_filter=True,
            embedding_profile=profile,
        )
        self.tables: tuple[Table, ...] = self._vector.tables
        self._search_sql = _hybrid_statement(profile, tagged=False)
        self._tagged_search_sql = _hybrid_statement(profile, tagged=True)

    async def initialize(self, connection: AsyncConnection, /) -> None:
        await self._fts.initialize(connection)
        await self._vector.initialize(connection)

    async def replace(
        self,
        connection: AsyncConnection,
        scope_id: str,
        memory_ref: ArtifactRef,
        projections: tuple[MemoryProjection, ...],
        /,
    ) -> None:
        await self._vector.replace(connection, scope_id, memory_ref, projections)

    async def delete(
        self,
        connection: AsyncConnection,
        scope_id: str,
        memory_ref: ArtifactRef,
        entry_ids: tuple[str, ...],
        /,
    ) -> None:
        await self._vector.delete(connection, scope_id, memory_ref, entry_ids)

    async def upsert(
        self,
        connection: AsyncConnection,
        scope_id: str,
        memory_ref: ArtifactRef,
        projections: tuple[MemoryProjection, ...],
        /,
    ) -> None:
        await self._vector.upsert(connection, scope_id, memory_ref, projections)

    async def search(
        self,
        connection: AsyncConnection,
        scope_id: str,
        request: MemorySearchRequest,
        /,
    ) -> MemorySearchChannels:
        if request.mode == "fts":
            return await self._fts.search(connection, scope_id, request)
        if request.mode == "vector":
            return await self._vector.search(connection, scope_id, request)
        if request.embedding_profile != self.profile or request.query_vector is None:
            raise CapabilityNotSupportedError("embedding-profile")
        if not await self.vector_complete(connection, scope_id, request.memories, self.profile):
            raise CapabilityNotSupportedError("vector")

        rows = (
            await connection.execute(
                self._search_sql if request.tag_filter is None else self._tagged_search_sql,
                {
                    "scope_id": scope_id,
                    "memory_artifact_ids": tuple(memory.artifact_id for memory in request.memories),
                    "analyzed_query": request.analyzed_query,
                    "query_vector": validate_embedding(request.query_vector, dimension=self.profile.dimension),
                    "candidate_limit": request.candidate_limit,
                    **memory_tag_parameters(request.tag_filter),
                },
            )
        ).mappings()
        ranked = tuple(rows)
        return MemorySearchChannels(
            fts=memory_channel_hits((row for row in ranked if row["channel"] == "fts"), request.memories),
            vector=memory_channel_hits((row for row in ranked if row["channel"] == "vector"), request.memories),
        )

    async def vector_complete(
        self,
        connection: AsyncConnection,
        scope_id: str,
        memories: tuple[ArtifactRef, ...],
        profile: EmbeddingProfile,
        /,
    ) -> bool:
        return await self._vector.vector_complete(connection, scope_id, memories, profile)

    async def hydrate(
        self,
        connection: AsyncConnection,
        scope_id: str,
        projections: tuple[MemoryProjection, ...],
        /,
    ) -> tuple[MemoryProjection, ...]:
        return await self._vector.hydrate(connection, scope_id, projections)


def _hybrid_statement(profile: EmbeddingProfile, *, tagged: bool):
    tag_filter = memory_tag_sql("f") if tagged else ""
    vector_tag_filter = memory_tag_sql("m") if tagged else ""
    return text(
        _HYBRID_SEARCH_SQL.format(fts_tag_filter=tag_filter, vector_tag_filter=vector_tag_filter)
    ).bindparams(
        bindparam("memory_artifact_ids", expanding=True),
        bindparam("query_vector", type_=VECTOR(profile.dimension)),
        *(
            (
                bindparam("tag_keys", expanding=True),
                bindparam("tag_hashes", expanding=True),
            )
            if tagged
            else ()
        ),
    )
