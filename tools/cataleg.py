"""
Catàleg de projectes: pas A de la migració a l'arxiu (docs/eines-migracio.md §1, model §10).

Llig l'inventari, troba les carpetes candidates a projecte (o a capa d'un projecte), en
calcula els totals per classe d'anàlisi, les còpies aproximades entre discs i una proposta
de sèrie, nom de projecte, any i capa, i ho escriu a la taula `candidates` (regenerada
sencera) i a `data/cataleg_AAAA-MM-DD.csv` (UTF-8 amb BOM, separador «;», una fila per
candidat, amb les columnes de revisió buides perquè les òmpliga Esteve).

Només escriu a `candidates` i a `inventory.analysis_class` (cau de la classe d'anàlisi,
app/classes.py). La resta són lectures per conjunts sobre els índexs existents:
  - marcadors de projecte per (drive_id, extension);
  - mida recursiva de les carpetes de nivell baix des de folder_stats;
  - una passada per disc sobre els fitxers vius (índex per disc), que acumula alhora els
    totals de cada candidat ancestre i la clau de còpia (nom NFC minúscula + mida, ≥ 1 MiB).

Exemples (des de l'arrel del projecte):
  python tools/cataleg.py                      # tots els discs, CSV a data/
  python tools/cataleg.py --drive INT-5 --drive EXT-B   # només aquests discs (la resta es conserva)
  python tools/cataleg.py --recalc             # torna a classificar tots els fitxers (diccionari canviat)
  python tools/cataleg.py --no-csv             # només la taula candidates
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import sqlite3
import sys
import time
import unicodedata
from collections import defaultdict
from datetime import date, datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import schema  # noqa: E402
from app.classes import (CLASS_TO_LAYER, MARKER_EXTENSIONS, PTS_PROJECT_MAX_BYTES,  # noqa: E402
                         is_marker, update_analysis_classes)
from app.config import PROJECT_ROOT, database_path  # noqa: E402

SEP = "\\"
TOOL_VERSION = "cataleg 0.1"
MIN_CANDIDATE_BYTES = 1_000_000_000   # «pesa ≥ 1 GB» (decimal, com les xifres del model)
COPY_MIN_SIZE = 1_048_576             # clau de còpia només per a fitxers ≥ 1 MiB (criteri de l'auditoria)
DOMINANT_SHARE = 0.90                 # classe dominant: > 90 % dels bytes
SAME_CONTENT_SHARE = 0.50             # disc amb el mateix contingut: ≥ 50 % dels bytes hi tenen còpia
CONTENT_MIN_VOTES = 5                 # vot per contingut: fitxers de projecte votants com a mínim
CONTENT_MIN_SHARE = 0.60              # … i quota mínima de la sèrie guanyadora
MAX_CONTAINER_DEPTH = 4               # els contenidors del vocabulari es busquen fins a aquesta profunditat
CLASSES = schema.ANALYSIS_CLASSES
SERIE_ORDER = {"lloc": 0, "programa": 1, "client": 2, "tipus": 3, "varis": 4}
RE_SERIE_NAME = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
RE_YEAR4 = re.compile(r"(?<!\d)(19[89]\d|20[0-4]\d)(?!\d)")
RE_TOKEN_YY = re.compile(r"^[a-z]{2,}(\d{2})$")
RE_DATE_PREFIX = re.compile(r"^(\d{2}|\d{4})[-_. ](\d{2})[-_. ](\d{2})(?![\d])")
RE_NONALNUM = re.compile(r"[^a-z0-9]+")
THIS_YEAR = date.today().year
RECUP_PROJECT = "2025_recuperacio-raid"   # model §11: found.00x sense context
MARKER_PH = ",".join("?" * len(MARKER_EXTENSIONS))

# Columnes del CSV: generades (en aquest ordre) i de revisió (buides).
CSV_GENERATED = [
    "candidate_id", "disc", "serie_disc", "ruta", "tipus", "pare", "fitxers", "bytes", "gb",
    "bytes_unics", "pct_unics",
    *[f"gb_{c.lower()}" for c in CLASSES],
    "data_min", "data_max", "marcadors", "serie_proposada", "serie_match", "projecte_proposat",
    "any_proposat", "capa_proposada", "confianca_capa", "discs_mateix_contingut", "canonica_proposada",
]
CSV_REVIEW = ["rev_serie", "rev_projecte", "rev_capa", "rev_any", "rev_decisio", "rev_fusionar_amb",
              "rev_canonica", "rev_primari", "rev_excepcions", "rev_notes"]


# ---------------------------------------------------------------------------
# Vocabulari
# ---------------------------------------------------------------------------

def fold(s: str) -> str:
    """Minúscules sense accents (NFKD i fora les marques), per a comparar noms de carpeta."""
    return "".join(ch for ch in unicodedata.normalize("NFKD", s) if not unicodedata.combining(ch)).lower()


def tokens(s: str) -> list[str]:
    return [t for t in RE_NONALNUM.split(fold(s)) if t]


class Vocab:
    """data/vocabulari.json carregat i validat (format a docs/migracio.md)."""

    def __init__(self, data: dict[str, Any]):
        series = data.get("series") or []
        for s in series:
            if not RE_SERIE_NAME.match(s.get("name", "")):
                raise ValueError(f"Nom de sèrie invàlid al vocabulari: {s.get('name')!r}")
            if s.get("kind") not in SERIE_ORDER:
                raise ValueError(f"Tipus de sèrie invàlid per a {s['name']}: {s.get('kind')!r}")
        # Precedència del model §2.1: lloc → programa → client → tipus (ordre estable dins de cada tipus)
        self.series: list[dict[str, Any]] = sorted(series, key=lambda s: SERIE_ORDER[s["kind"]])
        for s in self.series:
            s["keywords"] = [fold(k) for k in s.get("keywords", []) if k]
            # Paraules amb separador o acabades en dígit (la petaca, dh-, dw_, dh2, registre23):
            # es busquen literalment a la ruta normalitzada, a l'inici d'un token.
            s["literal_re"] = {k: re.compile(r"(?<![a-z0-9])" + re.escape(k)) for k in s["keywords"]
                               if not k.isalnum() or k[-1].isdigit()}
        self.series_by_name = {s["name"]: s for s in self.series}
        patterns = data.get("containers") or []
        self.container_re = re.compile("|".join(f"(?:{p})" for p in patterns), re.I) if patterns else None
        self.layer_words: dict[str, list[str]] = {
            layer: [fold(w) for w in words] for layer, words in (data.get("layer_words") or {}).items()}
        self.system_names = {fold(n) for n in data.get("system_names") or []}
        self.recovery_re = re.compile(data.get("recovery_pattern") or r"^found\.\d{3}$", re.I)
        self.serie_from_content = bool(data.get("serie_from_content", True))

    @classmethod
    def load(cls, path: Path) -> "Vocab":
        with open(path, encoding="utf-8-sig") as f:
            return cls(json.load(f))

    def is_container(self, folder_path: str) -> bool:
        return bool(self.container_re and self.container_re.search(folder_path))

    def special_kind(self, folder_name: str) -> str | None:
        n = fold(folder_name)
        if self.recovery_re.match(n):
            return "recuperacio"
        if n in self.system_names or "recycle" in n:
            return "sistema"
        return None

    def _serie_in_component(self, s: dict[str, Any], folded: str, toks: list[str]) -> str | None:
        """Text de la coincidència d'una sèrie en un component de ruta, o None."""
        for kw in s["keywords"]:
            lit = s["literal_re"].get(kw)
            if lit is not None:
                m = lit.search(folded)
                if m:
                    t = RE_NONALNUM.split(folded[m.start():], 1)[0]
                    return kw if t == kw else f"{kw} ({t})"
                continue
            for t in toks:
                if keyword_matches(kw, t):
                    return kw if t == kw else f"{kw} ({t})"
        return None

    def match_serie(self, path: str) -> tuple[str, str | None]:
        """(sèrie, serie_match) d'una ruta. Si coincideixen diverses sèries, guanya la que ho fa
        al component de ruta més profund (el més pròxim al candidat); en empat al mateix
        component, la primera per precedència (lloc > programa > client > tipus). serie_match
        diu la paraula guanyadora (i el token) i, darrere de «perd:», les sèries que han perdut.
        ('varis', None) si cap."""
        best: dict[str, tuple[int, int, str, str]] = {}   # sèrie -> (profunditat, precedència, sèrie, text)
        for depth, comp in enumerate(path.split(SEP)):
            folded = fold(comp)
            toks = tokens(comp)
            for rank, s in enumerate(self.series):
                text = self._serie_in_component(s, folded, toks)
                if text:
                    best[s["name"]] = (depth, rank, s["name"], text)   # la més profunda mana
        if not best:
            return "varis", None
        hits = sorted(best.values(), key=lambda h: (-h[0], h[1]))
        winner = hits[0]
        losers = [h for h in hits[1:] if h[2] != winner[2]]
        match = winner[3]
        if losers:
            match += " | perd: " + ", ".join(f"{h[2]}: {h[3]}" for h in losers)
        return winner[2], match

    def match_serie_in_names(self, names: list[str]) -> tuple[str, str | None]:
        """Vot per contingut: cada fitxer de projecte d'un candidat vota per la sèrie del seu nom
        (ruta relativa al candidat, mateixa regla que match_serie). S'assigna la guanyadora
        només amb CONTENT_MIN_VOTES vots com a mínim i CONTENT_MIN_SHARE o més dels vots:
        serie_match «dins: paraula (token) n/m». Si no, ('varis', «suggerit: sèrie n/m»);
        ('varis', None) sense cap vot."""
        votes: dict[str, int] = {}
        example: dict[str, str] = {}
        for n in names:
            serie, match = self.match_serie(n)
            if match:
                votes[serie] = votes.get(serie, 0) + 1
                example.setdefault(serie, match.split(" | perd:", 1)[0])
        if not votes:
            return "varis", None
        best = max(votes, key=votes.get)
        total = sum(votes.values())
        if total >= CONTENT_MIN_VOTES and votes[best] >= CONTENT_MIN_SHARE * total:
            return best, f"dins: {example[best]} {votes[best]}/{total}"
        return "varis", f"suggerit: {best} {votes[best]}/{total}"

    def match_layer(self, folder_name: str) -> str | None:
        toks = tokens(folder_name)
        for layer, words in self.layer_words.items():
            for w in words:
                for t in toks:
                    if t.startswith(w) or (len(w) >= 5 and w in t):
                        return layer
        return None


def keyword_matches(kw: str, token: str) -> bool:
    """Paraula clau contra un token del nom: igual, seguida només de dígits (dw23, pep26),
    o continguda si té 5 lletres o més (lapetacaproject, covetesvr)."""
    if token == kw:
        return True
    if token.startswith(kw) and token[len(kw):].isdigit():
        return True
    return len(kw) >= 5 and kw in token


def year_from_name(name: str) -> int | None:
    """Any del nom d'una carpeta: AAAA, prefix de data AA-MM-DD, o lletres+AA (dw23, pep26)."""
    m = RE_YEAR4.search(name)
    if m:
        return int(m.group(1))
    m = RE_DATE_PREFIX.match(name)
    if m and 1 <= int(m.group(2)) <= 12 and 1 <= int(m.group(3)) <= 31:
        yy = int(m.group(1))
        if yy <= THIS_YEAR % 100 + 1:
            return 2000 + yy
    for t in tokens(name):
        m = RE_TOKEN_YY.match(t)
        if m:
            yy = int(m.group(1))
            if yy <= THIS_YEAR % 100 + 1:
                return 2000 + yy
            if yy >= 90:
                return 1900 + yy
    return None


def make_descriptor(name: str, serie: dict[str, Any] | None, year: int | None) -> str:
    """Descriptor del nom de projecte: minúscules, sense accents, «-» com a separador, sense
    l'any ni el codi de sèrie; 2–30 caràcters."""
    code = fold(serie["code"]) if serie and serie.get("code") else None
    yy = f"{year % 100:02d}" if year else None
    toks = tokens(RE_DATE_PREFIX.sub("", name))
    stripped = []   # tokens sense l'any (dw23 -> dw, chacha26 -> chacha)
    for t in toks:
        if year and t == str(year):
            continue
        if yy and RE_TOKEN_YY.match(t) and t.endswith(yy):
            t = t[:-2]
        stripped.append(t)
    kept = [t for t in stripped if not (code and (t == code or (t.startswith(code) and t[len(code):].isdigit())))]
    d = "-".join(kept)
    if len(d) < 2:
        d = "-".join(stripped)
    if len(d) < 2:
        d = "-".join(tokens(name)) or "projecte"
    if len(d) > 30:
        d = d[:30].rstrip("-")
    return d


def nfc_lower(name: str) -> str:
    return unicodedata.normalize("NFC", name).lower()


def year_of(mtime: str | None) -> int | None:
    if not mtime or len(mtime) < 4 or not mtime[:4].isdigit():
        return None
    y = int(mtime[:4])
    return y if 1995 <= y <= THIS_YEAR + 1 else None


# ---------------------------------------------------------------------------
# Acumuladors
# ---------------------------------------------------------------------------

class Acc:
    """Totals d'un candidat (o del residu d'un disc), acumulats fitxer a fitxer."""
    __slots__ = ("files", "bytes", "cls", "ymin", "mtmin", "mtmax", "unique", "copy_bytes", "markers")

    def __init__(self) -> None:
        self.files = 0
        self.bytes = 0
        self.cls: dict[str, int] = dict.fromkeys(CLASSES, 0)
        self.ymin: dict[str, int] = {}          # classe -> any mínim (mtime) per a proposar l'any
        self.mtmin: str | None = None
        self.mtmax: str | None = None
        self.unique = 0                         # bytes sense còpia en cap altre disc
        self.copy_bytes: dict[int, int] = {}    # bit del disc -> bytes del candidat amb còpia allà
        self.markers: dict[str, int] = {}       # extensió de marcador -> fitxers (recursiu)

    def add(self, size: int, cls: str, year: int | None, mt: str | None, other_mask: int, ext: str | None) -> None:
        self.files += 1
        self.bytes += size
        self.cls[cls] = self.cls.get(cls, 0) + size
        if year is not None:
            y = self.ymin.get(cls)
            if y is None or year < y:
                self.ymin[cls] = year
            if mt:
                if self.mtmin is None or mt < self.mtmin:
                    self.mtmin = mt
                if self.mtmax is None or mt > self.mtmax:
                    self.mtmax = mt
        if other_mask:
            cb = self.copy_bytes
            m = other_mask
            while m:
                low = m & -m
                cb[low] = cb.get(low, 0) + size
                m ^= low
        else:
            self.unique += size
        if ext:
            self.markers[ext] = self.markers.get(ext, 0) + 1


class Cand:
    __slots__ = ("drive", "path", "name", "depth", "kind", "parent", "acc", "direct_marker", "row")

    def __init__(self, drive: str, path: str, kind: str, direct_marker: bool):
        self.drive = drive
        self.path = path
        self.name = path.rsplit(SEP, 1)[-1]
        self.depth = path.count(SEP) + 1
        self.kind = kind
        self.parent: Cand | None = None
        self.acc = Acc()
        self.direct_marker = direct_marker
        self.row: dict[str, Any] = {}


def parent_of(path: str) -> str:
    i = path.rfind(SEP)
    return path[:i] if i >= 0 else ""


# ---------------------------------------------------------------------------
# Pas 2: candidats d'un disc
# ---------------------------------------------------------------------------

def detect_candidates(conn: sqlite3.Connection, drive_id: str, vocab: Vocab) -> dict[str, Cand]:
    """Carpetes candidates d'un disc (model §10 i especificació §1 pas 2), per ruta."""
    folders = conn.execute(
        "SELECT folder_path, total_bytes FROM folder_stats WHERE drive_id = ?", (drive_id,)).fetchall()
    # Carpetes amb un fitxer de projecte directe, a qualsevol profunditat (índex drive_id, extension).
    marker_folders: set[str] = set()
    for (rel,) in conn.execute(
            f"SELECT relative_path FROM inventory WHERE drive_id = ? AND extension IN ({MARKER_PH})"
            " AND missing_since_utc IS NULL AND (extension != '.pts' OR size_bytes < ?)",
            (drive_id, *MARKER_EXTENSIONS, PTS_PROJECT_MAX_BYTES)):
        marker_folders.add(parent_of(rel))

    # Contenidors del vocabulari (fins a MAX_CONTAINER_DEPTH) i els seus ancestres: mai candidats
    # (tampoc si tenen un fitxer de projecte directe: els seus fitxers van al residu del disc).
    containers: set[str] = set()
    for fp, _ in folders:
        if fp and fp.count(SEP) < MAX_CONTAINER_DEPTH and vocab.is_container(fp):
            containers.add(fp)
    excluded: set[str] = set(containers)
    for c in containers:
        p = parent_of(c)
        while p:
            excluded.add(p)
            p = parent_of(p)

    def inside_files(fp: str) -> bool:
        return any(part.lower().endswith(".files") for part in fp.split(SEP))

    # Carpetes elegibles per estructura: nivell 1 del disc, o nivell 1 o 2 sota un contenidor.
    eligible: dict[str, int] = {}   # ruta -> bytes recursius (es completa després)
    for fp, _ in folders:
        if not fp or fp in excluded or inside_files(fp):
            continue
        p1 = parent_of(fp)
        if p1 == "" or p1 in containers or (p1 and parent_of(p1) in containers):
            eligible[fp] = 0
    for fp, tb in folders:
        p = fp
        while p:
            if p in eligible:
                eligible[p] += tb
            p = parent_of(p)

    cands: dict[str, Cand] = {}
    for fp, rec_bytes in eligible.items():
        name = fp.rsplit(SEP, 1)[-1]
        special = vocab.special_kind(name)
        if special:
            cands[fp] = Cand(drive_id, fp, special, False)
        elif rec_bytes >= MIN_CANDIDATE_BYTES:
            cands[fp] = Cand(drive_id, fp, "projecte", False)

    # Les carpetes amb fitxer de projecte directe són candidats sempre.
    for fp in sorted(marker_folders):
        if not fp or fp in excluded or inside_files(fp):
            continue
        c = cands.get(fp)
        if c is None:
            c = cands[fp] = Cand(drive_id, fp, "projecte", True)
        else:
            c.direct_marker = True

    # Imbricació: el candidat ancestre més pròxim és el pare; els imbricats són capes.
    for fp, c in cands.items():
        p = parent_of(fp)
        while p:
            if p in cands:
                c.parent = cands[p]
                break
            p = parent_of(p)
        if c.parent is not None and c.kind == "projecte":
            c.kind = "capa"
    return cands


# ---------------------------------------------------------------------------
# Pas 1 + agregats: passada per disc
# ---------------------------------------------------------------------------

def build_copy_keys(conn: sqlite3.Connection, bit_of: dict[str, int], log) -> dict[int, int]:
    """Clau de còpia (nom NFC minúscula + mida, ≥ 1 MiB) -> màscara de discs on apareix."""
    t0 = time.time()
    keys: dict[int, int] = {}
    get = keys.get
    for name, size, drive in conn.execute(
            "SELECT name, size_bytes, drive_id FROM inventory"
            " WHERE missing_since_utc IS NULL AND size_bytes >= ?", (COPY_MIN_SIZE,)):
        bit = bit_of.get(drive)
        if bit is None:
            continue
        k = hash((nfc_lower(name), size))
        keys[k] = get(k, 0) | bit
    log(f"  claus de còpia: {len(keys):,} (fitxers ≥ 1 MiB) en {time.time() - t0:.0f} s")
    return keys


def aggregate_drive(conn: sqlite3.Connection, drive_id: str, cands: dict[str, Cand],
                    keys: dict[int, int], own_bit: int) -> Acc:
    """Una passada pels fitxers vius del disc: acumula cada fitxer al(s) candidat(s) ancestre(s)
    o al residu del disc. Retorna l'acumulador del residu."""
    residu = Acc()
    cache: dict[str, list[Acc]] = {"": []}
    marker_set = set(MARKER_EXTENSIONS)

    def resolve(folder: str) -> list[Acc]:
        accs = cache.get(folder)
        if accs is None:
            accs = list(resolve(parent_of(folder)))
            c = cands.get(folder)
            if c is not None:
                accs.insert(0, c.acc)
            cache[folder] = accs
        return accs

    residu_list = [residu]
    keys_get = keys.get
    not_own = ~own_bit
    for path, name, ext, size, mtime, cls in conn.execute(
            "SELECT relative_path, name, extension, size_bytes, modified_utc, analysis_class"
            " FROM inventory WHERE drive_id = ? AND missing_since_utc IS NULL", (drive_id,)):
        i = path.rfind(SEP)
        accs = cache.get(path[:i] if i >= 0 else "")
        if accs is None:
            accs = resolve(path[:i] if i >= 0 else "")
        if not accs:
            accs = residu_list
        if size >= COPY_MIN_SIZE:
            other = keys_get(hash((nfc_lower(name), size)), 0) & not_own
        else:
            other = 0
        year = year_of(mtime)
        mext = ext if ext in marker_set and is_marker(ext, size) else None
        if cls is None:
            cls = "ALTRES"
        for acc in accs:
            acc.add(size, cls, year, mtime, other, mext)
    return residu


# ---------------------------------------------------------------------------
# Pas 3: proposta
# ---------------------------------------------------------------------------

def content_names(conn: sqlite3.Connection, c: Cand) -> list[str]:
    """Rutes (relatives al candidat) dels fitxers de projecte que conté: rang de l'índex únic
    (drive_id, relative_path) filtrat per extensió."""
    lo = c.path + SEP
    hi = lo[:-1] + chr(ord(SEP) + 1)   # «]»: límit superior exclusiu del rang de prefix
    names = [rel[len(lo):] for (rel,) in conn.execute(
        f"SELECT relative_path FROM inventory WHERE drive_id = ? AND extension IN ({MARKER_PH})"
        " AND missing_since_utc IS NULL AND relative_path >= ? AND relative_path < ?"
        " AND (extension != '.pts' OR size_bytes < ?)",
        (c.drive, *MARKER_EXTENSIONS, lo, hi, PTS_PROJECT_MAX_BYTES))]
    return names


def propose(c: Cand, vocab: Vocab, drive_of_bit: dict[int, str],
            conn: sqlite3.Connection | None = None) -> dict[str, Any]:
    a = c.acc
    row: dict[str, Any] = {
        "drive_id": c.drive, "relative_path": c.path, "kind": c.kind,
        "parent_path": c.parent.path if c.parent else None,
        "files_count": a.files, "total_bytes": a.bytes, "unique_bytes_approx": a.unique,
        "min_mtime": a.mtmin[:10] if a.mtmin else None, "max_mtime": a.mtmax[:10] if a.mtmax else None,
        "markers_json": json.dumps(dict(sorted(a.markers.items(), key=lambda kv: -kv[1])),
                                   ensure_ascii=False) if a.markers else None,
        "proposed_serie": None, "serie_match": None, "proposed_project": None,
        "proposed_year": None, "proposed_layer": None, "layer_confidence": None,
        "same_content_drives": None, "canonical_proposed": 0,
    }
    for cls in CLASSES:
        row[f"b_{cls.lower()}"] = a.cls.get(cls, 0)

    # Capa proposada: classe dominant (> 90 %) per regla; si no, paraules del nom; si no, ambigu.
    layer, conf = None, "ambigu"
    if a.bytes:
        dom, dom_bytes = max(a.cls.items(), key=lambda kv: kv[1])
        if dom_bytes > DOMINANT_SHARE * a.bytes and dom in CLASS_TO_LAYER:
            layer, conf = CLASS_TO_LAYER[dom], "regla"
    if layer is None:
        by_name = vocab.match_layer(c.name)
        if by_name:
            layer, conf = by_name, "nom"
    if c.kind in ("projecte", "capa"):
        row["proposed_layer"], row["layer_confidence"] = layer, conf

    # Discs amb el mateix contingut (≥ 50 % dels bytes amb còpia allà).
    if a.bytes:
        same = sorted(drive_of_bit[b] for b, nb in a.copy_bytes.items() if nb >= SAME_CONTENT_SHARE * a.bytes)
        row["same_content_drives"] = json.dumps(same) if same else None

    # Sèrie, projecte i any: els imbricats hereten del pare.
    if c.kind == "capa" and c.parent is not None:
        pr = c.parent.row
        for k in ("proposed_serie", "serie_match", "proposed_project", "proposed_year", "canonical_proposed"):
            row[k] = pr.get(k)
        # La capa té sèrie pròpia si la seua ruta en diu una (el component més profund mana):
        # si difereix de la del projecte pare, és el senyal que el pare agrupa diversos llocs.
        serie, match = vocab.match_serie(c.path)
        if match and serie != pr.get("proposed_serie"):
            row["proposed_serie"] = serie
            row["serie_match"] = f"{match} | pare: {pr.get('proposed_serie')}"
    elif c.kind == "recuperacio":
        row["proposed_serie"], row["proposed_project"] = "varis", RECUP_PROJECT
        row["proposed_year"] = int(RECUP_PROJECT[:4])
    elif c.kind == "projecte":
        serie, match = vocab.match_serie(c.path)
        if match is None and conn is not None and vocab.serie_from_content:
            serie, match = vocab.match_serie_in_names(content_names(conn, c))
            # «suggerit:» deixa la sèrie a varis però conserva la pista a serie_match
        row["proposed_serie"], row["serie_match"] = serie, match
        year = year_from_name(c.name)
        p = parent_of(c.path)
        while year is None and p:   # any del contenidor (PROJ26, FOT-VID25, PROJECTES2022)
            year = year_from_name(p.rsplit(SEP, 1)[-1])
            p = parent_of(p)
        if year is None:
            for group in (("ORIG",), ("PROJ",), ("PROD", "DOC")):
                ys = [a.ymin[g] for g in group if g in a.ymin]
                if ys:
                    year = min(ys)
                    break
        if year is None and a.ymin:
            year = min(a.ymin.values())
        row["proposed_year"] = year
        desc = make_descriptor(c.name, vocab.series_by_name.get(serie), year)
        row["proposed_project"] = f"{year if year else 'AAAA'}_{desc}"
    c.row = row
    return row


def propose_canonical(rows: list[dict[str, Any]], drive_info: dict[str, dict[str, Any]]) -> None:
    """Regla §10: entre candidats projecte amb el mateix projecte proposat i contingut compartit,
    guanya el més complet → disc sense errors de lectura → NTFS → mtime més antic → salut."""
    groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for r in rows:
        if r["kind"] == "projecte" and r["proposed_project"]:
            groups[(r["proposed_serie"], r["proposed_project"])].append(r)
    for members in groups.values():
        # components connexos per contingut compartit (en qualsevol direcció)
        shared = {id(r): set(json.loads(r["same_content_drives"] or "[]")) for r in members}
        comp_of: dict[int, int] = {}
        comps: list[list[dict[str, Any]]] = []
        for r in members:
            linked = [comp_of[id(o)] for o in members if id(o) in comp_of and
                      (o["drive_id"] in shared[id(r)] or r["drive_id"] in shared[id(o)])]
            if not linked:
                comp_of[id(r)] = len(comps)
                comps.append([r])
            else:
                target = min(linked)
                for ci in sorted(set(linked), reverse=True):
                    if ci != target:
                        for o in comps[ci]:
                            comp_of[id(o)] = target
                        comps[target] += comps[ci]
                        comps[ci] = []
                comp_of[id(r)] = target
                comps[target].append(r)
        for comp in comps:
            if not comp:
                continue
            def key(r: dict[str, Any]):
                d = drive_info.get(r["drive_id"], {})
                return (-r["files_count"], -r["total_bytes"], 1 if d.get("read_errors") else 0,
                        0 if (d.get("filesystem") or "").upper() == "NTFS" else 1,
                        r["min_mtime"] or "9999", 0 if d.get("health_status") == "Healthy" else 1)
            winner = min(comp, key=key)
            for r in comp:
                r["canonical_proposed"] = 1 if r is winner else 0


# ---------------------------------------------------------------------------
# Execució
# ---------------------------------------------------------------------------

def run(conn: sqlite3.Connection, vocab: Vocab, drives: list[str] | None = None,
        recalc: bool = False, log=print) -> list[dict[str, Any]]:
    """Genera els candidats dels discs indicats (o de tots) i els desa a `candidates`.
    Retorna les files generades (totes les de la taula, ordenades per disc i ruta)."""
    t0 = time.time()
    all_drives = [r[0] for r in conn.execute("SELECT drive_id FROM drives ORDER BY drive_id")]
    drives = drives or all_drives
    unknown = [d for d in drives if d not in all_drives]
    if unknown:
        raise SystemExit(f"Discs desconeguts: {', '.join(unknown)}")
    drive_info = {r[0]: {"filesystem": r[1], "health_status": r[2], "serial": r[3] or r[4]}
                  for r in conn.execute("SELECT drive_id, filesystem, health_status, serial_number, "
                                        "volume_serial FROM drives")}
    for d, n in conn.execute("SELECT drive_id, COUNT(*) FROM read_errors GROUP BY drive_id"):
        drive_info.setdefault(d, {})["read_errors"] = n
    scan_set = {d: s for d, s in conn.execute(
        "SELECT drive_id, MAX(scan_id) FROM scans WHERE status = 'done' GROUP BY drive_id")}
    bit_of = {d: 1 << i for i, d in enumerate(all_drives)}
    drive_of_bit = {b: d for d, b in bit_of.items()}

    log("Pas 1: classes d'anàlisi (inventory.analysis_class)…")
    for d in drives:
        update_analysis_classes(conn, d, recalc=recalc, log=log)
    keys = build_copy_keys(conn, bit_of, log)

    log("Pas 2 i 3: candidats, agregats i proposta…")
    rows: list[dict[str, Any]] = []
    for d in drives:
        t1 = time.time()
        cands = detect_candidates(conn, d, vocab)
        residu = aggregate_drive(conn, d, cands, keys, bit_of[d])
        ordered = sorted(cands.values(), key=lambda c: (c.depth, c.path))
        drows = [propose(c, vocab, drive_of_bit, conn) for c in ordered]
        rc = Cand(d, "", "residu", False)
        rc.acc = residu
        drows.append(propose(rc, vocab, drive_of_bit))
        rows += drows
        log(f"  {d}: {len(cands):,} candidats, residu {residu.bytes / 1e9:,.1f} GB ({time.time() - t1:.0f} s)")

    # Canònica: cal el conjunt sencer (també els discs no regenerats, que es llegeixen de la taula).
    keep: list[dict[str, Any]] = []
    if set(drives) != set(all_drives):
        keep = read_candidates(conn, exclude=drives)
    propose_canonical(rows + keep, drive_info)
    by_key = {(r["drive_id"], r["relative_path"]): r for r in rows}
    for r in rows:
        if r["kind"] == "capa":
            p = by_key.get((r["drive_id"], r["parent_path"]))
            if p:
                r["canonical_proposed"] = p["canonical_proposed"]

    write_candidates(conn, rows, drives, scan_set)
    log(f"Taula candidates: {len(rows):,} files escrites ({time.time() - t0:.0f} s en total)")
    return read_candidates(conn)


def read_candidates(conn: sqlite3.Connection, exclude: list[str] | None = None) -> list[dict[str, Any]]:
    conn.row_factory = sqlite3.Row
    try:
        sql = "SELECT * FROM candidates"
        params: list[Any] = []
        if exclude:
            sql += f" WHERE drive_id NOT IN ({','.join('?' * len(exclude))})"
            params = list(exclude)
        return [dict(r) for r in conn.execute(sql + " ORDER BY drive_id, relative_path", params)]
    finally:
        conn.row_factory = None


def write_candidates(conn: sqlite3.Connection, rows: list[dict[str, Any]], drives: list[str],
                     scan_set: dict[str, int]) -> None:
    """Regenera `candidates` (els discs indicats) conservant el candidate_id de les rutes que ja hi eren."""
    ph = ",".join("?" * len(drives))
    old = {(d, p): i for i, d, p in conn.execute(
        f"SELECT candidate_id, drive_id, relative_path FROM candidates WHERE drive_id IN ({ph})", drives)}
    gen = json.dumps({"tool": TOOL_VERSION, "scans": {d: scan_set.get(d) for d in drives}}, sort_keys=True)
    now = schema.utc_now_iso()
    cols = ["candidate_id", "drive_id", "relative_path", "kind", "parent_path", "files_count", "total_bytes",
            "unique_bytes_approx", *[f"b_{c.lower()}" for c in CLASSES], "min_mtime", "max_mtime",
            "markers_json", "proposed_serie", "serie_match", "proposed_project", "proposed_layer",
            "proposed_year", "layer_confidence", "same_content_drives", "canonical_proposed",
            "generated_scan_set", "generated_utc"]
    values = []
    for r in rows:
        r = dict(r)
        r["candidate_id"] = old.get((r["drive_id"], r["relative_path"]))
        r["generated_scan_set"], r["generated_utc"] = gen, now
        values.append(tuple(r[c] for c in cols))
    # primer les files amb id conservat, després les noves (reben ids nous per darrere)
    values.sort(key=lambda v: (v[0] is None, v[0] or 0))
    conn.execute("BEGIN")
    try:
        conn.execute(f"DELETE FROM candidates WHERE drive_id IN ({ph})", drives)
        conn.executemany(f"INSERT INTO candidates ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
                         values)
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise


# ---------------------------------------------------------------------------
# Eixida: CSV i resum
# ---------------------------------------------------------------------------

def _gb(n: int | None) -> str:
    return f"{(n or 0) / 1e9:.2f}".replace(".", ",")


def write_csv(rows: list[dict[str, Any]], path: Path, drive_info: dict[str, dict[str, Any]]) -> None:
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f, delimiter=";", lineterminator="\r\n")
        w.writerow(CSV_GENERATED + CSV_REVIEW)
        for r in rows:
            total = r["total_bytes"] or 0
            uniq = r["unique_bytes_approx"] or 0
            w.writerow([
                r["candidate_id"], r["drive_id"], drive_info.get(r["drive_id"], {}).get("serial") or "",
                r["relative_path"] or "(residu)", r["kind"], r["parent_path"] or "",
                r["files_count"], total, _gb(total), uniq, f"{100 * uniq / total:.0f}" if total else "",
                *[_gb(r[f"b_{c.lower()}"]) for c in CLASSES],
                r["min_mtime"] or "", r["max_mtime"] or "", r["markers_json"] or "",
                r["proposed_serie"] or "", r["serie_match"] or "", r["proposed_project"] or "",
                r["proposed_year"] or "", r["proposed_layer"] or "", r["layer_confidence"] or "",
                ", ".join(json.loads(r["same_content_drives"])) if r["same_content_drives"] else "",
                r["canonical_proposed"],
            ] + [""] * len(CSV_REVIEW))


def print_summary(rows: list[dict[str, Any]], log=print) -> dict[str, Any]:
    """Resum per pantalla. Retorna les xifres clau (per a proves)."""
    top = [r for r in rows if r["kind"] != "capa"]
    total_bytes = sum(r["total_bytes"] for r in top)
    log("")
    log("== Candidats per tipus (bytes dels de primer nivell; les capes són part del seu projecte)")
    by_kind: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    for r in rows:
        by_kind[r["kind"]][0] += 1
        if r["kind"] != "capa":
            by_kind[r["kind"]][1] += r["total_bytes"]
    for k in schema.CANDIDATE_KINDS:
        n, b = by_kind.get(k, [0, 0])
        pct = 100 * b / total_bytes if total_bytes else 0
        log(f"   {k:<12} {n:6,} candidats {b / 1e12:8.2f} TB {pct:5.1f} %" if k != "capa"
            else f"   {k:<12} {n:6,} candidats")
    log(f"   {'total':<12} {len(rows):6,} files     {total_bytes / 1e12:8.2f} TB")

    proj = [r for r in rows if r["kind"] == "projecte"]
    proj_bytes = sum(r["total_bytes"] for r in proj)
    log("")
    log("== Confiança de la capa proposada (projectes i capes)")
    conf_rows = [r for r in rows if r["kind"] in ("projecte", "capa")]
    for conf in ("regla", "nom", "ambigu"):
        rs = [r for r in conf_rows if r["layer_confidence"] == conf]
        b = sum(r["total_bytes"] for r in rs if r["kind"] == "projecte")
        log(f"   {conf:<8} {len(rs):6,} ({100 * len(rs) / len(conf_rows) if conf_rows else 0:4.0f} %)"
            f"   bytes de projecte {b / 1e12:7.2f} TB ({100 * b / proj_bytes if proj_bytes else 0:4.0f} %)")

    log("")
    log("== Sèries proposades (candidats projecte de primer nivell)")
    by_serie: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    matched_bytes = 0
    for r in proj:
        by_serie[r["proposed_serie"] or "?"][0] += 1
        by_serie[r["proposed_serie"] or "?"][1] += r["total_bytes"]
        if r["serie_match"] and not r["serie_match"].startswith("suggerit:"):
            matched_bytes += r["total_bytes"]
    for s, (n, b) in sorted(by_serie.items(), key=lambda kv: -kv[1][1]):
        log(f"   {s:<20} {n:5,} {b / 1e12:8.2f} TB {100 * b / proj_bytes if proj_bytes else 0:5.1f} %")
    pct_matched = 100 * matched_bytes / proj_bytes if proj_bytes else 0
    path_bytes = sum(r["total_bytes"] for r in proj if r["serie_match"]
                     and not r["serie_match"].startswith(("dins:", "suggerit:")))
    fora_bytes = sum(r["total_bytes"] for r in proj if r["proposed_layer"] == "FORA" and not r["serie_match"])
    log(f"   amb serie_match: {matched_bytes / 1e12:.2f} TB de {proj_bytes / 1e12:.2f} TB ({pct_matched:.1f} %):"
        f" per la ruta {100 * path_bytes / proj_bytes if proj_bytes else 0:.1f} %,"
        f" pel contingut («dins:») {100 * (matched_bytes - path_bytes) / proj_bytes if proj_bytes else 0:.1f} %;"
        f" sense comptar els «varis» FORA (imatges de disc, programari…):"
        f" {100 * matched_bytes / (proj_bytes - fora_bytes) if proj_bytes > fora_bytes else 0:.1f} %")

    varis = sorted((r for r in proj if r["proposed_serie"] == "varis"), key=lambda r: -r["total_bytes"])
    log("")
    log(f"== Candidats a «varis»: {len(varis):,} ({sum(r['total_bytes'] for r in varis) / 1e12:.2f} TB); els 20 més grans")
    for r in varis[:20]:
        log(f"   {r['total_bytes'] / 1e9:8.1f} GB  {r['drive_id']:<10} {r['relative_path'][:60]:<60} {r['serie_match'] or ''}")
    return {"rows": len(rows), "total_bytes": total_bytes, "proj_bytes": proj_bytes,
            "pct_serie_matched": pct_matched, "by_kind": dict(by_kind)}


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", type=Path, default=database_path, help="Ruta de la DB")
    ap.add_argument("--vocab", type=Path, default=PROJECT_ROOT / "data" / "vocabulari.json",
                    help="Vocabulari (per defecte data/vocabulari.json)")
    ap.add_argument("--drive", action="append", default=[], help="Només aquest drive_id (repetible)")
    ap.add_argument("--recalc", action="store_true", help="Torna a classificar tots els fitxers (diccionari canviat)")
    ap.add_argument("--no-csv", action="store_true", help="No escriure el CSV")
    ap.add_argument("--csv-dir", type=Path, default=PROJECT_ROOT / "data", help="Carpeta del CSV (per defecte data/)")
    args = ap.parse_args(argv)

    if not args.db.is_file():
        print(f"No existeix la DB: {args.db}", file=sys.stderr)
        return 2
    try:
        vocab = Vocab.load(args.vocab)
    except (OSError, ValueError, json.JSONDecodeError) as e:
        print(f"Vocabulari no vàlid ({args.vocab}): {e}", file=sys.stderr)
        return 2
    conn = sqlite3.connect(str(args.db), timeout=60.0)
    try:
        if schema.get_user_version(conn) < schema.SCHEMA_VERSION:
            print(f"Cal l'esquema v{schema.SCHEMA_VERSION}: executa  python -m app.migrate", file=sys.stderr)
            return 2
        schema.set_pragmas_for_writes(conn)
        t0 = time.time()
        print(f"DB: {args.db}  vocabulari: {args.vocab} ({len(vocab.series)} sèries)")
        rows = run(conn, vocab, args.drive or None, recalc=args.recalc)
        drive_info = {r[0]: {"serial": r[1] or r[2]} for r in conn.execute(
            "SELECT drive_id, serial_number, volume_serial FROM drives")}
        if not args.no_csv:
            args.csv_dir.mkdir(parents=True, exist_ok=True)
            out = args.csv_dir / f"cataleg_{datetime.now():%Y-%m-%d}.csv"
            write_csv(rows, out, drive_info)
            print(f"CSV: {out} ({len(rows):,} files)")
        print_summary(rows)
        print(f"\nTemps total: {time.time() - t0:.0f} s")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
