# Arxiu virtual: el pla per a l'executor (`pla_arxiu.json`)

L'arxiu virtual (espais d'arxiu i membres amb paper, vegeu `docs/migracio.md` «Arxiu virtual» i
`docs/usage.md`) s'exporta amb

```bash
python tools/espais.py --export-plan data/pla_arxiu.json
```

a un sol fitxer JSON (UTF-8, rutes amb `\`), que és l'entrada de `tools/executor.py` (pas 4 de
`docs/eines-migracio.md`: còpies per blocs amb registre, verificació per hash, manifests, README
esquelet, segellat, quarantena). L'exportació es nega si `--check` té errors (noms fora de regla,
jerarquia, papers contradictoris); `--force` l'escriu igualment. Els avisos no bloquegen.

El pla només descriu; no mou res. L'executor ha de tornar a comprovar contra la DB i el disc
abans de cada bloc (serial del volum, espai lliure, que l'origen encara hi siga).

## Estructura

| Clau | Contingut |
|---|---|
| `format` | `discaleg-pla-arxiu/1` |
| `generated_utc`, `database`, `schema_version` | quan, de quina DB i amb quina versió d'esquema |
| `conflicts` | `{errors, avisos}` de `--check` en el moment d'exportar |
| `drives` | per `drive_id`: `label`, `serial_number`, `volume_serial`, `filesystem`, `last_root_path`, `capacity_bytes`, `files_count`, `total_bytes` (la identitat que l'executor comprova abans de llegir o escriure) |
| `spaces` | tots els espais d'arxiu, ordenats per ruta virtual: `space_id`, `kind` (`fons`/`serie`/`projecte`/`capa`/`carpeta`), `target_name`, `target_path` (ruta de destí relativa a l'arrel del disc d'arxiu, p. ex. `ARXIU\diablo-wasi\2021_tambillo\RAW`), `parent_id`, `fons_id`, `serie_id`, `project_id`, `layer`, `state`, `canonical_drive_id`, `description`, `source` |
| `projects` | un per espai `projecte`: `target_path`, `name`, `year`, `serie`, `state`, `canonical_drive_id` (el del projecte o, si no, el de la sèrie), `layers` (`{RAW: space_id, …}`), totals (`files`, `bytes`, `unique_bytes`), `members` (els identificadors de cada llista que cauen dins del projecte) i `sources` (carpetes d'origen de les còpies: la «procedència» del README) |
| `copiar` | membres **canònica**, **part** i **tercers**: el que s'ha de copiar al destí |
| `verificar` | membres **còpia**: no es copien; es verifiquen contra la canònica (`canonical`: identificadors de les canòniques del mateix projecte, o del mateix espai si no hi ha projecte) i, un cop verificades, s'esborren amb registre |
| `esborrar` | membres **descartar**: s'esborren amb registre (després del pas 0, per blocs, amb `quarantena.csv`) |
| `quarantena` | membres **quarantena**: van a `_QUARANTENA\AAAA-MM-DD\` |
| `pendents` | membres **sense paper**: no es fa res fins que es decidisca |

Cada entrada de les cinc llistes té:

| Camp | Contingut |
|---|---|
| `id` | `m<item_id>-s<space_id>`, estable mentre no canvie la pertinença |
| `item_id`, `space_id`, `role` | identitat a la DB |
| `source.drive_id`, `source.path`, `source.kind` | carpeta (o fitxer) d'origen, ruta relativa a l'arrel escanejada del disc |
| `source.exclude` | subcarpetes **que no s'han de copiar amb aquest membre** perquè són membres d'un altre espai (regla del membre més profund): rutes relatives a la carpeta d'origen, per a `robocopy /XD` |
| `source.nested_in` | si aquesta carpeta és dins d'una altra carpeta membre, quina (informatiu) |
| `target_path`, `target_subpath`, `destination` | l'espai de destí, la subcarpeta dins de l'espai (`null` = arrel; `.` = el contingut va a l'arrel sense la carpeta d'origen) i la ruta de destí final de la carpeta |
| `project_id`, `project_path`, `layer` | projecte i capa (RAW/PROJ/OUT/DOC) on cau; `null` per als membres directes d'un fons (`PERSONAL`, `_TERCERS`…) |
| `files`, `bytes`, `unique_bytes` | fitxers i bytes que pertanyen al membre (sense els exclosos) i bytes sense còpia en cap altre disc (criteri del catàleg) |
| `note` | la nota del membre |

Rutes de destí: `destination` és relativa a l'arrel del disc d'arxiu (`canonical_drive_id` del
projecte o de la sèrie; si és `null`, l'executor ho demana). El primer component és el fons
(`ARXIU`, `PERSONAL`, `_TERCERS`, `_EINES`, `_DESCARTAR`).

## Exemple (retallat)

```json
{
 "format": "discaleg-pla-arxiu/1",
 "generated_utc": "2026-10-15T00:41:12+00:00",
 "schema_version": 14,
 "conflicts": {"errors": 0, "avisos": 75},
 "drives": {"EXT-F": {"label": "EXTF", "serial_number": "WX…", "volume_serial": "1A2B-3C4D",
                      "filesystem": "NTFS", "last_root_path": "L:\\", "capacity_bytes": 2000000000000,
                      "files_count": 64511, "total_bytes": 1933774557375}},
 "spaces": [
  {"space_id": 6, "kind": "projecte", "target_name": "2021_tambillo",
   "target_path": "ARXIU\\diablo-wasi\\2021_tambillo", "parent_id": 2, "fons_id": 1, "serie_id": 2,
   "project_id": 6, "layer": null, "state": null, "canonical_drive_id": null, "source": "import"},
  {"space_id": 7, "kind": "capa", "target_name": "RAW", "target_path": "ARXIU\\diablo-wasi\\2021_tambillo\\RAW",
   "parent_id": 6, "fons_id": 1, "serie_id": 2, "project_id": 6, "layer": "RAW", "state": null}
 ],
 "projects": [
  {"space_id": 6, "target_path": "ARXIU\\diablo-wasi\\2021_tambillo", "name": "2021_tambillo", "year": 2021,
   "serie": "diablo-wasi", "state": null, "canonical_drive_id": null, "layers": {"RAW": 7, "PROJ": 8, "OUT": 9},
   "files": 640816, "bytes": 10732848646557, "unique_bytes": 4520575689847,
   "members": {"copiar": ["m12-s7", "m19-s7"], "verificar": ["m13-s7"], "esborrar": ["m36-s6"],
               "quarantena": [], "pendents": []},
   "sources": ["EXT-D\\nou_TAMBILLO2021", "EXT-F\\DH2PTGUI"]}
 ],
 "copiar": [
  {"id": "m12-s7", "item_id": 12, "space_id": 7, "role": "canonica",
   "source": {"drive_id": "EXT-D", "path": "nou_TAMBILLO2021", "kind": "folder", "exclude": [], "nested_in": null},
   "target_path": "ARXIU\\diablo-wasi\\2021_tambillo\\RAW", "target_subpath": null,
   "destination": "ARXIU\\diablo-wasi\\2021_tambillo\\RAW\\nou_TAMBILLO2021",
   "project_id": 6, "project_path": "ARXIU\\diablo-wasi\\2021_tambillo", "layer": "RAW",
   "files": 32110, "bytes": 1209876543210, "unique_bytes": 1234567, "note": "RAW per dies 2021-11-01…12-03"},
  {"id": "m19-s7", "item_id": 19, "space_id": 7, "role": "part",
   "source": {"drive_id": "EXT-F", "path": "DH2PTGUI", "kind": "folder", "exclude": [], "nested_in": null},
   "target_path": "ARXIU\\diablo-wasi\\2021_tambillo\\RAW", "target_subpath": "processades",
   "destination": "ARXIU\\diablo-wasi\\2021_tambillo\\RAW\\processades\\DH2PTGUI",
   "project_id": 6, "project_path": "ARXIU\\diablo-wasi\\2021_tambillo", "layer": "RAW",
   "files": 4100, "bytes": 152500000000, "unique_bytes": 151000000000, "note": "panorames 360 2022"}
 ],
 "verificar": [
  {"id": "m13-s7", "item_id": 13, "space_id": 7, "role": "copia",
   "source": {"drive_id": "INT-5", "path": "DW23_TFM26\\RAW_DW21", "kind": "folder", "exclude": [], "nested_in": null},
   "target_path": "ARXIU\\diablo-wasi\\2021_tambillo\\RAW", "target_subpath": null,
   "destination": "ARXIU\\diablo-wasi\\2021_tambillo\\RAW\\RAW_DW21",
   "project_id": 6, "project_path": "ARXIU\\diablo-wasi\\2021_tambillo", "layer": "RAW",
   "files": 32110, "bytes": 1209876543210, "unique_bytes": 0, "note": "segona còpia (INT-5)",
   "canonical": ["m12-s7"]}
 ],
 "esborrar": [
  {"id": "m36-s6", "item_id": 36, "space_id": 6, "role": "descartar",
   "source": {"drive_id": "INT-2", "path": "RECOVERY_RAID5\\FOTO-VIDEO-RAID5\\foto-video_raid5", "kind": "folder",
              "exclude": [], "nested_in": "INT-2\\RECOVERY_RAID5"},
   "target_path": "ARXIU\\diablo-wasi\\2021_tambillo", "target_subpath": null,
   "destination": "ARXIU\\diablo-wasi\\2021_tambillo\\foto-video_raid5",
   "project_id": 6, "project_path": "ARXIU\\diablo-wasi\\2021_tambillo", "layer": null,
   "files": 1200, "bytes": 480700000000, "unique_bytes": 480700000000, "note": "imatge de disc del rescat"}
 ],
 "quarantena": [],
 "pendents": []
}
```

## Com l'ha de llegir l'executor (pas 4)

1. Comprovar `drives` contra el maquinari connectat (serial del volum i número de sèrie, com fa
   `hash_files.py`) i l'espai lliure del disc d'arxiu.
2. Per cada projecte amb `canonical_drive_id`, crear `target_path` i les capes; copiar les
   entrades de `copiar` del projecte (`robocopy` amb els paràmetres del model §9 i `/XD` per a
   `source.exclude`), de la font a `destination` (amb `target_subpath = "."` el contingut va
   directament a `target_path`). Registre a `data/logs/` i esdeveniment `copia` a `events`.
3. En un procés a part, SHA-256 rellegint origen i destí; resultat a `copies` i `events`.
4. Les entrades de `verificar` es comparen amb les seues `canonical` ja copiades (per hash); si
   tot hi és, passen a la llista d'esborrar amb registre; si hi ha fitxers que falten, es tracten
   com a `part` (decisió humana).
5. `esborrar` i `quarantena`: només després del pas 0 i amb la còpia verificada, per blocs, amb
   `quarantena.csv` i esdeveniment.
6. Per projecte: `manifest-sha256.txt`, `manifest-mides.txt`, README esquelet (`name`, `year`,
   `serie`, `sources`, dispositius si hi ha exiftool), segellat (`icacls`) i `snapraid sync`.

`pendents` es mostra i no s'executa.
