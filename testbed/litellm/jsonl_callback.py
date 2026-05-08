"""LiteLLM callback that appends each completion event to a JSONL file.

The coordinator reads this file after a run to recover per-run token usage and
cost. Events carry the run's metadata (run_id, task, interface, iteration) so
multiple concurrent runs can share the same log.
"""
from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from litellm.integrations.custom_logger import CustomLogger

EVENTS_FILE = Path(__file__).resolve().parent.parent / ".local" / "litellm" / "events.jsonl"

_lock = threading.Lock()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _extract_metadata(kwargs: dict[str, Any]) -> dict[str, Any]:
    md = (kwargs.get("litellm_params") or {}).get("metadata") or {}
    if not md:
        md = kwargs.get("litellm_metadata") or {}
    if not md:
        md = kwargs.get("metadata") or {}
    if md.keys() == {"user_id"}:
        md = {}
    return md


def _serialize(obj):
    if obj is None:
        return None
    if hasattr(obj, "model_dump"):
        try:
            return obj.model_dump()
        except Exception:
            pass
    if hasattr(obj, "dict"):
        try:
            return obj.dict()
        except Exception:
            pass
    if isinstance(obj, (list, tuple)):
        return [_serialize(x) for x in obj]
    if isinstance(obj, dict):
        return {k: _serialize(v) for k, v in obj.items()}
    return obj


def _build_doc(kwargs, response_obj, start, end, status: str) -> dict:
    md = _extract_metadata(kwargs)
    usage = getattr(response_obj, "usage", None) if response_obj is not None else None
    duration = None
    try:
        duration = (end - start).total_seconds()
    except Exception:
        pass

    optional_params = kwargs.get("optional_params") or {}
    system = (
        optional_params.get("system")
        or kwargs.get("system")
        or kwargs.get("system_message")
    )
    tools = optional_params.get("tools") or kwargs.get("tools")

    error = None
    if status != "success":
        exc = (
            kwargs.get("exception")
            or kwargs.get("traceback_exception")
            or kwargs.get("error")
        )
        if exc is not None:
            error = str(exc)[:1000]

    return {
        "ts": _now(),
        "status": status,
        "model": kwargs.get("model"),
        "run_id": md.get("run_id"),
        "task": md.get("task"),
        "interface": md.get("interface"),
        "iteration": md.get("iteration"),
        "prompt_tokens": getattr(usage, "prompt_tokens", None) if usage else None,
        "completion_tokens": getattr(usage, "completion_tokens", None) if usage else None,
        "total_tokens": getattr(usage, "total_tokens", None) if usage else None,
        "response_cost": kwargs.get("response_cost"),
        "duration_s": duration,
        "error": error,
        "system": _serialize(system),
        "tools": _serialize(tools),
        "messages": _serialize(kwargs.get("messages")),
        "response": _serialize(response_obj),
    }


def _append(doc: dict) -> None:
    EVENTS_FILE.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(doc, default=str) + "\n"
    with _lock:
        with EVENTS_FILE.open("a") as f:
            f.write(line)


class JsonlLogger(CustomLogger):
    def log_success_event(self, kwargs, response_obj, start_time, end_time):
        _append(_build_doc(kwargs, response_obj, start_time, end_time, "success"))

    def log_failure_event(self, kwargs, response_obj, start_time, end_time):
        _append(_build_doc(kwargs, response_obj, start_time, end_time, "failure"))

    async def async_log_success_event(self, kwargs, response_obj, start_time, end_time):
        _append(_build_doc(kwargs, response_obj, start_time, end_time, "success"))

    async def async_log_failure_event(self, kwargs, response_obj, start_time, end_time):
        _append(_build_doc(kwargs, response_obj, start_time, end_time, "failure"))


jsonl_logger = JsonlLogger()
