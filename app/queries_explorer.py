"""Consultes per a l'explorador simulat (arbre i contingut de carpetes des de la DB).

L'arbre de carpetes surt de `folder_stats` (una fila per carpeta, amb ruta relativa,
nom i profunditat; des de la versió 5 inclou també les carpetes sense fitxers directes).
Els fitxers d'una carpeta surten d'`inventory` amb un recorregut per rang de l'índex únic
(drive_id, relative_path), així que el cost depèn de la carpeta i no de la mida del disc.

A l'API les rutes van amb «/» (com les envia el frontend); a la DB amb «\\».
"""
from __future__ import annotations

import sqlite3
from typing import Any

SEP = "\\"


def _normalize_path(path: str) -> str:
    """Ruta en format API: separador «/», sense barres als extrems."""
    if not path:
        return ""
    return path.replace("\\", "/").strip("/")


def _to_db(path: str) -> str:
    return _normalize_path(path).replace("/", SEP)


def _to_api(path: str) -> str:
    return path.replace(SEP, "/")


def _depth(db_path: str) -> int:
    return 0 if not db_path else db_path.count(SEP) + 1


def _range_hi(prefix: str) -> str:
    """Límit superior (exclusiu) per a `col >= prefix AND col < hi` amb col·lació BINARY."""
    return prefix[:-1] + chr(ord(prefix[-1]) + 1)


def get_explorer_drive_ids(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Discs (arrels de l'arbre), des de la fitxa de discs."""
    cur = conn.execute("SELECT drive_id FROM drives ORDER BY drive_id")
    rows = [{"drive_id": r[0]} for r in cur.fetchall()]
    if rows:
        return rows
    cur = conn.execute("SELECT DISTINCT drive_id FROM inventory ORDER BY drive_id")
    return [{"drive_id": r[0]} for r in cur.fetchall()]


def _folder_direct_files(conn: sqlite3.Connection, drive_id: str, db_path: str) -> int:
    row = conn.execute(
        "SELECT files_count FROM folder_stats WHERE drive_id = ? AND folder_path = ? LIMIT 1",
        (drive_id, db_path),
    ).fetchone()
    return int(row[0]) if row else 0


def _children_folders(
    conn: sqlite3.Connection, drive_id: str, parent_db: str, with_has_children: bool
) -> list[dict[str, Any]]:
    depth = _depth(parent_db) + 1
    prefix = parent_db + SEP if parent_db else ""
    params: list[Any] = [drive_id, depth]
    rng = ""
    if prefix:
        rng = " AND folder_path >= ? AND folder_path < ?"
        params += [prefix, _range_hi(prefix)]
    rows = conn.execute(
        f"""
        SELECT folder_path, folder_name, files_count, total_bytes
        FROM folder_stats WHERE drive_id = ? AND folder_depth = ?{rng}
        ORDER BY folder_name COLLATE NOCASE
        """,
        params,
    ).fetchall()

    with_children: set[str] = set()
    if with_has_children and rows:
        # carpetes netes a profunditat+1 sota el mateix prefix -> quins fills tenen fills
        gparams: list[Any] = [drive_id, depth + 1]
        if prefix:
            gparams += [prefix, _range_hi(prefix)]
        plen = len(prefix)
        for (gp,) in conn.execute(
            f"SELECT folder_path FROM folder_stats WHERE drive_id = ? AND folder_depth = ?{rng}",
            gparams,
        ):
            rest = gp[plen:]
            i = rest.find(SEP)
            with_children.add(prefix + (rest[:i] if i >= 0 else rest))

    out: list[dict[str, Any]] = []
    for fp, fname, files_count, total_bytes in rows:
        out.append(
            {
                "name": fname or fp.rsplit(SEP, 1)[-1],
                "path": _to_api(fp),
                "has_children": (fp in with_children) if with_has_children else True,
                "files_count": int(files_count or 0),
                "total_bytes": int(total_bytes or 0),
            }
        )
    return out


def get_explorer_children(
    conn: sqlite3.Connection, drive_id: str, parent_path: str
) -> dict[str, Any]:
    """Subcarpetes directes de (drive_id, parent_path) i nombre de fitxers directes."""
    parent_db = _to_db(parent_path)
    return {
        "folders": _children_folders(conn, drive_id, parent_db, with_has_children=True),
        "files_count": _folder_direct_files(conn, drive_id, parent_db),
    }


def get_explorer_contents(
    conn: sqlite3.Connection,
    drive_id: str,
    folder_path: str,
    limit: int = 300,
    offset: int = 0,
) -> dict[str, Any]:
    """Contingut d'una carpeta: subcarpetes i fitxers directes (paginats, ordenats per nom)."""
    folder_db = _to_db(folder_path)
    folders = _children_folders(conn, drive_id, folder_db, with_has_children=False)
    prefix = folder_db + SEP if folder_db else ""
    params: list[Any] = [drive_id]
    if prefix:
        # rang de l'índex únic (drive_id, relative_path); fitxer directe = cap separador després del prefix
        where = ("relative_path >= ? AND relative_path < ? "
                 "AND instr(substr(relative_path, ?), ?) = 0")
        params += [prefix, _range_hi(prefix), len(prefix) + 1, SEP]
    else:
        where = "instr(relative_path, ?) = 0"
        params += [SEP]
    cur = conn.execute(
        f"""
        SELECT name, extension, size_bytes, modified_utc
        FROM inventory
        WHERE drive_id = ? AND {where} AND missing_since_utc IS NULL
        ORDER BY relative_path
        LIMIT ? OFFSET ?
        """,
        params + [limit, offset],
    )
    files = [dict(row) for row in cur.fetchall()]
    total_files = _folder_direct_files(conn, drive_id, folder_db)
    if files and offset + len(files) > total_files:
        total_files = offset + len(files)  # folder_stats desactualitzat: confia en el que s'ha llegit
    return {
        "folders": [{"name": f["name"], "path": f["path"]} for f in folders],
        "files": files,
        "total_files": total_files,
        "has_more": (offset + len(files)) < total_files and len(files) == limit,
    }
