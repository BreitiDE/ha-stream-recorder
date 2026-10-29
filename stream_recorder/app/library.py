"""
Aufnahmen und Einzeltitel: einlesen, Tags lesen/schreiben, nach Tags umbenennen, Papierkorb.

Wird von stream_recorder.py benutzt. Alle Pfade, die von außen kommen, sind relativ zu einem
der Hauptordner (record/, title/) und werden mit `resolve()` geprüft, damit nichts außerhalb
angefasst wird.
"""
from __future__ import annotations

import base64
import os
import re
import shutil
import threading
import time
from datetime import datetime
from pathlib import Path

import i18n
from i18n import _

AUDIO_EXT = {".mp3", ".aac", ".m4a", ".ogg", ".opus", ".flac", ".wav"}
TAG_FIELDS = ("title", "artist", "album", "date")      # Pflichtfelder für "Tags vollständig"
TAG_LABELS = {"title": "Titel", "artist": "Interpret", "album": "Album", "date": "Datum"}
TRACKLIST_DESC = "Titelliste"
PLACEHOLDERS = ("titel", "interpret", "album", "genre", "datum", "zeit", "jahr", "monat", "tag",
                "wochentag", "dateiname")
MIME = {".mp3": "audio/mpeg", ".aac": "audio/aac", ".m4a": "audio/mp4", ".ogg": "audio/ogg",
        ".opus": "audio/ogg", ".flac": "audio/flac", ".wav": "audio/wav"}


class LibraryError(Exception):
    pass


# --------------------------------------------------------------------------
# Pfade
# --------------------------------------------------------------------------
def resolve_in(root: Path, rel: str) -> Path:
    """Relativen Pfad aus der Oberfläche sicher in einen absoluten Pfad im Zielordner wandeln."""
    if not rel or "\x00" in rel:
        raise LibraryError(_("Pfad fehlt"))
    root = root.resolve()
    p = (root / rel).resolve()
    if p != root and root not in p.parents:
        raise LibraryError(_("Pfad liegt außerhalb des Speicherordners"))
    return p


def safe_name(s: str, limit: int = 200) -> str:
    s = re.sub(r'[\x00-\x1f/\\:*?"<>|]+', "-", str(s or ""))
    s = re.sub(r"\s+", " ", s).strip(" .")
    return s[:limit].strip()


# --------------------------------------------------------------------------
# Tags lesen
# --------------------------------------------------------------------------
def mutagen_ok() -> bool:
    try:
        import mutagen  # noqa: F401
        return True
    except ImportError:
        return False


TRACK_RE = re.compile(r"^\s*(\d+):(\d{2}):(\d{2})\s+(.+?)\s*$")


def parse_tracklist(text: str) -> list[dict]:
    out = []
    for line in (text or "").splitlines():
        m = TRACK_RE.match(line)
        if m:
            h, mi, s, t = m.groups()
            out.append({"start": int(h) * 3600 + int(mi) * 60 + int(s), "title": t})
    return out


def parse_date(s: str | None) -> datetime | None:
    s = (s or "").strip()
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", ""))
    except ValueError:
        pass
    m = re.match(r"^(\d{4})(?:-(\d{2}))?", s)
    if m:
        try:
            return datetime(int(m.group(1)), int(m.group(2) or 1), 1)
        except ValueError:
            return None
    return None


def read_info(path: Path, detail: bool = False) -> dict:
    """Datei-Infos und Tags. detail=True liefert zusätzlich Titelliste, Kapitel und Kommentar."""
    st = path.stat()
    info = {"size": st.st_size, "mtime": st.st_mtime, "ext": path.suffix.lower(), "duration": None,
            "bitrate": None, "title": "", "artist": "", "album": "", "date": "", "genre": "",
            "comment": "", "tracks": 0, "chapters": 0, "has_cover": False, "tracklist_text": "",
            "editable": path.suffix.lower() == ".mp3", "read_error": None, "cut_mark": "", "source": ""}
    if not mutagen_ok():
        info["read_error"] = "mutagen"
        return _finish_info(info, detail, [], [])
    tracks, chapters = [], []
    try:
        if info["ext"] == ".mp3":
            from mutagen.id3 import ID3, ID3NoHeaderError
            from mutagen.mp3 import MP3
            try:
                a = MP3(path)
                info["duration"] = float(a.info.length or 0) or None
                info["bitrate"] = int((a.info.bitrate or 0) / 1000) or None
            except Exception as e:
                info["read_error"] = f"Audio: {type(e).__name__}"
            try:
                t = ID3(path)
            except ID3NoHeaderError:
                t = None
            if t is not None:
                def txt(fid):
                    f = t.get(fid)
                    return str(f.text[0]).strip() if f and f.text else ""
                info.update(title=txt("TIT2"), artist=txt("TPE1"), album=txt("TALB"),
                            date=txt("TDRC"), genre=txt("TCON"))
                for c in t.getall("COMM"):
                    text = "\n".join(str(x) for x in c.text)
                    if c.desc == TRACKLIST_DESC:
                        info["tracklist_text"] = text
                        tracks = parse_tracklist(text)
                    elif not info["comment"]:
                        info["comment"] = text
                for ch in t.getall("CHAP"):
                    sub = ch.sub_frames.get("TIT2")
                    chapters.append({"start": round(ch.start_time / 1000, 1),
                                     "title": str(sub.text[0]) if sub and sub.text else ""})
                chapters.sort(key=lambda c: c["start"])
                info["has_cover"] = bool(t.getall("APIC"))
                for x in t.getall("TXXX"):
                    if x.desc == "stream-recorder" and x.text:
                        info["cut_mark"] = str(x.text[0])
                    elif x.desc == "Quelle" and x.text:
                        info["source"] = str(x.text[0])
        else:
            import mutagen
            f = mutagen.File(path, easy=True)
            if f is not None:
                if f.info and getattr(f.info, "length", None):
                    info["duration"] = float(f.info.length)
                br = getattr(f.info, "bitrate", None) if f.info else None
                info["bitrate"] = int(br / 1000) if br else None
                tags = f.tags or {}
                for k in ("title", "artist", "album", "date", "genre"):
                    v = tags.get(k) if hasattr(tags, "get") else None
                    info[k] = str(v[0]).strip() if v else ""
    except Exception as e:
        info["read_error"] = f"{type(e).__name__}: {e}"[:120]
    return _finish_info(info, detail, tracks, chapters)


def _finish_info(info: dict, detail: bool, tracks: list, chapters: list) -> dict:
    info["tracks"] = len(tracks)
    info["chapters"] = len(chapters)
    missing = [key for key in TAG_FIELDS if not info[key]]
    if info["read_error"] == "mutagen":
        info["tag_state"], info["missing"] = "unknown", []
    elif len(missing) == 0:
        info["tag_state"], info["missing"] = "full", []
    elif len(missing) == len(TAG_FIELDS):
        info["tag_state"], info["missing"] = "none", missing
    else:
        info["tag_state"], info["missing"] = "partial", missing
    d = parse_date(info["date"])
    info["when"] = d.timestamp() if d else info["mtime"]
    if detail:
        info["tracklist"] = tracks
        info["chapter_list"] = chapters or tracks
    else:
        info.pop("tracklist_text", None)
    return info


# --------------------------------------------------------------------------
# Einlesen mit Zwischenspeicher
# --------------------------------------------------------------------------
class Library:
    def __init__(self):
        self.lock = threading.Lock()
        self.cache: dict[str, tuple[float, int, dict]] = {}
        self.root: Path | None = None
        self.scanned_at = 0.0
        self.items: list[dict] = []
        self.search_text: dict[str, str] = {}

    def scan(self, root: Path | list[tuple[str, Path]], force: bool = False, max_age: float = 30) -> list[dict]:
        """root: Zielordner oder Liste (Präfix, Ordner). Dateien aus einem Ordner mit Präfix
        bekommen den Pfad "<Präfix>/<relativ>" (z. B. externer Ordner für Einzeltitel)."""
        roots = [("", root)] if isinstance(root, Path) else list(root)
        roots = [(pre, r.expanduser()) for pre, r in roots]
        key = tuple((pre, str(r)) for pre, r in roots)
        with self.lock:
            if not force and self.root == key and time.time() - self.scanned_at < max_age:
                return self.items
            if self.root != key:
                self.cache.clear()
            items, seen = [], set()
            for pre, base in roots:
                if not base.is_dir():
                    continue
                for dirpath, dirnames, filenames in os.walk(base):
                    dirnames[:] = sorted(d for d in dirnames if not d.startswith("."))
                    # ein Unterordner, der selbst als eigene Wurzel eingelesen wird, nicht doppelt
                    dirnames[:] = [d for d in dirnames if not any(
                        p2 and (Path(dirpath) / d) == r2 for p2, r2 in roots)]
                    for fn in filenames:
                        if fn.startswith(".") or Path(fn).suffix.lower() not in AUDIO_EXT:
                            continue
                        p = Path(dirpath) / fn
                        try:
                            st = p.stat()
                        except OSError:
                            continue
                        inner = str(p.relative_to(base))
                        rel = f"{pre}/{inner}" if pre else inner
                        seen.add(rel)
                        c = self.cache.get(rel)
                        if c and c[0] == st.st_mtime and c[1] == st.st_size:
                            info = c[2]
                        else:
                            try:
                                info = read_info(p, detail=True)
                            except OSError:
                                continue
                            self.cache[rel] = (st.st_mtime, st.st_size, info)
                        folder = str(Path(rel).parent)
                        items.append(dict(info, path=rel, name=fn, folder="" if folder == "." else folder.lstrip("@"),
                                          abs=str(p)))
            for k in list(self.cache):
                if k not in seen:
                    del self.cache[k]
            self.root, self.items, self.scanned_at = key, items, time.time()
            self.search_text = {
                it["path"]: " ".join([it["title"], it["artist"], it["album"], it["genre"], it["path"],
                                      it.get("tracklist_text", "")]).lower() for it in items}
            return items

    def invalidate(self):
        with self.lock:
            self.scanned_at = 0.0


# --------------------------------------------------------------------------
# Tags schreiben
# --------------------------------------------------------------------------
def write_tags(path: Path, fields: dict, cover: str | None = None) -> None:
    """fields: title, artist, album, date, genre, comment – None = unverändert, "" = löschen.
    cover: data:-URL zum Setzen, "remove" zum Entfernen, None = unverändert."""
    if path.suffix.lower() != ".mp3":
        raise LibraryError(_("Tags bearbeiten geht nur bei MP3-Dateien"))
    from mutagen.id3 import APIC, COMM, ID3, TALB, TCON, TDRC, TIT2, TPE1, ID3NoHeaderError
    try:
        t = ID3(path)
    except ID3NoHeaderError:
        t = ID3()
    frames = {"title": ("TIT2", TIT2), "artist": ("TPE1", TPE1), "album": ("TALB", TALB),
              "date": ("TDRC", TDRC), "genre": ("TCON", TCON)}
    for key, (fid, cls) in frames.items():
        v = fields.get(key)
        if v is None:
            continue
        v = str(v).strip()
        if v:
            t.setall(fid, [cls(encoding=3, text=v)])
        else:
            t.delall(fid)
    if fields.get("comment") is not None:
        keep = [c for c in t.getall("COMM") if c.desc == TRACKLIST_DESC]
        t.delall("COMM")
        for c in keep:
            t.add(c)
        v = str(fields["comment"]).strip()
        if v:
            t.add(COMM(encoding=3, lang="deu", desc="", text=v))
    if cover == "remove":
        t.delall("APIC")
    elif cover:
        m = re.match(r"^data:(image/(?:jpeg|png));base64,(.+)$", cover, re.S)
        if not m:
            raise LibraryError(_("Cover muss JPG oder PNG sein"))
        data = base64.b64decode(m.group(2))
        if len(data) > 5_000_000:
            raise LibraryError(_("Cover ist größer als 5 MB"))
        t.delall("APIC")
        t.add(APIC(encoding=3, mime=m.group(1), type=3, desc="Cover", data=data))
    t.save(path, v2_version=4)


def read_cover(path: Path) -> tuple[bytes, str] | None:
    if path.suffix.lower() != ".mp3" or not mutagen_ok():
        return None
    from mutagen.id3 import ID3, ID3NoHeaderError
    try:
        pics = ID3(path).getall("APIC")
    except ID3NoHeaderError:
        return None
    return (pics[0].data, pics[0].mime or "image/jpeg") if pics else None


# --------------------------------------------------------------------------
# Umbenennen
# --------------------------------------------------------------------------
PH_RE = re.compile(r"\{(\w+)\}")


def values_for(info: dict, stem: str) -> dict:
    raw = (info.get("date") or "").strip()
    d = parse_date(raw)
    has_month = bool(re.match(r"^\d{4}-\d{2}", raw))
    has_day = bool(re.match(r"^\d{4}-\d{2}-\d{2}", raw))
    has_time = bool(re.search(r"\d{1,2}:\d{2}", raw))
    return i18n.with_aliases({"titel": info.get("title", ""), "interpret": info.get("artist", ""), "album": info.get("album", ""),
            "genre": info.get("genre", ""), "datum": d.strftime("%Y-%m-%d") if d and has_day else "",
            "zeit": d.strftime("%H%M") if d and has_time else "", "jahr": d.strftime("%Y") if d else "",
            "monat": d.strftime("%m") if d and has_month else "", "tag": d.strftime("%d") if d and has_day else "",
            "wochentag": i18n.weekday(d.weekday()) if d and has_day else "", "dateiname": stem})


def build_name(pattern: str, vals: dict, skip_empty: bool) -> tuple[str, list[str]]:
    """Setzt das Muster zusammen. Liefert Name und die Liste leerer Platzhalter."""
    parts = PH_RE.split(pattern)        # literal, ph, literal, ph, ...
    lits, phs = parts[0::2], parts[1::2]
    unknown = [p for p in phs if p not in i18n.accept(PLACEHOLDERS)]
    if unknown:
        raise LibraryError(_("Unbekannter Platzhalter: {names}", names=", ".join("{" + u + "}" for u in unknown)))
    values = [safe_name(vals.get(p, "")) for p in phs]
    empty = [p for p, v in zip(phs, values) if not v]
    if skip_empty and empty:
        # leeren Platzhalter mit dem folgenden Trenner entfernen (beim letzten: mit dem davor)
        keep_lit = list(lits)
        keep_val = list(values)
        for i, v in enumerate(values):
            if v:
                continue
            if i + 1 < len(values) and any(values[i + 1:]):
                keep_lit[i + 1] = ""
            elif any(values[:i]):
                keep_lit[i] = ""
            keep_val[i] = ""
        lits, values = keep_lit, keep_val
    out = lits[0]
    for v, lit in zip(values, lits[1:]):
        out += v + lit
    name = safe_name(out)
    name = re.sub(r"^[\s_\-–·.]+|[\s_\-–·]+$", "", name)
    return name, empty


def plan_rename(root: Path, items: list[dict], pattern: str, skip_empty: bool) -> list[dict]:
    if not pattern.strip():
        raise LibraryError(_("Muster ist leer"))
    uses_tags = any(p not in ("dateiname", "filename") for p in PH_RE.findall(pattern))
    taken: dict[str, set[str]] = {}
    plan = []
    for it in items:
        p = Path(it["abs"])
        stem, ext = p.stem, p.suffix
        row = {"path": it["path"], "old": p.name, "new": p.name, "action": "keep", "note": "", "level": "ok"}
        if it.get("tag_state") == "none" and uses_tags:
            row.update(note=_("keine Tags – bleibt"), level="warn")
            plan.append(row)
            continue
        if it.get("tag_state") == "unknown":
            row.update(note=_("Tags nicht lesbar – bleibt"), level="warn")
            plan.append(row)
            continue
        name, empty = build_name(pattern, values_for(it, stem), skip_empty)
        if not name:
            row.update(note=_("Muster ergibt keinen Namen – bleibt"), level="warn")
            plan.append(row)
            continue
        new = name + ext
        folder = str(p.parent)
        used = taken.setdefault(folder, set())
        if new == p.name:
            row.update(note=_("unverändert"), level="same")
            used.add(new.lower())
            plan.append(row)
            continue
        candidate, n = new, 2
        while candidate.lower() in used or (
                (p.parent / candidate).exists() and (p.parent / candidate).resolve() != p.resolve()):
            candidate = f"{name} ({n}){ext}"
            n += 1
        used.add(candidate.lower())
        notes = []
        if candidate != new:
            notes.append(_("Name vergeben – „({n})“ angehängt", n=n - 1))
        if empty and not skip_empty:
            notes.append(_("leer: {names}", names=", ".join("{" + e + "}" for e in empty)))
        elif empty:
            notes.append(_("ausgelassen: {names}", names=", ".join("{" + e + "}" for e in empty)))
        row.update(new=candidate, action="rename", note=" · ".join(notes) or _("ok"),
                   level="warn" if candidate != new or (empty and not skip_empty) else "ok")
        plan.append(row)
    return plan


# --------------------------------------------------------------------------
# Papierkorb (Ordner deleted/ im Speicherordner)
# --------------------------------------------------------------------------
def _unique(dest_dir: Path, name: str) -> Path:
    dest = dest_dir / name
    stem, ext = Path(name).stem, Path(name).suffix
    n = 2
    while dest.exists():
        dest = dest_dir / f"{stem} ({n}){ext}"
        n += 1
    return dest


def trash_move(path: Path, rel: str, trash_root: Path) -> Path:
    """Verschiebt eine Datei nach <trash_root>/<rel>. Gleicher Name schon da: „(2)“ anhängen."""
    dest = (trash_root / rel).resolve()
    if trash_root.resolve() not in dest.parents:
        raise LibraryError(_("Pfad liegt außerhalb des Speicherordners"))
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest = _unique(dest.parent, dest.name)
    shutil.move(str(path), str(dest))
    return dest


def restore_move(src: Path, dest: Path) -> Path:
    """Legt eine Datei aus dem Papierkorb zurück. Belegter Name: „(2)“ anhängen."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest = _unique(dest.parent, dest.name)
    shutil.move(str(src), str(dest))
    return dest


# --------------------------------------------------------------------------
# Leere Ordner
# --------------------------------------------------------------------------
JUNK_FILES = {".DS_Store", "Icon\r", ".localized", "Thumbs.db", "desktop.ini"}


def is_junk(name: str) -> bool:
    """Reine Systemdateien (Finder, Windows), die einen Ordner nicht „belegen“."""
    return name in JUNK_FILES or name.startswith("._")


def _is_empty(d: Path) -> bool:
    try:
        entries = list(os.scandir(d))
    except OSError:
        return False
    return all(e.is_file(follow_symlinks=False) and is_junk(e.name) for e in entries)


def _remove_empty(d: Path) -> bool:
    """Entfernt einen Ordner nur, wenn er (bis auf Systemdateien) leer ist."""
    if not _is_empty(d):
        return False
    try:
        for e in os.scandir(d):
            os.unlink(e.path)
        os.rmdir(d)                # schlägt fehl, falls inzwischen doch etwas darin liegt
        return True
    except OSError:
        return False


def _inside(d: Path, roots: list[Path]) -> Path | None:
    for r in roots:
        if d != r and r in d.parents:
            return r
    return None


def prune_upwards(start: Path, roots: list[Path], protected: list[Path]) -> list[Path]:
    """Nach dem Löschen einer Datei: leere Ordner ab `start` nach oben entfernen, höchstens
    bis knapp unter einen der Hauptordner. Hauptordner und geschützte Ordner bleiben."""
    roots = [r.resolve() for r in roots]
    protected = {p.resolve() for p in protected} | set(roots)
    removed = []
    d = start.resolve()
    while _inside(d, roots) and d not in protected:
        if not _remove_empty(d):
            break
        removed.append(d)
        d = d.parent
    return removed


def find_empty_dirs(root: Path, protected: list[Path]) -> list[Path]:
    """Alle Ordner unter `root`, die nur leere Ordner und Systemdateien enthalten (tiefste zuerst).
    Versteckte Ordner werden nicht angefasst und zählen als Inhalt."""
    root = root.resolve()
    if not root.is_dir():
        return []
    protected = {p.resolve() for p in protected} | {root}
    empty: set[Path] = set()
    order: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(root, topdown=False):
        d = Path(dirpath)
        if any(part.startswith(".") for part in d.relative_to(root).parts):
            continue
        if any(not is_junk(f) for f in filenames):
            continue
        if any(d / sub not in empty for sub in dirnames):
            continue
        if d in protected:
            continue
        empty.add(d)
        order.append(d)
    return order


def remove_dirs(dirs: list[Path], roots: list[Path], protected: list[Path]) -> tuple[list[Path], list[str]]:
    """Entfernt die angegebenen Ordner (tiefste zuerst), jeweils nur wenn sie leer sind."""
    roots = [r.resolve() for r in roots]
    protected = {p.resolve() for p in protected} | set(roots)
    removed, skipped = [], []
    for d in sorted({p.resolve() for p in dirs}, key=lambda p: len(p.parts), reverse=True):
        if not _inside(d, roots) or d in protected:
            skipped.append(_("{name}: außerhalb oder geschützt", name=d))
            continue
        if not d.is_dir():
            continue
        if _remove_empty(d):
            removed.append(d)
        else:
            skipped.append(_("{name}: nicht leer", name=d.name))
    return removed, skipped
