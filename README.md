# Xiaowu Local Agent Stack

Xiaowu is a single-user, local-first, long-term-memory agent reference architecture. This repository documents a production deployment audited on 2026-09-11 and provides sanitized examples for rebuilding the same component boundaries without publishing private state, credentials, model files, or third-party source.

The design assumes one agent per memory domain. A memory bank belongs to one user and one agent identity; it is not a shared public knowledge bank.

~~~mermaid
flowchart TD
    U[Single user] --> H[Hermes gateway and agent]
    H --> L[llama.cpp main LLM<br/>Intel SYCL]
    H --> MB[Xiaowu memory bridge]
    MB --> MS[Xiaowu Memory Service]
    MS --> G[Memory Gate LLM]
    MS --> HS[Hindsight API]
    HS --> E[Embedding service]
    HS --> R[CPU reranker]
    HS --> PG[(PostgreSQL<br/>VectorChord and pgvector)]
    H --> S[Skills and plugins]
    S --> T[xiaowu_avatar_generate]
    T --> MCP[MCP over SSH]
    MCP --> C[Remote ComfyUI worker]
    C --> T
    T --> D[Image retrieval and delivery]
~~~

The audited stack uses Ubuntu, Hermes Agent, llama.cpp, Intel Arc GPUs, Hindsight, PostgreSQL with vector retrieval, a policy and ledger based memory service, Hermes skills/plugins, MCP, a remote ComfyUI image worker, and an OpenAI-compatible Faster-Qwen3-TTS voice path with Telegram Ogg/Opus delivery.

Start with [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md), [docs/AS-BUILT.md](docs/AS-BUILT.md), [docs/AS-BUILT-TTS.md](docs/AS-BUILT-TTS.md), [docs/TTS-HERMES-OPENAI-OPUS-SOP.md](docs/TTS-HERMES-OPENAI-OPUS-SOP.md), and [docs/VERIFICATION-MATRIX.md](docs/VERIFICATION-MATRIX.md). Deployment examples are intentionally parameterized. They are reference material, not unattended production installers.

No model weights, memory records, chat transcripts, state databases, generated images, transactions, credentials, personal identifiers, SSH material, or private network addresses are included.

## Public scope

The repository contains:

- evidence-backed, sanitized As-Built documentation;
- systemd, Docker Compose, Hermes, LLM, memory, and MCP examples;
- read-only health and secret scanning helpers;
- migration notes for active Xiaowu names and retained Yinyue compatibility names.

Custom production source was not copied because no local license file was found for those directories. The integration is documented instead.

## Quick validation

~~~bash
./scripts/healthcheck/check-local-stack.sh
./scripts/audit/secret-scan.sh .
~~~

Adjust values in config/env.example and the component env examples before using any deployment template.

## License

The original material in this repository is MIT licensed. Third-party projects retain their own licenses; see [THIRD_PARTY.md](THIRD_PARTY.md).
