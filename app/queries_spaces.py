"""Espais virtuals: arbre jeràrquic, CRUD i contingut.

Un espai és `smart` (una cerca desada: `query_json` amb el diccionari de
app.search_params.parse_search_params, executada a cada obertura) o `manual` (una
col·lecció d'elements, taula `items` + `space_members`). Els espais pengen d'un pare
(`parent_id`), així que l'arbre «Espais» de l'explorador pot tenir carpetes d'espais.

Les claus foranes de SQLite no s'activen per connexió, de manera que l'esborrat en cascada
es fa explícitament amb un CTE recursiu (descendents) en lloc de confiar en ON DELETE.
"""
from __future__ import annotations

import json
import sqlite3
from typing import Any

from app.queries import SPACE_ITEMS_SQL, TAG_ITEMS_SQL, folder_range, items_resolution_sql
from app.schema import utc_now_iso
from app.search_params import parse_search_params, run_file_search

SPACE_KINDS = ("smart", "manual")
NAME_MAX = 120
DESCRIPTION_MAX = 2000

# Ordenació del contingut d'un espai (clau de la interfície -> columna de search_inventory)
CONTENT_SORT_KEYS = {
    "name": "name",
    "type": "extension",
    "size": "size_bytes",
    "date": "modified_utc",
    "drive": "drive_id",
    "folder": "relative_path",
}


class SpaceError(ValueError):
    """Error de validació (nom buit, pare inexistent, cicle…), amb missatge per a l'usuari."""


def _row(r: sqlite3.Row | tuple) -> dict[str, Any]:
    d = dict(r)
    raw = d.pop("query_json", None)
    query = None
    if raw:
        try:
            query = json.loads(raw)
        except ValueError:
            query = None
    d["query"] = parse_search_params(query) if isinstance(query, dict) else None
    return d


def list_spaces(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Tots els espais (són pocs), ordenats per pare, ordre manual i nom."""
    cur = conn.execute(
        """
        SELECT space_id, parent_id, name, kind, query_json, description, sort_order,
               created_utc, updated_utc
        FROM spaces ORDER BY parent_id IS NOT NULL, parent_id, sort_order, name COLLATE NOCASE
        """
    )
    return [_row(r) for r in cur.fetchall()]


def build_tree(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Converteix la llista plana en arbre ({…espai, children: […]}); els orfes van a l'arrel."""
    by_id = {r["space_id"]: {**r, "children": []} for r in rows}
    roots: list[dict[str, Any]] = []
    for node in by_id.values():
        parent = by_id.get(node["parent_id"]) if node["parent_id"] is not None else None
        (parent["children"] if parent is not None else roots).append(node)
    return roots


def flatten_tree(tree: list[dict[str, Any]], depth: int = 0) -> list[dict[str, Any]]:
    """Recorregut en preordre amb `depth` (per a desplegables indentats i rutes)."""
    out: list[dict[str, Any]] = []
    for node in tree:
        out.append({k: v for k, v in node.items() if k != "children"} | {"depth": depth})
        out.extend(flatten_tree(node["children"], depth + 1))
    return out


def get_space(conn: sqlite3.Connection, space_id: int) -> dict[str, Any] | None:
    r = conn.execute(
        """
        SELECT space_id, parent_id, name, kind, query_json, description, sort_order,
               created_utc, updated_utc
        FROM spaces WHERE space_id = ?
        """,
        (space_id,),
    ).fetchone()
    return _row(r) if r else None


def get_ancestors(conn: sqlite3.Connection, space_id: int) -> list[dict[str, Any]]:
    """Avantpassats de l'arrel cap avall (sense l'espai mateix)."""
    rows = conn.execute(
        """
        WITH RECURSIVE up(space_id, parent_id, name, kind, lvl) AS (
            SELECT s.parent_id, p.parent_id, p.name, p.kind, 1
            FROM spaces s JOIN spaces p ON p.space_id = s.parent_id WHERE s.space_id = ?
            UNION ALL
            SELECT p.space_id, p.parent_id, p.name, p.kind, up.lvl + 1
            FROM up JOIN spaces p ON p.space_id = up.parent_id
        )
        SELECT space_id, name, kind FROM up ORDER BY lvl DESC
        """,
        (space_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def descendant_ids(conn: sqlite3.Connection, space_id: int) -> list[int]:
    """L'espai i tots els seus descendents."""
    rows = conn.execute(
        """
        WITH RECURSIVE sub(id) AS (
            SELECT ? UNION ALL SELECT s.space_id FROM spaces s JOIN sub ON s.parent_id = sub.id)
        SELECT id FROM sub
        """,
        (space_id,),
    ).fetchall()
    return [int(r[0]) for r in rows]


def _clean_name(name: str | None) -> str:
    n = " ".join((name or "").split())
    if not n:
        raise SpaceError("Cal un nom per a l'espai.")
    return n[:NAME_MAX]


def _check_parent(conn: sqlite3.Connection, parent_id: int | None, space_id: int | None) -> None:
    if parent_id is None:
        return
    if get_space(conn, parent_id) is None:
        raise SpaceError("L'espai pare no existeix.")
    if space_id is not None and parent_id in descendant_ids(conn, space_id):
        raise SpaceError("Un espai no pot penjar de si mateix ni d'un dels seus descendents.")


def _query_json(query: dict[str, Any] | None, kind: str) -> str | None:
    if kind != "smart":
        return None
    if not isinstance(query, dict):
        raise SpaceError("Una cerca desada necessita els criteris de cerca.")
    return json.dumps(parse_search_params(query), ensure_ascii=False)


def create_space(
    conn: sqlite3.Connection,
    name: str,
    kind: str,
    parent_id: int | None = None,
    query: dict[str, Any] | None = None,
    description: str | None = None,
) -> dict[str, Any]:
    if kind not in SPACE_KINDS:
        raise SpaceError(f"Tipus d'espai desconegut: {kind!r}")
    name = _clean_name(name)
    _check_parent(conn, parent_id, None)
    now = utc_now_iso()
    cur = conn.execute(
        """
        INSERT INTO spaces (parent_id, name, kind, query_json, description, sort_order, created_utc)
        VALUES (?, ?, ?, ?, ?,
                COALESCE((SELECT MAX(sort_order) + 1 FROM spaces WHERE parent_id IS ?), 0), ?)
        """,
        (parent_id, name, kind, _query_json(query, kind),
         (description or "").strip()[:DESCRIPTION_MAX] or None, parent_id, now),
    )
    conn.commit()
    return get_space(conn, int(cur.lastrowid))  # type: ignore[return-value]


_UNSET = object()


def update_space(
    conn: sqlite3.Connection,
    space_id: int,
    name: Any = _UNSET,
    parent_id: Any = _UNSET,
    query: Any = _UNSET,
    description: Any = _UNSET,
) -> dict[str, Any]:
    space = get_space(conn, space_id)
    if space is None:
        raise SpaceError("L'espai no existeix.")
    sets: list[str] = []
    params: list[Any] = []
    if name is not _UNSET:
        sets.append("name = ?")
        params.append(_clean_name(name))
    if parent_id is not _UNSET:
        _check_parent(conn, parent_id, space_id)
        sets.append("parent_id = ?")
        params.append(parent_id)
    if query is not _UNSET:
        if space["kind"] != "smart":
            raise SpaceError("Només una cerca desada té criteris.")
        sets.append("query_json = ?")
        params.append(_query_json(query, "smart"))
    if description is not _UNSET:
        sets.append("description = ?")
        params.append((description or "").strip()[:DESCRIPTION_MAX] or None)
    if not sets:
        return space
    sets.append("updated_utc = ?")
    params.append(utc_now_iso())
    params.append(space_id)
    conn.execute(f"UPDATE spaces SET {', '.join(sets)} WHERE space_id = ?", params)
    conn.commit()
    return get_space(conn, space_id)  # type: ignore[return-value]


def delete_space(conn: sqlite3.Connection, space_id: int) -> int:
    """Esborra l'espai i els seus descendents (i les pertinences). Retorna quants espais."""
    ids = descendant_ids(conn, space_id)
    if get_space(conn, space_id) is None:
        return 0
    ph = ",".join("?" * len(ids))
    conn.execute(f"DELETE FROM space_members WHERE space_id IN ({ph})", ids)
    conn.execute(f"DELETE FROM spaces WHERE space_id IN ({ph})", ids)
    conn.commit()
    return len(ids)


# ---------------------------------------------------------------------------
# Elements (identitat), pertinences i etiquetes
# ---------------------------------------------------------------------------

ITEM_KINDS = ("file", "folder")
TAG_COLORS = ("#e57373", "#ffb74d", "#fff176", "#81c784", "#4fc3f7", "#7986cb", "#ba68c8", "#a1887f")


def _to_db_path(path: str) -> str:
    return (path or "").replace("/", "\\").strip("\\")


def upsert_items(conn: sqlite3.Connection, specs: list[dict[str, Any]]) -> tuple[list[int], list[str]]:
    """Dona d'alta (o retroba) els elements descrits per [{kind, drive_id, path}] (ruta en format API).

    Fitxer: llegeix nom, mida i hash d'inventory (identitat per hash si en té). Carpeta: un sol
    element amb el rang de prefix (mai fitxer a fitxer). Retorna (item_ids, errors).
    """
    ids: list[int] = []
    errors: list[str] = []
    now = utc_now_iso()
    for spec in specs:
        kind = (spec.get("kind") or "").strip()
        drive_id = (spec.get("drive_id") or "").strip()
        path_db = _to_db_path(spec.get("path") or "")
        if kind not in ITEM_KINDS or not drive_id:
            errors.append(f"Element no vàlid: {spec!r}")
            continue
        if kind == "file":
            row = conn.execute(
                "SELECT name, size_bytes, hash_sha256 FROM inventory WHERE drive_id = ? AND relative_path = ?",
                (drive_id, path_db),
            ).fetchone()
            if row is None:
                errors.append(f"{drive_id}: el fitxer «{path_db}» no és a l'inventari.")
                continue
            name, size, h = row[0], row[1], row[2]
            lo = hi = None
        else:
            if not path_db:
                errors.append(f"{drive_id}: no es pot afegir l'arrel sencera d'un disc com a carpeta.")
                continue
            exists = conn.execute(
                "SELECT 1 FROM folder_stats WHERE drive_id = ? AND folder_path = ? LIMIT 1", (drive_id, path_db)
            ).fetchone()
            if exists is None:
                errors.append(f"{drive_id}: la carpeta «{path_db}» no és a l'inventari.")
                continue
            name = path_db.rsplit("\\", 1)[-1]
            size = None
            h = None
            lo, hi = folder_range(path_db)  # type: ignore[misc]
        cur = conn.execute(
            """
            INSERT INTO items (kind, drive_id, relative_path, hash_sha256, size_bytes, name,
                               range_lo, range_hi, created_utc)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(kind, drive_id, relative_path) DO UPDATE SET
                hash_sha256 = COALESCE(excluded.hash_sha256, items.hash_sha256),
                size_bytes = COALESCE(excluded.size_bytes, items.size_bytes),
                name = COALESCE(excluded.name, items.name)
            RETURNING item_id
            """,
            (kind, drive_id, path_db, h, size, name, lo, hi, now),
        )
        ids.append(int(cur.fetchone()[0]))
    return ids, errors


def refresh_item_hashes(conn: sqlite3.Connection) -> int:
    """Completa el hash dels elements fitxer que encara no en tenien quan el hash ja existeix.

    La taula items és petita i la cerca va per l'índex únic (drive_id, relative_path): ms.
    """
    cur = conn.execute(
        """
        UPDATE items SET hash_sha256 = (
            SELECT i.hash_sha256 FROM inventory i
            WHERE i.drive_id = items.drive_id AND i.relative_path = items.relative_path)
        WHERE kind = 'file' AND hash_sha256 IS NULL AND EXISTS (
            SELECT 1 FROM inventory i
            WHERE i.drive_id = items.drive_id AND i.relative_path = items.relative_path
              AND i.hash_sha256 IS NOT NULL)
        """
    )
    return cur.rowcount


def add_members(conn: sqlite3.Connection, space_id: int, specs: list[dict[str, Any]]) -> dict[str, Any]:
    space = get_space(conn, space_id)
    if space is None:
        raise SpaceError("L'espai no existeix.")
    if space["kind"] != "manual":
        raise SpaceError("Només es poden afegir elements a una col·lecció (no a una cerca desada).")
    ids, errors = upsert_items(conn, specs)
    now = utc_now_iso()
    added = 0
    for item_id in ids:
        cur = conn.execute(
            "INSERT OR IGNORE INTO space_members (space_id, item_id, added_utc) VALUES (?, ?, ?)",
            (space_id, item_id, now),
        )
        added += cur.rowcount
    conn.execute("UPDATE spaces SET updated_utc = ? WHERE space_id = ?", (now, space_id))
    conn.commit()
    return {"added": added, "already": len(ids) - added, "errors": errors, "item_ids": ids}


def remove_member(conn: sqlite3.Connection, space_id: int, item_id: int) -> bool:
    cur = conn.execute(
        "DELETE FROM space_members WHERE space_id = ? AND item_id = ?", (space_id, item_id)
    )
    conn.commit()
    return cur.rowcount > 0


def count_members(conn: sqlite3.Connection, space_id: int) -> int:
    return int(conn.execute(
        "SELECT COUNT(*) FROM space_members WHERE space_id = ?", (space_id,)).fetchone()[0])


def list_tags(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    cur = conn.execute(
        """
        SELECT t.tag_id, t.name, t.color, t.created_utc,
               (SELECT COUNT(*) FROM item_tags it WHERE it.tag_id = t.tag_id) AS items_count
        FROM tags t ORDER BY t.name COLLATE NOCASE
        """
    )
    return [dict(r) for r in cur.fetchall()]


def get_tag(conn: sqlite3.Connection, tag_id: int) -> dict[str, Any] | None:
    r = conn.execute(
        "SELECT tag_id, name, color, created_utc FROM tags WHERE tag_id = ?", (tag_id,)
    ).fetchone()
    return dict(r) if r else None


def _clean_tag_name(name: str | None) -> str:
    n = " ".join((name or "").split())
    if not n:
        raise SpaceError("Cal un nom per a l'etiqueta.")
    return n[:60]


def create_tag(conn: sqlite3.Connection, name: str, color: str | None = None) -> dict[str, Any]:
    name = _clean_tag_name(name)
    existing = conn.execute("SELECT tag_id FROM tags WHERE name = ? COLLATE NOCASE", (name,)).fetchone()
    if existing:
        return get_tag(conn, int(existing[0]))  # type: ignore[return-value]
    if not color:
        n = int(conn.execute("SELECT COUNT(*) FROM tags").fetchone()[0])
        color = TAG_COLORS[n % len(TAG_COLORS)]
    cur = conn.execute(
        "INSERT INTO tags (name, color, created_utc) VALUES (?, ?, ?)", (name, color, utc_now_iso())
    )
    conn.commit()
    return get_tag(conn, int(cur.lastrowid))  # type: ignore[return-value]


def update_tag(conn: sqlite3.Connection, tag_id: int, name: Any = _UNSET, color: Any = _UNSET) -> dict[str, Any]:
    if get_tag(conn, tag_id) is None:
        raise SpaceError("L'etiqueta no existeix.")
    sets: list[str] = []
    params: list[Any] = []
    if name is not _UNSET:
        n = _clean_tag_name(name)
        dup = conn.execute(
            "SELECT tag_id FROM tags WHERE name = ? COLLATE NOCASE AND tag_id <> ?", (n, tag_id)
        ).fetchone()
        if dup:
            raise SpaceError(f"Ja hi ha una etiqueta anomenada «{n}».")
        sets.append("name = ?")
        params.append(n)
    if color is not _UNSET:
        sets.append("color = ?")
        params.append(color or None)
    if sets:
        conn.execute(f"UPDATE tags SET {', '.join(sets)} WHERE tag_id = ?", params + [tag_id])
        conn.commit()
    return get_tag(conn, tag_id)  # type: ignore[return-value]


def delete_tag(conn: sqlite3.Connection, tag_id: int) -> bool:
    conn.execute("DELETE FROM item_tags WHERE tag_id = ?", (tag_id,))
    cur = conn.execute("DELETE FROM tags WHERE tag_id = ?", (tag_id,))
    conn.commit()
    return cur.rowcount > 0


def tag_items(conn: sqlite3.Connection, tag_id: int, specs: list[dict[str, Any]]) -> dict[str, Any]:
    if get_tag(conn, tag_id) is None:
        raise SpaceError("L'etiqueta no existeix.")
    ids, errors = upsert_items(conn, specs)
    now = utc_now_iso()
    added = 0
    for item_id in ids:
        cur = conn.execute(
            "INSERT OR IGNORE INTO item_tags (item_id, tag_id, added_utc) VALUES (?, ?, ?)",
            (item_id, tag_id, now),
        )
        added += cur.rowcount
    conn.commit()
    return {"added": added, "already": len(ids) - added, "errors": errors, "item_ids": ids}


def untag_items(conn: sqlite3.Connection, tag_id: int, specs: list[dict[str, Any]] | None = None,
                item_ids: list[int] | None = None) -> int:
    ids = list(item_ids or [])
    if specs:
        for spec in specs:
            r = conn.execute(
                "SELECT item_id FROM items WHERE kind = ? AND drive_id = ? AND relative_path = ?",
                (spec.get("kind"), spec.get("drive_id"), _to_db_path(spec.get("path") or "")),
            ).fetchone()
            if r:
                ids.append(int(r[0]))
    n = 0
    for item_id in ids:
        n += conn.execute(
            "DELETE FROM item_tags WHERE tag_id = ? AND item_id = ?", (tag_id, item_id)
        ).rowcount
    conn.commit()
    return n


def tags_of(conn: sqlite3.Connection, kind: str, drive_id: str, path: str) -> list[int]:
    """Etiquetes (ids) d'un element concret, si existeix a `items`."""
    rows = conn.execute(
        """
        SELECT it.tag_id FROM items t JOIN item_tags it ON it.item_id = t.item_id
        WHERE t.kind = ? AND t.drive_id = ? AND t.relative_path = ?
        """,
        (kind, drive_id, _to_db_path(path)),
    ).fetchall()
    return [int(r[0]) for r in rows]


def _resolved_contents(
    conn: sqlite3.Connection,
    source_sql: str,
    source_param: Any,
    limit: int,
    offset: int,
    sort: str | None,
    direction: str | None,
) -> tuple[list[dict[str, Any]], int]:
    """Pàgina dels fitxers resolts d'una font d'elements, amb el motiu i l'element d'origen."""
    sort_col = CONTENT_SORT_KEYS.get(sort or "", "name")
    sort_dir = "DESC" if direction == "desc" else "ASC"
    cur = conn.execute(
        f"""
        SELECT i.id, i.drive_id, i.relative_path, i.name, i.extension, i.size_bytes, i.modified_utc,
               i.missing_since_utc, i.hash_sha256, h.item_id, h.reason,
               t.kind AS item_kind, t.drive_id AS item_drive, t.relative_path AS item_path,
               COUNT(*) OVER () AS _total
        FROM ({items_resolution_sql(source_sql)}) h
        JOIN inventory i ON i.id = h.id
        JOIN items t ON t.item_id = h.item_id
        ORDER BY i.{sort_col} {sort_dir}, i.drive_id, i.relative_path
        LIMIT ? OFFSET ?
        """,
        (source_param, source_param, source_param, limit, offset),
    )
    rows = [dict(r) for r in cur.fetchall()]
    total = int(rows[0].pop("_total")) if rows else 0
    for r in rows[1:]:
        r.pop("_total", None)
    if not rows and offset > 0:
        total = int(conn.execute(
            f"SELECT COUNT(*) FROM ({items_resolution_sql(source_sql)})",
            (source_param, source_param, source_param),
        ).fetchone()[0])
    return rows, total


def get_manual_contents(conn, space, limit, offset, sort=None, direction=None):
    return _resolved_contents(conn, SPACE_ITEMS_SQL, space["space_id"], limit, offset, sort, direction)


def get_tag_contents(conn, tag_id, limit, offset, sort=None, direction=None):
    return _resolved_contents(conn, TAG_ITEMS_SQL, tag_id, limit, offset, sort, direction)


def get_smart_contents(
    conn: sqlite3.Connection,
    space: dict[str, Any],
    limit: int,
    offset: int,
    sort: str | None = None,
    direction: str | None = None,
) -> tuple[list[dict[str, Any]], int]:
    """Executa la cerca desada d'un espai `smart` (una pàgina i el total)."""
    query = space.get("query") or parse_search_params({})
    sort_by = CONTENT_SORT_KEYS.get(sort or "", None)
    sort_dir = direction if direction in ("asc", "desc") else None
    return run_file_search(conn, query, limit, offset, sort_by=sort_by or None, sort_dir=sort_dir)
