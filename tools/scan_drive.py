"""
Escaneja un disc o carpeta i el fusiona a la base de dades des de la línia d'ordres,
amb progrés per pantalla. Equivalent al formulari de la pàgina Discs, pensat per a
discs grans (milions de fitxers) on és més còmode veure l'avanç al terminal.

Exemples (des de l'arrel del projecte):
  python tools/scan_drive.py --drive EXT-A --root F:\\
  python tools/scan_drive.py --drive EXT-A                 # usa drives.last_root_path
  python tools/scan_drive.py --drive NOU-1 --root G:\\ --kind hdd --label "Toshiba 4TB"
  python tools/scan_drive.py --drive EXT-A --root F:\\ --replace   # reinicia (perd hashes)

Mode per defecte «update»: nous afegits, modificats actualitzats (hash invalidat),
desapareguts marcats, hashes dels no modificats conservats.
Si el serial del volum no coincideix amb el registrat per aquest drive_id, s'atura
(probable disc equivocat); --force o --replace ho permeten.
"""
from __future__ import annotations

import argparse
import os
import sqlite3
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import schema  # noqa: E402
from app.audit_to_db import scan_drive  # noqa: E402
from app.config import database_path  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", type=Path, default=database_path)
    ap.add_argument("--drive", required=True, help="drive_id (p. ex. EXT-A)")
    ap.add_argument("--root", help="Arrel a escanejar (per defecte drives.last_root_path)")
    ap.add_argument("--replace", action="store_true", help="Reiniciar el disc (esborra files i hashes)")
    ap.add_argument("--force", action="store_true", help="Ignorar la comprovació del serial del volum")
    ap.add_argument("--label", help="Etiqueta per a la fitxa del disc (drives.label)")
    ap.add_argument("--kind", choices=["hdd", "ssd", "nvme", "usb", "sd", "optical", "network", "other"])
    ap.add_argument("--notes", help="Notes per a la fitxa del disc")
    args = ap.parse_args(argv)

    args.db.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(args.db), timeout=60.0)
    try:
        if args.db.stat().st_size > 0 and schema.is_legacy_db(conn):
            print("Esquema antic: executa primer  python -m app.migrate", file=sys.stderr)
            return 2
        drive = args.drive.strip()
        root = args.root
        if not root and schema.has_table(conn, "drives"):
            row = conn.execute("SELECT last_root_path FROM drives WHERE drive_id = ?", (drive,)).fetchone()
            root = row[0] if row else None
        if not root or not os.path.isdir(root):
            print(f"Arrel no vàlida o no muntada: {root!r}. Indica --root.", file=sys.stderr)
            return 2

        mode = "replace" if args.replace else "update"
        print(f"Escanejant «{drive}» des de {root} (mode {mode})…", flush=True)
        t0 = time.time()
        last = [0.0]

        def progress(count: int, rel_dir: str) -> None:
            if time.time() - last[0] >= 2:
                last[0] = time.time()
                short = rel_dir if len(rel_dir) <= 60 else "…" + rel_dir[-59:]
                sys.stdout.write(f"\r{count:>10,} fitxers  {time.time() - t0:6.0f} s  {short:<60}")
                sys.stdout.flush()

        result = scan_drive(conn, root, drive, mode=mode, progress=progress,
                            force_volume=args.force or args.replace)
        sys.stdout.write("\r" + " " * 100 + "\r")
        print(result.message())

        sets, params = [], []
        for col, val in (("label", args.label), ("kind", args.kind), ("notes", args.notes)):
            if val:
                sets.append(f"{col} = ?"); params.append(val)
        if sets:
            params += [schema.utc_now_iso(), drive]
            conn.execute(f"UPDATE drives SET {', '.join(sets)}, updated_utc = ? WHERE drive_id = ?", params)
            conn.commit()
        return 0
    except Exception as e:
        print(f"\nERROR: {e}", file=sys.stderr)
        return 1
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
