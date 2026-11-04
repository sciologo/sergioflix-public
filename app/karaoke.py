"""Catálogo y entrega segura de los karaokes que pertenecen a la casa.

La carpeta es la fuente de verdad: una subcarpeta equivale a un artista y cada
archivo de video es una versión local de una canción. No se copian ni modifican
los originales. Las rutas físicas nunca se envían al televisor.
"""
from __future__ import annotations

import hashlib
import hmac
from html import escape as xml_escape
from pathlib import Path
from time import time
import re
import unicodedata
from urllib.parse import quote

from app.providers.stremio import ProviderError


VIDEO_EXTENSIONS = frozenset({".mp4", ".m4v", ".mov", ".mkv", ".webm", ".avi", ".ts", ".m2ts"})
IMAGE_EXTENSIONS = (".jpg", ".jpeg", ".png", ".webp")
TOKEN_SECONDS = 6 * 60 * 60

# Son las estanterías editoriales del karaoke. Al escanear se muestran solo
# los artistas que realmente tienen archivos, por eso la pantalla nunca ofrece
# una ficha vacía. Un artista puede aparecer en más de una estantería.
CATEGORIES = (
    ("Favoritos", (
        "Queen", "David Bowie", "Los Prisioneros", "Soda Stereo",
        "Macha y el Bloque Depresivo", "El Bloque Depresivo", "The Beatles",
    )),
    ("Mujer Latina", (
        "Shakira", "Thalía", "Jennifer Lopez", "Massiel", "Karol G", "Ana Gabriel",
        "Myriam Hernández", "Selena", "Gloria Trevi", "Paulina Rubio", "Rocío Dúrcal",
        "Alejandra Guzmán", "Yuri", "Amanda Miguel", "Mercedes Sosa", "Chavela Vargas",
        "Jenni Rivera", "Gloria Estefan", "Mon Laferte", "Marisela",
    )),
    ("Ídolos que avivan la depresión", (
        "José José", "Juan Gabriel", "Cristian Castro", "Luis Miguel", "Sandro",
        "Gianni Bella", "Ricardo Cocciante", "Enrique Iglesias", "Juan Antonio Labra",
        "Kiko Younge", "Alberto Plaza", "La Sociedad", "Bloque Depresivo", "Emmanuel",
        "El Bloque Depresivo", "Miguel Bosé", "Camilo Sesto", "Nino Bravo", "Leonardo Favio",
        "Roberto Carlos", "Zalo Reyes", "Arjona", "Chayanne", "Joaquín Sabina",
        "Los Ángeles Negros", "Ricardo Montaner", "Ricky Martin",
    )),
    ("Rock en español", (
        "Maná", "Los Prisioneros", "Molotov", "Lucybell", "Los Tres", "Soda Stereo",
        "Virus", "La Renga", "La Ley", "Los Bunkers", "Caifanes", "Café Tacvba",
        "Héroes del Silencio", "Enanitos Verdes", "Charly García", "Fito Páez",
        "Andrés Calamaro", "Los Fabulosos Cadillacs", "Gustavo Cerati",
        "El Cuarteto de Nos", "Babasónicos", "La Oreja de Van Gogh", "Sui Generis",
        "Para 30ñeras", "Para 30ñeras - 2",
    )),
    ("Rock Clásico", (
        "Queen", "Santana", "Led Zeppelin", "Deep Purple", "The Beatles", "Pink Floyd",
        "The Rolling Stones", "The Doors", "Creedence Clearwater Revival", "AC/DC",
        "Guns N' Roses", "Bon Jovi", "Eagles", "Dire Straits", "The Police", "U2",
        "Aerosmith", "Journey", "Scorpions", "The Who", "David Bowie", "Elton Jhon",
        "Elvis Presley", "Ex-Beatles", "Música de los 80s", "Rock Progresivo", "Toto",
    )),
    # Colecciones temáticas que no representan a un único artista, pero deben
    # conservar una ubicación editorial estable y nunca caer en "Otros artistas".
    ("Anglo alternativo", ("Anglo Hot",)),
    ("Canción social", ("Canciones de viejo Comunista",)),
    ("Especiales", ("Clásicos de Disney", "Novelas de los 90s")),
)


def _plain(value: str) -> str:
    """Clave comparable, independiente de tildes, mayúsculas y puntuación."""
    decomposed = unicodedata.normalize("NFKD", value)
    ascii_value = "".join(char for char in decomposed if not unicodedata.combining(char))
    return re.sub(r"[^a-z0-9]+", " ", ascii_value.casefold()).strip()


def _slug(value: str) -> str:
    return _plain(value).replace(" ", "-") or "artista"


def _quality(path: Path) -> int:
    """Ordena copias conocidas sin abrir ni reconvertir el archivo."""
    match = re.search(r"(?<!\d)(2160|1440|1080|960|720|576|540|480|478|360|262|240)p\b", path.stem, re.I)
    return int(match.group(1)) if match else 0


def _clean_title(path: Path, artist: str) -> str:
    """Convierte nombres de descarga largos en títulos apropiados para la TV."""
    title = unicodedata.normalize("NFC", path.stem)
    title = title.replace("｜", " - ").replace("⧸", "/").replace("＂", "\"")
    title = re.sub(r"\[[A-Za-z0-9_-]{11}\]", " ", title)
    title = re.sub(r"\[(?:[^\]]*(?:karaoke|instrumental|coros|letra)[^\]]*)\]", " ", title, flags=re.I)
    title = re.sub(r"\((?:[^)]*(?:karaoke|instrumental|backing|lyrics|tono|version|versión)[^)]*)\)", " ", title, flags=re.I)
    title = re.sub(r"\s*\(\d+\)\s*$", " ", title)
    title = re.sub(r"(?<!\d)(?:2160|1440|1080|960|720|576|540|480|478|360|262|240)p\b", " ", title, flags=re.I)
    noise = (
        r"karaoke|pianocanta|piano|instrumental|backing\s+tracks?|lyrics?|(?:con|y)\s+letra|letra|con\s+coros|"
        r"sin\s+voz|pista|versi[oó]n|tono\s+(?:bajo|original)|nuev[oa]|original\s+karaoke|"
        r"from\s+zoom\s+karaoke|karaoke\s+version|karafun|free\s+sheet\s+music\s+in\s+desc|"
        r"as\s+played\s+by[^-]*|karaokebot|al\s+estilo\s+de"
    )
    title = re.sub(r"\b(?:" + noise + r")\b", " ", title, flags=re.I)
    title = re.sub(r"éxito\s+italiano.*$", " ", title, flags=re.I)
    title = re.sub(r"[🔥🔴🎤▶＊🕺]", " ", title)
    title = re.sub(r"^\s*con\s*[-–—]\s*", " ", title, flags=re.I)
    # El artista suele venir antes o después del título, separado por un guion. No se
    # elimina en medio: "Killer Queen" es una canción, no el nombre del grupo repetido.
    aliases = [artist]
    if _plain(artist) == "ricardo cocciante":
        aliases.extend(("Riccardo Cocciante", "Riccardo Cicciante"))
    if _plain(artist) == "arjona":
        aliases.append("Ricardo Arjona")
    if _plain(artist) == "el bloque depresivo":
        # Las descargas usan indistintamente el nombre completo, el nombre corto
        # y la variante encabezada por Macha; todos representan al mismo grupo.
        aliases.extend(("Macha y el Bloque Depresivo", "Bloque Depresivo"))
    for alias in sorted(aliases, key=len, reverse=True):
        escaped = re.escape(alias)
        title = re.sub(r"^\s*" + escaped + r"\s*(?:[-–—:|]\s*)?", " ", title, flags=re.I)
        title = re.sub(r"\s*[-–—:|]\s*" + escaped + r"\s*[-–—:|]*\s*$", " ", title, flags=re.I)
        title = re.sub(r"\s*[-–—:|]\s*" + escaped + r"\s*[-–—:|]\s*", " - ", title, flags=re.I)
        title = re.sub(r"\s*[-–—:|]\s*" + escaped + r"(?=\s+(?:ft\.?|feat\.?))", " ", title, flags=re.I)
        title = re.sub(r"\s*[-–—:|]\s*" + escaped + r"(?=\s*\()", " ", title, flags=re.I)
        if len(_plain(alias).split()) > 1:
            title = re.sub(r"\s+" + escaped + r"\s*[-–—:|]*\s*$", " ", title, flags=re.I)
    title = re.sub(r"[|/]+", " - ", title)
    title = re.sub(r"\s*[-–—]+\s*", " - ", title)
    title = re.sub(r"(?:\s+-\s*){2,}", " - ", title)
    title = re.sub(r"\(\s+", "(", title)
    title = re.sub(r"\s+\)", ")", title)
    title = re.sub(r"\(\s*\)", " ", title)
    title = re.sub(r"\(\s*$", " ", title)
    title = re.sub(r"\s+", " ", title).strip(" .,!_-\"'“”")
    if title and title.upper() == title and any(char.isalpha() for char in title):
        title = title.title()
    return title or path.stem


class Karaoke:
    """Escanea la colección local y proyecta el mismo modelo visual que una serie."""

    def __init__(self, store, settings):
        self.store = store
        self.settings = settings
        self.root = Path(settings.karaoke_root).expanduser().resolve()

    def _inside_root(self, path: Path) -> bool:
        try:
            return path.resolve().is_relative_to(self.root)
        except (OSError, ValueError):
            return False

    def _artists(self) -> list[dict]:
        if not self.root.is_dir():
            return []
        artists = []
        for directory in sorted((item for item in self.root.iterdir() if item.is_dir()),
                                key=lambda item: _plain(item.name)):
            if not self._inside_root(directory):
                continue
            videos = sorted((item for item in directory.iterdir()
                             if item.is_file() and item.suffix.casefold() in VIDEO_EXTENSIONS),
                            key=lambda item: _plain(item.name))
            if not videos:
                continue
            artist_id = _slug(directory.name)
            groups: dict[str, dict] = {}
            for video in videos:
                title = _clean_title(video, directory.name)
                key = _plain(title) or hashlib.sha256(video.name.encode("utf-8")).hexdigest()[:12]
                group = groups.setdefault(key, {"name": title, "files": []})
                version_id = hashlib.sha256(
                    (directory.name.casefold() + "\0" + video.name.casefold()).encode("utf-8")
                ).hexdigest()[:20]
                group["files"].append({
                    "id": version_id, "path": video, "quality": _quality(video),
                    "bytes": video.stat().st_size,
                })
            songs = []
            for key, group in groups.items():
                files = sorted(group["files"], key=lambda item: (item["quality"], item["bytes"]), reverse=True)
                song_id = artist_id + "--" + hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]
                songs.append({"id": song_id, "name": group["name"], "files": files})
            songs.sort(key=lambda item: _plain(item["name"]))
            artists.append({
                "id": artist_id, "name": directory.name, "path": directory,
                "songs": songs,
            })
        return artists

    def _artist(self, artist_id: str) -> dict | None:
        return next((artist for artist in self._artists() if artist["id"] == artist_id), None)

    def _song(self, song_id: str) -> tuple[dict | None, dict | None]:
        for artist in self._artists():
            for song in artist["songs"]:
                if song["id"] == song_id:
                    return artist, song
        return None, None

    @staticmethod
    def _categories_for(name: str) -> list[str]:
        wanted = _plain(name)
        return [title for title, names in CATEGORIES if wanted in {_plain(item) for item in names}]

    @staticmethod
    def _art_url(artist_id: str, kind: str) -> str:
        return f"/karaoke/artists/{quote(artist_id, safe='-')}/{kind}"

    @staticmethod
    def _song_art_url(song_id: str) -> str:
        return f"/karaoke/songs/{quote(song_id, safe='-')}/thumbnail"

    def _card(self, artist: dict) -> dict:
        count = len(artist["songs"])
        categories = self._categories_for(artist["name"])
        return {
            "id": artist["id"], "type": "karaoke_artist", "name": artist["name"],
            "releaseInfo": f"{count} " + ("canción" if count == 1 else "canciones"),
            "description": "Karaoke local" + (" · " + " · ".join(categories) if categories else ""),
            "genres": categories[:3],
            "poster": self._art_url(artist["id"], "poster"),
            "background": self._art_url(artist["id"], "background"),
        }

    def catalog(self) -> dict:
        artists = self._artists()
        by_name = {_plain(artist["name"]): artist for artist in artists}
        used: set[str] = set()
        rows = []
        for title, names in CATEGORIES:
            selected = []
            for name in names:
                artist = by_name.get(_plain(name))
                if artist:
                    selected.append(self._card(artist))
                    used.add(artist["id"])
            if selected:
                rows.append({"title": title, "items": selected})
        remaining = [self._card(artist) for artist in artists if artist["id"] not in used]
        if remaining:
            rows.append({"title": "Otros artistas", "items": remaining})
        return {"items": rows, "artist_count": len(artists),
                "root_available": self.root.is_dir()}

    def artist(self, artist_id: str) -> dict:
        artist = self._artist(artist_id)
        if not artist:
            raise ProviderError("karaoke_artist_not_found", "Ese artista no está en la carpeta local.", 404)
        preferences = self.store.karaoke_preferences()
        episodes = []
        for number, song in enumerate(artist["songs"], 1):
            versions = []
            for source in song["files"]:
                quality = f'{source["quality"]}p' if source["quality"] else "calidad original"
                versions.append({
                    "id": source["id"], "version_id": source["id"], "label": quality,
                    "delivered": source["quality"] or None,
                    "size_mb": round(source["bytes"] / 1024 / 1024),
                    "status": preferences.get((song["id"], source["id"]), "neutral"),
                })
            versions.sort(key=lambda item: {"favorite": 0, "neutral": 1, "bad": 2}[item["status"]])
            default = next((item for item in versions if item["status"] != "bad"), versions[0])
            episodes.append({
                "id": song["id"], "type": "karaoke_song", "artist_id": artist["id"],
                "artist": artist["name"], "name": song["name"], "title": song["name"],
                "season": 1, "episode": number,
                "overview": "Karaoke local" + (f" · {len(versions)} versiones" if len(versions) > 1 else ""),
                "description": f'Karaoke local de {artist["name"]}',
                "thumbnail": self._song_art_url(song["id"]),
                "default_version_id": default["id"], "versions": versions,
            })
        categories = self._categories_for(artist["name"])
        count = len(episodes)
        return {
            "id": artist["id"], "type": "karaoke_artist", "name": artist["name"],
            "releaseInfo": f"{count} " + ("canción" if count == 1 else "canciones"),
            "description": "Canciones disponibles en la colección local de SergioFlix.",
            "genres": categories[:3],
            "poster": self._art_url(artist["id"], "poster"),
            "background": self._art_url(artist["id"], "background"),
            "episodes": episodes, "playback": {"videos": {}},
        }

    def preference(self, song_id: str, version_id: str, status: str, actor: str) -> dict:
        artist, song = self._song(song_id)
        if not artist or not song or version_id not in {item["id"] for item in song["files"]}:
            raise ProviderError("karaoke_version_not_found", "Esa versión no está en la carpeta local.", 404)
        self.store.set_karaoke_preference(song_id, version_id, status, actor)
        return self.artist(artist["id"])

    def play(self, song_id: str, version_id: str | None = None) -> dict:
        artist, song = self._song(song_id)
        if not artist or not song:
            raise ProviderError("karaoke_song_not_found", "Esa canción ya no está en la carpeta local.", 404)
        preferences = self.store.karaoke_preferences()
        ordered = sorted(song["files"], key=lambda item: {
            "favorite": 0, "neutral": 1, "bad": 2
        }[preferences.get((song_id, item["id"]), "neutral")])
        source = next((item for item in ordered if item["id"] == version_id), None) if version_id else None
        if version_id and not source:
            raise ProviderError("karaoke_version_not_found", "Esa versión ya no está disponible.", 404)
        if not source:
            source = next((item for item in ordered
                           if preferences.get((song_id, item["id"]), "neutral") != "bad"), ordered[0])
        expires = int(time()) + TOKEN_SECONDS
        signature = self._signature(song_id, source["id"], expires)
        url = (f"http://{self.settings.lan_ip}:{self.settings.media_port}/karaoke/media/"
               f"{quote(song_id, safe='-')}?version_id={source['id']}&expires={expires}&signature={signature}")
        return {"stream_url": url, "version_id": source["id"], "content_type": "video/mp4"}

    def _signature(self, song_id: str, version_id: str, expires: int) -> str:
        secret = self.settings.encryption_key.get_secret_value().encode("utf-8")
        payload = f"{song_id}\n{version_id}\n{expires}".encode("utf-8")
        return hmac.new(secret, payload, hashlib.sha256).hexdigest()

    def media(self, song_id: str, version_id: str, expires: int, signature: str) -> Path:
        if expires < int(time()) or expires > int(time()) + TOKEN_SECONDS + 60:
            raise ProviderError("karaoke_link_expired", "Vuelve a abrir la canción.", 410)
        expected = self._signature(song_id, version_id, expires)
        if not hmac.compare_digest(signature, expected):
            raise ProviderError("karaoke_link_invalid", "El enlace de reproducción no es válido.", 403)
        _, song = self._song(song_id)
        source = next((item for item in song["files"] if item["id"] == version_id), None) if song else None
        if not source or not self._inside_root(source["path"]) or not source["path"].is_file():
            raise ProviderError("karaoke_song_not_found", "El archivo ya no está disponible.", 404)
        return source["path"]

    @staticmethod
    def _named_image(directory: Path, names: tuple[str, ...]) -> Path | None:
        wanted = {name.casefold() for name in names}
        return next((item for item in directory.iterdir()
                     if item.is_file() and item.name.casefold() in wanted), None)

    def artist_art(self, artist_id: str, kind: str) -> tuple[Path | None, str, str]:
        artist = self._artist(artist_id)
        if not artist:
            raise ProviderError("karaoke_artist_not_found", "Ese artista no está en la carpeta local.", 404)
        stems = ("poster", "cover", "folder") if kind == "poster" else ("background", "backdrop", "fanart")
        names = tuple(stem + extension for stem in stems for extension in IMAGE_EXTENSIONS)
        path = self._named_image(artist["path"], names)
        return path, artist["name"], kind

    def song_art(self, song_id: str) -> tuple[Path | None, str, str]:
        artist, song = self._song(song_id)
        if not artist or not song:
            raise ProviderError("karaoke_song_not_found", "Esa canción no está disponible.", 404)
        path = None
        for source in song["files"]:
            for extension in IMAGE_EXTENSIONS:
                candidate = source["path"].with_suffix(extension)
                if candidate.is_file() and self._inside_root(candidate):
                    path = candidate
                    break
            if path:
                break
        return path, song["name"], artist["name"]

    @staticmethod
    def placeholder(title: str, subtitle: str, wide: bool) -> str:
        width, height = (1600, 900) if wide else (1000, 1500)
        title_size = 88 if wide else 78
        subtitle_size = 38 if wide else 34
        safe_title, safe_subtitle = xml_escape(title[:36]), xml_escape(subtitle[:52])
        return f'''<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">
<defs><linearGradient id="g" x1="0" y1="0" x2="1" y2="1"><stop stop-color="#33151a"/><stop offset="1" stop-color="#09090b"/></linearGradient></defs>
<rect width="100%" height="100%" fill="url(#g)"/><circle cx="{width * .78:.0f}" cy="{height * .22:.0f}" r="{min(width, height) * .18:.0f}" fill="#e53d49" opacity=".18"/>
<text x="{width * .08:.0f}" y="{height * .16:.0f}" fill="#e53d49" font-family="Arial,sans-serif" font-size="32" font-weight="700" letter-spacing="6">SERGIOFLIX KARAOKE</text>
<text x="{width * .08:.0f}" y="{height * .62:.0f}" fill="#fff" font-family="Arial,sans-serif" font-size="{title_size}" font-weight="700">{safe_title}</text>
<text x="{width * .08:.0f}" y="{height * .70:.0f}" fill="#aaa9b0" font-family="Arial,sans-serif" font-size="{subtitle_size}">{safe_subtitle}</text>
</svg>'''
