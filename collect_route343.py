from __future__ import annotations

import csv
import os
import signal
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import requests
from google.transit import gtfs_realtime_pb2

SYDNEY = ZoneInfo("Australia/Sydney")
API_URL = os.getenv(
    "TFNSW_VEHICLE_POSITIONS_URL",
    "https://api.transport.nsw.gov.au/v1/gtfs/vehiclepos/buses",
)
API_KEY = os.getenv("TFNSW_API_KEY", "").strip()
ROUTE = os.getenv("ROUTE_ID", "343").strip()
POLL_SECONDS = max(10, int(os.getenv("POLL_SECONDS", "15")))
MAX_RUNTIME_MINUTES = max(5, int(os.getenv("MAX_RUNTIME_MINUTES", "340")))
STOP_AT = os.getenv("STOP_AT_SYDNEY", "2026-07-26T23:00:00")
OUTPUT_DIR = Path(os.getenv("OUTPUT_DIR", "output"))

FIELDS = [
    "collection_time_utc",
    "collection_time_sydney",
    "feed_timestamp",
    "entity_id",
    "trip_id",
    "route_id",
    "start_time",
    "start_date",
    "schedule_relationship",
    "vehicle_id",
    "vehicle_label",
    "license_plate",
    "latitude",
    "longitude",
    "bearing",
    "odometer_m",
    "speed_mps",
    "speed_kmh",
    "current_stop_sequence",
    "stop_id",
    "current_status",
    "congestion_level",
    "occupancy_status",
]

stop_requested = False


def request_stop(signum, frame):
    global stop_requested
    stop_requested = True
    print(f"Received signal {signum}; finishing safely.", flush=True)


signal.signal(signal.SIGTERM, request_stop)
signal.signal(signal.SIGINT, request_stop)


def enum_name(message, field_name: str, value: int) -> str:
    try:
        field = message.DESCRIPTOR.fields_by_name[field_name]
        enum_value = field.enum_type.values_by_number.get(value)
        return enum_value.name if enum_value else str(value)
    except Exception:
        return str(value)


def route_matches(route_id: str) -> bool:
    route_id = (route_id or "").strip()
    if route_id == ROUTE:
        return True
    parts = route_id.replace("-", "_").split("_")
    return ROUTE in parts


def parse_stop_at(value: str) -> datetime:
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=SYDNEY)
    return dt.astimezone(SYDNEY)


def fetch_feed(session: requests.Session) -> gtfs_realtime_pb2.FeedMessage:
    response = session.get(
        API_URL,
        headers={
            "Authorization": f"apikey {API_KEY}",
            "Accept": "application/x-google-protobuf",
            "User-Agent": "UNSW-CVEN9415-Route343-Collector/1.0",
        },
        timeout=30,
    )
    response.raise_for_status()
    feed = gtfs_realtime_pb2.FeedMessage()
    feed.ParseFromString(response.content)
    return feed


def entity_to_row(entity, collected_utc: datetime, feed_timestamp: int) -> dict | None:
    if not entity.HasField("vehicle"):
        return None

    vehicle = entity.vehicle
    route_id = vehicle.trip.route_id if vehicle.HasField("trip") else ""
    if not route_matches(route_id):
        return None

    trip = vehicle.trip
    position = vehicle.position
    descriptor = vehicle.vehicle

    speed_mps = position.speed if position.HasField("speed") else ""
    speed_kmh = round(float(speed_mps) * 3.6, 3) if speed_mps != "" else ""

    return {
        "collection_time_utc": collected_utc.isoformat(),
        "collection_time_sydney": collected_utc.astimezone(SYDNEY).isoformat(),
        "feed_timestamp": feed_timestamp or "",
        "entity_id": entity.id,
        "trip_id": trip.trip_id,
        "route_id": route_id,
        "start_time": trip.start_time,
        "start_date": trip.start_date,
        "schedule_relationship": enum_name(
            trip, "schedule_relationship", trip.schedule_relationship
        ),
        "vehicle_id": descriptor.id,
        "vehicle_label": descriptor.label,
        "license_plate": descriptor.license_plate,
        "latitude": position.latitude if position.HasField("latitude") else "",
        "longitude": position.longitude if position.HasField("longitude") else "",
        "bearing": position.bearing if position.HasField("bearing") else "",
        "odometer_m": position.odometer if position.HasField("odometer") else "",
        "speed_mps": speed_mps,
        "speed_kmh": speed_kmh,
        "current_stop_sequence": vehicle.current_stop_sequence,
        "stop_id": vehicle.stop_id,
        "current_status": enum_name(vehicle, "current_status", vehicle.current_status),
        "congestion_level": enum_name(
            vehicle, "congestion_level", vehicle.congestion_level
        ),
        "occupancy_status": enum_name(
            vehicle, "occupancy_status", vehicle.occupancy_status
        ),
    }


def main() -> int:
    if not API_KEY:
        print("ERROR: TFNSW_API_KEY is missing.", file=sys.stderr)
        return 2

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    now_sydney = datetime.now(SYDNEY)
    stop_at = parse_stop_at(STOP_AT)

    if now_sydney >= stop_at:
        print(f"Collection window finished at {stop_at.isoformat()}. Nothing to do.")
        return 0

    filename = OUTPUT_DIR / f"route{ROUTE}_{now_sydney:%Y%m%d_%H%M%S}.csv"
    runtime_deadline = time.monotonic() + MAX_RUNTIME_MINUTES * 60
    seen = set()
    total_rows = 0
    polls = 0
    failures = 0

    print(f"Writing to {filename}", flush=True)
    print(f"Stopping no later than {stop_at.isoformat()}", flush=True)

    with requests.Session() as session, filename.open(
        "w", newline="", encoding="utf-8"
    ) as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS)
        writer.writeheader()
        handle.flush()

        while not stop_requested:
            if datetime.now(SYDNEY) >= stop_at:
                print("Reached Sydney stop time.", flush=True)
                break
            if time.monotonic() >= runtime_deadline:
                print("Reached safe GitHub Actions runtime limit.", flush=True)
                break

            started = time.monotonic()
            collected_utc = datetime.now(timezone.utc)

            try:
                feed = fetch_feed(session)
                feed_timestamp = int(feed.header.timestamp) if feed.header.timestamp else 0
                added = 0

                for entity in feed.entity:
                    row = entity_to_row(entity, collected_utc, feed_timestamp)
                    if row is None:
                        continue

                    key = (
                        row["feed_timestamp"],
                        row["entity_id"],
                        row["trip_id"],
                        row["vehicle_id"],
                        row["latitude"],
                        row["longitude"],
                    )
                    if key in seen:
                        continue

                    seen.add(key)
                    writer.writerow(row)
                    added += 1
                    total_rows += 1

                handle.flush()
                polls += 1
                print(
                    f"{datetime.now(SYDNEY):%Y-%m-%d %H:%M:%S %Z} | "
                    f"poll={polls} | added={added} | total={total_rows}",
                    flush=True,
                )
            except requests.HTTPError as exc:
                failures += 1
                status = exc.response.status_code if exc.response is not None else "unknown"
                print(f"HTTP error {status}: {exc}", flush=True)
                if status in (401, 403):
                    print("Check the TFNSW_API_KEY repository secret.", flush=True)
                    break
            except Exception as exc:
                failures += 1
                print(f"Poll failed ({type(exc).__name__}): {exc}", flush=True)

            elapsed = time.monotonic() - started
            time.sleep(max(0, POLL_SECONDS - elapsed))

    print(
        f"Finished. file={filename} rows={total_rows} polls={polls} failures={failures}",
        flush=True,
    )
    return 0 if total_rows > 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
