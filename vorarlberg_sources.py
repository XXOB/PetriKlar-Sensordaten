"""Vorarlberg public hydrology measurements, CC BY 4.0 (data.gv.at)."""
import json,re,urllib.request
from datetime import datetime
from zoneinfo import ZoneInfo
from bs4 import BeautifulSoup
URL='https://vowis.vorarlberg.at/stationsInfo/tbl_Abflussstationen.aspx'
NETWORK='https://vowis.vorarlberg.at/geoserver/owf/ows?service=WFS&request=GetFeature&typename=owf:Pegel&srsname=EPSG:4326&outputFormat=application/json'
def collect():
    with urllib.request.urlopen(NETWORK,timeout=40) as r:features=json.load(r)['features']
    coords={str(f['properties']['HZBNR']):f['geometry']['coordinates'] for f in features}
    with urllib.request.urlopen(URL,timeout=40) as r:s=BeautifulSoup(r.read(),'html.parser')
    rows=[];missing=[]
    for tr in s.find_all('tr'):
        cells=[td.get_text(' ',strip=True) for td in tr.find_all('td')]
        if len(cells)!=6:continue
        m=re.match(r'(.*),\s*(\d+)\s*/\s*(.*)',cells[0])
        if not m:continue
        name,sid,river=m.groups()
        if sid not in coords:missing.append(sid);continue
        try:stamp=datetime.strptime(cells[1],'%d.%m.%y %H:%M').replace(tzinfo=ZoneInfo('Europe/Vienna')).isoformat()
        except ValueError:continue
        items=[]
        for col,label,unit in [(2,'Durchfluss','m³/s'),(3,'Pegelstand','cm'),(4,'Wassertemperatur','°C')]:
            try:value=float(cells[col].replace(',','.'))
            except ValueError:continue
            items.append(dict(label=label,value=value,unit=unit,time=stamp))
        lon,lat=coords[sid]
        rows.append(dict(id='at-vbg-'+sid,name=name,river=river,lat=lat,lon=lon,src='at-vbg',country='AT',license='CC BY 4.0',license_url='https://creativecommons.org/licenses/by/4.0/',source_url=URL,attribution='Land Vorarlberg – Hydrographischer Dienst',updated=stamp,items=items,history={i['label']:[{'t':stamp,'v':i['value']}] for i in items},params={'pegel':any(i['label']=='Pegelstand' for i in items),'wt':any(i['label']=='Wassertemperatur' for i in items)}))
    return rows,[{'source':'Vorarlberg','missing_coordinates':missing}] if missing else []
