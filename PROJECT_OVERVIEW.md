# DiscScope — Visió general del projecte

Document de referència per recuperar el context del projecte en futurs xats.

## Estructura del projecte

```
DiscScope/
├── app/
│   ├── __init__.py
│   ├── main.py           # Entrada FastAPI, rutes, Jinja2
│   ├── config.py         # database_path (DATABASE_PATH o data/arxiu_discs.db)
│   ├── db.py             # get_db() — connexió SQLite per petició
│   ├── queries.py        # Consultes SQL (dashboard, cerca, duplicats, extensions, folders)
│   ├── queries_explorer.py # Consultes per a l'explorador simulat (arbre i contingut des de inventory)
│   ├── audit_to_db.py    # Esquema + audit directe a DB (sense CSV intermedis)
│   ├── routes/
│   │   ├── dashboard.py, search.py, duplicates.py, extensions.py, folders.py, explorer.py, discs.py
│   │   └── ...
│   ├── templates/       # Jinja2 (base.html, dashboard, search, discs, welcome, ...)
│   └── static/           # style.css, app.js
├── data/                 # Ubicació de arxiu_discs.db (exclosa per .gitignore)
├── .gitignore             # Exclou data/, venv/, *.db, .env
├── README.md
├── requirements.txt
└── run_inventari_WIN.bat
```

La carpeta **temp/** no forma part del projecte; conté scripts històrics (quick_audit_archive, import_audit_to_db, .bat) que s’han integrat o substituït per el flux directe a la DB des de la GUI.

## Stack

- **Backend:** Python 3.10+, FastAPI, uvicorn
- **Plantilles:** Jinja2 (FastAPI Jinja2Templates)
- **Base de dades:** SQLite3 (stdlib), lectura des de l’app; escriptura només des de la gestió de discs (audit directe o import CSV)
- **Frontend:** HTML + CSS + JS mínim (tema clar/fosc)

## Base de dades

- **Ruta:** `app/config.py` — per defecte `PROJECT_ROOT / "data" / "arxiu_discs.db"`; override amb variable d’entorn `DATABASE_PATH`.
- **Esquema versió 2** (`PRAGMA user_version = 2`, definit a `app/schema.py`):

  - **drives**: drive_id (PK), label, kind, interface, vendor, model, serial_number, volume_serial, filesystem, capacity_bytes, location, status, notes, files_count, total_bytes, hashed_files, hashed_bytes, last_root_path, last_scan_utc, created_utc, updated_utc.
  - **scans**: scan_id, drive_id, root_path, mode (update/replace), started_utc, finished_utc, status (running/done/aborted), files_count, total_bytes, files_new, files_changed, files_missing, tool_version, notes.
  - **inventory**: id, drive_id, relative_path, name, extension (`(no_ext)` si no en té), size_bytes, modified_utc, hash_sha256, hashed_utc, hash_size_bytes, hash_mtime_utc, first_seen_scan, last_seen_scan, missing_since_utc. Índexs: drive_id, (drive_id, extension), name, **únic (drive_id, relative_path)**, hash_sha256 (parcial), size_bytes.
  - **folder_stats**: id, drive_id, folder_path (relatiu a l'arrel; `''` = arrel), folder_name, folder_depth, files_count, total_bytes. Índexs: drive_id, folder_name.
  - **by_extension**: id, drive_id, extension, count, total_bytes. Índex: drive_id.

- **Creació/actualització:** `app/schema.py` (`init_schema`, `migrate`), `app/audit_to_db.py` (`scan_drive(conn, root, drive_id, mode)`), `python -m app.migrate` per a DB antigues, `tools/hash_files.py` per als hashes. Mode WAL per poder consultar mentre s'escriu.

## Flux de dades

- **Històric:** Els scripts de `tools/` generaven CSV (quick_audit_archive) i després s’importaven a la DB (import_audit_to_db). Aquests escaneigs del març de 2026 consten a `scans` com a `legacy-csv-import`.
- **Escaneig (scan_drive):** `os.scandir` iteratiu (sense seguir enllaços ni junctions) → taula temporal `scan_files` en chunks de 20.000 → fusió en SQL per conjunts dins d'una transacció: absents → `missing_since_utc`; existents → actualitzar metadades conservant el hash si mida i data no han canviat; nous → inserir. `folder_stats` i `by_extension` es recalculen per al disc. El registre a `scans` es crea abans (estat `running`) i es tanca al final (`done`/`aborted`).
- **Hash (tools/hash_files.py):** procés a part, per disc, només files amb `hash_sha256 IS NULL`, ordre per ruta, lots amb commit, Ctrl+C segur, `--max-minutes`, `--only-size-dupes`, `--workers`. Comprova el serial del volum contra `drives.volume_serial`.

## Rutes

| Ruta | Descripció |
|------|-------------|
| `/` | Inici: si no hi ha DB → benvinguda; si hi ha DB → dashboard (resum per disc, top extensions, top carpetes) |
| `/search` | Cerca per paraula clau (nom/ruta), filtres per disc |
| `/duplicates` | Duplicats per nom o nom+mida (fitxers/carpetes) |
| `/extensions` | Llistat per extensió (paginat) |
| `/folders` | Llistat de carpetes per mida (paginat) |
| `/explorador` | Explorador simulat d'arxius i carpetes des de la DB (arbre col·lapsable + contingut; sense accés al sistema de fitxers) |
| `/discs` | Gestió de discs: auditar i incorporar, o actualitzar un disc (formulari; missatges via query params) |
| `/health` | JSON: database_exists, database_path |

## Mòduls clau

- **app/main.py:** App FastAPI, muntatge de rutes i plantilles, filtre `format_bytes`.
- **app/config.py:** `database_path` (DATABASE_PATH o default).
- **app/db.py:** `get_db()` — obre SQLite; si el fitxer no existeix o l'esquema és antic (cal `python -m app.migrate`), retorna 503.
- **app/queries.py:** Totes les consultes de lectura (dashboard, cerca, duplicats, extensions, folders, get_drive_ids, etc.).
- **app/queries_explorer.py:** Consultes per a l'explorador (get_explorer_drive_ids, get_explorer_children, get_explorer_contents); deriva l'arbre des de `inventory.relative_path`.
- **app/schema.py:** `SCHEMA_VERSION`, `init_schema`, `migrate` (v0/v1 → v2), `refresh_drive_counters`, pragmes per a escriptures massives.
- **app/audit_to_db.py:** `scan_drive` (escaneig amb fusió update/replace), `delete_drive`, `import_from_csv_folder`, `audit_direct_to_db` (compatibilitat).
- **app/migrate.py:** CLI de migració (`python -m app.migrate [--db] [--legacy-audits]`).
- **app/volume_info.py:** etiqueta, sistema de fitxers, serial i capacitat del volum (Windows via ctypes).
- **tools/hash_files.py:** CLI de hash SHA-256 incremental per disc.
- **app/routes/explorer.py:** Pàgina Explorador (GET /explorador; requereix DB).
- **app/routes/discs.py:** Pàgina Discs (GET sense DB opcional per llista; POST audit directe o import CSV).

## Comportament sense DB

- **`/`:** Si `database_path` no és un fitxer, es renderitza la pàgina de benvinguda (enllaç a “Discs” per crear la primera DB).
- **`/discs`:** Es carrega sense dependre de `get_db` per al GET; es mostra el formulari d’audit (i opcionalment import CSV). En POST es pot crear la DB (init_schema) i executar l’audit directe.
- **Altres rutes** (search, duplicates, extensions, folders, explorador): depenen de `get_db()`; si no hi ha DB, retornen 503.

## Idioma

Missatges i etiquetes de la GUI en **català**.
