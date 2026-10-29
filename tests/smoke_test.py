"""Automatischer Funktionstest der App gegen nachgebaute Streams und einen nachgebauten Supervisor.

    python3 tests/smoke_test.py

Startet tests/fake_stream.py (Streams, Cover-Suche, Home-Assistant-API) und die App mit
temporärem Daten- und Speicherordner. Prüft Ingress (Seitenleiste) und direkten Zugriff mit
Login, Aufnahmen, ID3-Tags, Sensoren und Ereignisse in Home Assistant, Befehle per stdin,
Seite Aufnahmen, Schneiden, Cover, Papierkorb, leere Ordner, Sprache und Log.
Dauer rund 60 s. Exit-Code 0 = alles ok.
"""
import json
import os
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent))
from demo_files import make_mp3, tracklist  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "stream_recorder" / "app" / "stream_recorder.py"
PW = "testpasswort123"


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def wait(url: str, timeout: float = 20) -> None:
    end = time.time() + timeout
    while time.time() < end:
        try:
            httpx.get(url, timeout=1)
            return
        except httpx.HTTPError:
            time.sleep(0.2)
    raise RuntimeError(f"{url} nicht erreichbar")


def app_env(tmp: Path, name: str, fs: str, options: dict, **extra) -> tuple[dict, int, int]:
    data = tmp / name / "data"
    data.mkdir(parents=True, exist_ok=True)
    (data / "options.json").write_text(json.dumps(options))
    ing, direct = free_port(), free_port()
    env = dict(os.environ, STREAM_RECORDER_DATA_DIR=str(data),
               STREAM_RECORDER_STORAGE=str(tmp / name / "media" / "stream-recorder"),
               STREAM_RECORDER_LISTEN_HOST="127.0.0.1", STREAM_RECORDER_INGRESS_PORT=str(ing),
               STREAM_RECORDER_DIRECT_PORT=str(direct), STREAM_RECORDER_TRUSTED_PROXY="127.0.0.1",
               STREAM_RECORDER_SUPERVISOR_URL=fs, SUPERVISOR_TOKEN="test-token",
               STREAM_RECORDER_COVER_DEEZER=f"{fs}/deezer/search", STREAM_RECORDER_COVER_ITUNES=f"{fs}/itunes/search")
    env.update(extra)
    return env, ing, direct


def main() -> int:
    fs_port = free_port()
    tmp = Path(tempfile.mkdtemp(prefix="stream-recorder-test-"))
    FS = f"http://127.0.0.1:{fs_port}"
    opts = {"storage_path": "/media/stream-recorder", "username": "admin", "password": PW, "log_level": "debug"}
    env, ING, DIR = app_env(tmp, "a", FS, opts)
    S = tmp / "a" / "media" / "stream-recorder"
    REC, TIT, DEL = S / "record", S / "title", S / "deleted"
    DATA = tmp / "a" / "data"
    out = open(tmp / "a" / "stdout.txt", "wb")
    procs = [subprocess.Popen([sys.executable, str(ROOT / "tests/fake_stream.py"), str(fs_port)])]
    app = subprocess.Popen([sys.executable, str(APP)], env=env, stdin=subprocess.PIPE, stdout=out, stderr=out)
    procs.append(app)
    failures = []

    def check(name, cond, info=""):
        print(("  ok   " if cond else "  FAIL ") + name + (f"  [{info}]" if info and not cond else ""))
        if not cond:
            failures.append(name)

    def stdin(obj, newline=True):
        raw = obj if isinstance(obj, str) else json.dumps(obj)
        app.stdin.write((raw + ("\n" if newline else "")).encode())
        app.stdin.flush()

    def ha():
        return httpx.get(f"{FS}/_ha").json()

    try:
        wait(f"{FS}/play.pls")
        wait(f"http://127.0.0.1:{DIR}/login")
        H = {"X-Recorder": "1"}
        # Ingress: so ruft der Supervisor die App auf (Home Assistant hat schon angemeldet)
        c = httpx.Client(base_url=f"http://127.0.0.1:{ING}", timeout=40,
                         headers={"X-Remote-User-Display-Name": "Stefan", "X-Ingress-Path": "/api/hassio_ingress/abc"})
        d = httpx.Client(base_url=f"http://127.0.0.1:{DIR}", timeout=40)

        print("Ingress (Seitenleiste):")
        r = c.get("/")
        check("Oberfläche ohne Login", r.status_code == 200 and 'data-auth="ingress"' in r.text)
        check("nur relative Adressen (Ingress-Pfad)", 'href="/' not in r.text and 'fetch("/' not in r.text
              and 'src="/' not in r.text, [x for x in ('href="/', 'fetch("/', 'src="/') if x in r.text])
        st = c.get("/api/state").json()
        check("Benutzer aus Home Assistant", st["user"] == "Stefan" and st["auth"] == "ingress", st.get("user"))
        check("Login-Seite führt zurück", c.get("/login").status_code == 303)
        check("POST ohne X-Recorder → 403", c.post("/api/jobs", json={}).status_code == 403)
        check("Feste Ordner record/title/deleted angelegt", REC.is_dir() and TIT.is_dir() and DEL.is_dir())
        check("Sprache von Home Assistant (de)", st["lang"] == "de")
        check("Spendenknopf", st["donate"] == "https://paypal.me/breitide" and "paypal.me/breitide" in r.text)
        check("Sprachdatei", "SR_EN" in c.get("/lang-en.js").text)
        r = c.get("/manual")
        check("Anleitung (deutsch)", r.status_code == 200 and "Bedienungsanleitung" in r.text)
        check("Anleitung (englisch)", "User manual" in c.get("/manual", params={"lang": "en"}).text)
        check("Anleitungsbild", c.get("/manual/de/uebersicht.png").headers.get("content-type") == "image/png")
        check("Bildpfad abgesichert", c.get("/manual/de/..%2F..%2Fstream_recorder.py").status_code == 404)

        print("Direkter Zugriff (eigener Port mit Login):")
        r = d.get("/")
        check("ohne Anmeldung → /login", r.status_code == 303 and r.headers["location"] == "/login")
        check("API ohne Anmeldung → 401", d.get("/api/state").status_code == 401)
        check("Anleitung nur mit Anmeldung", d.get("/manual").status_code == 303)
        r = d.post("/login", data={"username": "admin", "password": "falsch"})
        check("falsches Passwort abgelehnt", r.status_code == 303 and "e=1" in r.headers["location"])
        check("Fehlermeldung auf der Login-Seite", "falsch" in d.get("/login?e=1").text)
        r = d.post("/login", data={"username": "admin", "password": PW, "remember": "1"})
        check("Anmeldung klappt", r.status_code == 303 and "sr_session" in d.cookies)
        check("Oberfläche ausgeliefert", 'data-auth="login"' in d.get("/").text)
        st = d.get("/api/state").json()
        check("Status-API mit Benutzer", st["user"] == "admin" and st["auth"] == "login")
        check("Sicherheits-Header (kein Einbetten)", d.get("/").headers.get("x-frame-options") == "DENY"
              and c.get("/").headers.get("x-frame-options") == "SAMEORIGIN")

        print("Stream-Test:")
        r = c.post("/api/stream-test", headers=H, json={"url": f"{FS}/play.m3u"}).json()
        check("m3u → Redirect → MP3", r.get("ok") and r["content_type"] == "audio/mpeg" and r["final_url"].endswith("/stream.mp3"), r)
        check("ICY-Titel und Bitrate erkannt", r.get("bitrate") == 128 and "Track" in (r.get("title") or ""), r)
        check("pls aufgelöst", c.post("/api/stream-test", headers=H, json={"url": f"{FS}/play.pls"}).json().get("ok"))
        r = c.post("/api/stream-test", headers=H, json={"url": f"{FS}/html"}).json()
        check("Webseite wird abgelehnt", not r.get("ok") and "Webseite" in r.get("error", ""), r)

        print("Einstellungen und Sender:")
        r = c.put("/api/settings", headers=H, json={"pre_roll_min": 0, "post_roll_min": 0})
        s = r.json()
        check("Einstellungen speichern", r.status_code == 200 and s["pre_roll_min"] == 0)
        check("Ordner fest unter dem Speicherordner", s["record_dir"] == str(REC) and s["title_dir"] == str(TIT)
              and s["trash_dir"] == str(DEL), s)
        check("Unbekannter Platzhalter abgelehnt", c.put("/api/settings", headers=H, json={"file_pattern": "{quatsch}"}).status_code == 400)
        c.post("/api/senders", headers=H, json={"name": "Fake Lounge", "url": f"{FS}/play.m3u"})
        senders = c.get("/api/senders").json()
        sid = next(x["id"] for x in senders if x["name"] == "Fake Lounge")
        check("Sender angelegt (+ sunshine live vorbelegt)", len(senders) == 2)

        print("Aufnahmen:")
        c.post("/api/record-now", headers=H, json={"sender_id": sid, "title": "Test OK", "duration_min": 0.12})
        c.post("/api/record-now", headers=H, json={"url": f"{FS}/drop.mp3", "sender": "Drop", "title": "Test Drop", "duration_min": 0.15})
        c.post("/api/record-now", headers=H, json={"url": f"{FS}/gibtsnicht", "title": "Test 404", "duration_min": 0.05})
        c.post("/api/record-now", headers=H, json={"sender_id": sid, "title": "Test Stop", "duration_min": 5})
        # Befehle aus Home Assistant (hassio.app_stdin): zwei JSON-Werte ohne Zeilenumbruch dazwischen
        stdin({"action": "record", "sender": "fake lounge", "title": "Per HA", "duration": 0.1}, newline=False)
        stdin({"action": "record", "sender": "Fake Lounge", "title": "HA Stop", "duration": 5})
        stdin({"action": "record", "sender": "Gibt es nicht"})
        time.sleep(3.5)
        st = c.get("/api/state").json()
        running = [j for j in st["jobs"] if j["running"]]
        check("laufende Aufnahmen sichtbar", len(running) >= 4 and running[0]["running"]["bytes"] > 0, len(running))
        states = ha()["states"]
        rec_state = states.get("binary_sensor.stream_recorder_recording", {})
        check("HA: binary_sensor Aufnahme läuft = on", rec_state.get("state") == "on"
              and rec_state["attributes"]["count"] >= 4, rec_state)
        stop = next(j for j in st["jobs"] if j["title"] == "Test Stop")
        check("Abbrechen", c.post(f"/api/jobs/{stop['id']}/stop", headers=H).status_code == 200)
        stdin("{\"action\": \"stop\", \"title\": \"HA Stop\"}")
        time.sleep(8)
        hist = {h["title"]: h for h in c.get("/api/history").json()}
        ok, drop, e404, stp = (hist.get(k) for k in ("Test OK", "Test Drop", "Test 404", "Test Stop"))
        check("Aufnahme erfolgreich", ok and ok["status"] == "ok", ok)
        check("Datei unter record/Sender/Jahr-Monat", ok and Path(ok["path"]).exists()
              and Path(ok["path"]).parent.parent == REC / "Fake Lounge", ok and ok["path"])
        check("Dauer gemessen", ok and 6.5 <= ok["duration_s"] <= 9, ok and (ok["duration_s"], ok["bytes"], ok["reconnects"], ok["started"], ok["ended"], ok["window_start"], ok["window_end"], ok["message"]))
        check("Reconnect nach Abbruch", drop and drop["reconnects"] >= 1, drop)
        check("404 → Fehler mit Meldung", e404 and e404["status"] == "error" and "404" in (e404["message"] or ""), e404)
        check("Stopp → abgebrochen", stp and stp["status"] == "aborted" and "Stefan" in stp["message"], stp)
        check("HA-Befehl: Aufnahme gestartet", hist.get("Per HA") and hist["Per HA"]["status"] == "ok", hist.get("Per HA"))
        check("HA-Befehl: Aufnahme gestoppt", hist.get("HA Stop") and hist["HA Stop"]["status"] == "aborted"
              and "Home Assistant" in hist["HA Stop"]["message"], hist.get("HA Stop"))
        check("keine .part-Dateien übrig", not list(S.rglob("*.part")))
        check("Aufträge danach entfernt", not [j for j in c.get("/api/state").json()["jobs"] if j["title"].startswith(("Test", "Per HA", "HA "))])
        if ok and ok["path"]:
            from mutagen.id3 import ID3
            t = ID3(ok["path"])
            check("ID3: Titel, Album, Titelliste, Kapitel",
                  str(t["TIT2"]) == "Test OK" and str(t["TALB"]) == "Fake Lounge"
                  and "Track" in str(t.getall("COMM")[0]) and t.getall("CHAP"), ok["tags"])
            check("Download", c.get(f"/api/history/{ok['id']}/file").content[:3] == b"ID3")

        print("Home Assistant:")
        time.sleep(1)
        h = ha()
        states = h["states"]
        check("Token wird mitgeschickt", h["auth_ok"])
        check("Sensoren angelegt", all(e in states for e in (
            "binary_sensor.stream_recorder_recording", "sensor.stream_recorder_next_recording",
            "sensor.stream_recorder_last_recording", "sensor.stream_recorder_planned", "sensor.stream_recorder_disk_free")), list(states))
        check("binary_sensor wieder off", states["binary_sensor.stream_recorder_recording"]["state"] == "off")
        last = states["sensor.stream_recorder_last_recording"]
        check("letzte Aufnahme mit Status und Datei", last["state"] in ("ok", "error", "aborted") and "file" in last["attributes"], last)
        check("Speicher frei in GB", float(states["sensor.stream_recorder_disk_free"]["state"]) > 0)
        ev = [(e["event"], e["data"]["title"]) for e in h["events"]]
        check("Ereignis stream_recorder_finished", ("stream_recorder_finished", "Test OK") in ev, ev)
        check("Ereignis stream_recorder_error bei Fehler", ("stream_recorder_error", "Test 404") in ev
              and ("stream_recorder_error", "Test OK") not in ev, ev)
        fin = next(e["data"] for e in h["events"] if e["event"] == "stream_recorder_finished" and e["data"]["title"] == "Test OK")
        check("Ereignis mit Datei und Dauer", fin["status"] == "ok" and fin["file"].endswith(".mp3") and fin["duration_s"] >= 6, fin)

        print("Aufträge:")
        r = c.post("/api/jobs", headers=H, json={"title": "Zukunft", "sender_id": sid, "start": "2099-01-01T20:00",
                                                 "duration_min": 60, "pre_min": 2, "post_min": 5, "repeat": "weekly"})
        jid = r.json().get("id")
        check("Auftrag anlegen", r.status_code == 200 and jid, r.text)
        time.sleep(0.5)
        nxt = ha()["states"]["sensor.stream_recorder_next_recording"]
        check("HA: nächste Aufnahme", nxt["attributes"]["title"] == "Zukunft" and nxt["state"].startswith("2099-01-01T19:58"), nxt)
        r = c.put(f"/api/jobs/{jid}", headers=H, json={"title": "Zukunft 2", "url": f"{FS}/stream.mp3", "start": "2099-01-02T21:00",
                                                       "duration_min": 30, "repeat": "once"})
        j = next(x for x in c.get("/api/state").json()["jobs"] if x["id"] == jid)
        check("Auftrag ändern", r.status_code == 200 and j["title"] == "Zukunft 2" and j["duration_min"] == 30, r.text)
        check("Validierung", c.post("/api/jobs", headers=H, json={"title": "x", "url": "ftp://x", "start": "2099-01-01T20:00",
                                                                    "duration_min": 5}).status_code == 400)
        check("Auftrag löschen", c.delete(f"/api/jobs/{jid}", headers=H).status_code == 200)

        print("Seite Aufnahmen:")
        make_mp3(REC / "Beispiel" / "2026-10" / "2026-10-25_2100_Deep.mp3", seconds=7200,
                 tags={"title": "Deep House", "artist": "DJ X", "album": "Beispiel", "date": "2026-10-25T21:00"},
                 tracks=tracklist(7200, 12))
        make_mp3(REC / "Archiv" / "Fremd ohne Tags.mp3", seconds=600)
        for k in range(12):
            make_mp3(REC / "Viele" / f"Datei {k:02d}.mp3", seconds=60,
                     tags={"title": f"Stück {k:02d}", "artist": "A", "album": "Viele", "date": "2026-01-01"})
        (REC / "Archiv" / "notiz.txt").write_text("kein Audio")
        L = c.get("/api/library", params={"rescan": "1", "per": 10}).json()
        n_all = L["stats"]["files"]
        check("record/ eingelesen (kein .txt, Pfade mit record/)", n_all >= 16 and all(p.startswith("record/") for p in L["all_paths"])
              and not any(p.endswith(".txt") for p in L["all_paths"]), n_all)
        check("Seiten: 10 pro Seite", len(L["items"]) == 10 and L["pages"] == -(-L["total"] // 10))
        L2 = c.get("/api/library", params={"per": 10, "page": 2}).json()
        check("Seite 2", L2["page"] == 2 and not set(i["path"] for i in L2["items"]) & set(i["path"] for i in L["items"]))
        check("alle anzeigen", len(c.get("/api/library", params={"per": 0}).json()["items"]) == n_all)
        check("Suche in Titelliste", c.get("/api/library", params={"q": "ocean drive"}).json()["total"] == 1)
        ext = next(i for i in c.get("/api/library", params={"q": "fremd"}).json()["items"])
        check("Fremde Datei als extern", ext["history"] is None and ext["tag_state"] == "none")
        okitem = next(i for i in c.get("/api/library", params={"per": 0}).json()["items"] if i["title"] == "Test OK")
        check("Recorder-Datei mit Status aus Erledigt", okitem["history"] and okitem["history"]["status"] == "ok")
        for bad in ("../data/options.json", "record/../../a/data/options.json", "deleted/x.mp3", "/etc/passwd"):
            check(f"Pfad abgelehnt: {bad}", c.get("/api/library/item", params={"path": bad}).status_code == 400)
        FR = "record/Archiv/Fremd ohne Tags.mp3"
        r = c.get("/api/library/stream", params={"path": FR}, headers={"Range": "bytes=0-99"})
        check("Abspielen mit Range-Anfrage", r.status_code == 206 and len(r.content) == 100)
        it = c.get("/api/library/item", params={"path": "record/Beispiel/2026-10/2026-10-25_2100_Deep.mp3"}).json()
        check("Titelliste und Kapitel gelesen", len(it["tracklist"]) == 12 and len(it["chapter_list"]) == 12)
        check("Titelliste als .txt", "Ocean Drive" in c.get("/api/library/tracklist", params={"path": it["path"]}).text)
        r = c.put("/api/library/tags", headers=H, json={"path": FR, "title": "Interview", "artist": "Gast", "album": "Archiv",
                                                       "date": "2026-03-14 11:00",
                                                       "cover": "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="})
        it2 = c.get("/api/library/item", params={"path": FR}).json()
        check("Tags schreiben inkl. Cover", r.status_code == 200 and it2["tag_state"] == "full" and it2["has_cover"], r.text)
        check("Datum wird geprüft", c.put("/api/library/tags", headers=H, json={"path": FR, "date": "gestern"}).status_code == 400)
        c.put("/api/library/tags", headers=H, json={"path": okitem["path"], "title": "", "artist": "", "album": ""})
        r = c.post("/api/library/fill", headers=H, json={"paths": [okitem["path"]]}).json()
        it3 = c.get("/api/library/item", params={"path": okitem["path"]}).json()
        check("Tags aus Auftrag ergänzt", r["done"] == 1 and it3["title"] == "Test OK" and it3["album"] == "Fake Lounge")
        paths = [okitem["path"], FR, "record/Viele/Datei 00.mp3", "record/Viele/Datei 01.mp3"]
        pat = "{interpret} - {titel}"
        pv = c.post("/api/library/rename/preview", headers=H, json={"paths": paths, "pattern": pat, "skip_empty": True}).json()
        news = {x["path"]: x["new"] for x in pv["plan"]}
        check("Umbenennen: Vorschau", news[FR] == "Gast - Interview.mp3" and pv["count"] == 4, pv)
        r = c.post("/api/library/rename", headers=H, json={"paths": paths, "pattern": pat, "skip_empty": True}).json()
        check("Umbenennen", r["done"] == 4 and (REC / "Archiv" / "Gast - Interview.mp3").exists() and r["undo"]["count"] == 4, r)
        h = next(x for x in c.get("/api/history").json() if x["title"] == "Test OK")
        check("Erledigt zeigt auf neuen Namen", h["file_name"] == news[okitem["path"]] and h["file_exists"])
        r = c.post("/api/library/undo", headers=H).json()
        check("Umbenennung zurückgenommen", r["done"] == 4 and (REC / "Archiv" / "Fremd ohne Tags.mp3").exists())

        print("Papierkorb (deleted/):")
        r = c.post("/api/library/trash", headers=H, json={"paths": [FR, "record/Viele/Datei 11.mp3"]}).json()
        check("In den Papierkorb (Unterordner bleiben)", r["done"] == 2 and not (REC / "Archiv" / "Fremd ohne Tags.mp3").exists()
              and (DEL / "record" / "Archiv" / "Fremd ohne Tags.mp3").exists() and (DEL / "record" / "Viele" / "Datei 11.mp3").exists(), r)
        check("Liste ohne gelöschte Dateien", c.get("/api/library").json()["stats"]["files"] == n_all - 2)
        T = c.get("/api/trash").json()
        check("Papierkorb-Liste mit Titel", T["count"] == 2 and any(i["title"] == "Interview" and i["orig"] == FR for i in T["items"]), T)
        check("Zähler in der Statusabfrage", c.get("/api/state").json()["trash"]["count"] == 2)
        make_mp3(REC / "Archiv" / "Fremd ohne Tags.mp3", seconds=30)           # Name inzwischen wieder belegt
        tid = next(i["id"] for i in T["items"] if i["orig"] == FR)
        r = c.post("/api/trash/restore", headers=H, json={"ids": [tid]}).json()
        check("Wiederherstellen mit (2) bei belegtem Namen", r["done"] == 1 and r["paths"] == ["record/Archiv/Fremd ohne Tags (2).mp3"]
              and not (DEL / "record" / "Archiv").exists(), r)
        tid2 = next(i["id"] for i in T["items"] if i["orig"] != FR)
        r = c.post("/api/trash/delete", headers=H, json={"ids": [tid2]}).json()
        check("Endgültig löschen", r["done"] == 1 and not (DEL / "record" / "Viele").exists() and DEL.is_dir())
        c.post("/api/library/trash", headers=H, json={"paths": ["record/Viele/Datei 10.mp3"]})
        (DEL / "record" / "fremd.mp3").write_bytes(b"x" * 100)                      # von Hand hineingelegt
        T = c.get("/api/trash").json()
        check("Von Hand hineingelegte Datei wird erkannt", T["count"] == 2 and any(i["name"] == "fremd.mp3" for i in T["items"]), T)
        con = sqlite3.connect(DATA / "recorder.db")
        con.execute("UPDATE trash SET deleted=? WHERE orig=?", (time.time() - 3 * 86400, "record/Viele/Datei 10.mp3"))
        con.commit()
        con.close()
        c.put("/api/settings", headers=H, json={"trash_days": 2})
        T = c.get("/api/trash").json()
        check("Automatisch leeren nach Tagen", T["count"] == 1 and T["days"] == 2 and not (DEL / "record" / "Viele").exists(), T)
        check("Tage werden geprüft", c.put("/api/settings", headers=H, json={"trash_days": -1}).status_code == 400)
        c.put("/api/settings", headers=H, json={"trash_days": 0})
        r = c.post("/api/trash/empty", headers=H).json()
        check("Papierkorb leeren", r["done"] == 1 and c.get("/api/trash").json()["count"] == 0 and DEL.is_dir()
              and not any(DEL.iterdir()), r)
        check("Papierkorb außerhalb abgelehnt", c.post("/api/library/trash", headers=H,
              json={"paths": ["../a/data/options.json"]}).json()["done"] == 0 and (DATA / "options.json").exists())

        print("Schneiden (Einzeltitel → title/):")
        cut_tracks = [(0, "A - Angeschnitten"), (10, "Künstler B - Zwei"), (30, "Fake Lounge - Jingle"),
                      (34, "Künstler C - Drei"), (55, "Künstler D - Kurz"), (60, "Künstler E - Fünf"), (80, "F - Ende")]
        cut_src = make_mp3(REC / "Schnitt" / "2026-10-25_2000_Mix.mp3", seconds=90, real_seconds=90,
                           tags={"title": "Mix am Abend", "artist": "Fake Lounge", "album": "Fake Lounge",
                                 "date": "2026-10-25T20:00", "genre": "Lounge"}, tracks=cut_tracks)
        rel = "record/Schnitt/2026-10-25_2000_Mix.mp3"
        r = c.put("/api/settings", headers=H, json={"cut_min_len": 15, "cut_offset": 0})
        check("Einstellungen Schneiden", r.status_code == 200 and r.json()["cut_min_len"] == 15)
        check("Muster wird geprüft", c.put("/api/settings", headers=H, json={"cut_pattern": "{quatsch}"}).status_code == 400)
        it = next(i for i in c.get("/api/library", params={"rescan": "1", "per": 0}).json()["items"] if i["path"] == rel)
        check("Aufnahme mit Titelliste ist schneidbar", it["cuttable"] and it["kind"] == "recording")
        pv = c.post("/api/library/cut/preview", headers=H, json={"paths": [rel, "record/Viele/Datei 05.mp3", "record/Gibt/es nicht.mp3"]}).json()
        f0 = pv["files"][0]
        sel = [x["raw"] for x in f0["segments"] if x["selected"]]
        reasons = {x["raw"]: x["reason"] for x in f0["segments"]}
        check("Vorschau: erster/letzter, Jingle und zu kurz abgewählt",
              sel == ["Künstler B - Zwei", "Künstler C - Drei", "Künstler E - Fünf"]
              and "erster" in reasons["A - Angeschnitten"] and "Senderkennung" in reasons["Fake Lounge - Jingle"], reasons)
        check("Datei ohne Titelliste nicht schneidbar", not pv["files"][1]["ok"] and "Titelliste" in pv["files"][1]["error"])
        check("Fehlende Datei nur als Fehler dieser Datei", not pv["files"][2]["ok"] and "nicht gefunden" in pv["files"][2]["error"])
        check("Ziel unter title/", f0["folder"] == str(TIT / "Fake Lounge" / "2026-10-25 Mix am Abend"), f0["folder"])
        before = cut_src.read_bytes()
        idx = [x["i"] for x in f0["segments"] if x["selected"]]
        r = c.post("/api/library/cut", headers=H, json={"items": [{"path": rel, "selected": idx}]}).json()
        made = r["files"][0]["created"]
        check("3 Einzeltitel angelegt", r["created"] == 3 and len(made) == 3, r)
        check("Aufnahme bleibt unverändert", cut_src.read_bytes() == before)
        check("Ordner und Name nach Muster", made[0] == "title/Fake Lounge/2026-10-25 Mix am Abend/01 Künstler B - Zwei.mp3", made[0])
        from mutagen.id3 import ID3
        from mutagen.mp3 import MP3
        first = S / made[0]
        tg = ID3(first)
        check("Tags: Interpret, Titel, Album = Sender, Nr", str(tg["TPE1"]) == "Künstler B" and str(tg["TIT2"]) == "Zwei"
              and str(tg["TALB"]) == "Fake Lounge" and str(tg["TRCK"]) == "1/3")
        check("Länge stimmt (20 s ± 0,1)", abs(MP3(first).info.length - 20) < 0.1)
        L = c.get("/api/library", params={"kind": "track", "per": 0}).json()
        check("Filter Einzeltitel", L["total"] == 3 and all(i["kind"] == "track" and i["path"].startswith("title/") for i in L["items"]))
        check("Einzeltitel kennt die Quelle", all(i["source"].startswith(rel) for i in L["items"]))
        r = c.post("/api/library/cut", headers=H, json={"items": [{"path": rel, "selected": idx[:1]}]}).json()
        check("Doppelt schneiden: Kopie mit (2)", r["files"][0]["created"] == [
            "title/Fake Lounge/2026-10-25 Mix am Abend/01 Künstler B - Zwei (2).mp3"], r)
        pv = c.post("/api/library/cut/preview", headers=H, json={"paths": [okitem["path"]], "min_len": 0}).json()["files"][0]
        segs = [x for x in pv["segments"] if x["selected"]]
        check("Echte Recorder-Aufnahme: Titelwechsel als Schnitte", pv["ok"] and len(segs) >= 1)
        print("Cover:")
        r = c.post("/api/library/cut", headers=H, json={"items": [{"path": rel, "selected": idx}]}).json()
        made3 = [S / m for m in r["files"][0]["created"]]
        check("Schnitt mit Cover-Suche: Deezer + iTunes gefunden", r["covers_online"] == 2 and r["cover_lookup"], r)
        pics = {ID3(m)["TIT2"].text[0]: bool(ID3(m).getall("APIC")) for m in made3}
        check("Titel ohne Treffer bleibt ohne Cover", pics == {"Zwei": True, "Drei": True, "Fünf": False}, pics)
        r = c.post("/api/library/cover-lookup", headers=H, json={"artist": "SRTW, Charity Children", "title": "Butterflies"}).json()
        check("Tag-Dialog-Suche", r["found"] and r["source"] == "Deezer", r.get("info"))
        c.put("/api/settings", headers=H, json={"cover_lookup": False})
        r = c.post("/api/library/cut", headers=H, json={"items": [{"path": rel, "selected": idx[:1]}]}).json()
        check("Cover-Suche abschaltbar", r["covers_online"] == 0 and not r["cover_lookup"], r)
        c.put("/api/settings", headers=H, json={"cover_lookup": True})

        print("Leere Ordner:")
        make_mp3(REC / "Leer-Test" / "2026-10" / "x.mp3", seconds=5)
        (REC / "Leer-Test" / "2026-10" / ".DS_Store").write_text("finder")
        c.get("/api/library", params={"rescan": "1"})
        r = c.post("/api/library/trash", headers=H, json={"paths": ["record/Leer-Test/2026-10/x.mp3"]}).json()
        check("Löschen entfernt leer gewordene Ordner", r["done"] == 1 and r["dirs_removed"] == 2 and not (REC / "Leer-Test").exists(), r)
        tracks = [p for p in c.get("/api/library", params={"kind": "track", "per": 0}).json()["all_paths"]]
        r = c.post("/api/library/trash", headers=H, json={"paths": tracks}).json()
        check("Alle Einzeltitel gelöscht: title/ bleibt", r["done"] == len(tracks) and TIT.is_dir() and not any(TIT.iterdir()), r)
        (REC / "Manuell" / "leer").mkdir(parents=True)
        found = sorted(x["path"] for x in c.get("/api/library/empty-dirs").json()["dirs"])
        check("Leere Ordner finden (ohne record/title/deleted)", found == ["record/Manuell", "record/Manuell/leer"], found)
        r = c.post("/api/library/empty-dirs", headers=H, json={"dirs": [str(REC / "Manuell"), str(REC / "Manuell" / "leer"),
                                                                      str(REC), str(TIT), str(DEL), str(S)]}).json()
        check("Entfernen: nie die Hauptordner", r["removed"] == 2 and REC.is_dir() and TIT.is_dir() and DEL.is_dir(), r)

        print("Sprache:")
        r = c.put("/api/settings", headers=H, json={"language": "en"}).json()
        check("Umschalten auf Englisch", r["lang"] == "en" and "station" in r["placeholders"]["rec"], r["placeholders"])
        check("Meldungen englisch", c.post("/api/jobs", headers=H, json={"title": ""}).json()["error"] == "Title missing")
        check("Oberfläche englisch", 'lang="en"' in c.get("/").text)
        check("Anleitung folgt der Sprache", "User manual" in c.get("/manual").text)
        pv = c.post("/api/library/rename/preview", headers=H, json={"paths": [it["path"]], "pattern": "{artist} - {title}"}).json()
        check("Englische Platzhalter", pv["plan"][0]["new"] == "Fake Lounge - Mix am Abend.mp3", pv["plan"])
        check("Login-Seite englisch", "Sign in" in httpx.get(f"http://127.0.0.1:{DIR}/login").text)
        httpx.post(f"{FS}/_ha", json={"language": "en"})
        r = c.put("/api/settings", headers=H, json={"language": "auto"}).json()
        check("Automatisch = Sprache von HA", r["language"] == "auto")
        c.put("/api/settings", headers=H, json={"language": "de"})
        httpx.post(f"{FS}/_ha", json={"language": "de"})

        print("Log:")
        log = c.get("/api/log").text
        check("Logdatei geschrieben", "Recording starts" in log and "Login admin" in log and "Command from Home Assistant" in log)
        check("Abgelehnter HA-Befehl im Log", "rejected: sender not found" in log)
        check("Log leeren", c.post("/api/log/clear", headers=H).status_code == 200 and "Recording starts" not in c.get("/api/log").text)
        check("Abmelden (direkt)", d.post("/api/logout", headers=H).status_code == 200)
        out.flush()
        console = (tmp / "a" / "stdout.txt").read_text("utf-8", "replace").strip()
        check("Nichts im HA-Log (stdout/stderr leer)", console == "", console[:300])

        print("Konfiguration:")
        bad = tmp / "opts-bad.json"
        bad.write_text(json.dumps({"storage_path": "/config/../etc", "username": "u", "password": ""}))
        r = subprocess.run([sys.executable, "-c", "import stream_recorder as s, json; print(json.dumps([s.CFG['storage_path'], s.CFG['warnings'], s.direct_enabled()]))"],
                           cwd=APP.parent, env=dict(os.environ, STREAM_RECORDER_OPTIONS=str(bad), STREAM_RECORDER_DATA_DIR=str(tmp / "cfg")),
                           capture_output=True, text=True)
        cfg = json.loads(r.stdout.strip().splitlines()[-1])
        check("Ungültiger Speicherort → Standard + Warnung", cfg[0] == "/media/stream-recorder" and cfg[1] and cfg[1][0][0] == "storage", cfg)
        check("Direktzugriff nur mit Benutzer und Passwort", cfg[2] is False)

        print("Zweite Instanz: ohne Login-Daten, ohne mutagen:")
        broken = tmp / "ohne-mutagen"
        (broken / "mutagen").mkdir(parents=True)
        (broken / "mutagen" / "__init__.py").write_text("raise ImportError('simuliert: mutagen fehlt')\n")
        env2, ing2, dir2 = app_env(tmp, "b", FS, {"storage_path": "/media/stream-recorder", "username": "", "password": ""},
                                   PYTHONPATH=str(broken), STREAM_RECORDER_STDIN="0")
        procs.append(subprocess.Popen([sys.executable, str(APP)], env=env2))
        wait(f"http://127.0.0.1:{dir2}/login")
        lp = httpx.get(f"http://127.0.0.1:{dir2}/login").text
        check("Direktzugriff aus: Hinweis auf der Login-Seite", "nicht eingerichtet" in lp)
        r = httpx.post(f"http://127.0.0.1:{dir2}/login", data={"username": "", "password": ""})
        check("Direktzugriff aus: keine Anmeldung möglich", "sr_session" not in r.cookies)
        c2 = httpx.Client(base_url=f"http://127.0.0.1:{ing2}", timeout=40)
        st2 = c2.get("/api/state").json()
        check("Warnung in der Oberfläche", any("mutagen" in w for w in st2.get("warnings", [])), st2.get("warnings"))
        c2.post("/api/record-now", headers=H, json={"url": f"{FS}/stream.mp3", "sender": "X", "title": "Ohne Tags", "duration_min": 0.08})
        time.sleep(7.5)
        h2 = next((x for x in c2.get("/api/history").json() if x["title"] == "Ohne Tags"), None)
        check("Aufnahme trotzdem erfolgreich", h2 and h2["status"] == "ok" and Path(h2["path"]).exists(), h2)
        check("Tags: Hinweis statt Fehler", h2 and "mutagen fehlt" in (h2["tags"] or ""), h2 and h2["tags"])
        log2 = (tmp / "b" / "data" / "log" / "stream-recorder.log").read_text("utf-8")
        check("Log: klarer Hinweis, kein Traceback", "mutagen' missing" in log2 and "Traceback" not in log2)

        print("Dritte Instanz: Ingress nur vom Supervisor:")
        env3, ing3, dir3 = app_env(tmp, "c", FS, opts, STREAM_RECORDER_TRUSTED_PROXY="172.30.32.2", STREAM_RECORDER_STDIN="0")
        procs.append(subprocess.Popen([sys.executable, str(APP)], env=env3))
        wait(f"http://127.0.0.1:{dir3}/login")
        check("Fremder Absender am Ingress-Port → 403", httpx.get(f"http://127.0.0.1:{ing3}/api/state").status_code == 403)
    finally:
        for p in procs:
            p.terminate()
        for p in procs:
            try:
                p.wait(10)
            except subprocess.TimeoutExpired:
                p.kill()

    print()
    print("ALLES OK" if not failures else f"{len(failures)} FEHLER: {', '.join(failures)}")
    return 0 if not failures else 1


if __name__ == "__main__":
    sys.exit(main())
