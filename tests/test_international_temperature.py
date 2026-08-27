"""Offline parser/collector tests. Never contact a production data endpoint."""
import copy
import json
from pathlib import Path
import sys
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import international_temperature_sources as feeds


class InternationalTemperatureTests(unittest.TestCase):
    def setUp(self):
        self.time=(datetime.now(timezone.utc)-timedelta(minutes=10)).isoformat()
        self.station={'objID':'0-203-1-245000','DBC':'245000','STATION_NAME':'Hřensko - Labe',
                      'STREAM_NAME':'Labe','GEOGR1':50.871,'GEOGR2':14.231}
        self.cz={'objList':[{'objID':self.station['objID'],'tsList':[{'tsConID':'TH','unit':'0C','tsData':[{'dt':self.time,'value':18.8}]}]}]}
        self.selection=feeds.RWS_STATIONS[0]
        self.nl={'Succesvol':True,'WaarnemingenLijst':[{
            'Locatie':{'Code':self.selection['Code'],'Naam':'Middelharnis, meetboei','Lat':51.776524,'Lon':4.192119,'Coordinatenstelsel':'ETRS89'},
            'AquoMetadata':{'Compartiment':{'Code':'OW'},'Grootheid':{'Code':'T'},'Eenheid':{'Code':'oC'},'ProcesType':'meting'},
            'MetingenLijst':[{'Tijdstip':self.time,'Meetwaarde':{'Waarde_Numeriek':20.6},'WaarnemingMetadata':{
                'Bemonsteringshoogte':'-200','Referentievlak':'WATSGL','Kwaliteitswaardecode':'00'}}]}]}

    def test_chmi_only_main_river_catalog_not_tributaries(self):
        keys=list(self.station)
        rows=[dict(self.station,STREAM_NAME=name) for name in ['Labe','Odra','Vltava','Morava']]
        catalog={'data':{'data':{'header':','.join(keys),'values':[[s[k] for k in keys] for s in rows]}}}
        self.assertEqual([s['STREAM_NAME'] for s in feeds.chmi_catalog(catalog)],['Labe','Odra'])

    def test_chmi_temperature_country_license_original_time_and_no_input_mutation(self):
        old=copy.deepcopy(self.cz);r=feeds.parse_chmi(self.station,self.cz)
        self.assertEqual(r['country'],'CZ');self.assertEqual(r['river'],'Elbe')
        self.assertEqual(r['license'],'CC BY 4.0');self.assertIn('Český',r['attribution'])
        self.assertEqual(r['items'][0]['value'],18.8);self.assertTrue(r['items'][0]['time'].endswith('+00:00'))
        self.assertEqual(old,self.cz)

    def test_chmi_waterlevel_wrong_unit_wrong_station_do_not_become_temperature(self):
        series=self.cz['objList'][0]['tsList'][0]
        series['tsConID']='H';self.assertIsNone(feeds.parse_chmi(self.station,self.cz))
        series['tsConID']='TH';series['unit']='CM';self.assertIsNone(feeds.parse_chmi(self.station,self.cz))
        series['unit']='0C';self.cz['objList'][0]['objID']='other';self.assertIsNone(feeds.parse_chmi(self.station,self.cz))

    def test_missing_sentinels_and_future_or_historic_values_are_not_live(self):
        p=self.cz['objList'][0]['tsList'][0]['tsData'][0]
        for v in [None,'',-999,999,float('nan'),True]:
            p['value']=v;self.assertIsNone(feeds.parse_chmi(self.station,self.cz))
        p['value']=0;self.assertEqual(feeds.parse_chmi(self.station,self.cz)['items'][0]['value'],0)
        p['dt']=(datetime.now(timezone.utc)+timedelta(days=1)).isoformat();self.assertIsNone(feeds.parse_chmi(self.station,self.cz))
        p['dt']='2016-11-03T10:00Z';self.assertIsNone(feeds.parse_chmi(self.station,self.cz))

    def test_rws_surface_water_only_and_consistent_horizon(self):
        r=feeds.parse_rws(self.nl,self.selection)
        self.assertEqual(r['country'],'NL');self.assertEqual(r['river'],'Rhein');self.assertEqual(r['items'][0]['value'],20.6)
        self.assertEqual(r['attribution'],'Rijkswaterstaat');self.assertIn('open-data',r['license_url'])
        measurement=self.nl['WaarnemingenLijst'][0]['MetingenLijst'][0]
        for key,value in [('Bemonsteringshoogte','-800'),('Referentievlak','NAP'),('Kwaliteitswaardecode','99')]:
            with self.subTest(key=key):
                old=measurement['WaarnemingMetadata'][key];measurement['WaarnemingMetadata'][key]=value
                self.assertIsNone(feeds.parse_rws(self.nl,self.selection));measurement['WaarnemingMetadata'][key]=old

    def test_rws_does_not_use_air_temperature_forecast_wrong_units_or_coordinates(self):
        for key,value in [('Compartiment',{'Code':'LT'}),('Eenheid',{'Code':'K'}),('ProcesType','verwachting')]:
            p=copy.deepcopy(self.nl);p['WaarnemingenLijst'][0]['AquoMetadata'][key]=value
            self.assertIsNone(feeds.parse_rws(p,self.selection))
        p=copy.deepcopy(self.nl);p['WaarnemingenLijst'][0]['Locatie']['Coordinatenstelsel']='RD'
        self.assertIsNone(feeds.parse_rws(p,self.selection))

    def test_rws_no_data_and_failed_response_never_mean_zero_degrees(self):
        self.assertIsNone(feeds.parse_rws({},self.selection))
        self.assertIsNone(feeds.parse_rws({'Succesvol':True,'WaarnemingenLijst':[]},self.selection))
        with self.assertRaises(ValueError):feeds.parse_rws({'Succesvol':False},self.selection)

    def test_one_failed_rws_station_does_not_discard_the_other_stations(self):
        responses=[OSError('timeout'),self.nl,self.nl]
        with patch.object(feeds,'read_json',side_effect=responses),patch.object(feeds,'parse_rws',return_value={'id':'ok'}):
            self.assertEqual(len(feeds.rijkswaterstaat()),2)

    def test_one_failed_chmi_station_does_not_discard_others(self):
        with patch.object(feeds,'chmi_catalog',return_value=[self.station,self.station]),patch.object(feeds,'read_json',side_effect=[{},OSError('timeout'),self.cz]):
            self.assertEqual(len(feeds.chmi()),1)

    def test_one_failed_country_does_not_discard_other_country(self):
        with patch.object(feeds,'chmi',side_effect=OSError('timeout')),patch.object(feeds,'rijkswaterstaat',return_value=[{'id':'nl'}]):
            self.assertEqual(feeds.collect(),[{'id':'nl'}])

    def test_rws_uses_new_service_and_sends_surface_water_and_selected_height(self):
        with patch.object(feeds,'read_json',return_value={}) as read:
            self.assertEqual(feeds.rijkswaterstaat(),[])
        self.assertEqual(read.call_count,3)
        for call in read.call_args_list:
            url,body=call.args;self.assertIn('ddapi20-',url)
            meta=body['AquoPlusWaarnemingMetadata'];self.assertEqual(meta['AquoMetadata']['Compartiment']['Code'],'OW')
            self.assertEqual(meta['WaarnemingMetadata']['BemonsteringshoogteLijst'],['-200'])
        self.assertNotIn('hoekvanholland',[s['Code'] for s in feeds.RWS_STATIONS])


if __name__=='__main__':unittest.main()
