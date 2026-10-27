"""OVF open hydrology data, using the same public API as data.vizugy.hu.

Free reuse with attribution: https://www.ovf.hu/hireink/ovf-hirek/az-adat-mindenkie
The anonymous short-lived token is requested normally, never stored. Parameter
89 is bottom-water temperature, 85 surface temperature; never mix their series.
"""
import json
import math
import urllib.request
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

SOURCE = 'https://data.vizugy.hu/'
LICENSE_URL = 'https://www.ovf.hu/hireink/ovf-hirek/az-adat-mindenkie'
BASE = 'https://vmservice.vizugy.hu/vraquery/'


def request(url, body=None, token=None):
    headers = {'User-Agent': 'Mozilla/5.0 (compatible; PetriKlar/1.0)',
               'Origin': SOURCE.rstrip('/'), 'Referer': SOURCE,
               'Accept': 'application/json', 'Content-Type': 'application/json'}
    if token:
        headers['Authorization'] = 'Bearer ' + token
    req = urllib.request.Request(url, headers=headers,
        data=json.dumps(body).encode() if body is not None else None)
    with urllib.request.urlopen(req, timeout=30) as response:
        raw = response.read(16_000_001)
    if len(raw) > 16_000_000:
        raise ValueError('OVF response exceeds size limit')
    return json.loads(raw)


def points(raw, now, temperature=False):
    result = {}
    for p in raw:
        try:
            # Missing values are not zero. UTC time survives the DST transition.
            v = float(p['Adat'])
            t = datetime.fromisoformat(p['UTCTime'].replace('Z', '+00:00'))
            if not t.tzinfo or not math.isfinite(v) or not now-timedelta(days=32) <= t <= now+timedelta(minutes=15):
                continue
            if not (-2 <= v <= 35 if temperature else -1500 <= v <= 2500):
                continue
            result[t.isoformat()] = {'t': t.isoformat(), 'v': v}
        except (KeyError, ValueError, TypeError):
            continue
    return sorted(result.values(), key=lambda p: p['t'])


def parse(stations, responses, now=None):
    now = now or datetime.now(timezone.utc)
    series = {code: {s['ItemId']: s.get('TsItemList', []) for s in rows}
              for code, rows in responses.items()}
    rows = []
    for s in stations:
        river = s.get('MdrNev', '').strip().casefold()
        if river not in ('duna', 'mura'):
            continue  # tributaries / Mosoni-Duna are not the drawn main Danube
        try:
            lat, lon = float(s['Lat']), float(s['Lon'])
            if not math.isfinite(lat) or not math.isfinite(lon) or not (-90 <= lat <= 90 and -180 <= lon <= 180):
                continue
        except (KeyError, TypeError, ValueError):
            continue
        sid = s['Tsz']
        level = points(series.get(68, {}).get(sid, []), now)
        # Consistent continuous sensor horizon, surface only when no bottom
        # observation is available in the current 48-hour window.
        temp, horizon, code = [], None, None
        for parameter, label in [(89, 'near river bed'), (85, 'near water surface')]:
            candidate = points(series.get(parameter, {}).get(sid, []), now, True)
            if candidate and now-datetime.fromisoformat(candidate[-1]['t']) <= timedelta(hours=48):
                temp, horizon, code = candidate, label, parameter
                break
        history, items = {}, []
        for label, unit, values in [('Pegelstand', 'cm', level), ('Wassertemperatur', '°C', temp)]:
            if not values:
                continue
            history[label] = values
            items.append(dict(label=label, unit=unit, value=values[-1]['v'], time=values[-1]['t']))
        if not items:
            continue
        rows.append(dict(id='hu-ovf-'+str(sid), name=s['Nev'], river='Mur' if river=='mura' else 'Donau',
            lat=lat, lon=lon, country='HU', src='hu-ovf',
            source_station=str(sid), source_url=SOURCE, attribution='Országos Vízügyi Főigazgatóság (OVF)',
            license='Free reuse with OVF attribution', license_url=LICENSE_URL,
            sampling_horizon=horizon, temperature_parameter=code,
            data_note='OVF operative observations (not final quality controlled). Temperature: '+str(horizon or 'not available')+'. Gauge height, not water depth.',
            level_datum_m=s.get('Npt'), river_km=s.get('Fkm'),
            updated=max(i['time'] for i in items), unit='cm', items=items, history=history,
            params={'pegel': bool(level), 'wt': bool(temp)}))
    return rows


def collect():
    token = request(SOURCE+'AuthApi/auth/token')['access_token']
    stations = request(BASE+'Vra/InternetVmo/11/false', token=token)
    selected = [s for s in stations if s.get('MdrNev', '').strip().casefold() in ('duna', 'mura')]
    if not selected:
        raise ValueError('OVF Danube station catalogue empty')
    now = datetime.now(timezone.utc)
    local = now.astimezone(ZoneInfo('Europe/Budapest'))
    responses = {}
    for code in (68, 89, 85):
        body = dict(torzsszamList=[s['Tsz'] for s in selected], adatFajtaKod=code,
            adatTipusKod=100, startTime=(local-timedelta(days=8)).strftime('%Y-%m-%d %H:%M:%S'),
            endTime=local.strftime('%Y-%m-%d %H:%M:%S'), dataExtFilter=0,
            valueFilter='Relativ', amKodFilter=[0])
        responses[code] = request(BASE+'TS/TsShortList', body, token)
    return parse(selected, responses, now)
