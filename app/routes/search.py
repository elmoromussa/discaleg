"""GET /search — Cerca per paraula clau en fitxers i/o carpetes."""
import sqlite3
from math import ceil
from fastapi import Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from app import queries

PER_PAGE = 100

FILES_SORT_COLUMNS = list(queries.SEARCH_INVENTORY_SORT_COLUMNS)
FOLDERS_SORT_COLUMNS = list(queries.FOLDER_STATS_SORT_COLUMNS)


def _parse_search_terms(q: str) -> list[str]:
    """Extreu termes de cerca (AND): separadors espais, comes, +, salts de línia i tabuladors."""
    if not q or not q.strip():
        return []
    normalized = q.replace("\n", " ").replace("\t", " ").replace(",", " ").replace("+", " ")
    return [t for t in normalized.split() if t]


def _parse_exclude(exclude: str) -> list[str]:
    """Converteix el camp exclude (comes o salts de línia) en llista de termes."""
    if not exclude or not exclude.strip():
        return []
    terms = []
    for part in exclude.replace("\n", ",").split(","):
        t = part.strip()
        if t:
            terms.append(t)
    return terms


def _parse_extensions(extension: str | None) -> list[str]:
    """Converteix el camp extension (comes o salts de línia) en llista d'extensions normalitzades."""
    if not extension or not extension.strip():
        return []
    out = []
    for part in extension.replace("\n", ",").split(","):
        t = part.strip().lstrip(".").strip()
        if t:
            out.append(t)
    return out


def _pagination_urls(
    request: Request,
    page: int,
    total_pages: int,
    param_name: str = "page",
) -> tuple[str | None, str | None]:
    params = dict(request.query_params)
    prev_url = next_url = None
    if page > 1:
        params[param_name] = page - 1
        prev_url = str(request.url.include_query_params(**params))
    if page < total_pages:
        params[param_name] = page + 1
        next_url = str(request.url.include_query_params(**params))
    return prev_url, next_url


def search_page(
    request: Request,
    templates: Jinja2Templates,
    conn: sqlite3.Connection,
) -> HTMLResponse:
    q = request.query_params.get("q", "").strip()
    q_name_raw = request.query_params.get("q_name", "").strip()
    q_path_raw = request.query_params.get("q_path", "").strip()
    selected_drive_ids = [
        x for x in request.query_params.getlist("drive_ids")
        if x and x.strip()
    ]
    drive_ids_filter: list[str] | None = selected_drive_ids if selected_drive_ids else None
    search_type = request.query_params.get("search_type", "files").strip() or "files"
    if search_type not in ("files", "folders", "both"):
        search_type = "files"
    scope = request.query_params.get("scope", "name").strip().lower() or "name"
    if scope not in queries.SEARCH_SCOPE_VALUES:
        scope = "name"
    max_depth_raw = request.query_params.get("max_depth", "").strip()
    max_depth = int(max_depth_raw) if max_depth_raw.isdigit() else None
    min_size_mb_raw = request.query_params.get("min_size_mb", "").strip()
    try:
        min_size_mb = float(min_size_mb_raw) if min_size_mb_raw else None
    except ValueError:
        min_size_mb = None
    min_size_folders_mb_raw = request.query_params.get("min_size_folders_mb", "").strip()
    try:
        min_size_folders_mb = float(min_size_folders_mb_raw) if min_size_folders_mb_raw else None
    except ValueError:
        min_size_folders_mb = None
    exclude_raw = request.query_params.get("exclude", "").strip()
    exclude_terms = _parse_exclude(exclude_raw)
    extension_raw = request.query_params.get("extension") or ""
    extensions_filter = _parse_extensions(extension_raw)
    extension = extension_raw.strip() or ""

    sort_f = request.query_params.get("sort_f", "").strip() or None
    dir_f = request.query_params.get("dir_f", "asc").strip().lower()
    if sort_f not in queries.SEARCH_INVENTORY_SORT_COLUMNS:
        sort_f = None
    if dir_f not in ("asc", "desc"):
        dir_f = "asc"

    sort_folders = request.query_params.get("sort_folders", "").strip() or None
    dir_folders = request.query_params.get("dir_folders", "asc").strip().lower()
    if sort_folders not in queries.FOLDER_STATS_SORT_COLUMNS:
        sort_folders = None
    if dir_folders not in ("asc", "desc"):
        dir_folders = "asc"

    page_files = max(1, int(request.query_params.get("page", 1)))
    page_folders = max(1, int(request.query_params.get("page_f", 1)))
    offset_files = (page_files - 1) * PER_PAGE
    offset_folders = (page_folders - 1) * PER_PAGE

    all_drive_ids = queries.get_drive_ids(conn)
    extensions = queries.get_extensions_for_filter(conn)
    rows_files = []
    total_files = 0
    total_pages_files = 0
    prev_url_files = next_url_files = None
    rows_folders = []
    total_folders = 0
    total_pages_folders = 0
    prev_url_folders = next_url_folders = None

    min_size_bytes = int(min_size_mb * 1024 * 1024) if min_size_mb is not None and min_size_mb > 0 else 0
    min_size_folders_bytes = (
        int(min_size_folders_mb * 1024 * 1024)
        if min_size_folders_mb is not None and min_size_folders_mb > 0
        else 0
    )

    name_terms_dual = _parse_search_terms(q_name_raw)
    path_terms_dual = _parse_search_terms(q_path_raw)
    legacy_terms = _parse_search_terms(q)
    dual_typed = bool(q_name_raw or q_path_raw)

    search_error: str | None = None
    if dual_typed:
        if not name_terms_dual and not path_terms_dual:
            search_error = (
                "Els camps «Nom del fitxer» i «Ruta» no contenen termes vàlids. "
                "Ompliu almenys un fragment o useu la cerca en un sol camp."
            )
    elif q and not legacy_terms:
        search_error = "El camp de cerca clàssica no conté cap terme vàlid."

    search_executed = False
    if search_error is None:
        if dual_typed and (name_terms_dual or path_terms_dual):
            search_executed = True
            if search_type in ("files", "both"):
                rows_files, total_files = queries.search_inventory(
                    conn,
                    drive_ids_filter,
                    limit=PER_PAGE,
                    offset=offset_files,
                    min_size_bytes=min_size_bytes,
                    exclude_terms=exclude_terms,
                    extensions=extensions_filter if extensions_filter else None,
                    sort_by=sort_f,
                    sort_dir=dir_f,
                    name_terms=name_terms_dual,
                    path_terms=path_terms_dual,
                    flex_terms=None,
                )
                total_pages_files = max(1, ceil(total_files / PER_PAGE)) if total_files else 0
                prev_url_files, next_url_files = _pagination_urls(
                    request, page_files, total_pages_files, "page"
                )
            if search_type in ("folders", "both"):
                folder_terms = name_terms_dual + path_terms_dual
                rows_folders, total_folders = queries.search_folders(
                    conn,
                    folder_terms,
                    drive_ids_filter,
                    limit=PER_PAGE,
                    offset=offset_folders,
                    max_depth=max_depth,
                    min_size_bytes=min_size_folders_bytes,
                    exclude_terms=exclude_terms,
                    sort_by=sort_folders,
                    sort_dir=dir_folders,
                )
                total_pages_folders = max(1, ceil(total_folders / PER_PAGE)) if total_folders else 0
                prev_url_folders, next_url_folders = _pagination_urls(
                    request, page_folders, total_pages_folders, "page_f"
                )
        elif legacy_terms:
            search_executed = True
            inv_name: list[str] = []
            inv_path: list[str] = []
            inv_flex: list[str] = []
            if scope == "both":
                inv_flex = legacy_terms
            elif scope == "name":
                inv_name = legacy_terms
            else:
                inv_path = legacy_terms

            if search_type in ("files", "both"):
                rows_files, total_files = queries.search_inventory(
                    conn,
                    drive_ids_filter,
                    limit=PER_PAGE,
                    offset=offset_files,
                    min_size_bytes=min_size_bytes,
                    exclude_terms=exclude_terms,
                    extensions=extensions_filter if extensions_filter else None,
                    sort_by=sort_f,
                    sort_dir=dir_f,
                    name_terms=inv_name,
                    path_terms=inv_path,
                    flex_terms=inv_flex,
                )
                total_pages_files = max(1, ceil(total_files / PER_PAGE)) if total_files else 0
                prev_url_files, next_url_files = _pagination_urls(
                    request, page_files, total_pages_files, "page"
                )
            if search_type in ("folders", "both"):
                rows_folders, total_folders = queries.search_folders(
                    conn,
                    legacy_terms,
                    drive_ids_filter,
                    limit=PER_PAGE,
                    offset=offset_folders,
                    max_depth=max_depth,
                    min_size_bytes=min_size_folders_bytes,
                    exclude_terms=exclude_terms,
                    sort_by=sort_folders,
                    sort_dir=dir_folders,
                )
                total_pages_folders = max(1, ceil(total_folders / PER_PAGE)) if total_folders else 0
                prev_url_folders, next_url_folders = _pagination_urls(
                    request, page_folders, total_pages_folders, "page_f"
                )

    search_attempted = dual_typed or bool(q)

    def _sort_url_files(col: str) -> str:
        next_dir = "desc" if (sort_f == col and dir_f == "asc") else "asc"
        return str(request.url.include_query_params(sort_f=col, dir_f=next_dir))

    def _sort_url_folders(col: str) -> str:
        next_dir = "desc" if (sort_folders == col and dir_folders == "asc") else "asc"
        return str(request.url.include_query_params(sort_folders=col, dir_folders=next_dir))

    sort_urls_files = {col: _sort_url_files(col) for col in FILES_SORT_COLUMNS}
    sort_urls_folders = {col: _sort_url_folders(col) for col in FOLDERS_SORT_COLUMNS}

    return templates.TemplateResponse(
        "search.html",
        {
            "request": request,
            "q": q,
            "q_name": q_name_raw,
            "q_path": q_path_raw,
            "search_error": search_error,
            "search_executed": search_executed,
            "search_attempted": search_attempted,
            "selected_drive_ids": selected_drive_ids,
            "drive_ids": all_drive_ids,
            "extensions": extensions,
            "extension": extension,
            "search_type": search_type,
            "scope": scope,
            "max_depth": max_depth_raw,
            "min_size_mb": min_size_mb_raw or "",
            "min_size_folders_mb": min_size_folders_mb_raw or "",
            "exclude": exclude_raw,
            "sort_f": sort_f,
            "dir_f": dir_f,
            "sort_folders": sort_folders,
            "dir_folders": dir_folders,
            "sort_urls_files": sort_urls_files,
            "sort_urls_folders": sort_urls_folders,
            "rows_files": rows_files,
            "total_files": total_files,
            "page_files": page_files,
            "total_pages_files": total_pages_files,
            "prev_url_files": prev_url_files,
            "next_url_files": next_url_files,
            "rows_folders": rows_folders,
            "total_folders": total_folders,
            "page_folders": page_folders,
            "total_pages_folders": total_pages_folders,
            "prev_url_folders": prev_url_folders,
            "next_url_folders": next_url_folders,
            "per_page": PER_PAGE,
        },
    )
