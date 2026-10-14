# Migració a l'arxiu: catàleg de projectes (pas A)

Eines que implementen el model d'arxiu v1.3 (`docs/model-arxiu.md`) seguint l'especificació de
`docs/eines-migracio.md`. Aquesta pàgina documenta el que ja existeix: l'esquema v9, les classes
d'anàlisi, el vocabulari i el catàleg (`tools/cataleg.py`). El comprovador de rutes de Metashape,
la importació de la revisió (`--import`, `--build-projects`) i l'executor vindran després.

## Esquema v9

`python -m app.migrate` afegeix, sense tocar cap taula existent:

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
| `serie_from_content` | `true` (per defecte) o `false`: si la ruta del candidat no conté cap paraula clau, buscar-ne als noms de les seues subcarpetes (3 nivells) i dels seus fitxers de projecte (vegeu el pas 3). |
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
   candidats. L'arrel del disc actua sempre com a contenidor del nivell 1.
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

- `proposed_serie` i `serie_match`: primera sèrie del vocabulari, en ordre de precedència
  (lloc → programa → client → tipus), amb una paraula clau a la ruta del candidat; `serie_match`
  diu la paraula (i el token, si no és idèntic: `dw (dw23)`). Si la ruta no diu res i
  `serie_from_content` és actiu, cada subcarpeta (fins a 3 nivells) i cada fitxer de projecte
  del candidat vota per la sèrie del seu nom amb la mateixa regla; guanya la que té més de la
  meitat dels vots, i `serie_match` ho marca com a `dins: kuelap 50/50` (paraula, vots a favor /
  vots totals) per poder-ho auditar: és una pista més feble (una carpeta `METASHAPE` amb
  projectes de diversos llocs pot caure al lloc majoritari). Sense cap coincidència, `varis` amb
  `serie_match` buit. Les capes hereten sèrie, projecte i any del pare; `recuperacio` va a
  `varis` / `2025_recuperacio-raid` (model §11).
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

**Eixida.** La taula `candidates` i `data/cataleg_AAAA-MM-DD.csv`: UTF-8 amb BOM, separador `;`,
decimals amb coma, una fila per candidat (també residu, sistema i recuperació), ordenada per disc
i ruta. Columnes generades: `candidate_id, disc, serie_disc` (número de sèrie, o serial del
volum), `ruta, tipus, pare, fitxers, bytes, gb, bytes_unics, pct_unics, gb_orig … gb_altres,
data_min, data_max, marcadors, serie_proposada, serie_match, projecte_proposat, any_proposat,
capa_proposada, confianca_capa, discs_mateix_contingut, canonica_proposada`; columnes de revisió
buides: `rev_serie, rev_projecte, rev_capa, rev_any, rev_decisio, rev_fusionar_amb, rev_canonica,
rev_primari, rev_excepcions, rev_notes` (les que importarà `--import` a `candidate_review`).

El resum per pantalla dona els candidats per tipus amb els bytes dels de primer nivell (les
capes són part del seu projecte, per no comptar dues vegades), el repartiment per confiança de
capa, les sèries proposades amb bytes i el percentatge de bytes de projecte amb `serie_match`,
i els 20 candidats més grans que han caigut a `varis` (la llista per ampliar el vocabulari).
La línia «amb serie_match» desglossa el percentatge de bytes de projecte trobats per la ruta i
pel contingut, i el percentatge sense comptar els `varis` de capa `FORA` (imatges de disc,
programari), que no són material d'arxiu.

## Verificació feta (14/10/2026)

- `pyflakes app tools` net (llevat dels avisos preexistents).
- `python -m app.migrate` sobre una DB de banc v8 (4 discs reals, 512 k fitxers): 0,5 s,
  `quick_check` ok; `init_schema` crea directament la v9.
- Catàleg sobre el banc i sobre una còpia de la DB real (API backup, 26 s; migració 10 s)
  amb traça de totes les sentències SQL: només `UPDATE inventory SET analysis_class` i
  `DELETE`/`INSERT` a `candidates`; signatures de totes les altres taules idèntiques abans i
  després; CSV amb una fila per candidat i BOM; regenerar un sol disc conserva els
  `candidate_id` i no toca els altres discs.
- DB real (22 discs, 7,0 M de fitxers), amb còpia prèvia a `BACKUP_DB\arxiu_discs_v8_2026-10-14.db`:
  migració 91 s (la major part l'`ANALYZE`), `quick_check` ok; catàleg 92 s (classificació
  inclosa), 2.328 candidats (530 projectes de primer nivell, 1.730 capes, 22 residus, 4 de
  recuperació, 42 de sistema), 55,2 TB; sèrie proposada amb `serie_match` per al 78,6 % dels
  bytes de projecte (66,8 % per la ruta, 11,8 % pel contingut; 86,6 % sense els `varis` de
  capa FORA). El criteri del 90 % de l'especificació no s'assoleix encara: els 20 `varis` més
  grans del resum són la llista per ampliar el vocabulari o decidir a la revisió.
