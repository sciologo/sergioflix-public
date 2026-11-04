"""Configuración local. La representación de los secretos siempre queda oculta."""
from pathlib import Path

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[1]


def _hosts(value: str) -> set[str]:
    return {host.strip().lower() for host in value.split(",") if host.strip()}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="VIDEO_", env_file=ROOT / ".env", extra="ignore"
    )
    api_key: SecretStr
    encryption_key: SecretStr
    database_url: SecretStr
    mediafusion_hosts: str = "mediafusion.elfhosted.com,mediafusionfortheweebs.midnightignite.me"
    penguplay_hosts: str = "pengu.uk,www.pengu.uk"
    hdhub_hosts: str = "hdhub.thevolecitor.qzz.io"
    flixstreams_hosts: str = "flixnest.app,free.flixnest.app"
    # Segundo catálogo de subtítulos. Es opcional: OpenSubtitles sigue funcionando
    # sin credenciales y SubDL se suma únicamente cuando existe una clave propia.
    subdl_api_key: SecretStr | None = None
    # El asistente vive únicamente en el PC. Esta clave nunca se entrega al frontend
    # ni a los televisores; ellos siguen autenticándose contra la API local.
    openai_api_key: SecretStr | None = None
    openai_model: str = ""
    # Solo fuentes de alojador: enlace HTTP que reproduce al instante, sin pares y sin
    # cuenta de descarga directa. Un torrent depende de que haya quien lo comparta, y
    # aunque el orden ya lo pone detrás, ofrecerlo gasta la espera de quien lo elige en
    # un fallo probable. Con esto no se ofrece: si un proveedor solo trae P2P, no aporta.
    direct_only: bool = True
    # Sondear a qué CDN lleva cada candidato antes de ordenar. Cuesta un par de
    # segundos por listado y solo aporta si los servidores difieren en velocidad.
    probe_sources: bool = True
    # Biblioteca privada de karaoke. Vive fuera del repositorio para que los videos
    # no entren en Git ni se pierdan al actualizar el código.
    karaoke_root: Path = Path.home() / "Videos" / "SergioFlix" / "Karaoke"
    # La instalación debe declarar su propia IPv4 LAN; loopback sirve para pruebas locales.
    lan_ip: str = "127.0.0.1"
    media_port: int = 8765
    https_port: int = 8443

    @property
    def allowed_hosts(self) -> set[str]:
        return _hosts(self.mediafusion_hosts)

    def provider_hosts(self, provider: str) -> set[str]:
        """Cada proveedor acepta solo sus propios dominios; una URL ajena nunca se guarda."""
        return _hosts(getattr(self, provider + "_hosts", ""))
