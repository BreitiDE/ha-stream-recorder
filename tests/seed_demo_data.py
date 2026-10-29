"""Legt Demo-Daten für die Entwicklung an (Sender, Aufträge, Aufnahmen, Einzeltitel, Papierkorb).

    python3 tests/seed_demo_data.py /pfad/recorder.db /pfad/speicherordner
"""
import sqlite3
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

APP = Path(__file__).resolve().parents[1] / "stream_recorder" / "app"
sys.path.insert(0, str(APP))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import stream_recorder as sr  # noqa: E402

db_path, storage = sys.argv[1], sys.argv[2]
sr.DB(Path(db_path))                     # Schema anlegen
con = sqlite3.connect(db_path)
now = time.time()
fake = "http://127.0.0.1:8001"
con.execute("INSERT INTO settings(key,value) VALUES('seeded','true')")
for name, url in (("sunshine live Lounge", sr.SEED_SENDERS[0][1]), ("Fake Lounge (Test)", f"{fake}/play.m3u")):
    con.execute("INSERT INTO senders(name,url,created) VALUES(?,?,?)", (name, url, now))


def at(days, hour, minute=0):
    d = (datetime.now() + timedelta(days=days)).replace(hour=hour, minute=minute, second=0, microsecond=0)
    return d.timestamp()


jobs = [
    ("Demo: läuft gerade", None, "Fake Lounge (Test)", f"{fake}/play.m3u", now - 60, 45, 0, 0, "once", "now"),
    ("Classics mit Eric SSL & DJ Falk", "Eric SSL, DJ Falk", "Fake Lounge (Test)", f"{fake}/play.m3u", at(1, 9), 240, 1, 3, "weekly", "manual"),
    ("Sugar Radio mit Robin Schulz", "Robin Schulz", "Fake Lounge (Test)", f"{fake}/play.m3u", at(1, 21), 60, 1, 3, "once", "manual"),
    ("Lounge am Abend", None, "sunshine live Lounge", sr.SEED_SENDERS[0][1], at(2, 20), 90, 0, 0, "weekdays", "manual"),
]
for j in jobs:
    con.execute("INSERT INTO jobs(title,host,sender,url,start,duration_min,pre_min,post_min,repeat,source,created)"
                " VALUES(?,?,?,?,?,?,?,?,?,?,?)", (*j, now))
from demo_files import seed_library  # noqa: E402
seed_library(con, storage, now, f"{fake}/play.m3u")
con.commit()
print("ok")
