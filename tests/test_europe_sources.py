import unittest
from datetime import datetime, timezone, timedelta
from unittest.mock import patch
import europe_sources as E


class EuropeanSources(unittest.TestCase):
    def test_history_requires_nearly_full_year_and_rejects_flat_or_gappy_series(self):
        now=datetime(2026,9,11,12,tzinfo=timezone.utc)
        points=[{'t':(now-timedelta(days=i)).isoformat(),'v':i%101} for i in range(365)]
        baseline=E.history_baseline(points,'m',now)
        self.assertEqual(baseline['days'],365)
        self.assertLess(baseline['low'],baseline['center'])
        self.assertIsNone(E.history_baseline(points[:90],'m',now))
        self.assertIsNone(E.history_baseline([p for i,p in enumerate(points) if not 140<i<185],'m',now))
        self.assertIsNone(E.history_baseline([{**p,'v':1} for p in points],'m',now))

    def setUp(self):
        self.now=datetime(2026,9,11,12,tzinfo=timezone.utc)

    def series(self, value=0, stamp='2026-09-11T11:00:00+00:00', kind='T', process='meting', height='-200'):
        return {'AquoMetadata':{'Compartiment':{'Code':'OW'},'Grootheid':{'Code':kind},
            'Eenheid':{'Code':'oC' if kind=='T' else 'cm'},'ProcesType':process,'Hoedanigheid':{'Code':'NAP'}},
            'Locatie':{'Code':'sample','Naam':'Sample','Coordinatenstelsel':'ETRS89','Lat':51.9,'Lon':5.5},
            'MetingenLijst':[{'Meetwaarde':{'Waarde_Numeriek':value},'Tijdstip':stamp,
                'WaarnemingMetadata':{'Kwaliteitswaardecode':'00','Bemonsteringshoogte':height,'Referentievlak':'WATSGL'}}]}

    def test_forecasts_old_readings_and_missing_values_are_not_current_data(self):
        payload={'Succesvol':True,'WaarnemingenLijst':[self.series(None),self.series(15,process='verwachting'),
            self.series(15,stamp='1934-01-01T00:00:00+00:00'),self.series(0)]}
        rows=E.parse_nl(payload,self.now)
        self.assertEqual(len(rows),1)
        self.assertEqual(rows[0]['items'][0]['value'],0)

    def test_depths_and_datums_remain_separate(self):
        rows=E.parse_nl({'Succesvol':True,'WaarnemingenLijst':[self.series(20),self.series(18,height='-500'),self.series(640,kind='WATHTE')]},self.now)
        self.assertEqual(len(rows),3)
        level=next(s for s in rows if s['items'][0]['label']=='Pegelstand')
        self.assertEqual(level['items'][0]['value'],640)
        self.assertEqual(level['level_datum'],'NAP')
        self.assertNotIn('references',level)

    def test_oversized_batches_are_split_without_losing_locations(self):
        catalogue={'Succesvol':True,'AquoMetadataLijst':[{'AquoMetadata_MessageID':1,'Compartiment':{'Code':'OW'},'Grootheid':{'Code':'T'},'ProcesType':'meting'}],
            'AquoMetadataLocatieLijst':[{'AquoMetaData_MessageID':1,'Locatie_MessageID':i} for i in range(3)],
            'LocatieLijst':[{'Locatie_MessageID':i,'Code':str(i),'Coordinatenstelsel':'ETRS89','Lat':52,'Lon':5} for i in range(3)]}
        seen=[]
        def response(url,body):
            if len(body['LocatieLijst'])>1:raise ValueError('too big')
            seen.append(body['LocatieLijst'][0]['Code'])
            return {'Succesvol':True,'WaarnemingenLijst':[]}
        with patch.object(E,'read_json',response):
            _,report=E.collect_nl(self.now,catalogue)
        self.assertEqual(set(seen),{'0','1','2'})
        self.assertEqual(report['failed_batches'],[])


if __name__=='__main__':unittest.main()
