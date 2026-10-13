"""GET /duplicates — Duplicats de fitxers o carpetes (per nom o nom+mida)."""
import sqlite3
from math import ceil
from fastapi import Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from app import queries

PER_PAGE = 50
FILES_PER_GROUP = 50
FOLDERS_PER_GROUP = 50
FOCUS_FILES = 500  # fitxers mostrats en mode «grup» (un sol grup, des de l'explorador)


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


def _parse_exclude(exclude: str) -> list[str]:
    if not exclude or not exclude.strip():
        return []
    terms = []
    for part in exclude.replace("\n", ",").split(","):
        t = part.strip()
        if t:
            terms.append(t)
    return terms


def _parse_include(include: str) -> list[str]:
    if not include or not include.strip():
        return []
    terms = []
    for part in include.replace("\n", ",").split(","):
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


def duplicates_page(
    request: Request,
    templates: Jinja2Templates,
    conn: sqlite3.Connection,
) -> HTMLResponse:
    # Mode «grup»: un sol grup de duplicats d'un fitxer concret (enllaç des de l'explorador)
    group = (request.query_params.get("group") or "").strip().lower()
    focus = None
    if group in ("hash", "name-size", "name"):
        if group == "hash":
            h = (request.query_params.get("hash") or "").strip().lower()
            files = queries.get_duplicate_hash_files(conn, h, limit=FOCUS_FILES) if h else []
            focus = {"kind": "hash", "hash": h, "name": files[0]["name"] if files else "",
                     "size_bytes": files[0]["size_bytes"] if files else None, "files": files}
        elif group == "name-size":
            name = (request.query_params.get("name") or "").strip()
            size_raw = (request.query_params.get("size") or "").strip()
            size = int(size_raw) if size_raw.isdigit() else None
            files = queries.get_duplicate_name_size_files(conn, name, size, limit=FOCUS_FILES) if name and size is not None else []
            focus = {"kind": "name-size", "name": name, "size_bytes": size, "files": files}
        else:
            name = (request.query_params.get("name") or "").strip()
            files = queries.get_duplicate_name_files(conn, name, limit=FOCUS_FILES) if name else []
            focus = {"kind": "name", "name": name, "size_bytes": None, "files": files}
        focus["ndrives"] = len({f["drive_id"] for f in focus["files"]})
        for f in focus["files"]:
            rp = f["relative_path"]
            f["folder"] = rp[: rp.rfind("\\")].replace("\\", "/") if "\\" in rp else ""

    type_ = (request.query_params.get("type") or "files").strip().lower()
    if type_ not in ("files", "folders"):
        type_ = "files"
    # en mode «grup» el formulari queda preparat amb el mateix criteri
    mode = (group if focus else (request.query_params.get("mode") or "name")).strip().lower()
    if mode not in ("name", "name-size", "hash"):
        mode = "name"
    if type_ == "folders" and mode == "hash":
        mode = "name"
    hash_scope = (request.query_params.get("hash_scope") or "any").strip().lower()
    if hash_scope not in ("any", "cross"):
        hash_scope = "any"
    submitted = request.query_params.get("submitted") == "1"
    page = max(1, int(request.query_params.get("page", 1)))
    offset = (page - 1) * PER_PAGE

    selected_drive_ids = [
        x for x in request.query_params.getlist("drive_ids")
        if x and x.strip()
    ]
    drive_ids_filter: list[str] | None = selected_drive_ids if selected_drive_ids else None
    exclude_raw = request.query_params.get("exclude", "").strip()
    exclude_terms = _parse_exclude(exclude_raw)
    include_raw = request.query_params.get("include", "").strip()
    include_terms = _parse_include(include_raw)
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
    min_size_folders_bytes = (
        int(min_size_folders_mb * 1024 * 1024)
        if min_size_folders_mb is not None and min_size_folders_mb > 0
        else 0
    )
    min_size_bytes = (
        int(min_size_mb * 1024 * 1024)
        if min_size_mb is not None and min_size_mb > 0
        else 0
    )
    extension_raw = request.query_params.get("extension") or ""
    extensions_filter = _parse_extensions(extension_raw)
    extension = extension_raw.strip() or ""

    sort_dup = request.query_params.get("sort_dup", "").strip() or None
    dir_dup = request.query_params.get("dir_dup", "asc").strip().lower()
    if dir_dup not in ("asc", "desc"):
        dir_dup = "asc"
    if type_ == "files":
        if mode == "hash":
            dup_sort_columns = queries.DUPLICATE_HASH_SORT_COLUMNS
        elif mode == "name-size":
            dup_sort_columns = queries.DUPLICATE_NAME_SIZE_SORT_COLUMNS
        else:
            dup_sort_columns = queries.DUPLICATE_NAMES_SORT_COLUMNS
    else:
        dup_sort_columns = (
            queries.DUPLICATE_FOLDERS_PATH_SIZE_SORT_COLUMNS
            if mode == "name-size"
            else queries.DUPLICATE_FOLDERS_PATH_SORT_COLUMNS
        )
    if sort_dup not in dup_sort_columns:
        sort_dup = None

    all_drive_ids = queries.get_drive_ids(conn)
    extensions = queries.get_extensions_for_filter(conn)
    hash_coverage = queries.get_hash_coverage(conn)
    groups_with_entries = []
    total = 0
    total_pages = 0
    prev_url = next_url = None

    if submitted:
        if type_ == "files":
            if mode == "hash":
                groups, total = queries.get_duplicate_hash_groups(
                    conn,
                    limit=PER_PAGE,
                    offset=offset,
                    drive_ids=drive_ids_filter,
                    min_size_bytes=min_size_bytes,
                    extensions=extensions_filter if extensions_filter else None,
                    exclude_terms=exclude_terms,
                    include_terms=include_terms,
                    cross_drive_only=(hash_scope == "cross"),
                    sort_by=sort_dup,
                    sort_dir=dir_dup,
                )
                total_pages = max(1, ceil(total / PER_PAGE)) if total else 0
                prev_url, next_url = _pagination_urls(request, page, total_pages)
                for g in groups:
                    files = queries.get_duplicate_hash_files(
                        conn, g["hash_sha256"], limit=FILES_PER_GROUP
                    )
                    groups_with_entries.append({**g, "files": files})
            elif mode == "name":
                groups, total = queries.get_duplicate_names_groups(
                    conn,
                    limit=PER_PAGE,
                    offset=offset,
                    drive_ids=drive_ids_filter,
                    min_size_bytes=min_size_bytes,
                    extensions=extensions_filter if extensions_filter else None,
                    exclude_terms=exclude_terms,
                    include_terms=include_terms,
                    sort_by=sort_dup,
                    sort_dir=dir_dup,
                )
                total_pages = max(1, ceil(total / PER_PAGE)) if total else 0
                prev_url, next_url = _pagination_urls(request, page, total_pages)
                for g in groups:
                    files = queries.get_duplicate_name_files(
                        conn, g["name"], limit=FILES_PER_GROUP
                    )
                    groups_with_entries.append({**g, "files": files})
            else:
                groups, total = queries.get_duplicate_name_size_groups(
                    conn,
                    limit=PER_PAGE,
                    offset=offset,
                    drive_ids=drive_ids_filter,
                    min_size_bytes=min_size_bytes,
                    extensions=extensions_filter if extensions_filter else None,
                    exclude_terms=exclude_terms,
                    include_terms=include_terms,
                    sort_by=sort_dup,
                    sort_dir=dir_dup,
                )
                total_pages = max(1, ceil(total / PER_PAGE)) if total else 0
                prev_url, next_url = _pagination_urls(request, page, total_pages)
                for g in groups:
                    files = queries.get_duplicate_name_size_files(
                        conn, g["name"], g["size_bytes"], limit=FILES_PER_GROUP
                    )
                    groups_with_entries.append({**g, "files": files})
        else:
            if mode == "name":
                groups, total = queries.get_duplicate_folders_by_path_groups(
                    conn,
                    limit=PER_PAGE,
                    offset=offset,
                    drive_ids=drive_ids_filter,
                    max_depth=max_depth,
                    min_size_bytes=min_size_folders_bytes,
                    exclude_terms=exclude_terms,
                    include_terms=include_terms,
                    sort_by=sort_dup,
                    sort_dir=dir_dup,
                )
                total_pages = max(1, ceil(total / PER_PAGE)) if total else 0
                prev_url, next_url = _pagination_urls(request, page, total_pages)
                for g in groups:
                    entries = queries.get_duplicate_folder_by_path_entries(
                        conn, g["folder_path"], limit=FOLDERS_PER_GROUP
                    )
                    groups_with_entries.append({**g, "entries": entries})
            else:
                groups, total = queries.get_duplicate_folders_by_path_size_groups(
                    conn,
                    limit=PER_PAGE,
                    offset=offset,
                    drive_ids=drive_ids_filter,
                    max_depth=max_depth,
                    min_size_bytes=min_size_folders_bytes,
                    exclude_terms=exclude_terms,
                    include_terms=include_terms,
                    sort_by=sort_dup,
                    sort_dir=dir_dup,
                )
                total_pages = max(1, ceil(total / PER_PAGE)) if total else 0
                prev_url, next_url = _pagination_urls(request, page, total_pages)
                for g in groups:
                    entries = queries.get_duplicate_folder_by_path_size_entries(
                        conn, g["folder_path"], g["total_bytes"], limit=FOLDERS_PER_GROUP
                    )
                    groups_with_entries.append({**g, "entries": entries})

    def _sort_url_dup(col: str) -> str:
        next_dir = "desc" if (sort_dup == col and dir_dup == "asc") else "asc"
        return str(request.url.include_query_params(sort_dup=col, dir_dup=next_dir))

    sort_urls_duplicates = {col: _sort_url_dup(col) for col in dup_sort_columns}
    SORT_LABELS = {
        "name": "Nom",
        "size_bytes": "Mida",
        "ndrives": "Nº discs",
        "total_files": "Nº fitxers",
        "folder_path": "Camí",
        "total_bytes": "Mida",
        "total_folders": "Nº carpetes",
        "wasted_bytes": "Espai recuperable",
    }
    sort_labels_duplicates = {col: SORT_LABELS.get(col, col) for col in dup_sort_columns}

    return templates.TemplateResponse(request, "duplicates.html",
        {
            "request": request,
            "type": type_,
            "mode": mode,
            "hash_scope": hash_scope,
            "hash_coverage": hash_coverage,
            "focus": focus,
            "submitted": submitted and focus is None,
            "groups": groups_with_entries,
            "total": total,
            "page": page,
            "per_page": PER_PAGE,
            "total_pages": total_pages,
            "prev_url": prev_url,
            "next_url": next_url,
            "drive_ids": all_drive_ids,
            "selected_drive_ids": selected_drive_ids,
            "extensions": extensions,
            "extension": extension,
            "exclude": exclude_raw,
            "include": include_raw,
            "max_depth": max_depth_raw,
            "min_size_mb": min_size_mb_raw or "",
            "min_size_folders_mb": min_size_folders_mb_raw or "",
            "sort_dup": sort_dup,
            "dir_dup": dir_dup,
            "sort_columns_duplicates": dup_sort_columns,
            "sort_urls_duplicates": sort_urls_duplicates,
            "sort_labels_duplicates": sort_labels_duplicates,
        },
    )
