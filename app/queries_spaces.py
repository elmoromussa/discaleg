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
