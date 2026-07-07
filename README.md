# tourmanager26-data

Databru for en daglig Claude-cloudrutine under Tour de France 2026.
GitHub Actions henter offentlige data fra tourmanager-API-et og
cyclingstage.com hver morgen (07:30 CEST) og committer dem til `data/`:

- `active_round.json` — aktiv etappe, deadline, låst-status
- `players.json` — rytterpool med posisjon, pris, tilgjengelighet
- `player_points.json` — totalpoeng per rytter-id
- `favourites.json` — cyclingstage-favoritter for aktiv etappe
- `status.json` — tidsstempel + eventuelle hentefeil

Kan slettes etter 26. juli 2026.
