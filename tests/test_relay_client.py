import http.server
import os
import sys
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "relay_client"))
import petriklar_relay  # noqa: E402


class FakeRelay(http.server.BaseHTTPRequestHandler):
    mode = "ok"
    seen = []

    def log_message(self, *args):
        pass

    def do_GET(self):
        FakeRelay.seen.append((self.path, self.headers.get("X-PetriKlar-Key"), self.headers.get("User-Agent")))
        if FakeRelay.mode == "ok":
            self.send_response(200)
            self.send_header("X-PetriKlar-Relay", "ok")
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(b"via relay")
        else:
            self.send_response(403)
            self.send_header("X-PetriKlar-Relay", "error")
            self.end_headers()
            self.wfile.write(b"Kein Zugriff.")


class RelayClientTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = http.server.HTTPServer(("127.0.0.1", 0), FakeRelay)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        os.environ["PETRIKLAR_RELAY_URL"] = f"http://127.0.0.1:{cls.server.server_port}/relay/fetch.php"
        os.environ["PETRIKLAR_RELAY_KEY"] = "testschluessel"
        os.environ["PETRIKLAR_RELAY_EXTRA_HOSTS"] = "gesperrt.invalid"
        os.environ["NO_PROXY"] = os.environ["no_proxy"] = "127.0.0.1"
        assert petriklar_relay.install()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        for name in ("PETRIKLAR_RELAY_URL", "PETRIKLAR_RELAY_KEY", "PETRIKLAR_RELAY_EXTRA_HOSTS"):
            os.environ.pop(name, None)

    def setUp(self):
        FakeRelay.mode = "ok"
        FakeRelay.seen = []

    def test_blocked_host_goes_through_relay_with_key_and_headers(self):
        req = urllib.request.Request("http://gesperrt.invalid/daten.csv?a=1&b=2", headers={"User-Agent": "PetriKlar-Test"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            self.assertEqual(resp.read(), b"via relay")
            self.assertEqual(resp.geturl(), "http://gesperrt.invalid/daten.csv?a=1&b=2")
        path, key, agent = FakeRelay.seen[0]
        self.assertEqual(path, "/relay/fetch.php?u=http%3A%2F%2Fgesperrt.invalid%2Fdaten.csv%3Fa%3D1%26b%3D2")
        self.assertEqual(key, "testschluessel")
        self.assertEqual(agent, "PetriKlar-Test")

    def test_rejected_relay_falls_back_to_direct_fetch(self):
        FakeRelay.mode = "error"
        with self.assertRaises(urllib.error.URLError):
            urllib.request.urlopen("http://gesperrt.invalid/x", timeout=5)
        self.assertEqual(len(FakeRelay.seen), 1)

    def test_other_hosts_are_not_relayed(self):
        with urllib.request.urlopen(os.environ["PETRIKLAR_RELAY_URL"] + "?direkt=1", timeout=10) as resp:
            resp.read()
        self.assertEqual(FakeRelay.seen[0][0], "/relay/fetch.php?direkt=1")

    def test_known_blocked_authorities_are_listed(self):
        for host in ("data.ooe.gv.at", "hydro.tirol.gv.at", "www.gkd.bayern.de", "wasserportal.berlin.de",
                     "undine.bafg.de", "afdj.ro", "egov.stmk.gv.at", "info.ktn.gv.at"):
            self.assertIn(host, petriklar_relay.RELAY_HOSTS)

    def test_php_relay_allows_every_python_host(self):
        php = Path(__file__).resolve().parents[1] / "relay_client" / "fetch.php"
        if not php.exists():
            self.skipTest("fetch.php liegt nur im Website-Repository")
        text = php.read_text(encoding="utf-8")
        for host in petriklar_relay.RELAY_HOSTS:
            self.assertIn("'" + host + "'", text)


if __name__ == "__main__":
    unittest.main()
