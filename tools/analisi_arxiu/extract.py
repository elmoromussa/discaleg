"""Extrau una còpia compacta i de només lectura d'arxiu_discs.db per a l'anàlisi arxivística.

Només llig la DB d'origen (mode ro, immutable). Escriu una DB nova a OUT amb les
columnes necessàries: disc, ruta, carpeta pare, nivells 1-3, nom, extensió, mida,
any de modificació, any de creació, i una clau de còpia (nom en minúscules + mida)
calculada amb el mateix criteri que l'auditoria (nom NFC minúscula + mida, >= 1 MB).
"""
import os, sqlite3, sys, time, unicodedata

SRC = sys.argv[1]
OUT = sys.argv[2]
BATCH = 200_000
LO = int(sys.argv[3]); HI = int(sys.argv[4])

src = sqlite3.connect(f"file:{SRC}?mode=ro&immutable=1", uri=True)
dst = sqlite3.connect(OUT)
dst.executescript("""
PRAGMA journal_mode=OFF; PRAGMA synchronous=OFF; PRAGMA cache_size=-400000;
CREATE TABLE IF NOT EXISTS f (
  id INTEGER PRIMARY KEY,
  drive TEXT NOT NULL,
  path TEXT NOT NULL,
  parent TEXT NOT NULL,
  depth INTEGER NOT NULL,
  l1 TEXT, l2 TEXT, l3 TEXT,
  name TEXT NOT NULL,
  ext TEXT NOT NULL,
  size INTEGER NOT NULL,
  myear INTEGER, cyear INTEGER,
  mdate TEXT, cdate TEXT,
  ckey TEXT
);
CREATE TABLE IF NOT EXISTS drives (drive_id TEXT PRIMARY KEY, label TEXT, kind TEXT, filesystem TEXT, capacity_bytes INTEGER, free_bytes INTEGER, last_root_path TEXT, model TEXT, serial_number TEXT);
""")
# copia de drives
rows = src.execute("SELECT drive_id, label, kind, filesystem, capacity_bytes, free_bytes, last_root_path, model, serial_number FROM drives").fetchall()
dst.executemany("INSERT OR REPLACE INTO drives VALUES (?,?,?,?,?,?,?,?,?)", rows)
dst.commit()

t0 = time.time()
last = 0
n = 0
cur = src.execute(
    "SELECT id, drive_id, relative_path, name, extension, size_bytes, modified_utc, created_utc "
    "FROM inventory WHERE missing_since_utc IS NULL AND id >= ? AND id < ? ORDER BY id", (LO, HI))
buf = []
def flush():
    dst.executemany("INSERT INTO f VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", buf)
    dst.commit()
    buf.clear()
for (fid, drive, rel, name, ext, size, mutc, cutc) in cur:
    parts = rel.split("\\")
    parent = "\\".join(parts[:-1])
    depth = len(parts) - 1
    l1 = parts[0] if depth >= 1 else ""
    l2 = parts[1] if depth >= 2 else ""
    l3 = parts[2] if depth >= 3 else ""
    myear = int(mutc[:4]) if mutc and mutc[:4].isdigit() else None
    cyear = int(cutc[:4]) if cutc and cutc[:4].isdigit() else None
    ckey = None
    if size >= 1_000_000:
        ckey = unicodedata.normalize("NFC", name).lower() + "|" + str(size)
    buf.append((fid, drive, rel, parent, depth, l1, l2, l3, name, (ext or "").lower(), size,
                myear, cyear, (mutc or "")[:10], (cutc or "")[:10], ckey))
    n += 1
    if len(buf) >= BATCH:
        flush()
        print(f"{n:,} files  {time.time()-t0:.0f}s", flush=True)
flush()
print(f"tram {LO}-{HI}: inserits {n:,} en {time.time()-t0:.0f}s", flush=True)
