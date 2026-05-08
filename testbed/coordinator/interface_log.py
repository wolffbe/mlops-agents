"""Append-only per-run log of every Hopsworks interface call.

One JSON object per line. Used by all three interfaces so the coordinator can
count and compare CLI / SDK / MCP usage uniformly across runs.

Each interface plugs in like this:

    CLI:
        A `hops` shim binary (./scripts/hops_shim.sh, on PATH ahead of the real
        binary when --interface cli) records argv before execv-ing the real
        `hops` from .venv. The shim writes via this module's `record_cli`.

    SDK:
        `hopsworks_logged` (a thin proxy module that the agent imports instead
        of `hopsworks`) intercepts attribute access on the SDK and times each
        call before delegating, writing via `record_sdk`.

    MCP:
        The `hops-mcp` server wraps each tool's handler with a decorator that
        calls `record_mcp` on entry/exit.

The file path is read from $INTERFACE_LOG_FILE (set by the coordinator per
run); $RUN_ID provides the tag.
"""
from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_lock = threading.Lock()


def _file() -> Path:
    p = os.environ.get("INTERFACE_LOG_FILE")
    return Path(p) if p else Path.cwd() / "interface_calls.jsonl"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def record(
    *,
    interface: str,
    operation: str,
    args: dict[str, Any] | None = None,
    duration_s: float | None = None,
    status: str = "success",
    result_summary: str | None = None,
    error: str | None = None,
    run_id: str | None = None,
) -> None:
    doc = {
        "ts": _now(),
        "run_id": run_id or os.environ.get("RUN_ID"),
        "interface": interface,
        "operation": operation,
        "args": args or {},
        "duration_s": duration_s,
        "status": status,
        "result_summary": result_summary,
        "error": error,
    }
    path = _file()
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(doc, default=str) + "\n"
    with _lock:
        with path.open("a") as f:
            f.write(line)


def record_cli(operation: str, args: dict, duration_s: float, exit_code: int) -> None:
    record(
        interface="cli",
        operation=operation,
        args=args,
        duration_s=duration_s,
        status="success" if exit_code == 0 else "error",
        error=f"exit={exit_code}" if exit_code != 0 else None,
    )


def record_sdk(operation: str, args: dict, duration_s: float, error: str | None = None) -> None:
    record(
        interface="sdk",
        operation=operation,
        args=args,
        duration_s=duration_s,
        status="error" if error else "success",
        error=error,
    )


def record_mcp(operation: str, args: dict, duration_s: float, error: str | None = None) -> None:
    record(
        interface="mcp",
        operation=operation,
        args=args,
        duration_s=duration_s,
        status="error" if error else "success",
        error=error,
    )


def count_calls(path: Path) -> dict[str, int]:
    """Aggregate calls in a JSONL file by interface."""
    out = {"total": 0, "cli": 0, "sdk": 0, "mcp": 0, "errors": 0}
    if not path.exists():
        return out
    with path.open() as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                doc = json.loads(line)
            except json.JSONDecodeError:
                continue
            out["total"] += 1
            iface = doc.get("interface")
            if iface in out:
                out[iface] += 1
            if doc.get("status") == "error":
                out["errors"] += 1
    return out
