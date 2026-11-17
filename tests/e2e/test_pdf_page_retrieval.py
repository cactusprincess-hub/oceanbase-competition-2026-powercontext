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

from __future__ import annotations

import asyncio
import io

import pytest
from pypdf import PdfWriter

from powercontext.builtin.artifacts.memory import MemoryEntryInput
from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.runtime import (
    BuiltinConfig,
    GetMemoryEntryRequest,
    SubmitSourceObservation,
    open_builtin_runtime,
)
from powercontext.builtin.scope import ScopeDraft
from powercontext.sources import SourceDefinitionRegistry, manifest_for_definition, project_source_for_transport
from powercontext.sources.pdf_pages import (
    PDF_PAGE_SOURCE_DEFINITION,
    PDFDocumentInput,
    PDFPageInput,
    parse_pdf_document,
    search_pdf_pages,
)
from powercontext.sources.projections import TEXT_EVIDENCE_PROJECTION_KEY


class _PagePipeline:
    async def extract(self, request, /):
        return tuple(
            MemoryEntryInput(
                kind="source-evidence",
                text=source.projection(TEXT_EVIDENCE_PROJECTION_KEY)["content"],
                sources=(source,),
            )
            for source in request.sources
        )


def test_nonblank_document_identity_is_preserved() -> None:
    writer = PdfWriter()
    writer.add_blank_page(width=100, height=200)
    content = io.BytesIO()
    writer.write(content)
    pages = parse_pdf_document(PDFDocumentInput(document_id=" report ", content=content.getvalue()))
    assert pages[0].document_id == " report "


def test_search_distinguishes_companies_after_restart(tmp_path) -> None:
    async def scenario():
        config = BuiltinConfig(database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'reports.db'}"))
        registry = SourceDefinitionRegistry((PDF_PAGE_SOURCE_DEFINITION,))
        async with open_builtin_runtime(config) as runtime:
            scope = await runtime.scopes.create(
                ScopeDraft(title="Reports", summary="Synthetic annual reports", idempotency_key="reports")
            )
            await runtime.ingestion.register(manifest_for_definition(PDF_PAGE_SOURCE_DEFINITION))
            for index in range(26):
                name = "ASTER_HOLDINGS_2025_10K" if index == 25 else f"BIRCH_{index}_2025_10K"
                text = (
                    "Consolidated Statements of Income. Net sales 2025 123."
                    if index == 25
                    else "Consolidated Statements of Income. Total revenue and net sales in FY2025 were 456."
                )
                source = await registry.resolve(
                    PDFPageInput(document_id=name, document_sha256=f"{index:064x}", page_number=1, text=text)
                )
                await runtime.ingestion.submit(
                    SubmitSourceObservation(
                        scope_id=scope.scope_id,
                        observation=project_source_for_transport(registry, source),
                    )
                )
        async with open_builtin_runtime(config, candidate_pipeline=_PagePipeline()) as runtime:
            memory = runtime.memory.for_scope(scope.scope_id)
            for _ in range(27):
                if not (await memory.flush()).processed:
                    break
        async with open_builtin_runtime(config) as runtime:
            memory = runtime.memory.for_scope(scope.scope_id)
            citations = await search_pdf_pages(memory, "What was Aster Holdings revenue for FY2025?", limit=1)
            assert len(citations) == 1
            record = await memory.get(GetMemoryEntryRequest(citation=citations[0]))
            assert record.entry.text.startswith("ASTER_HOLDINGS_2025_10K\n")
            assert record.entry.sources

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("question", "statement", "note"),
    [
        (
            "What was Aster's cash flow from operating activities in 2025 according to the cash flow statement?",
            "CONSOLIDATED STATEMENTS OF CASH FLOWS\n2025 2024\n"
            "Cash provided by operating activities 500 400.\n"
            "Cash used for investing activities (20) (30). Cash used for financing activities (100) (50).",
            "Refer to the statement of cash flows for cash flow from operating activities in 2025.",
        ),
        (
            "What was Aster's net income in 2025 according to its income statement?",
            "CONSOLIDATED STATEMENTS OF OPERATIONS\n2025 2024\n"
            "Net sales 1000 900. Cost of sales 600 550. Operating income 300 250. Net earnings 200 180.",
            "Refer to the statement of income for net income in 2025.",
        ),
    ],
)
def test_search_finds_plural_financial_statements_among_notes(tmp_path, question, statement, note) -> None:
    async def scenario():
        config = BuiltinConfig(database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'reports.db'}"))
        registry = SourceDefinitionRegistry((PDF_PAGE_SOURCE_DEFINITION,))
        async with open_builtin_runtime(config) as runtime:
            scope = await runtime.scopes.create(
                ScopeDraft(title="Statements and notes", summary="Synthetic annual report", idempotency_key="report")
            )
            await runtime.ingestion.register(manifest_for_definition(PDF_PAGE_SOURCE_DEFINITION))
            # Exact question wording in cross-references must not crowd the actual
            # accounting statement out of a page-retrieval result.
            for index in range(27):
                source = await registry.resolve(
                    PDFPageInput(
                        document_id="ASTER_2025_10K",
                        document_sha256="a" * 64,
                        page_number=index + 1,
                        text=statement if index == 26 else f"Note {index + 1}. {note}",
                    )
                )
                await runtime.ingestion.submit(
                    SubmitSourceObservation(
                        scope_id=scope.scope_id,
                        observation=project_source_for_transport(registry, source),
                    )
                )
        async with open_builtin_runtime(config, candidate_pipeline=_PagePipeline()) as runtime:
            memory = runtime.memory.for_scope(scope.scope_id)
            for _ in range(28):
                if not (await memory.flush()).processed:
                    break
        async with open_builtin_runtime(config) as runtime:
            memory = runtime.memory.for_scope(scope.scope_id)
            citations = await search_pdf_pages(memory, question, limit=1)
            texts = [(await memory.get(GetMemoryEntryRequest(citation=citation))).entry.text for citation in citations]
            assert texts == [f"ASTER_2025_10K\n{statement}"]

    asyncio.run(scenario())
