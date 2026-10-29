# Stream Recorder – Entwicklung und Übergabe

Aufbau, Entscheidungen, Tests und offene Punkte der Home-Assistant-App. Was die App kann,
steht in `README.md`, die Bedienung in der Anleitung (`stream_recorder/app/web/manual-de.html`).

> **Weitermachen mit Claude:** ZIP in einen neuen Chat hochladen (oder im Projekt „AI / KI“
> unter `ha-stream-recorder/`) und schreiben: *„Lies ENTWICKLUNG.md und mach mit dem
> Stream Recorder weiter: …“*

## 1. Stand

- Version 2.0.0, Home-Assistant-App (früher „Add-on“) für amd64 und aarch64,
  Repository `BreitiDE/ha-stream-recorder`, Slug `stream_recorder`.
- Hervorgegangen aus der Mac-Version 1.5.2 (Python-Dienst mit launchd). Die Mac-Version
  wird nicht weitergeführt, Daten werden nicht übernommen (Wunsch: Neustart).
- Getestet mit nachgebauten Streams und einem nachgebauten Supervisor
  (`tests/fake_stream.py`). **Nicht** auf einem echten Home Assistant und nicht gegen die
  echten Stream-Server, Deezer oder iTunes getestet (kein Zugang aus der Entwicklungsumgebung).

## 2. Struktur

```
ha-stream-recorder/
├── repository.yaml            App-Repository für den App Store
├── README.md, LICENSE (MIT), ENTWICKLUNG.md
├── docs/*.png                 Bilder fürs README (aus der englischen Anleitung)
├── .github/workflows/test.yml Smoke-Test bei jedem Push
├── stream_recorder/           die App
│   ├── config.yaml            Ingress 8099, Port 8765 optional, map media/share, stdin, homeassistant_api
│   ├── Dockerfile             ghcr.io/home-assistant/base + Python aus Alpine + pip (ohne build.yaml)
│   ├── run.sh                 startet nur Python (kein bashio-Aufruf)
│   ├── requirements.txt       starlette, uvicorn, httpx, mutagen
│   ├── translations/de|en.yaml  Texte der App-Konfiguration
│   ├── README.md, DOCS.md, CHANGELOG.md, icon.png, logo.png
│   └── app/
│       ├── stream_recorder.py Server: Konfig, Zugriff, DB, Scheduler, Recorder, ID3, HA, API
│       ├── library.py         Seite Aufnahmen: Einlesen, Tags, Umbenennen, Papierkorb, leere Ordner
│       ├── cutter.py          Einzeltitel verlustfrei schneiden
│       ├── covers.py          Cover-Suche Deezer/iTunes
│       ├── ha.py              Supervisor-API (Sprache, Zustände, Ereignisse), stdin-Leser
│       ├── i18n.py            Server-Texte Deutsch/Englisch, Platzhalter-Aliasse
│       └── web/
│           ├── index.html     Oberfläche (eine Datei, Vanilla JS, Farben nach FARBDESIGN.md)
│           ├── lang-en.js     englische Texte der Oberfläche (Schlüssel = deutscher Text)
│           ├── login.html     Anmeldung für den direkten Zugriff
│           ├── manual-de.html, manual-en.html, manual/{de,en}/*.png   Anleitung
└── tests/
    ├── fake_stream.py         Icecast-Nachbau, Cover-Suche, Supervisor (/core/api/…, /_ha)
    ├── smoke_test.py          Ende-zu-Ende-Test, ~135 Prüfungen, ~60 s, Exit 0 = ok
    ├── dev.sh                 lokale Umgebung mit Demo-Daten
    ├── seed_demo_data.py, demo_files.py
    └── manual_screens.py      Bilder der Anleitung (DE + EN)
```

## 3. Entscheidungen und Fallstricke

- **Zwei Ports, ein Server:** uvicorn bekommt zwei Sockets (8099 Ingress, 8765 direkt).
  `is_ingress()` erkennt den Weg am lokalen Port. Auf dem Ingress-Port dürfen nur
  `trusted_proxies` (Supervisor `172.30.32.2`) verbinden, der Benutzername kommt aus
  `X-Remote-User-Display-Name`. Auf dem direkten Port gilt das Login (HMAC-Cookie). Der
  Port ist in `config.yaml` standardmäßig aus (`8765/tcp: null`).
- **Login-Daten aus der App-Konfiguration:** `options.json` (username/password). In die
  Sitzung geht ein Hash von Benutzer+Passwort (`pw_version`), damit eine Änderung alle
  Sitzungen beendet. Beide leer = direkter Zugriff aus.
- **Ingress-Pfade:** Die Oberfläche nutzt nur relative Adressen (`api/…`, `manual`,
  `lang-en.js`). Der Smoke-Test prüft, dass kein `href="/` o. Ä. vorkommt.
- **CSRF:** Pflicht-Header `X-Recorder: 1` bei allen ändernden Aufrufen.
- **Feste Ordner:** `record/`, `title/`, `deleted/` unter `storage_path`. Pfade in der API
  beginnen mit `record/` bzw. `title/` (`split_rel()`), `resolve_in()` verhindert Ausbrüche.
  Die Hauptordner stehen in `lib_protected()` und werden nie entfernt.
- **Papierkorb:** `library.trash_move()` verschiebt nach `deleted/<record|title>/<Pfad>`,
  Tabelle `trash` merkt Herkunft und Löschzeit. `trash_sync()` gleicht Tabelle und Ordner
  ab (auch von Hand hineingelegte Dateien, beim Start, bei „Neu einlesen“ und beim Öffnen).
  Automatisch leeren läuft stündlich im Scheduler und sofort, wenn sich `trash_days` ändert.
- **Home Assistant:** Entitäten per REST `POST /core/api/states/<id>` über den Supervisor
  (`homeassistant_api: true`, `SUPERVISOR_TOKEN`). Nur Änderungen werden gesendet, alle
  10 Minuten alles neu (nach HA-Neustart). Folge: keine `unique_id`, nicht in der
  Oberfläche umbenennbar. Eine echte Integration (Custom Component) wäre der Ausbauschritt.
- **Befehle:** `hassio.app_stdin` schreibt das `input` als JSON auf stdin, nicht unbedingt mit
  Zeilenende. `ha.StdinReader` dekodiert deshalb Wert für Wert mit `raw_decode`.
  Der Slug im App Store ist `<sha1(repo-url)[:8]>_stream_recorder` = `f91e2bd5_stream_recorder`.
- **Log:** nur `RotatingFileHandler` nach `/data/log/stream-recorder.log`, uvicorn mit
  `log_config=None`. Auf stdout/stderr kommt nichts (Wunsch: nichts ins HA-Log). Der
  Smoke-Test prüft das.
- **Sprache:** Quelltexte deutsch. Server: `i18n._()` mit `EN`-Wörterbuch. Oberfläche:
  `t()` + `translateDom()` mit `lang-en.js`. Gleiches deutsches Wort mit verschiedener
  Übersetzung: Attribut `data-en`. „auto“ nimmt die Sprache von HA (`GET /core/api/config`,
  stündlich). Platzhalter gibt es in beiden Sprachen (`i18n.ALIASES`).
- **Startskript:** `init: false`, `#!/usr/bin/with-contenv bashio`, aber kein bashio-Aufruf
  (bei My TrackView gab `bashio::addon.version` eine Warnung).
- **Dockerfile ohne build.yaml:** neues HA-Build-Verfahren (seit 2026), Basis
  `ghcr.io/home-assistant/base:latest` (Multi-Arch). Python-Pakete per pip, alle rein in Python.
- Übernommen aus 1.x: kein ffmpeg, verlustfreies Schneiden, strenge Cover-Suche, Reconnect,
  `jobs.last_window` gegen doppeltes „verpasst“, Wiederholungen in lokaler Zeit.

## 4. Entwickeln und Testen

```bash
pip install -r stream_recorder/requirements.txt
python3 tests/smoke_test.py          # → ALLES OK
tests/dev.sh --fresh                 # Seitenleiste: http://127.0.0.1:8099/, direkt: :8765 (admin / devpasswort)
SR_LANG=en tests/dev.sh              # Home Assistant auf Englisch
pip install playwright && playwright install chromium
python3 tests/manual_screens.py      # Bilder der Anleitung neu (nur in Wegwerf-Umgebung: nutzt /media, /data)
```

Neue Texte in der Oberfläche: in `lang-en.js` ergänzen, neue Server-Texte in `i18n.EN`.

Release: Version in `config.yaml`, `Dockerfile` (`BUILD_VERSION`) und `stream_recorder.py`
(`__version__`) erhöhen, `CHANGELOG.md` ergänzen, auf GitHub pushen. Home Assistant zeigt
das Update an und baut die App auf dem Gerät.

## 5. Ideen und offene Punkte

- Echte Integration (Gerät mit Entitäten, umbenennbar, Dienste statt `hassio.app_stdin`).
- Benachrichtigung direkt aus der App (heute über das Ereignis per Automation).
- Cover je Sender hinterlegen und bei jeder Aufnahme einbetten.
- Herunterladen mehrerer Dateien als ZIP.
- `.cue`-Datei zusätzlich zu den Kapiteln.
- Fertige Images in einer Registry (heute baut Home Assistant die App auf dem Gerät).
