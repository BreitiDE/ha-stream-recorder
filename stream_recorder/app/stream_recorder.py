#!/usr/bin/env python3
"""
Stream Recorder – Home Assistant app for scheduled recording of MP3 web radio streams.

  HA sidebar (ingress :8099) ─┐
  Browser direct (:8765, login)┴─► web UI / API ─► scheduler ─► recorder ─► /media/stream-recorder/record

- jobs with start time, duration, pre-/post-roll and repeat
- recording without re-encoding, ICY titles become an ID3 track list + chapters
- automatic reconnect, playlists (.m3u/.pls) are resolved
- lossless cutting into single tracks (title/), trash folder (deleted/)
- entities, events and stdin commands for Home Assistant
- log only into the app (file), nothing into the Home Assistant log
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import logging
import logging.handlers
import os
import re
import secrets
import shutil
import socket
import sqlite3
import sys
import time
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import parse_qs, quote, urljoin

import httpx
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import (FileResponse, HTMLResponse, JSONResponse,
                                 PlainTextResponse, RedirectResponse, Response)
from starlette.routing import Route

sys.path.insert(0, str(Path(__file__).resolve().parent))
import covers  # noqa: E402
import cutter  # noqa: E402
import ha  # noqa: E402
import i18n  # noqa: E402
import library as lib  # noqa: E402
from i18n import _  # noqa: E402

__version__ = "2.0.1"
BASE = Path(__file__).resolve().parent
WEB = BASE / "web"
log = logging.getLogger("stream-recorder")
UA = f"stream-recorder/{__version__}"
DONATE_URL = "https://paypal.me/breitide"

# --------------------------------------------------------------------------
# Configuration: app options (/data/options.json) + environment (tests)
# --------------------------------------------------------------------------
DEFAULTS = {
    "data_dir": "/data",
    "storage_path": "/media/stream-recorder",
    "username": "",
    "password": "",
    "log_level": "info",
    "listen_host": "0.0.0.0",
    "ingress_port": 8099,
    "direct_port": 8765,
    "trusted_proxies": ["172.30.32.2"],      # Supervisor (ingress proxy)
    "supervisor_url": "http://supervisor",
    "history_days": 90,          # erledigte Einträge so lange behalten (Dateien bleiben)
    "min_success_ratio": 0.97,   # Anteil der geplanten Dauer für "erfolgreich"
    "reconnect_delay": 5,        # Sekunden zwischen Verbindungsversuchen
    "read_timeout": 20,          # Sekunden ohne Daten = Verbindung neu aufbauen
    "session_hours": 12,         # Login ohne "angemeldet bleiben" (direkter Zugriff)
    "remember_days": 30,         # Login mit "angemeldet bleiben"
    "cover_deezer_url": covers.DEEZER_URL,
    "cover_itunes_url": covers.ITUNES_URL,
}
# Einstellungen aus der Oberfläche (Datenbank). Muster: Standard hängt von der Sprache ab.
SETTING_DEFAULTS = {
    "pre_roll_min": 1,
    "post_roll_min": 3,
    "rename_skip_empty": True,
    "cut_min_len": 90,           # Sekunden, kürzere Stücke werden nicht geschnitten
    "cut_offset": 0,             # Sekunden, verschiebt alle Schnittpunkte
    "prune_empty_dirs": True,    # nach dem Löschen leer gewordene Ordner entfernen
    "cover_lookup": True,        # beim Schneiden Song-Cover online suchen (Deezer, iTunes)
    "trash_days": 0,             # Papierkorb nach X Tagen automatisch leeren, 0 = nie
    "language": "auto",          # auto = Sprache von Home Assistant
}
PATTERN_KEYS = ("folder_pattern", "file_pattern", "cut_pattern", "rename_pattern")
REC_PLACEHOLDERS = ("sender", "titel", "moderation", "datum", "zeit", "jahr", "monat", "tag", "wochentag")
SEED_SENDERS = [
    ("sunshine live Lounge",
     "https://stream.sunshine-live.de/lounge/mp3-192/&utm_source=radio.menu/play.m3u"),
]
STORAGE_RE = re.compile(r"^/(media|share)(/[^\x00]*)?$")


def load_config() -> dict:
    cfg = dict(DEFAULTS)
    cfg["warnings"] = []
    env = os.environ.get
    cfg["data_dir"] = env("STREAM_RECORDER_DATA_DIR") or cfg["data_dir"]
    opt_file = Path(env("STREAM_RECORDER_OPTIONS") or Path(cfg["data_dir"]) / "options.json")
    try:
        opts = json.loads(opt_file.read_text("utf-8"))
    except (OSError, ValueError):
        opts = {}
    for k in ("storage_path", "username", "password", "log_level"):
        if opts.get(k) is not None:
            cfg[k] = opts[k]
    cfg["storage_path"] = str(cfg["storage_path"] or DEFAULTS["storage_path"]).rstrip("/") or DEFAULTS["storage_path"]
    if env("STREAM_RECORDER_STORAGE"):                     # Tests: beliebiger Ordner
        cfg["storage_path"] = env("STREAM_RECORDER_STORAGE")
    elif not STORAGE_RE.match(cfg["storage_path"]) or ".." in cfg["storage_path"].split("/"):
        cfg["warnings"].append(("storage", cfg["storage_path"]))
        cfg["storage_path"] = DEFAULTS["storage_path"]
    for envname, key, typ in (("STREAM_RECORDER_LOG_FILE", "log_file", str),
                              ("STREAM_RECORDER_LISTEN_HOST", "listen_host", str),
                              ("STREAM_RECORDER_INGRESS_PORT", "ingress_port", int),
                              ("STREAM_RECORDER_DIRECT_PORT", "direct_port", int),
                              ("STREAM_RECORDER_SUPERVISOR_URL", "supervisor_url", str),
                              ("STREAM_RECORDER_COVER_DEEZER", "cover_deezer_url", str),
                              ("STREAM_RECORDER_COVER_ITUNES", "cover_itunes_url", str)):
        if env(envname):
            cfg[key] = typ(env(envname))
    if env("STREAM_RECORDER_TRUSTED_PROXY"):
        cfg["trusted_proxies"] = [x.strip() for x in env("STREAM_RECORDER_TRUSTED_PROXY").split(",") if x.strip()]
    cfg.setdefault("log_file", str(Path(cfg["data_dir"]) / "log" / "stream-recorder.log"))
    cfg["username"] = str(cfg["username"] or "").strip()
    cfg["password"] = str(cfg["password"] or "")
    return cfg


CFG = load_config()
DATA = Path(CFG["data_dir"])


def setup_logging() -> None:
    """Nur in die Logdatei (Ansicht „Log“ in der App), nichts nach stdout/HA-Log."""
    Path(CFG["log_file"]).parent.mkdir(parents=True, exist_ok=True)
    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(message)s", "%Y-%m-%d %H:%M:%S")
    fh = logging.handlers.RotatingFileHandler(CFG["log_file"], maxBytes=5_000_000,
                                              backupCount=3, encoding="utf-8")
    fh.setFormatter(fmt)
    root = logging.getLogger()
    root.handlers[:] = [fh]
    root.setLevel(str(CFG["log_level"]).upper() if str(CFG["log_level"]).upper() in
                  ("DEBUG", "INFO", "WARNING", "ERROR") else "INFO")
    for noisy in ("httpx", "httpcore", "uvicorn.access"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


# --------------------------------------------------------------------------
# Ordner
# --------------------------------------------------------------------------
def storage() -> Path:
    return Path(CFG["storage_path"])


def rec_dir() -> Path:
    return storage() / "record"


def title_dir() -> Path:
    return storage() / "title"


def trash_dir() -> Path:
    return storage() / "deleted"


def lib_roots() -> list[tuple[str, Path]]:
    return [("record", rec_dir()), ("title", title_dir())]


def ensure_dirs() -> None:
    for d in (rec_dir(), title_dir(), trash_dir()):
        try:
            d.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            log.error("Cannot create folder %s: %s", d, e)


# --------------------------------------------------------------------------
# Datenbank
# --------------------------------------------------------------------------
SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS senders (
    id INTEGER PRIMARY KEY, name TEXT NOT NULL, url TEXT NOT NULL, created REAL);
CREATE TABLE IF NOT EXISTS jobs (
    id INTEGER PRIMARY KEY, title TEXT NOT NULL, host TEXT, sender TEXT, url TEXT NOT NULL,
    start REAL NOT NULL, duration_min REAL NOT NULL, pre_min REAL DEFAULT 0,
    post_min REAL DEFAULT 0, repeat TEXT DEFAULT 'once', source TEXT DEFAULT 'manual',
    last_window REAL, created REAL);
CREATE TABLE IF NOT EXISTS history (
    id INTEGER PRIMARY KEY, job_id INTEGER, title TEXT, host TEXT, sender TEXT, url TEXT,
    path TEXT, window_start REAL, window_end REAL, started REAL, ended REAL,
    duration_s REAL, bytes INTEGER DEFAULT 0, status TEXT, message TEXT, tags TEXT,
    tracks INTEGER DEFAULT 0, reconnects INTEGER DEFAULT 0, content_type TEXT, bitrate INTEGER,
    job_start REAL);
CREATE INDEX IF NOT EXISTS ix_hist_ended ON history(ended);
CREATE INDEX IF NOT EXISTS ix_jobs_start ON jobs(start);
CREATE TABLE IF NOT EXISTS rename_log (
    id INTEGER PRIMARY KEY, batch TEXT, ts REAL, old_path TEXT, new_path TEXT, undone INTEGER DEFAULT 0);
CREATE INDEX IF NOT EXISTS ix_hist_path ON history(path);
CREATE TABLE IF NOT EXISTS trash (
    id INTEGER PRIMARY KEY, orig TEXT NOT NULL, path TEXT NOT NULL UNIQUE, deleted REAL, size INTEGER,
    title TEXT, artist TEXT, kind TEXT, duration REAL);
"""
REPEATS = {"once": "einmalig", "daily": "täglich", "weekdays": "werktags", "weekly": "wöchentlich"}


class DB:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.con = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self.con.row_factory = sqlite3.Row
        self.con.execute("PRAGMA journal_mode=WAL")
        self.con.executescript(SCHEMA)

    def q(self, sql: str, args: tuple = ()) -> list[dict]:
        return [dict(r) for r in self.con.execute(sql, args).fetchall()]

    def one(self, sql: str, args: tuple = ()) -> dict | None:
        r = self.con.execute(sql, args).fetchone()
        return dict(r) if r else None

    def x(self, sql: str, args: tuple = ()) -> int:
        cur = self.con.execute(sql, args)
        return cur.lastrowid

    def setting(self, key: str):
        r = self.one("SELECT value FROM settings WHERE key=?", (key,))
        if r:
            return json.loads(r["value"])
        if key in PATTERN_KEYS:
            return i18n.default_pattern(key)
        return SETTING_DEFAULTS[key]

    def set_setting(self, key: str, value) -> None:
        self.x("INSERT INTO settings(key,value) VALUES(?,?) "
               "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, json.dumps(value)))


db: DB
http: httpx.AsyncClient
RUNNING: dict[int, "Recorder"] = {}
SUP = ha.Supervisor(CFG["supervisor_url"], os.environ.get("SUPERVISOR_TOKEN"))
HA_LANG: str | None = None
HA_KICK: asyncio.Event | None = None


def apply_language() -> None:
    s = db.setting("language")
    i18n.set_lang(s if s in ("de", "en") else (HA_LANG or os.environ.get("STREAM_RECORDER_LANG") or "en"))


# --------------------------------------------------------------------------
# Zugriff: Ingress (Home Assistant meldet an) oder direkter Port mit eigenem Login
# --------------------------------------------------------------------------
SECRET_FILE = DATA / "secret.key"
COOKIE = "sr_session"
FAILS: dict[str, list[float]] = {}


def b64e(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).decode().rstrip("=")


def b64d(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def direct_enabled() -> bool:
    return bool(CFG["username"] and CFG["password"])


def secret() -> bytes:
    if not SECRET_FILE.exists():
        DATA.mkdir(parents=True, exist_ok=True)
        SECRET_FILE.write_bytes(secrets.token_bytes(32))
        SECRET_FILE.chmod(0o600)
    return SECRET_FILE.read_bytes()


def pw_version() -> str:
    """Ändert sich mit Benutzer/Passwort – alte Anmeldungen werden damit ungültig."""
    return hashlib.sha256(f"{CFG['username']}\0{CFG['password']}".encode()).hexdigest()[:12]


def make_session(user: str, seconds: int) -> str:
    payload = b64e(json.dumps({"u": user, "exp": int(time.time() + seconds), "pv": pw_version()}).encode())
    sig = b64e(hmac.new(secret(), payload.encode(), hashlib.sha256).digest())
    return f"{payload}.{sig}"


def cookie_user(raw: str | None) -> str | None:
    if not raw or not direct_enabled() or "." not in raw:
        return None
    payload, sig = raw.rsplit(".", 1)
    good = b64e(hmac.new(secret(), payload.encode(), hashlib.sha256).digest())
    if not hmac.compare_digest(sig, good):
        return None
    try:
        d = json.loads(b64d(payload))
    except ValueError:
        return None
    if d.get("exp", 0) < time.time() or d.get("u") != CFG["username"] or d.get("pv") != pw_version():
        return None
    return d["u"]


def session_user(request: Request) -> str | None:
    return getattr(request.state, "user", None)


def is_ingress(scope) -> bool:
    server = scope.get("server") or (None, None)
    return server[1] == CFG["ingress_port"]


PUBLIC_PATHS = {"/login", "/favicon.svg", "/favicon.ico"}
SEC_HEADERS = [(b"x-content-type-options", b"nosniff"), (b"referrer-policy", b"same-origin")]


class AuthMiddleware:
    """Ingress-Port: nur der Supervisor darf verbinden, Home Assistant hat schon angemeldet.
    Direkter Port: alles außer /login braucht eine gültige Sitzung.
    Ändernde API-Aufrufe brauchen immer den Header X-Recorder (Schutz gegen Cross-Site-Requests)."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        ingress = is_ingress(scope)
        frame = b"SAMEORIGIN" if ingress else b"DENY"

        async def send_sec(msg):
            if msg["type"] == "http.response.start":
                msg["headers"] = list(msg.get("headers", [])) + SEC_HEADERS + [(b"x-frame-options", frame)]
            await send(msg)

        req = Request(scope)
        state = scope.setdefault("state", {})
        path = scope["path"]
        if ingress:
            client = (scope.get("client") or ("?",))[0].removeprefix("::ffff:")
            if client not in CFG["trusted_proxies"]:
                log.warning("Rejected request on the ingress port from %s", client)
                return await PlainTextResponse("Forbidden", status_code=403)(scope, receive, send_sec)
            user = (req.headers.get("x-remote-user-display-name") or req.headers.get("x-remote-user-name")
                    or "Home Assistant").strip()[:64]
            state.update(user=user, auth="ingress")
        else:
            state["auth"] = "login"
            if path in PUBLIC_PATHS:
                return await self.app(scope, receive, send_sec)
            user = cookie_user(req.cookies.get(COOKIE))
            if not user:
                if path.startswith("/api/"):
                    resp = JSONResponse({"error": _("nicht angemeldet")}, status_code=401)
                else:
                    resp = RedirectResponse("/login", status_code=303)
                return await resp(scope, receive, send_sec)
            state["user"] = user
        if scope["method"] not in ("GET", "HEAD") and path != "/login" and req.headers.get("x-recorder") != "1":
            return await JSONResponse({"error": _("Anfrage abgelehnt")}, status_code=403)(scope, receive, send_sec)
        await self.app(scope, receive, send_sec)


def client_ip(request: Request) -> str:
    return (request.client.host if request.client else "?").removeprefix("::ffff:")


async def login_page(request: Request) -> Response:
    if request.state.auth == "ingress":
        return RedirectResponse("./", status_code=303)
    if request.method == "POST":
        ip = client_ip(request)
        now = time.time()
        FAILS[ip] = [t for t in FAILS.get(ip, []) if now - t < 300]
        if len(FAILS[ip]) >= 5:
            log.warning("Login locked for %s (too many failed attempts)", ip)
            return RedirectResponse("/login?e=locked", status_code=303)
        if not direct_enabled():
            return RedirectResponse("/login", status_code=303)
        form = parse_qs((await request.body()).decode("utf-8", "replace"))
        user = (form.get("username") or [""])[0].strip()
        pw = (form.get("password") or [""])[0]
        remember = bool(form.get("remember"))
        if hmac.compare_digest(user.encode(), CFG["username"].encode()) and \
                hmac.compare_digest(pw.encode(), CFG["password"].encode()):
            FAILS.pop(ip, None)
            secs = CFG["remember_days"] * 86400 if remember else CFG["session_hours"] * 3600
            resp = RedirectResponse("/", status_code=303)
            resp.set_cookie(COOKIE, make_session(user, secs), max_age=secs if remember else None,
                            httponly=True, samesite="lax")
            log.info("Login %s from %s", user, ip)
            return resp
        FAILS[ip].append(now)
        log.warning("Login failed for '%s' from %s", user[:40], ip)
        return RedirectResponse("/login?e=1", status_code=303)

    if cookie_user(request.cookies.get(COOKIE)):
        return RedirectResponse("/", status_code=303)
    err = request.query_params.get("e")
    msg = ""
    if not direct_enabled():
        msg = _("Der direkte Zugriff ist nicht eingerichtet. Benutzername und Passwort in der "
                "App-Konfiguration eintragen oder die App über die Seitenleiste von Home Assistant öffnen.")
    elif err == "locked":
        msg = _("Zu viele Fehlversuche. Bitte 5 Minuten warten.")
    elif err:
        msg = _("Benutzername oder Passwort falsch.")
    html = i18n.translate_html((WEB / "login.html").read_text("utf-8"), i18n.LOGIN_EN)
    block = (f'<p class="alert" role="alert"><svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="9"/>'
             f'<path d="M12 8v5M12 16.5v.5"/></svg>{escape(msg)}</p>') if msg else ""
    return HTMLResponse(html.replace("<!--ERROR-->", block), headers={"cache-control": "no-store"})


async def logout(request: Request) -> Response:
    resp = JSONResponse({"ok": True})
    resp.delete_cookie(COOKIE)
    return resp


def escape(s: str) -> str:
    return (str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            .replace('"', "&quot;").replace("'", "&#39;"))


# --------------------------------------------------------------------------
# Stream öffnen (Weiterleitungen, Playlists, ICY)
# --------------------------------------------------------------------------
class StreamError(Exception):
    pass


PLAYLIST_TYPES = {"audio/x-mpegurl", "audio/mpegurl", "application/x-mpegurl", "audio/x-scpls",
                  "audio/scpls", "application/pls+xml", "application/xspf+xml", "text/plain",
                  "application/vnd.apple.mpegurl", "text/uri-list"}
EXTENSIONS = {"audio/mpeg": "mp3", "audio/mp3": "mp3", "audio/mpeg3": "mp3", "audio/x-mpeg": "mp3",
              "audio/aac": "aac", "audio/aacp": "aac", "audio/x-aac": "aac",
              "audio/ogg": "ogg", "application/ogg": "ogg", "audio/opus": "opus"}


def content_type(resp: httpx.Response) -> str:
    return resp.headers.get("content-type", "").split(";")[0].strip().lower()


def parse_playlist(text: str, base: str) -> str | None:
    if "#EXT-X-TARGETDURATION" in text or "#EXT-X-STREAM-INF" in text:
        raise StreamError(_("HLS-Streams werden nicht unterstützt"))
    for m in re.finditer(r"(?im)^\s*File\d+\s*=\s*(\S+)", text):          # .pls
        return urljoin(base, m.group(1))
    for m in re.finditer(r"<location>\s*([^<\s]+)\s*</location>", text):   # .xspf
        return urljoin(base, m.group(1))
    for line in text.splitlines():                                          # .m3u
        line = line.strip()
        if line and not line.startswith("#") and re.match(r"(?i)^(https?://|/|[\w.-]+/)", line):
            return urljoin(base, line)
    return None


@asynccontextmanager
async def open_stream(url: str, depth: int = 0):
    """Öffnet einen Audio-Stream. Folgt Weiterleitungen und löst Playlists auf."""
    if depth > 4:
        raise StreamError(_("Zu viele verschachtelte Playlists"))
    req = http.build_request("GET", url, headers={"Icy-MetaData": "1", "User-Agent": UA, "Accept": "*/*"})
    try:
        resp = await http.send(req, stream=True, follow_redirects=True)
    except httpx.TimeoutException as e:
        raise StreamError(_("Zeitüberschreitung beim Verbinden ({err})", err=type(e).__name__)) from e
    except httpx.HTTPError as e:
        raise StreamError(_("Verbindung fehlgeschlagen ({err})", err=f"{type(e).__name__}: {e}")) from e
    try:
        if resp.status_code >= 400:
            raise StreamError(f"HTTP {resp.status_code}")
        ct = content_type(resp)
        if ct in PLAYLIST_TYPES or ct.startswith("text/") and ct != "text/html":
            body = bytearray()
            async for chunk in resp.aiter_raw():
                body += chunk
                if len(body) > 256_000:
                    break
            target = parse_playlist(body.decode("utf-8", "replace"), str(resp.url))
            if not target:
                raise StreamError(_("Playlist enthält keine Stream-URL"))
            await resp.aclose()
            async with open_stream(target, depth + 1) as inner:
                yield inner
            return
        if ct == "text/html":
            raise StreamError(_("Antwort ist eine Webseite, kein Audio-Stream"))
        yield resp
    finally:
        await resp.aclose()


def parse_icy_title(raw: bytes) -> str | None:
    raw = raw.rstrip(b"\0")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        text = raw.decode("latin-1")
    m = re.search(r"StreamTitle='(.*?)';", text, re.S)
    return m.group(1).strip() if m else None


class IcyReader:
    """Trennt ICY-Metadaten (alle icy-metaint Bytes) vom Audio."""

    def __init__(self, metaint: int):
        self.metaint = metaint
        self.left = metaint
        self.state = "audio"
        self.meta_len = 0
        self.meta = bytearray()

    def feed(self, data: bytes) -> tuple[bytes, list[str]]:
        if not self.metaint:
            return data, []
        out, titles, i, n = bytearray(), [], 0, len(data)
        while i < n:
            if self.state == "audio":
                k = min(self.left, n - i)
                out += data[i:i + k]
                i += k
                self.left -= k
                if self.left == 0:
                    self.state = "len"
            elif self.state == "len":
                self.meta_len = data[i] * 16
                i += 1
                if self.meta_len == 0:
                    self.state, self.left = "audio", self.metaint
                else:
                    self.state = "meta"
                    self.meta.clear()
            else:
                k = min(self.meta_len - len(self.meta), n - i)
                self.meta += data[i:i + k]
                i += k
                if len(self.meta) == self.meta_len:
                    t = parse_icy_title(bytes(self.meta))
                    if t is not None:
                        titles.append(t)
                    self.state, self.left = "audio", self.metaint
        return bytes(out), titles


async def test_stream(url: str, seconds: float = 6) -> dict:
    """Verbindet kurz und liefert, was der Stream über sich verrät."""
    t0 = time.time()
    info = {"url": url}
    async with open_stream(url) as resp:
        ct = content_type(resp)
        h = resp.headers
        info.update(final_url=str(resp.url), status=resp.status_code, content_type=ct,
                    extension=EXTENSIONS.get(ct, "mp3"), name=h.get("icy-name"),
                    genre=h.get("icy-genre"), description=h.get("icy-description"),
                    bitrate=_int(h.get("icy-br", "").split(",")[0]),
                    metaint=_int(h.get("icy-metaint")), server=h.get("server"))
        reader = IcyReader(info["metaint"] or 0)
        audio, title, first = 0, None, None
        async for chunk in resp.aiter_raw():
            first = first or time.time()
            a, titles = reader.feed(chunk)
            audio += len(a)
            if titles:
                title = titles[-1]
            if time.time() - t0 > seconds or (title is not None and audio > 64_000):
                break
        dt = max(0.001, time.time() - (first or t0))
        info.update(bytes=audio, seconds=round(dt, 1), title=title,
                    ok=audio > 0 and ct != "text/html")
    info["elapsed"] = round(time.time() - t0, 1)
    return info


def _int(v) -> int | None:
    try:
        return int(str(v).strip())
    except (TypeError, ValueError):
        return None


# --------------------------------------------------------------------------
# Dateinamen
# --------------------------------------------------------------------------
def clean(s: str, limit: int = 80) -> str:
    s = re.sub(r'[\x00-\x1f/\\:*?"<>|]+', "-", str(s or "")).strip(" .-")
    s = re.sub(r"\s+", " ", s)
    return s[:limit].strip() or _("ohne Titel")


class _Fmt(dict):
    def __missing__(self, key):
        return "{" + key + "}"


def rec_values(job: dict) -> dict:
    dt = datetime.fromtimestamp(job["start"])
    return i18n.with_aliases({
        "sender": clean(job.get("sender") or "Stream"), "titel": clean(job["title"]),
        "moderation": clean(job.get("host") or ""), "datum": dt.strftime("%Y-%m-%d"),
        "zeit": dt.strftime("%H%M"), "jahr": dt.strftime("%Y"), "monat": dt.strftime("%m"),
        "tag": dt.strftime("%d"), "wochentag": i18n.weekday(dt.weekday())})


def target_path(job: dict, ext: str) -> Path:
    vals = _Fmt(rec_values(job))
    folder = rec_dir()
    for part in db.setting("folder_pattern").format_map(vals).split("/"):
        if part.strip():
            folder = folder / clean(part)
    name = clean(db.setting("file_pattern").format_map(vals), 150)
    p = folder / f"{name}.{ext}"
    i = 2
    while p.exists() or p.with_name(p.name + ".part").exists():
        p = folder / f"{name} ({i}).{ext}"
        i += 1
    return p


# --------------------------------------------------------------------------
# ID3-Tags
# --------------------------------------------------------------------------
def fmt_hms(s: float) -> str:
    s = int(s)
    return f"{s // 3600:02d}:{s % 3600 // 60:02d}:{s % 60:02d}"


def mutagen_hint() -> str:
    return _("Python-Paket 'mutagen' fehlt – ID3-Tags werden nicht geschrieben.")


def mutagen_ok() -> bool:
    try:
        import mutagen.id3  # noqa: F401
        return True
    except ImportError:
        return False


def write_tags(path: Path, rec: "Recorder", length_s: float) -> list[str]:
    from mutagen.id3 import (COMM, CTOC, ID3, TALB, TCON, TDRC, TIT2, TPE1, TXXX, CHAP,
                             CTOCFlags, ID3NoHeaderError)
    try:
        tags = ID3(path)
        had = [k[:4] for k in tags.keys()]
    except ID3NoHeaderError:
        tags, had = ID3(), []
    job = rec.job
    done = []
    tags.setall("TIT2", [TIT2(encoding=3, text=job["title"])]); done.append(_("Titel"))
    artist = job.get("host") or rec.info.get("name") or job.get("sender")
    if artist:
        tags.setall("TPE1", [TPE1(encoding=3, text=artist)]); done.append(_("Interpret"))
    if job.get("sender"):
        tags.setall("TALB", [TALB(encoding=3, text=job["sender"])]); done.append(_("Album"))
    tags.setall("TDRC", [TDRC(encoding=3, text=datetime.fromtimestamp(job["start"]).strftime("%Y-%m-%dT%H:%M"))])
    done.append(_("Datum"))
    if rec.info.get("genre"):
        tags.setall("TCON", [TCON(encoding=3, text=rec.info["genre"])]); done.append(_("Genre"))
    tags.setall("TXXX:Stream", [TXXX(encoding=3, desc="Stream", text=job["url"])])
    tracks = [(o, t) for o, t in rec.tracks if t and o < length_s + 5]
    if tracks:
        lines = "\n".join(f"{fmt_hms(o)}  {t}" for o, t in tracks)
        # Beschreibung "Titelliste" ist das Dateiformat (wird so wieder gelesen), nicht übersetzen
        tags.setall("COMM", [COMM(encoding=3, lang="deu", desc=lib.TRACKLIST_DESC, text=lines)])
        done.append(_("Titelliste ({n})", n=len(tracks)))
        tags.delall("CHAP")
        tags.delall("CTOC")
        ids = []
        for i, (o, t) in enumerate(tracks[:500]):
            end = tracks[i + 1][0] if i + 1 < len(tracks) else length_s
            cid = f"ch{i}"
            ids.append(cid)
            tags.add(CHAP(element_id=cid, start_time=int(o * 1000), end_time=int(max(o, end) * 1000),
                          sub_frames=[TIT2(encoding=3, text=t)]))
        tags.add(CTOC(element_id="toc", flags=CTOCFlags.TOP_LEVEL | CTOCFlags.ORDERED,
                      child_element_ids=ids, sub_frames=[TIT2(encoding=3, text="Titel")]))
        done.append(_("Kapitel"))
    tags.save(path, v2_version=4)
    if had:
        done.append(_("Stream-Tags übernommen"))
    return done


def audio_length(path: Path, rec: "Recorder") -> float:
    try:
        from mutagen import File
        f = File(path)
        if f is not None and f.info and f.info.length:
            return float(f.info.length)
    except Exception:
        pass
    if rec.bitrate:
        return rec.bytes * 8 / (rec.bitrate * 1000)
    return rec.connected_s


# --------------------------------------------------------------------------
# Aufnahme
# --------------------------------------------------------------------------
class Recorder:
    def __init__(self, job: dict, w_start: float, w_end: float):
        self.job = job
        self.w_start, self.w_end = w_start, w_end
        self.started = time.time()
        self.bytes = 0
        self.connected_s = 0.0
        self.sessions = 0
        self.failures = 0
        self.tracks: list[tuple[float, str]] = []
        self.info: dict = {}
        self.bitrate: int | None = None
        self.last_error: str | None = None
        self.stopped_by: str | None = None
        self.path: Path | None = None
        self.part: Path | None = None
        self.fh = None
        self.hist_id: int | None = None
        self.task: asyncio.Task | None = None
        self.session_task: asyncio.Task | None = None
        self._rate = (time.time(), 0)
        self.kbps = 0.0

    # --- Steuerung ---------------------------------------------------------
    def stop(self, reason: str) -> None:
        if self.stopped_by:
            return
        self.stopped_by = reason
        if self.session_task and not self.session_task.done():
            self.session_task.cancel()

    def snapshot(self) -> dict:
        total = max(1.0, self.w_end - self.w_start)
        elapsed = time.time() - self.w_start
        return {"bytes": self.bytes, "elapsed_s": round(min(elapsed, total)), "total_s": round(total),
                "progress": max(0.0, min(1.0, elapsed / total)), "kbps": round(self.kbps),
                "title": self.tracks[-1][1] if self.tracks else None,
                "connected": bool(self.session_task and not self.session_task.done() and self.fh),
                "reconnects": max(0, self.sessions - 1), "error": self.last_error,
                "file": str(self.path) if self.path else None}

    # --- Ablauf ------------------------------------------------------------
    async def run(self) -> None:
        job = self.job
        log.info("Recording starts: #%s '%s' until %s (%s)", job["id"], job["title"],
                 datetime.fromtimestamp(self.w_end).strftime("%H:%M:%S"), job["url"])
        self.hist_id = db.x(
            "INSERT INTO history(job_id,title,host,sender,url,window_start,window_end,started,status,job_start)"
            " VALUES(?,?,?,?,?,?,?,?,'running',?)",
            (job["id"], job["title"], job.get("host"), job.get("sender"), job["url"],
             self.w_start, self.w_end, self.started, job["start"]))
        db.x("UPDATE jobs SET last_window=? WHERE id=?", (self.w_start, job["id"]))
        ha_kick()
        try:
            while not self.stopped_by and time.time() < self.w_end:
                self.session_task = asyncio.create_task(self._session())
                try:
                    await self.session_task
                except asyncio.CancelledError:
                    if self.stopped_by:
                        break
                    raise
                except StreamError as e:
                    self.last_error = str(e)
                except Exception as e:  # httpx-Lesefehler, Timeouts usw.
                    self.last_error = f"{type(e).__name__}: {e}"[:200]
                if self.stopped_by or time.time() >= self.w_end:
                    break
                self.failures += 1
                log.warning("Recording #%s: %s – retrying in %ss", job["id"],
                            self.last_error, CFG["reconnect_delay"])
                await asyncio.sleep(min(CFG["reconnect_delay"], max(0.0, self.w_end - time.time())))
        finally:
            await self._finish()

    async def _session(self) -> None:
        async with open_stream(self.job["url"]) as resp:
            ct = content_type(resp)
            h = resp.headers
            if self.fh is None:
                ext = EXTENSIONS.get(ct, "mp3")
                self.path = target_path(self.job, ext)
                self.path.parent.mkdir(parents=True, exist_ok=True)
                self.part = self.path.with_name(self.path.name + ".part")
                self.fh = open(self.part, "ab")
                self.info = {"content_type": ct, "name": h.get("icy-name"), "genre": h.get("icy-genre"),
                             "final_url": str(resp.url)}
                self.bitrate = _int(h.get("icy-br", "").split(",")[0])
                log.info("Recording #%s: %s, %s kbit/s, file %s", self.job["id"], ct or "?",
                         self.bitrate or "?", self.path)
            self.sessions += 1
            if self.sessions > 1:
                log.info("Recording #%s: reconnected (connection %d)", self.job["id"], self.sessions)
            self.last_error = None
            reader = IcyReader(_int(h.get("icy-metaint")) or 0)
            last = time.time()
            async for chunk in resp.aiter_raw():
                audio, titles = reader.feed(chunk)
                if audio:
                    self.fh.write(audio)
                    self.bytes += len(audio)
                for t in titles:
                    self._track(t)
                now = time.time()
                self.connected_s += now - last
                last = now
                t0, b0 = self._rate
                if now - t0 >= 2:
                    self.kbps = (self.bytes - b0) * 8 / 1000 / (now - t0)
                    self._rate = (now, self.bytes)
                if now >= self.w_end or self.stopped_by:
                    return
            raise StreamError(_("Stream vom Server beendet"))

    def _track(self, title: str) -> None:
        if not title or (self.tracks and self.tracks[-1][1] == title):
            return
        if self.bitrate:
            offset = self.bytes * 8 / (self.bitrate * 1000)
        else:
            offset = self.connected_s
        self.tracks.append((round(offset, 1), title))
        log.debug("Recording #%s: title %s", self.job["id"], title)

    async def _finish(self) -> None:
        ended = time.time()
        if self.fh:
            self.fh.close()
        planned = self.w_end - self.w_start
        length, tags_done, status, msg = 0.0, [], "error", None
        try:
            if self.part and self.part.exists() and self.part.stat().st_size > 0:
                self.part.rename(self.path)
                length = await asyncio.to_thread(audio_length, self.path, self)
                if self.path.suffix == ".mp3" and not mutagen_ok():
                    log.error("ID3 tags for %s not written: Python package 'mutagen' missing", self.path.name)
                    tags_done = [_("nicht geschrieben (mutagen fehlt)")]
                elif self.path.suffix == ".mp3":
                    try:
                        tags_done = await asyncio.to_thread(write_tags, self.path, self, length)
                    except Exception as e:
                        log.exception("Writing ID3 tags for %s failed", self.path)
                        tags_done = [_("Fehler: {err}", err=type(e).__name__)]
            elif self.part and self.part.exists():
                self.part.unlink()
                self.path = None
        except OSError as e:
            log.exception("Finishing the file failed")
            self.last_error = _("Datei: {err}", err=e)

        rec_info = []
        if self.sessions > 1:
            rec_info.append(_("{n} Unterbrechung(en)", n=self.sessions - 1))
        if self.stopped_by:
            status = "aborted"
            msg = _("App beendet") if self.stopped_by == SHUTDOWN else self.stopped_by
        elif self.bytes == 0:
            status = "error"
            msg = self.last_error or _("keine Daten empfangen")
        elif length >= planned * CFG["min_success_ratio"]:
            status = "ok"
        else:
            status = "error"
            msg = _("unvollständig: {a} von {b} min", a=f"{length / 60:.0f}", b=f"{planned / 60:.0f}")
            if self.last_error:
                msg += f" · {self.last_error}"
        if rec_info:
            msg = " · ".join(([msg] if msg else []) + rec_info)
        db.x("UPDATE history SET path=?, ended=?, duration_s=?, bytes=?, status=?, message=?, tags=?,"
             " tracks=?, reconnects=?, content_type=?, bitrate=? WHERE id=?",
             (str(self.path) if self.path else None, ended, length, self.bytes, status, msg,
              ", ".join(tags_done), len(self.tracks), max(0, self.sessions - 1),
              self.info.get("content_type"), self.bitrate, self.hist_id))
        log.info("Recording #%s finished: %s, %.1f min, %.1f MB%s", self.job["id"], status,
                 length / 60, self.bytes / 1e6, f" ({msg})" if msg else "")
        RUNNING.pop(self.job["id"], None)
        if self.stopped_by != SHUTDOWN:
            finalize_job(self.job["id"])
            ha_event_finished({"title": self.job["title"], "sender": self.job.get("sender"), "status": status,
                               "message": msg, "file": str(self.path) if self.path else None,
                               "duration_s": round(length), "started": iso(self.started), "ended": iso(ended)})
        ha_kick()


SHUTDOWN = "__shutdown__"


def next_start(start: float, repeat: str, after: float) -> float | None:
    if repeat not in ("daily", "weekdays", "weekly"):
        return None
    dt = datetime.fromtimestamp(start)
    while dt.timestamp() <= after:
        if repeat == "weekly":
            dt += timedelta(days=7)
        else:
            dt += timedelta(days=1)
            while repeat == "weekdays" and dt.weekday() >= 5:
                dt += timedelta(days=1)
    return dt.timestamp()


def finalize_job(job_id: int) -> None:
    """Einmalige Aufträge löschen, wiederkehrende auf den nächsten Termin setzen."""
    job = db.one("SELECT * FROM jobs WHERE id=?", (job_id,))
    if not job:
        return
    nxt = next_start(job["start"], job["repeat"], time.time() - job["pre_min"] * 60 + 1)
    if nxt:
        db.x("UPDATE jobs SET start=? WHERE id=?", (nxt, job_id))
    else:
        db.x("DELETE FROM jobs WHERE id=?", (job_id,))


def window(job: dict) -> tuple[float, float]:
    return (job["start"] - job["pre_min"] * 60,
            job["start"] + (job["duration_min"] + job["post_min"]) * 60)


# --------------------------------------------------------------------------
# Scheduler
# --------------------------------------------------------------------------
def start_recording(job: dict, w_start: float, w_end: float) -> None:
    rec = Recorder(job, w_start, w_end)
    RUNNING[job["id"]] = rec
    rec.task = asyncio.create_task(rec.run())


def tick() -> None:
    now = time.time()
    for job in db.q("SELECT * FROM jobs ORDER BY start"):
        if job["id"] in RUNNING:
            continue
        w_start, w_end = window(job)
        if now >= w_end:
            if job["last_window"] != w_start:
                log.warning("Job #%s '%s' missed", job["id"], job["title"])
                msg = _("verpasst – App oder Home Assistant war nicht aktiv")
                db.x("INSERT INTO history(job_id,title,host,sender,url,window_start,window_end,started,"
                     "ended,duration_s,status,message) VALUES(?,?,?,?,?,?,?,?,?,0,'error',?)",
                     (job["id"], job["title"], job["host"], job["sender"], job["url"], w_start, w_end,
                      w_start, w_end, msg))
                ha_event_finished({"title": job["title"], "sender": job["sender"], "status": "error",
                                   "message": msg, "file": None, "duration_s": 0,
                                   "started": iso(w_start), "ended": iso(w_end)})
            finalize_job(job["id"])
            ha_kick()
        elif now >= w_start:
            start_recording(job, w_start, w_end)


async def scheduler() -> None:
    last_purge = 0.0
    while True:
        try:
            tick()
            if time.time() - last_purge > 3600:
                last_purge = time.time()
                n = db.con.execute("DELETE FROM history WHERE ended < ? AND status != 'running'",
                                   (time.time() - CFG["history_days"] * 86400,)).rowcount
                if n:
                    log.info("%d old entries removed from the list", n)
                await asyncio.to_thread(trash_auto_empty)
        except Exception:
            log.exception("Scheduler error")
        await asyncio.sleep(1)


# --------------------------------------------------------------------------
# Home Assistant: Entitäten, Ereignisse, Befehle
# --------------------------------------------------------------------------
def iso(ts: float | None) -> str | None:
    return datetime.fromtimestamp(ts).astimezone().isoformat(timespec="seconds") if ts else None


def ha_kick() -> None:
    if HA_KICK is not None:
        HA_KICK.set()


def ha_event_finished(data: dict) -> None:
    if not SUP.enabled:
        return
    asyncio.get_event_loop().create_task(SUP.fire("stream_recorder_finished", data))
    if data["status"] == "error":
        asyncio.get_event_loop().create_task(SUP.fire("stream_recorder_error", data))


def ha_states() -> dict[str, tuple[str, dict]]:
    recs = list(RUNNING.values())
    jobs = [j for j in db.q("SELECT * FROM jobs ORDER BY start") if j["id"] not in RUNNING]
    upcoming = sorted(jobs, key=lambda j: window(j)[0])
    nxt = upcoming[0] if upcoming else None
    last = db.one("SELECT * FROM history WHERE status != 'running' ORDER BY COALESCE(ended, started) DESC LIMIT 1")
    free = disk_free(storage())
    icon = "mdi:radio"
    out = {
        "binary_sensor.stream_recorder_recording": ("on" if recs else "off", {
            "friendly_name": _("Stream-Recorder Aufnahme läuft"), "icon": "mdi:record-rec",
            "count": len(recs), "titles": [r.job["title"] for r in recs],
            "senders": [r.job.get("sender") for r in recs],
            "current_track": recs[0].tracks[-1][1] if recs and recs[0].tracks else None,
            "kbps": round(sum(r.kbps for r in recs)),
            "ends": iso(max(r.w_end for r in recs)) if recs else None}),
        "sensor.stream_recorder_next_recording": (iso(window(nxt)[0]) if nxt else "unknown", {
            "friendly_name": _("Stream-Recorder nächste Aufnahme"), "device_class": "timestamp", "icon": icon,
            "title": nxt["title"] if nxt else None, "sender": nxt["sender"] if nxt else None,
            "start": iso(nxt["start"]) if nxt else None, "duration_min": nxt["duration_min"] if nxt else None,
            "repeat": nxt["repeat"] if nxt else None}),
        "sensor.stream_recorder_last_recording": (last["status"] if last else "unknown", {
            "friendly_name": _("Stream-Recorder letzte Aufnahme"), "icon": icon,
            "title": last["title"] if last else None, "sender": last["sender"] if last else None,
            "message": last["message"] if last else None, "file": last["path"] if last else None,
            "started": iso(last["started"]) if last else None, "ended": iso(last["ended"]) if last else None,
            "duration_min": round((last["duration_s"] or 0) / 60, 1) if last else None}),
        "sensor.stream_recorder_planned": (str(len(jobs)), {
            "friendly_name": _("Stream-Recorder geplante Aufträge"), "icon": "mdi:calendar-clock",
            "unit_of_measurement": _("Aufträge"), "state_class": "measurement",
            "next": [{"title": j["title"], "start": iso(j["start"]), "sender": j["sender"]} for j in upcoming[:5]]}),
        "sensor.stream_recorder_disk_free": (f"{free / 1e9:.1f}" if free is not None else "unknown", {
            "friendly_name": _("Stream-Recorder Speicher frei"), "icon": "mdi:harddisk",
            "unit_of_measurement": "GB", "device_class": "data_size", "state_class": "measurement",
            "path": str(storage())}),
    }
    return out


async def ha_loop() -> None:
    """Entitäten aktuell halten (bei Änderung sofort, sonst alle 15 s), Sprache von HA holen."""
    global HA_LANG
    last_force = last_lang = 0.0
    while True:
        try:
            if time.time() - last_lang > 3600:
                last_lang = time.time()
                lang = await SUP.language()
                if lang and lang != HA_LANG:
                    HA_LANG = lang
                    apply_language()
                    log.info("Home Assistant language: %s", lang)
            force = time.time() - last_force > 600       # nach HA-Neustart wieder anlegen
            if force:
                last_force = time.time()
            await SUP.set_states(ha_states(), force=force)
        except Exception:
            log.exception("Home Assistant update failed")
        try:
            await asyncio.wait_for(HA_KICK.wait(), timeout=15)
        except asyncio.TimeoutError:
            pass
        HA_KICK.clear()


def find_sender(name: str) -> dict | None:
    name = str(name or "").strip().lower()
    if not name:
        return None
    for s in db.q("SELECT * FROM senders"):
        if s["name"].strip().lower() == name:
            return s
    return None


async def on_stdin(value) -> None:
    """Befehle über die Aktion hassio.app_stdin, z. B.
    {"action": "record", "sender": "sunshine live Lounge", "duration": 60, "title": "Sofort"}
    {"action": "stop"}   bzw. {"action": "stop", "title": "Sofort"}"""
    try:
        if isinstance(value, str):
            value = {"action": value.strip()}
        if not isinstance(value, dict):
            raise ValueError("input must be an object")
        action = str(value.get("action") or value.get("command") or "").strip().lower()
        log.info("Command from Home Assistant: %s", json.dumps(value, ensure_ascii=False)[:300])
        if action in ("record", "record_now", "start"):
            d = {"title": value.get("title") or _("Sofortaufnahme"),
                 "duration_min": value.get("duration_min") or value.get("duration") or 60}
            if value.get("url"):
                d.update(url=value["url"], sender=value.get("sender") or "")
            else:
                s = find_sender(value.get("sender")) if value.get("sender") else (db.q(
                    "SELECT * FROM senders ORDER BY name COLLATE NOCASE LIMIT 1") or [None])[0]
                if not s:
                    raise ValueError(f"sender not found: {value.get('sender')}")
                d["sender_id"] = s["id"]
            jid, e = record_now(d)
            if e:
                raise ValueError(e)
            log.info("Recording started by Home Assistant (job #%s)", jid)
        elif action == "stop":
            title, sender = value.get("title"), value.get("sender")
            recs = [r for r in RUNNING.values() if (not title or r.job["title"] == title)
                    and (not sender or (r.job.get("sender") or "").lower() == str(sender).lower())]
            for r in recs:
                r.stop(_("abgebrochen von {user}", user="Home Assistant"))
            log.info("Stopped by Home Assistant: %d recording(s)", len(recs))
        else:
            raise ValueError(f"unknown action '{action}' (record, stop)")
    except Exception as e:
        log.warning("Command from Home Assistant rejected: %s", e)


# --------------------------------------------------------------------------
# API
# --------------------------------------------------------------------------
def err(msg: str, code: int = 400) -> JSONResponse:
    return JSONResponse({"error": msg}, status_code=code)


async def body_json(request: Request) -> dict:
    try:
        d = await request.json()
        return d if isinstance(d, dict) else {}
    except ValueError:
        return {}


def parse_local(s: str) -> float:
    return datetime.fromisoformat(s).timestamp()


def job_out(j: dict) -> dict:
    w_start, w_end = window(j)
    out = dict(j, window_start=w_start, window_end=w_end, repeat_label=_(REPEATS.get(j["repeat"], j["repeat"])))
    rec = RUNNING.get(j["id"])
    out["running"] = rec.snapshot() if rec else None
    return out


def disk_free(path: Path) -> int | None:
    p = path
    while not p.exists() and p != p.parent:
        p = p.parent
    try:
        return shutil.disk_usage(p).free
    except OSError:
        return None


def warnings_out() -> list[str]:
    out = []
    if not mutagen_ok():
        out.append(mutagen_hint())
    for kind, val in CFG["warnings"]:
        if kind == "storage":
            out.append(_("Speicherort „{path}“ ist ungültig (erlaubt: unter /media oder /share). "
                         "Verwendet wird {default}.", path=val, default=DEFAULTS["storage_path"]))
    if not storage().is_dir():
        out.append(_("Speicherordner {path} ist nicht erreichbar.", path=storage()))
    return out


async def api_state(request: Request) -> JSONResponse:
    now = time.time()
    jobs = [job_out(j) for j in db.q("SELECT * FROM jobs ORDER BY start")]
    since = now - 30 * 86400
    s = db.one("SELECT SUM(status='ok') ok, SUM(status='aborted') aborted, SUM(status='error') errors,"
               " COALESCE(SUM(duration_s),0) secs, COALESCE(SUM(bytes),0) bytes"
               " FROM history WHERE ended >= ?", (since,))
    upcoming = [j for j in jobs if not j["running"]]
    t = db.one("SELECT COUNT(*) n, COALESCE(SUM(size),0) b FROM trash")
    return JSONResponse({
        "version": __version__, "now": now, "user": session_user(request), "auth": request.state.auth,
        "lang": i18n.LANG, "jobs": jobs,
        "stats": {"planned": len(upcoming), "next": upcoming[0]["window_start"] if upcoming else None,
                  "running": len(RUNNING), "kbps": round(sum(r.kbps for r in RUNNING.values())),
                  "ok": s["ok"] or 0, "aborted": s["aborted"] or 0, "errors": s["errors"] or 0,
                  "hours": s["secs"] / 3600, "bytes": s["bytes"], "disk_free": disk_free(storage())},
        "trash": {"count": t["n"], "bytes": t["b"]},
        "settings": settings_out(),
        "warnings": warnings_out(),
        "donate": DONATE_URL,
    })


async def api_history(request: Request) -> JSONResponse:
    status = request.query_params.get("status")
    limit = max(1, min(int(request.query_params.get("limit", 200)), 2000))
    where, args = "status != 'running'", []
    if status in ("ok", "aborted", "error"):
        where += " AND status=?"
        args.append(status)
    rows = db.q(f"SELECT * FROM history WHERE {where} ORDER BY started DESC LIMIT {limit}", tuple(args))
    for r in rows:
        r["planned_s"] = (r["window_end"] or 0) - (r["window_start"] or 0)
        r["file_exists"] = bool(r["path"] and Path(r["path"]).exists())
        r["file_name"] = Path(r["path"]).name if r["path"] else None
    return JSONResponse(rows)


async def api_history_file(request: Request) -> Response:
    r = db.one("SELECT path FROM history WHERE id=?", (int(request.path_params["id"]),))
    if not r or not r["path"] or not Path(r["path"]).exists():
        return err(_("Datei nicht vorhanden"), 404)
    p = Path(r["path"])
    return FileResponse(p, filename=p.name)


async def api_history_delete(request: Request) -> JSONResponse:
    db.x("DELETE FROM history WHERE id=? AND status != 'running'", (int(request.path_params["id"]),))
    return JSONResponse({"ok": True})


def validate_job(d: dict) -> tuple[dict | None, str | None]:
    title = str(d.get("title") or "").strip()
    if not title:
        return None, _("Titel fehlt")
    url, sender = str(d.get("url") or "").strip(), str(d.get("sender") or "").strip()
    if d.get("sender_id"):
        s = db.one("SELECT * FROM senders WHERE id=?", (int(d["sender_id"]),))
        if not s:
            return None, _("Sender nicht gefunden")
        url, sender = s["url"], s["name"]
    if not re.match(r"(?i)^https?://", url):
        return None, _("Stream-URL muss mit http:// oder https:// beginnen")
    try:
        start = parse_local(str(d.get("start")))
        duration = float(d.get("duration_min"))
        pre = float(d.get("pre_min") if d.get("pre_min") not in (None, "") else db.setting("pre_roll_min"))
        post = float(d.get("post_min") if d.get("post_min") not in (None, "") else db.setting("post_roll_min"))
    except (TypeError, ValueError):
        return None, _("Startzeit, Dauer oder Vor-/Nachlauf ungültig")
    if not 0 < duration <= 24 * 60:
        return None, _("Dauer muss zwischen 1 Minute und 24 Stunden liegen")
    if not (0 <= pre <= 60 and 0 <= post <= 120):
        return None, _("Vorlauf 0–60 min, Nachlauf 0–120 min")
    repeat = d.get("repeat") or "once"
    if repeat not in REPEATS:
        return None, _("Wiederholung ungültig")
    if start + (duration + post) * 60 < time.time() and repeat == "once":
        return None, _("Der Zeitraum liegt komplett in der Vergangenheit")
    return {"title": title[:200], "host": str(d.get("host") or "").strip()[:200] or None,
            "sender": sender[:100] or None, "url": url, "start": start, "duration_min": duration,
            "pre_min": pre, "post_min": post, "repeat": repeat}, None


async def api_jobs_create(request: Request) -> JSONResponse:
    j, e = validate_job(await body_json(request))
    if e:
        return err(e)
    if j["repeat"] != "once" and window(j)[1] < time.time():
        j["start"] = next_start(j["start"], j["repeat"], time.time())
    jid = db.x("INSERT INTO jobs(title,host,sender,url,start,duration_min,pre_min,post_min,repeat,source,created)"
               " VALUES(?,?,?,?,?,?,?,?,?,'manual',?)",
               (j["title"], j["host"], j["sender"], j["url"], j["start"], j["duration_min"],
                j["pre_min"], j["post_min"], j["repeat"], time.time()))
    log.info("Job #%s created: '%s' %s, %s min", jid, j["title"],
             datetime.fromtimestamp(j["start"]).strftime("%Y-%m-%d %H:%M"), j["duration_min"])
    tick()
    ha_kick()
    return JSONResponse({"id": jid})


async def api_jobs_update(request: Request) -> JSONResponse:
    jid = int(request.path_params["id"])
    if jid in RUNNING:
        return err(_("Läuft gerade – erst abbrechen"), 409)
    if not db.one("SELECT id FROM jobs WHERE id=?", (jid,)):
        return err(_("Auftrag nicht gefunden"), 404)
    j, e = validate_job(await body_json(request))
    if e:
        return err(e)
    db.x("UPDATE jobs SET title=?,host=?,sender=?,url=?,start=?,duration_min=?,pre_min=?,post_min=?,"
         "repeat=?,last_window=NULL WHERE id=?",
         (j["title"], j["host"], j["sender"], j["url"], j["start"], j["duration_min"],
          j["pre_min"], j["post_min"], j["repeat"], jid))
    log.info("Job #%s changed", jid)
    ha_kick()
    return JSONResponse({"ok": True})


async def api_jobs_delete(request: Request) -> JSONResponse:
    jid = int(request.path_params["id"])
    rec = RUNNING.get(jid)
    if rec:
        rec.stop(_("abgebrochen von {user}", user=session_user(request)))
        if rec.task:
            await asyncio.wait({rec.task}, timeout=15)
    db.x("DELETE FROM jobs WHERE id=?", (jid,))
    log.info("Job #%s deleted", jid)
    ha_kick()
    return JSONResponse({"ok": True})


async def api_jobs_stop(request: Request) -> JSONResponse:
    jid = int(request.path_params["id"])
    rec = RUNNING.get(jid)
    if not rec:
        return err(_("Läuft nicht"), 409)
    rec.stop(_("abgebrochen von {user}", user=session_user(request)))
    if rec.task:
        await asyncio.wait({rec.task}, timeout=15)
    return JSONResponse({"ok": True})


def record_now(d: dict) -> tuple[int | None, str | None]:
    d = dict(d, start=datetime.now().strftime("%Y-%m-%dT%H:%M:%S"), pre_min=0, post_min=0, repeat="once")
    j, e = validate_job(d)
    if e:
        return None, e
    j["start"] = time.time()
    jid = db.x("INSERT INTO jobs(title,host,sender,url,start,duration_min,pre_min,post_min,repeat,source,created)"
               " VALUES(?,?,?,?,?,?,0,0,'once','now',?)",
               (j["title"], j["host"], j["sender"], j["url"], j["start"], j["duration_min"], time.time()))
    tick()
    return jid, None


async def api_record_now(request: Request) -> JSONResponse:
    jid, e = record_now(await body_json(request))
    if e:
        return err(e)
    return JSONResponse({"id": jid})


# --- Sender ---------------------------------------------------------------
async def api_senders(request: Request) -> JSONResponse:
    if request.method == "POST":
        d = await body_json(request)
        name, url = str(d.get("name") or "").strip(), str(d.get("url") or "").strip()
        if not name or not re.match(r"(?i)^https?://", url):
            return err(_("Name und eine http(s)-URL angeben"))
        if d.get("id"):
            db.x("UPDATE senders SET name=?, url=? WHERE id=?", (name[:100], url, int(d["id"])))
        else:
            db.x("INSERT INTO senders(name,url,created) VALUES(?,?,?)", (name[:100], url, time.time()))
        return JSONResponse({"ok": True})
    return JSONResponse(db.q("SELECT * FROM senders ORDER BY name COLLATE NOCASE"))


async def api_senders_delete(request: Request) -> JSONResponse:
    db.x("DELETE FROM senders WHERE id=?", (int(request.path_params["id"]),))
    return JSONResponse({"ok": True})


async def api_stream_test(request: Request) -> JSONResponse:
    url = str((await body_json(request)).get("url") or "").strip()
    if not re.match(r"(?i)^https?://", url):
        return err(_("Bitte eine http(s)-URL angeben"))
    try:
        info = await asyncio.wait_for(test_stream(url), timeout=25)
        log.info("Stream test %s: %s, %s kbit/s, title: %s", url, info.get("content_type"),
                 info.get("bitrate"), info.get("title"))
        return JSONResponse(info)
    except asyncio.TimeoutError:
        return JSONResponse({"ok": False, "url": url, "error": _("Keine Antwort innerhalb von 25 s")})
    except StreamError as e:
        return JSONResponse({"ok": False, "url": url, "error": str(e)})
    except Exception as e:
        log.exception("Stream test failed")
        return JSONResponse({"ok": False, "url": url, "error": f"{type(e).__name__}: {e}"})


# --- Einstellungen ------------------------------------------------------------
SETTING_KEYS = ("folder_pattern", "file_pattern", "pre_roll_min", "post_roll_min", "cut_pattern",
                "cut_min_len", "cut_offset", "cover_lookup", "prune_empty_dirs", "trash_days", "language")


def settings_out() -> dict:
    out = {k: db.setting(k) for k in SETTING_KEYS}
    out.update(storage=str(storage()), record_dir=str(rec_dir()), title_dir=str(title_dir()),
               trash_dir=str(trash_dir()), log_file=CFG["log_file"], data_dir=str(DATA),
               lang=i18n.LANG, ha_lang=HA_LANG, ha=SUP.enabled, ha_ok=SUP.ok,
               direct=direct_enabled(), direct_port=CFG["direct_port"], direct_user=CFG["username"],
               placeholders={"rec": i18n.ui_names(REC_PLACEHOLDERS),
                             "cut": i18n.ui_names(cutter.CUT_PLACEHOLDERS),
                             "rename": i18n.ui_names(lib.PLACEHOLDERS)})
    return out


def check_pattern(v: str, allowed, label: str) -> str | None:
    bad = [x for x in re.findall(r"\{(\w+)\}", v) if x not in i18n.accept(allowed)]
    if bad:
        return _("{label}: unbekannter Platzhalter {names}", label=label, names=", ".join("{" + b + "}" for b in bad))
    return None


async def api_settings(request: Request) -> JSONResponse:
    if request.method == "GET":
        return JSONResponse(settings_out())
    d = await body_json(request)
    if "language" in d:
        v = str(d["language"] or "auto")
        if v not in ("auto", "de", "en"):
            return err(_("Sprache ungültig"))
        db.set_setting("language", v)
        apply_language()
        log.info("Language: %s (%s)", v, i18n.LANG)
    for k, label in (("folder_pattern", _("Unterordner")), ("file_pattern", _("Dateiname"))):
        if k in d:
            v = str(d[k]).strip().strip("/") if k == "folder_pattern" else str(d[k]).strip()
            if k == "file_pattern" and not v:
                return err(_("Dateiname darf nicht leer sein"))
            e = check_pattern(v, REC_PLACEHOLDERS, label)
            if e:
                return err(e)
            db.set_setting(k, v)
    for k in ("pre_roll_min", "post_roll_min"):
        if k in d:
            try:
                db.set_setting(k, max(0.0, min(120.0, float(d[k]))))
            except (TypeError, ValueError):
                return err(_("Vor-/Nachlauf ungültig"))
    if "cut_pattern" in d:
        v = str(d["cut_pattern"] or "").strip().strip("/")
        if not v:
            return err(_("Muster für Einzeltitel darf nicht leer sein"))
        e = check_pattern(v, cutter.CUT_PLACEHOLDERS, _("Einzeltitel"))
        if e:
            return err(e)
        db.set_setting("cut_pattern", v)
    for k, lo, hi, label in (("cut_min_len", 0, 3600, _("Mindestlänge")), ("cut_offset", -120, 120, _("Verschiebung"))):
        if k in d:
            try:
                v = float(d[k])
            except (TypeError, ValueError):
                return err(_("{label} ungültig", label=label))
            if not lo <= v <= hi:
                return err(_("{label} muss zwischen {lo} und {hi} Sekunden liegen", label=label, lo=lo, hi=hi))
            db.set_setting(k, v)
    if "trash_days" in d:
        try:
            v = int(float(d["trash_days"] or 0))
        except (TypeError, ValueError):
            return err(_("Tage für den Papierkorb ungültig"))
        if not 0 <= v <= 3650:
            return err(_("Tage für den Papierkorb: 0 bis 3650"))
        db.set_setting("trash_days", v)
        await asyncio.to_thread(trash_auto_empty)          # neue Frist gilt sofort
    for k in ("cover_lookup", "prune_empty_dirs"):
        if k in d:
            db.set_setting(k, bool(d[k]))
    ha_kick()
    return JSONResponse(settings_out())


async def api_log(request: Request) -> PlainTextResponse:
    n = max(10, min(int(request.query_params.get("lines", 300)), 5000))
    try:
        with open(CFG["log_file"], "rb") as f:
            f.seek(0, 2)
            size = f.tell()
            f.seek(max(0, size - n * 300))
            lines = f.read().decode("utf-8", "replace").splitlines()[-n:]
    except OSError:
        lines = []
    return PlainTextResponse("\n".join(lines), headers={"cache-control": "no-store"})


async def api_log_clear(request: Request) -> JSONResponse:
    p = Path(CFG["log_file"])
    try:
        if p.exists():
            os.truncate(p, 0)
        for old in p.parent.glob(p.name + ".*"):
            old.unlink()
    except OSError as e:
        return err(_("Log leeren fehlgeschlagen: {err}", err=e.strerror or e))
    log.info("Log cleared by %s", session_user(request))
    return JSONResponse({"ok": True})


# --- Aufnahmen und Einzeltitel ----------------------------------------------------
LIB = lib.Library()
COVERS: covers.CoverFinder | None = None


def cover_finder():
    global COVERS
    if COVERS is None:
        COVERS = covers.CoverFinder(DATA, CFG["cover_deezer_url"], CFG["cover_itunes_url"])
    return COVERS


PER_PAGE = (10, 25, 50, 100, 0)          # 0 = alle
SORT_KEYS = {"when": "when", "title": "title", "artist": "artist", "album": "album",
             "duration": "duration", "size": "size", "name": "name"}


def history_by_path() -> dict[str, dict]:
    out = {}
    for h in db.q("SELECT id,title,host,sender,status,message,started,ended,window_start,job_start,path "
                  "FROM history WHERE path IS NOT NULL ORDER BY started"):
        out[os.path.realpath(h["path"])] = h
    return out


def lib_items(force: bool = False) -> list[dict]:
    return LIB.scan(lib_roots(), force=force)


def split_rel(rel: str) -> tuple[Path, str, str]:
    """"record/…" bzw. "title/…" -> (Hauptordner, Präfix, Rest)."""
    rel = str(rel or "")
    pre, _sep, inner = rel.partition("/")
    roots = dict(lib_roots())
    if pre not in roots or not inner:
        raise lib.LibraryError(_("Datei nicht gefunden"))
    return roots[pre], pre, inner


def lib_item(rel: str, detail: bool = False) -> dict:
    base, pre, inner = split_rel(rel)
    p = lib.resolve_in(base, inner)
    if not p.is_file() or p.suffix.lower() not in lib.AUDIO_EXT:
        raise lib.LibraryError(_("Datei nicht gefunden"))
    info = lib.read_info(p, detail=detail)
    return dict(info, path=rel, name=p.name, folder=folder_of(rel), abs=str(p))


def folder_of(rel: str) -> str:
    inner = str(rel).partition("/")[2]
    f = str(Path(inner).parent)
    return "" if f == "." else f


def item_kind(it: dict) -> str:
    if it.get("cut_mark") == cutter.CUT_MARK or str(it["path"]).startswith("title/"):
        return "track"
    return "recording"


def rel_of(p: Path) -> str:
    """Pfad einer Datei so, wie die Oberfläche ihn kennt."""
    p = p.resolve()
    for pre, base in lib_roots():
        b = base.resolve()
        if b in p.parents:
            return f"{pre}/{p.relative_to(b)}"
    raise lib.LibraryError(_("Pfad liegt außerhalb des Speicherordners"))


def item_out(it: dict, hist: dict) -> dict:
    o = {k: it[k] for k in ("path", "name", "size", "mtime", "when", "ext", "duration", "bitrate",
                            "title", "artist", "album", "date", "genre", "tracks", "chapters", "has_cover",
                            "tag_state", "editable", "source")}
    o["folder"] = folder_of(it["path"])
    o["missing"] = [_(lib.TAG_LABELS[k]) for k in it.get("missing", [])]
    o["read_error"] = _("mutagen fehlt") if it.get("read_error") == "mutagen" else it.get("read_error")
    o["kind"] = item_kind(it)
    o["cuttable"] = o["kind"] == "recording" and it["editable"] and max(it["chapters"], it["tracks"]) >= 3
    h = hist.get(os.path.realpath(it["abs"]))
    o["history"] = ({"id": h["id"], "status": h["status"], "message": h["message"], "title": h["title"],
                     "sender": h["sender"], "host": h["host"]} if h else None)
    return o


def lib_lock_check(paths: list[str]) -> None:
    if len(paths) > 2000:
        raise lib.LibraryError(_("Zu viele Dateien auf einmal (max. 2000)"))


def lib_errors(fn):
    async def wrapper(request: Request):
        try:
            return await fn(request)
        except lib.LibraryError as e:
            return err(str(e))
        except PermissionError as e:
            return err(_("Keine Berechtigung: {name}", name=e.filename or e), 403)
        except OSError as e:
            log.exception("File error")
            return err(_("Dateifehler: {err}", err=e.strerror or e))
    return wrapper


@lib_errors
async def api_library(request: Request) -> JSONResponse:
    qp = request.query_params
    if qp.get("rescan") == "1":
        await asyncio.to_thread(trash_sync)
    items = await asyncio.to_thread(lib_items, qp.get("rescan") == "1")
    hist = history_by_path()
    total_all = len(items)
    stats = {"files": total_all, "bytes": sum(i["size"] for i in items),
             "seconds": sum(i["duration"] or 0 for i in items),
             "incomplete": sum(1 for i in items if i["tag_state"] in ("none", "partial"))}
    albums = sorted({i["album"] for i in items if i["album"]}, key=str.lower)
    q = (qp.get("q") or "").strip().lower()
    album = qp.get("album") or ""
    tags = qp.get("tags") or "all"
    kind = qp.get("kind") or "all"
    for i in items:
        i["kind"] = item_kind(i)
    stats["tracks"] = sum(1 for i in items if i["kind"] == "track")
    stats["recordings"] = total_all - stats["tracks"]
    sel = items
    if q:
        words = q.split()
        sel = [i for i in sel if all(w in LIB.search_text.get(i["path"], "") for w in words)]
    if album == "__none__":
        sel = [i for i in sel if not i["album"]]
    elif album:
        sel = [i for i in sel if i["album"] == album]
    if kind in ("recording", "track"):
        sel = [i for i in sel if i["kind"] == kind]
    if tags == "full":
        sel = [i for i in sel if i["tag_state"] == "full"]
    elif tags == "missing":
        sel = [i for i in sel if i["tag_state"] in ("none", "partial")]
    sort = SORT_KEYS.get(qp.get("sort") or "when", "when")
    desc = (qp.get("dir") or ("desc" if sort in ("when", "size", "duration") else "asc")) == "desc"

    def key(i):
        v = i.get(sort)
        if isinstance(v, str):
            return (v == "", v.lower())
        return (v is None, v or 0)
    sel = sorted(sel, key=key, reverse=desc)
    if desc:   # leere Werte trotzdem ans Ende
        sel = [i for i in sel if not key(i)[0]] + [i for i in sel if key(i)[0]]
    try:
        per = int(qp.get("per", 25))
    except ValueError:
        per = 25
    per = per if per in PER_PAGE else 25
    pages = max(1, -(-len(sel) // per)) if per else 1
    try:
        page = min(max(1, int(qp.get("page", 1))), pages)
    except ValueError:
        page = 1
    chunk = sel[(page - 1) * per: page * per] if per else sel
    t = db.one("SELECT COUNT(*) n, COALESCE(SUM(size),0) b FROM trash")
    return JSONResponse({
        "root": str(storage()), "root_exists": rec_dir().is_dir(), "scanned_at": LIB.scanned_at,
        "stats": stats, "albums": albums, "total": len(sel), "page": page, "pages": pages, "per": per,
        "sort": sort, "dir": "desc" if desc else "asc", "all_paths": [i["path"] for i in sel],
        "items": [item_out(i, hist) for i in chunk], "mutagen": lib.mutagen_ok(),
        "rename_pattern": db.setting("rename_pattern"), "undo": undo_info(),
        "rename_placeholders": i18n.ui_names(lib.PLACEHOLDERS),
        "trash": {"count": t["n"], "bytes": t["b"]}})


@lib_errors
async def api_library_item(request: Request) -> JSONResponse:
    it = await asyncio.to_thread(lib_item, request.query_params.get("path", ""), True)
    hist = history_by_path()
    out = item_out(it, hist)
    out.update(comment=it["comment"], tracklist=it["tracklist"], chapter_list=it["chapter_list"])
    h = hist.get(os.path.realpath(it["abs"]))
    if h:
        start = h.get("job_start") or h.get("window_start")
        out["from_job"] = {"title": h["title"] or "", "artist": h["host"] or h["sender"] or "",
                           "album": h["sender"] or "",
                           "date": datetime.fromtimestamp(start).strftime("%Y-%m-%dT%H:%M") if start else ""}
    return JSONResponse(out)


@lib_errors
async def api_library_stream(request: Request) -> Response:
    it = lib_item(request.query_params.get("path", ""))
    p = Path(it["abs"])
    dl = request.query_params.get("dl") == "1"
    return FileResponse(p, media_type=lib.MIME.get(p.suffix.lower(), "application/octet-stream"),
                        filename=p.name, content_disposition_type="attachment" if dl else "inline",
                        headers={"cache-control": "no-store"})


@lib_errors
async def api_library_cover(request: Request) -> Response:
    it = lib_item(request.query_params.get("path", ""))
    c = await asyncio.to_thread(lib.read_cover, Path(it["abs"]))
    if not c:
        return Response(status_code=404)
    cache = "private, max-age=86400" if request.query_params.get("v") else "no-store"
    return Response(c[0], media_type=c[1], headers={"cache-control": cache})


@lib_errors
async def api_library_tracklist(request: Request) -> Response:
    it = await asyncio.to_thread(lib_item, request.query_params.get("path", ""), True)
    if not it["tracklist"]:
        return err(_("Keine Titelliste vorhanden"), 404)
    lines = [f"{it['title'] or it['name']}", f"{it['album']}  {it['date']}".strip(), ""]
    lines += [f"{lib_fmt(t['start'])}  {t['title']}" for t in it["tracklist"]]
    name = Path(it["name"]).stem + " – " + _("Titelliste") + ".txt"
    return PlainTextResponse("\n".join(lines) + "\n", headers={
        "content-disposition": f"attachment; filename*=UTF-8''{quote(name)}"})


def lib_fmt(s: float) -> str:
    s = int(s)
    return f"{s // 3600}:{s % 3600 // 60:02d}:{s % 60:02d}"


@lib_errors
async def api_library_tags(request: Request) -> JSONResponse:
    d = await body_json(request)
    if not lib.mutagen_ok():
        return err(mutagen_hint())
    it = lib_item(str(d.get("path") or ""))
    fields = {k: d.get(k) for k in ("title", "artist", "album", "date", "genre", "comment") if k in d}
    if fields.get("date"):
        v = str(fields["date"]).strip()
        if v and not re.match(r"^\d{4}(-\d{2}(-\d{2}([ T]\d{2}:\d{2}(:\d{2})?)?)?)?$", v):
            return err(_("Datum bitte als JJJJ, JJJJ-MM-TT oder JJJJ-MM-TT HH:MM"))
        fields["date"] = v.replace(" ", "T")
    await asyncio.to_thread(lib.write_tags, Path(it["abs"]), fields, d.get("cover"))
    LIB.invalidate()
    log.info("Tags changed: %s", it["path"])
    return JSONResponse({"ok": True})


@lib_errors
async def api_library_fill(request: Request) -> JSONResponse:
    """Leere Tags aus dem passenden Eintrag unter „Erledigt“ ergänzen."""
    d = await body_json(request)
    paths = list(d.get("paths") or [])
    lib_lock_check(paths)
    if not lib.mutagen_ok():
        return err(mutagen_hint())
    hist = history_by_path()
    done, skipped = 0, []
    for rel in paths:
        try:
            it = lib_item(rel)
        except lib.LibraryError as e:
            skipped.append(f"{rel}: {e}")
            continue
        h = hist.get(os.path.realpath(it["abs"]))
        if not h:
            skipped.append(_("{name}: kein Eintrag unter „Erledigt“", name=it["name"]))
            continue
        if not it["editable"]:
            skipped.append(_("{name}: nur MP3", name=it["name"]))
            continue
        start = h.get("job_start") or h.get("window_start")
        src = {"title": h["title"], "artist": h["host"] or h["sender"], "album": h["sender"],
               "date": datetime.fromtimestamp(start).strftime("%Y-%m-%dT%H:%M") if start else None}
        fields = {k: v for k, v in src.items() if v and not it[k]}
        if not fields:
            skipped.append(_("{name}: schon vollständig", name=it["name"]))
            continue
        await asyncio.to_thread(lib.write_tags, Path(it["abs"]), fields)
        done += 1
    LIB.invalidate()
    log.info("Tags filled from job: %d file(s)", done)
    return JSONResponse({"done": done, "skipped": skipped})


def rename_plan(d: dict) -> tuple[list[dict], list[dict], str, bool]:
    paths = list(d.get("paths") or [])
    lib_lock_check(paths)
    pattern = str(d.get("pattern") or "").strip()
    skip = bool(d.get("skip_empty", True))
    items = [lib_item(p) for p in paths]
    return items, lib.plan_rename(storage(), items, pattern, skip), pattern, skip


@lib_errors
async def api_library_rename_preview(request: Request) -> JSONResponse:
    _items, plan, _p, _s = await asyncio.to_thread(rename_plan, await body_json(request))
    return JSONResponse({"plan": plan, "count": sum(1 for r in plan if r["action"] == "rename")})


@lib_errors
async def api_library_rename(request: Request) -> JSONResponse:
    d = await body_json(request)
    items, plan, pattern, skip = await asyncio.to_thread(rename_plan, d)
    db.set_setting("rename_pattern", pattern)
    db.set_setting("rename_skip_empty", skip)
    batch = secrets.token_hex(6)
    now = time.time()
    done, errors = 0, []
    by_path = {i["path"]: i for i in items}
    hist = history_by_path()
    for row in plan:
        if row["action"] != "rename":
            continue
        old = Path(by_path[row["path"]]["abs"])
        new = old.with_name(row["new"])
        try:
            if new.exists() and new.resolve() != old.resolve():
                raise FileExistsError(row["new"])
            old.rename(new)
        except OSError as e:
            errors.append(f"{row['old']}: {e.strerror or e}")
            continue
        db.x("INSERT INTO rename_log(batch,ts,old_path,new_path) VALUES(?,?,?,?)", (batch, now, str(old), str(new)))
        h = hist.get(os.path.realpath(old))
        if h:
            db.x("UPDATE history SET path=? WHERE id=?", (str(new), h["id"]))
        log.info("Renamed: %s → %s", old.name, new.name)
        done += 1
    LIB.invalidate()
    return JSONResponse({"done": done, "errors": errors, "undo": undo_info()})


def undo_info() -> dict | None:
    r = db.one("SELECT batch, MAX(ts) ts, COUNT(*) n FROM rename_log WHERE undone=0 "
               "GROUP BY batch ORDER BY ts DESC LIMIT 1")
    return {"batch": r["batch"], "ts": r["ts"], "count": r["n"]} if r else None


@lib_errors
async def api_library_undo(request: Request) -> JSONResponse:
    u = undo_info()
    if not u:
        return err(_("Es gibt keine Umbenennung zum Zurücknehmen"))
    rows = db.q("SELECT * FROM rename_log WHERE batch=? AND undone=0 ORDER BY id DESC", (u["batch"],))
    done, errors = 0, []
    for r in rows:
        old, new = Path(r["old_path"]), Path(r["new_path"])
        if not new.exists():
            errors.append(_("{name} gibt es nicht mehr", name=new.name))
        elif old.exists():
            errors.append(_("{name} ist schon vorhanden", name=old.name))
        else:
            try:
                new.rename(old)
            except OSError as e:
                errors.append(f"{new.name}: {e.strerror or e}")
            else:
                db.x("UPDATE history SET path=? WHERE path=?", (str(old), str(new)))
                done += 1
                log.info("Rename undone: %s → %s", new.name, old.name)
        db.x("UPDATE rename_log SET undone=1 WHERE id=?", (r["id"],))
    LIB.invalidate()
    return JSONResponse({"done": done, "errors": errors, "undo": undo_info()})


def cut_sender(it: dict, h: dict | None) -> str:
    return (h or {}).get("sender") or it.get("album") or it.get("artist") or ""


def cut_params(d: dict) -> tuple[float, float]:
    try:
        offset = float(d["offset"]) if d.get("offset") not in (None, "") else float(db.setting("cut_offset"))
        min_len = float(d["min_len"]) if d.get("min_len") not in (None, "") else float(db.setting("cut_min_len"))
    except (TypeError, ValueError):
        raise lib.LibraryError(_("Verschiebung oder Mindestlänge ungültig"))
    if not (-120 <= offset <= 120 and 0 <= min_len <= 3600):
        raise lib.LibraryError(_("Verschiebung −120…120 s, Mindestlänge 0…3600 s"))
    return offset, min_len


def cut_plan_for(rel: str, offset: float, min_len: float, hist: dict) -> dict:
    try:
        it = lib_item(rel, detail=True)
    except (lib.LibraryError, OSError) as e:
        return {"path": rel, "name": Path(rel).name, "title": Path(rel).name, "sender": "", "duration": None,
                "ok": False, "error": str(e), "segments": [], "folder": None}
    h = hist.get(os.path.realpath(it["abs"]))
    sender = cut_sender(it, h)
    out = {"path": rel, "name": it["name"], "title": it["title"] or it["name"], "sender": sender,
           "duration": it["duration"], "ok": False, "error": None, "segments": [], "folder": None}
    if item_kind(it) == "track":
        out["error"] = _("ist schon ein Einzeltitel")
    elif not it["editable"]:
        out["error"] = _("Schneiden geht nur bei MP3-Dateien")
    elif max(it["chapters"], it["tracks"]) < 3:
        out["error"] = _("keine Titelliste (mindestens 3 Titel nötig)")
    elif not it["duration"]:
        out["error"] = _("Länge nicht lesbar")
    else:
        out["segments"] = cutter.plan(it, it["duration"], sender, offset, min_len)
        out["ok"] = True
        vals = cutter.source_values(it, sender, Path(it["name"]).stem)
        example = cutter.target_for(title_dir(), str(db.setting("cut_pattern")),
                                    dict(vals, nr="01", interpret=_("Interpret"), titel=_("Titel")), set())
        out["folder"] = str(example.parent)
    return out


@lib_errors
async def api_library_cut_preview(request: Request) -> JSONResponse:
    d = await body_json(request)
    paths = list(d.get("paths") or [])
    lib_lock_check(paths)
    offset, min_len = cut_params(d)
    hist = history_by_path()
    files = await asyncio.to_thread(lambda: [cut_plan_for(p, offset, min_len, hist) for p in paths])
    return JSONResponse({"files": files, "cut_dir": str(title_dir()), "offset": offset, "min_len": min_len,
                         "pattern": db.setting("cut_pattern")})


@lib_errors
async def api_library_cut(request: Request) -> JSONResponse:
    """items: [{path, selected: [Segment-Index, …]}] – die Auswahl aus der Vorschau."""
    d = await body_json(request)
    items = list(d.get("items") or [])
    lib_lock_check([i.get("path") for i in items])
    offset, min_len = cut_params(d)
    hist = history_by_path()
    pattern = str(db.setting("cut_pattern"))
    results, total = [], 0
    cstats: dict = {}
    finder = cover_finder().find if db.setting("cover_lookup") and lib.mutagen_ok() else None

    def work():
        nonlocal total
        for item in items:
            rel = str(item.get("path") or "")
            res = {"path": rel, "created": [], "error": None}
            try:
                plan = cut_plan_for(rel, offset, min_len, hist)
                if not plan["ok"]:
                    raise lib.LibraryError(plan["error"])
                want = {int(x) for x in item.get("selected") or []}
                segs = [dict(s, selected=s["i"] in want) for s in plan["segments"]]
                it = lib_item(rel, detail=True)
                made = cutter.cut(Path(it["abs"]), it, segs, plan["sender"], title_dir(), pattern, rel,
                                  find_cover=finder, stats=cstats)
                res["created"] = [rel_of(m) for m in made]
                total += len(made)
                log.info("Cut: %s → %d tracks in %s", rel, len(made), title_dir())
            except (lib.LibraryError, OSError) as e:
                res["error"] = str(e)
                log.warning("Cutting failed: %s: %s", rel, e)
            results.append(res)
    await asyncio.to_thread(work)
    LIB.invalidate()
    return JSONResponse({"files": results, "created": total, "covers_online": cstats.get("online", 0),
                         "covers_fallback": cstats.get("fallback", 0), "cover_lookup": bool(finder)})


def data_url(res: dict) -> str:
    return f"data:{res['mime']};base64," + base64.b64encode(res["data"]).decode()


@lib_errors
async def api_library_cover_lookup(request: Request) -> JSONResponse:
    """Cover für Interpret/Titel suchen (für den Tag-Dialog). Speichert noch nichts in der Datei."""
    d = await body_json(request)
    artist, title = str(d.get("artist") or "").strip(), str(d.get("title") or "").strip()
    if not artist or not title:
        return err(_("Für die Suche werden Interpret und Titel gebraucht"))
    res = await asyncio.to_thread(cover_finder().find, artist, title, False)
    if not res:
        return JSONResponse({"found": False})
    return JSONResponse({"found": True, "data_url": data_url(res), "source": res["source"], "info": res.get("info")})


@lib_errors
async def api_library_covers(request: Request) -> JSONResponse:
    """Für ausgewählte Einzeltitel Cover online suchen und einbetten."""
    d = await body_json(request)
    paths = list(d.get("paths") or [])
    lib_lock_check(paths)
    replace = bool(d.get("replace"))
    if not lib.mutagen_ok():
        return err(mutagen_hint())
    found, missing, skipped = 0, [], []

    def work():
        nonlocal found
        for rel in paths:
            try:
                it = lib_item(rel)
            except lib.LibraryError as e:
                skipped.append(f"{Path(rel).name}: {e}")
                continue
            if item_kind(it) != "track":
                skipped.append(_("{name}: nur für Einzeltitel", name=it["name"]))
                continue
            if not it["editable"]:
                skipped.append(_("{name}: nur MP3", name=it["name"]))
                continue
            if it["has_cover"] and not replace:
                skipped.append(_("{name}: hat schon ein Cover", name=it["name"]))
                continue
            if not it["artist"] or not it["title"]:
                skipped.append(_("{name}: Interpret oder Titel fehlt", name=it["name"]))
                continue
            res = cover_finder().find(it["artist"], it["title"])
            if not res:
                missing.append(it["name"])
                continue
            lib.write_tags(Path(it["abs"]), {}, data_url(res))
            found += 1
    await asyncio.to_thread(work)
    LIB.invalidate()
    log.info("Cover lookup: %d found, %d without match, %d skipped", found, len(missing), len(skipped))
    return JSONResponse({"found": found, "missing": missing, "skipped": skipped})


# --- Papierkorb (deleted/) -----------------------------------------------------------
def lib_dir_roots() -> list[Path]:
    return [r for _p, r in lib_roots()]


def lib_protected() -> list[Path]:
    """Ordner, die nie entfernt werden."""
    return [storage(), rec_dir(), title_dir(), trash_dir()]


@lib_errors
async def api_library_trash(request: Request) -> JSONResponse:
    d = await body_json(request)
    paths = list(d.get("paths") or [])
    lib_lock_check(paths)
    done, errors, folders = 0, [], set()
    for rel in paths:
        try:
            it = lib_item(rel)
            src = Path(it["abs"])
            dest = await asyncio.to_thread(lib.trash_move, src, rel, trash_dir())
        except (lib.LibraryError, OSError) as e:
            errors.append(f"{Path(rel).name}: {e}")
            continue
        db.x("INSERT OR REPLACE INTO trash(orig,path,deleted,size,title,artist,kind,duration) VALUES(?,?,?,?,?,?,?,?)",
             (rel, str(dest), time.time(), it["size"], it["title"], it["artist"], item_kind(it), it["duration"]))
        log.info("Moved to trash: %s", rel)
        folders.add(src.parent)
        done += 1
    pruned = []
    if db.setting("prune_empty_dirs"):
        for f in sorted(folders, key=lambda p: len(p.parts), reverse=True):
            pruned += await asyncio.to_thread(lib.prune_upwards, f, lib_dir_roots(), lib_protected())
        for p in pruned:
            log.info("Empty folder removed: %s", p)
    LIB.invalidate()
    return JSONResponse({"done": done, "errors": errors, "dirs_removed": len(pruned)})


def trash_sync() -> None:
    """Datenbank und Ordner deleted/ abgleichen (Dateien ohne Eintrag aufnehmen, verschwundene austragen)."""
    root = trash_dir()
    rows = {r["path"]: r for r in db.q("SELECT * FROM trash")}
    seen = set()
    if root.is_dir():
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [x for x in dirnames if not x.startswith(".")]
            for fn in filenames:
                if fn.startswith(".") or lib.is_junk(fn):
                    continue
                p = Path(dirpath) / fn
                seen.add(str(p))
                if str(p) in rows:
                    continue
                rel = str(p.relative_to(root))
                if not rel.startswith(("record/", "title/")):
                    rel = "record/" + rel
                try:
                    st = p.stat()
                except OSError:
                    continue
                try:
                    info = lib.read_info(p) if p.suffix.lower() in lib.AUDIO_EXT else {}
                except Exception:
                    info = {}
                db.x("INSERT OR IGNORE INTO trash(orig,path,deleted,size,title,artist,kind,duration) VALUES(?,?,?,?,?,?,?,?)",
                     (rel, str(p), st.st_mtime, st.st_size, info.get("title"), info.get("artist"),
                      "track" if rel.startswith("title/") else "recording", info.get("duration")))
    for path in rows:
        if path not in seen:
            db.x("DELETE FROM trash WHERE path=?", (path,))


def trash_prune(p: Path) -> None:
    lib.prune_upwards(p.parent, [trash_dir()], [trash_dir()])


def trash_delete_rows(rows: list[dict]) -> int:
    n = 0
    for r in rows:
        p = Path(r["path"])
        try:
            if p.exists():
                p.unlink()
            n += 1
        except OSError as e:
            log.warning("Deleting %s from the trash failed: %s", p, e)
            continue
        db.x("DELETE FROM trash WHERE id=?", (r["id"],))
        trash_prune(p)
    return n


def trash_auto_empty() -> None:
    days = int(db.setting("trash_days") or 0)
    if days <= 0:
        return
    trash_sync()
    rows = db.q("SELECT * FROM trash WHERE deleted < ?", (time.time() - days * 86400,))
    if rows:
        n = trash_delete_rows(rows)
        log.info("Trash: %d file(s) older than %d days deleted permanently", n, days)


async def api_trash(request: Request) -> JSONResponse:
    await asyncio.to_thread(trash_sync)
    rows = db.q("SELECT * FROM trash ORDER BY deleted DESC")
    items = [{"id": r["id"], "orig": r["orig"], "name": Path(r["orig"]).name, "folder": folder_of(r["orig"]),
              "kind": r["kind"] or "recording", "title": r["title"] or "", "artist": r["artist"] or "",
              "size": r["size"] or 0, "duration": r["duration"], "deleted": r["deleted"]} for r in rows]
    days = int(db.setting("trash_days") or 0)
    return JSONResponse({"items": items, "count": len(items), "bytes": sum(i["size"] for i in items),
                         "days": days, "path": str(trash_dir())})


def trash_rows(d: dict) -> list[dict]:
    ids = [int(x) for x in (d.get("ids") or [])][:5000]
    if not ids:
        return []
    marks = ",".join("?" * len(ids))
    return db.q(f"SELECT * FROM trash WHERE id IN ({marks})", tuple(ids))


async def api_trash_restore(request: Request) -> JSONResponse:
    rows = trash_rows(await body_json(request))
    done, errors, restored = 0, [], []
    for r in rows:
        src = Path(r["path"])
        try:
            if not src.exists():
                raise lib.LibraryError(_("nicht mehr im Papierkorb"))
            base, _pre, inner = split_rel(r["orig"])
            dest = lib.resolve_in(base, inner)
            dest = await asyncio.to_thread(lib.restore_move, src, dest)
        except (lib.LibraryError, OSError) as e:
            errors.append(f"{Path(r['orig']).name}: {e}")
            continue
        db.x("DELETE FROM trash WHERE id=?", (r["id"],))
        trash_prune(src)
        restored.append(rel_of(dest))
        log.info("Restored from trash: %s", rel_of(dest))
        done += 1
    LIB.invalidate()
    return JSONResponse({"done": done, "errors": errors, "paths": restored})


async def api_trash_delete(request: Request) -> JSONResponse:
    rows = trash_rows(await body_json(request))
    n = await asyncio.to_thread(trash_delete_rows, rows)
    log.info("Trash: %d file(s) deleted permanently by %s", n, session_user(request))
    return JSONResponse({"done": n})


async def api_trash_empty(request: Request) -> JSONResponse:
    await asyncio.to_thread(trash_sync)
    n = await asyncio.to_thread(trash_delete_rows, db.q("SELECT * FROM trash"))
    log.info("Trash emptied by %s: %d file(s)", session_user(request), n)
    return JSONResponse({"done": n})


def dir_display(d: Path) -> str:
    s = storage().resolve()
    return str(d.relative_to(s)) if s in d.parents else str(d)


@lib_errors
async def api_library_empty_dirs(request: Request) -> JSONResponse:
    if request.method == "GET":
        def find():
            out = []
            for r in lib_dir_roots():
                out += lib.find_empty_dirs(r, lib_protected())
            return out
        dirs = await asyncio.to_thread(find)
        return JSONResponse({"dirs": [{"abs": str(d), "path": dir_display(d)} for d in dirs]})
    d = await body_json(request)
    want = [Path(str(x)) for x in (d.get("dirs") or [])][:5000]
    removed, skipped = await asyncio.to_thread(lib.remove_dirs, want, lib_dir_roots(), lib_protected())
    for p in removed:
        log.info("Empty folder removed: %s", p)
    LIB.invalidate()
    return JSONResponse({"removed": len(removed), "skipped": skipped})


# --- Seiten -----------------------------------------------------------------
FAVICON = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32"><rect width="32" height="32" rx="7" fill="#ce6c00"/>
<circle cx="16" cy="16" r="6" fill="none" stroke="#0b0b0b" stroke-width="2.5"/><circle cx="16" cy="16" r="2.5" fill="#0b0b0b"/></svg>"""


async def index(request: Request) -> HTMLResponse:
    html = (WEB / "index.html").read_text("utf-8")
    html = html.replace('<html lang="de">', f'<html lang="{i18n.LANG}" data-auth="{request.state.auth}">', 1)
    return HTMLResponse(html, headers={"cache-control": "no-store"})


async def lang_js(request: Request) -> Response:
    return FileResponse(WEB / "lang-en.js", media_type="text/javascript", headers={"cache-control": "no-store"})


async def manual(request: Request) -> HTMLResponse:
    lang = request.query_params.get("lang") or i18n.LANG
    page = WEB / ("manual-en.html" if lang == "en" else "manual-de.html")
    if not page.is_file():
        return PlainTextResponse("manual missing", status_code=404)
    return HTMLResponse(page.read_text("utf-8"), headers={"cache-control": "no-store"})


async def manual_image(request: Request) -> Response:
    lang, name = request.path_params["lang"], request.path_params["name"]
    if lang not in ("de", "en") or not re.fullmatch(r"[a-z0-9-]+\.png", name):
        return Response(status_code=404)
    p = WEB / "manual" / lang / name
    if not p.is_file():
        return Response(status_code=404)
    return FileResponse(p, media_type="image/png", headers={"cache-control": "public, max-age=86400"})


async def favicon(request: Request) -> Response:
    return Response(FAVICON, media_type="image/svg+xml", headers={"cache-control": "public, max-age=604800"})


async def favicon_ico(request: Request) -> Response:
    return Response(status_code=204)


# --------------------------------------------------------------------------
# App
# --------------------------------------------------------------------------
@asynccontextmanager
async def lifespan(app):
    global db, http, HA_KICK
    db = DB(DATA / "recorder.db")
    apply_language()
    ensure_dirs()
    HA_KICK = asyncio.Event()
    n = db.con.execute("UPDATE history SET status='error', ended=COALESCE(ended,?), "
                       "message=? WHERE status='running'", (time.time(), _("App unerwartet beendet"))).rowcount
    if n:
        log.warning("%d interrupted recording(s) marked as error", n)
    if not db.one("SELECT id FROM senders LIMIT 1") and not db.one("SELECT 1 FROM settings WHERE key='seeded'"):
        for name, url in SEED_SENDERS:
            db.x("INSERT INTO senders(name,url,created) VALUES(?,?,?)", (name, url, time.time()))
        db.set_setting("seeded", True)
    http = httpx.AsyncClient(timeout=httpx.Timeout(connect=15, read=CFG["read_timeout"], write=15, pool=15),
                             limits=httpx.Limits(max_connections=50, max_keepalive_connections=5))
    tasks = [asyncio.create_task(scheduler()), asyncio.create_task(ha_loop())]
    if os.environ.get("STREAM_RECORDER_STDIN", "1") == "1":
        ha.StdinReader(asyncio.get_running_loop(), on_stdin).start()
    await asyncio.to_thread(trash_sync)                 # Dateien in deleted/ mitzählen
    if not mutagen_ok():
        log.error("Python package 'mutagen' missing – ID3 tags will not be written")
    for w in CFG["warnings"]:
        log.error("Invalid option %s: %s", *w)
    log.info("Stream Recorder %s started: ingress port %s, direct port %s (%s), storage %s, language %s, "
             "Home Assistant API %s", __version__, CFG["ingress_port"], CFG["direct_port"],
             "login" if direct_enabled() else "not configured", storage(), i18n.LANG,
             "on" if SUP.enabled else "off")
    try:
        yield
    finally:
        for t in tasks:
            t.cancel()
        recs = list(RUNNING.values())
        for r in recs:
            r.stop(SHUTDOWN)
        if recs:
            await asyncio.wait({r.task for r in recs if r.task}, timeout=60)
        await http.aclose()
        await SUP.close()
        log.info("Stream Recorder stopped")


routes = [
    Route("/", index),
    Route("/login", login_page, methods=["GET", "POST"]),
    Route("/lang-en.js", lang_js),
    Route("/manual", manual),
    Route("/manual/{lang}/{name}", manual_image),
    Route("/favicon.svg", favicon),
    Route("/favicon.ico", favicon_ico),
    Route("/api/logout", logout, methods=["POST"]),
    Route("/api/state", api_state),
    Route("/api/history", api_history),
    Route("/api/history/{id:int}/file", api_history_file),
    Route("/api/history/{id:int}", api_history_delete, methods=["DELETE"]),
    Route("/api/jobs", api_jobs_create, methods=["POST"]),
    Route("/api/jobs/{id:int}", api_jobs_update, methods=["PUT"]),
    Route("/api/jobs/{id:int}", api_jobs_delete, methods=["DELETE"]),
    Route("/api/jobs/{id:int}/stop", api_jobs_stop, methods=["POST"]),
    Route("/api/record-now", api_record_now, methods=["POST"]),
    Route("/api/senders", api_senders, methods=["GET", "POST"]),
    Route("/api/senders/{id:int}", api_senders_delete, methods=["DELETE"]),
    Route("/api/stream-test", api_stream_test, methods=["POST"]),
    Route("/api/settings", api_settings, methods=["GET", "PUT"]),
    Route("/api/log", api_log),
    Route("/api/log/clear", api_log_clear, methods=["POST"]),
    Route("/api/library", api_library),
    Route("/api/library/item", api_library_item),
    Route("/api/library/stream", api_library_stream),
    Route("/api/library/cover", api_library_cover),
    Route("/api/library/tracklist", api_library_tracklist),
    Route("/api/library/tags", api_library_tags, methods=["PUT"]),
    Route("/api/library/fill", api_library_fill, methods=["POST"]),
    Route("/api/library/rename/preview", api_library_rename_preview, methods=["POST"]),
    Route("/api/library/rename", api_library_rename, methods=["POST"]),
    Route("/api/library/undo", api_library_undo, methods=["POST"]),
    Route("/api/library/trash", api_library_trash, methods=["POST"]),
    Route("/api/library/empty-dirs", api_library_empty_dirs, methods=["GET", "POST"]),
    Route("/api/library/cut/preview", api_library_cut_preview, methods=["POST"]),
    Route("/api/library/cut", api_library_cut, methods=["POST"]),
    Route("/api/library/cover-lookup", api_library_cover_lookup, methods=["POST"]),
    Route("/api/library/covers", api_library_covers, methods=["POST"]),
    Route("/api/trash", api_trash),
    Route("/api/trash/restore", api_trash_restore, methods=["POST"]),
    Route("/api/trash/delete", api_trash_delete, methods=["POST"]),
    Route("/api/trash/empty", api_trash_empty, methods=["POST"]),
]
app = Starlette(lifespan=lifespan, routes=routes)
app.add_middleware(AuthMiddleware)


def bind(host: str, port: int) -> socket.socket:
    s = socket.socket(socket.AF_INET6 if ":" in host else socket.AF_INET, socket.SOCK_STREAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind((host, port))
    s.set_inheritable(True)
    return s


def main() -> None:
    if "--version" in sys.argv:
        print(__version__)
        return
    import uvicorn
    setup_logging()
    socks = [bind(CFG["listen_host"], CFG["ingress_port"]), bind(CFG["listen_host"], CFG["direct_port"])]
    config = uvicorn.Config(app, log_config=None, access_log=False, timeout_graceful_shutdown=70)
    uvicorn.Server(config).run(sockets=socks)


if __name__ == "__main__":
    main()
