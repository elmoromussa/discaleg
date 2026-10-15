---
title: Discàleg — Documentació
layout: default
---

# Discàleg — Documentació

**Discàleg** és una aplicació web local per mantenir i consultar l'inventari dels teus discs i carpetes. Audita directoris, desa les dades en una base SQLite i permet cercar, detectar duplicats, veure estadístiques per extensió i carpetes, i explorar tot l'arxiu des del navegador.

## Inici ràpid

1. **Requisits:** Python 3.10+
2. **Instal·lació:** `pip install -r requirements.txt`
3. **Executar:** doble clic a `run_inventari_WIN.bat` (Windows) o `uvicorn app.main:app --host 127.0.0.1 --port 8000`
4. **Primera vegada:** obre **Discs**, indica una carpeta i un identificador del disc; es crearà la base de dades automàticament.

## Documentació

| Pàgina | Descripció |
|--------|-------------|
| [Instal·lació](instalacio.html) | Requisits, entorn virtual, dependències |
| [Configuració](configuracio.html) | Ruta de la base de dades, variable d'entorn |
| [Ús i flux de treball](usage.html) | Auditar discs, cercar, duplicats, explorador |
| [Escaneig, actualització i hash](escaneig_i_hash.html) | Esquema v2, migració, modes d'escaneig, càlcul de SHA-256 incremental |
| [Model d'arxiu](model-arxiu.html) | Model d'arxiu digital v1.3: sèries, projectes, capes, suports, procediments |
| [Eines de migració](eines-migracio.html) | Especificació del catàleg, el comprovador de rutes i l'executor |
| [Migració: catàleg de projectes](migracio.html) | Esquema v9 a v14, classes d'anàlisi, vocabulari, `tools/cataleg.py`, comprovador de rutes Metashape i arxiu virtual (`tools/espais.py` i secció «Arxiu» de l'app) |
| [Arxiu virtual: pla per a l'executor](arxiu-virtual.html) | Format de `pla_arxiu.json` (`tools/espais.py --export-plan`): llistes copiar, verificar, esborrar, quarantena i pendents, amb exemple |

## Enllaços

- **Repositori:** [github.com/elmoromussa/discaleg](https://github.com/elmoromussa/discaleg)
- **README:** [README principal del projecte](https://github.com/elmoromussa/discaleg/blob/main/README.md)
