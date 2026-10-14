"""Espais virtuals: arbre jeràrquic, CRUD i contingut.

Un espai és `smart` (una cerca desada: `query_json` amb el diccionari de
app.search_params.parse_search_params, executada a cada obertura) o `manual` (una
col·lecció d'elements, taula `items` + `space_members`). Els espais pengen d'un pare
(`parent_id`), així que l'arbre «Espais» de l'explorador pot tenir carpetes d'espais.

Les claus foranes de SQLite no s'activen per connexió, de manera que l'esborrat en cascada
es fa explícitament amb un CTE recursiu (descendents) en lloc de confiar en ON DELETE.

Arxiu virtual (v12): un espai pot ser a més un node de l'arxiu de destí (`archive_kind`
fons › serie › projecte › capa › carpeta, amb `target_name` = nom de la carpeta de destí),
i cada membre d'un espai d'arxiu té un paper (`role`) i un destí (`target_subpath`). Les
regles de jerarquia i de nomenclatura (model d'arxiu §8) es validen ací, en escriure; els
càlculs (totals, arbre de destí, cobertura, comprovacions) són a app/queries_arxiu.py.
"""
from __future__ import annotations

import json
import re
import sqlite3
from typing import Any

from app.queries import SPACE_ITEMS_SQL, TAG_ITEMS_SQL, folder_range, items_resolution_sql
from app.schema import ARCHIVE_KINDS, MEMBER_ROLES, PROJECT_LAYERS, SPACE_STATES, utc_now_iso
from app.search_params import parse_search_params, run_file_search

SPACE_KINDS = ("smart", "manual")
NAME_MAX = 120
DESCRIPTION_MAX = 2000

# Nomenclatura del model d'arxiu (§8) per a target_name segons archive_kind.
RE_SERIE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
RE_PROJECT = re.compile(r"^\d{4}_[a-z0-9]+(-[a-z0-9]+)*$")
RE_FONS = re.compile(r"^_?[A-Za-z0-9][A-Za-z0-9_-]*$")
RE_CARPETA = re.compile(r'^[^\\/:*?"<>|]+$')
# Tipus de pare admesos per a cada tipus d'espai d'arxiu (None = arrel).
ARCHIVE_PARENTS: dict[str, tuple[str | None, ...]] = {
    "fons": (None,),
    "serie": ("fons",),
    "projecte": ("serie",),
    "capa": ("projecte",),
    "carpeta": ("capa", "carpeta", "fons"),
}
# target_subpath que vol dir «el contingut de la carpeta d'origen va a l'arrel de l'espai,
# sense la carpeta d'origen pel mig».
SUBPATH_ROOT = "."
SPACE_COLUMNS = ("space_id, parent_id, name, kind, query_json, description, sort_order, "
                 "created_utc, updated_utc, archive_kind, target_name, state, canonical_drive_id")

# Ordenació del contingut d'un espai (clau de la interfície -> columna de search_inventory)
CONTENT_SORT_KEYS = {
    "name": "name",
    "type": "extension",
    "size": "size_bytes",
    "date": "modified_utc",
    "drive": "drive_id",
    "folder": "relative_path",
}


class SpaceError(ValueError):
    """Error de validació (nom buit, pare inexistent, cicle…), amb missatge per a l'usuari."""


def _row(r: sqlite3.Row | tuple) -> dict[str, Any]:
    d = dict(r)
    raw = d.pop("query_json", None)
    query = None
    if raw:
        try:
            query = json.loads(raw)
        except ValueError:
            query = None
    d["query"] = parse_search_params(query) if isinstance(query, dict) else None
    return d


def list_spaces(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Tots els espais (són pocs), ordenats per pare, ordre manual i nom."""
    cur = conn.execute(
        f"""
        SELECT {SPACE_COLUMNS}
        FROM spaces ORDER BY parent_id IS NOT NULL, parent_id, sort_order, name COLLATE NOCASE
        """
    )
    return [_row(r) for r in cur.fetchall()]


def build_tree(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Converteix la llista plana en arbre ({…espai, children: […]}); els orfes van a l'arrel."""
    by_id = {r["space_id"]: {**r, "children": []} for r in rows}
    roots: list[dict[str, Any]] = []
    for node in by_id.values():
        parent = by_id.get(node["parent_id"]) if node["parent_id"] is not None else None
        (parent["children"] if parent is not None else roots).append(node)
    return roots


def flatten_tree(tree: list[dict[str, Any]], depth: int = 0) -> list[dict[str, Any]]:
    """Recorregut en preordre amb `depth` (per a desplegables indentats i rutes)."""
    out: list[dict[str, Any]] = []
    for node in tree:
        out.append({k: v for k, v in node.items() if k != "children"} | {"depth": depth})
        out.extend(flatten_tree(node["children"], depth + 1))
    return out


def get_space(conn: sqlite3.Connection, space_id: int) -> dict[str, Any] | None:
    r = conn.execute(
        f"SELECT {SPACE_COLUMNS} FROM spaces WHERE space_id = ?", (space_id,)
    ).fetchone()
    return _row(r) if r else None


def find_archive_child(conn: sqlite3.Connection, parent_id: int | None, target_name: str) -> dict[str, Any] | None:
    """L'espai d'arxiu fill de `parent_id` (None = arrel) amb aquest target_name, si existeix."""
    r = conn.execute(
        f"""
        SELECT {SPACE_COLUMNS} FROM spaces
        WHERE parent_id IS ? AND archive_kind IS NOT NULL AND target_name = ?
        """,
        (parent_id, target_name),
    ).fetchone()
    return _row(r) if r else None


def get_ancestors(conn: sqlite3.Connection, space_id: int) -> list[dict[str, Any]]:
    """Avantpassats de l'arrel cap avall (sense l'espai mateix)."""
    rows = conn.execute(
        """
        WITH RECURSIVE up(space_id, parent_id, name, kind, lvl) AS (
            SELECT s.parent_id, p.parent_id, p.name, p.kind, 1
            FROM spaces s JOIN spaces p ON p.space_id = s.parent_id WHERE s.space_id = ?
            UNION ALL
            SELECT p.space_id, p.parent_id, p.name, p.kind, up.lvl + 1
            FROM up JOIN spaces p ON p.space_id = up.parent_id
        )
        SELECT space_id, name, kind FROM up ORDER BY lvl DESC
        """,
        (space_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def descendant_ids(conn: sqlite3.Connection, space_id: int) -> list[int]:
    """L'espai i tots els seus descendents."""
    rows = conn.execute(
        """
        WITH RECURSIVE sub(id) AS (
            SELECT ? UNION ALL SELECT s.space_id FROM spaces s JOIN sub ON s.parent_id = sub.id)
        SELECT id FROM sub
        """,
        (space_id,),
    ).fetchall()
    return [int(r[0]) for r in rows]


def _clean_name(name: str | None) -> str:
    n = " ".join((name or "").split())
    if not n:
        raise SpaceError("Cal un nom per a l'espai.")
    return n[:NAME_MAX]


def _check_parent(conn: sqlite3.Connection, parent_id: int | None, space_id: int | None) -> None:
    if parent_id is None:
        return
    if get_space(conn, parent_id) is None:
        raise SpaceError("L'espai pare no existeix.")
    if space_id is not None and parent_id in descendant_ids(conn, space_id):
        raise SpaceError("Un espai no pot penjar de si mateix ni d'un dels seus descendents.")


def validate_target_name(archive_kind: str, target_name: str | None) -> str:
    """Normalitza i valida el nom de destí segons el tipus (model §8). Llança SpaceError."""
    n = (target_name or "").strip()
    if not n:
        raise SpaceError("Un espai d'arxiu necessita el nom de la carpeta de destí (target_name).")
    if archive_kind == "capa":
        n = n.upper()
        if n not in PROJECT_LAYERS:
            raise SpaceError(f"Una capa ha de ser una de {', '.join(PROJECT_LAYERS)}: «{n}».")
    elif archive_kind == "projecte":
        if not RE_PROJECT.match(n) or not 2 <= len(n) - 5 <= 30:
            raise SpaceError(f"Nom de projecte no vàlid (AAAA_descriptor, minúscules, guions): «{n}».")
    elif archive_kind == "serie":
        if not RE_SERIE.match(n):
            raise SpaceError(f"Nom de sèrie no vàlid (minúscules, dígits i guions): «{n}».")
    elif archive_kind == "fons":
        if not RE_FONS.match(n):
            raise SpaceError(f"Nom de fons no vàlid: «{n}».")
    else:
        if not RE_CARPETA.match(n) or n in (".", ".."):
            raise SpaceError(f"Nom de carpeta no vàlid: «{n}».")
    return n[:NAME_MAX]


def _parent_kind(conn: sqlite3.Connection, parent_id: int | None) -> str | None:
    if parent_id is None:
        return None
    r = conn.execute("SELECT archive_kind FROM spaces WHERE space_id = ?", (parent_id,)).fetchone()
    if r is None:
        raise SpaceError("L'espai pare no existeix.")
    return r[0]


def _check_archive(
    conn: sqlite3.Connection, archive_kind: str | None, target_name: str | None,
    parent_id: int | None, space_id: int | None, validate_name: bool = True,
) -> str | None:
    """Jerarquia fons › serie › projecte › capa › carpeta i nom únic entre germans.

    Retorna el target_name normalitzat (None per a un espai normal). Amb `validate_name`
    fals s'accepta un nom que no segueix la nomenclatura (la importació d'un CSV amb noms
    provisionals: `--check` els senyala després).
    """
    if archive_kind is None:
        return None
    if archive_kind not in ARCHIVE_KINDS:
        raise SpaceError(f"Tipus d'espai d'arxiu desconegut: {archive_kind!r}")
    pk = _parent_kind(conn, parent_id)
    allowed = ARCHIVE_PARENTS[archive_kind]
    if pk not in allowed:
        if pk is None and parent_id is not None:
            raise SpaceError(f"Un espai «{archive_kind}» ha de penjar d'un espai d'arxiu, no d'un espai normal.")
        if pk is None:
            raise SpaceError(f"Només un fons pot ser a l'arrel; un espai «{archive_kind}» necessita un pare "
                             f"de tipus {', '.join(a for a in allowed if a)}.")
        raise SpaceError(f"Un espai «{archive_kind}» no pot penjar d'un espai «{pk}» "
                         f"(cal {', '.join(a for a in allowed if a)}).")
    if validate_name:
        name = validate_target_name(archive_kind, target_name)
    else:
        name = (target_name or "").strip()[:NAME_MAX]
        if not name or not RE_CARPETA.match(name):
            raise SpaceError(f"Nom de destí no vàlid: «{target_name}».")
    dup = conn.execute(
        """
        SELECT space_id FROM spaces
        WHERE parent_id IS ? AND archive_kind IS NOT NULL AND target_name = ? AND space_id IS NOT ?
        """,
        (parent_id, name, space_id),
    ).fetchone()
    if dup:
        raise SpaceError(f"Ja hi ha un espai d'arxiu «{name}» sota el mateix pare.")
    if space_id is not None:
        # els fills existents han de continuar sent compatibles amb el tipus nou
        bad = conn.execute(
            "SELECT archive_kind FROM spaces WHERE parent_id = ? AND archive_kind IS NOT NULL",
            (space_id,),
        ).fetchall()
        for (ck,) in bad:
            if archive_kind not in ARCHIVE_PARENTS[ck]:
                raise SpaceError(f"L'espai té fills «{ck}», que no poden penjar d'un espai «{archive_kind}».")
    return name


def _check_state(state: str | None) -> str | None:
    if state is None or state == "":
        return None
    if state not in SPACE_STATES:
        raise SpaceError(f"Estat desconegut: {state!r} (cal {', '.join(SPACE_STATES)}).")
    return state


def _query_json(query: dict[str, Any] | None, kind: str) -> str | None:
    if kind != "smart":
        return None
    if not isinstance(query, dict):
        raise SpaceError("Una cerca desada necessita els criteris de cerca.")
    return json.dumps(parse_search_params(query), ensure_ascii=False)


def create_space(
    conn: sqlite3.Connection,
    name: str,
    kind: str,
    parent_id: int | None = None,
    query: dict[str, Any] | None = None,
    description: str | None = None,
    archive_kind: str | None = None,
    target_name: str | None = None,
    state: str | None = None,
    canonical_drive_id: str | None = None,
    commit: bool = True,
    validate_name: bool = True,
) -> dict[str, Any]:
    if kind not in SPACE_KINDS:
        raise SpaceError(f"Tipus d'espai desconegut: {kind!r}")
    if archive_kind is not None and kind != "manual":
        raise SpaceError("Un espai d'arxiu és sempre una col·lecció (kind = manual).")
    name = _clean_name(name or target_name)
    _check_parent(conn, parent_id, None)
    target = _check_archive(conn, archive_kind, target_name, parent_id, None, validate_name)
    state = _check_state(state)
    now = utc_now_iso()
    cur = conn.execute(
        """
        INSERT INTO spaces (parent_id, name, kind, query_json, description, sort_order, created_utc,
                            archive_kind, target_name, state, canonical_drive_id)
        VALUES (?, ?, ?, ?, ?,
                COALESCE((SELECT MAX(sort_order) + 1 FROM spaces WHERE parent_id IS ?), 0), ?,
                ?, ?, ?, ?)
        """,
        (parent_id, name, kind, _query_json(query, kind),
         (description or "").strip()[:DESCRIPTION_MAX] or None, parent_id, now,
         archive_kind, target, state, (canonical_drive_id or "").strip() or None),
    )
    if commit:
        conn.commit()
    return get_space(conn, int(cur.lastrowid))  # type: ignore[return-value]


_UNSET = object()


def update_space(
    conn: sqlite3.Connection,
    space_id: int,
    name: Any = _UNSET,
    parent_id: Any = _UNSET,
    query: Any = _UNSET,
    description: Any = _UNSET,
    archive_kind: Any = _UNSET,
    target_name: Any = _UNSET,
    state: Any = _UNSET,
    canonical_drive_id: Any = _UNSET,
    sort_order: Any = _UNSET,
    commit: bool = True,
) -> dict[str, Any]:
    space = get_space(conn, space_id)
    if space is None:
        raise SpaceError("L'espai no existeix.")
    sets: list[str] = []
    params: list[Any] = []
    if name is not _UNSET:
        sets.append("name = ?")
        params.append(_clean_name(name))
    if parent_id is not _UNSET:
        _check_parent(conn, parent_id, space_id)
        sets.append("parent_id = ?")
        params.append(parent_id)
    if archive_kind is not _UNSET or target_name is not _UNSET or parent_id is not _UNSET:
        # qualsevol canvi de tipus, nom de destí o pare es valida contra la jerarquia sencera
        new_kind = space["archive_kind"] if archive_kind is _UNSET else archive_kind
        new_target = space["target_name"] if target_name is _UNSET else target_name
        new_parent = space["parent_id"] if parent_id is _UNSET else parent_id
        if new_kind is not None and space["kind"] != "manual":
            raise SpaceError("Un espai d'arxiu és sempre una col·lecció (kind = manual).")
        target = _check_archive(conn, new_kind, new_target, new_parent, space_id)
        sets += ["archive_kind = ?", "target_name = ?"]
        params += [new_kind, target]
        if new_kind is None and space["archive_kind"] is not None:
            kids = conn.execute(
                "SELECT 1 FROM spaces WHERE parent_id = ? AND archive_kind IS NOT NULL LIMIT 1", (space_id,)
            ).fetchone()
            if kids:
                raise SpaceError("No es pot convertir en espai normal: té espais d'arxiu a sota.")
    if state is not _UNSET:
        sets.append("state = ?")
        params.append(_check_state(state))
    if canonical_drive_id is not _UNSET:
        sets.append("canonical_drive_id = ?")
        params.append((canonical_drive_id or "").strip() or None)
    if sort_order is not _UNSET:
        sets.append("sort_order = ?")
        params.append(int(sort_order))
    if query is not _UNSET:
        if space["kind"] != "smart":
            raise SpaceError("Només una cerca desada té criteris.")
        sets.append("query_json = ?")
        params.append(_query_json(query, "smart"))
    if description is not _UNSET:
        sets.append("description = ?")
        params.append((description or "").strip()[:DESCRIPTION_MAX] or None)
    if not sets:
        return space
    sets.append("updated_utc = ?")
    params.append(utc_now_iso())
    params.append(space_id)
    conn.execute(f"UPDATE spaces SET {', '.join(sets)} WHERE space_id = ?", params)
    if commit:
        conn.commit()
    return get_space(conn, space_id)  # type: ignore[return-value]


def delete_space(conn: sqlite3.Connection, space_id: int) -> int:
    """Esborra l'espai i els seus descendents (i les pertinences). Retorna quants espais."""
    ids = descendant_ids(conn, space_id)
    if get_space(conn, space_id) is None:
        return 0
    ph = ",".join("?" * len(ids))
    conn.execute(f"DELETE FROM space_members WHERE space_id IN ({ph})", ids)
    conn.execute(f"DELETE FROM spaces WHERE space_id IN ({ph})", ids)
    conn.commit()
    return len(ids)


# ---------------------------------------------------------------------------
# Elements (identitat), pertinences i etiquetes
# ---------------------------------------------------------------------------

ITEM_KINDS = ("file", "folder")
TAG_COLORS = ("#e57373", "#ffb74d", "#fff176", "#81c784", "#4fc3f7", "#7986cb", "#ba68c8", "#a1887f")


def _to_db_path(path: str) -> str:
    return (path or "").replace("/", "\\").strip("\\")


def upsert_items(conn: sqlite3.Connection, specs: list[dict[str, Any]]) -> tuple[list[int], list[str]]:
    """Dona d'alta (o retroba) els elements descrits per [{kind, drive_id, path}] (ruta en format API).

    Fitxer: llegeix nom, mida i hash d'inventory (identitat per hash si en té). Carpeta: un sol
    element amb el rang de prefix (mai fitxer a fitxer). Retorna (item_ids, errors).
    """
    ids: list[int] = []
    errors: list[str] = []
    now = utc_now_iso()
    for spec in specs:
        kind = (spec.get("kind") or "").strip()
        drive_id = (spec.get("drive_id") or "").strip()
        path_db = _to_db_path(spec.get("path") or "")
        if kind not in ITEM_KINDS or not drive_id:
            errors.append(f"Element no vàlid: {spec!r}")
            continue
        if kind == "file":
            row = conn.execute(
                "SELECT name, size_bytes, hash_sha256 FROM inventory WHERE drive_id = ? AND relative_path = ?",
                (drive_id, path_db),
            ).fetchone()
            if row is None:
                errors.append(f"{drive_id}: el fitxer «{path_db}» no és a l'inventari.")
                continue
            name, size, h = row[0], row[1], row[2]
            lo = hi = None
        else:
            if not path_db:
                errors.append(f"{drive_id}: no es pot afegir l'arrel sencera d'un disc com a carpeta.")
                continue
            exists = conn.execute(
                "SELECT 1 FROM folder_stats WHERE drive_id = ? AND folder_path = ? LIMIT 1", (drive_id, path_db)
            ).fetchone()
            if exists is None:
                errors.append(f"{drive_id}: la carpeta «{path_db}» no és a l'inventari.")
                continue
            name = path_db.rsplit("\\", 1)[-1]
            size = None
            h = None
            lo, hi = folder_range(path_db)  # type: ignore[misc]
        cur = conn.execute(
            """
            INSERT INTO items (kind, drive_id, relative_path, hash_sha256, size_bytes, name,
                               range_lo, range_hi, created_utc)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(kind, drive_id, relative_path) DO UPDATE SET
                hash_sha256 = COALESCE(excluded.hash_sha256, items.hash_sha256),
                size_bytes = COALESCE(excluded.size_bytes, items.size_bytes),
                name = COALESCE(excluded.name, items.name)
            RETURNING item_id
            """,
            (kind, drive_id, path_db, h, size, name, lo, hi, now),
        )
        ids.append(int(cur.fetchone()[0]))
    return ids, errors


def refresh_item_hashes(conn: sqlite3.Connection) -> int:
    """Completa el hash dels elements fitxer que encara no en tenien quan el hash ja existeix.

    La taula items és petita i la cerca va per l'índex únic (drive_id, relative_path): ms.
    """
    cur = conn.execute(
        """
        UPDATE items SET hash_sha256 = (
            SELECT i.hash_sha256 FROM inventory i
            WHERE i.drive_id = items.drive_id AND i.relative_path = items.relative_path)
        WHERE kind = 'file' AND hash_sha256 IS NULL AND EXISTS (
            SELECT 1 FROM inventory i
            WHERE i.drive_id = items.drive_id AND i.relative_path = items.relative_path
              AND i.hash_sha256 IS NOT NULL)
        """
    )
    return cur.rowcount


def check_role(role: str | None) -> str | None:
    r = (role or "").strip() or None
    if r is not None and r not in MEMBER_ROLES:
        raise SpaceError(f"Paper desconegut: {role!r} (cal {', '.join(MEMBER_ROLES)}).")
    return r


def check_subpath(subpath: str | None) -> str | None:
    """Destí dins de l'espai: ruta relativa amb «\\», sense «..»; «.» = arrel sense carpeta d'origen."""
    s = (subpath or "").replace("/", "\\").strip().strip("\\")
    if not s:
        return None
    if s == SUBPATH_ROOT:
        return SUBPATH_ROOT
    parts = s.split("\\")
    for p in parts:
        if not p.strip() or p in (".", "..") or not RE_CARPETA.match(p):
            raise SpaceError(f"Destí no vàlid: «{subpath}».")
    return "\\".join(p.strip() for p in parts)[:400]


def add_members(
    conn: sqlite3.Connection,
    space_id: int,
    specs: list[dict[str, Any]],
    role: str | None = None,
    target_subpath: str | None = None,
    note: str | None = None,
    commit: bool = True,
) -> dict[str, Any]:
    """Afegeix elements a una col·lecció. `role`, `target_subpath` i `note` són els valors per
    defecte; cada spec pot portar els seus. Un element que ja hi era conserva paper, destí i
    nota llevat que la spec (o els valors per defecte) en done de nous."""
    space = get_space(conn, space_id)
    if space is None:
        raise SpaceError("L'espai no existeix.")
    if space["kind"] != "manual":
        raise SpaceError("Només es poden afegir elements a una col·lecció (no a una cerca desada).")
    ids, errors = upsert_items(conn, specs)
    ok_specs = [s for s in specs if (s.get("kind") or "") in ITEM_KINDS and s.get("drive_id")]
    now = utc_now_iso()
    added = 0
    for i, item_id in enumerate(ids):
        spec = ok_specs[i] if i < len(ok_specs) else {}
        r = check_role(spec.get("role", role))
        sp = check_subpath(spec.get("target_subpath", target_subpath))
        nt = (spec.get("note", note) or "").strip() or None
        cur = conn.execute(
            """
            INSERT INTO space_members (space_id, item_id, added_utc, note, role, target_subpath)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(space_id, item_id) DO UPDATE SET
                role = COALESCE(excluded.role, space_members.role),
                target_subpath = COALESCE(excluded.target_subpath, space_members.target_subpath),
                note = COALESCE(excluded.note, space_members.note)
            """,
            (space_id, item_id, now, nt, r, sp),
        )
        added += 1 if cur.rowcount and conn.execute(
            "SELECT added_utc = ? FROM space_members WHERE space_id = ? AND item_id = ?",
            (now, space_id, item_id)).fetchone()[0] else 0
    conn.execute("UPDATE spaces SET updated_utc = ? WHERE space_id = ?", (now, space_id))
    if commit:
        conn.commit()
    return {"added": added, "already": len(ids) - added, "errors": errors, "item_ids": ids}


def update_member(
    conn: sqlite3.Connection, space_id: int, item_id: int,
    role: Any = _UNSET, target_subpath: Any = _UNSET, note: Any = _UNSET, commit: bool = True,
) -> bool:
    """Canvia paper, destí o nota d'un membre (None esborra el valor)."""
    sets: list[str] = []
    params: list[Any] = []
    if role is not _UNSET:
        sets.append("role = ?")
        params.append(check_role(role))
    if target_subpath is not _UNSET:
        sets.append("target_subpath = ?")
        params.append(check_subpath(target_subpath))
    if note is not _UNSET:
        sets.append("note = ?")
        params.append((note or "").strip()[:DESCRIPTION_MAX] or None)
    if not sets:
        return False
    cur = conn.execute(
        f"UPDATE space_members SET {', '.join(sets)} WHERE space_id = ? AND item_id = ?",
        params + [space_id, item_id],
    )
    if commit:
        conn.commit()
    return cur.rowcount > 0


def move_member(conn: sqlite3.Connection, space_id: int, item_id: int, to_space_id: int) -> bool:
    """Trasllada un membre (amb paper, destí i nota) a una altra col·lecció."""
    target = get_space(conn, to_space_id)
    if target is None or target["kind"] != "manual":
        raise SpaceError("L'espai de destí no existeix o no és una col·lecció.")
    row = conn.execute(
        "SELECT added_utc, note, role, target_subpath FROM space_members WHERE space_id = ? AND item_id = ?",
        (space_id, item_id),
    ).fetchone()
    if row is None:
        return False
    conn.execute(
        """
        INSERT INTO space_members (space_id, item_id, added_utc, note, role, target_subpath)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(space_id, item_id) DO UPDATE SET
            note = excluded.note, role = excluded.role, target_subpath = excluded.target_subpath
        """,
        (to_space_id, item_id, row[0], row[1], row[2], row[3]),
    )
    conn.execute("DELETE FROM space_members WHERE space_id = ? AND item_id = ?", (space_id, item_id))
    now = utc_now_iso()
    conn.execute("UPDATE spaces SET updated_utc = ? WHERE space_id IN (?, ?)", (now, space_id, to_space_id))
    conn.commit()
    return True


def remove_member(conn: sqlite3.Connection, space_id: int, item_id: int) -> bool:
    cur = conn.execute(
        "DELETE FROM space_members WHERE space_id = ? AND item_id = ?", (space_id, item_id)
    )
    conn.commit()
    return cur.rowcount > 0


def count_members(conn: sqlite3.Connection, space_id: int) -> int:
    return int(conn.execute(
        "SELECT COUNT(*) FROM space_members WHERE space_id = ?", (space_id,)).fetchone()[0])


def list_tags(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    cur = conn.execute(
        """
        SELECT t.tag_id, t.name, t.color, t.created_utc,
               (SELECT COUNT(*) FROM item_tags it WHERE it.tag_id = t.tag_id) AS items_count
        FROM tags t ORDER BY t.name COLLATE NOCASE
        """
    )
    return [dict(r) for r in cur.fetchall()]


def get_tag(conn: sqlite3.Connection, tag_id: int) -> dict[str, Any] | None:
    r = conn.execute(
        "SELECT tag_id, name, color, created_utc FROM tags WHERE tag_id = ?", (tag_id,)
    ).fetchone()
    return dict(r) if r else None


def _clean_tag_name(name: str | None) -> str:
    n = " ".join((name or "").split())
    if not n:
        raise SpaceError("Cal un nom per a l'etiqueta.")
    return n[:60]


def create_tag(conn: sqlite3.Connection, name: str, color: str | None = None) -> dict[str, Any]:
    name = _clean_tag_name(name)
    existing = conn.execute("SELECT tag_id FROM tags WHERE name = ? COLLATE NOCASE", (name,)).fetchone()
    if existing:
        return get_tag(conn, int(existing[0]))  # type: ignore[return-value]
    if not color:
        n = int(conn.execute("SELECT COUNT(*) FROM tags").fetchone()[0])
        color = TAG_COLORS[n % len(TAG_COLORS)]
    cur = conn.execute(
        "INSERT INTO tags (name, color, created_utc) VALUES (?, ?, ?)", (name, color, utc_now_iso())
    )
    conn.commit()
    return get_tag(conn, int(cur.lastrowid))  # type: ignore[return-value]


def update_tag(conn: sqlite3.Connection, tag_id: int, name: Any = _UNSET, color: Any = _UNSET) -> dict[str, Any]:
    if get_tag(conn, tag_id) is None:
        raise SpaceError("L'etiqueta no existeix.")
    sets: list[str] = []
    params: list[Any] = []
    if name is not _UNSET:
        n = _clean_tag_name(name)
        dup = conn.execute(
            "SELECT tag_id FROM tags WHERE name = ? COLLATE NOCASE AND tag_id <> ?", (n, tag_id)
        ).fetchone()
        if dup:
            raise SpaceError(f"Ja hi ha una etiqueta anomenada «{n}».")
        sets.append("name = ?")
        params.append(n)
    if color is not _UNSET:
        sets.append("color = ?")
        params.append(color or None)
    if sets:
        conn.execute(f"UPDATE tags SET {', '.join(sets)} WHERE tag_id = ?", params + [tag_id])
        conn.commit()
    return get_tag(conn, tag_id)  # type: ignore[return-value]


def delete_tag(conn: sqlite3.Connection, tag_id: int) -> bool:
    conn.execute("DELETE FROM item_tags WHERE tag_id = ?", (tag_id,))
    cur = conn.execute("DELETE FROM tags WHERE tag_id = ?", (tag_id,))
    conn.commit()
    return cur.rowcount > 0


def tag_items(conn: sqlite3.Connection, tag_id: int, specs: list[dict[str, Any]]) -> dict[str, Any]:
    if get_tag(conn, tag_id) is None:
        raise SpaceError("L'etiqueta no existeix.")
    ids, errors = upsert_items(conn, specs)
    now = utc_now_iso()
    added = 0
    for item_id in ids:
        cur = conn.execute(
            "INSERT OR IGNORE INTO item_tags (item_id, tag_id, added_utc) VALUES (?, ?, ?)",
            (item_id, tag_id, now),
        )
        added += cur.rowcount
    conn.commit()
    return {"added": added, "already": len(ids) - added, "errors": errors, "item_ids": ids}


def untag_items(conn: sqlite3.Connection, tag_id: int, specs: list[dict[str, Any]] | None = None,
                item_ids: list[int] | None = None) -> int:
    ids = list(item_ids or [])
    if specs:
        for spec in specs:
            r = conn.execute(
                "SELECT item_id FROM items WHERE kind = ? AND drive_id = ? AND relative_path = ?",
                (spec.get("kind"), spec.get("drive_id"), _to_db_path(spec.get("path") or "")),
            ).fetchone()
            if r:
                ids.append(int(r[0]))
    n = 0
    for item_id in ids:
        n += conn.execute(
            "DELETE FROM item_tags WHERE tag_id = ? AND item_id = ?", (tag_id, item_id)
        ).rowcount
    conn.commit()
    return n


def tags_of(conn: sqlite3.Connection, kind: str, drive_id: str, path: str) -> list[int]:
    """Etiquetes (ids) d'un element concret, si existeix a `items`."""
    rows = conn.execute(
        """
        SELECT it.tag_id FROM items t JOIN item_tags it ON it.item_id = t.item_id
        WHERE t.kind = ? AND t.drive_id = ? AND t.relative_path = ?
        """,
        (kind, drive_id, _to_db_path(path)),
    ).fetchall()
    return [int(r[0]) for r in rows]


def _resolved_contents(
    conn: sqlite3.Connection,
    source_sql: str,
    source_param: Any,
    limit: int,
    offset: int,
    sort: str | None,
    direction: str | None,
) -> tuple[list[dict[str, Any]], int]:
    """Pàgina dels fitxers resolts d'una font d'elements, amb el motiu i l'element d'origen."""
    sort_col = CONTENT_SORT_KEYS.get(sort or "", "name")
    sort_dir = "DESC" if direction == "desc" else "ASC"
    cur = conn.execute(
        f"""
        SELECT i.id, i.drive_id, i.relative_path, i.name, i.extension, i.size_bytes, i.modified_utc,
               i.missing_since_utc, i.hash_sha256, h.item_id, h.reason,
               t.kind AS item_kind, t.drive_id AS item_drive, t.relative_path AS item_path,
               COUNT(*) OVER () AS _total
        FROM ({items_resolution_sql(source_sql)}) h
        JOIN inventory i ON i.id = h.id
        JOIN items t ON t.item_id = h.item_id
        ORDER BY i.{sort_col} {sort_dir}, i.drive_id, i.relative_path
        LIMIT ? OFFSET ?
        """,
        (source_param, source_param, source_param, limit, offset),
    )
    rows = [dict(r) for r in cur.fetchall()]
    total = int(rows[0].pop("_total")) if rows else 0
    for r in rows[1:]:
        r.pop("_total", None)
    if not rows and offset > 0:
        total = int(conn.execute(
            f"SELECT COUNT(*) FROM ({items_resolution_sql(source_sql)})",
            (source_param, source_param, source_param),
        ).fetchone()[0])
    return rows, total


def get_manual_contents(conn, space, limit, offset, sort=None, direction=None):
    return _resolved_contents(conn, SPACE_ITEMS_SQL, space["space_id"], limit, offset, sort, direction)


def get_tag_contents(conn, tag_id, limit, offset, sort=None, direction=None):
    return _resolved_contents(conn, TAG_ITEMS_SQL, tag_id, limit, offset, sort, direction)


def get_smart_contents(
    conn: sqlite3.Connection,
    space: dict[str, Any],
    limit: int,
    offset: int,
    sort: str | None = None,
    direction: str | None = None,
) -> tuple[list[dict[str, Any]], int]:
    """Executa la cerca desada d'un espai `smart` (una pàgina i el total)."""
    query = space.get("query") or parse_search_params({})
    sort_by = CONTENT_SORT_KEYS.get(sort or "", None)
    sort_dir = direction if direction in ("asc", "desc") else None
    return run_file_search(conn, query, limit, offset, sort_by=sort_by or None, sort_dir=sort_dir)
