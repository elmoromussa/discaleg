"""Cobertura navegable d'un disc: a cada nivell de carpeta, què hi ha dins i fora de l'arxiu virtual.

Complementa `queries_arxiu.ArxiuCalc.coverage` (resum per disc i per paper) amb una vista per
carpeta, com TreeSize: les subcarpetes directes d'una carpeta amb el total, la part coberta per
algun membre, la part fora de tot espai, el % del disc i l'any i la classe dominants del que
queda fora. Només lectura; reutilitza els totals per membre de l'ArxiuCalc (regla del membre
més profund) i les mateixes taules temporals.

Per a cada subcarpeta: el total surt de `folder_stats` (un rang de l'índex (drive_id,
folder_path) agrupat pel segment següent, dècimes de segon en un disc gran); la part coberta
és la suma dels totals dels membres que hi ha a dins (o tot, si la carpeta és dins d'un
membre); la resta és fora. L'any i la classe dominants es calculen només sobre els fitxers
fora de tot membre (els forats entre rangs coberts, com a `coverage_details`).
"""
from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any

from app.queries_arxiu import FOLDER_INDEX, PATH_INDEX, SEP, ArxiuCalc, _role_key
from app.queries_spaces import SpaceError

_cache: dict[str, Any] = {"stamps": None, "data": {}}


def normalize_folder(folder: str) -> str:
    return (folder or "").strip().replace("/", SEP).strip(SEP)


def folder_bounds(folder: str) -> tuple[str, str | None]:
    """[lo, hi) del subarbre d'una carpeta (format DB); l'arrel és tot el disc."""
    if not folder:
        return "", None
    return folder + SEP, folder + "]"


def _dominant(counter: Counter) -> tuple[str, float]:
    total = sum(counter.values())
    if not total:
        return "", 0.0
    k, v = counter.most_common(1)[0]
    return k, 100.0 * v / total


def folder_coverage(calc: ArxiuCalc, drive_id: str, folder: str, stamps: Any = None,
                    details: bool = True) -> dict[str, Any]:
    """Nivell de cobertura d'una carpeta d'un disc (vegeu el mòdul). Es desa per `stamps`
    (segells d'inventari i de membres de la memòria cau): canvien → s'oblida tot."""
    folder = normalize_folder(folder)
    if _cache["stamps"] != stamps:
        _cache["stamps"] = stamps
        _cache["data"] = {}
    key = (drive_id, folder, details)
    if key in _cache["data"]:
        return _cache["data"][key]
    out = _folder_coverage(calc, drive_id, folder, details)
    _cache["data"][key] = out
    return out


def _folder_coverage(calc: ArxiuCalc, drive_id: str, folder: str, details: bool) -> dict[str, Any]:
    c = calc.conn
    drive = c.execute("SELECT files_count, total_bytes FROM drives WHERE drive_id = ?", (drive_id,)).fetchone()
    if drive is None:
        raise SpaceError(f"El disc «{drive_id}» no és a la DB.")
    drive_bytes = int(drive[1] or 0)
    here = c.execute("SELECT files_count, total_bytes FROM folder_stats WHERE drive_id = ? AND folder_path = ?",
                     (drive_id, folder)).fetchone()
    if here is None and folder:
        raise SpaceError(f"La carpeta «{folder}» no és a l'inventari de {drive_id}.")
    lo, hi = folder_bounds(folder)
    n = len(lo) + 1
    seg = (f"CASE WHEN instr(substr({{col}}, {n}), '\\') = 0 THEN substr({{col}}, {n}) "
           f"ELSE substr({{col}}, {n}, instr(substr({{col}}, {n}), '\\') - 1) END")
    upper = "AND folder_path < ?" if hi is not None else ""
    params: list[Any] = [drive_id, lo] + ([hi] if hi is not None else [])
    children: dict[str, dict[str, Any]] = {}
    for name, files, nbytes, nfold in c.execute(
            f"""
            SELECT {seg.format(col='folder_path')} AS s, SUM(files_count), SUM(total_bytes), COUNT(*)
            FROM folder_stats INDEXED BY {FOLDER_INDEX}
            WHERE drive_id = ? AND folder_path >= ? {upper}
            GROUP BY s
            """, params):
        children[name] = {"name": name, "kind": "folder", "path": lo + name,
                          "files": int(files or 0), "bytes": int(nbytes or 0), "folders": int(nfold or 0)}
    children.pop("", None)  # a l'arrel, la fila de la carpeta mateixa (fitxers directes, més avall)

    # membres del disc: totals (membre més profund), paper i espai
    totals = calc.item_totals()
    by_item: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for m in calc.members:
        it = calc.items[m["item_id"]]
        if it["drive_id"] == drive_id:
            by_item[m["item_id"]].append(m)
    items = [calc.items[i] for i in by_item]
    folder_items = [it for it in items if it["kind"] == "folder"]

    def member_info(item_id: int) -> list[dict[str, Any]]:
        return [{"role": _role_key(m["role"]), "space_id": m["space_id"],
                 "space_path": calc.spaces[m["space_id"]]["virtual_path"]} for m in by_item[item_id]]

    # la carpeta mateixa és dins d'un membre? (el més pròxim)
    inside = None
    for it in folder_items:
        rp = it["relative_path"]
        if rp == folder or (folder and folder.startswith(rp + SEP)) or rp == "":
            if inside is None or len(rp) > len(inside["relative_path"]):
                inside = it
    rows: list[dict[str, Any]] = []
    for name, ch in children.items():
        path = ch["path"]
        exact = next((it for it in folder_items if it["relative_path"] == path), None)
        under = [it for it in items if it["relative_path"].startswith(path + SEP)]
        if inside is not None or exact is not None:
            covered = ch["bytes"]
            cov_files = ch["files"]
        else:
            covered = sum(totals[it["item_id"]]["bytes"] for it in under)
            cov_files = sum(totals[it["item_id"]]["files"] for it in under)
        unc = max(0, ch["bytes"] - covered)
        unc_files = max(0, ch["files"] - cov_files)
        ch.update({
            "covered_bytes": min(covered, ch["bytes"]), "uncovered_bytes": unc, "uncovered_files": unc_files,
            "pct_disc": 100.0 * ch["bytes"] / drive_bytes if drive_bytes else 0.0,
            "member": member_info(exact["item_id"]) if exact is not None else [],
            "inside": {"path": inside["relative_path"], "members": member_info(inside["item_id"])} if inside is not None and exact is None else None,
            "members_under": len(under) if exact is None and inside is None else 0,
            "year": "", "year_pct": 0.0, "cls": "", "cls_pct": 0.0,
        })
        rows.append(ch)
    # fitxers directes de la carpeta
    direct_files = int(here[0] or 0) if here is not None else 0
    direct_bytes = int(here[1] or 0) if here is not None else 0
    if direct_files or direct_bytes:
        if inside is not None:
            covered, cov_files = direct_bytes, direct_files
        else:
            file_items = [it for it in items if it["kind"] == "file"
                          and it["relative_path"].rsplit(SEP, 1)[0] == folder
                          and (SEP in it["relative_path"] or not folder)]
            if not folder:
                file_items = [it for it in items if it["kind"] == "file" and SEP not in it["relative_path"]]
            covered = sum(totals[it["item_id"]]["bytes"] for it in file_items)
            cov_files = sum(totals[it["item_id"]]["files"] for it in file_items)
        rows.append({
            "name": "", "kind": "files", "path": folder, "files": direct_files, "bytes": direct_bytes, "folders": 0,
            "covered_bytes": min(covered, direct_bytes), "uncovered_bytes": max(0, direct_bytes - covered),
            "uncovered_files": max(0, direct_files - cov_files),
            "pct_disc": 100.0 * direct_bytes / drive_bytes if drive_bytes else 0.0,
            "member": [], "inside": {"path": inside["relative_path"], "members": member_info(inside["item_id"])} if inside is not None else None,
            "members_under": 0, "year": "", "year_pct": 0.0, "cls": "", "cls_pct": 0.0,
        })

    # any i classe dominants del que queda fora (forats entre rangs coberts, dins del subarbre)
    if details and inside is None and any(r["uncovered_bytes"] or r["uncovered_files"] for r in rows):
        ranges = sorted((it["range_lo"], it["range_hi"]) for it in folder_items)
        merged: list[tuple[str, str]] = []
        for rlo, rhi in ranges:
            if merged and rlo <= merged[-1][1]:
                if rhi > merged[-1][1]:
                    merged[-1] = (merged[-1][0], rhi)
            else:
                merged.append((rlo, rhi))
        gaps: list[tuple[str, str | None]] = []
        prev = lo
        for rlo, rhi in merged:
            if rhi <= lo:
                continue
            if hi is not None and rlo >= hi:
                break
            if rlo > prev:
                gaps.append((prev, rlo))
            prev = max(prev, rhi)
        if hi is None or prev < hi:
            gaps.append((prev, hi))
        years: dict[str, Counter] = defaultdict(Counter)
        classes: dict[str, Counter] = defaultdict(Counter)
        fseg = seg.format(col="relative_path")
        for glo, ghi in gaps:
            if ghi is not None and glo >= ghi:
                continue
            q = f"""
                SELECT {fseg} AS s, substr(modified_utc, 1, 4) AS y, analysis_class AS cls, COUNT(*), SUM(size_bytes)
                FROM inventory INDEXED BY {PATH_INDEX}
                WHERE drive_id = ? AND relative_path >= ? {"AND relative_path < ?" if ghi is not None else ""}
                  AND missing_since_utc IS NULL
                GROUP BY s, y, cls
                """
            p: list[Any] = [drive_id, glo] + ([ghi] if ghi is not None else [])
            for s, y, cls, nfiles, nbytes in c.execute(q, p):
                years[s][y or "?"] += int(nfiles)
                classes[s][cls or "?"] += int(nbytes or 0)
        for r in rows:
            k = r["name"]
            if k in years or k in classes:
                r["year"], r["year_pct"] = _dominant(years[k])
                r["cls"], r["cls_pct"] = _dominant(classes[k])

    rows.sort(key=lambda r: (-r["bytes"], r["name"].lower()))
    total_bytes = sum(r["bytes"] for r in rows)
    total_files = sum(r["files"] for r in rows)
    covered_bytes = sum(r["covered_bytes"] for r in rows)
    for r in rows:
        r["pct_here"] = 100.0 * r["bytes"] / total_bytes if total_bytes else 0.0
        r["bar_cov"] = 100.0 * r["covered_bytes"] / total_bytes if total_bytes else 0.0
        r["bar_unc"] = 100.0 * r["uncovered_bytes"] / total_bytes if total_bytes else 0.0
    crumbs = []
    acc = ""
    for part in folder.split(SEP) if folder else []:
        acc = part if not acc else acc + SEP + part
        crumbs.append({"name": part, "path": acc})
    return {
        "drive_id": drive_id, "folder": folder, "crumbs": crumbs,
        "drive_bytes": drive_bytes, "drive_files": int(drive[0] or 0),
        "bytes": total_bytes, "files": total_files, "covered_bytes": covered_bytes,
        "uncovered_bytes": max(0, total_bytes - covered_bytes),
        "inside": {"path": inside["relative_path"], "members": member_info(inside["item_id"])} if inside is not None else None,
        "rows": rows,
    }
