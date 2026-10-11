"""
Calcula el SHA-256 dels fitxers d'un disc i el guarda a inventory.hash_sha256.

Procés incremental i reprenible: només tracta files sense hash (o amb hash invalidat
per un re-escaneig), es pot aturar amb Ctrl+C o --max-minutes i continuar més tard.
L'ordre de lectura segueix la ruta (localitat al disc). Els fitxers de 0 bytes
reben el hash constant sense obrir-los.

Exemples (des de l'arrel del projecte):
  python tools/hash_files.py --status
  python tools/hash_files.py --drive EXT-A --root F:\\
  python tools/hash_files.py --drive EXT-A                 # usa drives.last_root_path
  python tools/hash_files.py --drive SSD-2 --root E:\\BACK_SSDEX --workers 4
  python tools/hash_files.py --drive INT-7 --only-size-dupes --max-minutes 240

Opcions de rendiment:
  --workers N         fils en paral·lel (1 per a HDD; 4–8 per a SSD/NVMe)
  --only-size-dupes   només fitxers amb una mida repetida a tota la DB (candidats a duplicat)
  --min-size BYTES    ignora fitxers més petits
Seguretat: si drives.volume_serial és conegut i no coincideix amb el volum muntat a
--root, s'atura (evita hashejar el disc equivocat); --force ho omet.
"""
from __future__ import annotations

import argparse
import hashlib
import os
import signal
import sqlite3
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import schema  # noqa: E402
from app.config import database_path  # noqa: E402
from app.volume_info import get_volume_info  # noqa: E402

BUFFER = 8 * 1024 * 1024  # 8 MiB per lectura
PAGE = 2000  # files per pàgina de la DB
_stop = False


def _utc_iso(ts: float) -> str:
    try:
        return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()
    except (OSError, ValueError, OverflowError):
        return ""


def sha256_file(path: str, size_hint: int) -> str:
    if size_hint == 0:
        return schema.SHA256_EMPTY
    h = hashlib.sha256()
    if size_hint <= BUFFER:
        with open(path, "rb", buffering=0) as f:
            h.update(f.read())
        return h.hexdigest()
    buf = bytearray(BUFFER)
    mv = memoryview(buf)
    with open(path, "rb", buffering=0) as f:
        while True:
            n = f.readinto(mv)
            if not n:
                break
            h.update(mv[:n])
    return h.hexdigest()


def _hash_one(root: str, row: tuple) -> tuple:
    """row = (id, relative_path, size, mtime). Retorna (id, hash|None, size, mtime_iso, error)."""
    fid, rel, size_db, mtime_db = row
    path = os.path.join(root, rel)
    try:
        st = os.stat(path)
    except FileNotFoundError:
        return fid, None, size_db, mtime_db, "missing"
    except OSError as e:
        return fid, None, size_db, mtime_db, f"stat: {e}"
    size = st.st_size
    mtime = _utc_iso(st.st_mtime)
    try:
        digest = sha256_file(path, size)
    except OSError as e:
        return fid, None, size, mtime, f"read: {e}"
    return fid, digest, size, mtime, None


def _fmt_bytes(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} PB"


def _fmt_secs(s: float) -> str:
    s = int(s)
    return f"{s // 3600}h{(s % 3600) // 60:02d}m" if s >= 3600 else f"{s // 60}m{s % 60:02d}s"


def print_status(conn: sqlite3.Connection) -> None:
    print(f"{'disc':8} {'fitxers':>10} {'amb hash':>10} {'%':>6} {'mida':>10} {'pendent':>10}  últim escaneig / arrel")
    for r in conn.execute(
        "SELECT drive_id, files_count, hashed_files, total_bytes, hashed_bytes, last_scan_utc, last_root_path "
        "FROM drives ORDER BY drive_id"):
        pct = 100.0 * r[2] / r[1] if r[1] else 0.0
        print(f"{r[0]:8} {r[1]:>10,} {r[2]:>10,} {pct:>5.1f}% {_fmt_bytes(r[3]):>10} "
              f"{_fmt_bytes(r[3] - r[4]):>10}  {(r[5] or '')[:10]} {r[6] or ''}")


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    global _stop
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", type=Path, default=database_path)
    ap.add_argument("--drive", help="drive_id a processar")
    ap.add_argument("--root", help="Ruta on està muntat el disc (per defecte drives.last_root_path)")
    ap.add_argument("--workers", type=int, default=1)
    ap.add_argument("--only-size-dupes", action="store_true")
    ap.add_argument("--min-size", type=int, default=0)
    ap.add_argument("--max-minutes", type=float, default=0, help="Atura després d'aquest temps")
    ap.add_argument("--batch", type=int, default=500, help="Fitxers per transacció")
    ap.add_argument("--force", action="store_true", help="No comprovar el serial del volum")
    ap.add_argument("--status", action="store_true", help="Mostra l'estat per disc i surt")
    args = ap.parse_args(argv)

    if not args.db.is_file():
        print(f"No existeix la DB: {args.db}", file=sys.stderr)
        return 2
    conn = sqlite3.connect(str(args.db), timeout=60.0)
    if schema.is_legacy_db(conn):
        print("Esquema antic: executa primer  python -m app.migrate", file=sys.stderr)
        return 2
    schema.set_pragmas_for_writes(conn)

    if args.status or not args.drive:
        print_status(conn)
        return 0

    drive = args.drive.strip()
    drow = conn.execute(
        "SELECT last_root_path, volume_serial FROM drives WHERE drive_id = ?", (drive,)
    ).fetchone()
    if not drow:
        print(f"El disc «{drive}» no és a la DB.", file=sys.stderr)
        return 2
    root = args.root or drow[0]
    if not root or not os.path.isdir(root):
        print(f"Arrel no vàlida o no muntada: {root!r}. Indica --root.", file=sys.stderr)
        return 2
    vol = get_volume_info(Path(root))
    if drow[1] and vol["volume_serial"] and drow[1] != vol["volume_serial"] and not args.force:
        print(f"ATENCIÓ: el volum a {root} té serial {vol['volume_serial']} però «{drive}» "
              f"es va escanejar amb {drow[1]}. Disc equivocat? Usa --force per continuar.",
              file=sys.stderr)
        return 3
    if not drow[1] and vol["volume_serial"]:
        conn.execute("UPDATE drives SET volume_serial = ?, label = COALESCE(label, ?), "
                     "filesystem = COALESCE(filesystem, ?), capacity_bytes = COALESCE(capacity_bytes, ?) "
                     "WHERE drive_id = ?",
                     (vol["volume_serial"], vol["label"], vol["filesystem"], vol["capacity_bytes"], drive))
        conn.commit()

    where = "drive_id = ? AND hash_sha256 IS NULL AND missing_since_utc IS NULL AND size_bytes >= ?"
    params: list = [drive, args.min_size]
    if args.only_size_dupes:
        # es calcula un sol cop (una passada per tota la DB), no a cada pàgina
        conn.execute("CREATE TEMP TABLE dup_sizes (size_bytes INTEGER PRIMARY KEY) WITHOUT ROWID")
        conn.execute("INSERT INTO temp.dup_sizes SELECT size_bytes FROM inventory "
                     "WHERE missing_since_utc IS NULL GROUP BY size_bytes HAVING COUNT(*) > 1")
        conn.commit()
        where += " AND size_bytes IN (SELECT size_bytes FROM temp.dup_sizes)"
    todo_files, todo_bytes = conn.execute(
        f"SELECT COUNT(*), COALESCE(SUM(size_bytes), 0) FROM inventory WHERE {where}", params
    ).fetchone()
    print(f"{drive} @ {root}: {todo_files:,} fitxers pendents, {_fmt_bytes(todo_bytes)}"
          + (" (només mides repetides)" if args.only_size_dupes else ""))
    if todo_files == 0:
        return 0

    def on_sigint(*_):
        global _stop
        _stop = True
        print("\nAturant… (es desa el lot actual)")
    signal.signal(signal.SIGINT, on_sigint)

    t0 = time.time()
    deadline = t0 + args.max_minutes * 60 if args.max_minutes > 0 else None
    done_files = done_bytes = 0
    n_missing = n_errors = 0
    last_print = 0.0
    last_path = ""
    pool = ThreadPoolExecutor(max_workers=max(1, args.workers)) if args.workers > 1 else None

    try:
        while not _stop:
            if deadline and time.time() > deadline:
                print("\nTemps màxim assolit.")
                break
            rows = conn.execute(
                f"SELECT id, relative_path, size_bytes, modified_utc FROM inventory "
                f"WHERE {where} AND relative_path > ? ORDER BY relative_path LIMIT ?",
                params + [last_path, PAGE],
            ).fetchall()
            if not rows:
                break
            last_path = rows[-1][1]
            for i in range(0, len(rows), args.batch):
                if _stop:
                    break
                batch = rows[i:i + args.batch]
                if pool:
                    results = list(pool.map(lambda r: _hash_one(root, r), batch))
                else:
                    results = [_hash_one(root, r) for r in batch]
                now = schema.utc_now_iso()
                upd: list[tuple] = []
                miss: list[tuple] = []
                b_files = b_bytes = 0
                for fid, digest, size, mtime, err in results:
                    if digest:
                        upd.append((digest, now, size, mtime, size, mtime, fid))
                        b_files += 1; b_bytes += size
                    elif err == "missing":
                        miss.append((now, fid)); n_missing += 1
                    else:
                        n_errors += 1
                        print(f"\n  error {fid}: {err}", file=sys.stderr)
                conn.execute("BEGIN")
                conn.executemany(
                    "UPDATE inventory SET hash_sha256 = ?, hashed_utc = ?, hash_size_bytes = ?, "
                    "hash_mtime_utc = ?, size_bytes = ?, modified_utc = ? WHERE id = ?", upd)
                conn.executemany(
                    "UPDATE inventory SET missing_since_utc = ? WHERE id = ?", miss)
                conn.execute(
                    "UPDATE drives SET hashed_files = hashed_files + ?, hashed_bytes = hashed_bytes + ?, "
                    "files_count = files_count - ?, updated_utc = ? WHERE drive_id = ?",
                    (b_files, b_bytes, len(miss), now, drive))
                conn.execute("COMMIT")
                done_files += b_files; done_bytes += b_bytes
                if time.time() - last_print > 2:
                    last_print = time.time()
                    el = last_print - t0
                    rate = done_bytes / el if el > 0 else 0
                    eta = (todo_bytes - done_bytes) / rate if rate > 0 else 0
                    sys.stdout.write(
                        f"\r{done_files:,}/{todo_files:,} fitxers  {_fmt_bytes(done_bytes)}/{_fmt_bytes(todo_bytes)}"
                        f"  {rate / 1e6:.0f} MB/s  ETA {_fmt_secs(eta)}   ")
                    sys.stdout.flush()
    finally:
        if pool:
            pool.shutdown(wait=True)
        schema.refresh_drive_counters(conn, drive)
        conn.commit()
        conn.close()
    el = time.time() - t0
    print(f"\nFet: {done_files:,} fitxers, {_fmt_bytes(done_bytes)} en {_fmt_secs(el)} "
          f"({done_bytes / el / 1e6 if el else 0:.0f} MB/s). Desapareguts: {n_missing}, errors: {n_errors}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
