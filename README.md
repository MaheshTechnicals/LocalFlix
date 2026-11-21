# LocalFlix

**Your own streaming service, in one Python file.** Put videos in a folder, run one command, and every phone, laptop, tablet and smart-TV browser on your Wi-Fi gets a polished streaming interface. It includes multi-audio, subtitles, resume across devices, next episode, seek previews and automatic transcoding.

- **One file, no installs:** `localflix.py` uses only the Python standard library (3.10+).
- **Plays anything:** MKV, MP4, AVI, MOV, TS, WebM, HEVC/H.265, AV1, VP9, AC3/EAC3/DTS/TrueHD, multiple audio tracks, embedded and external subtitles. Files a device can't play are remuxed or transcoded on the fly through ffmpeg.
- **TV-first:** works with a remote's arrows, OK and Back keys on Samsung (Tizen), LG (webOS), Android TV, Fire TV and other TV browsers.
- **Private:** LAN only, no telemetry, no account. An optional password/PIN and per-profile PINs are available.

---

## 60-second quick start

> Requirement: **Python 3.10 or newer**. **ffmpeg** is strongly recommended. The setup wizard offers to install it.

### Windows 10/11
```bat
winget install Python.Python.3.13
winget install Gyan.FFmpeg
```
Then double-click **`start-windows.bat`**, or run:
```bat
py localflix.py
```
Allow Python through the firewall when Windows asks.

### macOS
```bash
brew install python ffmpeg
```
```bash
python3 localflix.py
```
Or double-click **`start-mac.command`**. If macOS asks, allow incoming connections for Python.

### Linux / Raspberry Pi OS / WSL
```bash
sudo apt install python3 ffmpeg
```
```bash
python3 localflix.py
```
Fedora: `sudo dnf install python3 ffmpeg` · Arch: `sudo pacman -S python ffmpeg` · Alpine: `sudo apk add python3 ffmpeg`

### Android (Termux)
```bash
pkg install python ffmpeg
```
```bash
termux-setup-storage
```
```bash
sh start-termux.sh
```

### Docker
```bash
docker build -t localflix .
```
```bash
docker run -d --name localflix -p 8000:8000 -v /path/to/videos:/media -v localflix-data:/data localflix
```
On Linux, `--network host` makes the banner show your real LAN address.

### With uv
```bash
uv run localflix.py
```

On first run a short wizard asks for your media folder (default `./output` next to the script), port and an optional PIN. It then caches the hls.js player and tests your hardware encoders. The terminal prints every address to open, with a QR code:

```
  Open on any device on the same Wi-Fi:
    ➜  http://192.168.1.20:8000   (best)
```

Drop videos into the folder. They appear within a minute (or press **Rescan**).

---

## Choosing which videos LocalFlix shows

Out of the box LocalFlix indexes **one folder** only (`./output`, or whatever you pick in the wizard). Nothing else on the device is touched until you choose more in **Settings → Libraries → Which videos to show**, in the wizard, with `--scope`, or from the buttons on the empty-library screen:

| Scope | What gets indexed |
|---|---|
| **Only my folders** (default) | Just the folders you add with *Add folder* (any path, any drive). |
| **Common video folders** | Your platform's standard folders plus USB drives and SD cards as they are plugged in. |
| **Entire device** | Everything above plus your whole home folder and every drive. System, app and developer folders are skipped. |

Folders per platform for *Common*:

| Platform | Folders |
|---|---|
| Windows | Videos and Downloads (real locations, including OneDrive redirection), Public Videos, OneDrive\Videos, other fixed and USB drives |
| macOS | ~/Movies, ~/Downloads, external disks in /Volumes |
| Linux | XDG Videos/Downloads (localised names work), /media/$USER, /run/media/$USER, /mnt. Under WSL: Windows Videos/Downloads |
| Android (Termux) | Movies, Download, DCIM (camera), Videos, Telegram, WhatsApp Video, SD cards. Run `termux-setup-storage` once |

*Entire device* adds the home folder (Linux/macOS), all fixed and removable drives (Windows; network and CD drives are skipped), the Windows drives under WSL, and internal storage (Android).

```bash
python localflix.py --scope common
```

How it behaves:
- **Your own folders always win.** If a folder you added sits inside an auto-detected one (e.g. `~/Videos/Anime` inside the home folder), its files belong to your folder and its type (Movies / TV Shows) applies. No file is listed twice, and watch history moves along with the file.
- **Auto folders show an *Auto* badge.** The bin icon *hides* them. Discovery never brings a hidden folder back, and its watch history is kept if you show it again.
- **Unplugged drives and SD cards** show as *Not connected*. Their videos are hidden, not deleted, and come back with their watch history when the drive returns. New drives are picked up within about a minute.
- **Skipped in auto folders:** system and app folders (`Windows`, `Program Files`, `AppData`, `Library`, `node_modules`, `.cache`, `Android/data`, `*.app`, photo-library packages, …), LocalFlix's own cache, and files smaller than *auto_min_size_mb* (default 2 MB: app sounds, ads, thumbnails).
- `.ts` files are only treated as video if they really are MPEG transport streams, so TypeScript files are never indexed.
- **Scan timing:** auto folders can be huge, so they are rescanned every *auto_scan_interval* (default 30 min) instead of every minute. *Rescan library* forces a scan now.
- **Privacy:** with *Common* or *Entire device*, everyone who can open LocalFlix on your network sees those videos. Set a password under Network & Security if that matters.

---

## Open on your TV

1. Make sure the TV and the computer running LocalFlix are on the **same Wi-Fi / network**.
2. Open the TV's web browser: *Samsung Internet* (Tizen), *Web Browser* (LG webOS), *Silk* (Fire TV), *Chrome* or *TV Bro* (Android TV / Google TV).
3. Type the address shown in the terminal and add **`/tv`**, for example `http://192.168.1.20:8000/tv`.
4. TV mode turns on automatically: big text, focus highlight, no mouse needed.

   *Screenshot placeholder: TV home screen*

| Remote key | Action |
|---|---|
| Arrows | Move between items · in the player ←/→ seek ±10 s (hold to speed up), ↑ show controls |
| OK / Enter | Open / play / pause |
| Back | Close dialog → leave the player → previous page. Press twice on Home to exit |
| Play/Pause, Stop, ⏪ ⏩, ⏭ | Media keys work in the player |
| 🔴 🟢 🟡 🔵 | Subtitles · Audio · Speed · Next episode |

Tip: on the computer, click **Open on TV** in the top bar for a big address and a QR code. If you set a password, an admin can create a **one-time login QR**, so nobody has to type a password with a remote.

---

## Features

| Area | What you get |
|---|---|
| Library | Automatic movie / TV-show / episode detection from file and folder names, seasons, posters from local `poster.jpg`/`fanart.jpg` or generated frames, optional TMDB metadata, instant search |
| Playback | Direct Play when the device supports the file. Otherwise Direct Stream (remux, video copied) or Transcode (H.264, hardware accelerated when available). Instant seeking anywhere, even while transcoding |
| Audio | Every audio track selectable, instant switching, preferred-language auto-pick, 5.1 → stereo downmix, optional AC3/EAC3 passthrough |
| Subtitles | Embedded text subs, external `.srt .vtt .ass .ssa .sub .ttml` (`Movie.en.srt`, `Movie.hi.forced.srt`, `Subs/`), image subs (PGS/VobSub) burned in. Size, colour, background, edge, position and delay settings |
| Player | Resume, Skip Intro/Credits (from chapters), next-episode countdown, seek-bar thumbnails, chapters, speed, quality, sleep timer, fit/fill/crop, PiP, AirPlay, Cast (Remote Playback), lock-screen controls, stats overlay |
| Profiles | "Who's watching?", per-profile PIN, kids profiles, Continue Watching and My List synced across every device |
| Security | LAN only by default, optional password/PIN, admin PIN, rate-limited login, private-network-only filter, files outside your folders are never served |

### Keyboard shortcuts (player)
`Space`/`K` play · `J`/`L` ±10 s · `←`/`→` ±5 s · `↑`/`↓` volume · `M` mute · `F` fullscreen · `C` subtitles · `A` audio · `[`/`]` subtitle delay · `<`/`>` speed · `N` next · `I` stats · `0–9` jump to 0–90 % · `Esc` back · `?` help

### Touch (phones/tablets)
Double-tap left/right to skip ±10 s · tap to show controls · long-press for 2× speed · swipe up/down on the right for volume, on the left for brightness.

---

## Supported formats

| Container | Video | Audio | Subtitles |
|---|---|---|---|
| MP4, M4V, MOV, MKV, WebM, AVI, TS/M2TS, MPG, WMV, FLV, OGV, 3GP, VOB, DivX | H.264, HEVC (8/10-bit, HDR10/HLG/Dolby Vision*), AV1, VP9, VP8, MPEG-4/Xvid, MPEG-2, VC-1, … | AAC, MP3, Opus, Vorbis, FLAC, AC3, EAC3, DTS, TrueHD, PCM, … | SRT, ASS/SSA, WebVTT, mov_text, MicroDVD, TTML, PGS, VobSub, DVB |

\*HDR is tone-mapped to SDR for screens that can't show HDR. Anything the browser can't decode is converted by ffmpeg. Without ffmpeg only browser-native files (usually MP4/WebM with H.264/AAC) play.

---

## Command line

```text
python localflix.py                         serve (first run → setup wizard)
python localflix.py serve -f DIR [-f DIR2] [--scope folders|common|all] [-p PORT] [--host IP] [--no-open] [--tv] [--no-wizard]
                          [--dev-web PATH] [--ssl-cert FILE --ssl-key FILE]
python localflix.py setup                   interactive wizard
python localflix.py doctor [--full|--json]  diagnostics (also in Settings → Advanced)
python localflix.py scan [--full]           rescan libraries, then exit
python localflix.py clear-cache [--all]
python localflix.py service [print|install|uninstall]   start automatically at login/boot
python localflix.py version
python localflix.py --selftest              built-in test suite
python localflix.py --dump-web DIR          write the embedded web files (edit, then use --dev-web DIR)
python localflix.py fixtures DIR            generate test videos with ffmpeg
```

Environment overrides: `LOCALFLIX_HOME` (data folder), `LOCALFLIX_PORT`, `LOCALFLIX_MEDIA`, `FFMPEG_PATH`.

Data locations: Windows `%APPDATA%\LocalFlix` + `%LOCALAPPDATA%\LocalFlix\Cache` · macOS `~/Library/Application Support/LocalFlix` + `~/Library/Caches/LocalFlix` · Linux/Termux `~/.config/localflix` + `~/.cache/localflix`. Logs are in `<config>/logs`.

### Start automatically
`python localflix.py service` prints the right file for your OS: a systemd *user* unit (Linux), a launchd agent (macOS), a Startup-folder `.bat` and `schtasks` command (Windows), or a Termux:Boot script. `service install` writes and enables it, and no admin rights are needed.

---

## Firewall fixes

- **Windows:** when the prompt appears, allow *Private networks*. Missed it? Run in an **admin** terminal:
  ```bat
  netsh advfirewall firewall add rule name="LocalFlix" dir=in action=allow protocol=TCP localport=8000
  ```
- **macOS:** System Settings → Network → Firewall → Options → allow incoming connections for Python.
- **Linux (ufw):**
  ```bash
  sudo ufw allow 8000/tcp
  ```
- **WSL2:** use the Windows host's IP and add a port proxy, or run LocalFlix directly on Windows.
- **Docker:** publish the port (`-p 8000:8000`) or use `--network host`.

`python localflix.py doctor` prints the exact command for your machine.

---

## Termux guide (Android)

1. Install **Termux** from F-Droid (the Play Store build is outdated).
2. Run `pkg install python ffmpeg` and `termux-setup-storage` (grant the permission).
3. Point LocalFlix at your phone's videos, for example: `python localflix.py -f ~/storage/shared/Movies -f ~/storage/shared/Download`.
4. **Keep it running:** `start-termux.sh` takes a wake-lock automatically. Also open Android Settings → Apps → Termux → Battery → *Unrestricted*.
5. **Hotspot:** you can serve to a laptop connected to the phone's hotspot. The banner lists every address.
6. **Start on boot:** install **Termux:Boot**, open it once, then run `python localflix.py service install`.

Phones transcode slowly. Prefer MP4/H.264 files, or set *Max streaming quality* to 720p.

---

## FAQ / troubleshooting

**No sound?** The audio is probably AC3/DTS/TrueHD and your browser can't decode it. Install the full ffmpeg (see Quick start) and LocalFlix will convert the audio to AAC. Run `doctor` to confirm ffmpeg is found.

**Buffering or slow start?** Lower *Max streaming quality* (Settings → Playback), enable hardware acceleration (Settings → Transcoding → *Re-detect*), or use the wired network for the server. Remuxed files (MKV with H.264) start almost instantly. Full transcodes need a reasonably fast CPU or GPU.

**My TV can't open the page.** Type the raw IP address (not `localflix.local`, which many TVs can't resolve). Use `http://`, not `https://`. Check the firewall and that both devices are on the same network, not a guest Wi-Fi.

**A file doesn't show up.** Check that the extension is supported and the file isn't named like a release sample (`sample.mkv`, `*-sample.*`). Then press *Rescan* or run `python localflix.py scan`. Errors appear under Settings → Libraries.

**Wrong show/episode detected?** Use `Show Name/Season 01/Show Name - S01E02.mkv` or `Movies/Title (2019)/Title (2019).mkv`.

**Subtitles out of sync?** Press `[` / `]` (or use the subtitles menu) to shift them by 250 ms.

**hls.js not cached (offline install)?** Safari and many TVs play HLS natively. Otherwise connect once to the internet and LocalFlix downloads and verifies hls.js automatically.

**Reset everything?** Stop the server and delete the data folder listed under *Data locations*.

---

## Development

- Everything lives in `localflix.py`, split into `# ===== SECTION: name =====` blocks: platform, settings, database, ffmpeg, name-parser, probe, subtitles, scanner, artwork, tmdb, library-queries, playback-decision, hls-engine, qr, auth, http, install-helpers, doctor, app, wizard, service, fixtures, selftest, cli, web-ui.
- UI work: `python localflix.py --dump-web web/`, then `python localflix.py --dev-web web/` and edit `web/app.js` / `web/app.css` live. Paste the files back into the `APP_JS` / `APP_CSS` constants when done. The core JS must stay ES2019, with no `?.` or `??`.
- Tests: `python localflix.py --selftest` covers the name parser, Range parsing, MIME map, segment tables, a 37-case playback decision matrix, subtitles, path safety, settings and schema migrations, QR, and API contract tests that run a real server with generated fixture media, including on-demand HLS, transcode, auth and CSRF.
- Lint: `ruff check --isolated localflix.py` and `flake8 localflix.py`.

### Verification status

Verified automatically on Linux (Python 3.12, ffmpeg 6.1):
- self-test (27 tests);
- HLS timestamp alignment across seeks for remux (B-frame and MPEG-TS sources) and transcode;
- 10,000-file library: Home ~50 ms, search ~15 ms;
- no orphaned ffmpeg after SIGTERM or SIGKILL;
- Chromium UI walkthrough: profiles, home, details, playback with audio switching and subtitles, TV-mode D-pad navigation, settings, Open-on-TV modal.

Still to verify by hand on real hardware (PRD §16.2): Windows / macOS / Raspberry Pi / Termux first-run, Samsung/LG/Android TV browsers, Safari/iOS native HLS, a Lighthouse audit, and HW encoders (NVENC/QSV/VAAPI/VideoToolbox) on machines that have them.

---

## Licences & credits

LocalFlix is original work. It is not affiliated with or styled on any commercial streaming service's branding. It uses [hls.js](https://github.com/video-dev/hls.js) (Apache-2.0, downloaded at runtime and checksum-verified) and calls [FFmpeg](https://ffmpeg.org) (LGPL/GPL) as an external program. Optional metadata from TMDB: *This product uses the TMDB API but is not endorsed or certified by TMDB.*
