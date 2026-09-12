import unittest
from datetime import date, timedelta
from unittest.mock import patch
from austria_level_references import parse, offered_files
from country_pipeline import combine

class ArchiveReferencesTest(unittest.TestCase):
    def csv(self, count=365, unit='cm'):
        rows=['Wasserstand\nEinheit:;['+unit+']\nPegelnullpunkt:\n01.01.2000;522,79\nBundesmeldenetz\nWerte:']
        rows += [(date(2023,1,1)+timedelta(days=i)).strftime('%d.%m.%Y')+' 00:00:00;'+str(50+i%100) for i in range(count)]
        rows += ['01.01.2024 00:00:00; Lücke']
        return '\n'.join(rows).encode('cp1252')

    def test_real_daily_values_only(self):
        b=parse(self.csv())
        self.assertEqual(b['days'],365)
        self.assertEqual(b['end'],'2023-12-31')
        self.assertEqual(b['datum_m'],522.79)
        self.assertGreater(b['low'],0)
        for raw in [self.csv(100), self.csv(unit='m³/s')]:
            with self.assertRaises(ValueError):parse(raw)

    def test_nested_download_metadata(self):
        f={'fileName':'W-Tagesmittel-test.csv','fileNr':2}
        self.assertEqual(list(offered_files([{'data':[{'data':[f]}]}])),[f])

    def test_reference_survives_publish_without_faking_live_history(self):
        b=parse(self.csv())
        row={'id':'at-test','country':'AT','level_datum_m':522.79,'items':[{'label':'Pegelstand','value':96,'unit':'cm','time':'2026-09-12T10:00:00+00:00'}]}
        with patch('country_pipeline.read',return_value={'references':{'at-test':b}}):
            result=combine([{'stations':[row]}])['stations'][0]
            self.assertEqual(result['history_baseline'],b)
            self.assertEqual(result['history'],{})
            row['level_datum_m']=523.79
            self.assertNotIn('history_baseline',combine([{'stations':[row]}])['stations'][0])

if __name__=='__main__': unittest.main()
