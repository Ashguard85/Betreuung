# v64 – kompakte Push-Titel

Die Push-Titel sind jetzt absichtlich kurz, damit iOS sie sauber mit seiner eigenen Absenderzeile kombiniert:

- `Neuer Termin` → iOS zeigt z. B. `Neuer Termin from Betreuung`
- `Änderung` → `Änderung from Betreuung`
- `Löschung` → `Löschung from Betreuung`
- `Wiederherstellung` → `Wiederherstellung from Betreuung`
- Test → `Test from Betreuung`

Der Detailtext bleibt darunter separat, z. B. `Vreni am 28.08.`.

Nur das Docker-Backend wurde geändert; die Pages-PWA bleibt unverändert.

# v63 – saubere Push-Kopfzeilen

iOS ergänzt bei Web-Push selbst den Namen der sendenden PWA (z. B. `from Betreuung`).
Darum wird der App-Titel nicht mehr zusätzlich als Push-Titel gesendet.

Neue Push-Titel:
- `Neuer Termin`
- `Termin geändert`
- `Termin gelöscht`
- `Termin wiederhergestellt`
- `Neue Termine`
- Test: `Test erfolgreich`

Der Haupttext enthält nur noch die eigentliche Information, z. B. `Vreni am 28.08.`.

Nur das Docker-Backend wurde geändert; die Pages-PWA bleibt unverändert.

# v62 – verständlichere Push-Texte

- Test-Push: „Test erfolgreich – Benachrichtigungen sind auf diesem Gerät aktiviert.“
- Neue Termine: „Neuer Termin: <Person> am <Datum>“
- Änderungen: „Termin geändert: <Person> am <Datum>“
- Löschungen: „Termin gelöscht: <Person> am <Datum>“
- Wiederherstellungen: „Termin wiederhergestellt: <Person> am <Datum>“
- Sammelmeldungen wurden ebenfalls sprachlich gekürzt.

Nur die serverseitig erzeugten Push-Texte wurden geändert; die Pages-PWA bleibt unverändert.

# v61 – Listenansicht ab heute

- Im aktuellen Jahr zeigt die fortlaufende Liste standardmäßig nur Termine ab heute.
- Mehrtägige Termine, die bereits vor heute begonnen haben, erscheinen ab heute als Fortsetzung.
- Bei bewusst ausgewählten vergangenen Jahren bleibt die vollständige Historie sichtbar.
- CSV/PDF/ICS-Exporte bleiben unverändert und richten sich weiterhin nach den bestehenden Exportfiltern.

# v60 – Push-Benachrichtigungen und neue Änderungen

- Web Push pro Gerät für neue, geänderte, gelöschte und wiederhergestellte Betreuungseinträge.
- Für diese Funktion müssen Pages PWA v60 und Docker Fullstack v60 gemeinsam verwendet werden.
- Das Gerät, das eine Änderung selbst speichert, wird nicht zusätzlich benachrichtigt.
- Startseite zeigt bei ungelesenen Änderungen einen direkten Link `🔔 N neue Änderungen`.
- Änderungen-Inbox zeigt die neuen Einträge und öffnet vorhandene Termine direkt.
- App-Icon-Badge wird mit der Zahl ungelesener Änderungen synchronisiert, soweit das Betriebssystem die Badging API unterstützt.
- Setup → Benachrichtigungen: Gerätename, Push aktivieren/deaktivieren und Test-Mitteilung.
- VAPID-Schlüsselpaar wird beim ersten Aktivieren automatisch unter `/app/data/webpush-vapid-private.pem` erzeugt und bleibt damit über Container-Neustarts erhalten.
- Push-Subscriptions und Lesestatus sind gerätebezogen und werden nicht in JSON-Backups exportiert.
- Die Geräte-ID wird als nicht-geheimer API-Queryparameter übertragen; dadurch entstehen keine zusätzlichen CORS-Header. Die bestehende Cloudflare-Option „OPTIONS zum Origin durchlassen“ bleibt für den Service-Token-Zugriff erforderlich.

# v59 - Jahresplan-PDF nutzt die A4-Hoehe besser

- Die 31 Tageszeilen im Jahres-/Mehrmonats-PDF werden jetzt dynamisch hoeher.
- Der vorhandene Leerraum unter dem Kalender wird fuer groessere Tagesfelder genutzt.
- Titel, Legende und Fusszeile bleiben auf derselben A4-Seite.
- Bei mehr Legendenzeilen wird die Tabellenhoehe automatisch etwas reduziert, damit kein Umbruch auf eine zweite Seite entsteht.
- Monats-PDF mit nur einem Monat bleibt unveraendert.

# v58 – getrennte UID für manuellen Kalenderimport

- Abo-/Feed-Termine behalten ihre bisherige UID `betreuung-<id>@host`.
- Einzel- und Bereichsimporte verwenden jetzt stabil `betreuung-manual-<id>@host`.
- Dadurch verwechselt Apple Kalender den manuellen Import nicht mehr mit dem read-only Abo-Termin.
- Derselbe Betreuungseintrag behält bei wiederholtem manuellen Import dieselbe Manual-UID.

# v57 – direkter Apple-Kalender-Aufruf

- Ein gespeicherter Termin hat im Bearbeiten-Dialog jetzt ` In Apple Kalender`.
- Der Listen-Button ` Apple Kalender` funktioniert jetzt auch aus der GitHub-Pages-PWA mit Cloudflare Service Auth.
- Direkte Apple-Aufrufe verwenden den bereits vorhandenen read-only `/calendar.ics`-Pfad mit langem iCal-Token. Es ist kein zusätzlicher Cloudflare-Bypass nötig, wenn `/calendar.ics` wie bisher gezielt freigegeben ist.
- `/calendar.ics` unterstützt nun optional `entry_id` oder `from`/`to` plus die bestehenden Personen-/Suchfilter. Diese Filter reduzieren ausschließlich die Ausgabe; der Pfad bleibt schreibgeschützt.
- Wenn `ICAL_TOKEN` nicht aktiviert ist, fällt die Oberfläche auf den bisherigen authentifizierten Datei-Teilen-Weg zurück.

# v56 – saubere Historie und Update-Reihenfolge

- Bearbeitete Termine speichern ab jetzt den Vorher- und Nachher-Zustand.
- „Letzte Änderungen“ zeigt konkret Betreuung, Zeitraum und Bemerkung als `von → auf`.
- Alte History-Einträge bleiben erhalten und lesbar; es wird nichts gelöscht.
- Reines erneutes Speichern ohne fachliche Änderung erzeugt keinen zusätzlichen „Geändert“-Eintrag.
- Beim PWA-Start wird zuerst nach der neuesten Online-Version gesucht; erst danach wird ein wartender Worker aktiviert. Dadurch werden vermeidbare Zwischenversions-Sprünge verhindert.
- Updates bleiben weiterhin vor laufenden Schreibvorgängen/Formularen geschützt.

# v55 – zuverlässige PWA-Update-Erkennung

- Behebt die Race Condition beim Erkennen eines bereits laufenden `installing` Workers.
- Der manuelle Update-Check wartet jetzt bis zu 12 Sekunden auf `installed/waiting` statt pauschal nur 900 ms.
- `register()` prüft unmittelbar `waiting` und `installing`, auch wenn `updatefound` schon vor dem Listener ausgelöst wurde.
- Updates werden weiterhin nicht aggressiv mitten in einer Benutzersitzung aktiviert.

# v54 – Jahreskalender Runtime-Fix

- Behebt `entry is not defined` aus v53 im Jahreskalender.
- Die vollflächige Darstellung mehrtägiger Fortsetzungstage aus v53 bleibt unverändert.

# v53 – mehrtägige Einträge vollflächig im Jahresplan

- Reine Fortsetzungstage füllen jetzt die ganze Betreuungsfläche der Jahreszelle.
- Bei zusätzlichem eigenem Eintrag am selben Tag bleiben beide sichtbar.

# v52 – kompakte Fortsetzungen im Jahresplan

- Volle Zwischentage mehrtägiger Einträge zeigen im Jahresplan nur noch den Namen.
- Der letzte Tag zeigt bei einer Endzeit weiterhin `Name · bis HH:MM`.
- Der Jahres-PDF-Export verwendet dieselbe kompakte Darstellung.

# v51 – Mehrtägige Betreuung mit Von-/Bis-Datum

- Einzelne Betreuungseinträge können jetzt einen echten Start- und Endzeitpunkt über mehrere Kalendertage haben.
- Beispiele: 15.08. 19:00 → 16.08. 22:00 und 15.08. 07:00 → 20.08. 12:00.
- Bei Terminen mit Uhrzeit zeigt die Maske zusätzlich **Bis · Datum** mit dem nativen Datumspicker.
- Bestehende Übernacht-Einträge bleiben kompatibel: alte 22:00–05:00-Termine werden bei der Datenbankmigration automatisch auf den Folgetag abgebildet.
- Liste, nächste 7 Tage und Jahresübersicht zeigen Fortsetzungen an allen betroffenen Tagen.
- CSV, JSON-Backup/Restore, PDF und iCalendar verwenden das echte Enddatum.
- Ganztägige Einträge und die Batch-Erstellung bleiben unverändert.
- Keine vorhandenen Einträge, Personen, Tokens oder Einstellungen werden gelöscht.

# v50 – Datumspicker für Von/Bis

- Alle Datumsfelder besitzen jetzt einen sichtbaren Kalender-Button.
- Besonders die Von-/Bis-Felder lassen sich damit eindeutig per nativer Datumsauswahl öffnen.
- Der bisherige native Date-Input über das gesamte Feld bleibt erhalten.
- `showPicker()` wird genutzt, wenn der Browser es unterstützt; iOS/ältere Browser erhalten einen Focus/Click-Fallback.
- Keine Änderung an IndexedDB, localStorage, Serverdaten oder fachlicher Terminlogik.

# v46 – Service-Worker Cache-Recovery

Die Fullstack-PWA verwendet dieselbe sichere Update-Logik wie das Pages-Frontend: einmalige Recovery von v43-v45, danach sichere Aktivierung vollständig geladener Updates beim nächsten App-Start. Keine `clients.claim()`-Übernahme und keine unkontrollierten Reloads.

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


### Name beim einmaligen ICS-Export

Der Kalendername (`X-WR-CALNAME`) des Zeitraum-/Jahresexports kann per Environment-Variable gesetzt werden:

```yaml
- ICAL_EXPORT_NAME=Betreuung Noemi
```

Ohne gesetzten Wert wird `Betreuungsplan` verwendet. Die Titel der einzelnen Termine werden weiterhin aus dem persönlichen iCal-Titel der Betreuungsperson bzw. dem globalen Titel-Template erzeugt.

## iCal / Cloudflare Access

Für alle intern erzeugten Kalender-Links wird HTTPS verwendet. Empfohlen ist im Stack:

```yaml
- APP_URL=https://betreuung.example.ch
```

`APP_URL` muss eine vollständige HTTPS-URL sein. Ohne `APP_URL` erzeugt die App als Fallback `https://<aktueller-host>/calendar.ics`.

Der Endpunkt `/calendar.ics` liefert immer `Cache-Control: private, no-store` sowie zusätzliche No-Cache-Header aus. Wenn Cloudflare Access mit OTP eingesetzt wird, kann nur dieser exakte Pfad als Bypass freigegeben werden; der geheime Token wird weiterhin von der App geprüft.

Im Setup werden zwei Arten von Links angezeigt:

- **Gesamtkalender**: enthält alle Betreuungseinträge und verwendet den globalen `ICAL_TOKEN`. Diesen Link nicht weitergeben.
- **Pro Person**: enthält nur die Termine dieser Person und verwendet einen eigenen zufälligen, widerrufbaren Personen-Token. Ein Personen-Link kann deshalb unabhängig von allen anderen Freigaben widerrufen werden.

Die Personen-Links verwenden weiterhin denselben Pfad `/calendar.ics`, daher reicht in Cloudflare Access eine einzige Bypass-Regel exakt für diesen Pfad. Eine Umbenennung ändert den persönlichen Freigabe-Link nicht. Nur „Freigabe widerrufen“ erzeugt bewusst einen neuen Token und macht den alten Link ungültig.

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


## v33
Setup-Bereiche sind einklappbar, Kalenderlinks werden vollständig umbrochen angezeigt, Personenfeeds sind einzeln einklappbar und der Änderungsverlauf besitzt nun die fehlenden API-Endpunkte.


## Kalenderexport eines Zeitraums

In der Listenansicht kann ein frei gewählter Zeitraum als einmalige `.ics`-Datei exportiert werden. Die aktuelle Personenauswahl und Suche werden übernommen. So kann z. B. nach abgeschlossener Planung das komplette Jahr 01.01.–31.12. in Outlook, Apple Kalender oder andere iCalendar-kompatible Programme importiert werden. Nachtbetreuungen, die vom Vortag in den gewählten Zeitraum hineinreichen, werden ebenfalls berücksichtigt.

## Native iPhone/iPad-App (Capacitor)

Unter `mobile/` liegt zusätzlich ein lokaler iOS-Client. Die Oberfläche wird in die App gebündelt; Daten werden weiterhin direkt von dieser Flask-Instanz geladen. Beim ersten Start werden Server-URL sowie Cloudflare Access Client ID/Client Secret eingegeben. Unter iOS werden diese Daten im System-Keychain gespeichert und bei Requests an den konfigurierten Server als Service-Token-Header mitgesendet.

Die bestehende Docker-/PWA-Version bleibt unverändert. Anleitung für Build und Installation: `mobile/README_IOS.md`.


## v40 – getrennte GitHub-Pages-PWA

Der Docker-/Flask-Server bleibt vollständig hinter Cloudflare Access. Das statische iPhone-Frontend liegt in einem separaten GitHub-Pages-Repository.

Für den Server im Portainer-Stack ergänzen:

```text
AUTH_ENABLED=false
PWA_ALLOWED_ORIGINS=https://betreuung2.DEINE-DOMAIN.TLD
APP_URL=https://betreuung.DEINE-DOMAIN.TLD
```

`PWA_ALLOWED_ORIGINS` akzeptiert eine kommaseparierte Liste expliziter Origins; kein `*`. `PWA_ALLOWED_ORIGIN` bleibt rückwärtskompatibel. Bei leeren Werten werden keine Cross-Origin-Antworten freigegeben.

Cloudflare Access bleibt die Authentifizierungsschicht. Für die bestehende Weboberfläche kann OTP/Allow verwendet werden; zusätzlich kann eine `Service Auth`-Policy das Service Token der externen PWA akzeptieren. Die PWA sendet `CF-Access-Client-ID` und `CF-Access-Client-Secret` bei jedem Serverrequest.

Wichtig: Wenn `AUTH_ENABLED=true` gesetzt ist, verlangt Flask zusätzlich eine eigene Session-Anmeldung. Für den beschriebenen Cloudflare-Aufbau deshalb `AUTH_ENABLED=false` verwenden und den Origin ausschließlich über Cloudflare Tunnel/Access veröffentlichen.


## v43 – robuste PWA Offline-/Update-Architektur

- Vollständige Docker-App-Shell wird versioniert lokal gecacht.
- Die Root-Navigation `/` startet cache-first; Login-, Export- und Kalender-Routen behalten ihre Server-Semantik.
- Die zuletzt lesbaren GET-API-Antworten bleiben in einem stabilen privaten Daten-Cache über Frontend-Updates hinweg erhalten. Wenn nur dieser Cache verfügbar ist, wird der Datenserver als nicht erreichbar markiert und Schreibvorgänge werden deaktiviert.
- Service-Worker-Updates werden vollständig im Hintergrund installiert und warten anschließend. Kein automatisches `skipWaiting()`, kein `clients.claim()` und kein automatischer Reload bei `controllerchange`.
- Im Setup werden App-Version und ein optionaler „Jetzt aktualisieren“-Button angezeigt. Der Button wird bei laufenden Schreib-/Importvorgängen oder offenen Dialogen blockiert.
- Die aktive Hauptansicht wird in `sessionStorage` gemerkt und nach einem kontrollierten Reload wiederhergestellt.
- Bestehende SQLite-Daten, Browser-Speicher und Benutzerkonfigurationen werden durch das Update nicht verändert.


### iPhone/Home-Screen-PWA

Für Cloudflare Access wird empfohlen, `OPTIONS` zum Origin durchzulassen (**Bypass OPTIONS requests to origin**) und CORS durch Flask mit `PWA_ALLOWED_ORIGINS` zu erzwingen. Dadurch können Browser- und installierte PWA-Origins gezielt und nachvollziehbar freigegeben werden.
