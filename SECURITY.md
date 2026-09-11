# Security

Do not report real credentials in a public issue. Use GitHub private vulnerability reporting when available.

This repository excludes production state, memory rows, chats, images, transactions, environment files, private keys, model weights, and host-specific network configuration. Examples bind sensitive APIs to loopback by default.

Before publishing a fork, run:

~~~bash
./scripts/audit/secret-scan.sh .
~~~

Review every staged file manually. Treat a clean pattern scan as one control, not proof that content is appropriate to publish.
