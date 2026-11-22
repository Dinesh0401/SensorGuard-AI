"""Ingest real Q1 2024 observations for the 8 approved pilot monitoring stations.

Persists raw batches with cryptographic hashes, station metadata, sensor registries,
and normalized, versioned observations into the SQLite database.
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import UTC, datetime

from dotenv import load_dotenv

from sensorguard.db.session import create_db_engine, get_session_factory, init_db
from sensorguard.ingest import IngestionPipeline
from sensorguard.providers import OpenAQClient

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

# 8 approved reference monitoring stations across UK AURN
PILOT_LOCATION_IDS = [141, 153, 154, 157, 698, 804, 1795, 2312]
DEFAULT_WINDOW_FROM = "2024-01-01T00:00:00Z"
DEFAULT_WINDOW_TO = "2024-03-31T23:59:59Z"


def main() -> int:
    load_dotenv()

    parser = argparse.ArgumentParser(description="Ingest pilot monitoring network data")
    parser.add_argument(
        "--from", dest="datetime_from", default=DEFAULT_WINDOW_FROM, help="UTC start timestamp"
    )
    parser.add_argument("--to", dest="datetime_to", default=DEFAULT_WINDOW_TO, help="UTC end timestamp")
    parser.add_argument("--max-pages", type=int, default=5, help="Max pages per sensor (at 1000 per page)")
    parser.add_argument("--page-size", type=int, default=1000, help="Page size (up to 1000)")
    args = parser.parse_args()

    engine = create_db_engine()
    init_db(engine)
    session_factory = get_session_factory(engine)

    client = OpenAQClient()

    logger.info(
        "Starting ingestion for %d pilot stations (%s -> %s)...",
        len(PILOT_LOCATION_IDS),
        args.datetime_from,
        args.datetime_to,
    )
    start_time = datetime.now(UTC)

    total_inserted = 0
    total_deduped = 0
    total_invalid = 0

    with session_factory() as session:
        pipeline = IngestionPipeline(session)

        for loc_id in PILOT_LOCATION_IDS:
            logger.info("Fetching metadata for location %d...", loc_id)
            loc_resp = client.get_location(loc_id)
            loc_results = loc_resp.payload.get("results", [])
            if not loc_results:
                logger.warning("No details returned for location %d, skipping.", loc_id)
                continue

            loc_data = loc_results[0]
            station, sensors = pipeline.upsert_station_and_sensors(loc_data)
            logger.info(
                "Upserted station '%s' (DB id: %d) with %d sensors", station.name, station.id, len(sensors)
            )

            pm25_sensors = [s for s in sensors if s.parameter == "pm25"]
            if not pm25_sensors:
                logger.warning("No PM2.5 sensor found for location %d", loc_id)
                continue

            for sensor in pm25_sensors:
                logger.info(
                    "Ingesting hours for sensor %d (DB id: %d)...", sensor.provider_sensor_id, sensor.id
                )
                res = pipeline.ingest_sensor_hours(
                    sensor_db_id=sensor.id,
                    provider_sensor_id=sensor.provider_sensor_id,
                    client=client,
                    datetime_from=args.datetime_from,
                    datetime_to=args.datetime_to,
                    max_pages=args.max_pages,
                    page_size=args.page_size,
                )
                session.commit()
                logger.info(
                    "Sensor %d: fetched=%d, inserted=%d, deduped=%d, invalid=%d",
                    sensor.provider_sensor_id,
                    res.records_fetched,
                    res.records_inserted,
                    res.records_deduplicated,
                    res.records_invalid,
                )
                total_inserted += res.records_inserted
                total_deduped += res.records_deduplicated
                total_invalid += res.records_invalid

    duration = (datetime.now(UTC) - start_time).total_seconds()
    logger.info(
        "Ingestion completed in %.1fs: inserted=%d, deduplicated=%d, invalid=%d",
        duration,
        total_inserted,
        total_deduped,
        total_invalid,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
