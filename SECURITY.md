# Security Policy

LocalFlix is meant to run on a home network. It can still be reached by everyone on that network, and sometimes from further away by mistake, so security reports are taken seriously.

## Supported versions

Only the **latest release** gets security fixes. Upgrading is a single file: replace `localflix.py` and restart.

## Reporting a vulnerability

**Please don't open a public issue for security problems.**

Report privately through GitHub: go to **Security → Advisories → Report a vulnerability** on this repository ([direct link](https://github.com/MaheshTechnicals/LocalFlix/security/advisories/new)). Include:

- the LocalFlix version (`python3 localflix.py version`) and OS;
- what an attacker can do, and from where (same Wi-Fi, a web page the victim visits, …);
- steps or a proof of concept.

You'll get a reply within **7 days**. After a fix ships, you'll be credited in the advisory and the changelog unless you'd rather stay anonymous.

## What's in scope

- Reading files outside the configured media folders (path traversal, symlinks).
- Getting past the password, the admin PIN or a profile PIN, or getting around the login rate limit.
- CSRF, DNS rebinding or Host-header issues that let a web page control a LocalFlix server.
- Running commands through file names, settings or ffmpeg arguments.
- XSS in the web UI from media file names, metadata or subtitles.

## Out of scope

- Anything that needs the attacker to already control the machine running LocalFlix.
- Running with no password on a network you don't trust. That is a documented choice; the banner warns about it.
- Exposing LocalFlix to the internet through port forwarding. It isn't designed for that. Use a VPN such as Tailscale or WireGuard instead.

## Hardening tips for users

- Set a **password** and an **admin PIN** under Settings → Network & Security.
- Leave *Only allow private-network clients* on (the default).
- Use [free HTTPS](README.md#-fixed-address--free-https) if other people share your Wi-Fi.
- Keep LocalFlix updated.
