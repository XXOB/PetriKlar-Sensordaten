import unittest
from unittest.mock import patch
from datetime import datetime, timezone, timedelta
import europe_sources as E


class NLResilience(unittest.TestCase):
    def test_trickling_response_has_total_time_limit(self):
        from io import BytesIO
        with patch.object(E,'urlopen',return_value=BytesIO(b'{}')), patch.object(E.time,'monotonic',side_effect=[0,41]):
            with self.assertRaises(TimeoutError):E.read_json(E.NL_BASE+'test',{})

    def test_timeout_does_not_fan_out_into_station_retries(self):
        locations=[{'Code':str(i)} for i in range(40)]
        with patch.object(E,'nl_locations',return_value=locations), patch.object(E,'read_json',side_effect=TimeoutError('slow')) as fetch:
            rows,report=E.collect_nl(datetime.now(timezone.utc),{'fixture':True})
        self.assertEqual(fetch.call_count,1)
        self.assertEqual(rows,[])
        self.assertEqual(len(report['failed_batches'][0]['locations']),40)

    def test_history_resumes_successful_window_after_failure(self):
        now=datetime.now(timezone.utc)
        row={'src':'rijkswaterstaat','source_station':'amerongen.beneden','level_datum':'NAP',
             'items':[{'label':'Pegelstand','unit':'cm'}],'history':{'Pegelstand':[]}}
        calls=[]
        def fetch(url,body):
            calls.append(body)
            if len(calls)==2:raise TimeoutError('slow')
            return {'Succesvol':True,'WaarnemingenLijst':[]}
        with patch.object(E,'read_json',fetch):
            report=E.nl_backfill_locations([row],now,budget=2)
        self.assertEqual(len(calls),2)
        self.assertEqual(datetime.fromisoformat(row['nl_history_cursor']),now-timedelta(days=338))
        self.assertNotIn('history_checked_at',row)
        self.assertEqual(len(report['errors']),1)
        self.assertEqual(datetime.fromisoformat(calls[0]['Periode']['Einddatumtijd'])-datetime.fromisoformat(calls[0]['Periode']['Begindatumtijd']),timedelta(days=28))
        with patch.object(E,'read_json',return_value={'Succesvol':True,'WaarnemingenLijst':[]}) as fetch:
            E.nl_backfill_locations([row],now,budget=1)
        self.assertEqual(fetch.call_args.args[1]['Periode']['Begindatumtijd'],calls[1]['Periode']['Begindatumtijd'])
