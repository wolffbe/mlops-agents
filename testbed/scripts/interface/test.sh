#!/usr/bin/env bash
# Verify that built interfaces are installed and reachable by the agent.
#   cli: binary on .venv/bin and `<name> --help` exits 0.
#   sdk: package importable in the .venv python.
#   mcp: binary on .venv/bin and registered in .claude/settings.json under .mcpServers[<name>].
#
# Usage:
#   make interface-test                       # check every interfaces/<name>-<type>
#   make interface-test NAME=hops             # check all three types for `hops`
#   make interface-test TYPE=cli NAME=hops    # check just one
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
TYPE="${1:-}"
NAME="${2:-}"

ok=0; fail=0; missing=0

check_one() {
  local typ="$1" nm="$2"
  local pkg_dir="$ROOT/interfaces/$nm-$typ"
  if [ ! -d "$pkg_dir" ]; then
    printf "  %-30s %s\n" "$nm-$typ" "missing (no interfaces/$nm-$typ source)"
    missing=$((missing + 1))
    return
  fi

  case "$typ" in
    cli)
      if "$ROOT/.venv/bin/$nm" --help >/dev/null 2>&1; then
        printf "  %-30s %s\n" "$nm-$typ" "ok (binary callable)"
        ok=$((ok + 1))
      else
        printf "  %-30s %s\n" "$nm-$typ" "FAIL (binary not on .venv/bin or --help failed)"
        fail=$((fail + 1))
      fi
      ;;
    mcp)
      local bin="$ROOT/.venv/bin/$nm-mcp"
      local registered="no"
      if command -v jq >/dev/null 2>&1 \
         && jq -e --arg n "$nm" '.mcpServers[$n]' "$ROOT/.claude/settings.json" >/dev/null 2>&1; then
        registered="yes"
      fi
      if [ -x "$bin" ] && [ "$registered" = "yes" ]; then
        printf "  %-30s %s\n" "$nm-$typ" "ok (binary present, registered in .claude/settings.json)"
        ok=$((ok + 1))
      else
        printf "  %-30s %s\n" "$nm-$typ" "FAIL (binary=$([ -x "$bin" ] && echo yes || echo no), registered=$registered)"
        fail=$((fail + 1))
      fi
      ;;
    sdk)
      # Resolve python distribution name from pyproject.toml.
      local pkg
      pkg=$(grep -E '^\s*name\s*=' "$pkg_dir/pyproject.toml" 2>/dev/null \
            | head -1 | sed -E 's/.*"([^"]+)".*/\1/' || true)
      pkg="${pkg:-$nm-$typ}"
      if "$ROOT/.venv/bin/python" -c "import importlib, sys; importlib.import_module('${pkg//-/_}')" >/dev/null 2>&1; then
        printf "  %-30s %s\n" "$nm-$typ" "ok (importable)"
        ok=$((ok + 1))
      elif "$ROOT/.venv/bin/python" -c "import importlib; importlib.import_module('$nm')" >/dev/null 2>&1; then
        printf "  %-30s %s\n" "$nm-$typ" "ok (importable as $nm)"
        ok=$((ok + 1))
      else
        printf "  %-30s %s\n" "$nm-$typ" "FAIL (cannot import in .venv)"
        fail=$((fail + 1))
      fi
      ;;
    *)
      printf "  %-30s %s\n" "$nm-$typ" "FAIL (unknown type)"
      fail=$((fail + 1))
      ;;
  esac
}

echo "interface-test in $ROOT/interfaces/"

if [ -n "$TYPE" ] && [ -n "$NAME" ]; then
  check_one "$TYPE" "$NAME"
elif [ -n "$NAME" ]; then
  for typ in cli mcp sdk; do
    [ -d "$ROOT/interfaces/$NAME-$typ" ] && check_one "$typ" "$NAME"
  done
else
  # Scan everything that's been built.
  shopt -s nullglob
  found=0
  for d in "$ROOT/interfaces/"*-cli "$ROOT/interfaces/"*-mcp "$ROOT/interfaces/"*-sdk; do
    [ -d "$d" ] || continue
    found=1
    base=$(basename "$d")
    typ="${base##*-}"
    nm="${base%-$typ}"
    check_one "$typ" "$nm"
  done
  if [ $found -eq 0 ]; then
    echo "  no interfaces built yet (interfaces/ is empty)"
  fi
fi

echo
echo "summary: ok=$ok fail=$fail missing=$missing"
[ $fail -eq 0 ] && [ $missing -eq 0 ]
