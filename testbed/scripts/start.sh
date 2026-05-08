#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SESSION="banter"

if [ ! -f "$ROOT/.venv/pyvenv.cfg" ]; then
  bash "$ROOT/scripts/install.sh"
fi

if [ -f "$ROOT/.env" ]; then
  set -a
  # shellcheck disable=SC1091
  source "$ROOT/.env"
  set +a
fi

if [ -z "${ANTHROPIC_API_KEY:-}" ] || [[ "$ANTHROPIC_API_KEY" == sk-ant-... ]]; then
  echo "ANTHROPIC_API_KEY not set. edit $ROOT/.env" >&2
  exit 1
fi
export ANTHROPIC_API_KEY
export LITELLM_MASTER_KEY="${LITELLM_MASTER_KEY:-sk-local-master-key}"
export LITELLM_BASE_URL="${LITELLM_BASE_URL:-http://localhost:4000}"
export LITELLM_EVENTS_FILE="${LITELLM_EVENTS_FILE:-$ROOT/.local/litellm/events.jsonl}"
export MLE_BENCH_DATA_DIR="${MLE_BENCH_DATA_DIR:-$ROOT/.local/mle-bench-data}"
export PYTHONPATH="$ROOT/litellm:${PYTHONPATH:-}"

mkdir -p "$(dirname "$LITELLM_EVENTS_FILE")"

tmux kill-session -t "$SESSION" 2>/dev/null || true

ENV_FORWARD=(
  "ANTHROPIC_API_KEY=$ANTHROPIC_API_KEY"
  "LITELLM_MASTER_KEY=$LITELLM_MASTER_KEY"
  "LITELLM_BASE_URL=$LITELLM_BASE_URL"
  "LITELLM_EVENTS_FILE=$LITELLM_EVENTS_FILE"
  "MLE_BENCH_DATA_DIR=$MLE_BENCH_DATA_DIR"
  "PYTHONPATH=$PYTHONPATH"
)
ENV_PREFIX="$(printf '%s ' "${ENV_FORWARD[@]}")"

tmux new-session -d -s "$SESSION" -c "$ROOT" \
  "${ENV_PREFIX} uv run litellm --config litellm/config.yaml --port 4000 --num_workers 1"

tmux split-window -v -t "$SESSION:0.0" -c "$ROOT" \
  "echo 'waiting for LiteLLM...'; until curl -fs http://localhost:4000/health/liveliness >/dev/null 2>&1; do sleep 1; done; echo; echo 'Ready.'; echo '  uv run coordinator --task aerial-cactus-identification --model claude-opus'; echo '  Events log: $LITELLM_EVENTS_FILE'; echo; exec \$SHELL"

tmux select-layout -t "$SESSION:0" even-vertical
tmux set-option -t "$SESSION" pane-border-status top
tmux set-option -t "$SESSION" pane-border-format ' #{pane_index}: #{pane_current_command} '
tmux select-pane -t "$SESSION:0.1"
tmux attach -t "$SESSION"
