# OS and hardware As-Built

| Item | Observed value | Evidence |
|---|---|---|
| OS | Ubuntu 24.04.4 LTS | /etc/os-release and hostnamectl |
| Kernel | 7.0.0-31-generic x86_64 | uname |
| CPU | Intel Core i7-12700H, 14 cores, 20 logical CPUs | lscpu |
| RAM | 62 GiB reported | free |
| Storage | 2 TB class NVMe with root and data partitions | lsblk and df |
| Main GPU | Intel Arc Pro B60 | sycl-ls and xpu-smi |
| Auxiliary GPU | Intel Arc A770M | sycl-ls and xpu-smi |
| Integrated GPU | Intel Iris Xe | sycl-ls and xpu-smi |
| oneAPI compiler | IntelLLVM 2025.3.2 | llama-server version |
| Level Zero runtime | Unified Runtime over Level Zero 1.14.37020+3 | sycl-ls |

The service launchers select Level Zero device 0 for the main LLM and device 1 for the memory LLM and embedding service. At audit time those selectors mapped to B60 and A770M respectively.

No packages or drivers were installed or changed during the audit.
