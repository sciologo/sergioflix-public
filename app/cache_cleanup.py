"""Limpiar únicamente directorios generados por esta app, conservando sesiones activas."""
import re
import shutil
import time
from pathlib import Path


def cleanup(root:Path,protected:set[str],budget:int=48*1024**3):
    root=root.resolve()
    entries=[]
    for folder in root.iterdir():
        if not folder.is_dir() or folder.is_symlink() or not re.fullmatch(r'[A-Za-z0-9_-]{20,80}',folder.name):continue
        resolved=folder.resolve()
        if resolved.parent!=root:continue
        files=[f for f in folder.rglob('*') if f.is_file() and not f.is_symlink() and f.resolve().is_relative_to(root)]
        entries.append((max([f.stat().st_mtime for f in files]+[folder.stat().st_mtime]),sum(f.stat().st_size for f in files),folder))
    total=sum(row[1] for row in entries)
    for modified,size,folder in sorted(entries,key=lambda row:row[0]):
        if folder.name in protected:continue
        if total<=budget and time.time()-modified<48*3600:continue
        # Última comprobación antes de borrar: no salir de la raíz de caché del proyecto.
        if folder.resolve().parent!=root or folder.is_symlink():continue
        shutil.rmtree(folder)
        total-=size
