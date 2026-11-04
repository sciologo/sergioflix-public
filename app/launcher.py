"""Lanzador para este PC. Reutiliza el servicio existente y abre el panel autenticado."""
import subprocess
import sys
import time
import webbrowser
from pathlib import Path

from app.settings import ROOT,Settings
from app.storage import Store
from app.admin_session import AdminSession
from app.supervisor import healthy, STARTUP_TIMEOUT

TASK_NAME = "SergioFlix"


def main():
    flags=subprocess.CREATE_NO_WINDOW if sys.platform=='win32' else 0
    if not healthy():
        print('Iniciando el servicio vigilado de la videoteca…',flush=True)
        scheduled = False
        if sys.platform == 'win32':
            query = subprocess.run(['schtasks','/Query','/TN',TASK_NAME],creationflags=flags,capture_output=True)
            if query.returncode == 0:
                scheduled = subprocess.run(['schtasks','/Run','/TN',TASK_NAME],creationflags=flags,capture_output=True).returncode == 0
        if not scheduled:
            # Respaldo para una instalación que todavía no ejecutó el acceso de inicio
            # automático. El supervisor sigue siendo uno solo gracias a su mutex.
            subprocess.Popen([sys.executable,'-m','app.supervisor'],cwd=ROOT,
                stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,stdin=subprocess.DEVNULL,creationflags=flags)
        deadline=time.monotonic()+STARTUP_TIMEOUT
        while time.monotonic()<deadline:
            if healthy():break
            time.sleep(.5)
        if not healthy(): raise SystemExit('El servicio no pudo iniciar. Revisa Docker Desktop y logs/server.log.')
    if '--no-browser' not in sys.argv:
        token=AdminSession(Store(Settings())).issue()
        webbrowser.open('http://127.0.0.1:8765/#access='+token)
    print('Aplicación disponible. Mantén el PC y Docker Desktop encendidos.')


if __name__=='__main__': main()
