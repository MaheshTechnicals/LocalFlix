# Changelog

## 1.0.0 — 2026-11-21

First release, built to the LocalFlix PRD (milestones M1–M8).

### Dependency versions (looked up on 2026-11-21)

| Component | Version | Notes |
|---|---|---|
| Python (development) | 3.12.3 (latest stable upstream: 3.15.2) | Minimum supported: **3.10**. PEP 723 header included (`uv run localflix.py`). |
| hls.js | **1.7.4** (npm `latest`) | Pinned in `HLSJS_VERSION`. SHA-384 `p9vgU/7IYJWd3I4STrtqxqR/2cAQlhIX7D/HrTnIMEjjcUox165b7se7sxmm3zDg`. Cached to `<cache>/vendor/hls.min.js`. |
| FFmpeg / ffprobe | Recommended: latest stable (9.1 "Gauss", released upstream). Tested with **6.1.1** (Ubuntu 24.04). | Any build ≥ 5 works. The static-build helper picks the newest `n<ver>-latest` GPL release from BtbN and verifies its SHA-256. |
| Optional | `segno` (QR), `zeroconf` (mDNS), `chardet` (subtitle encodings) | Detected at runtime. A built-in pure-Python QR encoder is used when `segno` is missing. |

### Added
- Single-file server `localflix.py` (stdlib only) with the web UI embedded, plus launchers (`start-windows.bat`, `start-linux.sh`, `start-mac.command`, `start-termux.sh`), a `Dockerfile` and a GitHub Actions CI matrix.
- CLI: `serve` (default), `setup` (wizard), `doctor`, `scan`, `clear-cache`, `service print|install|uninstall`, `version`, `fixtures`, `--selftest`, `--dump-web`, `--dev-web`.
- Platform support: per-OS config/cache/log paths, UTF-8/ANSI console, Windows long paths, process-tree kill (taskkill / killpg), sleep prevention (SetThreadExecutionState / caffeinate / systemd-inhibit / termux-wake-lock), LAN IP detection that puts virtual bridges last, automatic port fallback, firewall hints, graceful shutdown with no orphaned ffmpeg.
- Library: incremental `os.scandir` scanner, a "guessit-lite" name parser (64-case test table), ffprobe metadata, external subtitle discovery, local and generated artwork (WebP/JPEG), trickplay sprites, optional TMDB, FTS5 search with a LIKE fallback.
- Playback: capability negotiation, Direct Play → Direct Stream (remux) → Transcode, **on-demand HLS** (fMP4/CMAF, VOD playlist up front, keyframe-exact GOP segmentation for remux, restart-on-seek, a separate job per audio rendition, throttling, idle kill, transcode cap, LRU cache), **simple HLS mode** (single EVENT-playlist ffmpeg, TS legacy), HW encoder detection by test encode with software fallback, HDR→SDR tone-mapping, image-subtitle burn-in, quality caps.
- Subtitles: SRT/ASS/SSA/MicroDVD/TTML → WebVTT (keeps italics, bold and colours), encoding detection (BOM → UTF-8 → UTF-16 → chardet → cp1252 → latin-1), custom overlay renderer, delay and style settings.
- Profiles with PINs, kids filter, server-side watch state, My List, optional server password (PBKDF2-SHA256, 240k iterations), admin PIN, login rate limiting, one-time QR login, private-network-only mode, CSRF and Origin checks, CSP with a nonce.
- Web UI: profile picker, home with hero and rows, hover cards, details/series modals, library grids with filters, search (with an on-screen keyboard on TV), settings generated from the server schema, Open-on-TV modal with QR, English/Hindi i18n, Dark/OLED/Light/Auto themes, accent colours.
- TV: UA auto-detect plus `?tv=1` and `/tv`, 10-foot CSS, geometry-based spatial navigation with row memory, Tizen/webOS/Android TV/Fire TV key codes, media and colour keys, Back-to-exit.
- Player: trickplay scrubbing, chapters, Skip Intro/Credits, next-episode countdown, sleep timer, audio/subtitle/speed/quality/episode/chapter menus, fit modes, PiP, AirPlay, Remote Playback, Media Session, stats overlay, touch gestures, keyboard shortcuts, automatic fallback on errors.

### Known limitations
- Manual checks on real Windows, macOS, Termux and smart-TV hardware (PRD §16.2 #1–3) and a Lighthouse audit were not run in the build environment. See README → "Verification status".
- flake8 is configured with `extend-ignore = E501` because the embedded web assets cannot fit the 79-column limit. `ruff check --isolated` passes with default rules.
