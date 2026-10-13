"""Escaneigs en segon pla llançats des de la web, amb estat consultable (un a la vegada)."""
from __future__ import annotations

import logging
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

from app.audit_to_db import scan_drive
from app.schema import utc_now_iso

_lock = threading.Lock()
_current: dict[str, Any] | None = None  # últim escaneig llançat (en curs o acabat)


def get_status() -> dict[str, Any] | None:
    with _lock:
        return dict(_current) if _current else None


def is_running() -> bool:
    st = get_status()
    return bool(st and st["status"] == "running")


def _set(**fields: Any) -> None:
    with _lock:
        if _current is not None:
            _current.update(fields)


def start_scan(
    db_path: Path, root_path: Path, drive_id: str, mode: str,
    exclude_paths: list[str] | None = None,
) -> dict[str, Any]:
    """Arrenca l'escaneig en un fil. Llança RuntimeError si ja n'hi ha un en curs.

    exclude_paths: llista normalitzada que s'aplica i es desa a la fitxa; None = la desada."""
    global _current
    with _lock:
        if _current and _current["status"] == "running":
            raise RuntimeError(
                f"Ja hi ha un escaneig en curs («{_current['drive_id']}»). Espera que acabi."
            )
        _current = {
            "drive_id": drive_id,
            "root_path": str(root_path),
            "mode": mode,
            "status": "running",
            "started_utc": utc_now_iso(),
            "started_ts": time.time(),
            "files_seen": 0,
            "current_dir": "",
            "phase": "recorregut",
            "message": None,
            "error": None,
            "finished_utc": None,
            "seconds": None,
        }
        snapshot = dict(_current)

    def progress(count: int, rel_dir: str) -> None:
        _set(files_seen=count, current_dir=rel_dir)

    def run() -> None:
        conn = sqlite3.connect(str(db_path), timeout=60.0)
        try:
            # quan el recorregut acaba, scan_drive fusiona: ho indiquem amb la fase
            def progress_phase(count: int, rel_dir: str) -> None:
                progress(count, rel_dir)

            result = scan_drive(
                conn, root_path, drive_id, mode=mode, progress=progress_phase,
                force_volume=(mode == "replace"), exclude_paths=exclude_paths,
            )
            _set(status="done", message=result.message(), files_seen=result.files_count,
                 phase="acabat", finished_utc=utc_now_iso(), seconds=round(result.seconds))
        except Exception as e:  # noqa: BLE001
            logging.exception("Error en l'escaneig en segon pla")
            _set(status="error", error=str(e), phase="error", finished_utc=utc_now_iso(),
                 seconds=round(time.time() - snapshot["started_ts"]))
        finally:
            conn.close()

    threading.Thread(target=run, name=f"scan-{drive_id}", daemon=True).start()
    return snapshot
