"""GET /search — Cerca per paraula clau en fitxers i/o carpetes.

La lògica de paràmetres i execució és a app/search_params.py (compartida amb els espais
intel·ligents); la pàgina hi afegeix paginació, ordenació i el formulari «Desa com a espai».
"""
import json
import sqlite3
from math import ceil

from fastapi import Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from app import queries, queries_spaces
from app.search_params import (  # noqa: F401  (reexportats per compatibilitat)
    _file_terms,
    _parse_search_terms,
    has_search_criteria,
    parse_search_params,
    run_file_search,
    run_folder_search,
)

PER_PAGE = 100

FILES_SORT_COLUMNS = list(queries.SEARCH_INVENTORY_SORT_COLUMNS)
FOLDERS_SORT_COLUMNS = list(queries.FOLDER_STATS_SORT_COLUMNS)


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
    p = parse_search_params(request.query_params, request.query_params.getlist)
    name_terms, path_terms, flex_terms, dual_typed = _file_terms(p)
    search_type = p["search_type"]

    page_files = max(1, int(request.query_params.get("page", 1)))
    page_folders = max(1, int(request.query_params.get("page_f", 1)))
    offset_files = (page_files - 1) * PER_PAGE
    offset_folders = (page_folders - 1) * PER_PAGE

    all_drive_ids = queries.get_drive_ids(conn)
    extensions = queries.get_extensions_for_filter(conn)
    rows_files: list = []
    total_files = 0
    total_pages_files = 0
    prev_url_files = next_url_files = None
    rows_folders: list = []
    total_folders = 0
    total_pages_folders = 0
    prev_url_folders = next_url_folders = None

    search_error: str | None = None
    scoped = bool(p["in_drive"])
    if dual_typed and not name_terms and not path_terms and not scoped:
        search_error = (
            "Els camps «Nom del fitxer» i «Ruta» no contenen termes vàlids. "
            "Ompliu almenys un fragment o useu la cerca en un sol camp."
        )
    elif not dual_typed and p["q"] and not flex_terms and not name_terms and not path_terms and not scoped:
        search_error = "El camp de cerca clàssica no conté cap terme vàlid."

    search_executed = False
    if search_error is None and has_search_criteria(p):
        search_executed = True
        if search_type in ("files", "both"):
            rows_files, total_files = run_file_search(conn, p, PER_PAGE, offset_files)
            total_pages_files = max(1, ceil(total_files / PER_PAGE)) if total_files else 0
            prev_url_files, next_url_files = _pagination_urls(
                request, page_files, total_pages_files, "page"
            )
        if search_type in ("folders", "both"):
            rows_folders, total_folders = run_folder_search(conn, p, PER_PAGE, offset_folders)
            total_pages_folders = max(1, ceil(total_folders / PER_PAGE)) if total_folders else 0
            prev_url_folders, next_url_folders = _pagination_urls(
                request, page_folders, total_pages_folders, "page_f"
            )

    search_attempted = dual_typed or bool(p["q"]) or scoped
    sort_f = p["sort"] or None
    dir_f = p["dir"]
    sort_folders = p["sort_folders"] or None
    dir_folders = p["dir_folders"]

    def _sort_url_files(col: str) -> str:
        next_dir = "desc" if (sort_f == col and dir_f == "asc") else "asc"
        return str(request.url.include_query_params(sort_f=col, dir_f=next_dir))

    def _sort_url_folders(col: str) -> str:
        next_dir = "desc" if (sort_folders == col and dir_folders == "asc") else "asc"
        return str(request.url.include_query_params(sort_folders=col, dir_folders=next_dir))

    sort_urls_files = {col: _sort_url_files(col) for col in FILES_SORT_COLUMNS}
    sort_urls_folders = {col: _sort_url_folders(col) for col in FOLDERS_SORT_COLUMNS}
    clear_scope_url = str(request.url.remove_query_params(["in_drive", "in_path", "page", "page_f"])) if scoped else None

    # «Desa com a espai»: llista d'espais per triar el pare i, si s'editen els criteris d'un
    # espai existent (?space=ID), l'espai a actualitzar
    spaces_flat = queries_spaces.flatten_tree(
        queries_spaces.build_tree(queries_spaces.list_spaces(conn))
    )
    editing_raw = request.query_params.get("space", "").strip()
    editing_space = queries_spaces.get_space(conn, int(editing_raw)) if editing_raw.isdigit() else None
    if editing_space is not None and editing_space["kind"] != "smart":
        editing_space = None
    default_space_name = " ".join(name_terms + path_terms + flex_terms)
    if not default_space_name and scoped:
        default_space_name = p["in_drive"] + (" › " + p["in_path"].replace("/", " › ") if p["in_path"] else "")
    query_json = json.dumps(p, ensure_ascii=False)
    tags = queries_spaces.list_tags(conn)

    return templates.TemplateResponse(request, "search.html",
        {
            "request": request,
            "q": p["q"],
            "q_name": p["q_name"],
            "q_path": p["q_path"],
            "search_error": search_error,
            "search_executed": search_executed,
            "search_attempted": search_attempted,
            "selected_drive_ids": p["drive_ids"],
            "drive_ids": all_drive_ids,
            "extensions": extensions,
            "extension": p["extension"],
            "search_type": search_type,
            "scope": p["scope"],
            "max_depth": p["max_depth"],
            "min_size_mb": p["min_size_mb"],
            "include_missing": p["include_missing"],
            "min_size_folders_mb": p["min_size_folders_mb"],
            "exclude": p["exclude"],
            "in_drive": p["in_drive"],
            "in_path": p["in_path"],
            "clear_scope_url": clear_scope_url,
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
            "spaces_flat": spaces_flat,
            "editing_space": editing_space,
            "default_space_name": default_space_name,
            "query_json": query_json,
            "tags": tags,
            "tag_id": p["tag_id"],
            "only_single_copy": p["only_single_copy"],
            "only_duplicates": p["only_duplicates"],
        },
    )
