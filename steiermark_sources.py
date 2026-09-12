"""Official HyDaVis CSV downloads. The publisher's accuracy disclaimer is accepted.
Do not label these downloads CC BY: provenance records the actual download terms.
"""
import csv
import io
import json
import re
import http.cookiejar
import urllib.request
import urllib.parse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from bs4 import BeautifulSoup

BASE='https://egov.stmk.gv.at/at.gv.stmk.hydavis-p/pub/praesentation/index.xhtml'
SOURCE='https://www.hydrografie.steiermark.at/cms/beitrag/12813605/142514764'
LICENSE='Public download — publisher disclaimer accepted'
MEZ=timezone(timedelta(hours=1))
PARAMS={'2001':('Pegelstand','cm'),'2003':('Wassertemperatur','°C')}

def url(code, **extra):
    return BASE+'?'+urllib.parse.urlencode(dict(messcode=code,stationsstatus='ONLINE',ansichtstyp='karte',**extra))

def request(op,u,data=None):
    req=urllib.request.Request(u,data=data,headers={'User-Agent':'PetriKlar/1.0 (public hydrology downloads)'})
    with op.open(req,timeout=60) as r:
        limit=12000000 if data is not None else 32000000
        body=r.read(limit)
        if len(body)>=limit:raise ValueError('Download exceeds 12 MB limit')
        return body

def inventory(code):
    raw=request(urllib.request.build_opener(),url(code)).decode('utf8')
    m=re.search(r"var stationen = JSON.parse\('(.*?)'\);",raw,re.S)
    if not m:raise ValueError('HyDaVis station list missing')
    return json.loads(m[1].replace("\\'", "'"))

def parse_csv(raw):
    text=raw.decode('utf-16-be' if raw.startswith(b'\x00') else 'utf-8-sig')
    lines=text.splitlines()
    if not lines or not lines[0].startswith('Einheit:'):raise ValueError('Not a HyDaVis CSV download')
    unit=lines[0].split(':',1)[1].strip();points={}
    for row in csv.DictReader(lines[1:],delimiter=';'):
        try:
            stamp=datetime.strptime(row['ZEITPUNKT'],'%d.%m.%Y %H:%M:%S').replace(tzinfo=MEZ)
            value=float(row['WERT'].replace(',','.'))
            if not -10000 < value < 100000:continue
            points[stamp.isoformat()]={'t':stamp.isoformat(),'v':value}
        except (ValueError,KeyError,AttributeError):continue
    return unit, sorted(points.values(),key=lambda p:p['t'])

def download(code,hdnr,start,end):
    op=urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
    u=url(code,hdnr=hdnr,detail='download')
    s=BeautifulSoup(request(op,u),'html.parser');f=s.find('form',id='layout:inhalt:mainForm')
    if not f:raise ValueError('Download form missing')
    data={}
    for e in f.select('input[name]'):
        if e.get('type') in ('radio','checkbox') and not e.has_attr('checked'):continue
        data[e['name']]=e.get('value','')
    for e in f.select('select[name]'):
        o=e.find('option',selected=True) or e.find('option')
        if o:data[e['name']]=o.get('value','')
    for k in data:
        if k.endswith('datumvon_input'):data[k]=start.strftime('%d.%m.%Y')
        if k.endswith('datumbis_input'):data[k]=end.strftime('%d.%m.%Y')
    button=f.find('button',id=re.compile('hiddendownloadbutton$'))
    if not button:raise ValueError('Download action missing')
    # This is the form action triggered by the explicitly approved confirmation.
    data[button['name']]=''
    return parse_csv(request(op,urllib.parse.urljoin(u,f['action']),urllib.parse.urlencode(data).encode()))

def collect(annual=False, budget=None, previous=None):
    from build_sensor_packets import retain
    from europe_sources import history_baseline
    now=datetime.now(timezone.utc);old={s['id']:s for s in (previous or [])};jobs=[];errors=[]
    for code in PARAMS:
        if annual and code!='2001':continue
        try:
            for meta in inventory(code):
                sid='at-stmk-'+meta['hdnr']
                if annual and old.get(sid,{}).get('history_baseline'):continue
                jobs.append((code,meta))
        except Exception as e:errors.append({'parameter':code,'error':str(e)})
    # Prioritize the rivers already drawn on the European map.
    jobs.sort(key=lambda x:(x[1].get('gewaesser') not in ('Mur','Enns'),x[1]['hdnr']))
    if budget is not None:jobs=jobs[:budget]
    def one(job):
        code,m=job;label,expected=PARAMS[code]
        if not annual:
            latest=m.get('letzterMesswert') or {}
            stamp=datetime.strptime(latest['zeitpunkt'],'%b %d, %Y, %I:%M:%S %p').replace(tzinfo=MEZ)
            if (now-stamp).total_seconds()>8*86400:raise ValueError('No recent observations')
            unit=expected;points=[{'t':stamp.isoformat(),'v':float(latest['wert'])}]
        else:
            return historical_one(code,m,label,expected)
        return make_row(code,m,label,expected,unit,points)
    def historical_one(code,m,label,expected):
        try:
            unit,points=download(code,m['hdnr'],now-timedelta(days=365 if annual else 8),now)
        except ValueError as error:
            if not annual or '12 MB' not in str(error):raise
            points=[]
            start=now-timedelta(days=365)
            while start<now:
                end=min(now,start+timedelta(days=60))
                unit,chunk=download(code,m['hdnr'],start,end)
                points.extend(chunk);start=end
        return make_row(code,m,label,expected,unit,points)
    def make_row(code,m,label,expected,unit,points):
        if unit!=expected:raise ValueError('Unexpected unit: '+unit)
        points=[p for p in points if datetime.fromisoformat(p['t'])<=now]
        if not points:raise ValueError('No observations in requested period')
        hist=retain(points,now.timestamp());latest=points[-1]
        row=dict(id='at-stmk-'+m['hdnr'],name=m['mstnam'],river=m.get('gewaesser',''),lat=m['koordinatenBreite'],lon=m['koordinatenLaenge'],src='at-stmk',country='AT',license=LICENSE,license_url=SOURCE,source_url=url(code,hdnr=m['hdnr'],detail='download'),attribution='Land Steiermark – Hydrografischer Dienst',updated=latest['t'],params={'pegel':code=='2001','wt':code=='2003'},items=[dict(label=label,value=latest['v'],unit=unit,time=latest['t'])],history={label:hist})
        if label=='Pegelstand':
            baseline=history_baseline(hist,unit,now)
            if baseline:row['history_baseline']=baseline
        return row
    rows=[]
    with ThreadPoolExecutor(max_workers=3) as pool:
        futures={pool.submit(one,j):j for j in jobs}
        for f in as_completed(futures):
            code,m=futures[f]
            try:rows.append(f.result())
            except Exception as e:errors.append({'station':m['hdnr'],'parameter':code,'error':str(e)})
    return rows,dict(stations=len(rows),errors=errors,annual=annual)
