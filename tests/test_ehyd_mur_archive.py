import json
import unittest
from pathlib import Path
from ehyd_mur_archive import parse

class MurArchiveTests(unittest.TestCase):
    def test_gap_is_not_zero_and_end_marker_is_not_a_measurement(self):
        raw='Exportzeitreihe:;WTemperatur\nWerte:\n01.01.2023 00:00:00; 0,0\n01.02.2023 00:00:00; Lücke\n01.03.2023 00:00:00; 9,5\n01.01.2024 00:00:00; Lücke'.encode('cp1252')
        self.assertEqual(parse(raw),[{'month':'2023-01','value':0.0},{'month':'2023-03','value':9.5}])

    def test_real_archives_are_monthly_and_separate_from_live_data(self):
        data=json.loads((Path(__file__).resolve().parents[1]/'ehyd-mur-temperature-history.json').read_text(encoding='utf-8'))
        self.assertEqual(len(data['stations']),12)
        for s in data['stations']:
            self.assertTrue(s['historical'])
            self.assertEqual(s['resolution'],'monthly-mean')
            self.assertEqual(s['to'],'2023-12')
            self.assertNotIn('items',s)
            self.assertGreater(len(s['values']),50)
            self.assertEqual(len({p['month'] for p in s['values']}),len(s['values']))

if __name__=='__main__':unittest.main()
