"""
Esquema de la base de dades i migracions.

Versió (PRAGMA user_version):
  0/1  esquema original: inventory, folder_stats, by_extension (sense drives ni hashes)
  2    + drives, scans, columnes de hash i de seguiment a inventory, índex únic
         (drive_id, relative_path), extensió buida -> '(no_ext)', folder_path relatiu.
  3    + índex de text complet FTS5 (trigram) sobre inventory(name, relative_path),
         mantingut amb triggers; accelera la cerca per subcadena.
  4    idx_inventory_name substituït per un índex parcial cobridor
         (name, size_bytes, drive_id, extension) WHERE missing_since_utc IS NULL:
         els duplicats per nom i per nom+mida es resolen només amb l'índex.
  5    folder_stats inclou totes les carpetes (també les sense fitxers directes) i té
         índexs (drive_id, folder_depth) i (drive_id, folder_path) per a l'explorador;
         idx_inventory_hash substituït per un índex parcial cobridor
         (hash_sha256, size_bytes, drive_id) per als duplicats per contingut.
  6    + scan_changes (detall per fitxer de cada escaneig en mode update: nous, modificats,
         desapareguts), índex parcial cobridor (drive_id, hash_sha256, size_bytes) per als
         informes de còpia única i comparació entre discs, i taules dels espais virtuals:
         spaces (jeràrquics; cerques guardades o col·leccions), items (identitat d'un fitxer
         per hash amb disc+ruta com a referència secundària, o d'una carpeta per prefix),
         space_members, tags, item_tags. Tot additiu: la migració tarda segons.
  7    + identitat física del disc (drives: free_bytes, physical_size_bytes, health_status;
         scans: serial_number, model, interface, free_bytes, health_status, errors_count),
         inventory.created_utc i inventory.attributes (data de creació i atributs de Windows,
         sense efecte sobre el criteri de «modificat»), i la taula read_errors amb les carpetes
         i fitxers que l'escaneig o el hash no han pogut llegir. Només ALTER TABLE ADD COLUMN
         i una taula nova: la migració tarda segons.
  8    + exclusions per disc: drives.exclude_paths (llista JSON de rutes relatives a l'arrel,
         carpetes o fitxers, que l'escaneig no recorre) i scans.exclude_paths (la llista
         aplicada en aquell escaneig; NULL als escaneigs anteriors a la v8). Dues columnes.
  9    + migració a l'arxiu (docs/eines-migracio.md §0): taules series, projects, candidates,
         candidate_review, project_sources, project_refs, psx_check, events i copies, i la
         columna inventory.analysis_class (classe d'anàlisi de app/classes.py, cau que omple
         tools/cataleg.py). Tot additiu: la migració tarda segons.

Ús:
  - init_schema(conn): crea una DB nova directament a la versió actual (idempotent).
  - migrate(conn, ...): porta una DB existent a la versió actual pas a pas (idempotent;
    pot tardar minuts amb milions de files; executar amb `python -m app.migrate`).
"""
from __future__ import annotations

import sqlite3
from collections.abc import Callable
from datetime import datetime, timezone

SCHEMA_VERSION = 9

# Tipus de suport admesos a drives.kind (CHECK) i la seva etiqueta a la interfície.
DRIVE_KINDS: dict[str, str] = {
    "hdd": "Disc dur",
    "ssd": "SSD",
    "nvme": "NVMe",
    "usb": "Memòria USB",
    "sd": "Targeta SD",
    "optical": "Òptic",
    "network": "Xarxa",
    "other": "Altre",
}

# Extensió canònica per a fitxers sense extensió (coincideix amb l'escàner directe).
NO_EXT = "(no_ext)"

# Hash SHA-256 (hex) d'un fitxer de 0 bytes; s'assigna sense obrir el fitxer.
SHA256_EMPTY = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"

# Nom de la taula FTS5 (consultada des de queries.py)
FTS_TABLE = "inventory_fts"

# Atributs de Windows (inventory.attributes) d'un fitxer que només és al núvol (OneDrive i
# similars, «fitxers sota demanda»): llegir-lo en força la descàrrega. El hash els salta.
FILE_ATTRIBUTE_OFFLINE = 0x1000
FILE_ATTRIBUTE_RECALL_ON_OPEN = 0x40000
FILE_ATTRIBUTE_RECALL_ON_DATA_ACCESS = 0x400000
FILE_ATTR_CLOUD_MASK = (FILE_ATTRIBUTE_OFFLINE | FILE_ATTRIBUTE_RECALL_ON_OPEN
                        | FILE_ATTRIBUTE_RECALL_ON_DATA_ACCESS)

# Fases en què es pot registrar un error de lectura (read_errors.phase) i la seva etiqueta.
READ_ERROR_PHASES: dict[str, str] = {
    "scandir": "Carpeta il·legible",
    "entry": "Entrada il·legible",
    "hash_stat": "Hash: stat",
    "hash_read": "Hash: lectura",
}


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
    """True si hi ha dades amb un esquema anterior (cal migrar)."""
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

# v3: FTS5 trigram com a índex extern sobre inventory (content=), sincronitzat amb triggers.
# El trigger d'UPDATE només salta si canvia name o relative_path (l'escaneig no els toca
# en files existents; el hash tampoc), així els re-escaneigs no reindexen res.
_DDL_V3_FTS = f"""
CREATE VIRTUAL TABLE IF NOT EXISTS {FTS_TABLE} USING fts5(
    name, relative_path, content='inventory', content_rowid='id', tokenize='trigram');
CREATE TRIGGER IF NOT EXISTS inventory_fts_ai AFTER INSERT ON inventory BEGIN
    INSERT INTO {FTS_TABLE}(rowid, name, relative_path) VALUES (new.id, new.name, new.relative_path);
END;
CREATE TRIGGER IF NOT EXISTS inventory_fts_ad AFTER DELETE ON inventory BEGIN
    INSERT INTO {FTS_TABLE}({FTS_TABLE}, rowid, name, relative_path)
        VALUES ('delete', old.id, old.name, old.relative_path);
END;
CREATE TRIGGER IF NOT EXISTS inventory_fts_au AFTER UPDATE OF name, relative_path ON inventory BEGIN
    INSERT INTO {FTS_TABLE}({FTS_TABLE}, rowid, name, relative_path)
        VALUES ('delete', old.id, old.name, old.relative_path);
    INSERT INTO {FTS_TABLE}(rowid, name, relative_path) VALUES (new.id, new.name, new.relative_path);
END;
"""


# v4: índex parcial cobridor per a duplicats per nom / nom+mida (GROUP BY sense ordenació
# temporal ni accés a la taula) i per a cercar per nom exacte entre fitxers presents.
# Substitueix idx_inventory_name (name), que la cerca per LIKE no feia servir.
_DDL_V4_INDEXES = """
DROP INDEX IF EXISTS idx_inventory_name;
CREATE INDEX IF NOT EXISTS idx_inventory_name_size
    ON inventory(name, size_bytes, drive_id, extension) WHERE missing_since_utc IS NULL;
"""


# v5: explorador sobre folder_stats i duplicats per hash només amb l'índex.
_DDL_V5_INDEXES = """
CREATE INDEX IF NOT EXISTS idx_folder_stats_drive_depth ON folder_stats(drive_id, folder_depth);
CREATE INDEX IF NOT EXISTS idx_folder_stats_drive_path ON folder_stats(drive_id, folder_path);
DROP INDEX IF EXISTS idx_inventory_hash;
CREATE INDEX IF NOT EXISTS idx_inventory_hash_size
    ON inventory(hash_sha256, size_bytes, drive_id)
    WHERE hash_sha256 IS NOT NULL AND missing_since_utc IS NULL;
"""


# v6: canvis per fitxer de cada escaneig, índex per disc dels fitxers amb hash, espais virtuals.
_DDL_V6 = """
CREATE TABLE IF NOT EXISTS scan_changes (
    id            INTEGER PRIMARY KEY,
    scan_id       INTEGER NOT NULL REFERENCES scans(scan_id),
    drive_id      TEXT NOT NULL,
    relative_path TEXT NOT NULL,
    change        TEXT NOT NULL CHECK (change IN ('new', 'changed', 'missing')),
    old_size      INTEGER,
    new_size      INTEGER,
    old_mtime     TEXT,
    new_mtime     TEXT
);
CREATE INDEX IF NOT EXISTS idx_scan_changes_scan ON scan_changes(scan_id, change, relative_path);

CREATE INDEX IF NOT EXISTS idx_inventory_drive_hash
    ON inventory(drive_id, hash_sha256, size_bytes)
    WHERE hash_sha256 IS NOT NULL AND missing_since_utc IS NULL;

CREATE TABLE IF NOT EXISTS spaces (
    space_id    INTEGER PRIMARY KEY,
    parent_id   INTEGER REFERENCES spaces(space_id) ON DELETE CASCADE,
    name        TEXT NOT NULL,
    kind        TEXT NOT NULL CHECK (kind IN ('smart', 'manual')),
    query_json  TEXT,
    description TEXT,
    sort_order  INTEGER NOT NULL DEFAULT 0,
    created_utc TEXT NOT NULL,
    updated_utc TEXT
);
CREATE INDEX IF NOT EXISTS idx_spaces_parent ON spaces(parent_id, sort_order, name);

CREATE TABLE IF NOT EXISTS items (
    item_id       INTEGER PRIMARY KEY,
    kind          TEXT NOT NULL CHECK (kind IN ('file', 'folder')),
    drive_id      TEXT NOT NULL,
    relative_path TEXT NOT NULL,
    hash_sha256   TEXT,
    size_bytes    INTEGER,
    name          TEXT,
    range_lo      TEXT,
    range_hi      TEXT,
    created_utc   TEXT NOT NULL,
    UNIQUE (kind, drive_id, relative_path)
);
CREATE INDEX IF NOT EXISTS idx_items_hash ON items(hash_sha256) WHERE hash_sha256 IS NOT NULL;

CREATE TABLE IF NOT EXISTS space_members (
    space_id  INTEGER NOT NULL REFERENCES spaces(space_id) ON DELETE CASCADE,
    item_id   INTEGER NOT NULL REFERENCES items(item_id) ON DELETE CASCADE,
    added_utc TEXT NOT NULL,
    note      TEXT,
    PRIMARY KEY (space_id, item_id)
);
CREATE INDEX IF NOT EXISTS idx_space_members_item ON space_members(item_id);

CREATE TABLE IF NOT EXISTS tags (
    tag_id      INTEGER PRIMARY KEY,
    name        TEXT NOT NULL UNIQUE COLLATE NOCASE,
    color       TEXT,
    created_utc TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS item_tags (
    item_id   INTEGER NOT NULL REFERENCES items(item_id) ON DELETE CASCADE,
    tag_id    INTEGER NOT NULL REFERENCES tags(tag_id) ON DELETE CASCADE,
    added_utc TEXT NOT NULL,
    PRIMARY KEY (item_id, tag_id)
);
CREATE INDEX IF NOT EXISTS idx_item_tags_tag ON item_tags(tag_id);
"""


# v7: identitat física del disc, data de creació i atributs per fitxer, errors de lectura.
# Columnes noves (taula -> [(nom, DDL)]), afegides amb ALTER TABLE si falten (idempotent).
_V7_COLUMNS: dict[str, list[tuple[str, str]]] = {
    "drives": [
        ("free_bytes", "INTEGER"),           # espai lliure del volum a l'últim escaneig
        ("physical_size_bytes", "INTEGER"),  # mida del disc físic (capacity_bytes és la del volum)
        ("health_status", "TEXT"),           # Get-Disk HealthStatus (Healthy, Warning, Unhealthy)
    ],
    "scans": [
        ("serial_number", "TEXT"),   # maquinari vist en aquest escaneig (també si és genèric)
        ("model", "TEXT"),
        ("interface", "TEXT"),       # bus: NVMe, SATA, USB, …
        ("free_bytes", "INTEGER"),
        ("health_status", "TEXT"),
        ("errors_count", "INTEGER"),  # files de read_errors d'aquest escaneig
    ],
    "inventory": [
        ("created_utc", "TEXT"),   # st_birthtime (data de creació)
        ("attributes", "INTEGER"),  # st_file_attributes (atributs de Windows)
    ],
}

_DDL_V7 = """
CREATE TABLE IF NOT EXISTS read_errors (
    id            INTEGER PRIMARY KEY,
    drive_id      TEXT NOT NULL,
    scan_id       INTEGER REFERENCES scans(scan_id),
    phase         TEXT NOT NULL CHECK (phase IN ('scandir', 'entry', 'hash_stat', 'hash_read')),
    relative_path TEXT NOT NULL,
    error_code    INTEGER,
    message       TEXT,
    created_utc   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_read_errors_scan ON read_errors(scan_id);
CREATE INDEX IF NOT EXISTS idx_read_errors_drive_path ON read_errors(drive_id, relative_path);
"""

# v8: exclusions per disc (llista JSON de rutes relatives a l'arrel de l'escaneig).
_V8_COLUMNS: dict[str, list[tuple[str, str]]] = {
    "drives": [("exclude_paths", "TEXT")],  # la llista de la fitxa, aplicada per defecte
    "scans": [("exclude_paths", "TEXT")],   # la llista aplicada en aquell escaneig ('[]' si cap)
}

# v9: migració a l'arxiu (model d'arxiu v1.3, docs/eines-migracio.md §0).
# Classes d'anàlisi (app/classes.py) amb columna de bytes a `candidates`; l'ordre és el de les
# columnes b_* i el del CSV del catàleg.
ANALYSIS_CLASSES: tuple[str, ...] = (
    "ORIG", "IMG", "VID", "PROJ", "PROC", "PROD", "DOC", "AUD", "CODI", "SOFT", "IMGDISC",
    "COMP", "SYS", "ALTRES",
)
CANDIDATE_KINDS: tuple[str, ...] = ("projecte", "capa", "residu", "recuperacio", "sistema")
PROJECT_LAYERS: tuple[str, ...] = ("RAW", "PROJ", "OUT", "DOC")

_V9_COLUMNS: dict[str, list[tuple[str, str]]] = {
    "inventory": [("analysis_class", "TEXT")],  # classe d'anàlisi (cau; NULL = pendent)
}

_DDL_V9 = f"""
CREATE TABLE IF NOT EXISTS series (
    serie_id      INTEGER PRIMARY KEY,
    name          TEXT NOT NULL UNIQUE,
    code          TEXT,
    kind          TEXT NOT NULL CHECK (kind IN ('lloc', 'programa', 'client', 'tipus', 'varis')),
    status        TEXT NOT NULL DEFAULT 'activa' CHECK (status IN ('activa', 'inactiva')),
    disk_a_serial TEXT,
    disk_b_serial TEXT,
    notes         TEXT,
    created_utc   TEXT NOT NULL,
    updated_utc   TEXT
);

CREATE TABLE IF NOT EXISTS projects (
    project_id    INTEGER PRIMARY KEY,
    serie_id      INTEGER NOT NULL REFERENCES series(serie_id),
    name          TEXT NOT NULL,
    year          INTEGER,
    status        TEXT NOT NULL DEFAULT 'obert'
                  CHECK (status IN ('obert', 'en-tancament', 'tancat', 'descartat')),
    primary_model TEXT CHECK (primary_model IS NULL OR primary_model IN ('primari', 'secundari')),
    rights        TEXT,
    exceptions    TEXT,
    raw_style     TEXT CHECK (raw_style IS NULL OR raw_style IN ('nou', 'llegat')),
    created_utc   TEXT NOT NULL,
    updated_utc   TEXT,
    sealed_utc    TEXT,
    UNIQUE (serie_id, name)
);

CREATE TABLE IF NOT EXISTS candidates (
    candidate_id        INTEGER PRIMARY KEY,
    drive_id            TEXT NOT NULL,
    relative_path       TEXT NOT NULL,
    kind                TEXT NOT NULL CHECK (kind IN {CANDIDATE_KINDS!r}),
    parent_path         TEXT,
    files_count         INTEGER NOT NULL DEFAULT 0,
    total_bytes         INTEGER NOT NULL DEFAULT 0,
    unique_bytes_approx INTEGER,
    {", ".join(f"b_{c.lower():<8} INTEGER NOT NULL DEFAULT 0" for c in ANALYSIS_CLASSES)},
    min_mtime           TEXT,
    max_mtime           TEXT,
    markers_json        TEXT,
    proposed_serie      TEXT,
    serie_match         TEXT,
    proposed_project    TEXT,
    proposed_layer      TEXT CHECK (proposed_layer IS NULL OR
                                    proposed_layer IN ('RAW', 'PROJ', 'OUT', 'DOC', 'FORA')),
    proposed_year       INTEGER,
    layer_confidence    TEXT CHECK (layer_confidence IS NULL OR
                                    layer_confidence IN ('regla', 'nom', 'ambigu')),
    same_content_drives TEXT,
    canonical_proposed  INTEGER NOT NULL DEFAULT 0,
    generated_scan_set  TEXT,
    generated_utc       TEXT,
    UNIQUE (drive_id, relative_path)
);

CREATE TABLE IF NOT EXISTS candidate_review (
    drive_id      TEXT NOT NULL,
    relative_path TEXT NOT NULL,
    candidate_id  INTEGER REFERENCES candidates(candidate_id),
    serie         TEXT,
    project       TEXT,
    layer         TEXT CHECK (layer IS NULL OR layer IN ('RAW', 'PROJ', 'OUT', 'DOC')),
    year          INTEGER,
    decision      TEXT CHECK (decision IS NULL OR decision IN
                      ('tancar', 'obert', 'descartar', 'fusionar-amb', 'tercers', 'personal')),
    fusion_target TEXT,
    canonical     INTEGER,
    primary_model TEXT CHECK (primary_model IS NULL OR primary_model IN ('primari', 'secundari')),
    exceptions    TEXT,
    notes         TEXT,
    reviewed_utc  TEXT NOT NULL,
    PRIMARY KEY (drive_id, relative_path)
);

CREATE TABLE IF NOT EXISTS project_sources (
    project_id   INTEGER NOT NULL REFERENCES projects(project_id),
    candidate_id INTEGER NOT NULL REFERENCES candidates(candidate_id),
    layer        TEXT CHECK (layer IS NULL OR layer IN ('RAW', 'PROJ', 'OUT', 'DOC')),
    canonical    INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (project_id, candidate_id)
);

CREATE TABLE IF NOT EXISTS project_refs (
    project_id      INTEGER NOT NULL REFERENCES projects(project_id),
    uses_project_id INTEGER NOT NULL REFERENCES projects(project_id),
    kind            TEXT NOT NULL CHECK (kind IN ('captures', 'gis-base')),
    PRIMARY KEY (project_id, uses_project_id, kind)
);

CREATE TABLE IF NOT EXISTS psx_check (
    id                   INTEGER PRIMARY KEY,
    drive_id             TEXT NOT NULL,
    relative_path        TEXT NOT NULL,
    checked_utc          TEXT NOT NULL,
    app_version          TEXT,
    chunks               INTEGER,
    cameras              INTEGER,
    resolved_same        INTEGER,
    resolved_other_drive INTEGER,
    orphans              INTEGER,
    photos_json          TEXT,
    notes                TEXT,
    UNIQUE (drive_id, relative_path)
);

CREATE TABLE IF NOT EXISTS events (
    event_id      INTEGER PRIMARY KEY,
    utc           TEXT NOT NULL,
    agent         TEXT NOT NULL,
    kind          TEXT NOT NULL CHECK (kind IN ('copia', 'verificacio', 'tancament', 'esmena',
                      'reobertura', 'quarantena', 'esborrat', 'excepcio', 'pas0')),
    project_id    INTEGER REFERENCES projects(project_id),
    drive_id      TEXT,
    relative_path TEXT,
    outcome       TEXT NOT NULL CHECK (outcome IN ('ok', 'error', 'parcial')),
    details_json  TEXT
);
CREATE INDEX IF NOT EXISTS idx_events_project ON events(project_id, utc);
CREATE INDEX IF NOT EXISTS idx_events_drive ON events(drive_id, relative_path);

CREATE TABLE IF NOT EXISTS copies (
    project_id      INTEGER NOT NULL REFERENCES projects(project_id),
    layer           TEXT NOT NULL CHECK (layer IN ('RAW', 'PROJ', 'OUT', 'DOC')),
    drive_id        TEXT NOT NULL,
    verified_utc    TEXT NOT NULL,
    manifest_sha256 TEXT,
    status          TEXT NOT NULL CHECK (status IN ('parcial', 'completa')),
    PRIMARY KEY (project_id, layer, drive_id)
);
"""


def _exec_statements(conn: sqlite3.Connection, sql: str) -> None:
    """Executa un bloc DDL sentència a sentència (executescript faria COMMIT implícit).

    Els triggers contenen ';' interns: es parteix per ';' només fora de BEGIN...END.
    """
    buf: list[str] = []
    depth = 0
    for part in sql.split(";"):
        buf.append(part)
        upper = part.upper()
        depth += upper.count(" BEGIN") + upper.count("\nBEGIN")
        depth -= upper.count("END\n") + upper.count("END ") + (1 if upper.rstrip().endswith("END") else 0)
        if depth <= 0:
            stmt = ";".join(buf).strip()
            buf = []
            depth = 0
            if stmt:
                conn.execute(stmt)


def _add_missing_columns(
    conn: sqlite3.Connection, table: str, columns: list[tuple[str, str]]
) -> list[str]:
    """ALTER TABLE ADD COLUMN per a cada columna que falti (no reescriu la taula)."""
    existing = table_columns(conn, table)
    added = []
    for name, ddl in columns:
        if name not in existing:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}")
            added.append(name)
    return added


def _add_missing_inventory_columns(conn: sqlite3.Connection) -> list[str]:
    return _add_missing_columns(conn, "inventory", _INVENTORY_V2_COLUMNS)


def _apply_v7(conn: sqlite3.Connection) -> list[str]:
    """Columnes i taula de la v7 (idempotent). Retorna les columnes afegides."""
    added: list[str] = []
    for table, columns in _V7_COLUMNS.items():
        added += [f"{table}.{c}" for c in _add_missing_columns(conn, table, columns)]
    _exec_statements(conn, _DDL_V7)
    return added


def _apply_v8(conn: sqlite3.Connection) -> list[str]:
    """Columnes de la v8 (idempotent). Retorna les columnes afegides."""
    added: list[str] = []
    for table, columns in _V8_COLUMNS.items():
        added += [f"{table}.{c}" for c in _add_missing_columns(conn, table, columns)]
    return added


def _apply_v9(conn: sqlite3.Connection) -> list[str]:
    """Columna i taules de la v9 (idempotent). Retorna les columnes afegides."""
    added: list[str] = []
    for table, columns in _V9_COLUMNS.items():
        added += [f"{table}.{c}" for c in _add_missing_columns(conn, table, columns)]
    _exec_statements(conn, _DDL_V9)
    return added


def set_pragmas_for_writes(conn: sqlite3.Connection) -> None:
    """Pragmes per a escriptures massives (escaneig / hash). Cal fora de transacció."""
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = NORMAL")
    conn.execute("PRAGMA temp_store = MEMORY")
    conn.execute("PRAGMA cache_size = -262144")  # 256 MB


def init_schema(conn: sqlite3.Connection) -> None:
    """Crea l'esquema complet (DB nova) o completa'l si falta alguna cosa. Idempotent.

    Per a una DB antiga amb dades (user_version < SCHEMA_VERSION) NO fa la conversió de
    dades: cal executar migrate(); aquí només es creen taules/columnes que faltin.
    """
    _exec_statements(conn, _DDL_BASE)
    _exec_statements(conn, _DDL_V2_TABLES)
    _add_missing_inventory_columns(conn)
    legacy = is_legacy_db(conn) and conn.execute("SELECT 1 FROM inventory LIMIT 1").fetchone()
    if not legacy:
        # DB nova (o buida): índexs, FTS i versió directament
        _exec_statements(conn, _DDL_V2_INDEXES)
        _exec_statements(conn, _DDL_V3_FTS)
        _exec_statements(conn, _DDL_V4_INDEXES)
        _exec_statements(conn, _DDL_V5_INDEXES)
        _exec_statements(conn, _DDL_V6)
        _apply_v7(conn)
        _apply_v8(conn)
        _apply_v9(conn)
        conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
    conn.commit()


# ---------------------------------------------------------------------------
# Migracions
# ---------------------------------------------------------------------------

def migrate(
    conn: sqlite3.Connection,
    legacy_roots: dict[str, str] | None = None,
    legacy_dates: dict[str, str] | None = None,
    log: Callable[[str], None] = print,
) -> bool:
    """Porta la DB a SCHEMA_VERSION pas a pas. Retorna True si s'ha fet alguna migració.

    legacy_roots: drive_id -> arrel usada a l'escaneig antic (p. ex. 'F:\\\\'). Si falta,
                  es dedueix de folder_stats. legacy_dates: drive_id -> data ISO (opcional).
    """
    version = get_user_version(conn)
    if version >= SCHEMA_VERSION:
        log(f"La DB ja és a la versió {version}; res a fer.")
        return False
    if not has_table(conn, "inventory"):
        init_schema(conn)
        log("DB buida: esquema creat directament a la versió actual.")
        return True
    conn.execute("PRAGMA foreign_keys = OFF")
    if version < 2:
        _migrate_to_v2(conn, dict(legacy_roots or {}), dict(legacy_dates or {}), log)
    if get_user_version(conn) < 3:
        _migrate_to_v3(conn, log)
    if get_user_version(conn) < 4:
        _migrate_to_v4(conn, log)
    if get_user_version(conn) < 5:
        _migrate_to_v5(conn, log)
    if get_user_version(conn) < 6:
        _migrate_to_v6(conn, log)
    if get_user_version(conn) < 7:
        _migrate_to_v7(conn, log)
    if get_user_version(conn) < 8:
        _migrate_to_v8(conn, log)
    if get_user_version(conn) < 9:
        _migrate_to_v9(conn, log)
    log("  ANALYZE…")
    conn.execute("ANALYZE")
    conn.commit()
    log(f"Migració completada (versió {get_user_version(conn)}).")
    return True


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


def _migrate_to_v2(
    conn: sqlite3.Connection,
    legacy_roots: dict[str, str],
    legacy_dates: dict[str, str],
    log: Callable[[str], None],
) -> None:
    now = utc_now_iso()
    log("Migrant a la versió 2 (drives, scans, hash, seguiment)…")
    conn.execute("BEGIN")
    try:
        _exec_statements(conn, _DDL_BASE)  # per si falta algun índex antic
        _exec_statements(conn, _DDL_V2_TABLES)
        added = _add_missing_inventory_columns(conn)
        log(f"  columnes afegides a inventory: {', '.join(added) or '(cap)'}")

        n = conn.execute(
            "UPDATE inventory SET extension = ? WHERE extension = ''", (NO_EXT,)
        ).rowcount
        log(f"  extensions buides -> {NO_EXT}: {n} fitxers")
        conn.execute(
            """
            INSERT INTO by_extension (drive_id, extension, count, total_bytes)
            SELECT drive_id, ?, SUM(count), SUM(total_bytes)
            FROM by_extension WHERE extension IN ('', ?) GROUP BY drive_id
            """,
            (NO_EXT, NO_EXT),
        )
        conn.execute(
            """
            DELETE FROM by_extension WHERE extension IN ('', ?) AND id NOT IN (
                SELECT MAX(id) FROM by_extension WHERE extension = ? GROUP BY drive_id)
            """,
            (NO_EXT, NO_EXT),
        )

        _exec_statements(conn, _DDL_V2_INDEXES)
        log("  índexs creats (únic drive_id+relative_path, hash, mida)")

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

        conn.execute("PRAGMA user_version = 2")
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise


def _migrate_to_v3(conn: sqlite3.Connection, log: Callable[[str], None]) -> None:
    log("Migrant a la versió 3 (índex de text FTS5 trigram)… pot tardar uns minuts")
    conn.execute("BEGIN")
    try:
        _exec_statements(conn, _DDL_V3_FTS)
        conn.execute(f"INSERT INTO {FTS_TABLE}({FTS_TABLE}) VALUES ('rebuild')")
        conn.execute("PRAGMA user_version = 3")
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    log("  índex FTS construït")


def _migrate_to_v4(conn: sqlite3.Connection, log: Callable[[str], None]) -> None:
    log("Migrant a la versió 4 (índex cobridor per a duplicats)…")
    conn.execute("BEGIN")
    try:
        _exec_statements(conn, _DDL_V4_INDEXES)
        conn.execute("PRAGMA user_version = 4")
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    log("  índex idx_inventory_name_size creat (idx_inventory_name eliminat)")


def fill_missing_folders(conn: sqlite3.Connection, drive_id: str | None = None) -> int:
    """Afegeix a folder_stats les carpetes intermèdies absents (sense fitxers directes).

    Les carpetes es dedueixen dels prefixos de les rutes ja presents a folder_stats.
    Retorna el nombre de files inserides. Cal cridar-ho dins d'una transacció.
    """
    params: tuple = (drive_id,) if drive_id else ()
    where = "WHERE drive_id = ?" if drive_id else ""
    rows = conn.execute(f"SELECT drive_id, folder_path FROM folder_stats {where}", params).fetchall()
    have = {(d, p) for d, p in rows}
    missing: set[tuple[str, str]] = set()
    for d, p in rows:
        while True:
            i = p.rfind("\\")
            if i < 0:
                if p and (d, "") not in have:
                    missing.add((d, ""))
                break
            p = p[:i]
            if (d, p) in have or (d, p) in missing:
                break
            missing.add((d, p))
    if missing:
        conn.executemany(
            "INSERT INTO folder_stats (drive_id, folder_path, folder_name, folder_depth, "
            "files_count, total_bytes) VALUES (?, ?, ?, ?, 0, 0)",
            [(d, p, p.rsplit("\\", 1)[-1] if p else "", p.count("\\") + 1 if p else 0)
             for d, p in missing],
        )
    return len(missing)


def _migrate_to_v5(conn: sqlite3.Connection, log: Callable[[str], None]) -> None:
    log("Migrant a la versió 5 (carpetes intermèdies, índexs d'explorador i de hash)…")
    conn.execute("BEGIN")
    try:
        n = fill_missing_folders(conn)
        log(f"  carpetes intermèdies afegides a folder_stats: {n}")
        _exec_statements(conn, _DDL_V5_INDEXES)
        conn.execute("PRAGMA user_version = 5")
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    log("  índexs creats")


def _migrate_to_v6(conn: sqlite3.Connection, log: Callable[[str], None]) -> None:
    log("Migrant a la versió 6 (canvis per escaneig, índex de hash per disc, espais virtuals)…")
    conn.execute("BEGIN")
    try:
        _exec_statements(conn, _DDL_V6)
        conn.execute("PRAGMA user_version = 6")
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    log("  taules scan_changes, spaces, items, space_members, tags, item_tags i índex "
        "idx_inventory_drive_hash creats")


def _migrate_to_v7(conn: sqlite3.Connection, log: Callable[[str], None]) -> None:
    log("Migrant a la versió 7 (identitat física del disc, data de creació i atributs, "
        "errors de lectura)…")
    conn.execute("BEGIN")
    try:
        added = _apply_v7(conn)
        conn.execute("PRAGMA user_version = 7")
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    log(f"  columnes afegides: {', '.join(added) or 'cap (ja hi eren)'}; taula read_errors creada")


def _migrate_to_v8(conn: sqlite3.Connection, log: Callable[[str], None]) -> None:
    log("Migrant a la versió 8 (exclusions per disc)…")
    conn.execute("BEGIN")
    try:
        added = _apply_v8(conn)
        conn.execute("PRAGMA user_version = 8")
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    log(f"  columnes afegides: {', '.join(added) or 'cap (ja hi eren)'}")


def _migrate_to_v9(conn: sqlite3.Connection, log: Callable[[str], None]) -> None:
    log("Migrant a la versió 9 (taules de migració a l'arxiu i inventory.analysis_class)…")
    conn.execute("BEGIN")
    try:
        added = _apply_v9(conn)
        conn.execute("PRAGMA user_version = 9")
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    log(f"  columnes afegides: {', '.join(added) or 'cap (ja hi eren)'}; taules series, projects, "
        "candidates, candidate_review, project_sources, project_refs, psx_check, events i copies")


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
