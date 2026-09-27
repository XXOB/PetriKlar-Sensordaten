"""Traegt fehlende Vergangenheit ins 366-Tage-Temperaturarchiv nach.

Das Archiv (wassertemperatur_verlauf.json) waechst sonst nur mit: eine Station,
die neu aufgenommen wird, hat dort keine Vergangenheit. Dieses Skript holt sie
dort, wo die Quelle sie offen anbietet:

  gkd          - Tabellenseite mit Zeitraum (beginn=/ende=), 15-Minuten-Werte
  pegelonline  - Messwerte der letzten 31 Tage (mehr gibt der Dienst nicht her)
  AT/CH/NL     - aus der bereits gesammelten europe-history.json

Alles andere bleibt unangetastet; erfundene Werte gibt es nicht. Aufruf:

    python seed_temperature_archive.py --days 120
"""
from __future__ import annotations

import argparse
import json
import re
import time
from datetime import datetime, timedelta
from pathlib import Path
from urllib.request import Request, urlopen

import fetch_wasserwerte as core

ROOT = Path(__file__).resolve().parent
EUROPE_HISTORY = ROOT / 'europe-history.json'
PEGELONLINE = 'https://www.pegelonline.wsv.de/webservices/rest-api/v2/stations/'
CHUNK_DAYS = 30
ROW = re.compile(r'(\d{2}\.\d{2}\.\d{4})[\s ]+(\d{2}:\d{2})[^<]*</td>\s*<td[^>]*>\s*([-\d.,]+|--)')


def fetch(url, timeout=60):
    request = Request(url, headers={'User-Agent': 'PetriKlar temperature archive (+https://www.petriklar.com)'})
    with urlopen(request, timeout=timeout) as response:
        return response.read()


def gkd_points(station_url, days):
    """Tagesgenaue Zeitraeume der GKD-Tabelle, in Bloecken von 30 Tagen."""
    points, end = [], datetime.now()
    remaining = days
    while remaining > 0:
        span = min(CHUNK_DAYS, remaining)
        start = end - timedelta(days=span)
        url = (station_url.rstrip('/') + '/messwerte/tabelle?beginn='
               + start.strftime('%d.%m.%Y') + '&ende=' + end.strftime('%d.%m.%Y'))
        try:
            html = fetch(url).decode('utf-8', 'replace')
        except Exception as error:
            print('  GKD', url, error)
            break
        found = 0
        for day, clock, value in ROW.findall(html):
            number = core.to_number(value)
            if number is None:
                continue
            points.append({'t': day + ' ' + clock, 'v': number})
            found += 1
        print(f'  GKD {start:%d.%m.}–{end:%d.%m.}: {found} Werte')
        if not found:
            break
        end = start
        remaining -= span
        time.sleep(.5)
    return points


def pegelonline_points(station_id):
    uuid = str(station_id).replace('po-', '').replace('pegelonline-', '')
    if not re.fullmatch(r'[0-9a-f-]{36}', uuid):
        return []
    try:
        raw = json.loads(fetch(PEGELONLINE + uuid + '/WT/measurements.json?start=P31D'))
    except Exception as error:
        print('  PEGELONLINE', uuid, error)
        return []
    return [{'t': row.get('timestamp'), 'v': row.get('value')} for row in raw if isinstance(row, dict)]


def europe_points(station_id, europe):
    row = europe.get(str(station_id))
    return list((row or {}).get('history', {}).get('Wassertemperatur') or [])


def covered_days(values):
    stamps = [core.rolling_history_dt(p.get('t')) for p in values or []]
    stamps = [s for s in stamps if s]
    return (max(stamps) - min(stamps)).days if len(stamps) > 1 else 0


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--days', type=int, default=120, help='So weit zurueck nachtragen')
    parser.add_argument('--only', help='Nur Stationen dieses Gewaessers')
    args = parser.parse_args()

    stations = json.loads(core.JSON_FILE.read_text(encoding='utf-8'))['stations']
    try:
        archive = json.loads(core.TEMP_HISTORY_FILE.read_text(encoding='utf-8'))
    except Exception:
        archive = {'stations': []}
    have = {str(s.get('id')): covered_days(s.get('values')) for s in archive.get('stations', [])}
    try:
        europe = {str(s.get('id')): s for s in json.loads(EUROPE_HISTORY.read_text(encoding='utf-8')).get('stations', [])}
    except Exception:
        europe = {}

    filled = 0
    for station in stations:
        river = core.temperature_archive_river(station.get('river'))
        if not river:
            continue
        if args.only and core.normalized_header(args.only) != core.normalized_header(station.get('river')):
            continue
        sid = str(station.get('id'))
        if have.get(sid, 0) >= min(args.days, 300):
            continue
        source = str(station.get('src') or '')
        print(f'{river} · {station.get("name")} ({source or "?"}) – Archiv {have.get(sid, 0)} Tage')
        if source == 'gkd' and str(sid).startswith('http'):
            points = gkd_points(sid, args.days)
        elif source == 'pegelonline' or sid.startswith(('po-', 'pegelonline-')):
            points = pegelonline_points(sid)
        else:
            points = europe_points(sid, europe)
            if points:
                print(f'  europe-history: {len(points)} Werte')
        if not points:
            continue
        series = station.setdefault('history', {}).setdefault('Wassertemperatur', [])
        series.extend(points)
        filled += 1

    core.update_temperature_archive(stations)
    print(f'Nachgetragen: {filled} Stationen')


if __name__ == '__main__':
    main()
