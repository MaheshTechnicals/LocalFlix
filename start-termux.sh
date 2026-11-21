#!/data/data/com.termux/files/usr/bin/sh
# LocalFlix launcher for Termux (Android).  Usage: sh start-termux.sh
cd "$(dirname "$0")" || exit 1
if ! command -v python >/dev/null 2>&1; then
  echo "Installing Python…"
  pkg install -y python || exit 1
fi
if ! command -v ffmpeg >/dev/null 2>&1; then
  printf "ffmpeg is not installed (needed for transcoding, multi-audio and thumbnails). Install now? [Y/n] "
  read -r ans
  case "$ans" in n*|N*) ;; *) pkg install -y ffmpeg ;; esac
fi
if [ ! -d "$HOME/storage/shared" ]; then
  echo "Tip: run 'termux-setup-storage' to let LocalFlix read your phone's Movies/Download folders."
fi
if command -v termux-wake-lock >/dev/null 2>&1; then
  termux-wake-lock
fi
echo "Tip: disable battery optimisation for Termux (Android Settings → Apps → Termux → Battery) for uninterrupted streaming."
exec python localflix.py "$@"
