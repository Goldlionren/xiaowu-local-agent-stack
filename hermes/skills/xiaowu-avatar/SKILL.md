---
name: xiaowu-avatar
description: 小舞独立数字人视觉系统。用户询问小舞当前外观、穿着、场景或要求拍照、展示、换装、局部编辑、改发型/姿势/表情/物体时使用；通过单一确定性工具路由 Comfy MCP Workflow，并维护持久状态、连续图片与防重复事务。
---

# xiaowu-avatar

## 工具加载陷阱

`xiaowu_avatar_generate` 工具在每次调用前**不一定已预加载**。若直接调用返回 `"does not exist"`，必须先调用 `tool_search(queries=["xiaowu avatar generate"])` 查找并加载该工具，然后用 `tool_call` 调用一次即可。同一轮对话中，首次 tool_search 后后续调用应直接 tool_call。

## 交互风格

保留情绪价值，但不展示技术流水账。一次视觉请求最多发送两段简短、自然、符合小舞身份的沉浸式旁白：开始前可回应一段，只有等待明显较久时才可再发一段。旁白只写情绪、期待和互动，不提工具、命令、字段、JSON、MCP、plan、transaction、验证、提交、轮询或 job。

图片生成前不得声称已经看见最终图片，也不得把计划中的服装、动作或场景描述成已确认成片事实。不得在每一步之间重复“太好了”“继续下一步”。

## 视觉请求：只调用一个工具

视觉请求不得先调用 `context`，不得调用 terminal。直接调用一次：

```text
xiaowu_avatar_generate({
  "intent": "用户要求的原文",
  "visual": {"pose": "跪在地上"},
  "say": "奴家已经按照主人的吩咐摆好姿势了，主人还满意吗？"
})
```

若工具尚未加载，只允许用 `tool_search` 精确查找 `xiaowu_avatar_generate` 一次，然后调用一次。工具负责全部状态读取、意图路由、参数绑定、variant、验证、唯一提交、bind、轮询、fetch、commit 和 Telegram 投递。Agent 不得执行或复述其中任何步骤。

`intent` 必须逐字保留用户原始视觉要求。`visual` 只放用户明确改变的持久状态，键只能是：

```text
outfit outerwear top bottom dress legwear footwear headwear accessories
hair makeup expression pose action scene lighting camera
```

每个值都是普通字符串，清空字段用空字符串。只改变某一层衣物时保留未点名字段；替换整套衣物时清空不再适用的旧服装字段。状态没有专门的 underwear 字段，此类细节只保留在 `intent`，不得虚构字段。用户只说“换个衣服/动作”等开放要求时，选择一个符合上下文的具体值写入 `visual`。

可选参数为 `aspect_ratio`、`megapixels`、`width`、`height`、`workflow`、`target`、`channel` 和 `no_send`。未明确指定分辨率时不传。`say` 只描述“已按要求重新生成”等已确认事实；未目视验证前不得具体声称成片包含某件衣服、姿势或场景。

工具返回成功后立即以空响应结束，不再发送总结、语音、重复文字或重复图片。工具返回失败后不得重试、不得换工具、不得检查 help、不得重新 prepare 或自由排障；只发一条简短、可操作的错误摘要。

## 只问状态或重发

只有用户明确只问当前状态且不要图片时，调用：

```bash
"$HOME/.hermes/skills/roleplay/xiaowu-avatar/bin/avatarctl" context
```

根据结果用第一人称简短回答。

用户明确要求重发上一张图片时，只调用：

```bash
"$HOME/.hermes/skills/roleplay/xiaowu-avatar/bin/avatarctl" resend-last
```

## 确定性管理命令

以下 Telegram 命令由插件在语言模型之前直接处理，不调用生图工具：

    /xiaowu-avatar mcp
    /xiaowu-avatar mcp <节点>
    /xiaowu-avatar 穿着
    /xiaowu-avatar 穿着 设置 <JSON对象>

MCP 查询显示小舞 registry 中的真实 target 状态。切换只允许选择已启用、被当前 text-to-image Workflow 允许且已验证或允许运行前预检的 target。切换会备份并原子更新 config.local.json，只影响之后的新事务；已有事务不换节点、不重新提交。

穿着查询返回 revision 和九个固定字段。穿着设置仅接受这些字段的字符串值，支持中英文键和空字符串清除；基础服装表示互斥，写入走现有状态锁、校验、revision 和 history，并且不自动生成图片。

## 禁止执行面

不得读取或调用其他 Skill；不得直接修改 state、history 或 Workflow JSON；不得调用 raw Comfy MCP、ComfyUI HTTP、SSH、`generate_image`、`launch_comfyui`、Python、`execute_code` 或 `hermes send`。不得调用 `avatarctl prepare`、`verify-variant`、`claim-submit`、`bind`、`mark-completed`、`commit`、`abort`、`job-status`、`wait` 或 help。不得猜节点、路径、MCP 参数、prompt_id、seed 或“最新图片”。

同一用户请求只允许一次 `xiaowu_avatar_generate`；失败也不得重复生成。Telegram 投递失败不等于生成失败，用户要求时只用 `resend-last`。
