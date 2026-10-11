---
title: Escaneig, actualització i hash — DiscScope
layout: default
---

# Escaneig, actualització i hash

## Esquema de la base de dades (versió 2)

| Taula | Contingut |
|-------|-----------|
| **drives** | Una fitxa per disc (*drive_id*): etiqueta, tipus, model, serial del volum, capacitat, estat, notes, i comptadors (fitxers, bytes, fitxers amb hash, últim escaneig i arrel). |
| **scans** | Un registre per cada escaneig: disc, arrel, mode, inici/fi, estat (`running`, `done`, `aborted`), fitxers nous/modificats/desapareguts. |
| **inventory** | Un fitxer per fila. A més de ruta, nom, extensió, mida i data: `hash_sha256`, `hashed_utc`, mida i data en el moment del hash, `first_seen_scan`, `last_seen_scan` i `missing_since_utc`. Índex únic per (`drive_id`, `relative_path`). |
| **folder_stats**, **by_extension** | Agregats per carpeta (ruta relativa a l'arrel escanejada) i per extensió. Es recalculen a cada escaneig. |

Els fitxers sense extensió tenen `extension = '(no_ext)'`.

### Migrar una base de dades antiga

Una DB creada amb versions anteriors (sense `drives` ni hashes) s'ha de migrar un sol cop. L'app ho detecta i ho avisa (error 503 amb la instrucció). Treballa sobre una còpia si vols conservar l'original:

```bash
python -m app.migrate
# o indicant la DB i, si en tens, la carpeta amb els logs dels audits originals (dóna l'arrel i la data de cada disc)
python -m app.migrate --db data\arxiu_discs.db --legacy-audits C:\ruta\AUDIT_HHDD
```

Amb 4,8 milions de fitxers tarda mig minut. La DB creix (índexs nous) i passa a mode WAL.

## Escanejar un disc

Des de **Discs** (o programàticament amb `app.audit_to_db.scan_drive`). Dos modes:

- **Actualitzar** (per defecte, botó *Actualitzar* o formulari sense la casella marcada). Es recorre tot el disc i es fusiona amb el que hi ha:
  - fitxers nous → s'afegeixen;
  - fitxers amb mida o data diferents → s'actualitzen i el seu hash s'invalida;
  - fitxers iguals → es conserven amb el seu hash;
  - fitxers que ja no hi són → es marquen amb `missing_since_utc` (no s'esborren).
- **Reiniciar** (casella *Reiniciar el disc*). Esborra totes les files del disc i les torna a inserir. Es perden els hashes. Útil només si l'inventari antic és erroni.

El recorregut usa `os.scandir` (a Windows mida i data venen amb el llistat, sense crides addicionals) i la fusió es fa en SQL per conjunts. Un disc d'un milió de fitxers es recorre en pocs minuts; el límit és la velocitat del disc en llistar directoris.

Cada escaneig registra el serial del volum a `drives.volume_serial`. Això permet detectar més tard si s'està processant el disc equivocat (per exemple dos discs externs que es munten a la mateixa lletra).

## Calcular hashes (SHA-256)

El hash és un procés **a part**, incremental i reprenible, perquè llegeix cada byte del disc: a 150–250 MB/s, 1 TB són entre 1 h i 2 h. Es fa disc a disc amb l'eina de línia d'ordres:

```bash
# estat de tots els discs (fitxers amb hash, bytes pendents)
python tools/hash_files.py --status

# hashejar un disc (l'arrel per defecte és la de l'últim escaneig)
python tools/hash_files.py --drive EXT-A --root F:\

# aturar al cap de 4 hores i continuar un altre dia
python tools/hash_files.py --drive INT-7 --max-minutes 240

# només candidats a duplicat (fitxers amb mida repetida a tota la DB): estalvia un 35 % de lectura
python tools/hash_files.py --drive EXT-B --only-size-dupes

# SSD / NVMe: diversos fils
python tools/hash_files.py --drive SSD-2 --root E:\BACK_SSDEX --workers 4
```

Comportament:

- Només tracta files sense hash (noves o invalidades per un re-escaneig). Es pot aturar amb **Ctrl+C**: el lot en curs es desa i la propera execució continua on era.
- Llegeix en ordre de ruta (localitat al disc), amb buffer de 8 MiB. Els fitxers de 0 bytes reben el hash constant sense obrir-se.
- Si un fitxer ha canviat des de l'escaneig, s'actualitzen mida i data juntament amb el hash. Si ha desaparegut, es marca.
- Si el serial del volum no coincideix amb el registrat per aquell *drive_id*, s'atura (`--force` per ometre la comprovació).
- `--workers 1` (per defecte) és el millor per a discs mecànics; amb SSD o NVMe `--workers 4` o `8` multiplica la velocitat.

### Estratègia recomanada per a molts TB

1. Re-escaneja cada disc (mode *Actualitzar*) quan el connectis: pocs minuts, i deixa la fitxa del disc completa.
2. Llança el hash del mateix disc amb `--max-minutes` ajustat al temps que el tindràs connectat. Repeteix fins a completar-lo.
3. Si l'objectiu principal és trobar duplicats, comença amb `--only-size-dupes` a tots els discs, i deixa la resta per a quan vulguis verificació d'integritat completa.
4. Comprova el progrés amb `--status` o a la pàgina **Discs** (percentatge de hash per disc).
