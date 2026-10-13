---
title: Configuració — Discàleg
layout: default
---

# Configuració

## Ubicació de la base de dades

Per defecte Discàleg busca la base de dades a:

```
<directori del projecte>/data/arxiu_discs.db
```

La carpeta `data/` es crea automàticament la primera vegada que audites un disc des de la secció **Discs**.

### Usar una ruta diferent

Si vols que la base de dades estigui en una altra ubicació (per exemple un disc extern), defineix la variable d'entorn **`DATABASE_PATH`**:

**Windows (CMD):**
```cmd
set DATABASE_PATH=H:\backups\arxiu_discs.db
uvicorn app.main:app --host 127.0.0.1 --port 8000
```

**Windows (PowerShell):**
```powershell
$env:DATABASE_PATH = "H:\backups\arxiu_discs.db"
uvicorn app.main:app --host 127.0.0.1 --port 8000
```

**Linux / macOS:**
```bash
export DATABASE_PATH=/ruta/arxiu_discs.db
uvicorn app.main:app --host 127.0.0.1 --port 8000
```

## Comprovar l'estat de la base de dades

Pots fer una petició a l'endpoint de salut:

```
GET http://127.0.0.1:8000/health
```

La resposta inclou `database_exists` i `database_path`.
