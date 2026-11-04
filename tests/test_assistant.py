"""El LLM solo interpreta texto y la videoteca conserva el control del catálogo."""
import asyncio
import json

import httpx
import pytest

from app.assistant import Assistant, collect_results, resolve_episode
from app.providers.stremio import ProviderError


def test_luna_uses_structured_low_cost_response_without_storage():
    captured = {}

    def responder(request):
        captured["body"] = json.loads(request.content)
        captured["authorization"] = request.headers.get("authorization")
        plan = {
            "message": "Encontré varias películas de Batman.",
            "searches": [
                {"term": "Batman", "kind": "title", "media_type": "movie",
                 "alternate_title": None, "year": 1989, "reason": "Es la película de Batman que buscas."},
                {"term": "The Dark Knight", "kind": "title", "media_type": "movie",
                 "alternate_title": "El caballero oscuro", "year": 2008,
                 "reason": "Una alternativa con el tono oscuro que describes."},
            ],
            "start_seconds": None,
            "approximate": False,
            "series_title": None,
            "season": None,
            "episode": None,
            "episode_title": None,
        }
        return httpx.Response(200, json={"output": [{"type": "message", "content": [
            {"type": "output_text", "text": json.dumps(plan)}
        ]}]})

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(responder)) as client:
            return await Assistant(client, "private-test-key", "test-model").interpret(
                "Busca todas las películas de Batman"
            )

    result = asyncio.run(run())
    assert result["searches"][1]["term"] == "The Dark Knight"
    assert captured["body"]["model"] == "test-model"
    assert captured["body"]["reasoning"] == {"effort": "medium"}
    assert captured["body"]["text"]["verbosity"] == "low"
    assert captured["body"]["text"]["format"]["strict"] is True
    assert captured["body"]["store"] is False
    assert captured["authorization"] == "Bearer private-test-key"
    choice_schema = captured["body"]["text"]["format"]["schema"]["properties"]["searches"]["items"]
    assert "reason" in choice_schema["required"]
    assert choice_schema["properties"]["reason"]["maxLength"] == 220


@pytest.mark.parametrize('reason', [None, "", "  ", {"html": "no es texto"}])
def test_model_response_without_a_text_explanation_is_rejected(reason):
    async def run():
        response = {"output": [{"type": "message", "content": [
            {"type": "output_text", "text": json.dumps({"searches": [{"reason": reason}]})}
        ]}]}
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(200, json=response))) as client:
            await Assistant(client, "private-test-key", "test-model").interpret("Busca Batman")

    with pytest.raises(ProviderError) as error:
        asyncio.run(run())
    assert error.value.code == 'assistant_invalid_response'


def test_explanations_follow_the_resolved_title_without_changing_cached_catalog_data():
    first = {"id": "tt1", "type": "movie", "name": "Primera", "releaseInfo": "2000"}
    second = {"id": "tt2", "type": "series", "name": "Segunda", "releaseInfo": "2020"}

    class FakeCatalog:
        async def page(self, media_type, skip, genre, search):
            if search == 'Primera':
                await asyncio.sleep(.01)
                return {"items": [second, first]}
            return {"items": [second]}

    plan = {"searches": [
        {"term": "Primera", "year": 2000, "media_type": "movie", "reason": "Motivo de la primera."},
        {"term": "Ausente", "media_type": "movie", "reason": "Este motivo no debe aparecer."},
        {"term": "Segunda", "year": 2020, "media_type": "series", "reason": "Motivo de la segunda."},
        {"term": "Primera", "year": 2000, "media_type": "movie", "reason": "Elección duplicada."},
    ]}
    result = asyncio.run(collect_results(FakeCatalog(), plan))
    assert [(item['id'], item['assistant_reason']) for item in result] == [
        ('tt1', 'Motivo de la primera.'), ('tt2', 'Motivo de la segunda.')]
    assert 'assistant_reason' not in first and 'assistant_reason' not in second


def test_only_the_title_selected_by_ai_is_returned_from_each_search():
    class FakeCatalog:
        async def page(self, media_type, skip, genre, search):
            results = {
                ("movie", "Batman"): [
                    {"id": "tt1", "type": "movie", "name": "Batman"},
                    {"id": "tt2", "type": "movie", "name": "Batman Returns"},
                ],
                ("movie", "The Dark Knight"): [
                    {"id": "tt2", "type": "movie", "name": "Batman Returns"},
                    {"id": "tt3", "type": "movie", "name": "The Dark Knight"},
                ],
            }
            return {"items": results.get((media_type, search), [])}

    plan = {"searches": [
        {"term": "Batman", "kind": "title", "media_type": "movie"},
        {"term": "The Dark Knight", "kind": "title", "media_type": "movie"},
    ]}
    result = asyncio.run(collect_results(FakeCatalog(), plan))
    assert [item["id"] for item in result] == ["tt1", "tt3"]


def test_twenty_choices_keep_ai_order_despite_search_completion_order_and_noise():
    class FakeCatalog:
        active = 0
        peak = 0
        completed = []

        async def page(self, media_type, skip, genre, search):
            index = int(search.split()[-1])
            self.active += 1
            self.peak = max(self.peak, self.active)
            try:
                await asyncio.sleep((4 - index % 4) / 1000)
                self.completed.append(index)
                return {"items": [
                    {"id": "tt-noise", "type": media_type, "name": "Otra película"},
                    {"id": "tt" + str(index), "type": media_type, "name": search,
                     "releaseInfo": "2000"},
                ]}
            finally:
                self.active -= 1

    catalog = FakeCatalog()
    plan = {"searches": [
        {"term": "Elegida " + str(index), "kind": "title", "media_type": "movie", "year": 2000}
        for index in range(20)
    ]}
    result = asyncio.run(collect_results(catalog, plan, limit=60))
    assert [item["id"] for item in result] == ["tt" + str(index) for index in range(20)]
    assert catalog.peak == 4
    assert catalog.completed != list(range(20))


def test_missing_choices_and_failed_searches_never_fill_with_secondary_results():
    class FakeCatalog:
        async def page(self, media_type, skip, genre, search):
            if search == "Taxi Driver":
                raise ProviderError("timeout", "No respondió.")
            return {"items": [
                {"id": "tt-other", "type": "movie", "name": "Otras historias"},
                {"id": "tt-silo", "type": "series", "name": "Silo"},
            ]}

    plan = {"searches": [
        {"term": "Taxi Driver", "kind": "title", "media_type": "movie"},
        {"term": "The Godfather", "kind": "title", "media_type": "movie"},
        {"term": "Silo", "kind": "title", "media_type": "series"},
    ]}
    result = asyncio.run(collect_results(FakeCatalog(), plan))
    assert [item["id"] for item in result] == ["tt-silo"]


def test_original_and_translated_titles_resolve_one_choice_and_deduplicate():
    class FakeCatalog:
        calls = []

        async def page(self, media_type, skip, genre, search):
            self.calls.append(search)
            return {"items": [{"id": "tt-godfather", "type": "movie", "name": "El Padrino",
                               "releaseInfo": "1972"}] if search == "El padrino" else []}

    catalog = FakeCatalog()
    result = asyncio.run(collect_results(catalog, {"searches": [
        {"term": "The Godfather", "alternate_title": "El padrino", "year": 1972, "media_type": "movie"},
        {"term": "El padrino", "year": 1972, "media_type": "movie"},
        {"term": "The Godfather", "alternate_title": "El padrino", "year": 1972, "media_type": "movie"},
    ]}))
    assert [item["id"] for item in result] == ["tt-godfather"]
    assert catalog.calls.count("The Godfather") == 1


def test_year_and_media_type_disambiguate_remakes_and_exclude_similar_titles():
    class FakeCatalog:
        async def page(self, media_type, skip, genre, search):
            return {"items": [
                {"id": "tt-sequel", "type": "movie", "name": "Dracula II", "releaseInfo": "1992"},
                {"id": "tt-series", "type": "series", "name": "Dracula", "releaseInfo": "1992–1993"},
                {"id": "tt-old", "type": "movie", "name": "Dracula", "releaseInfo": "1931"},
                {"id": "tt-unknown-year", "type": "movie", "name": "Dracula"},
                {"id": "tt-selected", "type": "movie", "name": "Drácula", "releaseInfo": "1992"},
            ]}

    result = asyncio.run(collect_results(FakeCatalog(), {"searches": [
        {"term": "Dracula", "year": 1992, "kind": "title", "media_type": "movie"},
    ]}))
    assert [item["id"] for item in result] == ["tt-selected"]


def test_both_types_resolve_one_work_and_person_searches_are_not_used():
    class FakeCatalog:
        calls = []

        async def page(self, media_type, skip, genre, search):
            self.calls.append((media_type, search))
            return {"items": [{"id": "tt-silo", "type": "series", "name": "Silo",
                               "releaseInfo": "2023–"}] if media_type == "series" else []}

    catalog = FakeCatalog()
    result = asyncio.run(collect_results(catalog, {"searches": [
        {"term": "Robert De Niro", "kind": "person", "media_type": "both"},
        {"term": "Silo", "kind": "title", "year": 2023, "media_type": "both"},
    ]}))
    assert [item["id"] for item in result] == ["tt-silo"]
    assert catalog.calls == [("movie", "Silo"), ("series", "Silo")]


def test_catalog_title_can_omit_a_leading_article_when_the_year_matches():
    class FakeCatalog:
        async def page(self, media_type, skip, genre, search):
            return {"items": [
                {"id": "tt-old", "type": "movie", "name": "City of Ember", "releaseInfo": "1990"},
                {"id": "tt-ember", "type": "movie", "name": "City of Ember", "releaseInfo": "2008"},
            ]}

    result = asyncio.run(collect_results(FakeCatalog(), {"searches": [
        {"term": "The City of Ember", "year": 2008, "media_type": "movie"},
    ]}))
    assert [item["id"] for item in result] == ["tt-ember"]


def test_limit_applies_to_choices_and_results():
    class FakeCatalog:
        calls = []

        async def page(self, media_type, skip, genre, search):
            self.calls.append(search)
            return {"items": [{"id": search, "type": media_type, "name": search}]}

    catalog = FakeCatalog()
    plan = {"searches": [{"term": "Título " + str(index), "media_type": "movie"} for index in range(25)]}
    result = asyncio.run(collect_results(catalog, plan, limit=15))
    assert len(catalog.calls) == 20
    assert [item["id"] for item in result] == ["Título " + str(index) for index in range(15)]


def test_lookup_timeout_keeps_completed_choices_and_cancels_slow_searches(monkeypatch):
    monkeypatch.setattr('app.assistant.LOOKUP_TIMEOUT', 0.02)

    class FakeCatalog:
        cancelled = False

        async def page(self, media_type, skip, genre, search):
            if search == "Lenta":
                try:
                    await asyncio.sleep(10)
                except asyncio.CancelledError:
                    self.cancelled = True
                    raise
            return {"items": [{"id": search, "type": media_type, "name": search}]}

    catalog = FakeCatalog()
    result = asyncio.run(collect_results(catalog, {"searches": [
        {"term": "Primera", "media_type": "movie"},
        {"term": "Lenta", "media_type": "movie"},
        {"term": "M", "media_type": "movie"},
    ]}))
    assert [item["id"] for item in result] == ["Primera", "M"]
    assert catalog.cancelled


def test_episode_is_resolved_only_inside_the_identified_series():
    class FakeCatalog:
        calls = []

        async def detail(self, media_type, content_id):
            self.calls.append((media_type, content_id))
            return {"episodes": [
                {"id": "tt-silo:1:2", "season": 1, "episode": 2, "name": "Holston's Pick"},
                {"id": "tt-silo:1:3", "season": 1, "episode": 3, "name": "Machines"},
            ]}

    catalog = FakeCatalog()
    items, episode = asyncio.run(resolve_episode(catalog, [
        {"id": "tt-film", "type": "movie", "name": "Machines"},
        {"id": "tt-silo", "type": "series", "name": "Silo", "assistant_reason": "Coincide con el capítulo descrito."},
    ], {"series_title": "Silo", "season": 2, "episode": 2, "episode_title": "Machines"}))
    assert catalog.calls == [("series", "tt-silo")]
    assert [item["id"] for item in items] == ["tt-silo"]
    assert episode == {"video_id": "tt-silo:1:3", "season": 1, "episode": 3, "title": "Machines"}
    assert items[0]["assistant_episode"] == episode
    assert items[0]["assistant_reason"] == "Coincide con el capítulo descrito."


def test_episode_catalog_retry_keeps_the_original_choice_explanation():
    cached = {"id": "tt-silo", "type": "series", "name": "Silo"}

    class FakeCatalog:
        async def page(self, *args):
            return {"items": [cached]}

        async def detail(self, *args):
            return {"episodes": [{"id": "tt-silo:1:3", "season": 1, "episode": 3, "name": "Machines"}]}

    items, episode = asyncio.run(resolve_episode(FakeCatalog(), [], {
        "series_title": "Silo", "season": 1, "episode": 3,
        "searches": [{"term": "Silo", "media_type": "series", "reason": "El episodio de la máquina que describes."}],
    }))
    assert episode['video_id'] == 'tt-silo:1:3'
    assert items[0]['assistant_reason'] == 'El episodio de la máquina que describes.'
    assert 'assistant_reason' not in cached
