"""Auditoría con campos permitidos; jamás registra cuerpos, URLs o excepciones HTTP."""
import atexit
import json
import logging
import queue
import threading
from datetime import datetime, timezone
from logging.handlers import QueueHandler, QueueListener, RotatingFileHandler

from app.settings import ROOT

QUEUE_CAPACITY = 10_000
_logger = None
_listener = None
_setup_lock = threading.Lock()


class _AuditFileHandler(RotatingFileHandler):
    def _open(self):
        # Incluso el primer mkdir/open se ejecuta en el escritor, nunca en la API.
        from pathlib import Path
        Path(self.baseFilename).parent.mkdir(parents=True, exist_ok=True)
        return super()._open()


class _AuditQueueHandler(QueueHandler):
    def __init__(self, records):
        super().__init__(records)
        self.dropped = 0

    def enqueue(self, record):
        # Un disco detenido tampoco puede agotar la memoria ni bloquear peticiones.
        # Handler.handle serializa este contador; se informa al recuperarse la cola.
        try:
            if self.dropped:
                notice = logging.LogRecord(record.name, logging.WARNING, "", 0,
                    json.dumps({"at": datetime.now(timezone.utc).isoformat(),
                                "event": "audit_queue_overflow", "actor": "system",
                                "count": self.dropped}), (), None)
                self.queue.put_nowait(notice)
                self.dropped = 0
            self.queue.put_nowait(record)
        except queue.Full:
            self.dropped += 1


class _AuditListener(QueueListener):
    def enqueue_sentinel(self):
        # Al cerrar se drena la cola, aunque estuviese llena. Solo espera el cierre.
        self.queue.put(self._sentinel)


def _shutdown_audit():
    global _listener
    if _listener is not None:
        _listener.stop()
        for handler in _listener.handlers:
            handler.close()
        _listener = None


atexit.register(_shutdown_audit)


def make_logger() -> logging.Logger:
    global _logger, _listener
    if _logger is None:
        with _setup_lock:
            if _logger is None:
                logger = logging.getLogger("android_video.audit")
                records = queue.Queue(maxsize=QUEUE_CAPACITY)
                handler = _AuditFileHandler(ROOT / "logs" / "audit.jsonl", maxBytes=2_000_000,
                                           backupCount=4, encoding="utf-8", delay=True)
                handler.setFormatter(logging.Formatter("%(message)s"))
                _listener = _AuditListener(records, handler)
                logger.addHandler(_AuditQueueHandler(records))
                logger.setLevel(logging.INFO)
                logger.propagate = False
                # httpx incluye la URL en INFO; un manifiesto privado es una credencial.
                logging.getLogger("httpx").setLevel(logging.CRITICAL)
                logging.getLogger("httpcore").setLevel(logging.CRITICAL)
                _listener.start()
                _logger = logger
    return _logger


def audit(event: str, actor: str = "system", **fields) -> None:
    # Proveedor y cantidad permiten medir filtros sin registrar títulos, cuerpos ni URLs.
    allowed = {key: fields[key] for key in
               ("request_id", "operation", "status", "elapsed_ms", "error_code", "provider", "count",
                "scale", "offset_seconds", "quality", "max_residual_seconds", "samples", "reason",
                "target_ms", "offset_ms", "available_ms", "conversion", "operation_id",
                "video_id", "source_fingerprint", "subtitle_key",
                "position_ms", "ready_state", "paused", "seeking", "waiting")
               if key in fields}
    make_logger().info(json.dumps({"at": datetime.now(timezone.utc).isoformat(), "event": event, "actor": actor, **allowed}, ensure_ascii=False))
