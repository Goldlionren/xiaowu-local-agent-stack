# llama.cpp deployment example

The audited deployment runs three system services from one SYCL-enabled llama.cpp build:

- main agent LLM on 10000 and Level Zero device 0;
- memory LLM on 10002 and Level Zero device 1;
- embedding model on 10001 and Level Zero device 1.

Use private absolute paths in the host env files. Do not place GGUF or mmproj files in this repository. The example unit calls a wrapper because systemd ExecStart does not perform normal shell variable expansion.

Build llama.cpp according to its upstream Intel SYCL documentation. The audited build enabled GGML_SYCL, SYCL DNN, FP16, graph support, and Level Zero API support.
