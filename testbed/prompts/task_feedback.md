# Per-task interface feedback

A previous task run completed in this workspace: `${PARENT_WORKSPACE}`.

It contains the agent's full record:
  - `prompt.md` — the prompt the agent received
  - `submission.csv` — the agent's final submission (if any)
  - `commands.sh` — every bash command the agent ran (one block per Bash tool call)
  - `mcp_calls.txt` — MCP tool calls made during the run (if any)
  - `agent.md` — human-readable transcript of the full agent session
  - `agent.jsonl` — raw Claude Code stream-json log
  - `interface_calls.jsonl` — structured interface call records
  - `inline_python/` — extracted `python -c` snippets (if any)
  - any scripts (`*.py`, `*.ipynb`) the agent wrote

## Your job

Read the relevant files. Then write `feedback.md` in *your* current workspace answering:

How could the `${INTERFACE}` interface (${INTERFACE_LISTING}) be improved so that the previous task would have needed *fewer* turns and *fewer* local python invocations — i.e. so more of the work could be done through the interface itself instead of bash/python fallbacks?

Be concrete: name specific commands, missing flags, missing endpoints, or capabilities that would have saved turns. ≤300 words. Stop after writing the file.
