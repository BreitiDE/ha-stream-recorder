"""
Cover-Bilder online suchen – anhand von Interpret und Titel.

Quellen (ohne Konto/Schlüssel): zuerst Deezer, dann Apple iTunes. Ein Treffer wird nur
übernommen, wenn Interpret und Titel ausreichend übereinstimmen: lieber kein Cover als ein
falsches. Ergebnisse (auch „nichts gefunden“) landen in einem Zwischenspeicher, damit ein
Song nicht bei jedem Schnitt erneut angefragt wird.
"""
from __future__ import annotations

import hashlib
import logging
import re
import sqlite3
import threading
import time
import unicodedata
from pathlib import Path

import httpx

log = logging.getLogger("stream-recorder")

DEEZER_URL = "https://api.deezer.com/search"
ITUNES_URL = "https://itunes.apple.com/search"
NOT_FOUND_RETRY = 14 * 86400          # „nicht gefunden“ nach 14 Tagen erneut versuchen
MAX_IMAGE = 3_000_000


def _norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode().lower()
    return s


def clean_title(t: str) -> str:
    """„Butterflies (Original Mix) [Remastered]“ → „Butterflies“."""
    t = re.sub(r"\s*[\(\[][^\)\]]*[\)\]]", " ", t or "")
    t = re.sub(r"\s+(feat\.?|ft\.?|featuring)\s+.*$", "", t, flags=re.I)
    return re.sub(r"\s+", " ", t).strip(" -")


def artists_of(a: str) -> list[str]:
    parts = re.split(r"\s*(?:,|&|\+|/|\bx\b|\bvs\b\.?|\bfeaturing\b|\bfeat\b\.?|\bft\b\.?|\band\b|\bund\b)\s*", a or "", flags=re.I)
    return [p.strip() for p in parts if p.strip()]


def _tokens(s: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]+", _norm(s)) if w not in ("the", "a", "mix", "edit", "radio", "original", "remix", "version")}


def similar(a: str, b: str) -> float:
    ta, tb = _tokens(a), _tokens(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def matches(q_artist: str, q_title: str, r_artist: str, r_title: str) -> bool:
    t = clean_title(q_title)
    if similar(t, clean_title(r_title)) < 0.6 and _norm(t) not in _norm(r_title):
        return False
    qa, ra = artists_of(q_artist), artists_of(r_artist)
    if not qa:
        return True
    ra_tokens = _tokens(" ".join(ra))
    hits = sum(1 for a in qa if _tokens(a) and _tokens(a) <= ra_tokens)
    return hits >= 1 and (hits / len(qa) >= 0.5 or similar(q_artist, r_artist) >= 0.5)


class CoverFinder:
    def __init__(self, data_dir: Path, deezer_url: str = DEEZER_URL, itunes_url: str = ITUNES_URL):
        self.dir = data_dir / "covers"
        self.dir.mkdir(parents=True, exist_ok=True)
        self.db_path = data_dir / "covers.db"
        self.deezer_url, self.itunes_url = deezer_url, itunes_url
        self.lock = threading.Lock()
        with self._con() as con:
            con.execute("CREATE TABLE IF NOT EXISTS cover_cache (key TEXT PRIMARY KEY, ts REAL, found INTEGER,"
                        " source TEXT, file TEXT, mime TEXT, info TEXT)")
        self._itunes_blocked_until = 0.0
        self._offline_until = 0.0          # nach Netzwerkfehlern kurz gar nicht mehr fragen

    def _con(self):
        return sqlite3.connect(self.db_path, timeout=10)

    @staticmethod
    def key(artist: str, title: str) -> str:
        return " | ".join(sorted(_tokens(" ".join(artists_of(artist))))) + " || " + " ".join(sorted(_tokens(clean_title(title))))

    # ------------------------------------------------------------------
    def find(self, artist: str, title: str, use_cache: bool = True) -> dict | None:
        """{"data": bytes, "mime": str, "source": str, "info": str} oder None."""
        if not title or not (artist or "").strip():
            return None
        k = self.key(artist, title)
        if use_cache:
            with self._con() as con:
                r = con.execute("SELECT ts, found, source, file, mime, info FROM cover_cache WHERE key=?", (k,)).fetchone()
            if r:
                ts, found, source, file, mime, info = r
                if found and file and (self.dir / file).exists():
                    return {"data": (self.dir / file).read_bytes(), "mime": mime, "source": source, "info": info}
                if not found and time.time() - ts < NOT_FOUND_RETRY:
                    return None
        if time.time() < self._offline_until:
            return None
        res, net_error = None, False
        with httpx.Client(timeout=httpx.Timeout(8.0, connect=5.0), follow_redirects=True,
                          headers={"User-Agent": "stream-recorder (private use)"}) as c:
            for fn in (self._deezer, self._itunes):
                try:
                    res = fn(c, artist, title)
                except (httpx.TransportError, httpx.HTTPStatusError) as e:
                    net_error = True
                    log.warning("Cover lookup: %s not reachable: %s", fn.__name__.strip("_"), e)
                    res = None
                except (ValueError, KeyError, TypeError) as e:
                    log.debug("Cover lookup %s: unexpected answer: %s", fn.__name__, e)
                    res = None
                if res:
                    break
        if not res and net_error:
            # nichts als „nicht gefunden“ merken, wenn ein Dienst nicht erreichbar war
            self._offline_until = time.time() + 300
            log.warning("Cover lookup paused for 5 minutes (services not reachable)")
            return None
        with self.lock, self._con() as con:
            if res:
                name = hashlib.sha1(k.encode()).hexdigest() + (".png" if res["mime"] == "image/png" else ".jpg")
                (self.dir / name).write_bytes(res["data"])
                con.execute("INSERT OR REPLACE INTO cover_cache VALUES(?,?,?,?,?,?,?)",
                            (k, time.time(), 1, res["source"], name, res["mime"], res["info"]))
                log.info("Cover found (%s): %s - %s", res["source"], artist, title)
            else:
                con.execute("INSERT OR REPLACE INTO cover_cache VALUES(?,?,?,?,?,?,?)",
                            (k, time.time(), 0, None, None, None, None))
                log.info("No cover found: %s - %s", artist, title)
        return res

    # ------------------------------------------------------------------
    def _image(self, c: httpx.Client, url: str) -> tuple[bytes, str] | None:
        if not url:
            return None
        r = c.get(url)
        r.raise_for_status()
        mime = r.headers.get("content-type", "").split(";")[0].strip().lower()
        if mime not in ("image/jpeg", "image/png") or not r.content or len(r.content) > MAX_IMAGE:
            return None
        return r.content, mime

    def _deezer(self, c: httpx.Client, artist: str, title: str) -> dict | None:
        main = artists_of(artist)[0] if artists_of(artist) else artist
        for q in (f'artist:"{main}" track:"{clean_title(title)}"', f"{artist} {clean_title(title)}"):
            r = c.get(self.deezer_url, params={"q": q, "limit": 10})
            r.raise_for_status()
            for hit in r.json().get("data") or []:
                if matches(artist, title, hit["artist"]["name"], hit["title"]):
                    alb = hit.get("album") or {}
                    img = self._image(c, alb.get("cover_big") or alb.get("cover_xl") or alb.get("cover_medium"))
                    if img:
                        return {"data": img[0], "mime": img[1], "source": "Deezer",
                                "info": f'{hit["artist"]["name"]} – {hit["title"]} ({alb.get("title", "")})'}
        return None

    def _itunes(self, c: httpx.Client, artist: str, title: str) -> dict | None:
        if time.time() < self._itunes_blocked_until:
            return None
        r = c.get(self.itunes_url, params={"term": f"{artist} {clean_title(title)}", "media": "music",
                                           "entity": "song", "limit": 10, "country": "DE"})
        if r.status_code in (403, 429):          # Abruflimit: eine Weile Pause
            self._itunes_blocked_until = time.time() + 120
            return None
        r.raise_for_status()
        for hit in r.json().get("results") or []:
            if matches(artist, title, hit.get("artistName", ""), hit.get("trackName", "")):
                url = (hit.get("artworkUrl100") or "").replace("100x100bb", "600x600bb")
                img = self._image(c, url)
                if img:
                    return {"data": img[0], "mime": img[1], "source": "iTunes",
                            "info": f'{hit.get("artistName")} – {hit.get("trackName")} ({hit.get("collectionName", "")})'}
        return None
