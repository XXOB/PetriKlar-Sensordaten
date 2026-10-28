"""Regressions for independent snapshot merge and offline station metadata."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from datetime import datetime,timezone,timedelta
ROOT=Path(__file__).resolve().parents[1]
SCRIPT=ROOT/'tools/build_sensor_packets.py'
if not SCRIPT.exists(): SCRIPT=ROOT/'build_sensor_packets.py'
spec=importlib.util.spec_from_file_location('packets_recovery',SCRIPT)
builder=importlib.util.module_from_spec(spec);spec.loader.exec_module(builder)

class SnapshotRecoveryTests(unittest.TestCase):
    def test_stale_chart_cannot_replace_newer_temperature_and_offline_gauge_stays(self):
        now=datetime.now(timezone.utc).replace(microsecond=0)
        new=now.isoformat();old=(now-timedelta(days=3)).isoformat()
        row={'id':'bb-potsdam','river':'Havel','name':'Potsdam','lat':52.4,'lon':13.07,
             'items':[{'label':'Wassertemperatur','value':11.8,'unit':'°C','time':new}],
             'history':{'Wassertemperatur':[{'t':new,'v':11.8}]},'data_source_url':'https://current.example/value'}
        stale={**row,'items':[{'label':'Wassertemperatur','value':12.4,'unit':'°C','time':old}],
               'history':{'Wassertemperatur':[{'t':old,'v':12.4}]},'data_source_url':'https://stale.example/chart'}
        hof={'id':'gkd-hof','river':'Sächsische Saale','name':'Hof','lat':50.31,'lon':11.91,
             'params':{'wt':True},'items':[],'history':{}}
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp); data=root/'assets/data';data.mkdir(parents=True)
            (root/'wasserwerte.json').write_text(json.dumps({'updated':new,'stations':[row,hof]}),encoding='utf8')
            (data/'lfu-brandenburg-current.json').write_text(json.dumps({'updated':new,'stations':[stale]}),encoding='utf8')
            builder.build(root,root/'packets')
            result=json.loads((root/'packets/temperature-map.json').read_text(encoding='utf8'))
            rows={s['id']:s for s in result['data']['stations']}
            self.assertEqual(rows['bb-potsdam']['items'][0]['time'],new)
            self.assertEqual(rows['bb-potsdam']['items'][0]['value'],11.8)
            self.assertEqual(len(rows['bb-potsdam']['history']['Wassertemperatur']),2)
            self.assertEqual(rows['bb-potsdam']['data_source_url'],row['data_source_url'])
            self.assertIsNone(rows['gkd-hof']['items'][0]['value'])
            self.assertIsNone(rows['gkd-hof']['items'][0]['time'])

if __name__=='__main__':unittest.main()
