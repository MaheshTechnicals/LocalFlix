# Changelog

## 1.2.0 — 2026-11-22 (mobile & details redesign)

### Fixed
- **Missing posters:** series cards now borrow (or trigger) their first episode's artwork. On-screen requests jump ahead of the background queue. Generation is ~3× cheaper: one tone-mapped 1280px JPEG serves as thumbnail and backdrop, with a fallback for files that can't seek. The page retries images that are still generating, and live updates also refresh series cards.
- **Player taps:** the controls overlay covered the whole picture and swallowed taps and double-taps, even while hidden. It is now click-through except for real controls.
- **Phone player bar:** on phones (portrait and landscape) and small tablets the bar shows only essentials. Speed, quality, chapters, PiP and next episode moved into the ⚙ menu, so nothing overflows. Safe-area padding for notched phones. The next-episode card is compact on landscape phones.
- **Settings → Libraries on phones:** folder rows stack instead of wrapping the path one letter per line.
- **Font sizes:** fixed steps (16px desktop, 15px ≤1200px, 14px phones, 22px TV) instead of viewport-width scaling. Smaller headings on phones.

### Changed
- **Double-tap / double-click on the video:** centre toggles fullscreen (browser bars hidden, landscape lock on phones), left and right seek ∓10 s and add up with repeated taps. On iPhone, native fullscreen keeps the selected subtitles.
- **Details page redesign** (movies, episodes, series): full-bleed hero, Play / Resume / Start over with round My List and Watched buttons, metadata row with quality badges and remaining time, episode rows with thumbnails, progress and synopsis, a season picker, "More like this", and an "About" section. Full-screen layout on phones. The chapters section was removed from the page; chapters are still available in the player.

## 1.1.0 — 2026-11-21 (library scope)

### Added
- **Library scope** (Settings → Libraries, wizard, `--scope`, empty-library buttons):
  - *Only my folders*: the default; a fresh install indexes just `./output`.
  - *Common video folders*: per-platform standard folders plus USB drives and SD cards.
  - *Entire device*: home folder and every fixed/removable drive.
- Per-platform discovery: Windows known folders (OneDrive-aware), fixed and removable drives (network and CD drives skipped), macOS ~/Movies and /Volumes, Linux XDG dirs and /media, /run/media, /mnt, WSL Windows folders and drives, Termux shared storage, WhatsApp/Telegram folders and SD cards, Docker /media.
- Auto libraries carry an *Auto* badge. Hiding one is remembered. Drives are detected about every minute. Unplugged drives show as *Not connected* and their items are hidden, not deleted.
- Nested folders: the deepest library owns a file, so nothing is listed twice and watch history / My List follow a file into a newly added nested folder.
- Exclusion rules for auto folders (system, app and developer folders, LocalFlix's own cache), `auto_min_size_mb` (default 2 MB), and `auto_scan_interval` (default 30 min).
- `GET /api/v1/libraries/discover?scope=` previews detected folders. DB migration 4 adds the library columns `source`, `auto_key`, `last_scan`, `offline` and `user_disabled`.

### Fixed
- `.ts` files that are not MPEG transport streams (e.g. TypeScript `index.d.ts`) are no longer indexed as video.
- An empty mount point (drive pulled but folder left behind) no longer marks a library's items as missing.

## 1.0.1 — 2026-11-21 (full code audit)

### Security
- DNS-rebinding guard: requests with an unknown `Host` header are rejected (421). Previously any website could get admin access when no password was set.
- `Origin: null` counts as cross-origin. Admin elevation needs the dedicated admin PIN (it no longer falls back to the shared password). Changing the password signs out other sessions.
- Without a selected profile the server never falls back to a PIN-protected profile. Kids profiles and disabled libraries no longer leak through series lists, search or item details. Library routes are admin-only.
- Inputs: non-object JSON, bad `Content-Length` and NaN/Infinity numbers return 400 instead of 500 or corrupting stored state.

### Streaming (M5)
- Stopped encoders are killed hard, so truncated segments are never served after a seek or idle stop.
- Simple HLS mode no longer freezes after ~78 s (throttling now follows the segments the player fetches).
- Remux GOP numbering is correct for files whose video starts after the audio.
- The transcode cap evicts only idle jobs (no more ping-pong between viewers) and also covers simple mode.
- Segments deleted by cache cleanup are regenerated. HW→software fallback drops the HW output so init and segments match. 10-bit HEVC is only copied to clients that report `hevc10`.

### Reliability
- One SQLite connection per request thread is closed when the thread ends (it used to leak file descriptors).
- Keyframe warm-up runs in a single bounded worker. Symlink loops are skipped. A full rescan no longer re-probes unchanged files. Purged items' thumbnails are removed. Trickplay runs on its own worker and isn't blocked while streaming.
- Windows sleep prevention now holds for the whole stream. Termux releases its wake-lock on exit. A locked or unreadable database is no longer mistaken for corruption.
- TLS handshakes moved off the accept loop. A full connection pool answers 503 instead of stalling. IPv6 bind (`--host ::`) works.

### Parsing & subtitles
- Episode titles no longer show release tags (`Show.S01E02.1080p` → "Episode 2"). Future-year numbers stay in the title ("Blade Runner 2049").
- Shared `Subs/` folders only attach matching files. MicroDVD frame-rate headers and TTML tick/frame timestamps are supported.

### Web UI & player
- Password-protected servers show the login page (it used to be blank). Back no longer leaves the app (history depth is tracked). Grid cards size correctly. The profile-menu Settings link works.
- TV: separate focus-outline rule for old engines, `clamp()` fallbacks for Chromium < 79, OK opens on release and long-press opens quick actions, extra scroller padding for focused cards, gamepad OK works.
- Player: the resume position is kept while a stream loads. Auto-next no longer un-marks the episode as watched. Restart loops are capped. Overlapping negotiations don't orphan sessions. The next-episode countdown cancels when you seek back. "Sleep at end of episode" is respected. Listener and Media Session handler leaks are fixed. Pause survives quality and audio changes.

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
