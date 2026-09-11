# Architecture

The stack separates agent reasoning, durable memory, and external execution.

Hermes receives a single user's messages and calls a loopback OpenAI-compatible main model. A memory bridge queries the Memory Service before the model call and injects retrieved context as background data. After a conversation event, the memory write path extracts candidates, applies secret and durability policy, records an idempotent ledger entry, and submits accepted memories to Hindsight.

Hindsight uses a dedicated local LLM for memory work, a dedicated embedding endpoint, a CPU reranker, and PostgreSQL with vector and text extensions. This keeps memory workload and context independent from the main agent.

The visual path exposes one model-facing tool. The tool owns state, workflow selection, parameter binding, transaction claims, submission, polling, output retrieval, commit, and delivery. MCP provides the transport to remote ComfyUI. Raw infrastructure tools are excluded from the normal agent toolset.

All core AI and memory HTTP listeners in the audited deployment bind to loopback. Remote image execution uses configured MCP transports.
