# MCP inventory

Hostnames, account names, SSH key paths, and private addresses are replaced with variables.

| Server | Transport | Enabled in Hermes | Model-facing toolset | Workspace | Capabilities | Live status |
|---|---|---|---|---|---|---|
| comfy_3060 | stdio over SSH | yes | raw toolset disabled; visual plugin mediates | REMOTE_XIAOWU_ROOT | 39 tools including workflow run, job, fetch, validation | PASS: connected in 4.89 s, 39 tools discovered |
| comfy_5090 | stdio over SSH | configured | excluded; registry enabled false | REMOTE_XIAOWU_ROOT | expected Comfy MCP | DISABLED BY DESIGN for avatar; direct connection failed |
| comfy_4080s | stdio over SSH | yes | excluded | separate Comfy project | 39 tools | PASS: connected in 7.33 s |
| comfytv_4080s | HTTP MCP | yes | raw toolset disabled | separate ComfyTV service | not discovered during audit | FAIL: 30 s connection timeout |
| Hindsight MCP | HTTP endpoint exposed by Hindsight | not configured as Hermes production memory route | none | local Hindsight | Hindsight MCP surface | PARTIAL: service capability present; Hermes uses Memory Service API |

The live gateway repeatedly attempted comfy_5090 even though the avatar registry disables the target. This is a configuration hygiene issue: workflow policy is fail-closed, while the lower-level Hermes MCP entry remains enabled.
