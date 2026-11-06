# Copyright (c) 2026 OceanBase.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0

"""Share in-flight embedding work and cache successful vectors."""

from __future__ import annotations

import asyncio
import math
from collections import OrderedDict
from collections.abc import Iterable
from typing import TypeAlias

from powercontext.builtin.inference.models import EmbeddingResult, EmbeddingVector, InferenceUsage
from powercontext.builtin.inference.protocols import EmbeddingModel, QueryEmbeddingModel

_Key: TypeAlias = tuple[str, str]
_Completed: TypeAlias = tuple[EmbeddingVector, int, InferenceUsage]


class CoalescingEmbeddingModel:
    """Batch unique pending texts while preserving each caller's order and usage."""

    def __init__(
        self,
        model: EmbeddingModel,
        *,
        max_batch_size: int = 64,
        coalesce_ms: float = 5.0,
        cache_size: int = 1024,
    ) -> None:
        if type(max_batch_size) is not int or max_batch_size <= 0:
            raise ValueError("max_batch_size must be a positive integer")  # noqa: TRY003
        if (
            isinstance(coalesce_ms, bool)
            or not isinstance(coalesce_ms, (int, float))
            or not math.isfinite(coalesce_ms)
            or coalesce_ms < 0
        ):
            raise ValueError("coalesce_ms must be nonnegative and finite")  # noqa: TRY003
        if type(cache_size) is not int or cache_size < 0:
            raise ValueError("cache_size must be a nonnegative integer")  # noqa: TRY003
        self._model = model
        self.profile = model.profile
        self._max_batch_size = max_batch_size
        self._coalesce_seconds = coalesce_ms / 1000
        self._cache_size = cache_size
        self._cache: OrderedDict[_Key, EmbeddingVector] = OrderedDict()
        self._flights: dict[_Key, asyncio.Future[_Completed]] = {}
        self._pending: OrderedDict[_Key, asyncio.Future[_Completed]] = OrderedDict()
        self._lock = asyncio.Lock()
        self._drain_task: asyncio.Task[None] | None = None
        self._batch_tasks: set[asyncio.Task[None]] = set()
        self._next_batch = 0

    async def embed(self, texts: tuple[str, ...], /) -> EmbeddingResult:
        return await self._embed("document", texts)

    async def embed_query(self, texts: tuple[str, ...], /) -> EmbeddingResult:
        if isinstance(self._model, QueryEmbeddingModel):
            return await self._embed("query", texts)
        return await self._embed("document", texts)

    async def _embed(self, kind: str, texts: tuple[str, ...]) -> EmbeddingResult:
        if not texts:
            return EmbeddingResult(vectors=())
        resolved: dict[_Key, EmbeddingVector] = {}
        waiting: dict[_Key, asyncio.Future[_Completed]] = {}
        async with self._lock:
            for text in texts:
                key = (kind, text)
                if key in resolved or key in waiting:
                    continue
                if key in self._cache:
                    resolved[key] = self._cache[key]
                    self._cache.move_to_end(key)
                    continue
                future = self._flights.get(key)
                if future is None:
                    future = asyncio.get_running_loop().create_future()
                    self._flights[key] = future
                    self._pending[key] = future
                waiting[key] = future
            if self._pending and self._drain_task is None:
                self._drain_task = asyncio.create_task(self._drain())
        outcomes = await asyncio.gather(*(asyncio.shield(future) for future in waiting.values()))
        used: dict[int, InferenceUsage] = {}
        for key, (vector, batch_id, usage) in zip(waiting, outcomes, strict=True):
            resolved[key] = vector
            used[batch_id] = usage
        return EmbeddingResult(
            vectors=tuple(resolved[(kind, text)] for text in texts),
            usage=InferenceUsage(
                requests=sum(item.requests for item in used.values()),
                input_tokens=_sum_optional(item.input_tokens for item in used.values()),
                output_tokens=_sum_optional(item.output_tokens for item in used.values()),
            ),
        )

    async def _drain(self) -> None:
        await asyncio.sleep(self._coalesce_seconds)
        while True:
            async with self._lock:
                if not self._pending:
                    self._drain_task = None
                    return
                first_kind = next(iter(self._pending))[0]
                batch: list[tuple[_Key, asyncio.Future[_Completed]]] = []
                for key in tuple(self._pending):
                    if key[0] == first_kind:
                        batch.append((key, self._pending.pop(key)))
                        if len(batch) == self._max_batch_size:
                            break
                self._next_batch += 1
                batch_id = self._next_batch
            task = asyncio.create_task(self._run_batch(batch, batch_id))
            self._batch_tasks.add(task)
            task.add_done_callback(self._batch_tasks.discard)

    async def _run_batch(self, batch: list[tuple[_Key, asyncio.Future[_Completed]]], batch_id: int) -> None:
        keys = tuple(key for key, _ in batch)
        texts = tuple(key[1] for key in keys)
        try:
            if keys[0][0] == "query":
                result = await self._model.embed_query(texts)  # type: ignore[attr-defined]
            else:
                result = await self._model.embed(texts)
            if len(result.vectors) != len(batch):
                raise ValueError("embedding vector count does not match input")  # noqa: TRY003, TRY301
        except Exception as error:
            async with self._lock:
                for key, future in batch:
                    self._flights.pop(key, None)
                    if not future.done():
                        future.set_exception(error)
            return
        async with self._lock:
            for (key, future), vector in zip(batch, result.vectors, strict=True):
                self._flights.pop(key, None)
                if self._cache_size:
                    self._cache[key] = vector
                    self._cache.move_to_end(key)
                    if len(self._cache) > self._cache_size:
                        self._cache.popitem(last=False)
                if not future.done():
                    future.set_result((vector, batch_id, result.usage))


def _sum_optional(values: Iterable[int | None]) -> int | None:
    found = False
    total = 0
    for value in values:
        if value is not None:
            found = True
            total += value
    return total if found else None
