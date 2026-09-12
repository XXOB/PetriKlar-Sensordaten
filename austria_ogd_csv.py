"""CC-BY-4.0 OGD CSV feeds from Tyrol and Salzburg."""
import csv,io,re,urllib.request
from datetime import datetime,timezone
from functools import lru_cache
from pyproj import Transformer

TIROL=['https://hydro.tirol.gv.at/hydro/ogd/OGD_W.csv','https://hydro.tirol.gv.at/hydro/ogd/OGD_WT.csv']
SALZBURG=['https://www.salzburg.gv.at/ogd/56c28e2d-8b9e-41ba-b7d6-fa4896b5b48b/Hydrografie%20Pegelstand.txt','https://www.salzburg.gv.at/ogd/56c28e2d-8b9e-41ba-b7d6-fa4896b5b48b/Hydrografie%20Seen.txt']
@lru_cache(maxsize=8)
def transformer(crs):return Transformer.from_crs(crs,'EPSG:4326',always_xy=True)
def parse(raw,src,source,attribution):
    try:text=raw.decode('utf-8-sig')
    except (UnicodeDecodeError,LookupError):text=raw.decode('cp1252')
    grouped={}
    for p in csv.DictReader(io.StringIO(text),delimiter=';'):
        parameter=p.get('Parameter','').strip()
        if parameter not in ('W','WT','Q'):continue
        label={'W':'Pegelstand','WT':'Wassertemperatur','Q':'Durchfluss'}[parameter]
        try:
            value=float(p['Wert'].replace(',','.'));unit=p['Einheit'].strip()
            if unit!={'W':'cm','WT':'°C','Q':'m³/s'}[parameter]:continue
            stamp=p['Zeitstempel in ISO8601'].split(' ')[0]
            stamp=stamp[:10].replace('.','-')+stamp[10:]
            t=datetime.fromisoformat(stamp)
            if t.tzinfo is None or t>datetime.now(timezone.utc):continue
            sid=p['Stationsnummer'].strip()
            x=float(p['Rechtswert'].replace('RW','').strip());y=float(p['Hochwert'].replace('HW','').strip())
            lon,lat=transformer(p['EPSG-Code'].strip()).transform(x,y)
            if not 8<lon<18 or not 45<lat<50:continue
        except (ValueError,KeyError):continue
        row=grouped.setdefault(sid,dict(id=src+'-'+sid,name=p['Stationsname'],river=p['Gewässer'],lat=lat,lon=lon,src=src,country='AT',license='CC BY 4.0',license_url='https://creativecommons.org/licenses/by/4.0/',source_url=source,attribution=attribution,params={'pegel':False,'wt':False},items=[],history={}))
        row['history'].setdefault(label,[]).append(dict(t=t.isoformat(),v=value))
        row['params']['wt' if parameter=='WT' else 'pegel']=True
    for row in grouped.values():
        for label,points in row['history'].items():
            points.sort(key=lambda p:p['t']);p=points[-1]
            row['items'].append(dict(label=label,value=p['v'],unit={'Pegelstand':'cm','Wassertemperatur':'°C','Durchfluss':'m³/s'}[label],time=p['t']))
        row['updated']=max(i['time'] for i in row['items'])
    return list(grouped.values())
def collect():
    from country_pipeline import combine
    rows=[];errors=[]
    for src,urls,credit in [('at-tirol',TIROL,'Land Tirol – data.tirol.gv.at'),('at-sbg',SALZBURG,'Land Salzburg – Hydrographischer Dienst')]:
        for u in urls:
            try:
                with urllib.request.urlopen(u,timeout=45) as r:raw=r.read(10000000)
                rows.extend(parse(raw,src,u,credit))
            except Exception as e:errors.append({'source':src,'url':u,'error':str(e)})
    return combine([{'stations':rows}])['stations'],errors
