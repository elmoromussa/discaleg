---
title: Ús i flux de treball — DiscScope
layout: default
---

# Ús i flux de treball

## Arrencar sense base de dades

Si encara no hi ha cap fitxer de base de dades, l'app mostra una pantalla de benvinguda amb un enllaç a **Discs**. Des de **Discs** pots auditar la primera carpeta o disc i es crearà la base de dades automàticament.

## Gestió de discs

### Incorporar un disc nou

1. Ves a **Discs**.
2. Indica la **carpeta o disc a auditar** (p. ex. `D:\` o `E:\backups`).
3. Indica un **identificador del disc** (p. ex. `EXT-A`, `HDD1`). Aquest nom servirà per filtrar i identificar el disc a tot l'app.
4. Enviar el formulari. L'app recorre la carpeta i escriu directament a la base de dades (sense CSV intermedis).

En discs molt grans (p. ex. 5–8 TB amb molts fitxers), el procés pot dur diversos minuts.

### Actualitzar un disc existent

1. A la llista de discs, prem **Actualitzar** i tria la unitat o carpeta on està muntat (o usa el formulari amb el mateix *drive_id*).
2. Enviar. Els fitxers nous s'afegeixen, els modificats s'actualitzen, els desapareguts es marquen i els hashes dels fitxers no modificats es conserven.

Si marques **Reiniciar el disc**, l'inventari d'aquest *drive_id* s'esborra i es torna a crear (es perden els hashes).

### Accions de l'explorador

A l'**Explorador**, el clic dret (o el botó ⋯ de cada fila, o Maj+F10 amb la fila enfocada) obre un menú d'accions. Per a una carpeta: **Cerca dins d'aquesta carpeta** (obre Cercar limitat a aquella carpeta; sense cap terme llista tots els seus fitxers), **Exporta a CSV** (només la carpeta o amb subcarpetes; UTF-8 amb BOM i separador «;», obre bé amb Excel) i **Copia la ruta**. Per a un fitxer: cerca del nom a tots els discs, **Còpies d'aquest fitxer** (per contingut si té hash, si no per nom i mida) i copiar la ruta. El botó ⋯ de la barra d'eines fa el mateix per a la carpeta oberta. Les files del llistat es recorren amb les fletxes.

### Espais virtuals: cerques desades

Un **espai** agrupa fitxers de qualsevol disc sense moure'ls. La primera forma són les **cerques desades**: després d'executar una cerca de fitxers a **Cercar**, **Desa aquesta cerca com a espai** li posa nom i la penja de l'arrel o d'un altre espai. A l'**Explorador**, el panell **Espais** (sota Discs) mostra l'arbre; en obrir una cerca desada es repeteix la cerca i la llista mostra els fitxers amb les columnes **Disc** i **Carpeta** (clicables per anar-hi), ordenació al servidor i «Carrega 300 més». La barra d'eines té **Edita els criteris** (torna a Cercar amb els criteris carregats; en desar s'actualitza l'espai), **Reanomena**, **Mou a** i **Elimina** (també els espais que conté). El botó **+** del panell crea un espai contenidor (una carpeta d'espais) dins de l'espai obert o a l'arrel. L'URL `?space=ID` es pot compartir.

### Espais virtuals: col·leccions i etiquetes

La segona forma d'espai són les **col·leccions**: hi afegiu fitxers i carpetes a mà. A l'Explorador, seleccioneu files (clic, Ctrl+clic, Maj+clic, Ctrl+A) i trieu **Afegeix a…** al menú contextual (també per a la carpeta oberta): un diàleg deixa triar la col·lecció (o crear-ne una) i marcar **etiquetes** (o crear-ne). Els fitxers s'identifiquen pel **contingut** (hash SHA-256) quan en tenen, de manera que l'espai mostra totes les còpies presents a qualsevol disc i sobreviu a moure'ls; si encara no tenen hash s'identifiquen per disc i ruta, i el hash es completa sol quan es calcula. Les carpetes entren **senceres** com un sol element (prefix de ruta), mai fitxer a fitxer. La columna **Motiu** diu per què hi és cada fitxer, i el menú de la fila permet treure l'element de la col·lecció. Les etiquetes es veuen al node **Etiquetes** del panell Espais, i a **Cercar** hi ha el filtre «Només fitxers amb l'etiqueta», que també pot formar part d'una cerca desada.

### Fitxa del disc

A cada targeta de **Discs**, **Edita la fitxa** desplega un formulari per indicar l'**etiqueta** (nom amb sentit per a tu), el **tipus** de suport (disc dur, SSD, NVMe, memòria USB, targeta SD, òptic, xarxa, altre), la **ubicació** física (on és guardat) i **notes** lliures. Aquestes dades es mostren a la targeta i al rètol del disc a l'Explorador. L'escaneig no les toca: només omple l'etiqueta amb la del volum si encara és buida.

### Què ha canviat entre escaneigs

A cada targeta de **Discs**, **Canvis** obre la vista dels escaneigs del disc: per a cada escaneig en mode actualització hi ha tres pestanyes amb els fitxers **nous**, **modificats** (mida i data abans i després, amb el que ha canviat ressaltat) i **desapareguts**, paginats i ordenats per ruta. La carpeta de cada fitxer enllaça a l'Explorador i el nom a la cerca. Els comptadors de la taula «Últims escaneigs» també hi porten. Els escaneigs complets (reinici del disc o importació dels CSV originals) no tenen detall per fitxer: tot era nou.

### Hashes i verificació

El càlcul del SHA-256 per fitxer es fa amb `python tools/hash_files.py` (vegeu [Escaneig, actualització i hash](escaneig_i_hash.html)). És incremental: es pot aturar i reprendre, i la pàgina **Discs** mostra el percentatge de fitxers amb hash per disc.

## Rutes principals

| Ruta | Descripció |
|------|-------------|
| **Inici** | Si hi ha DB: resum per disc, top extensions, top carpetes. Si no: benvinguda. |
| **Cercar** | Cerca per fragments de nom i/o de ruta (tots han de coincidir), o cerca clàssica d'un sol camp; comodins `*` i `?`; filtre per disc i extensió. |
| **Duplicats** | Fitxers amb el mateix nom, o nom + mida, en més d'un disc. |
| **Extensions** | Estadístiques per extensió (paginat). |
| **Carpetes** | Llistat de carpetes per mida (paginat). |
| **Explorador** | Arbre de carpetes i fitxers des de la DB (sense accés al sistema de fitxers) i panell d'espais virtuals (cerques desades). Clic dret, botó ⋯ o Maj+F10 sobre un element: cerca dins la carpeta, exportació a CSV, còpies d'un fitxer, copiar la ruta. |
| **Discs** | Auditar i incorporar o actualitzar discs; fitxa de cada disc (etiqueta, tipus, ubicació, notes); vista «Canvis» per escaneig. |

## Notes

- Les cerques i llistats grans fan servir **paginació** per no sobrecarregar la memòria.
- Les rutes que requereixen la base de dades retornen error **503** si la DB no existeix; **Inici** i **Discs** es carreguen sense DB.
