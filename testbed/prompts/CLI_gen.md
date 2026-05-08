# CLI Generation Prompt

You are generating `${NAME}-cli`, a command-line interface that wraps the Python SDK (or equivalent) of the system documented under `${DOCS}`. Read that documentation to identify the user-facing operations and emit a Click- or Typer-based CLI that exposes them.

The CLI is one of three interface variants in a controlled comparison (CLI, MCP, SDK). It must implement the design patterns below — they are stylistic surfaces meant to minimize per-task token consumption while keeping capability parity with the other two interfaces.

## Design patterns to follow

### 1. Progressive capability discovery via `--help`

Organise commands hierarchically (`${NAME} <group> <subcommand>`). At each level, `--help` lists only what is one step down — group descriptions at the top, subcommand summaries inside a group, parameter details inside a subcommand. Schema cost is paid at invocation, not at session start.

### 2. Concise tool descriptions; documentation on demand

Each command's one-line summary fits in ~80 characters. Multi-line help is reserved for examples and rare edge cases. Provide a `${NAME} docs <topic>` command (or equivalent) that prints longer-form reference material only when the agent asks for it.

### 3. Few commands per group; prefer dispatch over enumeration

Avoid emitting a separate command for every method on the SDK. Group related operations and use a small number of verbs (`get`, `list`, `create`, `delete`, `update`, `run`) that take typed targets. The total top-level surface should stay small.

### 4. Selective output sizing

Every list/get command accepts `--fields f1,f2,...` so the agent can request just the columns it needs. Default output should be the minimal informative subset, not the full record.

### 5. Predictable output sizing through limits and pagination

Every list-like command supports `--limit N` (with a sane default such as 20) and `--offset N` or `--page N`. Document the default and maximum in the command's help so the agent can estimate token impact before calling.

### 6. Machine-parseable output

Support a global `--json` flag that emits compact JSON to stdout. When the output stream is not a TTY, default to JSON automatically. In TTY mode, use plain text — never coloured or boxed output that the agent would have to strip.

### 7. Composability via Unix piping

Errors and progress go to stderr; data goes to stdout. Each command exits non-zero on failure. Where it makes sense, accept input on stdin (e.g., reading IDs to operate on from a previous command's output). The agent should be able to compose `${NAME}` commands with standard shell tools (`jq`, `xargs`, `grep`).

## Patterns explicitly NOT to follow

- **Structural permission classification** (auto-approve reads, prompt on writes, block deletes). Behavioural rather than stylistic; would impose capability constraints that confound the cross-interface comparison.
- **Full capability coverage** beyond what the experimental tasks require. Coverage parity is enforced at the operation level, not by mirroring every SDK method.
- **Stateful sessions across invocations.** Each experimental run uses one isolated context; do not persist state in `~/.${NAME}/` or similar between calls.
- **Per-user OAuth, tenant isolation, audit trails.** Single-user experiments only.
- **Centralized telemetry and observability** baked into the binary.
- **Code Mode** (the CLI emits orchestration scripts to be executed). Out of scope.

## Output

Write the project into the current directory:

- A Python package implementing the CLI with Click or Typer.
- A `pyproject.toml` exposing a `${NAME}` console script entry point.
- A short `--help` smoke test fixture demonstrating the hierarchical help output.

Do not generate documentation pages, READMEs, or marketing copy.

## Telemetry contract

Every CLI invocation must record a row in `$INTERFACE_LOG_FILE` via `coordinator.interface_log.record_cli` (or its shell equivalent). Wrap the entry point so this happens automatically — do not require callers to opt in.
