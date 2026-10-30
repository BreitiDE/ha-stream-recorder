"""
Language support (German / English) for server-side texts.

The source texts in the code are German. `_()` returns the English version from
`EN` when the effective language is English. Templates use {name} placeholders:
    _("{n} Dateien", n=3)

Placeholders in file name patterns exist in both languages ({titel} = {title} …),
both are always accepted, the UI shows the ones of the current language.
"""
from __future__ import annotations

LANG = "de"


def set_lang(lang: str) -> None:
    global LANG
    LANG = "de" if str(lang or "").lower().startswith("de") else "en"


def _(text: str, **kw) -> str:
    if LANG == "en":
        text = EN.get(text, text)
    return text.format(**kw) if kw else text


WEEKDAYS = {"de": ["Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag"],
            "en": ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]}


def weekday(i: int) -> str:
    return WEEKDAYS[LANG][i]


# English alias -> German placeholder (the German names are the internal ones)
ALIASES = {"station": "sender", "title": "titel", "host": "moderation", "date": "datum", "time": "zeit",
           "year": "jahr", "month": "monat", "day": "tag", "weekday": "wochentag", "artist": "interpret",
           "filename": "dateiname", "show": "sendung", "no": "nr"}
GERMAN_OF = ALIASES
ENGLISH_OF = {v: k for k, v in ALIASES.items()}


def with_aliases(vals: dict) -> dict:
    """Value dict with German keys -> additionally under the English names."""
    out = dict(vals)
    for en, de in ALIASES.items():
        if de in vals and en not in out:
            out[en] = vals[de]
    return out


def accept(names) -> set[str]:
    """Allowed placeholder names (German + English) for a list of German names."""
    names = set(names)
    return names | {ENGLISH_OF[n] for n in names if n in ENGLISH_OF}


def ui_names(names) -> list[str]:
    """Placeholder names as the UI shows them in the current language."""
    return [ENGLISH_OF.get(n, n) if LANG == "en" else n for n in names]


DEFAULT_PATTERNS = {
    "de": {"folder_pattern": "{sender}/{jahr}-{monat}", "file_pattern": "{datum}_{zeit}_{titel}",
           "cut_pattern": "{sender}/{datum} {sendung}/{nr} {interpret} - {titel}",
           "rename_pattern": "{datum}_{zeit}_{interpret} - {titel}"},
    "en": {"folder_pattern": "{station}/{year}-{month}", "file_pattern": "{date}_{time}_{title}",
           "cut_pattern": "{station}/{date} {show}/{no} {artist} - {title}",
           "rename_pattern": "{date}_{time}_{artist} - {title}"},
}


def default_pattern(key: str) -> str:
    return DEFAULT_PATTERNS[LANG][key]


def translate_html(html: str, pairs: dict[str, str]) -> str:
    """Static HTML pages (login): replace German phrases when English is active."""
    if LANG != "en":
        return html
    for de, en in sorted(pairs.items(), key=lambda kv: -len(kv[0])):
        html = html.replace(de, en)
    return html.replace('<html lang="de">', '<html lang="en">')


LOGIN_EN = {
    "Stream Recorder – Anmeldung": "Stream Recorder – Sign in",
    "Stream Recorder": "Stream Recorder",
    "Bitte anmelden": "Please sign in",
    "Benutzername": "Username",
    ">Passwort<": ">Password<",
    "Angemeldet bleiben (30 Tage)": "Stay signed in (30 days)",
    ">Anmelden<": ">Sign in<",
    "Nach 5 Fehlversuchen 5 Minuten Sperre": "5 failed attempts lock the login for 5 minutes",
}

# --------------------------------------------------------------------------
# English texts (key = German source text)
# --------------------------------------------------------------------------
EN: dict[str, str] = {
    'Album': 'Album',
    'Anfrage abgelehnt': 'Request rejected',
    'Antwort ist eine Webseite, kein Audio-Stream': 'The answer is a web page, not an audio stream',
    'App beendet': 'App stopped',
    'App unerwartet beendet': 'App stopped unexpectedly',
    'Auftrag nicht gefunden': 'Job not found',
    'Aufträge': 'jobs',
    'Benutzername oder Passwort falsch.': 'Wrong username or password.',
    'Bitte eine http(s)-URL angeben': 'Please enter an http(s) URL',
    'Cover ist größer als 5 MB': 'Cover is larger than 5 MB',
    'Cover muss JPG oder PNG sein': 'Cover must be JPG or PNG',
    'Datei nicht gefunden': 'File not found',
    'Datei nicht vorhanden': 'File does not exist',
    'Datei: {err}': 'File: {err}',
    'Dateifehler: {err}': 'File error: {err}',
    'Dateiname': 'File name',
    'Dateiname darf nicht leer sein': 'File name must not be empty',
    'Datum': 'Date',
    'Datum bitte als JJJJ, JJJJ-MM-TT oder JJJJ-MM-TT HH:MM': 'Please enter the date as YYYY, YYYY-MM-DD or YYYY-MM-DD HH:MM',
    'Dauer muss zwischen 1 Minute und 24 Stunden liegen': 'Duration must be between 1 minute and 24 hours',
    'Der Zeitraum liegt komplett in der Vergangenheit': 'The time window is completely in the past',
    'Der direkte Zugriff ist nicht eingerichtet. Benutzername und Passwort in der App-Konfiguration eintragen oder die App über die Seitenleiste von Home Assistant öffnen.': 'Direct access is not set up. Enter username and password in the app configuration or open the app from the Home Assistant sidebar.',
    'Einzeltitel': 'Single tracks',
    'Es gibt keine Umbenennung zum Zurücknehmen': 'There is no renaming to undo',
    'Fehler: {err}': 'Error: {err}',
    'Für die Suche werden Interpret und Titel gebraucht': 'Artist and title are needed for the search',
    'Genre': 'Genre',
    'HLS-Streams werden nicht unterstützt': 'HLS streams are not supported',
    'Interpret': 'Artist',
    'Kapitel': 'Chapters',
    'Keine Antwort innerhalb von 25 s': 'No answer within 25 s',
    'Keine Berechtigung: {name}': 'No permission: {name}',
    'Keine MP3-Frames gefunden': 'No MP3 frames found',
    'Keine Titelliste vorhanden': 'No track list available',
    'Log leeren fehlgeschlagen: {err}': 'Clearing the log failed: {err}',
    'Länge nicht lesbar': 'Length not readable',
    'Läuft gerade – erst abbrechen': 'Currently running – stop it first',
    'Läuft nicht': 'Not running',
    'Mindestlänge': 'Minimum length',
    'Muster ergibt keinen Namen – bleibt': 'Pattern gives no name – unchanged',
    'Muster für Einzeltitel darf nicht leer sein': 'Pattern for single tracks must not be empty',
    'Muster ist leer': 'Pattern is empty',
    'Name und eine http(s)-URL angeben': 'Enter a name and an http(s) URL',
    'Name vergeben – „({n})“ angehängt': 'Name taken – “({n})” added',
    'Pfad fehlt': 'Path missing',
    'Pfad liegt außerhalb des Speicherordners': 'Path is outside the storage folder',
    'Playlist enthält keine Stream-URL': 'Playlist contains no stream URL',
    "Python-Paket 'mutagen' fehlt – ID3-Tags werden nicht geschrieben.": "Python package 'mutagen' missing – ID3 tags are not written.",
    'Schneiden geht nur bei MP3-Dateien': 'Cutting works only for MP3 files',
    'Sender nicht gefunden': 'Station not found',
    'Senderkennung / Moderation': 'Station ID / presenter',
    'Sofortaufnahme': 'Instant recording',
    'Speicherordner {path} ist nicht erreichbar.': 'Storage folder {path} is not reachable.',
    'Speicherort „{path}“ ist ungültig (erlaubt: unter /media oder /share). Verwendet wird {default}.': 'Storage folder “{path}” is invalid (allowed: below /media or /share). Using {default}.',
    'Sprache ungültig': 'Invalid language',
    'Startzeit, Dauer oder Vor-/Nachlauf ungültig': 'Start time, duration or pre-/post-roll invalid',
    'Stream vom Server beendet': 'Stream ended by the server',
    'Stream Recorder Aufnahme läuft': 'Stream Recorder recording',
    'Stream Recorder Speicher frei': 'Stream Recorder free storage',
    'Stream Recorder geplante Aufträge': 'Stream Recorder planned jobs',
    'Stream Recorder letzte Aufnahme': 'Stream Recorder last recording',
    'Stream Recorder nächste Aufnahme': 'Stream Recorder next recording',
    'Stream-Tags übernommen': 'stream tags kept',
    'Stream-URL muss mit http:// oder https:// beginnen': 'Stream URL must start with http:// or https://',
    'Tage für den Papierkorb ungültig': 'Invalid number of days for the trash',
    'Tage für den Papierkorb: 0 bis 3650': 'Days for the trash: 0 to 3650',
    'Tags bearbeiten geht nur bei MP3-Dateien': 'Editing tags works only for MP3 files',
    'Tags nicht lesbar – bleibt': 'Tags not readable – unchanged',
    'Titel': 'Title',
    'Titel fehlt': 'Title missing',
    'Titel {nr}': 'Track {nr}',
    'Titelliste': 'Track list',
    'Titelliste ({n})': 'Track list ({n})',
    'Unbekannt': 'Unknown',
    'Unbekannter Platzhalter: {names}': 'Unknown placeholder: {names}',
    'Unbekannter Sender': 'Unknown station',
    'Unterordner': 'Subfolder',
    'Verbindung fehlgeschlagen ({err})': 'Connection failed ({err})',
    'Verschiebung': 'Offset',
    'Verschiebung oder Mindestlänge ungültig': 'Offset or minimum length invalid',
    'Verschiebung −120…120 s, Mindestlänge 0…3600 s': 'Offset −120…120 s, minimum length 0…3600 s',
    'Vor-/Nachlauf ungültig': 'Pre-/post-roll invalid',
    'Vorlauf 0–60 min, Nachlauf 0–120 min': 'Pre-roll 0–60 min, post-roll 0–120 min',
    'Wiederholung ungültig': 'Invalid repeat',
    'Zeitüberschreitung beim Verbinden ({err})': 'Timeout while connecting ({err})',
    'Zu viele Dateien auf einmal (max. 2000)': 'Too many files at once (max. 2000)',
    'Zu viele Fehlversuche. Bitte 5 Minuten warten.': 'Too many failed attempts. Please wait 5 minutes.',
    'Zu viele verschachtelte Playlists': 'Too many nested playlists',
    'abgebrochen von {user}': 'stopped by {user}',
    'ausgelassen: {names}': 'left out: {names}',
    'einmalig': 'once',
    'erster Titel (angeschnitten)': 'first track (cut off)',
    'ist schon ein Einzeltitel': 'is already a single track',
    'keine Daten empfangen': 'no data received',
    'keine Tags – bleibt': 'no tags – unchanged',
    'keine Titelliste (mindestens 3 Titel nötig)': 'no track list (at least 3 tracks needed)',
    'kürzer als {n} s': 'shorter than {n} s',
    'leer: {names}': 'empty: {names}',
    'letzter Titel (angeschnitten)': 'last track (cut off)',
    'mutagen fehlt': 'mutagen missing',
    'nicht angemeldet': 'not signed in',
    'nicht geschrieben (mutagen fehlt)': 'not written (mutagen missing)',
    'nicht mehr im Papierkorb': 'no longer in the trash',
    'ohne Titel': 'untitled',
    'ok': 'ok',
    'täglich': 'daily',
    'unverändert': 'unchanged',
    'unvollständig: {a} von {b} min': 'incomplete: {a} of {b} min',
    'verpasst – App oder Home Assistant war nicht aktiv': 'missed – app or Home Assistant was not running',
    'werktags': 'weekdays',
    'wöchentlich': 'weekly',
    '{label} muss zwischen {lo} und {hi} Sekunden liegen': '{label} must be between {lo} and {hi} seconds',
    '{label} ungültig': '{label} invalid',
    '{label}: unbekannter Platzhalter {names}': '{label}: unknown placeholder {names}',
    '{name} gibt es nicht mehr': '{name} no longer exists',
    '{name} ist schon vorhanden': '{name} already exists',
    '{name}: Interpret oder Titel fehlt': '{name}: artist or title missing',
    '{name}: außerhalb oder geschützt': '{name}: outside or protected',
    '{name}: hat schon ein Cover': '{name}: already has a cover',
    '{name}: kein Eintrag unter „Erledigt“': '{name}: no entry under “Done”',
    '{name}: nicht leer': '{name}: not empty',
    '{name}: nur MP3': '{name}: MP3 only',
    '{name}: nur für Einzeltitel': '{name}: only for single tracks',
    '{name}: schon vollständig': '{name}: already complete',
    '{nr} Titel': '{nr} Track',
    '{n} Unterbrechung(en)': '{n} interruption(s)',
}
