"""The Seine must never be coloured by a daily aggregate or a stale sensor."""
import unittest
from datetime import datetime, timezone
from unittest.mock import patch

import fetch_river_sensor_additions as module


NOW = datetime(2026, 11, 24, 18, 0, tzinfo=timezone.utc).timestamp()
SEINE = {'features': [{'properties': {'name': 'Seine'},
                       'geometry': {'type': 'LineString', 'coordinates': [
                           [1.08, 49.44], [0.92, 49.4025],
                           [0.4672, 49.4769], [0.316, 49.4318]]}}]}


class SynapsesTests(unittest.TestCase):
    def test_only_dated_surface_measurements_and_no_fabricated_current(self):
        recent = int(NOW - 3600)
        old = int(NOW - 6 * 86400)
        payload = {'courbes': [
            {'idlieu': '1', 'idparametre': '9',
             'courbe': f'{old}|11.1;{recent}|10.3;{recent + 1800}|99'},
            {'idlieu': '3', 'idparametre': '9', 'courbe': ''},
            {'idlieu': '4', 'idparametre': '9', 'courbe': ''},
            {'idlieu': '6', 'idparametre': '9', 'courbe': ''},
        ]}
        with patch.object(module, 'fetch_json', return_value=payload) as fetch:
            stations = module.synapses_temperatures(SEINE, NOW)
        self.assertEqual(len(stations), 4)
        self.assertEqual(stations[0]['items'][0]['value'], 10.3)
        self.assertEqual(stations[0]['items'][0]['time'],
                         datetime.fromtimestamp(recent, timezone.utc).isoformat())
        self.assertEqual(len(stations[0]['history']['Wassertemperatur']), 2)
        self.assertIsNone(stations[1]['items'][0]['value'])
        self.assertEqual(stations[1]['items'][0]['quality'], 'unavailable')
        self.assertIn('parametres=9', fetch.call_args.args[0])
        self.assertNotIn('8', [s['id'].split('-')[-1] for s in stations])  # Balise A bottom

    def test_broken_response_does_not_erase_previous_feed(self):
        with patch.object(module, 'fetch_json', return_value={'courbes': []}):
            with self.assertRaisesRegex(RuntimeError, 'incomplete'):
                module.synapses_temperatures(SEINE, NOW)


if __name__ == '__main__':
    unittest.main()
