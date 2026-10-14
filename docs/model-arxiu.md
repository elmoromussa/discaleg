# Model d'arxiu digital de Panograma Labs — v1.4 (14/10/2026)

Document de decisions. Fixa què és l'arxiu, com s'ordena, què es conserva i com es protegeix. Es fonamenta en l'anàlisi arxivística (`claude/analisi-arxivistica-2026-10-14.md`), en l'informe de bones pràctiques (`claude/bones-practiques-arxiu-digital-2026-10-14.md`) i en la revisió independent de la v1.2 (`claude/revisio-model-2026-10-14.md`), que ha motivat aquesta versió. On una regla ve d'una font, s'indica entre claudàtors. Tot el que ací és regla ha de poder-se codificar a les eines (catàleg, comprovador de rutes, executor); el que necessita criteri humà es diu explícitament i es registra.

> **Canvis v1.4**: el pas 0 es fa amb EXT-E (reformatat) i INT-3, sense comprar disc; la compra del CMR de 16 TB es posposa fins després del catàleg i es condiciona a un llindar de preu (mercat de 2026 amb escassetat: 16 TB nou a 680 € el 14/10); mentre no arribe, Yottamaster A = 4 BarraCuda (dades) + Exos (paritat, al final); la paritat no és essencial (ho és la segona còpia), però l'Exos és l'únic disc que pot fer-la.
> **Canvis v1.3** (25 punts de la revisió): pas 0 i disc de 16 TB; README immutable; abast de SnapRAID; sèries grans en discs grans; versions a quarantena; imatges processades referenciades pel `.psx`; any dels projectes assemblats; precedència de sèrie i casos especials; regla de còpia canònica; definicions per a les eines; nomenclatura com a regla verificable; plantilles de README i SERIE.md; especificació del manifest; procediment ACL; estats de suport, agents i excepcions; glossari; tancament per tipus; tercera còpia; renovació de suports; 360 i webs; dispositius i dates; projectes PhotoScan; kit de recuperació; robocopy.
> **v1.2**: organització de RAW. **v1.1**: la sèrie com a nivell propi.

## Glossari (una accepció per terme)

- **Fons**: tot l'arxiu de Panograma Labs (`ARXIU\`).
- **Sèrie**: carpeta estable d'un jaciment o programa, que acumula projectes (`ARXIU\diablo-wasi\`).
- **Projecte**: la unitat d'arxiu: una campanya, encàrrec o treball, amb carpeta pròpia dins d'una sèrie. És la unitat de tancament, hash, README i còpies.
- **Capa de projecte**: una de les quatre carpetes fixes d'un projecte: RAW, PROJ, OUT, DOC.
- **Classe d'anàlisi**: la categoria que `capes.py` dona a cada fitxer de l'inventari actual (ORIG, IMG, VID, PROC, PROJ, PROD, DOC, SOFT, IMGDISC, SYS, CODI, COMP, ALTRES). Serveix per a proposar la capa de projecte; no és una capa.
- **Nivell de suport**: treball / arxiu canònic / segona còpia / tercera còpia.
- **Còpia canònica**: entre diverses còpies d'un mateix contingut a l'inventari actual, la que es tria com a origen de la migració (regla a §10).
- **Arxiu canònic**: el nivell de suport on viu la còpia principal de l'arxiu (Yottamaster A).
- **Còpia**: el mateix contingut en un suport físic diferent, verificat per hash. Paritat, RAID i còpies dins del mateix disc no ho són. [DPC; APTrust 2026]
- **Família**: agrupació de treball usada a l'anàlisi i a la migració (cicle per família). Coincideix normalment amb la sèrie; no és etiqueta permanent.
- **Candidat**: a l'inventari actual, una carpeta que el catàleg proposa com a projecte o com a capa d'un projecte (definició a §10).
- **Estat**: d'un projecte (obert, en-tancament, tancat, descartat) o d'un suport (en-reorganització, tancat, obert).
- **Agent**: qui fa una acció registrada: `esteve`, o un script amb nom i versió (`discaleg-executor 0.3`).

## 0. Principis

1. **Una ordenació física, moltes lectures.** Cada fitxer viu en un sol lloc; les vistes per lloc, client, any o tipus són espais virtuals i etiquetes a discaleg. [RiC-CM 1.0 RiC-E03; ISAD(G)]
2. **Captura, procés i resultat van separats.** [OAIS; ADS; TIER]
3. **Tancat vol dir immutable i verificat.** Hash de tots els fitxers, dues còpies, només lectura. Qualsevol canvi posterior és un esdeveniment registrat. [OAIS AIP; NDSA Integrity]
4. **Les còpies es compten amb honestedat** (vegeu glossari).
5. **La veritat està al disc, no a l'app.** README, SERIE.md i manifest viuen amb les dades; discaleg els indexa i es pot reconstruir del disc.
6. **Cap suport d'arxiu depén d'una carcassa ni d'un programari.** NTFS amb sector lògic 512e comprovat, discs llegibles sols en qualsevol PC (en macOS, només lectura).
7. **Verificació automàtica, rellegint.** SHA-256 calculat a les dues bandes per un procés separat del que copia. [NDSA Integrity L2]
8. **Res s'esborra ni es mou a la paperera sense còpia verificada; tot esborrat passa per `_QUARANTENA` amb registre.** Inclou les versions intermèdies de projecte.
9. **Suficiència.** Objectiu: NDSA Nivell 2 complet, Nivell 3 en integritat i metadades. El Nivell 4 no és objectiu. [NDSA V2.1]
10. **Els suports s'identifiquen per número de sèrie.**
11. **Tota excepció a una regla es registra** (README del projecte, esdeveniment a discaleg, columna del catàleg) i la decideix `esteve`.

## 1. Dos arxius i les carpetes del fons

| Arxiu | Contingut | Règim | Còpies |
|---|---|---|---|
| **ARXIU** (Panograma Labs) | sèries de lloc o programa; sèries de tipus `docencia`, `fototeca`, `publicacions`, `varis`; carpetes de fons `_EINES`, `_GIS-BASE`, `_TERCERS`, `_KIT`, `_QUARANTENA` | preservació: hash, estats, README, auditoria | RAW, OUT, DOC: 3 (canònic + segona + tercera al final); PROJ: 2; `docencia`, `fototeca`, `_TERCERS`, `_EINES`, `_GIS-BASE`: 2 |
| **PERSONAL** | família, mòbils, música, sèries, jocs, pel·lícules, programari personal | còpia de seguretat: inventariat a discaleg, sense estats ni README | 2 (disc propi + extern) |

Carpetes del fons (no són sèries):
- `_EINES\`: instal·ladors de versions concretes de programari professional (Metashape, Pano2VR, drivers), amb versió al nom. Criteri: s'ha usat per a produir un OUT de l'arxiu → `_EINES`; programari personal (jocs, programes antics sense ús professional) → PERSONAL.
- `_GIS-BASE\`: cartografia de referència compartida (capes oficials, MDT, ortofotos PNOA), per font i any (`_GIS-BASE\icv-2023\`). Un `.qgz` hi apunta amb ruta relativa; `_GIS-BASE` viu replicada a **tots** els discs de l'arxiu canònic perquè la ruta resolga.
- `_TERCERS\<persona>\`: material que no és registre de Panograma (seguretat_josep, escritori_josep, drive_muyuna): es conserva tal com està, amb un README que diu de qui és, quan i per què; propietari `tercer`; dues còpies; sense tancament ni aprimat; es retorna o s'esborra quan el propietari ho diga.
- `_KIT\`: kit de recuperació (§7.6).
- `_QUARANTENA\`: §9.

PERSONAL viu en un disc físic propi fora de la Yottamaster. El que no és res d'això (imatges de disc, dependències de codi, caches, paperera, descàrregues) no entra a l'arxiu.

## 2. Nivells, sèrie i projecte

| Nivell ISAD(G) | Ací | Exemple | Es tanca? |
|---|---|---|---|
| Fons | ARXIU | `ARXIU\` | no |
| Sèrie | jaciment o programa | `ARXIU\diablo-wasi\` | no (accruals); estat activa / inactiva |
| Projecte | campanya, encàrrec o treball | `ARXIU\diablo-wasi\2023_registre\` | sí |
| Fitxer | ítem | `RAW\...\DSC01234.ARW` | — |

[ISAD(G) §2; ADS «specific site or feature *or* separate instances of fieldwork»; SND; CASRAI «stable elements first»]

### 2.1 Sèrie

Carpeta de nom estable, sense any, minúscules i guions: `diablo-wasi`, `sanxo-llop`, `covetes`, `alcavors`, `carmelites-gandia`. Conté `SERIE.md` (plantilla a §3.4) i els projectes.

**Precedència per a assignar sèrie** (la primera que aplique):
1. **Jaciment o lloc** amb nom estable (`diablo-wasi`, `sanxo-llop`).
2. **Programa o línia de treball** amb nom estable que abasta diversos llocs (`pep` per a Torre Benasau i Fontanars si es van fer dins del mateix programa). `chacha-xr` **no** és sèrie: és un projecte dins de `diablo-wasi`.
3. **Client**, només si no hi ha lloc ni programa estables (`minutera`, encàrrecs menuts repetits).
4. **Sèrie de tipus**: `docencia` (unitat = curs o assignatura, `2025_cursos-inesia-25-26`), `fototeca` (fotografia pròpia no lligada a cap treball; unitat = sessió, `2019_viatge-chachapoyas`), `publicacions` (articles, ponències, llibres que no són d'un sol jaciment).
5. `varis`: el que no encaixa en cap; cada projecte amb README que ho justifica.

Una captura feta per a un treball va a la sèrie del treball, encara que hi haja fotos personals (es trien i passen a PERSONAL al pas B). Una publicació sobre un sol jaciment és un projecte de la sèrie del jaciment amb `tipus=publicacio`; sobre diversos, de `publicacions`, i referencia les sèries. El codi propi (Oculat, discaleg, territori) viu a git; a l'arxiu només les dades de prova i les versions lliurades, com a projecte de `varis` o del jaciment per al qual es va fer, amb `tipus=codi`.

**Una sèrie viu sencera en un disc** de l'arxiu canònic i en un disc de la segona còpia; l'assignació la fa `esteve` al catàleg i es registra a `SERIE.md`. Les sèries grans van als discs grans (§7). Si una sèrie amenaça de superar el seu disc, es trasllada sencera a un disc més gran; mai es parteix.

### 2.2 Projecte

Un encàrrec, campanya o treball amb inici, final i productes. Cada campanya d'un jaciment és un projecte; un treball que beu de diverses campanyes (`2024_model-global`, `2022_chacha-xr`) també, dins de la mateixa sèrie. Un `.psx` no és mai un projecte: és un fitxer de PROJ, i n'hi pot haver desenes.

**Nom**: `AAAA_descriptor`, validable amb `^\d{4}_[a-z0-9]+(-[a-z0-9]+)*$`, descriptor de 2 a 30 caràcters que diu el treball (`2023_registre`, `2024_dron`, `2024_model-global`, `2025_maquinal`, `2024_documentacio-general`). El codi de sèrie **no** va al nom del projecte (ja és al camí i a `SERIE.md`); els codis que ja existeixen als noms de fitxer (`SLL1_003.psx`) es conserven.

**Any** = any d'inici del treball, decidit per `esteve` al catàleg. Valor proposat per l'eina: any de la captura més antiga del RAW propi; si el projecte no té RAW propi (referencia captures), any del fitxer de PROJ més antic; si només té OUT, any del producte més antic.

**Referències a captures d'altres projectes**: no es copien. El projecte té a `RAW\seleccio.txt` la llista (ruta relativa des de la carpeta del projecte, mida, SHA-256) i el `.psx` apunta amb ruta relativa (`..\..\2023_registre\RAW\...`). Condició: **origen i destí al mateix disc**, garantida dins d'una sèrie. Entre sèries diferents només si les dues són al mateix disc (ho verifica el comprovador); si no, les fotos seleccionades es copien dins del projecte com a excepció registrada. discaleg guarda la relació «usa captures de».

**Projectes menuts**: un treball d'un dia és un projecte igualment, amb README de cinc línies. Encàrrecs repetits d'un client en un any poden ser un sol projecte, amb una carpeta de dia per encàrrec dins de RAW (`2024-03-05_vilanova`).

## 3. Estructura interna d'un projecte

```
ARXIU/
  diablo-wasi/                      ← sèrie
    SERIE.md
    2021_tambillo/                  ← projecte
    2023_registre/
      README.md
      manifest-sha256.txt
      manifest-mides.txt
      referencies-sha256.txt        ← només si referencia captures d'altres projectes
      RAW/
      PROJ/
      OUT/
      DOC/
    2024_model-global/
      RAW/seleccio.txt
      PROJ/ OUT/ DOC/
```

Cap altra carpeta al primer nivell del projecte. Noms fixos exempts de la regla de minúscules: `README.md`, `SERIE.md`, `RAW`, `PROJ`, `OUT`, `DOC`, `manifest-*.txt`, `referencies-sha256.txt`, `seleccio.txt`, `captura.csv`, i les carpetes del fons amb `_`.

### 3.1 RAW: captura i auxiliars de procedència

RAW conté el que va eixir dels dispositius **més una llista tancada d'auxiliars de procedència**: XMP sidecar (metadades, no edició de l'original), logs de vol, punts de control, fotos de calibratge, `captura.csv` (generat per l'eina d'ingesta abans de segellar) i `seleccio.txt`. Res més: res editat, retallat ni convertit. «Intocable» vol dir que, una vegada segellat, no canvia; abans de segellar, l'eina d'ingesta pot afegir `captura.csv`.

**Imatges processades per a fotogrametria** (TIF o JPG revelats des del RAW que el `.psx` referencia en lloc de l'ARW): es conserven a `RAW\processades\<dia>\<dispositiu>\`, perquè sense elles el `.psx` no s'obri i ARIADNEplus 2023 les inclou («originals i, si n'hi ha, les processades»). Exportacions intermèdies que cap `.psx` referencia són procés i no s'arxiven.

**Organització (ingesta nova)**: sessió de captura → dispositiu. [SND; ADS; Krogh, *The DAM Book*]

```
RAW/
  2023-07-12_sector-nord/
    sony-a7iii/        DSC01234.ARW, DSC01234.JPG, DSC01234.xmp, C0012.MP4
    dji-mavic3/        DJI_0042.JPG, DJI_0042.MP4, DJIFlightRecord_2023-07-12.txt
    insta360-x3/       IMG_20230712_101533.insp, VID_20230712_101540.insv
    captura.csv
  _control/            gcp.csv (amb datum i CRS), gnss-export.txt, croquis-gcp.pdf, una foto per punt
  _calibratge/         fotos del patró, per dispositiu
  processades/         només si un .psx les referencia
```

- Carpeta de dia `AAAA-MM-DD_descriptor`; data = `DateTimeOriginal` de l'EXIF en **hora local del lloc de captura**; si els dispositius van desajustats, mana el dispositiu principal i la diferència s'anota al README.
- Carpeta de dispositiu sempre, amb nom del vocabulari tancat (§6).
- Fitxers amb el nom de càmera, sense reanomenar. Foto i vídeo junts dins del dispositiu; sense carpeta `video`. RAW i JPG de la mateixa foto, junts.
- Gravacions de pantalla, renders, exportacions: no són captura; PROJ o OUT.
- `captura.csv` per carpeta de dia: fitxer, dispositiu, hora, GPS, mida, SHA-256; generat amb exiftool per l'eina d'ingesta.
- Lightroom importa «afegint en el lloc»; el catàleg va a PROJ.

**RAW llegat (migració)**: conserva la seua disposició original, carpeta per carpeta, sense reorganitzar per dia ni per dispositiu: els `.psx` hi apunten, i reordenar milions de fitxers sense valor afegit és risc. `captura.csv` a l'arrel de RAW. El README registra l'equivalència amb la carpeta d'origen. Coexistiran dos estils de RAW; el README ho declara (`raw-estil: nou | llegat`).

**Noms NFD, espais i accents dins de RAW llegat**: no es normalitzen (trencarien els `.psx`); el manifest els escriu tal com són al disc; el README ho anota.

### 3.2 PROJ, OUT, DOC

| Capa | Hi va | No hi va |
|---|---|---|
| **PROJ** | `.psx` final + `.files` aprimada; `.psz` si el projecte és menut; `.lrcat`; `.blend`; `.qgz`; `.p2vr`; `.pts`; `.prproj`; rutes relatives a `../RAW` | versions intermèdies (→ quarantena), `.blend1`, `.lrdata`, `.lrprev`, caches, previews, `.files` sense `.psx` |
| **OUT** | núvol de punts dens (E57, LAZ o TXT); malla OBJ ASCII + MTL + JPG; ortofoto i MDT GeoTIFF; panorama equirectangular màster TIFF (obligatori) i paquet Pano2VR publicat (opcional, es conserva); vídeo muntat: màster ProRes 422 en MOV o FFV1 en MKV si existeix, més el H.264 de difusió; PDF/A; GPKG/SHP; glTF/GLB de difusió; instantània datada d'un lloc web publicat (`web_AAAA-MM-DD.zip`) | proves, exportacions intermèdies |
| **DOC** | `informe-metashape.pdf`, `cameres.txt`, `marcadors.csv`, `gcp.csv` amb datum, croquis, memòria, permisos, pressupost i contracte, correus clau, bibliografia, `manifest-sha256_AAAA-MM-DD.txt` (manifests anteriors si el projecte s'ha esmenat o reobert) | — |

Dins de PROJ, OUT i DOC hi pot haver subcarpetes lliures (`PROJ\metashape\`, `OUT\ortofotos\`).

**Metashape: definicions per a les eines i per a `esteve`:**
- **`.psx` final**: el que ha produït els OUT. **Producte**: cada OUT independent (ortofoto general, model d'una UE, panorama). Un producte, un `.psx` final. **Excepció registrada**: projectes estructurats per unitats (150 `.psx` per UE a Sanxo Llop) són un conjunt de productes; s'arxiven tots i el README ho diu.
- **Versió intermèdia**: qualsevol `.psx` que no és final. Va a quarantena, llevat que **continga feina única**: màscares manuals, marcadors col·locats, chunks alineats o productes que el final no té. Ho decideix `esteve` al pas D, obrint-lo; si en conté, es queda a PROJ amb nota al README.
- **Aprimar**: només des de Metashape o la seua API, per chunk: eliminar mapes de profunditat i punts clau (regenerables); conservar càmeres, punts d'enllaç, núvol dens, malla, textura, ortofoto i MDT (encara que també siguen a OUT: la duplicació és acceptada). Mai esborrar fitxers dins de `.files` a mà.
- **Autocontingut**: totes les rutes de càmera del `.psx` resolen dins de la carpeta del projecte o de la seua sèrie, cap d'absoluta, cap fora del disc.
- **`.psz`**: un sol fitxer que conté tot el projecte (no les fotos). Opcional per a projectes de menys de **20 GB** de `.files`; la resta es conserven com a `.psx` + `.files`. [Agisoft Helpdesk; PRONOM fmt/1502]
- **Projectes PhotoScan antics**: es conserven tal com estan (obrir-los amb Metashape 2.x converteix el format); el README registra la versió; si cal reenllaçar, es fa sobre una còpia i es conserven els dos.
- **Primari / secundari** [LAD-BW]: el model registra una situació espacial que ja no existeix → primari: malla amb textura i núvol a OUT. Només va servir per a traure una ortofoto o plànol → secundari: ortofoto i núvol a OUT. El `.psx` es conserva en tots dos casos. Camp al README i columna al catàleg.

### 3.3 README.md (immutable: entra al manifest)

Camps obligatoris en negreta. No conté res que canvie amb el temps (suports, verificacions): això viu a discaleg i al catàleg.

```
# AAAA_descriptor — títol llarg
**Sèrie:** diablo-wasi        **Codi de sèrie:** DW
**Estat en segellar:** tancat (AAAA-MM-DD)
**Lloc:**                      **Client / programa:**
**Dates de captura:**          **Autor:** Esteve Ribera-Torró (Panograma Labs)
**Drets i accés:** propietari de les dades; llicència o condicions del client; embargament fins a (data) si n'hi ha; material no publicable (sí/no)
**Etiquetes:** lloc=..., client=..., tipus=..., any=AAAA
**Usa captures de:** (projectes, o «cap»)
**Resum:** què es va documentar i amb quin objectiu, en dues frases.
**raw-estil:** nou | llegat
## Captura
Dispositius, nombre d'imatges, format, control (equip, CRS, datum), calibratge, desajustos d'hora.
## Procés
Programari i versió, paràmetres, RMSE de control i de comprovació; .psx final(s) i productes; primari/secundari de cada model; versions conservades per feina única; què es va aprimar.
## Productes
OUT amb format, GSD/resolució, CRS.
## Procedència
Carpetes d'origen (suport per número de sèrie i ruta) i equivalències de noms antics; fitxers coneguts com a danyats o truncats (llista) i decisió presa; «recuperat de RAID danyat» si escau.
## Excepcions
Regles del model no seguides i per què.
## Notes
**Data de redacció del README:** AAAA-MM-DD
```

[Cornell README; ARIADNEplus 2023 §3.3; ISAD(G) 3.4.1, 3.7.3; PREMIS Rights]

### 3.4 SERIE.md (mutable; es versiona al catàleg git; no entra en cap manifest)

```
# diablo-wasi — nom llarg
**Codi:** DW   **Estat:** activa | inactiva
**Lloc:** descripció, coordenades, CRS de referència
**Programa / clients:** història
**Projectes:** llista AAAA_descriptor — estat — any
**Relacions:** sèries relacionades; material a _TERCERS
**Suports:** disc de l'arxiu canònic (número de sèrie) i disc de la segona còpia (número de sèrie)
**Data de revisió:** AAAA-MM-DD
```

### 3.5 Manifests

- `manifest-sha256.txt`: una línia per fitxer del projecte (README inclòs), format `<sha256>  <ruta>`, ruta relativa a la carpeta del projecte amb `/`, bytes UTF-8 tal com són al disc (sense normalitzar), LF, sense BOM. Verificable amb `sha256sum -c` o `Get-FileHash`.
- `manifest-mides.txt`: `<bytes>\t<ruta>`, mateix ordre: permet la precomprovació barata (nombre de fitxers i mides) abans de rellegir. [NDSA]
- `referencies-sha256.txt`: només per a projectes que referencien captures d'altres: `<sha256>  <ruta relativa que ix del projecte>`. Separat perquè `manifest-sha256.txt` no isca mai del projecte.
- Els manifests no són un bag BagIt; són **convertibles** a BagIt si cal dipositar. [RFC 8493]
- Viuen amb el projecte, a discaleg i (el seu hash) al catàleg git.

## 4. Capes i política de conservació

| Què | Conservar | Còpies | Format |
|---|---|---|---|
| Captura (RAW) i processades referenciades | sempre, sencer; **el vídeo no s'avalua ni se selecciona** | 3 | el de captura [ADS 2020]; DNG opcional més avant; TIFF no |
| OUT i DOC | sempre | 3 | obert (§3.2) |
| PROJ: `.psx` final + `.files` aprimada, `.lrcat`, `.blend`, `.qgz`… | sempre | 2 | natiu |
| Versions intermèdies | no, llevat feina única | — | quarantena |
| Procés regenerable | no | 0 | — |
| Programari | `_EINES` només el que ha produït OUT | 2 | — |
| Imatges de disc, dependències de codi, paperera, descàrregues | no | — | — |

**Correspondència classe d'anàlisi → capa de projecte** (proposta de l'eina; `esteve` confirma):

| Classe | Capa proposada | Nota |
|---|---|---|
| ORIG | RAW | |
| IMG | `RAW\processades` si un `.psx` la referencia; OUT si és producte (nom, format, carpeta `out/export/final`); si no, **ambigua**: decisió humana | 3,6 TB ambigus a l'inventari |
| VID | RAW si és de càmera o dron (carpeta o nom de dispositiu, EXIF); OUT si és muntatge; si no, ambigua | 4,8 TB ambigus |
| PROC | no s'arxiva, excepte `.files` del `.psx` final | |
| PROJ | PROJ | |
| PROD | OUT | |
| DOC | DOC | |
| COMP | mirar dins: ambigua | |
| SOFT, IMGDISC, SYS, CODI | fora de l'arxiu (CODI propi → git) | |

## 5. Estats, agents, tancament, esmena

**Estats de projecte**: obert → en-tancament → tancat; o descartat.
**Estats de suport** [principi del projecte]: en-reorganització (tots, durant la migració) → tancat (ordenat, hash, segona còpia; només lectura) o obert (treball, auditat).
**Agents**: `esteve`; scripts amb nom i versió. Només `esteve` segella, reobri, autoritza quarantena i excepcions.

**Projectes nous**: en lliurar, el projecte passa a **en-tancament**: PROJ i OUT es copien a l'arxiu canònic i a la segona còpia (sense segellar) el mateix dia, perquè cap capa es quede en còpia única a l'SSD. Se segella quan passen **6 mesos** sense canvis (rellotge: mtime màxim de RAW, PROJ i OUT; DOC i README no compten) o abans si `esteve` ho decideix. La còpia de l'SSD passa a quarantena després del segellat.

**Projectes llegat (migració)**: el rellotge dels 6 mesos ja s'ha complit; se segellen al pas D.

**Criteris de tancament** (tots): un `.psx` final per producte (excepció UE registrada); RAW complet (per a projectes que referencien: `seleccio.txt` resol al 100 %); OUT segons el tipus (taula); DOC amb informe, càmeres, marcadors i GCP si n'hi ha; README complet; manifests generats; segona còpia verificada per relectura; estat registrat a discaleg; commit al catàleg.

| Tipus | OUT mínim per a tancar |
|---|---|
| fotogrametria | núvol dens + malla amb textura si primari + ortofoto/MDT si n'hi ha + informe a DOC |
| dron sense fotogrametria | tot el RAW; OUT = muntatges o ortofotos si n'hi ha |
| 360 / VR | màster equirectangular TIFF per panorama; paquet publicat opcional |
| vídeo | màster del muntatge (ProRes o FFV1) + difusió H.264 |
| GIS | GPKG + `.qgz` a PROJ + PDF dels plànols |
| publicació | PDF/A + fonts editables a PROJ |
| docència | materials finals (PDF, vídeo) + fonts a PROJ |

**Segellat**: ACL de només lectura (§7.5), paritat sincronitzada, esdeveniment a discaleg (data, agent, hash del manifest, suports).

**Esmena lleugera** (errada al README, fitxer afegit a DOC): sense reobrir: `esteve` registra l'esdeveniment «esmena», l'eina alça l'ACL, aplica el canvi, regenera `manifest-sha256.txt` (l'anterior passa a `DOC\manifest-sha256_AAAA-MM-DD.txt`), torna a segellar i replica a la segona còpia. RAW, PROJ i OUT no es toquen en una esmena; si cal tocar-los, és una **reobertura**: nou cicle en-tancament → tancat.

## 6. Vocabularis

Un terme preferent per concepte, amb definició d'una línia, en `_KIT\vocabulari.md` i al catàleg git. [Toronto; FAIR I2]

- **lloc** (= sèrie de lloc) i **sublloc** (`la-petaca`, `tambillo` dins de `diablo-wasi`).
- **client / programa**: `palp`, `ajuntament-bocairent`, `propi`, `inesia`…
- **tipus** (definits): `fotogrametria` (model o ortofoto per SfM), `dron` (captura aèria sense model), `360-vr`, `video`, `gis`, `3d-modelat` (modelat no fotogramètric), `publicacio`, `recerca` (treball propi sense client ni publicació), `codi`, `docencia`.
- **any**: vista cronològica.
- **dispositiu** (tancat; s'amplia afegint): `sony-a7iii`, `sony-a7r`, `nikon-d300s`, `dji-mavic3`, `dji-mini`, `insta360-x3`, `gopro-fusion`, `gopro-max`, `mobil-esteve`, `escaner`, `altre` (amb nota).

Espais virtuals a discaleg: per sèrie, any, client, tipus, estat, «RAW de dron», «projectes sense segona còpia», «en-tancament fa més de 6 mesos». Apunten a sèrie o projecte + número de sèrie del suport.

## 7. Suports

### 7.1 Estat objectiu (els rols actuals són «en-reorganització» fins al final)

| Nivell | Suport | Contingut | Règim |
|---|---|---|---|
| Treball | SSD-3 (2 TB) ara; NVMe 2-4 TB més avant | projectes oberts amb les quatre capes des del primer dia | editable; RAW copiat a l'arxiu el dia de la captura |
| **Arxiu canònic**: Yottamaster A | 4 × BarraCuda 8 TB (dades, 32 TB útils) + Exos 16 TB (paritat SnapRAID, quan s'haja buidat). Quan es compre el CMR de 16 TB: 3 BarraCuda + disc nou (dades: sèries grans) + Exos (paritat) = 40 TB; el BarraCuda que surt va a B | ARXIU, per sèries senceres; `_GIS-BASE` replicada a cada disc | SnapRAID sobre els discs sencers; `sync` després de cada ingesta o tancament; `_QUARANTENA` exclosa; `scrub` setmanal; fins que l'Exos es buide, A funciona sense paritat, protegida per B |
| **Segona còpia**: Yottamaster B | INT-3 (8), INT-5 (8), EXT-E (5, reformatat NTFS) i, quan arribe el disc nou, el 4t BarraCuda (8) | còpia de RAW, OUT, DOC, PROJ per sèries senceres; `_KIT` | **desconnectada** excepte per a copiar i verificar («fire break»); sense paritat |
| Tercera còpia (al final) | núvol d'objectes via rclone (B2, Hetzner o Glacier) | RAW + OUT + DOC dels projectes tancats, **excepte material marcat no publicable o amb embargament** | hash del proveïdor acceptat com a prova de bit-rot; `rclone check --download` anual sobre una mostra del 10 % |
| PERSONAL | 5é BarraCuda o extern propi + extern vell | — | còpia de seguretat |
| Catàleg | repositori git **privat** `arxiu-cataleg` + discaleg | README, SERIE.md, vocabularis, catàleg CSV, hash de cada manifest | commit a cada tancament o esmena |

El dipòsit institucional (Zenodo, ADS, tDAR) és difusió, no còpia.

### 7.2 Regles

- Paritat sempre en CMR i en un disc de mida ≥ el disc de dades més gran: per això el disc nou, quan es compre, és de **16 TB i no més** (l'Exos és l'únic disc actual que pot fer de paritat). La paritat no és una còpia ni és essencial: l'essencial és B; la paritat estalvia restaurar des de B si un BarraCuda mor i repara corrupció silenciosa (`scrub`).
- Els BarraCuda (DM-SMR) s'escriuen per blocs grans i seqüencials (una campanya d'una vegada) i no es reobrin «in situ»: es treballa a l'SSD i es reingereix.
- Sèrie → disc: ho assigna `esteve` al catàleg; `SERIE.md` ho registra; en B, la mateixa sèrie en un sol disc.
- La DB de discaleg i les seues còpies viuen en un disc diferent dels que descriu (PC-C + `_KIT` a B).

### 7.3 Verificació (una sola regla)

Arxiu canònic: `scrub` setmanal (tot l'array cada trimestre) + relectura SHA-256 completa de cada sèrie contra el seu manifest **cada 12 mesos**. Segona còpia: relectura completa cada 12 mesos. SMART mensual a A, i a B cada vegada que es connecta. Restauració de prova d'un projecte cada any. [DPC: 6-12 mesos en HDD; NDSA L3]

### 7.4 Salut i renovació dels suports

- Porta d'entrada: cap disc rep arxiu sense SMART complet i prova de lectura sencera (badblocks en mode lectura o equivalent), i sense comprovar `LogicalSectorSize` = 512 des del PC on es formatarà. [principi 6]
- Els BarraCuda tenen uns cinc anys i vénen d'un array corromput: si algun falla la porta, no entra. Risc de lot: dades en BarraCuda (A), segona còpia en INT-3/INT-5 (model a confirmar) i disc nou: dos fabricants o dos lots com a mínim. [DPC]
- Renovació: cada suport d'arxiu es reemplaça als **5 anys** d'entrada o abans si SMART ho aconsella. [LoC; DPC]
- Els externs exFAT es buiden (contingut migrat i verificat) abans de reformatar-los NTFS.

### 7.5 Segellat amb ACL

`icacls <projecte> /save _KIT\acl\<serie>_<projecte>_<data>.acl /T` (el `.acl` **fora** de l'arbre segellat) i després `icacls <projecte> /deny Users:(OI)(CI)(DE,WD,AD,WEA,WA) /T`. Els administradors conserven control total: `snapraid fix`, la quarantena, l'esmena i la reobertura s'executen elevats, i l'eina fa `icacls /restore` abans i torna a segellar després. robocopy no copia l'ACL: l'executor aplica el mateix segellat a la segona còpia després de verificar-la.

### 7.6 Kit de recuperació (`_KIT`, a B i a PC-C)

Còpies de la DB de discaleg (setmanal), `snapraid.conf` i el seu `content`, `rclone.conf`, els `.acl`, el vocabulari, una còpia del catàleg git, i un `COM-RESTAURAR.md` de dues pàgines.

## 8. Nomenclatura (regla verificable)

- Sèries: `^[a-z0-9]+(-[a-z0-9]+)*$`. Projectes: `^\d{4}_[a-z0-9]+(-[a-z0-9]+)*$`. Carpetes de dia: `^\d{4}-\d{2}-\d{2}_[a-z0-9-]+$`. Dispositius: vocabulari tancat.
- Fitxers nous a OUT i DOC: minúscules, `a-z 0-9 - _ .`, sense espais ni accents, un sol punt, projecte i contingut al nom (`2024_covetes_ortofoto-general_2cm.tif`), versió `_v01` només en treball.
- Exempts: noms fixos (§3), fitxers de càmera, tot el RAW llegat, tot el que un `.psx` referencia, i el contingut de `.files`.
- Longitud: els camins nous es mantenen per sota de 200 caràcters; dins de `.files` no es pot garantir (10.471 rutes > 240 a l'inventari) i s'accepta: robocopy i rclone les copien; qualsevol script Python usa `\\?\`. [Microsoft Learn; NARA]
- NFD i accents llegat: es conserven (§3.1).

## 9. Procediments

**Pas 0 (abans de tocar cap disc).** Sense comprar res: (1) EXT-E (WD Elements de 5 TB físics, 2 TB en ús amb `REGISTRE23`, que té còpia a SSD-1 i a INT-5): verificar per hash que `REGISTRE23` és idèntic a una de les còpies, reformatar EXT-E sencer a NTFS (5 TB), porta d'entrada (§7.4), i copiar-hi amb verificació els únics de `RECUPERACIO_Z` (2,7 TB) i de MAC-INESIA (0,5 TB). (2) INT-3 (2,7 TB lliures): copiar-hi amb verificació el RAW únic de 2025-26 que ara només és a INT-5 (FOT-VID25, PROJ26, ~1,5 TB); el de FTGM25 ja és a INT-3 i es copia a INT-5 o EXT-E. (3) Registre a discaleg. Després d'això es pot esborrar la imatge AOMEI d'INT-3 (quarantena) i formatar els BarraCuda. Si abans d'arribar ací s'ha comprat el CMR de 16 TB, el pas 0 es fa al disc nou i EXT-E es reserva per a B.

**Ingesta d'un projecte nou.** Crear `AAAA_descriptor` a l'SSD amb les quatre capes i README esquelet; copiar targetes a RAW; el mateix dia, copiar RAW a l'arxiu canònic i verificar; `captura.csv`; treballar a PROJ; en lliurar, en-tancament (§5).

**Tancament.** Decidir versions (feina única) → quarantena de les altres; exportar OUT; aprimar `.files` o `.psz`; comprovar autocontenció obrint el `.psx` des de la carpeta final; completar README; manifests; copiar a A i a B; verificar rellegint; ACL; `snapraid sync`; esdeveniment; commit.

**Migració del llegat (cicle per família)**: A) catàleg automàtic; B) revisió al catàleg (sèrie, projecte, capa, any, canònica, estat, excepcions); C) executor: destí, còpia per mapa, hash a les dues bandes, README esquelet, registre, **sense esborrar**; D) tancament manual on calga; E) quarantena dels orígens verificats (per blocs, amb CSV) i esborrat passats 30 dies i una verificació més. Ordre: pas 0 → residus i imatges de disc → pilot ≤ 2019 → DW → Covetes → resta → 2025-26 quan es tanquen → C: i D: al final → Exos buit → paritat.

**Quarantena.** `_QUARANTENA\AAAA-MM-DD\<ruta original>` al mateix disc, exclosa de SnapRAID, amb `quarantena.csv` (fitxer, origen, destí verificat, motiu, agent, data). Esborrat definitiu als 30 dies i amb la còpia reverificada.

**Eines.** robocopy `/E /COPY:DAT /DCOPY:DAT /XJ /R:2 /W:5 /MT:8 /XD "$RECYCLE.BIN" "System Volume Information" /LOG+:...` (mai `/MIR`); o rclone `copy` + `rclone check --checksum`; hash amb `hash_files.py` de discaleg; AOMEI només per a la imatge del sistema de C:. Temps previst de hash: desenes d'hores per passada completa; es planifica per sèries.

## 10. Catàleg de projectes (pas A)

**Candidat**: una carpeta de l'inventari que (a) conté directament un fitxer de projecte (`.psx`, `.lrcat`, `.blend`, `.qgz`, `.p2vr`, `.prproj`, `.pts`…) o (b) és de nivell 1 o 2 sota un contenidor conegut (`PROJECTES2022`, `PROJ26`, `Root\Foto-Video`, `Users\elmor\Documents`…), i (c) pesa ≥ 1 GB o conté un fitxer de projecte. La resta s'agrupa en «residu» per disc.

**Columnes generades**: id; suport (número de sèrie); ruta; sèrie proposada (per vocabulari de paraules clau i precedència §2.1); projecte proposat (`AAAA_descriptor`) i **capa dins del projecte** si el candidat n'és només una part; any proposat (§2.2); classes d'anàlisi en bytes; bytes «únics» (orientatiu: nom+mida ≥ 1 MB; els < 1 MB compten com a únics; **cap esborrat es decideix amb aquesta columna: es recalcula amb hash abans del pas E**); discs on apareix el mateix contingut; marcadors de projecte; `.psx` amb comprovació de rutes (resoltes / altre disc / orfes); mapa de subcarpetes → capa amb confiança (regla / nom / ambigu); proposta de canònica.

**Regla de còpia canònica** (precedència): la còpia més completa (fitxers i bytes) → sense errors de lectura al suport → NTFS abans que exFAT → mtime original conservat → suport amb millor SMART. [precedent: INT-5\DW23_TFM26]

**Columnes que omple `esteve`**: sèrie, projecte, capa, any, estat (tancar / obert / descartar / fusionar-amb / tercers / personal), canònica, primari/secundari, excepcions, observacions.

Les xifres de dimensionament (candidats, concentració, ambigus) són a l'anàlisi i es recalculen quan es genera el catàleg.

## 11. Casos especials (una regla per cas)

- **`RECUPERACIO_Z\Root\Foto-Video`**: una còpia més dels projectes de PROJECTES2022/2024B; entra al joc de la canònica.
- **`found.000/001`**: Siegfried identifica formats; el que es pot assignar a un projecte (per nom, mida o hash) hi entra amb procedència «recuperat de RAID»; els 787 GB sense context van a `varis\2025_recuperacio-raid\` amb README que diu d'on vénen, i no s'esborren.
- **Fitxers danyats o truncats coneguts** dins d'un projecte: es conserven si no hi ha còpia sana, llistats al README amb la marca «danyat»; si n'hi ha, la sana és la canònica.
- **Material personal en discs professionals**: al pas B, per carpeta; les carpetes dubtoses (fotos de família en viatge de treball) es parteixen a mà; en cas de dubte, PERSONAL.
- **PC-C `Users\elmor`**: `Documents\ARQ_UA` → `docencia` o `varis` (màster: `recerca`); `Desktop` i `Downloads` es revisen; el que no s'assigna, quarantena.
- **MAC-INESIA**: `revisarYUPANA` → projectes de 2018 de la sèrie corresponent (viatge Perú) o `fototeca`; `Google Drive_ordenar`, `TEMP`, `DESCARREGUES` → revisió i quarantena.
- **Projectes 2007-2017 sense estructura**: s'importen amb RAW llegat tal com estan (si hi ha captura), PROJ i OUT segons es puga, la resta a DOC; no es reorganitzen.
- **Vídeos 360 de 155 GB**: RAW, sense selecció, tres còpies; el cost s'assumeix.
- **Material de client amb condicions**: camp «Drets i accés» al README; exclòs de la tercera còpia i de qualsevol dipòsit.
- **Llocs web i visors publicats**: no són còpia; instantània datada a OUT en tancar.
- **Dades GIS compartides**: `_GIS-BASE`.
- **Codi propi**: git; a l'arxiu només dades de prova i lliuraments.
- **Projectes vius amb RAW en un sol disc (2025-26)**: còpia pont al pas 0, abans de res.
- **Còpia parcial d'un projecte obert** (RAW a l'arxiu, resta a l'SSD): discaleg la modela «per capa»; l'executor resincronitza RAW quan creix.

## 12. Pendents a discaleg

Relació «usa captures de»; còpies per capa; estats de suport i de projecte; esdeveniments (còpia, verificació, tancament, esmena, reobertura, quarantena, esborrat, excepció) amb agent, data i resultat [PREMIS]; lectura de README i SERIE.md; exportació i importació del catàleg; vista «projecte» que agrupa `.psx` + `.files`; `capes.py` integrat; exclusions per defecte (`node_modules`, `venv`, `.git`, `.gradle`, `.vscode`, `.cursor`, `RECYCLER`); `--path` al hash; Siegfried per a `found.00x`; comprovador de rutes de Metashape (annex); v9 núvol.

## 13. Decisions que fixa aquest document

1. Nivells fons / sèrie / projecte / fitxer; precedència de sèrie; nom `AAAA_descriptor`; una sèrie, un disc; referències per ruta relativa i `seleccio.txt`.
2. ARXIU i PERSONAL; sèries de tipus; carpetes de fons `_EINES`, `_GIS-BASE`, `_TERCERS`, `_KIT`, `_QUARANTENA`.
3. RAW / PROJ / OUT / DOC; RAW nou per dia i dispositiu, RAW llegat tal com està; README immutable i manifests propis.
4. Política per capa; `.psx` final + `.files` aprimada conservats; versions a quarantena; vídeo sense selecció.
5. Estats, agents, 6 mesos com a convenció, esmena lleugera.
6. Yottamaster A = 3 BarraCuda + disc nou 16 TB + Exos (paritat); B desconnectada; tercera còpia al final amb exclusions; dipòsit no és còpia.
7. RAW tal com es capta.
8. **Compra del CMR de 16 TB posposada**: després del catàleg i només si el preu baixa del llindar (orientatiu: ≤ 500 € nou, SATA, Exos X16/X18/X20 o Toshiba MG09/MG10; mai SAS ni de segona mà per al pas 0). Mentrestant, pas 0 amb EXT-E + INT-3 i Yottamaster A sense disc nou.
9. Eines: robocopy/rclone + SHA-256 rellegint + discaleg; AOMEI només sistema.
10. Porta d'entrada SMART + lectura + 512e; renovació als 5 anys.

Pròxim pas: pla de migració i especificació de les tres eines (catàleg, comprovador de rutes, executor), i pas 0 en rebre el disc.

## Annex: comprovació de rutes dels projectes Metashape

Un `.psx` és un XML menut que apunta a `<nom>.files\`; dins: `project.zip`, i per chunk `chunk.zip` (càmeres, sensors, marcadors) i per frame `frame.zip`, que conté la ruta de la foto de cada càmera, relativa a la carpeta del frame o absoluta. El comprovador (només lectura, sense Metashape) obri cada `frame.zip`, extrau les rutes, les resol i les contrasta amb l'inventari de discaleg: resolta al mateix lloc / resolta en un altre disc per nom i mida / orfe. Eixida per `.psx`: càmeres, resoltes, orfes, discs implicats, versió de Metashape o PhotoScan, i la llista de fotos usades (= `seleccio.txt`). S'executa al pas A (dependències i orfes, sobretot a `RECUPERACIO_Z` i `found.00x`) i al pas D (autocontenció al destí). El reenllaç en bloc es fa amb l'API de Metashape sobre una còpia; els projectes PhotoScan antics es conserven sense convertir. L'estructura exacta de `frame.xml` es confirma amb un projecte real abans d'escriure l'eina. [pypsxlib; Agisoft Helpdesk]
