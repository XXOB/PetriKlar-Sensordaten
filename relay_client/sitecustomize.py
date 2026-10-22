# Wird von Python automatisch geladen, wenn dieser Ordner im PYTHONPATH steht
# (Messwerte-Lauf). Ohne PETRIKLAR_RELAY_URL/-KEY bleibt alles beim Alten.
try:
    import petriklar_relay
    petriklar_relay.install()
except Exception as exc:  # die Abrufe duerfen daran nie scheitern
    import sys
    print(f"[Relay] nicht aktiv: {exc}", file=sys.stderr)
