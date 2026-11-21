#!/bin/bash
# LocalFlix launcher for macOS — double-click in Finder.
cd "$(dirname "$0")" || exit 1
PY=""
for c in python3 /opt/homebrew/bin/python3 /usr/local/bin/python3; do
  if command -v "$c" >/dev/null 2>&1 && "$c" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' 2>/dev/null; then
    PY="$c"; break
  fi
done
if [ -z "$PY" ]; then
  echo "Python 3.10+ is required. Install it with:  brew install python"
  echo "(or download it from https://www.python.org/downloads/macos/)"
  read -r -p "Press Enter to close…"
  exit 1
fi
"$PY" localflix.py "$@"
status=$?
if [ $status -ne 0 ]; then
  echo
  echo "LocalFlix stopped with an error. Run: $PY localflix.py doctor"
  read -r -p "Press Enter to close…"
fi
exit $status
