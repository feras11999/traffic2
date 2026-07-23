#!/usr/bin/env python3
from __future__ import annotations

import csv
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import requests
from google.transit import gtfs_realtime_pb2

API_URL = "https://api.transport.nsw.gov.au/v1/gtfs/vehiclepos/buses"
ROUTE_FILTER = "343"
POLL_INTERVAL_SECONDS = 15
MAX_RUN_MINUTES = 350
REQUEST_TIMEOUT_SECONDS = 45

SYDNEY_TZ = ZoneInfo("Australia/Sydney")
FINAL_STOP_TIME = datetime(2026, 7, 26, 23, 0, 0, tzinfo=SYDNEY_TZ)

OUTPUT_COLUMNS = [
    "collected_at", "route_id", "trip_id", "start_time", "vehicle_id",
    "lat", "lon", "speed_mps", "bearing_deg", "congestion_level",
    "occupancy_status", "feed_vehicle_ts", "age_s",
]


def route_matches(route_id: str, wanted_route: str) -> bool:
    route_id = (route_id or "").strip()
    wanted_route = wanted_route.strip()
    return (
        route_id == wanted_route
        or route_id.endswith(f"_{wanted_route}")
        or route_id.split("_")[-1] == wanted_route
    )


def protobuf_value(message: Any, field_name: str) -> Any:
    try:
        if message.HasField(field_name):
            return getattr(message, field_name)
    except (ValueError, AttributeError):
        pass
    value = getattr(message, field_name, "")
    return value if value is not None else ""


def collect_rows(feed_bytes: bytes, collected_at: datetime) -> list[dict[str, Any]]:
    feed = gtfs_realtime_pb2.FeedMessage()
    feed.ParseFromString(feed_bytes)

    rows: list[dict[str, Any]] = []
    collected_epoch = int(collected_at.timestamp())

    for entity in feed.entity:
        if not entity.HasField("vehicle"):
            continue

        vp = entity.vehicle
        trip = vp.trip
        route_id = trip.route_id or ""

        if not route_matches(route_id, ROUTE_FILTER):
            continue

        feed_vehicle_ts = int(vp.timestamp or 0)
        age_s = collected_epoch - feed_vehicle_ts if feed_vehicle_ts else ""

        rows.append({
            "collected_at": collected_at.isoformat(),
            "route_id": route_id,
            "trip_id": trip.trip_id or "",
            "start_time": trip.start_time or "",
            "vehicle_id": vp.vehicle.id or "",
            "lat": protobuf_value(vp.position, "latitude"),
            "lon": protobuf_value(vp.position, "longitude"),
            "speed_mps": protobuf_value(vp.position, "speed"),
            "bearing_deg": protobuf_value(vp.position, "bearing"),
            "congestion_level": int(vp.congestion_level),
            "occupancy_status": int(vp.occupancy_status),
            "feed_vehicle_ts": feed_vehicle_ts or "",
            "age_s": age_s,
        })

    return rows


def append_rows(csv_path: Path, rows: list[dict[str, Any]]) -> None:
    new_file = not csv_path.exists()
    with csv_path.open("a", newline="", encoding="utf-8") as csv_file:
        writer = csv.DictWriter(csv_file, fieldnames=OUTPUT_COLUMNS)
        if new_file:
            writer.writeheader()
        writer.writerows(rows)


def main() -> int:
    api_key = os.getenv("TFNSW_API_KEY", "").strip()
    if not api_key:
        print("ERROR: TFNSW_API_KEY secret is missing.", file=sys.stderr)
        return 1

    started_at = datetime.now(SYDNEY_TZ)
    if started_at >= FINAL_STOP_TIME:
        print("Final stop time has passed. Nothing to collect.")
        return 0

    run_deadline_epoch = time.time() + MAX_RUN_MINUTES * 60
    csv_path = Path(f"route343_{started_at.strftime('%Y%m%d_%H%M%S')}.csv")

    headers = {
        "Authorization": f"apikey {api_key}",
        "Accept": "application/x-google-protobuf",
        "User-Agent": "UNSW-CVEN9415-Route343-Collector/1.0",
    }

    session = requests.Session()
    polls = 0
    saved_rows = 0
    consecutive_errors = 0

    print(f"Starting at {started_at.isoformat()}")
    print(f"Saving to {csv_path}")

    while True:
        now = datetime.now(SYDNEY_TZ)

        if now >= FINAL_STOP_TIME:
            print("Reached Sunday 11:00 PM Sydney time.")
            break

        if time.time() >= run_deadline_epoch:
            print("Reached this GitHub job's safe runtime limit.")
            break

        poll_started = time.monotonic()

        try:
            response = session.get(
                API_URL,
                headers=headers,
                timeout=REQUEST_TIMEOUT_SECONDS,
            )
            response.raise_for_status()

            rows = collect_rows(response.content, now)
            if rows:
                append_rows(csv_path, rows)
                saved_rows += len(rows)

            polls += 1
            consecutive_errors = 0
            print(
                f"{now.strftime('%Y-%m-%d %H:%M:%S %Z')} | "
                f"poll={polls} | rows={len(rows)} | total={saved_rows}",
                flush=True,
            )

        except Exception as exc:
            consecutive_errors += 1
            print(
                f"Error #{consecutive_errors}: {type(exc).__name__}: {exc}",
                file=sys.stderr,
                flush=True,
            )
            if consecutive_errors >= 10:
                return 1

        elapsed = time.monotonic() - poll_started
        time.sleep(max(0.0, POLL_INTERVAL_SECONDS - elapsed))

    if not csv_path.exists():
        append_rows(csv_path, [])

    print(f"Finished. Polls={polls}, rows={saved_rows}, file={csv_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
