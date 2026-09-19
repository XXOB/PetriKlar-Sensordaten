"""Station-specific relative levels from explicitly offered eHYD daily-mean CSVs.
Never treat discharge or temperature as gauge height. Archives stay out of the
live week history; only their documented statistical comparison is attached.
"""
import json, re, math
from pathlib import Path
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor
from urllib.request import urlopen, Request
from ehyd_mur_archive import get, BASE

def ehyd_network():
    """Alle eHYD-Messstellen mit Wasserstand-Tagesmitteln, als (hzbnr, lat, lon).

    Das Bundesnetz 'pegel_aktuell' kennt nur rund 300 Pegel - die Landespegel
    (z.B. Oberoesterreich an Traun und Enns) fehlen dort. eHYD selbst fuehrt
    alle, mit Koordinaten in EPSG:3857."""
    with urlopen(Request(BASE+'services/Messstellen/json?filter=alle',headers={'User-Agent':'PetriKlar public water data'}),timeout=60) as r:
        data=json.loads(r.read(20_000_001))
    R=6378137;out=[]
    for f in data.get('features',[]):
        p=f.get('properties') or {}
        if not str(p.get('symbol','')).startswith('owf_messstelle') or 'W-Tagesmittel' not in str(p.get('fjson','')):continue
        x,y=f['geometry']['coordinates'][:2]
        out.append((str(p['hzbnr01']),math.degrees(2*math.atan(math.exp(y/R))-math.pi/2),math.degrees(x/R)))
    return out

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
    # Das letzte Jahr einer eHYD-Datei ist oft nur angefangen oder voller
    # Luecken (z.B. Theresienthal/Traun: 2016 nur 144 Tage, 2017-2023 Luecke).
    # Deshalb das juengste VOLLSTAENDIGE Jahr nehmen - hoechstens 15 Jahre alt,
    # damit kein laengst veraendertes Flussbett den Vergleich verfaelscht.
    alle=dates;dates=None
    for year in sorted({d.year for d in alle},reverse=True):
        if year<datetime.now().year-15:break
        jahr={d:v for d,v in alle.items() if d.year==year}
        ds=sorted(jahr)
        if len(ds)>=300 and (ds[-1]-ds[0]).days>=330 and max((b-a).days for a,b in zip(ds,ds[1:]))<=30:
            dates=jahr;break
    if dates is None:
        raise ValueError('No complete archive year in the last 15 years')
    ds=sorted(dates)
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

def collect(stations,network=None):
    candidates=[];errors=[]
    if network is None:network=ehyd_network()
    for s in stations:
        if s.get('country')!='AT' or s.get('history_baseline') or s.get('river') not in ('Drau','Salzach','Enns','Inn','Lech','Traun','Donau','Mur'):continue
        if not any(i.get('label')=='Pegelstand' and i.get('unit')=='cm' for i in s.get('items',[])):continue
        sid=s['id'].rsplit('-',1)[-1]
        if not re.fullmatch(r'\d{6}',sid):
            # Landeskennungen (z.B. OOe-HD-Nummern) ueber die Lage zuordnen: genau
            # eine eHYD-Messstelle im Umkreis von 150 m, die naechste erst weiter weg.
            nah=sorted((111*math.hypot((lon-s['lon'])*math.cos(math.radians(lat)),lat-s['lat']),hzb) for hzb,lat,lon in network)
            if not nah or nah[0][0]>=.15 or (len(nah)>1 and nah[1][0]<.15):
                errors.append({'id':s['id'],'error':'No unique eHYD station within 150 m'});continue
            sid=nah[0][1]
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
            # Plausibilitaet: der heutige Pegel muss zum Archivjahr passen. Liegt er
            # weit ausserhalb, wurde der Pegel seither versetzt - dann lieber keine
            # Referenz als eine falsche Einfaerbung.
            live=next((i.get('value') for i in s.get('items',[]) if i.get('label')=='Pegelstand'),None)
            try:live=float(str(live).replace(',','.'))
            except (TypeError,ValueError):live=None
            span=b['high']-b['low']
            if live is not None and not (b['low']-3*span<=live<=b['high']+3*span):
                raise ValueError('Current reading does not fit the archive year')
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
    parser=argparse.ArgumentParser();parser.add_argument('--stations',required=True)
    args=parser.parse_args()
    out=collect(json.loads(Path(args.stations).read_text(encoding='utf-8'))['stations'])
    target=ROOT/'austria-level-references.json'
    previous=json.loads(target.read_text(encoding='utf8')) if target.exists() else {}
    refs={**previous.get('references',{}),**out['references']}
    for error in out['errors']:refs.pop(error['id'],None)
    out['references']=refs
    target.write_text(json.dumps(out,ensure_ascii=False,separators=(',',':')),encoding='utf-8')
    print('References',len(out['references']),'unavailable',len(out['errors']))
