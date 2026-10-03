import asyncio
from importlib import import_module


class FakeCache:
    def __init__(self):
        self.cached = None
        self.cache_writes = []

    async def connect(self):
        return None

    async def get_cached_search(self, query, types, **kwargs):
        return self.cached

    async def cache_search(self, query, types, payload, ttl, **kwargs):
        self.cache_writes.append((query, types, payload, ttl))


class FakeSupabase:
    enabled = True

    def __init__(self):
        self.sync_calls = []

    async def sync_search_results(self, query, results):
        self.sync_calls.append((query, results))


class FakeSession:
    pass


def test_read_only_search_keeps_cache_and_live_results_but_skips_persistence(monkeypatch):
    unified = import_module("mindex_api.routers.unified_search")
    cache_module = import_module("mindex_api.cache")
    supabase_module = import_module("mindex_api.supabase_client")
    scraper_module = import_module("mindex_api.scrape_pipeline")

    cache = FakeCache()
    supabase = FakeSupabase()
    scrape_calls = []
    scrape_store_calls = []
    event_calls = []

    def scrape(query):
        scrape_calls.append(query)
        return [{"id": "fixture-live-result", "name": "Amanita fixture"}]

    async def store_scraped(*args, **kwargs):
        scrape_store_calls.append((args, kwargs))

    async def empty_taxa_query(*args, **kwargs):
        return []

    monkeypatch.setattr(cache_module, "get_cache", lambda: cache)
    monkeypatch.setattr(supabase_module, "get_supabase", lambda: supabase)
    monkeypatch.setattr(unified, "_resolve_domains", lambda _types: ["taxa"])
    monkeypatch.setattr(unified, "_build_dispatch", lambda *args, **kwargs: {"taxa": empty_taxa_query()})
    monkeypatch.setattr(scraper_module, "LIVE_SCRAPERS", {"taxa": scrape})
    monkeypatch.setattr(unified, "_async_store_scraped", store_scraped)
    monkeypatch.setattr(unified, "schedule_domain_event", lambda **event: event_calls.append(event))

    response = asyncio.run(
        unified.unified_search(
            q="Amanita fixture",
            types="taxa",
            read_only=True,
            limit=10,
            lat=None,
            lng=None,
            radius=100,
            toxicity=None,
            kingdom=None,
            facility_type=None,
            since=None,
            until=None,
            session=FakeSession(),
        )
    )

    assert response.results["taxa"] == [{"id": "fixture-live-result", "name": "Amanita fixture"}]
    assert scrape_calls == ["Amanita fixture"]
    assert len(cache.cache_writes) == 1
    assert cache.cache_writes[0][3] == 120
    assert scrape_store_calls == []
    assert supabase.sync_calls == []
    assert event_calls == []
