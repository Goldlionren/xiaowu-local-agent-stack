# xiaowu-avatar 0.3.10

小舞的独立、多 Workflow、多 GPU 数字人视觉 Skill。它保留 0.2.4 的角色、场景、关系和长期记忆状态，在其上增加自然语言路由、Comfy MCP execution plan、三 target registry、可恢复 transaction 和局部图片编辑连续性。

## 运行边界

运行时只依赖 Hermes、Hermes MCP runtime、`comfy_5090`、`comfy_4080s`、`comfy_3060`、GPU 主机的 ComfyUI/Workflow、Telegram、Python 标准库。本目录不读取或调用任何其他 Skill。

默认 backend：

```json
{"execution":{"mode":"mcp","default_target":"comfy_3060"}}
```

故障恢复可由管理员在新操作前改成 `direct_http`；已提交 MCP job 绝不跨 backend 重跑。

## 常用命令

```bash
AVATARCTL="$HOME/.hermes/skills/roleplay/xiaowu-avatar/bin/avatarctl"
"$AVATARCTL" doctor
"$AVATARCTL" context
"$AVATARCTL" workflows
"$AVATARCTL" workflow-info yinyue_cosplay01
"$AVATARCTL" mcp-status
"$AVATARCTL" mcp-target
"$AVATARCTL" wardrobe
"$AVATARCTL" prepare --intent '3:4，2MP，拍张现在的照片' --no-send
"$AVATARCTL" transaction-status TRANSACTION_ID
"$AVATARCTL" resend-last
```

生产 agent 只调用一次 `xiaowu_avatar_generate`。该工具内部严格执行 `prepare → MCP → bind → wait/fetch → commit` 协议，Agent 不再手工编排步骤。

0.3.10 保留上述图片事务边界，并增加不经过语言模型的 MCP 节点与结构化穿着管理命令。Telegram 原始消息若仍带 `/xiaowu-avatar` 而标准化文本丢了命令前缀，入口守卫会先恢复原命令。插件在 Skill turn 上硬性禁止其他工具和第二次生成，并校验最终回复：没有成功调用生成工具时，禁止声称已有新图或输出图片链接。未提交的旧事务会被安全取代；已绑定任务只恢复轮询，绝不重复提交。

## 当前 Workflow registry

- `yinyue_cosplay01`：text-to-image；展示当前形象、完整换装、新场景、没有 source 时重建。
- `yinyue_edit01`：image-edit；使用同一 target 的上一张远端结果进行局部修改。

Registry 位于 `registry/workflows.json`。核心代码不固定 node ID；语义角色到 frontend workflow slot 的地址全部在 registry 中。

3060、4080s 和 5090 已部署同一份 `Krea2_YINYUE_cosplay01.json`，采用 Everyone Avatar API 合约：运行时只修改 Prompt `45.value`，身份参考图位于 `17.image`，并保留 Workflow 自身的分辨率和 sampler seed。三台机器的 workflow、参考图、node classes 和模型依赖均已核验；默认节点仍为 3060。`yinyue_edit01` 尚未部署，因此继续保持 fail-closed。

## 确定性管理命令

Telegram 用户可以直接使用以下命令：

    /xiaowu-avatar mcp
    /xiaowu-avatar mcp 3060
    /xiaowu-avatar mcp 4080s
    /xiaowu-avatar mcp 5090
    /xiaowu-avatar 穿着
    /xiaowu-avatar 穿着 设置 {"服装":"黑色西装","袜子":"黑色丝袜"}

MCP 选项完全来自小舞自己的 registry。只有已启用、被 text-to-image Workflow 允许且已经验证或允许运行前预检的 target 才能选择。切换会保留 config.local.json 的其他设置并先备份原配置，只影响之后的新事务。

穿着命令直接读写持久状态，不经过语言模型，也不会生成图片。支持服装、外套、上装、下装、连衣裙、腿部穿着或袜子、鞋子、头饰和配饰；空字符串用于清除字段。

## 事务安全

`prepare` 先创建独立 transaction_id。MCP `vary_workflow` 在 transaction 专属目录创建 variant，`list_workflow_slots` 的业务参数必须由 `verify-variant` 完整比对；只有通过后 `claim-submit` 才消费唯一提交权。`run_workflow(wait=false)` 永远只运行 variant，一次 transaction 最多提交一次。`commit` 只接受 completed job 的 Hermes Linux 本地图片并幂等更新 state；Telegram 失败不会重新生成。

MCP 等待总 deadline 为 240 秒，每次 `job(wait)` 默认 20 秒。`fetch_outputs(inline_images=true)` 的 Windows saved path 只作远端 metadata；MCP ImageContent 经 Hermes runtime 缓存后产生的 `MEDIA:/absolute/linux/path` 才能交给 `commit`。commit 会验证文件存在、可读、非空且具有真实图片签名。

## Workflow 可编辑性

不做完整 SHA-256、node_count、input_count、模型、LoRA 或 topology baseline。用户可修改 GPU Workflow；运行前只验证 registry 声明的本次语义接口仍存在且可写。

## 文档

公开仓库提供净化后的核心源码、插件、schema、registry、恢复脚本和测试。
生产专用的 persona、记忆、状态、交易、图片、Workflow JSON、模型文件与连接配置不在仓库中。
部署边界和验证记录见仓库根目录的 `docs/XIAOWU-AVATAR-AS-BUILT.md`。

## 测试

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -v
RUN_STAGING_SCRIPT_TESTS=1 bash tests/test_safe_scripts.sh
```

所有 helper 仅使用 Python 标准库。已有 production state/history/output 不会被安装器清空。
