"""UK readings must reach the first-paint packet, not only a later side feed."""
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from build_sensor_packets import build


class UkTemperaturePacketTests(unittest.TestCase):
    def test_british_stations_are_in_current_week_and_station_packets(self):
        now = datetime.now(timezone.utc).replace(microsecond=0)
        time = (now-timedelta(minutes=20)).isoformat()
        rows = [{'id': 'ea-water-temp-THAMES', 'name': 'Thames test', 'river': 'Thames',
                 'src': 'environment-agency', 'country': 'GB', 'license': 'OGL-3.0',
                 'license_url': 'https://www.nationalarchives.gov.uk/doc/open-government-licence/version/3/',
                 'lat': 51.482298, 'lon': -.250833,
                 'items': [{'label':'Wassertemperatur','value':13.7,'unit':'°C','time':time}],
                 'history': {'Wassertemperatur':[{'t':time,'v':13.7}]}},
                {'id':'ea-water-temp-OLD','name':'Old thermometer','river':'Tyne',
                 'src':'environment-agency','country':'GB','license':'OGL-3.0',
                 'items':[{'label':'Wassertemperatur','value':8,'time':(now-timedelta(hours=25)).isoformat()}]}]
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root/'assets/data').mkdir(parents=True)
            (root/'assets/data/uk-river-temperatures.json').write_text(
                json.dumps({'updated':now.isoformat(),'stations':rows}), encoding='utf8')
            # Newer UK copy wins, including its real source time and provenance.
            old = {**rows[0], 'source_url':'old-mirror', 'items':[
                {'label':'Wassertemperatur','value':10,'time':(now-timedelta(hours=2)).isoformat()}]}
            (root/'wasserwerte.json').write_text(json.dumps({'stations':[old]}), encoding='utf8')
            build(root,root/'packets')
            current=json.loads((root/'packets/temperature-map-current.json').read_text())
            week=json.loads((root/'packets/temperature-map.json').read_text())
            for packet in (current,week):
                by_id={s['id']:s for s in packet['data']['stations']}
                self.assertEqual(set(by_id), {s['id'] for s in rows})
                self.assertEqual(by_id[rows[0]['id']]['items'][0]['value'],13.7)
                self.assertEqual(by_id[rows[0]['id']]['items'][0]['time'],time)
            self.assertNotIn('history',current['data']['stations'][0])
            self.assertEqual(current['updated'],now.isoformat())
            self.assertEqual(current['measurement_status']['sources']['environment-agency']['fresh'],1)
            index=json.loads((root/'packets/index.json').read_text())
            entry=next(e for e in index['stations'] if e['id']==rows[0]['id'])
            station=json.loads((root/'packets'/entry['path']).read_text())
            self.assertEqual(station['river'],'Thames')
            self.assertEqual(station['history']['Wassertemperatur'][-1]['v'],13.7)


if __name__ == '__main__':
    unittest.main()
