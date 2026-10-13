"""API dels espais virtuals (/api/spaces/*) i formulari «Desa com a espai» (/spaces/desa)."""
from __future__ import annotations

import json
import sqlite3
from typing import Any
from urllib.parse import quote

from fastapi import Request
from fastapi.responses import JSONResponse, PlainTextResponse, RedirectResponse

from app import queries_spaces
from app.queries_spaces import SpaceError
from app.search_params import parse_search_params

CONTENTS_LIMIT_MAX = 500


def _err(msg: str, status: int = 400) -> JSONResponse:
    return JSONResponse({"error": msg}, status_code=status)


def _int_or_none(v: Any) -> int | None:
    if v is None or v == "" or v == "null":
        return None
    try:
        return int(v)
    except (TypeError, ValueError):
        raise SpaceError("Identificador d'espai no vàlid.")


def api_tree(conn: sqlite3.Connection) -> dict[str, Any]:
    rows = queries_spaces.list_spaces(conn)
    tree = queries_spaces.build_tree(rows)
    return {"spaces": tree, "flat": queries_spaces.flatten_tree(tree)}


async def api_create(request: Request, conn: sqlite3.Connection):
    try:
        body = await request.json()
    except ValueError:
        return _err("Cos JSON no vàlid.")
    try:
        space = queries_spaces.create_space(
            conn,
            name=body.get("name", ""),
            kind=body.get("kind", "manual"),
            parent_id=_int_or_none(body.get("parent_id")),
            query=body.get("query"),
            description=body.get("description"),
        )
    except SpaceError as e:
        return _err(str(e))
    return {"space": space}


async def api_update(request: Request, conn: sqlite3.Connection, space_id: int):
    try:
        body = await request.json()
    except ValueError:
        return _err("Cos JSON no vàlid.")
    kwargs: dict[str, Any] = {}
    try:
        if "name" in body:
            kwargs["name"] = body["name"]
        if "parent_id" in body:
            kwargs["parent_id"] = _int_or_none(body["parent_id"])
        if "query" in body:
            kwargs["query"] = body["query"]
        if "description" in body:
            kwargs["description"] = body["description"]
        space = queries_spaces.update_space(conn, space_id, **kwargs)
    except SpaceError as e:
        return _err(str(e), 404 if "no existeix" in str(e) else 400)
    return {"space": space}


def api_delete(conn: sqlite3.Connection, space_id: int):
    n = queries_spaces.delete_space(conn, space_id)
    if n == 0:
        return _err("L'espai no existeix.", 404)
    return {"deleted": n}


def _api_path(db_path: str) -> str:
    return db_path.replace("\\", "/")


def _decorate_rows(rows: list[dict[str, Any]]) -> None:
    for r in rows:
        rp = r["relative_path"]
        r["folder"] = rp[: rp.rfind("\\")].replace("\\", "/") if "\\" in rp else ""
        if "item_path" in r and r["item_path"] is not None:
            r["item_path"] = _api_path(r["item_path"])


def api_contents(
    conn: sqlite3.Connection, space_id: int, limit: int, offset: int, sort: str, direction: str
):
    space = queries_spaces.get_space(conn, space_id)
    if space is None:
        return _err("L'espai no existeix.", 404)
    limit = max(1, min(CONTENTS_LIMIT_MAX, limit))
    offset = max(0, offset)
    ancestors = queries_spaces.get_ancestors(conn, space_id)
    if space["kind"] == "smart":
        rows, total = queries_spaces.get_smart_contents(conn, space, limit, offset, sort, direction)
        members = None
    else:
        if queries_spaces.refresh_item_hashes(conn):
            conn.commit()
        rows, total = queries_spaces.get_manual_contents(conn, space, limit, offset, sort, direction)
        members = queries_spaces.count_members(conn, space_id)
    _decorate_rows(rows)
    return {
        "space": space,
        "ancestors": ancestors,
        "members": members,
        "rows": rows,
        "total": total,
        "offset": offset,
        "has_more": offset + len(rows) < total,
    }


def _specs_from(body: Any) -> list[dict[str, Any]]:
    items = body.get("items") if isinstance(body, dict) else None
    if not isinstance(items, list) or not items:
        raise SpaceError("Cal una llista d'elements {kind, drive_id, path}.")
    if len(items) > 2000:
        raise SpaceError("Massa elements de cop (màxim 2000).")
    return [i for i in items if isinstance(i, dict)]


async def api_add_items(request: Request, conn: sqlite3.Connection, space_id: int):
    try:
        body = await request.json()
        result = queries_spaces.add_members(conn, space_id, _specs_from(body))
    except ValueError as e:  # SpaceError també
        return _err(str(e), 404 if "no existeix" in str(e) else 400)
    return result


def api_remove_item(conn: sqlite3.Connection, space_id: int, item_id: int):
    if not queries_spaces.remove_member(conn, space_id, item_id):
        return _err("Aquest element no és a l'espai.", 404)
    return {"removed": 1}


def api_tags(conn: sqlite3.Connection):
    return {"tags": queries_spaces.list_tags(conn)}


async def api_tag_create(request: Request, conn: sqlite3.Connection):
    try:
        body = await request.json()
        tag = queries_spaces.create_tag(conn, body.get("name", ""), body.get("color"))
    except ValueError as e:
        return _err(str(e))
    return {"tag": tag}


async def api_tag_update(request: Request, conn: sqlite3.Connection, tag_id: int):
    try:
        body = await request.json()
        kwargs: dict[str, Any] = {}
        if "name" in body:
            kwargs["name"] = body["name"]
        if "color" in body:
            kwargs["color"] = body["color"]
        tag = queries_spaces.update_tag(conn, tag_id, **kwargs)
    except ValueError as e:
        return _err(str(e), 404 if "no existeix" in str(e) else 400)
    return {"tag": tag}


def api_tag_delete(conn: sqlite3.Connection, tag_id: int):
    if not queries_spaces.delete_tag(conn, tag_id):
        return _err("L'etiqueta no existeix.", 404)
    return {"deleted": 1}


async def api_items_tag(request: Request, conn: sqlite3.Connection, remove: bool):
    try:
        body = await request.json()
        tag_ids = body.get("tag_ids") if isinstance(body, dict) else None
        if not isinstance(tag_ids, list) or not tag_ids:
            raise SpaceError("Cal tag_ids.")
        specs = _specs_from(body)
        out: dict[str, Any] = {"tags": {}}
        for tid in tag_ids:
            tid = int(tid)
            if remove:
                out["tags"][tid] = {"removed": queries_spaces.untag_items(conn, tid, specs)}
            else:
                out["tags"][tid] = queries_spaces.tag_items(conn, tid, specs)
    except (ValueError, TypeError) as e:
        return _err(str(e), 404 if "no existeix" in str(e) else 400)
    return out


def api_item_tags(conn: sqlite3.Connection, kind: str, drive_id: str, path: str):
    return {"tag_ids": queries_spaces.tags_of(conn, kind, drive_id, path)}


def api_tag_contents(conn: sqlite3.Connection, tag_id: int, limit: int, offset: int, sort: str, direction: str):
    tag = queries_spaces.get_tag(conn, tag_id)
    if tag is None:
        return _err("L'etiqueta no existeix.", 404)
    limit = max(1, min(CONTENTS_LIMIT_MAX, limit))
    offset = max(0, offset)
    if queries_spaces.refresh_item_hashes(conn):
        conn.commit()
    rows, total = queries_spaces.get_tag_contents(conn, tag_id, limit, offset, sort, direction)
    _decorate_rows(rows)
    return {"tag": tag, "rows": rows, "total": total, "offset": offset, "has_more": offset + len(rows) < total}


async def save_from_search(request: Request, conn: sqlite3.Connection):
    """Formulari de Cercar: crea una cerca desada (o n'actualitza una) i obre l'explorador."""
    form = await request.form()
    try:
        query_json = form.get("query_json") or "{}"
        query = json.loads(query_json)
        if not isinstance(query, dict):
            raise ValueError
    except ValueError:
        return PlainTextResponse("Criteris de cerca no vàlids.", status_code=400)
    query = parse_search_params(query)
    try:
        space_id = _int_or_none(form.get("space_id"))
        parent_id = _int_or_none(form.get("parent_id"))
        if space_id is not None:
            space = queries_spaces.update_space(
                conn, space_id, name=form.get("name") or queries_spaces.get_space(conn, space_id)["name"],
                query=query,
            )
        else:
            space = queries_spaces.create_space(
                conn, name=form.get("name") or "", kind="smart", parent_id=parent_id, query=query
            )
    except (SpaceError, TypeError) as e:
        return PlainTextResponse(f"No s'ha pogut desar l'espai: {e}", status_code=400)
    return RedirectResponse(url=f"/explorador?space={quote(str(space['space_id']))}", status_code=303)
