# Port map

Private addresses and unrelated desktop services are omitted.

| Port | Protocol | Service | Bind | Purpose | Exposure | Verification |
|---:|---|---|---|---|---|---|
| 10000 | HTTP/TCP | main llama-server | 127.0.0.1 | Hermes main LLM | private loopback | listener, /v1/models, chat HTTP 200 |
| 10001 | HTTP/TCP | embedding llama-server | 127.0.0.1 | Hindsight embeddings | private loopback | listener, /v1/models, 2560-d embedding |
| 10002 | HTTP/TCP | memory llama-server | 127.0.0.1 | Memory Gate and Hindsight LLM | private loopback | listener, /v1/models, Memory health |
| 8888 | HTTP/TCP | Hindsight API | 127.0.0.1 | REST, metrics, MCP capability | private loopback | /health HTTP 200 |
| 8890 | HTTP/TCP | Xiaowu Memory Service | 127.0.0.1 | gate, ledger, retain, retrieve | private loopback | /health and /v1/retrieve HTTP 200 |
| 5436 | PostgreSQL/TCP | PostgreSQL container | 127.0.0.1 | Hindsight data store | private loopback | container health and SQL metadata |

The Hermes gateway does not expose a separate TCP listener in this map; its configured messaging and MCP transports are outbound clients.
