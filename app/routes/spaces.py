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
    else:
        rows, total = [], 0
    for r in rows:
        rp = r["relative_path"]
        r["folder"] = rp[: rp.rfind("\\")].replace("\\", "/") if "\\" in rp else ""
    return {
        "space": space,
        "ancestors": ancestors,
        "rows": rows,
        "total": total,
        "offset": offset,
        "has_more": offset + len(rows) < total,
    }


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
