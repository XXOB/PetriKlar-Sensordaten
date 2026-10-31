"""Conservative map-only temperature screening; never alter source snapshots.

This is intentionally not a replacement for official QA. A reading is withheld
only on a strong cross-source conflict or an implausible step followed by a
day-long, tenth-degree-identical plateau. Other measurements remain untouched.
"""

from datetime import datetime, timezone
from math import asin, cos, radians, sin, sqrt
from zoneinfo import ZoneInfo


def timestamp(raw):
    try:
        text = str(raw)
        if '.' in text[:6] and len(text) >= 16:
            value = datetime.strptime(text[:16], '%d.%m.%Y %H:%M')
        else:
            value = datetime.fromisoformat(text.replace('Z', '+00:00'))
        if not value.tzinfo:
            value = value.replace(tzinfo=ZoneInfo('Europe/Berlin'))
        return value.timestamp()
    except (TypeError, ValueError):
        return None


def reading(station):
    for item in station.get('items', []):
        if item.get('label', '').lower() == 'wassertemperatur':
            try:
                value = float(str(item.get('value')).replace(',', '.'))
            except (ValueError, TypeError):
                continue
            stamp = timestamp(item.get('time'))
            if stamp is not None:
                return stamp, value
    return None


def points(station):
    result = {}
    for label, values in station.get('history', {}).items():
        if 'wassertemperatur' not in label.lower():
            continue
        for point in values:
            stamp = timestamp(point.get('t'))
            try:
                value = float(str(point.get('v')).replace(',', '.'))
            except (ValueError, TypeError):
                continue
            if stamp is not None:
                result[stamp] = value
    current = reading(station)
    if current:
        result[current[0]] = current[1]
    return sorted(result.items())


def km(a, b):
    lat1, lon1, lat2, lon2 = map(radians, (float(a['lat']), float(a['lon']),
                                           float(b['lat']), float(b['lon'])))
    dlat, dlon = lat2-lat1, lon2-lon1
    return 12742 * asin(sqrt(sin(dlat/2)**2 + cos(lat1)*cos(lat2)*sin(dlon/2)**2))


def screen(stations, archive=None):
    """Mutate only derived packet rows; return {station_id: [(start, end)]}.

    The flag remains in current packets so later browser-side merges cannot
    reintroduce a rejected raw reading into river colour or the bite index.
    """
    by_id = {str(row['id']): row for row in stations}
    archived = {str(row.get('id')): row.get('values', []) for row in (archive or {}).get('stations', [])}
    series = {key: points({**row, 'history': {
        '_archived Wassertemperatur': archived.get(key, []), **row.get('history', {})}})
        for key, row in by_id.items()}
    rejected = {}
    for key, observations in series.items():
        for i, (stamp, value) in enumerate(observations):
            if not -2 <= value <= 40:
                rejected.setdefault(key, []).append((stamp, stamp, 'physical-range'))
                continue
            if 0 < i < len(observations)-1:
                before, after = observations[i-1], observations[i+1]
                if stamp-before[0] <= 6*3600 and after[0]-stamp <= 6*3600 and \
                   abs(before[1]-after[1]) <= 3 and \
                   (value <= min(before[1], after[1])-8 or value >= max(before[1], after[1])+8):
                    rejected.setdefault(key, []).append((stamp, stamp, 'temporal-spike'))
                    continue
            if i == 0:
                continue
            previous = observations[i-1]
            if stamp-previous[0] > 12*3600 or abs(value-previous[1]) < 8:
                continue
            run = [(stamp, value)]
            for later in observations[i+1:]:
                if round(later[1], 1) != round(value, 1) or later[0]-run[-1][0] > 12*3600:
                    break
                run.append(later)
            if len(run) >= 5 and run[-1][0]-stamp >= 24*3600:
                rejected.setdefault(key, []).append((stamp, run[-1][0], 'step-and-stuck'))

    # Independent, almost co-located measurements make a strong OCR check.
    # Limit comparisons to current items and to the same named waterbody.
    candidates = [(key, row, reading(row)) for key, row in by_id.items()]
    candidates = [(key, row, point) for key, row, point in candidates if point]
    by_river = {}
    for candidate in candidates:
        by_river.setdefault(candidate[1].get('river'), []).append(candidate)
    for key, row, point in candidates:
        if not all(k in row for k in ('lat', 'lon')):
            continue
        stamp, value = point
        if any(start <= stamp <= end for start, end, _ in rejected.get(key, [])):
            continue
        peers = []
        for other_key, other, other_point in by_river.get(row.get('river'), []):
            if other_key == key or other.get('river') != row.get('river'):
                continue
            if any(start <= other_point[0] <= end for start, end, _ in rejected.get(other_key, [])):
                continue
            if other.get('src') == row.get('src') or not all(k in other for k in ('lat', 'lon')):
                continue
            if abs(stamp-other_point[0]) > 6*3600:
                continue
            if abs(float(other['lat'])-float(row['lat'])) > .02 or abs(float(other['lon'])-float(row['lon'])) > .04:
                continue
            if km(row, other) <= 2:
                peers.append((other.get('src'), other_point[1]))
        for i, (source_a, a) in enumerate(peers):
            for source_b, b in peers[i+1:]:
                if source_a != source_b and abs(a-b) <= 1.5 and abs(value-(a+b)/2) >= 3.5:
                    rejected.setdefault(key, []).append((stamp, stamp, 'co-located-conflict'))
                    break
            if any(start <= stamp <= end for start, end, _ in rejected.get(key, [])):
                break

    for key, row in by_id.items():
        intervals = rejected.get(key, [])
        current = reading(row)
        active = next((interval for interval in intervals if current and
                       interval[0] <= current[0] <= interval[1]), None)
        row['temperature_quarantined'] = active is not None
        if not intervals:
            continue
        if active:
            row['temperature_rejected_reading'] = {
                'value': current[1],
                'time': datetime.fromtimestamp(current[0], timezone.utc).isoformat()}
        row['temperature_quality_reason'] = active[2] if active else intervals[-1][2]
        row['temperature_rejected_intervals'] = [
            [datetime.fromtimestamp(start, timezone.utc).isoformat(),
             datetime.fromtimestamp(end, timezone.utc).isoformat()]
            for start, end, _ in intervals]
        for item in row.get('items', []):
            if item.get('label', '').lower() == 'wassertemperatur':
                stamp = timestamp(item.get('time'))
                if stamp is not None and any(start <= stamp <= end for start, end, _ in intervals):
                    item['quality'] = 'suspect'
                    item['value'] = None
        for label, values in row.get('history', {}).items():
            if 'wassertemperatur' in label.lower():
                row['history'][label] = [p for p in values if not (
                    (stamp := timestamp(p.get('t'))) is not None and
                    any(start <= stamp <= end for start, end, _ in intervals))]
    return {key: [(start, end) for start, end, _ in intervals] for key, intervals in rejected.items()}
