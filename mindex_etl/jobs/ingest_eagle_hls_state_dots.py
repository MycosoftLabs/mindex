"""
Bulk-ingest HLS / permanent camera URLs for Eagle (eagle.video_sources).

Primary implementation: Caltrans `streamlist.htm` → per-loc .htm → `videoStreamURL` (.m3u8).

Stubs: CDOT, TxDOT, FDOT, WSDOT, SkylineWebcams, AirNow.gov, LiveWorldWebcams — wire in as
dedicated public feed URLs / parsers become available (no mock rows).

Run from repo root with DB env (see mindex_api.config) or MINDEX_DB_DSN:

  set PYTHONPATH=.
  python -m mindex_etl.jobs.ingest_eagle_hls_state_dots --caltrans --max-loc 100

  python -m mindex_etl.jobs.ingest_eagle_hls_state_dots --caltrans
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import httpx
import psycopg
from bs4 import BeautifulSoup

CALTRANS_STREAMLIST = "https://cwwp2.dot.ca.gov/vm/streamlist.htm"
M3U8_RE = re.compile(r'var\s+videoStreamURL\s*=\s*["\'](https://[^"\']+\.m3u8)', re.I)
# Some mirrors return GitHub-style markdown; live site often returns HTML tables.
ROW_RE = re.compile(
    r"^\| (?P<route>[^|]+) \| (?P<county>[^|]+) \| (?P<place>[^|]+) \|"
    r' \[(?P<label>[^\]]+)\]\((?P<url>https?://cwwp2\.dot\.ca\.gov/vm/loc/[^)]+)\)',
    re.MULTILINE,
)
LOC_HREF_RE = re.compile(
    r"https?://cwwp2\.dot\.ca\.gov/vm/loc/[^\"'\s<>)]+",
    re.I,
)
HTTP_TIMEOUT = 25.0

logger = logging.getLogger(__name__)


def _load_dsn() -> str:
    dsn = (os.environ.get("MINDEX_DB_DSN") or "").strip()
    if dsn:
        if dsn.startswith("postgresql+asyncpg://"):
            dsn = dsn.replace("postgresql+asyncpg://", "postgresql://", 1)
        return dsn
    # Repo checkout: mindex_api.config
    root = Path(__file__).resolve().parents[2]
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    from mindex_api.config import settings  # type: ignore

    u = str(settings.mindex_db_dsn)
    if u.startswith("postgresql+asyncpg://"):
        u = u.replace("postgresql+asyncpg://", "postgresql://", 1)
    return u


def _fetch_m3u8_from_loc_page(client: httpx.Client, loc_url: str) -> Optional[str]:
    try:
        r = client.get(loc_url, follow_redirects=True, timeout=HTTP_TIMEOUT)
        r.raise_for_status()
    except Exception as e:
        logger.debug("loc fetch failed %s: %s", loc_url, e)
        return None
    m = M3U8_RE.search(r.text)
    if not m:
        return None
    return m.group(1).strip()


def _row_from_url(url: str, label: str, route: str, county: str, place: str) -> Dict[str, Any]:
    u = url.replace("http://", "https://", 1)
    path = re.search(r"/vm/loc/(.+)\.htm", u, re.I)
    slug = (path.group(1).replace("/", "_") if path else hashlib.sha256(u.encode()).hexdigest()[:20])
    return {
        "route": route,
        "county": county,
        "place": place,
        "label": label,
        "loc_url": u,
        "id": f"caltrans_m3u8_{slug}",
    }


def _parse_caltrans_streamlist(html: str) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for m in ROW_RE.finditer(html):
        rows.append(
            _row_from_url(
                m.group("url"),
                m.group("label").strip(),
                m.group("route").strip(),
                m.group("county").strip(),
                m.group("place").strip(),
            )
        )
    if rows:
        return rows
    # HTML: table rows with anchor to /vm/loc/...
    soup = BeautifulSoup(html, "html.parser")
    for tr in soup.find_all("tr"):
        tds = tr.find_all("td")
        if len(tds) < 4:
            continue
        a = None
        for cand in tds[3].find_all("a", href=True):
            if "vm/loc" in (cand.get("href") or ""):
                a = cand
                break
        if not a:
            continue
        href = a["href"].strip()
        if not href.lower().startswith("http"):
            href = "https://cwwp2.dot.ca.gov" + href
        label = a.get_text(strip=True) or href
        route = tds[0].get_text(strip=True) if tds[0] else ""
        county = tds[1].get_text(strip=True) if len(tds) > 1 else ""
        place = tds[2].get_text(strip=True) if len(tds) > 2 else ""
        rows.append(_row_from_url(href, label, route, county, place))
    if not rows:
        # Last resort: every unique loc link in page
        seen: set[str] = set()
        for m in LOC_HREF_RE.finditer(html):
            u = m.group(0).rstrip(").,;")
            u = u.replace("http://", "https://", 1)
            if u in seen:
                continue
            seen.add(u)
            rows.append(_row_from_url(u, u, "", "", ""))
    return rows


def ingest_caltrans(
    dsn: str,
    max_loc: Optional[int] = None,
    dry_run: bool = False,
) -> Tuple[int, int]:
    """Fetch Caltrans stream list and loc pages, upsert eagle.video_sources."""
    with httpx.Client(headers={"User-Agent": "MINDEX-EagleIngest/1.0"}) as client:
        r = client.get(CALTRANS_STREAMLIST, follow_redirects=True, timeout=60.0)
        r.raise_for_status()
        rows = _parse_caltrans_streamlist(r.text)
    if not rows:
        logger.warning("No rows parsed from stream list — page format may have changed")
        return 0, 0
    if max_loc is not None:
        rows = rows[: max(0, max_loc)]

    ok, err = 0, 0
    to_write: List[Dict[str, Any]] = []
    with httpx.Client(headers={"User-Agent": "MINDEX-EagleIngest/1.0"}) as client:
        with ThreadPoolExecutor(max_workers=8) as ex:
            fut_map = {ex.submit(_fetch_m3u8_from_loc_page, client, row["loc_url"]): row for row in rows}
            for fut in as_completed(fut_map):
                row = fut_map[fut]
                try:
                    m3u8 = fut.result()
                except Exception as e:  # noqa: BLE001
                    err += 1
                    if err <= 5:
                        logger.warning("m3u8 task failed %s: %s", row["loc_url"], e)
                    continue
                if not m3u8:
                    err += 1
                    continue
                meta = {
                    "route": row["route"],
                    "county": row["county"],
                    "place": row["place"],
                    "label": row["label"],
                    "loc_url": row["loc_url"],
                }
                to_write.append(
                    {
                        "id": row["id"],
                        "stream_url": m3u8,
                        "embed_url": row["loc_url"],
                        "provenance": "caltrans_streamlist_htm+loc_htm_m3u8",
                        "retention": {"ingest_job": "ingest_eagle_hls_state_dots", "feed": "caltrans"},
                        "meta": meta,
                    }
                )
                ok += 1

    if dry_run:
        logger.info("Dry run: would upsert %d rows (skipped)", len(to_write))
        return len(to_write), 0

    upsert_sql = """
        INSERT INTO eagle.video_sources (
            id, kind, provider, stable_location, lat, lng, location_confidence,
            stream_url, embed_url, media_url, source_status, permissions, retention_policy,
            provenance_method, updated_at
        ) VALUES (
            %(id)s, 'permanent', 'caltrans', true, NULL, NULL, NULL,
            %(stream_url)s, %(embed_url)s, NULL, 'active', %(permissions)s::jsonb, %(retention_policy)s::jsonb,
            %(provenance_method)s, NOW()
        )
        ON CONFLICT (id) DO UPDATE SET
            kind = EXCLUDED.kind,
            provider = EXCLUDED.provider,
            stream_url = EXCLUDED.stream_url,
            embed_url = EXCLUDED.embed_url,
            source_status = EXCLUDED.source_status,
            retention_policy = EXCLUDED.retention_policy,
            provenance_method = EXCLUDED.provenance_method,
            updated_at = NOW()
    """
    upsert_sql_legacy = """
        INSERT INTO eagle.video_sources (
            id, kind, provider, stable_location, lat, lng, location_confidence,
            stream_url, embed_url, media_url, source_status, permissions, retention_policy,
            updated_at
        ) VALUES (
            %(id)s, 'permanent', 'caltrans', true, NULL, NULL, NULL,
            %(stream_url)s, %(embed_url)s, NULL, 'active', %(permissions)s::jsonb, %(retention_policy)s::jsonb,
            NOW()
        )
        ON CONFLICT (id) DO UPDATE SET
            kind = EXCLUDED.kind,
            provider = EXCLUDED.provider,
            stream_url = EXCLUDED.stream_url,
            embed_url = EXCLUDED.embed_url,
            source_status = EXCLUDED.source_status,
            retention_policy = EXCLUDED.retention_policy,
            updated_at = NOW()
    """

    werr = 0
    with psycopg.connect(dsn) as conn:
        for row in to_write:
            perms_d: Dict[str, Any] = {"caltrans": row["meta"]} if row.get("meta") else {}
            params = {
                "id": row["id"],
                "stream_url": row["stream_url"],
                "embed_url": row["embed_url"],
                "permissions": json.dumps(perms_d),
                "retention_policy": json.dumps(row.get("retention") or {}),
                "provenance_method": (row.get("provenance") or "caltrans_streamlist")[:200],
            }
            try:
                conn.execute(upsert_sql, params)
            except Exception as e:  # noqa: BLE001
                err = str(e).lower()
                if "provenance_method" in err or "column" in err:
                    try:
                        conn.execute(upsert_sql_legacy, {k: v for k, v in params.items() if k != "provenance_method"})
                    except Exception as e2:  # noqa: BLE001
                        werr += 1
                        if werr <= 5:
                            logger.warning("upsert %s: %s", row["id"], e2)
                else:
                    werr += 1
                    if werr <= 5:
                        logger.warning("upsert %s: %s", row["id"], e)
        conn.commit()

    return ok, err + werr


# --- Provider stubs (no fake camera rows) ---------------------------------

def stub_cdot() -> int:
    """CDOT public CCTV HLS: wire parser when a stable public index URL is in env."""
    url = (os.environ.get("MINDEX_CDOT_CCTV_INDEX_URL") or "").strip()
    if not url:
        logger.info("CDOT: set MINDEX_CDOT_CCTV_INDEX_URL to enable (stub)")
        return 0
    raise NotImplementedError("CDOT parser not implemented; URL reserved for MINDEX_CDOT_CCTV_INDEX_URL")


def stub_txdot() -> int:
    url = (os.environ.get("MINDEX_TXDOT_CCTV_INDEX_URL") or "").strip()
    if not url:
        logger.info("TxDOT: set MINDEX_TXDOT_CCTV_INDEX_URL to enable (stub)")
        return 0
    raise NotImplementedError("TxDOT parser not implemented")


def stub_fdot() -> int:
    url = (os.environ.get("MINDEX_FDOT_CCTV_INDEX_URL") or "").strip()
    if not url:
        logger.info("FLDOT: set MINDEX_FDOT_CCTV_INDEX_URL to enable (stub)")
        return 0
    raise NotImplementedError("FDOT parser not implemented")


def stub_ws_dot() -> int:
    url = (os.environ.get("MINDEX_WSDOT_CCTV_INDEX_URL") or "").strip()
    if not url:
        logger.info("WSDOT: set MINDEX_WSDOT_CCTV_INDEX_URL to enable (stub)")
        return 0
    raise NotImplementedError("WSDOT parser not implemented")


def stub_skylinewebcams() -> int:
    if not (os.environ.get("MINDEX_SKYLINE_FEED_URL") or "").strip():
        logger.info("SkylineWebcams: set MINDEX_SKYLINE_FEED_URL (or API contract) to enable (stub)")
    return 0


def stub_airnow() -> int:
    """AirNow.gov — camera/video feeds are not the primary AQI API; add dedicated ingest if product defines URLs."""
    if not (os.environ.get("MINDEX_AIRNOW_CAM_URL") or "").strip():
        logger.info("AirNow: set MINDEX_AIRNOW_CAM_URL when a camera listing endpoint is available (stub)")
    return 0


def stub_liveworldwebcams() -> int:
    if not (os.environ.get("MINDEX_LIVEWORLDWEBCAMS_LIST_URL") or "").strip():
        logger.info("LiveWorldWebcams: set MINDEX_LIVEWORLDWEBCAMS_LIST_URL to enable (stub)")
    return 0


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    p = argparse.ArgumentParser()
    p.add_argument("--caltrans", action="store_true", help="Ingest Caltrans streamlist + loc pages")
    p.add_argument("--max-loc", type=int, default=None, help="Limit loc pages (testing)")
    p.add_argument("--dry-run", action="store_true", help="Fetch and parse but do not write DB")
    p.add_argument("--stubs", action="store_true", help="Log stub provider env hints only")
    args = p.parse_args()
    dsn = _load_dsn()
    if not dsn and not args.dry_run and args.caltrans:
        logger.error("Set MINDEX_DB_DSN or run from a configured mindex environment")
        sys.exit(1)
    t0 = time.time()
    if args.caltrans:
        u, e = ingest_caltrans(dsn, max_loc=args.max_loc, dry_run=args.dry_run)
        logger.info("Caltrans: upserted/ok=%s errors+missing_m3u8=%s in %.1fs", u, e, time.time() - t0)
    if args.stubs:
        stub_cdot()
        stub_txdot()
        stub_fdot()
        stub_ws_dot()
        stub_skylinewebcams()
        stub_airnow()
        stub_liveworldwebcams()
    if not args.caltrans and not args.stubs:
        p.print_help()
        sys.exit(0)


if __name__ == "__main__":
    main()
