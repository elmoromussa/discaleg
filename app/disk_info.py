"""Identitat física del disc on és una ruta: sèrie, model, bus, mida, salut i tipus de suport.

A Windows s'obté amb PowerShell (Get-Partition -> Get-Disk, més Get-PhysicalDisk per al
tipus de suport) en un subprocés amb límit de temps. Si no és Windows, la ruta és de xarxa,
PowerShell falla o esgota el temps, tots els valors són None i qui crida continua.

Les carcasses USB sovint tornen una sèrie buida o genèrica (p. ex. 0123456789ABCDEF amb
model «Generic DISK01»): is_generic_serial() les detecta perquè no s'usin com a identitat.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path

from app.volume_info import volume_root

TIMEOUT_SECONDS = 25

# Sèries que tornen les carcasses i els controladors genèrics (normalitzades: només [0-9A-Z])
GENERIC_SERIALS = frozenset({
    "0123456789ABCDEF",
    "123456789ABCDEF",
    "0000000000000000",
    "00000000",
    "NONE",
    "NA",
    "UNKNOWN",
})

_EMPTY: dict = {
    "serial_number": None,
    "model": None,
    "vendor": None,
    "interface": None,
    "physical_size_bytes": None,
    "health_status": None,
    "media_type": None,
}

# Script PowerShell: un objecte JSON per al disc físic de la lletra d'unitat {letter}.
_PS_SCRIPT = (
    "$ErrorActionPreference = 'Stop'; "
    "$p = Get-Partition -DriveLetter '{letter}' | Select-Object -First 1; "
    "$d = $p | Get-Disk; "
    "$pd = Get-PhysicalDisk | Where-Object {{ [string]$_.DeviceId -eq [string]$d.Number }} | Select-Object -First 1; "
    "$mt = $null; if ($pd) {{ $mt = [string]$pd.MediaType }}; "
    "[pscustomobject]@{{ SerialNumber = [string]$d.SerialNumber; Model = [string]$d.Model; "
    "Manufacturer = [string]$d.Manufacturer; BusType = [string]$d.BusType; Size = [int64]$d.Size; "
    "HealthStatus = [string]$d.HealthStatus; MediaType = $mt }} | ConvertTo-Json -Compress"
)


def is_generic_serial(serial: str | None, model: str | None = None) -> bool:
    """True si la sèrie no identifica el disc (buida, genèrica o d'una carcassa «Generic»)."""
    s = (serial or "").strip().strip(".")
    if not s:
        return True
    norm = re.sub(r"[^0-9A-Za-z]", "", s).upper()
    if not norm or len(set(norm)) <= 1 or norm in GENERIC_SERIALS:
        return True
    if (model or "").strip().lower().startswith("generic"):
        return True
    return False


def _clean(value: object) -> str | None:
    s = str(value).strip() if value is not None else ""
    return s or None


def get_physical_disk_info(path: Path | str, timeout: float = TIMEOUT_SECONDS) -> dict:
    """Retorna {serial_number, model, vendor, interface, physical_size_bytes, health_status,
    media_type}; tot None si no es pot determinar (no Windows, ruta UNC, error o temps esgotat)."""
    info = dict(_EMPTY)
    if os.name != "nt":
        return info
    root = str(volume_root(Path(path)))
    drive, _ = os.path.splitdrive(root)
    if len(drive) != 2 or drive[1] != ":" or not drive[0].isalpha():
        return info  # ruta UNC o sense lletra d'unitat
    script = _PS_SCRIPT.format(letter=drive[0].upper())
    try:
        proc = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
             "-Command", script],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if proc.returncode != 0 or not proc.stdout.strip():
            return info
        data = json.loads(proc.stdout.strip().splitlines()[-1])
    except (OSError, subprocess.SubprocessError, ValueError):
        return info
    if not isinstance(data, dict):
        return info
    info["serial_number"] = _clean(data.get("SerialNumber"))
    info["model"] = _clean(data.get("Model"))
    info["vendor"] = _clean(data.get("Manufacturer"))
    info["interface"] = _clean(data.get("BusType"))
    size = data.get("Size")
    info["physical_size_bytes"] = int(size) if isinstance(size, (int, float)) and size > 0 else None
    info["health_status"] = _clean(data.get("HealthStatus"))
    media = _clean(data.get("MediaType"))
    info["media_type"] = media if media and media.lower() != "unspecified" else None
    return info


def suggest_kind(info: dict) -> str | None:
    """Tipus de suport (drives.kind) deduïble del maquinari, o None si no és clar.

    Només HDD i SSD són segurs; un bus USB no diu què hi ha dins la carcassa."""
    media = (info.get("media_type") or "").lower()
    bus = (info.get("interface") or "").lower()
    if media == "hdd":
        return "hdd"
    if media == "ssd":
        return "nvme" if bus == "nvme" else "ssd"
    return None


def identity_serial(info: dict) -> str | None:
    """La sèrie només si serveix d'identitat (no genèrica); si no, None."""
    serial = info.get("serial_number")
    return None if is_generic_serial(serial, info.get("model")) else serial
