import sqlite3, sys, time
sys.path.insert(0, ".")
from capes import classify, is_recup

DB = sys.argv[1]; LO = int(sys.argv[2]); HI = int(sys.argv[3])
c = sqlite3.connect(DB)
c.executescript("""
PRAGMA journal_mode=OFF; PRAGMA synchronous=OFF;
CREATE TABLE IF NOT EXISTS lay (id INTEGER PRIMARY KEY, layer TEXT NOT NULL, recup INTEGER NOT NULL);
""")
t0 = time.time(); buf = []; n = 0
cur = c.execute("SELECT id, path, name, ext FROM f WHERE id >= ? AND id < ?", (LO, HI))
rows = cur.fetchall()
for fid, path, name, ext in rows:
    pl = path.lower()
    buf.append((fid, classify(pl, name.lower(), ext), 1 if is_recup(pl) else 0))
    n += 1
    if len(buf) >= 500_000:
        c.executemany("INSERT OR REPLACE INTO lay VALUES (?,?,?)", buf); buf.clear()
c.executemany("INSERT OR REPLACE INTO lay VALUES (?,?,?)", buf)
c.commit()
print(f"tram {LO}-{HI}: {n:,} classificats en {time.time()-t0:.0f}s", flush=True)
