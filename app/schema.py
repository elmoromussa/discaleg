"""
Esquema de la base de dades i migracions.

Versió (PRAGMA user_version):
  0/1  esquema original: inventory, folder_stats, by_extension (sense drives ni hashes)
  2    + drives, scans, columnes de hash i de seguiment a inventory, índex únic
         (drive_id, relative_path), extensió buida -> '(no_ext)', folder_path relatiu.

Ús:
  - init_schema(conn): crea una DB nova directament a la versió actual (idempotent).
  - migrate(conn, ...): porta una DB existent a la versió actual (idempotent, pot
    tardar uns minuts amb milions de files; executar amb `python -m app.migrate`).
"""
from __future__ import annotations

import sqlite3
from collections.abc import Callable
from datetime import datetime, timezone

SCHEMA_VERSION = 2

# Extensió canònica per a fitxers sense extensió (coincideix amb l'escàner directe).
NO_EXT = "(no_ext)"

# Hash SHA-256 (hex) d'un fitxer de 0 bytes; s'assigna sense obrir el fitxer.
SHA256_EMPTY = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"


def utc_now_iso() -> str:
    return datetime.now(tz=timezone.utc).replace(microsecond=0).isoformat()


def get_user_version(conn: sqlite3.Connection) -> int:
    return int(conn.execute("PRAGMA user_version").fetchone()[0])


def has_table(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (name,)
    ).fetchone()
    return row is not None


def table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}


def is_legacy_db(conn: sqlite3.Connection) -> bool:
    """True si hi ha dades amb l'esquema antic (cal migrar)."""
    return has_table(conn, "inventory") and get_user_version(conn) < SCHEMA_VERSION


# ---------------------------------------------------------------------------
# DDL
# ---------------------------------------------------------------------------

_DDL_BASE = """
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
"""

_DDL_V2_TABLES = """
CREATE TABLE IF NOT EXISTS drives (
    drive_id        TEXT PRIMARY KEY,
    label           TEXT,
    kind            TEXT CHECK (kind IS NULL OR kind IN
                        ('hdd','ssd','nvme','usb','sd','optical','network','other')),
    interface       TEXT,
    vendor          TEXT,
    model           TEXT,
    serial_number   TEXT,
    volume_serial   TEXT,
    filesystem      TEXT,
    capacity_bytes  INTEGER,
    location        TEXT,
    status          TEXT NOT NULL DEFAULT 'active',
    notes           TEXT,
    files_count     INTEGER NOT NULL DEFAULT 0,
    total_bytes     INTEGER NOT NULL DEFAULT 0,
    hashed_files    INTEGER NOT NULL DEFAULT 0,
    hashed_bytes    INTEGER NOT NULL DEFAULT 0,
    last_root_path  TEXT,
    last_scan_utc   TEXT,
    created_utc     TEXT NOT NULL,
    updated_utc     TEXT
);

CREATE TABLE IF NOT EXISTS scans (
    scan_id       INTEGER PRIMARY KEY AUTOINCREMENT,
    drive_id      TEXT NOT NULL REFERENCES drives(drive_id),
    root_path     TEXT NOT NULL,
    mode          TEXT NOT NULL DEFAULT 'update',
    started_utc   TEXT NOT NULL,
    finished_utc  TEXT,
    status        TEXT NOT NULL DEFAULT 'running',
    files_count   INTEGER,
    total_bytes   INTEGER,
    files_new     INTEGER,
    files_changed INTEGER,
    files_missing INTEGER,
    tool_version  TEXT,
    notes         TEXT
);
CREATE INDEX IF NOT EXISTS idx_scans_drive ON scans(drive_id, started_utc);
"""

# Columnes noves d'inventory a la v2: (nom, tipus/DDL)
_INVENTORY_V2_COLUMNS: list[tuple[str, str]] = [
    ("hash_sha256", "TEXT"),
    ("hashed_utc", "TEXT"),
    ("hash_size_bytes", "INTEGER"),
    ("hash_mtime_utc", "TEXT"),
    ("first_seen_scan", "INTEGER"),
    ("last_seen_scan", "INTEGER"),
    ("missing_since_utc", "TEXT"),
]

_DDL_V2_INDEXES = """
CREATE UNIQUE INDEX IF NOT EXISTS ux_inventory_drive_path ON inventory(drive_id, relative_path);
CREATE INDEX IF NOT EXISTS idx_inventory_hash ON inventory(hash_sha256) WHERE hash_sha256 IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_inventory_size ON inventory(size_bytes);
"""


def _exec_statements(conn: sqlite3.Connection, sql: str) -> None:
    """Executa un bloc DDL sentència a sentència (executescript faria COMMIT implícit)."""
    for stmt in sql.split(";"):
        if stmt.strip():
            conn.execute(stmt)


def _add_missing_inventory_columns(conn: sqlite3.Connection) -> list[str]:
    existing = table_columns(conn, "inventory")
    added = []
    for name, ddl in _INVENTORY_V2_COLUMNS:
        if name not in existing:
            conn.execute(f"ALTER TABLE inventory ADD COLUMN {name} {ddl}")
            added.append(name)
    return added


def set_pragmas_for_writes(conn: sqlite3.Connection) -> None:
    """Pragmes per a escriptures massives (escaneig / hash). Cal fora de transacció."""
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = NORMAL")
    conn.execute("PRAGMA temp_store = MEMORY")
    conn.execute("PRAGMA cache_size = -262144")  # 256 MB


def init_schema(conn: sqlite3.Connection) -> None:
    """Crea l'esquema complet (DB nova) o completa'l si falta alguna cosa. Idempotent.

    Per a una DB antiga amb dades (user_version < 2) NO fa la conversió de dades:
    cal executar migrate(); aquí només es creen taules/columnes/índexs que faltin.
    """
    conn.executescript(_DDL_BASE)
    conn.executescript(_DDL_V2_TABLES)
    _add_missing_inventory_columns(conn)
    legacy = is_legacy_db(conn) and conn.execute("SELECT 1 FROM inventory LIMIT 1").fetchone()
    if not legacy:
        # DB nova (o buida): podem crear l'índex únic i fixar la versió directament
        conn.executescript(_DDL_V2_INDEXES)
        conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
    conn.commit()


# ---------------------------------------------------------------------------
# Migració de dades existents (v0/v1 -> v2)
# ---------------------------------------------------------------------------

def _legacy_root_from_folder_stats(conn: sqlite3.Connection, drive_id: str) -> str | None:
    """Dedueix l'arrel de l'escaneig antic: fila de profunditat 0 o prefix d'unitat."""
    row = conn.execute(
        "SELECT folder_path FROM folder_stats WHERE drive_id = ? AND folder_depth = 0 LIMIT 1",
        (drive_id,),
    ).fetchone()
    if row and row[0]:
        return row[0]
    row = conn.execute(
        "SELECT MIN(folder_path) FROM folder_stats WHERE drive_id = ?", (drive_id,)
    ).fetchone()
    if row and row[0] and len(row[0]) >= 3 and row[0][1:3] == ":\\":
        return row[0][:3]
    return None


def migrate(
    conn: sqlite3.Connection,
    legacy_roots: dict[str, str] | None = None,
    legacy_dates: dict[str, str] | None = None,
    log: Callable[[str], None] = print,
) -> bool:
    """Porta la DB a SCHEMA_VERSION. Retorna True si s'ha fet alguna migració.

    legacy_roots: drive_id -> arrel usada a l'escaneig antic (p. ex. 'F:\\\\'). Si falta,
                  es dedueix de folder_stats.
    legacy_dates: drive_id -> data ISO de l'escaneig antic (opcional).
    """
    version = get_user_version(conn)
    if version >= SCHEMA_VERSION:
        log(f"La DB ja és a la versió {version}; res a fer.")
        return False
    if not has_table(conn, "inventory"):
        init_schema(conn)
        log("DB buida: esquema creat directament a la versió actual.")
        return True

    legacy_roots = dict(legacy_roots or {})
    legacy_dates = dict(legacy_dates or {})
    now = utc_now_iso()

    log(f"Migrant de la versió {version} a la {SCHEMA_VERSION}…")
    conn.execute("PRAGMA foreign_keys = OFF")
    conn.execute("BEGIN")
    try:
        # 1) Taules i columnes noves
        _exec_statements(conn, _DDL_BASE)  # per si falta algun índex antic
        _exec_statements(conn, _DDL_V2_TABLES)
        added = _add_missing_inventory_columns(conn)
        log(f"  columnes afegides a inventory: {', '.join(added) or '(cap)'}")

        # 2) Normalitzar extensió buida
        n = conn.execute(
            "UPDATE inventory SET extension = ? WHERE extension = ''", (NO_EXT,)
        ).rowcount
        log(f"  extensions buides -> {NO_EXT}: {n} fitxers")
        # by_extension: fusionar '' amb '(no_ext)' si existeixen ambdues
        conn.execute(
            """
            INSERT INTO by_extension (drive_id, extension, count, total_bytes)
            SELECT drive_id, ?, SUM(count), SUM(total_bytes)
            FROM by_extension WHERE extension IN ('', ?) GROUP BY drive_id
            HAVING COUNT(*) > 0
            """,
            (NO_EXT, NO_EXT),
        )
        # esborrar les files originals ('' i '(no_ext)' antigues), deixant la fusionada (id més alt)
        conn.execute(
            """
            DELETE FROM by_extension WHERE extension IN ('', ?) AND id NOT IN (
                SELECT MAX(id) FROM by_extension WHERE extension = ? GROUP BY drive_id)
            """,
            (NO_EXT, NO_EXT),
        )

        # 3) Índexs nous (l'únic falla si hi ha duplicats: millor saber-ho ara)
        _exec_statements(conn, _DDL_V2_INDEXES)
        log("  índexs creats (únic drive_id+relative_path, hash, mida)")

        # 4) drives + scans històrics + folder_path relatiu + seguiment a inventory
        drive_ids = [r[0] for r in conn.execute("SELECT DISTINCT drive_id FROM inventory ORDER BY 1")]
        for d in drive_ids:
            root = legacy_roots.get(d) or _legacy_root_from_folder_stats(conn, d) or ""
            when = legacy_dates.get(d) or now
            conn.execute(
                "INSERT OR IGNORE INTO drives (drive_id, created_utc) VALUES (?, ?)", (d, when)
            )
            cur = conn.execute(
                """
                INSERT INTO scans (drive_id, root_path, mode, started_utc, finished_utc, status,
                                   tool_version, notes)
                VALUES (?, ?, 'replace', ?, ?, 'done', 'legacy-csv-import',
                        'Escaneig original (quick_audit_archive + import CSV), migrat a v2')
                """,
                (d, root, when, when),
            )
            scan_id = cur.lastrowid

            # folder_path absolut -> relatiu a l'arrel ('' per a l'arrel mateixa)
            if root:
                root_clean = root.rstrip("\\/")
                prefix = root_clean + "\\"
                conn.execute(
                    """
                    UPDATE folder_stats SET folder_path =
                        CASE
                          WHEN upper(rtrim(folder_path, '\\')) = upper(?) THEN ''
                          WHEN upper(substr(folder_path, 1, ?)) = upper(?) THEN substr(folder_path, ?)
                          ELSE folder_path
                        END
                    WHERE drive_id = ?
                    """,
                    (root_clean, len(prefix), prefix, len(prefix) + 1, d),
                )

            conn.execute(
                "UPDATE inventory SET first_seen_scan = ?, last_seen_scan = ? WHERE drive_id = ?",
                (scan_id, scan_id, d),
            )
            refresh_drive_counters(conn, d, root_path=root, scan_utc=when)
            log(f"  {d}: arrel antiga «{root or '?'}», scan #{scan_id}")

        conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise

    log("  ANALYZE…")
    conn.execute("ANALYZE")
    conn.commit()
    log("Migració completada.")
    return True


def refresh_drive_counters(
    conn: sqlite3.Connection,
    drive_id: str,
    root_path: str | None = None,
    scan_utc: str | None = None,
) -> None:
    """Recalcula els comptadors de drives a partir d'inventory (fitxers presents)."""
    files, total, hfiles, hbytes = conn.execute(
        """
        SELECT COUNT(*), COALESCE(SUM(size_bytes), 0),
               COALESCE(SUM(hash_sha256 IS NOT NULL), 0),
               COALESCE(SUM(CASE WHEN hash_sha256 IS NOT NULL THEN size_bytes ELSE 0 END), 0)
        FROM inventory WHERE drive_id = ? AND missing_since_utc IS NULL
        """,
        (drive_id,),
    ).fetchone()
    conn.execute(
        """
        UPDATE drives SET files_count = ?, total_bytes = ?, hashed_files = ?, hashed_bytes = ?,
               last_root_path = COALESCE(?, last_root_path),
               last_scan_utc = COALESCE(?, last_scan_utc),
               updated_utc = ?
        WHERE drive_id = ?
        """,
        (files, total, hfiles, hbytes, root_path, scan_utc, utc_now_iso(), drive_id),
    )
