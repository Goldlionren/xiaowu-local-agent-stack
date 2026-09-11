# 银月2号 / 小舞（Xiaowu）As-Built 文档

**文档版本：** v0.2<br>
**状态日期：** 2026-09-11（Australia/Sydney）<br>
**部署类型：** 单用户、Local-first、长期记忆 Agent<br>
**生产身份：** 小舞 / `xiaowu`<br>
**公开参考仓库：** `Goldlionren/xiaowu-local-agent-stack`<br>
**首个公开 Release：** `v0.1.0`

---

## 0. 文档目的与审计边界

本文档记录银月2号截至 2026-09-11 已实际部署、现场验证并能够作为 As-Built 基线的组件状态。

本文档遵循以下原则：

- 以本机实际 OS、systemd、Docker、进程、端口、Git、Hermes CLI、API、数据库 metadata、MCP connection、Identity Studio state 和 transaction evidence 为事实依据。
- 仅将有现场证据的项目标记为 `PASS`。
- 未完成独立验证的项目标记为 `PARTIAL`、`NOT TESTED` 或 `DISABLED BY DESIGN`。
- 不把银月1号的配置混入银月2号。
- 不公开生产 memory rows、chat、Telegram destination、token、key、SSH material、数据库 dump、个人图片、生成图片、模型权重或其他隐私数据。
- 角色身份已迁移到 Xiaowu namespace，但部分 `yinyue*` 名称仍作为兼容或部署标识保留；不能简单视为迁移失败。
- 2026-09-11 审计期间未停止、重启、升级、删除或重建任何生产服务。

---

# 第一部分：Ubuntu OS、Hermes、Docker 与 LLM Services

## 1.1 主机与操作系统

| 项目 | 当前 As-Built |
|---|---|
| OS | Ubuntu 24.04.4 LTS |
| Kernel | Linux 7.0.0-31-generic x86_64 |
| Hardware platform | Intel NUC12SNKi72 |
| CPU | Intel Core i7-12700H |
| CPU topology | 14 cores / 20 logical CPU |
| RAM | 62 GiB reported by `free` |
| Swap | 90 GiB |
| Primary storage | Samsung 970 EVO Plus 2 TB |
| Role | Xiaowu local agent host |

### GPU

| GPU | 当前角色 |
|---|---|
| Intel Arc Pro B60 | 主 Agent LLM |
| Intel Arc A770M | Memory LLM + Embedding |
| Intel Iris Xe | 系统可见，非主要推理设备 |

现场 `xpu-smi` 状态为 normal。

---

## 1.2 Intel oneAPI / SYCL

现场确认：

- SYCL / Level Zero 可用。
- `SYCL device 0` = Arc Pro B60。
- `SYCL device 1` = Arc A770M。
- `SYCL device 2` = Iris Xe。
- 主 llama.cpp build 使用 IntelLLVM 2025.3.2。
- Unified Runtime over Level Zero：`1.14.37020+3`。
- llama.cpp build 启用了：
  - `GGML_SYCL`
  - SYCL DNN
  - FP16
  - graph support
  - Level Zero API support

该配置是银月2号当前实际生产推理基础。

---

## 1.3 主 LLM Service

### systemd / runtime

| 项目 | 当前值 |
|---|---|
| Unit | `llama-yinyue2.service` |
| Scope | system |
| 状态 | enabled / active |
| API | OpenAI-compatible |
| Bind | `127.0.0.1:10000` |
| Alias | `yinyue2` |
| Hermes Provider | `custom:yinyue2-local` |
| GPU | Arc Pro B60 |
| SYCL mapping | `ONEAPI_DEVICE_SELECTOR=level_zero:0`, `SYCL0` |

### llama.cpp

| 项目 | 当前值 |
|---|---|
| Version | `0.4.0-dev build 1` |
| Commit | `4d9176092d00586775af140581bb0b558ddc4389` |
| Worktree | clean |
| Compiler | IntelLLVM 2025.3.2 |

### 模型

`HauhauCS/Qwen3.6-35B-A3B-Uncensored-HauhauCS-Aggressive`

当前生产参数：

```text
quantization: Q4_K_M
context: 262144
parallel: 1
n_gpu_layers: 999
kv_unified: enabled
cache_type_k: q8_0
cache_type_v: q8_0
flash_attn: on
batch: 1024
ubatch: 256
threads: 8
jinja: enabled
mmproj: enabled
mmproj_offload: disabled
fit: off
MTP: disabled
```

### 现场验证

- `/v1/models` 返回 `yinyue2`。
- API 报告 context `262144`。
- Chat Completion HTTP `200`。
- 短测总时延约 `0.449 s`。
- llama.cpp timing 约 `74.09 tok/s`。
- 测试的 8-token 上限在 reasoning 阶段耗尽，因此该测试证明 API 与推理链路正常，但不用于声明文本回答质量。

> **重要兼容说明**<br>
> 当前角色已经迁移到 `xiaowu`，但主 LLM alias `yinyue2` 和 provider `yinyue2-local` 仍属于正常生产标识，本阶段不做强制重命名。

---

## 1.4 Hermes Agent

### 当前版本

| 项目 | 当前值 |
|---|---|
| Hermes | Agent v0.20.6 |
| Gateway unit | `hermes-gateway.service` |
| Scope | user systemd |
| Enabled | Yes |
| Active | Yes |
| Restart | `always` |
| RestartSec | `5` |
| Linger | enabled |

Hermes 默认模型：

```text
model = yinyue2
provider = custom:yinyue2-local
base URL = http://127.0.0.1:10000/v1
```

### Hermes Git 状态

| 项目 | 当前值 |
|---|---|
| Origin | `https://github.com/NousResearch/hermes-agent.git` |
| HEAD | `26350357d76e4508c8df9304a3374bdc5a6f6220` |
| Git state | detached HEAD |
| Worktree | dirty |
| Tracked modifications | 5 |
| Untracked files | 2 |

Hermes CLI 还报告：

- upstream：`45a6101f`
- local：`26350357`
- carried commits：26324
- behind：7149

由于当前是 detached HEAD，不能把普通 branch divergence 作为唯一依据。后续升级前必须先做 carried/local changes reconciliation。

---

## 1.5 Docker

现场版本：

| 项目 | 当前值 |
|---|---|
| Docker Engine | 29.4.1 |
| Docker Compose | 5.1.3 |

当前与长期记忆有关的主要容器：

| Container | Image | 状态 | Network |
|---|---|---|---|
| `yinyue2-hindsight-api` | `ghcr.io/vectorize-io/hindsight-api:0.8.6` | running | host |
| `yinyue2-hindsight-db` | `tensorchord/vchord-suite:pg18-latest` | running / healthy | mapped |

PostgreSQL 映射：

```text
127.0.0.1:5436 -> container:5432
```

Memory Docker network：

```text
yinyue2-memory-net
```

数据库使用持久化 Hindsight PostgreSQL volume。

### 运维约束

本部署不把 `docker prune`、volume delete、recreate 等破坏性操作作为日常 health check 的一部分。

---

## 1.6 第一部分端口矩阵

| Port | Service | Bind | 状态 |
|---:|---|---|---|
| 10000 | Main Agent LLM | loopback | PASS |
| 10001 | Embedding llama-server | loopback | PASS |
| 10002 | Memory LLM | loopback | PASS |
| 8888 | Hindsight API | loopback | PASS |
| 8890 | Xiaowu Memory Service | loopback | PASS |
| 5436 | PostgreSQL host mapping | loopback | PASS |

---

# 第二部分：Hindsight 为核心的长期记忆解决方案

## 2.1 当前记忆架构

生产链路可抽象为：

```text
User / Hermes
    |
    v
Xiaowu Memory Bridge
    |
    v
Xiaowu Memory Service :8890
    |
    +--> Memory Gate
    |
    +--> Hindsight API :8888
            |
            +--> Memory LLM :10002
            +--> Embedding :10001
            +--> CPU Reranker
            +--> PostgreSQL 18 / VectorChord / pgvector
```

当前生产 bank：

```text
xiaowu-main
```

设计原则：

```text
single-user / one-agent-per-memory-domain
```

一个 memory bank 对应一个用户和一个 Agent identity，不作为共享公共知识库。

---

## 2.2 Hindsight

| 项目 | 当前值 |
|---|---|
| Version | 0.8.6 |
| API | `127.0.0.1:8888` |
| Health | HTTP 200 |
| DB state | connected |
| Container | `yinyue2-hindsight-api` |

OpenAPI 现场可见能力包括：

- bank
- memory
- observation
- document
- entity
- consolidation
- stats
- metrics

Hindsight MCP surface 存在。

### License 状态

现场存在许可证 metadata 冲突：

- OCI image label：MIT
- live OpenAPI metadata：Apache-2.0

因此公开仓库没有复制 Hindsight 源码，仅记录集成方式与镜像引用。

---

## 2.3 Memory LLM

| 项目 | 当前值 |
|---|---|
| Unit | `llama-yinyue2-hindsight.service` |
| Alias | `yinyue2-hindsight` |
| Port | 10002 |
| Model | Qwen3.5 9B Q8_0 |
| Context | 16384 |
| Device | Level Zero device 1 / A770M |
| 状态 | PASS |

该服务与主 Agent LLM 分离，专用于 Memory/Hindsight 侧推理。

---

## 2.4 Embedding Service

| 项目 | 当前值 |
|---|---|
| Unit | `llama-yinyue2-embedding.service` |
| Alias | `yinyue2-embedding` |
| Port | 10001 |
| Model | Qwen3 Embedding 4B Q4_K_M |
| Context | 8192 |
| Device | Level Zero device 1 / A770M |
| 状态 | PASS |

Live request 返回：

```text
embedding dimensions = 2560
```

---

## 2.5 PostgreSQL + Vector Stack

| 项目 | 当前值 |
|---|---|
| PostgreSQL | 18.3 |
| `vchord` | 1.1.1 |
| `vector` | 0.8.2 |
| `vchord_bm25` | 0.3.0 |
| `pg_tokenizer` | 0.1.1 |
| `pg_trgm` | 1.6 |

已确认 control schema 包含：

```text
ingest_ledger
source_event_registry
turn_candidate_manifest
turn_registry
```

审计仅检查 schema、extension、health 与 metadata；没有 dump memory rows。

---

## 2.6 Xiaowu Memory Service

| 项目 | 当前值 |
|---|---|
| Version | `0.5.1-phase8f-fast2` |
| Unit | `xiaowu-memory.service` |
| Bind | `127.0.0.1:8890` |
| State | enabled / active |
| Production bank | `xiaowu-main` |

`/health` 当前确认：

- PostgreSQL healthy
- Hindsight healthy
- Memory Gate healthy

### Retrieval API

```text
/v1/retrieve
```

现场 nonce query：

- HTTP 200
- mode = `current`
- resolved = `true`
- elapsed ≈ `1.013 s`

实际 memory/context 内容没有打印或保存进公开审计数据。

---

## 2.7 Memory Gate

Memory Gate 已经纳入 production health path，并由 Memory Service `/health` 现场验证为 healthy。

其职责是对记忆写入/检索前后的策略与记忆上下文进行控制，而不是直接让 Hermes 访问数据库。

---

## 2.8 写入与 Ledger

当前生产记忆服务已经具有独立 control schema 与 ledger 类结构，包括：

```text
ingest_ledger
source_event_registry
turn_candidate_manifest
turn_registry
```

这为以下能力提供基础：

- ingest audit
- source event tracking
- turn-level candidate tracking
- idempotency / replay handling
- downstream consolidation / retrieval bookkeeping

本文档不公开实际生产记录。

---

## 2.9 Consolidation

Hindsight API 现场路由包含 consolidation 能力，并作为长期记忆生命周期的一部分保留。

As-Built 仅确认该 surface 与 production Hindsight stack 存在，不在本版本声称所有 consolidation policy 都完成独立逐项 benchmark。

---

## 2.10 Current / Historical Retrieval

Memory Service 已具备 `/v1/retrieve`，现场 nonce query 证明 `current` 模式工作正常。

生产设计同时包含：

- current-state retrieval
- historical-state retrieval
- reranking
- Memory Gate policy
- Hindsight-backed memory store

其中本次审计对完整链路取得 PASS，但并未打印真实用户记忆结果。

---

## 2.11 Reranker

Live Compose 当前指向：

```text
BAAI/bge-reranker-v2-m3
```

已确认配置：

```text
device: CPU
bucket batching: enabled
batch: 8
max candidates: 32
```

状态：

```text
PARTIAL
```

原因：本次没有单独进行独立 reranker probe；其存在与配置已确认，并且整条 retrieval 链路现场通过。

---

## 2.12 Memory Stack 当前结论

| 组件 | 状态 |
|---|---|
| Hindsight 0.8.6 | PASS |
| PostgreSQL 18.3 | PASS |
| VectorChord / pgvector | PASS |
| Memory LLM | PASS |
| Embedding | PASS |
| Xiaowu Memory Service 0.5.1-phase8f-fast2 | PASS |
| Memory Gate | PASS |
| `/v1/retrieve` | PASS |
| Reranker independent probe | PARTIAL |
| Production bank `xiaowu-main` | PASS |

---

# 第三部分：Skills、Plugins、MCP 与 Xiaowu Avatar

## 3.1 Hermes 能力层

现场扫描结果：

- root：120 个 `SKILL.md` entrypoint
- qwen-worker：108 个 `SKILL.md` entrypoint

公开仓库只记录与银月2号核心架构直接相关的 active integration，不公开完整主机能力清单。

---

## 3.2 当前 Active Xiaowu 命名

2026-09-11 已完成 Avatar namespace 迁移：

| 类型 | 当前生产名 |
|---|---|
| Identity | `xiaowu` |
| Skill | `xiaowu-avatar` |
| Plugin | `xiaowu-visual` |
| Tool | `xiaowu_avatar_generate` |
| Router | `xiaowu-model-router` |
| State authority | Identity Studio `/xiaowu/xiaowu-avatar` |

历史命名：

```text
yinyue-avatar
yinyue-visual
yinyue-model-router
yinyue_avatar_generate
```

现主要作为 compatibility、backup、migration history 或 legacy artifacts 存在。

---

## 3.3 Xiaowu Avatar

当前生产状态：

| 项目 | 当前值 |
|---|---|
| Root Skill | `xiaowu-avatar` |
| Plugin | `xiaowu-visual 1.3.0` |
| Tool | `xiaowu_avatar_generate` |
| Router | `xiaowu-model-router 0.3.0` |
| Identity slug | `xiaowu` |
| Workflow authority | skill registry + transaction state |
| State authority | Identity Studio |
| Production Comfy target | `comfy_3060` |

API workflow contract：

```text
prompt: 45.value
reference image: 17.image
```

README 中另记录 frontend control plane：

```text
63.value
49.aspect_ratio
49.megapixels
111.seed
```

两者代表 API/frontend 的不同表示，不能混写。

### 最新成功事务

审计确认 revision `20`：

```text
submit_count = 1
generation = completed
job = completed
commit_status = committed
delivery_status = delivered
```

生成结果经过文件级验证：

```text
1328 x 1776
RGB PNG
SHA256:
b379b33142ba2bbdedf502408a5c0841b99ae330729fd3f52023116c85b050ad
```

图片、prompt、destination、transaction ID、prompt ID 均未进入公开仓库。

---

## 3.4 Xiaowu Visual Plugin

| 项目 | 当前值 |
|---|---|
| Plugin | `xiaowu-visual` |
| Version | 1.3.0 |
| Enabled | Yes |
| License in local production directory | 未发现 |
| Public source | 未复制 |

公开仓库仅描述 integration contract 与 architecture，不复制无许可证的生产源码。

---

## 3.5 Xiaowu Model Router

| 项目 | 当前值 |
|---|---|
| Plugin | `xiaowu-model-router` |
| Version | 0.3.0 |
| Enabled | Yes |
| 状态 | PARTIAL |

其生产集成存在且启用，但本次没有完成 isolated router test，因此不标记完整 PASS。

---

## 3.6 其他 Plugins

当前 Hermes live config / CLI 确认：

| Plugin | Version | 状态 |
|---|---:|---|
| `comfy-video-orchestrator` | 0.4.0 | enabled |
| `xiaowu-memory-bridge` | 0.3.0 | enabled |
| `xiaowu-model-router` | 0.3.0 | enabled |
| `xiaowu-visual` | 1.3.0 | enabled |
| `tavern-roleplay-predispatch` | 0.1.0 | installed / not enabled |

`comfy-video-orchestrator`：

- symlink 到 Git 项目
- HEAD：`1631261951c553eac58c16d5cc0a33b30924c792`
- branch：main
- tests 目录存在 dirty state

---

## 3.7 MCP Inventory

### comfy_3060

```text
status: PASS
transport: stdio over SSH
connect test: 4.89 s
discovered tools: 39
role: Xiaowu Avatar production target
```

远端 Windows project root 实际为：

```text
D:\AI\XiaowuAvatar
```

公开 repo 中使用变量替代。

### comfy_5090

```text
Avatar registry: enabled=false
status: DISABLED BY DESIGN
```

直接 connection test 为 closed。

同时存在一个待处理的一致性问题：

- Avatar registry 已 disabled。
- Hermes lower-level MCP entry 仍 enabled，并会周期尝试连接。

这会产生 parked warning，但不影响 Xiaowu Avatar 的 fail-closed 行为。应在未来 change window 做配置统一。

### comfy_4080s

```text
status: PASS
connect test: 7.33 s
discovered tools: 39
```

### comfytv_4080s

```text
status: FAIL
reason: HTTP MCP 30 s timeout
```

该故障与当前 Xiaowu Avatar 的 3060 主链路分离，不影响 Avatar production path。

### Hindsight MCP

```text
status: PARTIAL
```

Hindsight MCP surface 存在，但 production memory route 当前主要使用 `:8890` Memory Service API，而不是 Hermes `mcp_servers` 入口。

---

## 3.8 Xiaowu Avatar 完整生成链路

当前已验证 production path：

```text
Hermes
  -> xiaowu_avatar_generate
  -> xiaowu-visual
  -> MCP
  -> comfy_3060
  -> workflow
  -> generated output
  -> output fetch
  -> Identity Studio state commit
  -> delivery
```

revision 20 已证明：

- generation completed
- retrieval completed
- state commit completed
- delivery delivered

---

## 3.9 qwen-worker Mirror

`xiaowu-avatar` 在 root 和 qwen-worker mirror 均存在。

现场确认两者不是完全一致：

- SKILL 示例措辞不同
- 一个 test 文件不同
- workflow backup 存在差异

因此：

```text
qwen-worker mirror = PARTIAL
```

后续需要建立 hash/deploy manifest，明确这些差异是 intentional 还是 drift。

---

## 3.10 Legacy Yinyue Compatibility

### 当前仍属正常兼容项

以下 `yinyue*` 命名仍属于正常生产兼容/部署标识：

```text
yinyue2
yinyue2-local
yinyue2-hindsight
yinyue2-embedding
yinyue_cosplay01
Krea2_YINYUE
yinyue-prefixed output
/yinyue_avatar compatibility command
部分 CSS / temp / test identifiers
```

### 非 active authority 的历史遗留

```text
old state/yinyue-avatar
namespace migration backup
pre-migration / pre-fix / backup files
指向旧 state root 的历史文档
stale bytecode
```

本次审计没有删除这些文件。

---

# 4. Runtime Verification Matrix

| 项目 | 状态 |
|---|---|
| Ubuntu OS | PASS |
| Intel GPU | PASS |
| oneAPI / SYCL | PASS |
| Main LLM | PASS |
| Hermes CLI | PASS |
| Hermes Gateway | PASS |
| Docker | PASS |
| PostgreSQL | PASS |
| Hindsight | PASS |
| Memory LLM | PASS |
| Embedding | PASS |
| Xiaowu Memory Service | PASS |
| Memory Gate | PASS |
| `/v1/retrieve` | PASS |
| Reranker independent probe | PARTIAL |
| `xiaowu-avatar` | PASS |
| `xiaowu-visual` | PASS |
| `xiaowu-model-router` isolated test | PARTIAL |
| `xiaowu_avatar_generate` | PASS |
| Comfy MCP / `comfy_3060` | PASS |
| Image generation | PASS |
| Image retrieval | PASS |
| Delivery | PASS |
| `comfy_5090` | DISABLED BY DESIGN |
| `comfy_4080s` | PASS |
| `comfytv_4080s` | FAIL |
| Hindsight MCP as Hermes route | PARTIAL |
| qwen-worker mirror | PARTIAL |
| Production pytest suite | NOT TESTED |

---

# 5. Security / Public Release 状态

2026-09-11 public candidate 已完成 Secret/Privacy Review：

```text
PASS FOR PUBLIC RELEASE
```

检查覆盖：

- private key headers
- Telegram token / ID patterns
- common API token patterns
- Bearer values
- database URLs with credentials
- RFC1918 IP
- personal home paths
- prohibited file types
- symlinks
- maximum file sizes
- staged Git content

本机没有安装：

```text
gitleaks
trufflehog
```

因此采用 `rg`、`find`、Git index review 与人工关键词检查作为替代，并记录 limitation。

明确未进入公开仓库的内容：

- production memory
- chat transcripts
- Telegram destination
- API tokens
- passwords
- SSH keys
- cookies
- DB dumps
- personal images
- generated images
- runtime state
- transaction data
- backup data
- raw logs
- GGUF
- safetensors
- embedding/reranker/model files
- 无许可证的 custom Memory/Avatar/plugin 源码

---

# 6. GitHub Public Reference Repository

## 6.1 Repository

```text
Goldlionren/xiaowu-local-agent-stack
```

用途：

- 公开 As-Built reference
- sanitized deployment examples
- architecture documentation
- health / secret scan helpers
- migration notes
- integration boundaries

它不是生产目录镜像，也不是数据库或长期记忆备份。

## 6.2 初始公开基线

审计时 public candidate：

```text
branch: main
commit: 3c2b51a6da3ebe7469f65546f0d2127b2fafc87b
message:
Initial public release: Xiaowu local agent stack as-built 2026-09-11
```

## 6.3 GitHub 发布后状态

2026-09-11 已完成：

- GitHub CLI authentication
- Public repository creation
- `origin` 配置
- `main` push
- local/remote HEAD 一致性验证
- `v0.1.0` tag / GitHub Release 发布

首个 GitHub Release：

```text
v0.1.0
```

公开仓库应视为今后银月2号 / Xiaowu 架构文档与 sanitized reference configuration 的版本化基线。

---

# 7. 与 2026-09-10 As-Built 的主要差异

| 项目 | 2026-09-10 | 2026-09-11 |
|---|---|---|
| Memory Service | `0.5.0-phase8f-fast1` | `0.5.1-phase8f-fast2` |
| Avatar public name | yinyue-* | xiaowu-* |
| Identity state authority | legacy yinyue path | `xiaowu/xiaowu-avatar` |
| Main LLM context | 131072 文档基线 | 262144 live |
| Main LLM systemd | 未完全冻结 | live verified |
| Hermes Gateway | 未完全冻结 | enabled / active verified |
| Avatar production target | 未完整冻结 | `comfy_3060` |
| `comfy_5090` | 不明确 | DISABLED BY DESIGN |
| Avatar evidence | 部分 | revision 20 completed/committed/delivered |
| Public repo | 无 | 已发布 |
| GitHub Release | 无 | `v0.1.0` |

---

# 8. 当前 Known Issues

1. **Hermes detached HEAD + dirty**<br>
   后续升级前必须先审查 carried/local changes，不能直接覆盖。

2. **qwen-worker `xiaowu-avatar` mirror drift**<br>
   当前存在两处源码/测试/backup 类差异，需要建立 hash/deploy manifest。

3. **`comfy_5090` registry 与 MCP config 不一致**<br>
   Avatar registry 已 disabled，但 Hermes lower-level MCP 仍尝试连接并产生日志噪声。

4. **`comfytv_4080s` timeout**<br>
   当前 HTTP MCP 30 秒超时。

5. **Reranker 未做独立 probe**<br>
   仅有 live config 与整链路 retrieval evidence。

6. **`xiaowu-model-router` 未做 isolated test**。

7. **Production pytest 未执行**<br>
   Hermes venv / PATH 没有 pytest，本次未自动安装。

8. **Custom source licensing 未冻结**<br>
   Memory Service、Avatar、Visual、Router 的本地生产目录没有 LICENSE，因此未进入公开源码仓库。

9. **Hindsight license metadata 冲突**<br>
   OCI image 与 live OpenAPI metadata 不一致。

---

# 9. 建议的下一阶段工作

按优先级建议：

1. 将本 As-Built v0.2 作为 2026-09-11 正式基线。
2. 让 GitHub repository 成为后续 sanitized As-Built 与配置模板的唯一公开版本基线。
3. 为 `xiaowu-avatar` root / qwen-worker mirror 建立 hash/deploy manifest。
4. 在独立 maintenance window 处理 Hermes detached/dirty/upstream reconciliation。
5. 统一 `comfy_5090` workflow registry 与 Hermes MCP enabled state。
6. 单独排查 `comfytv_4080s` timeout。
7. 为 reranker 与 model-router 增加 isolated health tests。
8. 对 custom Memory/Avatar/Plugin 明确版权与许可证后，再决定是否单独开源实现。
9. 后续每次生产变更采用：
   - live audit
   - As-Built diff
   - secret review
   - Git commit
   - tagged release

---

# 10. 当前生产版本索引

| Component | Version / Commit / Revision |
|---|---|
| Ubuntu | 24.04.4 LTS |
| Kernel | 7.0.0-31-generic |
| Hermes | v0.20.6 |
| Hermes HEAD | `26350357d76e4508c8df9304a3374bdc5a6f6220` |
| llama.cpp | 0.4.0-dev build 1 |
| llama.cpp HEAD | `4d9176092d00586775af140581bb0b558ddc4389` |
| Main model alias | `yinyue2` |
| Main context | 262144 |
| Hindsight | 0.8.6 |
| PostgreSQL | 18.3 |
| VChord | 1.1.1 |
| pgvector | 0.8.2 |
| Memory Service | `0.5.1-phase8f-fast2` |
| Production bank | `xiaowu-main` |
| `xiaowu-avatar` | schema 2 / visual system 1 / revision 20 |
| `xiaowu-visual` | 1.3.0 |
| `xiaowu-model-router` | 0.3.0 |
| `comfy-video-orchestrator` | 0.4.0 |
| Public repo initial commit | `3c2b51a6da3ebe7469f65546f0d2127b2fafc87b` |
| Public release | `v0.1.0` |

---

# 11. 结论

截至 2026-09-11，银月2号已经形成完整的单机 Local-first Agent 基础设施：

- Ubuntu 24.04.4 LTS 作为宿主系统；
- Intel Arc Pro B60 承载 262k context 主 Agent LLM；
- Intel Arc A770M 承载 Memory LLM 与 Embedding；
- Hermes v0.20.6 提供 Agent/Gateway/Skills/Plugins/MCP 能力；
- Hindsight 0.8.6 + PostgreSQL 18.3 + VectorChord/pgvector 构成长记忆底座；
- Xiaowu Memory Service 0.5.1-phase8f-fast2 负责生产 memory policy、retrieval 与 control plane；
- `xiaowu-avatar` / `xiaowu-visual` / `xiaowu-model-router` 已成为当前 active Xiaowu namespace；
- `comfy_3060` 是当前 Avatar production worker；
- revision 20 已现场证明生成、取回、状态提交与 delivery 完整闭环；
- sanitized reference architecture 已发布到 GitHub，并形成 `v0.1.0` 公开 Release。

当前最重要的后续工作不再是继续堆叠组件，而是把已经形成的生产架构进一步工程化：冻结 Hermes 本地改动、消除 mirror drift、统一 MCP registry 状态、补齐 isolated probes，并把后续每次生产变更纳入版本化 As-Built / GitHub release 流程。
