import unittest
from datetime import datetime, timezone, timedelta
from country_pipeline import combine, recent, country


class CountryPipelineTest(unittest.TestCase):
    def test_old_history_cannot_replace_new_measurement(self):
        now = datetime.now(timezone.utc)
        def payload(time, value):
            return {'stations': [{'id': 'nl1', 'src': 'rijkswaterstaat', 'items': [{'label': 'Pegelstand', 'time': time.isoformat(), 'value': value, 'unit': 'cm'}], 'history': {'Pegelstand': [{'t': time.isoformat(), 'v': value}]}}]}
        result = combine([payload(now, 123), payload(now-timedelta(days=2), 100)])
        row = result['stations'][0]
        self.assertEqual(row['items'][0]['value'], 123)
        self.assertEqual(len(row['history']['Pegelstand']), 2)
        self.assertEqual(country(row), 'NL')

    def test_four_hour_actual_observations_only(self):
        now = datetime.now(timezone.utc)
        points = [{'t': (now-timedelta(hours=i)).isoformat(), 'v': i} for i in range(250)]
        points.append({'t': (now+timedelta(days=1)).isoformat(), 'v': 999})
        output = recent(points, now.timestamp())
        self.assertLessEqual(len(output), 49)
        self.assertEqual(output[-1]['v'], 0)
        self.assertTrue(all(p in points for p in output))


if __name__ == '__main__':
    unittest.main()
