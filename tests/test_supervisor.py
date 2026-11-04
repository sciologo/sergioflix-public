"""Arranque lento, fallos transitorios y recuperación sin duplicar servidores."""
from types import SimpleNamespace
import ctypes
import subprocess
import sys
import time

import httpx
import pytest

from app import supervisor


@pytest.fixture
def clock(monkeypatch):
    state = SimpleNamespace(now=0, records=[])
    monkeypatch.setattr(supervisor.time, "monotonic", lambda: state.now)
    monkeypatch.setattr(supervisor.time, "sleep", lambda seconds: setattr(state, "now", state.now+seconds))
    monkeypatch.setattr(supervisor, "_record", lambda event, **fields: state.records.append((event, fields)))
    return state


def process(exit_at=None, clock=None):
    return SimpleNamespace(pid=1234,
        poll=lambda: 7 if exit_at is not None and clock.now >= exit_at else None)


def test_startup_can_take_longer_than_old_watchdog_without_being_terminated(clock, monkeypatch):
    monkeypatch.setattr(supervisor, "healthy", lambda: 45 <= clock.now < 60)
    assert supervisor._watch(process()) == "health_timeout"
    ready = next(fields for event, fields in clock.records if event == "backend_ready")
    assert ready["elapsed_ms"] == 45_000
    assert clock.now == 70


def test_startup_deadline_and_process_exit_are_distinguished(clock, monkeypatch):
    monkeypatch.setattr(supervisor, "healthy", lambda: False)
    assert supervisor._watch(process()) == "startup_timeout"
    assert clock.now == supervisor.STARTUP_TIMEOUT
    clock.now = 0
    assert supervisor._watch(process(exit_at=10, clock=clock)) == "process_exit"
    assert clock.now == 10


@pytest.mark.parametrize("adopted", [False, True])
def test_recovery_resets_failures_for_owned_and_reused_backends(clock, monkeypatch, adopted):
    responses = iter([True, False, False, True, False, False, False])
    monkeypatch.setattr(supervisor, "healthy", lambda: next(responses))
    assert supervisor._watch(None if adopted else process()) == "health_timeout"
    assert clock.now == 30
    counts = [fields["count"] for event, fields in clock.records if event == "health_failed"]
    assert counts == [1, 2, 1, 2, 3]


def test_health_probe_ignores_environment_proxies_and_tolerates_short_delays(monkeypatch):
    calls = []
    def get(url, **kwargs):
        calls.append((url, kwargs))
        return SimpleNamespace(status_code=200)
    monkeypatch.setattr(supervisor.httpx, "get", get)
    assert supervisor.healthy()
    assert calls == [("http://127.0.0.1:8765/health", {"timeout": 5, "trust_env": False})]
    def timeout(*args, **kwargs):
        raise httpx.ReadTimeout("test")
    monkeypatch.setattr(supervisor.httpx, "get", timeout)
    assert not supervisor.healthy()


def test_setup_failure_is_bounded_and_does_not_crash_supervisor(monkeypatch):
    def timeout(*args, **kwargs):
        assert kwargs["timeout"] == supervisor.SETUP_TIMEOUT
        raise supervisor.subprocess.TimeoutExpired(args[0], kwargs["timeout"])
    monkeypatch.setattr(supervisor.subprocess, "run", timeout)
    assert supervisor._run_logged(["docker", "compose", "up"], None) == 124


@pytest.mark.parametrize("recovered,port_busy", [(True, False), (False, True)])
def test_does_not_launch_duplicate_if_backend_recovers_during_setup(
        clock, monkeypatch, tmp_path, recovered, port_busy):
    class Complete(Exception):
        pass
    monkeypatch.setattr(supervisor, "ROOT", tmp_path)
    monkeypatch.setattr(supervisor, "_single_instance", lambda: True)
    health = iter([False, recovered])
    ports = iter([False, port_busy])
    monkeypatch.setattr(supervisor, "healthy", lambda: next(health))
    monkeypatch.setattr(supervisor, "_ports_in_use", lambda: next(ports))
    monkeypatch.setattr(supervisor, "_run_logged", lambda *args: 0)
    def no_launch(*args, **kwargs):
        pytest.fail("No debe arrancar un segundo backend.")
    monkeypatch.setattr(supervisor.subprocess, "Popen", no_launch)
    monkeypatch.setattr(supervisor.time, "sleep", lambda _: (_ for _ in ()).throw(Complete()))
    before = list(supervisor.logger.handlers)
    try:
        with pytest.raises(Complete):
            supervisor.main()
        assert ("restart_deferred", {"reason": "backend_recovered_or_port_in_use"}) in clock.records
    finally:
        for handler in supervisor.logger.handlers[:]:
            if handler not in before:
                supervisor.logger.removeHandler(handler)
                handler.close()


def test_failed_tree_stop_is_reported_and_keeps_process_available_for_retry(monkeypatch):
    records = []
    monkeypatch.setattr(supervisor, "_record", lambda event, **fields: records.append((event, fields)))
    monkeypatch.setattr(supervisor.sys, "platform", "win32")
    monkeypatch.setattr(supervisor.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(returncode=1))
    assert not supervisor._terminate(process())
    assert records == [("backend_stop_failed", {"pid": 1234, "reason": "tree_termination_failed"})]


@pytest.mark.skipif(sys.platform != "win32", reason="Árbol de procesos de Windows")
def test_windows_stop_terminates_owned_parent_and_its_child(tmp_path):
    from ctypes import wintypes
    child_file = tmp_path / "owned-child.pid"
    script = (
        "import pathlib,subprocess,sys,time; "
        "child=subprocess.Popen([sys.executable,'-c','import time;time.sleep(60)']); "
        "pathlib.Path(sys.argv[1]).write_text(str(child.pid));time.sleep(60)"
    )
    parent = subprocess.Popen([sys.executable, "-c", script, str(child_file)],
                              creationflags=subprocess.CREATE_NO_WINDOW)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    child_handle = None
    try:
        deadline = time.monotonic()+15
        while not child_file.exists() and time.monotonic() < deadline:
            time.sleep(.05)
        assert child_file.exists()
        child_pid = int(child_file.read_text())
        child_handle = kernel.OpenProcess(0x100000, False, child_pid)
        assert child_handle
        assert supervisor._terminate(parent)
        assert parent.poll() is not None
        assert kernel.WaitForSingleObject(child_handle, 5000) == 0
    finally:
        if parent.poll() is None:
            supervisor._terminate(parent)
        if child_handle:
            if kernel.WaitForSingleObject(child_handle, 0) != 0:
                subprocess.run(["taskkill", "/PID", str(child_pid), "/T", "/F"],
                               capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW, check=False)
            kernel.CloseHandle(child_handle)
