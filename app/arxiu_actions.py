"""Accions de l'arxiu virtual des de la interfície, amb registre i «desfer».

Cada acció escriu amb `queries_spaces` (sense commit) i, a la mateixa transacció, una fila a
`arxiu_log` amb les operacions inverses (`undo_json`): una llista d'operacions primitives que
`undo_last` torna a aplicar en ordre. Les primitives són prou per a desfer qualsevol acció:

  member_set   (space_id, item_id, role, target_subpath, note, added_utc?)   crea o deixa com era
  member_del   (space_id, item_id)
  space_set    (space_id, camps…)                                             deixa com era
  space_insert (fila sencera, amb el mateix space_id)                         torna un espai esborrat
  space_del    (space_id)

Les accions validen amb les mateixes regles que la línia d'ordres (jerarquia, nomenclatura,
paper, destí). No toquen cap disc.
"""
from __future__ import annotations

import json
import sqlite3
from typing import Any

from app import queries_spaces
from app.queries_spaces import SpaceError
from app.schema import utc_now_iso

SPACE_FIELDS = ("parent_id", "name", "kind", "query_json", "description", "sort_order", "created_utc",
                "updated_utc", "archive_kind", "target_name", "state", "canonical_drive_id", "source")


# ---------------------------------------------------------------------------
# Lectura d'estat (per a construir les inverses)
# ---------------------------------------------------------------------------

def _member_row(conn: sqlite3.Connection, space_id: int, item_id: int) -> dict[str, Any] | None:
    r = conn.execute(
        "SELECT space_id, item_id, added_utc, note, role, target_subpath FROM space_members "
        "WHERE space_id = ? AND item_id = ?", (space_id, item_id)).fetchone()
    return dict(zip(("space_id", "item_id", "added_utc", "note", "role", "target_subpath"), tuple(r))) if r else None


def _space_row(conn: sqlite3.Connection, space_id: int) -> dict[str, Any] | None:
    r = conn.execute(f"SELECT space_id, {', '.join(SPACE_FIELDS)} FROM spaces WHERE space_id = ?",
                     (space_id,)).fetchone()
    return dict(zip(("space_id", *SPACE_FIELDS), tuple(r))) if r else None


def _space_label(conn: sqlite3.Connection, space_id: int) -> str:
    chain = []
    cur = space_id
    for _ in range(20):
        r = conn.execute("SELECT parent_id, archive_kind, target_name, name FROM spaces WHERE space_id = ?",
                         (cur,)).fetchone()
        if r is None:
            break
        chain.append(r[2] or r[3])
        if r[0] is None or r[1] is None:
            break
        cur = r[0]
    return "\\".join(reversed(chain)) or str(space_id)


def _item_label(conn: sqlite3.Connection, item_id: int) -> str:
    r = conn.execute("SELECT drive_id, relative_path FROM items WHERE item_id = ?", (item_id,)).fetchone()
    return f"{r[0]}\\{r[1]}" if r else str(item_id)


def _member_undo(row: dict[str, Any] | None, space_id: int, item_id: int) -> dict[str, Any]:
    if row is None:
        return {"op": "member_del", "space_id": space_id, "item_id": item_id}
    return {"op": "member_set", **row}


# ---------------------------------------------------------------------------
# Registre
# ---------------------------------------------------------------------------

def _log(conn: sqlite3.Connection, action: str, summary: str, undo: list[dict[str, Any]]) -> int:
    cur = conn.execute(
        "INSERT INTO arxiu_log (utc, action, summary, undo_json) VALUES (?, ?, ?, ?)",
        (utc_now_iso(), action, summary[:500], json.dumps(undo, ensure_ascii=False)),
    )
    return int(cur.lastrowid)


def last_actions(conn: sqlite3.Connection, limit: int = 10) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT log_id, utc, action, summary, undone_utc FROM arxiu_log ORDER BY log_id DESC LIMIT ?",
        (limit,)).fetchall()
    return [dict(zip(("log_id", "utc", "action", "summary", "undone_utc"), tuple(r))) for r in rows]


def last_undoable(conn: sqlite3.Connection) -> dict[str, Any] | None:
    r = conn.execute(
        "SELECT log_id, utc, action, summary FROM arxiu_log WHERE undone_utc IS NULL ORDER BY log_id DESC LIMIT 1"
    ).fetchone()
    return dict(zip(("log_id", "utc", "action", "summary"), tuple(r))) if r else None


def _apply_ops(conn: sqlite3.Connection, ops: list[dict[str, Any]]) -> None:
    for op in ops:
        kind = op.get("op")
        if kind == "member_set":
            conn.execute(
                """
                INSERT INTO space_members (space_id, item_id, added_utc, note, role, target_subpath)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(space_id, item_id) DO UPDATE SET
                    note = excluded.note, role = excluded.role, target_subpath = excluded.target_subpath
                """,
                (op["space_id"], op["item_id"], op.get("added_utc") or utc_now_iso(),
                 op.get("note"), op.get("role"), op.get("target_subpath")),
            )
        elif kind == "member_del":
            conn.execute("DELETE FROM space_members WHERE space_id = ? AND item_id = ?",
                         (op["space_id"], op["item_id"]))
        elif kind == "space_set":
            fields = [f for f in SPACE_FIELDS if f in op]
            if fields:
                conn.execute(f"UPDATE spaces SET {', '.join(f + ' = ?' for f in fields)} WHERE space_id = ?",
                             [op[f] for f in fields] + [op["space_id"]])
        elif kind == "space_insert":
            conn.execute(
                f"INSERT OR REPLACE INTO spaces (space_id, {', '.join(SPACE_FIELDS)}) "
                f"VALUES ({', '.join('?' * (len(SPACE_FIELDS) + 1))})",
                [op["space_id"]] + [op.get(f) for f in SPACE_FIELDS],
            )
        elif kind == "space_del":
            conn.execute("DELETE FROM space_members WHERE space_id = ?", (op["space_id"],))
            conn.execute("DELETE FROM spaces WHERE space_id = ?", (op["space_id"],))
        else:
            raise SpaceError(f"Operació desconeguda al registre: {kind!r}")


def undo_last(conn: sqlite3.Connection) -> dict[str, Any] | None:
    """Desfà la darrera acció no desfeta. Retorna la fila del registre, o None si no n'hi ha."""
    r = conn.execute(
        "SELECT log_id, action, summary, undo_json FROM arxiu_log WHERE undone_utc IS NULL ORDER BY log_id DESC LIMIT 1"
    ).fetchone()
    if r is None:
        return None
    ops = json.loads(r[3])
    _apply_ops(conn, ops)
    conn.execute("UPDATE arxiu_log SET undone_utc = ? WHERE log_id = ?", (utc_now_iso(), r[0]))
    conn.commit()
    return {"log_id": r[0], "action": r[1], "summary": r[2]}


# ---------------------------------------------------------------------------
# Accions sobre membres
# ---------------------------------------------------------------------------

def add_members(conn: sqlite3.Connection, space_id: int, specs: list[dict[str, Any]],
                role: str | None, target_subpath: str | None, note: str | None) -> dict[str, Any]:
    space = queries_spaces.get_space(conn, space_id)
    if space is None or space["archive_kind"] is None:
        raise SpaceError("L'espai no existeix o no és un espai d'arxiu.")
    ids, errors = queries_spaces.upsert_items(conn, specs)
    undo = [_member_undo(_member_row(conn, space_id, iid), space_id, iid) for iid in ids]
    res = queries_spaces.add_members(conn, space_id, specs, role=role, target_subpath=target_subpath,
                                     note=note, commit=False)
    label = _space_label(conn, space_id)
    n = len(ids)
    summary = f"Afegir {n} element{'s' if n != 1 else ''} a «{label}»" + (f" com a {role}" if role else "")
    if n == 1:
        summary = f"Afegir {_item_label(conn, ids[0])} a «{label}»" + (f" com a {role}" if role else "")
    if ids:
        _log(conn, "afegir-membres", summary, undo)
    conn.commit()
    res["errors"] = errors
    res["summary"] = summary
    return res


def update_member(conn: sqlite3.Connection, space_id: int, item_id: int, fields: dict[str, Any]) -> dict[str, Any]:
    old = _member_row(conn, space_id, item_id)
    if old is None:
        raise SpaceError("Aquest element no és a l'espai.")
    kwargs: dict[str, Any] = {}
    for k in ("role", "target_subpath", "note"):
        if k in fields:
            kwargs[k] = fields[k]
    if not kwargs:
        return {"changed": False}
    queries_spaces.update_member(conn, space_id, item_id, commit=False, **kwargs)
    conn.execute("UPDATE spaces SET updated_utc = ? WHERE space_id = ?", (utc_now_iso(), space_id))
    what = ", ".join(f"{k} → {kwargs[k] or '—'}" for k in kwargs)
    summary = f"Canviar {what} de {_item_label(conn, item_id)} a «{_space_label(conn, space_id)}»"
    _log(conn, "canviar-membre", summary, [{"op": "member_set", **old}])
    conn.commit()
    return {"changed": True, "summary": summary}


def remove_member(conn: sqlite3.Connection, space_id: int, item_id: int) -> dict[str, Any]:
    old = _member_row(conn, space_id, item_id)
    if old is None:
        raise SpaceError("Aquest element no és a l'espai.")
    summary = f"Treure {_item_label(conn, item_id)} de «{_space_label(conn, space_id)}»"
    conn.execute("DELETE FROM space_members WHERE space_id = ? AND item_id = ?", (space_id, item_id))
    conn.execute("UPDATE spaces SET updated_utc = ? WHERE space_id = ?", (utc_now_iso(), space_id))
    _log(conn, "treure-membre", summary, [{"op": "member_set", **old}])
    conn.commit()
    return {"removed": True, "summary": summary}


def move_member(conn: sqlite3.Connection, space_id: int, item_id: int, to_space_id: int) -> dict[str, Any]:
    old = _member_row(conn, space_id, item_id)
    if old is None:
        raise SpaceError("Aquest element no és a l'espai.")
    if to_space_id == space_id:
        return {"moved": False}
    target = queries_spaces.get_space(conn, to_space_id)
    if target is None or target["archive_kind"] is None:
        raise SpaceError("L'espai de destí no existeix o no és un espai d'arxiu.")
    undo = [_member_undo(_member_row(conn, to_space_id, item_id), to_space_id, item_id),
            {"op": "member_set", **old}]
    conn.execute(
        """
        INSERT INTO space_members (space_id, item_id, added_utc, note, role, target_subpath)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(space_id, item_id) DO UPDATE SET
            note = excluded.note, role = excluded.role, target_subpath = excluded.target_subpath
        """,
        (to_space_id, item_id, old["added_utc"], old["note"], old["role"], old["target_subpath"]),
    )
    conn.execute("DELETE FROM space_members WHERE space_id = ? AND item_id = ?", (space_id, item_id))
    now = utc_now_iso()
    conn.execute("UPDATE spaces SET updated_utc = ? WHERE space_id IN (?, ?)", (now, space_id, to_space_id))
    summary = (f"Moure {_item_label(conn, item_id)} de «{_space_label(conn, space_id)}» "
               f"a «{_space_label(conn, to_space_id)}»")
    _log(conn, "moure-membre", summary, undo)
    conn.commit()
    return {"moved": True, "summary": summary}


# ---------------------------------------------------------------------------
# Accions sobre espais
# ---------------------------------------------------------------------------

def create_space(conn: sqlite3.Connection, parent_id: int | None, archive_kind: str, target_name: str,
                 state: str | None = None, canonical_drive_id: str | None = None) -> dict[str, Any]:
    space = queries_spaces.create_space(
        conn, name=target_name, kind="manual", parent_id=parent_id, archive_kind=archive_kind,
        target_name=target_name, state=state, canonical_drive_id=canonical_drive_id, commit=False,
    )
    summary = f"Crear {archive_kind} «{_space_label(conn, space['space_id'])}»"
    _log(conn, "crear-espai", summary, [{"op": "space_del", "space_id": space["space_id"]}])
    conn.commit()
    return {"space": space, "summary": summary}


def update_space(conn: sqlite3.Connection, space_id: int, fields: dict[str, Any]) -> dict[str, Any]:
    old = _space_row(conn, space_id)
    if old is None:
        raise SpaceError("L'espai no existeix.")
    kwargs: dict[str, Any] = {}
    for k in ("target_name", "state", "canonical_drive_id", "parent_id", "description", "archive_kind"):
        if k in fields:
            kwargs[k] = fields[k]
    if "target_name" in kwargs:
        kwargs["name"] = kwargs["target_name"]
    if not kwargs:
        return {"changed": False}
    label_before = _space_label(conn, space_id)
    queries_spaces.update_space(conn, space_id, commit=False, **kwargs)
    what = ", ".join(f"{k} → {kwargs[k] if kwargs[k] is not None else '—'}" for k in kwargs if k != "name")
    summary = f"Canviar {what} de «{label_before}»"
    undo_fields = {k: old[k] for k in ("parent_id", "name", "archive_kind", "target_name", "state",
                                       "canonical_drive_id", "description", "updated_utc")}
    _log(conn, "canviar-espai", summary, [{"op": "space_set", "space_id": space_id, **undo_fields}])
    conn.commit()
    return {"changed": True, "summary": summary}


def reorder_space(conn: sqlite3.Connection, space_id: int, direction: int) -> dict[str, Any]:
    """Intercanvia l'ordre amb el germà anterior (-1) o següent (+1), entre espais d'arxiu."""
    me = _space_row(conn, space_id)
    if me is None:
        raise SpaceError("L'espai no existeix.")
    siblings = conn.execute(
        "SELECT space_id, sort_order FROM spaces WHERE parent_id IS ? AND archive_kind IS NOT NULL "
        "ORDER BY sort_order, target_name", (me["parent_id"],)).fetchall()
    ids = [r[0] for r in siblings]
    i = ids.index(space_id)
    j = i + (1 if direction > 0 else -1)
    if j < 0 or j >= len(ids):
        return {"moved": False}
    # normalitza l'ordre perquè l'intercanvi siga efectiu encara que hi haja empats
    undo = [{"op": "space_set", "space_id": r[0], "sort_order": r[1]} for r in siblings]
    ids[i], ids[j] = ids[j], ids[i]
    for pos, sid in enumerate(ids):
        conn.execute("UPDATE spaces SET sort_order = ? WHERE space_id = ?", (pos, sid))
    summary = f"Reordenar «{_space_label(conn, space_id)}» ({'avall' if direction > 0 else 'amunt'})"
    _log(conn, "reordenar-espai", summary, undo)
    conn.commit()
    return {"moved": True, "summary": summary}


def delete_space(conn: sqlite3.Connection, space_id: int) -> dict[str, Any]:
    """Esborra un espai d'arxiu i els seus descendents (amb els membres); tot es pot desfer."""
    if _space_row(conn, space_id) is None:
        raise SpaceError("L'espai no existeix.")
    label = _space_label(conn, space_id)
    ids = queries_spaces.descendant_ids(conn, space_id)
    undo: list[dict[str, Any]] = []
    # pares abans que fills (descendant_ids ja ve en amplada des de l'arrel)
    for sid in ids:
        row = _space_row(conn, sid)
        if row:
            undo.append({"op": "space_insert", **row})
    for sid in ids:
        for r in conn.execute("SELECT space_id, item_id, added_utc, note, role, target_subpath "
                              "FROM space_members WHERE space_id = ?", (sid,)):
            undo.append({"op": "member_set", **dict(zip(("space_id", "item_id", "added_utc", "note", "role",
                                                         "target_subpath"), tuple(r)))})
    ph = ",".join("?" * len(ids))
    n_members = conn.execute(f"SELECT COUNT(*) FROM space_members WHERE space_id IN ({ph})", ids).fetchone()[0]
    conn.execute(f"DELETE FROM space_members WHERE space_id IN ({ph})", ids)
    conn.execute(f"DELETE FROM spaces WHERE space_id IN ({ph})", ids)
    summary = f"Esborrar «{label}» ({len(ids)} espais, {n_members} membres)"
    _log(conn, "esborrar-espai", summary, undo)
    conn.commit()
    return {"deleted": len(ids), "members": n_members, "summary": summary}
