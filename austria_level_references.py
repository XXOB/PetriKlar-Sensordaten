"""Station-specific relative levels from explicitly offered eHYD daily-mean CSVs.
Never treat discharge or temperature as gauge height. Archives stay out of the
live week history; only their documented statistical comparison is attached.
"""
import json, re, math
from pathlib import Path
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor
from ehyd_mur_archive import get, BASE

ROOT=Path(__file__).resolve().parent

def offered_files(node):
    if isinstance(node, list):
        for child in node: yield from offered_files(child)
    elif isinstance(node, dict):
        if 'fileName' in node and 'fileNr' in node: yield node
        else:
            for child in node.values(): yield from offered_files(child)

def parse(raw):
    text=raw.decode('cp1252')
    if not re.search(r'Einheit:[^\n]*;\s*\[cm\]',text) or 'Wasserstand' not in text:
        raise ValueError('Not a gauge-height series in cm')
    head,body=text.split('Werte:',1)
    dates={}
    for m in re.finditer(r'(\d{2}\.\d{2}\.\d{4}) \d{2}:\d{2}:\d{2};\s*(-?\d+(?:,\d+)?)\s*(?:\r?\n|$)',body):
        dates[datetime.strptime(m[1],'%d.%m.%Y').date()]=float(m[2].replace(',','.'))
    if not dates:raise ValueError('Empty daily archive')
    year=max(d.year for d in dates)
    dates={d:v for d,v in dates.items() if d.year==year}
    ds=sorted(dates)
    if len(ds)<300 or (ds[-1]-ds[0]).days<330 or max((b-a).days for a,b in zip(ds,ds[1:]))>30:
        raise ValueError('Incomplete last archive year')
    vals=sorted(dates.values())
    def q(p):
        n=(len(vals)-1)*p;i=int(n)
        return vals[i]+(vals[min(i+1,len(vals)-1)]-vals[i])*(n-i)
    lo,mid,hi=q(.1),q(.5),q(.9)
    if not lo<mid<hi:raise ValueError('No usable distribution')
    datum=head.split('Pegelnullpunkt:',1)[-1].split('Bundesmeldenetz',1)[0]
    pnps=re.findall(r'(\d{2}\.\d{2}\.\d{4})\s*;\s*(\d+,\d+)',datum)
    pnp=float(pnps[-1][1].replace(',','.')) if pnps else None
    return dict(method='daily-mean-p10-p50-p90',low=lo,center=mid,high=hi,unit='cm',
                start=str(ds[0]),end=str(ds[-1]),days=len(ds),source='eHYD',datum_m=pnp)

def collect(stations,network):
    candidates=[];errors=[]
    for s in stations:
        if s.get('country')!='AT' or s.get('history_baseline') or s.get('river') not in ('Drau','Salzach','Enns','Inn','Lech','Traun','Donau','Mur'):continue
        if not any(i.get('label')=='Pegelstand' and i.get('unit')=='cm' for i in s.get('items',[])):continue
        sid=s['id'].rsplit('-',1)[-1]
        if not re.fullmatch(r'\d{6}',sid):
            matches=[]
            for f in network['features']:
                p=f['properties'];lon,lat=f['geometry']['coordinates'][:2]
                if p.get('gewaesser')!=s['river']:continue
                km=111*math.hypot((lon-s['lon'])*math.cos(math.radians(lat)),lat-s['lat'])
                if km<.15:matches.append(p['hzbnr'])
            if len(matches)!=1:errors.append({'id':s['id'],'error':'No unique same-river station within 150 m'});continue
            sid=matches[0]
        candidates.append((s,sid))
    def one(job):
        s,sid=job
        try:
            info=json.loads(get(BASE+'services/Messstellen/info?hzbnr='+sid))
            f=next((f for f in offered_files(info) if f['fileName'].startswith('W-Tagesmittel-')),None)
            if f is None: raise ValueError('No offered daily gauge-height archive')
            url=BASE+'services/MessstellenExtraData/owf?id='+sid+'&file='+str(f['fileNr'])
            b=parse(get(url));b.update(source_url=url,hzbnr=sid)
            current=s.get('level_datum_m')
            if current is not None and b['datum_m'] is not None and abs(current-b['datum_m'])>.02:
                raise ValueError('Gauge datum differs from archive')
            return (s['id'],b,None)
        except Exception as e:return (s['id'],None,str(e) or type(e).__name__)
    refs={}
    with ThreadPoolExecutor(max_workers=3) as pool:
        for sid,b,error in pool.map(one,candidates):
            if b:refs[sid]=b;print(sid,b['end'],flush=True)
            else:errors.append({'id':sid,'error':error})
    return {'references':refs,'errors':errors}

if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser();parser.add_argument('--stations',required=True);parser.add_argument('--network',required=True)
    args=parser.parse_args()
    out=collect(json.loads(Path(args.stations).read_text(encoding='utf-8'))['stations'],json.loads(Path(args.network).read_text(encoding='utf-8')))
    (ROOT/'austria-level-references.json').write_text(json.dumps(out,ensure_ascii=False,separators=(',',':')),encoding='utf-8')
    print('References',len(out['references']),'unavailable',len(out['errors']))
