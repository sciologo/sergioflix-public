"""Un disco lento no puede detener la API ni crecer sin límite en memoria."""
import json
import logging
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from app import observability


@pytest.fixture
def audit_writer(monkeypatch, tmp_path):
    original_get_logger = logging.getLogger
    isolated = logging.Logger("test.audit")
    monkeypatch.setattr(observability, "ROOT", tmp_path)
    monkeypatch.setattr(observability, "_logger", None)
    monkeypatch.setattr(observability, "_listener", None)
    monkeypatch.setattr(logging, "getLogger", lambda name=None:
        isolated if name == "android_video.audit" else original_get_logger(name))
    yield tmp_path / "logs" / "audit.jsonl"
    observability._shutdown_audit()


def test_health_responds_even_when_first_audit_file_open_is_blocked(audit_writer, monkeypatch):
    from test_api import client
    entered, release = threading.Event(), threading.Event()
    original_open = observability._AuditFileHandler._open

    def slow_open(handler):
        entered.set()
        assert release.wait(10), "La prueba debe liberar el disco simulado."
        return original_open(handler)

    monkeypatch.setattr(observability._AuditFileHandler, "_open", slow_open)
    with client() as browser, ThreadPoolExecutor(max_workers=1) as pool:
        request = pool.submit(browser.get, "/health")
        try:
            assert entered.wait(5)
            # El escritor sigue bloqueado: ambas respuestas deben salir antes de liberarlo.
            assert request.result(timeout=2).status_code == 200
            assert browser.get("/health").status_code == 200
        finally:
            release.set()
    observability._shutdown_audit()
    rows = [json.loads(line) for line in audit_writer.read_text(encoding="utf-8").splitlines()]
    assert len([row for row in rows if row["event"] == "request"]) == 2


def test_full_audit_queue_stays_bounded_and_reports_lost_events(audit_writer, monkeypatch):
    entered, release = threading.Event(), threading.Event()
    original_emit = observability._AuditFileHandler.emit
    monkeypatch.setattr(observability, "QUEUE_CAPACITY", 2)

    def slow_emit(handler, record):
        entered.set()
        assert release.wait(10)
        original_emit(handler, record)

    monkeypatch.setattr(observability._AuditFileHandler, "emit", slow_emit)
    observability.audit("first")
    try:
        assert entered.wait(5)
        for _ in range(5):
            observability.audit("while_disk_blocked")
        handler = observability.make_logger().handlers[0]
        assert handler.queue.qsize() == 2
        assert handler.dropped == 3
    finally:
        release.set()
    handler.queue.join()
    observability.audit("recovered", url="https://private.invalid/token")
    observability._shutdown_audit()
    rows = [json.loads(line) for line in audit_writer.read_text(encoding="utf-8").splitlines()]
    assert [row["event"] for row in rows] == [
        "first", "while_disk_blocked", "while_disk_blocked", "audit_queue_overflow", "recovered"]
    assert rows[-2]["count"] == 3
    assert "private.invalid" not in audit_writer.read_text(encoding="utf-8")


def test_concurrent_initialization_writes_every_event_once_and_drains_on_close(audit_writer):
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(lambda index: observability.audit("concurrent", request_id=str(index)), range(100)))
    assert len(observability.make_logger().handlers) == 1
    observability._shutdown_audit()
    rows = [json.loads(line) for line in audit_writer.read_text(encoding="utf-8").splitlines()]
    assert len(rows) == 100
    assert {row["request_id"] for row in rows} == {str(index) for index in range(100)}


def test_rotation_still_keeps_valid_json_without_repeating_records(audit_writer):
    observability.make_logger()
    observability._listener.handlers[0].maxBytes = 500
    for index in range(12):
        observability.audit("rotation", request_id=str(index))
    observability._shutdown_audit()
    files = list(audit_writer.parent.glob("audit.jsonl*"))
    assert 1 < len(files) <= 5
    rows = [json.loads(line) for path in files for line in path.read_text(encoding="utf-8").splitlines()]
    assert len(rows) == 12
    assert {row["request_id"] for row in rows} == {str(index) for index in range(12)}
