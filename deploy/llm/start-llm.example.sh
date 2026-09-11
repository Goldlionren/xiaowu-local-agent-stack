#!/usr/bin/env bash
set -euo pipefail

source "$ONEAPI_SETVARS" --force >/dev/null 2>&1
export ONEAPI_DEVICE_SELECTOR
export ZES_ENABLE_SYSMAN=1

exec "$LLAMA_SERVER_BIN" \
  --model "$MODEL_FILE" \
  --mmproj "$MMPROJ_FILE" \
  --alias "$MODEL_ALIAS" \
  --device SYCL0 \
  --n-gpu-layers 999 \
  --ctx-size "$LLAMA_CONTEXT" \
  --parallel 1 \
  --kv-unified \
  --cache-type-k q8_0 \
  --cache-type-v q8_0 \
  --flash-attn on \
  --batch-size 1024 \
  --ubatch-size 256 \
  --threads 8 \
  --jinja \
  --image-min-tokens 1024 \
  --no-mmproj-offload \
  --fit off \
  --host "$LLAMA_HOST" \
  --port "$LLAMA_PORT"
