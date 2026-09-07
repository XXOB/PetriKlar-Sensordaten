"""Collect official gauge references in one request; hourly histories for map rivers.

python tools/fetch_water_levels.py --output assets/data/water-levels.json
The identical standalone script runs in PetriKlar-Sensordaten's hourly pipeline.
No invented reference values; unsuccessful requests retain original timestamps.
"""
from __future__ import annotations

import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone, timedelta
import json
import math
from pathlib import Path
from urllib.request import Request, urlopen

API = 'https://www.pegelonline.wsv.de/webservices/rest-api/v2/'
INVENTORY_URL = API + 'stations.json?includeTimeseries=true&includeCharacteristicValues=true&includeCurrentMeasurement=true'
RIVERS = {'RHEIN': 'Rhein', 'ELBE': 'Elbe', 'DONAU': 'Donau', 'MAIN': 'Main',
          'MOSEL': 'Mosel', 'WESER': 'Weser', 'ODER': 'Oder'}


def fetch(url):
    with urlopen(Request(url, headers={'User-Agent': 'PetriKlar-level-map/1.0 (+https://www.petriklar.com)'}), timeout=30) as response:
        return json.load(response)


def number(value):
    if value is None or isinstance(value, bool):
        return None
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except (TypeError, ValueError):
        return None


def timestamp(value):
    try:
        parsed = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
        return parsed.timestamp() if parsed.tzinfo else None
    except ValueError:
        return None


def references(series):
    """Require matching units, ordered finite values and compatible reference periods."""
    refs = {}
    for row in series.get('characteristicValues') or []:
        key = row.get('shortname')
        if key not in ('MNW', 'MW', 'MHW'):
            continue
        refs[key] = {k: row[k] for k in ('value', 'unit', 'timespanStart', 'timespanEnd', 'validFrom') if k in row}
    if len(refs) != 3:
        return refs, 'missing-references'
    if series.get('unit') != 'cm' or any(x.get('unit') != 'cm' for x in refs.values()):
        return refs, 'reference-unit'
    values = [number(refs[k].get('value')) for k in ('MNW', 'MW', 'MHW')]
    if any(v is None for v in values) or not values[0] < values[1] < values[2]:
        return refs, 'reference-order'
    periods = {(v.get('timespanStart'), v.get('timespanEnd'), v.get('validFrom')) for v in refs.values()}
    if len(periods) != 1:
        return refs, 'reference-period'
    return refs, None


def hourly(points, now):
    """Preserve an actual observation per hour, including the last partial hour."""
    hours = {}
    for p in points:
        t, v = p.get('timestamp', p.get('t')), number(p.get('value', p.get('v')))
        ts = timestamp(t)
        if v is None or ts is None or not now - 8 * 86400 <= ts <= now + 900:
            continue
        bucket = int(ts // 3600)
        if bucket not in hours or ts > timestamp(hours[bucket]['t']):
            hours[bucket] = {'t': t, 'v': v}
    return [hours[k] for k in sorted(hours)]


def convert(inventory, previous, now):
    old = {s['id']: s for s in previous.get('stations', [])}
    stations = []
    for s in inventory:
        w = next((t for t in s.get('timeseries', []) if t.get('shortname') == 'W'), None)
        if w is None:
            continue
        uuid = s['uuid']
        refs, reason = references(w)
        current = w.get('currentMeasurement') or {}
        station = {'id': uuid, 'name': s['shortname'], 'river': RIVERS.get(s['water']['shortname'], s['water']['longname'].title()),
                   'lat': s.get('latitude'), 'lon': s.get('longitude'), 'source': 'PEGELONLINE / WSV',
                   'source_url': 'https://www.pegelonline.wsv.de/gast/stammdaten?pegelnr=' + str(s['number']),
                   'unit': w.get('unit'), 'references': refs, 'reference_issue': reason,
                   'gauge_zero': w.get('gaugeZero'), 'mapped_river': s['water']['shortname'] in RIVERS,
                   'current': {'t': current.get('timestamp'), 'v': number(current.get('value'))}}
        station['history'] = hourly(old.get(uuid, {}).get('history', []) + [station['current']], now)
        stations.append(station)
    return stations


def report(stations):
    po = [s for s in stations if s.get('source') == 'PEGELONLINE / WSV']
    by_river = {}
    for river in RIVERS.values():
        group = [s for s in po if s['river'] == river]
        by_river[river] = {'total': len(group), 'usable_references': sum(s['reference_issue'] is None for s in group)}
    return {'pegelonline_total': len(po), 'complete_references': sum(len(s['references']) == 3 for s in po),
            'usable_references': sum(s['reference_issue'] is None for s in po), 'rivers': by_river,
            'issues': dict(Counter(s['reference_issue'] for s in po if s['reference_issue']))}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=Path('pegelkarte.json'))
    parser.add_argument('--inventory', type=Path, help='Use a previously downloaded official response')
    parser.add_argument('--austria', type=Path, help='Optional local wasserwerte.json: gray Austrian gauges')
    parser.add_argument('--no-history', action='store_true')
    args = parser.parse_args()
    now = datetime.now(timezone.utc)
    previous = json.loads(args.output.read_text(encoding='utf8')) if args.output.exists() else {}
    # A failed inventory aborts before writing anything. The pipeline can retain its dated file.
    inventory = json.loads(args.inventory.read_text(encoding='utf8')) if args.inventory else fetch(INVENTORY_URL)
    stations = convert(inventory, previous, now.timestamp())
    if not stations:
        raise RuntimeError('No W series in inventory; preserving previous file')
    failures = []
    if not args.no_history:
        selected = [s for s in stations if s['mapped_river']]
        with ThreadPoolExecutor(max_workers=4) as pool:
            tasks = {pool.submit(fetch, API + 'stations/' + s['id'] + '/W/measurements.json?start=P8D'): s for s in selected}
            for future in as_completed(tasks):
                station = tasks[future]
                try:
                    points = future.result()
                    if not isinstance(points, list) or not points:
                        raise ValueError('Empty history')
                    station['history'] = hourly(station['history'] + points + [station['current']], now.timestamp())
                except Exception as exc:
                    failures.append({'id': station['id'], 'error': str(exc)[:140]})
        print(f'History: {len(selected) - len(failures)}/{len(selected)} retrieved')
    if args.austria and args.austria.exists():
        for s in json.loads(args.austria.read_text(encoding='utf8')).get('stations', []):
            if not str(s.get('src', '')).startswith('at-'):
                continue
            item = next((x for x in s.get('items', []) if x.get('label') == 'Pegelstand'), None)
            if item:
                stations.append({'id': s['id'], 'name': s['name'], 'river': s['river'], 'lat': s.get('lat'), 'lon': s.get('lon'),
                                 'source': s.get('src'), 'source_url': s.get('source_url', ''), 'unit': item.get('unit'),
                                 'current': {'v': number(item.get('value')), 't': item.get('time')},
                                 'references': {}, 'reference_issue': 'missing-references', 'history': []})
    data = {'schema': 1, 'fetched_at': now.isoformat(), 'source_url': INVENTORY_URL,
            'report': report(stations), 'history_errors': failures, 'stations': stations}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    temp = args.output.with_suffix('.tmp')
    temp.write_text(json.dumps(data, ensure_ascii=False, separators=(',', ':')) + '\n', encoding='utf8')
    temp.replace(args.output)
    print(json.dumps(data['report'], ensure_ascii=False))


if __name__ == '__main__':
    main()
