"""GET/POST /discs — Gestió de discs: escanejar i incorporar, actualitzar o eliminar."""
import html
import logging
import re
import sqlite3
from pathlib import Path
from urllib.parse import quote

from fastapi import HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from app import queries, scan_jobs, schema
from app.audit_to_db import delete_drive, load_excludes, parse_exclude_lines
from app.config import database_path

# Longitud màxima del missatge d'error mostrat a la plantilla (evitar HTML massa llarg)
_MAX_ERROR_MSG_LEN = 500


def _sanitize_error_message(msg: str) -> str:
    """Redueix longitud i elimina caràcters que podrien afectar l'HTML."""
    if not msg:
        return "Error desconegut."
    s = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", msg)
    return (s[: _MAX_ERROR_MSG_LEN] + "…") if len(s) > _MAX_ERROR_MSG_LEN else s


def discs_page_get(
    request: Request,
    templates: Jinja2Templates,
    msg_ok: str | None = None,
    msg_error: str | None = None,
) -> HTMLResponse:
    """Pàgina Discs: llista de discs (si hi ha DB) i formulari d'escaneig."""
    drives: list[dict] = []
    scans: list[dict] = []
    error_counts: dict[str, int] = {}
    if database_path.is_file():
        try:
            conn = sqlite3.connect(str(database_path), timeout=10.0)
            conn.row_factory = sqlite3.Row
            try:
                if schema.is_legacy_db(conn):
                    msg_error = msg_error or (
                        "La base de dades té l'esquema antic. Executa «python -m app.migrate» "
                        "abans d'escanejar."
                    )
                else:
                    drives = queries.get_drives_summary(conn)
                    for d in drives:
                        d["excludes"] = load_excludes(d.get("exclude_paths"))
                    scans = queries.get_recent_scans(conn, limit=10)
                    error_counts = queries.get_read_error_counts(conn)
            finally:
                conn.close()
        except Exception as e:
            logging.exception("Error llegint la llista de discs")
            msg_error = msg_error or _sanitize_error_message(str(e))
    return templates.TemplateResponse(request, "discs.html",
        {
            "request": request,
            "drives": drives,
            "scans": scans,
            "error_counts": error_counts,
            "drive_kinds": schema.DRIVE_KINDS,
            "msg_ok": msg_ok,
            "msg_error": msg_error,
            "job": scan_jobs.get_status(),
        },
    )


CHANGES_PER_PAGE = 100

_CHANGE_LABELS = {"new": "Nous", "changed": "Modificats", "missing": "Desapareguts"}


def _parent_folder(relative_path: str) -> str:
    """Carpeta d'una ruta relativa de la DB, en format API («/») per a l'enllaç a l'explorador."""
    i = relative_path.rfind("\\")
    return relative_path[:i].replace("\\", "/") if i > 0 else ""


def scan_changes_page(
    request: Request,
    templates: Jinja2Templates,
    conn: sqlite3.Connection,
    drive_id: str,
) -> HTMLResponse:
    """Vista «què ha canviat» d'un disc: fitxers nous, modificats i desapareguts per escaneig."""
    drive = queries.get_drive(conn, drive_id)
    if drive is None:
        raise HTTPException(status_code=404, detail=f"El disc «{drive_id}» no és a la base de dades.")
    scans = queries.get_drive_scans(conn, drive_id)
    scan_raw = request.query_params.get("scan", "").strip()
    scan = None
    if scan_raw.isdigit():
        scan = next((s for s in scans if s["scan_id"] == int(scan_raw)), None)
    if scan is None and scans:
        scan = scans[0]
    change = request.query_params.get("tipus", "new").strip().lower()
    if change not in queries.SCAN_CHANGE_KINDS:
        change = "new"
    page = max(1, int(request.query_params.get("page", 1) or 1))

    counts = {k: 0 for k in queries.SCAN_CHANGE_KINDS}
    rows: list[dict] = []
    total = 0
    total_pages = 0
    prev_url = next_url = None
    has_detail = False
    if scan is not None:
        counts = queries.get_scan_change_counts(conn, scan["scan_id"])
        has_detail = any(counts.values())
        total = counts[change]
        total_pages = max(1, -(-total // CHANGES_PER_PAGE)) if total else 0
        page = min(page, total_pages) if total_pages else 1
        rows = queries.get_scan_changes(
            conn, scan["scan_id"], change, CHANGES_PER_PAGE, (page - 1) * CHANGES_PER_PAGE
        )
        for r in rows:
            r["folder"] = _parent_folder(r["relative_path"])
            r["name"] = r["relative_path"].rsplit("\\", 1)[-1]
        params = dict(request.query_params)
        params["scan"] = str(scan["scan_id"])
        params["tipus"] = change
        if page > 1:
            params["page"] = str(page - 1)
            prev_url = str(request.url.include_query_params(**params))
        if page < total_pages:
            params["page"] = str(page + 1)
            next_url = str(request.url.include_query_params(**params))

    def tab_url(kind: str) -> str:
        return str(request.url.include_query_params(
            scan=scan["scan_id"] if scan else "", tipus=kind, page=1))

    return templates.TemplateResponse(request, "scan_changes.html",
        {
            "request": request,
            "drive": drive,
            "drive_kinds": schema.DRIVE_KINDS,
            "scans": scans,
            "scan": scan,
            "change": change,
            "change_labels": _CHANGE_LABELS,
            "counts": counts,
            "has_detail": has_detail,
            "rows": rows,
            "total": total,
            "page": page,
            "total_pages": total_pages,
            "prev_url": prev_url,
            "next_url": next_url,
            "tab_urls": {k: tab_url(k) for k in queries.SCAN_CHANGE_KINDS},
            "per_page": CHANGES_PER_PAGE,
        },
    )


ERRORS_PER_PAGE = 100


def read_errors_page(
    request: Request,
    templates: Jinja2Templates,
    conn: sqlite3.Connection,
    drive_id: str,
) -> HTMLResponse:
    """Carpetes i fitxers que l'escaneig o el hash no han pogut llegir, per disc.

    Filtres: ?scan= (un escaneig; sense valor, tots, inclosos els errors del hash, que no
    tenen escaneig), ?fase= (scandir, entry, hash_stat, hash_read), ?page=.
    """
    drive = queries.get_drive(conn, drive_id)
    if drive is None:
        raise HTTPException(status_code=404, detail=f"El disc «{drive_id}» no és a la base de dades.")
    scans = queries.get_drive_scans(conn, drive_id)
    scan_raw = request.query_params.get("scan", "").strip()
    scan_id = int(scan_raw) if scan_raw.isdigit() else None
    scan = next((s for s in scans if s["scan_id"] == scan_id), None) if scan_id is not None else None
    if scan_id is not None and scan is None:
        scan_id = None
    phase = request.query_params.get("fase", "").strip().lower()
    if phase not in schema.READ_ERROR_PHASES:
        phase = ""
    page = max(1, int(request.query_params.get("page", 1) or 1))

    counts = queries.get_read_error_phase_counts(conn, drive_id, scan_id)
    total = counts.get(phase, 0) if phase else sum(counts.values())
    total_pages = max(1, -(-total // ERRORS_PER_PAGE)) if total else 0
    page = min(page, total_pages) if total_pages else 1
    rows = queries.get_read_errors(
        conn, drive_id, phase or None, scan_id, ERRORS_PER_PAGE, (page - 1) * ERRORS_PER_PAGE
    )
    for r in rows:
        if r["phase"] == "scandir":  # la ruta és una carpeta
            r["folder"] = r["relative_path"].replace("\\", "/")
            r["name"] = ""
        else:
            r["folder"] = _parent_folder(r["relative_path"])
            r["name"] = r["relative_path"].rsplit("\\", 1)[-1]

    base_params = {"scan": str(scan_id) if scan_id is not None else "", "fase": phase}
    prev_url = next_url = None
    if page > 1:
        prev_url = str(request.url.include_query_params(**base_params, page=page - 1))
    if page < total_pages:
        next_url = str(request.url.include_query_params(**base_params, page=page + 1))

    def tab_url(kind: str) -> str:
        return str(request.url.include_query_params(
            scan=base_params["scan"], fase=kind, page=1))

    return templates.TemplateResponse(request, "read_errors.html",
        {
            "request": request,
            "drive": drive,
            "drive_kinds": schema.DRIVE_KINDS,
            "scans": scans,
            "scan": scan,
            "scan_id": scan_id,
            "phase": phase,
            "phase_labels": schema.READ_ERROR_PHASES,
            "counts": counts,
            "rows": rows,
            "total": total,
            "page": page,
            "total_pages": total_pages,
            "prev_url": prev_url,
            "next_url": next_url,
            "tab_urls": {k: tab_url(k) for k in ("", *schema.READ_ERROR_PHASES)},
        },
    )


# Longituds màximes dels camps de la fitxa (evitar textos desmesurats a la DB)
_CARD_MAX = {"label": 120, "location": 200, "notes": 4000}


def _clean_field(value: str | None, name: str) -> str:
    """Normalitza un camp de text de la fitxa: espais, salts de línia i longitud."""
    s = (value or "").replace("\r\n", "\n").strip()
    if name != "notes":
        s = " ".join(s.split())
    limit = _CARD_MAX[name]
    return s[:limit] if len(s) > limit else s


async def discs_card_post(request: Request) -> RedirectResponse:
    """Desa la fitxa editable d'un disc (etiqueta, tipus, ubicació, notes) i torna a /discs."""
    try:
        form = await request.form()
        drive_id = (form.get("drive_id") or "").strip()
        if not drive_id:
            return RedirectResponse(
                url="/discs?error=" + quote("Cal indicar l'identificador del disc (drive_id)."),
                status_code=302,
            )
        if not database_path.is_file():
            return RedirectResponse(url="/discs?error=" + quote("No hi ha base de dades."), status_code=302)
        kind = (form.get("kind") or "").strip() or None
        if kind is not None and kind not in schema.DRIVE_KINDS:
            return RedirectResponse(
                url="/discs?error=" + quote(f"Tipus de disc desconegut: {kind}"), status_code=302
            )
        raw_excludes = form.get("exclude_paths")
        exclude_paths: list[str] | None = None
        if raw_excludes is not None:  # camp present: la llista del formulari substitueix la desada
            try:
                exclude_paths = parse_exclude_lines(str(raw_excludes))
            except ValueError as e:
                return RedirectResponse(
                    url="/discs?error=" + quote(f"Exclusions de «{drive_id}»: {e}") + "#disc-" + quote(drive_id),
                    status_code=302,
                )
        conn = sqlite3.connect(str(database_path), timeout=30.0)
        try:
            found = queries.update_drive_card(
                conn,
                drive_id,
                label=_clean_field(form.get("label"), "label"),
                kind=kind,
                location=_clean_field(form.get("location"), "location"),
                notes=_clean_field(form.get("notes"), "notes"),
                exclude_paths=exclude_paths,
            )
        finally:
            conn.close()
        if not found:
            return RedirectResponse(
                url="/discs?error=" + quote(f"El disc «{drive_id}» no és a la base de dades."),
                status_code=302,
            )
        return RedirectResponse(
            url="/discs?ok=" + quote(f"Fitxa de «{drive_id}» desada.") + "#disc-" + quote(drive_id),
            status_code=302,
        )
    except Exception as e:
        logging.exception("Error en POST /discs/fitxa")
        return RedirectResponse(
            url="/discs?error=" + quote(_sanitize_error_message(str(e))), status_code=302
        )


def _error_html_response(message: str) -> HTMLResponse:
    """Resposta d'error en HTML sense usar plantilles (evita 500 si Jinja2/url_for falla)."""
    err_escaped = html.escape(message)
    body = (
        "<!DOCTYPE html><html lang='ca'><head><meta charset='UTF-8'><title>Error</title></head><body>"
        "<h1>Error en processar l'escaneig</h1><p class='msg-error'>"
        + err_escaped
        + "</p><p><a href='/discs'>Tornar a Discs</a></p></body></html>"
    )
    return HTMLResponse(content=body, status_code=200)


async def discs_page_post(request: Request, templates: Jinja2Templates) -> HTMLResponse | RedirectResponse:
    """Processa el formulari: escanejar carpeta i incorporar/actualitzar a la DB."""
    try:
        form = await request.form()
        root_str = (form.get("root_path") or "").strip()
        drive_id = (form.get("drive_id") or "").strip()
        replace = form.get("replace") in ("on", "true", "1")
        mode = "replace" if replace else "update"

        if not drive_id:
            return _error_html_response("Cal indicar l'identificador del disc (drive_id).")

        root_path: Path | None = None
        if root_str:
            try:
                root_path = Path(root_str).resolve()
            except Exception:
                pass
        if not root_path or not root_path.is_dir():
            return _error_html_response(
                "La ruta de la carpeta o disc a escanejar no existeix o no és un directori."
            )

        # Exclusions del formulari: buit = fer servir (i conservar) la llista desada a la fitxa
        exclude_paths: list[str] | None = None
        raw_excludes = (form.get("exclude_paths") or "").strip()
        if raw_excludes:
            try:
                exclude_paths = parse_exclude_lines(raw_excludes, str(root_path))
            except ValueError as e:
                return _error_html_response(f"Exclusions: {e}")

        database_path.parent.mkdir(parents=True, exist_ok=True)
        # L'escaneig s'executa en segon pla; la pàgina Discs mostra el progrés (/api/scan/status)
        scan_jobs.start_scan(database_path, root_path, drive_id, mode, exclude_paths=exclude_paths)
        return RedirectResponse(url="/discs", status_code=302)
    except Exception as e:
        logging.exception("Error en POST /discs (escaneig)")
        return _error_html_response(_sanitize_error_message(str(e)))


async def discs_delete_post(request: Request) -> RedirectResponse:
    """Elimina una unitat (drive_id) de la base de dades i redirigeix a /discs."""
    try:
        form = await request.form()
        drive_id = (form.get("drive_id") or "").strip()
        if not drive_id:
            return RedirectResponse(
                url="/discs?error=" + quote("Cal indicar l'identificador del disc (drive_id)."),
                status_code=302,
            )
        if not database_path.is_file():
            return RedirectResponse(url="/discs?error=" + quote("No hi ha base de dades."), status_code=302)
        conn = sqlite3.connect(str(database_path), timeout=60.0)
        try:
            delete_drive(conn, drive_id)
        finally:
            conn.close()
        return RedirectResponse(
            url=f"/discs?ok={quote('Unitat «' + drive_id + '» eliminada de la base de dades.')}",
            status_code=302,
        )
    except Exception as e:
        logging.exception("Error en POST /discs/delete")
        return RedirectResponse(
            url="/discs?error=" + quote(_sanitize_error_message(str(e))),
            status_code=302,
        )
