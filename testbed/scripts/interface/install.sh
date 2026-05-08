#!/usr/bin/env bash
# Install an interface so the coding agent can reach it. Supports four sources:
#
#   1. (default) Pre-built package under interfaces/<name>-<type>/.
#         make interface-install TYPE=cli NAME=hops
#
#   2. A local path on disk that has its own pyproject.toml.
#         make interface-install TYPE=cli NAME=hops SOURCE=../my-hops-cli
#
#   3. A git URL (anything `uv pip install` accepts as a `git+...` ref).
#         make interface-install TYPE=mcp NAME=hops SOURCE=git+https://github.com/me/hops-mcp
#
#   4. A PyPI distribution name (the same string you'd pass to `pip install`).
#         make interface-install TYPE=sdk NAME=hopsworks SOURCE=hopsworks
#
# Optional BIN= overrides the binary / import name we probe with after install
# (defaults to NAME for cli/sdk, NAME-mcp for mcp). We persist a small manifest
# at .local/interface-installs/<name>-<type>.json so `interface-uninstall`
# knows what to remove without re-deriving anything.
set -euo pipefail

if [ "$#" -lt 2 ]; then
  echo "usage: $0 <cli|mcp|sdk> <name> [source] [bin]" >&2
  exit 1
fi

TYPE="$1"
NAME="$2"
SOURCE="${3:-}"
BIN_OVERRIDE="${4:-}"

case "$TYPE" in
  cli|sdk|mcp) ;;
  *) echo "ERROR: unknown type \"$TYPE\" (expected cli|mcp|sdk)" >&2; exit 1 ;;
esac

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
PKG_DIR="$ROOT/interfaces/$NAME-$TYPE"
CLAUDE_SETTINGS="$ROOT/.claude/settings.json"
MANIFEST_DIR="$ROOT/.local/interface-installs"
MANIFEST="$MANIFEST_DIR/$NAME-$TYPE.json"
mkdir -p "$MANIFEST_DIR"

if [ ! -f "$ROOT/.venv/pyvenv.cfg" ]; then
  echo "ERROR: .venv missing — run \`make install\` first." >&2
  exit 1
fi
for cmd in uv claude; do
  command -v "$cmd" >/dev/null 2>&1 || { echo "ERROR: \`$cmd\` not on PATH" >&2; exit 1; }
done
if [ "$TYPE" = "mcp" ] && [ ! -f "$CLAUDE_SETTINGS" ]; then
  mkdir -p "$(dirname "$CLAUDE_SETTINGS")"
  echo '{}' > "$CLAUDE_SETTINGS"
fi

# --- resolve source -------------------------------------------------------
classify_source() {
  local s="$1"
  if [ -z "$s" ]; then
    echo "builtin"   # use interfaces/<name>-<type>/
  elif [[ "$s" =~ ^(git\+|hg\+|bzr\+|svn\+) ]]; then
    echo "vcs"
  elif [[ "$s" =~ ^https?:// ]]; then
    echo "url"
  elif [ -e "$s" ]; then
    echo "path"
  else
    # bare token — treat as a PyPI distribution name (or extras spec).
    echo "pypi"
  fi
}

KIND=$(classify_source "$SOURCE")
echo "=== installing $NAME-$TYPE (source kind: $KIND) ==="

INSTALL_REF=""    # what we hand to `uv pip install`
case "$KIND" in
  builtin)
    if [ ! -d "$PKG_DIR" ]; then
      cat >&2 <<EOF
ERROR: nothing built at $PKG_DIR.
Either:
  1) build it first:    make interface-build TYPE=$TYPE NAME=$NAME DOCS=...
  2) install elsewhere: make interface-install TYPE=$TYPE NAME=$NAME SOURCE=<path|git+url|pypi-name>
EOF
      exit 1
    fi
    if [ ! -f "$PKG_DIR/pyproject.toml" ]; then
      echo "ERROR: $PKG_DIR has no pyproject.toml" >&2
      exit 1
    fi
    INSTALL_REF="-e $PKG_DIR"
    ;;
  path)
    abs=$(cd "$SOURCE" 2>/dev/null && pwd || readlink -f "$SOURCE" 2>/dev/null)
    [ -z "$abs" ] && { echo "ERROR: cannot resolve $SOURCE" >&2; exit 1; }
    if [ ! -f "$abs/pyproject.toml" ] && [ ! -f "$abs/setup.py" ]; then
      echo "ERROR: $abs has neither pyproject.toml nor setup.py" >&2
      exit 1
    fi
    INSTALL_REF="-e $abs"
    ;;
  vcs|url)
    INSTALL_REF="$SOURCE"
    ;;
  pypi)
    INSTALL_REF="$SOURCE"
    ;;
esac

cd "$ROOT"
# shellcheck disable=SC2086
uv pip install $INSTALL_REF

# --- pick the probe binary / import name ----------------------------------
DEFAULT_BIN="$NAME"
[ "$TYPE" = "mcp" ] && DEFAULT_BIN="$NAME-mcp"
BIN="${BIN_OVERRIDE:-$DEFAULT_BIN}"

# --- MCP registration -----------------------------------------------------
if [ "$TYPE" = "mcp" ]; then
  if ! command -v jq >/dev/null 2>&1; then
    echo "warning: jq missing — register $NAME in $CLAUDE_SETTINGS manually:" >&2
    echo "  \"mcpServers\": { \"$NAME\": { \"command\": \"$BIN\" } }" >&2
  else
    tmp=$(mktemp)
    jq --arg name "$NAME" --arg cmd "$BIN" \
      '.mcpServers = (.mcpServers // {}) | .mcpServers[$name] = {"command":$cmd,"args":[]}' \
      "$CLAUDE_SETTINGS" > "$tmp" && mv "$tmp" "$CLAUDE_SETTINGS"
    echo "registered $NAME in $CLAUDE_SETTINGS (command: $BIN)"
  fi
fi

# --- propagate required env vars to .agent.env ---------------------------
# An interface package can declare its credentials/config requirements by
# shipping `agent.env.example` (a key=value template). On install we union
# its keys with the testbed's `.agent.env`, never overwriting existing values
# but appending any missing ones with their template defaults.
ENV_TEMPLATE=""
if [ "$KIND" = "builtin" ] && [ -f "$PKG_DIR/agent.env.example" ]; then
  ENV_TEMPLATE="$PKG_DIR/agent.env.example"
elif [ "$KIND" = "path" ] && [ -f "$abs/agent.env.example" ]; then
  ENV_TEMPLATE="$abs/agent.env.example"
fi
AGENT_ENV="$ROOT/.agent.env"
if [ -n "$ENV_TEMPLATE" ]; then
  touch "$AGENT_ENV"
  appended=()
  while IFS= read -r line; do
    case "$line" in ""|"#"*) continue ;; esac
    key="${line%%=*}"
    if ! grep -qE "^\s*${key}=" "$AGENT_ENV"; then
      printf '%s\n' "$line" >> "$AGENT_ENV"
      appended+=("$key")
    fi
  done < "$ENV_TEMPLATE"
  if [ "${#appended[@]}" -gt 0 ]; then
    echo
    echo "appended ${#appended[@]} env var(s) to $AGENT_ENV: ${appended[*]}"
    echo "fill them in before running \`make task INTERFACE=$TYPE\`."
  fi
fi

# --- persist install manifest --------------------------------------------
cat > "$MANIFEST" <<EOF
{
  "name":   "$NAME",
  "type":   "$TYPE",
  "source": "$([ -z "$SOURCE" ] && echo "$PKG_DIR" || echo "$SOURCE")",
  "kind":   "$KIND",
  "bin":    "$BIN",
  "ref":    "$INSTALL_REF"
}
EOF

# --- static readiness check -----------------------------------------------
echo
echo "=== static readiness check ==="
if ! bash "$ROOT/scripts/interface/test.sh" "$TYPE" "$NAME"; then
  cat >&2 <<EOF

Static check failed. Likely causes:
  - cli: \`$BIN\` not on .venv/bin/. Override with BIN=<actual-binary> if your
         package's entry point differs from \`$NAME\`.
  - sdk: import name doesn't match \`$NAME\`. Override with BIN=<import-name>.
  - mcp: binary \`$BIN\` missing from .venv/bin/, or .claude/settings.json
         mcpServers section wasn't updated.
EOF
  exit 1
fi

# --- agent reachability probe ---------------------------------------------
echo
echo "=== agent reachability check ==="
if ! curl -fs http://localhost:4000/health/liveliness >/dev/null 2>&1; then
  echo "warning: LiteLLM not reachable at :4000; agent reachability check skipped." >&2
  echo "         start it with \`make start\` and re-run \`make interface-install\`." >&2
  echo "installed (agent check skipped): $NAME-$TYPE"
  exit 0
fi

if [ -f "$ROOT/.env" ]; then set -a; source "$ROOT/.env"; set +a; fi

probe_dir="$ROOT/.local/interface-probe/$NAME-$TYPE"
rm -rf "$probe_dir"; mkdir -p "$probe_dir"

case "$TYPE" in
  cli) probe_prompt="Run \`$BIN --help\` via the bash tool exactly once. Report whether it exited 0. Then stop. Do not run any other commands." ;;
  sdk) probe_prompt="Run \`python -c 'import $BIN; print($BIN.__name__)'\` via the bash tool exactly once. Report whether it exited 0. Then stop. Do not run any other commands." ;;
  mcp) probe_prompt="List the MCP tools available to you and confirm a tool from the \`$NAME\` server is connected. Then stop. Do not invoke any tools other than listing." ;;
esac

if ANTHROPIC_BASE_URL=http://localhost:4000 timeout 120 claude --print \
     --dangerously-skip-permissions \
     --model claude-sonnet \
     "$probe_prompt" >"$probe_dir/probe.log" 2>&1; then
  echo "ok: agent reached $NAME-$TYPE"
else
  cat >&2 <<EOF
agent reachability probe FAILED for $NAME-$TYPE.
log: $probe_dir/probe.log

Common fixes:
  - cli: pass BIN=<actual-binary> if it differs from \`$NAME\`.
  - sdk: pass BIN=<import-name> if it differs from \`$NAME\`.
  - mcp: confirm \`mcpServers.$NAME\` exists in .claude/settings.json and
         \`$BIN\` is on PATH.
EOF
  exit 1
fi

echo "installed and reachable: $NAME-$TYPE"
