#!/bin/sh
# These files decide every published number, and a wrong one does not crash.
# Runs in the background; exit 2 wakes Claude only when something failed.
root=$(cd "$(dirname "$0")/../.." && pwd)
f=$(python "$root/.claude/hooks/_path.py")
case "$f" in
  *cfbroot/sim/*.py|*cfbroot/selection.py|*cfbroot/massey.py|*cfbroot/model.py)
    cd "$root" || exit 0
    .venv/Scripts/python.exe -m pytest tests/test_simulation.py tests/test_selection.py -q \
      || { echo "the simulation tests failed after that edit"; exit 2; }
    ;;
esac
