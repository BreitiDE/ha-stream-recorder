"""Erzeugt kleine Beispiel-MP3s mit ID3-Tags, Titelliste und Kapiteln (für Tests und Bilder).

Die Dateien enthalten nur wenige Sekunden Stille, ein Xing-Header gibt aber die gewünschte
Länge an. So zeigen Liste und Tag-Dialog realistische Werte, ohne große Dateien anzulegen.
"""
from __future__ import annotations

import struct
from pathlib import Path

FRAME = bytes([0xFF, 0xFB, 0x90, 0x00]) + bytes(413)   # MPEG-1 Layer III, 128 kbit/s, 44,1 kHz, Stereo


def png_cover(rgb: tuple[int, int, int], size: int = 160) -> bytes:
    """Einfaches Cover-Bild (Farbverlauf mit Kreis) als PNG, ohne Zusatzpakete."""
    import zlib
    r0, g0, b0 = rgb
    rows = []
    c = size / 2
    for y in range(size):
        row = bytearray([0])
        for x in range(size):
            f = 0.55 + 0.45 * (1 - (x + y) / (2 * size))
            d = ((x - c) ** 2 + (y - c) ** 2) ** 0.5
            ring = 0.75 if size * 0.18 < d < size * 0.22 else 1.0
            row += bytes(int(min(255, v * f * ring)) for v in (r0, g0, b0))
        rows.append(bytes(row))
    raw = zlib.compress(b"".join(rows), 9)

    def chunk(t, data):
        return struct.pack(">I", len(data)) + t + data + struct.pack(">I", zlib.crc32(t + data) & 0xFFFFFFFF)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", raw) + chunk(b"IEND", b""))


COVER_COLORS = [(206, 108, 0), (42, 120, 214), (27, 175, 122), (98, 80, 214), (232, 123, 164), (0, 131, 0),
                (180, 80, 40), (60, 60, 140)]


def xing_frame(seconds: float) -> bytes:
    frames = int(seconds * 44100 / 1152)
    body = bytes(32) + b"Xing" + struct.pack(">III", 0x3, frames, int(seconds * 16000))
    return (FRAME[:4] + body).ljust(len(FRAME), b"\0")


def make_mp3(path: Path, seconds: float = 60, real_seconds: float = 4, tags: dict | None = None,
             tracks: list[tuple[int, str]] | None = None, mtime: float | None = None,
             txxx: dict | None = None, cover: bytes | None = None) -> Path:
    import os
    path.parent.mkdir(parents=True, exist_ok=True)
    n = max(1, int(real_seconds * 44100 / 1152))
    path.write_bytes(xing_frame(seconds) + FRAME * n)
    if tags or tracks or txxx or cover:
        from mutagen.id3 import APIC, CHAP, COMM, CTOC, ID3, TALB, TCON, TDRC, TIT2, TPE1, TRCK, TXXX, CTOCFlags
        t = ID3()
        if cover:
            t.add(APIC(encoding=3, mime="image/png", type=3, desc="Cover", data=cover))
        for k, v in (txxx or {}).items():
            t.add(TXXX(encoding=3, desc=k, text=v))
        for key, cls in (("title", TIT2), ("artist", TPE1), ("album", TALB), ("date", TDRC), ("genre", TCON), ("track", TRCK)):
            if (tags or {}).get(key):
                t.add(cls(encoding=3, text=tags[key]))
        if tracks:
            lines = "\n".join(f"{s // 3600:02d}:{s % 3600 // 60:02d}:{s % 60:02d}  {title}" for s, title in tracks)
            t.add(COMM(encoding=3, lang="deu", desc="Titelliste", text=lines))
            ids = []
            for i, (s, title) in enumerate(tracks):
                end = tracks[i + 1][0] if i + 1 < len(tracks) else seconds
                ids.append(f"ch{i}")
                t.add(CHAP(element_id=f"ch{i}", start_time=int(s * 1000), end_time=int(end * 1000),
                           sub_frames=[TIT2(encoding=3, text=title)]))
            t.add(CTOC(element_id="toc", flags=CTOCFlags.TOP_LEVEL | CTOCFlags.ORDERED, child_element_ids=ids,
                       sub_frames=[TIT2(encoding=3, text="Titel")]))
        t.save(path, v2_version=4)
    if mtime:
        os.utime(path, (mtime, mtime))
    return path


def tracklist(seconds: int, count: int, prefix: str = "Künstler") -> list[tuple[int, str]]:
    step = seconds // count
    names = ["Sunrise", "Ocean Drive", "Midnight Glow", "Lost in Sound", "Deep Blue", "Afterhours",
             "Golden Hour", "City Lights", "Slow Motion", "Night Shift", "Echoes", "Low Tide"]
    return [(i * step + 5, f"{prefix} {chr(65 + i % 26)} - {names[i % len(names)]}") for i in range(count)]


def seed_library(con, storage: str, now: float, stream: str) -> None:
    """Beispiel-Aufnahmen unter <storage>/record, Einzeltitel unter <storage>/title,
    zwei Dateien im Papierkorb <storage>/deleted, dazu passende Einträge unter „Erledigt“."""
    from datetime import datetime, timedelta
    root = Path(storage) / "record"

    def day(d, h, m=0):
        return (datetime.now() - timedelta(days=d)).replace(hour=h, minute=m, second=0, microsecond=0)

    rows = [  # titel, sender, moderation, start, dauer s, geplant s, status, meldung, titelanzahl, tags?
        ("Deep House Session", "Beispielradio", "DJ Beispiel", day(1, 21), 7440, 7440, "ok", "1 Unterbrechung(en)", 31, "full"),
        ("Lounge am Abend", "sunshine live Lounge", "sunshine live - Lounge", day(2, 20), 3840, 3840, "ok", None, 14, "full"),
        ("Sunshine Live", "Sunshine Live", None, day(2, 16), 3840, 3840, "ok", None, 0, "none"),
        ("Chillout Sunday", "sunshine live Lounge", "sunshine live - Lounge", day(7, 10), 2712, 10980, "aborted",
         "abgebrochen von admin", 9, "full"),
        ("Feierabend-Mix", "Beispielradio", None, day(9, 18), 2490, 3720, "error", "unvollständig: 41 von 62 min", 8, "partial"),
    ]
    for title, sender, host, start, dur, planned, status, msg, ntracks, tagmode in rows:
        name = f"{start:%Y-%m-%d}_{start:%H%M}_{title}.mp3"
        p = root / sender / f"{start:%Y-%m}" / name
        tags = None
        if tagmode != "none":
            tags = {"title": title, "artist": host or "", "album": sender, "date": f"{start:%Y-%m-%dT%H:%M}", "genre": "Lounge"}
        make_mp3(p, seconds=dur, tags=tags, tracks=tracklist(dur, ntracks) if ntracks else None,
                 mtime=start.timestamp() + dur)
        ws = start.timestamp() - 60
        con.execute("INSERT INTO history(title,host,sender,url,path,window_start,window_end,started,ended,duration_s,"
                    "bytes,status,message,tags,tracks,job_start) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (title, host, sender, stream, str(p), ws, ws + planned, ws, ws + dur, dur, int(dur * 16000), status, msg,
                     "Titel, Interpret, Album, Datum, Genre" + (f", Titelliste ({ntracks}), Kapitel" if ntracks else "")
                     if tagmode != "none" else "nicht geschrieben (mutagen fehlt)", ntracks, start.timestamp()))
    # Fehler ohne Datei
    for title, d, msg in (("Deep Woods", 3, "HTTP 404"), ("Lounge am Morgen", 4, "verpasst – App oder Home Assistant war nicht aktiv")):
        ws = day(d, 7).timestamp()
        con.execute("INSERT INTO history(title,sender,url,window_start,window_end,started,ended,duration_s,bytes,status,message)"
                    " VALUES(?,?,?,?,?,?,?,0,0,'error',?)", (title, "Beispielradio", stream, ws, ws + 3600, ws, ws + 2, msg))
    # Fremde Dateien (nicht vom Recorder)
    make_mp3(root / "Archiv" / "Silvester 2025 Teil 1.mp3", seconds=10800,
             tags={"title": "Mitschnitt Silvester", "artist": "Partymix", "date": "2025-12-31T22:00"},
             mtime=datetime(2026, 1, 1, 1, 0).timestamp())
    make_mp3(root / "Archiv" / "Interview.mp3", seconds=1260, mtime=datetime(2026, 3, 14, 11, 0).timestamp())
    # Einzeltitel, die schon aus „Lounge am Abend“ geschnitten wurden
    start = day(2, 20)
    src = f"record/sunshine live Lounge/{start:%Y-%m}/{start:%Y-%m-%d}_{start:%H%M}_Lounge am Abend.mp3"
    folder = Path(storage) / "title" / "sunshine live Lounge" / f"{start:%Y-%m-%d} Lounge am Abend"
    for n, (s, title) in enumerate(tracklist(3840, 14)[1:4], 1):
        artist, song = title.split(" - ", 1)
        make_mp3(folder / f"{n:02d} {artist} - {song}.mp3", seconds=270 - n * 15,
                 tags={"title": song, "artist": artist, "album": "sunshine live Lounge",
                       "date": f"{start + timedelta(seconds=s):%Y-%m-%dT%H:%M}", "genre": "Lounge", "track": f"{n}/12"},
                 txxx={"stream-recorder": "Einzeltitel", "Quelle": f"{src} @ 0:{s // 60:02d}:{s % 60:02d}"},
                 cover=png_cover(COVER_COLORS[n]) if n != 3 else None,
                 mtime=datetime.now().timestamp() - 3600 + n)
    # Papierkorb: zwei gelöschte Dateien (Datum = Änderungszeit)
    trash = Path(storage) / "deleted"
    make_mp3(trash / "record" / "Archiv" / "Probeaufnahme.mp3", seconds=840,
             tags={"title": "Probeaufnahme", "artist": "Beispielradio"}, mtime=datetime.now().timestamp() - 2 * 86400)
    make_mp3(trash / "title" / "sunshine live Lounge" / "05 Künstler E - Deep Blue.mp3", seconds=240,
             tags={"title": "Deep Blue", "artist": "Künstler E"}, mtime=datetime.now().timestamp() - 5 * 3600)
