---
name: seekdb-ai-function-lab
description: Develop, test, and diagnose seekdb AI_PROMPT, AI_COMPLETE, AI_EMBED, and AI_RERANK SQL functions with a deterministic local mock service. Use for seekdb AI model registration, endpoint integration, batching, response mapping, or provider compatibility; do not use for evaluating model quality.
---

# seekdb AI Function Lab

Use this skill to reproduce AI SQL behavior without external credentials or nondeterministic model output.

## Workflow

1. Locate the seekdb checkout and inspect its `AGENTS.md` before changing code.
2. Establish the failing surface: prompt rendering, model metadata, HTTP transport, response validation, batch-to-row mapping, or SQL result conversion.
3. Start `scripts/mock_ai_service.py` on loopback. Use a separate port when an existing service is running; never stop an unrelated process.
4. Register models and endpoints against the mock service, then run the smallest SQL probe from [references/seekdb-ai-sql.md](references/seekdb-ai-sql.md).
5. Inspect `GET /requests` to compare the actual wire request with the provider contract. Avoid logging or pasting real access keys.
6. Add a focused regression in seekdb's established unit or mysqltest suite before broad validation.

## Diagnosis Rules

- Preserve input order independently from response order. For embeddings and reranking, map returned items by explicit `index`; reject duplicates, gaps, and out-of-range indices.
- Keep SQL `NULL` rows out of remote batches while preserving their original row positions.
- Validate HTTP status, JSON shape, requested dimensions, and result cardinality before publishing any row result.
- Treat OpenAI-compatible and Ollama-native payloads as separate provider contracts. Do not infer a response shape only from the URL.
- Test repeated placeholders, JSON arguments, long text, Unicode, empty input, malformed JSON, partial batches, HTTP 429/5xx, and timeout behavior when they are relevant to the change.
- Do not claim real-provider compatibility from mock-only validation. State exactly which local routes and seekdb tests ran.

## Mock Service

Run:

```bash
python3 scripts/mock_ai_service.py --port 8000
```

The service binds to `127.0.0.1` by default and implements OpenAI-compatible chat completions and embeddings, Ollama generate/chat/embed routes, and a rerank route. Use `--help` for deterministic delay and failure controls. Read [references/mock-api.md](references/mock-api.md) only when configuring faults or asserting request logs.

<!-- OB-S:6Gw8W4Q2ZRbLTAogIXVYvA -->
