"""Hub'Eau catalogue stations are not necessarily live thermometers."""
import unittest
import json
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import build_sensor_packets
import fetch_river_sensor_additions as module


NOW = datetime(2026, 10, 31, 8, 0, tzinfo=timezone.utc).timestamp()
GEOJSON = {'features': [
    {'properties': {'name': 'Maas'},
     'geometry': {'type': 'LineString', 'coordinates': [[5.0, 49.0], [5.2, 49.0]]}},
    {'properties': {'name': 'Mosel'},
     'geometry': {'type': 'LineString', 'coordinates': [[6.0, 49.0], [6.2, 49.0]]}},
]}
CATALOGUE = {'data': [
    {'code_station': 'meuse', 'libelle_station': 'La Meuse test',
     'libelle_cours_eau': 'La Meuse', 'longitude': 5.1, 'latitude': 49.0},
    {'code_station': 'moselle', 'libelle_station': 'La Moselle test',
     'libelle_cours_eau': 'La Moselle', 'longitude': 6.1, 'latitude': 49.0},
    {'code_station': 'tributary', 'libelle_cours_eau': 'La Meuse',
     'longitude': 5.1, 'latitude': 49.1},
]}


class FrenchTemperatureTests(unittest.TestCase):
    def test_current_measured_values_only_and_correct_river(self):
        readings = {'data': [
            {'code_station': 'meuse', 'date_mesure_temp': '2026-10-31',
             'heure_mesure_temp': '08:30:00', 'resultat': 12.3, 'symbole_unite': '°C'},
            {'code_station': 'moselle', 'date_mesure_temp': '2026-10-31',
             'heure_mesure_temp': '09:00:00', 'resultat': 13.4, 'symbole_unite': '°C'},
            {'code_station': 'meuse', 'date_mesure_temp': '2025-10-31',
             'heure_mesure_temp': '09:00:00', 'resultat': 30, 'symbole_unite': '°C'},
            {'code_station': 'moselle', 'date_mesure_temp': '2026-10-31',
             'heure_mesure_temp': '09:10:00', 'resultat': 99, 'symbole_unite': '°C'},
        ]}
        with patch.object(module, 'fetch_json', side_effect=[CATALOGUE, readings]) as fetch:
            stations, candidates = module.france_temperatures(GEOJSON, NOW)
        self.assertEqual(candidates, 2)
        self.assertEqual({row['river'] for row in stations}, {'Maas', 'Mosel'})
        self.assertEqual({row['river']: row['items'][0]['value'] for row in stations},
                         {'Maas': 12.3, 'Mosel': 13.4})
        self.assertEqual(stations[0]['items'][0]['time'], '2026-10-31T08:30:00+01:00')
        self.assertIn('date_debut_mesure=', fetch.call_args_list[1].args[0])

    def test_dormant_catalogue_is_visible_but_never_colours_river(self):
        with patch.object(module, 'fetch_json', side_effect=[CATALOGUE, {'data': []}]):
            stations, candidates = module.france_temperatures(GEOJSON, NOW)
        self.assertEqual(candidates, 2)
        self.assertEqual(len(stations), 2)
        self.assertTrue(all(row['items'][0]['value'] is None for row in stations))
        self.assertTrue(all(row['items'][0]['quality'] == 'unavailable' for row in stations))

    def test_incomplete_observation_page_is_not_silently_accepted(self):
        with patch.object(module, 'fetch_json', side_effect=[CATALOGUE,
                {'data': [], 'next': 'https://example.test/page2'}]):
            with self.assertRaisesRegex(RuntimeError, 'one page'):
                module.france_temperatures(GEOJSON, NOW)

    def test_temperature_additions_reach_live_and_current_packets(self):
        now = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
        station = {'id': 'fr-hubeau-temp-02094900', 'name': 'Moselle test',
                   'river': 'Mosel', 'country': 'FR', 'lat': 49.449, 'lon': 6.347,
                   'items': [{'label': 'Wassertemperatur', 'value': 12.3,
                              'unit': '°C', 'time': now}],
                   'history': {'Wassertemperatur': [{'t': now, 'v': 12.3}]}}
        scratch = Path(__file__).resolve().parents[1] / 'work' / 'tmp'
        scratch.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=scratch) as td:
            root = Path(td)
            data = root / 'assets/data'
            data.mkdir(parents=True)
            (data / 'river-sensor-additions.json').write_text(
                json.dumps({'updated': now, 'temperature_stations': [station]}), encoding='utf-8')
            output = root / 'out'
            build_sensor_packets.build(root, output)
            current = json.loads((output / 'temperature-map-current.json').read_text(encoding='utf-8'))
            full = json.loads((output / 'temperature-map.json').read_text(encoding='utf-8'))
        self.assertEqual(current['data']['stations'][0]['id'], station['id'])
        self.assertEqual(full['data']['stations'][0]['river'], 'Mosel')


if __name__ == '__main__':
    unittest.main()
