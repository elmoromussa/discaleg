r"""
Comprovador de rutes dels projectes Metashape (docs/eines-migracio.md §2, model §Annex).

Per a cada `.psx` de l'inventari que és en un disc connectat, llig (només lectura, amb el
prefix `\\?\`, sense Metashape) el `.psx`, `project.zip`, cada `chunk.zip` i cada `frame.zip`
de la seua `.files`, extrau la ruta de cada foto i la resol contra l'inventari de discaleg:
  - `same`: la ruta (relativa a la carpeta del frame, o absoluta amb la lletra del mateix disc)
    existeix al mateix disc; `same-mogut`: el mateix nom i mida és al mateix disc en una altra
    carpeta;
  - `altre`: ruta absoluta d'un altre disc (per la lletra de l'arrel registrada) que hi existeix;
    `altre-nom-mida`: el mateix nom i mida és en un altre disc;
  - `orfe`: enlloc.
Mai llig cap foto: la mida ve del `System/FileSize` que el frame guarda de cada càmera.

De cada chunk extrau l'etiqueta, les càmeres (també dins de <group>), les alineades (les que
tenen <transform>) i què té processat: punts d'enllaç (<point_cloud> al frame), mapes de
profunditat (<depth_map_sets>), núvol dens (<dense_clouds>), malla (<models>), textura
(<texture> o <textures> al doc.xml de model.zip), ortofoto (<orthomosaics>), MDT (<elevations>)
i model en tessel·les (<tiled_models>). Els chunks amb la mateixa etiqueta i el mateix conjunt
de fotos que són en més d'un `.psx` van a l'informe de chunks repetits, i un `.psx` queda
«absorbit» si tots els seus chunks són també en un altre `.psx` amb igual o més processament.
Estructura real del `.psx` documentada a docs/migracio.md.

Eixida: taula `psx_check` (una fila per .psx, reescrita a cada execució; `photos_json` amb
[ruta tal com la guarda el .psx, resolució, disc, ruta trobada, mida] per foto; `chunks_json`
amb el detall per chunk), `data/rutes_metashape_AAAA-MM-DD.csv` (una fila per .psx) i
`data/chunks_repetits_AAAA-MM-DD.csv` (una fila per chunk repetit i .psx). Resum per pantalla:
`.psx` resolts del tot / parcials / orfes, els 20 amb més orfes, chunks repetits i `.psx`
absorbits, i els discs no connectats (saltats).

Exemples (des de l'arrel del projecte):
  python tools/rutes_metashape.py                       # tots els .psx dels discs connectats
  python tools/rutes_metashape.py --drive INT-5         # només un disc
  python tools/rutes_metashape.py --drive INT-5 --path DW23_TFM26\METASHAPE23
  python tools/rutes_metashape.py --selection INT-5 "DW23_TFM26\METASHAPE23\FGM-GLOBAL2024.psx"
      # escriu data/seleccio_FGM-GLOBAL2024.txt: ruta relativa a la carpeta del .psx, mida, hash
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import posixpath
import re
import sqlite3
import sys
import time
import xml.etree.ElementTree as ET
import zipfile
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import schema  # noqa: E402
from app.audit_to_db import long_path  # noqa: E402
from app.config import PROJECT_ROOT, database_path  # noqa: E402
from app.volume_info import get_volume_info  # noqa: E402

SEP = "\\"
TOOL_VERSION = "rutes_metashape 0.2"
RE_ABS = re.compile(r"^([A-Za-z]):[/\\]")
RESOLUTIONS = ("same", "same-mogut", "altre", "altre-nom-mida", "orfe")
# Productes d'un chunk, en ordre de procés (vegeu read_psx per a l'element que els marca).
PRODUCTS = ("punts_enllac", "mapes_profunditat", "nuvol_dens", "malla", "textura", "ortofoto", "mdt",
            "model_tesselles")
CSV_COLUMNS = ["disc", "ruta", "format", "app", "app_original", "chunks", "frames", "cameres",
               "resoltes_mateix_disc", "resoltes_altre_disc", "orfes", "discs_implicats",
               "mida_files_bytes", "fitxers_files", "chunks_repetits", "absorbit", "absorbit_per", "notes"]
REPEATS_COLUMNS = ["grup", "etiqueta", "cameres", "n_psx", "disc", "ruta_psx", "chunk_id", "alineades",
                   *PRODUCTS, "processament", "maxim_del_grup", "psx_absorbit", "absorbit_per"]


# ---------------------------------------------------------------------------
# Discs connectats
# ---------------------------------------------------------------------------

def mounted_drives(conn: sqlite3.Connection) -> tuple[dict[str, str], list[tuple[str, str, str]]]:
    """(drive_id -> arrel muntada amb el serial del volum correcte, [(drive_id, arrel, motiu)] saltats)."""
    mounted: dict[str, str] = {}
    skipped: list[tuple[str, str, str]] = []
    for d, root, serial in conn.execute(
            "SELECT drive_id, last_root_path, volume_serial FROM drives ORDER BY drive_id"):
        if not root or not os.path.isdir(root):
            skipped.append((d, root or "?", "no muntat"))
            continue
        try:
            got = get_volume_info(Path(root)).get("volume_serial")
        except Exception as e:  # noqa: BLE001
            skipped.append((d, root, f"error llegint el volum: {e}"))
            continue
        if serial and got and got != serial:
            skipped.append((d, root, f"a {root} hi ha un altre volum ({got}, esperat {serial})"))
            continue
        mounted[d] = root
    return mounted, skipped


# ---------------------------------------------------------------------------
# Lectura del .psx
# ---------------------------------------------------------------------------

def _read_zip_xml(path: str) -> ET.Element:
    with zipfile.ZipFile(long_path(path)) as z:
        name = next((n for n in z.namelist() if n.endswith(".xml")), None)
        if name is None:
            raise ValueError("sense doc.xml")
        return ET.fromstring(z.read(name))


class ChunkInfo:
    __slots__ = ("id", "label", "enabled", "cameras", "aligned", "products", "models", "photos")

    def __init__(self, cid: str, label: str, enabled: bool):
        self.id = cid
        self.label = label
        self.enabled = enabled
        self.cameras = 0
        self.aligned = 0
        self.products: dict[str, bool] = dict.fromkeys(PRODUCTS, False)
        self.models = 0
        self.photos: list[tuple[str, int | None, str]] = []   # (ruta tal com es guarda, mida, carpeta del frame relativa al disc)

    def score(self) -> int:
        return sum(1 for p in PRODUCTS if self.products[p])

    def dominates(self, other: "ChunkInfo") -> bool:
        """True si té igual o més processament que `other` en tots els productes i alineades."""
        return self.aligned >= other.aligned and all(
            self.products[p] or not other.products[p] for p in PRODUCTS)

    def as_json(self) -> dict[str, Any]:
        return {"id": self.id, "etiqueta": self.label, "activat": self.enabled, "cameres": self.cameras,
                "alineades": self.aligned, "fotos": len(self.photos), "models": self.models,
                "productes": [p for p in PRODUCTS if self.products[p]]}


class PsxInfo:
    __slots__ = ("format_version", "app", "app_original", "chunks", "frames", "notes", "files_dir_rel")

    def __init__(self) -> None:
        self.format_version: str | None = None
        self.app: str | None = None
        self.app_original: str | None = None
        self.chunks: list[ChunkInfo] = []
        self.frames = 0
        self.notes: list[str] = []
        self.files_dir_rel: str | None = None

    @property
    def photos(self) -> list[tuple[str, int | None, str]]:
        return [p for c in self.chunks for p in c.photos]


def _model_has_texture(model_zip: str) -> bool:
    """La textura només consta al doc.xml de model.zip: <texture path> o <textures><texture>."""
    try:
        mx = _read_zip_xml(model_zip)
    except (zipfile.BadZipFile, ValueError, ET.ParseError, OSError):
        return False
    return mx.find("texture") is not None or mx.find("./textures/texture") is not None


def read_psx(root: str, rel: str) -> PsxInfo:
    """Llig el .psx i la seua .files (només lectura). Les rutes de carpeta són relatives al disc."""
    info = PsxInfo()
    psx_abs = os.path.join(root, rel)
    psx_dir_rel = rel.rsplit(SEP, 1)[0] if SEP in rel else ""
    try:
        doc = ET.parse(long_path(psx_abs)).getroot()
    except (ET.ParseError, OSError) as e:
        info.notes.append(f".psx il·legible: {e}")
        return info
    info.format_version = doc.get("version")
    files_path = (doc.get("path") or "{projectname}.files/project.zip").replace(
        "{projectname}", os.path.splitext(os.path.basename(rel))[0]).replace("/", SEP)
    if RE_ABS.match(files_path):
        info.notes.append(f".files amb ruta absoluta: {files_path}")
        files_dir_abs = os.path.dirname(files_path)
        files_dir_rel = None
    else:
        files_dir_rel = posixpath.normpath(posixpath.join(psx_dir_rel.replace(SEP, "/"),
                                                          os.path.dirname(files_path).replace(SEP, "/")))
        files_dir_rel = "" if files_dir_rel == "." else files_dir_rel.replace("/", SEP)
        files_dir_abs = os.path.join(root, files_dir_rel)
    info.files_dir_rel = files_dir_rel
    project_zip = os.path.join(files_dir_abs, os.path.basename(files_path))
    if not os.path.isfile(long_path(project_zip)):
        info.notes.append("falta project.zip")
        return info
    try:
        proj = _read_zip_xml(project_zip)
    except (zipfile.BadZipFile, ValueError, ET.ParseError, OSError) as e:
        info.notes.append(f"project.zip il·legible: {e}")
        return info
    meta = {p.get("name"): p.get("value") for p in proj.iter("property")}
    info.app = meta.get("Info/LastSavedSoftwareVersion")
    info.app_original = meta.get("Info/OriginalSoftwareName")
    if meta.get("Info/OriginalSoftwareVersion"):
        info.app_original = f"{info.app_original or '?'} {meta['Info/OriginalSoftwareVersion']}"
    chunks = proj.findall("./chunks/chunk")
    if not chunks:
        info.notes.append("project.zip sense <chunks>/<chunk>: estructura diferent de la documentada")
    for ch in chunks:
        ch_path = (ch.get("path") or "").replace("/", SEP)
        chunk_zip = os.path.join(files_dir_abs, ch_path)
        chunk_dir_abs = os.path.dirname(chunk_zip)
        chunk_dir_rel = (None if files_dir_rel is None else
                         os.path.join(files_dir_rel, os.path.dirname(ch_path)).rstrip(SEP))
        if not os.path.isfile(long_path(chunk_zip)):
            info.notes.append(f"falta {ch_path}")
            continue
        try:
            chx = _read_zip_xml(chunk_zip)
        except (zipfile.BadZipFile, ValueError, ET.ParseError, OSError) as e:
            info.notes.append(f"{ch_path} il·legible: {e}")
            continue
        ci = ChunkInfo(ch.get("id") or "", chx.get("label") or "", chx.get("enabled", "true") != "false")
        info.chunks.append(ci)
        cams = chx.findall("./cameras/camera") + chx.findall("./cameras/group/camera")
        ci.cameras = len(cams)
        ci.aligned = sum(1 for c in cams if c.find("transform") is not None)
        ci.products["mapes_profunditat"] = chx.find("./depth_map_sets/depth_maps") is not None
        ci.products["nuvol_dens"] = chx.find("./dense_clouds/dense_cloud") is not None
        ci.products["malla"] = chx.find("./models/model") is not None
        ci.products["ortofoto"] = chx.find("./orthomosaics/orthomosaic") is not None
        ci.products["mdt"] = chx.find("./elevations/elevation") is not None
        ci.products["model_tesselles"] = chx.find("./tiled_models/tiled_model") is not None
        ci.models = len(chx.findall("./models/model"))
        frames = chx.findall("./frames/frame")
        if not frames:
            info.notes.append(f"chunk {ci.id} sense <frames>")
        for fr in frames:
            info.frames += 1
            fr_path = (fr.get("path") or "").replace("/", SEP)
            frame_zip = os.path.join(chunk_dir_abs, fr_path)
            frame_dir_abs = os.path.dirname(frame_zip)
            frame_dir_rel = (None if chunk_dir_rel is None else
                             os.path.join(chunk_dir_rel, os.path.dirname(fr_path)).rstrip(SEP))
            if not os.path.isfile(long_path(frame_zip)):
                info.notes.append(f"falta {os.path.join(os.path.dirname(ch_path), fr_path)}")
                continue
            try:
                frx = _read_zip_xml(frame_zip)
            except (zipfile.BadZipFile, ValueError, ET.ParseError, OSError) as e:
                info.notes.append(f"frame {fr.get('id')} del chunk {ci.id} il·legible: {e}")
                continue
            if frx.find("point_cloud") is not None:
                ci.products["punts_enllac"] = True
            if not ci.products["textura"]:
                for m in frx.findall("model"):
                    mp = (m.get("path") or "").replace("/", SEP)
                    if mp and _model_has_texture(os.path.join(frame_dir_abs, mp)):
                        ci.products["textura"] = True
                        break
            for cam in frx.findall("./cameras/camera"):
                photo = cam.find("photo")
                if photo is None:
                    continue
                size = None
                for prop in photo.iter("property"):
                    if prop.get("name") == "System/FileSize":
                        try:
                            size = int(prop.get("value") or "")
                        except ValueError:
                            size = None
                        break
                ci.photos.append((photo.get("path") or "", size, frame_dir_rel or ""))
    return info


# ---------------------------------------------------------------------------
# Resolució contra l'inventari
# ---------------------------------------------------------------------------

class Resolver:
    """Cerca a l'inventari (només lectura) amb memòria cau: per ruta exacta al disc (índex únic
    drive_id+relative_path) i per mida (índex size_bytes) filtrant el nom sense distingir majúscules."""

    def __init__(self, conn: sqlite3.Connection, roots: dict[str, str]):
        self.conn = conn
        self.roots = roots                       # drive_id -> last_root_path (tots els discs)
        self.letter_drives: dict[str, list[str]] = {}
        for d, root in roots.items():
            if root and len(root) >= 2 and root[1] == ":":
                self.letter_drives.setdefault(root[0].upper(), []).append(d)
        self._path_cache: dict[tuple[str, str], tuple[int, str | None] | None] = {}
        self._size_cache: dict[int, list[tuple[str, str, str, str | None]]] = {}

    def by_path(self, drive: str, rel: str) -> tuple[int, str | None] | None:
        key = (drive, rel)
        if key not in self._path_cache:
            row = self.conn.execute(
                "SELECT size_bytes, hash_sha256 FROM inventory WHERE drive_id = ? AND relative_path = ?"
                " AND missing_since_utc IS NULL", (drive, rel)).fetchone()
            self._path_cache[key] = (row[0], row[1]) if row else None
        return self._path_cache[key]

    def by_name_size(self, name: str, size: int) -> list[tuple[str, str, str, str | None]]:
        """[(drive_id, relative_path, name, hash)] amb aquesta mida i aquest nom (sense majúscules)."""
        if size not in self._size_cache:
            self._size_cache[size] = self.conn.execute(
                "SELECT drive_id, relative_path, name, hash_sha256 FROM inventory"
                " WHERE size_bytes = ? AND missing_since_utc IS NULL", (size,)).fetchall()
        nl = name.lower()
        return [r for r in self._size_cache[size] if r[2].lower() == nl]

    def resolve(self, drive: str, frame_dir_rel: str, stored: str, size: int | None) -> tuple[str, str | None, str | None, str | None]:
        """(resolució, disc, ruta trobada, hash) d'una foto d'un .psx del disc `drive`."""
        p = stored.replace("\\", "/")
        name = posixpath.basename(p)
        m = RE_ABS.match(stored)
        if m:
            letter = m.group(1).upper()
            rest = p[3:].replace("/", SEP)
            own_root = self.roots.get(drive) or ""
            if own_root[:1].upper() == letter and own_root[1:2] == ":":
                rel = rest if own_root.rstrip("\\/") == own_root[:2] else _strip_root(rest, own_root)
                if rel is not None:
                    hit = self.by_path(drive, rel)
                    if hit:
                        return "same", drive, rel, hit[1]
            for d in self.letter_drives.get(letter, []):
                if d == drive:
                    continue
                rel = _strip_root(rest, self.roots[d])
                if rel is not None:
                    hit = self.by_path(d, rel)
                    if hit:
                        return "altre", d, rel, hit[1]
        else:
            rel = posixpath.normpath(posixpath.join(frame_dir_rel.replace(SEP, "/"), p))
            if not rel.startswith("../") and rel != "..":
                rel = rel.replace("/", SEP)
                hit = self.by_path(drive, rel)
                if hit:
                    return "same", drive, rel, hit[1]
        if size is None:
            return "orfe", None, None, None
        hits = self.by_name_size(name, size)
        same = [h for h in hits if h[0] == drive]
        if same:
            return "same-mogut", drive, same[0][1], same[0][3]
        if hits:
            hits.sort()
            return "altre-nom-mida", hits[0][0], hits[0][1], hits[0][3]
        return "orfe", None, None, None


def _strip_root(rest: str, root: str) -> str | None:
    """Ruta relativa a l'arrel registrada del disc (p. ex. `U:\\INT7M_B-INESIA`) o None si no hi és."""
    prefix = root[3:].rstrip("\\/")
    if not prefix:
        return rest
    if rest.lower().startswith(prefix.lower() + SEP):
        return rest[len(prefix) + 1:]
    return None


# ---------------------------------------------------------------------------
# Comprovació d'un .psx
# ---------------------------------------------------------------------------

def check_psx(conn: sqlite3.Connection, resolver: Resolver, drive: str, root: str, rel: str) -> dict[str, Any]:
    info = read_psx(root, rel)
    counts: Counter = Counter()
    drives_involved: set[str] = set()
    photos: list[list[Any]] = []
    for stored, size, frame_dir_rel in info.photos:
        res, d, found, _h = resolver.resolve(drive, frame_dir_rel, stored, size)
        counts[res] += 1
        if d and d != drive:
            drives_involved.add(d)
        photos.append([stored, res, d, found, size])
    files_n, files_b = 0, 0
    if info.files_dir_rel is not None:
        lo = info.files_dir_rel + SEP
        files_n, files_b = conn.execute(
            "SELECT COUNT(*), COALESCE(SUM(size_bytes), 0) FROM inventory WHERE drive_id = ?"
            " AND relative_path >= ? AND relative_path < ? AND missing_since_utc IS NULL",
            (drive, lo, lo[:-1] + chr(ord(SEP) + 1))).fetchone()
    return {
        "drive_id": drive, "relative_path": rel, "format": info.format_version, "app": info.app,
        "app_original": info.app_original, "chunks": len(info.chunks), "frames": info.frames,
        "cameras": len(photos),
        "resolved_same": counts["same"] + counts["same-mogut"],
        "resolved_other_drive": counts["altre"] + counts["altre-nom-mida"],
        "orphans": counts["orfe"], "counts": dict(counts),
        "drives_involved": sorted(drives_involved), "files_count": files_n, "files_bytes": files_b,
        "notes": "; ".join(info.notes) if info.notes else None, "photos": photos,
        "chunk_infos": info.chunks, "chunks_detail": [c.as_json() for c in info.chunks],
        "repeated_chunks": 0, "absorbed": 0, "absorbed_by": [],
    }


def save_check(conn: sqlite3.Connection, r: dict[str, Any]) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO psx_check (drive_id, relative_path, checked_utc, app_version, chunks,"
        " cameras, resolved_same, resolved_other_drive, orphans, photos_json, chunks_json, notes)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (r["drive_id"], r["relative_path"], schema.utc_now_iso(), r["app"], r["chunks"], r["cameras"],
         r["resolved_same"], r["resolved_other_drive"], r["orphans"],
         json.dumps(r["photos"], ensure_ascii=False, separators=(",", ":")),
         json.dumps(r["chunks_detail"], ensure_ascii=False), _notes_json(r)))


def _notes_json(r: dict[str, Any]) -> str:
    return json.dumps({"tool": TOOL_VERSION, "format": r["format"], "app_original": r["app_original"],
                       "frames": r["frames"], "counts": r["counts"], "drives": r["drives_involved"],
                       "files_count": r["files_count"], "files_bytes": r["files_bytes"],
                       "chunks_repetits": r["repeated_chunks"], "absorbit": r["absorbed"],
                       "absorbit_per": r["absorbed_by"], "notes": r["notes"]}, ensure_ascii=False)


# ---------------------------------------------------------------------------
# Chunks repetits i .psx absorbits
# ---------------------------------------------------------------------------

def _chunk_key(c: ChunkInfo) -> tuple[str, frozenset[str]] | None:
    if not c.photos:
        return None
    return c.label, frozenset(posixpath.basename(p.replace("\\", "/")).lower() for p, _, _ in c.photos)


def analyze_repeats(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Agrupa els chunks per (etiqueta, conjunt de fotos) presents en més d'un .psx; marca els .psx
    absorbits. Retorna les files de l'informe de chunks repetits i actualitza `rows` in situ."""
    groups: dict[tuple[str, frozenset[str]], list[tuple[dict[str, Any], ChunkInfo]]] = defaultdict(list)
    for r in rows:
        for c in r["chunk_infos"]:
            k = _chunk_key(c)
            if k is not None:
                groups[k].append((r, c))
    repeated = {k: v for k, v in groups.items() if len({id(r) for r, _ in v}) > 1}

    def psx_rank(r: dict[str, Any]) -> tuple[int, int, str, str]:
        # desempat entre chunks iguals: el .psx amb més chunks (el «final») i, si no, el primer
        return (-r["chunks"], -sum(c.score() for c in r["chunk_infos"]), r["drive_id"], r["relative_path"])

    # per cada chunk de cada .psx: quins altres .psx el dominen (igual o més processament)
    dominated_by: dict[tuple[int, int], set[tuple[str, str]]] = {}
    for k, members in repeated.items():
        for r, c in members:
            doms: set[tuple[str, str]] = set()
            for r2, c2 in members:
                if r2 is r:
                    continue
                if c2.dominates(c) and (not c.dominates(c2) or psx_rank(r2) < psx_rank(r)):
                    doms.add((r2["drive_id"], r2["relative_path"]))
            dominated_by[(id(r), id(c))] = doms
    for r in rows:
        keyed = [c for c in r["chunk_infos"] if _chunk_key(c) in repeated]
        r["repeated_chunks"] = len(keyed)
        with_photos = [c for c in r["chunk_infos"] if c.photos]
        if with_photos and all(dominated_by.get((id(r), id(c))) for c in with_photos):
            r["absorbed"] = 1
            by: set[tuple[str, str]] = set()
            for c in with_photos:
                by |= dominated_by[(id(r), id(c))]
            r["absorbed_by"] = sorted(f"{d}:{p}" for d, p in by)
        for c, cj in zip(r["chunk_infos"], r["chunks_detail"]):
            k = _chunk_key(c)
            if k in repeated:
                cj["repetit_a"] = sorted(f"{r2['drive_id']}:{r2['relative_path']}" for r2, _ in repeated[k] if r2 is not r)
                cj["dominat_per"] = sorted(f"{d}:{p}" for d, p in dominated_by.get((id(r), id(c)), set()))

    out: list[dict[str, Any]] = []
    for gi, (k, members) in enumerate(sorted(repeated.items(), key=lambda kv: (-len(kv[1]), kv[0][0])), 1):
        best = max(c.score() for _, c in members)
        for r, c in sorted(members, key=lambda rc: (rc[0]["drive_id"], rc[0]["relative_path"], rc[1].id)):
            out.append({"grup": gi, "etiqueta": c.label, "cameres": len(k[1]), "n_psx": len({id(r2) for r2, _ in members}),
                        "disc": r["drive_id"], "ruta_psx": r["relative_path"], "chunk_id": c.id,
                        "alineades": c.aligned, **{p: int(c.products[p]) for p in PRODUCTS},
                        "processament": c.score(), "maxim_del_grup": int(c.score() == best),
                        "psx_absorbit": r["absorbed"], "absorbit_per": ", ".join(r["absorbed_by"])})
    return out


def write_repeats_csv(rep: list[dict[str, Any]], path: Path) -> None:
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f, delimiter=";", lineterminator="\r\n")
        w.writerow(REPEATS_COLUMNS)
        for r in rep:
            w.writerow([r[c] for c in REPEATS_COLUMNS])


# ---------------------------------------------------------------------------
# Eixida
# ---------------------------------------------------------------------------

def write_csv(rows: list[dict[str, Any]], path: Path) -> None:
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f, delimiter=";", lineterminator="\r\n")
        w.writerow(CSV_COLUMNS)
        for r in rows:
            w.writerow([r["drive_id"], r["relative_path"], r["format"] or "", r["app"] or "",
                        r["app_original"] or "", r["chunks"], r["frames"], r["cameras"],
                        r["resolved_same"], r["resolved_other_drive"], r["orphans"],
                        ", ".join(r["drives_involved"]), r["files_bytes"], r["files_count"],
                        r["repeated_chunks"], r["absorbed"], ", ".join(r["absorbed_by"]), r["notes"] or ""])


def write_selection(r: dict[str, Any], out: Path) -> int:
    """seleccio.txt: una línia per foto resolta (sense repetir): ruta relativa a la carpeta del
    .psx (amb «/»; `disc:ruta` si és en un altre disc), mida i hash si existeix."""
    psx_dir = r["relative_path"].rsplit(SEP, 1)[0] if SEP in r["relative_path"] else ""
    seen: set[tuple[str, str]] = set()
    lines: list[str] = []
    for stored, res, d, found, size in r["photos"]:
        if res == "orfe" or not found or (d, found) in seen:
            continue
        seen.add((d, found))
        if d == r["drive_id"]:
            shown = posixpath.relpath(found.replace(SEP, "/"), psx_dir.replace(SEP, "/") or ".")
        else:
            shown = f"{d}:{found.replace(SEP, '/')}"
        lines.append(f"{shown}\t{size if size is not None else ''}\t{r['hashes'].get((d, found)) or ''}")
    out.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8", newline="\n")
    return len(lines)


def print_summary(rows: list[dict[str, Any]], rep: list[dict[str, Any]],
                  skipped: list[tuple[str, str, str]], skipped_psx: dict[str, int], log=print) -> None:
    full = [r for r in rows if r["cameras"] and r["orphans"] == 0]
    partial = [r for r in rows if r["cameras"] and 0 < r["orphans"] < r["cameras"]]
    orphan = [r for r in rows if r["cameras"] and r["orphans"] == r["cameras"]]
    empty = [r for r in rows if not r["cameras"]]
    cams = sum(r["cameras"] for r in rows)
    log("")
    log(f"== .psx comprovats: {len(rows):,} ({cams:,} càmeres, {sum(r['chunks'] for r in rows):,} chunks)")
    log(f"   resolts del tot: {len(full):,}   parcials: {len(partial):,}   orfes del tot: {len(orphan):,}"
        f"   sense càmeres o il·legibles: {len(empty):,}")
    tot: Counter = Counter()
    for r in rows:
        tot.update(r["counts"])
    log("   càmeres per resolució: " + ", ".join(f"{k} {tot.get(k, 0):,}" for k in RESOLUTIONS))
    prod: Counter = Counter()
    for r in rows:
        for c in r["chunk_infos"]:
            prod.update(p for p in PRODUCTS if c.products[p])
    log("   chunks amb producte: " + ", ".join(f"{p} {prod.get(p, 0):,}" for p in PRODUCTS))
    log("")
    log("== els 20 .psx amb més orfes")
    for r in sorted(rows, key=lambda r: -r["orphans"])[:20]:
        if r["orphans"] == 0:
            break
        log(f"   {r['orphans']:7,} / {r['cameras']:7,}  {r['drive_id']:<10} {r['relative_path'][:75]}")
    groups = {r["grup"] for r in rep}
    absorbed = [r for r in rows if r["absorbed"]]
    log("")
    log(f"== chunks repetits (mateixa etiqueta i mateixes fotos en més d'un .psx): {len(groups):,} grups,"
        f" {len(rep):,} chunks; .psx absorbits: {len(absorbed):,} de {len(rows):,}")
    for r in sorted(absorbed, key=lambda r: (-r['files_bytes'], r['drive_id'], r['relative_path']))[:20]:
        log(f"   {r['files_bytes'] / 1e9:7.1f} GB  {r['drive_id']:<10} {r['relative_path'][:60]:<60} ← "
            + ", ".join(r["absorbed_by"][:3]) + (" …" if len(r["absorbed_by"]) > 3 else ""))
    if skipped:
        log("")
        log("== discs no connectats (saltats)")
        for d, root, why in skipped:
            log(f"   {d:<11} {root:<22} {skipped_psx.get(d, 0):5} .psx   {why}")


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", type=Path, default=database_path, help="Ruta de la DB")
    ap.add_argument("--drive", action="append", default=[], help="Només aquest drive_id (repetible)")
    ap.add_argument("--path", help="Només els .psx sota aquesta carpeta (relativa a l'arrel del disc)")
    ap.add_argument("--selection", nargs=2, metavar=("DRIVE_ID", "RUTA_PSX"),
                    help="Escriu seleccio.txt d'un .psx (ruta relativa a l'arrel del disc) i no toca la DB")
    ap.add_argument("--out", type=Path, help="Fitxer de la selecció (per defecte data/seleccio_<nom>.txt)")
    ap.add_argument("--no-csv", action="store_true", help="No escriure els CSV")
    ap.add_argument("--csv-dir", type=Path, default=PROJECT_ROOT / "data", help="Carpeta dels CSV (per defecte data/)")
    args = ap.parse_args(argv)

    if not args.db.is_file():
        print(f"No existeix la DB: {args.db}", file=sys.stderr)
        return 2
    conn = sqlite3.connect(str(args.db), timeout=60.0)
    try:
        if schema.get_user_version(conn) < schema.SCHEMA_VERSION:
            print(f"Cal l'esquema v{schema.SCHEMA_VERSION}: executa  python -m app.migrate", file=sys.stderr)
            return 2
        schema.set_pragmas_for_writes(conn)
        t0 = time.time()
        roots = {d: r for d, r in conn.execute("SELECT drive_id, last_root_path FROM drives")}
        mounted, skipped = mounted_drives(conn)
        resolver = Resolver(conn, roots)

        if args.selection:
            drive, rel = args.selection
            if drive not in mounted:
                print(f"El disc {drive} no està connectat.", file=sys.stderr)
                return 2
            r = check_psx(conn, resolver, drive, mounted[drive], rel)
            r["hashes"] = {}
            for stored, res, d, found, size in r["photos"]:
                if d and found and (d, found) not in r["hashes"]:
                    hit = resolver.by_path(d, found)
                    r["hashes"][(d, found)] = hit[1] if hit else None
            out = args.out or (PROJECT_ROOT / "data" / f"seleccio_{Path(rel).stem}.txt")
            out.parent.mkdir(parents=True, exist_ok=True)
            n = write_selection(r, out)
            print(f"{rel}: {r['cameras']} càmeres, {r['resolved_same']} al mateix disc, "
                  f"{r['resolved_other_drive']} en altres, {r['orphans']} orfes; {n} línies a {out}")
            for cj in r["chunks_detail"]:
                print(f"   chunk {cj['id']} «{cj['etiqueta']}»: {cj['cameres']} càmeres, {cj['alineades']} alineades;"
                      f" {', '.join(cj['productes']) or 'sense productes'}")
            if r["notes"]:
                print("Notes:", r["notes"])
            return 0

        wanted = [d for d in (args.drive or sorted(mounted)) if d in mounted]
        for d in args.drive:
            if d not in mounted:
                print(f"Avís: el disc {d} no està connectat; es salta.", file=sys.stderr)
        skipped_psx = {d: n for d, n in conn.execute(
            "SELECT drive_id, COUNT(*) FROM inventory WHERE extension = '.psx' AND missing_since_utc IS NULL"
            " GROUP BY drive_id")}
        sql = ("SELECT drive_id, relative_path FROM inventory WHERE extension = '.psx'"
               " AND missing_since_utc IS NULL AND drive_id IN (%s)" % ",".join("?" * len(wanted)))
        params: list[Any] = list(wanted)
        if args.path:
            p = args.path.strip("\\/").replace("/", SEP)
            sql += " AND relative_path >= ? AND relative_path < ?"
            params += [p + SEP, p + chr(ord(SEP) + 1)]
        targets = conn.execute(sql + " ORDER BY drive_id, relative_path", params).fetchall() if wanted else []
        print(f"Discs connectats: {', '.join(wanted) or 'cap'}; .psx a comprovar: {len(targets):,}")

        rows: list[dict[str, Any]] = []
        for i, (d, rel) in enumerate(targets, 1):
            r = check_psx(conn, resolver, d, mounted[d], rel)
            save_check(conn, r)
            if i % 20 == 0 or i == len(targets):
                conn.commit()
            rows.append(r)
            print(f"\r{i:5}/{len(targets)}  {time.time() - t0:5.0f} s  {d} {rel[-60:]:<60}", end="", flush=True)
        print()
        rep = analyze_repeats(rows)
        for r in rows:   # segona passada: chunks repetits i absorció, que necessiten tot el conjunt
            conn.execute("UPDATE psx_check SET chunks_json = ?, notes = ? WHERE drive_id = ? AND relative_path = ?",
                         (json.dumps(r["chunks_detail"], ensure_ascii=False), _notes_json(r),
                          r["drive_id"], r["relative_path"]))
        conn.commit()
        if rows and not args.no_csv:
            args.csv_dir.mkdir(parents=True, exist_ok=True)
            stamp = f"{datetime.now():%Y-%m-%d}"
            out = args.csv_dir / f"rutes_metashape_{stamp}.csv"
            write_csv(rows, out)
            out2 = args.csv_dir / f"chunks_repetits_{stamp}.csv"
            write_repeats_csv(rep, out2)
            print(f"CSV: {out} ({len(rows):,} files); {out2} ({len(rep):,} files)")
        print_summary(rows, rep, skipped, skipped_psx)
        print(f"\nTemps total: {time.time() - t0:.0f} s")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
