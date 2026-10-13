"""Consultes per a l'explorador simulat (arbre i contingut de carpetes des de la DB).

L'arbre de carpetes surt de `folder_stats` (una fila per carpeta, amb ruta relativa,
nom i profunditat; des de la versió 5 inclou també les carpetes sense fitxers directes).
`files_count` i `total_bytes` de `folder_stats` són els fitxers DIRECTES de la carpeta;
els totals recursius es calculen agregant el rang de l'índex (drive_id, folder_path).
Els fitxers d'una carpeta surten d'`inventory` amb un recorregut per rang de l'índex únic
(drive_id, relative_path), així que el cost depèn de la carpeta i no de la mida del disc.

A l'API les rutes van amb «/» (com les envia el frontend); a la DB amb «\\».
"""
from __future__ import annotations

import sqlite3
from typing import Any

from app.schema import DRIVE_KINDS

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


def get_explorer_drives(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Discs (arrels de l'arbre) amb la fitxa resumida, des de `drives`."""
    cur = conn.execute(
        """
        SELECT drive_id, label, kind, status, files_count, total_bytes,
               last_root_path, last_scan_utc, location, notes
        FROM drives ORDER BY drive_id
        """
    )
    rows = [
        {
            "drive_id": r[0],
            "label": r[1] or "",
            "kind": r[2] or "",
            "kind_label": DRIVE_KINDS.get(r[2] or "", ""),
            "status": r[3] or "active",
            "files_count": int(r[4] or 0),
            "total_bytes": int(r[5] or 0),
            "root_path": r[6] or "",
            "last_scan_utc": r[7] or "",
            "location": r[8] or "",
            "notes": r[9] or "",
        }
        for r in cur.fetchall()
    ]
    if rows:
        return rows
    cur = conn.execute("SELECT DISTINCT drive_id FROM inventory ORDER BY drive_id")
    return [
        {"drive_id": r[0], "label": "", "kind": "", "kind_label": "", "status": "active",
         "files_count": 0, "total_bytes": 0, "root_path": "", "last_scan_utc": "",
         "location": "", "notes": ""}
        for r in cur.fetchall()
    ]


# Compatibilitat amb el nom antic
get_explorer_drive_ids = get_explorer_drives


def _folder_direct(conn: sqlite3.Connection, drive_id: str, db_path: str) -> tuple[int, int]:
    """(fitxers directes, bytes directes) de la carpeta segons folder_stats."""
    row = conn.execute(
        "SELECT files_count, total_bytes FROM folder_stats "
        "WHERE drive_id = ? AND folder_path = ? LIMIT 1",
        (drive_id, db_path),
    ).fetchone()
    return (int(row[0]), int(row[1])) if row else (0, 0)


def _folder_direct_files(conn: sqlite3.Connection, drive_id: str, db_path: str) -> int:
    return _folder_direct(conn, drive_id, db_path)[0]


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
    """Contingut d'una carpeta: subcarpetes i fitxers directes (paginats, ordenats per nom).

    Els comptadors de les subcarpetes són els directes (folder_stats); els recursius
    s'obtenen a part amb `get_explorer_folder_totals`.
    """
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
    total_files, direct_bytes = _folder_direct(conn, drive_id, folder_db)
    if files and offset + len(files) > total_files:
        total_files = offset + len(files)  # folder_stats desactualitzat: confia en el que s'ha llegit
    return {
        "folders": [
            {"name": f["name"], "path": f["path"],
             "files_count": f["files_count"], "total_bytes": f["total_bytes"]}
            for f in folders
        ],
        "files": files,
        "total_files": total_files,
        "direct_bytes": direct_bytes,
        "has_more": (offset + len(files)) < total_files and len(files) == limit,
    }


def get_explorer_folder_totals(
    conn: sqlite3.Connection, drive_id: str, folder_path: str
) -> dict[str, Any]:
    """Totals recursius de la carpeta i de cadascuna de les seves subcarpetes directes.

    Una sola passada pel rang de l'índex (drive_id, folder_path) agrupant pel primer
    component de la ruta relativa al prefix. A l'arrel d'un disc gran (~120k carpetes)
    triga ~150 ms; a qualsevol altra carpeta, molt menys.
    """
    folder_db = _to_db(folder_path)
    prefix = folder_db + SEP if folder_db else ""
    params: dict[str, Any] = {"sep": SEP, "start": len(prefix) + 1, "drive": drive_id}
    if prefix:
        rng = "folder_path >= :lo AND folder_path < :hi"
        params.update({"lo": prefix, "hi": _range_hi(prefix)})
    else:
        rng = "folder_path != ''"
    rows = conn.execute(
        f"""
        SELECT CASE WHEN instr(rest, :sep) = 0 THEN rest
                    ELSE substr(rest, 1, instr(rest, :sep) - 1) END AS child,
               SUM(fc), SUM(tb), COUNT(*) - 1
        FROM (SELECT substr(folder_path, :start) AS rest, files_count AS fc, total_bytes AS tb
              FROM folder_stats WHERE drive_id = :drive AND {rng})
        GROUP BY child
        """,
        params,
    ).fetchall()
    children: dict[str, dict[str, int]] = {}
    files_sum = 0
    bytes_sum = 0
    folders_sum = 0
    for child, fc, tb, sub in rows:
        fc = int(fc or 0)
        tb = int(tb or 0)
        sub = int(sub or 0)
        children[_to_api(child)] = {"files": fc, "bytes": tb, "folders": sub}
        files_sum += fc
        bytes_sum += tb
        folders_sum += sub + 1
    direct_files, direct_bytes = _folder_direct(conn, drive_id, folder_db)
    return {
        "subtree": {
            "files": files_sum + direct_files,
            "bytes": bytes_sum + direct_bytes,
            "folders": folders_sum,
        },
        "direct": {"files": direct_files, "bytes": direct_bytes, "folders": len(children)},
        "children": children,
    }
