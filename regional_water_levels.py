"""Official regional gauge supplements. Never substitute warning thresholds for MHW."""
import ast
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from html import unescape
import json
import re
from urllib.request import Request, urlopen

HND = 'https://www.hnd.bayern.de/pegel/'
GKD = 'https://www.gkd.bayern.de/de/fluesse/wasserstand/bayern/'
HVZ = 'https://www.hvz.baden-wuerttemberg.de/'


def fetch_text(url):
    with urlopen(Request(url, headers={'User-Agent': 'PetriKlar-level-map/1.0 (+https://www.petriklar.com)'}), timeout=20) as response:
        return response.read().decode('utf-8', errors='replace')


def text(value):
    return ' '.join(unescape(re.sub(r'<[^>]*>', ' ', value)).split())


def rows(html):
    return [re.findall(r'<t[dh]\b[^>]*>(.*?)</t[dh]>', row, re.S | re.I)
            for row in re.findall(r'<tr\b[^>]*>(.*?)</tr>', html, re.S | re.I)]


def german_time(value):
    # HND timestamps use legal local time. Explicit CET/CEST conversion also works
    # on Windows without an installed IANA timezone database.
    dt = datetime.strptime(value[:16], '%d.%m.%Y %H:%M')
    march = datetime(dt.year, 3, 31, 2)
    october = datetime(dt.year, 10, 31, 3)
    march -= timedelta(days=(march.weekday() + 1) % 7)
    october -= timedelta(days=(october.weekday() + 1) % 7)
    hours = 2 if march <= dt < october else 1
    if value.endswith('MESZ'):
        hours = 2
    elif value.endswith('MEZ'):
        hours = 1
    return dt.replace(tzinfo=timezone(timedelta(hours=hours))).isoformat()


def parse_statistics(html):
    period = re.search(r'Hauptwerte\s*\((\d{4})\s*-\s*(\d{4})\)', text(html))
    if not period:
        return {}
    refs = {}
    for row in rows(html):
        cells = list(map(text, row))
        if len(cells) == 5 and cells[0] in ('MNW', 'MW', 'MHW') and cells[4] == 'cm':
            try:
                refs[cells[0]] = {'value': float(cells[3].replace(',', '.')), 'unit': 'cm',
                                 'timespanStart': period[1], 'timespanEnd': period[2]}
            except ValueError:
                pass
    return refs


def parse_measurements(html):
    if not re.search(r'Wasserstand\s+cm\s+über Pegelnullpunkt', text(html)):
        raise ValueError('Not a water-level table in cm above gauge zero')
    result = []
    for row in rows(html):
        if len(row) != 2:
            continue
        date, value = map(text, row)
        # Green rows are forecasts, not measured observations (including single values).
        if 'green' in row[1].lower() or not re.fullmatch(r'-?\d+(?:[,.]\d+)?', value):
            continue
        try:
            result.append({'t': german_time(date), 'v': float(value.replace(',', '.'))})
        except ValueError:
            pass
    return result


def bayern_inventory(html):
    result = []
    for row in rows(html):
        if len(row) < 4 or text(row[1]) not in ('Donau', 'Main', 'Inn', 'Lech', 'Salzach'):
            continue
        link = re.search(r'href="(https://www\.hnd\.bayern\.de/pegel/[^\"]+)"', row[0])
        if link and not link[1].endswith('/abfluss'):
            result.append((text(row[0]), link[1], text(row[1])))
    return result


def bayern_station(name, url, river, old, now, hourly, validate):
    slug = url.rstrip('/').split('/')[-1]
    number = slug.rsplit('-', 1)[-1]
    gkd = GKD + slug
    metadata = fetch_text(gkd)
    embedded = metadata.split('LfUMap.init(', 1)[1]
    config, _ = json.JSONDecoder().raw_decode(embedded)
    point = next(p for p in config['pointer'] if p['p'] == number)
    stats = fetch_text(gkd + '/statistik')
    refs = parse_statistics(stats)
    values_html = fetch_text(url + '/tabelle?methode=wasserstand&days=8')
    # Refuse changed or incompatible gauge zeroes between statistics and measurements.
    stat_zero = re.search(r'Pegelnullpunkts?höhe:?\s*([\d,]+)\s*m', text(metadata))
    value_zero = re.search(r'über Pegelnullpunkt\s*\(([\d,]+)\s*m', text(values_html))
    if not stat_zero or not value_zero or stat_zero[1] != value_zero[1]:
        raise ValueError('Reference and current gauge zero do not match')
    series = hourly(old.get('history', []) + parse_measurements(values_html), now)
    if not series:
        raise ValueError('No recent measured water levels')
    _, issue = validate({'unit': 'cm', 'characteristicValues': [dict(v, shortname=k) for k, v in refs.items()]})
    return {'id': 'hnd-' + number, 'official_number': number, 'name': name, 'river': river,
            'lat': float(point['lat']), 'lon': float(point['lon']), 'source': 'LfU Bayern / HND',
            'source_url': url, 'reference_source': 'LfU Bayern / GKD', 'reference_source_url': gkd + '/statistik',
            'unit': 'cm', 'references': refs, 'reference_issue': issue,
            'current': series[-1], 'history': series, 'mapped_river': True}


def hvz_stations(html, old, now, hourly):
    result = []
    for line in html.splitlines():
        if not line.strip().startswith('['):
            continue
        try:
            row = ast.literal_eval(line.strip().rstrip(','))
        except (ValueError, SyntaxError):
            continue
        if len(row) < 57 or row[2] != 'Donau' or row[5] not in ('cm', ''):
            continue
        key = 'hvz-' + row[0]
        try:
            current = {'t': german_time(row[6]), 'v': float(row[4])}
        except ValueError:
            current = {'t': None, 'v': None}
        series = hourly(old.get(key, {}).get('history', []) + [current], now)
        # Official WQ-derived water levels in cm; zero is a missing-value sentinel.
        refs = {k: {'value': float(row[i]), 'unit': 'cm', 'method': 'WQ-derived'}
                for k, i in [('MW', 40), ('MNW', 43)]
                if isinstance(row[i], (int, float)) and row[i] > 0}
        issue = None if len(refs) == 2 and refs['MNW']['value'] < refs['MW']['value'] else 'missing-references'
        result.append({'id': key, 'name': row[1], 'river': 'Donau', 'lat': row[21], 'lon': row[20],
                       'source': 'LUBW / HVZ', 'source_url': HVZ + 'pegel.html?id=' + row[0],
                       'unit': 'cm', 'references': refs, 'reference_issue': issue,
                       'reference_source': 'LUBW / HVZ (WQ)', 'reference_source_url': HVZ + 'pegel.html?id=' + row[0],
                       'current': series[-1] if series else current, 'history': series, 'mapped_river': True})
    return result


def supplement(stations, previous, now, hourly, validate):
    old = {s['id']: s for s in previous.get('stations', [])}
    additions, errors = [], []
    existing = {s.get('source_url', '').split('pegelnr=')[-1]: s for s in stations if 'pegelnr=' in s.get('source_url', '')}
    try:
        inventory = bayern_inventory(fetch_text(HND + 'tabellen'))
        with ThreadPoolExecutor(max_workers=4) as pool:
            tasks = {}
            for name, url, river in inventory:
                number = url.rstrip('/').rsplit('-', 1)[-1]
                if number in existing and existing[number]['reference_issue'] is None:
                    continue
                tasks[pool.submit(bayern_station, name, url, river, old.get('hnd-' + number, {}), now, hourly, validate)] = number
            for future in as_completed(tasks):
                number = tasks[future]
                try:
                    station = future.result()
                    if number in existing:
                        # Replace the whole station, never combine different-source references/readings.
                        stations.remove(existing[number])
                    additions.append(station)
                except Exception as exc:
                    errors.append({'source': 'HND', 'id': number, 'error': str(exc)[:160]})
    except Exception as exc:
        errors.append({'source': 'HND', 'error': str(exc)[:160]})
    try:
        additions.extend(hvz_stations(fetch_text(HVZ + 'js/hvz_peg_stmn.js'), old, now, hourly))
    except Exception as exc:
        errors.append({'source': 'HVZ', 'error': str(exc)[:160]})
    present = {s['id'] for s in additions}
    for key, station in old.items():
        if key.startswith(('hnd-', 'hvz-')) and key not in present:
            if key.startswith('hnd-') and key[4:] in existing and existing[key[4:]] in stations and existing[key[4:]]['reference_issue'] is None:
                continue
            retained = dict(station, history=hourly(station.get('history', []), now))
            additions.append(retained)
    # HVZ also mirrors Neu-Ulm: keep the HND series when present.
    if any(s['id'] == 'hnd-10026293' for s in additions):
        additions = [s for s in additions if s['id'] != 'hvz-09047']
    # State and federal gauge numbers differ at some shared measuring sites.
    # Match name, river and nearby coordinates; retain one complete source series.
    for candidate in additions:
        duplicate = next((s for s in stations if same_site(s, candidate)), None)
        if duplicate:
            if duplicate['reference_issue'] is None or candidate['reference_issue'] is not None:
                continue
            stations.remove(duplicate)
        stations.append(candidate)
    return errors


def same_site(a, b):
    if a['river'] != b['river'] or a['name'].casefold() != b['name'].casefold():
        return False
    try:
        return abs(float(a['lat']) - float(b['lat'])) < 0.01 and abs(float(a['lon']) - float(b['lon'])) < 0.01
    except (TypeError, ValueError, KeyError):
        return False
