"""Collection clock and deduplication. Never request authorities from here."""
import argparse
import json
import math
import os
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo


def timestamp(value):
    if value is None or isinstance(value, bool):
        return None
    try:
        if isinstance(value, (int, float)):
            return (value / 1000 if value > 100_000_000_000 else float(value)) if math.isfinite(value) else None
        raw = str(value).strip()
        dt = (datetime.strptime(raw, '%d.%m.%Y %H:%M') if '.' in raw[:6]
              else datetime.fromisoformat(raw.replace('Z', '+00:00')))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=ZoneInfo('Europe/Berlin'))
        return dt.timestamp()
    except (TypeError, ValueError, OverflowError):
        return None


def iso(stamp):
    return datetime.fromtimestamp(stamp, timezone.utc).isoformat() if stamp is not None else None


def read(path):
    try:
        value = json.loads(Path(path).read_text(encoding='utf-8-sig'))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def should_collect(previous, now, force=False, interval=50*60):
    if force or previous.get('status') != 'success':
        return True
    last = timestamp(previous.get('started_at'))
    if last is None:
        return True
    # Bad/future metadata must not turn into an indefinite collection lock.
    age = now - last
    return not (-900 <= age < interval)


def source_updated_at(payloads):
    candidates = []
    for payload in payloads:
        value = payload.get('source_updated_at', payload.get('updated') or payload.get('fetched_at'))
        stamp = timestamp(value)
        if stamp is not None:
            candidates.append(stamp)
    return iso(max(candidates)) if candidates else None


def measurement_health(stations, kind, now):
    limit = 24*3600 if kind == 'temperature' else 6*3600
    latest = None
    counts = {'stations': len(stations), 'fresh': 0, 'stale': 0, 'missing': 0, 'future': 0}
    sources = {}
    for station in stations:
        source = str(station.get('src') or station.get('source') or 'unknown')
        summary = sources.setdefault(source, {'stations': 0, 'fresh': 0, 'latest_measurement_at': None})
        summary['stations'] += 1
        item = (next((i for i in station.get('items') or [] if i.get('label') == 'Wassertemperatur'), {})
                if kind == 'temperature' else station.get('current') or {})
        value = item.get('value') if kind == 'temperature' else item.get('v')
        stamp = timestamp(item.get('time') if kind == 'temperature' else item.get('t'))
        try:
            number = float(str(value).replace(',', '.'))
            valid = (math.isfinite(number) and not isinstance(value, bool) and
                     (-2 <= number <= 40 if kind == 'temperature' else number != -777))
        except (ValueError, TypeError):
            valid = False
        if not valid or stamp is None or station.get('temperature_quarantined') and kind == 'temperature':
            counts['missing'] += 1
        elif stamp > now+900:
            counts['future'] += 1
        else:
            latest = stamp if latest is None else max(latest, stamp)
            prior = timestamp(summary['latest_measurement_at'])
            summary['latest_measurement_at'] = iso(max(stamp, prior)) if prior is not None else iso(stamp)
            if now-stamp <= limit:
                counts['fresh'] += 1
                summary['fresh'] += 1
            else:
                counts['stale'] += 1
    return {**counts, 'freshness_limit_hours': limit//3600, 'latest_measurement_at': iso(latest),
            'sources': sources}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=['gate', 'start', 'finish', 'publish-check'])
    parser.add_argument('--file', type=Path, default=Path('collection-status.json'))
    parser.add_argument('--running-file', type=Path, default=Path('work/collection-running.json'))
    parser.add_argument('--force', action='store_true')
    parser.add_argument('--result', default='success')
    args = parser.parse_args()
    now = datetime.now(timezone.utc).timestamp()
    previous = read(args.file)
    if args.action == 'publish-check':
        event = read(os.getenv('GITHUB_EVENT_PATH', ''))
        run = event.get('workflow_run') or {}
        ready = run.get('name') != 'Messwerte' or str(run.get('id')) == str(previous.get('run_id'))
        print('Daten veroeffentlichen.' if ready else 'Messwerte-Lauf hat keinen neuen Abruf gespeichert; keine Scheinaktualisierung.')
        if os.getenv('GITHUB_OUTPUT'):
            with open(os.environ['GITHUB_OUTPUT'], 'a', encoding='utf8') as handle:
                handle.write(f'ready={str(ready).lower()}\n')
        return
    if args.action == 'gate':
        collect = should_collect(previous, now, args.force or os.getenv('PETRI_FORCE') == 'true')
        print('Messwerte abrufen.' if collect else 'Letzter Abruf ist noch keine 50 Minuten her; kein Doppelabruf.')
        if os.getenv('GITHUB_OUTPUT'):
            with open(os.environ['GITHUB_OUTPUT'], 'a', encoding='utf8') as handle:
                handle.write(f'collect={str(collect).lower()}\n')
        return
    run_id = os.getenv('GITHUB_RUN_ID')
    if args.action == 'start':
        status = {'status': 'running', 'started_at': iso(now), 'finished_at': None,
                  'previous_finished_at': previous.get('finished_at'), 'run_id': run_id}
        # Do not dirty a tracked file before intermediate country commits/rebases.
        args.running_file.parent.mkdir(parents=True, exist_ok=True)
        args.running_file.write_text(json.dumps(status, ensure_ascii=False, indent=2)+'\n', encoding='utf8')
        return
    else:
        running = read(args.running_file)
        status = {**(running if running.get('run_id') == run_id else {}),
                  'status': args.result, 'finished_at': iso(now), 'run_id': run_id}
    status['run_url'] = f'https://github.com/XXOB/PetriKlar-Sensordaten/actions/runs/{run_id}' if run_id else None
    args.file.write_text(json.dumps(status, ensure_ascii=False, indent=2)+'\n', encoding='utf8')
    if args.action == 'finish' and os.getenv('GITHUB_STEP_SUMMARY'):
        with open(os.environ['GITHUB_STEP_SUMMARY'], 'a', encoding='utf8') as handle:
            handle.write('## Tatsächlicher Messwerte-Abruf\n'
                         f"Start (UTC): {status.get('started_at', 'unbekannt')}\n\n"
                         f"Ende (UTC): {status['finished_at']}\n\nStatus: {status['status']}\n")


if __name__ == '__main__':
    main()
