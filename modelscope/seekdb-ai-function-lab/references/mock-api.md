# Deterministic Mock API

## Routes

| Route | Contract |
| --- | --- |
| `POST /v1/chat/completions` | OpenAI-style `choices[0].message.content` |
| `POST /v1/embeddings` | OpenAI-style indexed `data[]` embeddings |
| `POST /v1/rerank` | Indexed `results[]` with deterministic lexical scores |
| `POST /api/generate` | Ollama-style `response` |
| `POST /api/chat` | Ollama-style `message.content` |
| `POST /api/embed` | Ollama-style `embeddings[]` |
| `POST /api/embeddings` | Legacy Ollama-style single `embedding` |
| `GET /health` | Liveness and request count |
| `GET /requests` | Sanitized in-memory request log |
| `POST /reset` | Clear request log and failure counter |

The request log omits authorization and cookie headers. It is bounded by `--max-log-entries` and exists only in memory.

## Fault Controls

- `--delay-ms N`: delay every POST response by `N` milliseconds.
- `--fail-first N`: return `--failure-status` for the first `N` inference requests.
- `--failure-status CODE`: choose the injected HTTP status, normally `429` or `500`.
- Query parameter `?status=CODE`: fail one route without restarting the server.
- Query parameter `?delay_ms=N`: add a route-specific response delay.
- Query parameter `?reverse=1`: reverse indexed embedding or rerank results to expose positional mapping bugs.

Keep the server bound to loopback unless another isolated environment explicitly requires remote access.
