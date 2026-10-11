"""Consultes per a l'explorador simulat (arbre i contingut de carpetes des de la DB)."""
import sqlite3
from typing import Any


def _normalize_path(path: str) -> str:
    """Normalitza el camí a separador / (per comparacions i respostes)."""
    if not path:
        return ""
    return path.replace("\\", "/").strip("/")


def get_explorer_drive_ids(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Retorna la llista de drive_id (arrels de l'arbre)."""
    cur = conn.execute(
        "SELECT DISTINCT drive_id FROM inventory ORDER BY drive_id"
    )
    return [{"drive_id": row[0]} for row in cur.fetchall()]


def _parent_prefixes(parent_path: str) -> tuple[str, str]:
    """Retorna (prefix_slash, prefix_back) per a LIKE: fills immediats de parent_path."""
    norm = _normalize_path(parent_path)
    if not norm:
        return ("", "")
    return (norm + "/", norm + "\\")


def _direct_children_where(parent_path: str) -> tuple[str, list[Any]]:
    """
    Clàusula WHERE per a fitxers/carpetes "fills directes" de parent_path.
    - Arrel (parent_path buit): tots els relative_path del drive.
    - No arrel: relative_path comença per parent_path + / o \\.
    """
    if not parent_path or not _normalize_path(parent_path):
        return ("drive_id = ? AND missing_since_utc IS NULL", [])
    p_slash, p_back = _parent_prefixes(parent_path)
    return (
        "drive_id = ? AND missing_since_utc IS NULL AND (relative_path LIKE ? OR relative_path LIKE ?)",
        [p_slash, p_back],
    )


def get_explorer_children(
    conn: sqlite3.Connection, drive_id: str, parent_path: str
) -> dict[str, Any]:
    """
    Retorna subcarpetes i nombre de fitxers directes per al node (drive_id, parent_path).
    parent_path normalitzat; buit = arrel.
    """
    parent_path = _normalize_path(parent_path)
    where, extra_params = _direct_children_where(parent_path)
    params: list[Any] = [drive_id] + extra_params
    cur = conn.execute(
        f"""
        SELECT relative_path, name
        FROM inventory
        WHERE {where}
        LIMIT 15000
        """,
        params,
    )
    rows = cur.fetchall()
    folder_names: set[str] = set()
    files_count = 0
    prefix_len = 0
    if parent_path:
        prefix_slash, prefix_back = _parent_prefixes(parent_path)
        prefix_len = len(prefix_slash)  # same length as prefix_back
    for rel_path, name in rows:
        norm_path = _normalize_path(rel_path)
        if parent_path:
            if not (norm_path.startswith(parent_path + "/")):
                continue
            rest = norm_path[prefix_len:]
        else:
            rest = norm_path
        if "/" in rest or "\\" in rest:
            # és una carpeta (hi ha més nivells) o un fitxer dins subcarpeta
            first = rest.split("/")[0].split("\\")[0]
            folder_names.add(first)
        else:
            # fitxer directe en aquesta carpeta
            files_count += 1
    folders_list: list[dict[str, Any]] = []
    for name in sorted(folder_names):
        path = (parent_path + "/" + name) if parent_path else name
        folders_list.append({"name": name, "path": path, "has_children": True})
    return {"folders": folders_list, "files_count": files_count}


def get_explorer_contents(
    conn: sqlite3.Connection,
    drive_id: str,
    folder_path: str,
    limit: int = 300,
    offset: int = 0,
) -> dict[str, Any]:
    """
    Retorna contingut de la carpeta (drive_id, folder_path): subcarpetes i fitxers paginats.
    """
    folder_path = _normalize_path(folder_path)
    prefix_slash, prefix_back = _parent_prefixes(folder_path)
    params: list[Any] = [drive_id]
    if folder_path:
        params.extend([prefix_slash, prefix_back])
        where = "drive_id = ? AND missing_since_utc IS NULL AND (relative_path LIKE ? OR relative_path LIKE ?)"
    else:
        where = "drive_id = ? AND missing_since_utc IS NULL"
    # Subcarpetes: noms únics de fills immediats que tenen més nivells
    cur = conn.execute(
        f"""
        SELECT relative_path
        FROM inventory
        WHERE {where}
        LIMIT 10000
        """,
        params,
    )
    folder_names: set[str] = set()
    prefix_len = len(prefix_slash) if folder_path else 0
    for (rel_path,) in cur.fetchall():
        norm = _normalize_path(rel_path)
        if folder_path:
            if not norm.startswith(folder_path + "/"):
                continue
            rest = norm[prefix_len:]
        else:
            rest = norm
        if "/" in rest or "\\" in rest:
            seg = rest.split("/")[0].split("\\")[0]
            folder_names.add(seg)
    folders_list = [{"name": n} for n in sorted(folder_names)]
    # Fitxers directes: WHERE que correspongui exactament a un fitxer (sense més / o \)
    if folder_path:
        # relative_path ha de ser prefix_slash + name o prefix_back + name, amb name sense sep
        like_slash = prefix_slash + "%"
        like_back = prefix_back + "%"
        no_more_slash = prefix_slash + "%/%"
        no_more_back1 = prefix_slash + "%\\%"
        no_more_back2 = prefix_back + "%/%"
        no_more_back3 = prefix_back + "%\\%"
        cur_count = conn.execute(
            """
            SELECT COUNT(*)
            FROM inventory
            WHERE drive_id = ? AND missing_since_utc IS NULL AND (relative_path LIKE ? OR relative_path LIKE ?)
              AND relative_path NOT LIKE ? AND relative_path NOT LIKE ?
              AND relative_path NOT LIKE ? AND relative_path NOT LIKE ?
            """,
            (drive_id, like_slash, like_back, no_more_slash, no_more_back1, no_more_back2, no_more_back3),
        )
        total_files = cur_count.fetchone()[0]
        cur_files = conn.execute(
            """
            SELECT name, extension, size_bytes, modified_utc
            FROM inventory
            WHERE drive_id = ? AND missing_since_utc IS NULL AND (relative_path LIKE ? OR relative_path LIKE ?)
              AND relative_path NOT LIKE ? AND relative_path NOT LIKE ?
              AND relative_path NOT LIKE ? AND relative_path NOT LIKE ?
            ORDER BY name
            LIMIT ? OFFSET ?
            """,
            (drive_id, like_slash, like_back, no_more_slash, no_more_back1, no_more_back2, no_more_back3, limit, offset),
        )
    else:
        like_any_slash = "%/%"
        like_any_back = "%\\%"
        cur_count = conn.execute(
            """
            SELECT COUNT(*)
            FROM inventory
            WHERE drive_id = ? AND missing_since_utc IS NULL
              AND relative_path NOT LIKE ? AND relative_path NOT LIKE ?
            """,
            (drive_id, like_any_slash, like_any_back),
        )
        total_files = cur_count.fetchone()[0]
        cur_files = conn.execute(
            """
            SELECT name, extension, size_bytes, modified_utc
            FROM inventory
            WHERE drive_id = ? AND missing_since_utc IS NULL
              AND relative_path NOT LIKE ? AND relative_path NOT LIKE ?
            ORDER BY name
            LIMIT ? OFFSET ?
            """,
            (drive_id, like_any_slash, like_any_back, limit, offset),
        )
    files_list = [dict(row) for row in cur_files]
    return {
        "folders": folders_list,
        "files": files_list,
        "total_files": total_files,
        "has_more": (offset + len(files_list)) < total_files,
    }
