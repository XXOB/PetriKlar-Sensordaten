"""Leitet Abrufe gesperrter Behoerdenserver ueber den PetriKlar-Server um.

Seit Oktober 2026 lehnen mehrere Behoerdenserver (Oberoesterreich, Tirol,
Kaernten, Niederoesterreich, Steiermark, GKD Bayern, Wasserportal Berlin,
BfG Undine, LfU Brandenburg, NRW, AFDJ Rumaenien) Anfragen von GitHub-Servern
ab. Dieselben oeffentlichen Daten liefern sie an normale Webserver aus. Dieses
Modul haengt sich in urllib ein: Anfragen an die Hosts unten gehen an
relay/fetch.php auf petriklar.com, das die Datei holt und unveraendert
zurueckgibt. Alles andere bleibt ein direkter Abruf.

Aktiv nur, wenn PETRIKLAR_RELAY_URL und PETRIKLAR_RELAY_KEY gesetzt sind
(im Messwerte-Lauf aus dem GitHub-Secret). Scheitert der Umweg, wird direkt
abgerufen - schlechter als vorher kann es also nicht werden.

Geladen wird es ueber sitecustomize.py in diesem Ordner (PYTHONPATH), damit
auch Unterprozesse wie europe_sources.py es nutzen, ohne dass jedes
Abrufskript geaendert werden muss.
"""
from __future__ import annotations

import atexit
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

RELAY_HOSTS = frozenset({
    # Oesterreich
    # data.ooe.gv.at leitet auf www.land-oberoesterreich.gv.at um.
    "data.ooe.gv.at", "www.land-oberoesterreich.gv.at", "hydro.tirol.gv.at", "www.noe.gv.at", "www.noel.gv.at",
    "info.ktn.gv.at", "hydrographie.ktn.gv.at", "egov.stmk.gv.at",
    "www.hydrografie.steiermark.at",
    # Deutschland
    "www.gkd.bayern.de", "www.nid.bayern.de", "www.hnd.bayern.de",
    "wasserportal.berlin.de", "undine.bafg.de",
    "lfu.brandenburg.de", "mleuv-daten.brandenburg.de",
    "www.hydrologie.nrw.de",
    # Am 22.10.2026 von der Selbstheilung gemeldet (Zeitueberschreitung direkt).
    "www.pegelonline.wsv.de",
    # Niederlande
    "ddapi20-waterwebservices.rijkswaterstaat.nl",
    # Donau unterhalb Oesterreichs
    "afdj.ro",
})
# Alle uebrigen amtlichen Quellen: zuerst direkt. Lehnt der Server ab (keine
# Verbindung, Zeitueberschreitung, HTTP 403/429/451/5xx), wird derselbe Abruf
# ueber petriklar.com wiederholt - und fuer den Rest des Laufs gleich dort.
# Eine neue Sperre heilt sich so selbst und erscheint als Warnung im Lauf.
# relay/fetch.php muss jeden dieser Hosts ebenfalls erlauben (Test).
FALLBACK_HOSTS = frozenset({
    # Oesterreich
    "www.salzburg.gv.at", "vowis.vorarlberg.at", "ehyd.gv.at", "gis.lfrz.gv.at",
    # Deutschland
    "www.hlnug.de", "app.hlnug.de", "www.wasser.sachsen.de",
    "www.gewaessergueteonline.nlwkn.niedersachsen.de",
    "niz.baden-wuerttemberg.de", "www.hvz.baden-wuerttemberg.de", "inovum-services.de",
    "geodaten-wasser.rlp-umwelt.de", "www2.bsh.de", "www.gewaesser-monitoring.de",
    # Grossbritannien: nur bei direkter Sperre ueber Netcup wiederholen.
    "environment.data.gov.uk", "timeseries.sepa.org.uk",
    # Schweiz
    "www.hydrodaten.admin.ch", "data.bafu.admin.ch", "lindas.admin.ch",
    "ld.admin.ch", "environment.ld.admin.ch",
    # Niederlande
    "geo.rijkswaterstaat.nl",
    "rijkswaterstaatdata.nl", "waterinfo.rws.nl",
    # Nachbarlaender, Mur und Donau
    "opendata.chmi.cz", "danepubliczne.imgw.pl", "hubeau.eaufrance.fr", "www.dati.lombardia.it",
    "www.hidmet.gov.rs", "hidro.dhz.hr", "appd-bg.org", "www.arso.gov.si", "vode.arso.gov.si",
})
BLOCK_STATUS = frozenset({403, 429, 451, 500, 502, 503, 504})
# Weitere Hosts, die das PHP-Skript ebenfalls erlaubt, lassen sich ohne
# Codeaenderung zuschalten: PETRIKLAR_RELAY_EXTRA_HOSTS="a.example b.example"

_SKIP_HEADERS = {"host", "connection", "content-length", "accept-encoding", "x-petriklar-key"}
_stats: dict[str, list[int]] = {}
# Hosts, die in diesem Lauf direkt abgelehnt haben und ueber den Umweg liefen.
_newly_blocked: dict[str, str] = {}
_installed = False
_original: dict[str, object] = {}


def _hosts() -> frozenset[str]:
    extra = os.environ.get("PETRIKLAR_RELAY_EXTRA_HOSTS", "")
    return RELAY_HOSTS | {h.strip().lower() for h in extra.split() if h.strip()}


def _config():
    url = os.environ.get("PETRIKLAR_RELAY_URL", "").strip()
    key = os.environ.get("PETRIKLAR_RELAY_KEY", "").strip()
    return (url, key) if url and key else (None, None)


def relay_request(req: urllib.request.Request, relay_url: str, key: str) -> urllib.request.Request:
    """Baut die Anfrage an das Relay: Ziel-URL als Parameter, Kopfzeilen 1:1."""
    target = urllib.parse.quote(req.full_url, safe="")
    sep = "&" if "?" in relay_url else "?"
    out = urllib.request.Request(relay_url + sep + "u=" + target, data=req.data, method=req.get_method())
    for name, value in req.header_items():
        if name.lower() not in _SKIP_HEADERS:
            out.add_header(name, value)
    out.add_header("X-PetriKlar-Key", key)
    out.timeout = max(float(req.timeout or 0) if isinstance(req.timeout, (int, float)) else 0, 60.0)
    return out


def _count(host: str, index: int) -> None:
    _stats.setdefault(host, [0, 0])[index] += 1


def _report() -> None:
    for host, reason in sorted(_newly_blocked.items()):
        # stdout: GitHub zeigt die Zeile als gelbe Warnung im Lauf an.
        print(f"::warning title=Behoerde sperrt GitHub::{host} lehnt direkte Abrufe ab ({reason}); "
              "ueber petriklar.com abgerufen. Bitte in RELAY_HOSTS aufnehmen.", flush=True)
    if not _stats:
        return
    parts = [f"{host}: {ok} ueber petriklar.com, {direct} direkt" for host, (ok, direct) in sorted(_stats.items())]
    print("[Relay] " + "; ".join(parts), file=sys.stderr, flush=True)


def _via_relay(req, host: str, relay_url: str, key: str, fallback_note: str):
    """Antwort ueber petriklar.com - oder None, wenn der Umweg nicht klappt."""
    relay = relay_request(req, relay_url, key)
    try:
        if relay.type == "https":
            resp = _original["https"](_relay_https, relay)
        else:
            resp = _original["http"](_relay_http, relay)
        state = (resp.headers.get("X-PetriKlar-Relay") or "").lower()
        if state == "ok":
            resp.url = req.full_url
            _count(host, 0)
            return resp
        detail = resp.read(300).decode("utf-8", "replace").strip()
        resp.close()
        print(f"[Relay] {host}: Umweg abgelehnt (HTTP {resp.status}: {detail[:160]}){fallback_note}",
              file=sys.stderr, flush=True)
    except (urllib.error.URLError, OSError, ValueError) as exc:
        print(f"[Relay] {host}: Umweg nicht erreichbar ({exc}){fallback_note}", file=sys.stderr, flush=True)
    return None


def _patched(kind: str):
    original = _original[kind]

    def open_(self, req):
        # req.host zeigt bei gesetztem Proxy auf den Proxy - das Ziel steht in full_url.
        host = (urllib.parse.urlsplit(req.full_url).hostname or "").lower()
        relay_url, key = _config()
        if not relay_url:
            return original(self, req)
        if host in _hosts() or host in _newly_blocked:
            resp = _via_relay(req, host, relay_url, key, " - direkter Abruf")
            if resp is not None:
                return resp
            _count(host, 1)
            return original(self, req)
        if host not in FALLBACK_HOSTS:
            return original(self, req)
        # Zuerst direkt; nur bei einer Ablehnung ueber den Umweg.
        try:
            direct = original(self, req)
        except (urllib.error.URLError, OSError) as exc:
            reason = str(getattr(exc, "reason", exc))[:80]
            resp = _via_relay(req, host, relay_url, key, "")
            if resp is None:
                raise
            if resp.status not in BLOCK_STATUS:
                _newly_blocked[host] = reason
            return resp
        if direct.status not in BLOCK_STATUS:
            _count(host, 1)
            return direct
        resp = _via_relay(req, host, relay_url, key, "")
        if resp is None or resp.status in BLOCK_STATUS:
            if resp is not None:
                resp.close()
            _count(host, 1)
            return direct
        direct.close()
        _newly_blocked[host] = f"HTTP {direct.status}"
        return resp

    return open_


_relay_https = urllib.request.HTTPSHandler()
_relay_http = urllib.request.HTTPHandler()


def install() -> bool:
    """Haengt die Umleitung ein; ohne Konfiguration passiert nichts."""
    global _installed
    if _installed:
        return True
    if _config() == (None, None):
        return False
    _original["https"] = urllib.request.HTTPSHandler.https_open
    _original["http"] = urllib.request.HTTPHandler.http_open
    urllib.request.HTTPSHandler.https_open = _patched("https")
    urllib.request.HTTPHandler.http_open = _patched("http")
    atexit.register(_report)
    _installed = True
    return True
