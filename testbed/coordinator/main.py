"""Experiment coordinator.

Each invocation runs ONE (model, skill, interface, task) cell against a
workspace at ``results/<model>/<skill>/<interface>/<task>/<rep>/``. The CSV
row uses ``(model, skill, interface, task, rep)`` as identity. ``rep`` is
auto-assigned to the lowest free slot in ``0..--reps-1``; if all slots are
already filled, the run is skipped — so re-running ``make task*`` is
idempotent and only fills missing cells. Drop rows from ``results.csv`` (or
``make clean``) to re-measure. Skill identity is ``<file_id>@<short_hash>``
so silent edits to a skill file show up as new cells.

The coordinator spawns Claude Code as a subprocess pointed at the workspace.
After it exits, it scrapes LiteLLM events for tokens/cost (filtered by
timestamp range), grades the produced submission, extracts the agent's bash
history from the stream-json log, and upserts one row in ``results.csv``.
"""
from __future__ import annotations

import argparse
import atexit
import csv
import hashlib
import json
import os
import re
import shlex
import shutil
import signal
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from dotenv import dotenv_values, load_dotenv

from coordinator.usage_client import query_run_usage
from coordinator.grading import grade_run
from coordinator.interface_log import count_calls

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")
RESULTS_DIR = ROOT / "results"
RESULTS = RESULTS_DIR / "results.csv"
RUNS = RESULTS_DIR              # workspaces live at results/<model>/<interface>/<skill>/<task>/<rep>/
PROMPTS = ROOT / "prompts"
SKILLS_DIR = PROMPTS / "skills"
INTERFACES = ROOT / "interfaces"
CLAUDE_SETTINGS = ROOT / ".claude" / "settings.json"
AGENT_ENV = ROOT / ".agent.env"
MLE_DATA = Path(os.environ.get("MLE_BENCH_DATA_DIR", ROOT / ".local" / "mle-bench-data"))
COORDINATOR_PID = ROOT / ".local" / "coordinator.pid"
AGENT_PGID_FILE = ROOT / ".local" / "agent.pgid"


def _kill_leftover_agent() -> None:
    """Kill any agent process group left over from a coordinator that was killed
    before it could clean up (e.g. SIGKILL bypasses the normal killpg call)."""
    if not AGENT_PGID_FILE.exists():
        return
    try:
        pgid = int(AGENT_PGID_FILE.read_text().strip())
        os.killpg(pgid, signal.SIGTERM)
    except (ValueError, ProcessLookupError, PermissionError, OSError):
        pass
    finally:
        AGENT_PGID_FILE.unlink(missing_ok=True)


def _acquire_coordinator_lock() -> None:
    """Kill any running coordinator instance (and its parent make loop), then
    write our own PID+PGID. Ensures at most one experiment runs at a time."""
    _kill_leftover_agent()
    COORDINATOR_PID.parent.mkdir(parents=True, exist_ok=True)
    if COORDINATOR_PID.exists():
        try:
            parts = COORDINATOR_PID.read_text().strip().split()
            old_pid = int(parts[0])
            old_pgid = int(parts[1]) if len(parts) > 1 else old_pid
            os.kill(old_pid, 0)  # check it's alive
            print(f"[coordinator] killing existing instance (pid {old_pid}, pgid {old_pgid})")
            try:
                os.killpg(old_pgid, signal.SIGTERM)
            except (ProcessLookupError, PermissionError):
                os.kill(old_pid, signal.SIGTERM)
            for _ in range(20):   # wait up to 2s
                time.sleep(0.1)
                try:
                    os.kill(old_pid, 0)
                except ProcessLookupError:
                    break
            else:
                try:
                    os.killpg(old_pgid, signal.SIGKILL)
                except (ProcessLookupError, PermissionError):
                    pass
        except (ValueError, ProcessLookupError, PermissionError):
            pass
    COORDINATOR_PID.write_text(f"{os.getpid()} {os.getpgid(os.getpid())}")


def _release_coordinator_lock() -> None:
    try:
        if COORDINATOR_PID.exists():
            parts = COORDINATOR_PID.read_text().strip().split()
            if parts and parts[0] == str(os.getpid()):
                COORDINATOR_PID.unlink()
    except OSError:
        pass

# Identity columns form the row key. Repeats overwrite via FIFO eviction:
# when REPS slots are full for a given (model, interface, task, skill),
# the oldest slot is replaced. `skill` is `<file_id>@<short_hash>` (empty
# if no skill), so version bumps and silent edits both produce new identity.
KEY_FIELDS = ["model", "skill", "interface", "task", "rep"]
FIELDS = [
    *KEY_FIELDS,
    "started_at",
    "duration",
    "duration_s",
    "turns",
    "stopped_by",
    "prompt_tokens",
    "completion_tokens",
    "total_tokens",
    "cost_usd",
    "interface_calls",
    "interface_call_errors",
    "cli",                   # individual project CLI commands across all Bash calls
    "sdk",                   # non-empty lines in workspace .py files that import the project SDK
    "mcp",                   # MCP tool calls for the project server
    "bash",                  # individual non-CLI non-Python shell commands
    "python",                # non-empty lines in workspace .py files that do not import the SDK
    "error",
    "grade",
    "valid_submission",
    "submission_exists",
    "any_medal",
    "gold_medal",
    "silver_medal",
    "bronze_medal",
    "above_median",
    "task_path",
]

_PYTHON_TOKEN_RE = re.compile(r"(?:^|\s)(?:python|python3)(?=\s|$)")


def _split_shell_commands(cmd: str) -> list[str]:
    """Split a compound shell command by &&, ||, ; into individual sub-commands."""
    parts = re.split(r"\s*(?:&&|\|\||;)\s*", cmd)
    return [p.strip() for p in parts if p.strip()]


def _bash_first_token(cmd: str) -> str | None:
    """Return the first executable token, skipping env-var prefixes and cd/export."""
    try:
        tokens = shlex.split(cmd)
    except ValueError:
        return None
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        if "=" in tok and tok.split("=", 1)[0].isidentifier():
            i += 1
            continue
        if tok in ("cd", "export", "set", "unset"):
            i += 2
            continue
        if tok in ("&&", "||", ";", "|"):
            i += 1
            continue
        return tok.split("/")[-1]
    return None


def _extract_inline_pythons(cmd: str) -> list[str]:
    """Pull every `python -c '<src>'` or `python3 -c "<src>"` snippet out of a
    shell command and return the source strings."""
    try:
        tokens = shlex.split(cmd)
    except ValueError:
        return []
    out = []
    i = 0
    while i < len(tokens) - 1:
        tok = tokens[i]
        if tok in ("python", "python3") or tok.endswith("/python") or tok.endswith("/python3"):
            if tokens[i + 1] == "-c" and i + 2 < len(tokens):
                out.append(tokens[i + 2])
                i += 3
                continue
        i += 1
    return out


def _hms(seconds: float | int | None) -> str:
    if seconds is None:
        return ""
    total = int(round(float(seconds)))
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


def _render_agent_md(workspace: Path, events: list[dict]) -> None:
    """Render `<workspace>/agent.md` — a human-readable transcript of the
    Claude Code session. The raw stream-json stays in `agent.jsonl` for
    parsing; this file is for skim-reading."""
    out = workspace / "agent.md"
    lines: list[str] = []
    turn = 0
    for evt in events:
        et = evt.get("type")
        msg = evt.get("message") or {}
        if et == "user":
            content = msg.get("content")
            if isinstance(content, str):
                turn += 1
                lines.append(f"\n## turn {turn} — user\n\n{content.strip()}\n")
            elif isinstance(content, list):
                for block in content:
                    if not isinstance(block, dict):
                        continue
                    if block.get("type") == "tool_result":
                        c = block.get("content")
                        if isinstance(c, list):
                            c = "".join(p.get("text", "") for p in c if isinstance(p, dict))
                        c = (c or "").strip()
                        if c:
                            preview = c if len(c) <= 1500 else c[:1500] + "\n…[truncated]"
                            lines.append(f"\n_tool result_:\n```\n{preview}\n```\n")
        elif et == "assistant":
            content = msg.get("content") or []
            for block in content:
                if not isinstance(block, dict):
                    continue
                btype = block.get("type")
                if btype == "text":
                    text = (block.get("text") or "").strip()
                    if text:
                        lines.append(f"\n**assistant**: {text}\n")
                elif btype == "tool_use":
                    name = block.get("name") or "?"
                    inp = block.get("input") or {}
                    if name == "Bash":
                        desc = inp.get("description", "")
                        cmd = inp.get("command", "")
                        lines.append(f"\n`Bash` {desc}\n```bash\n{cmd}\n```\n")
                    else:
                        # Compact one-line summary for non-Bash tools.
                        keys = ", ".join(f"{k}={str(v)[:60]}" for k, v in inp.items())
                        lines.append(f"\n`{name}` ({keys})\n")
        elif et == "result":
            cost = evt.get("total_cost_usd")
            duration = evt.get("duration_ms")
            if cost is not None or duration is not None:
                lines.append(
                    f"\n---\n_session ended_ — cost: ${cost:.4f}  "
                    f"duration: {duration}ms\n" if cost is not None
                    else "\n---\n_session ended_\n"
                )
    out.write_text("# Agent transcript\n" + "".join(lines))


def _python_imports_project(source: str, project: str) -> bool:
    if not project:
        return False
    # Match `import <project>`, `from <project>`, `<project>.something(`.
    pat = re.compile(
        r"(?:^|\s|;)(?:import|from)\s+" + re.escape(project) + r"(?:\.|\s|$)"
        r"|(?:^|\s|;)" + re.escape(project) + r"\."
    )
    return bool(pat.search(source))


def extract_bash_commands(workspace: Path, project: str = "") -> dict:
    """Phase 1 — write workspace artifacts from agent.jsonl:
      - `commands.sh`           — one block per Bash tool invocation
      - `mcp_calls.txt`         — one line per MCP tool_use for this project
      - `inline_python/NNN.py`  — extracted `python -c '…'` snippets
      - `agent.md`              — human-readable transcript

    Phase 2 — count from those workspace files (never from the raw log):
      cli    — sub-commands in commands.sh whose first token is the project binary
      bash   — sub-commands in commands.sh that are neither CLI nor Python invocations
      mcp    — lines in mcp_calls.txt
      sdk    — non-comment LOC in workspace .py files that import the project SDK
      python — non-comment LOC in workspace .py files that do not import the SDK

    Deduplicates partial assistant messages (--include-partial-messages emits the
    same message multiple times as it streams; only the last version is kept).
    """
    out_counts: dict[str, int] = {"cli": 0, "sdk": 0, "mcp": 0, "bash": 0, "python": 0}
    log_path = workspace / "agent.jsonl"
    if not log_path.exists():
        return out_counts

    mcp_prefix = f"mcp__{project}__" if project else None
    inline_dir = workspace / "inline_python"
    inline_idx = 0
    commands_path = workspace / "commands.sh"
    mcp_path = workspace / "mcp_calls.txt"

    try:
        raw_lines = log_path.read_text().splitlines()
    except (OSError, UnicodeDecodeError):
        return out_counts

    # Parse and deduplicate: partial assistant events share a message id and grow
    # as content streams in. Keep only the last (most complete) version of each.
    raw_events: list[dict] = []
    for line in raw_lines:
        line = line.strip()
        if not line or not line.startswith("{"):
            continue
        try:
            raw_events.append(json.loads(line))
        except json.JSONDecodeError:
            continue

    seen: dict[str, int] = {}  # message id → index in `events`
    events: list[dict] = []
    for evt in raw_events:
        if evt.get("type") == "assistant":
            msg_id = (evt.get("message") or {}).get("id")
            if msg_id:
                if msg_id in seen:
                    events[seen[msg_id]] = evt
                else:
                    seen[msg_id] = len(events)
                    events.append(evt)
                continue
        events.append(evt)

    # --- Phase 1: collect then write workspace artifacts (only if non-empty) ---
    bash_blocks: list[str] = []
    mcp_calls: list[str] = []

    for evt in events:
        if evt.get("type") != "assistant":
            continue
        for block in (evt.get("message") or {}).get("content") or []:
            if not isinstance(block, dict) or block.get("type") != "tool_use":
                continue
            tool_name = block.get("name") or ""
            inp = block.get("input") or {}

            if mcp_prefix and tool_name.startswith(mcp_prefix):
                mcp_calls.append(tool_name)
                continue

            if tool_name != "Bash":
                continue

            cmd = inp.get("command")
            if not cmd:
                continue
            desc = inp.get("description", "")
            bash_blocks.append(f"# {desc}\n{cmd}\n\n")

            for src in _extract_inline_pythons(cmd):
                inline_idx += 1
                inline_dir.mkdir(exist_ok=True)
                (inline_dir / f"cmd_{inline_idx:03d}.py").write_text(
                    f"# extracted from `python -c` invocation\n"
                    f"# description: {desc}\n\n"
                    + src + "\n"
                )

    if bash_blocks:
        commands_path.write_text(
            "# Bash commands extracted from Claude Code stream log\n\n"
            + "".join(bash_blocks)
        )
    if mcp_calls:
        mcp_path.write_text("\n".join(mcp_calls) + "\n")
    if events:
        _render_agent_md(workspace, events)

    # --- Phase 2: count from workspace files ---

    # bash / cli from commands.sh
    if commands_path.exists():
        for block in commands_path.read_text().split("\n\n"):
            cmd = "\n".join(l for l in block.strip().splitlines() if not l.startswith("#"))
            if not cmd.strip():
                continue
            for sub in _split_shell_commands(cmd):
                first = _bash_first_token(sub) or ""
                if project and first == project:
                    out_counts["cli"] += 1
                elif _PYTHON_TOKEN_RE.search(sub):
                    pass
                else:
                    out_counts["bash"] += 1

    # mcp from mcp_calls.txt
    if mcp_path.exists():
        out_counts["mcp"] = sum(
            1 for l in mcp_path.read_text().splitlines() if l.strip()
        )

    # python / sdk LOC from .py files in workspace
    for py_file in workspace.rglob("*.py"):
        rel = py_file.relative_to(workspace)
        if any(part.startswith(".") for part in rel.parts):
            continue
        try:
            source = py_file.read_text(errors="replace")
            loc = sum(
                1 for line in source.splitlines()
                if line.strip() and not line.strip().startswith("#")
            )
            if project and _python_imports_project(source, project):
                out_counts["sdk"] += loc
            else:
                out_counts["python"] += loc
        except OSError:
            pass

    return out_counts


def _read_results() -> list[dict]:
    if not RESULTS.exists():
        return []
    with RESULTS.open() as f:
        return list(csv.DictReader(f))


def _row_key(row: dict) -> tuple[str, ...]:
    return tuple(str(row.get(k, "")) for k in KEY_FIELDS)


def compute_rep_slot(
    rows: list[dict], model: str, interface: str, task: str, skill: str, reps: int
) -> int | None:
    """Pick the next free rep slot in 0..reps-1 for a new run. Returns ``None``
    if all slots are already filled — re-running the same `make task*` is then
    idempotent: it fills missing slots and skips cells that are already full.
    Drop the row(s) from results.csv (or `make clean`) to re-measure.
    """
    used = set()
    for r in rows:
        if (r.get("model") == model
            and r.get("interface") == interface
            and r.get("task") == task
            and r.get("skill", "") == skill):
            try:
                used.add(int(r["rep"]))
            except (KeyError, ValueError):
                continue
    free = [s for s in range(reps) if s not in used]
    return free[0] if free else None


def upsert_result(row: dict) -> None:
    """Replace any existing row matching (model, interface, task, rep), else
    append. Rewrites the whole CSV so the schema header always matches FIELDS.
    """
    RESULTS.parent.mkdir(parents=True, exist_ok=True)
    rows = _read_results()
    key = _row_key(row)
    rows = [r for r in rows if _row_key(r) != key]
    rows.append(row)
    with RESULTS.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in FIELDS})


_VERSION_RE = re.compile(r"^v(\d+)$", re.IGNORECASE)


def _list_available_skills() -> list[str]:
    """All `<target>` directories under prompts/skills/ that contain at least
    one `vN.md`."""
    if not SKILLS_DIR.exists():
        return []
    out = []
    for d in sorted(SKILLS_DIR.iterdir()):
        if d.is_dir() and any(_VERSION_RE.match(p.stem) for p in d.glob("*.md")):
            out.append(d.name)
    return out


def _latest_version(target_dir: Path) -> Path | None:
    versions = []
    for p in target_dir.glob("*.md"):
        m = _VERSION_RE.match(p.stem)
        if m:
            versions.append((int(m.group(1)), p))
    if not versions:
        return None
    versions.sort()
    return versions[-1][1]


def resolve_skill(arg: str | None) -> tuple[Path | None, str | None, str | None]:
    """Resolve a --skills argument to (path, file_id, short_hash).

    Accepts:
      - ``hops``                 → prompts/skills/hops/<latest vN>.md
      - ``hops/v1``              → prompts/skills/hops/v1.md
      - ``hops/v1.md``           → prompts/skills/hops/v1.md
      - ``./path/to/custom.md``  → that file (any path)

    `file_id` in the returned tuple is the short identifier (e.g. ``hops/v1``)
    that gets recorded in results.csv.
    """
    if not arg:
        return None, None, None

    candidate = Path(arg)
    file_id: str | None = None

    if not candidate.is_absolute() and not candidate.exists():
        # Treat as a logical name under prompts/skills/.
        # Strip optional .md.
        token = arg[:-3] if arg.endswith(".md") else arg
        parts = token.split("/")
        if len(parts) == 1:
            target_dir = SKILLS_DIR / parts[0]
            if target_dir.is_dir():
                latest = _latest_version(target_dir)
                if latest is not None:
                    candidate = latest
                    file_id = f"{parts[0]}/{latest.stem}"
        elif len(parts) == 2:
            candidate = SKILLS_DIR / parts[0] / f"{parts[1]}.md"
            file_id = f"{parts[0]}/{parts[1]}"

    if not candidate.is_absolute():
        candidate = (Path.cwd() / candidate).resolve()

    if not candidate.exists():
        msg = f"skills file not found: {candidate}"
        available = _list_available_skills()
        if available:
            msg += "\navailable skill targets in prompts/skills/: " + ", ".join(available)
            for tgt in available:
                versions = sorted(
                    p.stem for p in (SKILLS_DIR / tgt).glob("*.md")
                    if _VERSION_RE.match(p.stem)
                )
                msg += f"\n  {tgt}: {', '.join(versions)}"
        raise SystemExit(msg)

    if file_id is None:
        try:
            rel = candidate.resolve().relative_to(SKILLS_DIR)
            file_id = str(rel.with_suffix(""))
        except ValueError:
            file_id = candidate.name  # explicit path outside SKILLS_DIR
    short_hash = hashlib.sha256(candidate.read_text().encode()).hexdigest()[:8]
    return candidate, file_id, short_hash


INTERFACE_MANIFEST_DIR = ROOT / ".local" / "interface-installs"


def discover_interfaces(interface: str) -> list[str]:
    """Return the names of installed interfaces of the requested type.

    `local` always yields []. For mcp we use .claude/settings.json (the source
    of truth for what Claude Code spawns). For cli/sdk we union two sources:
    locally-built packages under interfaces/<name>-<type>/ and installs
    from arbitrary sources tracked in .local/interface-installs/<name>-<type>.json.
    """
    if interface == "local":
        return []
    if interface == "mcp" and CLAUDE_SETTINGS.exists():
        try:
            cfg = json.loads(CLAUDE_SETTINGS.read_text())
            return sorted((cfg.get("mcpServers") or {}).keys())
        except (json.JSONDecodeError, OSError):
            return []

    if interface in {"cli", "sdk"}:
        suffix = f"-{interface}"
        names: set[str] = set()
        if INTERFACES.exists():
            for d in INTERFACES.iterdir():
                if d.is_dir() and d.name.endswith(suffix):
                    names.add(d.name[: -len(suffix)])
        if INTERFACE_MANIFEST_DIR.exists():
            for m in INTERFACE_MANIFEST_DIR.glob(f"*-{interface}.json"):
                names.add(m.stem[: -len(suffix)])
        return sorted(names)
    return []


def interface_block(interface: str, names: list[str]) -> str:
    """Render the dynamic interface section that goes into the prompt."""
    if interface == "local" or not names:
        return (
            "## Interfaces\n\n"
            f"Mode: `{interface}`. No external interfaces are installed for this run; "
            "treat this as a vanilla MLE task — use Python and standard CLI tools only.\n"
        )
    listing = "\n".join(f"  - `{n}`" for n in names)
    if interface == "cli":
        body = (
            "The following CLI tools are installed in `.venv/bin/` and on your PATH. "
            "Discover their commands with `<name> --help` (hierarchical help is supported).\n\n"
            f"{listing}\n"
        )
    elif interface == "sdk":
        body = (
            "The following Python SDKs are installed in your environment. "
            "Import them directly from Python (`import <name>`).\n\n"
            f"{listing}\n"
        )
    elif interface == "mcp":
        body = (
            "The following MCP servers are connected to your session. "
            "List their tools using your MCP-listing capability before calling.\n\n"
            f"{listing}\n"
        )
    else:
        body = f"Unknown interface mode `{interface}`.\n"
    return f"## Interfaces\n\nMode: `{interface}`.\n\n{body}"


def install_skill_in_workspace(workspace: Path, skills_path: Path | None) -> str | None:
    """Drop the skill markdown at <workspace>/.claude/skills/<target>-skills/<target>.md
    so Claude Code can resolve it via `/<target>-skills` (works under --bare too).
    Returns the skill command name (e.g. ``hops-skills``), or None.
    """
    if skills_path is None:
        return None
    # skill_id like "hops/v1" → target "hops"
    try:
        rel = skills_path.resolve().relative_to(SKILLS_DIR)
        target = rel.parts[0]
    except (ValueError, IndexError):
        target = skills_path.stem
    cmd_name = f"{target}-skills"
    skill_dir = workspace / ".claude" / "skills" / cmd_name
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / f"{target}.md").write_text(skills_path.read_text())
    return cmd_name


def build_prompt(
    task: str,
    data_dir: Path,
    workspace: Path,
    interface: str,
    skill_target: str | None,
    agent_env_present: bool,
) -> str:
    description_path = data_dir / "description.md"
    if not description_path.exists():
        raise SystemExit(
            f"description.md not found at {description_path}. "
            f"run `make prepare-task TASK={task}` first."
        )
    description = description_path.read_text()

    instructions = (PROMPTS / "MLE_exec.md").read_text()
    skill_md = ""
    if skill_target:
        # skill_target is the slash-command name (e.g. "hops-skills"); the file
        # inside the dir is the project token (e.g. "hops.md").
        target_token = skill_target.removesuffix("-skills")
        skill_md = (
            f"\n## Skill\n\n"
            f"A project-specific skill is available as `/{skill_target}`. "
            f"Invoke it via Claude Code's slash-command syntax to load the "
            f"skill's guidance. Source: `.claude/skills/{skill_target}/{target_token}.md`.\n"
        )

    interfaces_listing = discover_interfaces(interface)
    interfaces_md = interface_block(interface, interfaces_listing)

    env_md = ""
    if agent_env_present:
        env_md = (
            "## Credentials & project config\n\n"
            "A `.env` file is present in your workspace with credentials and project "
            "pointers (e.g. API keys, project name, host). Its values are also already "
            "exported into your shell environment, so any `python` or shell command "
            "you run inherits them automatically. If you'd rather load explicitly, "
            "`source .env` from bash or "
            "`from dotenv import load_dotenv; load_dotenv()` from Python.\n"
        )

    return (
        f"{instructions}{skill_md}\n\n---\n\n"
        f"{interfaces_md}\n"
        f"{env_md}\n"
        f"---\n\n"
        f"# Task: {task}\n\n{description}\n\n"
        f"## Workspace\n\n"
        f"You are running inside: {workspace.resolve()}\n"
        f"Dataset directory: {data_dir.resolve()}\n"
        f"Save your final submission as `submission.csv` in the workspace.\n"
    )


def _render(template: Path, **vars: str) -> str:
    text = template.read_text()
    for k, v in vars.items():
        text = text.replace("${" + k + "}", v)
    return text


def build_task_feedback_prompt(parent_workspace: Path, interface: str, names: list[str]) -> str:
    listing = ", ".join(f"`{n}`" for n in names) or "(none)"
    return _render(
        PROMPTS / "task_feedback.md",
        PARENT_WORKSPACE=str(parent_workspace.resolve()),
        INTERFACE=interface,
        INTERFACE_LISTING=listing,
    )


def _safe_skill(skill: str) -> str:
    """Filesystem-safe segment for the `skill` identity column."""
    if not skill:
        return "no-skill"
    return skill.replace("/", "__").replace("@", "_at_")


def cell_dir(model: str, interface: str, skill: str, task: str) -> Path:
    """Cell-level directory: `results/<model>/<skill>/<interface>/<task>/`."""
    return RUNS / model / _safe_skill(skill) / interface / task


def workspace_path(model: str, interface: str, skill: str, task: str, rep: int) -> Path:
    """Per-rep workspace: `<cell_dir>/<rep>/`."""
    return cell_dir(model, interface, skill, task) / str(rep)


def cell_feedback_session(
    *, task: str, interface: str, model: str, skill: str,
    agent_env_vars: dict[str, str],
) -> None:
    """Synthesize a single feedback.md across all reps of one cell.

    Reads `results.csv` for rows matching (model, interface, task, skill),
    collects each rep's per-task `feedback.md`, and runs a Claude Code
    session that writes a unified synthesis to
    `results/feedback/<cell-slug>.md`. No-op for local or when no per-rep
    feedback exists.
    """
    if interface == "local":
        return
    rows = [
        r for r in _read_results()
        if r.get("model") == model and r.get("interface") == interface
        and r.get("task") == task and r.get("skill", "") == skill
    ]
    rep_feedbacks: list[tuple[str, Path]] = []
    for r in rows:
        path = (r.get("task_path") or "").removeprefix("file://")
        if not path:
            continue
        fb = Path(path) / "feedback.md"
        if fb.exists():
            rep_feedbacks.append((r["rep"], fb))
    if not rep_feedbacks:
        return

    cell = cell_dir(model, interface, skill, task)
    out_path = cell / "feedback.md"
    fb_workspace = cell / ".cell-feedback"
    if fb_workspace.exists():
        shutil.rmtree(fb_workspace)
    fb_workspace.mkdir(parents=True, exist_ok=True)

    listing = "\n".join(
        f"- rep {rep} (`{fb}`):\n```\n{fb.read_text()[:4000]}\n```"
        for rep, fb in sorted(rep_feedbacks)
    )
    prompt = (
        f"# Cell feedback synthesis\n\n"
        f"Cell: task=`{task}`, interface=`{interface}`, model=`{model}`, "
        f"skill=`{skill or '(none)'}`.\n\n"
        f"You're given the per-rep `feedback.md` files for {len(rep_feedbacks)} "
        f"reps of this cell. Synthesize a single concise report that:\n"
        f"  1. Identifies recurring issues across reps (vs one-off noise).\n"
        f"  2. Calls out platform-interface friction the agent hit.\n"
        f"  3. Suggests interface or skill improvements with concrete examples.\n\n"
        f"Write the synthesis to `feedback.md` in the current directory and stop.\n\n"
        f"## Per-rep feedback\n\n{listing}\n"
    )
    (fb_workspace / "prompt.md").write_text(prompt)

    env = {
        **os.environ,
        **agent_env_vars,
        "ANTHROPIC_BASE_URL": "http://localhost:4000",
        "TASK": task,
        "INTERFACE": interface,
        "INTERFACE_LOG_FILE": str(fb_workspace / "interface_calls.jsonl"),
    }
    bootstrap = "Read `prompt.md` and follow it. Stop when feedback.md is written."
    log_path = fb_workspace / "agent.jsonl"
    print(f"[coordinator] cell feedback synthesis: {slug}")
    try:
        with log_path.open("w") as logf:
            subprocess.run(
                ["claude", "--print", "--bare", "--no-session-persistence",
                 "--dangerously-skip-permissions",
                 "--output-format", "stream-json", "--include-partial-messages",
                 "--verbose", "--model", model, bootstrap],
                env=env, cwd=str(fb_workspace),
                stdout=logf, stderr=subprocess.STDOUT,
            )
    except FileNotFoundError:
        print("[coordinator] cell feedback skipped: claude not on PATH")
        return

    fb_src = fb_workspace / "feedback.md"
    if fb_src.exists():
        out_path.write_text(fb_src.read_text())
        shutil.rmtree(fb_workspace, ignore_errors=True)
        print(f"[coordinator] cell feedback written to {out_path}")


def cli_cell_feedback() -> None:
    ap = argparse.ArgumentParser(
        description="Synthesize cross-rep feedback for one (model, interface, task, skill) cell."
    )
    ap.add_argument("--task", required=True)
    ap.add_argument("--interface", required=True, choices=["local", "sdk", "cli", "mcp"])
    ap.add_argument("--model", default="claude-sonnet")
    ap.add_argument("--skills", default=None,
                    help="same syntax as `coordinator --skills`")
    args = ap.parse_args()
    _, skill_file, skill_hash = resolve_skill(args.skills)
    skill = f"{skill_file}@{skill_hash}" if skill_file and skill_hash else ""
    agent_env_vars: dict[str, str] = {}
    if AGENT_ENV.exists():
        agent_env_vars = {k: v for k, v in dotenv_values(AGENT_ENV).items() if v is not None}
    cell_feedback_session(
        task=args.task, interface=args.interface, model=args.model,
        skill=skill, agent_env_vars=agent_env_vars,
    )


_BASE_REQUIREMENTS = ROOT / "requirements-base.txt"


def _ensure_base_packages() -> None:
    """Install requirements-base.txt into the shared venv once (idempotent).

    uv skips packages that are already installed, so this is a no-op after
    the first run. Add packages to requirements-base.txt to make them
    available to every agent run without re-downloading.
    """
    if not _BASE_REQUIREMENTS.exists():
        return
    if shutil.which("uv"):
        subprocess.run(
            ["uv", "pip", "install", "--quiet", "-r", str(_BASE_REQUIREMENTS)],
            check=True,
        )
    else:
        subprocess.run(
            [sys.executable, "-m", "pip", "install", "-q", "-r", str(_BASE_REQUIREMENTS)],
            check=True,
        )


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--task", default="aerial-cactus-identification")
    ap.add_argument("--model", default="claude-sonnet")
    ap.add_argument("--interface", default="local",
                    choices=["local", "sdk", "cli", "mcp"],
                    help="local = no platform interface; sdk/cli/mcp = the three "
                         "interface modalities the agent can invoke")
    ap.add_argument("--reps", type=int, default=1,
                    help="rep-slot capacity per (model, interface, task, skill) "
                         "cell in results.csv. rep is auto-assigned to the "
                         "lowest free slot in 0..reps-1; if all slots are "
                         "filled the run is skipped (idempotent re-run).")
    ap.add_argument("--skills", default=None,
                    help="optional skills markdown. Pass a bare name "
                         "(e.g. `hops`) for the latest prompts/skills/<name>/v*.md, "
                         "`<name>/v<N>` for a specific version, or an explicit path.")
    ap.add_argument("--project", default="",
                    help="project name (e.g. `hops`). agent invocations matching this token "
                         "are classified as project CLI/SDK/MCP calls.")
    args = ap.parse_args()
    _acquire_coordinator_lock()
    atexit.register(_release_coordinator_lock)

    skills_path, skill_file, skill_hash = resolve_skill(args.skills)
    skill = f"{skill_file}@{skill_hash}" if skill_file and skill_hash else ""

    data_dir = MLE_DATA / args.task / "prepared" / "public"
    if not data_dir.exists():
        raise SystemExit(
            f"data not prepared for {args.task} (looked at {data_dir}). "
            f"run `make prepare-task TASK={args.task}` first."
        )

    rep = compute_rep_slot(_read_results(), args.model, args.interface, args.task, skill, args.reps)
    if rep is None:
        print(f"[coordinator] skip: all {args.reps} rep slot(s) already filled for "
              f"{args.model}/{skill or 'no-skill'}/{args.interface}/{args.task}. "
              f"Drop the row(s) from results.csv (or `make clean`) to re-measure.")
        return

    workspace = workspace_path(args.model, args.interface, skill, args.task, rep)
    if workspace.exists():
        shutil.rmtree(workspace)
    workspace.mkdir(parents=True, exist_ok=True)

    skill_target = install_skill_in_workspace(workspace, skills_path)

    # Expose the agent's private env file two ways:
    #   1. Drop a copy at <workspace>/.env so the agent can `source .env` or
    #      `dotenv.load_dotenv()` explicitly.
    #   2. Inject every key/value into the Claude Code subprocess env so any
    #      bash / python the agent spawns inherits them automatically.
    agent_env_present = False
    agent_env_vars: dict[str, str] = {}
    if AGENT_ENV.exists():
        (workspace / ".env").write_text(AGENT_ENV.read_text())
        agent_env_vars = {k: v for k, v in dotenv_values(AGENT_ENV).items() if v is not None}
        agent_env_present = True

    interface_log = workspace / "interface_calls.jsonl"
    env = {
        **os.environ,
        **agent_env_vars,
        # Route Claude Code through our LiteLLM proxy so every model call
        # lands in events.jsonl and gets cost/token-tracked.
        "ANTHROPIC_BASE_URL": "http://localhost:4000",
        "TASK": args.task,
        "INTERFACE": args.interface,
        "INTERFACE_LOG_FILE": str(interface_log),
        # Strip standalone Python interpreter directories so the agent uses
        # the shared testbed venv and cannot fall back to a system Python.
        "PATH": ":".join(
            p for p in os.environ.get("PATH", "").split(":")
            if p and not any(s in p for s in (
                "/Library/Developer",
                "/usr/bin/python",
                "/usr/local/bin/python",
                "/opt/homebrew/bin/python",
                "/opt/homebrew/opt/python",
            ))
        ),
    }

    print(
        f"[coordinator] task={args.task} model={args.model} "
        f"interface={args.interface} skill={skill or '(none)'} rep={rep} "
        f"workspace={workspace}"
    )

    prompt = build_prompt(
        args.task, data_dir, workspace, args.interface, skill_target, agent_env_present
    )
    (workspace / "prompt.md").write_text(prompt)

    started_at = datetime.now(timezone.utc).isoformat()
    started = time.time()

    bootstrap = (
        "Read `prompt.md` in the current directory and follow it end-to-end. "
        "Save your final submission as `submission.csv` in this directory."
    )
    cmd = [
        "claude",
        "--print",
        "--bare",
        "--no-session-persistence",
        "--dangerously-skip-permissions",
        "--output-format", "stream-json",
        "--include-partial-messages",
        "--verbose",
        "--model", args.model,
        bootstrap,
    ]
    log_path = workspace / "agent.jsonl"
    stopped_by = "ok"

    # Background watcher: poll Claude Code's stream-json log every ~3s and
    # refresh commands.sh so the user can `tail -f` the agent's bash history.
    stop_watcher = threading.Event()

    def _watch():
        while not stop_watcher.wait(3.0):
            try:
                extract_bash_commands(workspace, args.project)
            except Exception:
                pass

    watcher = threading.Thread(target=_watch, daemon=True)
    watcher.start()

    interrupted = False
    try:
        with log_path.open("w") as logf:
            proc = subprocess.Popen(
                cmd,
                env=env,
                cwd=str(workspace),
                stdout=logf,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
            pgid = os.getpgid(proc.pid)
            AGENT_PGID_FILE.write_text(str(pgid))
            returncode = proc.wait()
        # Kill any background processes the agent left running.
        try:
            os.killpg(pgid, signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            pass
        AGENT_PGID_FILE.unlink(missing_ok=True)
        if returncode != 0:
            stopped_by = f"exit={returncode}"
    except FileNotFoundError:
        stop_watcher.set()
        raise SystemExit("claude binary not found on PATH. install via `npm i -g @anthropic-ai/claude-code`.")
    except KeyboardInterrupt:
        interrupted = True
        try:
            os.killpg(pgid, signal.SIGTERM)
        except (ProcessLookupError, PermissionError, UnboundLocalError):
            pass
    finally:
        stop_watcher.set()
        watcher.join(timeout=5.0)

    # Interrupted runs leave no trace: wipe the workspace and exit before
    # writing a row. results.csv only ever contains completed runs.
    if interrupted:
        if workspace.exists():
            shutil.rmtree(workspace, ignore_errors=True)
        print(f"[coordinator] interrupted; rep slot {rep} cleared for "
              f"{args.model}/{skill or 'no-skill'}/{args.interface}/{args.task}")
        raise SystemExit(130)

    duration = time.time() - started
    ended_at = datetime.now(timezone.utc).isoformat()

    time.sleep(1.0)
    usage = query_run_usage(started_at=started_at, ended_at=ended_at)
    report = grade_run(args.task, workspace)
    interface_counts = count_calls(interface_log)
    bash_counts = extract_bash_commands(workspace, args.project)
    print(f"[coordinator] commands.sh written; cli={bash_counts['cli']} bash={bash_counts['bash']} mcp={bash_counts['mcp']} sdk={bash_counts['sdk']} python={bash_counts['python']}")

    # Treat as a failed run if the agent never made real progress: zero tokens
    # consumed AND any error reported AND no submission produced. Wipe the
    # workspace and exit without writing a CSV row so a future re-run can
    # claim the same rep slot.
    no_tokens = (usage.get("total_tokens") or 0) == 0
    has_error = bool(usage.get("last_error"))
    no_submission = not report.get("submission_exists")
    if no_tokens and has_error and no_submission:
        print(f"[coordinator] agent exited without progress ({usage.get('last_error', '')[:120]}). "
              f"Wiping workspace; rep slot {rep} stays free.")
        shutil.rmtree(workspace, ignore_errors=True)
        raise SystemExit(2)

    row = {
        "model": args.model,
        "skill": skill,
        "interface": args.interface,
        "task": args.task,
        "rep": rep,
        "task_path": "file://" + str(workspace.resolve()),
        "started_at": started_at,
        "duration": _hms(duration),
        "duration_s": round(duration, 3),
        "turns": usage.get("turns", 0),
        "stopped_by": stopped_by,
        "prompt_tokens": usage.get("prompt_tokens", 0),
        "completion_tokens": usage.get("completion_tokens", 0),
        "total_tokens": usage.get("total_tokens", 0),
        "cost_usd": round(float(usage.get("cost", 0.0) or 0.0), 6),
        "interface_calls": interface_counts.get("total", 0),
        "interface_call_errors": interface_counts.get("errors", 0),
        "cli": bash_counts["cli"],
        "sdk": bash_counts["sdk"],
        "mcp": bash_counts["mcp"],
        "bash": bash_counts["bash"],
        "python": bash_counts["python"],
        "error": usage.get("last_error", ""),
        "grade": report.get("score"),
        "valid_submission": report.get("valid_submission"),
        "submission_exists": report.get("submission_exists"),
        "any_medal": report.get("any_medal"),
        "gold_medal": report.get("gold_medal"),
        "silver_medal": report.get("silver_medal"),
        "bronze_medal": report.get("bronze_medal"),
        "above_median": report.get("above_median"),
    }
    upsert_result(row)
    print(json.dumps({k: v for k, v in row.items() if k in FIELDS}, indent=2))

    # Optional feedback pass — separate session whose tokens are NOT counted
    # in results.csv. The agent reads the just-finished task's workspace and
    # writes feedback.md back into it. Side-effect only; not a measurement.
    if args.interface != "local":
        run_feedback_session(
            parent_workspace=workspace,
            task=args.task,
            model=args.model,
            interface=args.interface,
            agent_env_vars=agent_env_vars,
        )


def run_feedback_session(
    *,
    parent_workspace: Path,
    task: str,
    model: str,
    interface: str,
    agent_env_vars: dict[str, str],
) -> None:
    """Run a separate Claude Code session that reads the parent task's
    workspace and writes `feedback.md` back into it. Side-effect only — the
    session's cost / latency is NOT recorded in results.csv. The session's
    own workspace lives at `<parent_workspace>/.feedback/`.
    """
    fb_workspace = parent_workspace / ".feedback"
    fb_workspace.mkdir(parents=True, exist_ok=True)

    names = discover_interfaces(interface)
    fb_prompt = build_task_feedback_prompt(parent_workspace, interface, names)
    (fb_workspace / "prompt.md").write_text(fb_prompt)

    env = {
        **os.environ,
        **agent_env_vars,
        "ANTHROPIC_BASE_URL": "http://localhost:4000",
        "TASK": task,
        "INTERFACE": interface,
        # Feedback sessions write to a private interface log so they don't
        # contaminate the parent task's interface_calls.jsonl.
        "INTERFACE_LOG_FILE": str(fb_workspace / "interface_calls.jsonl"),
    }

    bootstrap = "Read `prompt.md` and follow it. Stop when feedback.md is written."
    log_path = fb_workspace / "agent.jsonl"
    print(f"[coordinator] feedback session for {parent_workspace.name}")
    try:
        with log_path.open("w") as logf:
            subprocess.run(
                ["claude", "--print", "--bare", "--no-session-persistence",
                 "--dangerously-skip-permissions",
                 "--output-format", "stream-json", "--include-partial-messages",
                 "--verbose", "--model", model, bootstrap],
                env=env, cwd=str(fb_workspace),
                stdout=logf, stderr=subprocess.STDOUT,
            )
    except FileNotFoundError:
        print("[coordinator] feedback skipped: claude not on PATH")
        return

    # Persist commands.sh for traceability.
    extract_bash_commands(fb_workspace)

    # Promote feedback.md to the parent task's folder.
    fb_src = fb_workspace / "feedback.md"
    if fb_src.exists():
        dest = parent_workspace / "feedback.md"
        dest.write_text(fb_src.read_text())
        print(f"[coordinator] task feedback written to {dest}")


if __name__ == "__main__":
    main()
