#!/usr/bin/env python3
"""Administrator-only recovery for an already-bound MCP transaction."""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))

import avatarctl
import mcp_executor


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("transaction_id")
    args = parser.parse_args()
    if not re.fullmatch(r"[0-9a-f]{32}", args.transaction_id, re.IGNORECASE):
        parser.error("transaction_id must be 32 hexadecimal characters")
    config = avatarctl.load_config()
    transaction = avatarctl.cmd_transaction_status(config, args.transaction_id)
    if transaction.get("status") in {"committed", "delivered", "delivery_failed"}:
        print(json.dumps({
            "ok": True,
            "transaction_id": transaction.get("transaction_id", ""),
            "prompt_id": transaction.get("prompt_id", ""),
            "status": transaction.get("status", ""),
            "submit_count": transaction.get("submit_count", 0),
            "commit_status": transaction.get("commit_status", ""),
            "delivery_status": transaction.get("delivery_status", ""),
            "result_image": transaction.get("result_image", ""),
        }, ensure_ascii=False))
        return 0
    if not transaction.get("prompt_id") or transaction.get("status") not in {
        "submitted", "waiting", "fetch_pending", "generation_completed",
    }:
        raise SystemExit("refusing recovery: transaction is not bound and resumable")

    from tools.mcp_tool import discover_mcp_tools
    from tools.registry import registry

    discover_mcp_tools()
    result = mcp_executor.execute_transaction(
        config,
        args.transaction_id,
        lambda name, values: registry.dispatch(name, values),
    )
    current = result.get("transaction", {})
    print(json.dumps({
        "ok": bool(result.get("ok")),
        "transaction_id": current.get("transaction_id", ""),
        "prompt_id": current.get("prompt_id", ""),
        "status": current.get("status", ""),
        "submit_count": current.get("submit_count", 0),
        "commit_status": current.get("commit_status", ""),
        "delivery_status": current.get("delivery_status", ""),
        "result_image": current.get("result_image", ""),
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
