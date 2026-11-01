import json
import os
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import collection_control as C
from country_pipeline import combine
from build_sensor_packets import current_level_packet

NOW = datetime(2026, 11, 1, 9, tzinfo=timezone.utc).timestamp()


class CollectionControlTests(unittest.TestCase):
    def test_gate_deduplicates_only_actual_successful_collections(self):
        previous = {'status': 'success', 'started_at': C.iso(NOW-1800)}
        self.assertFalse(C.should_collect(previous, NOW))
        self.assertTrue(C.should_collect(previous, NOW, force=True))
        self.assertTrue(C.should_collect({**previous, 'status': 'failure'}, NOW))
        self.assertTrue(C.should_collect({**previous, 'started_at': C.iso(NOW-3600)}, NOW))
        self.assertTrue(C.should_collect({**previous, 'started_at': C.iso(NOW+7200)}, NOW))
        self.assertTrue(C.should_collect({}, NOW))

    def test_repackaging_does_not_change_source_update_time(self):
        source = '2026-11-01T01:00:00+00:00'
        result = combine([{'updated': source, 'stations': []}])
        self.assertEqual(result['updated'], source)
        second = combine([result])
        self.assertEqual(second['updated'], source)
        self.assertEqual(second['source_updated_at'], source)
        self.assertIn('generated_at', second)

    def test_invalid_timestamps_are_not_fresh(self):
        for value in [None, '', 'not a date', float('nan'), float('inf'), True]:
            self.assertIsNone(C.timestamp(value))

    def test_health_uses_real_measurement_times_not_publication_time(self):
        levels = [{'id':'old','src':'agency','current':{'t':C.iso(NOW-7*3600),'v':123}},
                  {'id':'fresh','src':'agency','current':{'t':C.iso(NOW-900),'v':124}},
                  {'id':'future','current':{'t':C.iso(NOW+7200),'v':124}},
                  {'id':'sentinel','current':{'t':C.iso(NOW),'v':-777}}]
        health = C.measurement_health(levels, 'level', NOW)
        self.assertEqual((health['fresh'],health['stale'],health['future'],health['missing']), (1,1,1,1))
        self.assertEqual(health['latest_measurement_at'], C.iso(NOW-900))
        self.assertEqual(health['sources']['agency']['fresh'], 1)

    def test_temperature_quarantine_and_24_hour_policy_are_preserved(self):
        stations = [{'id':'a','items':[{'label':'Wassertemperatur','value':'8,1','time':C.iso(NOW-20*3600)}]},
                    {'id':'b','temperature_quarantined':True,'items':[{'label':'Wassertemperatur','value':35,'time':C.iso(NOW)}]}]
        health = C.measurement_health(stations, 'temperature', NOW)
        self.assertEqual(health['fresh'], 1)
        self.assertEqual(health['missing'], 1)
        self.assertEqual(health['freshness_limit_hours'], 24)

    def test_new_packet_generation_does_not_relabel_old_level_measurements(self):
        time = C.iso(NOW-7*3600)
        packet = current_level_packet({'stations':[{'id':'a','current':{'t':time,'v':123}}]}, C.iso(NOW))
        self.assertEqual(packet['stations'][0]['current']['t'], time)
        self.assertEqual(packet['measurement_status']['latest_measurement_at'], time)
        self.assertNotEqual(packet['measurement_status']['latest_measurement_at'], packet['generated_at'])

    def test_start_does_not_dirty_tracked_status_before_intermediate_commits(self):
        with tempfile.TemporaryDirectory() as folder:
            status, running = Path(folder)/'collection-status.json', Path(folder)/'running.json'
            status.write_text('{"status":"success","run_id":"previous"}', encoding='utf8')
            before = status.read_bytes()
            with patch.dict(os.environ, {'GITHUB_RUN_ID':'123'}):
                with patch('sys.argv', ['control','start','--file',str(status),'--running-file',str(running)]):
                    C.main()
                self.assertEqual(status.read_bytes(), before)
                self.assertEqual(C.read(running)['run_id'], '123')
                with patch('sys.argv', ['control','finish','--file',str(status),'--running-file',str(running)]):
                    C.main()
            self.assertEqual(C.read(status)['run_id'], '123')
            self.assertEqual(C.read(status)['started_at'], C.read(running)['started_at'])
            self.assertEqual(C.read(status)['status'], 'success')

    def test_skipped_gate_run_does_not_publish_a_fake_update(self):
        with tempfile.TemporaryDirectory() as folder:
            status, event, output = [Path(folder)/name for name in ['status.json','event.json','output']]
            status.write_text('{"run_id":"123"}', encoding='utf8')
            event.write_text('{"workflow_run":{"name":"Messwerte","id":456}}', encoding='utf8')
            with patch.dict(os.environ, {'GITHUB_EVENT_PATH':str(event),'GITHUB_OUTPUT':str(output)}):
                with patch('sys.argv',['control','publish-check','--file',str(status)]): C.main()
            self.assertIn('ready=false', output.read_text())


if __name__ == '__main__':
    unittest.main()
