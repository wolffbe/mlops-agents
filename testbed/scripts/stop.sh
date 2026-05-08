#!/usr/bin/env bash
tmux kill-session -t banter 2>/dev/null || true
echo "stopped."
