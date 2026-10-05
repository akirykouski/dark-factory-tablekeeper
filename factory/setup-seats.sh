#!/bin/sh
# Create the three ladder seats as Band Desktop owned headless Claude Code runtimes.
# Usage: setup-seats.sh <absolute result repo path>
set -eu
REPO="$1"; HERE="$(cd "$(dirname "$0")" && pwd)"
M="$HERE/mandates"; [ -d "$M" ] || M="$HERE/../mandates"
for seat in architect drafter fixer; do
  model=$(sed -n 's/^Model: //p' "$M/$seat.md")
  band agent create --session "ladder-$seat" --name "$seat" \
    --description "Factory seat: $seat" --cwd "$REPO" \
    --transport claude-code-cli --spawn-command "$HERE/bin/seat-$seat" \
    --runtime-model "$model" --runtime-auth subscription \
    --claude-context-mode local_config --claude-permission-mode bypassPermissions \
    --instructions-file "$M/$seat.md" --json >/dev/null
  echo "created $seat ($model)"
done
