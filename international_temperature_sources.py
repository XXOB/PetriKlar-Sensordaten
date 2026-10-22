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
# The only configured RWS series we have verified on the IJssel itself is
# Zwolle; its last usable observation is historical, so row() rejects it. The
# live Amerongen sensor is on the Nederrijn, a Rhine distributary with its own
# mapped route. Do not mislabel a nearby canal sensor as an IJssel reading.
RWS_STATIONS = (
    # Lobith is the upstream Dutch RWS station on the Bovenrijn/Rhine route.
    {"Code": "lobith.ponton", "river": "Rhein", "height": "-100", "reference": "WATSGL"},
    # These sites are in the downstream Rhine-Maas delta, not on the IJssel.
    {"Code": "middelharnis.meetboei", "river": "Rhein-Maas-Delta", "height": "-200", "reference": "WATSGL"},
    {"Code": "haringvliet.2", "river": "Rhein-Maas-Delta", "height": "-200", "reference": "WATSGL"},
    {"Code": "stellendam.binnen", "river": "Rhein-Maas-Delta", "height": "-200", "reference": "WATSGL"},
    # This Bovenrijn product is distinct from the ponton sensor and uses its
    # own documented sampling horizon/reference plane.
    {"Code": "lobith.bovenrijn.haven", "river": "Rhein", "height": "0", "reference": "NVT"},
    {"Code": "zwolle.ijssel", "river": "IJssel", "height": "-100", "reference": "WATSGL"},
    # Kampen is an official IJssel temperature site, but currently only returns
    # sparse observations. Keep polling so the first new valid reading is used.
    {"Code": "kampen", "river": "IJssel", "height": "-100", "reference": "WATSGL"},
    # Current Dutch Rhine-branch temperature is available at Amerongen, not
    # at the stale Lobith ponton series. Keep its physical river name/route.
    {"Code": "amerongen.boven", "river": "Nederrijn", "height": "0", "reference": "NVT"},
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
        result = row("rws-" + location["Code"], location["Naam"], selection.get("river", "Rhein"), location["Lat"], location["Lon"],
            "rijkswaterstaat", "https://waterinfo.rws.nl/#/publiek/watertemperatuur", points)
        if result:
            result.update(country="NL", attribution="Rijkswaterstaat",
                license="CC0-1.0", license_url="https://www.rijkswaterstaat.nl/zakelijk/open-data",
                source_station=location["Code"], sampling_height=selection["height"],
                sampling_reference=selection["reference"],
                data_note=(
                    "Bovenrijn/Rhein bei Lobith; Messhorizont −100/WATSGL. "
                    "Operativer Rijkswaterstaat-Wert, Qualitätscode 00; noch nicht abschließend geprüft."
                    if location["Code"] == "lobith.ponton" else
                    "Bovenrijn/Rhein bei Lobith, Hafenmessstelle; Messhorizont 0/NVT. "
                    "Operativer Rijkswaterstaat-Wert, Qualitätscode 00; noch nicht abschließend geprüft."
                    if location["Code"] == "lobith.bovenrijn.haven" else
                    "IJssel bei Zwolle; Messhorizont −100/WATSGL. "
                    "Operativer Rijkswaterstaat-Wert, Qualitätscode 00; noch nicht abschließend geprüft."
                    if location["Code"] == "zwolle.ijssel" else
                    "IJssel bei Kampen; Messhorizont −100/WATSGL. "
                    "Operativer Rijkswaterstaat-Wert, Qualitätscode 00; noch nicht abschließend geprüft."
                    if location["Code"] == "kampen" else
                    "Nederrijn bei Amerongen; Messhorizont 0/NVT. "
                    "Operativer Rijkswaterstaat-Wert, Qualitätscode 00; noch nicht abschließend geprüft."
                    if location["Code"] == "amerongen.boven" else
                    "Rijkswaterstaat-Messwert im Rhein-Maas-Delta; Messhorizont −200/WATSGL. "
                    "Operative Messung, Qualitätscode 00; noch nicht abschließend geprüft. "
                    "Keine Mischung verschiedener Messhöhen."))
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
            else:
                print(f"[Temperatur/RWS] {selection['Code']}: kein brauchbarer Messwert innerhalb der letzten 8 Tage")
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
