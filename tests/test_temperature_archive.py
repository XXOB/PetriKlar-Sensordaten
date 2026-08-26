import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from datetime import datetime, timedelta, time
from zoneinfo import ZoneInfo
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
def module(name):
    spec=importlib.util.spec_from_file_location(name,ROOT/(name+'.py'))
    m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m
c=module('fetch_wasserwerte')
feeds=module('temperature_sources')
at=module('austria_sources')

class ArchiveTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.file=Path(self.temp.name)/'archive.json'
        self.patch=patch.object(c,'TEMP_HISTORY_FILE',self.file);self.patch.start()
        self.date=(datetime.now(ZoneInfo('Europe/Berlin'))-timedelta(days=1)).date()
    def tearDown(self):
        self.patch.stop();self.temp.cleanup()
    def station(self,when,value=20):
        timestamp=datetime.combine(self.date,time.fromisoformat(when),ZoneInfo('Europe/Berlin')).isoformat(timespec='minutes')
        return {'id':'s','name':'Test','river':'Main','lat':50,'lon':8,'src':'pegelonline','source_url':'https://example.test/',
                'items':[{'label':'Wassertemperatur','time':timestamp,'value':value}]}
    def read(self):
        return json.loads(self.file.read_text(encoding='utf8'))
    def test_archive_keeps_real_observation_time(self):
        c.update_temperature_archive([self.station('07:45')]);p=self.read();point=p['stations'][0]['values'][0]
        self.assertEqual(p['schema_version'],2);self.assertIn('07:45',point['t']);self.assertIn('04:00',point['slot'])
        self.assertEqual(p['stations'][0]['src'],'pegelonline')
    def test_legacy_time_is_marked_not_falsely_declared_exact(self):
        self.file.write_text(json.dumps({'stations':[{'id':'s','river':'Main','values':[{'t':f'{self.date}T04:00','v':24}]}]}))
        c.update_temperature_archive([]);self.assertEqual(self.read()['stations'][0]['values'][0]['time_quality'],'legacy_4h')
        c.update_temperature_archive([self.station('06:15')]);p=self.read()['stations'][0]['values'][0]
        self.assertIn('06:15',p['t']);self.assertNotIn('time_quality',p)
    def test_newest_observation_per_slot_survives_repeated_runs(self):
        c.update_temperature_archive([self.station('07:45',22),self.station('09:30',21)])
        c.update_temperature_archive([self.station('06:45',19)])
        values=self.read()['stations'][0]['values'];self.assertEqual(len(values),2);self.assertEqual(values[0]['v'],22)
    def test_timezone_is_converted_not_dropped(self):
        self.assertEqual(c.rolling_history_dt('2026-08-26T06:30:00Z'),datetime(2026,8,26,8,30))
        self.assertEqual(c.rolling_history_dt('2026-01-26T06:30:00Z'),datetime(2026,1,26,7,30))
    def test_austrian_ogd_offsets_and_fixed_zrxp_timezone(self):
        self.assertEqual(at._time_text('2026-08-26T06:30:00Z'),'26.08.2026 08:30')
        self.assertEqual(at._time_text('2026-08-26T06:30:00.000Z'),'26.08.2026 08:30')
        self.assertEqual(at._zrxp_time('20260826063000','UTC+1'),'2026-08-26T06:30+01:00')
        self.assertEqual(c.rolling_history_dt(at._zrxp_time('20260826063000','UTC+1')),datetime(2026,8,26,7,30))
    def test_aliases_exclude_wrong_rivers_and_include_lake_proxy(self):
        self.assertEqual(c.temperature_archive_river('Bodensee'),'Bodensee')
        self.assertEqual(c.temperature_archive_river('RHEIN'),'Rhein')
        for name in ['Altrhein','Werra','Main-Donau-Kanal','Neue Donau']:
            self.assertIsNone(c.temperature_archive_river(name))
    def test_future_and_invalid_values_are_not_saved(self):
        s=self.station('07:45',200);c.update_temperature_archive([s]);self.assertEqual(self.read()['stations'],[])
    def test_feed_values_preserve_epoch_timezone_and_reject_empty(self):
        self.assertEqual(feeds.iso(1787725800000),'2026-08-26T06:30+00:00')
        self.assertIsNone(feeds.temperature(''));self.assertIsNone(feeds.temperature(None));self.assertEqual(feeds.temperature('19,4'),19.4)
    def test_feed_failure_keeps_cached_original_dates(self):
        old=self.station('07:45')
        with patch.object(feeds,'pegelonline',side_effect=OSError('test')),patch.object(feeds,'niz',return_value=[]),patch.object(feeds,'hlnug',return_value=[]):
            self.assertEqual(feeds.collect([old]),[old])
    def test_one_missing_po_coordinate_does_not_drop_the_other_stations(self):
        timestamp=datetime.now(ZoneInfo('Europe/Berlin')).isoformat()
        missing={'uuid':'bad','longname':'No coordinates','water':{'longname':'WESER'}}
        good={'uuid':'good','longname':'Valid','water':{'longname':'WESER'},'latitude':52,'longitude':9,'timeseries':[]}
        def fake(url):
            return [missing,good] if 'stations.json?' in url else [{'timestamp':timestamp,'value':20}]
        with patch.object(feeds,'fetch_json',side_effect=fake):
            rows=feeds.pegelonline()
        self.assertEqual([r['id'] for r in rows],['po-good'])

if __name__=='__main__': unittest.main()
