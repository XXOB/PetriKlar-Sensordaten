import unittest
from datetime import datetime,timezone
from steiermark_sources import parse_csv
from austria_ogd_csv import parse
from country_pipeline import combine
class AustrianImportTests(unittest.TestCase):
 def test_hydavis_encoding_zero_and_fixed_mez(self):
  raw='Einheit: cm\nDBMSNR;ZEITPUNKT;WERT\n1;12.09.2025 12:00:00;0\n1;12.09.2025 12:15:00;-2,5\n1;12.09.2025 12:30:00;Lücke\n'.encode('utf-16-be')
  unit,p=parse_csv(raw);self.assertEqual(unit,'cm');self.assertEqual([x['v'] for x in p],[0,-2.5]);self.assertTrue(p[0]['t'].endswith('+01:00'))
 def test_html_is_not_a_series(self):
  with self.assertRaises(ValueError):parse_csv(b'<html>Error</html>')
 def test_ogd_coordinates_and_parameters(self):
  text='Stationsname;Stationsnummer;Gewässer;Parameter;Zeitstempel in ISO8601;Wert;Einheit;Rechtswert;Hochwert;EPSG-Code\nTest;123;Inn;W;2025-09-12T10:00:00+0100;0;cm;11.4;47.3;EPSG:4326\nTest;123;Inn;WT;2025-09-12T10:00:00+0100;12.5;°C;11.4;47.3;EPSG:4326\n'
  rows=parse(text.encode('cp1252'),'at-tirol','https://example.test','Land Tirol')
  self.assertEqual(len(rows),1);self.assertEqual(rows[0]['lat'],47.3);self.assertEqual(rows[0]['items'][0]['value'],0);self.assertTrue(rows[0]['params']['wt'])
 def test_combined_params_preserve_both(self):
  s=dict(id='x',items=[dict(label='Pegelstand',value=1,unit='cm',time='2026-01-01T00:00:00+00:00')],history={})
  t={**s,'items':[dict(label='Wassertemperatur',value=10,unit='°C',time='2026-01-01T00:00:00+00:00')]}
  row=combine([{'stations':[s,t]}])['stations'][0]
  self.assertTrue(row['params']['pegel']);self.assertTrue(row['params']['wt'])
if __name__=='__main__':unittest.main()
