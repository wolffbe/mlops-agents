#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OS="$(uname -s | tr '[:upper:]' '[:lower:]')"

ensure_macos() {
  if ! command -v brew >/dev/null 2>&1; then
    cat >&2 <<EOF
Homebrew is required on macOS. Install with:
  /bin/bash -c "\$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
EOF
    exit 1
  fi
  for pkg in tmux make uv node; do
    if ! command -v "$pkg" >/dev/null 2>&1; then
      echo "installing $pkg via brew..."
      brew install "$pkg"
    fi
  done
  if ! command -v claude >/dev/null 2>&1; then
    echo "installing claude code via npm..."
    npm install -g @anthropic-ai/claude-code
  fi
}

ensure_linux() {
  if command -v apt-get >/dev/null 2>&1; then
    sudo apt-get update -y
    sudo apt-get install -y make tmux curl git ca-certificates nodejs npm
  elif command -v dnf >/dev/null 2>&1; then
    sudo dnf install -y make tmux curl git ca-certificates nodejs npm
  elif command -v pacman >/dev/null 2>&1; then
    sudo pacman -Sy --noconfirm make tmux curl git ca-certificates nodejs npm
  else
    echo "unsupported package manager. install make, tmux, curl, git, nodejs manually." >&2
  fi
  if ! command -v uv >/dev/null 2>&1; then
    echo "installing uv..."
    curl -LsSf https://astral.sh/uv/install.sh | sh
    export PATH="$HOME/.cargo/bin:$HOME/.local/bin:$PATH"
  fi
  if ! command -v claude >/dev/null 2>&1; then
    echo "installing claude code via npm..."
    npm install -g @anthropic-ai/claude-code
  fi
}

case "$OS" in
  darwin) ensure_macos ;;
  linux)  ensure_linux ;;
  *) echo "unsupported OS: $OS" >&2; exit 1 ;;
esac

echo "syncing python deps..."
cd "$ROOT"
uv sync

if [ -f "$ROOT/requirements-base.txt" ]; then
  echo "installing base agent packages..."
  uv pip install -r "$ROOT/requirements-base.txt"
fi

if [ ! -f "$ROOT/.env" ] && [ -f "$ROOT/.env.example" ]; then
  cp "$ROOT/.env.example" "$ROOT/.env"
  echo "created .env from .env.example."
fi

# Kaggle creds — must be a legacy kaggle.json (the new access_token format
# is incompatible with mlebench's pinned kaggle 1.6.17). Prompt the user to
# drop one in the project-local .kaggle/ folder if missing.
KAGGLE_FILE="$ROOT/.kaggle/kaggle.json"
if [ ! -f "$KAGGLE_FILE" ]; then
  cat <<EOF

Kaggle credentials missing.

To download competitions, place a legacy kaggle.json (NOT the access_token blob)
at:

  $KAGGLE_FILE

Get it from: https://www.kaggle.com -> Settings -> API -> "Create New Token"
then move/copy the downloaded file. The format must be:
  {"username": "...", "key": "..."}

If your browser only offered an "access_token" file, expire the current token
in the API panel and create a fresh one — the legacy form is still issued.

After placing the file, run:
  chmod 600 $KAGGLE_FILE

Skipped for now — install will continue, but make prepare-task will fail until
this is set.
EOF
fi

echo
echo "install complete. next steps:"
echo "  1. edit .env and set ANTHROPIC_API_KEY"
echo "  2. drop kaggle.json at .kaggle/kaggle.json (see prompt above)"
echo "     then accept rules at https://www.kaggle.com/c/<competition>/rules"
echo "  3. make prepare-task TASK=<slug>"
echo "  4. make start && make task-test"
