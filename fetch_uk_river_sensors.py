#!/usr/bin/env python3
"""Refresh open, live river-level gauges on the main British rivers.

England and Wales: Environment Agency flood-monitoring API.
Scotland: SEPA Hydrometric Time Series API.

Gauge-to-river assignment is spatial and constrained to the bundled OSM main
river geometries. Current readings retain the agency timestamp and unit; no
missing values or times are fabricated. OGL data attribution is stored per row.
"""
from __future__ import annotations

import concurrent.futures
import datetime as dt
import json
import math
import statistics
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

# This script is kept under tools/ in PetriKlar-Source and at repository
# root in PetriKlar-Sensordaten.
SCRIPT_DIR = Path(__file__).resolve().parent
ROOT = SCRIPT_DIR if (SCRIPT_DIR / "assets").is_dir() else SCRIPT_DIR.parent
GEOMETRY = ROOT / "assets/data/temperature-rivers.geojson"
OUTPUT = ROOT / "assets/data/uk-river-sensors.json"
EA = "https://environment.data.gov.uk/flood-monitoring"
SEPA = "https://timeseries.sepa.org.uk/KiWIS/KiWIS"
OGL = "https://www.nationalarchives.gov.uk/doc/open-government-licence/version/3/"
EA_ATTRIBUTION = "Environment Agency flood and river level data from the real-time data API (Beta)"
SEPA_ATTRIBUTION = "Contains SEPA hydrometric data, licensed under the Open Government Licence 3.0"
RIVERS = {
    "Aire", "Avon (Bristol)", "Clyde", "Dee (Aberdeenshire)", "Dee (Wales)",
    "Exe", "Great Ouse", "Medway", "Mersey", "Nene", "Ribble", "Severn",
    "Spey", "Tay", "Tees", "Thames", "Trent", "Tweed", "Tyne", "Wye",
    "Don (Aberdeenshire)",
    "Avon (Hampshire)", "Avon (Warwickshire)", "Tamar", "Fowey",
    "Tame (Midlands)", "Don (Yorkshire)", "Witham", "Weaver",
    "Leven (Cumbria)", "Kent", "Deben", "Stour (Suffolk)",
}
MAX_MATCH_KM = 0.35
EA_HISTORY_PER_RIVER = 2
SEPA_HISTORY_PER_RIVER = 1  # Anonymous SEPA API has a documented daily download cap.
TIMEOUT = 45


def fetch(url: str, *, referer: bool = False) -> dict | list:
    headers = {"User-Agent": "PetriKlar/1.0 (+https://www.petriklar.com; river map)"}
    if referer:
        # SEPA uses this string as a caller-specific credit bucket (not an HTTP URL).
        headers["Referer"] = "PetriKlar UK river maps"
    request = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
        return json.loads(response.read().decode("utf-8"))


def url_with(base: str, query: dict) -> str:
    return base + "?" + urllib.parse.urlencode(query)


def as_float(value):
    if isinstance(value, list):
        vals = [float(v) for v in value if isinstance(v, (int, float))]
        return statistics.mean(vals) if vals else None
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except (TypeError, ValueError):
        return None


def read_json(path: Path, fallback):
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return fallback


def build_network():
    data = read_json(GEOMETRY, {})
    network = {}
    for feature in data.get("features", []):
        name = feature.get("properties", {}).get("name")
        if name not in RIVERS:
            continue
        coordinates = feature.get("geometry", {}).get("coordinates", [])
        edges = []
        points = []
        for line in coordinates:
            points.extend(line)
            edges.extend((a, b) for a, b in zip(line, line[1:]))
        if points:
            network[name] = {
                "edges": edges,
                "bounds": (min(p[0] for p in points), min(p[1] for p in points),
                           max(p[0] for p in points), max(p[1] for p in points)),
            }
    missing = RIVERS - set(network)
    if missing:
        raise RuntimeError("Missing UK river geometry: " + ", ".join(sorted(missing)))
    return network


def distance_to_segment_km(lon, lat, a, b):
    scale_x = math.cos(math.radians(lat)) * 111.32
    px, py = lon * scale_x, lat * 111.32
    ax, ay = a[0] * scale_x, a[1] * 111.32
    bx, by = b[0] * scale_x, b[1] * 111.32
    dx, dy = bx - ax, by - ay
    denominator = dx * dx + dy * dy
    t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / denominator)) if denominator else 0.0
    return math.hypot(px - ax - t * dx, py - ay - t * dy)


def assign_river(lon, lat, network):
    if lon is None or lat is None:
        return None
    best = None
    # A small geographic bounding box keeps the search fast and avoids assigning
    # a gauge across a nearby parallel tributary or canal.
    lat_pad, lon_pad = 0.008, 0.015
    for name, river in network.items():
        x0, y0, x1, y1 = river["bounds"]
        if not (x0 - lon_pad <= lon <= x1 + lon_pad and y0 - lat_pad <= lat <= y1 + lat_pad):
            continue
        for a, b in river["edges"]:
            if lon < min(a[0], b[0]) - lon_pad or lon > max(a[0], b[0]) + lon_pad:
                continue
            if lat < min(a[1], b[1]) - lat_pad or lat > max(a[1], b[1]) + lat_pad:
                continue
            distance = distance_to_segment_km(lon, lat, a, b)
            if distance <= MAX_MATCH_KM and (best is None or distance < best[0]):
                best = (distance, name)
    return best[1] if best else None


def choose_representatives(rows, limit):
    selected = []
    by_river = {}
    for row in rows:
        by_river.setdefault(row["river"], []).append(row)
    for river, candidates in sorted(by_river.items()):
        if not candidates:
            continue
        bounds = build_network_cache.get(river, {}).get("bounds")
        if bounds:
            cx, cy = (bounds[0] + bounds[2]) / 2, (bounds[1] + bounds[3]) / 2
            first = min(candidates, key=lambda r: (r["lon"] - cx) ** 2 + (r["lat"] - cy) ** 2)
        else:
            first = candidates[0]
        chosen = [first]
        while len(chosen) < min(limit, len(candidates)):
            remaining = [r for r in candidates if r not in chosen]
            next_row = max(remaining, key=lambda r: min(
                (r["lon"] - x["lon"]) ** 2 * math.cos(math.radians(r["lat"])) ** 2
                + (r["lat"] - x["lat"]) ** 2 for x in chosen))
            chosen.append(next_row)
        selected.extend(chosen)
    return selected


def reduce_history(points, now):
    """Keep the closest available reading to each six-hour frame for 30 days."""
    cutoff = now - dt.timedelta(days=30)
    buckets = {}
    for point in points:
        try:
            stamp = dt.datetime.fromisoformat(str(point.get("t", "")).replace("Z", "+00:00"))
            value = as_float(point.get("v"))
            if stamp.tzinfo is None:
                stamp = stamp.replace(tzinfo=dt.timezone.utc)
            stamp = stamp.astimezone(dt.timezone.utc)
        except (TypeError, ValueError):
            continue
        if value is None or stamp < cutoff or stamp > now + dt.timedelta(minutes=15):
            continue
        bucket = int(stamp.timestamp() // (6 * 3600))
        boundary = bucket * 6 * 3600
        rank = abs(stamp.timestamp() - boundary)
        old = buckets.get(bucket)
        if old is None or rank < old[0]:
            buckets[bucket] = (rank, {"t": stamp.isoformat().replace("+00:00", "Z"), "v": value})
    return [v for _, v in sorted(buckets.values(), key=lambda item: item[1]["t"])]


def make_reference_band(method, low, high, unit):
    low, high = as_float(low), as_float(high)
    if low is None or high is None or low >= high:
        return None
    return {"method": method, "low": low, "center": (low + high) / 2,
            "high": high, "unit": unit}


def read_previous():
    packet = read_json(OUTPUT, {})
    return {row.get("id"): row for row in packet.get("stations", []) if row.get("id")}


def unpack_latest_time(item):
    return item.get("dateTime") or item.get("date")


def datetime_utc(value):
    try:
        stamp = dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=dt.timezone.utc)
        return stamp.astimezone(dt.timezone.utc)
    except (TypeError, ValueError):
        return None


def fetch_ea(network, previous, now):
    stations_url = url_with(f"{EA}/id/stations", {
        "parameter": "level", "status": "Active", "_view": "full", "_limit": 10000,
    })
    latest_url = url_with(f"{EA}/data/readings", {
        "latest": "", "parameter": "level", "qualifier": "Stage",
        "_view": "full", "_limit": 10000,
    })
    stations = fetch(stations_url).get("items", [])
    latest_rows = fetch(latest_url).get("items", [])
    latest = {}
    for item in latest_rows:
        measure = item.get("measure") or {}
        if measure.get("qualifier") != "Stage" or not measure.get("stationReference"):
            continue
        value = as_float(item.get("value"))
        time = unpack_latest_time(item)
        key = measure.get("@id") or measure.get("id")
        if value is None or not key:
            continue
        old = latest.get(key)
        if old is None or str(time or "") > str(old.get("time") or ""):
            latest[key] = {"value": value, "time": time, "unit": measure.get("unitName") or "m"}

    rows, measures_by_id = [], {}
    for station in stations:
        ref = station.get("stationReference") or station.get("notation")
        lat, lon = as_float(station.get("lat")), as_float(station.get("long"))
        river = assign_river(lon, lat, network)
        if not ref or not river:
            continue
        measures = [m for m in station.get("measures", [])
                    if m.get("parameter") == "level" and m.get("qualifier") == "Stage"]
        candidates = []
        for measure in measures:
            measure_id = measure.get("@id") or measure.get("id")
            reading = latest.get(measure_id)
            if reading:
                candidates.append((reading.get("time") or "", measure, reading))
        if not candidates:
            continue
        scale = station.get("stageScale") or {}
        if as_float(scale.get("typicalRangeLow")) is not None and as_float(scale.get("typicalRangeHigh")) is not None:
            local_datum = [candidate for candidate in candidates if candidate[1].get("unitName") == "mASD"]
            if local_datum:
                candidates = local_datum
        _, measure, reading = max(candidates, key=lambda x: str(x[0]))
        measure_url = measure.get("@id", "")
        measure_id = measure_url.rstrip("/").split("/")[-1]
        unit = reading["unit"]
        band = make_reference_band(
            "ea-typical-range-p05-p95",
            scale.get("typicalRangeLow"), scale.get("typicalRangeHigh"), unit)
        identifier = f"ea-{ref}"
        old = previous.get(identifier, {})
        row = {
            "id": identifier, "name": station.get("label") or str(ref), "river": river,
            "lat": lat, "lon": lon, "src": "environment-agency", "source": "environment-agency",
            "source_station": str(ref), "measure_id": measure_id,
            "source_url": f"{EA}/id/stations/{urllib.parse.quote(str(ref))}",
            "unit": unit, "country": "GB", "license": "OGL-3.0", "license_url": OGL,
            "attribution": EA_ATTRIBUTION, "reference_band": band,
            "items": [{"label": "Pegelstand", "value": reading["value"],
                       "unit": unit, "time": reading["time"]}],
            "current": {"t": reading["time"], "v": reading["value"]},
            "history": list(old.get("history", [])),
            "history_source": "Environment Agency stage readings",
            "history_interval_hours": 6,
        }
        rows.append(row)
        measures_by_id[identifier] = f"{EA}/id/measures/{urllib.parse.quote(measure_id)}/readings"

    representatives = choose_representatives(rows, EA_HISTORY_PER_RIVER)

    def get_history(row):
        old = previous.get(row["id"], {})
        old_time = (old.get("current") or {}).get("t")
        query = {"_limit": 10000}
        if old_time:
            query["since"] = old_time
        else:
            query["startdate"] = (now - dt.timedelta(days=7)).date().isoformat()
        try:
            data = fetch(url_with(measures_by_id[row["id"]], query)).get("items", [])
            points = list(row["history"])
            points.extend({"t": item.get("dateTime"), "v": as_float(item.get("value"))}
                          for item in data if item.get("dateTime") and as_float(item.get("value")) is not None)
            points.append(row["current"])
            row["history"] = reduce_history(points, now)
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, ValueError) as exc:
            if not row["history"]:
                row["history"] = [row["current"]]
            print(f"EA history {row['id']}: {str(exc)[:100]}")
        return row

    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as pool:
        list(pool.map(get_history, representatives))
    # Keep existing history for stations that are not the chosen timeline anchors.
    for row in rows:
        old = previous.get(row["id"], {})
        if not row["history"]:
            row["history"] = [row["current"]]
        if old.get("reference_band") and not row.get("reference_band"):
            row["reference_band"] = old["reference_band"]
    return rows


def sepa_query(params):
    return fetch(url_with(SEPA, {"service": "kisters", "type": "queryServices",
                                 "datasource": "0", "format": "json", **params}), referer=True)


def sepa_table(params):
    result = sepa_query(params)
    if not isinstance(result, list) or not result:
        return [], []
    header = result[0]
    if not isinstance(header, list):
        return [], []
    return header, [dict(zip(header, row)) for row in result[1:] if isinstance(row, list)]


def sepa_value_groups(result):
    if not isinstance(result, list):
        return {}
    return {str(group.get("ts_id")): group.get("data", [])
            for group in result if isinstance(group, dict) and group.get("ts_id")}


def fetch_sepa(network, previous, now):
    fields = ("station_name,station_no,stationparameter_name,stationparameter_no,ts_name,"
              "ts_shortname,ts_id,ts_path,ts_unitname,ts_unitsymbol,coverage,"
              "station_latitude,station_longitude,catchment_name,site_name,ca_sta")
    extras = "sepa_median_annual_minimum_level,sepa_median_annual_maximum_level"
    _, series = sepa_table({"request": "getTimeseriesList", "ts_path": "1/*/SG/15m.Cmd",
                            "returnfields": fields, "ca_sta_returnfields": extras})
    current_result = sepa_query({"request": "getTimeseriesValues", "ts_path": "1/*/SG/15m.Cmd",
                                 "returnfields": "Timestamp,Value,Quality Code"})
    current = sepa_value_groups(current_result)
    rows = []
    for meta in series:
        lat, lon = as_float(meta.get("station_latitude")), as_float(meta.get("station_longitude"))
        river = assign_river(lon, lat, network)
        tsid = str(meta.get("ts_id") or "")
        values = current.get(tsid, [])
        if not river or not tsid or not values:
            continue
        point = values[-1]
        value = as_float(point[1] if len(point) > 1 else None)
        time = point[0] if point else None
        stamp = datetime_utc(time)
        if value is None or not stamp or now - stamp > dt.timedelta(days=30):
            continue
        name, station_no = str(meta.get("station_name") or "SEPA gauge"), str(meta.get("station_no") or "")
        unit = str(meta.get("ts_unitsymbol") or "m")
        identifier = f"sepa-{station_no}"
        old = previous.get(identifier, {})
        band = make_reference_band("sepa-median-annual-extremes",
                                   meta.get("sepa_median_annual_minimum_level"),
                                   meta.get("sepa_median_annual_maximum_level"), unit)
        row = {
            "id": identifier, "name": name, "river": river, "lat": lat, "lon": lon,
            "src": "sepa", "source": "sepa", "source_station": station_no,
            "timeseries_id": tsid,
            "source_url": url_with(SEPA, {"service": "kisters", "type": "queryServices",
                "datasource": "0", "request": "getTimeseriesValues", "ts_id": tsid}),
            "unit": unit, "country": "GB", "license": "OGL-3.0", "license_url": OGL,
            "attribution": SEPA_ATTRIBUTION, "reference_band": band,
            "items": [{"label": "Pegelstand", "value": value, "unit": unit, "time": time}],
            "current": {"t": time, "v": value}, "history": list(old.get("history", [])),
            "history_source": "SEPA 15-minute hydrometric readings",
            "history_interval_hours": 6,
        }
        rows.append(row)

    representatives = choose_representatives(rows, SEPA_HISTORY_PER_RIVER)
    anchor_ids = {row["id"] for row in representatives}
    old_anchor_ids = {row_id for row_id in anchor_ids if previous.get(row_id, {}).get("history")}
    to_seed, to_update = [], []
    for row in representatives:
        if row["id"] in old_anchor_ids:
            to_update.append(row)
        else:
            to_seed.append(row)

    def pull_sepa_history(selected, period):
        if not selected:
            return
        ids = ",".join(row["timeseries_id"] for row in selected)
        try:
            groups = sepa_value_groups(sepa_query({"request": "getTimeseriesValues", "ts_id": ids,
                "period": period, "returnfields": "Timestamp,Value,Quality Code"}))
            for row in selected:
                points = list(row["history"])
                for point in groups.get(row["timeseries_id"], []):
                    if len(point) >= 2 and as_float(point[1]) is not None:
                        points.append({"t": point[0], "v": as_float(point[1])})
                points.append(row["current"])
                row["history"] = reduce_history(points, now)
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, ValueError) as exc:
            print(f"SEPA history {period}: {str(exc)[:140]}")

    # Seed a single evenly spaced Scottish gauge per major river with one week.
    # Later scheduled runs fetch only a short rolling window, respecting SEPA's
    # published anonymous API quota while the stored history grows to 30 days.
    pull_sepa_history(to_seed, "P7D")
    pull_sepa_history(to_update, "P6H")
    for row in rows:
        if not row["history"]:
            row["history"] = [row["current"]]
        if previous.get(row["id"], {}).get("reference_band") and not row.get("reference_band"):
            row["reference_band"] = previous[row["id"]]["reference_band"]
    return rows


build_network_cache = {}


def main():
    global build_network_cache
    now = dt.datetime.now(dt.timezone.utc)
    build_network_cache = build_network()
    previous = read_previous()
    sources = {}
    failed_sources = set()
    errors = []
    for name, source_id, collector in (("Environment Agency", "environment-agency", fetch_ea),
                                       ("SEPA", "sepa", fetch_sepa)):
        try:
            rows = collector(build_network_cache, previous, now)
            if rows:
                sources[source_id] = rows
            else:
                failed_sources.add(source_id)
                errors.append(f"{name}: no matching stations returned")
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, ValueError,
                KeyError, TypeError, RuntimeError) as exc:
            failed_sources.add(source_id)
            errors.append(f"{name}: {str(exc)[:180]}")
            print(errors[-1])
    stations = [row for rows in sources.values() for row in rows]
    preserved = [row for row in previous.values() if row.get("src") in failed_sources]
    stations.extend(preserved)
    # Preserve the last successfully published feed if both authorities are
    # temporarily unavailable; the UI will age its readings naturally.
    if not stations:
        stations = list(previous.values())
    if not stations:
        raise RuntimeError("No UK sensor data available and no previous snapshot to preserve")
    stations.sort(key=lambda row: row["id"])
    report = {"environment_agency": len(sources.get("environment-agency", [])),
              "sepa": len(sources.get("sepa", [])), "preserved_on_source_error": len(preserved)}
    report["history_anchors"] = sum(1 for row in stations if len(row.get("history", [])) > 1)
    old = read_json(OUTPUT, {})
    # Avoid a noisy commit when the source has not delivered a new value.
    if old.get("stations") == stations and old.get("source_report") == report:
        print(f"UK data unchanged: {len(stations)} stations; retaining {old.get('updated', 'prior timestamp')}")
        return
    packet = {
        "schema": 1,
        "updated": now.isoformat(timespec="seconds").replace("+00:00", "Z"),
        "source_url": "https://environment.data.gov.uk/flood-monitoring/doc/reference",
        "source_report": report,
        "stations": stations,
    }
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(packet, ensure_ascii=False, separators=(",", ":")) + "\n",
                      encoding="utf-8")
    print("UK rivers refreshed:", report, "total:", len(stations), "errors:", errors)


if __name__ == "__main__":
    main()
