#!/usr/bin/env bash
set -euo pipefail
git config user.name "github-actions[bot]"
git config user.email "github-actions[bot]@users.noreply.github.com"
if git diff --cached --quiet; then exit 0; fi
git commit -m "Messdaten aktualisiert ($(date -u +'%Y-%m-%d %H:%MZ'))"
for attempt in 1 2 3 4 5; do
  git pull --rebase origin main
  if git push origin HEAD:main; then exit 0; fi
  sleep $((attempt * 2))
done
echo "Daten konnten nach fünf Versuchen nicht gespeichert werden." >&2
exit 1
