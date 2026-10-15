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
from fastapi.responses import HTMLResponse, JSONResponse

from app import arxiu_actions, arxiu_cache, queries_cobertura
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


def split_path(path: str) -> dict[str, str]:
    r"""Trossos d'una ruta per truncar-la pel mig a la interfície: cap (primer segment), mig i cua
    (darrer segment), de manera que «RECUPERACIO_Z\Root\Foto-Video\cataleg» es puga mostrar
    com «RECUPERACIO_Z\…\cataleg» quan no hi cap sencera."""
    parts = (path or "").split("\\")
    if len(parts) == 1:
        return {"head": "", "mid": "", "tail": parts[0]}
    if len(parts) == 2:
        return {"head": parts[0], "mid": "", "tail": "\\" + parts[1]}
    return {"head": parts[0] + "\\", "mid": "\\".join(parts[1:-1]), "tail": "\\" + parts[-1]}


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


def _decorate(node: dict[str, Any], depth_offset: int = 0) -> int:
    """Afegeix a cada node el que la plantilla necessita i torna els projectes sense canònica
    del subarbre. `pending` = projectes sense canònica + membres sense paper (recursius)."""
    node["rel_depth"] = node["depth"] - depth_offset
    for m in node["members"]:
        m["api_path"] = _api_path(m["relative_path"])
        m["role_key"] = m["role"] or ROLE_NONE
        m["path_parts"] = split_path(m["relative_path"])
    missing = 0
    if node["archive_kind"] == "projecte" and "canonica" not in node["totals"]["by_role"]:
        missing = 1
    for c in node["children"]:
        missing += _decorate(c, depth_offset)
    no_role = node["totals"]["by_role"].get(ROLE_NONE, {}).get("members", 0)
    node["missing_canonica"] = missing
    node["no_role"] = no_role
    node["pending"] = missing + no_role
    return missing


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


def _decorate_cov_rows(rows: list[dict[str, Any]], level: int) -> None:
    for r in rows:
        r["api_path"] = _api_path(r["path"])
        r["level"] = level
        r["bytes_h"] = _format_bytes(r["bytes"])
        r["unc_h"] = _format_bytes(r["uncovered_bytes"])
        r["small"] = r["bytes"] < 1_000_000_000


def _format_bytes(value: int) -> str:
    from app.main import _format_bytes as f
    return f(value)


def cobertura_page(request: Request, templates, conn: sqlite3.Connection, disc: str = "", carpeta: str = ""):
    """Resum de tots els discs o, amb `disc`, la cobertura navegable d'una carpeta (`carpeta`)."""
    t0 = time.time()
    drives = [r[0] for r in conn.execute("SELECT drive_id FROM drives ORDER BY drive_id")]
    coverage = None
    level = None
    overview: list[dict[str, Any]] = []
    with arxiu_cache.locked() as calc:
        if disc and disc in drives:
            coverage = calc.coverage(disc)
            coverage["covered_pct"] = (100.0 * coverage["covered_bytes"] / coverage["drive_bytes"]
                                       if coverage["drive_bytes"] else 0.0)
            try:
                level = queries_cobertura.folder_coverage(calc, disc, carpeta, arxiu_cache.stamps())
            except SpaceError:
                level = queries_cobertura.folder_coverage(calc, disc, "", arxiu_cache.stamps())
        else:
            overview = _drives_coverage(calc, conn)
        ctx = _common(request, "cobertura", conn, calc)
    if level is not None:
        _decorate_cov_rows(level["rows"], len(level["crumbs"]) + 1)
        level["api_path"] = _api_path(level["folder"])
        for cr in level["crumbs"]:
            cr["api_path"] = _api_path(cr["path"])
        level["covered_pct"] = 100.0 * level["covered_bytes"] / level["bytes"] if level["bytes"] else 0.0
    ctx.update({"drives": drives, "disc": disc, "coverage": coverage, "overview": overview, "level": level,
                "elapsed": time.time() - t0})
    return templates.TemplateResponse(request, "arxiu_cobertura.html", ctx)


def cobertura_files(request: Request, templates, disc: str, carpeta: str, nivell: int):
    """Files d'un nivell (fragment HTML) per al desplegament en línia."""
    with arxiu_cache.locked() as calc:
        try:
            level = queries_cobertura.folder_coverage(calc, disc, carpeta, arxiu_cache.stamps())
        except SpaceError as e:
            return HTMLResponse(f'<tr><td colspan="10" class="muted">{e}</td></tr>', status_code=404)
        ctx = _common(request, "cobertura", None, calc)
    _decorate_cov_rows(level["rows"], max(1, nivell))
    ctx.update({"rows": level["rows"], "disc": disc, "parent_path": _api_path(level["folder"])})
    return templates.TemplateResponse(request, "arxiu_cobertura_files.html", ctx)


COVERED_PCT = 90.0   # un disc compta com a cobert amb aquest % de bytes dins d'espais
OUTSIDE_MIN = 1_000_000_000   # carpetes fora d'espai que valen la pena llistar a «Pendents»
OUTSIDE_PER_DRIVE = 8


def _drives_coverage(calc: ArxiuCalc, conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Resum de cobertura per disc (sense detalls), amb % cobert i les carpetes de nivell 1 fora."""
    out = []
    for d in [r[0] for r in conn.execute("SELECT drive_id FROM drives ORDER BY drive_id")]:
        c = calc.coverage(d)
        o = {k: c[k] for k in ("drive_id", "drive_files", "drive_bytes", "covered_files",
                               "covered_bytes", "uncovered_files", "uncovered_bytes", "covered")}
        o["pct"] = 100.0 * o["covered_bytes"] / o["drive_bytes"] if o["drive_bytes"] else 0.0
        o["is_covered"] = o["pct"] >= COVERED_PCT
        o["outside"] = [{"level1": g["level1"], "api_path": _api_path(g["level1"]), "files": g["files"], "bytes": g["bytes"]}
                        for g in c["groups"] if g["bytes"] >= OUTSIDE_MIN and g["level1"]]
        o["outside_more"] = max(0, len(o["outside"]) - OUTSIDE_PER_DRIVE)
        o["outside"] = o["outside"][:OUTSIDE_PER_DRIVE]
        out.append(o)
    return out


def _pending_groups(calc: ArxiuCalc, drives: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Els pendents ordenats per feina: errors, projectes sense canònica, membres sense paper,
    carpetes fora d'espai per disc, altres avisos i, al final, els niaments entre espais."""
    findings = calc.check()
    paths = {sid: s["virtual_path"] for sid, s in calc.spaces.items()}
    for f in findings:
        f["space_path"] = paths.get(f.get("space_id"), f.get("space_path", ""))
        f["other_space_path"] = paths.get(f.get("other_space_id"), "")
        if f.get("path") is not None:
            f["api_path"] = _api_path(f["path"])
            f["path_parts"] = split_path(f["path"])
    groups = [
        {"key": "errors", "title": "Errors", "hint": "Noms, jerarquia i papers contradictoris: l'executor es nega a escriure el pla mentre n'hi haja.",
         "severity": "error", "rows": [f for f in findings if f["severity"] == "error"]},
        {"key": "sense-canonica", "title": "Projectes sense canònica", "hint": "Cap membre del projecte té el paper canònica: tria quina còpia es copia a l'arxiu.",
         "severity": "avis", "rows": [f for f in findings if f["kind"] == "sense-canonica"]},
        {"key": "sense-paper", "title": "Membres sense paper", "hint": "Carpetes dins d'un espai sense decidir què són: canvia el paper o treu-les.",
         "severity": "avis", "rows": [f for f in findings if f["kind"] == "sense-paper"]},
        {"key": "fora", "title": "Carpetes fora de tot espai", "hint": f"Per disc, les carpetes de nivell 1 de més d'1 GB que no són en cap espai (fins a {OUTSIDE_PER_DRIVE} per disc).",
         "severity": "avis", "drives": [d for d in drives if d["outside"]],
         "rows": [dict(drive_id=d["drive_id"], **o) for d in drives for o in d["outside"]]},
        {"key": "altres", "title": "Altres avisos", "hint": "Orígens repetits en dos espais i orígens que ja no són a l'inventari.",
         "severity": "avis", "rows": [f for f in findings if f["severity"] != "error"
                                       and f["kind"] not in ("sense-canonica", "sense-paper", "niat-entre-espais")]},
        {"key": "niats", "title": "Niaments entre espais", "hint": "Carpetes membres dins d'una carpeta membre d'un altre espai: quasi tots són volguts (cada fitxer compta per al membre més profund).",
         "severity": "info", "collapsed": True, "rows": [f for f in findings if f["kind"] == "niat-entre-espais"]},
    ]
    for g in groups:
        g["n"] = len(g["rows"])
    return groups


def _n_pending(groups: list[dict[str, Any]]) -> int:
    return sum(g["n"] for g in groups if g["key"] in ("errors", "sense-canonica", "sense-paper"))


def pendents_page(request: Request, templates, conn: sqlite3.Connection):
    t0 = time.time()
    with arxiu_cache.locked() as calc:
        drives = _drives_coverage(calc, conn)
        groups = _pending_groups(calc, drives)
        ctx = _common(request, "pendents", conn, calc)
    ctx.update({"groups": groups, "n_err": groups[0]["n"], "n_pending": _n_pending(groups),
                "n_all": sum(g["n"] for g in groups), "elapsed": time.time() - t0})
    return templates.TemplateResponse(request, "arxiu_pendents.html", ctx)


def inici_page(request: Request, templates, conn: sqlite3.Connection, force: bool = False):
    """Pàgina d'entrada de l'Arxiu: quatre xifres i la llista de discs amb el % cobert."""
    t0 = time.time()
    with arxiu_cache.locked(force) as calc:
        roots = [calc.space_totals(r) for r in calc.roots]
        root_names = [(calc.spaces[r]["target_name"], calc.spaces[r]["archive_kind"]) for r in calc.roots]
        drives = _drives_coverage(calc, conn)
        groups = _pending_groups(calc, drives)
        n_spaces, n_members = len(calc.spaces), len(calc.members)
        ctx = _common(request, "inici", conn, calc)
    fons = [{"name": n, "kind": k, "totals": t} for (n, k), t in zip(root_names, roots)]
    ctx.update({
        "fons": fons,
        "total_bytes": sum(t["bytes"] for t in roots), "total_files": sum(t["files"] for t in roots),
        "unique_bytes": sum(t["unique_bytes"] for t in roots),
        "drives": drives, "n_drives_covered": sum(1 for d in drives if d["is_covered"]),
        "covered_pct": COVERED_PCT,
        "n_pending": _n_pending(groups), "n_err": groups[0]["n"],
        "n_spaces": n_spaces, "n_members": n_members,
        "elapsed": time.time() - t0, "cache": arxiu_cache.info(),
    })
    return templates.TemplateResponse(request, "arxiu_inici.html", ctx)


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
