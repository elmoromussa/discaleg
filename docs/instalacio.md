---
title: Instal·lació — Discàleg
layout: default
---

# Instal·lació

## Requisits

- **Python 3.10** o superior
- Sistema operatiu: Windows, Linux o macOS

## Passos

### 1. Clonar o descomprimir el projecte

```bash
git clone https://github.com/elmoromussa/discaleg.git
cd discaleg
```

### 2. Crear l'entorn virtual (recomanat)

**Windows (PowerShell o CMD):**
```bash
python -m venv venv
venv\Scripts\activate
```

**Linux / macOS:**
```bash
python3 -m venv venv
source venv/bin/activate
```

### 3. Instal·lar dependències

```bash
pip install -r requirements.txt
```

### 4. Executar l'aplicació

**Windows (ràpid):** doble clic a `run_inventari_WIN.bat`. El script activa el venv, inicia el servidor i obre el navegador.

**Línia de comandes:**
```bash
uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Obre el navegador a **http://127.0.0.1:8000**.

---

La base de dades **no** ve amb el repositori. La primera vegada que facis servir l'app, ves a **Discs** i audita una carpeta; es crearà `data/arxiu_discs.db` automàticament.
