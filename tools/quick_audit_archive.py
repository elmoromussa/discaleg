import argparse
import csv
import hashlib
import os
import sys
import shutil
import subprocess
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

def utc_iso(dt_ts: float) -> str:
    """Converteix timestamp Unix a ISO UTC. Retorna '' si el valor és invàlid (p.ex. a Windows)."""
    try:
        return datetime.fromtimestamp(dt_ts, tz=timezone.utc).isoformat()
    except (OSError, ValueError, OverflowError):
        return ""

def ensure_dir(p: Path) -> None:
    p.mkdir(parents=True, exist_ok=True)

def write_csv(path: Path, fieldnames: list[str], rows: list[dict]) -> None:
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(rows)

def sha256_file(path: Path, bufsize: int = 1024 * 1024) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while True:
            chunk = f.read(bufsize)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()

def run_cmd_to_file(cmd: list[str], out_file: Path) -> tuple[bool, str]:
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, check=False)
        if res.returncode != 0:
            return False, f"ERROR ({res.returncode}): {res.stderr.strip()}"
        out_file.write_text(res.stdout, encoding="utf-8", errors="replace")
        return True, "OK"
    except FileNotFoundError:
        return False, "ERROR: command not found"
    except Exception as e:
        return False, f"ERROR: {e}"

def run_audit(root: Path, reports_parent: Path, full: bool) -> Path | None:
    """Executa l'audit. Retorna la carpeta d'informes creada si ha anat bé, None si hi ha hagut error."""
    root = root.resolve()
    reports_parent = reports_parent.resolve()

    if not root.exists() or not root.is_dir():
        print(f"Root folder not found or not a directory: {root}")
        return None

    stamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    out_dir = reports_parent / f"audit_{stamp}"
    ensure_dir(out_dir)

    log_lines = []
    def log(msg: str):
        log_lines.append(msg)
        print(msg)

    log(f"ROOT: {root}")
    log(f"OUT:  {out_dir}")
    if full:
        log("Mode: FULL (hashes, Siegfried, ExifTool)")

    # 1) INVENTORY (filesystem) — streaming, light columns
    ext_stats = defaultdict(lambda: {"Count": 0, "TotalBytes": 0})
    folder_agg = defaultdict(lambda: {"FilesCount": 0, "TotalBytes": 0})
    all_files: list[Path] = []  # only populated when --full

    inv_path = out_dir / "inventory_files.csv"
    inv_fieldnames = ["RelativePath", "Name", "Extension", "SizeBytes", "ModifiedUtc"]
    file_count = 0

    with inv_path.open("w", newline="", encoding="utf-8") as inv_f:
        inv_writer = csv.DictWriter(inv_f, fieldnames=inv_fieldnames)
        inv_writer.writeheader()
        for dirpath, _dirnames, filenames in os.walk(root):
            d = Path(dirpath)
            for fn in filenames:
                p = d / fn
                try:
                    st = p.stat()
                except OSError:
                    continue

                rel = p.relative_to(root)
                ext = p.suffix.lower()

                mtime_str = utc_iso(st.st_mtime)
                inv_writer.writerow({
                    "RelativePath": str(rel),
                    "Name": p.name,
                    "Extension": ext,
                    "SizeBytes": st.st_size,
                    "ModifiedUtc": mtime_str,
                })

                ext_stats[ext]["Count"] += 1
                ext_stats[ext]["TotalBytes"] += st.st_size
                folder_agg[str(d)]["FilesCount"] += 1
                folder_agg[str(d)]["TotalBytes"] += st.st_size

                if full:
                    all_files.append(p)
                file_count += 1

    log(f"Wrote: inventory_files.csv ({file_count} files)")

    # 1b) by_extension
    by_ext_rows = []
    for ext, v in ext_stats.items():
        by_ext_rows.append({"Extension": ext if ext else "(no_ext)", "Count": v["Count"], "TotalBytes": v["TotalBytes"]})
    by_ext_rows.sort(key=lambda r: r["Count"], reverse=True)

    write_csv(out_dir / "by_extension.csv", ["Extension", "Count", "TotalBytes"], by_ext_rows)
    log("Wrote: by_extension.csv")

    # 2) folders_stats (direct folder agg + depth)
    folders_rows = []
    for folder_path, v in folder_agg.items():
        fp = Path(folder_path)
        try:
            rel = fp.relative_to(root)
            depth = len(rel.parts)
        except Exception:
            depth = None
        folders_rows.append({
            "FolderPath": folder_path,
            "FolderDepth": depth,
            "FilesCount": v["FilesCount"],
            "TotalBytes": v["TotalBytes"],
        })
    folders_rows.sort(key=lambda r: (r["TotalBytes"] or 0), reverse=True)
    write_csv(out_dir / "folders_stats.csv", ["FolderPath", "FolderDepth", "FilesCount", "TotalBytes"], folders_rows)
    log("Wrote: folders_stats.csv")

    # 3) hashes + duplicates (only in --full mode)
    if full:
        hash_rows = []
        by_hash = defaultdict(list)

        for i, p in enumerate(all_files, start=1):
            try:
                st = p.stat()
                h = sha256_file(p)
            except Exception as e:
                log(f"Hash failed: {p} ({e})")
                continue

            row = {"FullPath": str(p), "SizeBytes": st.st_size, "SHA256": h}
            hash_rows.append(row)
            by_hash[h].append(row)

            if i % 100 == 0:
                log(f"Hashed {i}/{len(all_files)}...")

        write_csv(out_dir / "hashes_sha256.csv", ["FullPath", "SizeBytes", "SHA256"], hash_rows)
        log("Wrote: hashes_sha256.csv")

        dup_rows = []
        for h, rows in by_hash.items():
            if len(rows) > 1:
                for r in rows:
                    dup_rows.append({"SHA256": h, "SizeBytes": r["SizeBytes"], "FullPath": r["FullPath"]})
        write_csv(out_dir / "duplicates_by_hash.csv", ["SHA256", "SizeBytes", "FullPath"], dup_rows)
        log(f"Wrote: duplicates_by_hash.csv ({len(dup_rows)} entries)")

        # 4) optional: Siegfried
        if shutil.which("sf"):
            ok, msg = run_cmd_to_file(["sf", "-csv", str(root)], out_dir / "siegfried_formats.csv")
            log(f"Siegfried: {msg} -> siegfried_formats.csv" if ok else f"Siegfried: {msg}")
        else:
            log("Siegfried not found (sf). Skipping format signatures step.")

        # 5) optional: ExifTool
        if shutil.which("exiftool"):
            cmd = [
                "exiftool", "-r", "-csv",
                "-FileName", "-Directory", "-FileSize", "-FileType", "-FileTypeExtension",
                "-CreateDate", "-ModifyDate", "-DateTimeOriginal",
                "-Make", "-Model", "-Software",
                "-XResolution", "-YResolution", "-ResolutionUnit",
                "-ImageWidth", "-ImageHeight",
                "-MimeType",
                str(root)
            ]
            ok, msg = run_cmd_to_file(cmd, out_dir / "exiftool_metadata.csv")
            log(f"ExifTool: {msg} -> exiftool_metadata.csv" if ok else f"ExifTool: {msg}")
        else:
            log("ExifTool not found (exiftool). Skipping embedded metadata step.")

    # write log
    (out_dir / "audit_log.txt").write_text("\n".join(log_lines), encoding="utf-8")
    log("Wrote: audit_log.txt")
    log("DONE.")
    return out_dir


def run_gui() -> None:
    """Obre una petita GUI per triar carpetes i llançar l'audit."""
    root_tk = tk.Tk()
    root_tk.title("Audit d'arxiu digital")
    root_tk.resizable(True, False)
    root_tk.minsize(420, 1)

    frm = ttk.Frame(root_tk, padding=12)
    frm.grid(row=0, column=0, sticky=(tk.W, tk.E))
    root_tk.columnconfigure(0, weight=1)
    frm.columnconfigure(1, weight=1)

    ttk.Label(frm, text="Carpeta a auditar (disc/carpeta arrel):").grid(row=0, column=0, columnspan=2, sticky=tk.W, pady=(0, 4))
    root_folder_var = tk.StringVar()
    root_entry = ttk.Entry(frm, textvariable=root_folder_var, width=50)
    root_entry.grid(row=1, column=0, columnspan=2, sticky=(tk.W, tk.E), pady=(0, 8))

    def browse_root() -> None:
        path = filedialog.askdirectory(title="Tria la carpeta (o disc) a auditar", parent=root_tk)
        if path:
            root_folder_var.set(path)

    ttk.Button(frm, text="Examinar…", command=browse_root).grid(row=2, column=0, sticky=tk.W, pady=(0, 16))

    ttk.Label(frm, text="Carpeta on desar els informes:").grid(row=3, column=0, columnspan=2, sticky=tk.W, pady=(0, 4))
    reports_var = tk.StringVar()
    reports_entry = ttk.Entry(frm, textvariable=reports_var, width=50)
    reports_entry.grid(row=4, column=0, columnspan=2, sticky=(tk.W, tk.E), pady=(0, 8))

    def browse_reports() -> None:
        path = filedialog.askdirectory(title="Tria la carpeta on desar els informes", parent=root_tk)
        if path:
            reports_var.set(path)

    ttk.Button(frm, text="Examinar…", command=browse_reports).grid(row=5, column=0, sticky=tk.W, pady=(0, 16))

    full_var = tk.BooleanVar(value=False)
    ttk.Checkbutton(
        frm,
        text="Audit complet (hashes SHA256, duplicats, Siegfried, ExifTool)",
        variable=full_var,
    ).grid(row=6, column=0, columnspan=2, sticky=tk.W, pady=(0, 16))

    def start_audit() -> None:
        r = root_folder_var.get().strip()
        rp = reports_var.get().strip()
        if not r:
            messagebox.showwarning("Carpeta a auditar", "Selecciona la carpeta (o disc) a auditar.", parent=root_tk)
            return
        if not rp:
            messagebox.showwarning("Carpeta d'informes", "Selecciona la carpeta on desar els informes.", parent=root_tk)
            return
        root_path = Path(r)
        reports_path = Path(rp)
        if not root_path.exists() or not root_path.is_dir():
            messagebox.showerror("Error", f"La carpeta a auditar no existeix o no és una carpeta:\n{root_path}", parent=root_tk)
            return
        if not reports_path.exists() or not reports_path.is_dir():
            messagebox.showerror("Error", f"La carpeta d'informes no existeix o no és una carpeta:\n{reports_path}", parent=root_tk)
            return
        root_tk.withdraw()
        try:
            out_dir = run_audit(root_path, reports_path, full_var.get())
            if out_dir is not None:
                messagebox.showinfo("Audit acabat", f"Informe desat a:\n{out_dir}\n(consulta la consola per al log complet.)", parent=root_tk)
            else:
                messagebox.showerror("Error", "L'audit ha fallat. Revisa la consola.", parent=root_tk)
        finally:
            root_tk.deiconify()

    ttk.Button(frm, text="Iniciar audit", command=start_audit).grid(row=7, column=0, columnspan=2, pady=(0, 8))

    root_tk.mainloop()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Audit d'arxiu digital (estructura de carpetes i fitxers). Mode ràpid per defecte; --full per hashes, Siegfried i ExifTool. Sense arguments s'obre la GUI."
    )
    parser.add_argument("root_folder", nargs="?", type=Path, help="Carpeta arrel a auditar (opcional; sense args s'obre la GUI)")
    parser.add_argument("reports_parent", nargs="?", type=Path, help="Carpeta on desar els informes (opcional)")
    parser.add_argument("--full", action="store_true", help="Audit complet: hashes SHA256, duplicats, Siegfried, ExifTool")
    args = parser.parse_args()

    if args.root_folder is not None and args.reports_parent is not None:
        out_dir = run_audit(args.root_folder, args.reports_parent, args.full)
        sys.exit(0 if out_dir is not None else 1)
    run_gui()


if __name__ == "__main__":
    main()
