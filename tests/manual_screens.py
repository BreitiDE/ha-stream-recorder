"""Erzeugt die Bilder für die Bedienungsanleitung (stream_recorder/app/web/manual/{de,en}/*.png).

    pip install playwright && playwright install chromium
    python3 tests/manual_screens.py

Startet Test-Server und App mit neutralen Beispieldaten und fotografiert die wichtigsten
Ansichten, einmal auf Deutsch, einmal auf Englisch. Damit die Pfade in den Bildern wie in
Home Assistant aussehen, nutzt das Skript /media/stream-recorder und /data, wenn es dort
schreiben darf (nur in einer Wegwerf-Umgebung ausführen!), sonst einen temporären Ordner.
Für hübsche Adressen sollte "stream.beispielradio.de" auf 127.0.0.1 zeigen (/etc/hosts).
"""
import asyncio
import json
import os
import shutil
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timedelta
from pathlib import Path

import httpx
from playwright.async_api import async_playwright

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "stream_recorder" / "app"
OUT = APP / "web" / "manual"
PW = "anleitung123"
MARK = ".sr-manual"


def free_port(prefer: int = 0):
    with socket.socket() as s:
        try:
            s.bind(("127.0.0.1", prefer))
        except OSError:
            s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def host_ok(name):
    try:
        return socket.gethostbyname(name) == "127.0.0.1"
    except OSError:
        return False


def prepare(path: Path, fallback: Path) -> Path:
    """Leeren Ordner anlegen. Fremde Inhalte werden nie gelöscht, nur Reste eines früheren Laufs."""
    try:
        if path.exists() and any(path.iterdir()):
            if not (path / MARK).exists():
                raise OSError("belegt")
            shutil.rmtree(path)
        path.mkdir(parents=True, exist_ok=True)
        (path / MARK).touch()
        return path
    except OSError:
        fallback.mkdir(parents=True, exist_ok=True)
        return fallback


def seed(db_path: Path, storage: str, stream: str):
    sys.path.insert(0, str(APP))
    import stream_recorder as sr
    sr.DB(db_path)
    con = sqlite3.connect(db_path)
    now = time.time()
    con.execute("INSERT INTO settings(key,value) VALUES('seeded','true')")
    for name, url in (("sunshine live Lounge", sr.SEED_SENDERS[0][1]), ("Beispielradio", stream)):
        con.execute("INSERT INTO senders(name,url,created) VALUES(?,?,?)", (name, url, now))

    def at(days, hour):
        return (datetime.now() + timedelta(days=days)).replace(hour=hour, minute=0, second=0, microsecond=0).timestamp()
    jobs = [("Lounge am Nachmittag", None, "Beispielradio", stream, now - 23 * 60, 60, 0, 0, "once"),
            ("Deep House Session", "DJ Beispiel", "Beispielradio", stream, at(1, 21), 120, 1, 3, "weekly"),
            ("Chillout Sunday", None, "sunshine live Lounge", sr.SEED_SENDERS[0][1], at(2, 10), 180, 1, 3, "once"),
            ("Feierabend-Mix", None, "sunshine live Lounge", sr.SEED_SENDERS[0][1], at(3, 18), 60, 0, 2, "weekdays")]
    for j in jobs:
        con.execute("INSERT INTO jobs(title,host,sender,url,start,duration_min,pre_min,post_min,repeat,source,created)"
                    " VALUES(?,?,?,?,?,?,?,?,?,'manual',?)", (*j, now))
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from demo_files import seed_library
    seed_library(con, storage, now, stream)
    con.commit()


async def shoot(ingress: str, direct: str, fs: str, lang: str):
    out = OUT / lang
    out.mkdir(parents=True, exist_ok=True)
    httpx.post(f"{fs}/_ha", json={"language": lang})
    httpx.put(f"{ingress}/api/settings", headers={"X-Recorder": "1"}, json={"language": lang})
    loc = "de-DE" if lang == "de" else "en-GB"
    async with async_playwright() as p:
        b = await p.chromium.launch(args=[f"--lang={loc}"])
        # Anmeldeseite (direkter Zugriff)
        ctx = await b.new_context(viewport={"width": 1280, "height": 860}, color_scheme="light", locale=loc,
                                  timezone_id="Europe/Berlin")
        pg = await ctx.new_page()
        await pg.goto(direct + "/login")
        await pg.fill("input[name=username]", "admin")
        await pg.screenshot(path=str(out / "login.png"), clip={"x": 390, "y": 150, "width": 500, "height": 560})
        await ctx.close()
        # alles andere wie in der Seitenleiste von Home Assistant
        ctx = await b.new_context(viewport={"width": 1280, "height": 860}, color_scheme="light", locale=loc,
                                  timezone_id="Europe/Berlin", extra_http_headers={"X-Remote-User-Display-Name": "Stefan"})
        pg = await ctx.new_page()
        await pg.goto(ingress + "/")
        await pg.evaluate("localStorage.clear()")
        await pg.goto(ingress + "/")
        await pg.wait_for_timeout(3000)
        await pg.screenshot(path=str(out / "uebersicht.png"), full_page=True)
        await pg.screenshot(path=str(out / "kopf.png"), clip={"x": 0, "y": 0, "width": 1280, "height": 250})
        await pg.click("#newJobBtn")
        await pg.wait_for_timeout(400)
        await pg.fill("#jobForm input[name=title]", "Deep House Session")
        await pg.fill("#jobForm input[name=host]", "DJ Beispiel")
        await pg.select_option("#jobForm select[name=repeat]", "weekly")
        await pg.wait_for_timeout(200)
        await pg.locator("#jobDlg").screenshot(path=str(out / "auftrag.png"))
        await pg.keyboard.press("Escape")
        await pg.click("#nowBtn")
        await pg.wait_for_timeout(400)
        await pg.locator("#nowDlg").screenshot(path=str(out / "jetzt.png"))
        await pg.keyboard.press("Escape")
        await pg.click("nav button[data-view=senders]")
        await pg.wait_for_timeout(500)
        await pg.click("#sendersTbl button[data-test]")
        await pg.wait_for_timeout(7000)
        await pg.screenshot(path=str(out / "sender.png"), full_page=True)
        await pg.click("nav button[data-view=settings]")
        await pg.wait_for_timeout(800)
        await pg.screenshot(path=str(out / "einstellungen.png"), full_page=True)
        await pg.locator("#cutForm").evaluate("f => f.closest('section').scrollIntoView()")
        await pg.locator("section.card:has(#cutForm)").screenshot(path=str(out / "einstellungen-schnitt.png"))
        await pg.click("nav button[data-view=library]")
        await pg.wait_for_timeout(1500)
        boxes = pg.locator("#libTbl input[data-sel]")
        await boxes.nth(0).check()
        await boxes.nth(2).check()
        await pg.locator("#libTbl [data-play]").first.click()
        await pg.wait_for_timeout(2500)
        await pg.set_viewport_size({"width": 1280, "height": 1020})
        await pg.wait_for_timeout(400)
        await pg.screenshot(path=str(out / "aufnahmen.png"))
        await pg.click('#libSel [data-act="rename"]')
        await pg.wait_for_timeout(1200)
        await pg.locator("#renDlg").screenshot(path=str(out / "umbenennen.png"))
        await pg.keyboard.press("Escape")
        await pg.locator('#libTbl tr[data-path*="Deep House"] [data-row="tags"]').click()
        await pg.wait_for_timeout(900)
        await pg.locator("#tagDlg").screenshot(path=str(out / "tags.png"))
        await pg.keyboard.press("Escape")
        await pg.click('#libKind button[data-k="track"]')
        await pg.wait_for_timeout(1200)
        await pg.screenshot(path=str(out / "einzeltitel.png"), clip={"x": 0, "y": 0, "width": 1280, "height": 560})
        await pg.click('#libKind button[data-k="all"]')
        await pg.wait_for_timeout(1200)
        await pg.locator('#libTbl tr[data-path*="Lounge am Abend.mp3"] [data-row="cut"]').click()
        await pg.wait_for_timeout(1500)
        await pg.locator("#cutDlg").screenshot(path=str(out / "schneiden.png"))
        await pg.keyboard.press("Escape")
        await pg.click("#plClose")
        await pg.click("#libBin")
        await pg.wait_for_timeout(900)
        await pg.locator("#binTbl input[data-bsel]").first.check()
        await pg.wait_for_timeout(200)
        await pg.locator("#binDlg").screenshot(path=str(out / "papierkorb.png"))
        await pg.keyboard.press("Escape")
        await pg.set_viewport_size({"width": 1280, "height": 860})
        await pg.click("nav button[data-view=log]")
        await pg.wait_for_timeout(800)
        await pg.screenshot(path=str(out / "log.png"), clip={"x": 0, "y": 0, "width": 1280, "height": 520})
        await b.close()


def english_history(db_path: Path):
    """Die Beispiel-Einträge unter „Erledigt“ so, wie sie die App auf Englisch geschrieben hätte."""
    con = sqlite3.connect(db_path)
    for de, en in (("Titel, Interpret, Album, Datum, Genre", "Title, Artist, Album, Date, Genre"),
                   ("Titelliste", "Track list"), ("Kapitel", "Chapters"),
                   ("nicht geschrieben (mutagen fehlt)", "not written (mutagen missing)")):
        con.execute("UPDATE history SET tags=replace(tags, ?, ?)", (de, en))
    for de, en in (("Unterbrechung(en)", "interruption(s)"), ("verpasst – App oder Home Assistant war nicht aktiv",
                   "missed – app or Home Assistant was not running"), ("abgebrochen von", "stopped by"),
                   ("unvollständig:", "incomplete:"), (" von ", " of ")):
        con.execute("UPDATE history SET message=replace(message, ?, ?)", (de, en))
    con.commit()
    con.close()


def main():
    fs, ing, direct = free_port(8000), free_port(8099), free_port(8765)
    host = "stream.beispielradio.de" if host_ok("stream.beispielradio.de") else "127.0.0.1"
    stream = f"http://{host}:{fs}/play.m3u"
    tmp = Path(tempfile.mkdtemp(prefix="sr-manual-"))
    storage = prepare(Path("/media/stream-recorder"), tmp / "media" / "stream-recorder")
    data = prepare(Path("/data"), tmp / "data")
    (data / "options.json").write_text(json.dumps({"storage_path": "/media/stream-recorder", "username": "admin",
                                                   "password": PW, "log_level": "info"}))
    env = dict(os.environ, STREAM_RECORDER_DATA_DIR=str(data), STREAM_RECORDER_STORAGE=str(storage),
               STREAM_RECORDER_LISTEN_HOST="127.0.0.1", STREAM_RECORDER_INGRESS_PORT=str(ing),
               STREAM_RECORDER_DIRECT_PORT=str(direct), STREAM_RECORDER_TRUSTED_PROXY="127.0.0.1",
               STREAM_RECORDER_SUPERVISOR_URL=f"http://127.0.0.1:{fs}", SUPERVISOR_TOKEN="manual",
               STREAM_RECORDER_STDIN="0",
               STREAM_RECORDER_COVER_DEEZER=f"http://127.0.0.1:{fs}/deezer/search",
               STREAM_RECORDER_COVER_ITUNES=f"http://127.0.0.1:{fs}/itunes/search")
    os.environ.update(STREAM_RECORDER_DATA_DIR=str(data))
    seed(data / "recorder.db", str(storage), stream)
    procs = [subprocess.Popen([sys.executable, str(ROOT / "tests/fake_stream.py"), str(fs)]),
             subprocess.Popen([sys.executable, str(APP / "stream_recorder.py")], env=env)]
    try:
        for _ in range(100):
            try:
                httpx.get(f"http://127.0.0.1:{direct}/login", timeout=1)
                httpx.get(f"http://127.0.0.1:{fs}/_ha", timeout=1)
                break
            except httpx.HTTPError:
                time.sleep(0.2)
        # ein Stream-Test fürs Log
        httpx.post(f"http://127.0.0.1:{ing}/api/stream-test", headers={"X-Recorder": "1"}, json={"url": stream}, timeout=30)
        for lang in ("de", "en"):
            if lang == "en":
                english_history(data / "recorder.db")
            asyncio.run(shoot(f"http://127.0.0.1:{ing}", f"http://127.0.0.1:{direct}", f"http://127.0.0.1:{fs}", lang))
        print("Bilder in", OUT)
        for f in sorted(OUT.rglob("*.png")):
            print(f"  {f.relative_to(OUT)!s:28} {f.stat().st_size // 1024} kB")
    finally:
        for p in procs:
            p.terminate()


if __name__ == "__main__":
    main()
