import unittest
from unittest.mock import patch
import austria_sources as a
class AustriaHistoryTest(unittest.TestCase):
 def test_level_history_preserves_times_and_zero(self):
  station={'type':'Feature','geometry':{'type':'Point','coordinates':[14,46]},'properties':{'hzbnr':1,'name':'Drau test','gewaesser':'Drau','letzter_wert_w':101,'letzter_wert_w_date':'2026-09-12T07:00:00+01:00','werte':{'wasserstand':[{'date':'2026-09-11T07:00:00+01:00','value':0},{'date':'2026-09-12T07:00:00+01:00','value':101},{'date':'x','value':None}],'abfluss':[{'date':'2026-09-12T07:00:00+01:00','value':500}]}}}
  with patch.object(a,'_fetch_json',side_effect=[{'features':[station]},{'features':[]}]):
   row=a.process_kaernten_live()[0]
  self.assertEqual([p['v'] for p in row['history']['Pegelstand']],[0,101])
  self.assertEqual(row['history']['Pegelstand'][0]['t'],'2026-09-11T07:00:00+01:00')
  self.assertEqual(row['history']['Durchfluss'][0]['v'],500)
if __name__=='__main__':unittest.main()
