# DiscScope — Inventari de discs (aplicació web)

Aplicació web local per consultar i visualitzar la base de dades d'inventari de discs (`arxiu_discs.db`). Permet veure resums per disc, cercar per paraula clau, detectar duplicats per nom o nom+mida, llistar extensions i carpetes, i explorar l'inventari amb un **explorador simulat** (arbre de carpetes i fitxers des de la base de dades, sense accés al sistema de fitxers). La base de dades es pot crear i omplir des de la mateixa aplicació (secció **Discs**), auditant carpetes o discs directament sense fitxers CSV intermedis.

**Nota:** El repositori no inclou la base de dades ni la carpeta `data/` (s’ignoren amb `.gitignore`), per poder compartir el codi sense dades personals. La DB es crea localment en auditar el primer disc des de **Discs**.

## Requisits

- Python 3.10+
- Base de dades SQLite: `arxiu_discs.db` (es pot crear des de l’app si no existeix)

## Instal·lació

```bash
pip install -r requirements.txt
```

## Configuració

- **Ubicació de la base de dades:** Per defecte l'app busca `data/arxiu_discs.db` dins del directori del projecte. Si la DB està en una altra ruta, defineix la variable d'entorn:

  ```bash
  set DATABASE_PATH=H:\ruta\arxiu_discs.db
  ```

  (A Linux/macOS: `export DATABASE_PATH=/ruta/arxiu_discs.db`)

## Estructura

- **`data/`** — Conté el fitxer de la base de dades (`arxiu_discs.db`, més `-wal`/`-shm` en mode WAL). Es crea automàticament en auditar el primer disc des de **Discs**. No es versiona (`.gitignore`).
- **`app/`** — Aplicació FastAPI: `schema.py` (esquema i migracions), `audit_to_db.py` (escaneig directe a la DB), `migrate.py` (CLI de migració), `queries*.py`, `routes/`, `templates/`, `static/`.
- **`tools/`** — Eines de línia d'ordres: `hash_files.py` (SHA-256 incremental per disc) i els scripts originals d'auditoria en CSV (`quick_audit_archive.py`, `import_audit_to_db.py`, `import_all_audits.bat`), conservats com a procedència de la base de dades.
- **`docs/`** — Documentació (GitHub Pages).
- **Arrel del projecte** — `requirements.txt`, `run_inventari_WIN.bat`, `PROJECT_OVERVIEW.md` (referència del projecte), `.gitignore`, `.gitattributes`.

## Executar l'aplicació

### Opció 1: Execució ràpida (Windows)

Doble clic a **`run_inventari_WIN.bat`**. El script activa el venv, arrenca el servidor i obre el navegador a http://127.0.0.1:8000.

Requisit: haver creat abans l'entorn virtual (`python -m venv venv`) i instal·lat les dependències (`pip install -r requirements.txt`).

### Opció 2: Línia de comandes

Des del directori arrel del projecte (on hi ha `requirements.txt`):

```bash
uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Obrir el navegador a: **http://127.0.0.1:8000**

## Arrencar sense base de dades

L’aplicació pot arrencar **sense cap fitxer de base de dades**. En aquest cas, la pàgina d’inici mostra una pantalla de benvinguda amb un enllaç a **Discs**. Des de **Discs** podeu auditar una carpeta o un disc (indicant la ruta i un identificador, *drive_id*); es crearà la base de dades automàticament i s’incorporarà el primer disc. Treball **d’un disc en un** (no en batch).

## Gestió de discs (flux de treball)

- **Incorporar un disc nou:** A **Discs**, indiqueu la **carpeta o disc a auditar** (p. ex. `D:\` o `E:\backups`) i un **identificador del disc** (p. ex. `EXT-A`, `HDD1`). En enviar el formulari, l’app recorre la carpeta i escriu directament a la base de dades (sense generar CSV). En discs grans (5–8 TB, molts fitxers) el procés pot durar diversos minuts.
- **Actualitzar un disc:** Premeu **Actualitzar** a la fitxa del disc (o indiqueu el mateix *drive_id* al formulari). Es torna a recórrer el disc i es fusiona: fitxers nous afegits, modificats actualitzats, desapareguts marcats; els hashes dels fitxers no modificats es conserven. La casella **Reiniciar el disc** esborra i torna a crear l'inventari d'aquell *drive_id* (perd els hashes).
- **Hash per fitxer (SHA-256):** procés a part, incremental i reprenible, amb `python tools/hash_files.py --drive EXT-A --root F:\` (vegeu `docs/escaneig_i_hash.md`). La pàgina **Discs** mostra el percentatge de fitxers amb hash de cada disc.
- **Migrar una DB antiga:** si la base de dades ve d'una versió anterior (sense taula `drives`), executeu un cop `python -m app.migrate` (opcionalment `--legacy-audits <carpeta amb els audit_log.txt>`). L'app avisa amb un error 503 fins que es faci.

## Rutes

| Ruta | Descripció |
|------|-------------|
| `/` | Inici: si no hi ha DB, benvinguda; si hi ha DB, dashboard (resum per disc, top extensions, top carpetes) |
| `/search` | Cerca per paraula clau (nom o ruta); filtre opcional per disc |
| `/duplicates/name` | Fitxers amb el mateix nom en més d'un disc |
| `/duplicates/name-size` | Fitxers amb el mateix nom i mida en més d'un disc |
| `/extensions` | Llistat d'estadístiques per extensió (paginat) |
| `/folders` | Llistat de carpetes per mida (paginat) |
| `/explorador` | Explorador simulat d'arxius i carpetes des de la DB (arbre + contingut) |
| `/discs` | Gestió de discs: auditar i incorporar o actualitzar un disc |
| `/health` | Comprova si la DB existeix (retorn JSON) |

## Documentació

La carpeta **`docs/`** conté la documentació en format Markdown. Si actives **GitHub Pages** al repositori (Settings → Pages → Deploy from a branch → branch **main**, carpeta **/docs**), es publicarà a `https://elmoromussa.github.io/discscope/`. Instruccions detallades: [docs/CONFIGURAR_GITHUB.md](docs/CONFIGURAR_GITHUB.md).

## Git i GitHub

El fitxer `.gitignore` exclou `data/`, `venv/`, `*.db` i `.env`, de manera que en fer push no es pugen dades personals ni l'entorn virtual. Nom recomanat per al repositori: **DiscScope** (o `discscope` en minúscules). Per instruccions per pujar el projecte a GitHub, vegeu la guia a continuació.

## Notes

- La base de dades es **llegeix** des de Cercar, Duplicats, Extensions, Carpetes i Explorador; es **escriu** des de **Discs** (escaneig directe o import des de carpeta d’audit en CSV) i des de `tools/hash_files.py`.
- Esquema versió 2: taules `drives` (fitxa i comptadors per disc), `scans` (historial d'escaneigs) i columnes de hash i seguiment a `inventory`; `folder_path` és relatiu a l'arrel escanejada i els fitxers sense extensió tenen `(no_ext)`.
- Les cerques i llistats grans fan servir **paginació** per no sobrecarregar la memòria.
- Si la DB no existeix, les rutes que la requereixen retornen error 503; la pàgina d’inici i **Discs** es carreguen sense DB.
