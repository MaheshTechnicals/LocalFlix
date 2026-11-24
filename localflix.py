#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""LocalFlix — a zero-setup personal media server with a premium streaming UI.

Copy this single file anywhere, drop videos into ``./output`` and run::

    python localflix.py

Every device on the same network can then open ``http://<lan-ip>:8000``.
Only the Python standard library is required.  ``ffmpeg``/``ffprobe`` unlock
transcoding, multi-audio, subtitles extraction and thumbnails.  Optional extras
(``segno`` for QR codes, ``zeroconf`` for mDNS) are feature-detected.
"""
from __future__ import annotations

# ===== SECTION: imports =====
import argparse
import base64
import collections
import contextlib
import email.utils
import fnmatch
import gzip
import hashlib
import hmac
import html
import http.client
import http.cookies
import http.server
import ipaddress
import itertools
import json
import logging
import logging.handlers
import math
import os
import platform
import queue
import random
import re
import secrets
import shutil
import signal
import socket
import sqlite3
import ssl
import struct
import subprocess
import sys
import tempfile
import textwrap
import threading
import time
import traceback
import unicodedata
import unittest
import urllib.error
import urllib.parse
import urllib.request
import uuid
import webbrowser
from collections.abc import Callable, Iterable, Iterator
from pathlib import Path
from typing import Any, ClassVar

try:  # optional: QR codes
    import segno  # type: ignore
except ImportError:  # pragma: no cover - optional
    segno = None  # type: ignore
try:  # optional: mDNS advertisement
    import zeroconf  # type: ignore
except ImportError:  # pragma: no cover - optional
    zeroconf = None  # type: ignore

if sys.version_info < (3, 10):  # noqa: UP036  # pragma: no cover
    sys.stderr.write("LocalFlix needs Python 3.10 or newer.\n")
    raise SystemExit(1)

# ===== SECTION: constants =====
APP_NAME = "LocalFlix"
VERSION = "1.2.5"
SCHEMA_VERSION = 4
HLSJS_VERSION = "1.7.4"  # resolved from the npm registry on 2026-11-21
HLSJS_SHA384 = "p9vgU/7IYJWd3I4STrtqxqR/2cAQlhIX7D/HrTnIMEjjcUox165b7se7sxmm3zDg"
HLSJS_URLS = [
    f"https://cdn.jsdelivr.net/npm/hls.js@{HLSJS_VERSION}/dist/hls.min.js",
    f"https://unpkg.com/hls.js@{HLSJS_VERSION}/dist/hls.min.js",
]
SCRIPT_DIR = Path(os.path.abspath(sys.argv[0] if sys.argv and sys.argv[0].endswith(".py") else __file__)).parent
START_TIME = time.time()

VIDEO_EXTS = {
    ".mp4", ".m4v", ".mkv", ".webm", ".mov", ".avi", ".wmv", ".flv", ".ts", ".m2ts", ".mts",
    ".mpg", ".mpeg", ".ogv", ".3gp", ".divx", ".vob",
}
SUB_EXTS = {".srt", ".vtt", ".ass", ".ssa", ".sub", ".ttml"}
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp"}
SKIP_DIRS = {"@eadir", "$recycle.bin", "lost+found", "system volume information", "#recycle", ".trash"}

# Hard-coded MIME map: never trust the Windows registry (§4.2.1).
MIME_MAP = {
    ".html": "text/html; charset=utf-8", ".htm": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8", ".mjs": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8", ".json": "application/json; charset=utf-8",
    ".m3u8": "application/vnd.apple.mpegurl", ".m4s": "video/iso.segment",
    ".mp4": "video/mp4", ".m4v": "video/mp4", ".mov": "video/quicktime", ".webm": "video/webm",
    ".mkv": "video/x-matroska", ".ts": "video/mp2t", ".m2ts": "video/mp2t", ".mts": "video/mp2t",
    ".avi": "video/x-msvideo", ".wmv": "video/x-ms-wmv", ".flv": "video/x-flv",
    ".mpg": "video/mpeg", ".mpeg": "video/mpeg", ".ogv": "video/ogg", ".3gp": "video/3gpp",
    ".vob": "video/mpeg", ".divx": "video/x-msvideo",
    ".vtt": "text/vtt; charset=utf-8", ".srt": "text/plain; charset=utf-8",
    ".svg": "image/svg+xml", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
    ".webp": "image/webp", ".gif": "image/gif", ".ico": "image/x-icon",
    ".woff2": "font/woff2", ".woff": "font/woff", ".txt": "text/plain; charset=utf-8",
}

# ISO 639-1/2/3 → display name (≥ 40 languages incl. Indian languages).
LANGS: dict[str, str] = {}
for _codes, _name in [
    (("en", "eng"), "English"), (("hi", "hin"), "Hindi"), (("ta", "tam"), "Tamil"),
    (("te", "tel"), "Telugu"), (("mr", "mar"), "Marathi"), (("bn", "ben"), "Bengali"),
    (("ur", "urd"), "Urdu"), (("pa", "pan"), "Punjabi"), (("gu", "guj"), "Gujarati"),
    (("ml", "mal"), "Malayalam"), (("kn", "kan"), "Kannada"), (("or", "ori"), "Odia"),
    (("as", "asm"), "Assamese"), (("ne", "nep"), "Nepali"), (("si", "sin"), "Sinhala"),
    (("es", "spa"), "Spanish"), (("fr", "fra", "fre"), "French"), (("de", "deu", "ger"), "German"),
    (("it", "ita"), "Italian"), (("pt", "por"), "Portuguese"), (("ru", "rus"), "Russian"),
    (("ja", "jpn"), "Japanese"), (("ko", "kor"), "Korean"), (("zh", "zho", "chi"), "Chinese"),
    (("ar", "ara"), "Arabic"), (("tr", "tur"), "Turkish"), (("nl", "nld", "dut"), "Dutch"),
    (("sv", "swe"), "Swedish"), (("no", "nor", "nob"), "Norwegian"), (("da", "dan"), "Danish"),
    (("fi", "fin"), "Finnish"), (("pl", "pol"), "Polish"), (("cs", "ces", "cze"), "Czech"),
    (("el", "ell", "gre"), "Greek"), (("he", "heb"), "Hebrew"), (("th", "tha"), "Thai"),
    (("vi", "vie"), "Vietnamese"), (("id", "ind"), "Indonesian"), (("ms", "msa", "may"), "Malay"),
    (("fa", "fas", "per"), "Persian"), (("uk", "ukr"), "Ukrainian"), (("hu", "hun"), "Hungarian"),
    (("ro", "ron", "rum"), "Romanian"), (("bg", "bul"), "Bulgarian"), (("hr", "hrv"), "Croatian"),
    (("sr", "srp"), "Serbian"), (("tl", "tgl", "fil"), "Filipino"), (("sw", "swa"), "Swahili"),
    (("und", "unk", "zxx"), "Unknown"),
]:
    for _c in _codes:
        LANGS[_c] = _name


def lang_name(code: str | None) -> str:
    """Return a display name for an ISO 639 code (falls back to the code)."""
    if not code:
        return ""
    c = code.lower().split("-")[0].split("_")[0]
    return LANGS.get(c, code)


def lang_norm(code: str | None) -> str:
    """Normalise a language code to ISO 639-2 (three letters) when known."""
    if not code:
        return ""
    c = code.lower().split("-")[0].split("_")[0]
    name = LANGS.get(c)
    if not name:
        # Allow full names like "english"
        for k, v in LANGS.items():
            if v.lower() == c and len(k) == 3:
                return k
        return c
    for k, v in LANGS.items():
        if v == name and len(k) == 3:
            return k
    return c


# ===== SECTION: platform =====
class PlatformInfo:
    """Detected runtime platform.  All OS-specific branching goes through here."""

    def __init__(self) -> None:
        self.system = sys.platform
        self.machine = platform.machine() or "unknown"
        self.is_windows = sys.platform.startswith("win")
        self.is_mac = sys.platform == "darwin"
        prefix = os.environ.get("PREFIX", "")
        self.is_termux = "com.termux" in prefix or os.path.isdir("/data/data/com.termux/files/usr") and not self.is_windows
        self.is_linux = sys.platform.startswith("linux") and not self.is_termux
        self.is_wsl = False
        self.is_docker = os.path.exists("/.dockerenv")
        if sys.platform.startswith("linux"):
            try:
                self.is_wsl = "microsoft" in Path("/proc/version").read_text(errors="ignore").lower()
            except OSError:
                pass
        self.is_arm = self.machine.lower() in ("arm64", "aarch64", "armv7l", "armv6l", "arm")

    @property
    def name(self) -> str:
        if self.is_termux:
            return "termux"
        if self.is_windows:
            return "windows"
        if self.is_mac:
            return "macos"
        return "linux"

    def describe(self) -> str:
        parts = [{"termux": "Termux (Android)", "windows": "Windows", "macos": "macOS", "linux": "Linux"}[self.name]]
        if self.is_wsl:
            parts.append("WSL")
        if self.is_docker:
            parts.append("Docker")
        parts.append(self.machine)
        try:
            if self.is_mac:
                parts.append(platform.mac_ver()[0])
            elif self.is_windows:
                parts.append(platform.version())
            else:
                parts.append(platform.release())
        except Exception:  # pragma: no cover  # noqa: BLE001, S110
            pass
        return " · ".join(p for p in parts if p)


PLAT = PlatformInfo()


class Paths:
    """Per-OS config / cache / log directories (§4.1)."""

    def __init__(self, home: str | None = None) -> None:
        home = home or os.environ.get("LOCALFLIX_HOME")
        if home:
            base = Path(home).expanduser().resolve()
            self.config = base
            self.cache = base / "cache"
        elif PLAT.is_windows:
            self.config = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming")) / APP_NAME
            self.cache = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local")) / APP_NAME / "Cache"
        elif PLAT.is_mac:
            self.config = Path.home() / "Library" / "Application Support" / APP_NAME
            self.cache = Path.home() / "Library" / "Caches" / APP_NAME
        elif PLAT.is_termux:
            self.config = Path.home() / ".config" / "localflix"
            self.cache = Path.home() / ".cache" / "localflix"
        else:
            self.config = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "localflix"
            self.cache = Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache") / "localflix"
        self.logs = self.config / "logs"
        self.db = self.config / "localflix.db"
        self.bin = self.config / "bin"
        self.vendor = self.cache / "vendor"
        self.hls = self.cache / "hls"
        self.images = self.cache / "images"
        self.subs = self.cache / "subs"
        self.trickplay = self.cache / "trickplay"
        self.tmdb = self.cache / "tmdb"
        self.keyframes = self.cache / "keyframes"
        self.tmp = self.cache / "tmp"

    def ensure(self) -> None:
        for p in (self.config, self.cache, self.logs, self.vendor, self.hls, self.images, self.subs,
                  self.trickplay, self.tmdb, self.keyframes, self.tmp):
            p.mkdir(parents=True, exist_ok=True)


def default_media_dir() -> Path:
    env = os.environ.get("LOCALFLIX_MEDIA")
    if env:
        return Path(env).expanduser()
    return SCRIPT_DIR / "output"


def long_path(p: Path) -> str:
    """Return a path string usable on Windows even beyond MAX_PATH (``\\\\?\\`` prefix)."""
    s = str(p)
    if PLAT.is_windows and len(s) > 240 and not s.startswith("\\\\?\\"):
        s = "\\\\?\\" + os.path.abspath(s)
    return s


def safe_join(root: Path, rel: str) -> Path | None:
    """Resolve ``rel`` inside ``root``; return None if it escapes (incl. via symlinks)."""
    try:
        root_r = Path(root).resolve()
        rel = rel.replace("\\", "/").lstrip("/")
        if any(part == ".." for part in rel.split("/")):
            return None
        cand = (root_r / rel).resolve()
        if cand == root_r or cand.is_relative_to(root_r):
            return cand
    except (OSError, ValueError, RuntimeError):
        return None
    return None


def setup_console() -> None:
    """UTF-8 console + ANSI colours on Windows 10+."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001, S110
            pass
    if PLAT.is_windows:
        try:
            import ctypes

            k32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
            h = k32.GetStdHandle(-11)
            mode = ctypes.c_uint32()
            if k32.GetConsoleMode(h, ctypes.byref(mode)):
                k32.SetConsoleMode(h, mode.value | 0x0004)
        except Exception:  # noqa: BLE001
            os.system("")


def _use_color() -> bool:
    return hasattr(sys.stdout, "isatty") and sys.stdout.isatty() and not os.environ.get("NO_COLOR")


def c(text: str, color: str) -> str:
    """Colourise terminal text when stdout is a TTY."""
    if not _use_color():
        return text
    codes = {"red": "31", "green": "32", "yellow": "33", "blue": "34", "magenta": "35", "cyan": "36",
             "bold": "1", "dim": "2"}
    return f"\033[{codes.get(color, '0')}m{text}\033[0m"


def popen_kwargs(low_priority: bool = True) -> dict[str, Any]:
    """Subprocess kwargs that isolate children so they can be killed as a tree (§4.2.2)."""
    kw: dict[str, Any] = {}
    if PLAT.is_windows:
        flags = 0x08000000 | 0x00000200  # CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP
        if low_priority:
            flags |= 0x00004000  # BELOW_NORMAL_PRIORITY_CLASS
        kw["creationflags"] = flags
    else:
        kw["start_new_session"] = True
        if low_priority and not PLAT.is_termux:
            def _nice() -> None:
                try:
                    os.nice(10)
                except OSError:
                    pass
            kw["preexec_fn"] = _nice
    return kw


def kill_tree(proc: subprocess.Popen | None, hard: bool = False) -> None:
    """Kill a child process and all its descendants, ignoring errors.

    ``hard`` sends SIGKILL right away: used for HLS encoders, because on SIGTERM ffmpeg finalises (and renames)
    the half-written segment, which would then be served as if it were complete.
    """
    if proc is None or proc.poll() is not None:
        return
    try:
        if PLAT.is_windows:
            subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], capture_output=True,
                           creationflags=0x08000000, timeout=10, check=False)
        else:
            try:
                os.killpg(proc.pid, signal.SIGKILL if hard else signal.SIGTERM)
            except (ProcessLookupError, PermissionError):
                proc.terminate()
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                with contextlib.suppress(Exception):
                    os.killpg(proc.pid, signal.SIGKILL)
    except Exception:  # noqa: BLE001
        with contextlib.suppress(Exception):
            proc.kill()
    with contextlib.suppress(Exception):
        proc.wait(timeout=3)


class ChildRegistry:
    """Tracks every spawned ffmpeg so shutdown never leaves orphans."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._procs: set[subprocess.Popen] = set()

    def add(self, p: subprocess.Popen) -> None:
        with self._lock:
            self._procs.add(p)

    def remove(self, p: subprocess.Popen) -> None:
        with self._lock:
            self._procs.discard(p)

    def kill_all(self) -> None:
        with self._lock:
            procs = list(self._procs)
            self._procs.clear()
        for p in procs:
            kill_tree(p)


CHILDREN = ChildRegistry()


def run_capture(args: list[str], timeout: float = 60, low_priority: bool = False,
                input_bytes: bytes | None = None) -> tuple[int, bytes, bytes]:
    """Run a command (list form, never a shell) and capture output; killable on shutdown."""
    try:
        p = subprocess.Popen(args, stdin=subprocess.PIPE if input_bytes is not None else subprocess.DEVNULL,
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, **popen_kwargs(low_priority))
    except (OSError, ValueError) as e:
        return 127, b"", str(e).encode()
    CHILDREN.add(p)
    try:
        out, err = p.communicate(input=input_bytes, timeout=timeout)
        return p.returncode, out, err
    except subprocess.TimeoutExpired:
        kill_tree(p)
        return -9, b"", b"timeout"
    finally:
        CHILDREN.remove(p)


def is_private_ip(ip: str) -> bool:
    try:
        a = ipaddress.ip_address(ip.split("%")[0])
        if isinstance(a, ipaddress.IPv6Address) and a.ipv4_mapped:
            a = a.ipv4_mapped
        return a.is_private or a.is_loopback or a.is_link_local
    except ValueError:
        return False


def is_loopback(ip: str) -> bool:
    try:
        a = ipaddress.ip_address(ip.split("%")[0])
        if isinstance(a, ipaddress.IPv6Address) and a.ipv4_mapped:
            a = a.ipv4_mapped
        return a.is_loopback
    except ValueError:
        return False


_VIRTUAL_IF = re.compile(r"^(docker|br-|virbr|veth|vmnet|vboxnet|cni|flannel|podman|lxc|lxd|tun|tap|utun|zt|tailscale|wg)", re.IGNORECASE)


def lan_ips() -> list[str]:
    """All private IPv4 addresses of this host, best candidate first (§4.2.7).

    Order: the default-route interface, then 192.168.x / 10.x / 172.16–31.x; virtual
    bridges (Docker, libvirt, VPN tunnels) go last.
    """
    ips: list[str] = []
    virtual: set[str] = set()
    primary = ""

    def add(ip: str) -> None:
        if ip and ip not in ips and not ip.startswith("127.") and is_private_ip(ip):
            ips.append(ip)

    for target in ("8.8.8.8", "192.168.0.1", "10.255.255.255"):
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.settimeout(0.2)
            s.connect((target, 80))
            ip = s.getsockname()[0]
            s.close()
            if not primary and is_private_ip(ip) and not ip.startswith("127."):
                primary = ip
            add(ip)
        except OSError:
            pass
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            add(info[4][0])
    except OSError:
        pass
    if not PLAT.is_windows:
        if shutil.which("ip"):
            _, out, _ = run_capture(["ip", "-4", "-o", "addr"], timeout=3)
            for m in re.finditer(r"^\d+:\s+(\S+)\s+inet\s+(\d+\.\d+\.\d+\.\d+)", out.decode(errors="ignore"), re.MULTILINE):
                add(m.group(2))
                if _VIRTUAL_IF.match(m.group(1)):
                    virtual.add(m.group(2))
        elif shutil.which("ifconfig"):
            _, out, _ = run_capture(["ifconfig"], timeout=3)
            iface = ""
            for line in out.decode(errors="ignore").splitlines():
                mi = re.match(r"^(\S+?):?\s", line)
                if mi and not line.startswith((" ", "\t")):
                    iface = mi.group(1)
                m = re.search(r"inet (?:addr:)?(\d+\.\d+\.\d+\.\d+)", line)
                if m:
                    add(m.group(1))
                    if _VIRTUAL_IF.match(iface):
                        virtual.add(m.group(1))

    def score(ip: str) -> tuple[int, int]:
        if ip in virtual:
            return (2, 0)
        if ip == primary:
            return (0, 0)
        if ip.startswith("192.168."):
            return (1, 0)
        if ip.startswith("10."):
            return (1, 1)
        if re.match(r"172\.(1[6-9]|2\d|3[01])\.", ip):
            return (1, 2)
        return (1, 3)
    ips.sort(key=score)
    return ips


class SleepInhibitor:
    """Prevent system sleep while streams are active (§4.2.6)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._count = 0
        self._proc: subprocess.Popen | None = None
        self._win_stop: threading.Event | None = None
        self.enabled = True

    def acquire(self) -> None:
        with self._lock:
            self._count += 1
            if self._count == 1 and self.enabled:
                self._start()

    def release(self) -> None:
        with self._lock:
            self._count = max(0, self._count - 1)
            if self._count == 0:
                self._stop()

    def _start(self) -> None:
        try:
            if PLAT.is_windows:
                # SetThreadExecutionState is per-thread: hold it on a dedicated thread for as long as needed.
                stop = self._win_stop = threading.Event()

                def hold() -> None:
                    import ctypes
                    k32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
                    k32.SetThreadExecutionState(0x80000000 | 0x00000001)
                    stop.wait()
                    k32.SetThreadExecutionState(0x80000000)
                threading.Thread(target=hold, name="no-sleep", daemon=True).start()
            elif PLAT.is_termux:
                pass  # the server holds termux-wake-lock for its whole lifetime (cmd_serve)
            elif PLAT.is_mac and shutil.which("caffeinate"):
                self._proc = subprocess.Popen(["caffeinate", "-i", "-w", str(os.getpid())], **popen_kwargs(False))
            elif shutil.which("systemd-inhibit"):
                self._proc = subprocess.Popen(
                    ["systemd-inhibit", "--what=sleep", "--who=LocalFlix", "--why=Streaming media",
                     "--mode=block", "sleep", "infinity"],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **popen_kwargs(False))
        except Exception as e:  # pragma: no cover  # noqa: BLE001
            log.debug("sleep inhibit failed: %s", e)

    def _stop(self) -> None:
        try:
            if self._win_stop is not None:
                self._win_stop.set()
                self._win_stop = None
            if self._proc:
                kill_tree(self._proc)
                self._proc = None
        except Exception:  # pragma: no cover  # noqa: BLE001, S110
            pass


# ===== SECTION: media-discovery =====
# Folder names skipped inside *auto-discovered* libraries (case-insensitive). User-added folders are only
# subject to SKIP_DIRS / hidden-file rules, because the user chose them explicitly.
AUTO_EXCLUDE_NAMES = {
    # Windows system / app data
    "windows", "program files", "program files (x86)", "programdata", "appdata", "$recycle.bin", "$windows.~bt",
    "$windows.~ws", "$winreagent", "system volume information", "recovery", "perflogs", "msocache", "intel", "amd",
    "nvidia", "drivers", "onedrivetemp", "windowsapps", "packages", "msys64", "cygwin64",
    # macOS
    "library", "applications", "system", "private", "cores", ".trash", ".trashes", ".spotlight-v100", ".fseventsd",
    ".documentrevisions-v100", ".temporaryitems",
    # Linux / dev / app caches
    "proc", "sys", "dev", "run", "boot", "etc", "usr", "bin", "sbin", "lib", "lib64", "var", "tmp", "snap",
    "lost+found", "node_modules", "site-packages", "dist-packages", "__pycache__", ".git", ".svn", ".cache",
    ".local", ".var", ".npm", ".cargo", ".rustup", ".gradle", ".m2", ".android", ".wine", ".steam", "steamapps",
    "steamlibrary", "anaconda3", "miniconda3", "venv", ".venv", "go", "flatpak",
    # Android shared storage
    "android", ".thumbnails", ".statuses",
}
AUTO_EXCLUDE_GLOBS = ("*.app", "*.photoslibrary", "*.bundle", "*.framework", "*.lrlibrary", "*.fcpbundle",
                      "*.imovielibrary", "*.tvlibrary", "*.photolibrary", "*.aplibrary")


def _user_dirs_xdg(key: str) -> Path | None:
    """Resolve an XDG user directory (e.g. XDG_VIDEOS_DIR) from ~/.config/user-dirs.dirs."""
    cfg = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "user-dirs.dirs"
    try:
        for line in cfg.read_text(encoding="utf-8", errors="ignore").splitlines():
            m = re.match(rf'\s*{key}\s*=\s*"(.*)"', line)
            if m:
                return Path(m.group(1).replace("$HOME", str(Path.home())))
    except OSError:
        pass
    return None


def _windows_known_folder(guid: str) -> Path | None:
    """SHGetKnownFolderPath (handles redirected / OneDrive-backed Videos & Downloads)."""
    try:
        import ctypes
        from ctypes import wintypes

        class GUID(ctypes.Structure):
            _fields_ = [("d1", wintypes.DWORD), ("d2", wintypes.WORD), ("d3", wintypes.WORD), ("d4", ctypes.c_ubyte * 8)]
        g = uuid.UUID(guid)
        gg = GUID(g.fields[0], g.fields[1], g.fields[2], (ctypes.c_ubyte * 8)(*g.bytes[8:]))
        out = ctypes.c_wchar_p()
        if ctypes.windll.shell32.SHGetKnownFolderPath(ctypes.byref(gg), 0, None, ctypes.byref(out)) == 0:  # type: ignore[attr-defined]
            val = out.value
            ctypes.windll.ole32.CoTaskMemFree(out)  # type: ignore[attr-defined]
            return Path(val) if val else None
    except Exception:  # noqa: BLE001
        return None
    return None


def _windows_drives() -> list[Path]:
    """Fixed and removable drive roots (network, CD and RAM drives are skipped)."""
    out: list[Path] = []
    try:
        import ctypes
        k32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        mask = k32.GetLogicalDrives()
        for i in range(26):
            if mask & (1 << i):
                root = f"{chr(65 + i)}:\\"
                if k32.GetDriveTypeW(ctypes.c_wchar_p(root)) in (2, 3):  # DRIVE_REMOVABLE, DRIVE_FIXED
                    out.append(Path(root))
    except Exception:  # noqa: BLE001
        import string
        out = [Path(f"{d}:\\") for d in string.ascii_uppercase if os.path.exists(f"{d}:\\")]
    return out


def _children(d: Path) -> list[Path]:
    try:
        return sorted(Path(e.path) for e in os.scandir(d) if e.is_dir() and not e.name.startswith("."))
    except OSError:
        return []


def _mounted_drives() -> list[tuple[Path, str]]:
    """External / secondary drives currently mounted (USB disks, SD cards, NAS mounts under /mnt)."""
    found: list[tuple[Path, str]] = []
    user = os.environ.get("USER") or os.environ.get("LOGNAME") or ""
    if PLAT.is_windows:
        sysdrive = (os.environ.get("SystemDrive") or "C:").upper()
        for d in _windows_drives():
            if not str(d).upper().startswith(sysdrive):
                found.append((d, f"Drive {str(d)[:2]}"))
    elif PLAT.is_mac:
        for d in _children(Path("/Volumes")):
            with contextlib.suppress(OSError):
                if d.is_symlink() or d.resolve() == Path("/"):
                    continue  # the boot volume ("Macintosh HD" → /)
                found.append((d, d.name))
    elif PLAT.is_termux:
        for d in _children(Path.home() / "storage"):
            if d.name.startswith("external"):
                found.append((d, "SD card"))
        for d in _children(Path("/storage")):
            if d.name not in ("emulated", "self") and re.fullmatch(r"[0-9A-F]{4}-[0-9A-F]{4}", d.name, re.IGNORECASE):
                found.append((d, f"SD card {d.name}"))
    else:
        bases = [Path("/media") / user, Path("/run/media") / user] if user else []
        for base in bases:
            for d in _children(base):
                found.append((d, d.name))
        for base in (Path("/media"), Path("/mnt")):
            for d in _children(base):
                if base == Path("/media") and d.name == user:
                    continue
                if PLAT.is_wsl and base == Path("/mnt") and re.fullmatch(r"[a-z]", d.name):
                    continue  # Windows drives under WSL are handled separately
                if d.name in ("wsl", "wslg") or (PLAT.is_docker and d.name in ("", )):
                    continue
                found.append((d, d.name))
    return found


def discover_media_roots(scope: str) -> list[dict[str, str]]:
    """Folders LocalFlix should index automatically for a library scope.

    ``folders``: nothing (only user-added folders) · ``common``: the platform's standard video folders plus
    external drives · ``all``: ``common`` plus the whole home folder and every drive (system folders are
    excluded while walking). Every returned path exists and is readable; nested duplicates are kept on purpose
    (the deepest library owns a file, which keeps item ids stable when the scope changes).
    """
    if scope not in ("common", "all"):
        return []
    home = Path.home()
    cands: list[tuple[Path, str, str]] = []  # (path, key, label)

    def add(p: Path | None, key: str, label: str) -> None:
        if p is not None:
            cands.append((p, key, label))

    if PLAT.is_windows:
        add(_windows_known_folder("18989B1D-99B5-455B-841C-AB7C74E4DDFC") or home / "Videos", "videos", "Videos")
        add(_windows_known_folder("374DE290-123F-4565-9164-39C4925E467B") or home / "Downloads", "downloads", "Downloads")
        add(Path(os.environ.get("PUBLIC") or r"C:\Users\Public") / "Videos", "public-videos", "Public Videos")
        od = os.environ.get("OneDrive")
        if od:
            add(Path(od) / "Videos", "onedrive-videos", "OneDrive Videos")
    elif PLAT.is_mac:
        add(home / "Movies", "movies", "Movies")
        add(home / "Downloads", "downloads", "Downloads")
    elif PLAT.is_termux:
        shared = home / "storage" / "shared"
        if not shared.exists() and os.access("/sdcard", os.R_OK):
            shared = Path("/sdcard")
        for sub, label in (("Movies", "Movies"), ("Download", "Downloads"), ("DCIM", "Camera"),
                           ("Videos", "Videos"), ("Telegram", "Telegram"),
                           ("Android/media/com.whatsapp/WhatsApp/Media/WhatsApp Video", "WhatsApp Video"),
                           ("WhatsApp/Media/WhatsApp Video", "WhatsApp Video")):
            add(shared / sub, "android-" + sub.lower().split("/")[-1].replace(" ", "-"), label)
    else:
        if PLAT.is_docker:
            add(Path("/media"), "docker-media", "Media")
        add(_user_dirs_xdg("XDG_VIDEOS_DIR") or home / "Videos", "videos", "Videos")
        add(_user_dirs_xdg("XDG_DOWNLOAD_DIR") or home / "Downloads", "downloads", "Downloads")
        if PLAT.is_wsl:
            for u in _children(Path("/mnt/c/Users")):
                if u.name.lower() in ("public", "default", "default user", "all users"):
                    continue
                add(u / "Videos", f"win-videos-{u.name.lower()}", f"Windows Videos ({u.name})")
                add(u / "Downloads", f"win-downloads-{u.name.lower()}", f"Windows Downloads ({u.name})")
    for d, label in _mounted_drives():
        add(d, "drive:" + str(d), label)
    if scope == "all":
        if PLAT.is_windows:
            for d in _windows_drives():
                add(d, "drive:" + str(d), f"Drive {str(d)[:2]}")
        elif PLAT.is_termux:
            shared = home / "storage" / "shared"
            add(shared if shared.exists() else Path("/sdcard"), "android-shared", "Internal storage")
        else:
            add(home, "home", "Home folder")
            if PLAT.is_wsl:
                for d in _children(Path("/mnt")):
                    if re.fullmatch(r"[a-z]", d.name):
                        add(d, f"drive:{d}", f"Windows drive {d.name.upper()}:")
            for extra in (Path("/srv"), Path("/data"), Path("/home") if PLAT.is_linux else None):
                if extra is not None and extra != home and not home.is_relative_to(extra):
                    add(extra, f"dir:{extra}", str(extra))
    out: list[dict[str, str]] = []
    seen: set[str] = set()
    for p, key, label in cands:
        try:
            rp = p.resolve()
        except OSError:
            continue
        norm = os.path.normcase(str(rp))
        if norm in seen or not rp.is_dir() or not os.access(rp, os.R_OK | os.X_OK):
            continue
        seen.add(norm)
        out.append({"path": str(rp), "key": key, "label": label})
    return out


def looks_like_mpegts(path: str) -> bool:
    """``.ts`` is also TypeScript: accept only real MPEG transport streams (sync byte 0x47 every 188/192 bytes)."""
    try:
        with open(path, "rb") as f:
            head = f.read(192 * 4)
    except OSError:
        return False
    for pkt, off in ((188, 0), (192, 4)):
        if len(head) >= off + pkt * 3 and all(head[off + i * pkt] == 0x47 for i in range(3)):
            return True
    return False


def auto_excluded(name: str) -> bool:
    low = name.lower()
    return low in AUTO_EXCLUDE_NAMES or any(fnmatch.fnmatch(low, g) for g in AUTO_EXCLUDE_GLOBS)


# ===== SECTION: logging =====
log = logging.getLogger("localflix")


class RedactFilter(logging.Filter):
    """Strip tokens, PINs and passwords from log records."""

    _re = re.compile(r"(token|pin|password|key|lf_session)=([^&\s\"']+)", re.IGNORECASE)

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            msg = record.getMessage()
            red = self._re.sub(r"\1=***", msg)
            if red != msg:
                record.msg, record.args = red, ()
        except Exception:  # noqa: BLE001, S110
            pass
        return True


class RingHandler(logging.Handler):
    """Keeps the last N log lines in memory for the UI log viewer."""

    def __init__(self, n: int = 500) -> None:
        super().__init__()
        self.lines: collections.deque[str] = collections.deque(maxlen=n)

    def emit(self, record: logging.LogRecord) -> None:
        with contextlib.suppress(Exception):
            self.lines.append(self.format(record))


RING = RingHandler()


def setup_logging(paths: Paths, level: str = "INFO", console: bool = True) -> None:
    log.setLevel(logging.DEBUG)
    for h in list(log.handlers):
        log.removeHandler(h)
    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(threadName)s: %(message)s", "%Y-%m-%d %H:%M:%S")
    lvl = getattr(logging, level.upper(), logging.INFO)
    redact = RedactFilter()
    try:
        fh = logging.handlers.RotatingFileHandler(paths.logs / "localflix.log", maxBytes=1_000_000,
                                                  backupCount=5, encoding="utf-8")
        fh.setFormatter(fmt)
        fh.setLevel(lvl)
        fh.addFilter(redact)
        log.addHandler(fh)
    except OSError:
        pass
    RING.setFormatter(fmt)
    RING.setLevel(lvl)
    RING.addFilter(redact)
    log.addHandler(RING)
    if console:
        ch = logging.StreamHandler(sys.stderr)
        ch.setFormatter(logging.Formatter("%(levelname)s: %(message)s"))
        ch.setLevel(max(lvl, logging.WARNING))
        ch.addFilter(redact)
        log.addHandler(ch)


# ===== SECTION: settings =====
QUALITY_PRESETS: dict[str, tuple[int | None, int | None]] = {
    # key: (max height, max video kbps)
    "auto": (None, None), "original": (None, None), "2160p": (2160, 20000), "1080p": (1080, 8000),
    "720p": (720, 4000), "480p": (480, 1500), "360p": (360, 800),
}

ACCENTS = ["#E5484D", "#F76B15", "#FFC53D", "#30A46C", "#12A594", "#3E63DD", "#8E4EC6", "#D6409F"]


def S(key: str, default: Any, typ: str, cat: str, scope: str = "server", **kw: Any) -> dict[str, Any]:
    d = {"key": key, "default": default, "type": typ, "category": cat, "scope": scope}
    d.update(kw)
    return d


SETTINGS_SPEC: list[dict[str, Any]] = [
    # General
    S("server_name", "LocalFlix", "str", "general", help="Name shown in the UI and on the network", maxlen=40),
    S("language", "en", "enum", "general", "user", choices=["en", "hi"], help="Interface language"),
    S("theme", "dark", "enum", "general", "user", choices=["dark", "oled", "light", "auto"], help="Colour theme"),
    S("accent", "#E5484D", "color", "general", "user", presets=ACCENTS, help="Accent colour"),
    S("card_size", "m", "enum", "general", "user", choices=["s", "m", "l"], help="Size of title cards"),
    S("poster_cards", False, "bool", "general", "user", help="Use 2:3 posters when available"),
    S("reduce_motion", False, "bool", "general", "user", help="Disable animations"),
    S("ui_scale", 100, "int", "general", "user", min=100, max=150, step=5, help="Interface zoom in %"),
    S("tv_mode", "auto", "enum", "general", "user", choices=["auto", "on", "off"], help="10-foot TV interface"),
    S("show_tv_button", True, "bool", "general", "user", help="Show the 'Open on TV' button"),
    # Libraries
    S("scan_interval", 60, "int", "libraries", min=0, max=3600, help="Seconds between automatic scans (0 = manual)"),
    S("ignore_patterns", ["sample.*", "*-sample.*", "*.sample.*", "*.part", "*.!qb", "*.crdownload"], "list", "libraries",
      help="Glob patterns to ignore (case-insensitive)"),
    S("include_hidden", False, "bool", "libraries", help="Index hidden files and folders"),
    S("purge_days", 7, "int", "libraries", min=0, max=365, help="Days before missing files are purged"),
    S("library_scope", "folders", "enum", "libraries", admin=True, choices=["folders", "common", "all"],
      help="Which videos to index: only your folders, the usual video folders + USB drives, or the whole computer"),
    S("auto_scan_interval", 1800, "int", "libraries", admin=True, min=0, max=86400,
      help="Seconds between scans of auto-detected folders (0 = manual). They can be very large"),
    S("auto_min_size_mb", 2, "int", "libraries", admin=True, min=0, max=1000,
      help="Skip smaller files in auto-detected folders (app sounds, ads, thumbnails)"),
    # Playback
    S("audio_lang", "eng", "str", "playback", "user", help="Preferred audio language (ISO code)", maxlen=12),
    S("audio_lang_fallback", ["hin", "eng"], "list", "playback", "user", help="Fallback audio languages"),
    S("subtitle_mode", "auto", "enum", "playback", "user", choices=["off", "auto", "always"],
      help="Auto: show subtitles when the audio is not in your language"),
    S("subtitle_lang", "eng", "str", "playback", "user", help="Preferred subtitle language", maxlen=12),
    S("autoplay_next", True, "bool", "playback", "user", help="Play the next episode automatically"),
    S("countdown_seconds", 10, "int", "playback", "user", min=3, max=60, help="Next-episode countdown"),
    S("skip_buttons", True, "bool", "playback", "user", help="Show Skip Intro / Credits buttons"),
    S("credits_fallback", True, "bool", "playback", "user", help="Offer next episode in the last 45 s"),
    S("resume_mode", "ask", "enum", "playback", "user", choices=["ask", "auto", "never"], help="Resume behaviour"),
    S("default_speed", 1.0, "float", "playback", "user", min=0.25, max=2.0, step=0.25, help="Default speed"),
    S("remember_volume", True, "bool", "playback", "user", help="Remember volume between videos"),
    S("max_quality", "auto", "enum", "playback", "user", choices=list(QUALITY_PRESETS), help="Maximum streaming quality"),
    S("prefer_direct", True, "bool", "playback", "user", help="Prefer direct play when possible"),
    S("allow_transcode", True, "bool", "playback", help="Allow server transcoding"),
    S("allow_passthrough", False, "bool", "playback", help="Pass through AC3/EAC3 when the client supports it"),
    S("downmix_stereo", True, "bool", "playback", help="Downmix surround to stereo when transcoding audio"),
    S("trickplay", True, "bool", "playback", help="Generate seek preview thumbnails"),
    S("stats_overlay", False, "bool", "playback", "user", help="Show play-method stats overlay"),
    S("auto_fullscreen", False, "bool", "playback", "user", help="Go fullscreen on play (desktop)"),
    S("sleep_timer", 0, "int", "playback", "user", min=0, max=180, help="Default sleep timer minutes (0 = off)"),
    # Subtitles appearance
    S("sub_size", 100, "int", "subtitles", "user", min=50, max=250, step=10, help="Subtitle size %"),
    S("sub_font", "sans", "enum", "subtitles", "user", choices=["sans", "serif", "mono", "rounded"], help="Font"),
    S("sub_color", "#FFFFFF", "color", "subtitles", "user", presets=["#FFFFFF", "#FFE066", "#7CF29C", "#7FDBFF"],
      help="Text colour"),
    S("sub_bg_opacity", 40, "int", "subtitles", "user", min=0, max=100, step=5, help="Background opacity %"),
    S("sub_edge", "shadow", "enum", "subtitles", "user", choices=["none", "shadow", "outline", "raised"],
      help="Edge style"),
    S("sub_position", 8, "int", "subtitles", "user", min=0, max=40, help="Distance from bottom (%)"),
    S("sub_delay_ms", 0, "int", "subtitles", "user", min=-10000, max=10000, step=250, help="Default delay (ms)"),
    # Transcoding (admin)
    S("ffmpeg_path", "", "str", "transcoding", admin=True, restart=True, help="Override ffmpeg path", maxlen=500),
    S("ffprobe_path", "", "str", "transcoding", admin=True, restart=True, help="Override ffprobe path", maxlen=500),
    S("hwaccel", "auto", "enum", "transcoding", admin=True,
      choices=["auto", "none", "nvenc", "qsv", "vaapi", "videotoolbox", "amf", "mediacodec", "v4l2m2m"],
      help="Hardware video encoder"),
    S("x264_preset", "auto", "enum", "transcoding", admin=True,
      choices=["auto", "ultrafast", "superfast", "veryfast", "faster", "fast", "medium"], help="Software encoder speed"),
    S("crf", 22, "int", "transcoding", admin=True, min=16, max=32, help="Quality (lower = better)"),
    S("hls_mode", "ondemand", "enum", "transcoding", admin=True, choices=["ondemand", "simple"],
      help="On-demand: instant seeking. Simple: one ffmpeg process, robust fallback"),
    S("segment_format", "fmp4", "enum", "transcoding", admin=True, choices=["fmp4", "ts"], help="HLS segment format"),
    S("max_transcodes", 0, "int", "transcoding", admin=True, min=0, max=16, help="Max concurrent ffmpeg jobs (0 = auto)"),
    S("ffmpeg_threads", 0, "int", "transcoding", admin=True, min=0, max=64, help="ffmpeg threads (0 = auto)"),
    S("lookahead", 12, "int", "transcoding", admin=True, min=3, max=60, help="Segments to encode ahead"),
    S("cache_limit_gb", 10, "float", "transcoding", admin=True, min=0.5, max=1000, help="Cache size limit (GB)"),
    S("allow_hevc_out", False, "bool", "transcoding", admin=True, help="Allow HEVC/AV1 output"),
    S("tonemap", True, "bool", "transcoding", admin=True, help="Tone-map HDR to SDR when needed"),
    S("burn_in", True, "bool", "transcoding", admin=True, help="Burn in image subtitles (PGS/VobSub)"),
    S("pause_bg_while_streaming", True, "bool", "transcoding", admin=True,
      help="Pause thumbnails/probing while streaming"),
    # Network & security (admin)
    S("bind", "lan", "enum", "network", admin=True, restart=True, choices=["lan", "localhost"], help="Listen on"),
    S("port", 8000, "int", "network", admin=True, restart=True, min=1, max=65535, help="HTTP port"),
    S("require_password", False, "bool", "network", admin=True, help="Require a password/PIN to use LocalFlix"),
    S("session_days", 30, "int", "network", admin=True, min=1, max=365, help="Login session length (days)"),
    S("private_only", True, "bool", "network", admin=True, help="Only allow private-network clients"),
    S("ssl_cert", "", "str", "network", admin=True, restart=True, help="HTTPS certificate path", maxlen=500),
    S("ssl_key", "", "str", "network", admin=True, restart=True, help="HTTPS key path", maxlen=500),
    S("mdns_name", "localflix", "str", "network", admin=True, restart=True, help="mDNS name (needs zeroconf)",
      maxlen=40),
    S("show_qr", True, "bool", "network", help="Show QR codes"),
    S("max_connections", 64, "int", "network", admin=True, restart=True, min=4, max=1024, help="Max connections"),
    # Metadata
    S("prefer_local_art", True, "bool", "metadata", help="Prefer artwork files next to the media"),
    S("generate_thumbs", True, "bool", "metadata", help="Generate thumbnails from video frames"),
    S("tmdb_key", "", "secret", "metadata", admin=True, help="TMDB API key (optional)", maxlen=200),
    S("tmdb_language", "en-US", "str", "metadata", help="TMDB language", maxlen=10),
    S("library_mode", "auto", "enum", "metadata", choices=["auto", "videos"], help="Videos only: no movie/show grouping"),
    # Profiles
    S("profiles_enabled", True, "bool", "profiles", help="Show the \"Who's watching?\" screen"),
    S("kids_max_rating", "PG", "enum", "profiles", choices=["G", "PG", "PG-13", "R"], help="Kids rating ceiling"),
    # Advanced
    S("log_level", "INFO", "enum", "advanced", admin=True, choices=["DEBUG", "INFO", "WARNING", "ERROR"], help="Log level"),
    S("prevent_sleep", True, "bool", "advanced", admin=True, help="Prevent sleep while streaming"),
    S("debug_overlay", False, "bool", "advanced", "user", help="Show debug overlay"),
]
SPEC_BY_KEY = {s["key"]: s for s in SETTINGS_SPEC}


def validate_setting(key: str, value: Any) -> Any:
    """Validate and coerce a setting value; raises ValueError when invalid."""
    spec = SPEC_BY_KEY.get(key)
    if not spec:
        raise ValueError(f"unknown setting {key}")
    t = spec["type"]
    if t == "bool":
        if isinstance(value, str):
            value = value.lower() in ("1", "true", "yes", "on")
        return bool(value)
    if t in ("int", "float"):
        try:
            v = int(value) if t == "int" else float(value)
        except (TypeError, ValueError):
            raise ValueError(f"{key} must be a number") from None
        if not math.isfinite(v):
            raise ValueError(f"{key} must be a finite number")
        if "min" in spec and v < spec["min"]:
            raise ValueError(f"{key} must be ≥ {spec['min']}")
        if "max" in spec and v > spec["max"]:
            raise ValueError(f"{key} must be ≤ {spec['max']}")
        return v
    if t == "enum":
        if value not in spec["choices"]:
            raise ValueError(f"{key} must be one of {spec['choices']}")
        return value
    if t == "color":
        if not isinstance(value, str) or not re.fullmatch(r"#[0-9a-fA-F]{6}", value):
            raise ValueError(f"{key} must be a #RRGGBB colour")
        return value.upper()
    if t == "list":
        if isinstance(value, str):
            value = [x.strip() for x in value.split(",") if x.strip()]
        if not isinstance(value, list) or len(value) > 100:
            raise ValueError(f"{key} must be a list")
        return [str(x)[:200] for x in value]
    if t in ("str", "secret"):
        v = "" if value is None else str(value)
        if len(v) > spec.get("maxlen", 1000):
            raise ValueError(f"{key} too long")
        return v.strip()
    raise ValueError("bad type")


# ===== SECTION: database =====
SCHEMA = """
CREATE TABLE IF NOT EXISTS libraries(
  id INTEGER PRIMARY KEY AUTOINCREMENT, path TEXT NOT NULL UNIQUE, type TEXT NOT NULL DEFAULT 'mixed',
  name TEXT, enabled INTEGER NOT NULL DEFAULT 1, added_at REAL);
CREATE TABLE IF NOT EXISTS series(
  id TEXT PRIMARY KEY, title TEXT, sort_title TEXT, year INTEGER, overview TEXT, poster_path TEXT,
  backdrop_path TEXT, tmdb_id INTEGER, genres_json TEXT, rating REAL, library_id INTEGER);
CREATE TABLE IF NOT EXISTS items(
  id TEXT PRIMARY KEY, library_id INTEGER NOT NULL REFERENCES libraries(id) ON DELETE CASCADE,
  rel_path TEXT NOT NULL, size INTEGER, mtime_ns INTEGER, kind TEXT, title TEXT, sort_title TEXT, year INTEGER,
  series_id TEXT, season INTEGER, episode INTEGER, episode_end INTEGER, episode_title TEXT, overview TEXT,
  rating REAL, content_rating TEXT, genres_json TEXT, cast_json TEXT, duration_s REAL, container TEXT,
  bitrate INTEGER, v_codec TEXT, v_profile TEXT, v_level INTEGER, v_pixfmt TEXT, v_w INTEGER, v_h INTEGER,
  v_fps REAL, v_bitdepth INTEGER, v_rotation INTEGER, hdr TEXT, faststart INTEGER, start_time REAL,
  probe_json TEXT, folder TEXT, unplayable TEXT, added_at REAL, probed_at REAL, missing_at REAL, tmdb_id INTEGER);
CREATE TABLE IF NOT EXISTS streams(
  item_id TEXT NOT NULL REFERENCES items(id) ON DELETE CASCADE, idx INTEGER, sidx INTEGER, type TEXT,
  codec TEXT, profile TEXT, lang TEXT, title TEXT, channels INTEGER, layout TEXT, default_flag INTEGER,
  forced_flag INTEGER, bitmap_flag INTEGER, external_path TEXT);
CREATE TABLE IF NOT EXISTS chapters(item_id TEXT NOT NULL REFERENCES items(id) ON DELETE CASCADE, idx INTEGER,
  start_s REAL, end_s REAL, title TEXT);
CREATE TABLE IF NOT EXISTS profiles(id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, avatar TEXT,
  pin_hash TEXT, kids INTEGER NOT NULL DEFAULT 0, created_at REAL, prefs_json TEXT);
CREATE TABLE IF NOT EXISTS watch_state(profile_id INTEGER NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
  item_id TEXT NOT NULL, position_s REAL, duration_s REAL, watched INTEGER DEFAULT 0, last_played_at REAL,
  audio_idx INTEGER, sub_idx INTEGER, PRIMARY KEY(profile_id, item_id));
CREATE TABLE IF NOT EXISTS mylist(profile_id INTEGER NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
  item_id TEXT NOT NULL, added_at REAL, PRIMARY KEY(profile_id, item_id));
CREATE TABLE IF NOT EXISTS settings(scope TEXT NOT NULL, key TEXT NOT NULL, value_json TEXT,
  PRIMARY KEY(scope, key));
CREATE TABLE IF NOT EXISTS sessions(token_hash TEXT PRIMARY KEY, profile_id INTEGER, created_at REAL,
  expires_at REAL, ip TEXT, admin INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS artwork(item_id TEXT NOT NULL, kind TEXT NOT NULL, path TEXT, source TEXT,
  updated_at REAL, PRIMARY KEY(item_id, kind));
CREATE TABLE IF NOT EXISTS kv(key TEXT PRIMARY KEY, value TEXT);
CREATE INDEX IF NOT EXISTS ix_items_lib_rel ON items(library_id, rel_path);
CREATE INDEX IF NOT EXISTS ix_items_series ON items(series_id, season, episode);
CREATE INDEX IF NOT EXISTS ix_items_added ON items(added_at);
CREATE INDEX IF NOT EXISTS ix_ws_last ON watch_state(profile_id, last_played_at);
CREATE INDEX IF NOT EXISTS ix_streams_item ON streams(item_id);
CREATE INDEX IF NOT EXISTS ix_chapters_item ON chapters(item_id);
"""

# Forward-only migrations: index i upgrades user_version i → i+1.
MIGRATIONS: list[Callable[[sqlite3.Connection, bool], None]] = []


def _mig_1(conn: sqlite3.Connection, fts: bool) -> None:
    for stmt in SCHEMA.split(";"):
        if stmt.strip():
            conn.execute(stmt)


def _mig_2(conn: sqlite3.Connection, fts: bool) -> None:
    if fts:
        conn.execute("CREATE VIRTUAL TABLE IF NOT EXISTS fts_items USING fts5(id UNINDEXED, title, series, genres, "
                     "overview, tokenize='unicode61 remove_diacritics 2')")


def _mig_3(conn: sqlite3.Connection, fts: bool) -> None:
    cols = {r[1] for r in conn.execute("PRAGMA table_info(items)")}
    if "subs_external" not in cols:
        conn.execute("ALTER TABLE items ADD COLUMN subs_external INTEGER DEFAULT 0")


def _mig_4(conn: sqlite3.Connection, fts: bool) -> None:
    cols = {r[1] for r in conn.execute("PRAGMA table_info(libraries)")}
    for name, ddl in (("source", "TEXT NOT NULL DEFAULT 'user'"), ("auto_key", "TEXT"), ("last_scan", "REAL"),
                      ("offline", "INTEGER NOT NULL DEFAULT 0"), ("user_disabled", "INTEGER NOT NULL DEFAULT 0")):
        if name not in cols:
            conn.execute(f"ALTER TABLE libraries ADD COLUMN {name} {ddl}")


MIGRATIONS.extend([_mig_1, _mig_2, _mig_3, _mig_4])


def has_fts5() -> bool:
    try:
        c_ = sqlite3.connect(":memory:")
        c_.execute("CREATE VIRTUAL TABLE t USING fts5(a)")
        c_.close()
        return True
    except sqlite3.Error:
        return False


class Database:
    """SQLite (WAL) with connection-per-thread and a global write lock."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.local = threading.local()
        self.write_lock = threading.RLock()
        self.fts = has_fts5()
        self._all: list[sqlite3.Connection] = []
        self._all_lock = threading.Lock()
        self._open_checked()

    def _open_checked(self) -> None:
        try:
            conn = self.conn()
            r = conn.execute("PRAGMA quick_check").fetchone()
            if r and r[0] != "ok":
                raise sqlite3.DatabaseError(r[0])
            self.migrate()
        except sqlite3.DatabaseError as e:
            msg = str(e).lower()
            if isinstance(e, sqlite3.OperationalError) and not any(
                    k in msg for k in ("malformed", "not a database", "corrupt")):
                raise  # locked / unable to open: not corruption — never rename the user's DB
            log.error("Database corrupted (%s) — rebuilding index", e)
            self.close_all()
            stamp = time.strftime("%Y%m%d-%H%M%S")
            for suffix in ("", "-wal", "-shm"):
                p = Path(str(self.path) + suffix)
                if p.exists():
                    p.rename(Path(str(self.path) + f".corrupt-{stamp}{suffix}"))
            self.local = threading.local()
            self.migrate()
            self._restore_watch_backup()

    def conn(self) -> sqlite3.Connection:
        c_ = getattr(self.local, "conn", None)
        if c_ is None:
            c_ = sqlite3.connect(str(self.path), timeout=30, check_same_thread=False, isolation_level=None)
            c_.row_factory = sqlite3.Row
            c_.execute("PRAGMA journal_mode=WAL")
            c_.execute("PRAGMA synchronous=NORMAL")
            c_.execute("PRAGMA foreign_keys=ON")
            c_.execute("PRAGMA busy_timeout=30000")
            self.local.conn = c_
            with self._all_lock:
                self._all.append(c_)
        return c_

    def release_thread(self) -> None:
        """Close the calling thread's connection (short-lived HTTP worker threads)."""
        c_ = getattr(self.local, "conn", None)
        if c_ is None:
            return
        self.local.conn = None
        with self._all_lock, contextlib.suppress(ValueError):
            self._all.remove(c_)
        with contextlib.suppress(Exception):
            c_.close()

    def close_all(self) -> None:
        with self._all_lock:
            for c_ in self._all:
                with contextlib.suppress(Exception):
                    c_.close()
            self._all.clear()
        self.local = threading.local()

    def migrate(self) -> None:
        conn = self.conn()
        with self.write_lock:
            ver = conn.execute("PRAGMA user_version").fetchone()[0]
            while ver < len(MIGRATIONS):
                conn.execute("BEGIN")
                try:
                    MIGRATIONS[ver](conn, self.fts)
                    ver += 1
                    conn.execute(f"PRAGMA user_version={ver}")
                    conn.execute("COMMIT")
                except Exception:
                    conn.execute("ROLLBACK")
                    raise
            if self.fts:
                try:
                    conn.execute("SELECT 1 FROM fts_items LIMIT 1")
                except sqlite3.Error:
                    self.fts = False

    def q(self, sql: str, args: Iterable[Any] = ()) -> list[sqlite3.Row]:
        return self.conn().execute(sql, tuple(args)).fetchall()

    def one(self, sql: str, args: Iterable[Any] = ()) -> sqlite3.Row | None:
        return self.conn().execute(sql, tuple(args)).fetchone()

    def x(self, sql: str, args: Iterable[Any] = ()) -> sqlite3.Cursor:
        with self.write_lock:
            return self.conn().execute(sql, tuple(args))

    @contextlib.contextmanager
    def tx(self) -> Iterator[sqlite3.Connection]:
        with self.write_lock:
            conn = self.conn()
            conn.execute("BEGIN IMMEDIATE")
            try:
                yield conn
                conn.execute("COMMIT")
            except BaseException:
                conn.execute("ROLLBACK")
                raise

    # --- settings -------------------------------------------------------
    def get_setting(self, key: str, scope: str = "server") -> Any:
        r = self.one("SELECT value_json FROM settings WHERE scope=? AND key=?", (scope, key))
        if r is not None:
            try:
                return json.loads(r[0])
            except (TypeError, ValueError):
                pass
        if scope != "server":
            return self.get_setting(key, "server")
        spec = SPEC_BY_KEY.get(key)
        return spec["default"] if spec else None

    def set_setting(self, key: str, value: Any, scope: str = "server") -> None:
        self.x("INSERT OR REPLACE INTO settings(scope,key,value_json) VALUES(?,?,?)",
               (scope, key, json.dumps(value)))

    def all_settings(self, scope: str = "server") -> dict[str, Any]:
        out = {s["key"]: s["default"] for s in SETTINGS_SPEC}
        for r in self.q("SELECT key,value_json FROM settings WHERE scope='server'"):
            with contextlib.suppress(ValueError, TypeError):
                out[r[0]] = json.loads(r[1])
        if scope != "server":
            for r in self.q("SELECT key,value_json FROM settings WHERE scope=?", (scope,)):
                with contextlib.suppress(ValueError, TypeError):
                    out[r[0]] = json.loads(r[1])
        return out

    def kv_get(self, key: str, default: str | None = None) -> str | None:
        r = self.one("SELECT value FROM kv WHERE key=?", (key,))
        return r[0] if r else default

    def kv_set(self, key: str, value: str) -> None:
        self.x("INSERT OR REPLACE INTO kv(key,value) VALUES(?,?)", (key, value))

    # --- watch-state backup ----------------------------------------------
    def backup_watch_state(self) -> None:
        try:
            data = {
                "profiles": [dict(r) for r in self.q("SELECT id,name,avatar,pin_hash,kids,created_at,prefs_json FROM profiles")],
                "watch": [dict(r) for r in self.q(
                    "SELECT w.*, i.rel_path, l.path AS lib_path FROM watch_state w "
                    "LEFT JOIN items i ON i.id=w.item_id LEFT JOIN libraries l ON l.id=i.library_id")],
                "mylist": [dict(r) for r in self.q("SELECT * FROM mylist")],
                "libraries": [dict(r) for r in self.q("SELECT * FROM libraries")],
                "settings": [dict(r) for r in self.q("SELECT * FROM settings")],
                "saved_at": time.time(),
            }
            tmp = self.path.parent / "watch_backup.json.tmp"
            tmp.write_text(json.dumps(data), encoding="utf-8")
            os.replace(tmp, self.path.parent / "watch_backup.json")
        except Exception as e:  # noqa: BLE001
            log.warning("watch backup failed: %s", e)

    def _restore_watch_backup(self) -> None:
        p = self.path.parent / "watch_backup.json"
        if not p.exists():
            return
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            with self.tx() as conn:
                for r in data.get("libraries", []):
                    conn.execute("INSERT OR IGNORE INTO libraries(id,path,type,name,enabled,added_at) VALUES(?,?,?,?,?,?)",
                                 (r["id"], r["path"], r["type"], r["name"], r["enabled"], r["added_at"]))
                for r in data.get("profiles", []):
                    conn.execute("INSERT OR IGNORE INTO profiles(id,name,avatar,pin_hash,kids,created_at,prefs_json) "
                                 "VALUES(?,?,?,?,?,?,?)", (r["id"], r["name"], r["avatar"], r["pin_hash"], r["kids"],
                                                           r["created_at"], r["prefs_json"]))
                for r in data.get("settings", []):
                    conn.execute("INSERT OR IGNORE INTO settings(scope,key,value_json) VALUES(?,?,?)",
                                 (r["scope"], r["key"], r["value_json"]))
                for r in data.get("watch", []):
                    conn.execute("INSERT OR IGNORE INTO watch_state(profile_id,item_id,position_s,duration_s,watched,"
                                 "last_played_at,audio_idx,sub_idx) VALUES(?,?,?,?,?,?,?,?)",
                                 (r["profile_id"], r["item_id"], r["position_s"], r["duration_s"], r["watched"],
                                  r["last_played_at"], r["audio_idx"], r["sub_idx"]))
                for r in data.get("mylist", []):
                    conn.execute("INSERT OR IGNORE INTO mylist(profile_id,item_id,added_at) VALUES(?,?,?)",
                                 (r["profile_id"], r["item_id"], r["added_at"]))
            log.info("Restored watch state from backup")
        except Exception as e:  # noqa: BLE001
            log.warning("could not restore watch backup: %s", e)


# ===== SECTION: ffmpeg =====
HW_CANDIDATES: dict[str, list[str]] = {
    "nvenc": ["h264_nvenc"], "qsv": ["h264_qsv"], "vaapi": ["h264_vaapi"], "videotoolbox": ["h264_videotoolbox"],
    "amf": ["h264_amf"], "mediacodec": ["h264_mediacodec"], "v4l2m2m": ["h264_v4l2m2m"],
}
DOCTOR_ENCODERS = ["libx264", "libx265", "libsvtav1", "h264_nvenc", "hevc_nvenc", "h264_qsv", "h264_vaapi",
                   "h264_videotoolbox", "h264_amf", "h264_mediacodec", "h264_v4l2m2m"]


class FFmpeg:
    """Locates ffmpeg/ffprobe and caches capabilities (encoders, filters, HW test results)."""

    def __init__(self, paths: Paths, override: str = "", probe_override: str = "") -> None:
        self.paths = paths
        self.ffmpeg = self._locate("ffmpeg", override or os.environ.get("FFMPEG_PATH", ""))
        self.ffprobe = self._locate("ffprobe", probe_override, sibling=self.ffmpeg)
        self.version = ""
        self.encoders: set[str] = set()
        self.filters: set[str] = set()
        self.hwaccels: list[str] = []
        self.hw_results: dict[str, bool] = {}
        self.hw_encoder: str | None = None
        if self.ffmpeg:
            self._inspect()

    @property
    def available(self) -> bool:
        return bool(self.ffmpeg and self.ffprobe)

    def _locate(self, name: str, override: str, sibling: str | None = None) -> str | None:
        exe = name + (".exe" if PLAT.is_windows else "")
        cands: list[str] = []
        if override:
            p = Path(override).expanduser()
            if p.is_dir():
                p = p / exe
            if name == "ffprobe" and p.name.lower().startswith("ffmpeg"):
                p = p.with_name(p.name.lower().replace("ffmpeg", "ffprobe"))
            cands.append(str(p))
        if sibling:
            cands.append(str(Path(sibling).with_name(exe)))
        cands.append(str(self.paths.bin / exe))
        w = shutil.which(name)
        if w:
            cands.append(w)
        if PLAT.is_windows:
            la = os.environ.get("LOCALAPPDATA", "")
            cands += [str(Path(la) / "Microsoft" / "WinGet" / "Links" / exe), rf"C:\ffmpeg\bin\{exe}",
                      str(Path.home() / "scoop" / "shims" / exe), rf"C:\ProgramData\chocolatey\bin\{exe}"]
            wg = Path(la) / "Microsoft" / "WinGet" / "Packages"
            if wg.is_dir():
                for d in wg.glob("Gyan.FFmpeg*/*/bin"):
                    cands.append(str(d / exe))
        elif PLAT.is_mac:
            cands += [f"/opt/homebrew/bin/{name}", f"/usr/local/bin/{name}"]
        else:
            cands += [f"/usr/bin/{name}", f"/usr/local/bin/{name}", f"/data/data/com.termux/files/usr/bin/{name}"]
        for cnd in cands:
            if cnd and os.path.isfile(cnd) and os.access(cnd, os.X_OK):
                return cnd
        return None

    def _inspect(self) -> None:
        assert self.ffmpeg
        rc, out, _ = run_capture([self.ffmpeg, "-hide_banner", "-version"], timeout=15)
        if rc != 0:
            self.ffmpeg = None
            return
        first = out.decode(errors="replace").splitlines()[0] if out else ""
        m = re.search(r"version\s+(\S+)", first)
        self.version = m.group(1) if m else first
        rc, out, _ = run_capture([self.ffmpeg, "-hide_banner", "-encoders"], timeout=15)
        for line in out.decode(errors="replace").splitlines():
            m = re.match(r"\s*[VAS][\w.]{5}\s+(\S+)", line)
            if m:
                self.encoders.add(m.group(1))
        rc, out, _ = run_capture([self.ffmpeg, "-hide_banner", "-filters"], timeout=15)
        for line in out.decode(errors="replace").splitlines():
            m = re.match(r"\s*[TSC.]{2,3}\s+(\S+)\s", line)
            if m:
                self.filters.add(m.group(1))
        rc, out, _ = run_capture([self.ffmpeg, "-hide_banner", "-hwaccels"], timeout=15)
        self.hwaccels = [x.strip() for x in out.decode(errors="replace").splitlines()[1:] if x.strip()]

    # --- hardware encoders ----------------------------------------------
    def hw_args(self, kind: str, bitrate_k: int = 6000, crf: int = 22) -> tuple[list[str], list[str], str]:
        """Return (input args, encoder args, final pixel filter) for a HW family."""
        if kind == "nvenc":
            return [], ["-c:v", "h264_nvenc", "-preset", "p4", "-rc", "vbr", "-cq", str(crf + 1),
                        "-maxrate", f"{bitrate_k}k", "-bufsize", f"{bitrate_k * 2}k", "-profile:v", "high"], "format=yuv420p"
        if kind == "qsv":
            return [], ["-c:v", "h264_qsv", "-global_quality", str(crf + 1), "-maxrate", f"{bitrate_k}k",
                        "-profile:v", "high"], "format=nv12"
        if kind == "vaapi":
            dev = "/dev/dri/renderD128"
            return ["-vaapi_device", dev], ["-c:v", "h264_vaapi", "-qp", str(crf + 1), "-profile:v", "high"], \
                "format=nv12,hwupload"
        if kind == "videotoolbox":
            return [], ["-c:v", "h264_videotoolbox", "-b:v", f"{bitrate_k}k", "-allow_sw", "1", "-profile:v", "high"], \
                "format=yuv420p"
        if kind == "amf":
            return [], ["-c:v", "h264_amf", "-quality", "speed", "-rc", "cqp", "-qp_i", str(crf + 1),
                        "-qp_p", str(crf + 2)], "format=yuv420p"
        if kind == "mediacodec":
            return [], ["-c:v", "h264_mediacodec", "-b:v", f"{bitrate_k}k"], "format=yuv420p"
        if kind == "v4l2m2m":
            return [], ["-c:v", "h264_v4l2m2m", "-b:v", f"{bitrate_k}k"], "format=yuv420p"
        return [], [], "format=yuv420p"

    def test_encoder(self, kind: str) -> bool:
        if not self.ffmpeg:
            return False
        enc = HW_CANDIDATES.get(kind, [kind])[0]
        if enc not in self.encoders:
            return False
        if kind == "vaapi" and not os.path.exists("/dev/dri/renderD128"):
            return False
        pre, encargs, fmt = self.hw_args(kind, 2000)
        args = [self.ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin"] + pre + [
            "-f", "lavfi", "-i", "testsrc=size=1280x720:rate=30", "-t", "1", "-vf", fmt] + encargs + ["-f", "null", "-"]
        rc, _, err = run_capture(args, timeout=25)
        if rc != 0:
            log.debug("HW test %s failed: %s", kind, err.decode(errors="replace")[-300:])
        return rc == 0

    def test_software(self, enc: str) -> bool:
        if not self.ffmpeg or enc not in self.encoders:
            return False
        rc, _, _ = run_capture([self.ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin", "-f", "lavfi", "-i",
                                "testsrc=size=640x360:rate=30", "-t", "1", "-c:v", enc, "-f", "null", "-"], timeout=40)
        return rc == 0

    def detect_hw(self, db: Database | None = None, force: bool = False) -> dict[str, bool]:
        """Test-encode 1 s per HW family; cached in the DB per ffmpeg version."""
        key = f"hw_results:{self.version}"
        if db is not None and not force:
            cached = db.kv_get(key)
            if cached:
                with contextlib.suppress(ValueError):
                    self.hw_results = json.loads(cached)
                    return self.hw_results
        res: dict[str, bool] = {}
        for kind in HW_CANDIDATES:
            if HW_CANDIDATES[kind][0] in self.encoders:
                res[kind] = self.test_encoder(kind)
        self.hw_results = res
        if db is not None:
            db.kv_set(key, json.dumps(res))
        return res

    def pick_hw(self, setting: str) -> str | None:
        if setting == "none" or not self.ffmpeg:
            return None
        if setting != "auto":
            return setting if self.hw_results.get(setting) else None
        order = ["videotoolbox", "nvenc", "qsv", "vaapi", "amf", "mediacodec", "v4l2m2m"]
        for k in order:
            if self.hw_results.get(k):
                return k
        return None


# ===== SECTION: name-parser =====
_RELEASE_TAGS = (
    r"2160p|1080p|1080i|720p|576p|480p|360p|4k|uhd|8k|hdr10\+?|hdr|dv|dovi|dolby[ .]?vision|sdr|10bit|10-bit|8bit|"
    r"blu-?ray|bdrip|brrip|bdremux|remux|web-?dl|web-?rip|webrip|web|hdtv|hdrip|dvdrip|dvdscr|dvd|hdcam|camrip|cam|"
    r"ts-?rip|telesync|x264|x265|h\.?264|h\.?265|hevc|avc|av1|vp9|xvid|divx|aac(?:2\.0|5\.1)?|ac3|eac3|e-ac-3|"
    r"ddp?(?:\+)?(?:[ .]?[257]\.[01])?|dts(?:-?hd)?(?:[ .-]?ma)?|truehd|atmos|flac|opus|mp3|lpcm|5\.1|7\.1|2\.0|"
    r"proper|repack|extended|unrated|uncut|directors[ .]cut|imax|remastered|limited|internal|multi|dual[ .-]?audio|"
    r"hindi|tamil|telugu|eng|english|esubs?|subs?|nf|amzn|dsnp|hmax|atvp|hulu|zee5|jiocinema|hotstar|yts|yify|rarbg|"
    r"psa|pahe|tigole|qxr|ettv|eztv|nsd|amzn"
)
_TAG_RE = re.compile(r"(?:^|[\s._\-\[\(])(?:" + _RELEASE_TAGS + r")(?=$|[\s._\-\]\)])", re.IGNORECASE)
_YEAR_RE = re.compile(r"(?:^|[\s._\-\[\(])((?:19|20)\d{2})(?=$|[\s._\-\]\)])")
_EP_PATTERNS = [
    # S01E02, S01E01-E02, S01E01E02, S1 E2, S01.E02
    re.compile(r"(?<![a-z0-9])s(\d{1,2})[\s._-]*e(\d{1,4})(?:(?:[\s._-]*-[\s._-]*e?|[\s._-]*e)(\d{1,4}))?(?![0-9])", re.IGNORECASE),
    # 1x02, 01x02-03
    re.compile(r"(?<![a-z0-9])(\d{1,2})x(\d{2,3})(?:-(\d{2,3}))?(?![0-9p])", re.IGNORECASE),
    # Season 1 Episode 2
    re.compile(r"season[\s._-]*(\d{1,2})[\s._-]*episode[\s._-]*(\d{1,4})()", re.IGNORECASE),
]
_SEASON_DIR_RE = re.compile(r"^(?:season|series|staffel|saison|temporada|s)[\s._-]*(\d{1,3})$", re.IGNORECASE)
_EP_ONLY_RE = re.compile(r"(?<![a-z0-9])(?:episode|ep|e)[\s._-]*(\d{1,4})(?![0-9])", re.IGNORECASE)
_DASH_NUM_RE = re.compile(r"\s-\s(\d{1,4})(?:v\d)?(?=$|\s|\[|\()")
_BRACKETS_RE = re.compile(r"\[[^\]]*\]|\{[^}]*\}")


def _clean_title(s: str, cut_tags: bool = True) -> str:
    s = _BRACKETS_RE.sub(" ", s)
    s = re.sub(r"\((?!(?:19|20)\d{2}\))[^)]*\)", " ", s)
    if " " not in s.strip() or s.count(".") >= 2 or s.count("_") >= 2:
        s = re.sub(r"(?<!\b[A-Za-z])[._](?![A-Za-z]\b)", " ", s)
        s = s.replace("_", " ").replace(".", " ")
    m = _TAG_RE.search(s) if cut_tags else None
    if m and m.start() > 0:
        s = s[:m.start()]
    s = re.sub(r"[\s\-–—:,]+$", "", s)
    s = re.sub(r"^[\s\-–—:,]+", "", s)
    s = re.sub(r"\s{2,}", " ", s).strip()
    if s and (s.islower() or s.isupper()) and len(s) > 3:
        s = " ".join(w if (w.isupper() and len(w) <= 3 and not s.isupper()) else w.capitalize() for w in s.split(" "))
    return s


def _strip_group(stem: str) -> str:
    """Remove a trailing release group like ``-GRP`` after tags."""
    return re.sub(r"-[A-Za-z0-9]{2,12}$", "", stem) if _TAG_RE.search(stem) else stem


def parse_name(rel_path: str, library_type: str = "mixed") -> dict[str, Any]:
    """Guess movie/episode metadata from a relative path (a small ``guessit``)."""
    rel_path = rel_path.replace("\\", "/")
    parts = [p for p in rel_path.split("/") if p]
    filename = parts[-1]
    stem = filename.rsplit(".", 1)[0] if "." in filename else filename
    dirs = parts[:-1]
    res: dict[str, Any] = {"kind": "movie", "title": None, "year": None, "series": None, "season": None,
                           "episode": None, "episode_end": None, "episode_title": None}
    # Season from folder context
    season_dir = None
    show_dir = None
    for i in range(len(dirs) - 1, -1, -1):
        m = _SEASON_DIR_RE.match(dirs[i].strip())
        if m:
            season_dir = int(m.group(1))
            if i > 0:
                show_dir = dirs[i - 1]
            break
    work = _strip_group(stem)
    ep_match = None
    for pat in _EP_PATTERNS:
        m = pat.search(work)
        if m:
            ep_match = m
            res["season"] = int(m.group(1))
            res["episode"] = int(m.group(2))
            if m.group(3):
                e2 = int(m.group(3))
                if e2 > res["episode"]:
                    res["episode_end"] = e2
            break
    if ep_match is None:
        m = _DASH_NUM_RE.search(work.replace("_", " "))
        if m and library_type != "movies":
            num = m.group(1)
            n = int(num)
            if not (1900 <= n <= 2099):
                if len(num) == 3 and not num.startswith("0") and season_dir is None:
                    res["season"], res["episode"] = int(num[0]), int(num[1:])
                else:
                    res["season"], res["episode"] = (season_dir or 1), n
                work2 = work.replace("_", " ")
                ep_match = re.search(re.escape(m.group(0)), work2)
                work = work2
    if ep_match is None and (season_dir is not None or library_type == "shows"):
        m = _EP_ONLY_RE.search(work)
        if m:
            res["season"], res["episode"] = season_dir or 1, int(m.group(1))
            ep_match = m
        else:
            m = re.match(r"^\s*(\d{1,3})(?:[\s._-]+|$)", work)
            if m and not _YEAR_RE.match(work):
                res["season"], res["episode"] = season_dir or 1, int(m.group(1))
                ep_match = m
    if ep_match is not None and res["episode"] is not None:
        res["kind"] = "episode"
        before = work[:ep_match.start()]
        after = work[ep_match.end():]
        series = _clean_title(before)
        ym = _YEAR_RE.search(before)
        folder_name = _clean_title(show_dir) if show_dir else ""
        if ym and series and not (folder_name and folder_name.lower() == series.lower()):
            series = _clean_title(before[:ym.start()]) or series
        if not series:
            src = show_dir or (dirs[-1] if dirs and season_dir is None else (dirs[-2] if len(dirs) >= 2 else ""))
            series = _clean_title(src) if src else ""
            sy = _YEAR_RE.search(src or "")
            if sy and series:
                series = _clean_title(src[:sy.start()]) or series
        if not series:
            series = "Unknown Show"
        if season_dir is not None and ep_match.re in (_EP_ONLY_RE,):
            res["season"] = season_dir
        res["series"] = series
        after = _strip_group(after)
        tm = _TAG_RE.search(after)
        if tm:
            after = after[:tm.start()]
        et = _clean_title(after, cut_tags=False)
        res["episode_title"] = et or None
        res["title"] = et or f"Episode {res['episode']}"
        return res
    # Movie
    work = stem
    ym = None
    max_year = time.localtime().tm_year + 1
    for m in _YEAR_RE.finditer(work):
        if m.start() > 0 and int(m.group(1)) <= max_year:
            ym = m  # take the last plausible year that isn't at the start ("2001 A Space Odyssey 1968")
    title = (_clean_title(work[:ym.start()], cut_tags=False) or _clean_title(work[:ym.start()])) if ym else \
        _clean_title(_strip_group(work))
    year = int(ym.group(1)) if ym else None
    if year is None and dirs:
        dm = _YEAR_RE.search(dirs[-1])
        if dm and dm.start() > 0:
            year = int(dm.group(1))
            if not title:
                title = _clean_title(dirs[-1][:dm.start()])
    if not title:
        title = _clean_title(stem) or stem
    res["title"] = title
    res["year"] = year
    if library_type == "shows" and dirs:
        res["kind"] = "episode"
        res["series"] = _clean_title(dirs[0])
        res["season"], res["episode"] = season_dir or 1, 0
    return res


def sort_title(t: str) -> str:
    t = unicodedata.normalize("NFKD", t or "").lower()
    t = re.sub(r"^(the|a|an)\s+", "", t)
    return re.sub(r"\s+", " ", t).strip()


def stable_id(*parts: Any) -> str:
    return hashlib.sha1("\x1f".join(str(p) for p in parts).encode("utf-8", "surrogatepass")).hexdigest()[:16]


# ===== SECTION: probe =====
def mp4_faststart(path: Path) -> bool | None:
    """True if the ``moov`` atom precedes ``mdat`` (progressive playback friendly)."""
    try:
        with open(long_path(path), "rb") as f:
            pos = 0
            for _ in range(64):
                f.seek(pos)
                hdr = f.read(16)
                if len(hdr) < 8:
                    return None
                size, typ = struct.unpack(">I4s", hdr[:8])
                if size == 1:
                    size = struct.unpack(">Q", hdr[8:16])[0]
                elif size == 0:
                    return typ == b"moov"
                if typ == b"moov":
                    return True
                if typ == b"mdat":
                    return False
                if size < 8:
                    return None
                pos += size
    except OSError:
        return None
    return None


def _frac(s: Any) -> float:
    try:
        if isinstance(s, str) and "/" in s:
            a, b = s.split("/")
            return float(a) / float(b) if float(b) else 0.0
        return float(s)
    except (TypeError, ValueError, ZeroDivisionError):
        return 0.0


def probe_file(ff: FFmpeg, path: Path, timeout: float = 120) -> dict[str, Any]:
    """Run ffprobe and return a normalised technical metadata dict."""
    if not ff.ffprobe:
        raise RuntimeError("ffprobe not available")
    rc, out, err = run_capture([ff.ffprobe, "-v", "error", "-print_format", "json", "-show_format", "-show_streams",
                                "-show_chapters", long_path(path)], timeout=timeout, low_priority=True)
    if rc != 0:
        raise RuntimeError((err.decode(errors="replace").strip().splitlines() or ["ffprobe failed"])[0][:300])
    return normalise_probe(json.loads(out.decode("utf-8", errors="replace") or "{}"), path)


def _stream_duration(st: dict[str, Any]) -> float:
    """A stream's own duration (MKV keeps it in the DURATION tag as HH:MM:SS.nnnnnnnnn)."""
    d = _frac(st.get("duration"))
    if d > 0:
        return d
    tag = next((v for k, v in (st.get("tags") or {}).items() if k.upper().startswith("DURATION")), "")
    m = re.match(r"(\d+):(\d+):(\d+(?:\.\d+)?)", str(tag))
    return int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3)) if m else 0.0


def video_stream_duration(ff: FFmpeg, path: Path) -> float:
    """Quick header-only lookup of the video stream duration (for items probed by older versions)."""
    if not ff.ffprobe:
        return 0.0
    rc, out, _ = run_capture([ff.ffprobe, "-v", "error", "-select_streams", "V:0", "-show_entries",
                              "stream=duration:stream_tags=DURATION", "-of", "json", long_path(path)], timeout=20)
    if rc != 0:
        return 0.0
    with contextlib.suppress(ValueError, IndexError, TypeError):
        return _stream_duration(json.loads(out.decode() or "{}")["streams"][0])
    return 0.0


def normalise_probe(data: dict[str, Any], path: Path | None = None) -> dict[str, Any]:
    fmt = data.get("format", {}) or {}
    streams = data.get("streams", []) or []
    fmt_name = (fmt.get("format_name") or "").lower()
    ext = path.suffix.lower() if path else ""
    container = {".mp4": "mp4", ".m4v": "mp4", ".mov": "mov", ".webm": "webm", ".mkv": "mkv", ".avi": "avi",
                 ".ts": "ts", ".m2ts": "ts", ".mts": "ts"}.get(ext)
    if not container:
        if "matroska" in fmt_name:
            container = "webm" if "webm" in fmt_name and ext == ".webm" else "mkv"
        elif "mp4" in fmt_name or "mov" in fmt_name:
            container = "mp4"
        else:
            container = fmt_name.split(",")[0] or ext.lstrip(".")
    res: dict[str, Any] = {
        "duration": _frac(fmt.get("duration")), "container": container, "bitrate": int(_frac(fmt.get("bit_rate"))),
        "start_time": _frac(fmt.get("start_time")), "video": None, "audio": [], "subs": [], "chapters": [],
        "attachments": 0, "tags": {k.lower(): v for k, v in (fmt.get("tags") or {}).items()},
    }
    a_i = s_i = 0
    for st in streams:
        ct = st.get("codec_type")
        tags = {k.lower(): v for k, v in (st.get("tags") or {}).items()}
        disp = st.get("disposition") or {}
        if ct == "video" and not disp.get("attached_pic"):
            if res["video"] is not None:
                continue
            pix = st.get("pix_fmt") or ""
            depth = int(st.get("bits_per_raw_sample") or 0) or (10 if "10" in pix else 12 if "12" in pix else 8)
            hdr = ""
            trc = st.get("color_transfer") or ""
            if trc == "smpte2084":
                hdr = "hdr10"
            elif trc == "arib-std-b67":
                hdr = "hlg"
            rot = 0
            for sd in st.get("side_data_list") or []:
                if "DOVI" in (sd.get("side_data_type") or "") or "Dolby Vision" in (sd.get("side_data_type") or ""):
                    hdr = "dv"
                if "rotation" in sd:
                    with contextlib.suppress(TypeError, ValueError):
                        rot = int(float(sd["rotation"]))
            if "rotate" in tags:
                with contextlib.suppress(ValueError):
                    rot = int(tags["rotate"])
            sar = st.get("sample_aspect_ratio") or "1:1"
            res["video"] = {
                "index": st.get("index"), "codec": st.get("codec_name"), "profile": st.get("profile"),
                "level": st.get("level"), "pix_fmt": pix, "bitdepth": depth, "width": st.get("width") or 0,
                "height": st.get("height") or 0, "fps": _frac(st.get("avg_frame_rate") or st.get("r_frame_rate")),
                "hdr": hdr, "rotation": rot, "sar": sar, "bitrate": int(_frac(st.get("bit_rate"))),
                "codec_tag": st.get("codec_tag_string"),
                "duration": _stream_duration(st),
            }
        elif ct == "audio":
            res["audio"].append({
                "index": st.get("index"), "sidx": a_i, "codec": st.get("codec_name"), "profile": st.get("profile"),
                "channels": st.get("channels") or 2, "layout": st.get("channel_layout") or "",
                "sample_rate": int(_frac(st.get("sample_rate"))), "bitrate": int(_frac(st.get("bit_rate"))),
                "lang": lang_norm(tags.get("language")) if tags.get("language") not in (None, "und") else "",
                "title": tags.get("title") or "",
                "default": bool(disp.get("default")), "forced": bool(disp.get("forced")),
            })
            a_i += 1
        elif ct == "subtitle":
            codec = st.get("codec_name") or ""
            res["subs"].append({
                "index": st.get("index"), "sidx": s_i, "codec": codec,
                "lang": lang_norm(tags.get("language")) if tags.get("language") not in (None, "und") else "",
                "title": tags.get("title") or "", "default": bool(disp.get("default")),
                "forced": bool(disp.get("forced")) or "forced" in (tags.get("title") or "").lower(),
                "bitmap": codec in ("hdmv_pgs_subtitle", "dvd_subtitle", "dvb_subtitle", "xsub", "pgssub"),
            })
            s_i += 1
        elif ct == "attachment":
            res["attachments"] += 1
    for i, ch in enumerate(data.get("chapters") or []):
        res["chapters"].append({"idx": i, "start": _frac(ch.get("start_time")), "end": _frac(ch.get("end_time")),
                                "title": ((ch.get("tags") or {}).get("title") or f"Chapter {i + 1}")})
    if not res["duration"] and res["video"]:
        res["duration"] = 0.0
    return res


def keyframe_table(ff: FFmpeg, path: Path, cache: Path, timeout: float = 600,
                   stream: str = "V:0") -> list[float] | None:
    """Return video keyframe times (seconds, relative to start_time), cached on disk."""
    if cache.exists():
        with contextlib.suppress(ValueError, OSError):
            return json.loads(cache.read_text())
    if not ff.ffprobe:
        return None
    rc, out, _ = run_capture([ff.ffprobe, "-v", "error", "-select_streams", stream, "-show_entries",
                              "packet=pts_time,flags", "-of", "csv=p=0", long_path(path)],
                             timeout=timeout, low_priority=True)
    if rc != 0:
        return None
    kfs: list[float] = []
    for line in out.decode(errors="ignore").splitlines():
        parts = line.split(",")
        if len(parts) >= 2 and "K" in parts[1] and parts[0] not in ("", "N/A"):
            with contextlib.suppress(ValueError):
                kfs.append(float(parts[0]))
    kfs = sorted(set(kfs))
    if not kfs:
        return None
    tmp = cache.with_name(f"{cache.stem}.{os.getpid()}.{threading.get_ident()}.tmp")
    try:
        tmp.write_text(json.dumps(kfs))
        os.replace(tmp, cache)
    except OSError as e:
        log.debug("keyframe cache write failed: %s", e)
        with contextlib.suppress(OSError):
            tmp.unlink()
    return kfs


# ===== SECTION: subtitles =====
def decode_text(data: bytes) -> str:
    """Decode subtitle bytes: BOM → utf-8 → utf-16 → (chardet) → cp1252 → latin-1."""
    if data.startswith(b"\xef\xbb\xbf"):
        return data[3:].decode("utf-8", errors="replace")
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        return data.decode("utf-16", errors="replace")
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        pass
    if len(data) > 4 and (data[1:2] == b"\x00" or data[0:1] == b"\x00"):
        with contextlib.suppress(UnicodeDecodeError):
            return data.decode("utf-16-le" if data[1:2] == b"\x00" else "utf-16-be")
    try:
        import chardet  # type: ignore
        enc = (chardet.detect(data[:200000]) or {}).get("encoding")
        if enc:
            return data.decode(enc, errors="replace")
    except Exception:  # noqa: BLE001, S110
        pass
    try:
        return data.decode("cp1252")
    except UnicodeDecodeError:
        return data.decode("latin-1", errors="replace")


def _vtt_ts(sec: float) -> str:
    sec = max(0.0, sec)
    h = int(sec // 3600)
    m = int(sec % 3600 // 60)
    s = sec % 60
    return f"{h:02d}:{m:02d}:{s:06.3f}"


def _html_to_vtt_markup(t: str) -> str:
    t = re.sub(r"<\s*font[^>]*color\s*=\s*[\"']?#?([0-9a-fA-F]{6})[\"']?[^>]*>(.*?)<\s*/\s*font\s*>",
               lambda m: f"<c.c{m.group(1).upper()}>{m.group(2)}</c>", t, flags=re.IGNORECASE | re.DOTALL)
    t = re.sub(r"<\s*/?\s*font[^>]*>", "", t, flags=re.IGNORECASE)
    t = re.sub(r"<\s*(/?)\s*([ibu])\s*>", lambda m: f"<{m.group(1)}{m.group(2).lower()}>", t, flags=re.IGNORECASE)
    t = re.sub(r"\{\\an?\d+\}", "", t)
    t = re.sub(r"<(?!/?(?:i|b|u|c(?:\.[\w]+)?)>)[^>]*>", "", t)
    return t.replace("-->", "→")


def srt_to_vtt(text: str) -> str:
    """Convert SubRip (or loose VTT) text to WebVTT."""
    text = text.replace("\r\n", "\n").replace("\r", "\n").lstrip("\ufeff")
    if text.lstrip().startswith("WEBVTT"):
        return text if text.endswith("\n") else text + "\n"
    out = ["WEBVTT", ""]
    ts_re = re.compile(r"(\d{1,2}):(\d{2}):(\d{2})[,.](\d{1,3})\s*-->\s*(\d{1,2}):(\d{2}):(\d{2})[,.](\d{1,3})")
    for block in re.split(r"\n\s*\n", text):
        lines = [ln for ln in block.split("\n") if ln.strip() != ""]
        for i, ln in enumerate(lines):
            m = ts_re.search(ln)
            if m:
                g = m.groups()
                a = int(g[0]) * 3600 + int(g[1]) * 60 + int(g[2]) + int(g[3].ljust(3, "0")) / 1000
                b = int(g[4]) * 3600 + int(g[5]) * 60 + int(g[6]) + int(g[7].ljust(3, "0")) / 1000
                body = "\n".join(_html_to_vtt_markup(x) for x in lines[i + 1:])
                if body.strip():
                    out.append(f"{_vtt_ts(a)} --> {_vtt_ts(b)}")
                    out.append(body)
                    out.append("")
                break
    return "\n".join(out) + "\n"


def ass_to_vtt(text: str) -> str:
    """Flatten ASS/SSA to WebVTT, keeping italics/bold/underline/primary colour."""
    text = text.replace("\r\n", "\n").replace("\r", "\n").lstrip("\ufeff")
    fmt: list[str] = []
    cues: list[tuple[float, float, str]] = []

    def ts(s: str) -> float:
        h, m, rest = s.strip().split(":")
        return int(h) * 3600 + int(m) * 60 + float(rest)

    def conv(t: str) -> str:
        t = t.replace("\\N", "\n").replace("\\n", "\n").replace("\\h", " ")
        out = []
        open_tags: list[str] = []
        pos = 0
        for m in re.finditer(r"\{([^}]*)\}", t):
            out.append(html.escape(t[pos:m.start()], quote=False))
            pos = m.end()
            for tag in re.findall(r"\\(\d?[a-z]+)([^\\]*)", m.group(1)):
                name, val = tag
                if name == "i" and val in ("1", "0"):
                    out.append("<i>" if val == "1" else "</i>")
                elif name == "b" and val in ("1", "0"):
                    out.append("<b>" if val == "1" else "</b>")
                elif name == "u" and val in ("1", "0"):
                    out.append("<u>" if val == "1" else "</u>")
                elif name in ("c", "1c"):
                    mm = re.match(r"&H([0-9A-Fa-f]{2})?([0-9A-Fa-f]{2})([0-9A-Fa-f]{2})([0-9A-Fa-f]{2})&?", val)
                    if "c" in open_tags:
                        out.append("</c>")
                        open_tags.remove("c")
                    if mm:
                        bb, gg, rr = mm.group(2), mm.group(3), mm.group(4)
                        out.append(f"<c.c{rr}{gg}{bb}".upper().replace("<C.C", "<c.c") + ">")
                        open_tags.append("c")
                elif name == "r":
                    for o in reversed(open_tags):
                        out.append(f"</{o}>")
                    open_tags.clear()
        out.append(html.escape(t[pos:], quote=False))
        for o in reversed(open_tags):
            out.append(f"</{o}>")
        res = "".join(out)
        for tg in ("i", "b", "u"):
            if res.count(f"<{tg}>") > res.count(f"</{tg}>"):
                res += f"</{tg}>"
        return res.replace("-->", "→")

    section = ""
    for line in text.split("\n"):
        ls = line.strip()
        if ls.startswith("["):
            section = ls.lower()
            continue
        if section == "[events]":
            if ls.lower().startswith("format:"):
                fmt = [x.strip().lower() for x in ls[7:].split(",")]
            elif ls.lower().startswith("dialogue:") and fmt:
                vals = ls[9:].split(",", len(fmt) - 1)
                if len(vals) < len(fmt):
                    continue
                d = dict(zip(fmt, vals))
                with contextlib.suppress(ValueError, KeyError):
                    body = conv(d.get("text", ""))
                    if body.strip():
                        cues.append((ts(d["start"]), ts(d["end"]), body))
    cues.sort(key=lambda x: x[0])
    out = ["WEBVTT", ""]
    for a, b, body in cues:
        out += [f"{_vtt_ts(a)} --> {_vtt_ts(b)}", body, ""]
    return "\n".join(out) + "\n"


def microdvd_to_vtt(text: str, fps: float = 23.976) -> str:
    out = ["WEBVTT", ""]
    first = True
    for line in text.replace("\r", "").split("\n"):
        m = re.match(r"\{(\d+)\}\{(\d+)\}(.*)", line.strip())
        if m and first:
            first = False
            with contextlib.suppress(ValueError):  # "{1}{1}25.000" header declares the frame rate
                hdr = float(m.group(3).strip())
                if int(m.group(1)) <= 1 and 10 <= hdr <= 120:
                    fps = hdr
                    continue
        if m:
            a, b = int(m.group(1)) / fps, int(m.group(2)) / fps
            body = re.sub(r"\{[^}]*\}", "", m.group(3)).replace("|", "\n")
            out += [f"{_vtt_ts(a)} --> {_vtt_ts(b)}", html.escape(body, quote=False), ""]
    return "\n".join(out) + "\n"


def _ttml_text(node: Any, parts: list[str]) -> None:
    """Collect the text of a TTML <p>, turning <br/> into newlines."""
    if node.text:
        parts.append(node.text)
    for ch in node:
        if ch.tag.endswith("br"):
            parts.append("\n")
        else:
            _ttml_text(ch, parts)
        if ch.tail:
            parts.append(ch.tail)


def ttml_to_vtt(text: str) -> str:
    import xml.etree.ElementTree as ET

    tick_rate = frame_rate = 0.0

    def tt(v: str) -> float:
        v = v.strip()
        if v.endswith("t"):
            return float(v[:-1]) / (tick_rate or 10_000_000)
        if v.endswith("f"):
            return float(v[:-1]) / (frame_rate or 25)
        if v.endswith("h"):
            return float(v[:-1]) * 3600
        if v.endswith("m") and not v.endswith("ms"):
            return float(v[:-1]) * 60
        if v.endswith("ms"):
            return float(v[:-2]) / 1000
        if v.endswith("s"):
            return float(v[:-1])
        parts = v.split(":")
        if len(parts) == 3:
            return int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2].replace(",", "."))
        if len(parts) == 4:  # HH:MM:SS:FF
            return int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2]) + int(parts[3]) / (frame_rate or 25)
        return float(v)

    out = ["WEBVTT", ""]
    try:
        root = ET.fromstring(text.encode("utf-8"))
    except ET.ParseError:
        return "WEBVTT\n\n"
    for k, v in root.attrib.items():
        with contextlib.suppress(ValueError):
            if k.endswith("tickRate"):
                tick_rate = float(v)
            elif k.endswith("frameRate"):
                frame_rate = float(v)
    for el in root.iter():
        if el.tag.endswith("}p") or el.tag == "p":
            b, e = el.get("begin"), el.get("end")
            if not b or not e:
                continue
            parts: list[str] = []
            _ttml_text(el, parts)
            with contextlib.suppress(ValueError):
                out += [f"{_vtt_ts(tt(b))} --> {_vtt_ts(tt(e))}", html.escape("".join(parts).strip(), quote=False), ""]
    return "\n".join(out) + "\n"


def convert_subtitle_file(path: Path, fps: float = 23.976) -> str:
    data = Path(long_path(path)).read_bytes()
    text = decode_text(data)
    ext = path.suffix.lower()
    if ext in (".ass", ".ssa"):
        return ass_to_vtt(text)
    if ext == ".sub":
        return microdvd_to_vtt(text, fps or 23.976)
    if ext == ".ttml" or ext == ".xml":
        return ttml_to_vtt(text)
    return srt_to_vtt(text)


def find_external_subs(media: Path) -> list[dict[str, Any]]:
    """Find ``<media>.<lang>[.forced|.sdh].ext`` next to the file or in ``Subs/``."""
    stem = media.stem.lower()
    found: list[dict[str, Any]] = []
    dirs = [media.parent]
    own_dirs: set[str] = set()  # Subs/<media stem>/ — every file there belongs to this media
    seen_dirs: set[str] = {os.path.normcase(str(media.parent.resolve()))}
    try:
        children = {e.name.lower(): e.path for e in os.scandir(media.parent) if e.is_dir()}
    except OSError:
        children = {}
    for d in ("subs", "subtitles", "sub"):
        if d not in children:
            continue
        p = Path(children[d])
        key = os.path.normcase(str(p.resolve()))
        if key in seen_dirs:
            continue
        seen_dirs.add(key)
        dirs.append(p)
        if (p / media.stem).is_dir():
            dirs.append(p / media.stem)
            own_dirs.add(str(p / media.stem))
    seen_files: set[str] = set()
    for d in dirs:
        try:
            entries = list(os.scandir(d))
        except OSError:
            continue
        for e in entries:
            if not e.is_file():
                continue
            p = Path(e.path)
            if p.suffix.lower() not in SUB_EXTS:
                continue
            ps = p.stem.lower()
            in_own_dir = str(d) in own_dirs
            if not (ps == stem or ps.startswith((stem + ".", stem + "_")) or in_own_dir):
                continue
            fkey = os.path.normcase(str(p.resolve()))
            if fkey in seen_files:
                continue
            seen_files.add(fkey)
            rest = ps[len(stem):].strip("._ -") if ps.startswith(stem) else ps
            tokens = [t for t in re.split(r"[._ \-]", rest) if t]
            lang = ""
            forced = sdh = False
            title_bits = []
            for t in tokens:
                if t in ("forced", "foreign"):
                    forced = True
                elif t in ("sdh", "cc", "hi") and t != "hi" or (t == "hi" and lang):
                    sdh = True
                elif not lang and (t in LANGS or lang_norm(t) in LANGS):
                    lang = lang_norm(t)
                elif t.isdigit():
                    continue
                else:
                    title_bits.append(t)
            found.append({"path": str(p), "lang": lang, "forced": forced, "sdh": sdh, "codec": p.suffix.lower()[1:],
                          "title": " ".join(title_bits)})
    found.sort(key=lambda x: (x["lang"] or "zzz", x["forced"], x["path"]))
    return found


# ===== SECTION: events =====
class EventBus:
    """Fan-out of server events to SSE subscribers."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._subs: list[queue.Queue[tuple[str, Any]]] = []

    def subscribe(self) -> queue.Queue[tuple[str, Any]]:
        q: queue.Queue[tuple[str, Any]] = queue.Queue(maxsize=200)
        with self._lock:
            self._subs.append(q)
        return q

    def unsubscribe(self, q: queue.Queue[tuple[str, Any]]) -> None:
        with self._lock:
            if q in self._subs:
                self._subs.remove(q)

    def publish(self, event: str, data: Any = None) -> None:
        with self._lock:
            subs = list(self._subs)
        for q in subs:
            with contextlib.suppress(queue.Full):
                q.put_nowait((event, data))

    @property
    def count(self) -> int:
        return len(self._subs)


# ===== SECTION: scanner =====
class Scanner:
    """Incremental library walker + ffprobe queue (§6.1)."""

    def __init__(self, app: App) -> None:
        self.app = app
        self.db = app.db
        self._wake = threading.Event()
        self._full = False
        self._stop = threading.Event()
        self.scanning = False
        self.status: dict[str, Any] = {"state": "idle", "last_scan": None, "errors": [], "found": 0, "probed": 0,
                                       "queue": 0}
        self.probe_q: queue.PriorityQueue[tuple[int, float, str]] = queue.PriorityQueue()
        self._queued: set[str] = set()
        self._qlock = threading.Lock()
        self._threads: list[threading.Thread] = []
        self._kf_running: set[str] = set()
        self._kf_q: queue.PriorityQueue[tuple[int, float, str, Path, Path]] = queue.PriorityQueue()
        self._kf_thread: threading.Thread | None = None
        self._kf_prio: dict[str, int] = {}
        self._manual = False

    def start(self) -> None:
        t = threading.Thread(target=self._loop, name="scanner", daemon=True)
        t.start()
        self._threads.append(t)
        n = 1 if PLAT.is_termux else 2
        for i in range(n):
            t = threading.Thread(target=self._probe_worker, name=f"probe-{i}", daemon=True)
            t.start()
            self._threads.append(t)

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()
        for _ in self._threads:
            self.probe_q.put((99, 0, ""))

    def trigger(self, full: bool = False) -> None:
        self._full = self._full or full
        self._manual = True
        self._wake.set()

    def _loop(self) -> None:
        first = True
        while not self._stop.is_set():
            if not first:
                user_iv = int(self.app.setting("scan_interval") or 0)
                auto_iv = int(self.app.setting("auto_scan_interval") or 0)
                ivs = [i for i in (user_iv, auto_iv) if i > 0]
                if self.app.setting("library_scope") != "folders":
                    ivs.append(60)  # cheap mount check: picks up USB drives / SD cards as they appear
                self._wake.wait(timeout=min(ivs) if ivs else None)
            if self._stop.is_set():
                break
            manual = first or self._manual
            first = False
            self._wake.clear()
            self._manual = False
            full, self._full = self._full, False
            try:
                self.scan_all(full, manual=manual)
            except Exception:  # pragma: no cover
                log.exception("scan failed")

    # --- auto-discovered libraries ------------------------------------------
    def sync_auto_libraries(self) -> bool:
        """Create/enable/disable auto libraries for the current scope and refresh offline flags."""
        scope = str(self.app.setting("library_scope") or "folders")
        desired = {os.path.normcase(d["path"]): d for d in discover_media_roots(scope)}
        changed = False
        with self.db.tx() as conn:
            rows = conn.execute("SELECT * FROM libraries").fetchall()
            by_path = {os.path.normcase(r["path"]): r for r in rows}
            for norm, d in desired.items():
                r = by_path.get(norm)
                if r is None:
                    conn.execute("INSERT INTO libraries(path,type,name,enabled,added_at,source,auto_key) "
                                 "VALUES(?,?,?,1,?,'auto',?)", (d["path"], "mixed", d["label"], time.time(), d["key"]))
                    changed = True
                elif r["source"] == "auto" and not r["enabled"] and not r["user_disabled"]:
                    conn.execute("UPDATE libraries SET enabled=1 WHERE id=?", (r["id"],))
                    changed = True
            for r in rows:
                if r["source"] == "auto" and r["enabled"] and os.path.normcase(r["path"]) not in desired:
                    conn.execute("UPDATE libraries SET enabled=0 WHERE id=?", (r["id"],))  # kept: ids stay stable
                    changed = True
            for r in conn.execute("SELECT id, path, offline FROM libraries WHERE enabled=1").fetchall():
                off = int(not (os.path.isdir(r["path"]) and os.access(r["path"], os.R_OK)))
                if off != r["offline"]:
                    conn.execute("UPDATE libraries SET offline=? WHERE id=?", (off, r["id"]))
                    changed = True
        if changed:
            self.app.events.publish("library.updated", {"libraries": True})
        return changed

    def _due(self, lib: sqlite3.Row, now: float) -> bool:
        iv = int(self.app.setting("auto_scan_interval" if lib["source"] == "auto" else "scan_interval") or 0)
        if lib["last_scan"] is None:
            return True
        return iv > 0 and now - lib["last_scan"] >= iv - 1

    def scan_all(self, full: bool = False, manual: bool = True) -> None:
        self.scanning = True
        self.status.update(state="scanning", errors=[], found=0)
        self.app.events.publish("scan.progress", {"state": "scanning", "found": 0})
        t0 = time.time()
        changed = False
        try:
            changed |= self.sync_auto_libraries()
            libs = self.db.q("SELECT * FROM libraries WHERE enabled=1 ORDER BY length(path) DESC")
            for lib in libs:
                if manual or full or self._due(lib, t0):
                    changed |= self.scan_library(lib, full)
            self._cleanup_series()
        finally:
            self.scanning = False
            self.status.update(state="idle", last_scan=time.time(), duration=round(time.time() - t0, 2),
                               queue=self.probe_q.qsize())
            self.app.events.publish("scan.progress", dict(self.status))
            if changed:
                self.app.events.publish("library.updated", {})
        log.info("Scan finished in %.2fs (%d files)", time.time() - t0, self.status["found"])

    def _skip_roots(self, root_real: Path, lib_id: int) -> set[str]:
        """Folders inside this root owned by another library (deepest root wins) + LocalFlix's own data."""
        out: set[str] = set()
        for r in self.db.q("SELECT id, path FROM libraries WHERE enabled=1 AND id<>?", (lib_id,)):
            p = Path(r["path"])
            if p != root_real and p.is_relative_to(root_real):
                out.add(os.path.normcase(str(p)))
        for own in (self.app.paths.config, self.app.paths.cache, SCRIPT_DIR / ".git"):
            with contextlib.suppress(OSError):
                out.add(os.path.normcase(str(own.resolve())))
        return out

    def _walk(self, root: Path, lib_id: int = 0, auto: bool = False) -> Iterator[tuple[str, int, int]]:
        include_hidden = bool(self.app.setting("include_hidden"))
        patterns = [p.lower() for p in (self.app.setting("ignore_patterns") or [])]
        min_size = int(self.app.setting("auto_min_size_mb") or 0) * 1024 * 1024 if auto else 0
        try:
            root_real = root.resolve()
        except OSError:
            root_real = root
        skip = self._skip_roots(root_real, lib_id)
        visited: set[tuple[int, int]] = set()
        stack = [root]
        while stack:
            d = stack.pop()
            try:
                dst = os.stat(long_path(d))
                if (dst.st_dev, dst.st_ino) in visited and dst.st_ino:
                    continue  # symlink loop / already walked
                visited.add((dst.st_dev, dst.st_ino))
            except OSError:
                continue
            try:
                it = os.scandir(long_path(d))
            except PermissionError as e:
                self.status["errors"].append(f"Permission denied: {d}")
                if PLAT.is_termux and "storage" in str(d):
                    self.status["errors"].append("Run `termux-setup-storage` to grant storage access")
                log.warning("scan: %s", e)
                continue
            except OSError as e:
                self.status["errors"].append(f"{d}: {e.strerror}")
                continue
            with it:
                for e in it:
                    name = e.name
                    low = name.lower()
                    if not include_hidden and (name.startswith(".") or low in SKIP_DIRS):
                        continue
                    if low.startswith(".localflix") or low in SKIP_DIRS:
                        continue
                    try:
                        if PLAT.is_windows and not include_hidden:
                            attrs = getattr(e.stat(follow_symlinks=False), "st_file_attributes", 0)
                            if attrs & 0x6:
                                continue
                        if e.is_symlink():
                            # links must stay inside the library (same rule as safe_join for serving)
                            real = Path(e.path).resolve()
                            if real != root_real and not real.is_relative_to(root_real):
                                continue
                        if e.is_dir(follow_symlinks=True):
                            if auto and auto_excluded(name):
                                continue
                            dp = os.path.normcase(e.path)
                            if dp in skip or (e.is_symlink() and os.path.normcase(str(Path(e.path).resolve())) in skip):
                                continue
                            stack.append(Path(e.path))
                            continue
                        if not e.is_file(follow_symlinks=True):
                            continue
                    except OSError:
                        continue
                    ext = os.path.splitext(low)[1]
                    if ext not in VIDEO_EXTS:
                        continue
                    if any(fnmatch.fnmatch(low, p) for p in patterns):
                        continue
                    try:
                        st = e.stat()
                    except OSError:
                        continue
                    if st.st_size < min_size:
                        continue
                    if ext in (".ts", ".mts", ".m2ts") and not looks_like_mpegts(e.path):
                        continue
                    rel = Path(e.path).relative_to(root).as_posix() if not PLAT.is_windows else \
                        os.path.relpath(e.path.replace("\\\\?\\", ""), str(root)).replace("\\", "/")
                    if any(fnmatch.fnmatch(rel.lower(), p) for p in patterns if "/" in p):
                        continue
                    yield rel, st.st_size, st.st_mtime_ns

    def scan_library(self, lib: sqlite3.Row, full: bool = False) -> bool:
        root = Path(lib["path"])
        if not root.is_dir() or not os.access(root, os.R_OK):
            # unplugged drive / unmounted share / revoked permission: hide its items but never mark them missing
            self.status["errors"].append(f"Folder not available: {root}")
            if PLAT.is_termux and ("/storage" in str(root) or "storage/" in str(root)):
                self.status["errors"].append("Run `termux-setup-storage` to grant storage access")
            if not lib["offline"]:
                self.db.x("UPDATE libraries SET offline=1 WHERE id=?", (lib["id"],))
                return True
            return False
        lib_id = lib["id"]
        lib_type = lib["type"] or "mixed"
        videos_only = self.app.setting("library_mode") == "videos"
        existing = {r["rel_path"]: r for r in self.db.q(
            "SELECT id, rel_path, size, mtime_ns, missing_at, probed_at FROM items WHERE library_id=?", (lib_id,))}
        seen: set[str] = set()
        now = time.time()
        batch: list[tuple[str, tuple[Any, ...]]] = []
        to_probe: list[str] = []
        changed = False

        def flush() -> None:
            if not batch:
                return
            with self.db.tx() as conn:
                for sql, args in batch:
                    conn.execute(sql, args)
            batch.clear()

        for rel, size, mtime_ns in self._walk(root, lib_id, lib["source"] == "auto"):
            seen.add(rel)
            self.status["found"] += 1
            if self.status["found"] % 500 == 0:
                self.app.events.publish("scan.progress", {"state": "scanning", "found": self.status["found"]})
            ex = existing.get(rel)
            if ex is not None and ex["size"] == size and ex["mtime_ns"] == mtime_ns and not full:
                if ex["missing_at"] is not None:
                    batch.append(("UPDATE items SET missing_at=NULL WHERE id=?", (ex["id"],)))
                    changed = True
                if ex["probed_at"] is None:
                    to_probe.append(ex["id"])
                continue
            info = parse_name(rel, lib_type)
            kind = info["kind"]
            if videos_only:
                kind = "video"
            elif lib_type == "movies":
                kind = "movie"
            series_id = None
            if kind == "episode" and info["series"]:
                series_id = stable_id(lib_id, sort_title(info["series"]))
                batch.append(("INSERT OR IGNORE INTO series(id,title,sort_title,library_id) VALUES(?,?,?,?)",
                              (series_id, info["series"], sort_title(info["series"]), lib_id)))
            elif kind == "episode":
                kind = "movie"
            iid = stable_id(lib_id, rel)
            folder = rel.split("/")[0] if "/" in rel else ""
            title = info["title"] or Path(rel).stem
            if ex is None:
                batch.append((
                    ("INSERT OR REPLACE INTO items(id,library_id,rel_path,size,mtime_ns,kind,title,sort_title,year,"
                     "series_id,season,episode,episode_end,episode_title,folder,added_at) "
                     "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)"),
                    (iid, lib_id, rel, size, mtime_ns, kind, title, sort_title(title), info["year"], series_id,
                     info["season"], info["episode"], info["episode_end"], info["episode_title"], folder,
                     min(now, mtime_ns / 1e9) if full else now)))
            else:
                file_changed = int(ex["size"] != size or ex["mtime_ns"] != mtime_ns)
                batch.append((
                    ("UPDATE items SET size=?,mtime_ns=?,kind=?,"
                     "title=COALESCE(CASE WHEN tmdb_id IS NULL THEN ? END,title),"
                     "sort_title=COALESCE(CASE WHEN tmdb_id IS NULL THEN ? END,sort_title),year=COALESCE(year,?),"
                     "series_id=?,season=?,episode=?,episode_end=?,episode_title=?,folder=?,missing_at=NULL,"
                     "probed_at=CASE WHEN ? THEN NULL ELSE probed_at END,"
                     "unplayable=CASE WHEN ? THEN NULL ELSE unplayable END WHERE id=?"),
                    (size, mtime_ns, kind, title, sort_title(title), info["year"], series_id, info["season"],
                     info["episode"], info["episode_end"], info["episode_title"], folder, file_changed, file_changed,
                     iid)))
            if self.db.fts:
                batch.append(("DELETE FROM fts_items WHERE id=?", (iid,)))
                batch.append((("INSERT INTO fts_items(id,title,series,genres,overview) SELECT id, title, ?, "
                               "COALESCE(genres_json,''), COALESCE(overview,'') FROM items WHERE id=?"),
                              (info["series"] or "", iid)))
            if ex is None or ex["size"] != size or ex["mtime_ns"] != mtime_ns or ex["probed_at"] is None:
                to_probe.append(iid)
            changed = True
            if len(batch) >= 1000:
                flush()
        flush()
        present = sum(1 for r in existing.values() if r["missing_at"] is None)
        if not seen and present > 0:
            # an empty mount point (drive unplugged but folder still there) must not wipe the library
            self.status["errors"].append(f"{root} looks empty or disconnected — keeping its items")
            self.db.x("UPDATE libraries SET offline=1 WHERE id=?", (lib_id,))
            return True
        # missing files (a file now owned by a deeper library is adopted: watch history moves with it)
        missing = [(rel, r["id"]) for rel, r in existing.items() if rel not in seen and r["missing_at"] is None]
        if missing:
            changed = True
            with self.db.tx() as conn:
                for rel, iid in missing:
                    new_id = self._owner_of(conn, root / rel, lib_id)
                    if new_id:
                        for tbl in ("watch_state", "mylist"):
                            conn.execute(f"UPDATE OR IGNORE {tbl} SET item_id=? WHERE item_id=?", (new_id, iid))
                        conn.execute("DELETE FROM items WHERE id=?", (iid,))
                        if self.db.fts:
                            conn.execute("DELETE FROM fts_items WHERE id=?", (iid,))
                        conn.execute("DELETE FROM artwork WHERE item_id=?", (iid,))
                    else:
                        conn.execute("UPDATE items SET missing_at=? WHERE id=?", (now, iid))
        purge_days = int(self.app.setting("purge_days") or 0)
        cutoff = now - purge_days * 86400
        gone = [r[0] for r in self.db.q("SELECT id FROM items WHERE library_id=? AND missing_at IS NOT NULL AND "
                                        "missing_at<=?", (lib_id, cutoff))]
        if gone:
            with self.db.tx() as conn:
                for iid in gone:
                    conn.execute("DELETE FROM items WHERE id=?", (iid,))
                    if self.db.fts:
                        conn.execute("DELETE FROM fts_items WHERE id=?", (iid,))
                    conn.execute("DELETE FROM artwork WHERE item_id=?", (iid,))
            for iid in gone:  # generated files (watch state is kept: ids are stable if the file comes back)
                shutil.rmtree(self.app.paths.trickplay / iid, ignore_errors=True)
                for f in self.app.paths.images.glob(f"{iid}_*"):
                    with contextlib.suppress(OSError):
                        f.unlink()
        self.db.x("UPDATE libraries SET last_scan=?, offline=0 WHERE id=?", (now, lib_id))
        if lib["offline"]:
            changed = True
        for iid in to_probe:
            self.enqueue_probe(iid)
        return changed

    @staticmethod
    def _owner_of(conn: sqlite3.Connection, abs_path: Path, not_lib: int) -> str | None:
        """Id of a present item in another library that is the same file (nested library took it over)."""
        try:
            if not abs_path.exists():
                return None
        except OSError:
            return None
        for r in conn.execute("SELECT id, path FROM libraries WHERE enabled=1 AND id<>?", (not_lib,)).fetchall():
            lp = Path(r["path"])
            if abs_path.is_relative_to(lp):
                rel = abs_path.relative_to(lp).as_posix()
                row = conn.execute("SELECT id FROM items WHERE library_id=? AND rel_path=? AND missing_at IS NULL",
                                   (r["id"], rel)).fetchone()
                if row:
                    return row[0]
        return None

    def _cleanup_series(self) -> None:
        self.db.x("DELETE FROM series WHERE id NOT IN (SELECT DISTINCT series_id FROM items WHERE series_id IS NOT NULL)")

    # --- probing ----------------------------------------------------------
    def enqueue_probe(self, iid: str, prio: int = 5) -> None:
        with self._qlock:
            if iid in self._queued:
                return
            self._queued.add(iid)
        self.probe_q.put((prio, time.time(), iid))
        self.status["queue"] = self.probe_q.qsize()

    def _probe_worker(self) -> None:
        while not self._stop.is_set():
            _prio, _, iid = self.probe_q.get()
            if not iid:
                break
            with self._qlock:
                self._queued.discard(iid)
            try:
                self.probe_item(iid)
            except Exception:  # pragma: no cover
                log.exception("probe %s failed", iid)
            self.status["probed"] += 1
            self.status["queue"] = self.probe_q.qsize()
            if self.probe_q.qsize() == 0:
                self.app.events.publish("library.updated", {"probed": True})
                self.app.events.publish("scan.progress", dict(self.status))

    def probe_item(self, iid: str) -> None:
        row = self.db.one("SELECT i.*, l.path AS lib_path FROM items i JOIN libraries l ON l.id=i.library_id "
                          "WHERE i.id=?", (iid,))
        if row is None:
            return
        path = safe_join(Path(row["lib_path"]), row["rel_path"])
        if path is None or not path.exists():
            return
        ext_subs = find_external_subs(path)
        info: dict[str, Any] | None = None
        err: str | None = None
        if self.app.ff.available:
            try:
                info = probe_file(self.app.ff, path)
            except Exception as e:  # noqa: BLE001
                err = str(e)
        else:
            info = {"duration": 0, "container": path.suffix.lower().lstrip(".").replace("m4v", "mp4"),
                    "bitrate": 0, "start_time": 0, "video": None, "audio": [], "subs": [], "chapters": []}
        faststart = mp4_faststart(path) if path.suffix.lower() in (".mp4", ".m4v", ".mov") else None
        with self.db.tx() as conn:
            conn.execute("DELETE FROM streams WHERE item_id=?", (iid,))
            conn.execute("DELETE FROM chapters WHERE item_id=?", (iid,))
            if info is None:
                conn.execute("UPDATE items SET probed_at=?, unplayable=? WHERE id=?", (time.time(), err, iid))
            else:
                v = info.get("video") or {}
                conn.execute(
                    "UPDATE items SET duration_s=?,container=?,bitrate=?,v_codec=?,v_profile=?,v_level=?,v_pixfmt=?,"
                    "v_w=?,v_h=?,v_fps=?,v_bitdepth=?,v_rotation=?,hdr=?,faststart=?,start_time=?,probe_json=?,"
                    "probed_at=?,unplayable=NULL,subs_external=? WHERE id=?",
                    (info["duration"], info["container"], info["bitrate"], v.get("codec"), v.get("profile"),
                     v.get("level"), v.get("pix_fmt"), v.get("width"), v.get("height"), v.get("fps"),
                     v.get("bitdepth"), v.get("rotation"), v.get("hdr") or None,
                     None if faststart is None else int(faststart), info.get("start_time") or 0,
                     json.dumps(info), time.time(), len(ext_subs), iid))
                for a in info["audio"]:
                    conn.execute("INSERT INTO streams(item_id,idx,sidx,type,codec,profile,lang,title,channels,layout,"
                                 "default_flag,forced_flag,bitmap_flag) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,0)",
                                 (iid, a["index"], a["sidx"], "a", a["codec"], a["profile"], a["lang"], a["title"],
                                  a["channels"], a["layout"], int(a["default"]), int(a["forced"])))
                n = 0
                for s in info["subs"]:
                    conn.execute("INSERT INTO streams(item_id,idx,sidx,type,codec,lang,title,default_flag,forced_flag,"
                                 "bitmap_flag) VALUES(?,?,?,?,?,?,?,?,?,?)",
                                 (iid, s["index"], n, "s", s["codec"], s["lang"], s["title"], int(s["default"]),
                                  int(s["forced"]), int(s["bitmap"])))
                    n += 1
                for x in ext_subs:
                    title = " ".join(b for b in (x["title"], "SDH" if x["sdh"] else "") if b)
                    conn.execute("INSERT INTO streams(item_id,idx,sidx,type,codec,lang,title,default_flag,forced_flag,"
                                 "bitmap_flag,external_path) VALUES(?,?,?,?,?,?,?,0,?,0,?)",
                                 (iid, -1, n, "s", x["codec"], x["lang"], title, int(x["forced"]), x["path"]))
                    n += 1
                for ch in info["chapters"]:
                    conn.execute("INSERT INTO chapters(item_id,idx,start_s,end_s,title) VALUES(?,?,?,?,?)",
                                 (iid, ch["idx"], ch["start"], ch["end"], ch["title"]))
        if info is not None:
            self.app.artwork.enqueue(iid, prio=6)
            if self.app.tmdb.enabled:
                self.app.tmdb.enqueue(iid)
            # warm the keyframe table for files that will likely be remuxed
            if self.app.ff.available and info.get("video") and info["container"] not in ("mp4", "mov", "webm"):
                self.warm_keyframes(iid, path)

    def warm_keyframes(self, iid: str, path: Path, prio: int = 5) -> None:
        """Queue a keyframe-table build (one ffprobe at a time). prio 0 = the user is about to play it."""
        try:
            st = path.stat()
        except OSError:
            return
        cache = self.app.paths.keyframes / f"{stable_id(iid, st.st_size, st.st_mtime_ns)}.json"
        if cache.exists():
            return
        with self._qlock:
            cur = self._kf_prio.get(iid)
            if cur is not None and cur <= prio:
                return
            self._kf_prio[iid] = prio
            self._kf_running.add(iid)
            if self._kf_thread is None:
                self._kf_thread = threading.Thread(target=self._kf_loop, name="keyframes", daemon=True)
                self._kf_thread.start()
        if prio == 0:  # the user is waiting: don't queue behind a long background scan
            threading.Thread(target=self._kf_one, args=(iid, path, cache), name="keyframes-now", daemon=True).start()
            return
        self._kf_q.put((prio, time.time(), iid, path, cache))

    def _kf_one(self, iid: str, path: Path, cache: Path) -> None:
        try:
            if not cache.exists():
                keyframe_table(self.app.ff, path, cache)
        except Exception as e:  # noqa: BLE001
            log.debug("keyframe build %s failed: %s", iid, e)
        finally:
            with self._qlock:
                self._kf_running.discard(iid)
                self._kf_prio.pop(iid, None)

    def _kf_loop(self) -> None:
        while not self._stop.is_set():
            try:
                prio, ts, iid, path, cache = self._kf_q.get(timeout=5)
            except queue.Empty:
                continue
            if cache.exists():
                with self._qlock:
                    self._kf_running.discard(iid)
                    self._kf_prio.pop(iid, None)
                continue
            if prio > 1 and self.app.setting("pause_bg_while_streaming") and self.app.hls.active_streams() > 0:
                self._kf_q.put((prio, ts, iid, path, cache))  # background work waits; user-requested work doesn't
                self._stop.wait(2)
                continue
            try:
                if not cache.exists():
                    keyframe_table(self.app.ff, path, cache)
            except Exception as e:  # noqa: BLE001
                log.debug("keyframe warmup %s failed: %s", iid, e)
            finally:
                with self._qlock:
                    self._kf_running.discard(iid)
                    self._kf_prio.pop(iid, None)


# ===== SECTION: artwork =====
class ArtworkWorker:
    """Local artwork discovery + generated thumbnails/backdrops + trickplay sprites (§6.4)."""

    WIDTHS = (240, 320, 480, 640, 960, 1280, 1920)

    def __init__(self, app: App) -> None:
        self.app = app
        self.db = app.db
        self.q: queue.PriorityQueue[tuple[int, float, str, str]] = queue.PriorityQueue()
        self.tq: queue.PriorityQueue[tuple[int, float, str, str]] = queue.PriorityQueue()  # trickplay (slow)
        self._queued: set[tuple[str, str]] = set()
        self._prio: dict[tuple[str, str], int] = {}
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self.resize_sem = threading.Semaphore(2)
        self.trick_status: dict[str, str] = {}

    def start(self) -> None:
        threading.Thread(target=self._loop, args=(self.q,), name="artwork", daemon=True).start()
        threading.Thread(target=self._loop, args=(self.tq,), name="trickplay", daemon=True).start()

    def stop(self) -> None:
        self._stop.set()
        self.q.put((99, 0, "", ""))
        self.tq.put((99, 0, "", ""))

    def enqueue(self, iid: str, prio: int = 6, job: str = "art") -> None:
        """Queue artwork work; a more urgent request (e.g. a card on screen) jumps ahead of the backlog."""
        with self._lock:
            cur = self._prio.get((iid, job))
            if cur is not None and cur <= prio:
                return
            self._prio[(iid, job)] = prio
            self._queued.add((iid, job))
        (self.tq if job == "trick" else self.q).put((prio, time.time(), iid, job))

    def _loop(self, q: queue.PriorityQueue[tuple[int, float, str, str]]) -> None:
        while not self._stop.is_set():
            prio, ts, iid, job = q.get()
            if not iid:
                break
            # pause low-priority background work while streams are active (setting); urgent jobs still run
            if prio > 3 and self.app.setting("pause_bg_while_streaming") and self.app.hls.active_streams() > 0:
                q.put((prio, ts, iid, job))
                self._stop.wait(2)
                continue
            with self._lock:
                if (iid, job) not in self._queued:
                    continue  # stale duplicate: a higher-priority copy already ran
                self._queued.discard((iid, job))
                self._prio.pop((iid, job), None)
            try:
                if job == "art":
                    self.process(iid)
                elif job == "trick":
                    self.make_trickplay(iid)
            except Exception as e:  # pragma: no cover  # noqa: BLE001
                log.warning("artwork %s/%s failed: %s", iid, job, e)

    def _item_path(self, iid: str) -> tuple[sqlite3.Row | None, Path | None]:
        row = self.db.one("SELECT i.*, l.path AS lib_path FROM items i JOIN libraries l ON l.id=i.library_id "
                          "WHERE i.id=?", (iid,))
        if row is None:
            return None, None
        return row, safe_join(Path(row["lib_path"]), row["rel_path"])

    def set_art(self, iid: str, kind: str, path: str, source: str) -> None:
        self.db.x("INSERT OR REPLACE INTO artwork(item_id,kind,path,source,updated_at) VALUES(?,?,?,?,?)",
                  (iid, kind, path, source, time.time()))

    def get_art(self, iid: str, kind: str) -> sqlite3.Row | None:
        return self.db.one("SELECT * FROM artwork WHERE item_id=? AND kind=?", (iid, kind))

    @staticmethod
    def _find_local(folder: Path, names: list[str]) -> Path | None:
        try:
            files = {e.name.lower(): Path(e.path) for e in os.scandir(folder) if e.is_file()}
        except OSError:
            return None
        for n in names:
            for ext in (".jpg", ".jpeg", ".png", ".webp"):
                p = files.get((n + ext).lower())
                if p:
                    return p
        return None

    def process(self, iid: str) -> None:
        row, path = self._item_path(iid)
        if row is None or path is None or not path.exists():
            return
        prefer_local = self.app.setting("prefer_local_art")
        stem = path.stem
        folder = path.parent
        if prefer_local:
            poster = self._find_local(folder, [f"{stem}-poster", f"{stem}.poster", stem] +
                                      (["poster", "folder", "cover"] if row["kind"] == "movie" else []))
            backdrop = self._find_local(folder, [f"{stem}-fanart", f"{stem}-backdrop", f"{stem}.fanart"] +
                                        (["fanart", "backdrop", "background"] if row["kind"] == "movie" else []))
            thumb = self._find_local(folder, [f"{stem}-thumb", f"{stem}.thumb"])
            if poster:
                self.set_art(iid, "poster", str(poster), "local")
            if backdrop:
                self.set_art(iid, "backdrop", str(backdrop), "local")
            if thumb:
                self.set_art(iid, "thumb", str(thumb), "local")
            if row["series_id"]:
                show_dir = folder.parent if _SEASON_DIR_RE.match(folder.name) else folder
                sp = self._find_local(show_dir, ["poster", "folder", "cover", "show"])
                sb = self._find_local(show_dir, ["fanart", "backdrop", "background"])
                if sp and not self.get_art(row["series_id"], "poster"):
                    self.set_art(row["series_id"], "poster", str(sp), "local")
                if sb and not self.get_art(row["series_id"], "backdrop"):
                    self.set_art(row["series_id"], "backdrop", str(sb), "local")
        if not self.app.setting("generate_thumbs") or not self.app.ff.available or not row["v_codec"]:
            return
        dur = row["duration_s"] or 0
        missing = [k for k in ("thumb", "backdrop")
                   if not ((ex := self.get_art(iid, k)) is not None and ex["path"] and os.path.exists(ex["path"]))]
        if missing:
            # one well-chosen 1280px frame serves as both thumbnail and backdrop (resized per request)
            out = self.generate_frame(path, dur, 0.15, 1280, f"{iid}_frame", row["v_rotation"] or 0,
                                      hdr=bool(row["hdr"]))
            if out:
                for k in missing:
                    self.set_art(iid, k, str(out), "generated")
        if row["series_id"] and not self.get_art(row["series_id"], "backdrop"):
            b = self.get_art(iid, "backdrop")
            if b:
                self.set_art(row["series_id"], "backdrop", b["path"], "generated")
        if row["series_id"] and not self.get_art(row["series_id"], "thumb"):
            t = self.get_art(iid, "thumb")
            if t:
                self.set_art(row["series_id"], "thumb", t["path"], "generated")
        self.app.events.publish("artwork.updated", {"id": iid, "seriesId": row["series_id"]})

    def _luma_score(self, path: Path, t: float) -> float:
        ff = self.app.ff
        rc, out, _ = run_capture([ff.ffmpeg or "ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-ss",
                                  f"{t:.2f}", "-i", long_path(path), "-frames:v", "1", "-vf", "scale=32:18,format=gray",
                                  "-f", "rawvideo", "-"], timeout=30, low_priority=True)
        if rc != 0 or not out:
            return -1.0
        vals = list(out)
        mean = sum(vals) / len(vals)
        var = sum((x - mean) ** 2 for x in vals) / len(vals)
        if mean < 18 or mean > 240:
            return var * 0.05
        return var + mean * 0.5

    def generate_frame(self, path: Path, dur: float, frac: float, width: int, name: str, rotation: int = 0,
                       hdr: bool = False) -> Path | None:
        ff = self.app.ff
        if not ff.ffmpeg:
            return None
        cands = [dur * frac, dur * (frac + 0.12), dur * (frac + 0.25)] if dur > 20 else [min(1.0, dur / 2)]
        best_t, best_s = cands[0], -2.0
        if len(cands) > 1:
            for t in cands:
                s_ = self._luma_score(path, t)
                if s_ > best_s:
                    best_t, best_s = t, s_
        out = self.app.paths.images / f"{name}.jpg"  # JPEG works everywhere; WebP variants come from image_for
        vf = f"scale='min({width},iw)':-2"
        if "tonemap" in ff.filters and "zscale" in ff.filters:
            vf = ("zscale=t=linear:npl=100,format=gbrpf32le,zscale=p=bt709,tonemap=tonemap=hable:desat=0,"
                  "zscale=t=bt709:m=bt709:r=tv,format=yuv420p," + vf) if hdr else vf
        args = [ff.ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "error", "-y", "-ss", f"{best_t:.2f}", "-i",
                long_path(path), "-frames:v", "1", "-vf", vf, "-q:v", "3", str(out)]
        rc, _, err = run_capture(args, timeout=90, low_priority=True)
        if (rc != 0 or not out.exists()) and best_t > 0:  # some files can't seek: grab an early frame instead
            args[args.index("-ss") + 1] = "0"
            rc, _, err = run_capture(args, timeout=90, low_priority=True)
        if rc != 0 or not out.exists():
            log.info("thumbnail failed for %s: %s", path.name, err.decode(errors="replace")[-200:])
            return None
        return out

    def image_for(self, oid: str, kind: str, width: int | None, webp_ok: bool) -> Path | None:
        """Resolve (and lazily resize) an artwork file for HTTP serving."""
        order = {"poster": ["poster", "thumb", "backdrop"], "backdrop": ["backdrop", "thumb", "poster"],
                 "thumb": ["thumb", "backdrop", "poster"]}.get(kind, [kind])
        art = None
        for k in order:
            art = self.get_art(oid, k)
            if art is not None and art["path"] and os.path.exists(art["path"]):
                break
            art = None
        if art is None:
            if self.db.one("SELECT 1 FROM items WHERE id=?", (oid,)):
                self.enqueue(oid, prio=3)
                return None
            # a series without its own artwork: borrow the first episode's frame (and make one if needed)
            eps = self.db.q("SELECT id FROM items WHERE series_id=? AND missing_at IS NULL "
                            "ORDER BY season, episode LIMIT 3", (oid,))
            for e in eps:
                for k in order:
                    a = self.get_art(e["id"], k)
                    if a is not None and a["path"] and os.path.exists(a["path"]):
                        art = a
                        break
                if art is not None:
                    break
            if art is None:
                if eps:
                    self.enqueue(eps[0]["id"], prio=3)
                return None
        src = Path(art["path"])
        if src.suffix.lower() == ".webp" and not webp_ok:
            j = src.with_suffix(".jpg")
            if j.exists():
                src = j
        if not width or not self.app.ff.ffmpeg:
            return src
        w = min(self.WIDTHS, key=lambda x: abs(x - width))
        if art["source"] == "generated" and w >= 960:
            return src
        ext = ".webp" if webp_ok and "libwebp" in self.app.ff.encoders else ".jpg"
        h = stable_id(str(src), src.stat().st_mtime_ns, w)
        out = self.app.paths.images / "resized" / f"{h}{ext}"
        if out.exists():
            return out
        out.parent.mkdir(parents=True, exist_ok=True)
        with self.resize_sem:
            if out.exists():
                return out
            tmp = out.with_name(f"{out.stem}.{threading.get_ident()}.tmp{ext}")
            args = [self.app.ff.ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "error", "-y", "-i", str(src),
                    "-vf", f"scale='min({w},iw)':-2", "-frames:v", "1"]
            args += ["-c:v", "libwebp", "-quality", "80"] if ext == ".webp" else ["-q:v", "3"]
            rc, _, _ = run_capture(args + [str(tmp)], timeout=20)
            if rc == 0 and tmp.exists():
                try:
                    os.replace(tmp, out)
                except OSError:
                    with contextlib.suppress(OSError):
                        tmp.unlink()
                return out if out.exists() else src
        return src

    # --- trickplay ----------------------------------------------------------
    def trick_dir(self, iid: str) -> Path:
        return self.app.paths.trickplay / iid

    def trick_index(self, iid: str) -> dict[str, Any] | None:
        p = self.trick_dir(iid) / "index.json"
        if p.exists():
            with contextlib.suppress(ValueError, OSError):
                return json.loads(p.read_text())
        return None

    def make_trickplay(self, iid: str) -> None:
        if self.trick_index(iid) is not None or not self.app.ff.ffmpeg:
            return
        row, path = self._item_path(iid)
        if row is None or path is None or not row["v_codec"]:
            return
        dur = row["duration_s"] or 0
        if dur < 30:
            return
        interval = 10 if dur < 4 * 3600 else 20
        d = self.trick_dir(iid)
        d.mkdir(parents=True, exist_ok=True)
        self.trick_status[iid] = "running"
        vf = (f"fps=1/{interval},scale=320:180:force_original_aspect_ratio=decrease,"
              f"pad=320:180:(ow-iw)/2:(oh-ih)/2,tile=10x10")
        args = [self.app.ff.ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "error", "-y", "-skip_frame", "nokey",
                "-i", long_path(path), "-an", "-sn", "-vf", vf, "-q:v", "6", str(d / "sheet_%03d.jpg")]
        threads = 1 if PLAT.is_termux else 2
        args[1:1] = ["-threads", str(threads)]
        rc, _, err = run_capture(args, timeout=max(600, dur / 2), low_priority=True)
        sheets = sorted(d.glob("sheet_*.jpg"))
        if rc != 0 or not sheets:
            log.debug("trickplay failed %s: %s", iid, err[-300:])
            self.trick_status[iid] = "failed"
            return
        idx = {"interval": interval, "tile": [10, 10], "w": 320, "h": 180, "sheets": len(sheets),
               "count": int(dur // interval) + 1}
        (d / "index.json").write_text(json.dumps(idx))
        self.trick_status[iid] = "done"
        self.app.events.publish("trickplay.ready", {"id": iid})


# ===== SECTION: tmdb =====
class Tmdb:
    """Optional TMDB metadata (only when a key is configured)."""

    BASE = "https://api.themoviedb.org/3"
    IMG = "https://image.tmdb.org/t/p/"

    def __init__(self, app: App) -> None:
        self.app = app
        self.q: queue.Queue[str] = queue.Queue()
        self._last = 0.0
        self._started = False
        self._start_lock = threading.Lock()

    @property
    def key(self) -> str:
        return str(self.app.setting("tmdb_key") or "")

    @property
    def enabled(self) -> bool:
        return bool(self.key)

    def enqueue(self, iid: str) -> None:
        with self._start_lock:
            if not self._started:
                self._started = True
                threading.Thread(target=self._loop, name="tmdb", daemon=True).start()
        self.q.put(iid)

    def _get(self, path: str, params: dict[str, Any]) -> dict[str, Any] | None:
        params = dict(params, language=self.app.setting("tmdb_language") or "en-US")
        key = self.key
        cache_key = hashlib.sha1((path + json.dumps(params, sort_keys=True)).encode()).hexdigest()
        cp = self.app.paths.tmdb / f"{cache_key}.json"
        if cp.exists():
            with contextlib.suppress(ValueError, OSError):
                return json.loads(cp.read_text(encoding="utf-8"))
        wait = 0.3 - (time.time() - self._last)
        if wait > 0:
            time.sleep(wait)
        self._last = time.time()
        headers = {"Accept": "application/json", "User-Agent": f"LocalFlix/{VERSION}"}
        if key.startswith("ey"):  # v4 read token
            headers["Authorization"] = f"Bearer {key}"
        else:
            params["api_key"] = key
        url = f"{self.BASE}{path}?{urllib.parse.urlencode(params)}"
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=15) as r:
                data = json.loads(r.read().decode("utf-8"))
        except (urllib.error.URLError, OSError, ValueError) as e:
            log.info("TMDB request failed: %s", e)
            return None
        with contextlib.suppress(OSError):
            cp.write_text(json.dumps(data), encoding="utf-8")
        return data

    def _download(self, rel: str | None, size: str, name: str) -> str | None:
        if not rel:
            return None
        out = self.app.paths.images / f"tmdb_{name}{Path(rel).suffix or '.jpg'}"
        if out.exists():
            return str(out)
        try:
            with urllib.request.urlopen(self.IMG + size + rel, timeout=20) as r:
                out.write_bytes(r.read())
            return str(out)
        except (urllib.error.URLError, OSError):
            return None

    def _loop(self) -> None:
        while True:
            iid = self.q.get()
            if not self.enabled:
                continue
            try:
                self.process(iid)
            except Exception as e:  # pragma: no cover  # noqa: BLE001
                log.info("TMDB %s: %s", iid, e)

    def process(self, iid: str) -> None:
        db = self.app.db
        row = db.one("SELECT * FROM items WHERE id=?", (iid,))
        if row is None:
            return
        art = self.app.artwork
        prefer_local = self.app.setting("prefer_local_art")
        if row["kind"] == "episode" and row["series_id"]:
            s = db.one("SELECT * FROM series WHERE id=?", (row["series_id"],))
            if s is None:
                return
            if s["tmdb_id"] is None:
                res = self._get("/search/tv", {"query": s["title"]})
                if not res or not res.get("results"):
                    return
                hit = res["results"][0]
                det = self._get(f"/tv/{hit['id']}", {"append_to_response": "content_ratings,credits"}) or hit
                genres = [g["name"] for g in det.get("genres", [])]
                cr = ""
                for x in (det.get("content_ratings") or {}).get("results", []):
                    if x.get("iso_3166_1") == "US":
                        cr = x.get("rating", "")
                poster = self._download(det.get("poster_path"), "w500", f"s{s['id']}_p")
                backdrop = self._download(det.get("backdrop_path"), "w1280", f"s{s['id']}_b")
                yr = (det.get("first_air_date") or "")[:4]
                db.x("UPDATE series SET tmdb_id=?, overview=?, genres_json=?, rating=?, year=? WHERE id=?",
                     (hit["id"], det.get("overview"), json.dumps(genres), det.get("vote_average"),
                      int(yr) if yr.isdigit() else None, s["id"]))
                db.x("UPDATE items SET content_rating=?, genres_json=? WHERE series_id=?", (cr, json.dumps(genres), s["id"]))
                for kind, p in (("poster", poster), ("backdrop", backdrop)):
                    if p and (not prefer_local or not art.get_art(s["id"], kind) or
                              art.get_art(s["id"], kind)["source"] != "local"):
                        art.set_art(s["id"], kind, p, "tmdb")
                s = db.one("SELECT * FROM series WHERE id=?", (row["series_id"],))
            if s and s["tmdb_id"] and row["season"] is not None and row["episode"]:
                ep = self._get(f"/tv/{s['tmdb_id']}/season/{row['season']}/episode/{row['episode']}", {})
                if ep and ep.get("name"):
                    db.x("UPDATE items SET title=?, episode_title=?, overview=?, rating=? WHERE id=?",
                         (ep["name"], ep["name"], ep.get("overview"), ep.get("vote_average"), iid))
        else:
            params: dict[str, Any] = {"query": row["title"]}
            if row["year"]:
                params["year"] = row["year"]
            res = self._get("/search/movie", params)
            if not res or not res.get("results"):
                return
            hit = res["results"][0]
            det = self._get(f"/movie/{hit['id']}", {"append_to_response": "credits,release_dates"}) or hit
            genres = [g["name"] for g in det.get("genres", [])]
            cast = [c_["name"] for c_ in (det.get("credits") or {}).get("cast", [])[:5]]
            cr = ""
            for x in (det.get("release_dates") or {}).get("results", []):
                if x.get("iso_3166_1") == "US":
                    for rd in x.get("release_dates", []):
                        if rd.get("certification"):
                            cr = rd["certification"]
                            break
            yr = (det.get("release_date") or "")[:4]
            db.x("UPDATE items SET tmdb_id=?, title=?, sort_title=?, year=?, overview=?, genres_json=?, rating=?, "
                 "content_rating=?, cast_json=? WHERE id=?",
                 (hit["id"], det.get("title") or row["title"], sort_title(det.get("title") or row["title"]),
                  int(yr) if yr.isdigit() else row["year"], det.get("overview"), json.dumps(genres),
                  det.get("vote_average"), cr, json.dumps(cast), iid))
            for kind, rel, size in (("poster", det.get("poster_path"), "w500"),
                                    ("backdrop", det.get("backdrop_path"), "w1280")):
                p = self._download(rel, size, f"{iid}_{kind[0]}")
                ex = art.get_art(iid, kind)
                if p and (not prefer_local or ex is None or ex["source"] != "local"):
                    art.set_art(iid, kind, p, "tmdb")
        r2 = db.one("SELECT i.title, s.title AS st, i.genres_json, i.overview FROM items i LEFT JOIN series s "
                    "ON s.id=i.series_id WHERE i.id=?", (iid,))
        if db.fts and r2:
            with db.tx() as conn:
                conn.execute("DELETE FROM fts_items WHERE id=?", (iid,))
                conn.execute("INSERT INTO fts_items(id,title,series,genres,overview) VALUES(?,?,?,?,?)",
                             (iid, r2["title"], r2["st"] or "", " ".join(json.loads(r2["genres_json"] or "[]")),
                              r2["overview"] or ""))
        self.app.events.publish("library.updated", {"id": iid})


# ===== SECTION: library-queries =====
RATING_ORDER = {"G": 0, "TV-Y": 0, "TV-Y7": 0, "TV-G": 0, "PG": 1, "TV-PG": 1, "PG-13": 2, "TV-14": 2, "R": 3,
                "TV-MA": 3, "NC-17": 4}


def res_label(w: int | None, h: int | None) -> str:
    if not h:
        return ""
    w = w or 0
    if h >= 2000 or w >= 3800:
        return "4K"
    if h >= 1000 or w >= 1900:
        return "1080p"
    if h >= 700 or w >= 1260:
        return "720p"
    if h >= 560:
        return "576p"
    if h >= 470:
        return "480p"
    return "SD"


class Library:
    """Read-side queries used by the HTTP API."""

    ITEM_COLS = ("i.id, i.kind, i.title, i.year, i.series_id, i.season, i.episode, i.episode_end, i.duration_s, "
                 "i.v_w, i.v_h, i.hdr, i.added_at, i.overview, i.genres_json, i.rating, i.content_rating, i.folder, "
                 "i.rel_path, i.v_codec, i.unplayable, i.probed_at, s.title AS series_title, "
                 "(SELECT COUNT(*) FROM streams x WHERE x.item_id=i.id AND x.type='a') AS n_audio, "
                 "(SELECT COUNT(*) FROM streams x WHERE x.item_id=i.id AND x.type='s') AS n_subs")

    def __init__(self, app: App) -> None:
        self.app = app
        self.db = app.db

    def _kids_clause(self, profile: sqlite3.Row | None) -> tuple[str, list[Any]]:
        if profile is None or not profile["kids"]:
            return "", []
        ceiling = RATING_ORDER.get(str(self.app.setting("kids_max_rating")), 1)
        bad = [k for k, v in RATING_ORDER.items() if v > ceiling]
        marks = ",".join("?" * len(bad))
        return f" AND (i.content_rating IS NULL OR i.content_rating='' OR i.content_rating NOT IN ({marks}))", bad

    def card(self, r: sqlite3.Row, ws: sqlite3.Row | None = None, mylist: set[str] | None = None) -> dict[str, Any]:
        d: dict[str, Any] = {
            "id": r["id"], "kind": r["kind"], "title": r["title"], "year": r["year"], "seriesId": r["series_id"],
            "seriesTitle": r["series_title"], "season": r["season"], "episode": r["episode"],
            "episodeEnd": r["episode_end"], "duration": r["duration_s"] or 0, "res": res_label(r["v_w"], r["v_h"]),
            "hdr": r["hdr"] or "", "audioCount": r["n_audio"], "subCount": r["n_subs"],
            "addedAt": r["added_at"], "isNew": (time.time() - (r["added_at"] or 0)) < 14 * 86400,
            "overview": r["overview"] or "", "genres": json.loads(r["genres_json"] or "[]"), "rating": r["rating"],
            "contentRating": r["content_rating"] or "", "folder": r["folder"] or "",
            "thumb": f"/api/v1/images/{r['id']}/thumb", "backdrop": f"/api/v1/images/{r['id']}/backdrop",
            "poster": f"/api/v1/images/{r['id']}/poster", "unplayable": r["unplayable"],
            "probed": r["probed_at"] is not None,
        }
        if ws is not None:
            d["position"] = ws["position_s"] or 0
            d["watched"] = bool(ws["watched"])
            d["lastPlayed"] = ws["last_played_at"]
        else:
            d["position"], d["watched"] = 0, False
        if mylist is not None:
            d["inList"] = r["id"] in mylist
        return d

    def _ws_map(self, pid: int, ids: list[str]) -> dict[str, sqlite3.Row]:
        if not ids:
            return {}
        out: dict[str, sqlite3.Row] = {}
        for i in range(0, len(ids), 500):
            chunk = ids[i:i + 500]
            marks = ",".join("?" * len(chunk))
            for r in self.db.q(f"SELECT * FROM watch_state WHERE profile_id=? AND item_id IN ({marks})", [pid, *chunk]):
                out[r["item_id"]] = r
        return out

    def _mylist(self, pid: int) -> set[str]:
        return {r[0] for r in self.db.q("SELECT item_id FROM mylist WHERE profile_id=?", (pid,))}

    def cards(self, rows: list[sqlite3.Row], pid: int) -> list[dict[str, Any]]:
        ws = self._ws_map(pid, [r["id"] for r in rows])
        ml = self._mylist(pid)
        return [self.card(r, ws.get(r["id"]), ml) for r in rows]

    def series_card(self, s: sqlite3.Row, pid: int) -> dict[str, Any]:
        stats = self.db.one("SELECT COUNT(*) AS n, COUNT(DISTINCT season) AS ns, MAX(added_at) AS added, "
                            "MAX(v_h) AS h, MAX(v_w) AS w, MAX(hdr) AS hdr FROM items WHERE series_id=? AND missing_at IS NULL",
                            (s["id"],))
        watched = self.db.one("SELECT COUNT(*) FROM watch_state w JOIN items i ON i.id=w.item_id WHERE i.series_id=? "
                              "AND w.profile_id=? AND w.watched=1", (s["id"], pid))[0]
        return {"id": s["id"], "kind": "series", "title": s["title"], "year": s["year"], "overview": s["overview"] or "",
                "genres": json.loads(s["genres_json"] or "[]"), "rating": s["rating"], "episodeCount": stats["n"],
                "seasonCount": stats["ns"], "addedAt": stats["added"],
                "isNew": (time.time() - (stats["added"] or 0)) < 14 * 86400, "res": res_label(stats["w"], stats["h"]),
                "hdr": stats["hdr"] or "", "watchedCount": watched,
                "thumb": f"/api/v1/images/{s['id']}/thumb", "backdrop": f"/api/v1/images/{s['id']}/backdrop",
                "poster": f"/api/v1/images/{s['id']}/poster", "inList": s["id"] in self._mylist(pid)}

    def base_where(self, profile: sqlite3.Row | None) -> tuple[str, list[Any]]:
        kc, ka = self._kids_clause(profile)
        return ("i.missing_at IS NULL AND i.library_id IN (SELECT id FROM libraries WHERE enabled=1 AND offline=0)"
                + kc), ka

    def items(self, profile: sqlite3.Row, params: dict[str, str]) -> dict[str, Any]:
        pid = profile["id"]
        typ = params.get("type", "")
        limit = max(1, min(200, int(params.get("limit") or 60)))
        offset = max(0, int(params.get("cursor") or 0))
        sort = params.get("sort", "added")
        order = "ASC" if params.get("order", "").lower() == "asc" else "DESC"
        if sort == "title" and "order" not in params:
            order = "ASC"
        if typ == "series":
            q = (params.get("q") or "").strip()
            bw, bargs = self.base_where(profile)
            where, args = f"id IN (SELECT i.series_id FROM items i WHERE {bw})", list(bargs)
            if q:
                where += " AND title LIKE ?"
                args.append(f"%{q}%")
            latest = "(SELECT MAX(added_at) FROM items WHERE series_id=series.id)"
            col = {"title": "sort_title", "year": "year", "rating": "rating"}.get(sort, latest)
            rows = self.db.q(f"SELECT * FROM series WHERE {where} ORDER BY {col} {order} LIMIT ? OFFSET ?",
                             args + [limit + 1, offset])
            more = len(rows) > limit
            return {"items": [self.series_card(s, pid) for s in rows[:limit]],
                    "next": str(offset + limit) if more else None}
        where, args = self.base_where(profile)
        if typ in ("movie", "episode", "video"):
            where += " AND i.kind=?"
            args.append(typ)
        elif typ == "movies":
            where += " AND i.kind IN ('movie','video')"
        q = (params.get("q") or "").strip()
        if q:
            ids = self.search_ids(q, 500)
            if not ids:
                return {"items": [], "next": None}
            where += " AND i.id IN ({})".format(",".join("?" * len(ids)))
            args += ids
        if params.get("genre"):
            where += " AND (i.genres_json LIKE ? OR s.genres_json LIKE ?)"
            args += [f'%"{params["genre"]}"%'] * 2
        if params.get("folder"):
            where += " AND i.folder=?"
            args.append(params["folder"])
        if params.get("series"):
            where += " AND i.series_id=?"
            args.append(params["series"])
        if params.get("unwatched") in ("1", "true"):
            where += " AND i.id NOT IN (SELECT item_id FROM watch_state WHERE profile_id=? AND watched=1)"
            args.append(pid)
        if params.get("watched") in ("1", "true"):
            where += " AND i.id IN (SELECT item_id FROM watch_state WHERE profile_id=? AND watched=1)"
            args.append(pid)
        if params.get("multiaudio") in ("1", "true"):
            where += " AND (SELECT COUNT(*) FROM streams x WHERE x.item_id=i.id AND x.type='a')>1"
        if params.get("hdr") in ("1", "true"):
            where += " AND i.hdr IS NOT NULL AND i.hdr<>''"
        resf = params.get("res")
        if resf == "4K":
            where += " AND (i.v_h>=2000 OR i.v_w>=3800)"
        elif resf == "1080p":
            where += " AND (i.v_h>=1000 OR i.v_w>=1900) AND i.v_h<2000"
        elif resf == "720p":
            where += " AND (i.v_h>=700 OR i.v_w>=1260) AND i.v_h<1000"
        col = {"title": "i.sort_title", "year": "i.year", "duration": "i.duration_s", "rating": "i.rating",
               "added": "i.added_at", "episode": "i.season, i.episode"}.get(sort, "i.added_at")
        if sort == "episode":
            order_sql = "i.season ASC, i.episode ASC"
        else:
            order_sql = f"{col} {order}, i.sort_title ASC"
        rows = self.db.q(f"SELECT {self.ITEM_COLS} FROM items i LEFT JOIN series s ON s.id=i.series_id WHERE {where} "
                         f"ORDER BY {order_sql} LIMIT ? OFFSET ?", args + [limit + 1, offset])
        more = len(rows) > limit
        total = self.db.one(f"SELECT COUNT(*) FROM items i LEFT JOIN series s ON s.id=i.series_id WHERE {where}", args)[0] \
            if offset == 0 else None
        return {"items": self.cards(rows[:limit], pid), "next": str(offset + limit) if more else None, "total": total}

    def search_ids(self, q: str, limit: int = 100) -> list[str]:
        q = q.strip()[:100]
        if not q:
            return []
        if self.db.fts:
            toks = [t for t in re.findall(r"\w+", q, re.UNICODE)][:8]
            if toks:
                expr = " ".join('"{}"*'.format(t.replace('"', "")) for t in toks)
                try:
                    return [r[0] for r in self.db.q("SELECT id FROM fts_items WHERE fts_items MATCH ? ORDER BY rank "
                                                    "LIMIT ?", (expr, limit))]
                except sqlite3.Error:
                    pass
        like = f"%{q}%"
        return [r[0] for r in self.db.q(
            "SELECT i.id FROM items i LEFT JOIN series s ON s.id=i.series_id WHERE i.title LIKE ? OR s.title LIKE ? "
            "OR i.rel_path LIKE ? LIMIT ?", (like, like, like, limit))]

    def search(self, profile: sqlite3.Row, q: str) -> dict[str, Any]:
        pid = profile["id"]
        ids = self.search_ids(q, 120)
        where, args = self.base_where(profile)
        items: list[dict[str, Any]] = []
        if ids:
            rows = self.db.q(f"SELECT {self.ITEM_COLS} FROM items i LEFT JOIN series s ON s.id=i.series_id WHERE {where} "
                             f"AND i.id IN (%s)" % ",".join("?" * len(ids)), args + ids)
            order = {iid: n for n, iid in enumerate(ids)}
            rows = sorted(rows, key=lambda r: order.get(r["id"], 0))
            items = self.cards(rows, pid)
        like = f"%{q.strip()}%"
        series = [self.series_card(s, pid) for s in self.db.q(
            f"SELECT * FROM series WHERE title LIKE ? AND id IN (SELECT i.series_id FROM items i WHERE {where}) "
            "LIMIT 20", [like, *args])]
        # collapse episodes of matched series
        sids = {s["id"] for s in series}
        items = [x for x in items if not (x["kind"] == "episode" and x["seriesId"] in sids)]
        return {"series": series, "items": items}

    def next_episode(self, row: sqlite3.Row) -> sqlite3.Row | None:
        if not row["series_id"]:
            return None
        return self.db.one(f"SELECT {self.ITEM_COLS} FROM items i LEFT JOIN series s ON s.id=i.series_id "
                           "WHERE i.series_id=? AND i.missing_at IS NULL AND (i.season>? OR (i.season=? AND i.episode>?)) "
                           "ORDER BY i.season, i.episode LIMIT 1",
                           (row["series_id"], row["season"] or 0, row["season"] or 0,
                            row["episode_end"] or row["episode"] or 0))

    def prev_episode(self, row: sqlite3.Row) -> sqlite3.Row | None:
        if not row["series_id"]:
            return None
        return self.db.one("SELECT id FROM items i WHERE i.series_id=? AND i.missing_at IS NULL AND (i.season<? OR "
                           "(i.season=? AND i.episode<?)) ORDER BY i.season DESC, i.episode DESC LIMIT 1",
                           (row["series_id"], row["season"] or 0, row["season"] or 0, row["episode"] or 0))

    def streams_info(self, iid: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        audio, subs = [], []
        for s in self.db.q("SELECT * FROM streams WHERE item_id=? ORDER BY type, sidx", (iid,)):
            if s["type"] == "a":
                ch = s["channels"] or 2
                chl = {1: "Mono", 2: "Stereo", 6: "5.1", 8: "7.1"}.get(ch, f"{ch}ch")
                lname = lang_name(s["lang"]) or f"Track {s['sidx'] + 1}"
                label = f"{lname} · {(s['codec'] or '').upper()} {chl}"
                if s["title"] and s["title"].lower() not in lname.lower():
                    label = f"{lname} ({s['title']}) · {(s['codec'] or '').upper()} {chl}"
                audio.append({"index": s["sidx"], "lang": s["lang"] or "", "label": label, "codec": s["codec"],
                              "channels": ch, "default": bool(s["default_flag"]), "title": s["title"] or ""})
            else:
                lname = lang_name(s["lang"]) or f"Subtitle {s['sidx'] + 1}"
                codec = (s["codec"] or "").replace("hdmv_pgs_subtitle", "PGS").replace("dvd_subtitle", "VobSub")
                codec = codec.replace("subrip", "SRT").replace("mov_text", "Text").upper() if len(codec) < 8 else codec
                bits = [lname]
                if s["title"] and s["title"].lower() != lname.lower():
                    bits.append(f"({s['title']})")
                if s["forced_flag"]:
                    bits.append("[Forced]")
                label = " ".join(bits) + f" · {codec}" + (" · External" if s["external_path"] else "")
                subs.append({"index": s["sidx"], "lang": s["lang"] or "", "label": label, "codec": s["codec"],
                             "forced": bool(s["forced_flag"]), "default": bool(s["default_flag"]),
                             "kind": "image" if s["bitmap_flag"] else "text", "external": bool(s["external_path"]),
                             "url": None if s["bitmap_flag"] else f"/api/v1/subtitles/{iid}/{s['sidx']}.vtt"})
        return audio, subs

    def details(self, profile: sqlite3.Row, iid: str) -> dict[str, Any] | None:
        pid = profile["id"]
        row = self.db.one(f"SELECT {self.ITEM_COLS}, i.container, i.bitrate, i.v_profile, i.v_bitdepth, i.v_fps, "
                          f"i.cast_json, i.size FROM items i LEFT JOIN series s ON s.id=i.series_id "
                          f"WHERE i.id=? AND {self.base_where(profile)[0]}", [iid, *self.base_where(profile)[1]])
        if row is None:
            return None
        d = self.cards([row], pid)[0]
        audio, subs = self.streams_info(iid)
        d.update({
            "audio": audio, "subtitles": subs, "container": row["container"], "bitrate": row["bitrate"],
            "videoCodec": row["v_codec"], "videoProfile": row["v_profile"], "bitDepth": row["v_bitdepth"],
            "fps": row["v_fps"], "width": row["v_w"], "height": row["v_h"], "size": row["size"],
            "cast": json.loads(row["cast_json"] or "[]"), "fileName": Path(row["rel_path"]).name,
            "chapters": [{"title": c_["title"], "start": c_["start_s"], "end": c_["end_s"]} for c_ in self.db.q(
                "SELECT * FROM chapters WHERE item_id=? ORDER BY idx", (iid,))],
        })
        nxt = self.next_episode(row)
        d["next"] = self.cards([nxt], pid)[0] if nxt else None
        prv = self.prev_episode(row)
        d["prevId"] = prv["id"] if prv else None
        ws = self.db.one("SELECT * FROM watch_state WHERE profile_id=? AND item_id=?", (pid, iid))
        d["audioPref"] = ws["audio_idx"] if ws else None
        d["subPref"] = ws["sub_idx"] if ws else None
        if row["series_id"]:
            s = self.db.one("SELECT * FROM series WHERE id=?", (row["series_id"],))
            if s is not None and not d["overview"]:
                d["seriesOverview"] = s["overview"] or ""
        return d

    def series(self, profile: sqlite3.Row, sid: str, season: int | None = None) -> dict[str, Any] | None:
        pid = profile["id"]
        s = self.db.one("SELECT * FROM series WHERE id=?", (sid,))
        if s is None:
            return None
        d = self.series_card(s, pid)
        where, args = self.base_where(profile)
        rows = self.db.q(f"SELECT {self.ITEM_COLS} FROM items i LEFT JOIN series s ON s.id=i.series_id WHERE {where} AND "
                         f"i.series_id=? ORDER BY i.season, i.episode, i.sort_title", args + [sid])
        eps = self.cards(rows, pid)
        seasons = sorted({e["season"] if e["season"] is not None else 1 for e in eps})

        def sn(e: dict[str, Any]) -> int:
            return 1 if e["season"] is None else e["season"]
        d["seasons"] = [{"number": n, "episodeCount": sum(1 for e in eps if sn(e) == n)} for n in seasons]
        if season is not None:
            eps = [e for e in eps if sn(e) == season]
        d["episodes"] = eps
        # next up: first unwatched after the most recently played episode
        ws_rows = [e for e in eps if e.get("lastPlayed")]
        nxt = None
        if ws_rows:
            last = max(ws_rows, key=lambda e: e["lastPlayed"] or 0)
            if not last["watched"]:
                nxt = last
            else:
                idx = eps.index(last)
                nxt = next((e for e in eps[idx + 1:] if not e["watched"]), None)
        d["nextUp"] = nxt or (next((e for e in eps if not e["watched"]), eps[0] if eps else None))
        return d

    def home(self, profile: sqlite3.Row) -> dict[str, Any]:
        pid = profile["id"]
        where, args = self.base_where(profile)
        rows_out: list[dict[str, Any]] = []
        base = f"SELECT {self.ITEM_COLS} FROM items i LEFT JOIN series s ON s.id=i.series_id WHERE {where}"
        # Continue watching (+ next up for series)
        cw = self.db.q(base + " AND i.id IN (SELECT item_id FROM watch_state WHERE profile_id=? AND watched=0 AND "
                       "position_s>15) ORDER BY (SELECT last_played_at FROM watch_state w WHERE w.item_id=i.id AND "
                       "w.profile_id=?) DESC LIMIT 20", args + [pid, pid])
        cw_cards = self.cards(cw, pid)
        seen_series = {c_["seriesId"] for c_ in cw_cards if c_["seriesId"]}
        recent_done = self.db.q("SELECT i.* FROM watch_state w JOIN items i ON i.id=w.item_id WHERE w.profile_id=? AND "
                                "w.watched=1 AND i.series_id IS NOT NULL ORDER BY w.last_played_at DESC LIMIT 30", (pid,))
        for r in recent_done:
            if r["series_id"] in seen_series:
                continue
            seen_series.add(r["series_id"])
            nxt = self.next_episode(r)
            if nxt is not None:
                ws = self.db.one("SELECT watched FROM watch_state WHERE profile_id=? AND item_id=?", (pid, nxt["id"]))
                if not ws or not ws["watched"]:
                    card = self.cards([nxt], pid)[0]
                    card["nextUp"] = True
                    cw_cards.append(card)
        if cw_cards:
            rows_out.append({"id": "continue", "title": "row.continue", "items": cw_cards[:24], "kind": "continue"})
        # Recently added (collapse episodes of the same series)
        recent = self.db.q(base + " ORDER BY i.added_at DESC LIMIT 200", args)
        rc_cards: list[dict[str, Any]] = []
        sids: set[str] = set()
        for card in self.cards(recent, pid):
            if card["seriesId"]:
                if card["seriesId"] in sids:
                    continue
                sids.add(card["seriesId"])
            rc_cards.append(card)
            if len(rc_cards) >= 30:
                break
        if rc_cards:
            rows_out.append({"id": "recent", "title": "row.recent", "items": rc_cards})
        # My list
        ml_ids = [r[0] for r in self.db.q("SELECT item_id FROM mylist WHERE profile_id=? ORDER BY added_at DESC", (pid,))]
        if ml_ids:
            ml_items = []
            id_rows = {r["id"]: r for r in self.db.q(base + " AND i.id IN ({})".format(",".join("?" * len(ml_ids))),
                                                     args + ml_ids)}
            item_cards = {c_["id"]: c_ for c_ in self.cards(list(id_rows.values()), pid)}
            for x in ml_ids:
                if x in item_cards:
                    ml_items.append(item_cards[x])
                else:
                    s = self.db.one("SELECT * FROM series WHERE id=?", (x,))
                    if s is not None:
                        ml_items.append(self.series_card(s, pid))
            if ml_items:
                rows_out.append({"id": "mylist", "title": "row.mylist", "items": ml_items})
        # Series
        series = self.db.q("SELECT * FROM series WHERE id IN (SELECT series_id FROM items i WHERE " + where +
                           ") ORDER BY (SELECT MAX(added_at) FROM items WHERE series_id=series.id) DESC LIMIT 40", args)
        if series:
            rows_out.append({"id": "series", "title": "row.shows", "items": [self.series_card(s, pid) for s in series]})
        # Movies
        movies = self.db.q(base + " AND i.kind IN ('movie','video') ORDER BY i.added_at DESC LIMIT 40", args)
        if movies:
            rows_out.append({"id": "movies", "title": "row.movies", "items": self.cards(movies, pid)})
        # Because you watched
        last = self.db.one("SELECT i.* FROM watch_state w JOIN items i ON i.id=w.item_id WHERE w.profile_id=? "
                           "ORDER BY w.last_played_at DESC LIMIT 1", (pid,))
        if last is not None:
            genres = json.loads(last["genres_json"] or "[]")
            sim: list[sqlite3.Row] = []
            if genres:
                sim = self.db.q(base + " AND i.id<>? AND i.genres_json LIKE ? AND (i.series_id IS NULL OR i.series_id<>?) "
                                "ORDER BY RANDOM() LIMIT 20", args + [last["id"], f'%"{genres[0]}"%', last["series_id"] or ""])
            elif last["folder"]:
                sim = self.db.q(base + " AND i.id<>? AND i.folder=? AND (i.series_id IS NULL OR i.series_id<>?) "
                                "ORDER BY RANDOM() LIMIT 20", args + [last["id"], last["folder"], last["series_id"] or ""])
            if len(sim) >= 3:
                t = last["title"]
                if last["series_id"]:
                    s = self.db.one("SELECT title FROM series WHERE id=?", (last["series_id"],))
                    t = s["title"] if s else t
                rows_out.append({"id": "because", "title": "row.because", "titleArg": t, "items": self.cards(sim, pid)})
        # Genre rows (TMDB) or folder rows
        genre_counts: dict[str, int] = collections.Counter()
        for r in self.db.q("SELECT genres_json FROM items i WHERE " + where + " AND genres_json IS NOT NULL", args):
            for g in json.loads(r[0] or "[]"):
                genre_counts[g] += 1
        for g, n in genre_counts.most_common(6):
            if n < 3:
                break
            rows = self.db.q(base + " AND i.genres_json LIKE ? AND i.kind<>'episode' ORDER BY RANDOM() LIMIT 30",
                             args + [f'%"{g}"%'])
            if len(rows) >= 3:
                rows_out.append({"id": f"genre:{g}", "title": g, "raw": True, "items": self.cards(rows, pid)})
        if not genre_counts:
            folders = self.db.q("SELECT folder, COUNT(*) AS n FROM items i WHERE " + where + " AND folder<>'' AND "
                                "series_id IS NULL GROUP BY folder ORDER BY n DESC LIMIT 6", args)
            for f in folders:
                if f["n"] < 2:
                    continue
                rows = self.db.q(base + " AND i.folder=? AND i.series_id IS NULL ORDER BY i.sort_title LIMIT 40",
                                 args + [f["folder"]])
                rows_out.append({"id": f"folder:{f['folder']}", "title": f["folder"], "raw": True,
                                 "items": self.cards(rows, pid)})
        multi = self.db.q(base + " AND i.kind IN ('movie','video') AND (SELECT COUNT(*) FROM streams x WHERE "
                          "x.item_id=i.id AND x.type='a')>1 ORDER BY i.added_at DESC LIMIT 30", args)
        if len(multi) >= 2:
            rows_out.append({"id": "multiaudio", "title": "row.multiaudio", "items": self.cards(multi, pid)})
        uhd = self.db.q(base + " AND (i.v_h>=2000 OR i.v_w>=3800 OR (i.hdr IS NOT NULL AND i.hdr<>'')) "
                        "ORDER BY i.added_at DESC LIMIT 30", args)
        if len(uhd) >= 2:
            rows_out.append({"id": "uhd", "title": "row.uhd", "items": self.cards(uhd, pid)})
        # Hero: prefer items with a backdrop
        hero_pool = self.db.q(base + " AND i.kind IN ('movie','video') AND i.id IN (SELECT item_id FROM artwork "
                              "WHERE kind='backdrop') ORDER BY i.added_at DESC LIMIT 12", args)
        hero = self.cards(hero_pool, pid)
        for s in series[:4]:
            if len(hero) >= 8:
                break
            hero.append(self.series_card(s, pid))
        if not hero:
            hero = rc_cards[:5]
        random.shuffle(hero)
        total = self.db.one(f"SELECT COUNT(*) FROM items i WHERE {where}", args)[0]
        return {"hero": hero[:5], "rows": rows_out, "total": total}


# ===== SECTION: playback-decision =====
DIRECT_CONTAINERS = {"mp4", "m4v", "mov", "webm"}
HLS_COPY_VIDEO = {"h264", "hevc", "av1"}
PASSTHROUGH_AUDIO = {"ac3", "eac3"}
BITMAP_SUB_CODECS = {"hdmv_pgs_subtitle", "dvd_subtitle", "dvb_subtitle", "xsub", "pgssub"}


def _h264_ok(profile: str, bitdepth: int) -> bool:
    p = (profile or "").lower()
    return bitdepth <= 8 and not any(x in p for x in ("4:4:4", "444", "4:2:2", "422", "high 10"))


def decide_playback(item: dict[str, Any], caps: dict[str, Any], opts: dict[str, Any]) -> dict[str, Any]:
    """Pure decision function (§8.2): returns method, reasons and encoding parameters.

    ``item`` is the normalised probe dict plus ``size``/``faststart``; ``opts`` carries server settings:
    ffmpeg (bool), allow_transcode, allow_passthrough, prefer_direct, quality, tonemap, burn_in, force.
    """
    reasons: list[str] = []
    v = item.get("video")
    audio = item.get("audio") or []
    subs = item.get("subs") or []
    container = (item.get("container") or "").lower()
    cv = [x.lower() for x in caps.get("video") or ["h264"]]
    ca = [x.lower() for x in caps.get("audio") or ["aac", "mp3"]]
    cc = [x.lower() for x in caps.get("containers") or ["mp4"]]
    hdr_caps = caps.get("hdr") or []
    a_idx = caps.get("audioIndex")
    if a_idx is None or not (0 <= int(a_idx) < len(audio)):
        a_idx = next((a["sidx"] for a in audio if a.get("default")), 0) if audio else None
    a_idx = int(a_idx) if a_idx is not None else None
    sel_audio = audio[a_idx] if a_idx is not None and audio else None
    s_idx = caps.get("subtitleIndex")
    burn_idx: int | None = None
    if (s_idx is not None and 0 <= int(s_idx) < len(subs) and subs[int(s_idx)].get("bitmap")
            and opts.get("burn_in", True)):
        burn_idx = int(s_idx)
    quality = opts.get("quality") or "auto"
    max_h, max_kbps = QUALITY_PRESETS.get(quality, (None, None))
    if caps.get("maxHeight"):
        max_h = min(max_h or 99999, int(caps["maxHeight"]))
    if caps.get("maxBitrateKbps"):
        max_kbps = min(max_kbps or 10 ** 9, int(caps["maxBitrateKbps"]))
    if opts.get("termux") and quality == "auto":
        max_h = min(max_h or 99999, 720)
    src_kbps = int((item.get("bitrate") or 0) / 1000)

    video_ok = True
    need_scale = False
    if v:
        codec = (v.get("codec") or "").lower()
        if codec not in cv:
            video_ok = False
            reasons.append(f"{codec}_unsupported")
        elif codec == "h264" and not _h264_ok(v.get("profile") or "", v.get("bitdepth") or 8):
            video_ok = False
            reasons.append("h264_profile_unsupported")
        elif codec == "hevc" and (v.get("bitdepth") or 8) > 8 and "hevc10" not in cv:
            video_ok = False
            reasons.append("hevc10_unsupported")
        if v.get("hdr") and not hdr_caps and opts.get("tonemap", True):
            video_ok = False
            reasons.append("hdr_tonemap")
        if v.get("hdr") == "dv" and "dv" not in hdr_caps and video_ok and codec == "hevc" and not hdr_caps:
            video_ok = False
        if max_h and (v.get("height") or 0) > max_h * 1.05 and quality not in ("original",):
            need_scale = True
            reasons.append("resolution_cap")
        if max_kbps and src_kbps > max_kbps * 1.1 and quality not in ("original",):
            need_scale = True
            reasons.append("bitrate_cap")
    audio_ok = True
    if sel_audio:
        acodec = (sel_audio.get("codec") or "").lower()
        if acodec not in ca:
            audio_ok = False
            reasons.append(f"{acodec}_audio_unsupported")
    multi = len(audio) > 1
    native_tracks = bool(caps.get("audioTracks"))
    force = opts.get("force") or ""

    def result(method: str, video_mode: str | None, audio_mode: str) -> dict[str, Any]:
        return {"method": method, "reason": reasons or ["compatible"], "videoMode": video_mode,
                "audioMode": audio_mode, "audioIndex": a_idx, "burnSubtitle": burn_idx,
                "maxHeight": max_h if need_scale or video_mode == "transcode" else None,
                "maxKbps": max_kbps}

    if not opts.get("ffmpeg"):
        reasons.append("ffmpeg_missing")
        return result("direct", None, "copy")

    container_ok = container in DIRECT_CONTAINERS and (container != "webm" or "webm" in cc)
    if not container_ok:
        reasons.append(f"container_{container or 'unknown'}")
    if multi and not native_tracks:
        reasons.append("multi_audio")
    faststart_bad = item.get("faststart") is False and (item.get("size") or 0) > 100 * 1024 * 1024
    if faststart_bad:
        reasons.append("moov_at_end")
    direct_ok = container_ok and video_ok and audio_ok and not need_scale and burn_idx is None and not faststart_bad
    if multi:
        # the client can switch embedded tracks itself only if every track is decodable
        direct_ok = direct_ok and native_tracks and all((a.get("codec") or "").lower() in ca for a in audio)
    if direct_ok and opts.get("prefer_direct", True) and force not in ("remux", "transcode"):
        reasons[:] = ["compatible"]
        return result("direct", None, "copy")

    acodec = (sel_audio.get("codec") or "").lower() if sel_audio else ""
    if acodec == "aac" or (acodec in PASSTHROUGH_AUDIO and acodec in ca and opts.get("allow_passthrough")):
        audio_mode = "copy"
    else:
        audio_mode = "aac"
    can_copy_video = (v is not None and video_ok and not need_scale and burn_idx is None and
                      (v.get("codec") or "").lower() in HLS_COPY_VIDEO)
    if v is None:
        return result("remux", None, audio_mode)
    if can_copy_video and force != "transcode":
        return result("remux", "copy", audio_mode)
    if not opts.get("allow_transcode", True):
        reasons.append("transcoding_disabled")
        return result("direct", None, "copy")
    if burn_idx is not None:
        reasons.append("subtitle_burn_in")
    if force == "transcode" and "forced" not in reasons:
        reasons.append("forced")
    return result("transcode", "transcode", audio_mode)


def codec_string(v: dict[str, Any] | None, mode: str | None, out_h: int) -> str:
    """RFC 6381 codec string for the video rendition."""
    if not v:
        return ""
    if mode == "transcode":
        return "avc1.640033" if out_h > 1080 else "avc1.640028"
    codec = (v.get("codec") or "").lower()
    lvl = int(v.get("level") or 40)
    prof = (v.get("profile") or "").lower()
    if codec == "h264":
        p = {"baseline": "42", "constrained baseline": "42", "main": "4d", "high": "64", "high 10": "6e"}.get(prof, "64")
        cons = "e0" if "constrained" in prof else "00"
        return f"avc1.{p}{cons}{max(10, min(lvl, 62)):02x}"
    if codec == "hevc":
        pr = "2.4" if "10" in prof or (v.get("bitdepth") or 8) > 8 else "1.6"
        return f"hvc1.{pr}.L{lvl if lvl > 0 else 120}.B0"
    if codec == "av1":
        return f"av01.0.08M.{10 if (v.get('bitdepth') or 8) > 8 else 8:02d}"
    if codec == "vp9":
        return "vp09.00.40.08"
    return "avc1.640028"


def audio_codec_string(codec: str, mode: str) -> str:
    if mode == "aac":
        return "mp4a.40.2"
    if mode in ("ac3", "eac3"):
        return "ac-3" if mode == "ac3" else "ec-3"
    return {"aac": "mp4a.40.2", "ac3": "ac-3", "eac3": "ec-3", "mp3": "mp4a.40.34", "opus": "opus",
            "flac": "fLaC"}.get(codec, "mp4a.40.2")


# ===== SECTION: hls-engine =====
SEG_LEN = 6.0
TRANSCODE_SEG = 3.0  # shorter video segments when encoding: faster start & seeks on slow CPUs


def build_fixed_segments(duration: float, seg: float = SEG_LEN) -> list[tuple[float, float]]:
    """Fixed-length segment table [(start, duration), …]."""
    out: list[tuple[float, float]] = []
    t = 0.0
    n = 0
    while t < duration - 0.05:
        d = min(seg, duration - t)
        out.append((round(t, 6), round(d, 6)))
        n += 1
        t = n * seg
    if not out:
        out.append((0.0, max(duration, 0.1)))
    return out


def build_keyframe_segments(kfs: list[float], duration: float, target: float = SEG_LEN, min_len: float = 4.0,
                            max_len: float = 10.0) -> tuple[list[tuple[float, float]], list[tuple[int, int]]]:
    """Merge GOPs (between real keyframes) into ~4–10 s segments.

    Returns the segment table and, per segment, the half-open GOP index range it covers.
    """
    # keyframe 0 is moved to t=0 (never inserted): ffmpeg's first GOP file always starts at the first real keyframe
    kfs = sorted(k for k in kfs if k >= 0) or [0.0]
    kfs[0] = 0.0
    bounds = kfs + [max(duration, kfs[-1] + 0.1)]
    segs: list[tuple[float, float]] = []
    groups: list[tuple[int, int]] = []
    a = 0
    n = len(kfs)
    while a < n:
        b = a + 1
        while b < n:
            cur = bounds[b] - bounds[a]
            nxt = bounds[b + 1] - bounds[a]
            if cur >= target or (nxt > max_len and cur >= min_len / 2):
                break
            b += 1
        start, end = bounds[a], bounds[b]
        if end - start <= 0.001 and segs:
            groups[-1] = (groups[-1][0], b)
            s0, _ = segs[-1]
            segs[-1] = (s0, round(end - s0, 6))
        else:
            segs.append((round(start, 6), round(end - start, 6)))
            groups.append((a, b))
        a = b
    # fold a tiny tail into the previous segment
    if len(segs) >= 2 and segs[-1][1] < 1.0:
        s0, _ = segs[-2]
        segs[-2] = (s0, round(segs[-1][0] + segs[-1][1] - s0, 6))
        groups[-2] = (groups[-2][0], groups[-1][1])
        segs.pop()
        groups.pop()
    return segs, groups


def media_playlist(segs: list[tuple[float, float]], init_uri: str, seg_uri: Callable[[int], str],
                   fmp4: bool = True) -> str:
    target = max(1, int(max((d for _, d in segs), default=6) + 0.999))
    lines = ["#EXTM3U", f"#EXT-X-VERSION:{7 if fmp4 else 3}", f"#EXT-X-TARGETDURATION:{target}",
             "#EXT-X-MEDIA-SEQUENCE:0", "#EXT-X-PLAYLIST-TYPE:VOD", "#EXT-X-INDEPENDENT-SEGMENTS"]
    if fmp4:
        lines.append(f'#EXT-X-MAP:URI="{init_uri}"')
    for i, (_, d) in enumerate(segs):
        lines.append(f"#EXTINF:{d:.6f},")
        lines.append(seg_uri(i))
    lines.append("#EXT-X-ENDLIST")
    return "\n".join(lines) + "\n"


def _suspend(proc: subprocess.Popen, suspend: bool) -> bool:
    """Pause/resume an encoder (throttling). Returns False when unsupported."""
    try:
        if PLAT.is_windows:
            import ctypes
            ntdll = ctypes.windll.ntdll  # type: ignore[attr-defined]
            k32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
            h = k32.OpenProcess(0x0800, False, proc.pid)  # PROCESS_SUSPEND_RESUME
            if not h:
                return False
            (ntdll.NtSuspendProcess if suspend else ntdll.NtResumeProcess)(h)
            k32.CloseHandle(h)
            return True
        os.killpg(proc.pid, signal.SIGSTOP if suspend else signal.SIGCONT)
        return True
    except Exception:  # noqa: BLE001
        return False


class TranscodeBusy(Exception):
    """Raised when the concurrent-transcode cap is reached and no job can be evicted."""


class TranscodeJob:
    """One ffmpeg process producing one rendition from a start segment."""

    def __init__(self, session: HlsSession, rendition: str, start_seg: int, args: list[str], outdir: Path,
                 file_for: Callable[[int], Path], hw: str | None = None) -> None:
        self.session = session
        self.rendition = rendition
        self.start_seg = start_seg
        self.args = args
        self.outdir = outdir
        self.file_for = file_for
        self.hw = hw
        self.proc: subprocess.Popen | None = None
        self.head = start_seg - 1  # last completed unit index
        self.paused = False
        self.started = time.time()
        self.last_request = time.time()
        self.requested = start_seg
        self.stderr: collections.deque[str] = collections.deque(maxlen=20)
        self.out_time = 0.0
        self.speed = 0.0
        self.finished = False
        self.killed = False
        self.returncode: int | None = None

    def start(self) -> None:
        self.outdir.mkdir(parents=True, exist_ok=True)
        log.debug("ffmpeg %s", " ".join(self.args))
        self.proc = subprocess.Popen(self.args, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                     stderr=subprocess.PIPE, **popen_kwargs(True))
        CHILDREN.add(self.proc)
        threading.Thread(target=self._read_progress, name=f"ffprog-{self.rendition}", daemon=True).start()
        threading.Thread(target=self._read_err, name=f"fferr-{self.rendition}", daemon=True).start()

    def _read_progress(self) -> None:
        assert self.proc and self.proc.stdout
        last_pub = 0.0
        for raw in self.proc.stdout:
            line = raw.decode(errors="ignore").strip()
            if line.startswith(("out_time_us=", "out_time_ms=")):
                with contextlib.suppress(ValueError):
                    self.out_time = int(line.split("=", 1)[1]) / 1e6
            elif line.startswith("speed="):
                with contextlib.suppress(ValueError):
                    self.speed = float(line.split("=", 1)[1].rstrip("x") or 0)
            elif line.startswith("progress=") and time.time() - last_pub > 1.0:
                last_pub = time.time()
                self.session.manager.app.events.publish("job.progress", {
                    "sessionId": self.session.id, "rendition": self.rendition, "time": round(self.out_time, 1),
                    "speed": self.speed, "startSeg": self.start_seg})
        rc = self.proc.wait()
        self.returncode = rc
        self.finished = True
        CHILDREN.remove(self.proc)
        if rc not in (0, None) and not self.killed:
            log.warning("ffmpeg %s exited %s: %s", self.rendition, rc, " | ".join(self.stderr)[-500:])

    def _read_err(self) -> None:
        assert self.proc and self.proc.stderr
        for raw in self.proc.stderr:
            line = raw.decode(errors="replace").strip()
            if line:
                self.stderr.append(line)

    def update_head(self) -> None:
        n = self.head + 1
        while self.file_for(n).exists():
            self.head = n
            n += 1

    def alive(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def kill(self) -> None:
        self.killed = True
        if self.proc is not None:
            kill_tree(self.proc, hard=True)
            CHILDREN.remove(self.proc)
        for p in list(self.outdir.glob("*.tmp")) if self.outdir.exists() else []:
            with contextlib.suppress(OSError):
                p.unlink()

    def set_paused(self, paused: bool) -> None:
        if self.proc is None or paused == self.paused or not self.alive():
            return
        if _suspend(self.proc, paused):
            self.paused = paused


class HlsSession:
    """A playback session: segment tables + on-demand jobs per rendition (§8.3)."""

    def __init__(self, manager: HlsManager, item_id: str, path: Path, info: dict[str, Any], decision: dict[str, Any],
                 mode: str, start_time: float = 0.0) -> None:
        self.manager = manager
        self.id = secrets.token_urlsafe(9).replace("-", "a").replace("_", "b")
        self.item_id = item_id
        self.path = path
        self.info = info
        self.decision = decision
        self.mode = mode
        self.fmp4 = manager.app.setting("segment_format") != "ts"
        self.start_time = start_time if mode == "simple" else 0.0
        self.start_hint = max(0.0, start_time)
        self.dir = manager.app.paths.hls / self.id
        self.dir.mkdir(parents=True, exist_ok=True)
        self.created = time.time()
        self.last_access = time.time()
        self.lock = threading.RLock()
        self.jobs: dict[str, TranscodeJob] = {}
        self.sw_fallback = False
        self.error: str | None = None
        self.duration = float(info.get("duration") or 0)
        # the video playlist must end where the *video* ends: when audio runs longer, segments past the last frame
        # would be empty and the player stalls at the end (and never reaches the next episode)
        vd = float((info.get("video") or {}).get("duration") or 0)
        self.vduration = vd if 1.0 < vd < self.duration - 0.5 else self.duration
        self.video_mode = decision.get("videoMode")
        self.audio = info.get("audio") or []
        self.gops: list[float] = []
        self.groups: list[tuple[int, int]] = []
        self.gop_time = 0.1
        self.vsegs: list[tuple[float, float]] = []
        self.asegs = build_fixed_segments(self.duration)
        self.out_h = 0
        self.hw = manager.app.ff.pick_hw(str(manager.app.setting("hwaccel")))
        v = info.get("video") or {}
        if self.video_mode == "transcode":
            mh = decision.get("maxHeight")
            self.out_h = min(v.get("height") or 1080, mh) if mh else (v.get("height") or 1080)
        else:
            self.out_h = v.get("height") or 0

    # --- tables -----------------------------------------------------------
    def prepare(self, kfs: list[float] | None) -> None:
        if self.mode == "simple":
            return
        if self.video_mode == "copy":
            assert kfs is not None
            st = float(self.info.get("start_time") or 0)
            rel = sorted({round(max(0.0, k - st), 6) for k in kfs if k - st >= -0.05}) or [0.0]
            rel[0] = 0.0
            self.vsegs, self.groups = build_keyframe_segments(rel, self.vduration)
            self.gops = rel
            gaps = [b - a for a, b in zip(self.gops, self.gops[1:]) if b - a > 0.0005]
            self.gop_time = max(0.005, min(0.5, (min(gaps) if gaps else 1.0) * 0.5))
        elif self.video_mode == "transcode":
            self.vsegs = build_fixed_segments(self.vduration, TRANSCODE_SEG)

    # --- playlists ----------------------------------------------------------
    def master(self, text_subs: list[dict[str, Any]]) -> str:
        lines = ["#EXTM3U", f"#EXT-X-VERSION:{7 if self.fmp4 else 3}", "#EXT-X-INDEPENDENT-SEGMENTS"]
        v = self.info.get("video")
        a_idx = self.decision.get("audioIndex")
        if self.mode == "simple":
            sel = self.audio[a_idx] if a_idx is not None and self.audio else None
            codecs = [c_ for c_ in (codec_string(v, self.video_mode, self.out_h),
                                    audio_codec_string((sel or {}).get("codec", "aac"), self.decision["audioMode"])
                                    if sel else "") if c_]
            bw = self._bandwidth()
            res = f",RESOLUTION={self._out_w()}x{self.out_h}" if v else ""
            lines.append(f'#EXT-X-STREAM-INF:BANDWIDTH={bw},CODECS="{",".join(codecs)}"{res}')
            lines.append("stream.m3u8")
            return "\n".join(lines) + "\n"
        a_codec = ""
        for a in self.audio:
            mode = self.audio_mode(a["sidx"])
            ch = 2 if mode == "aac" and (self.manager.app.setting("downmix_stereo") or (a.get("channels") or 2) <= 2) \
                else (a.get("channels") or 2)
            name = (lang_name(a.get("lang")) or f"Track {a['sidx'] + 1}") + (f" ({a['title']})" if a.get("title") else "")
            name = name.replace('"', "'")
            lang = f',LANGUAGE="{a["lang"]}"' if a.get("lang") else ""
            default = "YES" if a["sidx"] == a_idx else "NO"
            lines.append(f'#EXT-X-MEDIA:TYPE=AUDIO,GROUP-ID="aud",NAME="{name} #{a["sidx"] + 1}"{lang},DEFAULT={default},'
                         f'AUTOSELECT={default},CHANNELS="{ch}",URI="audio_{a["sidx"]}.m3u8"')
            if a["sidx"] == a_idx or not a_codec:
                a_codec = audio_codec_string(a.get("codec") or "aac", mode)
        sub_attr = ""
        if text_subs:
            sub_attr = ',SUBTITLES="subs"'
            for s in text_subs:
                nm = s["label"].replace('"', "'")
                lang = f',LANGUAGE="{s["lang"]}"' if s.get("lang") else ""
                lines.append(f'#EXT-X-MEDIA:TYPE=SUBTITLES,GROUP-ID="subs",NAME="{nm}"{lang},DEFAULT=NO,AUTOSELECT=NO,'
                             f'FORCED=NO,URI="sub_{s["index"]}.m3u8"')
        if v:
            codecs = ",".join(x for x in (codec_string(v, self.video_mode, self.out_h), a_codec) if x)
            fps = f",FRAME-RATE={v['fps']:.3f}" if v.get("fps") else ""
            aud = ',AUDIO="aud"' if self.audio else ""
            lines.append(f'#EXT-X-STREAM-INF:BANDWIDTH={self._bandwidth()},CODECS="{codecs}",'
                         f'RESOLUTION={self._out_w()}x{self.out_h}{fps}{aud}{sub_attr}')
            lines.append("video.m3u8")
        elif self.audio:
            lines.append(f'#EXT-X-STREAM-INF:BANDWIDTH=256000,CODECS="{a_codec}"{sub_attr}')
            lines.append(f"audio_{a_idx or 0}.m3u8")
        return "\n".join(lines) + "\n"

    def _out_w(self) -> int:
        v = self.info.get("video") or {}
        w, h = v.get("width") or 16, v.get("height") or 9
        if v.get("rotation") in (90, -90, 270, -270):
            w, h = h, w
        if not self.out_h or not h:
            return w
        return int(round(w * self.out_h / h / 2) * 2)

    def _bandwidth(self) -> int:
        if self.video_mode == "transcode":
            kb = self.decision.get("maxKbps") or {2160: 20000, 1440: 12000, 1080: 8000, 720: 4000, 480: 1500}.get(
                min((2160, 1440, 1080, 720, 480), key=lambda x: abs(x - (self.out_h or 1080))), 6000)
            return int(kb * 1000 * 1.1)
        return max(500_000, int((self.info.get("bitrate") or 4_000_000) * 1.1))

    def audio_mode(self, sidx: int) -> str:
        """Output codec for an audio rendition: ``aac`` (default) or ``ac3``/``eac3`` for surround passthrough.

        Audio is always (cheaply) re-encoded rather than stream-copied: a copied track starts at the container's
        seek point instead of the requested time, which shifts its timestamps after any resume/seek and leaves
        the player waiting for audio forever ("Preparing… 99%"). Re-encoding trims sample-accurately.
        """
        a = self.audio[sidx] if 0 <= sidx < len(self.audio) else {}
        codec = (a.get("codec") or "").lower()
        if codec in PASSTHROUGH_AUDIO and self.manager.app.setting("allow_passthrough") and \
                codec in (self.decision.get("clientAudio") or []) and codec in self.manager.app.ff.encoders:
            return codec
        return "aac"

    def _vmap(self) -> str:
        """Stream specifier of the real video stream (``0:v:0`` can be embedded cover art in some files)."""
        idx = (self.info.get("video") or {}).get("index")
        return f"0:{idx}" if isinstance(idx, int) else "0:V:0"  # V = video that isn't an attached picture

    def video_playlist(self) -> str:
        return media_playlist(self.vsegs, "init_video.mp4", lambda i: f"seg_video_{i}.m4s")

    def audio_playlist(self, k: int) -> str:
        return media_playlist(self.asegs, f"init_audio_{k}.mp4", lambda i: f"seg_audio_{k}_{i}.m4s")

    # --- ffmpeg command builders -------------------------------------------
    def _base(self, start: float) -> list[str]:
        app = self.manager.app
        thr = int(app.setting("ffmpeg_threads") or 0)
        args = [app.ff.ffmpeg or "ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-progress", "pipe:1",
                "-nostats"]
        if thr:
            args += ["-threads", str(thr)]
        return args

    def _hls_out(self, outdir: Path, prefix: str, start_number: int, hls_time: float, init_name: str) -> list[str]:
        return ["-f", "hls", "-hls_time", f"{hls_time:g}", "-hls_segment_type", "fmp4",
                "-hls_fmp4_init_filename", init_name, "-start_number", str(start_number),
                "-hls_segment_filename", str(outdir / f"{prefix}_%06d.m4s"),
                "-hls_flags", "+independent_segments+temp_file", "-hls_list_size", "0",
                "-hls_segment_options", "movflags=+frag_discont", str(outdir / f"{prefix}.m3u8")]

    def video_filters(self, sw: bool) -> tuple[list[str], str, list[str]]:
        """Return (input args, filter chain or filter_complex, encoder args)."""
        app = self.manager.app
        v = self.info.get("video") or {}
        filters: list[str] = []
        if v.get("hdr") and app.setting("tonemap"):
            if "zscale" in app.ff.filters and "tonemap" in app.ff.filters:
                filters.append("zscale=t=linear:npl=100,format=gbrpf32le,zscale=p=bt709,tonemap=tonemap=hable:desat=0,"
                               "zscale=t=bt709:m=bt709:r=tv")
            elif "tonemap_opencl" in app.ff.filters:
                pass
        mh = self.decision.get("maxHeight")
        if mh and (v.get("height") or 0) > mh:
            filters.append(f"scale=-2:{mh}")
        sar = v.get("sar") or "1:1"
        if sar not in ("1:1", "0:1", "") and not mh:
            filters.append("scale=trunc(iw*sar/2)*2:ih,setsar=1")
        kbps = self.decision.get("maxKbps") or {2160: 20000, 1080: 8000, 720: 4000, 480: 1500}.get(
            min((2160, 1080, 720, 480), key=lambda x: abs(x - (self.out_h or 1080))), 6000)
        crf = int(app.setting("crf") or 22)
        hw = None if sw else self.hw
        if hw:
            pre, enc, fmt = app.ff.hw_args(hw, kbps, crf)
        else:
            preset = str(app.setting("x264_preset") or "auto")
            if preset == "auto":
                preset = "superfast" if (PLAT.is_termux or PLAT.is_arm) else "veryfast"
            pre, fmt = [], "format=yuv420p"
            enc = ["-c:v", "libx264", "-preset", preset, "-crf", str(crf), "-profile:v", "high",
                   "-maxrate", f"{kbps}k", "-bufsize", f"{kbps * 2}k"]
            if "libx264" not in app.ff.encoders:
                enc = ["-c:v", "libopenh264", "-b:v", f"{kbps}k"] if "libopenh264" in app.ff.encoders else \
                    ["-c:v", "mpeg4", "-q:v", "4"]
        filters.append(fmt)
        enc += ["-force_key_frames", f"expr:gte(t,n_forced*{TRANSCODE_SEG:g})", "-sc_threshold", "0"]
        if not hw or hw in ("nvenc", "qsv", "amf"):
            enc += ["-g", str(int((v.get("fps") or 25) * TRANSCODE_SEG))]
        return pre, ",".join(filters), enc

    def video_args(self, seg: int, sw: bool = False) -> tuple[list[str], Callable[[int], Path], int, str]:
        """Command for the video rendition starting at segment ``seg``."""
        outdir = self.dir / "video"
        if self.video_mode == "copy":
            gop_start = self.groups[seg][0]
            t = self.gops[gop_start]
            nxt = self.gops[gop_start + 1] if gop_start + 1 < len(self.gops) else t + 1
            eps = min(0.01, max(0.0, (nxt - t) / 3))
            jobn = f"{gop_start}"
            args = self._base(t)
            args += ["-ss", f"{t + eps:.6f}", "-i", long_path(self.path),
                     "-map", self._vmap(), "-an", "-sn", "-dn", "-c:v", "copy"]
            if (self.info.get("video") or {}).get("codec") == "hevc":
                args += ["-tag:v", "hvc1"]
            args += ["-fflags", "+genpts", "-avoid_negative_ts", "make_non_negative", "-output_ts_offset", f"{t:.6f}",
                     "-max_muxing_queue_size", "4096"]
            args += self._hls_out(outdir, "gop", gop_start, self.gop_time, f"init_{jobn}.mp4")
            return args, lambda n: outdir / f"gop_{n:06d}.m4s", gop_start, "video"
        t = self.vsegs[seg][0]
        pre, vf, enc = self.video_filters(sw)
        args = self._base(t) + pre
        if t > 0:
            args += ["-ss", f"{t:.6f}"]
        args += ["-i", long_path(self.path)]
        burn = self.decision.get("burnSubtitle")
        if burn is not None:
            args += ["-filter_complex", f"[{self._vmap()}][0:s:{burn}]overlay=eof_action=pass[ov];[ov]{vf}[v]",
                     "-map", "[v]"]
        else:
            args += ["-map", self._vmap(), "-vf", vf]
        args += ["-an", "-sn", "-dn"] + enc
        args += ["-avoid_negative_ts", "make_non_negative", "-output_ts_offset", f"{t:.6f}", "-max_muxing_queue_size", "4096"]
        args += self._hls_out(outdir, "seg", seg, TRANSCODE_SEG, f"init_{seg}{'sw' if sw else ''}.mp4")
        return args, lambda n: outdir / f"seg_{n:06d}.m4s", seg, "video"

    def audio_args(self, k: int, seg: int) -> tuple[list[str], Callable[[int], Path], int, str]:
        outdir = self.dir / f"audio_{k}"
        t = self.asegs[seg][0]
        args = self._base(t)
        if t > 0:
            args += ["-ss", f"{t:.6f}"]
        args += ["-i", long_path(self.path), "-map", f"0:a:{k}", "-vn", "-sn", "-dn"]
        mode = self.audio_mode(k)
        a = self.audio[k] if k < len(self.audio) else {}
        ch = a.get("channels") or 2
        if mode in ("ac3", "eac3"):
            args += ["-c:a", mode, "-b:a", "640k" if ch > 2 else "224k"]
        elif self.manager.app.setting("downmix_stereo") or ch <= 2:
            args += ["-c:a", "aac", "-b:a", "192k", "-ac", "2"]
        else:
            args += ["-c:a", "aac", "-b:a", "384k", "-ac", str(min(ch, 6))]
        # first_pts=0 pads a late-starting track with silence (keeps A/V sync) so audio always covers the
        # segment from its first sample — otherwise the player has to skip the audio-less start
        args += ["-af", "aresample=async=1:first_pts=0"]
        if a.get("lang"):
            args += ["-metadata:s:a:0", f"language={a['lang']}"]
        args += ["-avoid_negative_ts", "make_non_negative", "-output_ts_offset", f"{t:.6f}"]
        args += self._hls_out(outdir, "seg", seg, SEG_LEN, f"init_{seg}.mp4")
        return args, lambda n: outdir / f"seg_{n:06d}.m4s", seg, f"audio_{k}"

    def simple_args(self) -> list[str]:
        """Single muxed ffmpeg (prototype behaviour / TS legacy)."""
        outdir = self.dir / "simple"
        outdir.mkdir(parents=True, exist_ok=True)
        t = self.start_time
        args = self._base(t)
        pre: list[str] = []
        vf = ""
        enc: list[str] = []
        if self.video_mode == "transcode":
            pre, vf, enc = self.video_filters(self.sw_fallback)
        args += pre
        if t > 0:
            args += ["-ss", f"{t:.3f}"]
        args += ["-i", long_path(self.path)]
        burn = self.decision.get("burnSubtitle")
        if self.info.get("video"):
            if self.video_mode == "transcode" and burn is not None:
                args += ["-filter_complex", f"[{self._vmap()}][0:s:{burn}]overlay=eof_action=pass[ov];[ov]{vf}[v]",
                         "-map", "[v]"]
            else:
                args += ["-map", self._vmap()]
                if self.video_mode == "transcode":
                    args += ["-vf", vf]
        a_idx = self.decision.get("audioIndex")
        if a_idx is not None and self.audio:
            args += ["-map", f"0:a:{a_idx}"]
        if self.video_mode == "transcode":
            args += enc
        elif self.info.get("video"):
            args += ["-c:v", "copy"]
            if (self.info.get("video") or {}).get("codec") == "hevc":
                args += ["-tag:v", "hvc1"]
        if a_idx is not None and self.audio:
            am = self.audio_mode(a_idx)
            if am in ("ac3", "eac3"):
                args += ["-c:a", am, "-b:a", "640k"]
            else:
                args += ["-c:a", "aac", "-b:a", "192k", "-ac", "2", "-af", "aresample=async=1:first_pts=0"]
        args += ["-sn", "-dn", "-avoid_negative_ts", "make_zero", "-max_muxing_queue_size", "4096",
                 "-f", "hls", "-hls_time", f"{TRANSCODE_SEG if self.video_mode == 'transcode' else SEG_LEN:g}",
                 "-hls_playlist_type", "event", "-hls_list_size", "0",
                 "-hls_flags", "+independent_segments+temp_file"]
        if self.fmp4:
            args += ["-hls_segment_type", "fmp4", "-hls_fmp4_init_filename", "s_init.mp4",
                     "-hls_segment_filename", str(outdir / "s_%05d.m4s")]
        else:
            args += ["-hls_segment_type", "mpegts", "-hls_segment_filename", str(outdir / "s_%05d.ts")]
        args.append(str(outdir / "stream.m3u8"))
        return args

    # --- job control --------------------------------------------------------
    def ensure_simple(self) -> TranscodeJob:
        with self.lock:
            job = self.jobs.get("simple")
            if job is not None and job.killed:
                raise FileNotFoundError("simple-mode job was stopped")  # client renegotiates at its position
            if job is None or (job.finished and job.returncode not in (0,) and not self.sw_fallback and job.head < 0):
                if self.video_mode == "transcode":
                    self.manager.make_room(self)
                if job is not None and self.video_mode == "transcode" and self.hw and not self.sw_fallback:
                    self.sw_fallback = True
                    log.warning("HW encode failed (%s) — retrying with software", self.hw)
                outdir = self.dir / "simple"
                ext = "m4s" if self.fmp4 else "ts"
                job = TranscodeJob(self, "simple", 0, self.simple_args(), outdir,
                                   lambda n: outdir / f"s_{n:05d}.{ext}", None if self.sw_fallback else self.hw)
                job.is_transcode = self.video_mode == "transcode"  # type: ignore[attr-defined]
                self.manager.register_job(job)
                job.start()
                self.jobs["simple"] = job
            job.last_request = time.time()
            return job

    def _restart(self, rendition: str, seg: int) -> TranscodeJob:
        old = self.jobs.get(rendition)
        if old is not None:
            old.kill()
            self.manager.unregister_job(old)
        if rendition == "video":
            args, file_for, unit, _ = self.video_args(seg, sw=self.sw_fallback)
            is_transcode = self.video_mode == "transcode"
        else:
            k = int(rendition.split("_")[1])
            args, file_for, unit, _ = self.audio_args(k, seg)
            is_transcode = False
        outdir = file_for(0).parent
        if is_transcode:
            self.manager.make_room(self)
        job = TranscodeJob(self, rendition, unit, args, outdir, file_for,
                           self.hw if rendition == "video" and is_transcode and not self.sw_fallback else None)
        job.is_transcode = is_transcode  # type: ignore[attr-defined]
        self.manager.register_job(job)
        job.start()
        self.jobs[rendition] = job
        return job

    def segment_units(self, rendition: str, seg: int) -> tuple[int, int]:
        """Units (GOP or segment indices) that make up the public segment ``seg``."""
        if rendition == "video" and self.video_mode == "copy":
            return self.groups[seg]
        return seg, seg + 1

    def unit_file(self, rendition: str, unit: int) -> Path:
        if rendition == "video":
            if self.video_mode == "copy":
                return self.dir / "video" / f"gop_{unit:06d}.m4s"
            return self.dir / "video" / f"seg_{unit:06d}.m4s"
        return self.dir / rendition / f"seg_{unit:06d}.m4s"

    def get_segment(self, rendition: str, seg: int, timeout: float = 20.0) -> list[Path] | None:
        """Return the file(s) composing a segment, starting/restarting ffmpeg as needed."""
        self.last_access = time.time()
        a, b = self.segment_units(rendition, seg)
        files = [self.unit_file(rendition, u) for u in range(a, b)]
        lookahead = int(self.manager.app.setting("lookahead") or 12)
        deadline = time.time() + timeout
        retried = False
        while True:
            if all(f.exists() for f in files):
                with self.lock:
                    job = self.jobs.get(rendition)
                    if job is not None:
                        job.requested = b  # playhead proxy for throttling
                        job.last_request = time.time()
                        if job.paused and job.head - b <= lookahead:
                            job.set_paused(False)
                return files
            if self.manager.get(self.id) is not self:
                return None  # session was stopped/replaced while we waited
            ahead = lookahead * 3 if rendition == "video" and self.video_mode == "copy" else lookahead
            with self.lock:
                job = self.jobs.get(rendition)
                # ffmpeg may finish writing the last file between the all-exist check above and this scan
                # (common on slow phones): nothing is missing any more, so loop round and serve it
                missing = next((u for u, f in zip(range(a, b), files) if not f.exists()), None)
                if missing is None:
                    continue
                restart = job is None or missing < job.start_seg or missing <= job.head or \
                    (job.finished and job.head < missing) or \
                    missing > max(job.head, job.start_seg) + max(2, ahead)
                if job is not None and job.finished and job.returncode not in (0, None) and job.head < missing:
                    if rendition == "video" and self.video_mode == "transcode" and self.hw and not self.sw_fallback:
                        self.sw_fallback = True
                        log.warning("HW encoder %s failed (%s) — falling back to software", self.hw,
                                    " ".join(job.stderr)[-200:])
                        # drop everything the HW encoder wrote so init + segments come from one encoder
                        shutil.rmtree(self.dir / "video", ignore_errors=True)
                        restart = True
                    elif retried:
                        self.error = " ".join(job.stderr)[-300:] or "ffmpeg failed"
                        return None
                    retried = True
                if restart:
                    job = self._restart(rendition, seg)
                job.requested = max(missing, b - 1)
                job.last_request = time.time()
                if job.paused:
                    job.set_paused(False)
            if time.time() > deadline:
                return None
            time.sleep(0.05)

    def seg_at(self, rendition: str, t: float) -> int:
        """Index of the public segment containing time ``t`` for a rendition."""
        table = self.vsegs if rendition == "video" else self.asegs
        for i, (a, d) in enumerate(table):
            if a <= t < a + d:
                return i
        return max(0, len(table) - 1) if table and t > 0 else 0

    def prestart(self) -> None:
        """Start the video + selected-audio encoders at the start position right away (saves 1–2 round trips
        and avoids a throw-away ffmpeg that the init request used to start at 0:00)."""
        if self.mode == "simple":
            with contextlib.suppress(TranscodeBusy, OSError, FileNotFoundError):
                self.ensure_simple()
            return
        rends = (["video"] if self.video_mode else []) + \
            ([f"audio_{self.decision.get('audioIndex') or 0}"] if self.audio else [])
        with self.lock:
            for r in rends:
                if r not in self.jobs:
                    with contextlib.suppress(TranscodeBusy, OSError, IndexError):
                        self._restart(r, self.seg_at(r, self.start_hint))

    def init_file(self, rendition: str) -> Path | None:
        d = self.dir / ("video" if rendition == "video" else rendition)
        for _ in range(400):
            cands: list[tuple[float, Path]] = []
            for p in (d.glob("init_*.mp4") if d.exists() else []):
                with contextlib.suppress(OSError):
                    st = p.stat()
                    if st.st_size > 0:
                        cands.append((st.st_mtime, p))
            if cands:
                return min(cands)[1]
            if self.manager.get(self.id) is not self:
                return None
            # kick a job so the init segment gets written
            with self.lock:
                if rendition not in self.jobs:
                    self._restart(rendition, self.seg_at(rendition, self.start_hint))
            time.sleep(0.05)
        return None

    def stop(self) -> None:
        with self.lock:
            for job in self.jobs.values():
                job.kill()
                self.manager.unregister_job(job)
            self.jobs.clear()
        shutil.rmtree(self.dir, ignore_errors=True)


class HlsManager:
    """Owns sessions and jobs: dedupe, throttling, idle kill, cap, LRU cache cleanup."""

    def __init__(self, app: App) -> None:
        self.app = app
        self.sessions: dict[str, HlsSession] = {}
        self.jobs: list[TranscodeJob] = []
        self.lock = threading.RLock()
        self._stop = threading.Event()
        self.direct_active: dict[str, float] = {}
        self._sleep_on = False

    def start(self) -> None:
        shutil.rmtree(self.app.paths.hls, ignore_errors=True)  # stale sessions from a previous run
        self.app.paths.hls.mkdir(parents=True, exist_ok=True)
        threading.Thread(target=self._monitor, name="hls-monitor", daemon=True).start()

    def stop(self) -> None:
        self._stop.set()
        with self.lock:
            sessions = list(self.sessions.values())
            self.sessions.clear()
        for s in sessions:  # outside the manager lock (lock order is always session → manager)
            s.stop()

    def max_transcodes(self) -> int:
        n = int(self.app.setting("max_transcodes") or 0)
        return n if n > 0 else (1 if PLAT.is_termux else 2)

    def register_job(self, job: TranscodeJob) -> None:
        with self.lock:
            self.jobs.append(job)

    def unregister_job(self, job: TranscodeJob) -> None:
        with self.lock:
            if job in self.jobs:
                self.jobs.remove(job)

    def make_room(self, session: HlsSession) -> None:
        """Enforce the transcode cap: evict only idle jobs (> 15 s without requests), else refuse."""
        victims: list[TranscodeJob] = []
        with self.lock:
            tj = [j for j in self.jobs if getattr(j, "is_transcode", False) and j.alive() and j.session is not session]
            now = time.time()
            while len(tj) >= self.max_transcodes():
                idle = [j for j in tj if now - j.last_request > 15]
                if not idle:
                    raise TranscodeBusy("Transcode limit reached — another stream is being converted")
                victim = min(idle, key=lambda j: j.last_request)
                log.info("Transcode cap reached — stopping idle job of session %s", victim.session.id)
                victims.append(victim)
                self.jobs.remove(victim)
                tj.remove(victim)
        for v in victims:
            v.kill()
            if v.session.jobs.get(v.rendition) is v:
                v.session.jobs.pop(v.rendition, None)

    def create(self, item_id: str, path: Path, info: dict[str, Any], decision: dict[str, Any],
               start_time: float = 0.0, replace: str | None = None) -> HlsSession:
        mode = str(self.app.setting("hls_mode") or "ondemand")
        if self.app.setting("segment_format") == "ts":
            mode = "simple"
        kfs: list[float] | None = None
        if mode == "ondemand" and decision.get("videoMode") == "copy":
            st = path.stat()
            cache = self.app.paths.keyframes / f"{stable_id(item_id, st.st_size, st.st_mtime_ns)}.json"
            if not cache.exists():
                # usually built already (details page / background warm-up); otherwise give it a moment, then start
                # instantly in simple mode while the table finishes for the next play / seek-heavy session
                self.app.scanner.warm_keyframes(item_id, path, prio=0)
                deadline = time.time() + 2.5
                while not cache.exists() and time.time() < deadline:
                    time.sleep(0.05)
            if cache.exists():
                kfs = keyframe_table(self.app.ff, path, cache)
            if not kfs:
                log.info("Keyframe table not ready for %s — starting in simple HLS mode", item_id)
                mode = "simple"
        if replace:
            self.stop_session(replace)
        v = info.get("video")
        if v is not None and "duration" not in v and self.app.ff.available:  # probed by an older version
            v["duration"] = video_stream_duration(self.app.ff, path)
        s = HlsSession(self, item_id, path, info, decision, mode, start_time)
        s.prepare(kfs)
        with self.lock:
            self.sessions[s.id] = s
        self._update_sleep()
        return s

    def get(self, sid: str) -> HlsSession | None:
        with self.lock:
            return self.sessions.get(sid)

    def stop_session(self, sid: str) -> bool:
        with self.lock:
            s = self.sessions.pop(sid, None)
        if s is not None:
            s.stop()
            self._update_sleep()
            return True
        return False

    def touch_direct(self, item_id: str) -> None:
        self.direct_active[item_id] = time.time()
        self._update_sleep()

    def active_streams(self) -> int:
        now = time.time()
        n = sum(1 for s in list(self.sessions.values()) if now - s.last_access < 30)
        n += sum(1 for t in list(self.direct_active.values()) if now - t < 60)
        return n

    def _update_sleep(self) -> None:
        active = self.active_streams() > 0
        if active and not self._sleep_on and self.app.setting("prevent_sleep"):
            self._sleep_on = True
            self.app.sleep.acquire()
        elif not active and self._sleep_on:
            self._sleep_on = False
            self.app.sleep.release()

    def _monitor(self) -> None:
        last_cleanup = 0.0
        while not self._stop.wait(0.5):
            now = time.time()
            lookahead = int(self.app.setting("lookahead") or 12)
            with self.lock:
                jobs = list(self.jobs)
                sessions = list(self.sessions.values())
            for j in jobs:
                with contextlib.suppress(Exception):
                    j.update_head()
                    if not j.alive():
                        continue
                    if now - j.last_request > (300 if j.rendition == "simple" else 60):  # idle timeout
                        j.kill()
                        self.unregister_job(j)
                        if j.session.jobs.get(j.rendition) is j:
                            j.session.jobs.pop(j.rendition, None)
                        continue
                    ahead = lookahead * (3 if j.rendition == "video" and j.session.video_mode == "copy" else 1)
                    if j.head - j.requested > ahead:
                        j.set_paused(True)
                    elif j.paused and j.head - j.requested <= ahead // 2:
                        j.set_paused(False)
            for s in sessions:
                if now - s.last_access > 1800:
                    log.info("Session %s expired", s.id)
                    self.stop_session(s.id)
            for k, t in list(self.direct_active.items()):
                if now - t > 120:
                    self.direct_active.pop(k, None)
            self._update_sleep()
            if now - last_cleanup > 60:
                last_cleanup = now
                with contextlib.suppress(Exception):
                    self.cleanup_cache()

    def cleanup_cache(self) -> None:
        """LRU cleanup of HLS segments (and generated images) to the configured limit."""
        limit = float(self.app.setting("cache_limit_gb") or 10) * 1024 ** 3
        files: list[tuple[float, int, Path]] = []
        total = 0
        for root in (self.app.paths.hls, self.app.paths.images / "resized", self.app.paths.subs):
            for dirpath, _, names in os.walk(root):
                for n in names:
                    p = Path(dirpath) / n
                    with contextlib.suppress(OSError):
                        st = p.stat()
                        files.append((max(st.st_mtime, st.st_atime), st.st_size, p))
                        total += st.st_size
        if total <= limit:
            return
        files.sort()
        active_dirs = {str(s.dir) for s in self.sessions.values() if time.time() - s.last_access < 120}
        now = time.time()
        for ts, size, p in files:
            if total <= limit * 0.9:
                break
            # active session: keep init/playlist files and anything touched in the last 5 minutes
            if any(str(p).startswith(d) for d in active_dirs) and (
                    not p.name.startswith(("seg_", "gop_")) or now - ts < 300):
                continue
            with contextlib.suppress(OSError):
                p.unlink()
                total -= size


def mp4_tfdt(data: bytes) -> list[int]:
    """Return baseMediaDecodeTime values found in an fMP4 fragment (used by tests/diagnostics)."""
    out: list[int] = []

    def walk(buf: bytes, start: int, end: int) -> None:
        i = start
        while i + 8 <= end:
            size, typ = struct.unpack(">I4s", buf[i:i + 8])
            hdr = 8
            if size == 1:
                size = struct.unpack(">Q", buf[i + 8:i + 16])[0]
                hdr = 16
            if size < 8 or i + size > end:
                break
            if typ in (b"moof", b"traf"):
                walk(buf, i + hdr, i + size)
            elif typ == b"tfdt":
                ver = buf[i + hdr]
                if ver == 1:
                    out.append(struct.unpack(">Q", buf[i + hdr + 4:i + hdr + 12])[0])
                else:
                    out.append(struct.unpack(">I", buf[i + hdr + 4:i + hdr + 8])[0])
            i += size
    walk(data, 0, len(data))
    return out


# ===== SECTION: qr =====
class QR:
    """Minimal pure-Python QR encoder (byte mode, EC level M, versions 1–10).  Uses ``segno`` when installed."""

    # (total codewords, ec codewords per block, [(blocks, data codewords per block), ...]) for level M
    _M: ClassVar[dict[int, tuple[int, int, list[tuple[int, int]]]]] = {
        1: (26, 10, [(1, 16)]), 2: (44, 16, [(1, 28)]), 3: (70, 26, [(1, 44)]), 4: (100, 18, [(2, 32)]),
        5: (134, 24, [(2, 43)]), 6: (172, 16, [(4, 27)]), 7: (196, 18, [(4, 31)]),
        8: (242, 22, [(2, 38), (2, 39)]), 9: (292, 22, [(3, 36), (2, 37)]), 10: (346, 26, [(4, 43), (1, 44)])}
    _ALIGN: ClassVar[dict[int, list[int]]] = {
        1: [], 2: [6, 18], 3: [6, 22], 4: [6, 26], 5: [6, 30], 6: [6, 34], 7: [6, 22, 38], 8: [6, 24, 42],
        9: [6, 26, 46], 10: [6, 28, 50]}

    def __init__(self) -> None:
        self.exp = [0] * 512
        self.log = [0] * 256
        x = 1
        for i in range(255):
            self.exp[i] = x
            self.log[x] = i
            x <<= 1
            if x & 0x100:
                x ^= 0x11D
        for i in range(255, 512):
            self.exp[i] = self.exp[i - 255]

    def _mul(self, a: int, b: int) -> int:
        return 0 if a == 0 or b == 0 else self.exp[self.log[a] + self.log[b]]

    def _rs(self, data: list[int], n: int) -> list[int]:
        gen = [1]
        for i in range(n):
            ng = [0] * (len(gen) + 1)
            for j, g in enumerate(gen):
                ng[j] ^= self._mul(g, 1)
                ng[j + 1] ^= self._mul(g, self.exp[i])
            gen = ng
        res = [0] * n
        for d in data:
            factor = d ^ res[0]
            res = res[1:] + [0]
            for j in range(n):
                res[j] ^= self._mul(gen[j + 1], factor)
        return res

    def matrix(self, text: str) -> list[list[bool]]:
        if segno is not None:
            q = segno.make(text, error="m", micro=False)
            return [[bool(v) for v in row] for row in q.matrix]
        data = text.encode("utf-8")
        ver = next((v for v in range(1, 11) if sum(b * d for b, d in self._M[v][2]) >= len(data) + (3 if v >= 10 else 2)), None)
        if ver is None:
            raise ValueError("text too long for QR")
        _total, ecn, groups = self._M[ver]
        cap = sum(b * d for b, d in groups)
        bits: list[int] = []

        def put(v: int, n: int) -> None:
            bits.extend((v >> (n - 1 - i)) & 1 for i in range(n))
        put(0b0100, 4)
        put(len(data), 8 if ver < 10 else 16)
        for b in data:
            put(b, 8)
        put(0, min(4, cap * 8 - len(bits)))
        while len(bits) % 8:
            bits.append(0)
        cw = [int("".join(map(str, bits[i:i + 8])), 2) for i in range(0, len(bits), 8)]
        pad = [0xEC, 0x11]
        i = 0
        while len(cw) < cap:
            cw.append(pad[i % 2])
            i += 1
        blocks, ecs = [], []
        pos = 0
        for nb, nd in groups:
            for _ in range(nb):
                blk = cw[pos:pos + nd]
                pos += nd
                blocks.append(blk)
                ecs.append(self._rs(blk, ecn))
        final: list[int] = []
        for i in range(max(len(b) for b in blocks)):
            for b in blocks:
                if i < len(b):
                    final.append(b[i])
        for i in range(ecn):
            for e in ecs:
                final.append(e[i])
        size = 17 + 4 * ver
        m: list[list[bool | None]] = [[None] * size for _ in range(size)]

        def finder(r: int, c_: int) -> None:
            for dr in range(-1, 8):
                for dc in range(-1, 8):
                    rr, cc = r + dr, c_ + dc
                    if 0 <= rr < size and 0 <= cc < size:
                        on = (0 <= dr <= 6 and dc in (0, 6)) or (0 <= dc <= 6 and dr in (0, 6)) or \
                             (2 <= dr <= 4 and 2 <= dc <= 4)
                        m[rr][cc] = on
        finder(0, 0)
        finder(0, size - 7)
        finder(size - 7, 0)
        for i in range(8, size - 8):
            m[6][i] = m[i][6] = i % 2 == 0
        al = self._ALIGN[ver]
        for r in al:
            for c_ in al:
                if (r, c_) in ((al[0], al[0]), (al[0], al[-1]), (al[-1], al[0])):
                    continue  # overlaps a finder pattern
                for dr in range(-2, 3):
                    for dc in range(-2, 3):
                        m[r + dr][c_ + dc] = max(abs(dr), abs(dc)) != 1
        m[size - 8][8] = True
        reserved = set()
        for i in range(9):
            reserved |= {(8, i), (i, 8)}
        for i in range(8):
            reserved |= {(8, size - 1 - i), (size - 1 - i, 8)}
        if ver >= 7:
            for i in range(6):
                for j in range(3):
                    reserved |= {(i, size - 11 + j), (size - 11 + j, i)}
        fbits = [(b >> (7 - i)) & 1 for b in final for i in range(8)]
        func = [[m[r][c_] is not None or (r, c_) in reserved for c_ in range(size)] for r in range(size)]
        best = None
        for mask in range(8):
            mm = [[bool(m[r][c_]) for c_ in range(size)] for r in range(size)]
            idx = 0
            col = size - 1
            up = True
            while col > 0:
                if col == 6:
                    col -= 1
                rows = range(size - 1, -1, -1) if up else range(size)
                for r in rows:
                    for cc in (col, col - 1):
                        if not func[r][cc]:
                            bit = fbits[idx] if idx < len(fbits) else 0
                            idx += 1
                            cond = [(r + cc) % 2 == 0, r % 2 == 0, cc % 3 == 0, (r + cc) % 3 == 0,
                                    (r // 2 + cc // 3) % 2 == 0, (r * cc) % 2 + (r * cc) % 3 == 0,
                                    ((r * cc) % 2 + (r * cc) % 3) % 2 == 0, ((r + cc) % 2 + (r * cc) % 3) % 2 == 0][mask]
                            mm[r][cc] = bool(bit) ^ cond
                up = not up
                col -= 2
            fmt = (0b00 << 3 | mask) << 10
            g = 0b10100110111
            rem = fmt
            for i in range(14, 9, -1):
                if rem & (1 << i):
                    rem ^= g << (i - 10)
            fmt = (fmt | rem) ^ 0b101010000010010
            fb = [(fmt >> i) & 1 for i in range(15)]
            for i in range(6):
                mm[i][8] = bool(fb[i])
            mm[7][8] = bool(fb[6])
            mm[8][8] = bool(fb[7])
            mm[8][7] = bool(fb[8])
            for i in range(9, 15):
                mm[8][14 - i] = bool(fb[i])
            for i in range(8):
                mm[8][size - 1 - i] = bool(fb[i])
            for i in range(8, 15):
                mm[size - 15 + i][8] = bool(fb[i])
            mm[size - 8][8] = True
            if ver >= 7:
                vrem = ver << 12
                for i in range(17, 11, -1):
                    if vrem & (1 << i):
                        vrem ^= 0b1111100100101 << (i - 12)
                vbits = (ver << 12) | vrem
                for i in range(18):
                    b = bool((vbits >> i) & 1)
                    mm[i // 3][size - 11 + i % 3] = b
                    mm[size - 11 + i % 3][i // 3] = b
            score = self._penalty(mm)
            if best is None or score < best[0]:
                best = (score, mm)
        assert best is not None
        return best[1]

    @staticmethod
    def _penalty(m: list[list[bool]]) -> int:
        n = len(m)
        s = 0
        for grid in (m, [list(x) for x in zip(*m)]):
            for row in grid:
                run = 1
                for i in range(1, n):
                    if row[i] == row[i - 1]:
                        run += 1
                    else:
                        if run >= 5:
                            s += run - 2
                        run = 1
                if run >= 5:
                    s += run - 2
                srow = "".join("1" if x else "0" for x in row)
                s += 40 * (srow.count("10111010000") + srow.count("00001011101"))
        for r in range(n - 1):
            for c_ in range(n - 1):
                if m[r][c_] == m[r + 1][c_] == m[r][c_ + 1] == m[r + 1][c_ + 1]:
                    s += 3
        dark = sum(sum(row) for row in m)
        s += abs(dark * 20 // (n * n) - 10) * 10
        return s

    def svg(self, text: str, scale: int = 8, border: int = 4) -> str:
        m = self.matrix(text)
        n = len(m) + border * 2
        path = []
        for r, row in enumerate(m):
            for c_, on in enumerate(row):
                if on:
                    path.append(f"M{c_ + border},{r + border}h1v1h-1z")
        return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {n} {n}" width="{n * scale}" height="{n * scale}" '
                f'shape-rendering="crispEdges"><rect width="100%" height="100%" fill="#fff"/>'
                f'<path fill="#000" d="{"".join(path)}"/></svg>')

    def terminal(self, text: str) -> str:
        m = self.matrix(text)
        n = len(m)
        pad = 2
        grid = [[False] * (n + pad * 2) for _ in range(pad)] + \
               [[False] * pad + row + [False] * pad for row in m] + [[False] * (n + pad * 2) for _ in range(pad + 1)]
        lines = []
        for r in range(0, len(grid) - 1, 2):
            line = []
            for c_ in range(len(grid[0])):
                top, bot = grid[r][c_], grid[r + 1][c_]
                line.append(" " if top and bot else "▄" if top and not bot else "▀" if bot and not top else "█")
            lines.append("".join(line))
        return "\n".join(lines)


QRGEN = QR()


# ===== SECTION: auth =====
PBKDF2_ITER = 240_000


def hash_secret(secret: str, salt: bytes | None = None, iterations: int = PBKDF2_ITER) -> str:
    salt = salt or secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac("sha256", secret.encode("utf-8"), salt, iterations)
    return f"pbkdf2_sha256${iterations}${base64.b64encode(salt).decode()}${base64.b64encode(dk).decode()}"


def verify_secret(secret: str, stored: str | None) -> bool:
    if not stored:
        return False
    try:
        _algo, it, salt, dk = stored.split("$")
        calc = hashlib.pbkdf2_hmac("sha256", secret.encode("utf-8"), base64.b64decode(salt), int(it))
        return hmac.compare_digest(calc, base64.b64decode(dk))
    except (ValueError, TypeError):
        return False


class Auth:
    """Sessions, optional server password, admin elevation and login rate limiting (§7)."""

    COOKIE = "lf_session"

    def __init__(self, app: App) -> None:
        self.app = app
        self.db = app.db
        self.fails: dict[str, list[float]] = {}
        self.lockouts: dict[str, tuple[float, int]] = {}
        self.one_time: dict[str, float] = {}
        self.lock = threading.Lock()

    @property
    def required(self) -> bool:
        return bool(self.app.setting("require_password")) and bool(self.db.kv_get("auth_hash"))

    def new_session(self, ip: str, profile_id: int | None = None, admin: bool = False) -> str:
        token = secrets.token_urlsafe(32)
        days = int(self.app.setting("session_days") or 30)
        self.db.x("INSERT INTO sessions(token_hash,profile_id,created_at,expires_at,ip,admin) VALUES(?,?,?,?,?,?)",
                  (hashlib.sha256(token.encode()).hexdigest(), profile_id, time.time(), time.time() + days * 86400, ip,
                   int(admin)))
        return token

    def lookup(self, token: str | None) -> sqlite3.Row | None:
        if not token or len(token) > 200:
            return None
        r = self.db.one("SELECT * FROM sessions WHERE token_hash=?", (hashlib.sha256(token.encode()).hexdigest(),))
        if r is None or (r["expires_at"] or 0) < time.time():
            return None
        return r

    def update(self, token: str, **fields: Any) -> None:
        sets = ",".join(f"{k}=?" for k in fields)
        self.db.x(f"UPDATE sessions SET {sets} WHERE token_hash=?",
                  list(fields.values()) + [hashlib.sha256(token.encode()).hexdigest()])

    def delete(self, token: str) -> None:
        self.db.x("DELETE FROM sessions WHERE token_hash=?", (hashlib.sha256(token.encode()).hexdigest(),))

    def rate_limited(self, ip: str) -> float | None:
        with self.lock:
            until, _ = self.lockouts.get(ip, (0.0, 0))
            if until > time.time():
                return until - time.time()
            return None

    def record_fail(self, ip: str) -> None:
        with self.lock:
            now = time.time()
            lst = [t for t in self.fails.get(ip, []) if now - t < 60] + [now]
            self.fails[ip] = lst
            if len(lst) >= 5:
                _, n = self.lockouts.get(ip, (0.0, 0))
                n += 1
                self.lockouts[ip] = (now + min(3600, 30 * (2 ** (n - 1))), n)
                self.fails[ip] = []

    def record_ok(self, ip: str) -> None:
        with self.lock:
            self.fails.pop(ip, None)
            self.lockouts.pop(ip, None)

    def check_password(self, pw: str) -> bool:
        return verify_secret(pw, self.db.kv_get("auth_hash"))

    def check_admin_pin(self, pin: str) -> bool:
        """Admin elevation needs the separate admin PIN (PRD §7); without one only loopback is admin."""
        return verify_secret(pin, self.db.kv_get("admin_pin_hash"))

    def make_one_time(self) -> str:
        tok = secrets.token_urlsafe(18)
        with self.lock:
            now = time.time()
            self.one_time = {k: v for k, v in self.one_time.items() if v > now}
            self.one_time[tok] = now + 600
        return tok

    def consume_one_time(self, tok: str) -> bool:
        with self.lock:
            exp = self.one_time.pop(tok, None)
            return bool(exp and exp > time.time())

    def cleanup(self) -> None:
        self.db.x("DELETE FROM sessions WHERE expires_at<?", (time.time(),))


# ===== SECTION: http =====
TV_UA_RE = re.compile(r"Tizen|Web0S|webOS|SMART-TV|SmartTV|NetCast|Android TV|GoogleTV|AFTT|AFTM|AFTS|AFTK|AFTB|"
                      r"Fire TV|CrKey|HbbTV|BRAVIA|PlayStation|Xbox|VIDAA|Roku|\bTV\b", re.IGNORECASE)


def parse_range(header: str | None, size: int) -> tuple[int, int] | None:
    """Parse a single HTTP byte range. Returns (start, end) inclusive, None for full, raises ValueError for 416."""
    if not header:
        return None
    m = re.fullmatch(r"\s*bytes\s*=\s*(\d*)\s*-\s*(\d*)\s*(?:,.*)?", header)
    if not m:
        return None
    a, b = m.group(1), m.group(2)
    if a == "" and b == "":
        return None
    if size == 0:
        raise ValueError("unsatisfiable")
    if a == "":
        n = int(b)
        if n == 0:
            raise ValueError("unsatisfiable")
        return max(0, size - n), size - 1
    start = int(a)
    end = int(b) if b != "" else size - 1
    if start >= size or (b != "" and end < start):
        raise ValueError("unsatisfiable")
    return start, min(end, size - 1)


def host_allowed(host_hdr: str | None, app: App) -> bool:
    """DNS-rebinding guard: accept IP literals, localhost names, this machine's names and the mDNS name."""
    if not host_hdr:
        return True  # HTTP/1.0 clients
    h = host_hdr.strip().lower()
    if h.startswith("["):
        h = h[1:h.find("]")] if "]" in h else h[1:]
    elif h.count(":") == 1:
        h = h.rsplit(":", 1)[0]
    h = h.rstrip(".")
    try:
        ipaddress.ip_address(h.split("%")[0])
        return True
    except ValueError:
        pass
    if h == "localhost" or h.endswith(".localhost"):
        return True
    names = {socket.gethostname().lower(), socket.gethostname().lower().split(".")[0]}
    with contextlib.suppress(OSError):
        names.add(socket.getfqdn().lower())
    mdns = str(app.setting("mdns_name") or "localflix").lower()
    names |= {f"{n}.local" for n in list(names)} | {f"{mdns}.local", mdns}
    return h in names


class HTTPError(Exception):
    def __init__(self, status: int, code: str, message: str = "", headers: dict[str, str] | None = None) -> None:
        super().__init__(message or code)
        self.status, self.code, self.message, self.headers = status, code, message or code, headers or {}


Route = tuple[str, "re.Pattern[str]", str, dict[str, Any]]
ROUTES: list[Route] = []


def route(method: str, pattern: str, **flags: Any) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    def deco(fn: Callable[..., Any]) -> Callable[..., Any]:
        ROUTES.append((method, re.compile("^" + pattern + "$"), fn.__name__, flags))
        return fn
    return deco


class Server(http.server.ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = not PLAT.is_windows
    request_queue_size = 64

    def __init__(self, addr: tuple[str, int], handler: type, app: App) -> None:
        self.app = app
        self.conn_sem = threading.BoundedSemaphore(int(app.setting("max_connections") or 64))
        if ":" in addr[0]:
            self.address_family = socket.AF_INET6
        super().__init__(addr, handler)

    def process_request(self, request: Any, client_address: Any) -> None:
        # never block the accept loop: when every slot is busy, answer 503 and close
        if not self.conn_sem.acquire(blocking=False):
            with contextlib.suppress(OSError):
                if not isinstance(request, ssl.SSLSocket):
                    request.settimeout(1)
                    request.sendall(b"HTTP/1.1 503 Service Unavailable\r\nRetry-After: 2\r\nContent-Length: 0\r\n"
                                    b"Connection: close\r\n\r\n")
                request.close()
            return
        super().process_request(request, client_address)

    def process_request_thread(self, request: Any, client_address: Any) -> None:
        try:
            if isinstance(request, ssl.SSLSocket):  # TLS handshake off the accept loop
                request.settimeout(30)
                try:
                    request.do_handshake()
                except (OSError, ssl.SSLError):
                    with contextlib.suppress(OSError):
                        request.close()
                    return
            super().process_request_thread(request, client_address)
        finally:
            self.app.db.release_thread()
            self.conn_sem.release()

    def handle_error(self, request: Any, client_address: Any) -> None:
        exc = sys.exc_info()[1]
        if isinstance(exc, (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, socket.timeout, ssl.SSLError,
                            TimeoutError)):
            return
        log.debug("HTTP error from %s: %s", client_address, exc)


class Handler(http.server.BaseHTTPRequestHandler):
    """Request handler: routing, auth, compression, ETags, Range, SSE."""

    protocol_version = "HTTP/1.1"
    server_version = f"LocalFlix/{VERSION}"
    sys_version = ""
    timeout = 30
    server: Server

    # --- plumbing -------------------------------------------------------------
    @property
    def app(self) -> App:
        return self.server.app

    def log_message(self, fmt: str, *args: Any) -> None:
        log.debug("%s %s", self.address_string(), fmt % args)

    @property
    def ip(self) -> str:
        return self.client_address[0] if self.client_address else ""

    def do_GET(self) -> None:
        self._dispatch("GET")

    def do_HEAD(self) -> None:
        self._dispatch("HEAD")

    def do_POST(self) -> None:
        self._dispatch("POST")

    def do_PUT(self) -> None:
        self._dispatch("PUT")

    def do_PATCH(self) -> None:
        self._dispatch("PATCH")

    def do_DELETE(self) -> None:
        self._dispatch("DELETE")

    def do_OPTIONS(self) -> None:
        self.send_response(204)
        self.send_header("Allow", "GET, HEAD, POST, PUT, PATCH, DELETE, OPTIONS")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _dispatch(self, method: str) -> None:
        self._resp_cookies: list[str] = []
        self._session_token: str | None = None
        self._session: sqlite3.Row | None = None
        self._raw_body = b""
        self._path = ""
        self._headers_sent = False
        try:
            try:
                n = int(self.headers.get("Content-Length") or 0)
                if n < 0:
                    raise ValueError
            except ValueError:
                self.close_connection = True
                raise HTTPError(400, "bad_request", "Invalid Content-Length") from None
            if n > 5_000_000:
                self.close_connection = True
                raise HTTPError(413, "too_large", "Request body too large")
            if n > 0:
                self._raw_body = self.rfile.read(n)
            parsed = urllib.parse.urlsplit(self.path)
            self.qs = {k: v[-1] for k, v in urllib.parse.parse_qs(parsed.query, keep_blank_values=True).items()}
            path = urllib.parse.unquote(parsed.path)
            self._path = path
            if not host_allowed(self.headers.get("Host"), self.app):
                raise HTTPError(421, "bad_host", "Unknown Host header (DNS rebinding protection)")
            if self.app.setting("private_only") and not is_private_ip(self.ip):
                raise HTTPError(403, "forbidden", "Only private-network clients are allowed")
            m_method = "GET" if method == "HEAD" else method
            for r_method, rx, name, flags in ROUTES:
                if r_method != m_method:
                    continue
                m = rx.match(path)
                if not m:
                    continue
                self._prepare_session(flags)
                if m_method in ("POST", "PUT", "PATCH", "DELETE") and not flags.get("no_csrf"):
                    self._csrf_check(flags)
                if flags.get("admin") and not self.is_admin():
                    raise HTTPError(403, "admin_required", "Administrator access required")
                getattr(self, name)(*m.groups())
                return
            raise HTTPError(404, "not_found", f"No route for {path}")
        except HTTPError as e:
            if self._headers_sent:
                self.close_connection = True
            else:
                self.send_json({"error": {"code": e.code, "message": e.message}}, e.status, headers=e.headers)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, TimeoutError):
            self.close_connection = True
        except Exception as e:  # noqa: BLE001
            log.error("Unhandled error on %s %s: %s\n%s", method, self.path, e, traceback.format_exc())
            if self._headers_sent:
                self.close_connection = True  # response already started: don't corrupt the stream
            else:
                with contextlib.suppress(Exception):
                    self.send_json({"error": {"code": "internal", "message": str(e)[:300]}}, 500)

    def _cookies(self) -> dict[str, str]:
        import http.cookies
        ck = http.cookies.SimpleCookie()
        with contextlib.suppress(http.cookies.CookieError):
            ck.load(self.headers.get("Cookie", ""))
        return {k: v.value for k, v in ck.items()}

    def _set_cookie(self, name: str, value: str, max_age: int) -> None:
        secure = "; Secure" if self.app.tls else ""
        self._resp_cookies.append(f"{name}={value}; Path=/; Max-Age={max_age}; HttpOnly; SameSite=Lax{secure}")

    def _prepare_session(self, flags: dict[str, Any]) -> None:
        auth = self.app.auth
        tok = self._cookies().get(Auth.COOKIE)
        sess = auth.lookup(tok)
        one = self.qs.get("token")
        if one and auth.consume_one_time(one):
            tok = auth.new_session(self.ip)
            sess = auth.lookup(tok)
            self._set_cookie(Auth.COOKIE, tok, int(self.app.setting("session_days") or 30) * 86400)
        media = self._path.startswith(("/api/v1/hls/", "/api/v1/stream/", "/api/v1/images/", "/api/v1/subtitles/",
                                       "/api/v1/trickplay/", "/static/", "/vendor/", "/favicon"))
        if sess is None and not auth.required and not media:  # no session rows for cookieless media fetches
            tok = auth.new_session(self.ip, None, admin=False)
            sess = auth.lookup(tok)
            self._set_cookie(Auth.COOKIE, tok, int(self.app.setting("session_days") or 30) * 86400)
        self._session_token = tok if sess is not None else None
        self._session = sess
        if auth.required and sess is None and not flags.get("public"):
            raise HTTPError(401, "auth_required", "Login required")

    def _csrf_check(self, flags: dict[str, Any]) -> None:
        ctype = (self.headers.get("Content-Type") or "").split(";")[0].strip().lower()
        allowed = {"application/json"} | ({"text/plain"} if flags.get("beacon") else set())
        if ctype not in allowed and int(self.headers.get("Content-Length") or 0) > 0:
            raise HTTPError(415, "unsupported_media_type", "Content-Type must be application/json")
        if ctype not in allowed and self.command not in ("DELETE",) and not flags.get("beacon"):
            raise HTTPError(415, "unsupported_media_type", "Content-Type must be application/json")
        host = (self.headers.get("Host") or "").lower()
        origin = self.headers.get("Origin") or self.headers.get("Referer")
        if origin and (origin == "null" or urllib.parse.urlsplit(origin).netloc.lower() != host):
            raise HTTPError(403, "bad_origin", "Cross-origin request rejected")

    def is_admin(self) -> bool:
        if is_loopback(self.ip) or self.app.is_own_ip(self.ip):
            return True
        if not self.app.setting("require_password"):
            return True
        return bool(self._session is not None and self._session["admin"])

    def profile(self) -> sqlite3.Row:
        db = self.app.db
        pid = None
        if self._session is not None:
            pid = self._session["profile_id"]
        if pid is not None:
            p = db.one("SELECT * FROM profiles WHERE id=?", (pid,))
            if p is not None:
                return p
        if db.one("SELECT COUNT(*) FROM profiles")[0] == 0:
            db.x("INSERT INTO profiles(name,avatar,kids,created_at) VALUES(?,?,0,?)", ("Me", "a1", time.time()))
        # no profile selected yet: never fall back to a PIN-protected profile
        p = db.one("SELECT * FROM profiles WHERE pin_hash IS NULL ORDER BY id LIMIT 1")
        if p is None:
            raise HTTPError(409, "profile_required", "Select a profile first")
        return p

    def body(self, limit: int = 1_000_000) -> Any:
        raw = self._raw_body
        if len(raw) > limit:
            raise HTTPError(413, "too_large", "Request body too large")
        if not raw:
            return {}
        try:
            obj = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            raise HTTPError(400, "bad_json", "Invalid JSON") from None
        if not isinstance(obj, dict):
            raise HTTPError(400, "bad_json", "JSON object expected")
        return obj

    def end_headers(self) -> None:
        self._headers_sent = True
        super().end_headers()

    def _common_headers(self) -> None:
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "same-origin")
        for ck in self._resp_cookies:
            self.send_header("Set-Cookie", ck)

    def send_bytes(self, data: bytes, ctype: str, status: int = 200, cache: str = "no-store",
                   headers: dict[str, str] | None = None, etag: str | None = None, compress: bool = True) -> None:
        if etag:
            inm = self.headers.get("If-None-Match")
            if inm and etag in [x.strip() for x in inm.split(",")]:
                self.send_response(304)
                self.send_header("ETag", etag)
                self.send_header("Cache-Control", cache)
                self._common_headers()
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
        gz = False
        if compress and len(data) > 1024 and "gzip" in (self.headers.get("Accept-Encoding") or "") and \
                any(t in ctype for t in ("json", "text", "javascript", "svg", "mpegurl", "xml")):
            data = gzip.compress(data, compresslevel=5)
            gz = True
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", cache)
        if gz:
            self.send_header("Content-Encoding", "gzip")
            self.send_header("Vary", "Accept-Encoding")
        if etag:
            self.send_header("ETag", etag)
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self._common_headers()
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(data)

    def send_json(self, obj: Any, status: int = 200, headers: dict[str, str] | None = None) -> None:
        self.send_bytes(json.dumps(obj, separators=(",", ":"), default=str).encode("utf-8"),
                        "application/json; charset=utf-8", status, headers=headers)

    def send_file(self, path: Path, ctype: str | None = None, cache: str = "no-store",
                  disposition: str | None = None, extra: list[Path] | None = None) -> None:
        """Serve one file (or a concatenation) with Range, ETag and sendfile."""
        files = [path] + (extra or [])
        try:
            sizes = [os.path.getsize(long_path(f)) for f in files]
            mtime = max(os.path.getmtime(long_path(f)) for f in files)
        except OSError:
            raise HTTPError(404, "not_found", "File not found") from None
        size = sum(sizes)
        ctype = ctype or MIME_MAP.get(path.suffix.lower(), "application/octet-stream")
        etag = f'"{size:x}-{int(mtime * 1000):x}"'
        inm = self.headers.get("If-None-Match")
        if inm and etag in inm and not self.headers.get("Range"):
            self.send_response(304)
            self.send_header("ETag", etag)
            self._common_headers()
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        rng_hdr = self.headers.get("Range")
        if rng_hdr and self.headers.get("If-Range") and self.headers.get("If-Range") != etag:
            rng_hdr = None
        try:
            rng = parse_range(rng_hdr, size)
        except ValueError:
            self.send_response(416)
            self.send_header("Content-Range", f"bytes */{size}")
            self.send_header("Content-Length", "0")
            self._common_headers()
            self.end_headers()
            return
        start, end = rng if rng else (0, size - 1)
        length = max(0, end - start + 1)
        self.send_response(206 if rng else 200)
        self.send_header("Content-Type", ctype)
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(length))
        self.send_header("ETag", etag)
        self.send_header("Last-Modified", email.utils.formatdate(mtime, usegmt=True))
        self.send_header("Cache-Control", cache)
        if rng:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        if disposition:
            self.send_header("Content-Disposition", disposition)
        self._common_headers()
        self.end_headers()
        if self.command == "HEAD" or length == 0:
            return
        self.connection.settimeout(120)
        offset = start
        remaining = length
        base = 0
        for f, fsize in zip(files, sizes):
            if remaining <= 0:
                break
            if offset >= base + fsize:
                base += fsize
                continue
            local_off = offset - base
            n = min(fsize - local_off, remaining)
            with open(long_path(f), "rb") as fh:
                self._sendfile(fh, local_off, n)
            offset += n
            remaining -= n
            base += fsize

    def _sendfile(self, fh: Any, offset: int, count: int) -> None:
        self.wfile.flush()
        if not isinstance(self.connection, ssl.SSLSocket):
            try:
                self.connection.sendfile(fh, offset, count)  # falls back to send() internally
                return
            except (AttributeError, ValueError):
                pass  # not a regular file/socket: use the copy loop below
            except OSError:
                self.close_connection = True  # bytes may already be on the wire: never resend
                raise
        fh.seek(offset)
        left = count
        while left > 0:
            chunk = fh.read(min(262144, left))
            if not chunk:
                break
            self.wfile.write(chunk)
            left -= len(chunk)

    # --- static & SPA -------------------------------------------------------
    def _web_asset(self, name: str) -> bytes:
        dev = self.app.dev_web
        if dev:
            p = Path(dev) / name
            if p.exists():
                return p.read_bytes()
        return WEB_ASSETS[name]

    @route("GET", r"/", public=True)
    def h_index(self) -> None:
        ua = self.headers.get("User-Agent", "")
        tv = bool(TV_UA_RE.search(ua)) or self.qs.get("tv") == "1"
        nonce = secrets.token_urlsafe(12)
        boot = {"version": VERSION, "tv": tv, "name": self.app.setting("server_name"),
                "hlsjs": HLSJS_VERSION, "assets": self.app.asset_versions(), "authRequired": self.app.auth.required}
        page = self._web_asset("index.html").decode("utf-8")
        page = page.replace("__NONCE__", nonce).replace("__BOOT__", json.dumps(boot).replace("</", "<\\/"))
        page = page.replace("__TV__", ' data-tv="1"' if tv else "").replace("__V_CSS__", boot["assets"]["css"])
        page = page.replace("__V_JS__", boot["assets"]["js"]).replace("__NAME__", html.escape(str(boot["name"])))
        csp = (f"default-src 'self'; script-src 'self' 'nonce-{nonce}'; style-src 'self' 'unsafe-inline'; "
               "img-src 'self' data: blob:; media-src 'self' blob: data:; connect-src 'self'; worker-src 'self' blob:; "
               "font-src 'self' data:; object-src 'none'; base-uri 'self'; frame-ancestors 'self'")
        self.send_bytes(page.encode("utf-8"), "text/html; charset=utf-8", cache="no-cache",
                        headers={"Content-Security-Policy": csp})

    @route("GET", r"/tv", public=True)
    def h_tv(self) -> None:
        self.send_response(302)
        self.send_header("Location", "/?tv=1#/home")
        self.send_header("Content-Length", "0")
        self._common_headers()
        self.end_headers()

    @route("GET", r"/static/(app\.js|app\.css|favicon\.svg|manifest\.webmanifest)", public=True)
    def h_static(self, name: str) -> None:
        data = self._web_asset(name)
        ext = Path(name).suffix
        ctype = MIME_MAP.get(ext, "application/manifest+json" if ext == ".webmanifest" else "text/plain")
        cache = "public, max-age=31536000, immutable" if self.qs.get("v") and not self.app.dev_web else "no-cache"
        self.send_bytes(data, ctype, cache=cache, etag=f'"{hashlib.sha1(data).hexdigest()[:16]}"')

    @route("GET", r"/favicon\.ico", public=True)
    def h_favicon(self) -> None:
        self.send_bytes(WEB_ASSETS["favicon.svg"], "image/svg+xml", cache="public, max-age=86400")

    @route("GET", r"/vendor/hls\.min\.js", public=True)
    def h_hlsjs(self) -> None:
        p = self.app.paths.vendor / "hls.min.js"
        if not p.exists():
            raise HTTPError(404, "hlsjs_missing", "hls.js is not cached yet")
        self.send_file(p, "text/javascript; charset=utf-8", cache="public, max-age=31536000, immutable")

    # --- system ----------------------------------------------------------
    @route("GET", r"/api/v1/system/info", public=True)
    def h_info(self) -> None:
        app = self.app
        authed = not app.auth.required or self._session is not None
        info: dict[str, Any] = {"name": app.setting("server_name"), "version": VERSION, "authRequired": app.auth.required,
                                "authenticated": authed}
        if authed:
            info.update({
                "platform": PLAT.describe(), "os": PLAT.name, "python": platform.python_version(),
                "ffmpeg": {"available": app.ff.available, "version": app.ff.version, "path": app.ff.ffmpeg,
                           "hw": app.ff.pick_hw(str(app.setting("hwaccel"))), "hwResults": app.ff.hw_results},
                "urls": app.urls(), "port": app.port, "uptime": int(time.time() - START_TIME),
                "hlsjs": {"version": HLSJS_VERSION, "cached": (app.paths.vendor / "hls.min.js").exists()},
                "qr": True, "admin": self.is_admin(), "scan": app.scanner.status,
                "profilesEnabled": bool(app.setting("profiles_enabled")),
                "installHint": install_hint(), "tmdb": app.tmdb.enabled,
                "openSecurity": not app.setting("require_password") and app.host in ("0.0.0.0", "::"),
                "mediaDirs": [r["path"] for r in app.db.q("SELECT path FROM libraries WHERE enabled=1")],
                "capabilities": {"fts": app.db.fts, "trickplay": bool(app.ff.ffmpeg), "transcode": app.ff.available,
                                 "hlsMode": app.setting("hls_mode")},
            })
        self.send_json(info)

    @route("GET", r"/api/v1/system/doctor", admin=True)
    def h_doctor(self) -> None:
        self.send_json({"checks": run_doctor(self.app, quick=self.qs.get("full") != "1")})

    @route("GET", r"/api/v1/system/logs", admin=True)
    def h_logs(self) -> None:
        self.send_json({"lines": list(RING.lines)})

    @route("POST", r"/api/v1/system/restart", admin=True)
    def h_restart(self) -> None:
        self.send_json({"ok": True})
        threading.Timer(0.5, self.app.restart).start()

    @route("POST", r"/api/v1/system/hw-detect", admin=True)
    def h_hwdetect(self) -> None:
        res = self.app.ff.detect_hw(self.app.db, force=True)
        self.send_json({"results": res, "selected": self.app.ff.pick_hw(str(self.app.setting("hwaccel")))})

    @route("GET", r"/api/v1/events")
    def h_events(self) -> None:
        q = self.app.events.subscribe()
        try:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Accel-Buffering", "no")
            self._common_headers()
            self.end_headers()
            self.close_connection = True
            if self.command == "HEAD":
                return
            self.connection.settimeout(60)
            self.wfile.write(b"retry: 3000\n\n")
            self.wfile.flush()
            while not self.app.stopping.is_set():
                try:
                    ev, data = q.get(timeout=15)
                    payload = f"event: {ev}\ndata: {json.dumps(data, default=str)}\n\n"
                except queue.Empty:
                    payload = f"event: heartbeat\ndata: {int(time.time())}\n\n"
                self.wfile.write(payload.encode("utf-8"))
                self.wfile.flush()
        except (TimeoutError, BrokenPipeError, ConnectionResetError, ConnectionAbortedError, OSError):
            pass
        finally:
            self.app.events.unsubscribe(q)

    # --- auth & profiles -----------------------------------------------------
    @route("GET", r"/api/v1/auth/status", public=True)
    def h_auth_status(self) -> None:
        s = self._session
        prof = None
        if s is not None and s["profile_id"] is not None:
            p = self.app.db.one("SELECT id,name,avatar,kids FROM profiles WHERE id=?", (s["profile_id"],))
            prof = dict(p) if p else None
        self.send_json({"required": self.app.auth.required, "authenticated": s is not None or not self.app.auth.required,
                        "profile": prof, "admin": self.is_admin(), "profilesEnabled": bool(self.app.setting("profiles_enabled")),
                        "loopback": is_loopback(self.ip) or self.app.is_own_ip(self.ip), "hasPassword": bool(self.app.db.kv_get("auth_hash")),
                        "hasAdminPin": bool(self.app.db.kv_get("admin_pin_hash"))})

    @route("POST", r"/api/v1/auth/login", public=True)
    def h_login(self) -> None:
        auth = self.app.auth
        wait = auth.rate_limited(self.ip)
        if wait:
            raise HTTPError(429, "rate_limited", f"Too many attempts. Try again in {int(wait) + 1}s",
                            {"Retry-After": str(int(wait) + 1)})
        b = self.body()
        if not auth.check_password(str(b.get("password", ""))[:200]):
            auth.record_fail(self.ip)
            time.sleep(0.3)
            raise HTTPError(401, "bad_credentials", "Wrong password")
        auth.record_ok(self.ip)
        tok = auth.new_session(self.ip)
        self._set_cookie(Auth.COOKIE, tok, int(self.app.setting("session_days") or 30) * 86400)
        self.send_json({"ok": True})

    @route("POST", r"/api/v1/auth/logout", public=True)
    def h_logout(self) -> None:
        if self._session_token:
            self.app.auth.delete(self._session_token)
        self._set_cookie(Auth.COOKIE, "", 0)
        self.send_json({"ok": True})

    @route("POST", r"/api/v1/auth/admin")
    def h_admin_login(self) -> None:
        auth = self.app.auth
        wait = auth.rate_limited(self.ip)
        if wait:
            raise HTTPError(429, "rate_limited", f"Too many attempts. Try again in {int(wait) + 1}s")
        b = self.body()
        if not auth.check_admin_pin(str(b.get("pin", ""))[:200]):
            auth.record_fail(self.ip)
            raise HTTPError(401, "bad_credentials", "Wrong admin PIN")
        auth.record_ok(self.ip)
        if self._session_token:
            auth.update(self._session_token, admin=1)
        self.send_json({"ok": True})

    @route("POST", r"/api/v1/auth/password", admin=True)
    def h_set_password(self) -> None:
        b = self.body()
        db = self.app.db
        if "password" in b:
            pw = str(b.get("password") or "")
            if pw:
                if len(pw) < 4:
                    raise HTTPError(400, "too_short", "Password must have at least 4 characters")
                db.kv_set("auth_hash", hash_secret(pw))
                if self._session_token:
                    self.app.auth.update(self._session_token, admin=1)
            else:
                db.x("DELETE FROM kv WHERE key='auth_hash'")
                db.set_setting("require_password", False)
        if "adminPin" in b:
            pin = str(b.get("adminPin") or "")
            if pin:
                db.kv_set("admin_pin_hash", hash_secret(pin))
            else:
                db.x("DELETE FROM kv WHERE key='admin_pin_hash'")
        if "require" in b:
            if b["require"] and not db.kv_get("auth_hash"):
                raise HTTPError(400, "no_password", "Set a password first")
            db.set_setting("require_password", bool(b["require"]))
        if "password" in b and self._session_token:  # password changed/removed: sign out every other session
            db.x("DELETE FROM sessions WHERE token_hash<>?", (hashlib.sha256(self._session_token.encode()).hexdigest(),))
        self.app.invalidate_settings()
        self.send_json({"ok": True, "required": self.app.auth.required})

    @route("POST", r"/api/v1/auth/qr-token", admin=True)
    def h_qr_token(self) -> None:
        tok = self.app.auth.make_one_time()
        urls = self.app.urls()
        base = urls[0] if urls else f"http://localhost:{self.app.port}"
        self.send_json({"token": tok, "url": f"{base}/?token={tok}", "expiresIn": 600})

    def _profile_json(self, p: sqlite3.Row) -> dict[str, Any]:
        return {"id": p["id"], "name": p["name"], "avatar": p["avatar"], "kids": bool(p["kids"]),
                "hasPin": bool(p["pin_hash"])}

    @route("GET", r"/api/v1/profiles")
    def h_profiles(self) -> None:
        if self.app.db.one("SELECT COUNT(*) FROM profiles")[0] == 0:
            self.app.db.x("INSERT INTO profiles(name,avatar,kids,created_at) VALUES(?,?,0,?)", ("Me", "a1", time.time()))
        self.send_json({"profiles": [self._profile_json(p) for p in self.app.db.q("SELECT * FROM profiles ORDER BY id")],
                        "enabled": bool(self.app.setting("profiles_enabled"))})

    @route("POST", r"/api/v1/profiles", admin=True)
    def h_profile_create(self) -> None:
        b = self.body()
        name = str(b.get("name") or "").strip()[:30]
        if not name:
            raise HTTPError(400, "bad_name", "Name required")
        if self.app.db.one("SELECT COUNT(*) FROM profiles")[0] >= 12:
            raise HTTPError(400, "too_many", "Maximum 12 profiles")
        pin = str(b.get("pin") or "")
        if pin and not re.fullmatch(r"\d{4}", pin):
            raise HTTPError(400, "bad_pin", "PIN must be 4 digits")
        cur = self.app.db.x("INSERT INTO profiles(name,avatar,pin_hash,kids,created_at) VALUES(?,?,?,?,?)",
                            (name, str(b.get("avatar") or "a1")[:20], hash_secret(pin) if pin else None,
                             int(bool(b.get("kids"))), time.time()))
        p = self.app.db.one("SELECT * FROM profiles WHERE id=?", (cur.lastrowid,))
        self.send_json(self._profile_json(p), 201)

    @route("PATCH", r"/api/v1/profiles/(\d+)")
    def h_profile_patch(self, pid: str) -> None:
        own = self._session is not None and self._session["profile_id"] == int(pid)
        if not own and not self.is_admin():
            raise HTTPError(403, "forbidden", "Not allowed")
        b = self.body()
        db = self.app.db
        p = db.one("SELECT * FROM profiles WHERE id=?", (int(pid),))
        if p is None:
            raise HTTPError(404, "not_found", "Profile not found")
        if "name" in b:
            db.x("UPDATE profiles SET name=? WHERE id=?", (str(b["name"]).strip()[:30] or p["name"], p["id"]))
        if "avatar" in b:
            db.x("UPDATE profiles SET avatar=? WHERE id=?", (str(b["avatar"])[:20], p["id"]))
        if "kids" in b and self.is_admin():
            db.x("UPDATE profiles SET kids=? WHERE id=?", (int(bool(b["kids"])), p["id"]))
        if "pin" in b:
            pin = str(b["pin"] or "")
            if pin and not re.fullmatch(r"\d{4}", pin):
                raise HTTPError(400, "bad_pin", "PIN must be 4 digits")
            db.x("UPDATE profiles SET pin_hash=? WHERE id=?", (hash_secret(pin) if pin else None, p["id"]))
        if b.get("clearHistory"):
            db.x("DELETE FROM watch_state WHERE profile_id=?", (p["id"],))
        if b.get("clearList"):
            db.x("DELETE FROM mylist WHERE profile_id=?", (p["id"],))
        self.send_json(self._profile_json(db.one("SELECT * FROM profiles WHERE id=?", (p["id"],))))

    @route("DELETE", r"/api/v1/profiles/(\d+)", admin=True)
    def h_profile_delete(self, pid: str) -> None:
        if self.app.db.one("SELECT COUNT(*) FROM profiles")[0] <= 1:
            raise HTTPError(400, "last_profile", "Cannot delete the last profile")
        self.app.db.x("DELETE FROM profiles WHERE id=?", (int(pid),))
        self.app.db.x("DELETE FROM settings WHERE scope=?", (f"profile:{pid}",))
        self.send_json({"ok": True})

    @route("POST", r"/api/v1/profiles/(\d+)/select")
    def h_profile_select(self, pid: str) -> None:
        db = self.app.db
        p = db.one("SELECT * FROM profiles WHERE id=?", (int(pid),))
        if p is None:
            raise HTTPError(404, "not_found", "Profile not found")
        if p["pin_hash"]:
            auth = self.app.auth
            wait = auth.rate_limited(self.ip)
            if wait:
                raise HTTPError(429, "rate_limited", f"Too many attempts. Try again in {int(wait) + 1}s")
            if not verify_secret(str(self.body().get("pin", "")), p["pin_hash"]):
                auth.record_fail(self.ip)
                raise HTTPError(401, "bad_pin", "Wrong PIN")
            auth.record_ok(self.ip)
        if self._session_token:
            self.app.auth.update(self._session_token, profile_id=p["id"])
        self.send_json(self._profile_json(p))

    # --- library browse ----------------------------------------------------
    @route("GET", r"/api/v1/home")
    def h_home(self) -> None:
        self.send_json(self.app.library.home(self.profile()))

    @route("GET", r"/api/v1/items")
    def h_items(self) -> None:
        try:
            self.send_json(self.app.library.items(self.profile(), self.qs))
        except ValueError:
            raise HTTPError(400, "bad_params", "Invalid parameters") from None

    @route("GET", r"/api/v1/search")
    def h_search(self) -> None:
        self.send_json(self.app.library.search(self.profile(), self.qs.get("q", "")))

    @route("GET", r"/api/v1/genres")
    def h_genres(self) -> None:
        counts: dict[str, int] = collections.Counter()
        for r in self.app.db.q("SELECT genres_json FROM items WHERE genres_json IS NOT NULL AND missing_at IS NULL"):
            for g in json.loads(r[0] or "[]"):
                counts[g] += 1
        folders = [r[0] for r in self.app.db.q("SELECT DISTINCT folder FROM items WHERE folder<>'' AND missing_at IS NULL "
                                               "ORDER BY folder LIMIT 100")]
        self.send_json({"genres": [g for g, _ in counts.most_common()], "folders": folders})

    def _warm_item(self, iid: str | None, prio: int) -> None:
        """Opening a details page means play is likely: build its keyframe table now (remux needs it)."""
        if not iid or not self.app.ff.available:
            return
        r = self.app.db.one("SELECT i.rel_path, i.container, l.path AS lib FROM items i JOIN libraries l "
                            "ON l.id=i.library_id WHERE i.id=?", (iid,))
        if r is None or (r["container"] or "") in ("mp4", "mov", "webm"):
            return
        p = safe_join(Path(r["lib"]), r["rel_path"])
        if p is not None:
            self.app.scanner.warm_keyframes(iid, p, prio=prio)

    @route("GET", r"/api/v1/items/([0-9a-f]{16})")
    def h_item(self, iid: str) -> None:
        d = self.app.library.details(self.profile(), iid)
        if d is None:
            raise HTTPError(404, "not_found", "Item not found")
        self._warm_item(iid, 0)
        self.send_json(d)

    @route("GET", r"/api/v1/series/([0-9a-f]{16})")
    def h_series(self, sid: str) -> None:
        d = self.app.library.series(self.profile(), sid)
        if d is None:
            raise HTTPError(404, "not_found", "Series not found")
        self._warm_item((d.get("nextUp") or {}).get("id"), 0)
        self.send_json(d)

    @route("GET", r"/api/v1/series/([0-9a-f]{16})/seasons/(\d+)")
    def h_season(self, sid: str, n: str) -> None:
        d = self.app.library.series(self.profile(), sid, int(n))
        if d is None:
            raise HTTPError(404, "not_found", "Series not found")
        self.send_json(d)

    # --- playback ----------------------------------------------------------
    def _item_file(self, iid: str) -> tuple[sqlite3.Row, Path]:
        row = self.app.db.one("SELECT i.*, l.path AS lib_path FROM items i JOIN libraries l ON l.id=i.library_id "
                              "WHERE i.id=? AND l.enabled=1", (iid,))
        if row is None:
            raise HTTPError(404, "not_found", "Item not found")
        p = safe_join(Path(row["lib_path"]), row["rel_path"])
        if p is None or not p.is_file():
            raise HTTPError(404, "file_missing", "Media file is missing")
        return row, p

    @route("POST", r"/api/v1/items/([0-9a-f]{16})/playback")
    def h_playback(self, iid: str) -> None:
        app = self.app
        prof = self.profile()
        caps = self.body()
        row, path = self._item_file(iid)
        if row["probed_at"] is None:
            app.scanner.probe_item(iid)
            row, path = self._item_file(iid)
        if row["unplayable"] and app.ff.available:
            raise HTTPError(422, "unplayable", row["unplayable"])
        info = json.loads(row["probe_json"] or "{}") or {"audio": [], "subs": [], "video": None,
                                                         "container": path.suffix.lower().lstrip(".")}
        info.setdefault("audio", [])
        info.setdefault("subs", [])
        audio_list, sub_list = app.library.streams_info(iid)
        scope = f"profile:{prof['id']}"
        us = app.db.all_settings(scope)
        ws = app.db.one("SELECT * FROM watch_state WHERE profile_id=? AND item_id=?", (prof["id"], iid))
        a_idx = caps.get("audioIndex")
        if a_idx is None:
            a_idx = pick_audio(audio_list, us, ws)
        s_idx = caps.get("subtitleIndex", "auto")
        if s_idx == "auto":
            s_idx = pick_subtitle(sub_list, audio_list, a_idx, us, ws)
        caps2 = dict(caps, audioIndex=a_idx, subtitleIndex=s_idx)
        item = dict(info, size=row["size"], faststart=None if row["faststart"] is None else bool(row["faststart"]))
        opts = {"ffmpeg": app.ff.available, "allow_transcode": us.get("allow_transcode", True),
                "allow_passthrough": us.get("allow_passthrough"), "prefer_direct": us.get("prefer_direct", True),
                "quality": caps.get("quality") or us.get("max_quality"), "tonemap": us.get("tonemap", True),
                "burn_in": us.get("burn_in", True), "force": caps.get("force"), "termux": PLAT.is_termux}
        dec = decide_playback(item, caps2, opts)
        dec["clientAudio"] = [x.lower() for x in caps.get("audio") or []]
        start = float(caps.get("startTime") or 0)
        resp: dict[str, Any] = {"method": dec["method"], "reason": dec["reason"], "audio": audio_list,
                                "subtitles": sub_list, "audioIndex": dec["audioIndex"],
                                "subtitleIndex": s_idx if isinstance(s_idx, int) else None,
                                "durationSec": info.get("duration") or row["duration_s"] or 0, "timeOffset": 0,
                                "mode": "direct", "itemId": iid}
        if dec["method"] == "direct":
            resp["url"] = f"/api/v1/stream/{iid}/direct"
            resp["sessionId"] = None
            app.hls.touch_direct(iid)
        else:
            sess = app.hls.create(iid, path, info, dec, start, replace=caps.get("replaceSession"))
            sess.prestart()
            resp.update({"sessionId": sess.id, "url": f"/api/v1/hls/{iid}/{sess.id}/master.m3u8", "mode": sess.mode,
                         "timeOffset": sess.start_time, "videoMode": sess.video_mode,
                         "hw": None if sess.video_mode != "transcode" or sess.sw_fallback else sess.hw})
        ch = [{"title": c_["title"], "start": c_["start_s"], "end": c_["end_s"]} for c_ in app.db.q(
            "SELECT * FROM chapters WHERE item_id=? ORDER BY idx", (iid,))]
        resp["chapters"] = ch
        resp["markers"] = skip_markers(ch, resp["durationSec"], row["kind"] == "episode")
        if app.setting("trickplay") and app.ff.ffmpeg and info.get("video"):
            ti = app.artwork.trick_index(iid)
            if ti:
                resp["trickplay"] = dict(ti, url=f"/api/v1/trickplay/{iid}/")
            else:
                app.artwork.enqueue(iid, prio=2, job="trick")
                resp["trickplay"] = None
        # warm subtitle extraction for the chosen track
        if isinstance(s_idx, int):
            threading.Thread(target=self._warm_sub, args=(iid, s_idx), daemon=True).start()
        resp["resume"] = (ws["position_s"] if ws and not ws["watched"] else 0) or 0
        lib = app.library
        r2 = app.db.one(f"SELECT {lib.ITEM_COLS} FROM items i LEFT JOIN series s ON s.id=i.series_id WHERE i.id=?", (iid,))
        nxt = lib.next_episode(r2) if r2 else None
        prv = lib.prev_episode(r2) if r2 else None
        resp["item"] = lib.cards([r2], prof["id"])[0] if r2 else None
        resp["next"] = lib.cards([nxt], prof["id"])[0] if nxt else None
        if nxt:
            self._warm_item(nxt["id"], 1)  # next episode starts instantly too
        resp["prevId"] = prv["id"] if prv else None
        log.info("Playback %s: %s (%s)", row["rel_path"], dec["method"], ",".join(dec["reason"]))
        self.send_json(resp)

    def _warm_sub(self, iid: str, sidx: int) -> None:
        with contextlib.suppress(Exception):
            self.app.subtitle_vtt(iid, sidx)

    @route("GET", r"/api/v1/stream/([0-9a-f]{16})/direct")
    def h_direct(self, iid: str) -> None:
        _row, path = self._item_file(iid)
        self.app.hls.touch_direct(iid)
        ctype = MIME_MAP.get(path.suffix.lower(), "application/octet-stream")
        if path.suffix.lower() == ".mov":
            ctype = "video/mp4"
        fname = urllib.parse.quote(path.name)
        self.send_file(path, ctype, cache="private, max-age=3600", disposition=f"inline; filename*=UTF-8''{fname}")

    @route("GET", r"/api/v1/hls/([0-9a-f]{16})/([A-Za-z0-9]{6,20})/([A-Za-z0-9_.]+)")
    def h_hls(self, iid: str, sid: str, name: str) -> None:
        s = self.app.hls.get(sid)
        if s is None or s.item_id != iid:
            raise HTTPError(404, "session_gone", "Playback session expired")
        s.last_access = time.time()
        m3u8 = "application/vnd.apple.mpegurl"
        if name == "master.m3u8":
            _, subs = self.app.library.streams_info(iid)
            text = [x for x in subs if x["kind"] == "text"]
            return self.send_bytes(s.master(text).encode(), m3u8)
        if s.mode == "simple":
            outdir = s.dir / "simple"
            try:
                job = s.ensure_simple()
            except FileNotFoundError:
                self.app.hls.stop_session(sid)
                raise HTTPError(404, "session_gone", "Playback session expired") from None
            except TranscodeBusy as e:
                raise HTTPError(503, "transcode_busy", str(e), {"Retry-After": "5"}) from None
            if name == "stream.m3u8":
                deadline = time.time() + 40
                while time.time() < deadline:
                    p = outdir / "stream.m3u8"
                    if p.exists():
                        txt = p.read_text(errors="ignore")
                        if txt.count("#EXTINF") >= 1 or "#EXT-X-ENDLIST" in txt:
                            return self.send_bytes(txt.encode(), m3u8)
                    if job.finished and job.returncode not in (0, None):
                        if s.video_mode == "transcode" and s.hw and not s.sw_fallback:
                            job = s.ensure_simple()
                            continue
                        raise HTTPError(500, "ffmpeg_failed", " ".join(job.stderr)[-300:] or "ffmpeg failed")
                    time.sleep(0.1)
                raise HTTPError(503, "not_ready", "Stream is still preparing", {"Retry-After": "2"})
            if not re.fullmatch(r"s_(init\.mp4|\d{5}\.(m4s|ts))", name):
                raise HTTPError(404, "not_found", "Unknown file")
            p = outdir / name
            deadline = time.time() + 20
            while not p.exists() and time.time() < deadline:
                time.sleep(0.05)
            if not p.exists():
                raise HTTPError(503, "not_ready", "Segment not ready", {"Retry-After": "1"})
            job.last_request = time.time()
            ms = re.match(r"s_(\d{5})\.", name)
            if ms:  # throttle relative to what the player actually fetches
                job.requested = int(ms.group(1))
                if job.paused and job.head - job.requested <= int(self.app.setting("lookahead") or 12):
                    job.set_paused(False)
            return self.send_file(p, MIME_MAP.get(p.suffix, "video/mp4"), cache="private, max-age=3600")
        if name == "video.m3u8" and s.video_mode:
            return self.send_bytes(s.video_playlist().encode(), m3u8)
        m = re.fullmatch(r"audio_(\d+)\.m3u8", name)
        if m:
            k = int(m.group(1))
            if k >= len(s.audio):
                raise HTTPError(404, "not_found", "No such audio track")
            return self.send_bytes(s.audio_playlist(k).encode(), m3u8)
        m = re.fullmatch(r"sub_(\d+)\.m3u8", name)
        if m:
            dur = max(1.0, s.duration)
            pl = (f"#EXTM3U\n#EXT-X-VERSION:3\n#EXT-X-TARGETDURATION:{int(dur) + 1}\n#EXT-X-MEDIA-SEQUENCE:0\n"
                  f"#EXT-X-PLAYLIST-TYPE:VOD\n#EXTINF:{dur:.3f},\n/api/v1/subtitles/{iid}/{m.group(1)}.vtt?hls=1\n"
                  f"#EXT-X-ENDLIST\n")
            return self.send_bytes(pl.encode(), m3u8)
        m = re.fullmatch(r"init_(video|audio_\d+)\.mp4", name)
        if m:
            try:
                p = s.init_file(m.group(1))
            except TranscodeBusy as e:
                raise HTTPError(503, "transcode_busy", str(e), {"Retry-After": "5"}) from None
            if p is None:
                raise HTTPError(503, "not_ready", "Init segment not ready", {"Retry-After": "1"})
            return self.send_file(p, "video/mp4", cache="private, max-age=3600")
        m = re.fullmatch(r"seg_(video|audio_\d+)_(\d+)\.m4s", name)
        if m:
            rend, n = m.group(1), int(m.group(2))
            table = s.vsegs if rend == "video" else s.asegs
            if n >= len(table) or (rend != "video" and int(rend.split("_")[1]) >= len(s.audio)):
                raise HTTPError(404, "not_found", "Segment out of range")
            try:
                files = s.get_segment(rend, n)
            except TranscodeBusy as e:
                raise HTTPError(503, "transcode_busy", str(e), {"Retry-After": "5"}) from None
            if files is None:
                if s.error:
                    raise HTTPError(500, "ffmpeg_failed", s.error)
                raise HTTPError(503, "not_ready", "Segment not ready yet", {"Retry-After": "1"})
            return self.send_file(files[0], "video/iso.segment" if s.fmp4 else "video/mp2t",
                                  cache="private, max-age=3600", extra=files[1:])
        raise HTTPError(404, "not_found", "Unknown HLS resource")

    @route("DELETE", r"/api/v1/hls/([A-Za-z0-9]{6,20})")
    def h_hls_stop(self, sid: str) -> None:
        self.send_json({"ok": self.app.hls.stop_session(sid)})

    @route("POST", r"/api/v1/hls/([A-Za-z0-9]{6,20})/stop", beacon=True)
    def h_hls_stop_beacon(self, sid: str) -> None:
        self.send_json({"ok": self.app.hls.stop_session(sid)})

    @route("GET", r"/api/v1/subtitles/([0-9a-f]{16})/(\d+)\.vtt")
    def h_subtitle(self, iid: str, n: str) -> None:
        try:
            vtt = self.app.subtitle_vtt(iid, int(n))
        except FileNotFoundError:
            raise HTTPError(404, "not_found", "Subtitle not found") from None
        except RuntimeError as e:
            raise HTTPError(500, "subtitle_failed", str(e)) from None
        if self.qs.get("hls") == "1" and "X-TIMESTAMP-MAP" not in vtt:
            vtt = vtt.replace("WEBVTT", "WEBVTT\nX-TIMESTAMP-MAP=MPEGTS:0,LOCAL:00:00:00.000", 1)
        self.send_bytes(vtt.encode("utf-8"), "text/vtt; charset=utf-8", cache="private, max-age=600")

    @route("GET", r"/api/v1/images/([0-9a-f]{16})/(poster|backdrop|thumb)")
    def h_image(self, oid: str, kind: str) -> None:
        w = None
        with contextlib.suppress(ValueError):
            w = int(self.qs.get("w") or 0) or None
        webp_ok = "image/webp" in (self.headers.get("Accept") or "")
        p = self.app.artwork.image_for(oid, kind, w, webp_ok)
        if p is None:
            raise HTTPError(404, "no_image", "No artwork yet")
        self.send_file(p, cache="private, max-age=86400")

    @route("GET", r"/api/v1/trickplay/([0-9a-f]{16})/(index\.json|sheet_\d{3}\.jpg)")
    def h_trick(self, iid: str, name: str) -> None:
        p = self.app.artwork.trick_dir(iid) / name
        if not p.exists():
            raise HTTPError(404, "not_ready", "Trickplay not ready")
        self.send_file(p, cache="private, max-age=86400")

    # --- watch state -------------------------------------------------------------
    @route("POST", r"/api/v1/progress", beacon=True)
    def h_progress(self) -> None:
        b = self.body()
        prof = self.profile()
        iid = str(b.get("itemId") or "")
        if not re.fullmatch(r"[0-9a-f]{16}", iid):
            raise HTTPError(400, "bad_item", "itemId required")
        try:
            pos = max(0.0, float(b.get("position") or 0))
            dur = max(0.0, float(b.get("duration") or 0))
            if not (math.isfinite(pos) and math.isfinite(dur)) or pos > 1e7 or dur > 1e7:
                raise ValueError
        except (TypeError, ValueError):
            raise HTTPError(400, "bad_params", "position/duration must be numbers") from None
        state = str(b.get("state") or "")
        db = self.app.db
        ws = db.one("SELECT * FROM watch_state WHERE profile_id=? AND item_id=?", (prof["id"], iid))
        watched = 1 if (dur > 0 and pos / dur >= 0.92) or state == "ended" else (ws["watched"] if ws else 0)
        if ws and ws["watched"] and pos > 15 and dur and pos < dur * 0.92 and state not in ("ended",):
            watched = 0  # rewatching
        a_idx = b.get("audioIndex") if isinstance(b.get("audioIndex"), int) else (ws["audio_idx"] if ws else None)
        s_idx = b.get("subtitleIndex") if isinstance(b.get("subtitleIndex"), int) else (ws["sub_idx"] if ws else None)
        db.x("INSERT INTO watch_state(profile_id,item_id,position_s,duration_s,watched,last_played_at,audio_idx,sub_idx) "
             "VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(profile_id,item_id) DO UPDATE SET position_s=excluded.position_s,"
             "duration_s=excluded.duration_s,watched=excluded.watched,last_played_at=excluded.last_played_at,"
             "audio_idx=excluded.audio_idx,sub_idx=excluded.sub_idx",
             (prof["id"], iid, pos, dur, watched, time.time(), a_idx, s_idx))
        if b.get("sessionId"):
            s = self.app.hls.get(str(b["sessionId"]))
            if s is not None:
                s.last_access = time.time()
        if state in ("ended", "stopped", "paused"):
            self.app.events.publish("progress.updated", {"profileId": prof["id"], "itemId": iid})
        self.send_json({"ok": True, "watched": bool(watched)})

    @route("POST", r"/api/v1/items/([0-9a-f]{16})/watched")
    def h_watched(self, iid: str) -> None:
        b = self.body()
        prof = self.profile()
        w = 1 if b.get("watched", True) else 0
        db = self.app.db
        ids = [iid]
        if db.one("SELECT 1 FROM series WHERE id=?", (iid,)):
            ids = [r[0] for r in db.q("SELECT id FROM items WHERE series_id=?", (iid,))]
        for x in ids:
            db.x("INSERT INTO watch_state(profile_id,item_id,position_s,duration_s,watched,last_played_at) "
                 "VALUES(?,?,0,(SELECT duration_s FROM items WHERE id=?),?,?) ON CONFLICT(profile_id,item_id) DO UPDATE "
                 "SET watched=excluded.watched, position_s=0, last_played_at=excluded.last_played_at",
                 (prof["id"], x, x, w, time.time()))
        self.app.events.publish("progress.updated", {"profileId": prof["id"], "itemId": iid})
        self.send_json({"ok": True, "watched": bool(w)})

    @route("POST", r"/api/v1/mylist/([0-9a-f]{16})")
    def h_mylist_add(self, iid: str) -> None:
        self.app.db.x("INSERT OR IGNORE INTO mylist(profile_id,item_id,added_at) VALUES(?,?,?)",
                      (self.profile()["id"], iid, time.time()))
        self.send_json({"ok": True, "inList": True})

    @route("DELETE", r"/api/v1/mylist/([0-9a-f]{16})")
    def h_mylist_del(self, iid: str) -> None:
        self.app.db.x("DELETE FROM mylist WHERE profile_id=? AND item_id=?", (self.profile()["id"], iid))
        self.send_json({"ok": True, "inList": False})

    # --- settings -------------------------------------------------------------
    @route("GET", r"/api/v1/settings/schema")
    def h_settings_schema(self) -> None:
        self.send_json({"settings": SETTINGS_SPEC, "admin": self.is_admin()})

    @route("GET", r"/api/v1/settings")
    def h_settings_get(self) -> None:
        prof = self.profile()
        vals = self.app.db.all_settings(f"profile:{prof['id']}")
        if vals.get("tmdb_key"):
            vals["tmdb_key"] = "••••" + str(vals["tmdb_key"])[-4:]
        self.send_json({"values": vals, "admin": self.is_admin(), "profileId": prof["id"]})

    @route("PUT", r"/api/v1/settings")
    def h_settings_put(self) -> None:
        b = self.body()
        prof = self.profile()
        scope_req = self.qs.get("scope", "user")
        errors: dict[str, str] = {}
        changed: dict[str, Any] = {}
        restart = False
        for k, v in (b.get("values") or b).items():
            spec = SPEC_BY_KEY.get(k)
            if spec is None:
                errors[k] = "unknown setting"
                continue
            if spec["type"] == "secret" and isinstance(v, str) and v.startswith("••••"):
                continue
            try:
                val = validate_setting(k, v)
            except ValueError as e:
                errors[k] = str(e)
                continue
            if spec["scope"] == "user" and scope_req != "server":
                self.app.db.set_setting(k, val, f"profile:{prof['id']}")
            else:
                if not self.is_admin():
                    errors[k] = "admin required"
                    continue
                if k == "require_password" and val and not self.app.db.kv_get("auth_hash"):
                    errors[k] = "Set a password first"
                    continue
                self.app.db.set_setting(k, val)
                restart = restart or bool(spec.get("restart"))
            changed[k] = val
        self.app.invalidate_settings()
        self.app.apply_settings(changed)
        if changed:
            self.app.events.publish("settings.changed", {"keys": list(changed), "profileId": prof["id"]})
        self.send_json({"ok": not errors, "errors": errors, "changed": list(changed), "restartRequired": restart},
                       200 if not errors or changed else 400)

    @route("GET", r"/api/v1/settings/export", admin=True)
    def h_settings_export(self) -> None:
        data = {"version": VERSION, "settings": [dict(r) for r in self.app.db.q("SELECT * FROM settings")],
                "libraries": [dict(r) for r in self.app.db.q("SELECT path,type,name,enabled FROM libraries")]}
        self.send_bytes(json.dumps(data, indent=2).encode(), "application/json",
                        headers={"Content-Disposition": "attachment; filename=localflix-settings.json"})

    @route("POST", r"/api/v1/settings/import", admin=True)
    def h_settings_import(self) -> None:
        b = self.body(5_000_000)
        n = 0
        for r in b.get("settings", []):
            k = r.get("key")
            if k in SPEC_BY_KEY:
                with contextlib.suppress(ValueError, TypeError):
                    val = validate_setting(k, json.loads(r.get("value_json") or "null"))
                    self.app.db.set_setting(k, val, str(r.get("scope") or "server")[:40])
                    n += 1
        self.app.invalidate_settings()
        self.send_json({"ok": True, "imported": n})

    @route("POST", r"/api/v1/settings/reset", admin=True)
    def h_settings_reset(self) -> None:
        self.app.db.x("DELETE FROM settings WHERE scope='server'")
        self.app.invalidate_settings()
        self.send_json({"ok": True})

    # --- libraries -----------------------------------------------------------
    @route("POST", r"/api/v1/library/scan", admin=True)
    def h_scan(self) -> None:
        b = self.body()
        self.app.scanner.trigger(bool(b.get("full")))
        self.send_json({"ok": True, "status": self.app.scanner.status})

    @route("GET", r"/api/v1/libraries", admin=True)
    def h_libs(self) -> None:
        out = []
        for r in self.app.db.q("SELECT l.*, (SELECT COUNT(*) FROM items i WHERE i.library_id=l.id AND i.missing_at IS NULL) "
                               "AS n FROM libraries l ORDER BY id"):
            d = dict(r)
            d["exists"] = os.path.isdir(r["path"])
            d["readable"] = os.access(r["path"], os.R_OK)
            d["nestedIn"] = next((o["path"] for o in self.app.db.q("SELECT path FROM libraries WHERE enabled=1 AND id<>?",
                                                                   (r["id"],))
                                  if Path(r["path"]).is_relative_to(Path(o["path"]))), None)
            out.append(d)
        self.send_json({"libraries": out, "scan": self.app.scanner.status, "scope": self.app.setting("library_scope")})

    @route("GET", r"/api/v1/libraries/discover", admin=True)
    def h_lib_discover(self) -> None:
        scope = self.qs.get("scope") or str(self.app.setting("library_scope"))
        if scope not in ("folders", "common", "all"):
            raise HTTPError(400, "bad_scope", "scope must be folders, common or all")
        self.send_json({"scope": scope, "folders": discover_media_roots(scope)})

    @route("POST", r"/api/v1/libraries", admin=True)
    def h_lib_add(self) -> None:
        b = self.body()
        p = Path(str(b.get("path") or "")).expanduser()
        if not str(p) or not p.is_absolute():
            raise HTTPError(400, "bad_path", "Absolute folder path required")
        if not p.exists():
            if b.get("create"):
                p.mkdir(parents=True, exist_ok=True)
            else:
                raise HTTPError(400, "not_found", "Folder does not exist")
        if not p.is_dir() or not os.access(p, os.R_OK):
            raise HTTPError(400, "unreadable", "Folder is not readable" +
                            (" — run termux-setup-storage" if PLAT.is_termux else ""))
        typ = b.get("type") if b.get("type") in ("mixed", "movies", "shows") else "mixed"
        rp = str(p.resolve())
        ex = self.app.db.one("SELECT * FROM libraries WHERE path=?", (rp,))
        if ex is not None and ex["source"] == "user" and ex["enabled"]:
            raise HTTPError(409, "exists", "Folder already added")
        if ex is not None:  # auto-detected or disabled folder: becomes a regular user folder
            self.app.db.x("UPDATE libraries SET source='user', enabled=1, user_disabled=0, type=? WHERE id=?",
                          (typ, ex["id"]))
        else:
            self.app.db.x("INSERT INTO libraries(path,type,name,enabled,added_at,source) VALUES(?,?,?,1,?,'user')",
                          (rp, typ, str(b.get("name") or p.name)[:60], time.time()))
        self.app.scanner.trigger()
        self.send_json({"ok": True}, 201)

    @route("PATCH", r"/api/v1/libraries/(\d+)", admin=True)
    def h_lib_patch(self, lid: str) -> None:
        b = self.body()
        db = self.app.db
        if "enabled" in b:
            en = int(bool(b["enabled"]))
            # for auto folders remember the user's choice so discovery never re-enables them
            db.x("UPDATE libraries SET enabled=?, user_disabled=CASE WHEN source='auto' THEN ? ELSE 0 END WHERE id=?",
                 (en, 1 - en, int(lid)))
            if en:
                self.app.scanner.trigger()
        if b.get("type") in ("mixed", "movies", "shows"):
            db.x("UPDATE libraries SET type=? WHERE id=?", (b["type"], int(lid)))
            self.app.scanner.trigger(full=True)
        if "name" in b:
            db.x("UPDATE libraries SET name=? WHERE id=?", (str(b["name"])[:60], int(lid)))
        self.app.events.publish("library.updated", {})
        self.send_json({"ok": True})

    @route("DELETE", r"/api/v1/libraries/(\d+)", admin=True)
    def h_lib_del(self, lid: str) -> None:
        db = self.app.db
        row = db.one("SELECT source FROM libraries WHERE id=?", (int(lid),))
        if row is not None and row["source"] == "auto":
            # auto folders would be rediscovered: hide instead (keeps watch history if re-enabled)
            db.x("UPDATE libraries SET enabled=0, user_disabled=1 WHERE id=?", (int(lid),))
            self.app.events.publish("library.updated", {})
            return self.send_json({"ok": True, "disabled": True})
        if db.fts:
            db.x("DELETE FROM fts_items WHERE id IN (SELECT id FROM items WHERE library_id=?)", (int(lid),))
        db.x("DELETE FROM libraries WHERE id=?", (int(lid),))
        self.app.scanner._cleanup_series()
        self.app.events.publish("library.updated", {})
        self.send_json({"ok": True})

    @route("GET", r"/api/v1/fs/browse", admin=True)
    def h_browse(self) -> None:
        raw = self.qs.get("path", "")
        blocked = ("/proc", "/sys", "/dev", "/run", "/boot", "/etc", "C:\\Windows")
        if not raw:
            roots: list[dict[str, str]] = []
            if PLAT.is_windows:
                import string
                for d in string.ascii_uppercase:
                    if os.path.exists(f"{d}:\\"):
                        roots.append({"name": f"{d}:\\", "path": f"{d}:\\"})
            else:
                roots.append({"name": "/", "path": "/"})
            for label, p in (("Home", Path.home()), ("LocalFlix folder", SCRIPT_DIR),
                             ("Shared storage", Path.home() / "storage" / "shared"), ("Media", Path("/media")),
                             ("Mounts", Path("/mnt")), ("Volumes", Path("/Volumes"))):
                if p.is_dir():
                    roots.append({"name": label, "path": str(p)})
            return self.send_json({"path": "", "parent": None, "dirs": roots})
        p = Path(raw).expanduser()
        try:
            p = p.resolve()
        except OSError:
            raise HTTPError(400, "bad_path", "Invalid path") from None
        if any(str(p).lower().startswith(b.lower()) for b in blocked if b != "/") and str(p) not in ("/",):
            raise HTTPError(403, "blocked", "System folder")
        if not p.is_dir():
            raise HTTPError(404, "not_found", "Folder not found")
        dirs = []
        videos = 0
        try:
            for e in sorted(os.scandir(p), key=lambda e: e.name.lower()):
                if e.name.startswith("."):
                    continue
                with contextlib.suppress(OSError):
                    if e.is_dir():
                        dirs.append({"name": e.name, "path": e.path})
                    elif os.path.splitext(e.name)[1].lower() in VIDEO_EXTS:
                        videos += 1
                if len(dirs) > 500:
                    break
        except PermissionError:
            raise HTTPError(403, "denied", "Permission denied" + (" — run termux-setup-storage" if PLAT.is_termux else "")) \
                from None
        parent = str(p.parent) if p.parent != p else ""
        self.send_json({"path": str(p), "parent": parent, "dirs": dirs, "videos": videos})

    @route("POST", r"/api/v1/cache/clear", admin=True)
    def h_cache_clear(self) -> None:
        b = self.body()
        kinds = b.get("kinds") or ["images", "hls", "subs", "trickplay"]
        paths = self.app.paths
        m = {"images": paths.images, "hls": paths.hls, "subs": paths.subs, "trickplay": paths.trickplay,
             "keyframes": paths.keyframes, "tmdb": paths.tmdb}
        for k in kinds:
            if k == "hls":
                for sid in list(self.app.hls.sessions):
                    self.app.hls.stop_session(sid)
            d = m.get(k)
            if d is not None:
                shutil.rmtree(d, ignore_errors=True)
                d.mkdir(parents=True, exist_ok=True)
            if k == "images":
                self.app.db.x("DELETE FROM artwork WHERE source IN ('generated','tmdb')")
                for r in self.app.db.q("SELECT id FROM items WHERE missing_at IS NULL"):
                    self.app.artwork.enqueue(r[0], prio=7)
        self.send_json({"ok": True})

    @route("POST", r"/api/v1/metadata/refresh", admin=True)
    def h_meta_refresh(self) -> None:
        if not self.app.tmdb.enabled:
            raise HTTPError(400, "no_key", "Set a TMDB key first")
        self.app.db.x("UPDATE series SET tmdb_id=NULL")
        for r in self.app.db.q("SELECT id FROM items WHERE missing_at IS NULL"):
            self.app.tmdb.enqueue(r[0])
        self.send_json({"ok": True})

    @route("GET", r"/api/v1/qr")
    def h_qr(self) -> None:
        url = self.qs.get("url", "")[:300]
        if not url:
            raise HTTPError(400, "bad_url", "url required")
        try:
            svg = QRGEN.svg(url)
        except ValueError as e:
            raise HTTPError(400, "too_long", str(e)) from None
        self.send_bytes(svg.encode(), "image/svg+xml", cache="private, max-age=3600")


def pick_audio(audio: list[dict[str, Any]], us: dict[str, Any], ws: sqlite3.Row | None) -> int | None:
    """Auto-pick an audio track: remembered → preferred language → fallbacks → default → first."""
    if not audio:
        return None
    if ws is not None and ws["audio_idx"] is not None and 0 <= ws["audio_idx"] < len(audio):
        return int(ws["audio_idx"])
    langs = [lang_norm(us.get("audio_lang") or "")] + [lang_norm(x) for x in us.get("audio_lang_fallback") or []]
    for lg in langs:
        if not lg:
            continue
        for a in audio:
            if lang_norm(a["lang"]) == lg:
                return a["index"]
    for a in audio:
        if a["default"]:
            return a["index"]
    return audio[0]["index"]


def pick_subtitle(subs: list[dict[str, Any]], audio: list[dict[str, Any]], a_idx: int | None,
                  us: dict[str, Any], ws: sqlite3.Row | None) -> int | None:
    """Auto-pick subtitles per the Off / Auto / Always preference (−1 in watch state = explicitly off)."""
    if not subs:
        return None
    if ws is not None and ws["sub_idx"] is not None:
        v = int(ws["sub_idx"])
        return v if 0 <= v < len(subs) else None
    mode = us.get("subtitle_mode") or "auto"
    pref = lang_norm(us.get("subtitle_lang") or "eng")
    a_lang = ""
    if a_idx is not None:
        for a in audio:
            if a["index"] == a_idx:
                a_lang = lang_norm(a["lang"])
    text = [s for s in subs if s["kind"] == "text"] or subs
    if mode == "off":
        return None
    if mode == "always" or (a_lang and a_lang != lang_norm(us.get("audio_lang") or "") and a_lang != pref):
        for s in text:
            if lang_norm(s["lang"]) == pref and not s["forced"]:
                return s["index"]
        for s in text:
            if lang_norm(s["lang"]) == pref:
                return s["index"]
        if mode == "always":
            return text[0]["index"]
    for s in subs:  # forced subs in the audio language
        if s["forced"] and a_lang and lang_norm(s["lang"]) == a_lang:
            return s["index"]
    return None


_INTRO_RE = re.compile(r"\b(intro|opening|op|title sequence|recap|cold open)\b", re.IGNORECASE)
_CREDITS_RE = re.compile(r"\b(credits|ending|ed|outro|end credits|closing)\b", re.IGNORECASE)


def skip_markers(chapters: list[dict[str, Any]], duration: float, episode: bool) -> dict[str, Any]:
    out: dict[str, Any] = {"intro": None, "credits": None}
    for ch in chapters:
        t = ch.get("title") or ""
        if out["intro"] is None and _INTRO_RE.search(t) and (ch["start"] < duration * 0.4 or duration == 0):
            out["intro"] = {"start": ch["start"], "end": ch["end"]}
        elif out["credits"] is None and _CREDITS_RE.search(t) and ch["start"] > duration * 0.5:
            out["credits"] = {"start": ch["start"], "end": ch["end"]}
    if out["credits"] is None and episode and duration > 300:
        out["credits"] = {"start": max(0.0, duration - 45), "end": duration, "fallback": True}
    return out


# ===== SECTION: install-helpers =====
def install_hint() -> dict[str, Any]:
    """The one-line ffmpeg install command for this OS / package manager (§5.2)."""
    if PLAT.is_termux:
        return {"manager": "pkg", "cmd": ["pkg", "install", "-y", "ffmpeg"], "text": "pkg install ffmpeg"}
    if PLAT.is_windows:
        for mgr, cmd in (("winget", ["winget", "install", "--id", "Gyan.FFmpeg", "-e", "--accept-source-agreements",
                                     "--accept-package-agreements"]),
                         ("scoop", ["scoop", "install", "ffmpeg"]), ("choco", ["choco", "install", "ffmpeg", "-y"])):
            if shutil.which(mgr):
                return {"manager": mgr, "cmd": cmd, "text": " ".join(cmd[:4] if mgr == "winget" else cmd)}
        return {"manager": "download", "cmd": None, "text": "winget install Gyan.FFmpeg"}
    if PLAT.is_mac:
        return {"manager": "brew", "cmd": ["brew", "install", "ffmpeg"] if shutil.which("brew") else None,
                "text": "brew install ffmpeg"}
    for mgr, cmd in (("apt-get", ["sudo", "apt-get", "install", "-y", "ffmpeg"]),
                     ("dnf", ["sudo", "dnf", "install", "-y", "ffmpeg"]),
                     ("pacman", ["sudo", "pacman", "-S", "--noconfirm", "ffmpeg"]),
                     ("apk", ["sudo", "apk", "add", "ffmpeg"]), ("zypper", ["sudo", "zypper", "install", "-y", "ffmpeg"])):
        if shutil.which(mgr):
            if os.geteuid() == 0 if hasattr(os, "geteuid") else False:
                cmd = cmd[1:]
            return {"manager": mgr, "cmd": cmd, "text": " ".join(cmd)}
    return {"manager": "download", "cmd": None, "text": "Install ffmpeg with your package manager"}


def download_static_ffmpeg(paths: Paths, progress: Callable[[str], None] = print) -> bool:
    """Download a checksum-verified static ffmpeg build (BtbN) into ``<config>/bin`` (Windows/Linux x64/arm64)."""
    base = "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/"
    arm = PLAT.machine.lower() in ("arm64", "aarch64")
    if PLAT.is_windows:
        plat = "winarm64" if arm else "win64"
    elif PLAT.is_linux and PLAT.machine.lower() in ("x86_64", "amd64", "aarch64", "arm64"):
        plat = "linuxarm64" if arm else "linux64"
    else:
        progress("No static build available for this platform.")
        return False
    try:
        with urllib.request.urlopen(base + "checksums.sha256", timeout=30) as r:
            sums = r.read().decode()
    except (urllib.error.URLError, OSError) as e:
        progress(f"Could not reach the download server: {e}")
        return False
    best: tuple[tuple[int, ...], str, str] | None = None
    for line in sums.splitlines():
        m = re.match(rf"([0-9a-f]{{64}})\s+(ffmpeg-n([\d.]+)-latest-{plat}-gpl-[\d.]+\.(?:zip|tar\.xz))$", line.strip())
        if m:
            ver = tuple(int(x) for x in m.group(3).split("."))
            if best is None or ver > best[0]:
                best = (ver, m.group(2), m.group(1))
    if best is None:
        progress("No matching release build found.")
        return False
    _, name, digest = best
    paths.bin.mkdir(parents=True, exist_ok=True)
    tmp = paths.tmp / name
    progress(f"Downloading {name} …")
    try:
        h = hashlib.sha256()
        with urllib.request.urlopen(base + name, timeout=60) as r, open(tmp, "wb") as f:
            total = int(r.headers.get("Content-Length") or 0)
            got = 0
            while True:
                chunk = r.read(1 << 20)
                if not chunk:
                    break
                f.write(chunk)
                h.update(chunk)
                got += len(chunk)
                if total and sys.stdout.isatty():
                    sys.stdout.write(f"\r  {got * 100 // total:3d}%  ({got >> 20} / {total >> 20} MB)")
                    sys.stdout.flush()
        print()
    except (urllib.error.URLError, OSError) as e:
        progress(f"Download failed: {e}")
        return False
    if h.hexdigest() != digest:
        progress("Checksum mismatch — refusing to install the download.")
        tmp.unlink(missing_ok=True)
        return False
    progress("Checksum verified ✓ — extracting …")
    exe = ".exe" if PLAT.is_windows else ""
    wanted = {f"ffmpeg{exe}", f"ffprobe{exe}"}
    try:
        if name.endswith(".zip"):
            import zipfile
            with zipfile.ZipFile(tmp) as z:
                for info in z.infolist():
                    if Path(info.filename).name in wanted and "/bin/" in info.filename:
                        (paths.bin / Path(info.filename).name).write_bytes(z.read(info))
        else:
            import tarfile
            with tarfile.open(tmp) as t:
                for m_ in t.getmembers():
                    if Path(m_.name).name in wanted and "/bin/" in m_.name and m_.isfile():
                        src = t.extractfile(m_)
                        if src:
                            out = paths.bin / Path(m_.name).name
                            out.write_bytes(src.read())
                            out.chmod(0o755)
    finally:
        tmp.unlink(missing_ok=True)
    ok = all((paths.bin / w).exists() for w in wanted)
    progress("ffmpeg installed into " + str(paths.bin) if ok else "Extraction failed.")
    return ok


def fetch_hlsjs(paths: Paths, quiet: bool = False) -> bool:
    """Download the pinned hls.js into the cache and verify its SHA-384 (§12.4)."""
    dest = paths.vendor / "hls.min.js"
    if dest.exists() and verify_hlsjs(dest):
        return True
    for url in HLSJS_URLS:
        try:
            req = urllib.request.Request(url, headers={"User-Agent": f"LocalFlix/{VERSION}"})
            with urllib.request.urlopen(req, timeout=30) as r:
                data = r.read()
        except (urllib.error.URLError, OSError) as e:
            if not quiet:
                log.info("hls.js download from %s failed: %s", url, e)
            continue
        digest = base64.b64encode(hashlib.sha384(data).digest()).decode()
        if digest != HLSJS_SHA384:
            log.warning("hls.js checksum mismatch from %s", url)
            continue
        paths.vendor.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_suffix(".tmp")
        tmp.write_bytes(data)
        os.replace(tmp, dest)
        (paths.vendor / "hls.min.js.sha384").write_text(digest)
        log.info("hls.js %s cached", HLSJS_VERSION)
        return True
    return False


def verify_hlsjs(p: Path) -> bool:
    try:
        return base64.b64encode(hashlib.sha384(p.read_bytes()).digest()).decode() == HLSJS_SHA384
    except OSError:
        return False


# ===== SECTION: doctor =====
def run_doctor(app: App | None, quick: bool = True, paths: Paths | None = None) -> list[dict[str, str]]:
    """Diagnostics (§5.3): each check → ok / warn / fail with an actionable fix."""
    checks: list[dict[str, str]] = []

    def add(name: str, status: str, detail: str, fix: str = "") -> None:
        checks.append({"name": name, "status": status, "detail": detail, "fix": fix})

    paths = paths or (app.paths if app else Paths())
    pv = sys.version_info
    add("Python", "ok" if pv >= (3, 10) else "fail", f"{platform.python_version()} ({platform.python_implementation()}, "
        f"{platform.python_build()[1]})", "" if pv >= (3, 10) else "Install Python 3.10 or newer")
    add("Operating system", "ok", PLAT.describe())
    ff = app.ff if app else FFmpeg(paths)
    hint = install_hint()
    if ff.ffmpeg:
        major = int(re.match(r"n?(\d+)", ff.version).group(1)) if re.match(r"n?(\d+)", ff.version or "") else 0
        add("ffmpeg", "ok" if major >= 5 or not major else "warn", f"{ff.ffmpeg} (version {ff.version})",
            "" if major >= 5 or not major else f"Old ffmpeg — upgrade: {hint['text']}")
    else:
        add("ffmpeg", "fail", "not found — direct play only", f"Install: {hint['text']}  (or run: localflix.py setup)")
    add("ffprobe", "ok" if ff.ffprobe else "fail", ff.ffprobe or "not found", "" if ff.ffprobe else f"Install: {hint['text']}")
    if ff.ffmpeg:
        have = [e for e in DOCTOR_ENCODERS if e in ff.encoders]
        add("Encoders", "ok" if "libx264" in ff.encoders else "warn", ", ".join(have) or "none",
            "" if "libx264" in ff.encoders else "Your ffmpeg lacks libx264 — install a full build")
        add("HW accelerators", "ok", ", ".join(ff.hwaccels) or "none")
        if quick and app is not None and app.ff.hw_results:
            res = app.ff.hw_results
        else:
            res = ff.detect_hw(app.db if app else None, force=not quick)
        if res:
            for k, v in res.items():
                add(f"HW encode test: {k}", "ok" if v else "warn", "works" if v else "failed — software fallback",
                    "" if v else "Update GPU drivers or set Hardware acceleration = none")
        else:
            add("HW encode", "warn", "no hardware encoder — software encoding will be used",
                "Fine for 1080p on most PCs; lower quality on weak devices")
        if not quick:
            ok = ff.test_software("libx264")
            add("Test encode libx264", "ok" if ok else "fail", "1 s testsrc " + ("succeeded" if ok else "failed"))
        add("Tone-mapping filters", "ok" if {"zscale", "tonemap"} <= ff.filters else "warn",
            "zscale+tonemap available" if {"zscale", "tonemap"} <= ff.filters else "zscale missing — HDR→SDR limited",
            "" if {"zscale", "tonemap"} <= ff.filters else "Install an ffmpeg build with libzimg")
    add("SQLite FTS5", "ok" if has_fts5() else "warn", f"sqlite {sqlite3.sqlite_version}",
        "" if has_fts5() else "Search falls back to LIKE (slower on huge libraries)")
    port = app.port if app else int(os.environ.get("LOCALFLIX_PORT") or 8000)
    if app and app.httpd:
        add("Port", "ok", f"listening on {app.host}:{port}")
    else:
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.bind(("0.0.0.0", port))
            s.close()
            add("Port", "ok", f"{port} is free")
        except OSError:
            add("Port", "warn", f"{port} is in use", "LocalFlix will try the next free port automatically")
    ips = lan_ips()
    add("LAN addresses", "ok" if ips else "warn", ", ".join(f"http://{ip}:{port}" for ip in ips) or "none found",
        "" if ips else "Connect to Wi-Fi / hotspot. Other devices must be on the same network")
    if PLAT.is_windows:
        add("Firewall", "warn", "Windows may block incoming connections",
            f'Run as admin: netsh advfirewall firewall add rule name="LocalFlix" dir=in action=allow protocol=TCP '
            f'localport={port}')
    elif PLAT.is_mac:
        add("Firewall", "warn", "macOS may prompt to allow incoming connections",
            "System Settings → Network → Firewall → Options → allow Python")
    elif PLAT.is_termux:
        add("Termux", "ok" if shutil.which("termux-wake-lock") else "warn", "wake-lock " +
            ("available" if shutil.which("termux-wake-lock") else "missing"),
            "Disable battery optimisation for Termux; install Termux:API for wake-lock")
    if app:
        for r in app.db.q("SELECT path FROM libraries"):
            p = Path(r[0])
            ok = p.is_dir() and os.access(p, os.R_OK)
            add(f"Library {p}", "ok" if ok else "fail", "readable" if ok else "missing or unreadable",
                "" if ok else ("Run termux-setup-storage" if PLAT.is_termux else "Check the folder path / permissions"))
    try:
        paths.cache.mkdir(parents=True, exist_ok=True)
        free = shutil.disk_usage(paths.cache).free
        add("Cache folder", "ok" if free > 2 * 1024 ** 3 else "warn",
            f"{paths.cache} — {free / 1024 ** 3:.1f} GB free, writable={os.access(paths.cache, os.W_OK)}",
            "" if free > 2 * 1024 ** 3 else "Free some disk space or lower the cache limit")
    except OSError as e:
        add("Cache folder", "fail", str(e))
    hp = paths.vendor / "hls.min.js"
    add("hls.js", "ok" if hp.exists() and verify_hlsjs(hp) else "warn",
        f"{HLSJS_VERSION} cached" if hp.exists() else "not cached — native HLS only until downloaded",
        "" if hp.exists() else "Connect to the internet once; it downloads automatically")
    opt = {"segno (QR)": segno is not None, "zeroconf (mDNS)": zeroconf is not None}
    try:
        import chardet  # type: ignore  # noqa: F401
        opt["chardet"] = True
    except ImportError:
        opt["chardet"] = False
    add("Optional libraries", "ok", ", ".join(f"{k}: {'yes' if v else 'no'}" for k, v in opt.items()),
        "Optional: pip install segno zeroconf")
    return checks


def print_doctor(checks: list[dict[str, str]]) -> None:
    icon = {"ok": c("✅", "green"), "warn": c("⚠️ ", "yellow"), "fail": c("❌", "red")}
    print(c(f"\n{APP_NAME} doctor — v{VERSION}\n", "bold"))
    for ch in checks:
        print(f" {icon[ch['status']]} {c(ch['name'], 'bold')}: {ch['detail']}")
        if ch["fix"] and ch["status"] != "ok":
            print(f"      → {ch['fix']}")
    print()


# ===== SECTION: app =====
class App:
    """Wires together DB, ffmpeg, workers and the HTTP server."""

    def __init__(self, paths: Paths, dev_web: str | None = None) -> None:
        self.paths = paths
        paths.ensure()
        self.db = Database(paths.db)
        self._settings: dict[str, Any] | None = None
        self._slock = threading.Lock()
        self.ff = FFmpeg(paths, str(self.setting("ffmpeg_path") or ""), str(self.setting("ffprobe_path") or ""))
        self.events = EventBus()
        self.sleep = SleepInhibitor()
        self.library = Library(self)
        self.scanner = Scanner(self)
        self.artwork = ArtworkWorker(self)
        self.tmdb = Tmdb(self)
        self.hls = HlsManager(self)
        self.auth = Auth(self)
        self.stopping = threading.Event()
        self.dev_web = dev_web
        self.httpd: Server | None = None
        self.port = 8000
        self.host = "0.0.0.0"
        self.tls = False
        self._sub_locks: dict[str, threading.Lock] = collections.defaultdict(threading.Lock)
        self._asset_v: dict[str, str] | None = None
        self._zc: Any = None
        self._restart_flag = False
        self._own_ips: set[str] = set()
        self._own_ips_at = 0.0

    # --- settings ---------------------------------------------------------------
    def setting(self, key: str) -> Any:
        s = self._settings
        if s is None:
            with self._slock:
                s = self._settings = self.db.all_settings("server")
        return s.get(key, SPEC_BY_KEY.get(key, {}).get("default"))

    def invalidate_settings(self) -> None:
        self._settings = None

    def apply_settings(self, changed: dict[str, Any]) -> None:
        if "log_level" in changed:
            for h in log.handlers:
                if not isinstance(h, logging.StreamHandler) or isinstance(h, logging.FileHandler):
                    h.setLevel(getattr(logging, str(changed["log_level"]), logging.INFO))
        if "prevent_sleep" in changed:
            self.sleep.enabled = bool(changed["prevent_sleep"])
        if "ffmpeg_path" in changed or "ffprobe_path" in changed:
            self.ff = FFmpeg(self.paths, str(self.setting("ffmpeg_path") or ""), str(self.setting("ffprobe_path") or ""))
            self.ff.detect_hw(self.db)
        if changed.get("tmdb_key"):
            for r in self.db.q("SELECT id FROM items WHERE missing_at IS NULL AND tmdb_id IS NULL"):
                self.tmdb.enqueue(r[0])
        if "library_scope" in changed or "auto_min_size_mb" in changed:
            self.scanner.trigger()
        if "library_mode" in changed or "ignore_patterns" in changed or "include_hidden" in changed:
            self.scanner.trigger(full=True)
        if "scan_interval" in changed:
            self.scanner.trigger()

    def asset_versions(self) -> dict[str, str]:
        if self._asset_v is None or self.dev_web:
            self._asset_v = {k.split(".")[1]: hashlib.sha1(WEB_ASSETS[k]).hexdigest()[:10] for k in ("app.js", "app.css")}
            if self.dev_web:
                self._asset_v = {k: str(int(time.time())) for k in self._asset_v}
        return self._asset_v

    def is_own_ip(self, ip: str) -> bool:
        """True when a request comes from this machine itself (e.g. its browser opened the LAN URL)."""
        now = time.time()
        if now - self._own_ips_at > 60:
            ips: set[str] = set(lan_ips())
            with contextlib.suppress(OSError):
                for info in socket.getaddrinfo(socket.gethostname(), None):
                    ips.add(str(info[4][0]))
            self._own_ips, self._own_ips_at = ips, now
        return ip.split("%")[0].replace("::ffff:", "") in self._own_ips

    def urls(self) -> list[str]:
        scheme = "https" if self.tls else "http"
        if self.host in ("127.0.0.1", "localhost", "::1"):
            return [f"{scheme}://localhost:{self.port}"]
        if self.host not in ("0.0.0.0", "::", ""):
            return [f"{scheme}://{self.host}:{self.port}"]
        return [f"{scheme}://{ip}:{self.port}" for ip in lan_ips()] or [f"{scheme}://localhost:{self.port}"]

    # --- subtitles ----------------------------------------------------------------
    def subtitle_vtt(self, iid: str, sidx: int) -> str:
        row = self.db.one("SELECT i.*, l.path AS lib_path FROM items i JOIN libraries l ON l.id=i.library_id WHERE i.id=?",
                          (iid,))
        if row is None:
            raise FileNotFoundError(iid)
        st = self.db.one("SELECT * FROM streams WHERE item_id=? AND type='s' AND sidx=?", (iid, sidx))
        if st is None or st["bitmap_flag"]:
            raise FileNotFoundError(f"{iid}/{sidx}")
        media = safe_join(Path(row["lib_path"]), row["rel_path"])
        if media is None:
            raise FileNotFoundError(iid)
        src = Path(st["external_path"]) if st["external_path"] else media
        try:
            mt = src.stat().st_mtime_ns
        except OSError:
            raise FileNotFoundError(str(src)) from None
        cache = self.paths.subs / f"{iid}_{sidx}_{stable_id(str(src), mt)}.vtt"
        if cache.exists():
            return cache.read_text(encoding="utf-8")
        with self._sub_locks[f"{iid}:{sidx}"]:
            if cache.exists():
                return cache.read_text(encoding="utf-8")
            if st["external_path"]:
                if not media.parent.resolve() in src.resolve().parents and src.resolve().parent != media.parent.resolve():
                    raise FileNotFoundError("external subtitle outside library")
                vtt = convert_subtitle_file(src, float(row["v_fps"] or 23.976))
            else:
                if not self.ff.ffmpeg:
                    raise RuntimeError("ffmpeg is required to extract embedded subtitles")
                codec = (st["codec"] or "").lower()
                is_ass = codec in ("ass", "ssa")
                args = [self.ff.ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "error", "-i", long_path(media),
                        "-map", f"0:{st['idx']}"]
                args += ["-c:s", "ass", "-f", "ass", "-"] if is_ass else ["-c:s", "srt", "-f", "srt", "-"]
                rc, out, err = run_capture(args, timeout=600, low_priority=True)
                if rc != 0:
                    raise RuntimeError((err.decode(errors="replace").strip().splitlines() or ["extract failed"])[0])
                text = decode_text(out)
                vtt = ass_to_vtt(text) if is_ass else srt_to_vtt(text)
            tmp = cache.with_suffix(".tmp")
            tmp.write_text(vtt, encoding="utf-8")
            os.replace(tmp, cache)
            return vtt

    # --- lifecycle --------------------------------------------------------------
    def ensure_defaults(self, folders: list[str] | None = None) -> None:
        if folders:
            for f in folders:
                p = Path(f).expanduser().resolve()
                p.mkdir(parents=True, exist_ok=True)
                self.db.x("INSERT OR IGNORE INTO libraries(path,type,name,enabled,added_at) VALUES(?,?,?,1,?)",
                          (str(p), "mixed", p.name, time.time()))
                self.db.x("UPDATE libraries SET enabled=1, source='user', user_disabled=0 WHERE path=?", (str(p),))
        if self.db.one("SELECT COUNT(*) FROM libraries")[0] == 0 and not self.db.kv_get("default_lib_added"):
            # first run: exactly one folder (./output); more via Settings → Libraries or the library scope
            p = default_media_dir()
            with contextlib.suppress(OSError):
                p.mkdir(parents=True, exist_ok=True)
            self.db.x("INSERT OR IGNORE INTO libraries(path,type,name,enabled,added_at) VALUES(?,?,?,1,?)",
                      (str(p.resolve()), "mixed", "Videos", time.time()))
        self.db.kv_set("default_lib_added", "1")
        if self.db.one("SELECT COUNT(*) FROM profiles")[0] == 0:
            self.db.x("INSERT INTO profiles(name,avatar,kids,created_at) VALUES(?,?,0,?)", ("Me", "a1", time.time()))

    def start_background(self) -> None:
        self.sleep.enabled = bool(self.setting("prevent_sleep"))
        self.hls.start()
        self.artwork.start()
        self.scanner.start()

        def bg() -> None:
            if self.ff.ffmpeg:
                self.ff.detect_hw(self.db)
            delay = 5
            while not self.stopping.is_set():
                if fetch_hlsjs(self.paths, quiet=True):
                    break
                self.stopping.wait(min(600, delay))
                delay *= 2
        threading.Thread(target=bg, name="bootstrap", daemon=True).start()

        def nightly() -> None:
            while not self.stopping.wait(6 * 3600):
                self.db.backup_watch_state()
                self.auth.cleanup()
        threading.Thread(target=nightly, name="backup", daemon=True).start()

    def bind(self, host: str, port: int) -> None:
        last: OSError | None = None
        for p in range(port, port + 21):
            try:
                self.httpd = Server((host, p), Handler, self)
                self.port = p
                self.host = host
                if p != port:
                    print(c(f"Port {port} is busy — using {p} instead.", "yellow"))
                return
            except OSError as e:
                last = e
                if getattr(e, "errno", None) in (13, 10013) and p < 1024:
                    break
        raise SystemExit(f"Could not bind a port in {port}-{port + 20}: {last}")

    def enable_tls(self, cert: str, key: str) -> None:
        assert self.httpd
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(cert, key)
        self.httpd.socket = ctx.wrap_socket(self.httpd.socket, server_side=True, do_handshake_on_connect=False)
        self.tls = True

    def advertise_mdns(self) -> None:
        if zeroconf is None or self.host in ("127.0.0.1", "localhost"):
            return
        try:
            ips = lan_ips()
            if not ips:
                return
            name = str(self.setting("mdns_name") or "localflix")
            info = zeroconf.ServiceInfo("_http._tcp.local.", f"{name}._http._tcp.local.",
                                        addresses=[socket.inet_aton(ips[0])], port=self.port,
                                        properties={"path": "/"}, server=f"{name}.local.")
            self._zc = zeroconf.Zeroconf()
            self._zc.register_service(info)
            log.info("mDNS: http://%s.local:%d", name, self.port)
        except Exception as e:  # pragma: no cover  # noqa: BLE001
            log.info("mDNS unavailable: %s", e)

    def stop(self) -> None:
        if self.stopping.is_set():
            return
        self.stopping.set()
        log.info("Shutting down …")
        with contextlib.suppress(Exception):
            self.scanner.stop()
            self.artwork.stop()
        with contextlib.suppress(Exception):
            self.hls.stop()
        CHILDREN.kill_all()
        with contextlib.suppress(Exception):
            self.sleep._stop()
        if self._zc is not None:
            with contextlib.suppress(Exception):
                self._zc.close()
        with contextlib.suppress(Exception):
            self.db.backup_watch_state()
        if self.httpd is not None:
            threading.Thread(target=self.httpd.shutdown, daemon=True).start()
        shutil.rmtree(self.paths.tmp, ignore_errors=True)

    def restart(self) -> None:
        self._restart_flag = True
        self.stop()


def print_banner(app: App) -> None:
    urls = app.urls()
    cols = shutil.get_terminal_size((80, 24)).columns
    w = max(20, min(64, cols - 4))

    def say(text: str, color: str = "", indent: int = 2, hang: int = 0) -> None:
        """Print ``text`` word-wrapped to the terminal (so narrow phone terminals don't garble it)."""
        for line in (textwrap.wrap(text, max(20, cols - 1), initial_indent=" " * indent,
                                   subsequent_indent=" " * (indent + hang)) or [""]):
            print(c(line, color) if color else line)

    print()
    print(c("  ╭" + "─" * w + "╮", "red"))
    title = f"{APP_NAME}  v{VERSION}"
    print(c("  │", "red") + c(title.center(w), "bold") + c("│", "red"))
    print(c("  ╰" + "─" * w + "╯", "red"))
    say(f"Platform   {PLAT.describe()} · Python {platform.python_version()}", hang=11)
    if app.ff.available:
        hw = app.ff.pick_hw(str(app.setting("hwaccel"))) or "software"
        say(f"ffmpeg     {app.ff.version}  ({hw} encoding)", hang=11)
    else:
        say(f"ffmpeg     not found — direct play only → {install_hint()['text']}", "yellow", hang=11)
    for r in app.db.q("SELECT path FROM libraries WHERE enabled=1"):
        say(f"Media      {r[0]}", hang=11)
    print()
    print(c("  Open on any device on the same Wi-Fi:", "bold"))
    for i, u in enumerate(urls):
        print(f"    {c('➜', 'green')}  {c(u, 'cyan' if i == 0 else 'dim')}{c('   (best)', 'dim') if i == 0 and len(urls) > 1 else ''}")
    print(f"    {c('➜', 'green')}  {c(f'http://localhost:{app.port}', 'dim')}  (this computer)")
    if urls and app.setting("show_qr") and sys.stdout.isatty():
        with contextlib.suppress(Exception):
            qr = QRGEN.terminal(urls[0]).splitlines()
            qw = max((len(x) for x in qr), default=0)
            if qw and qw + 2 <= cols:  # skip the QR rather than print a wrapped, unscannable one
                print()
                pad = " " * min(4, (cols - qw) // 2)
                for line in qr:
                    print(pad + line)
    print()
    say("On a TV: open the TV's web browser and type", "bold")
    if urls:
        print(f"    {c(urls[0] + '/tv', 'cyan')}")
    if not app.setting("require_password") and app.host in ("0.0.0.0", "::"):
        say("⚠  No password set — anyone on your Wi-Fi can watch. (Settings → Network & Security)", "yellow", hang=3)
    if PLAT.is_windows:
        say("Tip: if other devices can't connect, allow Python through Windows Firewall (localflix.py doctor).", "dim", hang=5)
    elif PLAT.is_mac:
        say("Tip: if macOS asks, allow incoming connections for Python.", "dim", hang=5)
    elif PLAT.is_termux:
        say("Tip: disable battery optimisation for Termux so streaming isn't interrupted.", "dim", hang=5)
    say("Press Ctrl+C to stop.", "dim")
    print()


# ===== SECTION: wizard =====
def _ask(prompt: str, default: str = "") -> str:
    try:
        v = input(f"  {prompt}" + (f" [{default}]" if default else "") + ": ").strip()
    except EOFError:
        return default
    return v or default


def _yes(prompt: str, default: bool = True) -> bool:
    v = _ask(prompt + (" (Y/n)" if default else " (y/N)")).lower()
    return default if not v else v.startswith("y")


def run_wizard(paths: Paths) -> None:
    """Interactive first-run setup (§5.2).  Never blocks when stdin is not a TTY."""
    if not sys.stdin.isatty():
        return
    paths.ensure()
    db = Database(paths.db)
    print(c(f"\n  Welcome to {APP_NAME} {VERSION} 🎬", "bold"))
    print(f"  Detected: {PLAT.describe()} · Python {platform.python_version()}\n")
    # 1. ffmpeg
    ff = FFmpeg(paths)
    if ff.available:
        print(f"  {c('✓', 'green')} ffmpeg {ff.version} found at {ff.ffmpeg}")
    else:
        hint = install_hint()
        print(c("  ffmpeg was not found.", "yellow") + " It enables transcoding, multi-audio and thumbnails.")
        print(f"  Install command: {c(hint['text'], 'cyan')}")
        if hint["cmd"] and _yes("Run this command now?"):
            try:
                subprocess.run(hint["cmd"], check=False)
            except OSError as e:
                print(f"  Could not run installer: {e}")
            ff = FFmpeg(paths)
        if (not ff.available and (PLAT.is_windows or PLAT.is_linux) and not PLAT.is_termux
                and _yes("Download a checksum-verified static ffmpeg build into LocalFlix's folder?", False)):
            download_static_ffmpeg(paths)
            ff = FFmpeg(paths)
        print(f"  {c('✓', 'green') if ff.available else c('!', 'yellow')} ffmpeg: "
              f"{ff.version if ff.available else 'not installed (direct play only)'}")
    # 2. media folders
    current = [r[0] for r in db.q("SELECT path FROM libraries")] or [str(default_media_dir())]
    if PLAT.is_termux and (Path.home() / "storage" / "shared" / "Movies").exists():
        print(f"  Tip: your phone's Movies folder is {Path.home() / 'storage/shared/Movies'}")
    elif PLAT.is_termux:
        print("  Tip: run termux-setup-storage to share your phone's storage with Termux.")
    folders = _ask("Media folder(s), comma separated", ", ".join(current))
    for f in [x.strip() for x in folders.split(",") if x.strip()]:
        p = Path(f).expanduser().resolve()
        with contextlib.suppress(OSError):
            p.mkdir(parents=True, exist_ok=True)
        db.x("INSERT OR IGNORE INTO libraries(path,type,name,enabled,added_at) VALUES(?,?,?,1,?)",
             (str(p), "mixed", p.name, time.time()))
    db.kv_set("default_lib_added", "1")
    common = discover_media_roots("common")
    every = discover_media_roots("all")
    print("\n  Which videos should LocalFlix show?")
    print("    1) Only the folder(s) above  (recommended to start)")
    print(f"    2) Also the usual video folders + USB drives  ({', '.join(d['label'] for d in common) or 'none found'})")
    print(f"    3) Every video on this computer  ({len(every)} locations, system folders skipped)")
    choice = _ask("Choose 1, 2 or 3", "1")
    db.set_setting("library_scope", {"2": "common", "3": "all"}.get(choice.strip(), "folders"))
    # 3. port, language, PIN
    port = _ask("Port", str(db.get_setting("port")))
    with contextlib.suppress(ValueError):
        db.set_setting("port", validate_setting("port", int(port)))
    lang = _ask("Language: en (English) / hi (हिन्दी)", str(db.get_setting("language")))
    if lang in ("en", "hi"):
        db.set_setting("language", lang)
    pin = _ask("Optional password/PIN to protect LocalFlix (leave empty for none)", "")
    if pin:
        db.kv_set("auth_hash", hash_secret(pin))
        db.set_setting("require_password", True)
    # 4. hls.js
    print("  Downloading hls.js player …", end=" ", flush=True)
    print(c("done", "green") if fetch_hlsjs(paths, quiet=True) else c("offline — native HLS only for now", "yellow"))
    # 5. HW detection
    if ff.ffmpeg:
        print("  Testing hardware encoders …", end=" ", flush=True)
        res = ff.detect_hw(db, force=True)
        good = [k for k, v in res.items() if v]
        print(c(", ".join(good), "green") if good else "none (software encoding)")
    db.kv_set("wizard_done", VERSION)
    db.close_all()
    print(c("\n  Setup complete! Starting the server …\n", "bold"))


# ===== SECTION: service =====
def service_files(paths: Paths) -> dict[str, tuple[Path, str]]:
    py = sys.executable
    script = str(Path(__file__).resolve())
    files: dict[str, tuple[Path, str]] = {}
    if PLAT.is_termux:
        files["termux"] = (Path.home() / ".termux" / "boot" / "localflix.sh",
                           f"#!/data/data/com.termux/files/usr/bin/sh\ntermux-wake-lock\n"  # noqa: ISC004
                           f"cd \"{SCRIPT_DIR}\"\nexec {py} \"{script}\" serve --no-open --no-wizard "
                           f">> \"{paths.logs}/boot.log\" 2>&1\n")
    elif PLAT.is_mac:
        files["launchd"] = (Path.home() / "Library" / "LaunchAgents" / "com.localflix.server.plist", f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>com.localflix.server</string>
  <key>ProgramArguments</key><array><string>{py}</string><string>{script}</string><string>serve</string>
    <string>--no-open</string><string>--no-wizard</string></array>
  <key>WorkingDirectory</key><string>{SCRIPT_DIR}</string>
  <key>RunAtLoad</key><true/><key>KeepAlive</key><true/>
  <key>StandardOutPath</key><string>{paths.logs}/service.log</string>
  <key>StandardErrorPath</key><string>{paths.logs}/service.log</string>
</dict></plist>
""")
    elif PLAT.is_windows:
        pyw = str(Path(py).with_name("pythonw.exe")) if Path(py).with_name("pythonw.exe").exists() else py
        startup = Path(os.environ.get("APPDATA", "")) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"
        files["startup"] = (startup / "LocalFlix.bat",
                            f'@echo off\r\ncd /d "{SCRIPT_DIR}"\r\nstart "" "{pyw}" "{script}" serve --no-open --no-wizard\r\n')
        files["schtasks"] = (Path("schtasks"), (f'schtasks /Create /SC ONLOGON /RL LIMITED /TN "LocalFlix" /TR '
                                                f'"\\"{pyw}\\" \\"{script}\\" serve --no-open --no-wizard" /F'))
    else:
        files["systemd"] = (Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "systemd" / "user" /
                            "localflix.service", f"""[Unit]
Description=LocalFlix media server
After=network-online.target

[Service]
ExecStart={py} {script} serve --no-open --no-wizard
WorkingDirectory={SCRIPT_DIR}
Restart=on-failure
RestartSec=5

[Install]
WantedBy=default.target
""")
    return files


def run_service(action: str, paths: Paths) -> int:
    files = service_files(paths)
    if action == "print":
        for kind, (p, content) in files.items():
            print(c(f"# {kind}: {p}", "bold"))
            print(content)
        if "systemd" in files:
            print(c("# then: systemctl --user daemon-reload && systemctl --user enable --now localflix", "dim"))
            print(c("# (for start at boot without login: loginctl enable-linger $USER)", "dim"))
        if "launchd" in files:
            print(c("# then: launchctl load -w ~/Library/LaunchAgents/com.localflix.server.plist", "dim"))
        if "termux" in files:
            print(c("# requires the Termux:Boot app (F-Droid); open it once after installing", "dim"))
        return 0
    for kind, (p, content) in files.items():
        if kind == "schtasks":
            continue
        if action == "install":
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content, encoding="utf-8")
            if kind == "termux":
                p.chmod(0o755)
            print(f"Wrote {p}")
            if kind == "systemd" and shutil.which("systemctl"):
                subprocess.run(["systemctl", "--user", "daemon-reload"], check=False)
                subprocess.run(["systemctl", "--user", "enable", "--now", "localflix"], check=False)
            elif kind == "launchd":
                subprocess.run(["launchctl", "load", "-w", str(p)], check=False)
        elif action == "uninstall":
            if kind == "systemd" and shutil.which("systemctl"):
                subprocess.run(["systemctl", "--user", "disable", "--now", "localflix"], check=False)
            elif kind == "launchd" and p.exists():
                subprocess.run(["launchctl", "unload", "-w", str(p)], check=False)
            if p.exists():
                p.unlink()
                print(f"Removed {p}")
    return 0


# ===== SECTION: fixtures =====
def make_fixtures(ff: FFmpeg, out: Path, small: bool = False) -> list[Path]:
    """Generate test media with ffmpeg lavfi (§16.1)."""
    if not ff.ffmpeg:
        raise RuntimeError("ffmpeg required to generate fixtures")
    out.mkdir(parents=True, exist_ok=True)
    d = 12 if small else 40
    sz = "320x180" if small else "640x360"
    base = [ff.ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "error", "-y"]
    made: list[Path] = []

    def run(args: list[str], dest: Path) -> None:
        if dest.exists():
            made.append(dest)
            return
        dest.parent.mkdir(parents=True, exist_ok=True)
        rc, _, err = run_capture(base + args + [str(dest)], timeout=300)
        if rc == 0:
            made.append(dest)
        else:
            log.warning("fixture %s failed: %s", dest.name, err.decode(errors="replace")[-300:])

    vid = ["-f", "lavfi", "-i", f"testsrc2=size={sz}:rate=24:duration={d}"]

    def aud(f: int) -> list[str]:
        return ["-f", "lavfi", "-i", f"sine=frequency={f}:sample_rate=48000:duration={d}"]
    run(vid + aud(440) + ["-c:v", "libx264", "-preset", "ultrafast", "-g", "48", "-pix_fmt", "yuv420p", "-c:a", "aac",
                          "-movflags", "+faststart", "-shortest"], out / "Big Test Movie (2021).mp4")
    meta = out / "chapters.txt"
    meta.write_text(";FFMETADATA1\n[CHAPTER]\nTIMEBASE=1/1000\nSTART=0\nEND=4000\ntitle=Intro\n[CHAPTER]\nTIMEBASE=1/1000\n"
                    f"START=4000\nEND={(d - 4) * 1000}\ntitle=Main\n[CHAPTER]\nTIMEBASE=1/1000\nSTART={(d - 4) * 1000}\n"
                    f"END={d * 1000}\ntitle=Credits\n")
    srt1 = out / "_en.srt"
    srt1.write_text("1\n00:00:01,000 --> 00:00:03,000\nHello <i>world</i>\n\n2\n00:00:05,000 --> 00:00:07,500\nSecond line\n")
    srt2 = out / "_hi.srt"
    srt2.write_text("1\n00:00:01,000 --> 00:00:03,000\nनमस्ते दुनिया\n", encoding="utf-8")
    multi = [
        "-i", str(srt1), "-i", str(srt2), "-i", str(meta),
        "-map", "0:v", "-map", "1:a", "-map", "2:a", "-map", "3:a", "-map", "4:s", "-map", "5:s", "-map_metadata", "6",
        "-map_chapters", "6", "-c:v", "libx264", "-preset", "ultrafast", "-g", "48", "-pix_fmt", "yuv420p",
        "-c:a:0", "ac3", "-c:a:1", "aac", "-c:a:2", "aac", "-c:s", "srt",
        "-metadata:s:a:0", "language=eng", "-metadata:s:a:1", "language=hin", "-metadata:s:a:2", "language=tam",
        "-metadata:s:s:0", "language=eng", "-metadata:s:s:1", "language=hin", "-t", str(d)]
    run(vid + aud(440) + aud(660) + aud(880) + multi, out / "Multi Audio Film (2019).mkv")
    if "libx265" in ff.encoders:
        run(vid + aud(500) + ["-c:v", "libx265", "-preset", "ultrafast", "-pix_fmt", "yuv420p10le", "-x265-params",
                              "log-level=error", "-c:a", "aac", "-shortest"], out / "HEVC Ten Bit (2020).mkv")
    if "libvpx-vp9" in ff.encoders:
        run(vid + aud(300) + ["-c:v", "libvpx-vp9", "-deadline", "realtime", "-cpu-used", "8", "-b:v", "300k",
                              "-c:a", "libopus", "-shortest"], out / "Web Clip VP9.webm")
    run(vid + aud(350) + ["-c:v", "mpeg4", "-vtag", "xvid", "-q:v", "5", "-c:a", "libmp3lame" if "libmp3lame" in
                          ff.encoders else "mp2", "-shortest"], out / "Old Xvid Movie 2004.avi")
    if not small:
        run(["-f", "lavfi", "-i", "testsrc2=size=3840x2160:rate=24:duration=4"] + aud(500) +
            ["-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest"],
            out / "UHD Demo (2024).mp4")
    for e in (1, 2, 3):
        run(vid + aud(400 + e * 50) + ["-c:v", "libx264", "-preset", "ultrafast", "-g", "48", "-pix_fmt", "yuv420p",
                                       "-c:a", "aac", "-shortest"],
            out / "Show" / "Season 01" / f"Show.S01E0{e}.Episode.{e}.1080p.WEB-DL.mkv")
    ext = out / "Show" / "Season 01" / "Show.S01E01.Episode.1.1080p.WEB-DL.en.srt"
    if not ext.exists():
        ext.write_bytes("1\r\n00:00:01,000 --> 00:00:04,000\r\nCafé crème – Windows-1252\r\n".encode("cp1252"))
    for f in (meta, srt1, srt2):
        f.unlink(missing_ok=True)
    return made


# ===== SECTION: selftest =====
NAME_CASES: list[tuple[str, dict[str, Any]]] = [
    ("Inception (2010).mkv", {"kind": "movie", "title": "Inception", "year": 2010}),
    ("Inception.2010.1080p.BluRay.x264-GRP.mkv", {"kind": "movie", "title": "Inception", "year": 2010}),
    ("Inception [2010].mp4", {"kind": "movie", "title": "Inception", "year": 2010}),
    ("The.Dark.Knight.2008.2160p.UHD.BluRay.x265.10bit.HDR.DTS-HD.MA.5.1-SWTYBLZ.mkv",
     {"kind": "movie", "title": "The Dark Knight", "year": 2008}),
    ("Movies/Interstellar (2014)/Interstellar.mkv", {"kind": "movie", "title": "Interstellar", "year": 2014}),
    ("Blade Runner 2049 (2017).mkv", {"kind": "movie", "title": "Blade Runner 2049", "year": 2017}),
    ("2001.A.Space.Odyssey.1968.1080p.mkv", {"kind": "movie", "title": "2001 A Space Odyssey", "year": 1968}),
    ("Dangal.2016.Hindi.1080p.WEB-DL.DD5.1.x264.mkv", {"kind": "movie", "title": "Dangal", "year": 2016}),
    ("3 Idiots (2009) [1080p].mp4", {"kind": "movie", "title": "3 Idiots", "year": 2009}),
    ("RRR.2022.Telugu.720p.HDRip.x264.mkv", {"kind": "movie", "title": "RRR", "year": 2022}),
    ("home video.mp4", {"kind": "movie", "title": "Home Video"}),
    ("VID_20230115_183000.mp4", {"kind": "movie"}),
    ("Amélie (2001).mkv", {"kind": "movie", "title": "Amélie", "year": 2001}),
    ("Spirited_Away_2001_720p.mkv", {"kind": "movie", "title": "Spirited Away", "year": 2001}),
    ("Movie Name.avi", {"kind": "movie", "title": "Movie Name"}),
    ("Parasite.2019.KOREAN.1080p.BluRay.x264.mkv", {"kind": "movie", "year": 2019}),
    ("The Matrix (1999)/The.Matrix.1999.REMASTERED.1080p.mkv", {"kind": "movie", "title": "The Matrix", "year": 1999}),
    ("Avatar.The.Way.of.Water.2022.IMAX.2160p.mkv", {"kind": "movie", "title": "Avatar The Way of Water", "year": 2022}),
    ("Movies/Sholay (1975)/movie.mkv", {"kind": "movie", "year": 1975}),
    ("Jawan 2023 Hindi WEB-DL 1080p.mkv", {"kind": "movie", "title": "Jawan", "year": 2023}),
    ("Breaking.Bad.S01E02.720p.BluRay.x264.mkv",
     {"kind": "episode", "series": "Breaking Bad", "season": 1, "episode": 2}),
    ("Breaking Bad/Season 1/Breaking.Bad.S01E03.mkv",
     {"kind": "episode", "series": "Breaking Bad", "season": 1, "episode": 3}),
    ("The Office (US) - S02E05 - Halloween.mkv", {"kind": "episode", "season": 2, "episode": 5}),
    ("Friends.1x02.The.One.With.The.Sonogram.mkv", {"kind": "episode", "series": "Friends", "season": 1, "episode": 2}),
    ("Lost/Season 1/Episode 02.mkv", {"kind": "episode", "series": "Lost", "season": 1, "episode": 2}),
    ("Show - 102.mkv", {"kind": "episode", "series": "Show", "season": 1, "episode": 2}),
    ("Show.S01E01-E02.mkv", {"kind": "episode", "series": "Show", "season": 1, "episode": 1, "episode_end": 2}),
    ("Show.S01E01E02.720p.mkv", {"kind": "episode", "season": 1, "episode": 1, "episode_end": 2}),
    ("[SubsPlease] One Piece - 1071 (1080p) [ABCD1234].mkv", {"kind": "episode", "series": "One Piece",
                                                              "episode": 1071}),
    ("Naruto - 012.mkv", {"kind": "episode", "series": "Naruto", "season": 1, "episode": 12}),
    ("[Group] Attack on Titan - 05 [720p].mkv", {"kind": "episode", "series": "Attack on Titan", "episode": 5}),
    ("Game of Thrones/Season 08/Game.of.Thrones.S08E06.The.Iron.Throne.2160p.mkv",
     {"kind": "episode", "series": "Game of Thrones", "season": 8, "episode": 6, "episode_title": "The Iron Throne"}),
    ("Sacred Games/Season 2/Sacred.Games.S02E01.Hindi.1080p.NF.WEB-DL.mkv",
     {"kind": "episode", "series": "Sacred Games", "season": 2, "episode": 1}),
    ("Panchayat/S01/Panchayat S01E04.mkv", {"kind": "episode", "series": "Panchayat", "season": 1, "episode": 4}),
    ("Mirzapur.S03E10.mkv", {"kind": "episode", "series": "Mirzapur", "season": 3, "episode": 10}),
    ("Stranger Things/Season 4/01 - Chapter One.mkv", {"kind": "episode", "series": "Stranger Things", "season": 4,
                                                       "episode": 1}),
    ("Doctor.Who.2005.S01E01.Rose.mkv", {"kind": "episode", "series": "Doctor Who", "season": 1, "episode": 1}),
    ("the.mandalorian.s02e08.1080p.web.h264-glhf.mkv", {"kind": "episode", "series": "The Mandalorian", "season": 2,
                                                        "episode": 8}),
    ("Severance.S2.E3.mkv", {"kind": "episode", "series": "Severance", "season": 2, "episode": 3}),
    ("Show Name S01 E05.mp4", {"kind": "episode", "series": "Show Name", "season": 1, "episode": 5}),
    ("Kota Factory/Season 1/Kota.Factory.S01E05.720p.mkv", {"kind": "episode", "series": "Kota Factory", "episode": 5}),
    ("The Crown/Season 03/The Crown - 3x07 - Moondust.mkv",
     {"kind": "episode", "series": "The Crown", "season": 3, "episode": 7}),
    ("Planet Earth II/Season 1/Planet.Earth.II.S01E01.Islands.2160p.mkv",
     {"kind": "episode", "series": "Planet Earth II", "season": 1, "episode": 1}),
    ("Dexter.S04E12.HDTV.XviD-NoTV.avi", {"kind": "episode", "series": "Dexter", "season": 4, "episode": 12}),
    ("Westworld S01E10 The Bicameral Mind.mkv", {"kind": "episode", "series": "Westworld", "episode": 10,
                                                 "episode_title": "The Bicameral Mind"}),
    ("Bluey/Season 2/Bluey S02E26 Sleepytime.mp4", {"kind": "episode", "series": "Bluey", "season": 2, "episode": 26}),
    ("Fargo.S01E01.The.Crocodiles.Dilemma.1080p.mkv", {"kind": "episode", "series": "Fargo", "episode": 1}),
    ("Chernobyl.Season.1.Episode.3.mkv", {"kind": "episode", "series": "Chernobyl", "season": 1, "episode": 3}),
    ("The Boys/Season 1/The Boys S01E01.mkv", {"kind": "episode", "series": "The Boys", "season": 1, "episode": 1}),
    ("Scam 1992/Season 1/Scam.1992.S01E01.mkv", {"kind": "episode", "series": "Scam 1992", "season": 1, "episode": 1}),
    ("The Family Man S02E09.mkv", {"kind": "episode", "series": "The Family Man", "season": 2, "episode": 9}),
    ("Cosmos.A.Spacetime.Odyssey.S01E02.mkv", {"kind": "episode", "series": "Cosmos A Spacetime Odyssey"}),
    ("Show/Season 2/E07.mkv", {"kind": "episode", "series": "Show", "season": 2, "episode": 7}),
    ("Rick and Morty - S05E01 - Mort Dinner Rick Andre.mkv", {"kind": "episode", "series": "Rick and Morty",
                                                              "season": 5, "episode": 1}),
    ("Sherlock.S03E00.Special.mkv", {"kind": "episode", "series": "Sherlock", "season": 3, "episode": 0}),
    ("Movie.Title.2015.PROPER.1080p.mkv", {"kind": "movie", "title": "Movie Title", "year": 2015}),
    ("Oppenheimer.2023.IMAX.2160p.UHD.BluRay.REMUX.HDR.DV.TrueHD.Atmos.7.1-FGT.mkv",
     {"kind": "movie", "title": "Oppenheimer", "year": 2023}),
    ("Kalki 2898 AD (2024).mkv", {"kind": "movie", "title": "Kalki 2898 AD", "year": 2024}),
    ("Pathaan (2023) Hindi 720p.mkv", {"kind": "movie", "title": "Pathaan", "year": 2023}),
    ("Movies/Lagaan/Lagaan.2001.mkv", {"kind": "movie", "title": "Lagaan", "year": 2001}),
    ("Holiday Trip 2019/clip01.mp4", {"kind": "movie"}),
    ("1917 (2019).mkv", {"kind": "movie", "title": "1917", "year": 2019}),
    ("Taare.Zameen.Par.2007.DVDRip.XviD.avi", {"kind": "movie", "title": "Taare Zameen Par", "year": 2007}),
    ("Some Show/Season 1/Some Show - 1x03.mkv", {"kind": "episode", "series": "Some Show", "season": 1, "episode": 3}),
]


class TestNameParser(unittest.TestCase):
    def test_table(self) -> None:
        self.assertGreaterEqual(len(NAME_CASES), 60)
        for path, exp in NAME_CASES:
            with self.subTest(path=path):
                got = parse_name(path)
                for k, v in exp.items():
                    self.assertEqual(got.get(k), v, f"{path}: {k} = {got.get(k)!r}, expected {v!r} (got {got})")


class TestHttpHelpers(unittest.TestCase):
    def test_mime(self) -> None:
        for ext, typ in ((".js", "text/javascript"), (".css", "text/css"), (".m3u8", "application/vnd.apple.mpegurl"),
                         (".m4s", "video/iso.segment"), (".mp4", "video/mp4"), (".mkv", "video/x-matroska"),
                         (".ts", "video/mp2t"), (".vtt", "text/vtt"), (".svg", "image/svg+xml"),
                         (".webp", "image/webp"), (".woff2", "font/woff2"), (".json", "application/json")):
            self.assertTrue(MIME_MAP[ext].startswith(typ), ext)

    def test_range(self) -> None:
        self.assertIsNone(parse_range(None, 1000))
        self.assertEqual(parse_range("bytes=0-99", 1000), (0, 99))
        self.assertEqual(parse_range("bytes=500-", 1000), (500, 999))
        self.assertEqual(parse_range("bytes=-200", 1000), (800, 999))
        self.assertEqual(parse_range("bytes=-5000", 1000), (0, 999))
        self.assertEqual(parse_range("bytes=900-5000", 1000), (900, 999))
        self.assertEqual(parse_range("bytes=0-0", 1000), (0, 0))
        self.assertEqual(parse_range("bytes=10-20,30-40", 1000), (10, 20))
        self.assertIsNone(parse_range("items=0-1", 1000))
        with self.assertRaises(ValueError):
            parse_range("bytes=1000-", 1000)
        with self.assertRaises(ValueError):
            parse_range("bytes=50-10", 1000)
        with self.assertRaises(ValueError):
            parse_range("bytes=-0", 1000)

    def test_qr(self) -> None:
        m = QRGEN.matrix("http://192.168.1.20:8000")
        self.assertIn(len(m), (21, 25, 29, 33))
        self.assertTrue(QRGEN.svg("x").startswith("<svg"))


class TestSegments(unittest.TestCase):
    def test_fixed(self) -> None:
        s = build_fixed_segments(20.0)
        self.assertEqual([round(d, 3) for _, d in s], [6.0, 6.0, 6.0, 2.0])
        self.assertEqual(s[2][0], 12.0)
        self.assertEqual(len(build_fixed_segments(0.02)), 1)

    def test_keyframes(self) -> None:
        kfs = [i * 2.0 for i in range(30)]
        segs, groups = build_keyframe_segments(kfs, 60.0)
        self.assertAlmostEqual(sum(d for _, d in segs), 60.0, places=3)
        self.assertTrue(all(4.0 <= d <= 10.0 for _, d in segs[:-1]))
        self.assertEqual(groups[0][0], 0)
        self.assertEqual(groups[-1][1], 30)
        for (a, b), (c_, d_) in itertools.pairwise(groups):
            self.assertEqual(b, c_)
        segs2, _ = build_keyframe_segments([0.0, 0.5, 11.0, 30.0, 31.0], 40.0)
        self.assertAlmostEqual(sum(d for _, d in segs2), 40.0, places=3)

    def test_playlist(self) -> None:
        pl = media_playlist([(0, 6.0), (6, 4.5)], "init.mp4", lambda i: f"s{i}.m4s")
        self.assertIn("#EXT-X-PLAYLIST-TYPE:VOD", pl)
        self.assertIn("#EXT-X-ENDLIST", pl)
        self.assertIn('#EXT-X-MAP:URI="init.mp4"', pl)
        self.assertIn("#EXT-X-TARGETDURATION:6", pl)


def _mk_item(container: str = "mp4", vcodec: str = "h264", profile: str = "High", depth: int = 8, h: int = 1080,
             audio: tuple[str, ...] = ("aac",), subs: tuple[tuple[str, bool], ...] = (), hdr: str = "",
             bitrate: int = 5_000_000, faststart: bool | None = True, video: bool = True) -> dict[str, Any]:
    return {"container": container, "bitrate": bitrate, "duration": 600, "size": 500 * 1024 * 1024,
            "faststart": faststart,
            "video": {"codec": vcodec, "profile": profile, "bitdepth": depth, "height": h, "width": h * 16 // 9,
                      "hdr": hdr} if video else None,
            "audio": [{"sidx": i, "codec": a, "channels": 2, "default": i == 0} for i, a in enumerate(audio)],
            "subs": [{"sidx": i, "codec": c_, "bitmap": b} for i, (c_, b) in enumerate(subs)]}


CHROME = {"video": ["h264", "vp9", "av1"], "audio": ["aac", "mp3", "opus", "flac"], "containers": ["mp4", "webm"]}
SAFARI = {"video": ["h264", "hevc", "hevc10"], "audio": ["aac", "mp3", "ac3", "eac3"], "containers": ["mp4"],
          "audioTracks": True, "hdr": ["hdr10"]}
OLD_TV = {"video": ["h264"], "audio": ["aac", "mp3", "ac3"], "containers": ["mp4"]}
OPTS = {"ffmpeg": True, "allow_transcode": True, "allow_passthrough": False, "prefer_direct": True, "quality": "auto",
        "tonemap": True, "burn_in": True}

DECISION_CASES: list[tuple[str, dict[str, Any], dict[str, Any], dict[str, Any], str, str | None]] = [
    ("mp4 h264 aac chrome", _mk_item(), CHROME, {}, "direct", None),
    ("mp4 h264 aac safari", _mk_item(), SAFARI, {}, "direct", None),
    ("mp4 h264 aac tv", _mk_item(), OLD_TV, {}, "direct", None),
    ("mkv h264 aac chrome", _mk_item("mkv"), CHROME, {}, "remux", "copy"),
    ("mkv h264 ac3 chrome", _mk_item("mkv", audio=("ac3",)), CHROME, {}, "remux", "copy"),
    ("mp4 h264 ac3 chrome", _mk_item(audio=("ac3",)), CHROME, {}, "remux", "copy"),
    ("mp4 h264 ac3 safari", _mk_item(audio=("ac3",)), SAFARI, {}, "direct", None),
    ("mp4 hevc chrome", _mk_item(vcodec="hevc", profile="Main"), CHROME, {}, "transcode", "transcode"),
    ("mp4 hevc safari", _mk_item(vcodec="hevc", profile="Main"), SAFARI, {}, "direct", None),
    ("mkv hevc10 safari", _mk_item("mkv", "hevc", "Main 10", 10), SAFARI, {}, "remux", "copy"),
    ("mkv hevc10 chrome", _mk_item("mkv", "hevc", "Main 10", 10), CHROME, {}, "transcode", "transcode"),
    ("mkv hevc hdr10 chrome", _mk_item("mkv", "hevc", "Main 10", 10, hdr="hdr10"), CHROME, {}, "transcode", "transcode"),
    ("mkv hevc hdr10 safari", _mk_item("mkv", "hevc", "Main 10", 10, hdr="hdr10"), SAFARI, {}, "remux", "copy"),
    ("webm vp9 opus chrome", _mk_item("webm", "vp9", "Profile 0", audio=("opus",)), CHROME, {}, "direct", None),
    ("webm vp9 safari", _mk_item("webm", "vp9", "Profile 0", audio=("opus",)), SAFARI, {}, "transcode", "transcode"),
    ("avi mpeg4 chrome", _mk_item("avi", "mpeg4", "Advanced Simple", audio=("mp3",)), CHROME, {}, "transcode",
     "transcode"),
    ("mkv multi-audio chrome", _mk_item("mkv", audio=("aac", "aac", "ac3")), CHROME, {}, "remux", "copy"),
    ("mp4 multi-audio chrome", _mk_item(audio=("aac", "aac")), CHROME, {}, "remux", "copy"),
    ("mp4 multi-audio safari native tracks", _mk_item(audio=("aac", "aac")), SAFARI, {}, "direct", None),
    ("h264 high10 chrome", _mk_item("mkv", "h264", "High 10", 10), CHROME, {}, "transcode", "transcode"),
    ("4k h264 with 1080p cap", _mk_item(h=2160, bitrate=40_000_000), CHROME, {"quality": "1080p"}, "transcode",
     "transcode"),
    ("1080p under 1080p cap", _mk_item(), CHROME, {"quality": "1080p"}, "direct", None),
    ("bitrate cap 720p", _mk_item(bitrate=9_000_000), CHROME, {"quality": "720p"}, "transcode", "transcode"),
    ("original quality big file", _mk_item(h=2160, bitrate=60_000_000), CHROME, {"quality": "original"}, "direct", None),
    ("pgs burn-in", dict(_mk_item("mkv", subs=(("hdmv_pgs_subtitle", True),)), ), dict(CHROME, subtitleIndex=0), {},
     "transcode", "transcode"),
    ("srt selected stays copy", _mk_item("mkv", subs=(("subrip", False),)), dict(CHROME, subtitleIndex=0), {}, "remux",
     "copy"),
    ("no ffmpeg → direct", _mk_item("mkv", "hevc"), CHROME, {"ffmpeg": False}, "direct", None),
    ("transcode disabled", _mk_item("avi", "mpeg4"), CHROME, {"allow_transcode": False}, "direct", None),
    ("forced transcode", _mk_item(), CHROME, {"force": "transcode"}, "transcode", "transcode"),
    ("forced remux", _mk_item(), CHROME, {"force": "remux"}, "remux", "copy"),
    ("moov at end large", _mk_item(faststart=False), CHROME, {}, "remux", "copy"),
    ("prefer direct off", _mk_item(), CHROME, {"prefer_direct": False}, "remux", "copy"),
    ("audio only mp4 aac", _mk_item(video=False), CHROME, {}, "direct", None),
    ("audio only mkv flac tv", _mk_item("mkv", video=False, audio=("flac",)), OLD_TV, {}, "remux", None),
    ("av1 mp4 chrome", _mk_item(vcodec="av1", profile="Main"), CHROME, {}, "direct", None),
    ("av1 mkv old tv", _mk_item("mkv", "av1", "Main"), OLD_TV, {}, "transcode", "transcode"),
    ("termux auto caps 720", _mk_item(h=1080), CHROME, {"termux": True}, "transcode", "transcode"),
]


class TestDecision(unittest.TestCase):
    def test_matrix(self) -> None:
        self.assertGreaterEqual(len(DECISION_CASES), 30)
        for name, item, caps, opt_over, method, vmode in DECISION_CASES:
            with self.subTest(name=name):
                d = decide_playback(item, caps, dict(OPTS, **opt_over))
                self.assertEqual(d["method"], method, f"{name}: {d}")
                self.assertEqual(d["videoMode"], vmode, f"{name}: {d}")

    def test_aac_audio_mode(self) -> None:
        d = decide_playback(_mk_item("mkv", audio=("eac3",)), SAFARI, dict(OPTS, allow_passthrough=True))
        self.assertEqual(d["audioMode"], "copy")
        d = decide_playback(_mk_item("mkv", audio=("eac3",)), CHROME, OPTS)
        self.assertEqual(d["audioMode"], "aac")


class TestSubtitles(unittest.TestCase):
    SRT = "1\r\n00:00:01,500 --> 00:00:03,000\r\n<i>Hello</i> <font color=\"#ff0000\">red</font>\r\n\r\n" \
          "2\r\n00:01:02,03 --> 00:01:04,000\r\nLine two\r\n"

    def test_srt(self) -> None:
        v = srt_to_vtt(self.SRT)
        self.assertTrue(v.startswith("WEBVTT"))
        self.assertIn("00:00:01.500 --> 00:00:03.000", v)
        self.assertIn("<i>Hello</i>", v)
        self.assertIn("<c.cFF0000>red</c>", v)
        self.assertIn("00:01:02.030 --> 00:01:04.000", v)

    def test_encodings(self) -> None:
        self.assertEqual(decode_text(b"\xef\xbb\xbfh\xc3\xa9"), "hé")
        self.assertEqual(decode_text("héllo".encode("utf-16")), "héllo")
        self.assertEqual(decode_text("Café – x".encode("cp1252")), "Café – x")
        self.assertEqual(decode_text("नमस्ते".encode()), "नमस्ते")
        self.assertIn("Café", srt_to_vtt(decode_text("1\r\n00:00:01,000 --> 00:00:02,000\r\nCafé\r\n".encode("cp1252"))))

    def test_ass(self) -> None:
        ass = ("[Script Info]\nTitle: x\n\n[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, "
               "Effect, Text\nDialogue: 0,0:00:01.00,0:00:02.50,Default,,0,0,0,,{\\i1}Hi{\\i0}, there\\Nline2\n"
               "Dialogue: 0,0:00:03.00,0:00:04.00,Default,,0,0,0,,{\\c&H0000FF&}Red\n")
        v = ass_to_vtt(ass)
        self.assertIn("00:00:01.000 --> 00:00:02.500", v)
        self.assertIn("<i>Hi</i>, there\nline2", v)
        self.assertIn("<c.cFF0000>Red</c>", v)

    def test_stream_duration(self) -> None:
        self.assertEqual(_stream_duration({"duration": "60.5"}), 60.5)
        self.assertAlmostEqual(_stream_duration({"tags": {"DURATION": "01:02:03.500000000"}}), 3723.5)
        self.assertEqual(_stream_duration({}), 0.0)

    def test_langs(self) -> None:
        self.assertGreaterEqual(len(set(LANGS.values())), 40)
        self.assertEqual(lang_name("hin"), "Hindi")
        self.assertEqual(lang_norm("te"), "tel")
        self.assertEqual(lang_name("mal"), "Malayalam")


class TestPathSafety(unittest.TestCase):
    def test_safe_join(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            root = Path(d) / "lib"
            (root / "sub").mkdir(parents=True)
            (root / "sub" / "a.mp4").write_bytes(b"x")
            (Path(d) / "secret.txt").write_text("s")
            self.assertIsNotNone(safe_join(root, "sub/a.mp4"))
            self.assertIsNone(safe_join(root, "../secret.txt"))
            self.assertIsNone(safe_join(root, "sub/../../secret.txt"))
            self.assertIsNone(safe_join(root, "..\\secret.txt"))
            if not PLAT.is_windows:
                os.symlink(Path(d) / "secret.txt", root / "link.txt")
                self.assertIsNone(safe_join(root, "link.txt"))


class TestSettingsAndSchema(unittest.TestCase):
    def test_validate(self) -> None:
        self.assertEqual(validate_setting("port", "9000"), 9000)
        self.assertTrue(validate_setting("autoplay_next", "yes"))
        self.assertEqual(validate_setting("accent", "#e5484d"), "#E5484D")
        for k, v in (("port", 0), ("theme", "neon"), ("accent", "red"), ("nope", 1), ("scan_interval", 99999)):
            with self.assertRaises(ValueError):
                validate_setting(k, v)
        for s in SETTINGS_SPEC:
            self.assertEqual(validate_setting(s["key"], s["default"]), s["default"] if s["type"] != "color" else
                             s["default"].upper(), s["key"])

    def test_migration(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "t.db"
            conn = sqlite3.connect(str(p))
            _mig_1(conn, False)
            conn.execute("PRAGMA user_version=1")
            conn.execute("INSERT INTO libraries(path,type) VALUES('/x','mixed')")
            conn.commit()
            conn.close()
            db = Database(p)
            self.assertEqual(db.one("PRAGMA user_version")[0], len(MIGRATIONS))
            cols = {r[1] for r in db.q("PRAGMA table_info(items)")}
            self.assertIn("subs_external", cols)
            self.assertEqual(db.one("SELECT path FROM libraries")[0], "/x")
            db.set_setting("theme", "oled", "profile:1")
            self.assertEqual(db.get_setting("theme", "profile:1"), "oled")
            self.assertEqual(db.get_setting("theme", "profile:2"), "dark")
            db.close_all()

    def test_auth_hash(self) -> None:
        h = hash_secret("1234", iterations=1000)
        self.assertTrue(verify_secret("1234", h))
        self.assertFalse(verify_secret("1235", h))
        self.assertGreaterEqual(PBKDF2_ITER, 200_000)


class TestServerAPI(unittest.TestCase):
    """Contract tests against a real server on a random port with fixture media."""

    app: App
    base: str
    tmp: tempfile.TemporaryDirectory[str]

    @classmethod
    def setUpClass(cls) -> None:
        cls.tmp = tempfile.TemporaryDirectory()
        home = Path(cls.tmp.name) / "home"
        media = Path(cls.tmp.name) / "media"
        media.mkdir(parents=True)
        paths = Paths(str(home))
        paths.ensure()
        setup_logging(paths, "WARNING", console=False)
        ff = FFmpeg(paths)
        cls.has_ff = ff.available and "libx264" in ff.encoders
        if cls.has_ff:
            fx = Paths().cache / "selftest-fixtures"
            make_fixtures(ff, fx, small=True)
            shutil.copytree(fx, media, dirs_exist_ok=True)
        else:
            (media / "Fake Movie (2020).mp4").write_bytes(b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 1000)
        vend = Paths().vendor / "hls.min.js"
        if vend.exists():
            shutil.copy(vend, paths.vendor / "hls.min.js")
        cls.app = App(paths)
        cls.app.db.set_setting("private_only", True)
        cls.app.db.set_setting("hls_mode", "ondemand")
        cls.app.db.set_setting("trickplay", False)
        cls.app.ensure_defaults([str(media)])
        cls.app.bind("127.0.0.1", random.randint(20000, 40000))
        cls.app.start_background()
        threading.Thread(target=cls.app.httpd.serve_forever, daemon=True).start()
        cls.base = f"http://127.0.0.1:{cls.app.port}"
        deadline = time.time() + 120
        while time.time() < deadline:
            st = cls.app.scanner.status
            if st.get("last_scan") and st["queue"] == 0 and cls.app.scanner.probe_q.qsize() == 0 and \
                    not cls.app.scanner._queued:
                break
            time.sleep(0.2)
        time.sleep(0.5)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.app.stop()
        cls.app.db.close_all()
        time.sleep(0.3)
        cls.tmp.cleanup()

    def req(self, path: str, method: str = "GET", body: Any = None, headers: dict[str, str] | None = None,
            raw: bool = False, cookie: str | None = None) -> tuple[int, dict[str, str], Any]:
        h = dict(headers or {})
        data = None
        if body is not None:
            data = json.dumps(body).encode()
            h.setdefault("Content-Type", "application/json")
        if cookie:
            h["Cookie"] = cookie
        conn = http.client.HTTPConnection("127.0.0.1", self.app.port, timeout=60)
        conn.request(method, path, body=data, headers=h)
        r = conn.getresponse()
        payload = r.read()
        hdrs = {k.lower(): v for k, v in r.getheaders()}
        conn.close()
        if raw:
            return r.status, hdrs, payload
        try:
            return r.status, hdrs, json.loads(payload.decode() or "null")
        except ValueError:
            return r.status, hdrs, payload

    def item_by(self, needle: str) -> dict[str, Any]:
        st, _, d = self.req("/api/v1/items?limit=200")
        self.assertEqual(st, 200)
        for it in d["items"]:
            if needle.lower() in (it["title"] or "").lower() or needle.lower() in (it.get("seriesTitle") or "").lower():
                return it
        self.fail(f"{needle} not in library: {[i['title'] for i in d['items']]}")

    def test_01_info_and_index(self) -> None:
        st, h, d = self.req("/api/v1/system/info")
        self.assertEqual(st, 200)
        self.assertEqual(d["version"], VERSION)
        self.assertEqual(h.get("x-content-type-options"), "nosniff")
        st, h, page = self.req("/", raw=True)
        self.assertEqual(st, 200)
        self.assertIn(b"LocalFlix", page)
        self.assertIn("nonce-", h.get("content-security-policy", ""))
        st, h, _js = self.req("/static/app.js?v=1", raw=True, headers={"Accept-Encoding": "gzip"})
        self.assertEqual(st, 200)
        self.assertEqual(h.get("content-encoding"), "gzip")
        st, _, _ = self.req("/static/app.js", raw=True, headers={"If-None-Match": h["etag"]})
        self.assertEqual(st, 304)

    def test_02_library(self) -> None:
        st, _, home = self.req("/api/v1/home")
        self.assertEqual(st, 200)
        self.assertIn("rows", home)
        self.assertGreater(home["total"], 0)
        st, _, d = self.req("/api/v1/items?type=series")
        self.assertEqual(st, 200)
        if self.has_ff:
            show = next((x for x in d["items"] if x["title"] == "Show"), {})
            st, _, s = self.req(f"/api/v1/series/{show['id']}")
            self.assertEqual(len(s["episodes"]), 3)
            st, _, sr = self.req("/api/v1/search?q=multi")
            self.assertTrue(any("Multi" in x["title"] for x in sr["items"]))

    def test_03_direct_play_and_range(self) -> None:
        it = self.item_by("Big Test Movie" if self.has_ff else "Fake Movie")
        caps = {"video": ["h264"], "audio": ["aac"], "containers": ["mp4"], "mse": True}
        st, _, pb = self.req(f"/api/v1/items/{it['id']}/playback", "POST", caps)
        self.assertEqual(st, 200, pb)
        self.assertEqual(pb["method"], "direct")
        st, h, data = self.req(pb["url"], raw=True, headers={"Range": "bytes=0-99"})
        self.assertEqual(st, 206)
        self.assertEqual(len(data), 100)
        self.assertTrue(h["content-range"].startswith("bytes 0-99/"))
        size = int(h["content-range"].split("/")[1])
        st, h, _ = self.req(pb["url"], raw=True, headers={"Range": f"bytes={size}-"})
        self.assertEqual(st, 416)
        st, h, data = self.req(pb["url"], raw=True, headers={"Range": "bytes=-10"})
        self.assertEqual((st, len(data)), (206, 10))
        st, h, _ = self.req(pb["url"], "HEAD", raw=True)
        self.assertEqual(int(h["content-length"]), size)

    def test_04_hls_ondemand(self) -> None:
        if not self.has_ff:
            self.skipTest("ffmpeg unavailable")
        it = self.item_by("Multi Audio Film")
        caps = {"video": ["h264"], "audio": ["aac"], "containers": ["mp4"], "mse": True, "audioIndex": 1}
        st, _, pb = self.req(f"/api/v1/items/{it['id']}/playback", "POST", caps)
        self.assertEqual(st, 200, pb)
        self.assertEqual(pb["method"], "remux")
        self.assertEqual(len(pb["audio"]), 3)
        self.assertEqual(len(pb["subtitles"]), 2)
        base = pb["url"].rsplit("/", 1)[0]
        st, _, master = self.req(pb["url"], raw=True)
        master = master.decode()
        self.assertIn("TYPE=AUDIO", master)
        self.assertEqual(master.count("TYPE=AUDIO"), 3)
        self.assertIn("TYPE=SUBTITLES", master)
        if pb["mode"] != "ondemand":
            self.skipTest("keyframe table not ready; simple mode used")
        st, _, vpl = self.req(base + "/video.m3u8", raw=True)
        vpl = vpl.decode()
        self.assertIn("#EXT-X-ENDLIST", vpl)
        segs = [ln for ln in vpl.splitlines() if ln.endswith(".m4s")]
        durs = [float(ln[8:-1]) for ln in vpl.splitlines() if ln.startswith("#EXTINF:")]
        self.assertGreater(len(segs), 1)
        st, _, init = self.req(base + "/init_video.mp4", raw=True)
        self.assertEqual(st, 200)
        self.assertIn(b"moov", init[:4000])
        # seek straight to the last segment first (restart), then the first
        last = len(segs) - 1
        st, _, seg_last = self.req(f"{base}/{segs[last]}", raw=True)
        self.assertEqual(st, 200)
        st, _, seg0 = self.req(f"{base}/{segs[0]}", raw=True)
        self.assertEqual(st, 200)
        tl = mp4_tfdt(seg_last)
        t0 = mp4_tfdt(seg0)
        self.assertTrue(t0 and tl)
        # timescale from init (mdhd) ~ approximate via ratio: last segment start / its playlist start
        start_last = sum(durs[:last])
        if start_last > 0 and tl[0] > 0:
            ratio = tl[0] / start_last
            self.assertLess(abs(t0[0] / ratio - 0.0), 0.2)
        # audio rendition
        st, _, _apl = self.req(base + "/audio_1.m3u8", raw=True)
        self.assertEqual(st, 200)
        st, _, ainit = self.req(base + "/init_audio_1.mp4", raw=True)
        self.assertEqual(st, 200)
        st, _, aseg = self.req(base + "/seg_audio_1_1.m4s", raw=True)
        self.assertEqual(st, 200)
        self.assertTrue(mp4_tfdt(aseg))
        # regression ("stuck at 99 %"): an AAC track started mid-file must carry its real time (6 s), not 0
        m = re.search(rb"mdhd", ainit)
        ver = ainit[m.end()]
        ats = struct.unpack(">I", ainit[m.end() + (20 if ver == 1 else 12): m.end() + (24 if ver == 1 else 16)])[0]
        self.assertAlmostEqual(mp4_tfdt(aseg)[0] / ats, 6.0, delta=0.15)
        st, _, _ = self.req(f"/api/v1/hls/{pb['sessionId']}", "DELETE")
        self.assertEqual(st, 200)

    def test_05_transcode(self) -> None:
        if not self.has_ff:
            self.skipTest("ffmpeg unavailable")
        it = self.item_by("Old Xvid Movie")
        caps = {"video": ["h264"], "audio": ["aac"], "containers": ["mp4"], "mse": True}
        st, _, pb = self.req(f"/api/v1/items/{it['id']}/playback", "POST", caps)
        self.assertEqual(pb["method"], "transcode")
        base = pb["url"].rsplit("/", 1)[0]
        st, _, seg = self.req(base + "/seg_video_1.m4s", raw=True)
        self.assertEqual(st, 200)
        tf = mp4_tfdt(seg)
        self.assertTrue(tf)
        st, _, init = self.req(base + "/init_video.mp4", raw=True)
        m = re.search(rb"mdhd", init)
        self.assertIsNotNone(m)
        ver = init[m.end()]
        ts = struct.unpack(">I", init[m.end() + (20 if ver == 1 else 12): m.end() + (24 if ver == 1 else 16)])[0]
        self.assertAlmostEqual(tf[0] / ts, TRANSCODE_SEG, delta=0.15)
        self.req(f"/api/v1/hls/{pb['sessionId']}", "DELETE")

    def test_06_subtitles(self) -> None:
        if not self.has_ff:
            self.skipTest("ffmpeg unavailable")
        it = self.item_by("Multi Audio Film")
        st, _, vtt = self.req(f"/api/v1/subtitles/{it['id']}/0.vtt", raw=True)
        self.assertEqual(st, 200)
        self.assertIn(b"WEBVTT", vtt)
        self.assertIn(b"Hello", vtt)
        ep = self.item_by("Episode 1")
        st, _, d = self.req(f"/api/v1/items/{ep['id']}")
        ext = [s for s in d["subtitles"] if s["external"]]
        self.assertTrue(ext)
        st, _, vtt = self.req(ext[0]["url"], raw=True)
        self.assertIn("Café crème".encode(), vtt)

    def test_07_security(self) -> None:
        st, _, _ = self.req("/api/v1/stream/../../etc/passwd/direct")
        self.assertEqual(st, 404)
        st, _, _ = self.req("/api/v1/images/..%2f..%2f/poster")
        self.assertEqual(st, 404)
        st, _, _ = self.req("/api/v1/library/scan", "POST", {}, headers={"Content-Type": "text/plain"})
        self.assertIn(st, (415, 400))
        st, _, _d = self.req("/api/v1/library/scan", "POST", {}, headers={"Origin": "http://evil.example"})
        self.assertEqual(st, 403)

    def test_08_progress_and_mylist(self) -> None:
        it = self.item_by("Big Test Movie" if self.has_ff else "Fake Movie")
        st, _, _ = self.req("/api/v1/progress", "POST", {"itemId": it["id"], "position": 20, "duration": 40})
        self.assertEqual(st, 200)
        st, _, home = self.req("/api/v1/home")
        cont = next((r for r in home["rows"] if r["id"] == "continue"), None)
        self.assertIsNotNone(cont)
        st, _, _ = self.req(f"/api/v1/mylist/{it['id']}", "POST", {})
        self.assertEqual(st, 200)
        st, _, d = self.req(f"/api/v1/items/{it['id']}")
        self.assertTrue(d["inList"])
        st, _, d = self.req("/api/v1/progress", "POST", {"itemId": it["id"], "position": 39, "duration": 40})
        self.assertTrue(d["watched"])

    def test_09_auth(self) -> None:
        st, _, _ = self.req("/api/v1/auth/password", "POST", {"password": "s3cret", "require": True})
        self.assertEqual(st, 200)
        try:
            st, _, _ = self.req("/api/v1/home")
            self.assertEqual(st, 401)
            st, _, _ = self.req("/api/v1/images/0123456789abcdef/poster")
            self.assertEqual(st, 401)
            st, _, _ = self.req("/api/v1/auth/login", "POST", {"password": "nope"})
            self.assertEqual(st, 401)
            st, h, _ = self.req("/api/v1/auth/login", "POST", {"password": "s3cret"})
            self.assertEqual(st, 200)
            cookie = h["set-cookie"].split(";")[0]
            st, _, _ = self.req("/api/v1/home", cookie=cookie)
            self.assertEqual(st, 200)
        finally:
            self.app.db.set_setting("require_password", False)
            self.app.db.x("DELETE FROM kv WHERE key='auth_hash'")
            self.app.invalidate_settings()

    def test_10_settings(self) -> None:
        st, _, d = self.req("/api/v1/settings", "PUT", {"values": {"theme": "oled", "port": 0}})
        self.assertIn("port", d["errors"])
        self.assertIn("theme", d["changed"])
        st, _, d = self.req("/api/v1/settings")
        self.assertEqual(d["values"]["theme"], "oled")
        st, _, d = self.req("/api/v1/qr?url=http://10.0.0.2:8000", raw=True)
        self.assertEqual(st, 200)


class TestLibraryScope(unittest.TestCase):
    """Auto-discovery, exclusions, nested ownership, unplugged drives and scope switching."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.paths = Paths(str(base / "home"))
        self.paths.ensure()
        self.app = App(self.paths)
        self.app.ff.ffmpeg = self.app.ff.ffprobe = None  # no probing needed
        self.base = base
        self.roots: list[Path] = []  # what "discovery" returns in these tests
        self._orig = globals()["discover_media_roots"]
        globals()["discover_media_roots"] = lambda scope: [
            {"path": str(r.resolve()), "key": str(r), "label": r.name} for r in self.roots if r.exists()
        ] if scope != "folders" else []

    def tearDown(self) -> None:
        globals()["discover_media_roots"] = self._orig
        self.app.db.close_all()
        self.tmp.cleanup()

    def mk(self, rel: str, size: int = 10) -> Path:
        p = self.base / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"\0" * size)
        return p

    def lib(self, path: Path, source: str = "user") -> int:
        if source == "auto":
            self.roots.append(path)
            self.app.db.set_setting("library_scope", "all")
        cur = self.app.db.x("INSERT INTO libraries(path,type,name,enabled,added_at,source) VALUES(?,?,?,1,?,?)",
                            (str(path.resolve()), "mixed", path.name, time.time(), source))
        return int(cur.lastrowid)

    def items(self, lib_id: int) -> set[str]:
        return {r[0] for r in self.app.db.q("SELECT rel_path FROM items WHERE library_id=? AND missing_at IS NULL",
                                            (lib_id,))}

    def scan(self) -> None:
        self.app.invalidate_settings()
        self.app.scanner.scan_all(manual=True)

    def test_discover(self) -> None:
        discover_media_roots = self._orig
        self.assertEqual(discover_media_roots("folders"), [])
        for sc in ("common", "all"):
            for d in discover_media_roots(sc):
                self.assertTrue(Path(d["path"]).is_dir(), d)
                self.assertTrue(d["key"] and d["label"])
        common = {d["path"] for d in discover_media_roots("common")}
        self.assertTrue(common <= {d["path"] for d in discover_media_roots("all")})
        self.assertTrue(auto_excluded("AppData") and auto_excluded("node_modules") and auto_excluded("Foo.app"))
        self.assertFalse(auto_excluded("Movies"))
        code = self.mk("code/index.d.ts")
        code.write_text("export declare const x: number;\n" * 50)
        tsf = self.mk("code/real.ts")
        tsf.write_bytes((b"\x47" + b"\0" * 187) * 4)
        self.assertFalse(looks_like_mpegts(str(code)))
        self.assertTrue(looks_like_mpegts(str(tsf)))

    def test_exclusions_and_min_size(self) -> None:
        root = self.base / "drive"
        self.mk("drive/Movies/Film (2020).mkv", 3 * 1024 * 1024)
        self.mk("drive/Movies/tiny-ad.mp4", 1000)
        self.mk("drive/AppData/Local/intro.mp4", 3 * 1024 * 1024)
        self.mk("drive/node_modules/x/demo.webm", 3 * 1024 * 1024)
        auto = self.lib(root, "auto")
        self.scan()
        self.assertEqual(self.items(auto), {"Movies/Film (2020).mkv"})
        self.app.db.x("UPDATE libraries SET source='user' WHERE id=?", (auto,))  # user folders: no auto rules
        self.scan()
        self.assertEqual(len(self.items(auto)), 4)

    def test_nested_owner_and_adoption(self) -> None:
        outer = self.base / "home2"
        self.mk("home2/Videos/Clip.mp4")
        self.mk("home2/Downloads/Other.mkv")
        self.app.db.set_setting("auto_min_size_mb", 0)
        o = self.lib(outer, "auto")
        self.scan()
        self.assertEqual(self.items(o), {"Videos/Clip.mp4", "Downloads/Other.mkv"})
        old_id = self.app.db.one("SELECT id FROM items WHERE library_id=? AND rel_path='Videos/Clip.mp4'", (o,))[0]
        self.app.ensure_defaults()  # creates profile 1
        self.app.db.x("INSERT INTO watch_state(profile_id,item_id,position_s,duration_s,watched,last_played_at) "
                      "VALUES(1,?,100,600,0,?)", (old_id, time.time()))
        inner = self.lib(outer / "Videos")  # user adds the nested folder: it now owns Clip.mp4
        self.scan()
        self.assertEqual(self.items(inner), {"Clip.mp4"})
        self.assertEqual(self.items(o), {"Downloads/Other.mkv"})
        new_id = self.app.db.one("SELECT id FROM items WHERE library_id=?", (inner,))[0]
        ws = self.app.db.one("SELECT position_s FROM watch_state WHERE item_id=?", (new_id,))
        self.assertIsNotNone(ws, "watch history should move to the nested library's item")
        self.assertEqual(ws[0], 100)

    def test_unplugged_drive_keeps_items(self) -> None:
        usb = self.base / "usb"
        f = self.mk("usb/Show/Season 1/Show.S01E01.mkv")
        lid = self.lib(usb)
        self.scan()
        self.assertEqual(len(self.items(lid)), 1)
        f.unlink()  # mount point still exists but is empty (drive pulled)
        self.scan()
        self.assertEqual(len(self.items(lid)), 1)
        self.assertEqual(self.app.db.one("SELECT offline FROM libraries WHERE id=?", (lid,))[0], 1)
        shutil.rmtree(usb)  # folder gone entirely
        self.scan()
        self.assertEqual(len(self.items(lid)), 1)
        self.mk("usb/Show/Season 1/Show.S01E01.mkv")  # plugged back in
        self.scan()
        self.assertEqual(self.app.db.one("SELECT offline FROM libraries WHERE id=?", (lid,))[0], 0)

    def test_scope_switch_keeps_rows(self) -> None:
        fake = self.base / "VideosDir"
        fake.mkdir()
        self.roots.append(fake)
        if True:
            self.app.db.set_setting("library_scope", "common")
            self.scan()
            row = self.app.db.one("SELECT * FROM libraries WHERE source='auto'")
            self.assertTrue(row is not None and row["enabled"])
            self.app.db.set_setting("library_scope", "folders")
            self.scan()
            row2 = self.app.db.one("SELECT * FROM libraries WHERE id=?", (row["id"],))
            self.assertEqual(row2["enabled"], 0)  # disabled, not deleted → item ids stay stable
            self.app.db.set_setting("library_scope", "common")
            self.scan()
            self.assertEqual(self.app.db.one("SELECT enabled FROM libraries WHERE id=?", (row["id"],))[0], 1)
            self.app.db.x("UPDATE libraries SET enabled=0, user_disabled=1 WHERE id=?", (row["id"],))
            self.scan()  # a folder the user hid is never re-enabled by discovery
            self.assertEqual(self.app.db.one("SELECT enabled FROM libraries WHERE id=?", (row["id"],))[0], 0)


def run_selftest(verbosity: int = 2) -> int:
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()
    for cls in (TestNameParser, TestHttpHelpers, TestSegments, TestDecision, TestSubtitles, TestPathSafety,
                TestSettingsAndSchema, TestLibraryScope, TestServerAPI):
        suite.addTests(loader.loadTestsFromTestCase(cls))
    res = unittest.TextTestRunner(verbosity=verbosity).run(suite)
    return 0 if res.wasSuccessful() else 1


# ===== SECTION: cli =====
def _add_serve_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("-f", "--folder", dest="folders", action="append", metavar="DIR", help="media folder (repeatable)")
    p.add_argument("-p", "--port", type=int, help="port (default 8000, auto-increments when busy)")
    p.add_argument("--host", help="bind address (default 0.0.0.0 = all LAN interfaces)")
    p.add_argument("--no-open", action="store_true", help="do not open a browser")
    p.add_argument("--tv", action="store_true", help="open the browser in TV mode")
    p.add_argument("--no-wizard", action="store_true", help="skip the first-run wizard")
    p.add_argument("--dev-web", metavar="PATH", help="serve index.html/app.css/app.js from PATH (live edit)")
    p.add_argument("--ssl-cert", help="HTTPS certificate (PEM)")
    p.add_argument("--ssl-key", help="HTTPS private key (PEM)")
    p.add_argument("--scope", choices=["folders", "common", "all"],
                   help="library scope (saved): only your folders, + standard video folders/USB drives, or everything")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="localflix", description=f"{APP_NAME} {VERSION} — personal media server")
    p.add_argument("--selftest", action="store_true", help="run the built-in test-suite")
    p.add_argument("--dump-web", metavar="DIR", help="write the embedded web files to DIR")
    p.add_argument("--home", metavar="DIR", help="data directory (overrides LOCALFLIX_HOME)")
    _add_serve_args(p)
    sub = p.add_subparsers(dest="cmd", metavar="command")
    _add_serve_args(sub.add_parser("serve", help="run the server (default)"))
    sub.add_parser("setup", help="interactive setup wizard")
    d = sub.add_parser("doctor", help="diagnostics report")
    d.add_argument("--full", action="store_true", help="also run test encodes")
    d.add_argument("--json", action="store_true", help="JSON output")
    s = sub.add_parser("scan", help="rescan libraries and exit")
    s.add_argument("--full", action="store_true", help="re-probe every file")
    cc = sub.add_parser("clear-cache", help="delete thumbnails, HLS segments and subtitles")
    cc.add_argument("--all", action="store_true", help="also delete keyframe/TMDB/vendor caches")
    sv = sub.add_parser("service", help="autostart helpers")
    sv.add_argument("action", nargs="?", default="print", choices=["print", "install", "uninstall"])
    sub.add_parser("version", help="print version")
    fx = sub.add_parser("fixtures", help="generate test media with ffmpeg")
    fx.add_argument("dir")
    fx.add_argument("--small", action="store_true")
    return p


def _can_open_browser() -> bool:
    if PLAT.is_docker:
        return False
    if PLAT.is_termux:  # Android: the phone's browser is opened through Termux (also from inside proot-distro)
        return True
    # a headless Linux server / SSH session has no browser to open
    return not (PLAT.is_linux and not PLAT.is_wsl and not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")))


def open_in_browser(url: str) -> bool:
    """Open ``url`` in this device's default browser: Windows, macOS, Linux desktops, WSL (Windows browser)
    and Android/Termux (also from inside proot-distro, through Termux's own launcher)."""
    def spawn(cmd: list[str]) -> bool:
        try:
            subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                             **popen_kwargs(False))
            return True
        except OSError:
            return False

    termux_prefix = "/data/data/com.termux/files/usr"
    if PLAT.is_termux:
        # Inside proot-distro the Termux tools only work with Termux's own environment (PREFIX, PATH, no proot
        # LD_PRELOAD), so run them with it, and check the exit code so a failing launcher falls through to the next.
        env = {k: v for k, v in os.environ.items() if k not in ("LD_PRELOAD", "LD_LIBRARY_PATH")}
        env.update(PREFIX=termux_prefix, HOME=os.environ.get("HOME", "/data/data/com.termux/files/home"),
                   PATH=f"{termux_prefix}/bin:/system/bin:" + os.environ.get("PATH", ""))
        for cmd in (["termux-open-url", url], [f"{termux_prefix}/bin/termux-open-url", url],
                    ["termux-open", url], [f"{termux_prefix}/bin/termux-open", url],
                    [f"{termux_prefix}/bin/am", "start", "-a", "android.intent.action.VIEW", "-d", url],
                    ["/system/bin/am", "start", "-a", "android.intent.action.VIEW", "-d", url]):
            if not (shutil.which(cmd[0]) or os.path.exists(cmd[0])):
                continue
            with contextlib.suppress(OSError, subprocess.SubprocessError):
                r = subprocess.run(cmd, check=False, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                   stderr=subprocess.STDOUT,
                                   env=env, timeout=15, **popen_kwargs(False))
                out = r.stdout.decode(errors="replace").lower()
                if r.returncode == 0 and "error" not in out and "exception" not in out:
                    return True
        return False
    if PLAT.is_wsl:
        for cmd in (["wslview", url], ["cmd.exe", "/c", "start", "", url.replace("&", "^&")],
                    ["powershell.exe", "-NoProfile", "-Command", f"Start-Process '{url}'"]):
            if shutil.which(cmd[0]) and spawn(cmd):
                return True
    try:
        return bool(webbrowser.open(url, new=2))
    except webbrowser.Error:
        return False


def cmd_serve(args: argparse.Namespace, paths: Paths) -> int:
    paths.ensure()
    first = not paths.db.exists()
    if not args.no_wizard and sys.stdin.isatty():
        db = Database(paths.db)
        done = db.kv_get("wizard_done")
        db.close_all()
        if first or not done:
            run_wizard(paths)
    app = App(paths, args.dev_web)
    setup_logging(paths, str(app.setting("log_level") or "INFO"))
    if not args.folders and os.environ.get("LOCALFLIX_MEDIA"):
        args.folders = [os.environ["LOCALFLIX_MEDIA"]]
    if getattr(args, "scope", None):
        app.db.set_setting("library_scope", args.scope)
        app.invalidate_settings()
    app.ensure_defaults(args.folders)
    port = args.port or int(os.environ.get("LOCALFLIX_PORT") or 0) or int(app.setting("port") or 8000)
    host = args.host or ("127.0.0.1" if app.setting("bind") == "localhost" else "0.0.0.0")
    app.bind(host, port)
    cert = args.ssl_cert or app.setting("ssl_cert")
    key = args.ssl_key or app.setting("ssl_key")
    if cert and key:
        try:
            app.enable_tls(cert, key)
        except (OSError, ssl.SSLError) as e:
            print(c(f"HTTPS disabled: {e}", "yellow"))

    def on_signal(signum: int, frame: Any) -> None:
        app.stop()
    for sname in ("SIGINT", "SIGTERM", "SIGBREAK", "SIGHUP"):
        sig = getattr(signal, sname, None)
        if sig is not None:
            with contextlib.suppress(ValueError, OSError):
                signal.signal(sig, on_signal)
    app.start_background()
    app.advertise_mdns()
    print_banner(app)
    if PLAT.is_termux and shutil.which("termux-wake-lock"):
        with contextlib.suppress(OSError):
            subprocess.Popen(["termux-wake-lock"], **popen_kwargs(False))
    if not args.no_open and _can_open_browser():
        # open the LAN address (the same one phones/TVs use), straight to the home page
        base = (app.urls() or [f"{'https' if app.tls else 'http'}://localhost:{app.port}"])[0]
        url = base + "/" + ("?tv=1" if args.tv else "") + "#/home"
        threading.Timer(1.0, lambda: open_in_browser(url) or log.info("Open %s in a browser", url)).start()
    log.info("Serving on %s:%d", app.host, app.port)
    try:
        assert app.httpd
        app.httpd.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        pass
    finally:
        app.stop()
        CHILDREN.kill_all()
        if PLAT.is_termux and shutil.which("termux-wake-unlock"):
            with contextlib.suppress(OSError):
                subprocess.Popen(["termux-wake-unlock"], **popen_kwargs(False))
        with contextlib.suppress(Exception):
            app.httpd.server_close()  # type: ignore[union-attr]
        app.db.close_all()
    print(c("\nLocalFlix stopped. Bye! 👋", "dim"))
    if app._restart_flag:
        print("Restarting …")
        if PLAT.is_windows:
            subprocess.Popen([sys.executable] + sys.argv)
            return 0
        os.execv(sys.executable, [sys.executable] + sys.argv)
    return 0


def main(argv: list[str] | None = None) -> int:
    setup_console()
    args = build_parser().parse_args(argv)
    if args.selftest:
        return run_selftest()
    if args.dump_web:
        out = Path(args.dump_web)
        out.mkdir(parents=True, exist_ok=True)
        for name, data in WEB_ASSETS.items():
            (out / name).write_bytes(data)
            print(f"wrote {out / name}")
        return 0
    paths = Paths(args.home)
    cmd = args.cmd or "serve"
    if cmd == "version":
        print(f"{APP_NAME} {VERSION} (Python {platform.python_version()}, {PLAT.describe()}, hls.js {HLSJS_VERSION})")
        return 0
    paths.ensure()
    setup_logging(paths, "INFO", console=cmd != "doctor")
    if cmd == "serve":
        return cmd_serve(args, paths)
    if cmd == "setup":
        if not sys.stdin.isatty():
            print("setup needs an interactive terminal")
            return 1
        run_wizard(paths)
        print(f"Start the server with:  {c(sys.executable + ' ' + Path(__file__).name, 'cyan')}")
        return 0
    if cmd == "doctor":
        app = App(paths)
        app.port = int(os.environ.get("LOCALFLIX_PORT") or app.setting("port") or 8000)
        checks = run_doctor(app, quick=not args.full)
        app.db.close_all()
        if args.json:
            print(json.dumps(checks, indent=2))
        else:
            print_doctor(checks)
        return 1 if any(ch["status"] == "fail" for ch in checks) else 0
    if cmd == "scan":
        app = App(paths)
        app.ensure_defaults()
        app.scanner.scan_all(full=args.full)
        n = 0
        while True:
            try:
                _, _, iid = app.scanner.probe_q.get_nowait()
            except queue.Empty:
                break
            app.scanner._queued.discard(iid)
            app.scanner.probe_item(iid)
            n += 1
        total = app.db.one("SELECT COUNT(*) FROM items WHERE missing_at IS NULL")[0]
        print(f"Scan complete: {total} items ({n} probed). Errors: {app.scanner.status['errors'] or 'none'}")
        app.db.close_all()
        return 0
    if cmd == "clear-cache":
        dirs = [paths.images, paths.hls, paths.subs, paths.trickplay, paths.tmp]
        if args.all:
            dirs += [paths.keyframes, paths.tmdb, paths.vendor]
        for d in dirs:
            shutil.rmtree(d, ignore_errors=True)
        paths.ensure()
        db = Database(paths.db)
        db.x("DELETE FROM artwork WHERE source IN ('generated','tmdb')")
        db.close_all()
        print("Cache cleared.")
        return 0
    if cmd == "service":
        return run_service(args.action, paths)
    if cmd == "fixtures":
        made = make_fixtures(FFmpeg(paths), Path(args.dir), small=args.small)
        print("\n".join(str(m) for m in made))
        return 0
    return 0


# ===== SECTION: web-ui =====
FAVICON_SVG = r"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64"><defs><linearGradient id="g" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#FF6B6F"/><stop offset="1" stop-color="#C2272D"/></linearGradient></defs><rect width="64" height="64" rx="14" fill="#0A0A0F"/><path d="M18 14h8v28h16v8H18z" fill="url(#g)"/><path d="M38 22l12 7-12 7z" fill="#F5F5F7"/></svg>"""

MANIFEST = r"""{"name":"LocalFlix","short_name":"LocalFlix","start_url":"/","display":"standalone","background_color":"#0A0A0F","theme_color":"#0A0A0F","icons":[{"src":"/static/favicon.svg","sizes":"any","type":"image/svg+xml"}]}"""

INDEX_HTML = r"""<!doctype html>
<html lang="en"__TV__ data-theme="dark">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="theme-color" content="#0A0A0F">
<meta name="color-scheme" content="dark light">
<meta name="description" content="LocalFlix — your personal media server">
<title>__NAME__</title>
<link rel="icon" href="/static/favicon.svg" type="image/svg+xml">
<link rel="manifest" href="/static/manifest.webmanifest">
<link rel="stylesheet" href="/static/app.css?v=__V_CSS__">
</head>
<body>
<svg xmlns="http://www.w3.org/2000/svg" style="display:none" aria-hidden="true">
<symbol id="i-play" viewBox="0 0 24 24"><path d="M7 4.5v15a1 1 0 0 0 1.5.86l12.5-7.5a1 1 0 0 0 0-1.72L8.5 3.64A1 1 0 0 0 7 4.5z" fill="currentColor"/></symbol>
<symbol id="i-pause" viewBox="0 0 24 24"><rect x="6" y="4" width="4.5" height="16" rx="1.2" fill="currentColor"/><rect x="13.5" y="4" width="4.5" height="16" rx="1.2" fill="currentColor"/></symbol>
<symbol id="i-info" viewBox="0 0 24 24"><circle cx="12" cy="12" r="9.5" fill="none" stroke="currentColor" stroke-width="2"/><path d="M12 11v6M12 7.2v.1" stroke="currentColor" stroke-width="2.4" stroke-linecap="round"/></symbol>
<symbol id="i-plus" viewBox="0 0 24 24"><path d="M12 5v14M5 12h14" stroke="currentColor" stroke-width="2.4" stroke-linecap="round"/></symbol>
<symbol id="i-check" viewBox="0 0 24 24"><path d="M5 12.5l4.5 4.5L19 7.5" fill="none" stroke="currentColor" stroke-width="2.6" stroke-linecap="round" stroke-linejoin="round"/></symbol>
<symbol id="i-search" viewBox="0 0 24 24"><circle cx="10.5" cy="10.5" r="6.5" fill="none" stroke="currentColor" stroke-width="2.2"/><path d="M15.5 15.5L20 20" stroke="currentColor" stroke-width="2.4" stroke-linecap="round"/></symbol>
<symbol id="i-tv" viewBox="0 0 24 24"><rect x="2.5" y="4.5" width="19" height="13" rx="2" fill="none" stroke="currentColor" stroke-width="2"/><path d="M8 21h8" stroke="currentColor" stroke-width="2" stroke-linecap="round"/></symbol>
<symbol id="i-back" viewBox="0 0 24 24"><path d="M15 5l-7 7 7 7" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"/></symbol>
<symbol id="i-close" viewBox="0 0 24 24"><path d="M6 6l12 12M18 6L6 18" stroke="currentColor" stroke-width="2.4" stroke-linecap="round"/></symbol>
<symbol id="i-chev-r" viewBox="0 0 24 24"><path d="M9 5l7 7-7 7" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"/></symbol>
<symbol id="i-chev-d" viewBox="0 0 24 24"><path d="M5 9l7 7 7-7" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"/></symbol>
<symbol id="i-rew" viewBox="0 0 24 24"><path d="M12 5a7 7 0 1 1-7 7" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"/><path d="M12 2L8 5l4 3z" fill="currentColor"/><text x="12" y="15.5" font-size="7.5" text-anchor="middle" fill="currentColor" font-family="sans-serif" font-weight="700">10</text></symbol>
<symbol id="i-fwd" viewBox="0 0 24 24"><path d="M12 5a7 7 0 1 0 7 7" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"/><path d="M12 2l4 3-4 3z" fill="currentColor"/><text x="12" y="15.5" font-size="7.5" text-anchor="middle" fill="currentColor" font-family="sans-serif" font-weight="700">10</text></symbol>
<symbol id="i-vol" viewBox="0 0 24 24"><path d="M4 9h4l5-4v14l-5-4H4z" fill="currentColor"/><path d="M16 8.5a5 5 0 0 1 0 7M18.5 6a8.5 8.5 0 0 1 0 12" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"/></symbol>
<symbol id="i-mute" viewBox="0 0 24 24"><path d="M4 9h4l5-4v14l-5-4H4z" fill="currentColor"/><path d="M16.5 9.5l5 5M21.5 9.5l-5 5" stroke="currentColor" stroke-width="2" stroke-linecap="round"/></symbol>
<symbol id="i-cc" viewBox="0 0 24 24"><rect x="2.5" y="5" width="19" height="14" rx="2.5" fill="none" stroke="currentColor" stroke-width="2"/><path d="M10.5 10.2a2.3 2.3 0 1 0 0 3.6M17.5 10.2a2.3 2.3 0 1 0 0 3.6" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/></symbol>
<symbol id="i-audio" viewBox="0 0 24 24"><path d="M4 12a8 8 0 0 1 16 0v5a2 2 0 0 1-2 2h-1v-6h3M4 13h3v6H6a2 2 0 0 1-2-2z" fill="none" stroke="currentColor" stroke-width="2" stroke-linejoin="round"/></symbol>
<symbol id="i-speed" viewBox="0 0 24 24"><path d="M4.5 17a8.5 8.5 0 1 1 15 0" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"/><path d="M12 14l4.5-5" stroke="currentColor" stroke-width="2.4" stroke-linecap="round"/></symbol>
<symbol id="i-hd" viewBox="0 0 24 24"><rect x="2.5" y="5" width="19" height="14" rx="2.5" fill="none" stroke="currentColor" stroke-width="2"/><text x="12" y="15.3" font-size="7.5" text-anchor="middle" fill="currentColor" font-family="sans-serif" font-weight="800">HD</text></symbol>
<symbol id="i-episodes" viewBox="0 0 24 24"><rect x="3" y="7" width="14" height="12" rx="2" fill="none" stroke="currentColor" stroke-width="2"/><path d="M7 4h12a2 2 0 0 1 2 2v9" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"/></symbol>
<symbol id="i-list" viewBox="0 0 24 24"><path d="M8 6h12M8 12h12M8 18h12M4 6h.1M4 12h.1M4 18h.1" stroke="currentColor" stroke-width="2.4" stroke-linecap="round"/></symbol>
<symbol id="i-pip" viewBox="0 0 24 24"><rect x="2.5" y="4.5" width="19" height="15" rx="2" fill="none" stroke="currentColor" stroke-width="2"/><rect x="12" y="11" width="7" height="6" rx="1" fill="currentColor"/></symbol>
<symbol id="i-airplay" viewBox="0 0 24 24"><path d="M6 17H4.5a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2h15a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2H18" fill="none" stroke="currentColor" stroke-width="2"/><path d="M12 14l5 6H7z" fill="currentColor"/></symbol>
<symbol id="i-cast" viewBox="0 0 24 24"><path d="M2.5 9V6.5a2 2 0 0 1 2-2h15a2 2 0 0 1 2 2v11a2 2 0 0 1-2 2H14" fill="none" stroke="currentColor" stroke-width="2"/><path d="M2.5 13a6.5 6.5 0 0 1 6.5 6.5M2.5 16.5a3 3 0 0 1 3 3M2.5 19.5h.1" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"/></symbol>
<symbol id="i-fs" viewBox="0 0 24 24"><path d="M4 9V4h5M20 9V4h-5M4 15v5h5M20 15v5h-5" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"/></symbol>
<symbol id="i-fs-exit" viewBox="0 0 24 24"><path d="M9 4v5H4M15 4v5h5M9 20v-5H4M15 20v-5h5" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"/></symbol>
<symbol id="i-gear" viewBox="0 0 24 24"><circle cx="12" cy="12" r="3.2" fill="none" stroke="currentColor" stroke-width="2"/><path d="M12 2.5v3M12 18.5v3M2.5 12h3M18.5 12h3M5.3 5.3l2.1 2.1M16.6 16.6l2.1 2.1M5.3 18.7l2.1-2.1M16.6 7.4l2.1-2.1" stroke="currentColor" stroke-width="2" stroke-linecap="round"/></symbol>
<symbol id="i-next" viewBox="0 0 24 24"><path d="M5 5v14l10-7z" fill="currentColor"/><rect x="16.5" y="5" width="2.5" height="14" rx="1" fill="currentColor"/></symbol>
<symbol id="i-prev" viewBox="0 0 24 24"><path d="M19 5v14L9 12z" fill="currentColor"/><rect x="5" y="5" width="2.5" height="14" rx="1" fill="currentColor"/></symbol>
<symbol id="i-moon" viewBox="0 0 24 24"><path d="M20 14.5A8.5 8.5 0 0 1 9.5 4a8.5 8.5 0 1 0 10.5 10.5z" fill="none" stroke="currentColor" stroke-width="2" stroke-linejoin="round"/></symbol>
<symbol id="i-stats" viewBox="0 0 24 24"><path d="M5 20V11M12 20V4M19 20v-6" stroke="currentColor" stroke-width="2.6" stroke-linecap="round"/></symbol>
<symbol id="i-fit" viewBox="0 0 24 24"><rect x="2.5" y="5" width="19" height="14" rx="2" fill="none" stroke="currentColor" stroke-width="2"/><path d="M7 9.5L5.5 12 7 14.5M17 9.5l1.5 2.5-1.5 2.5" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/></symbol>
<symbol id="i-home" viewBox="0 0 24 24"><path d="M3.5 11L12 4l8.5 7v8.5a1 1 0 0 1-1 1H15v-6H9v6H4.5a1 1 0 0 1-1-1z" fill="none" stroke="currentColor" stroke-width="2" stroke-linejoin="round"/></symbol>
<symbol id="i-film" viewBox="0 0 24 24"><rect x="3" y="3.5" width="18" height="17" rx="2" fill="none" stroke="currentColor" stroke-width="2"/><path d="M7 3.5v17M17 3.5v17M3 8.5h4M3 15.5h4M17 8.5h4M17 15.5h4" stroke="currentColor" stroke-width="2"/></symbol>
<symbol id="i-folder" viewBox="0 0 24 24"><path d="M3 6.5a2 2 0 0 1 2-2h4l2 2.5h8a2 2 0 0 1 2 2V17.5a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z" fill="none" stroke="currentColor" stroke-width="2" stroke-linejoin="round"/></symbol>
<symbol id="i-refresh" viewBox="0 0 24 24"><path d="M20 11a8 8 0 0 0-14.5-4.5L4 8M4 13a8 8 0 0 0 14.5 4.5L20 16" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"/><path d="M4 3.5V8h4.5M20 20.5V16h-4.5" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></symbol>
<symbol id="i-trash" viewBox="0 0 24 24"><path d="M4 7h16M9.5 7V4.5h5V7M6 7l1 13h10l1-13" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></symbol>
<symbol id="i-edit" viewBox="0 0 24 24"><path d="M4 20h4L19 9l-4-4L4 16z" fill="none" stroke="currentColor" stroke-width="2" stroke-linejoin="round"/></symbol>
<symbol id="i-lock" viewBox="0 0 24 24"><rect x="4.5" y="10.5" width="15" height="10" rx="2" fill="none" stroke="currentColor" stroke-width="2"/><path d="M8 10.5V7.5a4 4 0 0 1 8 0v3" fill="none" stroke="currentColor" stroke-width="2"/></symbol>
<symbol id="i-user" viewBox="0 0 24 24"><circle cx="12" cy="8.5" r="4" fill="none" stroke="currentColor" stroke-width="2"/><path d="M4 20.5a8 8 0 0 1 16 0" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"/></symbol>
<symbol id="i-star" viewBox="0 0 24 24"><path d="M12 3.5l2.6 5.4 5.9.8-4.3 4.1 1 5.8-5.2-2.8-5.2 2.8 1-5.8-4.3-4.1 5.9-.8z" fill="currentColor"/></symbol>
<symbol id="i-skip" viewBox="0 0 24 24"><path d="M4 5.5v13l8-6.5zM12 5.5v13l8-6.5z" fill="currentColor"/></symbol>
<symbol id="i-copy" viewBox="0 0 24 24"><rect x="8" y="8" width="12" height="12" rx="2" fill="none" stroke="currentColor" stroke-width="2"/><path d="M16 8V5.5a1.5 1.5 0 0 0-1.5-1.5h-9A1.5 1.5 0 0 0 4 5.5v9A1.5 1.5 0 0 0 5.5 16H8" fill="none" stroke="currentColor" stroke-width="2"/></symbol>
<symbol id="i-chapters" viewBox="0 0 24 24"><path d="M4 6h16M4 12h10M4 18h13" stroke="currentColor" stroke-width="2.4" stroke-linecap="round"/></symbol>
</svg>
<div id="app" aria-busy="true">
  <div class="boot"><div class="boot-logo"><span>Local</span><b>Flix</b></div><div class="boot-bar"></div>
  <noscript><p>LocalFlix needs JavaScript enabled.</p></noscript></div>
</div>
<div id="toasts" aria-live="polite" aria-atomic="false"></div>
<script nonce="__NONCE__">window.__LF__ = __BOOT__;</script>
<script src="/static/app.js?v=__V_JS__" defer></script>
</body>
</html>
"""

APP_CSS = r"""
/* LocalFlix — original design.  Core layout avoids :has(), inset, aspect-ratio and flex gap (TV browsers). */
:root{
  --bg:#0A0A0F;--bg2:#101017;--surface:#14141C;--surface2:#1C1C26;--surface3:#262633;--line:rgba(255,255,255,.09);
  --text:#F5F5F7;--muted:#A1A1AA;--dim:#71717A;--accent:#E5484D;--accent-ink:#fff;--ok:#30A46C;--warn:#FFB224;--err:#E5484D;
  --radius:10px;--radius-lg:16px;--shadow:0 10px 40px rgba(0,0,0,.55);--ease:cubic-bezier(.2,.8,.2,1);
  --font:Inter,"Segoe UI Variable","Segoe UI",-apple-system,BlinkMacSystemFont,"SF Pro Text",Roboto,system-ui,"Noto Sans","Noto Sans Devanagari",sans-serif;
  --pad:4vw;--card-w:18vw;--nav-h:68px;--fs-base:16px;
  --scale:1;
}
html[data-theme="oled"]{--bg:#000;--bg2:#050507;--surface:#0D0D12;--surface2:#16161E;--surface3:#20202A}
html[data-theme="light"]{--bg:#F6F6F8;--bg2:#ECECF1;--surface:#fff;--surface2:#F1F1F5;--surface3:#E4E4EB;--line:rgba(0,0,0,.1);
  --text:#16161D;--muted:#55555F;--dim:#7A7A85;--shadow:0 10px 30px rgba(0,0,0,.15)}
html[data-cards="s"]{--card-w:14vw}
html[data-cards="l"]{--card-w:24vw}
/* clamp() inside custom properties only where supported (older TV Chromium would drop the whole value) */
@supports (width:clamp(1px,1vw,2px)){
  :root{--card-w:clamp(160px,18vw,300px)}
  html[data-cards="s"]{--card-w:clamp(130px,14vw,230px)}
  html[data-cards="l"]{--card-w:clamp(200px,24vw,380px)}
}
*{box-sizing:border-box}
html{font-size:calc(var(--fs-base) * var(--scale));-webkit-text-size-adjust:100%;text-size-adjust:100%;background:var(--bg)}
html{overflow-x:hidden;max-width:100%}
body{margin:0;background:var(--bg);color:var(--text);font-family:var(--font);line-height:1.45;-webkit-font-smoothing:antialiased;overflow-x:hidden;min-height:100vh;max-width:100%}
a{color:inherit;text-decoration:none}
button,input,select,textarea{font:inherit;color:inherit}
button{cursor:pointer;border:0;background:none;padding:0}
img{display:block;max-width:100%}
.ico{width:1.25em;height:1.25em;display:inline-block;vertical-align:middle;flex:none}
.sr{position:absolute!important;width:1px;height:1px;overflow:hidden;clip:rect(0 0 0 0);white-space:nowrap}
:focus{outline:none}
:focus-visible{outline:3px solid var(--accent);outline-offset:3px}
.focus-ring:focus,html[data-tv] :focus{outline:3px solid var(--accent);outline-offset:3px}
html[data-tv][data-keynav] *{cursor:none!important}
.hidden{display:none!important}

/* boot */
.boot{position:fixed;top:0;left:0;right:0;bottom:0;display:flex;flex-direction:column;align-items:center;justify-content:center}
.boot-logo,.logo{font-weight:800;letter-spacing:-.03em;font-size:2.4rem;user-select:none;white-space:nowrap}
.boot-logo span,.logo span{color:var(--text)}
.boot-logo b,.logo b{color:var(--accent);font-weight:900}
.boot-bar{width:160px;height:3px;margin-top:22px;border-radius:3px;background:var(--surface2);overflow:hidden;position:relative}
.boot-bar:after{content:"";position:absolute;top:0;bottom:0;left:-40%;width:40%;background:var(--accent);animation:slide 1.1s infinite var(--ease)}
@keyframes slide{to{left:100%}}

/* buttons */
.btn{display:inline-flex;align-items:center;justify-content:center;min-height:44px;padding:0 1.2em;border-radius:8px;font-weight:650;
  background:var(--surface3);color:var(--text);transition:transform .15s var(--ease),background .15s,opacity .15s;white-space:nowrap;margin:0 .5em .5em 0}
.btn .ico{margin-right:.5em}
.btn:hover{background:color-mix(in srgb,var(--surface3) 70%,var(--text) 30%)}
.btn:active{transform:scale(.97)}
.btn-primary{background:var(--text);color:var(--bg)}
.btn-primary:hover{background:color-mix(in srgb,var(--text) 82%,transparent)}
.btn-accent{background:var(--accent);color:var(--accent-ink)}
.btn-ghost{background:rgba(255,255,255,.08)}
html[data-theme="light"] .btn-ghost{background:rgba(0,0,0,.06)}
.btn-sm{min-height:34px;padding:0 .85em;font-size:.88rem}
.btn-icon{width:44px;padding:0;border-radius:50%}
.btn-icon .ico{margin:0}
.btn[disabled]{opacity:.45;pointer-events:none}
.btn-danger{background:rgba(229,72,77,.15);color:#FF8A8D}
.chip{display:inline-flex;align-items:center;min-height:34px;padding:0 .9em;border-radius:999px;border:1px solid var(--line);
  background:var(--surface);font-size:.88rem;margin:0 .45em .45em 0;white-space:nowrap;transition:background .15s,border-color .15s}
.chip.on{background:var(--text);color:var(--bg);border-color:transparent}
.badge{display:inline-block;padding:.08em .45em;border-radius:4px;font-size:.72rem;font-weight:750;letter-spacing:.02em;
  border:1px solid rgba(255,255,255,.35);color:#fff;margin-right:.35em;line-height:1.5;vertical-align:middle}
.badge.fill{background:var(--accent);border-color:transparent}
.badge.ok{border-color:var(--ok);color:#7BE0A7}
.muted{color:var(--muted)}
.dim{color:var(--dim)}

/* nav */
.nav{position:fixed;top:0;left:0;right:0;z-index:40;height:var(--nav-h);display:flex;align-items:center;padding:0 var(--pad);
  background:linear-gradient(180deg,rgba(0,0,0,.75),rgba(0,0,0,0));transition:background .25s}
.nav.solid{background:rgba(10,10,15,.94)}
html[data-theme="light"] .nav.solid{background:rgba(246,246,248,.95)}
@supports (backdrop-filter:blur(10px)){html:not([data-lite]) .nav.solid{background:rgba(10,10,15,.72);backdrop-filter:saturate(1.4) blur(18px)}
  html[data-theme="light"]:not([data-lite]) .nav.solid{background:rgba(246,246,248,.75)}}
.nav .logo{font-size:1.65rem;margin-right:2.2vw}
.nav-links{display:flex;align-items:center}
.nav-links a{margin-right:1.4vw;font-size:.95rem;color:var(--muted);padding:.4em .1em;transition:color .15s;white-space:nowrap}
.nav-links a.on,.nav-links a:hover{color:var(--text)}
.nav-links a.on{font-weight:700}
.nav-spacer{flex:1}
.nav-act{display:flex;align-items:center}
.nav-act>*{margin-left:.6em}
.nav-search{display:flex;align-items:center;border:1px solid transparent;border-radius:6px;transition:all .2s;height:40px;overflow:hidden}
.nav-search.open{border-color:var(--line);background:rgba(0,0,0,.55)}
.nav-search input{width:0;border:0;background:transparent;padding:0;outline:none;transition:width .25s var(--ease)}
.nav-search.open input{width:clamp(140px,18vw,260px);padding-right:.6em}
.nav-search button{width:40px;height:40px;display:flex;align-items:center;justify-content:center}
.avatar{width:36px;height:36px;border-radius:8px;display:flex;align-items:center;justify-content:center;font-size:1.25rem;flex:none;overflow:hidden}
.menu{position:absolute;right:0;top:calc(100% + 8px);min-width:220px;background:var(--surface);border:1px solid var(--line);border-radius:12px;
  box-shadow:var(--shadow);padding:6px;z-index:60}
.menu button,.menu a{display:flex;align-items:center;width:100%;text-align:left;padding:.65em .8em;border-radius:8px;font-size:.95rem}
.menu button:hover,.menu a:hover,.menu button:focus,.menu a:focus{background:var(--surface2)}
.menu .ico{margin-right:.7em;color:var(--muted)}
.menu hr{border:0;border-top:1px solid var(--line);margin:6px 0}
.rel{position:relative}
.tabbar{display:none}

/* banner */
.banner{position:relative;z-index:30;margin:calc(var(--nav-h) + 8px) var(--pad) -40px;padding:.8em 1em;border-radius:10px;background:rgba(255,178,36,.12);
  border:1px solid rgba(255,178,36,.35);display:flex;align-items:center;flex-wrap:wrap;font-size:.92rem}
.banner code{background:rgba(0,0,0,.35);padding:.15em .45em;border-radius:5px;margin:0 .3em;user-select:all}
.banner .grow{flex:1;min-width:200px;margin-right:1em}

/* hero */
.hero{position:relative;height:82vh;height:min(82vh,56.25vw + 80px);min-height:420px;overflow:hidden;margin-bottom:-6vw}
.hero-bg{position:absolute;top:0;left:0;right:0;bottom:0;background-size:cover;background-position:center 20%;transition:opacity .8s;opacity:0}
.hero-bg.on{opacity:1}
.hero:after{content:"";position:absolute;top:0;left:0;right:0;bottom:0;pointer-events:none;
  background:linear-gradient(90deg,rgba(10,10,15,.92) 0%,rgba(10,10,15,.55) 38%,rgba(10,10,15,0) 70%),linear-gradient(0deg,var(--bg) 2%,rgba(10,10,15,0) 38%)}
html[data-theme="light"] .hero:after{background:linear-gradient(90deg,rgba(0,0,0,.75) 0%,rgba(0,0,0,.35) 40%,rgba(0,0,0,0) 70%),linear-gradient(0deg,var(--bg) 2%,rgba(0,0,0,0) 38%)}
.hero-body{position:absolute;z-index:2;left:var(--pad);bottom:max(14vw,120px);width:min(560px,46vw);color:#fff}
.hero-title{font-size:clamp(1.9rem,4.2vw,4rem);font-weight:850;line-height:1.02;letter-spacing:-.02em;margin:0 0 .3em;text-shadow:0 2px 20px rgba(0,0,0,.5)}
.hero-meta{font-size:.95rem;color:rgba(255,255,255,.85);margin-bottom:.8em}
.hero-meta>span{margin-right:.7em}
.hero-ov{font-size:1.02rem;color:rgba(255,255,255,.88);display:-webkit-box;-webkit-line-clamp:3;-webkit-box-orient:vertical;overflow:hidden;margin:0 0 1.2em;text-shadow:0 1px 8px rgba(0,0,0,.6)}
.hero-dots{position:absolute;z-index:3;right:var(--pad);bottom:max(14vw,120px);display:flex}
.hero-dots i{width:22px;height:3px;border-radius:2px;background:rgba(255,255,255,.35);margin-left:6px;transition:background .3s}
.hero-dots i.on{background:#fff}
.page-pad{padding-top:calc(var(--nav-h) + 24px)}

/* rows */
.rows{position:relative;z-index:5;padding-bottom:60px}
.row{margin:0 0 2.4vw;position:relative}
.row-h{display:flex;align-items:baseline;padding:0 var(--pad);margin:0 0 .55em}
.row-h h2{font-size:clamp(1.05rem,1.3vw,1.45rem);font-weight:750;margin:0;letter-spacing:-.01em}
.row-h a{margin-left:.9em;font-size:.85rem;color:var(--muted)}
.scroller{display:flex;overflow-x:auto;overflow-y:hidden;scroll-behavior:smooth;padding:10px var(--pad) 18px;scrollbar-width:none;-webkit-overflow-scrolling:touch;scroll-padding:0 var(--pad)}
.scroller::-webkit-scrollbar{display:none}
.scroller>*{flex:none;margin-right:.6vw}
.row-arrow{position:absolute;top:2.2em;bottom:18px;width:calc(var(--pad) - 6px);z-index:6;display:flex;align-items:center;justify-content:center;
  background:rgba(10,10,15,.55);opacity:0;transition:opacity .2s;color:#fff}
.row:hover .row-arrow{opacity:1}
.row-arrow.l{left:0;border-radius:0 8px 8px 0}
.row-arrow.r{right:0;border-radius:8px 0 0 8px}
.row-arrow .ico{width:2em;height:2em}

/* cards */
.card{width:var(--card-w);position:relative;border-radius:var(--radius);transition:transform .22s var(--ease),box-shadow .22s;cursor:pointer;display:block}
.card .ratio{position:relative;padding-top:56.25%;border-radius:var(--radius);overflow:hidden;background:var(--surface2)}
.card.poster .ratio{padding-top:150%}
.ph{position:absolute;top:0;left:0;right:0;bottom:0;display:flex;align-items:flex-end;padding:.7em;font-weight:750;font-size:1.02rem;line-height:1.15;color:#fff;
  text-shadow:0 1px 6px rgba(0,0,0,.6)}
.card img,.ep-thumb img{position:absolute;top:0;left:0;width:100%;height:100%;object-fit:cover;opacity:0;transition:opacity .35s}
.card img.ok,.ep-thumb img.ok{opacity:1}
.card .shade{position:absolute;left:0;right:0;bottom:0;height:60%;background:linear-gradient(0deg,rgba(0,0,0,.85),rgba(0,0,0,0));opacity:0;transition:opacity .2s}
.card img.ok~.shade{opacity:1}
.card .ct{position:absolute;left:.7em;right:.7em;bottom:.6em;color:#fff;font-weight:700;font-size:.93rem;line-height:1.2;text-shadow:0 1px 6px rgba(0,0,0,.7);
  display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}
.card .ct small{display:block;font-weight:500;opacity:.85;font-size:.78rem}
.card .bdg{position:absolute;top:.5em;left:.5em;right:.5em;display:flex;flex-wrap:wrap}
.card .bdg .badge{background:rgba(0,0,0,.55);border-color:transparent;margin-bottom:3px}
.card .bdg .badge.fill{background:var(--accent)}
.card .prog{position:absolute;left:0;right:0;bottom:0;height:4px;background:rgba(255,255,255,.25)}
.card .prog i{display:block;height:100%;background:var(--accent)}
.card .wchk{position:absolute;top:.5em;right:.5em;width:24px;height:24px;border-radius:50%;background:var(--ok);display:flex;align-items:center;justify-content:center;color:#fff}
.card .wchk .ico{width:15px;height:15px}
.card .below{padding:.45em .1em 0;font-size:.82rem;color:var(--muted);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.card:hover,.card:focus{transform:scale(1.05);z-index:5}
html[data-tv] .card:focus{transform:scale(1.1);box-shadow:0 0 0 4px var(--accent),0 16px 40px rgba(0,0,0,.6)}
.card:focus{outline-offset:2px}
.skel{background:linear-gradient(90deg,var(--surface) 0%,var(--surface2) 40%,var(--surface) 80%);background-size:300% 100%;animation:shim 1.4s infinite linear}
@keyframes shim{0%{background-position:100% 0}100%{background-position:-100% 0}}
.skel-card{width:var(--card-w);margin-right:.6vw;border-radius:var(--radius)}
.skel-card div{padding-top:56.25%;border-radius:var(--radius)}

/* hover preview */
.mini{position:absolute;z-index:80;width:calc(var(--card-w) * 1.5);background:var(--surface);border-radius:12px;box-shadow:0 18px 50px rgba(0,0,0,.7);overflow:hidden;
  transform-origin:center;animation:pop .2s var(--ease)}
@keyframes pop{from{transform:scale(.8);opacity:0}to{transform:scale(1);opacity:1}}
.mini .ratio{position:relative;padding-top:56.25%;background:var(--surface2)}
.mini .ratio img{position:absolute;top:0;left:0;width:100%;height:100%;object-fit:cover}
.mini-b{padding:.8em 1em 1em}
.mini-b .acts{display:flex;align-items:center;margin-bottom:.6em}
.mini-b .acts .btn{margin:0 .4em 0 0}
.mini-b .t{font-weight:750;margin-bottom:.25em}
.mini-b .m{font-size:.82rem;color:var(--muted)}
.mini-b .m span{margin-right:.6em}

/* grid pages */
.page{padding:calc(var(--nav-h) + 26px) var(--pad) 80px;min-height:100vh}
.page h1{font-size:clamp(1.6rem,2.6vw,2.4rem);margin:0 0 .5em;letter-spacing:-.02em}
.toolbar{display:flex;flex-wrap:wrap;align-items:center;margin-bottom:1.2em}
.toolbar select{background:var(--surface);border:1px solid var(--line);border-radius:999px;min-height:34px;padding:0 .9em;margin:0 .45em .45em 0}
.grid{display:flex;flex-wrap:wrap;margin-right:-.9vw}
.grid>.card{margin:0 .9vw 1.8vw 0;width:calc((100% - 6 * .9vw) / 6)}
.grid>.card.poster{width:calc((100% - 7 * .9vw) / 7)}
@supports (display:grid){.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(var(--card-w),1fr));grid-gap:1.6vw .9vw;margin-right:0}
  .grid>.card,.grid>.card.poster{width:auto;margin:0}
  .grid.posters{grid-template-columns:repeat(auto-fill,minmax(calc(var(--card-w) * .62),1fr))}}
.load-more{display:flex;justify-content:center;padding:20px}
.empty{text-align:center;padding:12vh 20px;max-width:640px;margin:0 auto}
.empty .art{font-size:4.5rem;margin-bottom:.2em}
.empty h2{margin:.2em 0}
.empty code{display:inline-block;background:var(--surface2);padding:.4em .7em;border-radius:8px;margin:.6em 0;word-break:break-all;user-select:all}

/* modal */
.overlay{position:fixed;top:0;left:0;right:0;bottom:0;z-index:100;background:rgba(0,0,0,.72);overflow-y:auto;-webkit-overflow-scrolling:touch;animation:fade .2s}
@keyframes fade{from{opacity:0}}
.modal{position:relative;margin:4vh auto 6vh;width:min(960px,94vw);background:var(--surface);border-radius:var(--radius-lg);box-shadow:var(--shadow);overflow:hidden;animation:rise .25s var(--ease)}
.modal.sm{width:min(520px,94vw);padding:1.6em}
@keyframes rise{from{transform:translateY(24px);opacity:0}}
.modal-x{position:absolute;top:14px;right:14px;z-index:5;background:rgba(0,0,0,.6);color:#fff}
.d-hero{position:relative;padding-top:50%;background:var(--surface2) center/cover no-repeat}
.d-hero:after{content:"";position:absolute;top:0;left:0;right:0;bottom:0;background:linear-gradient(0deg,var(--surface) 0%,rgba(20,20,28,0) 60%)}
html[data-theme="light"] .d-hero:after{background:linear-gradient(0deg,var(--surface) 0%,rgba(255,255,255,0) 60%)}
.d-head{position:absolute;left:2em;right:2em;bottom:1.4em;z-index:2}
.d-head h1{font-size:clamp(1.6rem,3.4vw,2.8rem);margin:0 0 .4em;line-height:1.05;letter-spacing:-.02em;text-shadow:0 2px 14px rgba(0,0,0,.5);color:#fff}
.d-body{padding:0 2em 2em}
.d-cols{display:flex;flex-wrap:wrap}
.d-main{flex:2 1 380px;margin-right:2em}
.d-side{flex:1 1 220px;font-size:.88rem;color:var(--muted)}
.d-side b{color:var(--text);font-weight:600}
.d-side p{margin:0 0 .7em}
.d-meta{margin:.4em 0 1em;color:var(--muted);font-size:.95rem}
.d-meta>span{margin-right:.8em}
.d-ov{font-size:1rem;line-height:1.55;margin:0 0 1em}
.tracks{margin-top:1.2em;border-top:1px solid var(--line);padding-top:1em}
.tracks h3{font-size:1rem;margin:.6em 0 .4em}
.tracks li{list-style:none;padding:.25em 0;color:var(--muted);font-size:.9rem}
.tracks ul{padding:0;margin:0}
.seasons{display:flex;align-items:center;justify-content:space-between;margin:1.4em 0 .6em;flex-wrap:wrap}
.seasons h2{margin:0;font-size:1.3rem}
.seasons select{background:var(--surface2);border:1px solid var(--line);border-radius:8px;padding:.55em 1em;font-weight:650}
.ep{display:flex;align-items:center;padding:1em .8em;border-bottom:1px solid var(--line);border-radius:10px;cursor:pointer;transition:background .15s}
.ep:hover,.ep:focus{background:var(--surface2)}
.ep-n{width:2.2em;font-size:1.4rem;color:var(--muted);text-align:center;flex:none;margin-right:.6em}
.ep-thumb{position:relative;width:min(160px,28vw);flex:none;border-radius:8px;overflow:hidden;background:var(--surface3);margin-right:1em}
.ep-thumb>div{padding-top:56.25%}
.ep-thumb .prog{position:absolute;left:0;right:0;bottom:0;height:3px;background:rgba(255,255,255,.25)}
.ep-thumb .prog i{display:block;height:100%;background:var(--accent)}
.ep-b{flex:1;min-width:0}
.ep-t{display:flex;justify-content:space-between;font-weight:650}
.ep-t span:last-child{color:var(--muted);font-weight:500;margin-left:1em;white-space:nowrap}
.ep-o{font-size:.86rem;color:var(--muted);margin-top:.3em;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}

/* forms / settings */
.set{display:flex;min-height:100vh;padding-top:var(--nav-h)}
.set-nav{width:260px;flex:none;padding:2em 1em 2em var(--pad);border-right:1px solid var(--line)}
.set-nav h1{font-size:1.5rem;margin:0 0 .8em .4em}
.set-nav a{display:flex;align-items:center;padding:.7em .8em;border-radius:8px;color:var(--muted);margin-bottom:2px}
.set-nav a.on,.set-nav a:hover,.set-nav a:focus{background:var(--surface2);color:var(--text)}
.set-main{flex:1;padding:2em var(--pad) 6em 3em;max-width:980px;min-width:0}
.set-main h2{margin:0 0 1em;font-size:1.5rem}
.field{display:flex;align-items:center;justify-content:space-between;padding:1em 0;border-bottom:1px solid var(--line)}
.field .lbl{flex:1;margin-right:1.4em;min-width:0}
.field .lbl b{display:block;font-weight:600}
.field .lbl small{color:var(--muted);font-size:.84rem}
.field .ctl{flex:none;max-width:60%;text-align:right}
.field.dis{opacity:.5}
.tag{font-size:.7rem;padding:.1em .45em;border-radius:4px;background:var(--surface3);color:var(--muted);margin-left:.5em;vertical-align:middle;font-weight:600}
.tag.warn{background:rgba(255,178,36,.15);color:#FFCB66}
.input{background:var(--surface2);border:1px solid var(--line);border-radius:8px;padding:.6em .8em;min-height:42px;min-width:220px;max-width:100%}
.input:focus{border-color:var(--accent)}
select.input{padding-right:2em}
.toggle{position:relative;width:52px;height:30px;border-radius:15px;background:var(--surface3);transition:background .2s;flex:none;display:inline-block;vertical-align:middle}
.toggle:after{content:"";position:absolute;top:3px;left:3px;width:24px;height:24px;border-radius:50%;background:#fff;transition:transform .2s var(--ease);box-shadow:0 1px 4px rgba(0,0,0,.4)}
.toggle[aria-checked="true"]{background:var(--accent)}
.toggle[aria-checked="true"]:after{transform:translateX(22px)}
.seg{display:inline-flex;background:var(--surface2);border-radius:9px;padding:3px;flex-wrap:wrap;justify-content:flex-end}
.seg button{padding:.45em .85em;border-radius:7px;font-size:.88rem;color:var(--muted);min-height:36px}
.seg button.on{background:var(--surface3);color:var(--text);font-weight:650}
.range{display:flex;align-items:center}
.range input{width:180px;accent-color:var(--accent)}
.range output{width:4em;text-align:right;color:var(--muted);font-variant-numeric:tabular-nums}
.swatches{display:flex;flex-wrap:wrap;justify-content:flex-end}
.swatches button{width:30px;height:30px;border-radius:50%;margin:0 0 6px 6px;border:2px solid transparent}
.swatches button.on{border-color:var(--text);box-shadow:0 0 0 2px var(--bg) inset}
.card-box{background:var(--surface);border:1px solid var(--line);border-radius:12px;padding:1em 1.2em;margin:0 0 1em}
.lib{display:flex;align-items:center;padding:.8em 0;border-bottom:1px solid var(--line)}
.lib:last-child{border-bottom:0}
.lib .grow{flex:1;min-width:0;margin:0 1em}
.lib .grow b{display:block;word-break:break-all}
.sub-prev{position:relative;height:150px;border-radius:12px;overflow:hidden;background:linear-gradient(135deg,#283048,#859398);margin:1em 0}
.check{display:flex;padding:.6em 0;border-bottom:1px solid var(--line);font-size:.92rem}
.check .s{width:2em;flex:none}
.check .fix{color:var(--muted);font-size:.84rem;margin-top:.2em}
.logs{background:#000;color:#d4d4d8;font:12px/1.5 ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;padding:1em;border-radius:10px;max-height:60vh;overflow:auto;white-space:pre-wrap;word-break:break-all}
.fsb{max-height:50vh;overflow:auto;border:1px solid var(--line);border-radius:10px;margin:.8em 0}
.fsb button{display:flex;align-items:center;width:100%;text-align:left;padding:.65em .9em;border-bottom:1px solid var(--line)}
.fsb button:hover,.fsb button:focus{background:var(--surface2)}
.fsb .ico{margin-right:.6em;color:var(--muted)}

/* profiles / login */
.who{min-height:100vh;display:flex;flex-direction:column;align-items:center;justify-content:center;padding:4vh 4vw;text-align:center}
.who h1{font-size:clamp(1.8rem,3.6vw,3.2rem);font-weight:700;margin:0 0 1.2em;letter-spacing:-.02em}
.who-list{display:flex;flex-wrap:wrap;justify-content:center;max-width:1100px}
.who-p{width:11vw;min-width:110px;max-width:190px;margin:0 1.2vw 2.4vw;display:flex;flex-direction:column;align-items:center;color:var(--muted);border-radius:12px;padding:6px;transition:color .2s}
.who-p .avatar{width:100%;height:auto;padding-top:100%;position:relative;border-radius:14px;font-size:clamp(2.6rem,4.4vw,5rem);transition:transform .2s var(--ease),box-shadow .2s}
.who-p .avatar span{position:absolute;top:50%;left:50%;transform:translate(-50%,-50%)}
.who-p:hover,.who-p:focus{color:var(--text)}
.who-p:hover .avatar,.who-p:focus .avatar{box-shadow:0 0 0 4px var(--text);transform:scale(1.04)}
.who-p .nm{margin-top:.7em;font-size:1.05rem;display:flex;align-items:center}
.who-p .nm .ico{width:.9em;height:.9em;margin-left:.3em}
.pinpad{display:flex;flex-wrap:wrap;width:270px;margin:1em auto 0;justify-content:center}
.pinpad button{width:76px;height:64px;margin:5px;border-radius:12px;background:var(--surface2);font-size:1.5rem;font-weight:650}
.pinpad button:focus,.pinpad button:hover{background:var(--surface3)}
.pin-dots{display:flex;justify-content:center;margin:1em 0}
.pin-dots i{width:16px;height:16px;border-radius:50%;border:2px solid var(--muted);margin:0 8px}
.pin-dots i.on{background:var(--text);border-color:var(--text)}
.shake{animation:shake .35s}
@keyframes shake{25%{transform:translateX(-8px)}75%{transform:translateX(8px)}}
.avatars{display:flex;flex-wrap:wrap;justify-content:center;margin:.8em 0}
.avatars button{margin:5px;border-radius:10px;border:3px solid transparent}
.avatars button.on{border-color:var(--text)}
.osk{display:flex;flex-wrap:wrap;max-width:560px;margin:1em auto 0;justify-content:center}
.osk button{min-width:46px;height:46px;margin:3px;border-radius:8px;background:var(--surface2);font-weight:650;padding:0 .6em}
.osk button:focus{background:var(--text);color:var(--bg)}
.osk button.wide{min-width:120px}

/* toasts */
#toasts{position:fixed;left:50%;bottom:28px;transform:translateX(-50%);z-index:300;display:flex;flex-direction:column;align-items:center;pointer-events:none;width:min(92vw,520px)}
.toast{background:var(--surface3);color:var(--text);padding:.8em 1.2em;border-radius:10px;box-shadow:var(--shadow);margin-top:8px;font-size:.95rem;pointer-events:auto;animation:rise .25s var(--ease);display:flex;align-items:center}
.toast.err{background:#4A1517;color:#FFD1D2}
.toast.ok{background:#11331F;color:#BFF2D3}
.toast button{margin-left:1em;font-weight:700;color:var(--accent)}

/* open on tv */
.tvm{text-align:center}
.tvm .url{font-size:clamp(1.4rem,3.2vw,2.3rem);font-weight:800;letter-spacing:-.01em;margin:.3em 0;word-break:break-all;user-select:all}
.tvm .qr{width:220px;height:220px;margin:1em auto;background:#fff;border-radius:12px;padding:8px}
.tvm .qr img{width:100%;height:100%;image-rendering:pixelated}
.tvm ol{text-align:left;color:var(--muted);max-width:420px;margin:1em auto;padding-left:1.3em}
.tvm ol li{margin:.3em 0}

/* player */
.player{position:fixed;top:0;left:0;right:0;bottom:0;z-index:200;background:#000;color:#fff;overflow:hidden;user-select:none;-webkit-user-select:none;touch-action:manipulation}
.player video{position:absolute;top:0;left:0;width:100%;height:100%;object-fit:contain;background:#000}
.player.fit-cover video{object-fit:cover}
.player.fit-fill video{object-fit:fill}
.player.fit-crop video{object-fit:cover;transform:scale(1.33,1)}
.player .bright{position:absolute;top:0;left:0;right:0;bottom:0;background:#000;opacity:0;pointer-events:none}
.p-ui{position:absolute;top:0;left:0;right:0;bottom:0;transition:opacity .3s;opacity:1}
.player.idle .p-ui{opacity:0}
/* the overlay is click-through: taps/double-taps on the picture reach the gesture layer; only real controls catch them */
.p-ui,.p-top,.p-bot{pointer-events:none}
.p-ui button,.p-ui input,.p-ui .seek,.p-ui .p-time,.p-ui .ttl{pointer-events:auto}
.player.idle .p-ui,.player.idle .p-ui *{pointer-events:none!important}
.player.idle{cursor:none}
.p-top{position:absolute;top:0;left:0;right:0;padding:18px 3vw 60px;display:flex;align-items:center;background:linear-gradient(180deg,rgba(0,0,0,.75),rgba(0,0,0,0))}
.p-top .ttl{margin-left:1em;min-width:0}
.p-top .ttl b{display:block;font-size:1.2rem;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.p-top .ttl small{color:rgba(255,255,255,.75)}
.p-bot{position:absolute;left:0;right:0;bottom:0;padding:60px 3vw max(18px,env(safe-area-inset-bottom));background:linear-gradient(0deg,rgba(0,0,0,.85),rgba(0,0,0,0))}
.p-btn{width:48px;height:48px;border-radius:50%;display:inline-flex;align-items:center;justify-content:center;color:#fff;transition:background .15s,transform .15s;flex:none;position:relative}
.p-btn:hover,.p-btn:focus{background:rgba(255,255,255,.14)}
.p-btn .ico{width:28px;height:28px}
.p-btn.on:after{content:"";position:absolute;bottom:5px;left:50%;width:16px;height:3px;margin-left:-8px;border-radius:2px;background:var(--accent)}
.p-row{display:flex;align-items:center}
.p-row>*{margin-right:.3em}
.p-time{font-variant-numeric:tabular-nums;font-size:.95rem;margin:0 .8em;white-space:nowrap;cursor:pointer}
.p-spacer{flex:1}
.p-vol{display:flex;align-items:center}
.p-vol input{width:0;opacity:0;transition:width .2s,opacity .2s;accent-color:#fff}
.p-vol:hover input,.p-vol:focus-within input{width:90px;opacity:1}
.seek{position:relative;height:28px;display:flex;align-items:center;cursor:pointer;margin-bottom:6px;touch-action:none}
.seek .trk{position:relative;height:4px;width:100%;background:rgba(255,255,255,.25);border-radius:3px;transition:height .15s}
.seek:hover .trk,.seek.drag .trk,.seek:focus .trk{height:7px}
.seek .buf,.seek .pl{position:absolute;top:0;left:0;bottom:0;border-radius:3px}
.seek .buf{background:rgba(255,255,255,.4)}
.seek .pl{background:var(--accent)}
.seek .knob{position:absolute;top:50%;width:16px;height:16px;margin:-8px 0 0 -8px;border-radius:50%;background:var(--accent);transform:scale(0);transition:transform .15s}
.seek:hover .knob,.seek.drag .knob,.seek:focus .knob{transform:scale(1)}
.seek .chap{position:absolute;top:-1px;bottom:-1px;width:3px;background:rgba(0,0,0,.75)}
.seek .mark{position:absolute;top:0;bottom:0;background:rgba(255,224,102,.35)}
.preview{position:absolute;bottom:42px;transform:translateX(-50%);text-align:center;pointer-events:none;display:none}
.preview .img{width:240px;height:135px;border-radius:8px;border:2px solid #fff;background:#111 no-repeat;box-shadow:0 8px 30px rgba(0,0,0,.6);overflow:hidden}
.preview .tm{margin-top:6px;font-weight:700;text-shadow:0 1px 4px #000;font-variant-numeric:tabular-nums}
.preview .cn{font-size:.82rem;opacity:.85}
.p-center{position:absolute;top:50%;left:50%;transform:translate(-50%,-50%);display:flex;flex-direction:column;align-items:center;pointer-events:none}
.spinner{width:64px;height:64px;border-radius:50%;border:4px solid rgba(255,255,255,.2);border-top-color:var(--accent);animation:spin .9s linear infinite}
@keyframes spin{to{transform:rotate(360deg)}}
.p-center .msg{margin-top:14px;font-size:.95rem;text-shadow:0 1px 6px #000;text-align:center;max-width:80vw}
.p-bigplay{width:84px;height:84px;border-radius:50%;background:rgba(0,0,0,.45);display:flex;align-items:center;justify-content:center;pointer-events:auto}
.p-bigplay .ico{width:46px;height:46px;margin-left:4px}
.subs{position:absolute;left:5%;right:5%;bottom:var(--sub-bottom,8%);text-align:center;pointer-events:none;transition:bottom .3s;z-index:3}
.player:not(.idle) .subs{bottom:calc(var(--sub-bottom,8%) + 90px)}
.subs .cue{display:inline-block;padding:.12em .45em;border-radius:4px;line-height:1.35;white-space:pre-wrap;font-size:var(--sub-size,2.4vw);
  color:var(--sub-color,#fff);background:rgba(0,0,0,var(--sub-bg,.4));font-weight:550;margin-top:.15em}
.subs.edge-shadow .cue{text-shadow:0 2px 4px rgba(0,0,0,.9),0 0 8px rgba(0,0,0,.7)}
.subs.edge-outline .cue{text-shadow:-1.5px -1.5px 0 #000,1.5px -1.5px 0 #000,-1.5px 1.5px 0 #000,1.5px 1.5px 0 #000}
.subs.edge-raised .cue{text-shadow:1px 1px 0 #333,2px 2px 0 #000}
.subs.font-serif .cue{font-family:Georgia,"Times New Roman",serif}
.subs.font-mono .cue{font-family:ui-monospace,Menlo,Consolas,monospace}
.subs.font-rounded .cue{font-family:"Arial Rounded MT Bold","Nunito","Varela Round",var(--font)}
.p-menu{position:absolute;right:3vw;bottom:110px;width:min(360px,90vw);max-height:60vh;overflow-y:auto;background:rgba(20,20,28,.96);border-radius:14px;padding:8px;box-shadow:var(--shadow);z-index:10}
.p-menu h4{margin:.5em .8em .4em;font-size:.82rem;text-transform:uppercase;letter-spacing:.08em;color:rgba(255,255,255,.6)}
.p-menu button{display:flex;align-items:center;width:100%;text-align:left;padding:.7em .8em;border-radius:8px;font-size:.95rem;color:#fff}
.p-menu button:hover,.p-menu button:focus{background:rgba(255,255,255,.1)}
.p-menu button .ck{width:1.4em;flex:none;color:var(--accent)}
.p-menu button small{margin-left:auto;padding-left:1em;color:rgba(255,255,255,.55);font-size:.8rem}
.p-menu .p-back{font-weight:700;border-bottom:1px solid rgba(255,255,255,.12);border-radius:8px 8px 0 0;margin-bottom:4px}
.p-menu .eps button{align-items:flex-start}
.skipbtn{position:absolute;right:3vw;bottom:130px;z-index:6;background:rgba(255,255,255,.92);color:#111;font-weight:750;padding:.75em 1.3em;border-radius:8px;display:flex;align-items:center;box-shadow:var(--shadow)}
.skipbtn .ico{margin-left:.4em}
.skipbtn:focus{outline:3px solid var(--accent);outline-offset:3px}
.nextcard{position:absolute;right:3vw;bottom:130px;z-index:7;width:min(380px,86vw);background:rgba(20,20,28,.96);border-radius:14px;overflow:hidden;box-shadow:var(--shadow)}
.nextcard .ratio{position:relative;padding-top:56.25%;background:#222 center/cover}
.nextcard .ring{position:absolute;bottom:10px;right:10px;width:46px;height:46px}
.nextcard .b{padding:.9em 1em}
.nextcard .b small{color:rgba(255,255,255,.65);text-transform:uppercase;letter-spacing:.08em;font-size:.72rem}
.nextcard .b b{display:block;margin:.2em 0 .7em}
.p-dialog{position:absolute;top:50%;left:50%;transform:translate(-50%,-50%);background:rgba(20,20,28,.96);padding:1.6em;border-radius:16px;text-align:center;z-index:12;width:min(440px,90vw);box-shadow:var(--shadow)}
.p-dialog h3{margin:0 0 .4em}
.p-dialog p{color:rgba(255,255,255,.75);margin:0 0 1.2em}
.stats{position:absolute;top:80px;left:3vw;z-index:9;background:rgba(0,0,0,.72);padding:.8em 1em;border-radius:10px;font:12px/1.6 ui-monospace,Menlo,Consolas,monospace;min-width:280px}
.stats b{color:var(--accent);font-weight:700}
.ripple{position:absolute;top:50%;width:120px;height:120px;margin-top:-60px;border-radius:50%;background:rgba(255,255,255,.18);display:flex;align-items:center;justify-content:center;flex-direction:column;font-weight:700;animation:rip .6s forwards;pointer-events:none}
@keyframes rip{from{transform:scale(.6);opacity:1}to{transform:scale(1.15);opacity:0}}
.osd{position:absolute;top:18%;left:50%;transform:translateX(-50%);background:rgba(0,0,0,.6);padding:.5em 1em;border-radius:8px;font-weight:700;font-size:1.1rem;pointer-events:none}


/* details page (movie / episode / series) */
.dp-modal{width:min(1100px,96vw);margin:3vh auto 5vh}
.dp-hero{position:relative;padding-top:44%;background:var(--surface2) center 20%/cover no-repeat}
.dp-shade{position:absolute;top:0;left:0;right:0;bottom:0;background:linear-gradient(0deg,var(--surface) 2%,rgba(20,20,28,0) 58%),linear-gradient(90deg,rgba(0,0,0,.55),rgba(0,0,0,0) 62%)}
.dp-head{position:relative;z-index:2;margin-top:-15%;padding:0 3rem}
.dp-kicker{font-size:.72rem;font-weight:800;letter-spacing:.28em;text-transform:uppercase;color:var(--accent)}
.dp-title{font-size:2.4rem;font-weight:850;line-height:1.05;letter-spacing:-.02em;margin:.12em 0 .3em;text-shadow:0 2px 18px rgba(0,0,0,.45);max-width:80%}
@supports (font-size:clamp(1px,1vw,2px)){.dp-title{font-size:clamp(1.7rem,3.4vw,3.1rem)}}
.dp-sub{font-weight:650;margin:.1em 0 .55em;font-size:1rem}
.dp-meta{display:flex;flex-wrap:wrap;align-items:center;color:var(--muted);font-size:.92rem}
.dp-meta>span{margin:0 .75em .45em 0;white-space:nowrap}
.dp-new{color:#46D369;font-weight:800}
.dp-rating{border:1px solid currentColor;padding:0 .4em;font-size:.8rem;border-radius:2px}
.dp-box{border:1px solid currentColor;border-radius:3px;padding:0 .35em;font-size:.7rem;font-weight:750;line-height:1.5}
.dp-star{color:#F5C518}
.dp-prog{display:flex;align-items:center;margin:.3em 0 .2em;font-size:.85rem;color:var(--muted)}
.dp-bar{width:200px;max-width:50%;height:3px;background:rgba(127,127,127,.35);margin-right:.8em;border-radius:2px;overflow:hidden}
.dp-bar i{display:block;height:100%;background:var(--accent)}
.dp-actions{display:flex;align-items:center;flex-wrap:wrap;margin-top:1rem}
.dp-actions .btn{margin:0 .6rem .6rem 0}
.dp-play{min-width:9.5rem;font-size:1.02rem}
.dp-rounds{display:flex;align-items:center}
.dp-round{width:44px;height:44px;border-radius:50%;border:2px solid rgba(255,255,255,.5);background:rgba(30,30,30,.55);color:#fff;display:inline-flex;align-items:center;justify-content:center;margin:0 .55rem .6rem 0;transition:border-color .15s,transform .15s;flex:none}
html[data-theme="light"] .dp-round{border-color:rgba(0,0,0,.35);background:rgba(255,255,255,.7);color:#111}
.dp-round:hover,.dp-round:focus{border-color:#fff;transform:scale(1.06)}
.dp-round.on{border-color:var(--ok);color:var(--ok)}
.dp-round .ico{width:22px;height:22px}
.dp-round-l{position:absolute;width:1px;height:1px;overflow:hidden;clip:rect(0 0 0 0);white-space:nowrap}
.dp-body{padding:1.1rem 3rem 2.6rem}
.dp-cols{display:flex;flex-wrap:wrap}
.dp-main{flex:2 1 400px;margin-right:2.5rem;min-width:0}
.dp-side{flex:1 1 220px;font-size:.88rem;line-height:1.5;min-width:0}
.dp-side p{margin:0 0 .7em}
.dp-side .dim,.dp-about .dim{color:var(--dim)}
.dp-ov{font-size:1rem;line-height:1.6;margin:0 0 1em}
.dp-sec{margin-top:2rem}
.dp-sec h2{font-size:1.3rem;font-weight:750;margin:0 0 .8rem}
.dp-sec-h{display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;margin-bottom:.4rem}
.dp-sec-h h2{margin:0 1em .4em 0}
.dp-season{background:var(--surface2);color:var(--text);border:1px solid var(--line);border-radius:6px;padding:.55em 1em;font-weight:700;font-size:.95rem;margin-bottom:.4em;max-width:100%}
.dp-season-one{color:var(--muted);font-weight:650}
.dp-ep{display:flex;align-items:center;padding:1.1rem 1rem;border-bottom:1px solid var(--line);border-radius:8px;transition:background .15s}
.dp-ep:hover,.dp-ep:focus{background:var(--surface2)}
.dp-ep-n{width:2.4em;font-size:1.45rem;color:var(--muted);text-align:center;flex:none;margin-right:.4em}
.dp-ep-thumb{position:relative;width:170px;flex:none;border-radius:6px;overflow:hidden;margin-right:1.1rem;background:var(--surface3)}
.dp-ep-thumb>div{padding-top:56.25%}
.dp-ep-thumb img{position:absolute;top:0;left:0;width:100%;height:100%;object-fit:cover;opacity:0;transition:opacity .35s}
.dp-ep-thumb img.ok{opacity:1}
.dp-ep-thumb .prog{position:absolute;left:0;right:0;bottom:0;height:3px;background:rgba(255,255,255,.25)}
.dp-ep-thumb .prog i{display:block;height:100%;background:var(--accent)}
.dp-ep-play{position:absolute;top:50%;left:50%;width:40px;height:40px;margin:-20px 0 0 -20px;border-radius:50%;border:2px solid #fff;background:rgba(0,0,0,.5);color:#fff;display:flex;align-items:center;justify-content:center;opacity:0;transition:opacity .15s}
.dp-ep:hover .dp-ep-play,.dp-ep:focus .dp-ep-play{opacity:1}
.dp-ep-b{flex:1;min-width:0}
.dp-ep-t{display:flex;justify-content:space-between;align-items:baseline}
.dp-ep-t b{font-weight:650;min-width:0;overflow:hidden;text-overflow:ellipsis}
.dp-ep-t span{color:var(--muted);margin-left:1em;white-space:nowrap;font-size:.88rem}
.dp-ep-o{color:var(--muted);font-size:.87rem;line-height:1.45;margin-top:.35em;display:-webkit-box;-webkit-line-clamp:3;-webkit-box-orient:vertical;overflow:hidden}
.dp-grid{display:flex;flex-wrap:wrap}
.dp-grid>.card{width:31%;margin:0 2% 1.2rem 0}
@supports (display:grid){.dp-grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));grid-gap:1.1rem}.dp-grid>.card{width:auto;margin:0}}
.dp-about p{font-size:.88rem;margin:.35em 0}
.dp-tech{color:var(--dim);font-size:.8rem;word-break:break-all}
@media (max-width:768px),(max-height:500px) and (pointer:coarse){
  .dp-modal{width:100%;margin:0;border-radius:0;min-height:100vh}
  .dp-modal .modal-x{position:fixed;top:10px;right:10px}
  .dp-hero{padding-top:56.25%}
  .dp-head{margin-top:-1.6rem;padding:0 1rem}
  .dp-title{max-width:100%}
  .dp-actions{display:block}
  .dp-actions .btn{display:flex;width:100%;margin:0 0 .55rem}
  .dp-rounds{justify-content:space-around;margin-top:.4rem}
  .dp-round,html[data-theme="light"] .dp-round{border:0;background:none;color:var(--text);flex-direction:column;width:auto;height:auto;padding:.4em .6em;border-radius:8px;margin:0}
  .dp-round.on{color:var(--ok)}
  .dp-round-l{position:static;width:auto;height:auto;clip:auto;font-size:.72rem;color:var(--muted);margin-top:.3em}
  .dp-body{padding:.6rem 1rem 2rem}
  .dp-main{margin-right:0}
  .dp-side{flex-basis:100%}
  .dp-ep{padding:.9rem .2rem;flex-wrap:wrap}
  .dp-ep-n{display:none}
  .dp-ep-thumb{width:40%;margin-right:.8rem}
  .dp-ep-t{display:block}
  .dp-ep-t span{display:block;margin:.15em 0 0}
  .dp-grid>.card{width:48%}
  @supports (display:grid){.dp-grid{grid-template-columns:repeat(2,minmax(0,1fr));grid-gap:.7rem}}
}
@media (max-height:500px) and (pointer:coarse){.dp-hero{padding-top:38%}.dp-actions{display:flex}.dp-actions .btn{width:auto;margin:0 .6rem .55rem 0}}
html[data-tv] .dp-round{width:56px;height:56px}
html[data-tv] .dp-round:focus{background:#fff;color:#000;outline:none}


/* ---------- responsive pass ---------- */
@media (max-width:1200px){:root{--fs-base:15px}}
@media (max-width:768px),(pointer:coarse) and (max-height:540px){:root{--fs-base:14px}}
html[data-tv]{--fs-base:22px}
.p-menu-more{display:none}
.p-top{padding-left:max(3vw,env(safe-area-inset-left));padding-right:max(3vw,env(safe-area-inset-right))}
.p-bot{padding-left:max(3vw,env(safe-area-inset-left));padding-right:max(3vw,env(safe-area-inset-right))}
.p-row{flex-wrap:nowrap;min-width:0}
.p-time{flex:none}
/* compact player: phones (portrait + landscape) and small tablets */
@media (max-width:1000px),(pointer:coarse) and (max-height:540px){
  .p-opt{display:none!important}
  .p-menu-more{display:block}
  .p-btn{width:42px;height:42px}
  .p-btn .ico{width:24px;height:24px}
  .p-row>*{margin-right:.15em}
  .p-time{font-size:.85rem;margin:0 .5em}
  .p-top{padding-top:max(10px,env(safe-area-inset-top));padding-bottom:36px}
  .p-top .ttl b{font-size:1rem}
  .p-top .ttl small{font-size:.8rem}
  .p-bot{padding-top:36px;padding-bottom:max(8px,env(safe-area-inset-bottom))}
  .seek{height:24px;margin-bottom:2px}
  .skipbtn,.nextcard{bottom:92px}
  .p-menu{bottom:84px;max-height:calc(100% - 120px)}
  .subs .cue{font-size:var(--sub-size-m,4.4vw)}
  .stats{top:64px;font-size:11px;min-width:0}
}
@media (pointer:coarse) and (max-height:540px){.subs .cue{font-size:calc(var(--sub-size-m,4.4vw) * .6)}}
@media (max-width:1000px),(pointer:coarse) and (max-height:540px){.nextcard{width:min(320px,70vw)}.nextcard .b{padding:.7em .9em}}
@media (max-height:540px){.nextcard .ratio{display:none}.nextcard{bottom:84px}}
@media (max-width:480px){
  .p-seekb{display:none!important}
  .p-btn{width:38px;height:38px}
  .p-btn .ico{width:22px;height:22px}
  .p-time{font-size:.8rem;margin:0 .3em}
}
/* settings: folder rows stack on phones instead of squeezing the path to one letter per line */
.lib .grow b{overflow-wrap:anywhere;word-break:break-word}
@media (max-width:768px){
  .lib{flex-wrap:wrap;align-items:flex-start}
  .lib>.ico{margin-top:.2em}
  .lib .grow{flex:1 1 calc(100% - 2.2em);margin:0 0 .6em .7em}
  .lib select.input{width:auto;margin-left:2em}
  .lib .toggle{margin-left:auto;margin-top:.35em}
  .field .lbl b{font-size:.95rem}
  .set-main h2{font-size:1.3rem}
  .page h1{font-size:1.5rem}
  .card-box{padding:.9em 1em}
  .btn{font-size:.92rem}
  .nav .logo{font-size:1.3rem}
  .row-h h2{font-size:1.05rem}
  .hero-title{font-size:1.8rem}
  .who h1{font-size:1.6rem}
  .modal.sm{padding:1.2em}
  .toolbar select,.chip{font-size:.85rem}
}


/* ---------- overflow & small-screen fixes (v1.2.1) ---------- */
.set,.set-main,.page,.card-box,.field,.field .lbl,.lib,.lib .grow,.dp,.dp-body,.dp-head{min-width:0;max-width:100%}
.lib .grow,.lib .grow *,.field .lbl small,.dp-tech,.empty code,.banner code{overflow-wrap:anywhere;word-break:break-word}
@media (max-width:768px){
  .set-main{padding-left:var(--pad);padding-right:var(--pad);width:100%}
  .lib{display:flex;flex-wrap:wrap;align-items:center}
  .lib .grow{flex:1 1 calc(100% - 2.4em)}
  .lib select.input{flex:0 1 auto;max-width:50%;margin-left:2.1em}
  .lib .toggle{margin:0 .4em 0 auto}
  .lib .btn-icon{margin-left:.2em!important}
}
/* round action buttons on phones always show their label in full (TV sizing must not win here) */
@media (max-width:768px),(max-height:500px) and (pointer:coarse){
  html[data-tv] .dp-round,.dp-round{width:auto;height:auto;min-width:4.5em}
  .dp-round-l{overflow:visible;white-space:nowrap;line-height:1.3}
  .dp-rounds{width:100%}
}

/* TV (10-foot) */
html[data-tv]{--fs-base:22px;--pad:5vw;--card-w:17vw;--nav-h:84px}
@supports (width:clamp(1px,1vw,2px)){html[data-tv]{--card-w:clamp(220px,17vw,340px)}}
html[data-tv] .scroller{padding-top:32px;padding-bottom:40px}
html[data-tv] .nav{padding:0 5vw}
html[data-tv] .nav-links a{font-size:1.05rem;padding:.5em .7em;border-radius:8px;margin-right:.5vw}
html[data-tv] .nav-links a:focus{background:var(--text);color:var(--bg);outline:none}
html[data-tv] .row-arrow{display:none}
html[data-tv] .btn{min-height:56px}
html[data-tv] .p-btn{width:64px;height:64px}
html[data-tv] .p-btn .ico{width:36px;height:36px}
html[data-tv] .p-btn:focus{background:#fff;color:#000;outline:none}
html[data-tv] .seek:focus{outline:none}
html[data-tv] .seek:focus .trk{height:10px;box-shadow:0 0 0 3px var(--accent)}
html[data-tv] .scroller{scroll-behavior:auto}
html[data-tv] .hero{height:72vh}
html[data-tv] .field .ctl :focus{outline:3px solid var(--accent)}
html[data-lite] *{backdrop-filter:none!important;-webkit-backdrop-filter:none!important}
html[data-lite] .card:hover{box-shadow:none}
html[data-motion="reduce"] *,html[data-motion="reduce"] *:before,html[data-motion="reduce"] *:after{animation-duration:.001s!important;transition-duration:.001s!important;scroll-behavior:auto!important}
@media (prefers-reduced-motion:reduce){*,*:before,*:after{animation-duration:.001s!important;transition-duration:.001s!important;scroll-behavior:auto!important}}

/* responsive */
@media (max-width:1200px){.grid>.card{width:calc((100% - 5 * .9vw) / 5)}}
@media (max-width:900px){.nav-links{display:none}.set{flex-direction:column}.set-nav{width:auto;border-right:0;border-bottom:1px solid var(--line);padding:1em var(--pad);display:flex;overflow-x:auto}
  .set-nav h1{display:none}.set-nav a{white-space:nowrap;margin-right:4px}.set-main{padding:1.4em var(--pad) 6em}}
@media (max-width:768px){
  :root{--pad:16px;--card-w:42vw;--nav-h:58px}
  html[data-cards="s"]{--card-w:34vw}html[data-cards="l"]{--card-w:62vw}
  .nav .logo{font-size:1.35rem}
  .nav-tvbtn{display:none}
  .tabbar{display:flex;position:fixed;left:0;right:0;bottom:0;z-index:45;background:rgba(10,10,15,.96);border-top:1px solid var(--line);padding-bottom:env(safe-area-inset-bottom)}
  html[data-theme="light"] .tabbar{background:rgba(255,255,255,.96)}
  .tabbar a{flex:1;display:flex;flex-direction:column;align-items:center;padding:8px 0 7px;font-size:.68rem;color:var(--muted)}
  .tabbar a.on{color:var(--text)}
  .tabbar .ico{width:24px;height:24px;margin-bottom:2px}
  body{padding-bottom:64px}
  .hero{height:72vh;min-height:380px;margin-bottom:-40px}
  .hero-body{width:auto;right:var(--pad);bottom:70px;text-align:center}
  .hero-ov{display:none}
  .hero-dots{display:none}
  .scroller>*{margin-right:8px}
  .grid>.card{width:calc((100% - 2 * 8px) / 2)}
  .row-arrow{display:none}
  .d-head{left:1em;right:1em}.d-body{padding:0 1em 1.4em}.d-main{margin-right:0}
  .field{flex-wrap:wrap}.field .ctl{max-width:100%;width:100%;text-align:left;margin-top:.6em}.swatches,.seg{justify-content:flex-start}
  .input{min-width:0;width:100%}
  .p-btn{width:42px;height:42px}.p-btn .ico{width:24px;height:24px}
  .p-hide-m{display:none!important}
  .subs .cue{font-size:var(--sub-size-m,4.4vw)}
  .ep-n{display:none}
  .mini{display:none}
}
@media (max-width:480px){.btn{padding:0 1em}.who-p{width:28vw}.hero-title{font-size:1.9rem}}
@media (min-width:1600px){:root{--card-w:15vw}}
@supports (width:clamp(1px,1vw,2px)){@media (min-width:1600px){:root{--card-w:clamp(240px,15vw,340px)}}}
/* grid layout sizes cards itself: neutralise the flex fallback widths above */
@supports (display:grid){.grid>.card,.grid>.card.poster{width:auto}}
@media (hover:none){.row-arrow{display:none}.card:hover{transform:none}}
@media print{.nav,.tabbar,#toasts{display:none}}
"""


APP_JS = r"""/* LocalFlix web app — vanilla JS, ES2019 core (no ?. ?? or private fields) so older TV browsers parse it. */
(function () {
'use strict';
var BOOT = window.__LF__ || {};
var LF = window.LF = {};

/* ===== utils ===== */
function $(s, r) { return (r || document).querySelector(s); }
function $$(s, r) { return Array.prototype.slice.call((r || document).querySelectorAll(s)); }
function isArr(x) { return Object.prototype.toString.call(x) === '[object Array]'; }
function h(tag, attrs) {
  var m = tag.split(/(?=[.#])/), el = document.createElement(m[0] || 'div'), i;
  for (i = 1; i < m.length; i++) { if (m[i][0] === '.') el.classList.add(m[i].slice(1)); else el.id = m[i].slice(1); }
  var kids = Array.prototype.slice.call(arguments, 2);
  if (attrs && (typeof attrs !== 'object' || attrs.nodeType || isArr(attrs))) { kids.unshift(attrs); attrs = null; }
  if (attrs) Object.keys(attrs).forEach(function (k) {
    var v = attrs[k];
    if (v === null || v === undefined || v === false) return;
    if (k === 'class') String(v).split(' ').forEach(function (c) { if (c) el.classList.add(c); });
    else if (k === 'style' && typeof v === 'object') Object.keys(v).forEach(function (s) { if (s.indexOf('--') === 0) el.style.setProperty(s, v[s]); else el.style[s] = v[s]; });
    else if (k === 'text') el.textContent = v;
    else if (k === 'html') el.innerHTML = v;
    else if (k === 'ref') v(el);
    else if (k.indexOf('on') === 0 && typeof v === 'function') el.addEventListener(k.slice(2), v);
    else if (v === true) el.setAttribute(k, '');
    else el.setAttribute(k, v);
  });
  append(el, kids);
  return el;
}
function append(el, kids) {
  kids.forEach(function (c) {
    if (c === null || c === undefined || c === false) return;
    if (isArr(c)) append(el, c); else el.appendChild(c.nodeType ? c : document.createTextNode(String(c)));
  });
  return el;
}
function icon(name, cls) {
  var ns = 'http://www.w3.org/2000/svg', s = document.createElementNS(ns, 'svg'), u = document.createElementNS(ns, 'use');
  s.setAttribute('class', 'ico' + (cls ? ' ' + cls : '')); s.setAttribute('aria-hidden', 'true');
  u.setAttributeNS('http://www.w3.org/1999/xlink', 'href', '#i-' + name); u.setAttribute('href', '#i-' + name);
  s.appendChild(u); return s;
}
function clear(el) { while (el && el.firstChild) el.removeChild(el.firstChild); return el; }
function debounce(fn, ms) { var t; return function () { var a = arguments, s = this; clearTimeout(t); t = setTimeout(function () { fn.apply(s, a); }, ms); }; }
function clamp(v, a, b) { return Math.max(a, Math.min(b, v)); }
function pad2(n) { return (n < 10 ? '0' : '') + n; }
function fmtTime(s) { s = Math.max(0, Math.floor(s || 0)); var hh = Math.floor(s / 3600), mm = Math.floor(s % 3600 / 60), ss = s % 60; return (hh ? hh + ':' + pad2(mm) : mm) + ':' + pad2(ss); }
function fmtDur(s) { s = (s || 0) > 0 ? Math.max(1, Math.round(s / 60)) : 0; if (s < 60) return s + t('u.min'); return Math.floor(s / 60) + t('u.h') + ' ' + (s % 60) + t('u.min'); }
function fmtSize(b) { if (!b) return ''; var u = ['B', 'KB', 'MB', 'GB', 'TB'], i = 0; while (b >= 1024 && i < 4) { b /= 1024; i++; } return b.toFixed(i > 2 ? 1 : 0) + ' ' + u[i]; }
function hashColor(s) { var x = 0, i; s = s || ''; for (i = 0; i < s.length; i++) x = (x * 31 + s.charCodeAt(i)) | 0; var hu = Math.abs(x) % 360; return 'linear-gradient(135deg,hsl(' + hu + ',45%,28%),hsl(' + ((hu + 50) % 360) + ',55%,14%))'; }
function store(k, v) { try { if (v === undefined) { var r = localStorage.getItem('lf.' + k); return r ? JSON.parse(r) : null; } localStorage.setItem('lf.' + k, JSON.stringify(v)); } catch (e) { return null; } }
function qs(o) { return Object.keys(o).filter(function (k) { return o[k] !== undefined && o[k] !== null && o[k] !== ''; }).map(function (k) { return encodeURIComponent(k) + '=' + encodeURIComponent(o[k]); }).join('&'); }
function parseQS(s) { var o = {}; (s || '').split('&').forEach(function (p) { if (!p) return; var i = p.indexOf('='); o[decodeURIComponent(i < 0 ? p : p.slice(0, i))] = i < 0 ? '' : decodeURIComponent(p.slice(i + 1)); }); return o; }
function copyText(txt) {
  if (navigator.clipboard && window.isSecureContext) return navigator.clipboard.writeText(txt).then(function () { return true; });
  var ta = h('textarea', { style: { position: 'fixed', top: '-1000px' } }); ta.value = txt; document.body.appendChild(ta); ta.select();
  var ok = false; try { ok = document.execCommand('copy'); } catch (e) { ok = false; } document.body.removeChild(ta); return Promise.resolve(ok);
}
LF.h = h; LF.icon = icon;

/* ===== i18n ===== */
var I18N = {
 en: {
  'nav.home': 'Home', 'nav.shows': 'TV Shows', 'nav.movies': 'Movies', 'nav.new': 'New & Recent', 'nav.mylist': 'My List', 'nav.search': 'Search',
  'nav.tv': 'Open on TV', 'nav.settings': 'Settings', 'nav.switch': 'Switch profile', 'nav.signout': 'Sign out', 'nav.manage': 'Manage profiles',
  'row.continue': 'Continue Watching', 'row.recent': 'Recently Added', 'row.mylist': 'My List', 'row.shows': 'TV Shows', 'row.movies': 'Movies',
  'row.because': 'Because you watched {0}', 'row.multiaudio': 'Multi-audio', 'row.uhd': '4K & HDR',
  'b.play': 'Play', 'b.resume': 'Resume', 'b.more': 'More Info', 'b.startover': 'Start over', 'b.mylist': 'My List', 'b.inlist': 'In My List',
  'b.watched': 'Watched', 'b.markwatched': 'Mark watched', 'b.markunwatched': 'Mark unwatched', 'b.close': 'Close', 'b.save': 'Save', 'b.cancel': 'Cancel',
  'b.delete': 'Delete', 'b.add': 'Add', 'b.edit': 'Edit', 'b.done': 'Done', 'b.retry': 'Retry', 'b.copy': 'Copy', 'b.copied': 'Copied!', 'b.loadmore': 'Load more',
  'b.rescan': 'Rescan library', 'b.fullscan': 'Full rescan', 'b.seeall': 'See all', 'b.series': 'Go to series', 'b.playnext': 'Play next', 'b.login': 'Sign in',
  'b.unlock': 'Unlock admin settings', 'b.browse': 'Browse…', 'b.ok': 'OK', 'b.back': 'Back',
  'who': "Who's watching?", 'who.add': 'Add profile', 'who.manage': 'Manage profiles', 'who.kids': 'Kids', 'who.pin': 'Enter PIN', 'who.wrongpin': 'Wrong PIN',
  'who.name': 'Name', 'who.avatar': 'Avatar', 'who.pinopt': '4-digit PIN (optional)', 'who.clearh': 'Clear history', 'who.clearl': 'Clear My List',
  'meta.seasons': '{0} seasons', 'meta.season': 'Season {0}', 'meta.episodes': '{0} episodes', 'meta.left': '{0} left', 'meta.new': 'NEW',
  'meta.audio': '{0} audio', 'meta.ep': 'S{0}:E{1}', 'meta.file': 'File', 'meta.cast': 'Cast', 'meta.genres': 'Genres', 'meta.video': 'Video',
  'meta.audiotracks': 'Audio tracks', 'meta.subtitles': 'Subtitles', 'meta.chapters': 'Chapters', 'meta.none': 'None', 'meta.unplayable': 'This file could not be read',
  'u.min': 'm', 'u.h': 'h',
  'sort.added': 'Recently added', 'sort.title': 'Title', 'sort.year': 'Year', 'sort.duration': 'Duration', 'sort.rating': 'Rating',
  'f.unwatched': 'Unwatched', 'f.watched': 'Watched', 'f.multiaudio': 'Multi-audio', 'f.4k': '4K', 'f.1080': '1080p', 'f.720': '720p', 'f.hdr': 'HDR', 'f.allgenres': 'All genres', 'f.allfolders': 'All folders',
  'search.ph': 'Titles, shows, genres', 'search.recent': 'Recent searches', 'search.none': 'No results for "{0}"', 'search.hint': 'Search your library',
  'empty.title': 'Your library is empty', 'empty.body': 'Copy videos into this folder, then press Rescan:', 'empty.list': 'Your list is empty',
  'empty.listbody': 'Add movies and shows with the + button.', 'empty.nothing': 'Nothing here yet',
  'ff.missing': 'ffmpeg is not installed — only some files can play. Install it for full compatibility:', 'ff.dismiss': 'Dismiss',
  'tv.title': 'Watch on your TV', 'tv.s1': 'Open the web browser on your TV (Samsung Internet, LG Web Browser, Silk, Chrome…)', 'tv.s2': 'Type this address and press OK:',
  'tv.s3': 'Use the arrow keys, OK and Back on your remote.', 'tv.scan': 'Or scan with your phone', 'tv.all': 'All addresses', 'tv.otl': 'Create one-time login QR',
  'login.title': 'Sign in to {0}', 'login.pw': 'Password', 'login.bad': 'Wrong password',
  'admin.pin': 'Admin PIN or server password', 'admin.needed': 'Administrator access required',
  'set.title': 'Settings', 'set.general': 'General', 'set.libraries': 'Libraries', 'set.playback': 'Playback', 'set.subtitles': 'Subtitles', 'set.transcoding': 'Transcoding',
  'set.network': 'Network & Security', 'set.metadata': 'Metadata', 'set.profiles': 'Profiles', 'set.advanced': 'Advanced', 'set.about': 'About',
  'set.restart': 'Restart required', 'set.saved': 'Saved', 'set.admin': 'Admin', 'set.addfolder': 'Add folder', 'set.folderpath': 'Folder path',
  'set.scanstatus': 'Scan status', 'set.items': '{0} items', 'set.lastscan': 'Last scan', 'set.never': 'never', 'set.scanning': 'Scanning… {0} files',
  'set.password': 'Server password', 'set.setpw': 'Set password', 'set.clearpw': 'Remove password', 'set.adminpin': 'Admin PIN', 'set.setpin': 'Set admin PIN',
  'set.urls': 'Addresses', 'set.hw': 'Hardware encoders', 'set.redetect': 'Re-detect', 'set.clearcache': 'Clear cache', 'set.doctor': 'Diagnostics', 'set.rundoctor': 'Run diagnostics',
  'set.logs': 'Server log', 'set.restartnow': 'Restart server', 'set.export': 'Export settings', 'set.import': 'Import settings', 'set.reset': 'Reset to defaults',
  'set.preview': 'The quick brown fox jumps over the lazy dog', 'set.open': 'Anyone on your Wi-Fi can use LocalFlix. Set a password to restrict access.',
  'p.more': 'More', 'dp.more': 'More like this', 'dp.about': 'About {0}', 'dp.series': 'Series', 'dp.upnext': 'Up next', 'dp.start': 'Start watching',
  'dp.specials': 'Specials',
  'scope.title': 'Which videos to show', 'scope.folders': 'Only my folders', 'scope.folders.d': 'Just the folders you add below. Nothing else on this device is touched.',
  'scope.common': 'Common video folders', 'scope.common.d': 'Videos, Movies and Downloads (phone storage on Android) plus USB drives and SD cards as they are plugged in.',
  'scope.all': 'Entire device', 'scope.all.d': 'Every video on this computer: home folder and all drives. System and app folders are skipped. The first scan can take a while.',
  'scope.warn': 'Everyone who can open LocalFlix on your network will see these videos. Consider setting a password.', 'scope.found': 'Detected folders',
  'scope.none': 'No folders detected on this device', 'lib.auto': 'Auto', 'lib.custom': 'Custom', 'lib.offline': 'Not connected', 'lib.inside': 'inside {0}',
  'lib.hide': 'Hide', 'empty.common': 'Use my video folders', 'empty.all': 'Find every video on this device',
  'set.refreshmeta': 'Refresh metadata', 'set.licenses': 'Licences', 'set.unsaved': 'Not admin — changes are disabled',
  'p.audio': 'Audio', 'p.subs': 'Subtitles', 'p.off': 'Off', 'p.speed': 'Speed', 'p.quality': 'Quality', 'p.episodes': 'Episodes', 'p.chapters': 'Chapters',
  'p.normal': 'Normal', 'p.auto': 'Auto', 'p.original': 'Original', 'p.skipintro': 'Skip Intro', 'p.skipcredits': 'Skip Credits', 'p.next': 'Next episode',
  'p.nextin': 'Next episode in {0}', 'p.watchcredits': 'Watch credits', 'p.resume': 'Resume from {0}?', 'p.fromstart': 'Start from beginning', 'p.preparing': 'Preparing stream…',
  'p.preparingpct': 'Preparing stream… {0}%', 'p.buffering': 'Buffering…', 'p.compat': 'Switching to compatible mode…', 'p.error': 'Playback error', 'p.trytranscode': 'Try transcoding',
  'p.endseries': "You've finished the series", 'p.sleep': 'Sleep timer', 'p.sleepoff': 'Off', 'p.sleepend': 'End of episode', 'p.still': 'Are you still watching?',
  'p.continue': 'Continue watching', 'p.delay': 'Subtitle delay', 'p.size': 'Size', 'p.fit': 'Video fit', 'p.fitf': 'Fit', 'p.fillf': 'Fill', 'p.stretch': 'Stretch', 'p.crop': '21:9 crop',
  'p.stats': 'Stats', 'p.method.direct': 'Direct Play', 'p.method.remux': 'Direct Stream', 'p.method.transcode': 'Transcoding', 'p.shortcuts': 'Keyboard shortcuts',
  'p.burn': 'Image subtitles need transcoding — restarting…', 'p.nohls': 'This browser cannot play HLS streams and hls.js is not available.', 'p.min': '{0} min',
  'toast.offline': 'Server unreachable — reconnecting…', 'toast.online': 'Reconnected', 'toast.exit': 'Press Back again to exit', 'toast.added': 'Added to My List',
  'toast.removed': 'Removed from My List', 'toast.scan': 'Library scan started', 'toast.err': 'Something went wrong', 'toast.newitems': 'Library updated',
  'err.404': 'Page not found', 'err.home': 'Go home'
 },
 hi: {
  'nav.home': 'होम', 'nav.shows': 'टीवी शो', 'nav.movies': 'फ़िल्में', 'nav.new': 'नया और हालिया', 'nav.mylist': 'मेरी सूची', 'nav.search': 'खोजें',
  'nav.tv': 'टीवी पर खोलें', 'nav.settings': 'सेटिंग्स', 'nav.switch': 'प्रोफ़ाइल बदलें', 'nav.signout': 'साइन आउट', 'nav.manage': 'प्रोफ़ाइल प्रबंधित करें',
  'row.continue': 'देखना जारी रखें', 'row.recent': 'हाल ही में जोड़े गए', 'row.mylist': 'मेरी सूची', 'row.shows': 'टीवी शो', 'row.movies': 'फ़िल्में',
  'row.because': 'क्योंकि आपने {0} देखा', 'row.multiaudio': 'बहु-भाषा ऑडियो', 'row.uhd': '4K और HDR',
  'b.play': 'चलाएँ', 'b.resume': 'जारी रखें', 'b.more': 'अधिक जानकारी', 'b.startover': 'शुरू से', 'b.mylist': 'मेरी सूची', 'b.inlist': 'सूची में है',
  'b.watched': 'देखा गया', 'b.markwatched': 'देखा हुआ चिह्नित करें', 'b.markunwatched': 'अनदेखा चिह्नित करें', 'b.close': 'बंद करें', 'b.save': 'सहेजें', 'b.cancel': 'रद्द करें',
  'b.delete': 'हटाएँ', 'b.add': 'जोड़ें', 'b.edit': 'संपादित करें', 'b.done': 'हो गया', 'b.retry': 'फिर कोशिश करें', 'b.copy': 'कॉपी करें', 'b.copied': 'कॉपी हो गया!', 'b.loadmore': 'और दिखाएँ',
  'b.rescan': 'लाइब्रेरी फिर से स्कैन करें', 'b.fullscan': 'पूरा स्कैन', 'b.seeall': 'सभी देखें', 'b.series': 'सीरीज़ पर जाएँ', 'b.playnext': 'अगला चलाएँ', 'b.login': 'साइन इन',
  'b.unlock': 'एडमिन सेटिंग्स खोलें', 'b.browse': 'ब्राउज़ करें…', 'b.ok': 'ठीक है', 'b.back': 'वापस',
  'who': 'कौन देख रहा है?', 'who.add': 'प्रोफ़ाइल जोड़ें', 'who.manage': 'प्रोफ़ाइल प्रबंधित करें', 'who.kids': 'बच्चे', 'who.pin': 'पिन डालें', 'who.wrongpin': 'गलत पिन',
  'who.name': 'नाम', 'who.avatar': 'अवतार', 'who.pinopt': '4 अंकों का पिन (वैकल्पिक)', 'who.clearh': 'इतिहास साफ़ करें', 'who.clearl': 'मेरी सूची साफ़ करें',
  'meta.seasons': '{0} सीज़न', 'meta.season': 'सीज़न {0}', 'meta.episodes': '{0} एपिसोड', 'meta.left': '{0} बाकी', 'meta.new': 'नया',
  'meta.audio': '{0} ऑडियो', 'meta.ep': 'S{0}:E{1}', 'meta.file': 'फ़ाइल', 'meta.cast': 'कलाकार', 'meta.genres': 'शैलियाँ', 'meta.video': 'वीडियो',
  'meta.audiotracks': 'ऑडियो ट्रैक', 'meta.subtitles': 'सबटाइटल', 'meta.chapters': 'अध्याय', 'meta.none': 'कोई नहीं', 'meta.unplayable': 'यह फ़ाइल पढ़ी नहीं जा सकी',
  'u.min': 'मि', 'u.h': 'घं',
  'sort.added': 'हाल ही में जोड़े गए', 'sort.title': 'शीर्षक', 'sort.year': 'वर्ष', 'sort.duration': 'अवधि', 'sort.rating': 'रेटिंग',
  'f.unwatched': 'अनदेखे', 'f.watched': 'देखे गए', 'f.multiaudio': 'बहु-ऑडियो', 'f.4k': '4K', 'f.1080': '1080p', 'f.720': '720p', 'f.hdr': 'HDR', 'f.allgenres': 'सभी शैलियाँ', 'f.allfolders': 'सभी फ़ोल्डर',
  'search.ph': 'फ़िल्में, शो, शैलियाँ', 'search.recent': 'हाल की खोजें', 'search.none': '"{0}" के लिए कोई परिणाम नहीं', 'search.hint': 'अपनी लाइब्रेरी में खोजें',
  'empty.title': 'आपकी लाइब्रेरी खाली है', 'empty.body': 'इस फ़ोल्डर में वीडियो कॉपी करें, फिर फिर से स्कैन दबाएँ:', 'empty.list': 'आपकी सूची खाली है',
  'empty.listbody': '+ बटन से फ़िल्में और शो जोड़ें।', 'empty.nothing': 'अभी यहाँ कुछ नहीं है',
  'ff.missing': 'ffmpeg इंस्टॉल नहीं है — केवल कुछ फ़ाइलें चलेंगी। पूरी संगतता के लिए इसे इंस्टॉल करें:', 'ff.dismiss': 'छिपाएँ',
  'tv.title': 'अपने टीवी पर देखें', 'tv.s1': 'अपने टीवी का वेब ब्राउज़र खोलें', 'tv.s2': 'यह पता टाइप करें और OK दबाएँ:',
  'tv.s3': 'रिमोट के तीर, OK और Back बटन का उपयोग करें।', 'tv.scan': 'या अपने फ़ोन से स्कैन करें', 'tv.all': 'सभी पते', 'tv.otl': 'एक-बार लॉगिन QR बनाएँ',
  'login.title': '{0} में साइन इन करें', 'login.pw': 'पासवर्ड', 'login.bad': 'गलत पासवर्ड',
  'admin.pin': 'एडमिन पिन या सर्वर पासवर्ड', 'admin.needed': 'एडमिन अनुमति आवश्यक है',
  'set.title': 'सेटिंग्स', 'set.general': 'सामान्य', 'set.libraries': 'लाइब्रेरी', 'set.playback': 'प्लेबैक', 'set.subtitles': 'सबटाइटल', 'set.transcoding': 'ट्रांसकोडिंग',
  'set.network': 'नेटवर्क और सुरक्षा', 'set.metadata': 'मेटाडेटा', 'set.profiles': 'प्रोफ़ाइल', 'set.advanced': 'उन्नत', 'set.about': 'जानकारी',
  'set.restart': 'रीस्टार्ट आवश्यक', 'set.saved': 'सहेजा गया', 'set.admin': 'एडमिन', 'set.addfolder': 'फ़ोल्डर जोड़ें', 'set.folderpath': 'फ़ोल्डर पथ',
  'set.scanstatus': 'स्कैन स्थिति', 'set.items': '{0} आइटम', 'set.lastscan': 'पिछला स्कैन', 'set.never': 'कभी नहीं', 'set.scanning': 'स्कैन हो रहा है… {0} फ़ाइलें',
  'set.password': 'सर्वर पासवर्ड', 'set.setpw': 'पासवर्ड सेट करें', 'set.clearpw': 'पासवर्ड हटाएँ', 'set.adminpin': 'एडमिन पिन', 'set.setpin': 'एडमिन पिन सेट करें',
  'set.urls': 'पते', 'set.hw': 'हार्डवेयर एन्कोडर', 'set.redetect': 'फिर से जाँचें', 'set.clearcache': 'कैश साफ़ करें', 'set.doctor': 'निदान', 'set.rundoctor': 'निदान चलाएँ',
  'set.logs': 'सर्वर लॉग', 'set.restartnow': 'सर्वर रीस्टार्ट करें', 'set.export': 'सेटिंग्स निर्यात करें', 'set.import': 'सेटिंग्स आयात करें', 'set.reset': 'डिफ़ॉल्ट पर रीसेट करें',
  'set.preview': 'यह सबटाइटल का पूर्वावलोकन है', 'set.open': 'आपके वाई-फ़ाई पर कोई भी LocalFlix का उपयोग कर सकता है। पहुँच सीमित करने के लिए पासवर्ड सेट करें।',
  'p.more': 'और', 'dp.more': 'ऐसे और टाइटल', 'dp.about': '{0} के बारे में', 'dp.series': 'सीरीज़', 'dp.upnext': 'आगे', 'dp.start': 'देखना शुरू करें',
  'dp.specials': 'स्पेशल',
  'scope.title': 'कौन-से वीडियो दिखाएँ', 'scope.folders': 'सिर्फ़ मेरे फ़ोल्डर', 'scope.folders.d': 'केवल नीचे जोड़े गए फ़ोल्डर। डिवाइस पर और कुछ नहीं छुआ जाता।',
  'scope.common': 'सामान्य वीडियो फ़ोल्डर', 'scope.common.d': 'Videos, Movies और Downloads (Android पर फ़ोन स्टोरेज) और लगाए गए USB ड्राइव व SD कार्ड।',
  'scope.all': 'पूरा डिवाइस', 'scope.all.d': 'इस कंप्यूटर के सभी वीडियो: होम फ़ोल्डर और सभी ड्राइव। सिस्टम और ऐप फ़ोल्डर छोड़ दिए जाते हैं। पहला स्कैन समय ले सकता है।',
  'scope.warn': 'आपके नेटवर्क पर LocalFlix खोलने वाला हर व्यक्ति ये वीडियो देख सकेगा। पासवर्ड सेट करने पर विचार करें।', 'scope.found': 'मिले हुए फ़ोल्डर',
  'scope.none': 'इस डिवाइस पर कोई फ़ोल्डर नहीं मिला', 'lib.auto': 'ऑटो', 'lib.custom': 'कस्टम', 'lib.offline': 'जुड़ा नहीं है', 'lib.inside': '{0} के अंदर',
  'lib.hide': 'छिपाएँ', 'empty.common': 'मेरे वीडियो फ़ोल्डर इस्तेमाल करें', 'empty.all': 'इस डिवाइस के सभी वीडियो खोजें',
  'set.refreshmeta': 'मेटाडेटा ताज़ा करें', 'set.licenses': 'लाइसेंस', 'set.unsaved': 'एडमिन नहीं — बदलाव अक्षम हैं',
  'p.audio': 'ऑडियो', 'p.subs': 'सबटाइटल', 'p.off': 'बंद', 'p.speed': 'गति', 'p.quality': 'गुणवत्ता', 'p.episodes': 'एपिसोड', 'p.chapters': 'अध्याय',
  'p.normal': 'सामान्य', 'p.auto': 'स्वचालित', 'p.original': 'मूल', 'p.skipintro': 'इंट्रो छोड़ें', 'p.skipcredits': 'क्रेडिट छोड़ें', 'p.next': 'अगला एपिसोड',
  'p.nextin': 'अगला एपिसोड {0} में', 'p.watchcredits': 'क्रेडिट देखें', 'p.resume': '{0} से जारी रखें?', 'p.fromstart': 'शुरू से चलाएँ', 'p.preparing': 'स्ट्रीम तैयार हो रही है…',
  'p.preparingpct': 'स्ट्रीम तैयार हो रही है… {0}%', 'p.buffering': 'बफ़र हो रहा है…', 'p.compat': 'संगत मोड पर स्विच हो रहा है…', 'p.error': 'प्लेबैक त्रुटि', 'p.trytranscode': 'ट्रांसकोडिंग आज़माएँ',
  'p.endseries': 'आपने सीरीज़ पूरी कर ली', 'p.sleep': 'स्लीप टाइमर', 'p.sleepoff': 'बंद', 'p.sleepend': 'एपिसोड के अंत में', 'p.still': 'क्या आप अभी भी देख रहे हैं?',
  'p.continue': 'देखना जारी रखें', 'p.delay': 'सबटाइटल विलंब', 'p.size': 'आकार', 'p.fit': 'वीडियो फ़िट', 'p.fitf': 'फ़िट', 'p.fillf': 'भरें', 'p.stretch': 'खींचें', 'p.crop': '21:9 क्रॉप',
  'p.stats': 'आँकड़े', 'p.method.direct': 'डायरेक्ट प्ले', 'p.method.remux': 'डायरेक्ट स्ट्रीम', 'p.method.transcode': 'ट्रांसकोडिंग', 'p.shortcuts': 'कीबोर्ड शॉर्टकट',
  'p.burn': 'इमेज सबटाइटल के लिए ट्रांसकोडिंग चाहिए — फिर से शुरू हो रहा है…', 'p.nohls': 'यह ब्राउज़र HLS स्ट्रीम नहीं चला सकता।', 'p.min': '{0} मिनट',
  'toast.offline': 'सर्वर उपलब्ध नहीं — फिर से जुड़ रहा है…', 'toast.online': 'फिर से जुड़ गया', 'toast.exit': 'बाहर निकलने के लिए फिर से Back दबाएँ', 'toast.added': 'मेरी सूची में जोड़ा गया',
  'toast.removed': 'मेरी सूची से हटाया गया', 'toast.scan': 'लाइब्रेरी स्कैन शुरू हुआ', 'toast.err': 'कुछ गलत हो गया', 'toast.newitems': 'लाइब्रेरी अपडेट हुई',
  'err.404': 'पेज नहीं मिला', 'err.home': 'होम पर जाएँ'
 }
};
var LANG = 'en';
function t(k) {
  var d = I18N[LANG] || I18N.en, s = d[k] !== undefined ? d[k] : (I18N.en[k] !== undefined ? I18N.en[k] : k), args = arguments;
  return String(s).replace(/\{(\d)\}/g, function (_, i) { var v = args[+i + 1]; return v === undefined ? '' : v; });
}
LF.t = t;
function fmtNum(n) { try { return new Intl.NumberFormat(LANG === 'hi' ? 'hi-IN' : undefined).format(n); } catch (e) { return String(n); } }
function fmtDate(ts) { try { return new Intl.DateTimeFormat(LANG === 'hi' ? 'hi-IN' : undefined, { dateStyle: 'medium', timeStyle: 'short' }).format(new Date(ts * 1000)); } catch (e) { return new Date(ts * 1000).toLocaleString(); } }

/* ===== state & api ===== */
var S = LF.S = { info: null, auth: null, settings: {}, schema: [], admin: false, profile: null, tv: !!BOOT.tv, page: null, pageKey: '', navCount: 0,
  online: true, lastHome: null };
function api(path, opts) {
  opts = opts || {};
  var init = { method: opts.method || 'GET', headers: { 'Accept': 'application/json' }, credentials: 'same-origin' };
  if (opts.body !== undefined) { init.headers['Content-Type'] = 'application/json'; init.body = JSON.stringify(opts.body); }
  else if (init.method !== 'GET' && init.method !== 'DELETE') { init.headers['Content-Type'] = 'application/json'; init.body = '{}'; }
  if (opts.signal) init.signal = opts.signal;
  if (opts.keepalive) init.keepalive = true;
  return fetch('/api/v1' + path, init).then(function (r) {
    return r.text().then(function (txt) {
      var d = null; try { d = txt ? JSON.parse(txt) : null; } catch (e) { d = null; }
      if (!r.ok) {
        var err = new Error((d && d.error && d.error.message) || r.statusText || 'Error');
        err.status = r.status; err.code = d && d.error && d.error.code; err.data = d;
        if (r.status === 401 && err.code === 'auth_required') showLogin();
        throw err;
      }
      return d;
    });
  });
}
LF.api = api;

/* ===== toasts ===== */
function toast(msg, type, action, ms) {
  var box = $('#toasts'), el = h('div.toast' + (type ? '.' + type : ''), { role: type === 'err' ? 'alert' : 'status' }, h('span', msg));
  if (action) el.appendChild(h('button', { onclick: function () { action.fn(); el.remove(); } }, action.label));
  box.appendChild(el);
  if (ms !== 0) setTimeout(function () { if (el.parentNode) el.remove(); }, ms || 3500);
  return el;
}
LF.toast = toast;

/* ===== theme & settings ===== */
var AVATARS = { a1: ['🦊', '#F76B15', '#C2410C'], a2: ['🐼', '#4B5563', '#111827'], a3: ['🦁', '#F5B800', '#B45309'], a4: ['🐬', '#0EA5E9', '#1E40AF'],
  a5: ['🦄', '#D946EF', '#7C3AED'], a6: ['🐸', '#22C55E', '#166534'], a7: ['🐯', '#F97316', '#9A3412'], a8: ['🐧', '#64748B', '#0F172A'],
  a9: ['🚀', '#6366F1', '#312E81'], a10: ['🎮', '#EC4899', '#9D174D'], a11: ['🌸', '#FB7185', '#BE123C'], a12: ['⚡', '#EAB308', '#713F12'] };
function avatar(key, cls) { var a = AVATARS[key] || AVATARS.a1; return h('div.avatar' + (cls ? '.' + cls : ''), { style: { background: 'linear-gradient(135deg,' + a[1] + ',' + a[2] + ')' }, 'aria-hidden': 'true' }, h('span', a[0])); }
function inkFor(hex) { var n = parseInt(hex.slice(1), 16), r = n >> 16, g = (n >> 8) & 255, b = n & 255; return (0.299 * r + 0.587 * g + 0.114 * b) > 165 ? '#111' : '#fff'; }
function applySettings() {
  var v = S.settings, root = document.documentElement;
  LANG = v.language === 'hi' ? 'hi' : 'en';
  root.setAttribute('lang', LANG);
  var theme = v.theme || 'dark';
  if (theme === 'auto') theme = (window.matchMedia && matchMedia('(prefers-color-scheme: light)').matches) ? 'light' : 'dark';
  root.setAttribute('data-theme', theme);
  root.style.setProperty('--accent', v.accent || '#E5484D');
  root.style.setProperty('--accent-ink', inkFor(v.accent || '#E5484D'));
  root.setAttribute('data-cards', v.card_size || 'm');
  root.setAttribute('data-motion', v.reduce_motion ? 'reduce' : 'full');
  var tvPref = v.tv_mode || 'auto', urlTv = /[?&]tv=1/.test(location.search) || location.hash.indexOf('#/tv') === 0;
  var handheld = /Mobi|iPhone|iPad|Android(?!.*(TV|AFT))/i.test(navigator.userAgent) && !BOOT.tv &&
    window.matchMedia && matchMedia('(pointer:coarse)').matches;
  if (handheld) store('tv', null); else if (urlTv) store('tv', true);
  S.tv = tvPref === 'on' || (tvPref === 'auto' && !handheld && (!!BOOT.tv || urlTv || store('tv') === true));
  if (tvPref === 'off') { S.tv = false; store('tv', false); }
  if (S.tv) { root.setAttribute('data-tv', '1'); root.setAttribute('data-lite', '1'); } else { root.removeAttribute('data-tv'); root.removeAttribute('data-lite'); }
  var scale = (v.ui_scale || 100) / 100; root.style.setProperty('--scale', scale);
}
function loadSettings() {
  return api('/settings').then(function (d) { S.settings = d.values || {}; S.admin = !!d.admin; applySettings(); })
    .catch(function (e) { if (e.status !== 409) throw e; applySettings(); });  /* 409: pick a profile first */
}

/* ===== spatial navigation (TV / keyboard) ===== */
var Nav = LF.Nav = {
  scope: function () {
    var p = $('.player'); if (p) return $('.p-dialog', p) || $('.p-menu', p) || p;
    var ovs = $$('.overlay'); if (ovs.length) return ovs[ovs.length - 1];
    return document.body;
  },
  visible: function (el) {
    if (el.disabled || el.getAttribute('aria-hidden') === 'true' || el.closest('.hidden')) return false;
    var r = el.getBoundingClientRect(); if (r.width < 2 || r.height < 2) return false;
    var cs = window.getComputedStyle(el); if (cs.visibility === 'hidden' || cs.display === 'none' || +cs.opacity === 0) return false;
    return true;
  },
  list: function (scope) {
    return $$('a[href],button,input,select,textarea,[tabindex]:not([tabindex="-1"])', scope).filter(function (el) {
      if (el.tabIndex < 0 && !el.hasAttribute('tabindex')) return false; return Nav.visible(el);
    });
  },
  first: function (scope) {
    scope = scope || Nav.scope();
    var af = $('[data-autofocus]', scope); if (af && Nav.visible(af)) return Nav.focus(af);
    var main = $('.p-bigplay, .hero .btn, .row .card, .who-p, .osk button, .grid .card, .page .btn, .set-nav a.on', scope) || Nav.list(scope)[0];
    if (main) Nav.focus(main);
  },
  focus: function (el) {
    if (!el) return;
    try { el.focus({ preventScroll: true }); } catch (e) { el.focus(); }
    var sc = el.closest('.scroller');
    if (sc) { var target = el.offsetLeft - (sc.clientWidth - el.offsetWidth) / 2; sc.scrollLeft = Math.max(0, target); var row = el.closest('[data-row]'); if (row) row._last = el; }
    var r = el.getBoundingClientRect(), navH = 90;
    var ov = el.closest('.overlay');
    if (ov) { if (r.top < 40 || r.bottom > window.innerHeight - 40) el.scrollIntoView({ block: 'center' }); }
    else if (!el.closest('.player') && !el.closest('.nav')) {
      if (r.top < navH || r.bottom > window.innerHeight - 20) {
        if (el.closest('.hero')) window.scrollTo(0, 0);
        else window.scrollBy(0, r.top - window.innerHeight / 2 + r.height / 2);
      }
    }
  },
  move: function (dir) {
    var scope = Nav.scope(), cur = document.activeElement;
    if (!cur || cur === document.body || !scope.contains(cur) || !Nav.visible(cur)) { Nav.first(scope); return true; }
    var cr = cur.getBoundingClientRect(), cx = cr.left + cr.width / 2, cy = cr.top + cr.height / 2, best = null, bestS = 1e9;
    Nav.list(scope).forEach(function (el) {
      if (el === cur) return;
      var r = el.getBoundingClientRect(), x = r.left + r.width / 2, y = r.top + r.height / 2, prim, sec, overlap;
      /* a candidate must lie beyond the current element in that direction, or share its row/column with
         its centre further along (a full-width bar in another row is never "to the right") */
      var rowOv = !(r.bottom <= cr.top + 2 || r.top >= cr.bottom - 2), colOv = !(r.right <= cr.left + 2 || r.left >= cr.right - 2);
      if (dir === 'left') { if (!(r.right <= cr.left + 4 || (rowOv && x < cx - 1))) return; prim = cr.left - r.right; overlap = rowOv; sec = Math.abs(y - cy); }
      else if (dir === 'right') { if (!(r.left >= cr.right - 4 || (rowOv && x > cx + 1))) return; prim = r.left - cr.right; overlap = rowOv; sec = Math.abs(y - cy); }
      else if (dir === 'up') { if (!(r.bottom <= cr.top + 4 || (colOv && y < cy - 1))) return; prim = cr.top - r.bottom; overlap = colOv; sec = Math.abs(x - cx); }
      else { if (!(r.top >= cr.bottom - 4 || (colOv && y > cy + 1))) return; prim = r.top - cr.bottom; overlap = colOv; sec = Math.abs(x - cx); }
      var s = Math.max(0, prim) + (overlap ? sec * 0.3 : sec * 2 + 40);
      if (s < bestS) { bestS = s; best = el; }
    });
    if (best && (dir === 'up' || dir === 'down')) {
      var row = best.closest('[data-row]'), crow = cur.closest('[data-row]');
      if (row && row !== crow && row._last && document.contains(row._last) && Nav.visible(row._last)) best = row._last;
    }
    if (best) { Nav.focus(best); return true; }
    return false;
  }
};
function isTyping() { var a = document.activeElement; return a && (a.tagName === 'INPUT' && /^(text|search|password|number|url|email)$/.test(a.type) || a.tagName === 'TEXTAREA' || a.isContentEditable); }
function keyName(e) {
  var k = e.key, c = e.keyCode;
  if (k === 'ArrowLeft' || k === 'Left' || c === 37) return 'left';
  if (k === 'ArrowRight' || k === 'Right' || c === 39) return 'right';
  if (k === 'ArrowUp' || k === 'Up' || c === 38) return 'up';
  if (k === 'ArrowDown' || k === 'Down' || c === 40) return 'down';
  if (k === 'Enter' || k === 'Select' || k === 'Accept' || c === 13 || c === 23 || c === 29443) return 'ok';
  if (k === 'Escape' || k === 'Esc' || c === 27 || c === 10009 || c === 461 || k === 'GoBack' || k === 'BrowserBack' || (c === 8 && !isTyping()) || (c === 4 && !k)) return 'back';
  if (k === 'MediaPlayPause' || c === 10252 || c === 179) return 'playpause';
  if (k === 'MediaPlay' || c === 415) return 'play';
  if (k === 'MediaPause' || c === 19) return 'pause';
  if (k === 'MediaStop' || c === 413) return 'stop';
  if (k === 'MediaRewind' || c === 412) return 'rew';
  if (k === 'MediaFastForward' || c === 417) return 'ff';
  if (k === 'MediaTrackNext' || k === 'MediaNextTrack' || c === 427 || c === 176) return 'next';
  if (k === 'MediaTrackPrevious' || k === 'MediaPreviousTrack' || c === 428 || c === 177) return 'prev';
  if (k === 'ColorF0Red' || c === 403) return 'red';
  if (k === 'ColorF1Green' || c === 404) return 'green';
  if (k === 'ColorF2Yellow' || c === 405) return 'yellow';
  if (k === 'ColorF3Blue' || c === 406) return 'blue';
  return null;
}
LF.keyName = keyName; LF.isTyping = isTyping;
function registerTvKeys() {
  try {
    if (window.tizen && tizen.tvinputdevice) {
      ['MediaPlayPause', 'MediaPlay', 'MediaPause', 'MediaStop', 'MediaRewind', 'MediaFastForward', 'MediaTrackNext', 'MediaTrackPrevious',
        'ColorF0Red', 'ColorF1Green', 'ColorF2Yellow', 'ColorF3Blue'].forEach(function (k) { try { tizen.tvinputdevice.registerKey(k); } catch (e) { /* unsupported key */ } });
    }
  } catch (e) { /* not tizen */ }
}
var lastBack = 0;
function exitApp() {
  try { if (window.tizen) { tizen.application.getCurrentApplication().exit(); return; } } catch (e) { /* ignore */ }
  try { if (window.webOS && webOS.platformBack) { webOS.platformBack(); return; } } catch (e) { /* ignore */ }
  try { window.close(); } catch (e) { /* ignore */ }
}
function goBack() {
  var menu = $('.menu'); if (menu) { menu.remove(); return; }
  var mini = $('.mini'); if (mini) { mini.remove(); }
  var ov = $$('.overlay'); if (ov.length) { var top = ov[ov.length - 1]; if (top._close) top._close(); else top.remove(); return; }
  var r = parseHash();
  if (r.name === 'home' || r.name === 'profiles' || r.name === 'login') {
    if (Date.now() - lastBack < 2200) { exitApp(); return; }
    lastBack = Date.now(); toast(t('toast.exit')); return;
  }
  if (histDepth() > 0) history.back(); else go('home', true);
}
document.addEventListener('keydown', function (e) {
  var k = keyName(e);
  if (LF.Player && LF.Player.active() && LF.Player.onKey(e, k)) { e.preventDefault(); return; }
  if (k === 'back') {
    if (isTyping() && e.keyCode === 8) return;
    if (isTyping() && (e.key === 'Escape' || e.keyCode === 27)) { document.activeElement.blur(); e.preventDefault(); return; }
    e.preventDefault(); goBack(); return;
  }
  if (k === 'left' || k === 'right' || k === 'up' || k === 'down') {
    var a = document.activeElement;
    if (isTyping() && (k === 'left' || k === 'right') && a.value) return;
    if (a && a.tagName === 'SELECT' && !S.tv) return;
    if (a && a.type === 'range' && (k === 'left' || k === 'right')) return;
    if (Nav.move(k)) e.preventDefault();
    return;
  }
  if (k === 'ok') {
    /* Activate on key *press*, explicitly: some TV browsers never send keyup for OK and some don't turn
       Enter into a click on custom controls. Native activation is prevented so nothing fires twice. */
    var c = document.activeElement;
    if (!c || c === document.body || c === document.documentElement) { e.preventDefault(); Nav.first(); return; }
    if (isTyping() || c.tagName === 'SELECT' || c.tagName === 'TEXTAREA') return;  /* forms / native pickers */
    if (c.tagName === 'INPUT' && !/^(checkbox|radio|button|submit|range)$/.test(c.type)) return;
    e.preventDefault();
    if (e.repeat) return;
    if (c.tagName === 'INPUT' && c.type === 'range') return;
    if (c.tagName === 'BUTTON' && c.type === 'submit' && c.form) { if (c.form.requestSubmit) c.form.requestSubmit(); else c.form.dispatchEvent(new Event('submit', { cancelable: true })); return; }
    c.click();
    return;
  }
  if ((e.key === 'ContextMenu' || e.keyCode === 93) && document.activeElement && document.activeElement.classList && document.activeElement.classList.contains('card')) {
    e.preventDefault(); quickActions(document.activeElement); return;
  }
  if (e.key === '/' && !isTyping() && !(LF.Player && LF.Player.active())) { e.preventDefault(); go('search'); }
});
/* TV: hide the mouse cursor only while the remote's arrow keys are in use (LG Magic Remote points with it) */
document.addEventListener('keydown', function (e) { if (S.tv && /^Arrow/.test(e.key || '')) document.documentElement.setAttribute('data-keynav', '1'); }, true);
document.addEventListener('mousemove', function () { document.documentElement.removeAttribute('data-keynav'); }, true);
/* Gamepad D-pad (optional) */
(function () {
  if (!navigator.getGamepads) return;
  var prev = {}, running = false;
  function loop() {
    var pads = navigator.getGamepads ? navigator.getGamepads() : [], any = false;
    for (var i = 0; i < pads.length; i++) {
      var p = pads[i]; if (!p) continue; any = true;
      [[12, 38], [13, 40], [14, 37], [15, 39], [0, 13], [1, 27], [9, 179]].forEach(function (m) {
        var b = p.buttons[m[0]], on = b && b.pressed, key = i + ':' + m[0];
        if (on && !prev[key]) {
          if (m[1] === 13) { var ae = document.activeElement; if (ae && ae !== document.body && ae.click) ae.click(); }
          else document.dispatchEvent(new KeyboardEvent('keydown', { key: { 38: 'ArrowUp', 40: 'ArrowDown', 37: 'ArrowLeft', 39: 'ArrowRight', 27: 'Escape', 179: 'MediaPlayPause' }[m[1]], bubbles: true }));
        }
        prev[key] = on;
      });
    }
    if (any) requestAnimationFrame(loop); else running = false;
  }
  window.addEventListener('gamepadconnected', function () { if (!running) { running = true; requestAnimationFrame(loop); } });
})();

/* ===== router ===== */
function parseHash() {
  var raw = location.hash.replace(/^#\/?/, ''), q = '', i = raw.indexOf('?');
  if (i >= 0) { q = raw.slice(i + 1); raw = raw.slice(0, i); }
  var parts = raw.split('/').filter(Boolean).map(decodeURIComponent);
  return { name: parts[0] || 'home', args: parts.slice(1), q: parseQS(q), raw: raw };
}
/* history depth lives in history.state so Back never leaves the app by accident */
function histDepth() { return (history.state && history.state.lfd) || 0; }
function go(path, replace) {
  var hsh = '#/' + path;
  try {
    if (replace) history.replaceState({ lfd: histDepth() }, '', hsh);
    else if (location.hash !== hsh) history.pushState({ lfd: histDepth() + 1 }, '', hsh);
  } catch (e) { if (replace) location.replace(hsh); else location.hash = hsh; return; }
  route();
}
document.addEventListener('click', function (e) {
  var a = e.target && e.target.closest ? e.target.closest('a[href^="#/"]') : null;
  if (!a || e.defaultPrevented || e.button || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
  e.preventDefault();
  go(a.getAttribute('href').slice(2));
});
LF.go = go; LF.parseHash = parseHash; LF.canBack = function () { return histDepth() > 0; };
var PAGES = {};
var OVERLAY_ROUTES = { item: 1, series: 1, watch: 1 };
var root, pageEl, navEl, tabEl;
var firstRoute = true;
function route() {
  firstRoute = false;
  var r = parseHash();
  closeMenus();
  if (r.name === 'tv') { store('tv', true); S.tv = true; applySettings(); go('home', true); return; }
  if (S.auth && S.auth.required && !S.auth.authenticated && r.name !== 'login') { go('login', true); return; }
  if (needsProfile() && r.name !== 'profiles' && r.name !== 'login') { go('profiles', true); return; }
  if (r.name !== 'watch' && LF.Player && LF.Player.active()) LF.Player.close(true);
  if (OVERLAY_ROUTES[r.name]) {
    if (!S.page) renderPage({ name: 'home', args: [], q: {} });
    closeOverlays(true);
    if (r.name === 'watch') { LF.Player.open(r.args[0], r.q); return; }
    if (r.name === 'item') openDetails(r.args[0]);
    if (r.name === 'series') openSeries(r.args[0]);
    return;
  }
  closeOverlays(true);
  var key = r.name + '/' + r.args.join('/') + '?' + qs(r.q);
  if (key === S.pageKey && S.page) { return; }
  renderPage(r);
}
function needsProfile() { return S.info && S.info.profilesEnabled && S.auth && !S.auth.profile && !store('skipWho'); }
function renderPage(r) {
  var fn = PAGES[r.name] || PAGES.notfound;
  S.page = r.name; S.pageKey = r.name + '/' + r.args.join('/') + '?' + qs(r.q);
  if (S.pageAbort) S.pageAbort.abort();
  if (S.pageCleanup) { try { S.pageCleanup(); } catch (e) { /* ignore */ } S.pageCleanup = null; }
  S.pageAbort = window.AbortController ? new AbortController() : null;
  clear(pageEl); window.scrollTo(0, 0);
  var full = r.name === 'profiles' || r.name === 'login';
  navEl.classList.toggle('hidden', full); tabEl.classList.toggle('hidden', full);
  updateNav(r.name);
  var res = fn(pageEl, r);
  if (res && res.then) res.then(function () { if (S.tv || document.activeElement === document.body) setTimeout(function () { Nav.first(document.body); }, 30); })
    .catch(function (e) { if (e && e.name === 'AbortError') return; console.error(e); pageEl.appendChild(errorBox(e)); });
  else if (S.tv) setTimeout(function () { Nav.first(document.body); }, 30);
  root.setAttribute('aria-busy', 'false');
}
function errorBox(e) {
  return h('div.empty', h('div.art', '⚠️'), h('h2', t('toast.err')), h('p.muted', (e && e.message) || ''),
    h('button.btn.btn-primary', { onclick: function () { S.pageKey = ''; route(); } }, t('b.retry')));
}

/* ===== shell: nav, tab bar ===== */
function buildShell() {
  root = $('#app'); clear(root);
  navEl = h('header.nav', { role: 'banner' });
  pageEl = h('main#main', { role: 'main' });
  tabEl = h('nav.tabbar', { 'aria-label': 'Primary' });
  root.appendChild(navEl); root.appendChild(pageEl); root.appendChild(tabEl);
  var onScroll = function () { navEl.classList.toggle('solid', window.pageYOffset > 30 || S.page === 'settings' || S.page === 'search'); };
  window.addEventListener('scroll', onScroll, { passive: true });
  renderNav();
}
var NAV_ITEMS = [['home', 'nav.home', 'home'], ['shows', 'nav.shows', 'episodes'], ['movies', 'nav.movies', 'film'], ['new', 'nav.new', 'star'], ['mylist', 'nav.mylist', 'plus']];
function renderNav() {
  clear(navEl);
  var links = h('nav.nav-links', { 'aria-label': 'Main' }, NAV_ITEMS.map(function (n) { return h('a', { href: '#/' + n[0], 'data-nav': n[0] }, t(n[1])); }));
  var input = h('input', { type: 'search', placeholder: t('search.ph'), 'aria-label': t('nav.search'),
    oninput: debounce(function () { if (input.value.trim()) go('search?q=' + encodeURIComponent(input.value.trim()), S.page === 'search'); }, 250),
    onkeydown: function (e) { if (e.key === 'Enter' && input.value.trim()) go('search?q=' + encodeURIComponent(input.value.trim())); } });
  var sbox = h('div.nav-search', h('button', { 'aria-label': t('nav.search'), onclick: function () {
    if (S.tv || window.innerWidth < 769) { go('search'); return; }
    sbox.classList.toggle('open'); if (sbox.classList.contains('open')) input.focus();
  } }, icon('search')), input);
  input.addEventListener('blur', function () { if (!input.value) sbox.classList.remove('open'); });
  var prof = S.auth && S.auth.profile;
  var pbtn = h('button.rel', { 'aria-label': t('nav.settings'), 'aria-haspopup': 'menu', onclick: function (e) { e.stopPropagation(); profileMenu(pbtn); } },
    prof ? avatar(prof.avatar) : h('div.avatar', { style: { background: 'var(--surface3)' } }, icon('user')));
  var tvb = (S.settings.show_tv_button !== false && !S.tv) ? h('button.btn.btn-ghost.btn-sm.nav-tvbtn', { onclick: openTvModal }, icon('tv'), t('nav.tv')) : null;
  append(navEl, [h('a.logo', { href: '#/home', 'aria-label': 'LocalFlix home' }, h('span', 'Local'), h('b', 'Flix')), links, h('div.nav-spacer'),
    h('div.nav-act.rel', sbox, tvb, pbtn)]);
  clear(tabEl);
  [['home', 'nav.home', 'home'], ['shows', 'nav.shows', 'episodes'], ['movies', 'nav.movies', 'film'], ['search', 'nav.search', 'search'], ['mylist', 'nav.mylist', 'plus']].forEach(function (n) {
    tabEl.appendChild(h('a', { href: '#/' + n[0], 'data-nav': n[0] }, icon(n[2]), h('span', t(n[1]))));
  });
}
function updateNav(name) {
  $$('[data-nav]').forEach(function (a) { a.classList.toggle('on', a.getAttribute('data-nav') === name); });
  navEl.classList.toggle('solid', window.pageYOffset > 30 || name === 'settings' || name === 'search' || name === 'notfound');
}
function closeMenus() { $$('.menu').forEach(function (m) { m.remove(); }); $$('.mini').forEach(function (m) { m.remove(); }); }
document.addEventListener('click', function (e) { if (!e.target.closest('.menu')) $$('.menu').forEach(function (m) { m.remove(); }); });
function profileMenu(anchor) {
  closeMenus();
  var items = [];
  if (S.info && S.info.profilesEnabled) items.push(h('button', { onclick: function () { store('skipWho', null); switchProfile(); } }, icon('user'), t('nav.switch')));
  items.push(h('a', { href: '#/settings/general' }, icon('gear'), t('nav.settings')));
  if (S.tv || window.innerWidth < 769) items.push(h('button', { onclick: openTvModal }, icon('tv'), t('nav.tv')));
  if (S.auth && S.auth.required) { items.push(h('hr')); items.push(h('button', { onclick: function () { api('/auth/logout', { method: 'POST' }).then(function () { location.reload(); }); } }, icon('lock'), t('nav.signout'))); }
  var m = h('div.menu', { role: 'menu' }, items);
  (anchor.parentNode || anchor).appendChild(m);
  setTimeout(function () { Nav.focus($('button,a', m)); }, 10);
}
function switchProfile() { S.auth.profile = null; go('profiles'); }

/* ===== overlays / modals ===== */
function modal(content, opts) {
  opts = opts || {};
  var prevFocus = document.activeElement;
  var ov = h('div.overlay', { role: 'dialog', 'aria-modal': 'true' });
  var box = h('div.modal' + (opts.small ? '.sm' : '') + (opts.cls ? '.' + opts.cls : ''));
  var x = h('button.btn.btn-icon.modal-x', { 'aria-label': t('b.close'), onclick: function () { ov._close(); } }, icon('close'));
  box.appendChild(x); append(box, [content]);
  ov.appendChild(box);
  ov.addEventListener('mousedown', function (e) { if (e.target === ov) ov._close(); });
  ov._close = function (silent) {
    if (!ov.parentNode) return;
    ov.remove();
    if (opts.onclose) opts.onclose(silent);
    if (prevFocus && document.contains(prevFocus)) Nav.focus(prevFocus);
  };
  document.body.appendChild(ov);
  document.body.style.overflow = 'hidden';
  var obs = setInterval(function () { if (!document.contains(ov)) { clearInterval(obs); if (!$('.overlay')) document.body.style.overflow = ''; } }, 300);
  setTimeout(function () { var af = $('[data-autofocus]', box) || $('.btn-primary', box) || x; Nav.focus(af); }, 30);
  return ov;
}
LF.modal = modal;
function routeModal(content, extra) {
  return modal(content, { cls: extra && extra.cls, onclose: function (silent) { if (silent) return; if (histDepth() > 0) history.back(); else go(S.page || 'home', true); } });
}
function closeOverlays(silent) { $$('.overlay').forEach(function (o) { if (o._close) o._close(silent); else o.remove(); }); document.body.style.overflow = ''; }

/* ===== cards & rows ===== */
var io = window.IntersectionObserver ? new IntersectionObserver(function (ents) {
  ents.forEach(function (en) { if (en.isIntersecting) { loadImg(en.target); io.unobserve(en.target); } });
}, { rootMargin: '400px 400px' }) : null;
function loadImg(img) { var s = img.getAttribute('data-src'); if (s) { img.src = s; img.removeAttribute('data-src'); } }
function lazyImg(src, alt) {
  /* artwork may still be generating on the server: retry twice (15 s, 60 s) while the image is on the page */
  var tries = 0;
  var img = h('img', { alt: alt || '', decoding: 'async', 'data-src': src, onload: function () { img.classList.add('ok'); },
    onerror: function () {
      img.classList.remove('ok');
      if (tries >= 2) return;
      var delay = tries++ === 0 ? 15000 : 60000;
      setTimeout(function () { if (document.contains(img)) img.src = src + (src.indexOf('?') < 0 ? '?' : '&') + 'r=' + Date.now(); }, delay);
    } });
  if (io) io.observe(img); else loadImg(img);
  return img;
}
function imgW(w) { return S.tv ? Math.min(w, 480) : w; }
function cardSub(it) {
  if (it.kind === 'episode') return [(it.seriesTitle || ''), it.season !== null && it.season !== undefined ? t('meta.ep', it.season, it.episode) : ''].filter(Boolean).join(' · ');
  if (it.kind === 'series') return it.seasonCount > 1 ? t('meta.seasons', it.seasonCount) : t('meta.episodes', it.episodeCount);
  return [it.year, it.duration ? fmtDur(it.duration) : ''].filter(Boolean).join(' · ');
}
function badges(it) {
  var b = [];
  if (it.isNew && !it.position) b.push(h('span.badge.fill', t('meta.new')));
  if (it.res === '4K') b.push(h('span.badge', '4K'));
  if (it.hdr) b.push(h('span.badge', it.hdr === 'dv' ? 'DV' : 'HDR'));
  if (it.audioCount > 1) b.push(h('span.badge', t('meta.audio', it.audioCount)));
  if (it.subCount > 0) b.push(h('span.badge', 'CC'));
  return b;
}
function card(it, opts) {
  opts = opts || {};
  var series = it.kind === 'series', poster = !!(S.settings.poster_cards && (it.kind === 'movie' || series));
  var href = series ? '#/series/' + it.id : '#/item/' + it.id;
  if (opts.play && !series) href = '#/watch/' + it.id;
  var sub = cardSub(it);
  if (opts.kind === 'continue' && it.duration && it.position) sub = t('meta.left', fmtDur(it.duration - it.position));
  var title = it.kind === 'episode' && opts.kind === 'continue' ? (it.seriesTitle || it.title) : it.title;
  var el = h('a.card' + (poster ? '.poster' : ''), { href: href, 'data-id': it.id, 'aria-label': title + (sub ? ', ' + sub : '') },
    h('div.ratio', { style: { background: hashColor(it.title) } },
      lazyImg((poster ? it.poster : (opts.kind === 'continue' ? it.thumb : it.thumb)) + '?w=' + imgW(poster ? 320 : 480), ''),
      h('div.shade'),
      h('div.bdg', badges(it)),
      it.watched ? h('span.wchk', icon('check')) : null,
      h('div.ct', title, sub ? h('small', sub) : null),
      (it.position > 0 && !it.watched && it.duration) ? h('div.prog', h('i', { style: { width: clamp(it.position / it.duration * 100, 2, 100) + '%' } })) : null));
  if (!S.tv && window.matchMedia && matchMedia('(hover:hover)').matches) {
    var timer;
    el.addEventListener('mouseenter', function () { timer = setTimeout(function () { miniCard(el, it); }, 400); });
    el.addEventListener('mouseleave', function () { clearTimeout(timer); });
  }
  el._item = it;
  return el;
}
LF.card = card;
function miniCard(el, it) {
  $$('.mini').forEach(function (m) { m.remove(); });
  var r = el.getBoundingClientRect(), w = r.width * 1.5;
  var left = clamp(r.left + window.pageXOffset - (w - r.width) / 2, 8, document.documentElement.clientWidth - w - 8);
  var series = it.kind === 'series';
  var inList = !!it.inList, watched = !!it.watched;
  var listBtn = h('button.btn.btn-ghost.btn-icon', { 'aria-label': t('b.mylist'), onclick: function (e) { e.preventDefault(); toggleList(it, listBtn); } }, icon(inList ? 'check' : 'plus'));
  var wBtn = series ? null : h('button.btn.btn-ghost.btn-icon', { 'aria-label': t('b.watched'), onclick: function (e) { e.preventDefault(); toggleWatched(it, wBtn); } }, icon('check'));
  if (wBtn && watched) wBtn.style.color = 'var(--ok)';
  var m = h('div.mini', { style: { left: left + 'px', top: (r.top + window.pageYOffset - r.height * 0.25) + 'px' } },
    h('a.ratio', { href: series ? '#/series/' + it.id : '#/item/' + it.id, style: { background: hashColor(it.title) } }, h('img', { src: (it.backdrop || it.thumb) + '?w=640', alt: '', onerror: function () { this.remove(); } })),
    h('div.mini-b',
      h('div.acts',
        h('button.btn.btn-primary.btn-icon', { 'aria-label': t('b.play'), onclick: function () { playItem(it); } }, icon('play')),
        listBtn, wBtn, h('div', { style: { flex: '1' } }),
        h('a.btn.btn-ghost.btn-icon', { href: series ? '#/series/' + it.id : '#/item/' + it.id, 'aria-label': t('b.more') }, icon('chev-d'))),
      h('div.t', it.kind === 'episode' ? (it.seriesTitle || it.title) : it.title),
      h('div.m', [it.year ? h('span', it.year) : null, it.duration ? h('span', fmtDur(it.duration)) : null, it.res ? h('span.badge', it.res) : null,
        it.kind === 'episode' ? h('span', t('meta.ep', it.season, it.episode) + ' ' + it.title) : null, series ? h('span', cardSub(it)) : null]),
      it.genres && it.genres.length ? h('div.m', it.genres.slice(0, 3).join(' • ')) : null));
  m.addEventListener('mouseleave', function () { m.remove(); });
  document.body.appendChild(m);
}
function quickActions(el) {
  var it = el._item; if (!it) return;
  closeMenus();
  var r = el.getBoundingClientRect();
  var m = h('div.menu', { role: 'menu', style: { position: 'fixed', left: Math.max(8, r.left) + 'px', right: 'auto', top: Math.min(r.bottom + 6, window.innerHeight - 200) + 'px' } },
    h('button', { onclick: function () { toggleList(it); m.remove(); } }, icon(it.inList ? 'check' : 'plus'), it.inList ? t('b.inlist') : t('b.mylist')),
    it.kind !== 'series' ? h('button', { onclick: function () { toggleWatched(it); m.remove(); } }, icon('check'), it.watched ? t('b.markunwatched') : t('b.markwatched')) : null,
    h('button', { onclick: function () { m.remove(); go((it.kind === 'series' ? 'series/' : 'item/') + it.id); } }, icon('info'), t('b.more')));
  document.body.appendChild(m);
  setTimeout(function () { Nav.focus($('button', m)); }, 10);
}
function toggleList(it, btn) {
  var add = !it.inList;
  return api('/mylist/' + it.id, { method: add ? 'POST' : 'DELETE' }).then(function () {
    it.inList = add; toast(add ? t('toast.added') : t('toast.removed'), 'ok');
    if (btn) { clear(btn); btn.appendChild(icon(add ? 'check' : 'plus')); }
  }).catch(function (e) { toast(e.message, 'err'); });
}
function toggleWatched(it, btn) {
  var w = !it.watched;
  return api('/items/' + it.id + '/watched', { body: { watched: w }, method: 'POST' }).then(function () {
    it.watched = w; if (btn) btn.style.color = w ? 'var(--ok)' : '';
    refreshSoon();
  }).catch(function (e) { toast(e.message, 'err'); });
}
function playItem(it) {
  if (it.kind === 'series') {
    return api('/series/' + it.id).then(function (s) { if (s.nextUp) go('watch/' + s.nextUp.id); });
  }
  go('watch/' + it.id);
}
LF.playItem = playItem;
function rowEl(rw) {
  var title = rw.raw ? rw.title : t(rw.title, rw.titleArg);
  var sc = h('div.scroller', { role: 'list' });
  var items = rw.items || [], shown = 0, CHUNK = 60;
  function more() { var end = Math.min(items.length, shown + CHUNK); for (; shown < end; shown++) sc.appendChild(card(items[shown], { kind: rw.kind })); }
  more();
  if (items.length > CHUNK) sc.addEventListener('scroll', function () { if (shown < items.length && sc.scrollLeft + sc.clientWidth * 2 > sc.scrollWidth) more(); }, { passive: true });
  var seeAll = rw.id === 'series' ? '#/shows' : rw.id === 'movies' ? '#/movies' : rw.id === 'recent' ? '#/new' : rw.id === 'mylist' ? '#/mylist' : null;
  var row = h('section.row', { 'data-row': rw.id, 'aria-label': title },
    h('div.row-h', h('h2', title), seeAll && !S.tv ? h('a', { href: seeAll }, t('b.seeall'), ' ›') : null),
    h('button.row-arrow.l', { 'aria-label': 'Scroll left', tabindex: '-1', onclick: function () { sc.scrollLeft -= sc.clientWidth * 0.85; } }, icon('back')),
    sc,
    h('button.row-arrow.r', { 'aria-label': 'Scroll right', tabindex: '-1', onclick: function () { sc.scrollLeft += sc.clientWidth * 0.85; } }, icon('chev-r')));
  return row;
}
function skeletonRows(n) {
  var out = [];
  for (var i = 0; i < (n || 3); i++) {
    var sc = h('div.scroller');
    for (var j = 0; j < 8; j++) sc.appendChild(h('div.skel-card', h('div.skel')));
    out.push(h('section.row', h('div.row-h', h('div.skel', { style: { width: '180px', height: '1.2em', borderRadius: '6px' } })), sc));
  }
  return out;
}

/* ===== home ===== */
function ffBanner() {
  if (!S.info || !S.info.ffmpeg || S.info.ffmpeg.available || store('ffDismiss')) return null;
  var b = h('div.banner', { role: 'note' }, h('div.grow', t('ff.missing'), h('code', S.info.installHint ? S.info.installHint.text : 'ffmpeg')),
    h('button.btn.btn-sm.btn-ghost', { onclick: function () { store('ffDismiss', true); b.remove(); } }, t('ff.dismiss')));
  return b;
}
function hero(items) {
  if (!items || !items.length) return null;
  var idx = 0, timer = null, paused = false;
  var bgs = items.map(function (it) { return h('div.hero-bg', { style: { backgroundImage: 'url("' + it.backdrop + '?w=' + (S.tv ? 1280 : 1920) + '"),' + hashColor(it.title) } }); });
  var body = h('div.hero-body'), dots = h('div.hero-dots', items.map(function () { return h('i'); }));
  var el = h('section.hero', { 'aria-roledescription': 'carousel', 'aria-label': 'Featured' }, bgs, body, items.length > 1 ? dots : null);
  function show(i) {
    idx = (i + items.length) % items.length;
    var it = items[idx];
    bgs.forEach(function (b, j) { b.classList.toggle('on', j === idx); });
    $$('i', dots).forEach(function (d, j) { d.classList.toggle('on', j === idx); });
    var hadFocus = body.contains(document.activeElement), focusIdx = hadFocus ? $$('.btn', body).indexOf(document.activeElement) : -1;
    clear(body);
    var resume = it.position > 15 && !it.watched;
    append(body, [
      h('h1.hero-title', it.kind === 'episode' ? (it.seriesTitle || it.title) : it.title),
      h('div.hero-meta', [it.year ? h('span', it.year) : null, it.duration ? h('span', fmtDur(it.duration)) : null, it.kind === 'series' ? h('span', cardSub(it)) : null,
        it.res ? h('span.badge', it.res) : null, it.hdr ? h('span.badge', 'HDR') : null, it.audioCount > 1 ? h('span.badge.ok', t('meta.audio', it.audioCount)) : null,
        it.contentRating ? h('span.badge', it.contentRating) : null]),
      it.overview ? h('p.hero-ov', it.overview) : null,
      h('div', h('button.btn.btn-primary', { onclick: function () { playItem(it); } }, icon('play'), resume ? t('b.resume') : t('b.play')),
        h('a.btn.btn-ghost', { href: it.kind === 'series' ? '#/series/' + it.id : '#/item/' + it.id }, icon('info'), t('b.more')))]);
    if (hadFocus) Nav.focus($$('.btn', body)[Math.max(0, focusIdx)]);
  }
  show(0);
  var reduce = S.settings.reduce_motion || (window.matchMedia && matchMedia('(prefers-reduced-motion: reduce)').matches);
  if (items.length > 1 && !reduce) {
    timer = setInterval(function () { if (!paused && document.contains(el) && !document.hidden) show(idx + 1); else if (!document.contains(el)) clearInterval(timer); }, 12000);
    el.addEventListener('mouseenter', function () { paused = true; }); el.addEventListener('mouseleave', function () { paused = false; });
    el.addEventListener('focusin', function () { paused = true; }); el.addEventListener('focusout', function () { paused = false; });
  }
  return el;
}
PAGES.home = function (el) {
  var rowsBox = h('div.rows', skeletonRows(3));
  var heroBox = h('div', h('div.hero', h('div.hero-bg.on.skel')));
  append(el, [ffBanner(), heroBox, rowsBox]);
  return api('/home', { signal: S.pageAbort && S.pageAbort.signal }).then(function (d) {
    S.lastHome = d;
    clear(heroBox); clear(rowsBox);
    if (!d.total) { heroBox.appendChild(emptyLibrary()); return; }
    var hr = hero(d.hero); if (hr) heroBox.appendChild(hr); else rowsBox.style.paddingTop = 'calc(var(--nav-h) + 30px)';
    d.rows.forEach(function (rw) { if (rw.items && rw.items.length) rowsBox.appendChild(rowEl(rw)); });
  });
};
function emptyLibrary() {
  var dirs = (S.info && S.info.mediaDirs) || [];
  var st = S.info && S.info.scan;
  return h('div.empty.page-pad', h('div.art', '🎬'), h('h2', t('empty.title')), h('p.muted', t('empty.body')),
    dirs.map(function (d) { return h('div', h('code', d)); }),
    st && st.errors && st.errors.length ? h('p', { style: { color: 'var(--warn)' } }, st.errors.slice(0, 3).join(' · ')) : null,
    h('p', h('button.btn.btn-primary', { 'data-autofocus': true, onclick: function () { api('/library/scan', { method: 'POST', body: { full: false } }).then(function () { toast(t('toast.scan')); }); } }, icon('refresh'), t('b.rescan')),
      S.admin ? h('a.btn.btn-ghost', { href: '#/settings/libraries' }, icon('folder'), t('set.addfolder')) : null),
    S.admin ? h('p', ['common', 'all'].map(function (sc) {
      return h('button.btn.btn-ghost', { onclick: function () {
        api('/settings?scope=server', { method: 'PUT', body: { values: { library_scope: sc } } }).then(function () { toast(t('toast.scan'), 'ok'); });
      } }, icon(sc === 'all' ? 'search' : 'film'), t(sc === 'all' ? 'empty.all' : 'empty.common'));
    })) : null);
}
var refreshSoon = debounce(function () {
  if (S.page === 'home' && !$('.overlay') && !(LF.Player && LF.Player.active())) {
    var focusedId = document.activeElement && document.activeElement.getAttribute && document.activeElement.getAttribute('data-id');
    var y = window.pageYOffset;
    api('/home').then(function (d) {
      var rowsBox = $('.rows', pageEl); if (!rowsBox) return;
      if (!d.total || !S.lastHome || !S.lastHome.total) { S.pageKey = ''; route(); return; }
      S.lastHome = d;
      var scrolls = {}; $$('[data-row]', rowsBox).forEach(function (r) { scrolls[r.getAttribute('data-row')] = $('.scroller', r).scrollLeft; });
      clear(rowsBox);
      d.rows.forEach(function (rw) { if (rw.items && rw.items.length) { var r = rowEl(rw); rowsBox.appendChild(r); if (scrolls[rw.id]) $('.scroller', r).scrollLeft = scrolls[rw.id]; } });
      window.scrollTo(0, y);
      if (focusedId) { var f = $('[data-id="' + focusedId + '"]', rowsBox); if (f) Nav.focus(f); }
    });
  } else if (S.page === 'mylist' && !$('.overlay') && !(LF.Player && LF.Player.active())) {
    S.pageKey = ''; route();  /* small page; browse grids are left alone so scroll/paging survive */
  }
}, 1500);
LF.refreshSoon = refreshSoon;

/* ===== browse grids ===== */
function browsePage(cfg) {
  return function (el, r) {
    var q = r.q, sort = q.sort || cfg.sort || 'added';
    var grid = h('div.grid' + (S.settings.poster_cards && cfg.posters ? '.posters' : ''), { 'data-row': 'grid' });
    var moreBox = h('div.load-more');
    var title = h('h1', t(cfg.title));
    var tb = h('div.toolbar');
    function setQ(patch) { var nq = {}; Object.keys(q).forEach(function (k) { nq[k] = q[k]; }); Object.keys(patch).forEach(function (k) { nq[k] = patch[k]; }); go(r.name + '?' + qs(nq), true); }
    if (!cfg.nosort) {
      tb.appendChild(h('select', { 'aria-label': 'Sort', onchange: function () { setQ({ sort: this.value }); } },
        (cfg.type === 'series' ? ['added', 'title', 'year', 'rating'] : ['added', 'title', 'year', 'duration', 'rating']).map(function (s) { return h('option', { value: s, selected: s === sort }, t('sort.' + s)); })));
      if (cfg.type !== 'series') [['unwatched', 'f.unwatched'], ['multiaudio', 'f.multiaudio'], ['hdr', 'f.hdr']].forEach(function (f) {
        tb.appendChild(h('button.chip' + (q[f[0]] === '1' ? '.on' : ''), { 'aria-pressed': q[f[0]] === '1' ? 'true' : 'false', onclick: function () { var p = {}; p[f[0]] = q[f[0]] === '1' ? '' : '1'; setQ(p); } }, t(f[1])));
      });
      if (cfg.type !== 'series') [['4K', 'f.4k'], ['1080p', 'f.1080'], ['720p', 'f.720']].forEach(function (f) {
        tb.appendChild(h('button.chip' + (q.res === f[0] ? '.on' : ''), { onclick: function () { setQ({ res: q.res === f[0] ? '' : f[0] }); } }, t(f[1])));
      });
      if (cfg.type !== 'series') api('/genres').then(function (g) {
        if (g.genres && g.genres.length) tb.appendChild(h('select', { 'aria-label': 'Genre', onchange: function () { setQ({ genre: this.value }); } }, h('option', { value: '' }, t('f.allgenres')),
          g.genres.map(function (x) { return h('option', { value: x, selected: q.genre === x }, x); })));
        if (g.folders && g.folders.length > 1) tb.appendChild(h('select', { 'aria-label': 'Folder', onchange: function () { setQ({ folder: this.value }); } }, h('option', { value: '' }, t('f.allfolders')),
          g.folders.map(function (x) { return h('option', { value: x, selected: q.folder === x }, x); })));
      }).catch(function () { /* optional */ });
    }
    append(el, [h('div.page', title, tb, grid, moreBox)]);
    el.firstChild.classList.add('page');
    var cursor = '0', loading = false, done = false;
    function load() {
      if (loading || done) return Promise.resolve();
      loading = true;
      var params = { type: cfg.type, sort: sort, order: q.order, cursor: cursor, limit: 60, unwatched: q.unwatched, multiaudio: q.multiaudio, hdr: q.hdr, res: q.res, genre: q.genre, folder: q.folder };
      return api('/items?' + qs(params)).then(function (d) {
        loading = false;
        d.items.forEach(function (it) { grid.appendChild(card(it)); });
        if (d.next) cursor = d.next; else done = true;
        clear(moreBox);
        if (!done) moreBox.appendChild(h('button.btn.btn-ghost', { onclick: load }, t('b.loadmore')));
        if (!grid.firstChild) grid.parentNode.appendChild(h('div.empty', h('div.art', '🍿'), h('h2', t('empty.nothing'))));
      }).catch(function (e) { loading = false; throw e; });
    }
    if (io) {
      var sio = new IntersectionObserver(function (en) { if (en[0].isIntersecting) load(); }, { rootMargin: '800px' });
      setTimeout(function () { sio.observe(moreBox); }, 100);
      S.pageCleanup = function () { sio.disconnect(); };
    }
    return load();
  };
}
PAGES.shows = browsePage({ title: 'nav.shows', type: 'series', posters: true });
PAGES.movies = browsePage({ title: 'nav.movies', type: 'movies', posters: true });
PAGES['new'] = browsePage({ title: 'nav.new', type: '', sort: 'added' });
PAGES.mylist = function (el) {
  var grid = h('div.grid');
  append(el, [h('div.page', h('h1', t('nav.mylist')), grid)]);
  return api('/home').then(function (d) {
    var row = null; d.rows.forEach(function (r) { if (r.id === 'mylist') row = r; });
    if (!row) { grid.parentNode.appendChild(h('div.empty', h('div.art', '➕'), h('h2', t('empty.list')), h('p.muted', t('empty.listbody')))); return; }
    row.items.forEach(function (it) { grid.appendChild(card(it)); });
  });
};

/* ===== search ===== */
function osk(input, onChange) {
  var keys = 'abcdefghijklmnopqrstuvwxyz1234567890'.split('');
  var box = h('div.osk', { 'aria-label': 'On-screen keyboard' });
  function press(ch) { input.value += ch; onChange(); }
  keys.forEach(function (k, i) { box.appendChild(h('button', { type: 'button', 'data-autofocus': i === 0 ? true : null, onclick: function () { press(k); } }, k.toUpperCase())); });
  box.appendChild(h('button.wide', { type: 'button', onclick: function () { press(' '); } }, '␣'));
  box.appendChild(h('button.wide', { type: 'button', onclick: function () { input.value = input.value.slice(0, -1); onChange(); } }, '⌫'));
  box.appendChild(h('button.wide', { type: 'button', onclick: function () { input.value = ''; onChange(); } }, '✕'));
  return box;
}
LF.osk = osk;
PAGES.search = function (el, r) {
  var q = r.q.q || '';
  var input = h('input.input', { type: 'search', value: q, placeholder: t('search.ph'), 'aria-label': t('nav.search'), style: { width: '100%', fontSize: '1.3rem', minHeight: '56px' } });
  var results = h('div');
  var ctrl = null;
  var run = debounce(function () {
    var v = input.value.trim();
    history.replaceState({ lfd: histDepth() }, '', '#/search' + (v ? '?q=' + encodeURIComponent(v) : ''));
    S.pageKey = 'search/?' + qs({ q: v });
    if (ctrl) ctrl.abort();
    ctrl = window.AbortController ? new AbortController() : null;
    clear(results);
    if (!v) { recent(); return; }
    api('/search?q=' + encodeURIComponent(v), { signal: ctrl && ctrl.signal }).then(function (d) {
      clear(results);
      var all = d.series.concat(d.items);
      if (!all.length) { results.appendChild(h('div.empty', h('div.art', '🔍'), h('h2', t('search.none', v)))); return; }
      var rec = store('recent') || []; rec = [v].concat(rec.filter(function (x) { return x !== v; })).slice(0, 8); store('recent', rec);
      var g = h('div.grid', { 'data-row': 'results' }); all.forEach(function (it) { g.appendChild(card(it)); }); results.appendChild(g);
    }).catch(function () { /* aborted */ });
  }, 150);
  function recent() {
    var rec = store('recent') || [];
    if (!rec.length) { results.appendChild(h('p.muted', t('search.hint'))); return; }
    results.appendChild(h('h3', t('search.recent')));
    results.appendChild(h('div', rec.map(function (x) { return h('button.chip', { onclick: function () { input.value = x; run(); } }, x); })));
  }
  input.addEventListener('input', run);
  append(el, [h('div.page', h('div', { style: { maxWidth: '900px', marginBottom: '1.4em' } }, input, S.tv ? osk(input, run) : null), results)]);
  if (!S.tv) setTimeout(function () { input.focus(); }, 50);
  if (q) run(); else recent();
};

/* ===== details ===== */
/* ===== details (movie / episode / series) ===== */
function langList(arr) {
  var seen = {}, out = [];
  (arr || []).forEach(function (x) {
    var n = (x.label || '').split(' · ')[0].replace(/\s*\[Forced\]/, '').trim() || '—';
    if (!seen[n]) { seen[n] = 1; out.push(n); }
  });
  return out;
}
function dpMeta(d, extra) {
  var bits = [];
  if (d.isNew && !d.position) bits.push(h('span.dp-new', t('meta.new')));
  if (d.year) bits.push(h('span', d.year));
  if (d.contentRating) bits.push(h('span.dp-rating', d.contentRating));
  if (extra) bits.push(h('span', extra));
  else if (d.duration) bits.push(h('span', fmtDur(d.duration)));
  if (d.res) bits.push(h('span.dp-box', d.res === '4K' ? '4K' : (d.res === '1080p' || d.res === '720p' ? 'HD' : d.res)));
  if (d.hdr) bits.push(h('span.dp-box', d.hdr === 'dv' ? 'Dolby Vision' : 'HDR'));
  if (d.audioCount > 1) bits.push(h('span.dp-box', t('meta.audio', d.audioCount)));
  if (d.subCount) bits.push(h('span.dp-box', 'CC'));
  if (d.rating) bits.push(h('span.dp-star', '★ ' + Number(d.rating).toFixed(1)));
  return h('div.dp-meta', bits);
}
function dpSide(rows) {
  return h('div.dp-side', rows.filter(function (r) { return r[1] && r[1].length; }).map(function (r) {
    return h('p', h('span.dim', r[0] + ': '), r[1].join(', '));
  }));
}
function roundBtn(ic, label, on, fn) {
  var b = h('button.dp-round' + (on ? '.on' : ''), { 'aria-label': label, title: label, onclick: function (e) { e.preventDefault(); fn(b); } }, icon(ic), h('span.dp-round-l', label));
  return b;
}
function setRound(b, ic, label, on) { clear(b); append(b, [icon(ic), h('span.dp-round-l', label)]); b.classList.toggle('on', on); b.setAttribute('aria-label', label); b.title = label; }
function dpShell(title, backdrop) {
  var hero = h('div.dp-hero', { style: { backgroundImage: 'url("' + backdrop + '?w=1280"),' + hashColor(title) } }, h('div.dp-shade'));
  var head = h('div.dp-head');
  var body = h('div.dp-body');
  return { hero: hero, head: head, body: body, root: h('div.dp', hero, head, body) };
}
function moreLikeThis(d, body) {
  var q = { limit: 13, sort: 'added' };
  if (d.genres && d.genres.length) q.genre = d.genres[0]; else if (d.folder) q.folder = d.folder; else return;
  q.type = d.kind === 'series' ? 'series' : 'movies';
  if (q.type === 'series') { delete q.genre; delete q.folder; }
  api('/items?' + qs(q)).then(function (r) {
    var items = (r.items || []).filter(function (x) { return x.id !== d.id; }).slice(0, 12);
    if (items.length < 2) return;
    var g = h('div.dp-grid', { 'data-row': 'similar' });
    items.forEach(function (it) { g.appendChild(card(it)); });
    body.appendChild(h('section.dp-sec', h('h2', t('dp.more')), g));
  }).catch(function () { /* optional */ });
}
function aboutSection(title, rows, tech) {
  return h('section.dp-sec.dp-about', h('h2', t('dp.about', title)),
    rows.filter(function (r) { return r[1] && r[1].length; }).map(function (r) { return h('p', h('span.dim', r[0] + ': '), r[1].join(', ')); }),
    tech ? h('p.dp-tech', tech) : null);
}
function openDetails(id) {
  if (LF.Player && LF.Player.preload) LF.Player.preload();
  var sh = dpShell('', '');
  sh.head.appendChild(h('div.skel', { style: { height: '2.4em', width: '60%', borderRadius: '8px' } }));
  routeModal(sh.root, { cls: 'dp-modal' });
  api('/items/' + id).then(function (d) {
    var fresh = dpShell(d.title, d.backdrop);
    sh.root.parentNode.replaceChild(fresh.root, sh.root); sh = fresh;
    var ep = d.kind === 'episode';
    var resume = d.position > 15 && !d.watched && d.position < d.duration - 60;
    var title = ep ? (d.seriesTitle || d.title) : d.title;
    var listB = roundBtn(d.inList ? 'check' : 'plus', t(d.inList ? 'b.inlist' : 'b.mylist'), d.inList, function (b) {
      toggleList(d).then(function () { setRound(b, d.inList ? 'check' : 'plus', t(d.inList ? 'b.inlist' : 'b.mylist'), d.inList); });
    });
    var watchB = roundBtn('check', t(d.watched ? 'b.watched' : 'b.markwatched'), d.watched, function (b) {
      toggleWatched(d).then(function () { setRound(b, 'check', t(d.watched ? 'b.watched' : 'b.markwatched'), d.watched); });
    });
    append(sh.head, [
      ep ? h('div.dp-kicker', t('dp.series')) : null,
      h('h1.dp-title', title),
      ep ? h('div.dp-sub', t('meta.ep', d.season, d.episode) + '  ' + d.title) : null,
      dpMeta(d),
      resume ? h('div.dp-prog', h('div.dp-bar', h('i', { style: { width: clamp(d.position / d.duration * 100, 2, 100) + '%' } })),
        h('span', t('meta.left', fmtDur(d.duration - d.position)))) : null,
      h('div.dp-actions',
        h('a.btn.btn-primary.dp-play', { href: '#/watch/' + d.id, 'data-autofocus': true }, icon('play'), resume ? t('b.resume') : t('b.play')),
        resume ? h('a.btn.btn-ghost', { href: '#/watch/' + d.id + '?t=0' }, icon('rew'), t('b.startover')) : null,
        h('div.dp-rounds', listB, watchB, ep && d.seriesId ? roundBtn('episodes', t('p.episodes'), false, function () { go('series/' + d.seriesId, true); }) : null))]);
    var audio = langList(d.audio), subs = langList(d.subtitles);
    var left = h('div.dp-main',
      d.unplayable ? h('div.banner', { style: { margin: '0 0 1em' } }, '⚠️ ' + t('meta.unplayable') + ': ' + d.unplayable) : null,
      h('p.dp-ov', d.overview || d.seriesOverview || ''));
    /* side column: cast & genres when known, otherwise languages; "About" lists everything else once */
    var rich = (d.cast && d.cast.length) || (d.genres && d.genres.length);
    var sideRows = rich ? [[t('meta.cast'), (d.cast || []).slice(0, 3)], [t('meta.genres'), d.genres]]
      : [[t('meta.audiotracks'), audio], [t('meta.subtitles'), subs]];
    append(sh.body, [h('div.dp-cols', left, dpSide(sideRows))]);
    if (d.next) {
      var n = d.next;
      sh.body.appendChild(h('section.dp-sec', h('h2', t('p.next')), episodeRow(n, true)));
    }
    if (!ep) moreLikeThis(d, sh.body);
    var tech = [(d.videoCodec || '').toUpperCase(), d.width ? d.width + '×' + d.height : '', d.bitDepth > 8 ? d.bitDepth + '-bit' : '',
      (d.container || '').toUpperCase(), fmtSize(d.size)].filter(Boolean).join(' · ') + (d.fileName ? '  ·  ' + d.fileName : '');
    sh.body.appendChild(aboutSection(title, rich ? [[t('meta.cast'), d.cast], [t('meta.genres'), d.genres], [t('meta.audiotracks'), audio],
      [t('meta.subtitles'), subs]] : [], tech));
    setTimeout(function () { Nav.focus($('[data-autofocus]', sh.root)); }, 20);
  }).catch(function (e) { clear(sh.body); sh.body.appendChild(errorBox(e)); });
}
function episodeRow(e, compact) {
  var prog = e.position > 0 && !e.watched && e.duration ? h('div.prog', h('i', { style: { width: clamp(e.position / e.duration * 100, 2, 100) + '%' } })) : null;
  return h('a.dp-ep', { href: '#/watch/' + e.id, 'data-id': e.id, 'aria-label': (e.episode !== null && e.episode !== undefined ? e.episode + '. ' : '') + e.title },
    compact ? null : h('div.dp-ep-n', e.episode !== null && e.episode !== undefined ? e.episode : '•'),
    h('div.dp-ep-thumb', { style: { background: hashColor(e.title) } }, h('div'), lazyImg(e.thumb + '?w=480'), h('span.dp-ep-play', icon('play')), prog),
    h('div.dp-ep-b',
      h('div.dp-ep-t', h('b', (compact ? t('meta.ep', e.season, e.episode) + ' · ' : '') + e.title), h('span', e.watched ? '✓ ' + fmtDur(e.duration) : fmtDur(e.duration))),
      e.overview ? h('div.dp-ep-o', e.overview) : null));
}
function openSeries(id) {
  if (LF.Player && LF.Player.preload) LF.Player.preload();
  var sh = dpShell('', '');
  sh.head.appendChild(h('div.skel', { style: { height: '2.4em', width: '60%', borderRadius: '8px' } }));
  routeModal(sh.root, { cls: 'dp-modal' });
  api('/series/' + id).then(function (s) {
    var fresh = dpShell(s.title, s.backdrop);
    sh.root.parentNode.replaceChild(fresh.root, sh.root); sh = fresh;
    function sn(e) { return e.season === null || e.season === undefined ? 1 : e.season; }
    var nu = s.nextUp;
    var curSeason = nu ? sn(nu) : (s.seasons[0] ? s.seasons[0].number : 1);
    var eps = h('div.dp-eps', { 'data-row': 'eps' });
    function renderEps() {
      clear(eps);
      s.episodes.filter(function (e) { return sn(e) === curSeason; }).forEach(function (e) { eps.appendChild(episodeRow(e, false)); });
    }
    var resume = nu && nu.position > 15 && !nu.watched;
    var allW = s.watchedCount >= s.episodeCount;
    var listB = roundBtn(s.inList ? 'check' : 'plus', t(s.inList ? 'b.inlist' : 'b.mylist'), s.inList, function (b) {
      toggleList(s).then(function () { setRound(b, s.inList ? 'check' : 'plus', t(s.inList ? 'b.inlist' : 'b.mylist'), s.inList); });
    });
    var watchB = roundBtn('check', t(allW ? 'b.watched' : 'b.markwatched'), allW, function (b) {
      allW = !allW;
      api('/items/' + s.id + '/watched', { method: 'POST', body: { watched: allW } }).then(function () {
        setRound(b, 'check', t(allW ? 'b.watched' : 'b.markwatched'), allW);
        s.episodes.forEach(function (e) { e.watched = allW; e.position = 0; }); renderEps(); refreshSoon();
      });
    });
    var extra = s.seasonCount > 1 ? t('meta.seasons', s.seasonCount) : t('meta.episodes', s.episodeCount);
    append(sh.head, [
      h('div.dp-kicker', t('dp.series')),
      h('h1.dp-title', s.title),
      dpMeta(s, extra),
      nu ? h('div.dp-sub', (resume ? t('b.resume') : (s.watchedCount ? t('dp.upnext') : t('dp.start'))) + ' · ' + t('meta.ep', nu.season, nu.episode) + ' ' + nu.title) : null,
      h('div.dp-actions',
        nu ? h('a.btn.btn-primary.dp-play', { href: '#/watch/' + nu.id, 'data-autofocus': true }, icon('play'),
          (resume ? t('b.resume') : t('b.play')) + ' ' + t('meta.ep', nu.season, nu.episode)) : null,
        h('div.dp-rounds', listB, watchB))]);
    var sel = s.seasons.length > 1 ? h('select.dp-season', { 'aria-label': 'Season', onchange: function () { curSeason = +this.value; renderEps(); } },
      s.seasons.map(function (x) { return h('option', { value: x.number, selected: x.number === curSeason }, (x.number === 0 ? t('dp.specials') : t('meta.season', x.number)) + ' (' + x.episodeCount + ')'); }))
      : h('span.dp-season-one', curSeason === 0 ? t('dp.specials') : t('meta.season', curSeason));
    append(sh.body, [
      h('div.dp-cols', h('div.dp-main', h('p.dp-ov', s.overview || '')), dpSide([[t('meta.genres'), s.genres]])),
      h('section.dp-sec', h('div.dp-sec-h', h('h2', t('p.episodes')), sel), eps)]);
    renderEps();
    moreLikeThis(s, sh.body);
    sh.body.appendChild(aboutSection(s.title, [[t('meta.genres'), s.genres]], extra));
    setTimeout(function () { Nav.focus($('[data-autofocus]', sh.root) || $('.dp-ep', sh.root)); }, 20);
  }).catch(function (e) { clear(sh.body); sh.body.appendChild(errorBox(e)); });
}

/* ===== profiles ===== */
function pinPad(title, onDone) {
  var val = '', dots = h('div.pin-dots', [0, 1, 2, 3].map(function () { return h('i'); }));
  var err = h('p', { style: { color: 'var(--err)', minHeight: '1.4em' }, role: 'alert' });
  var ov;
  function upd() { $$('i', dots).forEach(function (d, i) { d.classList.toggle('on', i < val.length); }); }
  function press(d) {
    if (val.length >= 4) return; val += d; upd();
    if (val.length === 4) {
      var v = val;
      onDone(v).then(function () { ov._close(); }).catch(function (e) { val = ''; upd(); err.textContent = e.message || t('who.wrongpin'); dots.classList.remove('shake'); void dots.offsetWidth; dots.classList.add('shake'); });
    }
  }
  var pad = h('div.pinpad', ['1', '2', '3', '4', '5', '6', '7', '8', '9', '', '0', '⌫'].map(function (d) {
    if (!d) return h('span', { style: { width: '76px', margin: '5px' } });
    return h('button', { type: 'button', onclick: function () { if (d === '⌫') { val = val.slice(0, -1); upd(); } else press(d); } }, d);
  }));
  var kh = function (e) { if (/^[0-9]$/.test(e.key)) { press(e.key); e.preventDefault(); } else if (e.key === 'Backspace' && val) { val = val.slice(0, -1); upd(); e.preventDefault(); e.stopPropagation(); } };
  document.addEventListener('keydown', kh, true);
  ov = modal(h('div', { style: { textAlign: 'center' } }, h('h2', title), dots, err, pad), { small: true, onclose: function () { document.removeEventListener('keydown', kh, true); } });
  setTimeout(function () { Nav.focus($('.pinpad button', ov)); }, 40);
  return ov;
}
function selectProfile(p) {
  var done = function (r) { S.auth.profile = r; store('lastProfile', p.id); return loadSettings().then(function () { renderNav(); go('home'); }); };
  if (p.hasPin) {
    pinPad(t('who.pin'), function (pin) { return api('/profiles/' + p.id + '/select', { method: 'POST', body: { pin: pin } }).then(done); });
  } else api('/profiles/' + p.id + '/select', { method: 'POST', body: {} }).then(done).catch(function (e) { toast(e.message, 'err'); });
}
function editProfile(p, after) {
  var name = h('input.input', { value: p ? p.name : '', placeholder: t('who.name'), maxlength: 30, 'data-autofocus': !S.tv });
  var av = p ? p.avatar : 'a' + (1 + Math.floor(Math.random() * 12));
  var avBox = h('div.avatars', Object.keys(AVATARS).map(function (k) {
    var b = h('button' + (k === av ? '.on' : ''), { type: 'button', 'aria-label': k, onclick: function () { av = k; $$('button', avBox).forEach(function (x) { x.classList.remove('on'); }); b.classList.add('on'); } }, avatar(k));
    return b;
  }));
  var pin = h('input.input', { type: 'password', inputmode: 'numeric', maxlength: 4, placeholder: t('who.pinopt'), autocomplete: 'off' });
  var kids = p ? !!p.kids : false;
  var kidsT = h('button.toggle', { role: 'switch', 'aria-checked': String(kids), 'aria-label': t('who.kids'), onclick: function () { kids = !kids; kidsT.setAttribute('aria-checked', String(kids)); } });
  var ov = modal(h('div',
    h('h2', p ? t('b.edit') : t('who.add')), h('div.field', h('div.lbl', h('b', t('who.name'))), h('div.ctl', name)),
    h('div', h('b', t('who.avatar')), avBox),
    h('div.field', h('div.lbl', h('b', t('who.pinopt'))), h('div.ctl', pin)),
    h('div.field', h('div.lbl', h('b', t('who.kids'))), h('div.ctl', kidsT)),
    p ? h('div', { style: { margin: '1em 0' } },
      h('button.btn.btn-sm.btn-ghost', { onclick: function () { api('/profiles/' + p.id, { method: 'PATCH', body: { clearHistory: true } }).then(function () { toast(t('set.saved'), 'ok'); }); } }, t('who.clearh')),
      h('button.btn.btn-sm.btn-ghost', { onclick: function () { api('/profiles/' + p.id, { method: 'PATCH', body: { clearList: true } }).then(function () { toast(t('set.saved'), 'ok'); }); } }, t('who.clearl'))) : null,
    h('div', { style: { marginTop: '1.2em' } },
      h('button.btn.btn-primary', { onclick: function () {
        var body = { name: name.value, avatar: av, kids: kids };
        if (pin.value) body.pin = pin.value;
        (p ? api('/profiles/' + p.id, { method: 'PATCH', body: body }) : api('/profiles', { method: 'POST', body: body }))
          .then(function () { ov._close(); after(); }).catch(function (e) { toast(e.message, 'err'); });
      } }, t('b.save')),
      p ? h('button.btn.btn-danger', { onclick: function () { if (confirm(t('b.delete') + ' ' + p.name + '?')) api('/profiles/' + p.id, { method: 'DELETE' }).then(function () { ov._close(); after(); }).catch(function (e) { toast(e.message, 'err'); }); } }, icon('trash'), t('b.delete')) : null)), { small: true });
}
PAGES.profiles = function (el) {
  var manage = false;
  var list = h('div.who-list', { 'data-row': 'who' });
  var mbtn = h('button.btn.btn-ghost', { onclick: function () { manage = !manage; mbtn.textContent = manage ? t('b.done') : t('who.manage'); load(); } }, t('who.manage'));
  append(el, [h('div.who', h('div.logo', { style: { position: 'absolute', top: '24px', left: '4vw', fontSize: '1.8rem' } }, h('span', 'Local'), h('b', 'Flix')),
    h('h1', manage ? t('who.manage') : t('who')), list, S.admin ? mbtn : null)]);
  function load() {
    return api('/profiles').then(function (d) {
      clear(list);
      if (!d.enabled && d.profiles.length === 1 && !manage) { selectProfile(d.profiles[0]); return; }
      d.profiles.forEach(function (p) {
        list.appendChild(h('button.who-p', { onclick: function () { if (manage) editProfile(p, load); else selectProfile(p); }, 'aria-label': p.name, 'data-autofocus': store('lastProfile') === p.id ? true : null },
          avatar(p.avatar), h('div.nm', p.name, p.hasPin ? icon('lock') : null, manage ? icon('edit') : null)));
      });
      if (S.admin && d.profiles.length < 12) list.appendChild(h('button.who-p', { onclick: function () { editProfile(null, load); } },
        h('div.avatar', { style: { background: 'var(--surface2)' } }, h('span', '+')), h('div.nm', t('who.add'))));
      setTimeout(function () { Nav.first(document.body); }, 20);
    });
  }
  return load();
};

/* ===== login ===== */
function showLogin() { if (parseHash().name !== 'login') { if (S.auth) S.auth.authenticated = false; go('login', true); } }
PAGES.login = function (el) {
  var pw = h('input.input', { type: 'password', placeholder: t('login.pw'), autocomplete: 'current-password', style: { width: '100%' }, 'data-autofocus': !S.tv });
  var err = h('p', { style: { color: 'var(--err)', minHeight: '1.4em' }, role: 'alert' });
  function submit(e) {
    if (e) e.preventDefault();
    api('/auth/login', { method: 'POST', body: { password: pw.value } }).then(function () { location.hash = '#/home'; location.reload(); })
      .catch(function (x) { err.textContent = x.message || t('login.bad'); pw.value = ''; });
  }
  append(el, [h('div.who', h('div.logo', { style: { marginBottom: '1em' } }, h('span', 'Local'), h('b', 'Flix')),
    h('form', { onsubmit: submit, style: { width: 'min(420px,90vw)' } }, h('h1', { style: { fontSize: '1.6rem' } }, t('login.title', BOOT.name || 'LocalFlix')), pw, err,
      h('button.btn.btn-accent', { type: 'submit', style: { width: '100%' } }, t('b.login'))),
    S.tv ? osk(pw, function () { /* noop */ }) : null)]);
};

/* ===== open on TV ===== */
function openTvModal() {
  closeMenus();
  var urls = (S.info && S.info.urls) || [location.origin];
  var main = urls[0] + '/tv';
  var qrBox = h('div.qr', h('img', { src: '/api/v1/qr?url=' + encodeURIComponent(urls[0]), alt: 'QR code for ' + urls[0], onerror: function () { qrBox.remove(); } }));
  var copyBtn = h('button.btn.btn-ghost.btn-sm', { onclick: function () { copyText(main).then(function (ok) { copyBtn.textContent = ok ? t('b.copied') : main; }); } }, icon('copy'), t('b.copy'));
  var otl = (S.auth && S.auth.required && S.admin) ? h('button.btn.btn-sm.btn-ghost', { onclick: function () {
    api('/auth/qr-token', { method: 'POST' }).then(function (d) { clear(qrBox); qrBox.appendChild(h('img', { src: '/api/v1/qr?url=' + encodeURIComponent(d.url), alt: 'One-time login QR' })); });
  } }, icon('lock'), t('tv.otl')) : null;
  modal(h('div.tvm', h('h2', t('tv.title')),
    h('ol', h('li', t('tv.s1')), h('li', t('tv.s2'))), h('div.url', main), copyBtn,
    h('ol', { start: '3' }, h('li', t('tv.s3'))),
    S.settings.show_qr === false ? null : h('div', h('p.muted', t('tv.scan')), qrBox, otl),
    urls.length > 1 ? h('p.muted', t('tv.all') + ': ', urls.join('  ·  ')) : null), { small: true });
}
LF.openTvModal = openTvModal;

PAGES.notfound = function (el) {
  append(el, [h('div.empty.page-pad', h('div.art', '🧭'), h('h2', t('err.404')), h('a.btn.btn-primary', { href: '#/home' }, t('err.home')))]);
};
"""


APP_JS += r"""
/* ===== settings ===== */
var SET_CATS = ['general', 'libraries', 'playback', 'subtitles', 'transcoding', 'network', 'metadata', 'profiles', 'advanced'];
var CHOICE_LABELS = {
  theme: { dark: 'Dark', oled: 'OLED Black', light: 'Light', auto: 'Auto' }, language: { en: 'English', hi: 'हिन्दी' },
  card_size: { s: 'S', m: 'M', l: 'L' }, tv_mode: { auto: 'Auto', on: 'On', off: 'Off' }, subtitle_mode: { off: 'Off', auto: 'Auto', always: 'Always' },
  resume_mode: { ask: 'Ask', auto: 'Auto', never: 'Never' }, hls_mode: { ondemand: 'On-demand', simple: 'Simple' }, segment_format: { fmp4: 'fMP4', ts: 'TS (legacy)' },
  bind: { lan: 'LAN', localhost: 'Localhost only' }, max_quality: { auto: 'Auto', original: 'Original', '2160p': '4K · 20 Mbps', '1080p': '1080p · 8 Mbps', '720p': '720p · 4 Mbps', '480p': '480p · 1.5 Mbps', '360p': '360p · 0.8 Mbps' },
  library_mode: { auto: 'Movies & Shows', videos: 'Videos only' }
};
function labelFor(key) { return key.replace(/_/g, ' ').replace(/\b\w/, function (c) { return c.toUpperCase(); }); }
function saveSetting(key, val, spec) {
  var body = {}; body[key] = val;
  return api('/settings' + (spec.scope === 'server' ? '?scope=server' : ''), { method: 'PUT', body: { values: body } }).then(function (d) {
    if (d.errors && d.errors[key]) { toast(d.errors[key], 'err'); return; }
    if (spec.type === 'list' && typeof val === 'string') val = val.split(',').map(function (x) { return x.trim(); }).filter(Boolean);
    S.settings[key] = val; applySettings();
    if (key === 'language' || key === 'show_tv_button') { renderNav(); S.pageKey = ''; route(); }
    toast(d.restartRequired ? t('set.saved') + ' · ' + t('set.restart') : t('set.saved'), 'ok', null, 1400);
  }).catch(function (e) { toast((e.data && e.data.errors && e.data.errors[key]) || e.message, 'err'); });
}
function control(spec) {
  var key = spec.key, val = S.settings[key], dis = spec.admin && !S.admin || (spec.scope === 'server' && !S.admin);
  var save = debounce(function (v) { saveSetting(key, v, spec); }, 400);
  var c;
  if (spec.type === 'bool') {
    c = h('button.toggle', { role: 'switch', 'aria-checked': String(!!val), 'aria-label': labelFor(key), disabled: dis, onclick: function () { val = !val; c.setAttribute('aria-checked', String(val)); saveSetting(key, val, spec); } });
  } else if (spec.type === 'enum' && spec.choices.length <= 4 && !S.tv) {
    c = h('div.seg', { role: 'radiogroup' }, spec.choices.map(function (ch) {
      var b = h('button' + (ch === val ? '.on' : ''), { role: 'radio', 'aria-checked': String(ch === val), disabled: dis, onclick: function () { val = ch; $$('button', c).forEach(function (x) { x.classList.toggle('on', x === b); x.setAttribute('aria-checked', String(x === b)); }); saveSetting(key, ch, spec); } },
        (CHOICE_LABELS[key] && CHOICE_LABELS[key][ch]) || ch);
      return b;
    }));
  } else if (spec.type === 'enum') {
    c = h('select.input', { disabled: dis, 'aria-label': labelFor(key), onchange: function () { saveSetting(key, this.value, spec); } },
      spec.choices.map(function (ch) { return h('option', { value: ch, selected: ch === val }, (CHOICE_LABELS[key] && CHOICE_LABELS[key][ch]) || ch); }));
  } else if ((spec.type === 'int' || spec.type === 'float') && spec.max !== undefined && spec.max - spec.min <= 400 && spec.step !== undefined || (spec.type === 'int' && spec.max <= 250 && spec.min >= 0 && key.indexOf('sub_') === 0)) {
    var out = h('output', String(val));
    var inp = h('input', { type: 'range', min: spec.min, max: spec.max, step: spec.step || 1, value: val, disabled: dis, 'aria-label': labelFor(key),
      oninput: function () { out.textContent = this.value; S.settings[key] = +this.value; if (key.indexOf('sub_') === 0) updSubPreview(); save(+this.value); } });
    c = h('div.range', inp, out);
  } else if (spec.type === 'color') {
    c = h('div.swatches', (spec.presets || []).map(function (col) {
      return h('button' + (col.toUpperCase() === String(val).toUpperCase() ? '.on' : ''), { style: { background: col }, 'aria-label': col, disabled: dis,
        onclick: function () { $$('button', c).forEach(function (x) { x.classList.remove('on'); }); this.classList.add('on'); S.settings[key] = col; if (key.indexOf('sub_') === 0) updSubPreview(); saveSetting(key, col, spec); } });
    }), h('input', { type: 'color', value: val, 'aria-label': 'Custom colour', disabled: dis, style: { width: '34px', height: '30px', border: '0', background: 'none', marginLeft: '6px' },
      onchange: function () { saveSetting(key, this.value.toUpperCase(), spec); } }));
  } else if (spec.type === 'list') {
    c = h('input.input', { value: (val || []).join(', '), disabled: dis, 'aria-label': labelFor(key), onchange: function () { saveSetting(key, this.value, spec); } });
  } else {
    c = h('input.input', { type: spec.type === 'secret' ? 'password' : (spec.type === 'int' || spec.type === 'float' ? 'number' : 'text'), value: val === null || val === undefined ? '' : val,
      min: spec.min, max: spec.max, step: spec.type === 'float' ? 'any' : null, disabled: dis, 'aria-label': labelFor(key), autocomplete: 'off',
      onchange: function () { var v = this.value; if (spec.type === 'int') v = parseInt(v, 10); if (spec.type === 'float') v = parseFloat(v); saveSetting(key, v, spec); } });
  }
  return h('div.field' + (dis ? '.dis' : ''), { title: spec.help || '' },
    h('div.lbl', h('b', labelFor(key), spec.restart ? h('span.tag.warn', t('set.restart')) : null, spec.admin || spec.scope === 'server' ? h('span.tag', spec.scope === 'server' ? 'server' : t('set.admin')) : null),
      h('small', spec.help || '')), h('div.ctl', c));
}
function updSubPreview() {
  var p = $('#subprev .subs'); if (!p) return;
  applySubStyle(p, S.settings);
}
function applySubStyle(el, v) {
  el.className = 'subs edge-' + (v.sub_edge || 'shadow') + ' font-' + (v.sub_font || 'sans');
  var size = (v.sub_size || 100) / 100;
  el.style.setProperty('--sub-size', (2.4 * size) + 'vw');
  el.style.setProperty('--sub-size-m', (4.4 * size) + 'vw');
  el.style.setProperty('--sub-color', v.sub_color || '#fff');
  el.style.setProperty('--sub-bg', String((v.sub_bg_opacity === undefined ? 40 : v.sub_bg_opacity) / 100));
  el.style.setProperty('--sub-bottom', (v.sub_position === undefined ? 8 : v.sub_position) + '%');
}
LF.applySubStyle = applySubStyle;
function adminGate() {
  if (S.admin) return null;
  return h('div.card-box', h('p.muted', t('set.unsaved')), h('button.btn.btn-accent', { onclick: function () {
    var inp = h('input.input', { type: 'password', placeholder: t('admin.pin'), 'data-autofocus': true, style: { width: '100%' } });
    var ov = modal(h('form', { onsubmit: function (e) { e.preventDefault(); api('/auth/admin', { method: 'POST', body: { pin: inp.value } }).then(function () { ov._close(); return loadSettings(); }).then(function () { S.pageKey = ''; route(); }).catch(function (x) { toast(x.message, 'err'); }); } },
      h('h2', t('b.unlock')), inp, h('div', { style: { marginTop: '1em' } }, h('button.btn.btn-primary', { type: 'submit' }, t('b.ok')))), { small: true });
  } }, icon('lock'), t('b.unlock')));
}
PAGES.settings = function (el, r) {
  var cat = r.args[0] || 'general';
  var nav = h('nav.set-nav', { 'aria-label': t('set.title') }, h('h1', t('set.title')),
    SET_CATS.map(function (c) { return h('a' + (c === cat ? '.on' : ''), { href: '#/settings/' + c }, t('set.' + c)); }));
  var main = h('section.set-main');
  append(el, [h('div.set', nav, main)]);
  return api('/settings/schema').then(function (d) {
    S.schema = d.settings; S.admin = d.admin;
    main.appendChild(h('h2', t('set.' + cat)));
    var gate = adminGate();
    var fields = d.settings.filter(function (s) { return s.category === cat && s.key !== 'library_scope'; });
    var extra = SET_EXTRA[cat];
    if (gate && (cat === 'transcoding' || cat === 'network' || cat === 'libraries' || cat === 'advanced')) main.appendChild(gate);
    if (extra && extra.before) main.appendChild(extra.before());
    fields.forEach(function (s) { main.appendChild(control(s)); });
    if (extra && extra.after) main.appendChild(extra.after());
  });
};
var SET_EXTRA = {
  subtitles: { before: function () {
    var p = h('div.sub-prev#subprev', h('div.subs', h('span.cue', t('set.preview'))));
    setTimeout(updSubPreview, 0); return p;
  } },
  general: { after: function () { return h('div.card-box', h('b', 'LocalFlix'), h('p.muted', 'v' + BOOT.version + ' · ' + ((S.info && S.info.platform) || ''))); } },
  libraries: { before: function () {
    var box = h('div');
    function scopeCard(cur) {
      var preview = h('div.muted', { style: { fontSize: '.88rem', marginTop: '.6em' } });
      function showPreview(sc) {
        clear(preview);
        if (sc === 'folders') return;
        api('/libraries/discover?scope=' + sc).then(function (r) {
          clear(preview);
          preview.appendChild(h('b', t('scope.found') + ': '));
          preview.appendChild(document.createTextNode(r.folders.length ? r.folders.map(function (f) { return f.label + ' (' + f.path + ')'; }).join(' · ') : t('scope.none')));
        }).catch(function () { /* admin only */ });
      }
      var opts = ['folders', 'common', 'all'].map(function (sc) {
        var b = h('button.chip' + (sc === cur ? '.on' : ''), { role: 'radio', 'aria-checked': String(sc === cur), disabled: !S.admin, style: { display: 'block', width: '100%', textAlign: 'left', borderRadius: '10px', padding: '.7em 1em', height: 'auto', marginBottom: '.5em' },
          onclick: function () {
            saveSetting('library_scope', sc, { scope: 'server', type: 'enum' }).then(function () { setTimeout(load, 1200); });
            opts.forEach(function (o) { o.classList.toggle('on', o === b); o.setAttribute('aria-checked', String(o === b)); });
            showPreview(sc); warn.classList.toggle('hidden', sc === 'folders' || (S.auth && S.auth.required));
          } }, h('b', t('scope.' + sc)), h('div', { style: { fontSize: '.84rem', opacity: '.8', whiteSpace: 'normal' } }, t('scope.' + sc + '.d')));
        return b;
      });
      var warn = h('div.banner' + (cur === 'folders' || (S.auth && S.auth.required) ? '.hidden' : ''), { style: { margin: '.6em 0 0' } }, '⚠️ ' + t('scope.warn'));
      showPreview(cur);
      return h('div.card-box', { role: 'radiogroup', 'aria-label': t('scope.title') }, h('b', t('scope.title')), h('div', { style: { marginTop: '.6em' } }, opts), preview, warn);
    }
    function load() {
      api('/libraries').then(function (d) {
        clear(box);
        box.appendChild(scopeCard(d.scope || 'folders'));
        var list = h('div.card-box');
        d.libraries.forEach(function (l) {
          var en = !!l.enabled;
          var tg = h('button.toggle', { role: 'switch', 'aria-checked': String(en), 'aria-label': 'Enabled', disabled: !S.admin, onclick: function () { en = !en; tg.setAttribute('aria-checked', String(en)); api('/libraries/' + l.id, { method: 'PATCH', body: { enabled: en } }); } });
          var typ = h('select.input', { style: { minWidth: '0', width: 'auto' }, disabled: !S.admin, onchange: function () { api('/libraries/' + l.id, { method: 'PATCH', body: { type: this.value } }).then(function () { toast(t('toast.scan')); }); } },
            [['mixed', 'Mixed'], ['movies', 'Movies'], ['shows', 'TV Shows']].map(function (o) { return h('option', { value: o[0], selected: o[0] === l.type }, o[1]); }));
          var auto = l.source === 'auto';
          var meta = [t('set.items', fmtNum(l.n))];
          if (l.offline || !l.exists) meta.push('⚠ ' + t('lib.offline'));
          else if (!l.readable) meta.push('⚠ not readable');
          if (l.nestedIn) meta.push(t('lib.inside', l.nestedIn));
          list.appendChild(h('div.lib' + (l.enabled ? '' : '.dis'), { style: { opacity: l.enabled ? '1' : '.55' } }, icon('folder'),
            h('div.grow', h('b', l.name && auto ? l.name : l.path, ' ', h('span.tag', auto ? t('lib.auto') : t('lib.custom'))), h('small.muted', (auto ? l.path + ' · ' : '') + meta.join(' · '))),
            typ, h('span', { style: { width: '10px' } }), tg,
            S.admin ? h('button.btn.btn-icon.btn-ghost', { 'aria-label': auto ? t('lib.hide') : t('b.delete'), title: auto ? t('lib.hide') : t('b.delete'), style: { marginLeft: '8px' }, onclick: function () { if (confirm((auto ? t('lib.hide') : t('b.delete')) + ' ' + l.path + '?')) api('/libraries/' + l.id, { method: 'DELETE' }).then(load); } }, icon('trash')) : null));
        });
        var st = d.scan || {};
        box.appendChild(list);
        box.appendChild(h('div', S.admin ? h('button.btn.btn-accent', { onclick: function () { folderPicker(function (p) { api('/libraries', { method: 'POST', body: { path: p } }).then(function () { toast(t('toast.scan'), 'ok'); load(); }).catch(function (e) { toast(e.message, 'err'); }); }); } }, icon('plus'), t('set.addfolder')) : null,
          h('button.btn.btn-ghost', { onclick: function () { api('/library/scan', { method: 'POST', body: {} }).then(function () { toast(t('toast.scan')); }); } }, icon('refresh'), t('b.rescan')),
          h('button.btn.btn-ghost', { onclick: function () { api('/library/scan', { method: 'POST', body: { full: true } }).then(function () { toast(t('toast.scan')); }); } }, t('b.fullscan'))));
        box.appendChild(h('div.card-box#scanstatus', scanStatusText(st)));
      });
    }
    load(); box._reload = load; return box;
  } },
  network: { before: function () {
    var box = h('div');
    var urls = (S.info && S.info.urls) || [];
    box.appendChild(h('div.card-box', h('b', t('set.urls')), urls.map(function (u) { return h('div', h('code', u)); })));
    if (S.info && S.info.openSecurity) box.appendChild(h('div.banner', { style: { margin: '0 0 1em' } }, '⚠️ ' + t('set.open')));
    if (!S.admin) return box;
    var pw = h('input.input', { type: 'password', placeholder: t('set.password'), autocomplete: 'new-password' });
    var pin = h('input.input', { type: 'password', inputmode: 'numeric', placeholder: t('set.adminpin'), autocomplete: 'off' });
    box.appendChild(h('div.card-box',
      h('div.field', h('div.lbl', h('b', t('set.password')), h('small', S.auth && S.auth.hasPassword ? '✓ set' : '—')), h('div.ctl', pw,
        h('div', { style: { marginTop: '.5em' } }, h('button.btn.btn-sm.btn-primary', { onclick: function () { api('/auth/password', { method: 'POST', body: { password: pw.value } }).then(function () { toast(t('set.saved'), 'ok'); pw.value = ''; refreshAuth(); }).catch(function (e) { toast(e.message, 'err'); }); } }, t('set.setpw')),
          h('button.btn.btn-sm.btn-danger', { onclick: function () { api('/auth/password', { method: 'POST', body: { password: '' } }).then(function () { toast(t('set.saved'), 'ok'); refreshAuth(); }); } }, t('set.clearpw'))))),
      h('div.field', h('div.lbl', h('b', t('set.adminpin')), h('small', S.auth && S.auth.hasAdminPin ? '✓ set' : '—')), h('div.ctl', pin,
        h('div', { style: { marginTop: '.5em' } }, h('button.btn.btn-sm.btn-primary', { onclick: function () { api('/auth/password', { method: 'POST', body: { adminPin: pin.value } }).then(function () { toast(t('set.saved'), 'ok'); pin.value = ''; refreshAuth(); }).catch(function (e) { toast(e.message, 'err'); }); } }, t('set.setpin')))))));
    return box;
  } },
  transcoding: { before: function () {
    var info = (S.info && S.info.ffmpeg) || {};
    var res = info.hwResults || {};
    var rows = Object.keys(res).map(function (k) { return h('div', (res[k] ? '✅ ' : '⚠️ ') + k); });
    var box = h('div.card-box', h('b', 'ffmpeg ' + (info.version || '—')), h('div.muted', info.path || ''),
      h('p', h('b', t('set.hw') + ': '), info.hw || 'software'), rows.length ? h('div.muted', rows) : null,
      S.admin ? h('div', { style: { marginTop: '.8em' } },
        h('button.btn.btn-sm.btn-ghost', { onclick: function () { var b = this; b.disabled = true; api('/system/hw-detect', { method: 'POST' }).then(function () { return refreshInfo(); }).then(function () { S.pageKey = ''; route(); }).catch(function (e) { toast(e.message, 'err'); b.disabled = false; }); } }, icon('refresh'), t('set.redetect')),
        h('button.btn.btn-sm.btn-ghost', { onclick: function () { api('/cache/clear', { method: 'POST', body: { kinds: ['hls', 'subs', 'trickplay'] } }).then(function () { toast(t('set.saved'), 'ok'); }); } }, icon('trash'), t('set.clearcache'))) : null);
    return box;
  } },
  metadata: { after: function () {
    return h('div.card-box', S.admin ? h('button.btn.btn-sm.btn-ghost', { onclick: function () { api('/metadata/refresh', { method: 'POST' }).then(function () { toast(t('set.saved'), 'ok'); }).catch(function (e) { toast(e.message, 'err'); }); } }, icon('refresh'), t('set.refreshmeta')) : null,
      S.admin ? h('button.btn.btn-sm.btn-ghost', { onclick: function () { api('/cache/clear', { method: 'POST', body: { kinds: ['images'] } }).then(function () { toast(t('set.saved'), 'ok'); }); } }, icon('trash'), t('set.clearcache')) : null,
      h('p.muted', { style: { fontSize: '.82rem' } }, 'This product uses the TMDB API but is not endorsed or certified by TMDB.'));
  } },
  profiles: { after: function () {
    var box = h('div.card-box');
    function load() {
      api('/profiles').then(function (d) {
        clear(box);
        d.profiles.forEach(function (p) {
          box.appendChild(h('div.lib', avatar(p.avatar), h('div.grow', h('b', p.name), h('small.muted', [p.kids ? t('who.kids') : '', p.hasPin ? 'PIN' : ''].filter(Boolean).join(' · '))),
            h('button.btn.btn-sm.btn-ghost', { onclick: function () { editProfile(p, load); } }, icon('edit'), t('b.edit'))));
        });
        if (S.admin) box.appendChild(h('button.btn.btn-sm.btn-accent', { style: { marginTop: '.8em' }, onclick: function () { editProfile(null, load); } }, icon('plus'), t('who.add')));
      });
    }
    load(); return box;
  } },
  advanced: { after: function () {
    var out = h('div');
    var doc = h('div');
    var logs = h('pre.logs.hidden');
    var imp = h('input', { type: 'file', accept: 'application/json', style: { display: 'none' }, onchange: function () {
      var f = this.files[0]; if (!f) return;
      var rd = new FileReader(); rd.onload = function () { try { api('/settings/import', { method: 'POST', body: JSON.parse(rd.result) }).then(function (d) { toast(t('set.saved') + ' (' + d.imported + ')', 'ok'); loadSettings(); }); } catch (e) { toast(e.message, 'err'); } }; rd.readAsText(f);
    } });
    append(out, [
      S.admin ? h('div.card-box', h('b', t('set.doctor')), h('div', { style: { margin: '.6em 0' } },
        h('button.btn.btn-sm.btn-primary', { onclick: function () {
          clear(doc); doc.appendChild(h('div.skel', { style: { height: '120px', borderRadius: '8px' } }));
          api('/system/doctor').then(function (d) {
            clear(doc);
            d.checks.forEach(function (c) { doc.appendChild(h('div.check', h('span.s', c.status === 'ok' ? '✅' : c.status === 'warn' ? '⚠️' : '❌'), h('div', h('b', c.name), ' — ', c.detail, c.fix && c.status !== 'ok' ? h('div.fix', '→ ' + c.fix) : null))); });
          }).catch(function (e) { clear(doc); doc.appendChild(h('p', e.message)); });
        } }, t('set.rundoctor')),
        h('button.btn.btn-sm.btn-ghost', { onclick: function () { api('/system/logs').then(function (d) { logs.textContent = d.lines.join('\n') || '(empty)'; logs.classList.remove('hidden'); logs.scrollTop = logs.scrollHeight; }); } }, t('set.logs')),
        h('button.btn.btn-sm.btn-ghost', { onclick: function () { if (confirm(t('set.restartnow') + '?')) api('/system/restart', { method: 'POST' }).then(function () { toast(t('set.restartnow') + '…'); setTimeout(function () { location.reload(); }, 4000); }); } }, icon('refresh'), t('set.restartnow'))), doc, logs) : null,
      S.admin ? h('div.card-box', h('a.btn.btn-sm.btn-ghost', { href: '/api/v1/settings/export', download: 'localflix-settings.json' }, t('set.export')),
        h('button.btn.btn-sm.btn-ghost', { onclick: function () { imp.click(); } }, t('set.import')), imp,
        h('button.btn.btn-sm.btn-danger', { onclick: function () { if (confirm(t('set.reset') + '?')) api('/settings/reset', { method: 'POST' }).then(function () { return loadSettings(); }).then(function () { S.pageKey = ''; route(); }); } }, t('set.reset'))) : null,
      h('div.card-box', h('b', t('set.about')), h('p.muted', 'LocalFlix v' + BOOT.version + ' · hls.js ' + BOOT.hlsjs + ' · ' + ((S.info && S.info.platform) || '') + ' · Python ' + ((S.info && S.info.python) || '')),
        h('p.muted', { style: { fontSize: '.85rem' } }, t('set.licenses') + ': hls.js © Dailymotion & contributors (Apache-2.0) · FFmpeg (LGPL/GPL, used as an external program) · TMDB metadata (optional) — "This product uses the TMDB API but is not endorsed or certified by TMDB."'))]);
    return out;
  } }
};
function scanStatusText(st) {
  if (!st) return '';
  var parts = [h('b', t('set.scanstatus') + ': ')];
  if (st.state === 'scanning') parts.push(t('set.scanning', fmtNum(st.found || 0)));
  else parts.push(t('set.lastscan') + ' ' + (st.last_scan ? fmtDate(st.last_scan) : t('set.never')) + (st.duration ? ' (' + st.duration + 's)' : '') + (st.queue ? ' · probing ' + st.queue : ''));
  if (st.errors && st.errors.length) parts.push(h('div', { style: { color: 'var(--warn)', marginTop: '.4em' } }, st.errors.slice(0, 5).join(' · ')));
  return parts;
}
function folderPicker(onPick) {
  var pathIn = h('input.input', { placeholder: t('set.folderpath'), style: { width: '100%' } });
  var list = h('div.fsb');
  var cur = '';
  function load(p) {
    api('/fs/browse?path=' + encodeURIComponent(p || '')).then(function (d) {
      cur = d.path; pathIn.value = d.path; clear(list);
      if (d.parent !== null && d.path) list.appendChild(h('button', { onclick: function () { load(d.parent); } }, icon('back'), '..'));
      d.dirs.forEach(function (x) { list.appendChild(h('button', { onclick: function () { load(x.path); } }, icon('folder'), x.name)); });
      if (d.videos) list.appendChild(h('div.muted', { style: { padding: '.6em .9em' } }, d.videos + ' video file(s) here'));
      Nav.focus($('button', list));
    }).catch(function (e) { toast(e.message, 'err'); });
  }
  var ov = modal(h('div', h('h2', t('set.addfolder')), pathIn, list,
    h('button.btn.btn-primary', { onclick: function () { var p = pathIn.value.trim() || cur; if (p) { ov._close(); onPick(p); } } }, t('b.add')),
    h('button.btn.btn-ghost', { onclick: function () { ov._close(); } }, t('b.cancel'))), { small: true });
  pathIn.addEventListener('keydown', function (e) { if (e.key === 'Enter') load(pathIn.value); });
  load('');
}

/* ===== live events (SSE) ===== */
var es = null, offlineToast = null, offlineTimer = null;
function connectEvents() {
  if (!window.EventSource) return;
  try { es = new EventSource('/api/v1/events'); } catch (e) { return; }
  es.onopen = function () {
    clearTimeout(offlineTimer); offlineTimer = null;
    if (offlineToast) { offlineToast.remove(); offlineToast = null; toast(t('toast.online'), 'ok'); refreshSoon(); }
    S.online = true;
  };
  es.onerror = function () {
    if (offlineTimer || offlineToast) return;
    offlineTimer = setTimeout(function () {
      offlineTimer = null;
      if (es.readyState !== 1 && !offlineToast) { S.online = false; offlineToast = toast(t('toast.offline'), 'err', null, 0); }
    }, 4000);
  };
  es.addEventListener('library.updated', function () { refreshSoon(); });
  es.addEventListener('scan.progress', function (e) {
    var d = {}; try { d = JSON.parse(e.data); } catch (x) { return; }
    if (S.info) S.info.scan = d;
    var el = $('#scanstatus'); if (el) { clear(el); append(el, [scanStatusText(d)]); }
  });
  es.addEventListener('settings.changed', function (e) {
    var d = {}; try { d = JSON.parse(e.data); } catch (x) { return; }
    loadSettings().then(function () { if (d.keys && (d.keys.indexOf('language') >= 0)) { renderNav(); } });
  });
  es.addEventListener('progress.updated', function (e) {
    var d = {}; try { d = JSON.parse(e.data); } catch (x) { return; }
    if (S.auth && S.auth.profile && d.profileId === S.auth.profile.id) refreshSoon();
  });
  es.addEventListener('job.progress', function (e) { if (LF.Player) { try { LF.Player.onJob(JSON.parse(e.data)); } catch (x) { /* ignore */ } } });
  es.addEventListener('trickplay.ready', function (e) { if (LF.Player) { try { LF.Player.onTrick(JSON.parse(e.data)); } catch (x) { /* ignore */ } } });
  es.addEventListener('artwork.updated', function (e) {
    var d = {}; try { d = JSON.parse(e.data); } catch (x) { return; }
    [d.id, d.seriesId].forEach(function (id) {
      if (!id) return;
      $$('[data-id="' + id + '"] .ratio').forEach(function (r) {
        if ($('img.ok', r)) return;
        var old = $('img', r); if (old) old.parentNode.removeChild(old);
        var c = r.parentNode._item; if (c) r.insertBefore(lazyImg(c.thumb + '?w=480&r=' + Date.now(), ''), r.firstChild);
      });
    });
  });
}
function refreshInfo() { return api('/system/info').then(function (d) { S.info = d; return d; }); }
function refreshAuth() { return api('/auth/status').then(function (d) { S.auth = d; S.admin = d.admin; return d; }); }

/* ===== boot ===== */
function boot() {
  registerTvKeys();
  if (S.tv) document.documentElement.setAttribute('data-tv', '1');
  Promise.all([refreshInfo(), refreshAuth()]).then(function () {
    if (S.auth.required && !S.auth.authenticated) { buildShell(); window.addEventListener('hashchange', route); route(); return null; }
    return loadSettings().then(function () {
      if (S.info.profilesEnabled === false && !S.auth.profile) return api('/profiles').then(function (d) { return api('/profiles/' + d.profiles[0].id + '/select', { method: 'POST', body: {} }); }).then(function (p) { S.auth.profile = p; })
        .catch(function () { /* PIN-protected: the profiles page shows the PIN pad */ });
      return null;
    }).then(function () {
      buildShell();
      window.addEventListener('hashchange', route);
      connectEvents();
      if (!location.hash || location.hash === '#' || location.hash === '#/') go('home', true);
      route();
    });
  }).catch(function (e) {
    var app = $('#app'); clear(app);
    app.appendChild(h('div.empty', h('div.art', '📡'), h('h2', t('toast.offline')), h('p.muted', e.message), h('button.btn.btn-primary', { onclick: function () { location.reload(); } }, t('b.retry'))));
  });
  if (window.matchMedia) {
    var mq = matchMedia('(prefers-color-scheme: light)');
    var onMq = function () { if (S.settings.theme === 'auto') applySettings(); };
    if (mq.addEventListener) mq.addEventListener('change', onMq); else if (mq.addListener) mq.addListener(onMq);
  }
}
LF.ready = boot;
LF.fmtTime = fmtTime; LF.fmtDur = fmtDur; LF.clamp = clamp; LF.debounce = debounce; LF.store = store; LF.$ = $; LF.$$ = $$; LF.clear = clear; LF.append = append;
LF.closeOverlays = closeOverlays; LF.hashColor = hashColor; LF.lazyImg = lazyImg; LF.qs = qs;
"""


APP_JS += r"""
/* ===== player ===== */
(function () {
var h = LF.h, icon = LF.icon, t = LF.t, api = LF.api, toast = LF.toast, S = LF.S, $ = LF.$, $$ = LF.$$, clamp = LF.clamp, fmtTime = LF.fmtTime, store = LF.store;
var P = null, hlsLoading = null;

function loadHls() {
  if (window.Hls) return Promise.resolve(window.Hls);
  if (hlsLoading) return hlsLoading;
  hlsLoading = new Promise(function (res) {
    var s = document.createElement('script'); s.src = '/vendor/hls.min.js'; s.async = true;
    s.onload = function () { res(window.Hls || null); };
    s.onerror = function () { hlsLoading = null; res(null); };
    document.head.appendChild(s);
  });
  return hlsLoading;
}
function detectCaps(video) {
  var MS = window.ManagedMediaSource || window.MediaSource || window.WebKitMediaSource;
  function mse(x) { try { return !!(MS && MS.isTypeSupported && MS.isTypeSupported(x)); } catch (e) { return false; } }
  function can(x) { try { return video.canPlayType(x) !== ''; } catch (e) { return false; } }
  function ok(x) { return mse(x) || can(x); }
  var v = [], a = [];
  if (ok('video/mp4; codecs="avc1.640028"') || ok('video/mp4; codecs="avc1.42E01E"')) v.push('h264');
  if (ok('video/mp4; codecs="hvc1.1.6.L120.90"') || ok('video/mp4; codecs="hev1.1.6.L120.90"')) v.push('hevc');
  if (ok('video/mp4; codecs="hvc1.2.4.L120.90"') || ok('video/mp4; codecs="hev1.2.4.L120.90"')) v.push('hevc10');
  if (ok('video/mp4; codecs="av01.0.08M.08"')) v.push('av1');
  if (ok('video/webm; codecs="vp9"') || ok('video/mp4; codecs="vp09.00.10.08"')) v.push('vp9');
  if (ok('video/webm; codecs="vp8"')) v.push('vp8');
  if (ok('audio/mp4; codecs="mp4a.40.2"')) a.push('aac');
  if (ok('audio/mpeg') || ok('audio/mp4; codecs="mp4a.40.34"')) a.push('mp3');
  if (ok('audio/webm; codecs="opus"') || ok('audio/mp4; codecs="opus"')) a.push('opus');
  if (ok('audio/webm; codecs="vorbis"')) a.push('vorbis');
  if (ok('audio/mp4; codecs="ac-3"')) a.push('ac3');
  if (ok('audio/mp4; codecs="ec-3"')) a.push('eac3');
  if (ok('audio/mp4; codecs="fLaC"') || ok('audio/flac')) a.push('flac');
  if (!v.length) v.push('h264');
  if (!a.length) a.push('aac');
  var containers = ['mp4']; if (can('video/webm; codecs="vp9"') || can('video/webm')) containers.push('webm');
  var hdr = []; try { if (matchMedia('(dynamic-range: high)').matches && (v.indexOf('hevc10') >= 0 || v.indexOf('av1') >= 0)) hdr.push('hdr10'); } catch (e) { /* old browser */ }
  return { profile: S.tv ? 'tv' : (/Mobi|Android|iPhone|iPad/.test(navigator.userAgent) ? 'mobile' : 'desktop'), mse: !!MS,
    nativeHls: can('application/vnd.apple.mpegurl'), video: v, audio: a, containers: containers, hdr: hdr, audioTracks: !!(video.audioTracks && /Safari/.test(navigator.userAgent) && !/Chrome|Android/.test(navigator.userAgent)), maxBitrateKbps: null };
}
LF.detectCaps = detectCaps;

/* --- subtitles --- */
function toSec(hh, mm, ss, ms) { return (hh ? parseInt(hh, 10) * 3600 : 0) + parseInt(mm, 10) * 60 + parseInt(ss, 10) + parseInt((ms + '00').slice(0, 3), 10) / 1000; }
function parseVTT(txt) {
  var cues = [];
  txt.replace(/\r/g, '').split(/\n\n+/).forEach(function (b) {
    var lines = b.split('\n'), i = 0;
    while (i < lines.length && lines[i].indexOf('-->') < 0) i++;
    if (i >= lines.length) return;
    var m = lines[i].match(/(?:(\d+):)?(\d+):(\d+)[.,](\d+)\s*-->\s*(?:(\d+):)?(\d+):(\d+)[.,](\d+)/);
    if (!m) return;
    var body = lines.slice(i + 1).join('\n');
    if (body.trim()) cues.push({ s: toSec(m[1], m[2], m[3], m[4]), e: toSec(m[5], m[6], m[7], m[8]), t: body });
  });
  cues.sort(function (x, y) { return x.s - y.s; });
  return cues;
}
function cueNode(text) {
  var root = h('span.cue'), stack = [root], re = /<(\/?)(i|b|u|c)((?:\.[\w]+)*)>/g, last = 0, m;
  function txt(s) { if (s) stack[stack.length - 1].appendChild(document.createTextNode(s.replace(/&amp;/g, '&').replace(/&lt;/g, '<').replace(/&gt;/g, '>').replace(/&nbsp;/g, ' '))); }
  text = text.replace(/<\d+:\d+[:.\d]*>/g, '').replace(/<(?!\/?(?:i|b|u|c)[.>])[^>]*>/g, '');
  while ((m = re.exec(text))) {
    txt(text.slice(last, m.index)); last = re.lastIndex;
    if (m[1]) { if (stack.length > 1) stack.pop(); continue; }
    var tag = m[2] === 'c' ? 'span' : m[2], el = document.createElement(tag);
    var cm = /\.c([0-9A-Fa-f]{6})/.exec(m[3] || ''); if (cm) el.style.color = '#' + cm[1];
    stack[stack.length - 1].appendChild(el); stack.push(el);
  }
  txt(text.slice(last));
  return root;
}

/* --- player core --- */
function pos() {
  if (!P) return 0;
  if (P.pendingPos !== null && P.pendingPos !== undefined) return P.pendingPos;
  return (P.offset || 0) + (P.video.currentTime || 0);
}
function dur() { return P ? (P.pb && P.pb.durationSec) || P.video.duration || 0 : 0; }
function isSimple() { return P && P.pb && P.pb.mode === 'simple'; }
function settings() { return S.settings || {}; }

function build(id) {
  var video = h('video', { playsinline: true, 'webkit-playsinline': true, preload: 'auto', 'x-webkit-airplay': 'allow', crossorigin: null });
  var root = h('div.player', { role: 'region', 'aria-label': 'Video player' });
  var subs = h('div.subs');
  var bright = h('div.bright');
  var gest = h('div', { style: { position: 'absolute', top: '0', left: '0', right: '0', bottom: '0' } });
  var center = h('div.p-center');
  var title = h('div.ttl', h('b', ''), h('small', ''));
  var btn = function (ic, label, fn, cls) { return h('button.p-btn' + (cls ? '.' + cls : ''), { 'aria-label': label, title: label, onclick: function (e) { e.stopPropagation(); fn(e); } }, icon(ic)); };
  var top = h('div.p-top', btn('back', t('b.back'), function () { close(); }), title);
  var trk = h('div.trk', h('div.buf'), h('div.pl'), h('div.knob'));
  var preview = h('div.preview', h('div.img'), h('div.tm'), h('div.cn'));
  var seek = h('div.seek', { tabindex: '0', role: 'slider', 'aria-label': 'Seek', 'aria-valuemin': '0' }, trk, preview);
  var time = h('span.p-time', { role: 'timer', onclick: function () { P.remaining = !P.remaining; store('remaining', P.remaining); tick(); } }, '0:00');
  var volIn = h('input', { type: 'range', min: '0', max: '1', step: '0.05', 'aria-label': 'Volume', oninput: function () { video.volume = +this.value; video.muted = +this.value === 0; saveVol(); } });
  var b = {
    play: btn('play', t('b.play'), togglePlay), rew: btn('rew', '-10s', function () { seekBy(-10); }), fwd: btn('fwd', '+10s', function () { seekBy(10); }),
    vol: btn('vol', 'Mute', function () { video.muted = !video.muted; saveVol(); }), next: btn('next', t('p.next'), playNext, 'p-opt'),
    subs: btn('cc', t('p.subs'), function () { menu('subs'); }), audio: btn('audio', t('p.audio'), function () { menu('audio'); }),
    speed: btn('speed', t('p.speed'), function () { menu('speed'); }, 'p-opt'), quality: btn('hd', t('p.quality'), function () { menu('quality'); }, 'p-opt'),
    eps: btn('episodes', t('p.episodes'), function () { menu('episodes'); }), chap: btn('chapters', t('p.chapters'), function () { menu('chapters'); }, 'p-opt'),
    pip: btn('pip', 'Picture in picture', togglePip, 'p-opt'), air: btn('airplay', 'AirPlay', function () { video.webkitShowPlaybackTargetPicker(); }),
    cast: btn('cast', 'Cast', function () { video.remote.prompt().catch(function () { /* cancelled */ }); }),
    gear: btn('gear', t('nav.settings'), function () { menu('settings'); }), fs: btn('fs', 'Fullscreen', toggleFs)
  };
  b.air.classList.add('hidden'); b.cast.classList.add('hidden'); b.next.classList.add('hidden'); b.eps.classList.add('hidden'); b.chap.classList.add('hidden');
  if (!document.pictureInPictureEnabled || video.disablePictureInPicture) b.pip.classList.add('hidden');
  b.rew.classList.add('p-seekb'); b.fwd.classList.add('p-seekb'); b.air.classList.add('p-opt'); b.cast.classList.add('p-opt');
  var row = h('div.p-row', b.play, b.rew, b.fwd, h('div.p-vol.p-opt', b.vol, volIn), time, h('div.p-spacer'), b.next, b.eps, b.chap, b.audio, b.subs, b.speed, b.quality, b.pip, b.air, b.cast, b.gear, b.fs);
  var bot = h('div.p-bot', seek, row);
  var ui = h('div.p-ui', top, bot);
  LF.append(root, [video, bright, subs, gest, center, ui]);
  return { root: root, video: video, subs: subs, bright: bright, gest: gest, center: center, title: title, ui: ui, seek: seek, trk: trk, preview: preview, time: time, volIn: volIn, b: b, row: row };
}

function preload() { if (window.MediaSource || window.ManagedMediaSource) loadHls(); }
function open(id, q) {
  if (P && P.id === id && !(q && q.t !== undefined)) return;
  if (P) teardown(true, true);  /* next/previous episode: keep fullscreen + orientation */
  q = q || {};
  var els = build(id);
  P = { id: id, els: els, video: els.video, root: els.root, offset: 0, pb: null, hls: null, cues: [], subIndex: null, audioIndex: null, quality: null,
    fallback: 0, subDelay: settings().sub_delay_ms || 0, remaining: !!store('remaining'), ended: false, menu: null, nextShown: false, countdown: null,
    sleepAt: null, sleepEnd: false, stats: !!settings().stats_overlay, fit: store('fit') || 'contain', netRetries: 0, mediaRetries: 0, startPos: 0,
    jobPct: null, waitingSince: 0, lastCueKey: '', focusBefore: document.activeElement, speedHold: null };
  document.body.appendChild(els.root);
  document.body.style.overflow = 'hidden';
  document.documentElement.style.overflow = 'hidden';
  LF.applySubStyle(els.subs, settings());
  setFit(P.fit);
  wire();
  spinner(t('p.preparing'));
  var vol = store('vol');
  if (settings().remember_volume !== false && vol !== null) { P.video.volume = vol.v; P.video.muted = !!vol.m; }
  els.volIn.value = P.video.muted ? 0 : P.video.volume;
  P.video.playbackRate = settings().default_speed || 1;
  var myP = P;
  api('/items/' + id).then(function (item) {
    if (P !== myP) return;
    P.item = item;
    setTitle(item);
    var start = 0;
    if (q.t !== undefined && q.t !== '') start = +q.t || 0;
    else {
      var resumable = item.position > 15 && !item.watched && item.position < (item.duration || 1e9) - 60;
      var mode = settings().resume_mode || 'ask';
      if (resumable && mode === 'auto') start = item.position;
      else if (resumable && mode === 'ask') { askResume(item.position); return; }
    }
    begin(start);
  }).catch(function (e) { showError(e.message); });
}
function askResume(p) {
  hideSpinner();
  var d = h('div.p-dialog', h('h3', t('p.resume', fmtTime(p))),
    h('button.btn.btn-primary', { 'data-autofocus': true, onclick: function () { d.remove(); begin(p); } }, icon('play'), t('b.resume')),
    h('button.btn.btn-ghost', { onclick: function () { d.remove(); begin(0); } }, t('p.fromstart')));
  P.root.appendChild(d);
  setTimeout(function () { LF.Nav.focus($('[data-autofocus]', d)); }, 30);
}
function begin(start) {
  P.startPos = start;
  negotiate({ startTime: start }).then(function (pb) { if (pb) load(pb, start); }).catch(function (e) { showError(e.message, e.code === 'unplayable'); });
}
function negotiate(o) {
  o = o || {};
  var caps = detectCaps(P.video);
  var body = {};
  Object.keys(caps).forEach(function (k) { body[k] = caps[k]; });
  body.startTime = o.startTime || 0;
  if (o.audioIndex !== undefined) body.audioIndex = o.audioIndex; else if (P.audioIndex !== null) body.audioIndex = P.audioIndex;
  if (o.subtitleIndex !== undefined) body.subtitleIndex = o.subtitleIndex;
  else if (P.burnIdx !== undefined && P.burnIdx !== null) body.subtitleIndex = P.burnIdx;
  else if (P.pb) body.subtitleIndex = null;
  else body.subtitleIndex = 'auto';
  if (o.force) body.force = o.force; else if (P.force) body.force = P.force;
  body.quality = o.quality || P.quality || null;
  if (P.pb && P.pb.sessionId) body.replaceSession = P.pb.sessionId;
  var myP = P, seq = P.negSeq = (P.negSeq || 0) + 1;
  return api('/items/' + P.id + '/playback', { method: 'POST', body: body }).then(function (pb) {
    if (P !== myP || seq !== myP.negSeq) {
      if (pb && pb.sessionId) api('/hls/' + pb.sessionId, { method: 'DELETE' }).catch(function () { /* gone */ });
      return null;
    }
    if (body.force) P.force = body.force;
    return pb;
  });
}
function destroyHls() { if (P && P.hls) { try { P.hls.destroy(); } catch (e) { /* ignore */ } P.hls = null; } }
function load(pb, start) {
  var first = !P.pb;
  P.pb = pb; P.offset = pb.timeOffset || 0; P.ended = false; P.netRetries = 0; P.pendingPos = start || 0;
  P.loadAt = Date.now(); P.everPlayed = false;
  if (P.audioIndex === null || P.audioIndex === undefined) P.audioIndex = pb.audioIndex;
  if (pb.item) setTitle(pb.item);
  var selSub = pb.subtitleIndex === null || pb.subtitleIndex === undefined ? null : pb.subtitles.filter(function (x) { return x.index === pb.subtitleIndex; })[0];
  P.burnIdx = (pb.method === 'transcode' && selSub && selSub.kind === 'image') ? pb.subtitleIndex : null;
  if (P.burnIdx !== null) P.subIndex = P.burnIdx;
  var v = P.video;
  destroyHls();
  if (P.seekFn) { v.removeEventListener('loadedmetadata', P.seekFn); P.seekFn = null; }
  P.loading = true;  // suppress 'paused' reports caused by swapping the source
  v.removeAttribute('src'); try { v.load(); } catch (e) { /* ignore */ }
  var localStart = isSimple() ? 0 : start;
  updateButtons();
  if (first && selSub && selSub.kind === 'text') loadSubs(pb.subtitleIndex);
  if (pb.method === 'direct') {
    v.src = pb.url;
    seekWhenReady(localStart);
    play();
    return;
  }
  spinner(t('p.preparing'));
  loadHls().then(function (Hls) {
    if (!P || P.pb !== pb) return;
    var nativeHls = v.canPlayType('application/vnd.apple.mpegurl') !== '';
    var preferNative = nativeHls && !(Hls && Hls.isSupported()) || (nativeHls && /iPhone|iPad|iPod/.test(navigator.userAgent) && !window.ManagedMediaSource);
    if (Hls && Hls.isSupported() && !preferNative) {
      var hls = new Hls({
        enableWorker: true, startFragPrefetch: true, backBufferLength: 30, maxBufferLength: 40, maxMaxBufferLength: 90, startPosition: localStart,
        capLevelToPlayerSize: false, abrEwmaDefaultEstimate: 5e6, preferManagedMediaSource: true, enableWebVTT: false, enableIMSC1: false,
        enableCEA708Captions: false, renderTextTracksNatively: false, fragLoadPolicy: { default: { maxTimeToFirstByteMs: 25000, maxLoadTimeMs: 60000,
          timeoutRetry: { maxNumRetry: 4, retryDelayMs: 0, maxRetryDelayMs: 0 }, errorRetry: { maxNumRetry: 6, retryDelayMs: 800, maxRetryDelayMs: 4000 } } },
        manifestLoadPolicy: { default: { maxTimeToFirstByteMs: 45000, maxLoadTimeMs: 60000, timeoutRetry: { maxNumRetry: 2, retryDelayMs: 0, maxRetryDelayMs: 0 }, errorRetry: { maxNumRetry: 3, retryDelayMs: 1000, maxRetryDelayMs: 4000 } } },
        liveSyncDurationCount: 99999, liveDurationInfinity: false
      });
      P.hls = hls;
      hls.on(Hls.Events.MANIFEST_PARSED, function () { selectHlsAudio(); play(); });
      hls.on(Hls.Events.ERROR, function (ev, data) { onHlsError(Hls, data); });
      hls.on(Hls.Events.FRAG_BUFFERED, function () { P.netRetries = 0; });
      hls.loadSource(pb.url);
      hls.attachMedia(v);
    } else if (nativeHls) {
      v.src = pb.url;
      seekWhenReady(localStart);
      v.addEventListener('loadedmetadata', function once() { v.removeEventListener('loadedmetadata', once); selectNativeAudio(); }, false);
      play();
    } else {
      fallbackOrError(t('p.nohls'));
    }
  });
}
function seekWhenReady(t0) {
  var v = P.video;
  if (P.seekFn) { v.removeEventListener('loadedmetadata', P.seekFn); P.seekFn = null; }
  if (!t0) return;
  var fn = P.seekFn = function () { v.removeEventListener('loadedmetadata', fn); if (P) P.seekFn = null; try { v.currentTime = t0; } catch (e) { /* ignore */ } };
  if (v.readyState >= 1) fn(); else v.addEventListener('loadedmetadata', fn);
}
function play() {
  var pr = P.video.play();
  if (pr && pr.catch) pr.catch(function (e) {
    if (e && e.name === 'NotAllowedError') { hideSpinner(); showBigPlay(); }
  });
}
function showBigPlay() {
  var c = P.els.center; LF.clear(c);
  var bp = h('button.p-bigplay', { 'aria-label': t('b.play'), onclick: function () { LF.clear(c); P.video.play(); } }, icon('play'));
  c.appendChild(bp); setTimeout(function () { LF.Nav.focus(bp); }, 20);
}
function onHlsError(Hls, data) {
  if (!data.fatal) {
    if (data.details === 'bufferStalledError') spinner(t('p.buffering'));
    return;
  }
  var code = data.response && data.response.code;
  if (data.type === Hls.ErrorTypes.NETWORK_ERROR) {
    if (P.netRetries++ < 3 && code !== 404) { setTimeout(function () { if (P && P.hls) P.hls.startLoad(); }, 1000 * P.netRetries); return; }
    if ((P.restarts = (P.restarts || 0) + 1) <= 2) { restart({}, true); return; }
  }
  if (data.type === Hls.ErrorTypes.MEDIA_ERROR) {
    P.mediaRetries++;
    if (P.mediaRetries === 1) { P.hls.recoverMediaError(); return; }
    if (P.mediaRetries === 2) { P.hls.swapAudioCodec(); P.hls.recoverMediaError(); return; }
  }
  fallbackOrError(t('p.error') + (data.details ? ' (' + data.details + ')' : ''));
}
function fallbackOrError(msg) {
  if (!P || !P.pb) return;
  var m = P.pb.method;
  var next = m === 'direct' ? 'remux' : (m === 'remux' ? 'transcode' : null);
  if (next && P.fallback < 2) {
    P.fallback++; P.mediaRetries = 0;
    toast(t('p.compat'));
    restart({ force: next });
    return;
  }
  showError(msg, false);
}
function restart(o, silent) {
  o = o || {};
  var at = o.t !== undefined ? o.t : pos();
  var wasPaused = !silent && P.video.paused && !P.loading && !!P.pb;
  spinner(t('p.preparing'));
  if (o.quality) P.quality = o.quality;
  return negotiate({ startTime: at, audioIndex: o.audioIndex, subtitleIndex: o.subtitleIndex, force: o.force, quality: o.quality }).then(function (pb) {
    if (!pb) return;
    if (o.audioIndex !== undefined) P.audioIndex = o.audioIndex;
    load(pb, at);
    if (wasPaused) { var vv = P.video; vv.addEventListener('playing', function once() { vv.removeEventListener('playing', once); vv.pause(); }); }
  }).catch(function (e) { showError(e.message); });
}
function hlsAudioIdx(sidx) {
  var tr = (P.hls && P.hls.audioTracks) || [];
  for (var i = 0; i < tr.length; i++) { if ((tr[i].name || '').slice(-('#' + (sidx + 1)).length) === '#' + (sidx + 1)) return i; }
  return sidx < tr.length ? sidx : -1;
}
function selectHlsAudio() { if (!P.hls || P.audioIndex === null) return; var i = hlsAudioIdx(P.audioIndex); if (i >= 0 && P.hls.audioTrack !== i) P.hls.audioTrack = i; }
function selectNativeAudio() {
  var at = P.video.audioTracks; if (!at || at.length < 2 || P.audioIndex === null) return;
  for (var i = 0; i < at.length; i++) at[i].enabled = (i === P.audioIndex);
}
function setAudio(sidx) {
  var pb = P.pb; P.menuClose && P.menuClose();
  var prev = P.audioIndex;
  if (sidx === prev) return;
  if (pb.method !== 'direct' && pb.mode === 'ondemand' && P.hls) { P.audioIndex = sidx; selectHlsAudio(); }
  else if (P.video.audioTracks && P.video.audioTracks.length > 1 && (pb.method === 'direct' || pb.mode === 'ondemand')) { P.audioIndex = sidx; selectNativeAudio(); }
  else { restart({ audioIndex: sidx }); }
  P.audioIndex = sidx;
  report('playing');
  var a = pb.audio.filter(function (x) { return x.index === sidx; })[0];
  if (a) osd(a.label);
}
function loadSubs(i) {
  P.subIndex = i; P.cues = []; P.lastCueKey = ''; LF.clear(P.els.subs);
  if (i === null || i === undefined) { updateButtons(); return; }
  var s = P.pb.subtitles.filter(function (x) { return x.index === i; })[0];
  if (!s) return;
  if (s.kind === 'image') {
    if (P.burnIdx === i) return;
    toast(t('p.burn')); P.burnIdx = i; restart({ subtitleIndex: i }); updateButtons(); return;
  }
  if (P.burnIdx !== null && P.burnIdx !== undefined) { P.burnIdx = null; restart({ subtitleIndex: null }); }
  var myP = P;
  fetch(s.url, { credentials: 'same-origin' }).then(function (r) { if (!r.ok) throw new Error('subtitle ' + r.status); return r.text(); })
    .then(function (txt) { if (P !== myP || P.subIndex !== i) return; P.cues = parseVTT(txt); renderSubs(true); })
    .catch(function (e) { toast(e.message, 'err'); });
  updateButtons();
}
function setSubs(i) {
  P.menuClose && P.menuClose();
  if (i === null && P.burnIdx !== null && P.burnIdx !== undefined) { P.burnIdx = null; P.subIndex = null; LF.clear(P.els.subs); restart({ subtitleIndex: null }); report('playing'); updateButtons(); return; }
  loadSubs(i);
  report('playing');
}
function renderSubs(force) {
  if (!P || !P.cues.length) { if (P && P.lastCueKey) { LF.clear(P.els.subs); P.lastCueKey = ''; } return; }
  var tt = pos() - (P.subDelay || 0) / 1000, act = [], key = '';
  for (var i = 0; i < P.cues.length; i++) { var c = P.cues[i]; if (c.s > tt) break; if (c.e > tt) { act.push(c); key += i + ','; } }
  if (key === P.lastCueKey && !force) return;
  P.lastCueKey = key;
  LF.clear(P.els.subs);
  act.forEach(function (c) { P.els.subs.appendChild(cueNode(c.t)); P.els.subs.appendChild(document.createElement('br')); });
}

/* --- transport --- */
function togglePlay() { if (!P) return; if (P.video.paused || P.video.ended) { LF.clear(P.els.center); play(); } else P.video.pause(); showUi(); }
function seekTo(target) {
  if (!P) return;
  var d = dur(); target = clamp(target, 0, Math.max(0, d - 0.5));
  if (isSimple()) {
    var v = P.video, endAbs = P.offset + (v.seekable && v.seekable.length ? v.seekable.end(v.seekable.length - 1) : 0);
    if (target < P.offset - 0.5 || target > endAbs - 2) { restart({ t: target }); return; }
    v.currentTime = target - P.offset;
  } else {
    if (P.pendingPos !== null && P.pendingPos !== undefined) P.pendingPos = target;
    P.video.currentTime = target;
  }
  renderSubs(true); tick();
}
function seekBy(d) { seekTo(pos() + d); osd((d > 0 ? '+' : '') + d + 's'); }
function osd(text) {
  if (!P) return;
  var o = $('.osd', P.root); if (o) o.remove();
  o = h('div.osd', text); P.root.appendChild(o);
  clearTimeout(P.osdT); P.osdT = setTimeout(function () { o.remove(); }, 1200);
}
function saveVol() { if (settings().remember_volume !== false) store('vol', { v: P.video.volume, m: P.video.muted }); updateButtons(); }
function toggleFs() {
  var d = document, el = document.documentElement;  /* the player is a fixed full-viewport layer: it fills the screen */
  var fsEl = d.fullscreenElement || d.webkitFullscreenElement;
  if (fsEl) { (d.exitFullscreen || d.webkitExitFullscreen).call(d); return; }
  var req = el.requestFullscreen || el.webkitRequestFullscreen;
  if (req) {
    var pr; try { pr = req.call(el, { navigationUI: 'hide' }); } catch (e) { pr = req.call(el); }
    if (pr && pr.then) pr.then(lockOrientation).catch(function () { /* denied (e.g. not from a user gesture) */ }); else lockOrientation();
  } else nativeFs();
}
function nativeFs() {
  /* iPhone Safari: only the <video> element can go fullscreen; hand it our subtitle track so captions stay */
  var v = P.video;
  if (!v.webkitEnterFullscreen) return;
  $$('track', v).forEach(function (tr) { v.removeChild(tr); });
  var s = P.subIndex !== null && P.subIndex !== undefined && P.pb ? P.pb.subtitles.filter(function (x) { return x.index === P.subIndex && x.kind === 'text'; })[0] : null;
  if (s) {
    var tr = document.createElement('track'); tr.kind = 'subtitles'; tr.src = s.url; tr.srclang = s.lang || 'und'; tr.label = s.label; tr['default'] = true;
    v.appendChild(tr);
  }
  try { v.webkitEnterFullscreen(); } catch (e) { /* not allowed */ }
}
function lockOrientation() { try { if (screen.orientation && screen.orientation.lock && /Mobi|Android/.test(navigator.userAgent)) screen.orientation.lock('landscape').catch(function () { /* not allowed */ }); } catch (e) { /* ignore */ } }
function togglePip() {
  if (document.pictureInPictureElement) document.exitPictureInPicture(); else if (P.video.requestPictureInPicture) P.video.requestPictureInPicture().catch(function (e) { toast(e.message, 'err'); });
}
function setFit(f) {
  P.fit = f; store('fit', f);
  P.root.classList.remove('fit-cover', 'fit-fill', 'fit-crop');
  if (f !== 'contain') P.root.classList.add('fit-' + f);
}
function playNext() {
  var n = P && P.pb && P.pb.next; if (!n) return;
  P.ended = true;
  report('ended');
  LF.go('watch/' + n.id, true);
}
function playPrev() { var p = P && P.pb && P.pb.prevId; if (p) { report('stopped'); LF.go('watch/' + p, true); } }

/* --- UI state --- */
function spinner(msg) {
  if (!P) return;
  var c = P.els.center; LF.clear(c);
  c.appendChild(h('div.spinner')); c.appendChild(h('div.msg', msg || ''));
  P.spinMsg = msg;
}
function hideSpinner() { if (P && $('.spinner', P.els.center)) LF.clear(P.els.center); }
function setTitle(it) {
  var b = $('b', P.els.title), s = $('small', P.els.title);
  if (it.kind === 'episode') { b.textContent = it.seriesTitle || it.title; s.textContent = t('meta.ep', it.season, it.episode) + ' · ' + it.title; }
  else { b.textContent = it.title; s.textContent = it.year || ''; }
  document.title = (it.seriesTitle ? it.seriesTitle + ' · ' : '') + it.title + ' — LocalFlix';
  mediaSession(it);
}
function updateButtons() {
  if (!P) return;
  var b = P.els.b, pb = P.pb, v = P.video;
  LF.clear(b.play).appendChild(icon(v.paused ? 'play' : 'pause'));
  b.play.setAttribute('aria-label', v.paused ? t('b.play') : 'Pause');
  LF.clear(b.vol).appendChild(icon(v.muted || v.volume === 0 ? 'mute' : 'vol'));
  var fsEl = document.fullscreenElement || document.webkitFullscreenElement;
  LF.clear(b.fs).appendChild(icon(fsEl ? 'fs-exit' : 'fs'));
  if (!pb) return;
  b.audio.classList.toggle('hidden', !(pb.audio && pb.audio.length > 1));
  b.subs.classList.toggle('hidden', !(pb.subtitles && pb.subtitles.length));
  b.subs.classList.toggle('on', P.subIndex !== null && P.subIndex !== undefined);
  b.next.classList.toggle('hidden', !pb.next);
  b.eps.classList.toggle('hidden', !(pb.item && pb.item.seriesId));
  b.chap.classList.toggle('hidden', !(pb.chapters && pb.chapters.length > 1));
  b.quality.classList.toggle('hidden', pb.method === 'direct' && !LF.S.info.ffmpeg.available);
  // chapters & markers on the seek bar
  $$('.chap,.mark', P.els.trk).forEach(function (x) { x.remove(); });
  var d = dur();
  if (d > 0) {
    (pb.chapters || []).forEach(function (c) { if (c.start > 1) P.els.trk.appendChild(h('div.chap', { style: { left: (c.start / d * 100) + '%' } })); });
    var mk = pb.markers || {};
    if (mk.intro) P.els.trk.appendChild(h('div.mark', { style: { left: (mk.intro.start / d * 100) + '%', width: ((mk.intro.end - mk.intro.start) / d * 100) + '%' } }));
  }
}
function showUi() {
  if (!P) return;
  P.root.classList.remove('idle');
  clearTimeout(P.idleT);
  P.idleT = setTimeout(function () {
    if (P && !P.video.paused && !P.menu && !P.dragging && !$('.p-dialog', P.root)) {
      P.root.classList.add('idle');
      if (P.root.contains(document.activeElement) && document.activeElement !== P.video) { try { document.activeElement.blur(); } catch (e) { /* ignore */ } }
    }
  }, Date.now() - (P.lastTouch || 0) < 1500 ? 4000 : 3000);  /* a little longer after a tap on touch screens */
}
function uiVisible() { return P && !P.root.classList.contains('idle'); }
function tick() {
  if (!P) return;
  var v = P.video, d = dur(), p = pos();
  var els = P.els;
  if (d > 0) {
    var pct = clamp(p / d * 100, 0, 100);
    $('.pl', els.trk).style.width = pct + '%';
    $('.knob', els.trk).style.left = pct + '%';
    var be = 0;
    for (var i = 0; i < v.buffered.length; i++) { if (v.buffered.start(i) <= v.currentTime + 0.5 && v.buffered.end(i) >= v.currentTime) be = v.buffered.end(i); }
    $('.buf', els.trk).style.width = clamp((P.offset + be) / d * 100, 0, 100) + '%';
    els.seek.setAttribute('aria-valuemax', String(Math.round(d)));
    els.seek.setAttribute('aria-valuenow', String(Math.round(p)));
    els.seek.setAttribute('aria-valuetext', fmtTime(p) + ' / ' + fmtTime(d));
  }
  els.time.textContent = P.remaining ? '-' + fmtTime(d - p) : fmtTime(p) + ' / ' + fmtTime(d);
  renderSubs(false);
  smartFeatures(p, d);
  if (P.stats) renderStats();
}
function smartFeatures(p, d) {
  var pb = P.pb; if (!pb) return;
  var mk = pb.markers || {}, st = settings();
  var skip = $('.skipbtn', P.root);
  var want = null;
  if (st.skip_buttons !== false && mk.intro && p >= mk.intro.start && p < mk.intro.end - 1) want = ['intro', t('p.skipintro'), mk.intro.end];
  else if (st.skip_buttons !== false && mk.credits && !mk.credits.fallback && p >= mk.credits.start && p < mk.credits.end - 1 && !pb.next) want = ['credits', t('p.skipcredits'), mk.credits.end];
  if (want && (!skip || skip.getAttribute('data-k') !== want[0])) {
    if (skip) skip.remove();
    skip = h('button.skipbtn', { 'data-k': want[0], onclick: function () { seekTo(want[2]); skip.remove(); } }, want[1], icon('skip'));
    P.root.appendChild(skip);
    if (LF.S.tv) setTimeout(function () { LF.Nav.focus(skip); }, 20);
  } else if (!want && skip) skip.remove();
  // next episode card
  var cstart = mk.credits ? mk.credits.start : (st.credits_fallback !== false && pb.item && pb.item.kind === 'episode' ? d - 45 : null);
  if (pb.next && !P.nextShown && !P.nextDismissed && d > 0) {
    if (cstart !== null && p >= cstart && p < d - 0.5) showNext(true);
  } else if (P.nextShown && cstart !== null && p < cstart - 1) {
    var nc = $('.nextcard', P.root); if (nc) nc.remove();
    clearInterval(P.countdown); P.nextShown = false; P.nextDismissed = false;
  }
  if (P.sleepAt && Date.now() >= P.sleepAt) { P.sleepAt = null; sleepFire(); }
}
function showNext(countdown) {
  if (!P || !P.pb.next) return;
  P.nextShown = true;
  var n = P.pb.next, secs = settings().countdown_seconds || 10, auto = settings().autoplay_next !== false && countdown;
  var ringC = 2 * Math.PI * 20;
  var ring = h('div.ring', { html: '<svg viewBox="0 0 46 46" width="46" height="46"><circle cx="23" cy="23" r="20" fill="rgba(0,0,0,.5)" stroke="rgba(255,255,255,.3)" stroke-width="3"/><circle class="rc" cx="23" cy="23" r="20" fill="none" stroke="#fff" stroke-width="3" stroke-dasharray="' + ringC + '" stroke-dashoffset="0" transform="rotate(-90 23 23)"/></svg>' });
  var label = h('small', auto ? t('p.nextin', secs) : t('p.next'));
  var card = h('div.nextcard', h('div.ratio', { style: { backgroundImage: 'url("' + n.thumb + '?w=480")' } }, auto ? ring : null),
    h('div.b', label, h('b', t('meta.ep', n.season, n.episode) + ' · ' + n.title),
      h('button.btn.btn-primary.btn-sm', { onclick: playNext, 'data-autofocus': true }, icon('play'), t('p.next')),
      h('button.btn.btn-ghost.btn-sm', { onclick: function () { P.nextDismissed = true; card.remove(); clearInterval(P.countdown); } }, t('p.watchcredits'))));
  P.root.appendChild(card);
  if (LF.S.tv) setTimeout(function () { LF.Nav.focus($('[data-autofocus]', card)); }, 30);
  if (auto) {
    var left = secs, rc = $('.rc', ring);
    P.countdown = setInterval(function () {
      if (!P) return;
      if (P.video.paused) return;
      left -= 1; label.textContent = t('p.nextin', Math.max(0, left));
      if (rc) rc.setAttribute('stroke-dashoffset', String(ringC * (1 - left / secs)));
      if (left <= 0) {
        clearInterval(P.countdown);
        if (P.sleepEnd) { P.sleepEnd = false; card.remove(); sleepFire(); return; }
        playNext();
      }
    }, 1000);
  }
}
function onEnded() {
  P.ended = true;
  report('ended');
  var pb = P.pb;
  if (P.sleepEnd) { P.sleepEnd = false; sleepFire(); return; }
  if (pb.next) { if (settings().autoplay_next !== false) playNext(); else if (!P.nextShown) showNext(false); return; }
  if (pb.item && pb.item.kind === 'episode') {
    var d = h('div.p-dialog', h('h3', t('p.endseries')), h('button.btn.btn-primary', { 'data-autofocus': true, onclick: function () { close(); } }, t('b.back')));
    P.root.appendChild(d); setTimeout(function () { LF.Nav.focus($('[data-autofocus]', d)); }, 20);
    return;
  }
  close();
}
function sleepFire() {
  P.video.pause();
  var d = h('div.p-dialog', h('h3', t('p.still')), h('button.btn.btn-primary', { 'data-autofocus': true, onclick: function () { d.remove(); if (P.ended && P.pb.next) playNext(); else play(); } }, t('p.continue')),
    h('button.btn.btn-ghost', { onclick: function () { close(); } }, t('b.back')));
  P.root.appendChild(d); setTimeout(function () { LF.Nav.focus($('[data-autofocus]', d)); }, 20);
}
function showError(msg, fatal) {
  if (!P) return;
  hideSpinner();
  $$('.p-dialog', P.root).forEach(function (x) { x.remove(); });
  var canTranscode = LF.S.info && LF.S.info.ffmpeg && LF.S.info.ffmpeg.available && !(P.pb && P.pb.method === 'transcode') && !fatal;
  var d = h('div.p-dialog', { role: 'alert' }, h('h3', t('p.error')), h('p', msg || ''),
    canTranscode ? h('button.btn.btn-primary', { 'data-autofocus': true, onclick: function () { d.remove(); P.fallback = 2; restart({ force: 'transcode' }); } }, t('p.trytranscode')) : null,
    !fatal ? h('button.btn.btn-ghost', { onclick: function () { d.remove(); P.fallback = 0; P.force = null; restart({}); } }, t('b.retry')) : null,
    h('button.btn.btn-ghost', { 'data-autofocus': !canTranscode ? true : null, onclick: function () { close(); } }, t('b.back')));
  P.root.appendChild(d);
  P.root.classList.remove('idle');
  setTimeout(function () { LF.Nav.focus($('[data-autofocus]', d)); }, 30);
}
function renderStats() {
  var el = $('.stats', P.root);
  if (!P.stats) { if (el) el.remove(); return; }
  if (!el) { el = h('div.stats'); P.root.appendChild(el); }
  if (P._lastStats && Date.now() - P._lastStats < 900) return;
  P._lastStats = Date.now();
  var v = P.video, pb = P.pb || {}, q = v.getVideoPlaybackQuality ? v.getVideoPlaybackQuality() : null, it = P.item || {};
  var be = 0; for (var i = 0; i < v.buffered.length; i++) { if (v.buffered.start(i) <= v.currentTime + 0.5 && v.buffered.end(i) >= v.currentTime) be = v.buffered.end(i) - v.currentTime; }
  var a = (pb.audio || []).filter(function (x) { return x.index === P.audioIndex; })[0];
  var lines = [
    ['Method', t('p.method.' + (pb.method || 'direct')) + (pb.mode && pb.mode !== 'direct' ? ' · ' + pb.mode : '') + (pb.hw ? ' · ' + pb.hw : '')],
    ['Reason', (pb.reason || []).join(', ')], ['Source', [(it.videoCodec || '').toUpperCase(), it.width ? it.width + '×' + it.height : '', it.container].filter(Boolean).join(' · ')],
    ['Output', v.videoWidth ? v.videoWidth + '×' + v.videoHeight : '—'], ['Bitrate', it.bitrate ? Math.round(it.bitrate / 1000) + ' kbps' : '—'],
    ['Dropped', q ? q.droppedVideoFrames + ' / ' + q.totalVideoFrames : '—'], ['Buffer', be.toFixed(1) + ' s'],
    ['Audio', a ? a.label : '—'], ['Player', P.hls ? 'hls.js ' + (window.Hls && Hls.version) : (pb.method === 'direct' ? 'native' : 'native HLS')]];
  LF.clear(el); lines.forEach(function (l) { el.appendChild(h('div', h('b', l[0] + ': '), String(l[1]))); });
}

/* --- menus --- */
function menu(kind, fromSettings) {
  if (!P || !P.pb) return;
  if (P.menu) { var same = P.menu.kind === kind; P.menuClose(); if (same) return; }
  var pb = P.pb, items = [], m = h('div.p-menu', { role: 'menu' });
  function opt(label, on, fn, small) { return h('button', { role: 'menuitemradio', 'aria-checked': on ? 'true' : 'false', onclick: function (e) { e.stopPropagation(); fn(); } }, h('span.ck', on ? '✓' : ''), label, small ? h('small', small) : null); }
  if (fromSettings) items.push(h('button.p-back', { onclick: function (e) { e.stopPropagation(); P.menuClose(); menu('settings'); } },
    h('span.ck', '‹'), t('b.back')));
  if (kind === 'audio') {
    items.push(h('h4', t('p.audio')));
    pb.audio.forEach(function (a) { items.push(opt(a.label, a.index === P.audioIndex, function () { setAudio(a.index); })); });
  } else if (kind === 'subs') {
    items.push(h('h4', t('p.subs')));
    items.push(opt(t('p.off'), P.subIndex === null || P.subIndex === undefined, function () { setSubs(null); }));
    pb.subtitles.forEach(function (s) { items.push(opt(s.label, s.index === P.subIndex, function () { setSubs(s.index); })); });
    items.push(h('h4', t('p.delay') + ': ' + (P.subDelay > 0 ? '+' : '') + (P.subDelay / 1000).toFixed(2) + 's'));
    items.push(h('div', { style: { display: 'flex', padding: '0 .6em .4em' } },
      h('button.btn.btn-sm.btn-ghost', { onclick: function () { subDelay(-250); P.menuClose(); menu('subs'); } }, '−0.25s'),
      h('button.btn.btn-sm.btn-ghost', { onclick: function () { subDelay(250); P.menuClose(); menu('subs'); } }, '+0.25s'),
      h('button.btn.btn-sm.btn-ghost', { onclick: function () { P.subDelay = 0; P.menuClose(); menu('subs'); renderSubs(true); } }, '0')));
    items.push(h('h4', t('p.size')));
    items.push(h('div', { style: { display: 'flex', padding: '0 .6em .4em' } }, [75, 100, 130, 160].map(function (sz) {
      return h('button.btn.btn-sm' + ((settings().sub_size || 100) === sz ? '.btn-primary' : '.btn-ghost'), { onclick: function () { S.settings.sub_size = sz; LF.applySubStyle(P.els.subs, settings()); api('/settings', { method: 'PUT', body: { values: { sub_size: sz } } }); P.menuClose(); menu('subs'); } }, sz + '%');
    })));
  } else if (kind === 'speed') {
    items.push(h('h4', t('p.speed')));
    [0.25, 0.5, 0.75, 1, 1.25, 1.5, 1.75, 2].forEach(function (r) { items.push(opt(r === 1 ? t('p.normal') : r + '×', P.video.playbackRate === r, function () { P.video.playbackRate = r; P.menuClose(); osd(r + '×'); })); });
  } else if (kind === 'quality') {
    items.push(h('h4', t('p.quality')));
    var cur = P.quality || settings().max_quality || 'auto';
    [['auto', t('p.auto')], ['original', t('p.original')], ['1080p', '1080p'], ['720p', '720p'], ['480p', '480p'], ['360p', '360p']].forEach(function (q) {
      items.push(opt(q[1], cur === q[0], function () { P.menuClose(); if (cur === q[0]) return; P.quality = q[0]; P.force = null; restart({ quality: q[0] }); }, q[0] === 'auto' ? t('p.method.' + pb.method) : null));
    });
  } else if (kind === 'chapters') {
    items.push(h('h4', t('p.chapters')));
    var p = pos();
    (pb.chapters || []).forEach(function (c) { items.push(opt(c.title, p >= c.start && p < c.end, function () { P.menuClose(); seekTo(c.start); }, fmtTime(c.start))); });
  } else if (kind === 'episodes') {
    items.push(h('h4', t('p.episodes')));
    var box = h('div.eps', h('div.spinner', { style: { width: '32px', height: '32px', margin: '1em auto' } }));
    items.push(box);
    api('/series/' + pb.item.seriesId + '/seasons/' + (pb.item.season || 1)).then(function (s) {
      LF.clear(box);
      s.episodes.forEach(function (e) { box.appendChild(opt((e.episode !== null ? e.episode + '. ' : '') + e.title, e.id === P.id, function () { P.menuClose(); if (e.id !== P.id) { report('stopped'); LF.go('watch/' + e.id, true); } }, LF.fmtDur(e.duration))); });
      var curB = $('[aria-checked="true"]', box); if (curB) LF.Nav.focus(curB);
    });
  } else if (kind === 'settings') {
    var pbm = P.pb || {};
    items.push(h('div.p-menu-more',
      h('h4', t('p.more')),
      opt(t('p.speed'), false, function () { P.menuClose(); menu('speed', true); }, P.video.playbackRate === 1 ? t('p.normal') : P.video.playbackRate + '×'),
      opt(t('p.quality'), false, function () { P.menuClose(); menu('quality', true); }, P.quality || settings().max_quality || 'auto'),
      pbm.chapters && pbm.chapters.length > 1 ? opt(t('p.chapters'), false, function () { P.menuClose(); menu('chapters', true); }) : null,
      pbm.next ? opt(t('p.next'), false, function () { P.menuClose(); playNext(); }, t('meta.ep', pbm.next.season, pbm.next.episode)) : null,
      document.pictureInPictureEnabled && !P.video.disablePictureInPicture ? opt('Picture in picture', false, function () { P.menuClose(); togglePip(); }) : null));
    items.push(h('h4', t('p.fit')));
    [['contain', t('p.fitf')], ['cover', t('p.fillf')], ['fill', t('p.stretch')], ['crop', t('p.crop')]].forEach(function (f) { items.push(opt(f[1], P.fit === f[0], function () { setFit(f[0]); P.menuClose(); })); });
    items.push(h('h4', t('p.sleep')));
    [[0, t('p.sleepoff')], [15, t('p.min', 15)], [30, t('p.min', 30)], [45, t('p.min', 45)], [60, t('p.min', 60)], ['end', t('p.sleepend')]].forEach(function (s) {
      var on = s[0] === 'end' ? P.sleepEnd : (s[0] === 0 ? !P.sleepAt && !P.sleepEnd : P.sleepMin === s[0] && !!P.sleepAt);
      items.push(opt(s[1], on, function () { setSleep(s[0]); P.menuClose(); }));
    });
    items.push(h('h4', t('p.stats')));
    items.push(opt(t('p.stats'), P.stats, function () { P.stats = !P.stats; renderStats(); P.menuClose(); }));
    if (!LF.S.tv) items.push(opt(t('p.shortcuts'), false, function () { P.menuClose(); shortcuts(); }));
  }
  LF.append(m, items);
  P.root.appendChild(m);
  P.menu = { kind: kind, el: m };
  P.menuClose = function () { if (P.menu) { P.menu.el.remove(); P.menu = null; } showUi(); var bb = P.els.b[{ subs: 'subs', audio: 'audio', speed: 'speed', quality: 'quality', episodes: 'eps', chapters: 'chap', settings: 'gear' }[kind]]; if (bb && LF.S.tv) LF.Nav.focus(bb); };
  showUi();
  setTimeout(function () { LF.Nav.focus($('[aria-checked="true"]', m) || $('button', m)); }, 20);
}
function setSleep(v) {
  P.sleepEnd = false; P.sleepAt = null; P.sleepMin = null;
  if (v === 'end') P.sleepEnd = true;
  else if (v) { P.sleepMin = v; P.sleepAt = Date.now() + v * 60000; }
  if (v) osd(t('p.sleep') + ': ' + (v === 'end' ? t('p.sleepend') : t('p.min', v)));
}
function subDelay(ms) { P.subDelay = (P.subDelay || 0) + ms; osd(t('p.delay') + ' ' + (P.subDelay > 0 ? '+' : '') + (P.subDelay / 1000).toFixed(2) + 's'); renderSubs(true); }
function shortcuts() {
  var rows = [['Space / K', 'Play / pause'], ['J / L', '−10 s / +10 s'], ['← / →', '−5 s / +5 s'], ['↑ / ↓', 'Volume'], ['M', 'Mute'], ['F', 'Fullscreen'], ['C', t('p.subs')], ['A', t('p.audio')],
    ['[ / ]', t('p.delay') + ' ∓250 ms'], ['< / >', t('p.speed')], ['N', t('p.next')], ['I', t('p.stats')], ['0–9', 'Seek 0–90 %'], ['Esc', t('b.back')]];
  var d = h('div.p-dialog', h('h3', t('p.shortcuts')), h('div', { style: { textAlign: 'left', fontSize: '.9rem' } }, rows.map(function (r) { return h('div', { style: { display: 'flex', justifyContent: 'space-between', padding: '.2em 0' } }, h('b', r[0]), h('span', r[1])); })),
    h('button.btn.btn-primary', { style: { marginTop: '1em' }, 'data-autofocus': true, onclick: function () { d.remove(); } }, t('b.close')));
  P.root.appendChild(d); setTimeout(function () { LF.Nav.focus($('[data-autofocus]', d)); }, 20);
}

/* --- reporting --- */
function report(state) {
  if (!P || !P.pb || !P.item) return;
  var p = pos(), d = dur();
  if (d <= 0) return;
  var body = { itemId: P.id, position: P.ended ? d : p, duration: d, state: state, audioIndex: P.audioIndex, subtitleIndex: P.subIndex === null || P.subIndex === undefined ? -1 : P.subIndex, sessionId: P.pb.sessionId };
  if ((state === 'stopped' || state === 'hidden') && navigator.sendBeacon) {
    try { if (navigator.sendBeacon('/api/v1/progress', new Blob([JSON.stringify(body)], { type: 'text/plain' }))) return; } catch (e) { /* fall through */ }
  }
  api('/progress', { method: 'POST', body: body, keepalive: true }).catch(function () { /* offline */ });
}

/* --- seek bar interaction --- */
function seekFrac(e) { var r = P.els.trk.getBoundingClientRect(); return clamp(((e.touches ? e.touches[0].clientX : e.clientX) - r.left) / r.width, 0, 1); }
function showPreview(frac) {
  var pv = P.els.preview, d = dur(), tt = frac * d, pb = P.pb || {};
  pv.style.display = 'block';
  var trkW = P.els.trk.getBoundingClientRect().width;
  pv.style.left = clamp(frac * trkW, 124, Math.max(124, trkW - 124)) + 'px';
  $('.tm', pv).textContent = fmtTime(tt);
  var ch = (pb.chapters || []).filter(function (c) { return tt >= c.start && tt < c.end; })[0];
  $('.cn', pv).textContent = ch && pb.chapters.length > 1 ? ch.title : '';
  var img = $('.img', pv), tp = pb.trickplay;
  if (tp) {
    var idx = Math.floor(tt / tp.interval), per = tp.tile[0] * tp.tile[1], sheet = Math.floor(idx / per), within = idx % per;
    var col = within % tp.tile[0], row = Math.floor(within / tp.tile[0]), sc = 240 / tp.w;
    img.style.display = 'block';
    img.style.backgroundImage = 'url("' + tp.url + 'sheet_' + ('00' + (sheet + 1)).slice(-3) + '.jpg")';
    img.style.backgroundSize = (tp.tile[0] * tp.w * sc) + 'px ' + (tp.tile[1] * tp.h * sc) + 'px';
    img.style.backgroundPosition = (-col * tp.w * sc) + 'px ' + (-row * tp.h * sc) + 'px';
  } else img.style.display = 'none';
}
function hidePreview() { if (P) P.els.preview.style.display = 'none'; }

/* --- events wiring --- */
function wire() {
  var v = P.video, els = P.els, root = P.root;
  ['play', 'pause', 'volumechange'].forEach(function (ev) { v.addEventListener(ev, function () { updateButtons(); if (ev === 'pause' && !P.loading) { showUi(); report('paused'); } }); });
  v.addEventListener('playing', function () { hideSpinner(); P.waitingSince = 0; P.everPlayed = true; P.mediaRetries = 0; P.restarts = 0; P.loading = false; P.pendingPos = null; showUi(); if (settings().auto_fullscreen && !LF.S.tv && !P.fsTried && !/Mobi/.test(navigator.userAgent)) { P.fsTried = true; if (!document.fullscreenElement) toggleFs(); } });
  v.addEventListener('waiting', function () { P.waitingSince = Date.now(); setTimeout(function () { if (P && P.waitingSince && Date.now() - P.waitingSince >= 350) spinner(P.pb && P.pb.method !== 'direct' ? t('p.preparing') : t('p.buffering')); }, 400); });
  v.addEventListener('canplay', function () { if (!v.paused) hideSpinner(); });
  v.addEventListener('seeked', function () { if (P && !P.loading) P.pendingPos = null; });
  v.addEventListener('seeked', LF.debounce(function () { if (P) report('playing'); }, 800));
  v.addEventListener('timeupdate', tick);
  v.addEventListener('ended', function () { if (!isSimple() || pos() >= dur() - 2) onEnded(); });
  v.addEventListener('error', function () {
    if (!P || !P.pb || P.hls) return;
    var e = v.error; if (!e) return;
    fallbackOrError(t('p.error') + ' (' + (e.message || 'code ' + e.code) + ')');
  });
  v.addEventListener('loadedmetadata', function () { tick(); updateButtons(); });
  P.reportT = setInterval(function () { if (P && !P.video.paused) report('playing'); }, 10000);
  /* stall watchdog: never spin forever. If nothing plays for a while (start or mid-stream), switch to the next
     safer method (direct → remux → transcode) or show the error screen with Retry / Try transcoding. */
  P.stallT = setInterval(function () {
    if (!P || !P.pb || $('.p-dialog', P.root) || $('.p-bigplay', P.root) || P.menu || document.hidden) return;
    var limit = P.pb.method === 'transcode' ? 45000 : 25000, now = Date.now();
    var startStall = !P.everPlayed && P.loadAt && now - P.loadAt > limit;
    var midStall = P.everPlayed && P.waitingSince && now - P.waitingSince > limit;
    if (!startStall && !midStall) return;
    P.loadAt = now; P.waitingSince = 0;
    if (P.hls) { try { P.hls.startLoad(); } catch (e) { /* ignore */ } }
    fallbackOrError(t('p.error') + ' (stalled)');
  }, 2000);
  P.rafT = setInterval(function () { if (P && !P.video.paused && P.cues.length) renderSubs(false); }, 200);
  if (window.WebKitPlaybackTargetAvailabilityEvent) v.addEventListener('webkitplaybacktargetavailabilitychanged', function (e) { els.b.air.classList.toggle('hidden', e.availability !== 'available'); });
  if (v.remote && v.remote.watchAvailability) v.remote.watchAvailability(function (a) { if (P && P.pb && P.pb.method === 'direct') els.b.cast.classList.toggle('hidden', !a); }).catch(function () { /* unsupported */ });
  /* phones fire a synthetic mousemove right after every tap: ignore it, the tap handler decides */
  root.addEventListener('mousemove', function () { if (Date.now() - (P.lastTouch || 0) > 1000) showUi(); });
  P.onOutside = function (e) {
    if (!P || !P.menu) return;
    var tg = e.target;
    if (tg.closest && (tg.closest('.p-menu') || tg.closest('.p-btn'))) return;  /* buttons toggle/switch menus themselves */
    P.menuClose();
    P.swallowTap = Date.now();
  };
  root.addEventListener('pointerdown', P.onOutside, true);
  root.addEventListener('touchstart', function () { /* handled by gestures */ }, { passive: true });
  P.onFs = function () { updateButtons(); };
  document.addEventListener('fullscreenchange', P.onFs);
  document.addEventListener('webkitfullscreenchange', P.onFs);
  P.onVis = function () { if (document.hidden) report('hidden'); };
  P.onHide = function () { report('stopped'); if (P && P.pb && P.pb.sessionId && navigator.sendBeacon) navigator.sendBeacon('/api/v1/hls/' + P.pb.sessionId + '/stop', new Blob(['{}'], { type: 'text/plain' })); };
  document.addEventListener('visibilitychange', P.onVis);
  window.addEventListener('pagehide', P.onHide);
  // seek bar
  var seek = els.seek;
  function down(e) {
    if (e.button !== undefined && e.button !== 0) return;
    P.dragging = true; seek.classList.add('drag'); showPreview(seekFrac(e)); e.preventDefault();
    var move = function (ev) { var f = seekFrac(ev); showPreview(f); $('.pl', els.trk).style.width = f * 100 + '%'; $('.knob', els.trk).style.left = f * 100 + '%'; };
    var up = function (ev) {
      document.removeEventListener('pointermove', move); document.removeEventListener('pointerup', up);
      P.dragging = false; seek.classList.remove('drag'); hidePreview();
      var f = seekFrac(ev.changedTouches ? ev.changedTouches[0] : ev); seekTo(f * dur()); showUi();
    };
    document.addEventListener('pointermove', move); document.addEventListener('pointerup', up);
  }
  if (window.PointerEvent) seek.addEventListener('pointerdown', down);
  else seek.addEventListener('mousedown', function (e) { var f = seekFrac(e); seekTo(f * dur()); });
  seek.addEventListener('mousemove', function (e) { if (!P.dragging) showPreview(seekFrac(e)); });
  seek.addEventListener('mouseleave', function () { if (!P.dragging) hidePreview(); });
  // gestures on the video surface
  var g = els.gest, lastTap = 0, tapT = null, start = null, longT = null, acc = 0, accT = null;
  g.addEventListener('click', function (e) {
    if (Date.now() - (P.swallowTap || 0) < 600) return;
    if (P.touchHandled) { P.touchHandled = false; return; }
    if (P.menu) { P.menuClose(); return; }
    clearTimeout(tapT);
    tapT = setTimeout(function () { togglePlay(); }, 220);
  });
  function seekRipple(dir) {
    acc += 10 * dir; clearTimeout(accT);
    $$('.ripple', root).forEach(function (r) { r.parentNode.removeChild(r); });
    var rp = h('div.ripple', { style: { left: dir < 0 ? '10%' : 'auto', right: dir > 0 ? '10%' : 'auto' } }, icon(dir < 0 ? 'rew' : 'fwd'), h('span', (acc > 0 ? '+' : '') + acc + 's'));
    root.appendChild(rp); setTimeout(function () { if (rp.parentNode) rp.parentNode.removeChild(rp); }, 700);
    accT = setTimeout(function () { seekTo(pos() + acc); acc = 0; }, 450);
  }
  function zone(x) { var w = root.getBoundingClientRect().width || window.innerWidth; return x < w * 0.33 ? -1 : (x > w * 0.67 ? 1 : 0); }
  g.addEventListener('dblclick', function (e) {
    clearTimeout(tapT);
    if (Date.now() - (P.lastTouch || 0) < 1000) return;  /* already handled by the touch handler */
    var z = zone(e.clientX);
    if (z) seekRipple(z); else toggleFs();
  });
  g.addEventListener('wheel', function (e) { e.preventDefault(); v.volume = clamp(v.volume + (e.deltaY < 0 ? 0.05 : -0.05), 0, 1); v.muted = false; els.volIn.value = v.volume; saveVol(); osd(Math.round(v.volume * 100) + '%'); }, { passive: false });
  g.addEventListener('touchstart', function (e) {
    if (e.touches.length !== 1) return;
    var tch = e.touches[0];
    P.lastTouch = Date.now();
    start = { uiWasVisible: uiVisible(), x: tch.clientX, y: tch.clientY, t: Date.now(), vol: v.volume, br: +(els.bright.style.opacity || 0), moved: false, side: tch.clientX > window.innerWidth / 2 ? 'r' : 'l' };
    longT = setTimeout(function () { if (start && !start.moved) { P.speedHold = v.playbackRate; v.playbackRate = 2; osd('2×'); } }, 550);
  }, { passive: true });
  g.addEventListener('touchmove', function (e) {
    if (!start) return;
    var tch = e.touches[0], dy = start.y - tch.clientY, dx = tch.clientX - start.x;
    if (Math.abs(dy) > 18 && Math.abs(dy) > Math.abs(dx)) {
      start.moved = true; clearTimeout(longT);
      var f = dy / (window.innerHeight * 0.6);
      if (start.side === 'r') { v.volume = clamp(start.vol + f, 0, 1); v.muted = false; osd('🔊 ' + Math.round(v.volume * 100) + '%'); }
      else { var b = clamp(start.br - f, 0, 0.85); els.bright.style.opacity = b; osd('☀ ' + Math.round((1 - b) * 100) + '%'); }
    } else if (Math.abs(dx) > 18) { start.moved = true; clearTimeout(longT); }
  }, { passive: true });
  g.addEventListener('touchend', function (e) {
    clearTimeout(longT);
    if (Date.now() - (P.swallowTap || 0) < 600) { start = null; P.touchHandled = true; return; }
    if (P.speedHold !== null && P.speedHold !== undefined) { v.playbackRate = P.speedHold; P.speedHold = null; P.touchHandled = true; return; }
    if (!start) return;
    var s = start; start = null;
    if (s.moved) { saveVol(); P.touchHandled = true; return; }
    P.touchHandled = true;
    var now = Date.now(), x = e.changedTouches[0].clientX, third = window.innerWidth / 3;
    if (now - lastTap < 300) {
      clearTimeout(tapT);
      var z = zone(x);
      if (z) seekRipple(z);            /* keep tapping to add ±10 s each time */
      else { toggleFs(); lastTap = 0; return; }
      lastTap = now; return;
    }
    lastTap = now;
    clearTimeout(tapT);
    /* single tap toggles the controls based on how they looked when the finger touched the screen */
    if (!s.uiWasVisible) { showUi(); return; }  /* show instantly; a second tap may still seek */
    tapT = setTimeout(function () { clearTimeout(P.idleT); root.classList.add('idle'); }, 300);
  });
  showUi();
}

/* --- Media Session --- */
function mediaSession(it) {
  if (!('mediaSession' in navigator) || !window.MediaMetadata) return;
  try {
    navigator.mediaSession.metadata = new MediaMetadata({ title: it.title, artist: it.seriesTitle || 'LocalFlix', album: it.kind === 'episode' ? t('meta.ep', it.season, it.episode) : '',
      artwork: [{ src: location.origin + it.thumb + '?w=480', sizes: '480x270', type: 'image/jpeg' }, { src: location.origin + it.backdrop + '?w=1280', sizes: '1280x720', type: 'image/jpeg' }] });
    var ms = navigator.mediaSession;
    var set = function (a, fn) { try { ms.setActionHandler(a, fn); } catch (e) { /* unsupported action */ } };
    set('play', function () { play(); }); set('pause', function () { P.video.pause(); });
    set('seekbackward', function () { seekBy(-10); }); set('seekforward', function () { seekBy(10); });
    set('seekto', function (d) { seekTo(d.seekTime); });
    set('nexttrack', P.pb && P.pb.next || (it.kind === 'episode') ? function () { playNext(); } : null);
    set('previoustrack', it.kind === 'episode' ? function () { playPrev(); } : null);
  } catch (e) { /* ignore */ }
}

/* --- keys --- */
var holdCount = 0;
function onKey(e, k) {
  if (!P) return false;
  var ae = document.activeElement, inPlayer = P.root.contains(ae);
  var dialog = $('.p-dialog', P.root);
  if (k === 'back') {
    if (P.menu) { P.menuClose(); return true; }
    if (dialog && $('.p-dialog h3', P.root) && dialog.querySelectorAll('button').length > 1 && !$('[role="alert"]', P.root) && dialog.textContent.indexOf(t('p.shortcuts')) >= 0) { dialog.remove(); return true; }
    if (LF.S.tv && uiVisible() && inPlayer && ae !== P.els.seek && !dialog && !P.video.paused) { P.root.classList.add('idle'); ae.blur(); return true; }
    close(); return true;
  }
  if (dialog || P.menu) {
    if (k === 'left' || k === 'right' || k === 'up' || k === 'down') { LF.Nav.move(k); return true; }
    return false;
  }
  showUi();
  switch (k) {
    case 'playpause': togglePlay(); return true;
    case 'play': play(); return true;
    case 'pause': P.video.pause(); return true;
    case 'stop': close(); return true;
    case 'rew': seekBy(-30); return true;
    case 'ff': seekBy(30); return true;
    case 'next': playNext(); return true;
    case 'prev': playPrev(); return true;
    case 'red': menu('subs'); return true;
    case 'green': menu('audio'); return true;
    case 'yellow': menu('speed'); return true;
    case 'blue': playNext(); return true;
  }
  var onCtl = inPlayer && ae !== P.els.seek && ae.tagName === 'BUTTON';
  if (k === 'ok') {
    if (onCtl) return false;
    togglePlay(); return true;
  }
  if (k === 'left' || k === 'right') {
    if (onCtl && LF.S.tv) { LF.Nav.move(k); return true; }
    if (onCtl && !LF.S.tv && ae.closest('.p-row')) return false;
    holdCount = e.repeat ? holdCount + 1 : 0;
    var step = LF.S.tv ? (holdCount > 20 ? 60 : holdCount > 8 ? 30 : 10) : 5;
    seekBy(k === 'left' ? -step : step);
    return true;
  }
  if (k === 'up') {
    if (LF.S.tv) { if (!inPlayer || ae === P.video || ae === document.body) { LF.Nav.focus(P.els.seek); } else LF.Nav.move('up'); return true; }
    P.video.volume = clamp(P.video.volume + 0.05, 0, 1); P.video.muted = false; P.els.volIn.value = P.video.volume; saveVol(); osd(Math.round(P.video.volume * 100) + '%'); return true;
  }
  if (k === 'down') {
    if (LF.S.tv) { if (ae === P.els.seek) LF.Nav.focus(P.els.b.play); else if (onCtl) { P.root.classList.add('idle'); ae.blur(); } else LF.Nav.focus(P.els.seek); return true; }
    P.video.volume = clamp(P.video.volume - 0.05, 0, 1); P.els.volIn.value = P.video.volume; saveVol(); osd(Math.round(P.video.volume * 100) + '%'); return true;
  }
  if (LF.isTyping()) return false;
  var key = e.key;
  if (key === ' ' || key === 'k' || key === 'K') { if (onCtl && key === ' ') return false; togglePlay(); return true; }
  if (key === 'j' || key === 'J') { seekBy(-10); return true; }
  if (key === 'l' || key === 'L') { seekBy(10); return true; }
  if (key === 'm' || key === 'M') { P.video.muted = !P.video.muted; saveVol(); return true; }
  if (key === 'f' || key === 'F') { toggleFs(); return true; }
  if (key === 'c' || key === 'C') { if (P.pb.subtitles.length) { var subs = P.pb.subtitles.filter(function (s) { return s.kind === 'text'; }); var ci = -1; subs.forEach(function (s, i) { if (s.index === P.subIndex) ci = i; }); var nx = ci + 1 < subs.length ? subs[ci + 1].index : null; setSubs(nx); osd(t('p.subs') + ': ' + (nx === null ? t('p.off') : subs[ci + 1].label)); } return true; }
  if (key === 'a' || key === 'A') { if (P.pb.audio.length > 1) { var ai = 0; P.pb.audio.forEach(function (x, i) { if (x.index === P.audioIndex) ai = i; }); setAudio(P.pb.audio[(ai + 1) % P.pb.audio.length].index); } return true; }
  if (key === '[') { subDelay(-250); return true; }
  if (key === ']') { subDelay(250); return true; }
  if (key === '<' || key === ',') { P.video.playbackRate = Math.max(0.25, P.video.playbackRate - 0.25); osd(P.video.playbackRate + '×'); return true; }
  if (key === '>' || key === '.') { P.video.playbackRate = Math.min(2, P.video.playbackRate + 0.25); osd(P.video.playbackRate + '×'); return true; }
  if (key === 'n' || key === 'N') { playNext(); return true; }
  if (key === 'i' || key === 'I') { P.stats = !P.stats; renderStats(); return true; }
  if (key === '?') { shortcuts(); return true; }
  if (/^[0-9]$/.test(key)) { seekTo(dur() * (+key) / 10); return true; }
  return false;
}

function onJob(d) {
  if (!P || !P.pb || d.sessionId !== P.pb.sessionId || !P.spinMsg || !$('.spinner', P.els.center)) return;
  var target = pos(), seg = 6, pct = clamp(Math.round(((d.time - target + seg) / (seg * 2)) * 100), 1, 100);
  if (d.time < target - 60) pct = clamp(Math.round((d.time % (seg * 2)) / (seg * 2) * 100), 1, 99);
  /* once the server is past the play position the wait is on the player side: say so instead of "99 %" */
  var msg = $('.msg', P.els.center);
  if (msg) msg.textContent = pct >= 95 ? t('p.buffering') : t('p.preparingpct', pct) + (d.speed ? '  ·  ' + d.speed.toFixed(1) + '×' : '');
}
function onTrick(d) { if (P && P.pb && d.id === P.id) api('/trickplay/' + P.id + '/index.json').then(function (ti) { ti.url = '/api/v1/trickplay/' + P.id + '/'; P.pb.trickplay = ti; }).catch(function () { /* not ready */ }); }

function teardown(silent, keepFs) {
  if (!P) return;
  var p = P;
  if (!p.ended) report('stopped');
  clearInterval(p.reportT); clearInterval(p.stallT); clearInterval(p.rafT); clearInterval(p.countdown); clearTimeout(p.idleT);
  document.removeEventListener('visibilitychange', p.onVis);
  window.removeEventListener('pagehide', p.onHide);
  document.removeEventListener('fullscreenchange', p.onFs);
  document.removeEventListener('webkitfullscreenchange', p.onFs);
  try { if (p.video.remote && p.video.remote.cancelWatchAvailability) p.video.remote.cancelWatchAvailability().catch(function () { /* none */ }); } catch (e) { /* ignore */ }
  destroyHls();
  try { p.video.pause(); p.video.removeAttribute('src'); p.video.load(); } catch (e) { /* ignore */ }
  if (p.pb && p.pb.sessionId) api('/hls/' + p.pb.sessionId, { method: 'DELETE', keepalive: true }).catch(function () { /* gone */ });
  if (!keepFs && (document.fullscreenElement || document.webkitFullscreenElement)) {
    try { (document.exitFullscreen || document.webkitExitFullscreen).call(document); } catch (e) { /* ignore */ }
    try { if (screen.orientation && screen.orientation.unlock) screen.orientation.unlock(); } catch (e) { /* ignore */ }
  }
  if (document.pictureInPictureElement) { try { document.exitPictureInPicture(); } catch (e) { /* ignore */ } }
  p.root.remove();
  P = null;
  document.body.style.overflow = '';
  document.documentElement.style.overflow = '';
  document.title = (window.__LF__ && window.__LF__.name) || 'LocalFlix';
  try {
    if ('mediaSession' in navigator) {
      navigator.mediaSession.metadata = null;
      ['play', 'pause', 'seekbackward', 'seekforward', 'seekto', 'nexttrack', 'previoustrack'].forEach(function (a) { try { navigator.mediaSession.setActionHandler(a, null); } catch (e) { /* unsupported */ } });
    }
  } catch (e) { /* ignore */ }
  if (!silent && p.focusBefore && document.contains(p.focusBefore)) LF.Nav.focus(p.focusBefore);
  LF.refreshSoon();
}
function close(fromRoute) {
  if (!P) return;
  var focus = P.focusBefore;
  teardown(!!fromRoute);
  if (!fromRoute) {
    if (LF.canBack()) history.back(); else LF.go(LF.S.page || 'home', true);
    setTimeout(function () { if (focus && document.contains(focus)) LF.Nav.focus(focus); }, 60);
  }
}
LF.Player = { preload: preload, open: open, close: close, active: function () { return !!P; }, onKey: onKey, onJob: onJob, onTrick: onTrick, parseVTT: parseVTT, debug: function () { return P ? { pos: pos(), dur: dur(), cues: P.cues.length, subIndex: P.subIndex, audioIndex: P.audioIndex, offset: P.offset, pb: P.pb, hls: P.hls } : null; } };
})();

if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', LF.ready); else LF.ready();
})();
"""


# ===== SECTION: web-assets =====
WEB_ASSETS: dict[str, bytes] = {
    "index.html": INDEX_HTML.encode("utf-8"),
    "app.css": APP_CSS.encode("utf-8"),
    "app.js": APP_JS.encode("utf-8"),
    "favicon.svg": FAVICON_SVG.encode("utf-8"),
    "manifest.webmanifest": MANIFEST.encode("utf-8"),
}

if __name__ == "__main__":
    sys.exit(main())
