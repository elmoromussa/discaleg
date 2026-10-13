"""Informes de còpies: fitxers amb còpia única i comparació entre discs.

Dos criteris d'identitat:
  * 'hash'      — contingut (SHA-256): l'objectiu; només compta els fitxers que ja tenen hash
                  i els altres queden com a «no comprovables».
  * 'name-size' — nom i mida: aproximació utilitzable sense hashes.

Dues estratègies, triades per la mida de l'àmbit (disc o carpeta analitzat):
  * àmbit petit (< BIG_SCOPE fitxers): anti-join `NOT EXISTS` fila a fila, amb l'índex exterior
    fixat (`INDEXED BY`) i una sonda per fila sobre un índex cobridor
    (idx_inventory_hash_size o idx_inventory_name_size);
  * àmbit gran: una sola passada per l'índex cobridor de tota la DB agrupant per clau
    (hash, o nom+mida) i quedant-se les claus que només existeixen a l'àmbit
    (`HAVING MIN(drive_id) = MAX(drive_id)`), i després pertinença `IN` per a les files de
    l'àmbit. Independent de la mida del disc: ~1–2 s amb 4,8 M de fitxers.
La comparació A→B usa sempre l'anti-join (la sonda troba de seguida si B té la clau).
Els fitxers de 0 bytes s'ignoren (tots comparteixen contingut). Resums i agrupacions per
carpeta es desen en una memòria cau en procés invalidada per drives.updated_utc (canvia amb
cada escaneig i cada lot de hash).
"""
from __future__ import annotations

import sqlite3
import threading
from typing import Any

from app.queries import folder_range

MODES = ("hash", "name-size")
SCOPES = ("cross", "any")  # còpia única: cap còpia en un altre disc / cap còpia enlloc
BIG_SCOPE = 150_000        # a partir d'aquí, l'estratègia d'una passada per l'índex global

_cache: dict[tuple, Any] = {}
_cache_lock = threading.Lock()
_CACHE_MAX = 300


def _stamp(conn: sqlite3.Connection) -> str:
    row = conn.execute("SELECT MAX(updated_utc), COUNT(*) FROM drives").fetchone()
    return f"{row[0]}|{row[1]}"


def _cached(conn: sqlite3.Connection, key: tuple, compute):
    full = key + (_stamp(conn),)
    with _cache_lock:
        if full in _cache:
            return _cache[full]
    value = compute()
    with _cache_lock:
        if len(_cache) >= _CACHE_MAX:
            _cache.clear()
        _cache[full] = value
    return value


def _to_db(path: str) -> str:
    return (path or "").replace("/", "\\").strip("\\")


def _scope_sql(drive_id: str, path_db: str) -> tuple[str, list[Any]]:
    rng = folder_range(path_db)
    if rng:
        return "a.drive_id = ? AND a.relative_path >= ? AND a.relative_path < ?", [drive_id, rng[0], rng[1]]
    return "a.drive_id = ?", [drive_id]


def _outer_index(mode: str, path_db: str) -> str:
    """Índex del bucle exterior: rang de ruta si hi ha carpeta; si no, el cobridor del criteri."""
    if path_db:
        return "ux_inventory_drive_path"
    return "idx_inventory_drive_hash" if mode == "hash" else "idx_inventory_drive"


def _key_cols(mode: str, alias: str) -> str:
    return f"{alias}.hash_sha256" if mode == "hash" else f"({alias}.name, {alias}.size_bytes)"


def _key_select(mode: str) -> str:
    return "hash_sha256" if mode == "hash" else "name, size_bytes"


def _match_sql(mode: str) -> str:
    if mode == "hash":
        return "b.hash_sha256 = a.hash_sha256"
    return "b.name = a.name AND b.size_bytes = a.size_bytes"


def _base_filter(mode: str, alias: str = "a") -> str:
    f = f"{alias}.missing_since_utc IS NULL AND {alias}.size_bytes > 0"
    if mode == "hash":
        f += f" AND {alias}.hash_sha256 IS NOT NULL"
    return f


def _global_index(mode: str) -> str:
    return "idx_inventory_hash_size" if mode == "hash" else "idx_inventory_name_size"


def _where(
    mode: str, kind: str, drive_id: str, path_db: str, scope: str, to_drive: str | None, big: bool
) -> tuple[str, str, list[Any]]:
    """(FROM amb INDEXED BY, WHERE, paràmetres) dels fitxers «sense còpia» de l'àmbit."""
    scope_sql, params = _scope_sql(drive_id, path_db)
    from_sql = f"inventory a INDEXED BY {_outer_index(mode, path_db)}"
    base = f"{scope_sql} AND {_base_filter(mode)}"
    if kind == "compare":
        cond = (f"NOT EXISTS (SELECT 1 FROM inventory b WHERE {_match_sql(mode)} AND b.drive_id = ? "
                f"AND b.missing_since_utc IS NULL)")
        return from_sql, f"{base} AND {cond}", params + [to_drive]
    if big:
        having = ("COUNT(*) = 1" if scope == "any" else "MIN(g.drive_id) = MAX(g.drive_id)") + " AND MIN(g.drive_id) = ?"
        cond = (f"{_key_cols(mode, 'a')} IN (SELECT {_key_select(mode)} FROM inventory g "
                f"INDEXED BY {_global_index(mode)} WHERE {_base_filter(mode, 'g')} "
                f"GROUP BY {_key_select(mode)} HAVING {having})")
        return from_sql, f"{base} AND {cond}", params + [drive_id]
    other = "b.id <> a.id" if scope == "any" else "b.drive_id <> a.drive_id"
    cond = f"NOT EXISTS (SELECT 1 FROM inventory b WHERE {_match_sql(mode)} AND {other} AND b.missing_since_utc IS NULL)"
    return from_sql, f"{base} AND {cond}", params


def scope_totals(conn: sqlite3.Connection, drive_id: str, path: str) -> dict[str, int]:
    """Fitxers presents de l'àmbit: total, bytes, amb hash, de 0 bytes (per als percentatges)."""
    path_db = _to_db(path)

    def compute():
        scope_sql, params = _scope_sql(drive_id, path_db)
        row = conn.execute(
            f"""
            SELECT COUNT(*), COALESCE(SUM(a.size_bytes), 0),
                   COALESCE(SUM(a.hash_sha256 IS NOT NULL), 0),
                   COALESCE(SUM(CASE WHEN a.hash_sha256 IS NOT NULL THEN a.size_bytes ELSE 0 END), 0),
                   COALESCE(SUM(a.size_bytes = 0), 0)
            FROM inventory a WHERE {scope_sql} AND a.missing_since_utc IS NULL
            """,
            params,
        ).fetchone()
        return {"files": int(row[0]), "bytes": int(row[1]), "hashed": int(row[2]),
                "hashed_bytes": int(row[3]), "empty": int(row[4])}

    return _cached(conn, ("scope", drive_id, path_db), compute)


def _is_big(conn: sqlite3.Connection, mode: str, drive_id: str, path: str) -> bool:
    t = scope_totals(conn, drive_id, path)
    n = t["hashed"] if mode == "hash" else t["files"]
    return n >= BIG_SCOPE


def _annotate(conn: sqlite3.Connection, mode: str, drive_id: str, path: str) -> dict[str, Any]:
    """Totals de l'àmbit i «no comprovables» (sense hash) o ignorats (0 bytes)."""
    t = scope_totals(conn, drive_id, path)
    comparable = t["hashed"] if mode == "hash" else t["files"]
    return {
        "scope_files": t["files"], "scope_bytes": t["bytes"], "empty_files": t["empty"],
        "unverifiable": (t["files"] - t["hashed"]) if mode == "hash" else 0,
        "unverifiable_bytes": (t["bytes"] - t["hashed_bytes"]) if mode == "hash" else 0,
        "comparable": max(0, comparable - t["empty"]),
    }


def grouped_by_folder(
    conn: sqlite3.Connection, mode: str, kind: str, drive_id: str, path: str, scope: str,
    to_drive: str | None, prefix: str = "",
) -> list[dict[str, Any]]:
    """Fitxers sense còpia agrupats pel component de ruta següent a `prefix` (o a l'àmbit).

    Una sola passada pels fitxers de l'àmbit; el resultat (i, per tant, el resum) es desa a la
    memòria cau.
    """
    base_api = prefix or path
    base_db = _to_db(base_api)

    def compute():
        big = kind != "compare" and _is_big(conn, mode, drive_id, base_api)
        from_sql, where, params = _where(mode, kind, drive_id, base_db, scope, to_drive, big)
        start = len(base_db) + 2 if base_db else 1
        rows = conn.execute(
            f"""
            SELECT CASE WHEN instr(rest, '\\') = 0 THEN '' ELSE substr(rest, 1, instr(rest, '\\') - 1) END AS child,
                   COUNT(*) AS files, COALESCE(SUM(size_bytes), 0) AS bytes
            FROM (SELECT substr(a.relative_path, ?) AS rest, a.size_bytes
                  FROM {from_sql} WHERE {where})
            GROUP BY child ORDER BY bytes DESC, files DESC
            """,
            [start] + params,
        ).fetchall()
        out = []
        for child, files, nbytes in rows:
            out.append({
                "name": child, "is_direct": child == "",
                "path": (base_db.replace("\\", "/") + "/" + child).strip("/") if child else base_db.replace("\\", "/"),
                "files": int(files), "bytes": int(nbytes),
            })
        return out

    return _cached(conn, ("grouped", mode, kind, drive_id, base_db, scope, to_drive), compute)


def _summary_from_groups(groups: list[dict[str, Any]]) -> dict[str, int]:
    return {"files": sum(g["files"] for g in groups), "bytes": sum(g["bytes"] for g in groups)}


def unique_summary(
    conn: sqlite3.Connection, mode: str, drive_id: str, path: str = "", scope: str = "cross"
) -> dict[str, Any]:
    """Fitxers de l'àmbit sense cap còpia (en un altre disc, o enlloc): recompte i bytes.

    Surt de l'agrupació per carpeta de primer nivell (mateixa passada, compartida a la cau).
    """
    out = dict(_summary_from_groups(grouped_by_folder(conn, mode, "unique", drive_id, path, scope, None)))
    out.update(_annotate(conn, mode, drive_id, path))
    return out


def compare_summary(
    conn: sqlite3.Connection, mode: str, from_drive: str, to_drive: str, path: str = ""
) -> dict[str, Any]:
    """«Tot el que hi ha a A (o a una carpeta d'A) és també a B?»: el que falta a B."""
    out = dict(_summary_from_groups(grouped_by_folder(conn, mode, "compare", from_drive, path, "cross", to_drive)))
    out.update(_annotate(conn, mode, from_drive, path))
    return out


def list_files(
    conn: sqlite3.Connection, mode: str, kind: str, drive_id: str, path: str, scope: str,
    to_drive: str | None, limit: int, offset: int, prefix: str = "",
) -> list[dict[str, Any]]:
    """Pàgina de fitxers sense còpia, en ordre de ruta (recorregut incremental de l'índex).

    `prefix` (format API) restringeix a una subcarpeta de l'àmbit (per baixar des de l'agrupació).
    """
    base_api = prefix or path
    path_db = _to_db(base_api)
    big = kind != "compare" and _is_big(conn, mode, drive_id, base_api)
    from_sql, where, params = _where(mode, kind, drive_id, path_db, scope, to_drive, big)
    cur = conn.execute(
        f"""
        SELECT a.id, a.drive_id, a.relative_path, a.name, a.extension, a.size_bytes, a.modified_utc,
               a.hash_sha256
        FROM {from_sql} WHERE {where}
        ORDER BY a.relative_path LIMIT ? OFFSET ?
        """,
        params + [limit, offset],
    )
    rows = [dict(r) for r in cur.fetchall()]
    for r in rows:
        rp = r["relative_path"]
        r["folder"] = rp[: rp.rfind("\\")].replace("\\", "/") if "\\" in rp else ""
    return rows


def all_drives_unique(conn: sqlite3.Connection, mode: str, scope: str = "cross") -> list[dict[str, Any]]:
    """Resum de còpia única de tots els discs en una sola passada per l'índex global.

    Agrupa per clau (hash o nom+mida), es queda les claus presents en un sol disc i suma per disc.
    """
    def compute():
        having = "COUNT(*) = 1" if scope == "any" else "MIN(drive_id) = MAX(drive_id)"
        rows = conn.execute(
            f"""
            SELECT d, SUM(n) AS files, SUM(b) AS bytes FROM (
                SELECT MIN(drive_id) AS d, COUNT(*) AS n, SUM(size_bytes) AS b
                FROM inventory INDEXED BY {_global_index(mode)}
                WHERE {_base_filter(mode, 'inventory')}
                GROUP BY {_key_select(mode)} HAVING {having})
            GROUP BY d
            """
        ).fetchall()
        return {r[0]: (int(r[1]), int(r[2])) for r in rows}

    per_drive = _cached(conn, ("overview", mode, scope), compute)
    out = []
    for (d,) in conn.execute("SELECT drive_id FROM drives ORDER BY drive_id"):
        files, nbytes = per_drive.get(d, (0, 0))
        s = {"drive_id": d, "files": files, "bytes": nbytes}
        s.update(_annotate(conn, mode, d, ""))
        out.append(s)
    return out
