"""FastAPI app: muntatge de rutes i Jinja2."""
import os
import string
from pathlib import Path
from urllib.parse import unquote

import sqlite3
from fastapi import Depends, FastAPI, Request
from fastapi.responses import JSONResponse, PlainTextResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.config import database_path
from app.db import get_db
from app.routes import dashboard, discs, duplicates, extensions, explorer, folders, search

def _format_bytes(value: int | None) -> str:
    if value is None:
        return ""
    for u in ("B", "KB", "MB", "GB", "TB"):
        if abs(value) < 1024:
            return f"{value:.1f} {u}"
        value /= 1024
    return f"{value:.1f} PB"


app = FastAPI(title="Discàleg", description="Inventari de discs — consulta i visualització de arxiu_discs.db")


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    """Mostra l'error real en lloc de 'Internal Server Error'."""
    try:
        import traceback
        tb = traceback.format_exc()
        body = "Error:\n\n" + str(exc) + "\n\n---\n" + tb
    except Exception:
        body = "Error: " + str(exc)
    # Les peticions /api/* han de rebre JSON perquè el frontend fa .json()
    if request.url.path.startswith("/api/"):
        return JSONResponse({"error": str(exc)}, status_code=500)
    return PlainTextResponse(body, status_code=200, media_type="text/plain; charset=utf-8")


# Ruta base per a plantilles (dins de app/)
templates_dir = Path(__file__).resolve().parent / "templates"
templates = Jinja2Templates(directory=str(templates_dir))
templates.env.filters["format_bytes"] = _format_bytes

# Muntar estàtics
static_dir = Path(__file__).resolve().parent / "static"
app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")


def _static_url(request: Request, name: str) -> str:
    """URL d'un fitxer estàtic amb la data de modificació com a versió (evita la memòria cau antiga)."""
    url = str(request.url_for("static", path="/" + name.lstrip("/")))
    try:
        version = int((static_dir / name).stat().st_mtime)
    except OSError:
        return url
    return f"{url}?v={version}"


templates.env.globals["static_url"] = _static_url


@app.get("/")
def index(request: Request):
    if not database_path.is_file():
        return templates.TemplateResponse(request, "welcome.html", {"request": request})
    conn = sqlite3.connect(str(database_path), timeout=10.0)
    conn.row_factory = sqlite3.Row
    try:
        return dashboard.dashboard(request, templates, conn)
    finally:
        conn.close()


@app.get("/search")
def search_route(request: Request, conn: sqlite3.Connection = Depends(get_db)):
    return search.search_page(request, templates, conn)


@app.get("/duplicates")
def duplicates_route(request: Request, conn: sqlite3.Connection = Depends(get_db)):
    return duplicates.duplicates_page(request, templates, conn)


@app.get("/duplicates/name")
def duplicates_name_redirect():
    return RedirectResponse(url="/duplicates?type=files&mode=name", status_code=302)


@app.get("/duplicates/name-size")
def duplicates_name_size_redirect():
    return RedirectResponse(url="/duplicates?type=files&mode=name-size", status_code=302)


@app.get("/extensions")
def extensions_route(request: Request, conn: sqlite3.Connection = Depends(get_db)):
    return extensions.extensions_page(request, templates, conn)


@app.get("/folders")
def folders_route(request: Request, conn: sqlite3.Connection = Depends(get_db)):
    return folders.folders_page(request, templates, conn)


@app.get("/explorador")
def explorador_route(request: Request, conn: sqlite3.Connection = Depends(get_db)):
    return explorer.explorer_page(request, templates, conn)


@app.get("/copies")
def copies_route(request: Request, conn: sqlite3.Connection = Depends(get_db)):
    """Informes de còpies: còpia única per disc i comparació entre discs."""
    from app.routes import copies
    return copies.copies_page(request, templates, conn)


@app.get("/api/explorer/roots")
def api_explorer_roots(conn: sqlite3.Connection = Depends(get_db)):
    """Retorna els discs (arrels de l'arbre) amb la fitxa resumida."""
    from app import queries_explorer
    drives = queries_explorer.get_explorer_drives(conn)
    return {"drives": drives}


@app.get("/api/explorer/tree/children")
def api_explorer_tree_children(
    conn: sqlite3.Connection = Depends(get_db),
    drive_id: str = "",
    path: str = "",
):
    """Retorna subcarpetes i nombre de fitxers per al node (drive_id, path)."""
    from urllib.parse import unquote
    from app import queries_explorer
    if not drive_id or not drive_id.strip():
        return {"folders": [], "files_count": 0}
    path = unquote(path).strip() if path else ""
    path = queries_explorer._normalize_path(path)
    result = queries_explorer.get_explorer_children(conn, drive_id.strip(), path)
    return result


@app.get("/api/explorer/contents")
def api_explorer_contents(
    conn: sqlite3.Connection = Depends(get_db),
    drive_id: str = "",
    path: str = "",
    limit: int = 300,
    offset: int = 0,
):
    """Retorna contingut de la carpeta (subcarpetes + fitxers paginats)."""
    from urllib.parse import unquote
    from app import queries_explorer
    if not drive_id or not drive_id.strip():
        return {"folders": [], "files": [], "total_files": 0, "has_more": False}
    path = unquote(path).strip() if path else ""
    path = queries_explorer._normalize_path(path)
    limit = max(1, min(500, limit))
    offset = max(0, offset)
    return queries_explorer.get_explorer_contents(
        conn, drive_id.strip(), path, limit=limit, offset=offset
    )


@app.get("/api/explorer/folder-totals")
def api_explorer_folder_totals(
    conn: sqlite3.Connection = Depends(get_db),
    drive_id: str = "",
    path: str = "",
):
    """Totals recursius (fitxers, bytes, carpetes) de la carpeta i de les seves subcarpetes."""
    from urllib.parse import unquote
    from app import queries_explorer
    if not drive_id or not drive_id.strip():
        return {"subtree": {"files": 0, "bytes": 0, "folders": 0},
                "direct": {"files": 0, "bytes": 0, "folders": 0}, "children": {}}
    path = unquote(path).strip() if path else ""
    path = queries_explorer._normalize_path(path)
    return queries_explorer.get_explorer_folder_totals(conn, drive_id.strip(), path)


@app.get("/api/spaces/tree")
def api_spaces_tree(conn: sqlite3.Connection = Depends(get_db)):
    """Arbre complet dels espais virtuals (i la llista plana amb profunditat)."""
    from app.routes import spaces
    return spaces.api_tree(conn)


@app.post("/api/spaces")
async def api_spaces_create(request: Request, conn: sqlite3.Connection = Depends(get_db)):
    """Crea un espai: {name, kind: smart|manual, parent_id?, query?, description?}."""
    from app.routes import spaces
    return await spaces.api_create(request, conn)


@app.patch("/api/spaces/{space_id}")
async def api_spaces_update(space_id: int, request: Request, conn: sqlite3.Connection = Depends(get_db)):
    """Modifica nom, pare, criteris o descripció d'un espai."""
    from app.routes import spaces
    return await spaces.api_update(request, conn, space_id)


@app.delete("/api/spaces/{space_id}")
def api_spaces_delete(space_id: int, conn: sqlite3.Connection = Depends(get_db)):
    """Esborra un espai i els seus descendents."""
    from app.routes import spaces
    return spaces.api_delete(conn, space_id)


@app.get("/api/spaces/{space_id}/contents")
def api_spaces_contents(
    space_id: int,
    conn: sqlite3.Connection = Depends(get_db),
    limit: int = 300,
    offset: int = 0,
    sort: str = "",
    dir: str = "",
):
    """Contingut d'un espai: una pàgina de fitxers (cerca desada) i el total."""
    from app.routes import spaces
    return spaces.api_contents(conn, space_id, limit, offset, sort, dir)


@app.post("/api/spaces/{space_id}/items")
async def api_spaces_add_items(space_id: int, request: Request, conn: sqlite3.Connection = Depends(get_db)):
    """Afegeix elements {items: [{kind: file|folder, drive_id, path}]} a una col·lecció."""
    from app.routes import spaces
    return await spaces.api_add_items(request, conn, space_id)


@app.delete("/api/spaces/{space_id}/items/{item_id}")
def api_spaces_remove_item(space_id: int, item_id: int, conn: sqlite3.Connection = Depends(get_db)):
    """Treu un element d'una col·lecció (l'element i les seves etiquetes es conserven)."""
    from app.routes import spaces
    return spaces.api_remove_item(conn, space_id, item_id)


@app.get("/api/tags")
def api_tags_list(conn: sqlite3.Connection = Depends(get_db)):
    from app.routes import spaces
    return spaces.api_tags(conn)


@app.post("/api/tags")
async def api_tags_create(request: Request, conn: sqlite3.Connection = Depends(get_db)):
    """Crea una etiqueta {name, color?} (si ja existeix amb aquest nom, la retorna)."""
    from app.routes import spaces
    return await spaces.api_tag_create(request, conn)


@app.patch("/api/tags/{tag_id}")
async def api_tags_update(tag_id: int, request: Request, conn: sqlite3.Connection = Depends(get_db)):
    from app.routes import spaces
    return await spaces.api_tag_update(request, conn, tag_id)


@app.delete("/api/tags/{tag_id}")
def api_tags_delete(tag_id: int, conn: sqlite3.Connection = Depends(get_db)):
    from app.routes import spaces
    return spaces.api_tag_delete(conn, tag_id)


@app.get("/api/tags/{tag_id}/contents")
def api_tags_contents(
    tag_id: int, conn: sqlite3.Connection = Depends(get_db),
    limit: int = 300, offset: int = 0, sort: str = "", dir: str = "",
):
    """Fitxers amb una etiqueta (resolts per hash, ruta o carpeta), paginats."""
    from app.routes import spaces
    return spaces.api_tag_contents(conn, tag_id, limit, offset, sort, dir)


@app.post("/api/items/tag")
async def api_items_tag(request: Request, conn: sqlite3.Connection = Depends(get_db)):
    """Etiqueta elements: {items: [...], tag_ids: [...]}."""
    from app.routes import spaces
    return await spaces.api_items_tag(request, conn, remove=False)


@app.post("/api/items/untag")
async def api_items_untag(request: Request, conn: sqlite3.Connection = Depends(get_db)):
    """Treu etiquetes d'elements: {items: [...], tag_ids: [...]}."""
    from app.routes import spaces
    return await spaces.api_items_tag(request, conn, remove=True)


@app.get("/api/items/tags")
def api_item_tags(kind: str = "file", drive_id: str = "", path: str = "", conn: sqlite3.Connection = Depends(get_db)):
    """Etiquetes d'un element concret (per marcar les caselles del diàleg «Afegeix a…»)."""
    from app.routes import spaces
    return spaces.api_item_tags(conn, kind, drive_id, unquote(path or ""))


@app.post("/spaces/desa", name="spaces_save")
async def spaces_save(request: Request, conn: sqlite3.Connection = Depends(get_db)):
    """Desa la cerca actual de Cercar com a espai (o actualitza'n un) i obre l'explorador."""
    from app.routes import spaces
    return await spaces.save_from_search(request, conn)


@app.get("/api/explorer/export.csv", name="explorer_export")
def api_explorer_export(drive_id: str = "", path: str = "", recursive: int = 1):
    """Exporta els fitxers d'una carpeta a CSV (UTF-8 amb BOM, separador «;») en streaming.

    Obre la seva pròpia connexió perquè el generador viu més enllà de la dependència get_db.
    """
    import csv
    import io
    from urllib.parse import quote

    from fastapi.responses import StreamingResponse

    from app import queries_explorer

    drive_id = (drive_id or "").strip()
    if not drive_id:
        return JSONResponse({"error": "Cal drive_id"}, status_code=400)
    if not database_path.is_file():
        return JSONResponse({"error": "Base de dades no trobada"}, status_code=503)
    path = queries_explorer._normalize_path(unquote(path or ""))
    recursive_flag = bool(recursive)

    def generate():
        conn = sqlite3.connect(str(database_path), timeout=10.0, check_same_thread=False)
        try:
            buf = io.StringIO()
            writer = csv.writer(buf, delimiter=";", lineterminator="\r\n")
            writer.writerow(queries_explorer.EXPORT_COLUMNS)
            yield "﻿" + buf.getvalue()
            buf.seek(0)
            buf.truncate()
            n = 0
            for row in queries_explorer.iter_folder_export(conn, drive_id, path, recursive_flag):
                writer.writerow(["" if v is None else v for v in row])
                n += 1
                if n % 2000 == 0:
                    yield buf.getvalue()
                    buf.seek(0)
                    buf.truncate()
            yield buf.getvalue()
        finally:
            conn.close()

    base = f"{drive_id}_{path.rsplit('/', 1)[-1] if path else 'arrel'}"
    safe = "".join(ch if ch.isalnum() or ch in "-_." else "_" for ch in base) or "export"
    filename = base + ".csv"
    headers = {
        "Content-Disposition": f"attachment; filename=\"{safe}.csv\"; filename*=UTF-8''{quote(filename)}",
        "Cache-Control": "no-store",
    }
    return StreamingResponse(generate(), media_type="text/csv; charset=utf-8", headers=headers)


@app.get("/discs")
def discs_route(
    request: Request,
    ok: str | None = None,
    error: str | None = None,
):
    return discs.discs_page_get(request, templates, msg_ok=ok, msg_error=error)


@app.post("/discs", name="discs_post")
async def discs_post(request: Request):
    try:
        result = await discs.discs_page_post(request, templates)
        return result
    except Exception as e:
        import traceback
        tb = traceback.format_exc()
        return PlainTextResponse(
            "Error en processar l'auditoria:\n\n" + str(e) + "\n\n---\n" + tb,
            status_code=200,
            media_type="text/plain; charset=utf-8",
        )


@app.get("/api/scan/status")
def api_scan_status():
    """Estat de l'escaneig en segon pla llançat des de Discs (o null si no n'hi ha hagut cap)."""
    from app import scan_jobs
    st = scan_jobs.get_status()
    if st:
        st.pop("started_ts", None)
    return {"job": st}


@app.post("/discs/delete", name="discs_delete")
async def discs_delete(request: Request):
    return await discs.discs_delete_post(request)


@app.get("/discs/{drive_id}/canvis", name="scan_changes_route")
def scan_changes_route(
    request: Request, drive_id: str, conn: sqlite3.Connection = Depends(get_db)
):
    """Què ha canviat en cada escaneig d'un disc (fitxers nous, modificats i desapareguts)."""
    return discs.scan_changes_page(request, templates, conn, drive_id)


@app.get("/discs/{drive_id}/errors", name="read_errors_route")
def read_errors_route(
    request: Request, drive_id: str, conn: sqlite3.Connection = Depends(get_db)
):
    """Carpetes i fitxers que l'escaneig o el hash no han pogut llegir."""
    return discs.read_errors_page(request, templates, conn, drive_id)


@app.post("/discs/fitxa", name="discs_card")
async def discs_card(request: Request):
    """Desa la fitxa d'un disc (etiqueta, tipus, ubicació, notes)."""
    return await discs.discs_card_post(request)


def _get_windows_drives() -> list[str]:
    """Llista d'unitats a Windows (GetLogicalDrives, os.path.exists o llista per defecte)."""
    # 1) API de Windows
    try:
        import ctypes
        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        bitmask = kernel32.GetLogicalDrives()
        if bitmask:
            out = [f"{chr(65 + i)}:\\" for i in range(26) if (bitmask >> i) & 1]
            if out:
                return out
    except Exception:
        pass
    # 2) Comprovació directa de cada lletra
    try:
        existents = [f"{d}:\\" for d in string.ascii_uppercase if os.path.exists(f"{d}:\\")]
        if existents:
            return existents
    except OSError:
        pass
    # 3) Últim recurs: llista habitual perquè el modal mostri opcions (el browse validarà)
    return ["C:\\", "D:\\", "E:\\", "F:\\", "G:\\", "H:\\"]


@app.get("/api/drives")
def api_drives():
    """Retorna la llista d'unitats disponibles (per al selector de discs)."""
    if os.name == "nt":
        drives = _get_windows_drives()
    else:
        drives = ["/"]
        for m in ("/home", "/mnt", "/media"):
            if os.path.isdir(m):
                try:
                    for name in os.listdir(m):
                        p = os.path.join(m, name)
                        if os.path.isdir(p):
                            drives.append(p + os.sep)
                except OSError:
                    pass
    # Mai retornar llista buida: fallback perquè el modal mostri sempre opcions
    if not drives:
        drives = ["C:\\", "D:\\", "E:\\", "F:\\", "G:\\", "H:\\"] if os.name == "nt" else ["/"]
    return {"drives": drives}


@app.get("/api/browse")
def api_browse(path: str = ""):
    """Retorna les subcarpetes d'una ruta (per navegar tipus explorador)."""
    if not path or not path.strip():
        return {"path": "", "folders": [], "parent": None, "error": "Ruta buida"}
    raw = unquote(path.strip()).rstrip(os.sep)
    if not raw:
        return {"path": "", "folders": [], "parent": None, "error": "Ruta buida"}
    try:
        abs_path = os.path.abspath(raw)
        if not os.path.isdir(abs_path):
            return {"path": abs_path, "folders": [], "parent": None, "error": "No és un directori"}
        parent = os.path.dirname(abs_path)
        if parent == abs_path or (os.name == "nt" and len(parent) <= 3 and abs_path.endswith("\\")):
            parent = None
        else:
            parent = parent + os.sep if not parent.endswith(os.sep) else parent
        folders_list = []
        for name in sorted(os.listdir(abs_path)):
            p = os.path.join(abs_path, name)
            if os.path.isdir(p) and not name.startswith("."):
                folders_list.append(name)
        return {
            "path": abs_path + (os.sep if not abs_path.endswith(os.sep) else ""),
            "folders": folders_list,
            "parent": parent,
            "error": None,
        }
    except OSError as e:
        return {"path": raw, "folders": [], "parent": None, "error": str(e)}


@app.get("/health")
def health():
    """Comprova que la DB existeix (útil per a scripts)."""
    return {"database_exists": database_path.is_file(), "database_path": str(database_path)}
