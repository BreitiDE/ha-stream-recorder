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
