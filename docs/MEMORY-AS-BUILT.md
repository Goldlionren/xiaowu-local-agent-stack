# Memory As-Built

## Components

| Component | Observed implementation | Status evidence |
|---|---|---|
| Xiaowu Memory Service | 0.5.1-phase8f-fast2 on loopback 8890 | /health returned healthy |
| Production bank | xiaowu-main | Memory Service health |
| Hindsight API | container image 0.8.6 on loopback 8888 | /health returned database connected |
| PostgreSQL | 18.3, containerized, loopback 5436 | container health and SQL version |
| Vector/text extensions | vchord 1.1.1, vector 0.8.2, vchord_bm25 0.3.0, pg_tokenizer 0.1.1, pg_trgm 1.6 | pg_extension metadata |
| Memory LLM | yinyue2-hindsight on 10002 | /v1/models |
| Embedding | yinyue2-embedding on 10001, 2560 dimensions | live embedding request |
| Reranker | local BAAI/bge-reranker-v2-m3, CPU forced, batch 8, max candidates 32 | live Compose configuration; direct isolated execution not tested |
| Memory Gate | memory LLM endpoint on 10002 | Memory Service health reported healthy |
| Retrieval | POST /v1/retrieve | nonce query returned HTTP 200, mode current, resolved true in 1.013 s |

The control schema contains ingest ledger, source event registry, turn candidate manifest, and turn registry tables. Hindsight owns banks, documents, chunks, memory units, observations, links, entities, operations, directives, webhooks, and related metadata.

The audit queried schema names, extension versions, and statistical row counts only. It did not select memory rows or export database data.

## API surface

The Memory Service exposes:

- GET /health
- GET /v1/info
- POST /v1/gate/evaluate
- POST /v1/memories/retain
- POST /v1/memories/process
- POST /v1/turns/process
- POST /v1/retrieve

Its OpenAPI and interactive documentation endpoints are disabled.

## Persistence and control

Accepted memory candidates are normalized into a versioned packet, associated with a source event and run identity, and written through an idempotent ledger before Hindsight delivery. Current and historical retrieval use separate resolution logic. Hindsight performs consolidation and stores vector, graph, observation, and lineage data in PostgreSQL.

The production source directory has no local Git metadata and no LICENSE file. Its implementation is not included here.
