---
title: Escaneig, actualització i hash — Discàleg
layout: default
---

# Escaneig, actualització i hash

## Esquema de la base de dades

| Taula | Contingut |
|-------|-----------|
| **drives** | Una fitxa per disc (*drive_id*): etiqueta, tipus, model, serial del volum, capacitat, estat, notes, i comptadors (fitxers, bytes, fitxers amb hash, últim escaneig i arrel). |
| **scans** | Un registre per cada escaneig: disc, arrel, mode, inici/fi, estat (`running`, `done`, `aborted`), fitxers nous/modificats/desapareguts. |
| **scan_changes** | El detall per fitxer de cada escaneig en mode actualització: ruta, tipus de canvi (`new`, `changed`, `missing`) i mida i data abans i després. És el que mostra la vista «Canvis» de cada disc. |
| **inventory** | Un fitxer per fila. A més de ruta, nom, extensió, mida i data: `hash_sha256`, `hashed_utc`, mida i data en el moment del hash, `first_seen_scan`, `last_seen_scan` i `missing_since_utc`. Índex únic per (`drive_id`, `relative_path`). |
| **folder_stats**, **by_extension** | Agregats per carpeta (ruta relativa a l'arrel escanejada; totes les carpetes, amb recomptes de fitxers directes) i per extensió. Es recalculen a cada escaneig. |

Els fitxers sense extensió tenen `extension = '(no_ext)'`.

**Versió 3: índex de text.** `inventory_fts` és un índex FTS5 amb tokenitzador *trigram* sobre nom i ruta, mantingut per triggers. La cerca de fitxers l'usa per podar candidats i després aplica el filtre exacte de sempre, així que els resultats són els mateixos però en mil·lisegons en lloc de segons. Fa créixer la DB (uns 2 GB per a 4,8 M de fitxers) i la construcció inicial tarda uns dos minuts. Fragments de menys de 3 caràcters no es poden indexar i cauen al camí lent.

**Versió 5: totes les carpetes i explorador ràpid.** `folder_stats` passa a contenir totes les carpetes, també les que no tenen cap fitxer directe (abans en faltaven 143.503 de 529.179). L'Explorador es construeix sobre `folder_stats` i llegeix els fitxers d'una carpeta per rang de l'índex, així que és complet i respon en mil·lisegons sigui quin sigui el disc. L'índex de hash passa a ser cobridor per als duplicats per contingut. La migració tarda uns 10 s.

**Versió 6: canvis per escaneig, índex de hash per disc i espais virtuals.** `scan_changes` guarda, per a cada escaneig en mode actualització, quins fitxers són nous, quins han canviat (mida o data abans i després) i quins han desaparegut. L'índex parcial `(drive_id, hash_sha256, size_bytes)` permet recórrer els fitxers amb hash d'un disc per als informes de còpia única i de comparació entre discs. Les taules `spaces`, `items`, `space_members`, `tags` i `item_tags` sostenen els espais virtuals (cerques guardades, col·leccions i etiquetes). Tot és additiu: la migració tarda segons i no reescriu dades.

**Versió 4: índex per a duplicats.** L'índex de nom se substitueix per un índex parcial cobridor sobre nom, mida, disc i extensió dels fitxers presents. Els duplicats per nom i per nom+mida es resolen recorrent només l'índex (d'uns 11 s per pàgina a uns 2 s amb 4,8 M de fitxers), i la DB només creix uns 50 MB.

### Migrar una base de dades antiga

Una DB creada amb versions anteriors (sense `drives` ni hashes) s'ha de migrar un sol cop. L'app ho detecta i ho avisa (error 503 amb la instrucció). Treballa sobre una còpia si vols conservar l'original:

```bash
python -m app.migrate
# o indicant la DB i, si en tens, la carpeta amb els logs dels audits originals (dóna l'arrel i la data de cada disc)
python -m app.migrate --db data\arxiu_discs.db --legacy-audits C:\ruta\AUDIT_HHDD
```

Amb 4,8 milions de fitxers tarda mig minut (versió 2) més uns dos minuts per l'índex de text (versió 3). La DB creix (índexs nous) i passa a mode WAL.

## Escanejar un disc

Des de **Discs** (o programàticament amb `app.audit_to_db.scan_drive`). A la web l'escaneig s'executa en segon pla i la pàgina mostra el progrés (fitxers recorreguts i carpeta actual); es pot tancar la pàgina i tornar-hi. Només pot haver-hi un escaneig en curs a la vegada. Dos modes:

- **Actualitzar** (per defecte, botó *Actualitzar* o formulari sense la casella marcada). Es recorre tot el disc i es fusiona amb el que hi ha:
  - fitxers nous → s'afegeixen;
  - fitxers amb mida o data diferents → s'actualitzen i el seu hash s'invalida;
  - fitxers iguals → es conserven amb el seu hash;
  - fitxers que ja no hi són → es marquen amb `missing_since_utc` (no s'esborren).
- **Reiniciar** (casella *Reiniciar el disc*). Esborra totes les files del disc i les torna a inserir. Es perden els hashes. Útil només si l'inventari antic és erroni.

A l'arrel del volum s'ignoren `$RECYCLE.BIN` i `System Volume Information` (no formen part de l'arxiu). Els fitxers que hi havia dins, en el primer re-escaneig, quedaran marcats com a desapareguts.

**Guarda contra l'arrel equivocada.** En mode actualitzar, si el disc tenia 1.000 fitxers o més i menys del 10 % apareixen al nou recorregut, l'escaneig s'atura sense tocar res. Passa típicament si s'ha triat una altra unitat, o una arrel diferent de la de l'escaneig anterior (SSD-1 i SSD-2 es van escanejar des d'una subcarpeta: `E:\REGISTRE23` i `E:\BACK_SSDEX`, no des de `E:\`). Si realment vols substituir l'inventari, usa **Reiniciar** o `--force`.

**Fitxers desapareguts.** Cercar, Duplicats i Explorador només mostren fitxers presents a l'últim escaneig. A Cercar hi ha la casella *Incloure fitxers desapareguts* (opcions avançades) per trobar-ne un que ja no hi és: apareix marcat com a *(desaparegut)*.

El recorregut usa `os.scandir` (a Windows mida i data venen amb el llistat, sense crides addicionals) i la fusió es fa en SQL per conjunts. Un disc d'un milió de fitxers es recorre en pocs minuts; el límit és la velocitat del disc en llistar directoris.

Cada escaneig registra el serial del volum a `drives.volume_serial`. Si en un escaneig posterior el volum muntat té un serial diferent, s'atura amb un missatge (probable disc equivocat, per exemple dos discs externs que es munten a la mateixa lletra). Si realment és el mateix *drive_id* reformatat o un disc nou, usa **Reiniciar el disc** (o `--force` al CLI).

### Escanejar des del terminal

Per a discs grans és més còmode veure el progrés al terminal:

```bash
python tools/scan_drive.py --drive EXT-A --root F:\
python tools/scan_drive.py --drive NOU-1 --root G:\ --kind hdd --label "Toshiba 4TB"
python tools/scan_drive.py --drive EXT-A --replace      # reinicia (perd hashes)
```

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
