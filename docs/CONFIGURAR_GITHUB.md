# Configurar el repositori a GitHub

Aquest document indica què posar a la secció **About** del repo i com activar la **pàgina de documentació** (GitHub Pages).

---

## 1. Secció About (descripció i topics)

A la pàgina del repositori [github.com/elmoromussa/discaleg](https://github.com/elmoromussa/discaleg):

1. A la columna de la dreta, clica la **pinyeta** al costat de "About".
2. **Description:** enganxa aquest text (o adapta’l):

   ```
   Aplicació web local per inventariar discs i carpetes. Audita directoris, desa a SQLite i permet cercar, duplicats, estadístiques i explorador. FastAPI + Python.
   ```

3. **Topics (etiquetes):** afegeix-ne algunes per fer el repo més fàcil de trobar, per exemple:

   ```
   python, fastapi, sqlite, disk-inventory, file-manager, catalan
   ```

4. Desa amb **Save changes**.

---

## 2. Activar la pàgina de documentació (GitHub Pages)

1. Al repositori, ves a **Settings** → **Pages** (menú esquerra).
2. A **Build and deployment**, a **Source** tria: **Deploy from a branch**.
3. A **Branch** tria: **main** / **/docs**.
4. Clica **Save**.

En uns minuts la documentació estarà disponible a:

**https://elmoromussa.github.io/discaleg/**

Pots afegir aquest enllaç a la descripció del repo (camp **Website** de la secció About) si vols.
