# PetriKlar Sensordaten

Dieses öffentliche Repository aktualisiert die von PetriKlar verwendeten
amtlichen Wasser- und Pegeldaten. Es enthält keine Nutzer-, Fang- oder
Angelplatzdaten und keine Zugangsdaten.

## Öffentliche Datendateien

- `wasserwerte.json` – aktueller konsolidierter Stationsstand
- `wassertemperatur_verlauf.json` – ausgedünnter Temperaturverlauf

Die Herkunft und der Messzeitpunkt werden an den jeweiligen Datensätzen
ausgewiesen. Die Werte werden unverändert beziehungsweise technisch
vereinheitlicht bereitgestellt; Aktualität und Vollständigkeit sind nicht
garantiert.


## Temperaturkarte (ab 26.08.2026)

`temperatur_zusatz.json` ergänzt PEGELONLINE-, LUBW-NIZ- und HLNUG-Temperaturen für die Kartenflüsse. `temperature_sources.py` lädt sie mit Zeitlimits und höchstens vier parallelen Abrufen. Das 4-Stunden-Archiv (Schema 2) behält die tatsächliche Messzeit in `t` und die Slotzeit getrennt in `slot`; ältere ungenaue Zeitstempel werden als `legacy_4h` gekennzeichnet. Keine Werte ohne ursprüngliche Messzeit künstlich aktualisieren.

Tests: `python -m unittest discover -s tests -v`
