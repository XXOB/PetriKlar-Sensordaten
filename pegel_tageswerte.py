"""Eigenes Pegelarchiv fuer PEGELONLINE-Stationen ohne amtliche Kennwerte.

Rund 300 WSV-Pegel (viele an Kanaelen, Stauhaltungen und der Kueste) haben
kein MNW/MW. PEGELONLINE gibt Messwerte nur 31 Tage rueckwirkend heraus, ein
laengeres offenes Archiv gibt es nicht. Deshalb sammeln wir selbst: je Station
und Tag der Median der 15-Minuten-Werte (beim ersten Mal die letzten 31 Tage).

Sobald ein Pegel mindestens 300 Tage aus den letzten zwoelf Monaten hat, bekommt
er - wie in Oesterreich und den Niederlanden - einen empirischen Vergleich aus
dem 10., 50. und 90. Perzentil (history_baseline). Das ist kein amtlicher
Kennwert. Aendert sich der Pegelnullpunkt, beginnt die Sammlung neu.

Laeuft stuendlich nach fetch_water_levels.py; Arbeit faellt nur an, wenn ein
neuer Tag abgeschlossen ist. Aufruf:
    python pegel_tageswerte.py --pegelkarte pegelkarte.json
"""
import argparse
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from statistics import median
from urllib.request import Request, urlopen

from europe_sources import history_baseline

ROOT = Path(__file__).resolve().parent
STORE = ROOT / 'pegel-tageswerte.json'
API = 'https://www.pegelonline.wsv.de/webservices/rest-api/v2/stations/'


def fetch(url):
    with urlopen(Request(url, headers={'User-Agent': 'PetriKlar-level-archive/1.0 (+https://www.petriklar.com)'}), timeout=40) as r:
        return json.load(r)


def target(s):
    return s.get('source') == 'PEGELONLINE / WSV' and s.get('reference_issue') == 'missing-references' and s.get('unit') == 'cm'


def expand(entry):
    """Kompakte Speicherung: Startdatum plus eine Liste (None = Luecke)."""
    if not entry or not entry.get('start'):
        return {}
    start = date.fromisoformat(entry['start'])
    return {start + timedelta(days=i): v for i, v in enumerate(entry.get('v', [])) if v is not None}


def compact(days, keep_from):
    days = {d: v for d, v in days.items() if d >= keep_from}
    if not days:
        return {'start': None, 'v': []}
    first, last = min(days), max(days)
    return {'start': first.isoformat(), 'v': [days.get(first + timedelta(days=i)) for i in range((last - first).days + 1)]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--pegelkarte', type=Path, default=ROOT / 'pegelkarte.json')
    parser.add_argument('--limit', type=int, default=120, help='Stationen mit Abruf je Lauf')
    args = parser.parse_args()
    now = datetime.now(timezone.utc)
    today, yesterday = now.date(), now.date() - timedelta(days=1)
    karte = json.loads(args.pegelkarte.read_text(encoding='utf8'))
    store = json.loads(STORE.read_text(encoding='utf8')) if STORE.exists() else {'stations': {}}
    archive = store.setdefault('stations', {})
    targets = [s for s in karte['stations'] if target(s)]

    def zero(s):
        z = s.get('gauge_zero') or {}
        return [z.get('value'), z.get('unit')]

    todo = []
    for s in targets:
        entry = archive.get(s['id'])
        if entry and entry.get('zero') != zero(s):
            entry = None  # Pegelnullpunkt geaendert: alte Werte nicht mehr vergleichbar
        days = expand(entry)
        archive[s['id']] = entry or {'zero': zero(s), 'start': None, 'v': []}
        last = max(days) if days else None
        if last is None or last < yesterday:
            span = 31 if last is None else min(31, (yesterday - last).days + 1)
            todo.append((s, days, span))
    # Laufende Stationen (kurze Luecke) zuerst, neue Stationen mit 31 Tagen danach.
    todo = sorted(todo, key=lambda job: job[2])[:args.limit]

    def one(job):
        s, days, span = job
        try:
            points = fetch(API + s['id'] + '/W/measurements.json?start=P' + str(span + 1) + 'D')
        except Exception as error:
            return s, days, str(error)[:140]
        per_day = {}
        for p in points:
            try:
                stamp = datetime.fromisoformat(str(p['timestamp']).replace('Z', '+00:00')).astimezone(timezone.utc)
                value = float(p['value'])
            except (KeyError, TypeError, ValueError):
                continue
            per_day.setdefault(stamp.date(), []).append(value)
        for d, values in per_day.items():
            # Nur abgeschlossene Tage mit genuegend Werten (15-Minuten-Takt: 96).
            if d < today and len(values) >= 24:
                days[d] = median(values)
        return s, days, None

    errors = []
    with ThreadPoolExecutor(max_workers=4) as pool:
        for s, days, error in pool.map(one, todo):
            if error:
                errors.append({'id': s['id'], 'error': error})
            archive[s['id']] = {'zero': zero(s), **compact(days, today - timedelta(days=400))}

    known = {s['id'] for s in targets}
    for sid in [k for k in archive if k not in known]:
        del archive[sid]  # Station hat inzwischen amtliche Kennwerte oder existiert nicht mehr
    with_baseline = 0
    for s in targets:
        days = expand(archive.get(s['id']))
        points = [{'t': d.isoformat() + 'T12:00:00+00:00', 'v': v} for d, v in sorted(days.items())]
        b = history_baseline(points, 'cm', now)
        if b:
            b.update(low=round(b['low'], 1), center=round(b['center'], 1), high=round(b['high'], 1),
                     source='PetriKlar-Archiv aus PEGELONLINE-Tagesmedianen')
            s['history_baseline'] = b
            with_baseline += 1
    store.update(updated=now.isoformat(), errors=errors)
    STORE.write_text(json.dumps(store, ensure_ascii=False, separators=(',', ':')), encoding='utf8')
    args.pegelkarte.write_text(json.dumps(karte, ensure_ascii=False, separators=(',', ':')) + '\n', encoding='utf8')
    lengths = sorted(len(expand(archive[s['id']])) for s in targets)
    print(f'Pegelarchiv: {len(targets)} Stationen ohne Kennwerte, {len(todo)} abgerufen, {len(errors)} Fehler, '
          f'{with_baseline} mit Vergleichswerten; Tage je Station: min {lengths[0] if lengths else 0}, '
          f'max {lengths[-1] if lengths else 0}')


if __name__ == '__main__':
    main()
