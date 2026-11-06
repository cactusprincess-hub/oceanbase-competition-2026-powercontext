"""Offline PDF pages as independently addressable captured Sources."""

# ruff: noqa: TRY003

from __future__ import annotations

import io
import math
import re
from collections import Counter
from hashlib import sha256
from itertools import pairwise
from typing import Protocol

from pydantic import BaseModel
from pypdf import PdfReader

from powercontext.builtin.artifacts.memory import MemoryCitation, MemoryHit
from powercontext.builtin.artifacts.search import analyze_fts_query, analyze_text
from powercontext.builtin.runtime.models import (
    GetMemoryEntryRequest,
    MemoryEntryRecord,
    MemorySearchPage,
    SearchMemoryRequest,
)
from powercontext.sources.definitions import AdapterSourceDefinition
from powercontext.sources.models import Source, SourceMaterialization
from powercontext.sources.projections import TEXT_EVIDENCE_PROJECTION_KEY, TextEvidence

_SHA256 = re.compile(r"[0-9a-f]{64}")
_SEARCH_STOPWORDS = frozenset({
    "a",
    "an",
    "and",
    "answer",
    "any",
    "are",
    "as",
    "at",
    "be",
    "by",
    "can",
    "calculate",
    "details",
    "directly",
    "explicitly",
    "for",
    "from",
    "give",
    "has",
    "have",
    "how",
    "if",
    "in",
    "is",
    "it",
    "only",
    "of",
    "on",
    "or",
    "question",
    "reported",
    "relying",
    "respond",
    "response",
    "shown",
    "state",
    "that",
    "the",
    "their",
    "then",
    "this",
    "to",
    "use",
    "using",
    "within",
    "was",
    "were",
    "what",
    "when",
    "which",
    "who",
    "with",
})


class PDFDocumentInput(BaseModel):
    document_id: str
    content: bytes


class PDFPageInput(BaseModel):
    document_id: str
    document_sha256: str
    page_number: int
    text: str


class PDFPageSource(Source):
    source_id: str
    document_id: str
    document_sha256: str
    page_number: int
    text: str


class _ScopedMemory(Protocol):
    async def search(self, request: SearchMemoryRequest, /) -> MemorySearchPage: ...

    async def get(self, request: GetMemoryEntryRequest, /) -> MemoryEntryRecord: ...


def parse_pdf_document(value: PDFDocumentInput) -> tuple[PDFPageInput, ...]:
    document_id = _document_id(value.document_id)
    document_hash = sha256(value.content).hexdigest()
    try:
        if not value.content:
            raise ValueError("PDF content must not be empty")  # noqa: TRY301
        reader = PdfReader(io.BytesIO(value.content), strict=False)
        if reader.is_encrypted and not reader.decrypt(""):
            raise ValueError("PDF requires a password")  # noqa: TRY301
        page_count = len(reader.pages)
        if page_count < 1:
            raise ValueError("PDF must contain at least one page")  # noqa: TRY301
        pages = tuple(
            PDFPageInput(
                document_id=document_id,
                document_sha256=document_hash,
                page_number=page_number,
                text=page.extract_text() or "",
            )
            for page_number, page in enumerate(reader.pages, start=1)
        )
    except Exception as error:
        raise ValueError("invalid PDF document") from error
    return pages


class PDFPageSourceAdapter:
    input_class = PDFPageInput
    name = "pdf-page"
    source_class = PDFPageSource

    async def resolve(self, value: PDFPageInput, /) -> PDFPageSource:
        document_id = _document_id(value.document_id)
        if _SHA256.fullmatch(value.document_sha256) is None:
            raise ValueError("document_sha256 must be a lowercase SHA-256 digest")
        if type(value.page_number) is not int or value.page_number < 1:
            raise ValueError("page_number must be a positive integer")
        source_id = _page_source_id(document_id, value.document_sha256, value.page_number)
        return PDFPageSource(
            name=source_id,
            source_id=source_id,
            materialization=SourceMaterialization.CAPTURED,
            document_id=document_id,
            document_sha256=value.document_sha256,
            page_number=value.page_number,
            text=value.text,
        )

    async def read(self, source: PDFPageSource, /) -> PDFPageInput:
        return PDFPageInput(
            document_id=source.document_id,
            document_sha256=source.document_sha256,
            page_number=source.page_number,
            text=source.text,
        )


class PDFPageTextEvidenceProjection:
    name = TEXT_EVIDENCE_PROJECTION_KEY.name
    version = TEXT_EVIDENCE_PROJECTION_KEY.version
    source_class: type[PDFPageSource] = PDFPageSource
    output_class: type[BaseModel] = TextEvidence

    def project(self, source: PDFPageSource, /) -> TextEvidence:
        return TextEvidence(
            source_type="pdf-page",
            source_id=source.source_id,
            content=f"{source.document_id}\n{source.text}",
            metadata={
                "document_id": source.document_id,
                "document_sha256": source.document_sha256,
                "page_number": source.page_number,
            },
        )


PDF_PAGE_SOURCE_DEFINITION = AdapterSourceDefinition(
    PDFPageSourceAdapter(), projections=(PDFPageTextEvidenceProjection(),)
)


async def search_pdf_pages(
    memory: _ScopedMemory,
    question: str,
    limit: int = 10,
) -> tuple[MemoryCitation, ...]:
    if not question.strip():
        raise ValueError("question must not be blank")
    if type(limit) is not int or limit < 1:
        raise ValueError("limit must be a positive integer")

    variants = _query_variants(question)
    hits: dict[tuple[str, int, str, str], MemoryHit] = {}
    rank_scores: Counter[tuple[str, int, str, str]] = Counter()
    best_scores: dict[tuple[str, int, str, str], float] = {}
    candidate_limit = max(100, limit * 4)
    for variant in variants:
        page = await memory.search(SearchMemoryRequest(query=variant, mode="fts", limit=candidate_limit))
        selectivity = 0.5 + math.log((candidate_limit + 1.0) / (len(page.hits) + 1.0))
        variant_terms = analyze_text(variant).split()
        variant_weight = selectivity * (1.25 if len(variant_terms) > 1 else 1.0)
        for rank, hit in enumerate(page.hits, start=1):
            identity = _hit_identity(hit)
            hits.setdefault(identity, hit)
            contribution = variant_weight / (10 + rank)
            rank_scores[identity] += contribution
            best_scores[identity] = max(best_scores.get(identity, 0.0), contribution)

    if not hits:
        return ()
    ordered = _rank_pdf_hits(question, tuple(hits.values()), rank_scores, best_scores)
    ordered = await _rank_neighboring_pages(memory, ordered, limit)
    return tuple(
        MemoryCitation(
            memory_ref=hit.memory_ref,
            entry_id=hit.entry_id,
            entry_version_id=hit.entry_version_id,
        )
        for hit in ordered[:limit]
    )


def _document_id(value: str) -> str:
    if not value or value != value.strip():
        raise ValueError("document_id must be non-empty and have no surrounding whitespace")
    return value


def _page_source_id(document_id: str, document_hash: str, page_number: int) -> str:
    document_key = sha256(document_id.encode("utf-8")).hexdigest()
    source_id = f"pdf-page:{document_key}:{document_hash}:{page_number}"
    if len(source_id) > 256:
        raise ValueError("PDF page source ID exceeds 256 characters")
    return source_id


def _query_variants(question: str) -> tuple[str, ...]:
    analyzed = analyze_fts_query(question).split()
    salient = tuple(dict.fromkeys(term for term in analyzed if term not in _SEARCH_STOPWORDS and len(term) > 1))
    variants = [question]
    variants.extend(" ".join(pair) for pair in pairwise(salient))
    variants.extend(form for term in salient for form in _term_forms(term))
    return tuple(dict.fromkeys(variant for variant in variants if variant.strip()))


def _rank_pdf_hits(
    question: str,
    hits: tuple[MemoryHit, ...],
    rank_scores: Counter[tuple[str, int, str, str]],
    best_scores: dict[tuple[str, int, str, str], float],
) -> tuple[MemoryHit, ...]:
    proper_names = {
        match.group().casefold()
        for match in re.finditer(r"\b[A-Z][A-Za-z]+\b", question)
        if match.start() > 0
    }
    query_terms = tuple(
        _term_stem(term)
        for term in analyze_fts_query(question).split()
        if term not in _SEARCH_STOPWORDS and term not in proper_names and len(term) > 1
    )
    hit_terms = [tuple(_term_stem(term) for term in analyze_text(hit.text).split()) for hit in hits]
    document_frequency = Counter(term for terms in hit_terms for term in set(terms))
    query_counts = Counter(query_terms)
    query_bigrams = tuple(dict.fromkeys(pairwise(query_terms)))
    average_length = sum(map(len, hit_terms)) / len(hit_terms)

    def score(index: int) -> tuple[float, bytes, bytes]:
        counts = Counter(hit_terms[index])
        overlap = sum(
            (math.log((len(hits) + 1) / (document_frequency[term] + 1)) + 1.0)
            * (1.0 + math.log(query_counts[term]))
            * _term_frequency_score(counts[term], len(hit_terms[index]), average_length)
            for term in query_counts.keys() & counts.keys()
        )
        coverage = len(query_counts.keys() & counts.keys()) / max(1, len(query_counts))
        adjacent = set(pairwise(hit_terms[index]))
        phrase_matches = sum(pair in adjacent for pair in query_bigrams)
        nearby_matches = sum(_terms_are_near(hit_terms[index], pair) for pair in query_bigrams)
        identity = _hit_identity(hits[index])
        total = (
            overlap
            + 4.0 * coverage
            + 0.8 * phrase_matches
            + 0.3 * nearby_matches
            + 6.0 * rank_scores[identity]
            + 8.0 * best_scores[identity]
        )
        return (-total, hits[index].entry_id.encode(), hits[index].entry_version_id.encode())

    return tuple(hits[index] for index in sorted(range(len(hits)), key=score))


def _term_stem(term: str) -> str:
    if term.isdigit() or len(term) <= 4:
        return term
    if term.endswith("ies") and len(term) > 5:
        term = f"{term[:-3]}y"
    elif term.endswith("s") and not term.endswith("ss") and len(term) > 5:
        term = term[:-1]
    if term.endswith("ly") and len(term) > 6:
        term = term[:-2]
    for suffix in ("ation", "ing", "ed"):
        if term.endswith(suffix) and len(term) - len(suffix) >= 4:
            term = term[: -len(suffix)]
            break
    if term.endswith("ate") and len(term) > 6:
        term = term[:-1]
    return term


def _term_forms(term: str) -> tuple[str, ...]:
    stem = _term_stem(term)
    forms = [term, stem]
    if term.endswith("ies") and len(term) > 5:
        singular = f"{term[:-3]}y"
        forms.extend((singular, f"{singular[:-1]}ic", f"{singular[:-1]}ical"))
    if stem.endswith("at"):
        forms.extend((f"{stem}e", f"{stem}es", f"{stem}ed", f"{stem}ing", f"{stem}ion", f"{stem}ions"))
    return tuple(dict.fromkeys(form for form in forms if len(form) > 1))


def _term_frequency_score(count: int, length: int, average_length: float) -> float:
    saturation = 1.5
    normalization = 0.75
    denominator = count + saturation * (1.0 - normalization + normalization * length / average_length)
    return count * (saturation + 1.0) / denominator


def _terms_are_near(terms: tuple[str, ...], pair: tuple[str, str], distance: int = 8) -> bool:
    left, right = pair
    left_positions = [index for index, term in enumerate(terms) if term == left]
    right_positions = [index for index, term in enumerate(terms) if term == right]
    return any(abs(left_index - right_index) <= distance for left_index in left_positions for right_index in right_positions)


async def _rank_neighboring_pages(
    memory: _ScopedMemory,
    hits: tuple[MemoryHit, ...],
    limit: int,
) -> tuple[MemoryHit, ...]:
    pool_size = min(len(hits), max(60, limit * 3))
    pool = hits[:pool_size]
    locations: dict[tuple[str, int, str, str], tuple[str, int]] = {}
    for hit in pool:
        citation = MemoryCitation(
            memory_ref=hit.memory_ref,
            entry_id=hit.entry_id,
            entry_version_id=hit.entry_version_id,
        )
        record = await memory.get(GetMemoryEntryRequest(citation=citation))
        location = _pdf_page_location(record)
        if location is not None:
            locations[_hit_identity(hit)] = location

    ranks = {_hit_identity(hit): rank for rank, hit in enumerate(pool, start=1)}

    def score(hit: MemoryHit) -> tuple[float, int]:
        identity = _hit_identity(hit)
        rank = ranks[identity]
        location = locations.get(identity)
        if location is None:
            return (float(rank), rank)
        document, page_number = location
        support = sum(
            1.0 / ((ranks[other_identity] + 5) * abs(other_page - page_number))
            for other_identity, (other_document, other_page) in locations.items()
            if other_identity != identity
            and other_document == document
            and 0 < abs(other_page - page_number) <= 2
        )
        return (rank - 60.0 * support, rank)

    reranked_pool = tuple(sorted(pool, key=score))
    return reranked_pool + hits[pool_size:]


def _pdf_page_location(record: MemoryEntryRecord) -> tuple[str, int] | None:
    for source in record.entry.sources:
        if source.source_type != "pdf-page":
            continue
        document_revision, separator, page = source.source_id.rpartition(":")
        if separator and page.isdigit():
            return document_revision, int(page)
    return None


def _hit_identity(hit: MemoryHit) -> tuple[str, int, str, str]:
    return (
        hit.memory_ref.artifact_id,
        hit.memory_ref.revision,
        hit.entry_id,
        hit.entry_version_id,
    )
