"""Explicitly refresh the three public gauge snapshots, then build_public.py.

Uses only the station UUIDs already present in the checked-in public snapshot.
An unavailable station keeps its old readings and original timestamps.
"""
import json
from datetime import datetime
from pathlib import Path
from urllib.request import Request, urlopen

SNAPSHOT = Path(__file__).with_name('discovery-pegel-snapshot.json')

def main():
    rows = json.loads(SNAPSHOT.read_text(encoding='utf-8'))
    refreshed = 0
    for station in rows:
        url = ('https://www.pegelonline.wsv.de/webservices/rest-api/v2/stations/'
               + station['uuid'] + '/W/measurements.json?start=P7D')
        try:
            with urlopen(Request(url, headers={'User-Agent': 'PetriKlar-public-station-builder/1.0'}), timeout=25) as response:
                points = json.load(response)
            points = [p for p in points if isinstance(p.get('value'), (int, float)) and p.get('timestamp')]
            points.sort(key=lambda p: datetime.fromisoformat(p['timestamp']))
            if not points:
                raise ValueError('Empty measurement series')
            station['history'] = points
            refreshed += 1
            print(station['shortname'], points[-1]['timestamp'])
        except Exception as exc:
            print(f"{station['shortname']}: keeping dated snapshot ({exc})")
    if refreshed:
        SNAPSHOT.write_text(json.dumps(rows, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(f'{refreshed}/{len(rows)} public gauges refreshed. Run tools/build_public.py next.')

if __name__ == '__main__':
    main()
