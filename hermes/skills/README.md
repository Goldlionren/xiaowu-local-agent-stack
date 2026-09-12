# Skills

The sanitized `xiaowu-avatar` source is published in this directory. Private identity content, live configuration, workflows, model assets, runtime state, and generated media remain excluded.

The public execution contract is:

1. Detect a Xiaowu visual request.
2. Call xiaowu_avatar_generate exactly once.
3. Let the tool own state, transaction, workflow, MCP, fetch, commit, and delivery.
4. Do not expose raw MCP operations to the model.
5. Do not retry a failed generation automatically.

See `xiaowu-avatar/README.md` and `docs/XIAOWU-AVATAR-AS-BUILT.md`.
