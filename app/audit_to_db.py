"""
Escaneig d'un disc o carpeta directament a la base de dades (sense CSV intermedis).

Pensat per a discs grans (milions de fitxers, TB de dades) i per a re-escaneigs:

  * Recorregut amb os.scandir (una sola crida de sistema per entrada; a Windows la
    mida i la data venen amb el llistat del directori, sense stat addicional).
  * Les files es bolquen a una taula temporal i la fusió amb `inventory` es fa en SQL
    per conjunts (no fila a fila des de Python).
  * Mode 'update' (per defecte): fitxers nous s'afegeixen, modificats s'actualitzen
    (el hash s'invalida només si canvia mida o data), absents es marquen amb
    missing_since_utc. Els hashes dels fitxers no modificats es conserven.
  * Mode 'replace': esborra tot el drive_id i torna a inserir (perd els hashes).
  * Cada escaneig queda registrat a `scans` i actualitza els comptadors de `drives`.
"""
from __future__ import annotations

import csv
import os
import sqlite3
import stat as statmod
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from app import schema
from app.disk_info import get_physical_disk_info, identity_serial, suggest_kind
from app.schema import NO_EXT, init_schema, utc_now_iso  # noqa: F401  (re-export)
from app.volume_info import get_volume_info

TOOL_VERSION = "discaleg-scan/3.0"
# Carpetes de sistema a l'arrel del volum que no formen part de l'arxiu (comparació en majúscules)
EXCLUDE_ROOT_DIRS = frozenset({"$RECYCLE.BIN", "SYSTEM VOLUME INFORMATION"})
# Guarda contra arrel/disc equivocat en mode update: si el disc tenia >= GUARD_MIN_FILES fitxers
# i menys del GUARD_MIN_MATCH apareixen al nou recorregut, s'atura sense tocar res.
GUARD_MIN_FILES = 1000
GUARD_MIN_MATCH = 0.10
CHUNK_SIZE = 20_000  # files a la taula temporal per executemany

ProgressFn = Callable[[int, str], None]  # (fitxers_llegits, directori_actual)
ErrorFn = Callable[[str, str, OSError], None]  # (fase, ruta_relativa, error)


@dataclass
class ScanResult:
    scan_id: int
    drive_id: str
    mode: str
    files_count: int
    total_bytes: int
    folders_count: int
    extensions_count: int
    files_new: int
    files_changed: int
    files_missing: int
    seconds: float
    errors_count: int = 0

    def message(self) -> str:
        n = _fmt_int
        base = (f"{self.drive_id}: {n(self.files_count)} fitxers, {n(self.folders_count)} carpetes, "
                f"{self.extensions_count} extensions en {self.seconds:.0f} s")
        if self.mode == "update":
            base += (f" (nous {n(self.files_new)}, modificats {n(self.files_changed)}, "
                     f"desapareguts {n(self.files_missing)})")
        if self.errors_count:
            base += (f". {n(self.errors_count)} carpetes o fitxers no s'han pogut llegir "
                     f"(vegeu «Errors» a la fitxa del disc)")
        return base


def error_code(e: OSError) -> int | None:
    """Codi de l'error: winerror a Windows (p. ex. 5 = accés denegat), si no errno."""
    return getattr(e, "winerror", None) or e.errno


def error_text(e: OSError) -> str:
    """Text curt de l'error (sense la ruta, que ja es guarda a part)."""
    text = e.strerror or str(e) or type(e).__name__
    return text if len(text) <= 300 else text[:299] + "…"


def _fmt_int(n: int) -> str:
    """Separador de milers amb punt (1.234.567)."""
    return f"{n:,}".replace(",", ".")


def _utc_iso(ts: float) -> str:
    """Timestamp -> ISO UTC (mateix format que l'escaneig original, per comparar mtime)."""
    try:
        return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()
    except (OSError, ValueError, OverflowError):
        return ""


def long_path(path: str) -> str:
    """Ruta absoluta amb el prefix \\\\?\\ (o \\\\?\\UNC\\ per a rutes de xarxa) a Windows.

    Permet recórrer i obrir rutes de més de 260 caràcters sense dependre de la política
    LongPathsEnabled. Només per a crides al sistema: el que es guarda a la DB (root_path,
    last_root_path, relative_path) continua sense prefix.
    """
    if os.name != "nt":
        return path
    p = os.path.abspath(path)
    if p.startswith("\\\\?\\"):
        return p
    if p.startswith("\\\\"):
        return "\\\\?\\UNC\\" + p[2:]
    return "\\\\?\\" + p


def _extension(name: str) -> str:
    i = name.rfind(".")
    if 0 < i < len(name) - 1:
        return name[i:].lower()
    return NO_EXT


def _walk(root: str, progress: ProgressFn | None, on_error: ErrorFn | None = None):
    """Itera (rel_dir, name, size, mtime) per a tots els fitxers regulars sota root.

    Per a cada directori (també els buits) emet abans una entrada amb name=None, perquè
    folder_stats contingui totes les carpetes. Iteratiu (sense recursió), no segueix
    enllaços simbòlics ni junctions, i continua si un directori o una entrada no es pot
    llegir: ho comunica a on_error (fase 'scandir' o 'entry') i segueix.
    """
    sep = os.sep
    stack: list[tuple[str, str]] = [(root, "")]
    count = 0
    while stack:
        abs_dir, rel_dir = stack.pop()
        try:
            it = os.scandir(abs_dir)
        except OSError as e:
            if on_error:
                on_error("scandir", rel_dir, e)
            continue
        yield rel_dir, None, 0, 0.0
        with it:
            for entry in it:
                try:
                    if entry.is_symlink() or (os.name == "nt" and entry.is_junction()):
                        continue
                    if entry.is_dir(follow_symlinks=False):
                        if not rel_dir and entry.name.upper() in EXCLUDE_ROOT_DIRS:
                            continue
                        stack.append((entry.path, (rel_dir + sep + entry.name) if rel_dir else entry.name))
                        continue
                    st = entry.stat(follow_symlinks=False)
                except OSError as e:
                    if on_error:
                        on_error("entry", (rel_dir + sep + entry.name) if rel_dir else entry.name, e)
                    continue
                if not statmod.S_ISREG(st.st_mode):
                    continue
                count += 1
                if progress and count % 5000 == 0:
                    progress(count, rel_dir)
                yield rel_dir, entry.name, st.st_size, st.st_mtime


def scan_drive(
    conn: sqlite3.Connection,
    root_path: Path | str,
    drive_id: str,
    mode: str = "update",
    progress: ProgressFn | None = None,
    notes: str | None = None,
    force_volume: bool = False,
) -> ScanResult:
    """Escaneja root_path i fusiona el resultat a la DB sota drive_id.

    Si el disc ja té un volume_serial registrat i no coincideix amb el del volum muntat
    a root_path, s'atura (probable disc equivocat) llevat que force_volume sigui True.
    """
    import time

    t0 = time.time()
    root = str(Path(root_path).resolve())
    if not os.path.isdir(root):
        raise NotADirectoryError(f"No és un directori: {root}")
    if mode not in ("update", "replace"):
        raise ValueError("mode ha de ser 'update' o 'replace'")
    drive_id = drive_id.strip()
    if not drive_id:
        raise ValueError("Cal un drive_id")

    schema.set_pragmas_for_writes(conn)
    init_schema(conn)
    if schema.is_legacy_db(conn):
        raise RuntimeError(
            "La base de dades té l'esquema antic. Executa primer: python -m app.migrate"
        )

    now = utc_now_iso()
    vol = get_volume_info(Path(root))
    hw = get_physical_disk_info(Path(root))  # tot None si no es pot (no Windows, UNC, error)
    hw_serial = identity_serial(hw)  # None si la sèrie és buida o genèrica (carcasses USB)
    prev = conn.execute(
        "SELECT volume_serial, last_root_path, serial_number FROM drives WHERE drive_id = ?",
        (drive_id,),
    ).fetchone()
    if (prev and prev[0] and vol["volume_serial"] and prev[0] != vol["volume_serial"]
            and not force_volume):
        raise RuntimeError(
            f"El volum muntat a {root} té el serial {vol['volume_serial']}, però «{drive_id}» "
            f"es va escanejar amb el serial {prev[0]} (arrel {prev[1]}). Si és el mateix disc "
            f"reformatat o un disc nou amb aquest identificador, marca «Reiniciar el disc» "
            f"(o --force al CLI)."
        )
    if prev and prev[2] and hw_serial and prev[2] != hw_serial and not force_volume:
        raise RuntimeError(
            f"El disc físic muntat a {root} té el número de sèrie {hw_serial} "
            f"({hw['model'] or 'model desconegut'}), però «{drive_id}» es va escanejar amb el "
            f"número de sèrie {prev[2]} (arrel {prev[1]}). Si realment és un disc nou amb aquest "
            f"identificador, marca «Reiniciar el disc» (o --force al CLI)."
        )

    # --- drives + scans (commit immediat: si l'escaneig peta, queda 'running' per detectar-ho)
    conn.execute(
        "INSERT OR IGNORE INTO drives (drive_id, created_utc) VALUES (?, ?)", (drive_id, now)
    )
    # Identitat: amb force/replace el maquinari detectat substitueix el registrat (disc nou);
    # si no, només s'omplen els camps buits. Espai lliure i salut es refresquen sempre.
    override = force_volume or mode == "replace"
    conn.execute(
        """
        UPDATE drives SET label = COALESCE(label, ?), filesystem = COALESCE(?, filesystem),
               volume_serial = CASE WHEN ? THEN COALESCE(?, volume_serial)
                                    ELSE COALESCE(volume_serial, ?) END,
               serial_number = CASE WHEN ? THEN COALESCE(?, serial_number)
                                    ELSE COALESCE(serial_number, ?) END,
               model = CASE WHEN ? THEN COALESCE(?, model) ELSE COALESCE(model, ?) END,
               vendor = CASE WHEN ? THEN COALESCE(?, vendor) ELSE COALESCE(vendor, ?) END,
               interface = CASE WHEN ? THEN COALESCE(?, interface) ELSE COALESCE(interface, ?) END,
               kind = COALESCE(kind, ?),
               capacity_bytes = COALESCE(?, capacity_bytes),
               physical_size_bytes = COALESCE(?, physical_size_bytes),
               free_bytes = COALESCE(?, free_bytes),
               health_status = COALESCE(?, health_status),
               updated_utc = ?
        WHERE drive_id = ?
        """,
        (vol["label"], vol["filesystem"],
         override, vol["volume_serial"], vol["volume_serial"],
         override, hw_serial, hw_serial,
         override, hw["model"], hw["model"],
         override, hw["vendor"], hw["vendor"],
         override, hw["interface"], hw["interface"],
         suggest_kind(hw),
         vol["capacity_bytes"], hw["physical_size_bytes"], vol["free_bytes"], hw["health_status"],
         now, drive_id),
    )
    # escaneigs anteriors d'aquest disc que van quedar «running» (procés tallat): tancar-los
    conn.execute(
        "UPDATE scans SET status = 'aborted', finished_utc = ?, notes = COALESCE(notes || ' | ', '') "
        "|| 'tancat en iniciar un escaneig nou' WHERE drive_id = ? AND status = 'running'",
        (now, drive_id),
    )
    cur = conn.execute(
        """
        INSERT INTO scans (drive_id, root_path, mode, started_utc, status, tool_version, notes,
                           serial_number, model, interface, free_bytes, health_status)
        VALUES (?, ?, ?, ?, 'running', ?, ?, ?, ?, ?, ?, ?)
        """,
        (drive_id, root, mode, now, TOOL_VERSION, notes,
         hw["serial_number"], hw["model"], hw["interface"], vol["free_bytes"], hw["health_status"]),
    )
    scan_id = int(cur.lastrowid)
    conn.commit()

    # --- taula temporal amb el llistat nou
    conn.execute("DROP TABLE IF EXISTS temp.scan_files")
    conn.execute(
        """
        CREATE TEMP TABLE scan_files (
            relative_path TEXT PRIMARY KEY,
            name TEXT NOT NULL, extension TEXT NOT NULL,
            size_bytes INTEGER NOT NULL, modified_utc TEXT
        ) WITHOUT ROWID
        """
    )
    ext_stats: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    folder_agg: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    chunk: list[tuple] = []
    files_count = 0
    total_bytes = 0
    sep = os.sep
    errors: list[tuple[str, str, int | None, str]] = []  # (fase, ruta relativa, codi, text)

    def flush() -> None:
        if chunk:
            conn.executemany(
                "INSERT OR REPLACE INTO temp.scan_files VALUES (?, ?, ?, ?, ?)", chunk
            )
            chunk.clear()

    def on_error(phase: str, rel: str, e: OSError) -> None:
        errors.append((phase, rel, error_code(e), error_text(e)))

    try:
        for rel_dir, name, size, mtime in _walk(long_path(root), progress, on_error):
            if name is None:
                folder_agg[rel_dir]  # registra la carpeta encara que no tingui fitxers
                continue
            rel = (rel_dir + sep + name) if rel_dir else name
            ext = _extension(name)
            chunk.append((rel, name, ext, size, _utc_iso(mtime)))
            files_count += 1
            total_bytes += size
            e = ext_stats[ext]; e[0] += 1; e[1] += size
            f = folder_agg[rel_dir]; f[0] += 1; f[1] += size
            if len(chunk) >= CHUNK_SIZE:
                flush()
        flush()
        conn.commit()  # tanca la transacció implícita de les insercions a la taula temporal

        # --- carpetes i entrades il·legibles: queden lligades a l'escaneig encara que la
        #     fusió posterior s'aturi (guarda) o falli
        if errors:
            conn.executemany(
                """
                INSERT INTO read_errors (drive_id, scan_id, phase, relative_path, error_code,
                                         message, created_utc)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                ((drive_id, scan_id, phase, rel, code, text, now)
                 for phase, rel, code, text in errors),
            )
            conn.execute("UPDATE scans SET errors_count = ? WHERE scan_id = ?", (len(errors), scan_id))
            conn.commit()

        # --- guarda: arrel o disc equivocat? (update sense --force)
        if mode == "update" and not force_volume:
            prev_present = conn.execute(
                "SELECT COUNT(*) FROM inventory WHERE drive_id = ? AND missing_since_utc IS NULL",
                (drive_id,),
            ).fetchone()[0]
            if prev_present >= GUARD_MIN_FILES:
                matched = conn.execute(
                    """
                    SELECT COUNT(*) FROM temp.scan_files s
                    WHERE EXISTS (SELECT 1 FROM inventory i
                                  WHERE i.drive_id = ? AND i.relative_path = s.relative_path)
                    """,
                    (drive_id,),
                ).fetchone()[0]
                if matched < GUARD_MIN_MATCH * prev_present:
                    raise RuntimeError(
                        f"Només {_fmt_int(matched)} dels {_fmt_int(prev_present)} fitxers coneguts de "
                        f"«{drive_id}» apareixen sota {root}. Probablement no és el mateix disc, o l'arrel "
                        f"no és la mateixa que a l'escaneig anterior ({prev[1] if prev else '?'}). "
                        f"No s'ha modificat res. Revisa l'arrel, o marca «Reiniciar el disc» "
                        f"(--force al CLI) si realment vols substituir l'inventari."
                    )

        # --- fusió amb inventory (una sola transacció)
        conn.execute("BEGIN")
        files_new = files_changed = files_missing = 0
        if mode == "replace":
            conn.execute("DELETE FROM inventory WHERE drive_id = ?", (drive_id,))
            cur = conn.execute(
                """
                INSERT INTO inventory (drive_id, relative_path, name, extension, size_bytes,
                                       modified_utc, first_seen_scan, last_seen_scan)
                SELECT ?, relative_path, name, extension, size_bytes, modified_utc, ?, ?
                FROM temp.scan_files
                """,
                (drive_id, scan_id, scan_id),
            )
            files_new = cur.rowcount
        else:
            # Detall per fitxer de l'escaneig (scan_changes), per conjunts i abans de tocar
            # inventory: a) desapareguts, b) modificats (mida o data), d) nous.
            # a) absents -> missing_since_utc (només els que encara no ho estaven)
            cur = conn.execute(
                """
                INSERT INTO scan_changes (scan_id, drive_id, relative_path, change,
                                          old_size, old_mtime)
                SELECT ?, drive_id, relative_path, 'missing', size_bytes, modified_utc
                FROM inventory
                WHERE drive_id = ? AND missing_since_utc IS NULL
                  AND NOT EXISTS (SELECT 1 FROM temp.scan_files s
                                  WHERE s.relative_path = inventory.relative_path)
                """,
                (scan_id, drive_id),
            )
            files_missing = cur.rowcount
            conn.execute(
                """
                UPDATE inventory SET missing_since_utc = ?
                WHERE drive_id = ? AND missing_since_utc IS NULL
                  AND NOT EXISTS (SELECT 1 FROM temp.scan_files s
                                  WHERE s.relative_path = inventory.relative_path)
                """,
                (now, drive_id),
            )
            # b) modificats (mida o data) abans d'actualitzar-los; inclou els que
            #    reapareixen després d'haver estat marcats com a desapareguts
            cur = conn.execute(
                """
                INSERT INTO scan_changes (scan_id, drive_id, relative_path, change,
                                          old_size, new_size, old_mtime, new_mtime)
                SELECT ?, i.drive_id, i.relative_path, 'changed',
                       i.size_bytes, s.size_bytes, i.modified_utc, s.modified_utc
                FROM inventory i JOIN temp.scan_files s ON s.relative_path = i.relative_path
                WHERE i.drive_id = ? AND (i.size_bytes <> s.size_bytes
                                          OR substr(i.modified_utc, 1, 19) IS NOT substr(s.modified_utc, 1, 19))
                """,
                (scan_id, drive_id),
            )
            files_changed = cur.rowcount
            # c) existents: actualitzar metadades; conservar hash si mida+data iguals
            conn.execute(
                """
                UPDATE inventory SET
                    hash_sha256 = CASE WHEN inventory.size_bytes = s.size_bytes
                                        AND substr(inventory.modified_utc, 1, 19) IS substr(s.modified_utc, 1, 19)
                                       THEN inventory.hash_sha256 END,
                    hashed_utc = CASE WHEN inventory.size_bytes = s.size_bytes
                                       AND substr(inventory.modified_utc, 1, 19) IS substr(s.modified_utc, 1, 19)
                                      THEN inventory.hashed_utc END,
                    hash_size_bytes = CASE WHEN inventory.size_bytes = s.size_bytes
                                            AND substr(inventory.modified_utc, 1, 19) IS substr(s.modified_utc, 1, 19)
                                           THEN inventory.hash_size_bytes END,
                    hash_mtime_utc = CASE WHEN inventory.size_bytes = s.size_bytes
                                           AND substr(inventory.modified_utc, 1, 19) IS substr(s.modified_utc, 1, 19)
                                          THEN inventory.hash_mtime_utc END,
                    size_bytes = s.size_bytes, modified_utc = s.modified_utc,
                    last_seen_scan = ?, missing_since_utc = NULL
                FROM temp.scan_files s
                WHERE inventory.drive_id = ? AND inventory.relative_path = s.relative_path
                """,
                (scan_id, drive_id),
            )
            # d) nous (primer el registre a scan_changes, mentre encara no són a inventory)
            cur = conn.execute(
                """
                INSERT INTO scan_changes (scan_id, drive_id, relative_path, change,
                                          new_size, new_mtime)
                SELECT ?, ?, s.relative_path, 'new', s.size_bytes, s.modified_utc
                FROM temp.scan_files s
                WHERE NOT EXISTS (SELECT 1 FROM inventory i
                                  WHERE i.drive_id = ? AND i.relative_path = s.relative_path)
                """,
                (scan_id, drive_id, drive_id),
            )
            files_new = cur.rowcount
            conn.execute(
                """
                INSERT INTO inventory (drive_id, relative_path, name, extension, size_bytes,
                                       modified_utc, first_seen_scan, last_seen_scan)
                SELECT ?, s.relative_path, s.name, s.extension, s.size_bytes, s.modified_utc, ?, ?
                FROM temp.scan_files s
                WHERE NOT EXISTS (SELECT 1 FROM inventory i
                                  WHERE i.drive_id = ? AND i.relative_path = s.relative_path)
                """,
                (drive_id, scan_id, scan_id, drive_id),
            )

        # --- agregats (es recalculen sempre amb l'estat actual del disc)
        conn.execute("DELETE FROM folder_stats WHERE drive_id = ?", (drive_id,))
        conn.executemany(
            """
            INSERT INTO folder_stats (drive_id, folder_path, folder_name, folder_depth,
                                      files_count, total_bytes) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                (drive_id, fp, fp.rsplit(sep, 1)[-1] if fp else "",
                 fp.count(sep) + 1 if fp else 0, v[0], v[1])
                for fp, v in folder_agg.items()
            ),
        )
        conn.execute("DELETE FROM by_extension WHERE drive_id = ?", (drive_id,))
        conn.executemany(
            "INSERT INTO by_extension (drive_id, extension, count, total_bytes) VALUES (?, ?, ?, ?)",
            ((drive_id, ext, v[0], v[1]) for ext, v in ext_stats.items()),
        )

        finished = utc_now_iso()
        conn.execute(
            """
            UPDATE scans SET finished_utc = ?, status = 'done', files_count = ?, total_bytes = ?,
                   files_new = ?, files_changed = ?, files_missing = ?, errors_count = ?
            WHERE scan_id = ?
            """,
            (finished, files_count, total_bytes, files_new, files_changed, files_missing,
             len(errors), scan_id),
        )
        schema.refresh_drive_counters(conn, drive_id, root_path=root, scan_utc=finished)
        conn.execute("COMMIT")
    except BaseException:
        try:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
        finally:
            conn.execute(
                "UPDATE scans SET status = 'aborted', finished_utc = ? WHERE scan_id = ?",
                (utc_now_iso(), scan_id),
            )
            conn.commit()
        raise
    finally:
        conn.execute("DROP TABLE IF EXISTS temp.scan_files")

    return ScanResult(
        scan_id=scan_id, drive_id=drive_id, mode=mode, files_count=files_count,
        total_bytes=total_bytes, folders_count=len(folder_agg), extensions_count=len(ext_stats),
        files_new=files_new, files_changed=files_changed, files_missing=files_missing,
        seconds=time.time() - t0, errors_count=len(errors),
    )


def audit_direct_to_db(
    conn: sqlite3.Connection,
    root_path: Path,
    drive_id: str,
    replace: bool,
) -> tuple[int, int, int]:
    """Compatibilitat amb la crida antiga. Retorna (fitxers, carpetes, extensions)."""
    r = scan_drive(conn, root_path, drive_id, mode="replace" if replace else "update")
    return r.files_count, r.folders_count, r.extensions_count


def delete_drive(conn: sqlite3.Connection, drive_id: str) -> None:
    """Elimina totes les dades d'un disc (inventari, agregats, escaneigs i fitxa)."""
    for table in ("scan_changes", "read_errors"):
        if schema.has_table(conn, table):
            conn.execute(f"DELETE FROM {table} WHERE drive_id = ?", (drive_id,))
    conn.execute("DELETE FROM inventory WHERE drive_id = ?", (drive_id,))
    conn.execute("DELETE FROM folder_stats WHERE drive_id = ?", (drive_id,))
    conn.execute("DELETE FROM by_extension WHERE drive_id = ?", (drive_id,))
    if schema.has_table(conn, "scans"):
        conn.execute("DELETE FROM scans WHERE drive_id = ?", (drive_id,))
    if schema.has_table(conn, "drives"):
        conn.execute("DELETE FROM drives WHERE drive_id = ?", (drive_id,))
    conn.commit()


def import_from_csv_folder(
    conn: sqlite3.Connection,
    audit_dir: Path,
    drive_id: str,
    replace: bool,
) -> tuple[int, int, int]:
    """Importa una carpeta d'audit en CSV (quick_audit_archive) com un escaneig 'replace'.

    folders_stats.csv porta rutes absolutes: es converteixen a relatives a l'arrel
    indicada a audit_log.txt (línia «ROOT:») si existeix.
    """
    audit_dir = audit_dir.resolve()
    inv_path = audit_dir / "inventory_files.csv"
    if not inv_path.exists():
        raise FileNotFoundError(f"Falta inventory_files.csv a {audit_dir}")
    schema.set_pragmas_for_writes(conn)
    init_schema(conn)
    if schema.is_legacy_db(conn):
        raise RuntimeError("Esquema antic: executa primer python -m app.migrate")

    root = ""
    log = audit_dir / "audit_log.txt"
    if log.is_file():
        for line in log.read_text(encoding="utf-8", errors="replace").splitlines():
            if line.startswith("ROOT:"):
                root = line[5:].strip()
                break
    prefix = (root.rstrip("\\/") + "\\") if root else ""

    now = utc_now_iso()
    conn.execute("INSERT OR IGNORE INTO drives (drive_id, created_utc) VALUES (?, ?)", (drive_id, now))
    cur = conn.execute(
        """
        INSERT INTO scans (drive_id, root_path, mode, started_utc, status, tool_version, notes)
        VALUES (?, ?, 'replace', ?, 'running', 'csv-import', ?)
        """,
        (drive_id, root or str(audit_dir), now, f"Importat de {audit_dir}"),
    )
    scan_id = int(cur.lastrowid)
    conn.commit()

    conn.execute("BEGIN")
    try:
        conn.execute("DELETE FROM inventory WHERE drive_id = ?", (drive_id,))
        conn.execute("DELETE FROM folder_stats WHERE drive_id = ?", (drive_id,))
        conn.execute("DELETE FROM by_extension WHERE drive_id = ?", (drive_id,))

        inv_count = 0
        total_bytes = 0
        with inv_path.open("r", newline="", encoding="utf-8") as f:
            rows: list[tuple] = []
            for row in csv.DictReader(f):
                size = int(row["SizeBytes"])
                rows.append((drive_id, row["RelativePath"], row["Name"],
                             row["Extension"] or NO_EXT, size, row.get("ModifiedUtc", ""),
                             scan_id, scan_id))
                total_bytes += size
                if len(rows) >= CHUNK_SIZE:
                    conn.executemany(_INSERT_INV, rows); inv_count += len(rows); rows.clear()
            if rows:
                conn.executemany(_INSERT_INV, rows); inv_count += len(rows)

        folder_count = 0
        fpath = audit_dir / "folders_stats.csv"
        if fpath.exists():
            with fpath.open("r", newline="", encoding="utf-8") as f:
                rows = []
                for row in csv.DictReader(f):
                    fp = row["FolderPath"]
                    if prefix and fp.upper().startswith(prefix.upper()):
                        fp = fp[len(prefix):]
                    elif root and fp.rstrip("\\/").upper() == root.rstrip("\\/").upper():
                        fp = ""
                    depth_raw = str(row.get("FolderDepth", "")).strip()
                    rows.append((drive_id, fp, fp.rsplit("\\", 1)[-1] if fp else "",
                                 int(depth_raw) if depth_raw.isdigit() else None,
                                 int(row["FilesCount"]), int(row["TotalBytes"])))
            conn.executemany(
                "INSERT INTO folder_stats (drive_id, folder_path, folder_name, folder_depth, "
                "files_count, total_bytes) VALUES (?, ?, ?, ?, ?, ?)", rows)
            folder_count = len(rows) + schema.fill_missing_folders(conn, drive_id)

        ext_count = 0
        epath = audit_dir / "by_extension.csv"
        if epath.exists():
            with epath.open("r", newline="", encoding="utf-8") as f:
                rows = [(drive_id, row["Extension"] or NO_EXT, int(row["Count"]), int(row["TotalBytes"]))
                        for row in csv.DictReader(f)]
            conn.executemany(
                "INSERT INTO by_extension (drive_id, extension, count, total_bytes) VALUES (?, ?, ?, ?)",
                rows)
            ext_count = len(rows)

        finished = utc_now_iso()
        conn.execute(
            "UPDATE scans SET finished_utc = ?, status = 'done', files_count = ?, total_bytes = ?, "
            "files_new = ? WHERE scan_id = ?",
            (finished, inv_count, total_bytes, inv_count, scan_id))
        schema.refresh_drive_counters(conn, drive_id, root_path=root or None, scan_utc=finished)
        conn.execute("COMMIT")
    except BaseException:
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        conn.execute("UPDATE scans SET status = 'aborted', finished_utc = ? WHERE scan_id = ?",
                     (utc_now_iso(), scan_id))
        conn.commit()
        raise
    return inv_count, folder_count, ext_count


_INSERT_INV = (
    "INSERT INTO inventory (drive_id, relative_path, name, extension, size_bytes, modified_utc, "
    "first_seen_scan, last_seen_scan) VALUES (?, ?, ?, ?, ?, ?, ?, ?)"
)
