"""
Escaneja un disc o carpeta i el fusiona a la base de dades des de la línia d'ordres,
amb progrés per pantalla. Equivalent al formulari de la pàgina Discs, pensat per a
discs grans (milions de fitxers) on és més còmode veure l'avanç al terminal.

Exemples (des de l'arrel del projecte):
  python tools/scan_drive.py --drive EXT-A --root F:\\
  python tools/scan_drive.py --drive EXT-A                 # usa drives.last_root_path
  python tools/scan_drive.py --drive NOU-1 --root G:\\ --kind hdd --label "Toshiba 4TB"
  python tools/scan_drive.py --drive EXT-A --root F:\\ --replace   # reinicia (perd hashes)
  python tools/scan_drive.py --drive PC-C --root C:\\ --exclude Windows --exclude pagefile.sys
  python tools/scan_drive.py --drive PC-C --exclude-from exclusions.txt   # una ruta per línia
  python tools/scan_drive.py --drive PC-C --clear-excludes                # buida la llista

Mode per defecte «update»: nous afegits, modificats actualitzats (hash invalidat),
desapareguts marcats, hashes dels no modificats conservats.
Si el serial del volum no coincideix amb el registrat per aquest drive_id, s'atura
(probable disc equivocat); --force o --replace ho permeten.

Exclusions: rutes relatives a l'arrel (carpeta amb tot el que penja, o fitxer), sense
comodins ni distinció de majúscules; s'accepten «/» i «\\» i rutes absolutes sota l'arrel.
--exclude i --exclude-from s'afegeixen a la llista desada a la fitxa del disc i la guarden;
--clear-excludes la buida abans (sol, o combinat amb les altres per substituir-la). El que
ja era a l'inventari sota una exclusió passa a «desaparegut» al proper escaneig.
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
from app.audit_to_db import (load_excludes, merge_excludes, normalize_exclude,  # noqa: E402
                             parse_exclude_lines, scan_drive)
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
    ap.add_argument("--exclude", action="append", default=[], metavar="RUTA",
                    help="Ruta relativa a l'arrel que no s'escaneja (repetible); s'afegeix a la llista desada")
    ap.add_argument("--exclude-from", type=Path, metavar="FITXER",
                    help="Fitxer UTF-8 amb una exclusió per línia (# comentaris); s'afegeixen a la llista desada")
    ap.add_argument("--clear-excludes", action="store_true", help="Buida la llista d'exclusions desada abans d'escanejar")
    args = ap.parse_args(argv)

    args.db.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(args.db), timeout=60.0)
    try:
        if args.db.stat().st_size > 0 and schema.is_legacy_db(conn):
            print("Esquema antic: executa primer  python -m app.migrate", file=sys.stderr)
            return 2
        drive = args.drive.strip()
        root = args.root
        saved_excludes: list[str] = []
        if schema.has_table(conn, "drives"):
            row = conn.execute("SELECT last_root_path, exclude_paths FROM drives WHERE drive_id = ?",
                               (drive,)).fetchone()
            if row:
                root = root or row[0]
                saved_excludes = load_excludes(row[1])
        if not root or not os.path.isdir(root):
            print(f"Arrel no vàlida o no muntada: {root!r}. Indica --root.", file=sys.stderr)
            return 2

        # Exclusions: la llista desada (o buida amb --clear-excludes) més les de la línia d'ordres
        exclude_paths: list[str] | None = None
        if args.exclude or args.exclude_from or args.clear_excludes:
            new = [normalize_exclude(p, root) for p in args.exclude]
            if args.exclude_from:
                new += parse_exclude_lines(args.exclude_from.read_text(encoding="utf-8-sig"), root)
            exclude_paths = merge_excludes([] if args.clear_excludes else saved_excludes, new)
        effective = exclude_paths if exclude_paths is not None else saved_excludes
        if effective:
            print(f"Exclusions ({len(effective)}): " + "; ".join(effective), flush=True)

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
                            force_volume=args.force or args.replace, exclude_paths=exclude_paths)
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
