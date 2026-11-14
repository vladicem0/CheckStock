#!/usr/bin/env bash
# Run intentionally from a trusted copy of this directory; never from review input.
set -euo pipefail
umask 077
source_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
policy_source="${1:-}"
runtime="$HOME/.local/lib/checkstock-review"
policy="$HOME/.claude/skills/stage-review/SKILL.md"
config="$HOME/.config/checkstock-review/config.json"

for source_file in remote_review.py protocol.py review.schema.json review-stage.sh; do
    test -f "$source_dir/$source_file" || { echo "Missing runtime file: $source_file" >&2; exit 1; }
done
if ! test -f "$policy" && { test -z "$policy_source" || ! test -f "$policy_source"; }; then
    echo 'First installation requires an explicitly supplied reviewer policy file.' >&2
    exit 1
fi
install -d -m 700 "$runtime" "$(dirname -- "$policy")" "$(dirname -- "$config")" "$HOME/bin"
if ! test -e "$policy"; then
    # noclobber also protects against a concurrent installation.
    (set -o noclobber; cat -- "$policy_source" > "$policy")
fi
for source_file in remote_review.py protocol.py review.schema.json; do
    install -m 600 "$source_dir/$source_file" "$runtime/$source_file"
done
install -m 700 "$source_dir/review-stage.sh" "$HOME/bin/review-stage.sh"
python3 - "$config" <<'PY'
import json
from pathlib import Path
import sys

path = Path(sys.argv[1])
if not path.exists():
    home = Path.home()
    config = {
        "repository": str(home / ".local/share/checkstock-review/repository"),
        "repository_url": "git@github-checkstock:vladicem0/CheckStock.git",
        "state_root": str(home / ".local/state/checkstock-review"),
        "policy_file": str(home / ".claude/skills/stage-review/SKILL.md"),
        "claude_executable": str(home / ".local/bin/claude"),
        "timeout_seconds": 900,
    }
    with path.open("x", encoding="utf-8") as handle:
        json.dump(config, handle, indent=2)
        handle.write("\n")
PY
python3 -m py_compile "$runtime/remote_review.py" "$runtime/protocol.py"
bash -n "$HOME/bin/review-stage.sh"
echo 'Review runtime installed; existing policy and configuration preserved.'
