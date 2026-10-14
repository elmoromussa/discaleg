"""Arxiu virtual: totals, arbre de destí, cobertura i comprovacions. Només lectura.

L'arxiu virtual és l'arbre d'espais d'arxiu (`spaces.archive_kind`: fons › serie › projecte ›
capa › carpeta, amb `target_name` = carpeta de destí) i els seus membres (carpetes o fitxers
d'origen dels discs, `space_members.role` = paper, `target_subpath` = destí dins de l'espai).

Tots els càlculs són per conjunts sobre els índexs existents; cap bucle per fitxer en Python:

* **Segments** (`temp.arxiu_seg`): la regla «cada fitxer pertany al membre MÉS PROFUND que el
  conté» es resol una vegada en Python sobre la llista de membres (centenars), no sobre els
  fitxers: el rang de prefix de cada carpeta membre menys els rangs dels membres niats a dins
  dona els segments propis de cada membre, i cada fitxer cau en un sol segment.
* **Totals per membre**: suma de `folder_stats` (fitxers directes de cada carpeta) sobre els
  segments pel rang de l'índex (drive_id, folder_path); un fitxer membre es llig d'inventory.
* **Fitxers ≥ 1 MiB dels membres** (`temp.arxiu_mf`): rang de l'índex únic (drive_id,
  relative_path) sobre els segments, amb la clau de còpia `name_key` + mida.
* **Claus de còpia** (`temp.arxiu_keys`): un GROUP BY sobre l'índex cobridor
  idx_inventory_name_key de tota la DB (quantes còpies vives té cada clau i en quins discs).
* **Únics d'un espai**: bytes dels fitxers dels membres canònica/part que no tenen cap còpia
  fora de l'espai (comparant el recompte global de la clau amb el recompte dins de l'espai),
  més els fitxers < 1 MiB, que compten com a únics (mateix criteri que tools/cataleg.py).
* **Cobertura d'un disc**: el complement dels rangs coberts (forats entre rangs, sobre
  folder_stats), agrupat per carpeta de nivell 1 i 2.

Un `ArxiuCalc` construeix les taules temporals una vegada per connexió i les reutilitza en
totes les crides; les funcions de mòdul en creen un per comoditat.
"""
from __future__ import annotations

import sqlite3
from collections import defaultdict
from typing import Any

from app import queries_spaces
from app.queries_spaces import ARCHIVE_PARENTS, SUBPATH_ROOT, SpaceError, validate_target_name
from app.schema import COPY_MIN_SIZE, MEMBER_ROLES

SEP = "\\"
ROLE_NONE = "sense-paper"
UNIQUE_ROLES = ("canonica", "part")
ROLE_ORDER = (*MEMBER_ROLES, ROLE_NONE)
FOLDER_INDEX = "idx_folder_stats_drive_path"
PATH_INDEX = "ux_inventory_drive_path"
KEY_INDEX = "idx_inventory_name_key"


def join_virtual(*parts: str | None) -> str:
    return SEP.join(p for p in parts if p)


def member_virtual_path(space_path: str, member: dict[str, Any]) -> str:
    """Ruta de destí d'un membre: espai + target_subpath + nom d'origen («.» = sense el nom)."""
    sub = member.get("target_subpath")
    if sub == SUBPATH_ROOT:
        return space_path
    return join_virtual(space_path, sub, member.get("name"))


def _role_key(role: str | None) -> str:
    return role or ROLE_NONE


def _empty_totals() -> dict[str, Any]:
    return {"files": 0, "bytes": 0, "members": 0,
            "by_role": {}, "unique_bytes": 0, "unique_big_bytes": 0, "unique_small_bytes": 0}


class ArxiuCalc:
    """Càlculs de l'arxiu virtual sobre una connexió (crea taules temporals; no escriu a la DB)."""

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn
        self.spaces: dict[int, dict[str, Any]] = {}   # espais d'arxiu, amb virtual_path i children
        self.all_spaces: dict[int, dict[str, Any]] = {}
        self.roots: list[int] = []
        self.members: list[dict[str, Any]] = []
        self.items: dict[int, dict[str, Any]] = {}
        self.item_members: dict[int, list[dict[str, Any]]] = defaultdict(list)
        self.item_parent: dict[int, int | None] = {}   # membre niat -> membre pare (més pròxim)
        self._segments = False
        self._totals: dict[int, dict[str, int]] | None = None
        self._mf = False
        self._keys = False
        self._load()

    # ------------------------------------------------------------------ càrrega
    def _load(self) -> None:
        rows = queries_spaces.list_spaces(self.conn)
        self.all_spaces = {r["space_id"]: r for r in rows}
        for r in rows:
            r["children"] = []
        for r in rows:
            p = self.all_spaces.get(r["parent_id"]) if r["parent_id"] is not None else None
            if p is not None:
                p["children"].append(r["space_id"])
        for r in rows:
            if r["archive_kind"] is not None:
                self.spaces[r["space_id"]] = r
        for sid, s in self.spaces.items():
            chain = []
            cur: dict[str, Any] | None = s
            while cur is not None and cur["archive_kind"] is not None:
                chain.append(cur["target_name"] or cur["name"])
                cur = self.all_spaces.get(cur["parent_id"]) if cur["parent_id"] is not None else None
            s["virtual_path"] = SEP.join(reversed(chain))
            s["depth"] = len(chain) - 1
            parent = self.all_spaces.get(s["parent_id"]) if s["parent_id"] is not None else None
            if parent is None or parent["archive_kind"] is None:
                self.roots.append(sid)
        self.roots.sort(key=lambda i: (self.spaces[i]["sort_order"], self.spaces[i]["name"].lower()))
        cur_rows = self.conn.execute(
            """
            SELECT m.space_id, m.item_id, m.role, m.target_subpath, m.note, m.added_utc,
                   t.kind, t.drive_id, t.relative_path, t.name, t.size_bytes, t.range_lo, t.range_hi
            FROM space_members m
            JOIN items t ON t.item_id = m.item_id
            JOIN spaces s ON s.space_id = m.space_id
            WHERE s.archive_kind IS NOT NULL
            ORDER BY t.drive_id, t.relative_path, m.space_id
            """
        ).fetchall()
        for r in cur_rows:
            m = dict(r)
            self.members.append(m)
            self.item_members[m["item_id"]].append(m)
            if m["item_id"] not in self.items:
                self.items[m["item_id"]] = {k: m[k] for k in
                                            ("item_id", "kind", "drive_id", "relative_path", "name",
                                             "size_bytes", "range_lo", "range_hi")}

    def subtree(self, space_id: int) -> list[int]:
        out = [space_id]
        i = 0
        while i < len(out):
            out.extend(self.all_spaces[out[i]]["children"]) if out[i] in self.all_spaces else None
            i += 1
        return out

    # ------------------------------------------------------------------ segments
    @staticmethod
    def _bounds(item: dict[str, Any]) -> tuple[str, str]:
        if item["kind"] == "folder":
            return item["range_lo"], item["range_hi"]
        p = item["relative_path"]
        return p, p + "\0"

    def _build_segments(self) -> None:
        if self._segments:
            return
        c = self.conn
        c.execute("DROP TABLE IF EXISTS temp.arxiu_seg")
        c.execute("DROP TABLE IF EXISTS temp.arxiu_exact")
        c.execute("DROP TABLE IF EXISTS temp.arxiu_file")
        c.execute("CREATE TEMP TABLE arxiu_seg (item_id INTEGER, drive_id TEXT, lo TEXT, hi TEXT)")
        c.execute("CREATE TEMP TABLE arxiu_exact (item_id INTEGER, drive_id TEXT, folder_path TEXT, "
                  "PRIMARY KEY (drive_id, folder_path)) WITHOUT ROWID")
        c.execute("CREATE TEMP TABLE arxiu_file (item_id INTEGER PRIMARY KEY, drive_id TEXT, "
                  "relative_path TEXT, parent_item INTEGER)")
        by_drive: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for it in self.items.values():
            by_drive[it["drive_id"]].append(it)
        seg_rows: list[tuple] = []
        exact_rows: list[tuple] = []
        file_rows: list[tuple] = []
        for drive_id, items in by_drive.items():
            # pares abans que fills: per lo ascendent i, en empat, rang més llarg primer
            items.sort(key=lambda it: (self._bounds(it)[0], "" if it["kind"] == "folder" else "~",
                                       -len(self._bounds(it)[1])))
            stack: list[dict[str, Any]] = []
            children: dict[int, list[dict[str, Any]]] = defaultdict(list)
            for it in items:
                lo, hi = self._bounds(it)
                while stack:
                    plo, phi = self._bounds(stack[-1])
                    if plo <= lo and hi <= phi and stack[-1]["kind"] == "folder":
                        break
                    stack.pop()
                parent = stack[-1] if stack else None
                self.item_parent[it["item_id"]] = parent["item_id"] if parent else None
                if parent is not None:
                    children[parent["item_id"]].append(it)
                stack.append(it)
            for it in items:
                lo, hi = self._bounds(it)
                if it["kind"] == "file":
                    seg_rows.append((it["item_id"], drive_id, lo, hi))
                    file_rows.append((it["item_id"], drive_id, it["relative_path"],
                                      self.item_parent.get(it["item_id"])))
                    continue
                exact_rows.append((it["item_id"], drive_id, it["relative_path"]))
                cur = lo
                for ch in sorted(children[it["item_id"]], key=lambda x: self._bounds(x)[0]):
                    clo, chi = self._bounds(ch)
                    if clo > cur:
                        seg_rows.append((it["item_id"], drive_id, cur, clo))
                    cur = max(cur, chi)
                if cur < hi:
                    seg_rows.append((it["item_id"], drive_id, cur, hi))
        c.executemany("INSERT INTO temp.arxiu_seg VALUES (?, ?, ?, ?)", seg_rows)
        c.executemany("INSERT OR IGNORE INTO temp.arxiu_exact VALUES (?, ?, ?)", exact_rows)
        c.executemany("INSERT INTO temp.arxiu_file VALUES (?, ?, ?, ?)", file_rows)
        self._segments = True

    # ------------------------------------------------------------------ totals per membre
    def item_totals(self) -> dict[int, dict[str, int]]:
        """{item_id: {files, bytes}} dels fitxers vius que pertanyen a cada membre (el més profund)."""
        if self._totals is not None:
            return self._totals
        self._build_segments()
        c = self.conn
        totals: dict[int, dict[str, int]] = {i: {"files": 0, "bytes": 0} for i in self.items}
        rows = c.execute(
            f"""
            SELECT s.item_id, SUM(f.files_count), SUM(f.total_bytes)
            FROM temp.arxiu_seg s
            CROSS JOIN folder_stats f INDEXED BY {FOLDER_INDEX}
                 ON f.drive_id = s.drive_id AND f.folder_path >= s.lo AND f.folder_path < s.hi
            WHERE NOT EXISTS (SELECT 1 FROM temp.arxiu_exact e
                              WHERE e.drive_id = f.drive_id AND e.folder_path = f.folder_path)
            GROUP BY s.item_id
            """
        ).fetchall()
        for item_id, files, nbytes in rows:
            totals[item_id]["files"] += int(files or 0)
            totals[item_id]["bytes"] += int(nbytes or 0)
        rows = c.execute(
            """
            SELECT e.item_id, f.files_count, f.total_bytes
            FROM temp.arxiu_exact e JOIN folder_stats f
                 ON f.drive_id = e.drive_id AND f.folder_path = e.folder_path
            """
        ).fetchall()
        for item_id, files, nbytes in rows:
            totals[item_id]["files"] += int(files or 0)
            totals[item_id]["bytes"] += int(nbytes or 0)
        rows = c.execute(
            """
            SELECT a.item_id, a.parent_item, i.size_bytes
            FROM temp.arxiu_file a JOIN inventory i
                 ON i.drive_id = a.drive_id AND i.relative_path = a.relative_path
            WHERE i.missing_since_utc IS NULL
            """
        ).fetchall()
        for item_id, parent, size in rows:
            totals[item_id]["files"] += 1
            totals[item_id]["bytes"] += int(size or 0)
            if parent is not None and parent in totals:
                totals[parent]["files"] -= 1
                totals[parent]["bytes"] -= int(size or 0)
        self._totals = totals
        return totals

    # ------------------------------------------------------------------ fitxers ≥ 1 MiB i claus
    def _build_member_files(self) -> None:
        if self._mf:
            return
        self._build_segments()
        c = self.conn
        c.execute("DROP TABLE IF EXISTS temp.arxiu_mf")
        c.execute("CREATE TEMP TABLE arxiu_mf (item_id INTEGER, id INTEGER, name_key TEXT, "
                  "size_bytes INTEGER, drive_id TEXT)")
        c.execute(
            f"""
            INSERT INTO temp.arxiu_mf
            SELECT s.item_id, i.id, i.name_key, i.size_bytes, i.drive_id
            FROM temp.arxiu_seg s
            CROSS JOIN inventory i INDEXED BY {PATH_INDEX}
                 ON i.drive_id = s.drive_id AND i.relative_path >= s.lo AND i.relative_path < s.hi
            WHERE i.missing_since_utc IS NULL AND i.size_bytes >= ?
            """,
            (COPY_MIN_SIZE,),
        )
        c.execute("CREATE INDEX temp.ix_arxiu_mf_key ON arxiu_mf(name_key, size_bytes)")
        c.execute("CREATE INDEX temp.ix_arxiu_mf_item ON arxiu_mf(item_id)")
        self._mf = True

    def _build_keys(self) -> None:
        if self._keys:
            return
        c = self.conn
        c.execute("DROP TABLE IF EXISTS temp.arxiu_keys")
        c.execute("CREATE TEMP TABLE arxiu_keys (name_key TEXT, size_bytes INTEGER, n INTEGER, "
                  "d1 TEXT, d2 TEXT, PRIMARY KEY (name_key, size_bytes)) WITHOUT ROWID")
        c.execute(
            f"""
            INSERT INTO temp.arxiu_keys
            SELECT name_key, size_bytes, COUNT(*), MIN(drive_id), MAX(drive_id)
            FROM inventory INDEXED BY {KEY_INDEX}
            WHERE missing_since_utc IS NULL AND size_bytes >= ? AND name_key IS NOT NULL
            GROUP BY name_key, size_bytes
            """,
            (COPY_MIN_SIZE,),
        )
        self._keys = True

    def item_unique(self) -> dict[int, dict[str, int]]:
        """Per membre: fitxers i bytes ≥ 1 MiB, i bytes ≥ 1 MiB sense còpia en cap altre disc
        (criteri del catàleg, per disc). Es desa a la instància."""
        if getattr(self, "_item_unique", None) is not None:
            return self._item_unique  # type: ignore[return-value]
        self._build_member_files()
        self._build_keys()
        out: dict[int, dict[str, int]] = {i: {"big_files": 0, "big_bytes": 0, "drive_unique_bytes": 0}
                                          for i in self.items}
        for item_id, n, b in self.conn.execute(
                "SELECT item_id, COUNT(*), SUM(size_bytes) FROM temp.arxiu_mf GROUP BY item_id"):
            out[item_id]["big_files"] = int(n)
            out[item_id]["big_bytes"] = int(b)
        for item_id, b in self.conn.execute(
                """
                SELECT f.item_id, SUM(f.size_bytes)
                FROM temp.arxiu_mf f JOIN temp.arxiu_keys k
                     ON k.name_key = f.name_key AND k.size_bytes = f.size_bytes
                WHERE k.d1 = k.d2
                GROUP BY f.item_id
                """):
            out[item_id]["drive_unique_bytes"] = int(b or 0)
        self._item_unique = out
        return out

    # ------------------------------------------------------------------ totals d'un espai
    def space_totals(self, space_id: int, recursive: bool = True) -> dict[str, Any]:
        ids = self.subtree(space_id) if recursive else [space_id]
        id_set = set(ids)
        totals = self.item_totals()
        uniq = self.item_unique()
        out = _empty_totals()
        out["space_id"] = space_id
        out["recursive"] = recursive
        by_role: dict[str, dict[str, int]] = {}
        small_unique = 0
        for m in self.members:
            if m["space_id"] not in id_set:
                continue
            t = totals.get(m["item_id"], {"files": 0, "bytes": 0})
            r = by_role.setdefault(_role_key(m["role"]), {"members": 0, "files": 0, "bytes": 0})
            r["members"] += 1
            r["files"] += t["files"]
            r["bytes"] += t["bytes"]
            out["members"] += 1
            out["files"] += t["files"]
            out["bytes"] += t["bytes"]
            if m["role"] in UNIQUE_ROLES:
                u = uniq.get(m["item_id"], {})
                small_unique += t["bytes"] - u.get("big_bytes", 0)
        out["by_role"] = {k: by_role[k] for k in ROLE_ORDER if k in by_role}
        self._build_member_files()
        self._build_keys()
        ph = ",".join("?" * len(ids))
        big = self.conn.execute(
            f"""
            WITH mf AS (
                SELECT f.id, f.name_key, f.size_bytes, m.role
                FROM temp.arxiu_mf f JOIN space_members m ON m.item_id = f.item_id
                WHERE m.space_id IN ({ph}))
            SELECT COALESCE(SUM(x.b), 0) FROM (
                SELECT name_key, size_bytes, COUNT(DISTINCT id) AS c,
                       SUM(CASE WHEN role IN ('canonica', 'part') THEN size_bytes ELSE 0 END) AS b
                FROM mf GROUP BY name_key, size_bytes) x
            JOIN temp.arxiu_keys k ON k.name_key = x.name_key AND k.size_bytes = x.size_bytes
            WHERE k.n = x.c AND x.b > 0
            """,
            ids,
        ).fetchone()[0]
        out["unique_big_bytes"] = int(big or 0)
        out["unique_small_bytes"] = max(0, small_unique)
        out["unique_bytes"] = out["unique_big_bytes"] + out["unique_small_bytes"]
        return out

    # ------------------------------------------------------------------ arbre de destí
    def member_rows(self, space_id: int) -> list[dict[str, Any]]:
        """Membres directes d'un espai amb totals, únics per disc i ruta virtual."""
        space = self.spaces.get(space_id) or self.all_spaces.get(space_id)
        if space is None:
            raise SpaceError("L'espai no existeix.")
        totals = self.item_totals()
        uniq = self.item_unique()
        out = []
        for m in self.members:
            if m["space_id"] != space_id:
                continue
            t = totals.get(m["item_id"], {"files": 0, "bytes": 0})
            u = uniq.get(m["item_id"], {"big_bytes": 0, "drive_unique_bytes": 0})
            unique_bytes = u["drive_unique_bytes"] + max(0, t["bytes"] - u["big_bytes"])
            row = dict(m)
            row["files"] = t["files"]
            row["bytes"] = t["bytes"]
            row["unique_bytes"] = unique_bytes
            row["unique_pct"] = round(100.0 * unique_bytes / t["bytes"], 1) if t["bytes"] else 0.0
            row["virtual_path"] = member_virtual_path(space.get("virtual_path", space["name"]), m)
            row["nested_in"] = self.item_parent.get(m["item_id"])
            out.append(row)
        out.sort(key=lambda r: (-r["bytes"], r["drive_id"], r["relative_path"]))
        return out

    def virtual_tree(self, root_space_id: int | None = None) -> list[dict[str, Any]]:
        """Arbre de destí: per espai, ruta virtual, totals recursius i membres amb ruta final."""
        self.item_totals()
        self.item_unique()
        roots = [root_space_id] if root_space_id is not None else self.roots
        for r in roots:
            if r not in self.spaces:
                raise SpaceError(f"L'espai {r} no és un espai d'arxiu.")

        def node(sid: int) -> dict[str, Any]:
            s = self.spaces[sid]
            n = {k: s[k] for k in ("space_id", "parent_id", "name", "archive_kind", "target_name",
                                   "state", "canonical_drive_id", "description", "virtual_path", "depth")}
            n["totals"] = self.space_totals(sid, recursive=True)
            n["members"] = self.member_rows(sid)
            kids = [c for c in s["children"] if c in self.spaces]
            kids.sort(key=lambda c: (self.spaces[c]["sort_order"], self.spaces[c]["target_name"] or ""))
            n["children"] = [node(c) for c in kids]
            return n

        return [node(r) for r in roots]

    # ------------------------------------------------------------------ cobertura
    def coverage(self, drive_id: str) -> dict[str, Any]:
        """Què queda del disc fora de tot espai d'arxiu, per carpeta de nivell 1 i 2, i el
        total cobert per paper."""
        self._build_segments()
        totals = self.item_totals()
        c = self.conn
        drive = c.execute(
            "SELECT files_count, total_bytes FROM drives WHERE drive_id = ?", (drive_id,)
        ).fetchone()
        if drive is None:
            raise SpaceError(f"El disc «{drive_id}» no és a la DB.")
        # rangs coberts (unió dels rangs de les carpetes membres) i fitxers membres solts
        ranges: list[tuple[str, str]] = []
        loose_files: list[dict[str, Any]] = []
        covered: dict[str, dict[str, int]] = {}
        seen: set[int] = set()
        for m in self.members:
            it = self.items[m["item_id"]]
            if it["drive_id"] != drive_id:
                continue
            if m["item_id"] not in seen:
                seen.add(m["item_id"])
                t = totals[m["item_id"]]
                r = covered.setdefault(_role_key(m["role"]), {"members": 0, "files": 0, "bytes": 0})
                r["members"] += 1
                r["files"] += t["files"]
                r["bytes"] += t["bytes"]
                if it["kind"] == "folder":
                    ranges.append((it["range_lo"], it["range_hi"]))
                elif self.item_parent.get(m["item_id"]) is None:
                    loose_files.append(it)
        ranges.sort()
        merged: list[tuple[str, str]] = []
        for lo, hi in ranges:
            if merged and lo <= merged[-1][1]:
                if hi > merged[-1][1]:
                    merged[-1] = (merged[-1][0], hi)
            else:
                merged.append((lo, hi))
        gaps: list[tuple[str, str | None]] = []
        prev = ""
        for lo, hi in merged:
            gaps.append((prev, lo))
            prev = hi
        gaps.append((prev, None))
        groups: dict[tuple[str, str], dict[str, int]] = defaultdict(lambda: {"files": 0, "bytes": 0})
        level_sql = r"""
            SELECT CASE WHEN instr(folder_path, '\') = 0 THEN folder_path
                        ELSE substr(folder_path, 1, instr(folder_path, '\') - 1) END AS l1,
                   CASE WHEN instr(folder_path, '\') = 0 THEN ''
                        WHEN instr(substr(folder_path, instr(folder_path, '\') + 1), '\') = 0
                             THEN substr(folder_path, instr(folder_path, '\') + 1)
                        ELSE substr(substr(folder_path, instr(folder_path, '\') + 1), 1,
                                    instr(substr(folder_path, instr(folder_path, '\') + 1), '\') - 1)
                   END AS l2,
                   SUM(files_count), SUM(total_bytes)
            FROM folder_stats INDEXED BY {idx}
            WHERE drive_id = ? AND folder_path >= ? {upper}
              AND NOT EXISTS (SELECT 1 FROM temp.arxiu_exact e
                              WHERE e.drive_id = folder_stats.drive_id AND e.folder_path = folder_stats.folder_path)
            GROUP BY l1, l2
        """
        for lo, hi in gaps:
            if hi is not None and lo >= hi:
                continue
            sql = level_sql.format(idx=FOLDER_INDEX, upper="AND folder_path < ?" if hi is not None else "")
            params: list[Any] = [drive_id, lo] + ([hi] if hi is not None else [])
            for l1, l2, files, nbytes in c.execute(sql, params):
                g = groups[(l1, l2)]
                g["files"] += int(files or 0)
                g["bytes"] += int(nbytes or 0)
        for it in loose_files:
            t = totals[it["item_id"]]
            rp = it["relative_path"]
            parts = rp.split(SEP)
            l1 = parts[0] if len(parts) > 1 else ""
            l2 = parts[1] if len(parts) > 2 else ""
            g = groups[(l1, l2)]
            g["files"] -= t["files"]
            g["bytes"] -= t["bytes"]
        rows = [{"level1": l1, "level2": l2, "files": g["files"], "bytes": g["bytes"]}
                for (l1, l2), g in groups.items() if g["files"] > 0 or g["bytes"] > 0]
        rows.sort(key=lambda r: (-r["bytes"], r["level1"], r["level2"]))
        level1: dict[str, dict[str, Any]] = {}
        for r in rows:
            g = level1.setdefault(r["level1"], {"level1": r["level1"], "files": 0, "bytes": 0, "children": []})
            g["files"] += r["files"]
            g["bytes"] += r["bytes"]
            g["children"].append(r)
        groups_out = sorted(level1.values(), key=lambda g: (-g["bytes"], g["level1"]))
        unc_files = sum(r["files"] for r in rows)
        unc_bytes = sum(r["bytes"] for r in rows)
        cov_files = sum(v["files"] for v in covered.values())
        cov_bytes = sum(v["bytes"] for v in covered.values())
        return {
            "drive_id": drive_id,
            "drive_files": int(drive[0] or 0), "drive_bytes": int(drive[1] or 0),
            "covered": {k: covered[k] for k in ROLE_ORDER if k in covered},
            "covered_files": cov_files, "covered_bytes": cov_bytes,
            "uncovered_files": unc_files, "uncovered_bytes": unc_bytes,
            "rows": rows, "groups": groups_out,
        }

    # ------------------------------------------------------------------ comprovacions
    def check(self) -> list[dict[str, Any]]:
        """Conflictes de l'arxiu virtual: llista de {severity: error|avis, kind, message, …}."""
        out: list[dict[str, Any]] = []

        def add(severity: str, kind: str, message: str, **extra: Any) -> None:
            d = {"severity": severity, "kind": kind, "message": message}
            d.update(extra)
            out.append(d)

        # espais: nom, jerarquia, duplicats, projecte sense canònica
        for sid, s in self.spaces.items():
            path = s["virtual_path"]
            try:
                validate_target_name(s["archive_kind"], s["target_name"])
            except SpaceError as e:
                add("error", "nom-invalid", str(e), space_id=sid, space_path=path)
            parent = self.all_spaces.get(s["parent_id"]) if s["parent_id"] is not None else None
            pk = parent["archive_kind"] if parent is not None else None
            if parent is not None and pk is None:
                add("error", "jerarquia", f"L'espai «{path}» ({s['archive_kind']}) penja d'un espai normal.",
                    space_id=sid, space_path=path)
            elif pk not in ARCHIVE_PARENTS[s["archive_kind"]]:
                add("error", "jerarquia",
                    f"L'espai «{path}» ({s['archive_kind']}) no pot penjar d'un espai «{pk or 'arrel'}».",
                    space_id=sid, space_path=path)
            if s["archive_kind"] == "projecte":
                ids = set(self.subtree(sid))
                if not any(m["space_id"] in ids and m["role"] == "canonica" for m in self.members):
                    add("avis", "sense-canonica", f"El projecte «{path}» no té cap membre canònica.",
                        space_id=sid, space_path=path)
        by_parent: dict[tuple[int | None, str], list[int]] = defaultdict(list)
        for sid, s in self.spaces.items():
            by_parent[(s["parent_id"], (s["target_name"] or "").lower())].append(sid)
        for (pid, name), sids in by_parent.items():
            if len(sids) > 1:
                add("error", "nom-duplicat",
                    f"Nom de destí «{name}» repetit entre germans: espais {', '.join(map(str, sids))}.",
                    space_id=sids[0], space_path=self.spaces[sids[0]]["virtual_path"])
        # membres: la mateixa carpeta en dos espais, papers contradictoris, sense paper, origen absent
        for item_id, ms in self.item_members.items():
            it = self.items[item_id]
            if len(ms) > 1:
                roles = {m["role"] for m in ms}
                paths = ", ".join(self.spaces[m["space_id"]]["virtual_path"] for m in ms)
                if "canonica" in roles and "descartar" in roles:
                    add("error", "paper-contradictori",
                        f"{it['drive_id']}\\{it['relative_path']} és canònica en un espai i descartar en un altre ({paths}).",
                        item_id=item_id, drive_id=it["drive_id"], path=it["relative_path"],
                        space_id=ms[0]["space_id"], other_space_id=ms[1]["space_id"])
                else:
                    add("avis", "origen-repetit",
                        f"{it['drive_id']}\\{it['relative_path']} és membre de {len(ms)} espais ({paths}).",
                        item_id=item_id, drive_id=it["drive_id"], path=it["relative_path"],
                        space_id=ms[0]["space_id"], other_space_id=ms[1]["space_id"])
            for m in ms:
                if m["role"] is None:
                    add("avis", "sense-paper",
                        f"{it['drive_id']}\\{it['relative_path']} no té paper a «{self.spaces[m['space_id']]['virtual_path']}».",
                        item_id=item_id, drive_id=it["drive_id"], path=it["relative_path"], space_id=m["space_id"])
        # niament: cada membre niat contra el membre pare més pròxim
        self._build_segments()
        for item_id, parent_id in self.item_parent.items():
            if parent_id is None:
                continue
            it, pit = self.items[item_id], self.items[parent_id]
            for m in self.item_members[item_id]:
                for pm in self.item_members[parent_id]:
                    if m["space_id"] != pm["space_id"]:
                        add("avis", "niat-entre-espais",
                            f"{it['drive_id']}\\{it['relative_path']} («{self.spaces[m['space_id']]['virtual_path']}», "
                            f"{m['role'] or 'sense paper'}) és dins de {pit['relative_path']} "
                            f"(«{self.spaces[pm['space_id']]['virtual_path']}», {pm['role'] or 'sense paper'}).",
                            item_id=item_id, drive_id=it["drive_id"], path=it["relative_path"],
                            space_id=m["space_id"], other_space_id=pm["space_id"], parent_item_id=parent_id)
                    elif {m["role"], pm["role"]} == {"canonica", "descartar"}:
                        add("error", "niat-paper-incompatible",
                            f"{it['drive_id']}\\{it['relative_path']} ({m['role']}) és dins de "
                            f"{pit['relative_path']} ({pm['role']}) al mateix espai "
                            f"«{self.spaces[m['space_id']]['virtual_path']}».",
                            item_id=item_id, drive_id=it["drive_id"], path=it["relative_path"],
                            space_id=m["space_id"], parent_item_id=parent_id)
        # origen absent de l'inventari
        for item_id, it in self.items.items():
            if it["kind"] == "folder":
                ok = self.conn.execute(
                    "SELECT 1 FROM folder_stats WHERE drive_id = ? AND folder_path = ? LIMIT 1",
                    (it["drive_id"], it["relative_path"])).fetchone()
            else:
                ok = self.conn.execute(
                    "SELECT 1 FROM inventory WHERE drive_id = ? AND relative_path = ? AND missing_since_utc IS NULL",
                    (it["drive_id"], it["relative_path"])).fetchone()
            if ok is None:
                m = self.item_members[item_id][0]
                add("avis", "origen-absent",
                    f"{it['drive_id']}\\{it['relative_path']} ja no és a l'inventari.",
                    item_id=item_id, drive_id=it["drive_id"], path=it["relative_path"], space_id=m["space_id"])
        order = {"error": 0, "avis": 1}
        out.sort(key=lambda d: (order[d["severity"]], d["kind"], d.get("space_path", ""), d.get("path", "")))
        return out


# ---------------------------------------------------------------------------
# Funcions de mòdul (una ArxiuCalc per crida)
# ---------------------------------------------------------------------------

def space_totals(conn: sqlite3.Connection, space_id: int, recursive: bool = True) -> dict[str, Any]:
    return ArxiuCalc(conn).space_totals(space_id, recursive)


def virtual_tree(conn: sqlite3.Connection, root_space_id: int | None = None) -> list[dict[str, Any]]:
    return ArxiuCalc(conn).virtual_tree(root_space_id)


def coverage(conn: sqlite3.Connection, drive_id: str) -> dict[str, Any]:
    return ArxiuCalc(conn).coverage(drive_id)


def check(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    return ArxiuCalc(conn).check()
