import unittest
from unittest.mock import patch

import austria_sources as sources


class KaerntenPartialTests(unittest.TestCase):
    def test_flow_stations_survive_lake_timeout(self):
        feature = {"type":"Feature", "geometry":{"type":"Point","coordinates":[14.64,46.54]},
                   "properties":{"hzbnr":213991,"name":"Altendorf-Sagerberg",
                                 "gewaesser":"Sagerbergbach","letzter_wert_w":166,
                                 "letzter_wert_w_date":"2026-09-21T08:30:00+02:00"}}
        with patch.object(sources,"_fetch_json",side_effect=[{"features":[feature]},TimeoutError("Seepegel")]):
            rows=sources.process_kaernten_live()
        self.assertEqual(len(rows),1)
        self.assertEqual(rows[0]["id"],"at-ktn-213991")
        self.assertEqual(rows[0]["items"][0]["label"],"Pegelstand")


if __name__ == "__main__":
    unittest.main()
