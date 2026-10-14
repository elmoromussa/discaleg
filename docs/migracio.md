# Migració a l'arxiu: catàleg de projectes (pas A)

Eines que implementen el model d'arxiu v1.3 (`docs/model-arxiu.md`) seguint l'especificació de
`docs/eines-migracio.md`. Aquesta pàgina documenta el que ja existeix: l'esquema v9, les classes
d'anàlisi, el vocabulari i el catàleg (`tools/cataleg.py`). El comprovador de rutes de Metashape,
la importació de la revisió (`--import`, `--build-projects`) i l'executor vindran després.

## Esquema v9, v10 i v11

`python -m app.migrate` afegeix, sense tocar cap taula existent (v10: només la columna
`psx_check.chunks_json`, detall per chunk del comprovador de rutes; v11: sis columnes a
`candidates` amb els `.psx` de cada candidat i la mida de les seues `.files`, llegides de
`psx_check`):

- `inventory.analysis_class` (text, nul·lable): classe d'anàlisi de cada fitxer (cau; NULL = pendent).
- `series`, `projects`, `candidates`, `candidate_review`, `project_sources`, `project_refs`,
  `psx_check`, `events`, `copies`: les taules de l'especificació §0, amb restriccions `CHECK`
  sobre els valors enumerats. Detall de columnes a `PROJECT_OVERVIEW.md`.

Dues diferències respecte de l'especificació, totes dues per robustesa:

- `candidate_review` té com a clau primària `(drive_id, relative_path)` i `candidate_id` com a
  columna de referència: la taula `candidates` es regenera sencera i la revisió no s'ha de perdre
  mai. El catàleg, a més, conserva el `candidate_id` de les rutes que ja hi eren.
- `candidates` té 14 columnes de bytes per classe (`b_orig` … `b_altres`), una per cada classe
  que `app/classes.py` pot assignar (inclosa `AUD`), i admet `proposed_layer = 'FORA'` (classe
  dominant SOFT, IMGDISC, SYS o CODI: fora de l'arxiu, taula del model §4) a més de RAW, PROJ,
  OUT, DOC i NULL. El tipus de candidat (`kind`) admet, a més de `projecte`, `capa` i `residu`,
  `recuperacio` (`found.00x`) i `sistema` (paperera, `System Volume Information`, `.Spotlight-V100`…).

## Classes d'anàlisi (`app/classes.py`)

És `tools/analisi_arxiu/capes.py` integrat a l'app: el mateix diccionari (regles de ruta,
noms exactes i extensions) i la mateixa funció `classify`. Cada fitxer rep una classe: ORIG, IMG,
VID, PROJ, PROC, PROD, DOC, AUD, CODI, SOFT, IMGDISC, COMP, SYS o ALTRES (definicions al mòdul).
`update_analysis_classes` la calcula i la desa a `inventory.analysis_class` per als fitxers vius
d'un disc o de tots, per lots de 50.000 files amb commit, només on és NULL; amb `--recalc` ho
refà tot (quan canvia el diccionari). Els fitxers nous d'un escaneig queden a NULL i es
classifiquen a la propera execució del catàleg; els modificats conserven la classe (no depèn de
mida ni data).

```bash
python -m app.classes                 # tots els discs, només files sense classe
python -m app.classes --drive EXT-A --recalc
```

Sobre la DB real (7 M de fitxers) la primera classificació tarda uns 2 minuts; les següents,
segons.

**`.pts` per mida.** L'extensió `.pts` és alhora el fitxer de projecte de PTGui (uns KB o MB) i
un núvol de punts en text (GB). Per sota de 50 MB (`classes.PTS_PROJECT_MAX_BYTES`) és classe
PROJ i marcador de projecte (fa candidat la carpeta); a partir de 50 MB és PROD (núvol de punts)
i no compta com a marcador. Si canvieu el llindar, cal `--recalc`.

## Vocabulari (`data/vocabulari.json`)

És l'únic fitxer de `data/` que es versiona (excepció al `.gitignore`). S'edita a mà; el
catàleg el llig a cada execució i el valida (noms de sèrie amb `^[a-z0-9]+(-[a-z0-9]+)*$`,
tipus admesos). Claus:

| Clau | Contingut |
|---|---|
| `series` | llista de sèries: `name` (nom de carpeta de la sèrie), `code` (codi curt que apareix als noms de fitxer, p. ex. `DW`), `kind` (`lloc`, `programa`, `client`, `tipus` o `varis`: fixa la precedència del model §2.1), `keywords` (paraules clau en minúscules) i, opcionalment, `tipus` (informatiu: el tipus de treball del model §6, p. ex. `video`). |
| `containers` | expressions regulars (sense distingir majúscules) sobre la **ruta relativa sencera** d'una carpeta, fins a 4 nivells. Una carpeta que hi coincideix és un contenidor: no és candidat (ni cap ancestre seu), i les seues carpetes de nivell 1 i 2 ho poden ser. Exemples: `(^|\\)PROJECTES20\d\d[A-Z]?$`, `^Users\\elmor\\(Documents|Desktop|Downloads)$`. |
| `layer_words` | per a `RAW`, `OUT`, `PROJ` i `DOC`, les paraules del nom de carpeta que proposen la capa quan la classe dominant no decideix (confiança `nom`). |
| `system_names` | noms de carpeta (minúscules) que són residu de sistema: candidat `sistema`. Qualsevol nom que continga `recycle` també. |
| `recovery_pattern` | expressió regular dels noms de recuperació de disc (`found.000`): candidat `recuperacio`. |
| `serie_from_content` | `true` (per defecte) o `false`: si la ruta del candidat no conté cap paraula clau, fer votar els seus fitxers de projecte (vegeu el pas 3). |
| `devices` | vocabulari tancat de dispositius del model §6 (informatiu; el catàleg no el fa servir encara). |

**Coincidència de paraules clau.** La ruta del candidat es normalitza (minúscules, sense accents)
i es parteix en tokens (`[a-z0-9]+`). Una paraula clau coincideix amb un token si és igual
(`dw`), si el token és la paraula seguida només de dígits (`dw23`, `pep26`, `cursos25`), o si la
paraula té 5 lletres o més i és dins del token (`lapetacaproject2022`, `covetesvr`). Les paraules
curtes no es busquen dins d'altres paraules (`pep` no coincideix amb `pepe`): si cal, s'afegeix la
forma llarga al vocabulari. Les paraules amb separador o acabades en dígit (`la petaca`, `dh-`,
`dw_`, `dh2`, `registre23`, `360_series`) es busquen literalment a la ruta normalitzada, a l'inici
d'un token (`dh2` troba `DH2PTGUI`; `dh-` troba `DH-EXPEDICIO-2021` però no `dh2ptgui`). Les
paraules de capa coincideixen com a prefix del token (`captur` →
`captures`) o dins del token si tenen 5 lletres o més (`fusion` → `0fusionrender`).

## Catàleg (`tools/cataleg.py`)

```bash
python tools/cataleg.py                        # tots els discs; CSV a data/cataleg_AAAA-MM-DD.csv
python tools/cataleg.py --drive INT-5 --drive EXT-B   # regenera només aquests (la resta es conserva)
python tools/cataleg.py --recalc               # torna a classificar tots els fitxers abans
python tools/cataleg.py --no-csv               # només la taula candidates
python tools/cataleg.py --vocab un_altre.json --csv-dir C:\temp
```

Cal l'esquema v9. Escriu només a `candidates` (regenerada sencera, o els discs indicats) i a
`inventory.analysis_class`; la resta són lectures per conjunts sobre els índexs existents. Sobre
la DB real (22 discs, 7 M de fitxers vius) tarda uns 3 minuts, la major part en la primera
classificació.

**Pas 1, classes.** `update_analysis_classes` per a cada disc.

**Pas 2, candidats** (model §10). Per disc:

1. Contenidors: carpetes que coincideixen amb `containers`; ni elles ni els seus ancestres són
   candidats, ni que tinguen un fitxer de projecte directe (els seus fitxers directes i les
   `.files` van al residu del disc: vigileu que cap patró encaixe amb una carpeta de projecte,
   com `FTGM_GENERAL_DW`). L'arrel del disc actua sempre com a contenidor del nivell 1.
2. Carpetes elegibles per estructura: les de nivell 1 del disc i les de nivell 1 o 2 sota un
   contenidor, llevat de les `.files` de Metashape (s'adjunten al seu `.psx`). Són candidats si
   pesen ≥ 1 GB (recursiu, calculat des de `folder_stats`), o sempre si són de sistema o de
   recuperació.
3. Carpetes amb un fitxer de projecte directe (`.psx .psz .lrcat .blend .qgz .qgs .p2vr .ggpkg
   .prproj .aep .pts .3dm .indd`), a qualsevol profunditat: candidats sempre (índex
   `(drive_id, extension)`).
4. Imbricació: si un candidat és dins d'un altre, el més pròxim és el seu pare (`parent_path`)
   i el candidat passa a `capa`. Els de dalt de tot són `projecte`.
5. El que no és dins de cap candidat (fitxers de l'arrel, carpetes menudes, contingut dels
   contenidors fora dels candidats) s'acumula al candidat `residu` del disc (`relative_path`
   buida).

Una sola passada pels fitxers vius del disc acumula cada fitxer a tots els seus candidats
ancestres (el projecte inclou les seues capes): fitxers, bytes, bytes per classe, any mínim per
classe, dates mínima i màxima (ignorant anys anteriors a 1995), marcadors per extensió i les
còpies: la clau «nom NFC en minúscules + mida» dels fitxers ≥ 1 MiB, calculada una vegada per a
tota la DB, dona per a cada fitxer en quins altres discs apareix. `unique_bytes_approx` són els
bytes sense cap còpia en un altre disc (els < 1 MiB compten com a únics); `same_content_drives`,
els discs on ≥ 50 % dels bytes del candidat tenen còpia. Cap esborrat es decideix amb aquestes
columnes: es recalculen amb hash abans del pas E.

**Pas 3, proposta.**

- `proposed_serie` i `serie_match`: sèries del vocabulari amb una paraula clau a la ruta del
  candidat. Si n'hi ha més d'una, guanya la que coincideix al component de ruta més profund (el
  més pròxim al candidat: a `PROJECTES2022\FGM\poterna_montesa` guanya `montesa` sobre
  `diablo-wasi`); en empat al mateix component, la primera per precedència (lloc → programa →
  client → tipus). `serie_match` diu la paraula guanyadora (i el token, si no és idèntic:
  `dw (dw23)`) i, darrere de `perd:`, les sèries que han perdut amb la seua paraula. Si la ruta
  no diu res i `serie_from_content` és actiu, cada fitxer de projecte del candidat vota per la
  sèrie de la seua ruta relativa amb la mateixa regla; la guanyadora s'assigna només amb 5 vots
  com a mínim i el 60 % o més dels vots (`CONTENT_MIN_VOTES`, `CONTENT_MIN_SHARE`), marcada
  `dins: covetes 51/51` (paraula, vots a favor / vots totals); si no arriba al llindar, el
  candidat queda a `varis` amb `serie_match = suggerit: sèrie n/m` perquè la revisió tinga la
  pista. Sense cap coincidència, `varis` amb `serie_match` buit. Les capes hereten sèrie,
  projecte i any del pare, però si la seua pròpia ruta diu una sèrie diferent
  (`PROJECTES2022\\FGM\\poterna_montesa` → `montesa` dins d'un pare `diablo-wasi`), la capa es
  queda amb la seua i `serie_match` acaba amb `| pare: diablo-wasi`: és el senyal que la carpeta
  pare agrupa diversos llocs. `recuperacio` va a `varis` / `2025_recuperacio-raid` (model §11).
- `proposed_year`: any del nom de la carpeta (`2024-covetes`, `25-05-28_...`, `DW23`, `TFM26`),
  si no, del nom d'un contenidor ancestre (`PROJ26`, `FOT-VID25`, `PROJECTES2022`), si no, el
  mínim `modified_utc` dels fitxers ORIG; si no n'hi ha, dels PROJ; si no, dels PROD i DOC; en
  últim terme, de qualsevol fitxer.
- `proposed_project`: `AAAA_descriptor`, amb el descriptor tret del nom de la carpeta: minúscules,
  sense accents, `-` com a separador, sense el prefix de data ni l'any ni el codi de la sèrie
  (`CHACHA26` → `2026_chacha`, `DW23_TFM26` → `2023_tfm26`), 2–30 caràcters. Si no queda res,
  el nom sencer normalitzat; sense any, `AAAA_`.
- `proposed_layer` i `layer_confidence`: classe dominant (> 90 % dels bytes) per regla (ORIG →
  RAW; PROJ i PROC → PROJ; PROD → OUT; DOC → DOC; SOFT, IMGDISC, SYS i CODI → FORA), confiança
  `regla`; si no, paraules de capa del nom de la carpeta, confiança `nom`; si no, `ambigu`. Es
  calcula per a `projecte` i `capa`; en un projecte indica que la carpeta sencera és homogènia.
- `canonical_proposed`: entre els candidats `projecte` amb la mateixa sèrie i el mateix projecte
  proposat, es formen grups pels discs amb contingut compartit; dins de cada grup guanya el més
  complet (fitxers, després bytes) → disc sense errors de lectura → NTFS → data mínima més antiga
  → disc amb salut `Healthy`. Els candidats sense cap còpia són canònics. Les capes copien la
  del seu projecte.
- `generated_scan_set`: JSON amb l'eina i l'últim `scan_id` acabat de cada disc regenerat;
  `generated_utc`, el moment.
- Enllaç amb el comprovador de rutes (v11), per a cada candidat i sumant els `.psx` vius que té
  a sota: `psx_total`, `psx_checked` (amb fila a `psx_check`), `psx_absorbed` (absorbits per un
  altre `.psx`), `psx_orphans` (totes les càmeres orfes, o il·legibles), `files_bytes` (bytes
  de les `.files` dels `.psx` comprovats, segons l'inventari) i `psx_unchecked` (sense
  comprovar: el disc no estava connectat quan va córrer el comprovador). Al CSV: `psx_total,
  psx_absorbits, psx_orfes, gb_files, psx_sense_comprovar`. Cal passar
  `tools/rutes_metashape.py` abans del catàleg perquè tinguen valor.

**Eixida.** La taula `candidates` i `data/cataleg_AAAA-MM-DD.csv`: UTF-8 amb BOM, separador `;`,
decimals amb coma, una fila per candidat (també residu, sistema i recuperació), ordenada per disc
i ruta. Columnes generades: `candidate_id, disc, serie_disc` (número de sèrie, o serial del
volum), `ruta, tipus, pare, fitxers, bytes, gb, bytes_unics, pct_unics, gb_orig … gb_altres,
data_min, data_max, marcadors, serie_proposada, serie_match, projecte_proposat, any_proposat,
capa_proposada, confianca_capa, discs_mateix_contingut, canonica_proposada`; columnes de revisió
buides: `rev_serie, rev_projecte, rev_capa, rev_any, rev_decisio, rev_fusionar_amb, rev_canonica,
rev_primari, rev_excepcions, rev_notes` (les que importarà `--import` a `candidate_review`).

El resum per pantalla dona els candidats per tipus, els `.psx` dels candidats de primer nivell
(comprovats, absorbits, orfes, sense comprovar i TB de `.files`), amb els bytes dels de primer nivell (les
capes són part del seu projecte, per no comptar dues vegades), el repartiment per confiança de
capa, les sèries proposades amb bytes i el percentatge de bytes de projecte amb `serie_match`,
i els 20 candidats més grans que han caigut a `varis` (la llista per ampliar el vocabulari).
La línia «amb serie_match» desglossa el percentatge de bytes de projecte trobats per la ruta i
pel contingut (els `suggerit:` no hi compten), i el percentatge sense comptar els `varis` de capa `FORA` (imatges de disc,
programari), que no són material d'arxiu.

## Revisió del catàleg (pas B) i construcció de projectes

### Omplir el CSV de revisió

1. Obriu `data/cataleg_AAAA-MM-DD.csv` amb LibreOffice Calc. Al diàleg d'importació: joc de
   caràcters **Unicode (UTF-8)**, separador **només punt i coma** (desmarqueu coma, tabulador i
   espai), delimitador de text `"`, i marqueu **«Format de camp entre cometes com a text»**;
   deixeu la detecció de números activada (les columnes `gb_*` porten coma decimal). No toqueu
   les columnes `disc` i `ruta`: són la clau.
2. Ompliu només les columnes `rev_*` de les files que decidiu. Valors admesos:
   - `rev_serie`: un nom del vocabulari (`diablo-wasi`, `covetes`…) o `varis`; buit = acceptar
     `serie_proposada`.
   - `rev_projecte`: `AAAA_descriptor` en minúscules (`2023_registre`); buit = acceptar
     `projecte_proposat` (només si ja és vàlid: els `AAAA_…` sense any no passen).
   - `rev_capa`: `RAW`, `PROJ`, `OUT` o `DOC` si el candidat és només una capa d'un projecte.
   - `rev_any`: quatre dígits; buit = `any_proposat` o l'any del nom del projecte.
   - `rev_decisio`: `tancar`, `obert`, `descartar`, `fusionar-amb`, `tercers` o `personal`.
   - `rev_fusionar_amb`: el candidat destí com a `DISC:ruta` (o només `ruta` si és del mateix
     disc), copiat de les columnes `disc` i `ruta` de la seua fila.
   - `rev_canonica`: `0` o `1`; `rev_primari`: `primari` o `secundari`; `rev_excepcions` i
     `rev_notes`: text lliure.
3. Deseu amb **Fitxer → Anomena i desa**, format **Text CSV**, marqueu «Edita la configuració
   del filtre» i confirmeu: UTF-8, separador `;`, delimitador `"`, «Desa el contingut de les
   cel·les tal com es mostra». El BOM és indiferent (l'importador l'accepta amb o sense). Si
   Calc demana «Mantén el format actual», digueu que sí. Deseu-lo com a
   `data/cataleg_revisat.csv` (ignorat per git).

### Importar i construir

```bash
python tools/cataleg.py --import data/cataleg_revisat.csv
python tools/cataleg.py --build-projects
```

`--import` llig només les files amb alguna columna `rev_*` plena, les valida totes abans
d'escriure res (candidat existent a `candidates` per `(disc, ruta)`, sèrie al vocabulari o
`varis`, nom de projecte amb `^\d{4}_[a-z0-9]+(-[a-z0-9]+)*$`, capa, any, decisió, destí de
`fusionar-amb` existent, canònica 0/1, primari/secundari) i, si hi ha cap error, escriu
`data/cataleg_errors.csv` (fila, disc, ruta, columna, valor, error) i no importa res. Si tot és
correcte, omple `candidate_review` amb clau `(disc, ruta)` (una reimportació substitueix la fila
d'aquell candidat; les files no presents al CSV es conserven). Les columnes generades del CSV
s'ignoren: podeu reimportar un CSV generat més tard sense perdre res.

`--build-projects` llig `candidate_review` i, sense tocar cap disc, crea o actualitza `series`
(codi i tipus del vocabulari), `projects` (any; estat `en-tancament` si la decisió és `tancar`,
`obert` si no; `primary_model`, `exceptions`; `raw_style = llegat`) i `project_sources` (el
candidat, la seua `rev_capa` i `rev_canonica`). Sèrie, projecte i any són els revisats o, si són
buits, els proposats. `descartar`, `tercers` i `personal` no fan projecte; `fusionar-amb` assigna
el candidat al projecte del destí (seguint la cadena, fins a cinc salts). El resum diu sèries i
projectes creats, candidats assignats i revisats sense projecte (amb el motiu); es pot repetir
tantes vegades com calga.

## Comprovador de rutes dels projectes Metashape (`tools/rutes_metashape.py`)

### Estructura real d'un `.psx` (inspecció del 14/10/2026)

Comprovada en mode lectura (prefix `\\?\`) sobre els 34 `.psx` dels discs connectats (PC-C i
PC-D: `METASH`, `PROJ-2024-ESTIU`, `ARQ_UA`…), tots guardats per Metashape 2.0 a 2.2 amb format de
document `1.2.0`. Els projectes PhotoScan antics (p. ex. `INT-7\PROJECTES2020\photoscan_projects`)
no s'han pogut obrir encara: el comprovador registra la versió i avisa si l'estructura difereix.

```
<nom>.psx                       XML de 106 bytes:
                                <document version="1.2.0" path="{projectname}.files/project.zip"/>
<nom>.files\                    {projectname} = nom del .psx sense extensió; sempre al costat del .psx
  project.zip  → doc.xml        <document version="1.2.0">
                                  <chunks next_id active_id> <chunk id="0" path="0/chunk.zip"/> …
                                  <meta> Info/OriginalSoftwareName (Agisoft Metashape),
                                         Info/OriginalSoftwareVersion, Info/LastSavedSoftwareVersion
                                         (p. ex. 2.1.1.17748), Info/LastSavedDateTime,
                                         Project/AbsolutePaths (false a tots els 34)
  <chunk>\chunk.zip → doc.xml   <chunk version label enabled> amb sensors, components, cameras
                                  (<camera id sensor_id label="DSC04946"> + transform; sense ruta),
                                  depth_map_sets, models, frames (<frame id="0" path="0/frame.zip"/>),
                                  reference, region, settings, meta
  <chunk>\<frame>\frame.zip     <frame version> amb cameras, thumbnails, point_cloud, depth_maps, model:
               → doc.xml          <camera camera_id="0">
                                    <photo path="../../../sony/DSC04946.JPG">
                                      <meta> Exif/DateTimeOriginal, Exif/Model, File/ImageWidth…,
                                             System/FileModifyDate, System/FileSize (bytes) </meta>
  <chunk>\<frame>\{depth_maps, model, orthomosaic, point_cloud, shapes, thumbnails}\…
```

- **On viuen les rutes de les fotos**: a `frame.zip` → `doc.xml`, un `<photo path>` per càmera.
  `chunk.zip` només té l'etiqueta de la càmera (el nom del fitxer sense extensió).
- **Respecte a què són relatives**: a la carpeta del frame (`<nom>.files\<chunk>\<frame>\`),
  amb `/` com a separador. Per tant `../../../` és la carpeta que conté el `.psx`,
  `../../../../` la de dalt, i s'han vist fins a `../../../../../../` (sis nivells:
  `2024-covetes-sectorH.psx` apunta a `TEMPORAL_COVETES\…` dos nivells per damunt del projecte).
- **Com es marquen les absolutes**: lletra d'unitat i `:/` (`F:/2024-Covetes/RAW/…`,
  `K:/SERIES/METASHAPE/…`, `Z:/Foto-Video/PEP_carmelites/…`, `C:/Users/elmor/Documents/…`),
  encara que `Project/AbsolutePaths` siga `false` (el paràmetre és la preferència en desar; les
  fotos que no són a la mateixa unitat que el projecte es guarden absolutes). No s'ha vist cap
  ruta UNC (`//servidor/…`). Un mateix projecte pot barrejar-ne (`sectorH`: 8.893 relatives i
  5.990 absolutes). 13 dels 28 `.psx` de PC-D apunten a `F:` o `K:` (discs no connectats).
- **Versió de l'aplicació**: `Info/LastSavedSoftwareVersion` i `Info/OriginalSoftwareVersion` al
  `<meta>` de `project.zip`; l'atribut `version` del `.psx` és la versió del format (1.2.0).
  Els projectes PhotoScan 1.6 i 1.7 (INT-7) tenen el mateix format i la mateixa estructura de
  chunks i frames, però 64 `.psx` antics no porten cap propietat `Info/*` i queden sense versió
  d'aplicació; 26 més no tenen `project.zip` (la `.files` és buida o incompleta).
- **Mida de cada foto**: `System/FileSize` al `<meta>` de cada `<photo>` (present a les 100 %
  de les 1,2 M de càmeres inspeccionades): permet resoldre per nom + mida sense obrir cap foto.
- **Projectes incomplets**: `.files` sense `project.zip` (`metashape\2024-covetes-tot2.psx`) i
  chunks absents (`metashape\2024-covetes-tot.psx`: 20 de 32 `chunk.zip` no hi són). El
  comprovador ho anota a `notes` i continua.
- Un chunk pot tenir diversos frames (sistemes multicàmera); als 34 projectes n'hi ha un per
  chunk. Les càmeres del frame són les fotos: el total de càmeres d'un `.psx` és la suma de tots
  els frames de tots els chunks.

### Ús

```bash
python tools/rutes_metashape.py                       # tots els .psx dels discs connectats
python tools/rutes_metashape.py --drive INT-5         # un disc (repetible)
python tools/rutes_metashape.py --drive INT-5 --path DW23_TFM26\METASHAPE23
python tools/rutes_metashape.py --selection INT-5 "DW23_TFM26\METASHAPE23\FGM-GLOBAL2024.psx"
```

Només lectura dels discs (prefix `\\?\`, mai obri cap foto ni Metashape) i de l'inventari;
escriu només la taula `psx_check` (una fila per `.psx`, reescrita a cada execució) i el CSV.
Un disc es considera connectat si `drives.last_root_path` existeix i el serial del volum
coincideix amb el registrat; els altres se salten i el resum els llista amb els seus `.psx`.

**Resolució de cada foto** contra l'inventari (fitxers vius):

| Resolució | Com | Columna |
|---|---|---|
| `same` | ruta relativa (des de la carpeta del frame) o absoluta amb la lletra de l'arrel del mateix disc, trobada al mateix disc per l'índex únic (disc, ruta) | `resolved_same` |
| `same-mogut` | no és a la ruta guardada, però el mateix nom (sense majúscules) i mida és al mateix disc en una altra carpeta | `resolved_same` |
| `altre` | ruta absoluta d'un altre disc (per la lletra de la seua arrel registrada) que hi existeix | `resolved_other_drive` |
| `altre-nom-mida` | el mateix nom i mida en un altre disc (índex per mida, nom comparat sense majúscules) | `resolved_other_drive` |
| `orfe` | enlloc (o sense `System/FileSize` i ruta no trobada) | `orphans` |

Les lletres d'unitat de les rutes absolutes només són fiables per al disc del mateix `.psx`:
les altres han canviat amb el temps, per això la prova de fons és nom + mida. Una ruta relativa
que puja per damunt de l'arrel del disc es resol també per nom + mida.

**Eixida.** `psx_check`: `app_version` (`Info/LastSavedSoftwareVersion`), `chunks`, `cameras`,
`resolved_same`, `resolved_other_drive`, `orphans`, `photos_json` (llista per càmera:
`[ruta tal com la guarda el .psx, resolució, disc, ruta trobada, mida]`) i `notes` (JSON:
versió del format, aplicació original, frames, recompte per resolució, discs implicats, fitxers i
bytes de la `.files` a l'inventari, incidències com «falta project.zip» o chunks absents).
`data/rutes_metashape_AAAA-MM-DD.csv` (UTF-8 amb BOM, `;`): una fila per `.psx` amb les mateixes
xifres més `chunks_repetits`, `absorbit` i `absorbit_per`. Resum: `.psx` resolts del tot,
parcials, orfes del tot, càmeres per resolució, chunks amb cada producte, els 20 amb més orfes,
chunks repetits i `.psx` absorbits, i els discs saltats.

### Chunks: càmeres, alineació i productes (`psx_check.chunks_json`)

De cada chunk es desa `{id, etiqueta, activat, cameres, alineades, fotos, models, productes}` i,
si el chunk és repetit, `repetit_a` (els altres `.psx` on és) i `dominat_per` (els que el tenen
amb igual o més processament). Elements usats, comprovats sobre 1.082 chunks de 107 projectes:

| Dada | On | Element |
|---|---|---|
| etiqueta, activat | `chunk.zip` → `doc.xml` | `<chunk label enabled>` |
| càmeres | `chunk.zip` | `<cameras><camera>` i també `<cameras><group><camera>` (220 chunks només tenen càmeres dins de grups) |
| alineades | `chunk.zip` | càmeres amb fill `<transform>` (les no alineades no en tenen) |
| punts d'enllaç | `frame.zip` → `doc.xml` | `<point_cloud path>` (a Metashape 2.x el «point cloud» del frame són els punts d'enllaç; el dens és `dense_cloud`) |
| mapes de profunditat | `chunk.zip` | `<depth_map_sets><depth_maps>` |
| núvol dens | `chunk.zip` | `<dense_clouds><dense_cloud>` |
| malla | `chunk.zip` | `<models><model>` (`models` = quants n'hi ha) |
| textura | `model.zip` → `doc.xml` de cada model del frame | `<texture path>` (format antic) o `<textures><texture>` (nou); `<mesh><hasUV>` no serveix perquè sempre hi és |
| ortofoto | `chunk.zip` | `<orthomosaics><orthomosaic>` |
| MDT | `chunk.zip` | `<elevations><elevation>` |
| model en tessel·les | `chunk.zip` | `<tiled_models><tiled_model>` |

Les fotos del chunk ixen dels `<photo path>` de tots els seus frames.

### Chunks repetits i `.psx` absorbits (`data/chunks_repetits_AAAA-MM-DD.csv`)

Dos chunks són «el mateix» si tenen la mateixa etiqueta i el mateix conjunt de noms de foto
(nom de fitxer, sense carpeta ni majúscules). L'informe llista els grups presents en més d'un
`.psx`, una fila per chunk i `.psx`: `grup, etiqueta, cameres, n_psx, disc, ruta_psx, chunk_id,
alineades`, un 0/1 per producte, `processament` (quants productes), `maxim_del_grup` (té el
màxim processament del grup), `psx_absorbit` i `absorbit_per`.

L'anàlisi inclou els `.psx` de discs no connectats comprovats en passis anteriors: l'empremta
del conjunt de fotos de cada chunk (`fotos_sha1`) es desa a `chunks_json`, així els grups es
formen amb tots els `.psx` coneguts i les marques d'absorció es refresquen també als antics
(els d'un passi anterior a aquesta versió, sense empremta, queden fora fins que es tornen a
comprovar). `data/files_absorbits_AAAA-MM-DD.csv` dona, per `.psx`, la categoria (`absorbit`;
`maxim` = no absorbit amb algun chunk màxim del seu grup; `orfe-illegible`; `altre`) i la mida
de la `.files` segons l'inventari; el resum suma els TB per categoria.

Un chunk està **dominat** per un altre del mateix grup si aquest té tantes o més càmeres
alineades i tots els productes del primer; en empat exacte, domina el del `.psx` amb més chunks
(el «final») i, si també empaten, el primer per disc i ruta, perquè de dues còpies idèntiques
només una compte com a absorbida. Un `.psx` queda **absorbit** quan tots els seus chunks amb
fotos estan dominats per chunks d'altres `.psx`; `absorbit_per` és la unió d'aquests. És la
lectura del model §3.2 sense obrir Metashape: el final es conserva, els solts absorbits poden
anar a quarantena, i els solts amb més processament que el final (fila amb `maxim_del_grup = 1`
i `psx_absorbit = 0`) són els que cal mirar un a un.

`--selection DISC RUTA` no toca la DB: comprova aquell `.psx` i escriu
`data/seleccio_<nom>.txt` (o `--out`), una línia per foto resolta sense repetir:
ruta relativa a la carpeta del `.psx` amb `/` (`../RAW/x.JPG`; `DISC:ruta` si és en un altre
disc), mida i hash SHA-256 si l'inventari el té (tabuladors). És el `RAW\seleccio.txt` del model
§2.2, pendent de revisar les fotos d'altres discs.

## Verificació feta (14/10/2026)

- `pyflakes app tools` net (llevat dels avisos preexistents).
- `python -m app.migrate` sobre una DB de banc v8 (4 discs reals, 512 k fitxers): 0,5 s,
  `quick_check` ok; `init_schema` crea directament la v9.
- Catàleg sobre el banc i sobre una còpia de la DB real (API backup, 26 s; migració 10 s)
  amb traça de totes les sentències SQL: només `UPDATE inventory SET analysis_class` i
  `DELETE`/`INSERT` a `candidates`; signatures de totes les altres taules idèntiques abans i
  després; CSV amb una fila per candidat i BOM; regenerar un sol disc conserva els
  `candidate_id` i no toca els altres discs.
- Comprovador de rutes sobre els 652 `.psx` dels set discs connectats (EXT-B, INT-3, INT-5,
  INT-7, MAC-INESIA, PC-C, PC-D): 197 s, 2,15 M de càmeres i 3.318 chunks; 608 `.psx` resolts
  del tot, 6 parcials, 11 orfes del tot (PhotoScan 2016-2020 d'INT-7), 27 sense càmeres o
  il·legibles; 581 grups de chunks repetits i 407 `.psx` absorbits. Només escriu `psx_check`.
- DB real (22 discs, 7,0 M de fitxers), amb còpia prèvia a `BACKUP_DB\arxiu_discs_v8_2026-10-14.db`:
  migració 91 s (la major part l'`ANALYZE`), `quick_check` ok; catàleg 92 s (classificació
  inclosa), 2.328 candidats (530 projectes de primer nivell, 1.730 capes, 22 residus, 4 de
  recuperació, 42 de sistema), 55,2 TB; sèrie proposada amb `serie_match` per al 78,6 % dels
  bytes de projecte (66,8 % per la ruta, 11,8 % pel contingut; 86,6 % sense els `varis` de
  capa FORA). El criteri del 90 % de l'especificació no s'assoleix encara: els 20 `varis` més
  grans del resum són la llista per ampliar el vocabulari o decidir a la revisió.
