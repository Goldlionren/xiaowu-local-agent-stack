# Runtime verification matrix

Status vocabulary is limited to PASS, FAIL, PARTIAL, NOT TESTED, and DISABLED BY DESIGN.

| Component | Status | Evidence from 2026-09-11 |
|---|---|---|
| Ubuntu OS | PASS | os-release and hostnamectl: Ubuntu 24.04.4 LTS |
| Intel GPU | PASS | sycl-ls and xpu-smi found B60, A770M, and Iris Xe; device state normal |
| oneAPI / SYCL | PASS | Level Zero devices enumerated; llama-server reports IntelLLVM 2025.3.2 |
| Main LLM | PASS | listener; /v1/models yinyue2; chat HTTP 200 at about 74 tok/s |
| Hermes CLI | PASS | hermes --version returned v0.20.6 |
| Hermes Gateway | PASS | user unit enabled and active with live PID |
| Docker | PASS | Engine 29.4.1 and Compose 5.1.3; containers running |
| PostgreSQL | PASS | healthy container; SQL reports PostgreSQL 18.3 |
| Hindsight | PASS | 0.8.6 /health returned healthy and database connected |
| Memory LLM | PASS | /v1/models returned yinyue2-hindsight |
| Embedding | PASS | live request returned 2560 dimensions |
| Reranker | PARTIAL | live config selects local CPU model; no isolated reranker probe |
| Memory Service | PASS | /health reports 0.5.1-phase8f-fast2 healthy |
| Memory Gate | PASS | Memory Service health reports gate healthy on 10002 |
| retrieve API | PASS | nonce query HTTP 200, current, resolved, 1.013 s |
| xiaowu-avatar | PASS | revision 20 transaction completed and committed |
| xiaowu-visual | PASS | enabled 1.3.0; transaction used published tool path |
| xiaowu-model-router | PARTIAL | enabled with xiaowu route; isolated tests unavailable |
| xiaowu_avatar_generate | PASS | latest transaction submit_count 1 and delivered |
| Comfy MCP | PASS | comfy_3060 connected and discovered 39 tools |
| 3060 target | PASS | registry active/verified and live connection succeeded |
| 5090 avatar target | DISABLED BY DESIGN | registry enabled false and fail-closed |
| Image generation | PASS | latest transaction generation and job completed |
| Image retrieval | PASS | committed PNG validated as 1328 by 1776 and hashed |
| Telegram delivery | PASS | latest transaction recorded attempted and successful text/media delivery |
| qwen-worker mirror | PARTIAL | profile stopped and two active-source files differ |
| comfy_4080s | PASS | live MCP test connected and found 39 tools |
| comfytv_4080s | FAIL | live HTTP MCP test timed out after 30 s |
| Production pytest suites | NOT TESTED | pytest not installed in gateway venv or PATH |
