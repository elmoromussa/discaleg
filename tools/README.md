# tools/

Eines de línia d'ordres. S'executen des de l'arrel del projecte.

| Fitxer | Ús |
|--------|----|
| `scan_drive.py` | Escaneja un disc i el fusiona a la DB (mateix motor que la pàgina Discs) amb progrés al terminal. `python tools/scan_drive.py --drive EXT-A --root F:\`. |
| `hash_files.py` | Calcula el SHA-256 dels fitxers d'un disc i el desa a `inventory.hash_sha256`. Incremental i reprenible. `python tools/hash_files.py --status` / `--drive EXT-A --root F:\`. Vegeu `docs/escaneig_i_hash.md`. |
| `cataleg.py` | Catàleg de projectes per a la migració a l'arxiu (pas A): classes d'anàlisi, candidats, proposta de sèrie/projecte/capa, CSV de revisió. `python tools/cataleg.py`. Vegeu `docs/migracio.md`. |
| `rutes_metashape.py` | Comprovador de rutes dels projectes Metashape: resol cada foto de cada `.psx` contra l'inventari (mateix disc, altre disc, orfe) sense obrir Metashape; taula `psx_check`, CSV i `--selection`. Vegeu `docs/migracio.md`. |
| `analisi_arxiu/` | Scripts de només lectura de l'anàlisi arxivística (14/10/2026): extracció compacta, diccionari de capes (ara integrat a `app/classes.py`) i arbre per disc. |
| `quick_audit_archive.py` | Script original (març 2026) que recorria un disc i generava `inventory_files.csv`, `folders_stats.csv`, `by_extension.csv` i `audit_log.txt`. Ja no cal: l'app escaneja directament a la DB. Es conserva com a procedència de les dades i per si cal auditar un disc des d'una màquina sense l'app. |
| `import_audit_to_db.py` | Importador original dels CSV a la DB (esquema antic). Substituït per `app.audit_to_db.import_from_csv_folder`, que entén l'esquema actual. |
| `import_all_audits.bat` | Batch original que importava els 20 discs. Només referència històrica. |
