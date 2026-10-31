"""Fetch additional official river gauges for the European water maps.

Sources: Hydrographischer Dienst Kärnten/Steiermark, Hub'Eau (France), and ARPA Lombardia.
Only measured water levels and water temperatures are imported. No reference
levels or temperatures are inferred; ARPA air-temperature sensors are excluded.
"""
from __future__ import annotations

from datetime import datetime, timezone, timedelta
import json
import math
from pathlib import Path
import re
import time
from html.parser import HTMLParser
from zoneinfo import ZoneInfo
import urllib.error
import urllib.parse
import urllib.request

# Liegt im Website-Repository unter tools/, im Sensordaten-Repository im
# Hauptordner. Beide Fassungen sind identisch (Test test_packet_rivers.py).
ROOT = Path(__file__).resolve().parent
if ROOT.name == 'tools':
    ROOT = ROOT.parent
OUT = ROOT / 'assets/data/river-sensor-additions.json'
GEOMETRY = ROOT / 'assets/data/temperature-rivers.geojson'
UA = 'PetriKlar/1.0 (+https://www.petriklar.com)'
HUB_BASE = 'https://hubeau.eaufrance.fr/api/v2/hydrometrie/'
HUB_TEMPERATURE = 'https://hubeau.eaufrance.fr/api/v1/temperature/'
KTN_URL = 'https://info.ktn.gv.at/asp/hydro/daten/json/hdkaernten_abfluss.json'
STMK_BASE = 'https://egov.stmk.gv.at/at.gv.stmk.hydavis-p/pub/praesentation/index.xhtml'
STMK_INFO = 'https://www.hydrografie.steiermark.at/cms/beitrag/12813605/142514764'
STMK_LICENSE = 'Public download — publisher disclaimer accepted'
ARPA_DATA = 'https://www.dati.lombardia.it/resource/647i-nhxk.json'
ARPA_META = 'https://www.dati.lombardia.it/resource/nf78-nj6b.json'
ARPA_META_PAGE = 'https://www.dati.lombardia.it/api/views/nf78-nj6b.json'
ARPA_CURRENT_PAGE = 'https://www.dati.lombardia.it/api/views/647i-nhxk.json'
ARPA_LOOKBACK_DAYS = 8


def read_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return default


def fetch_json(url: str, timeout: int = 35):
    request = urllib.request.Request(url, headers={
        'User-Agent': UA, 'Accept': 'application/json', 'Cache-Control': 'no-cache'})
    for attempt in range(4):
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return json.loads(response.read().decode('utf-8'))
        except urllib.error.HTTPError as exc:
            if exc.code != 429 or attempt == 3:
                raise
            # Socrata sometimes rate-limits a fresh client. Back off rather
            # than dropping the Italian sensor feed on the first 429.
            time.sleep(2 ** (attempt + 2))


def fetch_text(url: str, timeout: int = 45):
    request = urllib.request.Request(url, headers={
        'User-Agent': UA, 'Accept': 'text/html', 'Cache-Control': 'no-cache'})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read(32_000_001).decode('utf-8', 'replace')


def query_url(base: str, params: dict) -> str:
    return base + '?' + urllib.parse.urlencode(params)


def number(value):
    try:
        value = float(str(value).replace(',', '.'))
        return value if math.isfinite(value) else None
    except (TypeError, ValueError):
        return None


def stamp(value):
    try:
        dt = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.timestamp()
    except (TypeError, ValueError):
        return 0.0


def lines_for(geojson: dict, river: str):
    lines = []
    for feature in geojson.get('features', []):
        if str(feature.get('properties', {}).get('name', '')).casefold() != river.casefold():
            continue
        geometry = feature.get('geometry') or {}
        coords = geometry.get('coordinates') or []
        if geometry.get('type') == 'LineString':
            lines.append(coords)
        elif geometry.get('type') == 'MultiLineString':
            lines.extend(coords)
    return lines


def distance_km(lon: float, lat: float, lines) -> float:
    """Shortest local equirectangular point-to-segment distance."""
    best = float('inf')
    mx, my = 111.32 * math.cos(math.radians(lat)), 110.574
    px, py = lon * mx, lat * my
    for line in lines:
        for a, b in zip(line, line[1:]):
            ax, ay = a[0] * mx, a[1] * my
            bx, by = b[0] * mx, b[1] * my
            dx, dy = bx - ax, by - ay
            denom = dx * dx + dy * dy
            t = 0 if not denom else max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / denom))
            best = min(best, math.hypot(px - (ax + t * dx), py - (ay + t * dy)))
    return best


def hourly(points, now: float, keep_days: int = 8):
    buckets = {}
    for point in points:
        value = number(point.get('v'))
        ts = stamp(point.get('t'))
        if value is None or not now - keep_days * 86400 <= ts <= now + 900:
            continue
        bucket = int(ts // 3600)
        if bucket not in buckets or ts > buckets[bucket][0]:
            buckets[bucket] = (ts, {'t': point['t'], 'v': value})
    return [entry[1] for entry in sorted(buckets.values(), key=lambda x: x[0])]


def ktn_levels(geojson: dict, now: float):
    data = fetch_json(KTN_URL, timeout=45)
    route = lines_for(geojson, 'Drau')
    stations = []
    for feature in data.get('features', []):
        p = feature.get('properties') or {}
        # Tributary gauges within the Drau basin are not Drau gauges.
        if str(p.get('gewaesser', '')).casefold() != 'drau':
            continue
        hzbnr = str(p.get('hzbnr') or '').strip()
        coords = (feature.get('geometry') or {}).get('coordinates') or [None, None]
        lon, lat = number(coords[0]), number(coords[1])
        value = number(p.get('letzter_wert_w'))
        observed = p.get('letzter_wert_w_date')
        if not hzbnr or lon is None or lat is None or value is None or not observed:
            continue
        if value == -777 or stamp(observed) > now + 900 or stamp(observed) < now - 3 * 86400:
            continue
        if distance_km(lon, lat, route) > 1.2:
            continue
        history = hourly([{'t': x.get('date'), 'v': x.get('value')}
                          for x in (p.get('werte') or {}).get('wasserstand', [])], now)
        current = {'t': observed, 'v': value}
        history = hourly(history + [current], now)
        stations.append({
            'id': 'at-ktn-' + hzbnr, 'name': p.get('name') or ('Drau ' + hzbnr),
            'river': 'Drau', 'country': 'AT', 'src': 'at-ktn', 'source': 'at-ktn',
            'lat': lat, 'lon': lon, 'unit': 'cm', 'current': current, 'history': history,
            'items': [{'label': 'Pegelstand', 'value': value, 'unit': 'cm', 'time': observed}],
            'license': 'CC BY 4.0', 'license_url': 'https://creativecommons.org/licenses/by/4.0/',
            'attribution': p.get('lizenzzitat') or 'CC-BY-4.0: Land Kärnten - data.gv.at',
            'source_url': KTN_URL,
        })
    if len(stations) < 5:
        raise RuntimeError(f'Kärnten-Feed returned only {len(stations)} Drau gauges')
    return stations


def parse_stmk_table(page: str, geojson: dict, now: float, catalogue: list):
    """Read current and explicitly offset historical values from HyDaVis."""
    class Rows(HTMLParser):
        def __init__(self):
            super().__init__(convert_charrefs=True)
            self.rows, self.row, self.cell = [], None, None

        def handle_starttag(self, tag, attrs):
            if tag == 'tr':
                self.row = []
            elif tag in ('td', 'th') and self.row is not None and self.cell is None:
                self.cell = []

        def handle_data(self, data):
            if self.cell is not None:
                self.cell.append(data)

        def handle_endtag(self, tag):
            if tag in ('td', 'th') and self.cell is not None and self.row is not None:
                self.row.append(' '.join(''.join(self.cell).split()))
                self.cell = None
            elif tag == 'tr' and self.row is not None:
                self.rows.append(self.row)
                self.row = None

    parser = Rows()
    parser.feed(page)
    route = lines_for(geojson, 'Mur')
    by_id = {str(s.get('id') or '').removeprefix('at-stmk-'): s for s in catalogue
             if str(s.get('id') or '').startswith('at-stmk-')}
    stations = []
    for cells in parser.rows:
        if len(cells) < 4 or not any(cell.strip().casefold() == 'mur' for cell in cells):
            continue
        match = re.search(r'\bow\d{4}\b', cells[0], re.I)
        if not match:
            continue
        station_id = match.group(0).lower()
        meta = by_id.get(station_id)
        if not meta:
            continue
        name = re.sub(r'^' + re.escape(station_id) + r'\s*', '', cells[0], flags=re.I).strip()
        try:
            observed_dt = datetime.strptime(cells[2], '%d.%m.%Y %H:%M:%S').replace(
                tzinfo=timezone(timedelta(hours=1)))
        except ValueError:
            continue
        value = number(cells[3])
        lat, lon = number(meta.get('lat')), number(meta.get('lon'))
        if value is None or lat is None or lon is None:
            continue
        observed = observed_dt.isoformat()
        age = now - observed_dt.timestamp()
        if age < -900 or age > 8 * 86400 or distance_km(lon, lat, route) > 1.2:
            continue
        station_url = query_url(STMK_BASE, {
            'messcode': '2001', 'stationsstatus': 'ONLINE', 'ansichtstyp': 'liste',
            'hdnr': station_id,
        })
        current = {'t': observed, 'v': value}
        # HyDaVis explicitly provides readings for 1, 2, ... hours before the
        # last transmitted value. Preserve those observed values and their
        # declared offsets so the Mur timeline is useful, without fabricating
        # intermediate hourly values.
        offsets = (1, 2, 3, 4, 5, 6, 12, 24, 36, 48)
        history = [current]
        for offset, raw in zip(offsets, cells[4:14]):
            old_value = number(raw)
            if old_value is not None:
                old_time = (observed_dt - timedelta(hours=offset)).isoformat()
                history.append({'t': old_time, 'v': old_value})
        history.sort(key=lambda point: stamp(point['t']))
        stations.append({
            'id': 'at-stmk-' + station_id, 'name': name or meta.get('name') or station_id,
            'river': 'Mur', 'country': 'AT', 'src': 'at-stmk', 'source': 'at-stmk',
            'lat': lat, 'lon': lon, 'unit': 'cm',
            'current': current,
            'history': history,
            'items': [{'label': 'Pegelstand', 'value': value, 'unit': 'cm', 'time': observed}],
            'license': STMK_LICENSE, 'license_url': STMK_INFO,
            'attribution': 'Land Steiermark – Hydrografischer Dienst',
            'source_url': station_url,
            'data_note': 'Ungeprüfte Rohmessung. Historische Zeitpunkte folgen den amtlichen Spalten „Wert für n Stunden“; der Herausgeber weist auf mögliche Mess- und Übertragungsfehler hin.',
        })
    return stations


def parse_stmk_levels(page: str, geojson: dict, now: float, catalogue: list | None = None):
    """Parse current Mur readings embedded in the official HyDaVis station list."""
    catalogue = catalogue or []
    table_stations = parse_stmk_table(page, geojson, now, catalogue)
    if len(table_stations) >= 5:
        return table_stations
    match = re.search(r"var stationen = JSON\.parse\('(.*?)'\);", page, re.S)
    if not match:
        raise ValueError('HyDaVis station inventory/table missing')
    try:
        inventory = json.loads(match.group(1).replace("\\'", "'"))
    except ValueError as exc:
        raise ValueError('HyDaVis station inventory is not valid JSON') from exc
    route = lines_for(geojson, 'Mur')
    if not route:
        raise RuntimeError('No mapped line for Mur')
    stations = []
    for meta in inventory:
        if str(meta.get('gewaesser') or '').strip().casefold() != 'mur':
            continue
        station_id = str(meta.get('hdnr') or '').strip()
        lat, lon = number(meta.get('koordinatenBreite')), number(meta.get('koordinatenLaenge'))
        latest = meta.get('letzterMesswert') or {}
        value = number(latest.get('wert'))
        raw_time = str(latest.get('zeitpunkt') or '').strip()
        observed_dt = None
        for fmt in ('%b %d, %Y, %I:%M:%S %p', '%d.%m.%Y %H:%M:%S'):
            try:
                observed_dt = datetime.strptime(raw_time, fmt).replace(tzinfo=timezone(timedelta(hours=1)))
                break
            except ValueError:
                continue
        if not station_id or lat is None or lon is None or value is None or observed_dt is None:
            continue
        observed = observed_dt.isoformat()
        age = now - observed_dt.timestamp()
        if age < -900 or age > 8 * 86400 or distance_km(lon, lat, route) > 1.2:
            continue
        station_url = query_url(STMK_BASE, {
            'messcode': '2001', 'stationsstatus': 'ONLINE', 'ansichtstyp': 'liste',
            'hdnr': station_id,
        })
        stations.append({
            'id': 'at-stmk-' + station_id, 'name': meta.get('mstnam') or station_id,
            'river': 'Mur', 'country': 'AT', 'src': 'at-stmk', 'source': 'at-stmk',
            'lat': lat, 'lon': lon, 'unit': 'cm',
            'current': {'t': observed, 'v': value},
            'history': [{'t': observed, 'v': value}],
            'items': [{'label': 'Pegelstand', 'value': value, 'unit': 'cm', 'time': observed}],
            'license': STMK_LICENSE, 'license_url': STMK_INFO,
            'attribution': 'Land Steiermark – Hydrografischer Dienst',
            'source_url': station_url,
            'data_note': 'Ungeprüfte Rohmessung; der Herausgeber weist auf mögliche Mess- und Übertragungsfehler hin.',
        })
    if len(stations) < 5:
        raise RuntimeError(f'HyDaVis returned only {len(stations)} current Mur gauges')
    return stations


def steiermark_levels(geojson: dict, now: float, catalogue: list | None = None):
    page = fetch_text(query_url(STMK_BASE, {
        'messcode': '2001', 'stationsstatus': 'ONLINE', 'ansichtstyp': 'liste'}))
    return parse_stmk_levels(page, geojson, now, catalogue)


def hubeau_sites(label: str):
    return fetch_json(query_url(HUB_BASE + 'referentiel/sites', {
        'format': 'json', 'size': 1000, 'libelle_cours_eau': label}))


def france_levels(geojson: dict, now: float):
    sources = [('Maas', 'La Meuse'), ('Mosel', 'La Moselle'), ('Rhône', 'Le Rhône')]
    grouped = {}
    for river, label in sources:
        route = lines_for(geojson, river)
        if not route:
            raise RuntimeError(f'No mapped line for {river}')
        data = hubeau_sites(label)
        for site in data.get('data', []):
            if site.get('statut_site') not in (1, '1'):
                continue
            lon, lat = number(site.get('longitude_site')), number(site.get('latitude_site'))
            code = str(site.get('code_site') or '').strip()
            if not code or lon is None or lat is None or distance_km(lon, lat, route) > 1.5:
                continue
            grouped.setdefault(river, {})[code] = {
                'id': code, 'name': site.get('libelle_site') or f'{label} {code}',
                'river': river, 'country': 'FR', 'src': 'fr-hubeau', 'source': 'fr-hubeau',
                'lat': lat, 'lon': lon, 'unit': 'cm', 'code_site': code,
            }
    stations = []
    date_start = (datetime.now(timezone.utc) - timedelta(days=2)).date().isoformat()
    for river, sites in grouped.items():
        codes = list(sites)
        for start in range(0, len(codes), 4):
            batch = codes[start:start + 4]
            query = query_url(HUB_BASE + 'observations_tr', {
                'format': 'json', 'size': 5000, 'sort': 'desc',
                'code_entite': ','.join(batch), 'grandeur_hydro': 'H',
                'date_debut': date_start,
            })
            payload = fetch_json(query, timeout=40)
            grouped_points = {code: [] for code in batch}
            for row in payload.get('data', []):
                code = str(row.get('code_site') or '')
                value = number(row.get('resultat_obs'))
                observed = row.get('date_obs')
                if code not in grouped_points or value is None or not observed:
                    continue
                if stamp(observed) > now + 900 or stamp(observed) < now - 8 * 86400:
                    continue
                # Hub'Eau H readings are mm; convert to cm for a uniform map unit.
                grouped_points[code].append({'t': observed, 'v': value / 10.0})
            for code in batch:
                points = grouped_points[code]
                if not points:
                    continue
                points.sort(key=lambda x: stamp(x['t']))
                history = hourly(points, now)
                if not history:
                    continue
                station = sites[code]
                current = max(points, key=lambda x: stamp(x['t']))
                station.update({
                    'current': current, 'history': history,
                    'items': [{'label': 'Pegelstand', 'value': current['v'], 'unit': 'cm', 'time': current['t']}],
                    'source_url': query_url(HUB_BASE + 'observations_tr', {
                        'format': 'json', 'size': 1, 'code_entite': code,
                        'grandeur_hydro': 'H', 'sort': 'desc'}),
                    'license': 'Licence Ouverte / Open Licence 2.0',
                    'license_url': 'https://www.etalab.gouv.fr/licence-ouverte-open-licence/',
                    'attribution': "Source : Hub'Eau (OFB, Service Central Vigicrues, BRGM)",
                    'measurement_unit_original': 'mm',
                })
                stations.append(station)
            time.sleep(0.15)
    if len(stations) < 12:
        raise RuntimeError(f"Hub'Eau returned only {len(stations)} current French river gauges")
    return stations


def france_temperatures(geojson: dict, now: float):
    """Import only dated, observed Maas/Mosel water temperatures from Hub'Eau.

    The station catalogue includes long-dormant thermometers. Catalogue presence
    alone must never colour a river: stations without a recent reading get an
    explicit unavailable item; the map applies its stricter freshness threshold.
    """
    routes = {river: lines_for(geojson, river) for river in ('Maas', 'Mosel')}
    if not all(routes.values()):
        raise RuntimeError('No mapped line for French Maas or Mosel')
    catalogue = fetch_json(query_url(HUB_TEMPERATURE + 'station', {
        'format': 'json', 'bbox': '5.0,47.7,7.8,50.2', 'size': 1000,
    }))
    if not isinstance(catalogue.get('data'), list):
        raise RuntimeError('Hub\'Eau temperature catalogue missing')
    candidates = {}
    for row in catalogue['data']:
        label = str(row.get('libelle_cours_eau') or '').strip().casefold()
        river = {'la meuse': 'Maas', 'la moselle': 'Mosel'}.get(label)
        code = str(row.get('code_station') or '').strip()
        lon, lat = number(row.get('longitude')), number(row.get('latitude'))
        if not river or not code or lon is None or lat is None:
            continue
        if distance_km(lon, lat, routes[river]) > 1.5:
            continue
        candidates[code] = {
            'id': 'fr-hubeau-temp-' + code,
            'name': row.get('libelle_station') or code,
            'river': river, 'country': 'FR', 'src': 'fr-hubeau-temperature',
            'lat': lat, 'lon': lon, 'params': {'wt': True},
            'source_url': query_url(HUB_TEMPERATURE + 'chronique', {
                'format': 'json', 'code_station': code, 'size': 1,
            }),
            'license': 'Licence Ouverte / Open Licence 2.0',
            'license_url': 'https://www.etalab.gouv.fr/licence-ouverte-open-licence/',
            'attribution': "Source : Hub'Eau / Naïades (OFB, BRGM)",
        }
    if not candidates:
        raise RuntimeError('No mapped Hub\'Eau Maas/Mosel temperature stations')
    begin = datetime.fromtimestamp(now - 8 * 86400, timezone.utc).date().isoformat()
    readings = fetch_json(query_url(HUB_TEMPERATURE + 'chronique', {
        'format': 'json', 'code_station': ','.join(sorted(candidates)),
        'date_debut_mesure': begin, 'size': 5000,
    }), timeout=45)
    if not isinstance(readings.get('data'), list):
        raise RuntimeError('Hub\'Eau temperature observations missing')
    if readings.get('next'):
        raise RuntimeError('Hub\'Eau temperature observations exceeded one page')
    by_code = {code: [] for code in candidates}
    for row in readings['data']:
        code = str(row.get('code_station') or '').strip()
        value = number(row.get('resultat'))
        if code not in by_code or value is None or not -2 <= value <= 40:
            continue
        if row.get('symbole_unite') != '°C':
            continue
        try:
            local = datetime.fromisoformat(
                str(row['date_mesure_temp']) + 'T' + str(row['heure_mesure_temp'])
            ).replace(tzinfo=ZoneInfo('Europe/Paris'))
        except (KeyError, TypeError, ValueError):
            continue
        observed = local.isoformat()
        if not now - 8 * 86400 <= stamp(observed) <= now + 900:
            continue
        by_code[code].append({'t': observed, 'v': value})
    stations = []
    for code, points in by_code.items():
        history = hourly(points, now)
        if not history:
            stations.append({**candidates[code],
                             'items': [{'label': 'Wassertemperatur', 'value': None,
                                        'unit': '°C', 'time': None,
                                        'quality': 'unavailable'}],
                             'history': {}})
            continue
        current = history[-1]
        stations.append({**candidates[code],
                         'items': [{'label': 'Wassertemperatur', 'value': current['v'],
                                    'unit': '°C', 'time': current['t']}],
                         'history': {'Wassertemperatur': history}})
    return stations, len(candidates)


def arpa_levels(geojson: dict, now: float):
    # ARPA's map is a no-column visualization. Its public backing table is nf78-nj6b.
    meta = fetch_json(query_url(ARPA_META, {
        '$where': "tipologia = 'Livello Idrometrico'", '$limit': 1000}), timeout=35)
    stations = {}
    route = lines_for(geojson, 'Ticino')
    for s in meta:
        lon, lat = number(s.get('lng')), number(s.get('lat'))
        sid = str(s.get('idsensore') or '').strip()
        if not sid or lon is None or lat is None or distance_km(lon, lat, route) > 1.0:
            continue
        stations[sid] = {
            'id': 'it-arpa-' + sid, 'name': s.get('nomestazione') or f'Ticino {sid}',
            'river': 'Ticino', 'country': 'IT', 'src': 'it-arpa-lombardia',
            'source': 'it-arpa-lombardia', 'lat': lat, 'lon': lon, 'unit': 'cm',
            'arpa_sensor_id': sid, 'station_elevation_m': number(s.get('quota')),
        }
    if len(stations) < 3:
        raise RuntimeError(f'ARPA Lombardia returned only {len(stations)} Ticino level sensors')
    ids = list(stations)
    start = (datetime.now(timezone.utc) - timedelta(days=ARPA_LOOKBACK_DAYS)).replace(
        hour=0, minute=0, second=0, microsecond=0).strftime('%Y-%m-%dT%H:%M:%S.000')
    where = "idsensore in (" + ','.join("'" + sid + "'" for sid in ids) + ") AND data >= '" + start + "'"
    payload = fetch_json(query_url(ARPA_DATA, {
        '$where': where, '$limit': 10000, '$order': 'data DESC'}), timeout=45)
    by_id = {sid: [] for sid in ids}
    for row in payload:
        sid = str(row.get('idsensore') or '')
        value = number(row.get('valore'))
        # VA/VV are valid; no-data, uncertain, and invalid values are not measurements.
        if sid not in by_id or value is None or row.get('stato') not in ('VA', 'VV'):
            continue
        # ARPA labels timestamps as standard/solar time (UTC+01), year-round.
        raw_time = str(row.get('data') or '').replace(' ', 'T')
        if raw_time.endswith('Z'):
            observed = raw_time
        else:
            observed = raw_time[:19] + '+01:00'
        if stamp(observed) > now + 900 or stamp(observed) < now - (ARPA_LOOKBACK_DAYS + 1) * 86400:
            continue
        by_id[sid].append({'t': observed, 'v': value})
    result = []
    for sid, meta_row in stations.items():
        points = by_id[sid]
        if not points:
            continue
        points.sort(key=lambda x: stamp(x['t']))
        history = hourly(points, now)
        latest = max(points, key=lambda x: stamp(x['t']))
        meta_row.update({
            'current': latest, 'history': history,
            'items': [{'label': 'Pegelstand', 'value': latest['v'], 'unit': 'cm', 'time': latest['t']}],
            'license': 'CC0-1.0', 'license_url': 'https://creativecommons.org/publicdomain/zero/1.0/',
            'attribution': 'ARPA Lombardia',
            'source_url': 'https://www.dati.lombardia.it/resource/647i-nhxk.json',
            'data_note': 'Rohwert in cm; die Behörde veröffentlicht für diese Station keinen Referenz-Mittelwasserstand.',
        })
        result.append(meta_row)
    if len(result) < 3:
        raise RuntimeError(f'ARPA Lombardia returned only {len(result)} current Ticino readings')
    return result


def main():
    now = datetime.now(timezone.utc).timestamp()
    previous = read_json(OUT, {})
    old_levels = previous.get('level_stations', [])
    old_temperatures = previous.get('temperature_stations', [])
    sources = {}
    errors = {}
    geojson = read_json(GEOMETRY, {})
    try:
        sources['at-ktn'] = ktn_levels(geojson, now)
        print('Kärnten Drau Pegel:', len(sources['at-ktn']))
    except Exception as exc:
        errors['at-ktn'] = str(exc)[:240]
        sources['at-ktn'] = [s for s in old_levels if s.get('src') == 'at-ktn']
    try:
        catalogue = (read_json(ROOT / 'assets/data/europe-level.json', {})
                     or read_json(ROOT / 'europe-level.json', {})).get('stations', [])
        sources['at-stmk'] = steiermark_levels(geojson, now, catalogue)
        print('Steiermark Mur Pegel:', len(sources['at-stmk']))
    except Exception as exc:
        errors['at-stmk'] = str(exc)[:240]
        sources['at-stmk'] = [s for s in old_levels if s.get('src') == 'at-stmk']
    try:
        sources['fr-hubeau'] = france_levels(geojson, now)
        print('Hub’Eau Maas/Mosel/Rhône Pegel:', len(sources['fr-hubeau']))
    except Exception as exc:
        errors['fr-hubeau'] = str(exc)[:240]
        sources['fr-hubeau'] = [s for s in old_levels if s.get('src') == 'fr-hubeau']
    try:
        french_temperatures, candidate_count = france_temperatures(geojson, now)
        live_count = sum(s['items'][0]['value'] is not None for s in french_temperatures)
        print('Hub’Eau Maas/Mosel Temperatur:', live_count,
              'mit Messwert von', candidate_count, 'registrierten Stationen')
    except Exception as exc:
        errors['fr-hubeau-temperature'] = str(exc)[:240]
        french_temperatures = [s for s in old_temperatures
                               if s.get('src') == 'fr-hubeau-temperature']
        candidate_count = None
        live_count = sum(s['items'][0]['value'] is not None for s in french_temperatures)
    try:
        sources['it-arpa-lombardia'] = arpa_levels(geojson, now)
        print('ARPA Lombardia Ticino-Pegel:', len(sources['it-arpa-lombardia']))
    except Exception as exc:
        errors['it-arpa-lombardia'] = str(exc)[:240]
        sources['it-arpa-lombardia'] = [s for s in old_levels if s.get('src') == 'it-arpa-lombardia']

    # A failed service never erases its last successful readings.
    merged = {str(s.get('id')): s for group in sources.values() for s in group if s.get('id')}
    if len(merged) < 20:
        raise RuntimeError(f'Too few usable gauge stations ({len(merged)}); retaining previous file')
    report = {
        'updated': datetime.now(timezone.utc).isoformat(),
        'level_stations': list(merged.values()),
        'temperature_stations': [s for s in old_temperatures
                                 if s.get('src') != 'fr-hubeau-temperature'] + french_temperatures,
        'source_report': {
            'at-ktn': {'count': len(sources['at-ktn']), 'url': KTN_URL},
            'at-stmk': {'count': len(sources['at-stmk']), 'url': STMK_BASE},
            'fr-hubeau': {'count': len(sources['fr-hubeau']), 'base_url': HUB_BASE},
            'fr-hubeau-temperature': {'count': len(french_temperatures),
                                      'live_count': live_count,
                                      'candidate_count': candidate_count,
                                      'base_url': HUB_TEMPERATURE},
            'it-arpa-lombardia': {'count': len(sources['it-arpa-lombardia']), 'dataset': ARPA_DATA},
        },
        'errors': errors,
        'note': 'Nur gemessene Pegel und Wassertemperaturen. Keine Temperatur oder Normalwerte aus anderen Größen abgeleitet.',
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    temp = OUT.with_suffix('.tmp')
    temp.write_text(json.dumps(report, ensure_ascii=False, separators=(',', ':')) + '\n', encoding='utf-8')
    temp.replace(OUT)
    if errors:
        print('::warning::Ein oder mehrere Sensor-Feeds wurden nicht aktualisiert: ' + json.dumps(errors, ensure_ascii=False))


if __name__ == '__main__':
    main()
