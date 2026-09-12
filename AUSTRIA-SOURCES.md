# Österreich: ergänzte Messnetze (12.09.2026)

Aktive zusätzliche Quellen:
- Steiermark / HyDaVis: öffentliche Stationsliste und ausdrücklich angebotener CSV-Download. Nutzer hat den Daten-Haftungsausschluss bestätigt. Keine CC-BY-Lizenz für diese Quelle behaupten. Pegel-Jahresreihen werden stationsweise importiert; relative Einordnung ist P10/P50/P90 der Tagesmediane, keine amtlichen MW/MNW/MHW.
- Tirol: OGD_W.csv und OGD_WT.csv, CC BY 4.0. Metadaten: https://www.data.gv.at/datasets/6772e22a-a364-47df-9cf7-0590fdde1757 und https://www.data.gv.at/datasets/wassertemperatur-pegel-hydro-tirol
- Salzburg: Hydrografie Pegelstand.txt und Hydrografie Seen.txt, CC BY 4.0. Metadaten: https://service.salzburg.gv.at/ogdWebservice/get/56c28e2d-8b9e-41ba-b7d6-fa4896b5b48b
- Vorarlberg: öffentliche Wasser-Online-Tabelle, Koordinaten aus dem zugehörigen WFS. CC BY 4.0: https://www.data.gv.at/datasets/dfdc63e7-3cca-4515-b435-a300fc4e38e8

BMLUK, Oberösterreich und Kärnten bleiben aktiv. Für Niederösterreich, Wien und Burgenland wird weiterhin der nationale Feed verwendet; eine vollständige Landesabdeckung wird nicht behauptet. Der alternative NÖ-Kartenfeed bleibt ohne verifizierte Weiterverwendungslizenz deaktiviert.

Historische Abdeckung: 54 Steiermark-Pegel mit Jahresdownload. Die anderen geprüften Landes-Downloads bieten aktuelle/kurze Zeiträume, keine hier verifizierte vollständige Jahresreihe. Wasserstand darf nicht aus Abflusswerten oder fremden Pegelnullpunkten abgeleitet werden. Ein fehlender historischer Bezugsbereich bleibt fehlend.

Betrieb: Aktuelle Steiermark-Daten benötigen zwei kompakte Stationslisten, keine einzelnen Jahresdownloads. Der AT-Historienjob ergänzt höchstens drei fehlende Jahresreihen pro Lauf. Ein Monat stündlich, ältere Daten vierstündlich; acht Tage Kartendaten vierstündlich. Ein Quellenfehler darf andere Länder nicht stoppen.

## Prüfung 12.09.2026: historische Pegelbezüge
26 stationsbezogene eHYD-Tagesmittelverteilungen (2023), darunter sechs Drau-Stationen. `austria_level_references.py` verwendet nur ausdrücklich angebotene W-Tagesmittel-CSV, mindestens 300 Tage und eine ausreichende Jahresspanne. Keine Abflüsse, Lücken oder Monatsmittel als Pegeltageswerte. Bekannte unterschiedliche Pegelnullpunkte werden abgewiesen. Die Quelldateien und Zeiträume sind in `austria-level-references.json` dokumentiert. `country_pipeline.combine` erhält diese Bezüge beim Publizieren; sie werden nicht in die aktuelle Wochenhistorie eingefügt. Eigene ausreichend vollständige aktuelle Jahreshistorie hat Vorrang.

Offen: Salzach Ach/Ettenau und OÖ-Enns haben unvollständige letzte eHYD-Jahre; Bruck liefert dort einen ungültigen W-Export. Salzburg Hydris bietet weitere aktuelle Stationen, seine allgemeine Website-Lizenz ist jedoch nicht mit dem CC-BY-OGD-Datensatz gleichzusetzen. Es wird keine Vollständigkeit behauptet. Mur hat in den eingebundenen aktuellen Temperaturfeeds keine Messwerte; die zwölf historischen eHYD-Monatsreihen bleiben Archivdaten.
