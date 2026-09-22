"""Store current official NIZ Neckar temperatures for the public river map.

The NIZ endpoint publishes the latest observation, not a seven-day series. Keep
previously collected observations with their original times; never invent values.
"""
from __future__ import annotations

import argparse
import json
import math
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.request import Request, urlopen

URL = 'https://inovum-services.de/gmb/md/v1/gewaesser;1.0.0?page[limit]=1000'
SOURCE = 'https://niz.baden-wuerttemberg.de/oberflaechengewaesser/gueteparameter'


def convert(payload, previous, now):
    old = {s['id']: s for s in previous.get('stations', [])}
    stations = []
    for row in payload.get('data', []):
        attrs = row.get('attributes') or {}
        if attrs.get('gewaesser') != 'Neckar':
            continue
        position = attrs.get('geometry') or {}
        measurement = (attrs.get('messreihen') or {}).get('temp') or {}
        values = measurement.get('values') or {}
        ident = 'niz-' + str(attrs.get('id') or row.get('id') or '')
        try:
            lat, lon = float(position['lat']), float(position['lon'])
            value = float(str(values['latest']).replace(',', '.'))
            measured = datetime.fromtimestamp(int(values['latest-ts']) / 1000, timezone.utc)
        except (KeyError, TypeError, ValueError, OverflowError):
            continue
        if not (-2 <= value <= 40 and math.isfinite(lat) and math.isfinite(lon)
                and measured <= now + timedelta(minutes=15)):
            continue
        previous_station = old.get(ident, {})
        history = {point['t']: point for point in previous_station.get('history', {}).get('Wassertemperatur', [])
                   if isinstance(point, dict) and point.get('t')}
        if measurement.get('status') == 'operational':
            history[measured.isoformat()] = {'t': measured.isoformat(), 'v': value}
            current = {'label': 'Wassertemperatur', 'value': value, 'unit': '°C', 'time': measured.isoformat()}
        else:
            current = next(iter(previous_station.get('items', [])), None)
        if not current:
            continue
        points = sorted((p for p in history.values() if datetime.fromisoformat(p['t']) >= now - timedelta(days=9)),
                        key=lambda p: p['t'])
        stations.append({'id': ident, 'name': attrs.get('name') or 'Neckar', 'river': 'Neckar',
                         'lat': lat, 'lon': lon, 'src': 'niz', 'source_url': SOURCE,
                         'items': [current], 'history': {'Wassertemperatur': points}})
    return {'updated': now.isoformat(), 'source_url': URL, 'stations': stations}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=Path('niz_temperature.json'))
    args = parser.parse_args()
    previous = json.loads(args.output.read_text(encoding='utf8')) if args.output.exists() else {}
    with urlopen(Request(URL, headers={'User-Agent': 'PetriKlar-map/1.0 (+https://www.petriklar.com)'}), timeout=30) as response:
        payload = json.load(response)
    result = convert(payload, previous, datetime.now(timezone.utc))
    if not result['stations']:
        raise RuntimeError('No NIZ Neckar temperatures; preserving existing file')
    args.output.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.output.with_suffix('.tmp')
    temporary.write_text(json.dumps(result, ensure_ascii=False, separators=(',', ':')) + '\n', encoding='utf8')
    temporary.replace(args.output)
    print('NIZ Neckar temperature stations:', len(result['stations']))


if __name__ == '__main__':
    main()
