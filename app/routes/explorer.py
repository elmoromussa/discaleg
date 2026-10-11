"""GET /explorador — Vista d'explorador simulat (arbre + contingut des de la DB)."""
import sqlite3

from fastapi import Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates


def explorer_page(
    request: Request,
    templates: Jinja2Templates,
    conn: sqlite3.Connection,
) -> HTMLResponse:
    return templates.TemplateResponse(request, "explorador.html",
        {"request": request},
    )
