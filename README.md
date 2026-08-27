# PetriKlar Sensordaten

Dieses öffentliche Repository aktualisiert die von PetriKlar verwendeten
amtlichen Wasser- und Pegeldaten. Es enthält keine Nutzer-, Fang- oder
Angelplatzdaten und keine Zugangsdaten.

## Öffentliche Datendateien

- `wasserwerte.json` – aktueller konsolidierter Stationsstand
- `wassertemperatur_verlauf.json` – ausgedünnter Temperaturverlauf
- `temperatur_zusatz.json` – ergänzende Temperaturen für die Kartenflüsse

Die Herkunft und der Messzeitpunkt werden an den jeweiligen Datensätzen
ausgewiesen. Die Werte werden unverändert beziehungsweise technisch
vereinheitlicht bereitgestellt; Aktualität und Vollständigkeit sind nicht
garantiert.


## Temperaturkarte (ab 26.08.2026)

`temperatur_zusatz.json` ergänzt PEGELONLINE-, LUBW-NIZ- und HLNUG-Temperaturen für die Kartenflüsse. `temperature_sources.py` lädt sie mit Zeitlimits und höchstens vier parallelen Abrufen. Das 4-Stunden-Archiv (Schema 2) behält die tatsächliche Messzeit in `t` und die Slotzeit getrennt in `slot`; ältere ungenaue Zeitstempel werden als `legacy_4h` gekennzeichnet. Keine Werte ohne ursprüngliche Messzeit künstlich aktualisieren.

Tests: `python -m unittest discover -s tests -v`

## Nachbarländer (26.08.2026)

`international_temperature_sources.py` ergänzt die vorhandenen Adapter:

- **ČHMÚ, Tschechien:** TH/0C-Reihen an Labe und Odra. Im Prüfabruf neun
  Temperaturstationen (sechs Elbe, drei Oder), nicht alle Wasserstands-Pegel.
  Quelle: [ČHMÚ Open Data](https://opendata.chmi.cz/hydrology/now/),
  [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/).
  Namensnennung: Český hydrometeorologický ústav (ČHMÚ).
- **Rijkswaterstaat, Niederlande:** Middelharnis, Haringvliet Kier2 und Stellendam
  binnen, auf dem dargestellten Rhein/Waal/Haringvliet-Mündungsarm. Nicht Hoek van
  Holland auf einem anderen Arm. [Neue WaterWebservices](https://rijkswaterstaatdata.nl/waterdata/),
  [Open-Data-Bedingungen](https://www.rijkswaterstaat.nl/zakelijk/open-data).
  Nur Oberflächenwasser (OW), Temperatur T/oC, Prozess meting, Qualitätscode 00;
  derselbe obere Messhorizont −200/WATSGL je Standort. Noch ungeprüfte operative
  Messungen, keine Mischung mit Lufttemperatur oder tieferen Messhorizonten.

15 Sekunden Timeout je Anfrage, höchstens vier parallele ČHMÚ-Anfragen.
Ein ausgefallener Standort verwirft nicht die anderen erfolgreichen Abrufe.
Vorhandene Cachewerte behalten ihre ursprüngliche Messzeit. Der Collector sammelt
stündlich; acht Tage Stundenverlauf und das bestehende 366-Tage-/4-h-Archiv
behalten Quelle, Originalzeit, Land und Lizenzangaben. Fehlende ältere Historie
wird nicht rückwärts erfunden. Keine zusätzlichen Zugangsschlüssel erforderlich.

Frankreich: geprüfte Hub’Eau-Reihen an Rhein/Mosel nur historisch; keine
Live-Aktivierung. Polen/Slowakei: aktuelle Messstellen gefunden, aber kostenlose
kommerzielle Weiterverwendung bzw. stabile automatische Einbindung noch offen.
Insbesondere ist „noch nicht aktiviert“ **kein** pauschales rechtliches Verbot
öffentlicher oder hochwertiger Daten. Bestehende AT-/CH-Feeds bleiben unverändert.

Die geografische 50-km-Ausblendung und die Plausibilitätsprüfung finden im
Frontend statt. Sie löschen keine Originaldaten im Archiv. Für diese Erweiterung
genügt Commit/Push dieses Repositories und der nächste stündliche Workflow;
ein manueller Start von `wasserwerte.yml` beschleunigt den ersten Datenabruf.
