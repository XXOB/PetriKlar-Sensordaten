import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools' if (ROOT / 'tools/hungary_sources.py').exists() else ROOT))
import hungary_sources as H

NOW = datetime(2026, 10, 27, 8, tzinfo=timezone.utc)
META = dict(Tsz=1026, Nev='Budapest', MdrNev='Duna', Lat=47.5, Lon=19.04)


def series(code, values):
    return {code: [{'ItemId': 1026, 'TsItemList': values}]}


class HungaryTests(unittest.TestCase):
    def test_negative_gauge_is_not_missing(self):
        rows = H.parse([META], series(68, [{'UTCTime': '2026-10-27T07:00:00Z', 'Adat': -82}]), NOW)
        self.assertEqual(rows[0]['items'][0]['value'], -82)
        self.assertEqual(rows[0]['items'][0]['unit'], 'cm')

    def test_bottom_and_surface_are_not_mixed(self):
        p = {'UTCTime': '2026-10-27T07:00:00Z', 'Adat': 13.4}
        rows = H.parse([META], {**series(89, [p]), **series(85, [{**p, 'Adat': 1}])}, NOW)
        self.assertEqual(rows[0]['items'][0]['value'], 13.4)
        self.assertEqual(rows[0]['temperature_parameter'], 89)
        self.assertEqual(rows[0]['sampling_horizon'], 'near river bed')

    def test_surface_fallback_and_mura(self):
        rows = H.parse([{**META, 'MdrNev': 'Mura'}], series(85, [{'UTCTime': '2026-10-27T06:00:00Z', 'Adat': 11}]), NOW)
        self.assertEqual(rows[0]['river'], 'Mur')
        self.assertEqual(rows[0]['temperature_parameter'], 85)

    def test_missing_nan_future_and_old_temperature_are_not_live(self):
        rows = H.parse([META], series(89, [
            {'UTCTime': '2026-10-27T07:00:00Z'},
            {'UTCTime': '2026-10-27T07:00:00Z', 'Adat': None},
            {'UTCTime': '2026-10-27T07:00:00Z', 'Adat': float('nan')},
            {'UTCTime': '2026-10-28T07:00:00Z', 'Adat': 12},
            {'UTCTime': '2026-10-20T07:00:00Z', 'Adat': 12}]), NOW)
        self.assertEqual(rows, [])

    def test_utc_does_not_get_shifted_at_dst(self):
        points = H.points([{'UTCTime': t, 'Adat': 12} for t in
            ['2026-10-25T00:30:00Z', '2026-10-25T01:30:00Z']], NOW, True)
        self.assertEqual(len(points), 2)
        self.assertEqual(points[1]['t'], '2026-10-25T01:30:00+00:00')

    def test_tributary_not_assigned_to_main_danube(self):
        self.assertEqual(H.parse([{**META, 'MdrNev': 'Mosoni-Duna'}], series(68,
            [{'UTCTime': '2026-10-27T07:00:00Z', 'Adat': 40}]), NOW), [])

    def test_missing_coordinates_do_not_create_a_false_map_location(self):
        for coordinates in [dict(Lat=None), dict(Lon=float('nan')), dict(Lat=190)]:
            self.assertEqual(H.parse([{**META, **coordinates}], series(68,
                [{'UTCTime': '2026-10-27T07:00:00Z', 'Adat': 40}]), NOW), [])

    def test_public_token_and_batched_queries_not_stored(self):
        calls = []
        def request(url, body=None, token=None):
            calls.append((url, body, token))
            if url.endswith('/token'):
                return {'access_token': 'temporary-public-token'}
            if 'InternetVmo' in url:
                return [META]
            return []
        original = H.request
        try:
            H.request = request
            self.assertEqual(H.collect(), [])
        finally:
            H.request = original
        self.assertEqual(len(calls), 5)
        self.assertEqual([c[1]['adatFajtaKod'] for c in calls[2:]], [68, 89, 85])
        self.assertTrue(all(c[2] == 'temporary-public-token' for c in calls[1:]))


if __name__ == '__main__':
    unittest.main()
