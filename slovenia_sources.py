"""Current official ARSO hydrology observations for the Slovenian Mura."""
from __future__ import annotations

from datetime import datetime
from html.parser import HTMLParser
import math
import re
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo


ARSO_URL = "https://www.arso.gov.si/en/water/data/stanje_voda.html"
ARSO_ATTRIBUTION = "Agencija Republike Slovenije za okolje (ARSO)"
STATIONS = {
    "Gornja Radgona I": {"id": "1060", "lat": 46.6815096, "lon": 16.00025349},
    "Petanjci": {"id": "1070", "lat": 46.6488209, "lon": 16.05924412},
}
MONTHS = {
    "January": 1, "February": 2, "March": 3, "April": 4,
    "May": 5, "June": 6, "July": 7, "August": 8,
    "September": 9, "October": 10, "November": 11, "December": 12,
}


class _CurrentTableParser(HTMLParser):
    """Select the automatic-gauge table, not ARSO's separate 08:00 summary."""
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.heading_text = []
        self.await_table = False
        self.in_heading = False
        self.in_table = False
        self.in_cell = False
        self.in_datum = False
        self.after_table = False
        self.row = None
        self.cell = None
        self.rows = []
        self.datum = ""
        self.last_datum = ""
        self.datum_text = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "h2":
            self.in_heading = True
            self.heading_text = []
        elif tag == "table" and self.await_table:
            self.in_table = True
            self.await_table = False
        elif self.in_table and tag == "tr":
            self.row = []
        elif self.in_table and tag in ("th", "td"):
            self.in_cell = True
            self.cell = []
        elif tag == "p" and "datum" in attrs.get("class", "").split():
            self.in_datum = True
            self.datum_text = []

    def handle_data(self, data):
        if self.in_heading:
            self.heading_text.append(data)
        if self.in_cell and self.cell is not None:
            self.cell.append(data)
        if self.in_datum:
            self.datum_text.append(data)

    def handle_endtag(self, tag):
        if tag == "h2" and self.in_heading:
            heading = " ".join(" ".join(self.heading_text).split())
            self.in_heading = False
            if heading.casefold() == "automatic gauging stations":
                self.datum = self.last_datum
                self.await_table = True
        elif self.in_table and tag in ("th", "td") and self.in_cell:
            self.row.append(" ".join(" ".join(self.cell or []).split()))
            self.in_cell = False
            self.cell = None
        elif self.in_table and tag == "tr":
            if self.row:
                self.rows.append(self.row)
            self.row = None
        elif tag == "table" and self.in_table:
            self.in_table = False
            self.after_table = True
        elif tag == "p" and self.in_datum:
            self.last_datum = " ".join(" ".join(self.datum_text).split())
            self.in_datum = False


def _timestamp(text):
    match = re.search(
        r"\b(\d{1,2})\s+([A-Za-z]+)\s+(\d{4}),\s+at\s+(\d{1,2}):(\d{2})\b",
        text or "",
    )
    if not match or match.group(2) not in MONTHS:
        return None
    day, month, year, hour, minute = map(int, (
        match.group(1), MONTHS[match.group(2)], match.group(3), match.group(4), match.group(5)))
    try:
        return datetime(year, month, day, hour, minute,
                        tzinfo=ZoneInfo("Europe/Ljubljana")).isoformat(timespec="minutes")
    except ValueError:
        return None


def _number(value):
    try:
        number = float(str(value or "").replace(",", ".").strip())
        return number if math.isfinite(number) else None
    except (TypeError, ValueError):
        return None


def parse_current(payload):
    """Return the latest ARSO automatic observations at the two Mura gauges."""
    parser = _CurrentTableParser()
    parser.feed(payload.decode("utf-8", "replace") if isinstance(payload, bytes) else str(payload))
    measured_at = _timestamp(parser.datum)
    if not measured_at:
        raise ValueError("ARSO automatic-gauge table has no parseable publication time")
    by_name = {}
    for cells in parser.rows:
        if len(cells) < 6 or cells[0].casefold() not in ("mura", "mur"):
            continue
        station = STATIONS.get(cells[1])
        if station is None:
            continue
        level, discharge, temperature = (_number(cells[i]) for i in (2, 3, 5))
        items = []
        if level is not None:
            items.append({"label": "Pegelstand", "value": level, "unit": "cm", "time": measured_at})
        if discharge is not None:
            items.append({"label": "Durchfluss", "value": discharge, "unit": "m\u00b3/s", "time": measured_at})
        if temperature is not None and -2 <= temperature <= 40:
            items.append({"label": "Wassertemperatur", "value": temperature, "unit": "\u00b0C", "time": measured_at})
        if not items:
            continue
        name = cells[1]
        by_name[name] = {
            "id": "arso-si-" + station["id"], "name": name, "lat": station["lat"],
            "lon": station["lon"], "river": "Mur", "src": "arso-si", "country": "SI",
            "updated": measured_at, "source_url": ARSO_URL, "attribution": ARSO_ATTRIBUTION,
            "license": "ARSO-Nutzungsbedingungen: Quellenangabe erforderlich",
            "license_url": "https://vode.arso.gov.si/hidarhiv/",
            "data_note": "Vorlaeufige aktuelle ARSO-Messung an der Mura in Slowenien; Zeitstempel der automatischen Pegeltabelle.",
            "params": {"pegel": level is not None, "wt": temperature is not None,
                       "o2": False, "tr": False},
            "items": items,
            "history": {
                label: [{"t": measured_at, "v": value}]
                for label, value in (("Wassertemperatur", temperature), ("Pegelstand", level),
                                     ("Durchfluss", discharge))
                if value is not None
            },
        }
    return list(by_name.values())


def process_mura_current():
    request = Request(ARSO_URL, headers={
        "User-Agent": "PetriKlar/1.0 (+https://www.petriklar.com)",
        "Accept": "text/html",
    })
    with urlopen(request, timeout=60) as response:
        payload = response.read(1_000_001)
    if len(payload) > 1_000_000:
        raise ValueError("ARSO response exceeds 1 MB")
    stations = parse_current(payload)
    print(f"[Slowenien/ARSO] {len(stations)} aktuelle Mura-Stationen")
    return stations
