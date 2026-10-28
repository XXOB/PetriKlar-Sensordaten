import importlib.util
from datetime import datetime, timezone
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('lfu_temperature', ROOT / 'fetch_lfu_brandenburg_temperatures.py')
lfu = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(lfu)


class ChartTitleTests(unittest.TestCase):
    def test_reads_the_explicit_last_value_and_local_station_time(self):
        now = datetime(2026, 10, 16, 5, 20, tzinfo=timezone.utc)
        observed, value = lfu.parse_chart_title(
            'Rohdaten bis 16.10.2026 07:00 Uhr (letzter Wert: 14,10)', now
        )
        self.assertEqual(value, 14.10)
        self.assertEqual(observed.isoformat(), '2026-10-16T07:00:00+02:00')

    def test_accepts_ocr_spacing_and_decimal_period(self):
        now = datetime(2026, 10, 16, 5, 20, tzinfo=timezone.utc)
        _, value = lfu.parse_chart_title(
            'Rohdaten bis 16.10.2026 07:00 Uhr (letzter Wert: 12. 6)', now
        )
        self.assertEqual(value, 12.6)

    def test_accepts_ocr_misreading_yvert_from_live_spree_charts(self):
        now = datetime(2026, 10, 25, 6, 30, tzinfo=timezone.utc)
        observed, value = lfu.parse_chart_title(
            'ec Rohdaten bis 25.10.2026 05:00 Uhr (letzter Yvert: 10,40)', now
        )
        self.assertEqual(value, 10.4)
        self.assertEqual(observed.isoformat(), '2026-10-25T05:00:00+01:00')

    def test_real_potsdam_ocr_open_parenthesis_read_as_f(self):
        now = datetime(2026, 10, 28, 8, 20, tzinfo=timezone.utc)
        observed, value = lfu.parse_chart_title(
            '°C Rohdaten bis 28.10.2026 09:00 Uhr fletzter Wert: 11,30)', now)
        self.assertEqual(value,11.3)
        self.assertEqual(observed.isoformat(),'2026-10-28T09:00:00+01:00')

    def test_cumlosen_has_bootstrap_metadata(self):
        site=next(s for s in lfu.SITES if s['id']=='bb-cumlosen')
        station=lfu.station_metadata(site,{}, {})
        self.assertEqual(station['river'],'Elbe')
        self.assertEqual(station['lat'],53.04187)

    def test_rejects_missing_or_stale_chart_values(self):
        now = datetime(2026, 10, 16, 5, 20, tzinfo=timezone.utc)
        with self.assertRaises(ValueError):
            lfu.parse_chart_title('Chart ohne auslesbaren Wert', now)
        with self.assertRaises(ValueError):
            lfu.parse_chart_title(
                'Rohdaten bis 10.10.2026 07:00 Uhr (letzter Wert: 14,10)', now
            )


if __name__ == '__main__':
    unittest.main()
