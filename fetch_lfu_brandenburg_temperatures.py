"""Refresh the current Havel/Spree temperatures from Brandenburg's live charts.

The LfU's downloadable hourly CSV currently stops weeks before the live chart.
The live 31-day PNG chart includes its latest timestamp and value in the title;
Tesseract reads that title, and only the explicitly printed last observation is
saved. No values are inferred from the plotted line.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from io import BytesIO
import json
from pathlib import Path
import re
import subprocess
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

# Dieselbe Datei liegt in PetriKlar-Source (tools/) und im Repository
# PetriKlar-Sensordaten (Wurzel). Die Live-Karte liest die Sensordaten; lief
# der Abruf nur in PetriKlar-Source, fehlte die Havel dort.
ROOT = Path(__file__).resolve().parent
if ROOT.name == "tools":
    ROOT = ROOT.parent
OUTPUT = ROOT / "assets/data/lfu-brandenburg-current.json"
BERLIN = ZoneInfo("Europe/Berlin")
SITES = [
    {
        "id": "bb-potsdam", "name": "Potsdam", "river": "Havel",
        "chart": "https://mleuv-daten.brandenburg.de/w/WGMN/Potsdam/wtemp_31t_pots_kl.png",
        "page": "https://lfu.brandenburg.de/lfu/de/aufgaben/wasser/fliessgewaesser-und-seen/gewaesserueberwachung/wasserguetemessnetz/potsdam/",
    },
    {
        "id": "bb-beeskow", "name": "Beeskow", "river": "Spree",
        "chart": "https://mleuv-daten.brandenburg.de/w/WGMN/Beeskow/wtemp_31t_beeskow_kl.png",
        "page": "https://lfu.brandenburg.de/lfu/de/aufgaben/wasser/fliessgewaesser-und-seen/gewaesserueberwachung/wasserguetemessnetz/beeskow/",
    },
    {
        "id": "bb-leibsch", "name": "Leibsch", "river": "Spree",
        "chart": "https://mleuv-daten.brandenburg.de/w/WGMN/Leibsch/wtemp_31t_leibsch_kl.png",
        "page": "https://lfu.brandenburg.de/lfu/de/aufgaben/wasser/fliessgewaesser-und-seen/gewaesserueberwachung/wasserguetemessnetz/leibsch/",
    },
    {
        "id": "bb-neuhausen", "name": "Neuhausen", "river": "Spree",
        "chart": "https://mleuv-daten.brandenburg.de/w/WGMN/Neuhausen/wtemp_31t_neuhausen_kl.png",
        "page": "https://lfu.brandenburg.de/lfu/de/aufgaben/wasser/fliessgewaesser-und-seen/gewaesserueberwachung/wasserguetemessnetz/neuhausen/",
    },
    {
        "id": "bb-spremberg", "name": "Spremberg", "river": "Spree",
        "chart": "https://mleuv-daten.brandenburg.de/w/WGMN/Spremberg/wtemp_31t_spremberg_kl.png",
        "page": "https://lfu.brandenburg.de/lfu/de/aufgaben/wasser/fliessgewaesser-und-seen/gewaesserueberwachung/wasserguetemessnetz/spremberg/",
    },
]
TITLE = re.compile(
    r"roh?daten\s+bis\s+(\d{1,2}[./]\d{1,2}[./]\d{4})\s+(\d{1,2}:\d{2})\s*"
    # OCR often reads the initial W in "Wert" as Y and inserts a V ("Yvert").
    r"uhr\s*\(?\s*letzter\s*(?:wert|yvert)\s*[:;]?\s*(-?\d{1,2}(?:[,.]\s*\d{1,2})?)",
    re.IGNORECASE | re.DOTALL,
)


def parse_chart_title(text: str, now: datetime | None = None) -> tuple[datetime, float]:
    """Extract and validate the LfU chart's printed latest observation."""
    match = TITLE.search(" ".join(str(text).replace("\u00a0", " ").split()))
    if not match:
        raise ValueError(f"Could not read latest value in chart title: {text!r}")
    day = datetime.strptime(match.group(1).replace("/", "."), "%d.%m.%Y").date()
    hour, minute = map(int, match.group(2).split(":"))
    observed = datetime(day.year, day.month, day.day, hour, minute, tzinfo=BERLIN)
    value = float(match.group(3).replace(" ", "").replace(",", "."))
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    age = now.astimezone(timezone.utc) - observed.astimezone(timezone.utc)
    if age < -timedelta(minutes=20) or age > timedelta(hours=36):
        raise ValueError(f"Chart observation is not current ({observed.isoformat()})")
    if not -1 <= value <= 40:
        raise ValueError(f"Temperature is outside the expected range: {value}")
    return observed, value


def read_latest(chart_url: str, now: datetime | None = None) -> tuple[datetime, float]:
    from PIL import Image, ImageOps

    request = Request(chart_url, headers={"User-Agent": "PetriKlar water data updater/1.0"})
    with urlopen(request, timeout=25) as response:
        image_bytes = response.read()
    with Image.open(BytesIO(image_bytes)) as chart:
        chart = chart.convert("L")
        # The title sits above the plot; enlarge and clean it for small-font OCR.
        title = chart.crop((0, 0, chart.width, min(chart.height, max(30, chart.height // 7))))
        title = ImageOps.autocontrast(title).resize((title.width * 4, title.height * 4))
        buf = BytesIO()
        title.save(buf, format="PNG")
    result = subprocess.run(
        ["tesseract", "stdin", "stdout", "--psm", "6", "-l", "deu+eng", "-c", "preserve_interword_spaces=1"],
        input=buf.getvalue(), stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True,
    )
    return parse_chart_title(result.stdout.decode("utf-8", errors="replace"), now)


def station_metadata(site: dict, catalog: dict, old: dict) -> dict:
    previous = next((s for s in old.get("stations", []) if s.get("id") == site["id"]), {})
    catalog_row = next((s for s in catalog.get("stations", []) if s.get("id") == site["id"]), {})
    if not previous and not catalog_row:
        raise ValueError(f"No known station metadata for {site['id']}")
    row = {
        "id": site["id"], "name": site["name"], "river": site["river"],
        "lat": previous.get("lat", catalog_row.get("lat")),
        "lon": previous.get("lon", catalog_row.get("lon")),
        "src": "brandenburg", "country": "DE",
        "attribution": "Landesamt für Umwelt Brandenburg",
        "source_url": site["page"], "data_source_url": site["chart"],
        "data_note": "Letzter im offiziellen LfU-31-Tage-Diagramm ausgewiesener Rohwert (ungeprüft); keine Werte aus dem Kurvenbild abgeleitet.",
    }
    if row["lat"] is None or row["lon"] is None:
        raise ValueError(f"Missing coordinates for {site['id']}")
    return row


def main() -> None:
    catalog_path = ROOT / "assets/data/station-catalog.json"
    # Im Sensordaten-Repository gibt es keinen Stationskatalog; die Koordinaten
    # stammen dann aus dem zuletzt gespeicherten Stand.
    catalog = json.loads(catalog_path.read_text(encoding="utf-8")) if catalog_path.exists() else {"stations": []}
    old = json.loads(OUTPUT.read_text(encoding="utf-8")) if OUTPUT.exists() else {"stations": []}
    previous = {s.get("id"): s for s in old.get("stations", [])}
    output_rows = []
    successes = 0
    now = datetime.now(timezone.utc)
    for site in SITES:
        try:
            observed, value = read_latest(site["chart"], now)
            row = station_metadata(site, catalog, old)
            stamp = observed.isoformat(timespec="minutes")
            reading = {"label": "Wassertemperatur", "value": value, "unit": "°C", "time": stamp}
            row["items"] = [reading]
            row["history"] = {"Wassertemperatur": [{"t": stamp, "v": value}]}
            output_rows.append(row)
            successes += 1
            print(f"{site['river']} · {site['name']}: {value:.2f} °C ({stamp})")
        except Exception as error:
            old_row = previous.get(site["id"])
            if old_row:
                output_rows.append(old_row)
            print(f"WARNING {site['name']}: {error}")
    if not successes:
        raise RuntimeError("No current Havel/Spree chart values could be read; previous snapshot preserved.")
    payload = {
        "updated": now.replace(microsecond=0).isoformat(),
        "source": "Landesamt für Umwelt Brandenburg, current 31-day station charts (raw/unvalidated)",
        "stations": output_rows,
    }
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    temporary = OUTPUT.with_suffix(".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8")
    temporary.replace(OUTPUT)


if __name__ == "__main__":
    main()
