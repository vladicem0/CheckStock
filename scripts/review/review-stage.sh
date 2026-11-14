#!/usr/bin/env bash
set -euo pipefail
exec python3 "$HOME/.local/lib/checkstock-review/remote_review.py" "$@"
