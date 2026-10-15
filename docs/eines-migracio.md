# Eines de migració — especificació v1 (14/10/2026)

Tres eines dins del repositori discaleg, que implementen el model d'arxiu v1.3 (`claude/model-arxiu-2026-10-14.md`). Es construeixen en aquest ordre: **catàleg** i **comprovador de rutes** ara (pas A), **executor** quan arribe el disc de 16 TB (pas 0 i pas C). Totes tres: Python 3.12, només la biblioteca estàndard més el que ja hi ha a `requirements.txt`, CLI a `tools/`, convencions del `CLAUDE.md` (català, SQL per conjunts, mesurar contra la DB real en mode lectura, `\\?\` per a rutes llargues).

## 0. Esquema v9: taules de migració (additives)

`SCHEMA_VERSION = 9`, migració a `app/schema.py`. Cap canvi a les taules existents. El núvol passa a v10.

| Taula | Columnes | Ús |
|---|---|---|
| `series` | serie_id PK, name (únic, `^[a-z0-9]+(-[a-z0-9]+)*$`), code, kind (`lloc`/`programa`/`client`/`tipus`/`varis`), status (`activa`/`inactiva`), disk_a_serial, disk_b_serial, notes, created_utc, updated_utc | sèries del model §2.1 |
| `projects` | project_id PK, serie_id FK, name (`^\d{4}_[a-z0-9]+(-[a-z0-9]+)*$`, únic dins de la sèrie), year, status (`obert`/`en-tancament`/`tancat`/`descartat`), primary_model (`primari`/`secundari`/null), rights (text), exceptions (text), raw_style (`nou`/`llegat`), created_utc, updated_utc, sealed_utc | projectes §2.2 |
| `candidates` | candidate_id PK, drive_id, relative_path (carpeta), kind (`projecte`/`capa`/`residu`), files_count, total_bytes, unique_bytes_approx, bytes per classe (13 columnes `b_orig`, `b_img`, …), min_mtime, max_mtime, markers_json (recompte de fitxers de projecte per extensió), proposed_serie, proposed_project, proposed_layer (`RAW`/`PROJ`/`OUT`/`DOC`/null), proposed_year, layer_confidence (`regla`/`nom`/`ambigu`), same_content_drives (llista), canonical_proposed (0/1), generated_scan_set (text) | eixida del catàleg (pas A); es regenera sencera a cada execució |
| `candidate_review` | candidate_id PK FK, serie, project, layer, year, decision (`tancar`/`obert`/`descartar`/`fusionar-amb`/`tercers`/`personal`), fusion_target, canonical (0/1), primary_model, exceptions, notes, reviewed_utc | el que omple Esteve (pas B); **no es regenera mai**; s'enllaça per (drive_id, relative_path) si el candidate_id canvia |
| `project_sources` | project_id FK, candidate_id FK, layer, canonical (0/1) | de quines carpetes d'origen s'assembla cada projecte |
| `project_refs` | project_id FK, uses_project_id FK, kind (`captures`/`gis-base`) | «usa captures de» |
| `psx_check` | id PK, drive_id, relative_path (`.psx`), checked_utc, app_version, chunks, cameras, resolved_same, resolved_other_drive, orphans, photos_json (llista: ruta tal com la guarda el `.psx`, resolució, drive_id i ruta on s'ha trobat, size) , notes | eixida del comprovador |
| `events` | event_id PK, utc, agent, kind (`copia`/`verificacio`/`tancament`/`esmena`/`reobertura`/`quarantena`/`esborrat`/`excepcio`/`pas0`), project_id (nullable), drive_id (nullable), relative_path (nullable), outcome (`ok`/`error`/`parcial`), details_json | registre PREMIS-like; només escriuen les eines i Esteve via CLI |
| `copies` | project_id FK, layer, drive_id, verified_utc, manifest_sha256, status (`parcial`/`completa`) | còpies per capa i per suport (el que el README no pot portar) |

## 1. `tools/cataleg.py` — catàleg de projectes (pas A)

**Entrada**: la DB (mode lectura per a `inventory`/`folder_stats`; escriptura només a `candidates`), `data/vocabulari.json` (sèries amb paraules clau, codis, tipus; dispositius; contenidors coneguts; paraules de capa), i opcionalment `psx_check` si el comprovador ja ha corregut.

**Pas 1, classes d'anàlisi.** Integrar `tools/analisi_arxiu/capes.py` com a `app/classes.py` (mateix diccionari, regles de ruta i extensió) i calcular la classe de cada fitxer viu en una taula temporal o columna cau (`inventory.analysis_class`, additiva, nullable; es recalcula quan canvia el diccionari o l'escaneig). Agregats per carpeta pare.

**Pas 2, candidats.** Una carpeta és candidat si: (a) conté directament un fitxer marcador (`.psx .psz .lrcat .blend .qgz .qgs .p2vr .ggpkg .prproj .aep .pts .3dm .indd`), o (b) és de nivell 1 o 2 sota un contenidor del vocabulari (`PROJECTES20xx`, `PROJ2x`, `FOT-VID2x`, `Root\Foto-Video`, `Users\elmor\Documents`, `Users\elmor\Desktop`…) o de nivell 1 en discs sense contenidor; i (c) pesa ≥ 1 GB o compleix (a). Les carpetes `.files` no són candidats: s'adjunten al seu `.psx`. Les carpetes de sistema (`$RECYCLE.BIN`, `System Volume Information`, `.Spotlight-V100`, `found.00x` → candidat especial `recuperacio`) es marquen. La resta de cada disc s'agrupa en un candidat `residu` per disc amb els seus totals.

**Pas 3, proposta.** Per candidat:
- `proposed_serie`: primera sèrie del vocabulari amb paraula clau a la ruta (minúscules, NFC), en l'ordre de precedència del model (lloc → programa → client → tipus); sense coincidència → `varis`. Amb columna `serie_match` (quina paraula ha coincidit) per a poder auditar.
- `proposed_project`: `AAAA_descriptor` on el descriptor ix del nom de carpeta normalitzat (minúscules, accents fora, `_`/espais → `-`, sense any ni codi de sèrie), i l'any de la regla §2.2 (min `modified_utc` dels fitxers de classe ORIG del candidat; si no n'hi ha, dels PROJ; si no, dels PROD/DOC). Si el nom de carpeta conté un any (`2024-covetes`, `PROJECTES2022`, `DW23`), es prefereix.
- `proposed_layer` i `layer_confidence`: taula del model §4 (ORIG → RAW regla; PROC/PROJ → PROJ regla; PROD → OUT regla; DOC → DOC regla; IMG/VID/COMP → per nom de carpeta (`raw dcim orig …` → RAW nom; `out export final render ortho …` → OUT nom; `metashape proj chunks …` → PROJ nom) o `ambigu`). `kind=capa` quan el candidat és una sola classe dominant (> 90 % dels bytes) dins d'un candidat pare que ja és projecte.
- `unique_bytes_approx` amb el criteri de l'auditoria (nom NFC minúscula + mida, ≥ 1 MB), i `same_content_drives`: discs on ≥ 50 % dels bytes del candidat tenen còpia.
- `canonical_proposed`: entre candidats amb el mateix `proposed_project` i contingut compartit ≥ 50 %, el que guanya per la regla §10 (més complet → disc sense `read_errors` → NTFS → mtime més antic → SMART; les dues últimes només si `drives` té la dada).

**Eixida**: taula `candidates` i `data/cataleg_AAAA-MM-DD.csv` (UTF-8 amb BOM, `;`, una fila per candidat, columnes generades + columnes buides de revisió). Resum per pantalla: candidats per tipus, bytes coberts, % per confiança, sèries proposades amb bytes, candidats a `varis`.

**Importació de la revisió**: `tools/cataleg.py --import data/cataleg_revisat.csv` omple `candidate_review` (només files amb alguna columna de revisió emplenada; valida sèries i noms contra les expressions regulars i el vocabulari; errors en un CSV a part, no s'importa res si n'hi ha). `--build-projects` crea `series`, `projects`, `project_sources` a partir de la revisió, sense tocar cap disc.

**Vista a l'app** (mínima): `/cataleg` amb la taula filtrable per sèrie, estat de revisió i confiança, i exportació CSV. Si no hi ha temps, només CLI.

## 2. `tools/rutes_metashape.py` — comprovador de rutes

**Entrada**: tots els `.psx` de l'inventari (o `--drive`, `--path`), accedits al disc real amb `\\?\`; la DB en mode lectura per a resoldre.

**Procediment** per `.psx`: llegir l'XML del `.psx` (versió de l'app, ruta a `.files`); per cada `chunk.zip`, llegir `chunk.xml` (id, etiqueta, nombre de càmeres, sensors); per cada `frame.zip`, llegir `frame.xml` i extraure la ruta de cada `<photo path=…>`. **Primer pas del desenvolupament: obrir un `.psx` real (per exemple a `INT-5\DW23_TFM26\METASHAPE23`) i confirmar l'estructura i la base de les rutes relatives; documentar-la a `docs/`.** Resolució de cada ruta: (1) relativa a la carpeta del frame o del `.psx` → existeix a l'inventari del mateix disc? → `resolved_same`; (2) si no, buscar per nom + mida a tots els discs → `resolved_other_drive` (llista de discs); (3) si no, `orphan`. Mai llegir les fotos: només l'inventari.

**Eixida**: taula `psx_check` i `data/rutes_metashape_AAAA-MM-DD.csv` (una fila per `.psx`: disc, ruta, versió, chunks, càmeres, resoltes mateix disc, resoltes altre disc, orfes, discs implicats, mida de la `.files`), més `--selection <psx>` que escriu `seleccio.txt` (ruta relativa, mida, hash si existeix) per a un `.psx` donat. Resum: `.psx` totalment resolts / parcials / orfes, i els 20 amb més orfes.

**Només lectura.** No obri Metashape. No modifica cap `.psx`. El reenllaç és un altre script, més avant, amb l'API de Metashape i sobre còpia.

## 3. `tools/executor.py` — executor de còpies (pas 0 i pas C)

**Entrada**: el pla `data/pla_arxiu.json` que genera `tools/espais.py --export-plan` des de l'arxiu virtual (espais d'arxiu i membres amb paper; format i exemple a `docs/arxiu-virtual.md`): espais amb ruta de destí, resum per projecte i les llistes `copiar`, `verificar`, `esborrar`, `quarantena` i `pendents`, cada entrada amb origen (disc + ruta, subcarpetes a excloure), destí final, capa i projecte. (Substitueix el pla en CSV de `cataleg.py --plan` previst inicialment.) Per al pas 0 es pot escriure a mà un JSON amb el mateix format.

**Per línia**: comprovar serial del volum i número de sèrie del destí contra `drives` (com `hash_files.py`); comprovar espai lliure; crear destí; `robocopy` amb els paràmetres del model §9 i registre a `data/logs/`; després, en un procés separat, SHA-256 d'origen i destí **rellegint** (reutilitzar `hash_files.py`), comparació, resultat a `copies` i `events`; en cas de discrepància, reintent del fitxer i error si persisteix. **Mai esborra ni mou l'origen.** `--quarantine` és una acció a part: només sobre línies amb `copies.status=completa` verificada en les últimes 24 h, mou a `_QUARANTENA\AAAA-MM-DD\` amb `quarantena.csv`, i registra l'esdeveniment. Reprén per línia (idempotent: línies amb esdeveniment `ok` es salten). `--dry-run` imprimeix el pla sense fer res.

**Manifests i README**: `--manifest <projecte>` genera `manifest-sha256.txt`, `manifest-mides.txt` i, si cal, `referencies-sha256.txt` segons el model §3.5; `--readme-skeleton <projecte>` escriu el README amb els camps que es poden omplir des de la DB (sèrie, nom, any, procedència, dispositius a partir de l'EXIF si exiftool és disponible, nombre de fitxers). El segellat (`icacls`) i `snapraid sync` són `--seal <projecte>`, amb `/save` a `_KIT\acl\` abans.

## 4. Ordre de construcció i criteris d'acceptació

1. v9 de l'esquema + `app/classes.py` + `cataleg.py` (generació i CSV). Acceptació: corre sencer sobre la DB real en < 10 min; el CSV té una fila per candidat; les sèries proposades cobreixen ≥ 90 % dels bytes amb `serie_match` no buit; cap escriptura fora de `candidates` i `inventory.analysis_class`.
2. `rutes_metashape.py`. Acceptació: estructura documentada amb un `.psx` real; corre sobre els 865 `.psx` sense obrir Metashape; CSV amb resolts/orfes per `.psx`; `--selection` produeix un `seleccio.txt` verificable.
3. `cataleg.py --import` i `--build-projects`. Acceptació: rebutja CSV amb noms invàlids; no toca `candidate_review` en regenerar `candidates`.
4. `executor.py` (quan hi haja el disc de 16 TB): `--dry-run` primer; prova amb un projecte menut; després pas 0.

Cada pas: un commit, amb confirmació prèvia, missatge `Migració: …`. Documentar a `PROJECT_OVERVIEW.md`, `README.md` i `docs/migracio.md`.
