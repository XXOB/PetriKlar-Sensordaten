"""Refresh public catalogue sources. NIZ/HLNUG values remain client-side only."""
import json
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import Request, urlopen

DEST=Path(__file__).with_name('discovery-network-snapshot.json')
URLS={
 'pegel':'https://www.pegelonline.wsv.de/webservices/rest-api/v2/stations.json?includeTimeseries=true&includeCurrentMeasurement=true',
 'supplement':'https://xxob.github.io/PetriKlar-Sensordaten/temperatur_zusatz.json',
 'niz':'https://inovum-services.de/gmb/md/v1/gewaesser;1.0.0?page[limit]=1000',
 'hessen':'https://app.hlnug.de/json/wasser/getThemeStations/6/63,67,55,69,110,126,138,144?tformat=d.m.Y%20H:i'
}
PARAMS={'W':'Pegelstand','WT':'Wassertemperatur','Q':'Durchfluss','LT':'Lufttemperatur','WG':'Windgeschwindigkeit','WR':'Windrichtung','LF':'Leitfähigkeit','O2':'Sauerstoff','PH':'pH-Wert','CL':'Chlorid','GRU':'Grundwasserstand','DFH':'Durchfahrtshöhe','VA':'Strömungsgeschwindigkeit','V':'Volumen','HL':'Luftfeuchte','R':'Richtungstrom','MAXH':'Maximale Wellenhöhe','DIRTP':'Richtung Hauptseegang','SIGH':'Signifikante Wellenhöhe','RV':'Strömungsrichtung','TP':'Wellenperiode','SW':'Sichtweite','TR':'Trübung'}
NIZ={'temp':('Wassertemperatur','°C'),'o2':('Sauerstoff','mg/l'),'tr':('Trübung','FNU'),'lf':('Leitfähigkeit','µS/cm'),'pH':('pH-Wert','')}
def convert(key,data):
 if key=='supplement':return data['stations']
 out=[]
 if key=='pegel':
  for s in data:
   if not s.get('latitude') or not s.get('longitude'):continue # same station inventory as app
   items=[]
   for row in s.get('timeseries',[]):
    m=row.get('currentMeasurement') or {};code=row['shortname']
    items.append({'label':PARAMS.get(code,row.get('longname',code)),'value':m.get('value'),'time':m.get('timestamp'),'unit':row.get('unit',''),'parameter':code})
   out.append({'id':'pegelonline-'+s['uuid'],'name':s['shortname'].title(),'river':s['water']['longname'].title(),'lat':s['latitude'],'lon':s['longitude'],'src':'pegelonline','source_url':'https://www.pegelonline.wsv.de/gast/stammdaten?pegelnr='+s['number'],'items':items,'provider':'pegel','uuid':s['uuid'],'attribution':'PEGELONLINE / WSV – ungeprüfte Rohdaten'})
 elif key=='niz':
  for row in data['data']:
   a=row['attributes'];g=a.get('geometry',{})
   if g.get('lat') is None or not a.get('gewaesser'):continue
   # Catalogue metadata only: no locally hosted NIZ measurements.
   params=[k for k in NIZ if k in a.get('messreihen',{})]
   if not params:continue
   out.append({'id':'niz-'+str(a.get('id') or row['id']),'name':a.get('name') or a['gewaesser'],'river':a['gewaesser'],'lat':g['lat'],'lon':g.get('lon'),'src':'niz','provider':'niz','remote_id':str(a.get('id') or row['id']),'source_url':'https://niz.baden-wuerttemberg.de/oberflaechengewaesser/gueteparameter','items':[{'label':NIZ[k][0],'unit':NIZ[k][1],'value':None,'time':None} for k in params],'attribution':a.get('betreiber','LUBW / NIZ')})
 elif key=='hessen':
  for s in data:
   if s.get('isConti')!=1:continue # same continuous stations as app; no sporadic samples
   parts=s['displayName'].split(',');river=parts[0].strip();name=', '.join(x.strip() for x in parts[1:]).replace('Messstation','').strip(' ,')
   out.append({'id':'he-'+str(s['stationId']),'name':name or river,'river':river,'lat':s.get('lat'),'lon':s.get('lon'),'src':'hlnug','provider':'hlnug','remote_id':str(s['stationId']),'source_url':'https://www.hlnug.de/messwerte/datenportal','items':[{'label':'Wassertemperatur','unit':'°C','value':None,'time':None},{'label':'Sauerstoff','unit':'mg/l','value':None,'time':None}]})
 return out
def main():
 saved=json.loads(DEST.read_text(encoding='utf-8')) if DEST.exists() else {'datasets':{}}
 for key,url in URLS.items():
  try:
   if key=='supplement':
    data=json.loads((Path(__file__).resolve().parents[1]/'temperatur_zusatz.json').read_text(encoding='utf-8'))
   else:
    with urlopen(Request(url,headers={'User-Agent':'PetriKlar-public-catalog/1.0'}),timeout=35) as r:data=json.load(r)
   stations=convert(key,data)
   if not stations:raise ValueError('Empty inventory')
   saved['datasets'][key]={'fetched_at':datetime.now(timezone.utc).isoformat(),'source_url':url,'stations':stations}
   print(key,len(stations))
  except Exception as e:print(key,'kept previous snapshot:',e)
 if not all(k in saved['datasets'] for k in URLS):raise RuntimeError('Missing source inventory; no partial release')
 DEST.write_text(json.dumps(saved,ensure_ascii=False,separators=(',',':'))+'\n',encoding='utf-8')
if __name__=='__main__':main()
