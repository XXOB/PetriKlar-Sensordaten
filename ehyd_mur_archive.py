"""Import only WT monthly CSVs explicitly offered by eHYD's station download list.

Historical monthly means are kept separate from current readings and never used
to colour the live week map. Run manually when a new yearbook is published.
"""
import argparse
import json
import re
from datetime import datetime, timezone, timedelta
from pathlib import Path
from urllib.request import urlopen, Request

BASE = 'https://ehyd.gv.at/'
# Mur temperature stations in the official surface-water inventory.
STATIONS = ['203752','203794','203976','211086','211102','211136',
            '211185','211292','211326','211466','211490','211763']

def get(url):
    with urlopen(Request(url, headers={'User-Agent':'PetriKlar public water data'}), timeout=40) as r:
        value = r.read(4_000_001)
    if len(value)>4_000_000:
        raise ValueError('Unexpectedly large eHYD download')
    return value

def parse(raw):
    text = raw.decode('utf-8-sig') if raw.startswith(b'\xef\xbb\xbf') else raw.decode('cp1252')
    if 'Werte:' not in text or 'WTemperatur' not in text:
        raise ValueError('Not a water-temperature CSV')
    points=[]
    for line in text.split('Werte:',1)[1].splitlines():
        match=re.fullmatch(r'\s*(\d{2}\.\d{2}\.\d{4} \d{2}:\d{2}:\d{2});\s*(-?\d+(?:,\d+)?)\s*',line)
        if not match: continue  # Explicit gaps and end markers are not zero.
        day=datetime.strptime(match[1],'%d.%m.%Y %H:%M:%S').replace(tzinfo=timezone(timedelta(hours=1)))
        value=float(match[2].replace(',','.'))
        if -2<=value<=45: points.append({'month':day.strftime('%Y-%m'),'value':value})
    return sorted({p['month']:p for p in points}.values(),key=lambda p:p['month'])

def collect():
    rows=[]
    for sid in STATIONS:
        info=json.loads(get(BASE+'services/Messstellen/info?hzbnr='+sid))
        fields=[field for group in info for section in group.get('data',[]) for field in section.get('info',{}).get('data',[])]
        meta={f['caption']:f.get('text') for f in fields if 'caption' in f}
        files=[f for field in fields for f in field.get('data',{}).get('fileData',[])]
        for f in files:
            if not f['fileName'].startswith('WT-Monatsmittel-'):continue
            url=BASE+'services/MessstellenExtraData/'+f['msttyp']+'?id='+sid+'&file='+str(f['fileNr'])
            points=parse(get(url))
            if not points:continue
            rows.append({'id':'ehyd-archive-'+sid,'hzbnr':sid,'name':meta['Name'],'river':'Mur',
                         'country':'AT','src':'at-ehyd-archive','source_url':url,
                         'attribution':'Hydrographie Österreich / '+str(meta.get('Dienststelle','BMLUK')),
                         'license':'Public download — publisher disclaimer accepted',
                         'historical':True,'resolution':'monthly-mean','unit':'°C',
                         'from':points[0]['month'],'to':points[-1]['month'],'values':points})
            print(sid,meta['Name'],len(points),points[-1]['month'],flush=True)
    if len(rows)!=len(STATIONS):raise ValueError('Incomplete Mur temperature archive; keep previous file')
    return {'fetched_at':datetime.now(timezone.utc).isoformat(),'stations':rows}

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--output',default='ehyd-mur-temperature-history.json')
    args=parser.parse_args();data=collect()
    Path(args.output).write_text(json.dumps(data,ensure_ascii=False,separators=(',',':'))+'\n',encoding='utf-8')
