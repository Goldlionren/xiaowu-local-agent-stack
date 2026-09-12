# Xiaowu TTS / Hermes OpenAI-Compatible Ogg-Opus Integration SOP

**Scope:** Faster-Qwen3-TTS, Hermes v0.20.6, Telegram native voice delivery
**Status:** production-tested on 2026-09-12
**Change policy:** backup -> hash -> patch -> syntax/config validation -> runtime test -> regression -> freeze

---

## 1. Architecture

```text
Hermes
  |
  | POST /v1/audio/speech
  | OpenAI-compatible
  | response_format=opus
  v
Faster-Qwen3-TTS
  |
  | voice clone
  | 24 kHz mono
  v
FFmpeg / libopus
  |
  | Ogg/Opus
  v
Telegram native voice
```

Hermes is not patched for this integration. The backend is made compatible with the format Hermes already requests.

---

## 2. Known-good TTS software baseline

```text
Faster-Qwen3-TTS upstream: v0.4.0
upstream commit:
e2a215f61984c0e72a242f8dd72333338e7672f4

model:
Qwen/Qwen3-TTS-12Hz-0.6B-Base

PyTorch:
2.11.0+cu128

Torch CUDA:
12.8

qwen-tts-hf:
0.1.1.post1

transformers:
5.15.1
```

Do not blindly upgrade `transformers`; a tested 5.17.0 environment was incompatible with the current TTS stack.

---

## 3. Private-data boundary

Never publish:

```text
production voices.json
reference audio
reference transcript
private instruct text
private LAN IP
local Windows paths
backup directories
generated test audio
tokens / credentials
```

Use placeholders in public examples.

---

## 4. Pre-change backup rule

Before touching the OpenAI-compatible server:

1. stop the API;
2. copy the target file to an external backup directory;
3. compare SHA-256;
4. write down the exact rollback command;
5. only then patch.

Known-good pre-Opus SHA-256:

```text
26848025D268080A254D37A4E4B9C6D484A7E22A26EE8A1564EF324D1D986895
```

Known-good post-Opus SHA-256:

```text
2C293DB76E293145FB28892502551172BC1E5595A043278E174F556921C9898F
```

---

## 5. Required Faster-Qwen3-TTS compatibility changes

### 5.1 UTF-8 voice registry

Open the voice JSON file explicitly as UTF-8 on Windows.

### 5.2 Voice `instruct`

Forward:

```python
instruct=voice_cfg.get("instruct")
```

through both streaming and non-streaming voice-clone paths.

### 5.3 Opus output

Add support for:

```text
response_format=opus
```

with MIME:

```text
audio/ogg
```

Encode:

```text
PCM16LE -> FFmpeg -> libopus -> Ogg
```

A validated encoder profile is:

```text
codec: libopus
bitrate: 32k
VBR: on
application: voip
compression_level: 10
container: ogg
```

### 5.4 FFmpeg runtime PATH

The API launcher must make FFmpeg visible to the Python server process.

A generic Windows launcher pattern:

```bat
@echo off
setlocal
cd /d <FASTER_QWEN3_TTS_ROOT>
set "PATH=<FFMPEG_BIN>;%PATH%"
set PYTHONUTF8=1

.venv\Scripts\python.exe examples\openai_server.py ^
  --model Qwen/Qwen3-TTS-12Hz-0.6B-Base ^
  --voices <PRIVATE_VOICES_JSON> ^
  --host <TTS_HOST> ^
  --port 18000
```

Do not publish the production values substituted for the placeholders.

---

## 6. Static validation

Before starting the service:

```powershell
.\.venv\Scripts\python.exe -m py_compile `
  .\examples\openai_server.py
```

Expected: no output and exit code 0.

Confirm FFmpeg and libopus:

```powershell
Get-Command ffmpeg
ffmpeg -hide_banner -encoders | Select-String "opus"
```

Expected encoder availability includes `libopus`.

---

## 7. Health check

After startup:

```text
GET /health
```

Expected:

```json
{"status":"ok","model_loaded":true}
```

The server may emit a SoX warning in environments without SoX. SoX is not required for the currently validated path.

Hugging Face `404` probes for optional config files are not service failures when the model proceeds to `Model ready` and Uvicorn starts successfully.

---

## 8. Opus functional test

Request:

```json
{
  "model": "tts-1",
  "input": "Opus integration test.",
  "voice": "<VOICE_ID>",
  "response_format": "opus"
}
```

Validate with:

```powershell
ffprobe `
  -v error `
  -show_entries stream=codec_name,codec_type,sample_rate,channels `
  -show_entries format=format_name,duration,size `
  -of default=noprint_wrappers=1 `
  .\opus_test.ogg
```

Expected:

```text
codec_name=opus
codec_type=audio
sample_rate=48000
channels=1
format_name=ogg
```

Opus reporting 48 kHz is expected. Qwen remains a 24 kHz generation source.

---

## 9. Regression tests

### WAV

Expected:

```text
codec_name=pcm_s16le
sample_rate=24000
channels=1
format_name=wav
```

### MP3

Expected:

```text
codec_name=mp3
sample_rate=24000
channels=1
format_name=mp3
```

### PCM

Raw PCM should parse when explicitly described to FFmpeg as:

```text
s16le
24000 Hz
mono
```

All four output formats must pass before the TTS backend is considered Hermes-ready.

---

## 10. Hermes source-compatibility audit

Before editing Hermes configuration, verify the installed Hermes version actually supports:

```text
tts.openai.base_url
tts.openai.api_key
tts.openai.model
tts.openai.voice
response_format from output extension
Telegram .ogg output path
/voice on
/voice tts
/voice off
```

The validated Hermes version is:

```text
Hermes Agent v0.20.6
HEAD:
26350357d76e4508c8df9304a3374bdc5a6f6220
```

Do not assume future Hermes versions retain identical behavior without re-auditing.

---

## 11. Hermes configuration

### 11.1 Backup first

```bash
CONFIG="$HOME/.hermes/config.yaml"
STAMP="$(date +%Y%m%d-%H%M%S)"
BACKUP="$HOME/.hermes/config.yaml.pre-xiaowu-tts-$STAMP"

cp -a "$CONFIG" "$BACKUP"

sha256sum "$CONFIG"
sha256sum "$BACKUP"

echo "ROLLBACK:"
echo "cp -a '$BACKUP' '$CONFIG' && hermes gateway restart"
```

Hashes must match.

### 11.2 Sanitized target configuration

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
    voice: <VOICE_ID>
    speed: 1.0
  use_gateway: false

voice:
  auto_tts: false
```

`api_key: local` is a dummy value for a trusted local endpoint. Do not copy this pattern to an Internet-exposed service that requires real authentication.

### 11.3 Validate YAML before restart

```bash
"$HOME/.hermes/hermes-agent/venv/bin/python" - <<'PY'
from pathlib import Path
import yaml

path = Path.home() / ".hermes" / "config.yaml"
data = yaml.safe_load(path.read_text(encoding="utf-8"))

print("YAML PARSE: PASS")
print("tts.provider =", data.get("tts", {}).get("provider"))
print("tts.openai.base_url =", data.get("tts", {}).get("openai", {}).get("base_url"))
print("tts.openai.model =", data.get("tts", {}).get("openai", {}).get("model"))
print("tts.openai.voice =", data.get("tts", {}).get("openai", {}).get("voice"))
print("voice.auto_tts =", data.get("voice", {}).get("auto_tts"))
PY
```

### 11.4 Restart

```bash
hermes gateway restart
sleep 3
hermes gateway status
```

---

## 12. `/voice` operating modes

```text
/voice on
```

Means `voice_only`: TTS is used when the inbound message is voice.

```text
/voice tts
```

Means `all`: normal text replies and voice-input replies can be sent with TTS.

```text
/voice off
```

Means text only.

```text
/voice status
```

Displays the current chat mode.

Recommended temporary-node policy:

```text
gateway default: voice.auto_tts=false
TTS host online: /voice tts
TTS host offline: /voice off
```

---

## 13. End-to-end validation

With the TTS server running:

1. issue `/voice status`;
2. issue `/voice tts`;
3. send an ordinary text message;
4. confirm the TTS host receives `/v1/audio/speech`;
5. confirm HTTP 200;
6. confirm Telegram receives a native voice message;
7. confirm normal text delivery remains present;
8. issue `/voice off`;
9. send another text message;
10. confirm no TTS request is sent.

PASS requires both voice delivery and correct off/on behavior.

---

## 14. Failure behavior

Hermes catches auto-TTS exceptions and logs them rather than terminating the normal text-response path.

For the current temporary TTS node, operational policy is still to leave voice off while the node is intentionally offline.

No automatic fallback to Edge TTS is configured for Xiaowu.

---

## 15. Rollback

### Hermes

Use the exact backup filename recorded before the change:

```bash
cp -a "$HOME/.hermes/config.yaml.pre-xiaowu-tts-<TIMESTAMP>" \
      "$HOME/.hermes/config.yaml" && \
hermes gateway restart
```

### Faster-Qwen3-TTS

1. stop API;
2. restore known-good backed-up server/launcher;
3. run `py_compile`;
4. restart;
5. run `/health`;
6. retest WAV or the previous expected format.

Never use a placeholder backup path in an actual rollback command.

---

## 16. Migration to a dedicated 24x7 TTS host

Keep the same public contract:

```text
POST /v1/audio/speech
voice=<VOICE_ID>
response_format=opus
```

Then change only:

```yaml
tts:
  openai:
    base_url: http://<NEW_TTS_HOST>:18000/v1
```

Retest:

```text
health
opus
/voice tts
Telegram voice
/voice off
```

The target production design may use an RTX 5060 Ti 16 GB host; that hardware choice is not required by the API contract.

---

## 17. Public repository rules

A public Faster-Qwen3-TTS derivative/fork must retain the upstream MIT license and attribution.

Do not commit:

```text
xiaowu_voices.json
reference audio
private backups
generated WAV/MP3/PCM/OGG
private LAN addresses
local production launcher
.venv
model files
```

Publish instead:

```text
voices.example.json
start_tts_api.example.bat
integration documentation
generic placeholder configuration
```

Run a secret/privacy review before the first push.
