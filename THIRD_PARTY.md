# Third-party components

No third-party source is vendored in this repository.

| Component | Audited source or image | License evidence on host | Treatment |
|---|---|---|---|
| Hermes Agent | NousResearch/hermes-agent, commit 26350357d76e4508c8df9304a3374bdc5a6f6220 | Local MIT LICENSE, Nous Research copyright | Integration documented; source not copied |
| llama.cpp | ggml-org/llama.cpp, commit 4d9176092d00586775af140581bb0b558ddc4389 | Local MIT LICENSE, ggml authors copyright | Integration documented; source not copied |
| Hindsight | ghcr.io/vectorize-io/hindsight-api:0.8.6 | OCI image label says MIT; live OpenAPI metadata says Apache-2.0 | No source copied because evidence conflicts |
| VectorChord suite | tensorchord/vchord-suite:pg18-latest | Image identity recorded; license not independently frozen in this audit | Image reference only |
| PostgreSQL and extensions | PostgreSQL 18.3, vchord 1.1.1, vector 0.8.2, vchord_bm25 0.3.0 | Runtime version evidence only | Integration documented |
| Xiaowu Memory Service | Local production directory | No LICENSE file found | Source excluded |
| xiaowu-avatar | Local production skill | No LICENSE file found | Source and assets excluded |
| xiaowu-visual | Local production plugin | No LICENSE file found | Source and assets excluded |
| xiaowu-model-router | Local production plugin | No LICENSE file found | Source excluded |

Verify upstream licenses again before vendoring any implementation.
