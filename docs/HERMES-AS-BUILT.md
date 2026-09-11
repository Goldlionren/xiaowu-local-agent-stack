# Hermes As-Built

## Runtime

| Item | Observed value |
|---|---|
| Version | Hermes Agent v0.20.6, release date shown as 2026.8.27 |
| Install method | Git checkout with Python virtual environment |
| Upstream | NousResearch/hermes-agent |
| HEAD | 26350357d76e4508c8df9304a3374bdc5a6f6220 |
| Branch state | Detached HEAD |
| Worktree | Dirty: five modified tracked files and two untracked files |
| Reported divergence | Hermes CLI reported 7149 commits behind; branch divergence could not be computed for detached HEAD |
| Gateway | user systemd service, enabled and active |
| Gateway process | Python module hermes_cli.main gateway run |
| Restart policy | always, 5 second delay |
| Linger | enabled |

The CLI described the checkout as upstream 45a6101f, local 26350357, with 26324 carried commits. Because the local checkout is detached and dirty, update or replacement requires a separate change-controlled reconciliation.

## Provider and profiles

The default profile uses model yinyue2 through custom provider yinyue2-local at the loopback OpenAI-compatible endpoint on port 10000. The qwen-worker profile exists but its gateway was stopped during the audit.

The qwen-worker copy of xiaowu-avatar is not byte-identical to the root skill. Its SKILL example uses different response wording, and one test file differs. This is recorded as mirror drift.

## Enabled user plugins

- comfy-video-orchestrator 0.4.0
- xiaowu-memory-bridge 0.3.0
- xiaowu-model-router 0.3.0
- xiaowu-visual 1.3.0

Hermes configuration globally removes raw Comfy MCP toolsets from normal agent exposure. The visual plugin is allowed to call its required tools and publishes xiaowu_avatar_generate.

## State layout

Identity Studio selects the xiaowu identity. Current avatar state is below the Identity Studio xiaowu namespace. Legacy yinyue-avatar state and migration backups remain present but are not the active state authority.

Runtime databases, sessions, history, transaction records, logs, caches, identity assets, and environment files are intentionally excluded from this repository.

## Startup

The gateway is started by a user unit with a working directory at HERMES_HOME. The unit sets the virtual environment and Hermes home, writes to journald, and uses mixed cgroup termination. See the sanitized unit example under deploy/systemd.
