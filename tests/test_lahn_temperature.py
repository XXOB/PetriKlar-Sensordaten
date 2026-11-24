"""The official Lahn WT gauge must enter the compact temperature packet."""
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import temperature_sources as temps


class LahnTemperatureTests(unittest.TestCase):
    def test_pegelonline_leun_is_collected_but_non_wt_lahn_gauge_is_not(self):
        now=datetime.now(timezone.utc).isoformat()
        leun={"uuid":"32807065-b887-49f0-935a-80033e5f3cb0", "longname":"LEUN NEU",
              "water":{"longname":"LAHN"}, "latitude":50.5, "longitude":8.3,
              "timeseries":[{"shortname":"WT","currentMeasurement":{"timestamp":now,"value":5.7}}]}
        no_wt={**leun,"uuid":"other","longname":"ONLY LEVEL","timeseries":[]}

        def fake_fetch(url):
            if "stations.json" in url:
                return [leun,no_wt]
            return []

        with patch.object(temps,"fetch_json",side_effect=fake_fetch):
            rows=temps.pegelonline()
        self.assertEqual(len(rows),1)
        self.assertEqual(rows[0]["name"],"LEUN NEU")
        self.assertEqual(rows[0]["river"],"Lahn")
        self.assertEqual(rows[0]["items"][0]["value"],5.7)


if __name__=="__main__":
    unittest.main()
