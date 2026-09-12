"""Official CH/NL surface-water inventories and current observations.

No credentials or browser scraping. CH: BAFU Open-Use; NL: WaterWebservices CC0.
Native water-level datum is preserved; missing normal levels are never invented.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
from collections import defaultdict
import csv
from datetime import datetime, timezone, timedelta
import gzip
import hashlib
import json
import math
import time
import queue
import threading
from pathlib import Path
import re
from urllib.parse import quote, unquote
from urllib.request import Request, urlopen

CH_GRAPH = 'https://lindas.admin.ch/foen/hydro'
CH_QUERY = 'https://lindas.admin.ch/query?query=' + quote(
    'SELECT ?s ?p ?o WHERE { GRAPH <' + CH_GRAPH + '> { ?s ?p ?o } }')
NL_BASE = 'https://ddapi20-waterwebservices.rijkswaterstaat.nl/'
NL_HISTORY_TARGETS = {
    'amerongen.beneden','amerongen.boven','arnhem.nederrijn','belfeld.boven','culemborg','dalem',
    'desteeg','deventer','doesburg.ijssel','driel.beneden','driel.boven','eijsden.grens',
    'eisdenmazenhove.maas','eisdenmazenhove.maesbempdergreend','elsloo.maas','gennep',
    'grave.beneden','grave.boven','hagestein.beneden','hagestein.boven','kampen.ijssel',
    'kampen.keteldiep','krimpenaandelek.lek','lanaken','linne.stuw.beneden','lith.beneden',
    'lith.boven','lith.sluis','lixhebiefaval','lobith.bovenrijn.haven',
    'lobith.bovenrijn.tolkamer','maaseik','maastricht.borgharen.maas.beneden',
    'maastricht.sintpieter','maastricht.sintpieter.zuid','meeswijk','megen.maas',
    'millingenaanderijn.pannerdensekop','moerdijk','mook','neer','negenoord.oost','negenoord.west',
    'olst','rhenen.grebbeberg','roermond.boven','rotem','sambeek.beneden','sambeek.boven',
    'schoonhoven','smeermaas.zuidwillemsvaart','spaanjerd','stevensweert','steyl','uikhoven',
    'veessen','venlo','well','westervoort.hondsbroekschepleij.ijssel','westervoort.ijsselkop',
    'wijhe','zaltbommel','zutphen.ijssel'
}
LICENSES = {
    'bafu': {'country': 'CH', 'license': 'Open-Use',
             'license_url': 'https://ld.admin.ch/vocabulary/TermsOfUse/Open-Use',
             'license_evidence': 'https://environment.ld.admin.ch/.well-known/void/dataset/hydro',
             'attribution': 'Bundesamt für Umwelt BAFU, Abteilung Hydrologie'},
    'rijkswaterstaat': {'country': 'NL', 'license': 'CC0-1.0',
             'license_url': 'https://creativecommons.org/publicdomain/zero/1.0/',
             'license_evidence': 'https://rijkswaterstaatdata.nl/waterdata/',
             'attribution': 'Rijkswaterstaat'},
}


def read_json(url, body=None):
    if not url.startswith(NL_BASE):return _read_json(url,body)
    result=queue.Queue(maxsize=1)
    def request():
        try:result.put((True,_read_json(url,body)))
        except Exception as error:result.put((False,error))
    # Also bound DNS/TLS/header stalls, which socket read limits do not cover.
    threading.Thread(target=request,daemon=True).start()
    try:success,value=result.get(timeout=45)
    except queue.Empty:raise TimeoutError('RWS request exceeded 45 seconds') from None
    if not success:raise value
    return value


def _read_json(url, body=None):
    request = Request(url, data=None if body is None else json.dumps(body).encode(),
        headers={'User-Agent': 'PetriKlar/1.0 (+https://www.petriklar.com)',
                 'Accept': 'application/sparql-results+json,application/json',
                 'Content-Type': 'application/json'})
    started=time.monotonic()
    with urlopen(request, timeout=20 if url.startswith(NL_BASE) else 40) as response:
        if url.startswith(NL_BASE):
            # A socket timeout alone restarts whenever bytes arrive. Bound
            # slowly trickling RWS responses as well as silent connections.
            chunks=[]
            size=0
            while size<=32_000_000:
                if time.monotonic()-started>40:raise TimeoutError('RWS response exceeded 40-second budget')
                chunk=response.read1(min(65536,32_000_001-size))
                if not chunk:break
                chunks.append(chunk);size+=len(chunk)
            raw=b''.join(chunks)
        else:
            raw = response.read(32_000_001)
    if len(raw) > 32_000_000:
        raise ValueError('Response exceeds size limit')
    return json.loads(raw) if raw else {}


def number(value):
    if value is None or isinstance(value, bool):
        return None
    try:
        result = float(str(value).replace(',','.'))
        return result if math.isfinite(result) and abs(result) < 1e7 else None
    except (ValueError, TypeError):
        return None


def recent_time(value, now):
    try:
        dt = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
        return dt.tzinfo is not None and now-timedelta(days=9) <= dt <= now+timedelta(minutes=15)
    except ValueError:
        return False


def parse_ch(payload, now):
    triples = defaultdict(dict)
    for triple in payload['results']['bindings']:
        triples[triple['s']['value']][triple['p']['value']] = triple['o']['value']
    schema, geo = 'http://schema.org/', 'http://www.opengis.net/ont/geosparql#'
    dim = 'https://environment.ld.admin.ch/foen/hydro/dimension/'
    observations = {row[dim+'station']: row for row in triples.values() if dim+'station' in row}
    stations = []
    for uri, meta in triples.items():
        if schema+'identifier' not in meta or schema+'containedInPlace' not in meta:
            continue
        geometry = triples.get(meta.get(geo+'hasGeometry'), {}).get(geo+'asWKT', '')
        match = re.search(r'POINT\s*\(\s*([-\d.]+)\s+([-\d.]+)', geometry)
        if not match:
            continue
        lon, lat = map(float, match.groups())
        obs = observations.get(uri, {})
        stamp = obs.get(dim+'measurementTime')
        waterbody = meta[schema+'containedInPlace']
        river = unquote(waterbody.rsplit('/', 1)[-1])
        sid = meta[schema+'identifier']
        station = dict(id='bafu-'+sid, name=meta.get(schema+'name', sid),
            river=river, lat=lat, lon=lon, src='bafu', items=[], history={},
            source_url='https://www.hydrodaten.admin.ch/de/seen-und-fluesse/stationen-und-daten/'+sid,
            level_datum='LN02', **LICENSES['bafu'])
        for key, label, unit in [('waterTemperature','Wassertemperatur','°C'),
                                  ('waterLevel','Pegelstand','m'), ('discharge','Durchfluss','m³/s')]:
            value = number(obs.get(dim+key))
            if value is None or not recent_time(stamp, now):
                continue
            if key == 'waterTemperature' and not -2 <= value <= 40:
                continue
            station['items'].append(dict(label=label, value=value, unit=unit, time=stamp))
            station['history'][label] = [dict(t=stamp, v=value)]
        stations.append(station)
    return stations


def ch_history(rows, now):
    def load(station):
        sid=station['id'].removeprefix('bafu-')
        labels={item['label'] for item in station['items']}
        feeds=[]
        if 'Wassertemperatur' in labels:feeds.append('temperature_7days')
        if 'Pegelstand' in labels:feeds.append('p_q_7days')
        for feed in feeds:
            try:
                payload=read_json(f'https://www.hydrodaten.admin.ch/plots/{feed}/{sid}_{feed}_de.json')
                for series in payload.get('plot',{}).get('data',[]):
                    label={'Temperatur':'Wassertemperatur','Wasserstand':'Pegelstand','Abfluss':'Durchfluss'}.get(series.get('name'))
                    if not label:continue
                    points=[{'t':t,'v':number(v)} for t,v in zip(series.get('x',[]),series.get('y',[])) if number(v) is not None and recent_time(t,now)]
                    if points:station['history'][label]=points
            except Exception as error:
                station.setdefault('history_errors',[]).append(str(error))
        return station
    with ThreadPoolExecutor(max_workers=4) as pool:
        return list(pool.map(load,rows))


def ch_daily_level_history(rows, now):
    """Backfill official daily means from BAFU's prepared monthly Open Data files."""
    targets = {s['id']:s for s in rows if s.get('src') == 'bafu'
               and any(i.get('label') == 'Pegelstand' for i in s.get('items',[]))}
    if not targets:
        return {'requested_windows':0,'points':0}
    cursor = (now-timedelta(days=366)).date().replace(day=1)
    added = 0
    windows = 0
    errors = []
    current_month=now.date().replace(day=1)
    while cursor <= current_month:
        name=f'water_observations_{cursor.year}_data_1day_mean_{cursor.year}{cursor.month:02d}.csv.gz'
        url=f'https://data.bafu.admin.ch/download/water/observations/live/data/{cursor.year}/{name}'
        try:
            request=Request(url,headers={'User-Agent':'PetriKlar/1.0 (+https://www.petriklar.com)'})
            with urlopen(request,timeout=40) as response:raw=response.read(8_000_001)
            if len(raw)>8_000_000:raise ValueError('BAFU daily file exceeds size limit')
            for point in csv.DictReader(gzip.decompress(raw).decode('utf8').splitlines()):
                station=targets.get('bafu-'+point.get('station_no',''))
                value=number(point.get('value'))
                if station is None or point.get('parameter_name')!='W' or value is None \
                        or point.get('unit_symbol') not in ('m ü.M.','m ü. M.','m'):
                    continue
                stamp=datetime.fromisoformat(point['timestamp']).replace(tzinfo=timezone.utc)
                if not 0 <= (now-stamp).total_seconds()/86400 <= 366:continue
                station.setdefault('history',{}).setdefault('Pegelstand',[]).append(
                    {'t':stamp.isoformat(),'v':value})
                added += 1
        except Exception as error:
            errors.append({'file':name,'error':str(error)})
        windows += 1
        cursor=(cursor.replace(day=28)+timedelta(days=4)).replace(day=1)
    if not errors:
        checked = now.isoformat()
        for station in targets.values():
            station['history_checked_at'] = checked
            station['history_source'] = 'BAFU Open Data platform, official daily means'
    return {'requested_months':windows,'points':added,'errors':errors}


def nl_identity(loc, meta, flags):
    return (loc['Code'], meta.get('Grootheid',{}).get('Code'), flags.get('Bemonsteringshoogte'),
            flags.get('Referentievlak'), meta.get('Hoedanigheid',{}).get('Code'))


def nl_daily_level_history(payload, now):
    """Reduce RWS ten-minute measurements locally to one median per UTC day and series."""
    from statistics import median
    days = defaultdict(lambda:defaultdict(list))
    metadata = {}
    if payload.get('Succesvol') is not True:
        raise ValueError('RWS historical observations unsuccessful: '+str(payload.get('Foutmelding','no details')))
    for series in payload.get('WaarnemingenLijst',[]):
        meta,loc=series.get('AquoMetadata',{}),series.get('Locatie',{})
        if meta.get('Grootheid',{}).get('Code') != 'WATHTE' or meta.get('ProcesType') != 'meting':
            continue
        for point in series.get('MetingenLijst',[]):
            flags=point.get('WaarnemingMetadata',{})
            value=number(point.get('Meetwaarde',{}).get('Waarde_Numeriek'))
            try:stamp=datetime.fromisoformat(point['Tijdstip'])
            except (ValueError,KeyError):continue
            if value is None or stamp.tzinfo is None or str(flags.get('Kwaliteitswaardecode')) not in ('00','10','20','30','40'):
                continue
            # Instrument or series identifiers may change. Water levels remain comparable
            # only when location, native unit and official datum are identical.
            key=(loc['Code'],meta.get('Eenheid',{}).get('Code'),meta.get('Hoedanigheid',{}).get('Code'))
            days[key][stamp.astimezone(timezone.utc).date()].append(value)
            metadata[key]={'unit':key[1],'datum':key[2],'source_station':key[0]}
    return {key:{'points':[{'t':datetime.combine(day,datetime.min.time(),timezone.utc).isoformat(),'v':median(values)}
                           for day,values in sorted(group.items())],**metadata[key]}
            for key,group in days.items()}


def nl_backfill_locations(rows, now, budget=2):
    """Backfill bounded 28-day windows; persist progress and isolate station failures."""
    by_location=defaultdict(list)
    for row in rows:
        if row.get('src')=='rijkswaterstaat' and row.get('source_station') in NL_HISTORY_TARGETS \
                and any(i.get('label')=='Pegelstand' for i in row.get('items',[])):
            by_location[row['source_station']].append(row)
    stale=[]
    for location,group in by_location.items():
        if any(history_baseline(s.get('history',{}).get('Pegelstand',[]),
                                next(i['unit'] for i in s['items'] if i['label']=='Pegelstand'),now) for s in group):
            continue
        checked=max((s.get('history_checked_at','') for s in group
                     if s.get('history_source')=='Rijkswaterstaat WaterWebservices v2, same location/unit/NAP'),default='')
        try:is_recent=now-datetime.fromisoformat(checked)<timedelta(days=30)
        except ValueError:is_recent=False
        if not is_recent:stale.append(location)
    completed=[]
    errors=[]
    requests=0
    deadline=time.monotonic()+150
    for location in sorted(stale,key=lambda loc:max((s.get('nl_history_attempt_at','') for s in by_location[loc]),default=''))[:budget]:
        parsed={}
        group=by_location[location]
        saved=max((s.get('nl_history_cursor','') for s in group),default='')
        cursor=datetime.fromisoformat(saved) if saved else now-timedelta(days=366)
        while cursor<now and requests<budget and time.monotonic()<deadline:
            end=min(cursor+timedelta(days=28),now)
            body={'Locatie':{'Code':location},'AquoPlusWaarnemingMetadata':{
                'AquoMetadata':{'Compartiment':{'Code':'OW'},'Grootheid':{'Code':'WATHTE'},'ProcesType':'meting'}},
                'Periode':{'Begindatumtijd':cursor.strftime('%Y-%m-%dT%H:%M:%S+00:00'),
                           'Einddatumtijd':end.strftime('%Y-%m-%dT%H:%M:%S+00:00')}}
            requests+=1
            for row in group:row['nl_history_attempt_at']=now.isoformat()
            try:
                part=nl_daily_level_history(read_json(
                    NL_BASE+'ONLINEWAARNEMINGENSERVICES/OphalenWaarnemingen',body),now)
            except Exception as error:
                errors.append({'location':location,'error':str(error),'from':cursor.isoformat()})
                break
            for key,series in part.items():
                parsed.setdefault(key,{**series,'points':[]})['points'].extend(series['points'])
            cursor=end
            for row in group:row['nl_history_cursor']=cursor.isoformat()
        checked=now.isoformat()
        for row in by_location[location]:
            unit=next(i['unit'] for i in row['items'] if i['label']=='Pegelstand')
            key=(location,unit,row.get('level_datum'))
            if key in parsed:
                row.setdefault('history',{}).setdefault('Pegelstand',[]).extend(parsed[key]['points'])
            if cursor>=now:
                row['history_checked_at']=checked
                row['history_source']='Rijkswaterstaat WaterWebservices v2, same location/unit/NAP'
        if cursor>=now:completed.append(location)
    return {'eligible_locations':len(by_location),'backfilled_locations':completed,'remaining':max(0,len(stale)-len(completed)), 'requests':requests,'errors':errors}


def nl_locations(catalog):
    if catalog.get('Succesvol') is not True:
        raise ValueError('RWS catalogue unsuccessful')
    metadata = {m['AquoMetadata_MessageID'] for m in catalog['AquoMetadataLijst']
                if m.get('Compartiment', {}).get('Code') == 'OW'
                and m.get('Grootheid', {}).get('Code') in ('T', 'WATHTE')
                and m.get('ProcesType') == 'meting'}
    ids = {m['Locatie_MessageID'] for m in catalog['AquoMetadataLocatieLijst']
           if m['AquoMetaData_MessageID'] in metadata}
    return [s for s in catalog['LocatieLijst'] if s['Locatie_MessageID'] in ids
            and s.get('Coordinatenstelsel') == 'ETRS89'
            and number(s.get('Lat')) is not None and number(s.get('Lon')) is not None]


def parse_nl(payload, now):
    if payload.get('Succesvol') is not True:
        raise ValueError('RWS observations unsuccessful')
    rows = {}
    for series in payload.get('WaarnemingenLijst', []):
        meta, loc = series.get('AquoMetadata', {}), series.get('Locatie', {})
        kind = meta.get('Grootheid', {}).get('Code')
        unit = meta.get('Eenheid', {}).get('Code')
        if (meta.get('Compartiment', {}).get('Code') != 'OW' or meta.get('ProcesType') != 'meting'
                or loc.get('Coordinatenstelsel') != 'ETRS89' or kind not in ('T', 'WATHTE')):
            continue
        if (kind == 'T' and unit != 'oC') or (kind == 'WATHTE' and unit not in ('cm', 'm')):
            continue
        for point in series.get('MetingenLijst', []):
            flags = point.get('WaarnemingMetadata', {})
            value = number(point.get('Meetwaarde', {}).get('Waarde_Numeriek'))
            stamp = point.get('Tijdstip')
            if value is None or str(flags.get('Kwaliteitswaardecode')) not in ('00','10','20','30','40'):
                continue
            if not recent_time(stamp, now) or (kind == 'T' and not -2 <= value <= 40):
                continue
            # Preserve separate measurement horizons and water-level datums.
            identity = nl_identity(loc, meta, flags)
            code = hashlib.sha256(json.dumps(identity).encode()).hexdigest()[:12]
            label = 'Wassertemperatur' if kind == 'T' else 'Pegelstand'
            row = rows.setdefault(identity, dict(id='rws-'+loc['Code']+'-'+code,
                source_station=loc['Code'], name=loc.get('Naam',loc['Code']), river='',
                lat=loc['Lat'], lon=loc['Lon'], src='rijkswaterstaat',
                source_url='https://waterinfo.rws.nl/', items=[], history={},
                sampling_height=flags.get('Bemonsteringshoogte'),
                sampling_reference=flags.get('Referentievlak'),
                level_datum=meta.get('Hoedanigheid', {}).get('Code'), **LICENSES['rijkswaterstaat']))
            old = row['items'][0] if row['items'] else None
            if old is None or datetime.fromisoformat(stamp) > datetime.fromisoformat(old['time']):
                row['items'] = [dict(label=label,value=value,unit='°C' if kind=='T' else unit,time=stamp)]
                row['history'] = {label: [dict(t=stamp,v=value)]}
    return list(rows.values())


def collect_nl(now, catalog=None):
    started=time.monotonic()
    catalog = catalog or read_json(NL_BASE+'METADATASERVICES/OphalenCatalogus',
        {'CatalogusFilter': {'Compartimenten':True,'Grootheden':True,'Eenheden':True,'ProcesTypes':True}})
    locations = nl_locations(catalog)
    rows, errors = [], []
    deadline=time.monotonic()+180
    def batch(selection):
        if time.monotonic()>=deadline:
            errors.append({'locations':[s['Code'] for s in selection],'error':'NL time budget exhausted; retry next run'})
            return
        body = {'LocatieLijst':[{'Code':s['Code']} for s in selection],
                'AquoPlusWaarnemingMetadataLijst': [
                    {'AquoMetadata':{'Compartiment':{'Code':'OW'},'Grootheid':{'Code':kind},'ProcesType':'meting'}}
                    for kind in ('T','WATHTE')]}
        try:
            rows.extend(parse_nl(read_json(NL_BASE+'ONLINEWAARNEMINGENSERVICES/OphalenLaatsteWaarnemingen',body),now))
        except Exception as error:
            # Splitting a timed-out request multiplies the outage by up to 79
            # requests per batch. Only split rejected/oversized payloads.
            if isinstance(error,ValueError) and len(selection)>1:
                middle=len(selection)//2
                batch(selection[:middle]);batch(selection[middle:])
            else:
                errors.append({'locations':[s['Code'] for s in selection],'error':str(error)})
    # Rotate the starting group so a slow service cannot permanently starve
    # locations at the end of the catalogue when the time budget expires.
    if locations:
        offset=(int(now.timestamp()//3600)%((len(locations)+39)//40))*40
        locations=locations[offset:]+locations[:offset]
    for start in range(0, len(locations), 40):
        batch(locations[start:start+40])
        print(f'RWS: {min(start+40,len(locations))}/{len(locations)} catalogue locations checked', flush=True)
    return rows, {'catalogue_locations':len(locations),'stations':len(rows),
                  'elapsed_seconds':round(time.monotonic()-started,1),'failed_batches':errors}


def collect_at():
    import austria_sources as at
    # Verify national feed licence from its own metadata on each collection run.
    metadata=read_json(at.BMLUK_PEGEL_URL.split('/items?')[0]+'?f=json')
    if not any(link.get('rel')=='license' and link.get('href')=='https://creativecommons.org/licenses/by/4.0/' for link in metadata.get('links',[])):
        raise ValueError('BMLUK licence could not be confirmed')
    rows=at.process_bmluk_current()
    errors=[]
    for name,adapter in [('Oberösterreich',at.process_ooe_live),('Kärnten',at.process_kaernten_live)]:
        try:
            rows.extend(adapter())
        except Exception as error:
            errors.append({'source':name,'error':str(error)})
    for row in rows:
        row.update(country='AT',license='CC BY 4.0',license_url='https://creativecommons.org/licenses/by/4.0/')
        from zoneinfo import ZoneInfo
        for item in row['items']:
            item['value']=number(item.get('value'))
            try:
                item['time']=datetime.strptime(item['time'],'%d.%m.%Y %H:%M').replace(tzinfo=ZoneInfo('Europe/Vienna')).isoformat()
            except (ValueError,KeyError):
                item['time']=None
    from vorarlberg_sources import collect as collect_vbg
    try:
        vbg_rows, vbg_errors = collect_vbg()
        rows.extend(vbg_rows)
        errors.extend(vbg_errors)
    except Exception as error:
        errors.append({'source':'Vorarlberg','error':str(error)})
    from austria_ogd_csv import collect as collect_ogd
    try:
        ogd_rows, ogd_errors = collect_ogd()
        rows.extend(ogd_rows)
        errors.extend(ogd_errors)
    except Exception as error:
        errors.append({'source':'Tirol/Salzburg OGD','error':str(error)})
    from steiermark_sources import collect as collect_stmk
    try:
        stmk_rows, stmk_report = collect_stmk()
        rows.extend(stmk_rows)
        errors.extend(stmk_report['errors'])
    except Exception as error:
        errors.append({'source':'Steiermark','error':str(error)})
    from austria_public_tables import supplement
    rows, table_errors = supplement(rows, read_json)
    errors.extend(table_errors)
    return rows,{'stations':len(rows),'errors':errors,'unverified_sources_excluded':['Niederösterreich-Kartenfeed']}


def history_baseline(points, unit, now):
    """An empirical annual comparison, deliberately not official MW/MNW/MHW."""
    from statistics import median
    days = {}
    for point in points:
        value = number(point.get('v'))
        try:
            stamp = datetime.fromisoformat(point['t'])
            age = (now-stamp).total_seconds()/86400
        except (ValueError, KeyError, TypeError):
            continue
        if value is not None and 0 <= age <= 365:
            days.setdefault(stamp.astimezone(timezone.utc).date(), []).append(value)
    dates = sorted(days)
    if len(dates) < 300 or (dates[-1]-dates[0]).days < 330:
        return None
    if max((b-a).days for a,b in zip(dates,dates[1:])) > 30:
        return None
    values = sorted(median(days[day]) for day in dates)
    def quantile(p):
        index = (len(values)-1)*p
        lo = int(index)
        return values[lo]+(values[min(lo+1,len(values)-1)]-values[lo])*(index-lo)
    low, center, high = quantile(.1), quantile(.5), quantile(.9)
    if not low < center < high:
        return None
    return dict(method='daily-median-p10-p50-p90', low=low, center=center, high=high,
                unit=unit, start=str(dates[0]), end=str(dates[-1]), days=len(dates))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=Path('europe-water.json'))
    parser.add_argument('--ch-fixture', type=Path)
    parser.add_argument('--nl-catalog', type=Path)
    parser.add_argument('--only', choices=['CH','NL','AT'])
    parser.add_argument('--no-history', action='store_true')
    parser.add_argument('--history-only', action='store_true', help='Use the archive without refreshing live feeds')
    parser.add_argument('--history-budget', type=int, default=2, help='Maximum RWS locations backfilled per run')
    args = parser.parse_args()
    now = datetime.now(timezone.utc)
    archive_path=args.output.with_name('europe-history.json')
    previous_path=archive_path if archive_path.exists() else args.output
    previous=json.loads(previous_path.read_text(encoding='utf8')) if previous_path.exists() else {}
    rows, report = ([{**s,'history':dict(s.get('history',{}))} for s in previous.get('stations',[])],
                    dict(previous.get('source_report',{}))) if args.history_only else ([], {})
    if not args.history_only and args.only in (None,'CH'):
        payload = json.loads(args.ch_fixture.read_text()) if args.ch_fixture else read_json(CH_QUERY)
        ch = parse_ch(payload,now)
        if not args.no_history:ch=ch_history(ch,now)
        rows.extend(ch); report['CH']={'stations':len(ch),'history_failures':sum(bool(s.get('history_errors')) for s in ch)}
    if not args.history_only and args.only in (None,'NL'):
        catalog = json.loads(args.nl_catalog.read_text()) if args.nl_catalog else None
        nl, report['NL'] = collect_nl(now,catalog); rows.extend(nl)
    if not args.history_only and args.only in (None,'AT'):
        at,report['AT']=collect_at();rows.extend(at)
    # Retain real historical measurements using the same one-month/one-year policy.
    from build_sensor_packets import retain
    old={s['id']:s for s in previous.get('stations',[])}
    # Temporary source outages must not erase the accumulated annual archive.
    received={s['id'] for s in rows}
    rows.extend({**s,'history':dict(s.get('history',{})),'not_received_this_run':True}
                for key,s in old.items() if key not in received)
    for row in rows:
        prior=old.get(row['id'],{})
        for key in ('history_checked_at','history_source','nl_history_cursor','nl_history_attempt_at'):
            if prior.get(key) and not row.get(key):row[key]=prior[key]
        for label in old.get(row['id'],{}).get('history',{}):
            row.setdefault('history',{}).setdefault(label,[])
        for item in row.get('items',[]):
            if number(item.get('value')) is not None and recent_time(item.get('time'),now):
                row.setdefault('history',{}).setdefault(item['label'],[]).append({'t':item['time'],'v':number(item['value'])})
        for label,points in row.get('history',{}).items():
            row['history'][label]=retain(old.get(row['id'],{}).get('history',{}).get(label,[])+points,now.timestamp())
    if not args.no_history and args.only in (None,'CH'):
        def needs_ch_history(row):
            if row.get('src')!='bafu':return False
            level=next((i for i in row.get('items',[]) if i['label']=='Pegelstand'),None)
            if not level or history_baseline(row.get('history',{}).get('Pegelstand',[]),level['unit'],now):return False
            try:return now-datetime.fromisoformat(row.get('history_checked_at',''))>=timedelta(days=30)
            except ValueError:return True
        if any(needs_ch_history(row) for row in rows):
            report.setdefault('CH',{})['daily_level_history']=ch_daily_level_history(rows,now)
    if not args.no_history and args.only in (None,'NL'):
        report.setdefault('NL',{})['daily_level_history']=nl_backfill_locations(rows,now,max(0,args.history_budget))
    for row in rows:
        for label,points in row.get('history',{}).items():
            row['history'][label]=retain(points,now.timestamp())
        level = next((i for i in row.get('items',[]) if i['label']=='Pegelstand'), None)
        if level:
            row.pop('history_baseline',None)
            baseline = history_baseline(row.get('history',{}).get('Pegelstand',[]), level['unit'], now)
            if baseline:
                row['history_baseline'] = baseline
    result = {'updated':now.isoformat(),'stations':rows,'source_report':report}
    args.output.parent.mkdir(parents=True,exist_ok=True)
    archive_temp=archive_path.with_suffix('.tmp');archive_temp.write_text(json.dumps(result,ensure_ascii=False),encoding='utf8');archive_temp.replace(archive_path)
    for row in rows:
        for label,points in row.get('history',{}).items():
            row['history'][label]=[p for p in points if recent_time(p['t'],now)]
    temp=args.output.with_suffix('.tmp');temp.write_text(json.dumps(result,ensure_ascii=False),encoding='utf8');temp.replace(args.output)
    for kind,label in [('temperature','Wassertemperatur'),('level','Pegelstand')]:
        selected=[]
        for row in rows:
            items=[i for i in row.get('items',[]) if i['label']==label]
            if items or row.get('history',{}).get(label):
                selected.append({**row,'items':items,'history':{label:row.get('history',{}).get(label,[])}})
        target=args.output.with_name('europe-'+kind+'.json')
        temporary=target.with_suffix('.tmp');temporary.write_text(json.dumps({**result,'stations':selected},ensure_ascii=False,separators=(',',':')),encoding='utf8');temporary.replace(target)
    print(json.dumps(report),flush=True)


if __name__ == '__main__':
    main()
