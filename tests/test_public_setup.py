"""Instalación desde fuentes sin reutilizar credenciales ni plantillas personales."""
import ipaddress
import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest
from cryptography.fernet import Fernet
from dotenv import dotenv_values

from app import manage, media_tools
from app.settings import ROOT
from tizen import direccion


def test_init_generates_independent_credentials_and_explicit_lan(tmp_path, monkeypatch):
    monkeypatch.setattr(manage, "ROOT", tmp_path)
    manage.initialize("192.168.50.10")
    values = dotenv_values(tmp_path / ".env")
    assert values["VIDEO_LAN_IP"] == "192.168.50.10"
    assert len(values["VIDEO_API_KEY"]) >= 32
    Fernet(values["VIDEO_ENCRYPTION_KEY"].encode())
    assert values["VIDEO_POSTGRES_PASSWORD"] in values["VIDEO_DATABASE_URL"]
    assert not values["VIDEO_OPENAI_API_KEY"] and not values["VIDEO_OPENAI_MODEL"]
    another = tmp_path / "another"
    another.mkdir()
    monkeypatch.setattr(manage, "ROOT", another)
    manage.initialize("192.168.50.11")
    assert dotenv_values(another / ".env")["VIDEO_API_KEY"] != values["VIDEO_API_KEY"]


def test_init_never_overwrites_existing_configuration(tmp_path, monkeypatch):
    monkeypatch.setattr(manage, "ROOT", tmp_path)
    config = tmp_path / ".env"
    config.write_text("existing configuration\n", encoding="utf-8")
    manage.initialize("192.168.50.10")
    assert config.read_text(encoding="utf-8") == "existing configuration\n"


def test_init_rejects_invalid_address_before_writing_secrets(tmp_path, monkeypatch):
    monkeypatch.setattr(manage, "ROOT", tmp_path)
    with pytest.raises(ipaddress.AddressValueError):
        manage.initialize("not-an-ip")
    assert not (tmp_path / ".env").exists()


def test_tizen_preparation_keeps_templates_generic_and_output_private(tmp_path, monkeypatch):
    templates = tmp_path / "tizen" / "app"
    shutil.copytree(ROOT / "tizen" / "app", templates)
    before = {p.name: p.read_bytes() for p in templates.iterdir() if p.is_file()}
    # Las firmas anteriores nunca forman parte del siguiente paquete de fuentes.
    (templates / "author-signature.xml").write_text("old signature", encoding="utf-8")
    monkeypatch.setattr(direccion, "ROOT", tmp_path)
    settings = SimpleNamespace(lan_ip="192.168.50.10", media_port=8765)
    destination = direccion.prepare(settings)
    assert destination == tmp_path / "private" / "tizen-app"
    assert "http://192.168.50.10:8765" in (destination / "arranque.js").read_text(encoding="utf-8")
    assert ">192.168.50.10</tizen:allow-navigation>" in (destination / "config.xml").read_text(encoding="utf-8")
    assert not (destination / "author-signature.xml").exists()
    assert all((templates / name).read_bytes() == data for name, data in before.items())
    build = destination / ".buildResult"
    build.mkdir()
    (build / "stale.wgt").write_bytes(b"stale generated package")
    direccion.prepare(settings)
    assert not build.exists()


def test_ffmpeg_can_be_installed_on_path(tmp_path, monkeypatch):
    monkeypatch.setattr(media_tools, "ROOT", tmp_path)
    monkeypatch.setattr(media_tools.shutil, "which", lambda name: "/installed/bin/" + name)
    assert media_tools.executable("ffmpeg") == "/installed/bin/ffmpeg"
    assert media_tools.executable("ffprobe") == "/installed/bin/ffprobe"


def test_existing_bundled_ffmpeg_keeps_priority(tmp_path, monkeypatch):
    bundled = tmp_path / "tools" / "ffmpeg" / "bin" / "ffmpeg.exe"
    bundled.parent.mkdir(parents=True)
    bundled.write_bytes(b"test executable")
    monkeypatch.setattr(media_tools, "ROOT", tmp_path)
    monkeypatch.setattr(media_tools.shutil, "which", lambda name: "/other/ffmpeg")
    assert Path(media_tools.executable("ffmpeg")) == bundled
