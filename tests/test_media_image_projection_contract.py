from __future__ import annotations

import asyncio
from uuid import UUID

import pytest
from fastapi import HTTPException

from mindex_api.routers.all_life import list_media


class Result:
    def __init__(self, *, rows=None, scalar=None):
        self._rows = rows or []
        self._scalar = scalar

    def fetchall(self):
        return self._rows

    def mappings(self):
        return self

    def all(self):
        return self._rows

    def scalar_one(self):
        return self._scalar


class Session:
    def __init__(self, results):
        self.results = list(results)
        self.calls = []
        self.rollbacks = 0

    async def execute(self, statement, params=None):
        self.calls.append((str(statement), params or {}))
        result = self.results.pop(0)
        if isinstance(result, Exception):
            raise result
        return result

    async def rollback(self):
        self.rollbacks += 1


async def invoke(session):
    return await list_media(
        taxon_id=UUID("6db28640-67fb-4808-90de-956a856366f7"),
        db=session,
        limit=20,
        offset=0,
    )


def full_image_schema():
    return [
        (column,) for column in (
            "taxon_id", "mindex_id", "filename", "source", "source_url", "license",
            "attribution", "source_id", "species_confidence", "species_match_method",
            "verified", "label_state", "content_hash", "created_at",
        )
    ]


def test_exact_taxon_images_include_attribution_license_and_link_quality():
    session = Session([
        Result(rows=full_image_schema()),
        Result(rows=[{
            "id": "image-1",
            "taxon_id": "6db28640-67fb-4808-90de-956a856366f7",
            "mindex_id": "MYCO-IMG-1",
            "filename": "example.jpg",
            "source": "iNaturalist",
            "source_id": "observation-1",
            "source_url": "https://www.inaturalist.org/observations/1",
            "license": "CC BY-NC 4.0",
            "attribution": "Example observer",
            "species_confidence": 0.98,
            "species_match_method": "api",
            "verified": False,
            "label_state": "source_claimed",
            "content_hash": "a" * 64,
            "created_at": None,
        }]),
        Result(scalar=1),
        Result(rows=[]),
        Result(scalar=0),
        Result(rows=[]),
        Result(scalar=0),
    ])

    result = asyncio.run(invoke(session))

    assert result["image_state"] == "available"
    assert result["image_pagination"]["total"] == 1
    assert result["video_state"] == "available"
    assert result["video_pagination"]["total"] == 0
    assert result["audio_state"] == "available"
    assert result["audio_pagination"]["total"] == 0
    assert result["image"][0]["license"] == "CC BY-NC 4.0"
    assert result["image"][0]["attribution"] == "Example observer"
    assert result["image"][0]["verified"] is False
    assert "WHERE taxon_id = :id" in session.calls[1][0]
    assert session.calls[1][1]["id"] == "6db28640-67fb-4808-90de-956a856366f7"


def test_minimal_bootstrap_image_table_is_unavailable_not_empty():
    session = Session([Result(rows=[("id",), ("taxon_id",), ("filename",), ("mindex_id",)])])

    with pytest.raises(HTTPException) as raised:
        asyncio.run(invoke(session))

    assert raised.value.status_code == 503
    assert raised.value.detail["code"] == "image_schema_incomplete"
    assert len(session.calls) == 1
