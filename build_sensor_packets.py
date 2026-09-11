"""Small map payloads and station-specific histories; no network requests."""
import argparse
import hashlib
import json
import math
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
    archive=read(root/'wassertemperatur_verlauf.json')
    levels=read(root/'pegelkarte.json') or read(root/'assets/data/water-levels.json')
    cutoff=(datetime.now(timezone.utc)-timedelta(days=9)).isoformat()[:10]
    def recent(points):
        # ISO dates, including source-local timestamps: retain a full extra day.
        selected=[p for p in points if str(p.get('t',''))[:10]>=cutoff]
        return selected or points[-1:]
    rows={str(s['id']):s for s in extra.get('stations',[])}
    rows.update({str(s['id']):s for s in water.get('stations',[])})
    temperatures=[]
    for row in rows.values():
        items=[i for i in row.get('items',[]) if 'wassertemperatur' in i.get('label','').lower()]
        history={k:recent(v) for k,v in row.get('history',{}).items() if 'wassertemperatur' in k.lower()}
        if items or history:
            temperatures.append({**{k:v for k,v in row.items() if k not in ('items','history')},'items':items,'history':history})
    short_archive={**archive,'stations':[{**s,'values':recent(s.get('values',[]))} for s in archive.get('stations',[])]}
    rivers={'rhein','rhine','hochrhein','oberrhein','mittelrhein','niederrhein','donau','danube','dunaj','mosel','moselle','elbe','labe','weser','main','oder','odra','inn','lech'}
    def mapped(s):
        river=str(s.get('river','')).strip().lower()
        return river in rivers or 'bodensee' in river
    write(output/'temperature-map.json',{'data':{'stations':[s for s in temperatures if mapped(s)]},'archive':{**short_archive,'stations':[s for s in short_archive['stations'] if mapped(s)]},'updated':water.get('updated')})
    def compact_time(value):
        try:
            dt=datetime.fromisoformat(str(value).replace('Z','+00:00'))
            return int(dt.timestamp()*1000) if dt.tzinfo else value
        except ValueError:
            return value
    write(output/'level-map.json',{**{k:v for k,v in levels.items() if k in ('schema','fetched_at','source_url')},'packed_history':True,'stations':[{**s,'history':[[compact_time(p['t']),p['v']] for p in recent(s.get('history',[]))]} for s in levels.get('stations',[])]})
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
