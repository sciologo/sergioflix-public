"""Interpretación liviana de búsquedas habladas mediante OpenAI Responses."""
import asyncio
import json
import re
import unicodedata

import httpx

from app.providers.stremio import ProviderError


MAX_CHOICES = 20
MAX_REASON_LENGTH = 220
LOOKUP_TIMEOUT = 60


SEARCH_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "message": {"type": "string", "maxLength": 280},
        "searches": {
            "type": "array",
            "minItems": 1,
            "maxItems": MAX_CHOICES,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "term": {"type": "string", "minLength": 1, "maxLength": 120},
                    "kind": {"type": "string", "enum": ["title"]},
                    "media_type": {"type": "string", "enum": ["movie", "series", "both"]},
                    "alternate_title": {"type": ["string", "null"], "maxLength": 120},
                    "year": {"type": ["integer", "null"], "minimum": 1888, "maximum": 2100},
                    "reason": {"type": "string", "minLength": 1, "maxLength": MAX_REASON_LENGTH},
                },
                "required": ["term", "kind", "media_type", "alternate_title", "year", "reason"],
            },
        },
        "start_seconds": {"type": ["integer", "null"], "minimum": 0, "maximum": 86400},
        "approximate": {"type": "boolean"},
        "series_title": {"type": ["string", "null"], "maxLength": 120},
        "season": {"type": ["integer", "null"], "minimum": 1, "maximum": 100},
        "episode": {"type": ["integer", "null"], "minimum": 1, "maximum": 2000},
        "episode_title": {"type": ["string", "null"], "maxLength": 160},
    },
    "required": ["message", "searches", "start_seconds", "approximate",
                 "series_title", "season", "episode", "episode_title"],
}


INSTRUCTIONS = f"""Eres el asistente de búsqueda de una videoteca doméstica en español.
Selecciona las obras concretas que responden a la petición; no converses ni expliques
el proceso. Cada elemento de searches es una elección tuya, nunca una búsqueda genérica
por persona, género o tema. Escribe en term el título internacional completo en inglés
habitual en los catálogos (si no existe, el original), en
alternate_title el título en español si lo conoces y difiere (si no, null), y en year
el año de estreno si lo conoces (si no, null). kind siempre es title. No repitas obras.
Para recomendaciones, obras similares, filmografías o listas por actor, director,
género o tema, propone hasta {MAX_CHOICES} títulos pertinentes, ordenados de mayor a menor
afinidad. Intenta llegar a {MAX_CHOICES} si existen suficientes opciones confiables. Si
piden menos, respeta esa cantidad; si piden más, el máximo es {MAX_CHOICES}. No rellenes
con obras inventadas o que no cumplen. Para una persona elige obras en las que participa.
Respeta el tipo solicitado: si piden películas parecidas a una serie, elige películas.
No incluyas la obra de referencia entre las recomendaciones de obras parecidas.
Para buscar un título concreto o identificar una obra descrita sin título, selecciona
solo la obra pedida o las pocas candidatas realmente probables. Cada obra ocupa un solo
elemento de searches, aunque tenga un título traducido.
En reason escribe para CADA elección una explicación en español de unos 200 caracteres
(intenta entre 180 y {MAX_REASON_LENGTH}), en una o dos frases completas. Explica por qué
esa obra responde a ESTA petición, con un dato concreto y confiable: el papel del actor,
el director, el tema, el tono o la semejanza solicitada. Por ejemplo, si piden Robert De
Niro, relaciona la elección con su personaje o interpretación en esa película, sin
limitarte a repetir que aparece en ella. Para un título o capítulo concreto, explica
qué elementos coinciden con la descripción. No copies la sinopsis ni repitas la misma
frase en todos los resultados. No inventes participaciones, premios, preferencias del
usuario ni porcentajes de afinidad. No reveles razonamientos internos ni pasos del proceso.
Devuelve un mensaje natural y corto sobre tu selección, sin afirmar que ya está
disponible en el catálogo, y, solo si lo pidieron, el segundo desde el cual conviene
comenzar. No inventes un segundo exacto: si solo conoces un rango,
elige el centro y marca approximate=true; si no tienes una referencia razonable usa null.
Si la petición corresponde a un capítulo, searches debe buscar únicamente el nombre de
la serie con media_type=series. Escribe además series_title y, cuando los conozcas,
season, episode y episode_title. Nunca pongas el nombre del capítulo como una búsqueda
global: la aplicación lo localizará después dentro de la serie. Para películas y
búsquedas generales esos cuatro campos deben ser null.
No incluyas URLs, instrucciones de reproducción ni información ajena a la consulta."""


class Assistant:
    """Llama a Luna y acepta solamente la estructura que la API acordó."""

    def __init__(self, client: httpx.AsyncClient, api_key: str, model: str):
        self.client, self.api_key, self.model = client, api_key, model

    async def interpret(self, prompt: str) -> dict:
        body = {
            "model": self.model,
            "store": False,
            "reasoning": {"effort": "medium"},
            "text": {
                "verbosity": "low",
                "format": {
                    "type": "json_schema",
                    "name": "tv_catalog_search",
                    "strict": True,
                    "schema": SEARCH_SCHEMA,
                },
            },
            "instructions": INSTRUCTIONS,
            "input": prompt,
            # Una sola consulta incluye las veinte elecciones y sus explicaciones.
            "max_output_tokens": 7500,
        }
        try:
            response = await self.client.post(
                "https://api.openai.com/v1/responses",
                headers={"Authorization": "Bearer " + self.api_key},
                json=body,
                timeout=httpx.Timeout(45, connect=5, pool=5),
            )
        except (httpx.TimeoutException, httpx.NetworkError):
            raise ProviderError(
                "assistant_unavailable", "El asistente no respondió. Inténtalo nuevamente.", 503
            ) from None
        if response.status_code == 429:
            raise ProviderError("assistant_busy", "El asistente está ocupado. Inténtalo en un momento.", 503)
        if response.status_code in (401, 403):
            raise ProviderError("assistant_configuration", "El asistente no está disponible en este momento.", 503)
        if not 200 <= response.status_code < 300:
            raise ProviderError("assistant_unavailable", "El asistente no respondió. Inténtalo nuevamente.", 503)
        try:
            data = response.json()
            text = next(
                content["text"]
                for item in data.get("output", [])
                if item.get("type") == "message"
                for content in item.get("content", [])
                if content.get("type") == "output_text"
            )
            result = json.loads(text)
            if not isinstance(result, dict) or not isinstance(result.get("searches"), list) or not result["searches"]:
                raise ValueError
            if not all(isinstance(search, dict) for search in result["searches"]):
                raise ValueError
            for search in result["searches"]:
                reason = search.get("reason")
                if not isinstance(reason, str) or not reason.strip():
                    raise ValueError
                search["reason"] = " ".join(reason.split())[:MAX_REASON_LENGTH]
            return result
        except (KeyError, TypeError, ValueError, StopIteration, json.JSONDecodeError):
            raise ProviderError(
                "assistant_invalid_response", "El asistente no entendió la consulta. Inténtalo de otra forma.", 502
            ) from None


def _title_key(value: str) -> str:
    """Tolera tildes y puntuación, sin aceptar secuelas por coincidencia parcial."""
    return "".join(char for char in _plain(value) if char.isalnum())


def _selected_title(items: list[dict], choice: dict, media_type: str) -> dict | None:
    """Una búsqueda puede traer muchos vecinos; solo sirve la obra elegida."""
    def keys(title):
        result = {_title_key(title)}
        if choice.get("year") is not None:
            # Cinemeta a veces omite el artículo del título. Solo admitir esa
            # variante cuando el año permite distinguir la obra de sus homónimas.
            without_article = re.sub(r"^(?:the|a|an|el|la|los|las|un|una)\s+", "", _plain(title))
            result.add(_title_key(without_article))
        return result

    titles = keys(choice["term"])
    if choice.get("alternate_title"):
        titles.update(keys(choice["alternate_title"]))
    for item in items:
        if not item.get("id") or item.get("type") != media_type:
            continue
        if not keys(item.get("name") or "") & titles:
            continue
        if choice.get("year") is not None:
            released = re.match(r"\d{4}", str(item.get("releaseInfo") or ""))
            if not released or int(released[0]) != choice["year"]:
                continue
        return item
    return None


async def collect_results(catalog, plan: dict, limit: int = MAX_CHOICES) -> list[dict]:
    """Resuelve una ficha por elección, conservando el orden y sin resultados de relleno."""
    limit = min(limit, MAX_CHOICES)
    if limit <= 0:
        return []
    choices, known_choices = [], set()
    for choice in plan.get("searches", [])[:MAX_CHOICES]:
        if choice.get("kind", "title") != "title":
            continue
        term = str(choice.get("term", "")).strip()
        media_type = choice.get("media_type")
        if not term or media_type not in ("movie", "series", "both"):
            continue
        key = (_title_key(term), choice.get("year"), media_type)
        if key not in known_choices:
            known_choices.add(key)
            choices.append({**choice, "term": term})

    # Se comparte el cliente HTTP con la reproducción: no ocupar todas sus conexiones.
    slots = asyncio.Semaphore(4)

    async def lookup(choice):
        media_types = ("movie", "series") if choice["media_type"] == "both" else (choice["media_type"],)
        terms = [choice["term"]]
        alternate = choice.get("alternate_title")
        if alternate and _title_key(alternate) != _title_key(choice["term"]):
            terms.append(alternate)
        for term in terms:
            for media_type in media_types:
                try:
                    async with slots:
                        response = await catalog.page(media_type, 0, None, term)
                except ProviderError:
                    # Un título ausente o proveedor caído no invalida los demás.
                    continue
                item = _selected_title(response.get("items", []), choice, media_type)
                if item:
                    # La explicación pertenece a esta consulta, nunca a la ficha cacheada.
                    return {**item, "assistant_reason": str(choice.get("reason") or "")[:MAX_REASON_LENGTH]}
        return None

    async def resolve(choice):
        try:
            # Incluye la espera de turno: la lista completa termina antes del plazo
            # de la TV y conserva las fichas que sí se pudieron resolver.
            async with asyncio.timeout(LOOKUP_TIMEOUT):
                return await lookup(choice)
        except TimeoutError:
            return None

    responses = await asyncio.gather(*(resolve(choice) for choice in choices))
    found, known = [], set()
    for item in responses:
        if item and item["id"] not in known:
            known.add(item["id"])
            found.append(item)
            if len(found) >= limit:
                break
    return found


def _plain(value: str | None) -> str:
    """Compara títulos sin depender de tildes, mayúsculas o puntuación menor."""
    normalized = unicodedata.normalize("NFKD", value or "")
    return " ".join(
        "".join(char for char in normalized if not unicodedata.combining(char))
        .casefold().replace("-", " ").replace(":", " ").split()
    )


def _episode_from(detail: dict, plan: dict) -> dict | None:
    episodes = detail.get("episodes") or []
    season, number = plan.get("season"), plan.get("episode")
    wanted = _plain(plan.get("episode_title"))
    if season is not None and number is not None:
        for item in episodes:
            if item.get("season") == season and item.get("episode") == number:
                # El título es una segunda comprobación. Si contradice al número que
                # sugirió Luna, se busca el título real dentro de la misma serie.
                actual = _plain(item.get("name") or item.get("title"))
                if not wanted or wanted == actual or wanted in actual or actual in wanted:
                    return item
                break
    if not wanted:
        return None
    exact, partial = None, None
    for item in episodes:
        title = _plain(item.get("name") or item.get("title"))
        if title == wanted:
            exact = item
            break
        if wanted in title or title in wanted:
            partial = partial or item
    return exact or partial


async def resolve_episode(catalog, items: list[dict], plan: dict) -> tuple[list[dict], dict | None]:
    """Busca el capítulo solo dentro de la serie identificada por el catálogo."""
    series_title = _plain(plan.get("series_title"))
    if not series_title or not (plan.get("episode") or plan.get("episode_title")):
        return items, None
    series = [item for item in items if item.get("type") == "series"]
    if not series:
        try:
            retry = await catalog.page("series", 0, None, plan.get("series_title"))
            # Una consulta de capítulo contiene una sola elección: conservar su motivo
            # también cuando el catálogo solo logra resolverla en este segundo intento.
            choice = next(iter(plan.get("searches") or [{}]))
            reason = str(choice.get("reason") or "")[:MAX_REASON_LENGTH]
            series = [{**item, "assistant_reason": reason} for item in retry.get("items", [])
                      if item.get("type") == "series"]
        except ProviderError:
            return [], None
    exact = [item for item in series if _plain(item.get("name")) == series_title]
    candidates = exact or [item for item in series if series_title in _plain(item.get("name"))][:3]
    if not candidates:
        candidates = series[:1]
    for candidate in candidates:
        try:
            detail = await catalog.detail("series", candidate["id"])
        except ProviderError:
            continue
        episode = _episode_from(detail, plan)
        if not episode:
            continue
        target = {
            "video_id": episode["id"],
            "season": episode["season"],
            "episode": episode["episode"],
            "title": episode.get("name") or episode.get("title") or "",
        }
        enriched = {**candidate, "assistant_episode": target}
        return [enriched], target
    return series, None
