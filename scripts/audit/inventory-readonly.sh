#!/usr/bin/env bash
set -u

uname -a
cat /etc/os-release
lscpu
free -h
lsblk
df -h
lspci -nn
ip -brief addr
systemctl --user list-units --type=service --all --no-pager
systemctl list-units --type=service --all --no-pager
ss -lntup
docker ps -a
docker images --digests

# Review output locally. It may contain host-specific identifiers.
