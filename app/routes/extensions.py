"""GET /extensions — Llistat by_extension amb filtre per disc i paginació."""
import sqlite3
from math import ceil
from fastapi import Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from app import queries

PER_PAGE = 50


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


def extensions_page(
    request: Request,
    templates: Jinja2Templates,
    conn: sqlite3.Connection,
) -> HTMLResponse:
    submitted = request.query_params.get("submitted") == "1"
    selected_drive_ids = [
        x for x in request.query_params.getlist("drive_ids")
        if x and x.strip()
    ]
    drive_ids_filter = selected_drive_ids if selected_drive_ids else None
    page = max(1, int(request.query_params.get("page", 1)))
    offset = (page - 1) * PER_PAGE

    sort = request.query_params.get("sort", "").strip() or None
    dir_ = request.query_params.get("dir", "asc").strip().lower()
    if sort not in queries.EXTENSIONS_LIST_SORT_COLUMNS:
        sort = None
    if dir_ not in ("asc", "desc"):
        dir_ = "asc"

    all_drive_ids = queries.get_drive_ids(conn)
    rows = []
    total = 0
    total_pages = 0
    prev_url = next_url = None

    if submitted:
        rows, total = queries.get_extensions_list(
            conn, drive_ids_filter, limit=PER_PAGE, offset=offset,
            sort_by=sort, sort_dir=dir_,
        )
        total_pages = max(1, ceil(total / PER_PAGE)) if total else 0
        prev_url, next_url = _pagination_urls(request, page, total_pages)

    def _sort_url(col: str) -> str:
        next_dir = "desc" if (sort == col and dir_ == "asc") else "asc"
        return str(request.url.include_query_params(sort=col, dir=next_dir))

    sort_urls = {col: _sort_url(col) for col in queries.EXTENSIONS_LIST_SORT_COLUMNS}

    return templates.TemplateResponse(request, "extensions.html",
        {
            "request": request,
            "submitted": submitted,
            "selected_drive_ids": selected_drive_ids,
            "drive_ids": all_drive_ids,
            "rows": rows,
            "total": total,
            "page": page,
            "per_page": PER_PAGE,
            "total_pages": total_pages,
            "prev_url": prev_url,
            "next_url": next_url,
            "sort": sort,
            "dir": dir_,
            "sort_urls": sort_urls,
        },
    )
