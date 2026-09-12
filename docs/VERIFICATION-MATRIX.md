# Runtime verification matrix

Status vocabulary is limited to PASS, FAIL, PARTIAL, NOT TESTED, and DISABLED BY DESIGN.

| Component | Status | Evidence through 2026-09-12 |
|---|---|---|
| Ubuntu OS | PASS | os-release and hostnamectl: Ubuntu 24.04.4 LTS |
| Intel GPU | PASS | sycl-ls and xpu-smi found B60, A770M, and Iris Xe; device state normal |
| oneAPI / SYCL | PASS | Level Zero devices enumerated; llama-server reports IntelLLVM 2025.3.2 |
| Main LLM | PASS | listener; /v1/models yinyue2; chat HTTP 200 at about 74 tok/s |
| Hermes CLI | PASS | hermes --version returned v0.20.6 |
| Hermes Gateway | PASS | user unit enabled and active with live PID |
| Faster-Qwen3-TTS API | PASS | OpenAI-compatible speech API generated audio successfully |
| Qwen3-TTS voice clone | PASS | private xiaowu voice profile generated validated speech |
| TTS Opus output | PASS | ffprobe: opus, ogg, mono, 48 kHz decoder clock |
| TTS WAV regression | PASS | pcm_s16le, 24 kHz, mono, wav |
| TTS MP3 regression | PASS | mp3, 24 kHz, mono |
| TTS PCM regression | PASS | raw pcm_s16le parsed at 24 kHz mono |
| Hermes OpenAI TTS provider | PASS | tts.openai base_url/model/voice path audited and runtime-tested |
| Hermes Telegram TTS format | PASS | Telegram .ogg path maps to response_format=opus |
| Hermes /voice off | PASS | text-only mode verified |
| Hermes /voice on | PASS | voice_only command path present and validated |
| Hermes /voice tts | PASS | all-replies TTS mode successfully delivered Xiaowu voice |
| Telegram TTS delivery | PASS | Xiaowu Ogg/Opus voice message delivered successfully |
| Docker | PASS | Engine 29.4.1 and Compose 5.1.3; containers running |
| PostgreSQL | PASS | healthy container; SQL reports PostgreSQL 18.3 |
| Hindsight | PASS | 0.8.6 /health returned healthy and database connected |
| Memory LLM | PASS | /v1/models returned yinyue2-hindsight |
| Embedding | PASS | live request returned 2560 dimensions |
| Reranker | PARTIAL | live config selects local CPU model; no isolated reranker probe |
| Memory Service | PASS | /health reports 0.5.1-phase8f-fast2 healthy |
| Memory Gate | PASS | Memory Service health reports gate healthy on 10002 |
| retrieve API | PASS | nonce query HTTP 200, current, resolved, 1.013 s |
| xiaowu-avatar | PASS | revision 34 transaction completed, committed, and delivered |
| xiaowu-visual | PASS | enabled 1.4.0; management commands and generation tool verified |
| xiaowu-model-router | PASS | deferred tool bridge and output guards verified; 48 isolated tests pass |
| xiaowu_avatar_generate | PASS | latest transaction submit_count 1 and delivered |
| Comfy MCP | PASS | 3060, 4080s, and 5090 connected; each discovered 39 tools |
| 3060 target | PASS | registry active/verified and live connection succeeded |
| 4080s avatar target | PASS | workflow, reference, 22 node classes, and 6 model dependencies verified |
| 5090 avatar target | PASS | workflow, reference, 22 node classes, and 6 model dependencies verified |
| Image generation | PASS | 5090 job completed once under its original prompt binding |
| Image retrieval | PASS | existing completed 5090 output was resumed, fetched, and committed |
| Telegram delivery | PASS | recovered transaction recorded successful text and media delivery |
| qwen-worker mirror | PARTIAL | profile stopped and two active-source files differ |
| comfytv_4080s | FAIL | live HTTP MCP test timed out after 30 s |
| Published Avatar regression tests | PASS | 13 skill tests and 48 router tests pass in isolation |
