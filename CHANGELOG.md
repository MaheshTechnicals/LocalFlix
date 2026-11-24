# Changelog

All notable changes to LocalFlix are listed here. Versions follow [semantic versioning](https://semver.org).

## 1.4.2 — 2026-11-24 (open-source release)

### Fixed
- **Settings on phones:** the category bar scrolls sideways. Tapping a category further along (e.g. *Network & Security*) opened the page but scrolled the bar back to *General*. The bar now keeps its position and keeps the selected category in view.

### Added
- Open-source project files: MIT `LICENSE`, `CONTRIBUTING.md`, `CODE_OF_CONDUCT.md`, `SECURITY.md`, issue forms, a pull-request template, Dependabot and `.editorconfig`.
- **Release workflow:** pushing a `v*` tag tests the code, publishes a GitHub release with `localflix.py`, a zip with launchers and SHA-256 sums, and builds a multi-arch Docker image (`amd64`, `arm64`) on `ghcr.io/maheshtechnicals/localflix`.
- CI checks that the web UI still parses as ES2019, for old TV browsers.
- A self-test checks that every UI language has the same translation keys.
- README rewritten: screenshots, per-platform step-by-step setup, file-naming guide, TV guide, FAQ.

## 1.4.1 — 2026-11-24

### Added
- **Opening LocalFlix by IP switches to the DuckDNS address**, but only when the name works on that device. The page first loads a tiny image from the name, which proves the name resolves and its certificate is trusted there. If that fails, for example because the router blocks the name, the device stays on the IP. Path, `?tv=1` and the page hash are kept. You can turn this off under Settings → Network → *Domain redirect*.
- **Plain `http://` on the HTTPS port no longer breaks.** Typing `192.168.0.103:8000` without `https://` used to fail once HTTPS was on. Now the server answers with a small page that moves to the DuckDNS name, or to `https://` on the same address. TLS is now set up per connection; the HTTPS behaviour itself is unchanged.
- The banner's "this computer" link uses `https://` when HTTPS is on.
- `install_acme` uses tarfile's safe `data` filter on Python 3.12+, so the deprecation warning is gone.

## 1.4.0 — 2026-11-24 (fixed address + free HTTPS)

### Added
- **A fixed address even when the router changes the IP:** set a free DuckDNS name (Settings → Network & Security → *DuckDNS domain* and *token*). LocalFlix checks the LAN IP every 2 minutes and points the name at it. It updates only on a change, plus a 6-hourly keep-alive. The router needs no changes, and nothing is exposed to the internet.
  - The banner, QR code, TV hint and browser auto-open use the name as soon as it resolves to this device.
  - The DNS-rebinding guard accepts the name and any name on the HTTPS certificate.
- **Free trusted HTTPS:** `python3 localflix.py https`. It installs acme.sh after asking, gets a Let's Encrypt certificate for the DuckDNS name with a DNS-01 challenge (no open ports), and saves it in LocalFlix's settings.
  - The server runs the renewal check itself every day, so no cron is needed and it works on Termux.
  - A renewed certificate is reloaded without a restart.
  - `https --off` reverts to plain HTTP.
- Settings → Network shows the address status (name → IP, or the DuckDNS error), HTTPS validity, and all addresses.
- `doctor` checks the DuckDNS update and the certificate's remaining days. The banner warns when either needs attention.

## 1.3.0 — 2026-11-24 (full-source audit)

### Changed
- **Recently Added shows titles, not episodes:** a series appears once, as the show (poster, season and episode count), ordered by when it last got a new episode. Movies appear as before. The "See all" page (`#/new`) lists the same way, via the new `GET /api/v1/items?type=titles`.

### Fixed — library and scanner
- **Search index (FTS) re-keyed by rowid.** Deleting by the `UNINDEXED` id column scanned the whole index for every item, so scans got slower with the square of the library size: 8,000 items took 5 s, and 100k files took several minutes while holding the write lock. Migration 5 rebuilds the index.
- A folder that fails to list (flaky NAS, permission hiccup) no longer marks all its files as missing.
- Filesystem checks no longer run while the database write lock is held. A stale network mount used to freeze every write.
- External subtitle lookup caches folder listings. It was O(N²) in folders holding thousands of videos.
- Periodic mount checks no longer re-run series cleanup when no library was due.
- Broken files (truncated MP4 header, probe crash, link pointing outside the library) are marked unplayable instead of being re-probed on every scan.
- Subtitles: UTF-16 files without a BOM are decoded correctly, and VobSub `.idx`/`.sub` pairs no longer show up as empty text tracks.
- MPEG-TS recordings that start mid-packet are recognised.
- A "Scan now" pressed during a scan is no longer lost.
- Background ffmpeg runs at low priority without `preexec_fn`, which is unsafe in a threaded server.
- The Linux sleep inhibitor ends with the server, even after a crash.

### Fixed — streaming
- Audio renditions are padded and cut to the planned length, so the last audio segments always exist.
- Seeking backwards no longer stalls when playback reaches the segments left over from the earlier position.
- A stopped session can't be revived by an in-flight request, which used to leave an orphan ffmpeg and folder.
- When ffmpeg exits cleanly at the real end of a stream it is no longer restarted in a loop.
- A missing init segment (removed by cache cleanup) is regenerated.
- The transcode limit is now checked and claimed atomically.
- The sleep-prevention reference count is thread-safe.
- A corrupt keyframe cache no longer blocks the play request with a slow full-file scan.
- Artwork: items that produce no artwork no longer loop endlessly (404 → regenerate → event → reload).
- Trickplay: a failed trickplay is not retried on every play, and on phones it waits while something is streaming.

### Fixed — security and HTTP
- Switching **Require password** on signs out every device that never entered the password.
- Changing the admin PIN revokes admin rights from other devices.
- Login, admin PIN and profile PIN have separate brute-force counters, so knowing one secret no longer resets the lockout of another.
- With an admin PIN set, LAN devices need the PIN for admin actions even when no password is required. Without a PIN, the server password unlocks admin, as the prompt says.
- No session rows for health checks or HEAD requests. Docker's healthcheck created one every 30 s.
- Subtitle warm-up threads release their DB connection.
- The Host check caches this machine's names. A slow reverse DNS lookup happened on every request.
- hls.js is loaded with a version query, so upgrades reach browsers.
- A file that shrinks while being sent closes the connection instead of hanging it. The idle timeout is restored after a transfer.
- Client disconnects during streaming (TLS EOF, Wi-Fi drop) are not logged as errors.
- Invalid `startTime` values return 400.
- The software-fallback path returns 503/404 instead of 500.
- A second Ctrl+C forces exit, and the browser-open timer no longer delays shutdown.
- `--host ::` also accepts IPv4 clients on Windows.
- The systemd unit works when the path contains spaces.

### Fixed — web UI
- Seek-bar drag cleans up on `pointercancel` (Android back-swipe). Previously the controls could get stuck visible and the next tap would seek.
- The stall watchdog ignores user pauses. It used to downgrade the stream after a long pause.
- Old TV browsers (Chromium < 79) get plain CSS fallbacks for `min()`/`max()`/`clamp()`: hero text position, player bar padding, search box, dialogs.
- Tablets 769–900 px wide get the bottom tab bar; there was no navigation there before.
- Toasts no longer cover the phone tab bar or the player controls.
- Player episodes menu: season 0 (specials), failed requests and a closed player are handled.
- The C/A keys no longer throw before playback has started.
- Series "Mark watched" only changes state once the server confirms it.
- Search: typing in the nav box updates the open search page, and a stale debounce no longer rewrites the URL after you leave.
- Artwork refresh puts posters, not thumbnails, into poster cards.

## 1.2.5 — 2026-11-24

### Fixed
- **Startup banner garbled on phone terminals:** the box and long lines were fixed at about 68 columns, so a ~50-column Termux screen wrapped them into a mess. The banner now sizes itself to the terminal and word-wraps long lines. The QR code is skipped if it cannot fit unbroken.
- **Browser did not open automatically on Android (Termux / proot-distro):** the "headless Linux, no DISPLAY" rule also matched Termux, so the opener never ran. Termux now opens the phone's browser at the LAN address. It uses `termux-open-url`, `termux-open` or `am start`, run with Termux's own environment so it works from inside proot. The exit code is checked so a broken launcher falls through to the next.

## 1.2.4 — 2026-11-24

### Fixed
- **`StopIteration` 500 error on HLS segment requests (seen on Termux/Android):** the segment server checked whether all files of a segment existed. When they didn't, it scanned for the first missing one, but on slow devices ffmpeg could finish writing it between the two checks. The scan then found nothing and crashed the request. It now loops round and serves the segment.

## 1.2.3 — 2026-11-24

### Fixed
- **Player controls flashed and vanished on tap (normal and fullscreen):** phones fire a synthetic `mousemove` right after every tap. It showed the controls, then the tap handler saw them visible and hid them again. That event is now ignored after a touch, and a tap decides show/hide from how the controls looked when the finger touched. A tap shows them instantly; they stay for 4 s (3 s with a mouse); another tap hides them. Double-tap seek is unchanged.

## 1.2.2 — 2026-11-24 (playback hangs, TV remote, browser launch)

### Fixed — videos stuck at "Preparing stream… 99 %"
- **Root cause:** audio tracks were stream-copied after a seek or resume. A copied track starts at the container's seek point (an earlier video keyframe) instead of the requested time, so its timestamps were off by up to the GOP length (measured: 0.02 s or 20.8 s where 30 s was expected). The player then waited forever for audio at the play position while ffmpeg kept encoding, hence "99 %". MPEG-TS files (ADTS AAC) additionally failed with `fragParsingError`. Audio renditions are now always re-encoded (AAC; AC3/E-AC3 when passthrough is enabled), which is cheap and sample-accurate. Measured on 11 problem files: every one now starts in about 2 s from 0:00 and from a resume point (TS-AAC and long-GOP files used to hang).
- Late-starting audio is padded with silence, so playback starts at 0:00 instead of skipping ahead.
- Video playlists end where the video stream ends: files whose audio is longer than the video got an empty last segment and stalled at the very end, which also blocked auto-next.
- The real video stream is always used: `V:0` / its exact index, never embedded cover art.
- **Stall watchdog:** if nothing plays for 25 s (45 s when transcoding), the player switches to the next safer method (direct → remux → transcode) or shows the error screen with Retry and Try transcoding. The progress text says "Buffering…" instead of sitting at "99 %".

### Fixed — TV remote
- OK now activates the focused item on key press, for every control. Previously cards opened only on key release, after a long-press timer, and some TV browsers (Samsung/LG) never send key release, so OK did nothing or opened a small menu. More OK key codes are supported: `Select`, `Accept`, Android `DPAD_CENTER` (23), Samsung legacy (29443). OK with nothing focused now focuses the first item.
- Arrow navigation no longer jumps into a different row: Right from the player's volume button went to the seek bar instead of continuing along the controls. The same rule applies on every page.
- Search on TV starts on the on-screen keyboard.
- The mouse cursor is hidden only while the remote's arrow keys are in use, so LG Magic Remote pointing still works.
- Whole flow verified with keys only: profile → home → rows → details → play/pause/seek → control bar → ⚙ menus → Back × n → TV Shows → series → episode → search keyboard → settings toggles.

### Changed
- On start, the server opens the device's own browser at its **LAN address**, straight to the home page (`http://<lan-ip>:<port>/#/home`). Windows/macOS/Linux use the default browser, WSL uses the Windows browser, and Termux (also inside proot-distro) uses Termux's URL opener. Headless servers and Docker skip it. That machine keeps admin rights even though it doesn't connect over loopback.

## 1.2.1 — 2026-11-24 (phone polish & faster start)

### Fixed
- **Black strip on the right of phone screens:** a long folder path overflowed the page, so Android zoomed the whole page out. Long text now wraps everywhere, and the page can never be wider than the screen.
- **Cropped "My List" / "Mark watched" labels and oversized text:** a phone that had once opened the `/tv` link stayed in TV mode. Touch phones and tablets now ignore the remembered TV flag (unless TV mode is set to "On"), and the labels always fit.
- **Fullscreen lost on the next episode:** fullscreen is now held on the page rather than the player element, so auto-next and the "Next episode" button continue in fullscreen and landscape. Leaving the player still exits fullscreen.
- **Player menus:** sub-menus opened from ⚙ (Speed, Quality, Chapters) have a "‹ Back" row. A tap anywhere outside an open menu closes it without pausing or toggling the controls.

### Faster start & resume
- Encoders start at the resume position as soon as Play is pressed. The player no longer launches a throw-away ffmpeg at 0:00 before the real one.
- Transcodes use 3-second segments, so the first picture needs ~3 s of encoding instead of 6 s. Measured transcode resume: 4.0 s → 1.2 s on the test machine.
- Opening a details page (or a series' "up next" episode) builds that file's keyframe table right away, and the next episode is prepared while you watch. If a table still isn't ready, playback starts after at most 2.5 s instead of 8 s, and simple mode answers after one segment instead of three.
- The player script (hls.js) is pre-loaded when a details page opens. The first fragment is prefetched.

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
