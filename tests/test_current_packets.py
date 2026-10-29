"""The first-paint files must never carry week/month histories."""
import unittest

from build_sensor_packets import current_level_packet, current_temperature_packet


class CurrentPacketTests(unittest.TestCase):
    def test_temperature_keeps_real_items_and_metadata_without_history(self):
        stations = [{'id': 't1', 'river': 'Rhein', 'lat': 50.0,
                     'items': [{'label': 'Wassertemperatur', 'value': 14.2,
                                'time': '2026-10-29T10:00:00Z'}],
                     'history': {'Wassertemperatur': [{'t': 'old', 'v': 13.1}]}}]
        packet = current_temperature_packet(stations, 'source-time', 'build-time')
        self.assertEqual(packet['data']['stations'][0]['items'], stations[0]['items'])
        self.assertEqual(packet['data']['stations'][0]['river'], 'Rhein')
        self.assertNotIn('history', packet['data']['stations'][0])
        self.assertIsNone(packet['archive'])
        self.assertEqual(packet['generated_at'], 'build-time')
        self.assertTrue(packet['initialOnly'])
        self.assertIn('history', stations[0], 'The full week packet must stay intact')

    def test_level_keeps_native_unit_references_and_current(self):
        level = {'schema': 1, 'fetched_at': 'source-time',
                 'stations': [{'id': 'g1', 'river': 'Donau', 'unit': 'cm',
                               'references': {'MW': 230},
                               'current': {'t': '2026-10-29T10:00:00Z', 'v': 235},
                               'history': [{'t': 'old', 'v': 232}]}]}
        packet = current_level_packet(level, 'build-time')
        self.assertEqual(packet['stations'][0]['current'], level['stations'][0]['current'])
        self.assertEqual(packet['stations'][0]['references'], {'MW': 230})
        self.assertNotIn('history', packet['stations'][0])
        self.assertEqual(packet['generated_at'], 'build-time')
        self.assertTrue(packet['initialOnly'])


if __name__ == '__main__':
    unittest.main()
