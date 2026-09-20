"""Vergleichswerte fuer niederlaendische Pegel aus einem vollen Messjahr.

Rijkswaterstaat veroeffentlicht keine MNW/MW-Kennwerte, aber die vollstaendigen
Zehn-Minuten-Messungen. Wie bei den oesterreichischen eHYD-Archiven nehmen wir
je Pegel das juengste vollstaendige Kalenderjahr, bilden Tagesmediane und
daraus das 10., 50. und 90. Perzentil. Das ist ausdruecklich KEIN amtlicher
Kennwert, sondern ein empirischer Vergleich ("niedrig / normal / hoch").

Verwendet wird nur, was vergleichbar ist: gleicher Ort, Einheit cm und gleicher
Hoehenbezug (z.B. NAP) wie der heutige Messwert. Passt der heutige Wert nicht
zum Archivjahr, wurde der Pegel versetzt - dann lieber keine Referenz.

Der Abruf ist gross (ein Jahr = rund 50 000 Werte je Pegel). Deshalb laeuft
das Skript mit Zeitbudget und macht beim naechsten Lauf dort weiter, wo es
aufgehoert hat. Fertige Pegel werden erst im Folgejahr neu berechnet.

Aufruf:
    python nl_level_references.py --stations countries/NL/current/europe-water.json
"""
import argparse
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import Request, urlopen

from europe_sources import NL_BASE, nl_daily_level_history, number

ROOT = Path(__file__).resolve().parent
TARGET = ROOT / 'nl-level-references.json'
URL = NL_BASE + 'ONLINEWAARNEMINGENSERVICES/OphalenWaarnemingen'


def fetch_period(location, start, end, timeout=150):
    body = {'Locatie': {'Code': location},
            'AquoPlusWaarnemingMetadata': {'AquoMetadata': {
                'Compartiment': {'Code': 'OW'}, 'Grootheid': {'Code': 'WATHTE'}, 'ProcesType': 'meting'}},
            'Periode': {'Begindatumtijd': start.strftime('%Y-%m-%dT%H:%M:%S+00:00'),
                        'Einddatumtijd': end.strftime('%Y-%m-%dT%H:%M:%S+00:00')}}
    request = Request(URL, data=json.dumps(body).encode(), headers={
        'User-Agent': 'PetriKlar/1.0 (+https://www.petriklar.com)',
        'Accept': 'application/json', 'Content-Type': 'application/json'})
    with urlopen(request, timeout=timeout) as response:
        return json.loads(response.read(200_000_000))


def daily_medians(location, year, now):
    """Tagesmediane eines Kalenderjahres; bei Fehlern in zwei Halbjahren."""
    start, end = datetime(year, 1, 1, tzinfo=timezone.utc), datetime(year + 1, 1, 1, tzinfo=timezone.utc)
    try:
        parts = [nl_daily_level_history(fetch_period(location, start, end), now)]
    except Exception:
        middle = datetime(year, 7, 1, tzinfo=timezone.utc)
        parts = [nl_daily_level_history(fetch_period(location, a, b), now) for a, b in ((start, middle), (middle, end))]
    merged = {}
    for part in parts:
        for (loc, unit, datum), series in part.items():
            days = merged.setdefault((unit, datum), {})
            for point in series['points']:
                day = point['t'][:10]
                if day.startswith(str(year)):
                    days[day] = point['v']
    return merged


def baseline(days, unit, datum, location, year):
    ds = sorted(datetime.fromisoformat(d).date() for d in days)
    if len(ds) < 300 or (ds[-1] - ds[0]).days < 330 or max((b - a).days for a, b in zip(ds, ds[1:])) > 30:
        raise ValueError(f'Year {year} incomplete ({len(ds)} days)')
    values = sorted(days.values())

    def q(p):
        n = (len(values) - 1) * p
        i = int(n)
        return values[i] + (values[min(i + 1, len(values) - 1)] - values[i]) * (n - i)

    low, center, high = q(.1), q(.5), q(.9)
    if not low < center < high:
        raise ValueError('No usable distribution')
    return dict(method='daily-median-p10-p50-p90', low=round(low, 1), center=round(center, 1), high=round(high, 1),
                unit=unit, datum=datum, start=str(ds[0]), end=str(ds[-1]), days=len(ds),
                source='Rijkswaterstaat WaterWebservices', source_station=location)


def plausible(b, row):
    live = next((number(i.get('value')) for i in row.get('items', []) if i.get('label') == 'Pegelstand'), None)
    span = b['high'] - b['low']
    return live is None or b['low'] - 3 * span <= live <= b['high'] + 3 * span


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--stations', type=Path, required=True)
    parser.add_argument('--budget-minutes', type=float, default=40)
    args = parser.parse_args()
    now = datetime.now(timezone.utc)
    year = now.year - 1
    deadline = time.monotonic() + args.budget_minutes * 60
    previous = json.loads(TARGET.read_text(encoding='utf8')) if TARGET.exists() else {}
    refs = previous.get('references', {})
    tried = previous.get('tried', {})
    rows = [s for s in json.loads(args.stations.read_text(encoding='utf8'))['stations']
            if s.get('src') == 'rijkswaterstaat' and s.get('source_station')
            and any(i.get('label') == 'Pegelstand' and i.get('unit') == 'cm' for i in s.get('items', []))]
    by_location = {}
    for row in rows:
        by_location.setdefault(row['source_station'], []).append(row)

    def done(location):
        group = by_location[location]
        if all(str(refs.get(r['id'], {}).get('end', '')).startswith(str(year)) for r in group):
            return True
        # Ohne brauchbares Jahr nicht bei jedem Lauf erneut laden; nach
        # Abruffehlern hoechstens dreimal versuchen.
        t = tried.get(location, {})
        return t.get('year') == year and (not t.get('error') or t.get('attempts', 1) >= 3)

    todo = [loc for loc in sorted(by_location) if not done(loc)]
    errors, finished = [], 0
    for location in todo:
        if time.monotonic() > deadline:
            break
        try:
            series = daily_medians(location, year, now)
        except Exception as error:
            errors.append({'location': location, 'error': str(error)[:200] or type(error).__name__})
            attempts = tried.get(location, {}).get('attempts', 0) + 1 if tried.get(location, {}).get('year') == year else 1
            tried[location] = {'year': year, 'error': errors[-1]['error'], 'attempts': attempts}
            continue
        for row in by_location[location]:
            unit = next(i['unit'] for i in row['items'] if i.get('label') == 'Pegelstand')
            days = series.get((unit, row.get('level_datum')))
            try:
                if not days:
                    raise ValueError('No series with the same unit and datum')
                b = baseline(days, unit, row.get('level_datum'), location, year)
                if not plausible(b, row):
                    raise ValueError('Current reading does not fit the archive year')
                refs[row['id']] = b
            except Exception as error:
                refs.pop(row['id'], None)
                errors.append({'id': row['id'], 'error': str(error)[:200]})
        tried[location] = {'year': year}
        finished += 1
        print(location, 'ok', flush=True)
    remaining = len([loc for loc in by_location if not done(loc)])
    out = {'updated': now.isoformat(), 'year': year, 'references': refs, 'tried': tried,
           'errors': errors, 'remaining_locations': remaining}
    TARGET.write_text(json.dumps(out, ensure_ascii=False, separators=(',', ':')), encoding='utf8')
    print(f'NL references: {len(refs)} stations, {finished} locations this run, {remaining} remaining, {len(errors)} errors')


if __name__ == '__main__':
    main()
