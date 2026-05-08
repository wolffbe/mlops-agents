# MCP Generation Prompt

You are generating `${NAME}-mcp`, a Model Context Protocol server that exposes the operations of the system documented under `${DOCS}` as MCP tools. Read that documentation to identify the user-facing operations of the system's Python SDK (or equivalent) and emit an MCP server that wraps them.

The MCP server is one of three interface variants in a controlled comparison (CLI, MCP, SDK). It must implement the design patterns below — they are stylistic surfaces meant to minimize per-task token consumption while keeping capability parity with the other two interfaces.

## Design patterns to follow

### 1. Progressive capability discovery

Do not eagerly inject every tool's full schema into the agent's context at session start. The full operation surface is too large to fit cheaply.

Implement discovery as a ScaleMCP-style retrieval tool: expose a single tool such as `${NAME}_search(query, top_k)` that the agent calls with keywords describing what it needs. The tool returns a small ranked list of candidate tools with concise summaries. The agent then either calls those tools directly (if they were already equipped) or invokes a follow-up `${NAME}_describe(tool_name)` to fetch the full parameter schema before binding and calling.

The retriever is backed by an index of tool documents (name + description + synthetic questions) built from the source SDK as the single source of truth. Index entries should be content-hashed so the index can be regenerated automatically when the SDK changes; do not hand-curate it.

This converts schema cost from a per-session fixed tax into a per-need variable cost.

### 2. Concise tool descriptions; documentation on demand

Each tool's `description` field fits in ~120 characters and states what it does, not how. Long-form reference material is exposed via a separate resource or a dedicated `docs(topic)` tool, not inlined into every tool description.

### 3. Few tools; prefer a dispatch tool over a wide surface

Keep the tool count low. Where a group of operations shares an obvious verb (`get`, `list`, `create`, `delete`, `run`), prefer a single dispatch tool that takes a typed target (e.g., `${NAME}_run(action, target_type, ...)`) over one tool per operation.

### 4. Selective output sizing

Every tool that returns records accepts a `fields` parameter so the agent can request only the columns it needs. Default returns are the minimal informative subset, not the full record.

### 5. Predictable output sizing through limits and pagination

Every list-like tool accepts `limit` (with a sane default such as 20) and `offset` or `page`. The defaults and maxima must appear in the tool description so the agent can estimate token impact before invoking.

### 6. Machine-parseable output

MCP returns structured content by default — keep it that way. Emit consistent JSON-shaped objects with stable field names. Do not embed prose summaries inside structured fields; if a human-readable summary is wanted, return it as a separate `summary` field the agent can ignore.

## Output

Write the project into the current directory:

- A Python package implementing the MCP server.
- A `pyproject.toml` exposing a `${NAME}-mcp` console script entry point.
- A capabilities manifest demonstrating that listing tools returns concise descriptions and that detail is fetched on demand.

Do not generate documentation pages, READMEs, or marketing copy.

## Telemetry contract

Every tool invocation must record a row in `$INTERFACE_LOG_FILE` via `coordinator.interface_log.record_mcp`. Wrap each tool handler with a decorator that calls the recorder on entry/exit — do not require callers to opt in.
