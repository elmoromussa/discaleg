"""GET /copies — Còpia única (fitxers sense còpia en cap altre disc) i comparació entre discs."""
from __future__ import annotations

import sqlite3
import time
from math import ceil

from fastapi import Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from app import queries, queries_copies

PER_PAGE = 100


def _pagination_urls(request: Request, page: int, total_pages: int):
    params = dict(request.query_params)
    prev_url = next_url = None
    if page > 1:
        params["page"] = page - 1
        prev_url = str(request.url.include_query_params(**params))
    if page < total_pages:
        params["page"] = page + 1
        next_url = str(request.url.include_query_params(**params))
    return prev_url, next_url


def copies_page(request: Request, templates: Jinja2Templates, conn: sqlite3.Connection) -> HTMLResponse:
    qp = request.query_params
    view = (qp.get("view") or "unique").strip().lower()
    if view not in ("unique", "compare"):
        view = "unique"
    mode = (qp.get("mode") or "hash").strip().lower()
    if mode not in queries_copies.MODES:
        mode = "hash"
    scope = (qp.get("scope") or "cross").strip().lower()
    if scope not in queries_copies.SCOPES:
        scope = "cross"
    show = (qp.get("show") or "folders").strip().lower()
    if show not in ("folders", "list"):
        show = "folders"
    drive = (qp.get("drive") or qp.get("from") or "").strip()
    to_drive = (qp.get("to") or "").strip()
    path = (qp.get("path") or "").strip().replace("\\", "/").strip("/")
    prefix = (qp.get("prefix") or "").strip().replace("\\", "/").strip("/")
    page = max(1, int(qp.get("page", 1) or 1))
    submitted = qp.get("submitted") == "1" or bool(drive)

    drive_ids = queries.get_drive_ids(conn)
    coverage = queries.get_hash_coverage(conn)
    error = None
    summary = None
    groups = None
    rows = None
    total_pages = 0
    prev_url = next_url = None
    overview = None
    elapsed = None
    kind = "compare" if view == "compare" else "unique"

    if submitted:
        t0 = time.time()
        if drive and drive not in drive_ids:
            error = f"El disc «{drive}» no és a la base de dades."
        elif view == "compare" and (not to_drive or to_drive not in drive_ids):
            error = "Trieu el disc amb què comparar."
        elif view == "compare" and to_drive == drive:
            error = "Els dos discs han de ser diferents."
        elif not drive and view == "unique":
            overview = queries_copies.all_drives_unique(conn, mode, scope)
        elif drive:
            if view == "compare":
                summary = queries_copies.compare_summary(conn, mode, drive, to_drive, path)
            else:
                summary = queries_copies.unique_summary(conn, mode, drive, path, scope)
            if show == "folders":
                groups = queries_copies.grouped_by_folder(
                    conn, mode, kind, drive, path, scope, to_drive or None, prefix
                )
            else:
                rows = queries_copies.list_files(
                    conn, mode, kind, drive, path, scope, to_drive or None,
                    PER_PAGE, (page - 1) * PER_PAGE, prefix,
                )
                total_for_pages = summary["files"] if not prefix else None
                if total_for_pages is not None:
                    total_pages = max(1, ceil(total_for_pages / PER_PAGE)) if total_for_pages else 0
                    prev_url, next_url = _pagination_urls(request, page, total_pages)
                else:
                    # dins d'una subcarpeta no coneixem el total sense una altra passada: «Següent» si la pàgina és plena
                    prev_url, next_url = _pagination_urls(request, page, page + (1 if len(rows) == PER_PAGE else 0))
        elapsed = time.time() - t0

    def with_params(**changes) -> str:
        return str(request.url.include_query_params(**changes))

    crumbs = []
    if prefix:
        acc = ""
        for seg in prefix.split("/"):
            acc = acc + "/" + seg if acc else seg
            crumbs.append({"name": seg, "url": with_params(prefix=acc, page=1)})

    return templates.TemplateResponse(request, "copies.html",
        {
            "request": request,
            "view": view,
            "mode": mode,
            "scope": scope,
            "show": show,
            "drive": drive,
            "to_drive": to_drive,
            "path": path,
            "prefix": prefix,
            "crumbs": crumbs,
            "drive_ids": drive_ids,
            "coverage": coverage,
            "submitted": submitted,
            "error": error,
            "summary": summary,
            "overview": overview,
            "groups": groups,
            "rows": rows,
            "page": page,
            "total_pages": total_pages,
            "prev_url": prev_url,
            "next_url": next_url,
            "per_page": PER_PAGE,
            "elapsed": elapsed,
            "url_folders": with_params(show="folders", page=1),
            "url_list": with_params(show="list", page=1),
            "url_root_prefix": with_params(prefix="", page=1),
            "url_swap": with_params(**{"from": to_drive, "drive": to_drive, "to": drive, "page": 1, "prefix": ""}) if view == "compare" else None,
        },
    )
