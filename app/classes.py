"""Classes d'anàlisi: la categoria que rep cada fitxer de l'inventari per a proposar-ne la
capa de projecte (model d'arxiu v1.3 §4). És el diccionari de `tools/analisi_arxiu/capes.py`
integrat a l'app, més la funció que el desa a `inventory.analysis_class` (esquema v9).

Cada fitxer rep UNA classe. Primer s'apliquen regles de context de ruta (carpetes
contenidores conegudes); si cap no encaixa, regles de nom exacte; si no, per extensió.

Classes (schema.ANALYSIS_CLASSES; cada una té una columna b_* a `candidates`):
  ORIG   originals de captura (RAW, vídeo de càmera/dron, 360, àudio gravat, escaneig)
  IMG    imatges derivades o ambigües (JPG, TIF, PNG, PSD…): captura o exportació, no se sap
  VID    vídeo (ambigu: captura o muntatge)
  PROJ   fitxers de projecte (.psx, .lrcat, .blend, .qgz, .p2vr, .prproj…)
  PROC   fitxers de procés regenerables (.files de Metashape, .lrdata, .blend1, previews, caches)
  PROD   productes finals 3D/GIS (models, núvols, ortofotos geo, tiles)
  DOC    documents (pdf, office, text, bibliografia)
  AUD    àudio no captura (música, mp3)
  CODI   codi i web (py, js, html, css, repositoris)
  SOFT   programari i instal·ladors (exe, msi, dmg, iso, apk, jocs)
  IMGDISC imatges de disc i màquines virtuals (afi, dsk, vhd, qcow2, img)
  COMP   comprimits fora de .files (zip, rar, 7z, gz, tar)
  SYS    residus de sistema (paperera, SVI, Thumbs, .DS_Store, __MACOSX, temporals, parcials)
  ALTRES sense extensió o extensió no reconeguda

`found.00x` (recuperació de disc) no és una classe: els fitxers es classifiquen per extensió
i `is_recup` ho marca a part (el catàleg en fa un candidat `recuperacio`).

Correspondència classe → capa de projecte proposada (CLASS_TO_LAYER): ORIG → RAW, PROJ i
PROC → PROJ, PROD → OUT, DOC → DOC; SOFT, IMGDISC, SYS i CODI → FORA (fora de l'arxiu);
IMG, VID, COMP, AUD i ALTRES → ambigu (es decideix pel nom de carpeta o a mà).

CLI (normalment ho fa tools/cataleg.py):
  python -m app.classes                 # classifica els fitxers vius sense classe, tots els discs
  python -m app.classes --drive EXT-A   # només un disc
  python -m app.classes --recalc        # torna a classificar-ho tot (diccionari canviat)
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
import time
from collections.abc import Callable
from pathlib import Path

from app.schema import ANALYSIS_CLASSES

# Regles de context: (fragment de ruta en minúscules, classe). S'avaluen en ordre sobre la
# ruta relativa sencera (carpetes i nom, separador «\»).
PATH_RULES: list[tuple[str, str]] = [
    ("$recycle.bin\\", "SYS"),
    ("recycle-bin", "SYS"),
    ("system volume information\\", "SYS"),
    ("__macosx\\", "SYS"),
    ("\\.trashes\\", "SYS"),
    (".files\\", "PROC"),          # Metashape
    (".lrdata\\", "PROC"),         # Lightroom previews / smart previews
    ("\\adobe premiere pro audio previews\\", "PROC"),
    ("\\adobe premiere pro video previews\\", "PROC"),
    ("\\media cache\\", "PROC"),
    ("\\media cache files\\", "PROC"),
    ("\\cache\\", "PROC"),
    ("\\caches\\", "PROC"),
    ("\\.cache\\", "PROC"),
    ("\\__pycache__\\", "PROC"),
    ("\\node_modules\\", "CODI"),
    ("\\site-packages\\", "CODI"),
    ("\\venv\\", "CODI"),
    ("\\.git\\", "CODI"),
    ("\\.dart_tool\\", "CODI"),
    ("\\build\\app\\intermediates\\", "PROC"),
    ("\\.android\\avd\\", "IMGDISC"),
    ("\\steamapps\\", "SOFT"),
    ("\\steam\\", "SOFT"),
    ("\\jocs\\", "SOFT"),
    ("\\juegos\\", "SOFT"),
    ("\\games\\", "SOFT"),
    ("instaladors", "SOFT"),
    ("instalador", "SOFT"),
    ("\\programari\\", "SOFT"),
    ("\\software\\", "SOFT"),
    ("\\programas\\", "SOFT"),
    ("\\emule\\", "SYS"),
]

# Noms de fitxer exactes (minúscules) → classe
NAME_RULES: dict[str, str] = {
    "thumbs.db": "SYS", ".ds_store": "SYS", "desktop.ini": "SYS", "ehthumbs.db": "SYS",
    "zbthumbnail.info": "SYS", ".localized": "SYS",
}

EXT_RULES: dict[str, str] = {
    # ORIG — originals de captura
    **{e: "ORIG" for e in [
        ".arw", ".nef", ".dng", ".cr2", ".cr3", ".crw", ".raf", ".orf", ".rw2", ".pef", ".srw", ".x3f",
        ".gpr",                      # GoPro RAW
        ".insv", ".insp",            # Insta360
        ".360",                      # GoPro Max
        ".mts", ".m2ts",             # AVCHD càmera
        ".e57",                      # escàner
        ".xmp",                      # sidecar de RAW
    ]},
    # IMG — imatges (captura o derivat, no es distingeix per extensió)
    **{e: "IMG" for e in [
        ".jpg", ".jpeg", ".jpe", ".tif", ".tiff", ".tif_original", ".png", ".psd", ".psb", ".exr", ".hdr",
        ".bmp", ".gif", ".webp", ".heic", ".heif", ".tga", ".svg", ".ai", ".eps", ".indd", ".afphoto", ".xcf",
        ".cube", ".3fr",
    ]},
    # VID — vídeo
    **{e: "VID" for e in [
        ".mp4", ".mov", ".avi", ".mkv", ".mxf", ".mpg", ".mpeg", ".m4v", ".wmv", ".flv", ".webm", ".vob",
        ".3gp", ".ogv", ".lrv", ".thm", ".srt", ".trec", ".camrec", ".camproj", ".braw", ".r3d", ".dv", ".ts",
    ]},
    # PROJ — fitxers de projecte
    **{e: "PROJ" for e in [
        ".psx", ".psz", ".lrcat", ".blend", ".qgz", ".qgs", ".p2vr", ".ggpkg", ".prproj", ".aep", ".aeproj",
        ".c4d", ".3dm", ".max", ".skp", ".rvt", ".dwg", ".dxf", ".ggsk", ".kdenlive", ".drp", ".resolve",
        ".unity", ".uproject", ".sbs", ".spp", ".zpr", ".ztl", ".mb", ".ma", ".ccp", ".bin_cc",
        ".rcproj", ".rcbox", ".ptx", ".ttt", ".fla", ".xd", ".fig", ".sketch", ".afdesign", ".afpub",
        ".kra", ".clip", ".procreate", ".gpl", ".ctb", ".tbk", ".hip", ".wrap", ".mlt", ".vpr", ".vpj",
    ]},
    # PROC — regenerables
    **{e: "PROC" for e in [
        ".lrprev", ".blend1", ".blend2", ".3dmbak", ".bak", ".oc3", ".tls", ".grp", ".tmp", ".temp", ".pek",
        ".cfa", ".prv", ".ims", ".prmdc", ".mcdb", ".lock", ".lrdata", ".lr", ".idx", ".ipr", ".pyc",
        ".psmdcp", ".ldb", ".crdownload", ".download", ".part", ".sym", ".dill", ".o", ".obj_tmp",
        ".bak1", ".bak2", ".old", ".autosave", ".~lock", ".ffindex", ".bgm",
    ]},
    # PROD — productes 3D / GIS
    **{e: "PROD" for e in [
        ".obj", ".mtl", ".ply", ".fbx", ".glb", ".gltf", ".stl", ".las", ".laz", ".nxz", ".nxs", ".3ds",
        ".dae", ".usdz", ".usd", ".usda", ".usdc", ".abc", ".x3d", ".wrl", ".off", ".pcd", ".xyz", ".pts",
        ".gpkg", ".shp", ".shx", ".dbf", ".prj", ".cpg", ".qpj", ".sbn", ".sbx", ".ecw", ".mbtiles",
        ".geojson", ".kml", ".kmz", ".gpx", ".asc", ".bil", ".hgt", ".dem", ".vrt", ".ovr", ".aux",
        ".jgw", ".tfw", ".pgw", ".wld", ".sqlite", ".geotiff", ".jp2", ".sid", ".bin",  # .bin: CloudCompare
        ".mix", ".3mf", ".gcode", ".amf",
    ]},
    # DOC — documents
    **{e: "DOC" for e in [
        ".pdf", ".doc", ".docx", ".odt", ".rtf", ".txt", ".md", ".xls", ".xlsx", ".ods", ".csv", ".tsv",
        ".ppt", ".pptx", ".odp", ".key", ".pages", ".numbers", ".epub", ".mobi", ".azw3", ".djvu",
        ".bib", ".ris", ".enl", ".tex", ".gdoc", ".gsheet", ".gslides", ".one", ".onetoc2", ".nb",
        ".msg", ".eml", ".ics", ".vcf", ".mm", ".xmind", ".odg", ".vsd", ".vsdx", ".pub", ".wps",
    ]},
    # AUD — àudio
    **{e: "AUD" for e in [
        ".mp3", ".wav", ".flac", ".m4a", ".wma", ".aif", ".aiff", ".ogg", ".oga", ".opus", ".aac", ".pcm",
        ".mid", ".midi", ".ape", ".alac", ".caf", ".amr", ".sesx", ".aup", ".aup3",
    ]},
    # CODI — codi i web
    **{e: "CODI" for e in [
        ".py", ".js", ".ts", ".tsx", ".jsx", ".html", ".htm", ".css", ".scss", ".php", ".json", ".xml",
        ".yml", ".yaml", ".toml", ".ini", ".cfg", ".conf", ".sh", ".bat", ".ps1", ".cmd", ".dart", ".java",
        ".kt", ".c", ".cpp", ".h", ".hpp", ".cs", ".go", ".rs", ".rb", ".sql", ".ipynb", ".r", ".m",
        ".gradle", ".pro", ".cmake", ".mk", ".map", ".wasm", ".i", ".swig", ".lst", ".pyi", ".pyd",
        ".so", ".dylib", ".a", ".lib", ".pdb", ".def", ".jar", ".class", ".war", ".swf", ".fxp",
    ]},
    # SOFT — programari i instal·ladors
    **{e: "SOFT" for e in [
        ".exe", ".msi", ".msix", ".dmg", ".pkg", ".apk", ".aab", ".iso", ".dll", ".cab", ".sys", ".drv",
        ".ocx", ".cpl", ".appx", ".deb", ".rpm", ".app", ".ress", ".resource", ".assets", ".pak", ".pck",
        ".wsv", ".sav", ".dat", ".mdf", ".mds", ".cue", ".nrg", ".pima", ".tz", ".pth", ".ckpt", ".onnx",
        ".pt", ".safetensors", ".hdf5", ".h5", ".npz", ".npy",
    ]},
    # IMGDISC — imatges de disc i VM
    **{e: "IMGDISC" for e in [
        ".afi", ".adi", ".dsk", ".vhd", ".vhdx", ".vmdk", ".qcow2", ".img", ".vdi", ".ova", ".ovf",
        ".tib", ".tibx", ".wim", ".esd", ".sparsebundle", ".sparseimage", ".ipsw",
    ]},
    # COMP — comprimits
    **{e: "COMP" for e in [
        ".zip", ".rar", ".7z", ".gz", ".tgz", ".tar", ".bz2", ".xz", ".zst", ".lz", ".lzma", ".z", ".cbz",
        ".cbr", ".sit", ".sitx", ".ace", ".arj", ".lha", ".pack",
    ]},
    # altres residus
    ".db": "ALTRES", ".sqlite3": "ALTRES", ".kdbx": "DOC", ".torrent": "SYS",
}

# Fitxers de projecte que fan d'una carpeta un candidat (docs/eines-migracio.md §1 pas 2).
MARKER_EXTENSIONS: tuple[str, ...] = (
    ".psx", ".psz", ".lrcat", ".blend", ".qgz", ".qgs", ".p2vr", ".ggpkg", ".prproj", ".aep",
    ".pts", ".3dm", ".indd",
)

# .pts és alhora el projecte de PTGui (menut) i un núvol de punts en text (gran): per sota
# d'aquest llindar és PROJ i marcador de projecte; a partir d'ell, PROD.
PTS_PROJECT_MAX_BYTES = 50_000_000


def is_marker(ext_lower: str, size_bytes: int | None) -> bool:
    """True si el fitxer és un marcador de projecte (extensió, i mida per a .pts)."""
    if ext_lower not in MARKER_EXTENSIONS:
        return False
    return ext_lower != ".pts" or size_bytes is None or size_bytes < PTS_PROJECT_MAX_BYTES

# Classe → capa de projecte proposada per regla (model §4); absent = ambigu.
CLASS_TO_LAYER: dict[str, str] = {
    "ORIG": "RAW", "PROJ": "PROJ", "PROC": "PROJ", "PROD": "OUT", "DOC": "DOC",
    "SOFT": "FORA", "IMGDISC": "FORA", "SYS": "FORA", "CODI": "FORA",
}

assert set(EXT_RULES.values()) | set(NAME_RULES.values()) | {c for _, c in PATH_RULES} <= set(
    ANALYSIS_CLASSES)


def classify(path_lower: str, name_lower: str, ext_lower: str, size_bytes: int | None = None) -> str:
    """Classe d'un fitxer a partir de la ruta relativa, el nom i l'extensió (tot en minúscules).

    `ext_lower` és `inventory.extension` tal qual (amb punt; `(no_ext)` si no en té). La mida
    només decideix per a .pts (PTGui per sota de PTS_PROJECT_MAX_BYTES, núvol de punts a partir
    d'ell; sense mida, PROD com al diccionari original).
    """
    for frag, layer in PATH_RULES:
        if frag in path_lower:
            return layer
    if name_lower in NAME_RULES:
        return NAME_RULES[name_lower]
    if name_lower.startswith("._"):
        return "SYS"
    if ext_lower == ".pts" and size_bytes is not None and size_bytes < PTS_PROJECT_MAX_BYTES:
        return "PROJ"
    if ext_lower in EXT_RULES:
        return EXT_RULES[ext_lower]
    return "ALTRES"


def is_recup(path_lower: str) -> bool:
    """True si la ruta és dins d'una carpeta de recuperació de disc (found.000, found.001…)."""
    return "\\found.00" in path_lower or path_lower.startswith("found.00")


def update_analysis_classes(
    conn: sqlite3.Connection,
    drive_id: str | None = None,
    recalc: bool = False,
    batch: int = 50_000,
    log: Callable[[str], None] | None = None,
) -> int:
    """Calcula i desa `inventory.analysis_class` dels fitxers vius d'un disc (o de tots).

    Només toca les files amb la classe a NULL (fitxers nous de l'últim escaneig), o totes
    amb `recalc` (diccionari canviat). Per lots de `batch` files amb commit a cada lot, per
    rowid ascendent dins de cada disc (índex idx_inventory_drive): es pot aturar i reprendre.
    Retorna el nombre de files actualitzades. No escriu res més.
    """
    if drive_id:
        drives = [drive_id]
    else:
        drives = [r[0] for r in conn.execute("SELECT drive_id FROM drives ORDER BY drive_id")]
    cond = "" if recalc else " AND analysis_class IS NULL"
    total = 0
    t0 = time.time()
    for d in drives:
        last_id = 0
        n_drive = 0
        while True:
            rows = conn.execute(
                "SELECT id, relative_path, name, extension, size_bytes FROM inventory"
                f" WHERE drive_id = ? AND id > ? AND missing_since_utc IS NULL{cond}"
                " ORDER BY id LIMIT ?",
                (d, last_id, batch),
            ).fetchall()
            if not rows:
                break
            last_id = rows[-1][0]
            updates = [(classify(path.lower(), name.lower(), ext, size), fid)
                       for fid, path, name, ext, size in rows]
            conn.executemany("UPDATE inventory SET analysis_class = ? WHERE id = ?", updates)
            conn.commit()
            n_drive += len(rows)
            if len(rows) < batch:
                break
        total += n_drive
        if log and n_drive:
            log(f"  {d}: {n_drive:,} fitxers classificats ({time.time() - t0:.0f} s)")
    return total


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    from app import schema
    from app.config import database_path

    ap = argparse.ArgumentParser(description="Calcula inventory.analysis_class (classe d'anàlisi).")
    ap.add_argument("--db", type=Path, default=database_path, help="Ruta de la DB")
    ap.add_argument("--drive", help="Només aquest drive_id")
    ap.add_argument("--recalc", action="store_true", help="Torna a classificar també les files que ja tenen classe")
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
        n = update_analysis_classes(conn, args.drive, args.recalc, log=print)
        print(f"{n:,} fitxers classificats en {time.time() - t0:.0f} s")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
