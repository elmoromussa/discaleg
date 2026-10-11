"""
CLI: migra una base de dades existent a l'esquema actual.

  python -m app.migrate                       # usa DATABASE_PATH o data/arxiu_discs.db
  python -m app.migrate --db ruta\\a.db
  python -m app.migrate --legacy-audits C:\\...\\AUDIT_HHDD

--legacy-audits: carpeta amb una subcarpeta per drive_id que contingui audit_log.txt
(sortida de quick_audit_archive). Se'n llegeix l'arrel escanejada («ROOT: F:\\») i la
data (nom «audit_AAAA-MM-DD_HHMMSS» de la carpeta OUT) per registrar l'escaneig
històric. Sense aquesta opció, l'arrel es dedueix de folder_stats.

Es recomana fer-ho sobre una còpia de la DB. Amb ~5 M de fitxers tarda uns minuts.
"""
from __future__ import annotations

import argparse
import re
import sqlite3
import sys
import time
from pathlib import Path

from app import schema
from app.config import database_path

_RE_ROOT = re.compile(r"^ROOT:\s*(.+?)\s*$", re.M)
_RE_STAMP = re.compile(r"audit_(\d{4}-\d{2}-\d{2})_(\d{2})(\d{2})(\d{2})")


def read_legacy_audits(folder: Path) -> tuple[dict[str, str], dict[str, str]]:
    roots: dict[str, str] = {}
    dates: dict[str, str] = {}
    for sub in sorted(folder.iterdir()):
        log = sub / "audit_log.txt"
        if not sub.is_dir() or not log.is_file():
            continue
        text = log.read_text(encoding="utf-8", errors="replace")
        m = _RE_ROOT.search(text)
        if m:
            roots[sub.name] = m.group(1)
        m = _RE_STAMP.search(text)
        if m:
            # hora local de l'escaneig; es guarda sense zona per no inventar-ne una
            dates[sub.name] = f"{m.group(1)}T{m.group(2)}:{m.group(3)}:{m.group(4)}"
    return roots, dates


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description="Migra arxiu_discs.db a l'esquema actual.")
    ap.add_argument("--db", type=Path, default=database_path, help="Ruta de la DB")
    ap.add_argument("--legacy-audits", type=Path, default=None,
                    help="Carpeta AUDIT_HHDD amb <drive_id>/audit_log.txt")
    ap.add_argument("--check", action="store_true", help="Només informa de la versió")
    args = ap.parse_args(argv)

    if not args.db.is_file():
        print(f"No existeix la DB: {args.db}", file=sys.stderr)
        return 2

    conn = sqlite3.connect(str(args.db), timeout=60.0)
    try:
        v = schema.get_user_version(conn)
        print(f"DB: {args.db}  (versió {v}, objectiu {schema.SCHEMA_VERSION})")
        if args.check:
            return 0 if v >= schema.SCHEMA_VERSION else 1

        roots: dict[str, str] = {}
        dates: dict[str, str] = {}
        if args.legacy_audits:
            roots, dates = read_legacy_audits(args.legacy_audits)
            print(f"Arrels llegides dels logs d'auditoria: {len(roots)}")

        schema.set_pragmas_for_writes(conn)
        t0 = time.time()
        changed = schema.migrate(conn, legacy_roots=roots, legacy_dates=dates)
        print(f"Temps: {time.time() - t0:.1f} s")
        print("Comprovació d'integritat…", conn.execute("PRAGMA quick_check").fetchone()[0])
        return 0 if changed or v >= schema.SCHEMA_VERSION else 1
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
