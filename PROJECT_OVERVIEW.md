# Discàleg — Visió general del projecte

Document de referència per recuperar el context del projecte en futurs xats.

## Estructura del projecte

```
Discàleg/
├── app/
│   ├── __init__.py
│   ├── main.py           # Entrada FastAPI, rutes, Jinja2
│   ├── config.py         # database_path (DATABASE_PATH o data/arxiu_discs.db)
│   ├── db.py             # get_db() — connexió SQLite per petició
│   ├── queries.py        # Consultes SQL (dashboard, cerca, duplicats, extensions, folders)
│   ├── queries_explorer.py # Consultes per a l'explorador simulat (arbre i contingut des de inventory)
│   ├── queries_spaces.py # Espais virtuals: arbre jeràrquic, CRUD, contingut de les cerques desades
│   ├── queries_copies.py # Informes de còpies: còpia única i comparació entre discs (hash o nom+mida)
│   ├── search_params.py  # Paràmetres de cerca normalitzats i execució (Cercar i espais intel·ligents)
│   ├── audit_to_db.py    # Escaneig directe a DB (sense CSV intermedis), rutes llargues, errors de lectura
│   ├── disk_info.py      # Identitat física del disc (PowerShell Get-Disk) i detecció de sèries genèriques
│   ├── volume_info.py    # Etiqueta, sistema de fitxers, serial, capacitat i espai lliure del volum
│   ├── routes/
│   │   ├── dashboard.py, search.py, duplicates.py, extensions.py, folders.py, explorer.py, discs.py, spaces.py, copies.py
│   │   └── ...
│   ├── templates/       # Jinja2 (base.html, dashboard, search, discs, welcome, ...)
│   └── static/           # style.css, app.js (tema), explorer.js (explorador)
├── data/                 # Ubicació de arxiu_discs.db (exclosa per .gitignore)
├── .gitignore             # Exclou data/, venv/, *.db, .env
├── CLAUDE.md             # Guia per a Claude Code (convencions, dades, verificació)
├── LICENSE               # PolyForm Noncommercial 1.0.0
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
- **Esquema versió 8** (`PRAGMA user_version = 8`, definit a `app/schema.py`; la v8 afegeix `drives.exclude_paths` i `scans.exclude_paths` (exclusions per disc, llista JSON); la v7 és només `ALTER TABLE ADD COLUMN` i una taula nova, la migració tarda segons: `drives` guanya `free_bytes`, `physical_size_bytes` i `health_status` (i es passen a omplir `serial_number`, `model`, `vendor` i `interface`), `scans` guanya `serial_number`, `model`, `interface`, `free_bytes`, `health_status` i `errors_count`, `inventory` guanya `created_utc` i `attributes`, i s'afegeix `read_errors`; la v6 afegeix `scan_changes`, l'índex parcial `idx_inventory_drive_hash(drive_id, hash_sha256, size_bytes)` i les taules dels espais virtuals (`spaces`, `items`, `space_members`, `tags`, `item_tags`), tot additiu; la v5 completa `folder_stats` amb les carpetes sense fitxers directes, afegeix índexs (drive_id, folder_depth) i (drive_id, folder_path) i fa cobridor l'índex de hash; la v3 afegeix `inventory_fts`, FTS5 trigram sobre name i relative_path amb triggers de sincronització; la v4 substitueix l'índex de nom per `idx_inventory_name_size(name, size_bytes, drive_id, extension) WHERE missing_since_utc IS NULL`):

  - **drives**: drive_id (PK), label, kind, interface, vendor, model, serial_number, volume_serial, filesystem, capacity_bytes, location, status, notes, files_count, total_bytes, hashed_files, hashed_bytes, last_root_path, last_scan_utc, created_utc, updated_utc, i (v7) free_bytes, physical_size_bytes, health_status. L'escaneig omple el maquinari (`app/disk_info.py`) només als camps buits (amb «Reiniciar» o `--force` el substitueix), refresca sempre free_bytes i health_status, i dedueix `kind` (hdd/ssd/nvme) només si és nul i el tipus de suport és clar. Les sèries genèriques de les carcasses USB (p. ex. `0123456789ABCDEF`, model «Generic DISK…») no es guarden mai a `drives.serial_number` ni bloquegen res. (v8) exclude_paths: llista JSON de rutes relatives a l'arrel (carpeta amb tot el que penja, o fitxer; coincidència exacta sense distingir majúscules, sense comodins) que l'escaneig no recorre; NULL si cap. Normalització a `audit_to_db.normalize_exclude` («/» i «\» acceptats, sense separadors als extrems, absolutes sota l'arrel convertides a relatives; buides, amb «..» o fora de l'arrel rebutjades).
  - **scans**: scan_id, drive_id, root_path, mode (update/replace), started_utc, finished_utc, status (running/done/aborted), files_count, total_bytes, files_new, files_changed, files_missing, tool_version, notes, i (v7) el maquinari vist en aquell escaneig tal com s'ha detectat: serial_number, model, interface, free_bytes, health_status, més errors_count. (v8) exclude_paths: la llista aplicada en aquell escaneig (`[]` si cap; NULL als escaneigs anteriors a la v8).
  - **inventory**: id, drive_id, relative_path, name, extension (`(no_ext)` si no en té), size_bytes, modified_utc, created_utc (v7, `st_birthtime`), attributes (v7, `st_file_attributes` de Windows; la màscara `schema.FILE_ATTR_CLOUD_MASK` marca els fitxers que només són al núvol), hash_sha256, hashed_utc, hash_size_bytes, hash_mtime_utc, first_seen_scan, last_seen_scan, missing_since_utc. created_utc i attributes s'omplen a cada escaneig però no són criteri de «modificat» ni invaliden el hash. Índexs: drive_id, (drive_id, extension), **únic (drive_id, relative_path)**, hash_sha256 (parcial), size_bytes, (name, size_bytes, drive_id, extension) parcial sobre fitxers presents.
  - **folder_stats**: id, drive_id, folder_path (relatiu a l'arrel; `''` = arrel), folder_name, folder_depth, files_count, total_bytes (fitxers directes; totes les carpetes hi són, també les buides). Índexs: drive_id, folder_name, (drive_id, folder_depth), (drive_id, folder_path).
  - **by_extension**: id, drive_id, extension, count, total_bytes. Índex: drive_id.
  - **scan_changes** (v6): scan_id, drive_id, relative_path, change (`new`/`changed`/`missing`), old_size, new_size, old_mtime, new_mtime. Una fila per fitxer afectat en cada escaneig en mode update. Índex (scan_id, change, relative_path).
  - **spaces** (v6): space_id, parent_id (jeràrquics), name, kind (`smart` = cerca guardada amb `query_json`; `manual` = col·lecció), description, sort_order, created_utc, updated_utc.
  - **items** (v6): identitat d'un element referenciat per col·leccions i etiquetes: kind (`file`/`folder`), drive_id, relative_path (referència secundària; per a carpetes, el prefix), hash_sha256 (identitat primària dels fitxers; es completa quan el hash existeix), size_bytes, name, range_lo/range_hi (rang de prefix de les carpetes). Únic (kind, drive_id, relative_path).
  - **space_members** (v6): space_id, item_id, added_utc, note. **tags** (v6): tag_id, name (únic, sense distingir majúscules), color. **item_tags** (v6): item_id, tag_id, added_utc.
  - **read_errors** (v7): drive_id, scan_id (nul per als errors del hash), phase (`scandir` carpeta il·legible, `entry` entrada il·legible, `hash_stat`, `hash_read`), relative_path, error_code (winerror o errno), message, created_utc. Índexs (scan_id) i (drive_id, relative_path). Els errors del hash es reescriuen a cada execució (un registre per fitxer); `delete_drive` els esborra amb la resta.

- **Creació/actualització:** `app/schema.py` (`init_schema`, `migrate`), `app/audit_to_db.py` (`scan_drive(conn, root, drive_id, mode)`), `python -m app.migrate` per a DB antigues, `tools/hash_files.py` per als hashes. Mode WAL per poder consultar mentre s'escriu.

## Flux de dades

- **Històric:** Els scripts de `tools/` generaven CSV (quick_audit_archive) i després s’importaven a la DB (import_audit_to_db). Aquests escaneigs del març de 2026 consten a `scans` com a `legacy-csv-import`.
- **Escaneig (scan_drive):** abans de res, `get_volume_info` (etiqueta, sistema de fitxers, serial del volum, capacitat, espai lliure) i `get_physical_disk_info` (PowerShell amb límit de temps; tot nul si falla), i la comprovació del serial del volum i del número de sèrie físic contra `drives` (atura si no coincideixen, llevat de replace/`--force`). Recorregut `os.scandir` iteratiu sobre la ruta amb prefix `\\?\` (`long_path`: rutes de més de 260 caràcters sense dependre de LongPathsEnabled; a la DB tot es guarda sense prefix), sense seguir enllaços ni junctions, que a Windows ja dona mida, dates i atributs sense cap crida extra; les exclusions (`exclude_paths` de la crida, o si és None la llista desada a `drives`; en tots dos casos es desa a `drives` i a `scans`) s'apliquen a qualsevol profunditat com un conjunt de rutes en majúscules: la carpeta exclosa no entra a la pila (ni a `folder_stats`) i el fitxer exclòs no s'emet, i sense exclusions el cost és un sol `if` per entrada; `$RECYCLE.BIN` i `System Volume Information` a l'arrel s'exclouen sempre a part → taula temporal `scan_files` en chunks de 20.000 → fusió en SQL per conjunts dins d'una transacció: absents → `missing_since_utc`; existents → actualitzar metadades (inclosos created_utc i attributes) conservant el hash si mida i data no han canviat; nous → inserir. Les carpetes i entrades que no es poden llegir no s'ignoren: s'insereixen a `read_errors` (amb el scan_id, en un commit propi abans de la fusió, perquè quedin encara que la guarda aturi l'escaneig) i es compten a `scans.errors_count` i al missatge final, que també diu quantes exclusions s'han aplicat. `folder_stats` i `by_extension` es recalculen per al disc. En mode update, el que ja era a l'inventari sota una exclusió nova passa a «desaparegut» per la fusió normal, i la resta conserva els hashes; si es retira l'exclusió, reapareix amb el seu hash. El registre a `scans` es crea abans (estat `running`, amb el maquinari detectat) i es tanca al final (`done`/`aborted`). En mode update, abans de tocar `inventory`, s'insereixen a `scan_changes` (per conjunts) els fitxers desapareguts (mida i data antigues), els modificats (mida i data abans i després) i els nous; el primer escaneig d'un disc en mode update els registra tots com a nous, i el mode replace i la importació CSV no registren detall.
- **Hash (tools/hash_files.py):** procés a part, per disc, només files amb `hash_sha256 IS NULL`, ordre per ruta, lots amb commit, Ctrl+C segur, `--max-minutes`, `--only-size-dupes`, `--workers`. Comprova el serial del volum i el número de sèrie físic contra `drives`. Obre els fitxers amb el prefix `\\?\`. Per defecte salta els fitxers que només són al núvol (`attributes` amb OFFLINE, RECALL_ON_OPEN o RECALL_ON_DATA_ACCESS, i també si el `stat` del moment ho diu) i els compta a part; `--include-cloud` els hasheja (en força la descàrrega). Els errors de `stat` i de lectura van a `read_errors` (un registre per fitxer, reescrit a cada execució).

## Rutes

| Ruta | Descripció |
|------|-------------|
| `/` | Inici: si no hi ha DB → benvinguda; si hi ha DB → dashboard (resum per disc, top extensions, top carpetes) |
| `/search` | Cerca per paraula clau (nom/ruta), filtres per disc. `?tag_id=` limita als fitxers d'una etiqueta (resolts per hash, ruta o carpeta). `?in_drive=&in_path=` limita la cerca a una carpeta (rang de l'índex únic; sense termes llista tota la carpeta; dins de carpetes de menys de 200 k fitxers no s'usa la poda FTS) |
| `/duplicates` | Duplicats per nom, nom+mida o contingut (fitxers) i per camí (carpetes). `?group=hash&hash=` o `?group=name-size&name=&size=` mostra un sol grup (enllaç «Còpies d'aquest fitxer» de l'explorador) |
| `/extensions` | Llistat per extensió (paginat) |
| `/folders` | Llistat de carpetes per mida (paginat) |
| `/explorador` | Explorador simulat d'arxius i carpetes des de la DB (arbre + contingut, sense accés al sistema de fitxers). Accepta `?drive=&path=` per obrir una carpeta directament. API: `/api/explorer/roots`, `/api/explorer/tree/children`, `/api/explorer/contents`, `/api/explorer/folder-totals`, `/api/explorer/export.csv?drive_id=&path=&recursive=` (CSV en streaming, UTF-8 amb BOM, separador «;»). Menú contextual (clic dret, botó ⋯ o Maj+F10) amb cerca dins la carpeta, exportació CSV, còpies d'un fitxer i copiar la ruta |
| `/api/spaces/tree`, `POST /api/spaces`, `PATCH`/`DELETE /api/spaces/{id}`, `/api/spaces/{id}/contents` | Espais virtuals (JSON): arbre, creació, canvi de nom/pare/criteris, esborrat en cascada, i contingut paginat i ordenat al servidor d'una cerca desada |
| `POST /api/spaces/{id}/items`, `DELETE /api/spaces/{id}/items/{item_id}` | Col·leccions manuals: afegir elements `{kind: file|folder, drive_id, path}` (alta a `items`: fitxers amb nom, mida i hash d'inventory; carpetes amb el rang de prefix) i treure'n |
| `/api/tags`, `PATCH`/`DELETE /api/tags/{id}`, `/api/tags/{id}/contents`, `POST /api/items/tag`, `POST /api/items/untag`, `/api/items/tags` | Etiquetes sobre fitxers i carpetes (mateixa identitat que les col·leccions) i el seu contingut resolt |
| `POST /spaces/desa` | Formulari «Desa com a espai» de Cercar: crea (o actualitza, amb `space_id`) una cerca desada i redirigeix a `/explorador?space=ID` |
| `/copies` | Còpies: `view=unique` (fitxers d'un disc o carpeta sense cap còpia en un altre disc, o enlloc amb `scope=any`; sense disc, resum dels 20) i `view=compare` («tot el que hi ha a A és també a B?», amb carpeta opcional d'A). Criteri `mode=hash` (contingut; els fitxers sense hash queden com a «no comprovables») o `mode=name-size` (aproximat). Resultats per carpeta (`show=folders`, amb `prefix=` per baixar) o en llista paginada |
| `/discs` | Gestió de discs: auditar i incorporar, actualitzar o eliminar un disc, i editar-ne la fitxa (etiqueta, tipus, ubicació, notes, exclusions; `POST /discs/fitxa`). El formulari d'escaneig d'un disc nou accepta exclusions (una per línia); buit = la llista desada. Els discs amb exclusions porten la marca «Inventari parcial · N exclusions» |
| `/discs/{drive_id}/canvis` | Què ha canviat en cada escaneig del disc: pestanyes de fitxers nous, modificats (mida i data abans/després) i desapareguts, paginades, des de `scan_changes`; `?scan=&tipus=new|changed|missing&page=` |
| `/discs/{drive_id}/errors` | Carpetes i fitxers que l'escaneig o el hash no han pogut llegir, des de `read_errors`: selector d'escaneig (o tots, inclosos els del hash), pestanyes per fase, codi i text de l'error, paginat; `?scan=&fase=scandir|entry|hash_stat|hash_read&page=`. La fitxa del disc hi enllaça quan n'hi ha, i la taula d'escaneigs té la columna «Errors» |
| `/health` | JSON: database_exists, database_path |

## Mòduls clau

- **app/main.py:** App FastAPI, muntatge de rutes i plantilles, filtre `format_bytes`.
- **app/config.py:** `database_path` (DATABASE_PATH o default).
- **app/db.py:** `get_db()` — obre SQLite; si el fitxer no existeix o l'esquema és antic (cal `python -m app.migrate`), retorna 503.
- **app/queries.py:** Totes les consultes de lectura (dashboard, cerca, duplicats, extensions, folders, get_drive_ids, etc.).
- **app/queries_explorer.py:** Consultes per a l'explorador (get_explorer_drives, get_explorer_children, get_explorer_contents, get_explorer_folder_totals): arbre des de `folder_stats`, fitxers d'una carpeta per rang de l'índex únic (drive_id, relative_path), totals recursius per subcarpeta en una sola passada pel rang de (drive_id, folder_path).
- **app/static/explorer.js:** Frontend de l'explorador (arbre amb càrrega mandrosa i teclat, barra d'adreça, historial via `?drive=&path=` o `?space=`, ordenació, filtre, totals, paginació «carrega més», panell redimensionable, menú contextual `role=menu` amb teclat i files del llistat navegables amb fletxes). Dos modes: carpeta d'un disc i espai virtual (panell «Espais» sota «Discs»: arbre jeràrquic, columnes Disc i Carpeta, ordenació al servidor, accions de la barra d'eines: edita els criteris, reanomena, mou a, elimina; botó + per crear espais).
- **app/schema.py:** `SCHEMA_VERSION`, `DRIVE_KINDS` (tipus de suport i etiqueta en català), `init_schema`, `migrate` (pas a pas fins a la versió actual), `refresh_drive_counters`, pragmes per a escriptures massives.
- **app/audit_to_db.py:** `scan_drive` (escaneig amb fusió update/replace; `ScanResult.errors_count` i `excludes_count`; paràmetre `exclude_paths`), exclusions (`normalize_exclude`, `parse_exclude_lines`, `merge_excludes`, `load_excludes`, `dump_excludes`), `long_path` (prefix `\\?\`), `error_code`/`error_text` (format comú dels errors de lectura), `delete_drive`, `import_from_csv_folder`, `audit_direct_to_db` (compatibilitat).
- **app/disk_info.py:** `get_physical_disk_info(root)` (PowerShell `Get-Partition | Get-Disk` més `Get-PhysicalDisk`: sèrie, model, fabricant, bus, mida física, salut, tipus de suport; subprocés sense finestra amb 25 s de límit; tot None fora de Windows, en rutes UNC o si falla), `is_generic_serial`/`identity_serial` (sèries buides o de carcassa no són identitat), `suggest_kind` (hdd/ssd/nvme només si el tipus de suport és clar).
- **app/migrate.py:** CLI de migració (`python -m app.migrate [--db] [--legacy-audits]`).
- **app/scan_jobs.py:** escaneig en segon pla llançat des de Discs (un a la vegada), estat consultable a `/api/scan/status`.
- **app/volume_info.py:** etiqueta, sistema de fitxers, serial, capacitat i espai lliure del volum (Windows via ctypes).
- **tools/scan_drive.py:** CLI d'escaneig amb progrés; `--exclude RUTA` (repetible), `--exclude-from FITXER` (una per línia, UTF-8, `#` comentaris) i `--clear-excludes`, que es combinen amb la llista desada a la fitxa i la guarden.
- **tools/hash_files.py:** CLI de hash SHA-256 incremental per disc (salta els fitxers només al núvol llevat de `--include-cloud`; errors a `read_errors`; `--status` inclou la columna «núvol»).
- **app/routes/explorer.py:** Pàgina Explorador (GET /explorador; requereix DB).
- **app/search_params.py:** `parse_search_params` normalitza els paràmetres de cerca en un diccionari pla i serialitzable (el `query_json` de les cerques desades), `run_file_search` / `run_folder_search` els executen. Ho fan servir la pàgina Cercar i els espais intel·ligents.
- **app/queries_spaces.py:** espais virtuals: `list_spaces`/`build_tree`/`flatten_tree`, `create_space`, `update_space` (valida pare i cicles), `delete_space` (CTE recursiu: descendents i pertinences, sense dependre de les claus foranes), `get_smart_contents` (executa la cerca desada amb ordenació i paginació), `upsert_items`/`add_members`/`remove_member`, etiquetes (`create_tag`, `tag_items`, `untag_items`, `tags_of`), `refresh_item_hashes` (completa el hash dels elements quan arriba) i `get_manual_contents`/`get_tag_contents` sobre `queries.items_resolution_sql`: UNION de tres branques indexades (hash → totes les còpies presents a qualsevol disc; disc+ruta per als fitxers sense hash, també si han desaparegut; rang de prefix per a les carpetes), agrupada per fitxer amb el motiu.
- **app/routes/spaces.py:** API JSON dels espais i formulari «Desa com a espai».
- **app/queries_copies.py:** anti-join `NOT EXISTS` sobre els fitxers de l'àmbit (disc o carpeta): exterior per `idx_inventory_drive_hash` (hash) o per disc (nom+mida), sonda per `idx_inventory_hash_size` o `idx_inventory_name_size`; fitxers de 0 bytes ignorats; agrupació pel component de ruta següent; memòria cau en procés dels resums i agrupacions invalidada per `drives.updated_utc`. La cerca (`search_inventory`) accepta `only_single_copy` i `only_duplicates` amb la mateixa condició, utilitzables a les cerques desades.
- **app/routes/discs.py:** Pàgina Discs (GET sense DB opcional per llista; POST escaneig en segon pla; `discs_card_post` desa la fitxa del disc; `scan_changes_page` mostra els canvis per escaneig; `read_errors_page` els errors de lectura). L'escaneig només omple l'etiqueta del disc si és buida: la que escriu l'usuari a la fitxa es conserva. La fitxa mostra maquinari (model, bus, sèrie, mida física), espai lliure, salut (només si no és «Healthy») i el nombre d'errors de lectura.

## Comportament sense DB

- **`/`:** Si `database_path` no és un fitxer, es renderitza la pàgina de benvinguda (enllaç a “Discs” per crear la primera DB).
- **`/discs`:** Es carrega sense dependre de `get_db` per al GET; es mostra el formulari d’audit (i opcionalment import CSV). En POST es pot crear la DB (init_schema) i executar l’audit directe.
- **Altres rutes** (search, duplicates, extensions, folders, explorador): depenen de `get_db()`; si no hi ha DB, retornen 503.

## Idioma

Missatges i etiquetes de la GUI en **català**.
