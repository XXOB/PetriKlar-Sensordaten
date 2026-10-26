import datetime as dt
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import fetch_uk_river_temperatures as uk_temp
from fetch_uk_river_sensors import build_network


class UkRiverTemperatureTests(unittest.TestCase):
    def test_measure_id_supports_qualified_and_live_water_temperature_series(self):
        self.assertEqual(uk_temp.station_code_from_measure("https://example.test/E01111-temp-i-subdaily-C"), "E01111")
        self.assertEqual(uk_temp.station_code_from_measure("E01111-temp-i-900-C-qualified"), "E01111")
        self.assertIsNone(uk_temp.station_code_from_measure("E01111-temp-m-86400-C-qualified"))
        self.assertIsNone(uk_temp.station_code_from_measure("E01111-do-i-subdaily-mgL"))

    def test_maps_only_recent_valid_temperature_to_existing_river_geometry(self):
        network = build_network()
        geo = json.loads((ROOT / "assets/data/temperature-rivers.geojson").read_text(encoding="utf-8"))
        feature = next(f for f in geo["features"] if f["properties"].get("name") == "Thames")
        line = feature["geometry"]["coordinates"]
        if feature["geometry"]["type"] == "MultiLineString":
            line = line[0]
        lon, lat = line[len(line) // 2]
        now = dt.datetime(2026, 10, 25, 9, tzinfo=dt.timezone.utc)
        stations = [{"notation": "T001", "label": "Thames test station", "lat": lat, "long": lon,
                     "status": [{"label": "Active"}]}]
        latest = [{"measure": {"@id": "https://example.test/T001-temp-i-subdaily-C"},
                   "dateTime": "2026-10-25T08:30:00Z", "value": 12.6}]
        history = [{"measure": latest[0]["measure"], "dateTime": "2026-10-24T06:00:00Z", "value": 12.1}]
        rows = uk_temp.build_rows(stations, latest, network, now, history)
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["river"], "Thames")
        self.assertEqual(row["src"], "environment-agency")
        self.assertEqual(row["items"][0]["label"], "Wassertemperatur")
        self.assertEqual(row["items"][0]["unit"], "°C")
        self.assertEqual(row["items"][0]["value"], 12.6)
        self.assertEqual(len(row["history"]["Wassertemperatur"]), 2)
        self.assertEqual(row["license"], "OGL-3.0")

    def test_old_and_implausible_readings_do_not_create_new_station_rows(self):
        network = build_network()
        geo = json.loads((ROOT / "assets/data/temperature-rivers.geojson").read_text(encoding="utf-8"))
        feature = next(f for f in geo["features"] if f["properties"].get("name") == "Thames")
        line = feature["geometry"]["coordinates"]
        if feature["geometry"]["type"] == "MultiLineString": line = line[0]
        lon, lat = line[len(line) // 2]
        now = dt.datetime(2026, 10, 25, 9, tzinfo=dt.timezone.utc)
        station = {"notation": "T001", "label": "Thames test station", "lat": lat, "long": lon,
                   "status": [{"label": "Active"}]}
        old = [{"measure": {"@id": "T001-temp-i-subdaily-C"}, "dateTime": "2026-09-01T00:00:00Z", "value": 12}]
        implausible = [{"measure": {"@id": "T001-temp-i-subdaily-C"}, "dateTime": "2026-10-25T08:00:00Z", "value": 99}]
        self.assertEqual(uk_temp.build_rows([station], old, network, now), [])
        self.assertEqual(uk_temp.build_rows([station], implausible, network, now), [])


if __name__ == "__main__":
    unittest.main()
