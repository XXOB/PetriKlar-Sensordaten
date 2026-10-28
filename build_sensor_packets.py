"""Small map payloads and station-specific histories; no network requests."""
import argparse
import hashlib
import json
import math
import re
from datetime import datetime, timezone, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo


def read(path):
    return json.loads(path.read_text(encoding='utf8')) if path.exists() else {}


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary=path.with_suffix('.tmp')
    temporary.write_text(json.dumps(value,ensure_ascii=False,separators=(',',':'))+'\n',encoding='utf8')
    temporary.replace(path)


def numeric(value):
    if value is None or isinstance(value,bool):
        return None
    try:
        parsed=float(str(value).replace(',','.'))
        return parsed if math.isfinite(parsed) else None
    except (TypeError,ValueError):
        return None


def retain(points, now):
    """Keep real observation times: 30 days hourly, remainder of year every 4h."""
    buckets={}
    for point in points:
        try:
            dt=datetime.fromisoformat(str(point['t']).replace('Z','+00:00'))
            if dt.tzinfo is None:
                dt=dt.replace(tzinfo=ZoneInfo('Europe/Berlin'))
            stamp=dt.timestamp()
            value=float(str(point['v']).replace(',','.'))
        except (KeyError,TypeError,ValueError):
            continue
        if not math.isfinite(value) or not now-366*86400<=stamp<=now+900:
            continue
        interval=3600 if stamp>=now-30*86400 else 14400
        bucket=(interval,int(stamp//interval))
        if bucket not in buckets or stamp>buckets[bucket][0]:
            buckets[bucket]=(stamp,{**point,'t':dt.isoformat(),'v':value})
    return [p for _,p in sorted(buckets.values(),key=lambda pair:pair[0])]


def build(root, output):
    water=read(root/'wasserwerte.json')
    extra=read(root/'temperatur_zusatz.json')
    niz=read(root/'niz_temperature.json')
    # Brandenburg publishes current Havel/Spree readings in 31-day charts;
    # its downloadable hourly CSV can lag behind. The updater stores only the
    # explicitly printed latest value, never a value inferred from the graph.
    brandenburg_snapshot=read(root/'assets/data/lfu-brandenburg-current.json')
    # Die Nachbarnetze holen eigene Laeufe (Messwerte AT/CH/NL). Ohne sie
    # fehlen dem Paket die Stationen von Land Tirol, Vorarlberg, BAFU und
    # Rijkswaterstaat, und jede Karte muesste sie im Browser nachladen.
    europe=read(root/'europe-temperature.json') or read(root/'assets/data/europe-temperature.json')
    nl_waters=read(root/'assets/data/nl-gewaesser.json').get('waters',{})
    archive=read(root/'wassertemperatur_verlauf.json')
    levels=read(root/'pegelkarte.json') or read(root/'assets/data/water-levels.json')
    europe_levels=read(root/'europe-level.json') or read(root/'assets/data/europe-level.json')
    river_additions=read(root/'assets/data/river-sensor-additions.json')
    # Mur (ARSO) und Donau unterhalb Oesterreichs (DHMZ, RHMZ, AFDJ, APPD):
    # tools/fetch_extra_sensors.py bzw. fetch_extra_sensors.py im Sensordaten-Repository.
    extra_sensors=read(root/'assets/data/extra-sensors.json')
    now=datetime.now(timezone.utc).timestamp()
    def recent(points):
        # Last real observation in each UTC six-hour bucket; never average
        # or relabel readings. Keep an extra day for the seven-day viewport.
        buckets={}
        for p in points:
            try:
                dt=datetime.fromisoformat(str(p['t']).replace('Z','+00:00'))
                if dt.tzinfo is None: dt=dt.replace(tzinfo=ZoneInfo('Europe/Berlin'))
                stamp=dt.timestamp()
                if not now-8*86400<=stamp<=now: continue
                bucket=int(stamp//21600)
                if bucket not in buckets or stamp>buckets[bucket][0]:
                    buckets[bucket]=(stamp,p)
            except (KeyError,TypeError,ValueError):
                continue
        return [p for _,p in sorted(buckets.values())]
    def stamp_of(value):
        try:
            dt=datetime.fromisoformat(str(value).replace('Z','+00:00'))
        except (TypeError,ValueError):
            try:
                dt=datetime.strptime(str(value), '%d.%m.%Y %H:%M').replace(tzinfo=ZoneInfo('Europe/Berlin'))
            except (TypeError,ValueError):
                return 0.0
        if dt.tzinfo is None: dt=dt.replace(tzinfo=ZoneInfo('Europe/Berlin'))
        return dt.timestamp()
    def merge_temperature_rows(known, incoming):
        # Separate snapshots are produced at different times. A failed OCR/NIZ
        # refresh must not overwrite the newer measurement from the main feed.
        by_label={i.get('label'):i for i in known.get('items',[])}
        for item in incoming.get('items',[]):
            old=by_label.get(item.get('label'))
            valid=numeric(item.get('value'))
            if item.get('label')=='Wassertemperatur' and valid is not None and not -2<=valid<=40:
                valid=None
            if old is None or valid is not None and (numeric(old.get('value')) is None or stamp_of(item.get('time'))>=stamp_of(old.get('time'))):
                by_label[item.get('label')]=item
        history={}
        for source in (known,incoming):
            for label,points in (source.get('history') or {}).items():
                history.setdefault(label,{}).update({str(p['t']):p for p in points if isinstance(p,dict) and p.get('t')})
        # Preserve the provenance of the selected temperature, not the last
        # loaded (possibly stale) snapshot's source URL.
        selected=by_label.get('Wassertemperatur')
        provenance=known if selected is not None and any(selected is i for i in known.get('items',[])) else incoming
        metadata={**known,**incoming}
        for key in ('source_url','data_source_url','data_note','attribution','src'):
            if key in provenance: metadata[key]=provenance[key]
        return {**metadata,'items':list(by_label.values()),
                'history':{k:sorted(v.values(),key=lambda p:stamp_of(p['t'])) for k,v in history.items()}}
    rows={}
    for snapshot in (extra,water,niz,brandenburg_snapshot):
        for station in snapshot.get('stations',[]):
            key=str(station.get('id') or '')
            if key: rows[key]=merge_temperature_rows(rows[key],station) if key in rows else station

    def rws_series_key(station):
        if station.get('src') != 'rijkswaterstaat' or not station.get('source_station'):
            return None
        return (str(station['source_station']).casefold(),
                str(station.get('sampling_height') or ''),
                str(station.get('sampling_reference') or ''))

    def level_timestamp(station):
        current=station.get('current') or {}
        # Some feeds leave a plausible timestamp beside a null/no-data value.
        # It must not outrank an older but real measurement from another source.
        if numeric(current.get('v',current.get('value'))) is None:
            return 0.0
        return stamp_of(current.get('t'))

    def merge_level(old, new):
        """Keep the newest real reading, and union history by timestamp/value."""
        old_time,new_time=level_timestamp(old),level_timestamp(new)
        winner={**old,**new} if new_time>=old_time else {**new,**old}
        winner['current']=(new.get('current') if new_time>=old_time else old.get('current')) or {}
        points=[]
        for station in (old,new):
            history=station.get('history') or []
            if isinstance(history,dict):
                history=history.get('Pegelstand') or []
            if isinstance(history,list):
                points.extend(history)
            current=station.get('current') or {}
            if current.get('t') is not None:
                points.append(current)
        by_key={}
        for point in points:
            if not isinstance(point,dict) or point.get('t') is None:
                continue
            value=numeric(point.get('v',point.get('value')))
            if value is None:
                continue
            key=(str(point['t']),value)
            by_key[key]={'t':point['t'],'v':value}
        winner['history']=sorted(by_key.values(),key=lambda p:stamp_of(p['t']))
        return winner

    # Build the first-paint level packet from both the local WSV snapshot and
    # the published European snapshot. Previously the browser merged Europe
    # only after load, so Austria (including most Mur/Drau gauges) was absent
    # from the static packet.
    level_rows={str(s.get('id')):s for s in levels.get('stations',[])
                if s.get('id') and numeric((s.get('current') or {}).get('v',(s.get('current') or {}).get('value')))!=-777}
    for source in (europe_levels.get('stations',[]),river_additions.get('level_stations',[]),extra_sensors.get('level_stations',[])):
        for raw in source:
            station=dict(raw)
            if not station.get('id'):
                continue
            item=next((i for i in station.get('items',[]) if i.get('label')=='Pegelstand'),None)
            current=station.get('current') or {}
            if item:
                current={'v':numeric(item.get('value')),'t':item.get('time')}
                station['unit']=item.get('unit') or station.get('unit')
            else:
                current={'v':numeric(current.get('v',current.get('value'))),'t':current.get('t')}
            if current['v'] is None or not current.get('t'):
                continue
            # Tirol's -777 is a documented missing-value sentinel, not a level.
            if current['v']==-777:
                continue
            source_id=station.get('src') or station.get('source')
            station['source']=station.get('source') or source_id
            station['current']=current
            history=station.get('history') or []
            if isinstance(history,dict):
                history=history.get('Pegelstand') or []
            station['history']=history if isinstance(history,list) else []
            key=str(station['id'])
            level_rows[key]=merge_level(level_rows[key],station) if key in level_rows else station
    level_updates=[value for value in (levels.get('fetched_at'),europe_levels.get('updated'),river_additions.get('updated'),extra_sensors.get('updated')) if value]
    newest_level_update=max(level_updates,key=stamp_of) if level_updates else levels.get('fetched_at')
    levels={**levels,'fetched_at':newest_level_update,'stations':list(level_rows.values())}

    # Dieselbe RWS-Messreihe kann in Live- und Auslandssnapshot unter leicht
    # verschiedenen IDs stehen. Ueber Stationscode und Messhorizont zusammen-
    # fuehren, sonst entstehen Duplikate statt dass der frischere Wert gewinnt.
    rws_keys={rws_series_key(station): key for key,station in rows.items() if rws_series_key(station)}
    # Dieselbe Station kann in beiden Laeufen stehen; die Laeufe starten zu
    # verschiedenen Minuten. Es gewinnt der juengere Messwert, nicht die
    # zuletzt gelesene Datei.
    for station in europe.get('stations',[])+extra_sensors.get('temperature_stations',[]):
        station=dict(station)
        if station.get('country')=='NL' and not station.get('river'):
            station['river']=nl_waters.get(station.get('name',''), '')
            # Die Karte zeichnet Bovenrijn als niederlaendischen Rheinabschnitt.
            if station['river']=='Bovenrijn':
                station['river']='Rhein'
        key=str(station.get('id') or '')
        if not key: continue
        series_key=rws_series_key(station)
        key=rws_keys.get(series_key,key)
        known=rows.get(key)
        if known is None:
            rows[key]=station
            if series_key:
                rws_keys[series_key]=key
            continue
        by_label={i.get('label'):i for i in (known.get('items') or [])}
        for item in (station.get('items') or []):
            old=by_label.get(item.get('label'))
            if old is None or stamp_of(item.get('time'))>stamp_of(old.get('time')):
                by_label[item.get('label')]=item
        history=dict(known.get('history') or {})
        for label,points in (station.get('history') or {}).items():
            if not history.get(label): history[label]=points
        rows[key]={**known,'river':known.get('river') or station.get('river'),
                   'items':list(by_label.values()),'history':history}
    temperatures=[]
    for row in rows.values():
        items=[i for i in row.get('items',[]) if 'wassertemperatur' in i.get('label','').lower()]
        history={k:recent(v) for k,v in row.get('history',{}).items() if 'wassertemperatur' in k.lower()}
        if not items and (row.get('params') or {}).get('wt'):
            # A known thermometer is not a current reading. Keep the station
            # visible/clickable even during an outage (e.g. Saale/Hof), without
            # colouring any reach or stamping a fabricated value with 'now'.
            items=[{'label':'Wassertemperatur','value':None,'unit':'°C','time':None,'quality':'unavailable'}]
        if items or history:
            temperatures.append({**{k:v for k,v in row.items() if k not in ('items','history')},'items':items,'history':history})
    short_archive={**archive,'stations':[{**s,'values':recent(s.get('values',[]))} for s in archive.get('stations',[])]}
    # Alles, was die Karte zeichnet. Fehlt ein Name hier, verschwinden seine
    # Stationen still aus dem Paket, obwohl die Linie gezeichnet wird.
    rivers={'rhein','rhine','hochrhein','oberrhein','mittelrhein','niederrhein','donau','danube','dunaj',
            'mosel','moselle','elbe','labe','weser','main','oder','odra','inn','lech','salzach','enns',
            'mur','drau','traun','isar','neckar','saale','sächsische saale','leine','aller','ems',
            'aare','reuss','limmat','ticino','rhône','rhone','ijssel','lek','maas','nederrijn','waal','spree',
            'rhein-maas-delta','havel',
            # UK main rivers included by the centrally collected EA/SEPA feeds.
            'aire','avon','clyde','dee','don','exe','great ouse','medway','mersey','nene',
            'ribble','severn','spey','tay','tees','thames','trent','tweed','tyne','wye'}
    def mapped(s):
        river=str(s.get('river','')).strip().lower()
        tokens=re.findall(r'[^\W_]+',river,flags=re.UNICODE)
        return river in rivers or any(token in rivers for token in tokens) or 'bodensee' in river
    update_candidates=[(stamp_of(value),value) for value in
        (water.get('updated'),niz.get('updated'),brandenburg_snapshot.get('updated'),europe.get('updated'),
         europe_levels.get('updated'),river_additions.get('updated'),extra_sensors.get('updated')) if value]
    latest_update=max(update_candidates,default=(0,water.get('updated')),key=lambda item:item[0])[1]
    write(output/'temperature-map.json',{'data':{'stations':[s for s in temperatures if mapped(s)]},'archive':{**short_archive,'stations':[s for s in short_archive['stations'] if mapped(s)]},'updated':latest_update})
    def compact_time(value):
        try:
            dt=datetime.fromisoformat(str(value).replace('Z','+00:00'))
            return int(dt.timestamp()*1000) if dt.tzinfo else value
        except ValueError:
            return value
    write(output/'level-map.json',{**{k:v for k,v in levels.items() if k in ('schema','fetched_at','source_url')},'packed_history':True,'stations':[{**s,'history':[[compact_time(p['t']),p['v']] for p in recent(s.get('history',[]))]} for s in levels.get('stations',[])]})
    # Monatsverlauf der Pegel getrennt ablegen: die Wochenkarte bleibt klein,
    # die 30-Tage-Ansicht laedt diese Datei nur bei Bedarf.
    write(output/'level-history.json',{'fetched_at':levels.get('fetched_at'),'packed_history':True,
        'stations':[{'id':s['id'],'history':[[compact_time(p['t']),p['v']] for p in s.get('history',[])]}
                    for s in levels.get('stations',[]) if s.get('history')]})
    # Stable opaque filenames avoid source IDs becoming filesystem paths.
    index=[]
    archive_by_id={str(s['id']):s.get('values',[]) for s in archive.get('stations',[])}
    for kind,stations in [('temperature',temperatures),('level',levels.get('stations',[]))]:
        for station in stations:
            key=hashlib.sha256(str(station['id']).encode()).hexdigest()[:24]
            route=f'{kind}/{key}.json'
            write(output/route,station)
            history_path=f'archive/{kind}/{key}.json'
            previous=read(output/history_path)
            points=list(previous.get('values',[]))
            if kind=='temperature':
                points+=archive_by_id.get(str(station['id']),[])
                for values in station.get('history',{}).values():points+=values
                points += [{'t':i.get('time'),'v':i.get('value')} for i in station.get('items',[])]
            else:
                points+=station.get('history',[])
                points.append(station.get('current') or {})
            write(output/history_path,{'id':station['id'],'kind':kind,'retention_days':366,'fine_days':30,'values':retain(points,datetime.now(timezone.utc).timestamp())})
            index.append({'id':station['id'],'kind':kind,'river':station.get('river'),'path':route,'archive':history_path})
    write(output/'index.json',{'schema':1,'stations':index})


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--root',type=Path,default=Path(__file__).resolve().parents[1])
    parser.add_argument('--output',type=Path,default=None)
    args=parser.parse_args()
    build(args.root,args.output or args.root/'assets/data/packets')
