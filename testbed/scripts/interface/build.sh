#!/usr/bin/env bash
# Generate an interface (CLI, MCP server, or SDK wrapper) from the prompts
# under prompts/ and a documentation source. Output lands in
# interfaces/<NAME>-<TYPE>/ as a Python package.
set -euo pipefail

if [ "$#" -lt 3 ]; then
  echo "usage: $0 <cli|mcp|sdk> <name> <docs-path>" >&2
  exit 1
fi

TYPE="$1"
NAME="$2"
DOCS="$3"

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

case "$TYPE" in
  cli) TEMPLATE="$ROOT/prompts/CLI_gen.md" ;;
  mcp) TEMPLATE="$ROOT/prompts/MCP_gen.md" ;;
  sdk)
    echo "SDK generation isn't templated yet — vendor or wrap an existing SDK directly." >&2
    exit 1
    ;;
  *) echo "unknown type: $TYPE (expected cli|mcp|sdk)" >&2; exit 1 ;;
esac

if [ ! -f "$TEMPLATE" ]; then
  echo "ERROR: prompt template missing at $TEMPLATE" >&2
  exit 1
fi
for cmd in claude envsubst git curl; do
  command -v "$cmd" >/dev/null 2>&1 || { echo "ERROR: \`$cmd\` not on PATH (run \`make install\`)" >&2; exit 1; }
done

DOCS_DIR="$ROOT/.local/docs"
mkdir -p "$DOCS_DIR"

# Accept either a local path or a GitHub repo URL. URLs are cloned to
# .local/docs/<repo-name>/ on first use and `git pull`-ed afterwards so the
# generator has a stable on-disk copy to read.
if [[ "$DOCS" =~ ^(https?://github\.com/|git@github\.com:) ]]; then
  REPO=$(basename "${DOCS%.git}")
  CHECKOUT="$DOCS_DIR/$REPO"
  if [ ! -d "$CHECKOUT/.git" ]; then
    echo "=== cloning $DOCS into $CHECKOUT ==="
    if ! git clone --depth=1 "$DOCS" "$CHECKOUT"; then
      cat >&2 <<EOF
ERROR: failed to clone $DOCS
Possible causes:
  - typo in the URL (expected https://github.com/<owner>/<repo> or git@github.com:<owner>/<repo>.git)
  - repo is private and you're not authenticated
  - no network access
EOF
      exit 1
    fi
  else
    echo "=== updating $CHECKOUT ==="
    git -C "$CHECKOUT" pull --ff-only --quiet || true
  fi
  DOCS_ABS="$CHECKOUT"
elif [ -e "$DOCS" ]; then
  DOCS_ABS=$(cd "$DOCS" 2>/dev/null && pwd || readlink -f "$DOCS" 2>/dev/null || echo "$DOCS")
  if [ ! -d "$DOCS_ABS" ]; then
    echo "ERROR: docs path \"$DOCS\" exists but is not a directory" >&2
    exit 1
  fi
  if [ -z "$(ls -A "$DOCS_ABS" 2>/dev/null)" ]; then
    echo "ERROR: docs path \"$DOCS_ABS\" is empty — nothing for the agent to read" >&2
    exit 1
  fi
else
  cat >&2 <<EOF
ERROR: docs path "$DOCS" not found.
Provide either:
  - a local directory containing the documentation source, or
  - a GitHub URL (https://github.com/<owner>/<repo> or git@github.com:<owner>/<repo>.git)
    which will be cloned into .local/docs/.
EOF
  exit 1
fi

OUT="$ROOT/interfaces/$NAME-$TYPE"
mkdir -p "$OUT"

# Substitute $NAME and $DOCS into the prompt.
NAME="$NAME" DOCS="$DOCS_ABS" envsubst '${NAME} ${DOCS}' < "$TEMPLATE" > "$OUT/prompt.md"

if [ -f "$ROOT/.env" ]; then
  set -a; source "$ROOT/.env"; set +a
fi

echo "=== generating $NAME-$TYPE in $OUT ==="
ANTHROPIC_BASE_URL=http://localhost:4000 claude --print \
  --dangerously-skip-permissions \
  --model claude-sonnet \
  "Read prompt.md and generate the interface end-to-end into this directory. Produce a working Python package with a pyproject.toml." \
  > "$OUT/build.log" 2>&1 || true

echo "built: $OUT (log: $OUT/build.log)"
