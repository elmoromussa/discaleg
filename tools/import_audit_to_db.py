"""
Importa els CSV d'un audit (quick_audit_archive) a una base de dades SQLite.
Permet consolidar diversos discs en una sola DB per consultar en conjunt.

Ús (un disc per execució):
  python import_audit_to_db.py --db arxiu_discs.db --audit path/to/audit_2026-01-01_120000 --drive D

Reimportar el mateix disc (substitueix dades antigues d'aquest drive_id):
  python import_audit_to_db.py --db arxiu_discs.db --audit path/to/audit_nou --drive D --replace

Llistar els discs que ja hi són a la DB:
  python import_audit_to_db.py --db arxiu_discs.db --list
"""

import argparse
import csv
import sqlite3
import sys
from pathlib import Path


def init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS inventory (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            drive_id TEXT NOT NULL,
            relative_path TEXT NOT NULL,
            name TEXT NOT NULL,
            extension TEXT NOT NULL,
            size_bytes INTEGER NOT NULL,
            modified_utc TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_inventory_drive ON inventory(drive_id);
        CREATE INDEX IF NOT EXISTS idx_inventory_extension ON inventory(drive_id, extension);
        CREATE INDEX IF NOT EXISTS idx_inventory_name ON inventory(name);

        CREATE TABLE IF NOT EXISTS folder_stats (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            drive_id TEXT NOT NULL,
            folder_path TEXT NOT NULL,
            folder_name TEXT,
            folder_depth INTEGER,
            files_count INTEGER NOT NULL,
            total_bytes INTEGER NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_folder_stats_drive ON folder_stats(drive_id);
        CREATE INDEX IF NOT EXISTS idx_folder_stats_folder_name ON folder_stats(folder_name);

        CREATE TABLE IF NOT EXISTS by_extension (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            drive_id TEXT NOT NULL,
            extension TEXT NOT NULL,
            count INTEGER NOT NULL,
            total_bytes INTEGER NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_by_extension_drive ON by_extension(drive_id);
    """)
    # Migració: afegir folder_name si la taula ja existia sense aquesta columna
    try:
        conn.execute("ALTER TABLE folder_stats ADD COLUMN folder_name TEXT")
    except sqlite3.OperationalError:
        pass  # La columna ja existeix
    try:
        conn.execute("CREATE INDEX IF NOT EXISTS idx_folder_stats_folder_name ON folder_stats(folder_name)")
    except sqlite3.OperationalError:
        pass


def import_audit(conn: sqlite3.Connection, audit_dir: Path, drive_id: str, replace: bool) -> tuple[int, int, int]:
    """Importa un sol directori d'audit. Retorna (inventory_rows, folder_rows, ext_rows)."""
    audit_dir = audit_dir.resolve()
    if not audit_dir.is_dir():
        raise FileNotFoundError(f"No és un directori: {audit_dir}")

    inv_path = audit_dir / "inventory_files.csv"
    folder_path = audit_dir / "folders_stats.csv"
    ext_path = audit_dir / "by_extension.csv"

    if not inv_path.exists():
        raise FileNotFoundError(f"Falta inventory_files.csv a {audit_dir}")

    if replace:
        conn.execute("DELETE FROM inventory WHERE drive_id = ?", (drive_id,))
        conn.execute("DELETE FROM folder_stats WHERE drive_id = ?", (drive_id,))
        conn.execute("DELETE FROM by_extension WHERE drive_id = ?", (drive_id,))

    # inventory_files.csv
    inv_count = 0
    with inv_path.open("r", newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        rows = [
            (drive_id, row["RelativePath"], row["Name"], row["Extension"], int(row["SizeBytes"]), row.get("ModifiedUtc", ""))
            for row in reader
        ]
    conn.executemany(
        "INSERT INTO inventory (drive_id, relative_path, name, extension, size_bytes, modified_utc) VALUES (?, ?, ?, ?, ?, ?)",
        rows,
    )
    inv_count = len(rows)

    # folders_stats.csv
    folder_count = 0
    if folder_path.exists():
        with folder_path.open("r", newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            rows = []
            for row in reader:
                fp = row["FolderPath"]
                folder_name_val = Path(fp).name if fp.strip() else ""
                rows.append(
                    (
                        drive_id,
                        fp,
                        folder_name_val,
                        int(row["FolderDepth"]) if row.get("FolderDepth") and row["FolderDepth"].strip() else None,
                        int(row["FilesCount"]),
                        int(row["TotalBytes"]),
                    )
                )
        conn.executemany(
            "INSERT INTO folder_stats (drive_id, folder_path, folder_name, folder_depth, files_count, total_bytes) VALUES (?, ?, ?, ?, ?, ?)",
            rows,
        )
        folder_count = len(rows)

    # by_extension.csv
    ext_count = 0
    if ext_path.exists():
        with ext_path.open("r", newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            rows = [
                (drive_id, row["Extension"], int(row["Count"]), int(row["TotalBytes"]))
                for row in reader
            ]
        conn.executemany(
            "INSERT INTO by_extension (drive_id, extension, count, total_bytes) VALUES (?, ?, ?, ?)",
            rows,
        )
        ext_count = len(rows)

    return inv_count, folder_count, ext_count


def list_drives(conn: sqlite3.Connection) -> None:
    cur = conn.execute(
        "SELECT drive_id, COUNT(*) as files, SUM(size_bytes) as total_bytes FROM inventory GROUP BY drive_id ORDER BY drive_id"
    )
    rows = cur.fetchall()
    if not rows:
        print("La base de dades no té cap disc importat.")
        return
    print(f"{'drive_id':<12} {'fitxers':>12} {'total_bytes':>18}")
    print("-" * 44)
    for drive_id, files, total_bytes in rows:
        total_bytes = total_bytes or 0
        print(f"{drive_id:<12} {files:>12} {total_bytes:>18}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Importa els CSV d'un audit (quick_audit_archive) a una SQLite. Un disc per execució."
    )
    parser.add_argument("--db", type=Path, required=True, help="Fitxer de la base de dades SQLite")
    parser.add_argument("--audit", type=Path, help="Directori de l'audit (ex: reports/audit_2026-01-01_120000)")
    parser.add_argument("--drive", type=str, help="Identificador del disc (ex: D, E, HDD1)")
    parser.add_argument("--replace", action="store_true", help="Substitueix dades existents d'aquest drive_id")
    parser.add_argument("--list", action="store_true", help="Llista els drive_id presents a la DB i surt")
    args = parser.parse_args()

    if args.list:
        if not args.db.exists():
            print(f"La base de dades no existeix: {args.db}")
            sys.exit(1)
        conn = sqlite3.connect(args.db)
        try:
            list_drives(conn)
        finally:
            conn.close()
        return

    if not args.audit or not args.drive:
        parser.error("Cal --audit i --drive (o --list per llistar)")
        return

    args.db = args.db.resolve()
    args.db.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(args.db)
    try:
        init_schema(conn)
        inv_count, folder_count, ext_count = import_audit(conn, args.audit, args.drive, args.replace)
        conn.commit()
        print(f"Importat drive_id={args.drive}: inventory={inv_count}, folder_stats={folder_count}, by_extension={ext_count}")
    except FileNotFoundError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        conn.rollback()
        print(f"Error: {e}", file=sys.stderr)
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    main()
