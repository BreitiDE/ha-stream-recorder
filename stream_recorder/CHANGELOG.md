# Changelog

## 2.0.2 – 2026-10-30

- **Geändert:** Die Kopfzeile mit Logo, Menü, Status, Benutzer und „Unterstützen“ bleibt
  beim Scrollen oben stehen. Auf dem Handy bricht sie um und ist etwas kompakter.
- **Geändert:** Der Name der App lautet überall „Stream Recorder“ (ohne Bindestrich), auch
  in Browser-Tab, Anmeldeseite, Anleitung und den Sensornamen in Home Assistant.
- **Geändert:** Statt „angemeldet als“ steht ein Personen-Icon vor dem Namen. Beim
  Überfahren mit der Maus erscheint „angemeldet als …“.
- **Behoben:** Auf dem Handy war das Formular auf der Seite Sender breiter als der
  Bildschirm. Das Dank-Overlay von „Unterstützen“ ragte über den Rand und wird auf dem
  Handy jetzt ausgeblendet (Tippen öffnet direkt PayPal).

## 2.0.1 – 2026-10-29

- **Geändert:** Der Knopf oben rechts in der Menüzeile heißt jetzt **„Unterstützen“**
  (Englisch „Donate“). Beim Überfahren mit der Maus (oder bei Tastatur-Fokus) erscheint ein
  kleines Overlay: „Danke für Deine Unterstützung, über eine kleine Spende würde ich mich
  freuen.“ / „Thank you for your support. I would be happy about a small donation.“
  Der Link öffnet weiterhin https://paypal.me/breitide in einem neuen Tab.
- Gleiche Gestaltung wie in Home Assistant Cleanup.

## 2.0.0 – 2026-10-29

Der Stream-Recorder ist jetzt eine **App für Home Assistant**. Die Mac-Version (1.x) wird
nicht weitergeführt. Daten der Mac-Version werden nicht übernommen, die App startet leer.

- **Neu:** Installation über den App Store von Home Assistant (Repository
  `https://github.com/BreitiDE/ha-stream-recorder`), amd64 und aarch64.
- **Neu:** Oberfläche in der Seitenleiste (Ingress) ohne eigene Anmeldung. Optional
  direkter Zugriff auf einem eigenen Port mit Benutzername und Passwort aus der
  App-Konfiguration.
- **Neu:** Feste Ordner unter dem Speicherordner (Standard `/media/stream-recorder`):
  `record/` für Aufnahmen, `title/` für Einzeltitel, `deleted/` als Papierkorb. Der
  Speicherordner wird in der App-Konfiguration gesetzt (unter `/media` oder `/share`,
  auch Netzwerkspeicher).
- **Neu:** Eigener Papierkorb statt macOS-Papierkorb: Liste mit Wiederherstellen,
  Endgültig löschen, Papierkorb leeren und optional automatisch leeren nach X Tagen.
- **Neu:** Sensoren in Home Assistant (Aufnahme läuft, nächste und letzte Aufnahme,
  geplante Aufträge, freier Speicher), Ereignisse `stream_recorder_finished` und
  `stream_recorder_error`, Befehle per `hassio.app_stdin` (Aufnahme starten/stoppen).
- **Neu:** Deutsch und Englisch für Oberfläche, Meldungen und Anleitung, automatisch
  nach der Sprache von Home Assistant oder fest einstellbar. Platzhalter in beiden
  Sprachen (`{titel}` = `{title}` …).
- **Neu:** Spendenknopf (PayPal) oben rechts.
- Log nur noch im App-Log (Seite „Log“, Datei unter `/data/log`), nicht im Log von
  Home Assistant. „Log leeren“ auf der Log-Seite.
- Aufnahmen enden genauer am geplanten Ende (vorher bis zu etwa 1 Sekunde später).
- Zeitzone von Home Assistant, Aufträge bleiben über die Zeitumstellung hinweg auf
  derselben Uhrzeit.
- **Entfernt:** `install.sh`/`uninstall.sh`, launchd, caffeinate, Zielordner-Auswahl im
  Browser, externer Einzeltitel-Ordner, eigene Login-Datei und `config.toml`.

## Mac-Version (1.x)

### 1.5.2 – 2026-10-26

- **Geändert:** Neuer Abschnitt **Optionen** ganz oben unter Einstellungen. Dort steht jetzt
  „Leer gewordene Ordner nach dem Löschen automatisch entfernen“ (bisher im Abschnitt
  Aufnahmen), mit eigenem Speichern-Knopf. Reihenfolge: Optionen, Aufnahmen,
  Einzeltitel schneiden, System.
- Anleitung angepasst (Einstellungen › Optionen, neue Bilder).

### 1.5.1 – 2026-10-26

- **Neu:** Leer gewordene Ordner werden nach „In den Papierkorb“ automatisch entfernt,
  auch mehrere Ebenen nach oben. Abschaltbar unter Einstellungen (ab 1.5.2 im Abschnitt Optionen).
- **Neu:** Knopf „Leere Ordner entfernen“ auf der Seite Aufnahmen: sucht leere Ordner im
  Zielordner und im Einzeltitel-Ordner, zeigt sie an und entfernt sie nach Bestätigung.
- Systemdateien wie `.DS_Store`, `._*`, `Icon`, `Thumbs.db` zählen dabei als leer.
  Zielordner, Einzeltitel-Ordner und versteckte Ordner bleiben immer erhalten.

### 1.5.0 – 2026-10-26

- **Neu:** Song-Cover online suchen. Radio-Streams enthalten keine Bilder, daher sucht der
  Recorder anhand von Interpret und Titel bei Deezer und danach bei Apple iTunes.
  - automatisch beim Schneiden in Einzeltitel (Einstellung „Song-Cover beim Schneiden
    online suchen“, Standard an), ohne Treffer weiter das Cover der Aufnahme
  - nachträglich für ausgewählte Einzeltitel: „Cover suchen“ in der Auswahlleiste
  - im Tag-Dialog: „Cover online suchen“ mit Vorschau vor dem Speichern
  - strenger Abgleich (lieber kein Cover als ein falsches), Zwischenspeicher, bei fehlendem
    Internet 5 Minuten Pause statt Wartezeiten
- **Neu:** Vorschaubilder der Cover in der Liste der Aufnahmen und in der Wiedergabeleiste.
- Tag-Dialog bei Einzeltiteln: Hinweis auf die Quelle statt „keine Titelliste“.

### 1.4.0 – 2026-10-25

- **Neu:** Aufnahmen in **Einzeltitel schneiden**: Schere in den Aktionen jeder Zeile
  und „Titel schneiden …“ für mehrere Aufnahmen.
  - Schnitte an den Titelwechseln der Titelliste, verlustfrei ohne Neukodierung
    (kein ffmpeg nötig), ohne Ein-/Ausblenden
  - Vorschau mit allen erkannten Titeln: erster und letzter Titel, Senderkennungen und
    zu kurze Stücke sind abgewählt, jede Zeile lässt sich an-/abwählen und anhören
  - Tags je Einzeltitel: Interpret, Titel, Album = Sender, Titelnummer, Datum/Uhrzeit,
    Genre, Cover. Doppelte Namen bekommen „(2)“, es wird nichts überschrieben
  - Die Aufnahme bleibt unverändert
- **Neu:** Einstellungen › Einzeltitel schneiden: Ordner (auch außerhalb des Zielordners),
  Muster für Unterordner/Dateinamen, Mindestlänge, Verschiebung der Schnittpunkte.
- **Neu:** Filter „Alle / Aufnahmen / Einzeltitel“ auf der Seite Aufnahmen, Kennzahlen
  getrennt nach Aufnahmen und Einzeltiteln, Status „Einzeltitel“ mit Quelle.
- Gesperrte Knöpfe in der Liste sind jetzt sichtbar ausgegraut.
- Die Schnitt-Vorschau meldet fehlende Dateien je Datei statt insgesamt abzubrechen.

### 1.3.0 – 2026-10-25

- **Neu:** Menüpunkt **Aufnahmen** (zwischen Aufträge und Sender) mit allen Audiodateien
  im Zielordner, auch fremden:
  - Suche (auch in der Titelliste), Filter nach Sender/Album und Tag-Zustand,
    Sortierung per Spaltenkopf, 10/25/50/100/alle pro Seite mit Blättern
  - Status aus „Erledigt“ je Datei, fremde Dateien als „Extern“
  - Abspielen im Browser mit Wiedergabeleiste, Kapitelmarken und Kapitelsprung
  - Tags bearbeiten (Titel, Interpret, Album, Datum, Genre, Kommentar, Cover),
    Titelliste ansehen und als .txt speichern, „Werte aus Auftrag übernehmen“
  - Nach Tags umbenennen mit Platzhaltern, Vorschau, Konfliktschutz und
    **Zurücknehmen** der letzten Umbenennung. „Erledigt“ folgt dem neuen Namen
  - „Tags aus Auftrag ergänzen“ für mehrere Dateien auf einmal
  - In den macOS-Papierkorb (wiederherstellbar mit „Zurücklegen“)
- Die Liste „Erledigt“ auf der Seite Aufträge bleibt unverändert.
- `install.sh` kopiert das neue Modul `library.py` und installiert optional
  `pyobjc-framework-Cocoa` für den Papierkorb.
- Datenbank: neue Tabelle `rename_log`, Spalte `history.job_start` (wird automatisch ergänzt).
- Anleitung um den Abschnitt „Aufnahmen verwalten“ erweitert.

### 1.2.2 – 2026-10-24

- **Behoben:** Fehlte im venv das Paket `mutagen`, brach das Schreiben der ID3-Tags nach
  jeder Aufnahme mit einem Traceback ab (`ModuleNotFoundError`). Jetzt:
  - `install.sh` prüft nach der Installation, ob alle Pakete importierbar sind, installiert
    fehlende nach und bricht sonst mit klarer Meldung ab. Am Ende zeigt es die Versionen.
  - Der Dienst meldet ein fehlendes `mutagen` beim Start im Log und als Warnbalken in der
    Oberfläche. Aufnahmen laufen weiter, unter „ID3-Tags“ steht „nicht geschrieben
    (mutagen fehlt)“.

### 1.2.1 – 2026-10-24

- **Geändert:** Die Anleitung öffnet sich im selben Tab, „← zur Oberfläche“ führt zurück.
  Die Oberfläche merkt sich dabei die zuletzt geöffnete Ansicht.
- **Behoben:** Das Ergebnis von „Stream testen“ blieb nach dem Speichern eines Senders
  stehen. Es verschwindet jetzt beim Speichern, beim Leeren, beim Bearbeiten eines
  anderen Senders und sobald die URL im Formular geändert wird. Ein noch laufender Test
  blendet sein Ergebnis danach nicht mehr ein.

### 1.2.0 – 2026-10-24

- **Neu:** Bedienungsanleitung als HTML-Seite (`web/anleitung.html`) mit Screenshots,
  im Menü unter **Anleitung** (öffnet in neuem Tab, nur nach Anmeldung). Die Datei lässt
  sich auch direkt aus dem entpackten Ordner im Browser öffnen und drucken.
- **Neu:** `tests/manual_screens.py` erzeugt die Bilder der Anleitung neu.

### 1.1.0 – 2026-10-24

- **Entfernt:** „SL-Programm laden“ (Sendeplan von sunshine live) samt Parser
  `sl_program.py`, API `/api/sl` und `/api/sl/import` sowie der Option `sl_url`.
  Grund: Die Webseite ändert sich regelmäßig, die Auswertung wäre nicht verlässlich.
- `install.sh` entfernt beim Update die Reste der Version 1.0 (`sl_program.py`,
  `sendeplan.html`). Bestehende Aufträge, Sender und Einstellungen bleiben erhalten.

### 1.0.0 – 2026-10-24

- Erste Version: Weboberfläche mit Login, Aufträge mit Vor-/Nachlauf und Wiederholung,
  Sofortaufnahme, Sendeplan von sunshine live, Senderliste mit Stream-Test,
  Zielordner-Browser, Dateinamen-Muster, Aufnahme mit ICY-Titeln, Reconnect,
  Playlist-Auflösung, ID3-Tags mit Titelliste und Kapiteln, Liste „Erledigt“ mit Status,
  Logdatei, launchd-Installation für macOS.
