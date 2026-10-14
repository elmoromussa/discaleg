"""Arxiu virtual: importació del CSV de conjunts, arbre de destí, cobertura, comprovacions i
exportació (docs/migracio.md, «Arxiu virtual»).

  python tools/espais.py --import data/conjunts_2026-10-14.csv          # prova (no escriu res)
  python tools/espais.py --import data/conjunts_2026-10-14.csv --apply  # escriu els espais
  python tools/espais.py --tree [--root NOM]
  python tools/espais.py --coverage INT-5        (o --coverage all)
  python tools/espais.py --check
  python tools/espais.py --export data/conjunts_export.csv

Només llig la DB, llevat de --import amb --apply (escriu a spaces, items i space_members).
CSV: UTF-8 amb BOM i separador «;». Els errors de la importació van a
data/conjunts_errors.csv (columna «nivell»: error bloqueja tot; avis no).
"""
from __future__ import annotations

import argparse
import csv
import sqlite3
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from app import queries_spaces, schema  # noqa: E402
from app.config import database_path  # noqa: E402
from app.queries_arxiu import ROLE_NONE, SEP, ArxiuCalc  # noqa: E402
from app.queries_spaces import SUBPATH_ROOT, SpaceError  # noqa: E402
from app.schema import PROJECT_LAYERS  # noqa: E402

CSV_COLUMNS = ("serie", "projecte", "disc", "ruta", "gb", "unics", "capa", "paper", "nota")
EXPORT_COLUMNS = (*CSV_COLUMNS, "ruta_virtual", "target_subpath")
ROLE_FROM_PAPER = {"CAN": "canonica", "COP": "copia", "PART": "part", "DER": "descartar",
                   "QUAR": "quarantena", "TERC": "tercers", "?": None, "": None}
PAPER_FROM_ROLE = {"canonica": "CAN", "copia": "COP", "part": "PART", "descartar": "DER",
                   "quarantena": "QUAR", "tercers": "TERC", None: "?"}
ROLE_LABEL = {"canonica": "canònica", "copia": "còpia", "part": "part", "descartar": "descartar",
              "quarantena": "quarantena", "tercers": "tercers", ROLE_NONE: "sense paper"}
# Sèries del CSV que són fons propis, fora d'ARXIU (nom del fons de destí).
SPECIAL_FONS = {"personal": "PERSONAL", "_tercers": "_TERCERS", "_eines": "_EINES",
                "_descartar": "_DESCARTAR"}
MAIN_FONS = "ARXIU"
EMPTY = ("", "-")


def fmt_bytes(n: int | None) -> str:
    n = n or 0
    if abs(n) >= 1e12:
        return f"{n / 1e12:.2f} TB".replace(".", ",")
    if abs(n) >= 1e9:
        return f"{n / 1e9:.1f} GB".replace(".", ",")
    if abs(n) >= 1e6:
        return f"{n / 1e6:.0f} MB"
    return f"{n / 1e3:.0f} kB"


def fmt_int(n: int) -> str:
    return f"{n:,}".replace(",", ".")


def _ruta_db(s: str) -> str:
    return (s or "").strip().replace("/", "\\").strip("\\")


# ---------------------------------------------------------------------------
# Importació
# ---------------------------------------------------------------------------

def parse_capa(capa: str) -> tuple[str | None, str | None, str | None]:
    """(capa, target_subpath, error). Capa simple, «RAW\\processades», composta («RAW+PROJ»),
    «-» o «?» (sense capa: el membre va al projecte)."""
    c = (capa or "").strip().replace("/", "\\")
    if c in EMPTY or c == "?":
        return None, None, None
    head, _, rest = c.partition("\\")
    head = head.upper()
    if "+" in head:
        parts = [p.strip() for p in head.split("+")]
        if all(p in PROJECT_LAYERS for p in parts):
            return None, None, None
        return None, None, f"capa composta desconeguda: «{capa}»"
    if head not in PROJECT_LAYERS:
        return None, None, f"capa desconeguda: «{capa}» (cal {', '.join(PROJECT_LAYERS)}, composta, «-» o «?»)"
    if rest:
        try:
            sub = queries_spaces.check_subpath(rest)
        except SpaceError as e:
            return None, None, str(e)
        return head, sub, None
    return head, None, None


def _get_or_create(conn: sqlite3.Connection, cache: dict[str, dict[str, Any]], parent: dict[str, Any] | None,
                   kind: str, target_name: str, created: list[str], validate_name: bool = True) -> dict[str, Any]:
    parent_id = parent["space_id"] if parent else None
    vpath = (parent["virtual_path"] + SEP if parent else "") + target_name
    if vpath in cache:
        return cache[vpath]
    space = queries_spaces.find_archive_child(conn, parent_id, target_name)
    if space is None:
        space = queries_spaces.create_space(
            conn, name=target_name, kind="manual", parent_id=parent_id, archive_kind=kind,
            target_name=target_name, commit=False, validate_name=validate_name,
        )
        created.append(vpath)
    space["virtual_path"] = vpath
    cache[vpath] = space
    return space


def import_csv(conn: sqlite3.Connection, csv_path: Path, errors_path: Path, apply: bool) -> dict[str, Any]:
    """Crea (o actualitza) l'arbre fons › sèrie › projecte › capa i els membres del CSV.

    Tot es fa dins d'una transacció: amb `apply` fals, o si hi ha cap error, es desfà."""
    with open(csv_path, encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f, delimiter=";")
        missing = [c for c in CSV_COLUMNS if c not in (reader.fieldnames or [])]
        if missing:
            raise SystemExit(f"Al CSV li falten les columnes: {', '.join(missing)}")
        rows = list(reader)
    drives = {r[0] for r in conn.execute("SELECT drive_id FROM drives")}
    issues: list[dict[str, Any]] = []

    def issue(level: str, i: int, row: dict[str, Any], col: str, msg: str) -> None:
        issues.append({"nivell": level, "fila": i, "disc": row.get("disc", ""), "ruta": row.get("ruta", ""),
                       "columna": col, "valor": (row.get(col) or "") if col else "", "error": msg})

    cache: dict[str, dict[str, Any]] = {}
    created: list[str] = []
    members_added = members_updated = 0
    seen_members: Counter[tuple[int, str, str]] = Counter()
    conn.execute("BEGIN")
    try:
        for i, row in enumerate(rows, 2):
            row = {k: (v or "").strip() for k, v in row.items() if k}
            serie, projecte = row["serie"], row["projecte"]
            disc, ruta = row["disc"], _ruta_db(row["ruta"])
            paper = row["paper"].upper()
            if paper not in ROLE_FROM_PAPER:
                issue("error", i, row, "paper", f"paper desconegut: «{row['paper']}» (CAN, COP, PART, DER, ?)")
                continue
            role = ROLE_FROM_PAPER[paper]
            capa, subpath, err = parse_capa(row["capa"])
            if err:
                issue("error", i, row, "capa", err)
                continue
            # --- espai de destí
            try:
                if serie.lower() in SPECIAL_FONS:
                    fons = _get_or_create(conn, cache, None, "fons", SPECIAL_FONS[serie.lower()], created)
                    target = fons
                    if projecte not in EMPTY:
                        target = _get_or_create(conn, cache, fons, "carpeta", projecte, created)
                else:
                    if not queries_spaces.RE_SERIE.match(serie):
                        issue("error", i, row, "serie", f"nom de sèrie no vàlid: «{serie}»")
                        continue
                    fons = _get_or_create(conn, cache, None, "fons", MAIN_FONS, created)
                    sp_serie = _get_or_create(conn, cache, fons, "serie", serie, created)
                    target = sp_serie
                    if projecte in EMPTY:
                        issue("avis", i, row, "projecte", "fila sense projecte: el membre va a la sèrie")
                    else:
                        valid = bool(queries_spaces.RE_PROJECT.match(projecte)) and 2 <= len(projecte) - 5 <= 30
                        if not valid:
                            issue("avis", i, row, "projecte",
                                  f"nom de projecte fora de la regla AAAA_descriptor: «{projecte}» (es crea igualment)")
                        target = _get_or_create(conn, cache, sp_serie, "projecte", projecte, created,
                                                validate_name=valid)
                        if capa:
                            target = _get_or_create(conn, cache, target, "capa", capa, created)
            except SpaceError as e:
                issue("error", i, row, "serie", str(e))
                continue
            # --- membre
            if disc in EMPTY and ruta in EMPTY:
                issue("avis", i, row, "disc", "fila sense disc ni ruta: es crea l'espai sense membre")
                continue
            if disc not in drives:
                issue("error", i, row, "disc", f"disc desconegut: «{disc}»")
                continue
            if not ruta:
                issue("error", i, row, "ruta", "ruta buida")
                continue
            kind = "folder"
            if conn.execute("SELECT 1 FROM folder_stats WHERE drive_id = ? AND folder_path = ?",
                            (disc, ruta)).fetchone() is None:
                if conn.execute("SELECT 1 FROM inventory WHERE drive_id = ? AND relative_path = ? "
                                "AND missing_since_utc IS NULL", (disc, ruta)).fetchone():
                    kind = "file"
                else:
                    issue("error", i, row, "ruta", f"la carpeta «{ruta}» no és a l'inventari de {disc}")
                    continue
            key = (target["space_id"], disc, ruta.lower())
            seen_members[key] += 1
            if seen_members[key] > 1:
                issue("avis", i, row, "ruta", "fila repetida (mateix espai, disc i ruta): es queda l'última")
            before = conn.execute("SELECT COUNT(*) FROM space_members WHERE space_id = ?",
                                  (target["space_id"],)).fetchone()[0]
            res = queries_spaces.add_members(
                conn, target["space_id"],
                [{"kind": kind, "drive_id": disc, "path": ruta, "role": role,
                  "target_subpath": subpath, "note": row["nota"] or None}],
                commit=False,
            )
            if res["errors"]:
                issue("error", i, row, "ruta", "; ".join(res["errors"]))
                continue
            after = conn.execute("SELECT COUNT(*) FROM space_members WHERE space_id = ?",
                                 (target["space_id"],)).fetchone()[0]
            if after > before:
                members_added += 1
            else:
                members_updated += 1
        n_errors = sum(1 for x in issues if x["nivell"] == "error")
        if n_errors or not apply:
            conn.execute("ROLLBACK")
        else:
            conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    if issues:
        with open(errors_path, "w", encoding="utf-8-sig", newline="") as f:
            w = csv.writer(f, delimiter=";", lineterminator="\r\n")
            cols = ("nivell", "fila", "disc", "ruta", "columna", "valor", "error")
            w.writerow(cols)
            for x in issues:
                w.writerow([x[c] for c in cols])
    return {"rows": len(rows), "created": created, "members_added": members_added,
            "members_updated": members_updated, "issues": issues,
            "errors": sum(1 for x in issues if x["nivell"] == "error"),
            "warnings": sum(1 for x in issues if x["nivell"] == "avis"),
            "applied": apply and not any(x["nivell"] == "error" for x in issues)}


def print_import(res: dict[str, Any], errors_path: Path, apply: bool) -> None:
    print(f"Files llegides: {res['rows']}")
    print(f"Espais nous: {len(res['created'])}")
    for v in res["created"][:60]:
        print(f"  + {v}")
    if len(res["created"]) > 60:
        print(f"  … i {len(res['created']) - 60} més")
    print(f"Membres afegits: {res['members_added']} · actualitzats (ja hi eren): {res['members_updated']}")
    if res["issues"]:
        print(f"Incidències: {res['errors']} errors, {res['warnings']} avisos → {errors_path}")
        for x in res["issues"][:40]:
            print(f"  [{x['nivell']}] fila {x['fila']} {x['disc']} {x['ruta']} · {x['columna']}: {x['error']}")
        if len(res["issues"]) > 40:
            print(f"  … i {len(res['issues']) - 40} més al CSV")
    if res["errors"]:
        print("Hi ha errors: no s'ha escrit res.")
    elif not apply:
        print("Prova (sense --apply): no s'ha escrit res.")
    else:
        print("Escrit a la DB.")


# ---------------------------------------------------------------------------
# Arbre, cobertura, comprovacions, exportació
# ---------------------------------------------------------------------------

def _roles_summary(by_role: dict[str, dict[str, int]]) -> str:
    return ", ".join(f"{v['members']} {ROLE_LABEL.get(k, k)}" for k, v in by_role.items())


def find_root(calc: ArxiuCalc, name: str) -> int:
    n = name.strip().lower().replace("/", SEP)
    exact = [sid for sid, s in calc.spaces.items() if s["virtual_path"].lower() == n]
    if exact:
        return exact[0]
    cands = [sid for sid, s in calc.spaces.items() if (s["target_name"] or "").lower() == n
             or s["name"].lower() == n]
    if len(cands) == 1:
        return cands[0]
    if not cands:
        raise SystemExit(f"No hi ha cap espai d'arxiu «{name}».")
    paths = ", ".join(calc.spaces[c]["virtual_path"] for c in cands)
    raise SystemExit(f"Hi ha {len(cands)} espais «{name}»: digues la ruta virtual sencera ({paths}).")


def print_tree(calc: ArxiuCalc, root: int | None, show_members: bool = True) -> None:
    tree = calc.virtual_tree(root)

    def walk(node: dict[str, Any], depth: int) -> None:
        t = node["totals"]
        ind = "  " * depth
        state = f" [{node['state']}]" if node.get("state") else ""
        label = node["target_name"] or node["name"]
        print(f"{ind}{label}{state}  ({node['archive_kind']})  {fmt_bytes(t['bytes'])} · "
              f"{fmt_int(t['files'])} fitxers · únics {fmt_bytes(t['unique_bytes'])} · "
              f"{t['members']} membres ({_roles_summary(t['by_role']) or 'cap'})")
        if show_members:
            for m in node["members"]:
                paper = PAPER_FROM_ROLE.get(m["role"], m["role"] or "?")
                sub = f" → {m['target_subpath']}" if m["target_subpath"] and m["target_subpath"] != SUBPATH_ROOT else ""
                nested = " (niat)" if m.get("nested_in") else ""
                print(f"{ind}    {paper:<4} {m['drive_id']:<10} {m['relative_path']}  "
                      f"{fmt_bytes(m['bytes'])} · únics {m['unique_pct']:.0f} %{sub}{nested}")
        for ch in node["children"]:
            walk(ch, depth + 1)

    for n in tree:
        walk(n, 0)


def print_coverage(calc: ArxiuCalc, drive_id: str, max_level2: int = 12) -> None:
    cov = calc.coverage(drive_id)
    print(f"== {drive_id}: {fmt_bytes(cov['drive_bytes'])} · {fmt_int(cov['drive_files'])} fitxers")
    parts = [f"{ROLE_LABEL.get(k, k)} {fmt_bytes(v['bytes'])} ({v['members']})" for k, v in cov["covered"].items()]
    print(f"   cobert {fmt_bytes(cov['covered_bytes'])}: {'; '.join(parts) or 'res'}")
    print(f"   fora de tot espai: {fmt_bytes(cov['uncovered_bytes'])} · {fmt_int(cov['uncovered_files'])} fitxers")
    for g in cov["groups"]:
        name = g["level1"] or "(fitxers a l'arrel)"
        print(f"   {fmt_bytes(g['bytes']):>10}  {fmt_int(g['files']):>9}  {name}")
        kids = [c for c in g["children"] if c["level2"]]
        for ch in kids[:max_level2]:
            print(f"   {fmt_bytes(ch['bytes']):>10}  {fmt_int(ch['files']):>9}      {ch['level2']}")
        if len(kids) > max_level2:
            rest_b = sum(c["bytes"] for c in kids[max_level2:])
            print(f"   {fmt_bytes(rest_b):>10}  {'':>9}      … i {len(kids) - max_level2} carpetes més")


def print_check(calc: ArxiuCalc) -> int:
    findings = calc.check()
    if not findings:
        print("Cap conflicte.")
        return 0
    n_err = sum(1 for f in findings if f["severity"] == "error")
    print(f"{len(findings)} incidències: {n_err} errors, {len(findings) - n_err} avisos")
    for f in findings:
        print(f"  [{f['severity']}] {f['kind']}: {f['message']}")
    return n_err


def export_csv(calc: ArxiuCalc, path: Path) -> int:
    """Torna a escriure el CSV de conjunts des dels espais (una fila per membre)."""
    n = 0
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f, delimiter=";", lineterminator="\r\n")
        w.writerow(EXPORT_COLUMNS)

        def ancestors(sid: int) -> dict[str, str]:
            out: dict[str, str] = {}
            cur = calc.spaces.get(sid)
            while cur is not None:
                out.setdefault(cur["archive_kind"], cur["target_name"] or cur["name"])
                cur = calc.spaces.get(cur["parent_id"]) if cur["parent_id"] is not None else None
            return out

        def walk(sid: int) -> None:
            nonlocal n
            anc = ancestors(sid)
            serie = anc.get("serie") or anc.get("fons", "")
            if serie in SPECIAL_FONS.values():
                serie = next(k for k, v in SPECIAL_FONS.items() if v == serie)
            projecte = anc.get("projecte") or anc.get("carpeta") or "-"
            for m in calc.member_rows(sid):
                capa = anc.get("capa") or "-"
                if m["target_subpath"] and m["target_subpath"] != SUBPATH_ROOT and anc.get("capa"):
                    capa = f"{capa}\\{m['target_subpath']}"
                w.writerow([serie, projecte, m["drive_id"], m["relative_path"],
                            f"{m['bytes'] / 1e9:.0f}", f"{m['unique_pct']:.0f}", capa,
                            PAPER_FROM_ROLE.get(m["role"], m["role"] or "?"), m["note"] or "",
                            m["virtual_path"], m["target_subpath"] or ""])
                n += 1
            s = calc.spaces[sid]
            kids = sorted((c for c in s["children"] if c in calc.spaces),
                          key=lambda c: (calc.spaces[c]["sort_order"], calc.spaces[c]["target_name"] or ""))
            for c in kids:
                walk(c)

        for r in calc.roots:
            walk(r)
    return n


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description="Arxiu virtual: importació, arbre, cobertura, comprovacions.")
    ap.add_argument("--db", type=Path, default=database_path, help="Ruta de la DB")
    ap.add_argument("--import", dest="import_csv", type=Path, metavar="CSV",
                    help="Importa el CSV de conjunts (prova si no hi ha --apply)")
    ap.add_argument("--apply", action="store_true", help="Escriu de veritat (--import)")
    ap.add_argument("--errors", type=Path, default=None, help="CSV d'incidències (per defecte data/conjunts_errors.csv)")
    ap.add_argument("--tree", action="store_true", help="Arbre de destí amb totals")
    ap.add_argument("--root", default=None, help="Arrel de l'arbre (nom o ruta virtual)")
    ap.add_argument("--no-members", action="store_true", help="L'arbre sense la llista de membres")
    ap.add_argument("--coverage", metavar="DISC", default=None, help="Cobertura d'un disc (o «all»)")
    ap.add_argument("--check", action="store_true", help="Conflictes")
    ap.add_argument("--export", type=Path, metavar="CSV", help="Exporta el CSV de conjunts des dels espais")
    args = ap.parse_args(argv)
    if not (args.import_csv or args.tree or args.coverage or args.check or args.export):
        ap.print_help()
        return 2
    if not args.db.is_file():
        print(f"No existeix la DB: {args.db}", file=sys.stderr)
        return 1
    conn = sqlite3.connect(str(args.db), timeout=60.0, isolation_level=None)
    conn.row_factory = sqlite3.Row
    try:
        if schema.get_user_version(conn) < schema.SCHEMA_VERSION:
            print(f"Cal l'esquema v{schema.SCHEMA_VERSION}: executa  python -m app.migrate", file=sys.stderr)
            return 1
        rc = 0
        if args.import_csv:
            errors_path = args.errors or (PROJECT_ROOT / "data" / "conjunts_errors.csv")
            t0 = time.time()
            res = import_csv(conn, args.import_csv, errors_path, args.apply)
            print_import(res, errors_path, args.apply)
            print(f"({time.time() - t0:.1f} s)")
            if res["errors"]:
                rc = 1
        if args.tree or args.coverage or args.check or args.export:
            t0 = time.time()
            calc = ArxiuCalc(conn)
            if args.check:
                if print_check(calc):
                    rc = 1
            if args.tree:
                root = find_root(calc, args.root) if args.root else None
                print_tree(calc, root, show_members=not args.no_members)
            if args.coverage:
                drives = ([r[0] for r in conn.execute("SELECT drive_id FROM drives ORDER BY drive_id")]
                          if args.coverage.lower() == "all" else [args.coverage])
                for d in drives:
                    print_coverage(calc, d)
            if args.export:
                n = export_csv(calc, args.export)
                print(f"Exportades {n} files a {args.export}")
            print(f"({time.time() - t0:.1f} s)")
        return rc
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
