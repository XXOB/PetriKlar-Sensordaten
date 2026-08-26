"""Additional *existing app* feeds for the public river-temperature map.

Kept in a separate JSON file so this cannot replace oxygen/quality data in the app.
Only represented rivers (plus the explicit Bodensee proxy) are collected. Network
timeouts and four workers bound the extra hourly job; no browser scraping needed.
"""
import json
import math
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

RIVERS = {x.casefold(): x for x in ("Rhein", "Main", "Mosel", "Donau", "Weser", "Elbe", "Oder", "Bodensee")}
PO = "https://www.pegelonline.wsv.de/webservices/rest-api/v2/"
NIZ = "https://inovum-services.de/gmb/md/v1/gewaesser;1.0.0?page%5Blimit%5D=1000"
HLNUG = "https://app.hlnug.de/json/wasser/"


def fetch_json(url):
    req = urllib.request.Request(url, headers={"User-Agent": "PetriKlar/1.0 (https://www.petriklar.com)", "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=20) as response:
        return json.load(response)


def iso(value):
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value / 1000, timezone.utc).isoformat(timespec="minutes")
    # API timestamps include an offset: never silently strip it.
    return datetime.fromisoformat(str(value).replace("Z", "+00:00")).isoformat(timespec="minutes")


def temperature(value):
    try:
        v = float(str(value).replace(",", "."))
        return v if math.isfinite(v) and -2 <= v <= 40 else None
    except (TypeError, ValueError):
        return None


def hourly(points):
    buckets = {}
    for p in points:
        try:
            v = temperature(p["v"])
            if v is None:
                continue
            t = iso(p["t"])
            ts = datetime.fromisoformat(t).timestamp()
            if ts > time.time() + 900 or ts < time.time() - 9 * 86400:
                continue
            bucket = int(ts // 3600)
            if bucket not in buckets or ts > buckets[bucket][0]:
                buckets[bucket] = (ts, {"t": t, "v": v})
        except (TypeError, ValueError, KeyError):
            continue
    return [buckets[k][1] for k in sorted(buckets)]


def row(sid, name, river, lat, lon, src, url, points):
    points = hourly(points)
    if not points:
        return None
    lat, lon = float(lat), float(lon)
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        return None
    last = points[-1]
    return {"id": sid, "name": name, "river": RIVERS[river.casefold()], "lat": lat, "lon": lon,
            "src": src, "source_url": url, "items": [{"label": "Wassertemperatur", "value": last["v"],
            "unit": "°C", "time": last["t"]}], "history": {"Wassertemperatur": points}}


def pegelonline():
    stations = fetch_json(PO + "stations.json?hasTimeseries=WT&includeTimeseries=true&includeCurrentMeasurement=true")
    stations = [s for s in stations if s.get("water", {}).get("longname", "").casefold() in RIVERS]

    def load(s):
        if s.get("latitude") is None or s.get("longitude") is None:
            print(f"[Temperatur/PO] {s['longname']}: keine Koordinaten, nicht kartierbar")
            return None
        url = PO + "stations/" + s["uuid"] + "/WT/measurements.json?start=P8D"
        points = []
        try:
            points = [{"t": p["timestamp"], "v": p["value"]} for p in fetch_json(url)]
        except Exception as ex:
            print(f"[Temperatur/PO] {s['longname']}: Verlauf nicht verfügbar ({ex})")
        for series in s.get("timeseries", []):
            if series.get("shortname") == "WT" and series.get("currentMeasurement"):
                m = series["currentMeasurement"]
                points.append({"t": m["timestamp"], "v": m["value"]})
        try:
            return row("po-" + s["uuid"], s["longname"], s["water"]["longname"], s["latitude"], s["longitude"], "pegelonline", url, points)
        except (ValueError, TypeError, KeyError) as ex:
            print(f"[Temperatur/PO] {s['longname']}: ungültige Stationsangabe ({ex})")
            return None

    with ThreadPoolExecutor(max_workers=4) as pool:
        return [r for r in pool.map(load, stations) if r]


def niz():
    result = []
    for item in fetch_json(NIZ).get("data", []):
        a = item.get("attributes", {})
        if a.get("gewaesser", "").casefold() not in RIVERS:
            continue
        mr = a.get("messreihen", {}).get("temp", {})
        if mr.get("status", "operational") != "operational":
            continue
        values, g = mr.get("values", {}), a.get("geometry", {})
        try:
            r = row("niz-" + str(a.get("id") or item["id"]), a.get("name") or a["gewaesser"], a["gewaesser"],
                    g["lat"], g["lon"], "niz", "https://niz.baden-wuerttemberg.de/",
                    [{"t": values.get("latest-ts"), "v": values.get("latest")}])
            if r:
                result.append(r)
        except (ValueError, TypeError, KeyError):
            continue
    return result


def hlnug():
    stations = fetch_json(HLNUG + "getThemeStations/6/63,67,55,69,110,126,138,144?tformat=d.m.Y%20H:i")
    selected = [s for s in stations if str(s.get("isConti")) == "1" and s.get("displayName", "").split(",")[0].strip().casefold() in RIVERS]
    result = []
    for s in selected:
        try:
            sid = str(s["stationId"])
            end = min(int(s.get("lastTimestampValueType1") or time.time()), int(time.time()))
            url = HLNUG + f"getStationChartData/{sid}/150/{end-8*86400}/{end+3600}?pad=1&valueType=1"
            chart = fetch_json(url)
            points = [{"t": p[0], "v": p[1]} for p in (chart[0].get("data", []) if chart else [])]
            parts = s["displayName"].split(",")
            r = row("he-" + sid, ",".join(parts[1:]).strip(), parts[0].strip(), s["lat"], s["lon"], "hlnug", url, points)
            if r:
                result.append(r)
        except Exception as ex:
            print(f"[Temperatur/HLNUG] {s.get('displayName')}: {ex}")
    return result


def collect(previous=()):
    results = {}
    # Keep original timestamps on cached rows: old values must not look fresh.
    for r in previous:
        if r.get("id"):
            results[r["id"]] = r
    for name, adapter in (("PEGELONLINE", pegelonline), ("LUBW NIZ", niz), ("HLNUG", hlnug)):
        try:
            rows = adapter()
            for r in rows:
                results[r["id"]] = r
            print(f"[Temperatur/{name}] {len(rows)} Stationen an Kartenflüssen")
        except Exception as ex:
            print(f"[Temperatur/{name}] Quelle vorübergehend nicht verfügbar: {ex}")
    return list(results.values())
