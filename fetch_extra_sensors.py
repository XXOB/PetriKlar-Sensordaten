"""Zusaetzliche amtliche Messstellen fuer die Kartenpakete.

Diese Datei liegt identisch in PetriKlar-Source (tools/) und im Repository
PetriKlar-Sensordaten (Wurzel). Die Live-Karte liest die Pakete aus
PetriKlar-Sensordaten - jede Quelle, die nur in PetriKlar-Source lief, fehlte
dort (so fiel schon die Havel aus).

Quellen (alle frei abrufbar, ohne Schluessel; Quelle wird stets genannt):
  - Mur, Slowenien: ARSO, automatische Pegel (Pegel, Temperatur)
  - Donau, Kroatien: DHMZ, automatische Pegel (stuendlich; nur Pegel - die
    Wassertemperatur-Messungen dort sind derzeit unplausibel)
  - Donau, Serbien: RHMZ Srbije, Atom-Feed (taeglich 08:00; Pegel, Temperatur)
  - Donau, Rumaenien: AFDJ Galati, oeffentliche XML-Schnittstelle (taeglich;
    Pegel, Temperatur)
  - Donau, Bulgarien: APPD Ruse, Tabelle der Hydrometeorologischen Stationen
    (taeglich; Pegel, Temperatur)
  - Donau, Ungarn: OVF Open Data (Pegel und gemessene Wassertemperatur).
Slowakei (SHMU) bleibt ohne bestaetigte Weiterverwendung ausserhalb des Imports.

Faellt eine Quelle aus, bleiben ihre zuletzt gespeicherten Werte mit ihrem
echten Zeitstempel erhalten; die Karten blenden zu alte Werte selbst aus.
Mittelwasser (MW) veroeffentlicht keine dieser Quellen - die Pegelkarte zeigt
die Stationen deshalb grau, bis das eigene Pegelarchiv genug Werte hat.
"""
from __future__ import annotations

from datetime import datetime, timezone, timedelta
from html.parser import HTMLParser
import json
from pathlib import Path
import re
import sys
import unicodedata
from urllib.request import Request, urlopen
import xml.etree.ElementTree as ET
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent
if ROOT.name == "tools":
    ROOT = ROOT.parent
sys.path.insert(0, str(ROOT))
OUTPUT = ROOT / "assets/data/extra-sensors.json"
UA = "PetriKlar/1.0 (+https://www.petriklar.com)"

AFDJ_URL = "https://afdj.ro/ro/tabel_cotele_dunarii/xml"
RHMZ_URL = "https://www.hidmet.gov.rs/latin/osmotreni/stanje_voda.xml"
APPD_URL = "https://appd-bg.org/hidrology"
DHMZ_URL = "https://hidro.dhz.hr/hidroweb/skripte/hidrobazahtml.py?funkc=podaciautomatskih"

# Pegelorte am Fluss; Koordinaten der Orte am Donauufer (gerundet).
RHMZ_STATIONS = {
    "BEZDAN": (45.853, 18.861), "APATIN": (45.668, 18.977), "BOGOJEVO": (45.530, 19.131),
    "BAČKA PALANKA": (45.249, 19.392), "NOVI SAD": (45.255, 19.847), "SLANKAMEN": (45.134, 20.255),
    "ZEMUN": (44.846, 20.411), "PANČEVO": (44.840, 20.630), "SMEDEREVO": (44.668, 20.933),
    "BANATSKA PALANKA": (44.852, 21.338), "VELIKO GRADIŠTE": (44.762, 21.522),
    "GOLUBAC": (44.660, 21.632), "PRAHOVO": (44.300, 22.598),
}
APPD_STATIONS = {
    "Ново село": ("Novo Selo", 44.165, 22.785), "Видин": ("Vidin", 43.996, 22.882),
    "Лом": ("Lom", 43.828, 23.237), "Оряхово": ("Oryahovo", 43.735, 23.960),
    "Никопол": ("Nikopol", 43.706, 24.895), "Свищов": ("Svishtov", 43.623, 25.347),
    "Русе": ("Ruse", 43.856, 25.954), "Силистра": ("Silistra", 44.118, 27.261),
}
DHMZ_STATIONS = {
    "BATINA": (45.848, 18.852), "ALJMAŠ": (45.528, 18.957), "DALJ": (45.484, 19.000),
    "VUKOVAR": (45.352, 19.003), "ILOK": (45.224, 19.380),
}


def fetch(url: str, timeout: int = 45) -> bytes:
    with urlopen(Request(url, headers={"User-Agent": UA, "Accept": "*/*"}), timeout=timeout) as r:
        data = r.read(5_000_001)
    if len(data) > 5_000_000:
        raise ValueError(f"Antwort zu gross: {url}")
    return data


def slug(text: str) -> str:
    ascii_text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", ascii_text.lower()).strip("-")


def number(value):
    try:
        text = str(value).replace(",", ".").strip()
        if text in ("", "-", "*", "//"):
            return None
        return float(text)
    except (TypeError, ValueError):
        return None


def station(sid, name, lat, lon, src, country, url, attribution, license_text, when, level=None, temperature=None, note=""):
    items, history = [], {}
    if level is not None:
        items.append({"label": "Pegelstand", "value": level, "unit": "cm", "time": when})
        history["Pegelstand"] = [{"t": when, "v": level}]
    if temperature is not None and -2 <= temperature <= 35:
        items.append({"label": "Wassertemperatur", "value": temperature, "unit": "°C", "time": when})
        history["Wassertemperatur"] = [{"t": when, "v": temperature}]
    return {"id": sid, "name": name, "river": "Donau", "lat": lat, "lon": lon, "src": src,
            "country": country, "source_url": url, "attribution": attribution, "license": license_text,
            "data_note": note, "updated": when, "unit": "cm",
            "params": {"pegel": level is not None, "wt": "Wassertemperatur" in history, "o2": False, "tr": False},
            "items": items, "history": history}


def parse_afdj(payload: bytes):
    root = ET.fromstring(payload)
    rows = []
    for item in root.findall("item"):
        val = lambda name: (item.findtext(f"{name}/value") or "").strip()
        title = val("title")
        lat, lon = number(item.findtext("field_geolocation_demo_single/lat")), number(item.findtext("field_geolocation_demo_single/lng"))
        when = val("field_field_data_actualiz_cote")
        if not title or lat is None or lon is None or not when:
            continue
        when = datetime.fromisoformat(when).isoformat(timespec="minutes")
        rows.append(station("ro-afdj-" + slug(title), title, lat, lon, "ro-afdj", "RO", AFDJ_URL,
                            "AFDJ R.A. Galați", "Öffentliche Schnittstelle (Quelle: AFDJ)", when,
                            number(val("field_cota")), number(val("field_temperatura_masurata")),
                            f"Donau-km {val('field_km')}; Tageswert der AFDJ."))
    return rows


def parse_rhmz(payload: bytes):
    text = payload.decode("utf-8", "replace")
    head = re.search(r"(\d{2})\.(\d{2})\.(\d{4})\.\s*vreme:\s*\d{1,2}:\d{2}\s*\((\d{2}):(\d{2})\s*UTC\)", text)
    if not head:
        raise ValueError("RHMZ: kein Messzeitpunkt im Feed")
    day, month, year, hour, minute = map(int, head.groups())
    when = datetime(year, month, day, hour, minute, tzinfo=timezone.utc).isoformat(timespec="minutes")
    rows = []
    for entry in re.findall(r"<entry>(.*?)</entry>", text, flags=re.S):
        title = re.search(r"<title>\s*Reka:\s*([^<]+?)\s*-\s*Hidrolo\S*\s+stanica:\s*([^<]+?)\s*</title>", entry)
        if not title or title.group(1).strip().upper() != "DUNAV":
            continue
        name = title.group(2).strip().upper()
        coords = RHMZ_STATIONS.get(name)
        if not coords:
            continue
        sid = re.search(r"hm_id=(\d+)", entry)
        summary = re.search(r"<summary>(.*?)</summary>", entry, flags=re.S)
        summary = summary.group(1) if summary else ""
        level = re.search(r"Vodostaj:\s*(-?[\d.,]+|\*|-)\s*cm", summary)
        temp = re.search(r"Temperatura vode:\s*(-?[\d.,]+|\*|-)", summary)
        url = re.search(r"href='([^']+)'", entry)
        rows.append(station("rs-rhmz-" + (sid.group(1) if sid else slug(name)), name.title(), *coords, "rs-rhmz", "RS",
                            url.group(1) if url else RHMZ_URL, "RHMZ Srbije", "© RHMZ Srbije – Quelle wird genannt", when,
                            number(level.group(1)) if level else None, number(temp.group(1)) if temp else None,
                            "Tagesmeldung des RHMZ (08:00 Ortszeit)."))
    return rows


class _Tables(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.tables, self.row, self.cell, self.text = [], None, None, []

    def handle_starttag(self, tag, attrs):
        if tag == "table":
            self.tables.append([])
        elif tag == "tr" and self.tables:
            self.row = []
        elif tag in ("td", "th") and self.row is not None:
            self.cell = []

    def handle_data(self, data):
        self.text.append(data)
        if self.cell is not None:
            self.cell.append(data)

    def handle_endtag(self, tag):
        if tag in ("td", "th") and self.cell is not None and self.row is not None:
            self.row.append(" ".join(" ".join(self.cell).split()))
            self.cell = None
        elif tag == "tr" and self.row is not None and self.tables:
            if self.row:
                self.tables[-1].append(self.row)
            self.row = None


def parse_appd(payload: bytes):
    text = payload.decode("utf-8", "replace")
    date = re.search(r"Дунав\s+за\s+(\d{2})\.(\d{2})\.(\d{4})", text)
    if not date:
        raise ValueError("APPD: kein Datum in der Tabelle")
    day, month, year = map(int, date.groups())
    # Morgentermin der hydrometeorologischen Stationen (07:00 Ortszeit)
    when = datetime(year, month, day, 7, 0, tzinfo=ZoneInfo("Europe/Sofia")).isoformat(timespec="minutes")
    parser = _Tables()
    parser.feed(text)
    table = next((t for t in parser.tables if t and any("t вода" in c for c in t[0])), None)
    if not table:
        raise ValueError("APPD: Tabelle nicht gefunden")
    rows = []
    for cells in table[1:]:
        if len(cells) < 6 or cells[0] not in APPD_STATIONS:
            continue
        name, lat, lon = APPD_STATIONS[cells[0]]
        rows.append(station("bg-appd-" + slug(name), name, lat, lon, "bg-appd", "BG", APPD_URL,
                            "Изпълнителна агенция „Проучване и поддържане на река Дунав“ (APPD)",
                            "Offene Daten gem. RL 2019/1024 – Quellenangabe", when,
                            number(cells[2]), number(cells[5]), f"Donau-km {cells[1]}; Tageswert (Morgentermin)."))
    return rows


def parse_dhmz(payload: bytes):
    parser = _Tables()
    parser.feed(payload.decode("utf-8", "replace"))
    rows = []
    for table in parser.tables:
        for cells in table:
            if len(cells) < 6 or cells[2].upper() != "DUNAV" or cells[0].upper() not in DHMZ_STATIONS:
                continue
            stamp = re.match(r"(\d{1,2})\.\s*(\d{1,2})\.\s*(\d{4})\.\s*(\d{1,2}):(\d{2})", cells[4])
            if not stamp:
                continue
            d, m, y, hh, mm = map(int, stamp.groups())
            when = datetime(y, m, d, hh, mm, tzinfo=timezone(timedelta(hours=1))).isoformat(timespec="minutes")  # SEV = UTC+1
            name = cells[0].title()
            rows.append(station("hr-dhmz-" + cells[1], name, *DHMZ_STATIONS[cells[0].upper()], "hr-dhmz", "HR",
                                DHMZ_URL, "DHMZ – Državni hidrometeorološki zavod", "Otvorena dozvola (Izvor: DHMZ)",
                                when, number(cells[5]), None, "Automatischer Pegel (stündlich)."))
    return rows


def mura():
    from slovenia_sources import process_mura_current
    return process_mura_current()


def hungary():
    from hungary_sources import collect
    return collect()


SOURCES = [
    ("hu-ovf", "https://data.vizugy.hu/", hungary),
    ("arso-si", "https://www.arso.gov.si/en/water/data/stanje_voda.html", mura),
    ("hr-dhmz", DHMZ_URL, lambda: parse_dhmz(fetch(DHMZ_URL))),
    ("rs-rhmz", RHMZ_URL, lambda: parse_rhmz(fetch(RHMZ_URL))),
    ("ro-afdj", AFDJ_URL, lambda: parse_afdj(fetch(AFDJ_URL))),
    ("bg-appd", APPD_URL, lambda: parse_appd(fetch(APPD_URL))),
]


def merge_history(old: dict, new: dict, keep_days: int = 8):
    """Verlauf ueber die Laeufe fortschreiben (taegliche Quellen haben sonst nur einen Punkt)."""
    limit = datetime.now(timezone.utc) - timedelta(days=keep_days)
    history = {}
    if old.get('temperature_parameter') and new.get('temperature_parameter') and old['temperature_parameter'] != new['temperature_parameter']:
        old = {**old, 'history': {k:v for k,v in old.get('history',{}).items() if k!='Wassertemperatur'}}
    for label in set(old.get("history", {})) | set(new.get("history", {})):
        points = {p["t"]: p for p in (old.get("history", {}).get(label) or []) + (new.get("history", {}).get(label) or [])
                  if isinstance(p, dict) and p.get("t")}
        history[label] = [p for t, p in sorted(points.items(), key=lambda kv: datetime.fromisoformat(kv[0]))
                          if datetime.fromisoformat(t) >= limit]
    return {**new, "history": history}


def main():
    old = json.loads(OUTPUT.read_text(encoding="utf-8")) if OUTPUT.exists() else {}
    previous = {s["id"]: s for s in old.get("stations", [])}
    stations, report, errors = [], {}, {}
    for src, url, collect in SOURCES:
        try:
            rows = collect()
            if not rows:
                raise ValueError("keine Stationen gelesen")
            stations += [merge_history(previous.get(s["id"], {}), s) for s in rows]
            report[src] = {"count": len(rows), "url": url}
            print(f"[{src}] {len(rows)} Stationen")
        except Exception as error:  # Quelle faellt aus: letzten Stand behalten
            errors[src] = str(error)[:300]
            kept = [s for s in previous.values() if s.get("src") == src]
            stations += kept
            print(f"WARNING {src}: {error} - {len(kept)} gespeicherte Stationen behalten")
    if not report:
        raise SystemExit("Keine Zusatzquelle erreichbar; vorheriger Stand bleibt.")
    payload = {"updated": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
               "note": "Amtliche Messwerte; Mur (ARSO) und Donau (OVF, DHMZ, RHMZ, AFDJ, APPD). Keine abgeleiteten Werte.",
               "stations": stations,
               "temperature_stations": [s for s in stations if any(i["label"] == "Wassertemperatur" for i in s["items"])],
               "level_stations": [s for s in stations if any(i["label"] == "Pegelstand" for i in s["items"])],
               "source_report": report, "errors": errors}
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    tmp = OUTPUT.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8")
    tmp.replace(OUTPUT)


if __name__ == "__main__":
    main()
