# Betreuungsplan

Kleine iPhone-first PWA für einen einfachen Betreuungsplan.

## Funktionen

- ein Betreuungseintrag pro Datum
- Batch-Generierung für einen festen Wochentag über einen Von/Bis-Zeitraum
- Betreuungsperson per großen Buttons auswählen
- optionale Bemerkung
- fortlaufende Liste + Filter
- Jahresübersicht wie in der bisherigen Excel-Lösung
- alle Monate gleich breit
- Wochenenden automatisch markiert
- Ferien als Zeitraum von/bis erfassen
- Feiertage aus `.ics` importieren
- Ferien/Feiertage als schmaler Farbbalken rechts im Tagesfeld
- Farblegende im A4-Jahresausdruck
- Auswertung pro Betreuungsperson
- Listenexport als CSV und echtes A4-PDF
- Jahresübersicht als A4-Querformat über Drucken/PDF
- optionale private iCal-URL für iPhone/Apple Kalender
- zusätzlicher iCal-Feed pro Betreuungsperson mit eigenem abgeleiteten Token
- Login per Container-Umgebungsvariable ein- oder ausschaltbar
- SQLite-Datenbank in einem frei wählbaren Host-Pfad
- nach jeder Änderung automatische konsistente SQLite-Sicherung

## Portainer / Docker

Die App läuft in einem Container. Der Code kann direkt aus Git gebaut werden; die Daten bleiben außerhalb des Containers im gemounteten Datenpfad.

Beispiel-Umgebungsvariablen:

```text
AUTH_ENABLED=false
APP_USER=familie
APP_PASSWORD=<nur nötig wenn AUTH_ENABLED=true>
SECRET_KEY=<empfohlen wenn AUTH_ENABLED=true>
ICAL_TOKEN=<optional, langer zufälliger Token>
BACKUP_KEEP=50
```

Wenn du keine Loginseite willst:

```text
AUTH_ENABLED=false
```

Wenn du den Login später wieder aktivieren willst:

```text
AUTH_ENABLED=true
APP_USER=familie
APP_PASSWORD=<starkes Passwort>
SECRET_KEY=<langes zufälliges Secret>
```

Der Stack selbst bzw. dein Reverse Proxy kann zusätzlich bestimmen, ob die App überhaupt erreichbar ist.

## Persistente Daten

Im gemounteten Host-Pfad `/app/data` liegen:

```text
betreuung.sqlite
betreuung.sqlite-wal
betreuung.sqlite-shm
backups/
  betreuung-YYYYMMDD-HHMMSS-...sqlite
```

Der Code kommt aus Git. Die Daten liegen **nicht** im Git-Repository und bleiben bei Updates oder Container-Neubauten erhalten.


## Mehrere Einträge auf einmal

Unter **Eintragen → Mehrere Einträge erstellen** kannst du eine Betreuungsperson, einen festen Wochentag sowie `Von` und `Bis` festlegen. Die Vorschau zeigt, wie viele passende Termine gefunden wurden, wie viele neu erstellt werden und wie viele wegen bereits belegter Tage übersprungen werden.

Beim Erstellen werden ausschließlich normale Einzeltermine in `entries` angelegt. Es wird **keine Serie gespeichert**. Jeder erzeugte Termin kann danach unabhängig bearbeitet oder gelöscht werden. Bestehende Betreuungseinträge werden nicht überschrieben.

## Ferien und Feiertage

Unter **Setup → Ferien & Feiertage** kannst du einen Zeitraum mit `Von`, `Bis`, Bezeichnung, Art und Farbe erfassen. Zwei Wochen Ferien sind damit ein einziger Datensatz.

Feiertage können über eine `.ics`-Datei importiert werden. Ganztägige `VEVENT`-Einträge werden übernommen. Einfache jährlich wiederkehrende Termine (`RRULE:FREQ=YEARLY`) werden für das aktuelle Umfeld mit expandiert. Bei erneutem Import werden Ereignisse mit derselben ICS-UID aktualisiert.

In der Jahresübersicht erscheint rechts im Tagesfeld ein ca. 20 % breiter Farbbalken. Betreuung und Ferien/Feiertage können damit am selben Tag gleichzeitig sichtbar sein. Bei mehreren Markierungen am selben Tag wird der Balken vertikal aufgeteilt.

## iPhone / PWA

In Safari öffnen → Teilen → **Zum Home-Bildschirm**.

- HTTPS erforderlich
- Apple Touch Icon sowie 192/512 px PWA-Icons
- Service Worker mit Versionswechsel und Cache-Bereinigung
- zuletzt geladene Einträge, Personen und Zeiträume sind offline lesbar
- Änderungen und Server-Exporte werden offline deaktiviert

## PDF Export

Die Listenansicht kann nach Jahr, Person und Suchtext gefiltert und als echtes A4-PDF exportiert werden. Der Endpunkt lautet `/export.pdf`.

Die Jahresübersicht ist über **Drucken / PDF** für A4 Querformat optimiert. Farben und Farblegende werden im Druck mit ausgegeben.

## Reverse Proxy / HTTPS

Wenn die App übers Internet erreichbar ist, HTTPS verwenden. Bei `AUTH_ENABLED=false` hat jeder, der die App über Netzwerk/Reverse Proxy erreichen kann, vollen Zugriff auf die Betreuungsdaten. Deshalb den Zugriff über Firewall, VPN, Reverse-Proxy-Authentifizierung oder ein internes Netz begrenzen.

## iCal / Cloudflare Access

Der Endpunkt `/calendar.ics` liefert immer `Cache-Control: private, no-store` sowie zusätzliche No-Cache-Header aus. Wenn Cloudflare Access mit OTP eingesetzt wird, kann nur dieser exakte Pfad als Bypass freigegeben werden; der geheime Token wird weiterhin von der App geprüft.

Im Setup werden zwei Arten von Links angezeigt:

- **Gesamtkalender**: enthält alle Betreuungseinträge und verwendet den globalen `ICAL_TOKEN`. Diesen Link nicht weitergeben.
- **Pro Person**: enthält nur die Termine dieser Person und verwendet einen aus `ICAL_TOKEN` abgeleiteten HMAC-Token. Ein Personen-Link kann deshalb nicht durch Ändern des `person`-Parameters für eine andere Person verwendet werden.

Die Personen-Links verwenden weiterhin denselben Pfad `/calendar.ics`, daher reicht in Cloudflare Access eine einzige Bypass-Regel exakt für diesen Pfad. Wird eine Person umbenannt, muss ihr Personen-Kalender neu abonniert werden.

## Neu in v22 – Uhrzeiten & Termin teilen

- Betreuungseinträge können **ganztägig** oder mit **Von/Bis-Uhrzeit** gespeichert werden.
- Bestehende Datenbanken werden beim Start automatisch erweitert; vorhandene Einträge bleiben ganztägig.
- Die Zeitangaben werden im Gesamt-iCal und in den Personen-iCal-Feeds ausgegeben.
- In der Bearbeitungsmaske gibt es **„Termin teilen (.ics)“**. Auf iPhone/iPad öffnet die PWA über die Web Share API den nativen Teilen-Dialog; dort kann z. B. WhatsApp gewählt werden.
- CSV, PDF-Liste und vollständiges JSON-Backup enthalten die Zeitinformationen ebenfalls.
- Der Jahresplan bleibt bewusst kompakt und zeigt weiterhin primär die Betreuungsperson pro Tag.

Hinweis: Zeitangaben im iCal werden aktuell als lokale („floating“) Uhrzeiten ohne feste Zeitzone ausgegeben. Das passt für eine lokale Familienplanung; eine explizite Zeitzonenbehandlung kann bei Bedarf ergänzt werden.

## v23 – Zeitfelder
- Von/Bis auf iPhone als sauberes Zwei-Spalten-Layout mit Beschriftungen oberhalb der Zeitfelder.


## Kalender-Abos für Ferien / Feiertage

Unter **Setup → Ferien & Feiertage → Kalender-Abos** können HTTPS-iCalendar-URLs hinterlegt werden. Die App synchronisiert aktive Abos standardmäßig alle 12 Stunden und speichert die Termine lokal, damit sie auch bei einem vorübergehend nicht erreichbaren Feed sichtbar bleiben.

Optional im Stack:

```text
SUBSCRIPTION_SYNC_HOURS=12
ALLOW_HTTP_CALENDAR_SUBSCRIPTIONS=false
```

Aus Sicherheitsgründen blockiert der Server bei Kalender-Abos private, lokale und Link-Local-Zieladressen. HTTP ist standardmäßig deaktiviert; öffentliche HTTPS-Feeds sind empfohlen.

Personen-Kalender verwenden ab v30 jeweils einen eigenen zufälligen, widerrufbaren Schlüssel. Ein Widerruf betrifft nur die gewählte Person.
