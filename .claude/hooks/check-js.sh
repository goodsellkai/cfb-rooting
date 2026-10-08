#!/bin/sh
# The site has no bundler, so a syntax error in the page scripts only shows up
# in a browser. Exit 2 so it comes back as something to fix.
root=$(cd "$(dirname "$0")/../.." && pwd)
f=$(python "$root/.claude/hooks/_path.py")
case "$f" in
  *cfbroot/web/static/*.js)
    node --check "$f" || exit 2
    ;;
esac
