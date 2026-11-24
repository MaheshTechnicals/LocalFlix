# Contributing to LocalFlix

Thanks for helping. Bug reports, TV and device test results, translations, docs and code are all welcome.

## Ground rules

- **One file, standard library only.** `localflix.py` must run with nothing but Python 3.10+ (ffmpeg is an optional external program). Don't add pip dependencies. Optional extras like `zeroconf` must stay optional.
- **Old TVs matter.** The web UI's JavaScript must stay **ES2019**: no `?.`, no `??`, no class fields. Check by parsing it, as in [Checks before a pull request](#checks-before-a-pull-request).
- **LAN-first and private.** No telemetry, no accounts, no cloud services by default.
- Be kind. See the [Code of Conduct](CODE_OF_CONDUCT.md).

## Getting set up

```bash
git clone https://github.com/MaheshTechnicals/LocalFlix.git
```
```bash
cd LocalFlix
```
```bash
python3 localflix.py fixtures /tmp/lf-media --small
```
```bash
python3 localflix.py --home /tmp/lf-home serve -f /tmp/lf-media --no-open --no-wizard
```

The last two commands generate a few test videos with ffmpeg and start a throw-away server. Its data lives in `/tmp/lf-home`, so your real library is untouched.

## Where things live

Everything is in `localflix.py`, split into `# ===== SECTION: name =====` blocks:

| Section | What it does |
|---|---|
| `platform`, `media-discovery` | OS detection, LAN addresses, which folders the library scope covers |
| `settings`, `database` | Settings schema, SQLite (WAL) and forward-only migrations |
| `ffmpeg`, `probe`, `subtitles` | ffmpeg discovery, hardware-encoder tests, media probing, subtitle conversion to WebVTT |
| `name-parser`, `scanner`, `artwork`, `tmdb` | Movie/episode detection, library scans, thumbnails and posters, optional TMDB |
| `library-queries` | Home rows, browse, search, details |
| `playback-decision`, `hls-engine` | Direct play, remux or transcode, on-demand fMP4 HLS |
| `auth`, `http` | Sessions, PINs, CSRF and Host checks, the HTTP server and API routes |
| `app`, `wizard`, `service`, `cli` | Startup, banner, first-run wizard, autostart files, commands |
| `selftest` | The built-in test suite |
| `web-ui` | The embedded HTML, CSS and JS (`INDEX_HTML`, `APP_CSS`, `APP_JS`) |

### Working on the UI

```bash
python3 localflix.py --dump-web web/
```
```bash
python3 localflix.py --dev-web web/
```

`--dump-web` writes the embedded files out. With `--dev-web`, the server serves them from disk, so you can edit `web/app.js` and `web/app.css` and just reload the browser. When you're done, copy the changes back into `APP_JS` / `APP_CSS` in `localflix.py`. The `web/` folder is git-ignored.

## Checks before a pull request

```bash
python3 localflix.py --selftest
```
```bash
pip install ruff flake8 && ruff check --isolated localflix.py && flake8 localflix.py
```
```bash
python3 localflix.py --dump-web web && npx --yes acorn@8 --ecma2019 --silent web/app.js
```

CI runs the same checks on Linux, macOS and Windows, with Python 3.10 and 3.13. It also runs a real-server HLS smoke test.

## Pull requests

1. Fork the repo and create a branch: `git checkout -b fix/short-description`.
2. Keep the change focused, and add or adjust a self-test when you fix a bug.
3. Add a line under a new version heading in [CHANGELOG.md](CHANGELOG.md).
4. Open the PR and fill in the template. For UI changes, include a screenshot from a phone and a desktop.

Commit messages follow [Conventional Commits](https://www.conventionalcommits.org/) loosely: `fix: …`, `feat: …`, `docs: …`.

## Reporting bugs and testing devices

Use the **Bug report** issue form. These details help most:
- the output of `python3 localflix.py doctor`;
- the device and browser that fails (for a TV, the brand and model year);
- the file's codecs, shown in the item's *Info* panel.

Device reports are valuable even when everything works. Open a *Feature request / feedback* issue saying "works on …".

## Translations

UI strings live in the `I18N` dictionaries inside `APP_JS` (`en`, `hi`, …). To add a language, copy the `en` block and translate the values. Keep every key; the self-test checks that all languages have the same keys.

## Releases (maintainers)

1. Bump `VERSION` in `localflix.py` and add the CHANGELOG entry.
2. Merge to `main`, then tag: `git tag v1.2.3 && git push origin v1.2.3`.
3. The **Release** workflow tests the tag, attaches `localflix.py` and a zip, and publishes the Docker image to GHCR.
