# Xiaowu Avatar As-Built

## Active authority

| Role | Active name |
|---|---|
| Identity slug | xiaowu |
| Root skill | xiaowu-avatar |
| Worker mirror | qwen-worker xiaowu-avatar |
| Plugin | xiaowu-visual 1.3.0 |
| Tool | xiaowu_avatar_generate |
| Router | xiaowu-model-router 0.3.0 |
| State namespace | Identity Studio / xiaowu / xiaowu-avatar |
| Production target | comfy_3060 |
| Workflow authority | skill registry and transaction state |

The model-facing tool performs state read, intent routing, parameter binding, per-transaction workflow variation, verification, unique submission claim, prompt binding, polling, output fetch, state commit, and configured delivery. The skill contract allows one tool call per user request and forbids direct raw MCP fallback.

## Workflow contract

The current text-to-image workflow retains compatibility ID yinyue_cosplay01 and a production filename containing Krea2_YINYUE. Its active 3060 root is the XiaowuAvatar project. The registry marks:

- prompt slot 45.value for the API workflow contract;
- identity reference slot 17.image;
- per-transaction local JSON, SCP, and SHA256 verification;
- workflow resolution and sampler seeds preserved;
- comfy_3060 workflow present and interface verified;
- comfy_5090 disabled and unverified.

The README also describes the production frontend control plane using prompt slot 63.value, resolution slots 49.aspect_ratio and 49.megapixels, and shared seed slot 111.seed. This difference reflects frontend versus API workflow representations and must be kept explicit.

No LoRA setting was found in the selected live registry fields. The workflow had a prior LoRA fix snapshot; the exact model asset names are not published.

## Latest verified execution

At 2026-09-11 11:48–11:51 AEST, the latest transaction:

- targeted comfy_3060;
- used workflow ID yinyue_cosplay01;
- submitted exactly once;
- completed generation and job polling;
- committed revision 20;
- fetched a 1328 by 1776 PNG;
- recorded delivery as delivered.

The artifact SHA256 was checked locally. The image, destination, transaction ID, prompt ID, paths, and content are excluded from the public repository.

## Current drift

The root and qwen-worker copies are not exact mirrors. SKILL response wording differs, one test differs, and backup workflow files differ. The active gateway uses the default profile, so this did not invalidate the observed root execution; it is a maintenance issue.
