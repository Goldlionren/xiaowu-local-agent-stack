# Xiaowu Production Change / Audit / Release SOP

## Purpose

This SOP defines the standard change-management process for the Xiaowu / 银月2号 production deployment.

The objective is to ensure that every material production change is:

1. implemented deliberately;
2. verified against the live system;
3. reflected in the As-Built documentation;
4. reviewed for privacy and secrets;
5. committed to Git;
6. published through a traceable GitHub release when appropriate.

The public repository is a sanitized reference repository. It is not a production backup.

---

# 1. Core Principle

The live production system is the source of truth.

Documentation, Git history, old audit reports, memory, and prior assumptions must not override observed runtime state.

A component may only be marked `PASS` when direct evidence exists.

Allowed verification states:

- `PASS`
- `FAIL`
- `PARTIAL`
- `NOT TESTED`
- `DISABLED BY DESIGN`

---

# 2. Change Categories

## 2.1 Minor operational change

Examples:

- documentation correction;
- sanitized example update;
- non-functional README change;
- monitoring note;
- compatibility documentation.

Typical release impact:

```text
patch release
v0.1.1 -> v0.1.2
```

## 2.2 Production configuration change

Examples:

- LLM context change;
- llama.cpp launch parameter change;
- Memory Service version change;
- Hindsight configuration change;
- MCP target change;
- plugin configuration change;
- systemd unit change;
- Docker Compose change.

Requires:

- live verification;
- As-Built update;
- verification matrix update;
- secret scan;
- Git commit.

Usually merits at least a patch release.

## 2.3 Functional architecture change

Examples:

- new Memory subsystem;
- new Agent tool;
- new Xiaowu skill;
- new MCP worker;
- new retrieval mode;
- new visual pipeline;
- new long-term memory policy;
- production namespace migration.

Requires a full audit of affected architecture boundaries.

Typically:

```text
minor release
v0.1.x -> v0.2.0
```

---

# 3. Before Making a Production Change

Record:

```text
Date:
Operator:
Component:
Current version:
Current commit/revision:
Requested change:
Reason:
Expected impact:
Rollback method:
```

Before modifying production:

```bash
git status
systemctl status <affected-service>
```

Where relevant also capture:

```bash
docker ps
ss -lntup
hermes gateway status
```

Do not blindly update packages, reset Git trees, recreate Docker volumes, or restart unrelated services.

---

# 4. Production Change Rules

Never use the public Git repository as a direct replacement for production configuration.

Production-specific values must remain private, including:

- credentials;
- API tokens;
- Telegram IDs/destinations;
- SSH keys;
- private IP addresses;
- private hostnames;
- actual memory data;
- runtime state;
- transaction data;
- generated images;
- personal identity images;
- model weights;
- private `.env` files.

Do not use destructive commands unless they are explicitly part of an approved maintenance action.

Examples of commands that require special care:

```text
git reset --hard
docker prune
docker volume rm
docker compose down -v
rm -rf
apt upgrade
pip install --upgrade
```

---

# 5. Post-Change Live Verification

After a change, verify the actual runtime state.

## OS / Host

As required:

```bash
uname -a
cat /etc/os-release
lspci
sycl-ls
xpu-smi
```

## systemd

```bash
systemctl status <service>
systemctl --user status <service>
systemctl is-enabled <service>
systemctl --user is-enabled <service>
```

## Ports

```bash
ss -lntup
```

## Docker

```bash
docker ps
docker compose ps
```

Do not use destructive Docker maintenance commands as validation.

## Main LLM

At minimum:

```text
GET /v1/models
```

When appropriate run a short inference smoke test.

Confirm:

- model alias;
- context;
- device;
- HTTP status;
- inference path.

## Memory

Verify where applicable:

- Memory Service `/health`;
- Hindsight `/health`;
- PostgreSQL connectivity;
- Memory Gate;
- embedding;
- retrieval `/v1/retrieve`;
- current/historical mode behavior.

Do not print real memory payloads into public logs.

## MCP / Avatar

Where relevant verify:

```text
Hermes
-> Xiaowu tool
-> plugin
-> MCP
-> worker
-> generation
-> retrieval
-> state commit
-> delivery
```

Record revision / transaction status without publishing private payloads.

---

# 6. Update As-Built

Primary document:

```text
docs/AS-BUILT.md
```

Update only values verified on the live system.

Examples:

- versions;
- commit SHA;
- service state;
- context size;
- ports;
- model aliases;
- Memory Service version;
- production memory bank;
- active MCP targets;
- Avatar revision;
- compatibility names;
- known issues.

Do not silently remove unresolved issues.

---

# 7. Update Verification Matrix

Primary document:

```text
docs/VERIFICATION-MATRIX.md
```

Every affected component must be classified as:

```text
PASS
FAIL
PARTIAL
NOT TESTED
DISABLED BY DESIGN
```

A `PASS` must be supported by actual runtime evidence.

---

# 8. Git Diff Review

Before staging:

```bash
git status
git diff --stat
git diff
git diff --check
```

`git diff --check` must produce no output.

Review the complete change manually.

---

# 9. Secret / Privacy Review

Run:

```bash
./scripts/audit/secret-scan.sh .
```

Expected result:

```text
PASS private-key
PASS telegram-token
PASS common-api-token
PASS bearer-value
PASS credential-url
PASS private-ip
PASS personal-home
PASS telegram-id
PASS prohibited file types
```

Also review staged files:

```bash
git diff --cached
git status --short
```

Do not commit if any secret/privacy result is unresolved.

---

# 10. Commit

Use focused commit messages.

Examples:

```text
docs: update As-Built after memory service upgrade
fix: align xiaowu avatar MCP target configuration
feat: document historical memory retrieval path
ops: update production inference baseline
```

Then:

```bash
git add <explicit-files>
git diff --cached --check
git diff --cached
git commit -m "<message>"
```

Prefer explicit file paths instead of:

```bash
git add .
```

for production-related documentation updates.

---

# 11. Push

Before push:

```bash
git status
git log -3 --oneline
```

Then:

```bash
git push origin main
```

Verify:

```bash
git rev-parse HEAD
git ls-remote origin refs/heads/main
```

The two commit SHAs must match.

---

# 12. Release Decision

Not every commit requires a release.

Create a release when the change represents a meaningful public baseline.

## Patch

Example:

```text
v0.1.1 -> v0.1.2
```

Use for:

- As-Built corrections;
- verified version updates;
- configuration/reference fixes;
- documentation improvements.

## Minor

Example:

```text
v0.1.x -> v0.2.0
```

Use for:

- new architecture component;
- new major capability;
- new memory architecture;
- significant Agent integration;
- major namespace or workflow change.

## Major

Reserve for incompatible architectural generations.

---

# 13. Create Annotated Tag

Example:

```bash
git tag -a v0.1.2 \
  -m "Xiaowu Local Agent Stack v0.1.2"

git show --no-patch --decorate v0.1.2
git push origin v0.1.2
```

Verify:

```bash
git ls-remote origin refs/tags/v0.1.2
git ls-remote origin refs/tags/v0.1.2^{}
```

The dereferenced `^{}` commit must be the intended release commit.

Never move an already-published release tag to another commit.

---

# 14. GitHub Release

Example:

```bash
gh release create v0.1.2 \
  --title "v0.1.2" \
  --notes "<release notes>"
```

Release notes should summarize:

- what changed;
- what was verified;
- affected components;
- known limitations;
- privacy/security boundary.

Do not include production secrets or runtime payloads.

---

# 15. Post-Release Verification

Run:

```bash
gh release view <tag>

git status

git rev-parse HEAD

git ls-remote origin refs/heads/main
```

Expected final state:

```text
working tree clean
local main == origin/main
release tag points to intended commit
```

---

# 16. Recommended Change Lifecycle

The standard lifecycle is:

```text
Production Change
      |
      v
Live Verification
      |
      v
As-Built Update
      |
      v
Verification Matrix
      |
      v
Secret / Privacy Review
      |
      v
Git Diff Review
      |
      v
Commit
      |
      v
Push main
      |
      v
Tag
      |
      v
GitHub Release
```

---

# 17. Current Baseline

As of 2026-09-11:

```text
Repository:
Goldlionren/xiaowu-local-agent-stack

As-Built:
v0.2

GitHub Release:
v0.1.1

Release commit:
4d93f9d4b76aca59d2206e0233babac45ab0d325
```

This baseline becomes the reference point for future Xiaowu / 银月2号 production changes.
