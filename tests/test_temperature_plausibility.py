import copy
import unittest
from datetime import datetime, timedelta, timezone

from temperature_plausibility import screen


def row(identifier, source, river, lat, lon, value, when, history=None):
    return {'id': identifier, 'src': source, 'river': river, 'lat': lat, 'lon': lon,
            'items': [{'label': 'Wassertemperatur', 'value': value, 'time': when}],
            'history': {'Wassertemperatur': history or []}}


class TemperaturePlausibilityTest(unittest.TestCase):
    def test_day_long_plateau_after_jump_is_quarantined_but_raw_copy_survives(self):
        start = datetime(2026, 10, 27, 12, tzinfo=timezone.utc)
        history = [{'t': start.isoformat(), 'v': 11.0}]
        history += [{'t': (start + timedelta(hours=6*i)).isoformat(), 'v': 25.7}
                    for i in range(1, 7)]
        station = row('rischmuehle', 'pegelonline', 'Saale', 51.3502, 12.0023,
                      25.7, history[-1]['t'], history)
        original = copy.deepcopy(station)
        rejected = screen([station])
        self.assertIn('rischmuehle', rejected)
        self.assertTrue(station['temperature_quarantined'])
        self.assertEqual(station['items'][0]['value'], None)
        self.assertEqual(station['history']['Wassertemperatur'], history[:1])
        self.assertEqual(original['items'][0]['value'], 25.7)

    def test_two_agreeing_independent_sources_reject_ocr_not_direct_sensors(self):
        time = '2026-10-31T06:00:00+01:00'
        rows = [row('lfu', 'brandenburg', 'Havel', 52.4006, 13.07308, 17.6, time),
                row('po', 'pegelonline', 'Havel', 52.399887, 13.072071, 11.3, time),
                row('undine', 'undine', 'Havel', 52.4006, 13.07308, 11.6,
                    '31.10.2026 03:00')]
        rejected = screen(rows)
        self.assertEqual(set(rejected), {'lfu'})
        self.assertTrue(rows[0]['temperature_quarantined'])
        self.assertFalse(rows[1]['temperature_quarantined'])
        self.assertFalse(rows[2]['temperature_quarantined'])

    def test_thirteen_hour_old_wittenberg_is_not_quarantined(self):
        station = row('wittenberg', 'undine', 'Elbe', 51.87, 12.65, 10.8,
                      '2026-10-30T18:00:00+01:00')
        self.assertEqual(screen([station]), {})
        self.assertFalse(station['temperature_quarantined'])
        self.assertEqual(station['items'][0]['value'], 10.8)


if __name__ == '__main__':
    unittest.main()
