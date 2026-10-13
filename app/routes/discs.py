"""GET/POST /discs — Gestió de discs: escanejar i incorporar, actualitzar o eliminar."""
import html
import logging
import re
import sqlite3
from pathlib import Path
from urllib.parse import quote

from fastapi import Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from app import queries, scan_jobs, schema
from app.audit_to_db import delete_drive
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
                    scans = queries.get_recent_scans(conn, limit=10)
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
            "drive_kinds": schema.DRIVE_KINDS,
            "msg_ok": msg_ok,
            "msg_error": msg_error,
            "job": scan_jobs.get_status(),
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
        conn = sqlite3.connect(str(database_path), timeout=30.0)
        try:
            found = queries.update_drive_card(
                conn,
                drive_id,
                label=_clean_field(form.get("label"), "label"),
                kind=kind,
                location=_clean_field(form.get("location"), "location"),
                notes=_clean_field(form.get("notes"), "notes"),
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

        database_path.parent.mkdir(parents=True, exist_ok=True)
        # L'escaneig s'executa en segon pla; la pàgina Discs mostra el progrés (/api/scan/status)
        scan_jobs.start_scan(database_path, root_path, drive_id, mode)
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
