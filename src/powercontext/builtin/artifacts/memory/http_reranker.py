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

"""HTTP scoring adapter for identity-preserving Memory reranking."""

from __future__ import annotations

import math

import httpx

from powercontext.builtin.artifacts.memory.models import MemoryHit
from powercontext.builtin.artifacts.memory.reranking import MemoryRerankDecision, _validate_bounds
from powercontext.builtin.inference import InferenceUsage


class HTTPMemoryReranker:
    """Score candidates in bounded HTTP batches and select globally."""

    def __init__(
        self,
        endpoint: str,
        *,
        client: httpx.AsyncClient,
        batch_size: int = 32,
        policy_id: str = "http-cross-encoder-v1",
    ) -> None:
        if type(batch_size) is not int or batch_size < 1:
            raise ValueError("batch_size must be a positive integer")  # noqa: TRY003
        self.endpoint = endpoint
        self.client = client
        self.batch_size = batch_size
        self.policy_id = policy_id

    async def rerank(
        self,
        query: str,
        candidates: tuple[MemoryHit, ...],
        limit: int,
        /,
    ) -> MemoryRerankDecision:
        """Return original one-based ranks ordered by service score."""

        _validate_bounds(len(candidates), limit)
        scores: dict[int, float] = {}
        requests = 0
        for start in range(0, len(candidates), self.batch_size):
            batch = candidates[start : start + self.batch_size]
            expected_ranks = set(range(start + 1, start + len(batch) + 1))
            response = await self.client.post(
                self.endpoint,
                json={
                    "query": query,
                    "candidates": [
                        {"rank": start + offset + 1, "text": candidate.text} for offset, candidate in enumerate(batch)
                    ],
                },
            )
            requests += 1
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, dict) or set(payload) != {"scores"} or not isinstance(payload["scores"], list):
                raise ValueError("invalid reranker response")  # noqa: TRY003
            batch_scores: dict[int, float] = {}
            for item in payload["scores"]:
                if not isinstance(item, dict) or set(item) != {"rank", "score"}:
                    raise ValueError("invalid reranker score")  # noqa: TRY003
                rank = item["rank"]
                score = item["score"]
                if (
                    type(rank) is not int
                    or rank not in expected_ranks
                    or rank in batch_scores
                    or type(score) not in (int, float)
                ):
                    raise ValueError("invalid reranker score")  # noqa: TRY003
                try:
                    numeric_score = float(score)
                except OverflowError as exc:
                    raise ValueError("invalid reranker score") from exc  # noqa: TRY003
                if not math.isfinite(numeric_score):
                    raise ValueError("invalid reranker score")  # noqa: TRY003
                batch_scores[rank] = numeric_score
            if set(batch_scores) != expected_ranks:
                raise ValueError("incomplete reranker scores")  # noqa: TRY003
            scores.update(batch_scores)

        selected_ranks = tuple(sorted(scores, key=lambda rank: (-scores[rank], rank))[:limit])
        return MemoryRerankDecision(selected_ranks=selected_ranks, usage=InferenceUsage(requests=requests))
