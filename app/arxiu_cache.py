"""Memòria cau de l'arxiu virtual per al servidor web.

Els càlculs d'`app/queries_arxiu.py` viuen en taules temporals d'una connexió: el servidor en
manté una de dedicada (només lectura) amb una `ArxiuCalc` i la reutilitza en totes les
peticions, protegida per un cadenat (FastAPI serveix les rutes síncrones en fils).

Invalidació per segells, calculats a cada petició amb consultes de mil·lisegons:
  * inventari (`drives.updated_utc`, que canvia amb cada escaneig o lot de hash): es refà tot;
  * membres (files de `space_members` d'espais d'arxiu i files de `spaces`): es recarreguen
    espais i membres; si el conjunt de carpetes i fitxers membres no ha canviat, els segments,
    els totals per membre i les claus de còpia es conserven i només es recalculen els totals
    dels espais afectats (el canviat i els seus avantpassats). Un canvi de paper o de destí
    d'un membre costa així dècimes de segon en lloc de desenes.
"""
from __future__ import annotations

import sqlite3
import threading
import time
from typing import Any

from app.config import database_path
from app.queries_arxiu import ArxiuCalc

_lock = threading.RLock()
_state: dict[str, Any] = {"conn": None, "calc": None, "inv_stamp": None, "mem_stamp": None,
                          "built_utc": None, "elapsed": 0.0}


def _open() -> sqlite3.Connection:
    # isolation_level=None (mode autocommit): les escriptures a les taules temporals no obrin
    # cap transacció i la connexió veu sempre l'últim estat de la DB (en WAL, una transacció
    # de lectura oberta congelaria la instantània i els segells no canviarien mai).
    conn = sqlite3.connect(f"file:{database_path}?mode=ro", uri=True, timeout=30.0,
                           check_same_thread=False, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA temp_store = MEMORY")
    conn.execute("PRAGMA cache_size = -131072")
    return conn


def inventory_stamp(conn: sqlite3.Connection) -> str:
    r = conn.execute("SELECT MAX(updated_utc), COUNT(*), SUM(files_count) FROM drives").fetchone()
    return f"{r[0]}|{r[1]}|{r[2]}"


def members_stamp(conn: sqlite3.Connection) -> str:
    """Empremta de tots els espais i de les pertinences dels espais d'arxiu (centenars de files)."""
    import hashlib
    h = hashlib.sha1()
    for row in conn.execute(
            "SELECT space_id, parent_id, name, archive_kind, target_name, state, canonical_drive_id, "
            "sort_order, source, description FROM spaces ORDER BY space_id"):
        h.update(repr(tuple(row)).encode("utf-8", "surrogatepass"))
    for row in conn.execute(
            """
            SELECT m.space_id, m.item_id, m.role, m.target_subpath, m.note
            FROM space_members m JOIN spaces s ON s.space_id = m.space_id
            WHERE s.archive_kind IS NOT NULL ORDER BY m.space_id, m.item_id
            """):
        h.update(repr(tuple(row)).encode("utf-8", "surrogatepass"))
    return h.hexdigest()


def get_calc(force: bool = False) -> ArxiuCalc:
    """L'ArxiuCalc compartida, actualitzada segons els segells. Cal cridar-la amb el cadenat."""
    conn = _state["conn"]
    if conn is None:
        conn = _state["conn"] = _open()
    inv = inventory_stamp(conn)
    mem = members_stamp(conn)
    calc = _state["calc"]
    t0 = time.time()
    if force or calc is None or inv != _state["inv_stamp"]:
        calc = ArxiuCalc(conn)
        _state.update(calc=calc, inv_stamp=inv, mem_stamp=mem)
        _state["built_utc"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    elif mem != _state["mem_stamp"]:
        calc.reload_members()
        _state["mem_stamp"] = mem
    _state["elapsed"] = time.time() - t0
    return calc


class locked:
    """`with arxiu_cache.locked() as calc:` — accés exclusiu a la memòria cau."""

    def __init__(self, force: bool = False):
        self.force = force

    def __enter__(self) -> ArxiuCalc:
        _lock.acquire()
        try:
            return get_calc(self.force)
        except Exception:
            _lock.release()
            raise

    def __exit__(self, *exc: Any) -> None:
        _lock.release()


def info() -> dict[str, Any]:
    return {"built_utc": _state["built_utc"], "elapsed": _state["elapsed"]}


def reset() -> None:
    """Oblida la memòria cau (proves, o quan canvia DATABASE_PATH)."""
    with _lock:
        if _state["conn"] is not None:
            _state["conn"].close()
        _state.update(conn=None, calc=None, inv_stamp=None, mem_stamp=None, built_utc=None)
