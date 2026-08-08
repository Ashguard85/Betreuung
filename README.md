# Noemi Betreuung

Kleine iPhone-first Webapp für einen einfachen Betreuungsplan.

## Funktionen

- ein Betreuungseintrag pro Datum
- Betreuungsperson per großen Buttons auswählen
- optionale Bemerkung
- fortlaufende Liste + Filter
- Jahresübersicht wie in der bisherigen Excel-Lösung
- Wochenenden automatisch markiert
- Auswertung pro Betreuungsperson
- Drucken / PDF
- CSV Import / Export
- optionale private iCal-URL für iPhone/Apple Kalender
- Login
- SQLite-Datenbank in einem frei wählbaren Host-Pfad
- nach jeder Änderung automatische konsistente SQLite-Sicherung

## Empfohlen: Portainer direkt aus Git

1. Dieses Repository in dein Git-System pushen.
2. Auf dem Docker-Host einen Datenordner anlegen, z. B.
   `/volume1/docker/noemi-betreuung/data`
3. Portainer → **Stacks** → **Add stack** → **Repository**
4. Repository URL eintragen.
5. Compose path: `compose.yaml`
6. Unter Environment variables mindestens setzen:

```text
DATA_PATH=/volume1/docker/noemi-betreuung/data
APP_PORT=8094
APP_USER=familie
APP_PASSWORD=<starkes Passwort>
SECRET_KEY=<langes zufälliges Secret>
ICAL_TOKEN=<optional, langer zufälliger Token>
```

Zum Erzeugen von Secrets auf Linux:

```bash
openssl rand -hex 32
```

7. Stack deployen.
8. Aufrufen: `http://SERVER-IP:8094`

## Persistente Daten

Im Host-Pfad `DATA_PATH` liegen:

```text
betreuung.sqlite
betreuung.sqlite-wal
betreuung.sqlite-shm
backups/
  betreuung-YYYYMMDD-HHMMSS-...sqlite
```

Der Code kommt aus Git. Die Daten liegen **nicht** im Git-Repository und werden bei Updates oder Container-Neubauten nicht verändert.

## iPhone

In Safari öffnen → Teilen → **Zum Home-Bildschirm**.

Wenn `ICAL_TOKEN` gesetzt ist, zeigt die App unter **Setup** einen privaten iCal-Abolink. Diesen kannst du auf dem iPhone als Kalenderabonnement hinzufügen.

## Reverse Proxy / HTTPS

Wenn die App übers Internet erreichbar ist, unbedingt HTTPS über den vorhandenen Reverse Proxy (Synology Reverse Proxy, Nginx Proxy Manager, Traefik, Caddy etc.) verwenden.


## PWA / Offline

- installierbar auf iPhone/Android (HTTPS erforderlich)
- Apple Touch Icon sowie 192/512 px PWA-Icons
- Service Worker mit Versionswechsel und Cache-Bereinigung
- zuletzt geladene Einträge/Personen sind offline lesbar
- Änderungen und Server-Exporte werden offline deaktiviert

## PDF Export

Die Listenansicht kann nach Jahr, Person und Suchtext gefiltert und als CSV oder als echtes A4-PDF exportiert werden. Der PDF-Endpunkt lautet `/export.pdf`.
