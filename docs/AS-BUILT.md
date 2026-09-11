# Sanitized As-Built

Audit timestamp: 2026-09-11, Australia/Sydney.

This document records the observed Xiaowu deployment without publishing the hostname, account name, private addresses, destinations, secrets, personal assets, or runtime data.

| Plane | Observed implementation |
|---|---|
| Host | Ubuntu 24.04.4 LTS, Linux 7.0.0-31-generic, Intel Core i7-12700H, 64 GiB class RAM |
| GPU | Intel Arc Pro B60, Arc A770M, integrated Iris Xe |
| Main inference | llama.cpp SYCL build at commit 4d91760, model alias yinyue2, loopback port 10000 |
| Agent | Hermes Agent v0.20.6, user systemd gateway, custom provider yinyue2-local |
| Memory | Xiaowu Memory Service 0.5.1-phase8f-fast2, Hindsight 0.8.6, PostgreSQL 18.3 |
| Memory bank | xiaowu-main; single user and single agent domain |
| Visual | xiaowu-avatar skill, xiaowu-visual 1.3.0, xiaowu-model-router 0.3.0 |
| Remote execution | comfy_3060 active for the avatar workflow; comfy_5090 disabled in the workflow registry |

The production model and service aliases retain yinyue2 names. They are active compatibility and deployment identifiers, not evidence that the identity migration failed.

The previous 2026-09-10 As-Built described the Memory Service as 0.5.0-phase8f-fast1 and the visual components under yinyue names. The live 2026-09-11 system reports 0.5.1-phase8f-fast2 and exposes the Xiaowu skill, plugin, router, tool, Identity Studio namespace, and state root.

See the component documents and verification matrix for evidence and limitations.
