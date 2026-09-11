# Main LLM As-Built

## Main agent model

| Setting | Observed value |
|---|---|
| API | OpenAI compatible |
| Bind | 127.0.0.1:10000 |
| Alias | yinyue2 |
| Model | HauhauCS/Qwen3.6-35B-A3B-Uncensored-HauhauCS-Aggressive |
| Quantization | GGUF Q4_K_M |
| Multimodal projection | Present and enabled |
| Backend | llama.cpp with SYCL and Level Zero |
| llama.cpp commit | 4d9176092d00586775af140581bb0b558ddc4389 |
| Build | 0.4.0-dev build 1, IntelLLVM 2025.3.2 |
| GPU selector | Level Zero device 0, observed as Arc Pro B60 |
| GPU layers | 999 |
| Context | 262144 |
| Parallel slots | 1 |
| KV | unified; K and V cache q8_0 |
| Flash attention | on |
| Batch / ubatch | 1024 / 256 |
| Threads | 8 |
| Template | Jinja |
| mmproj offload | disabled |
| Fit | off |
| MTP | absent from the running command |

The live /v1/models response reported model ID yinyue2, 262144 context, 34,660,610,688 parameters, and Q4_K Medium. A short chat completion returned HTTP 200 in 0.449 seconds. Its timing block reported 74.09 predicted tokens per second. The eight-token response exhausted max_tokens while still in reasoning, so protocol and inference passed but exact text matching was not asserted.

## Dedicated memory LLM

The system service on loopback port 10002 uses alias yinyue2-hindsight, a Qwen3.5 9B Q8_0 GGUF, Level Zero device 1, 16384 context, one parallel slot, unified q8_0 KV, flash attention, and reasoning disabled.

## Embedding service

The system service on loopback port 10001 uses alias yinyue2-embedding, a Qwen3 Embedding 4B Q4_K_M GGUF, Level Zero device 1, 8192 context, last-token pooling, batch and ubatch 512. A live embedding request returned HTTP 200 and a 2560-dimensional vector.

Model files and mmproj files are not distributed.
