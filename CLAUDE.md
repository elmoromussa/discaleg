# Discàleg — guia per a Claude Code

Aplicació web local (FastAPI + SQLite) per inventariar discs i trobar còpies entre ells. La referència completa d'esquema, rutes i mòduls és `PROJECT_OVERVIEW.md`: llegeix-lo abans de tocar `app/schema.py`, `app/queries*.py` o les rutes. Mantén-lo actualitzat quan canviïs l'esquema, les rutes o els mòduls; el mateix per a `README.md` i `docs/`.

## Idioma

Tot en català: interfície, documentació, comentaris, missatges de commit i respostes a l'usuari. Noms de variables i funcions en anglès, com fins ara.

## Executar

```
venv\Scripts\python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

El venv és Python 3.12 amb FastAPI 0.143 i Starlette 1.7. No hi ha Node. La variable `DATABASE_PATH` canvia la base de dades; per defecte és `data/arxiu_discs.db`.

## Dades: la DB real és intocable des de proves

`data/arxiu_discs.db` (ignorada per git) és la base de dades de producció de l'usuari: uns 4,2 GB, 20 discs, 4,7 milions de fitxers, esquema v6. No la facis servir per a res que escrigui. Per llegir-la des d'scripts obre-la en mode només lectura:

```python
sqlite3.connect("file:data/arxiu_discs.db?mode=ro", uri=True)
```

Per a proves que escriuen o per arrencar un servidor de verificació, crea una DB de banc a part (`init_schema` + còpia d'uns quants discs per nom de columna, no amb `SELECT *`) i apunta-hi amb `DATABASE_PATH`. Les còpies de seguretat de l'usuari són fora del repositori; no proposis mai esborrar-ne cap.

## Rendiment

L'usuari treballa amb milions de files i li importa el temps de resposta. Escriu SQL per conjunts, aprofita els índexs cobridors que ja existeixen (vegeu l'esquema al PROJECT_OVERVIEW) i mesura qualsevol consulta nova contra la DB real en mode lectura abans de donar-la per bona. Referències mesurades: un rang de carpeta sobre l'índex únic (drive_id, relative_path) triga centèsimes de segon; la poda FTS costa uns 2,5 s i no compensa dins de carpetes petites; un GROUP BY sobre tota la DB per índex cobridor ronda 1 s.

## Esquema i migracions

Els canvis d'esquema són sempre additius: nova versió a `SCHEMA_VERSION`, pas de migració a `migrate()` de `app/schema.py`, i `python -m app.migrate` per aplicar-lo. Mode WAL. No reescriguis taules existents.

## Convencions de codi

- Starlette 1.x: `TemplateResponse(request, nom, context)`, amb la petició com a primer argument.
- Frontend sense frameworks: HTML Jinja2, `style.css`, `app.js` (tema) i `explorer.js` (explorador, pur JS sobre l'API). `[hidden]` és global a `style.css`.
- `style.css` i `base.html` són CRLF a la còpia de treball. Edita'ls amb l'eina Edit o amb Python `newline=""`; els heredocs de Bash els espatllen.
- Sense dependències noves si no cal; `requirements.txt` és la llista completa.

## Verificació

No hi ha suite de tests encara. Com a mínim passa `venv\Scripts\python -m pyflakes app tools` i arrenca el servidor contra una DB de banc. Per a canvis d'interfície, fes captures amb Chrome headless (`--headless=new --screenshot=... --window-size=1280,900`, i `420,900` per a mòbil) i comprova-les: l'usuari no veu la sortida de les eines i la captura és l'única prova. Mata el servidor de verificació en acabar, filtrant només els processos `python.exe` que executen uvicorn.

## Commits

Una fase de feina, un commit, i demana confirmació abans de fer-lo. Missatges en català amb el format `Àrea: què canvia` (per exemple `Explorador: menú contextual`). No facis push sense que t'ho demanin.

## Llicència

PolyForm Noncommercial 1.0.0 (fitxer `LICENSE`): ús, modificació i redistribució lliures amb finalitats no comercials, conservant l'avís d'autoria. No incorporis codi de tercers amb llicències incompatibles (copyleft fort com GPL) sense comentar-ho.
