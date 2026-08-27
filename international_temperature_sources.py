"""Verified, public temperature feeds for the existing map rivers (CZ and NL).

No headless browser, credentials or paid service. Original observation times and
agency quality flags are respected. FR historical-only rows and unresolved PL/SK
reuse conditions are documented, not silently enabled as live measurements.
"""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import json
import urllib.request

from temperature_sources import row

CHMI = "https://opendata.chmi.cz/hydrology/now/"
RWS = "https://ddapi20-waterwebservices.rijkswaterstaat.nl/ONLINEWAARNEMINGENSERVICES/OphalenWaarnemingen"
CHMI_LICENSE = "https://creativecommons.org/licenses/by/4.0/"
# Verified in the new RWS catalogue. Do not include nearby Maas/canal stations.
# The drawn Rhine/Waal route ends in Haringvliet, NOT at Hoek van Holland on
# another delta arm. Verified upper sampling horizon at each actual route site;
# never average the independently reported deeper horizons.
RWS_STATIONS = (
    {"Code": "middelharnis.meetboei", "height": "-200", "reference": "WATSGL"},
    {"Code": "haringvliet.2", "height": "-200", "reference": "WATSGL"},
    {"Code": "stellendam.binnen", "height": "-200", "reference": "WATSGL"},
)


def read_json(url, body=None):
    request = urllib.request.Request(url,
        data=json.dumps(body).encode("utf8") if body is not None else None,
        headers={"User-Agent": "PetriKlar/1.0 (https://www.petriklar.com)",
                 "Accept": "application/json", "Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=15) as response:
        data = response.read(8_000_001)
    if len(data) > 8_000_000:
        raise ValueError("Temperaturantwort überschreitet das Größenlimit")
    return json.loads(data) if data else {}


def chmi_catalog(payload):
    table = payload["data"]["data"]
    keys = table["header"].split(",")
    stations = [dict(zip(keys, values)) for values in table["values"]]
    return [s for s in stations if s.get("STREAM_NAME") in ("Labe", "Odra")]


def parse_chmi(station, payload):
    points = []
    for obj in payload.get("objList", []):
        if obj.get("objID") != station["objID"]:
            continue
        for series in obj.get("tsList", []):
            if series.get("tsConID") != "TH" or series.get("unit") != "0C":
                continue
            points.extend({"t": p.get("dt"), "v": p.get("value")} for p in series.get("tsData", []))
    result = row("chmi-" + station["DBC"], station["STATION_NAME"],
        {"Labe": "Elbe", "Odra": "Oder"}[station["STREAM_NAME"]], station["GEOGR1"], station["GEOGR2"],
        "chmi", CHMI + "data/" + station["objID"] + ".json", points)
    if result:
        result.update(country="CZ", license="CC BY 4.0", license_url=CHMI_LICENSE,
            attribution="Český hydrometeorologický ústav (ČHMÚ)",
            data_note="Operative, noch nicht abschließend geprüfte Messwerte. Zeitangaben der Quelle unverändert übernommen.")
    return result


def chmi():
    stations = chmi_catalog(read_json(CHMI + "metadata/meta1.json"))

    def load(station):
        try:
            payload = read_json(CHMI + "data/" + station["objID"] + ".json")
            return parse_chmi(station, payload)
        except Exception as ex:
            print(f"[Temperatur/ČHMÚ] {station['STATION_NAME']}: {ex}")
            return None

    with ThreadPoolExecutor(max_workers=4) as pool:
        return [result for result in pool.map(load, stations) if result]


def parse_rws(payload, selection):
    if not payload:
        return None  # HTTP 204: no measurements, not a zero-degree reading.
    if payload.get("Succesvol") is not True:
        raise ValueError("RWS meldet einen fehlgeschlagenen Abruf")
    candidates = []
    for series in payload.get("WaarnemingenLijst", []):
        meta, location = series.get("AquoMetadata", {}), series.get("Locatie", {})
        if location.get("Code") != selection["Code"] or location.get("Coordinatenstelsel") != "ETRS89":
            continue
        if (meta.get("Compartiment", {}).get("Code") != "OW" or
            meta.get("Grootheid", {}).get("Code") != "T" or
            meta.get("Eenheid", {}).get("Code") != "oC" or meta.get("ProcesType") != "meting"):
            continue
        points = []
        for measurement in series.get("MetingenLijst", []):
            flags = measurement.get("WaarnemingMetadata", {})
            if (str(flags.get("Bemonsteringshoogte")) != selection["height"] or
                flags.get("Referentievlak") != selection["reference"] or
                str(flags.get("Kwaliteitswaardecode")) != "00"):
                continue
            points.append({"t": measurement.get("Tijdstip"),
                           "v": measurement.get("Meetwaarde", {}).get("Waarde_Numeriek")})
        result = row("rws-" + location["Code"], location["Naam"], "Rhein", location["Lat"], location["Lon"],
            "rijkswaterstaat", "https://waterinfo.rws.nl/#/publiek/watertemperatuur", points)
        if result:
            result.update(country="NL", attribution="Rijkswaterstaat",
                license_url="https://www.rijkswaterstaat.nl/zakelijk/open-data",
                data_note="Rhein-Maas-Delta, Haringvliet: oberer Messhorizont (RWS-Code −200/WATSGL). Operative 10-Minuten-Mittel, Qualitätscode 00; noch nicht abschließend geprüft. Keine Mischung verschiedener Messhöhen.")
            candidates.append(result)
    # Do not average independent products. Prefer the freshest continuous series.
    return max(candidates, key=lambda r: (r["items"][0]["time"], len(r["history"]["Wassertemperatur"])), default=None)


def rijkswaterstaat():
    now = datetime.now(timezone.utc)
    results = []
    for selection in RWS_STATIONS:
        body = {"Locatie": {"Code": selection["Code"]}, "AquoPlusWaarnemingMetadata": {
            "AquoMetadata": {"Compartiment": {"Code": "OW"}, "Grootheid": {"Code": "T"}, "ProcesType": "meting"},
            "WaarnemingMetadata": {"BemonsteringshoogteLijst": [selection["height"]]}},
            "Periode": {"Begindatumtijd": (now - timedelta(days=8)).isoformat(timespec="seconds"),
                        "Einddatumtijd": now.isoformat(timespec="seconds")}}
        try:
            result = parse_rws(read_json(RWS, body), selection)
            if result:
                results.append(result)
        except Exception as ex:
            print(f"[Temperatur/RWS] {selection['Code']}: {ex}")
    return results


def collect():
    results = []
    for name, adapter in (("ČHMÚ", chmi), ("Rijkswaterstaat", rijkswaterstaat)):
        try:
            rows = adapter()
            results.extend(rows)
            print(f"[Temperatur/{name}] {len(rows)} Temperaturstationen")
        except Exception as ex:
            print(f"[Temperatur/{name}] Quelle nicht verfügbar, bestehende Messzeiten bleiben erhalten: {ex}")
    return results
