#!/usr/bin/env python3
"""Publish current Environment Agency river-water temperatures for mapped UK rivers.

Uses the revised Environment Agency Hydrology Data API (15-minute measured water
quality temperature series), not air temperatures or modelled estimates. Only
sensors within the same 350 m geometry-matching tolerance as the UK level feed
are attached to the major river lines. Histories are reduced to six-hour samples.
"""
from __future__ import annotations

import datetime as dt
import json
import math
import re
import urllib.parse
import urllib.request
from pathlib import Path

from fetch_uk_river_sensors import OGL, RIVERS, assign_river, build_network, datetime_utc, read_json, reduce_history

# This script is kept under tools/ in PetriKlar-Source and at repository
# root in PetriKlar-Sensordaten.
SCRIPT_DIR = Path(__file__).resolve().parent
ROOT = SCRIPT_DIR if (SCRIPT_DIR / "assets").is_dir() else SCRIPT_DIR.parent
OUTPUT = ROOT / "assets/data/uk-river-temperatures.json"
API = "https://environment.data.gov.uk/hydrology"
SOURCE_URL = "https://environment.data.gov.uk/hydrology/doc/reference"
STATION_URL = f"{API}/id/stations"
READINGS_URL = f"{API}/data/readings"
ATTRIBUTION = "Contains Environment Agency Hydrology Data API water-temperature data, licensed under the Open Government Licence 3.0"
USER_AGENT = "PetriKlar/1.0 (+https://www.petriklar.com; river temperature map)"
TIMEOUT = 90
MAX_HISTORY_DAYS = 30
MAX_CURRENT_AGE = dt.timedelta(hours=48)
MEASURE_SUFFIXES = ("-temp-i-subdaily-C", "-temp-i-900-C-qualified")


def fetch_json(url: str) -> dict:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
        return json.loads(response.read().decode("utf-8"))


def api_url(base: str, params) -> str:
    return base + ".json?" + urllib.parse.urlencode(params, doseq=True)


def station_code_from_measure(measure_id: str) -> str | None:
    identifier = str(measure_id or "").rstrip("/").split("/")[-1]
    for suffix in MEASURE_SUFFIXES:
        if identifier.endswith(suffix):
            return identifier[:-len(suffix)]
    return None


def as_number(value):
    try:
        number = float(value)
        return number if math.isfinite(number) else None
    except (TypeError, ValueError):
        return None


def build_rows(stations, latest_readings, network, now: dt.datetime, history_readings=(), previous=None):
    """Normalize live API rows; inputs are injectable for deterministic tests."""
    if now.tzinfo is None:
        now = now.replace(tzinfo=dt.timezone.utc)
    now = now.astimezone(dt.timezone.utc)
    previous = previous or {}
    station_by_code = {}
    for station in stations:
        code = str(station.get("notation") or station.get("stationGuid") or "")
        status = station.get("status") or []
        status_label = status[0].get("label") if isinstance(status, list) and status and isinstance(status[0], dict) else "Active"
        if code and status_label == "Active":
            station_by_code[code] = station

    latest_by_measure = {}
    for reading in latest_readings:
        measure_id = (reading.get("measure") or {}).get("@id") or reading.get("measure")
        code = station_code_from_measure(measure_id)
        station = station_by_code.get(code)
        value = as_number(reading.get("value"))
        stamp = datetime_utc(reading.get("dateTime") or reading.get("date"))
        if (not station or value is None or not -2 <= value <= 40 or not stamp
                or stamp < now - dt.timedelta(days=MAX_HISTORY_DAYS)
                or stamp > now + dt.timedelta(minutes=15)):
            continue
        if not isinstance(station.get("lat"), (int, float)) or not isinstance(station.get("long"), (int, float)):
            continue
        river = assign_river(station["long"], station["lat"], network)
        if river not in RIVERS:
            continue
        current = latest_by_measure.get(measure_id)
        if current is None or stamp > current["stamp"]:
            latest_by_measure[measure_id] = {"station": station, "river": river, "value": value,
                                             "time": stamp.isoformat().replace("+00:00", "Z"), "stamp": stamp}

    measurements_by_code = {}
    for measure_id, current in latest_by_measure.items():
        code = str(current["station"].get("notation") or current["station"].get("stationGuid"))
        candidate = {"measure_id": measure_id, **current}
        old = measurements_by_code.get(code)
        if old is None or current["stamp"] > old["stamp"]:
            measurements_by_code[code] = candidate

    history_by_measure = {}
    for reading in history_readings:
        measure_id = (reading.get("measure") or {}).get("@id") or reading.get("measure")
        if measure_id not in latest_by_measure:
            continue
        stamp = reading.get("dateTime") or reading.get("date")
        value = as_number(reading.get("value"))
        if stamp and value is not None and -2 <= value <= 40:
            history_by_measure.setdefault(measure_id, []).append({"t": stamp, "v": value})

    rows = []
    for code, current in sorted(measurements_by_code.items()):
        station, river, measure_id = current["station"], current["river"], current["measure_id"]
        identifier = f"ea-water-temp-{code}"
        old = previous.get(identifier, {})
        history = list(old.get("history", {}).get("Wassertemperatur", []))
        history.extend(history_by_measure.get(measure_id, []))
        history.append({"t": current["time"], "v": current["value"]})
        history = reduce_history(history, now)
        row = {
            "id": identifier,
            "name": station.get("label") or code,
            "river": river,
            "river_assignment": "geometry-within-350m",
            "lat": station["lat"],
            "lon": station["long"],
            "src": "environment-agency",
            "source": "environment-agency",
            "source_station": code,
            "measure_id": measure_id,
            "source_url": f"{API}/id/stations/{urllib.parse.quote(code)}",
            "country": "GB",
            "license": "OGL-3.0",
            "license_url": OGL,
            "attribution": ATTRIBUTION,
            "items": [{"label": "Wassertemperatur", "value": current["value"],
                       "unit": "°C", "time": current["time"]}],
            "history": {"Wassertemperatur": history},
            "history_source": "Environment Agency Hydrology API 15-minute measured readings",
            "history_interval_hours": 6,
        }
        rows.append(row)

    # If a source temporarily omits a previously seen series, keep its dated
    # observations. The map's ordinary freshness rules age them out naturally.
    retained = {row["id"] for row in rows}
    for identifier, old in previous.items():
        if identifier in retained:
            continue
        current = next((item for item in old.get("items", []) if item.get("label") == "Wassertemperatur"), None)
        stamp = datetime_utc(current.get("time")) if current else None
        if stamp and now - stamp <= dt.timedelta(days=MAX_HISTORY_DAYS):
            rows.append(old)
    return rows


def collect(now=None, fetcher=fetch_json, network=None, previous=None):
    now = now or dt.datetime.now(dt.timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=dt.timezone.utc)
    network = network or build_network()
    previous = previous or {}
    station_url = api_url(STATION_URL, [
        ("observedProperty", "temperature"), ("status.label", "Active"), ("_limit", 10000),
    ])
    stations = fetcher(station_url).get("items", [])
    latest_url = api_url(READINGS_URL, [
        ("observedProperty", "temperature"), ("latest", ""), ("_limit", 10000),
    ])
    latest = fetcher(latest_url).get("items", [])

    # Restrict the historical bulk request to actual mapped stations with a
    # reading in the 30-day display window; the EA documents a 100k soft limit.
    provisional = build_rows(stations, latest, network, now, previous=previous)
    measure_ids = sorted({row["measure_id"] for row in provisional})
    history = []
    if measure_ids:
        start = (now - dt.timedelta(days=MAX_HISTORY_DAYS)).date().isoformat()
        end = now.date().isoformat()
        params = [("measure", measure_id) for measure_id in measure_ids]
        params.extend([("mineq-date", start), ("maxeq-date", end), ("_limit", 100000)])
        history_url = api_url(READINGS_URL, params)
        try:
            history = fetcher(history_url).get("items", [])
        except Exception as exc:
            # Current values remain useful even if the optional playback query
            # is temporarily unavailable; existing history is retained.
            print(f"WARNING: EA temperature history unavailable: {str(exc)[:180]}")
    rows = build_rows(stations, latest, network, now, history, previous)
    matched = {row["id"] for row in rows}
    report = {
        "publisher": "Environment Agency",
        "active_temperature_stations": len(stations),
        "matched_major_river_stations": len(matched),
        "current_within_48h": sum(
            1 for row in rows
            if (item := next((i for i in row.get("items", []) if i.get("label") == "Wassertemperatur"), None))
            and (stamp := datetime_utc(item.get("time"))) and now - stamp <= MAX_CURRENT_AGE
        ),
        "rivers": sorted({row["river"] for row in rows}),
        "history_resolution_hours": 6,
    }
    if not rows:
        raise RuntimeError("No Environment Agency river temperature readings matched the UK river geometry")
    return {"schema": 1, "updated": now.isoformat(timespec="seconds").replace("+00:00", "Z"),
            "source_url": SOURCE_URL, "source_report": report, "stations": rows}


def main():
    old_packet = read_json(OUTPUT, {})
    previous = {row.get("id"): row for row in old_packet.get("stations", []) if row.get("id")}
    try:
        packet = collect(previous=previous)
    except Exception as exc:
        if previous:
            print(f"WARNING: UK water-temperature refresh failed; preserving {len(previous)} prior stations: {str(exc)[:200]}")
            return
        raise
    if packet["stations"] == old_packet.get("stations") and packet["source_report"] == old_packet.get("source_report"):
        print(f"UK water-temperature data unchanged: {len(packet['stations'])} stations; retaining {old_packet.get('updated', 'prior timestamp')}")
        return
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(packet, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8")
    print("UK water temperatures refreshed:", packet["source_report"])


if __name__ == "__main__":
    main()
