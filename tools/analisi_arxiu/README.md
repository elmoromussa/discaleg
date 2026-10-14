# Anàlisi arxivística de l'arxiu (14/10/2026)

Scripts de només lectura sobre `data/arxiu_discs.db`. Generen una DB compacta
(`arxiu.db`, ~3,6 GB) amb una fila per fitxer viu i la classificació per capes.

1. `extract.py SRC OUT LO HI` — extrau un tram d'ids (p. ex. 4 trams de 2.000.000)
   a `OUT` amb disc, ruta, pare, nivells 1-3, nom, extensió, mida, any de
   modificació i de creació, i clau de còpia (nom NFC minúscula + mida, ≥ 1 MB).
   En acabar els trams, crear els índexs (vegeu el final del script original).
2. `capes.py` — diccionari de capes (regles de ruta + extensió). Corregible.
3. `classify.py OUT LO HI` — assigna capa i marca `found.00x` a cada fila (taula `lay`).
4. `arbre.py NIVELL LIMIT [DISC]` — arbre de carpetes per disc amb capa dominant i anys.

Cap d'aquests scripts escriu a la DB de producció.
