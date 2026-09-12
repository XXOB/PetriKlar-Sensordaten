import json
import tempfile
import unittest
from datetime import datetime, timezone, timedelta
from pathlib import Path
from build_sensor_packets import build


class MapPacketResolution(unittest.TestCase):
    def test_four_hour_history_keeps_real_latest_readings_and_current(self):
        now=datetime.now(timezone.utc).replace(minute=0,second=0,microsecond=0)
        points=[{'t':(now-timedelta(minutes=15*i)).isoformat(),'v':i} for i in range(9*24*4)]
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            (root/'pegelkarte.json').write_text(json.dumps({'schema':1,'stations':[
                {'id':'test','river':'Lech','current':points[0],'history':points}
            ]}),encoding='utf8')
            build(root,root/'packets')
            station=json.loads((root/'packets/level-map.json').read_text())['stations'][0]
            history=station['history']
            self.assertLessEqual(len(history),49)
            self.assertEqual(station['current'],points[0])
            self.assertEqual(len({t//14400000 for t,v in history}),len(history))
            originals={int(datetime.fromisoformat(p['t']).timestamp()*1000):p['v'] for p in points}
            self.assertTrue(all(originals[t]==v for t,v in history))
            self.assertEqual(history[-1],[int(now.timestamp()*1000),0])
