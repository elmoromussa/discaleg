"""Diccionari de capes arxivístiques per a l'anàlisi de l'arxiu digital.

Cada fitxer rep UNA capa. Primer s'apliquen regles de context de ruta (carpetes
contenidores conegudes); si cap no encaixa, es classifica per extensió.

Capes:
  ORIG   originals de captura (RAW, vídeo de càmera/dron, 360, àudio gravat, escaneig)
  IMG    imatges derivades o ambigües (JPG, TIF, PNG, PSD...) — no se sap si són captura o exportació
  VID    vídeo (ambigu: captura o muntatge)
  PROJ   fitxers de projecte (.psx, .lrcat, .blend, .qgz, .p2vr, .prproj...)
  PROC   fitxers de procés regenerables (.files de Metashape, .lrdata, .blend1, previews, caches)
  PROD   productes finals 3D/GIS (models, núvols, ortofotos geo, tiles)
  DOC    documents (pdf, office, text, bibliografia)
  AUD    àudio no captura (música, mp3)
  CODI   codi i web (py, js, html, css, repositoris)
  SOFT   programari i instal·ladors (exe, msi, dmg, iso, apk, jocs)
  IMGDISC imatges de disc i màquines virtuals (afi, dsk, vhd, qcow2, img)
  COMP   comprimits fora de .files (zip, rar, 7z, gz, tar)
  RECUP  recuperació sense classificar (found.000/001 — es reclassifica per extensió dins)
  SYS    residus de sistema (paperera, SVI, Thumbs, .DS_Store, __MACOSX, temporals, descàrregues parcials)
  ALTRES sense extensió o extensió no reconeguda
"""

# Regles de context: (fragment de ruta en minúscules, capa). S'avaluen en ordre.
PATH_RULES = [
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

# Noms de fitxer exactes (minúscules) → capa
NAME_RULES = {
    "thumbs.db": "SYS", ".ds_store": "SYS", "desktop.ini": "SYS", "ehthumbs.db": "SYS",
    "zbthumbnail.info": "SYS", ".localized": "SYS",
}

EXT_RULES = {
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
        ".unity", ".uproject", ".sbs", ".spp", ".zpr", ".ztl", ".mb", ".ma", ".ztl", ".ccp", ".bin_cc",
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

def classify(path_lower: str, name_lower: str, ext_lower: str) -> str:
    for frag, layer in PATH_RULES:
        if frag in path_lower:
            return layer
    if name_lower in NAME_RULES:
        return NAME_RULES[name_lower]
    if name_lower.startswith("._"):
        return "SYS"
    if ext_lower in EXT_RULES:
        return EXT_RULES[ext_lower]
    return "ALTRES"

def is_recup(path_lower: str) -> bool:
    return "\\found.00" in path_lower or path_lower.startswith("found.00")
