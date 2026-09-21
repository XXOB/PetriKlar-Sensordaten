import unittest
from datetime import datetime, timedelta
from unittest.mock import patch

import fetch_wasserwerte as feed


class SachsenProvisionalTests(unittest.TestCase):
    def test_unvalidated_chart_is_exposed_without_inventing_a_number(self):
        page = '<img data-src="https://www.wasser.sachsen.de/stationen/img/BD_TW_SW_aktuell.png">' \
               '<a href="https://www.wasser.sachsen.de/stationen/download/BD.xlsx">Tabelle</a>'
        old = datetime.now(feed.ZoneInfo("Europe/Berlin")).replace(tzinfo=None) - timedelta(days=4)
        station = ("Bad Düben", "Vereinigte Mulde", 51.59, 12.586,
                   "https://www.wasser.sachsen.de/messstation-bad-dueben.html")
        with patch.object(feed, "SAXONY_STATIONS", [station]), \
             patch.object(feed, "fetch_gkd_html", return_value=page), \
             patch.object(feed, "fetch_bytes", return_value=b""), \
             patch.object(feed, "parse_xlsx_sensors", return_value=({"wt": (old, 17.5)}, {})):
            row = feed.process_sachsen()[0]
        self.assertEqual(row["provisional_chart_url"],
                         "https://www.wasser.sachsen.de/stationen/img/BD_TW_SW_aktuell.png")
        self.assertEqual(row["items"], [{"label": "Wassertemperatur", "value": None,
            "unit": "°C", "icon": "", "time": old.strftime("%d.%m.%Y %H:%M"),
            "chart_url": row["provisional_chart_url"], "quality": "unvalidated_chart"}])


if __name__ == "__main__":
    unittest.main()
