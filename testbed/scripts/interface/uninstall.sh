#!/usr/bin/env bash
# Uninstall a previously-installed interface and (for MCP) deregister it from
# opencode.json. Reads the install manifest written by install.sh so we can
# uninstall packages that came from PyPI / git / external paths even after
# their source dir is gone.
set -euo pipefail

if [ "$#" -lt 2 ]; then
  echo "usage: $0 <cli|mcp|sdk> <name>" >&2
  exit 1
fi

TYPE="$1"
NAME="$2"
case "$TYPE" in
  cli|sdk|mcp) ;;
  *) echo "ERROR: unknown type \"$TYPE\" (expected cli|mcp|sdk)" >&2; exit 1 ;;
esac

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
PKG_DIR="$ROOT/interfaces/$NAME-$TYPE"
CLAUDE_SETTINGS="$ROOT/.claude/settings.json"
MANIFEST="$ROOT/.local/interface-installs/$NAME-$TYPE.json"

if [ ! -f "$ROOT/.venv/pyvenv.cfg" ]; then
  echo "ERROR: .venv missing — run \`make install\` first." >&2
  exit 1
fi

# --- figure out the python distribution name -----------------------------
# Prefer the manifest (works for any source). Fall back to pyproject.toml in
# the source dir, else assume <name>-<type>.
PKG_NAME=""
if [ -f "$MANIFEST" ] && command -v jq >/dev/null 2>&1; then
  ref=$(jq -r '.ref' "$MANIFEST")
  case "$ref" in
    "-e "*) PKG_NAME=$(echo "$ref" | awk '{print $2}') ;;
    *) PKG_NAME="$ref" ;;
  esac
fi
SOURCE_PRESENT=1
if [ -z "$PKG_NAME" ]; then
  if [ -f "$PKG_DIR/pyproject.toml" ]; then
    PKG_NAME=$(grep -E '^\s*name\s*=' "$PKG_DIR/pyproject.toml" | head -1 \
               | sed -E 's/.*"([^"]+)".*/\1/' || true)
    [ -z "$PKG_NAME" ] && PKG_NAME="$NAME-$TYPE"
  else
    SOURCE_PRESENT=0
    PKG_NAME="$NAME-$TYPE"
    echo "warning: source dir $PKG_DIR not found; will still attempt to uninstall \`$PKG_NAME\`." >&2
  fi
fi

# Strip extras / version / path syntax from PKG_NAME to get the dist name.
DIST=$(echo "$PKG_NAME" | sed -E 's/\[[^]]*\]//; s/[<>=!~].*$//' \
                       | xargs -I{} basename {})
INSTALLED=0
if "$ROOT/.venv/bin/python" -m pip show "$DIST" >/dev/null 2>&1; then
  INSTALLED=1
fi

cd "$ROOT"
if [ $INSTALLED -eq 1 ]; then
  echo "=== uninstalling $DIST from .venv ==="
  uv pip uninstall -y "$DIST"
else
  echo "note: \`$DIST\` was not installed in .venv; nothing to remove."
fi

# --- MCP deregistration ---------------------------------------------------
if [ "$TYPE" = "mcp" ]; then
  if ! command -v jq >/dev/null 2>&1; then
    echo "warning: jq not on PATH; remove the .mcpServers.\"$NAME\" entry from $CLAUDE_SETTINGS manually." >&2
  elif [ ! -f "$CLAUDE_SETTINGS" ]; then
    echo "warning: $CLAUDE_SETTINGS missing; nothing to deregister." >&2
  elif jq -e --arg n "$NAME" '.mcpServers[$n]' "$CLAUDE_SETTINGS" >/dev/null 2>&1; then
    tmp=$(mktemp)
    jq --arg name "$NAME" 'if .mcpServers then del(.mcpServers[$name]) else . end' \
      "$CLAUDE_SETTINGS" > "$tmp" && mv "$tmp" "$CLAUDE_SETTINGS"
    echo "deregistered $NAME from $CLAUDE_SETTINGS"
  else
    echo "note: \`$NAME\` was not registered in $CLAUDE_SETTINGS; nothing to deregister."
  fi
fi

[ -f "$MANIFEST" ] && rm "$MANIFEST"

if [ $INSTALLED -eq 0 ] && [ $SOURCE_PRESENT -eq 0 ] && [ ! -f "$MANIFEST" ]; then
  echo "no-op: nothing was installed or built for $NAME-$TYPE."
  exit 0
fi
echo "uninstalled: $NAME-$TYPE"
