"""Informació del volum on és una ruta (etiqueta, sistema de fitxers, serial, capacitat)."""
from __future__ import annotations

import os
import shutil
from pathlib import Path


def volume_root(path: Path) -> Path:
    """'E:\\\\REGISTRE23' -> 'E:\\\\'; en POSIX retorna el punt de muntatge."""
    p = Path(path).resolve()
    if os.name == "nt":
        drive, _ = os.path.splitdrive(str(p))
        return Path(drive + "\\") if drive else p
    while not os.path.ismount(p) and p.parent != p:
        p = p.parent
    return p


def get_volume_info(path: Path) -> dict:
    """Retorna {label, filesystem, volume_serial, capacity_bytes, free_bytes} (None si no es pot)."""
    info: dict = {"label": None, "filesystem": None, "volume_serial": None,
                  "capacity_bytes": None, "free_bytes": None}
    root = volume_root(path)
    try:
        usage = shutil.disk_usage(str(root))
        info["capacity_bytes"] = usage.total
        info["free_bytes"] = usage.free
    except OSError:
        pass
    if os.name == "nt":
        try:
            import ctypes
            from ctypes import wintypes

            k32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
            label = ctypes.create_unicode_buffer(261)
            fs = ctypes.create_unicode_buffer(261)
            serial = wintypes.DWORD(0)
            maxlen = wintypes.DWORD(0)
            flags = wintypes.DWORD(0)
            ok = k32.GetVolumeInformationW(
                ctypes.c_wchar_p(str(root)), label, 261,
                ctypes.byref(serial), ctypes.byref(maxlen), ctypes.byref(flags), fs, 261,
            )
            if ok:
                info["label"] = label.value or None
                info["filesystem"] = fs.value or None
                s = serial.value
                info["volume_serial"] = f"{(s >> 16) & 0xFFFF:04X}-{s & 0xFFFF:04X}"
        except Exception:
            pass
    else:
        try:
            info["volume_serial"] = str(os.stat(root).st_dev)
        except OSError:
            pass
    return info
