"""Offline regressions for official UNDINE station metadata and current-value parsing."""
import importlib.util
import pathlib
import unittest
from unittest.mock import patch

ROOT=pathlib.Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location("collector",ROOT/"fetch_wasserwerte.py")
collector=importlib.util.module_from_spec(spec)
spec.loader.exec_module(collector)

class UndineTests(unittest.TestCase):
    def coords(self,key,pair):
        # Minimal excerpts from the official station pages, checked 2026-08-26.
        page=f'<title>Messstation {key}, Rhein | UNDINE</title><li>Rechtswert / Hochwert: <span>{pair}</span></li>'
        cache={'rhein/'+key:None} # legacy negative cache must not suppress recovery
        with patch.object(collector,'fetch_gkd_html',return_value=page):
            return collector.undine_station_coords('rhein',key,cache)

    def test_breisach_swapped_gk(self):
        c=self.coords('breisach','5328912 / 3393839')
        self.assertAlmostEqual(c['lat'],48.08941,places=4)
        self.assertAlmostEqual(c['lon'],7.57391,places=4)
        self.assertIn('breisach.html',c['source_url'])

    def test_weil_swapped_swiss_lv03(self):
        c=self.coords('weil','272310 / 611740')
        self.assertAlmostEqual(c['lat'],47.60137,places=4)
        self.assertAlmostEqual(c['lon'],7.59474,places=4)

    def test_swiss_reference_bern_and_lv95(self):
        self.assertEqual(collector.swiss_to_wgs84(600000,200000),collector.swiss_to_wgs84(2600000,1200000))
        lat,lon=collector.swiss_to_wgs84(600000,200000)
        self.assertAlmostEqual(lat,46.95108,places=4)
        self.assertAlmostEqual(lon,7.43864,places=4)

    def test_rekingen_uses_official_bafu_coordinates(self):
        with patch.object(collector,'fetch_gkd_html',side_effect=AssertionError('No UNDINE station page exists')):
            c=collector.undine_station_coords('rhein','rekingen',{'rhein/rekingen':None})
        self.assertAlmostEqual(c['lat'],47.57038,places=4)
        self.assertAlmostEqual(c['lon'],8.32963,places=4)
        self.assertTrue(c['source_url'].endswith('/2143'))

    def test_actual_slugs_and_positive_cache(self):
        self.assertTrue(collector.undine_station_url('rhein','koblenz_rhein').endswith('rhein_mst_koblenz_rh.html'))
        self.assertTrue(collector.undine_station_url('rhein','duesseldorf_flehe').endswith('rhein_mst_ddorf_flehe.html'))
        with patch.object(collector,'fetch_gkd_html',side_effect=AssertionError('Cached')):
            c=collector.undine_station_coords('rhein','breisach',{'rhein/breisach':{'lat':48.09,'lon':7.57,'name':'Breisach','river':'Rhein'}})
        self.assertIn('source_url',c)

    def test_failures_are_retriable_not_permanently_null(self):
        cache={}
        with patch.object(collector,'fetch_gkd_html',side_effect=OSError('temporary')) as fetch:
            self.assertIsNone(collector.undine_station_coords('rhein','test',cache))
            self.assertIn('retry_after',cache['rhein/test'])
            self.assertIsNone(collector.undine_station_coords('rhein','test',cache))
            self.assertEqual(fetch.call_count,1)
        cache['rhein/test']['retry_after']=0
        with patch.object(collector,'fetch_gkd_html',return_value='<title>Messstation Test, Rhein</title>Rechtswert / Hochwert: 3393839 / 5328912'):
            self.assertIsNotNone(collector.undine_station_coords('rhein','test',cache))

    def test_current_feed_does_not_invent_rheinfelden(self):
        js='var wt_rekingen="Datum: 26.08.2026, 05:00<br>Wassertemperatur: 22.2 °C"; var o2_rekingen="Datum: 26.08.2026, 05:00<br>Sauerstoffgehalt: 7.3 mg/l";'
        with patch.object(collector,'fetch_gkd_html',return_value=js):
            values=collector.undine_wt('rhein')
        self.assertEqual(values['rekingen']['t'],22.2)
        self.assertEqual(values['rekingen']['o2'],7.3)
        self.assertNotIn('rheinfelden',values)


    def test_kaub_gauge_page_and_potsdam_operator_position(self):
        self.assertIn('/pegel/rhein_pegel_kaub.html',collector.undine_station_url('rhein','kaub'))
        self.assertIn('potsdam_humboldtbr',collector.undine_station_url('elbe','potsdam'))
        with patch.object(collector,'fetch_gkd_html',side_effect=AssertionError('Use verified operator position')):
            c=collector.undine_station_coords('elbe','potsdam',{'elbe/potsdam':{'retry_after':1e12}})
        self.assertAlmostEqual(c['lat'],52.4006,places=4)
        self.assertAlmostEqual(c['lon'],13.07308,places=4)
        page='<title>Pegel Kaub, Rhein</title>Rechtswert / Hochwert: 3411690 / 5550660'
        with patch.object(collector,'fetch_gkd_html',return_value=page):
            c=collector.undine_station_coords('rhein','kaub',{'rhein/kaub':{'retry_after':1e12}})
        self.assertEqual(c['name'],'Kaub')
        self.assertEqual(c['river'],'Rhein')
        self.assertTrue(50.07<c['lat']<50.10 and 7.75<c['lon']<7.78)

    def test_missing_value_sentinel_is_not_an_observation(self):
        js='var wt_tangermuende="Datum: 28.10.2026, 07:00 Wassertemperatur: -9999.0"; var o2_tangermuende="Datum: 28.10.2026, 07:00 Sauerstoffgehalt: -9999.0";'
        with patch.object(collector,'fetch_gkd_html',return_value=js):
            self.assertEqual(collector.undine_wt('elbe'),{})

    def test_hamburg_numeric_table_uses_each_real_time(self):
        page='<table><tr><td>28.10.2026 08:50</td><td>Wassertemperatur</td><td>11.9</td><td>°C</td></tr><tr><td>28.10.2026 08:40</td><td>Sauerstoffkonzentration</td><td>8.3</td><td>mg/l</td></tr></table>'
        rows=collector.parse_hamburg_readings(page)
        self.assertEqual(rows[0]['value'],11.9)
        self.assertEqual(rows[0]['time'],'2026-10-28T08:50+01:00')
        self.assertEqual(rows[1]['time'],'2026-10-28T08:40+01:00')
        self.assertEqual(collector.parse_hamburg_readings(page.replace('11.9','-9999'))[0]['label'],'Sauerstoff')
        self.assertEqual(collector.parse_hamburg_readings('<td>11.9 °C</td>'),[])

if __name__=='__main__': unittest.main()
