# 银月2号 / 小舞（Xiaowu）TTS As-Built Addendum

**文档版本：** v0.1
**状态日期：** 2026-09-12（Australia/Sydney）
**状态：** PASS
**范围：** Faster-Qwen3-TTS + Hermes v0.20.6 + Telegram voice delivery
**公开边界：** 本文档仅记录 sanitized integration contract，不包含生产 voice sample、私有 voice profile、私网地址、token、个人路径或备份内容。

---

## 1. 目的

本文档是 `docs/AS-BUILT.md` 的 TTS 专项补充，记录 2026-09-12 已完成现场验证的 Xiaowu 语音输出链路。

当前已验证链路：

```text
Hermes v0.20.6
    |
    | OpenAI-compatible TTS
    v
Faster-Qwen3-TTS API
    |
    | Qwen3-TTS 24 kHz mono PCM
    v
FFmpeg / libopus
    |
    | Ogg / Opus
    v
Telegram native voice message
```

当前 TTS 节点仍为测试阶段的独立 Windows GPU 主机。后续可迁移到 24x7 专用节点，只需保持 OpenAI-compatible API contract 并修改 Hermes `base_url`。

---

## 2. TTS 节点运行基线

| 项目 | 已验证值 |
|---|---|
| OS | Windows 11 |
| GPU | NVIDIA RTX 4080 SUPER |
| PyTorch | `2.11.0+cu128` |
| Torch CUDA | 12.8 |
| GPU capability | 8.9 |
| Project | Faster-Qwen3-TTS |
| Upstream project version | 0.4.0 |
| Upstream baseline commit | `e2a215f61984c0e72a242f8dd72333338e7672f4` |
| TTS model | `Qwen/Qwen3-TTS-12Hz-0.6B-Base` |
| `qwen-tts-hf` | `0.1.1.post1` |
| `transformers` | `5.15.1` pinned |
| API style | OpenAI-compatible |
| API port | `18000` |
| Voice id | `xiaowu` |
| Model output sample rate | 24 kHz |

### Dependency constraint

`transformers==5.15.1` is part of the current known-good environment. A later tested `5.17.0` build caused a configuration incompatibility (`MimiConfig` / `rope_theta`), therefore the TTS environment must not be upgraded blindly.

SoX is not required for the current validated voice-clone/API path. FFmpeg with `libopus` support is required for Hermes/Telegram Opus output.

---

## 3. Faster-Qwen3-TTS local modifications

The production test instance contains local compatibility changes on top of upstream `v0.4.0`.

### 3.1 Windows UTF-8 voice profile loading

The OpenAI-compatible server explicitly opens the voice registry as UTF-8 so Windows locale defaults do not corrupt non-ASCII voice metadata.

### 3.2 Voice-profile `instruct` propagation

Voice-level `instruct` is propagated through both streaming and non-streaming voice-clone generation paths.

The production voice profile itself is private and is not part of this public repository.

### 3.3 OpenAI-compatible `response_format=opus`

Hermes uses `.ogg` for Telegram auto-TTS and maps the output extension to:

```text
response_format = opus
```

The Faster-Qwen3-TTS server was extended to support that request directly.

Validated conversion path:

```text
Qwen3-TTS float/PCM output
    |
    v
PCM16LE, 24 kHz, mono
    |
    v
FFmpeg + libopus
    |
    v
Ogg / Opus
```

The encoder uses `libopus`; the resulting stream is a real Ogg/Opus container rather than an MP3 file renamed to `.ogg`.

Current known-good modified server SHA-256:

```text
2C293DB76E293145FB28892502551172BC1E5595A043278E174F556921C9898F
```

Pre-Opus known-good server SHA-256:

```text
26848025D268080A254D37A4E4B9C6D484A7E22A26EE8A1564EF324D1D986895
```

### 3.4 FFmpeg process visibility

The Windows API launcher must make FFmpeg visible to the API process, not merely to an interactive PowerShell session. The validated deployment adds the FFmpeg directory to the launcher process `PATH` before starting Python.

The public reference repository does not publish the production launcher path or private host address.

---

## 4. Output-format regression matrix

After adding Opus, all original output formats were regression-tested.

| `response_format` | Status | Verified output |
|---|---|---|
| `wav` | PASS | PCM S16LE, 24 kHz, mono, WAV container |
| `pcm` | PASS | raw PCM S16LE, 24 kHz, mono |
| `mp3` | PASS | MP3, 24 kHz, mono |
| `opus` | PASS | Ogg/Opus, mono; decoder clock reports 48 kHz |

### Opus verification evidence

`ffprobe` returned:

```text
codec_name=opus
codec_type=audio
sample_rate=48000
channels=1
format_name=ogg
```

The 48 kHz value is expected for Opus playback/decoder timing and does not mean the Qwen model changed its native 24 kHz generation rate.

### PCM verification evidence

The raw PCM regression file was parsed by FFmpeg as:

```text
pcm_s16le
24000 Hz
mono
s16
```

No regression was found in WAV, PCM or MP3 after adding Opus.

---

## 5. Runtime performance observation

A validated Opus request generated approximately 5.58 seconds of audio in approximately 1.50 seconds after CUDA graph preparation:

```text
RTF ~= 3.72
```

This is an observation from the temporary RTX 4080 SUPER test host, not a guaranteed production SLA.

---

## 6. Hermes v0.20.6 integration

Hermes source audit confirmed native support for OpenAI-compatible TTS through `tts.openai`.

The OpenAI TTS implementation supports:

```text
base_url
api_key
model
voice
speed
instructions
response_format
```

Hermes maps output extension to OpenAI-compatible response format:

```text
.ogg  -> opus
.wav  -> wav
.flac -> flac
other -> mp3
```

For Telegram, Hermes creates an `.ogg` auto-TTS output path, therefore the TTS backend receives `response_format=opus`.

### Sanitized Hermes configuration

```yaml
tts:
  provider: openai
  edge:
    voice: zh-CN-XiaoxiaoNeural
    speed: 1
  openai:
    api_key: local
    base_url: http://<TTS_HOST>:18000/v1
    model: tts-1
    voice: xiaowu
    speed: 1.0
  use_gateway: false

voice:
  auto_tts: false
```

`api_key: local` is a dummy non-secret value required by Hermes' direct OpenAI-provider credential selection path. The local TTS endpoint does not use it for authentication.

The existing Edge block is retained as configuration history/optional manual provider selection. There is no automatic fallback to Edge in the validated Xiaowu path.

---

## 7. Voice-mode behavior

Hermes v0.20.6 command semantics were verified from source and runtime behavior.

| Command | Internal mode | Behavior |
|---|---|---|
| `/voice on` | `voice_only` | voice replies when the inbound message is voice |
| `/voice tts` | `all` | TTS for normal text replies and voice-input replies |
| `/voice off` | `off` | text only |
| `/voice status` | read-only | show current chat voice mode |

Current operational policy:

```text
Default: voice off
TTS host confirmed online: /voice tts
TTS host intentionally offline: /voice off
```

`voice.auto_tts` remains `false`, so gateway startup does not globally enable TTS.

---

## 8. Failure behavior

Hermes v0.20.6 source inspection confirmed TTS generation/delivery exceptions are caught and logged instead of terminating the text-response path.

Current temporary-host operations rely on manual voice-mode control: if the temporary TTS node is known to be offline, voice mode remains off.

A future 24x7 dedicated TTS host may remove the need for this operational convention without changing the integration contract.

---

## 9. Privacy and public-release boundary

The following are deliberately excluded from public source and documentation:

- production voice sample audio;
- production voice-clone reference text;
- private `voices.json` / voice-profile file;
- private voice-style instructions;
- private LAN addresses;
- local Windows paths;
- production backup directories;
- generated test audio;
- API/session tokens;
- personal identifiers.

Public examples must use placeholders such as:

```text
<TTS_HOST>
<VOICE_ID>
<MODEL_ID>
```

---

## 10. Rollback baseline

The production change was performed transactionally with file-level backups and SHA-256 checks before modification.

### Faster-Qwen3-TTS rollback principle

Restore the pre-change `openai_server.py` and launcher from the known-good backup, then run Python syntax validation before restart.

The private backup path is intentionally not published.

### Hermes rollback principle

Before the TTS provider switch, `~/.hermes/config.yaml` was copied to a timestamped known-good backup and the SHA-256 hashes were confirmed identical.

Rollback pattern:

```bash
cp -a "$HOME/.hermes/config.yaml.pre-xiaowu-tts-<TIMESTAMP>" \
      "$HOME/.hermes/config.yaml" && \
hermes gateway restart
```

The exact production backup filename is private operational state and is not included here.

---

## 11. Current status

| Component | Status |
|---|---|
| Faster-Qwen3-TTS API | PASS |
| custom `xiaowu` voice profile | PASS; private profile |
| Windows UTF-8 profile loading | PASS |
| voice-level `instruct` propagation | PASS |
| FFmpeg discovery in API process | PASS |
| `response_format=opus` | PASS |
| Ogg/Opus validation | PASS |
| WAV regression | PASS |
| PCM regression | PASS |
| MP3 regression | PASS |
| Hermes OpenAI-compatible provider | PASS |
| Hermes Telegram `.ogg` / `opus` selection | PASS |
| `/voice off` | PASS |
| `/voice on` | PASS |
| `/voice tts` | PASS |
| Telegram Xiaowu voice delivery | PASS |

The complete deployment and rollback procedure is maintained in `docs/TTS-HERMES-OPENAI-OPUS-SOP.md`.
