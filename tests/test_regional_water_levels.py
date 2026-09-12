import unittest

import regional_water_levels as regional


class RegionalWaterLevels(unittest.TestCase):
    def test_bavarian_inventory_includes_inn_and_lech(self):
        html = '''
        <table>
          <tr><td><a href="https://www.hnd.bayern.de/pegel/iller_lech/lechbruck-12002009">Lechbruck</a></td><td>Lech</td><td>WM</td><td>120 cm</td></tr>
          <tr><td><a href="https://www.hnd.bayern.de/pegel/inn/oberaudorf-18000500">Oberaudorf</a></td><td>Inn</td><td>RO</td><td>200 cm</td></tr>
          <tr><td><a href="https://www.hnd.bayern.de/pegel/isar/muenchen-16005701">München</a></td><td>Isar</td><td>M</td><td>100 cm</td></tr>
        <tr><td><a href="https://www.hnd.bayern.de/pegel/inn/burghausen-18606000">Burghausen</a></td><td>Salzach</td><td>AO</td><td>129 cm</td></tr></table>'''
        rows = regional.bayern_inventory(html)
        self.assertEqual([(name, river) for name, _, river in rows], [
            ('Lechbruck', 'Lech'), ('Oberaudorf', 'Inn'), ('Burghausen', 'Salzach')
        ])


if __name__ == '__main__':
    unittest.main()
