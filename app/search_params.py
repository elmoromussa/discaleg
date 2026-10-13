"""Paràmetres de cerca normalitzats i execució de la cerca de fitxers i carpetes.

`parse_search_params` converteix els paràmetres d'una URL (o el JSON d'una cerca desada
d'un espai virtual) en un diccionari pla i serialitzable; `run_file_search` i
`run_folder_search` l'executen. Ho fan servir la pàgina Cercar i els espais intel·ligents.
"""
from __future__ import annotations

import sqlite3
from typing import Any, Mapping

from app import queries


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


def _float_or_none(raw: str) -> float | None:
    try:
        return float(raw) if raw else None
    except ValueError:
        return None


def parse_search_params(qp: Mapping[str, Any], getlist=None) -> dict[str, Any]:
    """Normalitza els paràmetres de cerca (URL o JSON d'una cerca desada) en un diccionari pla.

    Claus: q_name, q_path, q, scope, drive_ids, extension, min_size_mb, exclude,
    include_missing, in_drive, in_path, search_type, max_depth, min_size_folders_mb,
    sort, dir (fitxers), sort_folders, dir_folders. Tot són cadenes/llistes simples
    perquè es pugui serialitzar tal qual.
    """
    def get(key: str, default: str = "") -> str:
        v = qp.get(key, default)
        if isinstance(v, list):
            v = v[0] if v else default
        return "" if v is None else str(v)

    if getlist is not None:
        drive_ids = [x for x in getlist("drive_ids") if x and str(x).strip()]
    else:
        raw = qp.get("drive_ids") or []
        drive_ids = [str(x) for x in (raw if isinstance(raw, list) else [raw]) if x and str(x).strip()]

    scope = get("scope", "name").strip().lower() or "name"
    if scope not in queries.SEARCH_SCOPE_VALUES:
        scope = "name"
    search_type = get("search_type", "files").strip() or "files"
    if search_type not in ("files", "folders", "both"):
        search_type = "files"
    in_drive = get("in_drive").strip()
    in_path = get("in_path").strip().replace("\\", "/").strip("/") if in_drive else ""
    sort = get("sort_f").strip() or get("sort").strip() or ""
    direction = (get("dir_f").strip() or get("dir").strip() or "asc").lower()
    sort_folders = get("sort_folders").strip()
    dir_folders = (get("dir_folders").strip() or "asc").lower()
    return {
        "q_name": get("q_name").strip(),
        "q_path": get("q_path").strip(),
        "q": get("q").strip(),
        "scope": scope,
        "drive_ids": drive_ids,
        "extension": (get("extension") or "").strip(),
        "min_size_mb": get("min_size_mb").strip(),
        "exclude": get("exclude").strip(),
        "include_missing": get("include_missing").strip() in ("1", "true", "True"),
        "in_drive": in_drive,
        "in_path": in_path,
        "search_type": search_type,
        "max_depth": get("max_depth").strip(),
        "min_size_folders_mb": get("min_size_folders_mb").strip(),
        "sort": sort if sort in queries.SEARCH_INVENTORY_SORT_COLUMNS else "",
        "dir": direction if direction in ("asc", "desc") else "asc",
        "sort_folders": sort_folders if sort_folders in queries.FOLDER_STATS_SORT_COLUMNS else "",
        "dir_folders": dir_folders if dir_folders in ("asc", "desc") else "asc",
    }


def _file_terms(p: dict[str, Any]) -> tuple[list[str], list[str], list[str], bool]:
    """(name_terms, path_terms, flex_terms, dual_typed) segons el mode de la cerca."""
    name_terms = _parse_search_terms(p["q_name"])
    path_terms = _parse_search_terms(p["q_path"])
    dual_typed = bool(p["q_name"] or p["q_path"])
    if dual_typed:
        return name_terms, path_terms, [], True
    legacy = _parse_search_terms(p["q"])
    if p["scope"] == "both":
        return [], [], legacy, False
    if p["scope"] == "path":
        return [], legacy, [], False
    return legacy, [], [], False


def has_search_criteria(p: dict[str, Any]) -> bool:
    """True si la cerca té prou criteris per executar-se (termes vàlids o un àmbit de carpeta)."""
    name_terms, path_terms, flex_terms, _ = _file_terms(p)
    return bool(name_terms or path_terms or flex_terms or p["in_drive"])


def run_file_search(
    conn: sqlite3.Connection,
    p: dict[str, Any],
    limit: int,
    offset: int,
    sort_by: str | None = None,
    sort_dir: str | None = None,
) -> tuple[list[dict[str, Any]], int]:
    """Executa la cerca de fitxers descrita per `p` (vegeu parse_search_params)."""
    name_terms, path_terms, flex_terms, _ = _file_terms(p)
    min_size_mb = _float_or_none(p["min_size_mb"])
    extensions = _parse_extensions(p["extension"])
    return queries.search_inventory(
        conn,
        p["drive_ids"] or None,
        limit=limit,
        offset=offset,
        min_size_bytes=int(min_size_mb * 1024 * 1024) if min_size_mb and min_size_mb > 0 else 0,
        exclude_terms=_parse_exclude(p["exclude"]),
        extensions=extensions or None,
        sort_by=sort_by if sort_by is not None else (p["sort"] or None),
        sort_dir=sort_dir if sort_dir is not None else p["dir"],
        name_terms=name_terms,
        path_terms=path_terms,
        flex_terms=flex_terms,
        include_missing=p["include_missing"],
        in_drive=p["in_drive"] or None,
        in_path=p["in_path"] or None,
    )


def run_folder_search(
    conn: sqlite3.Connection, p: dict[str, Any], limit: int, offset: int
) -> tuple[list[dict[str, Any]], int]:
    """Executa la cerca de carpetes descrita per `p`."""
    name_terms, path_terms, flex_terms, _ = _file_terms(p)
    min_size_mb = _float_or_none(p["min_size_folders_mb"])
    max_depth = int(p["max_depth"]) if p["max_depth"].isdigit() else None
    return queries.search_folders(
        conn,
        name_terms + path_terms + flex_terms,
        p["drive_ids"] or None,
        limit=limit,
        offset=offset,
        max_depth=max_depth,
        min_size_bytes=int(min_size_mb * 1024 * 1024) if min_size_mb and min_size_mb > 0 else 0,
        exclude_terms=_parse_exclude(p["exclude"]),
        sort_by=p["sort_folders"] or None,
        sort_dir=p["dir_folders"],
        in_drive=p["in_drive"] or None,
        in_path=p["in_path"] or None,
    )
