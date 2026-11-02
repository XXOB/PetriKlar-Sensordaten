"""French gauge colours use only comparable recent readings from the same gauge."""
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import fetch_river_sensor_additions as feeds
from build_sensor_packets import current_level_packet


NOW = datetime(2026, 11, 2, 12, tzinfo=timezone.utc).timestamp()


def readings(days=29, changed_gauge=False):
    rows = []
    for day in range(days):
        date = datetime.fromtimestamp(NOW, timezone.utc) - timedelta(days=day)
        for hour in (0, 1, 2, 3, 4):
            rows.append({'code_site': 'A1',
                         'code_station': 'new' if not changed_gauge or day < 7 else 'old',
                         'code_systeme_alti_serie': 31,
                         'date_obs': date.replace(hour=hour).isoformat(),
                         'resultat_obs': 1000 + day * 10})
    return {'data': rows, 'next': None}


class FrenchLevelReferenceTests(unittest.TestCase):
    def test_daily_medians_use_one_gauge_and_mm_to_cm(self):
        with patch.object(feeds, 'fetch_json', return_value=readings()) as fetch:
            band = feeds.french_reference_band('A1', NOW)
        self.assertEqual(band['days'], 29)
        self.assertEqual(band['unit'], 'cm')
        self.assertEqual(band['center'], 114)
        self.assertEqual(band['code_station'], 'new')
        self.assertIn('date_debut=', fetch.call_args.args[0])

    def test_incomplete_series_and_changed_datum_remain_uncoloured(self):
        for payload in (readings(15), readings(changed_gauge=True),
                        {**readings(), 'next': 'https://example.invalid/next'}):
            with patch.object(feeds, 'fetch_json', return_value=payload):
                self.assertIsNone(feeds.french_reference_band('A1', NOW))

    def test_paginated_high_frequency_series_uses_all_days(self):
        series = readings()
        first = {'data': series['data'][:75], 'next': 'https://example.test/page2'}
        second = {'data': series['data'][75:], 'next': None}
        with patch.object(feeds, 'fetch_json', side_effect=[first, second]) as fetch:
            band = feeds.french_reference_band('A1', NOW)
        self.assertEqual(band['days'], 29)
        self.assertEqual(fetch.call_count, 2)
        self.assertEqual(fetch.call_args_list[1].args[0], first['next'])

    def test_recent_cache_is_reused_but_changed_gauge_invalidates_it(self):
        band = {'method': 'fr-hubeau-recent-30d-daily-median-p10-p50-p90',
                'unit': 'cm', 'low': 90, 'center': 100, 'high': 110, 'days': 29,
                'fetched_at': datetime.fromtimestamp(NOW - 3600, timezone.utc).isoformat(),
                'code_station': 'old', 'code_systeme_alti_serie': 31}
        old = [{'id': 'A1', 'src': 'fr-hubeau', 'reference_band': band}]
        station = {'id': 'A1', 'code_station': 'old', 'code_systeme_alti_serie': 31}
        with patch.object(feeds, 'french_reference_band') as fetch:
            feeds.attach_french_reference_bands([station], old, NOW)
            fetch.assert_not_called()
        self.assertEqual(station['reference_band'], band)
        changed = {'id': 'A1', 'code_station': 'new', 'code_systeme_alti_serie': 31}
        with patch.object(feeds, 'french_reference_band', return_value=None) as fetch:
            feeds.attach_french_reference_bands([changed], old, NOW)
            fetch.assert_called_once()
        self.assertNotIn('reference_band', changed)

    def test_compact_first_paint_packet_keeps_french_reference(self):
        band = {'method': 'fr-hubeau-recent-30d-daily-median-p10-p50-p90',
                'low': 80, 'center': 100, 'high': 140, 'unit': 'cm', 'days': 29}
        station = {'id': 'A1', 'river': 'Maas', 'source': 'fr-hubeau',
                   'current': {'t': '2026-11-02T11:30:00Z', 'v': 105},
                   'reference_band': band, 'history': [{'t': '2026-11-01T11:30:00Z', 'v': 99}]}
        packet = current_level_packet({'schema': 1, 'stations': [station]},
                                      '2026-11-02T12:00:00Z')
        self.assertEqual(packet['stations'][0]['reference_band'], band)
        self.assertNotIn('history', packet['stations'][0])

    def test_one_timed_out_hubeau_batch_keeps_older_stations(self):
        rivers = ('Maas', 'Mosel', 'Rhône')
        geometry = {'features': [
            {'properties': {'name': river},
             'geometry': {'type': 'LineString',
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
             patch.object(feeds, 'attach_french_reference_bands'):
            # Build the old catalogue first; the feed itself is unavailable.
            for label in ('La Meuse', 'La Moselle', 'Le Rhône'):
                sites(label)
            result = feeds.france_levels(geometry, NOW, old)
        self.assertEqual(len(result), 12)
        self.assertEqual({s['river'] for s in result}, set(rivers))


if __name__ == '__main__':
    unittest.main()
