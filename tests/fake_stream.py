"""Nachbau eines Icecast-/QuantumCast-Servers für Tests.

    python3 tests/fake_stream.py [PORT]

/stream.mp3   MP3 128 kbit/s mit ICY-Metadaten (Titelwechsel alle 2 s), 1 s Vorpuffer
/redirect     302 → /stream.mp3 (wie stream.sunshine-live.de → streamabc)
/play.m3u     Playlist → /redirect
/play.pls     PLS-Playlist → /stream.mp3
/drop.mp3     Stream, der nach 3 s abbricht (Reconnect-Test)
/html         Webseite statt Stream
/deezer/search, /itunes/search, /img/N.png   Nachbau der Cover-Suche
/core/api/...   Nachbau der Home-Assistant-API hinter dem Supervisor (Sprache, Zustände,
                Ereignisse). /_ha zeigt, was angekommen ist (für den Smoke-Test).
"""
import asyncio
import sys
from pathlib import Path

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import (HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse, Response,
                                 StreamingResponse)
from starlette.routing import Route

FRAME = bytes([0xFF, 0xFB, 0x90, 0x00]) + bytes(413)   # MPEG-1 Layer III, 128 kbit/s, 44,1 kHz
RATE = 16000                                          # Byte pro Sekunde
METAINT = 8192


def icy_block(title: str) -> bytes:
    raw = f"StreamTitle='{title}';".encode()
    n = (len(raw) + 15) // 16
    return bytes([n]) + raw.ljust(n * 16, b"\0")


async def gen(request: Request, limit_s: float | None = None):
    icy = request.headers.get("icy-metadata") == "1"
    buf = bytearray()
    sent_audio, left, t0, track = 0, METAINT, asyncio.get_event_loop().time(), -1
    burst = RATE                     # 1 s Vorpuffer wie echte Server
    while True:
        now = asyncio.get_event_loop().time() - t0
        if limit_s and now > limit_s:
            return
        target = burst + int(now * RATE)
        while sent_audio + len(buf) < target:
            buf += FRAME
        chunk, buf = bytes(buf[:target - sent_audio]), bytearray(buf[target - sent_audio:])
        out = bytearray()
        for b in [chunk[i:i + 1024] for i in range(0, len(chunk), 1024)]:
            if not icy:
                out += b
                continue
            while b:
                k = min(left, len(b))
                out += b[:k]
                b = b[k:]
                left -= k
                if left == 0:
                    t = int((sent_audio + len(out)) / RATE / 2)
                    if t != track:
                        track = t
                        out += icy_block(f"Artist {t} - Track {t} 'Mix'")
                    else:
                        out += b"\0"
                    left = METAINT
        sent_audio += len(chunk)
        if out:
            yield bytes(out)
        await asyncio.sleep(0.1)


def headers(request):
    h = {"icy-name": "Beispielradio Lounge", "icy-genre": "Lounge", "icy-br": "128", "server": "FakeCast"}
    if request.headers.get("icy-metadata") == "1":
        h["icy-metaint"] = str(METAINT)
    return h


async def stream(request):
    return StreamingResponse(gen(request), media_type="audio/mpeg", headers=headers(request))


async def drop(request):
    return StreamingResponse(gen(request, limit_s=3), media_type="audio/mpeg", headers=headers(request))


async def redirect(request):
    return RedirectResponse("/stream.mp3", status_code=302)


async def m3u(request):
    base = str(request.base_url).rstrip("/")
    return Response(f"#EXTM3U\n#EXTINF:-1,Fake\n{base}/redirect\n", media_type="audio/x-mpegurl")


async def pls(request):
    return Response("[playlist]\nNumberOfEntries=1\nFile1=/stream.mp3\nTitle1=Fake\n", media_type="audio/x-scpls")


async def html(request):
    return HTMLResponse("<html><body>Kein Stream</body></html>")


# ---- nachgebaute Cover-Dienste (Deezer- und iTunes-Suche) ----------------
CATALOG = {  # (Interpret, Titel) -> (Quelle, Farbe)
    ("Künstler B", "Zwei"): ("deezer", 1),
    ("Künstler C", "Drei"): ("itunes", 2),
    ("SRTW & Charity Children", "Butterflies (Original Mix)"): ("deezer", 3),
    ("Künstler B", "Ocean Drive"): ("deezer", 4),
    ("Künstler D", "Lost in Sound"): ("itunes", 5),
    ("Künstler E", "Deep Blue"): ("deezer", 6),
    ("Artist 3", "Track 3 'Mix'"): ("deezer", 7),
}


def _hits(q: str, source: str):
    import re as _re
    words = set(_re.findall(r"[a-z0-9]+", q.lower()))
    for (artist, title), (src, color) in CATALOG.items():
        if src != source:
            continue
        need = set(_re.findall(r"[a-z0-9]+", f"{artist} {title}".lower().replace("original mix", "")))
        if need <= words | {"mix", "original"}:
            yield artist, title, color


async def deezer(request):
    base = str(request.base_url).rstrip("/")
    data = [{"title": t, "artist": {"name": a}, "album": {"title": f"Album {c}", "cover_big": f"{base}/img/{c}.png"}}
            for a, t, c in _hits(request.query_params.get("q", ""), "deezer")]
    return JSONResponse({"data": data, "total": len(data)})


async def itunes(request):
    base = str(request.base_url).rstrip("/")
    res = [{"artistName": a, "trackName": t, "collectionName": f"Album {c}", "artworkUrl100": f"{base}/img/{c}.png?s=100x100bb"}
           for a, t, c in _hits(request.query_params.get("term", ""), "itunes")]
    return JSONResponse({"resultCount": len(res), "results": res})


async def img(request):
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from demo_files import COVER_COLORS, png_cover
    n = int(request.path_params["n"]) % len(COVER_COLORS)
    return Response(png_cover(COVER_COLORS[n], 120), media_type="image/png")


HA = {"states": {}, "events": [], "auth_ok": True, "language": "de"}


def ha_auth(request) -> bool:
    ok = request.headers.get("authorization", "").startswith("Bearer ")
    HA["auth_ok"] = HA["auth_ok"] and ok
    return ok


async def ha_config(request):
    ha_auth(request)
    return JSONResponse({"language": HA["language"], "time_zone": "Europe/Berlin"})


async def ha_state(request):
    ha_auth(request)
    HA["states"][request.path_params["eid"]] = await request.json()
    return JSONResponse({"ok": True})


async def ha_event(request):
    ha_auth(request)
    HA["events"].append({"event": request.path_params["name"], "data": await request.json()})
    return JSONResponse({"message": "ok"})


async def ha_dump(request):
    if request.method == "POST":                    # Sprache von "Home Assistant" umstellen
        HA["language"] = (await request.json()).get("language", "de")
    return JSONResponse(HA)


app = Starlette(routes=[Route("/deezer/search", deezer), Route("/itunes/search", itunes), Route("/img/{n:int}.png", img),
                        Route("/stream.mp3", stream), Route("/drop.mp3", drop), Route("/redirect", redirect),
                        Route("/play.m3u", m3u), Route("/play.pls", pls), Route("/html", html),
                        Route("/core/api/config", ha_config), Route("/core/api/states/{eid}", ha_state, methods=["POST"]),
                        Route("/core/api/events/{name}", ha_event, methods=["POST"]),
                        Route("/_ha", ha_dump, methods=["GET", "POST"])])

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=int(sys.argv[1]) if len(sys.argv) > 1 else 8001, log_level="warning")
