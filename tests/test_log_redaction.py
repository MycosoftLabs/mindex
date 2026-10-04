import logging

import pytest

from mindex_etl import log_redaction
from mindex_etl.log_redaction import REDACTED, redact

FAKE_MAP_KEY = "0123456789abcdef0123456789abcdef"
FAKE_API_KEY = "FakeNasaApiKey1234567890abcdefXYZ"
FAKE_TOKEN = "eyJhbGciOiJSUzI1NiJ9.fakepayload.fakesig"


@pytest.fixture(autouse=True)
def _no_env_keys(monkeypatch):
    for name in log_redaction.SECRET_ENV_VARS:
        monkeypatch.delenv(name, raising=False)


@pytest.mark.parametrize(
    "text",
    [
        f'HTTP Request: GET https://firms.modaps.eosdis.nasa.gov/api/area/csv/{FAKE_MAP_KEY}/VIIRS_NOAA20_NRT/world/1 "HTTP/1.1 200 OK"',
        f"https://firms.modaps.eosdis.nasa.gov/api/country/csv/{FAKE_MAP_KEY}/MODIS_NRT/USA/1",
        f"https://firms.modaps.eosdis.nasa.gov/api/data_availability/csv/{FAKE_MAP_KEY}/all",
        f"https://firms.modaps.eosdis.nasa.gov/mapserver/wms/fires/{FAKE_MAP_KEY}/?SERVICE=WMS",
        f"https://api.nasa.gov/DONKI/FLR?startDate=2026-10-01&api_key={FAKE_API_KEY}",
        f"https://firms.modaps.eosdis.nasa.gov/api/area/csv?MAP_KEY={FAKE_MAP_KEY}&source=x",
        f"https://example.test/granules?token={FAKE_TOKEN}&page=1",
        f"headers={{'Authorization': 'Bearer {FAKE_TOKEN}'}}",
        f"Authorization: Bearer {FAKE_TOKEN}",
    ],
)
def test_redact_masks_secret(text):
    clean = redact(text)
    assert FAKE_MAP_KEY not in clean
    assert FAKE_API_KEY not in clean
    assert FAKE_TOKEN not in clean
    assert REDACTED in clean


def test_redact_keeps_harmless_urls():
    url = 'HTTP Request: GET https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/all_day.geojson "HTTP/1.1 200 OK"'
    assert redact(url) == url


def test_redact_masks_configured_key_values(monkeypatch):
    monkeypatch.setenv("NASA_FIRMS_MAP_KEY", FAKE_MAP_KEY)
    assert FAKE_MAP_KEY not in redact(f"fetch failed for key {FAKE_MAP_KEY} in some odd shape")


def test_installed_factory_redacts_httpx_records_and_tracebacks(caplog):
    log_redaction.install()
    url = f"https://firms.modaps.eosdis.nasa.gov/api/area/csv/{FAKE_MAP_KEY}/VIIRS_NOAA20_NRT/world/1"
    with caplog.at_level(logging.INFO):
        logging.getLogger("httpx").info('HTTP Request: %s %s "%s"', "GET", url, "HTTP/1.1 200 OK")
        try:
            raise RuntimeError(f"Client error '404 Not Found' for url '{url}'")
        except RuntimeError:
            logging.getLogger("mindex_etl.sources.nasa_firms").exception("FIRMS fetch failed")
    formatter = logging.Formatter("%(message)s")
    output = "\n".join(formatter.format(r) for r in caplog.records)
    assert FAKE_MAP_KEY not in output
    assert output.count(REDACTED) >= 2
