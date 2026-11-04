"""Mantiene disponible el servicio local sin convertir la aplicación en un servicio complejo."""
import ctypes
import json
import logging
import socket
import subprocess
import sys
import time
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler

import httpx

from app.settings import ROOT

CHECK_INTERVAL = 5
HEALTH_TIMEOUT = 5
STARTUP_TIMEOUT = 120
SETUP_TIMEOUT = 180
MAX_MISSES = 3
logger = logging.getLogger("android_video.supervisor")


def _record(event, **fields):
    # Solo causas controladas y números; nunca comandos, URLs o secretos.
    logger.info(json.dumps({"at": datetime.now(timezone.utc).isoformat(),
                            "event": event, **fields}))


def healthy() -> bool:
    try:
        return httpx.get("http://127.0.0.1:8765/health", timeout=HEALTH_TIMEOUT,
                         trust_env=False).status_code == 200
    except httpx.HTTPError:
        return False


def _ports_in_use() -> bool:
    """No lanzar otro backend sobre una instancia que aún conserva sus puertos."""
    for port in (8765, 8443):
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=1):
                return True
        except OSError:
            pass
    return False


def _watch(process=None) -> str:
    """El arranque tiene su plazo; una instancia lista tolera fallos transitorios."""
    started = time.monotonic()
    ready = process is None  # Una instancia ajena solo se adopta después de un health OK.
    misses = 0
    while process is None or process.poll() is None:
        if healthy():
            if not ready:
                _record("backend_ready", pid=process.pid,
                        elapsed_ms=round((time.monotonic()-started)*1000))
            ready, misses = True, 0
        elif not ready:
            if time.monotonic()-started >= STARTUP_TIMEOUT:
                return "startup_timeout"
        else:
            misses += 1
            _record("health_failed", pid=process.pid if process else None, count=misses)
            if misses >= MAX_MISSES:
                return "health_timeout"
        time.sleep(CHECK_INTERVAL)
    return "process_exit"


def _single_instance() -> bool:
    """Un mutex de Windows evita dos vigilantes y dos servidores sobre los mismos puertos."""
    if sys.platform != "win32":
        return True
    ctypes.windll.kernel32.CreateMutexW(None, False, "Local\\SergioFlixSupervisor")
    return ctypes.windll.kernel32.GetLastError() != 183


def _run_logged(arguments: list[str], log) -> int:
    flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    try:
        return subprocess.run(
            arguments, cwd=ROOT, stdout=log, stderr=log, stdin=subprocess.DEVNULL,
            creationflags=flags, check=False, timeout=SETUP_TIMEOUT,
        ).returncode
    except (OSError, subprocess.TimeoutExpired):
        # El siguiente ciclo puede recuperar Docker sin dejar el vigilante detenido.
        return 124


def _terminate(process):
    """Termina únicamente el hijo propio y sus conversiones; no deja FFmpeg huérfano."""
    if process.poll() is not None:
        return True
    try:
        if sys.platform == "win32":
            result = subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                           creationflags=subprocess.CREATE_NO_WINDOW, timeout=10, check=False)
            if result.returncode and process.poll() is None:
                _record("backend_stop_failed", pid=process.pid, reason="tree_termination_failed")
                return False
        else:
            process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=10)
    except (OSError, subprocess.SubprocessError):
        _record("backend_stop_failed", pid=process.pid, reason="termination_failed")
        return False
    return True


def main() -> None:
    if not _single_instance():
        return
    logs = ROOT / "logs"
    logs.mkdir(exist_ok=True)
    (ROOT / "private").mkdir(exist_ok=True)
    # Archivo independiente: dos procesos no deben rotar el mismo audit.jsonl.
    handler = RotatingFileHandler(logs / "supervisor.jsonl", maxBytes=1_000_000,
                                  backupCount=3, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False
    _record("supervisor_started")
    flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    while True:
        # Si una instancia anterior sigue sana, se la respeta y solo se la vigila.
        if healthy():
            _record("backend_reused")
            _record("unmanaged_backend_unhealthy", reason=_watch())
        if _ports_in_use():
            _record("restart_deferred", reason="port_in_use")
            time.sleep(30)
            continue
        with (logs / "server.log").open("ab") as log:
            result = _run_logged(["docker", "compose", "up", "-d", "--wait"], log)
            if result:
                _record("setup_failed", operation="postgres_start", exit_code=result)
                time.sleep(30)
                continue
            result = _run_logged([sys.executable, "-m", "app.manage", "migrate"], log)
            if result:
                _record("setup_failed", operation="migrate", exit_code=result)
                time.sleep(30)
                continue
            # Docker y la migración pueden tardar: otra instancia pudo recuperarse.
            if healthy() or _ports_in_use():
                _record("restart_deferred", reason="backend_recovered_or_port_in_use")
                time.sleep(CHECK_INTERVAL)
                continue
            process = subprocess.Popen(
                [sys.executable, "-m", "app.manage", "serve"], cwd=ROOT,
                stdout=log, stderr=log, stdin=subprocess.DEVNULL, creationflags=flags,
            )
            (ROOT / "private" / "server.pid").write_text(str(process.pid), encoding="ascii")
            _record("backend_started", pid=process.pid)
            reason = _watch(process)
            _record("backend_restart", pid=process.pid, reason=reason, exit_code=process.poll())
            # Si Windows no permite detenerlo todavía, se reintenta sobre el mismo
            # hijo. Nunca se pierde su control para abrir otra instancia encima.
            while not _terminate(process):
                time.sleep(CHECK_INTERVAL)
        time.sleep(CHECK_INTERVAL)


if __name__ == "__main__":
    main()
