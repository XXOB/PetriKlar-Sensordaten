"""Offline regressions for the twelve additional live-temperature rivers."""
import datetime as dt
import json
import sys
import unittest
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools" if (ROOT / "tools/fetch_uk_river_temperatures.py").exists() else ROOT))
import fetch_uk_river_temperatures as temp
from fetch_uk_river_sensors import build_network

NOW = dt.datetime(2026, 10, 26, 19, 30, tzinfo=dt.timezone.utc)
SITES = json.loads((ROOT / "tests/fixtures/uk-temperature-sites.json").read_text(encoding="utf8"))


class TemperatureExtensionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.network = build_network()

    def records(self, sites=SITES):
        stations = [{"notation": s["source_station"], "label": s["name"], "lat": s["lat"], "long": s["lon"],
                     "status": [{"label": "Active"}]} for s in sites]
        latest = [{"measure": {"@id": s["source_station"] + "-temp-i-subdaily-C"},
                   "value": s["items"][0]["value"], "dateTime": s["items"][0]["time"]} for s in sites]
        return stations, latest

    def test_real_sites_match_all_twelve_named_rivers_and_keep_measurements(self):
        stations, latest = self.records()
        rows = temp.build_rows(stations, latest, self.network, NOW)
        self.assertEqual(len(rows), 12)
        self.assertEqual({r["river"] for r in rows}, temp.ADDITIONAL_RIVERS)
        for row in rows:
            site = next(s for s in SITES if s["source_station"] == row["source_station"])
            self.assertEqual(row["river"], site["river"])
            self.assertEqual(row["items"], site["items"])
            self.assertEqual(row["river_assignment"], "agency-name-and-geometry-within-350m")
            self.assertEqual(row["country"], "GB")
            self.assertEqual(row["license"], "OGL-3.0")

    def test_named_nearby_drain_does_not_supply_main_river_temperature(self):
        station = {"notation": "E03659A", "label": "SINCIL DRAIN_SANDHILL BECK_E_202308",
                   "lat": 53.215, "long": -.55}
        # Even exactly on a drawn Witham segment, name mismatch must be rejected.
        edge = self.network["Witham"]["edges"][0][0]
        station.update(long=edge[0], lat=edge[1])
        self.assertIsNone(temp.assign_temperature_river(station, self.network))

    def test_named_tame_cannot_be_assigned_to_trent_at_the_confluence(self):
        station = {"label": "TAME_TEST", "lat": 50, "long": 0}
        network = {"Trent": {"bounds": (-1, 49, 1, 51), "edges": [((-.01, 50), (.01, 50))]}}
        # A known named river must never fall back to an unrelated nearby river.
        self.assertIsNone(temp.assign_temperature_river(station, network))

    def test_history_is_batched_and_one_failure_preserves_other_rivers_and_current_values(self):
        stations, latest = self.records()
        calls = []
        def fetch(url):
            query = parse_qs(urlsplit(url).query, keep_blank_values=True)
            calls.append(query)
            if "/id/stations" in url:
                return {"items": stations}
            if "latest" in query:
                return {"items": latest}
            measures = query["measure"]
            self.assertLessEqual(len(measures), temp.HISTORY_BATCH_SIZE)
            if len(calls) == 3:
                raise TimeoutError("one optional history batch unavailable")
            return {"items": [{"measure": {"@id": m}, "dateTime": "2026-10-25T06:00:00Z", "value": 11.0} for m in measures]}
        packet = temp.collect(now=NOW, fetcher=fetch, network=self.network)
        self.assertEqual(len(packet["stations"]), 12)
        self.assertEqual(packet["source_report"]["current_within_48h"], 12)
        self.assertEqual(len(calls), 4)
        self.assertEqual(sum(len(s["history"]["Wassertemperatur"]) == 2 for s in packet["stations"]), 6)


if __name__ == "__main__":
    unittest.main()
