import unittest

from fetch_water_levels import references


class GaugeAbsoluteExtremesTests(unittest.TestCase):
    def test_official_historical_extremes_survive_without_changing_mean_reference_validation(self):
        period = {'timespanStart': '2010-11-01', 'timespanEnd': '2020-10-31'}
        series = {'unit': 'cm', 'characteristicValues': [
            {'shortname': 'NNW', 'value': 35, 'unit': 'cm', 'occurrences': ['1934-07-26']},
            {'shortname': 'MNW', 'value': 82, 'unit': 'cm', **period},
            {'shortname': 'MW', 'value': 96, 'unit': 'cm', **period},
            {'shortname': 'MHW', 'value': 117, 'unit': 'cm', **period},
            {'shortname': 'HHW', 'value': 209, 'unit': 'cm', 'occurrences': ['1940-04-01']},
        ]}
        refs, issue = references(series)
        self.assertIsNone(issue)
        self.assertEqual(refs['NNW']['occurrences'], ['1934-07-26'])
        self.assertEqual(refs['HHW']['value'], 209)
        self.assertEqual(refs['MW']['value'], 96)

    def test_inconsistent_or_wrong_unit_extreme_is_not_published(self):
        period = {'timespanStart': '2010-11-01', 'timespanEnd': '2020-10-31'}
        series = {'unit': 'cm', 'characteristicValues': [
            {'shortname': 'NNW', 'value': 120, 'unit': 'cm'},
            {'shortname': 'MNW', 'value': 82, 'unit': 'cm', **period},
            {'shortname': 'MW', 'value': 96, 'unit': 'cm', **period},
            {'shortname': 'MHW', 'value': 117, 'unit': 'cm', **period},
            {'shortname': 'HHW', 'value': 209, 'unit': 'm'},
        ]}
        refs, issue = references(series)
        self.assertIsNone(issue)
        self.assertNotIn('NNW', refs)
        self.assertNotIn('HHW', refs)


if __name__ == '__main__':
    unittest.main()
