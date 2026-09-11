# Skills inventory

Hermes had 120 root skill entrypoints and 108 qwen-worker skill entrypoints at audit time. This public inventory records stack-relevant active items; the full host list was reviewed locally and is excluded because it contains unrelated personal capabilities.

| Name | Version | Runtime path | Source / commit | Purpose | Enabled | Dependency | External target | Verification |
|---|---|---|---|---|---|---|---|---|
| xiaowu-avatar | schema 2, visual system 1 | HERMES_HOME/skills/roleplay/xiaowu-avatar | Local, not Git, no LICENSE found | Deterministic avatar transaction authority | Installed and active through plugin | xiaowu-visual, MCP | comfy_3060 | PASS: revision 20 transaction delivered |
| qwen-worker xiaowu-avatar mirror | same runtime family | profile skill root | Local, not Git, no LICENSE found | Worker profile mirror | Profile exists; gateway stopped | qwen-worker profile | comfy_3060 | PARTIAL: mirror differs in SKILL wording and one test |
| comfy-video-orchestrator | project plugin/skill integration | external project symlink | Git HEAD 1631261951c553eac58c16d5cc0a33b30924c792, dirty | Higher-level Comfy video workflow | Plugin enabled | project adapter and MCP | comfy_5090 | PARTIAL: enabled, target unavailable |

Backup directories and the comfyui-workflow-core backup copy were classified as backups, not active implementations.

No skill source, identity material, or assets are published here because local licensing and privacy suitability were not established.
