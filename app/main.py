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


app = FastAPI(title="DiscScope", description="Inventari de discs — consulta i visualització de arxiu_discs.db")


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
