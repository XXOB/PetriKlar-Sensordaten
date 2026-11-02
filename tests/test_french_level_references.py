"""French level colours require a long-term, same-gauge historical H reference."""
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import fetch_river_sensor_additions as feeds
from build_sensor_packets import current_level_packet


NOW = datetime(2026, 11, 2, 12, tzinfo=timezone.utc).timestamp()
STATION = {'id': 'A1', 'src': 'fr-hubeau', 'code_station': 'A123456001',
           'code_systeme_alti_serie': 31}
METHOD = 'fr-hydroportail-daily-median-p10-p50-p90'


def metadata(activated='2013-11-18T00:00:00Z'):
    return {'data': [{'code_station': STATION['code_station'],
                      'date_activation_ref_alti_station': activated}]}


def readings(days=365):
    start = datetime(2025, 1, 1, tzinfo=timezone.utc)
    return [{'t': (start + timedelta(days=i, hours=h)).isoformat(),
             'v': 1000 + i, 'q': 20, 's': 12}
            for i in range(days) for h in (0, 4, 8, 12, 16)]


def series(rows, unit='mm'):
    return {'series': {'code': STATION['code_station'], 'metric': 'H',
            'unit': unit, 'statuses': 'pre_validated_and_validated', 'data': rows}}


class Response:
    def __init__(self, payload):
        import json
        self.body = json.dumps(payload).encode()
    def __enter__(self): return self
    def __exit__(self, *args): return False
    def read(self, _limit): return self.body


class FrenchLevelReferenceTests(unittest.TestCase):
    def test_historical_daily_medians_are_cm_and_require_300_days(self):
        with patch.object(feeds, 'fetch_json', return_value=metadata()), \
             patch.object(feeds.urllib.request, 'urlopen', return_value=Response(series(readings()))) as fetch:
            baseline = feeds.french_history_baseline(STATION, NOW)
        self.assertEqual(baseline['method'], METHOD)
        self.assertEqual(baseline['days'], 365)
        self.assertEqual(baseline['unit'], 'cm')
        self.assertAlmostEqual(baseline['center'], 118.2)
        self.assertEqual(baseline['code_station'], STATION['code_station'])
        self.assertIn('hydro.eaufrance.fr', fetch.call_args.args[0].full_url)
        self.assertIn('variableType', fetch.call_args.args[0].full_url)

    def test_missing_or_changed_datum_and_short_history_stay_gray(self):
        self.assertIsNone(feeds.french_history_baseline({**STATION,
                          'code_systeme_alti_serie': None}, NOW))
        with patch.object(feeds, 'fetch_json', return_value=metadata('2026-04-01T00:00:00Z')):
            self.assertIsNone(feeds.french_history_baseline(STATION, NOW))
        for payload in (series(readings(299)), series(readings(), 'm'),
                        {**series(readings()), 'series': {**series(readings())['series'], 'code': 'OTHER'}}):
            with patch.object(feeds, 'fetch_json', return_value=metadata()), \
                 patch.object(feeds.urllib.request, 'urlopen', return_value=Response(payload)):
                self.assertIsNone(feeds.french_history_baseline(STATION, NOW))

    def test_quality_filter_and_one_reading_per_day(self):
        rows = readings()[::5] + [{**readings(1)[0], 't': '2025-12-31T18:00:00Z',
                              'v': 99000, 'q': 16}]
        with patch.object(feeds, 'fetch_json', return_value=metadata()), \
             patch.object(feeds.urllib.request, 'urlopen', return_value=Response(series(rows))):
            baseline = feeds.french_history_baseline(STATION, NOW)
        self.assertEqual(baseline['days'], 365)
        self.assertAlmostEqual(baseline['center'], 118.2)

    def test_old_gap_does_not_poison_a_later_complete_year(self):
        rows = [{'t': '2023-12-01T12:00:00Z', 'v': 99000, 'q': 20, 's': 12}] + readings()
        with patch.object(feeds, 'fetch_json', return_value=metadata()), \
             patch.object(feeds.urllib.request, 'urlopen', return_value=Response(series(rows))):
            baseline = feeds.french_history_baseline(STATION, NOW)
        self.assertEqual(baseline['start'], '2025-01-01')
        self.assertEqual(baseline['days'], 365)

    def test_cache_reused_only_for_same_gauge_and_datum_and_old_band_removed(self):
        baseline = {'method': METHOD, 'unit': 'cm', 'days': 365,
                    'code_station': STATION['code_station'], 'code_systeme_alti_serie': 31,
                    'fetched_at': datetime.fromtimestamp(NOW - 3600, timezone.utc).isoformat()}
        old = [{'id': 'A1', 'src': 'fr-hubeau', 'history_baseline': baseline,
                'reference_band': {'method': 'fr-hubeau-recent-30d-daily-median-p10-p50-p90'}}]
        row = {**STATION, 'reference_band': old[0]['reference_band']}
        with patch.object(feeds, 'french_history_baseline') as fetch:
            feeds.attach_french_history_baselines([row], old, NOW)
            fetch.assert_not_called()
        self.assertEqual(row['history_baseline'], baseline)
        self.assertNotIn('reference_band', row)
        monthly_old = {**baseline, 'fetched_at': datetime.fromtimestamp(
            NOW - 31 * 86400, timezone.utc).isoformat()}
        month_row = {**STATION}
        with patch.object(feeds, 'french_history_baseline', return_value=None) as fetch:
            feeds.attach_french_history_baselines([month_row],
                [{**STATION, 'history_baseline': monthly_old}], NOW)
            fetch.assert_called_once()
        self.assertEqual(month_row['history_baseline'], monthly_old)
        changed = {**STATION, 'code_station': 'OTHER'}
        with patch.object(feeds, 'french_history_baseline', return_value=None) as fetch:
            feeds.attach_french_history_baselines([changed], old, NOW)
            fetch.assert_called_once()
        self.assertNotIn('history_baseline', changed)

    def test_compact_packet_keeps_long_term_reference_not_history(self):
        baseline = {'method': METHOD, 'days': 365, 'unit': 'cm'}
        station = {**STATION, 'current': {'t': '2026-11-02T11:30:00Z', 'v': 105},
                   'history_baseline': baseline,
                   'history': [{'t': '2026-11-01T11:30:00Z', 'v': 99}]}
        packet = current_level_packet({'schema': 1, 'stations': [station]},
                                      '2026-11-02T12:00:00Z')
        self.assertEqual(packet['stations'][0]['history_baseline'], baseline)
        self.assertNotIn('history', packet['stations'][0])

    def test_timed_out_hubeau_batch_keeps_older_stations(self):
        rivers = ('Maas', 'Mosel', 'Rhône')
        geometry = {'features': [
            {'properties': {'name': river}, 'geometry': {'type': 'LineString',
             'coordinates': [[5.0 + index, 48.0], [5.2 + index, 48.0]]}}
            for index, river in enumerate(rivers)]}
        old = []
        def sites(label):
            index = ('La Meuse', 'La Moselle', 'Le Rhône').index(label)
            river = rivers[index]
            records = []
            for number in range(4):
                code = f'{river}-{number}'
                records.append({'code_site': code, 'statut_site': 1,
                                'longitude_site': 5.1 + index, 'latitude_site': 48.0})
                old.append({'id': code, 'src': 'fr-hubeau', 'river': river,
                            'current': {'t': '2026-11-02T11:00:00Z', 'v': 100}})
            return {'data': records}
        with patch.object(feeds, 'hubeau_sites', side_effect=sites), \
             patch.object(feeds, 'fetch_json', side_effect=TimeoutError('temporary')), \
             patch.object(feeds, 'attach_french_history_baselines'):
            for label in ('La Meuse', 'La Moselle', 'Le Rhône'):
                sites(label)
            result = feeds.france_levels(geometry, NOW, old)
        self.assertEqual(len(result), 12)
        self.assertEqual({s['river'] for s in result}, set(rivers))


if __name__ == '__main__':
    unittest.main()
