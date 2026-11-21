#!/usr/bin/env sh
# LocalFlix launcher for Linux / Raspberry Pi / WSL.  Usage: ./start-linux.sh [serve options]
cd "$(dirname "$0")" || exit 1
PY=""
for c in python3 python; do
  if command -v "$c" >/dev/null 2>&1 && "$c" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' 2>/dev/null; then
    PY="$c"; break
  fi
done
if [ -z "$PY" ]; then
  echo "Python 3.10+ is required."
  echo "  Debian/Ubuntu/Raspberry Pi OS: sudo apt install python3"
  echo "  Fedora: sudo dnf install python3   Arch: sudo pacman -S python   Alpine: sudo apk add python3"
  exit 1
fi
exec "$PY" localflix.py "$@"
