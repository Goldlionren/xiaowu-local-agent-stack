# Memory data flow

## Retrieval

~~~mermaid
sequenceDiagram
    participant U as User
    participant H as Hermes
    participant B as Memory bridge
    participant M as Memory Service
    participant HS as Hindsight
    participant R as Reranker
    participant DB as PostgreSQL
    U->>H: message
    H->>B: pre-LLM hook
    B->>M: POST /v1/retrieve
    M->>M: infer current or historical intent
    M->>HS: recall and observation lookup
    HS->>DB: vector, text, graph and lineage queries
    HS->>R: rerank candidates
    R-->>HS: ranked candidates
    HS-->>M: memories and observations
    M->>M: authority and chronology resolution
    M-->>B: bounded background context
    B-->>H: context injection
    H-->>U: response
~~~

Current mode hydrates authoritative observations and overlays the deterministic current state. Historical mode follows observation lineage to source memories and resolves chronology. Failures degrade to empty context rather than injecting stale claims.

## Retention

~~~mermaid
sequenceDiagram
    participant H as Hermes event
    participant B as Memory bridge
    participant M as Memory Service
    participant G as Memory Gate LLM
    participant L as Ledger
    participant HS as Hindsight
    participant DB as PostgreSQL
    H->>B: completed conversation event
    B->>M: process turn
    M->>M: candidate extraction and secret pre-filter
    M->>G: durable-memory classification
    G-->>M: retain or reject and canonical form
    M->>L: source identity and idempotency check
    L->>DB: pending ledger record
    M->>HS: retain accepted memory
    HS->>DB: document, memory and vector persistence
    HS->>HS: asynchronous consolidation
    M->>L: delivered or retryable result
~~~

The bank boundary is fixed by service configuration and is not selectable in the public HTTP retrieval request. This supports one agent per memory domain.
