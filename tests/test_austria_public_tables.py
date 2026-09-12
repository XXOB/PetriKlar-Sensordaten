import unittest
from austria_public_tables import kaernten, salzburg

class PublicTablesTest(unittest.TestCase):
    def test_temperature_uses_wt_column_and_official_station_id(self):
        station={'id':'at-ktn-212324','name':'Oberdrauburg','river':'Drau','items':[{'label':'Pegelstand','value':109,'unit':'cm'}]}
        p={'data':[{'stationsnummer':212324,'station':'Alias','level':'11,2','metrics':50,'datum':'12.09.2026 13:30'},
                   {'stationsnummer':999999,'level':20,'datum':'12.09.2026 13:30'}]}
        rows=kaernten(p,[station]);self.assertEqual(len(rows),1)
        self.assertEqual(rows[0]['items'][0]['value'],109)
        self.assertEqual(rows[0]['items'][1]['value'],11.2)
        self.assertEqual(rows[0]['items'][1]['unit'],'°C')
        self.assertTrue(rows[0]['items'][1]['time'].endswith('+02:00'))
        self.assertEqual(station['items'],[{'label':'Pegelstand','value':109,'unit':'cm'}])

    def test_salzburg_excludes_air_groundwater_and_alarm_thresholds(self):
        row={'number':'203075','name':'Mittersill','WTO_OBJECT':'Salzach','latlng':[47.28,12.48],
             'plots':{'_OWF.W':'Water level'},'values':{'W':{'15m.Cmd.WiskiWeb':{'v':189,'unit':'cm','dt':1789207200000},'Cmd.ALM.MW':{'v':250,'unit':'cm','dt':1789207200000}}}}
        rows=salzburg([row,{**row,'plots':{'_GW.W':'Groundwater'}},{**row,'plots':{'_CL.LT':'Air'}}])
        self.assertEqual(len(rows),1)
        self.assertEqual(rows[0]['items'][0]['value'],189)
        self.assertNotIn('references',rows[0])

if __name__=='__main__': unittest.main()
