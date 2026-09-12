# Plugins

The deployment uses separate plugins for memory injection, model routing, and visual execution. The sanitized `xiaowu-model-router` source is published here. The visual plugin is bundled with `hermes/skills/xiaowu-avatar` so its transaction contract stays versioned with the skill.

Keep allow_tool_override false for routing and memory policy plugins. Grant the visual plugin only the MCP actions required by its transaction controller.

Private identity content, live provider configuration, credentials, state, and generated media remain excluded.
