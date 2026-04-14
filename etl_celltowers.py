#!/usr/bin/env python3
"""
MINDEX Cell Tower ETL — Load OpenCellID bulk data into signals.antennas

The OpenCellID CSV format:
  radio,mcc,net,area,cell,unit,lon,lat,range,samples,changeable,created,updated,averageSignal

This loader reads the gzipped CSV from NAS and bulk-inserts into MINDEX.
The full database has ~45M cell towers globally.

Usage:
  python etl_celltowers.py                    # Load all
  python etl_celltowers.py --limit 1000000    # Load first 1M
  python etl_celltowers.py --country US       # Load only US towers (MCC 310-316)
"""

import asyncio
import csv
import gzip
import io
import json
import logging
import os
import sys
import time

import asyncpg

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("celltower-etl")

DB_DSN = "postgresql://mycosoft:mycosoft_mindex_2026@192.168.0.189:5432/mindex"

# MCC (Mobile Country Code) to country mapping for major countries
MCC_COUNTRY = {
    "310": "US", "311": "US", "312": "US", "313": "US", "314": "US", "315": "US", "316": "US",
    "302": "CA",
    "334": "MX",
    "234": "GB", "235": "GB",
    "262": "DE",
    "208": "FR",
    "222": "IT",
    "214": "ES",
    "206": "BE", "204": "NL",
    "460": "CN", "461": "CN",
    "440": "JP", "441": "JP",
    "450": "KR",
    "404": "IN", "405": "IN",
    "466": "TW",
    "505": "AU",
    "530": "NZ",
    "724": "BR",
}

# Radio type mapping
RADIO_MAP = {
    "GSM": "2G", "CDMA": "2G",
    "UMTS": "3G", "WCDMA": "3G",
    "LTE": "4G",
    "NR": "5G",
}


async def load_celltowers(csv_path: str, limit: int = 0, country_filter: str = ""):
    log.info(f"Loading cell towers from {csv_path}")
    log.info(f"  Limit: {limit or 'unlimited'}, Country: {country_filter or 'all'}")

    conn = await asyncpg.connect(DB_DSN)
    log.info("Connected to MINDEX database")

    inserted = 0
    skipped = 0
    errors = 0
    batch = []
    BATCH_SIZE = 5000

    async def flush_batch():
        nonlocal inserted, errors
        if not batch:
            return
        try:
            await conn.executemany("""
                INSERT INTO signals.antennas (source, source_id, antenna_type, technology,
                    location, operator, frequency_mhz, properties)
                VALUES ('opencellid', $1, $2, $3,
                    ST_SetSRID(ST_MakePoint($4, $5), 4326)::geography,
                    $6, $7, $8::jsonb)
                ON CONFLICT DO NOTHING
            """, batch)
            inserted += len(batch)
        except Exception as e:
            errors += len(batch)
            if errors <= BATCH_SIZE:
                log.error(f"  Batch insert error: {e}")
        batch.clear()

    try:
        opener = gzip.open if csv_path.endswith(".gz") else open
        with opener(csv_path, "rt", encoding="utf-8", errors="replace") as f:
            reader = csv.DictReader(f)

            for row in reader:
                try:
                    lat = float(row.get("lat", 0))
                    lon = float(row.get("lon", 0))

                    # Skip invalid coordinates
                    if abs(lat) > 90 or abs(lon) > 180 or (lat == 0 and lon == 0):
                        skipped += 1
                        continue

                    radio = row.get("radio", "GSM")
                    mcc = row.get("mcc", "")
                    mnc = row.get("net", "")
                    lac = row.get("area", "")
                    cid = row.get("cell", "")
                    samples = int(row.get("samples", 0))
                    signal = int(row.get("averageSignal", 0))

                    # Country filter
                    if country_filter:
                        country = MCC_COUNTRY.get(mcc, "")
                        if country != country_filter:
                            skipped += 1
                            continue

                    source_id = f"{radio}-{mcc}-{mnc}-{lac}-{cid}"
                    technology = RADIO_MAP.get(radio, radio)
                    country = MCC_COUNTRY.get(mcc, "")

                    batch.append((
                        source_id,
                        "cell_tower",
                        technology,
                        lon, lat,
                        f"MCC:{mcc}/MNC:{mnc}",
                        None,  # frequency_mhz not in OpenCellID data
                        json.dumps({
                            "radio": radio, "mcc": mcc, "mnc": mnc,
                            "lac": lac, "cid": cid, "samples": samples,
                            "signal": signal, "country": country,
                        }),
                    ))

                    if len(batch) >= BATCH_SIZE:
                        await flush_batch()
                        if inserted % 50000 == 0 and inserted > 0:
                            log.info(f"  ... {inserted:,} towers inserted, {skipped:,} skipped")

                    if limit and (inserted + len(batch)) >= limit:
                        break

                except (ValueError, KeyError):
                    skipped += 1

        # Flush remaining
        await flush_batch()

    finally:
        await conn.close()

    log.info(f"Cell tower ETL complete:")
    log.info(f"  Inserted: {inserted:,}")
    log.info(f"  Skipped:  {skipped:,}")
    log.info(f"  Errors:   {errors:,}")
    return inserted


async def main():
    import argparse
    parser = argparse.ArgumentParser(description="MINDEX Cell Tower ETL")
    parser.add_argument("--file", default="/home/mycosoft/mindex/data/signals/cell_towers/cell_towers_full.csv.gz",
                        help="Path to OpenCellID CSV (gzipped)")
    parser.add_argument("--limit", type=int, default=0, help="Max towers to load (0=all)")
    parser.add_argument("--country", default="", help="Country filter (US, GB, DE, etc.)")
    args = parser.parse_args()

    # If running locally, use the NAS path
    csv_path = args.file

    # Check if file is local or on NAS
    if not os.path.exists(csv_path):
        # Try downloading a chunk locally for testing
        log.warning(f"File not found at {csv_path}")
        log.info("Attempting to run remotely on NAS via SSH...")

        # Run the ETL on the NAS itself via SSH + docker exec
        import paramiko
        ssh = paramiko.SSHClient()
        ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        ssh.connect("192.168.0.189", username="mycosoft", password=os.environ.get("VM_PASSWORD", "Loserology1!"), timeout=10)

        # Install asyncpg in the MINDEX container and run the script
        cmd = f"""docker exec mindex-api python3 -c "
import asyncio, csv, gzip, json, logging, time
from sqlalchemy import text
from mindex_api.dependencies import get_db_session_sync
logging.basicConfig(level=logging.INFO)
log = logging.getLogger('celltower')
log.info('Starting cell tower load from {csv_path}')
# This would need to be run inside the container
log.info('Cell tower ETL needs to run inside Docker container or with direct DB access')
"
"""
        # Actually, let's just use asyncpg directly since port 5432 is accessible
        log.info("Running ETL with direct DB connection over network...")
        log.info("But CSV file is on NAS - need to stream it")

        # Stream the gzipped CSV from NAS via SSH and load
        sftp = ssh.open_sftp()
        try:
            remote_file = sftp.open(csv_path, "r")
            log.info(f"Opened remote file: {csv_path}")

            conn = await asyncpg.connect(DB_DSN)
            log.info("Connected to MINDEX database")

            inserted = 0
            skipped = 0
            batch = []
            BATCH_SIZE = 5000

            # Read gzipped CSV through SFTP
            import io
            gz_data = remote_file.read()
            log.info(f"Read {len(gz_data) / 1024 / 1024:.1f} MB from NAS")

            decompressed = gzip.decompress(gz_data)
            log.info(f"Decompressed to {len(decompressed) / 1024 / 1024:.1f} MB")

            reader = csv.DictReader(io.StringIO(decompressed.decode("utf-8", errors="replace")))
            limit = args.limit

            for row in reader:
                try:
                    lat = float(row.get("lat", 0))
                    lon = float(row.get("lon", 0))
                    if abs(lat) > 90 or abs(lon) > 180 or (lat == 0 and lon == 0):
                        skipped += 1
                        continue

                    radio = row.get("radio", "GSM")
                    mcc = row.get("mcc", "")

                    if args.country:
                        country = MCC_COUNTRY.get(mcc, "")
                        if country != args.country:
                            skipped += 1
                            continue

                    mnc = row.get("net", "")
                    lac = row.get("area", "")
                    cid = row.get("cell", "")
                    source_id = f"{radio}-{mcc}-{mnc}-{lac}-{cid}"
                    technology = RADIO_MAP.get(radio, radio)

                    batch.append((
                        source_id, "cell_tower", technology, lon, lat,
                        f"MCC:{mcc}/MNC:{mnc}", None,
                        json.dumps({"radio": radio, "mcc": mcc, "mnc": mnc, "lac": lac, "cid": cid,
                                    "samples": int(row.get("samples", 0)), "country": MCC_COUNTRY.get(mcc, "")}),
                    ))

                    if len(batch) >= BATCH_SIZE:
                        try:
                            await conn.executemany("""
                                INSERT INTO signals.antennas (source, source_id, antenna_type, technology,
                                    location, operator, frequency_mhz, properties)
                                VALUES ('opencellid', $1, $2, $3,
                                    ST_SetSRID(ST_MakePoint($4, $5), 4326)::geography,
                                    $6, $7, $8::jsonb)
                                ON CONFLICT DO NOTHING
                            """, batch)
                            inserted += len(batch)
                        except Exception as e:
                            if inserted == 0:
                                log.error(f"Batch error: {e}")
                        batch.clear()
                        if inserted % 100000 == 0 and inserted > 0:
                            log.info(f"  ... {inserted:,} towers inserted")

                    if limit and inserted >= limit:
                        break
                except (ValueError, KeyError):
                    skipped += 1

            # Flush remaining
            if batch:
                try:
                    await conn.executemany("""
                        INSERT INTO signals.antennas (source, source_id, antenna_type, technology,
                            location, operator, frequency_mhz, properties)
                        VALUES ('opencellid', $1, $2, $3,
                            ST_SetSRID(ST_MakePoint($4, $5), 4326)::geography,
                            $6, $7, $8::jsonb)
                        ON CONFLICT DO NOTHING
                    """, batch)
                    inserted += len(batch)
                except Exception:
                    pass

            await conn.close()
            log.info(f"Cell tower ETL complete: {inserted:,} inserted, {skipped:,} skipped")

        finally:
            sftp.close()
            ssh.close()

        return

    # Local file path — direct load
    start = time.time()
    count = await load_celltowers(csv_path, args.limit, args.country)
    elapsed = time.time() - start
    log.info(f"Total time: {elapsed:.1f}s ({count / max(elapsed, 1):.0f} towers/sec)")


if __name__ == "__main__":
    asyncio.run(main())
