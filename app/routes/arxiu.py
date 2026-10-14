"""Secció «Arxiu»: arbre de destí, cobertura i comprovacions de l'arxiu virtual, i la seua API.

Les pàgines lligen de la memòria cau compartida (`app/arxiu_cache.py`); les escriptures (paper,
destí, nota, membres, espais) van per `queries_spaces` amb la connexió de la petició i la
memòria cau se n'adona pel segell dels membres.
"""
from __future__ import annotations

import json
import sqlite3
import time
from typing import Any

from fastapi import Request
from fastapi.responses import JSONResponse

from app import arxiu_actions, arxiu_cache
from app.queries_arxiu import ROLE_NONE, ROLE_ORDER, ArxiuCalc
from app.queries_spaces import ARCHIVE_PARENTS, SpaceError
from app.schema import ARCHIVE_KINDS, MEMBER_ROLES

ROLE_SHORT = {"canonica": "CAN", "copia": "COP", "part": "PART", "descartar": "DER",
              "quarantena": "QUAR", "tercers": "TERC", ROLE_NONE: "?"}
ROLE_LABEL = {"canonica": "canònica", "copia": "còpia", "part": "part", "descartar": "descartar",
              "quarantena": "quarantena", "tercers": "tercers", ROLE_NONE: "sense paper"}
KIND_LABEL = {"fons": "fons", "serie": "sèrie", "projecte": "projecte", "capa": "capa", "carpeta": "carpeta"}


def _api_path(db_path: str) -> str:
    return (db_path or "").replace("\\", "/")


def _common(request: Request, view: str, conn: sqlite3.Connection | None = None,
            calc: ArxiuCalc | None = None) -> dict[str, Any]:
    ctx = {
        "request": request, "view": view,
        "role_short": ROLE_SHORT, "role_label": ROLE_LABEL, "kind_label": KIND_LABEL,
        "roles": list(MEMBER_ROLES), "kinds": list(ARCHIVE_KINDS), "role_order": list(ROLE_ORDER),
        "role_none": ROLE_NONE, "archive_parents": ARCHIVE_PARENTS,
        "last_action": None, "spaces_json": "[]",
    }
    if conn is not None:
        ctx["last_action"] = arxiu_actions.last_undoable(conn)
    if calc is not None:
        ctx["spaces_json"] = json.dumps(
            [{"id": sid, "path": s["virtual_path"], "kind": s["archive_kind"]}
             for sid, s in sorted(calc.spaces.items(), key=lambda kv: kv[1]["virtual_path"].lower())],
            ensure_ascii=False)
    return ctx


def _decorate(node: dict[str, Any], depth_offset: int = 0) -> None:
    node["rel_depth"] = node["depth"] - depth_offset
    for m in node["members"]:
        m["api_path"] = _api_path(m["relative_path"])
        m["role_key"] = m["role"] or ROLE_NONE
    for c in node["children"]:
        _decorate(c, depth_offset)


def arbre_page(request: Request, templates, conn: sqlite3.Connection, force: bool = False,
               root: int | None = None):
    """Arbre de destí sencer o, amb `root`, només el subarbre d'un espai (amb el camí de dalt)."""
    t0 = time.time()
    crumbs: list[dict[str, Any]] = []
    with arxiu_cache.locked(force) as calc:
        if root is not None and root not in calc.spaces:
            root = None
        tree = calc.virtual_tree(root)
        n_spaces = len(calc.spaces)
        n_members = len(calc.members)
        drives = sorted({it["drive_id"] for it in calc.items.values()})
        offset = 0
        if root is not None:
            offset = calc.spaces[root]["depth"]
            cur = calc.spaces[root]
            while cur is not None:
                crumbs.append({"space_id": cur["space_id"], "name": cur["target_name"] or cur["name"],
                               "archive_kind": cur["archive_kind"]})
                cur = calc.spaces.get(cur["parent_id"]) if cur["parent_id"] is not None else None
            crumbs.reverse()
        ctx = _common(request, "arbre", conn, calc)
    for n in tree:
        _decorate(n, offset)
    ctx.update({
        "tree": tree, "n_spaces": n_spaces, "n_members": n_members, "drives": drives,
        "root": root, "crumbs": crumbs,
        "elapsed": time.time() - t0, "cache": arxiu_cache.info(),
    })
    return templates.TemplateResponse(request, "arxiu_arbre.html", ctx)


def api_tree(force: bool = False) -> dict[str, Any]:
    with arxiu_cache.locked(force) as calc:
        tree = calc.virtual_tree()
    for n in tree:
        _decorate(n)
    return {"tree": tree, "cache": arxiu_cache.info()}


def cobertura_page(request: Request, templates, conn: sqlite3.Connection, disc: str = ""):
    t0 = time.time()
    drives = [r[0] for r in conn.execute("SELECT drive_id FROM drives ORDER BY drive_id")]
    coverage = None
    overview: list[dict[str, Any]] = []
    with arxiu_cache.locked() as calc:
        if disc and disc in drives:
            coverage = calc.coverage(disc, details=True)
            coverage["covered_pct"] = (100.0 * coverage["covered_bytes"] / coverage["drive_bytes"]
                                       if coverage["drive_bytes"] else 0.0)
            for g in coverage["groups"]:
                g["api_path"] = _api_path(g["level1"])
                for ch in g["children"]:
                    ch["api_path"] = _api_path(f"{ch['level1']}\\{ch['level2']}" if ch["level2"] else ch["level1"])
        else:
            for d in drives:
                c = calc.coverage(d)
                overview.append({k: c[k] for k in ("drive_id", "drive_files", "drive_bytes", "covered_files",
                                                   "covered_bytes", "uncovered_files", "uncovered_bytes", "covered")})
        ctx = _common(request, "cobertura", conn, calc)
    ctx.update({"drives": drives, "disc": disc, "coverage": coverage, "overview": overview,
                "elapsed": time.time() - t0})
    return templates.TemplateResponse(request, "arxiu_cobertura.html", ctx)


def comprovacions_page(request: Request, templates, conn: sqlite3.Connection):
    t0 = time.time()
    with arxiu_cache.locked() as calc:
        findings = calc.check()
        paths = {sid: s["virtual_path"] for sid, s in calc.spaces.items()}
        ctx = _common(request, "comprovacions", conn, calc)
    for f in findings:
        f["space_path"] = paths.get(f.get("space_id"), f.get("space_path", ""))
        f["other_space_path"] = paths.get(f.get("other_space_id"), "")
        if f.get("path") is not None:
            f["api_path"] = _api_path(f["path"])
    n_err = sum(1 for f in findings if f["severity"] == "error")
    by_kind: dict[str, int] = {}
    for f in findings:
        by_kind[f["kind"]] = by_kind.get(f["kind"], 0) + 1
    ctx.update({"findings": findings, "n_err": n_err, "n_avis": len(findings) - n_err,
                "by_kind": by_kind, "elapsed": time.time() - t0})
    return templates.TemplateResponse(request, "arxiu_comprovacions.html", ctx)


# ---------------------------------------------------------------------------
# API d'accions (JSON). Cada acció queda al registre i es pot desfer.
# ---------------------------------------------------------------------------

def _err(msg: str, status: int = 400) -> JSONResponse:
    return JSONResponse({"error": msg}, status_code=status)


def _int(v: Any) -> int | None:
    if v is None or v == "" or v == "null":
        return None
    try:
        return int(v)
    except (TypeError, ValueError):
        raise SpaceError("Identificador no vàlid.")


async def _body(request: Request) -> dict[str, Any]:
    try:
        body = await request.json()
    except ValueError:
        raise SpaceError("Cos JSON no vàlid.")
    if not isinstance(body, dict):
        raise SpaceError("Cos JSON no vàlid.")
    return body


def _ok(result: dict[str, Any], conn: sqlite3.Connection) -> dict[str, Any]:
    result["last_action"] = arxiu_actions.last_undoable(conn)
    return result


async def api_add_members(request: Request, conn: sqlite3.Connection):
    """{space_id, items: [{kind, drive_id, path}], role?, target_subpath?, note?}"""
    try:
        b = await _body(request)
        items = b.get("items")
        if not isinstance(items, list) or not items:
            raise SpaceError("Cal una llista d'elements {kind, drive_id, path}.")
        if len(items) > 2000:
            raise SpaceError("Massa elements de cop (màxim 2000).")
        res = arxiu_actions.add_members(conn, int(b.get("space_id")), [i for i in items if isinstance(i, dict)],
                                        b.get("role") or None, b.get("target_subpath") or None, b.get("note") or None)
    except (SpaceError, TypeError, ValueError) as e:
        return _err(str(e))
    return _ok(res, conn)


async def api_update_member(request: Request, conn: sqlite3.Connection, space_id: int, item_id: int):
    try:
        b = await _body(request)
        res = arxiu_actions.update_member(conn, space_id, item_id, b)
    except SpaceError as e:
        return _err(str(e))
    return _ok(res, conn)


def api_remove_member(conn: sqlite3.Connection, space_id: int, item_id: int):
    try:
        res = arxiu_actions.remove_member(conn, space_id, item_id)
    except SpaceError as e:
        return _err(str(e), 404)
    return _ok(res, conn)


async def api_move_member(request: Request, conn: sqlite3.Connection, space_id: int, item_id: int):
    try:
        b = await _body(request)
        res = arxiu_actions.move_member(conn, space_id, item_id, int(b.get("to_space_id")))
    except (SpaceError, TypeError, ValueError) as e:
        return _err(str(e))
    return _ok(res, conn)


async def api_create_space(request: Request, conn: sqlite3.Connection):
    """{parent_id?, archive_kind, target_name, state?, canonical_drive_id?}"""
    try:
        b = await _body(request)
        res = arxiu_actions.create_space(conn, _int(b.get("parent_id")), b.get("archive_kind") or "",
                                         b.get("target_name") or "", b.get("state") or None,
                                         b.get("canonical_drive_id") or None)
    except SpaceError as e:
        return _err(str(e))
    return _ok(res, conn)


async def api_update_space(request: Request, conn: sqlite3.Connection, space_id: int):
    try:
        b = await _body(request)
        if "parent_id" in b:
            b["parent_id"] = _int(b["parent_id"])
        res = arxiu_actions.update_space(conn, space_id, b)
    except SpaceError as e:
        return _err(str(e))
    return _ok(res, conn)


async def api_reorder_space(request: Request, conn: sqlite3.Connection, space_id: int):
    try:
        b = await _body(request)
        res = arxiu_actions.reorder_space(conn, space_id, int(b.get("direction", 1)))
    except (SpaceError, TypeError, ValueError) as e:
        return _err(str(e))
    return _ok(res, conn)


def api_delete_space(conn: sqlite3.Connection, space_id: int):
    try:
        res = arxiu_actions.delete_space(conn, space_id)
    except SpaceError as e:
        return _err(str(e), 404)
    return _ok(res, conn)


def api_undo(conn: sqlite3.Connection):
    try:
        res = arxiu_actions.undo_last(conn)
    except SpaceError as e:
        return _err(str(e))
    return _ok({"undone": res}, conn)


def api_log(conn: sqlite3.Connection, limit: int = 20):
    return {"actions": arxiu_actions.last_actions(conn, max(1, min(200, limit)))}


def api_spaces(q: str = ""):
    """Espais d'arxiu (id, ruta virtual, tipus) que contenen `q`, per als selectors."""
    q = (q or "").strip().lower()
    with arxiu_cache.locked() as calc:
        rows = [{"id": sid, "path": s["virtual_path"], "kind": s["archive_kind"]}
                for sid, s in calc.spaces.items() if not q or q in s["virtual_path"].lower()]
    rows.sort(key=lambda r: r["path"].lower())
    return {"spaces": rows[:300]}
