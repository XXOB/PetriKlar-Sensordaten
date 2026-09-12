"""Public tables linked by the regional hydrographic services.
Keep their source attribution distinct from separately licensed OGD feeds.
"""
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
import math

TERMS = 'Public download — publisher disclaimer accepted'
KTN = 'https://hydrographie.ktn.gv.at/DE/repos/evoscripts/hydrografischer/getFluesseWassertemperatur%2ees'
SBG = 'https://www.salzburg.gv.at/wasser/hydro/grafiken/data.json'

def number(value):
    try:
        n=float(str(value).replace(',','.'))
        return n if math.isfinite(n) else None
    except (ValueError,TypeError): return None

def kaernten(payload, stations):
    """Join by official HZB number, never by name or nearby location."""
    output=[]
    by_id={s['id']:s for s in stations}
    for p in payload.get('data',[]):
        old=by_id.get('at-ktn-'+str(p.get('stationsnummer')))
        v=number(p.get('level')) # this specific temperature table's WT column
        if old is None or v is None or not -2<=v<=40:continue
        try:t=datetime.strptime(p['datum'],'%d.%m.%Y %H:%M').replace(tzinfo=ZoneInfo('Europe/Vienna')).isoformat()
        except (KeyError,ValueError):continue
        row={**old,'src':'at-ktn-public','license':TERMS,'license_url':'https://hydrographie.ktn.gv.at/Hochwasserwarnung/allgemeine_informationen',
             'source_url':KTN,'attribution':'Hydrographischer Dienst Kärnten – öffentliche Messwerttabelle',
             'params':{**old.get('params',{}),'wt':True},'history':dict(old.get('history',{}))}
        row['items']=[i for i in old.get('items',[]) if i['label']!='Wassertemperatur']+[{'label':'Wassertemperatur','unit':'°C','value':v,'time':t}]
        row['history']['Wassertemperatur']=old.get('history',{}).get('Wassertemperatur',[])+[{'t':t,'v':v}]
        output.append(row)
    return output

def salzburg(payload):
    output=[]
    for p in payload:
        # Atmospheric, spring and groundwater records can name the same river.
        if not any(k.startswith('_OWF.W') for k in p.get('plots',{})):continue
        ll=p.get('latlng',[])
        if len(ll)!=2 or not all(isinstance(v,(int,float)) for v in ll):continue
        items=[]
        for parameter,label,unit in [('W','Pegelstand','cm'),('Q','Durchfluss','m³/s')]:
            for key,v in p.get('values',{}).get(parameter,{}).items():
                if not key.startswith('15m.') or '.ALM.' in key or v.get('unit')!=unit:continue
                value=number(v.get('v'))
                if value is None or not isinstance(v.get('dt'),(int,float)):continue
                t=datetime.fromtimestamp(v['dt']/1000,ZoneInfo('Europe/Vienna')).isoformat()
                items.append(dict(label=label,value=value,unit=unit,time=t));break
        if not items:continue
        output.append(dict(id='at-sbg-'+str(p['number']),name=p['name'],river=p.get('WTO_OBJECT',''),lat=ll[0],lon=ll[1],country='AT',
            src='at-sbg-public',license=TERMS,license_url='https://www.salzburg.gv.at/rechtliche-hinweise',source_url=SBG,
            attribution='Land Salzburg – Hydrographischer Dienst, Hydris',level_datum_m=p.get('GAUGE_DATUM'),
            reference_issue=('changed-profile' if any(word in str(p.get('MALFUNCTION','')).lower() for word in ('profil','provil','versetzt')) else None),
            params={'pegel':True,'wt':False},items=items,history={i['label']:[{'t':i['time'],'v':i['value']}] for i in items}))
    return output

def supplement(stations, fetch):
    rows={s['id']:s for s in stations};errors=[]
    for name,url,parser in [('Kärnten Temperatur',KTN,lambda p:kaernten(p,list(rows.values()))),('Salzburg Hydris',SBG,salzburg)]:
        try:
            for s in parser(fetch(url)):
                prior=rows.get(s['id'],{})
                items={i['label']:i for i in prior.get('items',[])}
                for item in s['items']:
                    if str(item['time'])>=str(items.get(item['label'],{}).get('time','')):items[item['label']]=item
                s['items']=list(items.values());s['history']={**prior.get('history',{}),**s.get('history',{})}
                rows[s['id']]=s
        except Exception as e:errors.append({'source':name,'error':str(e)})
    return list(rows.values()),errors
