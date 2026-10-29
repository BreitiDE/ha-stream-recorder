"""
Aufnahmen in Einzeltitel schneiden – verlustfrei an MP3-Frame-Grenzen, ohne Neukodierung.

Die Schnittpunkte kommen aus den Kapiteln bzw. der Titelliste, die der Recorder während der
Aufnahme aus den Titel-Infos (ICY) des Streams schreibt. Erster und letzter Titel werden
verworfen (meist angeschnitten), ebenso zu kurze Stücke und reine Senderkennungen.
"""
from __future__ import annotations

import bisect
import mmap
import re
from datetime import timedelta
from pathlib import Path

import i18n
import library as lib
from i18n import _

CUT_MARK = "Einzeltitel"          # TXXX:stream-recorder in geschnittenen Dateien
CUT_PLACEHOLDERS = ("sender", "sendung", "datum", "zeit", "jahr", "monat", "tag", "nr", "interpret", "titel")

# --------------------------------------------------------------------------
# MP3-Frames
# --------------------------------------------------------------------------
_BR = {
    (3, 3): [0, 32, 64, 96, 128, 160, 192, 224, 256, 288, 320, 352, 384, 416, 448],   # MPEG1 Layer I
    (3, 2): [0, 32, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320, 384],      # MPEG1 Layer II
    (3, 1): [0, 32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320],       # MPEG1 Layer III
    (2, 3): [0, 32, 48, 56, 64, 80, 96, 112, 128, 144, 160, 176, 192, 224, 256],      # MPEG2/2.5 Layer I
    (2, 2): [0, 8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 144, 160],           # MPEG2/2.5 Layer II/III
}
_SR = {3: [44100, 48000, 32000], 2: [22050, 24000, 16000], 0: [11025, 12000, 8000]}


def _header(b: bytes | mmap.mmap, i: int):
    """Liefert (Frame-Länge, Samples, Samplerate, Kennung) oder None."""
    if b[i] != 0xFF or (b[i + 1] & 0xE0) != 0xE0:
        return None
    ver = (b[i + 1] >> 3) & 3
    layer = (b[i + 1] >> 1) & 3
    bri = b[i + 2] >> 4
    sri = (b[i + 2] >> 2) & 3
    pad = (b[i + 2] >> 1) & 1
    if ver == 1 or layer == 0 or bri in (0, 15) or sri == 3:
        return None
    table = _BR[(3, layer)] if ver == 3 else _BR[(2, 3 if layer == 3 else 2)]
    br = table[bri] * 1000
    sr = _SR[ver][sri]
    if layer == 3:                 # Layer I
        size, samples = (12 * br // sr + pad) * 4, 384
    elif layer == 2:               # Layer II
        size, samples = 144 * br // sr + pad, 1152
    else:                          # Layer III
        size, samples = (144 if ver == 3 else 72) * br // sr + pad, (1152 if ver == 3 else 576)
    return size, samples, sr, (ver, layer, sr)


def _id3v2_size(b) -> int:
    if len(b) >= 10 and b[:3] == b"ID3":
        s = (b[6] << 21) | (b[7] << 14) | (b[8] << 7) | b[9]
        return 10 + s + (10 if b[5] & 0x10 else 0)
    return 0


def scan_frames(b) -> tuple[list[int], list[int], list[float]]:
    """Offsets, Längen und Startzeiten aller Audio-Frames. Überspringt ID3, Xing/Info-Frames
    und Datenmüll (z. B. an Reconnect-Stellen)."""
    n = len(b)
    end = n
    if n >= 128 and b[n - 128:n - 125] == b"TAG":
        end = n - 128
    i = _id3v2_size(b)
    offs, sizes, times = [], [], []
    t, ident = 0.0, None
    while i + 4 <= end:
        h = _header(b, i)
        if h and i + h[0] <= end:
            size, samples, sr, idn = h
            nxt = i + size
            ok = nxt + 4 > end or _header(b, nxt) is not None
            if ok and (ident is None or idn == ident):
                if not offs and (b"Xing" in b[i:i + 48] or b"Info" in b[i:i + 48] or b[i + 36:i + 40] == b"VBRI"):
                    i = nxt                # VBR-Kopf, kein Audio
                    continue
                ident = idn
                offs.append(i)
                sizes.append(size)
                times.append(t)
                t += samples / sr
                i = nxt
                continue
        i += 1                             # neu synchronisieren
    times.append(t)                        # Ende als letzter Zeitpunkt
    return offs, sizes, times


# --------------------------------------------------------------------------
# Planung
# --------------------------------------------------------------------------
def alnum(s: str) -> str:
    return re.sub(r"[^0-9a-zäöüß]", "", (s or "").lower())


def split_title(s: str) -> tuple[str, str]:
    if " - " in s:
        a, t = s.split(" - ", 1)
        return a.strip(), t.strip()
    return "", s.strip()


def is_station(title: str, names: list[str]) -> bool:
    if " - " not in title:
        return True                       # keine „Künstler - Titel“-Form: Moderation, Jingle
    whole, left = alnum(title), alnum(split_title(title)[0])
    for n in names:
        k = alnum(n)
        if len(k) >= 4 and (whole == k or left == k or whole.startswith(k) or k.startswith(whole)):
            return True
    return False


def plan(info: dict, duration: float, sender: str, offset: float, min_len: float) -> list[dict]:
    """Segmente aus den Kapiteln. Alle Segmente kommen zurück, mit selected/reason."""
    chapters = [c for c in (info.get("chapter_list") or []) if c.get("title")]
    out = []
    names = [n for n in (sender, info.get("album"), info.get("artist")) if n]
    for k, c in enumerate(chapters):
        start = max(0.0, c["start"] + offset)
        end = chapters[k + 1]["start"] + offset if k + 1 < len(chapters) else duration
        end = max(start, min(end, duration))
        artist, title = split_title(c["title"])
        reason = ""
        if k == 0:
            reason = _("erster Titel (angeschnitten)")
        elif k == len(chapters) - 1:
            reason = _("letzter Titel (angeschnitten)")
        elif is_station(c["title"], names):
            reason = _("Senderkennung / Moderation")
        elif end - start < min_len:
            reason = _("kürzer als {n} s", n=int(min_len))
        out.append({"i": k, "start": round(start, 2), "end": round(end, 2), "dur": round(end - start, 1),
                    "raw": c["title"], "artist": artist, "title": title, "selected": not reason, "reason": reason})
    return out


class _Fmt(dict):
    def __missing__(self, key):
        return "{" + key + "}"


def target_for(cut_root: Path, pattern: str, vals: dict, used: set[str]) -> Path:
    unknown = [p for p in re.findall(r"\{(\w+)\}", pattern) if p not in i18n.accept(CUT_PLACEHOLDERS)]
    if unknown:
        raise lib.LibraryError(_("Unbekannter Platzhalter: {names}", names=", ".join("{" + u + "}" for u in unknown)))
    vals = i18n.with_aliases(vals)
    rendered = pattern.format_map(_Fmt({k: lib.safe_name(str(v)) for k, v in vals.items()}))
    parts = [lib.safe_name(p) for p in rendered.split("/")]
    parts = [re.sub(r"^[\s_\-–.]+|[\s_\-–]+$", "", p) for p in parts]
    parts = [p for p in parts if p]
    if not parts:
        parts = [_("{nr} Titel", nr=vals.get("nr", "00"))]
    folder = cut_root.joinpath(*parts[:-1])
    stem = parts[-1][:180]
    p = folder / f"{stem}.mp3"
    n = 2
    while p.exists() or str(p).lower() in used:     # nicht überspringen: zweite Kopie bekommt (2)
        p = folder / f"{stem} ({n}).mp3"
        n += 1
    used.add(str(p).lower())
    return p


def source_values(info: dict, sender: str, title_fallback: str) -> dict:
    d = lib.parse_date(info.get("date"))
    raw = info.get("date") or ""
    has_time = bool(re.search(r"\d{1,2}:\d{2}", raw))
    return {"sender": sender or _("Unbekannter Sender"), "sendung": info.get("title") or title_fallback,
            "datum": d.strftime("%Y-%m-%d") if d else "", "zeit": d.strftime("%H%M") if d and has_time else "",
            "jahr": d.strftime("%Y") if d else "", "monat": d.strftime("%m") if d else "",
            "tag": d.strftime("%d") if d else "", "_dt": d if d and has_time else None}


# --------------------------------------------------------------------------
# Schneiden
# --------------------------------------------------------------------------
def cut(src: Path, info: dict, segments: list[dict], sender: str, cut_root: Path, pattern: str,
        rel_source: str, find_cover=None, stats: dict | None = None) -> list[Path]:
    """Schreibt die ausgewählten Segmente als eigene MP3-Dateien mit Tags. Liefert die Pfade.
    find_cover(interpret, titel) -> {"data", "mime", "source"} | None sucht ein Song-Cover;
    ohne Treffer bekommt der Titel das Cover der Aufnahme (falls vorhanden).
    stats zählt "online" (Song-Cover gefunden) und "fallback" (Cover der Aufnahme)."""
    if src.suffix.lower() != ".mp3":
        raise lib.LibraryError(_("Schneiden geht nur bei MP3-Dateien"))
    chosen = [s for s in segments if s.get("selected")]
    if not chosen:
        return []
    from mutagen.id3 import APIC, ID3, TALB, TCON, TDRC, TIT2, TPE1, TRCK, TXXX, ID3NoHeaderError
    try:
        cover = [f for f in ID3(src).getall("APIC")]
    except ID3NoHeaderError:
        cover = []
    vals = source_values(info, sender, src.stem)
    used: set[str] = set()
    created: list[Path] = []
    with open(src, "rb") as fh, mmap.mmap(fh.fileno(), 0, access=mmap.ACCESS_READ) as b:
        offs, sizes, times = scan_frames(b)
        if not offs:
            raise lib.LibraryError(_("Keine MP3-Frames gefunden"))
        total = len(chosen)
        for n, seg in enumerate(chosen, 1):
            a = bisect.bisect_left(times, seg["start"], 0, len(offs))
            z = bisect.bisect_left(times, seg["end"], 0, len(offs))
            if z <= a:
                continue
            nr = f"{n:02d}"
            artist, title = seg.get("artist") or "", seg.get("title") or seg.get("raw") or _("Titel {nr}", nr=nr)
            dest = target_for(cut_root, pattern, dict(vals, nr=nr, interpret=artist or _("Unbekannt"), titel=title), used)
            dest.parent.mkdir(parents=True, exist_ok=True)
            part = dest.with_name(dest.name + ".part")
            with open(part, "wb") as out:
                # zusammenhängend kopieren; Müll zwischen Frames (Reconnect) wird ausgelassen
                _copy_frames(b, offs, sizes, a, z, out)
            t = ID3()
            t.add(TIT2(encoding=3, text=title))
            if artist:
                t.add(TPE1(encoding=3, text=artist))
            t.add(TALB(encoding=3, text=vals["sender"]))
            t.add(TRCK(encoding=3, text=f"{n}/{total}"))
            if vals["_dt"]:
                t.add(TDRC(encoding=3, text=(vals["_dt"] + timedelta(seconds=int(seg["start"]))).strftime("%Y-%m-%dT%H:%M")))
            elif vals["datum"]:
                t.add(TDRC(encoding=3, text=vals["datum"]))
            if info.get("genre"):
                t.add(TCON(encoding=3, text=info["genre"]))
            found = None
            if find_cover and artist:
                try:
                    found = find_cover(artist, title)
                except Exception:          # Cover-Suche darf das Schneiden nie verhindern
                    found = None
            if found:
                t.add(APIC(encoding=3, mime=found["mime"], type=3, desc="Cover", data=found["data"]))
                t.add(TXXX(encoding=3, desc="Cover-Quelle", text=found.get("source", "online")))
                if stats is not None:
                    stats["online"] = stats.get("online", 0) + 1
            else:
                for c in cover[:1]:
                    t.add(APIC(encoding=3, mime=c.mime, type=c.type, desc=c.desc, data=c.data))
                    if stats is not None:
                        stats["fallback"] = stats.get("fallback", 0) + 1
            t.add(TXXX(encoding=3, desc="stream-recorder", text=CUT_MARK))
            t.add(TXXX(encoding=3, desc="Quelle", text=f"{rel_source} @ {lib_fmt(seg['start'])}"))
            t.save(part, v2_version=4)
            part.rename(dest)
            created.append(dest)
    return created


def _copy_frames(b, offs, sizes, a, z, out) -> None:
    run_start = offs[a]
    run_end = offs[a] + sizes[a]
    for k in range(a + 1, z):
        if offs[k] == run_end:
            run_end += sizes[k]
        else:
            out.write(b[run_start:run_end])
            run_start, run_end = offs[k], offs[k] + sizes[k]
    out.write(b[run_start:run_end])


def lib_fmt(s: float) -> str:
    s = int(s)
    return f"{s // 3600}:{s % 3600 // 60:02d}:{s % 60:02d}"
