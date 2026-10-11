"""GET / — Dashboard amb resums per disc, top extensions, top carpetes."""
import sqlite3

from fastapi import Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from app import queries


def dashboard(
    request: Request,
    templates: Jinja2Templates,
    conn: sqlite3.Connection,
) -> HTMLResponse:
    drives = queries.get_drives_summary(conn)
    top_ext = queries.get_top_extensions(conn, limit=20)
    top_folders = queries.get_top_folders(conn, limit=30)
    return templates.TemplateResponse(request, "dashboard.html",
        {
            "request": request,
            "drives": drives,
            "top_extensions": top_ext,
            "top_folders": top_folders,
        },
    )
