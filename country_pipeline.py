"""Isolated country snapshots; combine only locally when publishing."""
import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def read(path):
    return json.loads(path.read_text(encoding='utf8')) if path.exists() else {'stations': []}


def country(row):
    return {'bafu': 'CH', 'rijkswaterstaat': 'NL'}.get(row.get('src'), row.get('country', 'AT'))


def combine(payloads):
    from build_sensor_packets import retain
    from europe_sources import history_baseline
    now = datetime.now(timezone.utc)
    rows = {}
    report = {}
    for payload in payloads:
        report.update(payload.get('source_report', {}))
        for incoming in payload.get('stations', []):
            old = rows.get(incoming['id'], {})
            row = {**old, **incoming}
            items = {i['label']: i for i in old.get('items', [])}
            for item in incoming.get('items', []):
                prior = items.get(item['label'], {})
                if str(item.get('time') or '') >= str(prior.get('time') or ''):
                    items[item['label']] = item
            row['items'] = list(items.values())
            labels = set(old.get('history', {})) | set(incoming.get('history', {}))
            row['history'] = {label: retain(old.get('history', {}).get(label, []) + incoming.get('history', {}).get(label, []), now.timestamp()) for label in labels}
            level = items.get('Pegelstand')
            row.pop('history_baseline', None)
            if level:
                baseline = history_baseline(row['history'].get('Pegelstand', []), level.get('unit'), now)
                if baseline:
                    row['history_baseline'] = baseline
            rows[row['id']] = row
    return {'updated': now.isoformat(), 'stations': list(rows.values()), 'source_report': report}


def write(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, separators=(',', ':')), encoding='utf8')


def run_country(code, historical=False):
    folder = ROOT / 'countries' / code / ('history' if historical else 'current')
    sources = [read(ROOT / 'europe-history.json')]
    sources += [read(ROOT / 'countries' / code / kind / 'europe-history.json') for kind in ('current', 'history')]
    seed = combine(sources)
    seed['stations'] = [s for s in seed['stations'] if country(s) == code]
    write(folder / 'europe-history.json', seed)
    command = [sys.executable, str(ROOT / 'europe_sources.py'), '--only', code, '--output', str(folder / 'europe-water.json')]
    command += ['--history-only', '--history-budget', '2'] if historical else ['--no-history']
    subprocess.run(command, check=True)


def recent(points, now):
    buckets = {}
    for point in points:
        try:
            stamp = datetime.fromisoformat(point['t'].replace('Z', '+00:00')).timestamp()
        except (KeyError, ValueError, TypeError):
            continue
        if now - 8*86400 <= stamp <= now:
            bucket = int(stamp // 14400)
            if bucket not in buckets or stamp > buckets[bucket][0]:
                buckets[bucket] = (stamp, point)
    return [p for _, p in sorted(buckets.values())]


def publish():
    payloads = [read(ROOT / 'europe-history.json')]
    for code in ('AT', 'CH', 'NL'):
        for kind in ('history', 'current'):
            payloads.append(read(ROOT / 'countries' / code / kind / 'europe-history.json'))
    merged = combine(payloads)
    write(ROOT / 'europe-history.json', merged)
    # Only four-hour samples for the eight-day map window.
    now = datetime.now(timezone.utc).timestamp()
    for row in merged['stations']:
        row['history'] = {label: recent(points, now) for label, points in row.get('history', {}).items()}
    write(ROOT / 'europe-water.json', merged)
    for kind, label in [('level', 'Pegelstand'), ('temperature', 'Wassertemperatur')]:
        selected = [{**s, 'items': [i for i in s.get('items', []) if i['label'] == label], 'history': {label: s.get('history', {}).get(label, [])}} for s in merged['stations'] if any(i['label'] == label for i in s.get('items', [])) or s.get('history', {}).get(label)]
        write(ROOT / ('europe-' + kind + '.json'), {**merged, 'stations': selected})


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=['AT', 'CH', 'NL', 'publish'])
    parser.add_argument('--history', action='store_true')
    args = parser.parse_args()
    publish() if args.action == 'publish' else run_country(args.action, args.history)
