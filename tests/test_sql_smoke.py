from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

pytest.importorskip("psycopg")
import psycopg  # type: ignore  # noqa: E402
from psycopg.rows import dict_row  # type: ignore  # noqa: E402


DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://mindex:mindex@localhost:5432/mindex")


@pytest.fixture(scope="module")
def conn():
    try:
        connection = psycopg.connect(DATABASE_URL, autocommit=True, row_factory=dict_row)
    except psycopg.OperationalError as exc:
        pytest.skip(f"Unable to connect to database: {exc}")
    else:
        yield connection
        connection.close()


def test_insert_taxon_and_query_traits_view(conn):
    taxon_name = f"Agaricus test {uuid4()}"
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO core.taxon (canonical_name, rank, common_name, source)
            VALUES (%s, 'species', %s, %s)
            RETURNING id
            """,
            (taxon_name, "Testcap", "sql-smoke"),
        )
        taxon_id = cur.fetchone()["id"]

        cur.execute(
            """
            INSERT INTO bio.taxon_trait (taxon_id, trait_name, value_text, source)
            VALUES (%s, 'edibility', 'choice', 'sql-smoke')
            RETURNING id
            """,
            (taxon_id,),
        )
        trait_id = cur.fetchone()["id"]

        cur.execute(
            "SELECT traits FROM app.v_taxon_with_traits WHERE id = %s",
            (taxon_id,),
        )
        row = cur.fetchone()

    assert row is not None
    assert row["traits"], "traits aggregation should not be empty"
    assert row["traits"][0]["trait_name"] == "edibility"

    with conn.cursor() as cur:
        cur.execute("DELETE FROM bio.taxon_trait WHERE id = %s", (trait_id,))
        cur.execute("DELETE FROM core.taxon WHERE id = %s", (taxon_id,))


def test_insert_telemetry_and_query_latest_samples(conn):
    taxon_name = f"Telemetry taxon {uuid4()}"
    recorded_at = datetime.now(timezone.utc)
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO core.taxon (canonical_name, rank, common_name)
            VALUES (%s, 'species', %s)
            RETURNING id
            """,
            (taxon_name, "Telemetry specimen"),
        )
        taxon_id = cur.fetchone()["id"]

        cur.execute(
            """
            INSERT INTO telemetry.device (name, slug, taxon_id, location)
            VALUES (
                %s,
                %s,
                %s,
                ST_SetSRID(ST_MakePoint(%s, %s), 4326)::geography
            )
            RETURNING id
            """,
            (
                f"Device-{uuid4()}",
                f"device-{uuid4()}",
                taxon_id,
                -122.33,
                47.6,
            ),
        )
        device_id = cur.fetchone()["id"]

        cur.execute(
            """
            INSERT INTO telemetry.stream (device_id, key, unit, description)
            VALUES (%s, 'temperature', 'C', 'Air temp stream')
            RETURNING id
            """,
            (device_id,),
        )
        stream_id = cur.fetchone()["id"]

        cur.execute(
            """
            INSERT INTO telemetry.sample (
                stream_id,
                recorded_at,
                value_numeric,
                value_unit,
                location
            )
            VALUES (
                %s,
                %s,
                %s,
                'C',
                ST_SetSRID(ST_MakePoint(%s, %s), 4326)::geography
            )
            RETURNING id
            """,
            (
                stream_id,
                recorded_at,
                21.5,
                -122.33,
                47.6,
            ),
        )
        sample_id = cur.fetchone()["id"]

        cur.execute(
            """
            SELECT sample_id, recorded_at
            FROM app.v_device_latest_samples
            WHERE device_id = %s AND stream_id = %s
            """,
            (device_id, stream_id),
        )
        row = cur.fetchone()

    assert row is not None
    assert row["sample_id"] == sample_id
    assert abs(row["recorded_at"] - recorded_at) < timedelta(seconds=1)

    with conn.cursor() as cur:
        cur.execute("DELETE FROM telemetry.sample WHERE id = %s", (sample_id,))
        cur.execute("DELETE FROM telemetry.stream WHERE id = %s", (stream_id,))
        cur.execute("DELETE FROM telemetry.device WHERE id = %s", (device_id,))
        cur.execute("DELETE FROM core.taxon WHERE id = %s", (taxon_id,))
