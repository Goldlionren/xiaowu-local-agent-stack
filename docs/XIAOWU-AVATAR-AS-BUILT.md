# Xiaowu Avatar As-Built

## Active authority

| Role | Active name |
|---|---|
| Identity slug | xiaowu |
| Root skill | xiaowu-avatar |
| Worker mirror | qwen-worker xiaowu-avatar |
| Plugin | xiaowu-visual 1.4.0 |
| Tool | xiaowu_avatar_generate |
| Router | xiaowu-model-router 0.3.0 |
| State namespace | Identity Studio / xiaowu / xiaowu-avatar |
| Saved production target | comfy_4080s; latest verified job used comfy_5090 |
| Workflow authority | skill registry and transaction state |

The model-facing tool performs state read, intent routing, parameter binding, per-transaction workflow variation, verification, unique submission claim, prompt binding, polling, output fetch, state commit, and configured delivery. The skill contract allows one tool call per user request and forbids direct raw MCP fallback.

## Workflow contract

The current text-to-image workflow retains compatibility ID yinyue_cosplay01 and a production filename containing Krea2_YINYUE. Its active GPU roots use the XiaowuAvatar project directory. The registry marks:

- prompt slot 45.value for the API workflow contract;
- identity reference slot 17.image;
- per-transaction local JSON, SCP, and SHA256 verification;
- workflow resolution and sampler seeds preserved;
- comfy_3060, comfy_4080s, and comfy_5090 enabled;
- all three text-to-image workflow interfaces verified;
- 4080s and 5090 deployed under `F:\AI\XiaowuAvatar`;
- image-edit workflow retained fail-closed because it is not deployed.

The README also describes the production frontend control plane using prompt slot 63.value, resolution slots 49.aspect_ratio and 49.megapixels, and shared seed slot 111.seed. This difference reflects frontend versus API workflow representations and must be kept explicit.

No LoRA setting was found in the selected live registry fields. The workflow had a prior LoRA fix snapshot; the exact model asset names are not published.

## Deterministic management commands

The visual plugin handles these commands before the language model. Target switching is registry-driven, validates readiness, backs up the local override, and affects only new transactions. Wardrobe updates use the existing state lock, schema validation, revision, and history without generating an image.

- `/xiaowu-avatar mcp [target]`
- `/xiaowu-avatar 穿着`
- `/xiaowu-avatar 穿着 设置 <JSON object>`

## Latest verified execution

At 2026-09-12 20:27–20:32 AEST, the verified recovery transaction:

- targeted comfy_5090;
- used workflow ID yinyue_cosplay01;
- submitted exactly once;
- survived a transient local MCP polling disconnect without resubmission;
- recovered the already completed prompt, fetched it, and committed revision 34;
- preserved the original prompt binding and submit_count of one;
- recorded delivery as delivered.

The artifact SHA256 was checked locally. The image, destination, transaction ID, prompt ID, paths, and content are excluded from the public repository.

## Current drift

The root skill and model router now have sanitized public source and regression tests. The qwen-worker mirror and all production identity/configuration/state material remain deployment-specific and are excluded from this repository.
