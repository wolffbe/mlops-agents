"""Read LiteLLM's JSONL event log and aggregate per-run usage."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
EVENTS_FILE = ROOT / ".local" / "litellm" / "events.jsonl"


def _iter_events(path: Path):
    if not path.exists():
        return
    with path.open() as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                continue


def query_run_usage(
    run_id: str | None = None,
    started_at: str | None = None,
    ended_at: str | None = None,
) -> dict[str, Any]:
    """Aggregate token/cost usage for a single run.

    Pass `run_id` if the agent forwards `litellm_metadata.run_id`. For Claude
    Code, which does not forward arbitrary metadata, pass the run's
    `started_at` / `ended_at` ISO timestamps and we'll filter by `ts` instead.
    """
    pt = ct = turns = 0
    cost = 0.0
    last_error: tuple[str, str] | None = None  # (ts, error_message)
    for doc in _iter_events(EVENTS_FILE):
        if run_id is not None:
            if doc.get("run_id") != run_id:
                continue
        else:
            ts = doc.get("ts")
            if not ts:
                continue
            if started_at and ts < started_at:
                continue
            if ended_at and ts > ended_at:
                continue
        turns += 1
        pt += int(doc.get("prompt_tokens") or 0)
        ct += int(doc.get("completion_tokens") or 0)
        c = doc.get("response_cost")
        if c is not None:
            try:
                cost += float(c)
            except (TypeError, ValueError):
                pass
        err = doc.get("error")
        if err:
            ts = doc.get("ts") or ""
            if last_error is None or ts > last_error[0]:
                last_error = (ts, str(err))
    return {
        "turns": turns,
        "prompt_tokens": pt,
        "completion_tokens": ct,
        "total_tokens": pt + ct,
        "cost": cost,
        "last_error": last_error[1] if last_error else "",
    }
