# Plugins inventory

| Name | Version | Source state | Purpose | Enabled | Runtime dependency | Verification |
|---|---|---|---|---|---|---|
| xiaowu-memory-bridge | 0.3.0 | Local directory, not Git, no LICENSE | Pre-LLM centralized memory retrieval | yes | Memory Service 8890 | PASS: service health and retrieve API; hook isolated test not run |
| xiaowu-model-router | 0.3.0 | Local directory, not Git, no LICENSE | Session routing to yinyue2 provider and Xiaowu visual skill | yes | Hermes hook system | PARTIAL: enabled and configured; pytest unavailable |
| xiaowu-visual | 1.3.0 | Local directory, not Git, no LICENSE | Publishes xiaowu_avatar_generate and visual turn controls | yes | xiaowu-avatar and MCP | PASS: latest post-restart transaction completed and delivered |
| comfy-video-orchestrator | 0.4.0 | Symlink to Git project at 1631261, dirty tests | Higher-level video execution surface | yes | external project and comfy_5090 | PARTIAL: target unavailable |
| tavern-roleplay-predispatch | 0.1.0 | Local directory, not Git | Roleplay predispatch hooks | no | Hermes hooks | NOT TESTED |

Bundled Hermes plugins were enumerated by the CLI but are outside the custom stack inventory. Enabled means selected in live Hermes configuration; it does not imply every external dependency passed.
