from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from regional_water_levels import parse_statistics, parse_measurements, german_time, bayern_inventory, hvz_stations, same_site


class RegionalWaterLevelTests(unittest.TestCase):
    def test_shared_sites_with_different_official_numbers(self):
        site = dict(name='TRUNSTADT', river='Main', lat=49.93, lon=10.76)
        self.assertTrue(same_site(site, dict(site, name='Trunstadt', lon=10.761)))
        self.assertFalse(same_site(site, dict(site, river='Donau')))
        self.assertFalse(same_site(site, dict(site, lat=50.0)))

    def test_statistics_select_annual_not_summer_or_winter(self):
        html = '<h2>Hauptwerte (1980 - 2025)</h2><table>'
        for key, values in [('MNW', [78, 67, 64]), ('MW', [159, 134, 147]), ('MHW', [445, 404, 474])]:
            html += '<tr>' + ''.join('<td>' + str(v) + '</td>' for v in [key, *values, 'cm']) + '</tr>'
        refs = parse_statistics(html + '</table>')
        self.assertEqual([refs[k]['value'] for k in ('MNW', 'MW', 'MHW')], [64, 147, 474])
        self.assertEqual(refs['MW']['timespanStart'], '1980')
        self.assertEqual(parse_statistics(html.replace('cm', 'm³/s')), {})

    def test_forecasts_are_not_readings_and_negative_values_are_preserved(self):
        html = '<th>Wasserstand <b>cm</b> über Pegelnullpunkt (394,78 m NN)</th>'
        html += '<tr><td>07.09.2026 18:00</td><td><span style="color:green">5</span></td></tr>'
        html += '<tr><td>07.09.2026 17:00</td><td><span style="color:blue">-6</span></td></tr>'
        html += '<tr><td>07.09.2026 16:00</td><td>-5 - 5</td></tr>'
        self.assertEqual(parse_measurements(html), [{'t': '2026-09-07T17:00:00+02:00', 'v': -6}])
        with self.assertRaises(ValueError):
            parse_measurements(html.replace('<b>cm</b>', '<b>m</b>'))

    def test_winter_and_summer_times(self):
        self.assertTrue(german_time('07.01.2026 17:00').endswith('+01:00'))
        self.assertTrue(german_time('07.09.2026 17:00 MESZ').endswith('+02:00'))

    def test_inventory_keeps_target_rivers_but_excludes_discharge_only_gauges(self):
        def row(river, suffix=''):
            return '<tr><td><a href="https://www.hnd.bayern.de/pegel/donau_bis_kelheim/test-123'+suffix+'">Test</a></td><td>'+river+'</td><td>Datum</td><td>10</td></tr>'
        river_stations = bayern_inventory(row('Donau') + row('Donau', '/abfluss') + row('Lech') + row('Sächsische Saale'))
        self.assertEqual({station[2] for station in river_stations}, {'Donau', 'Lech', 'Sächsische Saale'})
        main = bayern_inventory(row('Main') + row('Roter Main') + row('Weißer Main'))
        self.assertEqual(len(main), 1)
        self.assertEqual(main[0][2], 'Main')

    def test_hvz_import_keeps_neckar_upper_reach_stations_and_readings(self):
        row = [''] * 57
        row[0], row[1], row[2] = '00117', 'Horb', 'Neckar'
        row[4], row[5], row[6] = '31', 'cm', '13.10.2026 09:00 MESZ'
        row[20], row[21] = 8.707258, 48.446955
        row[40], row[43] = 72, 31
        blank_row = row.copy()
        blank_row[0], blank_row[1], blank_row[4], blank_row[6] = '00000', 'No reading', '--', '--'
        stations = hvz_stations(repr(row) + '\n' + repr(blank_row), {}, 1791882000, lambda points, _now: points)
        self.assertEqual(len(stations), 1)
        station = stations[0]
        self.assertEqual((station['id'], station['river']), ('hvz-00117', 'Neckar'))
        self.assertEqual((station['lat'], station['lon']), (48.446955, 8.707258))
        self.assertEqual(station['current']['v'], 31)
        self.assertEqual(station['references']['MW']['value'], 72)
        self.assertIsNone(station['reference_issue'])
